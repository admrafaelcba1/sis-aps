from __future__ import annotations

import pandas as pd

from database.connection import get_connection


def _read_sql_safe(conn, query: str) -> pd.DataFrame:
    try:
        return pd.read_sql_query(query, conn)
    except Exception:
        return pd.DataFrame()


def _to_num(series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _status_leitos(row) -> str:
    leitos = row.get("total_leitos_sus", 0)
    pop = row.get("populacao", 0)
    try:
        leitos = float(leitos) if pd.notna(leitos) else 0.0
    except Exception:
        leitos = 0.0
    try:
        pop = float(pop) if pd.notna(pop) else 0.0
    except Exception:
        pop = 0.0

    if leitos <= 0 and pop >= 50000:
        return "ALERTA: município de maior porte sem leitos na base"
    if leitos > 0 and pop > 0 and (leitos / pop * 10000) > 80:
        return "ALERTA: leitos por 10 mil hab. muito alto"
    if 0 < leitos <= 5 and pop >= 100000:
        return "ALERTA: valor muito baixo para município de grande porte"
    return "OK"


def montar_auditoria_leitos() -> dict:
    """Monta uma auditoria interna da camada de leitos.

    Esta função não busca nova fonte externa. Ela usa o que já foi importado
    para o banco novo e ajuda a decidir se a camada pode ser usada ou precisa
    ser corrigida antes de aparecer em dashboards oficiais.
    """
    with get_connection() as conn:
        base = _read_sql_safe(conn, "SELECT * FROM base_municipal_consolidada")
        indicadores = _read_sql_safe(conn, "SELECT * FROM indicadores_municipais")

    if base.empty:
        return {
            "resumo": {
                "total_leitos_consolidado": 0,
                "municipios_com_leitos": 0,
                "municipios_sem_leitos": 0,
                "leitos_por_10mil": None,
            },
            "por_municipio": pd.DataFrame(),
            "indicadores_leitos": pd.DataFrame(),
            "agregados_por_indicador": pd.DataFrame(),
            "alertas": ["A base consolidada está vazia. Gere a consolidação antes de auditar leitos."],
        }

    out = base.copy()
    if "total_leitos_sus" not in out.columns:
        out["total_leitos_sus"] = pd.NA
    if "populacao" not in out.columns:
        out["populacao"] = pd.NA

    out["total_leitos_sus"] = _to_num(out["total_leitos_sus"]).fillna(0)
    out["populacao"] = _to_num(out["populacao"])
    out["leitos_por_10mil_hab"] = (out["total_leitos_sus"] / out["populacao"] * 10000).where(out["populacao"] > 0)
    out["status_auditoria"] = out.apply(_status_leitos, axis=1)

    cols = [c for c in [
        "codigo_ibge", "municipio", "regiao_saude", "populacao", "total_leitos_sus",
        "leitos_por_10mil_hab", "total_ubs", "total_equipes_aps", "status_auditoria",
    ] if c in out.columns]
    por_municipio = out[cols].copy().sort_values("total_leitos_sus", ascending=False)

    total_leitos = float(out["total_leitos_sus"].sum(skipna=True))
    total_pop = float(out["populacao"].sum(skipna=True)) if out["populacao"].notna().any() else 0
    resumo = {
        "total_leitos_consolidado": int(round(total_leitos)),
        "municipios_com_leitos": int((out["total_leitos_sus"] > 0).sum()),
        "municipios_sem_leitos": int((out["total_leitos_sus"] <= 0).sum()),
        "leitos_por_10mil": (total_leitos / total_pop * 10000) if total_pop > 0 else None,
    }

    indicadores_leitos = pd.DataFrame()
    agregados = pd.DataFrame()
    if not indicadores.empty and "indicador" in indicadores.columns:
        indicadores_validos = {
            "leitos_sus_total",
            "leitos_existentes_total",
            "qtd_hospitais_leitos",
            "registros_leitos_cnes",
        }
        mask = indicadores["indicador"].astype(str).str.lower().isin(indicadores_validos)
        indicadores_leitos = indicadores.loc[mask].copy()
        if not indicadores_leitos.empty:
            indicadores_leitos["valor"] = _to_num(indicadores_leitos.get("valor"))
            keep = [c for c in ["municipio", "ano", "competencia", "indicador", "valor", "fonte", "importacao_id", "atualizado_em"] if c in indicadores_leitos.columns]
            indicadores_leitos = indicadores_leitos[keep].sort_values(["indicador", "municipio"])
            agregados = (
                indicadores_leitos.groupby("indicador", dropna=False)
                .agg(
                    registros=("valor", "size"),
                    municipios=("municipio", "nunique"),
                    total_valor=("valor", "sum"),
                    min_valor=("valor", "min"),
                    max_valor=("valor", "max"),
                )
                .reset_index()
                .sort_values("total_valor", ascending=False)
            )

    alertas = []
    if resumo["total_leitos_consolidado"] <= 0:
        alertas.append("A camada de leitos está zerada na base consolidada.")
    if (out["status_auditoria"].astype(str) != "OK").any():
        qtd = int((out["status_auditoria"].astype(str) != "OK").sum())
        alertas.append(f"Foram encontrados {qtd} município(s) com alerta de consistência na camada de leitos.")
    if indicadores_leitos.empty:
        alertas.append("Não foram encontrados indicadores oficiais de leitos agregados em indicadores_municipais. Recarregue o bloco CNES/DATASUS após aplicar a correção.")
    elif agregados.shape[0] > 1:
        alertas.append("Há mais de um indicador de leitos na origem. O campo oficial do dashboard deve usar leitos_sus_total; os demais são auxiliares de auditoria.")

    return {
        "resumo": resumo,
        "por_municipio": por_municipio,
        "indicadores_leitos": indicadores_leitos,
        "agregados_por_indicador": agregados,
        "alertas": alertas,
    }
