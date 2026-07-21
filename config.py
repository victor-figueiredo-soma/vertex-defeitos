#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Configuracao compartilhada dos scripts de nuvem (upload + tuning).

Todos os valores podem ser sobrescritos por variaveis de ambiente, para nao
precisar editar codigo entre ambientes. Os defaults vem da configuracao
verificada do projeto (soma-ai-hub).
"""

import os

# ----------------------------------------------------------------------------
# Projeto / regiao GCP
# ----------------------------------------------------------------------------
PROJECT_ID = os.environ.get("GCP_PROJECT", "soma-ai-hub")

# ATENCAO - REGIAO DO FINE-TUNING:
# O fine-tuning supervisionado do Gemini NAO esta disponivel em todas as regioes.
# 'us-central1' e a regiao padrao e a mais garantida para o SFT do Gemini.
# A memoria do projeto registra 'us-west1' como regiao habilitada para chamadas
# generateContent (inferencia), mas o tuning pode nao existir la. Se quiser usar
# us-west1, valide antes se o modelo-base aceita tuning nessa regiao.
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")

# ----------------------------------------------------------------------------
# Cloud Storage
# ----------------------------------------------------------------------------
# Nome do bucket (SEM 'gs://'). Deve casar com o GCS_PREFIX do gerar_dataset.py,
# pois os fileUri dentro dos JSONL ja apontam para 'gs://azzas-defeitos/staging/...'.
BUCKET_NAME = os.environ.get("GCS_BUCKET", "azzas-defeitos")
STAGING_PREFIX = "staging"   # onde ficam as imagens (casa com os fileUri do JSONL)
DATA_PREFIX = "data"         # onde ficam train.jsonl / validation.jsonl

# ----------------------------------------------------------------------------
# Modelo / fine-tuning
# ----------------------------------------------------------------------------
BASE_MODEL = os.environ.get("VERTEX_BASE_MODEL", "gemini-2.5-flash")
TUNED_MODEL_DISPLAY_NAME = os.environ.get(
    "VERTEX_TUNED_NAME", "validador-defeitos-azzas")

# ----------------------------------------------------------------------------
# Credenciais (Service Account)
# ----------------------------------------------------------------------------
# Vertex/Storage autenticam por Service Account (ADC), NAO por API key.
# Se GOOGLE_APPLICATION_CREDENTIALS ja estiver setada, ela tem prioridade.
DEFAULT_SA_KEY = r"C:\Users\Victor_figueiredo\Documents\Atacado\sa_key.json"


def ensure_credentials():
    """Garante que o ADC aponte para a chave da Service Account."""
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        if os.path.exists(DEFAULT_SA_KEY):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = DEFAULT_SA_KEY
        else:
            raise RuntimeError(
                "Credencial nao encontrada. Defina GOOGLE_APPLICATION_CREDENTIALS "
                f"ou coloque a chave em {DEFAULT_SA_KEY}."
            )
    return os.environ["GOOGLE_APPLICATION_CREDENTIALS"]


# Diretorio local com as saidas do gerar_dataset.py (relativo a este arquivo).
BUILD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build")
STAGING_DIR = os.path.join(BUILD_DIR, "staging")

# URIs derivados (para conveniencia)
GCS_STAGING_URI = f"gs://{BUCKET_NAME}/{STAGING_PREFIX}"
GCS_TRAIN_URI = f"gs://{BUCKET_NAME}/{DATA_PREFIX}/train.jsonl"
GCS_VALIDATION_URI = f"gs://{BUCKET_NAME}/{DATA_PREFIX}/validation.jsonl"
