---
name: logo-workflow
description: LOGODesign 的核心操作流，包括需求访谈、独立/系列立项、概念图、设计方向、迭代评审、决策记录、文字转曲、发布、检查导出。
---

# Logo Workflow

> 所有 `./logo` 命令都在仓库根目录执行。首次使用先运行 `./logo setup`（创建 .venv、安装依赖、初始化项目仓库并启用两个仓库的钩子）。
> 设计工作一律提交到**项目仓库** `projects/`（`git -C projects …`）；根目录的系统仓库只在修改系统本身时提交。
> 任何中间产物（描摹、调试图、脚本）都放 `.cache/<slug>/`，不要放到根目录或项目目录。
> **模板占位必须在提交前填完**：新建的 direction.md、notes.md、release.md、guide.md、决策与概念记录提交后只能追加，
> 带着“（…）”提示或空字段提交会被钩子拦截。

## 0. 需求访谈（任何设计之前）
**先问清楚再生成。** 用户提出做 Logo 时，不要直接画：按下面的轮次向用户提问（有提问工具时用选择题，每轮最多 4 题；
用户已经说清的不再重复问），据答案建立对应结构的项目。brief.md 未填完时 `./logo direction` 会拒绝开始设计；系列成员还须系列规则 series.md 已填完。

| 轮次 | 问什么 | 答案用于 |
|---|---|---|
| 1. 项目类型 | 独立 Logo / 新建品牌系列 / 加入已有系列（`./logo series` 查看已有系列） | 是否需要系列 |
| 2. 系列（仅系列） | 架构：统一型 / 背书型 / 独立型（不熟悉时用下方说明与例子解释）；本项目是母品牌还是子品牌；新系列要统一哪些视觉基因；母品牌改色、改图形时子品牌怎么跟进（默认跟随只确认一次 / 逐项确认）；各品牌封面要不要统一骨架、要不要家族封面（同时展示多个品牌）及用在哪些平台 | `series new`、`--series/--role`、series.md（含“改版跟进”“系列封面”） |
| 3. 品牌 | 名称；Logo 里放不放文字（中文 / 英文 / 不放）；行业、受众、关键词、品牌故事 | brief 品牌理念、字体策略 |
| 4. 视觉 | 喜欢与禁忌的风格、色彩倾向；有没有参考图（有则 `./logo concept` 登记） | brief 视觉偏好、概念图 |
| 5. 使用场景 | 网站 / App / 社交头像 / 印刷；需要哪些平台的封面（`./logo cover platforms`） | `--targets`、计划的封面平台 |
| 6. 确认 | 把整理好的需求摘要复述给用户，确认或修改 | brief“需求确认”一节 |

三种系列架构（向用户解释时可用）：
- **统一型**：子品牌就是母品牌加名字，如 Google 地图、Google 日历。子品牌不设计新图形，用母品牌标志加名称或换主题色。
- **背书型**：子品牌有自己的标志，注明“母品牌出品”，如 Courtyard by Marriott。子品牌沿用系列的视觉基因（线条、圆角、配色体系）。
- **独立型**：各品牌独立，如宝洁旗下的海飞丝与汰渍。视觉上不要求统一，只归为一组管理。

据答案建项目：
- **独立 Logo**：`./logo new <slug> --name <名称> --targets <目标>`
- **新建系列**：`./logo series new <系列名> --name <名称> --architecture unified|endorsed|independent`，填完 `projects/_series/<系列名>/series.md`
  （系列规则，全系列的设计依据，只追加）；再 `./logo new <slug> --series <系列名> --role master --targets …` 建母品牌。
- **子品牌**：`./logo new <slug> --series <系列名> --role sub --targets …`。统一型 / 背书型须母品牌已发布：
  色板、配色模式与 App 图标配色自动继承母品牌当前发布；统一型子品牌开始设计时用 `./logo version <slug> A --from-master`
  以母品牌主版起稿。独立型子品牌不继承，按独立项目设计。
- **已有项目加入系列**：`./logo series add <系列名> <slug> --role master|sub`（只登记关系，不改已有设计）。

建好后把访谈结果写进 `brief.md`，包括“需求确认”一节（确认日期、用户确认的要点），再开始第 2 步。
系列项目的每个版本都要对照 `series.md` 设计与评审（见第 3.5 节）；`[series]` 由命令写入，不要手改。
**子品牌立项时**：先 `./logo series compare <系列名>` 把系列现有成员与 `series.md` 给用户看，再问子品牌沿用哪些基因、主题色是什么，写进 brief。

用户带来**别处生成的 Logo**（其他 Agent、对话窗口、设计工具）时，按 `import-workflow` 技能导入：
新项目可用 `./logo import … --init` 以它初始化（色板等取自图）；已有项目的冲突逐项问用户改图片还是改项目规范。

## 1. 新建项目（细节）
1. 按第 0 步的命令新建；`project.toml` 的 `[series]`（所属系列与角色）与 brief 的“项目类型与架构”由命令写入。
2. 填写 `projects/<slug>/brief.md`（含字体策略，引用 fonts.toml 的 id）。
3. 在 `project.toml` 中定义 `[palette]`（系列子品牌已继承母品牌，按 series.md 调整）、按需调整 `[color_modes]` 与 `[export]`（生成目标、App 图标底色与图形配色）。
   生成哪些标志版式以发布中实际存在的文件为准；`check` 提示“旧写法”时按提示迁移（`[layouts]`、`profiles` 等已被取代）。
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

## 3.5 系列评审（系列项目）
子品牌（统一型 / 背书型）定稿前必须与用户做系列评审，否则 `./logo release` 拒绝：
1. `./logo series compare <系列名> [--with <子品牌>=<版本>]`：生成对比图（每列一个成员；彩色 / 单色 / 32px 三行），并打印评审清单。
2. **把对比图给用户看，按清单逐条提问**（有提问工具时用选择题），例如“线条与圆角：子品牌的线宽与母品牌一致吗？——一致 / 偏粗 / 偏细”；
   不要替用户下结论。
3. 记录用户结论：`./logo series review <系列名> --result pass|revise --note "<用户原话或要点>" --with <子品牌>=<版本>`
   （追加到 series.md；`--note` 必填）。需修改时按意见出新版本，再对比、再评审；以同一版本最近一次评审为准。
4. 通过后才能 `./logo release <子品牌> --from <版本>`。母品牌发布与基于发布的再发布不设门槛；独立型系列不设门槛。

## 3.6 母品牌改版后（系列项目）
母品牌出新发布后，`./logo check` 会对每个统一型 / 背书型子品牌提醒“母品牌已发布 rN”，并列出色板变化。逐个子品牌处理：
1. 把变化告诉用户（必要时 `./logo series compare` 给用户看新旧对比），按 series.md“改版跟进”的约定提问：
   默认跟随的，确认一次“全部跟随吗？”；逐项确认的，每种颜色单独问“跟随 / 保持”。图形有调整时再问“子品牌要不要随之改版？”。
2. 记录并执行：`./logo series sync <子品牌> --palette all|none|色名,色名 --note "<用户的决定>"`
   （`--note` 必填；结果追加到 series.md）。旧色值仍被子品牌已有设计使用时会自动保留为 `<色名>_r<N>`，冻结的版本与发布不受影响。
3. 需要改版时：先 `--palette none` 记下“已知悉”，再出新版本，按 3.5 节与用户重新做系列评审后发布。
4. 在项目仓库提交子品牌的 project.toml 与 series.md。

## 4. 定稿发布
1. 定稿决策：`./logo decision <slug> finalize-<字母>-vNNN`（为什么这个版本可以定稿）。
2. 创建发布：`./logo release <slug> --from <字母>-vNNN`（自动把 selected_version/direction 同步为该版本，
   并以该版本的 `logo.svg` 起稿主版 `layouts/primary.svg`，提交前可调整留白等，提交后冻结）。
   基于上一个发布修订版式：`./logo release <slug> --from rN`。
3. 放入版式（`releases/rN/layouts/`）：
   - **必需** `primary.svg`（主版，已由版本起稿）；
   - 可选 `icon.svg`（纯图形，用于小尺寸与图标类目标，缺省时用 primary）、`horizontal.svg` / `wordmark-cn.svg` / `wordmark-en.svg`（带文字的组合版）；
   - App 图标、头像、Android 图标默认由主版**自动排版**（品牌底色 `app_background` + 图形配色 `icon_mode`）；
     效果不满意时才放手工覆盖 `app-icon.svg` / `avatar.svg`（avatar 的底色元素须带 `id="background"`）；
   - 单色/反白自动换色效果不好时，可提供 `<版式>.mono.svg` / `<版式>.reversed.svg`。
4. 提交前必须：在 `release.md` 的“决策依据”中写明并引用 `decisions/NNN-*.md`（钩子校验存在）；
   填完 `guide.md`（标志结构、最小尺寸、安全留白、错误用法）——它会进入交付物，未填完时 `export` 会拒绝。
5. 预览：`./logo preview <slug> rN`（全部版式 + 自动排版的 App 图标与头像）或 `./logo preview <slug> rN/icon`。
6. `./logo index && ./logo check`，提交：`git -C projects add -A && git -C projects commit -m "[<slug>] release rN"`
7. 按需生成交付物（不提交）：
   - `./logo export <slug>`：生成项目 `[export] targets` 声明的目标（svg、favicon、ios、android、avatar）；
   - `./logo export <slug> ios avatar`：只生成点名的目标；`--list` 查看可用与已声明的目标；
   - `./logo render <slug> [版式] --mode mono --size 800`：任意版式、配色、尺寸的单张图；
   - 产物在 `projects/<slug>/dist/<发布号>/`，附 `manifest.json`（每个目标由哪个系统版本与渲染器生成、各文件 sha256）；
     需要交付时把该目录发给对方。`render` 的单张图在 `dist/render/<发布号>/`，不属于交付目录；
     app-icon / avatar 的配色由 `[export] icon_mode` 决定，不接受 `--mode`。
   - 需要标记交付时：`./logo state <slug> stage=delivered` 并提交。

## 5. 检查结果的处理
- ✗ 错误：必须修复后才能提交（钩子会拦截）。
- ⚠ 提醒：在回复中如实告诉用户，例如“选定版本尚未发布”“自动排版的图标中图形与底色几乎无法区分”“平台规格待核实”。
- “发布 rN 无法导出”：guide.md 已冻结补不上，按提示 `./logo release <slug> --from rN` 新建发布并在提交前填完。
- “没有引用决策记录”：`./logo decision` 补写决策，再在 `release.md` **末尾追加**引用（不要改动已有内容）。
- “历史中有提交违反不可变规则”：告诉用户是哪些提交；**绝不要用 `--no-verify` 绕过钩子**，被拦截就按规则新建版本/发布。
- “没有配置远程仓库 / 尚未推送”：提醒用户备份，不要自行添加远程或推送。

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
   由 HarfBuzz 排版：默认开字距（kern），连字与上下文替换（liga、clig、calt、dlig）关闭，字形所见即所得；
   需要时用 `--features` 开启，如 `--features calt,ss02` 或选择风格变体 `cv05=2`（关闭写作 `--features=-kern`，等同 `--no-kern`）；
   `--lang zh-Hans` / `zh-Hant` 选择地区字形。字体里没有的字会报错，不会转出空框。
   以 `-` 开头的值须用 `=` 连写，如 `--text=-ACME`。
   已入库：`inter`（英文）、`source-han-sans-sc-bold`（思源黑体 SC Bold，中文）。
