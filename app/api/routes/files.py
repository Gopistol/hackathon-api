from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response
from botocore.exceptions import ClientError

from app.services import storage

router = APIRouter(prefix="/api/files", tags=["files"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 디스크 여유 15GB 유지 조건 때문에 작게 둔다.


@router.post("", status_code=201)
async def upload_file(file: UploadFile) -> dict[str, str]:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="파일은 10MB 이하만 올릴 수 있습니다.")
    try:
        key = storage.upload(data, file.filename or "file", file.content_type)
    except storage.StorageDisabledError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"key": key, "url": f"/api/files/{key}"}


@router.get("/{key:path}")
def get_file(key: str) -> Response:
    try:
        data, content_type = storage.download(key)
    except storage.StorageDisabledError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ClientError:
        raise HTTPException(status_code=404, detail="File not found")
    return Response(content=data, media_type=content_type)
