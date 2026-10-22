"""运行时路径配置 —— 全后端唯一的路径/开关定义处。

设计要点（可扩展性）：
- 所有路径可用环境变量覆盖，测试用 monkeypatch.setenv 即可注入临时目录。
- 算法层（NL2SQL）路径：优先环境变量 MEDGUARD_NL2SQL_SRC，其次相对探测
  （默认与 MedGuard 同级的 NL2SQL 仓库），最后回退到已安装的包。
  仓库挪位置、或接入真实数据源时，只需改环境变量，不动代码。
"""
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 运行时数据目录（gitignore）：平台元数据库与演示业务库都放这里。
# Vercel Functions 的文件系统除 /tmp 外只读，部署时自动把运行时数据
# 目录指到 /tmp；演示业务库每次冷启动按固定种子重建，账号与报告走
# 外部 Postgres（见下方 METADATA_DATABASE_URL）。
_DEFAULT_DATA_DIR = (
    "/tmp/medguard"
    if (os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))
    else os.path.join(_PROJECT_ROOT, "data")
)
DATA_DIR = os.environ.get("MEDGUARD_DATA_DIR", _DEFAULT_DATA_DIR)
METADATA_DB = os.environ.get("MEDGUARD_METADATA_DB",
                             os.path.join(DATA_DIR, "medguard.db"))
BUSINESS_DB = os.environ.get("MEDGUARD_BUSINESS_DB",
                             os.path.join(DATA_DIR, "regional_health.db"))

# 前端构建产物目录（`cd frontend && npm run build` 的输出）。
#
# 存在时由后端**一并托管**，让演示只需一个进程、一个端口。这不只是为了
# 少开一个终端：前后端同源意味着浏览器根本不产生跨域预检——换端口、用
# 局域网 IP 给另一台机器访问、前面加一层反代，都不会撞 CORS。演示现场
# 少一个会翻车的环节。
#
# 目录不存在（纯后端开发、还没构建过）时静默跳过，前端仍走 Vite 的 proxy。
FRONTEND_DIST = os.environ.get(
    "MEDGUARD_FRONTEND_DIST",
    os.path.normpath(os.path.join(_PROJECT_ROOT, "..", "frontend", "dist")),
)

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

# ── 测试隔离（2026-09 代码评审）────────────────────────────────
# MEDGUARD_TESTING=1（由 tests/conftest.py 在导入任何 backend 模块前自动注入）
# 时，无论机器上是否存在真实 .env，都清空 LLM 配置并关闭 BGE——
# 保证 pytest 永不联网、结果确定可复现，演示与生产不受影响。
_TESTING = os.environ.get("MEDGUARD_TESTING", "0").lower() in {
    "1", "true", "yes", "on",
}


# ── 平台元数据库（Postgres，生产用）────────────────────────────
# 为空时用本地 SQLite（开发/测试默认）。Vercel Postgres/Neon 会注入
# POSTGRES_URL；也接受通用的 DATABASE_URL。测试隔离下强制为空，
# 保证 pytest 不会连到任何真实数据库。
METADATA_DATABASE_URL = "" if _TESTING else (
    os.environ.get("MEDGUARD_DATABASE_URL")
    or os.environ.get("POSTGRES_URL")
    or os.environ.get("DATABASE_URL")
    or ""
).strip()


# ── LLM（自然语言 → SQL 的翻译环节）──────────────────────────────
# 注意：审计环节（医盾）零 LLM，这条配置只影响「翻译」，不影响安全声明。
LLM_API_KEY = (os.environ.get("OPENAI_API_KEY") or "") if not _TESTING else ""
LLM_API_BASE = os.environ.get("OPENAI_API_BASE") or "https://api.deepseek.com/v1"
LLM_MODEL = os.environ.get("MODEL_NAME") or "deepseek-v4-pro"

# ── RAG 检索（默认尝试本地 BGE；不可用时自动降级为 BM25）──────────
#
# BGE 使用延迟加载：首次真正检索时才初始化；未安装 FlagEmbedding 则立即降级。
# 权重首次使用会下载，之后可完全离线运行。小型 CPU 演示优先 bge-small-zh-v1.5；有 GPU
# 或需要多语言/长文本时可换成 BAAI/bge-m3。
RAG_DENSE_ENABLED = (os.environ.get("MEDGUARD_RAG_DENSE", "1").lower() in {
    "1", "true", "yes", "on",
}) and not _TESTING
RAG_DENSE_MODEL = os.environ.get(
    "MEDGUARD_RAG_DENSE_MODEL", "BAAI/bge-small-zh-v1.5"
)
# 语料向量磁盘缓存（评审 A2b）：键绑定模型名 + 文档内容哈希，模型或语料
# 一变自动重建；冷启动编码 1.5 万条（约 60-70 秒）只在首次发生。
# 测试隔离下恒关——MEDGUARD_TESTING 不写、不读缓存文件。
RAG_DENSE_CACHE = (os.environ.get("MEDGUARD_RAG_DENSE_CACHE", "1").lower() in {
    "1", "true", "yes", "on",
}) and not _TESTING

# ── 数据源就绪门禁（评审 B6，2026-10）──────────────────────────
# 严格模式（生产/真实医院数据源建议开启）：非演示数据源存在未标注表/列
# 时禁止启用与查询（backend/readiness.py）。演示库恒为兼容模式。
STRICT_DATASOURCES = os.environ.get("MEDGUARD_STRICT_DATASOURCES", "0").lower() in {
    "1", "true", "yes", "on",
} and not _TESTING
# bge-small-zh-v1.5 在本项目 91 条语料上的实测分界：有效口语问法约
# 0.62～0.73；泛问/天气约 0.40～0.45；未覆盖膝痛最高约 0.54。
# 因而默认取 0.58。换模型或扩语料后必须用金标集重新校准。
RAG_DENSE_MIN_SCORE = float(os.environ.get("MEDGUARD_RAG_DENSE_MIN_SCORE", "0.58"))
RAG_CANDIDATE_K = max(3, int(os.environ.get("MEDGUARD_RAG_CANDIDATE_K", "20")))

# ── OpenCMKG 第二知识源（默认关）────────────────────────────────
# 第一知识源 YAML 约 91 条（人工整理、可信度高）；OpenCMKG 约 1.5 万条
# 检索文档（覆盖面大、质量等级低，见 knowledge/opencmkg.py）。开启后按
# 来源加权 RRF 融合（YAML 1.0、OpenCMKG 取 0.6～0.8），且 YAML 有可靠命中
# 时 top_k 至少保留一条人工证据。检索只召回事实，不参与直接诊断。
RAG_OPENCMKG_ENABLED = os.environ.get("MEDGUARD_RAG_OPENCMKG", "0").lower() in {
    "1", "true", "yes", "on",
}
# 权重直接进 RRF 求和：越界值钳制到 [0, 1]，防止负数/超权污染排名
RAG_OPENCMKG_WEIGHT = min(max(float(os.environ.get(
    "MEDGUARD_RAG_OPENCMKG_WEIGHT", "0.7")), 0.0), 1.0)

# ── 问答缓存（2026-09 评审拆分：预置只读 + 运行时学习）────────────
# 预置演示缓存：随仓库跟踪、**只读**（演示问法打磨过的计划）；
# 运行时学习缓存：写在 data/（gitignore），新问法在这里累计。
# 拆分后跑演示/测试不会再改动跟踪文件、把 git 弄脏。
PRESET_QUERY_CACHE = os.environ.get(
    "MEDGUARD_QUERY_CACHE_PRESET", os.path.join(DEMO_DIR, "query_cache.json")
)
LEARNED_QUERY_CACHE = os.environ.get(
    "MEDGUARD_QUERY_CACHE", os.path.join(DATA_DIR, "query_cache_learned.json")
)
QUERY_CACHE_PER_TOKEN = int(os.environ.get("MEDGUARD_QUERY_CACHE_LIMIT", "10"))
