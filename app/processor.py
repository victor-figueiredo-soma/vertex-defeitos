#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pipeline de processamento de uma notificacao: e-mail -> inferencia -> resposta.

Roda em background task (o webhook ja devolveu 202). Toda falha vira alerta para
ALERT_EMAIL — nunca exception silenciosa, porque aqui ninguem esta olhando o
terminal.

Politica de resposta (decidir_acao):
  - APROVADO/REPROVADO com confianca >= CONFIDENCE_THRESHOLD -> responde o
    cliente automaticamente.
  - INCONCLUSIVO, confianca baixa ou requer_revisao_manual -> cliente recebe
    "em analise" e ALERT_EMAIL recebe o caso para decisao humana.
  E a mesma regra do system_instruction.md (confianca < 0,70 -> revisao): o
  modelo foi treinado com ela, o app apenas a executa.
"""

import logging
import traceback

import inferencia
from app import email_parser, graph_client, settings

log = logging.getLogger("processor")

# Dedup em memoria: o Graph pode re-notificar a mesma mensagem (retry proprio ou
# subscription duplicada). Num servico de 1 instancia como o Railway isso cobre o
# caso comum; se um dia houver replica, trocar por um store compartilhado.
_processadas = set()
_MAX_PROCESSADAS = 5000


def decidir_acao(julgamento):
    """(acao, motivo_da_acao) para um julgamento do modelo.

    acao: 'responder'  -> veredito vai direto ao cliente
          'revisar'    -> humano decide; cliente recebe 'em analise'
    Funcao PURA - testavel sem rede.
    """
    resultado = julgamento.get("resultado")
    confianca = julgamento.get("confianca") or 0.0
    revisao = julgamento.get("requer_revisao_manual")

    if resultado == "INCONCLUSIVO":
        return "revisar", "resultado INCONCLUSIVO"
    if revisao:
        return "revisar", "modelo pediu revisao manual"
    if confianca < settings.CONFIDENCE_THRESHOLD:
        return "revisar", (f"confianca {confianca:.2f} abaixo do limiar "
                           f"{settings.CONFIDENCE_THRESHOLD:.2f}")
    return "responder", f"{resultado} com confianca {confianca:.2f}"


def montar_resposta_cliente(julgamento, motivo_alegado=""):
    """Texto do e-mail de resposta automatica. Funcao PURA.

    NAO repassa a `justificativa` nem o `defeito_identificado` do modelo. Medido
    em 2026-08-03 (build/RELATORIO_TESTE_PASTA.md): quando o cliente alega um
    motivo especifico, o modelo ECOA a alegacao em vez de identificar o defeito -
    num tecido rasgado alegado como "Mancha" ele respondeu "Mancha localizada
    identificada na peca". A causa e o dataset: `defeito_identificado` foi
    derivado do mesmo nome de arquivo que `motivo_alegado`, logo sao iguais em
    166/166 exemplos de treino.

    A DECISAO (aprovar/reprovar) se manteve correta nos testes; a EXPLICACAO nao.
    Como este texto vai para o cliente, ele e montado aqui a partir do motivo que
    NOS extraimos do e-mail — nunca de texto livre gerado pelo modelo. O
    julgamento completo continua indo integral para ALERT_EMAIL, onde um humano
    sabe interpretar.
    """
    alegado = (motivo_alegado or "").strip()
    trecho_motivo = f' referente a "{alegado}"' if alegado else ""

    if julgamento.get("resultado") == "APROVADO":
        return (
            "Olá!\n\n"
            f"Analisamos as imagens enviadas e sua solicitação de devolução"
            f"{trecho_motivo} foi APROVADA.\n\n"
            "Confirmamos a presença de defeito na peça.\n\n"
            "Em breve você receberá as instruções de postagem.\n\n"
            "Atenciosamente,\nEquipe de Devoluções"
        )
    return (
        "Olá!\n\n"
        f"Analisamos as imagens enviadas e sua solicitação de devolução"
        f"{trecho_motivo} NÃO foi aprovada: não identificamos, nas fotos "
        "recebidas, defeito compatível com o motivo informado.\n\n"
        "Se você acredita que houve um engano, responda este e-mail com novas "
        "fotos que mostrem o defeito de perto e com boa iluminação — vamos "
        "reavaliar.\n\n"
        "Atenciosamente,\nEquipe de Devoluções"
    )


RESPOSTA_EM_ANALISE = (
    "Olá!\n\n"
    "Recebemos sua solicitação de devolução. Ela está em análise pela nossa "
    "equipe e você receberá uma resposta em breve.\n\n"
    "Atenciosamente,\nEquipe de Devoluções"
)

RESPOSTA_SEM_IMAGEM = (
    "Olá!\n\n"
    "Recebemos sua solicitação de devolução, mas não encontramos nenhuma FOTO "
    "da peça em anexo — e a análise depende dela.\n\n"
    "Por favor, responda este e-mail anexando fotos que mostrem claramente o "
    "defeito alegado.\n\n"
    "Atenciosamente,\nEquipe de Devoluções"
)


def _notificar(destino, prefixo, assunto, corpo, rotulo_config):
    """Envia notificacao interna. Nunca levanta: notificacao que falha vira log.

    Nao pode propagar excecao porque e chamada de dentro dos handlers de erro -
    falhar aqui mascararia o problema original.
    """
    if not destino:
        log.error("%s nao configurado; notificacao perdida: %s",
                  rotulo_config, assunto)
        return
    try:
        graph_client.send_mail(destino, f"{prefixo} {assunto}", corpo)
    except Exception:
        log.exception("falha ao notificar '%s'", assunto)


def alertar(assunto, corpo):
    """ERRO DE EXECUCAO -> ALERT_EMAIL. Algo quebrou; alguem olha o sistema."""
    _notificar(settings.ALERT_EMAIL, "[vertex-defeitos ERRO]",
               assunto, corpo, "ALERT_EMAIL")


def enfileirar_revisao(assunto, corpo):
    """REVISAO HUMANA -> REVIEW_EMAIL. Nao e erro: e devolucao esperando decisao.

    O cliente ja recebeu "em analise"; se ninguem for avisado, o caso morre em
    silencio. Por isso REVIEW_EMAIL cai no ALERT_EMAIL quando nao configurado.
    """
    _notificar(settings.REVIEW_EMAIL, "[vertex-defeitos REVISAO]",
               assunto, corpo, "REVIEW_EMAIL/ALERT_EMAIL")


def processar_mensagem(message_id):
    """Processa UMA mensagem, fim a fim. Chamado pela background task do webhook."""
    if message_id in _processadas:
        log.info("mensagem %s ja processada; ignorando re-notificacao", message_id)
        return
    if len(_processadas) > _MAX_PROCESSADAS:
        _processadas.clear()
    _processadas.add(message_id)

    log.info("processando mensagem %s", message_id)

    # 1. Buscar o e-mail no Graph
    try:
        message = graph_client.get_message(message_id)
        attachments = graph_client.get_attachments(message_id)
    except Exception as e:
        log.exception("falha ao buscar mensagem %s no Graph", message_id)
        alertar("Falha ao buscar e-mail no Graph",
                f"message_id: {message_id}\n\n{traceback.format_exc()}")
        return

    frm = ((message.get("from") or {}).get("emailAddress") or {}).get("address", "?")
    subject = message.get("subject") or "(sem assunto)"

    # Nao responder a nos mesmos (resposta automatica nossa tambem gera notificacao
    # de mensagem nova se cair na inbox monitorada).
    if frm and frm.lower() == settings.GRAPH_MAILBOX.lower():
        log.info("mensagem %s e da propria caixa; ignorando", message_id)
        return

    # 2. Extrair foto + motivo
    try:
        parsed = email_parser.parse(message, attachments)
    except email_parser.EmailSemImagem:
        log.info("mensagem %s sem imagem; pedindo foto ao remetente", message_id)
        try:
            graph_client.send_mail(frm, f"Re: {subject}", RESPOSTA_SEM_IMAGEM,
                                   reply_to_message_id=message_id)
        except Exception:
            log.exception("falha ao responder pedido de foto")
            alertar("Falha ao responder e-mail sem imagem",
                    f"de: {frm}\nassunto: {subject}\nmessage_id: {message_id}")
        return

    # 3. Inferencia no Vertex
    mime = {"jpg": "image/jpeg", ".jpg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp"}.get(parsed["image_ext"], "image/jpeg")
    try:
        julgamento, endpoint = inferencia.classify(
            parsed["image_bytes"], parsed["motivo"], mime_type=mime,
            endpoint=settings.TUNED_ENDPOINT)
    except Exception:
        log.exception("falha na inferencia para %s", message_id)
        alertar("Falha na chamada ao Vertex AI",
                f"de: {frm}\nassunto: {subject}\nmessage_id: {message_id}\n\n"
                f"{traceback.format_exc()}")
        # Cliente nao fica sem resposta por causa de falha nossa.
        try:
            graph_client.send_mail(frm, f"Re: {subject}", RESPOSTA_EM_ANALISE,
                                   reply_to_message_id=message_id)
        except Exception:
            log.exception("falha ao enviar 'em analise' apos erro de inferencia")
        return

    log.info("julgamento %s: %s (conf=%.2f)", message_id,
             julgamento.get("resultado"), julgamento.get("confianca") or 0)

    # 4. Politica de resposta
    acao, motivo_acao = decidir_acao(julgamento)
    try:
        if acao == "responder":
            graph_client.send_mail(
                frm, f"Re: {subject}",
                montar_resposta_cliente(julgamento, parsed["motivo"]),
                reply_to_message_id=message_id)
        else:
            graph_client.send_mail(frm, f"Re: {subject}", RESPOSTA_EM_ANALISE,
                                   reply_to_message_id=message_id)
            enfileirar_revisao(
                f"{julgamento.get('resultado')} - {motivo_acao}",
                f"de: {frm}\nassunto: {subject}\nmessage_id: {message_id}\n"
                f"motivo alegado: {parsed['motivo']}\n\n"
                f"julgamento do modelo:\n"
                f"  resultado: {julgamento.get('resultado')}\n"
                f"  confianca: {julgamento.get('confianca')}\n"
                f"  defeito identificado: {julgamento.get('defeito_identificado')}\n"
                f"  justificativa: {julgamento.get('justificativa')}\n\n"
                "ATENCAO ao ler os dois ultimos campos: o modelo tende a ECOAR o\n"
                "motivo alegado em vez de identificar o defeito de fato (os dois\n"
                "campos eram iguais em 166/166 exemplos de treino). Confie na\n"
                "imagem, nao na descricao. Ver build/RELATORIO_TESTE_PASTA.md.\n\n"
                "Nota: 'confianca' nao e incerteza calibrada - vale 1.0 em toda\n"
                "foto nitida e 0.35 em imagem degradada, sem meio-termo.\n\n"
                f"Responda ao cliente diretamente: {frm}")
    except Exception:
        log.exception("falha ao enviar resposta para %s", frm)
        alertar("Falha ao enviar resposta ao cliente",
                f"de: {frm}\nassunto: {subject}\nmessage_id: {message_id}\n"
                f"acao pretendida: {acao}\n\n{traceback.format_exc()}")
