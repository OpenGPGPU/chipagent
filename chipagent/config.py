"""Configuration for ChipAgent phase 1.

Reads LLM endpoint settings from the environment. The deployment exposes an
Anthropic-protocol gateway via ``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_AUTH_TOKEN``
(the same endpoint Claude Code itself uses), so that is preferred. An
OpenAI-compatible endpoint (``LLM_API_*``) is also supported. When nothing is
configured the prototype runs fully offline against the template fallback.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class LLMConfig:
    """Connection details for a chat-completions endpoint."""

    api_key: Optional[str]
    base_url: Optional[str]
    model: Optional[str]
    style: str  # "anthropic" | "openai" | "none"
    enabled: bool
    version: str = "2023-06-01"
    custom_headers: str = ""

    @classmethod
    def from_env(cls) -> "LLMConfig":
        # Test/deterministic mode: force the offline template path.
        if os.environ.get("CHIPAGENT_DISABLE_LLM", "0") == "1":
            return cls(api_key=None, base_url=None, model=None, style="none", enabled=False)

        # Preferred: Anthropic-protocol gateway (used by Claude Code itself).
        anth_key = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
        anth_base = os.environ.get("ANTHROPIC_BASE_URL")
        anth_model = os.environ.get("ANTHROPIC_MODEL") or os.environ.get("LLM_API_MODEL")
        if anth_key and anth_base and anth_model:
            return cls(
                api_key=anth_key,
                base_url=anth_base,
                model=anth_model,
                style="anthropic",
                enabled=True,
                version=os.environ.get("ANTHROPIC_VERSION", "2023-06-01"),
                custom_headers=os.environ.get("ANTHROPIC_CUSTOM_HEADERS", ""),
            )

        # Fallback: OpenAI-compatible endpoint.
        key = os.environ.get("LLM_API_KEY")
        base = os.environ.get("LLM_API_URL")
        model = os.environ.get("LLM_API_MODEL")
        style = (os.environ.get("LLM_API_STYLE") or "openai").lower()
        enabled = bool(key and base and model)
        return cls(api_key=key, base_url=base, model=model, style=style, enabled=enabled)


@dataclass
class Settings:
    """Top-level settings assembled from environment defaults."""

    llm: LLMConfig
    default_output_dir: str = "generated"
    default_log_dir: str = "logs"
    use_git: bool = False  # write artifacts only by default; do not touch main branch
    repo_root: Optional[str] = None

    @classmethod
    def load(cls) -> "Settings":
        use_git = os.environ.get("CHIPAGENT_USE_GIT", "0") == "1"
        repo_root = os.environ.get("CHIPAGENT_REPO_ROOT")
        return cls(
            llm=LLMConfig.from_env(),
            default_output_dir=os.environ.get("CHIPAGENT_OUTPUT_DIR", "generated"),
            default_log_dir=os.environ.get("CHIPAGENT_LOG_DIR", "logs"),
            use_git=use_git,
            repo_root=repo_root,
        )
