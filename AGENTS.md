# LOGODesign 全局规则

- **唯一操作入口**：所有的项目创建、版本更新、状态流转、导出，必须通过 `./logo`，绝不允许手动建文件夹或手写 `project.toml`。
- **不可变性 (Immutability)**：提交 Git 后，`logo.svg` 绝不允许修改。必须使用 `./logo version` 产生新版本。
- **文件追加**：对于 `notes.md`, `release.md`, `direction.md`, `decision.md`, `concept.md`，已有内容不得修改，只能在末尾追加评审记录。
- **SVG 规范**：使用 viewBox；禁止 hardcoded width/height；所有文字使用 `./logo outline` 转曲；颜色必须来自于 `project.toml` 色板对应的十六进制值；不准用外链/图片。
- **工作流引导**：处理 Logo 项目时，始终参照 `logo-workflow` 技能。
