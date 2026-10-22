"""交接模块：让其他 Agent / 对话窗口按本系统需要的格式交付 Logo。

- brief：按项目生成给对方的说明书（需求要点、色板、SVG 硬性要求、交付格式）；--plain 用于只能对话的窗口；
- skill：生成可安装到其他 Agent 的通用 skill（不含项目信息）；
- receive：把对方回复的交接文本拆成文件放进收件箱（不直接导入，之后照常 ./logo import 预演、确认）；
- validate：只读自查（对方 Agent 可直接运行）；feedback：把问题写成对方能照做的修改意见。

交接文本格式 v1（每个文件一段，可分多条消息输出）：
    === LOGODESIGN 交接 v1 · 文件 1/2 ===
    [说明]
    source = "…"            # TOML，字段见 ./logo inbox --spec
    [文件 badge.svg]
    <svg …>…</svg>
    === 结束 1/2 ===
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import importer
import logo

VERSION = "v1"
REMINDER = f"按 LOGODesign 交接格式 {VERSION} 输出（每个文件一段，以“=== 结束 n/N ===”收尾）"
BLOCK_RE = re.compile(r"^=== LOGODESIGN 交接 v1 · 文件 (\d+)/(\d+) ===[ \t]*$(.*?)^=== 结束 \1/\2 ===[ \t]*$", re.M | re.S)
HEAD_RE = re.compile(r"^=== LOGODESIGN 交接 v1 · 文件 (\d+)/(\d+) ===", re.M)
FILE_RE = re.compile(r"^\[文件 ([a-z0-9][a-z0-9-]*\.svg)\][ \t]*$", re.M)
LAYOUTS = ("primary", "icon", "horizontal")

SVG_RULES = [
    "根元素 <svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"…\">：必须有 viewBox，不要写 width / height",
    "颜色只用项目说明书色板中的 6 位十六进制值（写法如 #1A2B3C），不要用 rgb()、颜色名等其他写法，不要用色板外的颜色",
    "不要用 <text> / <tspan>：能把文字转成路径就转；做不到就不放文字，把文字内容写在说明的 text 字段，由本系统转曲补上",
    "不要内嵌位图（<image>、data: 图片），不要 <script>、<foreignObject>、onload 等事件属性，不要任何外部链接或 @import",
    "不要 <!DOCTYPE> / <!ENTITY> 声明；不要编辑器元数据；尽量用简单的 <path> / <rect> / <circle>",
    "每个版式一份 SVG：primary（主版，必需），可选 icon（纯图形）、horizontal（横版）",
]


# ---------------------------------------------------------------------------
# 说明书与 skill
# ---------------------------------------------------------------------------
def brief_points(p: "logo.Project") -> list[str]:
    """brief.md 中已填写的字段（跳过模板占位与系统写入的项目类型行）。"""
    out = []
    for line in (p.dir / "brief.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^- \*\*(.+?)\*\*[:：]\s*(.+)$", line.strip())
        if m and not m.group(2).startswith("（") and m.group(1) not in ("系列规则", "生成目标", "确认日期", "用户确认的要点"):
            out.append(f"{m.group(1)}：{m.group(2)}")
    return out


def format_text(plain: bool, slug: str) -> list[str]:
    fields = "、".join(k for k in importer.SIDECAR_FIELDS if k not in ("project", "terms", "kind", "direction"))
    if plain:
        return [
            "请在回复中按下面的格式输出，每个 SVG 文件一段（文件多时可以分几条消息，每段都要有结束行）：",
            "```",
            f"=== LOGODESIGN 交接 {VERSION} · 文件 1/N ===",
            "[说明]",
            'source = "你的工具或模型名称"',
            'prompt = "生成这份设计用的提示词或思路"',
            'name = "英文短名，如 badge"',
            'layout = "primary"',
            'text = "标志里需要的文字（没有就留空）"',
            "[文件 badge.svg]",
            "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 200 200\">…</svg>",
            "=== 结束 1/N ===",
            "```",
            "说明部分是 TOML 格式；文件名只用小写英文、数字和短横线。不要写使用条款与授权判断，那由用户确认。",
        ]
    return [
        f"请把每个 SVG 保存到 LOGODesign 的收件箱 inbox/{slug}/<英文短名>.svg，",
        f"并在旁边写一份同名加 .import.toml 的说明（TOML，字段：{fields}）。",
        "写好后可以运行 `./logo handoff validate inbox/" + slug + "/<文件> --project " + slug + "` 自查并修正。",
        "不要自行导入或改动项目目录，导入由用户在 LOGODesign 中确认后完成。",
    ]


def cover_sections(p: "logo.Project", platforms: str | None, plain: bool) -> list[str]:
    """封面底图说明书：每个画幅的画布尺寸、共同安全区、遮挡区、Logo 区与标题区；只要底图，不画 Logo。"""
    import covers  # 延迟导入：covers.py 依赖 logo
    if not platforms:
        raise logo.LogoError("封面说明书须用 --platforms 指定目标平台（先问用户这张封面用在哪些平台）")
    targets = covers.parse_platforms(platforms)
    plats, ratios = covers.platforms_by_id(), covers.load_config()["ratios"]
    n = logo.fmt_num
    lines = ["", "## 只要底图（主视觉）", "- **不要画任何 Logo、品牌标志或商标**：Logo 由 LOGODesign 按定稿版本嵌入",
             "- 尽量不放文字（标题由 LOGODesign 用已登记字体加上）；确需文字时在说明里写明",
             "- 每个画幅一张 PNG，尺寸与画布完全一致；色调与上面的色板协调", "", "## 各画幅要求"]
    for r in covers.needed_ratios(targets):
        c = ratios[r]
        on = [plats[t] for t in targets if plats[t]["ratio"] == r]
        x, y, w, h = covers.common_safe(on, c) or (0, 0, c["width"], c["height"])
        lines += [f"### {r}：{c['width']}×{c['height']} 像素（平台：{'、'.join(pl['name'] for pl in on)}）",
                  f"- 共同安全区：x {n(x)}、y {n(y)}、宽 {n(w)}、高 {n(h)}（主体与留白都要在这里面，外面可能被裁掉）",
                  f"- Logo 区：安全区顶部靠左（或靠右），约 {n(h * 0.25)} 像素高，保持干净、对比清楚",
                  "- 标题区：安全区中部偏下，留出干净的区域"]
        occ = [covers.map_box(b, pl, covers.crop_window(pl, c)) for pl in on for b in pl.get("occlusions", [])]
        if occ:
            lines.append("- 遮挡区（头像、按钮会挡住，不要放重要内容）：" + "；".join(
                f"x {n(ox)}、y {n(oy)}、宽 {n(ow)}、高 {n(oh)}" for ox, oy, ow, oh in occ))
    lines += ["", "## 交付格式"]
    if plain:
        lines += ["每张图直接生成出来；并在回复里为每张图写一段说明（来源、提示词、对应画幅），例如：",
                  "```", 'source = "你的工具或模型名称"', 'prompt = "提示词"', 'ratio = "16x9"', "```",
                  "用户会下载原图交给 LOGODesign。不要写使用条款与授权判断，那由用户确认。"]
    else:
        lines += [f"每张图存到 LOGODesign 的收件箱 inbox/{p.slug}/<英文短名>-<画幅>.png，旁边写同名加 .import.toml 的说明",
                  "（TOML，字段：source、prompt、name、ratio、note）。不要自行导入，导入由用户确认后完成。"]
    return lines


def cmd_brief(a) -> None:
    p = logo.Project(a.slug)
    what = "封面底图" if a.type == "cover" else "Logo"
    lines = [f"# 为 LOGODesign 项目「{p.cfg['project'].get('name', p.slug)}」设计{what}",
             "", f"你生成的{what}将导入 LOGODesign 设计管理系统，请严格按以下要求交付。", "", "## 需求要点"]
    lines += [f"- {x}" for x in brief_points(p)] or ["- （项目简报尚未填写）"]
    import series  # 延迟导入：series.py 依赖 logo
    if (m := series.membership(p)):
        s = series.load_series(m["id"])
        lines += ["", f"## 品牌系列：{s.get('name', m['id'])}（{series.ARCHITECTURES.get(s.get('architecture'), '?')}，"
                      f"本项目是{series.ROLES.get(m.get('role'), '?')}）",
                  "须遵守以下系列规则："] + [f"- {r}" for r in series.checklist(m["id"])]
    if a.type == "cover":
        lines += ["", "## 品牌色板（底图色调要与之协调）"] + [f"- {k}：{v}" for k, v in p.palette.items()]
        lines += cover_sections(p, a.platforms, a.plain)
    else:
        lines += ["", "## 色板（只能用这些颜色）"] + [f"- {k}：{v}" for k, v in p.palette.items()]
        lines += ["", "## SVG 硬性要求"] + [f"{i}. {r}" for i, r in enumerate(SVG_RULES, 1)]
        lines += ["", "## 交付格式"] + format_text(a.plain, p.slug)
        lines += ["", f"若后续回复格式走样，用户会提醒：“{REMINDER}”。"]
    text = "\n".join(lines) + "\n"
    out = logo.CACHE / "handoff" / f"{p.slug}-{a.type}-brief{'-plain' if a.plain else ''}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"（已保存到 {logo.rel(out)}；说明书含项目需求与色板，发给外部工具前请确认用户同意）")
    if a.type == "logo":
        print(f"复用口令：{REMINDER}")


SKILL_TEXT = """---
name: logodesign-handoff
description: 为 LOGODesign 设计管理系统生成 Logo 时，按它要求的 SVG 规范与交接格式交付。用户提到 LOGODesign、交接格式或要把 Logo 交给 LOGODesign 时使用。
---

# LOGODesign 交接

用户会给你一份项目说明书（需求、色板、交付方式），以说明书为准；本 skill 是通用规范。

## SVG 硬性要求
{rules}

## 交付
- 能写文件时：每个 SVG 存到 LOGODesign 的 `inbox/<项目>/<英文短名>.svg`，旁边写 `<同名>.svg.import.toml`
  （TOML，字段：{fields}），可运行 `./logo handoff validate <文件> --project <项目>` 自查。
- 只能输出文字时：每个文件一段，“=== LOGODESIGN 交接 {version} · 文件 n/N ===”开头、“=== 结束 n/N ===”结尾，
  段内依次是 `[说明]`（上述 TOML 字段）与 `[文件 <英文短名>.svg]`（SVG 代码）。
- 不要写使用条款与授权判断（由用户确认），不要自行导入或改动 LOGODesign 的项目目录。
"""


def cmd_skill(a) -> None:
    dest = Path(a.dir).expanduser().resolve()
    if logo.ROOT in (dest, *dest.parents) and logo.CACHE not in (dest, *dest.parents):
        raise logo.LogoError("请把 skill 写到 LOGODesign 仓库之外（它是给其他 Agent 安装的），例如 ~/agent-skills")
    fields = "、".join(k for k in importer.SIDECAR_FIELDS if k not in ("project", "terms", "kind", "direction"))
    f = dest / "logodesign-handoff" / "SKILL.md"
    logo.write_new(f, SKILL_TEXT.format(rules="\n".join(f"{i}. {r}" for i, r in enumerate(SVG_RULES, 1)),
                                        fields=fields, version=VERSION))
    print(f"已生成：{f}（安装到其他 Agent 的 skill 目录即可；项目专属要求仍用 ./logo handoff brief 生成）")


# ---------------------------------------------------------------------------
# 交接文本：解析、截断检测
# ---------------------------------------------------------------------------
def parse_text(text: str) -> tuple[list[dict], list[str]]:
    """解析交接文本，返回 (完整的文件段, 问题)。缺段、缺结束标记都算问题。"""
    problems, blocks = [], {}
    for m in BLOCK_RE.finditer(text):
        n, total, body = int(m.group(1)), int(m.group(2)), m.group(3)
        if n in blocks:
            problems.append(f"第 {n}/{total} 段重复出现（两段的段号相同），请让对方按 1…N 依次编号后重发")
            continue
        fm = FILE_RE.search(body)
        if not fm:
            problems.append(f"第 {n}/{total} 段缺少“[文件 <英文短名>.svg]”行（文件名只能用小写英文、数字、短横线）")
            continue
        head = body[:fm.start()].replace("[说明]", "", 1)
        svg = "\n".join(ln for ln in body[fm.end():].strip().splitlines() if not ln.strip().startswith("```")).strip()
        try:
            meta = tomllib.loads("\n".join(ln for ln in head.splitlines() if not ln.strip().startswith("```")))
        except tomllib.TOMLDecodeError as e:
            problems.append(f"第 {n}/{total} 段的说明不是合法 TOML：{e}")
            continue
        blocks[n] = {"n": n, "total": total, "file": fm.group(1), "svg": svg + "\n",
                     "meta": {k: str(v) for k, v in meta.items() if k in importer.SIDECAR_FIELDS}}
    heads = [(int(m.group(1)), int(m.group(2))) for m in HEAD_RE.finditer(text)]
    if not heads:
        problems.append("没有找到交接段（应以“=== LOGODESIGN 交接 v1 · 文件 n/N ===”开头）")
        return [], problems
    totals = {t for _, t in heads}
    if len(totals) != 1:
        problems.append(f"各段的总数 N 不一致：{sorted(totals)}")
    total = max(totals)
    for n in range(1, total + 1):
        if n not in blocks and not any(f"第 {n}/" in p for p in problems):
            started = n in {h for h, _ in heads}
            problems.append(f"第 {n}/{total} 段{'没有结束标记（可能被截断）' if started else '没有收到'}，请让对方重新输出该段")
    names = [b["file"] for b in blocks.values()]
    for dup in sorted({x for x in names if names.count(x) > 1}):
        problems.append(f"文件名 {dup} 重复")
    return [blocks[k] for k in sorted(blocks)], problems


def read_input(path: str) -> str:
    f = Path(path)
    if f.is_symlink() or not f.is_file():
        raise logo.LogoError(f"文件不存在或是符号链接：{path}")
    if f.stat().st_size > importer.MAX_BYTES:
        raise logo.LogoError(f"{f.name} 超过 {importer.MAX_BYTES >> 20}MB")
    return f.read_text(encoding="utf-8", errors="replace")


def cmd_receive(a) -> None:
    """把交接文本拆成文件放进收件箱 inbox/<项目>/（原样保存 SVG 与来源说明），之后照常预演、确认、导入。"""
    p = logo.Project(a.project)
    blocks, problems = parse_text(read_input(a.text))
    if problems:
        raise logo.LogoError("交接文本不完整，未写入任何文件：\n  " + "\n  ".join(problems))
    dest = importer.INBOX / p.slug
    clash = [b["file"] for b in blocks if (dest / b["file"]).exists()]
    if clash:
        raise logo.LogoError(f"收件箱中已有同名文件：{', '.join(clash)}（先导入或移走）")
    dest.mkdir(parents=True, exist_ok=True)
    import series  # 延迟导入：series.py 依赖 logo
    for b in blocks:
        (dest / b["file"]).write_text(b["svg"], encoding="utf-8")
        meta = {**b["meta"], "project": p.slug}  # 去向由用户指定的 --project 决定，不听来源方的；条款与类型仍只是声明
        side = "".join(f"{k} = {series.toml_value(v)}\n" for k, v in meta.items())
        (dest / f"{b['file']}{importer.SIDECAR}").write_text(side, encoding="utf-8")
    print(f"已收进收件箱 {logo.rel(dest)}/：{', '.join(b['file'] for b in blocks)}")
    print("下一步：./logo import <文件> --analyze 预演，把对比图发给用户确认后再正式导入")


# ---------------------------------------------------------------------------
# 自查与修改意见
# ---------------------------------------------------------------------------
def inspect(path: str, project: str | None) -> dict:
    """只读检查：SVG 文件或交接文本；返回 {ready, files: [{file, format, hard, conflicts}], problems}。"""
    p = logo.Project(project) if project else None
    raw = Path(path)
    data = raw.read_bytes() if raw.is_file() and not raw.is_symlink() else b""
    if importer.sniff(data) == "svg":
        items, problems = [{"file": raw.name, "svg": data.decode("utf-8", errors="replace")}], []
    else:
        items, problems = parse_text(read_input(path))
    files = []
    for it in items:
        entry = {"file": it["file"], "format": [], "hard": [], "conflicts": []}
        try:
            rep = importer.analyze(p, it["svg"].encode("utf-8"), "svg", p is None, {}, False)
            entry.update(format=rep["format"], hard=rep["hard"], conflicts=rep["conflicts"] if p else [])
        except logo.LogoError as e:
            entry["hard"] = [str(e)]
        files.append(entry)
    ready = not problems and all(not f["hard"] and not f["conflicts"] for f in files)
    return {"ready": ready, "files": files, "problems": problems,
            "palette": dict(p.palette) if p else {}}


def cmd_validate(a) -> None:
    res = inspect(a.path, a.project)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        for prob in res["problems"]:
            print(f"✗ {prob}")
        for f in res["files"]:
            print(f"{f['file']}：")
            for h in f["hard"]:
                print(f"  ✗ {h}")
            if f["conflicts"]:
                print(f"  ✗ 色板外颜色：{'、'.join(f['conflicts'])}（可选：{'、'.join(res['palette'].values())}）")
            if f["format"]:
                print(f"  · 可自动整理（不影响外观）：{'；'.join(f['format'])}")
        print("可以交付" if res["ready"] else "需要修改后再交付")
    raise SystemExit(0 if res["ready"] else 1)


ADVICE = [  # 硬性问题 → 对方能照做的修改要求
    ("文字必须转为路径", "删除 <text> / <tspan>：能转成路径就转；做不到就不放文字，把文字内容写进说明的 text 字段"),
    ("禁止内嵌位图", "删除 <image> 与 data: 图片，图形全部用矢量路径画"),
    ("禁止脚本", "删除 <script>"),
    ("foreignObject", "删除 <foreignObject>"),
    ("事件属性", "删除 onload / onclick 等事件属性"),
    ("外部", "删除所有外部链接（href 只能指向本文件内的 #id）与 @import / url(http…)"),
    ("DOCTYPE", "删除 <!DOCTYPE> 与 <!ENTITY> 声明"),
    ("外观与原件不一致", "去掉 width / height，只保留 viewBox，并确认图形在 viewBox 内比例正确"),
    ("颜色", "颜色只用 6 位十六进制写法（如 #1A2B3C），不用 rgb()、颜色名或其他写法"),
    ("XML 解析失败", "SVG 不是合法的 XML，请检查标签是否闭合、属性引号是否成对"),
]


def cmd_feedback(a) -> None:
    """把检查出的硬性问题（以及用户决定“请对方改”的颜色）写成一段可直接粘贴给对方的修改意见。"""
    res = inspect(a.path, a.project)
    bad = [c for c in (a.colors or []) if not re.fullmatch(r"#[0-9A-Fa-f]{6}=[a-z][a-z0-9_]*", c.strip())]
    if bad:
        raise logo.LogoError(f"--color 写法为 #RRGGBB=色板色名（如 #FF6600=primary），收到：{', '.join(bad)}")
    fixes = importer.parse_resolves([f"{c.split('=')[0]}=map:{c.split('=')[1]}" for c in (a.colors or [])])
    pal = logo.Project(a.project).palette if a.project else {}
    seen = {c for f in res["files"] for c in f["conflicts"]}
    stray = [c for c in fixes if c not in seen]
    if stray:
        raise logo.LogoError(f"这些颜色不在任何文件的色板外颜色中：{', '.join(stray)}（须指定 --project 并以检查结果为准）")
    lines = ["请按以下要求修改后，再按原来的交接格式重新输出（只改列出的问题，其余保持不变）："]
    for prob in res["problems"]:
        lines.append(f"- {prob}")
    for f in res["files"]:
        asks = []
        for h in f["hard"]:
            asks.append(next((adv for key, adv in ADVICE if key in h), h))
        for color, (_, name) in fixes.items():
            if color in f["conflicts"]:
                if name not in pal:
                    raise logo.LogoError(f"{name} 不是色板中的色名（可选：{', '.join(pal)}）")
                asks.append(f"把颜色 {color} 改为 {pal[name]}")
        for ask in dict.fromkeys(asks):
            lines.append(f"- {f['file']}：{ask}")
    if len(lines) == 1:
        print("没有需要对方修改的硬性问题。色板外颜色是否让对方改，由用户决定（用 --color #RRGGBB=色名 加入修改意见）。")
        return
    lines.append(f"- 输出格式：{REMINDER}")
    print("\n".join(lines))


def register(sub) -> None:
    s = sub.add_parser("handoff", help="与其他 Agent 交接：生成说明书 / skill、收取交接文本、自查与修改意见")
    hs = s.add_subparsers(dest="handoff_cmd", required=True, metavar="<交接命令>")
    c = hs.add_parser("brief", help="按项目生成给其他 Agent 的说明书（需求、色板、SVG 要求、交付格式）")
    c.add_argument("slug")
    c.add_argument("--plain", action="store_true", help="对方只能对话（不能写文件）：用交接文本格式回复")
    c.add_argument("--type", choices=["logo", "cover"], default="logo", help="logo（默认）或 cover（只要底图，不画 Logo）")
    c.add_argument("--platforms", help="--type cover 时的目标平台，逗号分隔")
    c.set_defaults(fn=cmd_brief)
    c = hs.add_parser("skill", help="生成可安装到其他 Agent 的通用 skill（写到仓库之外的目录）")
    c.add_argument("dir", help="输出目录，如 ~/agent-skills")
    c.set_defaults(fn=cmd_skill)
    c = hs.add_parser("receive", help="把对方回复的交接文本拆成文件放进收件箱（不直接导入）")
    c.add_argument("text", help="保存了交接文本的文件")
    c.add_argument("--project", required=True, help="要加入的项目")
    c.set_defaults(fn=cmd_receive)
    c = hs.add_parser("validate", help="只读自查：SVG 文件或交接文本是否可以交付（对方 Agent 可直接运行）")
    c.add_argument("path", help="SVG 文件或交接文本文件")
    c.add_argument("--project", help="按该项目的色板检查颜色")
    c.add_argument("--json", action="store_true", help="以 JSON 输出")
    c.set_defaults(fn=cmd_validate)
    c = hs.add_parser("feedback", help="把问题写成可粘贴给对方的修改意见（色板外颜色须用户决定后用 --color 加入）")
    c.add_argument("path", help="SVG 文件或交接文本文件")
    c.add_argument("--project", help="按该项目的色板检查")
    c.add_argument("--color", dest="colors", action="append", metavar="#RRGGBB=色名",
                   help="用户决定请对方把这个颜色改成色板中的某色")
    c.set_defaults(fn=cmd_feedback)
