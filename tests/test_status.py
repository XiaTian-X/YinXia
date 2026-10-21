"""./logo status：新会话的 Agent 据此知道各项目做到哪、下一步做什么（只读、不渲染、结论与 check 一致）。"""
from __future__ import annotations

import json
import unittest

from helpers import HAS_RENDER, Workspace, fill_doc
from test_series import SeriesBase


def todo(ws: Workspace, slug: str) -> list[dict]:
    data = json.loads(ws.logo("status", slug, "--json").stdout)
    return data["projects"][0]["todo"]


def texts(items: list[dict]) -> str:
    return "\n".join(f"[{t['kind']}] {t['text']}" for t in items)


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)

    def test_interview_uncommitted_and_design(self):
        ws = self.ws
        ws.logo("new", "solo")
        items = todo(ws, "solo")
        self.assertEqual(items[0]["kind"], "访谈")  # 访谈排在最前
        self.assertIn("brief.md 还有", items[0]["text"])
        self.assertIn("未提交", texts(items))
        self.assertIn("新文档还有模板占位：solo/brief.md", texts(items))
        self.assertNotIn("还没有设计方向", texts(items))  # 访谈没做完不提示开始设计
        fill_doc(ws.projects / "solo" / "brief.md")
        ws.logo("index")
        ws.commit("projects", "[solo] init")
        items = todo(ws, "solo")
        self.assertEqual([t["kind"] for t in items], ["设计"])
        self.assertIn("./logo direction solo A", items[0]["command"])
        ws.new_version("solo")
        ws.logo("state", "solo", "selected_version=A-v001")
        self.assertIn("选定版本 A-v001 尚未发布", texts(todo(ws, "solo")))
        out = ws.logo("status").stdout
        self.assertIn("请把待办讲给用户，问从哪一项开始", out)
        self.assertIn("项目仓库没有配置远程仓库", out)  # 全局：备份

    def test_broken_project_does_not_hide_others(self):
        ws = self.ws
        ws.new_project("good")
        ws.new_project("bad")
        (ws.projects / "bad" / "project.toml").write_text("[state\nbroken", encoding="utf-8")
        data = json.loads(ws.logo("status", "--json").stdout)
        by = {p["slug"]: p for p in data["projects"]}
        self.assertIn("项目配置无法读取", by["bad"]["todo"][0]["text"])
        self.assertEqual(by["good"]["stage"], "briefing")  # 其他项目照常显示

    def test_empty_repo(self):
        self.assertIn("还没有项目", self.ws.logo("status").stdout)


class SeriesStatusTest(SeriesBase):
    def test_series_items(self):
        ws = self.ws
        ws.logo("series", "new", "group", "--architecture", "independent")
        ws.logo("new", "one", "--series", "group", "--role", "sub")
        fill_doc(ws.projects / "one" / "brief.md")
        self.assertIn("系列规则 _series/group/series.md 还有", texts(todo(ws, "one")))
        fill_doc(ws.projects / "_series" / "group" / "series.md")
        self.assertNotIn("系列规则", texts(todo(ws, "one")))
        self.family()
        ws.logo("state", "food", "selected_version=A-v001")
        items = texts(todo(ws, "food"))
        self.assertIn("选定版本 A-v001 尚未通过系列评审", items)
        self.release_master_again()
        items = texts(todo(ws, "food"))
        self.assertIn("母品牌 brand 已发布 r2（本项目依据 r1）", items)
        ws.logo("series", "review", "pets", "--result", "pass", "--note", "通过", "--with", "food=A-v001")
        self.assertNotIn("系列评审", texts(todo(ws, "food")))


@unittest.skipUnless(HAS_RENDER, "需要 resvg 与 magick")
class DeliveryStatusTest(unittest.TestCase):
    def setUp(self):
        from test_workflow import prepare_release
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)
        self.p = self.ws.new_project()
        self.ws.new_version()
        assert self.ws.commit("projects", "[demo] A-v001").returncode == 0
        prepare_release(self.ws)

    def test_covers_inbox_and_delivery(self):
        ws = self.ws
        self.assertEqual(todo(ws, "demo"), [])
        ws.logo("cover", "new", "demo", "spring", "--platforms", "x")
        ws.logo("cover", "status", "demo", "001", "live", "--on", "x")
        ws.logo("release", "demo", "--from", "r1")
        items = todo(ws, "demo")
        self.assertIn("上线中的封面（x）仍在使用旧 Logo r1", texts(items))
        self.assertIn("当前发布 r2 的 guide.md 未填完", texts(items))
        inbox = ws.root / "inbox"
        (inbox / "demo").mkdir(parents=True)
        (inbox / "demo" / "a.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>', encoding="utf-8")
        (inbox / "loose.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>', encoding="utf-8")
        self.assertIn("inbox/demo/a.svg 待导入", texts(todo(ws, "demo")))
        data = json.loads(ws.logo("status", "--json").stdout)
        self.assertIn("inbox/loose.svg 去向待定", "\n".join(t["text"] for t in data["global"]))
        kinds = [t["kind"] for t in todo(ws, "demo")]
        self.assertEqual(kinds, sorted(kinds, key=["访谈", "未提交", "设计", "系列", "交付", "封面", "收件箱"].index))


if __name__ == "__main__":
    unittest.main()
