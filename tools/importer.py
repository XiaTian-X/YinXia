"""导入模块：把其他地方（其他 Agent、设计工具）生成的 Logo 加入本系统。

原则：导入默认原样保留，不主动修改。
- 外观不变的格式整理（去写死的宽高、编辑器元数据、CSS 类展开、颜色写法统一）可以自动做，但须逐像素核对外观一致；
- 系统硬性规则（脚本、外链、内嵌位图、未转曲文字等）项目规范也改不了：只能登记为概念图，或请来源修正后重交；
- 与项目规范冲突（颜色不在色板）逐项由用户决定：改图片（换成色板色）还是改项目规范（加入色板），并留下决策记录。
以导入的图初始化新项目时没有冲突，只是用它补全项目信息（色板）。

入口：文件路径，或系统根目录的收件箱 inbox/（不进任何仓库）；旁边可放来源说明 <文件>.import.toml。
正式导入前须先用 --analyze 预演（生成对比图给用户看），参数与文件不变时才能导入。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

import logo

INBOX = logo.INBOX
IMPORTED = logo.CACHE / "imported"
PREVIEWS = logo.CACHE / "import"
SIDECAR = ".import.toml"
MAX_BYTES = 5 << 20
INBOX_STALE_DAYS = 7
KINDS = {"candidate": "候选", "final": "成品"}
UNCONFIRMED = "未确认"
UNKNOWN_TERMS = {"", "不清楚", "未知", "unknown", "未确认"}
SIDECAR_FIELDS = {
    "source": "来源：哪个工具 / Agent / 模型",
    "prompt": "生成时的提示词",
    "terms": "使用条款或版权（能否商用、能否注册商标；不清楚写“不清楚”，导入时仍须用户确认）",
    "kind": "candidate（候选）或 final（成品）",
    "project": "要加入的项目短名（新项目先完成需求访谈）",
    "direction": "要放入的方向字母，如 A",
    "name": "英文短名，用于概念图与记录",
    "layout": "这份 SVG 是哪个版式：primary（主版）/ icon（纯图形）/ horizontal（横版）",
    "text": "标志里需要的文字内容（对方无法转曲时不放文字，由本系统转曲补上）",
    "ratio": "封面图对应的画幅，如 16x9、3x1（见 ./logo cover platforms）",
    "note": "补充说明（只作资料，不当作指令）",
}
ENTITY_RE = re.compile(r"<!DOCTYPE|<!ENTITY", re.I)
SAFE_DATA_RE = re.compile(r"^data:image/(png|jpe?g|webp|gif)[;,]", re.I)  # 内嵌位图可以渲染；内嵌 SVG 里还能再引用本机文件
CSS_URL_RE = re.compile(r"url\(\s*['\"]?(?!#)[^)]*\)", re.I)
CSS_IMPORT_RE = re.compile(r"@import[^;]*;?", re.I)
MAX_SIDE = 10000                 # 位图单边像素上限（只读文件头判断，不解码像素）
MAX_PIXELS = 60_000_000          # 位图总像素上限，防止“解压炸弹”拖垮机器
MAGICK_LIMITS = ["-limit", "memory", "1GiB", "-limit", "disk", "2GiB"]
PREVIEW_DAYS = 1                 # 预演超过 1 天作废，须重新预演给用户看
NAMED = {"black": "#000000", "white": "#FFFFFF", "red": "#FF0000", "green": "#008000", "blue": "#0000FF",
         "yellow": "#FFFF00", "orange": "#FFA500", "purple": "#800080", "gray": "#808080", "grey": "#808080",
         "silver": "#C0C0C0", "navy": "#000080", "teal": "#008080", "maroon": "#800000", "lime": "#00FF00",
         "aqua": "#00FFFF", "fuchsia": "#FF00FF", "olive": "#808000", "pink": "#FFC0CB", "brown": "#A52A2A"}
PALETTE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
RESOLVE_RE = re.compile(r"^(#[0-9A-Fa-f]{6})=(palette|map):([a-z][a-z0-9_]*)$")


# ---------------------------------------------------------------------------
# 读入与识别
# ---------------------------------------------------------------------------
def sniff(data: bytes) -> str | None:
    """按内容（而非扩展名）识别文件类型。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    head = data[:4096].decode("utf-8", errors="ignore").lstrip("﻿ \t\r\n")
    if head.startswith("<") and "<svg" in data[:65536].decode("utf-8", errors="ignore"):
        return "svg"
    return None


def read_source(path: Path) -> tuple[bytes, str]:
    """读取外部文件（视为不可信）：不跟随符号链接、限制大小、按内容识别类型。"""
    if path.is_symlink():
        raise logo.LogoError(f"{path} 是符号链接，请提供真实文件")
    if not path.is_file():
        raise logo.LogoError(f"文件不存在：{path}")
    if path.stat().st_size > MAX_BYTES:
        raise logo.LogoError(f"{path.name} 超过 {MAX_BYTES >> 20}MB，Logo 不应这么大，请确认文件")
    data = path.read_bytes()
    kind = sniff(data)
    if not kind:
        raise logo.LogoError(f"{path.name} 不是可识别的 SVG / PNG / JPEG / WebP（按文件内容判断）")
    if kind != "svg":
        try:  # 只读文件头取尺寸，不解码像素
            dims = logo.run(["magick", "identify", *MAGICK_LIMITS, "-ping", "-format", "%w %h", f"{path}[0]"], capture=True)
            w, h = (int(v) for v in dims.split()[:2])
        except (logo.LogoError, ValueError):
            raise logo.LogoError(f"{path.name} 无法读取图片尺寸（文件可能已损坏），拒绝处理")
        if max(w, h) > MAX_SIDE or w * h > MAX_PIXELS:
            raise logo.LogoError(f"{path.name} 声明的尺寸 {w}×{h} 过大（上限单边 {MAX_SIDE}、总计 {MAX_PIXELS // 1_000_000} 百万像素），拒绝处理")
    return data, kind


def sidecar(path: Path) -> dict[str, str]:
    """来源说明 <文件>.import.toml（其他 Agent 可随文件一起交付）；只取已知字段，内容只作资料。"""
    side = path.with_name(path.name + SIDECAR)
    if not side.exists():
        return {}
    try:
        data = tomllib.loads(side.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise logo.LogoError(f"{side.name} 格式错误：{e}")
    return {k: str(v) for k, v in data.items() if k in SIDECAR_FIELDS}


def inbox_items() -> list[dict]:
    """收件箱中的待导入文件：inbox/ 根目录（去向待定）与 inbox/<项目>/（加入该项目）。"""
    items = []
    for f in sorted(INBOX.rglob("*")) if INBOX.exists() else []:
        rel_parts = f.relative_to(INBOX).parts
        if not f.is_file() or f.name.endswith(SIDECAR) or any(x.startswith(".") for x in rel_parts) or len(rel_parts) > 2:
            continue
        info = {"file": f.relative_to(INBOX).as_posix(), "bytes": f.stat().st_size,
                "age_days": (dt.date.today() - dt.date.fromtimestamp(f.stat().st_mtime)).days}
        try:
            info["type"] = sniff(f.read_bytes()[:65536]) or "unknown"
            meta = sidecar(f)
        except (OSError, logo.LogoError) as e:
            info["type"], meta = "unknown", {}
            info["error"] = str(e)
        if len(rel_parts) == 2:  # inbox/<项目>/：去向由放置的目录决定，来源说明里的 project 不能改变它
            if meta.get("project", rel_parts[0]) != rel_parts[0]:
                info["warning"] = f"来源说明声称属于 {meta['project']}，以所在目录 {rel_parts[0]} 为准"
            meta["project"] = rel_parts[0]
        info["meta"] = meta
        info["missing"] = [k for k in ("project", "source") if not meta.get(k)]
        info["confirm_with_user"] = ["terms", "kind"]  # 来源说明里即使写了，也只是来源方声明
        items.append(info)
    return items


# ---------------------------------------------------------------------------
# SVG：格式整理（外观不变）与问题分类
# ---------------------------------------------------------------------------
def norm_color(value: str) -> str | None:
    """颜色写法统一为 6 位大写十六进制；none 与 url(#…) 原样；无法识别时返回 None。"""
    v = value.strip()
    if v == "none" or v.startswith("url(#"):
        return v
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", v):
        return v.upper()
    if re.fullmatch(r"#[0-9A-Fa-f]{3}", v):
        return "#" + "".join(c * 2 for c in v[1:]).upper()
    m = re.fullmatch(r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)", v, re.I)
    if m and all(int(x) <= 255 for x in m.groups()):
        return "#" + "".join(f"{int(x):02X}" for x in m.groups())
    return NAMED.get(v.lower())


def norm_style(text: str, changes: list[str]) -> str:
    """样式文本中的颜色写法统一（无法识别的保持原样，由检查报告）。"""
    def fix(m: re.Match) -> str:
        new = norm_color(m.group(2))
        if new and new != m.group(2):
            changes.append(f"颜色 {m.group(2)} 写作 {new}")
            return f"{m.group(1)}:{new}"
        return m.group(0)
    return re.sub(r"(fill|stroke|stop-color|flood-color|lighting-color|color)\s*:\s*([^;}\"]+?)(?=\s*[;}]|$)", fix, text)


def expand_classes(root: ET.Element, changes: list[str]) -> None:
    """只含类选择器的 <style> 展开为元素属性并移除；含其他选择器时保留 <style>（颜色写法照样统一）。"""
    for style in [el for el in root.iter() if el.tag == f"{{{logo.SVG_NS}}}style"]:
        css = re.sub(r"/\*.*?\*/", "", style.text or "", flags=re.S)
        rules = re.findall(r"([^{}]+)\{([^{}]*)\}", css)
        selectors = [s.strip() for sel, _ in rules for s in sel.split(",")]
        if not rules or not all(re.fullmatch(r"\.[A-Za-z_][\w-]*", s) for s in selectors) or re.sub(r"[^{}]+\{[^{}]*\}", "", css).strip():
            style.text = norm_style(style.text or "", changes)
            continue
        decls: dict[str, list[tuple[str, str]]] = {}
        for sel, body in rules:
            pairs = [tuple(x.strip() for x in d.split(":", 1)) for d in body.split(";") if ":" in d]
            for s in sel.split(","):
                decls.setdefault(s.strip()[1:], []).extend(pairs)
        for el in root.iter():
            for cls in (el.get("class") or "").split():
                for prop, val in decls.get(cls, []):
                    el.set(prop, val)  # 类样式优先于同名呈现属性，与浏览器一致
            el.attrib.pop("class", None)
        for parent in root.iter():
            if style in list(parent):
                parent.remove(style)
        changes.append(f"CSS 类样式展开为属性（{len(decls)} 个类）")


def normalize(text: str) -> tuple[str, list[str]]:
    """外观不变的格式整理，返回 (整理后的 SVG, 改动清单)。调用方须再逐像素核对。"""
    changes: list[str] = []
    root = ET.fromstring(text)
    if root.tag != f"{{{logo.SVG_NS}}}svg":
        raise logo.LogoError("根元素不是带 xmlns 的 <svg>")
    if not root.get("viewBox"):
        w, h = (re.fullmatch(r"\s*([\d.]+)\s*(px)?\s*", root.get(k) or "") for k in ("width", "height"))
        if w and h:
            root.set("viewBox", f"0 0 {w.group(1)} {h.group(1)}")
            changes.append("按宽高补上 viewBox")
    for k in ("width", "height"):
        if root.get(k) is not None:
            root.attrib.pop(k)
            changes.append(f"去掉写死的 {k}")
    removed = 0
    for parent in list(root.iter()):
        for child in list(parent):
            ns = child.tag[1:].split("}")[0] if child.tag.startswith("{") else ""
            if ns != logo.SVG_NS or child.tag == f"{{{logo.SVG_NS}}}metadata":
                parent.remove(child)
                removed += 1
    for el in root.iter():
        for attr in list(el.attrib):
            ns = attr[1:].split("}")[0] if attr.startswith("{") else ""
            if ns and ns not in (logo.XLINK_NS, "http://www.w3.org/XML/1998/namespace"):
                el.attrib.pop(attr)
                removed += 1
    if removed:
        changes.append(f"删除编辑器元数据与命名空间内容（{removed} 处）")
    expand_classes(root, changes)
    for el in root.iter():
        for attr, val in list(el.attrib.items()):
            name = attr.split("}")[-1]
            if name in logo.COLOR_ATTRS:
                new = norm_color(val)
                if new and new != val:
                    el.set(attr, new)
                    changes.append(f"颜色 {val} 写作 {new}")
            elif name == "style":
                el.set(attr, norm_style(val, changes))
    if not changes:
        return text, []  # 无需整理：逐字保留原件
    return ET.tostring(root, encoding="unicode") + "\n", list(dict.fromkeys(changes))


def strip_external(text: str) -> tuple[str, int]:
    """去掉 SVG 中所有指向外部（本机文件、网址）的引用，只保留文内引用（#id）与内嵌位图（data:image/png 等）。
    返回 (清理后的 SVG, 去掉的处数)。渲染外部 SVG 前必须先清理，否则 resvg 会读入本机文件。"""
    if ENTITY_RE.search(text):
        raise logo.LogoError("SVG 含 DOCTYPE / ENTITY 声明（可被用来做实体膨胀攻击），拒绝处理；请来源去掉后重交")
    root = ET.fromstring(text)
    removed = 0
    for el in root.iter():
        for attr in list(el.attrib):
            name, val = attr.split("}")[-1], el.attrib[attr]
            if name == "href" and not (val.startswith("#") or SAFE_DATA_RE.match(val)):
                del el.attrib[attr]  # 外部 href 全部去掉后，xml:base 也就无从生效
                removed += 1
            elif CSS_URL_RE.search(val) or CSS_IMPORT_RE.search(val):
                el.set(attr, CSS_IMPORT_RE.sub("", CSS_URL_RE.sub("none", val)))
                removed += 1
        if el.tag == f"{{{logo.SVG_NS}}}style" and el.text and (CSS_URL_RE.search(el.text) or CSS_IMPORT_RE.search(el.text)):
            el.text = CSS_IMPORT_RE.sub("", CSS_URL_RE.sub("none", el.text))
            removed += 1
    return (ET.tostring(root, encoding="unicode") if removed else text), removed


def safe_render(text: str, out: Path, **kw) -> Path:
    """渲染不可信的外部 SVG：先去掉外部引用，再在空的临时目录里渲染（不会读到本机任何其他文件）。"""
    return logo.render_text(strip_external(text)[0], out, **kw)


def same_look(a: str, b: str) -> bool:
    """两份 SVG 渲染后是否逐像素一致（允许极小的抗锯齿差异）。"""
    with tempfile.TemporaryDirectory() as td:
        pa = safe_render(a, Path(td) / "a.png", width=512)
        pb = safe_render(b, Path(td) / "b.png", width=512)
        r = subprocess.run(["magick", "compare", "-metric", "AE", "-fuzz", "2%", str(pa), str(pb), "null:"],
                           capture_output=True, text=True)
    count = (r.stderr or r.stdout).strip().split(" ")[0]
    try:
        return r.returncode in (0, 1) and float(count) == 0
    except ValueError:
        return False  # 尺寸不同等无法比较的情况


def hard_issues(text: str) -> list[str]:
    """系统硬性规则（与项目无关）：用 check_svg 检查，色板放行文中全部颜色，只留结构性问题。"""
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "x.svg"
        f.write_text(text, encoding="utf-8")
        errors: list[str] = []
        logo.check_svg(f, {c.upper() for c in logo.HEX_IN_TEXT_RE.findall(text)}, errors, [])
    return [e.split(": ", 1)[-1] for e in errors]


# ---------------------------------------------------------------------------
# 分析：格式整理、硬性问题、项目规范冲突，以及用户给出的处理
# ---------------------------------------------------------------------------
def parse_resolves(items: list[str] | None) -> dict[str, tuple[str, str]]:
    out = {}
    for item in items or []:
        m = RESOLVE_RE.match(item.strip())
        if not m:
            raise logo.LogoError(f"--resolve 写法为 #RRGGBB=palette:色名（加入色板）或 #RRGGBB=map:色名（换成色板色），收到：{item}")
        out[m.group(1).upper()] = (m.group(2), m.group(3))
    return out


def svg_text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").lstrip("\ufeff")


def analyze(p: "logo.Project", data: bytes, kind: str, init: bool, resolves: dict, as_concept: bool) -> dict:
    """分析导入文件，返回报告：format（格式整理）、hard（硬性问题）、conflicts（色板外颜色）、unresolved、final 等。"""
    rep: dict = {"type": kind, "format": [], "hard": [], "conflicts": [], "unresolved": [], "palette_add": {},
                 "mapped": {}, "final": None, "as_concept": as_concept or kind != "svg", "external": 0}
    if kind != "svg":
        return rep
    text = svg_text(data)
    try:
        rep["external"] = strip_external(text)[1]  # 同时拒绝 DOCTYPE / ENTITY
    except ET.ParseError as e:
        raise logo.LogoError(f"SVG 解析失败：{e}")
    try:
        normalized, changes = normalize(text)
    except ET.ParseError as e:
        raise logo.LogoError(f"SVG 解析失败：{e}")
    if changes and not same_look(text, normalized):
        rep["hard"].append("格式整理后外观与原件不一致（可能依赖宽高比例或不支持的样式），未做整理")
        normalized = text
    else:
        rep["format"] = changes
    rep["hard"] += hard_issues(normalized)
    if rep["as_concept"]:
        return rep
    colors = list(dict.fromkeys(c.upper() for c in logo.HEX_IN_TEXT_RE.findall(normalized)))
    palette = {} if init else p.palette
    by_value = {v: k for k, v in palette.items()}
    rep["conflicts"] = [c for c in colors if c not in by_value]
    extra = [c for c in resolves if c not in rep["conflicts"]]
    if extra:
        raise logo.LogoError(f"这些颜色不存在冲突，无需处理：{', '.join(extra)}")
    used_names = set(palette)
    for c in rep["conflicts"]:
        how = resolves.get(c)
        if init and not how:  # 初始化：图的颜色就是色板，未命名的自动起名
            n = 1
            while f"color_{n}" in used_names:
                n += 1
            how = ("palette", f"color_{n}")
        if not how:
            rep["unresolved"].append(c)
            continue
        action, name = how
        if action == "map":
            if init:
                raise logo.LogoError("初始化项目时色板就取自图中的颜色，不能 map；用 palette:色名 给颜色起名")
            if name not in palette:
                raise logo.LogoError(f"map:{name} 不是色板中的色名（可选：{', '.join(palette)}）")
            rep["mapped"][c] = (name, palette[name])
        else:
            if name in used_names:
                raise logo.LogoError(f"色名 {name} 已存在，请换一个")
            used_names.add(name)
            rep["palette_add"][name] = c
    if not rep["unresolved"] and not rep["hard"]:
        final = logo.HEX_IN_TEXT_RE.sub(lambda m: rep["mapped"].get(m.group(0).upper(), (None, m.group(0)))[1], normalized)
        rep["final"] = final
    rep["normalized"] = normalized
    return rep


def preview_key(data: bytes, a, name: str) -> str:
    """预演与正式导入须完全相同：文件内容与所有影响写入内容的参数。"""
    fields = [hashlib.sha256(data).hexdigest(), a.project, bool(a.init), bool(a.as_concept), a.direction or "",
              a.new_direction or "", a.new_direction_letter or "", a.title or "", a.series_rule or "", name,
              sorted(a.resolve or []), preview_context(logo.Project(a.project))]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode("utf-8")).hexdigest()


def preview_context(owner) -> str:
    """预演依赖的色板、发布、系列及封面规格变化后必须重新确认。"""
    import covers
    import series
    projects = [p for p, _ in series.members(owner.sid)] if covers.is_family(owner) else [owner]
    files = {logo.CONFIG / "cover-platforms.toml"}
    for project in projects:
        files.add(project.toml_path)
        files.update((project.dir / "releases").glob("r*/layouts/*.svg"))
        files.update((project.dir / "covers").glob("*/cover.toml"))
        files.update((project.dir / "covers").glob("*/*.svg"))
        member = series.membership(project)
        if member and logo.SLUG_RE.fullmatch(member.get("id", "")):
            files.update((series.SERIES_DIR / member["id"]).glob("series.*"))
    if covers.is_family(owner):
        files.update(owner.dir.glob("series.*"))
        files.update((owner.dir / "covers").glob("*/cover.toml"))
        files.update((owner.dir / "covers").glob("*/*.svg"))
    records = [(logo.rel(path), logo.sha256_file(path)) for path in sorted(files) if path.is_file()]
    return hashlib.sha256(json.dumps(records, ensure_ascii=False).encode("utf-8")).hexdigest()


def preview_ok(key: str) -> bool:
    """有这次参数的预演记录，且未过期（超过 PREVIEW_DAYS 天须重新预演、重新给用户看）。"""
    try:
        when = dt.date.fromisoformat(previewed().get(key, ""))
    except ValueError:
        return False
    return (dt.date.today() - when).days <= PREVIEW_DAYS


def previewed() -> dict:
    try:
        return json.loads((PREVIEWS / "previewed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_preview(name: str, original: bytes, kind: str, rep: dict) -> Path:
    """原件与导入结果的并排对比图，供 Agent 发给用户确认。"""
    out = PREVIEWS / f"{name}-preview.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        src = t / f"original.{kind}"
        src.write_bytes(original)
        a = safe_render(svg_text(original), t / "a.png", size=480) if kind == "svg" else src
        cells = ["-label", "original", str(a)]
        if kind == "svg" and rep.get("final"):
            b = safe_render(rep["final"], t / "b.png", size=480)
            cells += ["-label", "imported", str(b)]
        logo.run(["magick", "montage", *cells, "-tile", f"{len(cells) // 3}x1", "-geometry", "480x480+12+12",
                  "-background", "#DDDDDD", str(out)])
    return out


def report_lines(rep: dict, init: bool) -> list[str]:
    lines = []
    if rep["type"] != "svg":
        lines.append("位图：只能登记为概念图（设计依据），之后由 Agent 重绘为 SVG 版本")
    if rep["format"]:
        lines.append("格式整理（外观不变，已逐像素核对）：" + "；".join(rep["format"]))
    for h in rep["hard"]:
        lines.append(f"✗ 系统硬性规则：{h}")
    if rep["hard"] and not rep["as_concept"]:
        lines.append("  → 这些问题项目规范也改不了：只能 --as-concept 登记为概念图，或请来源修正后重交")
    if rep.get("external"):
        lines.append(f"外部引用 {rep['external']} 处（指向本机文件或网址）：渲染与存档时一律剔除，存档图不含这些被引用的内容")
    if rep["palette_add"]:
        lines.append(("色板（取自图中颜色）：" if init else "改项目规范——加入色板：")
                     + "、".join(f"{k} {v}" for k, v in rep["palette_add"].items()))
    if rep["mapped"]:
        lines.append("改图片——换成色板色：" + "、".join(f"{c}→{n}（{v}）" for c, (n, v) in rep["mapped"].items()))
    if rep["unresolved"]:
        lines.append("需要用户决定（颜色不在色板中）：" + "、".join(rep["unresolved"]))
        lines.append("  → 逐个问用户：改图片 --resolve \"#RRGGBB=map:色板色名\"，或改项目规范 --resolve \"#RRGGBB=palette:新色名\"")
    return lines


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------
def terms_text(terms: str) -> str:
    terms = " ".join(terms.split())  # 用户在会话中给出的条款：合并为一行，保持记录格式
    return UNCONFIRMED if terms.lower() in UNKNOWN_TERMS else terms


def default_palette() -> dict[str, str]:
    """新项目模板里的默认色板（访谈前的占位颜色）。"""
    text = logo.render_template("project.toml", name="x", slug="x", date="2000-01-01")
    return {k: v.upper() for k, v in tomllib.loads(text).get("palette", {}).items()}


def check_init(p: "logo.Project") -> None:
    """--init 只用于尚未开始设计、色板仍是模板默认值的项目；继承型子品牌的色板来自母品牌，不能覆盖。"""
    import series  # 延迟导入：series.py 依赖 logo
    if any(p.versions_in(d) for d in p.directions()) or p.releases():
        raise logo.LogoError(f"{p.slug} 已开始设计，不能用 --init 初始化；请按已有项目导入（冲突逐项决定）")
    m = series.membership(p)
    if m and m.get("role") == "sub" and m.get("id") in series.all_series() \
            and series.load_series(m["id"]).get("architecture") in series.INHERITING:
        raise logo.LogoError(f"{p.slug} 是继承型系列的子品牌，色板继承自母品牌，不能用 --init 覆盖；"
                             "请去掉 --init 按已有项目导入，色板外的颜色逐项决定（并选择子品牌例外或更新系列规则）")
    if p.palette != default_palette():
        raise logo.LogoError(f"{p.slug} 的色板已经设定（不再是模板默认值），--init 会把它整个覆盖；"
                             "请去掉 --init 按已有项目导入，色板外的颜色逐项决定")
    text = p.toml_path.read_text(encoding="utf-8")
    if "[palette]\n" not in text or "[fonts]\n" not in text:
        raise logo.LogoError("project.toml 结构与模板不同，无法初始化色板；请手工整理后重试")


def apply_palette(p: "logo.Project", add: dict[str, str], init: bool) -> None:
    """改项目规范：新颜色加入色板；初始化时色板与配色模式整体取自图中颜色，App 底色取第一个颜色。"""
    import series  # 延迟导入：series.py 依赖 logo
    text = p.toml_path.read_text(encoding="utf-8")
    if init:
        if "[palette]\n" not in text or "[fonts]\n" not in text:
            raise logo.LogoError("project.toml 结构与模板不同，无法初始化色板；请手工整理后重试")
        lines = ["# 色板取自导入的设计原件（./logo import --init），可改色名", "[palette]"]
        lines += [f"{k} = {series.toml_value(v)}" for k, v in add.items()]
        lines += ["", "[color_modes]", 'modes = ["color", "mono", "reversed"]', "[color_modes.mono]"]
        lines += [f'{k} = "#000000"' for k in add] + ["[color_modes.reversed]"] + [f'{k} = "#FFFFFF"' for k in add]
        start, end = text.index("[palette]\n"), text.index("[fonts]\n")
        text = text[:start] + "\n".join(lines) + "\n\n" + text[end:]
        text = re.sub(r"^app_background = .*$", f"app_background = {series.toml_value(next(iter(add)))}", text,
                      count=1, flags=re.M)
    else:
        for k, v in add.items():
            text = series.toml_set(text, "palette", k, series.toml_value(v))
    p.toml_path.write_text(text, encoding="utf-8")


def fill_line(text: str, placeholder: str, value: str) -> str:
    """把模板占位行（整行）替换为内容。"""
    return re.sub(rf"^[ \t]*{re.escape(placeholder.strip())}[ \t]*$", lambda _: value, text, count=1, flags=re.M)


def write_decision(p: "logo.Project", name: str, meta: dict, rep: dict, note: str) -> Path:
    """冲突的处理是关键取舍：自动写好事实（冲突、两个选项、用户的选择与原话）的决策记录。"""
    ddir = p.dir / "decisions"
    nums = [int(m.group(1)) for f in ddir.glob("*.md") if (m := re.match(r"(\d{3})-", f.name))]
    num = f"{max(nums, default=0) + 1:03d}"
    f = ddir / f"{num}-import-{name}.md"
    text = logo.render_template("decision.md", num=num, title=f"导入 {name} 的冲突处理", date=logo.today())
    conflicts = "、".join(list(rep["palette_add"].values()) + list(rep["mapped"]))
    chosen = "；".join(report_lines({**rep, "unresolved": [], "hard": [], "format": [], "type": "svg"}, False))
    text = fill_line(text, "（在什么情况下需要做这个决定）",
                     f"导入外部设计 {name}（来源：{logo.md_inline(meta['source'])}，{KINDS[meta['kind']]}）时，"
                     f"颜色 {conflicts} 不在项目色板中。")
    text = fill_line(text, "1. **选项 A**:", "1. **选项 A**: 改图片——把这些颜色换成现有色板颜色，保持项目规范不变")
    text = fill_line(text, "2. **选项 B**:", "2. **选项 B**: 改项目规范——把这些颜色加入色板，保持图片原样")
    text = fill_line(text, "（选择了哪个，为什么，有哪些取舍）", f"用户选择：{chosen}。用户意见：{note}")
    logo.write_new(f, text)
    (ddir / ".gitkeep").unlink(missing_ok=True)
    return f


def archive_original(src: Path) -> Path:
    """原件留在本机 .cache/imported/<日期>/（不进仓库）；来自收件箱的连同来源说明一起移走。"""
    dest_dir = IMPORTED / logo.today()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest, n = dest_dir / src.name, 2
    while dest.exists():
        dest, n = dest_dir / f"{src.stem}-{n}{src.suffix}", n + 1
    in_inbox = INBOX in src.resolve().parents
    (shutil.move if in_inbox else shutil.copy2)(str(src), str(dest))
    side = src.with_name(src.name + SIDECAR)
    if in_inbox and side.exists():
        shutil.move(str(side), str(dest.with_name(dest.name + SIDECAR)))
    return dest


def archived_text(src_in_inbox: bool, kept: Path) -> str:
    """原件去向的说明：收件箱里的文件被移走；其他位置的文件只复制一份，原文件保留在原处。"""
    if src_in_inbox:
        return f"原件已从收件箱移至本机 {logo.rel(kept)}（不进仓库）"
    return f"原件已复制一份到本机 {logo.rel(kept)}（不进仓库；原文件保留在原处）"


def cmd_import(a) -> None:
    src = Path(a.file)
    if not src.exists() and (INBOX / a.file).exists():
        src = INBOX / a.file
    data, kind = read_source(src)
    meta = sidecar(src)
    if src.resolve().parent.parent == INBOX.resolve():
        meta["project"] = src.resolve().parent.name  # inbox/<项目>/ 下的文件属于该项目，来源说明不能改变去向
    described = [f"{label}：{meta.pop(k)}" for k, label in (("layout", "版式"), ("text", "需要的文字"), ("ratio", "画幅"))
                 if meta.get(k)]
    source_note = "；".join(described + [" ".join(meta.pop("note", "").split())]).strip("；")  # 来源方说明：只作资料
    # 使用条款与“候选/成品”是用户的确认与判断：只能来自本次会话；来源方写的只作参考
    claims = {k: meta.pop(k) for k in ("terms", "kind") if meta.get(k)}
    for k in ("project", "source", "prompt", "terms", "kind", "direction", "name"):
        if getattr(a, k, None):
            meta[k] = getattr(a, k)
    a.project = meta.get("project")
    if not a.project:
        raise logo.LogoError("须指定 --project（加入哪个项目；新项目请先完成需求访谈并 ./logo new）")
    a.direction = a.direction or meta.get("direction")
    p = logo.Project(a.project)
    name = logo.to_slug(meta.get("name") or src.stem)
    init = bool(a.init)
    if init:
        check_init(p)
    resolves = parse_resolves(a.resolve)
    rep = analyze(p, data, kind, init, resolves, bool(a.as_concept))
    key = preview_key(data, a, name)
    if a.analyze:
        out = write_preview(name, data, kind, rep)
        print(f"导入预演：{src.name}（{kind}，{len(data)} 字节）→ {p.slug}" + ("（初始化项目）" if init else ""))
        for line in report_lines(rep, init) or ["与项目规范没有冲突，可原样导入"]:
            print(f"  {line}")
        print(f"对比图：{logo.rel(out)}（左：原件；右：导入结果）——请发给用户确认后再正式导入")
        if claims:
            print("来源方声明（仅供参考，须向用户确认）：" + "；".join(
                f"{'使用条款' if k == 'terms' else '类型'} {v}" for k, v in claims.items()))
        if not rep["unresolved"] and (rep["final"] or rep["as_concept"]) and not (rep["hard"] and not rep["as_concept"]):
            store = previewed()
            store[key] = logo.today()
            PREVIEWS.mkdir(parents=True, exist_ok=True)
            (PREVIEWS / "previewed.json").write_text(json.dumps(store), encoding="utf-8")
            print("用户确认后，去掉 --analyze、其余参数不变，补上用户确认的 --terms 与 --kind"
                  "（--source / --prompt 来源说明中已有的可省略）")
        return
    if not preview_ok(key):
        raise logo.LogoError("请先用同样的文件与参数加上 --analyze 预演，把对比图发给用户确认，再正式导入（预演 1 天内有效）")
    missing = [k for k in ("source", "terms", "kind") if not meta.get(k)]
    if missing:
        raise logo.LogoError(f"须写明 {', '.join('--' + k for k in missing)}（向用户问清：来自哪个工具 / Agent、"
                             "使用条款或版权、候选还是成品；来源说明里的条款与类型只作参考，须由用户确认）")
    if meta["kind"] not in KINDS:
        raise logo.LogoError("--kind 只能是 candidate（候选）或 final（成品）")
    if rep["hard"] and not rep["as_concept"]:
        raise logo.LogoError("存在系统硬性规则问题，不能成为版本；用 --as-concept 只登记为概念图，或请来源修正后重交")
    changes_spec = bool(rep["palette_add"] or rep["mapped"]) and not init
    note = " ".join((a.note or "").split())  # 用户的原话：只能来自本次会话的 --note
    if changes_spec and not note:
        raise logo.LogoError("处理了与项目规范的冲突，须用 --note 写明用户的原话或要点（写进决策记录）")
    import series  # 延迟导入：series.py 依赖 logo
    m = series.membership(p)
    inheriting_sub = bool(m and m.get("role") == "sub"
                          and series.load_series(m["id"]).get("architecture") in series.INHERITING)
    if rep["palette_add"] and inheriting_sub and not init and a.series_rule not in ("exception", "update"):
        raise logo.LogoError("子品牌新增颜色会偏离母品牌的色板：先问用户是作为子品牌的例外（--series-rule exception），"
                             "还是更新系列规则（--series-rule update）")
    if not rep["as_concept"] and not (a.new_direction or a.direction):
        raise logo.LogoError("须指定放入哪个方向：--direction <字母>，或 --new-direction <英文短名>")
    ddir = p.direction(a.direction) if not rep["as_concept"] and not a.new_direction else None  # 先校验
    letter = None
    if not rep["as_concept"] and a.new_direction:  # 新方向的全部检查（含访谈门槛）在任何写入之前
        letter = logo.check_new_direction(p, a.new_direction_letter or next_letter(p))
    terms = terms_text(meta["terms"])
    with tempfile.TemporaryDirectory() as td:
        if kind == "svg":  # 原件渲染存档（概念图须为位图）；先剔除外部引用，不读入本机任何其他文件
            image = safe_render(svg_text(data), Path(td) / f"{name}.png", size=800)
        else:
            image = Path(td) / f"{name}.{kind}"
            logo.run(["magick", *MAGICK_LIMITS, str(src), "-strip", str(image)])  # 去掉 EXIF / GPS 等元数据
        # 渲染成功后才开始写入：新方向（含访谈门槛）→ 概念图 → 版本 → 色板 → 决策 → 系列记录
        if letter:
            ddir = logo.create_direction(p, letter, a.new_direction, a.title)
        # 来源说明与文件名来自外部：一律经 md_inline 处理后才写进文档（不能伪造标题、段落或“用户意见”）
        extra = (f"\n## 导入来源\n- **原件**: {logo.md_inline(src.name)}（sha256 {hashlib.sha256(data).hexdigest()}，"
                 f"原件保留在本机 {logo.rel(IMPORTED)}/）\n- **来源**: {logo.md_inline(meta['source'])}\n- **使用条款**: {terms}\n"
                 f"- **类型**: {KINDS[meta['kind']]}\n- **导入日期**: {logo.today()}\n"
                 + "".join(f"- **来源方声明的{'使用条款' if k == 'terms' else '类型'}**（仅作参考）: {logo.md_inline(v)}\n"
                           for k, v in claims.items())
                 + (f"- **来源方说明**（仅作资料）: {logo.md_inline(source_note)}\n" if source_note else "")
                 + (f"- **用户意见**: {note}\n" if note else ""))
        stem, dst = logo.register_concept(p, image, name, meta.get("prompt") or "（未提供）",
                                          logo.md_inline(meta["source"]), extra)
    md = p.dir / "concepts" / f"{stem}.md"
    print(f"已登记原件为概念图：{logo.rel(dst)}")
    if rep["palette_add"]:
        apply_palette(p, rep["palette_add"], init)
        print(("已用图中颜色初始化色板：" if init else "已加入色板：") + "、".join(f"{k} {v}" for k, v in rep["palette_add"].items()))
    if ddir:
        summary = [f"外部导入：{logo.md_inline(src.name)}（来源：{logo.md_inline(meta['source'])}；{KINDS[meta['kind']]}），"
                   f"原件见 concepts/{stem}.md。",
                   f"- **使用条款**: {terms}"]
        summary += [f"- {line}" for line in report_lines(rep, init)]
        vid, vdir = logo.create_version(p, ddir, rep["final"], f"外部导入（concepts/{stem}.md）", f"concepts/{stem}.md")
        notes = vdir / "notes.md"
        notes.write_text(fill_line(notes.read_text(encoding="utf-8"), "（这个版本做了什么设计或修改）", "\n".join(summary)),
                         encoding="utf-8")
        md.write_text(fill_line(md.read_text(encoding="utf-8"), "（从这张图中我们提取什么元素进入 SVG 矢量化）",
                                f"外部导入的设计原件，经格式整理后成为 {vid}（见下方“导入来源”）。"), encoding="utf-8")
        print(f"已创建版本：{vid} → {logo.rel(vdir)}（请与用户一起补写 notes.md 的“理念体现”）")
    if changes_spec:
        d = write_decision(p, name, meta, rep, note)
        print(f"已写入决策记录：{logo.rel(d)}")
    if rep["palette_add"] and inheriting_sub and not init:
        label = "子品牌例外" if a.series_rule == "exception" else "系列规则调整"
        rmd = series.SERIES_DIR / m["id"] / "series.md"
        if rmd.exists():
            logo.append_text(rmd, (
                f"\n\n### {logo.today()} {label}：{p.slug} 导入 {name} 新增颜色\n"
                f"- **颜色**：{'、'.join(f'{k} {v}' for k, v in rep['palette_add'].items())}\n"
                f"- **用户意见**：{note}\n"))
            print(f"已在 {series.rules_path(m['id'])} 追加{label}记录")
    in_inbox = INBOX in src.resolve().parents
    kept = archive_original(src)
    store = previewed()
    store.pop(key, None)
    (PREVIEWS / "previewed.json").write_text(json.dumps(store), encoding="utf-8")
    print(archived_text(in_inbox, kept))
    if terms == UNCONFIRMED:
        print("⚠ 使用条款未确认：发布基于这个导入的版本时 check 会提醒")
    if meta["kind"] == "final" and ddir:
        print(f"成品：确认后照常定稿——{logo.cli_prefix()} decision 记录定稿理由，再 {logo.cli_prefix()} release --from 该版本"
              + ("（系列子品牌先做系列评审）" if m and m.get("role") == "sub" else ""))
    elif not ddir:
        print("已作为设计依据登记：请补写概念图记录的“灵感提取”，之后由 Agent 重绘 SVG 版本（--inspired-by 引用）")


# ---------------------------------------------------------------------------
# 封面导入：外部图作为封面底图，Logo 仍由系统从发布嵌入
# ---------------------------------------------------------------------------
RATIO_TOLERANCE = 0.02  # 图片与画布比例相差在 2% 以内视为一致
FIT_RE = re.compile(r"^([0-9.]+x[0-9.]+)=(crop|pad)$")
FITS = {"crop": "xMidYMid slice", "pad": "xMidYMid meet"}
FIT_NAMES = {"crop": "裁切铺满", "pad": "完整放入（留边）"}
IMPORTED_BG = "imported-background"


def cover_image(src: Path, data: bytes, kind: str, td: Path, name: str) -> tuple[Path, list[str]]:
    """准备底图：位图去掉元数据；SVG 按原样渲染为 PNG（封面以位图导出，外观不变）。"""
    if kind == "svg":
        text = svg_text(data)
        try:
            removed = strip_external(text)[1]  # 同时拒绝 DOCTYPE / ENTITY
        except ET.ParseError as e:
            raise logo.LogoError(f"SVG 解析失败：{e}")
        if removed:
            raise logo.LogoError(f"SVG 含 {removed} 处外部引用（指向本机文件或网址），封面底图不能这样导入；"
                                 "请来源把图片内嵌或改为位图后重交")
        return logo.render_text(text, td / f"{name}.png", width=2560), ["SVG 已按原样渲染为 PNG（封面以位图导出）"]
    out = td / f"{name}.{kind}"
    logo.run(["magick", *MAGICK_LIMITS, str(src), "-strip", str(out)])
    return out, ["已去掉 EXIF / GPS 等元数据"]


def image_size(path: Path) -> tuple[int, int]:
    w, h = logo.run(["magick", "identify", *MAGICK_LIMITS, "-format", "%w %h", f"{path}[0]"], capture=True).split()
    return int(w), int(h)


def cover_plan(owner, a) -> dict:
    """要铺底图的画幅：新封面按目标平台推出；已有封面只处理 --ratio 指定的一个画幅。"""
    import covers  # 延迟导入：covers.py 依赖 logo
    ratios = covers.load_config()["ratios"]
    if a.cover:
        cdir = covers.cover_dir(owner, a.cover)
        if not a.ratio:
            raise logo.LogoError("给已有封面导入底图须用 --ratio 指定画幅（每个画幅一张图）")
        if a.ratio not in ratios or not (cdir / f"{a.ratio}.svg").exists():
            have = ", ".join(s.stem for s in sorted(cdir.glob("*.svg")))
            raise logo.LogoError(f"{cdir.name} 没有画幅 {a.ratio} 的设计稿（已有：{have}）")
        return {"cdir": cdir, "ratios": [a.ratio], "canvas": {a.ratio: ratios[a.ratio]}}
    if a.ratio:
        raise logo.LogoError("--ratio 只用于给已有封面（--cover）导入某个画幅的底图")
    if not a.platforms:
        raise logo.LogoError("新封面须用 --platforms 指定目标平台（先问用户这张封面用在哪些平台）")
    need = covers.needed_ratios(covers.parse_platforms(a.platforms))
    # 新封面的全部检查在任何写入之前（避免建好封面后才发现无法放入底图）
    if covers.is_family(owner):
        covers.family_members(owner, a)  # 成员须由用户指定、属于该系列且已发布
    else:
        covers.skeleton_choice(owner, a.from_skeleton, a.no_skeleton)  # 系列已有骨架时须先问用户是否套用
        rid = a.release or owner.cfg["state"].get("current_release")
        if not rid:
            raise logo.LogoError("项目还没有 Logo 发布；先 ./logo release，或用 --release 指定")
        covers.release_layouts(owner, rid)
    return {"cdir": None, "ratios": need, "canvas": {r: ratios[r] for r in need}}


def fit_plan(size: tuple[int, int], plan: dict, fits: dict[str, str]) -> tuple[dict, list[str]]:
    """比例一致的画幅直接铺满；不一致的须用户决定裁切还是留边（或换目标平台）。"""
    extra = [r for r in fits if r not in plan["ratios"]]
    if extra:
        raise logo.LogoError(f"--fit 指定的画幅不在本次处理范围内：{', '.join(extra)}")
    decided, unresolved = {}, []
    aspect = size[0] / size[1]
    for r in plan["ratios"]:
        c = plan["canvas"][r]
        if abs(aspect / (c["width"] / c["height"]) - 1) <= RATIO_TOLERANCE:
            decided[r] = fits.get(r, "crop")
        elif r in fits:
            decided[r] = fits[r]
        else:
            unresolved.append(r)
    return decided, unresolved


def backdrop_color(owner, members: str | None) -> str:
    import covers  # 延迟导入：covers.py 依赖 logo
    p = logo.Project(members.split(",")[0].strip()) if covers.is_family(owner) and members else owner
    try:
        return logo.app_background(p)
    except logo.LogoError:
        return next(iter(p.palette.values()), "#FFFFFF")


def composed(canvas: dict, href: str, fit: str, bg: str) -> str:
    w, h = canvas["width"], canvas["height"]
    return (f'<svg xmlns="{logo.SVG_NS}" viewBox="0 0 {w} {h}"><rect width="{w}" height="{h}" fill="{bg}"/>'
            f'<image href="{href}" x="0" y="0" width="{w}" height="{h}" preserveAspectRatio="{FITS[fit]}"/></svg>')


def cover_preview(name: str, img: Path, plan: dict, decided: dict, unresolved: list[str], bg: str) -> Path:
    """原图与各画幅的铺放效果（未决定的画幅同时给出裁切与留边两种），供用户选择。"""
    out = PREVIEWS / f"{name}-cover-preview.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cells = ["-label", "original", str(img)]
    for r in plan["ratios"]:
        for fit in ([decided[r]] if r in decided else ["crop", "pad"]):
            png = logo.render_text(composed(plan["canvas"][r], img.name, fit, bg), img.parent / f"{r}-{fit}.png",
                                   resources=img.parent, width=480)
            cells += ["-label", f"{r} {fit}", str(png)]
    logo.run(["magick", "montage", *cells, "-geometry", "480x480+10+10", "-tile", "3x", "-background", "#DDDDDD", str(out)])
    return out


def place_background(svg: Path, href: str, fit: str, canvas: dict) -> None:
    """在画幅设计稿的底色之上放入导入的底图；已导入过的底图被替换而不是叠加。"""
    text = svg.read_text(encoding="utf-8")
    el = (f'<image id="{IMPORTED_BG}" href="{href}" x="0" y="0" width="{canvas["width"]}" height="{canvas["height"]}" '
          f'preserveAspectRatio="{FITS[fit]}"/>')
    m = re.search(rf'<image\b[^>]*\bid="{IMPORTED_BG}"[^>]*/>', text)
    if m:
        text = text[:m.start()] + el + text[m.end():]
    else:
        m = re.search(r'<rect\b[^>]*\bid="background"[^>]*/>', text) or re.search(r"<svg\b[^>]*>", text)
        text = text[:m.end()] + "\n  " + el + text[m.end():]
    svg.write_text(text, encoding="utf-8")


def cmd_cover_import(a) -> None:
    import covers  # 延迟导入：covers.py 依赖 logo
    owner = covers.owner_of(a.slug)
    src = Path(a.file)
    if not src.exists() and (INBOX / a.file).exists():
        src = INBOX / a.file
    data, kind = read_source(src)
    meta = sidecar(src)
    claims = {k: meta.pop(k) for k in ("terms", "kind") if meta.get(k)}
    a.ratio = a.ratio or (meta.get("ratio") if a.cover else None)
    described = [f"{label}：{meta.pop(k)}" for k, label in (("ratio", "画幅"), ("text", "需要的文字")) if meta.get(k)]
    source_note = "；".join(described + [" ".join(meta.pop("note", "").split())]).strip("；")
    for k in ("source", "prompt", "terms", "kind"):
        if getattr(a, k, None):
            meta[k] = getattr(a, k)
    if a.contains_logo == "yes":
        raise logo.LogoError("封面里的 Logo 只能来自发布（系统嵌入）：请用不含 Logo 的图（可用 ./logo handoff brief "
                             "<项目> --type cover 让对方重出底图）")
    name = logo.to_slug(a.name or meta.get("name") or src.stem)
    a.name = name
    plan = cover_plan(owner, a)
    fits = {}
    for item in a.fit or []:
        m = FIT_RE.match(item.strip())
        if not m:
            raise logo.LogoError(f"--fit 写法为 画幅=crop（裁切铺满）或 画幅=pad（完整放入、留边），收到：{item}")
        fits[m.group(1)] = m.group(2)
    key = hashlib.sha256(json.dumps(
        ["cover", hashlib.sha256(data).hexdigest(), owner.slug, a.cover or "", a.ratio or "", a.platforms or "",
         sorted(fits.items()), a.contains_logo, a.contains_text, bool(a.from_skeleton), bool(a.no_skeleton),
         a.members or "", a.release or "", name, a.title or "", preview_context(owner)],
        ensure_ascii=False).encode("utf-8")).hexdigest()
    with tempfile.TemporaryDirectory() as td:
        img, notes = cover_image(src, data, kind, Path(td), name)
        size = image_size(img)
        decided, unresolved = fit_plan(size, plan, fits)
        bg = backdrop_color(owner, a.members)
        if a.analyze:
            out = cover_preview(name, img, plan, decided, unresolved, bg)
            target = f"{plan['cdir'].name} 的画幅 {a.ratio}" if plan["cdir"] else f"新封面（画幅：{', '.join(plan['ratios'])}）"
            print(f"封面导入预演：{src.name}（{size[0]}×{size[1]}）→ {owner.slug} {target}")
            for n in notes:
                print(f"  {n}")
            for r in plan["ratios"]:
                c = plan["canvas"][r]
                if r in decided:
                    print(f"  {r}（{c['width']}×{c['height']}）：{FIT_NAMES[decided[r]]}")
                else:
                    print(f"  {r}（{c['width']}×{c['height']}）：比例与图片不符，需要用户决定——改图片：--fit {r}=crop（裁切铺满）"
                          f"或 --fit {r}=pad（完整放入、留边）；或改项目：从 --platforms 去掉用这个画幅的平台")
            if a.contains_text == "yes":
                print("  图中有文字：位图文字无法转曲，日后改文案须重做底图（会记入素材说明）")
            if claims:
                print("来源方声明（仅供参考，须向用户确认）：" + "；".join(
                    f"{'使用条款' if k == 'terms' else '类型'} {v}" for k, v in claims.items()))
            print(f"对比图：{logo.rel(out)}（原图与各画幅的铺放效果）——请发给用户确认后再正式导入")
            if not unresolved:
                store = previewed()
                store[key] = logo.today()
                PREVIEWS.mkdir(parents=True, exist_ok=True)
                (PREVIEWS / "previewed.json").write_text(json.dumps(store), encoding="utf-8")
                print("用户确认后，去掉 --analyze、其余参数不变，补上用户确认的 --terms 与 --kind")
            return
        if not preview_ok(key):
            raise logo.LogoError("请先用同样的文件与参数加上 --analyze 预演，把对比图发给用户确认，再正式导入（预演 1 天内有效）")
        missing = [k for k in ("source", "terms", "kind") if not meta.get(k)]
        if missing:
            raise logo.LogoError(f"须写明 {', '.join('--' + k for k in missing)}（向用户问清来源、使用条款或版权、候选还是成品）")
        if meta["kind"] not in KINDS:
            raise logo.LogoError("--kind 只能是 candidate（候选）或 final（成品）")
        terms = terms_text(meta["terms"])
        if plan["cdir"]:
            cdir = plan["cdir"]
        else:
            covers.cmd_new(a)  # 新封面：照常立项起稿（Logo 从发布嵌入、参考线、系列骨架或家族成员）
            cdir = max(covers.project_covers(owner), key=lambda c: c.name)
        extra = (f"\n## 导入来源\n- **原件**: {logo.md_inline(src.name)}（sha256 {hashlib.sha256(data).hexdigest()}，"
                 f"原件保留在本机 {logo.rel(IMPORTED)}/）\n- **使用条款**: {terms}\n- **类型**: {KINDS[meta['kind']]}\n"
                 f"- **图中文字**: {'有（位图文字无法转曲，改文案须重做底图）' if a.contains_text == 'yes' else '无'}\n"
                 f"- **处理**: {'；'.join(notes)}\n"
                 f"- **铺放**: {'；'.join(f'{r} {FIT_NAMES[decided[r]]}' for r in plan['ratios'])}\n"
                 + "".join(f"- **来源方声明的{'使用条款' if k == 'terms' else '类型'}**（仅作参考）: {logo.md_inline(v)}\n"
                           for k, v in claims.items())
                 + (f"- **来源方说明**（仅作资料）: {logo.md_inline(source_note)}\n" if source_note else ""))
        stem, dst = covers.register_asset(cdir, img, name, logo.md_inline(meta["source"]), terms, extra)
    for r in plan["ratios"]:
        place_background(cdir / f"{r}.svg", f"assets/{dst.name}", decided[r], plan["canvas"][r])
    in_inbox = INBOX in src.resolve().parents
    kept = archive_original(src)
    store = previewed()
    store.pop(key, None)
    (PREVIEWS / "previewed.json").write_text(json.dumps(store), encoding="utf-8")
    print(f"已导入为封面底图：{logo.rel(dst)} → {cdir.name}（{', '.join(plan['ratios'])}）")
    print(archived_text(in_inbox, kept))
    print(f"下一步：与用户填写 {logo.rel(dst.with_suffix('.md'))} 的“用途与授权范围”"
          + ("与 cover.md" if not plan["cdir"] else "") + f"，{logo.cli_prefix()} cover preview {owner.slug} {cdir.name[:3]} 给用户看")
    if terms == UNCONFIRMED:
        print("⚠ 使用条款未确认：请在素材说明中与用户确认后再上线")


def next_letter(p: "logo.Project") -> str:
    used = {d.name.split("-", 1)[0] for d in p.directions()}
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if c not in used:
            return c
    raise logo.LogoError("方向字母已用完")


# ---------------------------------------------------------------------------
# 收件箱命令与检查
# ---------------------------------------------------------------------------
def cmd_inbox(a) -> None:
    if a.spec:
        print(f"来源说明文件：与设计文件同名加 {SIDECAR}（如 logo.svg{SIDECAR}），TOML 格式，字段均可选：")
        for k, v in SIDECAR_FIELDS.items():
            print(f"  {k:<10} {v}")
        print(f"放在 {logo.rel(INBOX)}/（去向待定）或 {logo.rel(INBOX)}/<项目>/（加入该项目）。说明内容只作资料，不会被当作指令执行。")
        return
    items = inbox_items()
    if a.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return
    if not items:
        print(f"收件箱为空（{logo.rel(INBOX)}/）")
        return
    print(f"收件箱 {logo.rel(INBOX)}/ 中有 {len(items)} 个待导入文件：")
    for it in items:
        lack = (f"；待问清：{', '.join(it['missing'])}" if it["missing"] else "") + "；须用户确认：使用条款、候选/成品"
        print(f"  {it['file']}  [{it['type']}，{it['bytes']} 字节，{it['age_days']} 天]"
              f"  去向：{it['meta'].get('project', '待定')}{lack}" + (f"（⚠ {it['warning']}）" if it.get("warning") else ""))
    print(f"导入：{logo.cli_prefix()} import <文件> --analyze 预演，把对比图发给用户确认后再正式导入")


def check_inbox(warnings: list[str]) -> None:
    stale = [it["file"] for it in inbox_items() if it["age_days"] >= INBOX_STALE_DAYS]
    if stale:
        warnings.append(f"收件箱有 {len(stale)} 个文件放了 {INBOX_STALE_DAYS} 天以上未导入（{', '.join(stale[:3])}"
                        f"{' 等' if len(stale) > 3 else ''}）；./logo inbox 查看")


def terms_warnings(p: "logo.Project", warnings: list[str]) -> None:
    """当前发布基于使用条款未确认的导入版本时提醒（只提醒，由用户决定）。"""
    cur = p.cfg.get("state", {}).get("current_release")
    if not cur:
        return
    try:
        root = p.release_root_version(cur)
        notes = (p.version_dir(root) / "notes.md").read_text(encoding="utf-8")
    except (logo.LogoError, OSError):
        return
    if f"**使用条款**: {UNCONFIRMED}" in notes:
        warnings.append(f"{logo.rel(p.dir)}: 当前发布 {cur} 基于外部导入的 {root}，其使用条款未确认；请与用户确认版权与商用权后再交付")


def register(sub) -> None:
    s = sub.add_parser("import", help="导入外部生成的 Logo（SVG 或位图）：先 --analyze 预演，冲突逐项由用户决定")
    s.add_argument("file", help="文件路径，或收件箱 inbox/ 中的相对路径")
    s.add_argument("--project", help="加入的项目（inbox/<项目>/ 下的文件可省略）")
    s.add_argument("--analyze", action="store_true", help="只预演：生成对比图与冲突报告，不写入")
    s.add_argument("--init", action="store_true", help="以这张图初始化项目（项目尚未开始设计）：色板取自图中颜色")
    s.add_argument("--as-concept", action="store_true", help="只登记为概念图（设计依据），不建版本")
    s.add_argument("--direction", help="放入已有方向，如 A")
    s.add_argument("--new-direction", help="新建方向放入，给英文短名")
    s.add_argument("--new-direction-letter", help="新方向的字母（默认下一个可用字母）")
    s.add_argument("--title", help="新方向的中文标题")
    s.add_argument("--resolve", action="append", metavar="#RRGGBB=palette:色名|map:色名",
                   help="色板外颜色的处理（逐个问用户）：palette 改项目规范（加入色板）；map 改图片（换成色板色）")
    s.add_argument("--series-rule", choices=["exception", "update"],
                   help="系列子品牌新增颜色时：作为子品牌例外，或更新系列规则（先问用户）")
    s.add_argument("--source", help="来源：哪个工具 / Agent / 模型")
    s.add_argument("--prompt", help="生成时的提示词")
    s.add_argument("--terms", help="使用条款或版权（不清楚就写“不清楚”，会被标为未确认）")
    s.add_argument("--kind", choices=list(KINDS), help="candidate 候选 / final 成品")
    s.add_argument("--name", help="英文短名（默认取文件名）")
    s.add_argument("--note", help="用户的原话或要点（处理了冲突时必填）")
    s.set_defaults(fn=cmd_import)
    s = sub.add_parser("inbox", help="查看收件箱 inbox/ 中待导入的文件")
    s.add_argument("--json", action="store_true", help="以 JSON 输出（供 Agent 读取）")
    s.add_argument("--spec", action="store_true", help="打印来源说明文件 <文件>.import.toml 的格式")
    s.set_defaults(fn=cmd_inbox)
