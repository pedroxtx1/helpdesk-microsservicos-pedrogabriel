"""Tratamento comum de erros e cabeçalhos de segurança."""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.middleware.base import BaseHTTPMiddleware

class SegurancaHTTP(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        tamanho = request.headers.get("content-length")
        if tamanho and (not tamanho.isdigit() or int(tamanho) > 64 * 1024):
            return JSONResponse(status_code=413, content={"detail": "Requisição grande demais."})
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if request.headers.get("authorization"):
            response.headers["Cache-Control"] = "no-store"
        return response


def configurar_erros(app: FastAPI):
    @app.exception_handler(IntegrityError)
    async def conflito(request: Request, exc: IntegrityError):
        return JSONResponse(status_code=409, content={"detail": "O registro conflita com outro já existente."})
