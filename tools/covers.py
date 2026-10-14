"""封面模块：封面是含 Logo 的品牌物料（构图、文案、图像），与 Logo 资产分开管理。

平台与画幅登记在 config/cover-platforms.toml；封面项目在 projects/<slug>/covers/NNN-名称/。
公共函数（TOML 读取、版式换色、规格依据检查等）复用 logo.py。
"""
from __future__ import annotations

import json
import re
import shutil
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
    """检查项目的全部封面：元数据、画幅稿、Logo 指纹、素材登记。"""
    try:
        cfg_all = load_config()
    except logo.LogoError:
        return  # 平台登记的问题由 check_platforms 报告
    plats, ratios = platforms_by_id(), cfg_all["ratios"]
    for cdir in project_covers(p):
        where = logo.rel(cdir)
        try:
            cfg = cover_cfg(cdir)
            c, st = cfg["cover"], cfg["state"]
            targets = list(c["platforms"])
        except (logo.LogoError, KeyError) as e:
            errors.append(f"{where}/cover.toml 无效：{e}")
            continue
        unknown = [t for t in targets if t not in plats]
        if unknown:
            errors.append(f"{where}/cover.toml: 未登记的平台 {', '.join(unknown)}（./logo cover platforms 查看）")
            continue
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
            svg = cdir / f"{key}.svg"
            if not svg.exists():
                errors.append(f"{where}: 缺少画幅稿 {key}.svg（目标平台需要）")
                continue
            root = check_cover_svg(svg, ratios[key], assets, errors, staged)
            if root is not None:
                errors.extend(logo_errors(p, root, logo.rel(svg)))
        for svg in sorted(cdir.glob("*.svg")):
            if svg.stem not in need:
                warnings.append(f"{logo.rel(svg)}: 当前目标平台不需要画幅 {svg.stem}（若是为新平台预备，可加入 platforms）")


# -- 命令 --------------------------------------------------------------------------
def cmd_new(a) -> None:
    p = logo.Project(a.slug)
    targets = [t.strip() for t in a.platforms.split(",") if t.strip()]
    plats = platforms_by_id()
    unknown = [t for t in targets if t not in plats]
    if not targets or unknown:
        raise logo.LogoError(f"未登记的平台：{', '.join(unknown) or '（空）'}（./logo cover platforms 查看）")
    rid = a.release or p.cfg["state"].get("current_release")
    if not rid:
        raise logo.LogoError("项目还没有 Logo 发布；先 ./logo release，或用 --release 指定")
    ldir = p.release_dir(rid) / "layouts"
    if not (ldir / "primary.svg").exists():
        raise logo.LogoError(f"{rid} 缺少主版 primary.svg")
    nums = [int(c.name[:3]) for c in project_covers(p)]
    cid = f"{max(nums, default=0) + 1:03d}-{logo.to_slug(a.name)}"
    cdir = p.dir / "covers" / cid
    ratios = load_config()["ratios"]
    need = needed_ratios(targets)
    layout = logo.mark_layout(ldir)
    try:
        bg = logo.app_background(p)
    except logo.LogoError:
        bg = next(iter(p.palette.values()), "#FFFFFF")
    texts = {}
    for key in need:  # 先全部生成，成功后再落盘
        canvas = ratios[key]
        on_ratio = [plats[t] for t in targets if plats[t]["ratio"] == key]
        safe = common_safe(on_ratio, canvas) or (0, 0, canvas["width"], canvas["height"])
        vb = [float(v) for v in re.split(r"[\s,]+", ET.fromstring(
            logo.layout_mode_text(p, ldir, layout, "color")).get("viewBox").strip())]
        lh = safe[3] * 0.15
        lw = lh * vb[2] / vb[3]
        if lw > safe[2] * 0.6:  # 很宽的 Logo：按宽度收
            lw, lh = safe[2] * 0.6, safe[2] * 0.6 * vb[3] / vb[2]
        vertical = canvas["height"] > canvas["width"]
        inset = lh * 0.25  # 与安全区边缘保持留白
        x = safe[0] + (safe[2] - lw) / 2 if vertical else safe[0] + inset
        y = safe[1] + inset
        texts[key] = (
            f'<svg xmlns="{logo.SVG_NS}" xmlns:xlink="{logo.XLINK_NS}" viewBox="0 0 {canvas["width"]} {canvas["height"]}">\n'
            f'  <rect id="background" width="{canvas["width"]}" height="{canvas["height"]}" fill="{bg}"/>\n'
            f'  {logo_group(p, ldir, rid, layout, logo.icon_mode(p), x, y, lh)}\n'
            f'  <g id="content">\n    <!-- 关键内容（标题、副标题、行动号召）放在这里，须在共同安全区内 -->\n  </g>\n'
            f'  {guides_xml(on_ratio, canvas)}\n</svg>\n')
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
    print("下一步：填写 cover.md；在主视觉画幅定稿后延展到其他画幅；关键内容放进 <g id=\"content\">")


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
