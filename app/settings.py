#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Configuracao de runtime do app (webhook + inferencia + resposta).

Reusa config.py (raiz) para tudo que e GCP/modelo — fonte unica. Aqui ficam so as
variaveis proprias do app: Azure/Graph, webhook e alertas.

No Railway nao existe arquivo .env: as variaveis vem do painel do servico. O
carregador de config.py usa os.environ.setdefault, entao o ambiente real sempre
tem prioridade sobre um .env local de desenvolvimento.
"""

import json
import os
import tempfile

import config as _core

# ----------------------------------------------------------------------------
# GCP / modelo (herdado da raiz - fonte unica)
# ----------------------------------------------------------------------------
# Aceita os dois nomes: GCP_PROJECT (historico deste repo) e GCP_PROJECT_ID
# (spec do deploy). O segundo vence se definido.
PROJECT_ID = os.environ.get("GCP_PROJECT_ID") or _core.PROJECT_ID
LOCATION = _core.LOCATION
CONFIDENCE_THRESHOLD = _core.CONFIDENCE_THRESHOLD

# Endpoint do modelo afinado. Aceita VERTEX_ENDPOINT_ID (spec) como resource name
# completo OU como ID numerico (montado na multi-region 'us', onde o SFT de
# Gemini 3.x entrega o modelo).
_ep = os.environ.get("VERTEX_ENDPOINT_ID", "").strip() or _core.TUNED_ENDPOINT
if _ep and not _ep.startswith("projects/"):
    _ep = f"projects/{PROJECT_ID}/locations/us/endpoints/{_ep}"
TUNED_ENDPOINT = _ep

# ----------------------------------------------------------------------------
# Credencial GCP no Railway
# ----------------------------------------------------------------------------
# O Railway nao tem metadata server nem disco pre-populado; a chave da Service
# Account vai INTEIRA numa variavel de ambiente (GOOGLE_APPLICATION_CREDENTIALS_JSON).
# Materializamos num arquivo temporario e apontamos GOOGLE_APPLICATION_CREDENTIALS
# para ele, que e o que o ADC dos SDKs Google sabe consumir.


def _materializar_json(raw):
    """Grava o JSON da SA num arquivo temporario e aponta o ADC para ele.

    O ADC do Google so sabe consumir CAMINHO de arquivo; plataformas como o
    Railway nao tem disco pre-populado, entao a chave chega por variavel de
    ambiente e precisa virar arquivo aqui.
    """
    json.loads(raw)  # valida antes de gravar: erro deve estourar no BOOT
    fd, caminho = tempfile.mkstemp(prefix="gcp_sa_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(raw)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = caminho
    return caminho


def ensure_credentials():
    """Garante ADC utilizavel. Retorna o caminho da credencial ou None.

    GOOGLE_APPLICATION_CREDENTIALS aceita as DUAS formas, e a distincao e feita
    pelo conteudo:
      - caminho de arquivo -> usado direto (o jeito local)
      - o JSON da SA inteiro -> materializado num temporario (o jeito Railway,
        onde nao existe arquivo no disco)

    GOOGLE_APPLICATION_CREDENTIALS_JSON continua aceita como alternativa
    explicita, e vence quando o caminho aponta para arquivo inexistente. Isso
    cobre o erro facil de replicar o .env local no painel: la o caminho existe so
    na maquina do dev, e antes ele vencia silenciosamente sobre o JSON - o boot
    passava e a falha aparecia na primeira inferencia real.
    """
    bruto = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()

    # JSON colado na variavel de caminho: aceita em vez de tratar como path.
    if bruto.startswith("{"):
        return _materializar_json(bruto)

    if bruto and os.path.exists(bruto):
        return bruto

    raw = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()
    if raw:
        return _materializar_json(raw)

    if bruto:
        # Caminho inexistente e sem JSON: devolve-lo faria o SDK falhar depois.
        # Limpa para o _core tentar DEFAULT_SA_KEY / ADC do ambiente.
        os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    return _core.ensure_credentials()


# ----------------------------------------------------------------------------
# Azure / Microsoft Graph
# ----------------------------------------------------------------------------
AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "").strip()
AZURE_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "").strip()
AZURE_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "").strip()

# Caixa monitorada (app permissions permitem /users/{mailbox}).
GRAPH_MAILBOX = os.environ.get("GRAPH_MAILBOX", "").strip()

# TRAVA DE ESCOPO: so processa e-mail cujo assunto CONTENHA este termo
# (case-insensitive, ignorando acentos). Existe porque a caixa monitorada recebe
# e-mail alheio ao fluxo - marketing, notificacoes, conversas - e sem o filtro
# qualquer mensagem com imagem seria julgada e teria resposta automatica enviada
# ao remetente.
#
# Vazio DESLIGA o filtro e processa tudo. Nao e o default de proposito: e melhor
# recusar mensagem legitima por assunto errado do que responder "sua devolucao foi
# aprovada" para uma newsletter.
ASSUNTO_FILTRO = os.environ.get("ASSUNTO_FILTRO", "devolucao").strip()

# ----------------------------------------------------------------------------
# Webhook
# ----------------------------------------------------------------------------
# URL publica do servico, usada para registrar a subscription no Graph.
# SEM DEFAULT e sem derivar de RAILWAY_PUBLIC_DOMAIN: por decisao do projeto e
# tratada como valor sensivel e vem SEMPRE do ambiente. Exigida apenas pelos
# comandos de subscription (ver validar_boot(subscription=True)) - o webhook em si
# nao precisa dela para servir notificacao.
#
# Normaliza o esquema: o Railway mostra o dominio sem "https://" no painel, e
# colar so o dominio geraria notificationUrl sem esquema - que o Graph recusa com
# erro pouco claro. Aceita com ou sem.
WEBHOOK_BASE_URL = os.environ.get("WEBHOOK_BASE_URL", "").strip().rstrip("/")
if WEBHOOK_BASE_URL and not WEBHOOK_BASE_URL.startswith(("http://", "https://")):
    WEBHOOK_BASE_URL = f"https://{WEBHOOK_BASE_URL}"

# Token combinado na criacao da subscription. O Graph devolve esse valor em cada
# notificacao; requisicao cujo clientState nao bater e REJEITADA (403).
WEBHOOK_CLIENT_STATE = os.environ.get("WEBHOOK_CLIENT_STATE", "").strip()

# ----------------------------------------------------------------------------
# Alertas e servidor
# ----------------------------------------------------------------------------
# ERROS DE EXECUCAO: Vertex fora do ar, Graph com erro, falha ao responder o
# cliente. E o canal de "algo quebrou, alguem precisa olhar o sistema".
#
# SEM DEFAULT: por decisao do projeto os enderecos sao tratados como valor
# sensivel e vem SEMPRE do ambiente. validar_boot() recusa subir sem eles, o que
# e melhor que mandar alerta para a caixa errada por heranca silenciosa.
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "").strip()

# FILA DE REVISAO HUMANA: casos em que o modelo nao decide sozinho (INCONCLUSIVO,
# confianca abaixo do limiar, ou requer_revisao_manual). Nao e erro - e trabalho
# a fazer: o cliente recebeu "em analise" e alguem precisa julgar e responder.
# Proposito diferente do ALERT_EMAIL, dai o endereco separado. Tambem sem default,
# pelo mesmo motivo - e porque um caso de revisao perdido e a pior falha possivel:
# o cliente foi avisado de que esta "em analise" e ninguem sabe que existe.
REVIEW_EMAIL = os.environ.get("REVIEW_EMAIL", "").strip()

PORT = int(os.environ.get("PORT", "8080"))


def validar_boot(webhook=True, subscription=False):
    """Falha ALTO no startup se faltar configuracao critica.

    Melhor recusar o boot do que aceitar webhook e falhar silenciosamente na
    primeira notificacao real. Como nenhum destes tem default em codigo (sao
    tratados como valores sensiveis), esta validacao e a unica rede: sem ela, o
    app subiria e s'o descobririamos o problema no primeiro e-mail real.

    webhook=False       para CLIs que nao servem notificacao (processar_manual):
                        exigir WEBHOOK_CLIENT_STATE seria atrito sem beneficio.
    subscription=True   para os comandos que criam/renovam a subscription, os
                        unicos que precisam da WEBHOOK_BASE_URL.
    """
    obrigatorias = [
        ("AZURE_TENANT_ID", AZURE_TENANT_ID),
        ("AZURE_CLIENT_ID", AZURE_CLIENT_ID),
        ("AZURE_CLIENT_SECRET", AZURE_CLIENT_SECRET),
        ("GRAPH_MAILBOX", GRAPH_MAILBOX),
        ("TUNED_ENDPOINT/VERTEX_ENDPOINT_ID", TUNED_ENDPOINT),
        ("ALERT_EMAIL", ALERT_EMAIL),
        ("REVIEW_EMAIL", REVIEW_EMAIL),
    ]
    if webhook:
        obrigatorias.append(("WEBHOOK_CLIENT_STATE", WEBHOOK_CLIENT_STATE))
    if subscription:
        obrigatorias.append(("WEBHOOK_BASE_URL", WEBHOOK_BASE_URL))

    faltando = [nome for nome, valor in obrigatorias if not valor]
    if faltando:
        raise RuntimeError(
            "Variaveis de ambiente ausentes: " + ", ".join(faltando)
            + ". Defina no .env (local) ou no painel do Railway - nenhuma delas "
              "tem default em codigo, de proposito.")
