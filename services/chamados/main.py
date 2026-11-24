"""Microsserviço de Chamados. Não possui FK nem acesso aos bancos externos."""
import os
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import DateTime, ForeignKey, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column, relationship, Session

from shared.database import criar_banco
from shared.events import publicar_evento
from shared.http import SegurancaHTTP, configurar_erros
from shared.security import UsuarioToken, exigir_perfil, usuario_atual, validar_configuracao

Base, engine, SessionLocal, get_db = criar_banco("CHAMADOS_DATABASE_URL")
USUARIOS_URL = os.getenv("USUARIOS_URL", "http://usuarios:8000")
INVENTARIO_URL = os.getenv("INVENTARIO_URL", "http://inventario:8000")
TIMEOUT_SERVICOS = float(os.getenv("TIMEOUT_SERVICOS", "2.0"))
Prioridade = Literal["baixa", "media", "alta", "critica"]
Status = Literal["aberto", "em_atendimento", "resolvido", "fechado"]
ENTRADA = ConfigDict(extra="forbid", str_strip_whitespace=True)
HORAS_SLA = {"critica": 4, "alta": 8, "media": 24, "baixa": 72}
TRANSICOES = {
    "aberto": {"em_atendimento"},
    "em_atendimento": {"resolvido"},
    "resolvido": {"fechado", "em_atendimento"},
    "fechado": set(),
}
EQUIPE = {"tecnico", "admin"}

class Chamado(Base):
    __tablename__ = "chamados"
    id: Mapped[int] = mapped_column(primary_key=True)
    titulo: Mapped[str] = mapped_column(String(120))
    descricao: Mapped[str] = mapped_column(Text)
    prioridade: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="aberto")
    # IDs são referências externas simples; não há chaves estrangeiras entre serviços.
    solicitante_id: Mapped[int] = mapped_column(index=True)
    tecnico_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    ativo_id: Mapped[int | None] = mapped_column(nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime)
    prazo_sla: Mapped[datetime] = mapped_column(DateTime)
    comentarios: Mapped[list["Comentario"]] = relationship(
        back_populates="chamado", order_by="Comentario.id", cascade="all, delete-orphan"
    )

class Comentario(Base):
    __tablename__ = "comentarios"
    id: Mapped[int] = mapped_column(primary_key=True)
    chamado_id: Mapped[int] = mapped_column(ForeignKey("chamados.id"))
    autor_id: Mapped[int] = mapped_column()  # ID externo do serviço de Usuários
    texto: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(DateTime)
    chamado: Mapped[Chamado] = relationship(back_populates="comentarios")

class ChamadoCriar(BaseModel):
    model_config = ENTRADA
    titulo: str = Field(min_length=3, max_length=120)
    descricao: str = Field(min_length=3, max_length=2000)
    prioridade: Prioridade
    ativo_id: int | None = Field(default=None, gt=0)

class Atribuicao(BaseModel):
    model_config = ENTRADA
    tecnico_id: int = Field(gt=0)

class MudancaStatus(BaseModel):
    model_config = ENTRADA
    status: Status

class ComentarioCriar(BaseModel):
    model_config = ENTRADA
    texto: str = Field(min_length=1, max_length=2000)

class ComentarioSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    autor_id: int
    texto: str
    criado_em: datetime

class ChamadoSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    titulo: str
    descricao: str
    prioridade: Prioridade
    status: Status
    solicitante_id: int
    tecnico_id: int | None
    ativo_id: int | None
    criado_em: datetime
    prazo_sla: datetime
    comentarios: list[ComentarioSaida] = Field(default_factory=list)

class Painel(BaseModel):
    total: int
    por_status: dict[str, int]
    atrasados: int


def chamada_externa(base_url: str, rota: str, authorization: str | None, nome: str) -> dict:
    """Consulta outro microsserviço por HTTP e repassa o token do usuário."""
    headers = {"Authorization": authorization} if authorization else {}
    try:
        response = httpx.get(f"{base_url}{rota}", headers=headers, timeout=TIMEOUT_SERVICOS)
    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
        raise HTTPException(503, f"Serviço de {nome} indisponível no momento. Tente novamente em instantes.") from exc
    if response.status_code == 401:
        raise HTTPException(401, "Token não aceito pelo serviço consultado.")
    if response.status_code == 403:
        raise HTTPException(403, "Acesso negado pelo serviço consultado.")
    if response.status_code >= 500:
        raise HTTPException(503, f"Serviço de {nome} apresentou uma falha.")
    if response.status_code >= 400:
        try: detalhe = response.json().get("detail", "Recurso não encontrado.")
        except ValueError: detalhe = "Recurso não encontrado."
        raise HTTPException(response.status_code, detalhe)
    return response.json()


def eh_equipe(usuario: UsuarioToken) -> bool:
    return usuario.perfil in EQUIPE

def calcular_prazo(prioridade: str, inicio: datetime) -> datetime:
    return inicio + timedelta(hours=HORAS_SLA[prioridade])

def salvar(db: Session, chamado: Chamado) -> Chamado:
    db.add(chamado); db.commit(); db.refresh(chamado)
    return chamado

def buscar(db: Session, chamado_id: int) -> Chamado | None:
    return db.get(Chamado, chamado_id)

def obter(db: Session, chamado_id: int, usuario: UsuarioToken) -> Chamado:
    chamado = buscar(db, chamado_id)
    if chamado is None or not (eh_equipe(usuario) or chamado.solicitante_id == usuario.id):
        raise HTTPException(404, f"Chamado {chamado_id} não encontrado.")
    return chamado

def publicar(tipo: str, destinatario_id: int, mensagem: str, chamado_id: int):
    publicar_evento(tipo, destinatario_id, mensagem, chamado_id)

def abrir(db: Session, dados: ChamadoCriar, usuario: UsuarioToken, authorization: str | None) -> Chamado:
    if dados.ativo_id is not None:
        chamada_externa(INVENTARIO_URL, f"/ativos/{dados.ativo_id}", authorization, "Inventário")
    agora = datetime.now()
    chamado = salvar(db, Chamado(
        **dados.model_dump(), solicitante_id=usuario.id, status="aberto", criado_em=agora,
        prazo_sla=calcular_prazo(dados.prioridade, agora),
    ))
    publicar("ChamadoAberto", usuario.id,
             f"Seu chamado #{chamado.id} foi aberto. Prazo: {chamado.prazo_sla:%d/%m %H:%M}.", chamado.id)
    return chamado

def listar(db: Session, usuario: UsuarioToken, status: str | None = None, tecnico_id: int | None = None):
    consulta = select(Chamado).order_by(Chamado.id)
    if not eh_equipe(usuario): consulta = consulta.where(Chamado.solicitante_id == usuario.id)
    if status: consulta = consulta.where(Chamado.status == status)
    if tecnico_id: consulta = consulta.where(Chamado.tecnico_id == tecnico_id)
    return list(db.scalars(consulta))

def atribuir(db: Session, chamado_id: int, tecnico_id: int, usuario: UsuarioToken, authorization: str | None):
    if not eh_equipe(usuario): raise HTTPException(403, "Só técnicos e administradores atribuem chamados.")
    chamado = obter(db, chamado_id, usuario)
    if chamado.status in ("resolvido", "fechado"):
        raise HTTPException(422, "Não é possível atribuir um chamado resolvido ou fechado.")
    tecnico = chamada_externa(USUARIOS_URL, f"/usuarios/{tecnico_id}", authorization, "Usuários")
    if tecnico.get("perfil") != "tecnico":
        raise HTTPException(422, f"Usuário {tecnico_id} não é técnico.")
    chamado.tecnico_id = tecnico_id
    if chamado.status == "aberto": chamado.status = "em_atendimento"
    salvar(db, chamado)
    publicar("ChamadoAtribuido", tecnico_id, f"O chamado #{chamado.id} foi atribuído a você.", chamado.id)
    return chamado

def mudar_status(db: Session, chamado_id: int, novo_status: str, usuario: UsuarioToken):
    chamado = obter(db, chamado_id, usuario)
    pode_solicitante = chamado.status == "resolvido" and novo_status in ("fechado", "em_atendimento")
    if not (eh_equipe(usuario) or pode_solicitante):
        raise HTTPException(403, "Você não pode mudar o status deste chamado.")
    if novo_status not in TRANSICOES[chamado.status]:
        raise HTTPException(422, f"Não é possível ir de '{chamado.status}' para '{novo_status}'.")
    if novo_status == "em_atendimento" and chamado.tecnico_id is None:
        raise HTTPException(422, "Atribua um técnico antes de iniciar o atendimento.")
    chamado.status = novo_status
    salvar(db, chamado)
    if novo_status == "resolvido":
        publicar("ChamadoResolvido", chamado.solicitante_id,
                 f"Seu chamado #{chamado.id} foi resolvido. Confirme se está tudo certo.", chamado.id)
    return chamado

def comentar(db: Session, chamado_id: int, dados: ComentarioCriar, usuario: UsuarioToken):
    chamado = obter(db, chamado_id, usuario)
    if chamado.status == "fechado": raise HTTPException(422, "Chamados fechados não recebem comentários.")
    comentario = Comentario(chamado_id=chamado.id, autor_id=usuario.id, texto=dados.texto, criado_em=datetime.now())
    db.add(comentario); db.commit(); db.refresh(comentario)
    return comentario

def painel(db: Session, usuario: UsuarioToken):
    chamados = listar(db, usuario)
    agora = datetime.now()
    atrasados = sum(1 for c in chamados if c.status in ("aberto", "em_atendimento") and c.prazo_sla < agora)
    return {"total": len(chamados), "por_status": dict(Counter(c.status for c in chamados)), "atrasados": atrasados}

def carregar_exemplos(db: Session):
    if db.scalar(select(Chamado.id).limit(1)): return
    agora = datetime.now()
    exemplos = [
        Chamado(titulo="Computador não liga", descricao="Apertei o botão e nada acontece.", prioridade="alta", status="em_atendimento", solicitante_id=2, tecnico_id=4, ativo_id=1, criado_em=agora, prazo_sla=calcular_prazo("alta", agora)),
        Chamado(titulo="Impressora atolando papel", descricao="Atola a cada 3 folhas.", prioridade="media", status="aberto", solicitante_id=3, tecnico_id=None, ativo_id=2, criado_em=agora, prazo_sla=calcular_prazo("media", agora)),
        Chamado(titulo="Acesso ao sistema de RH", descricao="Preciso de acesso para a nova estagiária.", prioridade="baixa", status="aberto", solicitante_id=3, tecnico_id=None, ativo_id=None, criado_em=agora, prazo_sla=calcular_prazo("baixa", agora)),
    ]
    db.add_all(exemplos); db.commit()

@asynccontextmanager
async def lifespan(app: FastAPI):
    validar_configuracao(); Base.metadata.create_all(engine)
    if os.getenv("DADOS_EXEMPLO", "1") == "1":
        with SessionLocal() as db: carregar_exemplos(db)
    yield

app = FastAPI(title="HelpDesk TI - Serviço de Chamados", version="1.0.0", lifespan=lifespan)
app.add_middleware(SegurancaHTTP); configurar_erros(app)

@app.get("/saude", tags=["Saúde"])
def saude(): return {"servico": "chamados", "status": "ok"}

@app.post("/chamados", response_model=ChamadoSaida, status_code=201, tags=["Chamados"])
def criar(dados: ChamadoCriar, usuario: UsuarioToken = Depends(usuario_atual), authorization: str | None = Header(None), db: Session = Depends(get_db)):
    return abrir(db, dados, usuario, authorization)

@app.get("/chamados", response_model=list[ChamadoSaida], tags=["Chamados"])
def listar_rota(status: Status | None = None, tecnico_id: int | None = None, usuario: UsuarioToken = Depends(usuario_atual), db: Session = Depends(get_db)):
    return listar(db, usuario, status, tecnico_id)

@app.get("/chamados/painel", response_model=Painel, tags=["Chamados"])
def painel_rota(usuario: UsuarioToken = Depends(usuario_atual), db: Session = Depends(get_db)):
    return painel(db, usuario)

@app.get("/chamados/{chamado_id}", response_model=ChamadoSaida, tags=["Chamados"])
def obter_rota(chamado_id: int, usuario: UsuarioToken = Depends(usuario_atual), db: Session = Depends(get_db)):
    return obter(db, chamado_id, usuario)

@app.patch("/chamados/{chamado_id}/atribuir", response_model=ChamadoSaida, tags=["Chamados"])
def atribuir_rota(chamado_id: int, dados: Atribuicao, usuario: UsuarioToken = Depends(usuario_atual), authorization: str | None = Header(None), db: Session = Depends(get_db)):
    return atribuir(db, chamado_id, dados.tecnico_id, usuario, authorization)

@app.patch("/chamados/{chamado_id}/status", response_model=ChamadoSaida, tags=["Chamados"])
def status_rota(chamado_id: int, dados: MudancaStatus, usuario: UsuarioToken = Depends(usuario_atual), db: Session = Depends(get_db)):
    return mudar_status(db, chamado_id, dados.status, usuario)

@app.post("/chamados/{chamado_id}/comentarios", response_model=ComentarioSaida, status_code=201, tags=["Chamados"])
def comentar_rota(chamado_id: int, dados: ComentarioCriar, usuario: UsuarioToken = Depends(usuario_atual), db: Session = Depends(get_db)):
    return comentar(db, chamado_id, dados, usuario)
