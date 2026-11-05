import secrets

from fastapi import APIRouter, Header

from app.core.config import get_settings
from app.core.errors import AppError
from app import seed

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/reset", summary="데모 데이터 초기화 (리허설용)")
def reset(x_admin_token: str = Header("", description="서버 ADMIN_TOKEN 값")):
    token = get_settings().admin_token.get_secret_value()
    if not token:
        raise AppError("ADMIN_DISABLED", "ADMIN_TOKEN 이 설정되지 않아 리셋을 쓸 수 없습니다.", 403)
    if not secrets.compare_digest(x_admin_token, token):
        raise AppError("FORBIDDEN", "관리 토큰이 올바르지 않습니다.", 403)
    seed.reset()
    return {"status": "reset"}
