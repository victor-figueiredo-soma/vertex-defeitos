#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes da rota do webhook: handshake, clientState estrito, 202 rapido."""

import os

os.environ.setdefault("WEBHOOK_CLIENT_STATE", "segredo-de-teste")

from fastapi.testclient import TestClient

from app import main as webhook_main
from app import settings

# O TestClient dispara o startup, que valida config critica; preenche o minimo.
settings.AZURE_TENANT_ID = settings.AZURE_TENANT_ID or "t"
settings.AZURE_CLIENT_ID = settings.AZURE_CLIENT_ID or "c"
settings.AZURE_CLIENT_SECRET = settings.AZURE_CLIENT_SECRET or "s"
settings.WEBHOOK_CLIENT_STATE = "segredo-de-teste"
settings.TUNED_ENDPOINT = settings.TUNED_ENDPOINT or "projects/x/locations/us/endpoints/1"
webhook_main.settings.ensure_credentials = lambda: None

client = TestClient(webhook_main.app)

_capturadas = []


def _fake_processar(message_id):
    _capturadas.append(message_id)


webhook_main.processor.processar_mensagem = _fake_processar


def _notif(client_state="segredo-de-teste", message_id="AAMk123"):
    return {"value": [{
        "clientState": client_state,
        "resourceData": {"id": message_id},
        "changeType": "created",
    }]}


def test_handshake_devolve_token_em_texto_puro():
    r = client.post("/webhook?validationToken=abc%20123")
    assert r.status_code == 200
    assert r.text == "abc 123"
    assert r.headers["content-type"].startswith("text/plain")


def test_notificacao_valida_da_202_e_agenda():
    _capturadas.clear()
    r = client.post("/webhook", json=_notif())
    assert r.status_code == 202
    assert _capturadas == ["AAMk123"]


def test_client_state_errado_da_403_e_nao_agenda():
    _capturadas.clear()
    r = client.post("/webhook", json=_notif(client_state="intruso"))
    assert r.status_code == 403
    assert _capturadas == []


def test_payload_sem_json_da_400():
    r = client.post("/webhook", content=b"nao e json",
                    headers={"content-type": "application/json"})
    assert r.status_code == 400


def test_item_sem_message_id_e_ignorado():
    _capturadas.clear()
    payload = {"value": [{"clientState": "segredo-de-teste", "resourceData": {}}]}
    r = client.post("/webhook", json=payload)
    assert r.status_code == 202
    assert _capturadas == []


def test_health():
    assert client.get("/health").json() == {"ok": True}
