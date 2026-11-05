"""실제 OpenAI API 호출 테스트 (비용 발생). 기본 pytest 실행에서는 제외된다.

실행: OPENAI_API_KEY 를 .env 또는 환경변수에 넣고 `pytest -m live`
"""

import asyncio

import pytest

from app.core.config import Settings
from app.llm.openai_provider import OpenAIProvider
from app.services import ai

pytestmark = pytest.mark.live

settings = Settings()
key = settings.openai_api_key.get_secret_value()


@pytest.mark.skipif(not key, reason="OPENAI_API_KEY 가 없습니다")
def test_live_structure_and_translate() -> None:
    provider = OpenAIProvider(key, settings.openai_model, 1024, settings.openai_timeout)

    async def run():
        parsed, needs = await ai.structure_step(provider, "menus", "기름떡볶이 사천 원이고요 순대는 오천 원이요")
        tr = await ai.translate_store(provider, "영천 할매 떡볶이", "40년 됐어요.", [{"id": "m0", "name_ko": "기름떡볶이", "price": 4000}])
        return parsed, tr

    parsed, tr = asyncio.run(run())
    assert [m["price"] for m in parsed["menus"]] == [4000, 5000]
    assert tr["en"]["menus"]["m0"]["name"] != "기름떡볶이"
    assert tr["zh"]["store"]["name"]
