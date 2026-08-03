#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Normalizacao de imagem compartilhada entre TREINO e PRODUCAO.

Existe um motivo unico para este modulo: o dataset de SFT e gerado com as fotos
reduzidas a MAX_DIM px (otimizar_dataset.py), e a inferencia de producao recebe
os anexos de e-mail em resolucao cheia (ate 4080 px). Servir numa resolucao
diferente da que foi treinada degrada em silencio - o modelo passa a ver um
numero de ladrilhos que nunca viu no treino.

Ladrilhamento do Gemini 2.x: <=384 px nos dois lados = 1 ladrilho; senao
ceil(w/768) * ceil(h/768) ladrilhos, a 258 tokens cada. 768 px e o maior valor
que ainda cabe em 1 ladrilho - por isso o default, e nao um valor intermediario
(1024 e 896 ja custam 2 ladrilhos).
"""

import io
import math
import os

from PIL import Image, ImageOps

MAX_DIM = int(os.environ.get("IMAGEM_MAX_DIM", "768"))
TOKENS_POR_LADRILHO = 258
JPEG_QUALITY = 88


def ladrilhos(w, h):
    """Ladrilhos que o Gemini 2.x usa para uma imagem w x h."""
    if w <= 384 and h <= 384:
        return 1
    return math.ceil(w / 768) * math.ceil(h / 768)


def tokens_estimados(w, h):
    return ladrilhos(w, h) * TOKENS_POR_LADRILHO


def normalizar(im, max_dim=MAX_DIM):
    """Aplica a orientacao do EXIF e reduz para <= max_dim no maior lado.

    A orientacao e aplicada aqui e o EXIF descartado na gravacao: o resultado
    visual fica igual ao original sem depender de o consumidor honrar a tag.
    Devolve (imagem, mudou).
    """
    im = ImageOps.exif_transpose(im)
    if max(im.size) <= max_dim:
        return im, False
    im.thumbnail((max_dim, max_dim), Image.LANCZOS)
    return im, True


def normalizar_arquivo(src, dst, mime, max_dim=MAX_DIM):
    """Grava src normalizado em dst. Devolve (ow, oh, nw, nh, bytes, reencodou).

    Preserva o formato para o mimeType declarado continuar batendo com os bytes
    - foi essa divergencia que gerou o bug do 207_mancha_aprovado (renomeado de
    .png para .jpeg sem converter o conteudo).
    """
    import shutil

    with Image.open(src) as original:
        im = ImageOps.exif_transpose(original)
        ow, oh = im.size
        if max(ow, oh) <= max_dim:
            # Ja esta pequena: copia os bytes em vez de reencodar, evitando uma
            # geracao de perda desnecessaria.
            shutil.copy2(src, dst)
            return ow, oh, ow, oh, os.path.getsize(src), False
        im.thumbnail((max_dim, max_dim), Image.LANCZOS)
        nw, nh = im.size
        _salvar(im, dst, mime)
    return ow, oh, nw, nh, os.path.getsize(dst), True


def normalizar_bytes(image_bytes, mime_type="image/jpeg", max_dim=MAX_DIM):
    """Versao para producao: bytes -> bytes normalizados.

    Nunca levanta: se a imagem nao for decodificavel pelo Pillow, devolve os
    bytes originais e deixa o proprio modelo lidar com o anexo - perder a
    reducao e melhor que derrubar a ingestao de um e-mail.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as original:
            im, mudou = normalizar(original, max_dim)
            if not mudou:
                return image_bytes
            buf = io.BytesIO()
            _salvar(im, buf, mime_type)
            return buf.getvalue()
    except OSError:
        return image_bytes


def _salvar(im, destino, mime):
    if mime == "image/png":
        im.save(destino, format="PNG", optimize=True)
        return
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    im.save(destino, format="JPEG", quality=JPEG_QUALITY, optimize=True)
