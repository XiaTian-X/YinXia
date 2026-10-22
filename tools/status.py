"""项目待办：新会话里的 Agent 没有之前的记忆，运行 ./logo status 即可知道每个项目做到哪、下一步做什么。

只读、不渲染（单色结构、越界等渲染类检查留给 ./logo check）；各项判断复用 check 用到的同一批函数，结论与 check 一致。
"""
from __future__ import annotations

import json
import re

import logo

# 待办类别，按处理顺序排列
KINDS = ("暂停", "访谈", "未提交", "设计", "系列", "交付", "封面", "收件箱")


def uncommitted(p: "logo.Project") -> list[str]:
    """项目目录中未提交的改动（相对项目仓库的路径）。"""
    r = logo.git("status", "--porcelain", "-z", "--no-renames", "--untracked-files=all", "--", p.slug, check=False,
                 repo="projects")
    return [x[3:] for x in r.stdout.decode("utf-8").split("\0") if len(x) > 3] if r.returncode == 0 else []


def project_todo(p: "logo.Project") -> list[dict]:
    import covers  # 延迟导入：均依赖 logo
    import importer
    import series
    out: list[dict] = []

    def add(kind: str, text: str, command: str = "") -> None:
        out.append({"kind": kind, "text": text, "command": command})

    st = p.cfg.get("state", {})
    # -- 访谈 --
    brief = p.dir / "brief.md"
    holes = logo.unfilled_placeholders(f"{p.slug}/brief.md", brief.read_text(encoding="utf-8")) if brief.exists() else ["缺少 brief.md"]
    if holes:
        add("访谈", f"brief.md 还有 {len(holes)} 处未填写，需求访谈未完成", "与用户完成 logo-workflow 第 0 步访谈")
    if (rh := series.rules_holes(p)):
        sid = series.membership(p)["id"]
        add("访谈", f"系列规则 {series.rules_path(sid)} 还有 {len(rh)} 处未填写", "与用户确定系列规则")
    # -- 未提交 --
    if (changed := uncommitted(p)):
        docs = [c for c in changed if (logo.PROJECTS / c).is_file()
                and logo.unfilled_placeholders(c, (logo.PROJECTS / c).read_text(encoding="utf-8", errors="ignore"))]
        text = f"有 {len(changed)} 个文件改动未提交" + (f"，其中 {len(docs)} 份新文档还有模板占位：{', '.join(docs[:3])}" if docs else "")
        add("未提交", text, f"填完后 ./logo index && ./logo check && git -C projects add -A && git -C projects commit -m \"[{p.slug}] …\"")
    # -- 设计 --
    sel, cur = st.get("selected_version"), st.get("current_release")
    try:
        root = p.release_root_version(cur) if cur else None
    except logo.LogoError:
        root = None
    m = series.membership(p)
    inheriting_sub = bool(m and m.get("role") == "sub" and m.get("id") in series.all_series()
                          and series.load_series(m["id"]).get("architecture") in series.INHERITING)
    stage = st.get("stage", "")
    if stage == "paused":
        add("暂停", "项目已暂停（以下待办供恢复时参考）", f"恢复时 ./logo state {p.slug} stage=iterating")
    versions = [f"{d.name.split('-', 1)[0]}-{v.name}" for d in p.directions() for v in p.versions_in(d)]
    if not holes and not p.directions():
        add("设计", "还没有设计方向", f"./logo direction {p.slug} A <英文短名>")
    elif versions and not sel:
        add("设计", f"已有 {len(versions)} 个版本，尚未选定", f"./logo compare {p.slug} 给用户看，选定后 ./logo decision 并 "
            f"./logo state {p.slug} selected_version=<版本>")
    if sel and "-v" in sel:  # 选定版本之后同方向又有新版本：系统判断不了用户是否看过，只提示给用户看
        letter, num = sel.split("-v")
        newer = [v for v in versions if v.startswith(f"{letter}-v") and v.split("-v")[1] > num]
        if newer:
            add("设计", f"选定版本 {sel} 之后又有新版本：{'、'.join(newer)}",
                f"./logo compare {p.slug} {sel} {' '.join(newer)} 给用户看后决定是否选定")
    if sel and sel != root:
        if inheriting_sub and not series.review_passed(m["id"], p.slug, sel):
            add("设计", f"选定版本 {sel} 尚未通过系列评审", f"./logo series compare {m['id']} --with {p.slug}={sel}，给用户看后 series review")
        add("设计", f"选定版本 {sel} 尚未发布", f"与用户确认定稿：./logo decision {p.slug} finalize-… 后 ./logo release {p.slug} --from {sel}")
    # -- 系列 --
    if (up := series.pending_update(p)):
        _, master, based, latest = up
        add("系列", f"母品牌 {master.slug} 已发布 {latest}（本项目依据 {based}），{series.diff_text(*series.palette_diff(p, master))}",
            f"逐项问用户后 ./logo series sync {p.slug} --palette … --note \"…\"")
    # -- 交付 --
    if cur and stage == "releasing" and sel == root:
        add("交付", f"当前发布 {cur} 尚未标记交付", f"./logo export {p.slug} 交付后 ./logo state {p.slug} stage=delivered")
    if cur:
        guide = p.dir / "releases" / cur / "guide.md"
        if guide.exists() and logo.unfilled_placeholders(f"{p.slug}/releases/{cur}/guide.md", guide.read_text(encoding="utf-8")):
            add("交付", f"当前发布 {cur} 的 guide.md 未填完，无法导出", "未提交时填完；已提交则 ./logo release --from 新建发布")
        terms: list[str] = []
        importer.terms_warnings(p, terms)
        for w in terms:
            add("交付", w.split(": ", 1)[-1], "与用户确认版权与商用权")
    # -- 封面 --
    for cdir in covers.project_covers(p):
        try:
            cfg = covers.cover_cfg(cdir)
            notes = [covers.outdated_warning(p, cdir, cfg)] + covers.skeleton_notes(cdir, cfg["cover"])
        except (logo.LogoError, KeyError):
            notes = [f"{logo.rel(cdir)}: cover.toml 无效，运行 ./logo check 查看"]
        for n in filter(None, notes):  # 去掉封面目录前缀，只保留画幅文件名与说明
            text = re.sub(rf"^{re.escape(logo.rel(cdir))}(/|[：:]\s*)", "", n)
            add("封面", f"{cdir.name}：{text}", f"./logo cover … {p.slug} {cdir.name[:3]}")
    # -- 收件箱 --
    for it in importer.inbox_items():
        if it["meta"].get("project") == p.slug:
            add("收件箱", f"inbox/{it['file']} 待导入（{it['type']}）", f"./logo import / cover import … --analyze，给用户看后导入")
    return sorted(out, key=lambda x: KINDS.index(x["kind"]))


def global_todo() -> list[dict]:
    import covers  # 延迟导入：均依赖 logo
    import importer
    import series
    out = []
    for it in importer.inbox_items():
        if not it["meta"].get("project"):
            out.append({"kind": "收件箱", "text": f"inbox/{it['file']} 去向待定（{it['type']}）", "command": "问用户放进哪个项目"})
    for sid in series.all_series():
        fam = covers.Family(sid)
        for cdir in covers.project_covers(fam):
            try:
                w = covers.family_outdated_warning(fam, cdir, covers.cover_cfg(cdir))
            except (logo.LogoError, KeyError):
                continue
            if w:
                out.append({"kind": "封面", "text": w, "command": f"./logo cover sync-logo {fam.slug} {cdir.name[:3]}"})
    backup: list[str] = []
    logo.check_backup(backup)
    out += [{"kind": "备份", "text": w.split("：", 1)[0], "command": "添加私有远程并定期推送"} for w in backup]
    return out


def cmd_status(a) -> None:
    if not logo.projects_repo_ready():
        raise logo.LogoError("项目仓库 projects/ 尚未初始化，请先运行 ./logo setup")
    projects = [logo.Project(a.slug)] if a.slug else logo.all_projects()
    data = {"projects": [], "global": [] if a.slug else global_todo()}
    for p in projects:
        try:
            st = p.cfg.get("state", {})
            entry = {"slug": p.slug, "name": p.cfg.get("project", {}).get("name", ""), "stage": st.get("stage", ""),
                     "current_release": st.get("current_release", ""), "todo": project_todo(p)}
        except logo.LogoError as e:  # 一个项目的配置坏了，不影响查看其他项目的待办
            entry = {"slug": p.slug, "name": "", "stage": "?", "current_release": "",
                     "todo": [{"kind": "访谈", "text": f"项目配置无法读取：{e}", "command": f"./logo check {p.slug}"}]}
        data["projects"].append(entry)
    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    if not data["projects"]:
        print("还没有项目：先与用户完成需求访谈（logo-workflow 第 0 步），再 ./logo new")
    for pr in data["projects"]:
        print(f"{pr['slug']}（{pr['name']} · {pr['stage']}" + (f" · 当前发布 {pr['current_release']}" if pr["current_release"] else "") + "）")
        for i, t in enumerate(pr["todo"], 1):
            print(f"  {i}. [{t['kind']}] {t['text']}" + (f"\n     → {t['command']}" if t["command"] else ""))
        if not pr["todo"]:
            print("  没有待办")
    if data["global"]:
        print("全局：")
        for t in data["global"]:
            print(f"  - [{t['kind']}] {t['text']}" + (f"（{t['command']}）" if t["command"] else ""))
    print("以上不含渲染类检查；完整检查：./logo check。请把待办讲给用户，问从哪一项开始。")


def register(sub) -> None:
    s = sub.add_parser("status", help="各项目的进度与待办（新会话先运行，把待办讲给用户）")
    s.add_argument("slug", nargs="?", help="只看这个项目")
    s.add_argument("--json", action="store_true", help="以 JSON 输出（供 Agent 读取）")
    s.set_defaults(fn=cmd_status)
