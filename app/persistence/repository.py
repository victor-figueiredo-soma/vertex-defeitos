#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Repositorio: grava imagens no Cloud Storage e registros no Firestore.

Modelo de dados (colecao settings.FIRESTORE_COLLECTION, doc por devolucao):
  { source, message_id, received_at, motivo_alegado, image_uri,
    model_output, model_version, status, decision, motivos_revisao,
    confirmed_label, confirmed_by, confirmed_at, used_in_training }

Docs de configuracao (colecao settings.FIRESTORE_CONFIG_COLLECTION):
  active_model:    { endpoint, updated_at }
  retrain_counter: { count_since_last, last_retrain_at, last_trained_endpoint }

Os clients sao criados sob demanda (lazy) para nao exigir credenciais no import
— importante para os testes unitarios das camadas puras.
"""

from app import settings

_storage_client = None
_firestore_client = None


def _storage():
    global _storage_client
    if _storage_client is None:
        from google.cloud import storage
        settings.ensure_credentials()
        _storage_client = storage.Client(project=settings.PROJECT_ID)
    return _storage_client


def _db():
    global _firestore_client
    if _firestore_client is None:
        from google.cloud import firestore
        settings.ensure_credentials()
        _firestore_client = firestore.Client(project=settings.PROJECT_ID)
    return _firestore_client


# ----------------------------------------------------------------------------
# Cloud Storage — imagens recebidas
# ----------------------------------------------------------------------------
def upload_image(devolucao_id, image_bytes, ext=".jpg"):
    """Sobe a imagem para gs://<bucket>/<inbox>/<id><ext> e retorna o gs:// URI."""
    blob_name = f"{settings.INBOX_PREFIX}/{devolucao_id}{ext}"
    bucket = _storage().bucket(settings.BUCKET_NAME)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(image_bytes)
    return f"gs://{settings.BUCKET_NAME}/{blob_name}"


# ----------------------------------------------------------------------------
# Firestore — registro de devolucao
# ----------------------------------------------------------------------------
def salvar_devolucao(devolucao_id, dados):
    """Cria/atualiza o documento da devolucao (merge)."""
    _db().collection(settings.FIRESTORE_COLLECTION).document(devolucao_id).set(
        dados, merge=True)
    return devolucao_id


def get_devolucao(devolucao_id):
    snap = _db().collection(settings.FIRESTORE_COLLECTION).document(devolucao_id).get()
    return snap.to_dict() if snap.exists else None


def listar_confirmados_nao_treinados():
    """Retorna (id, dados) dos registros confirmados ainda nao usados em treino.

    Fonte de verdade do dataset de re-treino — so entram rotulos confirmados.
    """
    col = _db().collection(settings.FIRESTORE_COLLECTION)
    query = col.where("status", "==", "confirmed").where("used_in_training", "==", False)
    return [(d.id, d.to_dict()) for d in query.stream()]


def marcar_como_treinados(ids):
    """Marca em lote os registros ja incorporados a um treino."""
    db = _db()
    batch = db.batch()
    col = db.collection(settings.FIRESTORE_COLLECTION)
    for i in ids:
        batch.update(col.document(i), {"used_in_training": True})
    batch.commit()


# ----------------------------------------------------------------------------
# Firestore — modelo ativo (trocado apos cada re-treino, sem redeploy)
# ----------------------------------------------------------------------------
def get_active_endpoint():
    snap = _db().collection(settings.FIRESTORE_CONFIG_COLLECTION).document(
        settings.ACTIVE_MODEL_DOC).get()
    return snap.to_dict().get("endpoint") if snap.exists else None


def set_active_endpoint(endpoint):
    from google.cloud import firestore
    _db().collection(settings.FIRESTORE_CONFIG_COLLECTION).document(
        settings.ACTIVE_MODEL_DOC).set(
        {"endpoint": endpoint, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)
