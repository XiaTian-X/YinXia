"""本地设计仓库登记与选择；登记表不进入公开系统仓库。"""
from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import logo

REGISTRY = logo.ROOT / ".yinxia" / "repositories.toml"
REPOSITORIES = logo.ROOT / "repositories"
KINDS = ("workspace", "single")


def registry() -> dict:
    data = logo.load_toml(REGISTRY) if REGISTRY.exists() else {}
    active, entries = data.get("active", "default"), data.get("repositories", {})
    if not isinstance(active, str) or not isinstance(entries, dict):
        raise logo.LogoError("本地仓库登记表格式错误")
    for name, value in entries.items():
        if name == "default" or not logo.SLUG_RE.fullmatch(name) or not isinstance(value, str) or not value:
            raise logo.LogoError(f"本地仓库登记无效：{name!r}")
    return {"active": active, "repositories": entries}


def paths(data: dict | None = None) -> dict[str, Path]:
    data = data if data is not None else registry()
    return {"default": logo.ROOT / "projects", **{
        name: (logo.ROOT / value).expanduser().resolve() if not value.startswith("~") else Path(value).expanduser().resolve()
        for name, value in data["repositories"].items()}}


def save(data: dict) -> None:
    q = lambda value: json.dumps(value, ensure_ascii=False)
    text = "# 本机仓库登记，由 ./logo repo 管理，不提交 Git\n"
    text += f"active = {q(data['active'])}\n\n[repositories]\n"
    text += "".join(f"{name} = {q(value)}\n" for name, value in sorted(data["repositories"].items()))
    logo.write_atomic(REGISTRY, text)


def kind(path: Path, staged: bool = False) -> str:
    meta = path / "repository.toml"
    raw = logo.staged_bytes(meta) if staged else None
    if raw is not None:
        try:
            data = tomllib.loads(raw.decode("utf-8"))
        except (UnicodeError, tomllib.TOMLDecodeError) as e:
            raise logo.LogoError(f"{logo.rel(meta)} 格式错误：{e}")
    elif meta.exists():
        data = logo.load_toml(meta)
    else:
        return "workspace"  # 兼容现有 projects/ 仓库
    section = data.get("repository", {})
    value = section.get("kind") if isinstance(section, dict) else None
    if value not in KINDS:
        raise logo.LogoError(f"{logo.rel(meta)}: [repository] kind 须为 workspace 或 single")
    return value


def git_root(path: Path) -> bool:
    if not (path / ".git").exists():
        return False
    r = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return r.returncode == 0 and Path(r.stdout.strip()).resolve() == path.resolve()


def validate_location(path: Path, existing: dict[str, Path], *, exclude: str | None = None) -> None:
    path = path.resolve()
    if path == logo.ROOT or path in logo.ROOT.parents:
        raise logo.LogoError("设计仓库不能是系统仓库或其父目录")
    if logo.ROOT in path.parents and path != logo.ROOT / "projects" and path.parent != REPOSITORIES:
        raise logo.LogoError("系统目录内的设计仓库须放在 repositories/<仓库名>/；projects/ 保留给 default")
    for name, other in existing.items():
        if name == exclude:
            continue
        other = other.resolve()
        if path == other or path in other.parents or other in path.parents:
            raise logo.LogoError(f"仓库路径与 {name} 重复或嵌套")
    if git_root(path):
        def common(p: Path) -> Path:
            r = subprocess.run(["git", "-C", str(p), "rev-parse", "--git-common-dir"],
                               capture_output=True, text=True, check=True)
            return (p / r.stdout.strip()).resolve()
        shared = common(path)
        if common(logo.ROOT) == shared:
            raise logo.LogoError("设计仓库不能与系统共用 Git 数据目录")
        for name, other in existing.items():
            if name != exclude and git_root(other) and common(other) == shared:
                raise logo.LogoError(f"设计仓库不能与 {name} 共用 Git 数据目录")


def select(selector: str | None = None, hook_path: str | None = None) -> tuple[str, Path]:
    # 提交钩子从 Git 的实际工作目录选择，不受另一个会话切换 active 的影响。
    if hook_path is not None:
        path = Path(hook_path).expanduser().resolve()
        validate_location(path, {}, exclude=None)
        if not git_root(path):
            raise logo.LogoError("提交钩子的工作目录不是独立设计仓库")
        # 默认仓库保持旧缓存位置，其他仓库即使登记丢失仍按路径隔离检查。
        if path == logo.ROOT / "projects":
            return "default", path
        return "hook-" + hashlib.sha256(str(path).encode()).hexdigest()[:16], path
    if selector == "default":
        path = logo.ROOT / "projects"
        validate_location(path, {})
        return "default", path
    data = registry()
    name = selector or data["active"]
    entries = paths(data)
    if name not in entries:
        raise logo.LogoError(f"仓库未登记：{name}（./logo repo list 查看；./logo repo use default 恢复默认）")
    path = entries[name].resolve()
    validate_location(path, entries, exclude=name)
    if name != "default" and not git_root(path):
        raise logo.LogoError(f"仓库 {name} 不存在或不是独立 Git 仓库：{path}")
    return name, path


def configure(selector: str | None = None, hook_path: str | None = None) -> None:
    name, path = select(selector, hook_path)
    logo.REPOSITORY_ID, logo.PROJECTS = name, path
    logo.PROJECTS_MD = path / "PROJECTS.md"
    location = hashlib.sha256(str(path).encode()).hexdigest()[:16]
    logo.CACHE = logo.ROOT / ".cache" if name == "default" else logo.ROOT / ".cache" / "repositories" / name / location
    logo.HISTORY_CACHE = logo.CACHE / "history-check.json"
    logo.STRUCTURE_CACHE = logo.CACHE / "structure-cache.json"
    logo.INBOX = logo.ROOT / "inbox" if name == "default" else path / "inbox"


def setup_data_repository(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if not (path / ".git").exists():
        logo.run(["git", "init", "-q", str(path)])
    if not git_root(path):
        raise logo.LogoError(f"不是独立 Git 仓库：{path}")
    wanted = logo.PROJECTS_GITIGNORE_TEMPLATE.read_text(encoding="utf-8")
    ignore = path / ".gitignore"
    if not ignore.exists() or ignore.read_text(encoding="utf-8") != wanted:
        ignore.write_text(wanted, encoding="utf-8")
        print(f"已写入 {logo.rel(ignore)}（白名单模板），请在该设计仓库提交")
    hook = os.path.relpath(logo.ROOT / ".githooks" / "projects", path)
    logo.run(["git", "-C", str(path), "config", "core.hooksPath", hook])


def check_layout(path: Path, value: str) -> list[str]:
    projects = list(path.glob("*/project.toml"))
    errors = []
    if (path / "inbox/project.toml").exists():
        errors.append("inbox 是收件箱保留目录，不能用作品牌项目目录")
    if value == "single":
        if len(projects) > 1:
            errors.append("single 仓库最多容纳一个品牌项目")
        if (path / "_series").exists() or any("series" in logo.load_toml(p) for p in projects):
            errors.append("single 仓库只管理独立品牌；系列请放在 workspace 仓库")
    return errors


def check_selected(errors: list[str], staged: bool = False) -> None:
    try:
        value = kind(logo.PROJECTS, staged)
        errors.extend(check_layout(logo.PROJECTS, value))
        if staged and value == "single":
            paths_ = logo.projects_paths("ls-files", "-z", "--cached")
            if sum(bool(re.fullmatch(r"[^/]+/project\.toml", p)) for p in paths_) > 1:
                errors.append("single 仓库暂存区包含多个品牌项目")
            if any(p.startswith("_series/") for p in paths_):
                errors.append("single 仓库暂存区不能包含系列记录")
            for path_ in paths_:
                if re.fullmatch(r"[^/]+/project\.toml", path_):
                    raw = logo.staged_bytes(logo.PROJECTS / path_)
                    try:
                        cfg = tomllib.loads(raw.decode("utf-8")) if raw is not None else {}
                    except (UnicodeError, tomllib.TOMLDecodeError):
                        errors.append(f"暂存区 {path_} 不是有效 TOML")
                        continue
                    if "series" in cfg:
                        errors.append("single 仓库暂存区不能登记品牌系列关系")
    except logo.LogoError as e:
        errors.append(str(e))


def cmd_list(a) -> None:
    data = registry()
    items = []
    for name, path in paths(data).items():
        try:
            value = kind(path)
            error = "" if git_root(path) else "尚未初始化或路径已失效"
        except logo.LogoError as e:
            value, error = "?", str(e)
        items.append({"id": name, "path": str(path), "kind": value, "active": name == data["active"],
                      "projects": len(list(path.glob("*/project.toml"))), "error": error})
    if a.json:
        print(json.dumps({"active": data["active"], "repositories": items}, ensure_ascii=False, indent=2))
    else:
        for item in items:
            print(f"{'*' if item['active'] else ' '} {item['id']} · {item['kind']} · {item['projects']} 个品牌 · {item['path']}"
                  + (f" · {item['error']}" if item['error'] else ""))


def cmd_new(a) -> None:
    data = registry()
    if a.id == "default" or not logo.SLUG_RE.fullmatch(a.id) or a.id in paths(data):
        raise logo.LogoError("仓库名须为未使用的小写英文/数字/短横线；default 已保留")
    path = REPOSITORIES / a.id
    validate_location(path, paths(data))
    if path.exists():
        raise logo.LogoError(f"目录已存在，拒绝覆盖；已有设计仓库请用 repo add 登记：{path}")
    setup_data_repository(path)
    logo.write_new(path / "repository.toml", f'[repository]\nkind = "{a.kind}"\n')
    data["repositories"][a.id] = path.relative_to(logo.ROOT).as_posix()
    save(data)
    logo.run([sys.executable, str(logo.ROOT / "tools" / "logo.py"), "--repo", a.id, "index"])
    print(f"已创建 {a.kind} 仓库 {a.id} → {logo.rel(path)}")
    print(f"选择本次命令：./logo --repo {a.id} status；设为默认：./logo repo use {a.id}")
    print(f"初始 repository.toml、.gitignore 与 PROJECTS.md 尚未提交，请在该仓库配置提交身份后保存")


def cmd_add(a) -> None:
    data = registry()
    if a.id == "default" or not logo.SLUG_RE.fullmatch(a.id) or a.id in paths(data):
        raise logo.LogoError("仓库名非法、已登记或被保留")
    path = Path(a.path).expanduser().resolve()
    validate_location(path, paths(data))
    if not git_root(path):
        raise logo.LogoError("只能登记已有的独立 Git 仓库，不能把其子目录当作仓库")
    tracked = subprocess.run(["git", "-C", str(path), "ls-files", "-z"], capture_output=True, text=True, check=True).stdout.split("\0")
    bad = [p for p in tracked if p and not logo.path_allowed(p, "projects")]
    if bad or (path / "project.toml").exists():
        raise logo.LogoError("已有仓库不符合设计仓库结构（品牌资料应在 <slug>/ 内），拒绝修改")
    errors = check_layout(path, kind(path))
    if errors:
        raise logo.LogoError("；".join(errors))
    setup_data_repository(path)
    data["repositories"][a.id] = logo.rel(path)
    save(data)
    print(f"已登记 {a.id} → {path}；Git 历史与设计文件保留。请用 --repo {a.id} index/check 检查。")


def cmd_use(a) -> None:
    select(a.id)
    data = registry()
    data["active"] = a.id
    save(data)
    print(f"默认设计仓库已切换为 {a.id}（只影响本机未指定 --repo 的操作）")


def cmd_forget(a) -> None:
    data = registry()
    if a.id not in data["repositories"]:
        raise logo.LogoError("该仓库未登记，或是不可移除的 default")
    del data["repositories"][a.id]
    if data["active"] == a.id:
        data["active"] = "default"
    save(data)
    print(f"已取消本机登记：{a.id}；仓库目录、设计文件与 Git 历史均保留")


def all_repositories(a) -> None:
    if getattr(a, "slug", None) or getattr(a, "staged", False) or (a.cmd == "check" and a.scope != "all"):
        raise logo.LogoError("--all-repos 不能与项目名、--staged 或 --scope 同时使用")
    results, failed = [], False
    if a.cmd == "check":
        r = subprocess.run([sys.executable, str(logo.ROOT / "tools/logo.py"), "--repo", "default", "check", "--scope", "system"],
                           cwd=logo.ROOT, capture_output=True, text=True)
        print("[系统仓库]\n" + r.stdout + r.stderr, end="")
        failed |= r.returncode != 0
    for name, path in paths().items():
        if name == "default" and not (path / ".git").exists():
            continue
        command = [sys.executable, str(logo.ROOT / "tools/logo.py"), "--repo", name, a.cmd]
        command += ["--json"] if a.cmd == "status" else ["--scope", "projects"]
        r = subprocess.run(command, cwd=logo.ROOT, capture_output=True, text=True)
        item = {"repository": name, "path": str(path)}
        if r.returncode:
            failed = True
            item["error"] = (r.stdout + r.stderr).strip()
        elif a.cmd == "status":
            item.update(json.loads(r.stdout))
        results.append(item)
        if a.cmd == "check":
            print(f"[设计仓库 {name}]\n" + r.stdout + r.stderr, end="")
        elif not a.json:
            print(f"[设计仓库 {name}] {path}")
            if "error" in item:
                print("✗ " + item["error"])
            for project in item.get("projects", []):
                print(f"  {project['slug']} · {project['stage']} · {project['current_release'] or '尚未发布'}")
                for todo in project["todo"]:
                    print(f"    [{todo['kind']}] {todo['text']}" + (f" → {todo['command']}" if todo['command'] else ""))
            for todo in item.get("global", []):
                print(f"  [{todo['kind']}] {todo['text']}")
    if a.cmd == "status" and a.json:
        print(json.dumps({"repositories": results}, ensure_ascii=False, indent=2))
    elif not results:
        print("没有已初始化的设计仓库，请运行 ./logo setup 或 ./logo repo new")
    if failed:
        raise SystemExit(1)


def register(sub) -> None:
    s = sub.add_parser("repo", help="设计仓库：登记、创建、选择；支持单品牌和多品牌工作区")
    s.set_defaults(fn=cmd_list, json=False)
    ss = s.add_subparsers(dest="repo_action")
    p = ss.add_parser("list", help="列出本机登记的设计仓库")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_list)
    p = ss.add_parser("new", help="在 repositories/<名称>/ 新建独立设计仓库")
    p.add_argument("id")
    p.add_argument("--kind", choices=KINDS, default="workspace")
    p.set_defaults(fn=cmd_new)
    p = ss.add_parser("add", help="登记已有设计仓库并配置钩子，不迁移设计历史")
    p.add_argument("id")
    p.add_argument("path")
    p.set_defaults(fn=cmd_add)
    for command, fn in (("use", cmd_use), ("forget", cmd_forget)):
        p = ss.add_parser(command, help="切换默认仓库" if command == "use" else "取消本机登记，保留目录与历史")
        p.add_argument("id")
        p.set_defaults(fn=fn)
