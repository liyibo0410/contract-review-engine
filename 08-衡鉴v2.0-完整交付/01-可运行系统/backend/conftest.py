# -*- coding: utf-8 -*-
"""pytest 全局夹具

**为什么必须放在 conftest.py**：`app.config` 在模块导入时就把 `HJ_DB` 读进常量了。
如果测试模块里再去改 `os.environ`，此时 config 早已绑定到生产库路径，
测试会直接写进 `backend/data/hengjian.db`（实测踩过这个坑：幂等用例被真实数据污染而失败）。
pytest 会在导入任何测试模块之前先导入 conftest，所以环境隔离只能放在这里。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# ---- 环境隔离：必须在导入 app.* 之前完成 ----
_TMP_DB = Path(tempfile.gettempdir()) / "hengjian_pytest.db"
os.environ["HJ_DB"] = str(_TMP_DB)
os.environ["HJ_LLM_ENABLED"] = "0"        # 测试不依赖外部模型，走确定性降级路径
os.environ["HJ_LLM_API_KEY"] = ""
os.environ["HJ_WRITEBACK_DRY_RUN"] = "0"  # 允许测试真实回写分支

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from app import config as app_config  # noqa: E402
from app import db  # noqa: E402


def pytest_configure(config):  # noqa: ARG001 - 参数名必须是 config，由 pytest 注入
    """会话开始前清空测试库，保证用例从干净状态开始。"""
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(_TMP_DB) + suffix)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass
    db.init_db()


@pytest.fixture(scope="session")
def tmp_db_path() -> Path:
    return app_config.DB_PATH
