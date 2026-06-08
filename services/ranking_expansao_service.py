from __future__ import annotations

import pandas as pd

from services.dashboard_aps_service import carregar_base_dashboard
from services.parametros_ms_service import calcular_parametros_ms_gerencial


def _num(v, default: float = 0.0) -> float:
    try:
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def _norm_0_100(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce").fillna(0)
    if s.empty:
        return s
    mn, mx = float(s.min()), float(s.max())
    if mx <= mn:
        return pd.Series(0, index=s.index, dtype=float)
    return ((s - mn) / (mx - mn) * 100).round(1)


def _carregar_distancias_municipais() -> pd.DataFrame:
    # A base municipal consolidada já possui os indicadores territoriais principais.
    # Evita recalcular distâncias linha-a-linha nesta tela para manter a navegação rápida.
    return pd.DataFrame()


def _carregar_confiabilidade_municipal() -> pd.DataFrame:
    try:
        from services.georreferenciamento_service import qualificar_unidades_aps_georreferenciadas
        qual = qualificar_unidades_aps_georreferenciadas()
        df = qual.get("resumo_municipal", pd.DataFrame()).copy()
        if df.empty or "municipio" not in df.columns:
            return pd.DataFrame()
        # Padroniza colunas mais comuns.
        total_col = next((c for c in ["total_unidades", "total_unidades_unicas_cnes", "total"] if c in df.columns), None)
        geo_col = next((c for c in ["com_coordenadas_validas", "georreferenciadas"] if c in df.columns), None)
        if total_col is None or geo_col is None:
            return pd.DataFrame()
        out = pd.DataFrame()
        out["municipio"] = df["municipio"].astype(str)
        out["total_ubs_unicas"] = pd.to_numeric(df[total_col], errors="coerce").fillna(0)
        out["ubs_com_coordenada"] = pd.to_numeric(df[geo_col], errors="coerce").fillna(0)
        out["percentual_ubs_georreferenciadas"] = out.apply(lambda r: round((r["ubs_com_coordenada"] / r["total_ubs_unicas"] * 100), 1) if r["total_ubs_unicas"] > 0 else 0, axis=1)
        out["ubs_pendentes_coordenada"] = (out["total_ubs_unicas"] - out["ubs_com_coordenada"]).clip(lower=0)
        def selo(p):
            if p >= 90: return "Alta confiabilidade"
            if p >= 70: return "Média confiabilidade"
            return "Baixa confiabilidade / em validação"
        out["selo_confiabilidade"] = out["percentual_ubs_georreferenciadas"].apply(selo)
        out["territorios_suspeitos_validacao"] = 0
        out["leitura_confiabilidade"] = "Selo calculado pela completude de coordenadas das UBS; validação territorial permanece recomendada."
        return out
    except Exception:
        return pd.DataFrame()


def calcular_ranking_expansao_aps(capacidade_equipes_por_ubs: int = 1) -> pd.DataFrame:
    """Ranking orientativo para expansão/reorganização da APS.

    Não substitui o score oficial. Cruza déficits estimados, distância territorial,
    score integrado e confiabilidade para orientar priorização de análise.
    """
    base = carregar_base_dashboard().copy()
    if base.empty or "municipio" not in base.columns:
        return pd.DataFrame()

    params = calcular_parametros_ms_gerencial(capacidade_equipes_por_ubs).copy()
    dist = _carregar_distancias_municipais()
    conf = _carregar_confiabilidade_municipal()

    keep_base = [c for c in [
        "municipio", "regiao_saude", "populacao", "score_prioridade_integrada", "classe_prioridade",
        "score_acesso_territorial", "score_vulnerabilidade_social", "score_fragilidade_capacidade",
        "score_vazio_assistencial", "score_equidade_territorial", "distancia_maxima_territorios_km", "distancia_media_territorios_km",
        "territorios_criticos_distantes", "territorios_criticos_distancia", "percentual_territorios_criticos_distantes"
    ] if c in base.columns]
    out = base[keep_base].copy()
    # Usa indicadores territoriais já presentes na base municipal consolidada.
    if "territorios_criticos_distantes" not in out.columns and "territorios_criticos_distancia" in base.columns:
        out["territorios_criticos_distantes"] = base["territorios_criticos_distancia"]
    for _col in ["distancia_maxima_territorios_km", "distancia_media_territorios_km", "territorios_criticos_distantes"]:
        if _col not in out.columns and _col in base.columns:
            out[_col] = base[_col]

    if not params.empty:
        cols = [c for c in [
            "municipio", "tipo_deficit_parametro_ms", "deficit_estimado_ubs", "deficit_estimado_equipes",
            "ubs_existentes", "ubs_necessarias_estimadas", "equipes_aps_existentes", "equipes_esf_necessarias_estimadas",
            "sintese_gerencial_parametro_ms", "prioridade_parametro_ms"
        ] if c in params.columns]
        out = out.merge(params[cols], on="municipio", how="left")

    if not dist.empty:
        out = out.merge(dist, on="municipio", how="left")

    if not conf.empty:
        cols = [c for c in [
            "municipio", "selo_confiabilidade", "percentual_ubs_georreferenciadas", "ubs_pendentes_coordenada",
            "territorios_suspeitos_validacao", "leitura_confiabilidade"
        ] if c in conf.columns]
        out = out.merge(conf[cols], on="municipio", how="left")

    for c in ["deficit_estimado_ubs", "deficit_estimado_equipes", "distancia_maxima_territorios_km", "territorios_criticos_distantes", "score_prioridade_integrada", "score_vulnerabilidade_social", "score_acesso_territorial"]:
        if c not in out.columns:
            out[c] = 0
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0)

    out["subscore_deficit_ubs"] = _norm_0_100(out["deficit_estimado_ubs"])
    out["subscore_deficit_equipes"] = _norm_0_100(out["deficit_estimado_equipes"])
    out["subscore_distancia"] = _norm_0_100(out["distancia_maxima_territorios_km"])
    out["subscore_territorios_criticos"] = _norm_0_100(out["territorios_criticos_distantes"])
    out["subscore_score_integrado"] = pd.to_numeric(out["score_prioridade_integrada"], errors="coerce").fillna(0).clip(0, 100)

    # Penaliza confiabilidade baixa para não transformar dado frágil em decisão direta.
    selo = out.get("selo_confiabilidade", pd.Series("", index=out.index)).astype(str)
    fator_conf = selo.map({
        "Alta confiabilidade": 1.00,
        "Média confiabilidade": 0.85,
        "Baixa confiabilidade / em validação": 0.65,
        "Sem avaliação": 0.55,
    }).fillna(0.75)
    out["fator_confiabilidade"] = fator_conf

    bruto = (
        out["subscore_deficit_ubs"] * 0.30 +
        out["subscore_deficit_equipes"] * 0.20 +
        out["subscore_distancia"] * 0.20 +
        out["subscore_territorios_criticos"] * 0.10 +
        out["subscore_score_integrado"] * 0.20
    )
    out["indice_prioridade_expansao_aps"] = (bruto * out["fator_confiabilidade"]).round(1)

    def classe(v):
        # O índice é relativo e conservador porque usa fator de confiabilidade.
        # Faixas ajustadas para funcionar como ranking de triagem, não como score oficial 0-100.
        v = _num(v)
        if v >= 35: return "Prioridade crítica para análise"
        if v >= 25: return "Alta prioridade para análise"
        if v >= 15: return "Monitoramento intensivo"
        return "Monitoramento regular"

    out["classe_prioridade_expansao"] = out["indice_prioridade_expansao_aps"].apply(classe)

    def eixo(r):
        du, de = int(_num(r.get("deficit_estimado_ubs"))), int(_num(r.get("deficit_estimado_equipes")))
        dist = _num(r.get("distancia_maxima_territorios_km"))
        if du > 0 and de > 0: return "Expansão física e reorganização de equipes"
        if du > 0 and dist >= 15: return "Expansão/readequação física com validação territorial"
        if du > 0: return "Avaliar capacidade física da rede"
        if de > 0: return "Reorganização/provimento de equipes"
        if dist >= 15: return "Acesso territorial e validação de vazios"
        return "Monitoramento"

    out["eixo_recomendado"] = out.apply(eixo, axis=1)
    out["sintese_ranking_expansao"] = out.apply(lambda r: (
        f"{r.get('municipio')}: índice {r.get('indice_prioridade_expansao_aps')} — {r.get('classe_prioridade_expansao')}. "
        f"Déficit estimado: {int(_num(r.get('deficit_estimado_ubs')))} UBS e {int(_num(r.get('deficit_estimado_equipes')))} equipes. "
        f"Maior distância territorial: {_num(r.get('distancia_maxima_territorios_km')):.1f} km. "
        f"Eixo recomendado: {r.get('eixo_recomendado')}."
    ), axis=1)

    return out.sort_values(["indice_prioridade_expansao_aps", "deficit_estimado_ubs", "deficit_estimado_equipes"], ascending=[False, False, False]).reset_index(drop=True)


def resumo_ranking_expansao_aps(capacidade_equipes_por_ubs: int = 1) -> dict:
    df = calcular_ranking_expansao_aps(capacidade_equipes_por_ubs)
    if df.empty:
        return {"base": df, "resumo_classe": pd.DataFrame(), "resumo_eixo": pd.DataFrame(), "top20": df}
    resumo_classe = df.groupby("classe_prioridade_expansao", dropna=False).agg(
        municipios=("municipio", "count"),
        deficit_ubs=("deficit_estimado_ubs", "sum"),
        deficit_equipes=("deficit_estimado_equipes", "sum"),
    ).reset_index().sort_values(["deficit_ubs", "deficit_equipes"], ascending=[False, False])
    resumo_eixo = df.groupby("eixo_recomendado", dropna=False).agg(
        municipios=("municipio", "count"),
        indice_medio=("indice_prioridade_expansao_aps", "mean"),
        deficit_ubs=("deficit_estimado_ubs", "sum"),
        deficit_equipes=("deficit_estimado_equipes", "sum"),
    ).reset_index().sort_values("indice_medio", ascending=False)
    resumo_eixo["indice_medio"] = resumo_eixo["indice_medio"].round(1)
    return {"base": df, "resumo_classe": resumo_classe, "resumo_eixo": resumo_eixo, "top20": df.head(20)}
