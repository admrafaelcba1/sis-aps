from __future__ import annotations

import io
import json
import re
import unicodedata
from datetime import datetime
from typing import Any

import pandas as pd
import requests

URL_XLSX = "https://dadosabertos.mt.gov.br/dataset/9ab63159-b5fb-4147-a1e9-76f50dd0d320/resource/f1898cd2-c120-4147-a28a-c115fa571a38/download/compensacao-ambiental-02fev2023.xlsx"
URL_DATASET = "https://dadosabertos.mt.gov.br/dataset/compensacao-ambiental"
FONTE = "DADOSABERTOS_MT_COMPENSACAO_AMBIENTAL_SEMA"


def _norm(txt: Any) -> str:
    s = "" if txt is None else str(txt)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s.lower()).strip("_")
    return s


def _limpar_texto(valor: Any) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    texto = str(valor).replace("\xa0", " ").strip()
    texto = re.sub(r"\s+", " ", texto)
    return texto


def _primeira_coluna(df: pd.DataFrame, opcoes: list[str]) -> str | None:
    norm_cols = {_norm(c): c for c in df.columns}
    for op in opcoes:
        if op in norm_cols:
            return norm_cols[op]
    for col_norm, col in norm_cols.items():
        for op in opcoes:
            if op and op in col_norm:
                return col
    return None


def _valor_numero(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    s = re.sub(r"[^0-9,.-]", "", s)
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _ano_de_linha(row: pd.Series) -> int | None:
    texto = " ".join(_limpar_texto(v) for v in row.values)
    m = re.search(r"\b(20\d{2}|19\d{2})\b", texto)
    if m:
        return int(m.group(1))
    return 2023


def _padronizar(df: pd.DataFrame) -> pd.DataFrame:
    # Remove linhas e colunas completamente vazias e normaliza cabeçalhos.
    df = df.dropna(axis=0, how="all").dropna(axis=1, how="all").copy()
    df.columns = [_limpar_texto(c) or f"coluna_{i+1}" for i, c in enumerate(df.columns)]

    col_municipio = _primeira_coluna(df, ["municipio", "munic", "cidade", "localidade"])
    col_processo = _primeira_coluna(df, ["processo", "n_processo", "numero_processo", "protocolo"])
    col_empreendedor = _primeira_coluna(df, ["empreendedor", "interessado", "requerente", "empresa", "nome"])
    col_empreendimento = _primeira_coluna(df, ["empreendimento", "atividade", "obra", "projeto", "descricao_do_empreendimento"])
    col_tipo = _primeira_coluna(df, ["tipo_compensacao", "compensacao", "modalidade", "tipo", "categoria"])
    col_valor = _primeira_coluna(df, ["valor", "valor_total", "vl", "montante", "recurso"])
    col_situacao = _primeira_coluna(df, ["situacao", "status", "fase", "andamento"])
    col_descricao = _primeira_coluna(df, ["descricao", "observacao", "objeto", "resumo", "informacoes"])
    col_lat = _primeira_coluna(df, ["latitude", "lat"])
    col_lon = _primeira_coluna(df, ["longitude", "lon", "lng", "long"])

    out = pd.DataFrame()
    out["municipio"] = df[col_municipio].map(_limpar_texto) if col_municipio else ""
    out["codigo_ibge"] = ""
    out["processo"] = df[col_processo].map(_limpar_texto) if col_processo else ""
    out["empreendedor"] = df[col_empreendedor].map(_limpar_texto) if col_empreendedor else ""
    out["empreendimento"] = df[col_empreendimento].map(_limpar_texto) if col_empreendimento else ""
    out["tipo_compensacao"] = df[col_tipo].map(_limpar_texto) if col_tipo else ""
    out["valor"] = df[col_valor].map(_valor_numero) if col_valor else None
    out["situacao"] = df[col_situacao].map(_limpar_texto) if col_situacao else ""
    out["ano"] = df.apply(_ano_de_linha, axis=1)
    out["descricao"] = df[col_descricao].map(_limpar_texto) if col_descricao else ""
    out["latitude"] = df[col_lat].map(_valor_numero) if col_lat else None
    out["longitude"] = df[col_lon].map(_valor_numero) if col_lon else None
    out["fonte_url"] = URL_XLSX
    out["dataset_titulo"] = "Compensação Ambiental"
    out["recurso_nome"] = "Compensação Ambiental - 02/02/2023"
    out["formato"] = "XLSX"
    out["url_dataset_portal"] = URL_DATASET
    out["observacao"] = "Base estadual de compensação ambiental publicada no Portal de Dados Abertos MT. Camada ambiental complementar; não entra automaticamente na Base Completa."

    atributos = []
    for _, row in df.iterrows():
        dados = {str(k): _limpar_texto(v) for k, v in row.items() if _limpar_texto(v)}
        atributos.append(json.dumps(dados, ensure_ascii=False))
    out["atributos_json"] = atributos

    # Remove linhas sem qualquer conteúdo analítico.
    uteis = ["municipio", "processo", "empreendedor", "empreendimento", "tipo_compensacao", "situacao", "descricao"]
    mask = out[uteis].astype(str).apply(lambda s: s.str.strip().replace("nan", "").replace("None", "")).ne("").any(axis=1)
    out = out[mask].copy()
    out = out.drop_duplicates(subset=["processo", "empreendedor", "empreendimento", "municipio"], keep="last")
    return out.reset_index(drop=True)


def carregar_compensacao_ambiental_sema_mt() -> pd.DataFrame:
    headers = {"User-Agent": "Mozilla/5.0 Plataforma APS SES-MT"}
    resp = requests.get(URL_XLSX, headers=headers, timeout=60)
    resp.raise_for_status()
    content = resp.content
    if not content or len(content) < 1024:
        raise RuntimeError("Arquivo XLSX de compensação ambiental veio vazio ou incompleto.")

    # Tenta todas as planilhas e escolhe a que gerar mais linhas úteis.
    planilhas = pd.read_excel(io.BytesIO(content), sheet_name=None, dtype=object)
    candidatos = []
    for nome, raw in planilhas.items():
        if raw is None or raw.empty:
            continue
        # Alguns XLSX vêm com cabeçalho deslocado. Testa leitura direta e variações simples.
        for header in [0, 1, 2, 3, 4, 5]:
            try:
                df = pd.read_excel(io.BytesIO(content), sheet_name=nome, header=header, dtype=object)
                pad = _padronizar(df)
                if len(pad) > 0:
                    pad["observacao"] = pad["observacao"] + f" Planilha: {nome}; header={header}."
                    candidatos.append(pad)
            except Exception:
                continue
    if not candidatos:
        raise RuntimeError("XLSX lido, mas nenhuma planilha apresentou linhas úteis reconhecíveis.")
    out = max(candidatos, key=len)
    return out


def testar_compensacao_ambiental_sema_mt() -> dict:
    try:
        df = carregar_compensacao_ambiental_sema_mt()
        return {
            "ok": True,
            "fonte": FONTE,
            "linhas": int(len(df)),
            "colunas": int(len(df.columns)),
            "municipios_preenchidos": int(df["municipio"].astype(str).str.strip().replace("", pd.NA).notna().sum()) if "municipio" in df.columns else 0,
            "url": URL_XLSX,
            "amostra_colunas": list(df.columns)[:20],
        }
    except Exception as exc:
        return {
            "ok": False,
            "fonte": FONTE,
            "erro": str(exc),
            "url": URL_XLSX,
        }
