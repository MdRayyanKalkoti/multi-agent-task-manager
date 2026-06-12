"""Shared base class for all agents."""
from abc import ABC

from app.logger import get_logger
from app.services.gemini_client import GeminiClient, gemini_client


class BaseAgent(ABC):
    """Common plumbing: a name, a logger and a handle to the LLM client."""

    name: str = "base"

    def __init__(self, llm: GeminiClient | None = None) -> None:
        self.llm = llm or gemini_client
        self.logger = get_logger(f"agents.{self.name}")
