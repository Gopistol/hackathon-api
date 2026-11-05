"""공통 에러 형식: {"error": {"code": "...", "message": "..."}}"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.llm.base import LLMError

logger = logging.getLogger(__name__)

_HTTP_CODES = {
    400: "BAD_REQUEST",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "UPLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
}


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def not_found(what: str) -> AppError:
    return AppError("NOT_FOUND", f"{what} 정보를 찾을 수 없습니다.", 404)


def error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def _json(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content=error_body(code, message))


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, e: AppError):
        return _json(e.status_code, e.code, e.message)

    @app.exception_handler(LLMError)
    async def _llm_error(_: Request, e: LLMError):
        return _json(e.status_code, "LLM_FAILED", str(e))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, e: StarletteHTTPException):
        return _json(e.status_code, _HTTP_CODES.get(e.status_code, "HTTP_ERROR"), str(e.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, e: RequestValidationError):
        first = e.errors()[0] if e.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
        return _json(422, "VALIDATION_ERROR", f"{loc}: {first.get('msg', '잘못된 요청')}".strip(": "))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, e: Exception):
        logger.exception("처리되지 않은 오류: %s", type(e).__name__)
        return _json(500, "INTERNAL_ERROR", "서버 오류가 발생했습니다.")


# Swagger 에 에러 형식을 보여주기 위한 responses 예시
ERROR_RESPONSES = {
    404: {"description": "없음", "content": {"application/json": {"example": error_body("NOT_FOUND", "가게 정보를 찾을 수 없습니다.")}}},
    422: {"description": "입력 오류", "content": {"application/json": {"example": error_body("VALIDATION_ERROR", "lang: Input should be 'ko', 'en' or 'zh'")}}},
}
