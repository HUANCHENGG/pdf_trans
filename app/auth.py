"""共享 token 鉴权：所有页面与 API（除 /health）需携带 token。

获取方式：URL ?token=xxx（成功后写入 cookie）、cookie、X-Auth-Token 请求头。
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import APP_TOKEN

PUBLIC_PATHS = {"/health", "/static"}
TOKEN_COOKIE = "pdftrans_token"


class TokenAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path == "/health" or path.startswith("/static"):
            return await call_next(request)

        provided = (
            request.query_params.get("token")
            or request.headers.get("X-Auth-Token")
            or request.cookies.get(TOKEN_COOKIE)
        )
        if provided == APP_TOKEN:
            response = await call_next(request)
            # 首次通过 URL 携带 token 时固化到 cookie，便于后续访问
            if request.query_params.get("token") == APP_TOKEN:
                response.set_cookie(TOKEN_COOKIE, APP_TOKEN, httponly=True, samesite="lax")
            return response

        if path.startswith("/api/"):
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return RedirectResponse(f"{path}?token={APP_TOKEN}", status_code=303)
