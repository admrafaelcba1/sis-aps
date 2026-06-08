from __future__ import annotations

from typing import Any
import pandas as pd

from database.queries import read_table


def _num(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if df is None or df.empty or col not in df.columns:
        return pd.Series(default, index=df.index if isinstance(df, pd.DataFrame) else None, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce").fillna(default)


def _safe_div(num: pd.Series, den: pd.Series, mult: float = 1.0) -> pd.Series:
    den = pd.to_numeric(den, errors="coerce").replace(0, pd.NA)
    return (pd.to_numeric(num, errors="coerce") / den * mult).replace([float("inf"), -float("inf")], pd.NA).fillna(0)


def _norm01(s: pd.Series, inverter: bool = False) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce").fillna(0)
    if s.empty:
        return s
    mn = float(s.min())
    mx = float(s.max())
    if mx == mn:
        out = pd.Series(50.0, index=s.index)
    else:
        out = ((s - mn) / (mx - mn)) * 100
    if inverter:
        out = 100 - out
    return out.clip(0, 100).round(1)


def _classificar(score: Any) -> str:
    try:
        score = float(score)
    except Exception:
        return "Sem dado"
    if score >= 75:
        return "Prioridade territorial crítica"
    if score >= 55:
        return "Alta prioridade territorial"
    if score >= 35:
        return "Média prioridade territorial"
    return "Monitoramento territorial"


def _recomendacao(row: pd.Series) -> str:
    classe = str(row.get("classe_geointeligencia", ""))
    frag = float(row.get("score_fragilidade_capacidade", 0) or 0)
    social = float(row.get("score_social_geo", 0) or 0)
    acesso = float(row.get("score_acesso_territorial", 0) or 0)
    risco = float(row.get("score_risco_sanitario", 0) or 0)
    if "crítica" in classe.lower():
        return "Priorizar análise territorial detalhada, validação regional de vazios assistenciais e pactuação de resposta proporcional ao risco."
    if frag >= 65 and social >= 55:
        return "Avaliar suficiência de UBS/equipes, reorganização territorial da APS e busca ativa em áreas vulneráveis."
    if acesso >= 65:
        return "Validar distância real, rotas, ruralidade, bairros/localidades e eventuais barreiras de acesso até UBS/APS."
    if risco >= 60:
        return "Articular APS, vigilância e ações intersetoriais para territórios com risco sanitário/social relevante."
    return "Manter monitoramento territorial e validar indicadores com equipes municipais e regionais."


def _motivo(row: pd.Series) -> str:
    pares = [
        ("vulnerabilidade social", row.get("score_social_geo", 0)),
        ("fragilidade de capacidade APS", row.get("score_fragilidade_capacidade", 0)),
        ("acesso territorial/distância", row.get("score_acesso_territorial", 0)),
        ("pressão assistencial", row.get("score_pressao_assistencial", 0)),
        ("risco sanitário/intersetorial", row.get("score_risco_sanitario", 0)),
    ]
    pares = sorted([(n, float(v or 0)) for n, v in pares], key=lambda x: x[1], reverse=True)
    top = pares[:3]
    return "; ".join([f"{n} ({v:.1f})" for n, v in top])


def _base_principal() -> pd.DataFrame:
    base = read_table("base_municipal_consolidada")
    if base.empty:
        base = read_table("municipios")
    if base.empty:
        return pd.DataFrame()
    out = base.copy()
    if "latitude" not in out.columns or "longitude" not in out.columns:
        malhas = read_table("malhas_geograficas_municipais")
        if not malhas.empty and "municipio" in malhas.columns:
            cols = [c for c in ["municipio", "latitude_centroide", "longitude_centroide", "area_km2"] if c in malhas.columns]
            aux = malhas[cols].copy()
            out = out.merge(aux, on="municipio", how="left", suffixes=("", "_malha"))
            if "latitude" not in out.columns and "latitude_centroide" in out.columns:
                out["latitude"] = out["latitude_centroide"]
            if "longitude" not in out.columns and "longitude_centroide" in out.columns:
                out["longitude"] = out["longitude_centroide"]
    return out


def carregar_geointeligencia_aps() -> pd.DataFrame:
    """Monta camada geoterritorial municipal cruzando a base completa disponível.

    A função é tolerante a bases ausentes. Ela usa o que estiver carregado no
    banco: base municipal consolidada, MDS, CNES, INEP, SINASC, SIM, SINAN e
    camadas territoriais já agregadas.
    """
    df = _base_principal()
    if df.empty or "municipio" not in df.columns:
        return pd.DataFrame()
    out = df.copy()

    # Complementos por tabelas específicas, quando existirem.
    for tabela, prefixo in [
        ("mds_cadunico_bolsa_familia_municipal", "mds"),
        ("base_publica_inep_censo_escolar_municipal", "inep"),
        ("base_publica_sinasc_municipal", "sinasc"),
        ("base_publica_sim_mortalidade_municipal", "sim"),
    ]:
        t = read_table(tabela)
        if not t.empty and "municipio" in t.columns:
            cols = [c for c in t.columns if c == "municipio" or c not in out.columns]
            out = out.merge(t[cols], on="municipio", how="left")

    sinan = read_table("base_publica_sinan_municipal")
    if not sinan.empty and "municipio" in sinan.columns:
        sinan_sum = sinan.groupby("municipio", as_index=False).agg(
            sinan_notificacoes=("notificacoes", lambda x: pd.to_numeric(x, errors="coerce").fillna(0).sum()),
            sinan_agravos=("agravo", "nunique"),
        )
        out = out.merge(sinan_sum, on="municipio", how="left")

    # Indicadores básicos.
    pop = _num(out, "populacao")
    equipes = _num(out, "total_equipes_aps")
    ubs = _num(out, "total_ubs")
    prof = _num(out, "total_profissionais_aps")
    area = _num(out, "area_km2")

    out["geo_pop_por_equipe"] = _safe_div(pop, equipes, 1)
    out["geo_pop_por_ubs"] = _safe_div(pop, ubs, 1)
    out["geo_equipes_10mil"] = _safe_div(equipes, pop, 10000)
    out["geo_ubs_10mil"] = _safe_div(ubs, pop, 10000)
    out["geo_densidade"] = _safe_div(pop, area, 1)

    # Eixo social: usa MDS, pobreza, BPC, indígenas/quilombolas quando houver.
    cad_pessoas = _num(out, "cadunico_pessoas")
    pbf_pessoas = _num(out, "bolsa_familia_pessoas")
    if pbf_pessoas.sum() == 0:
        pbf_pessoas = _num(out, "bolsa_familia_familias") * 3
    pobreza_fam = _num(out, "cadunico_familias_pobreza_extrema") + _num(out, "cadunico_familias_pobreza") + _num(out, "cadunico_familias_extrema_pobreza")
    bpc = _num(out, "bpc_total")
    ind = _num(out, "populacao_indigena")
    quil = _num(out, "populacao_quilombola")
    out["pct_cadunico_geo"] = _safe_div(cad_pessoas, pop, 100)
    out["pct_pbf_geo"] = _safe_div(pbf_pessoas, pop, 100)
    out["bpc_por_1000_geo"] = _safe_div(bpc, pop, 1000)
    out["pop_tradicional_geo"] = ind + quil
    out["score_social_geo"] = (
        _norm01(out["pct_cadunico_geo"]) * 0.35
        + _norm01(out["pct_pbf_geo"]) * 0.25
        + _norm01(pobreza_fam) * 0.20
        + _norm01(out["bpc_por_1000_geo"]) * 0.10
        + _norm01(out["pop_tradicional_geo"]) * 0.10
    ).round(1)

    out["score_fragilidade_capacidade"] = (
        _norm01(out["geo_pop_por_equipe"]) * 0.35
        + _norm01(out["geo_pop_por_ubs"]) * 0.35
        + _norm01(out["geo_equipes_10mil"], inverter=True) * 0.15
        + _norm01(out["geo_ubs_10mil"], inverter=True) * 0.15
    ).round(1)

    assent = _num(out, "qtd_assentamentos")
    terras = _num(out, "qtd_terras_indigenas_intersecoes")
    amb = _num(out, "qtd_ocorrencias_ambientais")
    # Se não tiver camadas agregadas na base consolidada, mantém 0 sem quebrar.
    out["score_acesso_territorial"] = (
        _norm01(area) * 0.35
        + _norm01(out["geo_densidade"], inverter=True) * 0.20
        + _norm01(assent) * 0.20
        + _norm01(terras) * 0.15
        + _norm01(amb) * 0.10
    ).round(1)

    nasc = _num(out, "nascidos_vivos")
    idosos_bpc = _num(out, "bpc_idoso")
    out["score_pressao_assistencial"] = (
        _norm01(pop) * 0.35
        + _norm01(cad_pessoas) * 0.25
        + _norm01(nasc) * 0.15
        + _norm01(idosos_bpc) * 0.10
        + _norm01(prof, inverter=True) * 0.15
    ).round(1)

    obitos = _num(out, "obitos") + _num(out, "obitos_total")
    obitos_inf = _num(out, "obitos_infantis")
    sinan_not = _num(out, "sinan_notificacoes")
    escolas_rurais = _num(out, "escolas_rurais")
    escolas_ind = _num(out, "escolas_indigenas")
    escolas_quil = _num(out, "escolas_quilombolas")
    out["score_risco_sanitario"] = (
        _norm01(sinan_not) * 0.30
        + _norm01(obitos) * 0.20
        + _norm01(obitos_inf) * 0.20
        + _norm01(escolas_rurais + escolas_ind + escolas_quil) * 0.15
        + _norm01(amb) * 0.15
    ).round(1)

    out["score_geointeligencia_aps"] = (
        out["score_social_geo"] * 0.30
        + out["score_acesso_territorial"] * 0.25
        + out["score_fragilidade_capacidade"] * 0.20
        + out["score_pressao_assistencial"] * 0.15
        + out["score_risco_sanitario"] * 0.10
    ).round(1)
    out["classe_geointeligencia"] = out["score_geointeligencia_aps"].map(_classificar)
    out["ranking_geointeligencia"] = out["score_geointeligencia_aps"].rank(method="dense", ascending=False).astype(int)
    out["motivo_geointeligencia"] = out.apply(_motivo, axis=1)
    out["recomendacao_geointeligencia"] = out.apply(_recomendacao, axis=1)

    return out.sort_values("ranking_geointeligencia")


def resumo_geointeligencia(df: pd.DataFrame) -> dict[str, Any]:
    if df is None or df.empty:
        return {"municipios": 0, "criticos": 0, "alta": 0, "score_medio": 0}
    return {
        "municipios": int(df["municipio"].nunique()) if "municipio" in df.columns else len(df),
        "criticos": int(df["classe_geointeligencia"].astype(str).str.contains("crítica", case=False, na=False).sum()),
        "alta": int(df["classe_geointeligencia"].astype(str).str.contains("Alta", case=False, na=False).sum()),
        "score_medio": round(float(pd.to_numeric(df["score_geointeligencia_aps"], errors="coerce").mean()), 1),
    }


def componentes_geointeligencia_municipio(municipio: str, df: pd.DataFrame | None = None) -> pd.DataFrame:
    base = carregar_geointeligencia_aps() if df is None else df
    if base is None or base.empty or "municipio" not in base.columns:
        return pd.DataFrame()
    alvo = base[base["municipio"].astype(str).str.lower().eq(str(municipio).lower())]
    if alvo.empty:
        return pd.DataFrame()
    r = alvo.iloc[0]
    return pd.DataFrame([
        {"Eixo": "Vulnerabilidade social", "Score": r.get("score_social_geo", 0), "Peso": "30%"},
        {"Eixo": "Acesso territorial", "Score": r.get("score_acesso_territorial", 0), "Peso": "25%"},
        {"Eixo": "Fragilidade APS", "Score": r.get("score_fragilidade_capacidade", 0), "Peso": "20%"},
        {"Eixo": "Pressão assistencial", "Score": r.get("score_pressao_assistencial", 0), "Peso": "15%"},
        {"Eixo": "Risco sanitário/intersetorial", "Score": r.get("score_risco_sanitario", 0), "Peso": "10%"},
    ])
