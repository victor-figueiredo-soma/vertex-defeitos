#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Camada de inferencia — o "cerebro" do produto.

Recebe (bytes da imagem, motivo alegado) e devolve o JSON de julgamento no
formato definido pelo system_instruction. E DESACOPLADO de Outlook, Cloud Run e
Firestore de proposito: pode ser chamado direto (ver inferir_local.py) para
testar o modelo assim que a role aiplatform.user estiver concedida, sem infra.

A requisicao e um espelho da linha de treino (systemInstruction + imagem +
"Motivo alegado pelo cliente: ..."), garantindo consistencia treino/producao.
"""

import os
import re
import json

from app import settings

# Campos obrigatorios no JSON de saida (schema do system_instruction).
REQUIRED_KEYS = {
    "motivo_alegado", "existe_defeito", "defeito_identificado", "motivo_correto",
    "divergencia_motivo", "subtipo", "resultado", "confianca",
    "requer_revisao_manual", "justificativa",
}
RESULTADOS_VALIDOS = {"APROVADO", "REPROVADO", "INCONCLUSIVO"}


def load_system_instruction():
    """Le o system_instruction.md (mesma fonte do treino) e remove o cabecalho.

    Mesma logica de parse de gerar_dataset.load_system_instruction(), mas
    apontando para o arquivo via settings (evita o caminho O:\\ do script de
    treino). Mantem uma unica fonte do prompt: system_instruction.md.
    """
    with open(settings.SYSTEM_INSTRUCTION_FILE, encoding="utf-8") as f:
        content = f.read()
    parts = content.split("\n---\n", 1)
    return parts[1].strip() if len(parts) == 2 else content.strip()


def resolve_endpoint():
    """Descobre qual modelo/endpoint usar, em ordem de prioridade.

    1) settings.TUNED_ENDPOINT (env) — pratico para teste local.
    2) Firestore config/active_model.endpoint — verdade em producao.
    3) settings.FALLBACK_BASE_MODEL — so para exercitar o pipeline (NAO validador).
    """
    if settings.TUNED_ENDPOINT:
        return settings.TUNED_ENDPOINT, "env"
    try:
        from app.persistence import repository
        endpoint = repository.get_active_endpoint()
        if endpoint:
            return endpoint, "firestore"
    except Exception:
        pass
    return settings.FALLBACK_BASE_MODEL, "fallback"


def _inconclusivo(motivo, justificativa):
    """Resposta segura quando nao da para confiar na saida do modelo."""
    return {
        "motivo_alegado": motivo,
        "existe_defeito": None,
        "defeito_identificado": None,
        "motivo_correto": None,
        "divergencia_motivo": {"houve": 0, "motivo_correto_sugerido": None, "observacao": None},
        "subtipo": "n/a",
        "resultado": "INCONCLUSIVO",
        "confianca": 0.0,
        "requer_revisao_manual": 1,
        "justificativa": justificativa,
    }


def _extrair_json(texto):
    """Extrai o objeto JSON da resposta do modelo, tolerando ```json ... ```."""
    texto = texto.strip()
    m = re.search(r"\{.*\}", texto, re.DOTALL)
    if not m:
        raise ValueError("nenhum objeto JSON encontrado na resposta")
    return json.loads(m.group(0))


def _validar(obj, motivo):
    """Valida o schema minimo; devolve o obj ou um INCONCLUSIVO seguro."""
    faltando = REQUIRED_KEYS - set(obj)
    if faltando:
        return _inconclusivo(
            motivo, f"Saida do modelo incompleta (campos ausentes: {sorted(faltando)}).")
    if obj.get("resultado") not in RESULTADOS_VALIDOS:
        return _inconclusivo(
            motivo, f"Resultado invalido retornado pelo modelo: {obj.get('resultado')!r}.")
    return obj


def classify(image_bytes, motivo, mime_type="image/jpeg", endpoint=None):
    """Classifica uma devolucao. Retorna (julgamento_dict, model_version_str).

    Nunca levanta por conta de saida malformada do modelo: nesse caso devolve um
    INCONCLUSIVO (requer_revisao_manual=1). Erros de infra/permissao (403 etc.)
    sim propagam, para o chamador tratar/retentar.
    """
    import vertexai
    from vertexai.generative_models import GenerativeModel, Part

    settings.ensure_credentials()
    vertexai.init(project=settings.PROJECT_ID, location=settings.LOCATION)

    if endpoint is None:
        endpoint, _origem = resolve_endpoint()

    model = GenerativeModel(endpoint, system_instruction=load_system_instruction())
    resp = model.generate_content(
        [
            Part.from_data(data=image_bytes, mime_type=mime_type),
            Part.from_text(f"Motivo alegado pelo cliente: {motivo}"),
        ],
        generation_config={"temperature": 0, "response_mime_type": "application/json"},
    )

    try:
        obj = _extrair_json(resp.text)
    except (ValueError, json.JSONDecodeError) as e:
        return _inconclusivo(motivo, f"Nao foi possivel interpretar a saida do modelo ({e})."), endpoint

    return _validar(obj, motivo), endpoint


def classify_file(image_path, motivo, endpoint=None):
    """Conveniencia para teste local: recebe caminho de arquivo em vez de bytes."""
    ext = os.path.splitext(image_path)[1].lower()
    mime = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jfif": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
    }.get(ext, "image/jpeg")
    with open(image_path, "rb") as f:
        data = f.read()
    return classify(data, motivo, mime_type=mime, endpoint=endpoint)
