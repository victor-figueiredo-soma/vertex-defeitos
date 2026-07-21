#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Constroi o dataset de re-treino a partir dos rotulos CONFIRMADOS em producao.

Cada registro confirmado (imagem ja em gs://.../inbox/, motivo, confirmed_label)
vira uma linha JSONL no MESMO formato do treino original — reusando
gerar_dataset.jsonl_line. O resultado e o dataset base + os novos exemplos reais,
com split estratificado por resultado.

Saida: build/retrain/train.jsonl e build/retrain/validation.jsonl.
"""

import os
import json
import random

import config
import gerar_dataset  # reuso de jsonl_line (formato Vertex); import sem efeitos colaterais
from app import settings
from app.inference.classifier import load_system_instruction

VAL_RATIO = 0.15
MIN_GROUP_FOR_VAL = 4
SEED = 42

_MIME_BY_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp"}


def _mime_from_uri(uri):
    ext = os.path.splitext(uri or "")[1].lower()
    return _MIME_BY_EXT.get(ext, "image/jpeg")


def _linha_de_registro(system_instruction, dados):
    """Converte um registro confirmado do Firestore em linha JSONL de treino."""
    label = dados["confirmed_label"]
    return gerar_dataset.jsonl_line(
        system_instruction,
        dados["image_uri"],
        _mime_from_uri(dados["image_uri"]),
        dados.get("motivo_alegado") or label.get("motivo_alegado"),
        label,
    )


def _ler_base(nome):
    """Le uma base JSONL existente (train/validation) como lista de dicts."""
    caminho = os.path.join(config.BUILD_DIR, nome)
    if not os.path.exists(caminho):
        return []
    with open(caminho, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def build(records, output_dir=None):
    """Gera os JSONL de re-treino.

    records: lista de (id, dados) de registros confirmados (status=confirmed).
    Retorna dict com contagens, caminhos e ids_usados.
    """
    random.seed(SEED)
    output_dir = output_dir or os.path.join(config.BUILD_DIR, "retrain")
    os.makedirs(output_dir, exist_ok=True)
    system_instruction = load_system_instruction()

    # Split estratificado dos NOVOS exemplos por resultado confirmado.
    grupos = {}
    for rid, dados in records:
        if not dados.get("confirmed_label") or not dados.get("image_uri"):
            continue
        res = dados["confirmed_label"].get("resultado", "NA")
        grupos.setdefault(res, []).append((rid, dados))

    novos_train, novos_val, ids_usados = [], [], []
    for res, items in grupos.items():
        items = sorted(items, key=lambda x: x[0])
        random.shuffle(items)
        n_val = round(len(items) * VAL_RATIO) if len(items) >= MIN_GROUP_FOR_VAL else 0
        novos_val.extend(items[:n_val])
        novos_train.extend(items[n_val:])
    ids_usados = [rid for rid, _ in novos_train + novos_val]

    # Base imutavel + novos exemplos reais.
    train = _ler_base("train.jsonl") + [_linha_de_registro(system_instruction, d)
                                        for _, d in novos_train]
    val = _ler_base("validation.jsonl") + [_linha_de_registro(system_instruction, d)
                                           for _, d in novos_val]

    train_path = os.path.join(output_dir, "train.jsonl")
    val_path = os.path.join(output_dir, "validation.jsonl")
    with open(train_path, "w", encoding="utf-8") as f:
        for line in train:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    with open(val_path, "w", encoding="utf-8") as f:
        for line in val:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    return {
        "train_path": train_path,
        "val_path": val_path,
        "novos_train": len(novos_train),
        "novos_val": len(novos_val),
        "total_train": len(train),
        "total_val": len(val),
        "ids_usados": ids_usados,
    }
