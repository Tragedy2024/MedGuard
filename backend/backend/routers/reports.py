"""报告与检测效能路由。

检测效能数字来自论文仓库 results/rq3/ 实测（受控注入实验），
不得杜撰——前端展示的权威数字由此接口提供。
"""
import json
import os
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from backend import config
from backend.db import get_report, list_reports
from backend.schemas import (DetectionMetrics, ReportDetail, ReportSummary,
                             Token)
from backend.security import token_from_query

router = APIRouter(prefix="/api", tags=["reports"])

# 检测效能：来自论文仓库 results/rq3/ 实测（受控注入实验）
_DETECTION = {
    "precision": {"value": 1.0, "detail": "误报 0/66"},
    "recall": {"value": 1.0, "detail": "漏报 0/20"},
    "blocked_detection": {"value": 1.0, "detail": "检出 10/10（L2=8 / L3=2）"},
    "source": "NL2SQL/results/rq3/",
}


@router.get("/metrics/detection", response_model=DetectionMetrics)
def detection_metrics():
    return _DETECTION


@router.get("/reports", response_model=list[ReportSummary])
def reports(token: Token = Depends(token_from_query), limit: int = 20):
    """列出**该令牌发起的**查询报告。

    这个端点原先不收任何身份、无过滤地返回全表，于是医生跑完查询、患者
    登录后看到的是医生的记录。现在身份既必填、又必须签名有效。
    """
    if not os.path.exists(config.METADATA_DB):
        return []
    try:
        return list_reports(config.METADATA_DB, account=token.account,
                            limit=limit)
    except sqlite3.OperationalError:
        # 元数据库文件在、但表没建（初始化被跳过或失败）——当作"还没有报告"，
        # 而不是把 500 抛给用户。os.path.exists 那条守卫挡不住这种。
        return []


@router.get("/reports/{report_id}", response_model=ReportDetail)
def report_detail(report_id: int, token: Token = Depends(token_from_query)):
    if not os.path.exists(config.METADATA_DB):
        raise HTTPException(status_code=404, detail="报告不存在")
    rec = get_report(config.METADATA_DB, report_id, account=token.account)
    if rec is None:
        # 越权与不存在一律 404：不暴露"这条记录存在"本身。
        raise HTTPException(status_code=404, detail="报告不存在")
    return rec


@router.get("/reports/{report_id}/export")
def export_report(report_id: int, token: Token = Depends(token_from_query)):
    if not os.path.exists(config.METADATA_DB):
        raise HTTPException(status_code=404, detail="报告不存在")
    rec = get_report(config.METADATA_DB, report_id, account=token.account)
    if rec is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    body = json.dumps(rec, ensure_ascii=False, indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="medguard-report-{report_id}.json"'},
    )