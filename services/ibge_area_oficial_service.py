from __future__ import annotations

from pathlib import Path
import unicodedata

import pandas as pd
import requests

from config.settings import GEO_DIR
from config.ibge_estimativas_2025_mt import CODIGO_IBGE_2025_MT_POR_MUNICIPIO

URL_AREAS_IBGE_2025_XLS = "https://geoftp.ibge.gov.br/organizacao_do_territorio/estrutura_territorial/areas_territoriais/2025/AR_BR_RG_UF_RGINT_RGI_MUN_2025.xls"
CACHE_AREAS_MT = GEO_DIR / "areas_territoriais_ibge_2025_mt.csv"


def _chave(valor) -> str:
    texto = str(valor or "").strip().upper()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    return " ".join(texto.split())


def _codigo_limpo(valor) -> str:
    import re
    digitos = re.sub(r"\D", "", "" if valor is None else str(valor))
    if len(digitos) >= 7:
        return digitos[:7]
    return digitos


def _normalizar_planilha_areas(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["codigo_ibge", "municipio", "area_km2"])

    original_cols = list(df.columns)
    cols_norm = {c: _chave(c) for c in original_cols}

    def achar_coluna(candidatos: list[str], contem_todos: list[str] | None = None):
        contem_todos = contem_todos or []
        for c, n in cols_norm.items():
            if n in candidatos:
                return c
        for c, n in cols_norm.items():
            if all(t in n for t in contem_todos):
                return c
        return None

    col_codigo = achar_coluna(["CD_MUN", "CD MUNICIPIO", "CODIGO MUNICIPIO", "CODIGO DO MUNICIPIO"], ["MUN"])
    col_nome = achar_coluna(["NM_MUN", "NOME DO MUNICIPIO", "MUNICIPIO"], ["MUN"])
    col_uf = achar_coluna(["SIGLA_UF", "SIGLA UF", "UF"], ["UF"])
    col_area = None
    # Prioriza a coluna temática municipal, geralmente AR_MUN_2025.
    for c, n in cols_norm.items():
        if ("AR" in n or "AREA" in n) and "MUN" in n and "2025" in n:
            col_area = c
            break
    if col_area is None:
        for c, n in cols_norm.items():
            if "AREA" in n or n.startswith("AR_") or n.startswith("AR "):
                col_area = c
                break

    if col_codigo is None or col_area is None:
        # Fallback por conteúdo: código IBGE municipal MT tem 7 dígitos e começa com 51.
        for c in original_cols:
            s = df[c].astype(str).str.replace(r"\D", "", regex=True)
            score = s.str.match(r"^51\d{5}$", na=False).sum()
            if score > 50:
                col_codigo = c
                break
        numericas = []
        for c in original_cols:
            vals = pd.to_numeric(df[c], errors="coerce")
            if vals.notna().sum() > 50 and vals.max(skipna=True) and vals.max(skipna=True) > 100:
                numericas.append((c, vals.notna().sum(), vals.max(skipna=True)))
        if col_area is None and numericas:
            # Normalmente a área é uma coluna numérica com valores > 100, mas não código.
            for c, _, _ in numericas[::-1]:
                if c != col_codigo:
                    col_area = c
                    break

    if col_codigo is None or col_area is None:
        return pd.DataFrame(columns=["codigo_ibge", "municipio", "area_km2"])

    out = pd.DataFrame()
    out["codigo_ibge"] = df[col_codigo].map(_codigo_limpo)
    out["area_km2"] = pd.to_numeric(df[col_area], errors="coerce")
    out["municipio"] = df[col_nome].astype(str).str.strip() if col_nome is not None else None

    if col_uf is not None:
        uf = df[col_uf].astype(str).str.upper().str.strip()
        out = out[uf.eq("MT") | out["codigo_ibge"].astype(str).str.startswith("51")].copy()
    else:
        out = out[out["codigo_ibge"].astype(str).str.startswith("51")].copy()

    out = out[out["codigo_ibge"].astype(str).str.match(r"^51\d{5}$", na=False)].copy()
    out = out.dropna(subset=["area_km2"])
    out = out.drop_duplicates(subset=["codigo_ibge"], keep="last")
    return out[["codigo_ibge", "municipio", "area_km2"]]


def baixar_areas_oficiais_ibge_2025_mt(forcar: bool = False) -> pd.DataFrame:
    """Carrega a planilha oficial de Áreas Territoriais 2025 do IBGE.

    Se a planilha já tiver sido baixada/processada, usa cache local em data/geo.
    Requer o pacote xlrd para ler .xls. Caso xlrd não esteja instalado, retorna
    cache se existir; caso contrário, retorna DataFrame vazio sem quebrar o app.
    """
    CACHE_AREAS_MT.parent.mkdir(parents=True, exist_ok=True)
    if CACHE_AREAS_MT.exists() and not forcar:
        try:
            return pd.read_csv(CACHE_AREAS_MT, dtype={"codigo_ibge": str})
        except Exception:
            pass

    try:
        # Baixa para arquivo temporário para reduzir problemas de engine/URL.
        temp = CACHE_AREAS_MT.with_suffix(".xls")
        resp = requests.get(URL_AREAS_IBGE_2025_XLS, timeout=60)
        resp.raise_for_status()
        temp.write_bytes(resp.content)

        planilhas = pd.read_excel(temp, sheet_name=None, engine="xlrd")
        escolhido = pd.DataFrame()
        for nome, dados in planilhas.items():
            if "MUN" in _chave(nome):
                escolhido = _normalizar_planilha_areas(dados)
                if len(escolhido) >= 140:
                    break
        if escolhido.empty:
            for _, dados in planilhas.items():
                escolhido = _normalizar_planilha_areas(dados)
                if len(escolhido) >= 140:
                    break

        # Garante que só entram códigos oficiais de MT conhecidos no ciclo 2025.
        codigos_oficiais = set(CODIGO_IBGE_2025_MT_POR_MUNICIPIO.values())
        escolhido = escolhido[escolhido["codigo_ibge"].astype(str).isin(codigos_oficiais)].copy()
        if not escolhido.empty:
            escolhido.to_csv(CACHE_AREAS_MT, index=False)
        return escolhido
    except Exception:
        if CACHE_AREAS_MT.exists():
            try:
                return pd.read_csv(CACHE_AREAS_MT, dtype={"codigo_ibge": str})
            except Exception:
                pass
        return pd.DataFrame(columns=["codigo_ibge", "municipio", "area_km2"])
