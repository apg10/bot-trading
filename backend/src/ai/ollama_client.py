"""N02: Qwen explícito, asesor estructurado; sin fallback ni autoridad operativa.

Perfil aprobado tras benchmark local: think=false, 8192/2048 tokens, 0.2,
techo 110 s. La cola también consume vigencia. Un gate de proceso serializa
inferencia incluso entre instancias/loops; no usar varios workers para prometer
un límite global de toda la instalación sin coordinación externa.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    import httpx

from .scenario_models import (
    QWEN_MODEL, ScenarioAnalysisResult, ScenarioError, ScenarioOutput, ScenarioSnapshot,
)

logger = logging.getLogger(__name__)
_INFERENCE_GATE = threading.Lock()
MAX_JOBS = 64
MAX_BODY_BYTES = 65_536
MAX_JSON_DEPTH = 64
OPTIONS = {"num_ctx": 8192, "num_predict": 2048, "temperature": 0.2}


def _httpx_module():
    # httpx está actualmente en el extra test del proyecto (packaging fuera de
    # N02). Conservar import lazy: su ausencia no debe romper el arranque/API.
    try:
        import httpx
    except ImportError:
        raise ScenarioError("unavailable") from None
    return httpx


class OllamaMessage(BaseModel):
    model_config = ConfigDict(strict=True)
    role: str
    content: str
    thinking: str | None = None


class OllamaResponse(BaseModel):
    model_config = ConfigDict(strict=True)
    model: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_./:-]+$")
    created_at: str
    message: OllamaMessage
    done: bool
    done_reason: str
    total_duration: int | None = Field(None, ge=0, le=2**63 - 1)
    load_duration: int | None = Field(None, ge=0, le=2**63 - 1)
    prompt_eval_count: int | None = Field(None, ge=0, le=2**63 - 1)
    eval_count: int | None = Field(None, ge=0, le=2**63 - 1)
    eval_duration: int | None = Field(None, ge=0, le=2**63 - 1)


def _strict_json(content: str | bytes) -> Any:
    if len(content) > MAX_BODY_BYTES:
        raise ValueError("JSON too large")
    text = content.decode("utf-8") if isinstance(content, bytes) else content
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError("JSON too deep")
        elif char in "]}":
            depth -= 1

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("nonfinite number")
        return parsed

    def constant(_value):
        raise ValueError("nonfinite constant")

    def integer(value):
        if len(value.lstrip("-")) > 64:
            raise ValueError("integer too large")
        return int(value)

    return json.loads(text, object_pairs_hook=pairs, parse_float=number,
                      parse_int=integer, parse_constant=constant)


@dataclass
class _Job:
    snapshot: ScenarioSnapshot
    fingerprint: str
    task: asyncio.Task[ScenarioAnalysisResult]
    waiters: int = 0


class OllamaClient:
    def __init__(self, base_url: str = "http://localhost:11434", model: str = QWEN_MODEL,
                 *, http: httpx.AsyncClient | None = None,
                 clock_ms: Callable[[], int] | None = None, timeout_s: float = 110.0):
        if model != QWEN_MODEL:
            raise ValueError("N02 exige el tag Qwen aprobado; no hay fallback")
        if not math.isfinite(timeout_s) or not 0 < timeout_s <= 110.0:
            raise ValueError("El presupuesto debe ser positivo y como máximo 110 s")
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._http = http
        self._owns_http = http is None
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._jobs: OrderedDict[str, _Job] = OrderedDict()
        self._retiring: set[asyncio.Task] = set()
        self._active_chats: set[asyncio.Task] = set()
        self._close_task: asyncio.Task | None = None
        self._closed = False

    @property
    def model(self) -> str:
        return QWEN_MODEL

    def _get_http(self):
        if self._closed:
            raise ScenarioError("client_closed")
        if self._http is None:
            httpx = _httpx_module()
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_s, connect=5.0))
        return self._http

    async def _require_model(self):
        status, body = await self._read_http("GET", "/api/tags")
        if status != 200:
            raise ScenarioError("unavailable")
        try:
            models = _strict_json(body)["models"]
            if not isinstance(models, list) or any(not isinstance(item, dict) for item in models):
                raise ValueError("invalid tags")
        except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
            raise ScenarioError("unavailable") from None
        if not any(item.get("name") == QWEN_MODEL for item in models):
            raise ScenarioError("model_missing")

    async def _read_http(self, method, path, payload=None):
        """Acotar durante recepción, no después de que post() bufee todo."""
        async with self._get_http().stream(
            method, f"{self.base_url}{path}", json=payload,
            headers={"Accept-Encoding": "identity"},
        ) as response:
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > MAX_BODY_BYTES:
                    raise ScenarioError("invalid_structure")
                body.extend(chunk)
            return response.status_code, bytes(body)

    @asynccontextmanager
    async def _gate(self, snapshot: ScenarioSnapshot | None = None):
        while not _INFERENCE_GATE.acquire(blocking=False):
            if self._closed:
                raise ScenarioError("client_closed")
            if snapshot is not None:
                snapshot.assert_current(self._clock_ms())
            await asyncio.sleep(0.005)
        try:
            if self._closed:
                raise ScenarioError("client_closed")
            if snapshot is not None:
                snapshot.assert_current(self._clock_ms())
            yield
        finally:
            _INFERENCE_GATE.release()

    async def chat(self, messages: list[OllamaMessage], *, response_schema: dict | None = None,
                   snapshot: ScenarioSnapshot | None = None) -> OllamaResponse:
        """Transporte serializado; solo analyze_snapshot acepta su contenido como asesoría."""
        if self._closed:
            raise ScenarioError("client_closed")
        httpx = _httpx_module()
        current = asyncio.current_task()
        self._active_chats.add(current)
        effective = None
        try:
            async with asyncio.timeout(self.timeout_s):
                async with self._gate(snapshot):
                    await self._require_model()
                    if snapshot is not None:
                        snapshot.assert_current(self._clock_ms())
                    if (len(messages) > 4 or any(len(msg.content) > 32_768 for msg in messages)):
                        raise ScenarioError("invalid_snapshot")
                    payload = {"model": QWEN_MODEL, "stream": False, "think": False,
                               "options": dict(OPTIONS), "keep_alive": "5m",
                               "messages": [msg.model_dump(exclude_none=True) for msg in messages]}
                    if response_schema is not None:
                        payload["format"] = response_schema
                    if len(json.dumps(payload).encode()) > 65_536:
                        raise ScenarioError("invalid_snapshot")
                    status, body = await self._read_http("POST", "/api/chat", payload)
                    if status != 200:
                        raise ScenarioError("model_missing" if status == 404 else "unavailable")
                    try:
                        data = _strict_json(body)
                    except (ValueError, TypeError, UnicodeError, RecursionError):
                        raise ScenarioError("invalid_json") from None
                    reported = data.get("model") if isinstance(data, dict) else None
                    if (isinstance(reported, str) and len(reported) <= 128
                            and all(c.isascii() and (c.isalnum() or c in "_.:/-") for c in reported)):
                        effective = reported
                    try:
                        result = OllamaResponse.model_validate(data)
                    except ValidationError:
                        raise ScenarioError("invalid_structure", effective_model=effective) from None
                    if result.model != QWEN_MODEL:
                        raise ScenarioError("model_mismatch", effective_model=result.model)
                    if not result.done or result.done_reason != "stop" or (
                        result.eval_count is not None and result.eval_count > OPTIONS["num_predict"]
                    ):
                        raise ScenarioError("incomplete_generation", effective_model=result.model)
                    if result.message.role != "assistant":
                        raise ScenarioError("invalid_structure", effective_model=result.model)
                    if result.message.thinking or "<think>" in result.message.content.lower():
                        raise ScenarioError("unexpected_reasoning", effective_model=result.model)
                    return result
        except (TimeoutError, httpx.TimeoutException):
            raise ScenarioError("timeout", effective_model=effective) from None
        except httpx.HTTPError:
            raise ScenarioError("unavailable", effective_model=effective) from None
        finally:
            self._active_chats.discard(current)

    def _prune(self):
        now = self._clock_ms()
        for identity, job in list(self._jobs.items()):
            if job.task.done() and (job.snapshot.valid_until_ms < now or job.task.cancelled()
                                    or job.task.exception() is not None):
                self._jobs.pop(identity, None)

    async def analyze_snapshot(self, snapshot: ScenarioSnapshot) -> ScenarioAnalysisResult:
        if self._closed:
            raise ScenarioError("client_closed")
        try:
            # A frozen Pydantic object can still contain mutable dicts/lists.
            # Revalidate a detached copy before hashing or giving it to a job.
            snapshot = ScenarioSnapshot.model_validate(snapshot.model_dump() if isinstance(
                snapshot, ScenarioSnapshot) else snapshot)
            snapshot = snapshot.model_copy(deep=True)
        except (ValidationError, TypeError, ValueError):
            raise ScenarioError("invalid_snapshot") from None
        snapshot.assert_current(self._clock_ms())
        self._prune()
        fingerprint = snapshot.fingerprint()
        job = self._jobs.get(snapshot.snapshot_id)
        if job is not None and job.fingerprint != fingerprint:
            raise ScenarioError("snapshot_id_conflict")
        cached = job is not None and job.task.done()
        shared = job is not None and not cached
        if job is None:
            if len(self._jobs) + len(self._retiring) >= MAX_JOBS:
                completed = next((identity for identity, item in self._jobs.items() if item.task.done()), None)
                if completed is None:
                    raise ScenarioError("busy")
                self._jobs.pop(completed)
            job = _Job(snapshot, fingerprint, asyncio.create_task(self._analyze(snapshot)))
            self._jobs[snapshot.snapshot_id] = job
        self._jobs.move_to_end(snapshot.snapshot_id)
        job.waiters += 1
        try:
            result = await asyncio.shield(job.task)
            result.analysis.validate_for(snapshot, self._clock_ms())
            return result.model_copy(deep=True, update={"cache_hit": cached, "deduplicated": shared})
        finally:
            job.waiters -= 1
            if job.waiters == 0 and not job.task.done():
                if self._jobs.get(snapshot.snapshot_id) is job:
                    self._jobs.pop(snapshot.snapshot_id)
                self._retiring.add(job.task)
                job.task.add_done_callback(self._retiring.discard)
                if not job.task.cancelling():
                    job.task.cancel()
                await asyncio.shield(asyncio.gather(job.task, return_exceptions=True))
            if job.task.done() and (job.task.cancelled() or job.task.exception() is not None):
                if self._jobs.get(snapshot.snapshot_id) is job:
                    self._jobs.pop(snapshot.snapshot_id)

    async def _analyze(self, snapshot: ScenarioSnapshot) -> ScenarioAnalysisResult:
        started = time.perf_counter_ns()
        effective = None
        outcome = "cancelled"
        tokens = None
        remaining_s = (snapshot.valid_until_ms - self._clock_ms()) / 1000
        if remaining_s <= 0:
            raise ScenarioError("snapshot_expired")
        try:
            async with asyncio.timeout(min(self.timeout_s, remaining_s)):
                schema = ScenarioOutput.model_json_schema()
                for name in ("snapshot_id", "symbol", "timeframe", "as_of_close_time_ms", "valid_until_ms", "data_source"):
                    schema["properties"][name]["const"] = getattr(snapshot, name)
                for definition, fields in (("EvidenceCitation", ("evidence_id",)),
                                          ("Comparison", ("left_ref", "right_ref")),
                                          ("FutureCondition", ("metric_ref", "threshold_ref"))):
                    for name in fields:
                        schema["$defs"][definition]["properties"][name]["enum"] = list(snapshot.evidence)
                messages = [OllamaMessage(role="system", content=(
                    "Eres Qwen, asesor de escenarios condicionales Binance SPOT, nunca una estrategia. "
                    "No tienes autoridad para comprar, vender, fijar tamaños, stops, riesgo ni ejecución. "
                    "No short, margin, futuros, préstamos o apalancamiento. Responde solo el JSON Schema. "
                    "No prosa, cifras nuevas, summary, interpretation ni instrucciones operativas. "
                    "Copia metadatos y valores exactos; cita TODOS los operandos utilizados como evidencia. "
                    "Las observations deben ser comparaciones VERDADERAS de este snapshot. "
                    "Conditions se refieren a métricas de un FUTURO snapshot CERRADO, comparadas con "
                    "umbrales referenciados del snapshot de entrada; no afirman sucesos observados. "
                    "Conditions: conjunción all. Invalidations: disyunción any con exactamente una "
                    "complementaria por condición, mismos refs: gt/lte, gte/lt, lt/gte, lte/gt, eq/ne, ne/eq. "
                    "No propongas condiciones imposibles ni inventes fórmulas del método. "
                    "Incluye continuación, fallo y lateralización; conserva todos los missing_data. "
                    "El origen y estado declarados por el cliente no certifican mercado real. executable=false."
                )), OllamaMessage(role="user", content=snapshot.model_dump_json())]
                response = await self.chat(messages, response_schema=schema, snapshot=snapshot)
                effective = response.model
                if len(response.message.content.encode()) > 32_768:
                    raise ScenarioError("invalid_structure")
                try:
                    body = _strict_json(response.message.content)
                except (ValueError, TypeError, UnicodeError, RecursionError):
                    raise ScenarioError("invalid_json") from None
                try:
                    analysis = ScenarioOutput.model_validate(body)
                except ValidationError:
                    raise ScenarioError("invalid_structure") from None
                analysis.validate_for(snapshot, self._clock_ms())
                rate = (response.eval_count * 1e9 / response.eval_duration
                        if response.eval_count is not None and response.eval_duration else None)
                duration_ms = (time.perf_counter_ns() - started) // 1_000_000
                result = ScenarioAnalysisResult(
                    analysis=analysis, model_used=response.model, duration_ms=duration_ms,
                    total_duration_ms=response.total_duration / 1e6 if response.total_duration is not None else None,
                    load_duration_ms=response.load_duration / 1e6 if response.load_duration is not None else None,
                    prompt_tokens=response.prompt_eval_count, generated_tokens=response.eval_count,
                    generation_tokens_per_second=rate,
                    data_source="TEST_ONLY" if snapshot.data_source == "TEST_ONLY" else "unverified",
                    summaries={"continuation": "Posible continuación, condicionada a futuras observaciones cerradas.",
                               "failure": "Posible fallo, condicionado a futuras observaciones cerradas.",
                               "sideways": "Posible lateralización, condicionada a futuras observaciones cerradas."},
                )
                outcome, tokens = "ok", response.eval_count
                return result
        except TimeoutError:
            outcome = "snapshot_expired" if remaining_s <= self.timeout_s else "timeout"
            raise ScenarioError(outcome,
                                duration_ms=(time.perf_counter_ns() - started) // 1_000_000,
                                effective_model=effective) from None
        except ScenarioError as error:
            error.duration_ms = (time.perf_counter_ns() - started) // 1_000_000
            error.effective_model = error.effective_model or effective
            effective, outcome = error.effective_model, error.code
            raise
        finally:
            logger.info("qwen_scenarios snapshot=%s requested_model=%s model=%s duration_ms=%s tokens=%s outcome=%s",
                        snapshot.snapshot_id, QWEN_MODEL, effective,
                        (time.perf_counter_ns() - started) // 1_000_000, tokens, outcome)

    async def analyze_market(self, analysis_result) -> ScenarioAnalysisResult:
        """Compatibilidad de nombre, no de diccionarios libres ni salida textual."""
        return await self.analyze_snapshot(analysis_result)

    async def generate_strategy(self, market_context: str) -> ScenarioAnalysisResult:
        """Alias deprecated: solo contexto identificado, devuelve escenarios no ejecutables."""
        try:
            if not isinstance(market_context, str) or len(market_context) > 32_768:
                raise ValueError("invalid context size")
            body = _strict_json(market_context)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ScenarioError("invalid_snapshot") from None
        return await self.analyze_snapshot(body)

    async def health_check(self) -> bool:
        try:
            httpx = _httpx_module()
        except ScenarioError:
            return False
        try:
            async with asyncio.timeout(min(5.0, self.timeout_s)):
                await self._require_model()
            return True
        except (ScenarioError, httpx.HTTPError, TimeoutError):
            return False

    async def close(self):
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._finish_close())
        await asyncio.shield(self._close_task)

    async def _finish_close(self):
        jobs = list(self._jobs.values())
        self._jobs.clear()
        tasks = {job.task for job in jobs} | self._retiring | self._active_chats
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            if self._http is not None and self._owns_http:
                await self._http.aclose()
                self._http = None
