#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Contador de re-treino — incremento ATOMICO com gatilho por lote.

Regra de negocio: a cada RETRAIN_THRESHOLD_INPUTS rotulos CONFIRMADOS POR HUMANO,
dispara-se o re-treino. So a camada de review chama isto (increment_and_maybe_trigger),
garantindo que o contador reflita verdade, nunca a propria previsao do modelo.

O incremento roda dentro de uma transacao Firestore para ser seguro sob
concorrencia (varias confirmacoes simultaneas). Quando o limiar e atingido, o
contador zera na MESMA transacao e uma mensagem e publicada no Pub/Sub.
"""

from app import settings
from app.persistence import repository


def compute_next(current, threshold):
    """Logica PURA do contador (testavel sem Firestore).

    Retorna (stored, count, fired):
      - count:  valor apos o incremento.
      - fired:  True se atingiu o limiar.
      - stored: valor a persistir (zera ao disparar; senao mantem o count).
    """
    count = int(current or 0) + 1
    fired = count >= threshold
    return (0 if fired else count), count, fired


def _counter_ref(db):
    return db.collection(settings.FIRESTORE_CONFIG_COLLECTION).document(
        settings.RETRAIN_COUNTER_DOC)


def _publicar_gatilho():
    """Publica no topico de re-treino. Retorna o message_id (ou None em erro)."""
    try:
        from google.cloud import pubsub_v1
        settings.ensure_credentials()
        publisher = pubsub_v1.PublisherClient()
        topic = publisher.topic_path(settings.PROJECT_ID, settings.RETRAIN_TOPIC)
        future = publisher.publish(topic, b"retrain")
        return future.result(timeout=30)
    except Exception as e:  # nao derruba a confirmacao do rotulo se o publish falhar
        print(f"[counter] AVISO: falha ao publicar gatilho de re-treino: {e}")
        return None


def increment_and_maybe_trigger(threshold=None, publish=True):
    """Incrementa o contador atomicamente; dispara re-treino ao atingir o limiar.

    Retorna dict: { count, threshold, fired, message_id }.
      - count: valor apos o incremento (antes de eventual reset).
      - fired: True se atingiu o limiar (contador foi zerado e gatilho publicado).
    """
    if threshold is None:
        threshold = settings.RETRAIN_THRESHOLD_INPUTS

    from google.cloud import firestore
    db = repository._db()
    ref = _counter_ref(db)

    @firestore.transactional
    def _txn(transaction):
        snap = ref.get(transaction=transaction)
        data = snap.to_dict() if snap.exists else {}
        stored, count, fired = compute_next(data.get("count_since_last"), threshold)
        transaction.set(ref, {"count_since_last": stored}, merge=True)
        return count, fired

    count, fired = _txn(db.transaction())

    message_id = None
    if fired and publish:
        message_id = _publicar_gatilho()

    return {"count": count, "threshold": threshold, "fired": fired,
            "message_id": message_id}


def registrar_checkpoint_retrain(trained_endpoint):
    """Chamado ao fim de um re-treino bem-sucedido (registra metadados)."""
    from google.cloud import firestore
    db = repository._db()
    _counter_ref(db).set(
        {
            "last_retrain_at": firestore.SERVER_TIMESTAMP,
            "last_trained_endpoint": trained_endpoint,
        },
        merge=True,
    )
