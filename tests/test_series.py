"""需求访谈与品牌系列：按访谈结果新建独立/系列项目、系列规则、继承与检查。"""
from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

from helpers import PRIMARY, TRIANGLE, Workspace, fill_doc

SQUARE = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><path fill="{PRIMARY}" d="M10 10h80v80H10Z"/></svg>\n'


class SeriesTest(unittest.TestCase):
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

    def commit_ok(self, msg: str = "x") -> None:
        self.ws.logo("index")
        r = self.ws.commit("projects", msg)
        self.assertEqual(r.returncode, 0, self.ws.blocked(r))

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


if __name__ == "__main__":
    unittest.main()
