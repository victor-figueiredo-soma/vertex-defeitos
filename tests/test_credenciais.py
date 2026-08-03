#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes da resolucao de credencial GCP.

Regressao de um bug que quebrou o primeiro deploy no Railway: a forma natural de
configurar o painel e replicar o .env local inteiro, e ali GOOGLE_APPLICATION_
CREDENTIALS aponta para um arquivo que existe apenas na maquina do dev. Com as
duas variaveis definidas, o caminho inexistente vencia e o JSON inline era
ignorado - o boot passava e a falha aparecia so na primeira inferencia real,
depois de um cliente ja ter mandado e-mail.
"""

import json
import os

import pytest

from app import settings

FAKE_SA = json.dumps({
    "type": "service_account",
    "project_id": "projeto-teste",
    "private_key_id": "abc",
    "private_key": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
    "client_email": "sa@projeto-teste.iam.gserviceaccount.com",
    "client_id": "1",
    "token_uri": "https://oauth2.googleapis.com/token",
})


@pytest.fixture(autouse=True)
def _ambiente_limpo(monkeypatch, tmp_path):
    """Isola do .env e da chave real da maquina."""
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", raising=False)
    # DEFAULT_SA_KEY inexistente, para nao mascarar os cenarios sob teste.
    monkeypatch.setattr(settings._core, "DEFAULT_SA_KEY",
                        str(tmp_path / "nao-existe.json"))


def test_json_colado_na_variavel_de_caminho(monkeypatch):
    """GOOGLE_APPLICATION_CREDENTIALS com o JSON dentro, nao um caminho.

    E como o Railway esta configurado: uma variavel so, com o conteudo da chave.
    O ADC do Google exige caminho de arquivo, entao materializamos."""
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", FAKE_SA)
    caminho = settings.ensure_credentials()
    assert os.path.exists(caminho)
    assert json.load(open(caminho))["project_id"] == "projeto-teste"
    # A variavel passa a apontar para o arquivo, nao mais para o JSON.
    assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == caminho


def test_json_com_espacos_em_volta_ainda_e_reconhecido(monkeypatch):
    """Colar no painel costuma trazer espaco ou quebra de linha."""
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", f"\n  {FAKE_SA}  \n")
    assert os.path.exists(settings.ensure_credentials())


def test_caminho_inexistente_com_json_usa_o_json(monkeypatch, tmp_path):
    """O CENARIO DO RAILWAY. Antes: usava o caminho quebrado e falhava depois."""
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS",
                       str(tmp_path / "nao-existe.json"))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", FAKE_SA)

    caminho = settings.ensure_credentials()
    assert os.path.exists(caminho), "deveria ter materializado o JSON"
    assert json.load(open(caminho))["project_id"] == "projeto-teste"
    # E aponta o ADC para o arquivo criado.
    assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == caminho


def test_caminho_valido_vence_o_json(monkeypatch, tmp_path):
    """Chave em disco e mais barata que escrever temporario a cada boot."""
    real = tmp_path / "sa.json"
    real.write_text(FAKE_SA, encoding="utf-8")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(real))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", FAKE_SA)

    assert settings.ensure_credentials() == str(real)


def test_so_json_funciona(monkeypatch):
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", FAKE_SA)
    caminho = settings.ensure_credentials()
    assert os.path.exists(caminho)


def test_json_invalido_falha_no_boot(monkeypatch):
    """Melhor quebrar o deploy que subir e falhar no primeiro e-mail."""
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", "{isso nao e json")
    with pytest.raises(json.JSONDecodeError):
        settings.ensure_credentials()


def test_caminho_quebrado_sem_json_nao_devolve_caminho_invalido(monkeypatch,
                                                               tmp_path):
    """Sem JSON e sem chave em disco, cai no ADC do ambiente - nunca devolve um
    caminho que o SDK nao conseguiria abrir."""
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS",
                       str(tmp_path / "nao-existe.json"))
    try:
        resultado = settings.ensure_credentials()
    except RuntimeError:
        # Sem ADC no ambiente de teste: falhar aqui e o comportamento correto.
        return
    assert resultado is None or os.path.exists(resultado)
