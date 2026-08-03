#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Parser de e-mail — extrai a IMAGEM e o MOTIVO de uma mensagem.

Funcoes PURAS (sem rede): recebem o JSON da mensagem do Graph + a lista de anexos
(no formato de graph_client.get_attachments) e devolvem os dados prontos para a
inferencia. Isso mantem a logica testavel e desacoplada da fonte (poderia vir de
outro provedor de e-mail sem mudar este modulo).
"""

import re
import html as _html

_IMG_EXT = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/jfif": ".jpg",
}


class EmailSemImagem(Exception):
    """Levantada quando a mensagem nao contem nenhuma imagem utilizavel."""


def strip_html(texto):
    """Remove tags HTML e normaliza espacos (para extrair o corpo em texto puro)."""
    if not texto:
        return ""
    texto = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", texto)
    texto = re.sub(r"(?s)<[^>]+>", " ", texto)
    texto = _html.unescape(texto)
    return re.sub(r"\s+", " ", texto).strip()


def extrair_corpo(message):
    """Texto puro do corpo da mensagem (trata body.contentType html/text)."""
    body = message.get("body") or {}
    content = body.get("content", "")
    if (body.get("contentType") or "").lower() == "html":
        return strip_html(content)
    return re.sub(r"\s+", " ", content or "").strip()


def extrair_motivo(message):
    """Deriva o motivo alegado: prioriza o assunto; cai para o corpo.

    O texto e passado ao modelo como "Motivo alegado pelo cliente". Nao tenta
    classificar aqui — apenas entrega o que o cliente escreveu.
    """
    subject = (message.get("subject") or "").strip()
    if subject:
        return subject
    corpo = extrair_corpo(message)
    return corpo[:280] if corpo else ""


def escolher_imagem(attachments):
    """Escolhe o primeiro anexo de imagem com bytes. Retorna (bytes, ext) ou None."""
    for att in attachments or []:
        ctype = (att.get("contentType") or "").lower().split(";")[0]
        data = att.get("bytes")
        if data and ctype in _IMG_EXT:
            return data, _IMG_EXT[ctype]
        # fallback por extensao do nome, se o contentType vier generico
        name = (att.get("name") or "").lower()
        if data:
            for ext in (".jpg", ".jpeg", ".png", ".webp", ".jfif"):
                if name.endswith(ext):
                    return data, ".jpg" if ext in (".jpeg", ".jfif") else ext
    return None


def parse(message, attachments):
    """Extrai tudo que a inferencia precisa de uma mensagem.

    Retorna dict:
      { message_id, motivo, image_bytes, image_ext, subject, body_text, from_addr }
    Levanta EmailSemImagem se nao houver imagem utilizavel.
    """
    escolha = escolher_imagem(attachments)
    if not escolha:
        raise EmailSemImagem("nenhuma imagem encontrada nos anexos")
    image_bytes, image_ext = escolha

    frm = ((message.get("from") or {}).get("emailAddress") or {}).get("address")
    return {
        "message_id": message.get("id"),
        "motivo": extrair_motivo(message),
        "image_bytes": image_bytes,
        "image_ext": image_ext,
        "subject": (message.get("subject") or "").strip(),
        "body_text": extrair_corpo(message),
        "from_addr": frm,
    }
