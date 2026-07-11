import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ReadmeCommunityTest(unittest.TestCase):
    def test_readmes_document_realtime_wechat_discussion_channel(self):
        english_readme = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese_readme = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

        self.assertIn("Community & Support", english_readme)
        self.assertIn("WeChat discussion group", english_readme)
        self.assertIn("weixin.jpg", english_readme)

        self.assertIn("社区与支持", chinese_readme)
        self.assertIn("微信群", chinese_readme)
        self.assertIn("weixin.jpg", chinese_readme)


if __name__ == "__main__":
    unittest.main()
