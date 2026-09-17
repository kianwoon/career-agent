"""API routes for pluggable sources: CRUD, guided wizard, flows."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.orm import Source, SourceFlow, SourceRecording
from app.models.schemas import (
    SourceCreate,
    SourceFlowUpdate,
    SourceFlowView,
    SourceView,
    WizardCompleteRequest,
    WizardCompleteResponse,
    WizardStartRequest,
    WizardStartResponse,
)
from app.services.encryption import encrypt_credentials, encrypt_session_state
from app.services.session import (
    prepare_source_cookies,
    self_heal_source_session,
    session_is_stale,
)
from app.services.site_probe import probe_site
from app.services.site_profiles import profile_for_source
from app.services.source_flows import (
    FLOW_TYPES,
    WizardSession,
    autofill_wizard_login,
    discover_flow,
    domain_of,
    execute_flow,
    is_root_selector,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sources", dependencies=[])

# MCF splits public job search (www) from the logged-in EMPLOYER talent search
# (employer host). Candidate discovery + cookie capture must target the latter:
# a session captured on www.mycareersfuture.gov.sg does not authorize
# employer.mycareersfuture.gov.sg/talent-search.
MCF_EMPLOYER_HOST = "employer.mycareersfuture.gov.sg"
MCF_TALENT_SEARCH_URL = f"https://{MCF_EMPLOYER_HOST}/talent-search"
MCF_TALENT_SEARCH_INPUT = "#talent-search-input"

# FastJobs likewise separates its public/employer login host from the logged-in
# EMPLOYER talent search. Candidate discovery + cookie capture must target the
# talent-search route on the employer host; the coyid is tied to the recorded
# employer account (f5e92471), so candidate results are scoped to that account.
FASTJOBS_EMPLOYER_HOST = "employer.fastjobs.sg"
FASTJOBS_TALENT_SEARCH_URL = "https://employer.fastjobs.sg/p/talent/search/?coyid=22091"

# Candidate cards carry a NAME, not a link label: a bare "a" title selector
# picks the first anchor (nav/footer/facet) and yields junk like
# "Employment Status". This selector prefers a profile-ish anchor, then any
# heading/name element. FastJobs candidate cards are plain divs — the
# heading/name fallbacks cover them. Jobs keep the legacy {"title": "a"}.
CANDIDATE_CARD_FIELDS: dict[str, str] = {
    "title": "a[href*='/candidate'], a[href*='/profile'], h1, h2, h3, .name, [class*='name']"
}


def _card_fields(flow_type: str | None) -> dict[str, str]:
    """Field map for a synthesized card step: name-bearing for candidates,
    legacy first-anchor for jobs."""
    return dict(CANDIDATE_CARD_FIELDS) if flow_type == "find_candidates" else {"title": "a"}


# Backend mirror of the extension's pruneNavNoise rule: a recorded click whose
# selector sits inside a top navbar is incidental UNLESS it is the site's
# search/talent entry. The extension already prunes these, but a stale/older
# extension build (or a worker that injected before the prune shipped) can
# still hand the backend nav clicks — e.g. the FastJobs "Talent search\n NEW"
# multiline label that replays as a dead-weight step, so filter them again
# server-side before persisting.
_NAV_NOISE_RE = re.compile(r"navbar-container|navbar-nav", re.IGNORECASE)


def _is_nav_noise_click(step: dict[str, Any]) -> bool:
    """True for a recorded navbar click that isn't the search/talent entry."""
    if step.get("action") != "click":
        return False
    selector = str(step.get("selector") or "")
    text = str(step.get("text") or "")
    if not _NAV_NOISE_RE.search(selector):
        return False
    return not re.search(r"search|talent", text, re.IGNORECASE)


# Login-wall detour clicks: recorded when the user's session lapsed mid-recording,
# so the login page was showing and its login links/buttons got captured. On
# replay (now logged in) those nodes don't exist -> every leg aborts. Scope: this
# helper is ONLY applied by _prune_recorded_steps, which runs at stop-time merge
# for find_candidates / job filter recordings — flows that never legitimately
# include a login-page click. It is NOT used while RECORDING a fresh login flow,
# so a real "Login" button there is unaffected.
_LOGIN_WALL_SELECTOR_RE = re.compile(
    r"#login\b|#login-form|login-card|login-form", re.IGNORECASE
)
_LOGIN_WALL_TEXT_RE = re.compile(r"^login( to manage|\s*$)", re.IGNORECASE)


def _is_login_wall_click(step: dict[str, Any]) -> bool:
    """True for a login-wall detour click (lapsed-session login page nodes)."""
    if step.get("action") != "click":
        return False
    selector = str(step.get("selector") or "")
    text = str(step.get("text") or "")
    return bool(
        _LOGIN_WALL_SELECTOR_RE.search(selector)
        or _LOGIN_WALL_TEXT_RE.search(text)
    )


def _prune_recorded_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop nav-noise + login-wall clicks, then dedupe repeated clicks globally.

    A stale/older extension build — or an EXISTING stored flow recorded before
    the prune shipped — can carry junk navbar clicks ("Malaysia Jobs", "Chats",
    repeated Talent-search) and login-wall detour clicks (recorded while the
    session had lapsed). After a user re-records, the verification probe replays
    prefix+recorded+suffix live, so unpruned junk becomes visible auto-clicks and
    is carried forward permanently.

    Only clicks are matched: navigate/wait/fill/press pass through untouched.
    Consecutive identical clicks are collapsed first; then identical (selector+
    text) clicks are collapsed GLOBALLY to their LAST occurrence so the first
    "Talent search" entry click survives while later repeats are dropped (fills
    are never deduped — repeated fills with param:query are meaningful legs).
    """
    # 1) drop nav-noise + login-wall clicks, collapse consecutive identical clicks
    consec: list[dict[str, Any]] = []
    for step in steps:
        if _is_nav_noise_click(step) or _is_login_wall_click(step):
            continue
        if step.get("action") == "click" and consec:
            prev = consec[-1]
            if (
                prev.get("action") == "click"
                and prev.get("selector") == step.get("selector")
                and prev.get("text") == step.get("text")
            ):
                continue
        consec.append(step)

    # 2) global dedupe of identical clicks -> keep LAST occurrence only.
    # Walk backwards; first-seen while walking back == last in original order.
    seen_clicks: set[tuple[Any, Any]] = set()
    keep: list[bool] = [True] * len(consec)
    for idx in range(len(consec) - 1, -1, -1):
        step = consec[idx]
        if step.get("action") != "click":
            continue
        key = (step.get("selector"), step.get("text"))
        if key in seen_clicks:
            keep[idx] = False
        else:
            seen_clicks.add(key)
    return [step for idx, step in enumerate(consec) if keep[idx]]


def _looks_like_card_click(step: dict[str, Any]) -> bool:
    """Heuristic: does a recorded click plausibly target a candidate result?

    Used on the LAST-RESORT fallback path (stored card rotted + auto-detect
    failed) to refuse silently persisting a known-bad card when the user never
    clicked anything card-shaped. Matches a selector mentioning candidate/
    profile/card/result OR a name-shaped text label (e.g. "Ahmad Rizal").
    """
    if step.get("action") != "click":
        return False
    selector = str(step.get("selector") or "")
    text = str(step.get("text") or "").strip()
    if re.search(r"candidate|profile|card|result", selector, re.IGNORECASE):
        return True
    # Name-shaped text: 2–4 Title-Case words, no digits, reasonable length.
    return bool(
        text
        and len(text) <= 60
        and re.fullmatch(r"(?:[A-Z][a-z'.-]+\s+){1,3}[A-Z][a-z'.-]+", text)
    )


def _is_mcf_candidates(source: Source, flow_type: str | None) -> bool:
    """True for a MyCareersFuture find_candidates flow (needs the employer app)."""
    return (
        flow_type == "find_candidates"
        and "mycareersfuture.gov.sg" in (source.domain or "")
    )


def _is_fastjobs(source_or_domain: Any) -> bool:
    """True for any FastJobs TLD (fastjobs.sg, regional fastjobs.* hosts, …).

    FastJobs serves regional accounts from the same employer portal
    (employer.fastjobs.sg), so host/login derivation must be tolerant of the
    source's TLD rather than matching the literal ".sg" domain.
    """
    domain = getattr(source_or_domain, "domain", source_or_domain) or ""
    return "fastjobs." in domain


def _is_fastjobs_candidates(source: Source, flow_type: str | None) -> bool:
    """True for a FastJobs find_candidates flow (needs the employer talent search)."""
    return flow_type == "find_candidates" and _is_fastjobs(source)


def _candidate_entry_url(source: Source) -> str:
    """Landing URL for a find_candidates recording, per source.

    Candidate discovery starts on a DIFFERENT host/route than the public job
    search for MCF and FastJobs (logged-in employer talent search) — recording
    the user's clicks on the public site would capture steps that replay
    against an empty/unauthorized page. Fall back to base_url for every other
    source.
    """
    domain = source.domain or ""
    prof = profile_for_source(domain, getattr(source, "profile", None))
    if prof and prof.candidate_entry_url:
        return prof.candidate_entry_url
    if "mycareersfuture.gov.sg" in domain:
        return MCF_TALENT_SEARCH_URL
    if _is_fastjobs(domain):
        return FASTJOBS_TALENT_SEARCH_URL
    return source.base_url


def _session_capture_urls(source: Source) -> list[str]:
    """URLs whose cookies must be captured to cover every host a flow touches.

    MCF splits its public site (www) from the employer talent-search app
    (employer host) — capturing only base_url would leave the candidate flow
    unauthorized, so both hosts are captured and merged. FastJobs employer
    talent search is likewise captured alongside the login/base URL. The
    profile registry is the primary source; the hardcoded checks remain as a
    fallback for sites without a profile.
    """
    urls = [source.base_url]
    prof = profile_for_source(source.domain or "", getattr(source, "profile", None))
    entry = prof.candidate_entry_url if prof else None
    if entry and entry not in urls:
        urls.append(entry)
        return urls
    if "mycareersfuture.gov.sg" in (source.domain or ""):
        urls.append(MCF_TALENT_SEARCH_URL)
    if _is_fastjobs(source):
        urls.append(FASTJOBS_TALENT_SEARCH_URL)
    return urls


def _merge_cookies(cookie_lists: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Merge cookie blobs, de-duplicating by (name, domain, path)."""
    merged: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for cookies in cookie_lists:
        for c in cookies or []:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            merged[(c.get("name"), c.get("domain"), c.get("path"))] = c
    return list(merged.values())



# In-memory wizard sessions (single-process dev deployment).
_wizards: dict[str, WizardSession] = {}


def _source_view(source: Source, flows: list[SourceFlow]) -> SourceView:
    return SourceView(
        id=source.id,
        name=source.name,
        base_url=source.base_url,
        domain=source.domain,
        enabled=bool(source.enabled),
            has_session=bool(source.session_state),
            # Auto re-login state. `has_credentials` is a BOOLEAN only — the
            # encrypted blob (and never the plaintext) stays server-side.
            has_credentials=bool(getattr(source, "login_credentials", None)),
            # session_is_stale already covers missing state / old capture /
            # near-expiry, i.e. exactly the cases self-heal will act on.
            needs_relogin=session_is_stale(source),
        flows={f.flow_type: f.status for f in flows},
        profile=getattr(source, "profile", None),
        created_at=source.created_at,
    )


async def _get_source(source_id: str, db: AsyncSession) -> Source:
    source = await db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    return source


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=list[SourceView])
async def list_sources(db: AsyncSession = Depends(get_db)) -> list[SourceView]:
    sources = (await db.execute(select(Source).order_by(Source.created_at))).scalars().all()
    flows = (await db.execute(select(SourceFlow))).scalars().all()
    by_source: dict[str, list[SourceFlow]] = {}
    for f in flows:
        by_source.setdefault(f.source_id, []).append(f)
    return [_source_view(s, by_source.get(s.id, [])) for s in sources]


@router.post("", response_model=SourceView, status_code=201)
async def create_source(
    req: SourceCreate, db: AsyncSession = Depends(get_db)
) -> SourceView:
    url = req.base_url.strip()
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    domain = domain_of(url)
    # A bare word ("JobStreet") would silently become https://jobstreet/ —
    # navigate-to-nowhere. A real site host needs a dot (localhost exempt
    # for dev). Rejecting here surfaces the typo at creation time.
    if not domain or ("." not in domain and domain != "localhost"):
        raise HTTPException(
            400,
            f"'{req.base_url.strip()}' is not a valid site URL — "
            "use something like sg.jobstreet.com",
        )

    existing = (
        await db.execute(select(Source).where(Source.domain == domain))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, f"A source for {domain} already exists")

    source = Source(name=req.name.strip(), base_url=url, domain=domain)
    db.add(source)
    await db.commit()
    await db.refresh(source)
    # Probe the site once so unknown sources start with a capability profile
    # (auth model, Cloudflare, login patterns). Best-effort: a probe failure
    # must never fail registration — store None and move on.
    try:
        source.profile = await probe_site(source.base_url)
        await db.commit()
        await db.refresh(source)
    except Exception as exc:
        logger.warning("Site probe failed for %s: %s", domain, exc)
        source.profile = None
    return _source_view(source, [])


# NOTE: `from __future__ import annotations` makes `-> None` a string that
# FastAPI evaluates to NoneType — a truthy response_model, which trips the
# "204 must not have a response body" assert. Pass response_model=None.
@router.delete("/{source_id}", status_code=204, response_model=None)
async def delete_source(source_id: str, db: AsyncSession = Depends(get_db)) -> None:
    source = await _get_source(source_id, db)
    await db.delete(source)
    await db.commit()


class SourceUpdate(BaseModel):
    enabled: bool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)
    base_url: str | None = Field(default=None, min_length=1, max_length=1000)


@router.patch("/{source_id}", response_model=SourceView)
async def update_source(
    source_id: str, req: SourceUpdate, db: AsyncSession = Depends(get_db)
) -> SourceView:
    """Update a source: enable/disable, rename, or repoint its base_url.

    base_url repointing matters for sites whose landing page isn't the working
    app (e.g. SEEK: sg.employer.seek.com is marketing; the Talent Search
    candidate app lives at /talentsearch behind OAuth).
    """
    source = await _get_source(source_id, db)
    if req.enabled is not None:
        source.enabled = req.enabled
    if req.name is not None:
        source.name = req.name.strip()
    if req.base_url is not None:
        url = req.base_url.strip()
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        domain = domain_of(url)
        if not domain or ("." not in domain and domain != "localhost"):
            raise HTTPException(
                400,
                f"'{req.base_url.strip()}' is not a valid site URL — "
                "use something like sg.jobstreet.com",
            )
        clash = (
            await db.execute(
                select(Source).where(Source.domain == domain, Source.id != source.id)
            )
        ).scalar_one_or_none()
        if clash:
            raise HTTPException(409, f"A source for {domain} already exists")
        source.base_url = url
        source.domain = domain
    await db.commit()
    await db.refresh(source)
    flows = (
        await db.execute(select(SourceFlow).where(SourceFlow.source_id == source.id))
    ).scalars().all()
    return _source_view(source, list(flows))


@router.delete("/{source_id}/session", response_model=SourceView)
async def clear_source_session(
    source_id: str, db: AsyncSession = Depends(get_db)
) -> SourceView:
    """Drop the stored login session (Re-login path).

    Mirrors the agent_login wipe: clears session_state/captured_at so
    has_session flips false and the wizard opens a clean, logged-out login
    page for fresh credentials. Also clears expires_at so the staleness
    self-heal never sees an orphaned expiry.

    Saved auto re-login CREDENTIALS are deliberately KEPT: wiping the session
    is the "session went bad" path, and the stored credentials are exactly what
    lets the next search re-authenticate headlessly. Use
    DELETE /{source_id}/credentials to forget them.
    """
    source = await _get_source(source_id, db)
    if (
        source.session_state is not None
        or source.captured_at is not None
        or source.expires_at is not None
    ):
        source.session_state = None
        source.captured_at = None
        source.expires_at = None
        await db.commit()
        await db.refresh(source)
    flows = (
        await db.execute(select(SourceFlow).where(SourceFlow.source_id == source.id))
    ).scalars().all()
    return _source_view(source, list(flows))


# ---------------------------------------------------------------------------
# Auto re-login credentials (encrypted at rest; never echoed back)
# ---------------------------------------------------------------------------


class SourceCredentials(BaseModel):
    username: str = Field(..., min_length=1, max_length=320)
    password: str = Field(..., min_length=1, max_length=1024)


@router.post("/{source_id}/credentials", response_model=SourceView)
async def save_source_credentials(
    source_id: str, req: SourceCredentials, db: AsyncSession = Depends(get_db)
) -> SourceView:
    """Store (or replace) the encrypted login credentials for auto re-login.

    Only the AES-256-GCM blob is persisted — the plaintext never lands in the
    DB and is never returned. The response reports `has_credentials` only.
    """
    source = await _get_source(source_id, db)
    source.login_credentials = encrypt_credentials(req.username, req.password)
    source.credentials_updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(source)
    logger.info("Stored auto re-login credentials for %s", source_id)
    flows = (
        await db.execute(select(SourceFlow).where(SourceFlow.source_id == source.id))
    ).scalars().all()
    return _source_view(source, list(flows))


@router.delete("/{source_id}/credentials", response_model=SourceView)
async def clear_source_credentials(
    source_id: str, db: AsyncSession = Depends(get_db)
) -> SourceView:
    """Forget the saved credentials (auto re-login falls back to the banner)."""
    source = await _get_source(source_id, db)
    if source.login_credentials is not None or source.credentials_updated_at is not None:
        source.login_credentials = None
        source.credentials_updated_at = None
        await db.commit()
        await db.refresh(source)
        logger.info("Cleared auto re-login credentials for %s", source_id)
    flows = (
        await db.execute(select(SourceFlow).where(SourceFlow.source_id == source.id))
    ).scalars().all()
    return _source_view(source, list(flows))


# ---------------------------------------------------------------------------
# Guided wizard: login -> record -> complete
# ---------------------------------------------------------------------------


@router.post("/{source_id}/wizard/start", response_model=WizardStartResponse, status_code=201)
async def wizard_start(
    source_id: str, req: WizardStartRequest, db: AsyncSession = Depends(get_db)
) -> WizardStartResponse:
    source = await _get_source(source_id, db)
    if req.mode == "record" and req.flow_type not in FLOW_TYPES:
        raise HTTPException(400, f"flow_type must be one of {FLOW_TYPES}")

    wizard_id = f"wiz-{source.id}-{req.mode}"
    start_url = source.base_url
    # Idempotent: if an active wizard already exists for this source/mode,
    # return it instead of killing and restarting (double-clicks, retries).
    existing_wiz = _wizards.get(wizard_id)
    if existing_wiz and await existing_wiz.age_s() < 300:
        return WizardStartResponse(wizard_id=wizard_id, mode=req.mode, start_url=start_url)
    old = _wizards.pop(wizard_id, None)
    if old:
        await old.close()

    storage_state = None
    # Login mode starts a CLEAN, logged-out browser so the user can enter
    # fresh credentials (Re-login = switch accounts). Other modes reuse the
    # stored session so recording happens as the authenticated user.
    if source.session_state and req.mode != "login":
        import json

        from app.services.encryption import decrypt_session_state

        try:
            storage_state = json.loads(decrypt_session_state(source.session_state))
        except Exception as exc:
            logger.warning("Could not decrypt source session: %s", exc)

    wiz = WizardSession(source.id, req.flow_type or "login", domain=source.domain)
    logger.info(
        "Wizard start %s/%s: session_state=%s",
        source_id,
        req.mode,
        "present" if storage_state else "NONE (logged-out guest browsing)",
    )
    try:
        await wiz.start(start_url, storage_state)
    except Exception as exc:
        await wiz.close()
        raise HTTPException(502, f"Could not start wizard browser in container: {exc}")

    # Re-login (mode=login) starts CLEAN, so the sign-in page is empty. If a
    # password is saved, fill it backend-side so the operator lands on a
    # pre-filled form (they still confirm/submit). Fill happens with or without
    # a CAPTCHA/MFA present; a blocker only stops auto-submit (which we never
    # do anyway). Secrets never leave the backend.
    if req.mode == "login":
        await _autofill_login_wizard(source, wiz)

    _wizards[wizard_id] = wiz
    return WizardStartResponse(wizard_id=wizard_id, mode=req.mode, start_url=start_url)


async def _autofill_login_wizard(source: Source, wiz: WizardSession) -> None:
    """Fill the wizard login form with the source's SAVED credentials (backend).

    Purely best-effort: a missing/unreadable blob, no login form within the
    poll window, or a browser error all fall back silently to the current
    empty-form behaviour (the operator types manually). Never logs or returns
    the username/password.
    """
    from app.services.encryption import decrypt_credentials

    blob = getattr(source, "login_credentials", None)
    if not blob:
        return
    try:
        username, password = decrypt_credentials(blob)
    except Exception:
        logger.info("wizard auto-fill: failed (stored credentials unreadable)")
        return
    try:
        status, blocker = await autofill_wizard_login(wiz.page, username, password)
    except Exception as exc:
        logger.warning("wizard auto-fill: failed (%s)", exc)
        return
    logger.info(
        "wizard auto-fill: %s%s",
        status,
        " (blocker-present)" if blocker else "",
    )



async def _wiz(source_id: str, mode: str) -> WizardSession:
    wiz = _wizards.get(f"wiz-{source_id}-{mode}")
    if wiz is None:
        raise HTTPException(404, "No active wizard session (expired or completed)")
    return wiz


@router.get("/{source_id}/wizard/screenshot")
async def wizard_screenshot(source_id: str, mode: str = "login", zoom: str = "page"):
    """Live PNG of the wizard browser. The UI polls this for a live view.

    zoom=page  — full viewport (default)
    zoom=qr    — locate a QR code region on the page and return an enlarged
                 crop, so a phone can scan it directly from the preview.
    """
    from fastapi import Response

    wiz = await _wiz(source_id, mode)
    if zoom != "qr":
        png = await wiz.screenshot()
        if png is None:
            raise HTTPException(502, "Screenshot unavailable — browser may be navigating")
        return Response(content=png, media_type="image/png")

    crop = await wiz.locate_qr_region()
    if crop is None:
        # No QR found — fall back to the full page.
        png = await wiz.screenshot()
        if png is None:
            raise HTTPException(502, "Screenshot unavailable")
        return Response(content=png, media_type="image/png")
    x, y, w, h = crop
    png = await wiz.screenshot(clip={"x": x, "y": y, "width": w, "height": h}, scale=2)
    if png is None:
        raise HTTPException(502, "Screenshot unavailable")
    return Response(
        content=png,
        media_type="image/png",
        headers={"X-QR-Region": f"{x},{y},{w},{h}"},
    )


@router.get("/{source_id}/wizard/status")
async def wizard_status(source_id: str, mode: str = "login") -> dict:
    wiz = await _wiz(source_id, mode)
    return await wiz.status()


class WizardCredentials(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    submit: bool = True


@router.post("/{source_id}/agent_login")
async def agent_login(source_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Open the site's login page in the USER's browser via the extension.

    The user signs in there (they're already trusted — same browser they use
    daily). When done, the extension captures cookies and they're POSTed to
    /{source_id}/agent_session.
    """
    source = await _get_source(source_id, db)
    from app.services.agent_relay import agent_registry

    login_url = source.base_url
    prof = profile_for_source(source.domain or "", getattr(source, "profile", None))
    if "linkedin.com" in source.domain:
        login_url = "https://www.linkedin.com/login"
    elif "mycareersfuture.gov.sg" in source.domain:
        login_url = "https://www.mycareersfuture.gov.sg/sign-in"
    elif prof and prof.candidate_entry_url:
        # Portal sites with a dedicated employer host (FastJobs): derive the
        # login host from the profile's candidate entry URL so a host change
        # lives in one place, not as another domain literal here.
        from urllib.parse import urlparse

        host = urlparse(prof.candidate_entry_url).hostname or FASTJOBS_EMPLOYER_HOST
        login_url = f"https://{host}/site/login/"
    elif _is_fastjobs(source):
        # Any FastJobs TLD (profile-less included) signs in via the employer host.
        login_url = f"https://{FASTJOBS_EMPLOYER_HOST}/site/login/"

    # Re-login = switch accounts. Best-effort wipe the browser's cookies for
    # this site so the login page starts clean. Never fail login on this.
    # MCF splits www/employer hosts, so clear (and later capture) both.
    for target in _session_capture_urls(source):
        try:
            await agent_registry.dispatch(
                "clear_cookies", {"url": target}, timeout_s=20
            )
        except Exception as exc:  # clear is best-effort
            if "Unknown action" in str(exc):
                logger.info(
                    "clear_cookies unsupported for %s (extension outdated — reload the "
                    "unpacked extension to enable cookie wipe); continuing to login page: %s",
                    source_id,
                    exc,
                )
                break
            logger.warning("clear_cookies failed for %s: %s", source_id, exc)

    # Drop the stored session BEFORE navigating so has_session flips false and
    # the freshly captured login replaces it.
    if source.session_state is not None or source.captured_at is not None:
        source.session_state = None
        source.captured_at = None
        await db.commit()

    try:
        # activate=True brings the agent tab to the foreground so the user
        # actually sees the login page they're being asked to sign in on.
        await agent_registry.dispatch(
            "navigate", {"url": login_url, "activate": True}, timeout_s=60
        )
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))

    # Re-login should land on a PRE-FILLED sign-in form when creds are saved.
    # The page lives in the USER's browser via the extension relay, so the
    # fill is a second relay command (never a backend Playwright page). Fill
    # only — the extension never submits; CAPTCHA/MFA stay manual.
    autofill = await _autofill_agent_login(source)
    return {"ok": True, "login_url": login_url, "autofill": autofill}


async def _autofill_agent_login(source: Source) -> str:
    """Relay-fill the agent tab's login form with SAVED credentials.

    Best-effort: no saved/unreadable blob, an outdated extension (unknown
    action), or a relay failure all degrade to the previous empty-form
    behaviour. The username/password travel only inside the agent relay
    command (the extension's poll channel) — never logged, never in the
    response (only the derived status string is returned).
    """
    from app.services.encryption import decrypt_credentials

    blob = getattr(source, "login_credentials", None)
    if not blob:
        return "skipped-no-credentials"
    try:
        username, password = decrypt_credentials(blob)
    except Exception:
        logger.info("agent_login auto-fill: stored credentials unreadable")
        return "skipped-unreadable"
    from app.services.agent_relay import agent_registry

    try:
        result = await agent_registry.dispatch(
            "autofill_login",
            {"username": username, "password": password},
            timeout_s=30,
        )
    except Exception as exc:  # relay/timeout/unknown action — never fatal
        if "Unknown action" in str(exc):
            logger.info(
                "autofill_login unsupported (extension outdated — reload the "
                "unpacked extension to enable auto-fill)"
            )
        else:
            logger.warning("agent_login auto-fill failed: %s", exc)
        return "skipped-error"
    if not isinstance(result, dict):
        return "empty-form"
    if result.get("blocked"):
        return "filled-blocker-present" if result.get("filled") else "blocked"
    return "filled" if result.get("filled") else "empty-form"


class AgentSessionPayload(BaseModel):
    cookies: list[dict[str, Any]]


class AgentRecordRequest(BaseModel):
    flow_type: str = Field(..., description="find_jobs or find_candidates")
    query_hint: str | None = Field(default=None)


class AgentRecordStartRequest(BaseModel):
    flow_type: str = Field(default="find_jobs", description="find_jobs or find_candidates")


def _store_agent_cookies(
    source: Source, cookies: list[dict[str, Any]]
) -> datetime | None:
    """Filter/expiry-stamp extension cookies onto a source row (no commit).

    Shared by both /agent_session endpoints and the pre-search self-heal path so
    every capture writes the same filtered+stripped blob plus captured_at and the
    earliest cookie expiry. Returns the computed expires_at. Caller commits.
    """
    filtered, expires_at = prepare_source_cookies(cookies, source)
    source.session_state = encrypt_session_state(
        json.dumps({"cookies": filtered, "origins": []})
    )
    source.captured_at = datetime.utcnow()
    source.expires_at = expires_at
    return expires_at


@router.post("/{source_id}/agent_session", response_model=SourceView)
async def agent_session(
    source_id: str, req: AgentSessionPayload, db: AsyncSession = Depends(get_db)
) -> SourceView:
    """Store cookies captured by the extension after a manual login.

    Stored as a Playwright-compatible storage_state blob (same format the
    wizard captures), so every existing consumer keeps working.
    """
    source = await _get_source(source_id, db)
    if not req.cookies:
        # An empty capture must not flip has_session true (false positive).
        source.session_state = None
        source.captured_at = None
        source.expires_at = None
        await db.commit()
        raise HTTPException(422, "No cookies captured — sign in to the site first")
    _store_agent_cookies(source, req.cookies)
    await db.commit()
    flows = (
        await db.execute(select(SourceFlow).where(SourceFlow.source_id == source.id))
    ).scalars().all()
    return _source_view(source, list(flows))


@router.post("/{source_id}/agent_session/capture")
async def agent_session_capture(source_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Ask the extension for the current site's cookies (POST to /agent_session next)."""
    source = await _get_source(source_id, db)
    from app.services.agent_relay import agent_registry

    try:
        cookies: list[dict[str, Any]] = []
        for url in _session_capture_urls(source):
            part = await agent_registry.dispatch("get_cookies", {"url": url}, timeout_s=20)
            cookies = _merge_cookies([cookies, part])
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))
    return {"ok": True, "cookies": cookies}


@router.put("/{source_id}/agent_session", response_model=SourceView)
async def agent_session_store(
    source_id: str, req: AgentSessionPayload, db: AsyncSession = Depends(get_db)
) -> SourceView:
    """Store cookies captured by the extension after a manual login."""
    source = await _get_source(source_id, db)
    if not req.cookies:
        # An empty capture must not flip has_session true (false positive).
        source.session_state = None
        source.captured_at = None
        source.expires_at = None
        await db.commit()
        raise HTTPException(422, "No cookies captured — sign in to the site first")
    _store_agent_cookies(source, req.cookies)
    await db.commit()
    flows = (
        await db.execute(select(SourceFlow).where(SourceFlow.source_id == source.id))
    ).scalars().all()
    return _source_view(source, list(flows))


@router.post("/{source_id}/agent_record/start")
async def agent_record_manual_start(
    source_id: str,
    req: AgentRecordStartRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Start recording the user's clicks in the agent tab.

    For jobs: the user does the keyword search (or it's already on screen),
    then clicks the filter-panel options they want (Industry, Salary, Work
    Type…). For candidates: the recorder opens the source's employer talent
    search and the user searches + clicks a result card. Every captured event
    is recorded in the page; /agent_record/stop collects them.
    """
    source = await _get_source(source_id, db)  # 404 if unknown source
    flow_type = (req.flow_type if req else None) or "find_jobs"
    if flow_type not in FLOW_TYPES:
        raise HTTPException(400, f"flow_type must be one of {FLOW_TYPES}")
    from app.services.agent_relay import agent_registry

    # Candidate discovery runs on the logged-in employer app — without a
    # stored session the recorder would open a login wall and every captured
    # click would replay against it. Jobs search stays on the public site.
    if flow_type == "find_candidates" and not source.session_state:
        raise HTTPException(422, "Sign in first (Login button), then record.")

    # Candidate flows start on their per-source entry URL (employer talent
    # search); job flows keep the public base_url. Pass it to the recorder so
    # it attaches/opens a tab the user can actually see and click.
    entry_url = (
        _candidate_entry_url(source)
        if flow_type == "find_candidates"
        else source.base_url
    )
    try:
        await agent_registry.dispatch(
            "start_record", {"baseUrl": entry_url}, timeout_s=30
        )
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))
    return {"ok": True, "recording": True}


@router.post("/{source_id}/agent_record/stop", response_model=SourceFlowView)
async def agent_record_manual_stop(
    source_id: str,
    req: AgentRecordRequest,
    db: AsyncSession = Depends(get_db),
) -> SourceFlowView:
    """Stop recording, merge captured events into the flow, save it.

    Merge order: [navigate + search prefix …] + [recorded events] + [extract card].
    For an existing flow the search prefix + extract step are reused (jobs and
    candidates). For a FIRST-TIME candidates flow (no flow yet) the entry URL
    is the prefix and the card step is detected live via find_result_card.
    """
    source = await _get_source(source_id, db)
    if req.flow_type not in FLOW_TYPES:
        raise HTTPException(400, f"flow_type must be one of {FLOW_TYPES}")

    from app.services.agent_relay import agent_registry

    try:
        data = await agent_registry.dispatch("stop_record", {}, timeout_s=30)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))
    clicks = (data or {}).get("events", [])
    if not clicks:
        raise HTTPException(
            422, "No clicks were recorded — click some filter options, then press Stop"
        )

    # Convert raw recorded events into replayable steps (Part 4): the
    # templatizer isn't reachable from this route, so apply the minimal inline
    # conversion — a text-input fill carries NO typed text (never store the
    # user's query literally); it becomes param:"query". A press keeps only
    # the key. Everything else (clicks) stays literal.
    def _to_step(e: dict[str, Any]) -> dict[str, Any] | None:
        action = e.get("action")
        selector = e.get("selector")
        if action == "fill":
            if not selector:
                return None
            return {"action": "fill", "selector": selector, "param": "query"}
        if action == "press":
            return {"action": "press", "key": e.get("key") or "Enter"}
        if action == "click":
            if not selector:
                return None
            step: dict[str, Any] = {"action": "click", "selector": selector}
            if e.get("text"):
                step["text"] = e["text"]
            return step
        return None

    recorded: list[dict[str, Any]] = []
    for e in clicks:
        if not isinstance(e, dict):
            continue
        step = _to_step(e)
        if step:
            recorded.append(step)
    # Mirror the extension's pruneNavNoise server-side too (a stale extension
    # build still sends them) and collapse repeated background clicks.
    recorded = _prune_recorded_steps(recorded)
    if not recorded:
        raise HTTPException(
            422, "No usable events were recorded — click a result card, then press Stop"
        )

    # Prefix: reuse the existing flow's search steps up to (and including)
    # the last non-extract step, so keyword + submit + waits replay as before.
    existing = (
        await db.execute(
            select(SourceFlow).where(
                SourceFlow.source_id == source.id,
                SourceFlow.flow_type == req.flow_type,
            )
        )
    ).scalar_one_or_none()
    prefix: list[dict[str, Any]] = []
    if existing and existing.steps:
        for step in existing.steps:
            if "card" in step or step.get("action") == "extract":
                break
            prefix.append(step)
    if not prefix:
        # First-time flow: lead with the source's candidate entry URL when
        # recording candidates, else the public base_url.
        entry = (
            _candidate_entry_url(source)
            if req.flow_type == "find_candidates"
            else source.base_url
        )
        prefix = [
            {"action": "navigate", "url": entry},
            {"action": "wait", "seconds": 3},
        ]

    # Prune the reused prefix too: an existing flow recorded before the nav
    # prune shipped carries junk navbar clicks that would otherwise be replayed
    # by the verification probe and carried forward permanently.
    prefix = _prune_recorded_steps(prefix)

    # Suffix: the extract step from the existing flow, if any.
    suffix: list[dict[str, Any]] = []
    if existing and existing.steps:
        for step in existing.steps:
            if "card" in step or step.get("action") == "extract":
                suffix = [step]
                break
    if not suffix and req.flow_type == "find_jobs":
        # Jobs has no sensible card to synthesize — a recorded jobs flow must
        # reuse the existing extract from "Record jobs".
        raise HTTPException(
            502, "No result-card step in the existing flow — press Record jobs first"
        )

    # Probe the stored extract against the LIVE results page: seek rotates
    # obfuscated classes, so a stored card selector can silently rot to
    # junk/zero rows. When it's dead, re-detect the card from the current
    # page via the extension (find_result_card) instead of saving the same
    # broken step again — re-record must be able to heal the selector.
    extract_ok = False
    heal_note: str | None = None
    probe_steps = prefix + recorded + suffix
    try:
        probe_url = (
            _candidate_entry_url(source)
            if req.flow_type == "find_candidates"
            else source.base_url
        )
        probe_q = (req.query_hint or "qc").strip() or "qc"
        probe = await agent_registry.dispatch(
            "run_flow",
            {
                "baseUrl": probe_url,
                "query": probe_q,
                "steps": probe_steps,
            },
            timeout_s=90,
        )
        # A login wall means the probe never reached a results page — heal
        # must NOT run here: find_result_card on a login page can detect
        # guest-job-card rows and save a bogus selector.
        if isinstance(probe, dict) and probe.get("needs_human"):
            heal_note = "login wall during verification — sign in on the site, then re-record"
            raise RuntimeError(heal_note)
        if suffix:
            rows = await agent_registry.dispatch(
                "extract",
                {"card": suffix[0].get("card", ""), "fields": suffix[0].get("fields", {}), "maxItems": 10},
                timeout_s=30,
            )
            real = [
                r for r in (rows or [])
                if isinstance(r, dict)
                and (r.get("title") or "").strip()
                and len(r.get("raw_text") or "") > 60
            ]
            extract_ok = len(real) >= 2
            # A card selector that drifts onto a filter-facet panel extracts N
            # identical rows all titled with the facet label (production: 20
            # rows all "Employment Status"). Real result rows never share a
            # single title — treat a uniform-title extract as dead so the
            # existing heal/refuse machinery re-detects or refuses.
            if extract_ok:
                norm_titles = {
                    (r.get("title") or "").strip().lower().rstrip("…")
                    for r in real
                }
                if len(norm_titles) == 1:
                    extract_ok = False
                    heal_note = (
                        f"extracted rows all share one title "
                        f"({real[0].get('title')!r}) — card matches a filter "
                        "panel, not result rows"
                    )
            # Second guard: if the normalizer would drop EVERY row (facet/
            # chrome/page-blob junk), the card extracts no real candidates.
            if extract_ok:
                try:
                    from app.agent.nodes import _normalize_flow_candidate

                    kept = [
                        _normalize_flow_candidate(r, source.name, i, source.base_url)
                        for i, r in enumerate(real)
                    ]
                    if not any(k is not None for k in kept):
                        extract_ok = False
                        heal_note = (
                            "extracted rows all fail candidate normalization "
                            "(facet/chrome text) — card matches a filter panel, "
                            "not result rows"
                        )
                except Exception:
                    pass  # normalizer unavailable — keep the row-count verdict
    except Exception as exc:
        extract_ok = False
        heal_note = heal_note or str(exc)[:120]
    if not extract_ok:
        # detect the card from the live page (currently showing the results
        # the user just recorded against — the probe left it there for jobs
        # and candidates alike).
        healed = False
        try:
            found = await agent_registry.dispatch("find_result_card", {}, timeout_s=30)
            if found and found.get("found") and found.get("card"):
                suffix = [{
                    "card": found["card"],
                    "fields": _card_fields(req.flow_type),
                }]
                healed = True
        except Exception:
            pass  # keep the old suffix; better than failing the whole save
        if not healed and not suffix:
            # First-time candidates recording where no card was detected.
            raise HTTPException(
                502,
                "Couldn't detect result cards — click a candidate card while recording, then press Done",
            )
        # Existing flow whose stored card rotted AND auto-detect failed: the
        # old `suffix` is now KNOWN-BAD (the probe just proved it extracts
        # nothing). Persisting it blindly is exactly the junk-card regression —
        # it would keep the stale {"card":"div.candidate"} forever while the
        # flow grows extra input steps. For candidates, refuse with a
        # diagnosable error instead of silently saving the bad suffix; the
        # message distinguishes "you never clicked a card" (the production
        # symptom) from "your click didn't resolve to a card".
        if req.flow_type == "find_candidates" and not healed and not extract_ok:
            clicked_card = any(_looks_like_card_click(s) for s in recorded)
            hint = (
                "No candidate click was recorded in this session. "
                if not clicked_card
                else "The recorded click did not resolve to a usable result card. "
            )
            raise HTTPException(
                502,
                hint
                + "The stored result card no longer matches the page, so the "
                "flow was NOT updated. Open a candidate search, click an actual "
                "candidate result card, then press Done.",
            )
        if not healed and not heal_note:
            heal_note = "stored card selector no longer matches — could not auto-detect a new one"

    steps = prefix + recorded + suffix

    if existing:
        existing.steps = steps
        existing.status = "active"
        # Only claim verification when the probe actually extracted rows.
        if extract_ok:
            existing.last_verified_at = datetime.utcnow()
        flow = existing
    else:
        flow = SourceFlow(source_id=source.id, flow_type=req.flow_type, steps=steps)
        db.add(flow)
    await db.commit()
    await db.refresh(flow)
    return SourceFlowView(
        id=flow.id,
        source_id=flow.source_id,
        flow_type=flow.flow_type,
        steps=flow.steps,
        status=flow.status,
        created_at=flow.created_at,
        note=heal_note,
    )


@router.post("/{source_id}/agent_record", response_model=SourceFlowView)
async def agent_record(
    source_id: str,
    req: AgentRecordRequest,
    db: AsyncSession = Depends(get_db),
) -> SourceFlowView:
    """Create a flow for a source. For LinkedIn, flows are SYNTHESIZED from
    URL templates (jobs and people searches have dedicated URLs and distinct
    card structures — no browser recording needed or possible: the generic
    search page mixes jobs and people in one list). For other sites, the
    extension auto-discovers the flow in the user's browser.
    """
    source = await _get_source(source_id, db)
    if req.flow_type not in FLOW_TYPES:
        raise HTTPException(400, f"flow_type must be one of {FLOW_TYPES}")

    steps: list[dict[str, Any]]
    if source.domain == "linkedin.com":
        steps = _linkedin_builtin_flow(req.flow_type)
    else:
        steps = await _agent_discover(source, req)

    card: str | None = None
    if source.domain == "linkedin.com":
        card, _fields = _linkedin_card_spec(req.flow_type)
    else:
        card = (steps[-1] or {}).get("card") if steps else None
    if not card:
        raise HTTPException(502, "Agent could not identify result cards on the page")

    existing = (
        await db.execute(
            select(SourceFlow).where(
                SourceFlow.source_id == source.id,
                SourceFlow.flow_type == req.flow_type,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.steps = steps
        existing.status = "active"
        existing.last_verified_at = datetime.utcnow()
        flow = existing
    else:
        flow = SourceFlow(source_id=source.id, flow_type=req.flow_type, steps=steps)
        db.add(flow)
    await db.commit()
    await db.refresh(flow)
    return SourceFlowView(
        id=flow.id,
        source_id=flow.source_id,
        flow_type=flow.flow_type,
        steps=flow.steps,
        status=flow.status,
        created_at=flow.created_at,
    )


def _linkedin_builtin_flow(flow_type: str) -> list[dict[str, Any]]:
    """LinkedIn flows from URL templates — no recording.

    Jobs:  /jobs/search?keywords={query}  → dedicated jobs list
    People: /search/results/people/?keywords={query} → dedicated people list
    The card/fields step is appended by the caller via _linkedin_card_spec.
    """
    if flow_type == "find_jobs":
        url = "https://www.linkedin.com/jobs/search/?keywords={query}"
    else:
        url = "https://www.linkedin.com/search/results/people/?keywords={query}"
    return [
        {"action": "navigate", "url": url},
        {"action": "wait", "seconds": 3},
    ]


def _linkedin_card_spec(flow_type: str) -> tuple[str, dict[str, str]]:
    """CSS selectors for one result card on LinkedIn's dedicated lists."""
    if flow_type == "find_jobs":
        card = "div.base-card, li.scaffold-layout__list-item, .jobs-search-results__list-item"
        fields = {
            "title": ".base-search-card__title, .job-card-list__title",
            "company": ".base-search-card__subtitle, .job-card-container__company-name",
            "location": ".job-search-card__location, .job-card-container__metadata-item",
        }
    else:
        card = "div.entity-result, li.reusable-search__result-container, .reusable-search__entity-result"
        fields = {
            "title": ".entity-result__title-text a, .entity-result__title-line a",
            "company": ".entity-result__primary-subtitle",
            "location": ".entity-result__secondary-subtitle",
        }
    return card, fields


async def _agent_discover_fastjobs(source: Source) -> list[dict[str, Any]]:
    """FastJobs employer talent-search discovery (Cloudflare + coyid aware).

    Mirrors the MCF branch: navigate the employer talent route, detect a
    result card, probe fallbacks. FastJobs is Cloudflare-protected and its
    talent URL carries a per-account ``coyid`` — a second employer account (or
    an account change) has a different coyid, so the ACTUAL url the extension
    lands on is preferred over the hardcoded default. A login wall surfaces a
    502 with re-login guidance instead of an empty flow.
    """
    from app.services.agent_relay import agent_registry

    logger.info("agent_record: fastjobs candidate discovery for %s starting", source.name)
    default_url = _candidate_entry_url(source)
    prefix: list[dict[str, Any]] = [
        {"action": "navigate", "url": default_url},
        {"action": "wait", "seconds": 6},
    ]

    # Landing + login-wall / coyid probe.
    try:
        await agent_registry.dispatch(
            "run_flow",
            {"baseUrl": default_url, "steps": prefix},
            timeout_s=120,
        )
    except RuntimeError as exc:
        raise HTTPException(502, f"Agent discovery failed: {exc}")
    try:
        page_state = await agent_registry.dispatch(
            "page_state",
            {
                "selectors": [
                    "input[type='password']",
                    "[data-testid*='candidate']",
                    "[data-testid*='card']",
                    "table tbody tr",
                ]
            },
            timeout_s=60,
        )
    except RuntimeError as exc:
        raise HTTPException(502, f"Agent discovery failed: {exc}")
    state = page_state if isinstance(page_state, dict) else {}

    report_url = (state.get("url") or "").strip()
    counts = state.get("counts") or {}
    login_wall = bool(state.get("loginHint")) or counts.get("input[type='password']", 0) > 0
    if login_wall:
        raise HTTPException(
            502,
            "FastJobs employer session expired — re-login via the wizard, "
            "then retry recording",
        )

    # Prefer the resolved URL (a different coyid / extra params) so a second
    # account's talent search replays correctly; fall back to the constant.
    resolved_url = report_url if report_url else default_url
    prefix = [
        {"action": "navigate", "url": resolved_url},
        {"action": "wait", "seconds": 5},
    ]

    found = await agent_registry.dispatch("find_result_card", {}, timeout_s=30)
    logger.info("agent_record: fastjobs find_result_card → %s", found)
    if not (isinstance(found, dict) and found.get("found") and found.get("card")):
        # CF/SPA can paint results slowly — wait and retry once.
        await agent_registry.dispatch(
            "run_flow",
            {"baseUrl": resolved_url, "steps": [{"action": "wait", "seconds": 10}]},
            timeout_s=60,
        )
        found = await agent_registry.dispatch("find_result_card", {}, timeout_s=30)
    if isinstance(found, dict) and found.get("found") and found.get("card"):
        card = found["card"]
        if not is_root_selector(card):
            logger.info("agent_record: fastjobs card found: %s", card)
            return prefix + [{"card": card, "fields": _card_fields("find_candidates")}]
        logger.warning("agent_record: fastjobs rejected root card selector %s", card)

    # Last resort: probe candidate card selectors via extract.
    for cand in (
        "[data-testid*='candidate']",
        "[data-testid*='card']",
        "div[class*='Card']",
        "table tbody tr",
    ):
        try:
            rows = await agent_registry.dispatch(
                "extract", {"card": cand, "fields": {}, "maxItems": 10}, timeout_s=60
            )
        except Exception as exc:
            logger.debug("agent_record: fastjobs extract probe failed: %s", exc)
            continue
        real = [r for r in rows or [] if len(r.get("raw_text") or "") > 80]
        if len(real) >= 3:
            logger.info("agent_record: fastjobs fallback extract card: %s", cand)
            return prefix + [{"card": cand, "fields": _card_fields("find_candidates")}]

    # Diagnosable failure: surface page state (title/bodyChars).
    try:
        diag = await agent_registry.dispatch(
            "page_state",
            {
                "selectors": [
                    "input[type='password']",
                    "[data-testid*='candidate']",
                    "[data-testid*='card']",
                    "table tbody tr",
                ]
            },
            timeout_s=30,
        )
    except Exception as exc:
        logger.debug("agent_record: fastjobs page_state failed: %s", exc)
        diag = {}
    diag = diag if isinstance(diag, dict) else {}
    logger.warning("agent_record: fastjobs page_state → %s", json.dumps(diag)[:500])
    if diag.get("loginHint") or (diag.get("counts") or {}).get("input[type='password']", 0) > 0:
        raise HTTPException(
            502,
            "FastJobs employer session expired — re-login via the wizard, "
            "then retry recording",
        )
    body_chars = diag.get("bodyChars", 0)
    title = diag.get("title", "?")
    raise HTTPException(
        502,
        f"FastJobs talent-search loaded (title '{title}', {body_chars} chars) but no "
        "candidate rows — run a search returning visible candidates first.",
    )


async def _agent_discover(source: Source, req: AgentRecordRequest) -> list[dict[str, Any]]:
    """Extension-driven discovery for non-LinkedIn sites.

    For seek: run the known search steps and detect the result card live via
    find_result_card (the legacy generic scorer guessed wrong here and 502'd
    the record button). For other sites: fall back to legacy discover_flow.
    """
    from app.services.agent_relay import agent_registry

    if _is_mcf_candidates(source, req.flow_type):
        logger.info("agent_record: mcf candidate discovery for %s starting", source.name)
        query = (req.query_hint or "software engineer").strip() or "software engineer"
        # Landing navigation runs first (mirrors SEEK) so the employer SPA is
        # on the talent-search route before the search input is filled.
        prefix: list[dict[str, Any]] = [
            {"action": "navigate", "url": MCF_TALENT_SEARCH_URL},
            {"action": "wait", "seconds": 6},
        ]
        try:
            flow_res = await agent_registry.dispatch(
                "run_flow",
                {
                    "baseUrl": MCF_TALENT_SEARCH_URL,
                    "query": query,
                    "steps": prefix
                    + [
                        {"action": "fill", "selector": MCF_TALENT_SEARCH_INPUT, "param": "query"},
                        {"action": "press", "key": "Enter"},
                        {"action": "wait", "seconds": 6},
                    ],
                },
                timeout_s=120,
            )
        except RuntimeError as exc:
            raise HTTPException(502, f"Agent discovery failed: {exc}")
        if isinstance(flow_res, dict) and flow_res.get("needs_human"):
            raise HTTPException(
                502,
                "MyCareersFuture employer session expired — re-login then retry "
                f"({flow_res.get('error') or 'login page'})",
            )
        found = await agent_registry.dispatch("find_result_card", {}, timeout_s=30)
        logger.info("agent_record: mcf find_result_card → %s", found)
        if not (isinstance(found, dict) and found.get("found") and found.get("card")):
            # React SPA can paint results slowly — wait and retry once.
            await agent_registry.dispatch(
                "run_flow",
                {"baseUrl": MCF_TALENT_SEARCH_URL, "steps": [{"action": "wait", "seconds": 10}]},
                timeout_s=60,
            )
            found = await agent_registry.dispatch("find_result_card", {}, timeout_s=30)
        if isinstance(found, dict) and found.get("found") and found.get("card"):
            card = found["card"]
            if not is_root_selector(card):
                logger.info("agent_record: mcf card found: %s", card)
                return prefix + [{"card": card, "fields": _card_fields("find_candidates")}]
            logger.warning("agent_record: mcf rejected root card selector %s", card)
        # Last resort: probe candidate card selectors via extract.
        for cand in (
            "[data-testid*='talent']",
            "[data-testid*='candidate']",
            "[data-testid*='card']",
            "div[class*='Card']",
            "table tbody tr",
        ):
            try:
                rows = await agent_registry.dispatch(
                    "extract", {"card": cand, "fields": {}, "maxItems": 10}, timeout_s=60
                )
            except Exception as exc:
                logger.debug("agent_record: mcf extract probe failed: %s", exc)
                continue
            real = [r for r in rows or [] if len(r.get("raw_text") or "") > 80]
            if len(real) >= 3:
                logger.info("agent_record: mcf fallback extract card: %s", cand)
                return prefix + [{"card": cand, "fields": _card_fields("find_candidates")}]
        # Diagnosable failure: surface page state (title/bodyChars/loginHint).
        try:
            state = await agent_registry.dispatch(
                "page_state",
                {
                    "selectors": [
                        MCF_TALENT_SEARCH_INPUT,
                        "[data-testid*='talent']",
                        "[data-testid*='candidate']",
                        "input[type='password']",
                    ]
                },
                timeout_s=30,
            )
        except Exception as exc:
            logger.debug("agent_record: mcf page_state failed: %s", exc)
            state = {}
        logger.warning("agent_record: mcf page_state → %s", json.dumps(state)[:500])
        if isinstance(state, dict) and (
            state.get("loginHint")
            or (state.get("counts") or {}).get("input[type='password']", 0) > 0
        ):
            raise HTTPException(
                502,
                "MyCareersFuture employer talent-search is showing a login wall — "
                "re-login via the wizard, then retry recording",
            )
        body_chars = state.get("bodyChars", 0) if isinstance(state, dict) else 0
        title = state.get("title", "?") if isinstance(state, dict) else "?"
        raise HTTPException(
            502,
            f"MCF talent-search loaded (title '{title}', {body_chars} chars) but no "
            "candidate rows — run a search returning visible candidates first "
            f"(query '{query}' may match nothing; try broader).",
        )

    if _is_fastjobs_candidates(source, req.flow_type):
        return await _agent_discover_fastjobs(source)

    if "seek.com" in (source.domain or ""):
        logger.info("agent_record: seek discovery for %s starting", source.name)
        try:
            # NOTE: run_flow's step loop does NOT execute find_result_card
            # steps (only navigate/fill/click/press/wait/card) — the card
            # detector must be dispatched as its own top-level command
            # (supported since extension v1.5.0).
            #
            # Search via URL param, not DOM fill: the `#uncoupledFreeText`
            # input is often absent when the talent-search page first loads
            # (rendered lazily / behind feature flags), which made run_flow
            # fail with "Element not found: #uncoupledFreeText". The results
            # page itself accepts the search term as a query-string param
            # (same param the candidate deep-links use in agent/nodes.py),
            # and run_flow substitutes {query} in navigate URLs.
            search_url = (
                "https://sg.employer.seek.com/talentsearch/search/profiles"
                "?locationList=24553&nation=24553&pageNumber=1"
                "&salaryNation=24553&salaryType=MONTHLY&searchId=112aa"
                "&searchType=new_search&sortBy=relevance"
                "&uncoupledFreeText={query}&willingToRelocate=false"
            )
            flow_res = await agent_registry.dispatch(
                "run_flow",
                {
                    "baseUrl": source.base_url,
                    "query": req.query_hint or "qc",
                    "steps": [
                        {"action": "navigate", "url": search_url},
                        {"action": "wait", "seconds": 8},
                    ],
                },
                timeout_s=120,
            )
            if isinstance(flow_res, dict) and flow_res.get("needs_human"):
                raise HTTPException(
                    502,
                    "Seek session expired — "
                    + (flow_res.get("error") or "login page")
                    + "; re-login then retry",
                )
            found = await agent_registry.dispatch("find_result_card", {}, timeout_s=30)
            logger.info(
                "agent_record: seek find_result_card → %s", json.dumps(found)[:200] if found else found
            )
            if not (isinstance(found, dict) and found.get("found") and found.get("card")):
                # SEEK SPA can render slowly — wait longer and retry once.
                await agent_registry.dispatch(
                    "run_flow",
                    {"baseUrl": source.base_url, "steps": [{"action": "wait", "seconds": 10}]},
                    timeout_s=60,
                )
                found = await agent_registry.dispatch("find_result_card", {}, timeout_s=30)
                logger.info("agent_record: seek find_result_card retry → %s", found)
            if isinstance(found, dict) and found.get("found") and found.get("card"):
                logger.info("agent_record: seek card found: %s", found["card"])
                return [
                    {"action": "navigate", "url": search_url},
                    {"action": "wait", "seconds": 5},
                    {"card": found["card"], "fields": _card_fields(req.flow_type)},
                ]
            # Last resort: probe candidate card selectors via extract and
            # pick the first with 3+ text-rich rows.
            for cand in (
                "[data-testid*='card']",
                "[data-testid*='result']",
                "div[class*='Card']",
            ):
                try:
                    rows = await agent_registry.dispatch(
                        "extract", {"card": cand, "fields": {}, "maxItems": 10}, timeout_s=60
                    )
                except Exception as exc:
                    logger.debug("agent_record: seek extract probe failed: %s", exc)
                    continue
                real = [
                    r for r in rows or [] if len(r.get("raw_text") or "") > 80
                ]
                if len(real) >= 3:
                    logger.info("agent_record: seek fallback extract card: %s", cand)
                    return [
                        {"action": "navigate", "url": search_url},
                        {"action": "wait", "seconds": 5},
                        {"card": cand, "fields": _card_fields(req.flow_type)},
                    ]
            logger.warning("agent_record: seek no card detected: %s", found)
            try:
                state = await agent_registry.dispatch(
                    "page_state",
                    {
                        "selectors": [
                            "[data-testid*='card']",
                            "[data-testid*='result']",
                            "div[class*='Card']",
                            "article",
                            "input[type='password']",
                        ]
                    },
                    timeout_s=30,
                )
            except Exception as exc:
                logger.debug("agent_record: seek page_state failed: %s", exc)
                state = {}
            logger.warning(
                "agent_record: seek page_state → %s",
                json.dumps(state)[:500] if isinstance(state, dict) else state,
            )
            if isinstance(state, dict) and (
                state.get("loginHint")
                or (state.get("counts") or {}).get("input[type='password']", 0) > 0
            ):
                raise HTTPException(
                    502,
                    "Seek results page is showing a login wall — "
                    "re-login via the wizard, then retry recording",
                )
            body_chars = state.get("bodyChars", 0) if isinstance(state, dict) else 0
            if body_chars < 500:
                raise HTTPException(
                    502,
                    "Seek page barely rendered "
                    f"({body_chars} chars) — SSO/app may still be loading; "
                    "open the site, run a search manually, then retry",
                )
            title = state.get("title", "?") if isinstance(state, dict) else "?"
            raise HTTPException(
                502,
                f"Seek page loaded (title '{title}', {body_chars} chars) but no "
                "candidate rows — run a search returning visible candidates "
                f"first (query '{req.query_hint}' may match nothing; try "
                "broader), extension v1.7.4+ required",
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("agent_record: seek discovery failed")
            raise HTTPException(502, f"Agent discovery failed: {str(exc)[:120]}")

    try:
        data = await agent_registry.dispatch(
            "discover_flow",
            {
                "baseUrl": source.base_url,
                "query": req.query_hint or "software engineer",
                "flowType": req.flow_type,
            },
            timeout_s=180,
        )
    except RuntimeError as exc:
        raise HTTPException(502, f"Agent discovery failed: {exc}")
    return (data or {}).get("steps", [])

async def wizard_credentials(source_id: str, req: WizardCredentials, mode: str = "login") -> dict:
    """Type credentials into the visible login form (UI-driven sign-in)."""
    wiz = await _wiz(source_id, mode)
    try:
        result = await wiz.fill_credentials(req.username, req.password, req.submit)
    except Exception as exc:
        # Browser-level failures (unknown key, navigation race) should not 500 —
        # the wizard stays open and the user can retry or do it via the preview.
        logger.warning("fill_credentials raised on %s: %s", source_id, exc)
        result = {"ok": False, "reason": f"Browser error while filling the form: {exc}"}
    if result.get("ok"):
        # Heuristic: if we're no longer on a login-looking page, mark logged in.
        url = result.get("url", "").lower()
        if not any(p in url for p in ("login", "signin", "sign-in", "auth")):
            await wiz.mark_logged_in()
    return result


class WizardMfa(BaseModel):
    code: str = Field(..., min_length=3, max_length=10)


@router.post("/{source_id}/wizard/mfa")
async def wizard_mfa(source_id: str, req: WizardMfa, mode: str = "login") -> dict:
    wiz = await _wiz(source_id, mode)
    result = await wiz.submit_mfa(req.code)
    if result.get("ok"):
        url = result.get("url", "").lower()
        if not any(p in url for p in ("login", "signin", "sign-in", "auth", "verify", "mfa", "otp")):
            await wiz.mark_logged_in()
    return result


class WizardClick(BaseModel):
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)


@router.post("/{source_id}/wizard/click")
async def wizard_click(source_id: str, req: WizardClick, mode: str = "login") -> dict:
    """Click at screenshot coordinates (for consent screens, cookies, etc.)."""
    wiz = await _wiz(source_id, mode)
    return await wiz.click_at(req.x, req.y)


class WizardType(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)


@router.post("/{source_id}/wizard/type")
async def wizard_type(source_id: str, req: WizardType, mode: str = "login") -> dict:
    """Type into the focused element (click a field in the preview first)."""
    wiz = await _wiz(source_id, mode)
    return await wiz.type_text(req.text)


class WizardKey(BaseModel):
    key: str = Field(..., min_length=1, max_length=20)


@router.post("/{source_id}/wizard/key")
async def wizard_key(source_id: str, req: WizardKey, mode: str = "login") -> dict:
    """Press a named key (Enter, Tab, Escape…) in the wizard browser."""
    wiz = await _wiz(source_id, mode)
    return await wiz.press_key(req.key)


class WizardScroll(BaseModel):
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    delta_y: int = Field(...)


@router.post("/{source_id}/wizard/scroll")
async def wizard_scroll(source_id: str, req: WizardScroll, mode: str = "login") -> dict:
    """Scroll the wizard page (wheel) at screenshot coordinates."""
    wiz = await _wiz(source_id, mode)
    return await wiz.scroll_at(req.x, req.y, req.delta_y)


@router.post("/{source_id}/wizard/{mode}/complete", response_model=WizardCompleteResponse)
async def wizard_complete(
    source_id: str,
    mode: str,
    req: WizardCompleteRequest,
    db: AsyncSession = Depends(get_db),
) -> WizardCompleteResponse:
    source = await _get_source(source_id, db)
    wiz = _wizards.get(f"wiz-{source_id}-{mode}")
    if wiz is None:
        raise HTTPException(404, "No active wizard session (already completed or expired)")
    _wizards.pop(f"wiz-{source_id}-{mode}", None)

    try:
        if mode == "login":
            captured = await wiz.capture_state()
            source.session_state = encrypt_session_state(
                __import__("json").dumps(captured["storage_state"])
            )
            source.captured_at = datetime.utcnow()
            await db.commit()
            return WizardCompleteResponse()

        if mode != "record" or wiz.flow_type not in FLOW_TYPES:
            raise HTTPException(400, "Invalid wizard mode")

        # LLM auto-record: drive the headless browser with the search query,
        # then ask the LLM to identify the search box + result card structure.
        flow_type = wiz.flow_type
        try:
            discovered = await discover_flow(
                base_url=source.base_url,
                query=req.query_hint or "software engineer",
                flow_type=flow_type,
                session=wiz,
            )
        except Exception as exc:
            logger.exception("discover_flow failed")
            raise HTTPException(502, f"Auto-record failed: {exc}")

        steps = discovered["steps"]
        card = discovered["card"]
        embed = []
        if card:
            step: dict[str, Any] = {"card": card}
            if discovered.get("fields"):
                step["fields"] = discovered["fields"]
            embed.append(step)

        recording = SourceRecording(
            source_id=source.id, flow_type=flow_type, events=discovered.get("raw", [])
        )
        db.add(recording)

        existing = (
            await db.execute(
                select(SourceFlow).where(
                    SourceFlow.source_id == source.id,
                    SourceFlow.flow_type == flow_type,
                )
            )
        ).scalar_one_or_none()

        if existing:
            existing.steps = steps + embed
            existing.status = "active"
            existing.last_verified_at = datetime.utcnow()
            flow = existing
        else:
            flow = SourceFlow(source_id=source.id, flow_type=flow_type, steps=steps + embed)
            db.add(flow)

        await db.commit()
        await db.refresh(flow)
        return WizardCompleteResponse(
            flow_id=flow.id,
            steps=steps,
            card_selectors=(
                {"card": card, "fields": discovered.get("fields", {})} if card else None
            ),
        )
    finally:
        await wiz.close()


@router.post("/{source_id}/wizard/{mode}/cancel", status_code=204, response_model=None)
async def wizard_cancel(source_id: str, mode: str) -> None:
    wiz = _wizards.pop(f"wiz-{source_id}-{mode}", None)
    if wiz:
        await wiz.close()


# ---------------------------------------------------------------------------
# Flows: inspect / edit / test-run
# ---------------------------------------------------------------------------


@router.get("/{source_id}/flows", response_model=list[SourceFlowView])
async def list_flows(source_id: str, db: AsyncSession = Depends(get_db)) -> list[SourceFlowView]:
    await _get_source(source_id, db)
    flows = (
        await db.execute(select(SourceFlow).where(SourceFlow.source_id == source_id))
    ).scalars().all()
    return [
        SourceFlowView(
            id=f.id,
            source_id=f.source_id,
            flow_type=f.flow_type,
            steps=f.steps,
            status=f.status,
            created_at=f.created_at,
        )
        for f in flows
    ]


@router.delete("/{source_id}/flows/{flow_type}", status_code=204, response_model=None)
async def delete_flow(
    source_id: str, flow_type: str, db: AsyncSession = Depends(get_db)
) -> None:
    """Remove a recorded flow by type (find_jobs / find_candidates)."""
    if flow_type not in FLOW_TYPES:
        raise HTTPException(400, f"flow_type must be one of {FLOW_TYPES}")
    flows = (
        await db.execute(
            select(SourceFlow).where(
                SourceFlow.source_id == source_id,
                SourceFlow.flow_type == flow_type,
            )
        )
    ).scalars().all()
    if not flows:
        raise HTTPException(404, "Flow not found")
    for flow in flows:
        await db.delete(flow)
    await db.commit()


@router.patch("/{source_id}/flows/{flow_id}", response_model=SourceFlowView)
async def update_flow(
    source_id: str, flow_id: str, req: SourceFlowUpdate, db: AsyncSession = Depends(get_db)
) -> SourceFlowView:
    flow = await db.get(SourceFlow, flow_id)
    if flow is None or flow.source_id != source_id:
        raise HTTPException(404, "Flow not found")
    if req.steps is not None:
        flow.steps = req.steps
    if req.status is not None:
        flow.status = req.status
    await db.commit()
    await db.refresh(flow)
    return SourceFlowView(
        id=flow.id,
        source_id=flow.source_id,
        flow_type=flow.flow_type,
        steps=flow.steps,
        status=flow.status,
        created_at=flow.created_at,
    )


@router.post("/{source_id}/flows/{flow_id}/test")
async def test_flow(
    source_id: str, flow_id: str, query: str = "test", db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Test-run a flow and report result count (marks it broken on failure)."""
    source = await _get_source(source_id, db)
    flow = await db.get(SourceFlow, flow_id)
    if flow is None or flow.source_id != source_id:
        raise HTTPException(404, "Flow not found")

    # Pre-run self-heal: a stale stored session would bounce the flow to a
    # login wall. Best-effort re-capture via the extension relay before we
    # spend the run (never fails the test — existing wall handling still
    # applies downstream).
    entry_url = (
        _candidate_entry_url(source)
        if flow.flow_type == "find_candidates"
        else source.base_url
    )
    await self_heal_source_session(source, db, entry_url)

    result = await execute_flow(
        base_url=source.base_url,
        steps=flow.steps,
        query=query,
        storage_state_encrypted=source.session_state,
        card_selectors=flow.steps[-1] if flow.steps and flow.steps[-1].get("card") else None,
        source_id=source.id,
    )
    if result["results"]:
        flow.status = "active"
        flow.last_verified_at = datetime.utcnow()
    else:
        flow.status = "broken"
    await db.commit()
    return result
