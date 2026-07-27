"""LLM client abstraction.

The phase 1 plan calls for a "lightweight" integration with a mature agent
(Claude / Codex) as an execution engine, accessed via API (Section 3.3).
This module wraps the deployment's Anthropic-protocol gateway (the same
endpoint Claude Code uses) and degrades gracefully to a deterministic offline
fallback when no endpoint is configured or a call fails, so the prototype
always produces a runnable result.

Only one method is exposed: :meth:`LLMClient.chat`, which returns the
assistant text. Callers are responsible for prompt construction and parsing.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, Iterator, List, Optional

from .config import LLMConfig


class LLMError(RuntimeError):
    """Raised when the LLM endpoint cannot be reached and no fallback applies."""


class LLMClient:
    """Thin wrapper over an Anthropic- or OpenAI-compatible endpoint."""

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._openai_client = None
        if config.enabled and config.style == "openai":
            try:  # imported lazily so the package works without the SDK installed
                from openai import OpenAI

                self._openai_client = OpenAI(api_key=config.api_key, base_url=config.base_url)
            except Exception:
                self._openai_client = None

    @property
    def available(self) -> bool:
        if not self._config.enabled:
            return False
        return self._config.style == "anthropic" or self._openai_client is not None

    @property
    def model(self) -> Optional[str]:
        return self._config.model

    def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> str:
        """Return assistant text for the given prompt pair.

        Raises :class:`LLMError` if the endpoint is unavailable and the caller
        did not supply a fallback.
        """
        if not self._config.enabled:
            raise LLMError("LLM endpoint not configured (running in offline mode)")
        if self._config.style == "anthropic":
            return self._chat_anthropic(system, user, temperature, max_tokens)
        if self._config.style == "openai" and self._openai_client is not None:
            return self._chat_openai(system, user, temperature, max_tokens)
        raise LLMError("LLM endpoint not configured (running in offline mode)")

    def stream(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> Iterator[str]:
        """Yield assistant text deltas as they arrive (Anthropic SSE stream).

        Used by the conversational CLI for a Claude-style typing effect. When
        the endpoint is offline or non-Anthropic, yields a single fallback
        chunk so callers can render *something* instead of erroring. Raises
        :class:`LLMError` only if the gateway is reachable but the HTTP call
        itself fails mid-stream.
        """
        if not self._config.enabled:
            yield ""  # offline → caller renders its own deterministic reply
            return
        if self._config.style == "anthropic":
            yield from self._stream_anthropic(system, user, temperature, max_tokens)
            return
        # No streaming path for OpenAI-style here; fall back to the full chat.
        try:
            yield self._chat_openai(system, user, temperature, max_tokens)
        except LLMError:
            yield ""

    # ------------------------------------------------------------------
    # Anthropic Messages API (preferred path)
    # ------------------------------------------------------------------
    def _chat_anthropic(self, system: str, user: str, temperature: float, max_tokens: int) -> str:
        url = self._config.base_url.rstrip("/") + "/v1/messages"
        body: Dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": user}],
            # The served reasoning model (glm-5.2) can spend the entire token
            # budget in a thinking block and never emit a final answer. Disable
            # extended thinking so the response lands in a text block.
            "thinking": {"type": "disabled"},
        }
        if system:
            body["system"] = system
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._config.api_key}",
            "anthropic-version": self._config.version,
        }
        # Inject any custom headers the gateway requires (e.g. x-project).
        for pair in self._config.custom_headers.split(","):
            if ":" in pair:
                k, v = pair.split(":", 1)
                headers[k.strip()] = v.strip()

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise LLMError(f"LLM call failed: HTTP {exc.code}: {exc.read().decode()[:300]}") from exc
        except Exception as exc:  # pragma: no cover - network path
            raise LLMError(f"LLM call failed: {exc}") from exc

        return self._extract_anthropic_text(payload)

    def _stream_anthropic(self, system, user, temperature, max_tokens) -> Iterator[str]:
        """Yield text deltas from the Anthropic streaming Messages API."""
        url = self._config.base_url.rstrip("/") + "/v1/messages"
        body: Dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "messages": [{"role": "user", "content": user}],
            "thinking": {"type": "disabled"},
        }
        if system:
            body["system"] = system
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {self._config.api_key}",
            "anthropic-version": self._config.version,
        }
        for pair in self._config.custom_headers.split(","):
            if ":" in pair:
                k, v = pair.split(":", 1)
                headers[k.strip()] = v.strip()

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as exc:
            raise LLMError(f"LLM stream failed: HTTP {exc.code}: {exc.read().decode()[:300]}") from exc
        except Exception as exc:  # pragma: no cover - network path
            raise LLMError(f"LLM stream failed: {exc}") from exc

        # Parse SSE: lines of "event: X" / "data: {json}". Yield text deltas.
        with resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="ignore").rstrip("\n")
                if not line.startswith("data:"):
                    continue
                payload_str = line[len("data:"):].strip()
                if not payload_str or payload_str == "[DONE]":
                    continue
                try:
                    evt = json.loads(payload_str)
                except json.JSONDecodeError:
                    continue
                if evt.get("type") == "content_block_delta":
                    delta = evt.get("delta", {}) or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        yield delta["text"]

    @staticmethod
    def _extract_anthropic_text(payload: Dict[str, Any]) -> str:
        """Concatenate text content blocks from a Messages API response.

        Some reasoning models emit their answer inside a ``thinking`` block and
        leave the ``text`` block empty (or run out of tokens first). When no
        text block is present, fall back to thinking-block content so the
        caller still gets something to parse a code fence out of.
        """
        text_parts: List[str] = []
        thinking_parts: List[str] = []
        for block in payload.get("content", []) or []:
            if block.get("type") == "text" and block.get("text"):
                text_parts.append(block["text"])
            elif block.get("type") == "thinking" and block.get("thinking"):
                thinking_parts.append(block["thinking"])
        if text_parts:
            return "\n".join(text_parts)
        return "\n".join(thinking_parts)

    # ------------------------------------------------------------------
    # OpenAI-compatible path (fallback; the deployment gateway does not serve it)
    # ------------------------------------------------------------------
    def _chat_openai(self, system: str, user: str, temperature: float, max_tokens: int) -> str:
        # Some OpenAI-compatible proxies only accept user/assistant roles, so
        # fold the system prompt into the user message.
        combined_user = f"{system}\n\n---\n\n{user}" if system else user
        try:
            resp = self._openai_client.chat.completions.create(
                model=self._config.model,
                messages=[{"role": "user", "content": combined_user}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content or ""
        except Exception as exc:  # pragma: no cover - network path
            raise LLMError(f"LLM call failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Helpers for prompt engineering used by the parser and skills
    # ------------------------------------------------------------------
    @staticmethod
    def extract_json(text: str) -> Dict[str, Any]:
        """Best-effort extraction of the first JSON object from model output."""
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def extract_code(text: str) -> str:
        """Extract Verilog/SystemVerilog code from a fenced or raw model reply."""
        fence = re.search(r"```(?:systemverilog|verilog|sv|v)?\s*\n([\s\S]*?)```", text)
        if fence:
            return fence.group(1).strip() + "\n"
        return text.strip() + "\n"
