#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sobe o dataset para o Cloud Storage (passo que faltava no pipeline).

O que faz:
  1. Autentica pela Service Account (ADC).
  2. Cria o bucket se ele ainda nao existir (na regiao de config.LOCATION).
  3. Sobe todas as imagens de build/staging/ para gs://<bucket>/staging/
     -> este caminho casa EXATAMENTE com os fileUri gravados nos JSONL.
  4. Sobe train.jsonl e validation.jsonl para gs://<bucket>/data/

Idempotente: por padrao pula objetos que ja existem no bucket com o mesmo
tamanho. Use --force para reenviar tudo.

Uso:
  python upload_gcs.py            # sobe o que falta
  python upload_gcs.py --force    # reenvia tudo
  python upload_gcs.py --dry-run  # so lista o que faria, sem enviar
"""

import os
import sys
import argparse

import config

try:
    from google.cloud import storage
    from google.api_core import exceptions as gcp_exc
except ImportError:
    sys.exit("Falta a dependencia. Rode: pip install -r requirements.txt")


def get_or_create_bucket(client, name, location):
    try:
        bucket = client.get_bucket(name)
        print(f"[bucket] '{name}' ja existe (regiao {bucket.location}).")
        if bucket.location and bucket.location.lower() != location.lower():
            print(f"  AVISO: bucket em {bucket.location}, mas config.LOCATION="
                  f"{location}. Ideal manter na mesma regiao do tuning.")
        return bucket
    except gcp_exc.NotFound:
        print(f"[bucket] criando '{name}' em {location} ...")
        bucket = client.bucket(name)
        bucket.storage_class = "STANDARD"
        return client.create_bucket(bucket, location=location)


def upload_file(bucket, local_path, blob_name, force, dry_run):
    blob = bucket.blob(blob_name)
    if not force and blob.exists():
        blob.reload()
        if blob.size == os.path.getsize(local_path):
            return "skip"
    if dry_run:
        return "would-upload"
    blob.upload_from_filename(local_path)
    return "upload"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="reenvia mesmo se ja existir")
    ap.add_argument("--dry-run", action="store_true", help="nao envia, so lista")
    args = ap.parse_args()

    config.ensure_credentials()

    if not os.path.isdir(config.STAGING_DIR):
        sys.exit(f"Nao encontrei {config.STAGING_DIR}. Rode gerar_dataset.py antes.")

    client = storage.Client(project=config.PROJECT_ID)
    bucket = get_or_create_bucket(client, config.BUCKET_NAME, config.LOCATION)

    # 1) Imagens -> staging/
    imgs = sorted(f for f in os.listdir(config.STAGING_DIR)
                  if os.path.isfile(os.path.join(config.STAGING_DIR, f)))
    print(f"[imagens] {len(imgs)} arquivos em build/staging/")
    counts = {"upload": 0, "skip": 0, "would-upload": 0}
    for i, fn in enumerate(imgs, 1):
        local = os.path.join(config.STAGING_DIR, fn)
        blob_name = f"{config.STAGING_PREFIX}/{fn}"
        res = upload_file(bucket, local, blob_name, args.force, args.dry_run)
        counts[res] = counts.get(res, 0) + 1
        if i % 50 == 0 or i == len(imgs):
            print(f"  {i}/{len(imgs)} processados...")

    # 2) JSONL -> data/
    print("[jsonl] enviando train/validation")
    for fn in ("train.jsonl", "validation.jsonl"):
        local = os.path.join(config.BUILD_DIR, fn)
        if not os.path.exists(local):
            print(f"  AVISO: {fn} nao encontrado, pulando.")
            continue
        blob_name = f"{config.DATA_PREFIX}/{fn}"
        res = upload_file(bucket, local, blob_name, args.force, args.dry_run)
        counts[res] = counts.get(res, 0) + 1
        print(f"  {fn}: {res}")

    print("-" * 60)
    print(f"Enviados: {counts['upload']} | Pulados (ja existiam): {counts['skip']}"
          + (f" | Simulados: {counts['would-upload']}" if args.dry_run else ""))
    print(f"Imagens em: {config.GCS_STAGING_URI}/")
    print(f"Treino:     {config.GCS_TRAIN_URI}")
    print(f"Validacao:  {config.GCS_VALIDATION_URI}")


if __name__ == "__main__":
    main()
