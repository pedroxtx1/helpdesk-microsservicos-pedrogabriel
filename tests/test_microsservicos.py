from fastapi.testclient import TestClient
from sqlalchemy import select

from services.usuarios.main import app as usuarios_app, SessionLocal as UsuariosDB, Usuario, UsuarioCriar, criar_usuario
from services.inventario.main import app as inventario_app
from services.chamados.main import app as chamados_app, SessionLocal as ChamadosDB, Chamado
from services.notificacoes.main import app as notificacoes_app


def criar_conta(db, nome, email, perfil):
    return criar_usuario(db, UsuarioCriar(nome=nome, email=email, perfil=perfil, senha="Senha1234"))


def test_servicos_exigem_token_e_rejeitam_token_falso():
    with TestClient(usuarios_app) as u, TestClient(inventario_app) as i, \
         TestClient(chamados_app) as c, TestClient(notificacoes_app) as n:
        assert u.get("/usuarios").status_code == 401
        assert i.get("/ativos").status_code == 401
        assert c.get("/chamados").status_code == 401
        assert n.get("/notificacoes").status_code == 401
        cab = {"Authorization": "Bearer token.falso.invalido"}
        assert u.get("/usuarios", headers=cab).status_code == 401
        assert i.get("/ativos", headers=cab).status_code == 401
        assert c.get("/chamados", headers=cab).status_code == 401
        assert n.get("/notificacoes", headers=cab).status_code == 401


def test_login_e_permissoes_por_perfil():
    with TestClient(usuarios_app) as u, TestClient(inventario_app) as i:
        with UsuariosDB() as db:
            if not db.scalar(select(Usuario.id)):
                criar_conta(db, "Admin", "admin@teste.com", "admin")
                criar_conta(db, "Ana", "ana@teste.com", "solicitante")
                criar_conta(db, "Bruno", "bruno@teste.com", "solicitante")
                criar_conta(db, "Carla", "carla@teste.com", "tecnico")
        login = u.post("/auth/login", json={"email": "ana@teste.com", "senha": "Senha1234"})
        assert login.status_code == 200
        ana = {"Authorization": "Bearer " + login.json()["access_token"]}
        assert u.get("/usuarios", headers=ana).status_code == 200
        assert "email" not in u.get("/usuarios", headers=ana).json()[0]
        assert i.post("/ativos", headers=ana, json={
            "patrimonio": "PAT-TESTE", "tipo": "computador", "descricao": "Desktop", "setor": "TI"
        }).status_code == 403


def test_solicitante_nao_acessa_chamado_de_outra_pessoa():
    with TestClient(usuarios_app) as u, TestClient(chamados_app) as c:
        with UsuariosDB() as db:
            if not db.scalar(select(Usuario.id)):
                criar_conta(db, "Ana", "ana@teste.com", "solicitante")
                criar_conta(db, "Bruno", "bruno@teste.com", "solicitante")
        with UsuariosDB() as db:
            ana_id = db.scalar(select(Usuario.id).where(Usuario.email == "ana@teste.com"))
            bruno_id = db.scalar(select(Usuario.id).where(Usuario.email == "bruno@teste.com"))
        from datetime import datetime, timedelta
        with ChamadosDB() as db:
            db.query(Chamado).delete()
            agora = datetime.now()
            db.add(Chamado(titulo="Chamado da Ana", descricao="Teste de privacidade", prioridade="baixa", status="aberto", solicitante_id=ana_id, tecnico_id=None, ativo_id=None, criado_em=agora, prazo_sla=agora + timedelta(hours=72)))
            db.add(Chamado(titulo="Chamado do Bruno", descricao="Teste de privacidade", prioridade="baixa", status="aberto", solicitante_id=bruno_id, tecnico_id=None, ativo_id=None, criado_em=agora, prazo_sla=agora + timedelta(hours=72)))
            db.commit()
        r = u.post("/auth/login", json={"email": "ana@teste.com", "senha": "Senha1234"})
        headers = {"Authorization": "Bearer " + r.json()["access_token"]}
        resposta_lista = c.get("/chamados", headers=headers)
        assert resposta_lista.status_code == 200
        assert all(chamado["solicitante_id"] == r.json()["usuario"]["id"] for chamado in resposta_lista.json())
        with ChamadosDB() as db:
            chamado_bruno_id = db.scalar(select(Chamado.id).where(Chamado.solicitante_id == bruno_id))
        assert c.get(f"/chamados/{chamado_bruno_id}", headers=headers).status_code == 404


def test_rotas_de_saude_de_cada_servico():
    with TestClient(usuarios_app) as u, TestClient(inventario_app) as i, \
         TestClient(chamados_app) as c, TestClient(notificacoes_app) as n:
        assert u.get("/saude").json()["status"] == "ok"
        assert i.get("/saude").json()["status"] == "ok"
        assert c.get("/saude").json()["status"] == "ok"
        assert n.get("/saude").json()["status"] == "ok"


def test_chamado_com_timeout_de_inventario_retorna_503(monkeypatch):
    from services.chamados import main as chamados_module
    with TestClient(usuarios_app) as u, TestClient(chamados_app) as c:
        login = u.post("/auth/login", json={"email": "ana@teste.com", "senha": "Senha1234"})
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        monkeypatch.setattr(chamados_module, "INVENTARIO_URL", "http://127.0.0.1:1")
        monkeypatch.setattr(chamados_module, "TIMEOUT_SERVICOS", 0.2)
        resposta = c.post("/chamados", headers=headers, json={
            "titulo": "Falha ao acessar ativo", "descricao": "Teste de timeout do inventário.",
            "prioridade": "baixa", "ativo_id": 999,
        })
        assert resposta.status_code == 503
        assert "Inventário indisponível" in resposta.json()["detail"]


def test_fluxo_de_chamado_sla_atribuicao_resolucao_e_fechamento(monkeypatch):
    from datetime import datetime
    from services.chamados import main as chamados_module

    def servico_falso(base_url, rota, authorization, nome):
        if rota.startswith("/ativos/"):
            return {"id": 1, "patrimonio": "PAT-TESTE", "tipo": "computador", "descricao": "Desktop", "setor": "TI"}
        if rota.startswith("/usuarios/"):
            return {"id": 4, "nome": "Carla", "perfil": "tecnico"}
        raise AssertionError("Rota externa inesperada")

    monkeypatch.setattr(chamados_module, "chamada_externa", servico_falso)
    with TestClient(usuarios_app) as u, TestClient(chamados_app) as c:
        ana = u.post("/auth/login", json={"email": "ana@teste.com", "senha": "Senha1234"}).json()
        carla = u.post("/auth/login", json={"email": "carla@teste.com", "senha": "Senha1234"}).json()
        h_ana = {"Authorization": "Bearer " + ana["access_token"]}
        h_carla = {"Authorization": "Bearer " + carla["access_token"]}
        r = c.post("/chamados", headers=h_ana, json={
            "titulo": "Computador sem iniciar", "descricao": "O computador não liga.",
            "prioridade": "critica", "ativo_id": 1,
        })
        assert r.status_code == 201
        chamado = r.json()
        delta = datetime.fromisoformat(chamado["prazo_sla"]) - datetime.fromisoformat(chamado["criado_em"])
        assert round(delta.total_seconds() / 3600) == 4
        cid = chamado["id"]
        atribuido = c.patch(f"/chamados/{cid}/atribuir", headers=h_carla, json={"tecnico_id": 4})
        assert atribuido.status_code == 200 and atribuido.json()["status"] == "em_atendimento"
        comentario = c.post(f"/chamados/{cid}/comentarios", headers=h_carla, json={"texto": "Estou verificando o equipamento."})
        assert comentario.status_code == 201 and comentario.json()["autor_id"] == carla["usuario"]["id"]
        resolvido = c.patch(f"/chamados/{cid}/status", headers=h_carla, json={"status": "resolvido"})
        assert resolvido.status_code == 200
        fechado = c.patch(f"/chamados/{cid}/status", headers=h_ana, json={"status": "fechado"})
        assert fechado.status_code == 200 and fechado.json()["status"] == "fechado"
        comentario = c.post(f"/chamados/{cid}/comentarios", headers=h_ana, json={"texto": "Obrigado."})
        assert comentario.status_code == 422


def test_ids_de_outros_servicos_nao_sao_chaves_estrangeiras():
    from services.chamados.main import Chamado as ModeloChamado
    assert not ModeloChamado.__table__.c.solicitante_id.foreign_keys
    assert not ModeloChamado.__table__.c.tecnico_id.foreign_keys
    assert not ModeloChamado.__table__.c.ativo_id.foreign_keys


def test_login_erro_mesmo_para_senha_incorreta_ou_email_inexistente():
    with TestClient(usuarios_app) as u:
        criar = u.post("/auth/login", json={"email": "email-inexistente@teste.com", "senha": "SenhaErrada123"})
        assert criar.status_code == 401
        assert criar.json()["detail"] == "E-mail ou senha inválidos."
        errado = u.post("/auth/login", json={"email": "ana@teste.com", "senha": "SenhaErrada123"})
        assert errado.status_code == 401
        assert errado.json()["detail"] == criar.json()["detail"]


def test_token_assinado_com_outro_segredo_e_token_alg_none_sao_rejeitados():
    import jwt
    from datetime import datetime, timedelta, timezone
    with TestClient(usuarios_app) as u:
        agora = datetime.now(timezone.utc)
        claims = {"sub": "1", "perfil": "admin", "iss": "helpdesk-ti-microsservicos", "iat": agora, "exp": agora + timedelta(hours=1)}
        tokens = [
            jwt.encode(claims, "segredo-diferente-de-32-caracteres-123456", algorithm="HS256"),
            jwt.encode(claims, key=None, algorithm="none"),
        ]
        for token in tokens:
            resposta = u.get("/usuarios", headers={"Authorization": f"Bearer {token}"})
            assert resposta.status_code == 401


def test_senha_fraca_e_campos_extras_sao_recusados():
    with TestClient(usuarios_app) as u, TestClient(chamados_app) as c:
        login = u.post("/auth/login", json={"email": "ana@teste.com", "senha": "Senha1234"}).json()
        headers = {"Authorization": "Bearer " + login["access_token"]}
        login_admin = u.post("/auth/login", json={"email": "admin@teste.com", "senha": "Senha1234"}).json()
        headers_admin = {"Authorization": "Bearer " + login_admin["access_token"]}
        fraca = u.post("/usuarios", headers=headers_admin, json={
            "nome": "Eva", "email": "eva-fraca@teste.com", "perfil": "solicitante", "senha": "abcdefgh"
        })
        extra = c.post("/chamados", headers=headers, json={
            "titulo": "Teste extra", "descricao": "Campos não permitidos", "prioridade": "baixa", "solicitante_id": 999
        })
        assert fraca.status_code == 422
        proibido = u.post("/usuarios", headers=headers, json={
            "nome": "Eva", "email": "eva-proibida@teste.com", "perfil": "admin", "senha": "Forte1234"
        })
        assert proibido.status_code == 403
        assert extra.status_code == 422


def test_limite_de_tamanho_do_corpo_http():
    with TestClient(inventario_app) as i:
        corpo = '{"descricao":"' + ('x' * 70000) + '"}'
        resposta = i.post("/ativos", content=corpo, headers={"Content-Type": "application/json"})
        assert resposta.status_code == 413


def test_servico_de_notificacoes_retorna_apenas_avisos_do_usuario():
    from datetime import datetime
    from services.notificacoes.main import SessionLocal as NotificacoesDB, Notificacao
    with TestClient(usuarios_app) as u, TestClient(notificacoes_app) as n:
        with UsuariosDB() as db:
            ana_id = db.scalar(select(Usuario.id).where(Usuario.email == "ana@teste.com"))
            bruno_id = db.scalar(select(Usuario.id).where(Usuario.email == "bruno@teste.com"))
        with NotificacoesDB() as db:
            db.query(Notificacao).delete()
            db.add(Notificacao(tipo="ChamadoAberto", destinatario_id=ana_id, mensagem="Aviso da Ana", criada_em=datetime.now(), chamado_id=10))
            db.add(Notificacao(tipo="ChamadoAberto", destinatario_id=bruno_id, mensagem="Aviso do Bruno", criada_em=datetime.now(), chamado_id=11))
            db.commit()
        login = u.post("/auth/login", json={"email": "ana@teste.com", "senha": "Senha1234"}).json()
        headers = {"Authorization": "Bearer " + login["access_token"]}
        resposta = n.get("/notificacoes", headers=headers)
        assert resposta.status_code == 200
        assert len(resposta.json()) == 1 and resposta.json()[0]["mensagem"] == "Aviso da Ana"


def test_bloqueia_login_apos_cinco_tentativas_erradas():
    with UsuariosDB() as db:
        if not db.scalar(select(Usuario.id).where(Usuario.email == "bloqueio@teste.com")):
            criar_conta(db, "Usuário Bloqueio", "bloqueio@teste.com", "solicitante")
    with TestClient(usuarios_app) as u:
        for _ in range(5):
            assert u.post("/auth/login", json={"email": "bloqueio@teste.com", "senha": "Errada123"}).status_code == 401
        assert u.post("/auth/login", json={"email": "bloqueio@teste.com", "senha": "Senha1234"}).status_code == 429


def test_token_expirado_e_rejeitado():
    import jwt
    from datetime import datetime, timedelta, timezone
    with TestClient(usuarios_app) as u:
        agora = datetime.now(timezone.utc) - timedelta(hours=2)
        vencido = jwt.encode({
            "sub": "1", "perfil": "admin", "iss": "helpdesk-ti-microsservicos",
            "iat": agora, "exp": agora + timedelta(minutes=1),
        }, "teste-local-seguro-nao-usar-em-producao-123456789", algorithm="HS256")
        assert u.get("/usuarios", headers={"Authorization": f"Bearer {vencido}"}).status_code == 401


def test_hash_de_senha_nao_guarda_a_senha_em_texto():
    with UsuariosDB() as db:
        usuario = db.scalar(select(Usuario).where(Usuario.email == "ana@teste.com"))
        assert usuario is not None
        assert "Senha1234" not in usuario.senha_hash
        assert usuario.senha_hash.startswith("scrypt$")


def test_metadata_dos_bancos_sao_isolados_por_servico():
    from services.usuarios.main import Base as UsuariosBase
    from services.inventario.main import Base as InventarioBase
    from services.chamados.main import Base as ChamadosBase
    from services.notificacoes.main import Base as NotificacoesBase
    assert set(UsuariosBase.metadata.tables) == {"usuarios"}
    assert set(InventarioBase.metadata.tables) == {"ativos"}
    assert set(ChamadosBase.metadata.tables) == {"chamados", "comentarios"}
    assert set(NotificacoesBase.metadata.tables) == {"notificacoes"}
