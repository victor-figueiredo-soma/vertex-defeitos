#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Le o JSON do avaliar_modelo.py e responde "quao eficiente o modelo e na deteccao
de defeitos?" em tres dimensoes:

  1. DECISAO  - concordancia com o veredicto do analista (APROVADO/REPROVADO/
     INCONCLUSIVO). Delega para avaliar_modelo.metricas.
  2. DETECCAO - o modelo ENXERGOU o defeito? Binario `existe_defeito` com
     intervalo de confianca, os dois erros nomeados pelo custo de negocio, e
     acerto do tipo de defeito.
  3. CONFIANCA - da para confiar? Calibracao, ECE e curva cobertura x acuracia,
     que responde quanto do fluxo e automatizavel com seguranca.

NAO chama a API: reprocessa o JSON, entao rodar de novo nao custa nada.

Uso:
  uv run python analisar_deteccao.py --in build/resultado_base_2026-07-29.json
  uv run python analisar_deteccao.py --in ... --md build/relatorio.md
"""

import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict

import tuning.avaliar_modelo as avaliar_modelo
from tuning.gerar_dataset import parse_motivo

# Meta de acuracia para considerar uma decisao automatizavel sem revisao humana.
META_ACURACIA = 0.95
FAIXAS = [(0.0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 0.95), (0.95, 1.01)]
# Motivo generico do catalogo: o cliente nao especificou o defeito.
GENERICO = "Peça c/ defeito"


def wilson(k, n, z=1.96):
    """IC 95% para proporcao. Com n=13 no lado 'sem defeito', reportar a taxa
    nua seria apresentar ruido como se fosse medida."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1.0 + z * z / n
    centro = (p + z * z / (2 * n)) / d
    margem = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d
    return (max(0.0, centro - margem), min(1.0, centro + margem))


def pct(x):
    return "n/a" if x != x else "%.1f%%" % (100 * x)


def taxa(k, n):
    """Proporcao com IC de Wilson, no formato 'X/Y = Z% [a-b]'."""
    if n == 0:
        return "0/0 = n/a"
    lo, hi = wilson(k, n)
    return "%d/%d = %s [%s-%s]" % (k, n, pct(k / n), pct(lo), pct(hi))


def canonizar(texto):
    """O modelo devolve `defeito_identificado` em texto livre ('mancha escura na
    manga'); o ground truth usa o catalogo. Reusa o parser do gerador para nao
    ter duas definicoes de motivo no repo."""
    if not texto or not isinstance(texto, str):
        return None
    return parse_motivo(texto.lower())


# ----------------------------------------------------------------------------
# Dimensao 2 - deteccao
# ----------------------------------------------------------------------------
def secao_deteccao(itens):
    L = ["## 2. Deteccao do defeito", ""]

    # Os INCONCLUSIVO tem existe_defeito=None no alvo (nao ha verdade sobre o
    # defeito numa imagem degradada de proposito), logo saem da binaria.
    bin_itens = [r for r in itens if r.get("esperado_existe_defeito") in (0, 1)
                 and r.get("existe_defeito") in (0, 1)]
    sem_resposta = [r for r in itens if r.get("esperado_existe_defeito") in (0, 1)
                    and r.get("existe_defeito") not in (0, 1)]

    L += ["### 2.1 Existe defeito? (binario)", ""]
    if not bin_itens:
        L += ["_Sem itens com `existe_defeito` definido nos dois lados._", ""]
        return L

    vp = sum(1 for r in bin_itens if r["esperado_existe_defeito"] == 1 and r["existe_defeito"] == 1)
    fn = sum(1 for r in bin_itens if r["esperado_existe_defeito"] == 1 and r["existe_defeito"] == 0)
    fp = sum(1 for r in bin_itens if r["esperado_existe_defeito"] == 0 and r["existe_defeito"] == 1)
    vn = sum(1 for r in bin_itens if r["esperado_existe_defeito"] == 0 and r["existe_defeito"] == 0)
    n_pos, n_neg = vp + fn, fp + vn

    L += ["Matriz (linha = verdade, coluna = modelo), n=%d:" % len(bin_itens), "",
          "| | previu defeito | previu sem defeito |",
          "|---|---|---|",
          "| **tem defeito** (n=%d) | %d | %d |" % (n_pos, vp, fn),
          "| **sem defeito** (n=%d) | %d | %d |" % (n_neg, fp, vn), ""]
    if sem_resposta:
        L += ["> %d itens ficaram fora: o modelo nao devolveu `existe_defeito` "
              "0/1 (falha de parse ou INCONCLUSIVO)." % len(sem_resposta), ""]

    L += ["| Metrica | Valor (IC 95%) | Leitura |", "|---|---|---|",
          "| Recall de defeito | %s | dos defeitos reais, quantos o modelo viu |" % taxa(vp, n_pos),
          "| Especificidade | %s | das pecas sem defeito, quantas reconheceu |" % taxa(vn, n_neg),
          "| Precisao | %s | quando diz 'tem defeito', quanto acerta |" % taxa(vp, vp + fp),
          "| Acuracia | %s | — |" % taxa(vp + vn, len(bin_itens)), ""]

    L += ["**Os dois erros, pelo custo de negocio:**", "",
          "| Erro | Taxa | Consequencia |", "|---|---|---|",
          "| Falso aprovado (sem defeito -> aprova) | %s | aprova devolucao indevida: prejuizo |" % taxa(fp, n_neg),
          "| **Falso reprovado** (tem defeito -> nega) | %s | nega devolucao legitima: atrito com o cliente |" % taxa(fn, n_pos),
          ""]
    if n_neg < 30:
        L += ["> O lado 'sem defeito' tem so %d exemplos no acervo. O intervalo "
              "acima e largo de proposito — qualquer numero pontual dali e "
              "instavel e nao deve virar meta." % n_neg, ""]

    # --- 2.2 tipo do defeito
    L += ["### 2.2 Acerto do tipo de defeito", "",
          "> Limitacao a declarar: no dataset `defeito_identificado` foi derivado "
          "do mesmo nome de arquivo que o motivo alegado, logo os dois sao "
          "sempre iguais. Esta secao mede **concordancia com o motivo alegado**, "
          "nao acuracia contra um laudo independente.", ""]

    com_defeito = [r for r in itens if r.get("esperado_existe_defeito") == 1]
    por_tipo = defaultdict(lambda: {"n": 0, "acertou": 0, "nada": 0, "outro": 0})
    confusoes = Counter()
    for r in com_defeito:
        esperado = r.get("esperado_defeito")
        d = por_tipo[esperado]
        d["n"] += 1
        pred = canonizar(r.get("defeito_identificado"))
        if r.get("defeito_identificado") in (None, "", "null"):
            d["nada"] += 1
            confusoes[(esperado, "<nao viu defeito>")] += 1
        elif pred == esperado:
            d["acertou"] += 1
        elif pred is None:
            d["outro"] += 1
            confusoes[(esperado, "<fora do catalogo>")] += 1
        else:
            d["outro"] += 1
            confusoes[(esperado, pred)] += 1

    L += ["| Tipo real | n | acertou o tipo | nao viu defeito | apontou outro |",
          "|---|---|---|---|---|"]
    for tipo, d in sorted(por_tipo.items(), key=lambda kv: -kv[1]["n"]):
        marca = "" if d["n"] >= 10 else " ⚠"
        if tipo == GENERICO:
            marca = " †"
        L.append("| %s%s | %d | %s | %d | %d |" % (
            tipo, marca, d["n"], pct(d["acertou"] / d["n"]), d["nada"], d["outro"]))

    # 'Peca c/ defeito' e o motivo GENERICO: o cliente nao especificou e o
    # system_instruction manda o modelo NOMEAR o defeito que encontrar. Responder
    # 'Furo' ali e obedecer, nao divergir — contar como erro subestima o modelo.
    esp = {t: d for t, d in por_tipo.items() if t != GENERICO}
    tot_n = sum(d["n"] for d in esp.values())
    tot_ok = sum(d["acertou"] for d in esp.values())
    if tot_n:
        L += ["| **TOTAL (sem o generico)** | %d | **%s** | %d | %d |" % (
            tot_n, pct(tot_ok / tot_n),
            sum(d["nada"] for d in esp.values()),
            sum(d["outro"] for d in esp.values())), ""]
        L += ["⚠ = menos de 10 exemplos, nao conclusivo.", "",
              "† `%s` e o motivo generico (cliente nao especificou). O prompt manda "
              "o modelo NOMEAR o defeito, entao apontar um tipo especifico ali e o "
              "comportamento correto — por isso ele fica fora do total." % GENERICO, ""]
        g = por_tipo.get(GENERICO)
        if g:
            L += ["Nos %d casos genericos, o modelo nomeou algum defeito em %d "
                  "(%s) e nao viu defeito em %d." % (
                      g["n"], g["n"] - g["nada"], pct((g["n"] - g["nada"]) / g["n"]),
                      g["nada"]), ""]

    if confusoes:
        L += ["**Confusoes mais frequentes:**", ""]
        for (esp, pred), c in confusoes.most_common(8):
            L.append("- `%s` -> `%s` (n=%d)" % (esp, pred, c))
        L.append("")

    # --- 2.3 divergencia de motivo
    div = [r for r in com_defeito if r.get("divergencia_houve") == 1]
    L += ["### 2.3 Divergencia de motivo", "",
          "No dataset o esperado e sempre `houve=0`. O modelo apontou divergencia "
          "em **%s** dos casos com defeito real — cada um e um alerta de "
          "rotulagem ou de leitura equivocada." % taxa(len(div), len(com_defeito)),
          ""]
    return L


# ----------------------------------------------------------------------------
# Dimensao 3 - confianca
# ----------------------------------------------------------------------------
def secao_confianca(itens, meta_acuracia):
    L = ["## 3. Confianca: quanto da para automatizar", ""]
    com_conf = [r for r in itens
                if isinstance(r.get("confianca"), (int, float))
                and r.get("previsto")]
    if not com_conf:
        return L + ["_Sem confianca numerica nos resultados._", ""]

    for r in com_conf:
        r["_ok"] = 1 if r["previsto"] == r["esperado"] else 0

    def tabela_faixas(grupo_base):
        linhas, ece = [], 0.0
        for lo, hi in FAIXAS:
            grupo = [r for r in grupo_base if lo <= r["confianca"] < hi]
            if not grupo:
                continue
            acc = sum(r["_ok"] for r in grupo) / len(grupo)
            conf_media = sum(r["confianca"] for r in grupo) / len(grupo)
            ece += (len(grupo) / len(grupo_base)) * abs(acc - conf_media)
            linhas.append("| %.2f-%.2f | %d | %s | %s | %+.1f pp |" % (
                lo, min(hi, 1.0), len(grupo), pct(acc), pct(conf_media),
                100 * (acc - conf_media)))
        return linhas, ece

    cab = ["| Faixa | n | acuracia | confianca media | gap |", "|---|---|---|---|---|"]
    L += ["### 3.1 Acuracia por faixa de confianca", ""]

    # As imagens aug_* foram degradadas de proposito e o alvo delas e
    # INCONCLUSIVO: acertar com confianca baixa e o comportamento desejado, o que
    # gera um gap POSITIVO enorme e infla o ECE. Medir calibracao junto com elas
    # esconde a overconfianca nas fotos reais, que e o que importa em producao.
    reais = [r for r in com_conf if not r.get("sintetico")]
    sinteticos = [r for r in com_conf if r.get("sintetico")]

    L += ["**Fotos reais do acervo** (n=%d) — é o que vale para producao:" % len(reais), ""]
    linhas, ece_reais = tabela_faixas(reais)
    L += cab + linhas
    L += ["", "**ECE nas fotos reais = %.3f.** Gap negativo = o modelo esta mais "
          "confiante do que acertado (overconfianca)." % ece_reais, ""]

    if sinteticos:
        linhas_s, ece_s = tabela_faixas(sinteticos)
        acc_s = sum(r["_ok"] for r in sinteticos) / len(sinteticos)
        L += ["**Imagens degradadas artificialmente** (`aug_*`, n=%d, alvo "
              "INCONCLUSIVO), separadas de proposito:" % len(sinteticos), ""]
        L += cab + linhas_s
        L += ["", "Aqui o modelo acerta %s e o gap e POSITIVO — ele reconhece que "
              "a imagem e ruim e declara confianca baixa. Comportamento correto, "
              "nao descalibracao: e por isso que estas linhas nao entram no ECE "
              "acima." % pct(acc_s), ""]

    # --- curva cobertura x acuracia
    L += ["### 3.2 Curva cobertura x acuracia", "",
          "Ordenando da maior para a menor confianca: se automatizar so os X% "
          "mais confiantes, qual a acuracia?", "",
          "| Cobertura | n | acuracia |", "|---|---|---|"]
    ordenado = sorted(com_conf, key=lambda r: -r["confianca"])
    for frac in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
        k = max(1, int(round(frac * len(ordenado))))
        topo = ordenado[:k]
        L.append("| %d%% | %d | %s |" % (
            round(100 * frac), k, pct(sum(r["_ok"] for r in topo) / k)))
    L.append("")

    # --- limiar operacional
    L += ["### 3.3 Limiar operacional", ""]
    melhor = None
    for t in sorted({r["confianca"] for r in com_conf}, reverse=True):
        auto = [r for r in com_conf if r["confianca"] >= t]
        acc = sum(r["_ok"] for r in auto) / len(auto)
        if acc >= meta_acuracia:
            melhor = (t, len(auto), acc)
    if melhor:
        t, n, acc = melhor
        L += ["Com `CONFIDENCE_THRESHOLD >= %.2f`, %d de %d decisoes (%s do "
              "volume) ficam automaticas mantendo %s de acuracia." % (
                  t, n, len(com_conf), pct(n / len(com_conf)), pct(acc)), ""]
    else:
        acc_total = sum(r["_ok"] for r in com_conf) / len(com_conf)
        L += ["**Nenhum limiar de confianca atinge %s de acuracia.** Mesmo o "
              "subconjunto mais confiante erra acima do aceitavel (acuracia "
              "geral: %s), portanto a confianca do modelo **nao serve como rede "
              "de seguranca** e o `CONFIDENCE_THRESHOLD` nao protege o fluxo." % (
                  pct(meta_acuracia), pct(acc_total)), ""]

    # Criterio alternativo: incoerencia entre o campo de deteccao e a decisao.
    incoerentes = [r for r in com_conf
                   if (r.get("existe_defeito") == 0 and r["previsto"] == "APROVADO")
                   or (r.get("existe_defeito") == 1 and r["previsto"] == "REPROVADO")]
    L += ["**Criterio alternativo — coerencia interna:** %d de %d respostas sao "
          "internamente incoerentes (`existe_defeito` contradiz o `resultado`). "
          "Hoje `decision_router.route` nao checa isso; e um filtro que nao "
          "depende da confianca declarada." % (len(incoerentes), len(com_conf)), ""]
    return L


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Analisa a eficiencia do modelo na deteccao de defeitos.")
    ap.add_argument("--in", dest="entrada", required=True,
                    help="JSON gerado pelo avaliar_modelo.py")
    ap.add_argument("--md", default=None,
                    help="onde salvar o relatorio (default: ao lado do JSON)")
    ap.add_argument("--meta-acuracia", type=float, default=META_ACURACIA,
                    help="acuracia minima para considerar uma decisao automatizavel")
    args = ap.parse_args()

    avaliar_modelo._stdout_utf8()

    with open(args.entrada, encoding="utf-8") as f:
        dados = json.load(f)
    # Aceita o formato novo {meta, itens} e o antigo (lista crua).
    meta = dados.get("meta", {}) if isinstance(dados, dict) else {}
    itens = dados.get("itens", []) if isinstance(dados, dict) else dados
    ok = [r for r in itens if not r.get("erro")]

    linhas = [
        "# Eficiencia do modelo na deteccao de defeitos", "",
        "| | |", "|---|---|",
        "| Modelo | `%s` (%s) |" % (meta.get("modelo", "?"),
                                    meta.get("origem_endpoint", "?")),
        "| Projeto / regiao | %s / %s |" % (meta.get("projeto", "?"),
                                            meta.get("regiao", "?")),
        "| Conjunto | %s |" % meta.get("conjunto", "?"),
        "| Avaliado em | %s |" % meta.get("timestamp", "?"),
        "| Itens | %d avaliados, %d falhas de chamada |" % (
            len(ok), len(itens) - len(ok)),
        "",
    ]
    if meta.get("origem_endpoint") == "fallback":
        linhas += ["> Modelo-BASE, nao afinado: estes numeros sao o **baseline**.", ""]

    # Dimensao 1: reusa o que o avaliar_modelo.py ja calcula, sem reimplementar.
    linhas += ["## 1. Decisao (concordancia com o analista)", "", "```"]
    import io
    buf, orig = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        avaliar_modelo.metricas("CONJUNTO AVALIADO", ok)
    finally:
        sys.stdout = orig
    linhas += buf.getvalue().rstrip().split("\n") + ["```", ""]

    linhas += secao_deteccao(ok)
    linhas += secao_confianca(ok, args.meta_acuracia)

    texto = "\n".join(linhas)
    print(texto)

    destino = args.md or os.path.join(
        os.path.dirname(args.entrada) or ".",
        "relatorio_" + os.path.basename(args.entrada).replace(".json", "") + ".md")
    with open(destino, "w", encoding="utf-8") as f:
        f.write(texto + "\n")
    print("\nRelatorio salvo em: %s" % destino)


if __name__ == "__main__":
    main()
