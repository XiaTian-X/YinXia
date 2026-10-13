---
name: logo-workflow
description: LOGODesign 的核心操作流，包括立项、概念图、设计方向、迭代评审、决策记录、文字转曲、发布、检查导出。
---

# Logo Workflow

> 所有命令都在仓库根目录执行。首次使用先运行 `./logo setup`（创建 .venv、安装依赖、启用 pre-commit 钩子）。
> 任何中间产物（描摹、调试图、脚本）都放 `.cache/<slug>/`，不要放到根目录或项目目录。

## 1. 新建项目
1. `./logo new <slug> --name <名称>`
2. 填写 `projects/<slug>/brief.md`（含字体策略，引用 fonts.toml 的 id）。
3. 在 `project.toml` 中定义 `[palette]`、按需调整 `[color_modes]`、`[layouts]`（纯图形项目删去 wordmark-cn/en）。
   `[state]` 不要手改。

## 2. 探索概念（可选）
1. 用 `generate_image` 生成图片，或使用用户提供的参考图。
2. 登记：`./logo concept <slug> <图片路径> --name <短名> --prompt "<提示词或来源说明>" [--tool <工具>]`
3. 在生成的 `concepts/NNN-<短名>.md` 末尾追加“灵感提取”。概念图提交后冻结。

## 3. 设计迭代
1. 新建方向：`./logo direction <slug> <字母> <英文短名> --title <中文标题>`，并在 `direction.md` 写核心理念与视觉基调。
2. 新建版本：`./logo version <slug> <字母> [--from B-v002 | --blank]`
3. 编写 SVG：`projects/<slug>/directions/<字母>-<名>/vNNN/logo.svg`。
   文字用 `./logo outline --font <id> --text "…" --fill "#HEX" --project <slug>` 生成 `<path>` 粘贴进来。
4. 预览自查：`./logo preview <slug> <字母>-vNNN`，**自己先看渲染图**（彩色/单色/反白、16/32/64px 可读性）。
5. 写 `notes.md`：改动、理念体现；声明的效果必须与实际 SVG 一致，不夸大。
6. 多方向时出对比图：`./logo compare <slug> [A-v003 B-v002 …]`，展示给用户。
7. 用户反馈：以“日期 + 原话/要点”的形式**追加**到该版本 `notes.md` 的“评审与反馈”段。
8. 选定方向时：`./logo decision <slug> select-direction-<x> --title "选定方向 X"`，写明取舍理由；
   然后 `./logo state <slug> selected_direction=<字母> selected_version=<字母>-vNNN`。
9. `./logo index && ./logo check`
10. 提交：`git add -A && git commit -m "[<slug>] <字母>-vNNN: <说明>"`。提交后该版本冻结。

## 4. 定稿发布
1. 定稿决策：`./logo decision <slug> finalize-<字母>-vNNN`（为什么这个版本可以定稿）。
2. 创建发布：`./logo release <slug> --from <字母>-vNNN`（自动把 selected_version/direction 同步为该版本）。
   基于上一个发布修订版式：`./logo release <slug> --from rN`。
3. 补全版式：按命令输出的清单，在 `releases/rN/layouts/` 补齐所有 svg：
   - `avatar.svg` 的底色元素须带 `id="background"`，图形放在圆形安全区内（check 会检测）；
   - 单色/反白自动换色效果不好时，可提供覆盖文件 `<版式>.mono.svg` / `<版式>.reversed.svg`。
4. 在 `release.md` 的“决策依据”中链接决策记录；完善 `guide.md`（最小尺寸、留白、错误用法）。
5. 预览：`./logo preview <slug> rN`（全部版式）或 `./logo preview <slug> rN/icon`。
6. `./logo index && ./logo check`，提交：`git commit -m "[<slug>] release rN"`
7. 导出：`./logo export <slug>`（生成 final/，阶段自动变为 delivered），然后 `./logo index`、`./logo check`、提交 `"[<slug>] export rN"`。

## 5. 检查结果的处理
- ✗ 错误：必须修复后才能提交（钩子会拦截）。
- ⚠ 提醒：在回复中如实告诉用户，例如“选定版本尚未发布”“final/ 与当前发布不一致”“平台规格待核实”。

## 文字转曲
1. 查阅 `assets/fonts/fonts.toml` 中已登记的字体 id。
2. 新字体入库：只用 OFL/Apache 等开源字体（非开源免费商用字体需先写授权复核决策），保存官方原文件与授权原文，
   在 fonts.toml 登记全部字段（含 sha256、download_url、retrieved_on）。下载前须征得用户同意。
3. 项目使用某字体时：写入 `project.toml` 的 `[fonts] used`，并在 fonts.toml 的 `used_by` 中加入项目 slug。
4. 转曲：`./logo outline --font <id> --text "TEXT" --size 120 --fill "#HEX" --project <slug>`，将输出粘贴至 svg。
