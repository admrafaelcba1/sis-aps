from __future__ import annotations

import pandas as pd

from database.queries import read_table

POVOS_COLS = [
    "pessoas_indigenas_2022",
    "pessoas_quilombolas_2022",
    "pessoas_tradicionais_total_2022",
]
DEF_COLS = [
    "pessoas_com_deficiencia_2022",
    "pessoas_diagnosticadas_autismo_2022",
    "pct_pessoas_com_deficiencia_2022",
    "pct_pessoas_diagnosticadas_autismo_2022",
]
TODOS = POVOS_COLS + DEF_COLS


def _indicadores_para_largo(ind: pd.DataFrame) -> pd.DataFrame:
    if ind.empty or "indicador" not in ind.columns:
        return pd.DataFrame()
    aux = ind[ind["indicador"].astype(str).str.lower().isin(TODOS)].copy()
    if aux.empty:
        return pd.DataFrame()
    aux["indicador"] = aux["indicador"].astype(str).str.lower()
    aux["valor"] = pd.to_numeric(aux.get("valor"), errors="coerce")
    if "codigo_ibge" not in aux.columns:
        aux["codigo_ibge"] = ""
    aux["codigo_ibge"] = aux["codigo_ibge"].astype(str).str.extract(r"(\d{7})", expand=False).fillna("")
    chaves = ["codigo_ibge", "municipio"] if "municipio" in aux.columns else ["codigo_ibge"]
    aux = aux.sort_values(chaves + ["indicador", "atualizado_em"], na_position="first")
    aux = aux.drop_duplicates(subset=chaves + ["indicador"], keep="last")
    largo = aux.pivot_table(index=chaves, columns="indicador", values="valor", aggfunc="sum").reset_index()
    largo.columns.name = None
    return largo


def montar_visao_ibge_censo2022() -> dict[str, pd.DataFrame | dict]:
    base = read_table("base_municipal_consolidada")
    ind = read_table("indicadores_municipais")
    largo = _indicadores_para_largo(ind)

    if base.empty:
        municipios = largo.copy()
    else:
        cols = [c for c in ["codigo_ibge", "municipio", "regiao_saude", "populacao"] if c in base.columns]
        municipios = base[cols].copy()
        if not largo.empty:
            if "codigo_ibge" in municipios.columns and "codigo_ibge" in largo.columns:
                municipios["codigo_ibge"] = municipios["codigo_ibge"].astype(str).str.extract(r"(\d{7})", expand=False)
                municipios = municipios.merge(largo.drop(columns=["municipio"], errors="ignore"), on="codigo_ibge", how="left")
            else:
                municipios = municipios.merge(largo, on="municipio", how="left")
    for col in TODOS:
        if col not in municipios.columns:
            municipios[col] = pd.NA
        municipios[col] = pd.to_numeric(municipios[col], errors="coerce")

    resumo = {
        "municipios": int(len(municipios)) if not municipios.empty else 0,
        "municipios_com_indigenas": int((municipios["pessoas_indigenas_2022"].fillna(0) > 0).sum()) if "pessoas_indigenas_2022" in municipios else 0,
        "municipios_com_quilombolas": int((municipios["pessoas_quilombolas_2022"].fillna(0) > 0).sum()) if "pessoas_quilombolas_2022" in municipios else 0,
        "populacao_indigena": int(municipios["pessoas_indigenas_2022"].fillna(0).sum()) if "pessoas_indigenas_2022" in municipios else 0,
        "populacao_quilombola": int(municipios["pessoas_quilombolas_2022"].fillna(0).sum()) if "pessoas_quilombolas_2022" in municipios else 0,
        "pessoas_com_deficiencia": int(municipios["pessoas_com_deficiencia_2022"].fillna(0).sum()) if "pessoas_com_deficiencia_2022" in municipios else 0,
        "pessoas_autismo": int(municipios["pessoas_diagnosticadas_autismo_2022"].fillna(0).sum()) if "pessoas_diagnosticadas_autismo_2022" in municipios else 0,
    }

    if not municipios.empty and "regiao_saude" in municipios.columns:
        agregacao = municipios.groupby("regiao_saude", dropna=False)[[c for c in TODOS if c in municipios.columns]].sum(numeric_only=True).reset_index()
        if "populacao" in municipios.columns:
            pop = municipios.groupby("regiao_saude", dropna=False)["populacao"].sum().reset_index(name="populacao")
            agregacao = pop.merge(agregacao, on="regiao_saude", how="left")
    else:
        agregacao = pd.DataFrame()

    qualidade_linhas = []
    total = max(len(municipios), 1)
    labels = {
        "pessoas_indigenas_2022": "População indígena",
        "pessoas_quilombolas_2022": "População quilombola",
        "pessoas_com_deficiencia_2022": "Pessoas com deficiência",
        "pessoas_diagnosticadas_autismo_2022": "Pessoas diagnosticadas com autismo",
    }
    for col, label in labels.items():
        serie = pd.to_numeric(municipios.get(col), errors="coerce") if col in municipios.columns else pd.Series(dtype=float)
        preenchidos = int(serie.notna().sum())
        positivos = int((serie.fillna(0) > 0).sum()) if len(serie) else 0
        qualidade_linhas.append({
            "campo": label,
            "coluna": col,
            "preenchidos": preenchidos,
            "municipios_com_valor_maior_que_zero": positivos,
            "cobertura_%": round(preenchidos / total * 100, 1),
            "status": "OK" if preenchidos > 0 else "Pendente",
        })
    qualidade = pd.DataFrame(qualidade_linhas)

    return {"resumo": resumo, "municipios": municipios, "agregacao": agregacao, "qualidade": qualidade, "indicadores": ind[ind["indicador"].astype(str).str.lower().isin(TODOS)].copy() if not ind.empty and "indicador" in ind.columns else pd.DataFrame()}
