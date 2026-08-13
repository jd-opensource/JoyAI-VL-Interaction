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
LiveKit Joy VL Interaction Server.

Main server that serves the web interface, issues LiveKit tokens, and consumes
video tracks for VLM analysis.
"""

import asyncio
import base64
import contextlib
import io
import json
import logging
import os
import re
import signal
import shutil
import socket
import subprocess
import sys
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

import aiohttp
from aiohttp import web
from livekit import api as livekit_api
from livekit import rtc as livekit_rtc
from PIL import Image

from .vlm_service import VLMService
from .video_processor import VideoProcessorTrack
from .rtsp_track import RTSPVideoTrack
from .asr import setup_asr_routes
from .tts import setup_tts_routes
from .background_model import BackgroundModelService
from .local_file_server import setup_local_file_routes

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global objects
vlm_service = None  # Kept for backwards compat; default session uses sessions["default"]
websockets = set()  # Track active WebSocket connections (all)
rtsp_tracks = {}  # session_id -> (rtsp_track, processor_track, frame_task, optional_livekit_relay)
livekit_workers = {}  # session_id -> LiveKitSessionWorker
uploaded_videos = {}  # upload_id -> UploadedVideo
video_ffmpeg_sessions = {}  # session_id -> UploadedVideoSession
uploaded_video_livekit_sessions = {}  # session_id -> upload_id
uploaded_video_frame_relays = {}  # session_id -> BrowserFrameLiveKitRelay
session_disconnect_cleanup_tasks = {}  # session_id -> delayed cleanup task
upload_session_lifecycle_locks = defaultdict(asyncio.Lock)

# Multi-session state
default_vlm_config = {}  # Set at startup; used to create new sessions
sessions = {}  # session_id -> {"vlm_service": VLMService}
session_websockets = defaultdict(set)  # session_id -> set of ws
ws_to_session = {}  # ws -> session_id
LIVEKIT_INTERNAL_URL = os.environ.get("LIVEKIT_INTERNAL_URL", "ws://127.0.0.1:8298")
LIVEKIT_PUBLIC_PATH = os.environ.get("LIVEKIT_PUBLIC_PATH", "/livekit")
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "joyvl")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "joyvl-secret-123456789012345678901234")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        logger.warning("Invalid integer env %s=%r; using %s", name, os.environ.get(name), default)
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        logger.warning("Invalid float env %s=%r; using %s", name, os.environ.get(name), default)
        return default


VIDEO_UPLOAD_ROOT = Path(os.environ.get("VIDEO_UPLOAD_ROOT", "/tmp/joyvl-video-uploads"))
VIDEO_UPLOAD_MAX_BYTES = _env_int("VIDEO_UPLOAD_MAX_BYTES", 10 * 1024 * 1024 * 1024)
VIDEO_UPLOAD_CHUNK_BYTES = _env_int("VIDEO_UPLOAD_CHUNK_BYTES", 1024 * 1024)
VIDEO_UPLOAD_RTSP_BASE = os.environ.get("VIDEO_UPLOAD_RTSP_BASE", "rtsp://127.0.0.1:8554").rstrip("/")
VIDEO_UPLOAD_FFMPEG_BIN = os.environ.get("VIDEO_UPLOAD_FFMPEG_BIN", "ffmpeg")
VIDEO_UPLOAD_READY_TIMEOUT = _env_float("VIDEO_UPLOAD_READY_TIMEOUT", 8.0)
VIDEO_UPLOAD_TERMINATE_TIMEOUT = _env_float("VIDEO_UPLOAD_TERMINATE_TIMEOUT", 3.0)
VIDEO_UPLOAD_PREROLL_SECONDS = max(0.0, _env_float("VIDEO_UPLOAD_PREROLL_SECONDS", 5.0))
VIDEO_UPLOAD_PREROLL_BLACK_THRESHOLD = max(
    0.0, _env_float("VIDEO_UPLOAD_PREROLL_BLACK_THRESHOLD", 8.0)
)
VIDEO_UPLOAD_PREROLL_MAX_EXTRA_SECONDS = max(
    0.0, _env_float("VIDEO_UPLOAD_PREROLL_MAX_EXTRA_SECONDS", 2.0)
)
VIDEO_UPLOAD_ALLOWED_SUFFIXES = {
    ".mp4",
    ".m4v",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
    ".ts",
}
VIDEO_UPLOAD_FRAME_MAX_BYTES = _env_int("VIDEO_UPLOAD_FRAME_MAX_BYTES", 2 * 1024 * 1024)
VIDEO_UPLOAD_FRAME_MAX_PIXELS = _env_int("VIDEO_UPLOAD_FRAME_MAX_PIXELS", 1920 * 1080)
VIDEO_UPLOAD_DISCONNECT_GRACE_SECONDS = max(
    0.0,
    _env_float("VIDEO_UPLOAD_DISCONNECT_GRACE_SECONDS", 5.0),
)
LIVEKIT_PROXY_ENV_VARS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)


async def _connect_livekit_room_without_proxy(room, token: str, options) -> None:
    """Connect to local LiveKit without inheriting HTTP proxy env vars."""
    saved = {name: os.environ.pop(name, None) for name in LIVEKIT_PROXY_ENV_VARS}
    try:
        await room.connect(LIVEKIT_INTERNAL_URL, token, options)
    finally:
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value


@dataclass
class UploadedVideo:
    upload_id: str
    path: Path
    original_name: str
    content_type: str
    size_bytes: int
    owner_session_id: str
    created_at: float


@dataclass
class UploadedVideoSession:
    session_id: str
    upload_id: str
    rtsp_url: str
    process: asyncio.subprocess.Process
    stderr_lines: deque
    stderr_task: asyncio.Task | None
    preroll_seconds: float = 0.0


def notify_session_json(session_id: str, payload: dict):
    """Send a JSON payload to WebSocket clients in this session."""
    handle_background_handoff_for_interaction(session_id, payload)
    send_to_session(session_id, json.dumps(payload, ensure_ascii=False))


def handle_background_handoff_for_interaction(session_id: str, payload: dict) -> None:
    if not isinstance(payload, dict) or payload.get("type") != "background_result_ready":
        return

    session = sessions.get(session_id)
    if not session or not session.get("vlm_service"):
        return
    handoff = payload.get("interaction_handoff")
    summary = ""
    if isinstance(handoff, dict):
        summary = str(handoff.get("summary") or "").strip()
    if not summary:
        logger.info(
            "[%s] Background result received without interaction handoff: task_id=%s",
            session_id,
            payload.get("task_id"),
        )
        return
    session["vlm_service"].queue_background_handoff(
        task_id=str(payload.get("task_id") or ""),
        question=str(payload.get("question") or ""),
        summary=summary,
    )
    logger.info(
        "[%s] Background handoff queued for interaction: task_id=%s summary_chars=%s",
        session_id,
        payload.get("task_id"),
        len(summary),
    )


def get_background_service(session_id: str):
    """Return the background model service for a session if it exists."""
    session = sessions.get(session_id)
    if not session:
        return None
    return session.get("background_service")


def get_or_create_session(session_id: str):
    """Get or create per-session state (VLM service). Thread-safe for aiohttp."""
    if session_id not in sessions:
        cfg = default_vlm_config
        sessions[session_id] = {
            "vlm_service": VLMService(
                model=cfg.get("model", "meta/llama-3.2-11b-vision-instruct"),
                api_base=cfg.get("api_base", "http://localhost:8000/v1"),
                api_key=cfg.get("api_key", "EMPTY"),
                prompt=cfg.get("prompt") or None,
                session_id=session_id,
            ),
            "background_service": BackgroundModelService(
                session_id=session_id,
                notify_callback=lambda payload, sid=session_id: notify_session_json(sid, payload),
                summarizer_api_base=cfg.get("api_base", "http://localhost:8000/v1"),
            ),
            "show_request_payload": False,
            "show_response_payload": False,
            "show_memory_state": False,
        }
        logger.info(f"Created new session: {session_id}")
    return sessions[session_id]


def send_to_session(session_id: str, message: str):
    """Send a message only to WebSocket clients in this session."""
    for ws in session_websockets.get(session_id, set()):
        try:
            asyncio.create_task(ws.send_str(message))
        except Exception as e:
            logger.error(f"Error sending to session {session_id}: {e}")


def get_session_callback(session_id: str):
    """Return a text_callback that sends VLM results only to this session."""
    _last_memory_hash = [None]

    def callback(text: str, metrics: dict):
        session = sessions.get(session_id)
        display_text = text
        if session and session.get("background_service"):
            display_text = session["background_service"].handle_foreground_response(
                text,
                metrics=metrics,
            )

        out = {"type": "vlm_response", "text": display_text, "metrics": metrics}
        if session and session.get("vlm_service"):
            svc = session["vlm_service"]
            if session.get("show_request_payload"):
                payload = svc.get_last_request_payload()
                if payload is not None:
                    out["request_payload"] = payload
            if session.get("show_response_payload"):
                payload = svc.get_last_response_payload()
                if payload is not None:
                    try:
                        out["response_payload"] = json.loads(json.dumps(payload, default=str))
                    except (TypeError, ValueError):
                        out["response_payload"] = payload
            resp = svc.get_last_response_payload()
            if resp and isinstance(resp, dict):
                sh = resp.get("streamingharness", {})
                memory = sh.get("memory") if isinstance(sh, dict) else None
                if memory:
                    mem_hash = json.dumps(memory, ensure_ascii=False, sort_keys=True)
                    if mem_hash != _last_memory_hash[0]:
                        _last_memory_hash[0] = mem_hash
                        out["memory_state"] = memory
                summarizer_timing = sh.get("summarizer_timing") if isinstance(sh, dict) else None
                if summarizer_timing:
                    out["summarizer_timing"] = summarizer_timing
        send_to_session(session_id, json.dumps(out, ensure_ascii=False))

    return callback


async def cleanup_session(session_id: str, reset_adapter: bool = True) -> dict:
    """Cancel active work and remove all server-side state for a session."""
    if not session_id:
        return {"session_id": session_id, "removed": False, "reason": "missing_session_id"}

    logger.info("[%s] Cleaning up session", session_id)

    disconnect_task = session_disconnect_cleanup_tasks.pop(session_id, None)
    if disconnect_task is not None and disconnect_task is not asyncio.current_task():
        disconnect_task.cancel()

    session_sockets = list(session_websockets.pop(session_id, set()))
    for ws in session_sockets:
        try:
            await ws.close()
        except Exception as e:
            logger.warning("[%s] Error closing websocket: %s", session_id, e)
        finally:
            websockets.discard(ws)
            ws_to_session.pop(ws, None)

    async with upload_session_lifecycle_locks[session_id]:
        if session_id in video_ffmpeg_sessions:
            await _stop_uploaded_video_session(session_id)

        await stop_uploaded_frame_relay(session_id)
        uploaded_video_livekit_sessions.pop(session_id, None)

        if session_id in rtsp_tracks:
            await _stop_rtsp_session(session_id)

        if session_id in livekit_workers:
            await stop_livekit_worker(session_id)

        session = sessions.pop(session_id, None)
        cancelled = 0
        cancelled_background = 0
        if session and session.get("vlm_service"):
            svc = session["vlm_service"]
            cancelled = await svc.cancel_active_requests()
            if reset_adapter:
                await svc.reset_adapter_session()
            await svc.close(cancel_requests=False)
        if session and session.get("background_service"):
            bg_svc = session["background_service"]
            cancelled_background = await bg_svc.cancel_active_requests()
            await bg_svc.close(cancel_requests=False)

    logger.info(
        "[%s] Session cleanup complete: removed=%s, websockets=%s, cancelled_vlm_tasks=%s, cancelled_background_tasks=%s",
        session_id,
        bool(session),
        len(session_sockets),
        cancelled,
        cancelled_background,
    )
    return {
        "session_id": session_id,
        "removed": bool(session),
        "websockets_closed": len(session_sockets),
        "cancelled_vlm_tasks": cancelled,
        "cancelled_background_tasks": cancelled_background,
    }


async def _cleanup_disconnected_upload_session(session_id: str) -> None:
    task = asyncio.current_task()
    try:
        await asyncio.sleep(VIDEO_UPLOAD_DISCONNECT_GRACE_SECONDS)
        if session_websockets.get(session_id):
            return
        if session_id not in uploaded_video_livekit_sessions:
            return
        logger.info(
            "[%s] Upload control channel remained disconnected; cleaning up session",
            session_id,
        )
        await cleanup_session(session_id)
    except asyncio.CancelledError:
        return
    finally:
        if session_disconnect_cleanup_tasks.get(session_id) is task:
            session_disconnect_cleanup_tasks.pop(session_id, None)


def is_port_available(port, host="0.0.0.0"):
    """Check if a port is available for binding"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
        sock.close()
        return True
    except OSError:
        return False


def find_process_using_port(port):
    """Find what process is using a port (Linux/Unix only)"""
    try:
        # Try lsof first (more reliable)
        result = subprocess.run(
            ["lsof", "-i", f":{port}", "-t"], capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0 and result.stdout.strip():
            pid = result.stdout.strip().split()[0]
            # Get process name
            name_result = subprocess.run(
                ["ps", "-p", pid, "-o", "comm="], capture_output=True, text=True, timeout=2
            )
            if name_result.returncode == 0:
                return f"PID {pid} ({name_result.stdout.strip()})"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # lsof not available, try netstat
        try:
            result = subprocess.run(
                ["netstat", "-tulpn"], capture_output=True, text=True, timeout=2
            )
            for line in result.stdout.split("\n"):
                if f":{port}" in line and "LISTEN" in line:
                    parts = line.split()
                    if len(parts) >= 7:
                        return parts[-1]  # PID/Program name
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return "unknown process"


def find_available_port(start_port=8080, max_attempts=10):
    """Find next available port starting from start_port"""
    for port in range(start_port, start_port + max_attempts):
        if is_port_available(port):
            return port
    return None


async def detect_local_service_and_model():
    """
    Auto-detect available local VLM services and select a model
    Returns: (api_base, model_name) or (None, None) if no service found
    """
    services = [
        ("http://localhost:11434/v1", "Ollama"),
        ("http://localhost:8000/v1", "vLLM"),
        ("http://localhost:30000/v1", "SGLang"),
    ]

    for api_base, service_name in services:
        try:
            # Try to connect to the service
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
                async with session.get(f"{api_base}/models") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        models = data.get("data", [])
                        if models:
                            # Prefer vision models
                            vision_keywords = ["vision", "llava", "llama-3.2", "gemini"]
                            for model in models:
                                model_id = model.get("id", "")
                                if any(keyword in model_id.lower() for keyword in vision_keywords):
                                    logger.info(f"✅ Auto-detected {service_name} at {api_base}")
                                    logger.info(f"   Selected model: {model_id}")
                                    return (api_base, model_id)

                            # If no vision model found, use the first one
                            model_id = models[0].get("id", "")
                            logger.info(f"✅ Auto-detected {service_name} at {api_base}")
                            logger.info(
                                f"   Selected model: {model_id} (vision model preferred but not found)"
                            )
                            return (api_base, model_id)
        except Exception as e:
            logger.debug(f"Service {service_name} not available at {api_base}: {e}")
            continue

    return (None, None)


async def index(request):
    """Serve the main HTML page"""
    content = open(os.path.join(os.path.dirname(__file__), "static", "index.html"), "r").read()
    return web.Response(content_type="text/html", text=content)


async def models(request):
    """Return available models from the VLM API"""
    try:
        # Check if custom API base and key are provided in query params
        api_base = request.rel_url.query.get("api_base")
        api_key = request.rel_url.query.get("api_key")

        if api_base:
            # Query models from the provided API endpoint
            from openai import AsyncOpenAI

            temp_client = AsyncOpenAI(base_url=api_base, api_key=api_key if api_key else "EMPTY")
            models_response = await temp_client.models.list()
            models_list = [
                {"id": model.id, "name": model.id, "current": False}
                for model in models_response.data
            ]
            return web.Response(
                content_type="application/json", text=json.dumps({"models": models_list})
            )
        else:
            # Use default session's VLM service (backwards compat when no api_base in query)
            default_svc = get_or_create_session("default")["vlm_service"]
            models_response = await default_svc.client.models.list()
            models_list = [
                {"id": model.id, "name": model.id, "current": model.id == default_svc.model}
                for model in models_response.data
            ]
            return web.Response(
                content_type="application/json", text=json.dumps({"models": models_list})
            )
    except Exception as e:
        logger.error(f"Error fetching models: {e}")
        # Return current model as fallback
        if sessions.get("default"):
            default_svc = sessions["default"]["vlm_service"]
            return web.Response(
                content_type="application/json",
                text=json.dumps(
                    {
                        "models": [
                            {"id": default_svc.model, "name": default_svc.model, "current": True}
                        ]
                    }
                ),
            )
        return web.Response(
            content_type="application/json", text=json.dumps({"models": [], "error": str(e)})
        )


async def detect_services(request):
    """Detect available local VLM services"""
    services = [
        {"name": "Ollama", "url": "http://localhost:11434/v1", "port": 11434, "path": "/api/tags"},
        {"name": "vLLM", "url": "http://localhost:8000/v1", "port": 8000, "path": "/v1/models"},
        {"name": "SGLang", "url": "http://localhost:30000/v1", "port": 30000, "path": "/v1/models"},
    ]

    detected = []

    async def check_service(service):
        """Check if a service is running by probing its endpoint"""
        try:
            timeout = aiohttp.ClientTimeout(total=1.0)  # 1 second timeout
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"http://localhost:{service['port']}{service['path']}"
                async with session.get(url) as response:
                    if response.status in [200, 404]:  # 404 is ok, means server is running
                        logger.info(f"Detected {service['name']} at {service['url']}")
                        return service
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
        return None

    # Check all services concurrently
    results = await asyncio.gather(*[check_service(s) for s in services])
    detected = [s for s in results if s is not None]

    # Default to NVIDIA API Catalog if no local services found
    if not detected:
        detected.append(
            {
                "name": "NVIDIA API Catalog",
                "url": "https://integrate.api.nvidia.com/v1",
                "port": None,
                "path": None,
                "requires_key": True,
            }
        )

    return web.Response(
        content_type="application/json",
        text=json.dumps({"detected": detected, "default": detected[0] if detected else None}),
    )


async def websocket_handler(request):
    """Handle WebSocket connections for text updates. Supports ?session_id= for multi-session."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    # Session ID from query or generate new.
    session_id = request.query.get("session_id", "").strip() or str(uuid.uuid4())
    ws_to_session[ws] = session_id
    session_websockets[session_id].add(ws)
    websockets.add(ws)
    disconnect_task = session_disconnect_cleanup_tasks.pop(session_id, None)
    if disconnect_task is not None:
        disconnect_task.cancel()
    logger.info(
        f"WebSocket client connected. session_id={session_id}, total clients: {len(websockets)}"
    )

    session = get_or_create_session(session_id)
    svc = session["vlm_service"]

    try:
        # Send initial message with current server configuration (include session_id if we generated it)
        await ws.send_json(
            {
                "type": "status",
                "text": "Connected to server",
                "status": "Ready",
                "session_id": session_id,
            }
        )

        # Send current server configuration for this session
        from .video_processor import VideoProcessorTrack as _VPT

        background_service = session.get("background_service")
        await ws.send_json(
            {
                "type": "server_config",
                "model": svc.model,
                "api_base": svc.api_base,
                "prompt": svc.prompt,
                "process_interval": _VPT.process_interval_seconds,
                "frames_per_batch": _VPT.frames_per_batch,
                "background_model": background_service.get_config()
                if background_service
                else None,
                "session_id": session_id,
            }
        )

        # Keep connection alive and handle incoming messages
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    # Re-resolve session in case it was recreated
                    svc = get_or_create_session(session_id)["vlm_service"]

                    if data.get("type") == "update_prompt":
                        new_prompt = data.get("prompt", "").strip()
                        if svc:
                            svc.update_prompt(new_prompt)
                            logger.info(f"[{session_id}] Prompt updated: {new_prompt}")

                            await ws.send_json(
                                {
                                    "type": "prompt_updated",
                                    "prompt": new_prompt,
                                }
                            )

                    elif data.get("type") == "update_model":
                        new_model = data.get("model", "").strip()
                        api_base = data.get("api_base", "").strip()
                        api_key = data.get("api_key", "").strip()

                        if new_model and svc:
                            svc.model = new_model
                            if api_base:
                                svc.update_api_settings(api_base, api_key if api_key else None)
                                bg_svc = get_background_service(session_id)
                                if bg_svc:
                                    bg_svc.update_summary_api(api_base=svc.api_base)
                                logger.info(
                                    f"[{session_id}] Model updated: {new_model}, API: {api_base}"
                                )
                            else:
                                logger.info(f"[{session_id}] Model updated: {new_model}")

                            await ws.send_json(
                                {
                                    "type": "model_updated",
                                    "model": new_model,
                                    "api_base": svc.api_base,
                                }
                            )

                    elif data.get("type") == "update_processing":
                        interval_sec = data.get("process_interval", 1.0)
                        try:
                            interval_sec = float(interval_sec)
                            if 0.1 <= interval_sec <= 60.0:
                                from .video_processor import VideoProcessorTrack

                                old_value = VideoProcessorTrack.process_interval_seconds
                                VideoProcessorTrack.process_interval_seconds = interval_sec
                                bg_svc = get_background_service(session_id)
                                if bg_svc:
                                    bg_svc.set_foreground_sampling(
                                        process_interval_seconds=interval_sec,
                                        frames_per_batch=VideoProcessorTrack.frames_per_batch,
                                    )
                                logger.info(
                                    f"[{session_id}] Processing interval updated: {old_value} → {interval_sec}s"
                                )

                                await ws.send_json(
                                    {
                                        "type": "processing_updated",
                                        "process_interval": interval_sec,
                                        "background_model": bg_svc.get_config()
                                        if bg_svc
                                        else None,
                                    }
                                )
                            else:
                                logger.warning(
                                    f"Processing interval out of range (0.1-60): {interval_sec}"
                                )
                        except ValueError:
                            logger.error(f"Invalid processing interval: {interval_sec}")

                    elif data.get("type") == "update_frames_per_batch":
                        fpb = data.get("frames_per_batch", 1)
                        try:
                            fpb = int(fpb)
                            if 1 <= fpb <= 30:
                                from .video_processor import VideoProcessorTrack

                                old_value = VideoProcessorTrack.frames_per_batch
                                VideoProcessorTrack.frames_per_batch = fpb
                                bg_svc = get_background_service(session_id)
                                if bg_svc:
                                    bg_svc.set_foreground_sampling(
                                        process_interval_seconds=VideoProcessorTrack.process_interval_seconds,
                                        frames_per_batch=fpb,
                                    )
                                logger.info(
                                    f"[{session_id}] Frames per batch updated: {old_value} → {fpb}"
                                )

                                await ws.send_json(
                                    {
                                        "type": "frames_per_batch_updated",
                                        "frames_per_batch": fpb,
                                        "background_model": bg_svc.get_config()
                                        if bg_svc
                                        else None,
                                    }
                                )
                            else:
                                logger.warning(
                                    f"Frames per batch out of range (1-30): {fpb}"
                                )
                        except ValueError:
                            logger.error(f"Invalid frames per batch: {fpb}")

                    elif data.get("type") == "update_background_config":
                        bg_svc = get_background_service(session_id)
                        if bg_svc:
                            try:
                                config = bg_svc.update_config(
                                    enabled=data.get("enabled")
                                    if "enabled" in data
                                    else None,
                                    frame_multiplier=data.get("frame_multiplier")
                                    if "frame_multiplier" in data
                                    else None,
                                    max_frames=data.get("max_frames")
                                    if "max_frames" in data
                                    else None,
                                    foreground_fps=data.get("foreground_fps")
                                    if "foreground_fps" in data
                                    else None,
                                    resize_long_edge=data.get("resize_long_edge")
                                    if "resize_long_edge" in data
                                    else None,
                                )
                                logger.info(
                                    "[%s] Background model config updated: %s",
                                    session_id,
                                    config,
                                )
                                await ws.send_json(
                                    {
                                        "type": "background_config_updated",
                                        "background_model": config,
                                    }
                                )
                            except (TypeError, ValueError) as err:
                                await ws.send_json(
                                    {
                                        "type": "background_result_error",
                                        "task_id": "",
                                        "error": f"Invalid background config: {err}",
                                    }
                                )

                    elif data.get("type") == "uploaded_video_frame":
                        upload_id = str(data.get("upload_id") or "").strip()
                        active_upload_id = uploaded_video_livekit_sessions.get(session_id)
                        if not active_upload_id or upload_id != active_upload_id:
                            logger.warning(
                                "[%s] Ignoring uploaded frame for inactive upload_id=%s",
                                session_id,
                                upload_id,
                            )
                            continue
                        await publish_uploaded_browser_frame(
                            session_id,
                            str(data.get("image") or ""),
                            media_time=data.get("media_time"),
                            frame_sequence=data.get("frame_sequence"),
                        )

                    elif data.get("type") == "set_debug":
                        session_data = get_or_create_session(session_id)
                        if "show_request_payload" in data:
                            session_data["show_request_payload"] = bool(
                                data["show_request_payload"]
                            )
                        if "show_response_payload" in data:
                            session_data["show_response_payload"] = bool(
                                data["show_response_payload"]
                            )
                        if "show_memory_state" in data:
                            session_data["show_memory_state"] = bool(
                                data["show_memory_state"]
                            )
                        logger.debug(
                            f"[{session_id}] Debug: request_payload="
                            f"{session_data.get('show_request_payload')}, response_payload="
                            f"{session_data.get('show_response_payload')}, memory_state="
                            f"{session_data.get('show_memory_state')}"
                        )

                    elif data.get("type") == "reset_session":
                        logger.info(f"[{session_id}] Client requested adapter session reset")
                        asyncio.create_task(svc.reset_adapter_session())

                    elif data.get("type") == "cleanup_session":
                        logger.info(f"[{session_id}] Client requested session cleanup")
                        asyncio.create_task(cleanup_session(session_id))
                        await ws.close()
                        break

                    elif data.get("type") == "update_max_latency":
                        max_latency = data.get("max_latency", 0.0)
                        try:
                            max_latency = float(max_latency)
                            if 0 <= max_latency <= 10.0:
                                from .video_processor import VideoProcessorTrack

                                old_value = VideoProcessorTrack.max_frame_latency
                                VideoProcessorTrack.max_frame_latency = max_latency
                                status = "disabled" if max_latency == 0 else f"{max_latency:.1f}s"
                                old_status = "disabled" if old_value == 0 else f"{old_value:.1f}s"
                                logger.info(
                                    f"[{session_id}] Max frame latency updated: {old_status} → {status}"
                                )

                                await ws.send_json(
                                    {"type": "max_latency_updated", "max_latency": max_latency}
                                )
                            else:
                                logger.warning(f"Max latency out of range (0-10.0): {max_latency}")
                        except ValueError:
                            logger.error(f"Invalid max latency value: {max_latency}")
                except json.JSONDecodeError:
                    logger.error("Invalid JSON from client")
                except Exception as e:
                    logger.error(f"Error handling client message: {e}")
            elif msg.type == web.WSMsgType.ERROR:
                logger.error(f"WebSocket error: {ws.exception()}")
    finally:
        session_sockets = session_websockets.get(session_id)
        if session_sockets is not None:
            session_sockets.discard(ws)
            if not session_sockets:
                session_websockets.pop(session_id, None)
        ws_to_session.pop(ws, None)
        websockets.discard(ws)
        logger.info(
            f"WebSocket client disconnected. session_id={session_id}, total clients: {len(websockets)}"
        )
        if (
            not session_websockets.get(session_id)
            and session_id in uploaded_video_livekit_sessions
        ):
            task = asyncio.create_task(_cleanup_disconnected_upload_session(session_id))
            session_disconnect_cleanup_tasks[session_id] = task

    return ws


def broadcast_text_update(text: str, metrics: dict):
    """Broadcast text update and metrics to all connected WebSocket clients"""
    if not websockets:
        return

    message = json.dumps({"type": "vlm_response", "text": text, "metrics": metrics})

    # Send to all connected clients
    dead_websockets = set()
    for ws in websockets:
        try:
            # Use asyncio to send without blocking
            asyncio.create_task(ws.send_str(message))
        except Exception as e:
            logger.error(f"Error sending to websocket: {e}")
            dead_websockets.add(ws)

    # Clean up dead connections
    websockets.difference_update(dead_websockets)


def _livekit_room_name(session_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in session_id)
    return f"joyvl-{safe or 'default'}"


def _livekit_token(identity: str, room_name: str, *, can_publish: bool, can_subscribe: bool) -> str:
    grants = livekit_api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=can_publish,
        can_subscribe=can_subscribe,
        can_publish_data=True,
    )
    return (
        livekit_api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(grants)
        .to_jwt()
    )


class LiveKitSessionWorker:
    """Server-side LiveKit participant that consumes browser video."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.room_name = _livekit_room_name(session_id)
        self.room = livekit_rtc.Room()
        self._closed = asyncio.Event()
        self._tasks = set()
        self._streams = set()

    async def start(self) -> None:
        token = _livekit_token(
            f"server-{self.session_id}",
            self.room_name,
            can_publish=False,
            can_subscribe=True,
        )

        @self.room.on("track_subscribed")
        def on_track_subscribed(track, publication, participant):
            if track.kind != livekit_rtc.TrackKind.KIND_VIDEO:
                return
            if str(participant.identity or "").startswith("rtsp-publisher-"):
                logger.info(
                    "[%s] Ignoring LiveKit RTSP relay track: participant=%s track=%s",
                    self.session_id,
                    participant.identity,
                    publication.sid,
                )
                return
            logger.info(
                "[%s] LiveKit video track subscribed: participant=%s track=%s",
                self.session_id,
                participant.identity,
                publication.sid,
            )
            if self.session_id in uploaded_video_livekit_sessions:
                notify_session_json(
                    self.session_id,
                    {
                        "type": "uploaded_video_status",
                        "session_id": self.session_id,
                        "phase": "track_published",
                        "upload_id": uploaded_video_livekit_sessions[self.session_id],
                        "message": "The browser video track is published to LiveKit.",
                    },
                )
            task = asyncio.create_task(
                self._consume_track(track, participant_identity=str(participant.identity or ""))
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        @self.room.on("disconnected")
        def on_disconnected(reason):
            logger.info("[%s] LiveKit worker disconnected: %s", self.session_id, reason)
            self._closed.set()

        await _connect_livekit_room_without_proxy(
            self.room,
            token,
            livekit_rtc.RoomOptions(auto_subscribe=True),
        )
        logger.info("[%s] LiveKit worker connected to room %s", self.session_id, self.room_name)

    async def _consume_track(self, track, *, participant_identity: str = "") -> None:
        session = get_or_create_session(self.session_id)
        processor = VideoProcessorTrack(
            None,
            session["vlm_service"],
            text_callback=get_session_callback(self.session_id),
            background_service=session.get("background_service"),
        )
        stream = livekit_rtc.VideoStream.from_track(
            track=track,
            format=livekit_rtc.VideoBufferType.RGB24,
            capacity=1,
        )
        self._streams.add(stream)
        first_frame = True
        relay_frame_count = None
        if participant_identity.startswith("upload-relay-"):
            relay = uploaded_video_frame_relays.get(self.session_id)
            relay_frame_count = relay.frame_count if relay is not None else 0
        try:
            async for frame_event in stream:
                if self._closed.is_set():
                    break
                if relay_frame_count is not None:
                    relay = uploaded_video_frame_relays.get(self.session_id)
                    current_count = relay.frame_count if relay is not None else relay_frame_count
                    if current_count <= relay_frame_count:
                        continue
                    relay_frame_count = current_count
                if first_frame:
                    first_frame = False
                    if self.session_id in uploaded_video_livekit_sessions:
                        notify_session_json(
                            self.session_id,
                            {
                                "type": "uploaded_video_status",
                                "session_id": self.session_id,
                                "phase": "analysis_started",
                                "upload_id": uploaded_video_livekit_sessions[self.session_id],
                                "message": "The first displayed video frame reached the analyzer.",
                            },
                        )
                    logger.info("[%s] First LiveKit video frame received", self.session_id)
                await processor.process_livekit_frame(frame_event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[%s] Error consuming LiveKit video track", self.session_id)
        finally:
            processor.stop()
            self._streams.discard(stream)
            try:
                await stream.aclose()
            except Exception:
                logger.debug("[%s] Error closing LiveKit video stream", self.session_id, exc_info=True)

    async def close(self) -> None:
        self._closed.set()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        for stream in list(self._streams):
            try:
                await stream.aclose()
            except Exception:
                pass
        try:
            await self.room.disconnect()
        except Exception:
            logger.debug("[%s] Error disconnecting LiveKit room", self.session_id, exc_info=True)


class BrowserFrameLiveKitRelay:
    """Publish browser-rendered upload frames into the local LiveKit room."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.room_name = _livekit_room_name(session_id)
        self.room = livekit_rtc.Room()
        self.video_source = None
        self.video_track = None
        self.publication = None
        self.width = 0
        self.height = 0
        self.frame_count = 0
        self._closed = False

    async def start(self, width: int, height: int) -> None:
        if self._closed or self.video_source is not None:
            return

        token = _livekit_token(
            f"upload-relay-{self.session_id}",
            self.room_name,
            can_publish=True,
            can_subscribe=False,
        )
        await _connect_livekit_room_without_proxy(
            self.room,
            token,
            livekit_rtc.RoomOptions(auto_subscribe=False),
        )
        self.width = int(width)
        self.height = int(height)
        self.video_source = livekit_rtc.VideoSource(self.width, self.height)
        self.video_track = livekit_rtc.LocalVideoTrack.create_video_track(
            "uploaded-browser-video",
            self.video_source,
        )
        publish_options = livekit_rtc.TrackPublishOptions()
        publish_options.source = livekit_rtc.TrackSource.SOURCE_CAMERA
        self.publication = await self.room.local_participant.publish_track(
            self.video_track,
            publish_options,
        )
        logger.info(
            "[%s] Browser-frame LiveKit relay published track=%s %sx%s",
            self.session_id,
            self.publication.sid,
            self.width,
            self.height,
        )
        notify_session_json(
            self.session_id,
            {
                "type": "uploaded_video_status",
                "session_id": self.session_id,
                "phase": "relay_ready",
                "upload_id": uploaded_video_livekit_sessions.get(self.session_id),
                "message": "The 7099 browser-frame relay is connected to LiveKit.",
            },
        )

    def publish_rgb(self, rgb_bytes: bytes, width: int, height: int) -> None:
        if self._closed or self.video_source is None:
            raise RuntimeError("Browser-frame LiveKit relay is not ready")
        if int(width) != self.width or int(height) != self.height:
            raise ValueError(
                f"Uploaded frame size changed from {self.width}x{self.height} "
                f"to {width}x{height}"
            )
        frame = livekit_rtc.VideoFrame(
            self.width,
            self.height,
            livekit_rtc.VideoBufferType.RGB24,
            rgb_bytes,
        )
        self.video_source.capture_frame(
            frame,
            timestamp_us=time.monotonic_ns() // 1000,
        )
        self.frame_count += 1

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            if self.publication is not None:
                await self.room.local_participant.unpublish_track(self.publication.sid)
        with contextlib.suppress(Exception):
            if self.video_source is not None:
                await self.video_source.aclose()
        with contextlib.suppress(Exception):
            await self.room.disconnect()


def _decode_uploaded_browser_frame(image_data: str) -> tuple[bytes, int, int]:
    prefix = "data:image/jpeg;base64,"
    if not image_data.startswith(prefix):
        raise ValueError("Uploaded browser frame must be a JPEG data URL")
    encoded = image_data[len(prefix) :]
    max_encoded = ((VIDEO_UPLOAD_FRAME_MAX_BYTES + 2) // 3) * 4 + 8
    if not encoded or len(encoded) > max_encoded:
        raise ValueError("Uploaded browser frame exceeds the encoded size limit")
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > VIDEO_UPLOAD_FRAME_MAX_BYTES:
        raise ValueError("Uploaded browser frame exceeds the decoded size limit")

    with Image.open(io.BytesIO(raw)) as image:
        width, height = image.size
        if width <= 0 or height <= 0 or width * height > VIDEO_UPLOAD_FRAME_MAX_PIXELS:
            raise ValueError("Uploaded browser frame dimensions are invalid")
        rgb = image.convert("RGB")
        return rgb.tobytes(), width, height


async def publish_uploaded_browser_frame(
    session_id: str,
    image_data: str,
    *,
    media_time=None,
    frame_sequence=None,
) -> None:
    relay = uploaded_video_frame_relays.get(session_id)
    if relay is None:
        raise RuntimeError("Uploaded browser-frame relay is not active")
    rgb_bytes, width, height = await asyncio.to_thread(
        _decode_uploaded_browser_frame,
        image_data,
    )
    relay.publish_rgb(rgb_bytes, width, height)
    if relay.frame_count == 1 or relay.frame_count % 30 == 0:
        logger.info(
            "[%s] Browser upload frame relayed count=%s media_time=%s sequence=%s",
            session_id,
            relay.frame_count,
            media_time,
            frame_sequence,
        )


async def stop_uploaded_frame_relay(session_id: str) -> bool:
    relay = uploaded_video_frame_relays.pop(session_id, None)
    if relay is None:
        return False
    await relay.close()
    logger.info(
        "[%s] Browser-frame LiveKit relay stopped after %s frames",
        session_id,
        relay.frame_count,
    )
    return True


class RTSPToLiveKitRelay:
    """Publish decoded RTSP frames to the browser through LiveKit/WebRTC."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.room_name = _livekit_room_name(session_id)
        self.room = livekit_rtc.Room()
        self.video_source = None
        self.video_track = None
        self.publication = None
        self.width = 0
        self.height = 0
        self._closed = False
        self._start_lock = asyncio.Lock()

    async def start(self, width: int, height: int) -> None:
        async with self._start_lock:
            if self._closed or self.video_source is not None:
                return

            token = _livekit_token(
                f"rtsp-publisher-{self.session_id}",
                self.room_name,
                can_publish=True,
                can_subscribe=False,
            )
            await _connect_livekit_room_without_proxy(
                self.room,
                token,
                livekit_rtc.RoomOptions(auto_subscribe=False),
            )

            self.width = width
            self.height = height
            self.video_source = livekit_rtc.VideoSource(width, height)
            self.video_track = livekit_rtc.LocalVideoTrack.create_video_track(
                "uploaded-rtsp-video",
                self.video_source,
            )
            publish_options = livekit_rtc.TrackPublishOptions()
            publish_options.source = livekit_rtc.TrackSource.SOURCE_CAMERA
            self.publication = await self.room.local_participant.publish_track(
                self.video_track,
                publish_options,
            )
            logger.info(
                "[%s] RTSP LiveKit relay published track=%s %sx%s",
                self.session_id,
                self.publication.sid,
                width,
                height,
            )

    async def publish_frame(self, frame) -> None:
        if self._closed:
            return

        rgb = frame.to_ndarray(format="rgb24")
        height, width = rgb.shape[:2]
        if self.video_source is None:
            await self.start(width, height)

        if self._closed or self.video_source is None:
            return
        if width != self.width or height != self.height:
            logger.warning(
                "[%s] Skipping RTSP relay frame with changed resolution %sx%s, expected %sx%s",
                self.session_id,
                width,
                height,
                self.width,
                self.height,
            )
            return

        video_frame = livekit_rtc.VideoFrame(
            width,
            height,
            livekit_rtc.VideoBufferType.RGB24,
            rgb.tobytes(),
        )
        self.video_source.capture_frame(video_frame, timestamp_us=int(time.time() * 1_000_000))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        with contextlib.suppress(Exception):
            if self.publication is not None:
                await self.room.local_participant.unpublish_track(self.publication.sid)
        with contextlib.suppress(Exception):
            if self.video_source is not None:
                await self.video_source.aclose()
        with contextlib.suppress(Exception):
            await self.room.disconnect()


async def ensure_livekit_worker(session_id: str) -> LiveKitSessionWorker:
    worker = livekit_workers.get(session_id)
    if worker is not None:
        return worker
    worker = LiveKitSessionWorker(session_id)
    await worker.start()
    livekit_workers[session_id] = worker
    return worker


async def stop_livekit_worker(session_id: str) -> None:
    worker = livekit_workers.pop(session_id, None)
    if worker is not None:
        await worker.close()


async def livekit_token(request):
    """Issue a LiveKit token for the browser participant and start the server worker."""
    try:
        data = await request.json()
    except Exception:
        data = {}

    session_id = str(data.get("session_id") or request.query.get("session_id") or "default")
    session_id = session_id.strip() or "default"
    role = str(data.get("role") or request.query.get("role") or "publisher").strip().lower()
    subscriber_only = role in {"subscriber", "playback", "viewer"}
    get_or_create_session(session_id)

    if not subscriber_only:
        try:
            await ensure_livekit_worker(session_id)
            if session_id in uploaded_video_livekit_sessions:
                notify_session_json(
                    session_id,
                    {
                        "type": "uploaded_video_status",
                        "session_id": session_id,
                        "phase": "livekit_connected",
                        "upload_id": uploaded_video_livekit_sessions[session_id],
                        "message": "The upload analysis worker is connected to LiveKit.",
                    },
                )
        except Exception as e:
            logger.error("[%s] Failed to start LiveKit worker: %s", session_id, e, exc_info=True)
            return web.Response(
                status=503,
                content_type="application/json",
                text=json.dumps({"error": f"LiveKit worker unavailable: {e}"}),
            )

    room_name = _livekit_room_name(session_id)
    token = _livekit_token(
        f"browser-{session_id}",
        room_name,
        can_publish=not subscriber_only,
        can_subscribe=subscriber_only,
    )
    scheme = "wss" if request.scheme == "https" else "ws"
    public_url = f"{scheme}://{request.host}{LIVEKIT_PUBLIC_PATH}"
    return web.Response(
        content_type="application/json",
        text=json.dumps(
            {
                "url": public_url,
                "token": token,
                "room": room_name,
                "session_id": session_id,
            }
        ),
    )


def _livekit_proxy_url(request) -> str:
    suffix = request.match_info.get("tail", "")
    path = f"/{suffix}" if suffix else "/"
    query = request.rel_url.query_string
    url = f"http://127.0.0.1:8298{path}"
    if query:
        url += f"?{query}"
    return url


async def livekit_proxy(request):
    """Proxy LiveKit HTTP and WebSocket signaling through the WebUI port."""
    target_url = _livekit_proxy_url(request)
    if request.headers.get("Upgrade", "").lower() == "websocket":
        ws_client = web.WebSocketResponse()
        await ws_client.prepare(request)
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower()
            not in {
                "host",
                "upgrade",
                "connection",
                "sec-websocket-key",
                "sec-websocket-version",
                "sec-websocket-extensions",
                "sec-websocket-protocol",
            }
        }
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(target_url, headers=headers) as ws_server:
                async def pump_client_to_server():
                    try:
                        async for msg in ws_client:
                            if msg.type == web.WSMsgType.TEXT:
                                await ws_server.send_str(msg.data)
                            elif msg.type == web.WSMsgType.BINARY:
                                await ws_server.send_bytes(msg.data)
                            elif msg.type == web.WSMsgType.CLOSE:
                                await ws_server.close()
                    except (aiohttp.ClientConnectionResetError, ConnectionResetError):
                        pass

                async def pump_server_to_client():
                    try:
                        async for msg in ws_server:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await ws_client.send_str(msg.data)
                            elif msg.type == aiohttp.WSMsgType.BINARY:
                                await ws_client.send_bytes(msg.data)
                            elif msg.type == aiohttp.WSMsgType.CLOSE:
                                await ws_client.close()
                    except (aiohttp.ClientConnectionResetError, ConnectionResetError):
                        pass

                tasks = [
                    asyncio.create_task(pump_client_to_server()),
                    asyncio.create_task(pump_server_to_client()),
                ]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*done, *pending, return_exceptions=True)
        return ws_client

    body = await request.read()
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length", "transfer-encoding"}
    }
    async with aiohttp.ClientSession() as session:
        async with session.request(
            request.method,
            target_url,
            data=body if body else None,
            headers=headers,
            allow_redirects=False,
        ) as resp:
            response_headers = {
                key: value
                for key, value in resp.headers.items()
                if key.lower() not in {"content-length", "transfer-encoding", "connection"}
            }
            return web.Response(
                status=resp.status,
                headers=response_headers,
                body=await resp.read(),
            )


async def session_cleanup(request):
    """Cancel active VLM work and remove a session."""
    try:
        data = await request.json()
    except Exception:
        data = {}

    session_id = (data.get("session_id") or "").strip()
    if not session_id:
        return web.Response(
            status=400,
            content_type="application/json",
            text=json.dumps({"error": "Missing session_id parameter"}),
        )

    reset_adapter = bool(data.get("reset_adapter", True))
    result = await cleanup_session(session_id, reset_adapter=reset_adapter)
    return web.Response(content_type="application/json", text=json.dumps(result))


def _safe_token(value: str, default: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._-")
    return (safe[:80] or default)


def _json_response(payload: dict, *, status: int = 200) -> web.Response:
    return web.Response(
        status=status,
        content_type="application/json",
        text=json.dumps(payload, ensure_ascii=False),
    )


def _stderr_tail(stderr_lines: deque, max_chars: int = 1600) -> str:
    text = "\n".join(str(line) for line in stderr_lines if line)
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _uploaded_rtsp_url(session_id: str, upload_id: str) -> str:
    safe_session = _safe_token(session_id, "default")
    safe_upload = _safe_token(upload_id, "upload")
    return f"{VIDEO_UPLOAD_RTSP_BASE}/joyvl-{safe_session}-{safe_upload}"


def _format_seconds(value: float) -> str:
    text = f"{max(0.0, float(value)):.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _format_ffmpeg_rate(rate) -> str:
    if rate:
        numerator = getattr(rate, "numerator", None)
        denominator = getattr(rate, "denominator", None)
        if numerator and denominator:
            return f"{numerator}/{denominator}"
        try:
            fps = float(rate)
            if fps > 0:
                return _format_seconds(fps)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return "25"


def _probe_uploaded_video(upload: UploadedVideo) -> dict:
    try:
        import av

        with av.open(str(upload.path)) as container:
            if not container.streams.video:
                raise ValueError("No video stream found")
            stream = container.streams.video[0]
            codec_context = stream.codec_context
            width = int(stream.width or codec_context.width or 0)
            height = int(stream.height or codec_context.height or 0)
            if width <= 0 or height <= 0:
                raise ValueError("Video dimensions are unknown")
            return {
                "codec": codec_context.name,
                "width": width,
                "height": height,
                "fps": _format_ffmpeg_rate(
                    getattr(stream, "average_rate", None) or getattr(stream, "base_rate", None)
                ),
            }
    except Exception as err:
        raise RuntimeError(f"Unable to probe uploaded video: {err}") from err


def _fit_uploaded_capture_size(width: int, height: int) -> tuple[int, int]:
    scale = min(1.0, 1280 / max(1, width), 720 / max(1, height))
    fitted_width = max(2, int(round(width * scale)))
    fitted_height = max(2, int(round(height * scale)))
    return fitted_width, fitted_height


def _build_uploaded_copy_ffmpeg_cmd(upload: UploadedVideo, rtsp_url: str) -> list[str]:
    return [
        VIDEO_UPLOAD_FFMPEG_BIN,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "warning",
        "-re",
        "-stream_loop",
        "-1",
        "-i",
        str(upload.path),
        "-map",
        "0:v:0",
        "-an",
        "-c:v",
        "copy",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
        rtsp_url,
    ]


def _build_uploaded_ffmpeg_cmd(
    upload: UploadedVideo,
    rtsp_url: str,
    *,
    video_info: dict | None = None,
) -> tuple[list[str], float]:
    preroll_seconds = VIDEO_UPLOAD_PREROLL_SECONDS
    real_cmd = _build_uploaded_copy_ffmpeg_cmd(upload, rtsp_url)
    if preroll_seconds <= 0:
        return real_cmd, 0.0

    return [
        VIDEO_UPLOAD_FFMPEG_BIN,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "warning",
        "-re",
        "-stream_loop",
        "-1",
        "-i",
        str(upload.path),
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        f"tpad=start_duration={_format_seconds(preroll_seconds)}:color=black",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
        rtsp_url,
    ], preroll_seconds


def _is_near_black_frame(frame) -> bool:
    try:
        rgb = frame.to_ndarray(format="rgb24")
        if rgb.size == 0:
            return False
        y_step = max(1, rgb.shape[0] // 120)
        x_step = max(1, rgb.shape[1] // 120)
        sample = rgb[::y_step, ::x_step]
        return float(sample.mean()) <= VIDEO_UPLOAD_PREROLL_BLACK_THRESHOLD
    except Exception:
        logger.debug("Unable to inspect RTSP preroll frame", exc_info=True)
        return False


async def _capture_ffmpeg_stderr(record: UploadedVideoSession) -> None:
    stream = record.process.stderr
    if stream is None:
        return

    try:
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                record.stderr_lines.append(text)
                logger.debug("[%s] ffmpeg: %s", record.session_id, text)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("[%s] Error reading ffmpeg stderr", record.session_id, exc_info=True)


async def _start_uploaded_ffmpeg(
    session_id: str,
    upload: UploadedVideo,
    rtsp_url: str,
    *,
    video_info: dict | None = None,
) -> UploadedVideoSession:
    cmd, preroll_seconds = _build_uploaded_ffmpeg_cmd(upload, rtsp_url, video_info=video_info)

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=(os.name == "posix"),
        )
    except FileNotFoundError as err:
        raise RuntimeError(
            f"ffmpeg not found: {VIDEO_UPLOAD_FFMPEG_BIN}. Set VIDEO_UPLOAD_FFMPEG_BIN."
        ) from err

    record = UploadedVideoSession(
        session_id=session_id,
        upload_id=upload.upload_id,
        rtsp_url=rtsp_url,
        process=process,
        stderr_lines=deque(maxlen=50),
        stderr_task=None,
        preroll_seconds=preroll_seconds,
    )
    record.stderr_task = asyncio.create_task(_capture_ffmpeg_stderr(record))
    if preroll_seconds > 0:
        notify_session_json(
            session_id,
            {
                "type": "uploaded_video_status",
                "session_id": session_id,
                "phase": "preroll_started",
                "preroll_seconds": preroll_seconds,
                "message": (
                    f"RTSP preroll started with {preroll_seconds:g}s of black video; "
                    "analysis count is paused until real frames arrive."
                ),
            },
        )
    else:
        notify_session_json(
            session_id,
            {
                "type": "uploaded_video_status",
                "session_id": session_id,
                "phase": "streaming_started",
                "preroll_seconds": 0,
                "message": "Uploaded video RTSP streaming started.",
            },
        )
    logger.info(
        "[%s] Started uploaded-video ffmpeg pid=%s upload_id=%s rtsp=%s preroll=%ss",
        session_id,
        process.pid,
        upload.upload_id,
        rtsp_url,
        preroll_seconds,
    )
    return record


def _signal_ffmpeg_process(process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
    if process.returncode is not None:
        return
    if os.name == "posix":
        os.killpg(process.pid, sig)
    elif sig == signal.SIGTERM:
        process.terminate()
    else:
        process.kill()


async def _terminate_uploaded_ffmpeg(record: UploadedVideoSession) -> dict:
    process = record.process
    killed = False

    if process.returncode is None:
        try:
            _signal_ffmpeg_process(process, signal.SIGTERM)
        except ProcessLookupError:
            pass

        try:
            await asyncio.wait_for(process.wait(), timeout=VIDEO_UPLOAD_TERMINATE_TIMEOUT)
        except asyncio.TimeoutError:
            killed = True
            try:
                _signal_ffmpeg_process(process, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()

    if record.stderr_task:
        record.stderr_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await record.stderr_task

    return {
        "ffmpeg_stopped": True,
        "ffmpeg_killed": killed,
        "ffmpeg_returncode": process.returncode,
    }


async def _stop_uploaded_video_session(session_id: str) -> dict:
    record = video_ffmpeg_sessions.pop(session_id, None)
    if record is None:
        return {"ffmpeg_stopped": False}

    result = await _terminate_uploaded_ffmpeg(record)
    logger.info(
        "[%s] Uploaded-video ffmpeg stopped upload_id=%s returncode=%s killed=%s",
        session_id,
        record.upload_id,
        result.get("ffmpeg_returncode"),
        result.get("ffmpeg_killed"),
    )
    return result


async def _open_rtsp_track(
    session_id: str,
    rtsp_url: str,
    *,
    connect_timeout: float = 0.0,
    ffmpeg_record: UploadedVideoSession | None = None,
) -> RTSPVideoTrack:
    deadline = time.time() + max(0.0, connect_timeout)
    last_error: Exception | None = None

    while True:
        if ffmpeg_record and ffmpeg_record.process.returncode is not None:
            stderr = _stderr_tail(ffmpeg_record.stderr_lines)
            message = f"ffmpeg exited before RTSP became available: {ffmpeg_record.process.returncode}"
            if stderr:
                message = f"{message}\n{stderr}"
            raise RuntimeError(message)

        try:
            return RTSPVideoTrack(rtsp_url)
        except Exception as err:
            last_error = err
            if connect_timeout <= 0 or time.time() >= deadline:
                raise last_error
            await asyncio.sleep(0.25)


async def _start_rtsp_processing(
    session_id: str,
    rtsp_url: str,
    *,
    connect_timeout: float = 0.0,
    ffmpeg_record: UploadedVideoSession | None = None,
    publish_to_livekit: bool = False,
    livekit_relay: RTSPToLiveKitRelay | None = None,
    skip_initial_black: bool = False,
    preroll_seconds: float = 0.0,
) -> dict:
    if session_id in rtsp_tracks:
        logger.warning("[%s] RTSP session already exists, stopping it first", session_id)
        await _stop_rtsp_session(session_id)

    logger.info("[%s] Starting RTSP processing for %s", session_id, rtsp_url)
    rtsp_track = await _open_rtsp_track(
        session_id,
        rtsp_url,
        connect_timeout=connect_timeout,
        ffmpeg_record=ffmpeg_record,
    )

    session = get_or_create_session(session_id)
    processor_track = VideoProcessorTrack(
        rtsp_track,
        session["vlm_service"],
        text_callback=get_session_callback(session_id),
        background_service=session.get("background_service"),
    )
    if livekit_relay is None and publish_to_livekit:
        livekit_relay = RTSPToLiveKitRelay(session_id)
    preroll_state = {
        "enabled": bool(skip_initial_black),
        "analysis_started": not bool(skip_initial_black),
        "preroll_seconds": max(0.0, float(preroll_seconds or 0.0)),
        "frames_skipped": 0,
        "first_frame_wall_time": None,
        "max_skip_seconds": max(
            0.0,
            float(preroll_seconds or 0.0) + VIDEO_UPLOAD_PREROLL_MAX_EXTRA_SECONDS,
        ),
    }
    stats = rtsp_track.get_stats()
    if livekit_relay is not None:
        width = int(stats.get("width") or 0)
        height = int(stats.get("height") or 0)
        if width > 0 and height > 0:
            await livekit_relay.start(width, height)

    async def consume_frames():
        try:
            while not rtsp_track._stopped:
                try:
                    frame = await rtsp_track.recv()
                    if livekit_relay is not None:
                        await livekit_relay.publish_frame(frame)

                    if preroll_state["enabled"] and not preroll_state["analysis_started"]:
                        now = time.time()
                        if preroll_state["first_frame_wall_time"] is None:
                            preroll_state["first_frame_wall_time"] = now
                        elapsed = now - preroll_state["first_frame_wall_time"]
                        near_black = _is_near_black_frame(frame)
                        if near_black and elapsed <= preroll_state["max_skip_seconds"]:
                            preroll_state["frames_skipped"] += 1
                            continue

                        preroll_state["analysis_started"] = True
                        notify_session_json(
                            session_id,
                            {
                                "type": "uploaded_video_status",
                                "session_id": session_id,
                                "phase": "analysis_started",
                                "preroll_seconds": preroll_state["preroll_seconds"],
                                "frames_skipped": preroll_state["frames_skipped"],
                                "message": (
                                    "Real uploaded video frames reached the analyzer; "
                                    "count will start from the next VLM result."
                                ),
                            },
                        )
                        logger.info(
                            "[%s] Uploaded-video preroll finished after skipping %s frames",
                            session_id,
                            preroll_state["frames_skipped"],
                        )

                    await processor_track.process_av_frame(frame)
                except StopAsyncIteration:
                    logger.info("[%s] RTSP stream ended", session_id)
                    break
                except Exception as e:
                    logger.error("[%s] Error consuming RTSP frame: %s", session_id, e)
                    break
        finally:
            if livekit_relay is not None:
                await livekit_relay.close()
            logger.info("[%s] Frame consumption stopped", session_id)

    frame_task = asyncio.create_task(consume_frames())
    rtsp_tracks[session_id] = (
        rtsp_track,
        processor_track,
        frame_task,
        livekit_relay,
        preroll_state,
    )

    logger.info(
        "[%s] RTSP processing started: %s %sx%s",
        session_id,
        stats.get("codec"),
        stats.get("width"),
        stats.get("height"),
    )
    return stats


async def video_upload(request):
    """Receive a user-selected video file and store it under /tmp without starting a session."""
    if request.content_type != "multipart/form-data":
        return _json_response({"error": "Expected multipart/form-data upload"}, status=400)

    upload_id = uuid.uuid4().hex
    upload_dir = VIDEO_UPLOAD_ROOT / upload_id
    upload_dir.mkdir(parents=True, exist_ok=False)

    session_id = "default"
    original_name = ""
    content_type = ""
    size_bytes = 0
    dest_path: Path | None = None

    try:
        reader = await request.multipart()
        while True:
            part = await reader.next()
            if part is None:
                break

            if part.name == "session_id":
                session_id = _safe_token((await part.text()).strip(), "default")
                continue

            if part.name not in {"file", "video"}:
                await part.release()
                continue

            original_name = Path(part.filename or "video.mp4").name
            suffix = Path(original_name).suffix.lower()
            if suffix not in VIDEO_UPLOAD_ALLOWED_SUFFIXES:
                shutil.rmtree(upload_dir, ignore_errors=True)
                return _json_response(
                    {
                        "error": (
                            "Unsupported video extension. Allowed: "
                            + ", ".join(sorted(VIDEO_UPLOAD_ALLOWED_SUFFIXES))
                        )
                    },
                    status=400,
                )

            content_type = part.headers.get("Content-Type", "")
            dest_path = upload_dir / f"video{suffix}"
            with dest_path.open("wb") as out:
                while True:
                    chunk = await part.read_chunk(VIDEO_UPLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    size_bytes += len(chunk)
                    if size_bytes > VIDEO_UPLOAD_MAX_BYTES:
                        shutil.rmtree(upload_dir, ignore_errors=True)
                        return _json_response(
                            {
                                "error": (
                                    f"Video upload exceeds limit "
                                    f"({VIDEO_UPLOAD_MAX_BYTES} bytes)"
                                )
                            },
                            status=413,
                        )
                    out.write(chunk)
            break

        if dest_path is None or not dest_path.exists() or size_bytes <= 0:
            shutil.rmtree(upload_dir, ignore_errors=True)
            return _json_response({"error": "Missing video file"}, status=400)

        uploaded = UploadedVideo(
            upload_id=upload_id,
            path=dest_path,
            original_name=original_name,
            content_type=content_type,
            size_bytes=size_bytes,
            owner_session_id=session_id,
            created_at=time.time(),
        )
        uploaded_videos[upload_id] = uploaded
        logger.info(
            "[%s] Uploaded video upload_id=%s path=%s bytes=%s",
            session_id,
            upload_id,
            dest_path,
            size_bytes,
        )
        return _json_response(
            {
                "status": "uploaded",
                "upload_id": upload_id,
                "filename": original_name,
                "size_bytes": size_bytes,
                "path": str(dest_path),
                "session_id": session_id,
            }
        )
    except ValueError as err:
        shutil.rmtree(upload_dir, ignore_errors=True)
        logger.warning("[%s] Invalid video upload request: %s", session_id, err)
        return _json_response({"error": "Invalid multipart upload"}, status=400)
    except Exception:
        shutil.rmtree(upload_dir, ignore_errors=True)
        logger.exception("[%s] Video upload failed", session_id)
        return _json_response({"error": "Video upload failed"}, status=500)


async def video_start(request):
    """Bind an uploaded video to a browser-published LiveKit session."""
    try:
        data = await request.json()
    except Exception:
        data = {}

    session_id = _safe_token(str(data.get("session_id") or "default").strip(), "default")
    upload_id = str(data.get("upload_id") or "").strip()
    if not upload_id:
        return _json_response({"error": "Missing upload_id"}, status=400)

    async with upload_session_lifecycle_locks[session_id]:
        upload = uploaded_videos.get(upload_id)
        if upload is None or not upload.path.exists():
            return _json_response({"error": "Uploaded video not found"}, status=404)

        if session_id in video_ffmpeg_sessions:
            await _stop_uploaded_video_session(session_id)
        if session_id in rtsp_tracks:
            await _stop_rtsp_session(session_id)

        await stop_uploaded_frame_relay(session_id)
        if session_id in livekit_workers:
            await stop_livekit_worker(session_id)

        get_or_create_session(session_id)
        uploaded_video_livekit_sessions[session_id] = upload_id
        notify_session_json(
            session_id,
            {
                "type": "uploaded_video_status",
                "session_id": session_id,
                "phase": "local_media_ready",
                "upload_id": upload_id,
                "message": "The uploaded video is ready for browser playback.",
            },
        )
        try:
            video_info = _probe_uploaded_video(upload)
            capture_width, capture_height = _fit_uploaded_capture_size(
                int(video_info["width"]),
                int(video_info["height"]),
            )
            await ensure_livekit_worker(session_id)
            notify_session_json(
                session_id,
                {
                    "type": "uploaded_video_status",
                    "session_id": session_id,
                    "phase": "livekit_connected",
                    "upload_id": upload_id,
                    "message": "The upload analysis worker is connected to LiveKit.",
                },
            )
            relay = BrowserFrameLiveKitRelay(session_id)
            uploaded_video_frame_relays[session_id] = relay
            await relay.start(capture_width, capture_height)
        except Exception as err:
            await stop_uploaded_frame_relay(session_id)
            await stop_livekit_worker(session_id)
            uploaded_video_livekit_sessions.pop(session_id, None)
            logger.error(
                "[%s] Failed to prepare uploaded browser-frame relay: %s",
                session_id,
                err,
                exc_info=True,
            )
            return _json_response(
                {"error": f"Unable to prepare LiveKit upload relay: {err}"},
                status=500,
            )

        logger.info(
            "[%s] Uploaded video bound to 7099 LiveKit relay upload_id=%s path=%s capture=%sx%s",
            session_id,
            upload_id,
            upload.path,
            capture_width,
            capture_height,
        )

    return _json_response(
        {
            "status": "started",
            "session_id": session_id,
            "upload_id": upload_id,
            "transport": "livekit-upload-relay",
            "capture_width": capture_width,
            "capture_height": capture_height,
        }
    )


async def video_stop(request):
    """Stop a browser-published uploaded-video analysis session."""
    try:
        data = await request.json()
    except Exception:
        data = {}

    session_id = _safe_token(str(data.get("session_id") or "default").strip(), "default")
    async with upload_session_lifecycle_locks[session_id]:
        upload_id = uploaded_video_livekit_sessions.pop(session_id, None)
        relay_stopped = await stop_uploaded_frame_relay(session_id)
        ffmpeg_result = await _stop_uploaded_video_session(session_id)
        rtsp_stopped = False
        if session_id in rtsp_tracks:
            await _stop_rtsp_session(session_id)
            rtsp_stopped = True

        livekit_stopped = session_id in livekit_workers
        await stop_livekit_worker(session_id)
        session = sessions.get(session_id)
        cancelled_requests = 0
        if session and session.get("vlm_service"):
            cancelled_requests = await session["vlm_service"].cancel_active_requests()

    logger.info(
        "[%s] Uploaded LiveKit video stopped upload_id=%s cancelled_vlm_tasks=%s",
        session_id,
        upload_id,
        cancelled_requests,
    )

    return _json_response(
        {
            "status": "stopped",
            "session_id": session_id,
            "upload_id": upload_id,
            "transport": "livekit-upload-relay",
            "relay_stopped": relay_stopped,
            "livekit_stopped": livekit_stopped,
            "cancelled_vlm_tasks": cancelled_requests,
            "rtsp_stopped": rtsp_stopped,
            **ffmpeg_result,
        }
    )


async def rtsp_start(request):
    """
    Start RTSP stream processing.

    Accepts RTSP URL and creates a video processing pipeline.

    POST /api/rtsp/start
    Body: {"rtsp_url": "rtsp://...", "session_id": "optional-id"}
    """
    try:
        data = await request.json()
        rtsp_url = data.get("rtsp_url")
        session_id = data.get("session_id", "default")

        if not rtsp_url:
            logger.warning("RTSP start request missing rtsp_url")
            return web.Response(
                status=400,
                content_type="application/json",
                text=json.dumps({"error": "Missing rtsp_url parameter"}),
            )

        try:
            stats = await _start_rtsp_processing(session_id, rtsp_url)
        except Exception as e:
            logger.error(f"Failed to create RTSP track: {e}")
            return web.Response(
                status=500,
                content_type="application/json",
                text=json.dumps({"error": f"Failed to connect to RTSP stream: {str(e)}"}),
            )

        return web.Response(
            content_type="application/json",
            text=json.dumps({"status": "started", "session_id": session_id, "stream_info": stats}),
        )

    except Exception as e:
        logger.error(f"Error starting RTSP: {e}", exc_info=True)
        return web.Response(
            status=500, content_type="application/json", text=json.dumps({"error": str(e)})
        )


async def rtsp_stop(request):
    """
    Stop RTSP stream processing.

    POST /api/rtsp/stop
    Body: {"session_id": "optional-id"}
    """
    try:
        data = await request.json()
        session_id = data.get("session_id", "default")

        await _stop_rtsp_session(session_id)

        return web.Response(
            content_type="application/json",
            text=json.dumps({"status": "stopped", "session_id": session_id}),
        )

    except Exception as e:
        logger.error(f"Error stopping RTSP: {e}", exc_info=True)
        return web.Response(
            status=500, content_type="application/json", text=json.dumps({"error": str(e)})
        )


async def rtsp_status(request):
    """
    Get status of all RTSP streams.

    GET /api/rtsp/status
    """
    try:
        status_list = []

        for session_id, record in rtsp_tracks.items():
            rtsp_track, processor_track, frame_task = record[:3]
            livekit_relay = record[3] if len(record) > 3 else None
            preroll_state = record[4] if len(record) > 4 else None
            stats = rtsp_track.get_stats()
            status_list.append(
                {
                    "session_id": session_id,
                    "connected": stats.get("connected"),
                    "frames_received": stats.get("frames_received"),
                    "livekit_relay": bool(livekit_relay),
                    "preroll": preroll_state,
                    "stream_info": {
                        "codec": stats.get("codec"),
                        "width": stats.get("width"),
                        "height": stats.get("height"),
                        "fps": stats.get("fps"),
                    },
                }
            )

        return web.Response(
            content_type="application/json",
            text=json.dumps({"active_streams": len(rtsp_tracks), "streams": status_list}),
        )

    except Exception as e:
        logger.error(f"Error getting RTSP status: {e}", exc_info=True)
        return web.Response(
            status=500, content_type="application/json", text=json.dumps({"error": str(e)})
        )


async def _stop_rtsp_session(session_id: str):
    """Helper function to stop an RTSP session"""
    if session_id in rtsp_tracks:
        record = rtsp_tracks[session_id]
        rtsp_track, processor_track, frame_task = record[:3]
        livekit_relay = record[3] if len(record) > 3 else None

        # Signal stop first so _read_frame exits early on its next iteration
        rtsp_track._stopped = True

        # Cancel frame consumption task
        if frame_task and not frame_task.done():
            frame_task.cancel()
            try:
                await frame_task
            except asyncio.CancelledError:
                pass

        # Stop tracks (rtsp_track.stop acquires _read_lock to wait for
        # any in-flight executor thread before closing the container)
        try:
            processor_track.stop()
        except Exception as e:
            logger.warning(f"Error stopping processor track: {e}")

        try:
            rtsp_track.stop()
        except Exception as e:
            logger.warning(f"Error stopping RTSP track: {e}")

        if livekit_relay is not None:
            try:
                await livekit_relay.close()
            except Exception as e:
                logger.warning(f"Error stopping RTSP LiveKit relay: {e}")

        # Remove from tracking
        del rtsp_tracks[session_id]
        logger.info(f"RTSP stream stopped: {session_id}")
    else:
        logger.warning(f"RTSP session {session_id} not found")


async def on_startup(app):
    """Initialize resources on server startup"""
    logger.info("Server startup complete")


async def on_shutdown(app):
    """Cleanup on server shutdown"""

    logger.info("Shutting down server...")

    disconnect_tasks = list(session_disconnect_cleanup_tasks.values())
    session_disconnect_cleanup_tasks.clear()
    for task in disconnect_tasks:
        task.cancel()
    if disconnect_tasks:
        await asyncio.gather(*disconnect_tasks, return_exceptions=True)

    # Close all websockets and clear session state
    for ws in list(websockets):
        await ws.close()
    websockets.clear()
    session_websockets.clear()
    ws_to_session.clear()

    for session_id in list(video_ffmpeg_sessions.keys()):
        await _stop_uploaded_video_session(session_id)
    logger.info("Uploaded-video ffmpeg processes closed")

    for session_id in list(uploaded_video_frame_relays.keys()):
        await stop_uploaded_frame_relay(session_id)
    uploaded_video_livekit_sessions.clear()

    # Close all RTSP streams
    for session_id in list(rtsp_tracks.keys()):
        await _stop_rtsp_session(session_id)
    logger.info("RTSP streams closed")

    for session_id in list(livekit_workers.keys()):
        await stop_livekit_worker(session_id)
    logger.info("LiveKit workers closed")

    for session_id, session in list(sessions.items()):
        svc = session.get("vlm_service")
        if svc:
            await svc.close()
        bg_svc = session.get("background_service")
        if bg_svc:
            await bg_svc.close()
        sessions.pop(session_id, None)
    logger.info("VLM sessions closed")

    logger.info("Cleanup complete")


async def create_app(test_mode=False):
    """
    Create and configure the aiohttp web application.

    Args:
        test_mode: If True, use test configuration

    Returns:
        Configured web.Application instance
    """
    # Create web application
    app = web.Application(client_max_size=VIDEO_UPLOAD_MAX_BYTES)
    app.router.add_get("/", index)
    app.router.add_get("/models", models)
    app.router.add_get("/detect-services", detect_services)
    app.router.add_get("/ws", websocket_handler)
    setup_asr_routes(app)
    setup_tts_routes(app)
    setup_local_file_routes(app)
    app.router.add_post("/api/livekit/token", livekit_token)
    app.router.add_route("*", "/livekit/{tail:.*}", livekit_proxy)
    app.router.add_post("/api/session/cleanup", session_cleanup)
    app.router.add_post("/api/video/upload", video_upload)
    app.router.add_post("/api/video/start", video_start)
    app.router.add_post("/api/video/stop", video_stop)

    # RTSP endpoints
    app.router.add_post("/api/rtsp/start", rtsp_start)
    app.router.add_post("/api/rtsp/stop", rtsp_stop)
    app.router.add_get("/api/rtsp/status", rtsp_status)

    # Serve static files (images, etc.)
    # Always serve from static/images within the package (works for both pip and dev installs)
    images_dir = os.path.join(os.path.dirname(__file__), "static", "images")
    images_dir = os.path.abspath(images_dir)

    if os.path.exists(images_dir):
        app.router.add_static("/images", images_dir, name="images")
        logger.info(f"Serving static files from: {images_dir}")
    else:
        logger.warning(f"⚠️  Static images directory not found: {images_dir}")

    # Serve favicon files
    favicon_dir = os.path.join(os.path.dirname(__file__), "static", "favicon")
    favicon_dir = os.path.abspath(favicon_dir)

    if os.path.exists(favicon_dir):
        app.router.add_static("/favicon", favicon_dir, name="favicon")
        logger.info(f"Serving favicon files from: {favicon_dir}")
    else:
        logger.warning(f"⚠️  Favicon directory not found: {favicon_dir}")

    if not test_mode:
        app.on_startup.append(on_startup)
        app.on_shutdown.append(on_shutdown)

    return app


def get_app_config_dir():
    """Get the application config directory following OS conventions"""
    import os
    from pathlib import Path

    # Follow XDG Base Directory spec on Linux, use OS-appropriate paths elsewhere
    if os.name == "posix":
        if "darwin" in os.sys.platform.lower():
            # macOS
            config_dir = Path.home() / "Library" / "Application Support" / "joy-vl-interaction"
        else:
            # Linux/Unix (including Jetson)
            config_dir = (
                Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "joy-vl-interaction"
            )
    else:
        # Windows
        config_dir = Path(os.environ.get("APPDATA", Path.home())) / "joy-vl-interaction"

    # Create directory if it doesn't exist
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def generate_self_signed_cert(cert_path="cert.pem", key_path="key.pem"):
    """Generate a self-signed SSL certificate if it doesn't exist"""
    import subprocess
    import os

    if os.path.exists(cert_path) and os.path.exists(key_path):
        return True

    logger.info("🔐 Generating self-signed SSL certificate...")
    logger.info(f"   Saving to: {os.path.dirname(os.path.abspath(cert_path)) or '.'}")
    try:
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:4096",
                "-nodes",
                "-out",
                cert_path,
                "-keyout",
                key_path,
                "-days",
                "365",
                "-subj",
                "/CN=localhost",
            ],
            check=True,
            capture_output=True,
        )
        logger.info(f"✅ Generated {cert_path} and {key_path}")
        return True
    except FileNotFoundError:
        logger.warning("⚠️  openssl not found - cannot auto-generate certificates")
        logger.warning(
            "⚠️  Install openssl: sudo apt install openssl (Linux) or brew install openssl (Mac)"
        )
        return False
    except subprocess.CalledProcessError as e:
        logger.warning(f"⚠️  Failed to generate certificates: {e}")
        return False


def main():
    """Main entry point"""
    import argparse
    import ssl
    from . import __version__

    parser = argparse.ArgumentParser(
        description="LiveKit Joy VL Interaction - Real-time vision model interaction",
        epilog="Examples:\n"
        "  vLLM:    python -m joy_interaction_webui.server --model llama-3.2-11b-vision-instruct --api-base http://localhost:8000/v1\n"
        "  SGLang:  python -m joy_interaction_webui.server --model llama-3.2-11b-vision-instruct --api-base http://localhost:30000/v1\n"
        "  Ollama:  python -m joy_interaction_webui.server --model llava:7b --api-base http://localhost:11434/v1\n"
        "  HTTPS:   python -m joy_interaction_webui.server --model llava:7b --api-base http://localhost:11434/v1 --ssl-cert cert.pem --ssl-key key.pem",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8090, help="Port to bind to (default: 8090)")
    parser.add_argument(
        "--auto-port",
        action="store_true",
        help="Automatically find available port if default is taken",
    )
    parser.add_argument(
        "--model", help="VLM model name (optional, will auto-detect if not specified)"
    )
    parser.add_argument(
        "--api-base", help="VLM API base URL (optional, will auto-detect or use NVIDIA NGC)"
    )
    parser.add_argument(
        "--api-key",
        default="EMPTY",
        help="API key - use 'EMPTY' for local servers, required for NVIDIA NGC/OpenAI (default: EMPTY)",
    )
    parser.add_argument(
        "--prompt",
        default="",
        help="Initial prompt to send to VLM (default: empty, waits for user input)",
    )
    # Get default SSL cert paths (platform-specific)
    default_config_dir = get_app_config_dir()
    default_cert_path = str(default_config_dir / "cert.pem")
    default_key_path = str(default_config_dir / "key.pem")

    parser.add_argument("--process-interval", type=float, default=1.0, help="Processing interval in seconds (default: 1.0)")
    parser.add_argument("--frames-per-batch", type=int, default=1, help="Number of frames to batch per VLM inference (default: 1). E.g., 2 means capture 2 frames within each process-interval and send them together.")
    parser.add_argument(
        "--ssl-cert",
        default=None,  # Will be set to config dir if not specified
        help=f"Path to SSL certificate file (default: {default_cert_path}, auto-generated if missing)",
    )
    parser.add_argument(
        "--ssl-key",
        default=None,  # Will be set to config dir if not specified
        help=f"Path to SSL private key file (default: {default_key_path}, auto-generated if missing)",
    )
    parser.add_argument(
        "--no-ssl",
        action="store_true",
        help="Disable SSL (not recommended - webcam requires HTTPS)",
    )

    args = parser.parse_args()

    # Cloud deployment: env overrides for default API base, model, and frame interval
    if os.environ.get("LIVE_VLM_API_BASE"):
        if not args.api_base:
            args.api_base = os.environ.get("LIVE_VLM_API_BASE").strip()
            logger.info(f"Using API base from env: {args.api_base}")
    if os.environ.get("LIVE_VLM_DEFAULT_MODEL"):
        if not args.model:
            args.model = os.environ.get("LIVE_VLM_DEFAULT_MODEL").strip()
            logger.info(f"Using default model from env: {args.model}")
    if os.environ.get("LIVE_VLM_PROCESS_INTERVAL"):
        try:
            args.process_interval = float(os.environ.get("LIVE_VLM_PROCESS_INTERVAL"))
            logger.info(f"Using process_interval from env: {args.process_interval}s")
        except ValueError:
            pass
    if os.environ.get("LIVE_VLM_FRAMES_PER_BATCH"):
        try:
            args.frames_per_batch = int(os.environ.get("LIVE_VLM_FRAMES_PER_BATCH"))
            logger.info(f"Using frames_per_batch from env: {args.frames_per_batch}")
        except ValueError:
            pass

    # Set default SSL cert paths to config directory if not specified
    if args.ssl_cert is None:
        config_dir = get_app_config_dir()
        args.ssl_cert = str(config_dir / "cert.pem")
    if args.ssl_key is None:
        config_dir = get_app_config_dir()
        args.ssl_key = str(config_dir / "key.pem")

    # Auto-detect service and model if not specified
    api_base = args.api_base
    model = args.model
    api_key = args.api_key

    if not model or not api_base:
        logger.info("No model/API specified, auto-detecting local services...")
        detected_api_base, detected_model = asyncio.run(detect_local_service_and_model())

        if detected_api_base and detected_model:
            if not api_base:
                api_base = detected_api_base
            if not model:
                model = detected_model
        else:
            # Fall back to NVIDIA NGC
            logger.warning("⚠️  No local VLM service found (Ollama, vLLM, SGLang)")
            logger.info("📡 Falling back to NVIDIA API Catalog")
            logger.info("   You'll need an API key from: https://build.nvidia.com")
            if not api_base:
                api_base = "https://integrate.api.nvidia.com/v1"
            if not model:
                model = (
                    os.environ.get("LIVE_VLM_DEFAULT_MODEL") or "meta/llama-3.2-11b-vision-instruct"
                ).strip()
                if os.environ.get("LIVE_VLM_DEFAULT_MODEL"):
                    logger.info(f"Using default model from env: {model}")
            if api_key == "EMPTY":
                logger.warning("⚠️  API key required for NVIDIA API Catalog")
                logger.warning("   Set with: --api-key YOUR_API_KEY")
                logger.warning("   Or use WebUI to configure API settings after starting")

    # Initialize VLM service and default session for multi-session support
    global vlm_service, default_vlm_config
    vlm_service = VLMService(model=model, api_base=api_base, api_key=api_key, prompt=args.prompt)
    default_vlm_config = {
        "model": model,
        "api_base": api_base,
        "api_key": api_key,
        "prompt": args.prompt,
    }
    sessions["default"] = {
        "vlm_service": vlm_service,
        "background_service": BackgroundModelService(
            session_id="default",
            notify_callback=lambda payload: notify_session_json("default", payload),
            summarizer_api_base=api_base,
        ),
        "show_request_payload": False,
        "show_response_payload": False,
        "show_memory_state": False,
    }

    # Log initialization with better formatting
    service_name = "Local" if "localhost" in api_base or "127.0.0.1" in api_base else "Cloud"
    logger.info("Initialized VLM service:")
    logger.info(f"  Model: {model}")
    logger.info(f"  API: {api_base} ({service_name})")
    logger.info(f"  Prompt: {args.prompt}")

    # Update frame processing rate in VideoProcessorTrack if needed
    # (This is a bit hacky but works for this demo)
    VideoProcessorTrack.process_interval_seconds = args.process_interval
    VideoProcessorTrack.frames_per_batch = args.frames_per_batch

    # Create web application using create_app
    app = asyncio.run(create_app(test_mode=False))

    # Setup SSL (auto-generate certificates if needed)
    ssl_context = None
    protocol = "http"
    if not args.no_ssl:
        # Try to auto-generate if certificates don't exist
        if not os.path.exists(args.ssl_cert) or not os.path.exists(args.ssl_key):
            success = generate_self_signed_cert(args.ssl_cert, args.ssl_key)
            if not success:
                # FAIL FAST - SSL is required for webcam access
                logger.error("")
                logger.error("❌ Cannot start server without SSL certificates")
                logger.error("❌ Webcam access requires HTTPS!")
                logger.error("")
                logger.error("🔧 To fix, install openssl:")
                logger.error("   Linux/Jetson: sudo apt install openssl")
                logger.error("   macOS: brew install openssl")
                logger.error("")
                logger.error("   Then restart the server")
                logger.error("")
                logger.error(
                    "⚠️  Or run with --no-ssl if you don't need camera access (not recommended)"
                )
                logger.error("")
                sys.exit(1)

        # Load certificates (they must exist at this point)
        if os.path.exists(args.ssl_cert) and os.path.exists(args.ssl_key):
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(args.ssl_cert, args.ssl_key)
            protocol = "https"
            logger.info("SSL enabled - using HTTPS")
        else:
            # This should never happen, but just in case
            logger.error("❌ SSL certificates missing after generation - unexpected error")
            sys.exit(1)
    else:
        logger.warning("⚠️  SSL disabled with --no-ssl flag")
        logger.warning("⚠️  Webcam access will NOT work without HTTPS!")

    # Get network addresses
    import socket
    import subprocess

    # Run server
    logger.info(f"Starting server on {args.host}:{args.port}")
    logger.info("")
    logger.info("=" * 70)
    logger.info("Access the server at:")
    logger.info(f"  Local:   {protocol}://localhost:{args.port}")

    # Get network interfaces - try multiple methods for cross-platform support
    network_ips = []

    # Method 1: hostname -I (Linux)
    try:
        result = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=1)
        if result.returncode == 0:
            ips = result.stdout.strip().split()
            for ip in ips:
                # Filter out loopback and docker bridges (172.17.x.x)
                if not ip.startswith("127.") and not ip.startswith("172.17."):
                    network_ips.append(ip)
    except Exception:
        pass

    # Method 2: Socket method (cross-platform fallback)
    if not network_ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            if ip and ip != "127.0.0.1":
                network_ips.append(ip)
        except Exception:
            pass

    # Display all found network IPs
    for ip in network_ips:
        logger.info(f"  Network: {protocol}://{ip}:{args.port}")

    logger.info("=" * 70)
    logger.info("")
    logger.info("Press Ctrl+C to stop")

    # Setup signal handlers for graceful shutdown
    def signal_handler(signum, frame):
        logger.info("\nReceived signal to terminate. Shutting down gracefully...")
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        web.run_app(app, host=args.host, port=args.port, ssl_context=ssl_context)
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}")


def stop():
    """Stop the running joy-vl-interaction server"""
    import sys
    import time

    try:
        import psutil
    except ImportError:
        logger.error("psutil is required for the stop command")
        logger.error("Install it with: pip install joy-vl-interaction[dev]")
        sys.exit(1)

    print("Stopping Joy VL Interaction server...")

    # Find and kill processes running joy_vl_interaction.server
    found = False
    killed = []

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline")
            if cmdline:
                cmdline_str = " ".join(cmdline)
                if "joy_vl_interaction.server" in cmdline_str or "joy-vl-interaction" in cmdline_str:
                    # Don't kill the stop command itself
                    if "stop" not in cmdline_str:
                        found = True
                        print(f"  Stopping process {proc.info['pid']}: {proc.info['name']}")
                        proc.terminate()
                        killed.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    if not found:
        print("✓ No running server found")
        return

    # Wait for graceful shutdown
    time.sleep(2)

    # Force kill if still running
    for proc in killed:
        try:
            if proc.is_running():
                print(f"  Force killing process {proc.pid}")
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # Final verification
    time.sleep(1)
    still_running = False
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline = proc.info.get("cmdline")
            if cmdline:
                cmdline_str = " ".join(cmdline)
                if "joy_vl_interaction.server" in cmdline_str or "joy-vl-interaction" in cmdline_str:
                    if "stop" not in cmdline_str:
                        still_running = True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    if still_running:
        print("❌ Failed to stop server")
        sys.exit(1)
    else:
        print("✓ Server stopped successfully")


if __name__ == "__main__":
    main()
