"""Fábrica de conexão: cada serviço recebe sua própria URL de banco."""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


def criar_banco(variavel: str):
    """Cria metadata/Base, engine e sessão exclusivos para um microsserviço."""
    class Base(DeclarativeBase):
        pass

    url = os.getenv(variavel)
    if not url:
        raise RuntimeError(f"A variável {variavel} não foi configurada.")
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
        pool_pre_ping=True,
    )
    sessao = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def get_db():
        db = sessao()
        try:
            yield db
        finally:
            db.close()
    return Base, engine, sessao, get_db
