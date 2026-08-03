#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gestao da subscription do Graph — CLI.

Subscriptions de mail expiram em no maximo ~3 dias (4230 minutos). Criar uma vez
e esquecer NAO funciona: renove antes de expirar (cron do Railway, ou rode
`renovar` manualmente).

Uso:
  uv run python -m app.subscription criar
  uv run python -m app.subscription listar
  uv run python -m app.subscription renovar          # renova todas as nossas
"""

import datetime
import sys

from app import graph_client, settings

# Margem folgada: 2 dias (o maximo e ~3).
VALIDADE = datetime.timedelta(days=2)


def _expiracao_iso():
    fim = datetime.datetime.now(datetime.timezone.utc) + VALIDADE
    return fim.strftime("%Y-%m-%dT%H:%M:%SZ")


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
    subs = graph_client.list_subscriptions()
    nossas = [s for s in subs
              if settings.WEBHOOK_BASE_URL
              and (s.get("notificationUrl") or "").startswith(
                  settings.WEBHOOK_BASE_URL)]
    if not nossas:
        print("nenhuma subscription nossa para renovar "
              "(confira WEBHOOK_BASE_URL). Rode 'criar' primeiro.")
        return
    for s in nossas:
        graph_client.renew_subscription(s["id"], _expiracao_iso())
        print(f"renovada {s['id']} ate {_expiracao_iso()}")


def main():
    acoes = {"criar": criar, "listar": listar, "renovar": renovar}
    acao = sys.argv[1] if len(sys.argv) > 1 else ""
    if acao not in acoes:
        sys.exit(f"uso: python -m app.subscription [{'|'.join(acoes)}]")
    settings.validar_boot()
    acoes[acao]()


if __name__ == "__main__":
    main()
