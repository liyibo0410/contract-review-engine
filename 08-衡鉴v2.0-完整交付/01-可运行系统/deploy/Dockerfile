# ============================================================
#  衡鉴 · 后端镜像（多阶段构建）
#  目标：最终镜像不含编译工具链，且不携带 venv / __pycache__（交付要求）
# ============================================================
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# 构建期依赖（psycopg/mupdf 等编译需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY backend/app ./backend/app
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install . \
    && /opt/venv/bin/pip install rapidocr-onnxruntime


# ------------------------------------------------------------
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="衡鉴 HengJian" \
      org.opencontainers.image.description="企业合同智能审查系统" \
      org.opencontainers.image.version="2.0.0"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    PATH="/opt/venv/bin:$PATH" \
    HJ_HOST=0.0.0.0 \
    HJ_PORT=8130 \
    HJ_DB=/data/hengjian.db

# OpenCV / onnxruntime 运行时依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 libgl1 libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY backend /app/backend
COPY frontend /app/frontend

RUN mkdir -p /data /app/backend/data/reports /app/backend/data/uploads

EXPOSE 8130

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8130/api/system/health || exit 1

WORKDIR /app/backend

# 首次启动若样例缺失则自动生成，保证容器开箱即用
CMD ["sh", "-c", "python tools/gen_samples.py >/dev/null 2>&1 || true; exec uvicorn app.main:app --host 0.0.0.0 --port 8130"]
