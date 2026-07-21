# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Video frame processor.

Consumes decoded video frames from LiveKit or RTSP, samples them at the configured
foreground cadence, and forwards images to the VLM service.
"""

import asyncio
import logging
import time
from typing import Optional

import av
import numpy as np
from PIL import Image

from .vlm_service import VLMService

# Enable swscaler warnings to track hardware acceleration status.
av.logging.set_level(av.logging.WARNING)

logger = logging.getLogger(__name__)

_cv2_module = None


def get_cv2():
    """Import cv2 only when frame conversion/overlay actually needs it."""
    global _cv2_module
    if _cv2_module is None:
        import cv2

        _cv2_module = cv2
    return _cv2_module


class VideoProcessorTrack:
    """
    Process decoded video frames and send sampled frames to the VLM.

    The class keeps the old name so existing RTSP/session code can remain small.
    It no longer implements a WebRTC track; callers either use ``recv`` with a
    source object that has ``recv()``, or call ``process_*`` directly.
    """

    process_interval_seconds = 1.0
    max_frame_latency = 0.0
    frames_per_batch = 1

    def __init__(
        self,
        track,
        vlm_service: VLMService,
        text_callback=None,
        background_service=None,
    ):
        self.track = track
        self.vlm_service = vlm_service
        self.text_callback = text_callback
        self.background_service = background_service
        self.last_frame: Optional[np.ndarray] = None
        self.frame_count = 0
        self.dropped_frames = 0
        self.first_frame_pts = None
        self.first_frame_time = None
        self.frame_time_base = None
        self._last_process_time = 0.0
        self._frame_buffer = []
        self._last_sub_capture_time = 0.0
        self._last_callback_inference_count = 0
        self._last_callback_response = None
        self._stopped = False

    async def recv(self):
        """Receive one frame from ``self.track``, process it, and return it."""
        if self._stopped or self.track is None:
            raise StopAsyncIteration

        frame = await self.track.recv()
        await self.process_av_frame(frame)
        return frame

    async def process_av_frame(self, frame) -> None:
        """Process a PyAV ``VideoFrame``."""
        pts = getattr(frame, "pts", None)
        time_base = getattr(frame, "time_base", None)
        frame_timestamp, timestamp_kind, frame_latency = self._frame_timing(pts, time_base)

        max_latency = self.__class__.max_frame_latency
        while (
            self.track is not None
            and max_latency > 0
            and frame_latency > max_latency
            and pts is not None
        ):
            self.dropped_frames += 1
            frame = await self.track.recv()
            pts = getattr(frame, "pts", None)
            time_base = getattr(frame, "time_base", None)
            frame_timestamp, timestamp_kind, frame_latency = self._frame_timing(pts, time_base)
            if self.dropped_frames % 100 == 0:
                self._reset_timing(pts, time_base)
                break

        t1 = time.time()
        img = frame.to_ndarray(format="bgr24")
        t2 = time.time()
        pil_img = Image.fromarray(img[:, :, ::-1])
        t3 = time.time()
        await self._process_image(
            pil_img,
            img,
            frame_timestamp=frame_timestamp,
            timestamp_kind=timestamp_kind,
            pts=pts,
            frame_timing_ms={
                "frame_to_ndarray_ms": 1000 * (t2 - t1),
                "frame_copy_ms": 0.0,
                "bgr_to_rgb_pil_ms": 1000 * (t3 - t2),
                "pre_vlm_total_ms": 1000 * (t3 - t1),
            },
        )

    async def process_livekit_frame(self, frame_event) -> None:
        """Process a LiveKit ``VideoFrameEvent``."""
        frame = frame_event.frame
        timestamp_us = int(getattr(frame_event, "timestamp_us", 0) or 0)
        pts = timestamp_us if timestamp_us else None
        frame_timestamp, timestamp_kind, _ = self._frame_timing(pts, 1 / 1_000_000)

        t1 = time.time()
        rgb = self._livekit_frame_to_rgb(frame)
        t2 = time.time()
        pil_img = Image.fromarray(rgb, "RGB")
        t3 = time.time()
        bgr = rgb[:, :, ::-1].copy()
        t4 = time.time()
        await self._process_image(
            pil_img,
            bgr,
            frame_timestamp=frame_timestamp,
            timestamp_kind=timestamp_kind,
            pts=pts,
            frame_timing_ms={
                "frame_to_ndarray_ms": 1000 * (t2 - t1),
                "frame_copy_ms": 1000 * (t4 - t3),
                "bgr_to_rgb_pil_ms": 1000 * (t3 - t2),
                "pre_vlm_total_ms": 1000 * (t4 - t1),
            },
        )

    def _livekit_frame_to_rgb(self, frame) -> np.ndarray:
        """Return an RGB ndarray for a LiveKit frame."""
        from livekit import rtc

        video_type = getattr(frame, "type", None)
        if video_type != rtc.VideoBufferType.RGB24:
            frame = frame.convert(rtc.VideoBufferType.RGB24)

        width = int(frame.width)
        height = int(frame.height)
        data = np.frombuffer(frame.data, dtype=np.uint8)
        return data.reshape((height, width, 3)).copy()

    def _frame_timing(self, pts, time_base):
        frame_timestamp = time.time()
        timestamp_kind = "wall_clock_seconds"
        frame_latency = 0.0

        if pts is None:
            return frame_timestamp, timestamp_kind, frame_latency

        if self.first_frame_pts is None:
            self._reset_timing(pts, time_base)

        if self.first_frame_pts is not None and self.frame_time_base is not None:
            frame_offset = (pts - self.first_frame_pts) * self.frame_time_base
            expected_wall_time = self.first_frame_time + frame_offset
            frame_latency = time.time() - expected_wall_time
            frame_timestamp = frame_offset
            timestamp_kind = "relative_seconds"

        return frame_timestamp, timestamp_kind, frame_latency

    def _reset_timing(self, pts, time_base) -> None:
        if pts is None:
            return
        try:
            self.first_frame_pts = pts
            self.first_frame_time = time.time()
            self.frame_time_base = float(time_base) if time_base is not None else None
            logger.info(
                "Latency tracking initialized: PTS=%s, time_base=%s",
                self.first_frame_pts,
                self.frame_time_base,
            )
        except Exception:
            self.first_frame_pts = None
            self.first_frame_time = None
            self.frame_time_base = None

    async def _process_image(
        self,
        pil_img: Image.Image,
        bgr_img: np.ndarray,
        *,
        frame_timestamp: float,
        timestamp_kind: str,
        pts,
        frame_timing_ms: dict,
    ) -> None:
        self.frame_count += 1
        self.last_frame = bgr_img.copy()

        now = time.time()
        interval_sec = self.__class__.process_interval_seconds
        frames_per_batch = max(1, self.__class__.frames_per_batch)
        background_needs_frame = self._background_needs_frame(now)

        if self.frame_count == 1:
            logger.info("First frame received: %s", bgr_img.shape)

        if frames_per_batch <= 1:
            time_since_last = now - self._last_process_time
            need_conversion = (time_since_last >= interval_sec) or (self.frame_count == 1)

            if background_needs_frame:
                self._cache_background_frame(
                    pil_img,
                    frame_timestamp=frame_timestamp,
                    timestamp_kind=timestamp_kind,
                    pts=pts,
                    frames_per_batch=frames_per_batch,
                    process_interval_seconds=interval_sec,
                    wall_time=now,
                )

            if need_conversion:
                asyncio.create_task(
                    self.vlm_service.process_frame(
                        pil_img,
                        frame_timing_ms=frame_timing_ms,
                        frame_metadata={
                            "timestamp": frame_timestamp,
                            "timestamp_kind": timestamp_kind,
                            "pts": pts,
                            "timestamp_interval_seconds": interval_sec,
                        },
                    )
                )
                self._last_process_time = now
                logger.info(
                    "Frame %s: Sending to VLM (interval=%ss)",
                    self.frame_count,
                    interval_sec,
                )
        else:
            sub_interval = interval_sec / frames_per_batch
            time_since_last_sub = now - self._last_sub_capture_time
            need_capture = (time_since_last_sub >= sub_interval) or (self.frame_count == 1)

            if need_capture:
                self._frame_buffer.append(
                    {
                        "image": pil_img,
                        "timestamp": frame_timestamp,
                        "timestamp_kind": timestamp_kind,
                        "timestamp_interval_seconds": interval_sec,
                        "pts": pts,
                        "frame_timing_ms": frame_timing_ms,
                    }
                )
                self._last_sub_capture_time = now

            if background_needs_frame:
                self._cache_background_frame(
                    pil_img,
                    frame_timestamp=frame_timestamp,
                    timestamp_kind=timestamp_kind,
                    pts=pts,
                    frames_per_batch=frames_per_batch,
                    process_interval_seconds=interval_sec,
                    wall_time=now,
                )

            if len(self._frame_buffer) >= frames_per_batch:
                batch = list(self._frame_buffer)
                self._frame_buffer.clear()
                asyncio.create_task(self.vlm_service.process_frame_batch(batch))
                self._last_process_time = now
                logger.info(
                    "Frame %s: Sending %s frames to VLM (interval=%ss, batch=%s)",
                    self.frame_count,
                    len(batch),
                    interval_sec,
                    frames_per_batch,
                )

        self._emit_text_update()

    def _emit_text_update(self) -> None:
        if not self.text_callback:
            return

        response, _ = self.vlm_service.get_current_response()
        metrics = self.vlm_service.get_metrics()
        inference_count = int(metrics.get("total_inferences") or 0)
        has_new_inference = inference_count > self._last_callback_inference_count
        has_new_response = response != self._last_callback_response

        if inference_count > 0 and (has_new_inference or has_new_response):
            self._last_callback_inference_count = inference_count
            self._last_callback_response = response
            handoff_meta = self.vlm_service.consume_background_handoff_metric()
            if handoff_meta:
                metrics = dict(metrics)
                metrics["background_handoff"] = handoff_meta
            self.text_callback(response, metrics)

    def _background_needs_frame(self, wall_time: float) -> bool:
        if not self.background_service:
            return False
        try:
            self.background_service.set_foreground_sampling(
                process_interval_seconds=self.__class__.process_interval_seconds,
                frames_per_batch=self.__class__.frames_per_batch,
            )
            return self.background_service.should_sample_frame(wall_time)
        except Exception:
            logger.warning("Failed to check background frame sampling", exc_info=True)
            return False

    def _cache_background_frame(
        self,
        pil_img: Image.Image,
        *,
        frame_timestamp: float,
        timestamp_kind: str,
        pts,
        frames_per_batch: int,
        process_interval_seconds: float,
        wall_time: float,
    ) -> None:
        if not self.background_service:
            return
        try:
            self.background_service.set_foreground_sampling(
                process_interval_seconds=process_interval_seconds,
                frames_per_batch=frames_per_batch,
            )
            self.background_service.add_frame(
                pil_img,
                timestamp=frame_timestamp,
                timestamp_kind=timestamp_kind,
                pts=pts,
                foreground_frames_per_batch=frames_per_batch,
                wall_time=wall_time,
            )
        except Exception:
            logger.warning("Failed to cache background frame", exc_info=True)

    def stop(self) -> None:
        self._stopped = True

    def _add_text_overlay(self, img: np.ndarray, text: str, status: str = "") -> np.ndarray:
        img_copy = img.copy()
        cv2 = get_cv2()
        height, width = img_copy.shape[:2]
        full_text = f"{text} {status}" if status else text

        max_chars_per_line = 60
        words = full_text.split()
        lines = []
        current_line = []
        current_length = 0

        for word in words:
            if current_length + len(word) + 1 <= max_chars_per_line:
                current_line.append(word)
                current_length += len(word) + 1
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                current_line = [word]
                current_length = len(word)

        if current_line:
            lines.append(" ".join(current_line))

        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.7
        font_thickness = 2
        text_color = (255, 255, 255)
        bg_color = (0, 0, 0)
        padding = 10
        line_height = 30
        total_text_height = len(lines) * line_height + 2 * padding

        overlay = img_copy.copy()
        cv2.rectangle(overlay, (0, height - total_text_height), (width, height), bg_color, -1)
        cv2.addWeighted(overlay, 0.7, img_copy, 0.3, 0, img_copy)

        y_position = height - total_text_height + padding + line_height
        for line in lines:
            cv2.putText(
                img_copy,
                line,
                (padding, y_position),
                font,
                font_scale,
                text_color,
                font_thickness,
                cv2.LINE_AA,
            )
            y_position += line_height

        return img_copy
