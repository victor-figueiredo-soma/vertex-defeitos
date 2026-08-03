#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API do webhook — FastAPI, hospedada no Railway.

Fluxo:
  1. Handshake: o Graph chama POST /webhook?validationToken=... na criacao da
     subscription; devolvemos o token em texto puro em ate 10s.
  2. Notificacao: POST /webhook com JSON; validamos o clientState de CADA item,
     devolvemos 202 imediatamente e processamos em background task.

Rodar local:
  uv run uvicorn app.main:app --port 8080
"""

import asyncio
import logging

from fastapi import BackgroundTasks, FastAPI, Request, Response

from app import processor, settings, subscription

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("webhook")

app = FastAPI(title="vertex-defeitos webhook", docs_url=None, redoc_url=None)


# Intervalo do loop de manutencao da subscription. Bem menor que a
# MARGEM_RENOVACAO (12h) para que uma checagem perdida - deploy, restart, queda
# momentanea do Graph - ainda deixe varias tentativas antes de expirar.
INTERVALO_MANUTENCAO_S = 3 * 3600


async def _loop_subscription():
    """Mantem a subscription viva enquanto o servico estiver no ar.

    Subscriptions de mail do Graph expiram em ~3 dias e, uma vez EXPIRADAS, nao
    podem ser renovadas (o Graph as apaga). Sem isto, o fluxo automatico morre em
    silencio a cada 3 dias: nenhum erro, nenhum e-mail, so nada acontecendo.

    subscription.garantir() e idempotente e nunca levanta, entao o loop nao morre
    por uma falha transitoria de rede.
    """
    while True:
        try:
            await asyncio.to_thread(subscription.garantir)
        except Exception:
            log.exception("erro inesperado no loop de subscription")
        await asyncio.sleep(INTERVALO_MANUTENCAO_S)


@app.on_event("startup")
async def _startup():
    # Falha alto no boot se faltar config critica - melhor que aceitar webhook
    # e quebrar na primeira notificacao real.
    settings.validar_boot()
    settings.ensure_credentials()
    log.info("boot ok | mailbox=%s | endpoint=%s | alertas=%s",
             settings.GRAPH_MAILBOX,
             settings.TUNED_ENDPOINT[-30:],
             settings.ALERT_EMAIL or "(DESLIGADO)")

    # Cria/renova a subscription no boot e mantem viva em background. Assim o
    # deploy e autossuficiente: nao depende de rodar a CLI a mao nem de cron
    # externo. Nao bloqueia o boot - se o Graph estiver fora, o loop tenta de novo.
    asyncio.create_task(_loop_subscription())


@app.get("/health")
def health():
    """Probe do Railway."""
    return {"ok": True}


@app.get("/webhook")
@app.post("/webhook")
async def webhook(request: Request, background: BackgroundTasks):
    # ------------------------------------------------------------------
    # 1) Handshake de validacao da subscription: o Graph manda
    #    ?validationToken=... e espera o token de volta em texto puro.
    #    Chega por POST na criacao, mas aceitar GET tambem nao custa.
    # ------------------------------------------------------------------
    token = request.query_params.get("validationToken")
    if token:
        log.info("handshake de validacao do Graph")
        return Response(content=token, media_type="text/plain", status_code=200)

    if request.method == "GET":
        return Response(status_code=405)

    # ------------------------------------------------------------------
    # 2) Notificacao de mensagem nova.
    # ------------------------------------------------------------------
    try:
        payload = await request.json()
    except Exception:
        log.warning("payload nao-JSON recebido no webhook")
        return Response(status_code=400)

    autenticadas = 0
    for notif in payload.get("value", []):
        # Validacao ESTRITA por item: o clientState foi combinado na criacao da
        # subscription; item sem o valor certo e descartado - pode ser sonda ou
        # subscription orfa apontando para ca.
        if notif.get("clientState") != settings.WEBHOOK_CLIENT_STATE:
            log.warning("clientState invalido de %s; item descartado",
                        request.client.host if request.client else "?")
            continue
        autenticadas += 1

        message_id = (notif.get("resourceData") or {}).get("id")
        if not message_id:
            # Autenticada porem malformada: loga e segue - nao e intrusao.
            log.warning("notificacao sem resourceData.id; ignorada")
            continue

        background.add_task(processor.processar_mensagem, message_id)

    if autenticadas == 0 and payload.get("value"):
        # NENHUM item passou no clientState -> 403 sinaliza rejeicao.
        return Response(status_code=403)

    # 202 rapido: o Graph re-tenta (e eventualmente desativa a subscription) se
    # nao respondermos em ~10s. O processamento real esta nas background tasks.
    return Response(status_code=202)
