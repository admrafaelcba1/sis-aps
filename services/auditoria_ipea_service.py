from __future__ import annotations

import pandas as pd

from database.connection import db_session


def montar_visao_ipea() -> dict:
    with db_session() as conn:
        try:
            df = pd.read_sql_query(
                """
                SELECT municipio, ano, competencia, indicador, valor, fonte, atualizado_em
                  FROM indicadores_municipais
                 WHERE indicador LIKE 'ipea_%'
                    OR fonte LIKE '%IPEA%'
                    OR fonte LIKE '%IVS%'
                """,
                conn,
            )
        except Exception:
            df = pd.DataFrame()

    if df.empty:
        return {
            "resumo": {"municipios_com_ivs": 0, "indicadores": 0, "ano_mais_recente": "—", "registros": 0},
            "dados": df,
            "tabela_municipal": pd.DataFrame(),
            "qualidade": pd.DataFrame(),
        }

    df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
    tabela = df.pivot_table(index=["municipio"], columns="indicador", values="valor", aggfunc="max").reset_index()
    tabela.columns = [str(c) for c in tabela.columns]
    qualidade = (
        df.groupby("indicador")
        .agg(municipios=("municipio", "nunique"), registros=("valor", "size"), valores_preenchidos=("valor", lambda s: int(s.notna().sum())), ano_min=("ano", "min"), ano_max=("ano", "max"))
        .reset_index()
    )
    resumo = {
        "municipios_com_ivs": int(df["municipio"].nunique()),
        "indicadores": int(df["indicador"].nunique()),
        "ano_mais_recente": int(pd.to_numeric(df["ano"], errors="coerce").max()) if pd.to_numeric(df["ano"], errors="coerce").notna().any() else "—",
        "registros": int(len(df)),
    }
    return {"resumo": resumo, "dados": df, "tabela_municipal": tabela, "qualidade": qualidade}
