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
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib
import urllib.parse
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

# ./logo 以脚本方式运行本文件（__main__）；登记为 logo，使 covers.py 的 import logo 拿到同一份模块
sys.modules.setdefault("logo", sys.modules[__name__])

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ROOT / "projects"  # 独立的项目 Git 仓库（系统仓库中被忽略）
TEMPLATES = ROOT / "templates"
CONFIG = ROOT / "config"
TARGETS_TOML = CONFIG / "logo-targets.toml"
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
LICENSE_REVIEW_NAME = "LICENSE-REVIEW.md"  # 非开源字体的授权复核记录，放在字体目录内
LICENSE_REVIEW_REQUIRED = ("允许用于 Logo / 商标", "允许转曲（文字转路径）")
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
    re.compile(r"^[^/]+/covers/[^/]+/assets/\d{3}-[^/]+\.(png|jpe?g|webp)$"),  # 封面素材登记后不可替换
    re.compile(r"^_series/[^/]+/covers/[^/]+/assets/\d{3}-[^/]+\.(png|jpe?g|webp)$"),  # 家族封面素材同样
]

APPEND_ONLY_PATTERNS = [
    re.compile(r"^[^/]+/directions/[^/]+/v\d{3}/notes\.md$"),
    re.compile(r"^[^/]+/directions/[^/]+/direction\.md$"),
    re.compile(r"^[^/]+/releases/r\d+/(release|guide)\.md$"),
    re.compile(r"^[^/]+/decisions/[^/]+\.md$"),
    re.compile(r"^[^/]+/concepts/[^/]+\.md$"),
    re.compile(r"^[^/]+/covers/[^/]+/cover\.md$"),
    re.compile(r"^[^/]+/covers/[^/]+/assets/[^/]+\.md$"),
    re.compile(r"^_series/[^/]+/series\.md$"),
    re.compile(r"^_series/[^/]+/covers/[^/]+/cover\.md$"),
    re.compile(r"^_series/[^/]+/covers/[^/]+/assets/[^/]+\.md$"),
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
# 项目仓库根目录允许的文件；项目目录内的文件见 PROJECT_FILE_PATTERNS，系列记录见 SERIES_FILE_RE
PROJECTS_ROOT_FILES = {"PROJECTS.md", ".gitignore"}
SERIES_FILE_RE = re.compile(r"_series/([a-z0-9]+(?:-[a-z0-9]+)*)/(.+)")  # 系列目录内：series.toml/md 与家族封面

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
    r"covers/\.gitkeep",
    r"covers/\d{3}-[a-z0-9-]+/cover\.(toml|md)",
    r"covers/\d{3}-[a-z0-9-]+/[0-9.]+x[0-9.]+\.svg",
    r"covers/\d{3}-[a-z0-9-]+/assets/\d{3}-[a-z0-9-]+\.(md|png|jpe?g|webp)",
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
    (re.compile(r"^[^/]+/covers/[^/]+/cover\.md$"), "cover.md"),
    (re.compile(r"^[^/]+/covers/[^/]+/assets/\d{3}-[^/]+\.md$"), "cover-asset.md"),
    # 系列规则按架构使用不同模板，同一路径匹配多个模板时合并检测
    (re.compile(r"^_series/[^/]+/series\.md$"), "series-unified.md"),
    (re.compile(r"^_series/[^/]+/series\.md$"), "series-endorsed.md"),
    (re.compile(r"^_series/[^/]+/series\.md$"), "series-independent.md"),
    (re.compile(r"^_series/[^/]+/covers/[^/]+/cover\.md$"), "cover.md"),  # 家族封面
    (re.compile(r"^_series/[^/]+/covers/[^/]+/assets/\d{3}-[^/]+\.md$"), "cover-asset.md"),
]
OUTLINE_FONT_RE = re.compile(r"<!--\s*outline:\s*font=([A-Za-z0-9_-]+)")  # ./logo outline 写入的来源注释
DECISION_REF_RE = re.compile(r"decisions/(\d{3}-[a-z0-9-]+\.md)")
CONCEPT_REF_RE = re.compile(r"concepts/(\d{3}-[a-z0-9-]+\.md)")

# 单色/反白版结构检查：只看图形内部（离轮廓 2px 以上）的颜色分界线——外轮廓任何配色都会保留，不能算数。
# 非彩色版保留的内部分界线不足彩色版的 STRUCTURE_MIN_RATIO 时提醒。标定（256px 渲染）：
# cat-dog 各版本单色化后 0%；正确修复（次色留白）100%；单色 Logo、acme 等无内部结构的跳过。
STRUCTURE_MIN_RATIO = 0.6
STRUCTURE_MIN_INTERIOR = 150  # 彩色版内部分界线少于此像素数视为没有内部结构（如同色图形重叠的抗锯齿接缝）
STRUCTURE_RENDER_SIZE = 256
STRUCTURE_CACHE = CACHE / "structure-cache.json"
HISTORY_CACHE = CACHE / "history-check.json"  # 项目仓库历史扫描结果（按 HEAD 缓存）
MODE_NAMES = {"color": "彩色", "mono": "单色", "reversed": "反白"}
STRUCTURE_PALETTE_HINT = "在 [color_modes.mono] 中把部分颜色映射为 #FFFFFF、在 [color_modes.reversed] 中映射为 #000000，作为留白保留细节"

RELEASE_LAYOUT_CHECKLIST = "\n".join((
    "- [ ] `primary.svg`（必需：主版）",
    "- [ ] `icon.svg`（可选：纯图形版，用于小尺寸与图标类目标；缺省时用 primary）",
    "- [ ] `horizontal.svg` / `wordmark-cn.svg` / `wordmark-en.svg`（可选：带文字的组合版）",
    "- [ ] `app-icon.svg` / `avatar.svg`（可选：自动排版不满意时的手工覆盖）",
))

# Logo 资产：标志版式（svg 目标与单色结构检查的对象）与按需生成的目标
LOGO_LAYOUTS = ("primary", "icon", "horizontal", "wordmark-cn", "wordmark-en")
MANUAL_LAYOUTS = ("app-icon", "avatar")  # 自动排版不满意时的手工覆盖
LAYOUT_NAMES_HINT = "可用：" + "、".join(LOGO_LAYOUTS + MANUAL_LAYOUTS) + " 及 <标志版式>.<配色>.svg"
TARGET_NAMES = ("svg", "favicon", "ios", "android", "avatar")
LEGACY_PROFILE_TARGETS = {"general": "svg", "favicon": "favicon", "ios": "ios", "android": "android",
                          "social": "avatar"}  # 旧写法 [export] profiles 的换算

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

    def add_font(self, font_id: str) -> bool:
        """把字体写入 project.toml 的 [fonts] used（保留其余内容）；已存在时返回 False。"""
        used = list(self.cfg.get("fonts", {}).get("used", []))
        if font_id in used:
            return False
        line = "used = [" + ", ".join(f'"{x}"' for x in used + [font_id]) + "]"
        text = self.toml_path.read_text(encoding="utf-8")
        m = re.search(r"^\[fonts\]\s*$", text, re.M)
        if not m:
            text = text.rstrip("\n") + f"\n\n[fonts]\n{line}\n"
        else:
            nxt = re.search(r"^\[", text[m.end():], re.M)
            end = m.end() + nxt.start() if nxt else len(text)
            section = text[m.end():end]
            used_re = re.compile(r"^used\s*=\s*\[[^\]]*\]", re.M)  # 数组可能跨多行
            if used_re.search(section):
                section = used_re.sub(line, section, count=1)
            else:
                section = "\n" + line + section
            text = text[:m.end()] + section + text[end:]
        self.toml_path.write_text(text, encoding="utf-8")
        return True

    def svg_fonts(self, staged: bool = False) -> dict[str, list[str]]:
        """项目各版本与发布版式中经 outline 转曲的字体 → 出现的文件（依据 outline 注释）。"""
        files = [v / "logo.svg" for d in self.directions() for v in self.versions_in(d)]
        files += [f for rd in self.releases() for f in sorted((rd / "layouts").glob("*.svg"))]
        files += sorted((self.dir / "covers").glob("*/*.svg"))  # 封面中的标题等文字同样计入
        found: dict[str, list[str]] = {}
        for f in files:
            if not f.exists():
                continue
            data = staged_bytes(f) if staged else None
            text = (data if data is not None else f.read_bytes()).decode("utf-8", errors="ignore")
            for fid in sorted(set(OUTLINE_FONT_RE.findall(text))):
                found.setdefault(fid, []).append(rel(f))
        return found

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
def recolor_svg(src: Path, mapping: dict[str, str]) -> str:
    text = src.read_text(encoding="utf-8")
    return HEX_IN_TEXT_RE.sub(lambda m: mapping.get(m.group(0).upper(), m.group(0)), text)


def layout_mode_text(p: Project, ldir: Path, name: str, mode: str) -> str:
    """某版式在某配色下的 SVG 文本：优先使用手工覆盖文件 name.mode.svg，否则按色板自动换色。"""
    override = ldir / f"{name}.{mode}.svg"
    if mode != "color" and override.exists():
        return override.read_text(encoding="utf-8")
    return recolor_svg(ldir / f"{name}.svg", p.mode_map(mode))


def load_targets() -> dict:
    return load_toml(TARGETS_TOML)


def project_targets(p: Project) -> list[str]:
    """项目声明的生成目标：[export] targets；旧写法 profiles 自动换算（general→svg，social→avatar）。"""
    ex = p.cfg.get("export", {})
    if "targets" in ex:
        names = list(ex["targets"])
    else:
        names = [LEGACY_PROFILE_TARGETS[x] for x in ex.get("profiles", []) if x in LEGACY_PROFILE_TARGETS]
    unknown = [t for t in names if t not in TARGET_NAMES]
    if unknown:
        raise LogoError(f"[export] targets 含未知目标：{', '.join(unknown)}（可选：{', '.join(TARGET_NAMES)}）")
    return [t for t in TARGET_NAMES if t in names]


def release_logo_layouts(ldir: Path) -> list[str]:
    """发布中存在的标志版式（svg 目标与单色结构检查的对象），按固定顺序。"""
    return [n for n in LOGO_LAYOUTS if (ldir / f"{n}.svg").exists()]


def is_layout_file(name: str) -> bool:
    """文件名是否为系统认识的发布版式：标志版式、app-icon / avatar 手工覆盖、标志版式的配色覆盖 name.mode.svg。
    其他文件（如旧发布里的 cover-*.svg）生成时会被忽略。"""
    if not name.endswith(".svg"):
        return False
    stem = name[:-4]
    if stem in LOGO_LAYOUTS or stem in MANUAL_LAYOUTS:
        return True
    m = re.fullmatch(r"(.+)\.([a-z]+)", stem)
    return bool(m and m.group(1) in LOGO_LAYOUTS)


def mark_layout(ldir: Path) -> str:
    """图标类目标所用的图形：icon.svg，缺省时用主版 primary.svg。"""
    return "icon" if (ldir / "icon.svg").exists() else "primary"


def compose_svg(src_text: str, content: float, background: str | None, canvas: int = 1000) -> str:
    """把源 SVG 装进方形外框：可选铺满底色，图形等比缩放进居中、边长为 content 比例的方框。"""
    root = ET.fromstring(src_text)
    if not root.get("viewBox"):
        raise LogoError("自动排版需要源 SVG 带 viewBox")
    inner = canvas * content
    off = (canvas - inner) / 2
    for k, v in (("x", off), ("y", off), ("width", inner), ("height", inner)):
        root.set(k, fmt_num(v))
    root.set("preserveAspectRatio", "xMidYMid meet")
    outer = ET.Element(f"{{{SVG_NS}}}svg", {"viewBox": f"0 0 {canvas} {canvas}"})
    if background:
        ET.SubElement(outer, f"{{{SVG_NS}}}rect", {"width": str(canvas), "height": str(canvas), "fill": background})
    outer.append(root)
    return ET.tostring(outer, encoding="unicode")


def solid_svg(color: str, canvas: int = 100) -> str:
    return (f'<svg xmlns="{SVG_NS}" viewBox="0 0 {canvas} {canvas}">'
            f'<rect width="{canvas}" height="{canvas}" fill="{color}"/></svg>')


def icon_mode(p: Project) -> str:
    """自动排版图标里图形的配色（[export] icon_mode，兼容旧名 android_foreground_mode）。"""
    ex = p.cfg.get("export", {})
    return ex.get("icon_mode") or ex.get("android_foreground_mode") or "color"


def app_icon_text(p: Project, ldir: Path) -> str:
    """App 图标（iOS / Google Play / Android 旧版 / Apple 触控）：手工覆盖 app-icon.svg，否则自动排版。"""
    if (ldir / "app-icon.svg").exists():
        return (ldir / "app-icon.svg").read_text(encoding="utf-8")
    comp = load_targets()["compose"]
    return compose_svg(layout_mode_text(p, ldir, mark_layout(ldir), icon_mode(p)), comp["app_icon_content"],
                       app_background(p))


def avatar_text(p: Project, ldir: Path) -> str:
    """头像：手工覆盖 avatar.svg，否则品牌底色铺满、图形缩进圆形安全区。"""
    if (ldir / "avatar.svg").exists():
        return (ldir / "avatar.svg").read_text(encoding="utf-8")
    comp = load_targets()["compose"]
    return compose_svg(layout_mode_text(p, ldir, mark_layout(ldir), icon_mode(p)), comp["avatar_content"],
                       app_background(p))


def android_foreground_text(p: Project, ldir: Path, mode: str) -> str:
    """Android 自适应图标前景层 / 单色层：透明画布，图形缩放到中心 adaptive_safe_ratio 区域。"""
    ap = load_targets()["android"]
    return compose_svg(layout_mode_text(p, ldir, mark_layout(ldir), mode), ap["adaptive_safe_ratio"], None)


def render_text(svg_text: str, out: Path, size: int | None = None, background: str | None = None,
                resources: Path | None = None, width: int | None = None, height: int | None = None) -> Path:
    """把 SVG 文本渲染为 PNG（只用 resvg，输出可重复、不含时间等元数据）：
    给 size 时适配进 size×size，否则按 width / height。"""
    with tempfile.TemporaryDirectory() as td:
        svg = Path(td) / "s.svg"
        svg.write_text(svg_text, encoding="utf-8")
        if size:
            return render(svg, out, size, size, background, resources)
        return render(svg, out, width, height, background, resources)


def write_ico(pngs: list[Path], out: Path) -> Path:
    """把多张 PNG 打包为 .ico（ICO 允许直接内嵌 PNG），不依赖外部工具。"""
    data = [f.read_bytes() for f in pngs]
    entries, offset = b"", 6 + 16 * len(data)
    for d in data:
        w, h = struct.unpack(">II", d[16:24])
        entries += struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, 1, 32, len(d), offset)
        offset += len(d)
    out.write_bytes(struct.pack("<HHH", 0, 1, len(data)) + entries + b"".join(data))
    return out


def avatar_safe_warning(ldir: Path, av: dict) -> str | None:
    """检查手工头像 avatar.svg 的图形是否超出圆形安全区（自动排版的头像天生合规）。"""
    layout = ldir / "avatar.svg"
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


def target_warnings(p: Project, ldir: Path) -> list[str]:
    """按项目声明的目标检查发布：品牌底色配置、手工覆盖文件、Android 安全区、单色结构。"""
    targets = project_targets(p)
    out = []
    if set(targets) & {"favicon", "ios", "android", "avatar"}:
        try:
            app_background(p)
        except LogoError as e:
            out.append(str(e))
            return out
    if set(targets) & {"ios", "android", "favicon"} and (ldir / "app-icon.svg").exists():
        if (w := app_icon_transparency_warning(ldir / "app-icon.svg")):
            out.append(w)
    if "avatar" in targets and (w := avatar_safe_warning(ldir, load_targets()["avatar"])):
        out.append(w)
    auto = [(n, f) for n, f, ts in (("app-icon", app_icon_text, {"ios", "android", "favicon"}),
                                    ("avatar", avatar_text, {"avatar"}))
            if set(targets) & ts and not (ldir / f"{n}.svg").exists()]
    mode = icon_mode(p)
    users = [n for n, _ in auto] + (["Android 前景层"] if "android" in targets else [])
    if users and mode != "color":  # 自动排版用非彩色图形时，图形在该配色下是否糊成一块
        mark = mark_layout(ldir)
        if (w := structure_warning(f"{rel(ldir)}: 自动排版的 {'、'.join(users)}（icon_mode={mode}）",
                                   lambda m: layout_mode_text(p, ldir, mark, m), ["color", mode],
                                   f"可改 [export] icon_mode = \"color\"，或提供 {mark}.{mode}.svg / app-icon.svg 手工覆盖")):
            out.append(w)
    for name, make in auto:
        if interior_edge_pixels(make(p, ldir)) < STRUCTURE_MIN_INTERIOR:
            out.append(f"{rel(ldir)}: 自动排版的 {name} 中图形与底色几乎无法区分（icon_mode={icon_mode(p)}，"
                       f"app_background={app_background(p)}）；请换 [export] icon_mode 或 app_background，"
                       f"或提供 {name}.svg 手工覆盖")
    if "android" in targets:
        with tempfile.TemporaryDirectory() as td:
            fg = render_text(android_foreground_text(p, ldir, icon_mode(p)), Path(td) / "fg.png",
                             load_targets()["android"]["adaptive_size"])
            if (w := android_safe_warning(fg, load_targets()["android"])):
                out.append(f"{rel(ldir)}: {w}")
    out.extend(release_structure_warnings(p, ldir, targets))
    return out


def release_structure_warnings(p: Project, ldir: Path, targets: list[str]) -> list[str]:
    """检查发布中各标志版式（及 Android 单色层所用图形）的单色/反白结构；内容相同的版式只检查一次。"""
    modes = p.cfg.get("color_modes", {}).get("modes", ["color"])
    checks = {n: modes for n in release_logo_layouts(ldir)}
    if "android" in targets:
        fg = mark_layout(ldir)
        checks[fg] = sorted(set(checks.get(fg, [])) | {"color", "mono"})  # Android 13+ 主题图标使用单色层
    groups: dict[tuple, list[str]] = {}  # 内容与配色模式都相同的版式合并为一组，只检查一次
    for name, ms in checks.items():
        key = (tuple(ms), tuple(layout_mode_text(p, ldir, name, m) for m in ms))
        groups.setdefault(key, []).append(name)
    out = []
    for (ms, _), names in groups.items():
        first = names[0]
        label = rel(ldir / f"{first}.svg") + (f"（同内容：{'、'.join(names[1:])}）" if names[1:] else "")
        hint = f"可提供 {first}.mono.svg / {first}.reversed.svg 等手工配色文件，或{STRUCTURE_PALETTE_HINT}"
        if (w := structure_warning(label, lambda m, n=first: layout_mode_text(p, ldir, n, m), list(ms), hint)):
            out.append(w)
    return out


def app_background(p: Project) -> str:
    """App 图标类产物的品牌底色（[export] app_background，兼容旧名 android_background），须为色板中的色名。"""
    ex = p.cfg.get("export", {})
    key = ex.get("app_background") or ex.get("android_background")
    if not key:
        raise LogoError("[export] 缺少 app_background（填色板中的色名，用作 App 图标、头像与 Android 背景层的底色）")
    if key not in p.palette:
        raise LogoError(f"[export] app_background 必须是色板中的色名，当前：{key!r}")
    return p.palette[key]


def app_icon_transparency_warning(svg: Path) -> str | None:
    """手工 App 图标应自带铺满的不透明底色；有透明像素时返回提醒。"""
    with tempfile.TemporaryDirectory() as td:
        png = render_fit(svg, Path(td) / "a.png", 256)
        min_alpha = float(run(["magick", str(png), "-alpha", "extract", "-format", "%[fx:minima]", "info:"],
                              capture=True).strip() or 1)
    if min_alpha < 0.99:
        return (f"{rel(svg)} 含透明区域：iOS/Google Play 图标须不透明，导出时会用 app_background 铺底；"
                "建议在版式中自带铺满画布的底色，或删除它改用自动排版")
    return None


def android_safe_warning(png: Path, ap: dict) -> str | None:
    """前景图形超出官方 66dp 安全区（108dp 画布内的中心圆）时返回提醒。"""
    ratio = ap.get("adaptive_safe_zone", 66 / 108)
    if alpha_outside_circle(png, ratio) > 0.05:
        return f"Android 自适应图标前景超出 {round(ratio * 108)}dp 安全区，在部分启动器的遮罩下会被裁切"
    return None


def cached(key_text: str, compute):
    """按内容哈希缓存可 JSON 序列化的计算结果（渲染类检查用，内容不变时不重复渲染）。"""
    key = hashlib.sha256(key_text.encode("utf-8")).hexdigest()
    try:
        cache = json.loads(STRUCTURE_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if key not in cache:
        cache[key] = compute()
        STRUCTURE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        STRUCTURE_CACHE.write_text(json.dumps(cache), encoding="utf-8")
    return cache[key]


def interior_edge_pixels(svg_text: str) -> float:
    """渲染后统计图形内部的颜色分界像素数（排除外轮廓附近 2px），结果按内容哈希缓存。"""
    return cached(f"v2|{STRUCTURE_RENDER_SIZE}|{svg_text}", lambda: _interior_edge_pixels(svg_text))


def _interior_edge_pixels(svg_text: str) -> float:
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
           background: str | None = None, resources: Path | None = None) -> Path:
    """渲染 SVG 为 PNG；同时给出宽高时按比例适配到该框内。resources 为相对路径素材（如封面照片）的查找目录。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["resvg", str(svg), str(out)]
    if resources:
        cmd += ["--resources-dir", str(resources.resolve())]
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
    import series  # 延迟导入：series.py 依赖本模块
    if bool(a.series) != bool(a.role):
        raise LogoError("--series 与 --role 须同时给出（独立项目两者都不填）")
    master = series.check_join(a.series, a.role, slug) if a.series else None  # 先校验，避免留下半成品
    targets = [x.strip() for x in (a.targets or "svg,favicon").split(",") if x.strip()]
    unknown = [x for x in targets if x not in TARGET_NAMES]
    if unknown or not targets:
        raise LogoError(f"--targets 含未知目标：{', '.join(unknown) or '（空）'}（可选：{', '.join(TARGET_NAMES)}）")
    targets = [x for x in TARGET_NAMES if x in targets]
    kind, rules = series.describe(a.series, a.role)
    vals = {"name": a.name or slug, "slug": slug, "date": today()}
    toml = render_template("project.toml", **vals).replace(
        'targets = ["svg", "favicon"]', "targets = [" + ", ".join(f'"{x}"' for x in targets) + "]")
    if master:
        toml = series.inherit_from(toml, master)
    if a.series:
        rid = master.cfg["state"]["current_release"] if master else None
        toml = toml.rstrip("\n") + "\n\n" + series.series_section(a.series, a.role, rid)
    write_new(d / "project.toml", toml)
    write_new(d / "brief.md", render_template("brief.md", project_type=kind, series_rules=rules,
                                              targets="、".join(targets), **vals))
    for sub in ("concepts", "directions", "decisions", "releases"):
        (d / sub).mkdir(parents=True, exist_ok=True)
        (d / sub / ".gitkeep").touch()
    print(f"已创建项目：{rel(d)}（{kind}）")
    if master:
        print(f"色板与配色模式已继承母品牌 {master.slug}（{master.cfg['state']['current_release']}）")
    print(f"下一步：按需求访谈填完 {rel(d / 'brief.md')}"
          + ("" if master else " 与 project.toml 中的色板") + "，经用户确认后再 ./logo direction 开始设计")


def cmd_direction(a) -> None:
    p = Project(a.slug)
    d = create_direction(p, a.letter, a.name, a.title)
    print(f"已创建方向：{rel(d)}")


def create_direction(p: Project, letter: str, name: str, title: str | None = None) -> Path:
    """新建方向（含需求访谈门槛）；./logo direction 与 ./logo import 共用。"""
    letter = letter.upper()
    if not re.fullmatch(r"[A-Z]", letter):
        raise LogoError("方向编号必须是单个大写字母，例如 A")
    if any(d.name.split("-", 1)[0] == letter for d in p.directions()):
        raise LogoError(f"方向 {letter} 已存在")
    brief = p.dir / "brief.md"
    holes = unfilled_placeholders(f"{p.slug}/brief.md", brief.read_text(encoding="utf-8")) if brief.exists() else ["（缺少 brief.md）"]
    if holes:  # 先问清楚再设计：需求访谈的结果写进 brief.md 并经用户确认后才能开始
        raise LogoError(f"{rel(brief)} 还有 {len(holes)} 处未填写（如“{holes[0]}”）；"
                        "请先完成需求访谈（logo-workflow 技能第 0 步）并经用户确认，再开始设计")
    import series  # 延迟导入：series.py 依赖本模块
    if (holes := series.rules_holes(p)):  # 系列成员：系列层面的访谈（系列规则）也须先完成
        sid = series.membership(p)["id"]
        raise LogoError(f"系列规则 {series.rules_path(sid)} 还有 {len(holes)} 处未填写（如“{holes[0]}”）；"
                        "请先与用户确定系列规则，再开始设计系列成员")
    d = p.dir / "directions" / f"{letter}-{to_slug(name)}"
    write_new(d / "direction.md", render_template(
        "direction.md", letter=letter, name=name, title=title or name, date=today()))
    (p.dir / "directions" / ".gitkeep").unlink(missing_ok=True)
    if p.cfg["state"].get("stage") == "briefing":
        p.set_state(stage="exploring")
    return d


def cmd_version(a) -> None:
    p = Project(a.slug)
    ddir = p.direction(a.direction)
    if a.from_master and (a.based_on or a.blank):
        raise LogoError("--from-master 不能与 --from / --blank 同时使用")
    if a.from_master:
        import series  # 延迟导入：series.py 依赖本模块
        base_svg, base = series.master_start(p)
    else:
        base = a.based_on or (None if a.blank else p.latest_version_id(ddir))
        base_svg = p.version_dir(base) / "logo.svg" if base else None  # 先校验，避免留下半成品目录
    inspired = p.concept_ref(a.inspired_by) if a.inspired_by else "无"
    svg = (base_svg or TEMPLATES / "blank.svg").read_text(encoding="utf-8")
    vid, vdir = create_version(p, ddir, svg, base or "（无，全新起稿）", inspired)
    print(f"已创建版本：{vid} → {rel(vdir)}")
    print("提示：完成 logo.svg 与 notes.md 后再提交；提交后该版本即冻结")


def create_version(p: Project, ddir: Path, svg_text: str, based_on: str, inspired: str) -> tuple[str, Path]:
    """在方向下新建下一个版本（logo.svg 与 notes.md），推进阶段；./logo version 与 ./logo import 共用。"""
    letter = ddir.name.split("-", 1)[0]
    existing = p.versions_in(ddir)
    num = int(existing[-1].name[1:]) + 1 if existing else 1
    vid, vdir = f"{letter}-v{num:03d}", ddir / f"v{num:03d}"
    vdir.mkdir(parents=True)
    (vdir / "logo.svg").write_text(svg_text, encoding="utf-8")
    write_new(vdir / "notes.md", render_template(
        "version-notes.md", id=vid, based_on=based_on, inspired_by=inspired, direction=ddir.name, date=today()))
    stage = p.cfg["state"].get("stage")
    if stage in ("briefing", "exploring", "delivered") and p.cfg["state"].get("selected_direction"):
        p.set_state(stage="iterating")
    return vid, vdir


def cmd_concept(a) -> None:
    p = Project(a.slug)
    src = Path(a.image)
    if not src.exists():
        raise LogoError(f"图片不存在：{src}")
    if src.suffix.lower() not in CONCEPT_SUFFIXES:
        raise LogoError(f"概念图只支持 {', '.join(sorted(CONCEPT_SUFFIXES))}，收到：{src.suffix}")
    stem, dst = register_concept(p, src, a.name, a.prompt, a.tool)
    print(f"已登记概念图：{rel(dst)}（提示词记录于 {stem}.md）")


def register_concept(p: Project, src: Path, name: str, prompt: str, tool: str, extra: str = "") -> tuple[str, Path]:
    """把图片登记为下一个编号的概念图（图片与同名 .md）；extra 追加在记录末尾。返回 (编号-短名, 图片路径)。"""
    cdir = p.dir / "concepts"
    nums = [int(m.group(1)) for f in cdir.glob("*.md") if (m := re.match(r"(\d{3})-", f.name))]
    num = max(nums, default=0) + 1
    stem = f"{num:03d}-{to_slug(name)}"
    dst = cdir / f"{stem}{src.suffix.lower()}"
    shutil.copyfile(src, dst)
    write_new(cdir / f"{stem}.md", render_template(
        "concept.md", id=stem, image=dst.name, prompt=prompt, tool=tool, date=today(),
        sha256=sha256_file(dst)) + extra)
    (cdir / ".gitkeep").unlink(missing_ok=True)
    return stem, dst


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
    skipped: list[str] = []
    if RELEASE_RE.fullmatch(src):
        root_version = p.release_root_version(src)
        # 只继承版式；guide.md 为只追加文件，新发布重新起稿，避免误继承。
        # 系统不认识的文件（如旧发布中的 cover-*.svg，封面已改由 ./logo cover 管理）不继承
        layouts.mkdir(parents=True)
        for f in sorted((p.release_dir(src) / "layouts").glob("*.svg")):
            if is_layout_file(f.name):
                shutil.copyfile(f, layouts / f.name)
            else:
                skipped.append(f.name)
    elif VERSION_ID_RE.match(src):
        version_svg = p.version_dir(src) / "logo.svg"  # 校验存在
        import series  # 延迟导入：series.py 依赖本模块
        series.release_gate(p, src)  # 系列子品牌：须先通过系列评审
        root_version = src
        layouts.mkdir(parents=True)
        if version_svg.exists():
            shutil.copyfile(version_svg, layouts / "primary.svg")  # 主版以定稿版本起稿
    else:
        raise LogoError("--from 需要版本号（A-v004）或发布号（r1）")
    write_new(rdir / "release.md", render_template(
        "release.md", id=rid, based_on=src, date=today(), layouts=RELEASE_LAYOUT_CHECKLIST))
    write_new(rdir / "guide.md", render_template("release-guide.md", id=rid, name=p.cfg["project"]["name"]))
    (p.dir / "releases" / ".gitkeep").unlink(missing_ok=True)
    # 发布即确认定稿：选定方向/版本与发布的来源保持一致（唯一事实来源仍是 project.toml）
    p.set_state(stage="releasing", current_release=rid,
                selected_direction=root_version.split("-")[0], selected_version=root_version)
    print(f"已创建发布：{rid} → {rel(rdir)}（定稿版本：{root_version}）")
    if skipped:
        print(f"未继承 {src} 中不属于 Logo 版式的文件：{', '.join(skipped)}（封面请用 ./logo cover 管理）")
    if VERSION_ID_RE.match(src) and (layouts / "primary.svg").exists():
        print(f"已用 {src}/logo.svg 起稿主版 layouts/primary.svg：提交前可调整（如留白），提交后冻结")
    print("其余版式可选（见 release.md 清单）；完成后用 ./logo preview 查看自动排版效果")


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


# Logo 文字默认只开不改变字形的特性：字距（kern）及 HarfBuzz 的必需特性（ccmp、locl、mark、mkmk 等）照常；
# 会替换字形的连字与上下文替换默认关闭，需要时用 --features 开启（如 calt、ss02、cv05）
DEFAULT_FEATURES = {"kern": True, "liga": False, "clig": False, "calt": False, "dlig": False}
FEATURE_RE = re.compile(r"^([+-]?)([A-Za-z0-9]{4})(?:=(\d+))?$")
LANG_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")  # BCP 47 语言标签（如 zh-Hans、en-US）


def parse_features(spec: str | None, no_kern: bool = False) -> dict[str, bool | int]:
    """--features 写法：tag 开启、-tag 关闭、tag=N 选第 N 个替代（如 cv05=2），逗号分隔。"""
    feats: dict[str, bool | int] = dict(DEFAULT_FEATURES)
    for item in filter(None, (x.strip() for x in (spec or "").split(","))):
        m = FEATURE_RE.match(item)
        if not m:
            raise LogoError(f"无法识别的排版特性：{item!r}（写法如 calt、-kern、ss02、cv05=2）")
        sign, tag, num = m.groups()
        feats[tag] = int(num) if num else sign != "-"
    if no_kern:
        feats["kern"] = False
    return feats


def shape_text(font_path: Path, text: str, features: dict, lang: str | None = None,
               font_number: int = 0) -> list[tuple[int, int, int, int, int]]:
    """用 HarfBuzz 排版，返回 [(字形编号, 字符序号, x 前进量, x 偏移, y 偏移)]，单位为字体单位。"""
    try:
        import uharfbuzz as hb
    except ImportError:
        raise LogoError("缺少 uharfbuzz，请先运行 ./logo setup")
    face = hb.Face(hb.Blob.from_file_path(str(font_path)), font_number)
    font = hb.Font(face)
    buf = hb.Buffer()
    buf.add_codepoints([ord(c) for c in text])  # 按码位加入：cluster 即字符序号（若用 add_utf8 则是字节偏移）
    buf.guess_segment_properties()
    if lang:
        buf.language = lang
    hb.shape(font, buf, features)
    return [(i.codepoint, i.cluster, pos.x_advance, pos.x_offset, pos.y_offset)
            for i, pos in zip(buf.glyph_infos, buf.glyph_positions)]


def harfbuzz_version() -> str:
    import uharfbuzz as hb
    return hb.version_string()


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
    else:
        print("⚠ 未指定 --project，无法校验填充色是否在色板中", file=sys.stderr)
    features = parse_features(a.features, a.no_kern)
    if a.lang and not LANG_RE.match(a.lang):  # 会写进 SVG 注释，须是合法的语言标签
        raise LogoError(f"--lang 须为语言标签（如 zh-Hans、zh-Hant、en），收到：{a.lang!r}")
    font_number = entry.get("font_number", 0)
    glyphs = shape_text(path, a.text, features, a.lang, font_number)
    missing = sorted({a.text[cl] for gid, cl, *_ in glyphs if gid == 0}, key=a.text.index)
    if missing:  # HarfBuzz 用 .notdef（空框）代替字体里没有的字
        raise LogoError(f"字体 {a.font} 不包含这些字符：{''.join(missing)}")
    font = TTFont(path, fontNumber=font_number)
    gs = font.getGlyphSet()
    order = font.getGlyphOrder()
    scale = a.size / font["head"].unitsPerEm
    pen = SVGPathPen(gs, ntos=fmt_num)
    bpen = BoundsPen(gs)
    x = 0.0
    for gid, _cluster, x_adv, x_off, y_off in glyphs:
        t = (scale, 0, 0, -scale, x + x_off * scale, -y_off * scale)  # HarfBuzz 的 y 向上，SVG 的 y 向下
        gs[order[gid]].draw(TransformPen(pen, t))
        gs[order[gid]].draw(TransformPen(bpen, t))
        x += x_adv * scale
        if x_adv:  # 零宽字形（组合附加符号）后不加字间距，免得符号与字母分开
            x += a.tracking
    d = pen.getCommands()
    bounds = bpen.bounds or (0, 0, 0, 0)
    safe_text = a.text.replace("--", "‐‐")  # XML 注释中不允许出现 “--”
    feat_str = ",".join(f"{k}={int(v)}" for k, v in sorted(features.items()))
    comment = (f"<!-- outline: font={a.font} size={a.size} tracking={a.tracking} "
               f"shaper=harfbuzz-{harfbuzz_version()} features={feat_str} lang={a.lang or 'auto'} "
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
    if a.project and Project(a.project).add_font(a.font):
        print(f"已将字体 {a.font} 写入 {a.project} 的 [fonts] used", file=sys.stderr)


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
    if a.action == "review":
        if len(fonts) != 1 or not a.ids:
            raise LogoError("用法：./logo fonts review <字体 id>")
        f = fonts[0]
        dest = license_review_path(f)
        write_new(dest, render_template("font-license-review.md", id=f["id"], family=f.get("family", ""),
                                        weight=f.get("weight", "")))
        print(f"已创建授权复核：{rel(dest)}（填写后由 check 校验；两项“允许”须为“是”）")
        return
    if a.action == "fetch":
        for f in fonts:
            print(f"{f['id']}: {fetch_font(f)}")
        return
    usage: dict[str, set[str]] = {}
    if projects_repo_ready():
        for p in all_projects():
            for fid in set(p.cfg.get("fonts", {}).get("used", [])) | set(p.svg_fonts()):
                usage.setdefault(fid, set()).add(p.slug)
    for f in fonts:
        used = ", ".join(sorted(usage.get(f["id"], set()))) or "—"
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
        ldir = rdir / "layouts"
        known = set(LOGO_LAYOUTS) | {"app-icon", "avatar"}  # 旧发布中的 cover-* 不属于 Logo 资产，不再预览
        items = [(f.stem, f.read_text(encoding="utf-8")) for f in layouts if f.stem.split(".")[0] in known]
        try:  # 未手工覆盖时，附上自动排版的 App 图标与头像，导出前即可查看效果
            if not (ldir / "app-icon.svg").exists():
                items.append(("app-icon（自动排版）", app_icon_text(p, ldir)))
            if not (ldir / "avatar.svg").exists():
                items.append(("avatar（自动排版）", avatar_text(p, ldir)))
        except LogoError as e:
            print(f"⚠ 无法预览自动排版：{e}")
        with tempfile.TemporaryDirectory() as td:
            cells = []
            for i, (label, text) in enumerate(items):
                png = render_text(text, Path(td) / f"{i}.png", 360)
                cell = Path(td) / f"cell-{i}.png"
                run(["magick", str(png), "-background", "white", "-gravity", "center",
                     "-extent", "400x400", str(cell)])
                cells.append(cell)
            out = CACHE / p.slug / "preview" / f"{a.target}-sheet.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            run(["magick", "montage", *map(str, cells), "-tile", "4x", "-geometry", "+6+6",
                 "-background", "#DDDDDD", str(out)])
        print(f"预览：{rel(out)}")
        print("顺序（从左到右、从上到下）：" + ", ".join(label for label, _ in items))
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
def resvg_version() -> str:
    return run(["resvg", "--version"], capture=True).strip()


def export_release(p: Project, rid: str | None) -> tuple[str, Path, str]:
    """解析并校验要导出的发布：返回 (发布号, 版式目录, 定稿版本)。"""
    rid = rid or p.cfg["state"].get("current_release")
    if not rid:
        raise LogoError("未指定发布号，且 project.toml 中没有 current_release")
    rdir = p.release_dir(rid)
    ldir = rdir / "layouts"
    root_version = p.release_root_version(rid)  # 追溯链不完整时拒绝导出
    if not (ldir / "primary.svg").exists():
        raise LogoError(f"{rid} 缺少主版 primary.svg")
    errors: list[str] = []
    for f in sorted(ldir.glob("*.svg")):
        check_svg(f, layout_palette(p, f, set(p.palette.values())), errors, [])
    if errors:
        raise LogoError("版式未通过检查：\n  " + "\n  ".join(errors))
    return rid, ldir, root_version


def build_target(p: Project, ldir: Path, target: str, out: Path) -> None:
    """在 out 目录中生成一个目标的全部文件（只用 resvg 与标准库，结果可重复）。"""
    out.mkdir(parents=True)
    t = load_targets()
    slug = p.slug
    if target == "svg":
        for name in release_logo_layouts(ldir):
            for mode in p.cfg.get("color_modes", {}).get("modes", ["color"]):
                (out / f"{slug}-{name}-{mode}.svg").write_text(layout_mode_text(p, ldir, name, mode), encoding="utf-8")
    elif target == "favicon":
        fp = t["favicon"]
        mark = layout_mode_text(p, ldir, mark_layout(ldir), "color")
        pngs = {s: render_text(mark, out / f"favicon-{s}.png", s) for s in fp["sizes"]}
        write_ico([pngs[s] for s in fp["ico_sizes"]], out / "favicon.ico")
        render_text(app_icon_text(p, ldir), out / "apple-touch-icon.png", fp["apple_touch_size"], app_background(p))
    elif target == "ios":
        for s in t["ios"]["sizes"]:
            render_text(app_icon_text(p, ldir), out / f"AppIcon-{s}.png", s, app_background(p))  # 不透明
    elif target == "android":
        ap, bg = t["android"], app_background(p)
        icon = app_icon_text(p, ldir)
        for dens, s in ap["mipmap"].items():
            render_text(icon, out / f"mipmap-{dens}" / "ic_launcher.png", s)
        render_text(icon, out / f"play-store-{ap['play_store_size']}.png", ap["play_store_size"], bg)
        size = ap["adaptive_size"]
        render_text(solid_svg(bg), out / "adaptive" / "ic_launcher_background.png", size)
        render_text(android_foreground_text(p, ldir, icon_mode(p)), out / "adaptive" / "ic_launcher_foreground.png", size)
        render_text(android_foreground_text(p, ldir, "mono"), out / "adaptive" / "ic_launcher_monochrome.png", size)
    elif target == "avatar":
        av = avatar_text(p, ldir)
        for s in t["avatar"]["sizes"]:
            render_text(av, out / f"{slug}-avatar@{s}.png", s)
    else:
        raise LogoError(f"未知目标：{target}")


def short_head(repo: str) -> str:
    """系统仓库或项目仓库当前提交的短哈希（尚无提交时为空），写入导出记录用于追溯。"""
    r = git("rev-parse", "--short", "HEAD", check=False, repo=repo)
    return r.stdout.decode().strip() if r.returncode == 0 else ""


def write_dist_extras(p: Project, rid: str, rdir: Path, root_version: str, dist: Path, built: list[str]) -> dict:
    """写入 guide.md（发布指南 + 色板附录）与 manifest.json：只登记交付物（各目标目录与 guide.md），
    每个目标记录生成它的版本（只生成部分目标时，其余目标保留上次的记录）。"""
    guide_src = rdir / "guide.md"
    pal_rows = "\n".join(f"| {k} | `{v}` |" for k, v in p.palette.items())
    (dist / "guide.md").write_text(
        f"<!-- 由 ./logo export 生成；内容来源：{rel(guide_src)} 与 project.toml -->\n\n"
        + guide_src.read_text(encoding="utf-8")
        + f"\n\n---\n\n## 附录：色板（自动生成）\n\n| 色名 | 色值 |\n|---|---|\n{pal_rows}\n", encoding="utf-8")

    try:
        old = json.loads((dist / "manifest.json").read_text(encoding="utf-8")).get("targets", {})
    except (OSError, ValueError, AttributeError):
        old = {}
    old = old if isinstance(old, dict) else {}  # 旧格式（目标列表）没有逐目标来源
    origin = {
        "git_head": short_head("projects"),  # 项目仓库：设计内容的版本
        "system_head": short_head("system"),  # 系统仓库：生成它的工具版本
        "renderer": resvg_version(),          # 渲染器版本：重新生成出现像素差异时据此排查
    }
    present = [t for t in TARGET_NAMES if (dist / t).is_dir()]
    targets = {t: origin if t in built else old.get(t, dict.fromkeys(origin)) for t in present}
    files = sorted([dist / "guide.md"] + [f for t in present for f in (dist / t).rglob("*") if f.is_file()])
    manifest = {
        "project": p.slug,
        "release": rid,
        "based_on": load_release_meta(rdir).get("based_on", ""),
        "design_version": root_version,
        "targets": targets,
        "files": {f.relative_to(dist).as_posix(): sha256_file(f) for f in files},
    }
    (dist / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def cmd_export(a) -> None:
    p = Project(a.slug)
    if a.list:
        declared = project_targets(p)
        for t in TARGET_NAMES:
            print(f"{t:<8} {'已声明' if t in declared else '—'}")
        return
    rid, ldir, root_version = export_release(p, a.release)
    rdir = ldir.parent
    if not (rdir / "guide.md").exists():
        raise LogoError(f"{rid} 缺少 guide.md（交付物的一部分）")
    holes = unfilled_placeholders(f"{p.slug}/releases/{rid}/guide.md", (rdir / "guide.md").read_text(encoding="utf-8"))
    if holes:
        raise LogoError(f"{rid}/guide.md 是交付物的一部分，还有 {len(holes)} 处模板占位未填写（如“{holes[0]}”）；"
                        "guide.md 只能追加，请新建发布并在提交前填完")
    targets = a.targets or project_targets(p)
    unknown = [t for t in targets if t not in TARGET_NAMES]
    if unknown:
        raise LogoError(f"未知目标：{', '.join(unknown)}（可选：{', '.join(TARGET_NAMES)}）")
    if not targets:
        raise LogoError("没有要生成的目标：在 [export] targets 中声明，或在命令中点名，如 ./logo export demo ios")
    dist = p.dir / "dist" / rid
    build = p.dir / "dist" / f".build-{rid}"
    if build.exists():
        shutil.rmtree(build)
    try:
        for t in targets:  # 全部目标都生成成功后才替换，任何一个失败都不动已有产物
            build_target(p, ldir, t, build / t)
    except BaseException:
        shutil.rmtree(build, ignore_errors=True)
        raise
    dist.mkdir(parents=True, exist_ok=True)
    for t in targets:  # 逐个目标替换：只动本次生成的目标，其他目标的旧产物保留
        old = dist / f".old-{t}"
        if (dist / t).exists():
            (dist / t).rename(old)
        (build / t).rename(dist / t)
        shutil.rmtree(old, ignore_errors=True)
    shutil.rmtree(build, ignore_errors=True)
    manifest = write_dist_extras(p, rid, rdir, root_version, dist, targets)
    print(f"已生成 {', '.join(targets)} → {rel(dist)}（{rid} ← {root_version}；目录内共 {len(manifest['files'])} 个文件，不提交）")
    committed = projects_paths("ls-tree", "-r", "--name-only", "-z", "HEAD")
    for w in target_warnings(p, ldir) + release_doc_warnings(p, rid, committed):
        print(f"⚠ {w}")


def cmd_render(a) -> None:
    """按需生成单张图：任意标志版式（或 app-icon / avatar 的排版结果）、配色与尺寸。"""
    p = Project(a.slug)
    rid, ldir, _ = export_release(p, a.release)
    name = a.layout
    if name in ("app-icon", "avatar") and a.mode:
        raise LogoError(f"{name} 的配色由 [export] icon_mode 决定（手工覆盖时按文件原样），--mode 不适用")
    if name == "app-icon":
        text = app_icon_text(p, ldir)
    elif name == "avatar":
        text = avatar_text(p, ldir)
    elif (ldir / f"{name}.svg").exists() and name in LOGO_LAYOUTS:
        text = layout_mode_text(p, ldir, name, a.mode or "color")
    else:
        raise LogoError(f"{rid} 没有版式 {name}（可选：{', '.join(release_logo_layouts(ldir) + ['app-icon', 'avatar'])}）")
    mode = a.mode or "color"
    out_dir = p.dir / "dist" / "render" / rid  # 不放进交付目录 dist/<发布号>/，manifest 只登记交付物
    out_dir.mkdir(parents=True, exist_ok=True)
    if a.format == "svg":
        out = out_dir / f"{p.slug}-{name}-{mode}.svg"
        out.write_text(text, encoding="utf-8")
    else:
        out = render_text(text, out_dir / f"{p.slug}-{name}-{mode}@{a.size}.png", a.size)
    print(f"已生成：{rel(out)}（不提交）")


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


def legacy_config(cfg: dict) -> list[str]:
    """project.toml 中已被取代的旧写法及迁移建议（旧写法仍兼容，只提醒）。"""
    out = []
    if "layouts" in cfg:
        out.append("[layouts] 已不起作用，可删除（生成时以发布中实际存在的版式为准）")
    ex = cfg.get("export", {})
    if "profiles" in ex:
        if "targets" in ex:
            out.append("[export] profiles 已被 targets 取代，可删除")
        else:
            names = [LEGACY_PROFILE_TARGETS[x] for x in ex["profiles"] if x in LEGACY_PROFILE_TARGETS]
            out.append("[export] profiles 改为 targets = [" + ", ".join(
                f'"{n}"' for n in TARGET_NAMES if n in names) + "]")
    for old, new in (("android_background", "app_background"), ("android_foreground_mode", "icon_mode")):
        if old in ex:
            out.append(f"[export] {old} 改名为 {new}" + ("（新名已存在，旧名可删除）" if new in ex else ""))
    return out


def release_doc_warnings(p: Project, rid: str, committed: set[str]) -> list[str]:
    """已提交的发布文档（只追加，不能改写）是否可交付、可追溯。尚未提交的由 check_new_docs 检查，这里跳过。

    - guide.md 留有模板占位：export 会拒绝，追加也补不上，只能新建发布；
    - release.md 没有引用决策记录：追溯链缺“决策依据”，可补写决策后在末尾追加引用。
    """
    out = []
    rdir = p.release_dir(rid)
    base = f"{p.slug}/releases/{rid}"
    guide = rdir / "guide.md"
    if f"{base}/guide.md" in committed and guide.exists():
        holes = unfilled_placeholders(f"{base}/guide.md", guide.read_text(encoding="utf-8"))
        if holes:
            out.append(f"{rel(guide)}: 发布 {rid} 无法导出——guide.md 已提交但还有 {len(holes)} 处模板占位"
                       f"（只能追加，补不上）；请 ./logo release {p.slug} --from {rid} 新建发布，并在提交前填完 guide.md")
    rmd = rdir / "release.md"
    if f"{base}/release.md" in committed and rmd.exists():
        refs = DECISION_REF_RE.findall(rmd.read_text(encoding="utf-8"))
        if not refs:
            out.append(f"{rel(rmd)}: 发布 {rid} 没有引用决策记录，追溯链缺少“决策依据”；"
                       f"可用 ./logo decision {p.slug} <短名> 补写决策，再在 release.md 末尾追加引用 decisions/NNN-*.md")
        for ref in refs:
            if not (p.dir / "decisions" / ref).exists():
                out.append(f"{rel(rmd)}: 引用的决策记录不存在 decisions/{ref}")
    return out


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
    if (legacy := legacy_config(cfg)):
        warnings.append(f"{r}: 使用了旧写法（仍兼容）：{'；'.join(legacy)}")
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
    declared = cfg.get("fonts", {}).get("used", [])
    for fid in declared:
        if fid not in font_ids:
            errors.append(f"{r}: 使用了未登记的字体 {fid}")
    actual = p.svg_fonts(staged)
    for fid, files in sorted(actual.items()):
        if fid not in font_ids:
            errors.append(f"{files[0]}: 转曲所用字体 {fid} 未在 fonts.toml 登记")
        elif fid not in declared:
            errors.append(f"{r}: SVG 中用字体 {fid} 转曲（如 {files[0]}），但 [fonts] used 未声明")
    for fid in declared:
        if fid in font_ids and fid not in actual:
            warnings.append(f"{r}: [fonts] used 声明了 {fid}，但没有 SVG 用它转曲（依据 outline 生成的注释判断）")
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
    committed = projects_paths("ls-tree", "-r", "--name-only", "-z", "HEAD")  # 已提交（冻结/只追加生效）的文件
    indexed = projects_paths("ls-files", "-z", "--cached") if staged else set()
    for rd in p.releases():
        try:
            release_versions[rd.name] = p.release_root_version(rd.name)
        except LogoError as e:
            errors.append(f"{rel(rd)}: 追溯链断裂：{e}")
        ldir = rd / "layouts"
        if not (ldir / "primary.svg").exists():
            errors.append(f"{rel(ldir)}: 缺少主版 primary.svg（发布唯一必需的版式）")
        unknown: dict[str, list[str]] = {"frozen": [], "new": [], "staged": []}
        for f in sorted(ldir.glob("*.svg")) if ldir.exists() else []:
            check_svg(f, layout_palette(p, f, palette), errors, warnings, staged)
            if not is_layout_file(f.name):
                path = f.relative_to(PROJECTS).as_posix()
                kind = "frozen" if path in committed else "new" if not staged else "staged" if path in indexed else ""
                if kind:
                    unknown[kind].append(f.name)
                continue
            m = re.fullmatch(r"(.+)\.([a-z]+)", f.stem)  # 手工配色覆盖文件 name.mode.svg
            if m:
                base, mode = m.groups()
                if not (ldir / f"{base}.svg").exists():
                    errors.append(f"{rel(f)}: 覆盖文件没有对应的基础版式 {base}.svg")
                if mode == "color" or mode not in modes:
                    errors.append(f"{rel(f)}: 覆盖文件的配色 {mode} 不在 [color_modes] modes 中（color 不可覆盖）")
        hint = f"不是系统认识的版式（{LAYOUT_NAMES_HINT}），生成时会被忽略"
        if unknown["frozen"] and rd.name == cur_rel:  # 已冻结无法删除；只对当前发布提醒
            warnings.append(f"{rel(ldir)}: {', '.join(unknown['frozen'])} {hint}；"
                            f"已冻结，./logo release {p.slug} --from {rd.name} 不会继承")
        if unknown["new"]:
            warnings.append(f"{rel(ldir)}: {', '.join(unknown['new'])} {hint}；提交时会被拦截，请改名或删除（封面请用 ./logo cover）")
        if unknown["staged"]:
            errors.append(f"{rel(ldir)}: {', '.join(unknown['staged'])} {hint}；请改名或删除（封面请用 ./logo cover）")

    if cur_rel in release_versions:
        root = release_versions[cur_rel]
        if sel_ver and sel_ver != root:
            warnings.append(f"{r}: 选定版本 {sel_ver} 尚未发布（当前发布 {cur_rel} 基于 {root}）；"
                            f"确认定稿后运行 ./logo release {p.slug} --from {sel_ver}")
        ldir = p.release_dir(cur_rel) / "layouts"
        try:
            if (ldir / "primary.svg").exists():
                warnings.extend(target_warnings(p, ldir))
        except LogoError as e:
            warnings.append(f"{rel(ldir)}: 无法完成版式检查：{e}")
        warnings.extend(release_doc_warnings(p, cur_rel, committed))

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

    import covers  # 延迟导入：covers.py 依赖本模块
    covers.check_covers(p, errors, warnings, staged)
    import importer  # 延迟导入：importer.py 依赖本模块
    importer.terms_warnings(p, warnings)

    if (p.dir / "final").exists():
        warnings.append(f"{rel(p.dir / 'final')}: 交付物已改为按需生成到 dist/（不提交），旧的 final/ 可以删除")


def license_review_path(entry: dict) -> Path:
    return FONTS_DIR / Path(entry["file"]).parent / LICENSE_REVIEW_NAME


def license_review_errors(entry: dict) -> list[str]:
    """非开源字体的授权复核：文件存在、模板填完、两项“允许”均为“是”。"""
    f = license_review_path(entry)
    if not f.exists():
        return [f"非开源字体须有授权复核 {rel(f)}（./logo fonts review {entry.get('id')} 创建）"]
    text = f.read_text(encoding="utf-8")
    lines = {ln.strip() for ln in text.splitlines()}
    holes = [ph for ph in template_placeholders("font-license-review.md") if ph in lines]
    if holes:
        return [f"{rel(f)} 还有 {len(holes)} 处模板占位未填写（如“{holes[0]}”）"]
    out = []
    for label in LICENSE_REVIEW_REQUIRED:
        m = re.search(rf"^\s*-\s*\*\*{re.escape(label)}\*\*\s*[:：]\s*(\S*)", text, re.M)
        if not m or not m.group(1).startswith("是"):
            out.append(f"{rel(f)}：“{label}”须为“是”，否则不得使用该字体")
    return out


def check_fonts(errors: list[str], warnings: list[str]) -> None:
    """检查字体登记表（使用情况由各项目的 [fonts] used 与 SVG 内容推导，不在此登记）。"""
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
        if f.get("tier") == "free-commercial" and f.get("file"):
            errors.extend(f"{where}: {e}" for e in license_review_errors(f))
        if f.get("file"):
            fp = FONTS_DIR / f["file"]
            if not fp.exists():
                warnings.append(f"{where}: 字体文件未下载，运行 ./logo fonts fetch {fid}")
            elif f.get("sha256") and sha256_file(fp) != f["sha256"]:
                errors.append(f"{where}: sha256 与文件不符")
        if f.get("license_file") and not (FONTS_DIR / f["license_file"]).exists():
            errors.append(f"{where}: 授权文件不存在")


def spec_warnings(name: str, item: dict) -> list[str]:
    """单个规格条目的依据检查：official 须有官方来源，common/generic 须写明依据；均按年复核。"""
    if item.get("generic"):
        return []  # 通用比例，不对应具体平台
    out = []
    limit = dt.date.today() - dt.timedelta(days=365)
    basis = item.get("basis", "official")
    if basis not in ("official", "common", "generic"):
        out.append(f"导出规格 {name} 的 basis 只能是 official、common 或 generic")
    v = item.get("verified_on", "")
    if basis != "official":
        if not item.get("verify_note"):
            out.append(f"导出规格 {name} 采用{'通用' if basis == 'common' else '泛用'}规格，须在 verify_note 中写明依据")
        if not v:
            out.append(f"导出规格 {name} 缺少复核日期 verified_on")
        elif dt.date.fromisoformat(v) < limit:
            out.append(f"导出规格 {name} 的通用规格超过 12 个月未复核，建议检查平台是否已公布官方规格")
        return out
    if not v:
        note = item.get("verify_note")
        out.append(f"导出规格 {name} 尚未按官方文档核实" + (f"（{note}）" if note else ""))
    elif dt.date.fromisoformat(v) < limit:
        out.append(f"导出规格 {name} 核实已超过 12 个月（{v}），建议复核")
    if not item.get("source"):
        out.append(f"导出规格 {name} 缺少官方规格来源链接 source")
    return out


def profile_warnings() -> list[str]:
    """Logo 目标（iOS / Android / 头像）的规格依据检查；封面平台由 covers.py 检查。"""
    t = load_targets()
    return [w for key in ("ios", "android", "avatar") for w in spec_warnings(key, t[key])]


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


def projects_paths(*args: str) -> set[str]:
    """项目仓库中 git 命令（ls-tree / ls-files 等，带 -z）列出的路径；命令失败（如尚无提交）时为空集。"""
    if not projects_repo_ready():
        return set()
    r = git(*args, check=False, repo="projects")
    return {x for x in r.stdout.decode("utf-8").split("\0") if x} if r.returncode == 0 else set()


def check_history(warnings: list[str]) -> None:
    """扫描项目仓库全部历史中违反不可变规则的提交（冻结文件被改/删、只追加文件被改写）。

    钩子只把守下一次提交，用 --no-verify 绕过的提交事后只能从历史里发现；改写历史（amend / rebase）
    无法从本地察觉，需要远程仓库保护。结果按 HEAD 与规则缓存，HEAD 不变时不重复扫描。
    """
    r = git("rev-parse", "HEAD", check=False, repo="projects")
    if r.returncode != 0:
        return
    rules = "|".join(pt.pattern for pt in FROZEN_PATTERNS + APPEND_ONLY_PATTERNS)
    key = hashlib.sha256(f"v1|{r.stdout.decode().strip()}|{rules}".encode()).hexdigest()
    try:
        cache = json.loads(HISTORY_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if cache.get("key") != key:
        found = []
        log = git("-c", "core.quotePath=false", "log", "--format=@@%H", "--name-status", "--no-renames",
                  "--diff-filter=MD", repo="projects").stdout.decode("utf-8")
        commit = ""
        for line in log.splitlines():
            if line.startswith("@@"):
                commit = line[2:]
                continue
            status, _, path = line.partition("\t")
            if not path:
                continue
            if any(pt.match(path) for pt in FROZEN_PATTERNS):
                found.append(f"{commit[:7]} {'删除' if status == 'D' else '修改'}了冻结文件 {path}")
            elif any(pt.match(path) for pt in APPEND_ONLY_PATTERNS):
                if status == "D":
                    found.append(f"{commit[:7]} 删除了只追加文件 {path}")
                    continue
                old = git("show", f"{commit}^:{path}", check=False, repo="projects").stdout
                new = git("show", f"{commit}:{path}", check=False, repo="projects").stdout
                if not new.startswith(old.rstrip(b"\n")):
                    found.append(f"{commit[:7]} 改写了只追加文件 {path}")
        cache = {"key": key, "found": found}
        HISTORY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    found = cache["found"]
    if found:
        more = f" 等 {len(found)} 处" if len(found) > 5 else ""
        warnings.append(f"项目仓库历史中有提交违反不可变规则（多半是用 --no-verify 绕过了钩子）：{'；'.join(found[:5])}{more}。"
                        "历史无法自动修复，请确认是否有意为之")


def check_backup(warnings: list[str]) -> None:
    """两个仓库是否有本机之外的备份：没有远程、没有上游或有未推送的提交时提醒（只读本地记录，不访问网络）。"""
    for repo, label, cmd in (("system", "系统", "git"), ("projects", "项目", "git -C projects")):
        if repo == "projects" and not projects_repo_ready():
            continue
        if git("rev-parse", "--verify", "HEAD", check=False, repo=repo).returncode != 0:
            continue  # 尚无提交
        if not git("remote", check=False, repo=repo).stdout.decode().split():
            warnings.append(f"{label}仓库没有配置远程仓库：全部历史只存在本机，磁盘损坏即丢失，也无法在服务器端防止历史被改写；"
                            f"建议添加私有远程（{cmd} remote add origin <地址>）并定期推送")
            continue
        up = git("rev-parse", "--abbrev-ref", "@{u}", check=False, repo=repo)
        if up.returncode != 0:
            warnings.append(f"{label}仓库当前分支没有设置上游，无法判断是否已备份（{cmd} push -u origin <分支>）")
            continue
        ahead = int(git("rev-list", "--count", "@{u}..HEAD", repo=repo).stdout.decode().strip() or 0)
        if ahead:
            warnings.append(f"{label}仓库有 {ahead} 个提交尚未推送到 {up.stdout.decode().strip()}")


@functools.lru_cache(maxsize=None)
def template_placeholders(template_name: str) -> tuple[str, ...]:
    """模板中必须被替换的占位行：含（提示）或以冒号结尾的空字段；“追加区”、{{变量}} 行与 > 说明除外。"""
    out, in_append = [], False
    for line in (TEMPLATES / template_name).read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("#"):
            in_append = "追加" in s
            continue
        if not s or in_append or "{{" in s or s.startswith(">"):
            continue  # 空行、追加区、变量行与“>”引用的说明文字都不是占位
        if "（" in s or re.search(r"[:：]$", s):
            out.append(s)
    return tuple(out)


def unfilled_placeholders(path: str, text: str) -> list[str]:
    """返回文档中仍原样保留的模板占位行；path 为相对项目仓库的路径。"""
    lines = {ln.strip() for ln in text.splitlines()}
    holes = [ph for pat, tpl in DOC_TEMPLATES if pat.match(path) for ph in template_placeholders(tpl)]
    return [ph for ph in dict.fromkeys(holes) if ph in lines]


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
    m = SERIES_FILE_RE.fullmatch(path)
    if not m:
        return False
    inner = m.group(2)
    return inner in ("series.toml", "series.md") or (
        inner.startswith("covers/") and any(pt.fullmatch(inner) for pt in PROJECT_FILE_PATTERNS))


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


def index_row(p: Project, role: str | None = None) -> str:
    c = p.cfg
    st = c.get("state", {})
    return (f"| [{p.slug}]({p.slug}/brief.md) | " + (f"{role} | " if role else "")
            + f"{c.get('project', {}).get('name', '')} | "
            f"{st.get('stage', '')} | {len(p.directions())} | {st.get('selected_direction') or '—'} | "
            f"{st.get('selected_version') or '—'} | {st.get('current_release') or '—'} | {st.get('updated', '')} |")


def build_index() -> str:
    """项目看板：独立项目一张表；有系列时每个系列一张表（含角色，母品牌在前）。"""
    import series  # 延迟导入：series.py 依赖本模块
    header = "| 项目 | 名称 | 阶段 | 方向数 | 选定方向 | 选定版本 | 当前发布 | 更新 |"
    groups = series.index_groups()
    grouped = {p.slug for _, ms in groups for p, _, _ in ms}
    lines = ["# 项目看板", "", "<!-- 由 ./logo index 自动生成，请勿手改 -->", ""]
    solo = [index_row(p) for p in all_projects() if p.slug not in grouped]
    if groups and solo:
        lines += ["## 独立项目", ""]
    if solo or not groups:  # 全部项目都在系列中时，不显示空的独立项目表
        lines += [header, "|---|---|---|---|---|---|---|---|"] + (solo or ["| — | 暂无项目 | | | | | | |"])
    else:
        lines.pop()  # 去掉表头前的空行，系列表自带分隔
    for title, ms in groups:
        lines += ["", f"## {title}", "", header.replace("| 项目 |", "| 项目 | 角色 | 母品牌发布 |"), "|---" * 10 + "|"]
        lines += [index_row(p, f"{role} | {based}") for p, role, based in ms] or ["| — | | | 暂无成员 | | | | | | |"]
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
            import series  # 延迟导入：series.py 依赖本模块
            series.check_series(perr, warnings)
            import covers  # 延迟导入：covers.py 依赖本模块
            covers.check_family_covers(perr, warnings, staged)
            check_secrets("projects", staged, perr)
        if scope != "system":
            check_immutability(staged, perr)
            errors.extend(perr)
        else:
            # 系统提交不因项目仓库工作区里未完成的内容被拦截，但要让人看到
            warnings.extend(f"[项目仓库] {e}" for e in perr)
    if not a.slug:
        check_fonts(errors, warnings)
        if scope != "projects":
            check_stray_files("system", errors, warnings)
            check_secrets("system", a.staged, errors)
        if not a.staged:  # 以下只在手动检查时运行，不拖慢提交
            check_hooks(warnings)
            import importer  # 延迟导入：importer.py 依赖本模块
            importer.check_inbox(warnings)
            check_backup(warnings)
            if has_projects and scope != "system":
                check_history(warnings)
        try:
            warnings.extend(profile_warnings())
            import covers  # 延迟导入：covers.py 依赖本模块
            covers.check_platforms(errors, warnings)
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
    s.add_argument("--series", help="所属系列（先用 ./logo series new 创建）；独立项目不填")
    s.add_argument("--role", choices=["master", "sub"], help="在系列中的角色：master 母品牌 / sub 子品牌")
    s.add_argument("--targets", help="生成目标，逗号分隔（默认 svg,favicon）：" + ", ".join(TARGET_NAMES))
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
    s.add_argument("--from-master", action="store_true",
                   help="系列子品牌（统一型/背书型）以母品牌发布的主版起稿")
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
    s.add_argument("--text", required=True, help="要转曲的文字；以 - 开头时须写成 --text=-文字")
    s.add_argument("--size", type=float, default=100, help="字号（SVG 单位）")
    s.add_argument("--tracking", type=float, default=0, help="字间距（SVG 单位）")
    s.add_argument("--fill", required=True, help="填充色（须在项目色板中）")
    s.add_argument("--project", help="项目短名：校验填充色在色板中，并把字体自动写入该项目的 [fonts] used")
    s.add_argument("--no-kern", action="store_true", help="不应用字距调整（等同 --features=-kern）")
    s.add_argument("--features", help="OpenType 特性，逗号分隔：calt 开启、-kern 关闭、cv05=2 选替代；"
                                      "默认只开字距，连字与上下文替换关闭。以 - 开头时须写成 --features=-kern")
    s.add_argument("--lang", help="语言标签，影响地区字形（如 zh-Hans、zh-Hant）；默认按文字推断")
    s.add_argument("--svg", help="输出为独立 SVG 文件；不填则输出 <path> 片段")
    s.set_defaults(fn=cmd_outline)

    s = sub.add_parser("fonts", help="查看已登记字体、下载并校验（字体文件不进 Git）、创建授权复核")
    s.add_argument("action", nargs="?", choices=["list", "fetch", "review"], default="list",
                   help="list 查看；fetch 下载并校验；review 为非开源字体创建授权复核记录")
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

    s = sub.add_parser("export", help="按需生成 Logo 资产到 dist/（不提交）")
    s.add_argument("slug")
    s.add_argument("targets", nargs="*", help=f"目标（默认项目 [export] targets）：{', '.join(TARGET_NAMES)}")
    s.add_argument("--release", help="发布号（默认 current_release）")
    s.add_argument("--list", action="store_true", help="列出可用目标及项目已声明的目标")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("render", help="按需生成单张图：任意版式、配色、尺寸（输出到 dist/，不提交）")
    s.add_argument("slug")
    s.add_argument("layout", nargs="?", default="primary",
                   help="primary / icon / horizontal / wordmark-cn / wordmark-en，或 app-icon / avatar 的排版结果")
    s.add_argument("--mode", help="color（默认）/ mono / reversed；app-icon / avatar 由 [export] icon_mode 决定")
    s.add_argument("--size", type=int, default=1024)
    s.add_argument("--format", choices=["png", "svg"], default="png")
    s.add_argument("--release", help="发布号（默认 current_release）")
    s.set_defaults(fn=cmd_render)

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

    import covers  # 延迟导入：covers.py 依赖本模块
    covers.register(sub)
    import series  # 延迟导入：series.py 依赖本模块
    series.register(sub)
    import importer  # 延迟导入：importer.py 依赖本模块
    importer.register(sub)
    import handoff  # 延迟导入：handoff.py 依赖本模块
    handoff.register(sub)

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
