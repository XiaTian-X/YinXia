"""系列模块：多个 Logo 项目组成的品牌系列（母品牌与子品牌）。

系列记录在项目仓库的 projects/_series/<系列名>/：series.toml（名称、架构，由命令维护）与 series.md（系列规则）。
成员关系以各项目 project.toml 的 [series] 为准（由 ./logo new --series / ./logo series add 写入），不在系列记录里重复登记。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import logo

SERIES_DIR = logo.PROJECTS / "_series"
ARCHITECTURES = {"unified": "统一型", "endorsed": "背书型", "independent": "独立型"}
ROLES = {"master": "母品牌", "sub": "子品牌"}
INHERITING = ("unified", "endorsed")  # 子品牌继承母品牌色板、需要母品牌先发布的架构


def all_series() -> dict[str, dict]:
    """全部系列 {系列名: series.toml 内容}；读取失败的跳过（由 check_series 报告）。"""
    out = {}
    for d in sorted(SERIES_DIR.iterdir()) if SERIES_DIR.exists() else []:
        if d.is_dir() and (d / "series.toml").exists():
            try:
                out[d.name] = logo.load_toml(d / "series.toml").get("series", {})
            except logo.LogoError:
                continue
    return out


def load_series(sid: str) -> dict:
    found = all_series()
    if sid not in found:
        names = ", ".join(found) or "（无）"
        raise logo.LogoError(f"系列不存在：{sid}（已有：{names}；用 ./logo series new 创建）")
    return found[sid]


def membership(p: "logo.Project") -> dict | None:
    try:
        return p.cfg.get("series") or None
    except logo.LogoError:
        return None  # project.toml 无法解析：由 check_project 报告，这里不中断系列检查与看板


def members(sid: str) -> list[tuple["logo.Project", str]]:
    return [(p, m["role"]) for p in logo.all_projects() if (m := membership(p)) and m.get("id") == sid]


def master_of(sid: str) -> "logo.Project | None":
    return next((p for p, role in members(sid) if role == "master"), None)


def rules_path(sid: str) -> str:
    return f"_series/{sid}/series.md"


def describe(sid: str | None, role: str | None) -> tuple[str, str]:
    """brief.md 中“项目类型与架构”两行的内容。"""
    if not sid:
        return "独立 Logo（不属于系列）", "无"
    s = load_series(sid)
    master = master_of(sid)
    who = ROLES[role] + (f"（母品牌：{master.slug}）" if role == "sub" and master else "")
    return (f"系列「{s.get('name', sid)}」（{sid}，{ARCHITECTURES.get(s.get('architecture'), '?')}）的{who}",
            f"`{rules_path(sid)}`")


def check_join(sid: str, role: str, slug: str) -> "logo.Project | None":
    """校验项目能否以该角色加入系列；返回需要继承的母品牌（无需继承时为 None）。"""
    s = load_series(sid)
    if role not in ROLES:
        raise logo.LogoError(f"--role 只能是 {' / '.join(ROLES)}")
    master = master_of(sid)
    if role == "master":
        if master and master.slug != slug:
            raise logo.LogoError(f"系列 {sid} 已有母品牌 {master.slug}，一个系列只能有一个母品牌")
        return None
    if s.get("architecture") not in INHERITING:
        return None
    if not master:
        raise logo.LogoError(f"{ARCHITECTURES[s['architecture']]}系列 {sid} 还没有母品牌：先用 "
                             f"./logo new <slug> --series {sid} --role master 创建并完成发布")
    if not master.cfg["state"].get("current_release"):
        raise logo.LogoError(f"母品牌 {master.slug} 还没有发布；子品牌要以母品牌定稿的标志与色板为准，请先发布母品牌")
    return master


def toml_value(v) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    return json.dumps(v, ensure_ascii=False)  # TOML 基本字符串与 JSON 的转义规则兼容


def series_section(sid: str, role: str, master_release: str | None) -> str:
    lines = ["[series]", "# 由 ./logo new --series / ./logo series add 写入，请勿手改",
             f"id = {toml_value(sid)}", f"role = {toml_value(role)}"]
    if master_release:
        lines.append(f"master_release = {toml_value(master_release)}  # 继承母品牌色板与起稿所依据的发布")
    return "\n".join(lines) + "\n"


def inherit_from(text: str, master: "logo.Project") -> str:
    """把新项目 project.toml（模板文本）中的色板、配色模式与 App 图标配色换成母品牌的。"""
    mc = master.cfg
    rid = mc["state"]["current_release"]
    block = [f"# 色板与配色模式继承自母品牌 {master.slug}（{rid}），按系列规则维护", "[palette]"]
    block += [f"{k} = {toml_value(v)}" for k, v in mc.get("palette", {}).items()]
    modes = mc.get("color_modes", {})
    block += ["", "[color_modes]"] + [f"{k} = {toml_value(v)}" for k, v in modes.items() if not isinstance(v, dict)]
    for name, table in modes.items():
        if isinstance(table, dict):
            block += [f"[color_modes.{name}]"] + [f"{k} = {toml_value(v)}" for k, v in table.items()]
    start, end = text.index("[palette]\n"), text.index("[fonts]\n")
    text = text[:start] + "\n".join(block) + "\n\n" + text[end:]
    ex = mc.get("export", {})
    bg, mode = ex.get("app_background") or ex.get("android_background"), logo.icon_mode(master)
    if bg:
        text = re.sub(r'^app_background = .*$', f"app_background = {toml_value(bg)}", text, count=1, flags=re.M)
    return re.sub(r'^icon_mode = .*$', f"icon_mode = {toml_value(mode)}", text, count=1, flags=re.M)


def master_start(p: "logo.Project") -> tuple[Path, str]:
    """子品牌以母品牌主版起稿：返回 (母品牌发布中的 primary.svg, 写进 notes.md 的来源说明)。"""
    m = membership(p)
    if not m or m.get("role") != "sub":
        raise logo.LogoError("--from-master 只用于系列子品牌")
    arch = load_series(m["id"]).get("architecture")
    if arch not in INHERITING:
        raise logo.LogoError(f"{ARCHITECTURES.get(arch, arch)}系列的子品牌独立设计，不能以母品牌起稿")
    master, rid = master_of(m["id"]), m.get("master_release")
    if not master or not rid:
        raise logo.LogoError(f"找不到系列 {m['id']} 的母品牌或其发布记录")
    svg = master.release_dir(rid) / "layouts" / "primary.svg"
    if not svg.exists():
        raise logo.LogoError(f"母品牌 {master.slug} 的 {rid} 缺少 primary.svg")
    return svg, f"母品牌 {master.slug} {rid}/primary.svg（系列 {m['id']}）"


# ---------------------------------------------------------------------------
# 检查与看板
# ---------------------------------------------------------------------------
def check_series(errors: list[str], warnings: list[str]) -> None:
    """系列记录与成员关系：系列须有效、角色合法、一个系列最多一个母品牌、继承型子品牌须有母品牌及其发布。"""
    valid: dict[str, dict] = {}
    for d in sorted(SERIES_DIR.iterdir()) if SERIES_DIR.exists() else []:
        if not d.is_dir():
            continue
        where = logo.rel(d)
        if not logo.SLUG_RE.match(d.name):
            errors.append(f"{where}: 系列名只能使用小写英文、数字和短横线")
            continue
        try:
            s = logo.load_toml(d / "series.toml").get("series", {})
        except logo.LogoError as e:
            errors.append(f"{where}: {e}")
            continue
        if s.get("id") != d.name:
            errors.append(f"{where}/series.toml: id 须与目录名 {d.name} 一致")
        if s.get("architecture") not in ARCHITECTURES:
            errors.append(f"{where}/series.toml: architecture 须为 {' / '.join(ARCHITECTURES)}")
        if not s.get("name"):
            errors.append(f"{where}/series.toml: 缺少 name")
        if not (d / "series.md").exists():
            errors.append(f"{where}: 缺少系列规则 series.md")
        valid[d.name] = s
    masters: dict[str, list[str]] = {}
    for p in logo.all_projects():
        m = membership(p)
        if not m:
            continue
        r = logo.rel(p.toml_path)
        sid, role = m.get("id"), m.get("role")
        if sid not in valid:
            errors.append(f"{r}: [series] 所属系列 {sid!r} 不存在（_series/{sid}/）")
            continue
        if role not in ROLES:
            errors.append(f"{r}: [series] role 须为 {' / '.join(ROLES)}")
            continue
        if role == "master":
            masters.setdefault(sid, []).append(p.slug)
    for sid, slugs in masters.items():
        if len(slugs) > 1:
            errors.append(f"系列 {sid} 有多个母品牌：{', '.join(slugs)}（一个系列只能有一个母品牌）")
    for p in logo.all_projects():
        m = membership(p)
        if not m or m.get("id") not in valid or m.get("role") != "sub":
            continue
        if valid[m["id"]].get("architecture") not in INHERITING:
            continue
        r = logo.rel(p.toml_path)
        slugs = masters.get(m["id"], [])
        if not slugs:
            errors.append(f"{r}: 子品牌所属的{ARCHITECTURES[valid[m['id']]['architecture']]}系列 {m['id']} 没有母品牌")
            continue
        rid = m.get("master_release")
        try:
            logo.Project(slugs[0]).release_dir(rid or "")
        except logo.LogoError:
            errors.append(f"{r}: [series] master_release {rid!r} 不是母品牌 {slugs[0]} 的发布")


def index_groups() -> list[tuple[str, list[tuple["logo.Project", str]]]]:
    """看板分组：[(系列标题, [(项目, 角色名)])]，母品牌在前。"""
    out = []
    for sid, s in all_series().items():
        ms = sorted(members(sid), key=lambda x: (x[1] != "master", x[0].slug))
        title = f"系列：{s.get('name', sid)}（{sid}，{ARCHITECTURES.get(s.get('architecture'), '?')}）"
        out.append((title, [(p, ROLES.get(role, role)) for p, role in ms]))
    return out


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------
def cmd_list(a) -> None:
    found = all_series()
    if not found:
        print("还没有系列（./logo series new <系列名> --name 名称 --architecture unified|endorsed|independent）")
        return
    for sid, s in found.items():
        print(f"{sid}  {s.get('name', '')}  [{ARCHITECTURES.get(s.get('architecture'), '?')}]  规则：{rules_path(sid)}")
        ms = members(sid)
        for p, role in sorted(ms, key=lambda x: (x[1] != "master", x[0].slug)):
            extra = f"（基于母品牌 {membership(p).get('master_release')}）" if membership(p).get("master_release") else ""
            print(f"  {ROLES.get(role, role)}  {p.slug}  {p.cfg['project'].get('name', '')}{extra}")
        if not ms:
            print("  （还没有成员）")


def cmd_new(a) -> None:
    if not logo.projects_repo_ready():
        raise logo.LogoError("项目仓库 projects/ 尚未初始化，请先运行 ./logo setup")
    if not logo.SLUG_RE.match(a.id):
        raise logo.LogoError("系列名只能使用小写英文、数字和短横线，例如 pets")
    if a.architecture not in ARCHITECTURES:
        raise logo.LogoError(f"--architecture 须为 {' / '.join(ARCHITECTURES)}")
    d = SERIES_DIR / a.id
    if d.exists():
        raise logo.LogoError(f"系列已存在：{logo.rel(d)}")
    name = a.name or a.id
    logo.write_new(d / "series.toml", (
        "# 系列元数据，由 ./logo series 命令维护，请勿手改；成员以各项目 project.toml 的 [series] 为准\n"
        f"[series]\nid = {toml_value(a.id)}\nname = {toml_value(name)}\n"
        f"architecture = {toml_value(a.architecture)}\ncreated = {toml_value(logo.today())}\n"))
    logo.write_new(d / "series.md", logo.render_template(f"series-{a.architecture}.md", id=a.id, name=name,
                                                         date=logo.today()))
    print(f"已创建系列：{logo.rel(d)}（{ARCHITECTURES[a.architecture]}）")
    print(f"下一步：按需求访谈填写 {rules_path(a.id)}，再用 ./logo new <slug> --series {a.id} --role master 创建母品牌")


def cmd_add(a) -> None:
    """已有项目加入系列（新项目请用 ./logo new --series）。只登记关系，不改动已有的色板与设计。"""
    p = logo.Project(a.slug)
    if membership(p):
        raise logo.LogoError(f"{p.slug} 已属于系列 {membership(p).get('id')}")
    master = check_join(a.id, a.role, p.slug)
    rid = master.cfg["state"]["current_release"] if master else None
    text = p.toml_path.read_text(encoding="utf-8")
    p.toml_path.write_text(text.rstrip("\n") + "\n\n" + series_section(a.id, a.role, rid), encoding="utf-8")
    kind, rules = describe(a.id, a.role)
    brief = p.dir / "brief.md"
    if brief.exists():  # brief 可随时修改：更新“项目类型与架构”两行（老项目没有这两行时跳过）
        b = brief.read_text(encoding="utf-8")
        b = re.sub(r"^- \*\*项目类型\*\*[:：].*$", f"- **项目类型**：{kind}", b, count=1, flags=re.M)
        b = re.sub(r"^- \*\*系列规则\*\*[:：].*$", f"- **系列规则**：{rules}", b, count=1, flags=re.M)
        brief.write_text(b, encoding="utf-8")
    print(f"{p.slug} 已加入系列 {a.id}（{ROLES[a.role]}）")
    if master:
        print(f"已有项目的色板不会自动替换；请按 {rules_path(a.id)} 复核与母品牌 {master.slug}（{rid}）的一致性")


def register(sub) -> None:
    s = sub.add_parser("series", help="品牌系列：创建系列、项目加入系列、查看系列与成员")
    ss = s.add_subparsers(dest="series_cmd", metavar="<系列命令>")
    s.set_defaults(fn=cmd_list)
    c = ss.add_parser("list", help="列出系列、架构与成员（默认）")
    c.set_defaults(fn=cmd_list)
    c = ss.add_parser("new", help="创建系列：series.toml 与按架构生成的系列规则 series.md")
    c.add_argument("id", help="系列名（小写英文/数字/短横线），如 pets")
    c.add_argument("--name", help="系列显示名称，可用中文")
    c.add_argument("--architecture", required=True, choices=list(ARCHITECTURES),
                   help="unified 统一型 / endorsed 背书型 / independent 独立型")
    c.set_defaults(fn=cmd_new)
    c = ss.add_parser("add", help="已有项目加入系列（新项目用 ./logo new --series）")
    c.add_argument("id", help="系列名")
    c.add_argument("slug", help="项目短名")
    c.add_argument("--role", required=True, choices=list(ROLES), help="master 母品牌 / sub 子品牌")
    c.set_defaults(fn=cmd_add)
