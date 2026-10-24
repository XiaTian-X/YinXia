"""多个独立 Git 仓库的选择、隔离、钩子与完整交付流程。"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from helpers import HAS_RENDER, TRIANGLE, Workspace, fill_doc


class RepositoriesTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)

    def cli(self, repo: str, *args: str, ok: bool = True):
        return self.ws.logo("--repo", repo, *args, ok=ok)

    def git(self, repo: Path, *args: str):
        return subprocess.run(["git", "-C", str(repo), *args], env=self.ws.env, capture_output=True, text=True)

    def commit(self, path: Path, message: str, ok: bool = True):
        self.assertEqual(self.git(path, "add", "-A").returncode, 0)
        result = self.git(path, "commit", "-q", "-m", message)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def repo(self, name: str, kind: str = "workspace") -> Path:
        self.ws.logo("repo", "new", name, "--kind", kind)
        path = self.ws.root / "repositories" / name
        self.commit(path, "initialize design repository")
        return path

    def brand(self, repo: str, slug: str = "brand") -> Path:
        self.cli(repo, "new", slug, "--name", repo + " brand")
        path = self.ws.root / "repositories" / repo / slug
        fill_doc(path / "brief.md")
        self.cli(repo, "index")
        self.commit(path.parent, "create brand")
        return path

    def version(self, repo: str, project: Path) -> Path:
        self.cli(repo, "direction", project.name, "A", "mark")
        d = project / "directions/A-mark"
        fill_doc(d / "direction.md")
        self.cli(repo, "version", project.name, "A")
        v = d / "v001"
        (v / "logo.svg").write_text(TRIANGLE.format(c="#000000"), encoding="utf-8")
        fill_doc(v / "notes.md")
        self.cli(repo, "index")
        self.commit(project.parent, "first version")
        return v

    def release(self, repo: str, project: Path):
        self.cli(repo, "decision", project.name, "finalize")
        fill_doc(project / "decisions/001-finalize.md")
        self.cli(repo, "release", project.name, "--from", "A-v001")
        rdir = project / "releases/r1"
        fill_doc(rdir / "release.md")
        with (rdir / "release.md").open("a", encoding="utf-8") as f:
            f.write("\n依据：decisions/001-finalize.md\n")
        fill_doc(rdir / "guide.md")
        self.cli(repo, "index")
        self.commit(project.parent, "release r1")

    def test_registry_modes_and_system_git_isolation(self):
        solo = self.repo("solo", "single")
        self.repo("team")
        self.brand("solo")
        self.brand("team", "one")
        self.brand("team", "two")
        listing = json.loads(self.ws.logo("repo", "list", "--json").stdout)
        by = {r["id"]: r for r in listing["repositories"]}
        self.assertEqual(listing["active"], "default")
        self.assertEqual((by["solo"]["kind"], by["solo"]["projects"]), ("single", 1))
        self.assertEqual((by["team"]["kind"], by["team"]["projects"]), ("workspace", 2))
        self.assertTrue((solo / ".git").is_dir())
        self.assertEqual(self.ws.git("system", "status", "--porcelain").stdout, "")
        self.assertEqual(self.ws.git("system", "add", "-A", "--dry-run").stdout, "")
        self.assertNotEqual(self.ws.logo("repo", "new", "solo", ok=False).returncode, 0)
        self.assertNotEqual(self.ws.logo("repo", "new", "../escape", ok=False).returncode, 0)

    def test_single_rejects_second_brand_and_series(self):
        path = self.repo("solo", "single")
        self.brand("solo")
        self.assertIn("最多容纳一个", self.cli("solo", "new", "other", ok=False).stderr)
        self.assertFalse((path / "other").exists())
        self.assertIn("single", self.cli("solo", "series", "new", "group", "--architecture", "independent", ok=False).stderr)
        self.assertFalse((path / "_series").exists())
        # 暂存区中的第二个项目即使在工作区被移走，也不能绕过 single 限制。
        shutil.copytree(path / "brand", path / "other")
        self.git(path, "add", "-A")
        shutil.rmtree(path / "other")
        result = self.cli("solo", "check", "--staged", "--scope", "projects", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("暂存区包含多个品牌", result.stdout)

    def test_inbox_is_reserved_and_cannot_be_force_committed(self):
        path = self.repo("alpha")
        self.assertIn("收件箱保留", self.cli("alpha", "new", "inbox", ok=False).stderr)
        self.assertFalse((path / "inbox").exists())
        incoming = path / "inbox/brief.md"
        incoming.parent.mkdir()
        incoming.write_text("外部待确认资料。\n", encoding="utf-8")
        self.assertEqual(self.git(path, "status", "--porcelain").stdout, "")
        self.assertEqual(self.git(path, "add", "-f", "inbox/brief.md").returncode, 0)
        result = self.git(path, "commit", "-q", "-m", "reject inbox")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("inbox/brief.md", result.stdout + result.stderr)

    def test_active_selection_and_explicit_override(self):
        self.repo("alpha")
        self.repo("beta")
        self.ws.logo("repo", "use", "alpha")
        a = self.brand("alpha")
        b = self.brand("beta")
        current = json.loads(self.ws.logo("status", "--json").stdout)
        other = json.loads(self.cli("beta", "status", "--json").stdout)
        self.assertEqual(current["repository"], "alpha")
        self.assertEqual(other["repository"], "beta")
        self.assertEqual(current["projects"][0]["name"], "alpha brand")
        self.assertEqual(other["projects"][0]["name"], "beta brand")
        self.assertIn("./logo --repo beta direction", other["projects"][0]["todo"][0]["command"])
        self.assertTrue((a / "project.toml").is_file() and (b / "project.toml").is_file())
        self.assertNotEqual(self.cli("missing", "new", "brand", ok=False).returncode, 0)
        self.cli("default", "new", "legacy")
        fill_doc(self.ws.projects / "legacy/brief.md")
        default = json.loads(self.cli("default", "status", "--json").stdout)
        hints = "\n".join(t["command"] for t in default["projects"][0]["todo"])
        self.assertIn("./logo --repo default direction", hints)

    def test_hook_checks_committing_repository_even_after_forget(self):
        pa = self.repo("alpha")
        self.repo("beta")
        a = self.brand("alpha")
        self.brand("beta")
        v = self.version("alpha", a)
        self.ws.logo("repo", "use", "beta")
        original = (v / "logo.svg").read_text(encoding="utf-8")
        (v / "logo.svg").write_text(original.replace("90 90", "80 90"), encoding="utf-8")
        self.ws.logo("repo", "forget", "alpha")
        result = self.commit(pa, "invalid rewrite", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("已冻结的文件被修改", result.stdout + result.stderr)
        (v / "logo.svg").write_text(original, encoding="utf-8")
        with (v / "notes.md").open("a", encoding="utf-8") as f:
            f.write("\n追加评审：通过。\n")
        self.commit(pa, "append review")
        self.assertTrue((pa / ".git").exists())  # forget 只移除登记

    def test_register_external_repo_and_reject_unsafe_paths(self):
        source = self.repo("source", "single")
        self.brand("source")
        external = self.ws.tmp / "external design"
        self.ws.git("system", "clone", "--quiet", "--no-local", str(source), str(external))
        self.ws.logo("repo", "add", "external", str(external))
        self.assertEqual(json.loads(self.cli("external", "status", "--json").stdout)["projects"][0]["slug"], "brand")
        with (external / "brand/brief.md").open("a", encoding="utf-8") as f:
            f.write("\n追加说明。\n")
        self.commit(external, "brief update")
        for name, path in (("duplicate", external), ("system", self.ws.root), ("nested", source / "brand")):
            result = self.ws.logo("repo", "add", name, str(path), ok=False)
            self.assertNotEqual(result.returncode, 0, name)

    def test_aggregate_status_check_and_missing_repo(self):
        self.repo("alpha")
        beta = self.repo("beta", "single")
        self.brand("alpha")
        self.brand("beta")
        result = json.loads(self.ws.logo("status", "--all-repos", "--json").stdout)
        self.assertEqual({r["repository"] for r in result["repositories"]}, {"default", "alpha", "beta"})
        self.ws.logo("check", "--all-repos")
        beta.rename(self.ws.tmp / "missing-beta")
        result = self.ws.logo("status", "--all-repos", "--json", ok=False)
        self.assertNotEqual(result.returncode, 0)
        by = {r["repository"]: r for r in json.loads(result.stdout)["repositories"]}
        self.assertIn("error", by["beta"])
        self.assertEqual(by["alpha"]["projects"][0]["slug"], "brand")
        self.assertNotEqual(self.ws.logo("check", "--all-repos", "--staged", ok=False).returncode, 0)

    def test_active_recovery_and_series_scoping(self):
        self.repo("alpha")
        self.repo("beta")
        self.cli("alpha", "series", "new", "group", "--architecture", "independent")
        self.assertIn("group", self.cli("alpha", "series").stdout)
        self.assertIn("还没有系列", self.cli("beta", "series").stdout)
        self.ws.logo("repo", "use", "alpha")
        registry = self.ws.root / ".yinxia/repositories.toml"
        registry.write_text(registry.read_text().replace('active = "alpha"', 'active = "missing"'))
        self.assertIn("仓库未登记", self.ws.logo("status", ok=False).stderr)
        self.ws.logo("repo", "use", "default")
        self.assertEqual(json.loads(self.ws.logo("status", "--json").stdout)["repository"], "default")

    @unittest.skipUnless(HAS_RENDER, "需要 resvg 与 magick")
    def test_inbox_and_import_preview_are_isolated(self):
        from test_handoff import block
        for repo in ("alpha", "beta"):
            self.repo(repo)
            p = self.brand(repo)
            self.version(repo, p)
        svg = TRIANGLE.format(c="#000000")
        reply = self.ws.write(".cache/reply.txt", block(1, 1, "badge.svg", svg))
        self.cli("alpha", "handoff", "receive", str(reply), "--project", "brand")
        self.assertTrue((self.ws.root / "repositories/alpha/inbox/brand/badge.svg").exists())
        self.assertIn("收件箱为空", self.cli("beta", "inbox").stdout)
        self.assertIn("收件箱为空", self.ws.logo("inbox").stdout)
        handoff = self.cli("alpha", "handoff", "brief", "brand").stdout
        self.assertIn("repositories/alpha/inbox/brand/", handoff)
        self.assertIn("./logo --repo alpha handoff validate", handoff)
        source = self.ws.write(".cache/source.svg", svg)
        self.cli("alpha", "import", str(source), "--project", "brand", "--direction", "A", "--analyze")
        result = self.cli("beta", "import", str(source), "--project", "brand", "--direction", "A",
                          "--source", "test", "--terms", "unknown", "--kind", "candidate", ok=False)
        self.assertIn("请先用同样的文件", result.stderr)
        self.assertTrue(list((self.ws.root / ".cache/repositories/alpha").glob("*/import/previewed.json")))
        self.assertFalse(list((self.ws.root / ".cache/repositories/beta").glob("*/import/previewed.json")))
        # 取消登记后把同一别名指向另一个仓库，不能复用旧仓库的预演授权。
        self.ws.logo("repo", "forget", "alpha")
        self.ws.logo("repo", "forget", "beta")
        self.ws.logo("repo", "add", "alpha", str(self.ws.root / "repositories/beta"))
        result = self.cli("alpha", "import", str(source), "--project", "brand", "--direction", "A",
                          "--source", "test", "--terms", "unknown", "--kind", "candidate", ok=False)
        self.assertIn("请先用同样的文件", result.stderr)
        self.assertEqual(self.ws.git("system", "status", "--porcelain").stdout, "")

    @unittest.skipUnless(HAS_RENDER, "需要 resvg 与 magick")
    def test_release_export_and_manifest_use_selected_git(self):
        self.repo("alpha", "single")
        self.repo("beta")
        a = self.brand("alpha")
        b = self.brand("beta")
        self.version("alpha", a)
        self.version("beta", b)
        self.release("alpha", a)
        self.release("beta", b)
        self.ws.logo("repo", "use", "beta")
        self.cli("alpha", "export", "brand", "svg")
        manifest = json.loads((a / "dist/r1/manifest.json").read_text())
        self.assertEqual(manifest["repository"], "alpha")
        self.assertEqual(manifest["targets"]["svg"]["git_head"], self.git(a.parent, "rev-parse", "--short", "HEAD").stdout.strip())
        self.assertFalse((b / "dist").exists())
        self.assertEqual(self.git(a.parent, "status", "--porcelain").stdout, "")
        self.assertEqual(self.git(b.parent, "status", "--porcelain").stdout, "")
