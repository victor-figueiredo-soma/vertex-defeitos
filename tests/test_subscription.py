#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes de subscription.garantir(): a manutencao automatica do webhook.

O que esta em jogo: subscription de mail do Graph expira em ~3 dias e, uma vez
expirada, NAO pode ser renovada. Se esta logica falhar, o fluxo automatico morre
em silencio - nenhum erro, nenhum e-mail, so nada acontecendo.
"""

import datetime

from app import subscription

URL = "https://exemplo.up.railway.app"


def _cfg(monkeypatch):
    monkeypatch.setattr(subscription.settings, "WEBHOOK_BASE_URL", URL)
    monkeypatch.setattr(subscription.settings, "WEBHOOK_CLIENT_STATE", "tok")


def _sub(horas_para_expirar, id_="sub-1", url=None):
    exp = (datetime.datetime.now(datetime.timezone.utc)
           + datetime.timedelta(hours=horas_para_expirar))
    return {"id": id_,
            "notificationUrl": url or f"{URL}/webhook",
            "expirationDateTime": exp.strftime("%Y-%m-%dT%H:%M:%SZ")}


def _mock(monkeypatch, subs, criar=None, renovar=None):
    chamadas = []
    monkeypatch.setattr(subscription.graph_client, "list_subscriptions",
                        lambda: subs)
    monkeypatch.setattr(subscription.graph_client, "create_subscription",
                        criar or (lambda exp: chamadas.append("criar")
                                  or {"id": "nova"}))
    monkeypatch.setattr(subscription.graph_client, "renew_subscription",
                        renovar or (lambda sid, exp: chamadas.append("renovar")
                                    or {"expirationDateTime": exp}))
    return chamadas


def test_sem_nenhuma_cria(monkeypatch):
    _cfg(monkeypatch)
    chamadas = _mock(monkeypatch, [])
    assert subscription.garantir()[0] == "criada"
    assert chamadas == ["criar"]


def test_com_folga_nao_faz_nada(monkeypatch):
    _cfg(monkeypatch)
    chamadas = _mock(monkeypatch, [_sub(48)])
    assert subscription.garantir()[0] == "ok"
    assert chamadas == [], "nao deveria ter tocado no Graph"


def test_perto_de_expirar_renova(monkeypatch):
    """MARGEM_RENOVACAO e 12h: com 6h restantes, tem que renovar."""
    _cfg(monkeypatch)
    chamadas = _mock(monkeypatch, [_sub(6)])
    assert subscription.garantir()[0] == "renovada"
    assert chamadas == ["renovar"]


def test_forcar_renova_mesmo_com_folga(monkeypatch):
    _cfg(monkeypatch)
    chamadas = _mock(monkeypatch, [_sub(48)])
    assert subscription.garantir(forcar=True)[0] == "renovada"
    assert chamadas == ["renovar"]


def test_renew_que_falha_cria_outra(monkeypatch):
    """Subscription expirada: o Graph a apagou e o PATCH da 404. Recriar e a saida
    - insistir no PATCH nunca funcionaria."""
    _cfg(monkeypatch)
    criadas = []

    def renew_404(sid, exp):
        raise RuntimeError("404 Not Found")

    _mock(monkeypatch, [_sub(1)],
          criar=lambda exp: criadas.append("criar") or {"id": "recriada"},
          renovar=renew_404)
    acao, detalhe = subscription.garantir()
    assert acao == "criada"
    assert detalhe == "recriada"
    assert criadas == ["criar"]


def test_ignora_subscription_de_outro_projeto(monkeypatch):
    """O tenant Azure e compartilhado: renovar a subscription de outro projeto
    seria interferencia."""
    _cfg(monkeypatch)
    alheia = _sub(48, id_="de-outro",
                  url="https://outro-servico.up.railway.app/graph-webhook")
    chamadas = _mock(monkeypatch, [alheia])
    # Nao reconhece nenhuma como nossa -> cria a nossa, sem tocar na alheia.
    assert subscription.garantir()[0] == "criada"
    assert chamadas == ["criar"]


def test_sem_base_url_nao_gerencia(monkeypatch):
    monkeypatch.setattr(subscription.settings, "WEBHOOK_BASE_URL", "")
    monkeypatch.setattr(subscription.settings, "WEBHOOK_CLIENT_STATE", "tok")
    assert subscription.garantir()[0] == "ignorada"


def test_sem_client_state_nao_gerencia(monkeypatch):
    monkeypatch.setattr(subscription.settings, "WEBHOOK_BASE_URL", URL)
    monkeypatch.setattr(subscription.settings, "WEBHOOK_CLIENT_STATE", "")
    assert subscription.garantir()[0] == "ignorada"


def test_graph_fora_do_ar_nao_levanta(monkeypatch):
    """Chamada de dentro de loop de fundo: levantar mataria o loop e a
    manutencao pararia para sempre."""
    _cfg(monkeypatch)

    def explode():
        raise RuntimeError("Graph fora do ar")

    monkeypatch.setattr(subscription.graph_client, "list_subscriptions", explode)
    assert subscription.garantir()[0] == "ignorada"


def test_expiracao_ilegivel_renova_por_seguranca(monkeypatch):
    """Sem saber quanto falta, renovar e mais seguro que assumir folga."""
    _cfg(monkeypatch)
    s = _sub(48)
    s["expirationDateTime"] = "data-invalida"
    chamadas = _mock(monkeypatch, [s])
    assert subscription.garantir()[0] == "renovada"
    assert chamadas == ["renovar"]


def test_margem_maior_que_intervalo_do_loop():
    """Invariante de desenho: a margem tem que cobrir varias checagens perdidas.
    Se alguem aumentar o intervalo do loop sem aumentar a margem, uma queda de
    algumas horas deixaria a subscription expirar."""
    from app import main
    assert (subscription.MARGEM_RENOVACAO.total_seconds()
            >= 3 * main.INTERVALO_MANUTENCAO_S)
