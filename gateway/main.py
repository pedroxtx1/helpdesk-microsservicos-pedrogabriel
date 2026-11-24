"""API Gateway: ponto único de entrada e servidor da interface web."""
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, Response, JSONResponse

from shared.http import SegurancaHTTP

WEB = Path(__file__).resolve().parents[1] / "app" / "web"
SERVICOS = {
    "/auth": os.getenv("USUARIOS_URL", "http://usuarios:8000"),
    "/usuarios": os.getenv("USUARIOS_URL", "http://usuarios:8000"),
    "/ativos": os.getenv("INVENTARIO_URL", "http://inventario:8000"),
    "/chamados": os.getenv("CHAMADOS_URL", "http://chamados:8000"),
    "/notificacoes": os.getenv("NOTIFICACOES_URL", "http://notificacoes:8000"),
}
METODOS = ["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"]
app = FastAPI(title="HelpDesk TI - API Gateway", version="1.0.0", docs_url="/docs", redoc_url=None)
app.add_middleware(SegurancaHTTP)

@app.get("/", include_in_schema=False)
def tela():
    return FileResponse(WEB / "index.html")

@app.get("/app.js", include_in_schema=False)
def javascript():
    return FileResponse(WEB / "app.js", media_type="text/javascript")

@app.get("/estilo.css", include_in_schema=False)
def css():
    return FileResponse(WEB / "estilo.css", media_type="text/css")

@app.get("/saude", tags=["Saúde"])
async def saude():
    resultado = {"gateway": "ok", "servicos": {}}
    async with httpx.AsyncClient(timeout=1.5) as client:
        for nome, url in [
            ("usuarios", SERVICOS["/usuarios"]), ("inventario", SERVICOS["/ativos"]),
            ("chamados", SERVICOS["/chamados"]), ("notificacoes", SERVICOS["/notificacoes"]),
        ]:
            try:
                response = await client.get(f"{url}/saude")
                resultado["servicos"][nome] = "ok" if response.is_success else "indisponivel"
            except httpx.HTTPError:
                resultado["servicos"][nome] = "indisponivel"
    resultado["status"] = "ok" if all(v == "ok" for v in resultado["servicos"].values()) else "degradado"
    return resultado

@app.api_route("/{caminho:path}", methods=METODOS, include_in_schema=False)
async def encaminhar(caminho: str, request: Request):
    caminho_completo = "/" + caminho
    destino = next((url for prefixo, url in SERVICOS.items()
                    if caminho_completo == prefixo or caminho_completo.startswith(prefixo + "/")), None)
    if destino is None:
        raise HTTPException(404, "Rota não encontrada no API Gateway.")
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in {"host", "content-length", "connection", "transfer-encoding"}}
    corpo = await request.body()
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            upstream = await client.request(
                request.method, f"{destino}{caminho_completo}", params=request.query_params,
                content=corpo, headers=headers,
            )
    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
        return JSONResponse(status_code=503, content={"detail": "O serviço solicitado está indisponível. Tente novamente."})
    resposta_headers = {k: v for k, v in upstream.headers.items()
                        if k.lower() not in {"content-encoding", "transfer-encoding", "connection", "content-length"}}
    return Response(content=upstream.content, status_code=upstream.status_code, headers=resposta_headers)
