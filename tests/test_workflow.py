"""端到端流程测试：在隔离工作区里经由真实的 ./logo 命令与 Git 钩子走完整流程。"""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

from helpers import FONT_BINARIES, HAS_RENDER, PRIMARY, TRIANGLE, Workspace, fill_doc, logo

ALL_TARGETS = '["svg", "favicon", "ios", "android", "avatar"]'


def set_targets(p: Path, targets: str = ALL_TARGETS) -> None:
    toml = p / "project.toml"
    text = toml.read_text(encoding="utf-8")
    new = text.replace('targets = ["svg", "favicon"]', f"targets = {targets}")
    assert new != text, "模板中找不到 targets 行"
    toml.write_text(new, encoding="utf-8")


def prepare_release(ws: Workspace, slug: str = "demo", extra: dict[str, str] | None = None) -> str:
    """在已有 A-v001 的项目上完成定稿决策与 r1 发布（只放主版 primary.svg 与 extra 中的文件），并提交。"""
    p = ws.projects / slug
    ws.logo("decision", slug, "finalize-a-v001", "--title", "定稿 A-v001")
    fill_doc(next((p / "decisions").glob("*.md")))
    ws.logo("release", slug, "--from", "A-v001")
    rdir = p / "releases" / "r1"
    (rdir / "layouts" / "primary.svg").write_text(TRIANGLE.format(c=PRIMARY), encoding="utf-8")
    for name, text in (extra or {}).items():
        (rdir / "layouts" / name).write_text(text, encoding="utf-8")
    fill_doc(rdir / "release.md")
    with (rdir / "release.md").open("a", encoding="utf-8") as f:
        f.write("\n依据：decisions/001-finalize-a-v001.md\n")
    fill_doc(rdir / "guide.md")
    ws.logo("index")
    r = ws.commit("projects", f"[{slug}] release r1")
    assert r.returncode == 0, ws.blocked(r)
    return "r1"


def pixel(png: Path, x: str, y: str) -> str:
    """读取 PNG 某点颜色（RRGGBBAA）；x/y 可用 fx 表达式，如 "w/2"。"""
    return subprocess.run(["magick", str(png), "-format", f"%[hex:p{{%[fx:{x}],%[fx:{y}]}}]", "info:"],
                          capture_output=True, text=True, check=True).stdout.strip().upper()


class LatestTest(unittest.TestCase):
    """latest/：当前发布的固定地址副本，由 ./logo latest 生成，提交时必须与当前发布一致。"""

    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)
        self.p = self.ws.new_project()
        self.ws.new_version()
        assert self.ws.commit("projects", "[demo] A-v001").returncode == 0

    def test_latest_follows_current_release(self):
        ws, p = self.ws, self.p
        self.assertIn("current_release", ws.logo("latest", "demo", ok=False).stderr)
        prepare_release(ws, extra={"icon.svg": TRIANGLE.format(c=PRIMARY)})
        d = p / "latest"
        ws.logo("latest", "demo")
        self.assertEqual(sorted(f.name for f in d.iterdir()),
                         sorted(f"demo-{n}{m}-latest.svg" for n in ("primary", "icon") for m in ("", "-mono", "-reversed")))
        self.assertEqual((d / "demo-primary-latest.svg").read_bytes(),
                         (p / "releases" / "r1" / "layouts" / "primary.svg").read_bytes())
        self.assertNotIn(PRIMARY, (d / "demo-primary-mono-latest.svg").read_text(encoding="utf-8"))
        r = ws.commit("projects", "[demo] latest")
        self.assertEqual(r.returncode, 0, ws.blocked(r))

        # 新发布去掉了 icon，却没更新 latest/：提交被拦截
        self.assertIn("latest demo", ws.logo("release", "demo", "--from", "r1").stdout)
        rdir = p / "releases" / "r2"
        (rdir / "layouts" / "icon.svg").unlink()
        fill_doc(rdir / "release.md")
        with (rdir / "release.md").open("a", encoding="utf-8") as f:
            f.write("\n依据：decisions/001-finalize-a-v001.md\n")
        fill_doc(rdir / "guide.md")
        ws.logo("index")
        r = ws.commit("projects", "[demo] release r2")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("与当前发布 r2 不一致", "\n".join(ws.blocked(r)))
        ws.logo("latest", "demo")
        self.assertFalse((d / "demo-icon-latest.svg").exists())
        r = ws.commit("projects", "[demo] release r2")
        self.assertEqual(r.returncode, 0, ws.blocked(r))

        # 手改 latest/ 也会被发现
        f = d / "demo-primary-latest.svg"
        f.write_text(f.read_text(encoding="utf-8").replace(PRIMARY, "#123456"), encoding="utf-8")
        self.assertIn("内容与发布不一致", ws.logo("check", ok=False).stdout)
        ws.git("projects", "add", "-A")
        self.assertNotEqual(ws.commit("projects", "[demo] 手改 latest", add=False).returncode, 0)

    def test_latest_without_release_is_rejected(self):
        ws, p = self.ws, self.p
        (p / "latest").mkdir()
        (p / "latest" / "demo-primary-latest.svg").write_text(TRIANGLE.format(c=PRIMARY), encoding="utf-8")
        self.assertIn("还没有当前发布", ws.logo("check", ok=False).stdout)


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)
        self.p = self.ws.new_project()

    def state(self) -> dict:
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

    def test_release_needs_only_primary(self):
        ws = self.ws
        ws.new_version()
        self.assertEqual(ws.commit("projects", "[demo] A-v001").returncode, 0)
        prepare_release(ws)  # 只放 primary.svg 也能提交
        st = self.state()
        self.assertEqual((st["stage"], st["selected_version"], st["current_release"]), ("releasing", "A-v001", "r1"))
        self.assertNotIn("缺少", ws.logo("check").stdout)
        ws.logo("release", "demo", "--from", "r1")
        self.assertEqual(self.state()["selected_version"], "A-v001")  # r2 → r1 → A-v001
        (self.p / "releases" / "r2" / "layouts" / "primary.svg").unlink()
        self.assertIn("缺少主版 primary.svg", ws.logo("check", ok=False).stdout)

    def test_release_seeds_primary_from_version(self):
        ws = self.ws
        square = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><path fill="{PRIMARY}" d="M10 10h80v80H10Z"/></svg>\n'
        logo_svg = ws.new_version(svg=square) / "logo.svg"
        ws.logo("release", "demo", "--from", "A-v001")
        primary = self.p / "releases" / "r1" / "layouts" / "primary.svg"
        self.assertEqual(primary.read_bytes(), logo_svg.read_bytes())
        primary.write_text(TRIANGLE.format(c=PRIMARY), encoding="utf-8")  # 发布版式可另行调整
        ws.logo("release", "demo", "--from", "r1")  # 基于发布：继承上一个发布的版式，而不是版本稿
        self.assertEqual((self.p / "releases" / "r2" / "layouts" / "primary.svg").read_text(encoding="utf-8"),
                         TRIANGLE.format(c=PRIMARY))

    def test_legacy_config_warning(self):
        toml = self.p / "project.toml"
        modern = toml.read_text(encoding="utf-8")
        self.assertNotIn("旧写法", self.ws.logo("check").stdout)
        toml.write_text(modern.replace('targets = ["svg", "favicon"]', 'profiles = ["general", "social"]')
                        .replace('app_background = "primary"', 'android_background = "primary"')
                        + '\n[layouts]\nlogo = ["primary"]\n', encoding="utf-8")
        out = self.ws.logo("check").stdout
        self.assertIn("使用了旧写法", out)
        for part in ("[layouts] 已不起作用", 'profiles 改为 targets = ["svg", "avatar"]',
                     "android_background 改名为 app_background"):
            self.assertIn(part, out)
        self.assertNotIn("android_foreground_mode", out)
        toml.write_text(modern.replace('targets = ["svg", "favicon"]',
                                       'targets = ["svg"]\nprofiles = ["general"]'), encoding="utf-8")
        self.assertIn("profiles 已被 targets 取代", self.ws.logo("check").stdout)

    def test_paused_stage(self):
        ws = self.ws
        ws.new_version()
        ws.logo("state", "demo", "stage=paused")
        ws.logo("version", "demo", "A")  # 暂停中的项目继续出稿不会自动恢复阶段
        self.assertEqual(self.state()["stage"], "paused")
        ws.logo("index")
        self.assertIn("| paused |", (ws.projects / "PROJECTS.md").read_text(encoding="utf-8"))
        self.assertNotIn("✗", ws.logo("check").stdout)

    def test_export_refuses_unfilled_guide(self):
        ws = self.ws
        ws.new_version()
        ws.commit("projects", "[demo] A-v001")
        ws.logo("release", "demo", "--from", "A-v001")
        (self.p / "releases" / "r1" / "layouts" / "primary.svg").write_text(TRIANGLE.format(c=PRIMARY), encoding="utf-8")
        r = ws.logo("export", "demo", ok=False)
        self.assertIn("guide.md", r.stderr)
        self.assertFalse((self.p / "dist").exists())

    @unittest.skipUnless(HAS_RENDER, "需要 resvg 与 magick")
    def test_guide_erratum_is_append_only_and_reaches_delivery(self):
        ws = self.ws
        ws.new_version()
        self.assertEqual(ws.commit("projects", "[demo] A-v001").returncode, 0)
        prepare_release(ws)
        ws.logo("export", "demo")
        rdir = self.p / "releases" / "r1"
        guide = rdir / "guide.md"
        original = guide.read_bytes()
        frozen = (rdir / "layouts" / "primary.svg").read_bytes()
        dist = self.p / "dist" / "r1"
        before = json.loads((dist / "manifest.json").read_text(encoding="utf-8"))
        correction = "\n## 2026-10-23 勘误\n适用于 r1：主版画布为 1:1，以实际 SVG 为准。\n"
        with guide.open("a", encoding="utf-8") as f:
            f.write(correction)
        result = ws.commit("projects", "[demo] append guide erratum")
        self.assertEqual(result.returncode, 0, ws.blocked(result))
        self.assertTrue(guide.read_bytes().startswith(original))
        self.assertEqual((rdir / "layouts" / "primary.svg").read_bytes(), frozen)
        ws.logo("export", "demo", "svg")
        self.assertIn(correction, (dist / "guide.md").read_text(encoding="utf-8"))
        after = json.loads((dist / "manifest.json").read_text(encoding="utf-8"))
        self.assertNotEqual(before["files"]["guide.md"], after["files"]["guide.md"])
        for name, digest in before["files"].items():
            if name != "guide.md":
                self.assertEqual(after["files"][name], digest)
        self.assertEqual(after["files"]["guide.md"], hashlib.sha256((dist / "guide.md").read_bytes()).hexdigest())
        # 改写已提交指南仍然被同一个钩子拦截。
        guide.write_text("# 改写原文\n" + guide.read_text(encoding="utf-8"), encoding="utf-8")
        result = ws.commit("projects", "[demo] invalid rewrite")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("只允许在末尾追加", "\n".join(ws.blocked(result)))

    def test_targets_declaration_and_legacy_profiles(self):
        ws = self.ws
        listing = ws.logo("export", "demo", "--list").stdout
        self.assertRegex(listing, r"svg\s+已声明")
        self.assertRegex(listing, r"ios\s+—")
        toml = self.p / "project.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace(
            'targets = ["svg", "favicon"]', 'profiles = ["general", "favicon", "ios", "android", "social"]'),
            encoding="utf-8")
        listing = ws.logo("export", "demo", "--list").stdout
        for t in ("svg", "favicon", "ios", "android", "avatar"):  # 旧写法自动换算
            self.assertRegex(listing, rf"{t}\s+已声明")
        set_targets_raw = toml.read_text(encoding="utf-8").replace(
            'profiles = ["general", "favicon", "ios", "android", "social"]', 'targets = ["svg", "cover"]')
        toml.write_text(set_targets_raw, encoding="utf-8")
        self.assertIn("未知目标", ws.logo("export", "demo", "--list", ok=False).stderr)


@unittest.skipUnless(HAS_RENDER, "需要 resvg 与 magick")
class ExportTest(unittest.TestCase):
    EXPECTED = {
        "svg": {"demo-primary-color.svg", "demo-primary-mono.svg", "demo-primary-reversed.svg"},
        "favicon": {"favicon-16.png", "favicon-32.png", "favicon-48.png", "favicon.ico", "apple-touch-icon.png"},
        "ios": {"AppIcon-1024.png"},
        "android": {f"mipmap-{d}/ic_launcher.png" for d in ("mdpi", "hdpi", "xhdpi", "xxhdpi", "xxxhdpi")}
        | {"play-store-512.png", "adaptive/ic_launcher_background.png", "adaptive/ic_launcher_foreground.png",
           "adaptive/ic_launcher_monochrome.png"},
        "avatar": {"demo-avatar@400.png", "demo-avatar@800.png", "demo-avatar@1080.png"},
    }

    @classmethod
    def setUpClass(cls):
        cls.ws = Workspace()
        cls.p = cls.ws.new_project()
        set_targets(cls.p)
        cls.ws.new_version()
        assert cls.ws.commit("projects", "[demo] A-v001").returncode == 0
        prepare_release(cls.ws)
        cls.dist = cls.p / "dist" / "r1"
        cls.out = cls.ws.logo("export", "demo")

    @classmethod
    def tearDownClass(cls):
        cls.ws.cleanup()

    def manifest(self) -> dict:
        return json.loads((self.dist / "manifest.json").read_text(encoding="utf-8"))

    def test_outputs_per_target(self):
        files = set(self.manifest()["files"])
        expected = {f"{t}/{n}" for t, names in self.EXPECTED.items() for n in names} | {"guide.md"}
        self.assertEqual(files, expected)  # 只有声明目标的产物，没有通用 PNG 套装与封面
        self.assertEqual(sorted(self.manifest()["targets"]), sorted(self.EXPECTED))

    def test_manifest_traceability(self):
        m = self.manifest()
        self.assertEqual((m["release"], m["based_on"], m["design_version"]), ("r1", "A-v001", "A-v001"))
        origin = {"git_head": self.ws.git("projects", "rev-parse", "--short", "HEAD").stdout.strip(),
                  "system_head": self.ws.git("system", "rev-parse", "--short", "HEAD").stdout.strip(),
                  "renderer": subprocess.run(["resvg", "--version"], capture_output=True, text=True).stdout.strip()}
        for target in self.EXPECTED:  # 每个目标记录生成它的版本
            self.assertEqual(m["targets"][target], origin, target)
        for name, digest in m["files"].items():
            self.assertEqual(hashlib.sha256((self.dist / name).read_bytes()).hexdigest(), digest, name)

    def test_deterministic_without_metadata(self):
        before = self.manifest()["files"]
        self.ws.logo("export", "demo")
        self.assertEqual(self.manifest()["files"], before)  # 同一发布再导出，逐字节相同
        for png in self.dist.rglob("*.png"):
            data, i = png.read_bytes(), 8
            while i < len(data):
                n, kind = struct.unpack(">I", data[i:i + 4])[0], data[i + 4:i + 8]
                self.assertNotIn(kind, (b"tEXt", b"zTXt", b"iTXt", b"tIME"), png.name)
                i += 12 + n

    def test_ico_is_valid(self):
        data = (self.dist / "favicon" / "favicon.ico").read_bytes()
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((reserved, kind, count), (0, 1, 3))
        sizes = []
        for k in range(count):
            w, h, _, _, _, _, length, offset = struct.unpack("<BBBBHHII", data[6 + 16 * k:22 + 16 * k])
            self.assertEqual(data[offset:offset + 8], b"\x89PNG\r\n\x1a\n")
            sizes.append(w)
        self.assertEqual(sizes, [16, 32, 48])

    def test_auto_composition(self):
        bg, white = PRIMARY[1:] + "FF", "FFFFFFFF"  # 模板：app_background=primary，icon_mode=reversed
        for f in ("ios/AppIcon-1024.png", "favicon/apple-touch-icon.png", "android/play-store-512.png",
                  "avatar/demo-avatar@400.png"):
            self.assertEqual(pixel(self.dist / f, "2", "2"), bg, f)               # 品牌底色铺满
            self.assertEqual(pixel(self.dist / f, "w/2", "h*0.6"), white, f)      # 图形居中且可见
        fg = self.dist / "android/adaptive/ic_launcher_foreground.png"
        self.assertTrue(pixel(fg, "2", "2").endswith("00"))                       # 前景层四周透明
        self.assertEqual(pixel(fg, "w/2", "h*0.55"), white)
        out = self.ws.logo("check").stdout
        self.assertNotIn("无法区分", out)
        self.assertNotIn("✗", out)

    def test_dist_untracked_and_stage_unchanged(self):
        self.assertEqual(self.ws.git("projects", "status", "--porcelain").stdout.strip(), "")
        st = tomllib.loads((self.p / "project.toml").read_text(encoding="utf-8"))["state"]
        self.assertEqual(st["stage"], "releasing")  # 导出不再自动改阶段

    def test_compare(self):
        out = self.ws.logo("compare", "demo").stdout
        self.assertIn("A-v001", out)
        self.assertTrue((self.ws.root / ".cache" / "demo" / "compare" / "A-v001.png").exists())
        self.assertIn("方向不存在：Z", self.ws.logo("compare", "demo", "A-v001", "Z-v001", ok=False).stderr)

    def test_partial_export_keeps_target_origin(self):
        mpath = self.dist / "manifest.json"
        original = mpath.read_text(encoding="utf-8")
        try:
            m = json.loads(original)
            m["targets"]["ios"] = {"git_head": "old1234", "system_head": "old5678", "renderer": "resvg 0.1"}
            mpath.write_text(json.dumps(m), encoding="utf-8")
            self.ws.logo("export", "demo", "svg")
            m = self.manifest()
            self.assertEqual(m["targets"]["ios"]["git_head"], "old1234")  # 没重新生成的目标保留原来源
            self.assertNotEqual(m["targets"]["svg"]["git_head"], "old1234")
            legacy = dict(m, targets=sorted(m["targets"]))  # 旧格式：目标列表，没有逐目标来源
            mpath.write_text(json.dumps(legacy), encoding="utf-8")
            self.ws.logo("export", "demo", "svg")
            m = self.manifest()
            self.assertEqual(m["targets"]["ios"], {"git_head": None, "system_head": None, "renderer": None})
            self.assertIsNotNone(m["targets"]["svg"]["git_head"])
        finally:
            self.ws.logo("export", "demo")

    def test_partial_export_and_atomic_failure(self):
        ws = self.ws
        fav_before = (self.dist / "favicon" / "favicon.ico").read_bytes()
        ws.logo("export", "demo", "svg")
        self.assertEqual((self.dist / "favicon" / "favicon.ico").read_bytes(), fav_before)  # 其他目标保留
        self.assertIn("favicon/favicon.ico", self.manifest()["files"])
        ios_before = (self.dist / "ios" / "AppIcon-1024.png").read_bytes()
        toml = self.p / "project.toml"
        original = toml.read_text(encoding="utf-8")
        broken = original.replace('app_background = "primary"', 'app_background = "nope"')
        self.assertNotEqual(broken, original)
        toml.write_text(broken, encoding="utf-8")
        try:
            self.assertNotEqual(ws.logo("export", "demo", "ios", ok=False).returncode, 0)
            self.assertEqual((self.dist / "ios" / "AppIcon-1024.png").read_bytes(), ios_before)  # 失败不动旧产物
            self.assertFalse(any(d.name.startswith((".build", ".old")) for d in (self.p / "dist").rglob(".*")))
        finally:
            toml.write_text(original, encoding="utf-8")

    def test_override_wins_and_visibility(self):
        ws = self.ws
        override = self.p / "releases" / "r1" / "layouts" / "app-icon.svg"
        override.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                            '<rect width="10" height="10" fill="#111111"/></svg>', encoding="utf-8")
        toml = self.p / "project.toml"
        original = toml.read_text(encoding="utf-8")
        try:
            ws.logo("export", "demo", "ios")
            self.assertEqual(pixel(self.dist / "ios/AppIcon-1024.png", "w/2", "h/2"), "111111FF")  # 用手工覆盖
            override.unlink()
            toml.write_text(original.replace('icon_mode = "reversed"', 'icon_mode = "color"'), encoding="utf-8")
            self.assertIn("自动排版的 app-icon 中图形与底色几乎无法区分", ws.logo("check").stdout)
        finally:
            override.unlink(missing_ok=True)
            toml.write_text(original, encoding="utf-8")
            ws.logo("export", "demo", "ios")

    def test_render(self):
        ws = self.ws
        ws.logo("render", "demo", "--mode", "mono", "--size", "300")
        out = self.p / "dist" / "render" / "r1" / "demo-primary-mono@300.png"  # 不在交付目录 dist/r1/ 里
        self.assertEqual(subprocess.run(["magick", "identify", "-format", "%wx%h", str(out)],
                                        capture_output=True, text=True).stdout, "300x300")
        ws.logo("render", "demo", "app-icon", "--format", "svg")
        self.assertIn("<rect", (out.parent / "demo-app-icon-color.svg").read_text(encoding="utf-8"))
        self.assertIn("没有版式", ws.logo("render", "demo", "horizontal", ok=False).stderr)
        self.assertIn("--mode 不适用", ws.logo("render", "demo", "avatar", "--mode", "mono", ok=False).stderr)
        legacy = self.dist / "render" / "old.png"  # 旧版 render 留在交付目录里的文件不计入 manifest
        legacy.parent.mkdir(exist_ok=True)
        legacy.write_bytes(b"x")
        ws.logo("export", "demo", "svg")
        self.assertFalse([f for f in self.manifest()["files"] if f.startswith("render/")])
        self.assertFalse((self.dist / "render" / "demo-primary-mono@300.png").exists())

    def test_android_safe_zone(self):
        ap = logo.load_targets()["android"]

        class P:
            cfg = {"export": {}}

            def mode_map(self, mode):
                return {}
        with tempfile.TemporaryDirectory() as td:
            tdir = Path(td)
            (tdir / "primary.svg").write_text(TRIANGLE.format(c=PRIMARY), encoding="utf-8")
            ok = logo.render_text(logo.android_foreground_text(P(), tdir, "color"), tdir / "ok.png", ap["adaptive_size"])
            self.assertIsNone(logo.android_safe_warning(ok, ap))
            (tdir / "primary.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                                              f'<path fill="{PRIMARY}" d="M0 0h100v100H0z"/></svg>', encoding="utf-8")
            bad = logo.render_text(logo.android_foreground_text(P(), tdir, "color"), tdir / "bad.png", ap["adaptive_size"])
            self.assertIsNotNone(logo.android_safe_warning(bad, ap))  # 铺满方块的四角超出 66dp 圆


# 依赖颜色区分结构的设计：方块上的圆，单色化后成为一整块
COLOR_RELIANT = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                 f'<rect x="5" y="5" width="90" height="90" fill="{PRIMARY}"/>'
                 '<circle cx="50" cy="50" r="30" fill="#FFB300"/></svg>\n')


@unittest.skipUnless(HAS_RENDER, "需要 resvg 与 magick")
class MonoStructureTest(unittest.TestCase):
    def setUp(self):
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
        logos = ["primary", "horizontal", "icon", "wordmark-cn", "wordmark-en", "wordmark-pinyin"]
        for n in logos:
            (ldir / f"{n}.svg").write_text(COLOR_RELIANT, encoding="utf-8")
        out = ws.logo("check").stdout
        self.assertIn("primary.svg（同内容：icon、horizontal、wordmark-cn、wordmark-en、wordmark-pinyin）", out)
        kept = COLOR_RELIANT.replace("#FFB300", "#FFFFFF").replace(PRIMARY, "#000000")
        for n in logos:
            for mode, txt in (("mono", kept), ("reversed", kept.replace("#000000", "#X").replace("#FFFFFF", "#000000")
                                                         .replace("#X", "#FFFFFF"))):
                (ldir / f"{n}.{mode}.svg").write_text(txt, encoding="utf-8")
        self.assertNotIn("非彩色版只保留", ws.logo("check").stdout)


    def test_auto_icons_warn_when_icon_mode_collapses_mark(self):
        ws = self.ws
        set_targets(self.p, '["svg", "android"]')
        ws.new_version()
        ws.commit("projects", "[demo] A-v001")
        ws.logo("release", "demo", "--from", "A-v001")
        (self.p / "releases" / "r1" / "layouts" / "primary.svg").write_text(COLOR_RELIANT, encoding="utf-8")
        self.assertIn("app-icon、Android 前景层（icon_mode=reversed）", ws.logo("check").stdout)  # 模板默认 reversed
        toml = self.p / "project.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace('icon_mode = "reversed"', 'icon_mode = "color"'),
                        encoding="utf-8")
        self.assertNotIn("Android 前景层（icon_mode", ws.logo("check").stdout)


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
        self.assertIn("shaper=harfbuzz-", on)
        self.assertIn("features=calt=0,clig=0,dlig=0,kern=1,liga=0", on)
        self.assertIn("kern=0", off)
        self.assertNotIn('text="A--V"', on)  # 注释中的 “--” 已转义
        self.assertNotEqual(on.split("advance=")[1], off.split("advance=")[1])
        same = ws.logo("outline", "--font", "inter", "--text", "A--V", "--size", "2048", "--fill", PRIMARY,
                       "--project", "demo", "--features=-kern").stdout  # 以 - 开头的值须用 = 连写
        self.assertEqual(same, off)  # --no-kern 与 --features -kern 等价
        self.assertEqual(ws.logo("outline", "--font", "inter", "--text", "A--V", "--size", "2048", "--fill", PRIMARY,
                                 "--project", "demo").stdout, on)  # 同样参数，结果逐字相同

    def test_opt_in_features_and_missing_chars(self):
        ws = self.ws
        plain = ws.logo("outline", "--font", "inter", "--text=1x2", "--fill", PRIMARY).stdout
        alt = ws.logo("outline", "--font", "inter", "--text=1x2", "--fill", PRIMARY, "--features", "calt").stdout
        self.assertNotEqual(plain.splitlines()[1], alt.splitlines()[1])  # calt 把 x 换成乘号，路径不同
        self.assertIn("calt=1", alt)
        r = ws.logo("outline", "--font", "inter", "--text", "A猫B狗", "--fill", PRIMARY, ok=False)
        self.assertIn("不包含这些字符：猫狗", r.stderr)
        r = ws.logo("outline", "--font", "inter", "--text", "A", "--fill", PRIMARY, "--lang", "zh --><script>", ok=False)
        self.assertIn("--lang 须为语言标签", r.stderr)  # 不合法的值不能写进 SVG 注释

    def test_combining_marks(self):
        def outline(text, tracking="0"):
            out = self.ws.logo("outline", "--font", "inter", "--text", text, "--size", "2048", "--fill", PRIMARY,
                               "--tracking", tracking).stdout
            bbox = [float(v) for v in out.split("bbox=")[1].split()[0].split(",")]
            return bbox, float(out.split("advance=")[1].split()[0])
        (_, top_x, _, _), _ = outline("X")
        (_, top_acc, _, _), _ = outline("X\u0301")
        self.assertLess(top_acc, top_x - 300)  # 附加符号按 HarfBuzz 的 y 偏移抬到大写字母上方（SVG 中 y 向下）
        (_, _, right_x, _), _ = outline("x")
        (_, _, right_acc, _), _ = outline("x\u0301")
        self.assertLessEqual(right_acc, right_x + 50)  # 附加符号按 x 偏移回到字母上方，不越出字母右侧
        _, adv = outline("x\u0301", tracking="100")
        self.assertEqual(adv, 1118 + 100)  # 零宽附加符号后不再加字间距

    @unittest.skipUnless(any(f.name == "SourceHanSansSC-Bold.otf" for f in FONT_BINARIES), "思源黑体未下载")
    def test_cjk_and_latin(self):
        out = self.ws.logo("outline", "--font", "source-han-sans-sc-bold", "--text", "猫狗 Cat&Dog", "--fill", PRIMARY,
                           "--lang", "zh-Hans").stdout
        self.assertIn("lang=zh-Hans", out)
        self.assertIn('<path fill="', out)

    def test_missing_font_points_to_fetch(self):
        ws = Workspace()
        self.addCleanup(ws.cleanup)
        r = ws.logo("outline", "--font", "inter", "--text", "A", "--fill", PRIMARY, ok=False)
        self.assertIn("./logo fonts fetch inter", r.stderr)


if __name__ == "__main__":
    unittest.main()
