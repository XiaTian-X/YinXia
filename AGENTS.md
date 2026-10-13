# LOGODesign 全局规则

- **唯一操作入口**：项目创建、方向、版本、概念图、决策记录、发布、状态流转、导出，必须通过 `./logo`，绝不允许手动建文件夹。
- **状态唯一来源**：`project.toml` 的 `[state]` 只能由 `./logo state / version / release / export` 修改，禁止手改；`[palette]`、`[color_modes]`、`[fonts]`、`[layouts]`、`[export]` 属于项目配置，可以手工维护。
- **不可变性 (Immutability)**：以下文件提交后绝不允许修改或删除——
  - `directions/*/vNNN/logo.svg`：要改就用 `./logo version` 产生新版本；
  - `releases/rN/layouts/*.svg`：要改就用 `./logo release --from rN` 产生新发布；
  - `concepts/NNN-*.png|jpg|webp`：概念图原样保存。
- **只追加文件**：`notes.md`、`direction.md`、`release.md`、`guide.md`（releases 下）、`decisions/*.md`、`concepts/*.md`，已有内容不得修改，只能在末尾追加评审记录。
- **`final/` 是纯产物**：只能由 `./logo export` 生成，禁止手改或放入文件（由 `manifest.json` 校验）。
- **目录纪律**：仓库里只允许出现规定结构内的文件。描摹中间件、临时脚本、测试输出一律放 `.cache/`（已被 Git 忽略）或用完删除，绝不提交到根目录或项目目录。
- **SVG 规范**：使用 viewBox；禁止 hardcoded width/height；所有文字使用 `./logo outline --project <slug>` 转曲；颜色必须来自 `project.toml` 色板的十六进制值；不准用外链/位图/脚本。
- **字体**：只用 `assets/fonts/fonts.toml` 登记过的字体；项目用到时同时写入 `[fonts] used` 与 fonts.toml 的 `used_by`。
- **追溯**：选定方向、定稿等关键取舍用 `./logo decision` 记录；用户的评审意见追加进对应的 `notes.md` / `release.md`。
- **检查**：每次提交前 `./logo check` 必须通过（pre-commit 钩子强制执行）；提醒（⚠）要在回复中告知用户。
- **工作流引导**：处理 Logo 项目时，始终参照 `logo-workflow` 技能。
