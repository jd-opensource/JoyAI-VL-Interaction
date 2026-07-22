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
Video Track Processor
Handles video frames, adds text overlays, and manages VLM processing
"""

import asyncio
import numpy as np
from PIL import Image
from aiortc import VideoStreamTrack
from aiortc.mediastreams import MediaStreamError
from typing import Optional
import logging
import time
import av

from .vlm_service import VLMService

# Enable swscaler warnings to track hardware acceleration status
# TODO: Implement hardware-accelerated color space conversion on Jetson using NVMM/VPI
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


class VideoProcessorTrack(VideoStreamTrack):
    """
    Video track that receives frames, sends them to VLM for analysis,
    and overlays responses on the video before sending back
    """

    # Class variable for processing interval in seconds (can be updated dynamically)
    process_interval_seconds = 1.0
    # Max allowed latency before dropping frames (in seconds, 0 = disabled)
    max_frame_latency = 0.0
    # Number of frames to batch per VLM inference (1 = original behavior)
    frames_per_batch = 1

    def __init__(
        self,
        track: VideoStreamTrack,
        vlm_service: VLMService,
        text_callback=None,
        background_service=None,
    ):
        super().__init__()
        self.track = track
        self.vlm_service = vlm_service
        self.text_callback = text_callback  # Callback to send text updates
        self.background_service = background_service
        self.last_frame: Optional[np.ndarray] = None
        self.frame_count = 0
        self.dropped_frames = 0
        self.first_frame_pts = None  # Track first frame PTS to calculate relative time
        self.first_frame_time = None  # Wall clock time of first frame
        self.frame_time_base = None  # Time base for PTS conversion (e.g., 1/90000)
        self._last_process_time = 0.0  # Wall clock time of last VLM processing
        self._frame_buffer = []  # Buffer for collecting frames before batch send
        self._last_sub_capture_time = 0.0  # Wall clock time of last sub-frame capture
        self._last_callback_inference_count = 0
        self._last_callback_response = None

    def _notify_text_callback(self) -> None:
        """Send a completed VLM response once, even if the input video has ended."""
        if not self.text_callback:
            return

        response, _ = self.vlm_service.get_current_response()
        metrics = self.vlm_service.get_metrics()
        inference_count = int(metrics.get("total_inferences") or 0)
        has_new_inference = inference_count > self._last_callback_inference_count
        has_new_response = response != self._last_callback_response
        if inference_count <= 0 or not (has_new_inference or has_new_response):
            return

        self._last_callback_inference_count = inference_count
        self._last_callback_response = response
        handoff_meta = self.vlm_service.consume_background_handoff_metric()
        if handoff_meta:
            metrics = dict(metrics)
            metrics["background_handoff"] = handoff_meta
        self.text_callback(response, metrics)

    def _schedule_vlm_task(self, coroutine) -> None:
        """Notify the browser when a VLM task finishes, without waiting for another frame."""
        task = asyncio.create_task(coroutine)

        def on_done(completed_task) -> None:
            if completed_task.cancelled():
                return
            try:
                completed_task.result()
            except Exception:
                logger.error("VLM frame task failed", exc_info=True)
                return
            self._notify_text_callback()

        task.add_done_callback(on_done)

    async def recv(self):
        """
        Receive frame from input track, process it, and return with text overlay
        """
        try:
            # Get frame from incoming track
            frame = await self.track.recv()

            # Initialize timing on first frame
            if self.first_frame_pts is None and frame.pts is not None:
                self.first_frame_pts = frame.pts
                self.first_frame_time = time.time()
                # Store time_base for PTS conversion (e.g., 1/90000 for 90kHz clock)
                self.frame_time_base = float(frame.time_base)
                logger.info(
                    f"Latency tracking initialized: PTS={frame.pts}, time_base={frame.time_base} ({self.frame_time_base}s per tick)"
                )

            # Calculate actual frame age (latency) using PTS and time_base
            # Note: Some streams (like RTSP) may not have PTS set, so skip latency checks
            frame_latency = 0.0
            frame_timestamp = time.time()
            timestamp_kind = "wall_clock_seconds"
            if frame.pts is not None and self.first_frame_pts is not None:
                # PTS is in time_base units, convert to seconds: pts * time_base
                frame_time_offset = (frame.pts - self.first_frame_pts) * self.frame_time_base
                expected_wall_time = self.first_frame_time + frame_time_offset
                current_time = time.time()
                frame_latency = current_time - expected_wall_time
                frame_timestamp = frame_time_offset
                timestamp_kind = "relative_seconds"

            # Check for accumulated latency and drop old frames if needed (only if max_latency > 0)
            max_latency = self.__class__.max_frame_latency
            if max_latency > 0 and frame_latency > max_latency and frame.pts is not None:
                logger.warning(
                    f"Frame is {frame_latency:.2f}s behind, dropping frames (threshold: {max_latency}s)"
                )

                # Drop frames until we get a fresh one
                dropped_count = 0
                while frame_latency > max_latency:
                    self.dropped_frames += 1
                    dropped_count += 1

                    # Get next frame
                    frame = await self.track.recv()

                    # Recalculate latency for new frame (using time_base for correct conversion)
                    if frame.pts is not None and self.first_frame_pts is not None:
                        frame_time_offset = (
                            frame.pts - self.first_frame_pts
                        ) * self.frame_time_base
                        expected_wall_time = self.first_frame_time + frame_time_offset
                        frame_latency = time.time() - expected_wall_time
                    else:
                        # If PTS becomes unavailable, stop dropping frames
                        break

                    # Prevent infinite loop
                    if dropped_count > 100:
                        logger.error(
                            f"Dropped {dropped_count} frames, but still behind. Resetting timing."
                        )
                        if frame.pts is not None:
                            self.first_frame_pts = frame.pts
                            self.first_frame_time = time.time()
                            self.frame_time_base = float(frame.time_base)
                        break

                if dropped_count > 0:
                    logger.info(
                        f"Dropped {dropped_count} frames, now at {frame_latency:.2f}s latency"
                    )

            # Increment frame counter
            self.frame_count += 1

            # Only convert to numpy when needed (for VLM processing or first frame)
            # This avoids expensive CPU color conversion on every frame
            now = time.time()
            interval_sec = self.__class__.process_interval_seconds
            frames_per_batch = max(1, self.__class__.frames_per_batch)
            background_needs_frame = self._background_needs_frame(now)

            if frames_per_batch <= 1:
                # --- Original single-frame logic ---
                time_since_last = now - self._last_process_time
                need_conversion = (time_since_last >= interval_sec) or (self.frame_count == 1)

                if need_conversion or background_needs_frame:
                    t1 = time.time()
                    img = frame.to_ndarray(format="bgr24")
                    t2 = time.time()
                    self.last_frame = img.copy()
                    t3 = time.time()

                    if self.frame_count % 100 == 0:
                        logger.info(
                            f"Frame conversion times: to_ndarray={1000*(t2-t1):.1f}ms, copy={1000*(t3-t2):.1f}ms"
                        )
                    if self.frame_count == 1:
                        logger.info(f"First frame received: {img.shape}")

                    t4 = time.time()
                    pil_img = Image.fromarray(img[:, :, ::-1])
                    t5 = time.time()
                    frame_timing_ms = {
                        "frame_to_ndarray_ms": 1000 * (t2 - t1),
                        "frame_copy_ms": 1000 * (t3 - t2),
                        "bgr_to_rgb_pil_ms": 1000 * (t5 - t4),
                        "pre_vlm_total_ms": 1000 * (t5 - t1),
                    }
                    if background_needs_frame:
                        self._cache_background_frame(
                            pil_img,
                            frame_timestamp=frame_timestamp,
                            timestamp_kind=timestamp_kind,
                            pts=frame.pts,
                            frames_per_batch=frames_per_batch,
                            process_interval_seconds=interval_sec,
                            wall_time=now,
                        )
                    if need_conversion:
                        self._schedule_vlm_task(
                            self.vlm_service.process_frame(
                                pil_img,
                                frame_timing_ms=frame_timing_ms,
                                frame_metadata={
                                    "timestamp": frame_timestamp,
                                    "timestamp_kind": timestamp_kind,
                                    "pts": frame.pts,
                                    "timestamp_interval_seconds": interval_sec,
                                },
                            )
                        )
                        self._last_process_time = now
                        logger.info(f"Frame {self.frame_count}: Sending to VLM (interval={interval_sec}s)")
            else:
                # --- Multi-frame batch logic ---
                sub_interval = interval_sec / frames_per_batch
                time_since_last_sub = now - self._last_sub_capture_time
                need_capture = (time_since_last_sub >= sub_interval) or (self.frame_count == 1)

                if need_capture or background_needs_frame:
                    t1 = time.time()
                    img = frame.to_ndarray(format="bgr24")
                    t2 = time.time()
                    self.last_frame = img.copy()
                    t3 = time.time()
                    pil_img = Image.fromarray(img[:, :, ::-1])
                    t4 = time.time()

                    if self.frame_count == 1:
                        logger.info(f"First frame received: {img.shape}")

                    if need_capture:
                        self._frame_buffer.append({
                            "image": pil_img,
                            "timestamp": frame_timestamp,
                            "timestamp_kind": timestamp_kind,
                            "timestamp_interval_seconds": interval_sec,
                            "pts": frame.pts,
                            "frame_timing_ms": {
                                "frame_to_ndarray_ms": 1000 * (t2 - t1),
                                "frame_copy_ms": 1000 * (t3 - t2),
                                "bgr_to_rgb_pil_ms": 1000 * (t4 - t3),
                                "pre_vlm_total_ms": 1000 * (t4 - t1),
                            },
                        })
                        self._last_sub_capture_time = now

                    if background_needs_frame:
                        self._cache_background_frame(
                            pil_img,
                            frame_timestamp=frame_timestamp,
                            timestamp_kind=timestamp_kind,
                            pts=frame.pts,
                            frames_per_batch=frames_per_batch,
                            process_interval_seconds=interval_sec,
                            wall_time=now,
                        )

                    if len(self._frame_buffer) >= frames_per_batch:
                        batch = list(self._frame_buffer)
                        self._frame_buffer.clear()
                        self._schedule_vlm_task(
                            self.vlm_service.process_frame_batch(batch)
                        )
                        self._last_process_time = now
                        logger.info(
                            f"Frame {self.frame_count}: Sending {len(batch)} frames to VLM "
                            f"(interval={interval_sec}s, batch={frames_per_batch})"
                        )

            # Also update promptly during live playback. Completion callbacks above
            # cover the case where the final request finishes after video EOF.
            self._notify_text_callback()

            # Return original frame directly - zero-copy passthrough!
            # This avoids expensive BGR→YUV conversion
            return frame

        except MediaStreamError:
            # Track ended (user stopped, tab closed, etc.) — normal, not an error
            logger.debug("Video track ended")
            raise
        except Exception as e:
            logger.error(f"Error processing frame: {e}", exc_info=True)
            raise

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

    def _add_text_overlay(self, img: np.ndarray, text: str, status: str = "") -> np.ndarray:
        """
        Add text overlay to image

        Args:
            img: Input image (BGR format)
            text: Text to overlay (VLM response)
            status: Optional status text

        Returns:
            Image with text overlay
        """
        img_copy = img.copy()
        cv2 = get_cv2()
        height, width = img_copy.shape[:2]

        # Prepare text
        full_text = f"{text} {status}" if status else text

        # Text wrapping - split long captions
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

        # Text properties
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.7
        font_thickness = 2
        text_color = (255, 255, 255)  # White
        bg_color = (0, 0, 0)  # Black background
        padding = 10
        line_height = 30

        # Calculate total height needed
        total_text_height = len(lines) * line_height + 2 * padding

        # Create semi-transparent overlay at bottom
        overlay = img_copy.copy()
        cv2.rectangle(overlay, (0, height - total_text_height), (width, height), bg_color, -1)

        # Blend overlay with original image
        alpha = 0.7
        cv2.addWeighted(overlay, alpha, img_copy, 1 - alpha, 0, img_copy)

        # Add text lines
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
