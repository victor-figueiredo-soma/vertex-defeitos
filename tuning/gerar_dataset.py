#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gerador de dataset para fine-tuning supervisionado do Gemini (Vertex AI).
Validacao de defeitos de devolucao - AZZAS.

O que faz:
  1. Le as imagens do diretorio de origem.
  2. Faz o parse do motivo e do resultado (APROVADO/REPROVADO) a partir do nome.
  3. Exclui Trilha B (Modelagem/Encolhimento), nao-imagens e arquivos sem rotulo.
  4. Deriva o JSON-alvo por REGRA (decisao do projeto - modo PoC).
  5. Sanitiza os nomes e gera copias em build/staging (para subir ao GCS).
  6. Faz o split estratificado por (motivo, resultado).
  7. Escreve train.jsonl e validation.jsonl no formato do Vertex AI.
  8. Roda uma varredura de qualidade (nitidez/brilho) e gera candidatos a
     INCONCLUSIVO em build/qualidade_candidatos.csv (NAO altera rotulos).

Modo PoC: rotulo derivado de motivo + aprovado/reprovado. Justificativa templatizada.
"""

import os
import re
import csv
import json
import shutil
import random
import unicodedata

# ----------------------------------------------------------------------------
# Configuracao
# ----------------------------------------------------------------------------
PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(PROJ_DIR)
SRC_DIR = os.environ.get("DATASET_ORIGEM", os.path.join(BASE, "dataset_defeitos"))
BUILD_DIR = os.path.join(PROJ_DIR, "build")
STAGING_DIR = os.path.join(BUILD_DIR, "staging")
# Os JSONL vao para dataset/ (versionado); as imagens ficam em build/ (fora do git).
DATASET_DIR = os.path.join(PROJ_DIR, "dataset")
SYSTEM_INSTRUCTION_FILE = os.path.join(PROJ_DIR, "system_instruction.md")

# Prefixo do bucket no Cloud Storage, usado nos fileUri de cada exemplo.
# Vem de GCS_BUCKET (mesma variavel que o config.py le) para que o JSONL gerado
# aqui e o upload_gcs.py nunca apontem para buckets diferentes.
GCS_BUCKET = os.environ.get("GCS_BUCKET", "vertex-defeitos")
GCS_PREFIX = f"gs://{GCS_BUCKET}/staging"

VAL_RATIO = 0.15          # fracao para validacao
MIN_GROUP_FOR_VAL = 4     # grupos menores que isso ficam inteiros no treino
SEED = 42

# Set de INCONCLUSIVO augmentado, derivado SO de imagens do split de treino
# (sem vazamento para validacao). Degradacoes que destroem a determinabilidade.
NUM_INCONCLUSIVO = 50
DEGRADES = ["heavy_blur", "underexposed", "overexposed", "pixelate"]
AUG_MAX_DIM = 1024        # redimensiona antes de degradar (consistencia + tamanho)
AUG_CONFIANCA = 0.35      # abaixo do limiar 0.70 -> INCONCLUSIVO

# Varredura de qualidade (apenas para sinalizar candidatos a revisao/INCONCLUSIVO).
# Nitidez e relativa: sinaliza o decil inferior do proprio dataset.
SHARPNESS_PCT = 10        # percentil de corte para baixa nitidez relativa
BRIGHT_MIN = 50.0         # muito escura
BRIGHT_MAX = 210.0        # muito estourada

# ----------------------------------------------------------------------------
# Catalogo de motivos
# ----------------------------------------------------------------------------
# Ordem importa: motivos especificos antes do generico "peca c/ defeito".
MOTIVO_PATTERNS = [
    ("Fio puxado",          [r"fio\s*puxado"]),
    ("Mancha",              [r"mancha"]),
    ("Esgarçado",           [r"esgar", r"egra"]),
    ("Zíper",               [r"ziper", r"zíper"]),
    ("Acessório quebrado",  [r"acess"]),
    ("Furo",                [r"furo"]),
    ("Sem botão",           [r"sem\s*bot", r"bot[ãa]o"]),
    ("Tecido (couro)",      [r"couro"]),
    ("Peça suja/mofada",    [r"suja", r"mofad"]),
    ("Sem etiqueta composição", [r"sem\s*etiqueta", r"etiqueta\s*composi"]),
    ("Etiqueta de mostruário",  [r"mostru"]),
    # Trilha B (excluida do dataset)
    ("Modelagem",           [r"modelagem"]),
    ("Encolhimento",        [r"encolhimento"]),
    # Generico - sempre por ultimo
    ("Peça c/ defeito",     [r"c\s*defeito", r"com\s*defeito", r"peça\s*c"]),
]

TRILHA_B = {"Modelagem", "Encolhimento"}

JUSTIF_APROVADO = {
    "Mancha":              "Mancha localizada identificada na peça, compatível com o motivo alegado.",
    "Esgarçado":           "Esgarçamento da trama identificado, compatível com o motivo alegado.",
    "Fio puxado":          "Fio puxado identificado no tecido, compatível com o motivo alegado.",
    "Furo":                "Furo/rompimento identificado na peça, compatível com o motivo alegado.",
    "Acessório quebrado":  "Aviamento/acessório com avaria identificado, compatível com o motivo alegado.",
    "Zíper":               "Defeito no zíper identificado, compatível com o motivo alegado.",
    "Sem botão":           "Ausência de botão confirmada, compatível com o motivo alegado.",
    "Peça suja/mofada":    "Sujidade/mofo identificado na peça, compatível com o motivo alegado.",
    "Tecido (couro)":      "Defeito de couro/matéria-prima identificado, compatível com o motivo alegado.",
    "Peça c/ defeito":     "Defeito físico confirmado na peça, compatível com a sinalização do cliente.",
    "Sem etiqueta composição": "Ausência da etiqueta de composição confirmada, compatível com o motivo alegado.",
    "Etiqueta de mostruário":  "Etiqueta de mostruário identificada na peça, compatível com o motivo alegado.",
}

IMG_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
           ".png": "image/png", ".jfif": "image/jpeg", ".webp": "image/webp"}

# ----------------------------------------------------------------------------
# Funcoes
# ----------------------------------------------------------------------------
def slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return re.sub(r"_+", "_", text)


def parse_motivo(name_lower):
    for canonical, patterns in MOTIVO_PATTERNS:
        for pat in patterns:
            if re.search(pat, name_lower):
                return canonical
    return None


def parse_resultado(name_lower):
    if "reprovado" in name_lower:
        return "REPROVADO"
    if "aprovado" in name_lower:
        return "APROVADO"
    return None


def derivar_alvo(motivo, resultado):
    """Modo PoC: deriva o JSON-alvo por regra."""
    if resultado == "APROVADO":
        return {
            "motivo_alegado": motivo,
            "existe_defeito": 1,
            "defeito_identificado": motivo,
            "motivo_correto": 1,
            "divergencia_motivo": {"houve": 0, "motivo_correto_sugerido": None, "observacao": None},
            "subtipo": "mancha_indeterminada" if motivo == "Mancha" else "n/a",
            "resultado": "APROVADO",
            "confianca": 1.0,
            "requer_revisao_manual": 0,
            "justificativa": JUSTIF_APROVADO.get(motivo, "Defeito confirmado, compatível com o motivo alegado."),
        }
    # REPROVADO
    return {
        "motivo_alegado": motivo,
        "existe_defeito": 0,
        "defeito_identificado": None,
        "motivo_correto": 0,
        "divergencia_motivo": {"houve": 0, "motivo_correto_sugerido": None, "observacao": None},
        "subtipo": "n/a",
        "resultado": "REPROVADO",
        "confianca": 1.0,
        "requer_revisao_manual": 0,
        "justificativa": f"Não foi identificado defeito compatível com o motivo alegado ({motivo}); devolução reprovada.",
    }


def load_system_instruction():
    with open(SYSTEM_INSTRUCTION_FILE, encoding="utf-8") as f:
        content = f.read()
    # Pega o corpo apos o primeiro separador '---' (remove o cabecalho/metadados).
    parts = content.split("\n---\n", 1)
    return parts[1].strip() if len(parts) == 2 else content.strip()


def sharpness_brightness(path):
    """Nitidez (variancia do laplaciano) e brilho medio, via Pillow."""
    try:
        from PIL import Image, ImageFilter, ImageStat
        with Image.open(path) as im:
            g = im.convert("L")
            g.thumbnail((512, 512))
            lap = g.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1))
            stat = ImageStat.Stat(lap)
            sharp = stat.stddev[0] ** 2
            bright = ImageStat.Stat(g).mean[0]
            return round(sharp, 1), round(bright, 1)
    except Exception as e:
        return None, None


JUSTIF_INCONCLUSIVO = {
    "heavy_blur":   "Imagem com desfoque acentuado; não é possível confirmar a existência ou o tipo de defeito.",
    "underexposed": "Imagem subexposta (muito escura); evidência visual insuficiente para decidir.",
    "overexposed":  "Imagem superexposta (estourada); evidência visual insuficiente para decidir.",
    "pixelate":     "Imagem com resolução/detalhe insuficiente; não é possível confirmar o defeito.",
}


def degrade(src_path, dst_path, kind):
    """Gera versao degradada que torna a foto indecidivel (caso INCONCLUSIVO)."""
    from PIL import Image, ImageFilter, ImageEnhance
    im = Image.open(src_path).convert("RGB")
    im.thumbnail((AUG_MAX_DIM, AUG_MAX_DIM))
    if kind == "heavy_blur":
        out = im.filter(ImageFilter.GaussianBlur(radius=max(im.size) * 0.04))
    elif kind == "underexposed":
        out = ImageEnhance.Brightness(im).enhance(0.18)
    elif kind == "overexposed":
        out = ImageEnhance.Contrast(ImageEnhance.Brightness(im).enhance(3.2)).enhance(0.5)
    elif kind == "pixelate":
        w, h = im.size
        f = max(8, min(w, h) // 36)
        out = im.resize((max(1, w // f), max(1, h // f)), Image.BILINEAR).resize((w, h), Image.NEAREST)
    else:
        out = im
    out.save(dst_path, "JPEG", quality=85)


def derivar_inconclusivo(motivo, kind):
    return {
        "motivo_alegado": motivo,
        "existe_defeito": None,
        "defeito_identificado": None,
        "motivo_correto": None,
        "divergencia_motivo": {"houve": 0, "motivo_correto_sugerido": None, "observacao": None},
        "subtipo": "n/a",
        "resultado": "INCONCLUSIVO",
        "confianca": AUG_CONFIANCA,
        "requer_revisao_manual": 1,
        "justificativa": JUSTIF_INCONCLUSIVO[kind],
    }


def jsonl_line(system_instruction, file_uri, mime, motivo, alvo):
    return {
        "systemInstruction": {"role": "system", "parts": [{"text": system_instruction}]},
        "contents": [
            {"role": "user", "parts": [
                {"fileData": {"mimeType": mime, "fileUri": file_uri}},
                {"text": f"Motivo alegado pelo cliente: {motivo}"},
            ]},
            {"role": "model", "parts": [
                {"text": json.dumps(alvo, ensure_ascii=False)}
            ]},
        ],
    }


# ----------------------------------------------------------------------------
# Execucao
# ----------------------------------------------------------------------------
def main():
    random.seed(SEED)
    os.makedirs(STAGING_DIR, exist_ok=True)
    os.makedirs(DATASET_DIR, exist_ok=True)
    system_instruction = load_system_instruction()

    registros = []          # exemplos validos
    excluidos = []          # (arquivo, motivo_exclusao)

    for root, _, files in os.walk(SRC_DIR):
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            name_lower = fn.lower()
            full = os.path.join(root, fn)

            if ext not in IMG_EXT:
                excluidos.append((fn, f"extensao nao suportada ({ext})"))
                continue
            motivo = parse_motivo(name_lower)
            resultado = parse_resultado(name_lower)
            if motivo is None:
                excluidos.append((fn, "motivo nao identificado"))
                continue
            if motivo in TRILHA_B:
                excluidos.append((fn, f"Trilha B - {motivo} (nao decidivel por imagem)"))
                continue
            if resultado is None:
                excluidos.append((fn, "sem APROVADO/REPROVADO no nome"))
                continue

            registros.append({"file": full, "fn": fn, "ext": ext,
                               "motivo": motivo, "resultado": resultado})

    # Sanitiza nomes + copia para staging + calcula qualidade
    quality_rows = []
    for i, r in enumerate(sorted(registros, key=lambda x: x["fn"])):
        out_ext = ".jpeg" if r["ext"] == ".jfif" else r["ext"]
        new_name = f"{i:03d}_{slugify(r['motivo'])}_{r['resultado'].lower()}{out_ext}"
        dst = os.path.join(STAGING_DIR, new_name)
        shutil.copy2(r["file"], dst)
        r["staging_name"] = new_name
        r["mime"] = IMG_EXT[r["ext"]]
        r["file_uri"] = f"{GCS_PREFIX}/{new_name}"

        sharp, bright = sharpness_brightness(r["file"])
        r["sharp"], r["bright"] = sharp, bright
        quality_rows.append({"staging_name": new_name, "motivo": r["motivo"],
                             "resultado": r["resultado"], "sharpness": sharp,
                             "brightness": bright, "flag": ""})

    # Flag de qualidade: nitidez no decil inferior (relativa) + exposicao extrema.
    sharps = sorted(q["sharpness"] for q in quality_rows if q["sharpness"] is not None)
    sharp_cut = sharps[max(0, len(sharps) * SHARPNESS_PCT // 100 - 1)] if sharps else 0
    for q in quality_rows:
        flag = []
        if q["sharpness"] is not None and q["sharpness"] <= sharp_cut:
            flag.append("baixa_nitidez_relativa")
        if q["brightness"] is not None and (q["brightness"] < BRIGHT_MIN or q["brightness"] > BRIGHT_MAX):
            flag.append("exposicao")
        q["flag"] = "|".join(flag)

    # Split estratificado por (motivo, resultado)
    grupos = {}
    for r in registros:
        grupos.setdefault((r["motivo"], r["resultado"]), []).append(r)

    train, val = [], []
    for key, items in grupos.items():
        items = sorted(items, key=lambda x: x["staging_name"])
        random.shuffle(items)
        n_val = round(len(items) * VAL_RATIO) if len(items) >= MIN_GROUP_FOR_VAL else 0
        for it in items[:n_val]:
            it["split"] = "validation"
        for it in items[n_val:]:
            it["split"] = "train"
        val.extend(items[:n_val])
        train.extend(items[n_val:])

    # INCONCLUSIVO augmentado: deriva SO de imagens de treino, em rodizio por motivo.
    by_motivo = {}
    for r in train:
        by_motivo.setdefault(r["motivo"], []).append(r)
    for m in by_motivo:
        random.shuffle(by_motivo[m])
    motivos_ordenados = sorted(by_motivo)
    pointers = {m: 0 for m in by_motivo}
    aug_records = []
    j = k = 0
    while len(aug_records) < NUM_INCONCLUSIVO:
        progrediu = False
        for m in motivos_ordenados:
            if len(aug_records) >= NUM_INCONCLUSIVO:
                break
            p = pointers[m]
            if p < len(by_motivo[m]):
                src = by_motivo[m][p]
                pointers[m] += 1
                progrediu = True
                kind = DEGRADES[k % len(DEGRADES)]
                k += 1
                name = f"aug_{j:03d}_inconclusivo_{kind}.jpeg"
                j += 1
                degrade(src["file"], os.path.join(STAGING_DIR, name), kind)
                aug_records.append({"staging_name": name, "motivo": src["motivo"],
                                    "kind": kind, "src": src["staging_name"],
                                    "file_uri": f"{GCS_PREFIX}/{name}"})
        if not progrediu:
            break

    # Escreve JSONL (train = reais de treino + inconclusivos augmentados; val = reais)
    def line_real(r):
        return jsonl_line(system_instruction, r["file_uri"], r["mime"],
                          r["motivo"], derivar_alvo(r["motivo"], r["resultado"]))

    def line_aug(a):
        return jsonl_line(system_instruction, a["file_uri"], "image/jpeg",
                          a["motivo"], derivar_inconclusivo(a["motivo"], a["kind"]))

    with open(os.path.join(DATASET_DIR, "train.jsonl"), "w", encoding="utf-8") as f:
        for r in train:
            f.write(json.dumps(line_real(r), ensure_ascii=False) + "\n")
        for a in aug_records:
            f.write(json.dumps(line_aug(a), ensure_ascii=False) + "\n")
    with open(os.path.join(DATASET_DIR, "validation.jsonl"), "w", encoding="utf-8") as f:
        for r in val:
            f.write(json.dumps(line_real(r), ensure_ascii=False) + "\n")

    # Manifesto de rastreabilidade (nome sanitizado -> arquivo original)
    with open(os.path.join(BUILD_DIR, "manifesto.csv"), "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["staging_name", "origem", "split", "motivo", "resultado",
                    "degrade", "arquivo_original"])
        for r in sorted(registros, key=lambda x: x["staging_name"]):
            w.writerow([r["staging_name"], "real", r["split"], r["motivo"],
                        r["resultado"], "", r["fn"]])
        for a in aug_records:
            w.writerow([a["staging_name"], "augmentado", "train", a["motivo"],
                        "INCONCLUSIVO", a["kind"], a["src"]])

    # Relatorio de qualidade (candidatos a revisao/INCONCLUSIVO)
    quality_rows.sort(key=lambda x: (x["sharpness"] is None, x["sharpness"] or 0))
    with open(os.path.join(BUILD_DIR, "qualidade_candidatos.csv"), "w",
              newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["staging_name", "motivo", "resultado",
                                          "sharpness", "brightness", "flag"])
        w.writeheader()
        w.writerows(quality_rows)

    # Relatorio geral
    from collections import Counter
    dist = Counter((r["motivo"], r["resultado"]) for r in registros)
    n_train_total = len(train) + len(aug_records)
    print("=" * 60)
    print(f"Reais validos: {len(registros)}  |  Inconclusivos aug: {len(aug_records)}")
    print(f"Total dataset: {len(registros) + len(aug_records)}")
    print(f"Treino: {n_train_total} ({len(train)} reais + {len(aug_records)} aug)  |  Validacao: {len(val)}")
    print(f"Excluidos: {len(excluidos)}")
    print("-" * 60)
    print("Distribuicao reais (motivo | resultado):")
    for (m, res), n in sorted(dist.items()):
        print(f"  {m:24s} {res:10s} {n}")
    print("-" * 60)
    print("Inconclusivos aug por degradacao:")
    for kind, n in sorted(Counter(a["kind"] for a in aug_records).items()):
        print(f"  {kind:16s} {n}")
    print("-" * 60)
    flagged = [q for q in quality_rows if q["flag"]]
    print(f"Candidatos a revisao por qualidade (CSV): {len(flagged)}")
    print("-" * 60)
    print("Exclusoes:")
    for reason, n in sorted(Counter(reason for _, reason in excluidos).items()):
        print(f"  {n:3d}  {reason}")
    print("=" * 60)
    print(f"Saidas em: {BUILD_DIR}")


if __name__ == "__main__":
    main()
