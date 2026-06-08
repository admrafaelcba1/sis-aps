from __future__ import annotations

import numpy as np
import pandas as pd

from database.queries import read_table

try:
    from services.auditoria_inep_service import montar_visao_inep
except Exception:  # mantém compatibilidade se a camada INEP ainda não existir
    montar_visao_inep = None


COLUNAS_BASE = [
    "id", "codigo_ibge", "municipio", "regiao_saude", "populacao", "area_km2",
    "densidade_hab_km2", "latitude", "longitude", "total_ubs", "total_equipes_aps",
    "total_profissionais_aps", "total_leitos_sus", "nascidos_vivos", "obitos", "obitos_infantis",
    "total_equipes_70", "total_equipes_71", "total_equipes_72", "total_equipes_73", "total_equipes_74", "total_equipes_76",
]

COLUNAS_INEP = [
    "escolas_total", "escolas_urbanas", "escolas_rurais", "escolas_indigenas", "escolas_quilombolas",
    "escolas_educacao_especial_aee", "matriculas_total", "matriculas_educacao_especial",
]


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([np.nan] * len(df), index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce")


def _div(numerador: pd.Series, denominador: pd.Series) -> pd.Series:
    den = pd.to_numeric(denominador, errors="coerce").replace({0: np.nan})
    num = pd.to_numeric(numerador, errors="coerce")
    return num / den


def _classificar_pressao(row: pd.Series) -> str:
    pop = row.get("populacao")
    equipes = row.get("total_equipes_aps")
    ubs = row.get("total_ubs")
    pop_equipe = row.get("pop_por_equipe_aps")
    pop_ubs = row.get("pop_por_ubs")

    pop = 0 if pd.isna(pop) else float(pop)
    equipes = 0 if pd.isna(equipes) else float(equipes)
    ubs = 0 if pd.isna(ubs) else float(ubs)

    if pop <= 0:
        return "Sem população informada"
    if equipes <= 0 and ubs <= 0:
        return "Crítica — sem UBS e sem equipes"
    if equipes <= 0:
        return "Crítica — sem equipes APS/INE"
    if ubs <= 0:
        return "Alta — sem UBS/estabelecimento APS"

    if pd.notna(pop_equipe) and pop_equipe > 4500:
        return "Alta — população por equipe elevada"
    if pd.notna(pop_ubs) and pop_ubs > 12000:
        return "Alta — população por UBS elevada"
    if pd.notna(pop_equipe) and pop_equipe > 3500:
        return "Monitoramento — população por equipe acima do parâmetro"
    if pd.notna(pop_ubs) and pop_ubs > 8000:
        return "Monitoramento — população por UBS acima do parâmetro"
    return "Adequada/monitoramento regular"


def _anexar_inep(out: pd.DataFrame) -> pd.DataFrame:
    """Anexa os indicadores municipais do INEP já importados, sem exigir nova carga.

    A camada INEP permanece como auditoria/staging. Estes indicadores derivados servem para leitura territorial,
    não para índice oficial de vulnerabilidade.
    """
    if montar_visao_inep is None or out.empty or "municipio" not in out.columns:
        return out
    try:
        visao = montar_visao_inep().get("municipios", pd.DataFrame())
    except Exception:
        return out
    if visao is None or visao.empty or "municipio" not in visao.columns:
        return out

    cols = ["municipio"] + [c for c in COLUNAS_INEP if c in visao.columns]
    if len(cols) <= 1:
        return out

    inep = visao[cols].drop_duplicates(subset=["municipio"]).copy()
    for c in COLUNAS_INEP:
        if c in inep.columns:
            inep[c] = pd.to_numeric(inep[c], errors="coerce").fillna(0)

    # evita sobrescrever eventual coluna já existente na base principal
    for c in COLUNAS_INEP:
        if c in out.columns:
            out = out.drop(columns=[c])
    return out.merge(inep, on="municipio", how="left")


def montar_indicadores_derivados() -> pd.DataFrame:
    """Calcula indicadores derivados a partir da base municipal consolidada.

    Esta camada não cria nova fonte externa: ela transforma as bases já carregadas em medidas comparáveis.
    Os cálculos usam apenas campos existentes na `base_municipal_consolidada` e, quando disponível,
    a camada INEP importada em `indicadores_municipais`.
    """
    base = read_table("base_municipal_consolidada")
    if base.empty:
        return base

    out = base.copy()
    for col in COLUNAS_BASE:
        if col not in out.columns:
            out[col] = np.nan

    out = _anexar_inep(out)

    numericas = [
        "populacao", "area_km2", "densidade_hab_km2", "total_ubs", "total_equipes_aps",
        "total_profissionais_aps", "total_leitos_sus", "nascidos_vivos", "obitos", "obitos_infantis",
        "total_equipes_70", "total_equipes_71", "total_equipes_72", "total_equipes_73", "total_equipes_74", "total_equipes_76",
    ] + COLUNAS_INEP
    for col in numericas:
        if col in out.columns:
            out[col] = _num(out, col)

    pop = out["populacao"]
    out["pop_por_equipe_aps"] = _div(pop, out["total_equipes_aps"]).round(2)
    out["pop_por_ubs"] = _div(pop, out["total_ubs"]).round(2)
    out["profissionais_por_equipe"] = _div(out["total_profissionais_aps"], out["total_equipes_aps"]).round(2)

    out["equipes_aps_por_10mil_hab"] = (_div(out["total_equipes_aps"], pop) * 10000).round(2)
    out["ubs_por_10mil_hab"] = (_div(out["total_ubs"], pop) * 10000).round(2)
    out["profissionais_por_10mil_hab"] = (_div(out["total_profissionais_aps"], pop) * 10000).round(2)
    out["leitos_sus_por_10mil_hab"] = (_div(out["total_leitos_sus"], pop) * 10000).round(2)
    out["nascidos_vivos_por_1000_hab"] = (_div(out["nascidos_vivos"], pop) * 1000).round(2)
    out["obitos_por_1000_hab"] = (_div(out["obitos"], pop) * 1000).round(2)
    out["mortalidade_infantil_por_1000_nv"] = (_div(out["obitos_infantis"], out["nascidos_vivos"]) * 1000).round(2)

    if "escolas_total" in out.columns:
        out["escolas_por_10mil_hab"] = (_div(out["escolas_total"], pop) * 10000).round(2)
        out["matriculas_por_1000_hab"] = (_div(out.get("matriculas_total", pd.Series(index=out.index, dtype=float)), pop) * 1000).round(2)
        out["matriculas_por_escola"] = _div(out.get("matriculas_total", pd.Series(index=out.index, dtype=float)), out["escolas_total"]).round(2)
        out["escolas_rurais_por_10mil_hab"] = (_div(out.get("escolas_rurais", pd.Series(index=out.index, dtype=float)), pop) * 10000).round(2)
        out["perc_escolas_rurais"] = (_div(out.get("escolas_rurais", pd.Series(index=out.index, dtype=float)), out["escolas_total"]) * 100).round(2)
        out["perc_escolas_indigenas"] = (_div(out.get("escolas_indigenas", pd.Series(index=out.index, dtype=float)), out["escolas_total"]) * 100).round(2)
        out["perc_escolas_quilombolas"] = (_div(out.get("escolas_quilombolas", pd.Series(index=out.index, dtype=float)), out["escolas_total"]) * 100).round(2)

    out["sem_equipe_aps"] = out["total_equipes_aps"].fillna(0) <= 0
    out["sem_ubs"] = out["total_ubs"].fillna(0) <= 0
    out["sem_coordenada"] = out["latitude"].isna() | out["longitude"].isna()
    out["classificacao_pressao_aps"] = out.apply(_classificar_pressao, axis=1)

    ordem = [
        "codigo_ibge", "municipio", "regiao_saude", "populacao", "total_ubs", "total_equipes_aps", "total_profissionais_aps",
        "pop_por_equipe_aps", "pop_por_ubs", "profissionais_por_equipe",
        "equipes_aps_por_10mil_hab", "ubs_por_10mil_hab", "profissionais_por_10mil_hab", "leitos_sus_por_10mil_hab",
        "nascidos_vivos_por_1000_hab", "obitos_por_1000_hab", "mortalidade_infantil_por_1000_nv",
        "escolas_total", "escolas_urbanas", "escolas_rurais", "escolas_indigenas", "escolas_quilombolas", "escolas_educacao_especial_aee",
        "matriculas_total", "matriculas_educacao_especial", "escolas_por_10mil_hab", "escolas_rurais_por_10mil_hab",
        "perc_escolas_rurais", "perc_escolas_indigenas", "perc_escolas_quilombolas", "matriculas_por_escola", "matriculas_por_1000_hab",
        "sem_equipe_aps", "sem_ubs", "sem_coordenada", "classificacao_pressao_aps",
        "total_equipes_70", "total_equipes_71", "total_equipes_72", "total_equipes_73", "total_equipes_74", "total_equipes_76",
        "latitude", "longitude", "area_km2", "densidade_hab_km2",
    ]
    extras = [c for c in out.columns if c not in ordem]
    return out[[c for c in ordem if c in out.columns] + extras]


def resumo_indicadores_derivados(df: pd.DataFrame | None = None) -> dict:
    if df is None:
        df = montar_indicadores_derivados()
    if df is None or df.empty:
        return {
            "municipios": 0,
            "sem_equipe": 0,
            "sem_ubs": 0,
            "pop_media_por_equipe": np.nan,
            "pop_media_por_ubs": np.nan,
            "mortalidade_infantil": np.nan,
            "escolas_total": 0,
        }

    return {
        "municipios": int(len(df)),
        "sem_equipe": int(df.get("sem_equipe_aps", pd.Series(dtype=bool)).fillna(False).sum()),
        "sem_ubs": int(df.get("sem_ubs", pd.Series(dtype=bool)).fillna(False).sum()),
        "pop_media_por_equipe": float(pd.to_numeric(df.get("pop_por_equipe_aps"), errors="coerce").replace([np.inf, -np.inf], np.nan).mean()),
        "pop_media_por_ubs": float(pd.to_numeric(df.get("pop_por_ubs"), errors="coerce").replace([np.inf, -np.inf], np.nan).mean()),
        "mortalidade_infantil": float(_div(_num(df, "obitos_infantis").sum(), pd.Series([_num(df, "nascidos_vivos").sum()])).iloc[0] * 1000) if _num(df, "nascidos_vivos").sum() else np.nan,
        "escolas_total": int(pd.to_numeric(df.get("escolas_total", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()),
    }


def ranking_prioridades_derivadas(df: pd.DataFrame | None = None, limite: int = 30) -> pd.DataFrame:
    if df is None:
        df = montar_indicadores_derivados()
    if df.empty:
        return df

    out = df.copy()
    out["_score"] = 0
    out.loc[out["sem_equipe_aps"].fillna(False), "_score"] += 100
    out.loc[out["sem_ubs"].fillna(False), "_score"] += 80
    out["_score"] += pd.to_numeric(out["pop_por_equipe_aps"], errors="coerce").fillna(0) / 100
    out["_score"] += pd.to_numeric(out["pop_por_ubs"], errors="coerce").fillna(0) / 200
    # reforço leve para dispersão territorial educacional, sem transformar em índice oficial
    if "perc_escolas_rurais" in out.columns:
        out["_score"] += pd.to_numeric(out["perc_escolas_rurais"], errors="coerce").fillna(0) / 10
    if "escolas_indigenas" in out.columns:
        out["_score"] += (pd.to_numeric(out["escolas_indigenas"], errors="coerce").fillna(0) > 0).astype(int) * 5
    if "escolas_quilombolas" in out.columns:
        out["_score"] += (pd.to_numeric(out["escolas_quilombolas"], errors="coerce").fillna(0) > 0).astype(int) * 5

    cols = [
        "municipio", "regiao_saude", "populacao", "total_ubs", "total_equipes_aps", "total_profissionais_aps",
        "pop_por_equipe_aps", "pop_por_ubs", "profissionais_por_equipe",
        "escolas_total", "escolas_rurais", "perc_escolas_rurais", "escolas_indigenas", "escolas_quilombolas",
        "classificacao_pressao_aps",
    ]
    return out.sort_values("_score", ascending=False)[[c for c in cols if c in out.columns]].head(limite)
