"""Configura bancos isolados de teste antes de importar as aplicações."""
import os
from pathlib import Path

root = Path("/tmp/helpdesk-microsservicos-tests")
root.mkdir(parents=True, exist_ok=True)
for name in ("usuarios", "inventario", "chamados", "notificacoes"):
    path = root / f"{name}.sqlite3"
    if path.exists(): path.unlink()
    os.environ[f"{name.upper()}_DATABASE_URL"] = f"sqlite:///{path}"
os.environ["JWT_SEGREDO"] = "teste-local-seguro-nao-usar-em-producao-123456789"
os.environ["DADOS_EXEMPLO"] = "0"
os.environ["ATIVAR_CONSUMIDOR_EVENTOS"] = "0"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/15"
os.environ["INVENTARIO_URL"] = "http://127.0.0.1:8000"
os.environ["USUARIOS_URL"] = "http://127.0.0.1:8001"
