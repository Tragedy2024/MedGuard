# Issue tracker: 本地 Markdown

本仓库的 issue 与 spec 以 markdown 文件形式存放在 `.scratch/` 下。

之所以用本地 markdown 而非 GitHub/GitLab：本仓库没有 remote，`gh` CLI 未安装，且
`docs/superpowers/plans/2026-09-10-team-conventions.md` §4.2 已明确"不强制 PR"。

## 约定

- 一个功能一个目录：`.scratch/<feature-slug>/`
- spec 为 `.scratch/<feature-slug>/spec.md`
- 实现类工单每个一份文件：`.scratch/<feature-slug>/issues/<NN>-<slug>.md`，从 `01` 开始编号，
  **不要**合并成单个 tickets 文件
- triage 状态记录在每个工单文件靠顶部的 `Status:` 行（角色字符串见 `triage-labels.md`）
- 评论与会话历史追加到文件底部的 `## Comments` 标题下

## 当某个 skill 说 "publish to the issue tracker"

在 `.scratch/<feature-slug>/` 下新建文件（目录不存在则一并创建）。

## 当某个 skill 说 "fetch the relevant ticket"

读取所引用路径的文件。用户通常会直接给出路径或工单编号。

## Wayfinding 操作

供 `/wayfinder` 使用。**map** 是一个文件，每个工单对应一个 **child** 文件。

- **Map**：`.scratch/<effort>/map.md`（承载 Notes / Decisions-so-far / Fog 正文）。
- **Child ticket**：`.scratch/<effort>/issues/NN-<slug>.md`，从 `01` 编号，正文写问题。
  `Type:` 行记录工单类型（`research` / `prototype` / `grilling` / `task`）；
  `Status:` 行记录 `claimed` / `resolved`。
- **Blocking**：靠顶部的 `Blocked by: NN, NN` 行。当它列出的每个文件都是 `resolved` 时，
  该工单视为解除阻塞。
- **Frontier**：扫描 `.scratch/<effort>/issues/`，找出打开、未被阻塞、未被认领的文件；
  编号最小者优先。
- **Claim**：开始任何工作前，先置 `Status: claimed` 并保存。
- **Resolve**：在 `## Answer` 标题下追加答案，置 `Status: resolved`，
  然后把上下文指针（要点 + 链接）追加到 `map.md` 的 Decisions-so-far。
