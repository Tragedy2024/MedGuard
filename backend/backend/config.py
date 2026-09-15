"""运行时路径配置 —— 全后端唯一的路径/开关定义处。

设计要点（可扩展性）：
- 所有路径可用环境变量覆盖，测试用 monkeypatch.setenv 即可注入临时目录。
- 算法层（NL2SQL）路径：优先环境变量 MEDGUARD_NL2SQL_SRC，其次相对探测
  （默认与 MedGuard 同级的 NL2SQL 仓库），最后回退到已安装的包。
  仓库挪位置、或接入真实数据源时，只需改环境变量，不动代码。
"""
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 运行时数据目录（gitignore）：平台元数据库与演示业务库都放这里
DATA_DIR = os.environ.get(
    "MEDGUARD_DATA_DIR", os.path.join(_PROJECT_ROOT, "data")
)
METADATA_DB = os.environ.get("MEDGUARD_METADATA_DB",
                             os.path.join(DATA_DIR, "medguard.db"))
BUSINESS_DB = os.environ.get("MEDGUARD_BUSINESS_DB",
                             os.path.join(DATA_DIR, "regional_health.db"))

# 演示库（seed 与策略的静态资源）
DEMO_DIR = os.path.join(_PROJECT_ROOT, "demo")
SSA_DIR = os.path.join(DEMO_DIR, "ssa")
QUERIES_FILE = os.path.join(DEMO_DIR, "queries.json")

# 算法层（医盾引擎 = NL2SQL 论文仓库）的源码目录。
# 优先环境变量；否则探测多个相对位置，兼容两种仓库布局：
#   A) MedGuard 与 NL2SQL 同级：         <root>/AIC/MedGuard + <root>/AIC/NL2SQL
#   B) 交付版「后端代码」位于 <root>/后端代码（../AIC/NL2SQL）
_ALGO_CANDIDATES = [
    os.environ.get("MEDGUARD_NL2SQL_SRC", ""),
    # 交付版内置算法层（自包含，拿到文件夹即可运行）
    os.path.join(_PROJECT_ROOT, "vendor", "nl2sql", "src"),
    os.path.join(os.path.dirname(_PROJECT_ROOT), "NL2SQL", "src"),
    os.path.join(_PROJECT_ROOT, "..", "NL2SQL", "src"),
    os.path.join(os.path.dirname(_PROJECT_ROOT), "AIC", "NL2SQL", "src"),
    os.path.join(_PROJECT_ROOT, "..", "..", "AIC", "NL2SQL", "src"),
]
ALGO_SRC_DIR = next((p for p in _ALGO_CANDIDATES if p and os.path.isdir(p)), None)

# 层一准入：触发"必须绑定本人"检查的患者数据表集合。
# 可扩展：接入真实数据源时在此追加表名，或改为按数据源分组的配置。
PATIENT_TABLES = frozenset({
    "patients", "visits", "clinical_records", "billing",
})

# 演示库标识（当前唯一数据源；将来接入真实库时扩展为列表配置）
DEMO_DATASOURCE_ID = "regional_health"
DEMO_DATASOURCE_NAME = "区域医疗集团"


# ── .env（API Key 等敏感配置）────────────────────────────────────
# 两处都找：交付包根目录、backend/。不覆盖已存在的环境变量。
# MAC-SQL 的 core/api_config.py 也会自行向上查找 .env，这里重复加载是
# 有意的——产品层需要**提前**知道 LLM 是否可用，才能做优雅降级。
def _load_env_file(path: str) -> None:
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip()
                if k and v and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


for _p in (os.path.join(_PROJECT_ROOT, ".env"),
           os.path.join(os.path.dirname(_PROJECT_ROOT), ".env")):
    _load_env_file(_p)


# ── LLM（自然语言 → SQL 的翻译环节）──────────────────────────────
# 注意：审计环节（医盾）零 LLM，这条配置只影响「翻译」，不影响安全声明。
LLM_API_KEY = os.environ.get("OPENAI_API_KEY") or ""
LLM_API_BASE = os.environ.get("OPENAI_API_BASE") or "https://api.deepseek.com/v1"
LLM_MODEL = os.environ.get("MODEL_NAME") or "deepseek-v4-pro"

# ── 问答缓存 ─────────────────────────────────────────────────────
# 系统自动积累的「问法 → 查询计划」缓存。演示时先查它（离线、确定性、
# 毫秒级），未命中再调 LLM。预热方式就是提前跑几次查询。
QUERY_CACHE_FILE = os.environ.get(
    "MEDGUARD_QUERY_CACHE", os.path.join(DEMO_DIR, "query_cache.json")
)
QUERY_CACHE_PER_TOKEN = int(os.environ.get("MEDGUARD_QUERY_CACHE_LIMIT", "10"))