"""pytest 公共配置：把项目根加入 sys.path，测试无需安装包即可运行。

**测试隔离（2026-09 代码评审）**：在任何 backend 模块被导入之前强制打开
`MEDGUARD_TESTING`——config.py 据此清空 LLM 配置并关闭 BGE 语义检索，
保证无论机器上是否存在真实 `.env`，`pytest` **永不联网**、结果确定可复现。
"""
import os
import sys

os.environ["MEDGUARD_TESTING"] = "1"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (_ROOT, os.path.join(_ROOT, "backend")):
    if p not in sys.path:
        sys.path.insert(0, p)


# ── 问答缓存隔离（共享夹具：两个测试文件共用同一套隔离逻辑）────────
# 预置缓存默认指向 demo/query_cache.json（演示要预热的真文件，只读）；
# 学习缓存默认指向 data/（运行时）。测试把两个都指到临时目录，
# 写任何东西都不污染演示内容与 git 工作区。
import pytest  # noqa: E402


def _patch_cache_paths(tmp_path, monkeypatch) -> str:
    from backend import config
    preset = str(tmp_path / "preset_cache.json")
    learned = str(tmp_path / "learned_cache.json")
    monkeypatch.setattr(config, "PRESET_QUERY_CACHE", preset)
    monkeypatch.setattr(config, "LEARNED_QUERY_CACHE", learned)
    return learned


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    """查询路由测试用：缓存指到临时目录。"""
    return _patch_cache_paths(tmp_path, monkeypatch)


@pytest.fixture
def cache_file(tmp_path, monkeypatch):
    """缓存模块测试用：缓存指到临时目录，并把容量上限压到 3（容量/淘汰用例）。"""
    learned = _patch_cache_paths(tmp_path, monkeypatch)
    from backend import config
    monkeypatch.setattr(config, "QUERY_CACHE_PER_TOKEN", 3)
    return learned