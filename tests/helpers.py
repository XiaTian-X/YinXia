"""测试辅助：在临时目录里搭建一套隔离的 LOGODesign 工作区（系统仓库 + 项目仓库）。

每个工作区复制当前系统仓库的已跟踪文件，链接真实的 .venv，并使用隔离的 Git 配置，
因此测试不会读写真实仓库，也不受个人 Git 全局配置（签名、钩子等）影响。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import logo  # noqa: E402  测试直接调用其中的纯函数

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}
HAS_RENDER = bool(shutil.which("resvg") and shutil.which("magick"))
FONT_BINARIES = sorted(p for p in (REPO / "assets" / "fonts").rglob("*") if logo.FONT_BINARY_RE.search(p.name))

TRIANGLE = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><path fill="{c}" d="M10 90 50 10 90 90Z"/></svg>\n'
PRIMARY = "#00897B"


def fill_doc(path: Path) -> None:
    """把文档中的模板占位行替换为实际内容（模拟“提交前填完”）。"""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "{{" not in s:
            m = re.match(r"^(\s*(?:-|\d+\.)\s+\*\*[^*]+\*\*\s*[:：])", line)
            if m and (re.search(r"[:：]\s*$", s) or "（" in s[len(m.group(1).strip()):]):
                line = m.group(1) + " 测试内容"
            elif re.fullmatch(r"（.*）", s):
                line = "测试内容。"
        out.append(line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


class Workspace:
    """一个隔离的工作区。用法：ws = Workspace(); ...; ws.cleanup()"""

    def __init__(self, with_fonts: bool = False, setup: bool = True):
        self.tmp = Path(tempfile.mkdtemp(prefix="logo-test-"))
        self.root = self.tmp / "ws"
        self.root.mkdir()
        # 已跟踪 + 未忽略的新文件：测试反映“即将提交”的工作区，而不只是上一次提交
        files = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                               capture_output=True, check=True).stdout.decode().split("\0")
        for rel in filter(None, files):
            src, dst = REPO / rel, self.root / rel
            if src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        if with_fonts:
            for f in FONT_BINARIES:
                dst = self.root / f.relative_to(REPO)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
        (self.root / ".venv").symlink_to(REPO / ".venv")
        self.env = {**os.environ, **GIT_ENV}
        self.git("system", "init", "-q", "-b", "main")
        if setup:
            self.logo("setup")
            self.git("projects", "checkout", "-q", "-b", "main")
            assert self.commit("system", "init").returncode == 0, "系统仓库初始提交失败"
            assert self.commit("projects", "init").returncode == 0, "项目仓库初始提交失败"

    @property
    def projects(self) -> Path:
        return self.root / "projects"

    def cleanup(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- 执行命令 --------------------------------------------------------------
    def logo(self, *args: str, ok: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run([str(self.root / "logo"), *args], cwd=self.root, env=self.env,
                           capture_output=True, text=True)
        if ok and r.returncode != 0:
            raise AssertionError(f"./logo {' '.join(args)} 失败：\n{r.stdout}\n{r.stderr}")
        return r

    def py(self, code: str) -> str:
        """用工作区自己的 tools/logo.py 执行一段 Python（变量 logo 可用），返回标准输出。"""
        r = subprocess.run([str(self.root / ".venv" / "bin" / "python"), "-c",
                            f"import sys; sys.path.insert(0, 'tools'); import logo\n{code}"],
                           cwd=self.root, env=self.env, capture_output=True, text=True)
        if r.returncode != 0:
            raise AssertionError(r.stderr)
        return r.stdout

    def git(self, repo: str, *args: str, ok: bool = True) -> subprocess.CompletedProcess:
        path = self.projects if repo == "projects" else self.root
        r = subprocess.run(["git", "-C", str(path), *args], env=self.env, capture_output=True, text=True)
        if ok and r.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} 失败：\n{r.stdout}\n{r.stderr}")
        return r

    def commit(self, repo: str, msg: str, add: bool = True) -> subprocess.CompletedProcess:
        """暂存并提交，返回结果（钩子拦截时 returncode != 0，输出含 ✗ 行）。"""
        if add:
            self.git(repo, "add", "-A")
        return self.git(repo, "commit", "-q", "-m", msg, ok=False)

    # -- 文件 ------------------------------------------------------------------
    def write(self, rel: str, text: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    # -- 常用场景 --------------------------------------------------------------
    def new_project(self, slug: str = "demo") -> Path:
        """新建并提交一个项目：主色为 PRIMARY，brief 已填写。"""
        self.logo("new", slug, "--name", slug.title())
        toml = self.projects / slug / "project.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace('primary = "#000000"', f'primary = "{PRIMARY}"', 1),
                        encoding="utf-8")
        fill_doc(self.projects / slug / "brief.md")
        self.logo("index")
        r = self.commit("projects", f"[{slug}] init")
        assert r.returncode == 0, r.stdout + r.stderr
        return self.projects / slug

    def new_version(self, slug: str = "demo", letter: str = "A", name: str = "mountain",
                    svg: str | None = None, extra: tuple[str, ...] = ()) -> Path:
        """（必要时新建方向并）新建版本，写入 SVG、填完文档。返回版本目录。"""
        p = self.projects / slug
        if not any(d.name.startswith(f"{letter}-") for d in (p / "directions").glob("*") if d.is_dir()):
            self.logo("direction", slug, letter, name)
            fill_doc(next((p / "directions").glob(f"{letter}-*")) / "direction.md")
        out = self.logo("version", slug, letter, *extra).stdout
        vid = re.search(r"([A-Z]-v\d{3})", out).group(1)
        vdir = next((p / "directions").glob(f"{letter}-*")) / f"v{vid[-3:]}"
        (vdir / "logo.svg").write_text(svg or TRIANGLE.format(c=PRIMARY), encoding="utf-8")
        fill_doc(vdir / "notes.md")
        self.logo("index")
        return vdir

    def blocked(self, r: subprocess.CompletedProcess) -> list[str]:
        """提交结果中的 ✗ 行（被拦截的原因）。"""
        return [ln for ln in (r.stdout + r.stderr).splitlines() if ln.startswith("✗")]
