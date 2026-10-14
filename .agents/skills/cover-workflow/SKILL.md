---
name: cover-workflow
description: LOGODesign 的封面（品牌物料）制作流程：立项、素材登记、主视觉与多画幅延展、校对、上线记录。封面与 Logo 资产分开管理，Logo 流程见 logo-workflow。
---

# Cover Workflow

> 封面是含 Logo 的品牌物料（构图、文案、图像），生成逻辑与 Logo 资产不同。
> 封面放在项目仓库 `projects/<slug>/covers/NNN-名称/`，设计稿可以修改（靠 Git 历史追溯）。
> 平台与画幅登记见 `./logo cover platforms`（配置 `config/cover-platforms.toml`）。

## 1. 立项
1. 确认项目已有 Logo 发布（封面里的 Logo 只能来自发布）。
2. `./logo cover new <slug> <英文短名> --platforms youtube,x,xiaohongshu --title "中文标题" [--release rN]`
   - 按目标平台推出需要的画幅，每种画幅生成一份起稿 `<画幅>.svg`；
   - 生成 `cover.toml`（元数据，由命令维护，勿手改）与 `cover.md`（需求简报）。
3. **提交前填完 `cover.md`**：用途与背景、主信息（标题 / 副标题 / 行动号召）、视觉方向。提交后只能追加。

## 2. 收集素材
- 照片、插画：`./logo cover asset <slug> <封面编号> <图片> --name <短名> --source "<来源>" --license "<授权>"`，
  填完同名 `.md` 中的“用途与授权范围”。**未登记的图不能引用**；登记后不可替换，换图要重新登记。
- 设计稿中用 `<image href="assets/NNN-名称.png" …/>` 引用。
- 标题等文字：`./logo outline --font <id> --text "…" --fill "#HEX" --project <slug>` 转曲后粘贴。

## 3. 主视觉与多画幅延展
起稿 SVG 里有三个有含义的分组：
- `<g id="logo">`：从发布嵌入的 Logo。**只能移动、缩放**（改 `transform`），不能改内部；
- `<g id="content">`：标题、副标题、行动号召等**关键内容**，必须在共同安全区内；
- `<g id="guides">`：参考线（各平台裁切窗口、安全区、遮挡区、粗绿框的共同安全区），导出时去掉，可写文字标注。

做法：先在最重要的画幅定稿主视觉，再**改排**到其他画幅（不是简单缩放）。
设计规则：颜色不限于色板；可用已登记的位图；文字须转曲；禁止脚本、事件属性与外部资源；`viewBox` 固定为画幅画布。

## 4. 检查与提交
- `./logo index && ./logo check`，然后 `git -C projects add -A && git -C projects commit -m "[<slug>] cover NNN: <说明>"`。
- 会被拦截：Logo 被改动、引用未登记或被替换的素材、文字未转曲、画布尺寸不对、缺少目标平台需要的画幅稿、简报未填完。

## 5. 上线与更替
- `./logo cover status <slug> <封面编号> live --on x,youtube`（默认全部目标平台）；下线用 `archived`，草稿用 `draft`。
- `./logo cover list <slug>` 查看项目的封面、状态与上线平台。
