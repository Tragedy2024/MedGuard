# Domain Docs

探索代码库时，工程类 skill 应如何消费本仓库的领域文档。

## 探索之前，先读这些

- 仓库根目录的 **`CONTEXT.md`**，或
- 若存在，根目录的 **`CONTEXT-MAP.md`**：它指向每个 context 各自的 `CONTEXT.md`。
  读取与当前主题相关的每一份。
- **`docs/adr/`**：读取与你即将动手的区域相关的 ADR。多 context 仓库中，
  另需查看 `src/<context>/docs/adr/` 下的 context 级决策。

若这些文件不存在，**静默继续**。不要指出它们缺失，也不要建议预先创建。
`/domain-modeling` 技能（经 `/grill-with-docs` 与 `/improve-codebase-architecture` 触达）
会在术语或决策真正被敲定时按需创建它们。

## 文件结构

单 context 仓库（绝大多数仓库，**包括本仓库**）：

```
/
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-xxx.md
│   └── 0002-yyy.md
├── backend/
├── frontend/
└── demo/
```

多 context 仓库（根目录存在 `CONTEXT-MAP.md`）：

```
/
├── CONTEXT-MAP.md
├── docs/adr/                          ← 系统级决策
└── src/
    ├── ordering/
    │   ├── CONTEXT.md
    │   └── docs/adr/                  ← context 专属决策
    └── billing/
        ├── CONTEXT.md
        └── docs/adr/
```

本仓库目前为**单 context**：尚无 `CONTEXT.md`，也尚无 `docs/adr/`，等术语或决策真正
沉淀时再由 `/domain-modeling` 懒创建。

## 使用术语表中的词汇

当你的产出要命名某个领域概念时（issue 标题、重构提案、假设、测试名），
使用 `CONTEXT.md` 中定义的术语。不要漂移到术语表明确回避的同义词。

若你需要的概念还不在术语表中，这是一个信号：要么你在发明项目并不使用的语言
（重新考虑），要么确实存在一处空白（记下来，交给 `/domain-modeling`）。

## 标记 ADR 冲突

若你的产出与既有 ADR 相矛盾，明确抛出，而不是悄悄覆盖：

> _与 ADR-0007（event-sourced orders）冲突，但值得重开，因为……_
