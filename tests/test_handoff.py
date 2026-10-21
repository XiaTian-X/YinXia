"""与其他 Agent 交接：说明书、通用 skill、交接文本（截断检测）、收进收件箱、只读自查与修改意见。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from helpers import HAS_RENDER, PRIMARY, Workspace, fill_doc
from test_series import SeriesBase


def block(n: int, total: int, name: str, svg: str, meta: str = 'source = "ChatBot X"\n', end: bool = True) -> str:
    return (f"=== LOGODESIGN 交接 v1 · 文件 {n}/{total} ===\n[说明]\n{meta}[文件 {name}]\n```xml\n{svg}\n```\n"
            + (f"=== 结束 {n}/{total} ===\n" if end else ""))


GOOD = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><path fill="{PRIMARY}" d="M10 10h80v80H10Z"/></svg>'
BAD = GOOD.replace(PRIMARY, "#FF6600").replace("</svg>", '<text x="5" y="5">DEMO</text></svg>')


@unittest.skipUnless(HAS_RENDER, "需要 resvg 与 magick")
class HandoffTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)
        self.p = self.ws.new_project()
        self.reply = self.ws.root / ".cache" / "reply.txt"
        self.reply.parent.mkdir(exist_ok=True)

    def say(self, text: str) -> str:
        self.reply.write_text(text, encoding="utf-8")
        return ".cache/reply.txt"

    def test_brief(self):
        brief = self.p / "brief.md"
        brief.write_text(brief.read_text(encoding="utf-8").replace("- **用户确认的要点**： 测试内容", "- **用户确认的要点**： 私密备注"),
                         encoding="utf-8")
        out = self.ws.logo("handoff", "brief", "demo", "--plain").stdout
        for part in ("## 色板（只能用这些颜色）", f"- primary：{PRIMARY}", "## SVG 硬性要求", "必须有 viewBox",
                     "=== LOGODESIGN 交接 v1 · 文件 1/N ===", "=== 结束 1/N ===", "不要写使用条款与授权判断",
                     "发给外部工具前请确认用户同意", "复用口令：按 LOGODesign 交接格式 v1 输出"):
            self.assertIn(part, out)
        self.assertNotIn("私密备注", out)  # 需求确认一节不外发
        self.assertTrue((self.ws.root / ".cache" / "handoff" / "demo-logo-brief-plain.md").exists())
        files = self.ws.logo("handoff", "brief", "demo").stdout
        self.assertIn("inbox/demo/<英文短名>.svg", files)  # 能写文件的 Agent：直接放进收件箱并附来源说明
        self.assertIn("./logo handoff validate", files)

    def test_parse_receive_and_truncation(self):
        ws = self.ws
        text = "好的，设计如下：\n" + block(1, 2, "badge.svg", GOOD,
                                         'source = "ChatBot X"\nterms = "可商用"\nlayout = "primary"\nproject = "acme"\n') \
            + block(2, 2, "icon.svg", GOOD, end=False)
        r = ws.logo("handoff", "receive", self.say(text), "--project", "demo", ok=False)
        self.assertIn("第 2/2 段没有结束标记（可能被截断）", r.stderr)
        self.assertFalse((ws.root / "inbox").exists())  # 不完整就什么都不写
        r = ws.logo("handoff", "receive", self.say(block(1, 3, "a.svg", GOOD) + block(3, 3, "c.svg", GOOD)), "--project", "demo", ok=False)
        self.assertIn("第 2/3 段没有收到", r.stderr)
        r = ws.logo("handoff", "receive", self.say(block(1, 1, "A b.svg", GOOD)), "--project", "demo", ok=False)
        self.assertIn("缺少“[文件 <英文短名>.svg]”行", r.stderr)
        r = ws.logo("handoff", "receive", self.say(block(1, 1, "a.svg", GOOD, "source = \n")), "--project", "demo", ok=False)
        self.assertIn("不是合法 TOML", r.stderr)
        r = ws.logo("handoff", "receive", self.say(block(1, 2, "a.svg", GOOD) + block(2, 2, "a.svg", GOOD)), "--project", "demo", ok=False)
        self.assertIn("文件名 a.svg 重复", r.stderr)
        ws.logo("handoff", "receive", self.say(text + "</svg>\n=== 结束 2/2 ===\n"), "--project", "demo")
        inbox = ws.root / "inbox" / "demo"
        self.assertEqual((inbox / "badge.svg").read_text(encoding="utf-8"), GOOD + "\n")  # SVG 原样保存（去掉代码块标记）
        side = (inbox / "badge.svg.import.toml").read_text(encoding="utf-8")
        self.assertIn('terms = "可商用"', side)
        self.assertIn('project = "demo"', side)  # 来源方写的 acme 不能改变去向
        self.assertNotIn("acme", side)
        self.assertIn("已有同名文件", ws.logo("handoff", "receive", self.say(block(1, 1, "badge.svg", GOOD)), "--project", "demo",
                                         ok=False).stderr)
        # 收进来之后照常预演导入：来源方写的条款只作参考
        ws.logo("direction", "demo", "A", "mark")
        out = ws.logo("import", "demo/badge.svg", "--direction", "A", "--analyze").stdout
        self.assertIn("来源方声明（仅供参考，须向用户确认）：使用条款 可商用", out)
        self.assertIn("--terms", ws.logo("import", "demo/badge.svg", "--direction", "A", "--kind", "final", ok=False).stderr)

    def test_validate_and_feedback(self):
        ws = self.ws
        good = ws.write("good.svg", GOOD)
        r = ws.logo("handoff", "validate", str(good), "--project", "demo")
        self.assertIn("可以交付", r.stdout)
        path = self.say(block(1, 1, "badge.svg", BAD))
        r = ws.logo("handoff", "validate", path, "--project", "demo", "--json", ok=False)
        self.assertNotEqual(r.returncode, 0)
        res = json.loads(r.stdout)
        self.assertFalse(res["ready"])
        self.assertEqual(res["files"][0]["conflicts"], ["#FF6600"])
        self.assertTrue(any("文字必须转为路径" in h for h in res["files"][0]["hard"]))
        self.assertFalse((ws.root / ".cache" / "import" / "previewed.json").exists())  # 只读：不留预演记录
        self.assertFalse((ws.root / "inbox").exists())
        out = ws.logo("handoff", "feedback", path, "--project", "demo").stdout
        self.assertIn("badge.svg：删除 <text> / <tspan>", out)
        self.assertNotIn("#FF6600", out)  # 色板外颜色让不让对方改，由用户决定
        out = ws.logo("handoff", "feedback", path, "--project", "demo", "--color", "#FF6600=primary").stdout
        self.assertIn(f"badge.svg：把颜色 #FF6600 改为 {PRIMARY}", out)
        self.assertIn("输出格式：按 LOGODesign 交接格式 v1 输出", out)
        self.assertIn("没有需要对方修改的硬性问题", ws.logo("handoff", "feedback", str(good), "--project", "demo").stdout)
        two = self.say(block(1, 2, "badge.svg", BAD) + block(2, 2, "icon.svg", GOOD))
        out = ws.logo("handoff", "feedback", two, "--project", "demo", "--color", "#FF6600=primary").stdout
        self.assertNotIn("icon.svg：", out)  # 只对用到这个颜色的文件提出
        self.assertIn("不在任何文件的色板外颜色中：#123456",
                      ws.logo("handoff", "feedback", two, "--project", "demo", "--color", "#123456=primary", ok=False).stderr)
        r = ws.logo("handoff", "validate", self.say(block(1, 1, "x.svg", '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "b">]>' + GOOD)),
                    ok=False)
        self.assertIn("DOCTYPE / ENTITY", r.stdout)  # 不指定项目也能自查硬性问题

    def test_skill(self):
        ws = self.ws
        self.assertIn("仓库之外", ws.logo("handoff", "skill", str(ws.root / "tools"), ok=False).stderr)
        ws.logo("handoff", "skill", str(ws.tmp / "skills"))
        text = (ws.tmp / "skills" / "logodesign-handoff" / "SKILL.md").read_text(encoding="utf-8")
        for part in ("name: logodesign-handoff", "必须有 viewBox", "=== 结束 n/N ===", "不要写使用条款"):
            self.assertIn(part, text)
        self.assertNotIn(PRIMARY, text)  # 通用 skill 不含项目信息


@unittest.skipUnless(HAS_RENDER, "需要 resvg 与 magick")
class SeriesHandoffTest(SeriesBase):
    def test_brief_includes_series_rules(self):
        self.family()
        out = self.ws.logo("handoff", "brief", "food").stdout
        self.assertIn("## 品牌系列：Pets（背书型，本项目是子品牌）", out)
        self.assertIn("共享的视觉基因 · 构图与网格", out)


if __name__ == "__main__":
    unittest.main()
