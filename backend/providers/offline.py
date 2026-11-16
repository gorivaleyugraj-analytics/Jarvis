"""Deterministic offline provider.

Keeps JARVIS X useful with zero API keys and zero network (spec §23.6
"graceful degradation"). It recognises a handful of intents and always answers
honestly about being offline — it never fabricates research or claims a task
succeeded.
"""
from __future__ import annotations

import re

from .base import ChatMessage, ModelProvider, ProviderReply

_HELP_TEXT = """I'm running in **offline mode** — no model provider is configured, so I answer
from built-in rules only. What still works right now:

- **Missions** — give me a goal like "organize my downloads folder", "write a
  note about X", "remind me to Y" or "research Z" and I will plan it into
  steps, execute the safe ones and ask approval for sensitive ones.
- **Tools** — file operations inside my sandboxed workspace, notes, reminders
  and device status.
- **Memory** — store and retrieve facts you approve.

To enable full AI replies, set `JARVIS_OPENAI_API_KEY` (and optionally
`JARVIS_OPENAI_BASE_URL` / `JARVIS_OPENAI_MODEL`) in `.env`, then restart."""

_GREETING = re.compile(r"\b(hi|hello|hey|jarvis|good (morning|evening|afternoon))\b", re.I)
_HELP = re.compile(r"\b(help|what can you do|capabilities|who are you)\b", re.I)
_STATUS = re.compile(r"\b(status|health|are you (there|up|online))\b", re.I)
_MISSION = re.compile(
    r"\b(research|organize|report|remind|schedule|plan|build|create|clean up|compare)\b", re.I
)


def _last_user_text(messages: list[ChatMessage]) -> str:
    for msg in reversed(messages):
        if msg.role == "user":
            return msg.content.strip()
    return ""


class OfflineProvider(ModelProvider):
    name = "offline"
    model = "rule-based-v1"

    def available(self) -> bool:
        return True

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.4,
        max_tokens: int = 1024,
    ) -> ProviderReply:
        text = _last_user_text(messages)
        reply = self._respond(text)
        return ProviderReply(
            text=reply,
            provider=self.name,
            model=self.model,
            prompt_tokens=max(1, sum(len(m.content.split()) for m in messages)),
            completion_tokens=len(reply.split()),
        )

    def _respond(self, text: str) -> str:
        if not text:
            return "I didn't catch any input. Try `help` to see what I can do offline."
        if _HELP.search(text):
            return _HELP_TEXT
        if _STATUS.search(text) and len(text.split()) <= 6:
            return "All local subsystems are up. I'm in offline mode — tools, missions and memory work; cloud AI replies need a configured provider."
        if _GREETING.search(text) and len(text.split()) <= 6:
            return "Online and ready, in offline mode. Say `help` for capabilities, or give me a goal and I'll plan it."
        if _MISSION.search(text):
            return (
                f"I can turn that into a mission: “{text}”.\n\n"
                "Open the **Missions** panel (or POST /api/v1/missions with this goal) and I'll "
                "decompose it into steps, run the low-risk ones autonomously, and hold any "
                "sensitive action for your approval before executing it."
            )
        # Generic fallback: acknowledge, stay honest, point at what works.
        snippet = text if len(text) <= 200 else text[:200] + "…"
        return (
            f"You said: “{snippet}”.\n\n"
            "I'm the offline rule-based provider, so I can't reason freely about that yet — "
            "but I can still **plan and execute** it as a mission with the local tools, or you "
            "can configure a model provider for full conversational answers.\n\n" + _HELP_TEXT
        )
