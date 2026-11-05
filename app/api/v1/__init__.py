from fastapi import APIRouter

from app.api.v1 import admin, customer, merchant

router = APIRouter(prefix="/api/v1")
router.include_router(customer.router)
router.include_router(merchant.router)
router.include_router(admin.router)
