"""API routes for pluggable sources: CRUD, guided wizard, flows."""

from __future__ import annotations

import json
import logging
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
from app.services.encryption import encrypt_session_state
from app.services.source_flows import (
    FLOW_TYPES,
    WizardSession,
    discover_flow,
    domain_of,
    execute_flow,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sources", dependencies=[])

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
        flows={f.flow_type: f.status for f in flows},
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
    if not domain:
        raise HTTPException(400, "Invalid base_url")

    existing = (
        await db.execute(select(Source).where(Source.domain == domain))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, f"A source for {domain} already exists")

    source = Source(name=req.name.strip(), base_url=url, domain=domain)
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return _source_view(source, [])


# NOTE: `from __future__ import annotations` makes `-> None` a string that
# FastAPI evaluates to NoneType — a truthy response_model, which trips the
# "204 must not have a response body" assert. Pass response_model=None.
@router.delete("/{source_id}", status_code=204, response_model=None)
async def delete_source(source_id: str, db: AsyncSession = Depends(get_db)) -> None:
    source = await _get_source(source_id, db)
    await db.delete(source)
    await db.commit()


class SourceEnabledUpdate(BaseModel):
    enabled: bool


@router.patch("/{source_id}", response_model=SourceView)
async def update_source(
    source_id: str, req: SourceEnabledUpdate, db: AsyncSession = Depends(get_db)
) -> SourceView:
    """Enable/disable a source (e.g. turn off a built-in like LinkedIn)."""
    source = await _get_source(source_id, db)
    source.enabled = req.enabled
    await db.commit()
    await db.refresh(source)
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
    if source.session_state:
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

    _wizards[wizard_id] = wiz
    return WizardStartResponse(wizard_id=wizard_id, mode=req.mode, start_url=start_url)


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
    if "linkedin.com" in source.domain:
        login_url = "https://www.linkedin.com/login"
    try:
        await agent_registry.dispatch("navigate", {"url": login_url}, timeout_s=30)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))
    return {"ok": True, "login_url": login_url}


class AgentSessionPayload(BaseModel):
    cookies: list[dict[str, Any]]


class AgentRecordRequest(BaseModel):
    flow_type: str = Field(..., description="find_jobs or find_candidates")
    query_hint: str | None = Field(default=None)


@router.post("/{source_id}/agent_session", response_model=SourceView)
async def agent_session(
    source_id: str, req: AgentSessionPayload, db: AsyncSession = Depends(get_db)
) -> SourceView:
    """Store cookies captured by the extension after a manual login.

    Stored as a Playwright-compatible storage_state blob (same format the
    wizard captures), so every existing consumer keeps working.
    """
    source = await _get_source(source_id, db)
    storage_state = {
        "cookies": req.cookies,
        "origins": [],
    }
    source.session_state = encrypt_session_state(json.dumps(storage_state))
    source.captured_at = datetime.utcnow()
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
        cookies = await agent_registry.dispatch(
            "get_cookies", {"url": source.base_url}, timeout_s=20
        )
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))
    return {"ok": True, "cookies": cookies}


@router.put("/{source_id}/agent_session", response_model=SourceView)
async def agent_session_store(
    source_id: str, req: AgentSessionPayload, db: AsyncSession = Depends(get_db)
) -> SourceView:
    """Store cookies captured by the extension after a manual login."""
    source = await _get_source(source_id, db)
    storage_state = {"cookies": req.cookies, "origins": []}
    source.session_state = encrypt_session_state(json.dumps(storage_state))
    source.captured_at = datetime.utcnow()
    await db.commit()
    flows = (
        await db.execute(select(SourceFlow).where(SourceFlow.source_id == source.id))
    ).scalars().all()
    return _source_view(source, list(flows))


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


async def _agent_discover(source: Source, req: AgentRecordRequest) -> list[dict[str, Any]]:
    """Extension-driven discovery for non-LinkedIn sites."""
    from app.services.agent_relay import agent_registry

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

    result = await execute_flow(
        base_url=source.base_url,
        steps=flow.steps,
        query=query,
        storage_state_encrypted=source.session_state,
        card_selectors=flow.steps[-1] if flow.steps and flow.steps[-1].get("card") else None,
    )
    if result["results"]:
        flow.status = "active"
        flow.last_verified_at = datetime.utcnow()
    else:
        flow.status = "broken"
    await db.commit()
    return result
