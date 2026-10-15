"""纯函数单元测试：不创建工作区，毫秒级完成。"""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from helpers import FONT_BINARIES, REPO, fill_doc, logo


class ReleaseMetaTest(unittest.TestCase):
    def parse(self, text: str) -> dict:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "release.md").write_text(text, encoding="utf-8")
            return logo.load_release_meta(Path(td))

    def test_template_format(self):
        meta = self.parse("# 发布：r1\n\n- **基于**: A-v004\n- **日期**: 2026-10-13\n\n- [ ] `icon.svg`\n")
        self.assertEqual(meta, {"based_on": "A-v004", "date": "2026-10-13"})

    def test_plain_and_fullwidth_colon(self):
        self.assertEqual(self.parse("- based_on: r2\n")["based_on"], "r2")
        self.assertEqual(self.parse("- **基于**：`B-v001`\n")["based_on"], "B-v001")


class PlaceholderTest(unittest.TestCase):
    def test_append_sections_are_not_required(self):
        notes = logo.template_placeholders("version-notes.md")
        self.assertIn("（这个版本做了什么设计或修改）", notes)
        self.assertNotIn("（客户或 AI 的自审意见写在这里）", notes)  # 位于“追加区”
        self.assertNotIn("（对生成的版式的评审意见）", logo.template_placeholders("release.md"))

    def test_every_doc_template_has_placeholders(self):
        for _, tpl in logo.DOC_TEMPLATES:
            self.assertTrue(logo.template_placeholders(tpl), tpl)

    def test_fill_doc_clears_all_placeholders(self):
        for pat, tpl in logo.DOC_TEMPLATES:
            with self.subTest(tpl=tpl), tempfile.TemporaryDirectory() as td:
                f = Path(td) / "doc.md"
                f.write_text((REPO / "templates" / tpl).read_text(encoding="utf-8"), encoding="utf-8")
                path = {"brief.md": "x/brief.md", "direction.md": "x/directions/A-a/direction.md",
                        "version-notes.md": "x/directions/A-a/v001/notes.md", "release.md": "x/releases/r1/release.md",
                        "release-guide.md": "x/releases/r1/guide.md", "decision.md": "x/decisions/001-a.md",
                        "concept.md": "x/concepts/001-a.md", "cover.md": "x/covers/001-a/cover.md",
                        "cover-asset.md": "x/covers/001-a/assets/001-p.md"}[tpl]
                self.assertTrue(logo.unfilled_placeholders(path, f.read_text(encoding="utf-8")))
                fill_doc(f)
                self.assertEqual(logo.unfilled_placeholders(path, f.read_text(encoding="utf-8")), [])


class LayoutNamesTest(unittest.TestCase):
    def test_known_and_unknown(self):
        for ok in ("primary.svg", "icon.mono.svg", "wordmark-cn.reversed.svg", "app-icon.svg", "avatar.svg"):
            self.assertTrue(logo.is_layout_file(ok), ok)
        # 旧发布的封面、拼写错误、手工覆盖的配色文件（不会被使用）、非 SVG
        for bad in ("cover-og.svg", "cover-2.7x1.svg", "primay.svg", "app-icon.mono.svg", "primary.png"):
            self.assertFalse(logo.is_layout_file(bad), bad)


class PathRulesTest(unittest.TestCase):
    def test_system_allowlist(self):
        ok = ["tools/logo.py", "tools/requirements.txt", "tests/test_unit.py", "templates/projects.gitignore",
              "templates/brief.md", "config/cover-platforms.toml", "assets/fonts/fonts.toml",
              "assets/fonts/inter/LICENSE.txt", ".githooks/projects/pre-commit", ".claude/settings.json",
              ".agents/skills/logo-workflow/SKILL.md", "AGENTS.md", "logo"]
        bad = ["notes.txt", ".env", "trace.py", "tools/run.sh", "tools/debug.log", "config/x.json",
               "assets/fonts/inter/Inter-Regular.ttf", ".claude/settings.local.json", "projects/PROJECTS.md"]
        for p in ok:
            self.assertTrue(logo.path_allowed(p, "system"), p)
        for p in bad:
            self.assertFalse(logo.path_allowed(p, "system"), p)

    def test_projects_allowlist(self):
        ok = ["PROJECTS.md", ".gitignore", "acme/project.toml", "acme/brief.md", "acme/concepts/001-ref.png",
              "acme/concepts/001-ref.md", "acme/directions/A-mountain/direction.md",
              "acme/directions/A-mountain/v001/logo.svg", "acme/decisions/001-secret-garden.md",
              "acme/releases/r1/layouts/cover-2.7x1.svg", "acme/releases/r1/layouts/icon.mono.svg"]
        bad = ["notes.txt", "acme/trace.py", "acme/directions/A-mountain/v001/debug.png",
               "acme/releases/r1/notes.txt", "acme/concepts/ref.gif", "Acme/project.toml", ".final-build/a",
               "acme/final/png/general/x@64.png", "acme/dist/r1/ios/AppIcon-1024.png"]
        for p in ok:
            self.assertTrue(logo.path_allowed(p, "projects"), p)
        for p in bad:
            self.assertFalse(logo.path_allowed(p, "projects"), p)


class SecretRulesTest(unittest.TestCase):
    FAKES = {
        "private key": "-----BEGIN " + "OPENSSH PRIVATE KEY-----",  # 运行时拼接，源码本身不触发扫描
        "aws": "AKIA" + "ABCDEFGHIJKLMNOP",
        "github": "ghp_" + "a" * 36,
        "anthropic": "sk-ant-" + "a" * 24,
        "openai": "sk-proj-" + "a" * 40,
        "slack": "xoxb-" + "1" * 12,
        "google": "AIza" + "b" * 35,
    }

    def test_content_patterns_match_fakes(self):
        for name, fake in self.FAKES.items():
            self.assertTrue(logo.SECRET_CONTENT_RE.search(f"x = '{fake}'"), name)

    def test_content_patterns_ignore_normal_text_and_own_source(self):
        self.assertIsNone(logo.SECRET_CONTENT_RE.search("fill: url(#grad); sk-short; AKIA-not"))
        self.assertIsNone(logo.SECRET_CONTENT_RE.search((REPO / "tools" / "logo.py").read_text(encoding="utf-8")))

    def test_file_names(self):
        for n in (".env", ".env.local", "server.pem", "a.key", "id_rsa", "id_ed25519.pub", ".npmrc"):
            self.assertTrue(logo.SECRET_NAME_RE.match(n), n)
        for n in ("logo.py", "001-secret-garden.md", "env.md", "keys.svg"):
            self.assertFalse(logo.SECRET_NAME_RE.match(n), n)
        self.assertTrue(logo.SECRET_KEYWORD_RE.search("aws-credentials.toml"))


class SvgRulesTest(unittest.TestCase):
    PALETTE = {"#00897B"}

    def errors_for(self, body: str, root_attrs: str = 'viewBox="0 0 10 10"') -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "t.svg"
            f.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" {root_attrs}>{body}</svg>', encoding="utf-8")
            errors: list[str] = []
            logo.check_svg(f, self.PALETTE, errors, [])
            return errors

    def test_valid(self):
        self.assertEqual(self.errors_for('<path fill="#00897B" d="M0 0h1v1z"/>'), [])
        self.assertEqual(self.errors_for('<defs><linearGradient id="g"><stop stop-color="#00897B"/></linearGradient>'
                                         '</defs><path fill="url(#g)" d="M0 0h1v1z"/>'), [])

    def test_violations(self):
        cases = {
            "off palette": '<path fill="#FF0000" d="M0 0h1v1z"/>',
            "named color": '<path fill="red" d="M0 0h1v1z"/>',
            "text": "<text>A</text>",
            "image": '<image href="#x"/>',
            "script": "<script>1</script>",
            "external href": '<use href="https://x.example/a.svg#a"/>',
            "event attr": '<path onclick="x()" fill="#00897B" d="M0 0h1v1z"/>',
            "css import": "<style>@import url(https://x.example/a.css);</style>",
            "css url": '<path style="filter:url(https://x.example/f)" d="M0 0h1v1z"/>',
        }
        for name, body in cases.items():
            self.assertTrue(self.errors_for(body), name)
        self.assertTrue(self.errors_for("", 'viewBox="0 0 10 10" width="10"'))
        self.assertTrue(self.errors_for("", 'onload="x()" viewBox="0 0 10 10"'))
        self.assertTrue(self.errors_for("", ""))  # 缺 viewBox


class PreviewAndModesTest(unittest.TestCase):
    class FakeProject:
        palette = {"primary": "#00897B", "ink": "#1A1A1A"}

        def mode_map(self, mode):
            return {"#00897B": "#000000", "#1A1A1A": "#000000"} if mode == "mono" else {}

    def test_override_wins_over_recolor(self):
        p = self.FakeProject()
        with tempfile.TemporaryDirectory() as td:
            ldir = Path(td)
            (ldir / "icon.svg").write_text('<path fill="#00897B"/>', encoding="utf-8")
            self.assertIn("#000000", logo.layout_mode_text(p, ldir, "icon", "mono"))
            (ldir / "icon.mono.svg").write_text("OVERRIDE", encoding="utf-8")
            self.assertEqual(logo.layout_mode_text(p, ldir, "icon", "mono"), "OVERRIDE")
            self.assertIn("#00897B", logo.layout_mode_text(p, ldir, "icon", "color"))  # color 不可覆盖

    def test_override_palette_allows_black_and_white(self):
        p, base = self.FakeProject(), {"#00897B"}
        self.assertEqual(logo.layout_palette(p, Path("icon.svg"), base), base)  # 普通版式只能用色板
        self.assertEqual(logo.layout_palette(p, Path("cover-2.7x1.svg"), base), base)  # 比例里的点不算配色模式
        mono = logo.layout_palette(p, Path("icon.mono.svg"), base)
        self.assertTrue({"#000000", "#FFFFFF", "#00897B"} <= mono)
        self.assertNotIn("#FF0000", mono)

    def test_dark_background(self):
        self.assertEqual(logo.dark_background(self.FakeProject()), "#1A1A1A")
        light = self.FakeProject()
        light.palette = {"a": "#FFFFFF", "b": "#CCCCCC"}
        self.assertEqual(logo.dark_background(light), "#111111")


class AppBackgroundTest(unittest.TestCase):
    class P:
        palette = {"primary": "#00897B", "ink": "#1A1A1A"}

        def __init__(self, export):
            self.cfg = {"export": export}

    def test_resolution_and_alias(self):
        self.assertEqual(logo.app_background(self.P({"app_background": "ink"})), "#1A1A1A")
        self.assertEqual(logo.app_background(self.P({"android_background": "primary"})), "#00897B")  # 旧名兼容
        self.assertEqual(logo.app_background(self.P({"app_background": "ink", "android_background": "primary"})),
                         "#1A1A1A")  # 新名优先
        for bad in ({}, {"app_background": "nope"}):
            with self.assertRaises(logo.LogoError):
                logo.app_background(self.P(bad))


class ProfileWarningsTest(unittest.TestCase):
    TODAY = dt.date.today().isoformat()

    def warn(self, item: dict) -> list[str]:
        return logo.spec_warnings("t", item)

    def test_official(self):
        self.assertEqual(self.warn({"verified_on": self.TODAY, "source": "https://x"}), [])
        self.assertTrue(self.warn({"source": "https://x"}))  # 未核实
        self.assertTrue(self.warn({"verified_on": self.TODAY}))  # 缺来源
        self.assertTrue(self.warn({"verified_on": "2020-01-01", "source": "https://x"}))  # 过期

    def test_common_and_generic(self):
        for basis in ("common", "generic"):
            self.assertEqual(self.warn({"basis": basis, "verified_on": self.TODAY, "verify_note": "依据"}), [])
            self.assertTrue(self.warn({"basis": basis, "verified_on": self.TODAY}))  # 缺依据
            self.assertTrue(self.warn({"basis": basis, "verified_on": "2020-01-01", "verify_note": "依据"}))
            self.assertTrue(self.warn({"basis": basis, "verify_note": "依据"}))  # 缺复核日期
        self.assertTrue(self.warn({"basis": "guess", "verified_on": self.TODAY, "verify_note": "x"}))

    def test_real_configs_are_clean(self):
        self.assertEqual(logo.profile_warnings(), [])
        t = logo.load_targets()
        self.assertTrue({"compose", "favicon", "ios", "android", "avatar"} <= set(t))
        self.assertNotIn("social", t)  # 封面不属于 Logo 资产


class LogoAssetsTest(unittest.TestCase):
    class P:
        palette = {"primary": "#00897B", "ink": "#1A1A1A"}

        def __init__(self, export):
            self.cfg = {"export": export}

    def test_targets_and_legacy_profiles(self):
        self.assertEqual(logo.project_targets(self.P({"targets": ["ios", "svg"]})), ["svg", "ios"])  # 固定顺序
        self.assertEqual(logo.project_targets(self.P({"profiles": ["general", "social", "favicon"]})),
                         ["svg", "favicon", "avatar"])
        self.assertEqual(logo.project_targets(self.P({})), [])
        with self.assertRaises(logo.LogoError):
            logo.project_targets(self.P({"targets": ["cover"]}))

    def test_icon_mode_alias(self):
        self.assertEqual(logo.icon_mode(self.P({"icon_mode": "mono", "android_foreground_mode": "reversed"})), "mono")
        self.assertEqual(logo.icon_mode(self.P({"android_foreground_mode": "reversed"})), "reversed")
        self.assertEqual(logo.icon_mode(self.P({})), "color")

    def test_compose_geometry(self):
        import xml.etree.ElementTree as ET
        src = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 50 100"><path d="M0 0h1v1z"/></svg>'
        root = ET.fromstring(logo.compose_svg(src, 0.64, "#00897B"))
        ns = "{http://www.w3.org/2000/svg}"
        self.assertEqual(root.get("viewBox"), "0 0 1000 1000")
        rect, inner = list(root)
        self.assertEqual((rect.tag, rect.get("fill"), rect.get("width")), (f"{ns}rect", "#00897B", "1000"))
        self.assertEqual([inner.get(k) for k in ("x", "y", "width", "height")], ["180", "180", "640", "640"])
        self.assertEqual(inner.get("preserveAspectRatio"), "xMidYMid meet")  # 等比，不拉伸
        self.assertEqual(len(ET.fromstring(logo.compose_svg(src, 0.5, None))), 1)  # 不铺底色时只有图形
        with self.assertRaises(logo.LogoError):
            logo.compose_svg('<svg xmlns="http://www.w3.org/2000/svg"/>', 0.5, None)

    def test_write_ico(self):
        import struct
        def fake_png(w, h):
            return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h) + b"\0" * 9
        with tempfile.TemporaryDirectory() as td:
            pngs = []
            for s in (16, 256):
                f = Path(td) / f"{s}.png"
                f.write_bytes(fake_png(s, s))
                pngs.append(f)
            data = logo.write_ico(pngs, Path(td) / "a.ico").read_bytes()
            self.assertEqual(struct.unpack("<HHH", data[:6]), (0, 1, 2))
            e1, e2 = data[6:22], data[22:38]
            self.assertEqual(e1[0], 16)
            self.assertEqual(e2[0], 0)  # 256 在 ICO 目录中记为 0
            off2 = struct.unpack("<I", e2[12:16])[0]
            self.assertEqual(data[off2:off2 + 8], b"\x89PNG\r\n\x1a\n")


class FeatureParsingTest(unittest.TestCase):
    def test_defaults_keep_glyphs(self):
        f = logo.parse_features(None)
        self.assertEqual(f, {"kern": True, "liga": False, "clig": False, "calt": False, "dlig": False})

    def test_syntax(self):
        f = logo.parse_features("calt, -kern, ss02, cv05=2, +liga")
        self.assertEqual((f["calt"], f["kern"], f["ss02"], f["cv05"], f["liga"]), (True, False, True, 2, True))
        self.assertFalse(logo.parse_features(None, no_kern=True)["kern"])  # --no-kern 等同 -kern
        for bad in ("bad tag", "toolong", "ss0", "cv05=x"):
            with self.assertRaises(logo.LogoError):
                logo.parse_features(bad)


@unittest.skipUnless(any(f.name == "Inter-Regular.ttf" for f in FONT_BINARIES), "Inter 未下载（./logo fonts fetch inter）")
class ShapingTest(unittest.TestCase):
    # HarfBuzz 14.6.0（hb-shape --features=-liga,-calt）对 Inter Regular 的总前进宽度，单位为字体单位
    HARFBUZZ = {"AV": 2686, "To": 2390, "AVATAR": 7664, "Cat&Dog": 8597, "->": 2297}

    def shape(self, text, spec=None):
        return logo.shape_text(next(f for f in FONT_BINARIES if f.name == "Inter-Regular.ttf"), text, logo.parse_features(spec))

    def test_matches_harfbuzz_reference(self):
        for text, expected in self.HARFBUZZ.items():
            self.assertEqual(sum(g[2] for g in self.shape(text)), expected, text)

    def test_substitution_only_when_requested(self):
        self.assertEqual(len(self.shape("->")), 2)                  # 默认：两个字形，保持原样
        self.assertEqual(len(self.shape("->", "calt")), 1)          # 开启 calt：替换成箭头
        self.assertEqual(sum(g[2] for g in self.shape("->", "calt")), 1954)

    def test_clusters_are_character_indices(self):
        glyphs = self.shape("A猫B")
        self.assertEqual([(g[0] == 0, g[1]) for g in glyphs], [(False, 0), (True, 1), (False, 2)])


if __name__ == "__main__":
    unittest.main()
