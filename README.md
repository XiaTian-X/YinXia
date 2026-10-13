# LOGODesign

个人 Logo 设计管理仓库：AI 直接编写 SVG，您审阅；每一次迭代、取舍和交付都可以从成品一路追溯回最初的理念。

- 项目一览见 [projects/PROJECTS.md](projects/PROJECTS.md)（自动生成）
- AI 必须遵守的规则见 [AGENTS.md](AGENTS.md)，具体操作步骤见 [logo-workflow 技能](.agents/skills/logo-workflow/SKILL.md)

## 快速开始

```bash
./logo setup
```

需要本机已安装 `resvg` 与 `magick`（Homebrew）。`setup` 会创建 `.venv`、安装 fonttools、初始化项目仓库 `projects/`，并为两个仓库启用 pre-commit 钩子。

字体文件不进 Git。需要文字转曲时运行：

```bash
./logo fonts fetch
```

它按 [assets/fonts/fonts.toml](assets/fonts/fonts.toml) 中登记的官方地址下载（压缩包取 `archive_member`），sha256 与登记一致才落盘；`./logo fonts` 查看状态。

## 两个仓库

| 仓库 | 位置 | 内容 | 提交方式 |
|---|---|---|---|
| 系统仓库 | 根目录 | `tools/`、`templates/`、`config/`、字体登记与授权（`assets/fonts/`，不含字体文件）、规则文档、`.githooks/` | `git commit …` |
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
| `./logo version <slug> A [--from B-v002] [--inspired-by 001]` | 新建版本（可引用概念图） |
| `./logo preview <slug> A-v001` / `compare <slug>` | 预览 / 多版本对比（输出到 `.cache/`） |
| `./logo decision <slug> select-direction-a` | 新建决策记录 |
| `./logo state <slug> [key=value…]` | 查看/更新状态 |
| `./logo release <slug> --from A-v004` | 新建发布 |
| `./logo export <slug>` | 从当前发布生成 `final/` |
| `./logo outline --font inter --text "ACME" --fill "#HEX" --project <slug>` | 文字转路径（默认应用字距调整，`--no-kern` 关闭） |
| `./logo fonts [fetch] [id…]` | 查看字体状态 / 从官方地址下载并校验 |
| `./logo check` / `index` | 规范与一致性检查 / 刷新看板 |
| `./logo selftest [测试名]` | 运行系统自动化测试 |

## 规则如何被强制执行

`./logo check` 会拦截下列问题。提交时两个仓库的钩子分别以 `--staged --scope system|projects` 运行：项目仓库的不可变规则与 SVG 规范按暂存区内容检查，其余按工作区检查；系统仓库提交时，项目仓库里的问题只提醒、不拦截。

- 已冻结文件（版本 SVG、发布版式、概念图）被修改或删除；只追加文件被改写；
- SVG 不合规（缺 viewBox、写死宽高、`<text>`/位图/脚本/事件属性、外部引用与 CSS 外部资源、色板外颜色）；
- 新建文档仍留有模板占位；发布未引用决策记录；版本引用了未登记的概念图；
- 状态自相矛盾（选定版本不属于选定方向、发布阶段却没有当前发布）、发布的追溯链断裂；
- `final/` 被手改或混入文件；
- 字体未登记、校验值不符、`used_by` 与项目声明不一致；字体文件被提交到系统仓库；
- 目录结构外的文件（临时文件请放 `.cache/`）；系统仓库按目录和文件类型精确限定（如 `tools/` 只收 `*.py`）；
- 疑似密钥：文件名（`.env`、`*.pem`、`*.key`、`id_rsa`、`*credential*` 等）或内容（私钥、AWS/GitHub/Anthropic/OpenAI/Slack/Google 令牌）。

系统仓库的 `.gitignore` 采用白名单：根目录默认忽略一切，只放行系统文件；放行的目录内仍忽略临时文件、日志、编辑器杂物、密钥与字体文件。
项目仓库的 `.gitignore` 同样是白名单，逐层只放行规定的项目文件（由 `./logo setup` 从 `templates/projects.gitignore` 生成，与模板不一致时 `check` 会提醒）。

同时会提醒（不拦截）：选定版本尚未发布、`final/` 与当前发布不一致、当前发布缺少导出所需版式、头像超出圆形安全区、Android 图标超出 66dp 安全区、单色/反白版丢失内部细节（糊成一块）、brief.md 未填完、平台导出规格未核实或缺少来源。

`./logo export` 先在临时目录生成全部文件，成功后才整体替换 `final/`；发布的 `guide.md` 未填完时拒绝导出。

## 导出规格

所有尺寸在 [config/export-profiles.toml](config/export-profiles.toml)。每个平台登记尺寸、使用的版式、核对日期，并用 `basis` 标明依据：

| basis | 含义 | 要求 |
|---|---|---|
| `official`（默认） | 已按官方文档核实 | 必须有 `source` 官方链接 |
| `common` | 平台无官方规格页，采用业内通用规格 | 必须在 `verify_note` 写明依据 |
| `generic` | 连通用规格也没有，按通用比例导出 | 必须在 `verify_note` 说明 |

任何规格超过 12 个月未复核都会提醒。封面导出时按版式铺满后居中裁切，所以一个版式可服务多个相近比例的平台。

目前登记 17 个平台：YouTube、X、LinkedIn、Open Graph、GitHub（官方）；Facebook、小红书、抖音、微信公众号首条/次条、微博、视频号、快手（通用）；B 站空间头图、知乎主页背景（泛用）；另有 4:3、3:4 通用比例。

## 自动化测试

修改系统（`tools/`、`templates/`、`config/`、钩子）后、提交前运行：

```bash
./logo selftest
```

测试在临时目录里为每个用例搭建隔离的工作区（系统仓库 + 项目仓库，Git 配置与个人全局配置隔离），经由真实的 `./logo` 命令与提交钩子验证；不访问网络、不触碰真实仓库。`./logo selftest test_unit` 只跑毫秒级的单元测试。缺少 `resvg`/`magick` 时导出测试自动跳过，未下载 Inter 时字距测试自动跳过。
