from __future__ import annotations

import math
import unicodedata
from typing import Any

import pandas as pd

from database.queries import read_table
from services.dashboard_aps_service import carregar_base_dashboard, CODIGOS_EQUIPES_APS


def _norm(valor: Any) -> str:
    texto = str(valor or "").strip().upper()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    return " ".join(texto.split())


def _num(valor: Any, default: float = 0.0) -> float:
    try:
        if valor is None:
            return default
        if isinstance(valor, str):
            valor = valor.strip().replace("%", "").replace(".", "").replace(",", ".")
            if not valor:
                return default
        out = float(valor)
        if math.isnan(out):
            return default
        return out
    except Exception:
        return default


def _fmt_int(valor: Any) -> str:
    return f"{int(round(_num(valor))):,}".replace(",", ".")


def _fmt_dec(valor: Any, casas: int = 1) -> str:
    return f"{_num(valor):,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_pct(valor: Any, casas: int = 1) -> str:
    return f"{_fmt_dec(valor, casas)}%"


def _linha(base: pd.DataFrame, municipio: str) -> dict[str, Any]:
    if base is None or base.empty or "municipio" not in base.columns:
        return {}
    chave = _norm(municipio)
    tmp = base.copy()
    tmp["_chave_mun"] = tmp["municipio"].map(_norm)
    filtro = tmp["_chave_mun"].eq(chave)
    if not filtro.any():
        filtro = tmp["_chave_mun"].str.contains(chave, regex=False, na=False)
    if not filtro.any():
        return {}
    return tmp.loc[filtro].drop(columns=["_chave_mun"], errors="ignore").iloc[0].to_dict()


def _subset(base: pd.DataFrame, municipio: str) -> pd.DataFrame:
    if base is None or base.empty or "municipio" not in base.columns:
        return pd.DataFrame()
    chave = _norm(municipio)
    tmp = base.copy()
    tmp["_chave_mun"] = tmp["municipio"].map(_norm)
    out = tmp[tmp["_chave_mun"].eq(chave)].drop(columns=["_chave_mun"], errors="ignore")
    return out.copy()


def _classificar(valor: float, cortes: tuple[float, float, float], labels: tuple[str, str, str, str]) -> str:
    if valor >= cortes[2]:
        return labels[3]
    if valor >= cortes[1]:
        return labels[2]
    if valor >= cortes[0]:
        return labels[1]
    return labels[0]


def listar_municipios_analise() -> list[str]:
    base = carregar_base_dashboard()
    if base is None or base.empty:
        base = read_table("base_municipal_consolidada")
    if base.empty or "municipio" not in base.columns:
        return []
    return sorted(base["municipio"].dropna().astype(str).unique())


def carregar_fontes_status() -> pd.DataFrame:
    tabelas = [
        ("base_municipal_consolidada", "Base municipal consolidada", "estrutura APS, população e scores"),
        ("estabelecimentos_saude", "Estabelecimentos de saúde", "UBS/unidades, CNES e georreferência"),
        ("equipes_aps", "Equipes APS", "códigos 70, 71, 72, 73, 74 e 76"),
        ("profissionais_cnes", "Profissionais CNES", "vínculos profissionais por equipe/INE"),
        ("mds_cadunico_bolsa_familia_municipal", "MDS/VIS DATA", "CadÚnico, Bolsa Família, BPC e renda social"),
        ("base_publica_inep_censo_escolar_municipal", "INEP Censo Escolar", "escolas, matrículas e infraestrutura escolar"),
        ("base_publica_sinasc_municipal", "SINASC", "nascidos vivos e perfil materno-infantil"),
        ("base_publica_sim_mortalidade_municipal", "SIM", "mortalidade geral e grupos de causas"),
        ("base_publica_sinan_municipal", "SINAN", "agravos, notificações, óbitos e hospitalizações"),
        ("dados_mt_assentamentos", "Assentamentos", "territórios rurais especiais"),
        ("dados_mt_terras_indigenas", "Terras indígenas", "equidade territorial e povos originários"),
        ("dados_mt_areas_contaminadas", "Áreas contaminadas", "risco ambiental e vigilância territorial"),
        ("base_publica_ibge_setores_basico", "Setores censitários IBGE", "bairros, setores, ruralidade e território"),
    ]
    linhas = []
    for tabela, nome, uso in tabelas:
        df = read_table(tabela)
        if df.empty:
            status = "Ausente ou vazio"
            municipios = 0
            registros = 0
        else:
            status = "Carregada"
            registros = len(df)
            municipios = df["municipio"].nunique() if "municipio" in df.columns else (
                df["NM_MUN"].nunique() if "NM_MUN" in df.columns else 0
            )
        linhas.append({
            "base": nome,
            "tabela": tabela,
            "status": status,
            "registros": registros,
            "municípios": municipios,
            "uso analítico": uso,
        })
    return pd.DataFrame(linhas)


def montar_raio_x(row: dict[str, Any]) -> pd.DataFrame:
    pop = _num(row.get("populacao"))
    equipes = _num(row.get("total_equipes_aps"))
    ubs = _num(row.get("total_ubs"))
    prof = _num(row.get("total_profissionais_aps"))
    return pd.DataFrame([
        {"dimensão": "Demografia", "indicador": "População considerada", "valor": _fmt_int(pop), "leitura": "base populacional usada nos indicadores de pressão"},
        {"dimensão": "Capacidade APS", "indicador": "UBS/estabelecimentos", "valor": _fmt_int(ubs), "leitura": "estrutura física informada na base de saúde"},
        {"dimensão": "Capacidade APS", "indicador": "Equipes APS", "valor": _fmt_int(equipes), "leitura": "soma dos códigos 70, 71, 72, 73, 74 e 76"},
        {"dimensão": "Capacidade APS", "indicador": "Vínculos profissionais", "valor": _fmt_int(prof), "leitura": "vínculos CNES associados à APS"},
        {"dimensão": "Pressão", "indicador": "Habitantes por equipe", "valor": _fmt_dec(row.get("populacao_por_equipe")), "leitura": "quanto maior, maior a pressão potencial sobre equipes"},
        {"dimensão": "Pressão", "indicador": "Habitantes por UBS", "valor": _fmt_dec(row.get("populacao_por_ubs")), "leitura": "quanto maior, maior a pressão sobre estrutura física"},
        {"dimensão": "Acesso", "indicador": "Distância média território → UBS", "valor": f"{_fmt_dec(row.get('distancia_media_territorios_km'))} km", "leitura": "média dos territórios mapeados até a UBS/APS mais próxima"},
        {"dimensão": "Acesso", "indicador": "Maior distância território → UBS", "valor": f"{_fmt_dec(row.get('distancia_maxima_territorios_km'))} km", "leitura": "ponto de atenção para validação por rota real"},
        {"dimensão": "Território", "indicador": "Territórios mapeados", "valor": _fmt_int(row.get("territorios_mapeados")), "leitura": "bairros/localidades/setores disponíveis para leitura intramunicipal"},
        {"dimensão": "Equidade", "indicador": "Terras indígenas", "valor": _fmt_int(row.get("terras_indigenas_qtd_registros")), "leitura": "camada especial de equidade territorial"},
        {"dimensão": "Equidade", "indicador": "Assentamentos", "valor": _fmt_int(row.get("assentamentos_qtd_registros")), "leitura": "camada rural especial para acesso e logística"},
        {"dimensão": "Governança", "indicador": "Qualidade dos dados", "valor": _fmt_pct(row.get("qualidade_dados_score")), "leitura": str(row.get("classe_qualidade_dados", "-"))},
    ])


def montar_radar(row: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame([
        {"dimensão": "Acesso territorial", "score": _num(row.get("score_acesso_territorial")), "interpretação": "distância, vazios e territórios distantes da UBS"},
        {"dimensão": "Pressão assistencial", "score": _num(row.get("score_vazio_assistencial")), "interpretação": "população por equipe/UBS e pressão potencial"},
        {"dimensão": "Vulnerabilidade social", "score": _num(row.get("score_vulnerabilidade_social")), "interpretação": "renda, CadÚnico, saneamento e escolaridade"},
        {"dimensão": "Capacidade instalada", "score": _num(row.get("score_fragilidade_capacidade")), "interpretação": "fragilidade relativa de UBS, equipes e profissionais"},
        {"dimensão": "Equidade territorial", "score": _num(row.get("score_equidade_territorial")), "interpretação": "ruralidade, assentamentos, terras indígenas e barreiras territoriais"},
    ])


def montar_equipes(row: dict[str, Any], municipio: str) -> pd.DataFrame:
    equipes = read_table("equipes_aps")
    det = _subset(equipes, municipio)
    linhas = []
    for codigo, descricao in CODIGOS_EQUIPES_APS.items():
        qtd_row = _num(row.get(f"total_equipes_{codigo}"))
        qtd_det = 0
        if not det.empty and "codigo_tipo_equipe" in det.columns:
            qtd_det = int(det["codigo_tipo_equipe"].astype(str).str.extract(r"(\d+)")[0].eq(codigo).sum())
        linhas.append({
            "código": codigo,
            "tipo": descricao,
            "quantidade consolidada": int(qtd_row),
            "registros detalhados": int(qtd_det),
            "observação": "ok" if int(qtd_row) == int(qtd_det) or qtd_det == 0 else "validar divergência entre consolidado e detalhamento",
        })
    return pd.DataFrame(linhas)


def montar_perfil_social(row: dict[str, Any], municipio: str) -> pd.DataFrame:
    mds = _linha(read_table("mds_cadunico_bolsa_familia_municipal"), municipio)
    social = [
        ("CadÚnico", "Famílias cadastradas", mds.get("cadunico_familias", row.get("cadunico_familias"))),
        ("CadÚnico", "Pessoas cadastradas", mds.get("cadunico_pessoas", row.get("cadunico_pessoas"))),
        ("CadÚnico", "Famílias em pobreza", mds.get("cadunico_familias_pobreza", row.get("cadunico_familias_pobreza"))),
        ("CadÚnico", "Famílias em extrema pobreza", mds.get("cadunico_familias_extrema_pobreza", row.get("cadunico_familias_extrema_pobreza"))),
        ("Bolsa Família", "Famílias beneficiárias", mds.get("bolsa_familia_familias", row.get("bolsa_familia_familias"))),
        ("Bolsa Família", "Pessoas beneficiárias", mds.get("bolsa_familia_pessoas", row.get("bolsa_familia_pessoas"))),
        ("BPC", "Beneficiários BPC", mds.get("bpc_total", row.get("bpc_total"))),
        ("IBGE/Censo", "Taxa de analfabetismo estimada", row.get("taxa_analfabetismo_estimado_pct")),
        ("IBGE/Censo", "Baixo nível de instrução", row.get("nivel_instrucao_baixo_pct")),
        ("IBGE/Censo", "Renda Censo 2022/ref.", row.get("renda_censo_2022_ref", row.get("renda_censo_2022"))),
        ("IBGE/Censo", "% RDPC até 1/2 SM", row.get("pct_rdpc_ate_1_2_sm_2022")),
        ("IBGE/Censo", "Vulnerabilidade saneamento", row.get("indice_vulnerabilidade_saneamento_2022")),
        ("IBGE/Censo", "Percentual rural", row.get("percentual_rural_2022")),
    ]
    linhas = []
    for eixo, indicador, valor in social:
        if valor is None or str(valor) == "nan":
            exib = "-"
        elif "Taxa" in indicador or "%" in indicador or "Percentual" in indicador or "Vulnerabilidade" in indicador or "nível" in indicador:
            exib = _fmt_pct(valor)
        elif "Renda" in indicador:
            exib = _fmt_dec(valor, 2)
        else:
            exib = _fmt_int(valor)
        linhas.append({"eixo": eixo, "indicador": indicador, "valor": exib, "valor_numérico": _num(valor)})
    return pd.DataFrame(linhas)


def montar_perfil_epidemiologico(municipio: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    sinasc = _linha(read_table("base_publica_sinasc_municipal"), municipio)
    sim = _linha(read_table("base_publica_sim_mortalidade_municipal"), municipio)
    linhas = []
    for fonte, row, itens in [
        ("SINASC", sinasc, [
            ("Nascidos vivos", "nascidos_vivos"),
            ("Baixo peso", "baixo_peso"),
            ("% baixo peso", "perc_baixo_peso"),
            ("Prematuros", "prematuros"),
            ("% prematuridade", "perc_prematuros"),
            ("Mães adolescentes", "maes_adolescentes"),
            ("% mães adolescentes", "perc_maes_adolescentes"),
            ("Pré-natal insuficiente", "prenatal_insuficiente"),
            ("% pré-natal insuficiente", "perc_prenatal_insuficiente"),
            ("Cesáreas", "cesareas"),
            ("% cesáreas", "perc_cesareas"),
        ]),
        ("SIM", sim, [
            ("Óbitos totais", "obitos_total"),
            ("Óbitos infantis", "obitos_infantis"),
            ("% óbitos infantis", "perc_obitos_infantis"),
            ("Óbitos por causas externas", "obitos_causas_externas"),
            ("% causas externas", "perc_obitos_causas_externas"),
            ("Óbitos cardiovasculares", "obitos_cardiovasculares"),
            ("% cardiovasculares", "perc_obitos_cardiovasculares"),
            ("Óbitos respiratórios", "obitos_respiratorias"),
            ("% respiratórias", "perc_obitos_respiratorias"),
            ("Mortes maternas", "mortes_maternas"),
        ]),
    ]:
        for indicador, campo in itens:
            valor = row.get(campo)
            if valor is None or str(valor) == "nan":
                exib = "-"
            elif indicador.startswith("%"):
                exib = _fmt_pct(valor)
            else:
                exib = _fmt_int(valor)
            linhas.append({"fonte": fonte, "indicador": indicador, "valor": exib, "valor_numérico": _num(valor)})

    sinan = _subset(read_table("base_publica_sinan_municipal"), municipio)
    if not sinan.empty:
        cols = [c for c in ["agravo", "ano_referencia", "notificacoes", "casos_confirmados_provaveis", "obitos", "hospitalizacoes", "perc_obitos", "perc_hospitalizacoes"] if c in sinan.columns]
        sinan = sinan[cols].sort_values([c for c in ["notificacoes"] if c in cols], ascending=False)
    return pd.DataFrame(linhas), sinan


def montar_perfil_educacional(municipio: str) -> pd.DataFrame:
    ine = _linha(read_table("base_publica_inep_censo_escolar_municipal"), municipio)
    itens = [
        ("Escolas", "Total de escolas", "escolas_total"),
        ("Escolas", "Escolas públicas", "escolas_publicas"),
        ("Escolas", "Escolas rurais", "escolas_rurais"),
        ("Escolas", "Escolas urbanas", "escolas_urbanas"),
        ("Matrículas", "Matrículas totais", "matriculas_total"),
        ("Infraestrutura", "Escolas com internet", "escolas_com_internet"),
        ("Infraestrutura", "Escolas com água de rede", "escolas_com_agua_rede"),
        ("Infraestrutura", "Escolas com esgoto", "escolas_com_esgoto"),
        ("Infraestrutura", "Escolas com biblioteca/sala de leitura", "escolas_com_biblioteca_sala_leitura"),
        ("Infraestrutura", "Escolas com laboratório de informática", "escolas_com_lab_informatica"),
        ("Infraestrutura", "Escolas com quadra", "escolas_com_quadra"),
    ]
    linhas = []
    for eixo, indicador, campo in itens:
        valor = ine.get(campo)
        linhas.append({"eixo": eixo, "indicador": indicador, "valor": "-" if valor is None else _fmt_int(valor), "valor_numérico": _num(valor)})
    return pd.DataFrame(linhas)


def montar_territorio(municipio: str, row: dict[str, Any]) -> pd.DataFrame:
    bases = [
        ("Setores/bairros/localidades mapeados", row.get("territorios_mapeados"), "base territorial para leitura intramunicipal"),
        ("Territórios críticos/distantes da UBS", row.get("territorios_criticos_distantes"), "prioridade para validação de rota real e referência APS"),
        ("População em territórios críticos/distantes", row.get("populacao_territorios_criticos_distantes"), "população potencialmente exposta a barreira territorial"),
        ("Assentamentos registrados", row.get("assentamentos_qtd_registros"), "área rural especial"),
        ("Assentamentos críticos/distantes", row.get("assentamentos_criticos_distantes"), "área rural com maior barreira de acesso"),
        ("Terras indígenas/interseções", row.get("terras_indigenas_qtd_registros"), "equidade territorial"),
        ("Áreas contaminadas", row.get("areas_contaminadas_qtd_registros"), "vigilância ambiental/territorial"),
    ]
    return pd.DataFrame([{"indicador": a, "valor": _fmt_int(b), "leitura": c} for a, b, c in bases])


def montar_alertas(row: dict[str, Any], social: pd.DataFrame, epi: pd.DataFrame, sinan: pd.DataFrame) -> pd.DataFrame:
    alertas = []
    def add(eixo: str, criticidade: str, achado: str, encaminhamento: str):
        alertas.append({"eixo": eixo, "criticidade": criticidade, "achado": achado, "encaminhamento sugerido": encaminhamento})

    if _num(row.get("score_prioridade_integrada")) >= 65:
        add("Prioridade integrada", "Alta", f"Score integrado {_fmt_dec(row.get('score_prioridade_integrada'))}; classe {row.get('classe_prioridade', '-')}.", "Validar o município em reunião técnica com APS, ERS e planejamento.")
    if _num(row.get("score_acesso_territorial")) >= 60:
        add("Acesso territorial", "Alta", f"Score de acesso {_fmt_dec(row.get('score_acesso_territorial'))}; distância média {_fmt_dec(row.get('distancia_media_territorios_km'))} km.", "Checar rotas reais, população exposta, agenda rural, UBS de referência e necessidade de unidade volante/equipe itinerante.")
    if _num(row.get("score_fragilidade_capacidade")) >= 60:
        add("Capacidade APS", "Alta", f"Fragilidade da capacidade {_fmt_dec(row.get('score_fragilidade_capacidade'))}.", "Revisar composição de equipes, CNES, carga horária, vínculos e distribuição por UBS/território.")
    if _num(row.get("score_vulnerabilidade_social")) >= 55:
        add("Vulnerabilidade social", "Média/Alta", f"Vulnerabilidade social {_fmt_dec(row.get('score_vulnerabilidade_social'))}.", "Cruzar APS com assistência social, CadÚnico, educação e saneamento.")
    if _num(row.get("taxa_analfabetismo_estimado_pct")) >= 12:
        add("Educação e comunicação em saúde", "Média/Alta", f"Analfabetismo estimado {_fmt_pct(row.get('taxa_analfabetismo_estimado_pct'))}.", "Adequar comunicação, busca ativa e educação em saúde com linguagem simples e apoio territorial.")
    if _num(row.get("percentual_rural_2022")) >= 40:
        add("Ruralidade", "Média/Alta", f"População rural estimada {_fmt_pct(row.get('percentual_rural_2022'))}.", "Planejar agenda rural, transporte sanitário, visitas e pactuação de referência.")
    if not epi.empty:
        prenatal = epi.loc[epi["indicador"].eq("% pré-natal insuficiente"), "valor_numérico"]
        if not prenatal.empty and prenatal.iloc[0] >= 20:
            add("Materno-infantil", "Alta", f"Pré-natal insuficiente em {_fmt_pct(prenatal.iloc[0])} dos nascidos vivos.", "Priorizar captação precoce, busca ativa de gestantes e monitoramento mensal.")
        infantil = epi.loc[epi["indicador"].eq("Óbitos infantis"), "valor_numérico"]
        if not infantil.empty and infantil.iloc[0] > 0:
            add("Mortalidade infantil", "Alta", f"{_fmt_int(infantil.iloc[0])} óbito(s) infantil(is) na base SIM.", "Revisar investigação, pré-natal, puericultura e rede de atenção.")
    if not sinan.empty and "notificacoes" in sinan.columns:
        top = sinan.sort_values("notificacoes", ascending=False).head(1).iloc[0].to_dict()
        add("Vigilância/SINAN", "Monitoramento", f"Maior volume de notificações: {top.get('agravo', '-')}, com {_fmt_int(top.get('notificacoes'))} registros.", "Articular APS e vigilância para prevenção, acompanhamento e qualidade da notificação.")

    if not alertas:
        add("Monitoramento geral", "Regular", "Não foram encontrados alertas críticos pelos cortes atuais.", "Manter atualização das bases e validação técnica periódica.")
    return pd.DataFrame(alertas)


def montar_recomendacoes(row: dict[str, Any]) -> pd.DataFrame:
    linhas = [
        ("1. Validar a base", "Conferir CNES, INE, UBS, profissionais, coordenadas e competência das bases antes de decisão formal."),
        ("2. Ler território antes de obra", "Usar distância, população exposta, assentamentos, ruralidade e vazios para decidir se a resposta é UBS, equipe, agenda rural ou reorganização."),
        ("3. Priorizar população vulnerável", "Cruzar CadÚnico/Bolsa Família/BPC, escolaridade, saneamento, ruralidade e povos/territórios especiais."),
        ("4. Integrar APS e vigilância", "Conectar achados de SINASC, SIM e SINAN com busca ativa, linhas de cuidado e monitoramento das equipes."),
        ("5. Pactuar com ERS e município", "Transformar a leitura em plano de ação com responsáveis, prazo, evidência e acompanhamento regional."),
    ]
    if _num(row.get("score_acesso_territorial")) >= 60:
        linhas.insert(2, ("Ação focal: acesso", "Validar territórios distantes, rotas reais, transporte, equipe itinerante e UBS de referência."))
    if _num(row.get("score_fragilidade_capacidade")) >= 60:
        linhas.insert(2, ("Ação focal: capacidade", "Conferir déficit de equipes, profissionais, carga horária, unidade de lotação e vazios por código CNES/INE."))
    return pd.DataFrame([{"prioridade": a, "recomendação": b} for a, b in linhas])


def gerar_texto_relatorio(row: dict[str, Any], alertas: pd.DataFrame) -> str:
    municipio = str(row.get("municipio", "município selecionado"))
    regiao = str(row.get("regiao_saude", "-"))
    classe = str(row.get("classe_prioridade", "-"))
    score = _fmt_dec(row.get("score_prioridade_integrada"))
    pos = _fmt_int(row.get("posicao_prioridade"))
    motivo = str(row.get("principal_motivo_prioridade", "-"))
    fatores = str(row.get("fatores_prioritarios", "-"))

    principais = "; ".join(alertas.head(4)["achado"].astype(str).tolist()) if not alertas.empty else "sem alerta crítico pelos cortes atuais"

    return (
        f"O município de {municipio}, integrante da Região de Saúde {regiao}, apresenta classificação integrada "
        f"'{classe}', com score {score} e posição {pos} no ranking estadual do sistema. A leitura consolidada indica "
        f"como principal motivo: {motivo}. Os fatores que mais influenciaram a pontuação foram: {fatores}.\n\n"
        f"Na dimensão de capacidade e acesso, a base registra {_fmt_int(row.get('populacao'))} habitantes, "
        f"{_fmt_int(row.get('total_ubs'))} UBS/estabelecimentos, {_fmt_int(row.get('total_equipes_aps'))} equipes APS "
        f"e {_fmt_int(row.get('total_profissionais_aps'))} vínculos profissionais. A pressão estimada é de "
        f"{_fmt_dec(row.get('populacao_por_equipe'))} habitantes por equipe e {_fmt_dec(row.get('populacao_por_ubs'))} "
        f"habitantes por UBS. A distância média dos territórios mapeados até a UBS/APS mais próxima é de "
        f"{_fmt_dec(row.get('distancia_media_territorios_km'))} km, com maior distância de "
        f"{_fmt_dec(row.get('distancia_maxima_territorios_km'))} km.\n\n"
        f"Os principais alertas encontrados foram: {principais}. Essa leitura deve orientar validação técnica com a ERS "
        f"e o município, cruzando CNES/INE, CadÚnico/MDS, IBGE, INEP, SINASC, SIM, SINAN e camadas territoriais. "
        f"O relatório é instrumento de triagem qualificada para planejamento, pactuação regional e priorização de políticas públicas; "
        f"não substitui análise normativa, visita técnica, validação de rota real nem critérios oficiais de habilitação e financiamento."
    )


def analisar_coerencia_indicadores(municipio: str | None = None) -> pd.DataFrame:
    base = carregar_base_dashboard()
    if base.empty:
        base = read_table("base_municipal_consolidada")
    if municipio:
        row = _linha(base, municipio)
        base = pd.DataFrame([row]) if row else pd.DataFrame()

    linhas = []
    if base.empty:
        return pd.DataFrame([{"item": "Base integrada", "status": "crítico", "achado": "Base municipal não carregada.", "ação": "Gerar base consolidada."}])

    total_mun = base["municipio"].nunique() if "municipio" in base.columns else 0
    linhas.append({"item": "Cobertura municipal", "status": "ok" if total_mun == 142 else "atenção", "achado": f"{total_mun} município(s) na base integrada.", "ação": "Esperado para MT: 142 municípios."})

    for _, r in base.iterrows():
        mun = r.get("municipio", "-")
        soma_codigos = sum(_num(r.get(f"total_equipes_{c}")) for c in CODIGOS_EQUIPES_APS)
        total_eq = _num(r.get("total_equipes_aps"))
        if total_eq and abs(soma_codigos - total_eq) > 1:
            linhas.append({"item": f"{mun} — equipes APS", "status": "atenção", "achado": f"Total de equipes ({_fmt_int(total_eq)}) difere da soma dos códigos ({_fmt_int(soma_codigos)}).", "ação": "Validar consolidação CNES/INE."})
        pop = _num(r.get("populacao"))
        if total_eq > 0:
            calc = pop / total_eq
            if abs(calc - _num(r.get("populacao_por_equipe"))) > max(5, calc * 0.02):
                linhas.append({"item": f"{mun} — população/equipe", "status": "atenção", "achado": "Indicador não confere com população ÷ equipes.", "ação": "Regerar base consolidada."})
        ubs = _num(r.get("total_ubs"))
        if ubs > 0:
            calc = pop / ubs
            if abs(calc - _num(r.get("populacao_por_ubs"))) > max(5, calc * 0.02):
                linhas.append({"item": f"{mun} — população/UBS", "status": "atenção", "achado": "Indicador não confere com população ÷ UBS.", "ação": "Regerar base consolidada."})

    status_fontes = carregar_fontes_status()
    for _, f in status_fontes.iterrows():
        status = "ok" if f["status"] == "Carregada" else "atenção"
        if f["tabela"] == "cnes_estabelecimentos_gerais" and f["registros"] == 0:
            continue
        linhas.append({"item": f"Fonte — {f['base']}", "status": status, "achado": f"{f['registros']} registro(s), {f['municípios']} município(s).", "ação": f["uso analítico"]})

    return pd.DataFrame(linhas).drop_duplicates().reset_index(drop=True)


def carregar_analise_municipal_integrada(municipio: str) -> dict[str, Any]:
    base = carregar_base_dashboard()
    if base.empty:
        base = read_table("base_municipal_consolidada")
    row = _linha(base, municipio)
    if not row:
        return {"ok": False, "mensagem": "Município não localizado na base integrada."}

    social = montar_perfil_social(row, municipio)
    epi, sinan = montar_perfil_epidemiologico(municipio)
    educ = montar_perfil_educacional(municipio)
    territorio = montar_territorio(municipio, row)
    alertas = montar_alertas(row, social, epi, sinan)
    recomendacoes = montar_recomendacoes(row)
    equipes = montar_equipes(row, municipio)
    coerencia = analisar_coerencia_indicadores(municipio)
    radar = montar_radar(row)
    raio_x = montar_raio_x(row)

    return {
        "ok": True,
        "linha": row,
        "raio_x": raio_x,
        "radar": radar,
        "equipes": equipes,
        "social": social,
        "epidemiologico": epi,
        "sinan": sinan,
        "educacional": educ,
        "territorio": territorio,
        "alertas": alertas,
        "recomendacoes": recomendacoes,
        "coerencia": coerencia,
        "texto": gerar_texto_relatorio(row, alertas),
        "fontes": carregar_fontes_status(),
    }
