"""Hash de senha scrypt com sal aleatório, mantido do projeto original."""
import base64, hashlib, hmac, secrets
N, R, P, TAMANHO = 2**14, 8, 1, 32

def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()

def gerar_hash(senha: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(senha.encode(), salt=salt, n=N, r=R, p=P, dklen=TAMANHO)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"

def conferir(senha: str, guardado: str) -> bool:
    try:
        _, n, r, p, salt, expected = guardado.split("$")
        digest = hashlib.scrypt(senha.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=TAMANHO)
        return hmac.compare_digest(digest, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False

HASH_FALSO = gerar_hash(secrets.token_urlsafe(16))
