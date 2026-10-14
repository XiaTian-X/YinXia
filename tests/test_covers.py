"""封面模块（第二期）：平台登记的拆分、校验与查看。"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import PRIMARY, TRIANGLE, Workspace, fill_doc, logo

import covers  # noqa: E402  helpers 已把 tools/ 加入 sys.path

GOOD = '''
[ratios]
"3x1" = { width = 1500, height = 500 }
[[platform]]
id = "x"
name = "X"
ratio = "3x1"
width = 1500
height = 500
safe = [0, 0, 1500, 500]
occlusions = [[0, 200, 400, 300]]
verified_on = "2099-01-01"
source = "https://example.com"
'''


class PlatformConfigTest(unittest.TestCase):
    def check(self, text: str) -> tuple[list[str], list[str]]:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "cover-platforms.toml"
            f.write_text(text, encoding="utf-8")
            original, covers.PLATFORMS_TOML = covers.PLATFORMS_TOML, f
            try:
                errors, warnings = [], []
                covers.check_platforms(errors, warnings)
                return errors, warnings
            finally:
                covers.PLATFORMS_TOML = original

    def test_real_config_is_clean(self):
        errors, warnings = [], []
        covers.check_platforms(errors, warnings)
        self.assertEqual((errors, warnings), ([], []))
        cfg = covers.load_config()
        self.assertEqual(len(cfg["platform"]), 15)
        self.assertNotIn("generic", {k for p in cfg["platform"] for k in p})  # 通用比例只在 [ratios]
        self.assertTrue({"4x3", "3x4", "1x1"} <= set(cfg["ratios"]))

    def test_logo_config_has_no_covers(self):
        self.assertFalse((logo.CONFIG / "export-profiles.toml").exists())
        self.assertFalse(hasattr(logo, "load_profiles"))

    def test_valid_minimal(self):
        self.assertEqual(self.check(GOOD), ([], []))

    def test_errors(self):
        cases = {
            "id 重复": GOOD + GOOD[GOOD.index("[[platform]]"):],
            "未在 [ratios] 中定义": GOOD.replace('ratio = "3x1"', 'ratio = "5x1"'),
            "safe": GOOD.replace("safe = [0, 0, 1500, 500]", "safe = [0, 0, 1600, 500]"),
            "occlusions": GOOD.replace("[[0, 200, 400, 300]]", "[[1200, 200, 400, 300]]"),
            "width/height 须为正整数": GOOD.replace("width = 1500\nheight = 500\nsafe", "width = 0\nheight = 500\nsafe"),
            "id 须为小写字母": GOOD.replace('id = "x"', 'id = "X_1"'),
            "缺少 name": GOOD.replace('name = "X"\n', ""),
        }
        for frag, text in cases.items():
            with self.subTest(frag):
                errors, _ = self.check(text)
                self.assertTrue(any(frag in e for e in errors), errors)

    def test_spec_basis_warnings(self):
        _, warnings = self.check(GOOD.replace('source = "https://example.com"', 'basis = "common"'))
        self.assertTrue(any("cover.x" in w and "verify_note" in w for w in warnings), warnings)

    def test_crop_ratio(self):
        self.assertEqual(covers.crop_ratio({"width": 1500, "height": 500}, {"width": 1500, "height": 500}), 0)
        wechat = covers.crop_ratio({"width": 900, "height": 383}, {"width": 1640, "height": 624})
        self.assertAlmostEqual(wechat, 0.106, places=2)  # 2.35:1 从 2.63:1 画布左右共裁约 11%


class CoverCliTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)

    def test_list(self):
        out = self.ws.logo("cover", "platforms").stdout
        self.assertEqual(sum(1 for ln in out.splitlines() if "2026-" in ln), 15)
        self.assertIn("wechat-mp", out)
        only = self.ws.logo("cover", "platforms", "--ratio", "3x1").stdout
        self.assertEqual({ln.split()[0] for ln in only.splitlines() if "2026-" in ln}, {"x", "weibo", "zhihu"})
        self.assertIn("未知画幅", self.ws.logo("cover", "platforms", "--ratio", "7x2", ok=False).stderr)

    def test_broken_config_blocks_system_commit(self):
        cfg = self.ws.root / "config" / "cover-platforms.toml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace('ratio = "16x9"', 'ratio = "16x10"', 1),
                       encoding="utf-8")
        r = self.ws.commit("system", "x")
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(any("16x10" in ln for ln in self.ws.blocked(r)))

    def test_single_logo_module_when_run_as_script(self):
        code = ("import runpy, sys\n"
                "sys.path.insert(0, 'tools')  # 与直接运行脚本一致：脚本所在目录在搜索路径中\n"
                "sys.argv = ['logo', 'cover', 'platforms']\n"
                "try:\n    runpy.run_path('tools/logo.py', run_name='__main__')\n"
                "except SystemExit:\n    pass\n"
                "print('SAME' if sys.modules['covers'].logo is sys.modules['logo'] "
                "and sys.modules['logo'].__name__ == '__main__' else 'DIFFERENT')\n")
        r = subprocess.run([str(self.ws.root / ".venv" / "bin" / "python"), "-c", code], cwd=self.ws.root,
                           env=self.ws.env, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "SAME", r.stderr)


class CoverConfigFileTest(unittest.TestCase):
    def test_title_with_quotes_round_trips(self):
        cfg = {"cover": {"name": "spring", "title": '春季"上新"\\活动', "created": "2026-10-14", "release": "r1",
                         "platforms": ["x"]},
               "state": {"status": "draft", "live_on": [], "live_since": "", "updated": "2026-10-14"}}
        with tempfile.TemporaryDirectory() as td:
            covers.write_cover_cfg(Path(td), cfg)
            self.assertEqual(covers.cover_cfg(Path(td)), cfg)


class GeometryTest(unittest.TestCase):
    CANVAS = {"width": 1500, "height": 500}

    def test_crop_window(self):
        self.assertEqual(covers.crop_window({"width": 1500, "height": 500}, self.CANVAS), (0, 0, 1500, 500))
        x, y, w, h = covers.crop_window({"width": 980, "height": 300}, self.CANVAS)  # 微博更宽：上下裁
        self.assertEqual((x, w), (0, 1500))
        self.assertAlmostEqual(h, 1500 * 300 / 980)
        self.assertAlmostEqual(y, (500 - h) / 2)
        x, y, w, h = covers.crop_window({"width": 1000, "height": 500}, self.CANVAS)  # 更窄：左右裁
        self.assertEqual((x, y, w, h), (250, 0, 1000, 500))

    def test_map_box_and_common_safe(self):
        plat = {"width": 750, "height": 250, "safe": [75, 25, 600, 200]}
        win = covers.crop_window(plat, self.CANVAS)
        self.assertEqual(covers.map_box(plat["safe"], plat, win), (150, 50, 1200, 400))  # 放大 2 倍
        weibo = {"width": 980, "height": 300}
        cs = covers.common_safe([plat, weibo], self.CANVAS)
        wy = covers.crop_window(weibo, self.CANVAS)[1]
        self.assertEqual(cs, (150, 50, 1200, 400) if wy <= 50 else cs)
        self.assertIsNone(covers.common_safe([{"width": 100, "height": 100, "safe": [0, 0, 10, 10]},
                                              {"width": 100, "height": 100, "safe": [90, 90, 10, 10]}],
                                             {"width": 100, "height": 100}))


@unittest.skipUnless(logo.shutil.which("resvg"), "需要 resvg")
class CoverWorkflowTest(unittest.TestCase):
    """立项、元数据、设计稿规则、Logo 指纹、素材登记、状态，均经由真实命令与提交钩子。"""

    @classmethod
    def setUpClass(cls):
        cls.ws = Workspace()
        ws, cls.p = cls.ws, cls.ws.new_project()
        ws.new_version()
        assert ws.commit("projects", "[demo] A-v001").returncode == 0
        ws.logo("decision", "demo", "finalize", "--title", "定稿")
        fill_doc(next((cls.p / "decisions").glob("*.md")))
        ws.logo("release", "demo", "--from", "A-v001")
        r = cls.p / "releases" / "r1"
        (r / "layouts" / "primary.svg").write_text(TRIANGLE.format(c=PRIMARY), encoding="utf-8")
        fill_doc(r / "release.md")
        with (r / "release.md").open("a", encoding="utf-8") as f:
            f.write("\n依据：decisions/001-finalize.md\n")
        fill_doc(r / "guide.md")
        ws.logo("index")
        assert ws.commit("projects", "[demo] r1").returncode == 0
        ws.logo("cover", "new", "demo", "spring", "--platforms", "youtube,x,weibo,xiaohongshu", "--title", "春季活动")
        cls.c = cls.p / "covers" / "001-spring"
        fill_doc(cls.c / "cover.md")
        ws.logo("index")
        r = ws.commit("projects", "[demo] cover 001")
        assert r.returncode == 0, ws.blocked(r)

    @classmethod
    def tearDownClass(cls):
        cls.ws.cleanup()

    def tearDown(self):  # 每个用例结束后撤销未提交的改动
        self.ws.git("projects", "reset", "-q", "--hard")
        self.ws.git("projects", "clean", "-qfd")

    def svg(self, key: str = "3x1") -> Path:
        return self.c / f"{key}.svg"

    def blocked(self, *frags: str):
        r = self.ws.commit("projects", "x")
        self.assertNotEqual(r.returncode, 0, "应被拦截")
        text = "\n".join(self.ws.blocked(r))
        for f in frags:
            self.assertIn(f, text)

    def test_new_derives_ratios_and_starters(self):
        self.assertEqual(sorted(f.name for f in self.c.glob("*.svg")), ["16x9.svg", "3x1.svg", "3x4.svg"])
        cfg = covers.cover_cfg(self.c)
        self.assertEqual((cfg["cover"]["release"], cfg["state"]["status"]), ("r1", "draft"))
        text = self.svg().read_text(encoding="utf-8")
        for part in ('id="background"', 'id="logo"', 'data-release="r1"', 'data-mode="reversed"', 'id="content"',
                     'id="guides"', 'id="common-safe"'):
            self.assertIn(part, text)
        self.assertIn("weibo", text)  # 参考线标注了同画幅的各平台

    def test_unfilled_brief_blocked_on_new_cover(self):
        self.ws.logo("cover", "new", "demo", "summer", "--platforms", "og")
        self.ws.logo("index")
        self.blocked("002-summer/cover.md", "模板占位")

    def test_logo_tamper_blocked_but_move_allowed(self):
        import re as _re
        text = self.svg().read_text(encoding="utf-8")
        moved = _re.sub(r'(id="logo"[^>]*transform=")[^"]*"', r'\1translate(500 120) scale(0.5)"', text, count=1)
        self.assertNotEqual(moved, text)
        self.svg().write_text(moved, encoding="utf-8")
        self.assertEqual(self.ws.commit("projects", "[demo] move logo").returncode, 0)  # 移动、缩放允许
        self.addCleanup(self.ws.git, "projects", "reset", "-q", "--hard", "HEAD~1")
        self.svg().write_text(moved.replace('d="M10 90 50 10 90 90Z"', 'd="M10 90 50 20 90 90Z"'), encoding="utf-8")
        self.blocked("Logo 与 r1/primary.svg", "不能改动内部")

    def test_text_rules(self):
        text = self.svg().read_text(encoding="utf-8")
        self.svg().write_text(text.replace('<g id="content">', '<g id="content"><text>标题</text>'), encoding="utf-8")
        self.blocked("文字须转曲")
        self.svg().write_text(text, encoding="utf-8")
        self.assertIn("<text", text)  # 参考线层里的标注文字允许
        self.svg().write_text(text.replace('viewBox="0 0 1500 500"', 'viewBox="0 0 1500 600"', 1), encoding="utf-8")
        self.blocked("viewBox 须为 0 0 1500 500")

    def test_assets_registration(self):
        text = self.svg().read_text(encoding="utf-8")
        img = self.ws.tmp / "photo.png"
        subprocess.run(["resvg", "-w", "8", str(self.svg()), str(img)], check=True)
        with_img = text.replace('<g id="content">', '<image href="assets/001-photo.png" width="10" height="10"/>'
                                                    '<g id="content">')
        self.svg().write_text(with_img, encoding="utf-8")
        self.blocked("位图 assets/001-photo.png 未登记")
        self.ws.logo("cover", "asset", "demo", "001", str(img), "--name", "photo", "--source", "自摄", "--license", "自有版权")
        fill_doc(self.c / "assets" / "001-photo.md")
        r = self.ws.commit("projects", "[demo] cover asset")
        self.assertEqual(r.returncode, 0, self.ws.blocked(r))
        (self.c / "assets" / "001-photo.png").write_bytes(img.read_bytes() + b"\0")
        self.blocked("已冻结的文件被修改")  # 登记后不可替换
        self.ws.git("projects", "reset", "-q", "--hard", "HEAD~1")  # 撤销本用例的提交，不影响其他用例

    def test_ratio_files(self):
        self.svg("3x4").unlink()
        self.blocked("缺少画幅稿 3x4.svg")
        self.ws.git("projects", "reset", "-q", "--hard")  # blocked() 已暂存删除，需回到 HEAD
        (self.c / "9x16.svg").write_text(self.svg().read_text(encoding="utf-8"), encoding="utf-8")
        self.assertIn("不需要画幅 9x16", self.ws.logo("check").stdout)

    def test_status_and_list(self):
        self.assertIn("不是该封面的目标平台", self.ws.logo("cover", "status", "demo", "001", "live", "--on", "douyin",
                                                ok=False).stderr)
        self.ws.logo("cover", "status", "demo", "001", "live", "--on", "x,youtube")
        st = covers.cover_cfg(self.c)["state"]
        self.assertEqual((st["status"], st["live_on"]), ("live", ["x", "youtube"]))
        self.assertTrue(st["live_since"])
        self.assertIn("x, youtube", self.ws.logo("cover", "list", "demo").stdout)
        self.assertEqual(self.ws.commit("projects", "[demo] cover live").returncode, 0)
        self.addCleanup(self.ws.git, "projects", "reset", "-q", "--hard", "HEAD~1")
        self.ws.logo("cover", "status", "demo", "001", "archived")
        self.assertEqual(covers.cover_cfg(self.c)["state"]["live_on"], [])
        self.assertIn("--on 只用于 live", self.ws.logo("cover", "status", "demo", "001", "draft", "--on", "x",
                                                     ok=False).stderr)

    def test_cover_fonts_counted(self):
        text = self.svg().read_text(encoding="utf-8")
        self.svg().write_text(text.replace('<g id="content">', '<g id="content"><!-- outline: font=inter size=10 '
                                           'text="A" bbox=0,0,1,1 advance=1 -->'), encoding="utf-8")
        self.blocked("SVG 中用字体 inter 转曲", "001-spring/3x1.svg")

    def test_gitignore_allowlist(self):
        for rel in ("covers/001-spring/notes.txt", "covers/001-spring/assets/raw.psd", "covers/scratch.svg",
                    "covers/001-spring/export/x.png"):
            self.ws.write(f"projects/demo/{rel}", "x")
        self.assertEqual(self.ws.git("projects", "add", "-A", "--dry-run").stdout.strip(), "")


PHOTO = "#E76F51"


@unittest.skipUnless(logo.shutil.which("resvg") and logo.shutil.which("magick"), "需要 resvg 与 magick")
class CoverOutputTest(unittest.TestCase):
    """第 2 步：预览与导出（去参考线、按平台裁切、照片渲染、结果可重复）。"""

    @classmethod
    def setUpClass(cls):
        cls.ws = Workspace()
        ws, cls.p = cls.ws, cls.ws.new_project()
        ws.new_version()
        assert ws.commit("projects", "[demo] A-v001").returncode == 0
        ws.logo("release", "demo", "--from", "A-v001")
        (cls.p / "releases" / "r1" / "layouts" / "primary.svg").write_text(TRIANGLE.format(c=PRIMARY), encoding="utf-8")
        ws.logo("cover", "new", "demo", "spring", "--platforms", "x,weibo,xiaohongshu")
        cls.c = cls.p / "covers" / "001-spring"
        photo = ws.tmp / "photo.png"
        subprocess.run(["magick", "-size", "200x100", f"xc:{PHOTO}", str(photo)], check=True)
        ws.logo("cover", "asset", "demo", "001", str(photo), "--name", "photo", "--source", "测试", "--license", "自有版权")
        svg = cls.c / "3x1.svg"
        svg.write_text(svg.read_text(encoding="utf-8").replace(
            '<g id="content">', '<image href="assets/001-photo.png" x="700" y="60" width="700" height="380" '
                                'preserveAspectRatio="none"/><g id="content">'), encoding="utf-8")
        cls.dist = cls.p / "dist" / "covers" / "001-spring"
        cls.out = ws.logo("cover", "export", "demo", "001")

    @classmethod
    def tearDownClass(cls):
        cls.ws.cleanup()

    @staticmethod
    def px(png: Path, x: int, y: int) -> str:
        return subprocess.run(["magick", str(png), "-format", f"%[hex:p{{{x},{y}}}]", "info:"],
                              capture_output=True, text=True, check=True).stdout.strip().upper()[:6]

    @staticmethod
    def size(png: Path) -> str:
        return subprocess.run(["magick", "identify", "-format", "%wx%h", str(png)],
                              capture_output=True, text=True, check=True).stdout

    def manifest(self) -> dict:
        import json
        return json.loads((self.dist / "manifest.json").read_text(encoding="utf-8"))

    def test_files_and_sizes(self):
        expected = {"x-1500x500.png", "weibo-980x300.png", "xiaohongshu-1080x1440.png"}
        self.assertEqual(set(self.manifest()["files"]), expected)
        for name in expected:
            png = self.dist / name
            w, h = (int(v) for v in self.size(png).split("x"))
            self.assertEqual(f"{w}x{h}", name.rsplit("-", 1)[1][:-4])
            for x, y in ((1, h // 2), (w - 2, h // 2), (w // 2, 1), (w // 2, h - 2)):  # 铺满裁切：四边都是不透明底色，没有留边
                hexa = subprocess.run(["magick", str(png), "-format", f"%[hex:p{{{x},{y}}}]", "info:"],
                                      capture_output=True, text=True, check=True).stdout.strip().upper()
                self.assertEqual(hexa[:6], PRIMARY[1:], (name, x, y))
                self.assertTrue(len(hexa) == 6 or hexa.endswith("FF"), (name, x, y, hexa))

    def test_guides_removed_and_photo_rendered(self):
        x = self.dist / "x-1500x500.png"
        self.assertEqual(self.px(x, 750, 21), PRIMARY[1:])   # 共同安全区参考线所在位置：只剩底色
        self.assertEqual(self.px(x, 1495, 15), PRIMARY[1:])  # 平台标注文字所在位置：只剩底色
        self.assertEqual(self.px(x, 1050, 250), PHOTO[1:])   # 已登记的照片被渲染进成品

    def test_manifest_and_determinism(self):
        import hashlib
        m = self.manifest()
        self.assertEqual((m["cover"], m["release"], m["title"]), ("001-spring", "r1", "spring"))
        self.assertEqual(m["designs"]["3x1.svg"], hashlib.sha256((self.c / "3x1.svg").read_bytes()).hexdigest())
        before = m["files"]
        self.ws.logo("cover", "export", "demo", "001")
        self.assertEqual(self.manifest()["files"], before)
        for png in self.dist.glob("*.png"):
            data, i = png.read_bytes(), 8
            while i < len(data):
                n, kind = int.from_bytes(data[i:i + 4], "big"), data[i + 4:i + 8]
                self.assertNotIn(kind, (b"tEXt", b"zTXt", b"iTXt", b"tIME"), png.name)
                i += 12 + n

    def test_partial_export_keeps_others(self):
        weibo = (self.dist / "weibo-980x300.png").read_bytes()
        self.assertIn("x-1500x500.png", self.ws.logo("cover", "export", "demo", "001", "x").stdout)
        self.assertEqual((self.dist / "weibo-980x300.png").read_bytes(), weibo)
        self.assertIn("weibo-980x300.png", self.manifest()["files"])

    def test_refusals_and_transparency(self):
        r = self.ws.logo("cover", "export", "demo", "001", "douyin", ok=False)
        self.assertIn("不是该封面的目标平台", r.stderr)
        svg = self.c / "3x1.svg"
        original = svg.read_text(encoding="utf-8")
        try:
            svg.write_text(original.replace('d="M10 90 50 10 90 90Z"', 'd="M10 90 50 20 90 90Z"'), encoding="utf-8")
            self.assertIn("封面未通过检查", self.ws.logo("cover", "export", "demo", "001", ok=False).stderr)
            import re as _re
            svg.write_text(_re.sub(r'<rect id="background"[^>]*/>', "", original), encoding="utf-8")
            self.assertIn("含透明区域", self.ws.logo("cover", "export", "demo", "001", "x").stdout)
        finally:
            svg.write_text(original, encoding="utf-8")
            self.ws.logo("cover", "export", "demo", "001")

    def test_preview(self):
        out = self.ws.logo("cover", "preview", "demo", "001").stdout
        self.assertIn("3x1：画布 | x 裁切 | weibo 裁切 | x 缩略图 | weibo 缩略图", out)
        d = self.ws.root / ".cache" / "demo" / "covers" / "001-spring"
        for name in ("preview.png", "3x1-canvas.png", "x.png", "x-thumb.png", "xiaohongshu-thumb.png"):
            self.assertTrue((d / name).exists(), name)
        self.assertEqual(self.size(d / "x-thumb.png"), "360x120")          # 横幅：手机常见宽度
        self.assertEqual(self.size(d / "xiaohongshu-thumb.png"), "180x240")  # 竖版
        self.assertNotEqual(self.px(d / "x.png", 30, 150), PRIMARY[1:])     # X 左下头像遮挡区叠了红色


if __name__ == "__main__":
    unittest.main()
