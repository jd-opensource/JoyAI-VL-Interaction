#!/usr/bin/env python3
"""Smoke warmup helpers for OpenAI-compatible vLLM services."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


WHITE_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def chat_completions_url(api_base: str) -> str:
    return api_base.rstrip("/") + "/chat/completions"


def post_json(url: str, payload: dict, api_key: str, timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key and api_key != "EMPTY":
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def smoke_payload(kind: str, model: str) -> dict:
    if kind == "main-vlm":
        content = [
            {"type": "text", "text": "Warm up the vision-language model. Reply with OK."},
            {"type": "image_url", "image_url": {"url": WHITE_PNG_DATA_URL}},
        ]
    elif kind == "summary":
        content = "Warm up the summary model. Reply with OK."
    else:
        raise ValueError(f"unsupported smoke kind: {kind}")

    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 2,
        "temperature": 0.0,
        "top_p": 1.0,
    }


def run_smoke(args: argparse.Namespace) -> int:
    url = chat_completions_url(args.api_base)
    payload = smoke_payload(args.kind, args.model)
    for attempt in range(1, args.attempts + 1):
        started = time.perf_counter()
        try:
            response = post_json(url, payload, args.api_key, args.timeout)
            elapsed_ms = (time.perf_counter() - started) * 1000
            choice = (response.get("choices") or [{}])[0]
            text = ((choice.get("message") or {}).get("content") or "").strip()
            print(
                f"{args.kind} smoke: completed in {elapsed_ms:.1f}ms "
                f"(model={args.model}, response={text!r})"
            )
            return 0
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            if attempt >= args.attempts:
                print(
                    f"{args.kind} smoke: failed after {args.attempts} attempt(s): {exc}",
                    file=sys.stderr,
                )
                return 1
            print(
                f"{args.kind} smoke: attempt {attempt}/{args.attempts} failed: {exc}; "
                f"retrying in {args.interval}s",
                file=sys.stderr,
            )
            time.sleep(args.interval)

    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke warmup for JoyVL vLLM services")
    subparsers = parser.add_subparsers(dest="kind", required=True)

    for kind in ("main-vlm", "summary"):
        command = subparsers.add_parser(kind)
        command.add_argument("--api-base", required=True)
        command.add_argument("--model", required=True)
        command.add_argument("--api-key", default=os.environ.get("MODEL_API_KEY", "EMPTY"))
        command.add_argument("--attempts", type=int, default=60)
        command.add_argument("--interval", type=float, default=2.0)
        command.add_argument("--timeout", type=float, default=60.0)

    return parser


def main() -> int:
    return run_smoke(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
