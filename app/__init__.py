"""Aplicacao de runtime do validador de defeitos (AZZAS).

Camadas:
  - intake:      recebimento de e-mail (Outlook / Microsoft Graph)
  - inference:   chamada ao modelo Gemini afinado
  - routing:     politica de decisao (hibrida por confianca)
  - persistence: Firestore (registro + contador) e GCS (imagens)
  - review:      confirmacao de rotulo por humano
  - retrain:     re-treino em lote disparado por N rotulos confirmados

O codigo de TREINAMENTO da raiz (gerar_dataset.py, upload_gcs.py,
criar_tuning_job.py, config.py) nao e alterado; e reusado por esta aplicacao.
"""
