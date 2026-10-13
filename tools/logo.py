#!/usr/bin/env python3
"""LOGODesign 命令行工具。

所有项目操作（新建、出版本、发布、导出、检查）都通过本脚本完成，
以保证目录结构、命名、追溯记录和不可变规则始终一致。

用法：./logo <命令> [参数]，运行 ./logo -h 查看全部命令。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ROOT / "projects"
TEMPLATES = ROOT / "templates"
CONFIG = ROOT / "config"
PROFILES_TOML = CONFIG / "export-profiles.toml"
CACHE = ROOT / ".cache"
FONTS_DIR = ROOT / "assets" / "fonts"
FONTS_TOML = FONTS_DIR / "fonts.toml"
PROJECTS_MD = ROOT / "PROJECTS.md"

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VERSION_ID_RE = re.compile(r"^([A-Z])-v(\d{3})$")
RELEASE_RE = re.compile(r"^r(\d+)$")
CONCEPT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
HEX6_RE = re.compile(r"^#[0-9A-F]{6}$")
HEX_IN_TEXT_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")
STAGES = ["briefing", "exploring", "iterating", "releasing", "delivered", "paused"]
STATE_KEYS = ["stage", "selected_direction", "selected_version", "current_release"]
COLOR_ATTRS = {"fill", "stroke", "stop-color", "flood-color", "lighting-color", "color"}
STYLE_COLOR_RE = re.compile(r"(fill|stroke|stop-color|flood-color|lighting-color|color)\s*:\s*([^;}\s]+)")
FORBIDDEN_TAGS = {
    "text": "文字必须转为路径（使用 ./logo outline）",
    "tspan": "文字必须转为路径（使用 ./logo outline）",
    "textPath": "文字必须转为路径（使用 ./logo outline）",
    "image": "禁止内嵌位图",
    "script": "禁止脚本",
    "foreignObject": "禁止 foreignObject",
}

# 不可变（提交后禁止修改/删除）与只追加文件的路径规则（相对仓库根目录）
FROZEN_PATTERNS = [
    re.compile(r"^projects/[^/]+/directions/[^/]+/v\d{3}/logo\.svg$"),
    re.compile(r"^projects/[^/]+/releases/r\d+/layouts/[^/]+\.svg$"),
    re.compile(r"^projects/[^/]+/concepts/\d{3}-[^/]+\.(png|jpe?g|webp)$"),
]

APPEND_ONLY_PATTERNS = [
    re.compile(r"^projects/[^/]+/directions/[^/]+/v\d{3}/notes\.md$"),
    re.compile(r"^projects/[^/]+/directions/[^/]+/direction\.md$"),
    re.compile(r"^projects/[^/]+/releases/r\d+/(release|guide)\.md$"),
    re.compile(r"^projects/[^/]+/decisions/[^/]+\.md$"),
    re.compile(r"^projects/[^/]+/concepts/[^/]+\.md$"),
]

# 仓库中允许出现的文件（相对仓库根目录）；其余文件一律视为结构外杂物
ALLOWED_ROOT_FILES = {"AGENTS.md", "CLAUDE.md", "README.md", "PROJECTS.md", "logo", ".gitignore"}
ALLOWED_ROOT_DIRS = {".agents", ".claude", ".githooks", "assets", "config", "templates", "tools"}
PROJECT_FILE_PATTERNS = [re.compile(p) for p in (
    r"project\.toml",
    r"brief\.md",
    r"(concepts|directions|decisions|releases)/\.gitkeep",
    r"concepts/\d{3}-[a-z0-9-]+\.(md|png|jpe?g|webp)",
    r"directions/[A-Z]-[a-z0-9-]+/direction\.md",
    r"directions/[A-Z]-[a-z0-9-]+/v\d{3}/(logo\.svg|notes\.md)",
    r"decisions/\d{3}-[a-z0-9-]+\.md",
    r"releases/r\d+/(release|guide)\.md",
    r"releases/r\d+/layouts/[a-z0-9.-]+\.svg",
    r"final/.+",  # 由 manifest.json 单独校验
)]

# release.md 头部字段（模板写法 “- **基于**: A-v001”）到元数据键名的映射
RELEASE_META_KEYS = {"基于": "based_on", "日期": "date", "based_on": "based_on", "date": "date"}


class LogoError(Exception):
    """可预期的用户错误，直接打印信息并以非零状态退出。"""


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------
def today() -> str:
    return dt.date.today().isoformat()


def rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], capture: bool = False) -> str:
    try:
        r = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise LogoError(f"找不到命令：{cmd[0]}，请先安装")
    except subprocess.CalledProcessError as e:
        raise LogoError(f"命令失败：{' '.join(cmd)}\n{e.stderr.strip()}")
    return r.stdout if capture else ""


def load_toml(p: Path) -> dict:
    try:
        with p.open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        raise LogoError(f"文件不存在：{rel(p)}")
    except tomllib.TOMLDecodeError as e:
        raise LogoError(f"{rel(p)} 格式错误：{e}")


def render_template(template_name: str, **values: str) -> str:
    text = (TEMPLATES / template_name).read_text(encoding="utf-8")
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def write_new(p: Path, content: str) -> None:
    if p.exists():
        raise LogoError(f"文件已存在，拒绝覆盖：{rel(p)}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def to_slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not s:
        raise LogoError(f"无法从 “{text}” 生成英文短名，请使用小写英文/数字/短横线")
    return s


# ---------------------------------------------------------------------------
# 项目模型
# ---------------------------------------------------------------------------
class Project:
    def __init__(self, slug: str):
        self.slug = slug
        self.dir = PROJECTS / slug
        if not (self.dir / "project.toml").exists():
            raise LogoError(f"项目不存在：{slug}（可用 ./logo new 创建）")

    # -- 配置 ---------------------------------------------------------------
    @property
    def toml_path(self) -> Path:
        return self.dir / "project.toml"

    @property
    def cfg(self) -> dict:
        return load_toml(self.toml_path)

    @property
    def palette(self) -> dict[str, str]:
        return {k: v.upper() for k, v in self.cfg.get("palette", {}).items()}

    def mode_map(self, mode: str) -> dict[str, str]:
        """返回 {原色 hex: 目标 hex}。"""
        pal = self.palette
        if mode == "color":
            return {v: v for v in pal.values()}
        defaults = {"mono": "#000000", "reversed": "#FFFFFF"}
        overrides = {k: v.upper() for k, v in self.cfg.get("color_modes", {}).get(mode, {}).items()}
        if mode not in defaults and not overrides:
            raise LogoError(f"未知配色模式：{mode}（请在 [color_modes.{mode}] 中定义映射）")
        result = {}
        for key, hexv in pal.items():
            result[hexv] = overrides.get(key, defaults.get(mode, hexv))
        return result

    def set_state(self, **updates: str) -> None:
        """在 project.toml 的 [state] 表中更新字段（保留其余内容与注释）。"""
        text = self.toml_path.read_text(encoding="utf-8")
        updates = {**updates, "updated": today()}
        m = re.search(r"^\[state\]\s*$", text, re.M)
        if not m:
            raise LogoError("project.toml 缺少 [state] 表")
        start = m.end()
        nxt = re.search(r"^\[", text[start:], re.M)
        end = start + nxt.start() if nxt else len(text)
        section = text[start:end]
        for k, v in updates.items():
            line = f'{k} = "{v}"'
            pat = re.compile(rf"^{re.escape(k)}\s*=.*$", re.M)
            if pat.search(section):
                section = pat.sub(line, section, count=1)
            else:
                section = section.rstrip("\n") + "\n" + line + "\n"
        self.toml_path.write_text(text[:start] + section + text[end:], encoding="utf-8")

    # -- 方向与版本 -----------------------------------------------------------
    def directions(self) -> list[Path]:
        d = self.dir / "directions"
        return sorted(p for p in d.iterdir() if p.is_dir()) if d.exists() else []

    def direction(self, letter: str) -> Path:
        letter = letter.upper()
        for p in self.directions():
            if p.name.split("-", 1)[0] == letter:
                return p
        raise LogoError(f"方向不存在：{letter}（可用 ./logo direction 创建）")

    @staticmethod
    def versions_in(direction_dir: Path) -> list[Path]:
        return sorted(p for p in direction_dir.iterdir() if p.is_dir() and re.fullmatch(r"v\d{3}", p.name))

    def version_dir(self, vid: str) -> Path:
        m = VERSION_ID_RE.match(vid)
        if not m:
            raise LogoError(f"版本号格式应为 A-v001，收到：{vid}")
        p = self.direction(m.group(1)) / f"v{m.group(2)}"
        if not p.exists():
            raise LogoError(f"版本不存在：{vid}")
        return p

    def latest_version_id(self, direction_dir: Path) -> str | None:
        vs = self.versions_in(direction_dir)
        return f"{direction_dir.name.split('-', 1)[0]}-{vs[-1].name}" if vs else None

    # -- 发布 -----------------------------------------------------------------
    def releases(self) -> list[Path]:
        d = self.dir / "releases"
        if not d.exists():
            return []
        return sorted((p for p in d.iterdir() if RELEASE_RE.fullmatch(p.name)), key=lambda p: int(p.name[1:]))

    def release_dir(self, rid: str) -> Path:
        if not RELEASE_RE.fullmatch(rid):
            raise LogoError(f"发布号格式应为 r1，收到：{rid}")
        p = self.dir / "releases" / rid
        if not p.exists():
            raise LogoError(f"发布不存在：{rid}")
        return p

    def release_root_version(self, rid: str) -> str:
        """沿 based_on 链（r3 → r2 → A-v004）找到发布最终基于的设计版本。"""
        seen = set()
        cur = rid
        while RELEASE_RE.fullmatch(cur):
            if cur in seen:
                raise LogoError(f"发布 {rid} 的 based_on 存在循环引用")
            seen.add(cur)
            based = load_release_meta(self.release_dir(cur)).get("based_on", "")
            if not based:
                raise LogoError(f"releases/{cur}/release.md 缺少“基于”字段")
            if RELEASE_RE.fullmatch(based) and int(based[1:]) >= int(cur[1:]):
                raise LogoError(f"releases/{cur} 只能基于更早的发布，收到：{based}")
            cur = based
        self.version_dir(cur)  # 校验存在
        return cur

    # -- 目标解析（预览用）-------------------------------------------------------
    def resolve_target(self, target: str) -> Path:
        if VERSION_ID_RE.match(target):
            return self.version_dir(target) / "logo.svg"
        m = re.fullmatch(r"(r\d+)/(.+?)(\.svg)?", target)
        if m:
            p = self.release_dir(m.group(1)) / "layouts" / f"{m.group(2)}.svg"
            if not p.exists():
                raise LogoError(f"版式不存在：{rel(p)}")
            return p
        p = Path(target)
        if p.exists():
            return p
        raise LogoError(f"无法识别的目标：{target}（支持 A-v001、r1/icon 或文件路径）")


def all_projects() -> list[Project]:
    if not PROJECTS.exists():
        return []
    return [Project(p.name) for p in sorted(PROJECTS.iterdir()) if (p / "project.toml").exists()]


# ---------------------------------------------------------------------------
# SVG 工具
# ---------------------------------------------------------------------------
def svg_viewbox(path: Path) -> tuple[float, float, float, float]:
    root = ET.parse(path).getroot()
    vb = root.get("viewBox")
    if not vb:
        raise LogoError(f"{rel(path)} 缺少 viewBox")
    parts = [float(x) for x in re.split(r"[\s,]+", vb.strip())]
    return parts[0], parts[1], parts[2], parts[3]


def recolor_svg(src: Path, mapping: dict[str, str]) -> str:
    text = src.read_text(encoding="utf-8")
    return HEX_IN_TEXT_RE.sub(lambda m: mapping.get(m.group(0).upper(), m.group(0)), text)


def layout_mode_text(p: Project, ldir: Path, name: str, mode: str) -> str:
    """某版式在某配色下的 SVG 文本：优先使用手工覆盖文件 name.mode.svg，否则按色板自动换色。"""
    override = ldir / f"{name}.{mode}.svg"
    if mode != "color" and override.exists():
        return override.read_text(encoding="utf-8")
    return recolor_svg(ldir / f"{name}.svg", p.mode_map(mode))


def avatar_safe_warning(ldir: Path, av: dict) -> str | None:
    """检查头像图形是否超出圆形安全区；返回提醒文字，没有问题时返回 None。"""
    layout = ldir / f"{av['layout']}.svg"
    if not layout.exists():
        return None
    try:
        stripped = strip_element_by_id(layout, "background")
    except ET.ParseError:
        return None  # XML 错误已由 check_svg 报告
    if stripped is None:
        return f"{rel(layout)} 没有 id=\"background\" 的底色元素，无法做圆形安全区检查"
    with tempfile.TemporaryDirectory() as td:
        t = Path(td) / "fg.svg"
        t.write_text(stripped, encoding="utf-8")
        val = alpha_outside_circle(render_fit(t, Path(td) / "fg.png", 400), av["safe_radius_ratio"])
    if val > 0.05:
        return f"{rel(layout)} 图形超出圆形安全区（半径 {av['safe_radius_ratio']}），在圆形裁切下可能被切掉"
    return None


def strip_element_by_id(src: Path, element_id: str) -> str | None:
    tree = ET.parse(src)
    root = tree.getroot()
    for parent in root.iter():
        for child in list(parent):
            if child.get("id") == element_id:
                parent.remove(child)
                return ET.tostring(root, encoding="unicode")
    return None


def render(svg: Path, out: Path, width: int | None = None, height: int | None = None,
           background: str | None = None) -> Path:
    """渲染 SVG 为 PNG；同时给出宽高时按比例适配到该框内。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["resvg", str(svg), str(out)]
    if width:
        cmd += ["-w", str(width)]
    if height:
        cmd += ["-h", str(height)]
    if background:
        cmd += ["--background", background]
    run(cmd)
    return out


def render_fit(svg: Path, out: Path, box: int, background: str | None = None) -> Path:
    return render(svg, out, box, box, background)


def render_cover(svg: Path, out: Path, width: int, height: int) -> Path:
    """按"铺满后居中裁切"方式渲染到精确尺寸。"""
    _, _, vw, vh = svg_viewbox(svg)
    aspect = vw / vh
    rw = max(width, math.ceil(height * aspect))
    render(svg, out, width=rw)
    run(["magick", str(out), "-gravity", "center", "-crop", f"{width}x{height}+0+0", "+repage", str(out)])
    return out


def alpha_outside_circle(png: Path, radius_ratio: float) -> float:
    """返回圆形安全区之外的最大不透明度（0~1）。radius_ratio 是相对于内切圆半径的比例。"""
    w, h = (int(x) for x in run(["magick", "identify", "-format", "%w %h", str(png)], capture=True).split())
    cx, cy, r = w / 2, h / 2, min(w, h) / 2 * radius_ratio
    with tempfile.TemporaryDirectory() as td:
        mask = Path(td) / "mask.png"
        run(["magick", "-size", f"{w}x{h}", "xc:white", "-fill", "black",
             "-draw", f"circle {cx},{cy} {cx},{cy + r}", str(mask)])
        val = run(["magick", str(png), "-alpha", "extract", str(mask), "-compose", "multiply",
                   "-composite", "-format", "%[fx:maxima]", "info:"], capture=True)
    return float(val.strip() or 0)


# ---------------------------------------------------------------------------
# 命令：new / direction / version / concept / decision / release / state
# ---------------------------------------------------------------------------
def cmd_new(a) -> None:
    slug = a.slug
    if not SLUG_RE.match(slug):
        raise LogoError("项目短名只能使用小写英文、数字和短横线，例如 acme 或 green-tea")
    d = PROJECTS / slug
    if d.exists():
        raise LogoError(f"项目已存在：{rel(d)}")
    vals = {"name": a.name or slug, "slug": slug, "date": today()}
    write_new(d / "project.toml", render_template("project.toml", **vals))
    write_new(d / "brief.md", render_template("brief.md", **vals))
    for sub in ("concepts", "directions", "decisions", "releases"):
        (d / sub).mkdir(parents=True, exist_ok=True)
        (d / sub / ".gitkeep").touch()
    print(f"已创建项目：{rel(d)}\n下一步：填写 {rel(d / 'brief.md')} 与 project.toml 中的色板")


def cmd_direction(a) -> None:
    p = Project(a.slug)
    letter = a.letter.upper()
    if not re.fullmatch(r"[A-Z]", letter):
        raise LogoError("方向编号必须是单个大写字母，例如 A")
    if any(d.name.split("-", 1)[0] == letter for d in p.directions()):
        raise LogoError(f"方向 {letter} 已存在")
    d = p.dir / "directions" / f"{letter}-{to_slug(a.name)}"
    write_new(d / "direction.md", render_template(
        "direction.md", letter=letter, name=a.name, title=a.title or a.name, date=today()))
    (p.dir / "directions" / ".gitkeep").unlink(missing_ok=True)
    if p.cfg["state"].get("stage") == "briefing":
        p.set_state(stage="exploring")
    print(f"已创建方向：{rel(d)}")


def cmd_version(a) -> None:
    p = Project(a.slug)
    ddir = p.direction(a.direction)
    letter = ddir.name.split("-", 1)[0]
    existing = p.versions_in(ddir)
    num = int(existing[-1].name[1:]) + 1 if existing else 1
    vid = f"{letter}-v{num:03d}"
    base = a.based_on or (None if a.blank else p.latest_version_id(ddir))
    base_svg = p.version_dir(base) / "logo.svg" if base else None  # 先校验，避免留下半成品目录
    vdir = ddir / f"v{num:03d}"
    vdir.mkdir(parents=True)
    if base_svg:
        shutil.copyfile(base_svg, vdir / "logo.svg")
    else:
        shutil.copyfile(TEMPLATES / "blank.svg", vdir / "logo.svg")
    write_new(vdir / "notes.md", render_template(
        "version-notes.md", id=vid, based_on=base or "（无，全新起稿）", direction=ddir.name, date=today()))
    stage = p.cfg["state"].get("stage")
    if stage in ("briefing", "exploring", "delivered") and p.cfg["state"].get("selected_direction"):
        p.set_state(stage="iterating")
    print(f"已创建版本：{vid} → {rel(vdir)}")
    print("提示：完成 logo.svg 与 notes.md 后再提交；提交后该版本即冻结")


def cmd_concept(a) -> None:
    p = Project(a.slug)
    src = Path(a.image)
    if not src.exists():
        raise LogoError(f"图片不存在：{src}")
    if src.suffix.lower() not in CONCEPT_SUFFIXES:
        raise LogoError(f"概念图只支持 {', '.join(sorted(CONCEPT_SUFFIXES))}，收到：{src.suffix}")
    cdir = p.dir / "concepts"
    nums = [int(m.group(1)) for f in cdir.glob("*.md") if (m := re.match(r"(\d{3})-", f.name))]
    num = max(nums, default=0) + 1
    stem = f"{num:03d}-{to_slug(a.name)}"
    dst = cdir / f"{stem}{src.suffix.lower()}"
    shutil.copyfile(src, dst)
    write_new(cdir / f"{stem}.md", render_template(
        "concept.md", id=stem, image=dst.name, prompt=a.prompt, tool=a.tool, date=today(),
        sha256=sha256_file(dst)))
    (cdir / ".gitkeep").unlink(missing_ok=True)
    print(f"已登记概念图：{rel(dst)}（提示词记录于 {stem}.md）")


def cmd_decision(a) -> None:
    p = Project(a.slug)
    ddir = p.dir / "decisions"
    nums = [int(m.group(1)) for f in ddir.glob("*.md") if (m := re.match(r"(\d{3})-", f.name))]
    num = max(nums, default=0) + 1
    f = ddir / f"{num:03d}-{to_slug(a.name)}.md"
    write_new(f, render_template("decision.md", num=f"{num:03d}", title=a.title or a.name, date=today()))
    (ddir / ".gitkeep").unlink(missing_ok=True)
    print(f"已创建决策记录：{rel(f)}")


def cmd_release(a) -> None:
    p = Project(a.slug)
    rels = p.releases()
    num = int(rels[-1].name[1:]) + 1 if rels else 1
    rid = f"r{num}"
    rdir = p.dir / "releases" / rid
    layouts = rdir / "layouts"
    src = a.based_on
    if RELEASE_RE.fullmatch(src):
        root_version = p.release_root_version(src)
        # 只继承版式；guide.md 为只追加文件，新发布重新起稿，避免误继承
        shutil.copytree(p.release_dir(src) / "layouts", layouts)
    elif VERSION_ID_RE.match(src):
        p.version_dir(src)  # 校验存在
        root_version = src
        layouts.mkdir(parents=True)
    else:
        raise LogoError("--from 需要版本号（A-v004）或发布号（r1）")
    required = required_layouts(p)
    write_new(rdir / "release.md", render_template(
        "release.md", id=rid, based_on=src, date=today(),
        layouts="\n".join(f"- [ ] `{n}.svg`" for n in required)))
    write_new(rdir / "guide.md", render_template("release-guide.md", id=rid, name=p.cfg["project"]["name"]))
    (p.dir / "releases" / ".gitkeep").unlink(missing_ok=True)
    # 发布即确认定稿：选定方向/版本与发布的来源保持一致（唯一事实来源仍是 project.toml）
    p.set_state(stage="releasing", current_release=rid,
                selected_direction=root_version.split("-")[0], selected_version=root_version)
    print(f"已创建发布：{rid} → {rel(rdir)}（定稿版本：{root_version}）")
    print("需要的版式：" + ", ".join(required))


def cmd_state(a) -> None:
    p = Project(a.slug)
    updates = {}
    for item in a.assignments:
        if "=" not in item:
            raise LogoError(f"格式应为 key=value：{item}")
        k, v = item.split("=", 1)
        if k not in STATE_KEYS:
            raise LogoError(f"不支持的字段：{k}（可选：{', '.join(STATE_KEYS)}）")
        if k == "stage" and v not in STAGES:
            raise LogoError(f"stage 可选值：{', '.join(STAGES)}")
        if v:
            if k == "selected_version":
                p.version_dir(v)
            elif k == "current_release":
                p.release_dir(v)
            elif k == "selected_direction":
                v = p.direction(v).name.split("-", 1)[0]
        updates[k] = v
    if not updates:
        st = p.cfg["state"]
        for k in STATE_KEYS + ["updated"]:
            print(f"{k} = {st.get(k, '')}")
        return
    p.set_state(**updates)
    print("已更新：" + ", ".join(f"{k}={v}" for k, v in updates.items()))


# ---------------------------------------------------------------------------
# 命令：outline（文字转路径）
# ---------------------------------------------------------------------------
def load_fonts() -> list[dict]:
    if not FONTS_TOML.exists():
        return []
    return load_toml(FONTS_TOML).get("font", [])


def find_font(font_id: str) -> dict:
    for f in load_fonts():
        if f.get("id") == font_id:
            return f
    ids = ", ".join(f.get("id", "?") for f in load_fonts()) or "（无）"
    raise LogoError(f"字体未登记：{font_id}。已登记：{ids}")


def fmt_num(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def cmd_outline(a) -> None:
    try:
        from fontTools.pens.boundsPen import BoundsPen
        from fontTools.pens.svgPathPen import SVGPathPen
        from fontTools.pens.transformPen import TransformPen
        from fontTools.ttLib import TTFont
    except ImportError:
        raise LogoError("缺少 fonttools，请先运行 ./logo setup")
    entry = find_font(a.font)
    path = FONTS_DIR / entry["file"]
    if not path.exists():
        raise LogoError(f"字体文件不存在：{rel(path)}")
    if sha256_file(path) != entry.get("sha256"):
        raise LogoError(f"字体校验值不匹配：{rel(path)}，文件可能被替换或修改")
    if not HEX6_RE.match(a.fill.upper()):
        raise LogoError("--fill 必须是 6 位十六进制颜色，且应在项目色板中")
    if a.project:
        p = Project(a.project)
        if a.fill.upper() not in set(p.palette.values()):
            raise LogoError(f"--fill {a.fill.upper()} 不在 {a.project} 的色板中")
        if a.font not in p.cfg.get("fonts", {}).get("used", []):
            print(f"⚠ 字体 {a.font} 尚未写入 {a.project} 的 [fonts] used，"
                  "请补上并在 fonts.toml 的 used_by 中登记", file=sys.stderr)
    else:
        print("⚠ 未指定 --project，无法校验填充色是否在色板中", file=sys.stderr)
    font = TTFont(path, fontNumber=entry.get("font_number", 0))
    gs = font.getGlyphSet()
    cmap = font.getBestCmap()
    hmtx = font["hmtx"]
    scale = a.size / font["head"].unitsPerEm
    pen = SVGPathPen(gs, ntos=fmt_num)
    bpen = BoundsPen(gs)
    x = 0.0
    missing = []
    for ch in a.text:
        gname = cmap.get(ord(ch))
        if gname is None:
            missing.append(ch)
            continue
        t = (scale, 0, 0, -scale, x, 0)
        gs[gname].draw(TransformPen(pen, t))
        gs[gname].draw(TransformPen(bpen, t))
        x += hmtx[gname][0] * scale + a.tracking
    if missing:
        raise LogoError(f"字体 {a.font} 不包含这些字符：{''.join(missing)}")
    d = pen.getCommands()
    bounds = bpen.bounds or (0, 0, 0, 0)
    safe_text = a.text.replace("--", "‐‐")  # XML 注释中不允许出现 “--”
    comment = (f"<!-- outline: font={a.font} size={a.size} tracking={a.tracking} text=\"{safe_text}\" "
               f"bbox={','.join(fmt_num(b) for b in bounds)} advance={fmt_num(x)} -->")
    path_el = f'<path fill="{a.fill.upper()}" d="{d}"/>'
    if a.svg:
        pad = a.size * 0.1
        x0, y0, x1, y1 = bounds
        vb = f"{fmt_num(x0 - pad)} {fmt_num(y0 - pad)} {fmt_num(x1 - x0 + 2 * pad)} {fmt_num(y1 - y0 + 2 * pad)}"
        out = Path(a.svg)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f'<svg xmlns="{SVG_NS}" viewBox="{vb}">\n  {comment}\n  {path_el}\n</svg>\n',
                       encoding="utf-8")
        print(f"已输出：{out}")
    else:
        print(comment)
        print(path_el)


# ---------------------------------------------------------------------------
# 命令：preview / compare
# ---------------------------------------------------------------------------
def make_preview_sheet(p: Project, svg: Path, out: Path) -> Path:
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        variants = {}
        for mode in ("color", "mono", "reversed"):
            f = t / f"{mode}.svg"
            f.write_text(recolor_svg(svg, p.mode_map(mode)), encoding="utf-8")
            variants[mode] = f
        cells = []
        for mode, bg in (("color", "white"), ("mono", "white"), ("reversed", "#111111")):
            png = render_fit(variants[mode], t / f"{mode}.png", 440)
            cell = t / f"cell-{mode}.png"
            run(["magick", str(png), "-background", bg, "-gravity", "center", "-extent", "520x520", str(cell)])
            cells.append(cell)
        smalls = []
        for size, zoom in ((16, 8), (32, 4), (64, 2)):
            png = render_fit(variants["color"], t / f"s{size}.png", size)
            cell = t / f"cell-s{size}.png"
            run(["magick", str(png), "-filter", "point", "-resize", f"{zoom * 100}%", "-background", "white",
                 "-gravity", "center", "-extent", "520x200", str(cell)])
            smalls.append(cell)
        row1, row2 = t / "row1.png", t / "row2.png"
        run(["magick", *map(str, cells), "+append", str(row1)])
        run(["magick", *map(str, smalls), "+append", str(row2)])
        out.parent.mkdir(parents=True, exist_ok=True)
        run(["magick", str(row1), str(row2), "-background", "#DDDDDD", "-splice", "0x4", "-append", str(out)])
    return out


def cmd_preview(a) -> None:
    p = Project(a.slug)
    if RELEASE_RE.fullmatch(a.target):
        rdir = p.release_dir(a.target)
        layouts = sorted((rdir / "layouts").glob("*.svg"))
        if not layouts:
            raise LogoError(f"{a.target} 还没有任何版式")
        with tempfile.TemporaryDirectory() as td:
            cells = []
            for f in layouts:
                png = render_fit(f, Path(td) / f"{f.stem}.png", 360)
                cell = Path(td) / f"cell-{f.stem}.png"
                run(["magick", str(png), "-background", "white", "-gravity", "center",
                     "-extent", "400x400", str(cell)])
                cells.append(cell)
            out = CACHE / p.slug / "preview" / f"{a.target}-sheet.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            run(["magick", "montage", *map(str, cells), "-tile", "4x", "-geometry", "+6+6",
                 "-background", "#DDDDDD", str(out)])
        print(f"预览：{rel(out)}")
        print("顺序（从左到右、从上到下）：" + ", ".join(f.stem for f in layouts))
        return
    svg = p.resolve_target(a.target)
    label = a.target.replace("/", "_")
    out = make_preview_sheet(p, svg, CACHE / p.slug / "preview" / f"{label}.png")
    print(f"预览：{rel(out)}")
    print("第一行：彩色 / 单色 / 反白；第二行：16px×8、32px×4、64px×2 放大查看小尺寸效果")


def cmd_compare(a) -> None:
    p = Project(a.slug)
    ids = a.ids or [vid for d in p.directions() if (vid := p.latest_version_id(d))]
    if not ids:
        raise LogoError("没有可对比的版本")
    with tempfile.TemporaryDirectory() as td:
        cells = []
        for vid in ids:
            png = render_fit(p.version_dir(vid) / "logo.svg", Path(td) / f"{vid}.png", 400)
            cell = Path(td) / f"cell-{vid}.png"
            run(["magick", str(png), "-background", "white", "-gravity", "center", "-extent", "460x460", str(cell)])
            cells.append(cell)
        out = CACHE / p.slug / "compare" / f"{'_'.join(ids)}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        run(["magick", "montage", *map(str, cells), "-tile", f"{min(len(cells), 4)}x", "-geometry", "+6+6",
             "-background", "#DDDDDD", str(out)])
    print(f"对比图：{rel(out)}")
    print("顺序（从左到右、从上到下）：" + ", ".join(ids))


# ---------------------------------------------------------------------------
# 命令：export
# ---------------------------------------------------------------------------
def load_profiles() -> dict:
    return load_toml(PROFILES_TOML)


def required_layouts(p: Project) -> list[str]:
    cfg = p.cfg
    prof = load_profiles()
    enabled = cfg.get("export", {}).get("profiles", [])
    need = list(cfg.get("layouts", {}).get("logo", []))

    def add(name):
        if name and name not in need:
            need.append(name)

    if "favicon" in enabled:
        add(prof["favicon"]["layout"])
        add(prof["favicon"].get("apple_touch_layout"))
    if "ios" in enabled:
        add(prof["ios"]["layout"])
    if "android" in enabled:
        add(prof["android"]["legacy_layout"])
        add(prof["android"]["adaptive_foreground_layout"])
    if "social" in enabled:
        add(prof["social"]["avatar"]["layout"])
        for c in prof["social"].get("cover", []):
            add(c["layout"])
    return need


def cmd_export(a) -> None:
    p = Project(a.slug)
    cfg = p.cfg
    rid = a.release or cfg["state"].get("current_release")
    if not rid:
        raise LogoError("未指定发布号，且 project.toml 中没有 current_release")
    rdir = p.release_dir(rid)
    ldir = rdir / "layouts"
    root_version = p.release_root_version(rid)  # 追溯链不完整时拒绝导出
    need = required_layouts(p)
    missing = [n for n in need if not (ldir / f"{n}.svg").exists()]
    if missing:
        raise LogoError(f"{rid} 缺少版式：{', '.join(missing)}")

    errors, warnings = [], []
    for f in sorted(ldir.glob("*.svg")):
        check_svg(f, set(p.palette.values()), errors, warnings)
    if errors:
        raise LogoError("版式未通过检查：\n  " + "\n  ".join(errors))

    prof = load_profiles()
    enabled = cfg.get("export", {}).get("profiles", [])
    prefix = p.slug
    final = p.dir / "final"
    if final.exists():
        shutil.rmtree(final)
    (final / "svg").mkdir(parents=True)
    outputs: list[Path] = []
    modes = cfg.get("color_modes", {}).get("modes", ["color"])
    logo_layouts = cfg.get("layouts", {}).get("logo", [])
    special = [n for n in need if n not in logo_layouts]

    def layout_svg(name: str, mode: str) -> Path:
        """返回某版式某配色的最终 SVG（优先使用手工覆盖文件 name.mode.svg）。"""
        out = final / "svg" / (f"{prefix}-{name}-{mode}.svg" if name in logo_layouts else f"{prefix}-{name}.svg")
        if out.exists():
            return out
        out.write_text(layout_mode_text(p, ldir, name, mode), encoding="utf-8")
        outputs.append(out)
        return out

    for name in logo_layouts:
        for mode in modes:
            layout_svg(name, mode)
    for name in special:
        layout_svg(name, "color")

    png = final / "png"
    gp = prof["general"]
    if "general" in enabled:
        for name in logo_layouts:
            for mode in gp.get("modes", modes):
                for w in gp["sizes"]:
                    outputs.append(render(layout_svg(name, mode), png / "general" / f"{prefix}-{name}-{mode}@{w}.png",
                                          width=w))

    if "favicon" in enabled:
        fp = prof["favicon"]
        src = layout_svg(fp["layout"], "color")
        icos = []
        for s in fp["sizes"]:
            f = render_fit(src, png / "favicon" / f"favicon-{s}.png", s)
            outputs.append(f)
            if s in fp.get("ico_sizes", []):
                icos.append(f)
        if icos:
            ico = png / "favicon" / "favicon.ico"
            run(["magick", *map(str, icos), str(ico)])
            outputs.append(ico)
        if fp.get("apple_touch_layout"):
            f = render_fit(layout_svg(fp["apple_touch_layout"], "color"), png / "favicon" / "apple-touch-icon.png",
                           fp["apple_touch_size"])
            run(["magick", str(f), "-background", "white", "-alpha", "remove", "-alpha", "off", str(f)])
            outputs.append(f)

    if "ios" in enabled:
        ip = prof["ios"]
        src = layout_svg(ip["layout"], "color")
        for s in ip["sizes"]:
            f = render_fit(src, png / "ios" / f"AppIcon-{s}.png", s)
            if ip.get("opaque", True):
                run(["magick", str(f), "-background", "white", "-alpha", "remove", "-alpha", "off", str(f)])
            outputs.append(f)

    if "android" in enabled:
        ap = prof["android"]
        legacy = layout_svg(ap["legacy_layout"], "color")
        for dens, s in ap["mipmap"].items():
            outputs.append(render_fit(legacy, png / "android" / f"mipmap-{dens}" / "ic_launcher.png", s))
        play = render_fit(legacy, png / "android" / f"play-store-{ap['play_store_size']}.png", ap["play_store_size"])
        run(["magick", str(play), "-background", "white", "-alpha", "remove", "-alpha", "off", str(play)])
        outputs.append(play)
        size = ap["adaptive_size"]
        inner = round(size * ap["adaptive_safe_ratio"])
        ex = cfg.get("export", {})
        bg_key = ex.get("android_background", "")
        if bg_key not in p.palette:
            raise LogoError(f"[export] android_background 必须是色板中的色名，当前：{bg_key!r}")
        adir = png / "android" / "adaptive"
        bg = adir / "ic_launcher_background.png"
        adir.mkdir(parents=True, exist_ok=True)
        run(["magick", "-size", f"{size}x{size}", f"xc:{p.palette[bg_key]}", str(bg)])
        outputs.append(bg)
        fg_layout = ap["adaptive_foreground_layout"]
        for mode, fname in ((ex.get("android_foreground_mode", "color"), "ic_launcher_foreground.png"),
                            ("mono", "ic_launcher_monochrome.png")):
            with tempfile.TemporaryDirectory() as td:
                tmp_svg = Path(td) / "fg.svg"
                tmp_svg.write_text(layout_mode_text(p, ldir, fg_layout, mode), encoding="utf-8")
                f = render_fit(tmp_svg, adir / fname, inner)
            run(["magick", str(f), "-background", "none", "-gravity", "center", "-extent", f"{size}x{size}", str(f)])
            outputs.append(f)

    if "social" in enabled:
        sp = prof["social"]
        av = sp["avatar"]
        src = layout_svg(av["layout"], "color")
        for s in av["sizes"]:
            outputs.append(render_fit(src, png / "social" / "avatar" / f"{prefix}-avatar@{s}.png", s))
        if (w := avatar_safe_warning(ldir, av)):
            warnings.append(w)
        guides = CACHE / p.slug / "guides"
        for c in sp.get("cover", []):
            src = layout_svg(c["layout"], "color")
            f = render_cover(src, png / "social" / "covers" / f"{c['id']}-{c['width']}x{c['height']}.png",
                             c["width"], c["height"])
            outputs.append(f)
            draws = []
            for x, y, w, h in c.get("occlusions", []):
                draws += ["-fill", "rgba(255,0,0,0.35)", "-stroke", "none", "-draw", f"rectangle {x},{y} {x + w},{y + h}"]
            if c.get("safe"):
                x, y, w, h = c["safe"]
                draws += ["-fill", "none", "-stroke", "#00FF00", "-strokewidth", "3",
                          "-draw", f"rectangle {x},{y} {x + w},{y + h}"]
            if draws:
                guides.mkdir(parents=True, exist_ok=True)
                run(["magick", str(f), *draws, str(guides / f"{c['id']}.png")])

    # 指南与清单
    guide_src = rdir / "guide.md"
    guide = final / "guide.md"
    pal_rows = "\n".join(f"| {k} | `{v}` |" for k, v in p.palette.items())
    guide.write_text(
        "<!-- 由 ./logo export 自动生成，请勿手改；内容来源："
        f"{rel(guide_src)} 与 project.toml -->\n\n"
        + (guide_src.read_text(encoding="utf-8") if guide_src.exists() else "")
        + f"\n\n---\n\n## 附录 A：色板（自动生成）\n\n| 色名 | 色值 |\n|---|---|\n{pal_rows}\n"
        + "\n## 附录 B：文件清单（自动生成）\n\n"
        + "\n".join(f"- `{o.relative_to(final).as_posix()}`" for o in sorted(set(outputs)))
        + "\n",
        encoding="utf-8")
    outputs.append(guide)
    head = ""
    try:
        head = run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture=True).strip()
    except LogoError:
        pass
    manifest = {
        "project": p.slug,
        "release": rid,
        "based_on": load_release_meta(rdir).get("based_on", ""),
        "design_version": root_version,
        "generated_on": today(),
        "git_head": head,
        "files": {o.relative_to(final).as_posix(): sha256_file(o) for o in sorted(set(outputs))},
    }
    (final / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已导出 {len(manifest['files'])} 个文件 → {rel(final)}（{rid} ← {root_version}）")
    if rid == cfg["state"].get("current_release"):
        p.set_state(stage="delivered")
        print("项目阶段已更新为 delivered；请运行 ./logo index 并提交")
    for w in warnings:
        print(f"⚠ {w}")
    for w in profile_warnings(prof, enabled):
        print(f"⚠ {w}")


def load_release_meta(rdir: Path) -> dict:
    """解析 release.md 头部字段，兼容 “- **基于**: A-v001” 与 “- based_on: A-v001” 两种写法。"""
    meta = {}
    f = rdir / "release.md"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^-\s+\**\s*([^*:：]+?)\s*\**\s*[:：]\s*(.*)$", line)
            if m and m.group(1) in RELEASE_META_KEYS:
                meta.setdefault(RELEASE_META_KEYS[m.group(1)], m.group(2).strip().strip("`"))
    return meta


# ---------------------------------------------------------------------------
# 命令：check
# ---------------------------------------------------------------------------
def check_color_value(val: str, palette: set[str], where: str, errors: list[str]) -> None:
    v = val.strip()
    if v == "none" or v.startswith("url(#"):
        return
    if HEX6_RE.match(v.upper()):
        if v.upper() not in palette:
            errors.append(f"{where}: 颜色 {v} 未在项目色板中登记")
        return
    errors.append(f"{where}: 颜色 “{v}” 不合规（只允许色板中的 6 位十六进制、none 或 url(#…)）")


def staged_bytes(path: Path) -> bytes | None:
    """返回文件在 Git 暂存区中的内容；不在暂存区时返回 None。"""
    r = git("show", f":{rel(path)}", check=False)
    return r.stdout if r.returncode == 0 else None


def check_svg(path: Path, palette: set[str], errors: list[str], warnings: list[str],
              staged: bool = False) -> None:
    """检查 SVG 规范；staged=True 时检查暂存区里将被提交的内容，而不是工作区。"""
    r = rel(path)
    data = staged_bytes(path) if staged else None
    try:
        root = ET.fromstring(data) if data is not None else ET.parse(path).getroot()
    except ET.ParseError as e:
        errors.append(f"{r}: XML 解析失败：{e}")
        return
    if root.tag != f"{{{SVG_NS}}}svg":
        errors.append(f"{r}: 根元素必须是带 xmlns 的 <svg>")
        return
    if not root.get("viewBox"):
        errors.append(f"{r}: 缺少 viewBox")
    if root.get("width") or root.get("height"):
        errors.append(f"{r}: 根元素不要写死 width/height，只用 viewBox")
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag in FORBIDDEN_TAGS:
            errors.append(f"{r}: <{tag}> {FORBIDDEN_TAGS[tag]}")
        for attr, val in el.attrib.items():
            name = attr.split("}")[-1]
            if name == "href" and not val.startswith("#"):
                errors.append(f"{r}: 禁止外部引用 {val}")
            elif name in COLOR_ATTRS:
                check_color_value(val, palette, f"{r} <{tag} {name}>", errors)
            elif name == "style":
                for prop, v in STYLE_COLOR_RE.findall(val):
                    check_color_value(v, palette, f"{r} <{tag} style {prop}>", errors)
        if tag == "style" and el.text:
            for prop, v in STYLE_COLOR_RE.findall(el.text):
                check_color_value(v, palette, f"{r} <style {prop}>", errors)


def check_project(p: Project, errors: list[str], warnings: list[str], staged: bool = False) -> None:
    try:
        cfg = p.cfg
    except LogoError as e:
        errors.append(str(e))
        return
    r = rel(p.toml_path)
    for sect in ("project", "state", "palette"):
        if sect not in cfg:
            errors.append(f"{r}: 缺少 [{sect}]")
    pal = cfg.get("palette", {})
    for k, v in pal.items():
        if not HEX6_RE.match(str(v).upper()):
            errors.append(f"{r}: 色板 {k} = {v!r} 不是 6 位十六进制")
    vals = [str(v).upper() for v in pal.values()]
    if len(vals) != len(set(vals)):
        warnings.append(f"{r}: 色板中存在重复色值，配色模式映射可能产生歧义")
    st = cfg.get("state", {})
    stage = st.get("stage")
    if stage not in STAGES:
        errors.append(f"{r}: stage 必须是 {', '.join(STAGES)} 之一")
    valid = set()
    for key, fn in (("selected_direction", p.direction), ("selected_version", p.version_dir),
                    ("current_release", p.release_dir)):
        if st.get(key):
            try:
                fn(st[key])
                valid.add(key)
            except LogoError as e:
                errors.append(f"{r}: {key} 无效：{e}")

    # 状态之间必须互相一致（R5：唯一事实来源不能自相矛盾）
    sel_dir, sel_ver, cur_rel = st.get("selected_direction"), st.get("selected_version"), st.get("current_release")
    if {"selected_direction", "selected_version"} <= valid and sel_ver.split("-")[0] != sel_dir:
        errors.append(f"{r}: selected_version {sel_ver} 不属于 selected_direction {sel_dir}")
    if stage in ("releasing", "delivered") and not cur_rel:
        errors.append(f"{r}: 阶段为 {stage}，但没有 current_release")

    font_ids = {f.get("id") for f in load_fonts()}
    for fid in cfg.get("fonts", {}).get("used", []):
        if fid not in font_ids:
            errors.append(f"{r}: 使用了未登记的字体 {fid}")
    palette = set(vals)
    for d in p.directions():
        if not re.fullmatch(r"[A-Z]-[a-z0-9-]+", d.name):
            errors.append(f"{rel(d)}: 方向目录应命名为 A-name")
        if not (d / "direction.md").exists():
            errors.append(f"{rel(d)}: 缺少 direction.md")
        for v in p.versions_in(d):
            for need in ("logo.svg", "notes.md"):
                if not (v / need).exists():
                    errors.append(f"{rel(v)}: 缺少 {need}")
            if (v / "logo.svg").exists():
                check_svg(v / "logo.svg", palette, errors, warnings, staged)

    modes = cfg.get("color_modes", {}).get("modes", ["color"])
    release_versions: dict[str, str] = {}
    for rd in p.releases():
        try:
            release_versions[rd.name] = p.release_root_version(rd.name)
        except LogoError as e:
            errors.append(f"{rel(rd)}: 追溯链断裂：{e}")
        ldir = rd / "layouts"
        for f in sorted(ldir.glob("*.svg")) if ldir.exists() else []:
            check_svg(f, palette, errors, warnings, staged)
            m = re.fullmatch(r"(.+)\.([a-z]+)", f.stem)  # 手工配色覆盖文件 name.mode.svg（cover-2.7x1 不算）
            if m:
                base, mode = m.groups()
                if not (ldir / f"{base}.svg").exists():
                    errors.append(f"{rel(f)}: 覆盖文件没有对应的基础版式 {base}.svg")
                if mode == "color" or mode not in modes:
                    errors.append(f"{rel(f)}: 覆盖文件的配色 {mode} 不在 [color_modes] modes 中（color 不可覆盖）")

    if cur_rel in release_versions:
        root = release_versions[cur_rel]
        if sel_ver and sel_ver != root:
            warnings.append(f"{r}: 选定版本 {sel_ver} 尚未发布（当前发布 {cur_rel} 基于 {root}）；"
                            f"确认定稿后运行 ./logo release {p.slug} --from {sel_ver}")
        ldir = p.release_dir(cur_rel) / "layouts"
        try:
            missing = [n for n in required_layouts(p) if not (ldir / f"{n}.svg").exists()]
            if missing:
                warnings.append(f"{rel(ldir)}: 缺少现行导出配置要求的版式 {', '.join(missing)}，"
                                "需新建发布补齐后才能导出")
            if "social" in cfg.get("export", {}).get("profiles", []):
                if (w := avatar_safe_warning(ldir, load_profiles()["social"]["avatar"])):
                    warnings.append(w)
        except LogoError as e:
            warnings.append(f"{rel(ldir)}: 无法完成版式检查：{e}")

    final = p.dir / "final"
    if final.exists():
        mf = final / "manifest.json"
        if not mf.exists():
            errors.append(f"{rel(final)}: 缺少 manifest.json，final/ 只能由 ./logo export 生成")
        else:
            manifest = json.loads(mf.read_text(encoding="utf-8"))
            files = manifest.get("files", {})
            for name, digest in files.items():
                f = final / name
                if not f.exists():
                    errors.append(f"{rel(f)}: 清单中的文件缺失，请重新导出")
                elif sha256_file(f) != digest:
                    errors.append(f"{rel(f)}: 内容与清单不符（final/ 禁止手改，请重新导出）")
            listed = set(files) | {"manifest.json"}
            for f in final.rglob("*"):
                if f.is_file() and f.name != ".DS_Store" and f.relative_to(final).as_posix() not in listed:
                    errors.append(f"{rel(f)}: 不在导出清单中（final/ 禁止手动放入文件）")
            if manifest.get("release") != cur_rel:
                warnings.append(f"{rel(mf)}: final/ 由 {manifest.get('release') or '?'} 导出，"
                                f"与 current_release {cur_rel or '（空）'} 不一致，请重新导出")
            elif not manifest.get("based_on") or not manifest.get("design_version"):
                warnings.append(f"{rel(mf)}: 清单缺少 based_on/design_version（旧版工具导出），"
                                "请重新导出以补全追溯链")


def check_fonts(errors: list[str], warnings: list[str], projects: list[Project]) -> None:
    if not FONTS_TOML.exists():
        return
    seen = set()
    for f in load_fonts():
        fid = f.get("id", "?")
        where = f"fonts.toml [{fid}]"
        if fid in seen:
            errors.append(f"{where}: id 重复")
        seen.add(fid)
        for key in ("id", "family", "weight", "file", "license", "license_file", "tier",
                    "official_url", "download_url", "sha256", "retrieved_on"):
            if not f.get(key):
                errors.append(f"{where}: 缺少字段 {key}")
        if f.get("tier") not in ("open-source", "free-commercial"):
            errors.append(f"{where}: tier 必须是 open-source 或 free-commercial")
        if f.get("tier") == "free-commercial" and not f.get("license_review"):
            errors.append(f"{where}: 非开源字体必须填写 license_review（指向授权复核决策记录）")
        if f.get("file"):
            fp = FONTS_DIR / f["file"]
            if not fp.exists():
                errors.append(f"{where}: 字体文件不存在 {rel(fp)}")
            elif f.get("sha256") and sha256_file(fp) != f["sha256"]:
                errors.append(f"{where}: sha256 与文件不符")
        if f.get("license_file") and not (FONTS_DIR / f["license_file"]).exists():
            errors.append(f"{where}: 授权文件不存在")
    # used_by 反查必须与各项目的 [fonts] used 双向一致
    by_font = {f.get("id"): set(f.get("used_by", [])) for f in load_fonts()}
    slugs = {p.slug for p in projects}
    for p in projects:
        for fid in p.cfg.get("fonts", {}).get("used", []):
            if fid in by_font and p.slug not in by_font[fid]:
                errors.append(f"fonts.toml [{fid}]: used_by 缺少 {p.slug}（该项目 [fonts] used 中声明了此字体）")
    for fid, users in by_font.items():
        for slug in sorted(users):
            if slug not in slugs:
                errors.append(f"fonts.toml [{fid}]: used_by 中的项目 {slug} 不存在")
            elif fid not in Project(slug).cfg.get("fonts", {}).get("used", []):
                errors.append(f"fonts.toml [{fid}]: used_by 含 {slug}，但该项目 [fonts] used 未声明此字体")


def profile_warnings(prof: dict, enabled: list[str] | None = None) -> list[str]:
    out = []
    limit = dt.date.today() - dt.timedelta(days=365)

    def chk(name: str, item: dict):
        if item.get("generic"):
            return  # 通用比例，不对应具体平台
        v = item.get("verified_on", "")
        if not v:
            out.append(f"导出规格 {name} 尚未按官方文档核实")
        elif dt.date.fromisoformat(v) < limit:
            out.append(f"导出规格 {name} 核实已超过 12 个月（{v}），建议复核")
        if not item.get("source"):
            out.append(f"导出规格 {name} 缺少官方规格来源链接 source")

    for key in ("ios", "android"):
        if key in prof and (enabled is None or key in enabled):
            chk(key, prof[key])
    if "social" in prof and (enabled is None or "social" in enabled):
        chk("social.avatar", prof["social"]["avatar"])
        for c in prof["social"].get("cover", []):
            chk(f"social.cover.{c['id']}", c)
        pend = prof["social"].get("pending", [])
        if pend:
            out.append("以下平台规格待补充：" + "、".join(pend))
    return out


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=check)


def check_immutability(staged: bool, errors: list[str]) -> None:
    if git("rev-parse", "--verify", "HEAD", check=False).returncode != 0:
        return  # 尚无提交
    diff_args = ["diff", "--name-status", "--no-renames", "-z"] + (["--cached"] if staged else []) + ["HEAD"]
    raw = git(*diff_args).stdout.decode("utf-8")
    parts = [x for x in raw.split("\0") if x]
    changes = list(zip(parts[0::2], parts[1::2]))
    for status, path in changes:
        if status == "A":
            continue
        if any(pt.match(path) for pt in FROZEN_PATTERNS):
            action = "删除" if status == "D" else "修改"
            errors.append(f"{path}: 已冻结的文件被{action}（请新建版本/发布，而不是改动已提交的内容）")
        elif any(pt.match(path) for pt in APPEND_ONLY_PATTERNS):
            if status == "D":
                errors.append(f"{path}: 只追加文件不能删除")
                continue
            old = git("show", f"HEAD:{path}").stdout
            new = git("show", f":{path}").stdout if staged else (ROOT / path).read_bytes()
            if not new.startswith(old.rstrip(b"\n")):
                errors.append(f"{path}: 只允许在末尾追加内容，已有内容不能修改")


def path_allowed(path: str) -> bool:
    parts = path.split("/")
    if len(parts) == 1:
        return path in ALLOWED_ROOT_FILES
    if parts[0] in ALLOWED_ROOT_DIRS:
        return True
    if parts[0] == "projects" and len(parts) >= 3 and SLUG_RE.match(parts[1]):
        return any(pt.fullmatch("/".join(parts[2:])) for pt in PROJECT_FILE_PATTERNS)
    return False


def check_stray_files(errors: list[str], warnings: list[str]) -> None:
    """拦截目录结构之外的文件（描摹中间件、临时脚本等）。已纳入/暂存的文件报错，未跟踪的文件提醒。"""
    if git("rev-parse", "--git-dir", check=False).returncode != 0:
        return

    def ls(*args: str) -> set[str]:
        return {x for x in git("ls-files", "-z", *args).stdout.decode("utf-8").split("\0") if x}

    tracked = ls("--cached")
    untracked = ls("--others", "--exclude-standard")
    for path in sorted(tracked | untracked):
        if not path_allowed(path):
            msg = f"{path}: 不在规定的目录结构中（临时文件请放 .cache/，或删除）"
            (errors if path in tracked else warnings).append(msg)


def build_index() -> str:
    lines = ["# 项目看板", "", "<!-- 由 ./logo index 自动生成，请勿手改 -->", "",
             "| 项目 | 名称 | 阶段 | 方向数 | 选定方向 | 选定版本 | 当前发布 | 更新 |",
             "|---|---|---|---|---|---|---|---|"]
    for p in all_projects():
        c = p.cfg
        st = c.get("state", {})
        lines.append(
            f"| [{p.slug}](projects/{p.slug}/brief.md) | {c.get('project', {}).get('name', '')} | "
            f"{st.get('stage', '')} | {len(p.directions())} | {st.get('selected_direction') or '—'} | "
            f"{st.get('selected_version') or '—'} | {st.get('current_release') or '—'} | {st.get('updated', '')} |")
    if len(lines) == 6:
        lines.append("| — | 暂无项目 | | | | | | |")
    return "\n".join(lines) + "\n"


def cmd_index(a) -> None:
    PROJECTS_MD.write_text(build_index(), encoding="utf-8")
    print(f"已更新：{rel(PROJECTS_MD)}")


def cmd_check(a) -> None:
    errors: list[str] = []
    warnings: list[str] = []
    projects = [Project(a.slug)] if a.slug else all_projects()
    for p in projects:
        check_project(p, errors, warnings, a.staged)
    if not a.slug:
        check_fonts(errors, warnings, projects)
        check_stray_files(errors, warnings)
        try:
            warnings.extend(profile_warnings(load_profiles()))
        except LogoError as e:
            errors.append(str(e))
        if not PROJECTS_MD.exists() or PROJECTS_MD.read_text(encoding="utf-8") != build_index():
            msg = "PROJECTS.md 不是最新，请运行 ./logo index"
            (errors if a.staged else warnings).append(msg)
    check_immutability(a.staged, errors)
    for w in warnings:
        print(f"⚠ {w}")
    for e in errors:
        print(f"✗ {e}")
    if errors:
        print(f"\n检查未通过：{len(errors)} 个错误，{len(warnings)} 个提醒")
        sys.exit(1)
    print(f"检查通过（{len(warnings)} 个提醒）")


# ---------------------------------------------------------------------------
# 命令：setup
# ---------------------------------------------------------------------------
def cmd_setup(a) -> None:
    run(["git", "-C", str(ROOT), "config", "core.hooksPath", ".githooks"])
    print("已启用 Git 钩子（.githooks/pre-commit）")
    venv = ROOT / ".venv"
    if not venv.exists():
        run([sys.executable, "-m", "venv", str(venv)])
        print("已创建 .venv")
    print("正在安装 Python 依赖……")
    run([str(venv / "bin" / "pip"), "install", "-q", "-r", str(ROOT / "tools" / "requirements.txt")])
    for tool in ("resvg", "magick"):
        if not shutil.which(tool):
            print(f"⚠ 未找到 {tool}，请用 Homebrew 安装")
    print("环境就绪")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="logo", description="LOGODesign 项目管理工具")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="<命令>")

    s = sub.add_parser("setup", help="安装依赖并启用 Git 钩子")
    s.set_defaults(fn=cmd_setup)

    s = sub.add_parser("new", help="新建项目")
    s.add_argument("slug", help="项目短名（小写英文/数字/短横线）")
    s.add_argument("--name", help="项目显示名称，可用中文")
    s.set_defaults(fn=cmd_new)

    s = sub.add_parser("direction", help="新建设计方向")
    s.add_argument("slug")
    s.add_argument("letter", help="方向编号，单个大写字母，如 A")
    s.add_argument("name", help="方向英文短名，如 mountain")
    s.add_argument("--title", help="方向中文标题")
    s.set_defaults(fn=cmd_direction)

    s = sub.add_parser("version", help="在某方向下新建版本")
    s.add_argument("slug")
    s.add_argument("direction", help="方向编号，如 A")
    s.add_argument("--from", dest="based_on", help="基于哪个版本，如 B-v002（默认：该方向最新版本）")
    s.add_argument("--blank", action="store_true", help="从空白画布开始")
    s.set_defaults(fn=cmd_version)

    s = sub.add_parser("concept", help="登记概念灵感图（含提示词）")
    s.add_argument("slug")
    s.add_argument("image", help="图片路径")
    s.add_argument("--name", required=True, help="英文短名")
    s.add_argument("--prompt", required=True, help="生成提示词或来源说明")
    s.add_argument("--tool", default="generate_image", help="生成工具")
    s.set_defaults(fn=cmd_concept)

    s = sub.add_parser("decision", help="新建决策记录")
    s.add_argument("slug")
    s.add_argument("name", help="英文短名，如 select-direction-a")
    s.add_argument("--title", help="中文标题")
    s.set_defaults(fn=cmd_decision)

    s = sub.add_parser("release", help="新建发布（定稿版式集）")
    s.add_argument("slug")
    s.add_argument("--from", dest="based_on", required=True, help="基于版本（A-v004）或上一个发布（r1）")
    s.set_defaults(fn=cmd_release)

    s = sub.add_parser("state", help="查看或更新项目状态")
    s.add_argument("slug")
    s.add_argument("assignments", nargs="*", help="key=value，如 stage=iterating selected_version=A-v003")
    s.set_defaults(fn=cmd_state)

    s = sub.add_parser("outline", help="文字转路径（仅限已登记字体）")
    s.add_argument("--font", required=True, help="fonts.toml 中的字体 id")
    s.add_argument("--text", required=True)
    s.add_argument("--size", type=float, default=100, help="字号（SVG 单位）")
    s.add_argument("--tracking", type=float, default=0, help="字间距（SVG 单位）")
    s.add_argument("--fill", required=True, help="填充色（须在项目色板中）")
    s.add_argument("--project", help="项目短名：校验填充色在色板中、字体已在 [fonts] used 中声明")
    s.add_argument("--svg", help="输出为独立 SVG 文件；不填则输出 <path> 片段")
    s.set_defaults(fn=cmd_outline)

    s = sub.add_parser("preview", help="渲染预览图（彩色/单色/反白/小尺寸）")
    s.add_argument("slug")
    s.add_argument("target", help="A-v001、r1（全部版式）、r1/icon 或 SVG 路径")
    s.set_defaults(fn=cmd_preview)

    s = sub.add_parser("compare", help="生成多版本对比图（默认各方向最新版本）")
    s.add_argument("slug")
    s.add_argument("ids", nargs="*", help="版本号列表")
    s.set_defaults(fn=cmd_compare)

    s = sub.add_parser("export", help="由发布生成 final/ 交付文件")
    s.add_argument("slug")
    s.add_argument("--release", help="发布号（默认 current_release）")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("check", help="检查规范、不可变规则与导出一致性")
    s.add_argument("slug", nargs="?")
    s.add_argument("--staged", action="store_true", help="检查暂存区（供 pre-commit 使用）")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("index", help="重新生成 PROJECTS.md")
    s.set_defaults(fn=cmd_index)
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except LogoError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
