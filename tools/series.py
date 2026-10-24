"""系列模块：多个 Logo 项目组成的品牌系列（母品牌与子品牌）。

系列记录在项目仓库的 projects/_series/<系列名>/：series.toml（名称、架构，由命令维护）与 series.md（系列规则）。
成员关系以各项目 project.toml 的 [series] 为准（由 ./logo new --series / ./logo series add 写入），不在系列记录里重复登记。
"""
from __future__ import annotations

import json
import re
import tempfile
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


def skeleton_of(sid: str) -> str | None:
    """系列登记的封面骨架（“项目/封面目录”），没有时为 None。"""
    try:
        return logo.load_toml(SERIES_DIR / sid / "series.toml").get("covers", {}).get("skeleton") or None
    except logo.LogoError:
        return None


def membership(p: "logo.Project") -> dict | None:
    try:
        return p.cfg.get("series") or None
    except logo.LogoError:
        return None  # project.toml 无法解析：由 check_project 报告，这里不中断系列检查与看板


def members(sid: str) -> list[tuple["logo.Project", str]]:
    """系列成员与角色；角色缺失时记为 “?”（由 check_series 报错，这里不中断看板与列表）。"""
    return [(p, m.get("role") or "?") for p in logo.all_projects() if (m := membership(p)) and m.get("id") == sid]


def master_of(sid: str) -> "logo.Project | None":
    return next((p for p, role in members(sid) if role == "master"), None)


def rules_holes(p: "logo.Project") -> list[str]:
    """系列成员开始设计前，系列规则 series.md 须已填完（系列层面的需求访谈）；返回未填写的占位，非成员为空。"""
    m = membership(p)
    if not m or not m.get("id"):
        return []
    md = SERIES_DIR / m["id"] / "series.md"
    if not md.exists():
        return [f"（缺少 {rules_path(m['id'])}）"]
    return logo.unfilled_placeholders(rules_path(m["id"]), md.read_text(encoding="utf-8"))


def skeleton_problem(sid: str, ref: str) -> str | None:
    """登记的封面骨架是否仍然有效：封面目录存在、所属项目仍是该系列成员。"""
    import covers  # 延迟导入：covers.py 依赖 logo
    slug, _, cover = ref.partition("/")
    if not covers.COVER_DIR_RE.match(cover):
        return f"系列 {sid} 的封面骨架记录 {ref!r} 格式应为 项目/NNN-名称"
    if not covers.skeleton_dir(ref).is_dir():
        return f"系列 {sid} 登记的封面骨架 {ref} 不存在"
    try:
        if (membership(logo.Project(slug)) or {}).get("id") != sid:
            return f"系列 {sid} 的封面骨架 {ref} 所属项目 {slug} 已不是系列成员"
    except logo.LogoError:
        return f"系列 {sid} 的封面骨架 {ref} 所属项目 {slug} 不存在"
    return None


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
# 系列评审：并排对比、评审清单、评审记录与子品牌发布门槛
# ---------------------------------------------------------------------------
REVIEW_RESULTS = {"pass": "通过", "revise": "需修改"}
REVIEW_HEAD_RE = re.compile(r"^### (\d{4}-\d{2}-\d{2}) 系列评审：(通过|需修改)\s*$")
REVIEW_WITH_RE = re.compile(r"^- \*\*对比\*\*：(.*)$")
RULE_LINE_RE = re.compile(r"^- \*\*(.+?)\*\*[:：]\s*(.*)$")
PROCESS_SECTIONS = {"改版跟进", "系列封面", "家族封面"}  # 流程约定，不是设计评审项，不进评审清单


def default_ref(p: "logo.Project") -> str | None:
    """对比时默认取的版本：当前发布 → 选定版本 → 各方向中最后新建的版本。"""
    st = p.cfg["state"]
    if st.get("current_release"):
        return st["current_release"]
    if st.get("selected_version"):
        return st["selected_version"]
    latest = [vid for d in p.directions() if (vid := p.latest_version_id(d))]
    return max(latest, key=lambda v: (int(v.split("-v")[1]), v)) if latest else None


def ref_text(p: "logo.Project", ref: str, mode: str) -> str:
    """成员某个版本（版本号或发布号）在某配色下的 SVG 文本；发布取主版。"""
    if logo.RELEASE_RE.fullmatch(ref):
        ldir = p.release_dir(ref) / "layouts"
        if not (ldir / "primary.svg").exists():
            raise logo.LogoError(f"{p.slug} 的 {ref} 缺少 primary.svg")
        return logo.layout_mode_text(p, ldir, "primary", mode)
    return logo.recolor_svg(p.version_dir(ref) / "logo.svg", p.mode_map(mode))


def compare_set(sid: str, with_: list[str] | None) -> list[tuple["logo.Project", str, str]]:
    """参与对比的 [(项目, 角色, 版本)]：母品牌在前；--with slug=版本 覆盖默认版本。没有版本的成员跳过。"""
    load_series(sid)
    ms = sorted(members(sid), key=lambda x: (x[1] != "master", x[0].slug))
    picks: dict[str, str] = {}
    for item in with_ or []:
        slug, _, ref = item.partition("=")
        if not ref:
            raise logo.LogoError(f"--with 写法为 项目=版本，如 food=A-v003 或 brand=r2，收到：{item}")
        picks[slug] = ref
    unknown = [s for s in picks if s not in {p.slug for p, _ in ms}]
    if unknown:
        raise logo.LogoError(f"不是系列 {sid} 的成员：{', '.join(unknown)}")
    out = []
    for p, role in ms:
        ref = picks.get(p.slug) or default_ref(p)
        if not ref:
            continue
        if logo.RELEASE_RE.fullmatch(ref):
            p.release_dir(ref)
        else:
            p.version_dir(ref)  # 校验存在
        out.append((p, role, ref))
    if not out:
        raise logo.LogoError(f"系列 {sid} 还没有可对比的成员版本")
    return out


def rules_lines(sid: str) -> list[str]:
    """series.md 的各行；文件缺失时为空（缺失由 check_series 报错，这里不中断检查与命令）。"""
    md = SERIES_DIR / sid / "series.md"
    return md.read_text(encoding="utf-8").splitlines() if md.exists() else []


def checklist(sid: str) -> list[str]:
    """评审清单：series.md 中评审追加区之前的规则条目（字段与段落），供 Agent 逐条向用户提问。"""
    out, section = [], ""
    for line in rules_lines(sid):
        s = line.strip()
        if s.startswith("## "):
            section = s[3:]
            if "追加" in section:
                break
            continue
        if not s or s.startswith(("#", ">")) or not section or section in PROCESS_SECTIONS:  # 首个 ## 之前是标题与元数据
            continue
        m = RULE_LINE_RE.match(s)
        out.append(f"{section} · {m.group(1)}：{m.group(2)}" if m else f"{section}：{s}")
    return out


def cmd_compare(a) -> None:
    items = compare_set(a.id, a.with_)
    out = logo.CACHE / "_series" / a.id / "compare.png"
    with tempfile.TemporaryDirectory() as td:
        cells = []
        for row, (mode, size) in enumerate((("color", 300), ("mono", 300), ("color", 32))):
            for i, (p, role, ref) in enumerate(items):
                png = logo.render_text(ref_text(p, ref, mode), Path(td) / f"{row}-{i}.png", size)
                if size < 100:  # 小尺寸放大显示，看清像素级效果
                    logo.run(["magick", str(png), "-filter", "point", "-resize", "800%", str(png)])
                cell = Path(td) / f"cell-{row}-{i}.png"
                logo.run(["magick", str(png), "-background", "white", "-gravity", "center", "-extent", "320x320",
                          str(cell)])
                cells += ["-label", f"{p.slug} · {role} · {ref} · {'32px' if size < 100 else mode}", str(cell)]
        out.parent.mkdir(parents=True, exist_ok=True)
        logo.run(["magick", "montage", *cells, "-tile", f"{len(items)}x3", "-geometry", "+8+8",
                  "-background", "#DDDDDD", str(out)])
    print(f"系列对比图：{logo.rel(out)}")
    print("每列一个成员：" + "、".join(f"{p.slug}（{ROLES.get(role, role)}，{ref}）" for p, role, ref in items))
    print("三行依次为：彩色、单色（看结构是否一致）、32px 放大（看小尺寸下是否还像一家）")
    rules = checklist(a.id)
    if rules:
        print(f"\n评审清单（来自 {rules_path(a.id)}，请逐条向用户确认）：")
        for i, r in enumerate(rules, 1):
            print(f"  {i}. {r}")
    with_ = " ".join(f"--with {p.slug}={ref}" for p, _, ref in items)
    print(f"\n用户给出结论后记录：{logo.cli_prefix()} series review {a.id} --result pass|revise --note \"用户意见\" {with_}")


def reviews(sid: str) -> list[dict]:
    """series.md 中的评审记录（按出现顺序）：[{date, result, pairs: {(slug, 版本)}}]。"""
    out: list[dict] = []
    for line in rules_lines(sid):
        if (m := REVIEW_HEAD_RE.match(line)):
            out.append({"date": m.group(1), "result": m.group(2), "pairs": set()})
        elif out and (m := REVIEW_WITH_RE.match(line)):
            out[-1]["pairs"] |= {tuple(x.split(" ", 1)) for x in m.group(1).split("、") if " " in x}
    return out


def review_passed(sid: str, slug: str, vid: str) -> bool:
    """该成员版本最近一次参与的系列评审结论为“通过”。"""
    hits = [r for r in reviews(sid) if (slug, vid) in r["pairs"]]
    return bool(hits) and hits[-1]["result"] == "通过"


def release_gate(p: "logo.Project", vid: str) -> None:
    """统一型 / 背书型子品牌以版本定稿发布前，须有该版本通过的系列评审（先给用户看对比、听用户结论）。"""
    m = membership(p)
    if not m or m.get("role") != "sub" or load_series(m["id"]).get("architecture") not in INHERITING:
        return
    if not review_passed(m["id"], p.slug, vid):
        raise logo.LogoError(
            f"{p.slug} 是系列 {m['id']} 的子品牌，发布 {vid} 前须先做系列评审：./logo series compare {m['id']} "
            f"--with {p.slug}={vid}，按清单向用户逐条确认，用户通过后 ./logo series review {m['id']} --result pass "
            f"--note \"用户意见\" --with {p.slug}={vid}")


def cmd_review(a) -> None:
    items = compare_set(a.id, a.with_)
    note = " ".join(a.note.split())  # 合并为一行，保持记录格式固定
    if not note:
        raise logo.LogoError("--note 须写明用户的意见（原话或要点）")
    md = SERIES_DIR / a.id / "series.md"
    if not md.exists():
        raise logo.LogoError(f"缺少系列规则 {rules_path(a.id)}，无法记录评审")
    body = md.read_text(encoding="utf-8").rstrip("\n")
    record = (f"\n\n### {logo.today()} 系列评审：{REVIEW_RESULTS[a.result]}\n"
              f"- **对比**：{'、'.join(f'{p.slug} {ref}' for p, _, ref in items)}\n"
              f"- **用户意见**：{note}\n")
    md.write_text(body + record, encoding="utf-8")
    print(f"已记录系列评审（{REVIEW_RESULTS[a.result]}）→ {rules_path(a.id)}")
    if a.result == "revise":
        print("结论为需修改：按用户意见出新版本后重新对比与评审")


# ---------------------------------------------------------------------------
# 改版跟进：母品牌出新发布后，逐个子品牌征求用户意见并同步色板
# ---------------------------------------------------------------------------
def palette_diff(sub: "logo.Project", master: "logo.Project") -> tuple[dict, dict]:
    """母品牌色板相对子品牌的变化：(改了值的 {色名: (旧, 新)}, 新增的 {色名: 值})。子品牌已有相同值的不算变化。"""
    mine, theirs = sub.palette, master.palette
    changed = {k: (mine[k], v) for k, v in theirs.items() if k in mine and mine[k] != v}
    added = {k: v for k, v in theirs.items() if k not in mine}
    return changed, added


def diff_text(changed: dict, added: dict) -> str:
    parts = [f"{k} {a}→{b}" for k, (a, b) in changed.items()] + [f"新增 {k} {v}" for k, v in added.items()]
    return "、".join(parts) or "色板无变化"


def pending_update(p: "logo.Project") -> tuple[str, "logo.Project", str, str] | None:
    """继承型子品牌是否落后于母品牌：(系列名, 母品牌, 子品牌依据的发布, 母品牌当前发布)；不需要跟进时为 None。"""
    m = membership(p)
    if not m or m.get("role") != "sub":
        return None
    try:
        arch = load_series(m["id"]).get("architecture")
    except logo.LogoError:
        return None
    master = master_of(m["id"])
    if arch not in INHERITING or not master:
        return None
    latest = master.cfg["state"].get("current_release")
    based = m.get("master_release")
    return (m["id"], master, based, latest) if latest and based != latest else None


def palette_users(p: "logo.Project") -> set[str]:
    """子品牌受色板约束的 SVG（各版本与发布版式）中用到的颜色。"""
    files = [v / "logo.svg" for d in p.directions() for v in p.versions_in(d)]
    files += [f for rd in p.releases() for f in (rd / "layouts").glob("*.svg")]
    return {c.upper() for f in files if f.exists() for c in logo.HEX_IN_TEXT_RE.findall(f.read_text(encoding="utf-8"))}


def toml_set(text: str, table: str, key: str, literal: str) -> str:
    """在 TOML 文本的 [table] 中设置 key（已有则替换该行，否则加到表末尾；表不存在则新建），保留其余内容与注释。"""
    m = re.search(rf"^\[{re.escape(table)}\]\s*$", text, re.M)
    line = f"{key} = {literal}"
    if not m:
        return text.rstrip("\n") + f"\n\n[{table}]\n{line}\n"
    start = m.end()
    nxt = re.search(r"^\[", text[start:], re.M)
    end = start + nxt.start() if nxt else len(text)
    section = text[start:end]
    pat = re.compile(rf"^{re.escape(key)}\s*=.*$", re.M)
    if pat.search(section):
        section = pat.sub(lambda _: line, section, count=1)
    else:
        body = section.rstrip("\n")
        section = body + "\n" + line + "\n" + ("\n" if nxt else "")
    return text[:start] + section + text[end:]


def cmd_sync(a) -> None:
    p = logo.Project(a.slug)
    m = membership(p)
    if not m or m.get("role") != "sub":
        raise logo.LogoError(f"{p.slug} 不是系列子品牌")
    if load_series(m["id"]).get("architecture") not in INHERITING:
        raise logo.LogoError("独立型系列的子品牌不继承母品牌，无需跟进")
    master = master_of(m["id"])
    latest = master.cfg["state"].get("current_release") if master else None
    if not latest:
        raise logo.LogoError(f"系列 {m['id']} 的母品牌还没有发布")
    note = " ".join(a.note.split())
    if not note:
        raise logo.LogoError("--note 须写明用户的决定（跟随哪些颜色、是否需要改版）")
    changed, added = palette_diff(p, master)
    offered = {**{k: v[1] for k, v in changed.items()}, **added}
    if m.get("master_release") == latest and not offered:
        print(f"{p.slug} 已跟进母品牌 {master.slug} {latest}，色板一致，无需同步")
        return
    if a.palette == "all":
        keys = list(offered)
    elif a.palette == "none":
        keys = []
    else:
        keys = [k.strip() for k in a.palette.split(",") if k.strip()]
        bad = [k for k in keys if k not in offered]
        if bad:
            raise logo.LogoError(f"不是母品牌有变化的颜色：{', '.join(bad)}（可选：{', '.join(offered) or '无'}）")
    text = p.toml_path.read_text(encoding="utf-8")
    cfg_modes = p.cfg.get("color_modes", {})
    master_modes = master.cfg.get("color_modes", {})
    used, palette = palette_users(p), p.palette
    based = m.get("master_release") or "旧版"
    report = []
    for k in keys:
        new = offered[k]
        if k in changed and changed[k][0] in used:  # 旧值仍被冻结的设计使用：换个名字保留下来
            old, legacy, n = changed[k][0], f"{k}_{based}", 2
            while legacy in palette and palette[legacy] != old:
                legacy, n = f"{k}_{based}_{n}", n + 1
            text = toml_set(text, "palette", legacy, toml_value(old))
            for mode, table in cfg_modes.items():
                if isinstance(table, dict) and k in table:
                    text = toml_set(text, f"color_modes.{mode}", legacy, toml_value(table[k]))
            report.append(f"{k} {old}→{new}（旧值仍被已有设计使用，保留为 {legacy}）")
        else:
            report.append(f"{k} {changed[k][0]}→{new}" if k in changed else f"新增 {k} {new}")
        text = toml_set(text, "palette", k, toml_value(new))
        for mode, table in master_modes.items():
            if isinstance(table, dict) and k in table:
                text = toml_set(text, f"color_modes.{mode}", k, toml_value(table[k]))
    text = toml_set(text, "series", "master_release",
                    toml_value(latest) + "  # 继承母品牌色板与起稿所依据的发布")
    p.toml_path.write_text(text, encoding="utf-8")
    skipped = [k for k in offered if k not in keys]
    md = SERIES_DIR / m["id"] / "series.md"
    if md.exists():
        md.write_text(md.read_text(encoding="utf-8").rstrip("\n") + (
            f"\n\n### {logo.today()} 改版跟进：{p.slug} {based} → {master.slug} {latest}\n"
            f"- **色板**：{'、'.join(report) or '不改'}" + (f"；未跟随：{'、'.join(skipped)}" if skipped else "") + "\n"
            f"- **用户意见**：{note}\n"), encoding="utf-8")
    print(f"{p.slug} 已跟进母品牌 {master.slug} {latest}：" + ("；".join(report) or "色板不改"))
    if skipped:
        print(f"未跟随：{'、'.join(skipped)}")
    print(f"如母品牌的图形也有调整、子品牌需要改版：出新版本后用 {logo.cli_prefix()} series compare 与用户重新评审")


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
        if (ref := s and logo.load_toml(d / "series.toml").get("covers", {}).get("skeleton")):
            if (problem := skeleton_problem(d.name, ref)):
                errors.append(f"{where}/series.toml: {problem}（重新 ./logo series skeleton 登记）")
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
        cur = p.cfg["state"].get("current_release")
        try:
            root = p.release_root_version(cur) if cur else None
        except logo.LogoError:
            root = None  # 追溯链问题由 check_project 报告
        if root and not review_passed(m["id"], p.slug, root):
            warnings.append(f"{logo.rel(p.dir)}: 当前发布 {cur}（{root}）没有通过的系列评审记录；"
                            f"可 ./logo series compare {m['id']} 后与用户补做评审")
        if (up := pending_update(p)):
            _, master, based, latest = up
            warnings.append(f"{logo.rel(p.dir)}: 母品牌 {master.slug} 已发布 {latest}（子品牌依据 {based}），"
                            f"{diff_text(*palette_diff(p, master))}；请向用户确认是否跟进：./logo series sync {p.slug} "
                            "--palette all|none|色名,… --note \"用户的决定\"")


def index_groups() -> list[tuple[str, list[tuple["logo.Project", str, str]]]]:
    """看板分组：[(系列标题, [(项目, 角色名, 母品牌发布)])]，母品牌在前；子品牌落后于母品牌当前发布时标出最新。"""
    out = []
    for sid, s in all_series().items():
        ms = sorted(members(sid), key=lambda x: (x[1] != "master", x[0].slug))
        master = next((p for p, role in ms if role == "master"), None)
        latest = master.cfg["state"].get("current_release") if master else None
        rows = []
        for p, role in ms:
            based = (membership(p) or {}).get("master_release") or ""
            shown = "—" if not based else based if based == latest else f"{based}（最新 {latest or '—'}）"
            rows.append((p, ROLES.get(role, role), shown))
        title = f"系列：{s.get('name', sid)}（{sid}，{ARCHITECTURES.get(s.get('architecture'), '?')}）"
        out.append((title, rows))
    return out


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------
def cmd_list(a) -> None:
    found = all_series()
    if not found:
        print(f"还没有系列（{logo.cli_prefix()} series new <系列名> --name 名称 --architecture unified|endorsed|independent）")
        return
    for sid, s in found.items():
        print(f"{sid}  {s.get('name', '')}  [{ARCHITECTURES.get(s.get('architecture'), '?')}]  规则：{rules_path(sid)}"
              + (f"  封面骨架：{sk}" if (sk := skeleton_of(sid)) else ""))
        ms = members(sid)
        for p, role in sorted(ms, key=lambda x: (x[1] != "master", x[0].slug)):
            extra = f"（基于母品牌 {membership(p).get('master_release')}）" if membership(p).get("master_release") else ""
            print(f"  {ROLES.get(role, role)}  {p.slug}  {p.cfg['project'].get('name', '')}{extra}")
        if not ms:
            print("  （还没有成员）")


def cmd_new(a) -> None:
    import repositories
    if repositories.kind(logo.PROJECTS) == "single":
        raise logo.LogoError("single 仓库只管理独立品牌；系列请放在 workspace 仓库")
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
    print(f"下一步：按需求访谈填写 {rules_path(a.id)}，再用 {logo.cli_prefix()} new <slug> --series {a.id} --role master 创建母品牌")


def cmd_skeleton(a) -> None:
    """登记系列封面骨架：系列成员的一张通过检查的封面；成员立项时可套用（cover new --from-skeleton）。"""
    import covers  # 延迟导入：covers.py 依赖 logo
    load_series(a.id)
    p = logo.Project(a.slug)
    if (membership(p) or {}).get("id") != a.id:
        raise logo.LogoError(f"{p.slug} 不是系列 {a.id} 的成员，不能作为系列封面骨架")
    cdir = covers.cover_dir(p, a.cover)
    errors: list[str] = []
    covers.check_cover(p, cdir, errors, [])
    if errors:
        raise logo.LogoError("骨架封面未通过检查：\n  " + "\n  ".join(errors))
    note = " ".join(a.note.split())
    if not note:
        raise logo.LogoError("--note 须写明用户的意见（为什么以这张为骨架）")
    ref = f"{p.slug}/{cdir.name}"
    path = SERIES_DIR / a.id / "series.toml"
    path.write_text(toml_set(path.read_text(encoding="utf-8"), "covers", "skeleton", toml_value(ref)), encoding="utf-8")
    ratios = "、".join(s.stem for s in sorted(cdir.glob("*.svg")))
    md = SERIES_DIR / a.id / "series.md"
    if md.exists():
        md.write_text(md.read_text(encoding="utf-8").rstrip("\n") + (
            f"\n\n### {logo.today()} 封面骨架：{ref}\n- **画幅**：{ratios}\n- **用户意见**：{note}\n"), encoding="utf-8")
    print(f"已登记系列 {a.id} 的封面骨架：{ref}（画幅：{ratios}）")
    print(f"成员立项封面时须先问用户是否套用：{logo.cli_prefix()} cover new <项目> <名称> --platforms … --from-skeleton | --no-skeleton")


def cmd_add(a) -> None:
    """已有项目加入系列（新项目请用 ./logo new --series）。只登记关系，不改动已有的色板与设计。"""
    import repositories
    if repositories.kind(logo.PROJECTS) == "single":
        raise logo.LogoError("single 仓库只管理独立品牌；系列请放在 workspace 仓库")
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
    c = ss.add_parser("compare", help="系列对比图（彩色/单色/小尺寸）与评审清单")
    c.add_argument("id", help="系列名")
    c.add_argument("--with", dest="with_", action="append", metavar="项目=版本",
                   help="指定某成员参与对比的版本，如 food=A-v003（默认：当前发布 → 选定版本 → 最新版本），可重复")
    c.set_defaults(fn=cmd_compare)
    c = ss.add_parser("review", help="记录系列评审结论（用户的意见），追加到 series.md")
    c.add_argument("id", help="系列名")
    c.add_argument("--result", required=True, choices=list(REVIEW_RESULTS), help="pass 通过 / revise 需修改")
    c.add_argument("--note", required=True, help="用户的意见（原话或要点）")
    c.add_argument("--with", dest="with_", action="append", metavar="项目=版本", help="同 compare")
    c.set_defaults(fn=cmd_review)
    c = ss.add_parser("sync", help="子品牌跟进母品牌新发布：按用户决定同步色板并记录到 series.md")
    c.add_argument("slug", help="子品牌项目")
    c.add_argument("--palette", required=True, help="all 全部跟随 / none 不改色（仅确认已知悉）/ 逗号分隔的色名")
    c.add_argument("--note", required=True, help="用户的决定（原话或要点）")
    c.set_defaults(fn=cmd_sync)
    c = ss.add_parser("skeleton", help="登记系列封面骨架：成员立项封面时可套用")
    c.add_argument("id", help="系列名")
    c.add_argument("--from", dest="slug", required=True, help="提供骨架的成员项目")
    c.add_argument("cover", help="该项目的封面编号，如 001")
    c.add_argument("--note", required=True, help="用户的意见（为什么以这张为骨架）")
    c.set_defaults(fn=cmd_skeleton)
    c = ss.add_parser("add", help="已有项目加入系列（新项目用 ./logo new --series）")
    c.add_argument("id", help="系列名")
    c.add_argument("slug", help="项目短名")
    c.add_argument("--role", required=True, choices=list(ROLES), help="master 母品牌 / sub 子品牌")
    c.set_defaults(fn=cmd_add)
