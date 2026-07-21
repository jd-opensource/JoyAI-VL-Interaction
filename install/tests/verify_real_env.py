from __future__ import annotations

import importlib
import importlib.metadata
import shutil
import sys


BASE_MODULES = [
    "aiohttp",
    "av",
    "cv2",
    "httpx",
    "joy_interaction_webui.server",
    "livekit.api",
    "livekit.rtc",
    "numpy",
    "openai",
    "PIL",
    "psutil",
    "vllm",
]

OPTIONAL_MODULES = {
    "no_with": [],
    "with_asr": ["fastapi", "uvicorn", "websockets", "asr_adapter"],
    "with_tts": ["fastapi", "uvicorn", "websockets", "tts_adapter"],
    "with_background_agent": ["fastapi", "uvicorn", "pydantic", "codex_api.main"],
    "with_all": [
        "fastapi",
        "uvicorn",
        "websockets",
        "pydantic",
        "asr_adapter",
        "tts_adapter",
        "codex_api.main",
    ],
}

OPTIONAL_COMMANDS = {
    "no_with": [],
    "with_asr": ["joyvl-asr-adapter"],
    "with_tts": ["joyvl-tts-adapter"],
    "with_background_agent": ["streamingharness-codex-api"],
    "with_all": [
        "joyvl-asr-adapter",
        "joyvl-tts-adapter",
        "streamingharness-codex-api",
    ],
}


def import_module(name: str) -> None:
    importlib.import_module(name)
    print(f"import ok: {name}")


def version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in version.split("."):
        digits = []
        for char in part:
            if not char.isdigit():
                break
            digits.append(char)
        if not digits:
            break
        parts.append(int("".join(digits)))
    return tuple(parts)


def distribution_version(name: str) -> str:
    version = importlib.metadata.version(name)
    print(f"{name} version: {version}")
    return version


def verify_vllm_web_stack() -> None:
    instrumentator = distribution_version("prometheus-fastapi-instrumentator")
    starlette = distribution_version("starlette")
    fastapi = distribution_version("fastapi")

    if version_tuple(instrumentator) >= (8,):
        raise RuntimeError(
            "prometheus-fastapi-instrumentator>=8 is not allowed with vllm 0.22.0; "
            "it can fail inside vLLM metrics middleware on OpenAI API requests"
        )
    if version_tuple(starlette) >= (1,):
        raise RuntimeError(
            "starlette>=1 is not allowed in this vllm 0.22.0 environment; "
            "rerun install/install.sh with the default constraints file"
        )
    if version_tuple(fastapi) < (0, 115):
        raise RuntimeError(f"expected fastapi>=0.115, got {fastapi!r}")
    if version_tuple(fastapi) >= (0, 137):
        raise RuntimeError(
            "fastapi>=0.137 is not allowed with vllm 0.22.0; FastAPI 0.137 "
            "can leave _IncludedRouter entries that break vLLM metrics routing"
        )

    from fastapi import APIRouter, FastAPI
    from prometheus_fastapi_instrumentator import Instrumentator

    app = FastAPI()
    router = APIRouter()

    @router.post("/chat/completions")
    def _probe_chat_completions() -> dict[str, bool]:
        return {"ok": True}

    app.include_router(router, prefix="/v1")
    Instrumentator().instrument(app)

    route_types = [type(route).__name__ for route in app.routes]
    if "_IncludedRouter" in route_types:
        raise RuntimeError(
            "FastAPI included router route shape is incompatible with "
            "vllm 0.22.0 metrics middleware"
        )


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_real_env.py <case-name>", file=sys.stderr)
        return 2

    case = sys.argv[1]
    if case not in OPTIONAL_MODULES:
        print(f"unknown case: {case}", file=sys.stderr)
        return 2

    for module in BASE_MODULES + OPTIONAL_MODULES[case]:
        import_module(module)

    import vllm

    version = getattr(vllm, "__version__", "")
    print(f"vllm version: {version}")
    if version != "0.22.0":
        raise RuntimeError(f"expected vllm 0.22.0, got {version!r}")
    verify_vllm_web_stack()

    for command in ["joy-interaction-webui", "joy-interaction-webui-stop", *OPTIONAL_COMMANDS[case]]:
        resolved = shutil.which(command)
        print(f"command {command}: {resolved}")
        if not resolved:
            raise RuntimeError(f"missing command: {command}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
