"""Microsserviço de Inventário: banco e ciclo de vida próprios."""
import os
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import String, select
from sqlalchemy.orm import Mapped, mapped_column, Session
from shared.database import criar_banco
from shared.http import SegurancaHTTP, configurar_erros
from shared.security import UsuarioToken, exigir_perfil, usuario_atual, validar_configuracao

Base, engine, SessionLocal, get_db = criar_banco("INVENTARIO_DATABASE_URL")
TipoAtivo = Literal["computador", "notebook", "impressora", "rede", "outro"]

class Ativo(Base):
    __tablename__ = "ativos"
    id: Mapped[int] = mapped_column(primary_key=True)
    patrimonio: Mapped[str] = mapped_column(String(30), unique=True)
    tipo: Mapped[str] = mapped_column(String(30))
    descricao: Mapped[str] = mapped_column(String(200))
    setor: Mapped[str] = mapped_column(String(80))

class AtivoCriar(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    patrimonio: str = Field(min_length=3, max_length=30, pattern=r"^[A-Za-z0-9-]+$")
    tipo: TipoAtivo
    descricao: str = Field(min_length=2, max_length=200)
    setor: str = Field(min_length=2, max_length=80)

class AtivoSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patrimonio: str
    tipo: TipoAtivo
    descricao: str
    setor: str

def exemplos(db: Session):
    if db.scalar(select(Ativo.id).limit(1)):
        return
    for dados in [
        AtivoCriar(patrimonio="PAT-0001", tipo="computador", descricao="Desktop Dell OptiPlex", setor="Financeiro"),
        AtivoCriar(patrimonio="PAT-0002", tipo="impressora", descricao="Impressora HP LaserJet", setor="RH"),
        AtivoCriar(patrimonio="PAT-0003", tipo="notebook", descricao="Notebook Lenovo ThinkPad", setor="TI"),
    ]:
        db.add(Ativo(**dados.model_dump()))
    db.commit()

@asynccontextmanager
async def lifespan(app: FastAPI):
    validar_configuracao(); Base.metadata.create_all(engine)
    if os.getenv("DADOS_EXEMPLO", "1") == "1":
        with SessionLocal() as db: exemplos(db)
    yield

app = FastAPI(title="HelpDesk TI - Serviço de Inventário", version="1.0.0", lifespan=lifespan)
app.add_middleware(SegurancaHTTP); configurar_erros(app)

@app.get("/saude", tags=["Saúde"])
def saude():
    return {"servico": "inventario", "status": "ok"}

@app.post("/ativos", response_model=AtivoSaida, status_code=201, tags=["Inventário"])
def criar(dados: AtivoCriar, db: Session = Depends(get_db), _: UsuarioToken = Depends(exigir_perfil("tecnico", "admin"))):
    if db.scalar(select(Ativo).where(Ativo.patrimonio == dados.patrimonio)):
        raise HTTPException(422, "Já existe um ativo com este patrimônio.")
    ativo = Ativo(**dados.model_dump()); db.add(ativo); db.commit(); db.refresh(ativo)
    return ativo

@app.get("/ativos", response_model=list[AtivoSaida], tags=["Inventário"])
def listar(setor: str | None = Query(None, max_length=80), db: Session = Depends(get_db), _: UsuarioToken = Depends(usuario_atual)):
    consulta = select(Ativo).order_by(Ativo.id)
    if setor: consulta = consulta.where(Ativo.setor == setor)
    return list(db.scalars(consulta))

@app.get("/ativos/{ativo_id}", response_model=AtivoSaida, tags=["Inventário"])
def obter(ativo_id: int, db: Session = Depends(get_db), _: UsuarioToken = Depends(usuario_atual)):
    ativo = db.get(Ativo, ativo_id)
    if ativo is None: raise HTTPException(404, f"Ativo {ativo_id} não encontrado.")
    return ativo
