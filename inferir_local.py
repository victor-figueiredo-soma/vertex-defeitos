#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Teste LOCAL do modelo — o primeiro entregavel utilizavel.

Roda a inferencia isolada (imagem local + motivo -> JSON de julgamento), sem
depender de Outlook, Cloud Run ou Firestore. Serve para validar o modelo assim
que a role roles/aiplatform.user for concedida a Service Account.

Uso:
  # aponte para o modelo afinado (ou omita para usar o Gemini base de fallback):
  $env:TUNED_ENDPOINT = "projects/.../locations/.../endpoints/123"   # PowerShell

  uv run python inferir_local.py --image caminho/foto.jpg --motivo "Mancha"
  uv run python inferir_local.py --image foto.jpg --motivo "Furo" --endpoint <endpoint>

Sem --endpoint e sem TUNED_ENDPOINT, cai no modelo-base (so exercita o pipeline;
NAO e o validador afinado).
"""

import sys
import json
import argparse

from app.inference import classifier


def main():
    ap = argparse.ArgumentParser(description="Teste local do validador de defeitos.")
    ap.add_argument("--image", required=True, help="caminho da imagem da peca")
    ap.add_argument("--motivo", required=True, help="motivo alegado pelo cliente")
    ap.add_argument("--endpoint", default=None,
                    help="endpoint/modelo a usar (default: TUNED_ENDPOINT ou fallback)")
    args = ap.parse_args()

    endpoint, origem = classifier.resolve_endpoint()
    if args.endpoint:
        endpoint, origem = args.endpoint, "cli"
    print(f"[modelo] {endpoint}  (origem: {origem})", file=sys.stderr)
    if origem == "fallback":
        print("[aviso] usando modelo-base de fallback, NAO o validador afinado.",
              file=sys.stderr)

    julgamento, _ = classifier.classify_file(args.image, args.motivo, endpoint=endpoint)
    print(json.dumps(julgamento, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
