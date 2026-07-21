"""
RTSP video reader for IP camera support.

This module provides a small async frame reader for RTSP streams so the WebUI can
process IP camera feeds without an in-process WebRTC stack.

SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
"""

import av
import asyncio
import logging
import re
import threading
from typing import Optional
from av import VideoFrame

# Suppress verbose ffmpeg/libav logging (HEVC decoder errors are normal for IP cameras)
# These POC/slice errors happen due to network packet loss but stream recovers automatically
av.logging.set_level(av.logging.FATAL)  # Only show fatal errors that stop the stream

logger = logging.getLogger(__name__)


class RTSPVideoTrack:
    """
    Video reader that returns decoded PyAV VideoFrame objects from RTSP.

    This enables processing of IP camera feeds through the same pipeline as webcam input.
    Supports automatic reconnection on stream failure.

    Example:
        track = RTSPVideoTrack("rtsp://192.168.1.100:554/stream")
        frame = await track.recv()
    """

    def __init__(
        self,
        rtsp_url: str,
        reconnect_attempts: int = 5,
        reconnect_delay: float = 2.0,
        options: Optional[dict] = None,
    ):
        """
        Initialize RTSP video track.

        Args:
            rtsp_url: Full RTSP URL (e.g., rtsp://user:pass@192.168.1.100:554/stream)
            reconnect_attempts: Number of reconnection attempts on failure (default: 5)
            reconnect_delay: Base delay between reconnection attempts in seconds (default: 2.0)
            options: Additional PyAV container options (default: TCP transport)
        """
        self.rtsp_url = rtsp_url
        self.reconnect_attempts = reconnect_attempts
        self.reconnect_delay = reconnect_delay
        self.container: Optional[av.container.InputContainer] = None
        self.stream: Optional[av.video.VideoStream] = None
        self._stopped = False
        self._frame_count = 0
        self._read_lock = threading.Lock()

        # Default options for RTSP
        self.options = options or {
            "rtsp_transport": "tcp",  # TCP is more reliable than UDP for most networks
            "max_delay": "500000",  # 500ms max delay for low latency
            "rtsp_flags": "prefer_tcp",
        }

        # Connect to stream
        self._connect()

    def _sanitize_url(self, url: str) -> str:
        """
        Remove password from URL for safe logging.

        Args:
            url: RTSP URL potentially containing credentials

        Returns:
            URL with password replaced by ****
        """
        return re.sub(r"://([^:]+):([^@]+)@", r"://\1:****@", url)

    def _connect(self):
        """
        Connect to RTSP stream.

        Raises:
            Exception: If connection fails after all attempts
        """
        safe_url = self._sanitize_url(self.rtsp_url)

        try:
            logger.info(f"Connecting to RTSP stream: {safe_url}")

            # Open RTSP stream
            self.container = av.open(self.rtsp_url, options=self.options)

            # Get video stream
            if not self.container.streams.video:
                raise ValueError("No video stream found in RTSP source")

            self.stream = self.container.streams.video[0]

            # Log stream information
            codec = self.stream.codec_context.name
            width = self.stream.width or "unknown"
            height = self.stream.height or "unknown"
            fps = self.stream.average_rate or "unknown"

            logger.info(f"RTSP connected successfully: {codec} {width}x{height} @{fps}fps")

        except Exception as e:
            logger.error(f"Failed to connect to RTSP stream {safe_url}: {e}")
            raise

    async def recv(self) -> VideoFrame:
        """
        Receive next frame from RTSP stream.

        Runs demuxing/decoding in executor to avoid blocking event loop.

        Returns:
            VideoFrame: Next decoded video frame

        Raises:
            StopAsyncIteration: When stream ends or is stopped
        """
        if self._stopped:
            raise StopAsyncIteration

        try:
            # Read frame from container (blocking operation, run in executor)
            loop = asyncio.get_event_loop()
            frame = await loop.run_in_executor(None, self._read_frame)

            if frame is None:
                if not self._stopped:
                    logger.warning("RTSP stream ended unexpectedly, attempting reconnection")
                    await self._reconnect()
                    # Try again after reconnection
                    frame = await loop.run_in_executor(None, self._read_frame)
                    if frame is None:
                        raise StopAsyncIteration
                else:
                    raise StopAsyncIteration

            self._frame_count += 1

            # Log progress periodically
            if self._frame_count % 300 == 0:  # Every ~10 seconds at 30fps
                logger.debug(f"RTSP: Received {self._frame_count} frames")

            return frame

        except StopAsyncIteration:
            raise
        except Exception as e:
            logger.error(f"Error receiving RTSP frame: {e}", exc_info=True)
            # Try to reconnect on error
            if not self._stopped:
                await self._reconnect()
            raise

    def _read_frame(self) -> Optional[VideoFrame]:
        """
        Read and decode next frame from RTSP stream (blocking).

        This is a blocking operation and should be run in an executor.

        Returns:
            VideoFrame or None if stream ended or error occurred
        """
        with self._read_lock:
            if self._stopped or not self.container or not self.stream:
                return None

            try:
                for packet in self.container.demux(self.stream):
                    for frame in packet.decode():
                        if isinstance(frame, VideoFrame):
                            return frame

                logger.info("RTSP stream reached end of file")
                return None

            except av.error.EOFError:
                logger.warning("RTSP stream EOF")
                return None
            except Exception as e:
                if self._stopped:
                    return None
                logger.error(f"Error decoding RTSP frame: {e}")
                return None

    async def _reconnect(self):
        """
        Attempt to reconnect to RTSP stream with exponential backoff.

        Tries multiple times with increasing delay between attempts.
        """
        safe_url = self._sanitize_url(self.rtsp_url)
        logger.info(f"Attempting RTSP reconnection to {safe_url}...")

        # Clean up existing connection
        if self.container:
            try:
                self.container.close()
            except Exception as e:
                logger.debug(f"Error closing container during reconnect: {e}")
            self.container = None
            self.stream = None

        # Try to reconnect with exponential backoff
        for attempt in range(self.reconnect_attempts):
            try:
                logger.info(f"Reconnection attempt {attempt + 1}/{self.reconnect_attempts}")

                # Wait with exponential backoff (2, 4, 8, 16, 32 seconds)
                if attempt > 0:
                    delay = self.reconnect_delay * (2 ** (attempt - 1))
                    logger.info(f"Waiting {delay}s before reconnection attempt...")
                    await asyncio.sleep(delay)

                # Attempt connection
                self._connect()
                logger.info(f"RTSP reconnected successfully on attempt {attempt + 1}")
                return

            except Exception as e:
                logger.warning(f"Reconnection attempt {attempt + 1} failed: {e}")
                if attempt == self.reconnect_attempts - 1:
                    logger.error(
                        f"RTSP reconnection failed after {self.reconnect_attempts} attempts"
                    )
                    raise

    def stop(self):
        """
        Stop the RTSP stream and clean up resources.

        Should be called when stream is no longer needed.
        """
        self._stopped = True

        # Wait for any in-flight _read_frame to finish before closing the container,
        # otherwise the executor thread segfaults in native ffmpeg code.
        with self._read_lock:
            if self.container:
                try:
                    self.container.close()
                    logger.info(f"RTSP stream closed: {self._frame_count} frames received")
                except Exception as e:
                    logger.warning(f"Error closing RTSP container: {e}")
                finally:
                    self.container = None
                    self.stream = None

    @property
    def is_connected(self) -> bool:
        """Check if RTSP stream is currently connected."""
        return self.container is not None and not self._stopped

    def get_stats(self) -> dict:
        """
        Get statistics about the RTSP stream.

        Returns:
            Dictionary with stream statistics
        """
        stats = {
            "url": self._sanitize_url(self.rtsp_url),
            "connected": self.is_connected,
            "frames_received": self._frame_count,
            "stopped": self._stopped,
        }

        if self.stream:
            stats.update(
                {
                    "codec": self.stream.codec_context.name,
                    "width": self.stream.width,
                    "height": self.stream.height,
                    "fps": float(self.stream.average_rate) if self.stream.average_rate else None,
                }
            )

        return stats
