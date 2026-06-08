from __future__ import annotations

import math
import unicodedata
from typing import Any

import pandas as pd

from database.queries import read_table
from services.dashboard_aps_service import carregar_base_dashboard


def _chave(valor: Any) -> str:
    texto = str(valor or "").strip().upper()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    return " ".join(texto.replace("'", "").split())


def _num_series(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if col not in df.columns:
        return pd.Series([default] * len(df), index=df.index, dtype="float64")
    s = pd.to_numeric(df[col], errors="coerce")
    return s.replace([float("inf"), -float("inf")], pd.NA).fillna(default).astype("float64")


def _safe_div(num: pd.Series, den: pd.Series, mult: float = 1.0) -> pd.Series:
    num = pd.to_numeric(num, errors="coerce")
    den = pd.to_numeric(den, errors="coerce")
    out = (num * mult).divide(den.where(den != 0))
    return out.replace([float("inf"), -float("inf")], pd.NA).fillna(0).astype("float64")


def _normalizar_0_100(s: pd.Series, inverter: bool = False) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce").replace([float("inf"), -float("inf")], pd.NA)
    if s.dropna().empty:
        out = pd.Series([0.0] * len(s), index=s.index)
    else:
        mn = s.min(skipna=True)
        mx = s.max(skipna=True)
        if pd.isna(mn) or pd.isna(mx) or mx == mn:
            out = pd.Series([50.0] * len(s), index=s.index)
        else:
            out = ((s - mn) / (mx - mn)) * 100
    if inverter:
        out = 100 - out
    return out.fillna(0).clip(0, 100).astype("float64")


def _classificar_score(score: float) -> str:
    try:
        score = float(score)
    except Exception:
        score = 0.0
    if score >= 75:
        return "Prioridade crítica"
    if score >= 55:
        return "Alta prioridade"
    if score >= 35:
        return "Média prioridade"
    return "Monitoramento regular"


def _quadrante(vulnerabilidade: float, capacidade: float) -> str:
    vul_alta = vulnerabilidade >= 55
    cap_baixa = capacidade < 45
    if vul_alta and cap_baixa:
        return "Alta vulnerabilidade + baixa capacidade APS"
    if vul_alta and not cap_baixa:
        return "Alta vulnerabilidade + capacidade APS relevante"
    if not vul_alta and cap_baixa:
        return "Baixa/média vulnerabilidade + baixa capacidade APS"
    return "Baixa/média vulnerabilidade + capacidade APS favorável"


def _merge_por_municipio(base: pd.DataFrame, outra: pd.DataFrame, prefixo: str | None = None) -> pd.DataFrame:
    if base.empty or outra.empty or "municipio" not in outra.columns:
        return base
    b = base.copy()
    o = outra.copy()
    b["_chave_municipio"] = b["municipio"].map(_chave) if "municipio" in b.columns else ""
    o["_chave_municipio"] = o["municipio"].map(_chave)
    keep = [c for c in o.columns if c != "municipio"]
    if prefixo:
        ren = {c: f"{prefixo}_{c}" for c in keep if c != "_chave_municipio" and c not in b.columns}
        o = o.rename(columns=ren)
    out = b.merge(o.drop_duplicates("_chave_municipio"), on="_chave_municipio", how="left", suffixes=("", "_dup"))
    dup_cols = [c for c in out.columns if c.endswith("_dup")]
    if dup_cols:
        out = out.drop(columns=dup_cols)
    return out.drop(columns=["_chave_municipio"], errors="ignore")


def _agregar_sinan() -> pd.DataFrame:
    sinan = read_table("base_publica_sinan_municipal")
    if sinan.empty or "municipio" not in sinan.columns:
        return pd.DataFrame()
    for c in ["notificacoes", "registros_considerados", "obitos", "hospitalizacoes", "casos_confirmados_provaveis"]:
        if c in sinan.columns:
            sinan[c] = pd.to_numeric(sinan[c], errors="coerce").fillna(0)
    g = sinan.groupby("municipio", dropna=False)
    out = g.agg(
        sinan_notificacoes=("notificacoes", "sum") if "notificacoes" in sinan.columns else ("municipio", "size"),
        sinan_registros_considerados=("registros_considerados", "sum") if "registros_considerados" in sinan.columns else ("municipio", "size"),
        sinan_obitos=("obitos", "sum") if "obitos" in sinan.columns else ("municipio", "size"),
        sinan_hospitalizacoes=("hospitalizacoes", "sum") if "hospitalizacoes" in sinan.columns else ("municipio", "size"),
        sinan_casos_confirmados=("casos_confirmados_provaveis", "sum") if "casos_confirmados_provaveis" in sinan.columns else ("municipio", "size"),
    ).reset_index()
    if "agravo" in sinan.columns:
        piv = sinan.pivot_table(index="municipio", columns="agravo", values="registros_considerados", aggfunc="sum", fill_value=0).reset_index()
        ren = {}
        for c in piv.columns:
            if c == "municipio":
                continue
            cl = _chave(c).lower()
            if "tubercul" in cl:
                ren[c] = "sinan_tuberculose"
            elif "hans" in cl:
                ren[c] = "sinan_hanseniase"
            elif "viol" in cl:
                ren[c] = "sinan_violencia"
            elif "anim" in cl or "peconh" in cl:
                ren[c] = "sinan_animais_peconhentos"
            else:
                ren[c] = "sinan_" + "_".join(cl.split()[:3])[:40]
        piv = piv.rename(columns=ren)
        out = out.merge(piv, on="municipio", how="left")
    return out


def _preferir_coluna(df: pd.DataFrame, principal: str, alternativas: list[str]) -> pd.Series:
    if principal in df.columns:
        return _num_series(df, principal)
    for alt in alternativas:
        if alt in df.columns:
            return _num_series(df, alt)
    return pd.Series([0.0] * len(df), index=df.index)


def carregar_inteligencia_cruzada_aps(base_dashboard: pd.DataFrame | None = None) -> pd.DataFrame:
    """Monta base analítica para cruzamentos estratégicos da APS.

    A função não substitui tabelas oficiais. Ela cria uma camada gerencial de
    inteligência a partir das bases já importadas: MDS, CNES, SINASC, SIM,
    SINAN, INEP e base municipal consolidada.
    """
    base = base_dashboard.copy() if isinstance(base_dashboard, pd.DataFrame) and not base_dashboard.empty else carregar_base_dashboard()
    if base.empty:
        return pd.DataFrame()

    # Garantir chaves e campos populacionais.
    if "municipio" not in base.columns:
        return pd.DataFrame()
    base["municipio"] = base["municipio"].astype(str)
    base["populacao"] = _preferir_coluna(base, "populacao", ["populacao_ibge", "populacao_estimada"])

    # MDS consolidado local, quando existir.
    mds = read_table("mds_cadunico_bolsa_familia_municipal")
    if not mds.empty and "municipio" in mds.columns:
        campos_mds = [
            "municipio", "codigo_ibge", "cadunico_familias", "cadunico_pessoas", "cadunico_familias_pobreza_extrema",
            "cadunico_familias_baixa_renda", "bolsa_familia_familias", "bolsa_familia_pessoas",
            "bolsa_familia_valor_repassado", "bpc_total", "bpc_pcd", "bpc_idoso", "bpc_cadunico_total",
            "score_vulnerabilidade_mds", "ranking_vulnerabilidade_mds", "classificacao_vulnerabilidade_mds",
        ]
        campos_mds = [c for c in campos_mds if c in mds.columns]
        mds = mds[campos_mds].copy()
        base = _merge_por_municipio(base, mds)

    # Bases sanitárias e intersetoriais já importadas.
    sinasc = read_table("base_publica_sinasc_municipal")
    sim = read_table("base_publica_sim_mortalidade_municipal")
    sinan = _agregar_sinan()
    inep = read_table("base_publica_inep_censo_escolar_municipal")
    for df in [sinasc, sim, sinan, inep]:
        if not df.empty:
            base = _merge_por_municipio(base, df)

    pop = _num_series(base, "populacao")

    # Indicadores proporcionais.
    base["pct_pop_cadunico_decisao"] = _safe_div(_num_series(base, "cadunico_pessoas"), pop, 100)
    base["pct_pop_pbf_decisao"] = _safe_div(_num_series(base, "bolsa_familia_pessoas"), pop, 100)
    base["pct_familias_pobreza_extrema_decisao"] = _safe_div(
        _num_series(base, "cadunico_familias_pobreza_extrema"),
        _num_series(base, "cadunico_familias"),
        100,
    )
    base["bpc_por_mil_hab_decisao"] = _safe_div(_num_series(base, "bpc_total"), pop, 1000)
    base["valor_pbf_per_capita_decisao"] = _safe_div(_num_series(base, "bolsa_familia_valor_repassado"), pop, 1)

    # Capacidade APS.
    equipes = _preferir_coluna(base, "total_equipes_aps", ["equipes_aps", "total_equipes"])
    ubs = _preferir_coluna(base, "total_ubs", ["ubs", "estabelecimentos_aps", "total_estabelecimentos"])
    prof = _preferir_coluna(base, "total_profissionais_aps", ["profissionais_aps", "total_profissionais"])
    base["equipes_aps_por_10mil_decisao"] = _safe_div(equipes, pop, 10000)
    base["ubs_por_10mil_decisao"] = _safe_div(ubs, pop, 10000)
    base["profissionais_aps_por_10mil_decisao"] = _safe_div(prof, pop, 10000)
    base["pop_por_equipe_decisao"] = _safe_div(pop, equipes, 1)
    base["pop_por_ubs_decisao"] = _safe_div(pop, ubs, 1)

    # Pressão sanitária / assistencial.
    nascidos = _num_series(base, "nascidos_vivos")
    obitos = _preferir_coluna(base, "obitos_total", ["obitos"])
    obitos_inf = _num_series(base, "obitos_infantis")
    sinan_reg = _num_series(base, "sinan_registros_considerados")
    base["nascidos_por_10mil_decisao"] = _safe_div(nascidos, pop, 10000)
    base["obitos_por_10mil_decisao"] = _safe_div(obitos, pop, 10000)
    base["obitos_infantis_por_mil_nv_decisao"] = _safe_div(obitos_inf, nascidos, 1000)
    base["sinan_registros_por_10mil_decisao"] = _safe_div(sinan_reg, pop, 10000)

    # Infraestrutura escolar como marcador intersetorial.
    escolas = _num_series(base, "escolas_total")
    escolas_esgoto = _num_series(base, "escolas_com_esgoto")
    escolas_internet = _num_series(base, "escolas_com_internet")
    base["pct_escolas_sem_esgoto_decisao"] = 100 - _safe_div(escolas_esgoto, escolas, 100)
    base["pct_escolas_sem_internet_decisao"] = 100 - _safe_div(escolas_internet, escolas, 100)
    base["pct_escolas_sem_esgoto_decisao"] = base["pct_escolas_sem_esgoto_decisao"].clip(0, 100)
    base["pct_escolas_sem_internet_decisao"] = base["pct_escolas_sem_internet_decisao"].clip(0, 100)

    # Scores compostos.
    if "score_vulnerabilidade_mds" in base.columns and _num_series(base, "score_vulnerabilidade_mds").sum() > 0:
        score_social_mds = _num_series(base, "score_vulnerabilidade_mds")
    else:
        score_social_mds = (
            _normalizar_0_100(base["pct_pop_cadunico_decisao"]) * 0.35
            + _normalizar_0_100(base["pct_pop_pbf_decisao"]) * 0.30
            + _normalizar_0_100(base["pct_familias_pobreza_extrema_decisao"]) * 0.25
            + _normalizar_0_100(base["bpc_por_mil_hab_decisao"]) * 0.10
        )
    base["score_vulnerabilidade_social_decisao"] = score_social_mds.clip(0, 100).round(1)

    base["score_capacidade_aps_decisao"] = (
        _normalizar_0_100(base["equipes_aps_por_10mil_decisao"]) * 0.45
        + _normalizar_0_100(base["ubs_por_10mil_decisao"]) * 0.30
        + _normalizar_0_100(base["profissionais_aps_por_10mil_decisao"]) * 0.25
    ).clip(0, 100).round(1)
    base["score_fragilidade_capacidade_decisao"] = (100 - base["score_capacidade_aps_decisao"]).clip(0, 100).round(1)

    base["score_pressao_assistencial_decisao"] = (
        _normalizar_0_100(base["pop_por_equipe_decisao"]) * 0.35
        + _normalizar_0_100(base["pop_por_ubs_decisao"]) * 0.25
        + _normalizar_0_100(base["nascidos_por_10mil_decisao"]) * 0.20
        + _normalizar_0_100(base["obitos_por_10mil_decisao"]) * 0.20
    ).clip(0, 100).round(1)

    base["score_alerta_sanitario_decisao"] = (
        _normalizar_0_100(base["sinan_registros_por_10mil_decisao"]) * 0.45
        + _normalizar_0_100(base["obitos_infantis_por_mil_nv_decisao"]) * 0.25
        + _normalizar_0_100(_num_series(base, "perc_maes_adolescentes")) * 0.15
        + _normalizar_0_100(_num_series(base, "perc_prenatal_insuficiente")) * 0.15
    ).clip(0, 100).round(1)

    # Se o dashboard antigo já tiver acesso territorial, incorporar como ajuste.
    acesso = _preferir_coluna(base, "score_acesso_territorial", ["score_acesso", "score_distancia_territorial"])
    if acesso.sum() > 0:
        base["score_acesso_territorial_decisao"] = acesso.clip(0, 100)
    else:
        base["score_acesso_territorial_decisao"] = pd.Series([0.0] * len(base), index=base.index)

    base["score_descompasso_territorial_decisao"] = (
        base["score_vulnerabilidade_social_decisao"] * 0.35
        + base["score_pressao_assistencial_decisao"] * 0.25
        + base["score_fragilidade_capacidade_decisao"] * 0.30
        + base["score_acesso_territorial_decisao"] * 0.10
    ).clip(0, 100).round(1)

    base["score_prioridade_integrada_decisao"] = (
        base["score_vulnerabilidade_social_decisao"] * 0.30
        + base["score_pressao_assistencial_decisao"] * 0.22
        + base["score_fragilidade_capacidade_decisao"] * 0.22
        + base["score_alerta_sanitario_decisao"] * 0.16
        + base["score_acesso_territorial_decisao"] * 0.10
    ).clip(0, 100).round(1)

    base["classificacao_prioridade_decisao"] = base["score_prioridade_integrada_decisao"].map(_classificar_score)
    base["quadrante_decisao"] = [
        _quadrante(v, c) for v, c in zip(base["score_vulnerabilidade_social_decisao"], base["score_capacidade_aps_decisao"])
    ]
    base = base.sort_values("score_prioridade_integrada_decisao", ascending=False).reset_index(drop=True)
    base["ranking_prioridade_decisao"] = range(1, len(base) + 1)

    motivos = []
    encaminhamentos = []
    for _, row in base.iterrows():
        comps = [
            ("vulnerabilidade social", float(row.get("score_vulnerabilidade_social_decisao", 0))),
            ("pressão assistencial", float(row.get("score_pressao_assistencial_decisao", 0))),
            ("fragilidade de capacidade APS", float(row.get("score_fragilidade_capacidade_decisao", 0))),
            ("alertas sanitários", float(row.get("score_alerta_sanitario_decisao", 0))),
            ("acesso territorial", float(row.get("score_acesso_territorial_decisao", 0))),
        ]
        comps_ord = sorted(comps, key=lambda x: x[1], reverse=True)
        principais = [nome for nome, val in comps_ord[:3] if val >= 35]
        if not principais:
            principais = [comps_ord[0][0]]
        motivos.append("Prioridade puxada por " + ", ".join(principais) + ".")

        if row.get("classificacao_prioridade_decisao") in {"Prioridade crítica", "Alta prioridade"}:
            encaminhamentos.append("Validar dados com ERS/município e discutir plano de apoio APS regionalizado.")
        elif float(row.get("score_vulnerabilidade_social_decisao", 0)) >= 55:
            encaminhamentos.append("Aprofundar leitura social e articular APS, vigilância e assistência social.")
        elif float(row.get("score_fragilidade_capacidade_decisao", 0)) >= 60:
            encaminhamentos.append("Reavaliar suficiência de equipes, UBS e profissionais cadastrados no CNES.")
        else:
            encaminhamentos.append("Manter monitoramento e usar diagnóstico municipal para leitura pontual.")
    base["motivo_prioridade_decisao"] = motivos
    base["encaminhamento_decisao"] = encaminhamentos

    return base


def resumo_inteligencia_cruzada(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {}
    return {
        "municipios": int(len(df)),
        "prioridade_critica": int((df.get("classificacao_prioridade_decisao", pd.Series(dtype=str)) == "Prioridade crítica").sum()),
        "alta_prioridade": int((df.get("classificacao_prioridade_decisao", pd.Series(dtype=str)) == "Alta prioridade").sum()),
        "score_medio": round(float(_num_series(df, "score_prioridade_integrada_decisao").mean()), 1),
        "vulnerabilidade_media": round(float(_num_series(df, "score_vulnerabilidade_social_decisao").mean()), 1),
        "pressao_media": round(float(_num_series(df, "score_pressao_assistencial_decisao").mean()), 1),
        "fragilidade_media": round(float(_num_series(df, "score_fragilidade_capacidade_decisao").mean()), 1),
        "alerta_sanitario_medio": round(float(_num_series(df, "score_alerta_sanitario_decisao").mean()), 1),
    }


def gerar_sintese_decisao(df: pd.DataFrame) -> str:
    if df.empty:
        return "Não há base suficiente para síntese estratégica."
    resumo = resumo_inteligencia_cruzada(df)
    top = df.head(3)["municipio"].astype(str).tolist() if "municipio" in df.columns else []
    regioes = []
    if "regiao_saude" in df.columns:
        reg = df.groupby("regiao_saude", dropna=False)["score_prioridade_integrada_decisao"].mean().sort_values(ascending=False).head(3)
        regioes = [str(i) for i in reg.index]
    return (
        f"A leitura cruzada integra vulnerabilidade social, capacidade APS, pressão assistencial e alertas sanitários. "
        f"Foram avaliados {resumo.get('municipios', 0)} municípios; {resumo.get('prioridade_critica', 0)} estão em prioridade crítica "
        f"e {resumo.get('alta_prioridade', 0)} em alta prioridade. "
        f"Os maiores sinais de atenção aparecem inicialmente em {', '.join(top) if top else 'municípios do topo do ranking'}. "
        f"Regionalmente, a análise sugere maior atenção para {', '.join(regioes) if regioes else 'as regiões com maior score médio'}. "
        "O ranking é uma régua técnica preliminar e deve orientar validação territorial, pactuação regional e definição de ações de apoio."
    )


def matriz_decisao_estrategica(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "quadrante_decisao" not in df.columns:
        return pd.DataFrame()
    out = df.groupby("quadrante_decisao", dropna=False).agg(
        municipios=("municipio", "count"),
        score_medio=("score_prioridade_integrada_decisao", "mean"),
        populacao=("populacao", "sum"),
    ).reset_index()
    out["score_medio"] = pd.to_numeric(out["score_medio"], errors="coerce").round(1)
    return out.sort_values("score_medio", ascending=False)


def ranking_regional_decisao(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "regiao_saude" not in df.columns:
        return pd.DataFrame()
    out = df.groupby("regiao_saude", dropna=False).agg(
        municipios=("municipio", "count"),
        score_medio=("score_prioridade_integrada_decisao", "mean"),
        vulnerabilidade_media=("score_vulnerabilidade_social_decisao", "mean"),
        pressao_media=("score_pressao_assistencial_decisao", "mean"),
        fragilidade_media=("score_fragilidade_capacidade_decisao", "mean"),
        alerta_sanitario_medio=("score_alerta_sanitario_decisao", "mean"),
        populacao=("populacao", "sum"),
    ).reset_index()
    for c in ["score_medio", "vulnerabilidade_media", "pressao_media", "fragilidade_media", "alerta_sanitario_medio"]:
        out[c] = pd.to_numeric(out[c], errors="coerce").round(1)
    out["municipios_prioridade_alta_critica"] = df[df["classificacao_prioridade_decisao"].isin(["Prioridade crítica", "Alta prioridade"])].groupby("regiao_saude").size().reindex(out["regiao_saude"]).fillna(0).astype(int).values
    return out.sort_values("score_medio", ascending=False)
