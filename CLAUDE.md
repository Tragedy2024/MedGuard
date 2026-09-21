## Agent skills

### Issue tracker

Issue 与 spec 以 markdown 文件存放在 `.scratch/` 下（本地 markdown，未用 GitHub/GitLab）。
该目录**已被 `.gitignore` 忽略**——它是工作区，不是交付物；**目前尚无工单**。详见 `docs/agents/issue-tracker.md`。

### Triage labels

沿用五个默认 triage 角色字符串，未做重命名。详见 `docs/agents/triage-labels.md`。

### Domain docs

单 context 布局**约定**为根目录 `CONTEXT.md` + `docs/adr/`——**这两个目前都还不存在**，是
**有意的**：按 `docs/agents/domain.md`，它们由 `/domain-modeling` 在术语或决策真正敲定时
**懒创建**。缺省状态下**静默继续**，不要指出缺失、也不要预先创建。详见 `docs/agents/domain.md`。
