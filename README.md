# Validador de Defeitos de Devolução (AZZAS) — Fine-tuning Gemini / Vertex AI

Pipeline para afinar (fine-tuning supervisionado) um modelo Gemini no Vertex AI
que julga, a partir da **imagem da peça** + **motivo alegado pelo cliente**, se
uma devolução deve ser `APROVADO`, `REPROVADO` ou `INCONCLUSIVO`.

> **Status: PoC.** Os rótulos do dataset são derivados por regra a partir do nome
> do arquivo (motivo + aprovado/reprovado), não de anotação visual. Serve para
> validar o *pipeline* e o *formato*. Ver limitações no fim.

## Estrutura

| Arquivo | Função |
|---|---|
| `gerar_dataset.py` | Lê as fotos, gera `build/staging/`, `train.jsonl`, `validation.jsonl`, manifesto e relatório de qualidade |
| `upload_gcs.py` | Cria o bucket e sobe imagens + JSONL para o Cloud Storage |
| `criar_tuning_job.py` | Dispara o tuning job supervisionado no Vertex AI |
| `config.py` | Configuração compartilhada (projeto, região, bucket, modelo) |
| `system_instruction.md` | Prompt do sistema (igual em treino e inferência) |
| `catalogo_motivos.md` | Referência de rotulagem |

## Autenticação — NÃO é API key

O Vertex AI e o Cloud Storage autenticam por **Service Account (ADC)**, não por
API key. Configure a credencial de uma destas formas (a 1ª tem prioridade):

```powershell
# Opção A: variável de ambiente
$env:GOOGLE_APPLICATION_CREDENTIALS = "C:\Users\Victor_figueiredo\Documents\Atacado\sa_key.json"

# Opção B: deixar a chave no caminho padrão (config.DEFAULT_SA_KEY) — já é o default
```

### Permissões (IAM) necessárias na Service Account, no projeto `soma-ai-hub`

| Role | Para quê |
|---|---|
| `roles/aiplatform.user` | Criar e rodar o tuning job / inferência |
| `roles/storage.admin` (ou `objectAdmin` no bucket) | Criar bucket e subir/ler as fotos |

> Verificado em 2026-07-16: faltava `roles/aiplatform.user` — peça a um admin do
> `soma-ai-hub` para conceder antes de rodar o tuning.

## Passo a passo

```powershell
# 0. Instalar dependências
uv sync                         # cria o .venv e instala tudo do pyproject.toml
# (alternativa sem uv: pip install -r requirements.txt)

# Nos passos seguintes, use "uv run python ..." para rodar dentro do ambiente.

# 1. Gerar o dataset (se ainda não foi gerado — build/ já vem preenchido)
uv run python gerar_dataset.py

# 2. Subir imagens + JSONL para o Cloud Storage
uv run python upload_gcs.py            # use --dry-run primeiro para conferir
                                       # use --force para reenviar tudo

# 3. Disparar o fine-tuning no Vertex AI (GERA CUSTO)
uv run python criar_tuning_job.py                 # cria e acompanha até o fim
uv run python criar_tuning_job.py --no-wait       # cria e sai (acompanhe no Console)
```

## Configuração (via `config.py` ou variáveis de ambiente)

| Variável | Default | Observação |
|---|---|---|
| `GCP_PROJECT` | `soma-ai-hub` | Projeto de faturamento/execução |
| `GCP_LOCATION` | `us-central1` | **Região do tuning** — ver aviso abaixo |
| `GCS_BUCKET` | `azzas-defeitos` | Casa com os `fileUri` já gravados no JSONL |
| `VERTEX_BASE_MODEL` | `gemini-2.5-flash` | Modelo base do fine-tuning |

> **Aviso de região:** o fine-tuning do Gemini não existe em todas as regiões.
> `us-central1` é a mais garantida. A memória do projeto registra `us-west1`
> apenas para chamadas de inferência (`generateContent`). Se quiser tunar em
> `us-west1`, valide antes se o modelo aceita tuning lá. O bucket idealmente
> deve ficar na mesma região do tuning.

## Limitações conhecidas (PoC) — antes de considerar produção

- **Desbalanceamento severo:** 253 `APROVADO` × 13 `REPROVADO`. O modelo tende a
  aprovar quase tudo — o oposto do objetivo de barrar devoluções indevidas.
- **Rótulos por regra, não por imagem:** `confianca` é sempre 1.0, `divergencia`
  sempre 0, `justificativa` é template. O modelo aprende o formato, não a raciocinar.
- **INCONCLUSIVO sintético:** gerado por degradação (blur/exposição), não por
  casos reais de ângulo ruim / defeito não visível.
- **Recomendação para produção:** rerrotular por anotação visual humana e
  rebalancear as classes (mais exemplos `REPROVADO` e de motivos raros).

## Segurança

- `sa_key.json` e a pasta `build/` estão no `.gitignore` — **nunca** commitar
  credenciais nem os artefatos gerados.
