"""端到端流程测试：在隔离工作区里经由真实的 ./logo 命令与 Git 钩子走完整流程。"""
from __future__ import annotations

import hashlib
import json
import shutil
import unittest

from helpers import FONT_BINARIES, HAS_RENDER, PRIMARY, TRIANGLE, Workspace, fill_doc, logo

AVATAR_INSIDE = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                 '<rect id="background" width="100" height="100" fill="#111111"/>'
                 f'<circle cx="50" cy="50" r="30" fill="{PRIMARY}"/></svg>\n')


def prepare_release(ws: Workspace, slug: str = "demo") -> str:
    """在已有 A-v001 的项目上完成定稿决策与 r1 发布（版式、文档齐全），并提交。返回发布号。"""
    p = ws.projects / slug
    ws.logo("decision", slug, "finalize-a-v001", "--title", "定稿 A-v001")
    fill_doc(next((p / "decisions").glob("*.md")))
    ws.logo("release", slug, "--from", "A-v001")
    rdir = p / "releases" / "r1"
    for name in logo.required_layouts(logo_project_view(ws, slug)):
        (rdir / "layouts" / f"{name}.svg").write_text(TRIANGLE.format(c=PRIMARY), encoding="utf-8")
    (rdir / "layouts" / "avatar.svg").write_text(AVATAR_INSIDE, encoding="utf-8")
    fill_doc(rdir / "release.md")
    with (rdir / "release.md").open("a", encoding="utf-8") as f:
        f.write("\n依据：decisions/001-finalize-a-v001.md\n")
    fill_doc(rdir / "guide.md")
    ws.logo("index")
    r = ws.commit("projects", f"[{slug}] release r1")
    assert r.returncode == 0, ws.blocked(r)
    return "r1"


def logo_project_view(ws: Workspace, slug: str):
    """读取工作区里项目配置的轻量视图，供 required_layouts 使用（不依赖真实仓库路径）。"""
    import tomllib

    class View:
        cfg = tomllib.loads((ws.projects / slug / "project.toml").read_text(encoding="utf-8"))
    return View()


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)
        self.p = self.ws.new_project()

    def state(self) -> dict:
        import tomllib
        return tomllib.loads((self.p / "project.toml").read_text(encoding="utf-8"))["state"]

    def test_concept_and_inspired_version(self):
        ws = self.ws
        img = ws.tmp / "ref.png"
        img.write_bytes(b"\x89PNG\r\n")
        ws.logo("concept", "demo", str(img), "--name", "ref", "--tool", "user-upload", "--prompt", "用户参考图")
        fill_doc(self.p / "concepts" / "001-ref.md")
        ws.logo("direction", "demo", "A", "mountain")
        fill_doc(self.p / "directions" / "A-mountain" / "direction.md")
        r = ws.logo("version", "demo", "A", "--inspired-by", "009", ok=False)
        self.assertIn("概念图未登记", r.stderr)
        self.assertFalse((self.p / "directions" / "A-mountain" / "v001").exists())  # 不留半成品目录
        vdir = ws.new_version(extra=("--inspired-by", "001"))
        self.assertIn("concepts/001-ref.md", (vdir / "notes.md").read_text(encoding="utf-8"))
        r = ws.commit("projects", "[demo] A-v001")
        self.assertEqual(r.returncode, 0, ws.blocked(r))

    def test_state_alignment(self):
        ws = self.ws
        ws.new_version()
        ws.new_version(letter="B", name="wave")
        ws.logo("state", "demo", "selected_version=B-v001")
        self.assertEqual(self.state()["selected_direction"], "B")
        r = ws.logo("state", "demo", "selected_direction=A", "selected_version=B-v001", ok=False)
        self.assertIn("不属于", r.stderr)

    def test_release_syncs_selection_and_chains(self):
        ws = self.ws
        ws.new_version()
        self.assertEqual(ws.commit("projects", "[demo] A-v001").returncode, 0)
        prepare_release(ws)
        st = self.state()
        self.assertEqual((st["stage"], st["selected_version"], st["current_release"]), ("releasing", "A-v001", "r1"))
        ws.logo("release", "demo", "--from", "r1")
        self.assertEqual(self.state()["current_release"], "r2")
        self.assertEqual(self.state()["selected_version"], "A-v001")  # r2 → r1 → A-v001

    def test_export_refuses_unfilled_guide(self):
        ws = self.ws
        ws.new_version()
        ws.commit("projects", "[demo] A-v001")
        ws.logo("release", "demo", "--from", "A-v001")
        r = ws.logo("export", "demo", ok=False)
        self.assertIn("guide.md", r.stderr)
        self.assertFalse((self.p / "final").exists())


@unittest.skipUnless(HAS_RENDER, "需要 resvg 与 magick")
class ExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = Workspace()
        cls.p = cls.ws.new_project()
        cls.ws.new_version()
        assert cls.ws.commit("projects", "[demo] A-v001").returncode == 0
        prepare_release(cls.ws)
        cls.out = cls.ws.logo("export", "demo")

    @classmethod
    def tearDownClass(cls):
        cls.ws.cleanup()

    def manifest(self) -> dict:
        return json.loads((self.p / "final" / "manifest.json").read_text(encoding="utf-8"))

    def test_manifest_traceability(self):
        m = self.manifest()
        self.assertEqual((m["release"], m["based_on"], m["design_version"]), ("r1", "A-v001", "A-v001"))
        self.assertEqual(m["git_head"], self.ws.git("projects", "rev-parse", "--short", "HEAD").stdout.strip())
        self.assertEqual(m["system_head"], self.ws.git("system", "rev-parse", "--short", "HEAD").stdout.strip())
        for name, digest in m["files"].items():
            data = (self.p / "final" / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest, name)

    def test_all_platform_outputs(self):
        prof = logo.load_profiles()
        files = self.manifest()["files"]
        for c in prof["social"]["cover"]:
            self.assertIn(f"png/social/covers/{c['id']}-{c['width']}x{c['height']}.png", files, c["id"])
        for s in prof["social"]["avatar"]["sizes"]:
            self.assertIn(f"png/social/avatar/demo-avatar@{s}.png", files)
        for name in ("png/ios/AppIcon-1024.png", "png/favicon/favicon.ico", "png/android/play-store-512.png",
                     "png/android/adaptive/ic_launcher_monochrome.png", "guide.md"):
            self.assertIn(name, files)

    def test_stage_delivered_and_check_clean(self):
        import tomllib
        st = tomllib.loads((self.p / "project.toml").read_text(encoding="utf-8"))["state"]
        self.assertEqual(st["stage"], "delivered")
        self.ws.logo("index")
        r = self.ws.commit("projects", "[demo] export r1")
        self.assertEqual(r.returncode, 0, self.ws.blocked(r))
        self.assertNotIn("✗", self.ws.logo("check").stdout)

    def test_reexport_and_atomic_failure(self):
        ws, final = self.ws, self.p / "final"
        ws.logo("export", "demo")
        self.assertFalse((self.p / ".final-build").exists() or (self.p / ".final-old").exists())
        before = (final / "manifest.json").read_bytes()
        toml = self.p / "project.toml"
        original = toml.read_text(encoding="utf-8")
        toml.write_text(original.replace('android_background = "primary"', 'android_background = "nope"'),
                        encoding="utf-8")
        try:
            r = ws.logo("export", "demo", ok=False)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual((final / "manifest.json").read_bytes(), before)  # 旧交付物原样保留
            self.assertFalse((self.p / ".final-build").exists())
        finally:
            toml.write_text(original, encoding="utf-8")

    def test_android_safe_zone_warning(self):
        ldir = self.p / "releases" / "r1" / "layouts"
        full = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><path fill="#00897B" d="M0 0h100v100H0z"/></svg>'
        ap = logo.load_profiles()["android"]
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            tdir = Path(td)
            shutil.copy(ldir / "icon.svg", tdir / "icon.svg")

            class P:
                palette = {}

                def mode_map(self, mode):
                    return {}
            ok = logo.render_android_foreground(P(), tdir, ap, "color", tdir / "ok.png")
            self.assertIsNone(logo.android_safe_warning(ok, ap))
            (tdir / "icon.svg").write_text(full, encoding="utf-8")
            bad = logo.render_android_foreground(P(), tdir, ap, "color", tdir / "bad.png")
            self.assertIsNotNone(logo.android_safe_warning(bad, ap))


# 依赖颜色区分结构的设计：方块上的圆，单色化后成为一整块
COLOR_RELIANT = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                 f'<rect x="5" y="5" width="90" height="90" fill="{PRIMARY}"/>'
                 '<circle cx="50" cy="50" r="30" fill="#FFB300"/></svg>\n')


@unittest.skipUnless(HAS_RENDER, "需要 resvg 与 magick")
class MonoStructureTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self._cache = logo.STRUCTURE_CACHE
        logo.STRUCTURE_CACHE = Path(tempfile.mkdtemp()) / "structure.json"  # 不写入真实仓库的缓存
        self.addCleanup(setattr, logo, "STRUCTURE_CACHE", self._cache)
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)
        self.p = self.ws.new_project()
        toml = self.p / "project.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace('secondary = "#666666"', 'secondary = "#FFB300"', 1),
                        encoding="utf-8")

    @staticmethod
    def recolor(text: str, mapping: dict) -> str:
        return logo.HEX_IN_TEXT_RE.sub(lambda m: mapping.get(m.group(0).upper(), m.group(0)), text)

    def test_metric(self):
        black = {PRIMARY: "#000000", "#FFB300": "#000000"}
        keep = {PRIMARY: "#000000", "#FFB300": "#FFFFFF"}  # 修法：次色在单色版中留白
        tri = TRIANGLE.format(c=PRIMARY)
        self.assertIsNone(logo.structure_warning("tri", lambda m: tri if m == "color" else self.recolor(tri, black),
                                                 ["color", "mono"], ""))
        w = logo.structure_warning("blob", lambda m: COLOR_RELIANT if m == "color" else self.recolor(COLOR_RELIANT, black),
                                   ["color", "mono"], "")
        self.assertIn("单色版约", w)
        self.assertIsNone(logo.structure_warning(
            "fixed", lambda m: COLOR_RELIANT if m == "color" else self.recolor(COLOR_RELIANT, keep), ["color", "mono"], ""))

    def test_check_preview_and_palette_fix(self):
        ws = self.ws
        ws.new_version(svg=COLOR_RELIANT)
        self.assertEqual(ws.commit("projects", "[demo] A-v001").returncode, 0)
        ws.logo("state", "demo", "selected_version=A-v001")
        self.assertIn("选定版本 A-v001", ws.logo("check").stdout)
        self.assertIn("非彩色版只保留", ws.logo("preview", "demo", "A-v001").stdout)
        toml = self.p / "project.toml"
        text = toml.read_text(encoding="utf-8")
        mono, rev = text.index("[color_modes.mono]"), text.index("[color_modes.reversed]")
        text = (text[:mono] + text[mono:rev].replace('secondary = "#000000"', 'secondary = "#FFFFFF"')
                + text[rev:].replace('secondary = "#FFFFFF"', 'secondary = "#000000"'))
        toml.write_text(text, encoding="utf-8")
        self.assertNotIn("非彩色版只保留", ws.logo("check").stdout)

    def test_release_layouts_grouped_and_override_fix(self):
        ws = self.ws
        ws.new_version()
        ws.commit("projects", "[demo] A-v001")
        ws.logo("release", "demo", "--from", "A-v001")
        ldir = self.p / "releases" / "r1" / "layouts"
        logos = ["primary", "horizontal", "icon", "wordmark-cn", "wordmark-en"]
        for n in logos:
            (ldir / f"{n}.svg").write_text(COLOR_RELIANT, encoding="utf-8")
        out = ws.logo("check").stdout
        self.assertIn("primary.svg（同内容：horizontal、icon、wordmark-cn、wordmark-en）", out)
        kept = COLOR_RELIANT.replace("#FFB300", "#FFFFFF").replace(PRIMARY, "#000000")
        for n in logos:
            for mode, txt in (("mono", kept), ("reversed", kept.replace("#000000", "#X").replace("#FFFFFF", "#000000")
                                                         .replace("#X", "#FFFFFF"))):
                (ldir / f"{n}.{mode}.svg").write_text(txt, encoding="utf-8")
        self.assertNotIn("非彩色版只保留", ws.logo("check").stdout)


@unittest.skipUnless(any(f.name == "Inter-Regular.ttf" for f in FONT_BINARIES), "Inter 未下载")
class OutlineTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(with_fonts=True)
        self.addCleanup(self.ws.cleanup)
        self.ws.new_project()

    def test_palette_and_kerning(self):
        ws = self.ws
        r = ws.logo("outline", "--font", "inter", "--text", "AV", "--fill", "#FF0000", "--project", "demo", ok=False)
        self.assertIn("不在 demo 的色板中", r.stderr)
        on = ws.logo("outline", "--font", "inter", "--text", "A--V", "--size", "2048", "--fill", PRIMARY,
                     "--project", "demo").stdout
        off = ws.logo("outline", "--font", "inter", "--text", "A--V", "--size", "2048", "--fill", PRIMARY,
                      "--project", "demo", "--no-kern").stdout
        self.assertIn("kern=on", on)
        self.assertNotIn('text="A--V"', on)  # 注释中的 “--” 已转义
        self.assertNotEqual(on.split("advance=")[1], off.split("advance=")[1])

    def test_missing_font_points_to_fetch(self):
        ws = Workspace()
        self.addCleanup(ws.cleanup)
        r = ws.logo("outline", "--font", "inter", "--text", "A", "--fill", PRIMARY, ok=False)
        self.assertIn("./logo fonts fetch inter", r.stderr)


if __name__ == "__main__":
    unittest.main()
