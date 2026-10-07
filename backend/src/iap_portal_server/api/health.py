from fastapi import APIRouter, Response

from iap_portal_server.config import get_settings
from iap_portal_server.security import check_settings

router = APIRouter()


@router.get("/healthz")
async def liveness() -> dict:
    return {"ok": True}


@router.get("/healthz/security")
async def security_self_check(response: Response) -> dict:
    """Readiness: fails when critical auth configuration is unsafe.

    Details are logged at startup, not returned: this endpoint is reachable
    wherever the portal is.
    """
    report = check_settings(get_settings())
    if report.fatal:
        response.status_code = 500
        return {"status": "fail"}
    return {"status": "ok"}
