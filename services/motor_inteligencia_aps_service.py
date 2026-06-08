from __future__ import annotations

import math
import unicodedata
from typing import Any

import pandas as pd

from services.inteligencia_aps_service import carregar_inteligencia_cruzada_aps


PESOS_EIXOS_MOTOR_APS: dict[str, float] = {
    "vulnerabilidade_social": 25.0,
    "fragilidade_capacidade_aps": 20.0,
    "pressao_assistencial": 15.0,
    "vigilancia_agravos": 12.0,
    "materno_infantil": 10.0,
    "mortalidade": 10.0,
    "acesso_territorial": 5.0,
    "intersetorial_educacao": 3.0,
}

DESCRICAO_EIXOS_MOTOR_APS: dict[str, str] = {
    "vulnerabilidade_social": "MDS: CadÚnico, Bolsa Família, BPC e pobreza/extrema pobreza.",
    "fragilidade_capacidade_aps": "CNES/Equipes: suficiência relativa de equipes, UBS e profissionais frente à população.",
    "pressao_assistencial": "Demanda potencial: população por equipe/UBS, volume absoluto de vulneráveis, nascimentos e óbitos.",
    "vigilancia_agravos": "SINAN: carga relativa de agravos de notificação e sinais de vigilância territorial.",
    "materno_infantil": "SINASC/SIM: nascidos vivos, mortalidade infantil, perfil materno e riscos do ciclo gravídico-infantil.",
    "mortalidade": "SIM: mortalidade geral e grupos de causas relevantes para planejamento da APS.",
    "acesso_territorial": "Georreferenciamento: distância/vazios territoriais, quando a base estiver disponível.",
    "intersetorial_educacao": "INEP/infraestrutura escolar: marcador complementar para ações intersetoriais, PSE e território.",
}


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
    valid = s.dropna()
    if valid.empty:
        out = pd.Series([0.0] * len(s), index=s.index, dtype="float64")
    else:
        mn = valid.min()
        mx = valid.max()
        if pd.isna(mn) or pd.isna(mx) or mx == mn:
            out = pd.Series([50.0] * len(s), index=s.index, dtype="float64")
        else:
            out = ((s - mn) / (mx - mn)) * 100
    if inverter:
        out = 100 - out
    return out.fillna(0).clip(0, 100).astype("float64")


def _ponderar_componentes(componentes: list[tuple[pd.Series, float]]) -> pd.Series:
    """Média ponderada normalizada pelos componentes efetivamente disponíveis.

    Evita punir uma dimensão inteira quando uma base específica ainda não existe.
    O peso político-metodológico permanece fixo no eixo final; a normalização
    aqui ocorre apenas dentro de cada eixo temático.
    """
    if not componentes:
        return pd.Series(dtype="float64")
    idx = componentes[0][0].index
    soma = pd.Series([0.0] * len(idx), index=idx, dtype="float64")
    peso_total = 0.0
    for serie, peso in componentes:
        serie = pd.to_numeric(serie, errors="coerce").replace([float("inf"), -float("inf")], pd.NA).fillna(0).clip(0, 100)
        # Se a série inteira é zero, mantemos peso apenas se for eixo já calculado.
        # Para variáveis opcionais ausentes, a chamada deve evitar enviar a série.
        soma = soma + serie * float(peso)
        peso_total += float(peso)
    if peso_total <= 0:
        return pd.Series([0.0] * len(idx), index=idx, dtype="float64")
    return (soma / peso_total).clip(0, 100).astype("float64")


def _col_existe_com_sinal(df: pd.DataFrame, col: str) -> bool:
    if col not in df.columns:
        return False
    s = pd.to_numeric(df[col], errors="coerce")
    return bool(s.notna().any() and s.fillna(0).abs().sum() > 0)


def _componentes_disponiveis(df: pd.DataFrame, pares: list[tuple[str, float]]) -> list[tuple[pd.Series, float]]:
    comp: list[tuple[pd.Series, float]] = []
    for col, peso in pares:
        if _col_existe_com_sinal(df, col):
            comp.append((_normalizar_0_100(_num_series(df, col)), peso))
    return comp


def _classificar_motor(score: float) -> str:
    try:
        score = float(score)
    except Exception:
        score = 0.0
    if score >= 70:
        return "Prioridade crítica integrada"
    if score >= 55:
        return "Alta prioridade integrada"
    if score >= 38:
        return "Média prioridade integrada"
    if score >= 25:
        return "Atenção localizada"
    return "Monitoramento regular"


def _perfil_estrategico(row: pd.Series) -> str:
    score = float(row.get("score_motor_prioridade_aps", 0) or 0)
    social = float(row.get("eixo_vulnerabilidade_social", 0) or 0)
    frag = float(row.get("eixo_fragilidade_capacidade_aps", 0) or 0)
    pressao = float(row.get("eixo_pressao_assistencial", 0) or 0)
    vigilancia = float(row.get("eixo_vigilancia_agravos", 0) or 0)
    materno = float(row.get("eixo_materno_infantil", 0) or 0)
    volume = float(row.get("score_volume_absoluto", 0) or 0)
    pop = float(row.get("populacao", 0) or 0)

    if score >= 75 or (social >= 65 and frag >= 60 and (vigilancia >= 45 or materno >= 45 or pressao >= 55)):
        return "Crítico integrado"
    if volume >= 75 and score >= 45:
        return "Município-polo sob pressão absoluta"
    if pop > 0 and pop < 20000 and social >= 60:
        return "Município pequeno de alta vulnerabilidade"
    if frag >= 70 and pressao >= 50:
        return "Capacidade APS insuficiente frente à demanda"
    if materno >= 60:
        return "Alerta materno-infantil"
    if vigilancia >= 60:
        return "Vigilância territorial prioritária"
    if social >= 60:
        return "Alta vulnerabilidade social com monitoramento APS"
    if frag >= 65:
        return "Fragilidade estrutural APS"
    return "Situação relativa favorável/monitoramento"


def _alertas_linha(row: pd.Series) -> str:
    alertas: list[str] = []
    if float(row.get("eixo_vulnerabilidade_social", 0) or 0) >= 60:
        alertas.append("social")
    if float(row.get("eixo_fragilidade_capacidade_aps", 0) or 0) >= 60:
        alertas.append("capacidade APS")
    if float(row.get("eixo_pressao_assistencial", 0) or 0) >= 60:
        alertas.append("pressão assistencial")
    if float(row.get("eixo_vigilancia_agravos", 0) or 0) >= 55:
        alertas.append("vigilância/agravos")
    if float(row.get("eixo_materno_infantil", 0) or 0) >= 55:
        alertas.append("materno-infantil")
    if float(row.get("eixo_mortalidade", 0) or 0) >= 55:
        alertas.append("mortalidade")
    if float(row.get("eixo_acesso_territorial", 0) or 0) >= 55:
        alertas.append("acesso territorial")
    if not alertas:
        return "sem alerta dominante"
    return ", ".join(alertas)


def _recomendacao_linha(row: pd.Series) -> str:
    perfil = str(row.get("perfil_estrategico", ""))
    alertas = str(row.get("alertas_automaticos", ""))
    if "Crítico" in perfil:
        return "Abrir estudo técnico individual, validar com ERS/município e construir plano de apoio APS integrado."
    if "polo" in perfil.lower():
        return "Avaliar volume absoluto de população vulnerável, pressão regional e suficiência de rede para territórios urbanos populosos."
    if "pequeno" in perfil.lower():
        return "Priorizar apoio regional dirigido, busca ativa e pactuação de resposta proporcional ao alto risco relativo."
    if "materno" in alertas:
        return "Aprofundar linha materno-infantil: pré-natal, puericultura, nascimentos e mortalidade infantil."
    if "vigilância" in alertas or "agravos" in alertas:
        return "Integrar APS e Vigilância para busca ativa, investigação territorial e plano de enfrentamento de agravos."
    if "capacidade" in alertas:
        return "Reavaliar suficiência de equipes, UBS, profissionais e consistência dos cadastros CNES/INE."
    if "social" in alertas:
        return "Articular APS, assistência social e ações intersetoriais em territórios de maior vulnerabilidade."
    return "Manter monitoramento, usar diagnóstico municipal e validar sinais pontuais com a área técnica."


def _leitura_automatica(row: pd.Series) -> str:
    municipio = str(row.get("municipio", "O município"))
    classe = str(row.get("classificacao_motor_prioridade", "classificação não disponível"))
    perfil = str(row.get("perfil_estrategico", "perfil não definido"))
    alertas = str(row.get("alertas_automaticos", "sem alerta dominante"))
    score = float(row.get("score_motor_prioridade_aps", 0) or 0)
    top = []
    comps = [
        ("vulnerabilidade social", float(row.get("eixo_vulnerabilidade_social", 0) or 0)),
        ("fragilidade da capacidade APS", float(row.get("eixo_fragilidade_capacidade_aps", 0) or 0)),
        ("pressão assistencial", float(row.get("eixo_pressao_assistencial", 0) or 0)),
        ("vigilância/agravos", float(row.get("eixo_vigilancia_agravos", 0) or 0)),
        ("materno-infantil", float(row.get("eixo_materno_infantil", 0) or 0)),
        ("mortalidade", float(row.get("eixo_mortalidade", 0) or 0)),
        ("acesso territorial", float(row.get("eixo_acesso_territorial", 0) or 0)),
        ("intersetorial/educação", float(row.get("eixo_intersetorial_educacao", 0) or 0)),
    ]
    for nome, valor in sorted(comps, key=lambda x: x[1], reverse=True)[:3]:
        if valor >= 25:
            top.append(nome)
    motivo = ", ".join(top) if top else "indicadores atualmente disponíveis"
    return (
        f"{municipio} foi classificado como {classe} no motor de decisão APS, com score {score:.1f}/100. "
        f"O perfil predominante é: {perfil}. A prioridade é puxada principalmente por {motivo}. "
        f"Alertas automáticos: {alertas}. A leitura deve orientar validação técnica com ERS, município e áreas responsáveis antes de decisão administrativa."
    )


def metodologia_pesos_motor_aps() -> pd.DataFrame:
    rows = []
    for eixo, peso in PESOS_EIXOS_MOTOR_APS.items():
        rows.append({
            "eixo": eixo,
            "peso_percentual": peso,
            "fonte_base": DESCRICAO_EIXOS_MOTOR_APS.get(eixo, "-"),
            "justificativa": _justificativa_peso(eixo, peso),
        })
    return pd.DataFrame(rows)


def _justificativa_peso(eixo: str, peso: float) -> str:
    textos = {
        "vulnerabilidade_social": "Maior peso porque a APS deve priorizar equidade, populações vulneráveis e determinantes sociais.",
        "fragilidade_capacidade_aps": "Peso alto porque vulnerabilidade sem capacidade instalada suficiente tende a gerar desassistência.",
        "pressao_assistencial": "Captura volume e demanda potencial, evitando que grandes polos sejam invisibilizados por percentuais menores.",
        "vigilancia_agravos": "Agravos de notificação indicam necessidade de resposta territorial integrada entre APS e Vigilância.",
        "materno_infantil": "Ciclo materno-infantil é eixo sensível da APS e pode indicar falhas de acesso ou continuidade do cuidado.",
        "mortalidade": "Mortalidade orienta riscos sanitários e carga de doenças, mas exige cautela por população pequena e flutuação anual.",
        "acesso_territorial": "Acesso territorial entra como ajuste porque depende da qualidade do georreferenciamento disponível.",
        "intersetorial_educacao": "Marcador complementar de território e PSE; peso menor para não sobrepor indicadores diretamente sanitários.",
    }
    return textos.get(eixo, f"Peso metodológico de {peso}% definido para a régua preliminar.")


def carregar_motor_inteligencia_estrategica(base_dashboard: pd.DataFrame | None = None) -> pd.DataFrame:
    """Camada 13-C: motor avançado de decisão APS.

    Usa a inteligência cruzada 13-B como base, mas reorganiza os pesos considerando
    todas as bases já consolidadas: IBGE, MDS, CNES, SINASC, SIM, SINAN, INEP e
    georreferenciamento quando disponível.
    """
    df = carregar_inteligencia_cruzada_aps(base_dashboard)
    if df.empty:
        return pd.DataFrame()

    pop = _num_series(df, "populacao")

    # Volume absoluto evita esconder Cuiabá, Várzea Grande, Rondonópolis, Sinop etc.
    volume_componentes = [
        ("cadunico_pessoas", 25),
        ("bolsa_familia_pessoas", 20),
        ("bpc_total", 15),
        ("sinan_registros_considerados", 15),
        ("nascidos_vivos", 10),
        ("obitos_total", 10),
        ("populacao", 5),
    ]
    comps_volume = _componentes_disponiveis(df, volume_componentes)
    df["score_volume_absoluto"] = _ponderar_componentes(comps_volume).round(1) if comps_volume else 0.0

    # Eixo social já usa MDS e foi validado na Etapa 12-A.
    df["eixo_vulnerabilidade_social"] = _num_series(df, "score_vulnerabilidade_social_decisao").clip(0, 100).round(1)

    # Capacidade APS: leitura de fragilidade, não de capacidade favorável.
    df["eixo_fragilidade_capacidade_aps"] = _num_series(df, "score_fragilidade_capacidade_decisao").clip(0, 100).round(1)

    # Pressão: combina pressão relativa com volume absoluto para não distorcer polos populacionais.
    df["eixo_pressao_assistencial"] = (
        _num_series(df, "score_pressao_assistencial_decisao") * 0.60
        + _num_series(df, "score_volume_absoluto") * 0.40
    ).clip(0, 100).round(1)

    # Vigilância/agravos: taxas por população e agravos específicos, quando existirem.
    for col in ["sinan_tuberculose", "sinan_hanseniase", "sinan_violencia", "sinan_animais_peconhentos"]:
        if col in df.columns:
            df[f"{col}_por_10mil_motor"] = _safe_div(_num_series(df, col), pop, 10000)
    vigilancia_cols = [
        ("sinan_registros_por_10mil_decisao", 40),
        ("sinan_tuberculose_por_10mil_motor", 18),
        ("sinan_hanseniase_por_10mil_motor", 18),
        ("sinan_violencia_por_10mil_motor", 14),
        ("sinan_animais_peconhentos_por_10mil_motor", 10),
    ]
    comps_vig = _componentes_disponiveis(df, vigilancia_cols)
    if comps_vig:
        df["eixo_vigilancia_agravos"] = _ponderar_componentes(comps_vig).round(1)
    else:
        df["eixo_vigilancia_agravos"] = _num_series(df, "score_alerta_sanitario_decisao").clip(0, 100).round(1)

    # Materno-infantil: usa campos quando disponíveis; se não, aproveita o alerta sanitário 13-B.
    maternal_cols = [
        ("obitos_infantis_por_mil_nv_decisao", 35),
        ("perc_maes_adolescentes", 20),
        ("perc_prenatal_insuficiente", 20),
        ("perc_baixo_peso", 15),
        ("perc_prematuridade", 10),
        ("nascidos_por_10mil_decisao", 10),
    ]
    comps_mat = _componentes_disponiveis(df, maternal_cols)
    df["eixo_materno_infantil"] = _ponderar_componentes(comps_mat).round(1) if comps_mat else (_num_series(df, "score_alerta_sanitario_decisao") * 0.35).clip(0, 100).round(1)

    # Mortalidade geral e por grupos de causas, quando existirem.
    morte_cols = [
        ("obitos_por_10mil_decisao", 35),
        ("obitos_causas_externas", 15),
        ("obitos_cardiovasculares", 15),
        ("obitos_neoplasias", 10),
        ("obitos_respiratorias", 10),
        ("mortes_maternas", 15),
    ]
    # transformar contagens de causas em taxas temporárias, preservando se já existirem taxas
    for c, _ in list(morte_cols):
        if c in df.columns and c not in {"obitos_por_10mil_decisao"}:
            df[f"{c}_por_10mil_motor"] = _safe_div(_num_series(df, c), pop, 10000)
    morte_cols_calc = []
    for c, peso in morte_cols:
        if c == "obitos_por_10mil_decisao":
            morte_cols_calc.append((c, peso))
        elif f"{c}_por_10mil_motor" in df.columns:
            morte_cols_calc.append((f"{c}_por_10mil_motor", peso))
    comps_morte = _componentes_disponiveis(df, morte_cols_calc)
    df["eixo_mortalidade"] = _ponderar_componentes(comps_morte).round(1) if comps_morte else _normalizar_0_100(_num_series(df, "obitos_por_10mil_decisao")).round(1)

    # Acesso territorial entra como ajuste; se base territorial ainda for limitada, não força alerta artificial.
    df["eixo_acesso_territorial"] = _num_series(df, "score_acesso_territorial_decisao").clip(0, 100).round(1)

    # Intersetorial/educação: infraestrutura escolar como marcador complementar.
    inter_cols = [
        ("pct_escolas_sem_esgoto_decisao", 45),
        ("pct_escolas_sem_internet_decisao", 25),
        ("escolas_rurais", 15),
        ("matriculas_rurais", 15),
    ]
    comps_inter = _componentes_disponiveis(df, inter_cols)
    df["eixo_intersetorial_educacao"] = _ponderar_componentes(comps_inter).round(1) if comps_inter else 0.0

    pesos = PESOS_EIXOS_MOTOR_APS
    df["score_motor_prioridade_aps_bruto"] = (
        df["eixo_vulnerabilidade_social"] * pesos["vulnerabilidade_social"]
        + df["eixo_fragilidade_capacidade_aps"] * pesos["fragilidade_capacidade_aps"]
        + df["eixo_pressao_assistencial"] * pesos["pressao_assistencial"]
        + df["eixo_vigilancia_agravos"] * pesos["vigilancia_agravos"]
        + df["eixo_materno_infantil"] * pesos["materno_infantil"]
        + df["eixo_mortalidade"] * pesos["mortalidade"]
        + df["eixo_acesso_territorial"] * pesos["acesso_territorial"]
        + df["eixo_intersetorial_educacao"] * pesos["intersetorial_educacao"]
    ) / 100.0
    df["score_motor_prioridade_aps_bruto"] = df["score_motor_prioridade_aps_bruto"].clip(0, 100).round(1)

    # A régua final combina o score absoluto com uma calibração relativa estadual.
    # Isso evita uma tela sem priorização quando todos os municípios ficam concentrados
    # em faixas médias e preserva a ideia de ranking comparativo para decisão.
    df["score_motor_prioridade_relativo"] = _normalizar_0_100(df["score_motor_prioridade_aps_bruto"]).round(1)
    df["score_motor_prioridade_aps"] = (
        df["score_motor_prioridade_aps_bruto"] * 0.55
        + df["score_motor_prioridade_relativo"] * 0.45
    ).clip(0, 100).round(1)

    df["classificacao_motor_prioridade"] = df["score_motor_prioridade_aps"].map(_classificar_motor)
    df["perfil_estrategico"] = df.apply(_perfil_estrategico, axis=1)
    df["alertas_automaticos"] = df.apply(_alertas_linha, axis=1)
    df["recomendacao_motor"] = df.apply(_recomendacao_linha, axis=1)
    df["leitura_automatica_motor"] = df.apply(_leitura_automatica, axis=1)

    # Comparações: ranking relativo, absoluto e final.
    df = df.sort_values("score_motor_prioridade_aps", ascending=False).reset_index(drop=True)
    df["ranking_motor_prioridade"] = range(1, len(df) + 1)
    df["ranking_volume_absoluto"] = df["score_volume_absoluto"].rank(ascending=False, method="min").astype(int)
    df["ranking_risco_relativo"] = df["eixo_vulnerabilidade_social"].rank(ascending=False, method="min").astype(int)

    # Cobertura de bases por eixo com sinal, para transparência.
    eixos = [
        "eixo_vulnerabilidade_social", "eixo_fragilidade_capacidade_aps", "eixo_pressao_assistencial",
        "eixo_vigilancia_agravos", "eixo_materno_infantil", "eixo_mortalidade", "eixo_acesso_territorial", "eixo_intersetorial_educacao",
    ]
    df["cobertura_eixos_motor"] = df[eixos].gt(0).sum(axis=1)
    df["indice_cobertura_motor"] = (df["cobertura_eixos_motor"] / len(eixos) * 100).round(1)

    return df


def resumo_motor_inteligencia(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {}
    classe = df.get("classificacao_motor_prioridade", pd.Series(dtype=str)).astype(str)
    return {
        "municipios": int(len(df)),
        "criticos": int((classe == "Prioridade crítica integrada").sum()),
        "alta": int((classe == "Alta prioridade integrada").sum()),
        "media": int((classe == "Média prioridade integrada").sum()),
        "score_medio": round(float(_num_series(df, "score_motor_prioridade_aps").mean()), 1),
        "social_medio": round(float(_num_series(df, "eixo_vulnerabilidade_social").mean()), 1),
        "capacidade_medio": round(float(_num_series(df, "eixo_fragilidade_capacidade_aps").mean()), 1),
        "pressao_medio": round(float(_num_series(df, "eixo_pressao_assistencial").mean()), 1),
        "cobertura_media": round(float(_num_series(df, "indice_cobertura_motor").mean()), 1),
    }


def gerar_sintese_motor(df: pd.DataFrame) -> str:
    if df.empty:
        return "Não há dados suficientes para o motor de inteligência APS."
    r = resumo_motor_inteligencia(df)
    top = df.head(5)["municipio"].astype(str).tolist() if "municipio" in df.columns else []
    perfis = []
    if "perfil_estrategico" in df.columns:
        perfis = df["perfil_estrategico"].value_counts().head(3).index.astype(str).tolist()
    return (
        f"O motor avançado avaliou {r.get('municipios', 0)} municípios com pesos recalibrados para todas as bases consolidadas. "
        f"Há {r.get('criticos', 0)} municípios em prioridade crítica integrada e {r.get('alta', 0)} em alta prioridade integrada. "
        f"Os primeiros sinais de decisão aparecem em {', '.join(top) if top else 'municípios do topo do ranking'}. "
        f"Os perfis estratégicos mais frequentes entre os municípios são: {', '.join(perfis) if perfis else 'a definir conforme disponibilidade dos dados'}. "
        "A leitura combina risco relativo, volume absoluto e capacidade de resposta para evitar que municípios pequenos de alto risco ou polos populosos sejam subestimados."
    )


def ranking_regional_motor(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "regiao_saude" not in df.columns:
        return pd.DataFrame()
    out = df.groupby("regiao_saude", dropna=False).agg(
        municipios=("municipio", "count"),
        score_medio=("score_motor_prioridade_aps", "mean"),
        vulnerabilidade_media=("eixo_vulnerabilidade_social", "mean"),
        fragilidade_media=("eixo_fragilidade_capacidade_aps", "mean"),
        pressao_media=("eixo_pressao_assistencial", "mean"),
        vigilancia_media=("eixo_vigilancia_agravos", "mean"),
        materno_media=("eixo_materno_infantil", "mean"),
        populacao=("populacao", "sum"),
    ).reset_index()
    for c in out.columns:
        if c not in ["regiao_saude", "municipios"]:
            out[c] = pd.to_numeric(out[c], errors="coerce").round(1)
    altacrit = df[df["classificacao_motor_prioridade"].isin(["Prioridade crítica integrada", "Alta prioridade integrada"])]
    out["municipios_alta_critica"] = altacrit.groupby("regiao_saude").size().reindex(out["regiao_saude"]).fillna(0).astype(int).values
    return out.sort_values("score_medio", ascending=False)


def resumo_perfis_motor(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "perfil_estrategico" not in df.columns:
        return pd.DataFrame()
    out = df.groupby("perfil_estrategico", dropna=False).agg(
        municipios=("municipio", "count"),
        score_medio=("score_motor_prioridade_aps", "mean"),
        populacao=("populacao", "sum"),
    ).reset_index()
    out["score_medio"] = pd.to_numeric(out["score_medio"], errors="coerce").round(1)
    return out.sort_values(["score_medio", "municipios"], ascending=False)


def obter_leitura_motor_municipio(municipio: str, base_dashboard: pd.DataFrame | None = None) -> dict[str, Any]:
    df = carregar_motor_inteligencia_estrategica(base_dashboard)
    if df.empty or "municipio" not in df.columns:
        return {"ok": False, "erro": "Motor de inteligência não disponível."}
    alvo = _chave(municipio)
    tmp = df.copy()
    tmp["_chave"] = tmp["municipio"].map(_chave)
    linha = tmp[tmp["_chave"] == alvo]
    if linha.empty:
        return {"ok": False, "erro": "Município não localizado no motor de inteligência."}
    row = linha.iloc[0].drop(labels=["_chave"], errors="ignore").to_dict()
    comp = pd.DataFrame([
        {"eixo": "Vulnerabilidade social", "score": row.get("eixo_vulnerabilidade_social", 0), "peso_%": PESOS_EIXOS_MOTOR_APS["vulnerabilidade_social"], "fonte": DESCRICAO_EIXOS_MOTOR_APS["vulnerabilidade_social"]},
        {"eixo": "Fragilidade capacidade APS", "score": row.get("eixo_fragilidade_capacidade_aps", 0), "peso_%": PESOS_EIXOS_MOTOR_APS["fragilidade_capacidade_aps"], "fonte": DESCRICAO_EIXOS_MOTOR_APS["fragilidade_capacidade_aps"]},
        {"eixo": "Pressão assistencial", "score": row.get("eixo_pressao_assistencial", 0), "peso_%": PESOS_EIXOS_MOTOR_APS["pressao_assistencial"], "fonte": DESCRICAO_EIXOS_MOTOR_APS["pressao_assistencial"]},
        {"eixo": "Vigilância/agravos", "score": row.get("eixo_vigilancia_agravos", 0), "peso_%": PESOS_EIXOS_MOTOR_APS["vigilancia_agravos"], "fonte": DESCRICAO_EIXOS_MOTOR_APS["vigilancia_agravos"]},
        {"eixo": "Materno-infantil", "score": row.get("eixo_materno_infantil", 0), "peso_%": PESOS_EIXOS_MOTOR_APS["materno_infantil"], "fonte": DESCRICAO_EIXOS_MOTOR_APS["materno_infantil"]},
        {"eixo": "Mortalidade", "score": row.get("eixo_mortalidade", 0), "peso_%": PESOS_EIXOS_MOTOR_APS["mortalidade"], "fonte": DESCRICAO_EIXOS_MOTOR_APS["mortalidade"]},
        {"eixo": "Acesso territorial", "score": row.get("eixo_acesso_territorial", 0), "peso_%": PESOS_EIXOS_MOTOR_APS["acesso_territorial"], "fonte": DESCRICAO_EIXOS_MOTOR_APS["acesso_territorial"]},
        {"eixo": "Intersetorial/educação", "score": row.get("eixo_intersetorial_educacao", 0), "peso_%": PESOS_EIXOS_MOTOR_APS["intersetorial_educacao"], "fonte": DESCRICAO_EIXOS_MOTOR_APS["intersetorial_educacao"]},
    ])
    comp["score"] = pd.to_numeric(comp["score"], errors="coerce").round(1)
    return {"ok": True, "linha": row, "componentes": comp}
