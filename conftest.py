"""Shared pytest configuration.

Forces the offline template path so the test suite is fast and deterministic
and does not depend on the network LLM gateway. The real CLI still uses the
LLM when run normally.
"""
import os

os.environ.setdefault("CHIPAGENT_DISABLE_LLM", "1")
