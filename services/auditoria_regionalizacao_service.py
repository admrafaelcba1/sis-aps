from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Iterable

import pandas as pd

from config.settings import RAW_DIR
from database.connection import db_session

VERSAO_AUDITORIA_REGIONALIZACAO = "v19.4-normalizacao-definitiva"


def _norm_text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip().upper()
    text = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("ASCII")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return " ".join(text.split())


def _clean_municipio_ms(value) -> str | None:
    """Remove prefixos comuns da fonte MS, como 'MT - CUIABA'."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    text = str(value).strip()
    text = re.sub(r"^\s*MT\s*[-–—]\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*MATO\s+GROSSO\s*[-–—]\s*", "", text, flags=re.IGNORECASE)
    text = " ".join(text.split())
    return text.title() if text else None


def _canon_region(value) -> str:
    """Normaliza rótulos equivalentes de Região de Saúde.

    A fonte MS usa nomes como OESTE MATOGROSSENSE, SUDOESTE MATOGROSSENSE
    e SUL MATOGROSSENSE. A base local usa Oeste, Vale do Guaporé e Região Sul.
    Esta função gera uma chave canônica para evitar divergências falsas.
    """
    n = _norm_text(value)
    if not n or n in {"NONE", "NAN", "NULL"}:
        return ""

    # remove palavras genéricas sem valor discriminante
    n = re.sub(r"\bREGIAO\b", "", n)
    n = re.sub(r"\bDE\b", "", n)
    n = re.sub(r"\bDA\b", "", n)
    n = re.sub(r"\bDO\b", "", n)
    n = re.sub(r"\bDAS\b", "", n)
    n = re.sub(r"\bDOS\b", "", n)
    n = re.sub(r"\bSAUDE\b", "", n)
    n = " ".join(n.split())

    # aliases pós-normalização; incluir variações com e sem MATO/MATOGROSSENSE
    aliases = {
        "SUL": "SUL",
        "SUL MATOGROSSENSE": "SUL",
        "SUL MATO GROSSENSE": "SUL",
        "OESTE": "OESTE",
        "OESTE MATOGROSSENSE": "OESTE",
        "OESTE MATO GROSSENSE": "OESTE",
        "SUDOESTE": "VALE GUAPORE",
        "SUDOESTE MATOGROSSENSE": "VALE GUAPORE",
        "SUDOESTE MATO GROSSENSE": "VALE GUAPORE",
        "VALE GUAPORE": "VALE GUAPORE",
        "VALE ARINOS": "VALE ARINOS",
        "VALE ARINO": "VALE ARINOS",
        "BAIXADA CUIABANA": "BAIXADA CUIABANA",
        "ALTO TAPAJOS": "ALTO TAPAJOS",
        "CENTRO NORTE": "CENTRO NORTE",
        "ARAGUAIA XINGU": "ARAGUAIA XINGU",
        "GARCAS ARAGUAIA": "GARCAS ARAGUAIA",
        "MEDIO ARAGUAIA": "MEDIO ARAGUAIA",
        "NORTE ARAGUAIA KARAJA": "NORTE ARAGUAIA KARAJA",
        "TELES PIRES": "TELES PIRES",
        "VALE PEIXOTO": "VALE PEIXOTO",
        "MEDIO NORTE": "MEDIO NORTE",
        "MEDIO NORTE MATOGROSSENSE": "MEDIO NORTE",
        "MEDIO NORTE MATO GROSSENSE": "MEDIO NORTE",
        "NORTE": "NORTE",
        "NORTE MATOGROSSENSE": "NORTE",
        "NORTE MATO GROSSENSE": "NORTE",
        "NOROESTE": "NOROESTE",
        "NOROESTE MATOGROSSENSE": "NOROESTE",
        "NOROESTE MATO GROSSENSE": "NOROESTE",
    }
    return aliases.get(n, n)


def _read_table_safe(table: str) -> pd.DataFrame:
    try:
        with db_session() as conn:
            return pd.read_sql_query(f"SELECT * FROM {table}", conn)
    except Exception:
        return pd.DataFrame()


def _latest_raw_file() -> Path | None:
    apis_dir = RAW_DIR / "apis"
    if not apis_dir.exists():
        return None
    patterns = [
        "dadosabertos_regioes_saude*.csv",
        "*regioes_saude*.csv",
        "*regionalizacao*.csv",
        "*dadosabertos_ms*.csv",
    ]
    files: list[Path] = []
    for pattern in patterns:
        files.extend(list(apis_dir.glob(pattern)))
    unique = {str(p): p for p in files if p.is_file()}
    if not unique:
        return None
    return max(unique.values(), key=lambda p: p.stat().st_mtime)


def _read_csv_flex(path: Path) -> pd.DataFrame:
    errors = []
    for sep in [",", ";", "\t", "|"]:
        for enc in ["utf-8-sig", "utf-8", "latin1"]:
            try:
                df = pd.read_csv(path, sep=sep, encoding=enc, dtype=str)
                if len(df.columns) >= 2:
                    return df
            except Exception as exc:
                errors.append(f"{sep}/{enc}: {exc}")
    raise ValueError("Não foi possível ler o CSV bruto de regionalização. " + " | ".join(errors[:4]))


def _find_col(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    normalized = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in normalized:
            return normalized[cand.lower()]
    cols = list(df.columns)
    for cand in candidates:
        key = cand.lower()
        for col in cols:
            if key in str(col).strip().lower():
                return col
    return None


def _normalize_ms(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    col_codigo = _find_col(work, [
        "codigo_ibge", "cod_ibge", "co_municipio", "codigo_municipio", "cod_municipio",
        "codigo municipio", "codmun", "ibge",
    ])
    col_mun = _find_col(work, [
        "municipio", "nome_municipio", "no_municipio", "municipio_regiao_saude", "município",
    ])
    col_regiao = _find_col(work, [
        "regiao_saude_ms", "regiao_saude", "regiao_de_saude", "região de saúde", "nome_regiao_saude",
        "no_regiao_saude", "regiao_saude_sus",
    ])
    col_macro = _find_col(work, [
        "macrorregiao_saude_ms", "macrorregiao_saude", "macro_regiao_saude", "macrorregiao",
        "macroregiao", "macrorregião de saúde", "macrorregiao_saude_sus",
    ])
    col_cod_regiao = _find_col(work, ["codigo_regiao_saude", "co_regiao_saude", "cod_regiao_saude"])
    col_cod_macro = _find_col(work, ["codigo_macrorregiao_saude", "co_macro_regiao_saude", "cod_macro_regiao_saude"])

    out = pd.DataFrame()
    if col_codigo:
        out["codigo_ibge"] = work[col_codigo].astype(str).str.extract(r"(\d{7})", expand=False)
    else:
        out["codigo_ibge"] = None
    out["municipio_ms_original"] = work[col_mun].astype(str).str.strip() if col_mun else None
    out["municipio_ms"] = out["municipio_ms_original"].map(_clean_municipio_ms)
    out["regiao_saude_ms"] = work[col_regiao].astype(str).str.strip() if col_regiao else None
    out["macrorregiao_saude_ms"] = work[col_macro].astype(str).str.strip() if col_macro else None
    out["codigo_regiao_saude_ms"] = work[col_cod_regiao].astype(str).str.strip() if col_cod_regiao else None
    out["codigo_macrorregiao_saude_ms"] = work[col_cod_macro].astype(str).str.strip() if col_cod_macro else None

    out = out[out["codigo_ibge"].astype(str).str.match(r"^51\d{5}$", na=False)].copy()
    out = out.drop_duplicates(subset=["codigo_ibge"], keep="first")
    return out.reset_index(drop=True)


def _preparar_base_sistema() -> pd.DataFrame:
    base = _read_table_safe("base_municipal_consolidada")
    if base.empty:
        base = _read_table_safe("municipios")
    if base.empty:
        return pd.DataFrame(columns=["codigo_ibge", "municipio_sistema", "regiao_saude_sistema"])
    sistema = base.copy()
    if "codigo_ibge" not in sistema.columns:
        sistema["codigo_ibge"] = None
    sistema["codigo_ibge"] = sistema["codigo_ibge"].astype(str).str.extract(r"(\d{7})", expand=False)
    for col in ["municipio", "regiao_saude"]:
        if col not in sistema.columns:
            sistema[col] = None
    sistema = sistema[["codigo_ibge", "municipio", "regiao_saude"]].drop_duplicates(subset=["codigo_ibge"], keep="first")
    sistema = sistema.rename(columns={"municipio": "municipio_sistema", "regiao_saude": "regiao_saude_sistema"})
    return sistema


def montar_auditoria_regionalizacao_ms() -> dict:
    arquivo = _latest_raw_file()
    if arquivo is None:
        return {
            "ok": False,
            "mensagem": "Nenhum CSV bruto de regionalização encontrado em data/raw/apis. Execute primeiro o bloco Dados Abertos/MS — Regionalização de Saúde.",
            "arquivo": None,
            "raw": pd.DataFrame(),
            "regionalizacao_ms": pd.DataFrame(),
            "comparacao": pd.DataFrame(),
            "resumo": {},
            "agregado_regioes": pd.DataFrame(),
            "divergencias": pd.DataFrame(),
        }

    raw = _read_csv_flex(arquivo)
    ms = _normalize_ms(raw)
    sistema = _preparar_base_sistema()
    comp = sistema.merge(ms, on="codigo_ibge", how="outer")

    # Garante colunas usadas abaixo mesmo quando a fonte não trouxe algum campo
    for col in ["municipio_sistema", "regiao_saude_sistema", "municipio_ms", "regiao_saude_ms", "macrorregiao_saude_ms"]:
        if col not in comp.columns:
            comp[col] = None

    comp["regiao_sistema_norm"] = comp["regiao_saude_sistema"].map(_canon_region)
    comp["regiao_ms_norm"] = comp["regiao_saude_ms"].map(_canon_region)
    comp["municipio_sistema_norm"] = comp["municipio_sistema"].map(_norm_text)
    comp["municipio_ms_norm"] = comp["municipio_ms"].map(_norm_text)

    comp["status_comparacao"] = "OK"

    # Primeiro ausências estruturais
    comp.loc[comp["municipio_sistema"].isna(), "status_comparacao"] = "Sem município na base do sistema"
    comp.loc[comp["municipio_ms"].isna(), "status_comparacao"] = "Sem município na fonte MS"

    # Boa Esperança do Norte: ausência esperada na fonte MS atual
    mask_boa = comp["municipio_sistema"].map(_norm_text).eq("BOA ESPERANCA NORTE") & comp["municipio_ms"].isna()
    comp.loc[mask_boa, "status_comparacao"] = "Município novo ausente na fonte MS"

    # Como o merge é por codigo_ibge, divergência de nome não deve ser problema operacional.
    # Mantemos apenas para casos sem código em alguma ponta.
    mask_nome = (
        comp["codigo_ibge"].isna()
        & comp["municipio_sistema"].notna()
        & comp["municipio_ms"].notna()
        & (comp["municipio_sistema_norm"] != comp["municipio_ms_norm"])
    )
    comp.loc[mask_nome, "status_comparacao"] = "Nome divergente"

    # Região divergente só se ambas existem e as chaves canônicas forem diferentes.
    mask_reg = (
        comp["municipio_ms"].notna()
        & comp["regiao_saude_sistema"].notna()
        & comp["regiao_saude_ms"].notna()
        & (comp["regiao_sistema_norm"] != comp["regiao_ms_norm"])
    )
    comp.loc[mask_reg, "status_comparacao"] = "Região divergente"

    # Reaplica ausências no final para sobrepor qualquer cálculo de região
    comp.loc[comp["municipio_sistema"].isna(), "status_comparacao"] = "Sem município na base do sistema"
    comp.loc[comp["municipio_ms"].isna(), "status_comparacao"] = "Sem município na fonte MS"
    comp.loc[mask_boa, "status_comparacao"] = "Município novo ausente na fonte MS"

    # Colunas úteis para depuração visual, sem prejudicar UI atual
    comp["regiao_sistema_canonica"] = comp["regiao_sistema_norm"]
    comp["regiao_ms_canonica"] = comp["regiao_ms_norm"]

    agg = pd.DataFrame()
    if not ms.empty and "regiao_saude_ms" in ms.columns:
        agg = (
            ms.groupby(["macrorregiao_saude_ms", "regiao_saude_ms"], dropna=False)
            .agg(municipios=("codigo_ibge", "nunique"))
            .reset_index()
            .sort_values(["macrorregiao_saude_ms", "regiao_saude_ms"], na_position="last")
        )

    divergencias = comp[comp["status_comparacao"] != "OK"].copy()
    resumo = {
        "arquivo": str(arquivo),
        "linhas_raw": int(len(raw)),
        "municipios_ms": int(ms["codigo_ibge"].nunique()) if not ms.empty else 0,
        "municipios_sistema": int(sistema["codigo_ibge"].nunique()) if not sistema.empty else 0,
        "regioes_ms": int(ms["regiao_saude_ms"].dropna().nunique()) if "regiao_saude_ms" in ms.columns else 0,
        "macrorregioes_ms": int(ms["macrorregiao_saude_ms"].dropna().nunique()) if "macrorregiao_saude_ms" in ms.columns else 0,
        "divergencias": int(len(divergencias)),
        "versao_auditoria": VERSAO_AUDITORIA_REGIONALIZACAO,
        "observacao": "v19.4: comparação por código IBGE, sem divergência de nome quando o código bate, e com equivalência regional canônica para Oeste/Oeste Matogrossense, Região Sul/Sul Matogrossense, Vale do Guaporé/Sudoeste Matogrossense e Vale do Arinos/Vale dos Arinos.",
    }

    return {
        "ok": True,
        "mensagem": "Regionalização MS lida a partir do CSV bruto salvo pelo conector.",
        "arquivo": str(arquivo),
        "raw": raw,
        "regionalizacao_ms": ms,
        "comparacao": comp,
        "resumo": resumo,
        "agregado_regioes": agg,
        "divergencias": divergencias,
    }
