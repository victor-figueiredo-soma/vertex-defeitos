# -*- coding: utf-8 -*-
"""Testes da politica de decisao hibrida (funcao pura)."""

from app.routing import decision_router


def _julg(resultado, confianca, revisao=0):
    return {"resultado": resultado, "confianca": confianca, "requer_revisao_manual": revisao}


def test_aprovado_alta_confianca_e_automatico():
    r = decision_router.route(_julg("APROVADO", 0.95), threshold=0.70)
    assert r["auto"] is True
    assert r["status"] == decision_router.STATUS_AUTO
    assert r["motivos_revisao"] == []


def test_reprovado_alta_confianca_e_automatico():
    r = decision_router.route(_julg("REPROVADO", 0.80), threshold=0.70)
    assert r["auto"] is True
    assert r["decision"] == "REPROVADO"


def test_confianca_abaixo_do_limiar_vai_para_revisao():
    r = decision_router.route(_julg("APROVADO", 0.50), threshold=0.70)
    assert r["auto"] is False
    assert r["status"] == decision_router.STATUS_REVIEW
    assert any("confianca_abaixo" in m for m in r["motivos_revisao"])


def test_inconclusivo_sempre_vai_para_revisao():
    r = decision_router.route(_julg("INCONCLUSIVO", 0.99), threshold=0.70)
    assert r["auto"] is False
    assert "resultado_inconclusivo" in r["motivos_revisao"]


def test_flag_requer_revisao_manual_forca_revisao():
    r = decision_router.route(_julg("APROVADO", 0.99, revisao=1), threshold=0.70)
    assert r["auto"] is False
    assert "flag_requer_revisao_manual" in r["motivos_revisao"]


def test_confianca_ausente_trata_como_zero():
    r = decision_router.route({"resultado": "APROVADO"}, threshold=0.70)
    assert r["auto"] is False
    assert r["confianca"] == 0.0
