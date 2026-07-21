#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Orquestrador do re-treino (Cloud Run Job, disparado pelo Pub/Sub retrain-trigger).

Fluxo:
  1. Le do Firestore os rotulos confirmados ainda nao treinados.
  2. Constroi build/retrain/{train,validation}.jsonl (base + novos reais).
  3. Sobe os dois JSONL para gs://<bucket>/data/ (reusa helpers de upload_gcs).
     As imagens ja estao no GCS (staging/ do base e inbox/ dos novos).
  4. Dispara o fine-tuning (mesma chamada de criar_tuning_job) e aguarda.
  5. Em sucesso: ativa o novo endpoint (config/active_model), registra o
     checkpoint e marca os registros como usados em treino.

Uso manual:
  uv run python -m app.retrain.run_retrain
  uv run python -m app.retrain.run_retrain --no-wait   # dispara e sai
"""

import sys
import time
import argparse

import config
import upload_gcs  # reuso: get_or_create_bucket / upload_file
from app import settings
from app.persistence import repository, counter
from app.retrain import build_dataset_from_prod


def _subir_jsonl(train_path, val_path):
    """Sobe os dois JSONL de re-treino para gs://<bucket>/data/ (overwrite)."""
    from google.cloud import storage

    settings.ensure_credentials()
    client = storage.Client(project=config.PROJECT_ID)
    bucket = upload_gcs.get_or_create_bucket(client, config.BUCKET_NAME, config.LOCATION)
    upload_gcs.upload_file(bucket, train_path, f"{config.DATA_PREFIX}/train.jsonl",
                           force=True, dry_run=False)
    upload_gcs.upload_file(bucket, val_path, f"{config.DATA_PREFIX}/validation.jsonl",
                           force=True, dry_run=False)


def _disparar_tuning(wait):
    """Dispara o SFT (mesma chamada de criar_tuning_job) e retorna o job.

    Chamado direto (nao via subprocess) para capturar o endpoint resultante e
    ativa-lo. criar_tuning_job.py segue como o entrypoint manual/CLI.
    """
    import vertexai
    from vertexai.tuning import sft

    settings.ensure_credentials()
    vertexai.init(project=config.PROJECT_ID, location=config.LOCATION)
    job = sft.train(
        source_model=config.BASE_MODEL,
        train_dataset=config.GCS_TRAIN_URI,
        validation_dataset=config.GCS_VALIDATION_URI,
        tuned_model_display_name=config.TUNED_MODEL_DISPLAY_NAME,
    )
    print(f"[retrain] job criado: {job.resource_name}")
    if not wait:
        return job
    while not job.has_ended:
        time.sleep(60)
        job.refresh()
        print(f"[retrain] status: {job.state}")
    return job


def run(wait=True):
    """Executa o re-treino de ponta a ponta. Retorna um resumo (dict)."""
    confirmados = repository.listar_confirmados_nao_treinados()
    if not confirmados:
        print("[retrain] nenhum rotulo confirmado novo; nada a treinar.")
        return {"status": "skipped", "motivo": "sem novos confirmados"}

    build = build_dataset_from_prod.build(confirmados)
    print(f"[retrain] dataset: +{build['novos_train']} treino / +{build['novos_val']} "
          f"validacao (total {build['total_train']}/{build['total_val']})")

    _subir_jsonl(build["train_path"], build["val_path"])

    job = _disparar_tuning(wait)
    if not wait:
        return {"status": "submitted", "job": job.resource_name,
                "ids": build["ids_usados"]}

    if not job.has_succeeded:
        print(f"[retrain] FALHOU: {job.state}")
        return {"status": "failed", "state": str(job.state)}

    endpoint = job.tuned_model_endpoint_name
    repository.set_active_endpoint(endpoint)
    counter.registrar_checkpoint_retrain(endpoint)
    repository.marcar_como_treinados(build["ids_usados"])
    print(f"[retrain] OK — novo endpoint ativo: {endpoint}")
    return {"status": "succeeded", "endpoint": endpoint,
            "treinados": len(build["ids_usados"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-wait", action="store_true",
                    help="dispara o tuning e sai (nao ativa endpoint automaticamente)")
    args = ap.parse_args()
    resultado = run(wait=not args.no_wait)
    if resultado.get("status") == "failed":
        sys.exit(1)


if __name__ == "__main__":
    main()
