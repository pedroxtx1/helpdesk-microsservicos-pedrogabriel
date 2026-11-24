"""Publicação tolerante a falhas dos eventos de domínio na fila Redis."""
import json, logging, os

log = logging.getLogger("helpdesk.eventos")
QUEUE = os.getenv("FILA_EVENTOS", "helpdesk:eventos")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

def publicar_evento(tipo: str, destinatario_id: int, mensagem: str, chamado_id: int) -> None:
    evento = {"tipo": tipo, "destinatario_id": destinatario_id, "mensagem": mensagem, "chamado_id": chamado_id}
    try:
        from redis import Redis
        fila = Redis.from_url(REDIS_URL, socket_connect_timeout=0.4, socket_timeout=0.4, decode_responses=True)
        fila.rpush(QUEUE, json.dumps(evento, ensure_ascii=False))
        fila.close()
    except Exception:
        # Disponibilidade do sistema de chamados não depende do serviço de notificações.
        log.warning("Não foi possível publicar o evento %s; o chamado foi salvo normalmente.", tipo)
