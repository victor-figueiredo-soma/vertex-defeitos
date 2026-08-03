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
# 'us-central1' e a regiao padrao. 'us-west1' tambem ACEITA o SFT do
# gemini-2.5-flash (testado em 2026-07-29: o job foi criado sem erro de regiao),
# mas o job ficou 22h em JOB_STATE_RUNNING sem concluir e foi cancelado — provavel
# contencao de recursos. us-central1 e a regiao mais usada para tuning.
# O bucket 'vertex-defeitos' e multi-region US, entao atende as duas.
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")

# ----------------------------------------------------------------------------
# Cloud Storage
# ----------------------------------------------------------------------------
# Nome do bucket (SEM 'gs://'). Deve casar com o GCS_PREFIX do gerar_dataset.py,
# pois os fileUri dentro dos JSONL ja apontam para 'gs://<bucket>/staging/...'.
# Bucket real do projeto soma-ai-hub: 'vertex-defeitos'.
BUCKET_NAME = os.environ.get("GCS_BUCKET", "vertex-defeitos")
STAGING_PREFIX = "staging"   # onde ficam as imagens (casa com os fileUri do JSONL)
DATA_PREFIX = "data"         # onde ficam train.jsonl / validation.jsonl

# ----------------------------------------------------------------------------
# Modelo / fine-tuning
# ----------------------------------------------------------------------------
# ATENCAO - POR QUE SAIMOS DO gemini-2.5-flash:
# 1. Ele RETIRA em 2026-10-20. Treinar nele hoje e beco sem saida.
# 2. E o unico candidato SEM a linha "Supported endpoint for model tuning" na doc
#    atual; 3.5-flash e 3.1-flash-lite declaram us-central1 e europe-west4. Quatro
#    jobs nossos ficaram em JOB_STATE_RUNNING sem emitir um step - capacidade de
#    tuning de modelo em fim de vida explica isso melhor que qualquer hipotese
#    sobre o dataset (que tem folga de ordens de grandeza em todos os limites).
# 3. gemini-3.1-flash-lite e o replacement OFICIAL do 2.5-flash e o unico dos dois
#    recomendados que aceita tuning (3.5-flash-lite nao aceita). Ganha em visao
#    (MMMU-Pro 76,8% vs 66,7%) e o treino custa 40% menos ($3 vs $5 / 1M tokens).
BASE_MODEL = os.environ.get("VERTEX_BASE_MODEL", "gemini-3.1-flash-lite")
TUNED_MODEL_DISPLAY_NAME = os.environ.get(
    "VERTEX_TUNED_NAME", "validador-defeitos-azzas")

# ATENCAO - HIPERPARAMETROS DO SFT: None = default do Vertex, e isso e deliberado.
# A doc de migracao do Google e explicita: "Don't reuse hyperparameter values from
# previous Gemini versions, because the tuning service is optimized for the latest
# versions." E: "It's recommended to submit your first tuning job without changing
# the hyperparameters."
#
# Tinhamos fixado epochs=3 / lr_mult=2.0 / adapter=4 calibrados para o
# gemini-2.5-flash. Ao migrar para 3.1-flash-lite esses valores deixam de valer -
# por isso voltaram para None. So sobrescrever depois de ter um job que conclua e
# metricas para comparar.
#
# MEDIDO (job 8607385224512274432, gemini-3.1-flash-lite, 166 exemplos): o default do
# Vertex resolveu para adapterSize=2, epochCount=40, learningRateMultiplier=1.0.
# 40 epocas sobre 166 exemplos de alvo templatizado sao 17,5M tokens de treino
# (~US$52) e risco alto de overfitting. Seguir o default e o recomendado no PRIMEIRO
# job de um modelo novo, mas nos proximos vale fixar epochs bem menor - o job de
# us-west1 no 2.5-flash ja marcava train_acc=0,934 no step 1.
#
# Continuam centralizados aqui para criar_tuning_job.py (manual) e run_retrain.py
# (automatico) nao divergirem.
def _int_opcional(nome):
    v = os.environ.get(nome, "").strip()
    return int(v) if v else None


def _float_opcional(nome):
    v = os.environ.get(nome, "").strip()
    return float(v) if v else None


TUNING_EPOCHS = _int_opcional("VERTEX_TUNING_EPOCHS")
TUNING_LR_MULTIPLIER = _float_opcional("VERTEX_TUNING_LR_MULTIPLIER")
TUNING_ADAPTER_SIZE = _int_opcional("VERTEX_TUNING_ADAPTER_SIZE")

# A assinatura do SDK (vertexai.tuning.sft.train) diz Literal[1, 4, 8, 16, 32], mas a
# doc lista 1, 2, 4, 8, 16 para os modelos 3.x - e o proprio Vertex resolveu o default
# do gemini-3.1-flash-lite como ADAPTER_SIZE_TWO (job 8607385224512274432). Ou seja: 2
# e valido e 32 provavelmente nao. A uniao cobre os dois casos; quem valida de verdade
# e o servico.
ADAPTER_SIZES = [1, 2, 4, 8, 16, 32]

# ATENCAO - mediaResolution E A ALAVANCA REAL DE TOKEN DE IMAGEM:
# O custo de uma imagem NAO vem da dimensao em pixels - a doc do Vertex e
# explicita: "This doesn't affect the image dimensions sent to the model".
# Vem deste campo, cujo default e MEDIA_RESOLUTION_HIGH. Medido em
# gemini-2.5-flash com uma foto de 768px:
#     HIGH (default) = 1.290 tokens
#     MEDIUM         =   256 tokens
#     LOW            =    64 tokens
# Reduzir a imagem de 4000px para 768px so leva de 3.354 para 1.290; e este
# campo que corta de verdade.
#
# TEM QUE SER O MESMO no JSONL de treino e na inferencia de producao, senao o
# modelo e servido com um detalhe visual que nunca viu no treino.
#
# O default aqui e HIGH porque e o comportamento em que o baseline de 81% foi
# medido. Baixar para MEDIUM/LOW so depois de passar pelo gate de qualidade
# (avaliar_modelo.py --media-resolution ...), senao o ganho de token vem as
# custas de deteccao sem ninguem perceber.
MEDIA_RESOLUTIONS = ["MEDIA_RESOLUTION_LOW", "MEDIA_RESOLUTION_MEDIUM",
                     "MEDIA_RESOLUTION_HIGH"]
MEDIA_RESOLUTION = os.environ.get("VERTEX_MEDIA_RESOLUTION",
                                  "MEDIA_RESOLUTION_HIGH")

# ----------------------------------------------------------------------------
# Inferencia (consumido por inferencia.py)
# ----------------------------------------------------------------------------
# Endpoint do modelo AFINADO. Vazio = inferencia.py cai no BASE_MODEL, que serve
# para exercitar o pipeline mas NAO e o validador.
#
# ATENCAO: o SFT de Gemini 3.x entrega o modelo numa MULTI-REGION
# ('projects/N/locations/us/endpoints/ID'), nao na regiao do job. O SDK legado
# `vertexai` rejeita location='us', por isso inferencia.py usa google-genai para
# modelo afinado. Ver inferencia._chamar_genai.
TUNED_ENDPOINT = os.environ.get("TUNED_ENDPOINT", "").strip()

# Orcamento de "thinking". A doc de SFT pede o minimo em tarefas afinadas:
# melhora performance e reduz custo. A doc chama isso de thinking_level=MINIMAL
# nos Gemini 3.x, mas o SDK legado instalado (aiplatform 1.161.0) NAO tem esse
# campo - `GenerationConfig` so aceita `thinking_config.thinking_budget`.
# Testado: gemini-3.1-flash-lite aceita thinking_budget=0 sem erro.
# Vazio desliga o campo (util se um modelo futuro rejeitar o parametro).
THINKING_BUDGET = os.environ.get("VERTEX_THINKING_BUDGET", "0").strip()

# Abaixo deste limiar a decisao vai para revisao humana. Espelha a regra do
# system_instruction.md ("Confianca < 0,70 -> INCONCLUSIVO").
CONFIDENCE_THRESHOLD = float(os.environ.get("CONFIDENCE_THRESHOLD", "0.70"))

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
_RAIZ = os.path.dirname(os.path.abspath(__file__))

# Os JSONL do dataset sao VERSIONADOS (dataset/): sao pequenos (~2,8 MB) e sao a
# especificacao do que foi treinado. As imagens NAO vao para o git (96 MB + 31 MB)
# - vivem no GCS e sao referenciadas pelos fileUri. Ver README.
DATASET_DIR = os.path.join(_RAIZ, "dataset")

# build/ e artefato regeneravel e fica fora do git: imagens de staging,
# relatorios de avaliacao, manifestos.
BUILD_DIR = os.path.join(_RAIZ, "build")
STAGING_DIR = os.path.join(BUILD_DIR, "staging")

# URIs derivados (para conveniencia)
GCS_STAGING_URI = f"gs://{BUCKET_NAME}/{STAGING_PREFIX}"
GCS_TRAIN_URI = f"gs://{BUCKET_NAME}/{DATA_PREFIX}/train.jsonl"
GCS_VALIDATION_URI = f"gs://{BUCKET_NAME}/{DATA_PREFIX}/validation.jsonl"
