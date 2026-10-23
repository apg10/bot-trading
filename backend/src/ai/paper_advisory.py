"""Fail-closed advisory gate for PAPER entry decisions.

The advisory is a small, immutable hand-off from the N02 analysis boundary.
It is not an execution decision: the PAPER pipeline still performs its normal
lifecycle, market, strategy, risk, and symbol-filter checks before this gate.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

from src.models.audit_contract import N02AuditRecord


class AdvisoryResult(BaseModel):
    """The bounded advisory envelope accepted by the PAPER entry gate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    approved: bool
    reason: str
    snapshot_id: str
    valid_until_ms: int


class AdvisoryAuditEnvelope(BaseModel):
    """Advisory result paired with its caller-owned immutable N02 audit record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    advisory: AdvisoryResult
    audit_record: N02AuditRecord

    @model_validator(mode="after")
    def _validate_linkage(self) -> "AdvisoryAuditEnvelope":
        if self.advisory.snapshot_id != self.audit_record.snapshot_id:
            raise ValueError(
                "AdvisoryAuditEnvelope: snapshot_id mismatch between advisory and audit record"
            )
        if self.advisory.valid_until_ms != self.audit_record.valid_until_ms:
            raise ValueError(
                "AdvisoryAuditEnvelope: valid_until_ms mismatch between advisory and audit record"
            )
        return self


def evaluate_advisory(
    advisory_input: AdvisoryResult | None,
    current_bar_identity: str,
    captured_at_ms: int,
) -> tuple[bool, str]:
    """Return whether an advisory is valid for the current closed bar.

    ``snapshot_id`` is the advisory's context identity.  Comparing it with the
    current bar identity prevents an advisory for one market bar from being
    reused for another.  Checks are deliberately ordered so that the public
    reason is deterministic.
    """
    if advisory_input is None:
        return True, "advisory_not_provided"
    if advisory_input.valid_until_ms < captured_at_ms:
        return False, "advisory_expired"
    if not advisory_input.approved:
        return False, "advisory_not_approved"
    if advisory_input.snapshot_id != current_bar_identity:
        return False, "advisory_context_mismatch"
    return True, "advisory_approved"
