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
# .env local (opcional)
# ----------------------------------------------------------------------------
def _carregar_dotenv(caminho=None):
    """Le um .env simples (KEY=VALUE) para dentro de os.environ.

    Serve so ao desenvolvimento local: em producao (Cloud Run) as variaveis vem
    do proprio servico / Secret Manager e o arquivo nem existe. Por isso usa
    setdefault — o ambiente real SEMPRE vence o arquivo. Chaves sem valor sao
    ignoradas, para nao mascarar os defaults do codigo com string vazia.
    """
    caminho = caminho or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(caminho):
        return
    with open(caminho, encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, _, valor = linha.partition("=")
            valor = valor.strip().strip('"').strip("'")
            if valor:
                os.environ.setdefault(chave.strip(), valor)


_carregar_dotenv()  # antes de qualquer os.environ.get abaixo

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
# Vertex/Storage/Firestore autenticam por Service Account (ADC), NAO por API key.
# Chave em arquivo e apenas UMA das formas de entregar essa identidade.
DEFAULT_SA_KEY = r"C:\Users\Victor_figueiredo\Documents\Atacado\sa_key.json"


def ensure_credentials():
    """Garante que exista uma credencial utilizavel (ADC). Retorna o caminho da
    chave, ou None quando o ADC vem do ambiente (sem arquivo).

    Ordem:
      1) GOOGLE_APPLICATION_CREDENTIALS ja definida — vence sempre.
      2) Chave no caminho padrao (DEFAULT_SA_KEY) — conveniencia local.
      3) ADC do ambiente — SA anexada ao Cloud Run (metadata server) ou
         `gcloud auth application-default login`. NAO ha arquivo aqui, e e o
         caminho correto em producao: exigir chave quebraria o deploy.
    So falha se nenhum dos tres existir.
    """
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return os.environ["GOOGLE_APPLICATION_CREDENTIALS"]

    if os.path.exists(DEFAULT_SA_KEY):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = DEFAULT_SA_KEY
        return DEFAULT_SA_KEY

    import google.auth
    from google.auth.exceptions import DefaultCredentialsError

    try:
        google.auth.default()
    except DefaultCredentialsError as e:
        raise RuntimeError(
            "Credencial nao encontrada. Em producao, anexe uma Service Account ao "
            "Cloud Run. Localmente, defina GOOGLE_APPLICATION_CREDENTIALS, coloque "
            f"a chave em {DEFAULT_SA_KEY}, ou rode "
            "`gcloud auth application-default login`."
        ) from e
    return None


# Diretorio local com as saidas do gerar_dataset.py (relativo a este arquivo).
BUILD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build")
STAGING_DIR = os.path.join(BUILD_DIR, "staging")

# URIs derivados (para conveniencia)
GCS_STAGING_URI = f"gs://{BUCKET_NAME}/{STAGING_PREFIX}"
GCS_TRAIN_URI = f"gs://{BUCKET_NAME}/{DATA_PREFIX}/train.jsonl"
GCS_VALIDATION_URI = f"gs://{BUCKET_NAME}/{DATA_PREFIX}/validation.jsonl"
