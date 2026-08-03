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


def test_resposta_aprovado_contem_veredito_e_motivo_alegado():
    txt = processor.montar_resposta_cliente(_julg("APROVADO"), "Fio puxado")
    assert "APROVADA" in txt
    assert "Fio puxado" in txt


def test_resposta_reprovado_orienta_novas_fotos():
    txt = processor.montar_resposta_cliente(_julg("REPROVADO"), "Mancha")
    assert "NÃO foi aprovada" in txt
    assert "novas" in txt.lower()


def test_resposta_nunca_repassa_texto_do_modelo():
    """O modelo ecoa o motivo alegado; a justificativa dele pode nomear o defeito
    errado. Ver build/RELATORIO_TESTE_PASTA.md - num tecido rasgado alegado como
    'Mancha' ele respondeu 'Mancha localizada identificada'."""
    julg = _julg("APROVADO")
    julg["justificativa"] = "Mancha localizada identificada na peça."
    julg["defeito_identificado"] = "Mancha"
    for resultado in ("APROVADO", "REPROVADO"):
        julg["resultado"] = resultado
        txt = processor.montar_resposta_cliente(julg, "Furo")
        assert "Mancha" not in txt, f"{resultado} vazou a justificativa do modelo"
        assert "localizada identificada" not in txt


def test_resposta_sem_motivo_nao_quebra():
    txt = processor.montar_resposta_cliente(_julg("APROVADO"), "")
    assert "APROVADA" in txt
    assert '""' not in txt  # nao deixa aspas vazias no texto


# ---------------------------------------------------------------------------
# Trava de escopo por assunto. Sem ela, qualquer e-mail com imagem que caia na
# caixa monitorada seria julgado e receberia resposta automatica - inclusive
# marketing e notificacao de servico.
# ---------------------------------------------------------------------------
def test_assunto_com_o_termo_passa(monkeypatch):
    monkeypatch.setattr(processor.settings, "ASSUNTO_FILTRO", "devolucao")
    assert processor.assunto_no_escopo("Devolucao - peca manchada")


def test_acento_e_caixa_nao_importam(monkeypatch):
    """O cliente digita de qualquer jeito; o filtro nao pode ser literal."""
    monkeypatch.setattr(processor.settings, "ASSUNTO_FILTRO", "devolucao")
    for assunto in ("Devolução de peça", "DEVOLUÇÃO", "devoluçao", "DeVoLuCaO"):
        assert processor.assunto_no_escopo(assunto), assunto


def test_filtro_com_acento_casa_assunto_sem_acento(monkeypatch):
    """Vale nos dois sentidos: filtro acentuado, assunto sem acento."""
    monkeypatch.setattr(processor.settings, "ASSUNTO_FILTRO", "devolução")
    assert processor.assunto_no_escopo("DEVOLUCAO peca com furo")


def test_termo_em_qualquer_posicao_passa(monkeypatch):
    monkeypatch.setattr(processor.settings, "ASSUNTO_FILTRO", "devolucao")
    assert processor.assunto_no_escopo("Re: Fwd: minha devolucao")


def test_assunto_sem_o_termo_e_barrado(monkeypatch):
    monkeypatch.setattr(processor.settings, "ASSUNTO_FILTRO", "devolucao")
    for assunto in ("All aboard! Welcome to Railway!", "Peça com defeito",
                    "Suas reuniões desta semana", "", None):
        assert not processor.assunto_no_escopo(assunto), assunto


def test_filtro_vazio_desliga_a_trava(monkeypatch):
    monkeypatch.setattr(processor.settings, "ASSUNTO_FILTRO", "")
    assert processor.assunto_no_escopo("qualquer coisa")
    assert processor.assunto_no_escopo("")


def test_fora_do_escopo_nao_baixa_anexo_nem_responde(monkeypatch):
    """Sair ANTES do get_attachments: e-mail fora do fluxo nao deve consumir
    download de bytes, chamada ao Vertex nem gerar e-mail."""
    monkeypatch.setattr(processor.settings, "ASSUNTO_FILTRO", "devolucao")
    monkeypatch.setattr(processor.graph_client, "get_message",
                        lambda mid: {"subject": "Newsletter da semana",
                                     "from": {"emailAddress": {"address": "x@y.com"}}})

    chamou = []
    monkeypatch.setattr(processor.graph_client, "get_attachments",
                        lambda mid: chamou.append("anexo") or [])
    monkeypatch.setattr(processor.graph_client, "send_mail",
                        lambda *a, **kw: chamou.append("email"))
    processor._processadas.clear()
    processor.processar_mensagem("id-fora-do-escopo")
    assert chamou == [], f"nao deveria ter feito nada, fez: {chamou}"


# ---------------------------------------------------------------------------
# Roteamento das notificacoes internas: erro e revisao sao canais distintos.
# ---------------------------------------------------------------------------
def _capturar_envios(monkeypatch):
    enviados = []
    monkeypatch.setattr(processor.graph_client, "send_mail",
                        lambda to, subj, body, **kw: enviados.append((to, subj)))
    return enviados


def test_erro_vai_para_alert_email(monkeypatch):
    enviados = _capturar_envios(monkeypatch)
    monkeypatch.setattr(processor.settings, "ALERT_EMAIL", "erros@x.com")
    monkeypatch.setattr(processor.settings, "REVIEW_EMAIL", "revisao@x.com")
    processor.alertar("Falha na Vertex", "detalhe")
    assert enviados == [("erros@x.com", "[vertex-defeitos ERRO] Falha na Vertex")]


def test_revisao_vai_para_review_email(monkeypatch):
    enviados = _capturar_envios(monkeypatch)
    monkeypatch.setattr(processor.settings, "ALERT_EMAIL", "erros@x.com")
    monkeypatch.setattr(processor.settings, "REVIEW_EMAIL", "revisao@x.com")
    processor.enfileirar_revisao("INCONCLUSIVO - imagem ruim", "detalhe")
    assert enviados[0][0] == "revisao@x.com"
    assert "REVISAO" in enviados[0][1]


def test_notificacao_sem_destino_nao_levanta(monkeypatch):
    """Chamado de dentro de handler de erro: falhar aqui mascararia o original."""
    _capturar_envios(monkeypatch)
    monkeypatch.setattr(processor.settings, "ALERT_EMAIL", "")
    monkeypatch.setattr(processor.settings, "REVIEW_EMAIL", "")
    processor.alertar("x", "y")
    processor.enfileirar_revisao("x", "y")


def test_send_mail_que_falha_nao_propaga(monkeypatch):
    def explode(*a, **kw):
        raise RuntimeError("Graph fora do ar")
    monkeypatch.setattr(processor.graph_client, "send_mail", explode)
    monkeypatch.setattr(processor.settings, "ALERT_EMAIL", "erros@x.com")
    processor.alertar("x", "y")  # nao deve levantar
