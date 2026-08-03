#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gera uma versao OTIMIZADA do dataset de SFT: imagens menores + amostragem
balanceada. Objetivo: cortar o volume de tokens do tuning job sem perder
representatividade.

  1. Redimensiona cada imagem referenciada para <= MAX_DIM px no maior lado.
     768 px e o degrau em que a imagem cai para 1 ladrilho = 258 tokens no
     Gemini 2.x (1024 e 896 ainda custam 2 ladrilhos).
  2. Subamostra o treino por (motivo, resultado) com teto por classe.
     REPROVADO fica INTOCADO por padrao - sao so 13 no acervo inteiro e e a
     classe mais fraca do baseline (F1 16,3%, especificidade 30,8%).
  3. Reescreve os JSONL apontando para o novo prefixo do GCS.

POR QUE NAO REGERAR COM gerar_dataset.py:
  A numeracao la e por indice ({i:03d}) sobre a listagem da origem, entao
  regerar renomeia todos os arquivos, remexe o split E reintroduz o PNG de
  11 MB e os 13 MPO que foram corrigidos a mao. Este script trabalha sobre os
  artefatos ja curados (build/train.jsonl, build/validation.jsonl,
  build/staging/) e nunca toca em dataset_defeitos/.

Os registros do JSONL sao preservados VERBATIM - so o fileUri e reescrito.
Isso garante que systemInstruction e alvo fiquem byte a byte identicos aos
que ja foram auditados, sem risco de divergencia por re-derivacao.

Uso:
  uv run python otimizar_dataset.py
  uv run python otimizar_dataset.py --max-dim 1152          # gate reprovou 768
  uv run python otimizar_dataset.py --cap-aprovado 1 --cap-inconclusivo 1 \
      --cap-reprovado 1 --sufixo smoke                      # dataset de smoke test
"""

import os
import csv
import sys
import json
import random
import argparse
import collections

from PIL import Image, ImageOps

import config
import imagem

# O redimensionamento vive em imagem.py porque a inferencia de producao
# (app/inference/classifier.py) precisa aplicar exatamente o mesmo tratamento -
# senao treino e producao divergem na resolucao.
ladrilhos = imagem.ladrilhos
TOKENS_POR_LADRILHO = imagem.TOKENS_POR_LADRILHO


def _stdout_utf8():
    """As justificativas tem acento; o console do Windows costuma ser cp1252."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def ler_jsonl(caminho):
    with open(caminho, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def descrever(reg):
    """Extrai (motivo, resultado, arquivo, mime) de um registro do JSONL."""
    uri = mime = motivo = None
    alvo = None
    for c in reg["contents"]:
        for p in c.get("parts", []):
            if "fileData" in p:
                uri = p["fileData"]["fileUri"]
                mime = p["fileData"]["mimeType"]
            elif "text" in p and c["role"] == "user":
                motivo = p["text"].replace(
                    "Motivo alegado pelo cliente:", "").strip()
            elif "text" in p and c["role"] == "model":
                alvo = json.loads(p["text"])
    return motivo, alvo["resultado"], uri.rsplit("/", 1)[-1], mime


def subamostrar(regs, caps, seed):
    """Subamostra por (motivo, resultado) aplicando o teto de cada resultado.

    Ordena por nome antes de embaralhar para o resultado ser reproduzivel
    independentemente da ordem em que os registros vieram do arquivo.
    """
    grupos = collections.defaultdict(list)
    for r in regs:
        motivo, resultado, arquivo, _ = descrever(r)
        grupos[(motivo, resultado)].append((arquivo, r))

    rng = random.Random(seed)
    escolhidos = []
    for chave in sorted(grupos):
        itens = sorted(grupos[chave], key=lambda x: x[0])
        rng.shuffle(itens)
        cap = caps.get(chave[1])
        escolhidos.extend(r for _, r in (itens if cap is None else itens[:cap]))
    return escolhidos


def tabela(regs_antes, regs_depois, caps):
    """Imprime a distribuicao por (motivo, resultado) antes -> depois."""
    def contar(regs):
        c = collections.Counter()
        for r in regs:
            motivo, resultado, _, _ = descrever(r)
            c[(motivo, resultado)] += 1
        return c

    a, d = contar(regs_antes), contar(regs_depois)
    motivos = sorted({k[0] for k in a})
    print(f"{'motivo':24}{'APROVADO':>16}{'REPROVADO':>16}{'INCONCLUSIVO':>16}{'total':>14}")
    tot_a = collections.Counter()
    tot_d = collections.Counter()
    for m in motivos:
        linha = f"{m:24}"
        sa = sd = 0
        for res in ("APROVADO", "REPROVADO", "INCONCLUSIVO"):
            va, vd = a[(m, res)], d[(m, res)]
            tot_a[res] += va
            tot_d[res] += vd
            sa += va
            sd += vd
            linha += f"{va:>7} ->{vd:>5}"
        print(linha + f"{sa:>7} ->{sd:>5}")
    linha = f"{'TOTAL':24}"
    for res in ("APROVADO", "REPROVADO", "INCONCLUSIVO"):
        linha += f"{tot_a[res]:>7} ->{tot_d[res]:>5}"
    print(linha + f"{sum(tot_a.values()):>7} ->{sum(tot_d.values()):>5}")

    if tot_d["REPROVADO"]:
        print("\nrazao APROVADO:REPROVADO  %.1f:1  ->  %.1f:1" % (
            tot_a["APROVADO"] / max(tot_a["REPROVADO"], 1),
            tot_d["APROVADO"] / tot_d["REPROVADO"]))


def main():
    _stdout_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-dim", type=int, default=768,
                    help="maior lado da imagem em px (default: 768)")
    ap.add_argument("--cap-aprovado", type=int, default=18,
                    help="teto de APROVADO por motivo (default: 18)")
    ap.add_argument("--cap-inconclusivo", type=int, default=3,
                    help="teto de INCONCLUSIVO por motivo (default: 3)")
    ap.add_argument("--cap-reprovado", type=int, default=None,
                    help="teto de REPROVADO por motivo (default: sem teto)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--sufixo", default="opt",
                    help="sufixo dos arquivos de saida (default: opt)")
    ap.add_argument("--staging-prefix", default=None,
                    help="prefixo no bucket gravado nos fileUri "
                         "(default: staging<max_dim>). Use um prefixo novo para "
                         "nao reaproveitar objetos de uma tentativa anterior")
    ap.add_argument("--sem-imagens", action="store_true",
                    help="so reescreve os JSONL; nao reprocessa as imagens")
    ap.add_argument("--media-resolution", default=None,
                    choices=config.MEDIA_RESOLUTIONS,
                    help="grava generationConfig.mediaResolution em cada linha. "
                         "E ESTE campo, nao a dimensao da imagem, que define o "
                         "custo em tokens (HIGH=1290, MEDIUM=256, LOW=64). "
                         "Precisa casar com a inferencia de producao")
    args = ap.parse_args()

    caps = {"APROVADO": args.cap_aprovado,
            "REPROVADO": args.cap_reprovado,
            "INCONCLUSIVO": args.cap_inconclusivo}

    dst_dir = os.path.join(config.BUILD_DIR, f"staging_{args.max_dim}")
    gcs_prefix = args.staging_prefix or f"staging{args.max_dim}"
    prefixo = f"gs://{config.BUCKET_NAME}/{gcs_prefix}"
    os.makedirs(dst_dir, exist_ok=True)
    os.makedirs(config.DATASET_DIR, exist_ok=True)

    treino = ler_jsonl(os.path.join(config.DATASET_DIR, "train.jsonl"))
    valid = ler_jsonl(os.path.join(config.DATASET_DIR, "validation.jsonl"))

    print("=" * 78)
    print(f"origem   : {config.STAGING_DIR}")
    print(f"destino  : {dst_dir}  (<= {args.max_dim} px)")
    print(f"prefixo  : {prefixo}")
    print(f"caps     : {caps}  seed={args.seed}")
    print(f"mediaRes : {args.media_resolution or '(nao gravado -> HIGH default)'}")
    print("=" * 78)

    # 1. Subamostragem (so o treino; a validacao e o sinal de eval e ja e pequena)
    print("\n=== AMOSTRAGEM (treino) ===")
    treino_novo = subamostrar(treino, caps, args.seed)
    tabela(treino, treino_novo, caps)
    print(f"\nvalidacao mantida integralmente: {len(valid)} exemplos")

    # 2. Redimensionamento + reescrita do fileUri
    print(f"\n=== IMAGENS ===")
    manifesto = []
    tok_antes = tok_depois = 0
    reencodadas = copiadas = 0

    for reg in treino_novo + valid:
        motivo, resultado, arquivo, mime = descrever(reg)
        src = os.path.join(config.STAGING_DIR, arquivo)
        dst = os.path.join(dst_dir, arquivo)

        if args.sem_imagens:
            with Image.open(src) as im:
                ow, oh = ImageOps.exif_transpose(im).size
            nw = nh = None
        else:
            ow, oh, nw, nh, nbytes, reenc = imagem.normalizar_arquivo(
                src, dst, mime, args.max_dim)
            reencodadas += reenc
            copiadas += (not reenc)
            tok_antes += ladrilhos(ow, oh) * TOKENS_POR_LADRILHO
            tok_depois += ladrilhos(nw, nh) * TOKENS_POR_LADRILHO
            manifesto.append({
                "arquivo": arquivo, "motivo": motivo, "resultado": resultado,
                "mime": mime, "orig_w": ow, "orig_h": oh,
                "novo_w": nw, "novo_h": nh,
                "orig_bytes": os.path.getsize(src), "novo_bytes": nbytes,
                "ladrilhos_antes": ladrilhos(ow, oh),
                "ladrilhos_depois": ladrilhos(nw, nh),
            })

        # Reescreve so o fileUri; o resto do registro fica verbatim.
        for c in reg["contents"]:
            for p in c.get("parts", []):
                if "fileData" in p:
                    p["fileData"]["fileUri"] = f"{prefixo}/{arquivo}"

        if args.media_resolution:
            reg.setdefault("generationConfig", {})[
                "mediaResolution"] = args.media_resolution

    if not args.sem_imagens:
        print(f"  reencodadas : {reencodadas}")
        print(f"  copiadas    : {copiadas}  (ja estavam <= {args.max_dim} px)")
        print(f"  tokens img  : {tok_antes:,} -> {tok_depois:,} "
              f"({100 * tok_depois / max(tok_antes, 1):.1f}%)")

    # 3. Saidas
    out_train = os.path.join(config.DATASET_DIR, f"train_{args.sufixo}.jsonl")
    out_valid = os.path.join(config.DATASET_DIR, f"validation_{args.sufixo}.jsonl")
    for caminho, regs in ((out_train, treino_novo), (out_valid, valid)):
        with open(caminho, "w", encoding="utf-8") as f:
            for r in regs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    if manifesto:
        out_man = os.path.join(config.BUILD_DIR, f"manifesto_{args.sufixo}.csv")
        with open(out_man, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(manifesto[0]))
            w.writeheader()
            w.writerows(manifesto)
        print(f"  manifesto   : {out_man}")

    print("\n=== SAIDAS ===")
    print(f"  {out_train}  ({len(treino_novo)} linhas)")
    print(f"  {out_valid}  ({len(valid)} linhas)")
    print(f"\nSuba com:  uv run python upload_gcs.py")
    print(f"Depois confira tuningDataStats.totalBillableTokenCount no describe.")


if __name__ == "__main__":
    main()
