"""API routes for pluggable sources: CRUD, guided wizard, flows."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
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
    WizardEvent,
    WizardPollResponse,
    WizardStartRequest,
    WizardStartResponse,
)
from app.services.encryption import encrypt_session_state
from app.services.source_flows import (
    FLOW_TYPES,
    WizardSession,
    domain_of,
    execute_flow,
    templatize,
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


@router.delete("/{source_id}", status_code=204)
async def delete_source(source_id: str, db: AsyncSession = Depends(get_db)) -> None:
    source = await _get_source(source_id, db)
    await db.delete(source)
    await db.commit()


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
    # Close any stale wizard for this source/mode.
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

    start_url = source.base_url
    wiz = WizardSession(source.id, req.flow_type or "login", domain=source.domain)
    try:
        await wiz.start(start_url, storage_state)
    except Exception as exc:
        await wiz.close()
        raise HTTPException(
            502,
            "Could not open wizard browser. Ensure the CDP browser bridge is "
            f"reachable (BRAVE_CDP_URL). Details: {exc}",
        )

    _wizards[wizard_id] = wiz
    return WizardStartResponse(wizard_id=wizard_id, mode=req.mode, start_url=start_url)


@router.get("/{source_id}/wizard/events", response_model=WizardPollResponse)
async def wizard_poll(source_id: str, mode: str = "record") -> WizardPollResponse:
    wiz = _wizards.get(f"wiz-{source_id}-{mode}")
    if wiz is None:
        raise HTTPException(404, "No active wizard session")
    fresh = await wiz.drain_events()
    return WizardPollResponse(
        events=[WizardEvent(**e) for e in fresh],
        total_events=len(wiz.events),
    )


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
        await wiz.drain_events()

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

        flow_type = wiz.flow_type
        if not wiz.events:
            raise HTTPException(
                422,
                "No interactions were recorded. Check that event polling is "
                "working (the browser tab must stay open on the source site), "
                "then record the flow again.",
            )
        steps, card_selectors = templatize(wiz.events, req.query_hint)
        if card_selectors is None:
            raise HTTPException(
                422,
                "No result card was marked. During recording, Alt-click one "
                "of the search-result cards, then press Done again.",
            )

        recording = SourceRecording(
            source_id=source.id, flow_type=flow_type, events=wiz.events
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
            existing.steps = steps + ([{"card": card_selectors["card"]}] if card_selectors else [])
            existing.status = "active"
            existing.last_verified_at = datetime.utcnow()
            flow = existing
        else:
            embed = [{"card": card_selectors["card"]}] if card_selectors else []
            flow = SourceFlow(source_id=source.id, flow_type=flow_type, steps=steps + embed)
            db.add(flow)

        await db.commit()
        await db.refresh(flow)
        return WizardCompleteResponse(
            flow_id=flow.id, steps=steps, card_selectors=card_selectors
        )
    finally:
        await wiz.close()


@router.post("/{source_id}/wizard/{mode}/cancel", status_code=204)
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
