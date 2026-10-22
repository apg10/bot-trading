"""Immutable DTOs for explicit PAPER lifecycle state."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


VALID_LIFECYCLE_STATES = ("running", "paused", "stopped")
VALID_LIFECYCLE_ACTIONS = ("pause", "resume", "stop", "close", "reconcile")


class PaperLifecycleState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: str
    paper_session_id: str
    state: str
    last_command_id: str | None
    last_updated_ms: int

    @field_validator("state")
    @classmethod
    def validate_state(cls, v: str) -> str:
        if v not in VALID_LIFECYCLE_STATES:
            raise ValueError(
                "state must be one of 'running', 'paused', or 'stopped'"
            )
        return v


class PaperLifecycleCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    command_id: str
    account_id: str
    paper_session_id: str
    action: str
    issued_at_ms: int

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if v not in VALID_LIFECYCLE_ACTIONS:
            raise ValueError(
                "action must be one of 'pause', 'resume', 'stop', 'close', or 'reconcile'"
            )
        return v


class PaperLifecycleEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: int | None
    command_id: str
    account_id: str
    paper_session_id: str
    action: str
    previous_state: str
    resulting_state: str
    transitioned: bool
    timestamp_ms: int

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if v not in VALID_LIFECYCLE_ACTIONS:
            raise ValueError("invalid lifecycle action")
        return v

    @field_validator("previous_state", "resulting_state")
    @classmethod
    def validate_state(cls, v: str) -> str:
        if v not in VALID_LIFECYCLE_STATES:
            raise ValueError("invalid lifecycle state")
        return v


class PaperLifecycleReconciliationReport(BaseModel):
    """Immutable snapshot of the PAPER lifecycle/accounting consistency check."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str
    account_id: str
    paper_session_id: str
    expected_state: str | None
    observed_state: str | None
    consistent: bool
    discrepancies: tuple[str, ...]
    snapshot_digest: str
