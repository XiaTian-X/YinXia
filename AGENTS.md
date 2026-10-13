# LOGODesign 全局规则

- **两个 Git 仓库**：仓库根目录是**系统仓库**（`tools/`、`templates/`、`config/`、`assets/`、规则文档）；`projects/` 是独立的**项目仓库**（各项目与 `PROJECTS.md`）。设计工作只提交到项目仓库：`git -C projects add -A && git -C projects commit -m "[<slug>] …"`；只有修改系统本身时才在根目录提交，且系统改动与项目改动绝不混在同一次提交里。
- **唯一操作入口**：项目创建、方向、版本、概念图、决策记录、发布、状态流转、导出，必须通过 `./logo`，绝不允许手动建文件夹。
- **状态唯一来源**：`project.toml` 的 `[state]` 只能由 `./logo state / version / release / export` 修改，禁止手改；`[palette]`、`[color_modes]`、`[fonts]`、`[layouts]`、`[export]` 属于项目配置，可以手工维护。
- **不可变性 (Immutability)**：以下文件提交后绝不允许修改或删除——
  - `directions/*/vNNN/logo.svg`：要改就用 `./logo version` 产生新版本；
  - `releases/rN/layouts/*.svg`：要改就用 `./logo release --from rN` 产生新发布；
  - `concepts/NNN-*.png|jpg|webp`：概念图原样保存。
- **只追加文件**：`notes.md`、`direction.md`、`release.md`、`guide.md`（releases 下）、`decisions/*.md`、`concepts/*.md`，已有内容不得修改，只能在末尾追加评审记录。
- **`final/` 是纯产物**：只能由 `./logo export` 生成，禁止手改或放入文件（由 `manifest.json` 校验）。
- **目录纪律**：两个仓库里都只允许出现规定结构内的文件。描摹中间件、临时脚本、测试输出一律放 `.cache/`（已被 Git 忽略）或用完删除，绝不提交到根目录或项目目录。
- **SVG 规范**：使用 viewBox；禁止 hardcoded width/height；所有文字使用 `./logo outline --project <slug>` 转曲；颜色必须来自 `project.toml` 色板的十六进制值；不准用外链/位图/脚本/事件属性（`onload` 等）/CSS 外部资源（`@import`、`url(http…)`）。
- **字体**：只用 `assets/fonts/fonts.toml` 登记过的字体；项目用到时同时写入 `[fonts] used` 与 fonts.toml 的 `used_by`。字体文件绝不提交到 Git（`.otf/.ttf` 等已忽略，钩子也会拦截），需要时用 `./logo fonts fetch` 从登记的官方地址下载并校验。
- **追溯**：设计依据的图片（含用户参考图）用 `./logo concept` 登记，版本用 `--inspired-by` 引用；选定方向、定稿等关键取舍用 `./logo decision` 记录，发布的 `release.md` 必须引用决策记录；用户的评审意见追加进对应的 `notes.md` / `release.md`。
- **文档完整**：新建文档提交前必须填完模板占位（“（…）”提示与空字段），因为提交后只能追加；`guide.md` 是交付物，未填完不能导出。
- **检查**：每次提交前 `./logo check` 必须通过（两个仓库各有 pre-commit 钩子强制执行）；提醒（⚠）要在回复中告知用户。
- **工作流引导**：处理 Logo 项目时，始终参照 `logo-workflow` 技能。
