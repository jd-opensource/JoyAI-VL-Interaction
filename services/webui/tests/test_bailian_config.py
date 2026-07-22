from pathlib import Path
import tempfile
import unittest

from joy_interaction_webui.bailian_config import (
    BAILIAN_MODELS,
    BAILIAN_PROVIDER,
    BailianConfigurationError,
    load_bailian_api_key,
    resolve_provider,
)


class BailianConfigTests(unittest.TestCase):
    def test_environment_key_overrides_file(self):
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / "API.txt"
            key_file.write_text("说明\nsk-file.value-from-file\n", encoding="utf-8")

            self.assertEqual(
                load_bailian_api_key(
                    key_file=key_file, environ={"OPENAI_API_KEY": "sk-env.value"}
                ),
                "sk-env.value",
            )

    def test_key_file_preserves_complete_key_line(self):
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / "API.txt"
            expected = "sk-test.segment.with.dots-and-dashes"
            key_file.write_text(f"说明文字\n  {expected}  \n", encoding="utf-8")

            self.assertEqual(load_bailian_api_key(key_file=key_file, environ={}), expected)

    def test_key_file_requires_sk_prefix(self):
        for content in ("说明文字\n", "not-a-key\n"):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                key_file = Path(directory) / "API.txt"
                key_file.write_text(content, encoding="utf-8")

                with self.assertRaisesRegex(BailianConfigurationError, "does not contain"):
                    load_bailian_api_key(key_file=key_file, environ={})

    def test_missing_key_file_is_safe_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(BailianConfigurationError, "was not found"):
                load_bailian_api_key(key_file=Path(directory) / "missing.txt", environ={})

    def test_provider_defaults_to_bailian_and_rejects_unknown(self):
        self.assertEqual(resolve_provider(""), BAILIAN_PROVIDER)
        with self.assertRaisesRegex(ValueError, "Unsupported VLM provider"):
            resolve_provider("unsupported")

    def test_supported_bailian_models_are_explicit(self):
        self.assertEqual(
            BAILIAN_MODELS,
            ("qwen3.7-plus", "qwen3.6-plus", "qwen3.6-flash"),
        )
