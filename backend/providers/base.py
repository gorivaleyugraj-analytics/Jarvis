"""Provider-neutral model adapter interface (spec §7.1.5, §12).

Every backend — local llama.cpp, an OpenAI-compatible cloud, a stub — speaks
this interface, so the orchestrator never depends on a vendor.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ChatMessage:
    role: str  # system | user | assistant
    content: str

    def as_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


@dataclass
class ProviderReply:
    text: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    degraded: bool = False  # True when a fallback provider answered instead
    notice: str = ""


class ProviderError(Exception):
    """Raised when a provider cannot complete a request."""


class ModelProvider(ABC):
    name: str = "base"
    model: str = "unknown"

    @abstractmethod
    def available(self) -> bool:
        """Whether this provider can currently serve requests."""

    @abstractmethod
    def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.4,
        max_tokens: int = 1024,
    ) -> ProviderReply:
        """Return one assistant reply for the given conversation."""
