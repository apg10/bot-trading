"""Persistence API for the terminal's UI preferences document."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from src.persistence.sqlite_store import SQLiteStore, UIPreferencesConflictError


router = APIRouter(prefix="/api/preferences", tags=["preferences"])


def validate_iso8601(s: str) -> bool:
    """Return whether *s* is a parseable timestamp in the supported year range."""
    try:
        dt = datetime.fromisoformat(s)
        return 2000 <= dt.year <= 2100
    except (ValueError, TypeError):
        return False


class UiPreferencesSave(BaseModel):
    preferences: dict
    revision: int

    @field_validator("revision")
    @classmethod
    def check_revision(cls, value: int) -> int:
        if value < 0:
            raise ValueError("UI_PREFERENCES_INVALID_REVISION")
        return value


class ViewPreferences(BaseModel):
    activeView: str
    bottomTab: str
    interval: str

    @field_validator("activeView")
    @classmethod
    def check_active_view(cls, value: str) -> str:
        if value not in ("terminal", "laboratorio", "bot"):
            raise ValueError("UI_PREFERENCES_INVALID_ACTIVEVIEW")
        return value

    @field_validator("bottomTab")
    @classmethod
    def check_bottom_tab(cls, value: str) -> str:
        if value not in ("balances", "positions", "orders", "fills", "activity"):
            raise ValueError("UI_PREFERENCES_INVALID_BOTTOMTAB")
        return value

    @field_validator("interval")
    @classmethod
    def check_interval(cls, value: str) -> str:
        allowed = (
            "1s", "1m", "3m", "5m", "15m", "30m", "1h", "2h",
            "4h", "6h", "8h", "12h", "1d", "3d", "1w",
        )
        if value not in allowed:
            raise ValueError("UI_PREFERENCES_INVALID_INTERVAL")
        return value


class MetadataPreferences(BaseModel):
    lastSaved: str
    schemaVersion: str

    @field_validator("lastSaved")
    @classmethod
    def check_last_saved(cls, value: str) -> str:
        if not validate_iso8601(value):
            raise ValueError("UI_PREFERENCES_INVALID_TIMESTAMP")
        return value

    @field_validator("schemaVersion")
    @classmethod
    def check_meta_schema(cls, value: str) -> str:
        if value != "1":
            raise ValueError("UI_PREFERENCES_INVALID_SCHEMA_VERSION")
        return value


def get_store(request: Request) -> SQLiteStore:
    """Resolve the store published by the application lifespan."""
    store = getattr(request.app.state, "_sqlite_store", None)
    if not isinstance(store, SQLiteStore):
        raise HTTPException(status_code=503, detail="UI_PREFERENCES_STORE_UNAVAILABLE")
    return store


def validate_preferences(payload: Any) -> tuple[bool, Optional[str]]:
    """Validate the complete C1 contract and return its stable error code."""
    if not isinstance(payload, dict):
        return False, "UI_PREFERENCES_INVALID_TYPE"

    if payload.get("schemaVersion") != "1":
        return False, "UI_PREFERENCES_INVALID_SCHEMA_VERSION"

    view = payload.get("view")
    if not isinstance(view, dict):
        return False, "UI_PREFERENCES_INVALID_VIEW"
    if view.get("activeView") not in ("terminal", "laboratorio", "bot"):
        return False, "UI_PREFERENCES_INVALID_ACTIVEVIEW"
    if view.get("bottomTab") not in (
        "balances", "positions", "orders", "fills", "activity"
    ):
        return False, "UI_PREFERENCES_INVALID_BOTTOMTAB"
    if view.get("interval") not in (
        "1s", "1m", "3m", "5m", "15m", "30m", "1h", "2h",
        "4h", "6h", "8h", "12h", "1d", "3d", "1w",
    ):
        return False, "UI_PREFERENCES_INVALID_INTERVAL"

    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return False, "UI_PREFERENCES_INVALID_METADATA"
    last_saved = metadata.get("lastSaved", "")
    if not isinstance(last_saved, str) or not validate_iso8601(last_saved):
        return False, "UI_PREFERENCES_INVALID_TIMESTAMP"
    if metadata.get("schemaVersion") != "1":
        return False, "UI_PREFERENCES_INVALID_METADATA_SCHEMA_VERSION"

    return True, None


@router.put("/ui")
@router.post("/ui")
async def save_preferences(
    request: Request, store: SQLiteStore = Depends(get_store)
):
    raw = await request.body()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="UI_PREFERENCES_INVALID_JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="UI_PREFERENCES_INVALID_TYPE")
    revision = payload.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise HTTPException(status_code=400, detail="UI_PREFERENCES_INVALID_REVISION")
    preferences = payload.get("preferences")
    ok, error = validate_preferences(preferences)
    if not ok:
        raise HTTPException(status_code=400, detail=error)
    try:
        new_revision = await store.save_ui_preferences(
            json.dumps(preferences), revision
        )
        return {"status": "saved", "revision": new_revision}
    except UIPreferencesConflictError as exc:
        raise HTTPException(status_code=409, detail="UI_PREFERENCES_CONFLICT") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/ui")
async def get_preferences(store: SQLiteStore = Depends(get_store)):
    try:
        result = await store.get_ui_preferences()
        if result is None:
            raise HTTPException(status_code=404, detail="UI_PREFERENCES_NOT_FOUND")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
