"""Pipeline de fine-tuning: geracao de dataset, upload, tuning job e avaliacao.

Separado do app de producao (app/) de proposito - sao ciclos de vida diferentes:
o app roda continuamente no Railway, isto roda pontualmente quando se treina um
modelo novo. Os scripts sao executados como modulo, a partir da raiz do repo:

    uv run python -m tuning.otimizar_dataset --sufixo v4
    uv run python -m tuning.auditar_dataset --sufixo v4

O que e COMPARTILHADO entre tuning e producao fica na raiz, nao aqui:
config.py, imagem.py, inferencia.py e system_instruction.md. Em especial o
imagem.py, que garante a paridade de resolucao treino/inferencia - se ele vivesse
aqui, o app de producao dependeria deste pacote.
"""
