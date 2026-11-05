"""Google Maps: 길찾기 링크와 주소 → 좌표 변환."""

import logging
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


def directions_url(lat: float, lng: float) -> str:
    # 키 없이 열리는 공식 Maps URL. 모바일에서는 구글 지도 앱으로 열린다.
    return "https://www.google.com/maps/dir/?" + urlencode({"api": 1, "destination": f"{lat},{lng}"})


async def geocode(address: str) -> tuple[float, float] | None:
    """키가 없거나 실패하면 None. 호출한 쪽에서 시장 좌표를 쓴다."""
    key = get_settings().google_maps_api_key.get_secret_value()
    if not key or not address:
        return None
    params = {"address": address, "key": key, "language": "ko", "region": "kr"}
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                data = (await client.get(GEOCODE_URL, params=params)).json()
            if data.get("status") == "OK":
                loc = data["results"][0]["geometry"]["location"]
                return loc["lat"], loc["lng"]
            # 응답 status 만 남긴다. error_message 에는 키 관련 내용이 들어갈 수 있다.
            logger.warning("Geocoding 실패: status=%s", data.get("status"))
            return None
        except (httpx.HTTPError, ValueError, KeyError) as e:
            logger.warning("Geocoding 호출 오류(%d회): %s", attempt + 1, type(e).__name__)
    return None
