# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
VLM Service
Handles async image analysis using any OpenAI-compatible VLM API
(Works with vLLM, SGLang, Ollama, OpenAI, etc.)
"""

import asyncio
import base64
import io
import json
import math
import re
import time
from openai import AsyncOpenAI
from PIL import Image
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def _format_seconds_words(value: float) -> str:
    rounded = math.floor(value * 10 + 0.5) / 10
    return f"{rounded:.1f} seconds"


class VLMService:
    """Service for analyzing images using VLM via OpenAI-compatible API"""

    def __init__(
        self,
        model: str,
        api_base: str = "http://localhost:8000/v1",
        api_key: str = "EMPTY",
        prompt: Optional[str] = None,
        max_tokens: int = 512,
        session_id: str = "default",
    ):
        self.model = model
        self.api_base = api_base
        self.api_key = api_key if api_key else "EMPTY"
        self.prompt = prompt
        self.max_tokens = max_tokens
        self.session_id = session_id
        self.client = AsyncOpenAI(base_url=api_base, api_key=api_key)
        self.current_response = "Initializing..."
        self.is_processing = False
        self._processing_lock = asyncio.Lock()
        self._active_tasks = set()
        self._closed = False
        self._last_request_payload = None
        self._last_response_payload = None
        self.last_latency_breakdown_ms = {}
        self.last_frame_timing_ms = {}
        self.last_user_prompt = ""
        self._pending_background_handoff: Optional[dict] = None
        self._last_background_handoff_meta: Optional[dict] = None
        self._timestamp_turn_count = 0

        # Metrics tracking
        self.last_inference_time = 0.0  # seconds
        self.total_inferences = 0
        self.total_inference_time = 0.0

    def _format_frame_time_range(self, frame_metadata: Optional[dict]) -> str:
        if not frame_metadata:
            return ""

        timestamp = frame_metadata.get("timestamp")
        if timestamp is None:
            return ""

        try:
            timestamp_value = float(timestamp)
        except (TypeError, ValueError):
            return str(timestamp)

        return _format_seconds_words(timestamp_value)

    def _normalize_timestamp_interval(self, interval_seconds) -> float:
        try:
            interval = float(interval_seconds)
        except (TypeError, ValueError):
            interval = 1.0
        return max(0.0, interval)

    def _next_turn_timestamp_metadata(self, interval_seconds) -> dict:
        interval = self._normalize_timestamp_interval(interval_seconds)
        turn_index = self._timestamp_turn_count
        self._timestamp_turn_count += 1
        return {
            "timestamp": turn_index * interval,
            "timestamp_kind": "turn_seconds",
            "timestamp_turn": turn_index + 1,
            "timestamp_interval_seconds": interval,
        }

    def _apply_turn_timestamp(self, metadata: Optional[dict], turn_metadata: dict) -> dict:
        result = dict(metadata or {})
        if "timestamp" in result:
            result.setdefault("source_timestamp", result.get("timestamp"))
        if "timestamp_kind" in result:
            result.setdefault("source_timestamp_kind", result.get("timestamp_kind"))
        result.update(turn_metadata)
        return result

    def _metadata_with_next_turn_timestamp(self, metadata: Optional[dict]) -> dict:
        source = dict(metadata or {})
        turn_metadata = self._next_turn_timestamp_metadata(
            source.get("timestamp_interval_seconds")
        )
        return self._apply_turn_timestamp(source, turn_metadata)

    def _batch_with_next_turn_timestamp(self, frames_data: list) -> list:
        if not frames_data:
            return []
        first_frame = frames_data[0] if isinstance(frames_data[0], dict) else {}
        turn_metadata = self._next_turn_timestamp_metadata(
            first_frame.get("timestamp_interval_seconds")
        )
        return [
            self._apply_turn_timestamp(frame_data, turn_metadata)
            for frame_data in frames_data
        ]

    async def analyze_image(
        self,
        image: Image.Image,
        prompt: Optional[str] = None,
        frame_metadata: Optional[dict] = None,
    ) -> str:
        if prompt is None:
            prompt = self.prompt
        prompt_text = (prompt or "").strip()
        frame_time_range = self._format_frame_time_range(frame_metadata)

        try:
            start_time = time.perf_counter()

            # Convert PIL Image to base64
            jpeg_start = time.perf_counter()
            img_byte_arr = io.BytesIO()
            image.save(img_byte_arr, format="JPEG")
            img_byte_arr = img_byte_arr.getvalue()
            jpeg_end = time.perf_counter()

            base64_start = time.perf_counter()
            img_base64 = base64.b64encode(img_byte_arr).decode("utf-8")
            base64_end = time.perf_counter()

            request_build_start = time.perf_counter()
            image_url = f"data:image/jpeg;base64,{img_base64}"
            content = []
            if prompt_text:
                content.append({"type": "text", "text": prompt_text})
            content.append({"type": "image_url", "image_url": {"url": image_url}})
            messages = [
                {
                    "role": "user",
                    "content": content,
                }
            ]

            # Store request payload for debug (truncate base64 for display)
            truncate_len = 120
            if len(img_base64) > truncate_len:
                image_url_debug = f"data:image/jpeg;base64,{img_base64[:truncate_len]}...<{len(img_base64)} chars total>"
            else:
                image_url_debug = image_url
            debug_content = []
            if prompt_text:
                debug_content.append({"type": "text", "text": prompt_text})
            debug_content.append({"type": "image_url", "image_url": {"url": image_url_debug}})
            self._last_request_payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": debug_content,
                    }
                ],
                "max_tokens": self.max_tokens,
                "temperature": 0.7,
            }
            if frame_time_range:
                self._last_request_payload["frame_time_range"] = frame_time_range
            request_build_end = time.perf_counter()

            # Call API
            api_start = time.perf_counter()
            create_kwargs = {
                "model": self.model,
                "messages": messages,
                "max_tokens": self.max_tokens,
                "temperature": 0.7,
                "extra_headers": {"x-streaming-session": self.session_id},
            }
            if frame_time_range:
                create_kwargs["extra_body"] = {"frame_time_range": frame_time_range}
            response = await self.client.chat.completions.create(
                **create_kwargs,
            )
            api_end = time.perf_counter()

            # Store response payload for debug
            response_payload_start = time.perf_counter()
            try:
                self._last_response_payload = (
                    response.model_dump() if hasattr(response, "model_dump") else response.dict()
                )
            except Exception:
                self._last_response_payload = {
                    "id": getattr(response, "id", None),
                    "model": getattr(response, "model", None),
                    "choices": [
                        {
                            "index": getattr(c, "index", i),
                            "message": {
                                "role": getattr(getattr(c, "message", None), "role", None),
                                "content": getattr(getattr(c, "message", None), "content", None),
                            },
                            "finish_reason": getattr(c, "finish_reason", None),
                        }
                        for i, c in enumerate(getattr(response, "choices", []))
                    ],
                    "usage": getattr(response, "usage", None),
                }
            response_payload_end = time.perf_counter()

            # Calculate latency
            response_extract_start = time.perf_counter()
            result = self._extract_response_text(response)
            end_time = time.perf_counter()
            inference_time = end_time - start_time

            # Update metrics
            self.last_inference_time = inference_time
            self.total_inferences += 1
            self.total_inference_time += inference_time
            self.last_latency_breakdown_ms = {
                "jpeg_encode_ms": (jpeg_end - jpeg_start) * 1000,
                "base64_encode_ms": (base64_end - base64_start) * 1000,
                "request_build_ms": (request_build_end - request_build_start) * 1000,
                "api_call_ms": (api_end - api_start) * 1000,
                "response_payload_ms": (response_payload_end - response_payload_start) * 1000,
                "response_extract_ms": (end_time - response_extract_start) * 1000,
                "total_ms": inference_time * 1000,
            }
            logger.info(f"VLM response: {result} (latency: {inference_time*1000:.0f}ms)")
            return result

        except Exception as e:
            logger.error(f"Error analyzing image: {e}")
            return f"Error: {str(e)}"

    def get_last_request_payload(self) -> Optional[dict]:
        return self._last_request_payload

    def get_last_response_payload(self) -> Optional[dict]:
        return self._last_response_payload

    def _track_current_task(self):
        task = asyncio.current_task()
        if task is not None:
            self._active_tasks.add(task)
        return task

    def _untrack_task(self, task) -> None:
        if task is not None:
            self._active_tasks.discard(task)

    async def cancel_active_requests(self, timeout: float = 2.0) -> int:
        """Cancel in-flight VLM processing tasks for this session."""
        current_task = asyncio.current_task()
        tasks = [
            task
            for task in self._active_tasks
            if task is not current_task and not task.done()
        ]
        if not tasks:
            self.is_processing = False
            return 0

        for task in tasks:
            task.cancel()

        done, pending = await asyncio.wait(tasks, timeout=timeout)
        if done:
            await asyncio.gather(*done, return_exceptions=True)
        for task in pending:
            logger.warning(
                "Timed out waiting for VLM task cancellation for session %s",
                self.session_id,
            )

        self.is_processing = False
        logger.info("Cancelled %s VLM task(s) for session %s", len(tasks), self.session_id)
        return len(tasks)

    def clear_state(self) -> None:
        self.current_response = "Initializing..."
        self.is_processing = False
        self.prompt = None
        self._last_request_payload = None
        self._last_response_payload = None
        self.last_latency_breakdown_ms = {}
        self.last_frame_timing_ms = {}
        self.last_user_prompt = ""
        self._pending_background_handoff = None
        self._last_background_handoff_meta = None
        self._timestamp_turn_count = 0
        self.last_inference_time = 0.0
        self.total_inferences = 0
        self.total_inference_time = 0.0

    def queue_background_handoff(
        self,
        *,
        task_id: str,
        question: str,
        summary: str,
    ) -> None:
        """Record background completion metadata for UI/metrics without prompt injection."""
        compact_summary = self._compact_text(summary, 3500)
        self._pending_background_handoff = {
            "task_id": str(task_id or ""),
            "question": str(question or ""),
            "summary": compact_summary,
        }
        logger.info(
            "Recorded background handoff metadata for interaction session %s: task_id=%s summary_chars=%s",
            self.session_id,
            task_id,
            len(compact_summary),
        )

    def _resolve_prompt_for_inference(self, explicit_prompt: Optional[str], captured_prompt: Optional[str]) -> tuple[Optional[str], bool]:
        prompt_text = explicit_prompt if explicit_prompt is not None else captured_prompt
        handoff = self._pending_background_handoff
        if handoff and handoff.get("summary"):
            self._pending_background_handoff = None
            self._last_background_handoff_meta = {
                "task_id": str(handoff.get("task_id") or ""),
                "question": str(handoff.get("question") or ""),
                "summary": str(handoff.get("summary") or ""),
            }
            return prompt_text, True
        if explicit_prompt is not None:
            return explicit_prompt, False
        if captured_prompt:
            return captured_prompt, False
        return None, False

    def _compact_text(self, text, limit: int) -> str:
        value = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(value) <= limit:
            return value
        return value[: max(0, limit - 12)].rstrip() + " ...[截断]"

    async def close(self, cancel_requests: bool = True) -> None:
        self._closed = True
        if cancel_requests:
            await self.cancel_active_requests()
        self.clear_state()

        close = getattr(self.client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result

    async def process_frame(
        self,
        image: Image.Image,
        prompt: Optional[str] = None,
        frame_timing_ms: Optional[dict] = None,
        frame_metadata: Optional[dict] = None,
    ) -> None:
        if self._closed:
            return

        task = self._track_current_task()
        try:
            if self._processing_lock.locked():
                logger.debug("VLM busy, skipping frame")
                return

            async with self._processing_lock:
                if self._closed:
                    return
                self.is_processing = True
                try:
                    self.last_frame_timing_ms = frame_timing_ms or {}
                    captured_prompt = self.prompt
                    used_prompt, consumed_background_handoff = self._resolve_prompt_for_inference(
                        prompt,
                        captured_prompt,
                    )
                    self.last_user_prompt = str(used_prompt or "").strip()
                    request_frame_metadata = self._metadata_with_next_turn_timestamp(
                        frame_metadata
                    )
                    response = await self.analyze_image(
                        image,
                        used_prompt,
                        frame_metadata=request_frame_metadata,
                    )
                    if self._closed:
                        return
                    self.current_response = response
                    if captured_prompt and self.prompt == captured_prompt:
                        self.prompt = None
                    if consumed_background_handoff:
                        logger.info("Consumed background handoff metadata for session %s", self.session_id)
                finally:
                    self.is_processing = False
        finally:
            self._untrack_task(task)

    async def analyze_images(
        self,
        frames_data: list,
        prompt: Optional[str] = None,
    ) -> str:
        """Analyze multiple images in a single API call (batch mode)."""
        if prompt is None:
            prompt = self.prompt
        prompt_text = (prompt or "").strip()

        try:
            start_time = time.perf_counter()

            content: list[dict] = []
            debug_content: list[dict] = []
            if prompt_text:
                content.append({"type": "text", "text": prompt_text})
                debug_content.append({"type": "text", "text": prompt_text})

            frame_time_ranges: list[str] = []
            for frame_data in frames_data:
                image = frame_data["image"]
                frame_metadata = {
                    "timestamp": frame_data.get("timestamp"),
                    "timestamp_kind": frame_data.get("timestamp_kind"),
                }

                img_byte_arr = io.BytesIO()
                image.save(img_byte_arr, format="JPEG")
                img_bytes = img_byte_arr.getvalue()
                img_base64 = base64.b64encode(img_bytes).decode("utf-8")
                image_url = f"data:image/jpeg;base64,{img_base64}"

                content.append({"type": "image_url", "image_url": {"url": image_url}})

                truncate_len = 120
                if len(img_base64) > truncate_len:
                    debug_url = f"data:image/jpeg;base64,{img_base64[:truncate_len]}...<{len(img_base64)} chars>"
                else:
                    debug_url = image_url
                debug_content.append({"type": "image_url", "image_url": {"url": debug_url}})

                ftr = self._format_frame_time_range(frame_metadata)
                if ftr:
                    frame_time_ranges.append(ftr)

            messages = [{"role": "user", "content": content}]

            self._last_request_payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": debug_content}],
                "max_tokens": self.max_tokens,
                "temperature": 0.7,
                "frame_time_ranges": frame_time_ranges,
            }

            api_start = time.perf_counter()
            create_kwargs = {
                "model": self.model,
                "messages": messages,
                "max_tokens": self.max_tokens,
                "temperature": 0.7,
                "extra_headers": {"x-streaming-session": self.session_id},
            }
            if frame_time_ranges:
                create_kwargs["extra_body"] = {"frame_time_ranges": frame_time_ranges}
            response = await self.client.chat.completions.create(**create_kwargs)
            api_end = time.perf_counter()

            try:
                self._last_response_payload = (
                    response.model_dump() if hasattr(response, "model_dump") else response.dict()
                )
            except Exception:
                self._last_response_payload = None

            result = self._extract_response_text(response)
            end_time = time.perf_counter()
            inference_time = end_time - start_time

            self.last_inference_time = inference_time
            self.total_inferences += 1
            self.total_inference_time += inference_time
            self.last_latency_breakdown_ms = {
                "api_call_ms": (api_end - api_start) * 1000,
                "total_ms": inference_time * 1000,
            }
            logger.info(
                f"VLM batch response ({len(frames_data)} frames): {result} "
                f"(latency: {inference_time*1000:.0f}ms)"
            )
            return result

        except Exception as e:
            logger.error(f"Error analyzing images batch: {e}")
            return f"Error: {str(e)}"

    async def process_frame_batch(
        self,
        frames_data: list,
        prompt: Optional[str] = None,
        frame_timing_ms: Optional[dict] = None,
    ) -> None:
        """Process a batch of frames in a single VLM call."""
        if self._closed:
            return

        task = self._track_current_task()
        try:
            if self._processing_lock.locked():
                logger.debug("VLM busy, skipping frame batch")
                return

            async with self._processing_lock:
                if self._closed:
                    return
                self.is_processing = True
                try:
                    self.last_frame_timing_ms = frame_timing_ms or {}
                    captured_prompt = self.prompt
                    used_prompt, consumed_background_handoff = self._resolve_prompt_for_inference(
                        prompt,
                        captured_prompt,
                    )
                    self.last_user_prompt = str(used_prompt or "").strip()
                    request_frames_data = self._batch_with_next_turn_timestamp(frames_data)
                    response = await self.analyze_images(request_frames_data, used_prompt)
                    if self._closed:
                        return
                    self.current_response = response
                    if captured_prompt and self.prompt == captured_prompt:
                        self.prompt = None
                    if consumed_background_handoff:
                        logger.info("Consumed background handoff metadata for session %s", self.session_id)
                finally:
                    self.is_processing = False
        finally:
            self._untrack_task(task)

    def get_current_response(self) -> tuple[str, bool]:
        return self.current_response, self.is_processing

    def _extract_response_text(self, response) -> str:
        """Return assistant text from common OpenAI-compatible response shapes."""

        output_text = self._normalize_text_value(getattr(response, "output_text", None))
        if output_text:
            return output_text

        choices = getattr(response, "choices", None) or []
        if choices:
            choice = choices[0]
            message = getattr(choice, "message", None)
            if message is not None:
                for attr_name in ("content", "reasoning_content", "reasoning", "refusal"):
                    text = self._normalize_text_value(getattr(message, attr_name, None))
                    if text:
                        return text

                tool_calls = getattr(message, "tool_calls", None)
                if tool_calls:
                    return self._json_preview(tool_calls)

            for attr_name in ("text", "content"):
                text = self._normalize_text_value(getattr(choice, attr_name, None))
                if text:
                    return text

            delta = getattr(choice, "delta", None)
            if delta is not None:
                text = self._normalize_text_value(getattr(delta, "content", None))
                if text:
                    return text

        payload = self._response_to_dict(response)
        if payload:
            for path in (
                ("output_text",),
                ("choices", 0, "message", "content"),
                ("choices", 0, "message", "reasoning_content"),
                ("choices", 0, "message", "reasoning"),
                ("choices", 0, "message", "refusal"),
                ("choices", 0, "text"),
                ("choices", 0, "delta", "content"),
            ):
                text = self._normalize_text_value(self._get_path(payload, path))
                if text:
                    return text

        finish_reason = self._response_finish_reason(response, payload)
        logger.warning(
            "VLM returned empty content for session %s; finish_reason=%s",
            self.session_id,
            finish_reason or "unknown",
        )
        return f"Empty model response{': ' + finish_reason if finish_reason else ''}"

    def _normalize_text_value(self, value) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            parts = []
            for item in value:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content") or item.get("value") or ""
                else:
                    text = (
                        getattr(item, "text", None)
                        or getattr(item, "content", None)
                        or getattr(item, "value", None)
                        or ""
                    )
                normalized = self._normalize_text_value(text)
                if normalized:
                    parts.append(normalized)
            return "\n".join(parts).strip()
        if isinstance(value, dict):
            for key in ("text", "content", "value"):
                text = self._normalize_text_value(value.get(key))
                if text:
                    return text
            return self._json_preview(value)
        return str(value).strip()

    def _response_to_dict(self, response) -> dict:
        for attr_name in ("model_dump", "dict"):
            method = getattr(response, attr_name, None)
            if method is None:
                continue
            try:
                payload = method()
            except Exception:
                continue
            if isinstance(payload, dict):
                return payload
        if isinstance(response, dict):
            return response
        return {}

    def _get_path(self, payload: dict, path: tuple):
        value = payload
        for key in path:
            try:
                if isinstance(key, int):
                    value = value[key]
                else:
                    value = value.get(key)
            except (AttributeError, IndexError, KeyError, TypeError):
                return None
        return value

    def _response_finish_reason(self, response, payload: Optional[dict]) -> str:
        choices = getattr(response, "choices", None) or []
        if choices:
            finish_reason = getattr(choices[0], "finish_reason", None)
            if finish_reason:
                return str(finish_reason)
        finish_reason = self._get_path(payload or {}, ("choices", 0, "finish_reason"))
        return str(finish_reason) if finish_reason else ""

    def _json_preview(self, value) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, default=str, indent=2).strip()
        except Exception:
            return str(value).strip()

    def get_metrics(self) -> dict:
        avg_latency = (
            self.total_inference_time / self.total_inferences if self.total_inferences > 0 else 0.0
        )

        metrics = {
            "last_latency_ms": self.last_inference_time * 1000,
            "avg_latency_ms": avg_latency * 1000,
            "total_inferences": self.total_inferences,
            "is_processing": self.is_processing,
            "latency_breakdown_ms": self.last_latency_breakdown_ms,
            "frame_timing_ms": self.last_frame_timing_ms,
            "user_prompt": self.last_user_prompt,
        }
        if self._last_background_handoff_meta:
            metrics["background_handoff"] = self._last_background_handoff_meta
        return metrics

    def consume_background_handoff_metric(self) -> Optional[dict]:
        meta = self._last_background_handoff_meta
        self._last_background_handoff_meta = None
        return meta

    def update_prompt(self, new_prompt: Optional[str]) -> None:
        prompt_text = new_prompt.strip() if new_prompt else None
        self.prompt = prompt_text
        logger.info(f"Updated prompt to: {prompt_text}")

    def update_api_settings(
        self, api_base: Optional[str] = None, api_key: Optional[str] = None
    ) -> None:
        if api_base:
            self.api_base = api_base
        if api_key is not None:
            self.api_key = api_key if api_key else "EMPTY"

        self.client = AsyncOpenAI(base_url=self.api_base, api_key=self.api_key)

        masked_key = (
            "***" + self.api_key[-4:]
            if self.api_key and len(self.api_key) > 4 and self.api_key != "EMPTY"
            else "EMPTY"
        )
        logger.info(f"Updated API settings - base: {self.api_base}, key: {masked_key}")

    async def reset_adapter_session(self) -> bool:
        """Call the adapter's /v1/streaming/reset to flush session outputs."""
        import aiohttp

        reset_url = self.api_base.rstrip("/").removesuffix("/v1") + "/v1/streaming/reset"
        try:
            async with aiohttp.ClientSession() as http:
                async with http.post(
                    reset_url,
                    json={"user": self.session_id},
                    headers={"x-streaming-session": self.session_id},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    ok = resp.status == 200
                    logger.info(
                        f"Adapter reset for session {self.session_id}: status={resp.status}"
                    )
                    return ok
        except Exception as e:
            logger.warning(f"Adapter reset failed for session {self.session_id}: {e}")
            return False
