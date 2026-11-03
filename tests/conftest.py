import os
import tempfile

import pytest

# 앱 import 전에 설정한다. 테스트는 항상 mock 모드 + 임시 SQLite 로 돈다 (토큰 0).
os.environ["LLM_PROVIDER"] = "mock"
os.environ["S3_ENDPOINT_URL"] = ""
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c
