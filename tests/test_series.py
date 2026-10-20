"""需求访谈与品牌系列：按访谈结果新建独立/系列项目、系列规则、继承与检查。"""
from __future__ import annotations

import re
import subprocess
import tomllib
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from helpers import HAS_RENDER, PRIMARY, TRIANGLE, Workspace, fill_doc

SQUARE = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><path fill="{PRIMARY}" d="M10 10h80v80H10Z"/></svg>\n'


class SeriesBase(unittest.TestCase):
    """系列测试的公共工作区与辅助方法（本身不含测试）。"""

    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)

    # -- 辅助 -------------------------------------------------------------------
    def toml(self, slug: str) -> dict:
        return tomllib.loads((self.ws.projects / slug / "project.toml").read_text(encoding="utf-8"))

    def released_master(self, slug: str = "brand") -> Path:
        """一个已发布（r1，主版为正方形）的项目，作为母品牌。"""
        ws, p = self.ws, self.ws.new_project(slug)
        ws.new_version(slug, svg=SQUARE)
        assert ws.commit("projects", f"[{slug}] A-v001").returncode == 0
        ws.logo("decision", slug, "finalize", "--title", "定稿")
        fill_doc(next((p / "decisions").glob("*.md")))
        ws.logo("release", slug, "--from", "A-v001")
        r = p / "releases" / "r1"
        fill_doc(r / "release.md")
        with (r / "release.md").open("a", encoding="utf-8") as f:
            f.write("\n依据：decisions/001-finalize.md\n")
        fill_doc(r / "guide.md")
        ws.logo("index")
        assert ws.commit("projects", f"[{slug}] r1").returncode == 0
        return p

    def series(self, sid: str, arch: str) -> Path:
        self.ws.logo("series", "new", sid, "--name", sid.title(), "--architecture", arch)
        d = self.ws.projects / "_series" / sid
        fill_doc(d / "series.md")
        return d

    def family(self, arch: str = "endorsed") -> Path:
        """母品牌 brand（已发布 r1）+ 子品牌 food（A-v001 以母品牌主版起稿，已填完）。"""
        ws = self.ws
        self.released_master("brand")
        self.series("pets", arch)
        ws.logo("series", "add", "pets", "brand", "--role", "master")
        ws.logo("new", "food", "--series", "pets", "--role", "sub")
        food = ws.projects / "food"
        fill_doc(food / "brief.md")
        ws.logo("direction", "food", "A", "paw")
        fill_doc(next((food / "directions").glob("A-*")) / "direction.md")
        if arch == "independent":
            ws.new_version("food", "A", "paw")
        else:
            ws.logo("version", "food", "A", "--from-master")
            fill_doc(food / "directions" / "A-paw" / "v001" / "notes.md")
        return food

    def review(self, result: str, note: str = "用户：可以", ok: bool = True):
        return self.ws.logo("series", "review", "pets", "--result", result, "--note", note, "--with", "food=A-v001", ok=ok)

    def released_family(self, arch: str = "endorsed") -> Path:
        """family() 之后子品牌 food 也完成系列评审并发布 r1（主版为三角形，与母品牌的正方形不同）。"""
        ws = self.ws
        food = self.family(arch)
        ws.logo("series", "review", "pets", "--result", "pass", "--note", "通过", "--with", "food=A-v001")
        ws.logo("release", "food", "--from", "A-v001")
        r = food / "releases" / "r1"
        (r / "layouts" / "primary.svg").write_text(TRIANGLE.format(c=PRIMARY), encoding="utf-8")
        fill_doc(r / "release.md")
        with (r / "release.md").open("a", encoding="utf-8") as f:
            f.write("\n依据：decisions/001-x.md\n")
        ws.logo("decision", "food", "x", "--title", "定稿")
        fill_doc(next((food / "decisions").glob("*.md")))
        fill_doc(r / "guide.md")
        return food

    def release_master_again(self) -> None:
        """母品牌基于 r1 出 r2（填完发布文档）。"""
        r = self.ws.projects / "brand" / "releases" / "r2"
        self.ws.logo("release", "brand", "--from", "r1")
        fill_doc(r / "release.md")
        with (r / "release.md").open("a", encoding="utf-8") as f:
            f.write("\n依据：decisions/001-finalize.md\n")
        fill_doc(r / "guide.md")

    def commit_ok(self, msg: str = "x") -> None:
        self.ws.logo("index")
        r = self.ws.commit("projects", msg)
        self.assertEqual(r.returncode, 0, self.ws.blocked(r))



class SeriesTest(SeriesBase):
    # -- 独立项目与访谈门槛 ----------------------------------------------------------
    def test_standalone_project_from_intake(self):
        ws = self.ws
        ws.logo("new", "solo", "--name", "独立", "--targets", "ios,svg")
        cfg = self.toml("solo")
        self.assertEqual(cfg["export"]["targets"], ["svg", "ios"])  # 按固定顺序
        self.assertNotIn("series", cfg)
        brief = (ws.projects / "solo" / "brief.md").read_text(encoding="utf-8")
        self.assertIn("- **项目类型**：独立 Logo（不属于系列）", brief)
        self.assertIn("- **生成目标**：svg、ios", brief)
        self.assertIn("未知目标：cover", ws.logo("new", "x", "--targets", "svg,cover", ok=False).stderr)
        self.assertIn("须同时给出", ws.logo("new", "x", "--role", "master", ok=False).stderr)
        self.assertFalse((ws.projects / "x").exists())
        ws.logo("index")
        self.assertNotIn("## ", (ws.projects / "PROJECTS.md").read_text(encoding="utf-8"))  # 没有系列时看板不分组

    def test_direction_requires_completed_intake(self):
        ws = self.ws
        ws.logo("new", "solo")
        r = ws.logo("direction", "solo", "A", "mountain", ok=False)
        self.assertIn("需求访谈", r.stderr)
        self.assertFalse((ws.projects / "solo" / "directions" / "A-mountain").exists())
        brief = ws.projects / "solo" / "brief.md"
        fill_doc(brief)
        unconfirmed = re.sub(r"- \*\*用户确认的要点\*\*：.*", "- **用户确认的要点**：（访谈结束时向用户复述的需求摘要，"
                             "以及用户的确认或修改意见）", brief.read_text(encoding="utf-8"))
        brief.write_text(unconfirmed, encoding="utf-8")
        self.assertIn("用户确认的要点", ws.logo("direction", "solo", "A", "mountain", ok=False).stderr)  # 未经确认
        fill_doc(brief)
        ws.logo("direction", "solo", "A", "mountain")

    # -- 系列规则 -------------------------------------------------------------------
    def test_series_rules_by_architecture(self):
        ws = self.ws
        for arch, word in (("unified", "统一型"), ("endorsed", "背书型"), ("independent", "独立型")):
            ws.logo("series", "new", f"s-{arch}", "--architecture", arch)
            text = (ws.projects / "_series" / f"s-{arch}" / "series.md").read_text(encoding="utf-8")
            self.assertIn(word, text)
        self.assertIn("共享的视觉基因", (ws.projects / "_series" / "s-endorsed" / "series.md").read_text(encoding="utf-8"))
        self.assertIn("系列已存在", ws.logo("series", "new", "s-unified", "--architecture", "unified", ok=False).stderr)
        ws.logo("index")
        r = ws.commit("projects", "series")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("_series/s-endorsed/series.md: 还有", "\n".join(ws.blocked(r)))  # 未填完不能提交
        for arch in ("unified", "endorsed", "independent"):
            fill_doc(ws.projects / "_series" / f"s-{arch}" / "series.md")
        self.commit_ok("series")
        md = ws.projects / "_series" / "s-unified" / "series.md"
        md.write_text(md.read_text(encoding="utf-8").replace("测试内容", "改写", 1), encoding="utf-8")
        r = ws.commit("projects", "rewrite")
        self.assertIn("只允许在末尾追加", "\n".join(ws.blocked(r)))  # 系列规则只追加

    # -- 继承型系列（统一型 / 背书型）------------------------------------------------
    def test_endorsed_sub_inherits_master(self):
        ws = self.ws
        self.series("pets", "endorsed")
        self.assertIn("还没有母品牌", ws.logo("new", "food", "--series", "pets", "--role", "sub", ok=False).stderr)
        ws.logo("new", "draft", "--series", "pets", "--role", "master")
        self.assertIn("还没有发布", ws.logo("new", "food", "--series", "pets", "--role", "sub", ok=False).stderr)
        self.released_master("brand")
        self.assertIn("已有母品牌 draft", ws.logo("series", "add", "pets", "brand", "--role", "master", ok=False).stderr)
        draft = ws.projects / "draft" / "project.toml"  # 换由已发布的 brand 担任母品牌
        draft.write_text(draft.read_text(encoding="utf-8").split("\n[series]")[0] + "\n", encoding="utf-8")
        ws.logo("series", "add", "pets", "brand", "--role", "master")
        master = ws.projects / "brand" / "project.toml"
        master.write_text(master.read_text(encoding="utf-8").replace('icon_mode = "reversed"', 'icon_mode = "mono"'),
                          encoding="utf-8")
        ws.logo("new", "food", "--name", "食品", "--series", "pets", "--role", "sub")
        sub, m = self.toml("food"), self.toml("brand")
        self.assertEqual(sub["palette"], m["palette"])
        self.assertEqual(sub["color_modes"], m["color_modes"])
        self.assertEqual((sub["export"]["app_background"], sub["export"]["icon_mode"]), ("primary", "mono"))
        self.assertEqual(sub["series"], {"id": "pets", "role": "sub", "master_release": "r1"})
        brief = (ws.projects / "food" / "brief.md").read_text(encoding="utf-8")
        self.assertIn("系列「Pets」（pets，背书型）的子品牌（母品牌：brand）", brief)
        self.assertIn("`_series/pets/series.md`", brief)
        fill_doc(ws.projects / "food" / "brief.md")
        self.commit_ok("[food] init")

    def test_unified_sub_starts_from_master(self):
        ws = self.ws
        self.released_master("brand")
        self.series("fam", "unified")
        ws.logo("series", "add", "fam", "brand", "--role", "master")
        ws.logo("new", "brand-shop", "--series", "fam", "--role", "sub")
        fill_doc(ws.projects / "brand-shop" / "brief.md")
        ws.logo("direction", "brand-shop", "A", "lockup")
        fill_doc(next((ws.projects / "brand-shop" / "directions").glob("A-*")) / "direction.md")
        self.assertIn("不能与", ws.logo("version", "brand-shop", "A", "--from-master", "--blank", ok=False).stderr)
        ws.logo("version", "brand-shop", "A", "--from-master")
        v = ws.projects / "brand-shop" / "directions" / "A-lockup" / "v001"
        self.assertEqual((v / "logo.svg").read_text(encoding="utf-8"), SQUARE)
        self.assertIn("- **基于**: 母品牌 brand r1/primary.svg（系列 fam）", (v / "notes.md").read_text(encoding="utf-8"))
        fill_doc(v / "notes.md")
        self.commit_ok("[brand-shop] A-v001")  # 继承的色板覆盖母品牌主版的颜色
        self.assertIn("只用于系列子品牌", ws.logo("version", "brand", "A", "--from-master", ok=False).stderr)

    def test_independent_series_needs_no_master(self):
        ws = self.ws
        self.series("group", "independent")
        ws.logo("new", "one", "--series", "group", "--role", "sub")
        cfg = self.toml("one")
        self.assertEqual(cfg["series"], {"id": "group", "role": "sub"})  # 不继承、不记录母品牌发布
        self.assertEqual(cfg["palette"]["primary"], "#000000")
        fill_doc(ws.projects / "one" / "brief.md")
        ws.logo("direction", "one", "A", "own")
        self.assertIn("独立设计", ws.logo("version", "one", "A", "--from-master", ok=False).stderr)

    # -- 已有项目加入系列、检查、看板、目录 ----------------------------------------------
    def test_add_existing_project(self):
        ws = self.ws
        ws.logo("new", "solo")
        self.series("group", "independent")
        ws.logo("series", "add", "group", "solo", "--role", "master")
        brief = (ws.projects / "solo" / "brief.md").read_text(encoding="utf-8")
        self.assertIn("- **项目类型**：系列「Group」（group，独立型）的母品牌", brief)
        self.assertIn("已属于系列 group", ws.logo("series", "add", "group", "solo", "--role", "sub", ok=False).stderr)
        ws.logo("new", "other")
        self.assertIn("系列不存在：nope", ws.logo("series", "add", "nope", "other", "--role", "sub", ok=False).stderr)
        self.assertIn("母品牌  solo", ws.logo("series").stdout)

    def test_membership_errors(self):
        ws = self.ws
        self.released_master("brand")
        self.series("pets", "endorsed")
        ws.logo("series", "add", "pets", "brand", "--role", "master")
        ws.logo("new", "food", "--series", "pets", "--role", "sub")
        fill_doc(ws.projects / "food" / "brief.md")
        self.commit_ok("ok")
        food = ws.projects / "food" / "project.toml"
        good = food.read_text(encoding="utf-8")
        cases = [
            (good.replace('master_release = "r1"', 'master_release = "r9"'), "master_release 'r9' 不是母品牌 brand 的发布"),
            (good.replace('role = "sub"', 'role = "boss"'), "role 须为 master / sub"),
            (good.replace('role = "sub"\nmaster_release = "r1"  # 继承母品牌色板与起稿所依据的发布', 'role = "master"'),
             "系列 pets 有多个母品牌"),
            (good.replace('id = "pets"', 'id = "gone"'), "所属系列 'gone' 不存在"),
        ]
        for text, msg in cases:
            with self.subTest(msg=msg):
                self.assertNotEqual(text, good)
                food.write_text(text, encoding="utf-8")
                ws.logo("index")
                r = ws.commit("projects", "bad")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(msg, "\n".join(ws.blocked(r)))
                ws.git("projects", "reset", "-q", "--hard")
        brand = ws.projects / "brand" / "project.toml"  # 继承型系列的母品牌离开后，子品牌失去依据
        brand.write_text(brand.read_text(encoding="utf-8").split("\n[series]")[0] + "\n", encoding="utf-8")
        self.assertIn("没有母品牌", ws.logo("check", ok=False).stdout)

    def test_board_groups_by_series(self):
        ws = self.ws
        self.released_master("brand")
        ws.logo("new", "solo")
        self.series("pets", "endorsed")
        ws.logo("series", "add", "pets", "brand", "--role", "master")
        ws.logo("new", "a-food", "--series", "pets", "--role", "sub")
        ws.logo("index")
        board = (ws.projects / "PROJECTS.md").read_text(encoding="utf-8")
        self.assertIn("## 独立项目", board)
        self.assertIn("## 系列：Pets（pets，背书型）", board)
        section = board.split("## 系列：Pets")[1]
        self.assertLess(section.index("[brand]"), section.index("[a-food]"))  # 母品牌在前
        self.assertIn("| [brand](brand/brief.md) | 母品牌 |", section)
        self.assertNotIn("[solo]", section)
        self.assertNotIn("[brand]", board.split("## 系列")[0])  # 系列成员不再出现在独立项目表

    def test_series_directory_allowlist(self):
        ws = self.ws
        self.series("pets", "independent")
        for rel in ("_series/pets/notes.txt", "_series/pets/draft/x.md", "_series/readme.md"):
            ws.write(f"projects/{rel}", "x")
        listed = ws.git("projects", "add", "-A", "--dry-run").stdout
        self.assertIn("_series/pets/series.toml", listed)
        self.assertNotIn("notes.txt", listed)
        self.assertNotIn("readme.md", listed)
        ws.git("projects", "add", "-f", "_series/pets/notes.txt")
        ws.logo("index")
        r = ws.commit("projects", "junk", add=False)
        self.assertIn("_series/pets/notes.txt: 不在规定的目录结构中", "\n".join(ws.blocked(r)))


class SeriesReviewTest(SeriesBase):
    """第二期第 1 步：系列对比、评审记录与子品牌发布门槛（先给用户看、听用户结论，再发布）。"""

    def test_compare_and_checklist(self):
        self.family()
        out = self.ws.logo("series", "compare", "pets").stdout
        self.assertIn("brand（母品牌，r1）、food（子品牌，A-v001）", out)  # 母品牌在前；未发布的子品牌取最新版本
        self.assertIn("1. 共享的视觉基因 · 构图与网格：测试内容", out)  # 评审清单来自 series.md
        self.assertNotIn("系列名", out)
        self.assertNotIn("改版跟进", out)  # 流程约定不是设计评审项
        self.assertIn("背书组合 · 位置与比例", out)
        self.assertIn("--with brand=r1 --with food=A-v001", out)
        png = self.ws.root / ".cache" / "_series" / "pets" / "compare.png"
        import subprocess
        w, h = (int(v) for v in subprocess.run(["magick", "identify", "-format", "%w %h", str(png)],
                                               capture_output=True, text=True, check=True).stdout.split())
        self.assertGreater(h, w)  # 2 列 × 3 行
        for bad, msg in (("food=A-v009", "版本不存在"), ("nobody=A-v001", "不是系列 pets 的成员"), ("food", "写法为")):
            self.assertIn(msg, self.ws.logo("series", "compare", "pets", "--with", bad, ok=False).stderr)

    def test_sub_release_needs_passed_review(self):
        ws = self.ws
        self.family()
        r = ws.logo("release", "food", "--from", "A-v001", ok=False)
        self.assertIn("须先做系列评审", r.stderr)
        self.assertFalse((ws.projects / "food" / "releases" / "r1").exists())
        self.assertIn("--note", self.review("pass", "   ", ok=False).stderr)  # 必须写明用户意见
        self.review("revise", "猫耳太尖，\n和母品牌不一致")
        self.assertIn("须先做系列评审", ws.logo("release", "food", "--from", "A-v001", ok=False).stderr)
        self.review("pass")
        md = (ws.projects / "_series" / "pets" / "series.md").read_text(encoding="utf-8")
        self.assertIn("系列评审：需修改\n- **对比**：brand r1、food A-v001\n- **用户意见**：猫耳太尖， 和母品牌不一致", md)
        self.review("revise", "又改主意了")  # 以最近一次评审为准
        self.assertIn("须先做系列评审", ws.logo("release", "food", "--from", "A-v001", ok=False).stderr)
        self.review("pass")
        ws.logo("release", "food", "--from", "A-v001")
        self.assertNotIn("没有通过的系列评审", ws.logo("check").stdout)
        series_md = ws.projects / "_series" / "pets" / "series.md"
        series_md.write_text(series_md.read_text(encoding="utf-8").replace("系列评审：通过", "系列评审：需修改"),
                             encoding="utf-8")
        self.assertIn("当前发布 r1（A-v001）没有通过的系列评审记录", ws.logo("check").stdout)  # 历史发布只提醒
        ws.logo("release", "brand", "--from", "r1")  # 母品牌与基于发布的再发布不设门槛
        ws.logo("release", "food", "--from", "r1")

    def test_missing_rules_file_does_not_crash(self):
        ws = self.ws
        self.family()
        (ws.projects / "_series" / "pets" / "series.md").unlink()
        self.assertIn("缺少系列规则 series.md", ws.logo("check", ok=False).stdout)  # 报错而不是中断检查
        self.assertIn("须先做系列评审", ws.logo("release", "food", "--from", "A-v001", ok=False).stderr)
        self.assertIn("缺少系列规则", self.review("pass", ok=False).stderr)
        self.assertIn("food（子品牌，A-v001）", ws.logo("series", "compare", "pets").stdout)

    def test_independent_sub_has_no_gate(self):
        self.family("independent")
        self.ws.logo("release", "food", "--from", "A-v001")

    def test_board_shows_master_release(self):
        ws = self.ws
        self.family()
        ws.logo("index")
        board = (ws.projects / "PROJECTS.md").read_text(encoding="utf-8")
        self.assertIn("| 项目 | 角色 | 母品牌发布 |", board)
        self.assertIn("| [food](food/brief.md) | 子品牌 | r1 |", board)
        self.assertIn("| [brand](brand/brief.md) | 母品牌 | — |", board)
        ws.logo("release", "brand", "--from", "r1")
        ws.logo("index")
        self.assertIn("| [food](food/brief.md) | 子品牌 | r1（最新 r2） |",
                      (ws.projects / "PROJECTS.md").read_text(encoding="utf-8"))


class SeriesSyncTest(SeriesBase):
    """第二期第 2 步：母品牌出新发布后，按用户决定跟进色板，旧色值保护冻结的设计。"""

    def master_update(self) -> None:
        """母品牌改色（secondary、ink 改值，新增 accent，单色版 secondary 改为留白）并发布 r2。"""
        toml = self.ws.projects / "brand" / "project.toml"
        text = toml.read_text(encoding="utf-8")
        for old, new in (('secondary = "#666666"', 'secondary = "#777777"'), ('ink = "#111111"', 'ink = "#222222"\naccent = "#ABCDEF"'),
                         ('[color_modes.mono]\nprimary = "#000000"\nsecondary = "#000000"',
                          '[color_modes.mono]\nprimary = "#000000"\nsecondary = "#FFFFFF"')):
            self.assertEqual(text.count(old), 1, old)
            text = text.replace(old, new)
        toml.write_text(text, encoding="utf-8")
        self.release_master_again()

    def sync(self, palette: str, note: str = "用户：跟随", ok: bool = True):
        return self.ws.logo("series", "sync", "food", "--palette", palette, "--note", note, ok=ok)

    def test_follow_all_keeps_frozen_colors(self):
        ws = self.ws
        self.family()
        ws.new_version("food", "A", "paw", svg=SQUARE.replace(PRIMARY, "#666666"))  # A-v002 用母品牌原来的 secondary
        self.commit_ok("[food] A-v001 A-v002")
        self.master_update()
        out = ws.logo("check").stdout
        self.assertIn("母品牌 brand 已发布 r2（子品牌依据 r1），secondary #666666→#777777、ink #111111→#222222、"
                      "新增 accent #ABCDEF", out)
        self.assertIn("--note", self.sync("all", "  ", ok=False).stderr)  # 必须写明用户的决定
        self.assertIn("可选：secondary, ink, accent", self.sync("nope", ok=False).stderr)
        self.sync("all", "用户：全部跟随")
        cfg = self.toml("food")
        self.assertEqual({k: cfg["palette"].get(k) for k in ("secondary", "secondary_r1", "ink", "ink_r1", "accent")},
                         {"secondary": "#777777", "secondary_r1": "#666666", "ink": "#222222", "ink_r1": None,
                          "accent": "#ABCDEF"})  # 只有仍被设计使用的旧值才保留
        self.assertEqual((cfg["color_modes"]["mono"]["secondary"], cfg["color_modes"]["mono"]["secondary_r1"]),
                         ("#FFFFFF", "#000000"))  # 新值沿用母品牌映射，旧值沿用子品牌原映射
        self.assertEqual(cfg["series"]["master_release"], "r2")
        md = (ws.projects / "_series" / "pets" / "series.md").read_text(encoding="utf-8")
        self.assertIn("改版跟进：food r1 → brand r2\n- **色板**：secondary #666666→#777777（旧值仍被已有设计使用，保留为 "
                      "secondary_r1）、ink #111111→#222222、新增 accent #ABCDEF\n- **用户意见**：用户：全部跟随", md)
        self.assertNotIn("母品牌 brand 已发布", ws.logo("check").stdout)
        self.commit_ok("[food] 跟进 brand r2")  # 冻结的 A-v002 仍通过检查
        self.assertIn("无需同步", self.sync("all").stdout)

    def test_selected_none_and_name_collision(self):
        ws = self.ws
        food = self.family()
        ws.new_version("food", "A", "paw", svg=SQUARE.replace(PRIMARY, "#666666"))
        toml = food / "project.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace('ink = "#111111"', 'ink = "#111111"\nsecondary_r1 = "#010101"'),
                        encoding="utf-8")  # 旧值色名已被占用（值不同）
        self.master_update()
        self.sync("secondary", "用户：只跟 secondary")
        cfg = self.toml("food")
        self.assertEqual((cfg["palette"]["secondary"], cfg["palette"]["secondary_r1"], cfg["palette"]["secondary_r1_2"]),
                         ("#777777", "#010101", "#666666"))
        self.assertEqual((cfg["palette"]["ink"], "accent" in cfg["palette"]), ("#111111", False))
        md = (ws.projects / "_series" / "pets" / "series.md").read_text(encoding="utf-8")
        self.assertIn("；未跟随：ink、accent", md)

    def test_acknowledge_without_color_change(self):
        ws = self.ws
        self.family()
        self.release_master_again()  # 只出新发布、色板不变
        self.assertIn("母品牌 brand 已发布 r2（子品牌依据 r1），色板无变化", ws.logo("check").stdout)
        before = self.toml("food")["palette"]
        self.sync("none", "用户：已知悉，暂不改版")
        self.assertEqual(self.toml("food")["palette"], before)
        self.assertEqual(self.toml("food")["series"]["master_release"], "r2")
        self.assertIn("- **色板**：不改\n", (ws.projects / "_series" / "pets" / "series.md").read_text(encoding="utf-8"))
        self.assertIn("不是系列子品牌", ws.logo("series", "sync", "brand", "--palette", "all", "--note", "x", ok=False).stderr)

    def test_independent_sub_needs_no_sync(self):
        self.family("independent")
        self.assertIn("无需跟进", self.sync("all", ok=False).stderr)


@unittest.skipUnless(HAS_RENDER, "需要 resvg 与 magick")
class SeriesSkeletonTest(SeriesBase):
    """第二期第 3a 步：系列封面骨架——登记、套用（换 Logo、照片转图片区、参考线重画、原文提醒）与必须先问用户。"""

    def setUp(self):
        super().setUp()
        ws = self.ws
        self.food = self.released_family()
        brand = ws.projects / "brand"
        toml = brand / "project.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace("used = []", 'used = ["inter"]'), encoding="utf-8")
        ws.logo("cover", "new", "brand", "main", "--platforms", "x,youtube")
        self.main = brand / "covers" / "001-main"
        fill_doc(self.main / "cover.md")
        photo = ws.tmp / "photo.png"
        subprocess.run(["magick", "-size", "200x100", "xc:#E76F51", str(photo)], check=True)
        ws.logo("cover", "asset", "brand", "001", str(photo), "--name", "photo", "--source", "自摄", "--license", "自有版权")
        fill_doc(self.main / "assets" / "001-photo.md")
        svg = self.main / "3x1.svg"
        svg.write_text(svg.read_text(encoding="utf-8").replace('<g id="content">', (
            '<image href="assets/001-photo.png" x="800" y="60" width="600" height="380" preserveAspectRatio="none"/>'
            '<g id="content"><!-- outline: font=inter size=10 text="SALE" -->'
            '<rect x="700" y="200" width="300" height="80" fill="#FFFFFF"/>')), encoding="utf-8")
        self.commit_ok("[brand] cover 001 + [food] r1")

    def skeleton(self, *extra: str, ok: bool = True):
        return self.ws.logo("series", "skeleton", "pets", "--from", "brand", "001", "--note", "用户：以首张封面为骨架",
                            *extra, ok=ok)

    def test_register_rules(self):
        ws = self.ws
        ws.logo("new", "solo")
        self.assertIn("不是系列 pets 的成员", ws.logo("series", "skeleton", "pets", "--from", "solo", "001", "--note", "x",
                                                ok=False).stderr)
        svg = self.main / "3x1.svg"
        good = svg.read_text(encoding="utf-8")
        svg.write_text(good.replace("assets/001-photo.png", "assets/002-nope.png"), encoding="utf-8")
        self.assertIn("骨架封面未通过检查", self.skeleton(ok=False).stderr)  # 只能登记通过检查的封面
        svg.write_text(good, encoding="utf-8")
        self.assertIn("--note", ws.logo("series", "skeleton", "pets", "--from", "brand", "001", "--note", " ",
                                         ok=False).stderr)
        self.skeleton()
        self.assertEqual(tomllib.loads((ws.projects / "_series" / "pets" / "series.toml").read_text(encoding="utf-8"))
                         ["covers"]["skeleton"], "brand/001-main")
        md = (ws.projects / "_series" / "pets" / "series.md").read_text(encoding="utf-8")
        self.assertIn("封面骨架：brand/001-main\n- **画幅**：16x9、3x1\n- **用户意见**：用户：以首张封面为骨架", md)
        self.assertIn("封面骨架：brand/001-main", ws.logo("series").stdout)
        self.commit_ok("[pets] skeleton")

    def test_members_must_choose(self):
        ws = self.ws
        self.assertIn("没有登记封面骨架", ws.logo("cover", "new", "food", "a", "--platforms", "x", "--from-skeleton",
                                           ok=False).stderr)
        self.skeleton()
        for slug in ("food", "brand"):  # 母品牌也是成员
            self.assertIn("请先问用户是否套用", ws.logo("cover", "new", slug, "a", "--platforms", "x", ok=False).stderr)
        self.assertNotEqual(ws.logo("cover", "new", "food", "a", "--platforms", "x", "--from-skeleton", "--no-skeleton",
                                    ok=False).returncode, 0)
        ws.logo("cover", "new", "food", "plain", "--platforms", "x", "--no-skeleton")
        plain = self.food / "covers" / "001-plain"
        self.assertNotIn("skeleton", (plain / "cover.toml").read_text(encoding="utf-8"))
        self.assertIn("- **封面骨架**：无", (plain / "cover.md").read_text(encoding="utf-8"))

    def test_derive_from_skeleton(self):
        ws = self.ws
        self.skeleton()
        out = ws.logo("cover", "new", "food", "spring", "--platforms", "x,weibo,og", "--from-skeleton").stdout
        self.assertIn("1.91x1.svg：骨架 brand/001-main 没有这个画幅，已按常规起稿", out)
        self.assertIn("已写入 food 的 [fonts] used", out)
        c = self.food / "covers" / "001-spring"
        derived, source = (c / "3x1.svg").read_text(encoding="utf-8"), (self.main / "3x1.svg").read_text(encoding="utf-8")
        logo_tag = re.compile(r'<g id="logo"[^>]*>')
        self.assertIn('data-release="r1"', logo_tag.search(derived).group(0))  # food 自己的 r1（提交时按 food 的发布核对指纹）
        logo_part = derived[logo_tag.search(derived).start():derived.index('id="content"')]
        self.assertIn('d="M10 90 50 10 90 90Z"', logo_part)  # food 的三角形主版，而不是母品牌的正方形
        self.assertNotIn('d="M10 10h80v80H10Z"', logo_part)
        self.assertEqual(re.search(r'transform="[^"]*"', logo_tag.search(derived).group(0)).group(0),
                         re.search(r'transform="[^"]*"', logo_tag.search(source).group(0)).group(0))  # 位置不变
        self.assertNotIn("<image", derived)  # 照片授权按封面登记，不沿用
        self.assertIn("图片区", derived)
        self.assertIn('<!-- outline: font=inter size=10 text="SALE" -->', derived)  # 转曲注释逐字保留
        g0, g1 = __import__("covers").group_span(derived, "guides")
        self.assertIn(">weibo<", derived[g0:g1])
        self.assertNotIn(">youtube<", derived[g0:g1])  # 参考线按本封面平台重画
        cfg = tomllib.loads((c / "cover.toml").read_text(encoding="utf-8"))["cover"]
        self.assertEqual((cfg["skeleton"], list(cfg["skeleton_sha"])), ("brand/001-main", ["3x1.svg"]))
        self.assertIn("- **封面骨架**：brand/001-main", (c / "cover.md").read_text(encoding="utf-8"))
        self.assertIn("inter", self.toml("food")["fonts"]["used"])
        self.assertIn("001-spring/3x1.svg: 关键内容仍是骨架原文（来自 brand/001-main）", ws.logo("check").stdout)
        (c / "3x1.svg").write_text(derived.replace('fill="#FFFFFF"/>', 'fill="#000000"/>', 1), encoding="utf-8")
        self.assertNotIn("骨架原文", ws.logo("check").stdout)
        fill_doc(c / "cover.md")
        self.commit_ok("[food] cover from skeleton")  # 派生封面完整通过提交检查
        skel = self.main / "3x1.svg"
        skel.write_text(skel.read_text(encoding="utf-8") + "<!-- 调整 -->\n", encoding="utf-8")
        self.assertIn("所套用的骨架 brand/001-main/3x1.svg 已修改", ws.logo("check").stdout)

    def test_derived_cover_can_become_skeleton(self):
        ws = self.ws
        self.skeleton()
        ws.logo("cover", "new", "food", "spring", "--platforms", "x", "--from-skeleton")
        c = self.food / "covers" / "001-spring"
        fill_doc(c / "cover.md")
        ws.logo("series", "skeleton", "pets", "--from", "food", "001", "--note", "用户：改用 food 的封面作骨架")
        ws.logo("cover", "new", "brand", "again", "--platforms", "x", "--from-skeleton")
        text = (ws.projects / "brand" / "covers" / "002-again" / "3x1.svg").read_text(encoding="utf-8")
        self.assertEqual(text.count("data-skeleton="), 1)  # 只保留新指纹，设计稿仍是合法 XML
        self.assertIn("002-again/3x1.svg: 关键内容仍是骨架原文（来自 food/001-spring）", ws.logo("check").stdout)


@unittest.skipUnless(HAS_RENDER, "需要 resvg 与 magick")
class FamilyCoverTest(SeriesBase):
    """第二期第 3b 步：家族封面——系列名下同时展示多个成员的 Logo，逐个成员核对、提醒与同步。"""

    def setUp(self):
        super().setUp()
        self.released_family()
        self.fam = self.ws.projects / "_series" / "pets" / "covers" / "001-fam"

    def new_fam(self, *extra: str, ok: bool = True):
        return self.ws.logo("cover", "new", "series:pets", "fam", "--platforms", "x,youtube", *extra, ok=ok)

    def committed_fam(self) -> None:
        self.new_fam("--members", "food,brand")
        fill_doc(self.fam / "cover.md")
        self.commit_ok("[pets] family cover")
        tracked = set(self.ws.git("projects", "ls-files", "_series/pets/covers").stdout.split())
        self.assertEqual(tracked, {f"_series/pets/covers/001-fam/{n}" for n in ("cover.toml", "cover.md", "3x1.svg", "16x9.svg")})

    def test_new_rules(self):
        ws = self.ws
        self.assertIn("先问用户展示哪些品牌", self.new_fam(ok=False).stderr)
        self.assertIn("至少要两个成员", self.new_fam("--members", "food", ok=False).stderr)
        ws.logo("new", "solo")
        self.assertIn("不是系列 pets 的成员", self.new_fam("--members", "food,solo", ok=False).stderr)
        ws.logo("new", "drink", "--series", "pets", "--role", "sub")
        self.assertIn("成员 drink 还没有发布", self.new_fam("--members", "food,drink", ok=False).stderr)
        self.assertIn("不使用 --release", self.new_fam("--members", "food,brand", "--release", "r1", ok=False).stderr)
        self.assertIn("不使用 --release", self.new_fam("--members", "food,brand", "--from-skeleton", ok=False).stderr)
        self.assertIn("只用于家族封面", ws.logo("cover", "new", "food", "a", "--platforms", "x", "--members", "food,brand",
                                            ok=False).stderr)
        self.assertIn("系列不存在：nope", ws.logo("cover", "new", "series:nope", "a", "--platforms", "x", "--members",
                                             "food,brand", ok=False).stderr)
        self.assertFalse((ws.projects / "_series" / "pets" / "covers").exists())

    def test_starter_check_and_tamper(self):
        ws = self.ws
        self.committed_fam()
        svg = self.fam / "3x1.svg"
        root = ET.parse(svg).getroot()
        shown = {}
        for g in root.iter("{http://www.w3.org/2000/svg}g"):
            if (g.get("id") or "").startswith("logo-"):
                scale = float(re.search(r"scale\(([\d.]+)\)", g.get("transform")).group(1))
                shown[g.get("id")] = (g.get("data-project"), g.get("data-release"), scale * float(list(g)[0].get("height")))
        self.assertEqual({k: v[:2] for k, v in shown.items()}, {"logo-food": ("food", "r1"), "logo-brand": ("brand", "r1")})
        self.assertAlmostEqual(shown["logo-food"][2] / shown["logo-brand"][2], 1.25, places=2)  # 第一个成员为主，更大
        cfg = tomllib.loads((self.fam / "cover.toml").read_text(encoding="utf-8"))["cover"]
        self.assertEqual((cfg["members"], cfg["releases"]), (["food", "brand"], {"food": "r1", "brand": "r1"}))
        self.assertFalse([ln for ln in ws.logo("check").stdout.splitlines() if "001-fam" in ln])  # 起稿即干净
        good = svg.read_text(encoding="utf-8")
        brand_part = good[good.index('id="logo-brand"'):]
        tampered = good.replace(brand_part, brand_part.replace('d="M10 10h80v80H10Z"', 'd="M10 10h70v80H10Z"', 1))
        a0, a1 = __import__("covers").group_span(good, "logo-food")
        for text, msg in ((tampered, "Logo（brand）: Logo 与 r1/primary.svg"),
                          (good[:a0] + good[a1:], "成员 food 的 Logo 分组 <g id=\"logo-food\"> 应有 1 个，实有 0 个"),
                          (good.replace('data-project="brand"', 'data-project="food"'), "标注的项目 'food' 应为 brand")):
            with self.subTest(msg=msg):
                self.assertNotEqual(text, good)
                svg.write_text(text, encoding="utf-8")
                r = ws.commit("projects", "bad")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(msg, "\n".join(ws.blocked(r)))
                ws.git("projects", "reset", "-q", "--hard")

    def test_outdated_and_sync(self):
        ws = self.ws
        self.committed_fam()
        self.release_master_again()
        self.assertIn("家族封面使用的 Logo 不是成员的当前发布：brand 用 r1（当前 r2）", ws.logo("check").stdout)
        ws.logo("cover", "status", "series:pets", "001", "live", "--on", "x")
        self.assertIn("上线中的家族封面（x）仍在使用旧 Logo：brand 用 r1（当前 r2）", ws.logo("check").stdout)
        self.assertIn("须同时用 --member", ws.logo("cover", "sync-logo", "series:pets", "001", "--release", "r1",
                                                ok=False).stderr)
        self.assertIn("不是这张家族封面的成员", ws.logo("cover", "sync-logo", "series:pets", "001", "--member", "nope",
                                                ok=False).stderr)
        out = ws.logo("cover", "sync-logo", "series:pets", "001").stdout
        self.assertIn("3x1 brand：r1/primary → r2/primary", out)
        self.assertNotIn(" food：", out)  # food 已是当前发布，不动
        self.assertEqual(tomllib.loads((self.fam / "cover.toml").read_text(encoding="utf-8"))["cover"]["releases"],
                         {"food": "r1", "brand": "r2"})
        self.assertNotIn("家族封面", ws.logo("check").stdout)
        self.assertIn("无需同步", ws.logo("cover", "sync-logo", "series:pets", "001").stdout)
        ws.logo("cover", "sync-logo", "series:pets", "001", "--member", "brand", "--release", "r1")
        self.assertIn('id="logo-brand" data-project="brand" data-release="r1"', (self.fam / "3x1.svg").read_text(encoding="utf-8"))

    def test_assets_export_and_fonts(self):
        ws = self.ws
        self.committed_fam()
        photo = ws.tmp / "photo.png"
        subprocess.run(["magick", "-size", "200x100", "xc:#E76F51", str(photo)], check=True)
        ws.logo("cover", "asset", "series:pets", "001", str(photo), "--name", "photo", "--source", "自摄", "--license", "自有版权")
        fill_doc(self.fam / "assets" / "001-photo.md")
        self.commit_ok("[pets] family asset")
        (self.fam / "assets" / "001-photo.png").write_bytes(photo.read_bytes() + b"\0")
        r = ws.commit("projects", "replace")
        self.assertIn("已冻结的文件被修改", "\n".join(ws.blocked(r)))  # 家族封面素材同样登记后不可替换
        ws.git("projects", "reset", "-q", "--hard")
        ws.logo("cover", "export", "series:pets", "001", "x")
        dist = ws.projects / "_series" / "pets" / "dist" / "covers" / "001-fam"
        import json
        rec = json.loads((dist / "manifest.json").read_text(encoding="utf-8"))["files"]["x-1500x500.png"]
        self.assertEqual(rec["release"], {"food": "r1", "brand": "r1"})
        self.assertEqual(ws.git("projects", "status", "--porcelain").stdout.strip(), "")  # dist 不入库
        self.assertIn("预览：.cache/_series/pets/covers/001-fam/preview.png", ws.logo("cover", "preview", "series:pets", "001").stdout)
        svg = self.fam / "3x1.svg"
        good = svg.read_text(encoding="utf-8")
        svg.write_text(good.replace('<g id="content">', '<g id="content"><!-- outline: font=nofont size=1 -->'), encoding="utf-8")
        self.assertIn("转曲所用字体 nofont 未在 fonts.toml 登记", ws.logo("check", ok=False).stdout)
        svg.write_text(good.replace('<g id="content">', '<g id="content"><!-- outline: font=inter size=1 -->'), encoding="utf-8")
        self.assertNotIn("✗", ws.logo("check").stdout)  # 已登记字体即可，无须项目 [fonts] used

    def test_geometry_per_member_and_retarget(self):
        ws = self.ws
        self.committed_fam()
        svg = self.fam / "3x1.svg"
        text = svg.read_text(encoding="utf-8")
        text = re.sub(r'(id="logo-food"[^>]*transform=")[^"]*"', r'\1translate(40 320) scale(0.5)"', text, count=1)
        text = re.sub(r'(id="logo-brand"[^>]*transform="translate\([^)]*\) )scale\([^)]*\)', r"\1scale(0.03)", text, count=1)
        svg.write_text(text, encoding="utf-8")
        out = ws.logo("check").stdout
        self.assertIn("Logo（food）在 x 上会被遮挡", out)
        self.assertIn("Logo（brand） 在 x 手机显示", out)
        ws.git("projects", "checkout", "--", ".")
        out = ws.logo("cover", "retarget", "series:pets", "001", "--platforms", "x,youtube,og").stdout
        self.assertIn("1.91x1.svg：新起稿", out)
        new = (self.fam / "1.91x1.svg").read_text(encoding="utf-8")
        self.assertIn('id="logo-food"', new)
        self.assertIn('id="logo-brand"', new)
        self.assertIn("food:r1 brand:r1", ws.logo("cover", "list", "series:pets").stdout)
        self.assertNotIn("✗", ws.logo("check").stdout)


if __name__ == "__main__":
    unittest.main()
