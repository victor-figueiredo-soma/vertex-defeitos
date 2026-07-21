#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Confirmacao de rotulo por humano — a UNICA porta que alimenta o re-treino.

Quando um analista revisa uma devolucao e confirma (ou corrige) o julgamento, o
rotulo vira VERDADE e passa a poder ser usado em treino. So aqui o contador de
re-treino e incrementado — nunca a partir da propria previsao do modelo. Isso
evita realimentar o modelo com os proprios erros (confirmation bias / colapso).

A UI de revisao em si esta fora de escopo; este modulo define o contrato: recebe
o rotulo confirmado, persiste e (ao atingir o limiar) dispara o re-treino.
"""

from app import settings
from app.persistence import repository, counter
from app.inference.classifier import REQUIRED_KEYS, RESULTADOS_VALIDOS


def _validar_rotulo(label):
    """Garante que o rotulo confirmado siga o schema do system_instruction."""
    if not isinstance(label, dict):
        raise ValueError("rotulo confirmado deve ser um objeto JSON")
    faltando = REQUIRED_KEYS - set(label)
    if faltando:
        raise ValueError(f"rotulo confirmado incompleto (faltam: {sorted(faltando)})")
    if label.get("resultado") not in RESULTADOS_VALIDOS:
        raise ValueError(f"resultado invalido: {label.get('resultado')!r}")


def confirmar(devolucao_id, confirmed_label, confirmed_by):
    """Registra o rotulo confirmado e, se atingir o limiar, dispara o re-treino.

    Retorna dict: { devolucao_id, counter } onde counter e o resultado de
    counter.increment_and_maybe_trigger (inclui 'fired').
    """
    from google.cloud import firestore

    _validar_rotulo(confirmed_label)

    if repository.get_devolucao(devolucao_id) is None:
        raise LookupError(f"devolucao {devolucao_id} nao encontrada")

    repository.salvar_devolucao(devolucao_id, {
        "status": "confirmed",
        "decision": confirmed_label.get("resultado"),
        "confirmed_label": confirmed_label,
        "confirmed_by": confirmed_by,
        "confirmed_at": firestore.SERVER_TIMESTAMP,
        "used_in_training": False,
    })

    # So agora o contador sobe — 1 rotulo confirmado = 1 nova amostra de verdade.
    resultado_counter = counter.increment_and_maybe_trigger(
        threshold=settings.RETRAIN_THRESHOLD_INPUTS)

    return {"devolucao_id": devolucao_id, "counter": resultado_counter}
