# -*- coding: utf-8 -*-
"""衡鉴 · 应用入口

启动方式：
    uv run uvicorn app.main:app --reload --port 8130      （开发）
    python -m app.main                                     （等价，读 .env 配置）
    start.bat                                              （Windows 一键启动）
"""
from __future__ import annotations

import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .api import contracts, rules as rules_api

app = FastAPI(
    title=f"{config.PROJECT_CODE} · 企业合同智能审查系统",
    version=config.PROJECT_VERSION,
    description=(
        "面向企业合同审批场景的智能审查系统：多格式文档解析 → 条款结构化切分 → "
        "规则引擎与LLM双引擎风险识别 → 原文高亮定位 → 修改建议 → 审批回写。\n\n"
        "**边界声明**：系统只生成审查意见、只写审批评论区，不提供任何改变审批结论的动作。"
    ),
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if config.DEMO_MODE else ["http://127.0.0.1:%d" % config.PORT,
                                                  "http://localhost:%d" % config.PORT],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


@app.middleware("http")
async def add_process_time(request: Request, call_next):
    started = time.time()
    response = await call_next(request)
    response.headers["X-Process-Time-Ms"] = str(int((time.time() - started) * 1000))
    response.headers["X-HengJian-Version"] = config.PROJECT_VERSION
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    return JSONResponse(status_code=500,
                        content={"error": "internal_error", "detail": str(exc),
                                 "path": str(request.url.path)})


app.include_router(contracts.router)
app.include_router(rules_api.router)


@app.get("/api", tags=["system"])
def api_root():
    return {
        "project": config.PROJECT_CODE,
        "version": config.PROJECT_VERSION,
        "docs": "/api/docs",
        "console": "/",
        "endpoints": {
            "upload": "POST /api/contracts/upload",
            "pull": "POST /api/contracts/pull",
            "list": "GET /api/contracts",
            "detail": "GET /api/contracts/{id}",
            "document": "GET /api/contracts/{id}/document",
            "findings": "GET /api/contracts/{id}/findings",
            "adopt": "POST /api/contracts/{id}/findings/{fid}/adopt",
            "review": "GET /api/contracts/{id}/review",
            "writeback": "POST /api/contracts/{id}/writeback",
            "report": "GET /api/contracts/{id}/report",
            "events": "GET /api/contracts/{id}/events (SSE)",
            "rules": "GET /api/rules",
            "eval": "GET /api/eval/score",
            "overview": "GET /api/stats/overview",
            "health": "GET /api/system/health",
        },
    }


# 静态前端（零构建单页应用）。必须放在所有 /api 路由之后。
if config.FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(config.FRONTEND_DIR), html=True), name="frontend")


def run() -> None:
    import uvicorn
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT,
                reload=False, log_level="info")


if __name__ == "__main__":
    run()
