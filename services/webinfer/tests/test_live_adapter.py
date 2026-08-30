import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock


WEBINFER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBINFER_DIR))

from live_adapter import (  # noqa: E402
    AdapterConfig,
    StreamingInferAdapter,
    _internal_image_count,
    _limit_internal_messages,
)


def _turn(index: int, image_count: int = 1) -> list[dict]:
    content = [{"type": "text", "text": f"turn {index}"}]
    content.extend(
        {"type": "image", "image": f"image-{index}-{image_index}.png"}
        for image_index in range(image_count)
    )
    return [
        {"role": "user", "content": content},
        {"role": "assistant", "content": f"answer {index}"},
    ]


class PromptWindowTests(unittest.TestCase):
    def test_limits_recent_complete_turns(self):
        messages = _turn(1) + _turn(2) + _turn(3)

        limited = _limit_internal_messages(messages, max_turns=2)

        self.assertEqual(
            [
                message["content"][0]["text"]
                for message in limited
                if message["role"] == "user"
            ],
            ["turn 2", "turn 3"],
        )
        self.assertEqual(
            [
                message["content"]
                for message in limited
                if message["role"] == "assistant"
            ],
            ["answer 2", "answer 3"],
        )

    def test_limits_images_and_preserves_text_and_newest_images(self):
        messages = _turn(1, image_count=2) + _turn(2, image_count=3)

        limited = _limit_internal_messages(messages, max_images=2)

        self.assertEqual(_internal_image_count(limited), 2)
        self.assertEqual(
            len([message for message in limited if message["role"] == "user"]),
            1,
        )
        user_content = limited[0]["content"]
        self.assertEqual(user_content[0], {"type": "text", "text": "turn 2"})
        self.assertEqual(
            [item["image"] for item in user_content if item["type"] == "image"],
            ["image-2-1.png", "image-2-2.png"],
        )

    def test_caps_a_long_stream_below_backend_image_limit(self):
        messages = [message for index in range(1, 41) for message in _turn(index)]

        limited = _limit_internal_messages(messages, max_images=32)

        self.assertEqual(_internal_image_count(limited), 32)
        user_messages = [
            message for message in limited if message["role"] == "user"
        ]
        self.assertEqual(user_messages[0]["content"][0]["text"], "turn 9")
        self.assertEqual(user_messages[-1]["content"][0]["text"], "turn 40")

    def test_disabled_limits_do_not_mutate_messages(self):
        messages = _turn(1, image_count=2)

        limited = _limit_internal_messages(messages)

        self.assertEqual(limited, messages)
        self.assertIsNot(limited, messages)
        self.assertEqual(_internal_image_count(messages), 2)


class FailedTurnRollbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_model_failure_rolls_back_appended_turn(self):
        with tempfile.TemporaryDirectory() as frame_dir:
            adapter = StreamingInferAdapter(
                AdapterConfig(
                    enable_summarizer=False,
                    force_silence_before_query=False,
                    frame_save_dir=frame_dir,
                    per_session_dirs=False,
                    save_model_inputs=False,
                )
            )
            self.addAsyncCleanup(adapter.main_client.close)
            adapter._call_main_model = AsyncMock(side_effect=RuntimeError("backend failed"))
            state = adapter.get_session("rollback-test")
            request = SimpleNamespace(headers={})
            payload = {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "What is happening?"},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
                                },
                            },
                        ],
                    }
                ]
            }

            with self.assertRaisesRegex(RuntimeError, "backend failed"):
                await adapter._handle_chat_payload(state, payload, request)

            self.assertEqual(state.frame_count, 0)
            self.assertEqual(state.turn_count, 0)
            self.assertEqual(state.session_frame_counter, 0)
            self.assertEqual(state.current_chunk["frame_count"], 0)
            self.assertEqual(state.current_chunk["turn_count"], 0)
            self.assertEqual(state.current_chunk["messages"], [])
            self.assertEqual(state.current_chunk["image_paths"], [])
            self.assertEqual(state.async_summary_segment["messages"], [])


if __name__ == "__main__":
    unittest.main()
