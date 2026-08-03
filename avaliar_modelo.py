#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Avalia o modelo no dataset local e imprime as metricas — o numero de antes/depois
do fine-tuning.

Usa o MESMO caminho de codigo da producao (`app.inference.classifier`) e as
imagens LOCAIS de build/staging/, para nao depender do bucket.

Monta dois conjuntos:
  A) OFICIAL     - as linhas de build/validation.jsonl.
  B) DIAGNOSTICO - A + todos os REPROVADO + uma amostra de INCONCLUSIVO tirados
     do train.jsonl.

Por que existe o conjunto B: o validation.jsonl gerado hoje e 100% APROVADO
(MIN_GROUP_FOR_VAL=4 empurra os grupos pequenos inteiros para o treino), logo a
acuracia em A e enganosa — responder sempre APROVADO daria 100%. B mede as tres
classes.

  ATENCAO: B so e legitimo para o MODELO-BASE, que nunca viu esses exemplos.
  Para avaliar o modelo AFINADO, B e vazamento (o modelo treinou nessas imagens)
  — use --so-oficial, ou refaca o split antes.

Uso:
  uv run python avaliar_modelo.py                      # modelo de settings/fallback
  uv run python avaliar_modelo.py --endpoint <endpoint> # modelo especifico
  uv run python avaliar_modelo.py --so-oficial          # so validation.jsonl
  uv run python avaliar_modelo.py --out resultado.json  # onde salvar o detalhe
"""

import argparse
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import config
import inferencia

CLASSES = ["APROVADO", "REPROVADO", "INCONCLUSIVO"]
DEFAULT_OUT = os.path.join(config.BUILD_DIR, "resultado_avaliacao.json")


def _stdout_utf8():
    """As justificativas tem acento; o console do Windows costuma ser cp1252."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def ler_jsonl(nome, staging_dir=None):
    """Le um JSONL de treino e devolve os exemplos (imagem local + rotulo).

    staging_dir permite apontar para um acervo alternativo (ex.: as imagens
    redimensionadas em build/staging_768) mantendo os mesmos rotulos - e o que
    torna o A/B de resolucao um experimento de variavel unica.
    """
    itens = []
    staging_dir = staging_dir or config.STAGING_DIR
    caminho = os.path.join(config.DATASET_DIR, nome)
    with open(caminho, encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if not linha:
                continue
            reg = json.loads(linha)
            uri = motivo = alvo = None
            for c in reg["contents"]:
                for p in c.get("parts", []):
                    if "fileData" in p:
                        uri = p["fileData"]["fileUri"]
                    elif "text" in p and c["role"] == "user":
                        motivo = p["text"].replace(
                            "Motivo alegado pelo cliente:", "").strip()
                    elif "text" in p and c["role"] == "model":
                        alvo = json.loads(p["text"])
            arquivo = uri.rsplit("/", 1)[-1]
            itens.append({
                "arquivo": arquivo,
                "path": os.path.join(staging_dir, arquivo),
                "motivo": motivo,
                "esperado": alvo["resultado"],
                # Alem do resultado, o alvo tem os campos que dizem se o modelo
                # ENXERGOU o defeito — e nao apenas se acertou a decisao.
                # APROVADO -> 1, REPROVADO -> 0, INCONCLUSIVO -> None.
                "esperado_existe_defeito": alvo.get("existe_defeito"),
                "esperado_defeito": alvo.get("defeito_identificado"),
                # Os INCONCLUSIVO sao imagens degradadas artificialmente
                # (aug_*), nao fotos reais; separa-los evita ler as metricas
                # visuais como se fossem sobre o acervo real.
                "sintetico": arquivo.startswith("aug_"),
                "origem": nome,
            })
    return itens


def montar_holdout(sufixo, staging_dir=None):
    """Validacao oficial + os exemplos de train.jsonl que o sufixo NAO treinou.

    Depois da subamostragem (otimizar_dataset.py), o que ficou de fora do
    train_<sufixo>.jsonl nunca foi visto pelo modelo - e held-out legitimo para
    ELE, mesmo vindo do arquivo de treino. Isso multiplica a amostra limpa e traz
    o INCONCLUSIVO de volta para a medicao, coisa que a validacao oficial (100%
    APROVADO) nao faz.

    Continua sem REPROVADO: os 13 do acervo sao todos preservados no treino de
    proposito, entao a especificidade segue sem medicao limpa.
    """
    val = ler_jsonl("validation.jsonl", staging_dir)
    for i in val:
        i["conjunto"] = "oficial"

    treinados = {i["arquivo"] for i in ler_jsonl(f"train_{sufixo}.jsonl", staging_dir)}
    fora = [i for i in ler_jsonl("train.jsonl", staging_dir)
            if i["arquivo"] not in treinados]
    for i in fora:
        i["conjunto"] = "holdout"

    conjunto = val + fora
    print("=== CONJUNTO DE AVALIACAO (held-out de train_%s.jsonl) ===" % sufixo)
    print("  oficial (validation.jsonl) : %d  %s" % (
        len(val), dict(Counter(i["esperado"] for i in val))))
    print("  nao-treinados de train.jsonl: %d  %s" % (
        len(fora), dict(Counter(i["esperado"] for i in fora))))
    print("  TOTAL de chamadas          : %d  %s" % (
        len(conjunto), dict(Counter(i["esperado"] for i in conjunto))))
    if not any(i["esperado"] == "REPROVADO" for i in conjunto):
        print("  AVISO: nenhum REPROVADO no conjunto - especificidade nao medida.")
    return conjunto


def montar_conjunto(so_oficial, n_inconclusivo, seed, todos=False,
                    staging_dir=None):
    val = ler_jsonl("validation.jsonl", staging_dir)
    for i in val:
        i["conjunto"] = "oficial"
    print("=== CONJUNTO DE AVALIACAO ===")
    print("  oficial (validation.jsonl) : %d  %s" % (
        len(val), dict(Counter(i["esperado"] for i in val))))
    if so_oficial:
        print("  TOTAL de chamadas          : %d" % len(val))
        return val

    if todos:
        # Dataset inteiro. Com so 13 REPROVADO no acervo, amostrar joga fora a
        # unica evidencia do lado "sem defeito"; medir tudo e o que da suporte
        # estatistico as metricas por tipo de defeito.
        treino = ler_jsonl("train.jsonl", staging_dir)
        for i in treino:
            i["conjunto"] = "completo"
        conjunto = val + treino
        print("  + train.jsonl inteiro      : %d  %s" % (
            len(treino), dict(Counter(i["esperado"] for i in treino))))
        print("  TOTAL de chamadas          : %d" % len(conjunto))
        return conjunto

    treino = ler_jsonl("train.jsonl", staging_dir)
    reprovados = [i for i in treino if i["esperado"] == "REPROVADO"]
    inconclusivos = [i for i in treino if i["esperado"] == "INCONCLUSIVO"]
    random.Random(seed).shuffle(inconclusivos)
    amostra = inconclusivos[:n_inconclusivo]
    for i in reprovados + amostra:
        i["conjunto"] = "diagnostico"

    print("  + REPROVADO do treino      : %d" % len(reprovados))
    print("  + INCONCLUSIVO do treino   : %d (amostra de %d, seed=%d)" % (
        len(amostra), len(inconclusivos), seed))
    conjunto = val + reprovados + amostra
    print("  TOTAL de chamadas          : %d" % len(conjunto))
    return conjunto


def avaliar_um(item, endpoint, tentativas=4, media_resolution=None):
    """Classifica um exemplo, com retry so em erro transitorio."""
    for tentativa in range(tentativas):
        try:
            julg, _ = inferencia.classify_file(
                item["path"], item["motivo"], endpoint=endpoint,
                media_resolution=media_resolution)
            div = julg.get("divergencia_motivo") or {}
            item.update({
                "previsto": julg.get("resultado"),
                "confianca": julg.get("confianca"),
                "revisao": julg.get("requer_revisao_manual"),
                "defeito_identificado": julg.get("defeito_identificado"),
                # Os tres campos abaixo sustentam as metricas de DETECCAO
                # (analisar_deteccao.py); sem eles so da para medir a decisao.
                "existe_defeito": julg.get("existe_defeito"),
                "motivo_correto": julg.get("motivo_correto"),
                "divergencia_houve": div.get("houve") if isinstance(div, dict) else None,
                "subtipo": julg.get("subtipo"),
                "justificativa": (julg.get("justificativa") or "")[:200],
                "erro": None,
            })
            return item
        except Exception as e:  # a API tem varios tipos de excecao; tratamos igual
            msg = repr(e)[:200]
            transitorio = any(s in msg for s in
                              ("429", "503", "500", "Deadline", "timeout"))
            if transitorio and tentativa < tentativas - 1:
                time.sleep(3 * (tentativa + 1))
                continue
            item["previsto"] = None
            item["erro"] = msg
            return item
    return item


def metricas(titulo, itens):
    print("=" * 62)
    print(titulo + "  (n=%d)" % len(itens))
    print("=" * 62)
    if not itens:
        return
    acertos = sum(1 for r in itens if r["previsto"] == r["esperado"])
    print("  ACURACIA: %d/%d = %.1f%%" % (
        acertos, len(itens), 100.0 * acertos / len(itens)))
    print()
    print("  Matriz de confusao (linha = esperado, coluna = previsto):")
    labels = CLASSES + ["<falha>"]
    m = defaultdict(Counter)
    for r in itens:
        m[r["esperado"]][r["previsto"] or "<falha>"] += 1
    print("    %-14s %s" % ("", " ".join("%12s" % c[:12] for c in labels)))
    for e in CLASSES:
        if sum(m[e].values()) == 0:
            continue
        print("    %-14s %s" % (e[:14], " ".join("%12d" % m[e][c] for c in labels)))
    print()

    def fmt(x):
        return "   n/a  " if x != x else "%7.1f%%" % (100 * x)

    print("  Por classe:")
    print("    %-14s %8s %8s %8s %8s" % ("classe", "sup", "recall", "prec", "F1"))
    for c in CLASSES:
        sup = sum(1 for r in itens if r["esperado"] == c)
        tp = sum(1 for r in itens if r["esperado"] == c and r["previsto"] == c)
        fp = sum(1 for r in itens if r["esperado"] != c and r["previsto"] == c)
        if sup == 0 and fp == 0:
            continue
        rec = tp / sup if sup else float("nan")
        prec = tp / (tp + fp) if (tp + fp) else float("nan")
        soma = prec + rec
        f1 = (2 * prec * rec / soma) if (soma == soma and soma) else float("nan")
        print("    %-14s %8d %s %s %s" % (c, sup, fmt(rec), fmt(prec), fmt(f1)))
    print()

    # A politica de decisao vale tanto quanto a acuracia: se o erro vem com
    # confianca alta, o threshold nao serve de rede de seguranca.
    thr = config.CONFIDENCE_THRESHOLD
    baixa = [r for r in itens
             if isinstance(r.get("confianca"), (int, float)) and r["confianca"] < thr]
    flag = [r for r in itens if r.get("revisao") == 1]
    print("  Politica de decisao (CONFIDENCE_THRESHOLD=%.2f):" % thr)
    print("    confianca < %.2f          : %d (%.0f%%)" % (
        thr, len(baixa), 100.0 * len(baixa) / len(itens)))
    print("    requer_revisao_manual=1   : %d (%.0f%%)" % (
        len(flag), 100.0 * len(flag) / len(itens)))
    autom = [r for r in itens if r.get("revisao") == 0]
    if autom:
        certos = sum(1 for r in autom if r["previsto"] == r["esperado"])
        print("    decisoes automaticas      : %d, dessas corretas: %d (%.1f%%)" % (
            len(autom), certos, 100.0 * certos / len(autom)))


def main():
    ap = argparse.ArgumentParser(
        description="Avalia o modelo no dataset local e imprime as metricas.")
    ap.add_argument("--endpoint", default=None,
                    help="endpoint/modelo a avaliar (default: TUNED_ENDPOINT ou fallback)")
    ap.add_argument("--so-oficial", action="store_true",
                    help="avalia so validation.jsonl (obrigatorio para o modelo afinado)")
    ap.add_argument("--todos", action="store_true",
                    help="avalia as 316 imagens (train+validation). So para o modelo-BASE")
    ap.add_argument("--n-inconclusivo", type=int, default=25,
                    help="quantos INCONCLUSIVO amostrar do treino (default: 25)")
    ap.add_argument("--seed", type=int, default=42, help="seed da amostragem")
    ap.add_argument("--workers", type=int, default=6, help="chamadas em paralelo")
    ap.add_argument("--limit", type=int, default=0,
                    help="avalia so os N primeiros exemplos (teste rapido, gasta menos)")
    ap.add_argument("--holdout-vs", default=None, metavar="SUFIXO",
                    help="avalia validation.jsonl + tudo de train.jsonl que NAO "
                         "esta em train_<SUFIXO>.jsonl. Held-out legitimo para um "
                         "modelo treinado com o dataset subamostrado (ex.: v3lite)")
    ap.add_argument("--staging-dir", default=None,
                    help="acervo de imagens (default: build/staging). Use "
                         "build/staging_768 para o A/B de resolucao")
    ap.add_argument("--media-resolution", default=None,
                    choices=config.MEDIA_RESOLUTIONS,
                    help="detalhe visual enviado ao modelo (default: o de "
                         f"config.MEDIA_RESOLUTION = {config.MEDIA_RESOLUTION}). "
                         "E ESTE campo, nao a dimensao da imagem, que define o "
                         "custo em tokens")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help="JSON com o detalhe por exemplo (default: build/resultado_avaliacao.json)")
    args = ap.parse_args()

    _stdout_utf8()

    if not os.path.isdir(config.STAGING_DIR):
        sys.exit("Nao encontrei %s. Rode gerar_dataset.py antes." % config.STAGING_DIR)

    if args.holdout_vs:
        conjunto = montar_holdout(args.holdout_vs, args.staging_dir)
    else:
        conjunto = montar_conjunto(args.so_oficial, args.n_inconclusivo, args.seed,
                                   todos=args.todos, staging_dir=args.staging_dir)
    if args.limit:
        conjunto = conjunto[:args.limit]
        print("  --limit: avaliando so %d exemplos (metricas nao sao o baseline)"
              % len(conjunto))

    endpoint, origem = inferencia.resolve_endpoint()
    if args.endpoint:
        endpoint, origem = args.endpoint, "cli"
    print("  modelo: %s (origem: %s)" % (endpoint, origem))
    if origem == "fallback":
        print("  AVISO: modelo-BASE, nao o afinado. Este numero e o BASELINE.")
    elif not args.so_oficial and not args.holdout_vs:
        # --holdout-vs nao vaza: por construcao so inclui exemplos que aquele
        # dataset de treino nao usou.
        print("  AVISO: modelo afinado + conjunto diagnostico = VAZAMENTO."
              " Use --so-oficial ou --holdout-vs.")
    print("  projeto/regiao: %s / %s" % (config.PROJECT_ID, config.LOCATION))
    print()

    t0 = time.time()
    feitos = 0
    resultados = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(lambda it: avaliar_um(
                it, endpoint, media_resolution=args.media_resolution), conjunto):
            resultados.append(r)
            feitos += 1
            if feitos % 10 == 0 or feitos == len(conjunto):
                print("  %d/%d  (%.0fs)" % (feitos, len(conjunto), time.time() - t0))
    print()

    # Cabecalho junto do detalhe: sem isso o JSON nao diz QUAL modelo nem em que
    # regiao gerou os numeros, e o relatorio deixa de ser rastreavel.
    saida = {
        "meta": {
            "modelo": endpoint,
            "origem_endpoint": origem,
            "projeto": config.PROJECT_ID,
            "regiao": config.LOCATION,
            "conjunto": (f"holdout-vs-{args.holdout_vs}" if args.holdout_vs
                         else "oficial" if args.so_oficial
                         else "completo" if args.todos else "diagnostico"),
            "n": len(resultados),
            "staging_dir": args.staging_dir or config.STAGING_DIR,
            "media_resolution": args.media_resolution or config.MEDIA_RESOLUTION,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "itens": resultados,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=1)

    erros = [r for r in resultados if r["erro"]]
    ok = [r for r in resultados if not r["erro"]]
    print("=== FALHAS DE CHAMADA ===")
    print("  %d de %d" % (len(erros), len(resultados)))
    for r in erros[:5]:
        print("   %s -> %s" % (r["arquivo"], r["erro"][:160]))
    print()

    metricas("A) CONJUNTO OFICIAL DE VALIDACAO (validation.jsonl)",
             [r for r in ok if r["conjunto"] == "oficial"])
    if not args.so_oficial:
        print()
        if args.holdout_vs:
            titulo = ("B) HELD-OUT COMPLETO (oficial + nao-treinados de %s)"
                      % args.holdout_vs)
        elif args.todos:
            titulo = "B) CONJUNTO COMPLETO (316, 3 classes)"
        else:
            titulo = "B) CONJUNTO DIAGNOSTICO (3 classes)"
        metricas(titulo, ok)

    print()
    print("=== ACERTO POR MOTIVO ALEGADO ===")
    por_motivo = defaultdict(lambda: [0, 0])
    for r in ok:
        por_motivo[r["motivo"]][1] += 1
        if r["previsto"] == r["esperado"]:
            por_motivo[r["motivo"]][0] += 1
    print("  %-30s %6s %6s %8s" % ("motivo", "ok", "n", "acerto"))
    for m, (a, n) in sorted(por_motivo.items(), key=lambda kv: -kv[1][1]):
        print("  %-30s %6d %6d %7.0f%%" % ((m or "?")[:30], a, n, 100.0 * a / n))

    print()
    print("=== EXEMPLOS DE ERRO (ate 10) ===")
    for r in [x for x in ok if x["previsto"] != x["esperado"]][:10]:
        print("  %s" % r["arquivo"])
        print("     esperado=%s  previsto=%s  conf=%s" % (
            r["esperado"], r["previsto"], r.get("confianca")))
        print("     defeito=%r" % (r.get("defeito_identificado"),))
        print("     just=%s" % (r.get("justificativa") or "")[:150])

    print()
    print("Detalhe completo salvo em: %s" % args.out)


if __name__ == "__main__":
    main()
