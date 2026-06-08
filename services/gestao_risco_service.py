from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

try:
    from database.queries import read_table
except Exception:  # pragma: no cover
    read_table = None


def _normalizar_nome_coluna(col: str) -> str:
    texto = str(col or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-z0-9]+", "_", texto)
    return re.sub(r"_+", "_", texto).strip("_")


def _normalizar_municipio(valor: Any) -> str:
    texto = str(valor or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _to_num(s: Any, default: float = 0.0) -> float:
    try:
        if s is None or pd.isna(s):
            return default
        if isinstance(s, str):
            s = s.replace("%", "").replace("R$", "").replace(".", "").replace(",", ".").strip()
        v = float(s)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def _num_series(df: pd.DataFrame, coluna: str, default: float = 0.0) -> pd.Series:
    if coluna not in df.columns:
        return pd.Series(default, index=df.index, dtype="float64")
    if df.empty:
        return pd.Series(dtype="float64")
    s = df[coluna]
    if s.dtype == object:
        s = s.astype(str).str.replace("%", "", regex=False).str.replace("R$", "", regex=False)
        s = s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce").fillna(default).astype(float)


def _clip01(x: pd.Series | float) -> pd.Series | float:
    return np.clip(x, 0, 100)


def _score_maior_pior(s: pd.Series, p10: float | None = None, p90: float | None = None) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce").fillna(0).astype(float)
    if s.empty:
        return s
    low = float(np.nanpercentile(s, 10)) if p10 is None else float(p10)
    high = float(np.nanpercentile(s, 90)) if p90 is None else float(p90)
    if abs(high - low) < 1e-9:
        return pd.Series(0.0, index=s.index)
    return pd.Series(_clip01(((s - low) / (high - low)) * 100), index=s.index).astype(float)


def _score_menor_pior(s: pd.Series) -> pd.Series:
    return 100 - _score_maior_pior(s)


def _ler_tabela_segura(nome: str) -> pd.DataFrame:
    if read_table is None:
        return pd.DataFrame()
    try:
        df = read_table(nome)
        if isinstance(df, pd.DataFrame):
            df = df.copy()
            df.columns = [_normalizar_nome_coluna(c) for c in df.columns]
            return df
    except Exception:
        return pd.DataFrame()
    return pd.DataFrame()


def _primeira_tabela_existente(nomes: list[str]) -> tuple[str, pd.DataFrame]:
    for nome in nomes:
        df = _ler_tabela_segura(nome)
        if not df.empty:
            return nome, df
    return "", pd.DataFrame()


def _preparar_municipio(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if "municipio" not in out.columns:
        cand = [c for c in out.columns if "municip" in c or "unidade_territorial" in c]
        if cand:
            out = out.rename(columns={cand[0]: "municipio"})
    if "municipio" not in out.columns:
        return pd.DataFrame()
    out["municipio_norm"] = out["municipio"].map(_normalizar_municipio)
    return out.dropna(subset=["municipio_norm"])


def _merge_por_municipio(base: pd.DataFrame, outro: pd.DataFrame, sufixo: str = "") -> pd.DataFrame:
    outro = _preparar_municipio(outro)
    if outro.empty:
        return base
    cols = [c for c in outro.columns if c != "municipio"]
    tmp = outro[cols].drop_duplicates("municipio_norm")
    overlap = [c for c in tmp.columns if c in base.columns and c != "municipio_norm"]
    if overlap:
        tmp = tmp.rename(columns={c: f"{c}_{sufixo}" if sufixo else f"{c}_extra" for c in overlap})
    return base.merge(tmp, on="municipio_norm", how="left")


def _carregar_base_municipal_principal() -> tuple[str, pd.DataFrame]:
    nomes = [
        "base_municipal_completa",
        "base_municipal_consolidada",
        "base_municipal",
        "municipios_consolidados",
        "dim_municipio",
        "dim_municipios",
    ]
    nome, df = _primeira_tabela_existente(nomes)
    df = _preparar_municipio(df)
    return nome, df


def _enriquecer_com_fontes(base: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    fontes: dict[str, str] = {}
    fontes["base_principal"] = "base municipal consolidada"

    candidatos = {
        "mds": ["mds_cadunico_bolsa_familia_municipal", "mds_municipal", "cadunico_bolsa_familia_municipal"],
        "geointeligencia": ["geointeligencia_aps", "geo_inteligencia_aps", "geointeligencia_municipal"],
        "motor": ["motor_inteligencia_aps", "motor_decisao_aps", "prioridade_integrada_aps"],
        "inep": ["inep_censo_escolar_municipal", "inep_municipal", "censo_escolar_municipal"],
        "sinasc": ["sinasc_municipal", "datasus_sinasc_municipal", "sinasc_resumo_municipal"],
        "sim": ["sim_municipal", "datasus_sim_municipal", "sim_resumo_municipal"],
        "sinan": ["sinan_municipal", "datasus_sinan_municipal", "sinan_resumo_municipal"],
        "geo": ["base_municipal_geografica", "georreferencia_municipal", "geo_municipal"],
        "socio": ["socioeducacional_municipal", "determinantes_sociais_municipios_aps", "perfil_socioeducacional_municipal"],
    }
    out = base.copy()
    for chave, nomes in candidatos.items():
        nome, df = _primeira_tabela_existente(nomes)
        if not df.empty:
            fontes[chave] = nome
            out = _merge_por_municipio(out, df, sufixo=chave)
    return out, fontes


def _col(df: pd.DataFrame, nomes: list[str]) -> str | None:
    for n in nomes:
        nn = _normalizar_nome_coluna(n)
        if nn in df.columns:
            return nn
    # aproximação por substring
    for n in nomes:
        nn = _normalizar_nome_coluna(n)
        for c in df.columns:
            if nn in c:
                return c
    return None


def _serie(df: pd.DataFrame, nomes: list[str], default: float = 0.0) -> pd.Series:
    c = _col(df, nomes)
    if c is None:
        return pd.Series(default, index=df.index, dtype="float64")
    return _num_series(df, c, default)


def _texto_col(df: pd.DataFrame, nomes: list[str], default: str = "") -> pd.Series:
    c = _col(df, nomes)
    if c is None:
        return pd.Series(default, index=df.index, dtype="object")
    return df[c].fillna(default).astype(str)


def _classificar_risco(score: float) -> str:
    if score >= 80:
        return "Risco crítico"
    if score >= 65:
        return "Risco alto"
    if score >= 45:
        return "Risco moderado"
    if score >= 25:
        return "Risco baixo"
    return "Risco residual"


def _prioridade(score: float) -> str:
    if score >= 80:
        return "Resposta imediata e pactuação prioritária"
    if score >= 65:
        return "Plano de mitigação em curto prazo"
    if score >= 45:
        return "Monitoramento intensivo e ação dirigida"
    if score >= 25:
        return "Monitoramento regular"
    return "Acompanhamento de rotina"


def _principal_fator(row: pd.Series) -> str:
    fatores = {
        "Vulnerabilidade social": _to_num(row.get("risco_social")),
        "Capacidade APS": _to_num(row.get("risco_capacidade_aps")),
        "Acesso territorial": _to_num(row.get("risco_acesso_territorial")),
        "Materno-infantil": _to_num(row.get("risco_materno_infantil")),
        "Mortalidade": _to_num(row.get("risco_mortalidade")),
        "Vigilância/agregavos": _to_num(row.get("risco_vigilancia")),
        "Intersetorial/educação": _to_num(row.get("risco_intersetorial")),
        "Equidade territorial": _to_num(row.get("risco_equidade_territorial")),
    }
    fator = max(fatores.items(), key=lambda kv: kv[1])
    return f"{fator[0]} é o principal eixo de risco ({fator[1]:.1f})."


def _mitigacao(row: pd.Series) -> str:
    eixos: list[str] = []
    if _to_num(row.get("risco_social")) >= 60:
        eixos.append("articular APS, assistência social e vigilância para territórios de maior vulnerabilidade")
    if _to_num(row.get("risco_capacidade_aps")) >= 60:
        eixos.append("avaliar suficiência de equipes, distribuição de profissionais, CNES/INE e capacidade física das UBS")
    if _to_num(row.get("risco_acesso_territorial")) >= 60:
        eixos.append("validar rotas reais, vazios intramunicipais, distância até UBS e necessidade de reorganização territorial")
    if _to_num(row.get("risco_materno_infantil")) >= 60:
        eixos.append("fortalecer pré-natal, puericultura, busca ativa e vigilância do recém-nascido")
    if _to_num(row.get("risco_mortalidade")) >= 60:
        eixos.append("investigar perfil de mortalidade e qualificar linhas de cuidado prioritárias")
    if _to_num(row.get("risco_vigilancia")) >= 60:
        eixos.append("integrar APS e vigilância para busca ativa, tratamento oportuno e educação em saúde")
    if _to_num(row.get("risco_intersetorial")) >= 60:
        eixos.append("acionar educação, assistência social e gestão municipal para resposta intersetorial")
    if _to_num(row.get("risco_equidade_territorial")) >= 60:
        eixos.append("priorizar povos tradicionais, ruralidade, assentamentos, indígenas, quilombolas e populações dispersas")
    if not eixos:
        return "Manter monitoramento regular e validar indicadores com a área técnica antes de definir ação adicional."
    return "Mitigar por meio de: " + "; ".join(eixos[:4]) + "."


def _alertas(row: pd.Series) -> str:
    alertas = []
    mapeia = [
        ("Social", "risco_social"),
        ("Capacidade APS", "risco_capacidade_aps"),
        ("Acesso", "risco_acesso_territorial"),
        ("Materno-infantil", "risco_materno_infantil"),
        ("Mortalidade", "risco_mortalidade"),
        ("Vigilância", "risco_vigilancia"),
        ("Intersetorial", "risco_intersetorial"),
        ("Equidade", "risco_equidade_territorial"),
    ]
    for nome, c in mapeia:
        v = _to_num(row.get(c))
        if v >= 75:
            alertas.append(f"{nome}: crítico")
        elif v >= 60:
            alertas.append(f"{nome}: alto")
    return "; ".join(alertas) if alertas else "Sem alerta crítico nos eixos avaliados."


def carregar_gestao_risco_aps() -> dict[str, Any]:
    nome_base, base = _carregar_base_municipal_principal()
    if base.empty:
        return {
            "ok": False,
            "mensagem": "Não foi encontrada base municipal consolidada para montar a gestão de risco.",
            "base": pd.DataFrame(),
            "fontes": {},
        }
    base, fontes = _enriquecer_com_fontes(base)
    fontes["base_principal"] = nome_base or fontes.get("base_principal", "base municipal")

    df = base.copy()
    pop = _serie(df, ["populacao", "populacao_ibge", "populacao_total", "populacao_2022"], 0)
    df["populacao_risco"] = pop

    # Social/MDS
    pct_cad = _serie(df, ["pct_populacao_cadunico", "pct_cadunico_geo", "percentual_populacao_cadunico"], 0)
    pct_pbf = _serie(df, ["pct_populacao_bolsa_familia", "pct_pbf_geo", "percentual_populacao_pbf"], 0)
    score_mds = _serie(df, ["score_vulnerabilidade_mds", "score_social_geo", "score_vulnerabilidade_social_decisao"], 0)
    bpc_total = _serie(df, ["bpc_total", "beneficiarios_bpc"], 0)
    familias_pobreza = _serie(df, ["cadunico_familias_pobreza_extrema", "familias_pobreza_extrema", "familias_pobreza"], 0)
    df["risco_social"] = _clip01((pct_cad * 0.28) + (pct_pbf * 0.28) + (score_mds * 0.24) + (_score_maior_pior(bpc_total) * 0.10) + (_score_maior_pior(familias_pobreza) * 0.10))

    # APS capacity
    frag_cap = _serie(df, ["score_fragilidade_capacidade", "score_fragilidade_capacidade_decisao", "score_fragilidade_capacidade_geo"], 0)
    pop_por_eq = _serie(df, ["populacao_por_equipe", "pop_por_equipe", "geo_pop_por_equipe"], 0)
    pop_por_ubs = _serie(df, ["populacao_por_ubs", "pop_por_ubs", "geo_pop_por_ubs"], 0)
    equipes_por_10 = _serie(df, ["equipes_por_10mil_hab", "equipes_10mil"], 0)
    df["risco_capacidade_aps"] = _clip01((frag_cap * 0.45) + (_score_maior_pior(pop_por_eq) * 0.25) + (_score_maior_pior(pop_por_ubs) * 0.20) + (_score_menor_pior(equipes_por_10) * 0.10))

    # Access/geoterritorial
    score_geo = _serie(df, ["score_geointeligencia_aps", "score_acesso_territorial", "score_acesso_territorial_decisao", "indice_geo_preliminar"], 0)
    dist_max = _serie(df, ["distancia_maxima_km", "distancia_maxima_territorios_km", "distancia_maxima_intramunicipal_km"], 0)
    criticos_dist = _serie(df, ["territorios_criticos_distantes", "criticos_distantes", "territorios_criticos"], 0)
    assentamentos = _serie(df, ["qtd_assentamentos", "assentamentos"], 0)
    df["risco_acesso_territorial"] = _clip01((score_geo * 0.45) + (_score_maior_pior(dist_max) * 0.25) + (_score_maior_pior(criticos_dist) * 0.20) + (_score_maior_pior(assentamentos) * 0.10))

    # Maternal/child
    baixo_peso = _serie(df, ["baixo_peso_pct", "pct_baixo_peso", "baixo_peso"], 0)
    premat = _serie(df, ["prematuridade_pct", "pct_prematuridade", "prematuridade"], 0)
    maes_adol = _serie(df, ["maes_adolescentes_pct", "pct_maes_adolescentes", "maes_adolescentes"], 0)
    obitos_infantis = _serie(df, ["obitos_infantis", "mortalidade_infantil", "taxa_mortalidade_infantil"], 0)
    nascidos = _serie(df, ["nascidos_vivos", "sinasc_total", "total_nascidos_vivos"], 0)
    df["risco_materno_infantil"] = _clip01((_score_maior_pior(baixo_peso) * 0.22) + (_score_maior_pior(premat) * 0.22) + (_score_maior_pior(maes_adol) * 0.18) + (_score_maior_pior(obitos_infantis) * 0.25) + (_score_maior_pior(nascidos) * 0.13))

    # Mortality
    obitos_totais = _serie(df, ["obitos_totais", "sim_obitos_totais", "total_obitos"], 0)
    causas_ext = _serie(df, ["causas_externas", "obitos_causas_externas"], 0)
    cardio = _serie(df, ["cardiovasculares", "obitos_cardiovasculares"], 0)
    respiratorias = _serie(df, ["respiratorias", "obitos_respiratorios"], 0)
    maternas = _serie(df, ["mortes_maternas", "obitos_maternos"], 0)
    df["risco_mortalidade"] = _clip01((_score_maior_pior(obitos_totais) * 0.20) + (_score_maior_pior(causas_ext) * 0.22) + (_score_maior_pior(cardio) * 0.20) + (_score_maior_pior(respiratorias) * 0.18) + (_score_maior_pior(maternas) * 0.20))

    # Surveillance/SINAN
    sinan_total = _serie(df, ["sinan_total", "notificacoes_sinan", "registros_sinan"], 0)
    tub = _serie(df, ["tuberculose", "tube", "sinan_tuberculose"], 0)
    hans = _serie(df, ["hanseniase", "hans", "sinan_hanseniase"], 0)
    viol = _serie(df, ["violencia", "viol", "violencia_interpessoal"], 0)
    animais = _serie(df, ["animais_peconhentos", "anim", "acidentes_animais_peconhentos"], 0)
    df["risco_vigilancia"] = _clip01((_score_maior_pior(sinan_total) * 0.20) + (_score_maior_pior(tub) * 0.25) + (_score_maior_pior(hans) * 0.25) + (_score_maior_pior(viol) * 0.18) + (_score_maior_pior(animais) * 0.12))

    # Intersectoral/education
    analfab = _serie(df, ["taxa_analfabetismo_estimado_pct", "analfabetismo", "nao_alfabetizados"], 0)
    baixa_inst = _serie(df, ["baixa_instrucao_pct", "baixa_instrucao"], 0)
    saneamento = _serie(df, ["saneamento_indicador", "esgoto", "agua_esgoto", "pct_esgoto"], 0)
    escolas_sem_esgoto = _serie(df, ["escolas_sem_esgoto", "pct_escolas_sem_esgoto"], 0)
    internet_escolar = _serie(df, ["internet_escolar", "pct_internet_escolar"], 0)
    # saneamento pode vir como percentual favorável. Risco é maior quando saneamento ou internet são menores.
    df["risco_intersetorial"] = _clip01((_score_maior_pior(analfab) * 0.30) + (_score_maior_pior(baixa_inst) * 0.22) + (_score_menor_pior(saneamento) * 0.22) + (_score_maior_pior(escolas_sem_esgoto) * 0.16) + (_score_menor_pior(internet_escolar) * 0.10))

    # Equity/territorial special populations
    ind = _serie(df, ["populacao_indigena", "pessoas_indigenas", "indigenas"], 0)
    quil = _serie(df, ["populacao_quilombola", "pessoas_quilombolas", "quilombolas"], 0)
    terras = _serie(df, ["qtd_terras_indigenas_intersecoes", "terras_indigenas"], 0)
    rural = _serie(df, ["populacao_rural", "pct_rural", "escolas_rurais"], 0)
    situacao_rua = _serie(df, ["familias_situacao_rua", "cadunico_rua_familias", "pessoas_situacao_rua"], 0)
    df["risco_equidade_territorial"] = _clip01((_score_maior_pior(ind) * 0.25) + (_score_maior_pior(quil) * 0.20) + (_score_maior_pior(terras) * 0.18) + (_score_maior_pior(rural) * 0.20) + (_score_maior_pior(situacao_rua) * 0.17))

    pesos = {
        "risco_social": 0.22,
        "risco_capacidade_aps": 0.18,
        "risco_acesso_territorial": 0.16,
        "risco_materno_infantil": 0.12,
        "risco_mortalidade": 0.11,
        "risco_vigilancia": 0.10,
        "risco_intersetorial": 0.07,
        "risco_equidade_territorial": 0.04,
    }
    df["score_risco_integrado_aps"] = sum(df[k].fillna(0) * peso for k, peso in pesos.items()).round(1)
    df["ranking_risco_integrado_aps"] = df["score_risco_integrado_aps"].rank(ascending=False, method="dense").astype(int)
    df["classificacao_risco_integrado"] = df["score_risco_integrado_aps"].apply(_classificar_risco)
    df["prioridade_mitigacao"] = df["score_risco_integrado_aps"].apply(_prioridade)
    df["principal_fator_risco"] = df.apply(_principal_fator, axis=1)
    df["alertas_risco"] = df.apply(_alertas, axis=1)
    df["plano_mitigacao_resumido"] = df.apply(_mitigacao, axis=1)

    cols_base = [
        "ranking_risco_integrado_aps", "municipio", "regiao_saude", "populacao_risco",
        "score_risco_integrado_aps", "classificacao_risco_integrado", "prioridade_mitigacao",
        "risco_social", "risco_capacidade_aps", "risco_acesso_territorial", "risco_materno_infantil",
        "risco_mortalidade", "risco_vigilancia", "risco_intersetorial", "risco_equidade_territorial",
        "principal_fator_risco", "alertas_risco", "plano_mitigacao_resumido",
    ]
    for c in ["regiao_saude"]:
        if c not in df.columns:
            df[c] = "Não informada"
    cols = [c for c in cols_base if c in df.columns]
    base_risco = df[cols].sort_values("ranking_risco_integrado_aps").reset_index(drop=True)

    return {
        "ok": True,
        "base": base_risco,
        "fontes": fontes,
        "pesos": pesos,
        "mensagem": f"Gestão de risco calculada para {len(base_risco)} municípios.",
    }


def resumo_gestao_risco(df: pd.DataFrame) -> dict[str, Any]:
    if df is None or df.empty:
        return {"municipios": 0, "criticos": 0, "altos": 0, "score_medio": 0}
    cls = df.get("classificacao_risco_integrado", pd.Series(dtype=str)).astype(str)
    return {
        "municipios": int(len(df)),
        "criticos": int(cls.str.contains("crítico|critico", case=False, na=False).sum()),
        "altos": int(cls.str.contains("alto", case=False, na=False).sum()),
        "moderados": int(cls.str.contains("moderado", case=False, na=False).sum()),
        "score_medio": float(pd.to_numeric(df.get("score_risco_integrado_aps", pd.Series(dtype=float)), errors="coerce").mean() or 0),
    }


def resumo_regional_risco(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "regiao_saude" not in df.columns:
        return pd.DataFrame()
    out = df.copy()
    out["score_risco_integrado_aps"] = pd.to_numeric(out["score_risco_integrado_aps"], errors="coerce").fillna(0)
    grp = out.groupby("regiao_saude", dropna=False).agg(
        municipios=("municipio", "nunique"),
        score_medio=("score_risco_integrado_aps", "mean"),
        score_maximo=("score_risco_integrado_aps", "max"),
    ).reset_index()
    for classe in ["Risco crítico", "Risco alto", "Risco moderado", "Risco baixo", "Risco residual"]:
        grp[classe.lower().replace(" ", "_").replace("í", "i")] = out.assign(flag=out["classificacao_risco_integrado"].astype(str).eq(classe)).groupby("regiao_saude")["flag"].sum().reindex(grp["regiao_saude"]).fillna(0).astype(int).values
    return grp.sort_values("score_medio", ascending=False).round(1)


def componentes_risco_municipio(municipio: str, df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "municipio" not in df.columns:
        return pd.DataFrame()
    mun_norm = _normalizar_municipio(municipio)
    linha = df[df["municipio"].map(_normalizar_municipio) == mun_norm].head(1)
    if linha.empty:
        return pd.DataFrame()
    r = linha.iloc[0]
    componentes = [
        ("Vulnerabilidade social", "risco_social", "MDS/CadÚnico/Bolsa Família/BPC"),
        ("Capacidade APS", "risco_capacidade_aps", "CNES/equipes/UBS/profissionais"),
        ("Acesso territorial", "risco_acesso_territorial", "Georreferenciamento/vazios/distâncias"),
        ("Materno-infantil", "risco_materno_infantil", "SINASC/SIM"),
        ("Mortalidade", "risco_mortalidade", "SIM"),
        ("Vigilância e agravos", "risco_vigilancia", "SINAN"),
        ("Intersetorial/educação", "risco_intersetorial", "IBGE/INEP/saneamento/escolaridade"),
        ("Equidade territorial", "risco_equidade_territorial", "indígenas/quilombolas/ruralidade/assentamentos"),
    ]
    return pd.DataFrame([
        {"Eixo": nome, "Score": round(_to_num(r.get(col)), 1), "Fonte principal": fonte}
        for nome, col, fonte in componentes
    ]).sort_values("Score", ascending=False)


def obter_leitura_risco_municipio(municipio: str) -> dict[str, Any]:
    res = carregar_gestao_risco_aps()
    df = res.get("base", pd.DataFrame())
    if df.empty:
        return {"ok": False, "linha": {}, "componentes": pd.DataFrame(), "mensagem": res.get("mensagem", "Sem base de risco.")}
    mun_norm = _normalizar_municipio(municipio)
    linha = df[df["municipio"].map(_normalizar_municipio) == mun_norm].head(1)
    if linha.empty:
        return {"ok": False, "linha": {}, "componentes": pd.DataFrame(), "mensagem": "Município não encontrado no módulo de risco."}
    return {"ok": True, "linha": linha.iloc[0].to_dict(), "componentes": componentes_risco_municipio(municipio, df), "mensagem": "OK"}
