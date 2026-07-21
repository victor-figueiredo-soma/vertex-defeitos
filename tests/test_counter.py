# -*- coding: utf-8 -*-
"""Testes da logica pura do contador de re-treino (compute_next).

O incremento atomico real usa transacao Firestore; aqui validamos a REGRA de
disparo (incrementa, dispara em N, zera) de forma isolada e deterministica.
"""

from app.persistence import counter


def test_incremento_simples_nao_dispara_antes_do_limiar():
    stored, count, fired = counter.compute_next(current=0, threshold=3)
    assert (stored, count, fired) == (1, 1, False)


def test_penultimo_nao_dispara():
    stored, count, fired = counter.compute_next(current=1, threshold=3)  # -> 2
    assert (stored, count, fired) == (2, 2, False)


def test_atinge_o_limiar_dispara_e_zera():
    stored, count, fired = counter.compute_next(current=2, threshold=3)  # -> 3
    assert count == 3
    assert fired is True
    assert stored == 0  # zera para o proximo ciclo


def test_current_none_conta_como_zero():
    stored, count, fired = counter.compute_next(current=None, threshold=2)
    assert (stored, count, fired) == (1, 1, False)


def test_limiar_um_dispara_sempre():
    stored, count, fired = counter.compute_next(current=0, threshold=1)
    assert fired is True and stored == 0
