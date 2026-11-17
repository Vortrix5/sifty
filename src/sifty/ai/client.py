"""Thin wrapper around a local Ollama server.

Everything degrades gracefully: if Ollama isn't running, :func:`is_available`
returns ``False`` and callers fall back to non-AI behaviour instead of crashing.

Chat is **streamed**. A local model has to be loaded into RAM/VRAM on its first
request (a cold start that can take many seconds) and then emits tokens slowly.
A single blocking request with one wall-clock budget covers *all* of that at
once, so a model that is working - just slow - trips the timeout and the user
gets nothing. Streaming turns the timeout into "time between tokens" instead of
"time for the whole answer", so a slow-but-alive model keeps going and callers
can show progress as it arrives.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass

import httpx

from ..infra.config import load_config


class OllamaUnavailable(Exception):
    """Raised when the local Ollama server can't be reached."""


@dataclass
class OllamaClient:
    host: str
    model: str
    timeout: float
    keep_alive: str = "10m"

    @classmethod
    def from_config(cls, config=None) -> OllamaClient:
        config = config or load_config()
        ai = config.section("ai")
        return cls(
            host=ai.get("host", "http://localhost:11434"),
            model=ai.get("model", "qwen2.5:3b"),
            timeout=float(ai.get("timeout_seconds", 60)),
            keep_alive=str(ai.get("keep_alive", "10m")),
        )

    def _timeout(self) -> httpx.Timeout:
        """Split timeout: connecting is instant; generation is the slow part.

        ``timeout`` bounds the wait *between* streamed chunks (and the initial
        model load), not the total length of the answer.
        """
        return httpx.Timeout(self.timeout, connect=5.0, write=10.0, pool=5.0)

    def is_available(self) -> bool:
        """Return True if the Ollama server responds to a tags request."""
        try:
            resp = httpx.get(f"{self.host}/api/tags", timeout=3.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def list_models(self) -> list[str]:
        """Return the names of locally-pulled Ollama models (empty on error)."""
        try:
            resp = httpx.get(f"{self.host}/api/tags", timeout=3.0)
            if resp.status_code != 200:
                return []
            return [m.get("name", "") for m in resp.json().get("models", [])]
        except httpx.HTTPError:
            return []

    def _payload(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        payload: dict = {
            "model": self.model,
            "stream": True,
            "keep_alive": self.keep_alive,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
        return payload

    def _build_messages(self, system: str, user: str) -> list[dict]:
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def chat_stream(
        self,
        system: str,
        user: str,
        *,
        messages: list[dict] | None = None,
    ) -> Iterator[str]:
        """Stream the assistant's reply token-by-token.

        Pass ``messages`` (a full Ollama-format list) to continue a conversation;
        omit it for a single-turn exchange built from ``system`` + ``user``.
        Yields content chunks as they arrive. Raises :class:`OllamaUnavailable`
        with a human-readable message on transport errors or timeouts.
        """
        payload_messages = messages if messages is not None else self._build_messages(system, user)
        try:
            with httpx.stream(
                "POST",
                f"{self.host}/api/chat",
                json=self._payload(payload_messages),
                timeout=self._timeout(),
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    chunk = data.get("message", {}).get("content", "")
                    if chunk:
                        yield chunk
                    if data.get("done"):
                        break
        except httpx.TimeoutException as exc:
            raise OllamaUnavailable(
                "timed out - the model may still be loading; try again"
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(str(exc)) from exc

    def chat(
        self,
        system: str,
        user: str,
        *,
        messages: list[dict] | None = None,
    ) -> str:
        """Send a chat and return the full assistant text.

        Pass ``messages`` to continue a multi-turn conversation; omit for a
        single-turn exchange. Consumes :meth:`chat_stream` internally.
        """
        return "".join(self.chat_stream(system, user, messages=messages)).strip()

    def chat_once(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> dict:
        """Non-streaming POST to /api/chat; returns the full assistant message dict.

        Use for agentic steps where you need to inspect ``tool_calls`` before
        deciding whether to dispatch or yield a final answer.  The returned dict
        is shaped like ``{"role": "assistant", "content": "...", "tool_calls": [...]}``;
        ``tool_calls`` is absent (or empty) when the model produces a plain reply.
        Raises :class:`OllamaUnavailable` on network or HTTP errors.
        """
        payload = self._payload(messages, tools)
        payload["stream"] = False
        try:
            resp = httpx.post(
                f"{self.host}/api/chat",
                json=payload,
                timeout=self._timeout(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("message", {"role": "assistant", "content": ""})
        except httpx.TimeoutException as exc:
            raise OllamaUnavailable(
                "timed out - the model may still be loading; try again"
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(str(exc)) from exc
