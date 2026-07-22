import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from openai import APIStatusError
from PIL import Image

from joy_interaction_webui.bailian_config import BAILIAN_API_BASE, BAILIAN_MODEL
from joy_interaction_webui.video_processor import VideoProcessorTrack
from joy_interaction_webui.vlm_service import BAILIAN_DEFAULT_PROMPT, VLMService


class _FakeCompletions:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="中文结果"))],
            model_dump=lambda: {"choices": [{"message": {"content": "中文结果"}}]},
        )


class _FakeAsyncOpenAI:
    instances = []

    def __init__(self, *, base_url, api_key):
        self.base_url = base_url
        self.api_key = api_key
        self.chat = SimpleNamespace(completions=_FakeCompletions())
        self.__class__.instances.append(self)


class VLMServiceProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _FakeAsyncOpenAI.instances.clear()
        self.openai_patcher = patch(
            "joy_interaction_webui.vlm_service.AsyncOpenAI", _FakeAsyncOpenAI
        )
        self.openai_patcher.start()

    def tearDown(self):
        self.openai_patcher.stop()

    async def test_bailian_request_uses_data_url_default_prompt_and_no_local_fields(self):
        service = VLMService(
            model=BAILIAN_MODEL,
            api_base=BAILIAN_API_BASE,
            api_key="sk-fake.for-test-only",
            provider="bailian",
        )

        result = await service.analyze_image(Image.new("RGB", (2, 2), "white"))

        self.assertEqual(result, "中文结果")
        client = _FakeAsyncOpenAI.instances[0]
        self.assertEqual(client.base_url, BAILIAN_API_BASE)
        request = client.chat.completions.calls[0]
        self.assertEqual(request["model"], BAILIAN_MODEL)
        self.assertNotIn("extra_headers", request)
        self.assertNotIn("extra_body", request)
        content = request["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": BAILIAN_DEFAULT_PROMPT})
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    async def test_local_request_keeps_adapter_headers_and_frame_metadata(self):
        service = VLMService(
            model="streaming-infer-adapter",
            api_base="http://127.0.0.1:8070/v1",
            api_key="EMPTY",
            provider="local",
            session_id="session-1",
        )

        await service.analyze_image(
            Image.new("RGB", (2, 2), "white"), prompt="describe", frame_metadata={"timestamp": 1}
        )

        request = _FakeAsyncOpenAI.instances[0].chat.completions.calls[0]
        self.assertEqual(request["extra_headers"]["x-streaming-session"], "session-1")
        self.assertEqual(request["extra_body"], {"frame_time_range": "1.0 seconds"})

    async def test_bailian_authorization_error_is_safe_and_actionable(self):
        service = VLMService(
            model="qwen3.6-plus",
            api_base=BAILIAN_API_BASE,
            api_key="sk-fake.for-test-only",
            provider="bailian",
        )
        error = APIStatusError(
            "access denied",
            response=httpx.Response(
                403,
                request=httpx.Request("POST", "https://example.invalid/v1/chat/completions"),
            ),
            body=None,
        )

        self.assertEqual(
            service._request_error_message(error),
            "当前 API Key 无权调用所选百炼模型，请确认套餐或模型权限。",
        )

    async def test_video_task_is_used_for_every_processed_frame_and_observations_are_kept(self):
        service = VLMService(
            model=BAILIAN_MODEL,
            api_base=BAILIAN_API_BASE,
            api_key="sk-fake.for-test-only",
            provider="bailian",
        )
        service.update_video_task("发现顾客时报告")

        await service.process_frame(
            Image.new("RGB", (2, 2), "white"),
            frame_metadata={"timestamp": 3.5},
        )

        request = _FakeAsyncOpenAI.instances[0].chat.completions.calls[0]
        self.assertEqual(request["messages"][0]["content"][0]["text"], "发现顾客时报告")
        self.assertEqual(len(service.video_observations), 1)
        self.assertEqual(service.video_observations[0]["timestamp"], 3.5)

    async def test_video_question_uses_saved_observations_without_an_image(self):
        service = VLMService(
            model=BAILIAN_MODEL,
            api_base=BAILIAN_API_BASE,
            api_key="sk-fake.for-test-only",
            provider="bailian",
        )
        service._record_video_observation({"timestamp": 4}, "画面中有一位顾客。")

        answer = await service.answer_video_question("视频里是否有顾客？")

        self.assertEqual(answer, "中文结果")
        request = _FakeAsyncOpenAI.instances[0].chat.completions.calls[0]
        self.assertNotIn("extra_headers", request)
        self.assertNotIn("extra_body", request)
        content = request["messages"][0]["content"]
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]["type"], "text")
        self.assertIn("视频里是否有顾客？", content[0]["text"])
        self.assertIn("4.0 秒", content[0]["text"])


class _CompletedInferenceService:
    def __init__(self):
        self.inference_count = 0

    def get_current_response(self):
        return "视频末帧结果", False

    def get_metrics(self):
        return {"total_inferences": self.inference_count}

    def consume_background_handoff_metric(self):
        return None


class VideoProcessorCompletionCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_task_notifies_when_no_later_video_frame_arrives(self):
        service = _CompletedInferenceService()
        sent = []
        processor = object.__new__(VideoProcessorTrack)
        processor.vlm_service = service
        processor.text_callback = lambda text, metrics: sent.append((text, metrics))
        processor._last_callback_inference_count = 0
        processor._last_callback_response = None

        async def finish_after_video_end():
            service.inference_count = 1

        processor._schedule_vlm_task(finish_after_video_end())
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertEqual(sent, [("视频末帧结果", {"total_inferences": 1})])
