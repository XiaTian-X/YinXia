"""封面模块：封面是含 Logo 的品牌物料（构图、文案、图像），与 Logo 资产分开管理。

平台与画幅登记在 config/cover-platforms.toml；封面项目在 projects/<slug>/covers/NNN-名称/。
公共函数（TOML 读取、版式换色、规格依据检查等）复用 logo.py。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import logo

PLATFORMS_TOML = logo.CONFIG / "cover-platforms.toml"
PLATFORM_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def load_config() -> dict:
    return logo.load_toml(PLATFORMS_TOML)


def pad(text: str, width: int) -> str:
    """按终端显示宽度补空格（中文等全角字符占两列）。"""
    shown = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
    return text + " " * max(width - shown, 1)


def crop_ratio(platform: dict, canvas: dict) -> float:
    """从画幅画布“铺满后居中裁切”到平台比例时，被裁掉的画布比例（0 表示比例完全一致）。"""
    rp = platform["width"] / platform["height"]
    rc = canvas["width"] / canvas["height"]
    return 1 - min(rp / rc, rc / rp)


def box_inside(box, width: int, height: int) -> bool:
    x, y, w, h = box
    return w > 0 and h > 0 and x >= 0 and y >= 0 and x + w <= width and y + h <= height


def check_platforms(errors: list[str], warnings: list[str]) -> None:
    """校验封面平台登记：结构错误报错，规格依据问题提醒。"""
    try:
        cfg = load_config()
    except logo.LogoError as e:
        errors.append(str(e))
        return
    where = logo.rel(PLATFORMS_TOML)
    ratios = cfg.get("ratios", {})
    for key, r in ratios.items():
        if not (isinstance(r.get("width"), int) and isinstance(r.get("height"), int)
                and r["width"] > 0 and r["height"] > 0):
            errors.append(f"{where} 画幅 {key}: width/height 须为正整数")
    seen = set()
    for p in cfg.get("platform", []):
        pid = p.get("id", "?")
        tag = f"{where} 平台 {pid}"
        if not PLATFORM_ID_RE.match(str(pid)):
            errors.append(f"{tag}: id 须为小写字母、数字与短横线")
        if pid in seen:
            errors.append(f"{tag}: id 重复")
        seen.add(pid)
        if not p.get("name"):
            errors.append(f"{tag}: 缺少 name")
        if p.get("ratio") not in ratios:
            errors.append(f"{tag}: 画幅 {p.get('ratio')!r} 未在 [ratios] 中定义")
        w, h = p.get("width"), p.get("height")
        if not (isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0):
            errors.append(f"{tag}: width/height 须为正整数")
            continue
        boxes = ([("safe", p["safe"])] if "safe" in p else []) + [("occlusions", b) for b in p.get("occlusions", [])]
        for name, box in boxes:
            if len(box) != 4 or not box_inside(box, w, h):
                errors.append(f"{tag}: {name} {box} 须为 [x, y, 宽, 高] 且落在 {w}x{h} 以内")
        warnings.extend(logo.spec_warnings(f"cover.{pid}", p))
    for k, v in cfg.get("rules", {}).items():
        if k not in RULE_DEFAULTS:
            errors.append(f"{where} [rules]: 未知规则 {k}（可选：{', '.join(RULE_DEFAULTS)}）")
        elif not (isinstance(v, int) and v > 0):
            errors.append(f"{where} [rules]: {k} 须为正整数")


def cmd_platforms(a) -> None:
    cfg = load_config()
    ratios = cfg.get("ratios", {})
    platforms = [p for p in cfg.get("platform", []) if not a.ratio or p["ratio"] == a.ratio]
    if a.ratio and a.ratio not in ratios:
        raise logo.LogoError(f"未知画幅：{a.ratio}（可选：{', '.join(ratios)}）")
    order = list(ratios)
    platforms.sort(key=lambda p: (order.index(p["ratio"]) if p["ratio"] in order else len(order), p["id"]))
    cols = (18, 24, 8, 12, 6, 10)
    print("".join(pad(h, w) for h, w in zip(("平台", "名称", "画幅", "尺寸", "裁切", "依据"), cols)) + "复核")
    for p in platforms:
        crop = crop_ratio(p, ratios[p["ratio"]]) if p["ratio"] in ratios else 0
        row = (p["id"], p["name"], p["ratio"], f"{p['width']}x{p['height']}", f"{crop:.0%}", p.get("basis", "official"))
        print("".join(pad(v, w) for v, w in zip(row, cols)) + p.get("verified_on", "—"))
    print(f"\n画幅：" + "；".join(f"{k} {r['width']}x{r['height']}（{r.get('label', '')}）" for k, r in ratios.items()))


# ---------------------------------------------------------------------------
# 封面项目：projects/<slug>/covers/NNN-名称/{cover.toml, cover.md, <画幅>.svg, assets/}
# ---------------------------------------------------------------------------
COVER_DIR_RE = re.compile(r"^\d{3}-[a-z0-9-]+$")
COVER_STATUSES = ("draft", "live", "archived")
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
LOGO_FREE_ATTRS = {"x", "y", "width", "height", "preserveAspectRatio"}  # 嵌入 Logo 时允许改动的根属性


def platforms_by_id() -> dict[str, dict]:
    return {p["id"]: p for p in load_config().get("platform", [])}


def needed_ratios(platform_ids: list[str]) -> list[str]:
    """目标平台需要的画幅（按 [ratios] 中的顺序）。"""
    plats = platforms_by_id()
    wanted = {plats[i]["ratio"] for i in platform_ids}
    return [k for k in load_config()["ratios"] if k in wanted]


def project_covers(p: "logo.Project") -> list[Path]:
    d = p.dir / "covers"
    return sorted(c for c in d.iterdir() if c.is_dir() and COVER_DIR_RE.match(c.name)) if d.exists() else []


def cover_dir(p: "logo.Project", ref: str) -> Path:
    """把 001 / 001-spring 解析为封面目录。"""
    hits = [c for c in project_covers(p) if c.name == ref or c.name.split("-", 1)[0] == ref]
    if len(hits) != 1:
        raise logo.LogoError(f"封面不存在：{ref}（./logo cover list {p.slug} 查看）")
    return hits[0]


def cover_cfg(cdir: Path) -> dict:
    return logo.load_toml(cdir / "cover.toml")


def write_cover_cfg(cdir: Path, cfg: dict) -> None:
    """cover.toml 由命令维护（结构固定），整体重写。"""
    c, st = cfg["cover"], cfg["state"]

    def q(v) -> str:  # TOML 基本字符串与 JSON 的转义规则兼容（引号、反斜杠等）
        return json.dumps(v, ensure_ascii=False)

    def lst(v) -> str:
        return "[" + ", ".join(q(x) for x in v) + "]"
    (cdir / "cover.toml").write_text(
        "# 封面元数据（唯一事实来源），由 ./logo cover 命令维护，请勿手改\n"
        f'[cover]\nname = {q(c["name"])}\ntitle = {q(c["title"])}\ncreated = {q(c["created"])}\n'
        f'release = {q(c["release"])}\nplatforms = {lst(c["platforms"])}\n\n'
        f'[state]\nstatus = {q(st["status"])}\nlive_on = {lst(st["live_on"])}\n'
        f'live_since = {q(st["live_since"])}\nupdated = {q(st["updated"])}\n', encoding="utf-8")


# -- 几何：平台在画布上的裁切窗口、安全区与共同安全区 ----------------------------
def crop_window(platform: dict, canvas: dict) -> tuple[float, float, float, float]:
    """平台从画布“铺满后居中裁切”时，在画布坐标中保留的窗口 (x, y, 宽, 高)。"""
    cw, ch = canvas["width"], canvas["height"]
    rp = platform["width"] / platform["height"]
    if rp >= cw / ch:
        w, h = cw, cw / rp
    else:
        w, h = ch * rp, ch
    return (cw - w) / 2, (ch - h) / 2, w, h


def map_box(box, platform: dict, window) -> tuple[float, float, float, float]:
    """把平台像素坐标中的矩形映射到画布坐标。"""
    s = window[2] / platform["width"]
    return window[0] + box[0] * s, window[1] + box[1] * s, box[2] * s, box[3] * s


def intersect(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return (x0, y0, x1 - x0, y1 - y0) if x1 > x0 and y1 > y0 else None


def common_safe(platforms: list[dict], canvas: dict):
    """所有平台的裁切窗口与安全区的交集（画布坐标）；没有交集时返回 None。"""
    area = (0, 0, canvas["width"], canvas["height"])
    for p in platforms:
        win = crop_window(p, canvas)
        area = intersect(area, map_box(p["safe"], p, win) if "safe" in p else win) if area else None
    return area


def guides_xml(platforms: list[dict], canvas: dict) -> str:
    """参考线层：各平台裁切窗口（虚线）、安全区（绿）、遮挡区（红）、共同安全区（粗绿）；导出时去掉。"""
    n = logo.fmt_num
    unit = max(canvas["width"], canvas["height"]) / 400
    out = [f'<g id="guides" fill="none" stroke-width="{n(unit)}" font-family="sans-serif" font-size="{n(unit * 9)}">']
    for i, p in enumerate(platforms):
        x, y, w, h = crop_window(p, canvas)
        out.append(f'<rect x="{n(x)}" y="{n(y)}" width="{n(w)}" height="{n(h)}" stroke="#2F6FED" '
                   f'stroke-dasharray="{n(unit * 6)} {n(unit * 4)}"/>')
        out.append(f'<text x="{n(x + w - unit * 4)}" y="{n(y + unit * 12 * (i + 1))}" fill="#2F6FED" '
                   f'stroke="none" text-anchor="end">{p["id"]}</text>')  # 右上角逐行标注，不与左上角的 Logo 重叠
        win = (x, y, w, h)
        if "safe" in p:
            sx, sy, sw, sh = map_box(p["safe"], p, win)
            out.append(f'<rect x="{n(sx)}" y="{n(sy)}" width="{n(sw)}" height="{n(sh)}" stroke="#2DBE60"/>')
        for box in p.get("occlusions", []):
            ox, oy, ow, oh = map_box(box, p, win)
            out.append(f'<rect x="{n(ox)}" y="{n(oy)}" width="{n(ow)}" height="{n(oh)}" fill="#FF0000" '
                       f'fill-opacity="0.25" stroke="#FF0000"/>')
    if (cs := common_safe(platforms, canvas)):
        x, y, w, h = cs
        out.append(f'<rect id="common-safe" x="{n(x)}" y="{n(y)}" width="{n(w)}" height="{n(h)}" '
                   f'stroke="#2DBE60" stroke-width="{n(unit * 3)}"/>')
    out.append("</g>")
    return "\n  ".join(out)


# -- Logo：从发布嵌入，指纹校验 ----------------------------------------------------
def canonical(el: ET.Element) -> str:
    """去掉允许改动的根属性后做 XML 规范化，比较时不受空白与属性顺序影响。"""
    copy = ET.fromstring(ET.tostring(el))
    for k in LOGO_FREE_ATTRS:
        copy.attrib.pop(k, None)
    return ET.canonicalize(ET.tostring(copy, encoding="unicode"), strip_text=True)


def logo_group(p: "logo.Project", ldir: Path, release: str, layout: str, mode: str,
               x: float, y: float, height: float) -> str:
    """把发布中的版式按指定配色原样嵌入为 <g id="logo">，用 transform 定位与缩放。"""
    root = ET.fromstring(logo.layout_mode_text(p, ldir, layout, mode))
    vx, vy, vw, vh = (float(v) for v in re.split(r"[\s,]+", root.get("viewBox").strip()))
    root.set("width", logo.fmt_num(vw))
    root.set("height", logo.fmt_num(vh))
    s = height / vh
    n = logo.fmt_num
    return (f'<g id="logo" data-release="{release}" data-layout="{layout}" data-mode="{mode}" '
            f'transform="translate({n(x)} {n(y)}) scale({n(s)})">' + ET.tostring(root, encoding="unicode") + "</g>")


def logo_errors(p: "logo.Project", root: ET.Element, where: str) -> list[str]:
    """封面中的 Logo 必须来自发布且未被改动（位置与缩放可以改）。"""
    groups = [el for el in root.iter(f"{{{logo.SVG_NS}}}g") if el.get("id") == "logo"]
    if len(groups) != 1:
        return [f"{where}: 须有且只有一个 <g id=\"logo\">（Logo 由 ./logo cover new / sync-logo 嵌入）"]
    g = groups[0]
    rid, layout, mode = g.get("data-release", ""), g.get("data-layout", ""), g.get("data-mode", "")
    try:
        ldir = p.release_dir(rid) / "layouts"
    except logo.LogoError:
        return [f"{where}: Logo 标注的发布 {rid!r} 不存在"]
    if layout not in logo.LOGO_LAYOUTS or not (ldir / f"{layout}.svg").exists():
        return [f"{where}: Logo 标注的版式 {layout!r} 不在 {rid} 中"]
    inner = list(g)
    try:
        expected = ET.fromstring(logo.layout_mode_text(p, ldir, layout, mode))
    except (logo.LogoError, ET.ParseError) as e:
        return [f"{where}: 无法读取 {rid}/{layout}（{mode}）：{e}"]
    if len(inner) != 1 or canonical(inner[0]) != canonical(expected):
        return [f"{where}: Logo 与 {rid}/{layout}.svg（{mode}）不一致——Logo 只能移动、缩放，不能改动内部；"
                f"如需更新请运行 ./logo cover sync-logo"]
    return []


# -- 设计稿规则 --------------------------------------------------------------------
def check_cover_svg(path: Path, canvas: dict, assets: set[str], errors: list[str], staged: bool) -> ET.Element | None:
    """封面设计稿规则：画布尺寸固定；位图只能引用已登记素材；颜色不限；文字须转曲（参考线层除外）；
    禁止脚本、事件属性与外部资源。返回解析后的根元素。"""
    r = logo.rel(path)
    data = logo.staged_bytes(path) if staged else None
    try:
        root = ET.fromstring(data) if data is not None else ET.parse(path).getroot()
    except ET.ParseError as e:
        errors.append(f"{r}: XML 解析失败：{e}")
        return None
    vb = [float(v) for v in re.split(r"[\s,]+", (root.get("viewBox") or "").strip()) if v]
    if vb != [0, 0, canvas["width"], canvas["height"]]:
        errors.append(f"{r}: viewBox 须为 0 0 {canvas['width']} {canvas['height']}（该画幅的画布）")
    if root.get("width") or root.get("height"):
        errors.append(f"{r}: 根元素不要写死 width/height，只用 viewBox")

    def walk(el: ET.Element, in_guides: bool) -> None:
        tag = el.tag.split("}")[-1]
        guides = in_guides or el.get("id") == "guides"
        if tag in ("script", "foreignObject"):
            errors.append(f"{r}: 禁止 <{tag}>")
        if tag in ("text", "tspan", "textPath") and not guides:
            errors.append(f"{r}: <{tag}> 文字须转曲（./logo outline）；只有参考线层可以用文字标注")
        for attr, val in el.attrib.items():
            name = attr.split("}")[-1]
            if name.lower().startswith("on"):
                errors.append(f"{r}: <{tag} {name}> 禁止事件属性")
            elif logo.EXTERNAL_CSS_RE.search(val):
                errors.append(f"{r}: <{tag} {name}> 禁止引用外部资源")
            elif name == "href" and not val.startswith("#"):
                if tag != "image":
                    errors.append(f"{r}: 禁止外部引用 {val}")
                elif val not in assets:
                    errors.append(f"{r}: 位图 {val} 未登记（./logo cover asset 登记来源与授权后再引用）")
        if tag == "style" and el.text and logo.EXTERNAL_CSS_RE.search(el.text):
            errors.append(f"{r}: <style> 禁止 @import 或引用外部资源")
        for child in el:
            walk(child, guides)

    walk(root, False)
    return root


def check_covers(p: "logo.Project", errors: list[str], warnings: list[str], staged: bool = False) -> None:
    """检查项目的全部封面。"""
    try:
        load_config()
    except logo.LogoError:
        return  # 平台登记的问题由 check_platforms 报告
    for cdir in project_covers(p):
        check_cover(p, cdir, errors, warnings, staged)


def check_cover(p: "logo.Project", cdir: Path, errors: list[str], warnings: list[str], staged: bool = False) -> None:
    """检查一个封面：元数据、画幅稿规则、Logo 指纹、素材登记，以及校对提醒（提交检查与导出共用）。"""
    plats, ratios = platforms_by_id(), load_config()["ratios"]
    where = logo.rel(cdir)
    try:
        cfg = cover_cfg(cdir)
        c, st = cfg["cover"], cfg["state"]
        targets = list(c["platforms"])
    except (logo.LogoError, KeyError) as e:
        errors.append(f"{where}/cover.toml 无效：{e}")
        return
    unknown = [t for t in targets if t not in plats]
    if unknown:
        errors.append(f"{where}/cover.toml: 未登记的平台 {', '.join(unknown)}（./logo cover platforms 查看）")
        return
    if st.get("status") not in COVER_STATUSES:
        errors.append(f"{where}/cover.toml: status 须为 {'/'.join(COVER_STATUSES)}")
    if not set(st.get("live_on", [])) <= set(targets):
        errors.append(f"{where}/cover.toml: live_on 只能是目标平台的子集")
    try:
        p.release_dir(c.get("release", ""))
    except logo.LogoError:
        errors.append(f"{where}/cover.toml: 所用 Logo 发布 {c.get('release')!r} 不存在")
    # 素材：每张图须有登记记录，且内容与登记的 sha256 一致
    assets: set[str] = set()
    adir = cdir / "assets"
    for img in sorted(adir.glob("*")) if adir.exists() else []:
        if img.suffix.lower() not in ASSET_SUFFIXES:
            continue
        md = img.with_suffix(".md")
        if not md.exists():
            errors.append(f"{logo.rel(img)}: 素材未登记（缺少 {md.name}；用 ./logo cover asset 登记）")
            continue
        m = re.search(r"sha256\*\*[:：]\s*([0-9a-f]{64})", md.read_text(encoding="utf-8"))
        if not m or m.group(1) != logo.sha256_file(img):
            errors.append(f"{logo.rel(img)}: 内容与登记的 sha256 不符（素材登记后不可替换，换图请重新登记）")
            continue
        assets.add(f"assets/{img.name}")
    need = needed_ratios(targets)
    for key in need:
        if not (cdir / f"{key}.svg").exists():
            errors.append(f"{where}: 缺少画幅稿 {key}.svg（目标平台需要）")
    for svg in sorted(cdir.glob("*.svg")):  # 目录里的每一份设计稿都按规则检查，不只是目标平台需要的
        if svg.stem not in ratios:
            errors.append(f"{logo.rel(svg)}: {svg.stem} 不是已登记的画幅（可选：{', '.join(ratios)}）")
            continue
        if svg.stem not in need:
            warnings.append(f"{logo.rel(svg)}: 当前目标平台不需要画幅 {svg.stem}"
                            f"（若是为新平台预备，可用 ./logo cover retarget 加入目标平台）")
        root = check_cover_svg(svg, ratios[svg.stem], assets, errors, staged)
        if root is not None:
            found = logo_errors(p, root, logo.rel(svg))
            errors.extend(found)
            g = next((el for el in root.iter() if el.get("id") == "logo"), None)
            if not found and g is not None and g.get("data-release") != c.get("release"):
                errors.append(f"{logo.rel(svg)}: Logo 标注的发布 {g.get('data-release')} 与 cover.toml 记录的 "
                              f"{c.get('release')} 不一致（请用 ./logo cover sync-logo 更新，不要手改）")
    if not any(e.startswith(where) for e in errors):  # 已有结构错误时，渲染类提醒不可靠，跳过
        warnings.extend(cover_warnings(p, cdir))


# -- 渲染：去掉参考线，按平台“铺满后居中裁切”（预览与导出共用同一套） ----------------
def design_root(cdir: Path, ratio: str) -> ET.Element:
    """读入画幅设计稿并去掉参考线层。"""
    root = ET.parse(cdir / f"{ratio}.svg").getroot()
    for parent in list(root.iter()):
        for child in list(parent):
            if child.get("id") == "guides":
                parent.remove(child)
    return root


def platform_svg(root: ET.Element, platform: dict, overlay: bool = False) -> str:
    """把（已去参考线的）设计稿装进平台尺寸的外框，按比例铺满后居中裁切；overlay 时叠加安全区与遮挡区。"""
    w, h = platform["width"], platform["height"]
    inner = ET.fromstring(ET.tostring(root))
    for k, v in (("x", "0"), ("y", "0"), ("width", str(w)), ("height", str(h)),
                 ("preserveAspectRatio", "xMidYMid slice")):
        inner.set(k, v)
    outer = ET.Element(f"{{{logo.SVG_NS}}}svg", {"viewBox": f"0 0 {w} {h}"})
    outer.append(inner)
    if overlay:
        stroke = str(max(w, h) / 300)
        for box in platform.get("occlusions", []):
            ET.SubElement(outer, f"{{{logo.SVG_NS}}}rect", {
                "x": str(box[0]), "y": str(box[1]), "width": str(box[2]), "height": str(box[3]),
                "fill": "#FF0000", "fill-opacity": "0.35", "stroke": "#FF0000", "stroke-width": stroke})
        if "safe" in platform:
            x, y, bw, bh = platform["safe"]
            ET.SubElement(outer, f"{{{logo.SVG_NS}}}rect", {
                "x": str(x), "y": str(y), "width": str(bw), "height": str(bh),
                "fill": "none", "stroke": "#2DBE60", "stroke-width": stroke})
    return ET.tostring(outer, encoding="unicode")


def render_platform(cdir: Path, root: ET.Element, platform: dict, out: Path, width: int | None = None,
                    overlay: bool = False) -> Path:
    """渲染某平台的成品（默认平台像素尺寸；width 给出时按宽度缩放，用于缩略图）。"""
    return logo.render_text(platform_svg(root, platform, overlay), out, resources=cdir,
                            width=width or platform["width"], height=None if width else platform["height"])


def has_transparency(png: Path) -> bool:
    val = logo.run(["magick", str(png), "-alpha", "extract", "-format", "%[fx:minima]", "info:"], capture=True)
    return float(val.strip() or 1) < 0.99


def cover_targets(p: "logo.Project", cdir: Path, names: list[str]) -> tuple[dict, list[dict]]:
    """校验封面设计稿（违反规则时拒绝），返回 (cover.toml, 要处理的平台)。"""
    errors: list[str] = []
    warnings: list[str] = []
    check_cover(p, cdir, errors, warnings)
    if errors:
        raise logo.LogoError("封面未通过检查：\n  " + "\n  ".join(errors))
    for w in warnings:
        print(f"⚠ {w}")
    cfg = cover_cfg(cdir)
    targets = cfg["cover"]["platforms"]
    bad = [n for n in names if n not in targets]
    if bad:
        raise logo.LogoError(f"不是该封面的目标平台：{', '.join(bad)}（目标：{', '.join(targets)}）")
    plats = platforms_by_id()
    return cfg, [plats[t] for t in (names or targets)]


def cmd_preview(a) -> None:
    p = logo.Project(a.slug)
    cdir = cover_dir(p, a.cover)
    _, platforms = cover_targets(p, cdir, a.platforms)
    out_dir = logo.CACHE / p.slug / "covers" / cdir.name
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    rows, order = [], []
    for ratio in [r for r in load_config()["ratios"] if any(pl["ratio"] == r for pl in platforms)]:
        on_ratio = [pl for pl in platforms if pl["ratio"] == ratio]
        root = design_root(cdir, ratio)
        canvas = logo.render(cdir / f"{ratio}.svg", out_dir / f"{ratio}-canvas.png", width=900, resources=cdir)
        crops, thumbs = [], []
        for pl in on_ratio:
            crops.append(render_platform(cdir, root, pl, out_dir / f"{pl['id']}.png", width=600, overlay=True))
            thumbs.append(render_platform(cdir, root, pl, out_dir / f"{pl['id']}-thumb.png", width=thumb_width(pl)))
        row = out_dir / f"row-{ratio}.png"
        logo.run(["magick", "montage", str(canvas), *map(str, crops), *map(str, thumbs), "-tile",
                  f"{1 + 2 * len(on_ratio)}x", "-geometry", "+8+8", "-background", "#DDDDDD", str(row)])
        rows.append(row)
        order.append(f"{ratio}：画布 | " + " | ".join(f"{pl['id']} 裁切" for pl in on_ratio)
                     + " | " + " | ".join(f"{pl['id']} 缩略图" for pl in on_ratio))
    sheet = out_dir / "preview.png"
    logo.run(["magick", *map(str, rows), "-background", "#BBBBBB", "-gravity", "north", "-splice", "0x6",
              "-gravity", "west", "-append", str(sheet)])  # 分隔条加在每行顶部，各行左对齐
    print(f"预览：{logo.rel(sheet)}（单张图在同一目录）")
    print("每行一个画幅，从左到右：" + "\n  ".join([""] + order))
    print(f"红色为平台遮挡区（头像、按钮等），绿框为平台安全区；缩略图为手机上的常见显示宽度"
          f"（横幅 {rules()['thumb_width_landscape']}px、竖版/方形 {rules()['thumb_width_portrait']}px）")
    for w in stale_exports(p, cdir, cover_cfg(cdir)):
        print(f"⚠ {w}")


def cmd_export(a) -> None:
    p = logo.Project(a.slug)
    cdir = cover_dir(p, a.cover)
    cfg, platforms = cover_targets(p, cdir, a.platforms)
    dist = p.dir / "dist" / "covers" / cdir.name
    dist.mkdir(parents=True, exist_ok=True)
    old = read_manifest(dist)
    roots: dict[str, ET.Element] = {}
    written, warnings = [], []
    origin = {"release": cfg["cover"]["release"], "git_head": logo.short_head("projects"),
              "system_head": logo.short_head("system"), "renderer": logo.resvg_version()}
    # 暂存目录建在目标旁边（同一磁盘），os.replace 才能原子替换；系统临时目录可能在另一块磁盘上
    with tempfile.TemporaryDirectory(dir=dist.parent, prefix=".build-") as td:
        staged = []
        for pl in platforms:  # 全部渲染成功后再逐个替换，失败不动已有产物
            root = roots.setdefault(pl["ratio"], design_root(cdir, pl["ratio"]))
            name = f"{pl['id']}-{pl['width']}x{pl['height']}.png"
            staged.append((render_platform(cdir, root, pl, Path(td) / name), dist / name))
        records = {}
        for (tmp, final), pl in zip(staged, platforms):
            os.replace(tmp, final)
            written.append(final.name)
            design = f"{pl['ratio']}.svg"
            records[final.name] = {"sha256": logo.sha256_file(final), "platform": pl["id"], "design": design,
                                   "design_sha256": logo.sha256_file(cdir / design), **origin}
            if has_transparency(final):
                warnings.append(f"{final.name} 含透明区域：多数平台会以黑或白显示透明处，封面通常应铺满底色")
    # 每张图记录自己的来源：只导出部分平台时，其余图保留上次导出时的记录（文件仍在且未被改动）
    files = {}
    for f in sorted(f for f in dist.iterdir() if f.is_file() and f.suffix == ".png"):
        prev = old.get(f.name)
        if f.name in records:
            files[f.name] = records[f.name]
        elif prev and prev.get("sha256") == logo.sha256_file(f):
            files[f.name] = prev
        else:
            files[f.name] = {"sha256": logo.sha256_file(f), "design": None}  # 来源不明（旧格式或被改动）
    manifest = {"project": p.slug, "cover": cdir.name, "title": cfg["cover"]["title"], "files": files}
    (dist / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已导出 {', '.join(written)} → {logo.rel(dist)}（不提交）")
    for w in warnings + stale_exports(p, cdir, cfg):
        print(f"⚠ {w}")


def read_manifest(dist: Path) -> dict[str, dict]:
    """读取已导出封面的逐张记录 {文件名: 来源}；没有或无法解析时返回空。旧格式（文件名 → sha256）视为来源不明。"""
    try:
        files = json.loads((dist / "manifest.json").read_text(encoding="utf-8")).get("files", {})
    except (OSError, ValueError, AttributeError):
        return {}
    return {k: (v if isinstance(v, dict) else {"sha256": v, "design": None}) for k, v in files.items()}


def stale_exports(p: "logo.Project", cdir: Path, cfg: dict) -> list[str]:
    """dist 中由旧版设计稿导出、来源不明或已不是目标平台的图（只提醒）。"""
    dist = p.dir / "dist" / "covers" / cdir.name
    targets = set(cfg["cover"]["platforms"])
    out = []
    for name, rec in read_manifest(dist).items():
        if not (dist / name).exists():
            continue
        design = rec.get("design")
        if rec.get("platform") and rec["platform"] not in targets:
            out.append(f"{logo.rel(dist / name)}：{rec['platform']} 已不是该封面的目标平台，可删除")
        elif not design:
            out.append(f"{logo.rel(dist / name)}：来源不明（旧版导出记录），请重新导出")
        elif not (cdir / design).exists() or logo.sha256_file(cdir / design) != rec.get("design_sha256"):
            out.append(f"{logo.rel(dist / name)}：由旧版设计稿 {design} 导出，设计稿已修改，请重新导出")
    return out


# -- 校对辅助：关键内容越界、Logo 过小、Logo 过时（只提醒）与 sync-logo ---------------
RULE_DEFAULTS = {"min_logo_display_px": 16, "thumb_width_landscape": 360, "thumb_width_portrait": 180}
KEY_GROUPS = (("logo", "Logo"), ("content", "关键内容"))


def rules() -> dict:
    return {**RULE_DEFAULTS, **load_config().get("rules", {})}


def thumb_width(platform: dict) -> int:
    """手机上的常见显示宽度：横幅用 thumb_width_landscape，竖版与方形用 thumb_width_portrait。"""
    r = rules()
    return r["thumb_width_landscape"] if platform["width"] > platform["height"] else r["thumb_width_portrait"]


def keep_only(root: ET.Element, element_id: str) -> ET.Element | None:
    """复制设计稿，只保留指定分组（及 defs / style）；分组不存在或为空时返回 None。"""
    copy = ET.fromstring(ET.tostring(root))
    target = next((el for el in copy.iter() if el.get("id") == element_id), None)
    if target is None or not [c for c in target if not callable(c.tag)]:
        return None
    keep = set(target.iter())
    parents = {c: par for par in copy.iter() for c in par}
    node = target
    while node in parents:
        node = parents[node]
        keep.add(node)
    for par in list(copy.iter()):
        for child in list(par):
            if child not in keep and child.tag.split("}")[-1] not in ("defs", "style"):
                par.remove(child)
    return copy


def masked_alpha(png: Path, size: tuple[int, int], rects: list, inside: bool) -> float:
    """rects 之内（inside=True）或之外（False）的最大不透明度；边界按抗锯齿各放宽 1px。"""
    w, h = size
    fill, draw = ("black", "white") if inside else ("white", "black")
    pad = -1 if inside else 1
    cmd = ["magick", "-size", f"{w}x{h}", f"xc:{fill}", "-fill", draw]
    for x, y, rw, rh in rects:
        cmd += ["-draw", f"rectangle {x - pad},{y - pad} {x + rw + pad},{y + rh + pad}"]
    with tempfile.TemporaryDirectory() as td:
        mask = Path(td) / "mask.png"
        logo.run(cmd + [str(mask)])
        val = logo.run(["magick", str(png), "-alpha", "extract", str(mask), "-compose", "multiply",
                        "-composite", "-format", "%[fx:maxima]", "info:"], capture=True)
    return float(val.strip() or 0)


def geometry_warnings(cdir: Path, ratio: str, platforms: list[dict]) -> list[str]:
    """按平台检查关键内容（Logo、content）是否会被裁掉、超出安全区或被遮挡，以及 Logo 在手机上的显示高度。"""
    canvas = load_config()["ratios"][ratio]
    svg = cdir / f"{ratio}.svg"
    text = svg.read_text(encoding="utf-8")
    key = "cover-geometry-v1|" + json.dumps([text, canvas, platforms, rules()], sort_keys=True, ensure_ascii=False)
    # 照片不影响结论（只渲染 Logo 与 content），所以只按设计稿文本、画布、平台与规则缓存
    return logo.cached(key, lambda: _geometry_warnings(cdir, svg, canvas, platforms))


def _geometry_warnings(cdir: Path, svg: Path, canvas: dict, platforms: list[dict]) -> list[str]:
    out: list[str] = []
    where = logo.rel(svg)
    root = ET.parse(svg).getroot()
    cw, ch = canvas["width"], canvas["height"]
    rw, rh = (800, round(800 * ch / cw)) if cw >= ch else (round(800 * cw / ch), 800)
    s = rw / cw  # 渲染像素 / 画布单位
    with tempfile.TemporaryDirectory() as td:
        for gid, label in KEY_GROUPS:
            part = keep_only(root, gid)
            if part is None:
                continue
            png = logo.render_text(ET.tostring(part, encoding="unicode"), Path(td) / f"{gid}.png", resources=cdir,
                                   width=rw, height=rh)
            if masked_alpha(png, (rw, rh), [], inside=False) < 0.5:
                continue  # 完全透明（如空分组）
            for pl in platforms:
                win = crop_window(pl, canvas)
                allowed = intersect(win, map_box(pl["safe"], pl, win)) if "safe" in pl else win
                px = [(v * s) for v in allowed] if allowed else [0, 0, 0, 0]
                if masked_alpha(png, (rw, rh), [px], inside=False) > 0.5:
                    out.append(f"{where}：{label}在 {pl['id']} 上会被裁掉或超出安全区")
                occ = [[v * s for v in map_box(b, pl, win)] for b in pl.get("occlusions", [])]
                if occ and masked_alpha(png, (rw, rh), occ, inside=True) > 0.5:
                    out.append(f"{where}：{label}在 {pl['id']} 上会被遮挡（平台头像、按钮等区域）")
            if gid == "logo":
                trim = logo.run(["magick", str(png), "-trim", "-format", "%h", "info:"], capture=True).strip()
                logo_h = float(trim or 0) / s  # Logo 实际高度（画布单位）
                for pl in platforms:
                    win = crop_window(pl, canvas)
                    shown = logo_h * thumb_width(pl) / win[2]
                    if shown < rules()["min_logo_display_px"]:
                        out.append(f"{where}：Logo 在 {pl['id']} 手机显示（{thumb_width(pl)}px 宽）中只有约 "
                                   f"{shown:.0f}px 高，低于 {rules()['min_logo_display_px']}px，可能认不出")
    return out


def outdated_warning(p: "logo.Project", cdir: Path, cfg: dict) -> str | None:
    """封面使用的 Logo 发布不是项目当前发布时提醒；上线中的优先、已下线的不提醒。"""
    cur = p.cfg["state"].get("current_release")
    used, st = cfg["cover"]["release"], cfg["state"]
    if not cur or used == cur or st["status"] == "archived":
        return None
    if st["status"] == "live":
        return (f"{logo.rel(cdir)}：上线中的封面（{', '.join(st['live_on']) or '—'}）仍在使用旧 Logo {used}，"
                f"当前发布为 {cur}；运行 ./logo cover sync-logo {p.slug} {cdir.name[:3]}")
    return f"{logo.rel(cdir)}：使用的 Logo 发布 {used} 不是当前发布 {cur}；可运行 ./logo cover sync-logo"


def cover_warnings(p: "logo.Project", cdir: Path) -> list[str]:
    """一个封面的校对提醒（越界、Logo 过小、Logo 过时）；check、preview、export 共用。"""
    cfg = cover_cfg(cdir)
    out = []
    if (w := outdated_warning(p, cdir, cfg)):
        out.append(w)
    plats = platforms_by_id()
    for ratio in needed_ratios(cfg["cover"]["platforms"]):
        on_ratio = [plats[t] for t in cfg["cover"]["platforms"] if plats[t]["ratio"] == ratio]
        try:
            out.extend(geometry_warnings(cdir, ratio, on_ratio))
        except logo.LogoError as e:
            out.append(f"{logo.rel(cdir / (ratio + '.svg'))}: 无法完成越界检查：{e}")
    return out


def group_span(text: str, group_id: str) -> tuple[int, int]:
    """在设计稿文本中定位 <g id="…">…</g> 的起止位置（按 <g> 的嵌套层级配对），用于逐字保留其余内容的替换。"""
    m = re.search(rf'<g\b[^>]*\bid="{re.escape(group_id)}"[^>]*>', text)
    if not m:
        raise logo.LogoError(f'找不到 <g id="{group_id}">')
    depth, pos = 1, m.end()
    for tag in re.finditer(r"<g\b[^>]*?(/?)>|</g\s*>", text[pos:]):
        if tag.group(0).startswith("</"):
            depth -= 1
        elif not tag.group(1):
            depth += 1
        if depth == 0:
            return m.start(), pos + tag.end()
    raise logo.LogoError(f'<g id="{group_id}"> 没有闭合')


def cmd_sync_logo(a) -> None:
    p = logo.Project(a.slug)
    cdir = cover_dir(p, a.cover)
    cfg = cover_cfg(cdir)
    rid = a.release or p.cfg["state"].get("current_release")
    ldir = release_layouts(p, rid)
    if cfg["cover"]["release"] == rid:
        print(f"{cdir.name} 已在使用 {rid}，无需同步")
        return
    new_texts, report = {}, []
    ratios = load_config()["ratios"]
    for svg in sorted(s for s in cdir.glob("*.svg") if s.stem in ratios):  # 含预备中的画幅，免得发布不一致
        ratio = svg.stem
        text = svg.read_text(encoding="utf-8")
        a0, a1 = group_span(text, "logo")
        old = ET.fromstring(text[a0:a1])
        layout = old.get("data-layout", "")
        if layout not in logo.LOGO_LAYOUTS or not (ldir / f"{layout}.svg").exists():
            layout = logo.mark_layout(ldir)
        mode = old.get("data-mode", "color")
        inner = list(old)[0] if len(old) else None
        old_h = float(inner.get("height")) if inner is not None and inner.get("height") else None
        root = ET.fromstring(logo.layout_mode_text(p, ldir, layout, mode))
        vx, vy, vw, vh = (float(v) for v in re.split(r"[\s,]+", root.get("viewBox").strip()))
        h = old_h or vh  # 保持原来的显示高度，宽度随新 Logo 的比例变化
        root.set("width", logo.fmt_num(h * vw / vh))
        root.set("height", logo.fmt_num(h))
        opening = re.match(r"<g\b[^>]*>", text[a0:a1]).group(0)
        opening = re.sub(r'data-release="[^"]*"', f'data-release="{rid}"', opening)
        opening = re.sub(r'data-layout="[^"]*"', f'data-layout="{layout}"', opening)
        new_texts[svg] = text[:a0] + opening + ET.tostring(root, encoding="unicode") + "</g>" + text[a1:]
        report.append(f"{ratio}：{old.get('data-release')}/{old.get('data-layout')} → {rid}/{layout}（{mode}）")
    for svg, text in new_texts.items():  # 全部生成成功后再写回
        svg.write_text(text, encoding="utf-8")
    cfg["cover"]["release"] = rid
    cfg["state"]["updated"] = logo.today()
    write_cover_cfg(cdir, cfg)
    print(f"已同步 {cdir.name} 的 Logo 到 {rid}：\n  " + "\n  ".join(report))
    for w in cover_warnings(p, cdir):
        print(f"⚠ {w}")
    print(f"请运行 ./logo cover preview {p.slug} {cdir.name[:3]} 查看效果后提交")


# -- 命令 --------------------------------------------------------------------------
def logo_fill(svg_text: str) -> float:
    """Logo 图形实际占自身 viewBox 高度的比例（版式常带留白，按 viewBox 估算会偏大），结果按内容缓存。"""
    def compute() -> float:
        with tempfile.TemporaryDirectory() as td:
            png = logo.render_text(svg_text, Path(td) / "l.png", 400)
            full, trimmed = (float(logo.run(["magick", str(png), *extra, "-format", "%h", "info:"],
                                            capture=True).strip() or 0) for extra in ([], ["-trim"]))
        return min(trimmed / full, 1.0) if full and trimmed > 1 else 1.0
    return logo.cached(f"logo-fill-v1|{svg_text}", compute)


def place_logo(on_ratio: list[dict], canvas: dict, aspect: float, fill: float):
    """起稿时 Logo 的位置与大小 (x, y, 高) 及提示：在共同安全区内、避开遮挡区，并在各平台手机显示中不低于最小高度。"""
    safe = common_safe(on_ratio, canvas) or (0, 0, canvas["width"], canvas["height"])
    need_h = max(rules()["min_logo_display_px"] * 1.1 * crop_window(pl, canvas)[2] / thumb_width(pl)
                 for pl in on_ratio) / fill
    want = max(safe[3] * 0.15, need_h)
    for hmax, wmax in ((0.5, 0.6), (0.8, 0.9)):  # 常规上限；不够最小显示高度时放宽
        lh = min(want, safe[3] * hmax, safe[2] * wmax / aspect)
        if lh >= need_h:
            break
    lw = lh * aspect
    inset_x, inset_y = min(lh * 0.25, (safe[2] - lw) / 2), min(lh * 0.25, (safe[3] - lh) / 2)
    left, center, right = safe[0] + inset_x, safe[0] + (safe[2] - lw) / 2, safe[0] + safe[2] - inset_x - lw
    y = safe[1] + inset_y
    occ = [map_box(b, pl, crop_window(pl, canvas)) for pl in on_ratio for b in pl.get("occlusions", [])]
    candidates = (center, left, right) if canvas["height"] > canvas["width"] else (left, right, center)
    x = next((c for c in candidates if not any(intersect((c, y, lw, lh), o) for o in occ)), candidates[0])
    note = None
    if lh < need_h:
        shown = min(lh * fill * thumb_width(pl) / crop_window(pl, canvas)[2] for pl in on_ratio)
        note = (f"画幅太窄，Logo 放到最大在手机显示中仍只有约 {shown:.0f}px 高（低于 "
                f"{rules()['min_logo_display_px']}px）；建议在发布中提供更紧凑的 icon.svg")
    return x, y, lh, note


def starter_svg(p: "logo.Project", ldir: Path, rid: str, layout: str, mode: str, on_ratio: list[dict],
                canvas: dict) -> tuple[str, str | None]:
    """某画幅的起稿：品牌底色、按规则摆放的 Logo、空的 content 分组与参考线层。返回 (SVG 文本, 提示)。"""
    try:
        bg = logo.app_background(p)
    except logo.LogoError:
        bg = next(iter(p.palette.values()), "#FFFFFF")
    text = logo.layout_mode_text(p, ldir, layout, mode)
    vb = [float(v) for v in re.split(r"[\s,]+", ET.fromstring(text).get("viewBox").strip())]
    x, y, lh, note = place_logo(on_ratio, canvas, vb[2] / vb[3], logo_fill(text))
    return (f'<svg xmlns="{logo.SVG_NS}" xmlns:xlink="{logo.XLINK_NS}" viewBox="0 0 {canvas["width"]} {canvas["height"]}">\n'
            f'  <rect id="background" width="{canvas["width"]}" height="{canvas["height"]}" fill="{bg}"/>\n'
            f'  {logo_group(p, ldir, rid, layout, mode, x, y, lh)}\n'
            f'  <g id="content">\n    <!-- 关键内容（标题、副标题、行动号召）放在这里，须在共同安全区内 -->\n  </g>\n'
            f'  {guides_xml(on_ratio, canvas)}\n</svg>\n'), note


def parse_platforms(spec: str) -> list[str]:
    targets = list(dict.fromkeys(t.strip() for t in spec.split(",") if t.strip()))
    unknown = [t for t in targets if t not in platforms_by_id()]
    if not targets or unknown:
        raise logo.LogoError(f"未登记的平台：{', '.join(unknown) or '（空）'}（./logo cover platforms 查看）")
    return targets


def release_layouts(p: "logo.Project", rid: str) -> Path:
    ldir = p.release_dir(rid) / "layouts"
    if not (ldir / "primary.svg").exists():
        raise logo.LogoError(f"{rid} 缺少主版 primary.svg")
    return ldir


def cmd_new(a) -> None:
    p = logo.Project(a.slug)
    targets = parse_platforms(a.platforms)
    plats = platforms_by_id()
    rid = a.release or p.cfg["state"].get("current_release")
    if not rid:
        raise logo.LogoError("项目还没有 Logo 发布；先 ./logo release，或用 --release 指定")
    ldir = release_layouts(p, rid)
    nums = [int(c.name[:3]) for c in project_covers(p)]
    cid = f"{max(nums, default=0) + 1:03d}-{logo.to_slug(a.name)}"
    cdir = p.dir / "covers" / cid
    ratios = load_config()["ratios"]
    need = needed_ratios(targets)
    layout = logo.mark_layout(ldir)
    texts, notes = {}, []
    for key in need:  # 先全部生成，成功后再落盘
        on_ratio = [plats[t] for t in targets if plats[t]["ratio"] == key]
        texts[key], note = starter_svg(p, ldir, rid, layout, logo.icon_mode(p), on_ratio, ratios[key])
        if note:
            notes.append(f"{key}.svg：{note}")
    cdir.mkdir(parents=True)
    (cdir / "assets").mkdir()
    cfg = {"cover": {"name": logo.to_slug(a.name), "title": a.title or a.name, "created": logo.today(),
                     "release": rid, "platforms": targets},
           "state": {"status": "draft", "live_on": [], "live_since": "", "updated": logo.today()}}
    write_cover_cfg(cdir, cfg)
    logo.write_new(cdir / "cover.md", logo.render_template(
        "cover.md", id=cid, title=a.title or a.name, platforms="、".join(plats[t]["name"] for t in targets),
        release=rid, date=logo.today()))
    for key, text in texts.items():
        (cdir / f"{key}.svg").write_text(text, encoding="utf-8")
    (p.dir / "covers" / ".gitkeep").unlink(missing_ok=True)
    print(f"已创建封面：{logo.rel(cdir)}（画幅：{', '.join(need)}；Logo：{rid}/{layout}）")
    for n in notes:
        print(f"⚠ {n}")
    print("下一步：填写 cover.md；在主视觉画幅定稿后延展到其他画幅；关键内容放进 <g id=\"content\">")


def cmd_retarget(a) -> None:
    """修改封面的目标平台：新需要的画幅起稿，共用画幅的参考线按新平台组合重画，其余内容逐字保留。"""
    p = logo.Project(a.slug)
    cdir = cover_dir(p, a.cover)
    cfg = cover_cfg(cdir)
    old, new = list(cfg["cover"]["platforms"]), parse_platforms(a.platforms)
    if new == old:
        print(f"{cdir.name} 的目标平台未变化")
        return
    live = [t for t in cfg["state"]["live_on"] if t not in new]
    if live:
        raise logo.LogoError(f"{', '.join(live)} 正在上线，不能移除；先用 ./logo cover status 调整上线平台")
    plats, ratios = platforms_by_id(), load_config()["ratios"]
    rid = cfg["cover"]["release"]
    ldir = release_layouts(p, rid)
    layout, mode = logo.mark_layout(ldir), logo.icon_mode(p)
    for svg in sorted(s for s in cdir.glob("*.svg") if s.stem in ratios):  # 沿用已有设计稿中 Logo 的版式与配色
        try:
            g = next((el for el in ET.parse(svg).getroot().iter() if el.get("id") == "logo"), None)
        except ET.ParseError:
            continue  # 解析失败由 check 报告
        if g is not None and g.get("data-layout") in logo.LOGO_LAYOUTS and (ldir / f"{g.get('data-layout')}.svg").exists():
            layout, mode = g.get("data-layout"), g.get("data-mode") or mode
            break
    texts, report, notes = {}, [], []
    for key in needed_ratios(new):
        on_ratio = [plats[t] for t in new if plats[t]["ratio"] == key]
        svg = cdir / f"{key}.svg"
        if not svg.exists():
            texts[svg], note = starter_svg(p, ldir, rid, layout, mode, on_ratio, ratios[key])
            report.append(f"{key}.svg：新起稿")
            if note:
                notes.append(f"{key}.svg：{note}")
            continue
        text = svg.read_text(encoding="utf-8")
        guides = guides_xml(on_ratio, ratios[key])
        try:
            g0, g1 = group_span(text, "guides")
            updated = text[:g0] + guides + text[g1:]
        except logo.LogoError:  # 参考线层被删掉时补回
            end = text.rindex("</svg>")
            updated = text[:end] + "  " + guides + "\n" + text[end:]
        if updated != text:
            texts[svg] = updated
            report.append(f"{key}.svg：参考线按 {', '.join(pl['id'] for pl in on_ratio)} 更新")
    for svg, text in texts.items():  # 全部生成成功后再写回
        svg.write_text(text, encoding="utf-8")
    cfg["cover"]["platforms"] = new
    cfg["state"]["updated"] = logo.today()
    write_cover_cfg(cdir, cfg)
    md = cdir / "cover.md"  # 只追加文件：在末尾记录调整
    body = md.read_text(encoding="utf-8")
    md.write_text(body.rstrip("\n") + f"\n- {logo.today()} 目标平台调整：{', '.join(old)} → {', '.join(new)}\n",
                  encoding="utf-8")
    unused = [s.name for s in sorted(cdir.glob("*.svg")) if s.stem not in needed_ratios(new)]
    print(f"已调整 {cdir.name} 的目标平台：{', '.join(old)} → {', '.join(new)}")
    for line in report:
        print(f"  {line}")
    for n in notes:
        print(f"⚠ {n}")
    if unused:
        print(f"不再需要的画幅稿已保留：{', '.join(unused)}（确认不用后可删除）")
    print(f"请运行 ./logo cover preview {p.slug} {cdir.name[:3]} 查看效果后提交")


def cmd_asset(a) -> None:
    p = logo.Project(a.slug)
    cdir = cover_dir(p, a.cover)
    src = Path(a.image)
    if not src.exists():
        raise logo.LogoError(f"图片不存在：{src}")
    if src.suffix.lower() not in ASSET_SUFFIXES:
        raise logo.LogoError(f"素材只支持 {', '.join(sorted(ASSET_SUFFIXES))}，收到：{src.suffix}")
    adir = cdir / "assets"
    adir.mkdir(exist_ok=True)
    nums = [int(f.name[:3]) for f in adir.glob("*.md") if re.match(r"\d{3}-", f.name)]
    stem = f"{max(nums, default=0) + 1:03d}-{logo.to_slug(a.name)}"
    dst = adir / f"{stem}{src.suffix.lower()}"
    shutil.copyfile(src, dst)
    logo.write_new(adir / f"{stem}.md", logo.render_template(
        "cover-asset.md", id=stem, file=dst.name, sha256=logo.sha256_file(dst), date=logo.today(),
        source=a.source, license=a.license))
    print(f"已登记素材：{logo.rel(dst)}；在设计稿中用 <image href=\"assets/{dst.name}\" …/> 引用")


def cmd_status(a) -> None:
    p = logo.Project(a.slug)
    cdir = cover_dir(p, a.cover)
    cfg = cover_cfg(cdir)
    targets = cfg["cover"]["platforms"]
    on = [t.strip() for t in a.on.split(",")] if a.on else (targets if a.status == "live" else [])
    if a.status != "live" and a.on:
        raise logo.LogoError("--on 只用于 live")
    bad = [t for t in on if t not in targets]
    if bad:
        raise logo.LogoError(f"不是该封面的目标平台：{', '.join(bad)}（目标：{', '.join(targets)}）")
    st = cfg["state"]
    st["status"], st["live_on"] = a.status, on
    st["live_since"] = logo.today() if a.status == "live" else ""
    st["updated"] = logo.today()
    write_cover_cfg(cdir, cfg)
    print(f"{cdir.name}: {a.status}" + (f"（上线：{', '.join(on)}）" if on else ""))


def cmd_list(a) -> None:
    p = logo.Project(a.slug)
    covers = project_covers(p)
    if not covers:
        print(f"{a.slug} 还没有封面（./logo cover new 创建）")
        return
    print("".join(pad(h, w) for h, w in zip(("封面", "状态", "Logo", "目标平台"), (26, 10, 8, 40))) + "上线")
    for cdir in covers:
        cfg = cover_cfg(cdir)
        c, st = cfg["cover"], cfg["state"]
        row = (cdir.name, st["status"], c["release"], ", ".join(c["platforms"]))
        print("".join(pad(v, w) for v, w in zip(row, (26, 10, 8, 40))) + (", ".join(st["live_on"]) or "—"))


def register(sub) -> None:
    """在 ./logo 下注册 cover 子命令组。"""
    s = sub.add_parser("cover", help="封面（品牌物料）：立项起稿、素材登记、状态与平台登记")
    cs = s.add_subparsers(dest="cover_cmd", required=True, metavar="<封面命令>")
    c = cs.add_parser("new", help="立项：按目标平台推出画幅，生成 cover.toml、cover.md 与各画幅起稿")
    c.add_argument("slug")
    c.add_argument("name", help="英文短名，如 spring-campaign")
    c.add_argument("--platforms", required=True, help="目标平台，逗号分隔，如 youtube,x,xiaohongshu")
    c.add_argument("--release", help="所用 Logo 发布（默认 current_release）")
    c.add_argument("--title", help="中文标题")
    c.set_defaults(fn=cmd_new)
    c = cs.add_parser("asset", help="登记照片/插画素材（记录来源、授权与 sha256，登记后不可替换）")
    c.add_argument("slug")
    c.add_argument("cover", help="封面编号，如 001")
    c.add_argument("image")
    c.add_argument("--name", required=True, help="英文短名")
    c.add_argument("--source", required=True, help="来源（作者、网址或“自摄”）")
    c.add_argument("--license", required=True, help="授权（如 CC BY 4.0、已购买商用授权、自有版权）")
    c.set_defaults(fn=cmd_asset)
    c = cs.add_parser("preview", help="校对图：画布、各平台裁切（叠遮挡区/安全区）与手机缩略图")
    c.add_argument("slug")
    c.add_argument("cover")
    c.add_argument("platforms", nargs="*", help="只看这些平台（默认全部目标平台）")
    c.set_defaults(fn=cmd_preview)
    c = cs.add_parser("export", help="按平台尺寸导出 PNG 到 dist/covers/（不提交，结果可重复）")
    c.add_argument("slug")
    c.add_argument("cover")
    c.add_argument("platforms", nargs="*", help="只导出这些平台（默认全部目标平台）")
    c.set_defaults(fn=cmd_export)
    c = cs.add_parser("retarget", help="修改目标平台：新画幅起稿，共用画幅的参考线随之更新")
    c.add_argument("slug")
    c.add_argument("cover")
    c.add_argument("--platforms", required=True, help="新的目标平台（完整列表），逗号分隔")
    c.set_defaults(fn=cmd_retarget)
    c = cs.add_parser("sync-logo", help="Logo 出新发布后，替换各画幅中的 Logo（保留位置、缩放、配色与显示高度）")
    c.add_argument("slug")
    c.add_argument("cover")
    c.add_argument("--release", help="目标发布（默认 current_release）")
    c.set_defaults(fn=cmd_sync_logo)
    c = cs.add_parser("status", help="记录封面状态：draft / live / archived")
    c.add_argument("slug")
    c.add_argument("cover")
    c.add_argument("status", choices=COVER_STATUSES)
    c.add_argument("--on", help="live 时上线的平台，逗号分隔（默认全部目标平台）")
    c.set_defaults(fn=cmd_status)
    c = cs.add_parser("list", help="列出项目的封面：状态、所用 Logo、目标与上线平台")
    c.add_argument("slug")
    c.set_defaults(fn=cmd_list)
    c = cs.add_parser("platforms", help="列出封面平台：所属画幅、尺寸、从画布裁切的比例、规格依据")
    c.add_argument("--ratio", help="只看某个画幅，如 3x1")
    c.set_defaults(fn=cmd_platforms)
