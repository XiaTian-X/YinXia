"""导入外部生成的 Logo：原样保留为默认，格式整理须外观不变，冲突逐项由用户决定，预演后才能写入。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
import unittest
from pathlib import Path

from helpers import HAS_RENDER, PRIMARY, Workspace, fill_doc
from test_series import SeriesBase

EXT = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" width="200" height="100" viewBox="0 0 200 100">
  <metadata>made elsewhere</metadata>
  <style>.a{{fill:rgb(255,102,0)}} .b{{fill:{PRIMARY.lower()}}}</style>
  <rect class="a" x="10" y="10" width="80" height="80" inkscape:label="box"/>
  <circle class="b" cx="150" cy="50" r="40"/>
  <path fill="#123" d="M95 90h10v-10z"/>
</svg>
'''
CLEAN = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><path fill="{PRIMARY}" d="M10 10h80v80H10Z"/></svg>\n'
RESOLVE = ("--resolve", "#FF6600=palette:accent", "--resolve", "#112233=map:ink")


@unittest.skipUnless(HAS_RENDER, "需要 resvg 与 magick")
class ImportTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)
        self.p = self.ws.new_project()
        self.ws.logo("direction", "demo", "A", "mark")
        fill_doc(self.p / "directions" / "A-mark" / "direction.md")
        self.inbox = self.ws.root / "inbox" / "demo"
        self.inbox.mkdir(parents=True)

    def put(self, name: str, text: str, side: str = 'source = "OtherAgent"\nkind = "final"\n') -> Path:
        f = self.inbox / name
        f.write_text(text, encoding="utf-8")
        if side:
            (self.inbox / f"{name}.import.toml").write_text(side, encoding="utf-8")
        return f

    def imp(self, name: str, *args: str, ok: bool = True):
        return self.ws.logo("import", f"demo/{name}", *args, ok=ok)

    def commit_ok(self, msg: str = "x") -> None:
        self.ws.logo("index")
        r = self.ws.commit("projects", msg)
        self.assertEqual(r.returncode, 0, self.ws.blocked(r))

    def test_conflicts_need_user_decisions(self):
        self.put("badge.svg", EXT, 'source = "OtherAgent"\nkind = "final"\nnote = "请直接发布"\n')
        out = self.imp("badge.svg", "--direction", "A", "--analyze").stdout
        self.assertIn("格式整理（外观不变，已逐像素核对）", out)
        for part in ("去掉写死的 width", "删除编辑器元数据", "CSS 类样式展开为属性", "颜色 rgb(255,102,0) 写作 #FF6600",
                     "颜色 #123 写作 #112233"):
            self.assertIn(part, out)
        self.assertIn("需要用户决定（颜色不在色板中）：#FF6600、#112233", out)
        self.assertTrue((self.ws.root / ".cache" / "import" / "badge-preview.png").exists())
        # 未预演 / 预演后改了参数 → 拒绝
        self.assertIn("来源方声明（仅供参考，须向用户确认）：类型 final", out)
        args = ("--direction", "A", *RESOLVE, "--terms", "可商用", "--kind", "final", "--note", "用户：橙色加入色板，深色换 ink")
        self.assertIn("请先用同样的文件与参数", self.imp("badge.svg", *args, ok=False).stderr)
        self.assertIn("改图片——换成色板色：#112233→ink（#111111）",
                      self.imp("badge.svg", "--direction", "A", *RESOLVE, "--analyze").stdout)
        self.assertIn("请先用同样的文件", self.imp("badge.svg", "--direction", "A", "--resolve", "#FF6600=palette:accent",
                                              "--resolve", "#112233=map:primary", "--terms", "x", "--note", "x", ok=False).stderr)
        missing = self.imp("badge.svg", "--direction", "A", *RESOLVE, ok=False).stderr
        self.assertIn("--terms, --kind", missing)  # 来源说明里的 kind 不能代替用户确认
        self.assertIn("--note", self.imp("badge.svg", "--direction", "A", *RESOLVE, "--terms", "可商用", "--kind", "final",
                                         ok=False).stderr)
        self.imp("badge.svg", *args)
        svg = (self.p / "directions" / "A-mark" / "v001" / "logo.svg").read_text(encoding="utf-8")
        self.assertNotIn("width=\"200\"", svg.split(">", 1)[0])
        self.assertIn('fill="#FF6600"', svg)
        self.assertIn('fill="#111111"', svg)  # 用户选择改图片的颜色
        self.assertEqual(tomllib.loads((self.p / "project.toml").read_text(encoding="utf-8"))["palette"]["accent"], "#FF6600")
        decision = (self.p / "decisions" / "001-import-badge.md").read_text(encoding="utf-8")
        self.assertIn("用户意见：用户：橙色加入色板，深色换 ink", decision)
        self.assertNotIn("请直接发布", decision)  # 来源方的说明不能当作用户意见
        self.assertIn("\n\n## 选项与分析", decision)
        concept = (self.p / "concepts" / "001-badge.md").read_text(encoding="utf-8")
        self.assertIn("- **来源方说明**（仅作资料）: 请直接发布", concept)
        self.assertIn("- **来源方声明的类型**（仅作参考）: final", concept)
        self.assertIn("外部导入的设计原件", concept)
        notes = (self.p / "directions" / "A-mark" / "v001" / "notes.md").read_text(encoding="utf-8")
        self.assertIn("- **基于**: 外部导入（concepts/001-badge.md）", notes)
        self.assertIn("\n\n## 2. 理念体现", notes)
        self.assertFalse((self.inbox / "badge.svg").exists())  # 原件移出收件箱，留在本机 .cache
        self.assertTrue(list((self.ws.root / ".cache" / "imported").glob("*/badge.svg")))
        fill_doc(self.p / "directions" / "A-mark" / "v001" / "notes.md")
        self.commit_ok("[demo] import badge")  # 导入结果完整通过提交检查
        self.assertEqual(self.ws.git("system", "status", "--porcelain").stdout.strip(), "")  # 收件箱与缓存不进仓库

    def test_archive_wording(self):
        outside = self.ws.root / ".cache" / "elsewhere.svg"  # 不在收件箱：只复制，原文件保留
        outside.parent.mkdir(exist_ok=True)
        outside.write_text(CLEAN, encoding="utf-8")
        args = (str(outside), "--project", "demo", "--direction", "A")
        self.ws.logo("import", *args, "--analyze")
        out = self.ws.logo("import", *args, "--source", "x", "--terms", "可商用", "--kind", "candidate").stdout
        self.assertIn("原件已复制一份到本机", out)
        self.assertTrue(outside.exists())
        self.put("in.svg", CLEAN.replace("M10 10", "M20 20"))  # 收件箱：移走
        self.imp("in.svg", "--direction", "A", "--analyze")
        out = self.imp("in.svg", "--direction", "A", "--terms", "可商用", "--kind", "candidate").stdout
        self.assertIn("原件已从收件箱移至本机", out)
        self.assertFalse((self.inbox / "in.svg").exists())

    def test_clean_file_kept_byte_for_byte(self):
        self.put("clean.svg", CLEAN)
        out = self.imp("clean.svg", "--direction", "A", "--analyze").stdout
        self.assertIn("没有冲突，可原样导入", out)
        self.imp("clean.svg", "--direction", "A", "--terms", "自有版权", "--kind", "final")
        self.assertEqual((self.p / "directions" / "A-mark" / "v001" / "logo.svg").read_text(encoding="utf-8"), CLEAN)
        self.assertFalse(list((self.p / "decisions").glob("*import*")))  # 没有冲突就没有决策

    def test_hard_rules_only_as_concept(self):
        text = CLEAN.replace("</svg>", '<text x="10" y="50">ACME</text></svg>')
        self.put("t.svg", text)
        out = self.imp("t.svg", "--direction", "A", "--analyze").stdout
        self.assertIn("✗ 系统硬性规则：<text> 文字必须转为路径", out)
        self.assertIn("只能 --as-concept 登记为概念图，或请来源修正后重交", out)
        self.assertIn("请先用同样的文件", self.imp("t.svg", "--direction", "A", "--terms", "x", "--kind", "final", ok=False).stderr)
        self.imp("t.svg", "--as-concept", "--analyze")
        out = self.imp("t.svg", "--as-concept", "--terms", "不清楚", "--kind", "candidate").stdout
        self.assertIn("已作为设计依据登记", out)
        self.assertFalse(list((self.p / "directions" / "A-mark").glob("v*")))
        self.assertIn("- **使用条款**: 未确认", (self.p / "concepts" / "001-t.md").read_text(encoding="utf-8"))
        # 即使预演记录被伪造（本机缓存可被改写），含硬性问题的文件也不能成为版本
        self.put("t2.svg", text)
        self.ws.py(
            "import sys, json, argparse; sys.path.insert(0, 'tools'); import importer\n"
            "a = argparse.Namespace(project='demo', init=False, as_concept=False, direction='A', new_direction=None, resolve=None)\n"
            "data = (importer.INBOX / 'demo' / 't2.svg').read_bytes()\n"
            "importer.PREVIEWS.mkdir(parents=True, exist_ok=True)\n"
            "(importer.PREVIEWS / 'previewed.json').write_text(json.dumps({importer.preview_key(data, a): 'x'}))")
        self.assertIn("存在系统硬性规则问题", self.imp("t2.svg", "--direction", "A", "--terms", "x", "--kind", "final",
                                              ok=False).stderr)
        self.put("e.svg", '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "x">]>' + CLEAN)
        self.assertIn("DOCTYPE / ENTITY", self.imp("e.svg", "--direction", "A", "--analyze", ok=False).stderr)

    def test_cleanup_that_changes_look_is_not_applied(self):
        squeezed = CLEAN.replace('viewBox="0 0 100 100"', 'width="200" height="100" viewBox="0 0 100 100" preserveAspectRatio="none"')
        self.put("s.svg", squeezed)
        out = self.imp("s.svg", "--direction", "A", "--analyze").stdout
        self.assertIn("格式整理后外观与原件不一致", out)
        self.assertNotIn("格式整理（外观不变", out)

    def test_changed_file_needs_new_preview(self):
        broken = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><path fill="#00897B" d="M10 10h80v80H10Z"/></svg>\n'
        self.put("b.svg", broken)
        args = ("--new-direction", "fresh", "--terms", "x", "--kind", "final")
        self.imp("b.svg", *args, "--analyze")
        (self.inbox / "b.svg").write_text(broken.replace("viewBox", "viewbox"), encoding="utf-8")  # 文件变了：须重新预演
        self.assertIn("请先用同样的文件", self.imp("b.svg", *args, ok=False).stderr)
        self.assertEqual([d.name for d in (self.p / "directions").iterdir() if d.is_dir()], ["A-mark"])

    def test_raster_becomes_concept_without_metadata(self):
        png = self.inbox / "photo.jpg"  # 扩展名不对：按内容识别为 PNG
        subprocess.run(["magick", "-size", "64x64", "xc:#E76F51", "-set", "comment", "GPS 31.2,121.4", f"PNG:{png}"], check=True)
        (self.inbox / "photo.jpg.import.toml").write_text('source = "ImageTool"\nkind = "candidate"\n', encoding="utf-8")
        out = self.imp("photo.jpg", "--analyze").stdout
        self.assertIn("位图：只能登记为概念图", out)
        self.imp("photo.jpg", "--terms", "可商用", "--kind", "candidate")
        saved = self.p / "concepts" / "001-photo.png"
        self.assertTrue(saved.exists())
        self.assertNotIn(b"GPS", saved.read_bytes())  # 去掉 EXIF / 注释等元数据
        self.assertFalse(list((self.p / "directions" / "A-mark").glob("v*")))

    def test_init_project_from_image(self):
        ws = self.ws
        ws.logo("new", "fresh", "--name", "新品牌")
        fill_doc(ws.projects / "fresh" / "brief.md")
        f = self.ws.root / "inbox" / "orange.svg"
        f.write_text(EXT.replace(PRIMARY.lower(), "#2a9d8f"), encoding="utf-8")
        args = ("--project", "fresh", "--init", "--new-direction", "mark", "--resolve", "#FF6600=palette:orange")
        out = ws.logo("import", "orange.svg", *args, "--analyze").stdout
        self.assertIn("色板（取自图中颜色）：orange #FF6600、color_1 #2A9D8F、color_2 #112233", out)
        self.assertNotIn("需要用户决定", out)  # 初始化没有冲突，只是补全信息
        ws.logo("import", "orange.svg", *args, "--source", "OtherAgent", "--terms", "可商用", "--kind", "final")
        cfg = tomllib.loads((ws.projects / "fresh" / "project.toml").read_text(encoding="utf-8"))
        self.assertEqual(cfg["palette"], {"orange": "#FF6600", "color_1": "#2A9D8F", "color_2": "#112233"})
        self.assertEqual(cfg["export"]["app_background"], "orange")
        self.assertEqual(cfg["color_modes"]["mono"]["orange"], "#000000")
        self.assertFalse(list((ws.projects / "fresh" / "decisions").glob("*import*")))
        self.assertIn('fill="#FF6600"', (ws.projects / "fresh" / "directions" / "A-mark" / "v001" / "logo.svg").read_text(encoding="utf-8"))
        f.write_text(CLEAN, encoding="utf-8")
        self.assertIn("已开始设计，不能用 --init", ws.logo("import", "orange.svg", "--project", "fresh", "--init",
                                                   "--direction", "A", "--analyze", ok=False).stderr)

    def test_unconfirmed_terms_warn_on_release(self):
        self.put("clean.svg", CLEAN)
        self.imp("clean.svg", "--direction", "A", "--analyze")
        self.imp("clean.svg", "--direction", "A", "--terms", "不清楚", "--kind", "final")
        fill_doc(self.p / "directions" / "A-mark" / "v001" / "notes.md")
        self.ws.logo("release", "demo", "--from", "A-v001")
        self.assertIn("当前发布 r1 基于外部导入的 A-v001，其使用条款未确认", self.ws.logo("check").stdout)

    def test_inbox_listing_and_stale_reminder(self):
        f = self.put("a.svg", CLEAN, side="")
        items = json.loads(self.ws.logo("inbox", "--json").stdout)
        self.assertEqual((items[0]["file"], items[0]["type"], items[0]["meta"]["project"]), ("demo/a.svg", "svg", "demo"))
        self.assertEqual(items[0]["missing"], ["source"])
        self.assertEqual(items[0]["confirm_with_user"], ["terms", "kind"])  # 条款与类型始终须用户确认
        self.assertNotIn("收件箱有", self.ws.logo("check").stdout)
        old = f.stat().st_mtime - 8 * 86400
        os.utime(f, (old, old))
        self.assertIn("收件箱有 1 个文件放了 7 天以上未导入", self.ws.logo("check").stdout)
        self.assertIn("kind", self.ws.logo("inbox", "--spec").stdout)
        (self.inbox / "a.svg.import.toml").write_text('project = "elsewhere"\n', encoding="utf-8")
        item = json.loads(self.ws.logo("inbox", "--json").stdout)[0]
        self.assertEqual(item["meta"]["project"], "demo")  # 去向以所在目录为准
        self.assertIn("以所在目录 demo 为准", item["warning"])

    def test_unsafe_inputs(self):
        ws = self.ws
        target = self.put("real.svg", CLEAN, side="")
        (self.inbox / "link.svg").symlink_to(target)
        self.assertIn("符号链接", self.imp("link.svg", "--analyze", ok=False).stderr)
        (self.inbox / "big.svg").write_bytes(b"<svg>" + b" " * (6 << 20))
        self.assertIn("超过 5MB", self.imp("big.svg", "--analyze", ok=False).stderr)
        (self.inbox / "x.txt").write_text("hello", encoding="utf-8")
        self.assertIn("不是可识别的", self.imp("x.txt", "--analyze", ok=False).stderr)
        loose = ws.root / "inbox" / "loose.svg"
        loose.write_text(CLEAN, encoding="utf-8")
        self.assertIn("须指定 --project", ws.logo("import", "loose.svg", "--analyze", ok=False).stderr)


@unittest.skipUnless(HAS_RENDER, "需要 resvg 与 magick")
class SeriesImportTest(SeriesBase):
    def test_sub_new_color_needs_series_decision(self):
        ws = self.ws
        food = self.family()
        inbox = ws.root / "inbox" / "food"
        inbox.mkdir(parents=True)
        (inbox / "x.svg").write_text(CLEAN.replace(PRIMARY, "#FF6600"), encoding="utf-8")
        base = ("import", "food/x.svg", "--direction", "A", "--resolve", "#FF6600=palette:accent", "--source", "Other",
                "--terms", "可商用", "--kind", "candidate", "--note", "用户：子品牌要橙色")
        ws.logo(*base, "--analyze")
        self.assertIn("--series-rule exception", ws.logo(*base, ok=False).stderr)
        ws.logo(*base, "--series-rule", "exception", "--analyze")
        ws.logo(*base, "--series-rule", "exception")
        md = (ws.projects / "_series" / "pets" / "series.md").read_text(encoding="utf-8")
        self.assertIn("子品牌例外：food 导入 x 新增颜色\n- **颜色**：accent #FF6600\n- **用户意见**：用户：子品牌要橙色", md)
        self.assertIn("accent", tomllib.loads((food / "project.toml").read_text(encoding="utf-8"))["palette"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAS_RENDER, "需要 resvg 与 magick")
class CoverImportTest(unittest.TestCase):
    """封面导入：外部图作为底图，Logo 由系统嵌入；图中有 Logo 拒绝；比例不符逐个画幅由用户决定。"""

    def setUp(self):
        from test_workflow import prepare_release
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)
        self.p = self.ws.new_project()
        self.ws.new_version()
        assert self.ws.commit("projects", "[demo] A-v001").returncode == 0
        prepare_release(self.ws)
        self.inbox = self.ws.root / "inbox" / "demo"
        self.inbox.mkdir(parents=True)

    def image(self, name: str, size: str, color: str = "#E76F51") -> str:
        f = self.inbox / name
        subprocess.run(["magick", "-size", size, f"xc:{color}", "-set", "comment", "GPS 31.2,121.4", f"PNG:{f}"], check=True)
        (self.inbox / f"{name}.import.toml").write_text('source = "ImageTool"\n', encoding="utf-8")
        return f"demo/{name}"

    def ci(self, *args: str, ok: bool = True):
        return self.ws.logo("cover", "import", "demo", *args, ok=ok)

    def test_new_cover_with_ratio_decisions(self):
        ws = self.ws
        img = self.image("hero.png", "1600x900")
        base = (img, "--platforms", "youtube,x", "--contains-logo", "no", "--contains-text", "no")
        out = self.ci(*base, "--analyze").stdout
        self.assertIn("16x9（2560×1440）：裁切铺满", out)  # 比例一致
        self.assertIn("3x1（1500×500）：比例与图片不符，需要用户决定", out)
        self.assertIn("--fit 3x1=crop", out)
        self.assertTrue((ws.root / ".cache" / "import" / "hero-cover-preview.png").exists())
        self.assertIn("请先用同样的文件", self.ci(*base, "--terms", "可商用", "--kind", "final", ok=False).stderr)
        self.ci(*base, "--fit", "3x1=pad", "--analyze")
        self.assertIn("请先用同样的文件", self.ci(*base, "--fit", "3x1=pad", "--name", "other", "--terms", "x", "--kind", "final",
                                              ok=False).stderr)  # 改了名称也须重新预演
        self.assertIn("--terms", self.ci(*base, "--fit", "3x1=pad", ok=False).stderr)
        self.ci(*base, "--fit", "3x1=pad", "--terms", "可商用", "--kind", "final")
        c = self.p / "covers" / "001-hero"
        wide, banner = (c / "16x9.svg").read_text(encoding="utf-8"), (c / "3x1.svg").read_text(encoding="utf-8")
        self.assertIn('id="imported-background" href="assets/001-hero.png"', wide)
        self.assertIn('preserveAspectRatio="xMidYMid slice"', wide)
        self.assertIn('preserveAspectRatio="xMidYMid meet"', banner)  # 用户选择留边
        self.assertIn('<g id="logo"', wide)  # Logo 仍由系统从发布嵌入
        asset = c / "assets" / "001-hero.png"
        self.assertNotIn(b"GPS", asset.read_bytes())
        md = (c / "assets" / "001-hero.md").read_text(encoding="utf-8")
        self.assertIn("- **铺放**: 16x9 裁切铺满；3x1 完整放入（留边）", md)
        self.assertIn("- **图中文字**: 无", md)
        self.assertFalse((self.inbox / "hero.png").exists())
        fill_doc(c / "cover.md")
        fill_doc(c / "assets" / "001-hero.md")
        ws.logo("index")
        r = ws.commit("projects", "[demo] cover import")
        self.assertEqual(r.returncode, 0, ws.blocked(r))  # 导入的封面完整通过提交检查

    def test_logo_in_image_refused_and_text_recorded(self):
        img = self.image("a.png", "1500x500")
        self.assertIn("封面里的 Logo 只能来自发布", self.ci(img, "--platforms", "x", "--contains-logo", "yes",
                                                     "--contains-text", "no", "--analyze", ok=False).stderr)
        base = (img, "--platforms", "x", "--contains-logo", "no", "--contains-text", "yes")
        self.assertIn("图中有文字：位图文字无法转曲", self.ci(*base, "--analyze").stdout)
        self.ci(*base, "--terms", "不清楚", "--kind", "candidate")
        md = (self.p / "covers" / "001-a" / "assets" / "001-a.md").read_text(encoding="utf-8")
        self.assertIn("- **图中文字**: 有", md)
        self.assertIn("**授权**：未确认", md)

    def test_existing_cover_per_ratio(self):
        ws = self.ws
        ws.logo("cover", "new", "demo", "spring", "--platforms", "x,youtube")
        c = self.p / "covers" / "001-spring"
        img = self.image("banner.png", "1500x500")
        common = ("--contains-logo", "no", "--contains-text", "no")
        self.assertIn("须用 --ratio", self.ci(img, "--cover", "001", *common, "--analyze", ok=False).stderr)
        self.assertIn("只用于给已有封面", self.ci(img, "--ratio", "3x1", "--platforms", "x", *common, "--analyze", ok=False).stderr)
        self.assertIn("没有画幅 9x16", self.ci(img, "--cover", "001", "--ratio", "9x16", *common, "--analyze", ok=False).stderr)
        base = (img, "--cover", "001", "--ratio", "3x1", *common)
        self.ci(*base, "--analyze")
        self.ci(*base, "--terms", "可商用", "--kind", "final")
        self.assertIn("imported-background", (c / "3x1.svg").read_text(encoding="utf-8"))
        self.assertNotIn("imported-background", (c / "16x9.svg").read_text(encoding="utf-8"))  # 只动指定画幅
        img2 = self.image("banner2.png", "1500x500", "#264653")
        base2 = (img2, "--cover", "001", "--ratio", "3x1", *common)
        self.ci(*base2, "--analyze")
        self.ci(*base2, "--terms", "可商用", "--kind", "final")
        text = (c / "3x1.svg").read_text(encoding="utf-8")
        self.assertEqual(text.count('id="imported-background"'), 1)  # 再导入是替换，不是叠加
        self.assertIn("assets/002-banner2.png", text)

    def test_svg_image_rendered(self):
        f = self.inbox / "art.svg"
        f.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 100"><rect width="300" height="100" fill="#264653"/></svg>',
                     encoding="utf-8")
        (self.inbox / "art.svg.import.toml").write_text('source = "VectorTool"\n', encoding="utf-8")
        base = ("demo/art.svg", "--platforms", "x", "--contains-logo", "no", "--contains-text", "no")
        self.assertIn("SVG 已按原样渲染为 PNG", self.ci(*base, "--analyze").stdout)
        self.ci(*base, "--terms", "自有版权", "--kind", "final")
        self.assertTrue((self.p / "covers" / "001-art" / "assets" / "001-art.png").exists())
        f.write_text('<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "x">]><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3 1"/>',
                     encoding="utf-8")
        self.assertIn("DOCTYPE / ENTITY", self.ci(*base, "--analyze", ok=False).stderr)

    def test_cover_brief(self):
        out = self.ws.logo("handoff", "brief", "demo", "--type", "cover", "--platforms", "x,youtube").stdout
        for part in ("设计封面底图", "不要画任何 Logo", "### 16x9：2560×1440 像素", "### 3x1：1500×500 像素",
                     "共同安全区：", "遮挡区（头像、按钮会挡住", "inbox/demo/<英文短名>-<画幅>.png"):
            self.assertIn(part, out)
        self.assertNotIn("SVG 硬性要求", out)
        self.assertIn("--platforms", self.ws.logo("handoff", "brief", "demo", "--type", "cover", ok=False).stderr)
