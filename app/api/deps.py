"""Shared dependencies for API routers."""

from __future__ import annotations

from fastapi import Request

from ..config import AppConfig, SettingsStore
from ..db import Database


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_config(request: Request) -> AppConfig:
    return request.app.state.config


def get_store(request: Request) -> SettingsStore:
    return request.app.state.store
