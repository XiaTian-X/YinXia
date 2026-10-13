"""字体下载测试：用本地 file:// 压缩包模拟官方下载地址，不访问网络。"""
from __future__ import annotations

import hashlib
import unittest
import zipfile

from helpers import Workspace

FAKE_FONT = b"FAKE-FONT-BYTES-" * 64


class FontsFetchTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(setup=False)
        self.addCleanup(self.ws.cleanup)
        self.zip = self.ws.tmp / "TestFont-1.0.zip"
        with zipfile.ZipFile(self.zip, "w") as z:
            z.writestr("fonts/TestFont-Regular.ttf", FAKE_FONT)
            z.writestr("LICENSE.txt", "OFL")
        self.ws.write("assets/fonts/testfont/LICENSE.txt", "OFL\n")
        self.register(hashlib.sha256(FAKE_FONT).hexdigest())

    def register(self, sha: str, member: str = "fonts/TestFont-Regular.ttf", url: str | None = None,
                 tier: str = "open-source"):
        toml = self.ws.root / "assets" / "fonts" / "fonts.toml"
        base = toml.read_text(encoding="utf-8").split("\n# --test--")[0]
        toml.write_text(base + f'''
# --test--
[[font]]
id            = "testfont"
family        = "Test Font"
weight        = "Regular"
file          = "testfont/TestFont-Regular.ttf"
version       = "1.0"
license       = "OFL-1.1"
license_file  = "testfont/LICENSE.txt"
tier          = "{tier}"
official_url  = "https://example.com/testfont"
download_url  = "{url or self.zip.as_uri()}"
archive_member = "{member}"
sha256        = "{sha}"
retrieved_on  = "2026-10-13"
''', encoding="utf-8")

    @property
    def dest(self):
        return self.ws.root / "assets" / "fonts" / "testfont" / "TestFont-Regular.ttf"

    @property
    def cache(self):
        return self.ws.root / ".cache" / "downloads" / self.zip.name

    def test_fetch_verifies_and_skips(self):
        self.assertIn("未下载", self.ws.logo("fonts").stdout)
        self.assertIn("未下载", self.ws.logo("check").stdout)
        self.assertIn("已下载并校验", self.ws.logo("fonts", "fetch", "testfont").stdout)
        self.assertEqual(self.dest.read_bytes(), FAKE_FONT)
        self.assertIn("已就绪，跳过", self.ws.logo("fonts", "fetch", "testfont").stdout)
        self.assertIn("[已就绪]", self.ws.logo("fonts", "list", "testfont").stdout)

    def test_sha_mismatch_rejected(self):
        self.register("0" * 64)
        r = self.ws.logo("fonts", "fetch", "testfont", ok=False)
        self.assertIn("sha256 与 fonts.toml 不符", r.stderr)
        self.assertFalse(self.dest.exists())
        self.assertFalse(self.cache.exists())  # 可疑的下载缓存被删除

    def test_missing_member_and_corrupt_archive(self):
        self.register(hashlib.sha256(FAKE_FONT).hexdigest(), member="nope.ttf")
        self.assertIn("无法读取", self.ws.logo("fonts", "fetch", "testfont", ok=False).stderr)
        self.register(hashlib.sha256(FAKE_FONT).hexdigest())
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_bytes(b"not a zip")
        self.assertIn("无法读取", self.ws.logo("fonts", "fetch", "testfont", ok=False).stderr)
        self.assertIn("已下载并校验", self.ws.logo("fonts", "fetch", "testfont").stdout)  # 缓存删除后重试成功

    def test_modified_local_font_is_repaired(self):
        self.ws.logo("fonts", "fetch", "testfont")
        self.dest.write_bytes(b"tampered")
        self.assertIn("校验不符", self.ws.logo("fonts").stdout)
        self.assertIn("sha256 与文件不符", self.ws.logo("check", ok=False).stdout)
        self.ws.logo("fonts", "fetch", "testfont")
        self.assertEqual(self.dest.read_bytes(), FAKE_FONT)

    def test_unknown_id_and_unreachable_url(self):
        self.assertIn("字体未登记", self.ws.logo("fonts", "fetch", "nope", ok=False).stderr)
        self.register(hashlib.sha256(FAKE_FONT).hexdigest(), url=(self.ws.tmp / "missing.zip").as_uri())
        r = self.ws.logo("fonts", "fetch", "testfont", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.dest.exists())


class LicenseReviewTest(FontsFetchTest):
    def review(self):
        return self.ws.root / "assets" / "fonts" / "testfont" / "LICENSE-REVIEW.md"

    def check_errors(self) -> str:
        return "\n".join(ln for ln in self.ws.logo("check", ok=False).stdout.splitlines() if ln.startswith("✗"))

    def test_review_lifecycle(self):
        self.register(hashlib.sha256(FAKE_FONT).hexdigest(), tier="free-commercial")
        self.assertIn("须有授权复核", self.check_errors())
        self.assertIn("已创建授权复核", self.ws.logo("fonts", "review", "testfont").stdout)
        self.assertIn("模板占位未填写", self.check_errors())
        self.assertNotEqual(self.ws.logo("fonts", "review", "testfont", ok=False).returncode, 0)  # 不覆盖
        def fill(logo_ok: str, outline_ok: str):
            text = self.review().read_text(encoding="utf-8")
            for old, new in (("- **授权名称**：", "- **授权名称**：Test EULA"),
                             ("- **授权原文链接**：", "- **授权原文链接**：https://example.com/eula"),
                             ("- **复核日期**：", "- **复核日期**：2026-10-13"),
                             ("- **允许用于 Logo / 商标**：（是 / 否）", f"- **允许用于 Logo / 商标**：{logo_ok}"),
                             ("- **允许转曲（文字转路径）**：（是 / 否）", f"- **允许转曲（文字转路径）**：{outline_ok}"),
                             ("- **限制与义务**：（如署名、不得单独售卖字体文件等；无则写“无”）", "- **限制与义务**：无"),
                             ("（引用授权原文中支撑上述判断的条款原文）", "第 2 条：允许商业使用。"),
                             ("（是否准入，以及使用限制）", "准入。")):
                text = text.replace(old, new)
            self.review().write_text(text, encoding="utf-8")
        original = self.review().read_text(encoding="utf-8")
        fill("是", "否")
        self.assertIn("“允许转曲（文字转路径）”须为“是”", self.check_errors())
        self.review().write_text(original, encoding="utf-8")
        fill("是（含商标注册）", "是")
        self.assertEqual(self.check_errors(), "")

    def test_open_source_needs_no_review(self):
        self.assertNotIn("授权复核", self.ws.logo("check").stdout)


if __name__ == "__main__":
    unittest.main()
