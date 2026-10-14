"""封面模块：封面是含 Logo 的品牌物料（构图、文案、图像），与 Logo 资产分开管理。

第二期只负责平台登记（config/cover-platforms.toml）的读取、校验与查看；
起稿、导出与检查在第三期实现。公共函数（TOML 读取、规格依据检查等）复用 logo.py。
"""
from __future__ import annotations

import re
import unicodedata

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


def cmd_list(a) -> None:
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


def register(sub) -> None:
    """在 ./logo 下注册 cover 子命令组。"""
    s = sub.add_parser("cover", help="封面（品牌物料）：平台登记与画幅；起稿与导出见第三期")
    cs = s.add_subparsers(dest="cover_cmd", required=True, metavar="<封面命令>")
    c = cs.add_parser("list", help="列出封面平台：所属画幅、尺寸、从画布裁切的比例、规格依据")
    c.add_argument("--ratio", help="只看某个画幅，如 3x1")
    c.set_defaults(fn=cmd_list)
