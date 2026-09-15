# 内置算法层的改动记录

本目录是从别处拷进来的第三方代码，**默认保持原样**。任何改动都必须记在这里，
避免出现"没人知道它和上游不一样"的情况。

## 来源

| 子目录 | 来源 | 说明 |
|---|---|---|
| `src/`（除 `core/`） | 论文仓库 `NL2SQL/src` | 医盾安全审计引擎（论文贡献，**一行未改**） |
| `src/core/` | `NL2SQL/vendor/MAC-SQL/core` | MAC-SQL 多智能体体系，用于自然语言 → 多步分解计划 |

## 改动清单

### 1. `src/core/agents.py` — 删除两个未使用的 import

**改动**：删掉 `import pandas as pd` 与 `import tiktoken`。

**依据**：全 vendor 树检索确认，`pd.` 一次都没出现；`tiktoken` 的唯一
出现位置是第 529 行一条**注释**（`# encoder = tiktoken.get_encoding(...)`）。
两者都是死导入。

**为什么值得改**：`pandas` 会连带 `numpy` 拖进约 30MB，`tiktoken` 另加约 2MB。
本交付包的要求是「拿到文件夹即可运行」（见 `README-dev.md`），为一个从未执行
的 import 付 32MB 依赖不划算。

**行为影响**：无。删除的是未引用符号，不改变任何运行时路径。

**上游同步提示**：将来若从 MAC-SQL 更新 `core/`，这两行会重新出现，
需要再次删除，或改用安装 pandas / tiktoken。

---

## 未改但值得知道的事

- `src/core/agents.py` 保留着 `import pdb`（调试残留）。它属 Python 内置，
  零成本，故未动。
- `src/graph/sql_generator.py` 需要 `core.llm.safe_call_llm`——本目录已补上
  `core/`，所以这个模块现在**可以导入**了（在此之前是 `ModuleNotFoundError`）。
  它是论文的图分解生成器，本产品**不使用**；我们走 MAC-SQL 体系。
