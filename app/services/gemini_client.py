"""Thin async wrapper around the Gemini 2.5 Flash API (google-genai SDK).

Every agent talks to the LLM through this single client so that error
handling, timeouts and graceful degradation live in one place. If no
GEMINI_API_KEY is configured the client reports itself unavailable and
agents fall back to deterministic heuristics.
"""
import asyncio
import json
import re
from typing import Any, Optional

from app.config import get_settings
from app.logger import get_logger

logger = get_logger("services.gemini")

try:
    from google import genai
    from google.genai import types as genai_types

    _SDK_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SDK_AVAILABLE = False
    logger.warning("google-genai SDK not installed; LLM features disabled.")


class GeminiClient:
    """Lazy, fault-tolerant Gemini client."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client: Optional["genai.Client"] = None

    @property
    def available(self) -> bool:
        return _SDK_AVAILABLE and bool(self._settings.gemini_api_key)

    def _get_client(self) -> "genai.Client":
        if self._client is None:
            self._client = genai.Client(api_key=self._settings.gemini_api_key)
        return self._client

    async def generate(self, prompt: str, system: Optional[str] = None, temperature: float = 0.4) -> Optional[str]:
        """Return model text, or None on any failure (callers must handle None)."""
        if not self.available:
            return None
        try:
            client = self._get_client()
            config = genai_types.GenerateContentConfig(
                temperature=temperature,
                system_instruction=system,
            )
            # The SDK call is synchronous; keep the event loop responsive.
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=self._settings.gemini_model,
                contents=prompt,
                config=config,
            )
            return (response.text or "").strip() or None
        except Exception as exc:  # network, quota, safety blocks, ...
            logger.error("Gemini call failed: %s", exc)
            return None

    async def generate_json(self, prompt: str, system: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Ask for JSON, strip code fences, parse defensively."""
        raw = await self.generate(prompt, system=system, temperature=0.1)
        if not raw:
            return None
        cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        try:
            parsed = json.loads(cleaned)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            logger.warning("Gemini returned non-JSON payload: %.200s", raw)
            return None


gemini_client = GeminiClient()
