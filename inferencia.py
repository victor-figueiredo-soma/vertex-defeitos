#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cliente de inferencia do modelo afinado. Autonomo: sem Flask, sem Firestore,
sem infra.

Recebe (bytes da imagem, motivo alegado) e devolve o JSON de julgamento no
formato do system_instruction.md. A requisicao e um espelho da linha de treino
(systemInstruction + imagem + "Motivo alegado pelo cliente: ..."), o que mantem a
consistencia treino/inferencia.

Use como base do app de producao ou direto no terminal:

    uv run python inferencia.py build/staging/034_mancha_aprovado.jpg "Mancha"
"""

import json
import os
import re
import sys

import config
import imagem

# Campos obrigatorios no JSON de saida (schema do system_instruction.md).
REQUIRED_KEYS = {
    "motivo_alegado", "existe_defeito", "defeito_identificado", "motivo_correto",
    "divergencia_motivo", "subtipo", "resultado", "confianca",
    "requer_revisao_manual", "justificativa",
}
RESULTADOS_VALIDOS = {"APROVADO", "REPROVADO", "INCONCLUSIVO"}

SYSTEM_INSTRUCTION_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "system_instruction.md")


def load_system_instruction():
    """Le o system_instruction.md (mesma fonte do treino) e corta o cabecalho.

    Fonte unica do prompt: o mesmo arquivo alimenta o JSONL de treino
    (gerar_dataset.py) e esta chamada. Divergir aqui quebra a paridade.
    """
    with open(SYSTEM_INSTRUCTION_FILE, encoding="utf-8") as f:
        content = f.read()
    parts = content.split("\n---\n", 1)
    return parts[1].strip() if len(parts) == 2 else content.strip()


def resolve_endpoint():
    """(endpoint, origem). Env TUNED_ENDPOINT vence; senao cai no modelo-base.

    O fallback para modelo-base serve para exercitar o pipeline sem o afinado -
    NAO e o validador. Quem chama deve avisar quando origem == 'fallback'.
    """
    if config.TUNED_ENDPOINT:
        return config.TUNED_ENDPOINT, "env"
    return config.BASE_MODEL, "fallback"


def e_modelo_afinado(endpoint):
    """True se e um modelo afinado, nao um modelo-base.

    Modelo-base vem como nome curto ('gemini-3.1-flash-lite'); afinado vem como
    resource name ('projects/<n>/locations/<r>/endpoints/<id>').
    """
    return bool(endpoint) and endpoint.startswith("projects/")


def location_do_endpoint(endpoint, default=None):
    """Extrai a location de um resource name; `default` se nao houver.

    Necessario porque o SFT de Gemini 3.x entrega o modelo afinado numa
    MULTI-REGION ('projects/N/locations/us/endpoints/ID'), nao na regiao do job.
    """
    partes = (endpoint or "").split("/")
    if "locations" in partes:
        i = partes.index("locations")
        if i + 1 < len(partes):
            return partes[i + 1]
    return default


def _inconclusivo(motivo, justificativa):
    """Resposta segura quando nao da para confiar na saida do modelo."""
    return {
        "motivo_alegado": motivo,
        "existe_defeito": None,
        "defeito_identificado": None,
        "motivo_correto": None,
        "divergencia_motivo": {"houve": 0, "motivo_correto_sugerido": None,
                               "observacao": None},
        "subtipo": "n/a",
        "resultado": "INCONCLUSIVO",
        "confianca": 0.0,
        "requer_revisao_manual": 1,
        "justificativa": justificativa,
    }


def _extrair_json(texto):
    """Extrai o objeto JSON da resposta, tolerando cercas ```json ... ```."""
    texto = (texto or "").strip()
    m = re.search(r"\{.*\}", texto, re.DOTALL)
    if not m:
        raise ValueError("nenhum objeto JSON encontrado na resposta")
    return json.loads(m.group(0))


def _validar(obj, motivo):
    """Valida o schema minimo; devolve o obj ou um INCONCLUSIVO seguro."""
    faltando = REQUIRED_KEYS - set(obj)
    if faltando:
        return _inconclusivo(
            motivo,
            f"Saida do modelo incompleta (campos ausentes: {sorted(faltando)}).")
    if obj.get("resultado") not in RESULTADOS_VALIDOS:
        return _inconclusivo(
            motivo,
            f"Resultado invalido retornado pelo modelo: {obj.get('resultado')!r}.")
    return obj


def _chamar_genai(endpoint, image_bytes, mime_type, motivo, gen_config):
    """Chamada via google-genai. Unico caminho que alcanca multi-region.

    O SDK legado (`vertexai`) valida a location contra uma lista fixa que NAO
    inclui 'us'/'eu', entao `vertexai.init(location='us')` levanta ValueError e o
    modelo afinado de um Gemini 3.x fica inalcancavel por ele.
    """
    import google.genai as genai
    from google.genai import types

    location = location_do_endpoint(endpoint, config.LOCATION)
    client = genai.Client(vertexai=True, project=config.PROJECT_ID,
                          location=location)

    cfg = dict(gen_config)
    cfg["system_instruction"] = load_system_instruction()
    thinking = cfg.pop("thinking_config", None)
    if thinking is not None:
        cfg["thinking_config"] = types.ThinkingConfig(**thinking)

    resp = client.models.generate_content(
        model=endpoint,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            types.Part.from_text(text=f"Motivo alegado pelo cliente: {motivo}"),
        ],
        config=types.GenerateContentConfig(**cfg),
    )
    return resp.text


def _chamar_vertexai(endpoint, image_bytes, mime_type, motivo, gen_config):
    """Chamada via SDK legado. So para modelo-BASE em regiao normal."""
    import vertexai
    from vertexai.generative_models import GenerativeModel, Part

    vertexai.init(project=config.PROJECT_ID, location=config.LOCATION)
    model = GenerativeModel(endpoint, system_instruction=load_system_instruction())
    resp = model.generate_content(
        [
            Part.from_data(data=image_bytes, mime_type=mime_type),
            Part.from_text(f"Motivo alegado pelo cliente: {motivo}"),
        ],
        generation_config=gen_config,
    )
    return resp.text


def classify(image_bytes, motivo, mime_type="image/jpeg", endpoint=None,
             media_resolution=None):
    """Classifica uma devolucao. Retorna (julgamento_dict, endpoint_usado).

    Nunca levanta por saida malformada do modelo: nesse caso devolve um
    INCONCLUSIVO com requer_revisao_manual=1. Erros de infra/permissao (403 etc.)
    propagam, para o chamador tratar/retentar.
    """
    config.ensure_credentials()

    if endpoint is None:
        endpoint, _origem = resolve_endpoint()

    afinado = e_modelo_afinado(endpoint)

    # Reduz a imagem como o dataset de treino foi reduzido. Anexos de e-mail vem
    # em resolucao cheia (ate 4080 px); servir nessa resolucao um modelo treinado
    # em 768 px faz treino e inferencia divergirem. De quebra corta custo/latencia.
    image_bytes = imagem.normalizar_bytes(image_bytes, mime_type)

    gen_config = {"temperature": 0}

    # KNOWN ISSUE do Vertex: controlled generation (response_mime_type /
    # response_schema) num modelo AFINADO degrada a qualidade - "can result in
    # decreased model quality due to data misalignment during tuning and inference
    # time [...] you don't need to apply controlled generation when making
    # inference requests on tuned models". O SFT ja ensina o formato pelos alvos.
    # No modelo-base, ao contrario, ajuda a garantir JSON. Dai o condicional -
    # e a saida malformada segue coberta por _inconclusivo().
    if not afinado:
        gen_config["response_mime_type"] = "application/json"

    # mediaResolution TEM que casar com o gravado no JSONL de treino: e ele, nao a
    # dimensao da imagem, que define o detalhe visual que o modelo recebe
    # (HIGH=1290 tok, MEDIUM=256, LOW=64 numa foto de 768px).
    mr = media_resolution or config.MEDIA_RESOLUTION
    if mr:
        gen_config["media_resolution"] = mr

    # Thinking no minimo: a doc de SFT diz que melhora performance e reduz custo
    # em tarefas afinadas. Ver config.THINKING_BUDGET sobre por que e
    # thinking_budget e nao thinking_level.
    if config.THINKING_BUDGET != "":
        gen_config["thinking_config"] = {
            "thinking_budget": int(config.THINKING_BUDGET)}

    chamar = _chamar_genai if afinado else _chamar_vertexai
    texto = chamar(endpoint, image_bytes, mime_type, motivo, gen_config)

    try:
        obj = _extrair_json(texto)
    except (ValueError, json.JSONDecodeError) as e:
        return _inconclusivo(
            motivo, f"Nao foi possivel interpretar a saida do modelo ({e})."), endpoint

    return _validar(obj, motivo), endpoint


def classify_file(image_path, motivo, endpoint=None, media_resolution=None):
    """Conveniencia: recebe caminho de arquivo em vez de bytes."""
    ext = os.path.splitext(image_path)[1].lower()
    mime = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jfif": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
    }.get(ext, "image/jpeg")
    with open(image_path, "rb") as f:
        data = f.read()
    return classify(data, motivo, mime_type=mime, endpoint=endpoint,
                    media_resolution=media_resolution)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if len(sys.argv) < 3:
        sys.exit(f"uso: python {os.path.basename(__file__)} <imagem> <motivo>")
    endpoint, origem = resolve_endpoint()
    print(f"modelo: {endpoint}  (origem: {origem})")
    if origem == "fallback":
        print("AVISO: modelo-BASE, nao o afinado. Defina TUNED_ENDPOINT no .env.")
    julgamento, _ = classify_file(sys.argv[1], sys.argv[2])
    print(json.dumps(julgamento, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
