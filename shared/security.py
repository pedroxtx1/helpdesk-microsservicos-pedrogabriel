"""Validação JWT compartilhada: cada microsserviço valida o token independentemente."""
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Header, HTTPException, status

JWT_SECRET = os.getenv("JWT_SEGREDO", "")
JWT_ISSUER = "helpdesk-ti-microsservicos"
JWT_ALGORITHM = "HS256"
TOKEN_MINUTES = int(os.getenv("TOKEN_MINUTOS", "60"))


def validar_configuracao() -> None:
    if len(JWT_SECRET) < 32:
        raise RuntimeError("JWT_SEGREDO precisa ter pelo menos 32 caracteres. Configure o arquivo .env.")


@dataclass(frozen=True)
class UsuarioToken:
    id: int
    perfil: str


def criar_token(usuario_id: int, perfil: str) -> tuple[str, datetime]:
    validar_configuracao()
    agora = datetime.now(timezone.utc)
    expira = agora + timedelta(minutes=TOKEN_MINUTES)
    claims = {
        "sub": str(usuario_id), "perfil": perfil, "iat": agora,
        "exp": expira, "iss": JWT_ISSUER,
    }
    return jwt.encode(claims, JWT_SECRET, algorithm=JWT_ALGORITHM), expira


def decodificar_token(token: str) -> UsuarioToken:
    validar_configuracao()
    try:
        claims = jwt.decode(
            token, JWT_SECRET, algorithms=[JWT_ALGORITHM], issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "sub", "iss", "perfil"]},
        )
        perfil = claims["perfil"]
        if perfil not in {"solicitante", "tecnico", "admin"}:
            raise ValueError("perfil inválido")
        return UsuarioToken(id=int(claims["sub"]), perfil=perfil)
    except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def usuario_atual(authorization: str | None = Header(default=None)) -> UsuarioToken:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Faça login para continuar.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decodificar_token(authorization.split(" ", 1)[1].strip())


def exigir_perfil(*perfis: str):
    def verificar(usuario: UsuarioToken = Depends(usuario_atual)) -> UsuarioToken:
        if usuario.perfil not in perfis:
            raise HTTPException(status_code=403, detail="Você não tem permissão para esta ação.")
        return usuario
    return verificar
