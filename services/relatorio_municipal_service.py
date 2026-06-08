from __future__ import annotations

import math
from typing import Any

import pandas as pd

from services.dashboard_aps_service import carregar_base_dashboard
from services.indicadores_aps_estrutural_service import detalhar_municipio


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
    try:
        return f"{int(round(float(_num(valor)))):,}".replace(",", ".")
    except Exception:
        return "0"


def _fmt_decimal(valor: Any, casas: int = 1) -> str:
    try:
        return f"{float(_num(valor)):,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "-"


def _get(row: dict[str, Any], campo: str, default: Any = "-") -> Any:
    valor = row.get(campo, default)
    if valor is None:
        return default
    if isinstance(valor, float) and math.isnan(valor):
        return default
    return valor


def _linha_municipio(base: pd.DataFrame, municipio: str) -> dict[str, Any]:
    if base.empty or not municipio or "municipio" not in base.columns:
        return {}
    filtro = base["municipio"].astype(str).str.upper().eq(str(municipio).upper())
    if not filtro.any():
        filtro = base["municipio"].astype(str).str.contains(str(municipio), case=False, regex=False, na=False)
    if not filtro.any():
        return {}
    return base.loc[filtro].iloc[0].to_dict()


def _classificar_leitura(row: dict[str, Any]) -> str:
    classe = str(_get(row, "classe_prioridade", "Monitoramento regular"))
    qualidade = str(_get(row, "classe_qualidade_dados", "Não classificada"))
    motivo = str(_get(row, "principal_motivo_prioridade", "-"))

    if classe == "Prioridade crítica":
        return f"Município em prioridade crítica no ranking estadual. Motivo predominante: {motivo}. Qualidade dos dados: {qualidade}."
    if classe == "Alta prioridade":
        return f"Município em alta prioridade para análise regional. Motivo predominante: {motivo}. Qualidade dos dados: {qualidade}."
    if classe == "Monitoramento intensivo":
        return f"Município em monitoramento intensivo. Recomenda-se acompanhar evolução dos indicadores e validar fatores territoriais. Qualidade dos dados: {qualidade}."
    return f"Município em monitoramento regular pelos critérios atuais. A leitura deve ser mantida em atualização contínua. Qualidade dos dados: {qualidade}."


def montar_componentes_score(row: dict[str, Any]) -> pd.DataFrame:
    dados = [
        {
            "componente": "Acesso territorial à UBS",
            "score": _num(row.get("score_acesso_territorial")),
            "peso": "30%",
            "interpretação": "Distância de bairros/localidades/setores e assentamentos rurais até UBS/APS mais próxima.",
        },
        {
            "componente": "Pressão assistencial",
            "score": _num(row.get("score_vazio_assistencial")),
            "peso": "20%",
            "interpretação": "Pressão de população por equipe/UBS e territórios com pressão APS.",
        },
        {
            "componente": "Vulnerabilidade social",
            "score": _num(row.get("score_vulnerabilidade_social")),
            "peso": "20%",
            "interpretação": "Renda, escolaridade, saneamento e marcadores sociais disponíveis.",
        },
        {
            "componente": "Fragilidade da capacidade instalada",
            "score": _num(row.get("score_fragilidade_capacidade")),
            "peso": "20%",
            "interpretação": "Baixa disponibilidade relativa de UBS, equipes e vínculos profissionais.",
        },
        {
            "componente": "Equidade territorial",
            "score": _num(row.get("score_equidade_territorial")),
            "peso": "10%",
            "interpretação": "Territórios especiais, ruralidade, assentamentos, terras indígenas e pressão territorial.",
        },
    ]
    return pd.DataFrame(dados).sort_values("score", ascending=False).reset_index(drop=True)


def montar_quadro_indicadores(row: dict[str, Any]) -> pd.DataFrame:
    pop = _num(row.get("populacao"))
    equipes = _num(row.get("total_equipes_aps"))
    ubs = _num(row.get("total_ubs"))
    prof = _num(row.get("total_profissionais_aps"))
    pop_equipe = _num(row.get("populacao_por_equipe"))
    pop_ubs = _num(row.get("populacao_por_ubs"))
    qualidade = _num(row.get("qualidade_dados_score"))

    def leitura_pressao(valor: float, tipo: str) -> str:
        if tipo == "equipe":
            if valor > 4000:
                return "pressão elevada; requer análise de cobertura, adscrição e capacidade de resposta"
            if valor > 3500:
                return "atenção; acompanhar distribuição das equipes e crescimento populacional"
            return "pressão dentro de faixa de monitoramento inicial"
        if tipo == "ubs":
            if valor > 12000:
                return "pressão elevada sobre a rede física; avaliar distribuição territorial"
            if valor > 9000:
                return "atenção para capacidade instalada e acesso territorial"
            return "monitoramento regular da estrutura física"
        return "monitoramento"

    return pd.DataFrame([
        {"dimensão": "População", "indicador": "População considerada", "valor": _fmt_int(pop), "leitura técnica": "base populacional usada para pressão sobre APS"},
        {"dimensão": "Capacidade APS", "indicador": "Equipes APS", "valor": _fmt_int(equipes), "leitura técnica": "equipes CNES/INE nos códigos considerados pelo sistema"},
        {"dimensão": "Capacidade APS", "indicador": "UBS/estabelecimentos", "valor": _fmt_int(ubs), "leitura técnica": "estrutura física de referência na base local"},
        {"dimensão": "Capacidade APS", "indicador": "Vínculos profissionais", "valor": _fmt_int(prof), "leitura técnica": "vínculos associados às equipes APS na base CNES/INE"},
        {"dimensão": "Pressão assistencial", "indicador": "População por equipe", "valor": _fmt_decimal(pop_equipe), "leitura técnica": leitura_pressao(pop_equipe, "equipe")},
        {"dimensão": "Pressão assistencial", "indicador": "População por UBS", "valor": _fmt_decimal(pop_ubs), "leitura técnica": leitura_pressao(pop_ubs, "ubs")},
        {"dimensão": "Acesso territorial", "indicador": "Distância média território → UBS", "valor": f"{_fmt_decimal(row.get('distancia_media_territorios_km'))} km", "leitura técnica": "média dos bairros/localidades/setores até UBS/APS mais próxima"},
        {"dimensão": "Acesso territorial", "indicador": "Maior distância território → UBS", "valor": f"{_fmt_decimal(row.get('distancia_maxima_territorios_km'))} km", "leitura técnica": "maior distância identificada; exige validação de rota real e fluxo assistencial"},
        {"dimensão": "Acesso territorial", "indicador": "Territórios críticos/distantes", "valor": _fmt_int(row.get("territorios_criticos_distantes")), "leitura técnica": "bairros/localidades/setores classificados como críticos ou distantes"},
        {"dimensão": "Acesso rural", "indicador": "Assentamentos críticos/distantes", "valor": _fmt_int(row.get("assentamentos_criticos_distantes")), "leitura técnica": "foco em zonas rurais potencialmente desassistidas"},
        {"dimensão": "Território", "indicador": "Territórios mapeados", "valor": _fmt_int(row.get("territorios_mapeados")), "leitura técnica": "bairros/localidades/setores disponíveis para leitura intramunicipal"},
        {"dimensão": "Equidade", "indicador": "Terras indígenas na base", "valor": _fmt_int(row.get("terras_indigenas_qtd_registros")), "leitura técnica": "camada territorial especial para qualificação da análise"},
        {"dimensão": "Equidade", "indicador": "Assentamentos na base", "valor": _fmt_int(row.get("assentamentos_qtd_registros")), "leitura técnica": "camada territorial especial para acesso rural e vulnerabilidade"},
        {"dimensão": "Governança", "indicador": "Qualidade dos dados", "valor": f"{_fmt_decimal(qualidade)}%", "leitura técnica": str(_get(row, "classe_qualidade_dados", "-"))},
    ])


def montar_comparativo_regional(base: pd.DataFrame, municipio: str) -> pd.DataFrame:
    row = _linha_municipio(base, municipio)
    if not row or base.empty or "regiao_saude" not in base.columns:
        return pd.DataFrame()
    regiao = str(row.get("regiao_saude", ""))
    if not regiao:
        return pd.DataFrame()
    reg = base[base["regiao_saude"].astype(str).eq(regiao)].copy()
    if reg.empty:
        return pd.DataFrame()
    cols = [
        "posicao_prioridade", "municipio", "classe_prioridade", "score_prioridade_integrada",
        "score_acesso_territorial", "score_vazio_assistencial", "score_fragilidade_capacidade", "score_vulnerabilidade_social",
        "score_equidade_territorial", "distancia_media_territorios_km", "territorios_criticos_distantes", "assentamentos_criticos_distantes", "fatores_prioritarios",
    ]
    cols = [c for c in cols if c in reg.columns]
    reg = reg[cols].sort_values("score_prioridade_integrada", ascending=False)
    reg.insert(0, "posição na região", range(1, len(reg) + 1))
    return reg


def montar_acoes_prioritarias(row: dict[str, Any]) -> pd.DataFrame:
    acao = str(_get(row, "acao_sugerida", "Manter monitoramento regional."))
    validacao = str(_get(row, "validacao_recomendada", "Sem pendência técnica crítica nos campos atuais."))
    classe = str(_get(row, "classe_prioridade", "Monitoramento regular"))
    motivo = str(_get(row, "principal_motivo_prioridade", "-"))

    linhas = [
        {
            "eixo": "1. Leitura técnica",
            "encaminhamento": f"Registrar o município como {classe}, destacando que {motivo}.",
            "responsável sugerido": "Equipe técnica APS / Inteligência territorial",
        },
        {
            "eixo": "2. Ação inicial sugerida",
            "encaminhamento": acao,
            "responsável sugerido": "Coordenadoria APS + ERS",
        },
        {
            "eixo": "3. Validação de dados",
            "encaminhamento": validacao,
            "responsável sugerido": "Equipe de dados / CNES / Município",
        },
    ]

    if _num(row.get("score_acesso_territorial")) >= 50 or _num(row.get("assentamentos_criticos_distantes")) > 0:
        linhas.append({
            "eixo": "4. Acesso territorial à UBS",
            "encaminhamento": "Validar quem está distante da UBS/APS, priorizando comunidades rurais, assentamentos, rotas reais, agenda rural, unidade volante e referência territorial.",
            "responsável sugerido": "ERS + Município + Planejamento + APS",
        })
    if _num(row.get("score_vazio_assistencial")) >= 55:
        linhas.append({
            "eixo": "5. Pressão assistencial",
            "encaminhamento": "Cruzar vazios intramunicipais, distância até UBS e distribuição das equipes antes de propor expansão física.",
            "responsável sugerido": "ERS + Município + Planejamento",
        })
    if _num(row.get("score_vulnerabilidade_social")) >= 55:
        linhas.append({
            "eixo": "5. Vulnerabilidade social",
            "encaminhamento": "Integrar a leitura da APS com CadÚnico, assistência social, saneamento, educação e vulnerabilidade territorial.",
            "responsável sugerido": "APS + áreas intersetoriais",
        })
    if _num(row.get("score_equidade_territorial")) >= 50:
        linhas.append({
            "eixo": "6. Equidade territorial",
            "encaminhamento": "Verificar assentamentos, terras indígenas, ruralidade e barreiras reais de acesso para resposta diferenciada.",
            "responsável sugerido": "APS + ERS + Município",
        })
    return pd.DataFrame(linhas)


def gerar_texto_relatorio(row: dict[str, Any]) -> str:
    municipio = str(_get(row, "municipio", "município selecionado"))
    regiao = str(_get(row, "regiao_saude", "-"))
    classe = str(_get(row, "classe_prioridade", "Monitoramento regular"))
    posicao = _fmt_int(row.get("posicao_prioridade"))
    score = _fmt_decimal(row.get("score_prioridade_integrada"))
    motivo = str(_get(row, "principal_motivo_prioridade", "-"))
    fatores = str(_get(row, "fatores_prioritarios", "monitoramento regular"))
    acao = str(_get(row, "acao_sugerida", "Manter monitoramento regional."))
    validacao = str(_get(row, "validacao_recomendada", "Sem pendência técnica crítica nos campos atuais."))
    dist_media = _fmt_decimal(row.get('distancia_media_territorios_km'))
    dist_max = _fmt_decimal(row.get('distancia_maxima_territorios_km'))
    terr_dist = _fmt_int(row.get('territorios_criticos_distantes'))
    pop_exp = _fmt_int(row.get('populacao_territorios_criticos_distantes'))
    ass_dist = _fmt_int(row.get('assentamentos_criticos_distantes'))

    return f"""O município de {municipio}, integrante da Região de Saúde {regiao}, foi classificado pelo Sistema de Inteligência Territorial da APS como {classe}, ocupando a posição {posicao} no ranking estadual de prioridade integrada, com score {score}.

A classificação considera, de forma combinada, componentes de acesso territorial à UBS, pressão assistencial, vulnerabilidade social, fragilidade da capacidade instalada e equidade territorial. No caso analisado, o principal motivo identificado foi: {motivo}. Os fatores que mais contribuíram para a leitura foram: {fatores}.

Na camada territorial, a distância média dos bairros/localidades/setores até a UBS/APS mais próxima é de aproximadamente {dist_media} km, com maior distância identificada de {dist_max} km. A base indica {terr_dist} territórios críticos/distantes, população territorial exposta estimada em {pop_exp} pessoas e {ass_dist} assentamentos rurais em situação crítica/distante, quando disponíveis.

A base utilizada registra população considerada de {_fmt_int(row.get('populacao'))} habitantes, {_fmt_int(row.get('total_ubs'))} UBS/estabelecimentos, {_fmt_int(row.get('total_equipes_aps'))} equipes APS e {_fmt_int(row.get('total_profissionais_aps'))} vínculos profissionais associados, resultando em aproximadamente {_fmt_decimal(row.get('populacao_por_equipe'))} habitantes por equipe e {_fmt_decimal(row.get('populacao_por_ubs'))} habitantes por UBS.

Como encaminhamento inicial, recomenda-se: {acao}

Antes de qualquer decisão administrativa ou pactuação regional, recomenda-se ainda: {validacao}

Esta leitura não substitui normas oficiais de cobertura, habilitação, financiamento ou planejamento físico da rede. O relatório deve ser utilizado como triagem técnica qualificada para orientar análise municipal, diálogo com ERS e município, priorização territorial e eventual despacho gerencial."""


def carregar_relatorio_municipal(municipio: str) -> dict[str, Any]:
    base = carregar_base_dashboard()
    row = _linha_municipio(base, municipio)
    detalhes = detalhar_municipio(municipio)

    if not row and detalhes.get("linha"):
        row = dict(detalhes.get("linha", {}))

    if not row:
        return {
            "linha": {},
            "componentes": pd.DataFrame(),
            "indicadores": pd.DataFrame(),
            "comparativo_regional": pd.DataFrame(),
            "acoes": pd.DataFrame(),
            "texto": "",
            "leitura": "Município não localizado na base integrada.",
            "detalhes": detalhes,
        }

    return {
        "linha": row,
        "componentes": montar_componentes_score(row),
        "indicadores": montar_quadro_indicadores(row),
        "comparativo_regional": montar_comparativo_regional(base, municipio),
        "acoes": montar_acoes_prioritarias(row),
        "texto": gerar_texto_relatorio(row),
        "leitura": _classificar_leitura(row),
        "detalhes": detalhes,
    }
