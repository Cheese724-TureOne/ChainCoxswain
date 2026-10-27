# -*- coding: utf-8 -*-
"""Shared fixtures for ChainCoxswain backend tests."""
import os
import sys

import pytest

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import engine as E  # noqa: E402
from service import ChainCoxswainService  # noqa: E402

START = "2026-10-26"  # classic-sample reference date used by demo data


@pytest.fixture(scope="session")
def engine():
    return E


@pytest.fixture(scope="session")
def demo(engine):
    d = engine.demo_data(START)
    return d["materials"], d["orders"]


@pytest.fixture(scope="session")
def service(tmp_path_factory):
    """Service on demo data; logs go to a temp dir so tests never touch product logs."""
    tmp = tmp_path_factory.mktemp("service")
    s = ChainCoxswainService(data_dir=PROJECT_ROOT, log_path=str(tmp / "tool_logs.json"))
    s.load_demo()
    return s


@pytest.fixture(scope="session")
def a_zip_path():
    p = os.path.join(PROJECT_ROOT, "data", "A.zip")
    assert os.path.exists(p), "data/A.zip 缺失，无法运行 A.zip 相关测试"
    return p
