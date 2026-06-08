
from __future__ import annotations

import math
import pandas as pd

from services.dashboard_aps_service import carregar_base_dashboard


def _num(v, default: float = 0.0) -> float:
    try:
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def parametro_pessoas_por_esf(populacao: float) -> int:
    """Parâmetro populacional vigente usado pelo MS para pessoas vinculadas por eSF.

    Fonte normativa/metodológica: Portaria GM/MS nº 3.493/2024 / FAQ SAPS-MS.
    """
    pop = _num(populacao)
    if pop <= 20000:
        return 2000
    if pop <= 50000:
        return 2500
    if pop <= 100000:
        return 2750
    return 3000


def faixa_porte_ms(populacao: float) -> str:
    pop = _num(populacao)
    if pop <= 20000:
        return "Até 20 mil hab."
    if pop <= 50000:
        return "20.001 a 50 mil hab."
    if pop <= 100000:
        return "50.001 a 100 mil hab."
    return "Acima de 100 mil hab."


def calcular_parametros_ms(capacidade_equipes_por_ubs: int = 4) -> pd.DataFrame:
    """Calcula estimativa de equipes e UBS necessárias por município.

    Observações:
    - Equipes necessárias: população / parâmetro de pessoas vinculadas por eSF.
    - UBS necessárias: equipes necessárias / capacidade média de equipes por UBS.
    - A leitura é gerencial e estimativa; não substitui planejamento físico, obras, rotas,
      adscrição real, capacidade instalada da unidade ou validação técnica.
    """
    base = carregar_base_dashboard().copy()
    if base.empty:
        return pd.DataFrame()

    capacidade = max(1, int(capacidade_equipes_por_ubs or 4))
    pop_col = "populacao" if "populacao" in base.columns else "populacao_total"
    if pop_col not in base.columns:
        base["populacao"] = 0
        pop_col = "populacao"

    # Busca colunas prováveis.
    equipes_col = next((c for c in ["total_equipes_aps", "equipes_aps", "total_equipes", "equipes"] if c in base.columns), None)
    ubs_col = next((c for c in ["total_ubs", "ubs", "total_estabelecimentos_aps"] if c in base.columns), None)

    out = pd.DataFrame()
    out["municipio"] = base.get("municipio", "")
    out["regiao_saude"] = base.get("regiao_saude", "")
    out["populacao"] = pd.to_numeric(base.get(pop_col, 0), errors="coerce").fillna(0).astype(float)
    out["faixa_porte_ms"] = out["populacao"].apply(faixa_porte_ms)
    out["parametro_pessoas_por_esf"] = out["populacao"].apply(parametro_pessoas_por_esf)
    out["equipes_aps_existentes"] = pd.to_numeric(base.get(equipes_col, 0), errors="coerce").fillna(0).astype(float) if equipes_col else 0
    out["ubs_existentes"] = pd.to_numeric(base.get(ubs_col, 0), errors="coerce").fillna(0).astype(float) if ubs_col else 0

    out["equipes_esf_necessarias_estimadas"] = out.apply(
        lambda r: max(1, int(math.ceil(r["populacao"] / r["parametro_pessoas_por_esf"]))) if r["populacao"] > 0 else 0,
        axis=1,
    )
    out["deficit_estimado_equipes"] = (out["equipes_esf_necessarias_estimadas"] - out["equipes_aps_existentes"]).clip(lower=0).round(0).astype(int)
    out["saldo_estimado_equipes"] = (out["equipes_aps_existentes"] - out["equipes_esf_necessarias_estimadas"]).round(0).astype(int)

    out["capacidade_media_equipes_por_ubs_adotada"] = capacidade
    out["ubs_necessarias_estimadas"] = out["equipes_esf_necessarias_estimadas"].apply(lambda x: int(math.ceil(x / capacidade)) if x > 0 else 0)
    out["deficit_estimado_ubs"] = (out["ubs_necessarias_estimadas"] - out["ubs_existentes"]).clip(lower=0).round(0).astype(int)
    out["saldo_estimado_ubs"] = (out["ubs_existentes"] - out["ubs_necessarias_estimadas"]).round(0).astype(int)

    out["populacao_por_equipe_existente"] = out.apply(
        lambda r: round(r["populacao"] / r["equipes_aps_existentes"], 1) if r["equipes_aps_existentes"] > 0 else pd.NA,
        axis=1,
    )
    out["populacao_por_ubs_existente"] = out.apply(
        lambda r: round(r["populacao"] / r["ubs_existentes"], 1) if r["ubs_existentes"] > 0 else pd.NA,
        axis=1,
    )

    def leitura(r):
        de = int(r["deficit_estimado_equipes"])
        du = int(r["deficit_estimado_ubs"])
        if de > 0 and du > 0:
            return "Déficit estimado de equipes e possível déficit de UBS"
        if de > 0 and du <= 0:
            return "Déficit estimado de equipes; estrutura física pode ser suficiente em termos agregados"
        if de <= 0 and du > 0:
            return "Possível déficit de UBS pela capacidade média adotada; verificar distribuição territorial"
        return "Sem déficit agregado estimado pelos parâmetros adotados"

    def prioridade(r):
        de = int(r["deficit_estimado_equipes"])
        du = int(r["deficit_estimado_ubs"])
        if de >= 10 or du >= 3:
            return "Alta prioridade de análise"
        if de > 0 or du > 0:
            return "Atenção técnica"
        return "Monitoramento"

    out["leitura_parametro_ms"] = out.apply(leitura, axis=1)
    out["prioridade_parametro_ms"] = out.apply(prioridade, axis=1)

    return out.sort_values(["deficit_estimado_ubs", "deficit_estimado_equipes", "populacao"], ascending=[False, False, False]).reset_index(drop=True)


def resumo_parametros_ms(capacidade_equipes_por_ubs: int = 4) -> dict:
    df = calcular_parametros_ms(capacidade_equipes_por_ubs)
    if df.empty:
        return {
            "municipios": 0,
            "deficit_total_equipes": 0,
            "deficit_total_ubs": 0,
            "municipios_com_deficit_equipes": 0,
            "municipios_com_deficit_ubs": 0,
            "base": df,
        }
    return {
        "municipios": int(len(df)),
        "deficit_total_equipes": int(df["deficit_estimado_equipes"].sum()),
        "deficit_total_ubs": int(df["deficit_estimado_ubs"].sum()),
        "municipios_com_deficit_equipes": int((df["deficit_estimado_equipes"] > 0).sum()),
        "municipios_com_deficit_ubs": int((df["deficit_estimado_ubs"] > 0).sum()),
        "base": df,
    }


def classificar_tipo_deficit_parametro(row: pd.Series) -> str:
    de = int(_num(row.get("deficit_estimado_equipes")))
    du = int(_num(row.get("deficit_estimado_ubs")))
    if de > 0 and du > 0:
        return "Déficit de equipes e UBS"
    if de > 0 and du <= 0:
        return "Déficit de equipes"
    if de <= 0 and du > 0:
        return "Déficit de UBS"
    return "Sem déficit agregado"


def gerar_sintese_gerencial_parametros(row: pd.Series) -> str:
    municipio = str(row.get("municipio", "Município"))
    ubs_exist = int(_num(row.get("ubs_existentes")))
    ubs_nec = int(_num(row.get("ubs_necessarias_estimadas")))
    def_ubs = int(_num(row.get("deficit_estimado_ubs")))
    eq_exist = int(_num(row.get("equipes_aps_existentes")))
    eq_nec = int(_num(row.get("equipes_esf_necessarias_estimadas")))
    def_eq = int(_num(row.get("deficit_estimado_equipes")))
    tipo = classificar_tipo_deficit_parametro(row)

    if tipo == "Déficit de equipes e UBS":
        encaminhamento = "avaliar simultaneamente expansão/reorganização de equipes e possível ampliação física da rede."
    elif tipo == "Déficit de equipes":
        encaminhamento = "priorizar análise de provimento/reorganização de equipes antes de indicar nova UBS."
    elif tipo == "Déficit de UBS":
        encaminhamento = "avaliar capacidade física, distribuição territorial e possível necessidade de nova unidade ou readequação estrutural."
    else:
        encaminhamento = "manter monitoramento; não há déficit agregado estimado pelos parâmetros adotados."

    return (
        f"{municipio}: possui {ubs_exist} UBS e {eq_exist} equipes APS. "
        f"Pelos parâmetros adotados, a estimativa é de {ubs_nec} UBS e {eq_nec} equipes, "
        f"resultando em déficit estimado de {def_ubs} UBS e {def_eq} equipes. "
        f"Leitura: {encaminhamento}"
    )


def calcular_parametros_ms_gerencial(capacidade_equipes_por_ubs: int = 1) -> pd.DataFrame:
    df = calcular_parametros_ms(capacidade_equipes_por_ubs).copy()
    if df.empty:
        return df
    df["tipo_deficit_parametro_ms"] = df.apply(classificar_tipo_deficit_parametro, axis=1)
    df["sintese_gerencial_parametro_ms"] = df.apply(gerar_sintese_gerencial_parametros, axis=1)
    df["ranking_deficit_geral"] = (
        pd.to_numeric(df["deficit_estimado_ubs"], errors="coerce").fillna(0) * 10
        + pd.to_numeric(df["deficit_estimado_equipes"], errors="coerce").fillna(0)
    )
    return df.sort_values(["ranking_deficit_geral", "populacao"], ascending=[False, False]).reset_index(drop=True)


def resumo_gerencial_parametros_ms(capacidade_equipes_por_ubs: int = 1) -> dict:
    df = calcular_parametros_ms_gerencial(capacidade_equipes_por_ubs)
    if df.empty:
        return {"base": df, "resumo_tipo": pd.DataFrame(), "top_ubs": pd.DataFrame(), "top_equipes": pd.DataFrame()}
    resumo_tipo = (
        df.groupby("tipo_deficit_parametro_ms", dropna=False)
        .agg(
            municipios=("municipio", "count"),
            deficit_estimado_ubs=("deficit_estimado_ubs", "sum"),
            deficit_estimado_equipes=("deficit_estimado_equipes", "sum"),
            populacao_total=("populacao", "sum"),
        )
        .reset_index()
        .sort_values(["deficit_estimado_ubs", "deficit_estimado_equipes"], ascending=[False, False])
    )
    top_ubs = df[df["deficit_estimado_ubs"] > 0].sort_values(["deficit_estimado_ubs", "populacao"], ascending=[False, False]).head(20)
    top_equipes = df[df["deficit_estimado_equipes"] > 0].sort_values(["deficit_estimado_equipes", "populacao"], ascending=[False, False]).head(20)
    return {"base": df, "resumo_tipo": resumo_tipo, "top_ubs": top_ubs, "top_equipes": top_equipes}
