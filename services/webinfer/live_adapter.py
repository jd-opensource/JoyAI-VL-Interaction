#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenAI-compatible live adapter for StreamingHarness inference.

The adapter does not load model weights. It accepts the WebUI's ordinary
OpenAI chat-completion requests, stores incoming base64 frames on disk, keeps
StreamingHarness-style chunk/memory state, and calls the existing vLLM OpenAI
API servers:

  - main 8B inference API
  - summary API for mid-term summaries and long-term compression
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import functools
import io
import json
import logging
import math
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from aiohttp import web
from openai import AsyncOpenAI
from PIL import Image

from memory_summarizer import SummarizerModel


LOGGER = logging.getLogger("streaming_infer_adapter")
USER_QUERY_HEADER_EN = "[User Query (IMPORTANT — follow this instruction)]"
USER_QUERY_HEADER_ZH = "[用户问题（重要——请遵循此指令）]"

VIDEO_HISTORY_HEADER_EN = (
    "[Video History]\n"
    "The following are summaries of earlier video segments you can no longer see. "
    "Use them as background context, but always prioritize the current visual frames "
    "and the User Query below when making decisions.\n"
    "IMPORTANT: These summaries are written by an external system in a descriptive style. "
    "Do NOT imitate their writing style in your responses.\n"
)
VIDEO_HISTORY_HEADER_ZH = (
    "[Video History]\n"
    "以下是你已无法看到的早期视频片段的文字摘要。"
    "将其作为背景上下文使用，但在做决策时始终优先参考当前视觉帧及下方的用户问题。\n"
    "重要：这些摘要由外部系统以描述性风格撰写。不要在你的回复中模仿其写作风格。\n"
)

QA_HISTORY_HEADER_EN = (
    "[Q&A History]\n"
    "The following are previous queries and the system's responses.\n\n"
)
QA_HISTORY_HEADER_ZH = (
    "[Q&A History]\n"
    "以下是之前的用户提问及系统的回复。\n\n"
)

QA_QUERY_LABEL_EN = "Query"
QA_QUERY_LABEL_ZH = "提问"
QA_RESPONSE_LABEL_EN = "Response"
QA_RESPONSE_LABEL_ZH = "回复"


def _get_i18n(language: str = "en") -> dict[str, str]:
    if language == "en":
        return {
            "user_query_header": USER_QUERY_HEADER_EN,
            "video_history_header": VIDEO_HISTORY_HEADER_EN,
            "qa_history_header": QA_HISTORY_HEADER_EN,
            "qa_query_label": QA_QUERY_LABEL_EN,
            "qa_response_label": QA_RESPONSE_LABEL_EN,
        }
    return {
        "user_query_header": USER_QUERY_HEADER_EN,
        "video_history_header": VIDEO_HISTORY_HEADER_ZH,
        "qa_history_header": QA_HISTORY_HEADER_ZH,
        "qa_query_label": QA_QUERY_LABEL_ZH,
        "qa_response_label": QA_RESPONSE_LABEL_ZH,
    }
DEFAULT_SAVE_ROOT = "result"
TIME_RANGE_RE = re.compile(
    r"<(?P<range>\d+(?:\.\d+)?\s*(?:seconds?|s)(?:\s*(?:~|-)\s*\d+(?:\.\d+)?\s*(?:seconds?|s))?)>"
)
TIME_RANGE_VALUE_RE = re.compile(
    r"^(?P<range>\d+(?:\.\d+)?\s*(?:seconds?|s)\s*(?:~|-)\s*\d+(?:\.\d+)?\s*(?:seconds?|s))$"
)
TIME_VALUE_RE = re.compile(r"^(?P<value>\d+(?:\.\d+)?)(?:\s*(?:seconds?|s))$")
DEFAULT_SYSTEM_PROMPT_EN = """You are a real-time video streaming assistant observing a continuous camera feed frame by frame. The last frame represents the current moment.
## Action Format
At every inference step you MUST choose exactly one of the following three actions:
**Stay silent** — output ONLY:
</silence>
Choose this when nothing noteworthy has changed in the scene, no user query is pending, or there is nothing useful to say.
**Speak** — output the token followed by a concise reply:
</response> Your reply here.
Choose this when you observe something worth reporting or a significant state change, or when you can answer a user question based on available evidence.

**Delegate** — when a question is too hard or error-prone to answer reliably yourself, speak a brief note that you're delegating, then hand the question to the background solver:
</response> Brief note that you're delegating. </delegation> <the question>""".strip()




DEFAULT_SYSTEM_PROMPT="""You are a real-time video streaming assistant observing a continuous camera feed frame by frame. The last frame represents the current moment.
## Action Format
At every inference step you MUST choose exactly one of the following three actions:
**Stay silent** — output ONLY:
</silence>
Choose this when nothing noteworthy has changed in the scene, no user query is pending, or there is nothing useful to say.
**Speak** — output the token followed by a concise reply:
</response> Your reply here.
Choose this when you observe something worth reporting or a significant state change, or when you can answer a user question based on available evidence.

**Delegate** — when a question is too hard or error-prone to answer reliably yourself, speak a brief note that you're delegating, then hand the question to the background solver:
</response> Brief note that you're delegating. <delegation> <the question>""".strip()


def reset_chunk_state() -> dict[str, Any]:
    return {
        "messages": [],
        "response_records": [],
        "image_paths": [],
        "frame_time_ranges": [],
        "summarizer_frame_cache": [],
        "frame_count": 0,
        "turn_count": 0,
        "api_msg_cache": [],
    }


def _parse_start_second(time_range: Optional[str]) -> float:
    if not time_range:
        return -1.0
    try:
        start = re.split(r"\s*(?:-|~)\s*", str(time_range), maxsplit=1)[0].strip()
        start = re.sub(r"\s*seconds?$", "", start).strip()
        if start.endswith("s"):
            start = start[:-1]
        return float(start)
    except (ValueError, IndexError):
        return -1.0


def _format_seconds_words(value: float) -> str:
    rounded = math.floor(value * 10 + 0.5) / 10
    return f"{rounded:.1f} seconds"


def _parse_time_value_seconds(text: str) -> Optional[float]:
    match = TIME_VALUE_RE.fullmatch(str(text or "").strip())
    if not match:
        return None
    return float(match.group("value"))


def _normalize_time_range_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None

    match = TIME_RANGE_RE.search(text)
    if match:
        text = match.group("range").strip()

    match = TIME_VALUE_RE.fullmatch(text)
    if match:
        return _format_seconds_words(float(match.group("value")))

    match = TIME_RANGE_VALUE_RE.fullmatch(text)
    if match:
        range_text = match.group("range")
        separator = " ~ " if "~" in range_text else "-"
        parts = re.split(r"\s*(?:~|-)\s*", range_text, maxsplit=1)
        if len(parts) == 2:
            start = _parse_time_value_seconds(parts[0])
            end = _parse_time_value_seconds(parts[1])
            if start is not None and end is not None:
                return f"{_format_seconds_words(start)}{separator}{_format_seconds_words(end)}"
        return range_text

    return None


def _format_time_span(time_ranges: list[str]) -> Optional[str]:
    ranges = [str(tr).strip() for tr in time_ranges if str(tr or "").strip()]
    if not ranges:
        return None
    if len(ranges) == 1:
        return ranges[0]
    return f"{ranges[0]} ~ {ranges[-1]}"


def _format_batch_time_marker(time_ranges: list[str]) -> Optional[str]:
    ranges = [str(tr).strip() for tr in time_ranges if str(tr or "").strip()]
    return ranges[0] if ranges else None


def _format_turn_time_range(time_ranges: list[str]) -> str:
    ranges = [str(tr).strip() for tr in time_ranges if str(tr or "").strip()]
    if not ranges:
        return ""
    if all(time_range == ranges[0] for time_range in ranges):
        return ranges[0]
    return " ~ ".join(ranges)


def _extract_time_range_from_message(message: dict[str, Any]) -> Optional[str]:
    content = message.get("content", "")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text", "")).strip()
                match = TIME_RANGE_RE.search(text)
                if match:
                    return match.group("range")
    elif isinstance(content, str):
        match = TIME_RANGE_RE.search(content)
        if match:
            return match.group("range")
    return None


def _compute_chunk_frame_range(current_chunk: dict[str, Any]) -> str:
    frame_time_ranges = [
        str(time_range).strip()
        for time_range in current_chunk.get("frame_time_ranges", [])
        if str(time_range or "").strip()
    ]
    if frame_time_ranges:
        return _format_time_span(frame_time_ranges) or "unknown"

    user_messages = [
        message for message in current_chunk.get("messages", [])
        if message.get("role") == "user"
    ]
    if not user_messages:
        return "unknown"

    first_range = _extract_time_range_from_message(user_messages[0])
    last_range = _extract_time_range_from_message(user_messages[-1])
    if first_range and last_range:
        return f"{first_range} ~ {last_range}"
    return first_range or last_range or "unknown"


def _get_response_frame_indices(messages: list[dict[str, Any]]) -> list[int]:
    indices: list[int] = []
    frame_idx = -1
    for message in messages:
        if message.get("role") == "user":
            content = message.get("content")
            has_image = (
                isinstance(content, list)
                and any(
                    isinstance(item, dict) and item.get("type") == "image"
                    for item in content
                )
            )
            if has_image:
                frame_idx += 1
        elif message.get("role") == "assistant":
            if "</response>" in str(message.get("content", "")) and frame_idx >= 0:
                indices.append(frame_idx)
    return indices


def normalize_model_output(text: str) -> str:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return "</silence>"

    marker_positions = []
    for marker in ("</response>", "</silence>"):
        idx = raw.find(marker)
        if idx != -1:
            marker_positions.append((idx, marker))

    if marker_positions:
        _, marker = min(marker_positions, key=lambda item: item[0])
        if marker == "</silence>":
            return "</silence>"
        response_text = raw.split(marker, 1)[1].strip()
        if not response_text:
            return "</response>"
        first_line = " ".join(response_text.splitlines()[0].split())
        return f"</response> {first_line}" if first_line else "</response>"

    first_line = " ".join(raw.splitlines()[0].split())
    return f"</response> {first_line}" if first_line else "</silence>"


def extract_response_payload(text: str) -> Optional[str]:
    normalized = normalize_model_output(text)
    if not normalized.startswith("</response>"):
        return None
    payload = normalized[len("</response>"):].strip()
    return payload or None


def sanitize_output_name(name: str, max_len: int = 120) -> str:
    safe_chars = []
    for ch in str(name or ""):
        is_ascii_alnum = ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9")
        safe_chars.append(ch if is_ascii_alnum or ch in ("-", "_", ".") else "_")
    safe_name = "".join(safe_chars).strip("._")
    return (safe_name or "live_adapter")[:max_len]


def derive_model_output_name(model_path: str) -> str:
    normalized = os.path.normpath(str(model_path or "model"))
    model_name = os.path.basename(normalized) or "model"
    parent_name = os.path.basename(os.path.dirname(normalized))
    if model_name.startswith("checkpoint-") and parent_name:
        model_name = f"{parent_name}__{model_name}"
    return sanitize_output_name(model_name)


def resolve_save_dir(path: Optional[str], root: str = DEFAULT_SAVE_ROOT) -> Optional[str]:
    if path is None:
        return None
    path = str(path).strip()
    if not path:
        return None
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(root, path))


def derive_light_out_dir(out_dir: str) -> str:
    normalized_out_dir = os.path.normpath(out_dir)
    parent_dir = os.path.dirname(normalized_out_dir)
    base_name = os.path.basename(normalized_out_dir)
    if base_name.startswith("output_"):
        return os.path.join(parent_dir, f"output_light_{base_name[len('output_'):]}")
    if base_name == "output":
        return os.path.join(parent_dir, "output_light")
    return normalized_out_dir + "_light"


def build_model_input_record(
    chunk_index: int,
    messages: list[dict[str, Any]],
    frame_count: int,
    model: Optional[str] = None,
    generation_kwargs: Optional[dict[str, Any]] = None,
    inference_skipped: bool = False,
    skip_reason: Optional[str] = None,
    image_paths: Optional[list[str]] = None,
    frame_time_ranges: Optional[list[str]] = None,
    prefix_content: Optional[str] = None,
    prompt: Optional[str] = None,
) -> dict[str, Any]:
    if inference_skipped:
        return {
            "http_payload_skipped": True,
            "inference_skipped": True,
            "skip_reason": skip_reason,
            "would_be_messages": list(messages),
        }

    del chunk_index, frame_count, image_paths, frame_time_ranges, prefix_content, prompt
    record = {
        "model": model,
        "messages": list(messages),
    }
    if generation_kwargs:
        record.update(copy.deepcopy(generation_kwargs))
    return record


def build_static_system_content(
    extra_system_messages: Optional[list[str]] = None,
    memory_state: Optional[dict[str, Any]] = None,
    mid_term_summaries: Optional[list[dict[str, Any]]] = None,
    language: str = "en",
) -> str:
    i18n = _get_i18n(language)
    sections: list[str] = []
    for message in extra_system_messages or []:
        if message and message.strip():
            sections.append(message.strip())

    history_parts: list[str] = []
    if memory_state is not None and memory_state.get("long_term_memory"):
        history_parts.append(memory_state["long_term_memory"])
    if mid_term_summaries:
        for entry in mid_term_summaries:
            history_parts.append(f"<{entry['frame_range']}>\n{entry['summary_text']}")

    if history_parts:
        sections.append(
            i18n["video_history_header"]
            + "\n\n".join(history_parts)
        )

    return "\n\n".join(sections) if sections else ""


def build_dynamic_system_content(
    current_query_text: Optional[str] = None,
    memory_state: Optional[dict[str, Any]] = None,
    include_qa_history: bool = True,
    current_chunk_index: int = 0,
    language: str = "en",
) -> str:
    i18n = _get_i18n(language)
    sections: list[str] = []

    if include_qa_history and memory_state is not None and memory_state.get("qa_history"):
        qa_entries = [
            entry for entry in memory_state["qa_history"]
            if entry.get("archived_in_chunk", 0) < current_chunk_index
        ]
        if qa_entries:
            qa_lines: list[str] = []
            for idx, entry in enumerate(qa_entries, 1):
                q_time = entry["query_time"] or "N/A"
                parts = [f"#{idx} [{i18n['qa_query_label']}@{q_time}] {entry['query']}"]
                for response_time, payload in entry.get("responses", []):
                    parts.append(f"[{i18n['qa_response_label']}@{response_time}] {payload}")
                qa_lines.append("\n".join(parts))
            sections.append(
                i18n["qa_history_header"]
                + "\n".join(qa_lines)
            )

    if current_query_text:
        sections.append(i18n["user_query_header"] + "\n" + current_query_text.strip())

    return "\n\n".join(sections) if sections else ""


def archive_chunk_response_records(
    current_chunk: dict[str, Any],
    memory_state: dict[str, Any],
    current_query_text: Optional[str],
    query_start_time: Optional[str],
    chunk_index: int = 0,
    before_time_sec: float = float("inf"),
) -> None:
    if not current_chunk["response_records"] or not current_query_text:
        return

    query_start_sec = _parse_start_second(query_start_time)
    valid_records = [
        (time_range, payload)
        for time_range, payload in current_chunk["response_records"]
        if query_start_sec <= _parse_start_second(time_range) < before_time_sec
    ]
    if not valid_records:
        return

    existing = None
    for entry in memory_state["qa_history"]:
        if (
            entry["query"] == current_query_text
            and entry.get("archived_in_chunk") == chunk_index
            and entry.get("query_time") == query_start_time
        ):
            existing = entry
            break
    if existing:
        existing["responses"].extend(valid_records)
        existing["archived_in_chunk"] = chunk_index
    else:
        memory_state["qa_history"].append(
            {
                "query_time": query_start_time,
                "query": current_query_text,
                "responses": list(valid_records),
                "archived_in_chunk": chunk_index,
            }
        )


@dataclass
class AdapterConfig:
    host: str = "127.0.0.1"
    port: int = 8070
    adapter_model: str = "streaming-infer-adapter"
    main_api_base: str = "http://127.0.0.1:7060/v1"
    main_model: str = "streamingharness-8b"
    main_backends: tuple[dict[str, str], ...] = ()
    api_key: str = "EMPTY"
    allowed_local_image_roots: tuple[str, ...] = ()
    frame_seconds: float = 1.0
    max_pixels: int = 262144
    main_max_tokens: int = 128
    main_temperature: float = 0.8
    main_top_p: float = 0.9
    main_top_k: int = 40
    main_repetition_penalty: float = 1.0
    main_presence_penalty: float = 0.0
    honor_inbound_generation_params: bool = False
    chunk: int = 200
    compress_every_n_chunks: int = 5
    async_summary_lead_frames: int = 10
    use_prompt_as_query: bool = True
    force_silence_before_query: bool = True
    keep_qa_history: bool = True
    normalize_output: bool = True
    enable_summarizer: bool = True
    summarizer_model: str = "/tmp/models/Qwen3-VL-4B-Instruct"
    summarizer_api_base: str = "http://127.0.0.1:8065/v1"
    longterm_model: str = "/tmp/models/Qwen3-VL-4B-Instruct"
    longterm_api_base: str = "http://127.0.0.1:8065/v1"
    summarizer_max_pixels: int = 262144
    summarizer_key_frames: int = 0
    summarizer_phase_seconds: float = 10.0
    mid_term_max_tokens: int = 4000
    mid_term_target_tokens: int = 3000
    long_term_max_tokens: int = 2000
    long_term_target_tokens: int = 1000
    mid_term_temperature: float = 0.8
    mid_term_top_p: float = 0.9
    mid_term_top_k: int = 40
    mid_term_repetition_penalty: float = 1.1
    mid_term_presence_penalty: float = 0.0
    long_term_temperature: float = 1.0
    long_term_top_p: float = 1.0
    long_term_top_k: int = 80
    long_term_repetition_penalty: float = 1.1
    long_term_presence_penalty: float = 0.0
    long_term_memory_window: int = 40
    request_timeout_seconds: float = 300.0
    session_timeout_seconds: float = 3600.0
    out_dir: Optional[str] = None
    light_out_dir: Optional[str] = None
    debug_input_dir: Optional[str] = None
    save_root: Optional[str] = None
    output_model_name: str = ""
    per_session_dirs: bool = True
    save_model_inputs: bool = True
    save_debug_inputs: bool = False
    summarizer_debug: bool = False
    frame_save_dir: str = "/tmp/streaming_adapter_frames"
    language: str = "en"
    system_prompt: str = DEFAULT_SYSTEM_PROMPT_EN


@dataclass
class SessionState:
    session_id: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    frame_count: int = 0
    turn_count: int = 0
    chunk_index: int = 1
    current_chunk: dict[str, Any] = field(default_factory=reset_chunk_state)
    memory_state: dict[str, Any] = field(
        default_factory=lambda: {"long_term_memory": "", "qa_history": []}
    )
    current_query_text: Optional[str] = None
    query_start_time: Optional[str] = None
    query_in_current_chunk: bool = False
    mid_term_summaries: list[dict[str, Any]] = field(default_factory=list)
    mid_term_history: list[dict[str, Any]] = field(default_factory=list)
    long_term_history: list[dict[str, Any]] = field(default_factory=list)
    long_term_compression_next_index: int = 1
    async_summary_segment: dict[str, Any] = field(default_factory=reset_chunk_state)
    async_next_summary_target_turns: int = 0
    async_next_summary_index: int = 1
    async_pending_summary_jobs: list[dict[str, Any]] = field(default_factory=list)
    predictions: list[dict[str, Any]] = field(default_factory=list)
    session_started_at: float = field(default_factory=time.time)
    output_path: Optional[Path] = None
    light_output_path: Optional[Path] = None
    debug_input_dir: Optional[Path] = None
    session_out_dir: Optional[str] = None
    session_light_out_dir: Optional[str] = None
    session_frame_dir: Optional[Path] = None
    session_frame_counter: int = 0
    chunk_start_input_saved: set[int] = field(default_factory=set)
    last_access: float = field(default_factory=time.time)
    _pending_qa_archive: Optional[tuple[str, Optional[str]]] = field(default=None, repr=False)
    _pending_write_task: Optional[asyncio.Task] = field(default=None, repr=False)


class StreamingInferAdapter:
    def __init__(self, config: AdapterConfig):
        self.config = config
        self.sessions: dict[str, SessionState] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        Path(config.frame_save_dir).mkdir(parents=True, exist_ok=True)
        self.main_client = AsyncOpenAI(
            base_url=config.main_api_base,
            api_key=config.api_key,
            timeout=config.request_timeout_seconds,
        )
        self.main_clients: dict[str, tuple[AsyncOpenAI, str]] = {}
        if config.main_backends:
            for backend in config.main_backends:
                name = backend["name"]
                self.main_clients[name] = (
                    AsyncOpenAI(
                        base_url=backend["api_base"],
                        api_key=config.api_key,
                        timeout=config.request_timeout_seconds,
                    ),
                    backend.get("model", name),
                )
        else:
            self.main_clients[config.main_model] = (self.main_client, config.main_model)
        self.summarizer: Optional[SummarizerModel] = None
        if config.enable_summarizer:
            self.summarizer = SummarizerModel(
                model_name=config.summarizer_model,
                api_base=config.summarizer_api_base,
                longterm_model_name=config.longterm_model,
                longterm_api_base=config.longterm_api_base,
                mid_term_max_tokens=config.mid_term_max_tokens,
                mid_term_target_tokens=config.mid_term_target_tokens,
                long_term_max_tokens=config.long_term_max_tokens,
                long_term_target_tokens=config.long_term_target_tokens,
                key_frames_per_chunk=config.summarizer_key_frames,
                max_pixels=config.summarizer_max_pixels,
                prompt_phase_seconds=config.summarizer_phase_seconds,
                mid_term_temperature=config.mid_term_temperature,
                mid_term_top_p=config.mid_term_top_p,
                mid_term_top_k=config.mid_term_top_k,
                mid_term_repetition_penalty=config.mid_term_repetition_penalty,
                mid_term_presence_penalty=config.mid_term_presence_penalty,
                long_term_temperature=config.long_term_temperature,
                long_term_top_p=config.long_term_top_p,
                long_term_top_k=config.long_term_top_k,
                long_term_repetition_penalty=config.long_term_repetition_penalty,
                long_term_presence_penalty=config.long_term_presence_penalty,
                debug=config.summarizer_debug,
            )
        if config.out_dir:
            Path(config.out_dir).mkdir(parents=True, exist_ok=True)
        if config.light_out_dir:
            Path(config.light_out_dir).mkdir(parents=True, exist_ok=True)
        if config.debug_input_dir:
            Path(config.debug_input_dir).mkdir(parents=True, exist_ok=True)

    def _resolve_backend(self, model_name: Optional[str] = None) -> tuple[AsyncOpenAI, str]:
        if model_name and model_name in self.main_clients:
            return self.main_clients[model_name]
        return self.main_client, self.config.main_model

    def get_session(self, session_id: str) -> SessionState:
        session_id = _safe_session_id(session_id or "default")
        state = self.sessions.get(session_id)
        if state is None:
            state = SessionState(session_id=session_id)
            if self.config.per_session_dirs and self.config.save_root:
                self._init_session_dirs(state)
            state.output_path = self._session_output_path(state, light=False)
            state.light_output_path = self._session_output_path(state, light=True)
            state.debug_input_dir = self._session_debug_input_dir(state)
            state.async_next_summary_target_turns = self._async_first_summary_turns()
            # Per-session frame directory
            frame_dir = Path(self.config.frame_save_dir) / session_id
            frame_dir.mkdir(parents=True, exist_ok=True)
            state.session_frame_dir = frame_dir
            self.sessions[session_id] = state
            LOGGER.info(
                "Created session %s (output=%s light=%s debug_input=%s frames=%s)",
                session_id,
                state.output_path,
                state.light_output_path,
                state.debug_input_dir,
                state.session_frame_dir,
            )
        state.last_access = time.time()
        return state

    def _cleanup_expired_sessions(self) -> list[SessionState]:
        now = time.time()
        timeout = self.config.session_timeout_seconds
        expired = [
            sid for sid, s in self.sessions.items()
            if now - s.last_access > timeout
        ]
        expired_states = []
        for sid in expired:
            state = self.sessions.pop(sid, None)
            if state is not None:
                for job in state.async_pending_summary_jobs:
                    job["task"].cancel()
                expired_states.append(state)
                LOGGER.info("Expired session %s (idle %.0fs)", sid, now - state.last_access)
        return expired_states

    async def _session_cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(300)
            try:
                expired_states = self._cleanup_expired_sessions()
                for state in expired_states:
                    await self._flush_session_outputs(state)
            except Exception:
                LOGGER.exception("session cleanup error")

    def start_background_tasks(self) -> None:
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.ensure_future(self._session_cleanup_loop())

    def _init_session_dirs(self, state: SessionState) -> None:
        """Create per-session timestamped output/input directories."""
        session_ts = datetime.fromtimestamp(state.session_started_at).strftime("%Y%m%d_%H%M%S")
        model_name = self.config.output_model_name
        save_root = self.config.save_root

        state.session_out_dir = os.path.join(save_root, f"output_{session_ts}_{model_name}")
        state.session_light_out_dir = derive_light_out_dir(state.session_out_dir)

        Path(state.session_out_dir).mkdir(parents=True, exist_ok=True)
        Path(state.session_light_out_dir).mkdir(parents=True, exist_ok=True)

        if self.config.save_debug_inputs:
            state.debug_input_dir = Path(os.path.join(save_root, f"input_{session_ts}_{model_name}"))
            state.debug_input_dir.mkdir(parents=True, exist_ok=True)

    async def handle_models(self, request: web.Request) -> web.Response:
        del request
        now = int(time.time())
        data = [
            {
                "id": name,
                "object": "model",
                "created": now,
                "owned_by": "streamingharness",
            }
            for name in self.main_clients
        ]
        return web.json_response({"object": "list", "data": data})

    async def handle_health(self, request: web.Request) -> web.Response:
        del request
        return web.json_response(
            {
                "ok": True,
                "model": self.config.adapter_model,
                "backends": list(self.main_clients.keys()),
                "summarizer_enabled": self.summarizer is not None,
                "sessions": len(self.sessions),
            }
        )

    async def handle_reset(self, request: web.Request) -> web.Response:
        payload = await _read_json(request)
        session_id = _request_session_id(request, payload)
        session_id = _safe_session_id(session_id)
        removed_state = self.sessions.pop(session_id, None)
        if removed_state is not None:
            for job in removed_state.async_pending_summary_jobs:
                job["task"].cancel()
            await self._flush_session_outputs(removed_state)
        removed = removed_state is not None
        return web.json_response({"ok": True, "session_id": session_id, "removed": removed})

    async def handle_chat_completions(self, request: web.Request) -> web.Response:
        payload = await _read_json(request)
        session_id = _request_session_id(request, payload)
        requested_model = payload.get("model")
        client, model_name = self._resolve_backend(requested_model)
        state = self.get_session(session_id)
        async with state.lock:
            try:
                result = await self._handle_chat_payload(state, payload, request, client=client, model_name=model_name)
            except web.HTTPException:
                raise
            except Exception as exc:
                LOGGER.exception("chat completion failed")
                return _openai_error_response(str(exc), status=502)
        return web.json_response(result)

    def _session_output_path(self, state: SessionState, light: bool) -> Optional[Path]:
        if light:
            root = state.session_light_out_dir or self.config.light_out_dir
        else:
            root = state.session_out_dir or self.config.out_dir
        if not root:
            return None
        safe_session = sanitize_output_name(state.session_id)
        return Path(root) / "live" / f"{safe_session}.json"

    def _session_debug_input_dir(self, state: SessionState) -> Optional[Path]:
        if state.debug_input_dir:
            return state.debug_input_dir
        if not self.config.debug_input_dir:
            return None
        return Path(self.config.debug_input_dir)

    def _session_sample_data(self, state: SessionState) -> dict[str, Any]:
        return {
            "task_type": "live",
            "session_id": state.session_id,
            "adapter_model": self.config.adapter_model,
            "main_model": self.config.main_model,
            "main_api_base": self.config.main_api_base,
            "summarizer_model": self.config.summarizer_model,
            "summarizer_api_base": self.config.summarizer_api_base,
            "longterm_model": self.config.longterm_model,
            "longterm_api_base": self.config.longterm_api_base,
            "started_at": datetime.fromtimestamp(state.session_started_at).isoformat(
                timespec="seconds"
            ),
        }

    def _memory_trace(self, state: SessionState) -> dict[str, Any]:
        return {
            "mid_term_summaries": list(state.mid_term_history),
            "long_term_history": list(state.long_term_history),
            "qa_history": list(state.memory_state.get("qa_history", [])),
            "long_term_memory": state.memory_state.get("long_term_memory", ""),
        }

    def _write_json_file(self, path: Path, obj: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as file_obj:
            json.dump(obj, file_obj, ensure_ascii=False, indent=2)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(tmp_path, path)

    def _light_predictions(
        self,
        predictions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        light_keys = (
            "turn",
            "time_range",
            "query",
            "prediction",
            "total_time",
            "inference_time",
            "fourb_mid_term_inference_time",
            "fourb_long_term_inference_time",
            "ground_truth",
        )
        return [
            {key: prediction[key] for key in light_keys if key in prediction}
            for prediction in predictions
        ]

    @staticmethod
    def _strip_base64_images(obj: Any) -> tuple[Any, dict[str, str]]:
        """Recursively strip inline base64 image data from an object.

        Returns a (stripped_obj, images_dict) tuple where images_dict maps
        placeholder keys to the original base64 strings.
        """
        images: dict[str, str] = {}
        counter = [0]

        def _strip(node: Any) -> Any:
            if isinstance(node, str):
                if node.startswith("data:image/") and len(node) > 200:
                    key = f"__image_{counter[0]}__"
                    counter[0] += 1
                    images[key] = node
                    return key
                return node
            if isinstance(node, list):
                return [_strip(item) for item in node]
            if isinstance(node, dict):
                return {k: _strip(v) for k, v in node.items()}
            return node

        stripped = _strip(obj)
        return stripped, images

    def _write_session_outputs_sync(
        self,
        output_path: Optional[Path],
        light_output_path: Optional[Path],
        full_result: Optional[dict[str, Any]],
        light_result: Optional[dict[str, Any]],
    ) -> None:
        if light_output_path and light_result:
            self._write_json_file(light_output_path, light_result)
        if output_path and full_result:
            stripped_result, images = self._strip_base64_images(full_result)
            self._write_json_file(output_path, stripped_result)
            if images:
                images_path = output_path.with_suffix(".images.json")
                self._write_json_file(images_path, images)

    def _write_session_outputs(self, state: SessionState) -> None:
        total_time = time.time() - state.session_started_at
        output_path = state.output_path
        light_output_path = state.light_output_path
        if not output_path and not light_output_path:
            return
        predictions_snapshot = copy.deepcopy(state.predictions)
        sample_data = self._session_sample_data(state)
        memory_trace = copy.deepcopy(self._memory_trace(state))
        full_result = None
        light_result = None
        if output_path:
            full_result = {
                "sample_data": sample_data,
                "total_time": total_time,
                "total_turns": len(predictions_snapshot),
                "predictions": predictions_snapshot,
                "memory": memory_trace,
            }
        if light_output_path:
            light_result = {
                "sample_data": sample_data,
                "total_time": total_time,
                "total_turns": len(predictions_snapshot),
                "predictions": self._light_predictions(predictions_snapshot),
                "memory": memory_trace,
            }
        if state._pending_write_task and not state._pending_write_task.done():
            state._pending_write_task.cancel()
        task = asyncio.ensure_future(
            asyncio.to_thread(
                self._write_session_outputs_sync,
                output_path,
                light_output_path,
                full_result,
                light_result,
            )
        )
        task.add_done_callback(self._on_write_task_done)
        state._pending_write_task = task

    @staticmethod
    def _on_write_task_done(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            LOGGER.error("session output write failed: %s", exc, exc_info=exc)

    async def _flush_session_outputs(self, state: SessionState) -> None:
        """Write final session outputs synchronously at session end."""
        self._execute_pending_qa_archive(state)
        if self.config.keep_qa_history and state.current_query_text:
            archive_chunk_response_records(
                state.current_chunk,
                state.memory_state,
                state.current_query_text,
                state.query_start_time,
                chunk_index=state.chunk_index,
            )
        total_time = time.time() - state.session_started_at
        output_path = state.output_path
        light_output_path = state.light_output_path
        if not output_path and not light_output_path:
            return
        if not state.predictions:
            return
        sample_data = self._session_sample_data(state)
        memory_trace = self._memory_trace(state)
        full_result = None
        light_result = None
        if output_path:
            full_result = {
                "sample_data": sample_data,
                "total_time": total_time,
                "total_turns": len(state.predictions),
                "predictions": state.predictions,
                "memory": memory_trace,
            }
        if light_output_path:
            light_result = {
                "sample_data": sample_data,
                "total_time": total_time,
                "total_turns": len(state.predictions),
                "predictions": self._light_predictions(state.predictions),
                "memory": memory_trace,
            }
        await asyncio.to_thread(
            self._write_session_outputs_sync,
            output_path,
            light_output_path,
            full_result,
            light_result,
        )
        LOGGER.info(
            "[%s] final session output written (%d turns)",
            state.session_id,
            len(state.predictions),
        )

    def _save_live_debug_input(
        self,
        state: SessionState,
        record: dict[str, Any],
        stem: str,
    ) -> Optional[str]:
        debug_dir = state.debug_input_dir or self.config.debug_input_dir
        if not debug_dir:
            return None
        path = (
            Path(debug_dir)
            / f"{sanitize_output_name(state.session_id)}__{stem}.json"
        )
        record = copy.deepcopy(record)
        record.setdefault("saved_at", datetime.now().isoformat(timespec="seconds"))
        record.setdefault("session_id", state.session_id)
        self._write_json_file(path, record)
        return str(path)

    def _maybe_save_chunk_start_model_input(
        self,
        state: SessionState,
        turn_count: int,
        time_range: str,
        model_input_record: dict[str, Any],
    ) -> Optional[str]:
        if not (state.debug_input_dir or self.config.debug_input_dir):
            return None
        if state.chunk_index in state.chunk_start_input_saved:
            return None
        record = copy.deepcopy(model_input_record)
        record["stage"] = "main_8b_chunk_start"
        record["turn"] = turn_count
        record["time_range"] = time_range
        path = self._save_live_debug_input(
            state,
            record,
            f"chunk_{state.chunk_index:04d}__turn_{turn_count:04d}",
        )
        state.chunk_start_input_saved.add(state.chunk_index)
        return path

    def _save_summarizer_debug_input(
        self,
        state: SessionState,
        stage: str,
        index: int,
        record: Optional[dict[str, Any]],
    ) -> Optional[str]:
        if not record:
            return None
        return self._save_live_debug_input(
            state,
            record,
            f"{stage}__{index:04d}",
        )

    async def _handle_chat_payload(
        self,
        state: SessionState,
        payload: dict[str, Any],
        request: web.Request,
        *,
        client: Optional[AsyncOpenAI] = None,
        model_name: Optional[str] = None,
    ) -> dict[str, Any]:
        client = client or self.main_client
        model_name = model_name or self.config.main_model
        t_start = time.perf_counter()
        messages = payload.get("messages") or []
        if not isinstance(messages, list):
            raise web.HTTPBadRequest(text="messages must be a list")

        image_refs = _extract_all_image_refs(messages, request, payload)
        if not image_refs:
            return await self._forward_text_only(payload, client=client, model_name=model_name)

        turn_count = len(state.predictions) + 1
        raw_prompt_text = _extract_user_prompt_text(messages)
        prompt_text = _strip_time_range_from_text(raw_prompt_text)

        # Resolve time ranges for all images
        incoming_time_ranges = _extract_time_ranges_from_request(request, payload)
        if not incoming_time_ranges:
            single = _extract_time_range_from_request(request, payload)
            if single is None:
                single = _extract_time_range_from_text(raw_prompt_text)
            if single:
                incoming_time_ranges = [single]
        time_ranges: list[str] = []
        for i in range(len(image_refs)):
            if i < len(incoming_time_ranges) and incoming_time_ranges[i]:
                time_ranges.append(incoming_time_ranges[i])
            else:
                time_ranges.append(self._time_range_for_frame(state.frame_count + i))
        time_range = _format_turn_time_range(time_ranges)

        image_paths = [self._resolve_frame_ref(ref, state) for ref in image_refs]
        LOGGER.info(
            "[%s] turn=%d frames=%d(+%d) chunk=%d time=%s prompt=%r",
            state.session_id,
            turn_count,
            state.frame_count,
            len(image_refs),
            state.chunk_index,
            time_range,
            _short(prompt_text, 80),
        )

        query_text = self._update_query_state(state, prompt_text, time_ranges[0])

        await self._commit_required_async_summaries(
            state, state.turn_count, non_blocking=True,
        )

        if (
            self.config.chunk > 0
            and state.current_chunk["turn_count"] >= self.config.chunk
        ):
            self._execute_pending_qa_archive(state)
            carry_response_records = []
            if self.config.keep_qa_history and state.current_query_text:
                qa_cutoff = float("inf")
                if (
                    self._async_summary_enabled()
                    and state.async_summary_segment["frame_time_ranges"]
                ):
                    qa_cutoff = _parse_start_second(
                        state.async_summary_segment["frame_time_ranges"][0]
                    )
                    carry_response_records = [
                        (tr, payload)
                        for tr, payload in state.current_chunk["response_records"]
                        if _parse_start_second(tr) >= qa_cutoff
                    ]
                archive_chunk_response_records(
                    state.current_chunk,
                    state.memory_state,
                    state.current_query_text,
                    state.query_start_time,
                    chunk_index=state.chunk_index,
                    before_time_sec=qa_cutoff,
                )
            await self._flush_chunk(state, use_async_summary=self._async_summary_enabled())
            if (
                self._async_summary_enabled()
                and state.async_summary_segment["turn_count"] > 0
            ):
                carry = copy.deepcopy(state.async_summary_segment)
                carry_frames = carry["frame_count"]
                carry_turns = carry["turn_count"]
                carry["frame_count"] = 0
                carry["turn_count"] = 0
                carry["response_records"] = carry_response_records
                carry["api_msg_cache"] = []
                state.current_chunk = carry
                LOGGER.info(
                    "[%s] carried over %d unsummarized turn(s), %d frame(s) to new chunk",
                    state.session_id,
                    carry_turns,
                    carry_frames,
                )
            else:
                state.current_chunk = reset_chunk_state()
            state.chunk_index += 1
            state.query_in_current_chunk = bool(query_text)

        for tr, ip in zip(time_ranges, image_paths):
            state.frame_count += 1
            state.current_chunk["image_paths"].append(str(ip))
            state.current_chunk["frame_time_ranges"].append(tr)
            state.current_chunk["summarizer_frame_cache"].append({"path": str(ip)})
            state.current_chunk["frame_count"] += 1

        state.turn_count += 1
        state.current_chunk["turn_count"] += 1

        user_message = self._build_internal_user_message(
            time_ranges=time_ranges,
            image_paths=[str(ip) for ip in image_paths],
            query_text=query_text,
        )
        state.current_chunk["messages"].append(user_message)
        if self._async_summary_enabled():
            self._append_async_summary_user_message(
                state,
                time_ranges=time_ranges,
                image_paths=[str(ip) for ip in image_paths],
                query_text=query_text,
            )

        turn_input_record = {
            "source_message": messages[-1] if messages else None,
            "vllm_message": user_message,
            "chunk_index": state.chunk_index,
            "has_image": True,
            "image_path": str(image_paths[-1]),
            "image_paths_batch": [str(ip) for ip in image_paths],
            "num_chunk_turns": state.current_chunk["turn_count"],
            "num_chunk_frames": state.current_chunk["frame_count"],
            "image_paths": list(state.current_chunk["image_paths"]),
            "frame_time_ranges": list(state.current_chunk["frame_time_ranges"]),
        }
        is_forced_silence = (
            self.config.force_silence_before_query and not state.current_query_text
        )
        inference_start = None
        inference_time = 0.0
        chunk_start_model_input_path = None
        turn_model_input_record = None
        model_input_record = None

        if is_forced_silence:
            generated_text = "</silence>"
            raw_text = ""
            usage = None
            turn_model_input_record = build_model_input_record(
                chunk_index=state.chunk_index,
                messages=state.current_chunk["messages"],
                frame_count=state.current_chunk["frame_count"],
                inference_skipped=True,
                skip_reason="force_silence_before_query",
                image_paths=state.current_chunk["image_paths"],
                frame_time_ranges=state.current_chunk["frame_time_ranges"],
            )
            if self.config.save_model_inputs:
                model_input_record = turn_model_input_record
        else:
            t_prompt_build_start = time.perf_counter()
            internal_messages, prefix_content = self._build_main_internal_messages(state)
            api_messages = self._build_cached_api_messages(state, internal_messages)
            generation_kwargs = self._main_generation_kwargs(payload)
            http_messages = self._build_main_http_messages(api_messages)
            turn_model_input_record = build_model_input_record(
                chunk_index=state.chunk_index,
                messages=http_messages,
                frame_count=state.current_chunk["frame_count"],
                model=model_name,
                generation_kwargs=generation_kwargs,
                image_paths=state.current_chunk["image_paths"],
                frame_time_ranges=state.current_chunk["frame_time_ranges"],
                prefix_content=prefix_content,
            )
            if self.config.save_model_inputs:
                model_input_record = turn_model_input_record
            chunk_start_model_input_path = self._maybe_save_chunk_start_model_input(
                state,
                turn_count,
                time_range,
                turn_model_input_record,
            )
            t_prompt_build_end = time.perf_counter()
            inference_start = time.time()
            raw_text, usage = await self._call_main_model(
                payload,
                api_messages,
                client=client,
                model_name=model_name,
                session_state=state,
                generation_kwargs=generation_kwargs,
                http_messages=http_messages,
            )
            inference_time = time.time() - inference_start
            t_inference_end = time.perf_counter()
            generated_text = (
                normalize_model_output(raw_text)
                if self.config.normalize_output
                else (raw_text or "").strip()
            )

        self._execute_pending_qa_archive(state)

        response_payload = extract_response_payload(generated_text)
        if response_payload and state.current_query_text:
            state.current_chunk["response_records"].append((time_range, response_payload))

        state.current_chunk["messages"].append(
            {"role": "assistant", "content": generated_text}
        )
        if self._async_summary_enabled():
            state.async_summary_segment["messages"].append(
                {"role": "assistant", "content": generated_text}
            )
            self._submit_async_summary_if_needed(state)

        turn_output_record = {}
        if is_forced_silence:
            turn_output_record["inference_skipped"] = True
            turn_output_record["skip_reason"] = "force_silence_before_query"

        t_end = time.perf_counter()
        total_time = t_end - t_start

        prediction = {
            "turn": turn_count,
            "time_range": time_range,
            "query": query_text,
            "input": turn_input_record,
            "output": turn_output_record,
            "prediction": generated_text,
            "total_time": round(total_time, 3),
            "inference_time": round(inference_time, 3),
        }
        if model_input_record is not None:
            turn_input_record["model_input"] = model_input_record
        if chunk_start_model_input_path:
            prediction["chunk_start_model_input_path"] = chunk_start_model_input_path
        if raw_text and raw_text.strip() != generated_text:
            prediction["raw_prediction"] = raw_text
        state.predictions.append(prediction)

        t_end = time.perf_counter()
        adapter_timing = {
            "adapter_total_ms": round((t_end - t_start) * 1000, 1),
        }
        if not is_forced_silence:
            adapter_timing["prompt_build_ms"] = round((t_prompt_build_end - t_prompt_build_start) * 1000, 1)
            adapter_timing["vllm_inference_ms"] = round(inference_time * 1000, 1)
            adapter_timing["post_process_ms"] = round((t_end - t_inference_end) * 1000, 1)
            adapter_timing["pre_inference_ms"] = round((t_prompt_build_start - t_start) * 1000, 1)

        if not is_forced_silence:
            LOGGER.info(
                "[%s] turn=%d timing: total=%.1fms pre=%.1fms prompt_build=%.1fms vllm=%.1fms post=%.1fms",
                state.session_id,
                turn_count,
                adapter_timing["adapter_total_ms"],
                adapter_timing["pre_inference_ms"],
                adapter_timing["prompt_build_ms"],
                adapter_timing["vllm_inference_ms"],
                adapter_timing["post_process_ms"],
            )
        else:
            LOGGER.info(
                "[%s] turn=%d timing: total=%.1fms (forced silence, inference skipped)",
                state.session_id,
                turn_count,
                adapter_timing["adapter_total_ms"],
            )

        result = _chat_completion_response(
            model=self.config.adapter_model,
            content=generated_text,
            usage=usage,
            raw_model=model_name,
            raw_text=raw_text,
        )
        result["streamingharness"]["timing"] = adapter_timing
        summarizer_timing = {}
        if state.mid_term_history:
            last_mid = state.mid_term_history[-1]
            summarizer_timing["last_mid_term_ms"] = round(last_mid.get("inference_time", 0) * 1000, 1)
            summarizer_timing["last_mid_term_chunk"] = last_mid.get("chunk_index")
            if last_mid.get("barrier_wait_time") is not None:
                summarizer_timing["barrier_wait_ms"] = round(last_mid["barrier_wait_time"] * 1000, 1)
        if state.long_term_history:
            last_long = state.long_term_history[-1]
            summarizer_timing["last_long_term_ms"] = round(last_long.get("inference_time", 0) * 1000, 1)
        result["streamingharness"]["summarizer_timing"] = summarizer_timing
        result["streamingharness"]["memory"] = {
            "mid_term_summaries": [
                {"chunk_index": e["chunk_index"], "frame_range": e["frame_range"], "summary_text": e["summary_text"]}
                for e in state.mid_term_summaries
            ],
            "long_term_memory": state.memory_state.get("long_term_memory", ""),
        }
        return result

    async def _forward_text_only(
        self,
        payload: dict[str, Any],
        *,
        client: Optional[AsyncOpenAI] = None,
        model_name: Optional[str] = None,
    ) -> dict[str, Any]:
        client = client or self.main_client
        model_name = model_name or self.config.main_model
        generation_kwargs = self._main_generation_kwargs(payload)
        response = await client.chat.completions.create(
            model=model_name,
            messages=payload.get("messages") or [],
            **generation_kwargs,
        )
        raw_text = response.choices[0].message.content if response.choices else ""
        usage = response.usage.model_dump() if getattr(response, "usage", None) else None
        return _chat_completion_response(
            model=self.config.adapter_model,
            content=raw_text or "",
            usage=usage,
            raw_model=model_name,
            raw_text=raw_text or "",
        )

    def _time_range_for_frame(self, frame_index: int) -> str:
        start = frame_index * self.config.frame_seconds
        return f"{start:.1f} seconds"

    def _resolve_frame_ref(
        self,
        image_ref: dict[str, str],
        state: "SessionState",
    ) -> str:
        if image_ref.get("kind") == "path":
            return str(self._validate_local_image_path(image_ref.get("value", "")))
        if image_ref.get("kind") == "data_url":
            return self._save_base64_frame(image_ref.get("value", ""), state)
        raise web.HTTPBadRequest(text="unsupported image reference kind")

    def _save_base64_frame(self, data_url: str, state: "SessionState") -> str:
        match = re.match(r"data:image/\w+;base64,(.+)", data_url)
        if not match:
            raise web.HTTPBadRequest(text="invalid data URL format")
        state.session_frame_counter += 1
        return data_url

    def _validate_local_image_path(self, raw_path: str) -> Path:
        if not self.config.allowed_local_image_roots:
            raise web.HTTPBadRequest(text="local image paths are disabled")

        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise web.HTTPBadRequest(text=f"local image path does not exist: {path}")
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            raise web.HTTPBadRequest(text=f"unsupported local image extension: {path.suffix}")

        for root in self.config.allowed_local_image_roots:
            root_path = Path(root).expanduser().resolve()
            try:
                path.relative_to(root_path)
                return path
            except ValueError:
                continue

        allowed = ", ".join(self.config.allowed_local_image_roots)
        raise web.HTTPBadRequest(text=f"local image path is outside allowed roots: {allowed}")

    def _update_query_state(
        self,
        state: SessionState,
        prompt_text: str,
        time_range: str,
    ) -> Optional[str]:
        if not self.config.use_prompt_as_query:
            return None

        normalized_prompt = (prompt_text or "").strip()
        if not normalized_prompt:
            return None

        if state.current_query_text is None:
            state.current_query_text = normalized_prompt
            state.query_start_time = time_range
            state.query_in_current_chunk = True
            return normalized_prompt

        if normalized_prompt != state.current_query_text:
            state._pending_qa_archive = (state.current_query_text, state.query_start_time)
            state.current_query_text = normalized_prompt
            state.query_start_time = time_range
            state.query_in_current_chunk = True
            return normalized_prompt

        return state.current_query_text

    def _execute_pending_qa_archive(self, state: SessionState) -> None:
        if state._pending_qa_archive is None:
            return
        old_query, old_start_time = state._pending_qa_archive
        archive_chunk_response_records(
            state.current_chunk,
            state.memory_state,
            old_query,
            old_start_time,
            chunk_index=state.chunk_index,
        )
        state.current_chunk["response_records"] = []
        state._pending_qa_archive = None

    def _build_internal_user_message(
        self,
        time_range=None,
        image_path=None,
        query_text=None,
        *,
        time_ranges=None,
        image_paths=None,
    ) -> dict[str, Any]:
        i18n = _get_i18n(self.config.language)
        if time_ranges is None:
            time_ranges = [time_range] if time_range else []
        if image_paths is None:
            image_paths = [image_path] if image_path else []
        content: list[dict[str, Any]] = []
        if query_text:
            content.append({"type": "text", "text": i18n["user_query_header"] + "\n" + query_text})
        batch_time_marker = _format_batch_time_marker(time_ranges)
        if batch_time_marker:
            content.append({"type": "text", "text": f"<{batch_time_marker}>"})
        for ip in image_paths:
            content.append(
                {
                    "type": "image",
                    "image": ip,
                    "max_pixels": self.config.max_pixels,
                }
            )
        return {"role": "user", "content": content}

    def _build_main_internal_messages(
        self,
        state: SessionState,
    ) -> tuple[list[dict[str, Any]], str]:
        memory_state = state.memory_state if state.current_query_text else None
        static_content = build_static_system_content(
            memory_state=memory_state,
            mid_term_summaries=state.mid_term_summaries,
            language=self.config.language,
        )
        inject_query = state.current_query_text if not state.query_in_current_chunk else None
        dynamic_content = build_dynamic_system_content(
            current_query_text=inject_query,
            memory_state=memory_state,
            include_qa_history=self.config.keep_qa_history,
            current_chunk_index=state.chunk_index,
            language=self.config.language,
        )
        prefix_content = "\n\n".join(
            part for part in (static_content, dynamic_content) if part
        )
        all_messages = list(state.current_chunk["messages"])

        if prefix_content:
            for idx, message in enumerate(all_messages):
                if message.get("role") != "user":
                    continue
                new_message = dict(message)
                content = message.get("content")
                if isinstance(content, list):
                    new_message["content"] = [
                        {"type": "text", "text": prefix_content}
                    ] + list(content)
                elif isinstance(content, str):
                    new_message["content"] = prefix_content + "\n\n" + content
                else:
                    new_message["content"] = prefix_content
                all_messages[idx] = new_message
                break

        return all_messages, prefix_content

    def _build_main_api_messages(self, state: SessionState) -> list[dict[str, Any]]:
        all_messages, _ = self._build_main_internal_messages(state)
        return [_internal_message_to_openai(message) for message in all_messages]

    def _build_cached_api_messages(
        self,
        state: SessionState,
        internal_messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        cache = state.current_chunk["api_msg_cache"]
        chunk_msgs = state.current_chunk["messages"]
        # Incrementally convert new chunk messages and append to cache.
        # cache[i] corresponds to chunk_msgs[i] (without prefix injection).
        while len(cache) < len(chunk_msgs):
            cache.append(_internal_message_to_openai(chunk_msgs[len(cache)]))
        # internal_messages[0] has prefix injected, so always re-convert it.
        # internal_messages[1:] are identical to chunk_msgs[1:], so reuse cache.
        first_msg = _internal_message_to_openai(internal_messages[0])
        remaining = cache[1:len(internal_messages)]
        return [first_msg] + remaining

    def _build_main_http_messages(
        self,
        api_messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        messages = list(api_messages)
        system_prompt = self.config.system_prompt
        if system_prompt:
            messages = [{"role": "system", "content": system_prompt}] + messages
        return messages

    async def _call_main_model(
        self,
        inbound_payload: dict[str, Any],
        api_messages: list[dict[str, Any]],
        *,
        client: Optional[AsyncOpenAI] = None,
        model_name: Optional[str] = None,
        session_state: Optional["SessionState"] = None,
        generation_kwargs: Optional[dict[str, Any]] = None,
        http_messages: Optional[list[dict[str, Any]]] = None,
    ) -> tuple[str, Optional[dict[str, Any]]]:
        client = client or self.main_client
        model_name = model_name or self.config.main_model
        generation_kwargs = generation_kwargs or self._main_generation_kwargs(inbound_payload)
        api_messages = http_messages or self._build_main_http_messages(api_messages)
        response = await client.chat.completions.create(
            model=model_name,
            messages=api_messages,
            **generation_kwargs,
        )
        raw_text = response.choices[0].message.content if response.choices else ""
        usage = response.usage.model_dump() if getattr(response, "usage", None) else None
        return raw_text or "", usage

    def _main_generation_kwargs(self, inbound_payload: dict[str, Any]) -> dict[str, Any]:
        extra_body = _extract_extra_body(inbound_payload)
        extra_body.setdefault("skip_special_tokens", False)
        extra_body.setdefault("greedy", False)
        if self.config.honor_inbound_generation_params:
            extra_body.setdefault("top_k", self.config.main_top_k)
            extra_body.setdefault("repetition_penalty", self.config.main_repetition_penalty)
            return {
                "max_tokens": inbound_payload.get("max_tokens", self.config.main_max_tokens),
                "temperature": inbound_payload.get("temperature", self.config.main_temperature),
                "top_p": inbound_payload.get("top_p", self.config.main_top_p),
                "presence_penalty": inbound_payload.get("presence_penalty", self.config.main_presence_penalty),
                "extra_body": extra_body,
            }

        extra_body["top_k"] = self.config.main_top_k
        extra_body["repetition_penalty"] = self.config.main_repetition_penalty
        return {
            "max_tokens": self.config.main_max_tokens,
            "temperature": self.config.main_temperature,
            "top_p": self.config.main_top_p,
            "presence_penalty": self.config.main_presence_penalty,
            "extra_body": extra_body,
        }

    async def _flush_chunk(self, state: SessionState, use_async_summary: bool = False) -> None:
        current_chunk = state.current_chunk
        if current_chunk["frame_count"] <= 0:
            return

        _file_to_data_url_cached.cache_clear()

        if self.summarizer is not None and use_async_summary:
            await self._commit_required_async_summaries(
                state, state.turn_count, non_blocking=False,
            )
        elif self.summarizer is not None:
            mid_term_entry, summary_time = await asyncio.to_thread(
                self._build_mid_term_summary_entry,
                state,
                copy.deepcopy(current_chunk),
            )
            state.mid_term_summaries.append(mid_term_entry)
            state.mid_term_history.append(mid_term_entry)
            LOGGER.info(
                "[%s] chunk=%d range=%s mid-summary %.3fs (%d/%d buffered)",
                state.session_id,
                mid_term_entry["chunk_index"],
                mid_term_entry["frame_range"],
                summary_time,
                len(state.mid_term_summaries),
                self.config.compress_every_n_chunks,
            )
            if len(state.mid_term_summaries) >= self.config.compress_every_n_chunks:
                await asyncio.to_thread(self._compress_mid_terms, state)

    def _build_mid_term_summary_entry(
        self,
        state: SessionState,
        chunk_snapshot: dict[str, Any],
        chunk_index: Optional[int] = None,
        current_query_text: Optional[str] = None,
        query_start_time: Optional[str] = None,
    ) -> tuple[dict[str, Any], float]:
        assert self.summarizer is not None
        resolved_chunk_index = chunk_index if chunk_index is not None else state.chunk_index
        resolved_query_text = current_query_text if current_query_text is not None else (state.current_query_text or "")
        resolved_query_start_time = query_start_time if query_start_time is not None else state.query_start_time
        frame_range = _compute_chunk_frame_range(chunk_snapshot)
        key_frames = self.summarizer.select_key_frames(
            chunk_snapshot["image_paths"],
            chunk_snapshot["frame_time_ranges"],
            _get_response_frame_indices(chunk_snapshot["messages"]),
            chunk_snapshot["summarizer_frame_cache"],
        )
        start = time.time()
        summary, mid_term_debug_input = self.summarizer.generate_detailed_summary(
            resolved_chunk_index,
            frame_range,
            key_frames,
            chunk_snapshot["frame_count"],
            resolved_query_text,
        )
        elapsed = time.time() - start
        debug_input_path = self._save_summarizer_debug_input(
            state,
            "mid_term",
            resolved_chunk_index,
            copy.deepcopy(mid_term_debug_input),
        )
        entry = {
            "chunk_index": resolved_chunk_index,
            "frame_range": frame_range,
            "query": resolved_query_text,
            "query_start_time": resolved_query_start_time,
            "summary_text": summary,
            "frame_count": chunk_snapshot["frame_count"],
            "key_frame_count": len(key_frames),
            "inference_time": round(elapsed, 3),
            "compressed_to_long_term": False,
        }
        if debug_input_path:
            entry["debug_input_path"] = debug_input_path
        return entry, elapsed

    def _compress_mid_terms(self, state: SessionState) -> None:
        assert self.summarizer is not None
        batch_index = state.long_term_compression_next_index
        state.long_term_compression_next_index += 1
        source_chunk_indices = [
            entry["chunk_index"] for entry in state.mid_term_summaries
        ]
        source_frame_ranges = [
            entry["frame_range"] for entry in state.mid_term_summaries
        ]
        start = time.time()
        merged, token_count, compressed_text, long_term_debug_input = self.summarizer.batch_compress_to_longterm(
            state.memory_state["long_term_memory"],
            state.mid_term_summaries,
        )
        elapsed = time.time() - start
        state.memory_state["long_term_memory"] = merged
        for entry in state.mid_term_summaries:
            entry["compressed_to_long_term"] = True
            entry["compressed_batch_index"] = batch_index

        long_term_entry = {
            "batch_index": batch_index,
            "query": state.current_query_text,
            "query_start_time": state.query_start_time,
            "source_chunk_indices": source_chunk_indices,
            "source_frame_ranges": source_frame_ranges,
            "source_summary_count": len(source_frame_ranges),
            "compressed_text": compressed_text,
            "inference_time": round(elapsed, 3),
            "token_count_after_append": token_count,
        }
        debug_input_path = self._save_summarizer_debug_input(
            state,
            "long_term",
            batch_index,
            copy.deepcopy(long_term_debug_input),
        )
        if debug_input_path:
            long_term_entry["debug_input_path"] = debug_input_path
        state.long_term_history.append(long_term_entry)

        window = int(self.config.long_term_memory_window or 0)
        if window > 0 and len(state.long_term_history) > window:
            dropped_count = len(state.long_term_history) - window
            del state.long_term_history[:dropped_count]
            state.memory_state["long_term_memory"] = "\n\n".join(
                entry["compressed_text"].rstrip()
                for entry in state.long_term_history
                if entry.get("compressed_text")
            )
            token_count = self.summarizer.estimate_tokens(
                state.memory_state["long_term_memory"]
            )
            long_term_entry["token_count_after_slide"] = token_count

        state.mid_term_summaries.clear()
        LOGGER.info(
            "[%s] long-term compression batch=%d %.3fs tokens=%d",
            state.session_id,
            batch_index,
            elapsed,
            token_count,
        )

    def _async_summary_enabled(self) -> bool:
        return (
            self.summarizer is not None
            and self.config.chunk > 0
            and int(self.config.async_summary_lead_frames or 0) > 0
        )

    def _async_first_summary_turns(self) -> int:
        lead_turns = max(0, int(self.config.async_summary_lead_frames or 0))
        chunk = max(1, int(self.config.chunk or 1))
        return max(1, chunk - lead_turns + 1) if lead_turns > 0 else chunk

    def _append_async_summary_user_message(
        self,
        state: SessionState,
        time_range=None,
        image_path=None,
        query_text=None,
        *,
        time_ranges=None,
        image_paths=None,
    ) -> None:
        if time_ranges is None:
            time_ranges = [time_range] if time_range else []
        if image_paths is None:
            image_paths = [image_path] if image_path else []
        segment = state.async_summary_segment
        for tr, ip in zip(time_ranges, image_paths):
            segment["image_paths"].append(ip)
            segment["frame_time_ranges"].append(tr)
            segment["summarizer_frame_cache"].append({"path": ip})
            segment["frame_count"] += 1
        segment["turn_count"] += 1
        segment["messages"].append(
            self._build_internal_user_message(
                time_ranges=time_ranges,
                image_paths=image_paths,
                query_text=query_text,
            )
        )

    def _submit_async_summary_if_needed(self, state: SessionState) -> None:
        if not self._async_summary_enabled():
            return

        if state.async_next_summary_target_turns <= 0:
            state.async_next_summary_target_turns = self._async_first_summary_turns()
        if state.async_summary_segment["turn_count"] < state.async_next_summary_target_turns:
            return

        segment_snapshot = copy.deepcopy(state.async_summary_segment)
        summary_index = state.async_next_summary_index
        required_turn_count = state.turn_count + max(
            0, int(self.config.async_summary_lead_frames or 0) - 1
        )
        task = asyncio.create_task(
            asyncio.to_thread(
                self._build_mid_term_summary_entry,
                state,
                segment_snapshot,
                summary_index,
                state.current_query_text or "",
                state.query_start_time,
            )
        )
        state.async_pending_summary_jobs.append(
            {
                "summary_index": summary_index,
                "submitted_turn_count": state.turn_count,
                "submitted_frame_count": state.frame_count,
                "required_turn_count": required_turn_count,
                "task": task,
            }
        )
        LOGGER.info(
            "[%s] submitted async mid-summary %d at turn=%d required_by_turn=%d",
            state.session_id,
            summary_index,
            state.turn_count,
            required_turn_count,
        )

        state.async_summary_segment = reset_chunk_state()
        state.async_next_summary_index += 1
        state.async_next_summary_target_turns = max(1, int(self.config.chunk or 1))

    async def _commit_required_async_summaries(
        self,
        state: SessionState,
        upto_turn_count: Optional[int] = None,
        wait_all: bool = False,
        non_blocking: bool = False,
    ) -> None:
        if not self._async_summary_enabled():
            return

        while state.async_pending_summary_jobs:
            job = state.async_pending_summary_jobs[0]
            is_required = wait_all or (
                upto_turn_count is not None
                and job["required_turn_count"] <= upto_turn_count
            )
            if not is_required:
                break

            if non_blocking and not job["task"].done():
                break

            wait_start = time.time()
            mid_term_entry, summary_time = await job["task"]
            wait_time = time.time() - wait_start
            mid_term_entry["async_summary"] = True
            mid_term_entry["turn_count"] = job.get("submitted_turn_count", 0)
            mid_term_entry["submitted_turn_count"] = job["submitted_turn_count"]
            mid_term_entry["submitted_frame_count"] = job["submitted_frame_count"]
            mid_term_entry["required_turn_count"] = job["required_turn_count"]
            mid_term_entry["barrier_wait_time"] = round(wait_time, 3)
            state.async_pending_summary_jobs.pop(0)

            state.mid_term_summaries.append(mid_term_entry)
            state.mid_term_history.append(mid_term_entry)
            LOGGER.info(
                "[%s] committed async mid-summary %d range=%s wait=%.3fs (%d/%d buffered)",
                state.session_id,
                mid_term_entry["chunk_index"],
                mid_term_entry["frame_range"],
                wait_time,
                len(state.mid_term_summaries),
                self.config.compress_every_n_chunks,
            )
            if len(state.mid_term_summaries) >= self.config.compress_every_n_chunks:
                await asyncio.to_thread(self._compress_mid_terms, state)


def _safe_session_id(session_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(session_id or "default"))
    return safe.strip("._")[:120] or "default"


def _request_session_id(request: web.Request, payload: dict[str, Any]) -> str:
    return (
        request.headers.get("x-streaming-session")
        or request.headers.get("x-session-id")
        or str(payload.get("user") or "")
        or "default"
    )


def _extract_time_range_from_request(
    request: web.Request,
    payload: dict[str, Any],
) -> Optional[str]:
    candidates = (
        request.headers.get("x-frame-time-range"),
        request.headers.get("x-streaming-time-range"),
        str(payload.get("x_frame_time_range") or ""),
        str(payload.get("frame_time_range") or ""),
    )
    for candidate in candidates:
        normalized = _normalize_time_range_text(candidate)
        if normalized:
            return normalized
    return None


async def _read_json(request: web.Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text=f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="JSON body must be an object")
    return payload


def _extract_first_image_ref(
    messages: list[dict[str, Any]],
    request: web.Request,
    payload: dict[str, Any],
) -> Optional[dict[str, str]]:
    local_path = _extract_local_image_path_from_request(request, payload)
    if local_path:
        return {"kind": "path", "value": local_path}

    for message in reversed(messages):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "image_url":
                continue
            image_url = item.get("image_url")
            if isinstance(image_url, dict):
                url = image_url.get("url")
            else:
                url = image_url
            if isinstance(url, str) and url:
                file_path = _file_url_to_path(url)
                if file_path:
                    return {"kind": "path", "value": file_path}
                return {"kind": "data_url", "value": url}
    return None


def _extract_all_image_refs(
    messages: list[dict[str, Any]],
    request: web.Request,
    payload: dict[str, Any],
) -> list[dict[str, str]]:
    """Extract all image references from the request (supports batch frames)."""
    refs: list[dict[str, str]] = []

    local_path = _extract_local_image_path_from_request(request, payload)
    if local_path:
        refs.append({"kind": "path", "value": local_path})

    for message in reversed(messages):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "image_url":
                continue
            image_url = item.get("image_url")
            if isinstance(image_url, dict):
                url = image_url.get("url")
            else:
                url = image_url
            if isinstance(url, str) and url:
                file_path = _file_url_to_path(url)
                if file_path:
                    refs.append({"kind": "path", "value": file_path})
                else:
                    refs.append({"kind": "data_url", "value": url})
        if refs:
            break
    return refs


def _extract_time_ranges_from_request(
    request: web.Request,
    payload: dict[str, Any],
) -> list[str]:
    """Extract multiple time ranges from request (for batch frames)."""
    ranges = payload.get("frame_time_ranges")
    if isinstance(ranges, list) and ranges:
        parsed: list[str] = []
        for r in ranges:
            normalized = _normalize_time_range_text(r)
            if normalized:
                parsed.append(normalized)
        if parsed:
            return parsed

    single = _extract_time_range_from_request(request, payload)
    return [single] if single else []


def _extract_local_image_path_from_request(
    request: web.Request,
    payload: dict[str, Any],
) -> Optional[str]:
    candidates = (
        request.headers.get("x-local-image-path"),
        request.headers.get("x-frame-image-path"),
        str(payload.get("x_local_image_path") or ""),
        str(payload.get("local_image_path") or ""),
        str(payload.get("frame_image_path") or ""),
    )
    for candidate in candidates:
        candidate = (candidate or "").strip()
        if candidate:
            return candidate
    return None


def _file_url_to_path(url: str) -> Optional[str]:
    if not url.startswith("file://"):
        return None
    from urllib.parse import unquote, urlparse

    parsed = urlparse(url)
    if parsed.netloc not in {"", "localhost"}:
        return None
    return unquote(parsed.path)


def _extract_user_prompt_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            text_parts = [
                str(item.get("text", "")).strip()
                for item in content
                if isinstance(item, dict)
                and item.get("type") == "text"
                and str(item.get("text", "")).strip()
            ]
            return "\n".join(text_parts).strip()
    return ""


def _extract_time_range_from_text(text: str) -> Optional[str]:
    return _normalize_time_range_text(text)


def _strip_time_range_from_text(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if TIME_RANGE_RE.fullmatch(stripped):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _format_seconds(value: float) -> str:
    if abs(value - round(value)) < 1e-6:
        return f"{int(round(value))}s"
    return f"{value:.3f}".rstrip("0").rstrip(".") + "s"


def _internal_message_to_openai(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get("content")
    if not isinstance(content, list):
        return {"role": message.get("role", "user"), "content": content or ""}

    converted: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "image":
            max_pixels = int(item.get("max_pixels") or 0)
            converted.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _file_to_data_url(item["image"], max_pixels=max_pixels)},
                }
            )
        elif item.get("type") == "text":
            converted.append({"type": "text", "text": str(item.get("text", ""))})
        else:
            converted.append(item)
    return {"role": message.get("role", "user"), "content": converted}


def _file_to_data_url(path: str, max_pixels: int = 0) -> str:
    if path.startswith("data:"):
        return _resize_data_url_if_needed(path, max_pixels)
    return _file_to_data_url_cached(path, max_pixels)


@functools.lru_cache(maxsize=128)
def _file_to_data_url_cached(path: str, max_pixels: int = 0) -> str:
    ext = Path(path).suffix.lower()
    mime_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
    }.get(ext, "image/jpeg")

    if max_pixels > 0:
        try:
            with Image.open(path) as image:
                image.load()
                resized = _resize_image_if_needed(image, max_pixels)
                if resized is not None:
                    return _image_to_data_url(resized, mime_type)
        except Exception as exc:
            LOGGER.warning("failed to resize image %s for max_pixels=%s: %s", path, max_pixels, exc)

    with open(path, "rb") as file_obj:
        encoded = base64.b64encode(file_obj.read()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _resize_data_url_if_needed(data_url: str, max_pixels: int = 0) -> str:
    if max_pixels <= 0:
        return data_url
    match = re.match(r"^data:(image/[^;]+);base64,(.+)$", data_url, re.DOTALL)
    if not match:
        return data_url
    mime_type, encoded = match.groups()
    try:
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
            image.load()
            resized = _resize_image_if_needed(image, max_pixels)
            if resized is None:
                return data_url
            return _image_to_data_url(resized, mime_type)
    except Exception as exc:
        LOGGER.warning("failed to resize data URL image for max_pixels=%s: %s", max_pixels, exc)
        return data_url


def _resize_image_if_needed(image: Image.Image, max_pixels: int) -> Optional[Image.Image]:
    width, height = image.size
    if max_pixels <= 0 or width * height <= max_pixels:
        return None
    scale = (max_pixels / (width * height)) ** 0.5
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return image.resize(new_size, Image.LANCZOS)


def _image_to_data_url(image: Image.Image, mime_type: str) -> str:
    buffer = io.BytesIO()
    if mime_type == "image/png":
        image.save(buffer, format="PNG")
    elif mime_type == "image/webp":
        image.save(buffer, format="WEBP")
    else:
        mime_type = "image/jpeg"
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.save(buffer, format="JPEG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _extract_extra_body(payload: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "skip_special_tokens",
        "top_k",
        "repetition_penalty",
        "min_p",
        "stop_token_ids",
        "include_stop_str_in_output",
    )
    return {key: payload[key] for key in keys if key in payload}


def _chat_completion_response(
    model: str,
    content: str,
    usage: Optional[dict[str, Any]],
    raw_model: str,
    raw_text: str,
) -> dict[str, Any]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    response = {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": usage or {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }
    response["streamingharness"] = {
        "main_model": raw_model,
        "raw_content": raw_text,
    }
    return response


def _openai_error_response(message: str, status: int) -> web.Response:
    return web.json_response(
        {
            "error": {
                "message": message,
                "type": "streaming_infer_adapter_error",
                "param": None,
                "code": None,
            }
        },
        status=status,
    )


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return int(value)


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return float(value)


def _split_paths(value: str) -> tuple[str, ...]:
    if not value:
        return ()
    normalized = value.replace(",", os.pathsep)
    return tuple(item.strip() for item in normalized.split(os.pathsep) if item.strip())


def parse_args() -> AdapterConfig:
    parser = argparse.ArgumentParser(description="StreamingHarness live OpenAI adapter")
    parser.add_argument("--host", default=os.environ.get("ADAPTER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=_env_int("ADAPTER_PORT", 8070))
    parser.add_argument(
        "--adapter-model",
        default=os.environ.get("ADAPTER_MODEL", "streaming-infer-adapter"),
    )
    parser.add_argument(
        "--main-api-base",
        default=os.environ.get("MAIN_API_BASE", "http://127.0.0.1:7060/v1"),
    )
    parser.add_argument(
        "--main-model",
        default=os.environ.get("MAIN_MODEL", "streamingharness-8b"),
    )
    parser.add_argument(
        "--main-backends",
        default=os.environ.get("MAIN_BACKENDS", ""),
        help='JSON array of backends: [{"name":"...","api_base":"...","model":"..."},...]',
    )
    parser.add_argument("--api-key", default=os.environ.get("MODEL_API_KEY", "EMPTY"))
    parser.add_argument("--frame-seconds", type=float, default=_env_float("FRAME_SECONDS", 1.0))
    parser.add_argument("--max-pixels", type=int, default=_env_int("MAX_PIXELS", 262144))
    parser.add_argument("--main-max-tokens", type=int, default=_env_int("MAIN_MAX_TOKENS", 128))
    parser.add_argument(
        "--main-temperature",
        type=float,
        default=_env_float("MAIN_TEMPERATURE", 0.8),
    )
    parser.add_argument("--main-top-p", type=float, default=_env_float("MAIN_TOP_P", 0.9))
    parser.add_argument("--main-top-k", type=int, default=_env_int("MAIN_TOP_K", 40))
    parser.add_argument(
        "--main-repetition-penalty",
        type=float,
        default=_env_float("MAIN_REPETITION_PENALTY", 1.0),
    )
    parser.add_argument(
        "--main-presence-penalty",
        type=float,
        default=_env_float("MAIN_PRESENCE_PENALTY", 0.0),
    )
    parser.add_argument(
        "--honor-inbound-generation-params",
        action="store_true",
        default=_env_bool("HONOR_INBOUND_GENERATION_PARAMS", False),
        help="Use max_tokens/temperature/top_p from incoming WebUI requests instead of infer.sh-style defaults.",
    )
    parser.add_argument("--chunk", type=int, default=_env_int("CHUNK", 200))
    parser.add_argument(
        "--compress-every-n-chunks",
        type=int,
        default=_env_int("COMPRESS_EVERY_N_CHUNKS", 5),
    )
    parser.add_argument(
        "--async-summary-lead-frames",
        type=int,
        default=_env_int("ASYNC_SUMMARY_LEAD_FRAMES", 10),
        help="Generate async summaries this many turns before the chunk boundary. Name kept for compatibility.",
    )
    parser.add_argument(
        "--no-prompt-as-query",
        action="store_true",
        default=not _env_bool("USE_PROMPT_AS_QUERY", True),
    )
    parser.add_argument(
        "--force-silence-before-query",
        action="store_true",
        default=_env_bool("FORCE_SILENCE_BEFORE_QUERY", True),
    )
    parser.add_argument(
        "--no-force-silence-before-query",
        action="store_false",
        dest="force_silence_before_query",
        help="Disable infer.sh-style forced </silence> before the first query.",
    )
    parser.add_argument(
        "--no-qa-history",
        action="store_true",
        default=not _env_bool("KEEP_QA_HISTORY", True),
    )
    parser.add_argument(
        "--no-normalize-output",
        action="store_true",
        default=not _env_bool("NORMALIZE_OUTPUT", True),
    )
    parser.add_argument(
        "--disable-summarizer",
        action="store_true",
        default=not _env_bool("ENABLE_SUMMARIZER", True),
    )
    parser.add_argument(
        "--summarizer-model",
        default=os.environ.get(
            "SUMMARIZER_MODEL",
            "/tmp/models/Qwen3-VL-4B-Instruct",
        ),
    )
    parser.add_argument(
        "--summarizer-api-base",
        default=os.environ.get("SUMMARIZER_API_BASE", "http://127.0.0.1:8065/v1"),
    )
    parser.add_argument(
        "--longterm-model",
        default=os.environ.get(
            "LONGTERM_SUMMARIZER_MODEL",
            os.environ.get("SUMMARIZER_MODEL", "/tmp/models/Qwen3-VL-4B-Instruct"),
        ),
    )
    parser.add_argument(
        "--longterm-api-base",
        default=os.environ.get(
            "LONGTERM_SUMMARIZER_API_BASE",
            os.environ.get("SUMMARIZER_API_BASE", "http://127.0.0.1:8065/v1"),
        ),
    )
    parser.add_argument(
        "--summarizer-max-pixels",
        type=int,
        default=_env_int("SUMMARIZER_MAX_PIXELS", 262144),
    )
    parser.add_argument(
        "--summarizer-key-frames",
        type=int,
        default=_env_int("SUMMARIZER_KEY_FRAMES", 0),
    )
    parser.add_argument(
        "--summarizer-phase-seconds",
        type=float,
        default=_env_float("SUMMARIZER_PHASE_SECONDS", 10.0),
    )
    parser.add_argument(
        "--mid-term-max-tokens",
        type=int,
        default=_env_int("MID_TERM_MAX_TOKENS", 4000),
    )
    parser.add_argument(
        "--mid-term-target-tokens",
        type=int,
        default=_env_int("MID_TERM_TARGET_TOKEN_COUNT", 3000),
    )
    parser.add_argument(
        "--long-term-max-tokens",
        type=int,
        default=_env_int("LONG_TERM_MAX_TOKENS", 2000),
    )
    parser.add_argument(
        "--long-term-target-tokens",
        type=int,
        default=_env_int("LONG_TERM_TARGET_TOKEN_COUNT", 1000),
    )
    parser.add_argument(
        "--mid-term-temperature",
        type=float,
        default=_env_float("MID_TERM_TEMPERATURE", 0.8),
    )
    parser.add_argument(
        "--mid-term-top-p",
        type=float,
        default=_env_float("MID_TERM_TOP_P", 0.9),
    )
    parser.add_argument(
        "--mid-term-top-k",
        type=int,
        default=_env_int("MID_TERM_TOP_K", 40),
    )
    parser.add_argument(
        "--mid-term-repetition-penalty",
        type=float,
        default=_env_float("MID_TERM_REPETITION_PENALTY", 1.0),
    )
    parser.add_argument(
        "--mid-term-presence-penalty",
        type=float,
        default=_env_float("MID_TERM_PRESENCE_PENALTY", 0.0),
    )
    parser.add_argument(
        "--long-term-temperature",
        type=float,
        default=_env_float("LONG_TERM_TEMPERATURE", 1.0),
    )
    parser.add_argument(
        "--long-term-top-p",
        type=float,
        default=_env_float("LONG_TERM_TOP_P", 1.0),
    )
    parser.add_argument(
        "--long-term-top-k",
        type=int,
        default=_env_int("LONG_TERM_TOP_K", 80),
    )
    parser.add_argument(
        "--long-term-repetition-penalty",
        type=float,
        default=_env_float("LONG_TERM_REPETITION_PENALTY", 1.0),
    )
    parser.add_argument(
        "--long-term-presence-penalty",
        type=float,
        default=_env_float("LONG_TERM_PRESENCE_PENALTY", 0.0),
    )
    parser.add_argument(
        "--long-term-memory-window",
        type=int,
        default=_env_int("LONG_TERM_MEMORY_WINDOW", 40),
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=_env_float("REQUEST_TIMEOUT_SECONDS", 300.0),
    )
    parser.add_argument(
        "--allowed-local-image-roots",
        default=os.environ.get("ALLOWED_LOCAL_IMAGE_ROOTS", ""),
        help="Comma- or colon-separated directories whose image files may be referenced directly by requests.",
    )
    parser.add_argument(
        "--frame-save-dir",
        default=os.environ.get("FRAME_SAVE_DIR", "/tmp/streaming_adapter_frames"),
        help="Directory to save base64 frames received from WebUI.",
    )
    parser.add_argument(
        "--save-root",
        default=os.environ.get(
            "LIVE_ADAPTER_SAVE_ROOT",
            os.environ.get("SAVE_ROOT", DEFAULT_SAVE_ROOT),
        ),
        help="Root used for auto-generated output_*, output_light_*, and input_* dirs.",
    )
    parser.add_argument(
        "--run-timestamp",
        default=os.environ.get("LIVE_ADAPTER_RUN_TIMESTAMP", ""),
        help="Timestamp suffix for auto-generated live save dirs.",
    )
    parser.add_argument(
        "--output-model-name",
        default=os.environ.get("LIVE_ADAPTER_OUTPUT_MODEL_NAME", ""),
        help="Model-name suffix for auto-generated live save dirs.",
    )
    parser.add_argument(
        "--out-dir",
        default=os.environ.get("LIVE_ADAPTER_OUT_DIR") or os.environ.get("OUT_DIR"),
        help="Full live output directory. Relative paths resolve under save-root.",
    )
    parser.add_argument(
        "--light-out-dir",
        default=os.environ.get("LIVE_ADAPTER_LIGHT_OUT_DIR")
        or os.environ.get("LIGHT_OUT_DIR"),
        help="Light live output directory. Defaults to output_light_* derived from out-dir.",
    )
    parser.add_argument(
        "--debug-input-dir",
        default=os.environ.get("LIVE_ADAPTER_DEBUG_INPUT_DIR")
        or os.environ.get("DEBUG_INPUT_DIR"),
        help="Directory for live input_* debug snapshots. Relative paths resolve under save-root.",
    )
    parser.add_argument(
        "--no-live-save",
        action="store_true",
        default=not _env_bool("LIVE_SAVE_OUTPUTS", False),
        help="Disable live output_*/output_light_* writing.",
    )
    parser.add_argument(
        "--no-debug-inputs",
        action="store_true",
        default=not _env_bool("LIVE_SAVE_DEBUG_INPUTS", False),
        help="Disable live input_* debug snapshot writing.",
    )
    parser.add_argument(
        "--no-save-model-inputs",
        action="store_true",
        default=not _env_bool("SAVE_MODEL_INPUTS", True),
        help="Do not embed per-turn model_input records in output_*.",
    )
    parser.add_argument(
        "--no-summarizer-debug",
        action="store_true",
        default=not _env_bool("SUMMARIZER_DEBUG", True),
        help="Do not keep/save mid/long-term summary debug inputs.",
    )
    parser.add_argument(
        "--system-prompt",
        default=os.environ.get("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT_EN),
        help="System prompt prepended to every 8B main-model request. Set SYSTEM_PROMPT='' to disable the built-in default.",
    )
    parser.add_argument(
        "--language",
        default=os.environ.get("ADAPTER_LANGUAGE", "en"),
        choices=["zh", "en"],
        help="Language for context injection text (Video History header, Q&A History header, User Query header). 'zh' for Chinese, 'en' for English.",
    )
    args = parser.parse_args()

    raw_save_root = (args.save_root or DEFAULT_SAVE_ROOT).strip() or DEFAULT_SAVE_ROOT
    save_root = os.path.normpath(os.path.expanduser(raw_save_root))
    run_timestamp = args.run_timestamp.strip() or datetime.now().strftime("%Y%m%d_%H%M%S")
    output_model_name = (
        sanitize_output_name(args.output_model_name)
        if args.output_model_name.strip()
        else derive_model_output_name(args.main_model)
    )
    no_live_save = args.no_live_save
    no_debug_inputs = args.no_debug_inputs
    per_session_dirs = not no_live_save
    explicit_out_dir = resolve_save_dir(args.out_dir, save_root)
    explicit_light_out_dir = resolve_save_dir(args.light_out_dir, save_root)
    explicit_debug_input_dir = resolve_save_dir(args.debug_input_dir, save_root)
    if explicit_out_dir or explicit_light_out_dir or explicit_debug_input_dir:
        per_session_dirs = False

    if no_live_save:
        out_dir = None
        light_out_dir = None
    elif per_session_dirs:
        out_dir = None
        light_out_dir = None
    else:
        out_dir = explicit_out_dir
        if out_dir is None:
            out_dir = os.path.join(
                save_root,
                f"output_{run_timestamp}_{output_model_name}",
            )
        light_out_dir = explicit_light_out_dir
        if light_out_dir is None:
            light_out_dir = derive_light_out_dir(out_dir)

    if no_debug_inputs:
        debug_input_dir = None
    elif per_session_dirs:
        debug_input_dir = None
    else:
        debug_input_dir = explicit_debug_input_dir
        if debug_input_dir is None:
            debug_input_dir = os.path.join(
                save_root,
                f"input_{run_timestamp}_{output_model_name}",
            )

    return AdapterConfig(
        host=args.host,
        port=args.port,
        adapter_model=args.adapter_model,
        main_api_base=args.main_api_base,
        main_model=args.main_model,
        main_backends=tuple(json.loads(args.main_backends)) if args.main_backends else (),
        api_key=args.api_key,
        allowed_local_image_roots=_split_paths(args.allowed_local_image_roots),
        frame_seconds=args.frame_seconds,
        max_pixels=args.max_pixels,
        main_max_tokens=args.main_max_tokens,
        main_temperature=args.main_temperature,
        main_top_p=args.main_top_p,
        main_top_k=args.main_top_k,
        main_repetition_penalty=args.main_repetition_penalty,
        main_presence_penalty=args.main_presence_penalty,
        honor_inbound_generation_params=args.honor_inbound_generation_params,
        chunk=args.chunk,
        compress_every_n_chunks=args.compress_every_n_chunks,
        async_summary_lead_frames=args.async_summary_lead_frames,
        use_prompt_as_query=not args.no_prompt_as_query,
        force_silence_before_query=args.force_silence_before_query,
        keep_qa_history=not args.no_qa_history,
        normalize_output=not args.no_normalize_output,
        enable_summarizer=not args.disable_summarizer,
        summarizer_model=args.summarizer_model,
        summarizer_api_base=args.summarizer_api_base,
        longterm_model=args.longterm_model,
        longterm_api_base=args.longterm_api_base,
        summarizer_max_pixels=args.summarizer_max_pixels,
        summarizer_key_frames=args.summarizer_key_frames,
        summarizer_phase_seconds=args.summarizer_phase_seconds,
        mid_term_max_tokens=args.mid_term_max_tokens,
        mid_term_target_tokens=args.mid_term_target_tokens,
        long_term_max_tokens=args.long_term_max_tokens,
        long_term_target_tokens=args.long_term_target_tokens,
        mid_term_temperature=args.mid_term_temperature,
        mid_term_top_p=args.mid_term_top_p,
        mid_term_top_k=args.mid_term_top_k,
        mid_term_repetition_penalty=args.mid_term_repetition_penalty,
        mid_term_presence_penalty=args.mid_term_presence_penalty,
        long_term_temperature=args.long_term_temperature,
        long_term_top_p=args.long_term_top_p,
        long_term_top_k=args.long_term_top_k,
        long_term_repetition_penalty=args.long_term_repetition_penalty,
        long_term_presence_penalty=args.long_term_presence_penalty,
        long_term_memory_window=args.long_term_memory_window,
        request_timeout_seconds=args.request_timeout_seconds,
        out_dir=out_dir,
        light_out_dir=light_out_dir,
        debug_input_dir=debug_input_dir,
        save_root=save_root if per_session_dirs else None,
        output_model_name=output_model_name,
        per_session_dirs=per_session_dirs,
        save_model_inputs=not args.no_save_model_inputs,
        save_debug_inputs=not no_debug_inputs,
        summarizer_debug=not args.no_summarizer_debug,
        frame_save_dir=args.frame_save_dir,
        language=args.language,
        system_prompt=args.system_prompt,
    )


def create_app(config: AdapterConfig) -> web.Application:
    adapter = StreamingInferAdapter(config)
    app = web.Application(client_max_size=128 * 1024 * 1024)
    app["adapter"] = adapter
    async def _on_startup(_app: web.Application) -> None:
        adapter.start_background_tasks()
    app.on_startup.append(_on_startup)
    app.router.add_get("/health", adapter.handle_health)
    app.router.add_get("/v1/models", adapter.handle_models)
    app.router.add_post("/v1/chat/completions", adapter.handle_chat_completions)
    app.router.add_post("/v1/streaming/reset", adapter.handle_reset)
    return app


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = parse_args()
    LOGGER.info("Starting adapter on %s:%s", config.host, config.port)
    LOGGER.info("Adapter model: %s", config.adapter_model)
    if config.main_backends:
        LOGGER.info("Multi-backend mode: %d backends", len(config.main_backends))
        for b in config.main_backends:
            LOGGER.info("  Backend: %s -> %s (model=%s)", b["name"], b["api_base"], b.get("model", b["name"]))
    else:
        LOGGER.info("Main model: %s at %s", config.main_model, config.main_api_base)
    if config.per_session_dirs:
        LOGGER.info("Live save mode: per-session directories under %s", config.save_root)
    else:
        LOGGER.info("Live output dir: %s", config.out_dir or "disabled")
        LOGGER.info("Live light output dir: %s", config.light_out_dir or "disabled")
        LOGGER.info("Live debug input dir: %s", config.debug_input_dir or "disabled")
    if config.enable_summarizer:
        LOGGER.info(
            "Summarizer APIs: mid=%s long=%s",
            config.summarizer_api_base,
            config.longterm_api_base,
        )
    else:
        LOGGER.info("Summarizer disabled")
    web.run_app(create_app(config), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
