# -*- coding: utf-8 -*-
"""Testes do parser de e-mail (funcoes puras, sem rede)."""

import pytest

from app import email_parser


def test_strip_html_remove_tags_e_normaliza():
    html = "<p>Peca com <b>mancha</b>&nbsp;grande</p><script>x=1</script>"
    assert email_parser.strip_html(html) == "Peca com mancha grande"


def test_extrair_motivo_prioriza_assunto():
    msg = {"subject": "Devolucao - Mancha", "body": {"contentType": "text", "content": "oi"}}
    assert email_parser.extrair_motivo(msg) == "Devolucao - Mancha"


def test_extrair_motivo_cai_para_corpo_quando_sem_assunto():
    msg = {"subject": "", "body": {"contentType": "html", "content": "<p>Veio furada</p>"}}
    assert email_parser.extrair_motivo(msg) == "Veio furada"


def test_escolher_imagem_por_content_type():
    atts = [
        {"name": "doc.pdf", "contentType": "application/pdf", "bytes": b"x"},
        {"name": "foto.jpg", "contentType": "image/jpeg", "bytes": b"IMG"},
    ]
    data, ext = email_parser.escolher_imagem(atts)
    assert data == b"IMG" and ext == ".jpg"


def test_escolher_imagem_fallback_por_extensao():
    atts = [{"name": "foto.PNG", "contentType": "application/octet-stream", "bytes": b"P"}]
    data, ext = email_parser.escolher_imagem(atts)
    assert data == b"P" and ext == ".png"


def test_parse_completo():
    msg = {
        "id": "AAA123",
        "subject": "Furo na peca",
        "from": {"emailAddress": {"address": "cliente@x.com"}},
        "body": {"contentType": "text", "content": "segue foto"},
    }
    atts = [{"name": "p.jpg", "contentType": "image/jpeg", "bytes": b"IMG"}]
    out = email_parser.parse(msg, atts)
    assert out["message_id"] == "AAA123"
    assert out["motivo"] == "Furo na peca"
    assert out["image_bytes"] == b"IMG"
    assert out["image_ext"] == ".jpg"
    assert out["from_addr"] == "cliente@x.com"


def test_parse_sem_imagem_levanta():
    msg = {"id": "B", "subject": "s", "body": {"contentType": "text", "content": ""}}
    with pytest.raises(email_parser.EmailSemImagem):
        email_parser.parse(msg, [])
