---
name: logo-workflow
description: LOGODesign 的核心操作流，包括立项、设计方向、迭代、文字转曲、生成发布版与检查导出。
---

# Logo Workflow

## 1. 新建项目
使用 `./tools/logo.py new <slug> --name <名称>`。
然后填写生成的 `brief.md`，并在 `project.toml` 中定义色板。

## 2. 探索概念 (可选)
用 `generate_image` 生成图片。然后调用 `./tools/logo.py concept <slug> <图片相对路径> --name <短名> --prompt <你的提示词>` 登记。

## 3. 设计迭代
1. 新建方向：`./tools/logo.py direction <slug> <字母> <方向名>`
2. 新建版本：`./tools/logo.py version <slug> <字母>`
3. 编写 SVG：写入 `projects/<slug>/directions/<字母>-<名>/vNNN/logo.svg`。文字必须使用 `./tools/logo.py outline` 生成 `<path>` 粘贴进来。
4. 预览与自查：运行 `./tools/logo.py preview <slug> <字母>-vNNN`。检查效果。
5. 更新日志：写入当前版本的 `notes.md`。
6. 检查并更新状态：`./tools/logo.py state <slug> selected_direction=<字母>`
7. 检查：`./tools/logo.py check`
8. 提交：`git add . && git commit -m "[<slug>] <字母>-vNNN: <说明>"`。提交后锁定。

## 4. 定稿发布
1. 选定版本：`./tools/logo.py state <slug> selected_version=A-v001`
2. 创建 Release：`./tools/logo.py release <slug> --from A-v001`
3. 补全版式：`projects/<slug>/releases/rN/layouts/` 补齐 svg。
4. 预览：`./tools/logo.py preview <slug> rN`
5. 检查并提交：`./tools/logo.py check && git add . && git commit -m "[<slug>] release rN"`
6. 导出：`./tools/logo.py export <slug>`
7. 索引：`./tools/logo.py index`

## 文字转曲
1. 查阅 `cat assets/fonts/fonts.toml`
2. 转曲：`./tools/logo.py outline --font inter --text "TEXT" --size 120 --fill "#000000"`
3. 将输出粘贴至 svg。
