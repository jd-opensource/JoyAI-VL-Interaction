import json
import unittest

from joy_interaction_webui import server
from joy_interaction_webui.bailian_config import BAILIAN_MODELS, BAILIAN_PROVIDER


class _BailianService:
    provider = BAILIAN_PROVIDER
    model = "qwen3.6-plus"


class BailianModelsRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_sessions = server.sessions.copy()
        server.sessions.clear()
        server.sessions["default"] = {"vlm_service": _BailianService()}

    def tearDown(self):
        server.sessions.clear()
        server.sessions.update(self.original_sessions)

    async def test_models_route_returns_all_configured_bailian_models(self):
        response = await server.models(None)
        payload = json.loads(response.text)

        self.assertEqual([model["id"] for model in payload["models"]], list(BAILIAN_MODELS))
        current = [model["id"] for model in payload["models"] if model["current"]]
        self.assertEqual(current, ["qwen3.6-plus"])
