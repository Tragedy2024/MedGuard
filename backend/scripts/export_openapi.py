"""导出当前应用契约：python scripts/export_openapi.py。不会启动服务。"""
import json
import os
from pathlib import Path
import sys


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    os.environ["MEDGUARD_TESTING"] = "1"
    from backend.main import app

    target = root / "docs" / "api-contract" / "openapi.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(app.openapi(), ensure_ascii=False,
                                 separators=(",", ":")) + "\n", encoding="utf-8")
    print(target)
