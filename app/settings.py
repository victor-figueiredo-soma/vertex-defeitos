#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Configuracao de runtime do app (webhook + inferencia + resposta).

Reusa config.py (raiz) para tudo que e GCP/modelo — fonte unica. Aqui ficam so as
variaveis proprias do app: Azure/Graph, webhook e alertas.

No Railway nao existe arquivo .env: as variaveis vem do painel do servico. O
carregador de config.py usa os.environ.setdefault, entao o ambiente real sempre
tem prioridade sobre um .env local de desenvolvimento.
"""

import json
import os
import tempfile

import config as _core

# ----------------------------------------------------------------------------
# GCP / modelo (herdado da raiz - fonte unica)
# ----------------------------------------------------------------------------
# Aceita os dois nomes: GCP_PROJECT (historico deste repo) e GCP_PROJECT_ID
# (spec do deploy). O segundo vence se definido.
PROJECT_ID = os.environ.get("GCP_PROJECT_ID") or _core.PROJECT_ID
LOCATION = _core.LOCATION
CONFIDENCE_THRESHOLD = _core.CONFIDENCE_THRESHOLD

# Endpoint do modelo afinado. Aceita VERTEX_ENDPOINT_ID (spec) como resource name
# completo OU como ID numerico (montado na multi-region 'us', onde o SFT de
# Gemini 3.x entrega o modelo).
_ep = os.environ.get("VERTEX_ENDPOINT_ID", "").strip() or _core.TUNED_ENDPOINT
if _ep and not _ep.startswith("projects/"):
    _ep = f"projects/{PROJECT_ID}/locations/us/endpoints/{_ep}"
TUNED_ENDPOINT = _ep

# ----------------------------------------------------------------------------
# Credencial GCP no Railway
# ----------------------------------------------------------------------------
# O Railway nao tem metadata server nem disco pre-populado; a chave da Service
# Account vai INTEIRA numa variavel de ambiente (GOOGLE_APPLICATION_CREDENTIALS_JSON).
# Materializamos num arquivo temporario e apontamos GOOGLE_APPLICATION_CREDENTIALS
# para ele, que e o que o ADC dos SDKs Google sabe consumir.


def ensure_credentials():
    """Garante ADC utilizavel. Retorna o caminho da credencial ou None (ADC do ambiente)."""
    raw = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()
    if raw and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        # Valida que e JSON antes de gravar - erro aqui deve falhar alto no boot,
        # nao na primeira inferencia.
        json.loads(raw)
        fd, caminho = tempfile.mkstemp(prefix="gcp_sa_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(raw)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = caminho
        return caminho
    return _core.ensure_credentials()


# ----------------------------------------------------------------------------
# Azure / Microsoft Graph
# ----------------------------------------------------------------------------
AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "").strip()
AZURE_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "").strip()
AZURE_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "").strip()

# Caixa monitorada (app permissions permitem /users/{mailbox}).
GRAPH_MAILBOX = os.environ.get("GRAPH_MAILBOX", "dados@somagrupo.com.br").strip()

# ----------------------------------------------------------------------------
# Webhook
# ----------------------------------------------------------------------------
# URL publica do servico no Railway (ex.: https://xxx.up.railway.app). Usada para
# registrar a subscription no Graph.
WEBHOOK_BASE_URL = os.environ.get("WEBHOOK_BASE_URL", "").rstrip("/")

# Token combinado na criacao da subscription. O Graph devolve esse valor em cada
# notificacao; requisicao cujo clientState nao bater e REJEITADA (403).
WEBHOOK_CLIENT_STATE = os.environ.get("WEBHOOK_CLIENT_STATE", "").strip()

# ----------------------------------------------------------------------------
# Alertas e servidor
# ----------------------------------------------------------------------------
# Falhas de processamento (Vertex fora do ar, Graph com erro, e-mail sem imagem
# repetido...) sao enviadas para este endereco.
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "").strip()

PORT = int(os.environ.get("PORT", "8080"))


def validar_boot():
    """Falha ALTO no startup se faltar configuracao critica.

    Melhor recusar o boot do que aceitar webhook e falhar silenciosamente na
    primeira notificacao real.
    """
    faltando = [nome for nome, valor in (
        ("AZURE_TENANT_ID", AZURE_TENANT_ID),
        ("AZURE_CLIENT_ID", AZURE_CLIENT_ID),
        ("AZURE_CLIENT_SECRET", AZURE_CLIENT_SECRET),
        ("WEBHOOK_CLIENT_STATE", WEBHOOK_CLIENT_STATE),
        ("TUNED_ENDPOINT/VERTEX_ENDPOINT_ID", TUNED_ENDPOINT),
    ) if not valor]
    if faltando:
        raise RuntimeError(f"Variaveis de ambiente ausentes: {', '.join(faltando)}")
