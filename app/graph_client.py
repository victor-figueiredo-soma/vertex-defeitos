#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cliente do Microsoft Graph (Outlook): OAuth2 app-only, leitura de mensagens,
anexos, envio de e-mail e gestao da subscription do webhook.

Requer app registration no Azure com application permissions:
  Mail.Read       (ler a caixa monitorada)
  Mail.Send       (responder o cliente e alertar)
"""

import base64
import time

import requests

from app import settings

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_AUTHORITY = "https://login.microsoftonline.com/{tenant}"
_SCOPE = ["https://graph.microsoft.com/.default"]

# Cache simples do token: o MSAL ja cacheia internamente, mas manter a app
# construida evita refazer o discovery a cada chamada.
_msal_app = None
_token_cache = {"token": None, "expira_em": 0}


def _get_token():
    """Access token de aplicacao (client credentials), com cache por expiracao."""
    global _msal_app
    import msal

    agora = time.time()
    if _token_cache["token"] and agora < _token_cache["expira_em"] - 60:
        return _token_cache["token"]

    if not (settings.AZURE_TENANT_ID and settings.AZURE_CLIENT_ID
            and settings.AZURE_CLIENT_SECRET):
        raise RuntimeError(
            "Credenciais Azure ausentes (AZURE_TENANT_ID / AZURE_CLIENT_ID / "
            "AZURE_CLIENT_SECRET).")

    if _msal_app is None:
        _msal_app = msal.ConfidentialClientApplication(
            client_id=settings.AZURE_CLIENT_ID,
            authority=_AUTHORITY.format(tenant=settings.AZURE_TENANT_ID),
            client_credential=settings.AZURE_CLIENT_SECRET,
        )
    result = _msal_app.acquire_token_for_client(scopes=_SCOPE)
    if "access_token" not in result:
        raise RuntimeError(
            f"Falha ao autenticar no Azure: {result.get('error_description', result)}")
    _token_cache["token"] = result["access_token"]
    _token_cache["expira_em"] = agora + int(result.get("expires_in", 3600))
    return result["access_token"]


def _headers():
    return {"Authorization": f"Bearer {_get_token()}"}


def _mailbox_path():
    return f"/users/{settings.GRAPH_MAILBOX}"


# ----------------------------------------------------------------------------
# Leitura
# ----------------------------------------------------------------------------
def get_message(message_id):
    """JSON da mensagem: subject, body, from, hasAttachments..."""
    url = f"{GRAPH_BASE}{_mailbox_path()}/messages/{message_id}"
    r = requests.get(url, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def get_attachments(message_id):
    """Anexos da mensagem. Para fileAttachment, inclui os bytes decodificados.

    Cada item: { name, contentType, size, bytes(bytes|None) }.
    """
    url = f"{GRAPH_BASE}{_mailbox_path()}/messages/{message_id}/attachments"
    r = requests.get(url, headers=_headers(), timeout=60)
    r.raise_for_status()
    out = []
    for att in r.json().get("value", []):
        raw = att.get("contentBytes")
        out.append({
            "name": att.get("name"),
            "contentType": att.get("contentType"),
            "size": att.get("size"),
            "bytes": base64.b64decode(raw) if raw else None,
        })
    return out


# ----------------------------------------------------------------------------
# Envio
# ----------------------------------------------------------------------------
def send_mail(to_addr, subject, body_text, reply_to_message_id=None):
    """Envia e-mail como a caixa monitorada.

    Se reply_to_message_id for dado, usa o endpoint de reply do Graph (mantem a
    thread no cliente); senao cria mensagem nova via sendMail.
    """
    if reply_to_message_id:
        url = (f"{GRAPH_BASE}{_mailbox_path()}/messages/"
               f"{reply_to_message_id}/reply")
        payload = {"comment": body_text}
    else:
        url = f"{GRAPH_BASE}{_mailbox_path()}/sendMail"
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": body_text},
                "toRecipients": [{"emailAddress": {"address": to_addr}}],
            },
            "saveToSentItems": True,
        }
    r = requests.post(url, headers=_headers(), json=payload, timeout=30)
    r.raise_for_status()
    return True


# ----------------------------------------------------------------------------
# Subscription do webhook
# ----------------------------------------------------------------------------
def create_subscription(expiration_iso):
    """Cria a subscription de novas mensagens apontando para o nosso webhook."""
    if not settings.WEBHOOK_BASE_URL:
        raise RuntimeError("WEBHOOK_BASE_URL nao definido.")
    payload = {
        "changeType": "created",
        "notificationUrl": f"{settings.WEBHOOK_BASE_URL}/webhook",
        "resource": f"{_mailbox_path()}/mailFolders/inbox/messages",
        "expirationDateTime": expiration_iso,
        "clientState": settings.WEBHOOK_CLIENT_STATE,
    }
    r = requests.post(f"{GRAPH_BASE}/subscriptions", headers=_headers(),
                      json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def renew_subscription(subscription_id, expiration_iso):
    """Renova a expiracao (subscriptions de mail expiram em ~3 dias)."""
    r = requests.patch(f"{GRAPH_BASE}/subscriptions/{subscription_id}",
                       headers=_headers(),
                       json={"expirationDateTime": expiration_iso}, timeout=30)
    r.raise_for_status()
    return r.json()


def list_subscriptions():
    r = requests.get(f"{GRAPH_BASE}/subscriptions", headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json().get("value", [])
