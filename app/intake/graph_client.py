#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cliente do Microsoft Graph (Outlook) — autenticacao Azure + leitura de mensagens.

Autentica por *client credentials* (app registration Azure): tenant_id,
client_id e client_secret vem de settings (Secret Manager em producao). Requer a
permissao de aplicacao Mail.Read no Azure AD.

Expondo o minimo necessario ao fluxo de ingestao:
  - get_message(message_id): metadados + corpo da mensagem
  - get_attachments(message_id): anexos (com bytes, para imagens)
  - create_subscription / renew_subscription: gerenciam o webhook do Graph
"""

import base64

import requests

from app import settings

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_AUTHORITY = "https://login.microsoftonline.com/{tenant}"
_SCOPE = ["https://graph.microsoft.com/.default"]


def _get_token():
    """Obtem um access token de aplicacao via MSAL (client credentials)."""
    import msal

    if not (settings.AZURE_TENANT_ID and settings.AZURE_CLIENT_ID
            and settings.AZURE_CLIENT_SECRET):
        raise RuntimeError(
            "Credenciais Azure ausentes. Defina AZURE_TENANT_ID, AZURE_CLIENT_ID "
            "e AZURE_CLIENT_SECRET (Secret Manager em producao).")

    app = msal.ConfidentialClientApplication(
        client_id=settings.AZURE_CLIENT_ID,
        authority=_AUTHORITY.format(tenant=settings.AZURE_TENANT_ID),
        client_credential=settings.AZURE_CLIENT_SECRET,
    )
    result = app.acquire_token_for_client(scopes=_SCOPE)
    if "access_token" not in result:
        raise RuntimeError(
            f"Falha ao autenticar no Azure: {result.get('error_description', result)}")
    return result["access_token"]


def _headers():
    return {"Authorization": f"Bearer {_get_token()}"}


def _mailbox_path():
    # Caixa compartilhada/monitorada; app permissions permitem acessar /users/{mailbox}
    return f"/users/{settings.GRAPH_MAILBOX}"


def get_message(message_id):
    """Retorna o JSON da mensagem (inclui subject, body, from, hasAttachments)."""
    url = f"{GRAPH_BASE}{_mailbox_path()}/messages/{message_id}"
    r = requests.get(url, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def get_attachments(message_id):
    """Retorna a lista de anexos. Para fileAttachment, inclui os bytes decodificados.

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
# Subscriptions (webhook) — o Cloud Run recebe notificacao de nova mensagem.
# ----------------------------------------------------------------------------
def create_subscription(notification_url, expiration_iso):
    """Cria uma subscription para novas mensagens na caixa monitorada."""
    payload = {
        "changeType": "created",
        "notificationUrl": notification_url,
        "resource": f"{_mailbox_path()}/mailFolders/inbox/messages",
        "expirationDateTime": expiration_iso,
        "clientState": settings.GRAPH_CLIENT_STATE,
    }
    r = requests.post(f"{GRAPH_BASE}/subscriptions", headers=_headers(),
                      json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def renew_subscription(subscription_id, expiration_iso):
    """Renova a expiracao de uma subscription (chamado periodicamente por Scheduler)."""
    r = requests.patch(f"{GRAPH_BASE}/subscriptions/{subscription_id}",
                       headers=_headers(),
                       json={"expirationDateTime": expiration_iso}, timeout=30)
    r.raise_for_status()
    return r.json()
