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

  # dataset otimizado (imagens 768px + amostragem), gerado por otimizar_dataset.py
  python upload_gcs.py --staging-dir build/staging_768 --staging-prefix staging768 \
      --jsonl train_opt.jsonl validation_opt.jsonl
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


def imagens_referenciadas(build_dir, nomes_jsonl):
    """Nomes de arquivo citados nos fileUri dos JSONL informados.

    Subir a pasta de staging inteira envia imagens que nenhum JSONL referencia -
    o treino as ignora, mas ficam ocupando o bucket. Isso acontece na pratica
    porque a pasta local costuma ter sobras de outras geracoes (ex.: o acervo
    completo redimensionado para um A/B, quando o job so usa a amostra).
    """
    import json

    refs = set()
    for nome in nomes_jsonl:
        caminho = os.path.join(build_dir, nome)
        if not os.path.exists(caminho):
            continue
        with open(caminho, encoding="utf-8") as f:
            for linha in f:
                if not linha.strip():
                    continue
                for c in json.loads(linha).get("contents", []):
                    for p in c.get("parts", []):
                        if "fileData" in p:
                            refs.add(p["fileData"]["fileUri"].rsplit("/", 1)[-1])
    return refs


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
    ap.add_argument("--staging-dir", default=config.STAGING_DIR,
                    help="pasta local das imagens (default: build/staging)")
    ap.add_argument("--staging-prefix", default=config.STAGING_PREFIX,
                    help=f"prefixo no bucket (default: {config.STAGING_PREFIX}). "
                         "Precisa casar com os fileUri do JSONL")
    ap.add_argument("--jsonl", nargs="+",
                    default=["train.jsonl", "validation.jsonl"],
                    help="JSONL de build/ a subir para data/ "
                         "(default: train.jsonl validation.jsonl)")
    ap.add_argument("--todas-imagens", action="store_true",
                    help="sobe a pasta inteira, inclusive imagens que nenhum "
                         "JSONL referencia (default: so as referenciadas)")
    args = ap.parse_args()

    config.ensure_credentials()

    staging_dir = args.staging_dir
    if not os.path.isdir(staging_dir):
        sys.exit(f"Nao encontrei {staging_dir}. Rode gerar_dataset.py antes.")

    client = storage.Client(project=config.PROJECT_ID)
    bucket = get_or_create_bucket(client, config.BUCKET_NAME, config.LOCATION)

    # 1) Imagens -> <staging_prefix>/  (so as que os JSONL referenciam)
    imgs = sorted(f for f in os.listdir(staging_dir)
                  if os.path.isfile(os.path.join(staging_dir, f)))
    refs = imagens_referenciadas(config.BUILD_DIR, args.jsonl)
    if refs and not args.todas_imagens:
        ignoradas = [f for f in imgs if f not in refs]
        imgs = [f for f in imgs if f in refs]
        if ignoradas:
            print(f"[imagens] ignorando {len(ignoradas)} nao referenciadas pelos "
                  f"JSONL (use --todas-imagens para subir mesmo assim)")
        faltando = sorted(refs - set(imgs))
        if faltando:
            sys.exit(f"ERRO: {len(faltando)} imagens referenciadas nao existem em "
                     f"{staging_dir}, a comecar por {faltando[:3]}")
    print(f"[imagens] {len(imgs)} arquivos em {staging_dir}")
    counts = {"upload": 0, "skip": 0, "would-upload": 0}
    for i, fn in enumerate(imgs, 1):
        local = os.path.join(staging_dir, fn)
        blob_name = f"{args.staging_prefix}/{fn}"
        res = upload_file(bucket, local, blob_name, args.force, args.dry_run)
        counts[res] = counts.get(res, 0) + 1
        if i % 50 == 0 or i == len(imgs):
            print(f"  {i}/{len(imgs)} processados...")

    # 2) JSONL -> data/
    print("[jsonl] enviando %s" % ", ".join(args.jsonl))
    for fn in args.jsonl:
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
    base = f"gs://{config.BUCKET_NAME}"
    print(f"Imagens em: {base}/{args.staging_prefix}/")
    for fn in args.jsonl:
        print(f"JSONL:      {base}/{config.DATA_PREFIX}/{fn}")


if __name__ == "__main__":
    main()
