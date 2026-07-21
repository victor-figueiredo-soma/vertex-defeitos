#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Politica de decisao HIBRIDA por confianca.

Transforma o JSON do modelo em um encaminhamento operacional:
  - Alta confianca + resultado definido (APROVADO/REPROVADO) + sem flag de
    revisao  -> decisao AUTOMATICA.
  - INCONCLUSIVO, confianca < limiar, ou requer_revisao_manual == 1
    -> FILA HUMANA (pending_review).

Funcao pura (sem I/O): facil de testar e de auditar. O limiar espelha o
system_instruction (confianca < 0.70 -> revisao).
"""

from app import settings

# Estados possiveis do registro apos o roteamento.
STATUS_AUTO = "auto_decided"
STATUS_REVIEW = "pending_review"


def route(julgamento, threshold=None):
    """Decide o encaminhamento a partir do JSON de julgamento.

    Retorna um dict:
      {
        "status": "auto_decided" | "pending_review",
        "decision": "APROVADO" | "REPROVADO" | "INCONCLUSIVO",
        "auto": bool,
        "confianca": float,
        "motivos_revisao": [ ... ]   # por que foi para revisao (se foi)
      }
    """
    if threshold is None:
        threshold = settings.CONFIDENCE_THRESHOLD

    resultado = julgamento.get("resultado")
    try:
        confianca = float(julgamento.get("confianca") or 0.0)
    except (TypeError, ValueError):
        confianca = 0.0
    requer_revisao = bool(julgamento.get("requer_revisao_manual"))

    motivos_revisao = []
    if resultado == "INCONCLUSIVO":
        motivos_revisao.append("resultado_inconclusivo")
    if requer_revisao:
        motivos_revisao.append("flag_requer_revisao_manual")
    if confianca < threshold:
        motivos_revisao.append(f"confianca_abaixo_do_limiar_{threshold}")

    auto = not motivos_revisao and resultado in ("APROVADO", "REPROVADO")

    return {
        "status": STATUS_AUTO if auto else STATUS_REVIEW,
        "decision": resultado,
        "auto": auto,
        "confianca": confianca,
        "motivos_revisao": motivos_revisao,
    }
