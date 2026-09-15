"""报告与检测效能路由。

检测效能数字来自论文仓库 results/rq3/ 实测（受控注入实验），
不得杜撰——前端展示的权威数字由此接口提供。
"""
import json
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from backend import config
from backend.db import get_report, list_reports

router = APIRouter(prefix="/api", tags=["reports"])

# 检测效能：来自论文仓库 results/rq3/ 实测（受控注入实验）
_DETECTION = {
    "precision": {"value": 1.0, "detail": "误报 0/66"},
    "recall": {"value": 1.0, "detail": "漏报 0/20"},
    "blocked_detection": {"value": 1.0, "detail": "检出 10/10（L2=8 / L3=2）"},
    "source": "NL2SQL/results/rq3/",
}


@router.get("/metrics/detection")
def detection_metrics():
    return _DETECTION


@router.get("/reports")
def reports(limit: int = 20):
    if not os.path.exists(config.METADATA_DB):
        return []
    return list_reports(config.METADATA_DB, limit)


@router.get("/reports/{report_id}")
def report_detail(report_id: int):
    if not os.path.exists(config.METADATA_DB):
        raise HTTPException(status_code=404, detail="报告不存在")
    rec = get_report(config.METADATA_DB, report_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    return rec


@router.get("/reports/{report_id}/export")
def export_report(report_id: int):
    if not os.path.exists(config.METADATA_DB):
        raise HTTPException(status_code=404, detail="报告不存在")
    rec = get_report(config.METADATA_DB, report_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    body = json.dumps(rec, ensure_ascii=False, indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="medguard-report-{report_id}.json"'},
    )