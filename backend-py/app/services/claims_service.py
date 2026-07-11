"""Claims: submit + auto-adjudicate, listing, override, settle, tracking.

Port of backend/src/claims/claims.service.ts. All money is paise.
"""

from __future__ import annotations

import base64
import random
import time
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import AuthUser
from app.core.errors import bad_request, forbidden, not_found
from app.core.money import inr, js_round
from app.models import (
    Claim,
    ClaimEvent,
    ClaimEventType,
    ClaimStatus,
    ClaimType,
    Decision,
    FraudFlag,
    FraudSeverity,
    Patient,
    Policy,
    Role,
    Tenant,
    TenantType,
    Verdict,
)
from app.models.base import utcnow
from app.schemas.ai import AiAdmission, AiBill, AiLineItem, AiPolicy, ClaimPacket
from app.schemas.claims import OverrideClaimBody, SubmitClaimBody
from app.services import serializers as S
from app.services.ai_client import ai_client

_DAY_SECONDS = 86_400


# ── helpers ──────────────────────────────────────────────────
def _verdict_to_status(v: Verdict) -> ClaimStatus:
    if v == Verdict.APPROVE:
        return ClaimStatus.APPROVED
    if v == Verdict.DENY:
        return ClaimStatus.DENIED
    return ClaimStatus.QUERIED


def _severity(s: str) -> FraudSeverity:
    if s == "HIGH":
        return FraudSeverity.HIGH
    if s == "LOW":
        return FraudSeverity.LOW
    return FraudSeverity.MEDIUM


def _scope_where(user: AuthUser):
    """Restrict a claim query to what this user may see (None = no filter)."""
    role = user.get("role")
    tenant_id = user.get("tenantId")
    if role == Role.PLATFORM_ADMIN.value:
        return None
    if role in (Role.HOSPITAL_ADMIN.value, Role.HOSPITAL_STAFF.value):
        return Claim.hospital_tenant_id == (tenant_id or "__none__")
    if role in (Role.INSURER_ADMIN.value, Role.INSURER_ADJUDICATOR.value):
        return Claim.insurer_tenant_id == (tenant_id or "__none__")
    return Claim.id == "__none__"


async def _new_claim_number(session: AsyncSession) -> str:
    for _ in range(8):
        n = random.randint(100000, 999998)
        candidate = f"CLM-{n}"
        taken = (
            await session.execute(select(Claim.id).where(Claim.claim_number == candidate))
        ).first()
        if not taken:
            return candidate
    return f"CLM-{int(time.time() * 1000)}"


_DETAIL_LOAD = (
    selectinload(Claim.hospital),
    selectinload(Claim.insurer),
    selectinload(Claim.policy),
    selectinload(Claim.patient),
    selectinload(Claim.fraud_flags),
    selectinload(Claim.events),
    selectinload(Claim.decisions),
    selectinload(Claim.overridden_by),
)


async def _load_detail(session: AsyncSession, claim_id: str, user: AuthUser) -> Claim:
    stmt = select(Claim).where(Claim.id == claim_id).options(*_DETAIL_LOAD)
    where = _scope_where(user)
    if where is not None:
        stmt = stmt.where(where)
    claim = (await session.execute(stmt)).scalar_one_or_none()
    if not claim:
        raise not_found("Claim not found")
    return claim


async def get(session: AsyncSession, user: AuthUser, claim_id: str) -> dict:
    return S.claim_detail(await _load_detail(session, claim_id, user))


# ── submit + auto-adjudicate ─────────────────────────────────
async def submit(session: AsyncSession, user: AuthUser, body: SubmitClaimBody) -> dict:
    tenant_id = user.get("tenantId")
    if not tenant_id:
        raise bad_request("No hospital on token")
    hospital = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if not hospital or hospital.type != TenantType.HOSPITAL:
        raise forbidden("Only hospitals can submit claims")

    insurer = (
        await session.execute(select(Tenant).where(Tenant.id == body.insurerTenantId))
    ).scalar_one_or_none()
    if not insurer or insurer.type != TenantType.INSURER:
        raise bad_request("Target insurer not found")

    patient = None
    if body.memberId:
        patient = (
            await session.execute(
                select(Patient)
                .where(Patient.member_id == body.memberId)
                .options(selectinload(Patient.policy))
            )
        ).scalar_one_or_none()
        if patient and patient.insurer_tenant_id != insurer.id:
            patient = None

    policy: Policy | None = patient.policy if patient else None
    if policy is None:
        policy = (
            await session.execute(
                select(Policy)
                .where(Policy.insurer_tenant_id == insurer.id)
                .order_by(Policy.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if not policy:
        raise bad_request("This insurer has no policy configured yet")

    billed = (
        body.totalPaise
        if body.totalPaise is not None
        else sum(li.amountPaise for li in body.lineItems)
    )
    admitted_at = _parse_dt(body.admittedAt)
    discharged_at = _parse_dt(body.dischargedAt)
    los = max(
        1, js_round((discharged_at - admitted_at).total_seconds() / _DAY_SECONDS)
    )

    claim_number = await _new_claim_number(session)
    submitted_at = utcnow()
    line_items = [li.model_dump() for li in body.lineItems]

    claim = Claim(
        claim_number=claim_number,
        type=ClaimType(body.type) if body.type else ClaimType.CASHLESS,
        hospital_tenant_id=hospital.id,
        insurer_tenant_id=insurer.id,
        policy_id=policy.id,
        patient_id=patient.id if patient else None,
        admission_id=body.admissionId,
        patient_name=body.patientName,
        patient_age=body.patientAge,
        patient_gender=body.patientGender,
        diagnosis=body.diagnosis,
        procedure=body.procedure,
        admitted_at=admitted_at,
        discharged_at=discharged_at,
        length_of_stay_days=los,
        sum_insured_paise=policy.sum_insured_paise,
        billed_paise=billed,
        line_items=line_items,
        status=ClaimStatus.PROCESSING,
        submitted_by_id=user.get("sub"),
        submitted_at=submitted_at,
    )
    session.add(claim)
    await session.flush()
    session.add_all(
        [
            ClaimEvent(
                claim_id=claim.id,
                type=ClaimEventType.SUBMITTED,
                message=(
                    f"Claim {claim_number} submitted by {hospital.name} to {insurer.name}."
                ),
                actor_id=user.get("sub"),
            ),
            ClaimEvent(
                claim_id=claim.id,
                type=ClaimEventType.AI_STARTED,
                message="AI adjudication engine started.",
            ),
        ]
    )
    # Persist the claim + SUBMITTED/AI_STARTED events *before* invoking the AI, so a
    # failed/timed-out adjudication still leaves a visible, auditable claim record
    # (matches claims.service.ts, where claim.create committed before ai.adjudicate).
    await session.commit()

    packet = ClaimPacket(
        ref=claim_number,
        type=claim.type.value,
        hospital=hospital.name,
        insurer=insurer.name,
        policy=AiPolicy(
            policyNo=policy.plan_code,
            sumInsuredPaise=policy.sum_insured_paise,
            roomRentCapPerDayPaise=policy.room_rent_cap_per_day_paise,
            copayPct=policy.copay_pct,
            coveredProcedures=list(policy.covered_procedures or []),
            exclusions=list(policy.exclusions or []),
        ),
        admission=AiAdmission(
            admittedAt=body.admittedAt[:10],
            dischargedAt=body.dischargedAt[:10],
            lengthOfStayDays=los,
            procedure=body.procedure,
            diagnosis=body.diagnosis,
        ),
        bill=AiBill(
            lineItems=[AiLineItem(desc=li.desc, amountPaise=li.amountPaise) for li in body.lineItems],
            totalPaise=billed,
        ),
        dischargeSummary=(
            f"Patient {body.patientName} ({body.patientAge}y) admitted for "
            f"{body.procedure}; {los} day(s); billed ₹{inr(billed)}."
        ),
    )
    decision, latency_ms = await ai_client.adjudicate(packet)

    decided_at = utcnow()
    tat_seconds = max(0, js_round((decided_at - submitted_at).total_seconds()))
    status = _verdict_to_status(Verdict(decision.verdict))

    session.add(
        Decision(
            claim_id=claim.id,
            verdict=Verdict(decision.verdict),
            approved_amount_paise=decision.approvedAmountPaise,
            confidence=decision.confidence,
            rationale=decision.rationale,
            cited_clause_refs=list(decision.citedClauseRefs),
            model=decision.model,
            latency_ms=latency_ms,
        )
    )
    for f in decision.fraudFlags:
        session.add(
            FraudFlag(
                claim_id=claim.id,
                signal=f.signal,
                severity=_severity(f.severity),
                detail=f.detail,
            )
        )

    confidence_pct = js_round(decision.confidence * 100)
    session.add(
        ClaimEvent(
            claim_id=claim.id,
            type=ClaimEventType.AI_DECISION,
            message=(
                f"AI verdict: {decision.verdict} ({confidence_pct}% confidence, "
                f"{latency_ms}ms). {decision.rationale}"
            ),
        )
    )
    if decision.fraudFlags:
        signals = ", ".join(f.signal for f in decision.fraudFlags)
        session.add(
            ClaimEvent(
                claim_id=claim.id,
                type=ClaimEventType.FRAUD_FLAGGED,
                message=f"{len(decision.fraudFlags)} anomaly signal(s): {signals}.",
            )
        )
    if decision.verdict == "QUERY":
        session.add(
            ClaimEvent(
                claim_id=claim.id,
                type=ClaimEventType.QUERY_RAISED,
                message="Routed for review — additional information required.",
            )
        )

    claim.status = status
    claim.verdict = Verdict(decision.verdict)
    claim.approved_amount_paise = decision.approvedAmountPaise
    claim.confidence = decision.confidence
    claim.rationale = decision.rationale
    claim.cited_clause_refs = list(decision.citedClauseRefs)
    claim.ai_model = decision.model
    claim.ai_latency_ms = latency_ms
    claim.tat_seconds = tat_seconds
    claim.decided_at = decided_at

    await session.commit()
    return await get(session, user, claim.id)


async def extract_document(file_bytes: bytes | None, mimetype: str | None) -> dict:
    if not file_bytes:
        raise bad_request("No file uploaded")
    encoded = base64.b64encode(file_bytes).decode("ascii")
    return await ai_client.extract_document(encoded, mimetype or "image/jpeg")


# ── listing + detail ─────────────────────────────────────────
async def list_claims(session: AsyncSession, user: AuthUser, status: str | None) -> list[dict]:
    stmt = (
        select(Claim)
        .options(
            selectinload(Claim.hospital),
            selectinload(Claim.insurer),
            selectinload(Claim.fraud_flags),
        )
        .order_by(Claim.submitted_at.desc())
        .limit(200)
    )
    where = _scope_where(user)
    if where is not None:
        stmt = stmt.where(where)
    if status:
        stmt = stmt.where(Claim.status == ClaimStatus(status))
    claims = (await session.execute(stmt)).scalars().all()
    return [S.claim_summary(c, len(c.fraud_flags)) for c in claims]


async def track(session: AsyncSession, claim_number: str) -> dict:
    claim = (
        await session.execute(
            select(Claim)
            .where(Claim.claim_number == claim_number)
            .options(
                selectinload(Claim.hospital),
                selectinload(Claim.insurer),
                selectinload(Claim.events),
                selectinload(Claim.fraud_flags),
            )
        )
    ).scalar_one_or_none()
    if not claim:
        raise not_found("No claim with that number")
    return S.claim_track(claim, len(claim.fraud_flags))


# ── insurer override / settle ────────────────────────────────
async def override(
    session: AsyncSession, user: AuthUser, claim_id: str, body: OverrideClaimBody
) -> dict:
    claim = await _find_scoped(session, user, claim_id)

    if body.verdict == "APPROVE":
        status = ClaimStatus.SETTLED if body.settle else ClaimStatus.APPROVED
    elif body.verdict == "DENY":
        status = ClaimStatus.DENIED
    else:
        status = ClaimStatus.UNDER_REVIEW

    if body.verdict == "APPROVE":
        approved = (
            body.approvedAmountPaise
            if body.approvedAmountPaise is not None
            else (
                claim.approved_amount_paise
                if claim.approved_amount_paise is not None
                else claim.billed_paise
            )
        )
    elif body.verdict == "DENY":
        approved = 0
    else:
        approved = claim.approved_amount_paise

    now = utcnow()
    claim.status = status
    claim.verdict = Verdict(body.verdict)
    claim.approved_amount_paise = approved
    claim.overridden_by_id = user.get("sub")
    claim.override_note = body.note
    claim.overridden_at = now
    if body.settle:
        claim.settled_at = now

    amount_suffix = (
        f" (₹{inr(approved)})" if body.verdict == "APPROVE" and approved is not None else ""
    )
    session.add(
        ClaimEvent(
            claim_id=claim_id,
            type=ClaimEventType.OVERRIDDEN,
            message=f"Adjudicator override → {body.verdict}{amount_suffix}. {body.note}",
            actor_id=user.get("sub"),
        )
    )
    if body.settle:
        session.add(
            ClaimEvent(
                claim_id=claim_id,
                type=ClaimEventType.SETTLED,
                message="Claim settled — payout released.",
                actor_id=user.get("sub"),
            )
        )
    await session.commit()
    return await get(session, user, claim_id)


async def settle(session: AsyncSession, user: AuthUser, claim_id: str) -> dict:
    claim = await _find_scoped(session, user, claim_id)
    claim.status = ClaimStatus.SETTLED
    claim.settled_at = utcnow()
    session.add(
        ClaimEvent(
            claim_id=claim_id,
            type=ClaimEventType.SETTLED,
            message="Claim settled — payout released.",
            actor_id=user.get("sub"),
        )
    )
    await session.commit()
    return await get(session, user, claim_id)


async def respond_query(
    session: AsyncSession, user: AuthUser, claim_id: str, message: str
) -> dict:
    claim = await _find_scoped(session, user, claim_id)
    claim.status = ClaimStatus.UNDER_REVIEW
    session.add(
        ClaimEvent(
            claim_id=claim_id,
            type=ClaimEventType.NOTE,
            message=f"Hospital response: {message}",
            actor_id=user.get("sub"),
        )
    )
    await session.commit()
    return await get(session, user, claim_id)


# ── internal ─────────────────────────────────────────────────
async def _find_scoped(session: AsyncSession, user: AuthUser, claim_id: str) -> Claim:
    stmt = select(Claim).where(Claim.id == claim_id)
    where = _scope_where(user)
    if where is not None:
        stmt = stmt.where(where)
    claim = (await session.execute(stmt)).scalar_one_or_none()
    if not claim:
        raise not_found("Claim not found")
    return claim


def _parse_dt(value: str) -> datetime:
    """Parse an ISO date/datetime string into a naive UTC datetime."""
    s = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt
