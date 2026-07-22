from pathlib import Path
import unittest


STATIC_INDEX = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "joy_interaction_webui"
    / "static"
    / "index.html"
)


class LocalVideoUiTests(unittest.TestCase):
    def test_local_video_source_is_present_and_uses_browser_capture(self):
        content = STATIC_INDEX.read_text(encoding="utf-8")

        self.assertIn('data-source="local-video"', content)
        self.assertIn('id="localVideoFile"', content)
        self.assertIn('id="localVideoSource"', content)
        self.assertIn("captureStream", content)
        self.assertIn("startLocalVideo", content)

    def test_local_video_is_not_uploaded_to_a_server_route(self):
        content = STATIC_INDEX.read_text(encoding="utf-8")
        local_video_block = content[content.index("async function startLocalVideo"):]

        self.assertNotIn("FormData", local_video_block)
        self.assertNotIn("/api/upload", local_video_block)

    def test_local_video_waits_for_webrtc_and_keeps_final_response(self):
        content = STATIC_INDEX.read_text(encoding="utf-8")
        local_video_block = content[content.index("async function startLocalVideo"):]

        self.assertLess(
            local_video_block.index("await startWebRtcWithLocalStream"),
            local_video_block.index("await localVideoSource.play();"),
        )
        self.assertIn("Video finished; waiting for final analysis", local_video_block)
        self.assertIn("if (localVideoFinished && getActiveInputSource() === 'local-video')", content)

    def test_local_video_supports_persistent_tasks_and_completed_video_questions(self):
        content = STATIC_INDEX.read_text(encoding="utf-8")

        self.assertIn("function getActiveInputSource()", content)
        self.assertIn("type: 'set_video_task'", content)
        self.assertIn("type: 'ask_video_question'", content)
        self.assertIn("type: 'start_video_analysis'", content)
        self.assertIn("data.type === 'video_question_answer'", content)
