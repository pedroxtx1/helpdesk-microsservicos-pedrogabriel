"""Microsserviço consumidor dos eventos de domínio publicados pelo serviço de Chamados."""
import asyncio, json, logging, os
from contextlib import asynccontextmanager, suppress
from datetime import datetime

from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict
try:
    from redis import Redis
except ImportError:  # testes unitários podem rodar sem iniciar o consumidor Redis
    Redis = None
from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column, Session

from shared.database import criar_banco
from shared.events import QUEUE, REDIS_URL
from shared.http import SegurancaHTTP, configurar_erros
from shared.security import UsuarioToken, usuario_atual, validar_configuracao

log = logging.getLogger("helpdesk.notificacoes")
Base, engine, SessionLocal, get_db = criar_banco("NOTIFICACOES_DATABASE_URL")

class Notificacao(Base):
    __tablename__ = "notificacoes"
    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(40))
    destinatario_id: Mapped[int] = mapped_column(Integer, index=True)
    mensagem: Mapped[str] = mapped_column(Text)
    criada_em: Mapped[datetime] = mapped_column(DateTime)
    chamado_id: Mapped[int | None] = mapped_column(nullable=True)

class NotificacaoSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tipo: str
    destinatario_id: int
    mensagem: str
    criada_em: datetime
    chamado_id: int | None

async def consumir_eventos():
    """Consome a fila Redis e persiste avisos no banco exclusivo de Notificações."""
    cliente = Redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=1, socket_timeout=3)
    while True:
        try:
            item = await asyncio.to_thread(cliente.blpop, QUEUE, 1)
            if not item:
                continue
            evento = json.loads(item[1])
            with SessionLocal() as db:
                db.add(Notificacao(
                    tipo=str(evento["tipo"]), destinatario_id=int(evento["destinatario_id"]),
                    mensagem=str(evento["mensagem"]), chamado_id=int(evento["chamado_id"]),
                    criada_em=datetime.now(),
                ))
                db.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Falha ao consumir evento da fila. O consumidor tentará novamente.")
            await asyncio.sleep(1)

@asynccontextmanager
async def lifespan(app: FastAPI):
    validar_configuracao(); Base.metadata.create_all(engine)
    habilitado = os.getenv("ATIVAR_CONSUMIDOR_EVENTOS", "1") == "1"
    task = asyncio.create_task(consumir_eventos()) if Redis is not None and habilitado else None
    yield
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

app = FastAPI(title="HelpDesk TI - Serviço de Notificações", version="1.0.0", lifespan=lifespan)
app.add_middleware(SegurancaHTTP); configurar_erros(app)

@app.get("/saude", tags=["Saúde"])
def saude():
    return {"servico": "notificacoes", "status": "ok"}

@app.get("/notificacoes", response_model=list[NotificacaoSaida], tags=["Notificações"])
def listar(usuario: UsuarioToken = Depends(usuario_atual), db: Session = Depends(get_db)):
    consulta = select(Notificacao).where(Notificacao.destinatario_id == usuario.id).order_by(Notificacao.id)
    return list(db.scalars(consulta))
