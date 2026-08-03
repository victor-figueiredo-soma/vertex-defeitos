#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gatilho MANUAL do pipeline — CLI.

Existe por dois motivos:

1. O Microsoft Graph nao entrega webhook em localhost: exige URL publica HTTPS e
   a valida na criacao da subscription. Sem deploy (ou tunel), a notificacao
   automatica nao chega. Este CLI roda exatamente o mesmo `processar_mensagem`
   que o webhook chamaria, permitindo testar leitura do Graph + extracao da foto
   + inferencia + resposta ao cliente de ponta a ponta.
2. Em producao, e a forma de reprocessar um e-mail especifico que falhou.

Uso:
  uv run python -m app.processar_manual listar
  uv run python -m app.processar_manual ultimo
  uv run python -m app.processar_manual processar <MESSAGE_ID>
  uv run python -m app.processar_manual ultimo --dry-run   # nao envia e-mail

O --dry-run faz a inferencia de verdade e mostra o julgamento e o texto que SERIA
enviado, sem chamar o send_mail. Util para conferir antes de responder um cliente
real.
"""

import argparse
import sys

import requests

from app import email_parser, graph_client, processor, settings


def _stdout_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def listar(n):
    """Mensagens mais recentes da caixa monitorada."""
    url = (f"{graph_client.GRAPH_BASE}/users/{settings.GRAPH_MAILBOX}"
           f"/mailFolders/inbox/messages"
           f"?$top={n}&$select=id,subject,from,hasAttachments,receivedDateTime"
           f"&$orderby=receivedDateTime desc")
    r = requests.get(url, headers=graph_client._headers(), timeout=30)
    r.raise_for_status()
    msgs = r.json().get("value", [])
    if not msgs:
        print("caixa vazia (ou sem permissao Mail.Read).")
        return []
    # NAO mostra hasAttachments: o Graph reporta False quando a imagem e INLINE no
    # corpo (colada em vez de anexada), e foi exatamente o caso dos primeiros
    # testes reais - a coluna dizia "nao" e o anexo estava la. Mostra o filtro de
    # escopo, que e o que decide se o app olharia a mensagem.
    print(f"{'recebido':20} {'escopo':8} {'de':32} assunto")
    for m in msgs:
        frm = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "?")
        no_escopo = processor.assunto_no_escopo(m.get("subject"))
        print(f"{m.get('receivedDateTime','')[:19]:20} "
              f"{'PASSA' if no_escopo else '--':8} "
              f"{frm[:32]:32} {(m.get('subject') or '')[:40]}")
        print(f"  id: {m.get('id')}")
    if settings.ASSUNTO_FILTRO:
        print(f"\n(escopo: assunto precisa conter {settings.ASSUNTO_FILTRO!r})")
    return msgs


def _dry_run(message_id):
    """Faz tudo menos enviar e-mail: mostra julgamento e o texto que iria."""
    message = graph_client.get_message(message_id)
    frm = ((message.get("from") or {}).get("emailAddress") or {}).get("address", "?")
    subject = message.get("subject") or "(sem assunto)"
    print(f"de      : {frm}")
    print(f"assunto : {subject}")

    # A trava de escopo PRIMEIRO, na mesma ordem do pipeline real. Sem isto o
    # preview mentia: dizia "responderia pedindo a foto" para e-mail que o app
    # ignoraria por completo.
    if not processor.assunto_no_escopo(subject):
        print(f"\nFORA DO ESCOPO: o assunto nao contem "
              f"{settings.ASSUNTO_FILTRO!r}.")
        print("O app ignoraria esta mensagem em silencio - sem baixar anexo, "
              "sem chamar o Vertex, sem responder.")
        return

    attachments = graph_client.get_attachments(message_id)
    print(f"anexos  : {[(a['name'], a['contentType'], a['size']) for a in attachments]}")

    try:
        parsed = email_parser.parse(message, attachments)
    except email_parser.EmailSemImagem:
        print("\nSEM IMAGEM -> o app responderia pedindo a foto:")
        print(processor.RESPOSTA_SEM_IMAGEM)
        return

    print(f"motivo extraido: {parsed['motivo']!r}")
    print(f"imagem         : {len(parsed['image_bytes']):,} bytes {parsed['image_ext']}")

    import inferencia
    mime = {".jpg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp"}.get(parsed["image_ext"], "image/jpeg")
    julgamento, endpoint = inferencia.classify(
        parsed["image_bytes"], parsed["motivo"], mime_type=mime,
        endpoint=settings.TUNED_ENDPOINT)

    print(f"\nendpoint  : {endpoint}")
    print(f"resultado : {julgamento.get('resultado')}")
    print(f"confianca : {julgamento.get('confianca')}")
    print(f"revisao   : {julgamento.get('requer_revisao_manual')}")

    acao, motivo_acao = processor.decidir_acao(julgamento)
    print(f"acao      : {acao}  ({motivo_acao})")
    print("\n--- texto que SERIA enviado ao cliente ---")
    if acao == "responder":
        print(processor.montar_resposta_cliente(julgamento, parsed["motivo"]))
    else:
        print(processor.RESPOSTA_EM_ANALISE)
        print(f"\n(e o caso iria para REVIEW_EMAIL={settings.REVIEW_EMAIL})")
    print("\n(--dry-run: nenhum e-mail foi enviado)")


def main():
    _stdout_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("acao", choices=["listar", "ultimo", "processar"])
    ap.add_argument("message_id", nargs="?")
    ap.add_argument("--n", type=int, default=10, help="quantas listar (default: 10)")
    ap.add_argument("--dry-run", action="store_true",
                    help="infere e mostra, mas NAO envia e-mail")
    args = ap.parse_args()

    settings.validar_boot(webhook=False)
    settings.ensure_credentials()
    print(f"caixa   : {settings.GRAPH_MAILBOX}")
    print(f"endpoint: {settings.TUNED_ENDPOINT}")
    print()

    if args.acao == "listar":
        listar(args.n)
        return

    if args.acao == "ultimo":
        msgs = listar(1)
        if not msgs:
            return
        message_id = msgs[0]["id"]
        print()
    else:
        if not args.message_id:
            sys.exit("informe o MESSAGE_ID (ou use 'ultimo')")
        message_id = args.message_id

    if args.dry_run:
        _dry_run(message_id)
    else:
        # Mesmo caminho que o webhook usa. O dedup em memoria nao atrapalha aqui
        # porque cada execucao do CLI e um processo novo.
        processor.processar_mensagem(message_id)
        print("\nprocessado. Confira a caixa do remetente e o REVIEW_EMAIL.")


if __name__ == "__main__":
    main()
