"""Convenience entry point: python run.py"""

from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.getenv("ZHIYUAN_HOST", "127.0.0.1"),
        port=int(os.getenv("ZHIYUAN_PORT", "8000")),
        reload=os.getenv("ZHIYUAN_RELOAD", "") == "1",
    )
