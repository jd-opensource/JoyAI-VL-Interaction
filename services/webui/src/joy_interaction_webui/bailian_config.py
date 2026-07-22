"""Configuration helpers for the Bailian OpenAI-compatible vision endpoint."""

from __future__ import annotations

import os
from pathlib import Path


BAILIAN_API_BASE = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
BAILIAN_MODEL = "qwen3.7-plus"
BAILIAN_MODELS = (
    "qwen3.7-plus",
    "qwen3.6-plus",
    "qwen3.6-flash",
)
API_KEY_FILE = Path(r"D:\Study\job\2026\PKU\JoyAI\API.txt")

BAILIAN_PROVIDER = "bailian"
LOCAL_PROVIDER = "local"
SUPPORTED_PROVIDERS = frozenset({BAILIAN_PROVIDER, LOCAL_PROVIDER})


class BailianConfigurationError(RuntimeError):
    """Raised when the Bailian credential configuration is unavailable."""


def resolve_provider(value: str | None = None) -> str:
    """Return a supported provider, defaulting to Bailian."""
    provider = (value if value is not None else os.environ.get("VLM_PROVIDER", BAILIAN_PROVIDER))
    provider = provider.strip().lower()
    if not provider:
        provider = BAILIAN_PROVIDER
    if provider not in SUPPORTED_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise ValueError(f"Unsupported VLM provider {provider!r}; expected one of: {supported}.")
    return provider


def load_bailian_api_key(
    *,
    key_file: Path = API_KEY_FILE,
    environ: dict[str, str] | None = None,
) -> str:
    """Load an API key without transforming or exposing its value.

    A non-empty ``OPENAI_API_KEY`` takes precedence. Otherwise the first line
    beginning with ``sk-`` after surrounding whitespace is returned verbatim
    (except for that surrounding whitespace).
    """
    env = os.environ if environ is None else environ
    environment_key = env.get("OPENAI_API_KEY", "").strip()
    if environment_key:
        return environment_key

    try:
        lines = key_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise BailianConfigurationError(
            "Bailian API key file was not found. Set OPENAI_API_KEY or create the configured local key file."
        ) from exc
    except OSError as exc:
        raise BailianConfigurationError(
            "Bailian API key file could not be read. Check the configured local key file permissions."
        ) from exc

    for line in lines:
        candidate = line.strip()
        if candidate.startswith("sk-"):
            return candidate

    raise BailianConfigurationError(
        "Bailian API key file does not contain a valid key line starting with 'sk-'."
    )
