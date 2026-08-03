#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes das funcoes puras do processor: politica de decisao e textos."""

from app import processor


def _julg(resultado="APROVADO", confianca=0.95, revisao=0):
    return {"resultado": resultado, "confianca": confianca,
            "requer_revisao_manual": revisao,
            "justificativa": "Mancha identificada."}


def test_aprovado_confiante_responde():
    acao, _ = processor.decidir_acao(_julg("APROVADO", 0.95))
    assert acao == "responder"


def test_reprovado_confiante_responde():
    acao, _ = processor.decidir_acao(_julg("REPROVADO", 0.90))
    assert acao == "responder"


def test_inconclusivo_sempre_revisa():
    # Mesmo com confianca alta: INCONCLUSIVO nunca vai automatico.
    acao, motivo = processor.decidir_acao(_julg("INCONCLUSIVO", 0.99))
    assert acao == "revisar"
    assert "INCONCLUSIVO" in motivo


def test_confianca_baixa_revisa():
    acao, motivo = processor.decidir_acao(_julg("APROVADO", 0.50))
    assert acao == "revisar"
    assert "limiar" in motivo


def test_confianca_no_limiar_responde():
    # 0.70 e >= limiar: responde (a regra do prompt e '< 0,70 -> revisao').
    acao, _ = processor.decidir_acao(_julg("REPROVADO", 0.70))
    assert acao == "responder"


def test_revisao_manual_do_modelo_vence_confianca():
    acao, motivo = processor.decidir_acao(_julg("APROVADO", 0.99, revisao=1))
    assert acao == "revisar"
    assert "revisao manual" in motivo


def test_confianca_ausente_revisa():
    j = _julg("APROVADO")
    j["confianca"] = None
    acao, _ = processor.decidir_acao(j)
    assert acao == "revisar"


def test_resposta_aprovado_contem_veredito_e_justificativa():
    txt = processor.montar_resposta_cliente(_julg("APROVADO"))
    assert "APROVADA" in txt
    assert "Mancha identificada." in txt


def test_resposta_reprovado_orienta_novas_fotos():
    txt = processor.montar_resposta_cliente(_julg("REPROVADO"))
    assert "NÃO foi aprovada" in txt
    assert "novas" in txt.lower()
