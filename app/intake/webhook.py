#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Servico HTTP (Cloud Run) — webhook do Microsoft Graph + pipeline de processamento.

Duas responsabilidades:
  1. Validar a subscription do Graph (echo do validationToken).
  2. Receber notificacoes de nova mensagem e orquestrar o pipeline:
        Graph -> parser -> GCS -> classifier -> decision_router -> Firestore.

A orquestracao fica em processar_devolucao(), que NAO depende do Graph — recebe
o resultado do parser. Isso permite testar o fluxo com um payload mockado, sem
Outlook.

Entrypoint local:  uv run functions-framework --target=... (ou flask run)
Producao:          gunicorn app.intake.webhook:app
"""

import uuid

from flask import Flask, request, Response

from app import settings
from app.intake import graph_client, email_parser
from app.inference import classifier
from app.routing import decision_router
from app.persistence import repository

app = Flask(__name__)

_EXT_MIME = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def processar_devolucao(parsed):
    """Roda o pipeline para uma devolucao ja parseada. Retorna o registro salvo.

    parsed: saida de email_parser.parse (motivo, image_bytes, image_ext, ...).
    """
    from google.cloud import firestore

    devolucao_id = parsed.get("message_id") or uuid.uuid4().hex
    mime = _EXT_MIME.get(parsed["image_ext"], "image/jpeg")

    image_uri = repository.upload_image(
        devolucao_id, parsed["image_bytes"], parsed["image_ext"])

    julgamento, model_version = classifier.classify(
        parsed["image_bytes"], parsed["motivo"], mime_type=mime)

    routing = decision_router.route(julgamento)

    registro = {
        "source": "outlook",
        "message_id": parsed.get("message_id"),
        "from_addr": parsed.get("from_addr"),
        "received_at": firestore.SERVER_TIMESTAMP,
        "motivo_alegado": parsed["motivo"],
        "image_uri": image_uri,
        "model_output": julgamento,
        "model_version": model_version,
        "status": routing["status"],
        "decision": routing["decision"],
        "motivos_revisao": routing["motivos_revisao"],
        "confirmed_label": None,
        "confirmed_by": None,
        "confirmed_at": None,
        "used_in_training": False,
    }
    repository.salvar_devolucao(devolucao_id, registro)
    return {"id": devolucao_id, **routing}


def processar_mensagem(message_id):
    """Busca a mensagem no Graph, parseia e roda o pipeline."""
    message = graph_client.get_message(message_id)
    attachments = (graph_client.get_attachments(message_id)
                   if message.get("hasAttachments") else [])
    parsed = email_parser.parse(message, attachments)
    return processar_devolucao(parsed)


@app.route("/healthz", methods=["GET"])
def healthz():
    return Response("ok", mimetype="text/plain")


@app.route("/graph/notifications", methods=["POST"])
def notifications():
    # 1) Handshake de criacao da subscription: eco do validationToken.
    token = request.args.get("validationToken")
    if token:
        return Response(token, mimetype="text/plain", status=200)

    # 2) Notificacoes de mudanca (novas mensagens).
    body = request.get_json(silent=True) or {}
    for note in body.get("value", []):
        # Valida a origem pelo clientState combinado na criacao da subscription.
        if settings.GRAPH_CLIENT_STATE and \
                note.get("clientState") != settings.GRAPH_CLIENT_STATE:
            app.logger.warning("clientState invalido; notificacao ignorada")
            continue
        message_id = (note.get("resourceData") or {}).get("id")
        if not message_id:
            continue
        try:
            processar_mensagem(message_id)
        except email_parser.EmailSemImagem:
            app.logger.info("mensagem %s sem imagem; ignorada", message_id)
        except Exception:
            # Loga e segue: um erro numa mensagem nao pode travar as demais.
            app.logger.exception("falha ao processar mensagem %s", message_id)

    # Graph espera 202 rapido; o processamento pesado ja ocorreu de forma simples
    # aqui — para volumes maiores, publicar em Pub/Sub e processar async.
    return Response(status=202)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
