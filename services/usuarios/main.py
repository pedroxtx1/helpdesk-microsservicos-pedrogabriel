"""Microsserviço de Usuários e autenticação."""
import os, time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from threading import Lock
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import Boolean, String, select
from sqlalchemy.orm import Mapped, mapped_column, Session

from shared.database import criar_banco
from shared.http import SegurancaHTTP, configurar_erros
from shared.passwords import HASH_FALSO, conferir, gerar_hash
from shared.security import UsuarioToken, criar_token, exigir_perfil, usuario_atual, validar_configuracao

Base, engine, SessionLocal, get_db = criar_banco("USUARIOS_DATABASE_URL")
Perfil = Literal["solicitante", "tecnico", "admin"]

class Usuario(Base):
    __tablename__ = "usuarios"
    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    perfil: Mapped[str] = mapped_column(String(20))
    senha_hash: Mapped[str] = mapped_column(String(200))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)

class UsuarioCriar(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    nome: str = Field(min_length=2, max_length=120)
    email: EmailStr
    perfil: Perfil
    senha: str = Field(min_length=8, max_length=128)
    @field_validator("senha")
    @classmethod
    def senha_forte(cls, value: str) -> str:
        import re
        if not (re.search(r"[A-Za-z]", value) and re.search(r"\d", value)):
            raise ValueError("A senha precisa ter letras e números.")
        return value

class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    senha: str = Field(min_length=1, max_length=128)

class UsuarioSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    nome: str
    email: str
    perfil: Perfil

class UsuarioPublico(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    nome: str
    perfil: Perfil

class TokenSaida(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expira_em: datetime
    usuario: UsuarioSaida

_falhas: dict[str, list[float]] = defaultdict(list)
_lock = Lock()

def login_bloqueado(chave: str) -> bool:
    agora = time.monotonic()
    with _lock:
        _falhas[chave] = [t for t in _falhas[chave] if agora - t < 300]
        return len(_falhas[chave]) >= 5

def registrar_falha(chave: str):
    with _lock:
        _falhas[chave].append(time.monotonic())

def limpar_falhas(chave: str):
    with _lock:
        _falhas.pop(chave, None)

def criar_usuario(db: Session, dados: UsuarioCriar) -> Usuario:
    email = str(dados.email).lower()
    if db.scalar(select(Usuario).where(Usuario.email == email)):
        raise HTTPException(422, "Já existe um usuário com este e-mail.")
    usuario = Usuario(nome=dados.nome, email=email, perfil=dados.perfil, senha_hash=gerar_hash(dados.senha), ativo=True)
    db.add(usuario); db.commit(); db.refresh(usuario)
    return usuario

def carregar_exemplos(db: Session):
    if db.scalar(select(Usuario.id).limit(1)):
        return
    senha = os.getenv("SENHA_EXEMPLO", "Helpdesk2026")
    for nome, email, perfil in [
        ("Admin TI", "admin@empresa.com", "admin"),
        ("Ana Souza", "ana@empresa.com", "solicitante"),
        ("Bruno Lima", "bruno@empresa.com", "solicitante"),
        ("Carla Dias", "carla@empresa.com", "tecnico"),
        ("Diego Rocha", "diego@empresa.com", "tecnico"),
    ]:
        criar_usuario(db, UsuarioCriar(nome=nome, email=email, perfil=perfil, senha=senha))

@asynccontextmanager
async def lifespan(app: FastAPI):
    validar_configuracao()
    Base.metadata.create_all(engine)
    if os.getenv("DADOS_EXEMPLO", "1") == "1":
        with SessionLocal() as db:
            carregar_exemplos(db)
    yield

app = FastAPI(title="HelpDesk TI - Serviço de Usuários", version="1.0.0", lifespan=lifespan)
app.add_middleware(SegurancaHTTP)
configurar_erros(app)

@app.get("/saude", tags=["Saúde"])
def saude():
    return {"servico": "usuarios", "status": "ok"}

@app.post("/auth/login", response_model=TokenSaida, tags=["Autenticação"])
def login(dados: Login, request: Request, db: Session = Depends(get_db)):
    email = str(dados.email).lower()
    chave = f"{email}|{request.client.host if request.client else '-'}"
    if login_bloqueado(chave):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Muitas tentativas. Tente de novo em alguns minutos.")
    usuario = db.scalar(select(Usuario).where(Usuario.email == email))
    senha_ok = conferir(dados.senha, usuario.senha_hash if usuario else HASH_FALSO)
    if not usuario or not senha_ok or not usuario.ativo:
        registrar_falha(chave)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "E-mail ou senha inválidos.")
    limpar_falhas(chave)
    token, expira = criar_token(usuario.id, usuario.perfil)
    return TokenSaida(access_token=token, expira_em=expira, usuario=UsuarioSaida.model_validate(usuario))

@app.get("/auth/eu", response_model=UsuarioSaida, tags=["Autenticação"])
def eu(principal: UsuarioToken = Depends(usuario_atual), db: Session = Depends(get_db)):
    usuario = db.get(Usuario, principal.id)
    if usuario is None or not usuario.ativo:
        raise HTTPException(401, "Usuário inativo ou inexistente.")
    return usuario

@app.post("/usuarios", response_model=UsuarioSaida, status_code=201, tags=["Usuários"])
def criar(dados: UsuarioCriar, db: Session = Depends(get_db), _: UsuarioToken = Depends(exigir_perfil("admin"))):
    return criar_usuario(db, dados)

@app.get("/usuarios", response_model=list[UsuarioPublico], tags=["Usuários"])
def listar(perfil: Perfil | None = None, db: Session = Depends(get_db), _: UsuarioToken = Depends(usuario_atual)):
    consulta = select(Usuario).order_by(Usuario.id)
    if perfil:
        consulta = consulta.where(Usuario.perfil == perfil)
    return list(db.scalars(consulta))

@app.get("/usuarios/{usuario_id}", response_model=UsuarioPublico, tags=["Usuários"])
def obter(usuario_id: int, db: Session = Depends(get_db), _: UsuarioToken = Depends(usuario_atual)):
    usuario = db.get(Usuario, usuario_id)
    if usuario is None or not usuario.ativo:
        raise HTTPException(404, f"Usuário {usuario_id} não encontrado.")
    return usuario
