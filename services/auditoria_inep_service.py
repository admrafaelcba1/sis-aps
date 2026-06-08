from __future__ import annotations

import pandas as pd

from database.connection import db_session
from database.queries import read_table

INDICADORES_INEP = [
    "escolas_total",
    "escolas_urbanas",
    "escolas_rurais",
    "escolas_indigenas",
    "escolas_quilombolas",
    "escolas_educacao_especial_aee",
    "matriculas_total",
    "matriculas_educacao_especial",
]

ROTULOS = {
    "escolas_total": "Escolas totais",
    "escolas_urbanas": "Escolas urbanas",
    "escolas_rurais": "Escolas rurais",
    "escolas_indigenas": "Escolas indígenas",
    "escolas_quilombolas": "Escolas quilombolas",
    "escolas_educacao_especial_aee": "Escolas com educação especial/AEE",
    "matriculas_total": "Matrículas totais",
    "matriculas_educacao_especial": "Matrículas em educação especial",
}


def _ler_indicadores_inep() -> pd.DataFrame:
    try:
        df = read_table("indicadores_municipais")
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return df
    df = df.copy()
    if "indicador" not in df.columns:
        return pd.DataFrame()
    df = df[df["indicador"].astype(str).isin(INDICADORES_INEP)].copy()
    if df.empty:
        return df
    df["valor"] = pd.to_numeric(df.get("valor"), errors="coerce")
    if "atualizado_em" in df.columns:
        df = df.sort_values("atualizado_em")
    # mantém último valor por município/indicador para evitar duplicidade entre recargas
    chaves = [c for c in ["municipio", "indicador"] if c in df.columns]
    if len(chaves) == 2:
        df = df.drop_duplicates(subset=chaves, keep="last")
    return df


def montar_visao_inep() -> dict:
    indicadores = _ler_indicadores_inep()
    base = read_table("base_municipal_consolidada")
    if base.empty:
        base = read_table("municipios")
    base_cols = [c for c in ["codigo_ibge", "municipio", "regiao_saude", "populacao", "latitude", "longitude"] if c in base.columns]
    base = base[base_cols].drop_duplicates(subset=["municipio"]) if not base.empty and "municipio" in base.columns else pd.DataFrame()

    if indicadores.empty:
        return {
            "resumo": {
                "municipios_com_base": 0,
                "escolas_total": 0,
                "escolas_rurais": 0,
                "escolas_indigenas": 0,
                "escolas_quilombolas": 0,
                "escolas_educacao_especial_aee": 0,
            },
            "municipios": base,
            "indicadores": indicadores,
            "agregacao": pd.DataFrame(),
            "qualidade": _qualidade_vazia(),
        }

    pivot = indicadores.pivot_table(index="municipio", columns="indicador", values="valor", aggfunc="sum").reset_index()
    pivot.columns.name = None
    for col in INDICADORES_INEP:
        if col not in pivot.columns:
            pivot[col] = 0
        pivot[col] = pd.to_numeric(pivot[col], errors="coerce").fillna(0)
    if not base.empty:
        out = base.merge(pivot, on="municipio", how="left")
    else:
        out = pivot.copy()
    for col in INDICADORES_INEP:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
    if "populacao" in out.columns:
        out["populacao"] = pd.to_numeric(out["populacao"], errors="coerce")
        out["escolas_por_10mil_hab"] = ((out["escolas_total"] / out["populacao"].replace({0: pd.NA})) * 10000).round(2)
        out["matriculas_por_1000_hab"] = ((out["matriculas_total"] / out["populacao"].replace({0: pd.NA})) * 1000).round(2)
    out["presenca_rural"] = out["escolas_rurais"] > 0
    out["presenca_indigena_escolar"] = out["escolas_indigenas"] > 0
    out["presenca_quilombola_escolar"] = out["escolas_quilombolas"] > 0
    out["presenca_educacao_especial_aee"] = out["escolas_educacao_especial_aee"] > 0

    resumo = {
        "municipios_com_base": int((out["escolas_total"] > 0).sum()) if "escolas_total" in out.columns else 0,
        "escolas_total": int(out["escolas_total"].sum()),
        "escolas_rurais": int(out["escolas_rurais"].sum()),
        "escolas_indigenas": int(out["escolas_indigenas"].sum()),
        "escolas_quilombolas": int(out["escolas_quilombolas"].sum()),
        "escolas_educacao_especial_aee": int(out["escolas_educacao_especial_aee"].sum()),
    }

    if "regiao_saude" in out.columns:
        agg = out.groupby("regiao_saude", dropna=False).agg(
            municipios=("municipio", "count"),
            populacao=("populacao", "sum") if "populacao" in out.columns else ("municipio", "count"),
            escolas_total=("escolas_total", "sum"),
            escolas_urbanas=("escolas_urbanas", "sum"),
            escolas_rurais=("escolas_rurais", "sum"),
            escolas_indigenas=("escolas_indigenas", "sum"),
            escolas_quilombolas=("escolas_quilombolas", "sum"),
            escolas_educacao_especial_aee=("escolas_educacao_especial_aee", "sum"),
            matriculas_total=("matriculas_total", "sum"),
        ).reset_index()
    else:
        agg = pd.DataFrame()

    qualidade = _montar_qualidade(out)
    return {"resumo": resumo, "municipios": out, "indicadores": indicadores, "agregacao": agg, "qualidade": qualidade}


def _montar_qualidade(df: pd.DataFrame) -> pd.DataFrame:
    total = len(df) if df is not None else 0
    linhas = []
    for col in INDICADORES_INEP:
        preenchidos = int((pd.to_numeric(df.get(col, pd.Series(dtype=float)), errors="coerce").fillna(0) > 0).sum()) if total else 0
        linhas.append({
            "campo": ROTULOS.get(col, col),
            "coluna": col,
            "preenchidos": preenchidos,
            "pendentes_ou_zero": max(total - preenchidos, 0),
            "cobertura_%": round((preenchidos / total) * 100, 1) if total else 0,
            "status": "OK" if preenchidos > 0 else "Pendente",
        })
    return pd.DataFrame(linhas)


def _qualidade_vazia() -> pd.DataFrame:
    return pd.DataFrame([
        {"campo": ROTULOS.get(col, col), "coluna": col, "preenchidos": 0, "pendentes_ou_zero": 0, "cobertura_%": 0, "status": "Pendente"}
        for col in INDICADORES_INEP
    ])
