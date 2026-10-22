# LOGODesign

个人 Logo 设计管理仓库：AI 直接编写 SVG，您审阅；每一次迭代、取舍和交付都可以从成品一路追溯回最初的理念。

- 项目一览见 [projects/PROJECTS.md](projects/PROJECTS.md)（自动生成）
- AI 必须遵守的规则见 [AGENTS.md](AGENTS.md)，具体操作步骤见 [logo-workflow 技能](.agents/skills/logo-workflow/SKILL.md)

## 快速开始

```bash
./logo setup
```

需要本机已安装 `resvg` 与 `magick`（Homebrew）。`setup` 会创建 `.venv`、安装 fonttools 与 uharfbuzz（HarfBuzz 排版）、初始化项目仓库 `projects/`，并为两个仓库启用 pre-commit 钩子。

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
每次导出的 `dist/<发布号>/manifest.json` 为每个目标记录 `git_head`（项目仓库）、`system_head`（系统版本）与 `renderer`（resvg 版本）。

## 目录结构

```text
projects/                     # 项目仓库根目录（独立 .git）
├── PROJECTS.md                # 看板：独立项目与各系列分组
├── _series/<系列名>/          # 品牌系列：series.toml（名称、架构、封面骨架）、series.md（系列规则，只追加）、covers/（家族封面）
└── <slug>/
    ├── project.toml          # 元数据、色板、当前状态（唯一事实来源）
    ├── brief.md              # 品牌理念、偏好、字体策略
    ├── concepts/             # 灵感位图 + 同名 .md（提示词、来源）
    ├── directions/           # 并行方向 A/B/C，每个版本 vNNN/{logo.svg, notes.md}
    ├── decisions/            # 关键取舍记录
    ├── releases/rN/          # 定稿发布：release.md、guide.md、layouts/（必需 primary.svg）
    ├── covers/NNN-名称/      # 封面：cover.toml、cover.md、assets/、每种画幅一份设计稿
    └── dist/                 # ./logo export / render 的按需产物（被忽略，不提交）
```

## 追溯链

```text
dist/rN/manifest.json ──release──▶ releases/rN/release.md ──基于──▶ directions/X/vNNN ──基于──▶ … ──▶ concepts/
                                         └──决策依据──▶ decisions/NNN-*.md ──▶ brief.md
```

`manifest.json` 记录了 `release`、`based_on`（直接来源）与 `design_version`（最终设计版本）、每个目标的生成版本以及每个交付文件的 sha256。

## 常用命令

| 命令 | 作用 |
|---|---|
| `./logo status [项目] [--json]` | 各项目进度与待办（访谈、未提交、设计、系列、交付、封面、收件箱），新会话先看 |
| `./logo new <slug> --name 名称 [--series 系列 --role master\|sub] [--targets svg,ios]` | 按需求访谈结果新建独立或系列项目 |
| `./logo inbox [--json\|--spec]` | 查看收件箱 `inbox/`（外部设计的待导入文件，不进任何仓库） |
| `./logo handoff brief <项目> [--plain]` / `skill <目录>` | 给其他 Agent 的说明书（需求、色板、SVG 要求、交付格式）/ 通用 skill |
| `./logo handoff receive <文本> --project <项目>` / `validate` / `feedback` | 交接文本拆进收件箱（检测截断）/ 只读自查 / 生成给对方的修改意见 |
| `./logo import <文件> --project <项目> … --analyze` | 导入外部生成的 Logo：先预演（对比图与冲突报告），冲突逐项决定后再正式导入 |
| `./logo series [new\|add]` | 查看系列与成员 / 新建系列（统一型、背书型、独立型）/ 已有项目加入系列 |
| `./logo series compare <系列> [--with 项目=版本]` | 系列对比图（彩色 / 单色 / 32px）与评审清单 |
| `./logo series review <系列> --result pass\|revise --note "用户意见"` | 记录系列评审结论（子品牌发布前须通过） |
| `./logo series sync <子品牌> --palette all\|none\|色名 --note "用户决定"` | 子品牌跟进母品牌新发布（同步色板，旧值保留给冻结的设计） |
| `./logo series skeleton <系列> --from <项目> <封面> --note "…"` | 登记系列封面骨架；成员立项封面时 `--from-skeleton` 套用 |
| `./logo cover new series:<系列> <名称> --platforms … --members 主,次` | 家族封面：同时展示多个成员的 Logo（其余封面命令同样接受 `series:<系列>`） |
| `./logo concept <slug> <图片> --name x --prompt "…"` | 登记概念图 |
| `./logo direction <slug> A mountain` | 新建方向 |
| `./logo version <slug> A [--from B-v002 \| --from-master] [--inspired-by 001]` | 新建版本（可引用概念图；系列子品牌可从母品牌主版起稿） |
| `./logo preview <slug> A-v001` / `compare <slug>` | 预览 / 多版本对比（输出到 `.cache/`） |
| `./logo decision <slug> select-direction-a` | 新建决策记录 |
| `./logo state <slug> [key=value…]` | 查看/更新状态 |
| `./logo release <slug> --from A-v004` | 新建发布（以该版本起稿主版 `primary.svg`） |
| `./logo export <slug> [目标…]` | 按需生成 svg / favicon / ios / android / avatar 到 `dist/`（不提交） |
| `./logo render <slug> [版式] --mode mono --size 800` | 生成任意版式、配色、尺寸的单张图（到 `dist/render/<发布号>/`） |
| `./logo outline --font inter --text "ACME" --fill "#HEX" --project <slug> [--features calt,ss02] [--lang zh-Hans]` | 文字转路径：HarfBuzz 排版，默认只开字距，连字与上下文替换按需开启 |
| `./logo fonts [fetch\|review] [id…]` | 查看字体状态与使用方 / 下载并校验 / 为非开源字体创建授权复核 |
| `./logo check` / `index` | 规范与一致性检查 / 刷新看板 |
| `./logo cover new / asset / status / list` | 封面立项起稿 / 登记素材 / 记录上线 / 查看项目封面 |
| `./logo cover preview / export <slug> <封面> [平台…]` | 封面校对图 / 按平台导出 PNG（不提交） |
| `./logo cover import <项目\|series:系列> <图片> --contains-logo no --contains-text … --analyze` | 外部封面图作底图导入（Logo 由系统嵌入；比例不符逐画幅决定裁切或留边） |
| `./logo cover retarget <slug> <封面> --platforms a,b` | 调整封面的目标平台（新画幅起稿、参考线随之更新） |
| `./logo cover sync-logo <slug> <封面> [--release rN]` | 把封面中的 Logo 换成新发布（保留位置与显示高度） |
| `./logo cover platforms [--ratio 3x1]` | 查看封面平台、画幅与裁切比例 |
| `./logo selftest [测试名]` | 运行系统自动化测试 |

## 规则如何被强制执行

`./logo check` 会拦截下列问题。提交时两个仓库的钩子分别以 `--staged --scope system|projects` 运行：项目仓库的不可变规则与 SVG 规范按暂存区内容检查，其余按工作区检查；系统仓库提交时，项目仓库里的问题只提醒、不拦截。

- 已冻结文件（版本 SVG、发布版式、概念图）被修改或删除；只追加文件被改写；
- SVG 不合规（缺 viewBox、写死宽高、`<text>`/位图/脚本/事件属性、外部引用与 CSS 外部资源、色板外颜色）；
- 新建文档仍留有模板占位；发布未引用决策记录；版本引用了未登记的概念图；
- 状态自相矛盾（选定版本不属于选定方向、发布阶段却没有当前发布）、发布的追溯链断裂；
- 需求访谈未完成（brief.md 未填完，系列成员还要求 series.md 填完）就开始设计（`./logo direction` 拒绝）；登记的封面骨架已不存在或其项目已离开系列；系列关系不一致（系列不存在、角色非法、一个系列多个母品牌、继承型子品牌缺母品牌或其发布）；子品牌未通过系列评审就发布；系列规则 series.md 未填完或被改写；
- 发布缺少主版 `primary.svg`；新放入发布的文件不是系统认识的版式名（如拼错的 `primay.svg`、旧式 `cover-*.svg`，否则生成时会被静默忽略）；`final/` 等生成物被提交；
- 字体未登记、校验值不符；SVG 中转曲用到的字体未在项目 `[fonts] used` 声明；非开源字体缺少或未通过授权复核（`LICENSE-REVIEW.md`）；字体文件被提交到系统仓库；
- 目录结构外的文件（临时文件请放 `.cache/`）；系统仓库按目录和文件类型精确限定（如 `tools/` 只收 `*.py`）；
- 封面：Logo 被改动（只能移动缩放）、引用未登记或被替换的素材、文字未转曲、画布尺寸不对、缺少目标平台需要的画幅稿、画幅名未登记；封面目录里每一份设计稿都按规则检查；
- 疑似密钥：文件名（`.env`、`*.pem`、`*.key`、`id_rsa`、`*credential*` 等）或内容（私钥、AWS/GitHub/Anthropic/OpenAI/Slack/Google 令牌）。

系统仓库的 `.gitignore` 采用白名单：根目录默认忽略一切，只放行系统文件；放行的目录内仍忽略临时文件、日志、编辑器杂物、密钥与字体文件。
项目仓库的 `.gitignore` 同样是白名单，逐层只放行规定的项目文件（由 `./logo setup` 从 `templates/projects.gitignore` 生成，与模板不一致时 `check` 会提醒）。

同时会提醒（不拦截）：选定版本尚未发布、手工头像超出圆形安全区、Android 图标超出 66dp 安全区、手工 App 图标含透明区域、自动排版的图标中图形与底色几乎无法区分、单色/反白版丢失内部细节（糊成一块）、brief.md 未填完、平台导出规格未核实或缺少来源、`project.toml` 使用了旧写法。

已提交的数据钩子不会再看，以下由手动运行 `./logo check` 提醒：

- **当前发布能否交付**：`guide.md` 已提交却留有模板占位（导出会拒绝，只能新建发布）；`release.md` 没有引用决策记录（可补写决策后在末尾追加引用）；发布里有系统不认识的版式文件（`release --from rN` 不会继承它们）；
- **历史完整性**：扫描项目仓库全部历史，找出用 `--no-verify` 绕过钩子、修改了冻结文件或改写了只追加文件的提交（结果按 HEAD 缓存）；
- **备份**：两个仓库没有远程、没有上游或有未推送的提交（只读本地记录，不访问网络）。`amend`/`rebase` 改写历史无法在本地察觉，需要远程仓库保护分支。

这几项只在手动检查时运行，不拖慢提交。

`./logo export` 先在临时目录生成点名的目标，全部成功后才逐个替换 `dist/` 中对应目录（其他目标保留）；只用 resvg 渲染，同一发布再导出逐字节相同；发布的 `guide.md` 未填完时拒绝导出。

## 需求访谈与品牌系列

AI Agent 生成 Logo 之前，先按 [logo-workflow 技能](.agents/skills/logo-workflow/SKILL.md)第 0 步向使用者提问：独立 Logo 还是品牌系列、系列架构与角色、品牌、视觉偏好、使用场景，最后复述需求请用户确认。答案决定建出什么样的项目：

| 需求 | 命令 | 结果 |
|---|---|---|
| 独立 Logo | `./logo new <slug> --targets …` | 普通项目 |
| 新系列 | `./logo series new <系列名> --architecture …` | `_series/<系列名>/`：系列规则模板随架构不同 |
| 母品牌 | `./logo new <slug> --series <系列名> --role master` | 项目登记为系列母品牌 |
| 统一型 / 背书型子品牌 | `./logo new <slug> --series <系列名> --role sub` | 须母品牌已发布；继承其色板、配色模式与 App 图标配色，记录所依据的发布；统一型可 `version --from-master` 以母品牌主版起稿 |
| 独立型子品牌 | 同上 | 只登记归属，不继承 |

三种架构：**统一型**（子品牌 = 母品牌标志 + 名称，如 Google 地图）、**背书型**（子品牌有自己的标志并注明母品牌出品，如 Courtyard by Marriott）、**独立型**（各自独立，只归组管理，如宝洁旗下各品牌）。
brief.md 记录访谈结果（含“需求确认”），未填完时不能开始设计；看板按系列分组显示，子品牌落后于母品牌当前发布时标出。

**系列评审**：统一型 / 背书型子品牌定稿前，Agent 用 `series compare` 生成对比图并按 series.md 逐条向用户提问，用 `series review --note` 记录用户结论；没有该版本通过的评审时 `./logo release` 拒绝（以同一版本最近一次评审为准）。

**改版跟进**：母品牌出新发布后，`check` 对每个统一型 / 背书型子品牌提醒并列出色板变化；Agent 按 series.md“改版跟进”的约定逐个向用户确认，用 `series sync --note` 记录并同步。被替换的旧色值若仍被子品牌已有设计使用，会保留为 `<色名>_r<N>`，冻结的版本与发布照常通过检查。

**系列封面骨架**：把一张成员封面登记为骨架后，成员立项封面须明确选择 `--from-skeleton` 或 `--no-skeleton`。套用时 Logo 换成本项目发布（位置不变）、照片换成“图片区”参考框（素材授权按封面登记）、参考线按本封面平台重画；内容仍是骨架原文、或骨架之后被修改时会提醒。

**家族封面**：放在 `projects/_series/<系列>/covers/`，每个成员一个 `<g id="logo-<项目>">`，逐个按该项目的发布核对指纹、提醒越界与过时，`cover sync-logo series:<系列>` 逐个成员更新；第一个成员为主品牌（更大、底色取它）。

## 导入外部设计

其他 Agent、对话窗口或设计工具生成的 Logo 通过 `./logo import` 加入（流程见 [import-workflow 技能](.agents/skills/import-workflow/SKILL.md)）：

- **入口**：文件路径（拖进对话框），或系统根目录的收件箱 `inbox/`、`inbox/<项目>/`（不进任何仓库；可附来源说明 `<文件>.import.toml`，只作资料——其中的使用条款与候选/成品只是来源方声明，须由用户确认后用 `--terms` / `--kind` 写明）。
- **默认原样保留**：外观不变的格式整理（去写死的宽高、编辑器元数据、CSS 类展开、颜色写法统一）须逐像素核对一致才采用；不需要整理的文件逐字保留。
- **冲突逐项由用户决定**：色板外的颜色——改图片（`--resolve "#RRGGBB=map:色名"`）或改项目规范（`--resolve "#RRGGBB=palette:新色名"`，自动写决策记录；系列子品牌还要选择例外或更新系列规则）。
  系统硬性规则（脚本、外链、内嵌位图、未转曲文字、DOCTYPE/ENTITY）只能 `--as-concept` 登记为概念图或请来源修正。
- **先预演**：`--analyze` 生成“原件 / 导入结果”对比图；参数与文件和最后一次预演一致才能正式导入。
- **初始化**：`--init` 以导入的图初始化尚未开始设计的项目（色板取自图中颜色）。
- **追溯**：原件渲染存档为概念图（记下 sha256、来源、使用条款，原件留在本机 `.cache/imported/`）；位图去掉 EXIF / GPS 等元数据，只作设计依据。
  使用条款“不清楚”的导入版本被发布时 `check` 会提醒；收件箱里放了 7 天以上的文件也会提醒。

**交接**：让对方一开始就按规范交付，导入时几乎不用改。`./logo handoff brief <项目>` 生成说明书（能写文件的 Agent 直接放进收件箱；
`--plain` 给只能对话的窗口，回复用交接文本：每个文件一段，“=== LOGODESIGN 交接 v1 · 文件 n/N ===”开头、“=== 结束 n/N ===”结尾，缺段或截断时不写入任何文件）；
封面用 `--type cover --platforms …`：只要底图（各画幅尺寸、安全区、遮挡区，不画 Logo），收到后用 `./logo cover import` 作底图导入；
`handoff receive` 把交接文本原样拆进收件箱，之后照常预演、确认、导入；`handoff validate` 供对方只读自查；`handoff feedback` 把硬性问题（以及用户决定让对方改的颜色）写成对方能照做的修改意见。

## Logo 资产与封面

**Logo 资产**由发布中的主版 SVG 按规则推导，配置在 [config/logo-targets.toml](config/logo-targets.toml)：

| 目标 | 产物 | 来源 |
|---|---|---|
| `svg` | 各标志版式 × 彩色/单色/反白 | 按色板换色（可用 `<版式>.mono.svg` 等手工覆盖） |
| `favicon` | 16/32/48 PNG、`.ico`、180 Apple 触控图标 | `icon`（缺省 `primary`）；触控图标同 App 图标排版 |
| `ios` | 1024 不透明 | 自动排版：`app_background` 铺满，图形占 64%（或 `app-icon.svg`） |
| `android` | 5 种密度、Play 512、自适应背景/前景/单色层 | 同上；前景层按 66dp 安全区 |
| `avatar` | 400/800/1080 | 自动排版：图形缩进圆形安全区，占 56%（或 `avatar.svg`） |

规格用 `basis` 标明依据：`official`（须有官方 `source`）、`common`（业内通用，须写 `verify_note`）、`generic`（泛用比例，须写理由）；超过 12 个月未复核会提醒。

**封面**是含 Logo 的品牌物料（构图、文案、图像），生成逻辑与 Logo 资产不同，由独立的封面模块 [tools/covers.py](tools/covers.py) 管理，配置在 [config/cover-platforms.toml](config/cover-platforms.toml)：

- **画幅**（`[ratios]`）：16:9、3:1、2.7:1、6:1、1.91:1、4:3、3:4、9:16、1:1，每种一块设计画布，同画幅的平台共用一份设计稿；
- **平台**（`[[platform]]`，15 个）：所属画幅、尺寸、安全区/遮挡区与规格依据，导出时从画布居中裁切到平台尺寸。

`./logo cover platforms [--ratio 3x1]` 查看平台、尺寸与从画布裁切的比例；配置写错时 `check` 报错。

封面项目放在 `projects/<slug>/covers/NNN-名称/`（`cover.toml` 元数据、`cover.md` 简报、`assets/` 已登记素材、每种画幅一份设计稿），流程见 [cover-workflow 技能](.agents/skills/cover-workflow/SKILL.md)：`cover new` 立项起稿、`cover asset` 登记素材、`cover retarget` 调整目标平台、`cover status` 记录上线、`cover list` 查看。设计稿中的 Logo 只能移动缩放（按发布比对指纹），关键内容放在 `<g id="content">`。`cover preview` 出校对图（各平台裁切叠遮挡区/安全区、手机缩略图），`cover export` 按平台尺寸导出 PNG 到 `dist/covers/`（不提交、结果可重复，manifest 逐张记录由哪份设计稿导出，过期的会提醒）。校对时会提醒关键内容越界（被裁、超出安全区或被头像遮挡）、Logo 在手机上过小、Logo 过时（上线中的封面优先）；Logo 改版后用 `cover sync-logo` 一键替换。

## 自动化测试

修改系统（`tools/`、`templates/`、`config/`、钩子）后、提交前运行：

```bash
./logo selftest
```

测试在临时目录里为每个用例搭建隔离的工作区（系统仓库 + 项目仓库，Git 配置与个人全局配置隔离），经由真实的 `./logo` 命令与提交钩子验证；不访问网络、不触碰真实仓库。`./logo selftest test_unit` 只跑毫秒级的单元测试。

默认多进程并行（进程数取 CPU 核心数，最多 8；`-j N` 指定，`-j 1` 按原样串行运行，便于排查）：共享夹具（`setUpClass`）的测试类整类在同一进程，其余逐个分配；按 `.cache/selftest-times.json` 记录的上次耗时“最长任务优先”分配，使各进程同时结束。汇总时核对测试数，工作进程异常退出时其测试记为错误，不会漏报。每个进程只完整搭建一次工作区模板，之后的工作区直接复制模板。缺少 `resvg`/`magick` 时导出测试自动跳过，未下载 Inter / 思源黑体时转曲与排版测试自动跳过。
