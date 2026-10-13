#!/usr/bin/env python3
"""LOGODesign 命令行工具。

所有项目操作（新建、出版本、发布、导出、检查）都通过本脚本完成，
以保证目录结构、命名、追溯记录和不可变规则始终一致。

用法：./logo <命令> [参数]，运行 ./logo -h 查看全部命令。
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.parse
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ROOT / "projects"  # 独立的项目 Git 仓库（系统仓库中被忽略）
TEMPLATES = ROOT / "templates"
CONFIG = ROOT / "config"
PROFILES_TOML = CONFIG / "export-profiles.toml"
CACHE = ROOT / ".cache"
FONTS_DIR = ROOT / "assets" / "fonts"
FONTS_TOML = FONTS_DIR / "fonts.toml"
PROJECTS_MD = PROJECTS / "PROJECTS.md"

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VERSION_ID_RE = re.compile(r"^([A-Z])-v(\d{3})$")
RELEASE_RE = re.compile(r"^r(\d+)$")
CONCEPT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
FONT_BINARY_RE = re.compile(r"\.(otf|ttf|ttc|woff2?)$", re.I)  # 字体文件不进 Git
HEX6_RE = re.compile(r"^#[0-9A-F]{6}$")
HEX_IN_TEXT_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")
STAGES = ["briefing", "exploring", "iterating", "releasing", "delivered", "paused"]
STATE_KEYS = ["stage", "selected_direction", "selected_version", "current_release"]
COLOR_ATTRS = {"fill", "stroke", "stop-color", "flood-color", "lighting-color", "color"}
# CSS 中指向外部资源的 url(...)（url(#id) 引用文档内元素，允许）与 @import
EXTERNAL_CSS_RE = re.compile(r"url\(\s*['\"]?(?!#)|@import", re.I)
STYLE_COLOR_RE = re.compile(r"(fill|stroke|stop-color|flood-color|lighting-color|color)\s*:\s*([^;}\s]+)")
FORBIDDEN_TAGS = {
    "text": "文字必须转为路径（使用 ./logo outline）",
    "tspan": "文字必须转为路径（使用 ./logo outline）",
    "textPath": "文字必须转为路径（使用 ./logo outline）",
    "image": "禁止内嵌位图",
    "script": "禁止脚本",
    "foreignObject": "禁止 foreignObject",
}

# 不可变（提交后禁止修改/删除）与只追加文件的路径规则（相对项目仓库根目录）
FROZEN_PATTERNS = [
    re.compile(r"^[^/]+/directions/[^/]+/v\d{3}/logo\.svg$"),
    re.compile(r"^[^/]+/releases/r\d+/layouts/[^/]+\.svg$"),
    re.compile(r"^[^/]+/concepts/\d{3}-[^/]+\.(png|jpe?g|webp)$"),
]

APPEND_ONLY_PATTERNS = [
    re.compile(r"^[^/]+/directions/[^/]+/v\d{3}/notes\.md$"),
    re.compile(r"^[^/]+/directions/[^/]+/direction\.md$"),
    re.compile(r"^[^/]+/releases/r\d+/(release|guide)\.md$"),
    re.compile(r"^[^/]+/decisions/[^/]+\.md$"),
    re.compile(r"^[^/]+/concepts/[^/]+\.md$"),
]

# 系统仓库允许提交的文件（相对仓库根目录，精确到目录与文件类型）；与 .gitignore 的白名单保持一致
SYSTEM_FILE_PATTERNS = [re.compile(p) for p in (
    r"\.gitignore", r"AGENTS\.md", r"CLAUDE\.md", r"README\.md", r"logo",
    r"\.agents/skills/[a-z0-9-]+/[A-Za-z0-9_./-]+\.(md|py|sh|toml|json)",
    r"\.claude/settings\.json", r"\.claude/(commands|agents|skills)/[A-Za-z0-9_./-]+\.md",
    r"\.githooks/pre-commit", r"\.githooks/projects/pre-commit",
    r"assets/fonts/fonts\.toml", r"assets/fonts/[a-z0-9-]+/(LICENSE|OFL)[A-Za-z0-9_.-]*\.(txt|md)",
    r"config/[a-z0-9-]+\.toml",
    r"templates/[a-z0-9-]+\.(md|svg|toml|gitignore)",
    r"tools/[a-z0-9_]+\.py", r"tools/requirements\.txt",
    r"tests/[a-z0-9_]+\.py",
)]
# 项目仓库根目录允许的文件；项目目录内的文件见 PROJECT_FILE_PATTERNS
PROJECTS_ROOT_FILES = {"PROJECTS.md", ".gitignore"}

# 密钥与凭据：文件名特征，以及内容特征（私钥、各平台访问令牌）
SECRET_NAME_RE = re.compile(
    r"^(\.env(\..+)?|.*\.(pem|key|p12|pfx|jks|keystore|gpg)|id_(rsa|dsa|ecdsa|ed25519)(\.pub)?"
    r"|\.netrc|\.npmrc|\.pypirc)$", re.I)
# 仅用于系统仓库：项目仓库的文件名来自用户输入（如 001-secret-garden.md），按关键词判断会误伤
SECRET_KEYWORD_RE = re.compile(r"credential|secret", re.I)
PROJECTS_GITIGNORE_TEMPLATE = TEMPLATES / "projects.gitignore"
BINARY_SUFFIX_RE = re.compile(r"\.(png|jpe?g|webp|gif|ico|otf|ttf|ttc|woff2?|zip)$", re.I)
SECRET_CONTENT_RE = re.compile("|".join((
    r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----",  # 各类私钥
    r"\bAKIA[0-9A-Z]{16}\b",                     # AWS Access Key
    r"\bgh[pousr]_[A-Za-z0-9]{36,}",              # GitHub token
    r"\bgithub_pat_[A-Za-z0-9_]{60,}",            # GitHub fine-grained token
    r"\bsk-ant-[A-Za-z0-9_-]{20,}",               # Anthropic API key
    r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}",          # OpenAI 风格 API key
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}",            # Slack token
    r"\bAIza[0-9A-Za-z_-]{35}",                   # Google API key
)))
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

# 项目文档与其模板（用于检测未填写的模板占位）
DOC_TEMPLATES = [
    (re.compile(r"^[^/]+/brief\.md$"), "brief.md"),
    (re.compile(r"^[^/]+/directions/[^/]+/direction\.md$"), "direction.md"),
    (re.compile(r"^[^/]+/directions/[^/]+/v\d{3}/notes\.md$"), "version-notes.md"),
    (re.compile(r"^[^/]+/releases/r\d+/release\.md$"), "release.md"),
    (re.compile(r"^[^/]+/releases/r\d+/guide\.md$"), "release-guide.md"),
    (re.compile(r"^[^/]+/decisions/\d{3}-[^/]+\.md$"), "decision.md"),
    (re.compile(r"^[^/]+/concepts/\d{3}-[^/]+\.md$"), "concept.md"),
]
DECISION_REF_RE = re.compile(r"decisions/(\d{3}-[a-z0-9-]+\.md)")
CONCEPT_REF_RE = re.compile(r"concepts/(\d{3}-[a-z0-9-]+\.md)")

# 单色/反白版结构检查：只看图形内部（离轮廓 2px 以上）的颜色分界线——外轮廓任何配色都会保留，不能算数。
# 非彩色版保留的内部分界线不足彩色版的 STRUCTURE_MIN_RATIO 时提醒。标定（256px 渲染）：
# cat-dog 各版本单色化后 0%；正确修复（次色留白）100%；单色 Logo、acme 等无内部结构的跳过。
STRUCTURE_MIN_RATIO = 0.6
STRUCTURE_MIN_INTERIOR = 150  # 彩色版内部分界线少于此像素数视为没有内部结构（如同色图形重叠的抗锯齿接缝）
STRUCTURE_RENDER_SIZE = 256
STRUCTURE_CACHE = CACHE / "structure-cache.json"
MODE_NAMES = {"color": "彩色", "mono": "单色", "reversed": "反白"}
STRUCTURE_PALETTE_HINT = "在 [color_modes.mono] 中把部分颜色映射为 #FFFFFF、在 [color_modes.reversed] 中映射为 #000000，作为留白保留细节"

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

    def concept_ref(self, ref: str) -> str:
        """把 001 / 001-mountain 解析为 concepts/001-mountain.md，概念图未登记时报错。"""
        m = re.fullmatch(r"(\d{3})(-[a-z0-9-]+)?", ref)
        hits = sorted((self.dir / "concepts").glob(f"{m.group(1)}-*.md")) if m else []
        hits = [h for h in hits if not m.group(2) or h.stem == ref]
        if len(hits) != 1:
            raise LogoError(f"概念图未登记：{ref}（先用 ./logo concept 登记，再用编号引用）")
        return f"concepts/{hits[0].name}"

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


def release_structure_warnings(p: Project, ldir: Path, prof: dict) -> list[str]:
    """检查发布中各标志版式（及 Android 单色层所用版式）的单色/反白结构；内容相同的版式只检查一次。"""
    cfg = p.cfg
    modes = cfg.get("color_modes", {}).get("modes", ["color"])
    targets = {n: modes for n in cfg.get("layouts", {}).get("logo", [])}
    ap = prof.get("android", {})
    if "android" in cfg.get("export", {}).get("profiles", []) and ap.get("adaptive_foreground_layout"):
        fg = ap["adaptive_foreground_layout"]
        targets[fg] = sorted(set(targets.get(fg, [])) | {"color", "mono"})  # Android 13+ 主题图标使用单色层
    groups: dict[tuple, list[str]] = {}  # 内容与配色模式都相同的版式合并为一组，只检查一次
    for name, ms in targets.items():
        if (ldir / f"{name}.svg").exists():
            key = (tuple(ms), tuple(layout_mode_text(p, ldir, name, m) for m in ms))
            groups.setdefault(key, []).append(name)
    out = []
    for (ms, _), names in groups.items():
        first = names[0]
        label = rel(ldir / f"{first}.svg") + (f"（同内容：{'、'.join(names[1:])}）" if names[1:] else "")
        hint = f"可提供 {first}.mono.svg / {first}.reversed.svg 等手工配色文件，或{STRUCTURE_PALETTE_HINT}"
        if (w := structure_warning(label, lambda m, n=first: layout_mode_text(p, ldir, n, m), ms, hint)):
            out.append(w)
    return out


def render_android_foreground(p: Project, ldir: Path, ap: dict, mode: str, out: Path) -> Path:
    """按自适应图标规格生成前景层：图形缩放进中心区域，再扩展到整层画布（透明）。"""
    size = ap["adaptive_size"]
    with tempfile.TemporaryDirectory() as td:
        tmp_svg = Path(td) / "fg.svg"
        tmp_svg.write_text(layout_mode_text(p, ldir, ap["adaptive_foreground_layout"], mode), encoding="utf-8")
        render_fit(tmp_svg, out, round(size * ap["adaptive_safe_ratio"]))
    run(["magick", str(out), "-background", "none", "-gravity", "center", "-extent", f"{size}x{size}", str(out)])
    return out


def android_safe_warning(png: Path, ap: dict) -> str | None:
    """前景图形超出官方 66dp 安全区（108dp 画布内的中心圆）时返回提醒。"""
    ratio = ap.get("adaptive_safe_zone", 66 / 108)
    if alpha_outside_circle(png, ratio) > 0.05:
        return f"Android 自适应图标前景超出 {round(ratio * 108)}dp 安全区，在部分启动器的遮罩下会被裁切"
    return None


def interior_edge_pixels(svg_text: str) -> float:
    """渲染后统计图形内部的颜色分界像素数（排除外轮廓附近 2px），结果按内容哈希缓存。"""
    key = hashlib.sha256(f"v2|{STRUCTURE_RENDER_SIZE}|{svg_text}".encode("utf-8")).hexdigest()
    try:
        cache = json.loads(STRUCTURE_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if key in cache:
        return cache[key]
    with tempfile.TemporaryDirectory() as td:
        svg = Path(td) / "s.svg"
        svg.write_text(svg_text, encoding="utf-8")
        png = render_fit(svg, Path(td) / "s.png", STRUCTURE_RENDER_SIZE)
        # 颜色边缘：逐通道对称梯度（两侧都计），取最大值后二值化；
        # 内部掩膜：不透明区域向内收缩 2px，去掉外轮廓及抗锯齿造成的环；两者相乘后计数
        val = float(run(["magick", str(png),
                         "(", "-clone", "0", "-alpha", "off", "-separate", "-morphology", "Edge", "Diamond:1",
                         "-evaluate-sequence", "max", "-threshold", "12%", ")",
                         "(", "-clone", "0", "-alpha", "extract", "-threshold", "50%",
                         "-morphology", "Erode", "Disk:2", ")",
                         "-delete", "0", "-compose", "multiply", "-composite",
                         "-format", "%[fx:mean*w*h]", "info:"], capture=True).strip() or 0)
    cache[key] = val
    STRUCTURE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    STRUCTURE_CACHE.write_text(json.dumps(cache), encoding="utf-8")
    return val


def structure_warning(label: str, mode_text, modes: list[str], fix_hint: str) -> str | None:
    """比较彩色版与各非彩色版的分界线；有配色模式保留不足 STRUCTURE_MIN_RATIO 时返回一条提醒。

    mode_text(mode) 返回该配色下的 SVG 文本（与导出相同的逻辑，含手工覆盖文件）。
    """
    color = interior_edge_pixels(mode_text("color"))
    if color < STRUCTURE_MIN_INTERIOR:
        return None  # 没有可丢失的内部结构
    lost = []
    for mode in modes:
        if mode == "color":
            continue
        ratio = min(interior_edge_pixels(mode_text(mode)) / color, 1.0)
        if ratio < STRUCTURE_MIN_RATIO:
            lost.append(f"{MODE_NAMES.get(mode, mode)}版约 {ratio:.0%}")
    if not lost:
        return None
    return (f"{label}：非彩色版只保留了彩色版部分细节（{'、'.join(lost)}），图形可能糊成一块；{fix_hint}")


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
    if not projects_repo_ready():
        raise LogoError("项目仓库 projects/ 尚未初始化，请先运行 ./logo setup")
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
    inspired = p.concept_ref(a.inspired_by) if a.inspired_by else "无"
    vdir = ddir / f"v{num:03d}"
    vdir.mkdir(parents=True)
    if base_svg:
        shutil.copyfile(base_svg, vdir / "logo.svg")
    else:
        shutil.copyfile(TEMPLATES / "blank.svg", vdir / "logo.svg")
    write_new(vdir / "notes.md", render_template(
        "version-notes.md", id=vid, based_on=base or "（无，全新起稿）", inspired_by=inspired,
        direction=ddir.name, date=today()))
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
    if "selected_version" in updates and updates["selected_version"] and "selected_direction" not in updates:
        updates["selected_direction"] = updates["selected_version"].split("-")[0]  # 方向随版本自动对齐
    merged = {**p.cfg["state"], **updates}
    sd, sv = merged.get("selected_direction"), merged.get("selected_version")
    if updates and sd and sv and sv.split("-")[0] != sd:
        raise LogoError(f"selected_version {sv} 不属于 selected_direction {sd}")
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


def kerning_lookup(font):
    """返回 kern(左字形, 右字形) -> 字距调整（字体单位）。读取 GPOS 的 kern 特性，没有时退回旧式 kern 表。"""
    subtables = []
    if "GPOS" in font and font["GPOS"].table.FeatureList:
        gpos = font["GPOS"].table
        idx = {i for fr in gpos.FeatureList.FeatureRecord if fr.FeatureTag == "kern"
               for i in fr.Feature.LookupListIndex}
        for i in sorted(idx):
            lk = gpos.LookupList.Lookup[i]
            subs = [st.ExtSubTable if lk.LookupType == 9 else st for st in lk.SubTable]
            subtables.append([st for st in subs if getattr(st, "LookupType", lk.LookupType) == 2])
    legacy = {}
    if not subtables and "kern" in font:
        for kt in font["kern"].kernTables:
            legacy.update(getattr(kt, "kernTable", {}))

    def adv(vr) -> int:
        return getattr(vr, "XAdvance", None) or 0  # ValueRecord 可能为 None 或不含 XAdvance

    def kern(left: str, right: str) -> int:
        if not subtables:
            return legacy.get((left, right), 0)
        total = 0
        for lookup in subtables:  # 每个查找取第一个命中的子表，多个查找累加
            for st in lookup:
                cov = st.Coverage.glyphs
                if left not in cov:
                    continue
                if st.Format == 1:
                    pairs = st.PairSet[cov.index(left)].PairValueRecord
                    rec = next((r for r in pairs if r.SecondGlyph == right), None)
                    if rec is None:
                        continue
                    total += adv(rec.Value1)
                else:
                    c1 = st.ClassDef1.classDefs.get(left, 0)
                    c2 = st.ClassDef2.classDefs.get(right, 0)
                    total += adv(st.Class1Record[c1].Class2Record[c2].Value1)
                break
        return total

    return kern


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
        raise LogoError(f"字体文件未下载：{rel(path)}，请运行 ./logo fonts fetch {a.font}")
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
    kern = kerning_lookup(font) if not a.no_kern else (lambda _l, _r: 0)
    x = 0.0
    missing = []
    prev = None
    for ch in a.text:
        gname = cmap.get(ord(ch))
        if gname is None:
            missing.append(ch)
            continue
        if prev is not None:
            x += kern(prev, gname) * scale
        prev = gname
        t = (scale, 0, 0, -scale, x, 0)
        gs[gname].draw(TransformPen(pen, t))
        gs[gname].draw(TransformPen(bpen, t))
        x += hmtx[gname][0] * scale + a.tracking
    if missing:
        raise LogoError(f"字体 {a.font} 不包含这些字符：{''.join(missing)}")
    d = pen.getCommands()
    bounds = bpen.bounds or (0, 0, 0, 0)
    safe_text = a.text.replace("--", "‐‐")  # XML 注释中不允许出现 “--”
    comment = (f"<!-- outline: font={a.font} size={a.size} tracking={a.tracking} kern={'off' if a.no_kern else 'on'} "
               f"text=\"{safe_text}\" "
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


def font_status(entry: dict) -> str:
    fp = FONTS_DIR / entry["file"]
    if not fp.exists():
        return "未下载"
    return "已就绪" if sha256_file(fp) == entry.get("sha256") else "校验不符"


def fetch_font(entry: dict) -> str:
    """从 download_url 下载（压缩包取 archive_member），校验 sha256 后写入 assets/fonts/。"""
    dest = FONTS_DIR / entry["file"]
    if font_status(entry) == "已就绪":
        return "已就绪，跳过"
    url = entry["download_url"]
    cache = CACHE / "downloads" / Path(urllib.parse.urlparse(url).path).name
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        part = cache.with_name(cache.name + ".part")
        print(f"下载 {url} …")
        run(["curl", "-sSL", "--fail", "-o", str(part), url])
        part.rename(cache)
    member = entry.get("archive_member")
    try:
        if member:
            with zipfile.ZipFile(cache) as z:
                data = z.read(member)
        else:
            data = cache.read_bytes()
    except (zipfile.BadZipFile, KeyError) as e:
        cache.unlink(missing_ok=True)
        raise LogoError(f"[{entry['id']}] 下载内容无法读取（{e}），已删除缓存，请重试")
    if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
        cache.unlink(missing_ok=True)
        raise LogoError(f"[{entry['id']}] 下载文件的 sha256 与 fonts.toml 不符，已拒绝并删除缓存")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    part.write_bytes(data)
    part.rename(dest)
    return f"已下载并校验 → {rel(dest)}"


def cmd_fonts(a) -> None:
    fonts = load_fonts()
    if a.ids:
        known = {f.get("id") for f in fonts}
        unknown = [i for i in a.ids if i not in known]
        if unknown:
            raise LogoError(f"字体未登记：{', '.join(unknown)}")
        fonts = [f for f in fonts if f.get("id") in a.ids]
    if a.action == "fetch":
        for f in fonts:
            print(f"{f['id']}: {fetch_font(f)}")
        return
    for f in fonts:
        used = ", ".join(f.get("used_by", [])) or "—"
        print(f"{f['id']:<28} {f.get('family', '')} {f.get('weight', '')}  [{font_status(f)}]  使用：{used}")


# ---------------------------------------------------------------------------
# 命令：preview / compare
# ---------------------------------------------------------------------------
def luminance(hexv: str) -> float:
    r, g, b = (int(hexv[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def dark_background(p: Project) -> str:
    """反白预览用的深色底：色板中最深的颜色，不够深时退回 #111111。"""
    darkest = min(p.palette.values(), key=luminance, default="#111111")
    return darkest if luminance(darkest) < 0.25 else "#111111"


def make_preview_sheet(p: Project, mode_text, out: Path) -> Path:
    """mode_text(mode) 返回该配色下的 SVG 文本（与导出使用同一套逻辑）。"""
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        variants = {}
        for mode in ("color", "mono", "reversed"):
            f = t / f"{mode}.svg"
            f.write_text(mode_text(mode), encoding="utf-8")
            variants[mode] = f
        cells = []
        for mode, bg in (("color", "white"), ("mono", "white"), ("reversed", dark_background(p))):
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
    if svg.parent.name == "layouts" and RELEASE_RE.fullmatch(svg.parent.parent.name):
        def mode_text(mode: str) -> str:  # 发布版式：与导出一致，优先使用 name.mode.svg 覆盖文件
            return layout_mode_text(p, svg.parent, svg.stem, mode)
    else:
        def mode_text(mode: str) -> str:
            return recolor_svg(svg, p.mode_map(mode))
    out = make_preview_sheet(p, mode_text, CACHE / p.slug / "preview" / f"{label}.png")
    print(f"预览：{rel(out)}")
    print("第一行：彩色 / 单色 / 反白；第二行：16px×8、32px×4、64px×2 放大查看小尺寸效果")
    modes = p.cfg.get("color_modes", {}).get("modes", ["color", "mono", "reversed"])
    if (w := structure_warning(a.target, mode_text, modes,
                               f"可{STRUCTURE_PALETTE_HINT}，或在发布时提供 <版式>.mono.svg")):
        print(f"⚠ {w}")


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
    guide_rel = f"{p.slug}/releases/{rid}/guide.md"
    if not (rdir / "guide.md").exists():
        raise LogoError(f"{rid} 缺少 guide.md（交付物的一部分）")
    holes = unfilled_placeholders(guide_rel, (rdir / "guide.md").read_text(encoding="utf-8"))
    if holes:
        raise LogoError(f"{rid}/guide.md 是交付物的一部分，还有 {len(holes)} 处模板占位未填写（如“{holes[0]}”）；"
                        "guide.md 只能追加，请新建发布并在提交前填完")
    need = required_layouts(p)
    missing = [n for n in need if not (ldir / f"{n}.svg").exists()]
    if missing:
        raise LogoError(f"{rid} 缺少版式：{', '.join(missing)}")

    errors, warnings = [], []
    for f in sorted(ldir.glob("*.svg")):
        check_svg(f, layout_palette(p, f, set(p.palette.values())), errors, warnings)
    if errors:
        raise LogoError("版式未通过检查：\n  " + "\n  ".join(errors))

    prof = load_profiles()
    enabled = cfg.get("export", {}).get("profiles", [])
    prefix = p.slug
    # 先生成到临时目录，全部成功后再整体替换 final/，中途失败不会留下残缺的交付物
    final = p.dir / ".final-build"
    if final.exists():
        shutil.rmtree(final)
    (final / "svg").mkdir(parents=True)
    try:
        manifest = build_export(p, cfg, rid, rdir, ldir, root_version, need, final, prefix, prof, enabled, warnings)
    except BaseException:
        shutil.rmtree(final, ignore_errors=True)
        raise
    # 先把旧 final/ 挪开再换入新目录，任何时刻都不会出现缺失或半成品的 final/
    target, old = p.dir / "final", p.dir / ".final-old"
    if old.exists():
        shutil.rmtree(old)
    if target.exists():
        target.rename(old)
    final.rename(target)
    shutil.rmtree(old, ignore_errors=True)
    print(f"已导出 {len(manifest['files'])} 个文件 → {rel(target)}（{rid} ← {root_version}）")
    if rid == cfg["state"].get("current_release"):
        p.set_state(stage="delivered")
        print("项目阶段已更新为 delivered；请运行 ./logo index 并提交")
    for w in warnings:
        print(f"⚠ {w}")
    for w in profile_warnings(prof, enabled):
        print(f"⚠ {w}")


def build_export(p: Project, cfg: dict, rid: str, rdir: Path, ldir: Path, root_version: str, need: list[str],
                 final: Path, prefix: str, prof: dict, enabled: list[str], warnings: list[str]) -> dict:
    """在 final 目录中生成全部交付文件与 manifest.json，返回 manifest。"""
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
        ex = cfg.get("export", {})
        bg_key = ex.get("android_background", "")
        if bg_key not in p.palette:
            raise LogoError(f"[export] android_background 必须是色板中的色名，当前：{bg_key!r}")
        adir = png / "android" / "adaptive"
        bg = adir / "ic_launcher_background.png"
        adir.mkdir(parents=True, exist_ok=True)
        run(["magick", "-size", f"{size}x{size}", f"xc:{p.palette[bg_key]}", str(bg)])
        outputs.append(bg)
        for mode, fname in ((ex.get("android_foreground_mode", "color"), "ic_launcher_foreground.png"),
                            ("mono", "ic_launcher_monochrome.png")):
            f = render_android_foreground(p, ldir, ap, mode, adir / fname)
            outputs.append(f)
        if (w := android_safe_warning(adir / "ic_launcher_foreground.png", ap)):
            warnings.append(w)
    warnings.extend(release_structure_warnings(p, ldir, prof))

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
    def short_head(repo: str) -> str:
        r = git("rev-parse", "--short", "HEAD", check=False, repo=repo)
        return r.stdout.decode().strip() if r.returncode == 0 else ""

    manifest = {
        "project": p.slug,
        "release": rid,
        "based_on": load_release_meta(rdir).get("based_on", ""),
        "design_version": root_version,
        "generated_on": today(),
        "git_head": short_head("projects"),  # 项目仓库：设计内容的版本
        "system_head": short_head("system"),  # 系统仓库：生成它的工具版本
        "files": {o.relative_to(final).as_posix(): sha256_file(o) for o in sorted(set(outputs))},
    }
    (final / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


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
    r = git("show", f":{path.resolve().relative_to(PROJECTS).as_posix()}", check=False, repo="projects")
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
            if name.lower().startswith("on"):
                errors.append(f"{r}: <{tag} {name}> 禁止事件属性（等同脚本）")
            elif EXTERNAL_CSS_RE.search(val):
                errors.append(f"{r}: <{tag} {name}> 禁止引用外部资源")
            elif name == "href" and not val.startswith("#"):
                errors.append(f"{r}: 禁止外部引用 {val}")
            elif name in COLOR_ATTRS:
                check_color_value(val, palette, f"{r} <{tag} {name}>", errors)
            elif name == "style":
                for prop, v in STYLE_COLOR_RE.findall(val):
                    check_color_value(v, palette, f"{r} <{tag} style {prop}>", errors)
        if tag == "style" and el.text:
            if EXTERNAL_CSS_RE.search(el.text):
                errors.append(f"{r}: <style> 禁止 @import 或引用外部资源")
            for prop, v in STYLE_COLOR_RE.findall(el.text):
                check_color_value(v, palette, f"{r} <style {prop}>", errors)


def layout_palette(p: Project, f: Path, palette: set[str]) -> set[str]:
    """版式允许的颜色：色板；手工配色文件 name.mode.svg 另可使用该模式的映射色与黑白（单色/反白版本来就是黑白）。"""
    m = re.fullmatch(r"(.+)\.([a-z]+)", f.stem)
    if not m or m.group(2) == "color":
        return palette
    try:
        return palette | set(p.mode_map(m.group(2)).values()) | {"#000000", "#FFFFFF"}
    except LogoError:
        return palette  # 未知配色模式，由覆盖文件检查报错


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
            check_svg(f, layout_palette(p, f, palette), errors, warnings, staged)
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
            enabled = cfg.get("export", {}).get("profiles", [])
            prof = load_profiles()
            if "social" in enabled:
                if (w := avatar_safe_warning(ldir, prof["social"]["avatar"])):
                    warnings.append(w)
            warnings.extend(release_structure_warnings(p, ldir, prof))
            ap = prof.get("android", {})
            if "android" in enabled and (ldir / f"{ap.get('adaptive_foreground_layout')}.svg").exists():
                with tempfile.TemporaryDirectory() as td:
                    fg = render_android_foreground(p, ldir, ap, "color", Path(td) / "fg.png")
                    if (w := android_safe_warning(fg, ap)):
                        warnings.append(f"{rel(ldir)}: {w}")
        except LogoError as e:
            warnings.append(f"{rel(ldir)}: 无法完成版式检查：{e}")

    if sel_ver and "selected_version" in valid and (cur_rel not in release_versions
                                                    or release_versions[cur_rel] != sel_ver):
        svg = p.version_dir(sel_ver) / "logo.svg"
        try:
            w = structure_warning(f"{rel(svg)}（选定版本 {sel_ver}）", lambda m: recolor_svg(svg, p.mode_map(m)),
                                  modes, f"可{STRUCTURE_PALETTE_HINT}")
            if w:
                warnings.append(w)
        except LogoError as e:
            warnings.append(f"{rel(svg)}: 无法完成单色结构检查：{e}")

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


def check_fonts(errors: list[str], warnings: list[str], projects: list[Project] | None) -> None:
    """projects 为 None 表示项目仓库不在场（单独克隆系统仓库），跳过 used_by 反查。"""
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
                warnings.append(f"{where}: 字体文件未下载，运行 ./logo fonts fetch {fid}")
            elif f.get("sha256") and sha256_file(fp) != f["sha256"]:
                errors.append(f"{where}: sha256 与文件不符")
        if f.get("license_file") and not (FONTS_DIR / f["license_file"]).exists():
            errors.append(f"{where}: 授权文件不存在")
    # used_by 反查必须与各项目的 [fonts] used 双向一致
    if projects is None:
        return
    by_font = {f.get("id"): set(f.get("used_by", [])) for f in load_fonts()}
    slugs = {p.slug for p in projects}
    for p in projects:
        for fid in p.cfg.get("fonts", {}).get("used", []):
            if fid in by_font and p.slug not in by_font[fid]:
                errors.append(f"fonts.toml [{fid}]: used_by 缺少 {p.slug}（该项目 [fonts] used 中声明了此字体）")
    for fid, users in by_font.items():
        for slug in sorted(users):
            if slug not in slugs:
                # 系统仓库可能搭配不同的项目仓库使用，只提醒不拦截
                warnings.append(f"fonts.toml [{fid}]: used_by 中的项目 {slug} 不在当前项目仓库中")
            elif fid not in Project(slug).cfg.get("fonts", {}).get("used", []):
                errors.append(f"fonts.toml [{fid}]: used_by 含 {slug}，但该项目 [fonts] used 未声明此字体")


def profile_warnings(prof: dict, enabled: list[str] | None = None) -> list[str]:
    out = []
    limit = dt.date.today() - dt.timedelta(days=365)

    def chk(name: str, item: dict):
        if item.get("generic"):
            return  # 通用比例，不对应具体平台
        basis = item.get("basis", "official")
        if basis not in ("official", "common", "generic"):
            out.append(f"导出规格 {name} 的 basis 只能是 official、common 或 generic")
        if basis != "official":
            # 无官方规格时按通用规格：须写明依据，按年复核
            if not item.get("verify_note"):
                out.append(f"导出规格 {name} 采用{'通用' if basis == 'common' else '泛用'}规格，须在 verify_note 中写明依据")
            v = item.get("verified_on", "")
            if not v:
                out.append(f"导出规格 {name} 缺少复核日期 verified_on")
            elif dt.date.fromisoformat(v) < limit:
                out.append(f"导出规格 {name} 的通用规格超过 12 个月未复核，建议检查平台是否已公布官方规格")
            return
        v = item.get("verified_on", "")
        if not v:
            note = item.get("verify_note")
            out.append(f"导出规格 {name} 尚未按官方文档核实" + (f"（{note}）" if note else ""))
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


def git(*args: str, check: bool = True, repo: str = "system") -> subprocess.CompletedProcess:
    """在系统仓库（ROOT）或项目仓库（projects/）中执行 git。"""
    if repo == "projects" and not projects_repo_ready():
        raise LogoError("项目仓库 projects/ 尚未初始化，请先运行 ./logo setup")
    path = PROJECTS if repo == "projects" else ROOT
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, check=check)


def projects_repo_ready() -> bool:
    # 必须检查 projects/.git 本身：否则 git -C projects 会向上找到系统仓库
    return (PROJECTS / ".git").exists()


def check_immutability(staged: bool, errors: list[str]) -> None:
    """项目仓库中冻结文件与只追加文件的规则（与 HEAD 比较）。"""
    if git("rev-parse", "--verify", "HEAD", check=False, repo="projects").returncode != 0:
        return  # 尚无提交
    diff_args = ["diff", "--name-status", "--no-renames", "-z"] + (["--cached"] if staged else []) + ["HEAD"]
    raw = git(*diff_args, repo="projects").stdout.decode("utf-8")
    parts = [x for x in raw.split("\0") if x]
    changes = list(zip(parts[0::2], parts[1::2]))
    for status, path in changes:
        if status == "A":
            continue
        if any(pt.match(path) for pt in FROZEN_PATTERNS):
            action = "删除" if status == "D" else "修改"
            errors.append(f"projects/{path}: 已冻结的文件被{action}（请新建版本/发布，而不是改动已提交的内容）")
        elif any(pt.match(path) for pt in APPEND_ONLY_PATTERNS):
            if status == "D":
                errors.append(f"projects/{path}: 只追加文件不能删除")
                continue
            old = git("show", f"HEAD:{path}", repo="projects").stdout
            new = git("show", f":{path}", repo="projects").stdout if staged else (PROJECTS / path).read_bytes()
            if not new.startswith(old.rstrip(b"\n")):
                errors.append(f"projects/{path}: 只允许在末尾追加内容，已有内容不能修改")


@functools.lru_cache(maxsize=None)
def template_placeholders(template_name: str) -> tuple[str, ...]:
    """模板中必须被替换的占位行：含（提示）或以冒号结尾的空字段；“追加区”下的行与 {{变量}} 行除外。"""
    out, in_append = [], False
    for line in (TEMPLATES / template_name).read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("#"):
            in_append = "追加" in s
            continue
        if not s or in_append or "{{" in s:
            continue
        if "（" in s or re.search(r"[:：]$", s):
            out.append(s)
    return tuple(out)


def unfilled_placeholders(path: str, text: str) -> list[str]:
    """返回文档中仍原样保留的模板占位行；path 为相对项目仓库的路径。"""
    for pat, tpl in DOC_TEMPLATES:
        if pat.match(path):
            lines = {ln.strip() for ln in text.splitlines()}
            return [ph for ph in template_placeholders(tpl) if ph in lines]
    return []


def check_new_docs(staged: bool, errors: list[str], warnings: list[str]) -> None:
    """新增的项目文档必须填完模板占位并引用有效记录；已提交的只追加文档无法再改，只检查新增的。

    staged=True（提交时）违反即报错；否则提醒。brief.md 可随时修改，始终只提醒。
    """
    added = {x for x in git("diff", "--cached", "--name-only", "--diff-filter=A", "-z", repo="projects")
             .stdout.decode("utf-8").split("\0") if x}
    if not staged:
        added |= {x for x in git("ls-files", "-z", "--others", "--exclude-standard", repo="projects")
                  .stdout.decode("utf-8").split("\0") if x}
    added = {x for x in added if any(pat.match(x) for pat, _ in DOC_TEMPLATES)}  # 只看文档，跳过图片等
    briefs = {f"{p.slug}/brief.md" for p in all_projects()}
    for path in sorted(added | briefs):
        f = PROJECTS / path
        if staged and path in added:
            text = git("show", f":{path}", repo="projects").stdout.decode("utf-8")
        elif f.exists():
            text = f.read_text(encoding="utf-8")
        else:
            continue
        sink = errors if (staged and path not in briefs) else warnings
        holes = unfilled_placeholders(path, text)
        if holes:
            sink.append(f"projects/{path}: 还有 {len(holes)} 处模板占位未填写（如“{holes[0]}”）")
        if path in briefs or path not in added:
            continue
        slug = path.split("/", 1)[0]
        if re.match(r"^[^/]+/releases/r\d+/release\.md$", path):
            refs = DECISION_REF_RE.findall(text)
            if not refs:
                sink.append(f"projects/{path}: “决策依据”须引用决策记录 decisions/NNN-*.md（用 ./logo decision 创建）")
            for ref in refs:
                if not (PROJECTS / slug / "decisions" / ref).exists():
                    sink.append(f"projects/{path}: 引用的决策记录不存在 decisions/{ref}")
        if re.match(r"^[^/]+/directions/[^/]+/v\d{3}/notes\.md$", path):
            for ref in CONCEPT_REF_RE.findall(text):
                if not (PROJECTS / slug / "concepts" / ref).exists():
                    sink.append(f"projects/{path}: 灵感来源引用的概念图不存在 concepts/{ref}")


def check_hooks(warnings: list[str]) -> None:
    """钩子缺失时 git 会静默跳过检查，这里显式提醒。"""
    expected = {"system": (".githooks", ROOT / ".githooks" / "pre-commit"),
                "projects": ("../.githooks/projects", ROOT / ".githooks" / "projects" / "pre-commit")}
    gi = PROJECTS / ".gitignore"
    if projects_repo_ready() and (not gi.exists() or gi.read_text(encoding="utf-8")
                                  != PROJECTS_GITIGNORE_TEMPLATE.read_text(encoding="utf-8")):
        warnings.append("projects/.gitignore 与系统模板不一致，运行 ./logo setup 更新后在项目仓库提交")
    for repo, (hooks_path, script) in expected.items():
        if repo == "projects" and not projects_repo_ready():
            continue
        r = git("config", "core.hooksPath", check=False, repo=repo)
        if r.stdout.decode().strip() != hooks_path or not script.exists():
            warnings.append(f"{repo} 仓库的 pre-commit 钩子未启用或缺失，提交不会被检查；请运行 ./logo setup")


def path_allowed(path: str, scope: str) -> bool:
    """path 是否属于 scope（system/projects）仓库规定的目录结构。"""
    if scope == "system":
        return any(pt.fullmatch(path) for pt in SYSTEM_FILE_PATTERNS)
    parts = path.split("/")
    if len(parts) == 1:
        return path in PROJECTS_ROOT_FILES
    if SLUG_RE.match(parts[0]):
        return any(pt.fullmatch("/".join(parts[1:])) for pt in PROJECT_FILE_PATTERNS)
    return False


def check_secrets(scope: str, staged: bool, errors: list[str]) -> None:
    """拦截疑似密钥/凭据：文件名特征 + 文本内容特征。

    staged=True 时只扫描本次提交新增或修改的文件（读暂存区内容）；否则扫描所有已跟踪与未忽略的文件。
    """
    if git("rev-parse", "--git-dir", check=False, repo=scope).returncode != 0:
        return
    prefix = "projects/" if scope == "projects" else ""
    root = PROJECTS if scope == "projects" else ROOT

    def ls(*args: str) -> list[str]:
        return [x for x in git(*args, repo=scope).stdout.decode("utf-8").split("\0") if x]

    if staged:
        paths = ls("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    else:
        paths = ls("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    for path in sorted(set(paths)):
        name = path.rsplit("/", 1)[-1]
        if SECRET_NAME_RE.match(name) or (scope == "system" and SECRET_KEYWORD_RE.search(name)):
            errors.append(f"{prefix}{path}: 文件名疑似密钥/凭据，禁止提交")
            continue
        if BINARY_SUFFIX_RE.search(path):
            continue  # 图片、字体等二进制文件不做文本扫描
        if staged:
            data = git("show", f":{path}", check=False, repo=scope).stdout
        else:
            f = root / path
            if not f.is_file() or f.stat().st_size > 5 << 20:
                continue
            data = f.read_bytes()
        if b"\0" in data[:8192]:
            continue  # 二进制文件（图片等）不做文本扫描
        text = data.decode("utf-8", errors="ignore")
        m = SECRET_CONTENT_RE.search(text)
        if m:  # 不回显匹配内容，避免把密钥片段打印到终端或日志
            errors.append(f"{prefix}{path}: 第 {text.count(chr(10), 0, m.start()) + 1} 行疑似包含密钥，禁止提交")


def check_stray_files(scope: str, errors: list[str], warnings: list[str]) -> None:
    """拦截目录结构之外的文件（描摹中间件、临时脚本等）。已纳入/暂存的文件报错，未跟踪的文件提醒。"""
    if git("rev-parse", "--git-dir", check=False, repo=scope).returncode != 0:
        return
    prefix = "projects/" if scope == "projects" else ""

    def ls(*args: str) -> set[str]:
        return {x for x in git("ls-files", "-z", *args, repo=scope).stdout.decode("utf-8").split("\0") if x}

    tracked = ls("--cached")
    untracked = ls("--others", "--exclude-standard")
    for path in sorted(tracked):
        if scope == "system" and FONT_BINARY_RE.search(path):
            errors.append(f"{path}: 字体文件不应提交到 Git（由 ./logo fonts fetch 下载）")
    for path in sorted(tracked | untracked):
        if not path_allowed(path, scope):
            msg = f"{prefix}{path}: 不在规定的目录结构中（临时文件请放 .cache/，或删除）"
            (errors if path in tracked else warnings).append(msg)


def build_index() -> str:
    lines = ["# 项目看板", "", "<!-- 由 ./logo index 自动生成，请勿手改 -->", "",
             "| 项目 | 名称 | 阶段 | 方向数 | 选定方向 | 选定版本 | 当前发布 | 更新 |",
             "|---|---|---|---|---|---|---|---|"]
    for p in all_projects():
        c = p.cfg
        st = c.get("state", {})
        lines.append(
            f"| [{p.slug}]({p.slug}/brief.md) | {c.get('project', {}).get('name', '')} | "
            f"{st.get('stage', '')} | {len(p.directions())} | {st.get('selected_direction') or '—'} | "
            f"{st.get('selected_version') or '—'} | {st.get('current_release') or '—'} | {st.get('updated', '')} |")
    if len(lines) == 6:
        lines.append("| — | 暂无项目 | | | | | | |")
    return "\n".join(lines) + "\n"


def cmd_index(a) -> None:
    PROJECTS_MD.write_text(build_index(), encoding="utf-8")
    print(f"已更新：{rel(PROJECTS_MD)}")


def cmd_check(a) -> None:
    """scope=system/projects 供两个仓库的 pre-commit 钩子使用；默认 all 检查全部。"""
    errors: list[str] = []
    warnings: list[str] = []
    scope = a.scope
    if a.staged and scope == "all":
        raise LogoError("--staged 需要配合 --scope system 或 --scope projects（由各仓库的钩子传入）")
    has_projects = projects_repo_ready()
    projects: list[Project] | None = None
    if not has_projects:
        if scope == "projects" or a.slug:
            raise LogoError("项目仓库 projects/ 尚未初始化，请先运行 ./logo setup")
        warnings.append("未找到项目仓库 projects/（运行 ./logo setup 初始化），已跳过项目检查")
    else:
        staged = a.staged and scope == "projects"
        perr: list[str] = []
        projects = [Project(a.slug)] if a.slug else all_projects()
        for p in projects:
            check_project(p, perr, warnings, staged)
        if not a.slug:
            if not PROJECTS_MD.exists() or PROJECTS_MD.read_text(encoding="utf-8") != build_index():
                msg = "projects/PROJECTS.md 不是最新，请运行 ./logo index"
                (perr if staged else warnings).append(msg)
            check_stray_files("projects", perr, warnings)
            check_new_docs(staged, perr, warnings)
            check_secrets("projects", staged, perr)
        if scope != "system":
            check_immutability(staged, perr)
            errors.extend(perr)
        else:
            # 系统提交不因项目仓库工作区里未完成的内容被拦截，但要让人看到
            warnings.extend(f"[项目仓库] {e}" for e in perr)
    if not a.slug:
        check_fonts(errors, warnings, projects)
        if scope != "projects":
            check_stray_files("system", errors, warnings)
            check_secrets("system", a.staged, errors)
        if not a.staged:
            check_hooks(warnings)
        try:
            warnings.extend(profile_warnings(load_profiles()))
        except LogoError as e:
            errors.append(str(e))
    for w in warnings:
        print(f"⚠ {w}")
    for e in errors:
        print(f"✗ {e}")
    if errors:
        print(f"\n检查未通过：{len(errors)} 个错误，{len(warnings)} 个提醒")
        sys.exit(1)
    print(f"检查通过（{len(warnings)} 个提醒）")


# ---------------------------------------------------------------------------
# 命令：selftest
# ---------------------------------------------------------------------------
def cmd_selftest(a) -> None:
    """运行 tests/ 下的自动化测试（每个测试在临时目录中搭建隔离工作区，不触碰真实仓库）。"""
    cmd = [sys.executable, "-m", "unittest", *(a.tests or ["discover", "-s", str(ROOT / "tests")])]
    if a.verbose:
        cmd.append("-v")
    sys.exit(subprocess.run(cmd, cwd=ROOT / "tests").returncode)


# ---------------------------------------------------------------------------
# 命令：setup
# ---------------------------------------------------------------------------
def cmd_setup(a) -> None:
    run(["git", "-C", str(ROOT), "config", "core.hooksPath", ".githooks"])
    print("已启用系统仓库钩子（.githooks/pre-commit）")
    PROJECTS.mkdir(exist_ok=True)
    if not projects_repo_ready():
        run(["git", "init", "-q", str(PROJECTS)])
        print("已初始化项目仓库 projects/")
    gi = PROJECTS / ".gitignore"
    want = PROJECTS_GITIGNORE_TEMPLATE.read_text(encoding="utf-8")
    if not gi.exists() or gi.read_text(encoding="utf-8") != want:
        gi.write_text(want, encoding="utf-8")
        print("已写入 projects/.gitignore（白名单模板），请在项目仓库提交")
    # 相对路径以项目仓库根目录为基准，钩子脚本随系统仓库一起版本管理
    run(["git", "-C", str(PROJECTS), "config", "core.hooksPath", "../.githooks/projects"])
    print("已启用项目仓库钩子（.githooks/projects/pre-commit）")
    if not PROJECTS_MD.exists():
        PROJECTS_MD.write_text(build_index(), encoding="utf-8")
    venv = ROOT / ".venv"
    if not venv.exists():
        run([sys.executable, "-m", "venv", str(venv)])
        print("已创建 .venv")
    print("正在安装 Python 依赖……")
    run([str(venv / "bin" / "pip"), "install", "-q", "-r", str(ROOT / "tools" / "requirements.txt")])
    for tool in ("resvg", "magick"):
        if not shutil.which(tool):
            print(f"⚠ 未找到 {tool}，请用 Homebrew 安装")
    missing = [f["id"] for f in load_fonts() if font_status(f) != "已就绪"]
    if missing:
        print(f"⚠ 以下字体尚未下载：{', '.join(missing)}；需要时运行 ./logo fonts fetch [id…]")
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
    s.add_argument("--inspired-by", help="灵感来源的概念图编号，如 001 或 001-mountain（须先用 ./logo concept 登记）")
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
    s.add_argument("--no-kern", action="store_true", help="不应用字体自带的字距调整（kerning）")
    s.add_argument("--svg", help="输出为独立 SVG 文件；不填则输出 <path> 片段")
    s.set_defaults(fn=cmd_outline)

    s = sub.add_parser("fonts", help="查看已登记字体，或从官方地址下载并校验（字体文件不进 Git）")
    s.add_argument("action", nargs="?", choices=["list", "fetch"], default="list")
    s.add_argument("ids", nargs="*", help="字体 id（默认全部）")
    s.set_defaults(fn=cmd_fonts)

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
    s.add_argument("--scope", choices=["all", "system", "projects"], default="all",
                   help="检查范围：all（默认）、system（系统仓库提交）、projects（项目仓库提交）")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("selftest", help="运行系统自动化测试（修改系统后、提交前运行）")
    s.add_argument("tests", nargs="*", help="只运行指定测试，如 test_unit 或 test_guards.DocsTest")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_selftest)

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
