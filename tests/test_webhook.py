#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes da rota do webhook: handshake, clientState estrito, 202 rapido.

Tudo isolado em fixture com monkeypatch. A versao anterior mutava
`settings.ensure_credentials` e as constantes no nivel do modulo, o que vazava
para os outros arquivos de teste (test_credenciais quebrava por causa disso) -
poluicao classica de estado global entre testes.
"""

import pytest
from fastapi.testclient import TestClient

from app import main as webhook_main

CLIENT_STATE = "segredo-de-teste"


@pytest.fixture
def agendadas(monkeypatch):
    """Config minima para o startup passar, sem tocar em rede nem em credencial.

    Devolve a lista de message_ids que o webhook agendou para processamento.
    """
    st = webhook_main.settings
    monkeypatch.setattr(st, "AZURE_TENANT_ID", "t")
    monkeypatch.setattr(st, "AZURE_CLIENT_ID", "c")
    monkeypatch.setattr(st, "AZURE_CLIENT_SECRET", "s")
    monkeypatch.setattr(st, "GRAPH_MAILBOX", "caixa@teste.com")
    monkeypatch.setattr(st, "TUNED_ENDPOINT",
                        "projects/x/locations/us/endpoints/1")
    monkeypatch.setattr(st, "ALERT_EMAIL", "erros@teste.com")
    monkeypatch.setattr(st, "REVIEW_EMAIL", "revisao@teste.com")
    monkeypatch.setattr(st, "WEBHOOK_CLIENT_STATE", CLIENT_STATE)
    monkeypatch.setattr(st, "ensure_credentials", lambda: None)

    ids = []
    monkeypatch.setattr(webhook_main.processor, "processar_mensagem", ids.append)
    # O loop de manutencao da subscription nao deve rodar em teste.
    monkeypatch.setattr(webhook_main.subscription, "garantir",
                        lambda *a, **kw: ("ignorada", "teste"))
    return ids


@pytest.fixture
def client(agendadas):
    with TestClient(webhook_main.app) as c:
        yield c


def _notif(client_state=CLIENT_STATE, message_id="AAMk123"):
    return {"value": [{
        "clientState": client_state,
        "resourceData": {"id": message_id},
        "changeType": "created",
    }]}


def test_handshake_devolve_token_em_texto_puro(client):
    r = client.post("/webhook?validationToken=abc%20123")
    assert r.status_code == 200
    assert r.text == "abc 123"
    assert r.headers["content-type"].startswith("text/plain")


def test_notificacao_valida_da_202_e_agenda(client, agendadas):
    r = client.post("/webhook", json=_notif())
    assert r.status_code == 202
    assert agendadas == ["AAMk123"]


def test_client_state_errado_da_403_e_nao_agenda(client, agendadas):
    r = client.post("/webhook", json=_notif(client_state="intruso"))
    assert r.status_code == 403
    assert agendadas == []


def test_payload_sem_json_da_400(client):
    r = client.post("/webhook", content=b"nao e json",
                    headers={"content-type": "application/json"})
    assert r.status_code == 400


def test_item_sem_message_id_e_ignorado(client, agendadas):
    payload = {"value": [{"clientState": CLIENT_STATE, "resourceData": {}}]}
    r = client.post("/webhook", json=payload)
    assert r.status_code == 202
    assert agendadas == []


def test_lote_com_itens_validos_e_invalidos_processa_os_validos(client, agendadas):
    payload = {"value": [
        {"clientState": "intruso", "resourceData": {"id": "mau"}},
        {"clientState": CLIENT_STATE, "resourceData": {"id": "bom"}},
    ]}
    r = client.post("/webhook", json=payload)
    assert r.status_code == 202
    assert agendadas == ["bom"]


def test_health(client):
    assert client.get("/health").json() == {"ok": True}
