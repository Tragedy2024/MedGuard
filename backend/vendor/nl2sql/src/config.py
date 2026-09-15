"""Global path configuration — the single definition point for all project paths.

Every module derives its paths from here so the repository can be relocated
without editing source files.  ``experiments/common.py`` imports these names
directly, and the experiment scripts use them instead of literal paths.
"""
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SRC_DIR = os.path.join(PROJECT_ROOT, "src")
VENDOR_DIR = os.path.join(PROJECT_ROOT, "vendor", "MAC-SQL")

CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")
SSA_DIR = os.path.join(CONFIG_DIR, "ssa")

RESULTS_DIR = os.path.join(PROJECT_ROOT, "results")
EXPERIMENTS_DIR = os.path.join(PROJECT_ROOT, "experiments")
OUTPUTS_DIR = os.path.join(PROJECT_ROOT, "outputs")

# BIRD dev
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
BIRD_DIR = os.path.join(DATA_DIR, "bird-dev", "dev_20240627")
BIRD_DB_PATH = os.path.join(BIRD_DIR, "dev_databases")
BIRD_TABLES = os.path.join(BIRD_DIR, "dev_tables.json")
BIRD_DEV = os.path.join(BIRD_DIR, "dev.json")
BIRD_COMPLEX = os.path.join(DATA_DIR, "bird-dev", "complex_queries.json")

# Spider 1.0 dev
SPIDER_DIR = os.path.join(DATA_DIR, "spider1.0")
SPIDER_DB_PATH = os.path.join(SPIDER_DIR, "database")
SPIDER_TABLES = os.path.join(SPIDER_DIR, "tables.json")
SPIDER_DEV = os.path.join(SPIDER_DIR, "dev.json")

# Defaults (backward-compatible aliases)
DB_PATH = BIRD_DB_PATH
TABLES_JSON = BIRD_TABLES
DEV_JSON = BIRD_DEV
COMPLEX_JSON = BIRD_COMPLEX


def results_path(group: str, name: str) -> str:
    """Path to a file inside ``results/<group>/``."""
    return os.path.join(RESULTS_DIR, group, name)
