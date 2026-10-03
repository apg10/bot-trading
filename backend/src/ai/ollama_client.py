# Cliente HTTP para Ollama — análisis de mercado con IA local.

from __future__ import annotations

import json
from typing import Optional
from pydantic import BaseModel, Field


class OllamaMessage(BaseModel):
    """Mensaje para el modelo de IA."""
    role: str = Field(..., description="Role del mensaje (user/assistant/system)")
    content: str = Field(..., description="Contenido del mensaje")


class OllamaResponse(BaseModel):
    """Respuesta del modelo Ollama."""
    model: str = Field(..., description="Modelo utilizado")
    created_at: str = Field(..., description="Timestamp de creación")
    message: OllamaMessage = Field(..., description="Mensaje de respuesta")
    done: bool = Field(..., description="Si la generación está completa")
    total_duration: Optional[int] = Field(None, description="Duración total en nanosegundos")
    load_duration: Optional[int] = Field(None, description="Duración de carga en nanosegundos")
    prompt_eval_count: Optional[int] = Field(None, description="Número de tokens evaluados")
    eval_count: Optional[int] = Field(None, description="Número de tokens generados")


class OllamaClient:
    """Cliente HTTP para interactuar con Ollama."""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._http = None  # Inicializado lazy con httpx

    def _get_http(self):
        """Obtiene o crea el cliente HTTP."""
        if self._http is None:
            import httpx
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    async def chat(self, messages: list[OllamaMessage]) -> OllamaResponse:
        """Envía un mensaje al modelo y retorna la respuesta.

        Args:
            messages: Lista de mensajes para el chat

        Returns:
            OllamaResponse con la respuesta del modelo
        """
        http = self._get_http()

        payload = {
            "model": self.model,
            "messages": [msg.model_dump() for msg in messages],
            "stream": False,
        }

        response = await http.post(
            f"{self.base_url}/api/chat",
            json=payload,
        )

        if response.status_code != 200:
            raise Exception(f"Ollama API error: {response.status_code} - {response.text}")

        data = response.json()
        return OllamaResponse(**data)

    async def analyze_market(self, analysis_result: dict) -> str:
        """Analiza los resultados del análisis técnico con IA.

        Args:
            analysis_result: Diccionario con datos de análisis técnico

        Returns:
            Análisis en texto natural del mercado
        """
        system_prompt = """Eres un analista financiero experto especializado en trading de criptomonedas.
Tu tarea es analizar los datos técnicos proporcionados y dar recomendaciones claras de trading.
Responde en español, de forma concisa pero completa."""

        user_prompt = f"""Analiza el siguiente mercado:

{json.dumps(analysis_result, indent=2, ensure_ascii=False)}

Proporciona:
1. Análisis técnico general (tendencia, momentum, volatilidad)
2. Señales de entrada/salida recomendadas
3. Niveles clave de soporte y resistencia
4. Gestión de riesgo sugerida
5. Conclusión con acción recomendada (comprar/vender/esperar)"""

        messages = [
            OllamaMessage(role="system", content=system_prompt),
            OllamaMessage(role="user", content=user_prompt),
        ]

        response = await self.chat(messages)
        return response.message.content

    async def generate_strategy(self, market_context: str) -> str:
        """Genera una estrategia de trading basada en el contexto del mercado.

        Args:
            market_context: Descripción del contexto del mercado

        Returns:
            Estrategia generada por IA
        """
        system_prompt = """Eres un trader profesional con experiencia en mercados cripto.
Tu tarea es crear estrategias de trading basadas en el análisis técnico y el contexto del mercado."""

        user_prompt = f"""Dado el siguiente contexto del mercado:

{market_context}

Crea una estrategia de trading que incluya:
1. Tipo de posición (long/short)
2. Entradas sugeridas con niveles específicos
3. Stop loss y take profit
4. Tamaño de posición recomendado
5. Gestión de riesgo y criterios de salida"""

        messages = [
            OllamaMessage(role="system", content=system_prompt),
            OllamaMessage(role="user", content=user_prompt),
        ]

        response = await self.chat(messages)
        return response.message.content

    async def health_check(self) -> bool:
        """Verifica si Ollama está disponible."""
        try:
            http = self._get_http()
            response = await http.get(f"{self.base_url}/api/tags")
            return response.status_code == 200
        except Exception:
            return False

    async def close(self):
        """Cierra el cliente HTTP."""
        if self._http:
            await self._http.aclose()
            self._http = None
