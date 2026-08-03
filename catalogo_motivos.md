# Catálogo de Motivos de Defeito — Validação de Devoluções (AZZAS)

> Documento de referência para rotulagem do dataset e base do `system_instruction`
> do modelo de validação de defeitos (fine-tuning Gemini / Vertex AI).
>
> Versão: 1.0 — 2026-06-12

## 1. Objetivo do modelo

O modelo recebe **uma imagem da peça** e o **motivo de defeito alegado pelo cliente**
(texto) e deve produzir dois julgamentos:

1. **Existe defeito?** — há, de fato, um defeito físico na peça?
2. **O motivo confere?** — o defeito encontrado corresponde ao motivo alegado pelo cliente?

A partir disso, o modelo decide o **resultado**: `APROVADO`, `REPROVADO` ou
`INCONCLUSIVO` (quando não há confiança suficiente para afirmar).

## 2. Observação geral sobre marcações nas imagens

Algumas imagens podem conter marcação manual (círculo/elipse, geralmente verde)
sobre a área do defeito. Quando presente, trate-a como **indicação da região de
interesse** — vá até ela e confirme o defeito. A **ausência** de marcação não
significa ausência de defeito: avalie a peça inteira. A marcação nunca substitui
o julgamento.

Elementos de fundo são ruído e devem ser ignorados: mãos/unhas, fita crepe,
etiqueta de tamanho, linha de qualidade, saco plástico, piso, cabide, mesa.

## 3. Trilhas de classificação

Os motivos são organizados em três trilhas conforme a capacidade de decisão por imagem.

### Trilha A — Decididos por imagem (núcleo do modelo)

| Motivo | Descrição | Sinais a observar |
|---|---|---|
| **Mancha** | Alteração localizada de cor/tonalidade ou sujidade na superfície do tecido, não pertencente à estampa. | Contorno, distribuição, padrão de espalhamento; identificar sub-tipo (ver §5). |
| **Esgarçado** | Abertura ou afinamento da trama, com fibras distendidas e separação dos fios, sem necessariamente haver furo. | Regiões de tensão (costuras, entrepernas, axila); trama aberta. |
| **Fio puxado** | Laçada de fio deslocada para fora da trama (snag), formando fio solto ou repuxado. | Extensão, quantidade de pontos, rompimento associado. |
| **Furo** | Perda de material com perfuração ou rompimento do tecido/costura. | Bordas limpas (corte) vs. desfiadas (rasgo); costura rompida com desfiamento. |
| **Acessório quebrado** | Aviamento ou componente funcional/decorativo partido, solto, trincado ou ausente (fivela, ilhós, rebite, fecho, argola, aplicação). | Integridade do aviamento; peça faltando/trincada. |
| **Zíper** | Defeito no fecho: dente partido/faltante, cursor solto/ausente/fora do trilho, cremalheira que não fecha ou desalinha. | Componentes visíveis são avaliáveis; funcionalidade dinâmica não é avaliável em imagem estática → na dúvida, INCONCLUSIVO. |
| **Sem botão** | Botão ausente em posição prevista. | Botão reserva; marca de costura arrancada (queda) vs. posição que nunca recebeu botão. |
| **Peça suja/mofada** | Sujidade aderida ou mofo (pontos escuros/esverdeados, halo). | Mofo sugere umidade/armazenagem (logística), distinto de mancha de processo. |
| **Tecido (couro)** | Defeito específico de matéria-prima/couro: falha de curtimento, descascamento, cicatriz, vinco permanente, descolamento, irregularidade de superfície. | Falhas de superfície e estrutura do couro. |
| **Peça c/ defeito (genérico)** | Motivo aberto sem especificação pelo cliente. | Identificar e **nomear** o defeito presente em `defeito_identificado`; alta probabilidade de divergência. |
| **Sem etiqueta de composição** | Ausência da etiqueta interna obrigatória de composição/conservação. | Presença/ausência da etiqueta; não-conformidade (CDC/Inmetro). |
| **Etiqueta de mostruário** | Peça comercializada contendo etiqueta de mostruário/amostra indevida. | Identificação da etiqueta de mostruário. |

### Trilha B — Contexto apenas (não decididos por imagem) → revisão manual

| Motivo | Descrição | Tratamento |
|---|---|---|
| **Modelagem** | Inadequação de caimento/proporção/medidas em relação ao esperado. Depende de prova no corpo e tabela de medidas; a foto não evidencia defeito localizado. | `requer_revisao_manual = 1`; `resultado = INCONCLUSIVO`. |
| **Encolhimento** | Redução dimensional da peça, em regra após lavagem. Exige comparação de medidas antes/depois; não evidenciável por foto. | `requer_revisao_manual = 1`; `resultado = INCONCLUSIVO`. |

### Trilha C — Não-defeito → rota comercial

| Motivo | Descrição | Tratamento |
|---|---|---|
| **Acordo comercial** | Devolução motivada por decisão comercial, sem defeito físico do produto. | `existe_defeito = 0`; encaminhar à trilha comercial. Fornecido ao modelo apenas como contexto. |

## 4. Origem do defeito (fabricação vs. uso)

Quando avaliável, o modelo deve considerar indícios de origem para apoiar o
julgamento — sem que isso seja, por si só, decisivo:

- **Fabricação/expedição/logística:** defeito sem sinal de esforço ou lavagem
  (costura fraca, mancha de processo, aviamento mal fixado, mofo de estoque).
- **Uso/pós-venda:** defeito com sinal de esforço ou lavagem do cliente
  (rompimento por tensão, mancha com padrão de lavagem, botão arrancado).

## 5. Sub-tipos de Mancha (apenas identificação)

Quando o defeito identificado for **mancha**, preencher também `subtipo`:

- **`mancha_aparente_recebimento`** — mancha visível na entrega, sem padrão de
  espalhamento típico de lavagem.
- **`mancha_pos_lavagem`** — mancha com características de surgimento após
  lavagem/uso (ex.: halo difuso, migração de cor).
- **`mancha_indeterminada`** — sem elementos visuais suficientes para definir a origem.

O `subtipo` é um rótulo descritivo adicional e **não** altera a lógica de decisão.
Para os demais motivos, `subtipo = "n/a"`.

## 6. Esquema de saída (JSON)

```json
{
  "motivo_alegado": "string",
  "existe_defeito": "0 | 1 | null",
  "defeito_identificado": "string | null",
  "motivo_correto": "0 | 1 | null",
  "divergencia_motivo": {
    "houve": "0 | 1",
    "motivo_correto_sugerido": "string | null",
    "observacao": "string | null"
  },
  "subtipo": "string",
  "resultado": "APROVADO | REPROVADO | INCONCLUSIVO",
  "confianca": "número de 0.0 a 1.0",
  "requer_revisao_manual": "0 | 1",
  "justificativa": "string"
}
```

## 7. Lógica de decisão

Limiar de confiança: **`confianca < 0,70 → INCONCLUSIVO`** (parâmetro de governança, ajustável).

| Condição | existe_defeito | motivo_correto | resultado | divergência | revisão |
|---|---|---|---|---|---|
| Defeito confirmado, motivo confere | 1 | 1 | **APROVADO** | não | 0 |
| Defeito confirmado, motivo diverge | 1 | 0 | **APROVADO** | **sim** (especifica motivo correto) | 0 |
| Sem defeito (confiança alta) | 0 | 0 | **REPROVADO** | não | 0 |
| Confiança abaixo do limiar | null | null | **INCONCLUSIVO** | não | 1 |
| Trilha B (modelagem/encolhimento) | null | null | **INCONCLUSIVO** | não | 1 |
| Trilha C (acordo comercial) | 0 | — | **REPROVADO** (ótica de defeito) | não | rota comercial |

**Premissa adotada:** havendo defeito físico real, o resultado é APROVADO mesmo que
o cliente tenha errado o motivo — a divergência é registrada para fins de qualidade,
não para reprovar a devolução.
