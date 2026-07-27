# Validador de Defeitos de Devolução (AZZAS) — Fine-tuning Gemini / Vertex AI

Pipeline para afinar (fine-tuning supervisionado) um modelo Gemini no Vertex AI
que julga, a partir da **imagem da peça** + **motivo alegado pelo cliente**, se
uma devolução deve ser `APROVADO`, `REPROVADO` ou `INCONCLUSIVO`.

> **Status: PoC.** Os rótulos do dataset são derivados por regra a partir do nome
> do arquivo (motivo + aprovado/reprovado), não de anotação visual. Serve para
> validar o *pipeline* e o *formato*. Ver limitações no fim.

## Estrutura

### Treinamento (raiz — inalterado)
| Arquivo | Função |
|---|---|
| `gerar_dataset.py` | Lê as fotos, gera `build/staging/`, `train.jsonl`, `validation.jsonl`, manifesto e relatório de qualidade |
| `upload_gcs.py` | Cria o bucket e sobe imagens + JSONL para o Cloud Storage |
| `criar_tuning_job.py` | Dispara o tuning job supervisionado no Vertex AI |
| `config.py` | Configuração compartilhada (projeto, região, bucket, modelo) |
| `system_instruction.md` | Prompt do sistema (igual em treino e inferência) |
| `catalogo_motivos.md` | Referência de rotulagem |

### Aplicação de produção (`app/`)
| Módulo | Função |
|---|---|
| `inferir_local.py` | **CLI de teste local** do modelo (imagem + motivo → JSON), sem infra |
| `app/settings.py` | Config de runtime (endpoint, limiares, segredos) — reusa `config.py` |
| `app/inference/classifier.py` | Chama o modelo afinado e valida o JSON de saída |
| `app/routing/decision_router.py` | Política híbrida por confiança (auto vs. fila humana) |
| `app/intake/` | Ingestão Outlook: `graph_client`, `email_parser`, `webhook` (Cloud Run) |
| `app/persistence/` | `repository` (Firestore + GCS) e `counter` (contador atômico) |
| `app/review/confirm.py` | Registra rótulo confirmado por humano → dispara re-treino |
| `app/retrain/` | `build_dataset_from_prod` + `run_retrain` (Cloud Run Job) |
| `deploy/` | `Dockerfile.intake` (serviço) e `Dockerfile.retrain` (job) |
| `tests/` | Testes unitários das camadas puras |

## Fluxo de produção e re-treino

```
Outlook ──Graph webhook──► Cloud Run (app/intake/webhook)
        parser → GCS(inbox) → classifier → decision_router → Firestore
                                 │
              alta confiança → decisão automática
              incerto/baixa   → fila humana → app/review/confirm
                                                 │ (só rótulo confirmado)
                                      contador atômico +1 ; ao atingir
                                      RETRAIN_THRESHOLD_INPUTS → Pub/Sub
                                                 │
                                      Cloud Run Job (app/retrain/run_retrain):
                                      build dataset → upload → tuning → ativa
                                      novo endpoint (config/active_model)
```

### Testar o modelo localmente (primeiro passo utilizável)
Assim que a role `roles/aiplatform.user` estiver concedida:
```powershell
# aponte para o modelo afinado (ou omita para usar o Gemini base de fallback):
$env:TUNED_ENDPOINT = "projects/.../locations/.../endpoints/123"
uv run python inferir_local.py --image caminho\foto.jpg --motivo "Mancha"
```
Imprime o JSON de julgamento. Não depende de Outlook, Cloud Run ou Firestore.

### Serviços GCP integrados
| Necessidade | Serviço |
|---|---|
| Receber e-mail (Outlook) | Microsoft Graph API + Azure App Registration (segredo no Secret Manager) |
| Fluxo online (webhook + inferência) | Cloud Run (serviço) — `deploy/Dockerfile.intake` |
| Re-treino em lote | Cloud Run Job disparado por Pub/Sub — `deploy/Dockerfile.retrain` |
| Gatilho de re-treino | Pub/Sub (`retrain-trigger`) |
| Imagens recebidas | Cloud Storage (`inbox/` no bucket `azzas-defeitos`) |
| Registros + contador + fila | Firestore |
| Renovar subscription do Graph | Cloud Scheduler |
| Modelo afinado | Vertex AI (endpoint ativo em `config/active_model`) |

> **Segurança em produção:** o Cloud Run/Job usa a **identidade da service account
> anexada** (sem `sa_key.json`); só as credenciais Azure ficam no Secret Manager.

### Config de runtime (variáveis de ambiente — `app/settings.py`)
| Variável | Default | Observação |
|---|---|---|
| `RETRAIN_THRESHOLD_INPUTS` | `100` | Nº de rótulos confirmados para disparar re-treino |
| `CONFIDENCE_THRESHOLD` | `0.70` | Corte da política híbrida (espelha o system_instruction) |
| `TUNED_ENDPOINT` | — | Endpoint do modelo (senão lê `config/active_model` no Firestore) |
| `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` | — | App registration (Secret Manager) |
| `GRAPH_MAILBOX` | `dados@somagrupo.com.br` | Caixa monitorada |

## Autenticação — NÃO é API key

Vertex AI, Cloud Storage, Firestore e Pub/Sub autenticam por **Service Account
(ADC)**, não por API key. Chave em arquivo é apenas *uma* das formas de entregar
essa identidade — `config.ensure_credentials()` resolve nesta ordem:

```powershell
# Opção A: variável de ambiente (vence sempre)
$env:GOOGLE_APPLICATION_CREDENTIALS = "C:\Users\Victor_figueiredo\Documents\Atacado\sa_key.json"

# Opção B: chave no caminho padrão (config.DEFAULT_SA_KEY) — é o que o .env já faz

# Opção C: ADC do ambiente, SEM arquivo de chave
gcloud auth application-default login
```

**Em produção não há chave nenhuma:** o Cloud Run usa a SA anexada ao serviço
(via metadata server), que cai na opção C. Nunca embuta `sa_key.json` na imagem.

### Permissões (IAM) necessárias na Service Account, no projeto `soma-ai-hub`

| Role | Para quê |
|---|---|
| `roles/aiplatform.user` | Criar e rodar o tuning job / inferência |
| `roles/storage.admin` (ou `objectAdmin` no bucket) | Criar bucket e subir/ler as fotos |
| `roles/datastore.user` | Ler/gravar os registros e o contador no Firestore |
| `roles/pubsub.publisher` | Publicar o gatilho de re-treino em `retrain-trigger` |

Os dois últimos só afetam o app de produção (`app/`), não os scripts de dataset
e tuning. Sem eles a autenticação passa e a chamada falha com `PermissionDenied`.

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
