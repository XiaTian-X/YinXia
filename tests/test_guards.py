"""防护规则测试：不可变、SVG 规范、文档完整、结构白名单、密钥扫描、钩子与仓库拆分。

全部经由真实的 git commit 触发钩子，断言拦截与否及原因。
"""
from __future__ import annotations

import unittest

from helpers import PRIMARY, TRIANGLE, Workspace, fill_doc


class GuardBase(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)
        self.p = self.ws.new_project()
        self.v1 = self.ws.new_version()
        r = self.ws.commit("projects", "[demo] A-v001")
        self.assertEqual(r.returncode, 0, self.ws.blocked(r))

    def assertBlocked(self, r, *fragments: str):
        self.assertNotEqual(r.returncode, 0, "应被拦截却提交成功")
        text = "\n".join(self.ws.blocked(r))
        for frag in fragments:
            self.assertIn(frag, text)

    def assertCommitted(self, r):
        self.assertEqual(r.returncode, 0, "\n".join(self.ws.blocked(r)))


class ImmutabilityTest(GuardBase):
    def test_frozen_svg(self):
        (self.v1 / "logo.svg").write_text(TRIANGLE.format(c=PRIMARY).replace("90 90", "80 90"), encoding="utf-8")
        self.assertBlocked(self.ws.commit("projects", "x"), "已冻结的文件被修改")

    def test_frozen_delete(self):
        (self.v1 / "logo.svg").unlink()
        self.assertBlocked(self.ws.commit("projects", "x"), "已冻结的文件被删除")

    def test_append_only(self):
        notes = self.v1 / "notes.md"
        original = notes.read_text(encoding="utf-8")
        notes.write_text(original + "\n- 2026-10-13 用户：很好\n", encoding="utf-8")
        self.assertCommitted(self.ws.commit("projects", "[demo] review"))
        notes.write_text("# 改写\n" + notes.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertBlocked(self.ws.commit("projects", "x"), "只允许在末尾追加")

    def test_concept_image_frozen(self):
        img = self.ws.tmp / "c.png"
        img.write_bytes(b"\x89PNG")
        self.ws.logo("concept", "demo", str(img), "--name", "c", "--prompt", "p")
        fill_doc(self.p / "concepts" / "001-c.md")
        self.ws.logo("index")
        self.assertCommitted(self.ws.commit("projects", "[demo] concept"))
        (self.p / "concepts" / "001-c.png").write_bytes(b"\x89PNG changed")
        self.assertBlocked(self.ws.commit("projects", "x"), "已冻结")


class SvgAndStateTest(GuardBase):
    def test_staged_content_is_checked(self):
        v2 = self.ws.new_version()
        (v2 / "logo.svg").write_text(TRIANGLE.format(c="#FF0000"), encoding="utf-8")
        self.ws.git("projects", "add", "-A")
        (v2 / "logo.svg").write_text(TRIANGLE.format(c=PRIMARY), encoding="utf-8")  # 工作区改好，暂存区仍是坏的
        self.assertBlocked(self.ws.commit("projects", "x", add=False), "#FF0000")

    def test_event_attr_and_external_css(self):
        v2 = self.ws.new_version()
        (v2 / "logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 9" onload="x()">'
                                     "<style>@import url(https://x.example/a.css);</style></svg>", encoding="utf-8")
        self.assertBlocked(self.ws.commit("projects", "x"), "禁止事件属性", "@import")

    def test_state_contradiction(self):
        self.ws.new_version(letter="B", name="wave")
        self.assertCommitted(self.ws.commit("projects", "[demo] B-v001"))
        toml = self.p / "project.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace('selected_direction = ""', 'selected_direction = "B"')
                        .replace('selected_version = ""', 'selected_version = "A-v001"'), encoding="utf-8")
        self.ws.logo("index")
        self.assertBlocked(self.ws.commit("projects", "x"), "不属于")


class DocsTest(GuardBase):
    def test_unfilled_new_docs_blocked(self):
        self.ws.logo("direction", "demo", "C", "circle")
        self.ws.logo("version", "demo", "C")
        (next((self.p / "directions").glob("C-*")) / "v001" / "logo.svg").write_text(TRIANGLE.format(c=PRIMARY),
                                                                                       encoding="utf-8")
        self.ws.logo("index")
        self.assertBlocked(self.ws.commit("projects", "x"), "direction.md", "notes.md", "模板占位")

    def test_release_requires_decision(self):
        self.ws.logo("release", "demo", "--from", "A-v001")
        rdir = self.p / "releases" / "r1"
        fill_doc(rdir / "release.md")
        fill_doc(rdir / "guide.md")
        self.ws.logo("index")
        self.assertBlocked(self.ws.commit("projects", "x"), "须引用决策记录")
        with (rdir / "release.md").open("a", encoding="utf-8") as f:
            f.write("\n依据：decisions/007-missing.md\n")
        self.assertBlocked(self.ws.commit("projects", "x"), "决策记录不存在")

    def test_brief_only_warns(self):
        brief = self.p / "brief.md"
        brief.write_text(brief.read_text(encoding="utf-8") + "\n- **行业/领域**：\n", encoding="utf-8")  # 模板原样的空字段
        self.assertIn("brief.md: 还有 1 处模板占位", self.ws.logo("check").stdout)
        self.assertCommitted(self.ws.commit("projects", "[demo] brief"))  # brief 可随时修改，只提醒不拦截


class LegacyDataTest(GuardBase):
    """规则出台前（或绕过钩子）提交的数据：钩子只看新增文档，这些要由 check 提醒。"""

    def bypass(self, msg: str) -> None:
        self.ws.git("projects", "add", "-A")
        self.ws.git("projects", "commit", "-q", "--no-verify", "-m", msg)

    def release(self, src: str) -> str:
        """发布并填完文档、引用决策记录（不提交），返回发布号。"""
        out = self.ws.logo("release", "demo", "--from", src).stdout
        rid = out.split("已创建发布：", 1)[1].split()[0]
        rdir = self.p / "releases" / rid
        if not list((self.p / "decisions").glob("*.md")):
            self.ws.logo("decision", "demo", "finalize-a-v001")
            fill_doc(next((self.p / "decisions").glob("*.md")))
        fill_doc(rdir / "release.md")
        fill_doc(rdir / "guide.md")
        with (rdir / "release.md").open("a", encoding="utf-8") as f:
            f.write("\n依据：decisions/001-finalize-a-v001.md\n")
        self.ws.logo("index")
        return rid

    def test_committed_release_readiness(self):
        ws = self.ws
        ws.logo("release", "demo", "--from", "A-v001")
        ws.logo("index")
        self.bypass("[demo] legacy r1")  # 模板未填、没有决策记录
        out = ws.logo("check").stdout
        self.assertIn("发布 r1 无法导出", out)
        self.assertIn("发布 r1 没有引用决策记录", out)
        # 决策依据可以在末尾追加补上；guide.md 补不上，只能新建发布
        ws.logo("decision", "demo", "finalize-a-v001")
        fill_doc(next((self.p / "decisions").glob("*.md")))
        with (self.p / "releases" / "r1" / "release.md").open("a", encoding="utf-8") as f:
            f.write("\n## 决策依据（补记）\ndecisions/001-finalize-a-v001.md\n")
        self.assertCommitted(ws.commit("projects", "[demo] r1 补记决策依据"))
        out = ws.logo("check").stdout
        self.assertNotIn("没有引用决策记录", out)
        self.assertIn("发布 r1 无法导出", out)
        self.release("r1")
        self.assertCommitted(ws.commit("projects", "[demo] r2"))
        self.assertNotIn("无法导出", ws.logo("check").stdout)  # 只看当前发布

    def test_unknown_layouts(self):
        ws = self.ws
        self.release("A-v001")
        self.assertCommitted(ws.commit("projects", "[demo] r1"))
        self.release("r1")
        lay = self.p / "releases" / "r2" / "layouts"
        for name in ("cover-og.svg", "primay.svg"):
            (lay / name).write_text(TRIANGLE.format(c=PRIMARY), encoding="utf-8")
        out = ws.logo("check").stdout
        self.assertIn("cover-og.svg, primay.svg 不是系统认识的版式", out)
        self.assertIn("提交时会被拦截", out)
        self.assertBlocked(ws.commit("projects", "[demo] r2"), "cover-og.svg, primay.svg 不是系统认识的版式")
        self.bypass("[demo] legacy r2")
        self.assertIn("已冻结，./logo release demo --from r2 不会继承", ws.logo("check").stdout)
        out = ws.logo("release", "demo", "--from", "r2").stdout
        self.assertIn("未继承 r2 中不属于 Logo 版式的文件：cover-og.svg, primay.svg", out)
        self.assertEqual(sorted(f.name for f in (self.p / "releases" / "r3" / "layouts").iterdir()), ["primary.svg"])

    def test_bypassed_commits_found_in_history(self):
        ws = self.ws
        notes = self.v1 / "notes.md"
        notes.write_text(notes.read_text(encoding="utf-8") + "\n- 追加的评审意见\n", encoding="utf-8")
        self.assertCommitted(ws.commit("projects", "[demo] 追加评审"))
        self.assertNotIn("违反不可变规则", ws.logo("check").stdout)  # 正常追加不算
        (self.v1 / "logo.svg").write_text(TRIANGLE.format(c=PRIMARY).replace("M10 90", "M20 90"), encoding="utf-8")
        notes.write_text("改写开头\n" + notes.read_text(encoding="utf-8"), encoding="utf-8")
        self.bypass("[demo] 绕过钩子")
        out = ws.logo("check").stdout
        self.assertIn("违反不可变规则", out)
        self.assertIn("修改了冻结文件 demo/directions/A-mountain/v001/logo.svg", out)
        self.assertIn("改写了只追加文件 demo/directions/A-mountain/v001/notes.md", out)
        self.assertIn("违反不可变规则", ws.logo("check").stdout)  # 走缓存时结果相同


class BackupTest(GuardBase):
    def test_remote_upstream_and_unpushed(self):
        ws = self.ws
        out = ws.logo("check").stdout
        self.assertIn("系统仓库没有配置远程仓库", out)
        self.assertIn("项目仓库没有配置远程仓库", out)
        bare = ws.tmp / "projects-remote.git"
        ws.git("projects", "init", "-q", "--bare", str(bare))
        ws.git("projects", "remote", "add", "origin", str(bare))
        self.assertIn("项目仓库当前分支没有设置上游", ws.logo("check").stdout)
        ws.git("projects", "push", "-q", "-u", "origin", "main")
        out = ws.logo("check").stdout
        self.assertNotIn("项目仓库没有配置远程仓库", out)
        self.assertNotIn("项目仓库当前分支", out)
        self.assertNotIn("尚未推送", out)
        ws.new_version()
        self.assertCommitted(ws.commit("projects", "[demo] A-v002"))
        self.assertIn("项目仓库有 1 个提交尚未推送到 origin/main", ws.logo("check").stdout)


class FontUsageTest(GuardBase):
    OUTLINED = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">\n'
                '<!-- outline: font={f} size=100 tracking=0 shaper=harfbuzz-14.6.0 features=kern=1 lang=auto text="A" bbox=0,0,1,1 advance=1 -->\n'
                f'<path fill="{PRIMARY}" d="M10 90 50 10 90 90Z"/></svg>\n')

    def test_undeclared_font_blocked(self):
        v2 = self.ws.new_version(svg=self.OUTLINED.format(f="inter"))
        self.assertBlocked(self.ws.commit("projects", "x"), "SVG 中用字体 inter 转曲", "未声明")
        self.ws.py("logo.Project('demo').add_font('inter')")
        self.assertCommitted(self.ws.commit("projects", "[demo] A-v002"))
        self.assertIn("使用：demo", self.ws.logo("fonts", "list", "inter").stdout)

    def test_unknown_font_blocked(self):
        self.ws.new_version(svg=self.OUTLINED.format(f="comic-sans"))
        self.assertBlocked(self.ws.commit("projects", "x"), "comic-sans 未在 fonts.toml 登记")

    def test_declared_but_unused_warns(self):
        self.ws.py("logo.Project('demo').add_font('inter')")
        out = self.ws.logo("check").stdout
        self.assertIn("声明了 inter，但没有 SVG 用它转曲", out)
        self.assertNotIn("used_by", out)  # 不再有跨仓库的 used_by 反查

    def test_add_font_edits_toml(self):
        toml = self.p / "project.toml"
        self.assertEqual(self.ws.py("print(logo.Project('demo').add_font('inter'))").strip(), "True")
        self.assertEqual(self.ws.py("print(logo.Project('demo').add_font('inter'))").strip(), "False")
        self.ws.py("logo.Project('demo').add_font('source-han-sans-sc-bold')")
        self.assertIn('used = ["inter", "source-han-sans-sc-bold"]', toml.read_text(encoding="utf-8"))
        text = toml.read_text(encoding="utf-8")
        toml.write_text(text.replace('used = ["inter", "source-han-sans-sc-bold"]',
                                     'used = [\n  "inter",\n  "source-han-sans-sc-bold",\n]'), encoding="utf-8")  # 多行数组
        self.ws.py("logo.Project('demo').add_font('x-font')")
        self.assertEqual(self.ws.py("print(logo.Project('demo').cfg['fonts']['used'])").strip(),
                         "['inter', 'source-han-sans-sc-bold', 'x-font']")
        text = toml.read_text(encoding="utf-8")
        toml.write_text(text[:text.index("[fonts]")] + text[text.index("[export]"):], encoding="utf-8")  # 无 [fonts] 表
        self.ws.py("logo.Project('demo').add_font('inter')")
        self.assertEqual(self.ws.py("print(logo.Project('demo').cfg['fonts']['used'])").strip(), "['inter']")


class StructureAndSecretsTest(GuardBase):
    PROJECT_JUNK = ["notes.txt", ".env", "demo/trace.py", "demo/.final-build/a.png",
                    "demo/directions/A-mountain/v001/debug.png", "demo/directions/A-mountain/v001/trace.py",
                    "demo/decisions/draft.txt", "demo/concepts/ref.gif", "demo/final/x.tmp", "demo/id_rsa"]
    SYSTEM_JUNK = ["notes.txt", ".env", "trace.py", "tools/debug.log", "tools/run.sh", "config/.env.local",
                   "config/aws-credentials.toml", "tools/id_ed25519", ".claude/settings.local.json",
                   "assets/fonts/x/Foo.otf"]

    def test_projects_gitignore_hides_junk(self):
        for rel in self.PROJECT_JUNK:
            self.ws.write(f"projects/{rel}", "x")
        self.assertEqual(self.ws.git("projects", "add", "-A", "--dry-run").stdout.strip(), "")

    def test_final_blocked_and_dist_hidden(self):
        self.ws.write("projects/demo/dist/r1/ios/AppIcon-1024.png", "x")
        self.assertEqual(self.ws.git("projects", "add", "-A", "--dry-run").stdout.strip(), "")
        self.ws.write("projects/demo/final/manifest.json", "{}")
        self.ws.git("projects", "add", "-f", "demo/final/manifest.json")
        self.assertBlocked(self.ws.commit("projects", "x", add=False), "不在规定的目录结构中")

    def test_projects_forced_junk_blocked(self):
        self.ws.write("projects/demo/trace.py", "x")
        self.ws.write("projects/demo/id_rsa", "x")
        self.ws.git("projects", "add", "-f", "demo/trace.py", "demo/id_rsa")
        self.assertBlocked(self.ws.commit("projects", "x", add=False), "不在规定的目录结构中", "疑似密钥")

    def test_system_gitignore_hides_junk(self):
        for rel in self.SYSTEM_JUNK:
            self.ws.write(rel, "x")
        self.assertEqual(self.ws.git("system", "add", "-A", "--dry-run").stdout.strip(), "")

    def test_system_forced_junk_blocked(self):
        for rel, why in (("notes.txt", "不在规定的目录结构中"), ("tools/run.sh", "不在规定的目录结构中"),
                         ("config/aws-credentials.toml", "疑似密钥"), ("assets/fonts/x/Foo.otf", "字体文件不应提交")):
            with self.subTest(rel=rel):
                self.ws.write(rel, "x")
                self.ws.git("system", "add", "-f", rel)
                self.assertBlocked(self.ws.commit("system", "x", add=False), why)
                self.ws.git("system", "rm", "-q", "--cached", rel)

    def test_secret_content_blocked_without_echo(self):
        token = "ghp_" + "Q" * 36
        self.ws.write("tools/helper.py", f"TOKEN = '{token}'\n")
        r = self.ws.commit("system", "x")
        self.assertBlocked(r, "第 1 行疑似包含密钥")
        self.assertNotIn(token[:8], r.stdout + r.stderr)

    def test_legit_files_pass(self):
        self.ws.write("tools/helper.py", "def f():\n    return 1\n")
        self.ws.write(".claude/settings.json", "{}\n")
        self.assertCommitted(self.ws.commit("system", "add helper"))
        self.ws.logo("decision", "demo", "secret-garden", "--title", "秘密花园")  # 项目里的关键词不误伤
        fill_doc(self.p / "decisions" / "001-secret-garden.md")
        self.assertCommitted(self.ws.commit("projects", "[demo] decision"))


class RepoSplitTest(GuardBase):
    def test_system_commit_not_blocked_by_project_wip(self):
        v2 = self.ws.new_version()
        (v2 / "logo.svg").write_text(TRIANGLE.format(c="#FF0000"), encoding="utf-8")
        self.ws.write("README.md", self.ws.read("README.md") + "\n<!-- note -->\n")
        r = self.ws.commit("system", "docs")
        self.assertCommitted(r)
        self.assertIn("[项目仓库]", r.stdout + r.stderr)

    def test_hook_and_gitignore_drift_warnings(self):
        self.ws.git("projects", "config", "--unset", "core.hooksPath")
        self.ws.write("projects/.gitignore", ".DS_Store\n")
        out = self.ws.logo("check").stdout
        self.assertIn("projects 仓库的 pre-commit 钩子未启用", out)
        self.assertIn("projects/.gitignore 与系统模板不一致", out)
        self.ws.logo("setup")
        out = self.ws.logo("check").stdout
        self.assertNotIn("钩子未启用", out)
        self.assertNotIn("与系统模板不一致", out)


class SystemAloneTest(unittest.TestCase):
    def test_without_projects_repo(self):
        ws = Workspace(setup=False)
        self.addCleanup(ws.cleanup)
        r = ws.logo("check")
        self.assertIn("未找到项目仓库", r.stdout)
        self.assertIn("检查通过", r.stdout)
        self.assertIn("请先运行 ./logo setup", ws.logo("new", "x", ok=False).stderr)
        ws.logo("setup")
        ws.logo("new", "x")
        self.assertTrue((ws.projects / ".git").exists())


if __name__ == "__main__":
    unittest.main()
