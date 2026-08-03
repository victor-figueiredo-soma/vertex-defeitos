# vertex-defeitos

Validação automática de defeitos em devoluções de vestuário (AZZAS), com um Gemini
afinado no Vertex AI.

O cliente manda um e-mail com a **foto da peça** e o **motivo** do defeito. Um
webhook recebe a notificação, roda a inferência e responde o cliente — aprovando ou
recusando a devolução, ou encaminhando para decisão humana quando não há confiança
suficiente.

```
e-mail chega na caixa monitorada
  └─ Graph notifica POST /webhook   (valida clientState, responde 202 na hora)
       └─ background task
            assunto contém ASSUNTO_FILTRO? ──não──► ignora em silêncio
                          │ sim
            baixa anexos ──► foto + motivo ──► inferência no modelo afinado
              ├─ APROVADO/REPROVADO, confiança ≥ 0,70 ──► responde o cliente
              ├─ INCONCLUSIVO ou confiança baixa ──────► cliente: "em análise"
              │                                          REVIEW_EMAIL: caso p/ humano
              ├─ e-mail sem foto ──────────────────────► pede a foto ao cliente
              └─ falha (Vertex, Graph) ────────────────► ALERT_EMAIL
```

Toda resposta ao cliente vai para o remetente original (`message.from`), como reply
na mesma thread.

## Começando

```powershell
uv sync                  # cria o .venv a partir do uv.lock
cp .env.example .env     # preencha as variáveis marcadas SENSIVEL
```

As imagens do dataset não vêm no repositório (127 MB). Para rodar avaliação local:

```powershell
gcloud storage cp -r gs://vertex-defeitos/staging-v3lite build/staging_768
gcloud storage cp -r gs://vertex-defeitos/staging build/staging   # originais
```

Classificar uma imagem pelo terminal:

```powershell
uv run python inferencia.py build/staging_768/034_mancha_aprovado.jpg "Mancha"
```

Servir o webhook local: `uv run uvicorn app.main:app --port 8080`. Note que o
Microsoft Graph **não entrega notificação em `localhost`** — para exercitar o fluxo
sem deploy, use o gatilho manual:

```powershell
uv run python -m app.processar_manual listar          # o que há na caixa
uv run python -m app.processar_manual ultimo --dry-run # infere, NÃO envia e-mail
uv run python -m app.processar_manual ultimo           # processa e responde
```

O `--dry-run` mostra o julgamento e o texto exato que iria ao cliente. Use antes de
responder alguém de verdade.

Testes: `uv run python -m pytest tests/ -q`.

## Estrutura

Três camadas, na ordem em que o sistema se constrói: o `tuning/` produz um modelo,
a raiz é o que os dois lados compartilham, e o `app/` serve.

A dependência segue essa direção e **não deve ser invertida**: o `tuning/` não
importa nada de `app/`, e o `app/` não importa nada de `tuning/`. Só a raiz é
compartilhada.

**1. `tuning/` — pipeline de treino.** Roda pontualmente, quando se treina um modelo
novo. Sempre como módulo, a partir da raiz do repo:
`uv run python -m tuning.<script>`

| Módulo | Função |
|---|---|
| `gerar_dataset.py` | Monta o dataset a partir das fotos de origem |
| `otimizar_dataset.py` | Subamostragem balanceada + redução de imagem |
| `auditar_dataset.py` | Auditoria read-only do JSONL antes de gastar um job |
| `upload_gcs.py` | Sobe imagens e JSONL para o bucket |
| `criar_tuning_job.py` | Dispara o tuning job |
| `avaliar_modelo.py` | Mede o modelo (acurácia, matriz, política de confiança) |
| `analisar_deteccao.py` | Relatório de detecção e calibração (recall, ECE, IC 95%) |
| `testar_endpoint.py` | Testa o modelo em imagens **fora** da distribuição de treino, em três estratégias de motivo (genérico, correto, errado) |

**2. Raiz — compartilhado entre treino e produção**

| Arquivo | Função |
|---|---|
| `system_instruction.md` | Prompt do sistema. **Fonte única**, usada no treino e na inferência |
| `imagem.py` | Normalização de imagem. Garante a **paridade de resolução** treino/inferência |
| `config.py` | Projeto, região, bucket, modelo, endpoint, hiperparâmetros |
| `dataset/` | JSONL versionados (as imagens ficam no GCS) |
| `inferencia.py` | Cliente do modelo. Autônomo: sem Flask, sem infra |
| `catalogo_motivos.md` | Referência de rotulagem |

Os três primeiros são o que amarra treino e inferência. Mexer em qualquer um deles
só de um lado quebra a paridade — e quebra em silêncio, sem erro.

**3. `app/` — o webhook de produção (Railway).** Roda continuamente.

| Módulo | Função |
|---|---|
| `main.py` | Rotas FastAPI; loop que mantém a subscription viva |
| `processor.py` | Pipeline de uma mensagem: filtro → inferência → política → resposta |
| `email_parser.py` | Funções puras: extrai a foto e o motivo de uma mensagem |
| `graph_client.py` | Microsoft Graph: OAuth2 app-only, ler/enviar e-mail, subscriptions |
| `settings.py` | Env vars do app; resolução da credencial GCP |
| `subscription.py` | CLI e lógica de criar/renovar a subscription |
| `processar_manual.py` | CLI de operação: dispara o pipeline à mão e reprocessa e-mail que falhou |

`build/` fica fora do git: imagens de staging, relatórios, manifestos. Os JSONL em
`dataset/` são a exceção versionada — são pequenos e são a especificação exata do que
foi treinado.

## Configuração

O `config.py` carrega o `.env` com `os.environ.setdefault`, então **o ambiente real
sempre vence o arquivo** — no Railway o `.env` não existe e as variáveis vêm do painel.
Chaves com valor vazio são ignoradas, para não mascarar os defaults do código.

**Obrigatórias.** Não têm default e o boot recusa subir sem elas:

| Variável | O que é |
|---|---|
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` | App registration no Azure, com application permissions **Mail.Read** e **Mail.Send** |
| `GOOGLE_APPLICATION_CREDENTIALS` | Caminho do arquivo de chave (local) **ou o JSON inteiro da Service Account** (Railway, onde não há arquivo no disco). O app distingue pelo conteúdo |
| `GRAPH_MAILBOX` | Caixa de e-mail monitorada |
| `TUNED_ENDPOINT` | Resource name do modelo afinado |
| `ALERT_EMAIL` | Destino dos **erros de execução** — assunto `[vertex-defeitos ERRO]` |
| `REVIEW_EMAIL` | Destino da **fila de revisão humana** — assunto `[vertex-defeitos REVISAO]` |
| `WEBHOOK_CLIENT_STATE` | Segredo que valida cada notificação. Só para servir o webhook |
| `WEBHOOK_BASE_URL` | URL pública do serviço. Só para criar/renovar subscription |

`ALERT_EMAIL` e `REVIEW_EMAIL` são canais distintos de propósito: o primeiro é "algo
quebrou, alguém olha o sistema"; o segundo é "há uma devolução esperando decisão".

**Com default.** Só defina para mudar comportamento:

| Variável | Default | Nota |
|---|---|---|
| `ASSUNTO_FILTRO` | `devolucao` | Só processa e-mail cujo assunto **contenha** o termo (ignora acento e caixa). Vazio desliga o filtro |
| `CONFIDENCE_THRESHOLD` | `0.70` | Abaixo disso, vai para revisão humana |
| `VERTEX_MEDIA_RESOLUTION` | `MEDIA_RESOLUTION_HIGH` | Ver "Restrições" |
| `IMAGEM_MAX_DIM` | `768` | Maior lado da imagem, em treino **e** inferência |
| `VERTEX_THINKING_BUDGET` | `0` | Mínimo, como a doc de SFT recomenda |
| `GCP_PROJECT`, `GCP_LOCATION`, `GCS_BUCKET`, `VERTEX_BASE_MODEL` | ver `config.py` | |

`PORT` é injetada pelo Railway — não defina.

## Deploy no Railway

1. Aponte o serviço para este repositório. O `Dockerfile` é detectado
   automaticamente, e o `.dockerignore` corta `build/` e `dataset/` da imagem.
2. Gere o domínio público: **Settings → Networking → Generate Domain**.
3. Preencha as variáveis obrigatórias no painel, incluindo a `WEBHOOK_BASE_URL` com
   a URL gerada.
4. Com o serviço no ar, registre a subscription:
   `uv run python -m app.subscription criar`

O passo 4 precisa do serviço já respondendo: o Graph valida a URL no ato, fazendo um
`POST /webhook?validationToken=...` e esperando o token de volta em até 10 segundos.

**A renovação da subscription é automática.** Subscriptions de mail expiram em ~3
dias e, uma vez expiradas, **não podem ser renovadas** — o Graph as apaga e o `PATCH`
devolve 404. Por isso o app chama `subscription.garantir()` no boot e a cada 3 horas:

- não encontra nenhuma nossa → cria
- falta menos de 12h para expirar → renova
- tem folga → não faz nada
- renovação falha (já expirou) → cria outra

Ele identifica "as nossas" pelo `notificationUrl`, porque o tenant Azure é
compartilhado com outros projetos.

> **Isso depende do container estar de pé.** Se o serviço dormir por inatividade, o
> loop para e a subscription expira em silêncio. Em plano que suspende, use um cron
> externo chamando `python -m app.subscription manter`.

Comandos de inspeção: `listar`, `manter` (renova só se perto de expirar), `renovar`
(força agora).

## Restrições que o código assume

São propriedades atuais do sistema, não preferências. Mudar qualquer uma exige medir
de novo.

**Não volte para `gemini-2.5-flash`.** Ele retira em 2026-10-20 e não tem endpoint de
tuning declarado na documentação — jobs ficam em `JOB_STATE_RUNNING` sem emitir um
único step. O modelo em uso é `gemini-3.1-flash-lite`.

**O modelo afinado vive numa multi-region e o SDK legado não o alcança.** O SFT de
Gemini 3.x entrega o modelo em `locations/us`, e `vertexai.init(location='us')`
levanta `ValueError` — a lista de regiões do SDK é fixa. Por isso `inferencia.py`
usa `google-genai` para modelo afinado e mantém `vertexai` só para modelo-base.

Na mesma família de armadilha: os modelos 3.x **não respondem** em `us-central1`,
`europe-west4` nem `us-east4` para inferência (404) — só no endpoint `global`. Mas o
serviço de *tuning* aceita `us-central1`. São allowlists distintas.

**`mediaResolution` é a alavanca de token de imagem, não a dimensão em pixels.** A
doc é explícita: *"This doesn't affect the image dimensions sent to the model"*. Numa
foto de 768px, `HIGH` custa 1.290 tokens, `MEDIUM` 256 e `LOW` 64. Reduzir a
resolução da imagem mexe pouco no custo; este campo corta de verdade. Está em `HIGH`
porque `MEDIUM` custa 3,2pp de recall de detecção.

Ele **precisa ser o mesmo** no JSONL de treino e na inferência: servir um detalhe
visual diferente do treinado degrada em silêncio. É a mesma razão pela qual
`imagem.py` normaliza os dois lados.

**Não aplique controlled generation no modelo afinado.** Known issue do Vertex:
`response_mime_type`/`response_schema` num modelo afinado *piora* a qualidade — o SFT
já ensinou o formato. `inferencia.py` aplica só no modelo-base.

**Não reaproveite hiperparâmetro entre gerações de modelo.** A doc de migração pede
para rodar o primeiro job de um modelo novo nos defaults. Para este dataset o default
resolveu em `epochCount=40`, `adapterSize=2`, `lr=1.0` — 17,5M tokens (~US$52) para
166 exemplos, com a curva de eval plana desde o step ~150. Em re-treino, vale fixar
`--epochs` entre 5 e 8.

**Só dispare um tuning job por vez.** A quota `Global concurrent tuning jobs` tem
default 1 e é global, compartilhada entre todas as regiões e modelos.

## O modelo

```
base     : gemini-3.1-flash-lite
endpoint : definido em TUNED_ENDPOINT (.env local / painel do Railway)
```

O resource name carrega o número do projeto GCP e é tratado como valor sensível — não
fica em código nem aqui. Para descobrir o atual:

```powershell
gcloud ai tuning-jobs list --region=us-central1 --format="value(tunedModel.endpoint)"
```

Desempenho em **149 exemplos held-out** (`avaliar_modelo --holdout-vs v3lite`):

| Métrica | |
|---|---|
| Acurácia de decisão | 96,0% |
| Recall `APROVADO` | 96,9% |
| Recall `INCONCLUSIVO` | 90,0% |
| Falso reprovado (nega devolução legítima) | 3,1% |
| ECE (calibração) | 0,031 |

### O que o modelo NÃO faz bem

Três limites que afetam como o app o usa. Todos vêm da forma como o dataset foi
rotulado — o motivo e o resultado são derivados do **nome do arquivo**, não de
anotação humana.

**`confianca` não é incerteza calibrada.** O modelo emite `1.0` em toda foto nítida e
`0.35` em imagem degradada, sem meio-termo. Na prática é um detector de "a imagem
está ruim?". Consequência: o `CONFIDENCE_THRESHOLD` só dispara em foto borrada — em
foto nítida a resposta vai automática. Quem roteia para humano de fato é a classe
`INCONCLUSIVO`. **Não trate `confianca` como probabilidade de acerto.**

**O modelo ecoa o motivo alegado em vez de identificar o defeito.** No dataset,
`defeito_identificado` foi derivado do mesmo nome de arquivo que `motivo_alegado`, e
os dois são iguais em 166/166 exemplos. Resultado: se o cliente alega "Mancha" numa
peça rasgada, o modelo responde "Mancha localizada identificada". **A decisão
(aprovar/recusar) se mantém correta; a explicação não.** Por isso
`processor.montar_resposta_cliente` monta o texto ao cliente a partir do motivo que
*nós* extraímos do e-mail, e nunca repassa a `justificativa` do modelo. Há um teste
de regressão para isso.


## Dataset

| Arquivo | Linhas | Composição |
|---|---|---|
| `dataset/train.jsonl` | 278 | 215 APROVADO · 13 REPROVADO · 50 INCONCLUSIVO |
| `dataset/validation.jsonl` | 37 | 37 APROVADO |
| `dataset/train_v3lite.jsonl` | 166 | 123 APROVADO · 13 REPROVADO · 30 INCONCLUSIVO |
| `dataset/validation_v3lite.jsonl` | 37 | 37 APROVADO |

Os `v3lite` são o que treinou o modelo em uso. A subamostragem cortou `APROVADO` de
215 para 123 e preservou **todos os 13 `REPROVADO`**, melhorando a razão de 16,5:1
para 9,5:1 — o desbalanceamento que faz o modelo carimbar `APROVADO`.

Imagens no GCS: `gs://vertex-defeitos/staging/` (originais) e
`gs://vertex-defeitos/staging-v3lite/` (768px, as do tuning).

Outras limitações do dataset, além das três acima:


- **`INCONCLUSIVO` é sintético** — imagens degradadas de propósito (blur, exposição),
  não fotos ruins reais.
- `Sem etiqueta composição` e `Etiqueta de mostruário` estão no catálogo e no prompt,
  mas têm **0 exemplos**.

### Re-treinar

Use um **sufixo e um prefixo novos** em cada rodada; não reaproveite nome de
tentativa anterior.

```powershell
uv run python -m tuning.otimizar_dataset --sufixo v4 --staging-prefix staging-v4
uv run python -m tuning.auditar_dataset  --sufixo v4 --staging-dir build/staging_768
uv run python -m tuning.upload_gcs --staging-dir build/staging_768 `
    --staging-prefix staging-v4 --jsonl train_v4.jsonl validation_v4.jsonl
uv run python -m tuning.criar_tuning_job `
    --train-uri gs://vertex-defeitos/data/train_v4.jsonl `
    --validation-uri gs://vertex-defeitos/data/validation_v4.jsonl `
    --tuned-name validador-defeitos-v4 --epochs 6 --no-wait
```

Não rode `gerar_dataset.py` para ajustar o dataset existente: ele numera os arquivos
por índice sobre a listagem da origem, então regerar renomeia tudo e refaz o split. O
`otimizar_dataset.py` trabalha sobre os JSONL já curados.

Avaliar o resultado:

```powershell
uv run python -m tuning.avaliar_modelo --holdout-vs v4 --staging-dir build/staging_768 `
    --endpoint <novo-endpoint>
uv run python -m tuning.analisar_deteccao --in build/resultado_avaliacao.json
```

Nunca avalie o modelo afinado no dataset de treino.

## Autenticação e IAM

Vertex AI e Cloud Storage autenticam por **Service Account (ADC)**, não por API key.
`app/settings.ensure_credentials()` resolve nesta ordem:

1. `GOOGLE_APPLICATION_CREDENTIALS` com o **JSON** dentro → grava num arquivo
   temporário (o ADC do Google só consome caminho)
2. `GOOGLE_APPLICATION_CREDENTIALS` com um **caminho existente** → usa direto
3. `GOOGLE_APPLICATION_CREDENTIALS_JSON` → mesma materialização do item 1
4. `config.DEFAULT_SA_KEY`, e depois o ADC do ambiente

Um caminho definido mas inexistente é descartado em vez de propagado — devolvê-lo
faria o boot passar e a falha aparecer só na primeira inferência real.

A Service Account precisa de `roles/aiplatform.user` e `roles/storage.objectAdmin` no
bucket. Além dela, o **Vertex AI Service Agent** do projeto
(`service-<PROJECT_NUMBER>@gcp-sa-aiplatform.iam.gserviceaccount.com`) precisa de
leitura no bucket: é ele, não a SA, que lê as imagens durante o tuning.
