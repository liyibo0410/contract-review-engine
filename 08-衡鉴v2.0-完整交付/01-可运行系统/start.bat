@echo off
chcp 65001 >nul
title 衡鉴 · 企业合同智能审查系统

echo ============================================================
echo   衡鉴 HengJian - 企业合同智能审查系统 v2.0
echo ============================================================
echo.

cd /d "%~dp0"

REM ---------- 1. 检查 uv ----------
where uv >nul 2>nul
if errorlevel 1 (
  echo [警告] 未检测到 uv，将尝试使用系统 Python。
  echo        建议安装 uv：powershell -c "irm https://astral.sh/uv/install.ps1 ^| iex"
  set PY=python
) else (
  set PY=uv run python
)

REM ---------- 2. 准备虚拟环境与依赖 ----------
if not exist ".venv\Scripts\python.exe" (
  echo [1/4] 创建虚拟环境 .venv ...
  if "%PY%"=="python" (
    python -m venv .venv
  ) else (
    uv venv --python 3.11 .venv || uv venv .venv
  )
)

echo [2/4] 同步依赖 ...
if exist "uv.lock" (
  uv sync --quiet || uv pip install --python ".venv\Scripts\python.exe" -e . --quiet
) else (
  uv pip install --python ".venv\Scripts\python.exe" -r requirements.txt --quiet 2>nul
)
if errorlevel 1 (
  echo [警告] 依赖同步失败，若已有可用环境可忽略。
)

REM ---------- 3. 生成样例合同 ----------
if not exist "backend\samples\*.pdf" (
  echo [3/4] 首次运行：生成样例合同集与黄金标注 ...
  cd backend
  "..\.venv\Scripts\python.exe" tools\gen_samples.py
  cd ..
) else (
  echo [3/4] 样例合同已存在，跳过生成。
)

REM ---------- 4. 启动服务 ----------
echo [4/4] 启动服务 http://127.0.0.1:8130 ...
echo.
echo   控制台      http://127.0.0.1:8130/
echo   接口文档    http://127.0.0.1:8130/api/docs
echo   停止服务    Ctrl + C
echo.
start "" http://127.0.0.1:8130/
cd backend
"..\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8130
