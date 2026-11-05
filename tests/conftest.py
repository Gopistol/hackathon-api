import os
import tempfile
from pathlib import Path

import pytest

# 앱 import 전에 설정한다. 테스트는 항상 mock 모드 + 임시 DB·미디어 폴더로 돈다 (토큰 0).
_tmp = tempfile.mkdtemp()
os.environ["LLM_PROVIDER"] = "mock"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["MEDIA_DIR"] = f"{_tmp}/media"
os.environ["AUDIO_DIR"] = f"{_tmp}/audio"
os.environ["ADMIN_TOKEN"] = "test-admin-token"
os.environ["GOOGLE_MAPS_API_KEY"] = ""
os.environ["ELEVENLABS_API_KEY"] = ""
os.environ["CORS_ORIGIN_REGEX"] = r"^https://[a-z0-9-]+\.(vercel\.app|netlify\.app)$"

from fastapi.testclient import TestClient  # noqa: E402

from app import seed  # noqa: E402
from app.main import app  # noqa: E402

# demo/showcase 에 발표용 영상이 있어도 테스트는 임시 영상 5개로 돈다.
seed.SHOWCASE = Path(_tmp) / "showcase"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c

