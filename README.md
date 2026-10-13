# LOGODesign

个人 Logo 设计管理仓库：AI 直接编写 SVG，您审阅；每一次迭代、取舍和交付都可以从成品一路追溯回最初的理念。

- 项目一览见 [projects/PROJECTS.md](projects/PROJECTS.md)（自动生成）
- AI 必须遵守的规则见 [AGENTS.md](AGENTS.md)，具体操作步骤见 [logo-workflow 技能](.agents/skills/logo-workflow/SKILL.md)

## 快速开始

```bash
./logo setup
```

需要本机已安装 `resvg` 与 `magick`（Homebrew）。`setup` 会创建 `.venv`、安装 fonttools、初始化项目仓库 `projects/`，并为两个仓库启用 pre-commit 钩子。

## 两个仓库

| 仓库 | 位置 | 内容 | 提交方式 |
|---|---|---|---|
| 系统仓库 | 根目录 | `tools/`、`templates/`、`config/`、`assets/fonts/`、规则文档、`.githooks/` | `git commit …` |
| 项目仓库 | `projects/`（系统仓库忽略它） | 各项目目录、`PROJECTS.md` | `git -C projects commit …` |

系统可以单独分享或克隆；单独克隆时 `./logo check` 会跳过项目相关检查，运行 `./logo setup` 即可新建空的项目仓库。
每次导出的 `final/manifest.json` 同时记录 `git_head`（项目仓库）与 `system_head`（生成它的系统版本）。

## 目录结构

```text
projects/                     # 项目仓库根目录（独立 .git）
├── PROJECTS.md
└── <slug>/
    ├── project.toml          # 元数据、色板、当前状态（唯一事实来源）
    ├── brief.md              # 品牌理念、偏好、字体策略
    ├── concepts/             # 灵感位图 + 同名 .md（提示词、来源）
    ├── directions/           # 并行方向 A/B/C，每个版本 vNNN/{logo.svg, notes.md}
    ├── decisions/            # 关键取舍记录
    ├── releases/rN/          # 定稿发布：release.md、guide.md、layouts/*.svg
    └── final/                # 由 ./logo export 生成的交付文件（勿手改）
```

## 追溯链

```text
final/manifest.json ──release──▶ releases/rN/release.md ──基于──▶ directions/X/vNNN ──基于──▶ … ──▶ concepts/
                                         └──决策依据──▶ decisions/NNN-*.md ──▶ brief.md
```

`manifest.json` 记录了 `release`、`based_on`（直接来源）与 `design_version`（最终设计版本）以及每个文件的 sha256。

## 常用命令

| 命令 | 作用 |
|---|---|
| `./logo new <slug> --name 名称` | 新建项目 |
| `./logo concept <slug> <图片> --name x --prompt "…"` | 登记概念图 |
| `./logo direction <slug> A mountain` | 新建方向 |
| `./logo version <slug> A [--from B-v002]` | 新建版本 |
| `./logo preview <slug> A-v001` / `compare <slug>` | 预览 / 多版本对比（输出到 `.cache/`） |
| `./logo decision <slug> select-direction-a` | 新建决策记录 |
| `./logo state <slug> [key=value…]` | 查看/更新状态 |
| `./logo release <slug> --from A-v004` | 新建发布 |
| `./logo export <slug>` | 从当前发布生成 `final/` |
| `./logo outline --font inter --text "ACME" --fill "#HEX" --project <slug>` | 文字转路径 |
| `./logo check` / `index` | 规范与一致性检查 / 刷新看板 |

## 规则如何被强制执行

`./logo check` 会拦截下列问题。提交时两个仓库的钩子分别以 `--staged --scope system|projects` 运行：项目仓库的不可变规则与 SVG 规范按暂存区内容检查，其余按工作区检查；系统仓库提交时，项目仓库里的问题只提醒、不拦截。

- 已冻结文件（版本 SVG、发布版式、概念图）被修改或删除；只追加文件被改写；
- SVG 不合规（缺 viewBox、写死宽高、`<text>`/位图/脚本、外部引用、色板外颜色）；
- 状态自相矛盾（选定版本不属于选定方向、发布阶段却没有当前发布）、发布的追溯链断裂；
- `final/` 被手改或混入文件；
- 字体未登记、校验值不符、`used_by` 与项目声明不一致；
- 目录结构外的文件（临时文件请放 `.cache/`）。

同时会提醒（不拦截）：选定版本尚未发布、`final/` 与当前发布不一致、当前发布缺少导出所需版式、头像超出圆形安全区、平台导出规格未核实或缺少来源。

## 导出规格

所有尺寸在 [config/export-profiles.toml](config/export-profiles.toml)，每个平台登记尺寸、安全区/遮挡区、官方来源 `source` 与核对日期 `verified_on`；超过 12 个月未核对会提醒。尚未登记的平台列在 `[social] pending`。
