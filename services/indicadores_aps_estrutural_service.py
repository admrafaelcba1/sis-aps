from __future__ import annotations

import math
from typing import Dict, List, Tuple

import pandas as pd

from database.queries import read_table

CODIGOS_EQUIPES_APS: Dict[str, str] = {
    "70": "eSF",
    "71": "eSB",
    "72": "eNASF-AP",
    "73": "eCR",
    "74": "eAPP prisional",
    "76": "eAP",
}

COLUNAS_BASE = [
    "codigo_ibge",
    "municipio",
    "regiao_saude",
    "populacao",
    "total_ubs",
    "total_equipes_aps",
    "total_profissionais_aps",
    "indice_vulnerabilidade",
    "nivel_prioridade",
    "renda_censo_2022",
    "saneamento_censo_2022",
    "cadunico_familias_pobreza",
    "bolsa_familia_familias",
    "bpc_total",
    "latitude",
    "longitude",
]


def _num(valor, default: float = 0.0) -> float:
    try:
        if valor is None:
            return default
        if isinstance(valor, str) and not valor.strip():
            return default
        out = float(valor)
        if math.isnan(out):
            return default
        return out
    except Exception:
        return default


def _serie_num(s: pd.Series, default: float = 0.0) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(default)


def _div_segura(numerador: pd.Series, denominador: pd.Series) -> pd.Series:
    numerador = _serie_num(numerador)
    denominador = _serie_num(denominador)
    return numerador.divide(denominador.where(denominador != 0)).replace([float("inf"), -float("inf")], pd.NA)


def _classificar_linha(row: pd.Series) -> Tuple[str, str]:
    """Classificação gerencial inicial da estrutura APS.

    A regra abaixo não substitui norma oficial de cobertura. Ela serve para
    triagem visual: identifica municípios que merecem conferência técnica antes
    de cruzar com SISAB/e-Gestor.
    """
    pop = _num(row.get("populacao"))
    ubs = int(_num(row.get("total_ubs")))
    equipes = int(_num(row.get("total_equipes_aps")))
    profissionais = int(_num(row.get("total_profissionais_aps")))
    pop_por_equipe = _num(row.get("populacao_por_equipe"), default=0)
    prof_por_equipe = _num(row.get("profissionais_por_equipe"), default=0)
    vulnerabilidade = _num(row.get("indice_vulnerabilidade"), default=0)

    alertas: List[str] = []
    pontos = 100

    if pop > 0 and equipes == 0:
        alertas.append("sem equipe APS CNES/INE")
        pontos -= 45
    elif equipes > 0:
        if pop_por_equipe > 4000:
            alertas.append("população por equipe elevada")
            pontos -= 25
        elif pop_por_equipe > 3500:
            alertas.append("população por equipe em atenção")
            pontos -= 12

    if ubs == 0:
        alertas.append("sem UBS/estabelecimento APS na base")
        pontos -= 30

    if equipes > 0 and profissionais == 0:
        alertas.append("sem vínculos profissionais nas equipes")
        pontos -= 25
    elif equipes > 0 and prof_por_equipe < 3:
        alertas.append("baixa média de vínculos por equipe")
        pontos -= 12
    elif equipes > 0 and prof_por_equipe > 35:
        alertas.append("média muito alta; verificar duplicidade/critério")
        pontos -= 8

    if vulnerabilidade >= 70:
        alertas.append("alta vulnerabilidade associada")
        pontos -= 10
    elif vulnerabilidade >= 50:
        alertas.append("vulnerabilidade relevante")
        pontos -= 5

    if pontos < 45:
        classe = "Crítica"
    elif pontos < 65:
        classe = "Alta atenção"
    elif pontos < 80:
        classe = "Atenção"
    else:
        classe = "Adequada para monitoramento"

    return classe, "; ".join(alertas) if alertas else "Sem alerta estrutural crítico nos critérios atuais"


def carregar_aps_estrutural() -> pd.DataFrame:
    base = read_table("base_municipal_consolidada")
    if base.empty:
        return pd.DataFrame()

    colunas = [c for c in COLUNAS_BASE if c in base.columns]
    out = base[colunas].drop_duplicates(subset=["municipio"]).copy()

    for codigo in CODIGOS_EQUIPES_APS:
        col = f"total_equipes_{codigo}"
        if col in base.columns and col not in out.columns:
            out[col] = base[col]
        elif col not in out.columns:
            out[col] = 0

    for col in ["populacao", "total_ubs", "total_equipes_aps", "total_profissionais_aps", "indice_vulnerabilidade"] + [f"total_equipes_{c}" for c in CODIGOS_EQUIPES_APS]:
        if col not in out.columns:
            out[col] = 0
        out[col] = _serie_num(out[col])

    out["populacao_por_equipe"] = _div_segura(out["populacao"], out["total_equipes_aps"])
    out["populacao_por_ubs"] = _div_segura(out["populacao"], out["total_ubs"])
    out["profissionais_por_equipe"] = _div_segura(out["total_profissionais_aps"], out["total_equipes_aps"])
    out["equipes_por_10mil_hab"] = _div_segura(out["total_equipes_aps"] * 10000, out["populacao"])
    out["ubs_por_10mil_hab"] = _div_segura(out["total_ubs"] * 10000, out["populacao"])

    classes = out.apply(_classificar_linha, axis=1, result_type="expand")
    out["classificacao_estrutural"] = classes[0]
    out["alertas_estruturais"] = classes[1]

    ordem = {
        "Crítica": 0,
        "Alta atenção": 1,
        "Atenção": 2,
        "Adequada para monitoramento": 3,
    }
    out["ordem_classificacao"] = out["classificacao_estrutural"].map(ordem).fillna(9)
    out = out.sort_values(["ordem_classificacao", "populacao_por_equipe", "total_equipes_aps"], ascending=[True, False, True])
    return out.drop(columns=["ordem_classificacao"], errors="ignore")


def resumo_executivo(df: pd.DataFrame | None = None) -> Dict[str, float]:
    if df is None:
        df = carregar_aps_estrutural()
    if df.empty:
        return {
            "municipios": 0,
            "populacao": 0,
            "ubs": 0,
            "equipes": 0,
            "profissionais": 0,
            "sem_equipes": 0,
            "sem_ubs": 0,
            "criticos": 0,
            "pop_por_equipe": 0,
            "prof_por_equipe": 0,
        }

    equipes = _num(df["total_equipes_aps"].sum())
    profissionais = _num(df["total_profissionais_aps"].sum())
    populacao = _num(df["populacao"].sum())
    return {
        "municipios": int(df["municipio"].nunique()) if "municipio" in df.columns else int(len(df)),
        "populacao": populacao,
        "ubs": int(_num(df["total_ubs"].sum())),
        "equipes": int(equipes),
        "profissionais": int(profissionais),
        "sem_equipes": int((df["total_equipes_aps"] <= 0).sum()),
        "sem_ubs": int((df["total_ubs"] <= 0).sum()),
        "criticos": int(df["classificacao_estrutural"].isin(["Crítica", "Alta atenção"]).sum()),
        "pop_por_equipe": round(populacao / equipes, 1) if equipes else 0,
        "prof_por_equipe": round(profissionais / equipes, 1) if equipes else 0,
    }


def resumo_regional(df: pd.DataFrame | None = None) -> pd.DataFrame:
    if df is None:
        df = carregar_aps_estrutural()
    if df.empty or "regiao_saude" not in df.columns:
        return pd.DataFrame()

    grupos = df.copy()
    grupos["municipio_critico"] = grupos["classificacao_estrutural"].isin(["Crítica", "Alta atenção"]).astype(int)
    out = grupos.groupby("regiao_saude", dropna=False).agg(
        municipios=("municipio", "nunique"),
        populacao=("populacao", "sum"),
        ubs=("total_ubs", "sum"),
        equipes_aps=("total_equipes_aps", "sum"),
        profissionais_aps=("total_profissionais_aps", "sum"),
        municipios_criticos=("municipio_critico", "sum"),
    ).reset_index()
    out["populacao_por_equipe"] = _div_segura(out["populacao"], out["equipes_aps"]).round(1)
    out["profissionais_por_equipe"] = _div_segura(out["profissionais_aps"], out["equipes_aps"]).round(1)
    return out.sort_values(["municipios_criticos", "populacao_por_equipe"], ascending=[False, False])


def detalhar_municipio(municipio: str) -> Dict[str, object]:
    df = carregar_aps_estrutural()
    if df.empty or not municipio:
        return {"linha": {}, "tabela_equipes": pd.DataFrame(), "tabela_profissionais": pd.DataFrame()}

    linha_df = df[df["municipio"].astype(str).eq(str(municipio))]
    linha = linha_df.iloc[0].to_dict() if not linha_df.empty else {}

    equipes = read_table("equipes_aps")
    profissionais = read_table("profissionais_cnes")

    if not equipes.empty and "municipio" in equipes.columns:
        equipes = equipes[equipes["municipio"].astype(str).eq(str(municipio))].copy()
    else:
        equipes = pd.DataFrame()

    if not profissionais.empty and "municipio" in profissionais.columns:
        profissionais = profissionais[profissionais["municipio"].astype(str).eq(str(municipio))].copy()
    else:
        profissionais = pd.DataFrame()

    if not equipes.empty and "codigo_tipo_equipe" in equipes.columns:
        equipes["codigo_tipo_equipe"] = equipes["codigo_tipo_equipe"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(2).str[-2:]
        equipes_resumo = equipes.groupby(["codigo_tipo_equipe", "tipo_equipe"], dropna=False).agg(
            equipes=("ine", "nunique"),
            estabelecimentos=("cnes", "nunique"),
        ).reset_index().sort_values("codigo_tipo_equipe")
    else:
        equipes_resumo = pd.DataFrame()

    if not profissionais.empty and "codigo_tipo_equipe" in profissionais.columns:
        profissionais["codigo_tipo_equipe"] = profissionais["codigo_tipo_equipe"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(2).str[-2:]
        profissionais_resumo = profissionais.groupby(["codigo_tipo_equipe", "tipo_equipe"], dropna=False).agg(
            vinculos_profissionais=("nome_profissional", "count"),
            cbo_distintos=("cbo", "nunique"),
        ).reset_index().sort_values("codigo_tipo_equipe")
    else:
        profissionais_resumo = pd.DataFrame()

    return {"linha": linha, "tabela_equipes": equipes_resumo, "tabela_profissionais": profissionais_resumo}
