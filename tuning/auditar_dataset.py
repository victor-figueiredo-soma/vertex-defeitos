#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auditoria READ-ONLY dos JSONL gerados por gerar_dataset.py. Nao escreve nada.

Confere, antes de gastar dinheiro com um tuning job:
  - schema de cada linha (systemInstruction, roles, fileUri, JSON-alvo completo);
  - proporcao do split, vazamento de imagem entre treino/validacao e duplicatas;
  - balanceamento por resultado e por motivo alegado;
  - se todo fileUri tem imagem local e se ha imagem local orfa;
  - se o prefixo dos fileUri casa com o bucket do config.py (erro classico: o
    JSONL aponta para um bucket e o upload vai para outro);
  - coerencia entre o nome do arquivo e o rotulo (os rotulos da PoC sao derivados
    do nome, entao divergencia aqui e bug de rotulagem);
  - tamanho das imagens contra o limite de request do Vertex.

Uso:
  uv run python auditar_dataset.py
"""

import argparse
import json
import os
import sys
from collections import Counter

import config

REQUIRED_KEYS = {
    "motivo_alegado", "existe_defeito", "defeito_identificado", "motivo_correto",
    "divergencia_motivo", "subtipo", "resultado", "confianca",
    "requer_revisao_manual", "justificativa",
}
VALIDOS = {"APROVADO", "REPROVADO", "INCONCLUSIVO"}


def _stdout_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def carregar(nome):
    regs = []
    caminho = os.path.join(config.DATASET_DIR, nome)
    with open(caminho, encoding="utf-8") as f:
        for n, linha in enumerate(f, 1):
            linha = linha.strip()
            if not linha:
                continue
            try:
                regs.append((n, json.loads(linha)))
            except json.JSONDecodeError as e:
                print("  ERRO json %s linha %d: %s" % (nome, n, e))
    return regs


def extrair(reg):
    """-> dict com uri, motivo, gt (o JSON-alvo) e a lista de erros de schema."""
    erros = []
    uri = motivo = alvo = None
    if "systemInstruction" not in reg:
        erros.append("sem systemInstruction")
    conts = reg.get("contents") or []
    roles = [c.get("role") for c in conts]
    if roles != ["user", "model"]:
        erros.append("roles inesperados: %s" % roles)
    for c in conts:
        for p in c.get("parts", []):
            if "fileData" in p:
                uri = p["fileData"].get("fileUri")
            elif "text" in p and c.get("role") == "user":
                motivo = p["text"].replace("Motivo alegado pelo cliente:", "").strip()
            elif "text" in p and c.get("role") == "model":
                try:
                    alvo = json.loads(p["text"])
                except json.JSONDecodeError as e:
                    erros.append("resposta-alvo nao e JSON: %s" % e)
    if uri is None:
        erros.append("sem fileData/fileUri")
    if alvo is not None:
        falt = REQUIRED_KEYS - set(alvo)
        if falt:
            erros.append("alvo sem campos: %s" % sorted(falt))
        if alvo.get("resultado") not in VALIDOS:
            erros.append("resultado invalido: %r" % alvo.get("resultado"))
    return {"uri": uri, "motivo": motivo, "gt": alvo, "erros": erros}


def main():
    _stdout_utf8()

    ap = argparse.ArgumentParser()
    ap.add_argument("--sufixo", default="",
                    help="audita train_<sufixo>.jsonl em vez de train.jsonl "
                         "(ex.: --sufixo opt para o dataset otimizado)")
    ap.add_argument("--staging-dir", default=config.STAGING_DIR,
                    help="acervo local conferido contra os fileUri")
    args = ap.parse_args()

    if not os.path.isdir(config.BUILD_DIR):
        sys.exit("Nao encontrei %s. Rode gerar_dataset.py antes." % config.BUILD_DIR)

    sufixo = f"_{args.sufixo}" if args.sufixo else ""
    nome_train = f"train{sufixo}.jsonl"
    nome_valid = f"validation{sufixo}.jsonl"
    staging_dir = args.staging_dir

    splits = {}
    for nome in (nome_train, nome_valid):
        regs = carregar(nome)
        itens = []
        print("=== %s: %d linhas ===" % (nome, len(regs)))
        n_err = 0
        for linha, reg in regs:
            it = extrair(reg)
            it["linha"] = linha
            itens.append(it)
            if it["erros"]:
                n_err += 1
                if n_err <= 5:
                    print("  linha %d: %s" % (linha, "; ".join(it["erros"])))
        print("  registros com problema de schema: %d" % n_err)
        splits[nome] = itens

    tr, va = splits[nome_train], splits[nome_valid]
    total = len(tr) + len(va)
    if total == 0:
        sys.exit("JSONL vazios.")

    print()
    print("=== SPLIT ===")
    print("  treino     : %d (%.1f%%)" % (len(tr), 100.0 * len(tr) / total))
    print("  validacao  : %d (%.1f%%)" % (len(va), 100.0 * len(va) / total))
    print("  total      : %d" % total)

    print()
    print("=== VAZAMENTO (mesma imagem nos dois splits) ===")
    inter = set(i["uri"] for i in tr) & set(i["uri"] for i in va)
    print("  imagens em comum: %d %s" % (len(inter), sorted(inter)[:5]))

    print()
    print("=== DUPLICATAS DENTRO DO SPLIT ===")
    for nome, itens in (("treino", tr), ("validacao", va)):
        c = Counter(i["uri"] for i in itens)
        dup = {k: v for k, v in c.items() if v > 1}
        print("  %s: %d uris repetidas %s" % (nome, len(dup), list(dup.items())[:3]))

    print()
    print("=== BALANCEAMENTO: resultado ===")
    for nome, itens in (("treino", tr), ("validacao", va)):
        c = Counter((i["gt"] or {}).get("resultado") for i in itens)
        tot = sum(c.values()) or 1
        print("  %-10s %s" % (nome, "  ".join(
            "%s=%d (%.0f%%)" % (k, v, 100.0 * v / tot) for k, v in c.most_common())))

    print()
    print("=== BALANCEAMENTO: motivo alegado ===")
    ctr = Counter(i["motivo"] for i in tr)
    cva = Counter(i["motivo"] for i in va)
    todos = sorted(set(ctr) | set(cva), key=lambda m: -(ctr[m] + cva[m]))
    print("  %-38s %6s %6s" % ("motivo", "treino", "valid"))
    for m in todos:
        print("  %-38s %6d %6d" % ((m or "?")[:38], ctr[m], cva[m]))
    sem_val = [m for m in todos if ctr[m] > 0 and cva[m] == 0]
    print("  motivos SEM representacao na validacao: %d -> %s" % (len(sem_val), sem_val))

    print()
    print("=== IMAGENS LOCAIS ===")
    arquivos = set(os.listdir(staging_dir)) \
        if os.path.isdir(staging_dir) else set()
    print("  arquivos em %s: %d" % (staging_dir, len(arquivos)))
    faltando = []
    usados = set()
    for nome, itens in (("treino", tr), ("validacao", va)):
        for i in itens:
            fn = (i["uri"] or "").rsplit("/", 1)[-1]
            usados.add(fn)
            if fn not in arquivos:
                faltando.append((nome, i["linha"], fn))
    print("  fileUri sem arquivo local: %d %s" % (len(faltando), faltando[:5]))
    orfaos = sorted(arquivos - usados)
    print("  arquivos locais nao referenciados: %d %s" % (len(orfaos), orfaos[:5]))

    print()
    print("=== PREFIXO DO fileUri (tem que casar com o bucket do config) ===")
    print("  config.BUCKET_NAME = %s" % config.BUCKET_NAME)
    esperado = "gs://%s" % config.BUCKET_NAME
    # Corta no ultimo '/' para nao depender do nome do prefixo: o dataset
    # otimizado usa 'staging768/', nao 'staging/'.
    for k, v in Counter((i["uri"] or "").rsplit("/", 2)[0]
                        for i in tr + va).most_common():
        marca = "OK  " if k == esperado else "!!! "
        print("  %s%s -> %d" % (marca, k, v))

    print()
    print("=== COERENCIA nome-do-arquivo x rotulo ===")
    # Os nomes seguem NNN_<motivo>_<resultado>.<ext> e o rotulo da PoC e derivado
    # deles, entao qualquer divergencia aqui e bug de rotulagem.
    inconsistentes = []
    for nome, itens in (("treino", tr), ("validacao", va)):
        for i in itens:
            fn = (i["uri"] or "").rsplit("/", 1)[-1].lower()
            res = ((i["gt"] or {}).get("resultado") or "").lower()
            if res and res not in fn:
                inconsistentes.append((nome, i["linha"], fn, res))
    print("  divergencias nome x resultado: %d" % len(inconsistentes))
    for x in inconsistentes[:8]:
        print("   ", x)

    print()
    print("=== TAMANHO DAS IMAGENS (limite Vertex: 20MB por request) ===")
    tam = sorted(os.path.getsize(os.path.join(staging_dir, fn))
                 for fn in sorted(usados)
                 if os.path.exists(os.path.join(staging_dir, fn)))
    if tam:
        print("  min=%.0fKB  mediana=%.0fKB  max=%.1fMB  total=%.1fMB" % (
            tam[0] / 1024, tam[len(tam) // 2] / 1024, tam[-1] / 1e6, sum(tam) / 1e6))
        print("  arquivos > 7MB (limite pratico do SFT): %d"
              % sum(1 for t in tam if t > 7e6))


if __name__ == "__main__":
    main()
