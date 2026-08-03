# vertex-defeitos

Validação de defeitos em devoluções de vestuário (AZZAS) com Gemini afinado no
Vertex AI. Recebe a **foto da peça** + o **motivo alegado pelo cliente** e devolve um
JSON de julgamento: `APROVADO`, `REPROVADO` ou `INCONCLUSIVO`.

Este repositório contém o **modelo, o pipeline de tuning e o app de produção**:
um webhook (FastAPI, hospedado no Railway) que recebe notificações de novos
e-mails via Microsoft Graph, roda a inferência no modelo afinado e responde o
cliente automaticamente — com trava de confiança e revisão humana no circuito.

## App de produção — webhook

```
e-mail chega na caixa monitorada
  └─ Graph notifica POST /webhook (clientState validado, 202 imediato)
       └─ background task:
            busca e-mail + anexos ──► foto + motivo ──► inferência (endpoint afinado)
              ├─ APROVADO/REPROVADO com confiança ≥ 0.70 → responde o cliente
              ├─ INCONCLUSIVO / confiança < 0.70 → cliente: "em análise"
              │                                    REVIEW_EMAIL: caso p/ humano
              ├─ e-mail sem foto → responde pedindo a foto
              └─ qualquer falha (Vertex, Graph) → ALERT_EMAIL
```

Toda resposta ao cliente vai para o **remetente original** (`message.from`), como
reply na mesma thread. Os dois canais internos têm propósitos distintos:

| Canal | Recebe | Prefixo do assunto |
|---|---|---|
| `ALERT_EMAIL` | **erros de execução** — Vertex/Graph fora do ar, falha ao enviar | `[vertex-defeitos ERRO]` |
| `REVIEW_EMAIL` | **fila de revisão humana** — não é erro, é devolução esperando decisão | `[vertex-defeitos REVISAO]` |

`REVIEW_EMAIL` vazio cai em `ALERT_EMAIL`, para nenhum caso morrer em silêncio.

| Módulo | Função |
|---|---|
| `app/main.py` | Rotas FastAPI: handshake `validationToken`, validação estrita do `clientState`, 202 + BackgroundTasks |
| `app/processor.py` | Pipeline: e-mail → inferência → política de confiança → resposta/alerta |
| `app/graph_client.py` | Microsoft Graph: OAuth2 app-only (MSAL), ler mensagem/anexos, `send_mail`, subscriptions |
| `app/email_parser.py` | Funções puras: extrai foto (anexo) e motivo (assunto/corpo) da mensagem |
| `app/settings.py` | Env vars do app; credencial GCP via `GOOGLE_APPLICATION_CREDENTIALS_JSON` |
| `app/subscription.py` | CLI: criar/listar/renovar a subscription do Graph |

### Deploy no Railway

1. Crie o serviço apontando para este repo — o `Dockerfile` é detectado
   automaticamente e o `.dockerignore` corta `build/` e `dataset/` da imagem.
2. Configure as variáveis no painel:

| Variável | Valor |
|---|---|
| `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` | app registration com permissões de aplicação **Mail.Read** e **Mail.Send** |
| `WEBHOOK_BASE_URL` | URL pública do serviço (ex.: `https://xxx.up.railway.app`) |
| `WEBHOOK_CLIENT_STATE` | string aleatória longa — valida cada notificação |
| `GCP_PROJECT_ID` | `soma-ai-hub` |
| `GCP_LOCATION` | `us-central1` |
| `VERTEX_ENDPOINT_ID` | `711146528659472384` (ou o resource name completo) |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | o **conteúdo** do JSON da Service Account |
| `ALERT_EMAIL` | erros de execução (Vertex/Graph fora do ar) |
| `REVIEW_EMAIL` | fila de revisão humana (opcional — cai em `ALERT_EMAIL`) |
| `GRAPH_MAILBOX` | caixa monitorada (default `dados@somagrupo.com.br`) |

   O boot **recusa subir** se faltar variável crítica — melhor que aceitar
   webhook e falhar em silêncio.
3. Registre a subscription (depois do serviço no ar, pois o Graph valida a URL
   no ato): `uv run python -m app.subscription criar`
4. **Subscriptions de mail expiram em ~3 dias.** Agende
   `python -m app.subscription renovar` (cron do Railway) a cada 2 dias.

Rodar local: `uv run uvicorn app.main:app --port 8080`.

> **Por que foto + motivo, e não o corpo do e-mail:** o modelo é multimodal por
> construção — 100% dos exemplos de treino são `imagem + "Motivo alegado pelo
> cliente: X"`. Ele não julga texto sozinho; e-mail sem foto recebe resposta
> automática pedindo a foto.

## Modelo em produção

```
endpoint: projects/291753593894/locations/us/endpoints/711146528659472384
modelo base: gemini-3.1-flash-lite
```

Resultado medido em **149 exemplos held-out** (comparação pareada, mesmas imagens):

| | Base (`gemini-2.5-flash`) | **Afinado** |
|---|---|---|
| Acurácia de decisão | 81,2% | **96,0%** |
| Recall `APROVADO` | 84,5% | **96,9%** |
| Recall `INCONCLUSIVO` | 60,0% | **90,0%** |
| Falso reprovado (nega devolução legítima) | 12,1% | **3,1%** |
| ECE (calibração) | 0,120 | **0,031** |

> **Duas ressalvas antes de confiar nesses números:**
> 1. **`REPROVADO` não foi medido.** Os 13 exemplos do acervo estão todos no treino,
>    então a **especificidade** — a métrica que barra devolução indevida, e a pior do
>    baseline (30,8%) — segue sem número. A precisão de 100% no relatório de detecção
>    é artefato da ausência da classe, não mérito. Resolver exige rotular mais
>    `REPROVADO` ou usar os que vierem da produção.
> 2. **O ganho mistura duas mudanças:** troca de modelo-base e fine-tuning. O
>    zero-shot do `gemini-3.1-flash-lite` não foi medido, então a divisão é
>    desconhecida. São ~149 chamadas (~US$0,30) para descobrir.

## Estrutura

| Arquivo | Função |
|---|---|
| `inferencia.py` | **Cliente do modelo afinado.** Autônomo, sem infra. Base para o app |
| `system_instruction.md` | Prompt do sistema — **fonte única**, usada no treino e na inferência |
| `config.py` | Configuração compartilhada (projeto, região, bucket, modelo, hiperparâmetros) |
| `imagem.py` | Normalização de imagem compartilhada entre treino e inferência |
| `dataset/` | **JSONL versionados** — original (278/37) e o do tuning (166/37) |
| `gerar_dataset.py` | Gera o dataset a partir das fotos de origem |
| `otimizar_dataset.py` | Subamostragem balanceada + redução de imagem |
| `upload_gcs.py` | Sobe imagens + JSONL para o Cloud Storage |
| `criar_tuning_job.py` | Dispara o tuning job; grava `build/tuning_job.json` |
| `auditar_dataset.py` | Auditoria read-only do JSONL antes de gastar um job |
| `avaliar_modelo.py` | Mede o modelo (acurácia, matriz, política de confiança) |
| `analisar_deteccao.py` | Relatório de detecção e calibração (recall, ECE, IC 95%) |
| `catalogo_motivos.md` | Referência de rotulagem |

`build/` fica **fora do git**: imagens de staging (96 MB + 31 MB), relatórios e
manifestos. Os JSONL em `dataset/` são a exceção versionada — são pequenos e são a
especificação exata do que foi treinado.

## Começando

```powershell
uv sync                        # cria o .venv e instala tudo do pyproject.toml
cp .env.example .env           # ajuste GOOGLE_APPLICATION_CREDENTIALS se preciso
```

As imagens não vêm no repositório. Para rodar avaliação local, baixe do bucket:

```powershell
gcloud storage cp -r gs://vertex-defeitos/staging-v3lite build/staging_768
gcloud storage cp -r gs://vertex-defeitos/staging build/staging   # originais, 96 MB
```

### Usar o modelo

```powershell
uv run python inferencia.py build/staging_768/034_mancha_aprovado.jpg "Mancha"
```

Ou no código:

```python
import inferencia
julgamento, endpoint = inferencia.classify(image_bytes, "Mancha")
```

### Medir o modelo

```powershell
uv run python avaliar_modelo.py --holdout-vs v3lite --staging-dir build/staging_768
uv run python analisar_deteccao.py --in build/resultado_avaliacao.json
```

`--holdout-vs v3lite` avalia em **149 exemplos** (validação oficial + os 112 que a
subamostragem deixou fora do treino) em vez dos 37 da validação — 4× a amostra, e
inclui `INCONCLUSIVO`. Nunca avalie o modelo afinado no dataset de treino.

### Re-treinar

```powershell
uv run python otimizar_dataset.py --sufixo v4 --staging-prefix staging-v4
uv run python auditar_dataset.py --sufixo v4 --staging-dir build/staging_768
uv run python upload_gcs.py --staging-dir build/staging_768 `
    --staging-prefix staging-v4 --jsonl train_v4.jsonl validation_v4.jsonl
uv run python criar_tuning_job.py `
    --train-uri gs://vertex-defeitos/data/train_v4.jsonl `
    --validation-uri gs://vertex-defeitos/data/validation_v4.jsonl `
    --tuned-name validador-defeitos-v4 --epochs 6 --no-wait
```

**Use nomes novos** (`v4`, `staging-v4`) em cada rodada: não reaproveite prefixo,
URI nem display name de tentativa anterior.

## Lições que custaram caro

### O modelo base decide se o tuning roda

Quatro jobs em `gemini-2.5-flash` ficaram em `JOB_STATE_RUNNING` **sem emitir um único
step**, de 1h45 a 22h. A causa não era o dataset, IAM, região nem hiperparâmetro — foi
tudo descartado com evidência. Era o modelo: `gemini-2.5-flash` **retira em
2026-10-20** e é o único candidato sem a linha *"Supported endpoint for model tuning"*
na documentação. Trocar para `gemini-3.1-flash-lite` fez o primeiro step sair em 30
minutos e o job concluir em 2h09.

**Não volte para o 2.5-flash.**

### `mediaResolution` é a alavanca de token de imagem — não a dimensão

A doc é explícita: *"This doesn't affect the image dimensions sent to the model"*.
Medido em `gemini-2.5-flash` numa foto de 768px:

| `mediaResolution` | tokens |
|---|---|
| `HIGH` (default quando omitido) | 1.290 |
| `MEDIUM` | 256 |
| `LOW` | 64 |

Reduzir a imagem de 4000px para 768px leva de 3.354 para 1.290 tokens, mas a **média
do acervo só cai 6,8%** porque boa parte das fotos já era pequena. **A subamostragem
entrega ~95% da economia de treino; o resize, ~5%.** O resize continua no pipeline pelo
que realmente entrega: upload de 96 → 31 MB e menos latência por chamada.

Mantido em `HIGH`: medido, `MEDIUM` custa **3,2pp de recall de detecção**. E como
volume de token não influencia o job entrar em execução, não havia o que comprar
pagando qualidade.

### O endpoint afinado é multi-region e o SDK legado não o alcança

O SFT de Gemini 3.x entrega o modelo em `locations/us`, não na região do job.
`vertexai.init(location='us')` levanta `ValueError` (lista fixa de regiões). Por isso
`inferencia.py` usa **`google-genai`** para modelo afinado e mantém o `vertexai` só
para modelo-base.

Outra divergência da mesma família: os modelos 3.x **não respondem** em `us-central1`,
`europe-west4` nem `us-east4` para inferência (404), só no endpoint `global` — mas o
`GenAiTuningService` **aceita** `gemini-3.1-flash-lite` em `us-central1`. São
allowlists distintas.

### Não reaproveite hiperparâmetro entre gerações de modelo

A doc de migração é explícita, e o default do Vertex para este dataset resolveu para
`epochCount=40`, `adapterSize=2`, `lr=1.0` — **17,5M tokens, ~US$52** para 166
exemplos. Não houve overfitting (`eval_loss` caiu monotonicamente até 0,0001, sempre
abaixo do `train_loss`), mas a curva estava plana desde o step ~150. Para re-treinar,
fixe `--epochs` entre 5 e 8.

Nota: `adapterSize` aceita `2`, apesar de a assinatura do SDK dizer
`Literal[1,4,8,16,32]`. O serviço é quem valida.

### Controlled generation degrada modelo afinado

Known issue do Vertex: aplicar `response_mime_type`/`response_schema` num modelo
afinado *piora* a qualidade — o SFT já ensinou o formato. `inferencia.py` aplica só no
modelo-base.

## Dataset

| Arquivo | Linhas | Composição |
|---|---|---|
| `dataset/train.jsonl` | 278 | 215 APROVADO · 13 REPROVADO · 50 INCONCLUSIVO |
| `dataset/validation.jsonl` | 37 | 37 APROVADO |
| `dataset/train_v3lite.jsonl` | **166** | 123 APROVADO · **13 REPROVADO** · 30 INCONCLUSIVO |
| `dataset/validation_v3lite.jsonl` | 37 | 37 APROVADO |

Imagens no GCS: `gs://vertex-defeitos/staging/` (originais) e
`gs://vertex-defeitos/staging-v3lite/` (768px, as do tuning).

A subamostragem cortou `APROVADO` de 215 para 123 e preservou **todos os 13
`REPROVADO`**, melhorando a razão de 16,5:1 para 9,5:1 — o desbalanceamento que faz o
modelo carimbar `APROVADO`.

### Limitações conhecidas

- **Rótulos derivados por regra**, não por anotação humana: o motivo e o resultado vêm
  do nome do arquivo. Logo `confianca` é sempre 1.0, `divergencia_motivo.houve` sempre
  0 e a `justificativa` é template. O modelo aprende o formato, não a nuance.
- **`validation.jsonl` é 100% `APROVADO`** — responder sempre `APROVADO` daria 100%.
  Use `--holdout-vs`.
- **`INCONCLUSIVO` é sintético**: imagens degradadas artificialmente (blur, exposição),
  não fotos ruins reais.
- **`REPROVADO` pode não ser aprendível pela imagem:** em alguns casos o modelo
  descreve o defeito corretamente, mas o rótulo humano é `REPROVADO` — a recusa
  dependeu de critério não-visual (uso/lavagem, política comercial).
- `Sem etiqueta composição` e `Etiqueta de mostruário` estão no catálogo e no prompt,
  mas têm **0 exemplos** no dataset.

## Autenticação

Vertex AI, Storage e Firestore autenticam por **Service Account (ADC)**, não por API
key. `config.ensure_credentials()` resolve na ordem:
`GOOGLE_APPLICATION_CREDENTIALS` → `config.DEFAULT_SA_KEY` → ADC do ambiente
(metadata server no Cloud Run).

SA: `vertex-defeitos@soma-ai-hub.iam.gserviceaccount.com`. Roles necessárias:
`roles/aiplatform.user` e `roles/storage.objectAdmin` no bucket. O **Vertex AI Service
Agent** (`service-291753593894@gcp-sa-aiplatform.iam.gserviceaccount.com`) precisa de
leitura no bucket — é ele, não a SA, que lê as imagens durante o tuning.

> A quota **`Global concurrent tuning jobs`** tem default **1** e é *global,
> compartilhada entre todas as regiões e modelos*. Não dispare dois jobs em paralelo.
