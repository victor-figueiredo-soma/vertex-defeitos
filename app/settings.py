#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Configuracao de RUNTIME da aplicacao (inferencia, ingestao, re-treino).

Distinta de config.py (raiz), que guarda a config de TREINAMENTO/GCP. Aqui ficam
os parametros que variam por ambiente e sao lidos de variaveis de ambiente
(em producao, injetadas pelo Secret Manager / Cloud Run). Nada de segredo fica
hardcoded.

Reusa de config.py: PROJECT_ID, LOCATION, BUCKET_NAME e ensure_credentials().
"""

import os

# Reuso da config de treinamento/GCP (mesmo projeto, regiao e bucket).
import config as _train_config

# ----------------------------------------------------------------------------
# GCP (herdado da config de treinamento)
# ----------------------------------------------------------------------------
PROJECT_ID = _train_config.PROJECT_ID
LOCATION = _train_config.LOCATION
BUCKET_NAME = _train_config.BUCKET_NAME
ensure_credentials = _train_config.ensure_credentials

# ----------------------------------------------------------------------------
# Modelo / inferencia
# ----------------------------------------------------------------------------
# Endpoint do modelo afinado. Prioridade:
#   1) env TUNED_ENDPOINT (util para teste local rapido)
#   2) doc Firestore config/active_model (fonte da verdade em producao;
#      atualizado automaticamente apos cada re-treino)
TUNED_ENDPOINT = os.environ.get("TUNED_ENDPOINT")

# Modelo-base de fallback (se ainda nao houver modelo afinado, permite testar o
# pipeline com o Gemini base). NAO usar como validador real.
FALLBACK_BASE_MODEL = os.environ.get("VERTEX_BASE_MODEL", "gemini-2.5-flash")

# ----------------------------------------------------------------------------
# Politica de decisao (espelha o system_instruction: confianca < 0.70 = revisao)
# ----------------------------------------------------------------------------
CONFIDENCE_THRESHOLD = float(os.environ.get("CONFIDENCE_THRESHOLD", "0.70"))

# ----------------------------------------------------------------------------
# Re-treino (gatilho por lote)
# ----------------------------------------------------------------------------
# A cada N rotulos CONFIRMADOS POR HUMANO, dispara o re-treino.
RETRAIN_THRESHOLD_INPUTS = int(os.environ.get("RETRAIN_THRESHOLD_INPUTS", "100"))
RETRAIN_TOPIC = os.environ.get("RETRAIN_TOPIC", "retrain-trigger")

# ----------------------------------------------------------------------------
# Persistencia
# ----------------------------------------------------------------------------
FIRESTORE_COLLECTION = os.environ.get("FIRESTORE_COLLECTION", "devolucoes")
FIRESTORE_CONFIG_COLLECTION = os.environ.get("FIRESTORE_CONFIG_COLLECTION", "config")
INBOX_PREFIX = os.environ.get("INBOX_PREFIX", "inbox")  # prefixo GCS das imagens recebidas

# Docs de configuracao no Firestore
ACTIVE_MODEL_DOC = "active_model"
RETRAIN_COUNTER_DOC = "retrain_counter"

# ----------------------------------------------------------------------------
# Ingestao Outlook / Microsoft Graph (credenciais Azure - via Secret Manager)
# ----------------------------------------------------------------------------
AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID")
AZURE_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID")
AZURE_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET")
GRAPH_MAILBOX = os.environ.get("GRAPH_MAILBOX", "dados@somagrupo.com.br")
# Token esperado nas notificacoes do Graph (validacao de origem do webhook).
GRAPH_CLIENT_STATE = os.environ.get("GRAPH_CLIENT_STATE")

# Caminho do system_instruction (mesmo do treino) — fonte unica do prompt.
SYSTEM_INSTRUCTION_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "system_instruction.md",
)
