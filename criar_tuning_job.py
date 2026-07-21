#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dispara o fine-tuning supervisionado (SFT) do Gemini no Vertex AI.

Pre-requisitos (ver README.md):
  1. Dataset ja subido ao GCS (rode upload_gcs.py antes).
  2. Service Account com as roles:
       - roles/aiplatform.user   (criar/rodar o tuning job)
       - roles/storage.objectViewer no bucket (ler os JSONL/imagens)
  3. Vertex AI API habilitada no projeto.

O job roda de forma assincrona no Vertex. Este script cria o job, imprime o
resource name e (opcionalmente) fica aguardando ate concluir.

Uso:
  python criar_tuning_job.py                 # cria e acompanha
  python criar_tuning_job.py --no-wait       # cria e sai (acompanhe no Console)
  python criar_tuning_job.py --epochs 5      # sobrescreve hiperparametros
"""

import sys
import time
import argparse

import config

try:
    import vertexai
    from vertexai.tuning import sft
except ImportError:
    sys.exit("Falta a dependencia. Rode: pip install -r requirements.txt")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-wait", action="store_true",
                    help="nao aguarda a conclusao do job")
    ap.add_argument("--epochs", type=int, default=None,
                    help="numero de epocas (default: automatico do Vertex)")
    ap.add_argument("--learning-rate-multiplier", type=float, default=None,
                    help="multiplicador de learning rate (default: automatico)")
    ap.add_argument("--adapter-size", type=int, default=None,
                    choices=[1, 2, 4, 8, 16],
                    help="tamanho do adaptador LoRA (default: automatico)")
    args = ap.parse_args()

    config.ensure_credentials()

    print("=" * 60)
    print(f"Projeto : {config.PROJECT_ID}")
    print(f"Regiao  : {config.LOCATION}")
    print(f"Modelo  : {config.BASE_MODEL}")
    print(f"Treino  : {config.GCS_TRAIN_URI}")
    print(f"Valid.  : {config.GCS_VALIDATION_URI}")
    print("=" * 60)

    vertexai.init(project=config.PROJECT_ID, location=config.LOCATION)

    # Hiperparametros: so passa os que o usuario definiu; o resto o Vertex escolhe.
    kwargs = dict(
        source_model=config.BASE_MODEL,
        train_dataset=config.GCS_TRAIN_URI,
        validation_dataset=config.GCS_VALIDATION_URI,
        tuned_model_display_name=config.TUNED_MODEL_DISPLAY_NAME,
    )
    if args.epochs is not None:
        kwargs["epochs"] = args.epochs
    if args.learning_rate_multiplier is not None:
        kwargs["learning_rate_multiplier"] = args.learning_rate_multiplier
    if args.adapter_size is not None:
        kwargs["adapter_size"] = args.adapter_size

    print("Criando tuning job (isto gera custo no GCP)...")
    job = sft.train(**kwargs)
    print(f"\nJob criado: {job.resource_name}")
    print("Acompanhe no Console:")
    print(f"  https://console.cloud.google.com/vertex-ai/generative/language/"
          f"tuning?project={config.PROJECT_ID}")

    if args.no_wait:
        print("\n(--no-wait) Job rodando em background. Encerrando.")
        return

    print("\nAguardando conclusao (pode levar de dezenas de minutos a horas)...")
    while not job.has_ended:
        time.sleep(60)
        job.refresh()
        print(f"  status: {job.state}")

    if job.has_succeeded:
        print("\nTREINO CONCLUIDO COM SUCESSO")
        print(f"  Modelo afinado (endpoint): {job.tuned_model_endpoint_name}")
        print(f"  Modelo afinado (recurso) : {job.tuned_model_name}")
    else:
        print(f"\nTREINO FALHOU. Estado final: {job.state}")
        sys.exit(1)


if __name__ == "__main__":
    main()
