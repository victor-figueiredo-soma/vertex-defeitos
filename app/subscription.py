#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gestao da subscription do Graph.

Subscriptions de mail expiram em no maximo ~3 dias (4230 minutos), e uma vez
EXPIRADA nao pode ser renovada: o Graph a apaga e o PATCH devolve 404. Por isso
`garantir()` age antes de expirar e, se a renovacao falhar, CRIA outra.

Em producao nao e preciso rodar nada disto a mao: app/main.py chama garantir()
no boot e mantem um loop de fundo (ver INTERVALO_MANUTENCAO_S). A CLI existe para
inspecao e para o primeiro registro.

Uso:
  uv run python -m app.subscription listar    # mostra todas do tenant
  uv run python -m app.subscription criar     # primeiro registro
  uv run python -m app.subscription manter    # renova SO se perto de expirar
  uv run python -m app.subscription renovar   # forca renovacao agora
"""

import datetime
import logging
import sys

from app import graph_client, settings

log = logging.getLogger("subscription")

# Margem folgada: 2 dias (o maximo e ~3).
VALIDADE = datetime.timedelta(days=2)

# Renova quando faltar menos que isto para expirar. Precisa ser bem maior que o
# intervalo de checagem, senao uma checagem perdida (deploy, restart, queda) deixa
# a subscription morrer.
MARGEM_RENOVACAO = datetime.timedelta(hours=12)


def _agora():
    return datetime.datetime.now(datetime.timezone.utc)


def _expiracao_iso():
    return (_agora() + VALIDADE).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_expiracao(iso):
    """Graph devolve ISO-8601 com Z e as vezes fracao de segundo."""
    if not iso:
        return None
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def _nossas(subs):
    """Subscriptions que apontam para o NOSSO webhook.

    Filtra por notificationUrl porque o tenant Azure e compartilhado com outros
    projetos - listar sem filtrar traria subscriptions alheias, e renovar as dos
    outros seria interferencia.
    """
    if not settings.WEBHOOK_BASE_URL:
        return []
    alvo = f"{settings.WEBHOOK_BASE_URL}/webhook"
    return [s for s in subs if (s.get("notificationUrl") or "") == alvo]


def garantir(forcar=False):
    """Garante uma subscription valida. Idempotente e auto-curativa.

    - nenhuma nossa        -> cria
    - expirando em breve   -> renova
    - com folga            -> nao faz nada

    ATENCAO: subscription EXPIRADA nao pode ser renovada - o Graph a apaga e o
    PATCH devolve 404. Por isso a checagem e proativa (MARGEM_RENOVACAO) e o
    fallback de um renew que falha e CRIAR outra, nao insistir no PATCH.

    Retorna (acao, detalhe) com acao em {'criada','renovada','ok','ignorada'}.
    Nunca levanta: e chamada do startup e de loop de fundo, onde falhar derrubaria
    o servico por algo que a proxima tentativa resolve.
    """
    if not settings.WEBHOOK_BASE_URL:
        log.warning("WEBHOOK_BASE_URL nao definida; nao gerencio subscription")
        return "ignorada", "WEBHOOK_BASE_URL ausente"
    if not settings.WEBHOOK_CLIENT_STATE:
        log.warning("WEBHOOK_CLIENT_STATE nao definido; nao gerencio subscription")
        return "ignorada", "WEBHOOK_CLIENT_STATE ausente"

    try:
        nossas = _nossas(graph_client.list_subscriptions())
    except Exception:
        log.exception("falha ao listar subscriptions")
        return "ignorada", "falha ao listar"

    if not nossas:
        try:
            sub = graph_client.create_subscription(_expiracao_iso())
            log.info("subscription criada: %s (expira %s)",
                     sub.get("id"), sub.get("expirationDateTime"))
            return "criada", sub.get("id")
        except Exception:
            log.exception("falha ao criar subscription")
            return "ignorada", "falha ao criar"

    for s in nossas:
        expira = _parse_expiracao(s.get("expirationDateTime"))
        resta = (expira - _agora()) if expira else None

        if not forcar and resta and resta > MARGEM_RENOVACAO:
            log.info("subscription %s ok, expira em %s (faltam %.1fh)",
                     s.get("id"), s.get("expirationDateTime"),
                     resta.total_seconds() / 3600)
            return "ok", s.get("id")

        try:
            novo = graph_client.renew_subscription(s["id"], _expiracao_iso())
            log.info("subscription %s renovada ate %s",
                     s["id"], novo.get("expirationDateTime"))
            return "renovada", s["id"]
        except Exception:
            # Provavel 404: expirou e o Graph ja apagou. Criar outra e a saida.
            log.warning("renovacao de %s falhou; tentando criar nova", s["id"],
                        exc_info=True)
            try:
                sub = graph_client.create_subscription(_expiracao_iso())
                log.info("subscription recriada: %s", sub.get("id"))
                return "criada", sub.get("id")
            except Exception:
                log.exception("falha ao recriar subscription")
                return "ignorada", "falha ao renovar e recriar"

    return "ok", None


def criar():
    sub = graph_client.create_subscription(_expiracao_iso())
    print(f"subscription criada: {sub['id']}")
    print(f"  resource : {sub['resource']}")
    print(f"  expira em: {sub['expirationDateTime']}")
    print(f"  notifica : {sub['notificationUrl']}")


def listar():
    subs = graph_client.list_subscriptions()
    if not subs:
        print("nenhuma subscription ativa.")
        return
    for s in subs:
        print(f"{s['id']}")
        print(f"  resource : {s.get('resource')}")
        print(f"  expira em: {s.get('expirationDateTime')}")
        print(f"  notifica : {s.get('notificationUrl')}")


def renovar():
    """Forca a renovacao agora, sem esperar a MARGEM_RENOVACAO."""
    acao, detalhe = garantir(forcar=True)
    print(f"{acao}: {detalhe}")


def manter():
    """Renova SO se estiver perto de expirar. E o que o app roda em background.

    Util para agendar num cron externo, se um dia o servico nao ficar de pe o
    tempo todo.
    """
    acao, detalhe = garantir()
    print(f"{acao}: {detalhe}")


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    acoes = {"criar": criar, "listar": listar, "renovar": renovar,
             "manter": manter}
    acao = sys.argv[1] if len(sys.argv) > 1 else ""
    if acao not in acoes:
        sys.exit(f"uso: python -m app.subscription [{'|'.join(acoes)}]")
    # 'criar' GRAVA o clientState na subscription: sem ele, o webhook rejeitaria
    # toda notificacao depois (403). 'renovar' e 'manter' podem precisar CRIAR
    # (se a antiga expirou e o Graph a apagou), entao tambem exigem o clientState.
    # 'listar' nao precisa de nada. Todas menos 'listar' precisam da BASE_URL.
    settings.validar_boot(webhook=(acao != "listar"),
                          subscription=(acao != "listar"))
    acoes[acao]()


if __name__ == "__main__":
    main()
