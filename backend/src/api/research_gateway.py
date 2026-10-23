"""Default-deny ASGI boundary for the approved research API surface.

This middleware is intentionally scoped to research routes only.  Ordinary
UI/API traffic that does not match any catalogued path reaches the underlying
application unchanged.
"""

from __future__ import annotations

import asyncio
import json
import resource
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from src.api.research_allowlist import (
    ALLOWED,
    APPROVED_ROUTES,
    BLOCKED,
    BLOCKED_ROUTES,
    DEFERRED,
    DEFERRED_ROUTES,
    DENIED,
    check_allowlist,
    is_catalogued_path,
)
from src.models.research_audit import ResearchRequestAuditRecord


ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[None]], Callable[..., Awaitable[None]]], Awaitable[None]]
_REDACTED_ROUTE = "/<unrecognized-route>"
_ANALYSIS_PATHS: frozenset[str] = frozenset(
    entry.path
    for entry in BLOCKED_ROUTES + DEFERRED_ROUTES
    if entry.permission_category in {"analysis", "N02"}
)

# This is deliberately process-local.  A deployment with multiple workers has
# one budget per worker, matching the scope of R4's process limit.
_analysis_semaphore = asyncio.Semaphore(2)

# Pre-compute the set of catalogued paths so we can quickly gate on "is this a
# research route at all?"  Ordinary traffic bypasses the gateway entirely.
_CATALOGUED_PATHS: frozenset[str] = frozenset(
    entry.path for entry in APPROVED_ROUTES + BLOCKED_ROUTES + DEFERRED_ROUTES
)


class ResearchGatewayMiddleware:
    """Check exact method/path pairs before Starlette parses or routes a request."""

    def __init__(self, app: ASGIApp, tool_id: str, audit_store: Any | None = None) -> None:
        if (
            not isinstance(tool_id, str)
            or not tool_id.strip()
            or "://" in tool_id
            or tool_id.startswith("/")
        ):
            raise ValueError("tool_id must be a stable non-empty string")
        self.app = app
        self.tool_id = tool_id
        self.audit_store = audit_store
        self.response_cap_bytes = 1_048_576  # 1 MiB
        # The application owns the real health probe.  This hook lets an
        # application/deployment mark local inference unavailable without the
        # gateway importing or probing an inference client.
        self.inference_available = True

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Awaitable[dict[str, Any]]], send: Callable[..., Awaitable[None]]) -> None:
        scope_type = scope.get("type")
        if scope_type not in ("http", "websocket"):
            # Lifespan must reach the existing application unchanged.
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        request_id = str(uuid.uuid4())
        method = str(scope.get("method", "WEBSOCKET")).upper()
        path = scope.get("path")
        if not isinstance(path, str):
            path = ""

        # --- Ordinary traffic: bypass entirely if path not in catalog ---
        # Keep this check before the WebSocket policy as well: the gateway is
        # scoped to catalogued research paths and must not become a global WS
        # firewall for unrelated UI/API traffic.
        if path not in _CATALOGUED_PATHS and not is_catalogued_path(path):
            await self.app(scope, receive, send)
            return

        # --- WebSocket: catalogued research paths are always rejected ---
        if scope_type == "websocket":
            await self._send_websocket_close(send)
            await self._audit(
                request_id=request_id,
                method=method,
                path=_REDACTED_ROUTE,
                decision=DENIED,
                outcome="rejected",
                public_result_metadata=None,
                error_code="RESEARCH_WEBSOCKET_NOT_ALLOWED",
                started=started,
                scope=scope,
            )
            return

        raw_path = scope.get("raw_path")
        encoded = isinstance(raw_path, (bytes, bytearray)) and b"%" in raw_path
        if not isinstance(raw_path, (bytes, bytearray)) and "%" in path:
            encoded = True

        budgeted_route = path in _ANALYSIS_PATHS
        cpu_started = self._cpu_seconds()
        budget_acquired = False

        async def release_budget() -> None:
            nonlocal budget_acquired
            if budget_acquired:
                # Give other already-created request tasks a chance to contend
                # before releasing the slot (and keep the accounting bounded
                # to the lifetime of this request).
                await asyncio.sleep(0)
                _analysis_semaphore.release()
                budget_acquired = False

        if budgeted_route:
            try:
                await asyncio.wait_for(_analysis_semaphore.acquire(), timeout=0.1)
            except asyncio.TimeoutError:
                cpu_seconds = self._cpu_seconds() - cpu_started
                await self._send_rejection(send, request_id, DENIED, status_code=429)
                await self._audit(
                    request_id=request_id,
                    method=method,
                    path=path,
                    decision=DENIED,
                    outcome="rejected",
                    public_result_metadata={"status_code": 429, "cpu_seconds": cpu_seconds},
                    error_code=None,
                    started=started,
                    scope=scope,
                )
                return
            budget_acquired = True

        # This is the sole authorization decision for catalogued paths.  It
        # runs before audit persistence and before any downstream account or
        # session lookup can occur.
        decision = DENIED if encoded else self._authorize(method, path)

        # DEFERRED routes remain blocked by the documentary allowlist, but the
        # availability gate is kept here so an admitted future N02 route cannot
        # dispatch when the application has marked local inference unavailable.
        if decision == DEFERRED and not self._inference_is_available(scope):
            cpu_seconds = self._cpu_seconds() - cpu_started
            await self._send_rejection(send, request_id, DEFERRED, status_code=503)
            await self._audit(
                request_id=request_id,
                method=method,
                path=path,
                decision=DEFERRED,
                outcome="unavailable",
                public_result_metadata={
                    "status_code": 503,
                    "inference_available": False,
                    "cpu_seconds": cpu_seconds,
                },
                error_code=None,
                started=started,
                scope=scope,
            )
            await release_budget()
            return

        # --- Denied / blocked / deferred: reject before handler ---
        if decision != ALLOWED:
            await self._send_rejection(send, request_id, decision)
            metadata = {"cpu_seconds": self._cpu_seconds() - cpu_started} if budgeted_route else None
            await self._audit(
                request_id=request_id,
                method=method,
                path=path if decision != DENIED else _REDACTED_ROUTE,
                decision=decision,
                outcome="rejected",
                public_result_metadata=metadata,
                error_code=None if metadata is not None else "RESEARCH_ROUTE_NOT_ALLOWED",
                started=started,
                scope=scope,
            )
            await release_budget()
            return

        # --- Admitted: durable admission audit BEFORE dispatch ---
        admission_audit = self._build_audit_record(
            request_id=request_id,
            method=method,
            path=path,
            decision=ALLOWED,
            outcome="admitted",
            public_result_metadata={"allowlist_decision": "ALLOWED"},
            error_code=None,
            started=started,
            scope=scope,
        )
        if not await self._persist_audit(admission_audit, scope=scope):
            # If audit persistence fails, do NOT dispatch the handler.
            await self._send_rejection(send, request_id, DENIED)
            await self._audit(
                request_id=request_id,
                method=method,
                path=path,
                decision=DENIED,
                outcome="rejected",
                public_result_metadata=None,
                error_code="RESEARCH_AUDIT_PERSISTENCE_FAILURE",
                started=started,
                scope=scope,
            )
            await release_budget()
            return

        # Check the budget once more immediately before dispatch.  This keeps
        # an expensive admission/persistence path from handing work to the
        # application after the per-request CPU allowance is already spent.
        cpu_seconds = self._cpu_seconds() - cpu_started
        if budgeted_route and cpu_seconds > 2.0:
            await self._send_rejection(send, request_id, ALLOWED, status_code=429)
            await self._audit(
                request_id=request_id,
                method=method,
                path=path,
                decision=ALLOWED,
                outcome="rejected",
                public_result_metadata={"status_code": 429, "cpu_seconds": cpu_seconds},
                error_code=None,
                started=started,
                scope=scope,
            )
            await release_budget()
            return

        # --- Dispatch to handler and capture completion outcome ---
        status_code = 500
        response_started = False
        response_body_bytes = bytearray()
        response_cap_exceeded = False
        response_messages: list[dict[str, Any]] = []

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal status_code, response_started, response_body_bytes, response_cap_exceeded
            if message.get("type") == "http.response.start":
                response_started = True
                status_code = int(message.get("status", 500))
                response_messages.append(message)
            elif message.get("type") == "http.response.body":
                body = message.get("body", b"")
                if body:
                    # Retain only one byte beyond the cap.  This preserves the
                    # exact cap decision without allowing an upstream stream
                    # to turn the gateway into an unbounded buffer.
                    remaining = max(0, self.response_cap_bytes + 1 - len(response_body_bytes))
                    response_body_bytes.extend(body[:remaining])
                    if len(response_body_bytes) > self.response_cap_bytes:
                        response_cap_exceeded = True
                if not response_cap_exceeded:
                    response_messages.append(message)
            else:
                await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            await release_budget()
            await self._audit(
                request_id=request_id,
                method=method,
                path=path,
                decision=ALLOWED,
                outcome="error",
                public_result_metadata=None,
                error_code="RESEARCH_UPSTREAM_ERROR",
                started=started,
                scope=scope,
            )
            raise
        else:
            cpu_seconds = self._cpu_seconds() - cpu_started
            cpu_budget_exceeded = budgeted_route and cpu_seconds > 2.0
            if response_cap_exceeded:
                status_code = 413
                await self._send_rejection(send, request_id, ALLOWED, status_code=413)
            elif cpu_budget_exceeded:
                status_code = 429
                await self._send_rejection(send, request_id, ALLOWED, status_code=429)
            else:
                for response_message in response_messages:
                    await send(response_message)
            outcome = "success" if 200 <= status_code < 400 else "rejected" if status_code < 500 else "unavailable"
            metadata = {
                "status_code": status_code,
                "response_cap_exceeded": response_cap_exceeded,
            }
            if budgeted_route:
                metadata["cpu_seconds"] = cpu_seconds
            await self._audit(
                request_id=request_id,
                method=method,
                path=path,
                decision=ALLOWED,
                outcome=outcome,
                public_result_metadata=metadata if response_started or response_cap_exceeded or cpu_budget_exceeded else None,
                error_code=None,
                started=started,
                scope=scope,
            )
            await release_budget()

    # ------------------------------------------------------------------ private

    async def _send_websocket_close(self, send: Callable[..., Awaitable[None]]) -> None:
        """Send a proper ASGI WebSocket close frame."""
        await send({"type": "websocket.close", "code": 1008})

    async def _send_rejection(
        self,
        send: Callable[..., Awaitable[None]],
        request_id: str,
        decision: str,
        *,
        status_code: int = 403,
    ) -> None:
        payload = {
            "detail": {
                "code": "RESEARCH_ROUTE_NOT_ALLOWED",
                "decision": decision,
                "request_id": request_id,
            }
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        await send({"type": "http.response.start", "status": status_code, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    def _cpu_seconds() -> float:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return usage.ru_utime + usage.ru_stime

    def _inference_is_available(self, scope: dict[str, Any]) -> bool:
        """Read an application-owned availability hook without probing N02."""
        scoped_value = scope.get("inference_available")
        if isinstance(scoped_value, bool):
            return scoped_value
        return self.inference_available is not False

    def _build_audit_record(
        self, *, request_id: str, method: str, path: str, decision: str,
        outcome: str, public_result_metadata: dict[str, Any] | None,
        error_code: str | None, started: float, scope: dict[str, Any],
    ) -> ResearchRequestAuditRecord:
        return ResearchRequestAuditRecord(
            request_id=request_id,
            tool_id=self.tool_id,
            method=method,
            canonical_route=path,
            allowlist_decision=decision,
            sanitized_params={},
            provenance={"schema_version": "research_gateway.v1", "source": "research_gateway"},
            outcome=outcome,
            public_result_metadata=public_result_metadata,
            error_code=error_code,
            timing_ms=(time.perf_counter() - started) * 1000.0,
            captured_at_ms=int(time.time() * 1000),
        )

    @staticmethod
    def _authorize(method: str, path: str) -> str:
        """Authorize a catalogued request without consulting business state."""
        return check_allowlist(method, path)

    async def _persist_audit(
        self, record: ResearchRequestAuditRecord, *, scope: dict[str, Any] | None = None
    ) -> bool:
        """Return True if the record was durably persisted; False otherwise."""
        store = self.audit_store
        if store is None:
            app_state = (scope or {}).get("app", self.app)
            store = getattr(getattr(app_state, "state", None), "_sqlite_store", None)
        if store is None or not hasattr(store, "save_research_request_audit_record"):
            return False
        try:
            await store.save_research_request_audit_record(record)
            return True
        except Exception:
            return False

    async def _audit(
        self, *, request_id: str, method: str, path: str, decision: str,
        outcome: str, public_result_metadata: dict[str, Any] | None,
        error_code: str | None, started: float, scope: dict[str, Any],
    ) -> None:
        """Best-effort audit: never raise, never block the caller."""
        record = self._build_audit_record(
            request_id=request_id, method=method, path=path, decision=decision,
            outcome=outcome, public_result_metadata=public_result_metadata,
            error_code=error_code, started=started, scope=scope,
        )
        try:
            await self._persist_audit(record)
        except Exception:
            pass  # best-effort only; never raise or block


# Short names make direct ASGI composition and framework configuration clear.
ResearchGateway = ResearchGatewayMiddleware

__all__ = [
    "ResearchGateway",
    "ResearchGatewayMiddleware",
    "ALLOWED",
    "APPROVED_ROUTES",
    "BLOCKED",
    "BLOCKED_ROUTES",
    "DEFERRED",
    "DEFERRED_ROUTES",
    "DENIED",
    "check_allowlist",
]
