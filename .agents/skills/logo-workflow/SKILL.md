---
name: logo-workflow
description: LOGODesign 的核心操作流，包括立项、概念图、设计方向、迭代评审、决策记录、文字转曲、发布、检查导出。
---

# Logo Workflow

> 所有 `./logo` 命令都在仓库根目录执行。首次使用先运行 `./logo setup`（创建 .venv、安装依赖、初始化项目仓库并启用两个仓库的钩子）。
> 设计工作一律提交到**项目仓库** `projects/`（`git -C projects …`）；根目录的系统仓库只在修改系统本身时提交。
> 任何中间产物（描摹、调试图、脚本）都放 `.cache/<slug>/`，不要放到根目录或项目目录。
> **模板占位必须在提交前填完**：新建的 direction.md、notes.md、release.md、guide.md、决策与概念记录提交后只能追加，
> 带着“（…）”提示或空字段提交会被钩子拦截。

## 1. 新建项目
1. `./logo new <slug> --name <名称>`
2. 填写 `projects/<slug>/brief.md`（含字体策略，引用 fonts.toml 的 id）。
3. 在 `project.toml` 中定义 `[palette]`、按需调整 `[color_modes]`、`[layouts]`（纯图形项目删去 wordmark-cn/en）。
   `[state]` 不要手改。

## 2. 探索概念
1. 用 `generate_image` 生成图片，或使用用户提供的参考图。**凡是设计依据的图片（包括用户上传的参考图）都必须登记**，
   否则追溯链在“灵感来源”处断开。
2. 登记：`./logo concept <slug> <图片路径> --name <短名> --prompt "<提示词或来源说明>" [--tool <工具>]`
   用户参考图用 `--tool user-upload --prompt "<用户原话/来源>"`。
3. 在提交前填写 `concepts/NNN-<短名>.md` 的“灵感提取”。概念图提交后冻结。

## 3. 设计迭代
1. 新建方向：`./logo direction <slug> <字母> <英文短名> --title <中文标题>`，并在 `direction.md` 写核心理念与视觉基调。
2. 新建版本：`./logo version <slug> <字母> [--from B-v002 | --blank] [--inspired-by 001]`
   基于概念图起稿时用 `--inspired-by` 引用概念编号，notes.md 会记录 `concepts/NNN-*.md`。
3. 编写 SVG：`projects/<slug>/directions/<字母>-<名>/vNNN/logo.svg`。
   文字用 `./logo outline --font <id> --text "…" --fill "#HEX" --project <slug>` 生成 `<path>` 粘贴进来。
4. 预览自查：`./logo preview <slug> <字母>-vNNN`，**自己先看渲染图**（彩色/单色/反白、16/32/64px 可读性）。
   出现“非彩色版只保留了彩色版部分细节”时，说明单色/反白版会糊成一块（印章、单色印刷、Android 主题图标都会用到）：
   在 `project.toml` 的 `[color_modes.mono]` 中把部分颜色映射为 `#FFFFFF`、`[color_modes.reversed]` 中映射为 `#000000` 作为留白，
   或在发布时提供 `<版式>.mono.svg` / `<版式>.reversed.svg`（可用黑白）。
5. 写 `notes.md`：替换“改动说明”“理念体现”的占位；声明的效果必须与实际 SVG 一致，不夸大。
6. 多方向时出对比图：`./logo compare <slug> [A-v003 B-v002 …]`，展示给用户。
7. 用户反馈：以“日期 + 原话/要点”的形式**追加**到该版本 `notes.md` 的“评审与反馈”段。
8. 选定方向时：`./logo decision <slug> select-direction-<x> --title "选定方向 X"`，写明取舍理由；
   然后 `./logo state <slug> selected_version=<字母>-vNNN`（方向会自动对齐）。
9. `./logo index && ./logo check`
10. 提交：`git -C projects add -A && git -C projects commit -m "[<slug>] <字母>-vNNN: <说明>"`。提交后该版本冻结。

## 4. 定稿发布
1. 定稿决策：`./logo decision <slug> finalize-<字母>-vNNN`（为什么这个版本可以定稿）。
2. 创建发布：`./logo release <slug> --from <字母>-vNNN`（自动把 selected_version/direction 同步为该版本）。
   基于上一个发布修订版式：`./logo release <slug> --from rN`。
3. 补全版式：按命令输出的清单，在 `releases/rN/layouts/` 补齐所有 svg：
   - `avatar.svg` 的底色元素须带 `id="background"`，图形放在圆形安全区内（check 会检测）；
   - 单色/反白自动换色效果不好时，可提供覆盖文件 `<版式>.mono.svg` / `<版式>.reversed.svg`。
4. 提交前必须：在 `release.md` 的“决策依据”中写明并引用 `decisions/NNN-*.md`（钩子校验存在）；
   填完 `guide.md`（标志结构、最小尺寸、安全留白、错误用法）——它会原样进入交付物，未填完时 `export` 会拒绝。
5. 预览：`./logo preview <slug> rN`（全部版式）或 `./logo preview <slug> rN/icon`。
6. `./logo index && ./logo check`，提交：`git -C projects add -A && git -C projects commit -m "[<slug>] release rN"`
7. 导出：`./logo export <slug>`（生成 final/，阶段自动变为 delivered），然后 `./logo index`、`./logo check`，在项目仓库提交 `"[<slug>] export rN"`。

## 5. 检查结果的处理
- ✗ 错误：必须修复后才能提交（钩子会拦截）。
- ⚠ 提醒：在回复中如实告诉用户，例如“选定版本尚未发布”“final/ 与当前发布不一致”“平台规格待核实”。

## 文字转曲
1. `./logo fonts` 查看已登记字体及是否已下载；未下载时 `./logo fonts fetch <id>`（从官方地址下载并校验 sha256）。
   **字体文件不进 Git**，只提交 fonts.toml 登记与授权原文。
2. 新字体入库：优先 OFL/Apache 等开源字体，授权原文存入 `assets/fonts/<目录>/`，
   在 fonts.toml 登记全部字段（含 sha256、download_url、压缩包内路径 archive_member、retrieved_on），
   之后用 `./logo fonts fetch <id>` 下载并校验。新字体下载前须征得用户同意。
   非开源的免费商用字体（tier = "free-commercial"）还须 `./logo fonts review <id>` 创建并填完授权复核，
   “允许用于 Logo / 商标”“允许转曲”都为“是”才能使用。
3. 项目使用某字体：转曲时带上 `--project <slug>`，字体会自动写入该项目的 `[fonts] used`；
   `./logo fonts` 可查看每款字体被哪些项目使用。
4. 转曲：`./logo outline --font <id> --text "TEXT" --size 120 --fill "#HEX" --project <slug>`，将输出粘贴至 svg。
   默认应用字体自带的字距调整（与 HarfBuzz 排版一致）；需要逐字等距时加 `--no-kern`。
   已入库：`inter`（英文）、`source-han-sans-sc-bold`（思源黑体 SC Bold，中文）。
