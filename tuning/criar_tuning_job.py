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

Os hiperparametros vem de config.TUNING_* (nao do automatico do Vertex, cujo
default e epochCount=40). As flags abaixo sobrescrevem caso a caso.

O resource name do job e gravado em build/tuning_job.json - antes ele so existia
no terminal, e rodar com --no-wait e fechar a janela perdia o ID.

Uso:
  python criar_tuning_job.py                 # cria e acompanha
  python criar_tuning_job.py --no-wait       # cria e sai (acompanhe no Console)
  python criar_tuning_job.py --epochs 5      # sobrescreve hiperparametros
  python criar_tuning_job.py --train-uri gs://.../smoke_train.jsonl \
      --validation-uri gs://.../smoke_validation.jsonl \
      --tuned-name smoke-test --epochs 2 --no-wait
"""

import os
import sys
import json
import time
import argparse

import config

try:
    import vertexai
    from vertexai.tuning import sft
except ImportError:
    sys.exit("Falta a dependencia. Rode: pip install -r requirements.txt")


JOB_FILE = os.path.join(config.BUILD_DIR, "tuning_job.json")


def registrar_job(job, args, endpoint=None):
    """Grava o job em build/tuning_job.json.

    Sem isso o resource name so existe no stdout: rodar com --no-wait e fechar o
    terminal perde o ID, e recupera-lo exige um 'gcloud ai tuning-jobs list'.
    monitorar_metricas.py le este arquivo quando --job e omitido.
    """
    registro = {
        "resource_name": job.resource_name,
        "job_id": job.resource_name.rsplit("/", 1)[-1],
        "regiao": config.LOCATION,
        "projeto": config.PROJECT_ID,
        "modelo_base": config.BASE_MODEL,
        "tuned_name": args.tuned_name,
        "train_uri": args.train_uri,
        "validation_uri": args.validation_uri,
        "hiperparametros": {
            "epochs": args.epochs,
            "learning_rate_multiplier": args.learning_rate_multiplier,
            "adapter_size": args.adapter_size,
        },
        "criado_em": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if endpoint:
        registro["endpoint"] = endpoint
        registro["concluido_em"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    os.makedirs(config.BUILD_DIR, exist_ok=True)
    with open(JOB_FILE, "w", encoding="utf-8") as f:
        json.dump(registro, f, ensure_ascii=False, indent=2)
    print(f"Registrado em: {JOB_FILE}")
    print(f"Monitore com : uv run python monitorar_metricas.py")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-wait", action="store_true",
                    help="nao aguarda a conclusao do job")
    ap.add_argument("--epochs", type=int, default=config.TUNING_EPOCHS,
                    help=f"numero de epocas (default: {config.TUNING_EPOCHS})")
    ap.add_argument("--learning-rate-multiplier", type=float,
                    default=config.TUNING_LR_MULTIPLIER,
                    help="multiplicador de learning rate "
                         f"(default: {config.TUNING_LR_MULTIPLIER})")
    ap.add_argument("--adapter-size", type=int,
                    default=config.TUNING_ADAPTER_SIZE,
                    choices=config.ADAPTER_SIZES,
                    help="tamanho do adaptador LoRA "
                         f"(default: {config.TUNING_ADAPTER_SIZE})")
    ap.add_argument("--train-uri", default=config.GCS_TRAIN_URI,
                    help=f"JSONL de treino (default: {config.GCS_TRAIN_URI})")
    ap.add_argument("--validation-uri", default=config.GCS_VALIDATION_URI,
                    help="JSONL de validacao "
                         f"(default: {config.GCS_VALIDATION_URI})")
    ap.add_argument("--tuned-name", default=config.TUNED_MODEL_DISPLAY_NAME,
                    help="display name do modelo afinado "
                         f"(default: {config.TUNED_MODEL_DISPLAY_NAME})")
    args = ap.parse_args()

    config.ensure_credentials()

    print("=" * 60)
    print(f"Projeto : {config.PROJECT_ID}")
    print(f"Regiao  : {config.LOCATION}")
    print(f"Modelo  : {config.BASE_MODEL}")
    print(f"Treino  : {args.train_uri}")
    print(f"Valid.  : {args.validation_uri}")
    print(f"Afinado : {args.tuned_name}")
    hp = {k: v for k, v in (("epochs", args.epochs),
                            ("lr_mult", args.learning_rate_multiplier),
                            ("adapter", args.adapter_size)) if v is not None}
    print("Hiperp. : %s" % (hp if hp else
          "todos no default do Vertex (recomendado no 1o job de um modelo novo)"))
    print("=" * 60)

    vertexai.init(project=config.PROJECT_ID, location=config.LOCATION)

    # So passa o que foi definido. None = default do Vertex, que e o recomendado
    # para o PRIMEIRO job de um modelo novo (ver comentario em config.TUNING_*).
    kwargs = dict(
        source_model=config.BASE_MODEL,
        train_dataset=args.train_uri,
        validation_dataset=args.validation_uri,
        tuned_model_display_name=args.tuned_name,
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

    registrar_job(job, args)

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
        registrar_job(job, args, endpoint=job.tuned_model_endpoint_name)
    else:
        print(f"\nTREINO FALHOU. Estado final: {job.state}")
        sys.exit(1)


if __name__ == "__main__":
    main()
