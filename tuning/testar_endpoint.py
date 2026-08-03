#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Teste do endpoint afinado em imagens FORA da distribuicao de treino.

Por que isso vale: o held-out de 149 exemplos que produziu os 96,0% vem do mesmo
acervo do treino (mesmas cameras, mesmo fundo, mesmo fluxo de devolucao). Estas
imagens sao de outra origem — fotos de catalogo e de celular pescadas na web — e
incluem um NEGATIVO, classe que nunca teve medicao limpa (os 13 REPROVADO do
acervo estao todos no treino).

Roda cada imagem em tres estrategias de motivo, porque a resposta correta muda:

  generico : "Peca c/ defeito" — o cliente nao especificou. Mede DETECCAO pura.
  correto  : o motivo real da peca.            Mede CONFIRMACAO.
  errado   : um motivo deliberadamente falso.  Mede DIVERGENCIA — fraqueza
             conhecida, porque no treino divergencia_motivo.houve era 0 em
             100% dos 166 exemplos. O prompt manda aprovar quando ha defeito
             real e apenas REGISTRAR a divergencia; o SFT nunca viu isso.

Uso:
  uv run python -m tuning.testar_endpoint
  uv run python -m tuning.testar_endpoint --pasta "C:/caminho" --estrategias generico
"""

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import config
import inferencia

PASTA_DEFAULT = r"C:\Users\Victor_figueiredo\Downloads\pasta_teste"

# Ground truth. 'defeito' = ha defeito fisico real na peca (existe_defeito=1).
# 'motivo_correto' e o motivo que a foto de fato mostra; 'motivo_errado' e um
# motivo do catalogo que NAO corresponde, para testar divergencia.
CASOS = [
    {
        "arquivo": "images.jpg",
        "descricao": "sueter azul ribana destruido, varios rasgos grandes",
        "defeito": True,
        "esperado": "APROVADO",
        "motivo_correto": "Furo",
        "motivo_errado": "Sem botão",
    },
    {
        "arquivo": "images (1).jpg",
        "descricao": "tecido preto rasgado junto a costura, bordas desfiadas",
        "defeito": True,
        "esperado": "APROVADO",
        "motivo_correto": "Furo",
        "motivo_errado": "Mancha",
    },
    {
        "arquivo": "images (3).jpg",
        "descricao": "robe fleece verde, ziper arrebentado/solto",
        "defeito": True,
        "esperado": "APROVADO",
        "motivo_correto": "Zíper",
        "motivo_errado": "Peça suja/mofada",
    },
    {
        "arquivo": "images (4).jpg",
        "descricao": "peca listrada infantil com mancha vermelha",
        "defeito": True,
        "esperado": "APROVADO",
        "motivo_correto": "Mancha",
        "motivo_errado": "Zíper",
    },
    {
        # O unico negativo. E o teste de ESPECIFICIDADE: com um motivo alegado
        # qualquer e nenhum defeito na peca, o correto e REPROVADO.
        "arquivo": "49FP-AX7EN-C1.jpeg",
        "descricao": "camiseta marinho lisa de catalogo, sem defeito visivel",
        "defeito": False,
        "esperado": "REPROVADO",
        "motivo_correto": "Mancha",   # alegado, mas inexistente na peca
        "motivo_errado": "Furo",
    },
]

GENERICO = "Peça c/ defeito"


def _stdout_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def motivo_da_estrategia(caso, estrategia):
    if estrategia == "generico":
        return GENERICO
    if estrategia == "correto":
        return caso["motivo_correto"]
    return caso["motivo_errado"]


def rodar_um(pasta, caso, estrategia, endpoint):
    """Uma chamada ao endpoint. Mede latencia. Nunca levanta."""
    motivo = motivo_da_estrategia(caso, estrategia)
    caminho = os.path.join(pasta, caso["arquivo"])
    t0 = time.perf_counter()
    try:
        julgamento, _ = inferencia.classify_file(caminho, motivo, endpoint=endpoint)
        erro = None
    except Exception as e:
        julgamento, erro = {}, f"{type(e).__name__}: {e}"
    latencia = time.perf_counter() - t0

    div = julgamento.get("divergencia_motivo") or {}
    return {
        "arquivo": caso["arquivo"],
        "descricao": caso["descricao"],
        "estrategia": estrategia,
        "motivo_alegado": motivo,
        "esperado": caso["esperado"],
        "defeito_real": caso["defeito"],
        "previsto": julgamento.get("resultado"),
        "existe_defeito": julgamento.get("existe_defeito"),
        "defeito_identificado": julgamento.get("defeito_identificado"),
        "motivo_correto_modelo": julgamento.get("motivo_correto"),
        "divergencia_houve": div.get("houve") if isinstance(div, dict) else None,
        "divergencia_sugerido": (div.get("motivo_correto_sugerido")
                                 if isinstance(div, dict) else None),
        "confianca": julgamento.get("confianca"),
        "requer_revisao_manual": julgamento.get("requer_revisao_manual"),
        "justificativa": julgamento.get("justificativa"),
        "latencia_s": round(latencia, 2),
        "erro": erro,
    }


def imprimir_tabela(titulo, linhas):
    print("=" * 100)
    print(titulo)
    print("=" * 100)
    print(f"  {'arquivo':24} {'motivo alegado':20} {'esperado':11} "
          f"{'previsto':12} {'conf':>5} {'det':>4} {'div':>4} {'seg':>6}")
    for r in linhas:
        det = {1: "sim", 0: "nao", None: "?"}.get(r["existe_defeito"], "?")
        marca = " " if r["previsto"] == r["esperado"] else "X"
        conf = f"{r['confianca']:.2f}" if isinstance(r["confianca"], (int, float)) else "?"
        print(f"{marca} {r['arquivo'][:24]:24} {r['motivo_alegado'][:20]:20} "
              f"{r['esperado']:11} {str(r['previsto']):12} {conf:>5} {det:>4} "
              f"{str(r['divergencia_houve']):>4} {r['latencia_s']:>6.2f}")
        if r["erro"]:
            print(f"    ERRO: {r['erro'][:110]}")


def resumo(linhas):
    print()
    print("=" * 100)
    print("RESUMO")
    print("=" * 100)

    ok = [r for r in linhas if not r["erro"]]
    print(f"  chamadas: {len(linhas)}  falhas: {len(linhas) - len(ok)}")
    if not ok:
        return

    lat = [r["latencia_s"] for r in ok]
    print(f"  latencia: media {statistics.mean(lat):.2f}s | mediana "
          f"{statistics.median(lat):.2f}s | min {min(lat):.2f}s | max {max(lat):.2f}s")

    print()
    print("  --- acerto de DECISAO por estrategia ---")
    for est in ("generico", "correto", "errado"):
        sub = [r for r in ok if r["estrategia"] == est]
        if not sub:
            continue
        acertos = sum(1 for r in sub if r["previsto"] == r["esperado"])
        print(f"    {est:9} {acertos}/{len(sub)}")

    print()
    print("  --- DETECCAO de defeito (existe_defeito vs realidade) ---")
    com = [r for r in ok if r["defeito_real"]]
    sem = [r for r in ok if not r["defeito_real"]]
    if com:
        viu = sum(1 for r in com if r["existe_defeito"] == 1)
        print(f"    recall (viu o defeito quando havia)   : {viu}/{len(com)}")
    if sem:
        nao = sum(1 for r in sem if r["existe_defeito"] == 0)
        print(f"    especificidade (negou quando nao havia): {nao}/{len(sem)}"
              "   <- nunca medido antes")

    print()
    print("  --- DIVERGENCIA de motivo (estrategia 'errado') ---")
    err = [r for r in ok if r["estrategia"] == "errado" and r["defeito_real"]]
    if err:
        sinalizou = sum(1 for r in err if r["divergencia_houve"] == 1)
        print(f"    sinalizou divergencia: {sinalizou}/{len(err)}")
        print("    (esperado baixo: divergencia_motivo.houve era 0 em 100% do treino)")

    print()
    print("  --- confianca ---")
    import collections
    conf = collections.Counter(r["confianca"] for r in ok)
    print(f"    distribuicao: {dict(conf)}")
    print("    (esperado 1.0 em toda foto nitida - `confianca` nao e incerteza")
    print("     calibrada, e um detector de 'a imagem esta ruim?'. Ver README.)")


def main():
    _stdout_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--pasta", default=PASTA_DEFAULT)
    ap.add_argument("--estrategias", nargs="+",
                    default=["generico", "correto", "errado"],
                    choices=["generico", "correto", "errado"])
    ap.add_argument("--endpoint", default=None,
                    help="default: TUNED_ENDPOINT do .env")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--out", default="build/resultado_pasta_teste.json")
    args = ap.parse_args()

    endpoint = args.endpoint or config.TUNED_ENDPOINT
    if not endpoint:
        sys.exit("Nenhum endpoint. Defina TUNED_ENDPOINT no .env ou use --endpoint.")

    faltando = [c["arquivo"] for c in CASOS
                if not os.path.exists(os.path.join(args.pasta, c["arquivo"]))]
    if faltando:
        sys.exit(f"Arquivos ausentes em {args.pasta}: {faltando}")

    print(f"endpoint  : {endpoint}")
    print(f"pasta     : {args.pasta}")
    print(f"casos     : {len(CASOS)}  estrategias: {args.estrategias}")
    print(f"chamadas  : {len(CASOS) * len(args.estrategias)}")
    print(f"mediaRes  : {config.MEDIA_RESOLUTION}  |  max_dim: "
          f"{os.environ.get('IMAGEM_MAX_DIM', '768')}px")
    print()

    tarefas = [(c, e) for e in args.estrategias for c in CASOS]
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        linhas = list(ex.map(
            lambda t: rodar_um(args.pasta, t[0], t[1], endpoint), tarefas))

    for est in args.estrategias:
        sub = [r for r in linhas if r["estrategia"] == est]
        rotulo = {
            "generico": "A) MOTIVO GENERICO — 'Peça c/ defeito' (mede DETECCAO)",
            "correto": "B) MOTIVO CORRETO (mede CONFIRMACAO)",
            "errado": "C) MOTIVO ERRADO de proposito (mede DIVERGENCIA)",
        }[est]
        imprimir_tabela(rotulo, sub)
        print()

    resumo(linhas)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"endpoint": endpoint, "pasta": args.pasta,
                   "media_resolution": config.MEDIA_RESOLUTION,
                   "itens": linhas}, f, ensure_ascii=False, indent=2)
    print()
    print(f"detalhe salvo em: {args.out}")


if __name__ == "__main__":
    main()
