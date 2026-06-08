from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from components.ui_elements import aplicar_estilo_executivo_plotly, aplicar_tema_plotly, render_dataframe
from database.queries import read_table

from services.inteligencia_aps_service import (
    carregar_inteligencia_cruzada_aps,
    gerar_sintese_decisao,
    matriz_decisao_estrategica,
    ranking_regional_decisao,
    resumo_inteligencia_cruzada,
)

from services.motor_inteligencia_aps_service import (
    carregar_motor_inteligencia_estrategica,
    gerar_sintese_motor,
    metodologia_pesos_motor_aps,
    ranking_regional_motor,
    resumo_motor_inteligencia,
    resumo_perfis_motor,
)

from services.dashboard_aps_service import (
    CODIGOS_EQUIPES_APS,
    carregar_base_dashboard,
    carregar_equipes_municipio,
    construir_carteira_intervencoes_aps,
    construir_perfis_alertas_aps,
    carregar_territorios_prioritarios,
    carregar_territorios_desassistidos,
    carregar_unidades_municipio,
    matriz_equipes_por_codigo,
    resumo_estadual,
    resumo_regional_dashboard,
)

_PLOTLY_KEY_COUNTER: dict[str, int] = {}

GRAFICO_EXPLICACOES = {
    "mapa_estrategico_prioridade": {
        "titulo": "Como ler este mapa de bolhas",
        "itens": [
            "Cada bolha representa um município de Mato Grosso.",
            "O tamanho da bolha representa a população considerada na análise: bolhas maiores indicam maior volume populacional.",
            "A cor representa o score integrado de prioridade: quanto mais intensa/quente a cor, maior a prioridade relativa na régua estadual.",
            "Este mapa não mostra bairros ou comunidades; ele resume a situação no nível municipal.",
            "Use o mapa para localizar visualmente concentrações de prioridade, mas confirme os detalhes nas abas Vazio integrado, Territórios desassistidos e Diagnóstico Municipal.",
        ],
    },
    "distribuicao_prioridade": {
        "titulo": "Como ler este gráfico de rosca",
        "itens": [
            "Cada fatia mostra a proporção de municípios em cada classe de prioridade.",
            "A classe Prioridade crítica reúne municípios no topo relativo do risco estadual.",
            "Alta prioridade indica alerta forte, mas não necessariamente situação mais grave que todos os demais indicadores isolados.",
            "A leitura correta é comparativa: o gráfico mostra como o conjunto dos municípios se distribui na régua de prioridade.",
        ],
    },
    "grafico_ranking_integrado_barras": {
        "titulo": "Como ler este gráfico",
        "itens": [
            "As barras mostram a quantidade de equipes APS por código CNES/INE.",
            "O objetivo é visualizar a composição da capacidade APS cadastrada, não medir qualidade da assistência.",
            "Diferenças entre códigos podem refletir porte populacional, modelo de organização municipal e consistência do cadastro CNES/INE.",
        ],
    },
    "componentes_top_prioridade": {
        "titulo": "Como ler a composição dos municípios prioritários",
        "itens": [
            "Cada barra empilhada mostra quais componentes puxam a prioridade dos municípios no topo do ranking.",
            "Acesso territorial representa distância e dificuldade de alcance aos serviços APS.",
            "Pressão assistencial considera população por UBS/equipe e pressão potencial sobre a rede.",
            "Vulnerabilidade social e equidade territorial ajudam a identificar onde o vazio assistencial é mais sensível do ponto de vista social e territorial.",
        ],
    },
    "distribuicao_prioridade": {
        "titulo": "Como interpretar a distribuição por prioridade",
        "itens": [
            "O gráfico mostra a quantidade/proporção de municípios em cada classe de prioridade.",
            "Prioridade crítica e alta prioridade devem ser lidas como sinalizadores de aprofundamento técnico.",
            "Monitoramento regular não significa ausência de problema; significa apenas menor prioridade relativa na régua atual.",
        ],
    },
    "grafico_tatico_regiao": {
        "titulo": "Como ler o ranking regional",
        "itens": [
            "As barras comparam as Regiões de Saúde conforme o score médio ou indicador selecionado.",
            "Regiões com maior valor concentram mais pressão territorial, assistencial ou social na régua analisada.",
            "A análise regional não substitui a análise municipal: uma região pode ter média moderada e ainda conter municípios críticos.",
        ],
    },
    "grafico_tatico_score_regiao": {
        "titulo": "Como interpretar os motivos predominantes",
        "itens": [
            "O gráfico resume os principais motivos que aparecem entre os municípios prioritários.",
            "Ele ajuda a identificar se a prioridade está sendo puxada por acesso, capacidade, vulnerabilidade ou outro fator.",
            "Use esta leitura para orientar o tipo de resposta: ampliar equipe, revisar capacidade instalada, validar território ou aprofundar vulnerabilidade.",
        ],
    },
    "grafico_tatico_populacao_regiao": {
        "titulo": "Como ler a cobertura técnica dos dados",
        "itens": [
            "O gráfico mostra a distribuição dos municípios conforme classe de qualidade/cobertura dos dados.",
            "Quanto maior a qualidade dos dados, maior a segurança para usar a leitura na priorização inicial.",
            "Municípios com baixa qualidade de dados devem passar por validação antes de qualquer decisão administrativa.",
        ],
    },
    "mapa_territorios_desassistidos": {
        "titulo": "Como ler este mapa de territórios",
        "itens": [
            "Cada bolha representa um território/localidade/setor, não o município inteiro.",
            "O tamanho da bolha representa a população estimada naquele território quando disponível.",
            "A cor representa a distância até a UBS/APS mais próxima: cores mais intensas indicam maior distância.",
            "Este mapa é fundamental para identificar quem pode estar desassistido dentro do município, especialmente zona rural, assentamentos e localidades isoladas.",
            "A distância é uma referência geográfica de priorização; não substitui rota viária real, tempo de deslocamento ou validação local.",
        ],
    },
    "painel_territorios_desassistidos_mapa": {
        "titulo": "Como interpretar os territórios desassistidos",
        "itens": [
            "Cada ponto representa um território analisado, como bairro, localidade, setor ou assentamento.",
            "A cor indica a distância estimada até a UBS/APS de referência mais próxima.",
            "O tamanho indica população estimada quando a informação está disponível.",
            "A leitura principal é: territórios populosos e distantes devem ser analisados com prioridade.",
            "Dê atenção especial a pontos rurais, assentamentos e territórios que combinam distância alta e população relevante.",
        ],
    },
    "painel_territorios_desassistidos_prioridade": {
        "titulo": "Como ler a prioridade territorial",
        "itens": [
            "As barras mostram quantos territórios aparecem em cada classe de prioridade territorial.",
            "Classes mais críticas indicam maior combinação entre distância, população e dificuldade potencial de acesso.",
            "O gráfico ajuda a diferenciar municípios com poucos territórios críticos daqueles com problema territorial mais espalhado.",
        ],
    },
    "painel_territorios_desassistidos_regiao": {
        "titulo": "Como ler a distância máxima por Região de Saúde",
        "itens": [
            "Cada barra mostra a maior distância identificada entre território e UBS/APS dentro da Região de Saúde.",
            "Valores muito altos indicam a existência de pelo menos um território extremo que precisa de validação.",
            "Atenção: a maior distância não representa a média regional; ela mostra o pior caso identificado na base atual.",
        ],
    },
    "grafico_operacional_municipal": {
        "titulo": "Como ler o gráfico operacional municipal",
        "itens": [
            "O gráfico detalha componentes ou indicadores do município selecionado.",
            "Use para entender se o problema principal é acesso, capacidade instalada, pressão populacional ou vulnerabilidade.",
            "A leitura deve ser combinada com as tabelas do Diagnóstico Municipal e com a camada territorial.",
        ],
    },
    "perfis_municipais_distribuicao": {
        "titulo": "Como interpretar os perfis municipais APS",
        "itens": [
            "Cada perfil resume o principal tipo de problema identificado no município.",
            "Municípios com o mesmo perfil podem demandar respostas semelhantes, como equipe volante, nova UBS, qualificação CNES ou validação territorial.",
            "O perfil não é um rótulo definitivo: ele organiza a discussão técnica inicial.",
        ],
    },
    "perfis_componentes_dominantes": {
        "titulo": "Como ler o componente dominante",
        "itens": [
            "O gráfico mostra qual dimensão mais pesa na prioridade dos municípios.",
            "Se a fatia de fragilidade de capacidade é dominante, a prioridade decorre mais de UBS, equipes e profissionais.",
            "Se acesso territorial domina, o problema está mais ligado à distância e à distribuição espacial dos serviços.",
            "Se vulnerabilidade social domina, a resposta deve dialogar com determinantes sociais, vigilância e assistência social.",
        ],
    },
    "perfis_desequilibrio_intramunicipal": {
        "titulo": "Como interpretar o desequilíbrio intramunicipal",
        "itens": [
            "O gráfico identifica municípios em que a média municipal pode esconder territórios internos muito problemáticos.",
            "Quanto maior o alerta, maior a chance de haver bairros, comunidades ou áreas rurais distantes e pouco visíveis no dado agregado.",
            "Esta leitura ajuda a evitar que municípios aparentemente regulares deixem populações rurais ou periféricas sem atenção suficiente.",
        ],
    },
    "carteira_acoes_eixo": {
        "titulo": "Como ler a carteira por eixo de intervenção",
        "itens": [
            "As barras mostram em quais eixos se concentram as ações preliminares sugeridas.",
            "Eixos com mais ações indicam onde há maior demanda de resposta técnica na leitura atual.",
            "A carteira é preliminar e deve ser validada pela APS, ERS e município antes de qualquer decisão.",
        ],
    },
    "carteira_acoes_urgencia": {
        "titulo": "Como ler a urgência sugerida",
        "itens": [
            "O gráfico distribui as ações conforme a urgência técnica sugerida pelo sistema.",
            "Urgência muito alta ou alta indica necessidade de análise prioritária, não autorização automática de intervenção.",
            "Use esta visão para organizar reunião técnica, pactuação regional e validação dos dados.",
        ],
    },
    "carteira_acoes_regiao": {
        "titulo": "Como interpretar ações por Região de Saúde",
        "itens": [
            "O gráfico mostra onde se concentram as ações sugeridas por Região de Saúde.",
            "Regiões com muitas ações podem exigir estratégia regional, não apenas resposta município a município.",
            "Compare esta leitura com o ranking regional e os territórios desassistidos.",
        ],
    },
    "grafico_ranking_integrado_top": {
        "titulo": "Como ler a distribuição de prioridade",
        "itens": [
            "O gráfico mostra quantos municípios estão em cada classe da régua integrada.",
            "A régua é relativa à base estadual atual; ela serve para priorização técnica inicial.",
            "Municípios fora da prioridade crítica ainda podem ter alertas territoriais específicos.",
        ],
    },
    "grafico_vazio_pesos_score": {
        "titulo": "Como ler o score médio por Região de Saúde",
        "itens": [
            "As barras comparam as regiões pelo score médio de prioridade.",
            "Regiões com score médio maior concentram municípios com maior pressão na leitura integrada.",
            "A análise deve ser complementada com a distribuição interna da região, porque a média pode esconder extremos.",
        ],
    },
    "grafico_vazio_top_componentes": {
        "titulo": "Como ler este gráfico de bolhas",
        "itens": [
            "Cada bolha representa um município.",
            "Os eixos comparam dimensões do score, como vazio assistencial, vulnerabilidade e capacidade.",
            "O tamanho ou cor da bolha pode representar população, score ou classe conforme a configuração da aba.",
            "Municípios que aparecem mais afastados do centro do gráfico são os que combinam fatores mais intensos.",
            "Use o hover do mouse para ver o nome do município e os valores. A leitura principal é identificar agrupamentos e municípios fora do padrão.",
        ],
    },
    "grafico_vazio_prioridade_regiao": {
        "titulo": "Como ler municípios prioritários por região",
        "itens": [
            "Cada barra mostra quantos municípios prioritários existem em cada Região de Saúde.",
            "Regiões com mais municípios prioritários podem precisar de estratégia regional integrada.",
            "Essa leitura complementa o ranking: quantidade de municípios críticos é diferente de score médio regional.",
        ],
    },
    "grafico_vazio_qualidade_dados": {
        "titulo": "Como ler a composição média da prioridade regional",
        "itens": [
            "O gráfico compara os componentes médios de prioridade em cada Região de Saúde.",
            "Ajuda a identificar se a região tem problema mais ligado a acesso, vulnerabilidade ou equidade territorial.",
            "Use para direcionar a conversa: nem toda região prioritária precisa da mesma resposta.",
        ],
    },
    "grafico_metodologia_fontes": {
        "titulo": "Como ler a composição da prioridade municipal",
        "itens": [
            "As barras mostram os componentes que formam a prioridade do município selecionado.",
            "O maior componente indica o fator que mais pesa na leitura atual.",
            "A análise deve ser combinada com o relatório municipal e com os territórios desassistidos.",
        ],
    },
    "grafico_metodologia_confiabilidade": {
        "titulo": "Como ler o mapa de pressão territorial",
        "itens": [
            "Cada ponto representa um território intramunicipal analisado.",
            "A cor mostra a pressão APS ou distância, conforme a camada carregada.",
            "Pontos mais intensos indicam locais que merecem validação técnica.",
            "A leitura é preliminar e depende da qualidade dos dados territoriais disponíveis.",
        ],
    },
}

def _render_grafico_explicacao(key_base: str):
    info = GRAFICO_EXPLICACOES.get(key_base)
    if not info:
        return
    itens = "".join(f"<li>{item}</li>" for item in info.get("itens", []))
    st.markdown(
        f"""
        <div class="grafico-explicacao">
            <div class="grafico-explicacao-titulo">ℹ️ {info.get("titulo", "Como interpretar este gráfico")}</div>
            <ul>{itens}</ul>
        </div>
        """,
        unsafe_allow_html=True,
    )



def _plotly_chart(fig, key_base: str, **kwargs):
    """Renderiza gráficos Plotly com chave única, acabamento executivo GovMT e legenda explicativa."""
    numero = _PLOTLY_KEY_COUNTER.get(key_base, 0) + 1
    _PLOTLY_KEY_COUNTER[key_base] = numero
    fig = aplicar_estilo_executivo_plotly(fig)
    st.plotly_chart(fig, use_container_width=True, key=f"{key_base}_{numero}", **kwargs)
    _render_grafico_explicacao(key_base)


def _fmt_int(valor) -> str:
    try:
        return f"{int(round(float(valor))):,}".replace(",", ".")
    except Exception:
        return "0"


def _fmt_num(valor, casas: int = 1) -> str:
    try:
        return f"{float(valor):,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "-"


def _fmt_pct(valor, casas: int = 1) -> str:
    try:
        return f"{float(valor):,.{casas}f}%".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "-"


def _baixar_csv(df: pd.DataFrame, nome: str, label: str):
    if df is None or df.empty:
        return
    st.download_button(
        label=label,
        data=df.to_csv(index=False, sep=";").encode("utf-8-sig"),
        file_name=nome,
        mime="text/csv",
        use_container_width=True,
        key=f"download_{nome}",
    )


def _card_html(titulo: str, valor: str, subtitulo: str = "", destaque: str = ""):
    st.markdown(
        f"""
        <div style="background:linear-gradient(135deg,#ffffff 0%,#f6f9fc 100%);border:1px solid #e5edf5;border-radius:18px;padding:16px 18px;box-shadow:0 8px 22px rgba(15,43,71,.06);min-height:120px;">
            <div style="font-size:.78rem;font-weight:800;color:#60748a;text-transform:uppercase;letter-spacing:.04em;">{titulo}</div>
            <div style="font-size:1.75rem;font-weight:900;color:#0d3557;margin-top:6px;">{valor}</div>
            <div style="font-size:.88rem;color:#5e6b78;margin-top:4px;">{subtitulo}</div>
            <div style="font-size:.76rem;color:#0b6b8f;font-weight:700;margin-top:8px;">{destaque}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _painel_metodologia_compacto():
    st.markdown(
        """
        <div style="background:#eef7ff;border-left:5px solid #1f77b4;border-radius:14px;padding:14px 18px;margin:8px 0 16px 0;color:#17324d;">
        <b>Leitura integrada:</b> o painel combina capacidade instalada da APS, pressão populacional por equipe/UBS,
        vulnerabilidade social, determinantes territoriais e camadas de equidade. O score não substitui norma oficial,
        mas organiza prioridades para análise técnica, pactuação regional e despacho gerencial.
        </div>
        """,
        unsafe_allow_html=True,
    )


def _filtrar_base(base: pd.DataFrame, prefixo_key: str = "dashboard_aps") -> pd.DataFrame:
    col1, col2, col3 = st.columns([1.4, 1.2, 1.2])
    with col1:
        busca = st.text_input(
            "Buscar município",
            placeholder="Ex.: Cuiabá, Sinop, Cáceres",
            key=f"{prefixo_key}_busca_municipio",
        )
    with col2:
        regioes = ["Todas"]
        if "regiao_saude" in base.columns:
            regioes += sorted([r for r in base["regiao_saude"].dropna().astype(str).unique() if r and r.lower() != "none"])
        regiao = st.selectbox("Região de Saúde", regioes, key=f"{prefixo_key}_regiao_saude")
    with col3:
        classes = ["Todas", "Prioridade crítica", "Alta prioridade", "Monitoramento intensivo", "Monitoramento regular"]
        classe = st.selectbox("Classe de prioridade", classes, key=f"{prefixo_key}_classe_prioridade")

    out = base.copy()
    if busca:
        out = out[out["municipio"].astype(str).str.contains(busca, case=False, na=False)]
    if regiao != "Todas" and "regiao_saude" in out.columns:
        out = out[out["regiao_saude"].astype(str).eq(regiao)]
    if classe != "Todas":
        out = out[out["classe_prioridade"].astype(str).eq(classe)]
    return out


def _grafico_mapa(df: pd.DataFrame, titulo: str):
    mapa = df.copy()
    mapa = mapa[(pd.to_numeric(mapa.get("latitude", 0), errors="coerce") != 0) & (pd.to_numeric(mapa.get("longitude", 0), errors="coerce") != 0)]
    if mapa.empty:
        st.info("Sem coordenadas municipais suficientes para montar o mapa nesta seleção.")
        return
    hover_cols = [c for c in [
        "municipio", "regiao_saude", "populacao", "total_ubs", "total_equipes_aps", "populacao_por_equipe",
        "score_prioridade_integrada", "classe_prioridade", "alerta_acao"
    ] if c in mapa.columns]
    fig = px.scatter_mapbox(
        mapa,
        lat="latitude",
        lon="longitude",
        size="populacao",
        color="score_prioridade_integrada",
        hover_name="municipio",
        hover_data=hover_cols,
        zoom=4.2,
        height=520,
        title=titulo,
        color_continuous_scale="YlOrRd",
        size_max=34,
    )
    fig.update_layout(mapbox_style="open-street-map", margin={"r": 0, "t": 42, "l": 0, "b": 0})
    fig.update_coloraxes(colorbar_title_text="Score integrado")
    _plotly_chart(fig, "mapa_estrategico_prioridade")


def _grafico_componentes_top(df: pd.DataFrame, n: int = 15):
    top = df.head(n).copy()
    cols = ["score_acesso_territorial", "score_vazio_assistencial", "score_vulnerabilidade_social", "score_fragilidade_capacidade", "score_equidade_territorial"]
    cols = [c for c in cols if c in top.columns]
    if top.empty or not cols:
        return
    long = top[["municipio"] + cols].melt("municipio", var_name="componente", value_name="score")
    nomes = {
        "score_acesso_territorial": "Acesso territorial à UBS",
        "score_vazio_assistencial": "Pressão assistencial",
        "score_vulnerabilidade_social": "Vulnerabilidade social",
        "score_fragilidade_capacidade": "Fragilidade de capacidade",
        "score_equidade_territorial": "Equidade territorial",
    }
    long["componente"] = long["componente"].map(nomes).fillna(long["componente"])
    fig = px.bar(long, y="municipio", x="score", color="componente", orientation="h", text="score", title=f"Composição dos {n} municípios de maior prioridade")
    fig.update_layout(yaxis_title="", xaxis_title="Score do componente")
    fig.update_layout(yaxis={"categoryorder": "total ascending"}, legend_title_text="Componente")
    _plotly_chart(fig, "componentes_top_prioridade")



def _resumo_vazio_integrado(base: pd.DataFrame) -> dict:
    if base.empty:
        return {}
    prioritarios = base[base["classe_prioridade"].isin(["Prioridade crítica", "Alta prioridade"])] if "classe_prioridade" in base.columns else base.head(0)
    sem_equipe = int((pd.to_numeric(base.get("total_equipes_aps", 0), errors="coerce").fillna(0) <= 0).sum()) if "total_equipes_aps" in base.columns else 0
    sem_ubs = int((pd.to_numeric(base.get("total_ubs", 0), errors="coerce").fillna(0) <= 0).sum()) if "total_ubs" in base.columns else 0
    regiao_top = "-"
    if not prioritarios.empty and "regiao_saude" in prioritarios.columns:
        vc = prioritarios["regiao_saude"].astype(str).value_counts()
        if not vc.empty:
            regiao_top = vc.index[0]
    return {
        "municipios_prioritarios": len(prioritarios),
        "populacao_prioritaria": prioritarios.get("populacao", pd.Series(dtype=float)).sum() if not prioritarios.empty else 0,
        "sem_equipe": sem_equipe,
        "sem_ubs": sem_ubs,
        "regiao_top": regiao_top,
        "score_medio_vazio": base.get("score_vazio_assistencial", pd.Series(dtype=float)).mean(),
        "territorios_criticos_distantes": base.get("territorios_criticos_distantes", pd.Series(dtype=float)).sum(),
        "populacao_critica_distante": base.get("populacao_territorios_criticos_distantes", pd.Series(dtype=float)).sum(),
        "assentamentos_criticos_distantes": base.get("assentamentos_criticos_distantes", pd.Series(dtype=float)).sum(),
        "maior_distancia_territorial": base.get("distancia_maxima_territorios_km", pd.Series(dtype=float)).max(),
    }


def _explicacao_vazio_integrado():
    st.markdown(
        """
        <div style="background:#f7fbff;border:1px solid #d9eaf7;border-radius:16px;padding:14px 18px;margin:8px 0 16px 0;color:#17324d;">
        <b>Como ler o vazio assistencial integrado:</b> a partir desta etapa, a distância dos bairros, localidades, setores e assentamentos rurais até a UBS/APS mais próxima entra formalmente no score.
        A régua cruza acesso territorial, pressão populacional por equipe/UBS, vulnerabilidade social, capacidade instalada CNES/INE e camadas de equidade territorial.
        O objetivo é evidenciar quem pode estar desassistido, especialmente zonas rurais e comunidades distantes, para apoiar triagem, pactuação e validação técnica.
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.expander("Ver composição técnica da régua de vazio assistencial"):
        metodologia = pd.DataFrame([
            {"dimensão": "Acesso territorial à UBS", "peso no score integrado": "30%", "o que observa": "distância de bairros/localidades/setores e assentamentos até UBS/APS mais próxima"},
            {"dimensão": "Pressão assistencial", "peso no score integrado": "20%", "o que observa": "população por equipe, população por UBS e pressão APS nos territórios"},
            {"dimensão": "Vulnerabilidade social", "peso no score integrado": "20%", "o que observa": "baixa renda, baixa escolaridade, analfabetismo estimado e saneamento inadequado"},
            {"dimensão": "Fragilidade da capacidade instalada", "peso no score integrado": "20%", "o que observa": "menor disponibilidade relativa de equipes, UBS e profissionais por equipe"},
            {"dimensão": "Equidade territorial", "peso no score integrado": "10%", "o que observa": "terras indígenas, assentamentos, áreas especiais, ruralidade e territórios com pressão alta"},
        ])
        render_dataframe(metodologia, use_container_width=True, hide_index=True)
        st.caption("A régua é uma ferramenta de inteligência e priorização. Antes de decisão administrativa, recomenda-se validar dados com ERS, município, CNES e área técnica responsável.")


def _grafico_quadrante_vazio(df: pd.DataFrame):
    if df.empty:
        return
    req = ["score_fragilidade_capacidade", "score_vulnerabilidade_social", "score_acesso_territorial", "populacao"]
    if any(c not in df.columns for c in req):
        st.info("A base atual ainda não possui todos os componentes para o gráfico de quadrantes.")
        return
    fig = px.scatter(
        df,
        x="score_fragilidade_capacidade",
        y="score_vulnerabilidade_social",
        size="populacao",
        color="score_acesso_territorial",
        hover_name="municipio",
        hover_data=[c for c in ["regiao_saude", "classe_prioridade", "populacao_por_equipe", "populacao_por_ubs", "fatores_prioritarios", "classe_qualidade_dados"] if c in df.columns],
        title="Quadrante de decisão: vulnerabilidade social x fragilidade da capacidade APS",
        size_max=36,
        color_continuous_scale="YlOrRd",
    )
    fig.add_vline(x=50, line_dash="dash", line_width=1)
    fig.add_hline(y=50, line_dash="dash", line_width=1)
    fig.update_layout(height=520, xaxis_title="Fragilidade da capacidade instalada", yaxis_title="Vulnerabilidade social")
    _plotly_chart(fig, "distribuicao_prioridade")



def _card_prioridade_municipio(row: pd.Series, rank: int):
    municipio = str(row.get("municipio", "-"))
    regiao = str(row.get("regiao_saude", "-"))
    score = _fmt_num(row.get("score_prioridade_integrada", 0), 1)
    classe = str(row.get("classe_prioridade", "-"))
    motivo = str(row.get("principal_motivo_prioridade", row.get("fatores_prioritarios", "-")))
    acao = str(row.get("acao_sugerida", row.get("alerta_acao", "-")))
    qualidade = str(row.get("classe_qualidade_dados", "-"))
    st.markdown(
        f'''
        <div style="background:#ffffff;border:1px solid #dce8f2;border-radius:18px;padding:15px 16px;box-shadow:0 8px 22px rgba(15,43,71,.06);min-height:238px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <span style="font-size:.78rem;font-weight:900;color:#60748a;text-transform:uppercase;">#{rank} prioridade</span>
                <span style="font-size:.78rem;font-weight:800;color:#0d5c82;background:#eef7ff;border-radius:999px;padding:4px 8px;">Score {score}</span>
            </div>
            <div style="font-size:1.05rem;font-weight:900;color:#0d3557;line-height:1.2;">{municipio}</div>
            <div style="font-size:.82rem;color:#68798a;margin-top:2px;">{regiao}</div>
            <div style="font-size:.82rem;color:#8a3a19;font-weight:800;margin-top:8px;">{classe}</div>
            <div style="font-size:.80rem;color:#17324d;margin-top:8px;"><b>Motivo:</b> {motivo}</div>
            <div style="font-size:.80rem;color:#17324d;margin-top:8px;"><b>Ação:</b> {acao}</div>
            <div style="font-size:.76rem;color:#60748a;margin-top:8px;"><b>Dados:</b> {qualidade}</div>
        </div>
        ''',
        unsafe_allow_html=True,
    )


def _render_top_prioritarios(df: pd.DataFrame, n: int = 5):
    if df.empty:
        st.info("Sem municípios para exibir nesta seleção.")
        return
    top = df.sort_values("score_prioridade_integrada", ascending=False).head(n).reset_index(drop=True)
    st.markdown("#### Municípios que exigem leitura imediata")
    st.caption("Cards gerenciais com o principal fator de prioridade, qualidade da base e sugestão preliminar de encaminhamento.")
    cols = st.columns(min(n, 5))
    for i, row in top.iterrows():
        with cols[i % len(cols)]:
            _card_prioridade_municipio(row, int(row.get("posicao_prioridade", i + 1)))


def _render_pesos_score():
    st.markdown("#### Como o score está distribuído")
    pesos = [
        ("Acesso territorial à UBS", 30, "distância de bairros, comunidades, setores e assentamentos até UBS/APS"),
        ("Pressão assistencial", 20, "população por equipe, população por UBS e pressão APS territorial"),
        ("Vulnerabilidade social", 20, "baixa renda, escolaridade, analfabetismo estimado e saneamento"),
        ("Capacidade instalada", 20, "equipes, UBS e profissionais em relação à população"),
        ("Equidade territorial", 10, "territórios especiais, ruralidade e pressões intramunicipais"),
    ]
    cols = st.columns(5)
    for col, (nome, peso, desc) in zip(cols, pesos):
        with col:
            st.markdown(f"**{nome}**")
            st.progress(peso / 100)
            st.caption(f"{peso}% — {desc}")


def _render_ranking_regional_prioridade(df: pd.DataFrame):
    if df.empty or "regiao_saude" not in df.columns:
        return
    tmp = df.copy()
    tmp["prioritario"] = tmp["classe_prioridade"].isin(["Prioridade crítica", "Alta prioridade"]).astype(int)
    reg = tmp.groupby("regiao_saude", dropna=False).agg(
        municipios=("municipio", "nunique"),
        municipios_prioritarios=("prioritario", "sum"),
        populacao=("populacao", "sum"),
        score_medio=("score_prioridade_integrada", "mean"),
        acesso_medio=("score_acesso_territorial", "mean"),
        vazio_medio=("score_vazio_assistencial", "mean"),
        vulnerabilidade_media=("score_vulnerabilidade_social", "mean"),
        capacidade_media=("score_fragilidade_capacidade", "mean"),
        equidade_media=("score_equidade_territorial", "mean"),
    ).reset_index()
    reg["score_medio"] = reg["score_medio"].round(1)
    reg = reg.sort_values(["municipios_prioritarios", "score_medio"], ascending=False)
    st.markdown("#### Prioridade por Região de Saúde")
    col1, col2 = st.columns([1.1, 1])
    with col1:
        fig = px.bar(
            reg.sort_values("score_medio"),
            y="regiao_saude",
            x="score_medio",
            orientation="h",
            title="Score médio regional do vazio integrado",
            hover_data=["municipios", "municipios_prioritarios", "populacao"],
        )
        fig.update_layout(height=460, yaxis_title="Região de Saúde", xaxis_title="Score médio")
        _plotly_chart(fig, "grafico_tatico_regiao")
    with col2:
        cols = ["regiao_saude", "municipios", "municipios_prioritarios", "populacao", "score_medio", "acesso_medio", "vazio_medio", "vulnerabilidade_media", "capacidade_media", "equidade_media"]
        render_dataframe(reg[cols], use_container_width=True, hide_index=True)


def _render_leitura_dos_fatores(df: pd.DataFrame):
    if df.empty:
        return
    st.markdown("#### O que mais está puxando a prioridade")
    top = df.sort_values("score_prioridade_integrada", ascending=False).head(30).copy()
    col1, col2 = st.columns(2)
    with col1:
        if "principal_motivo_prioridade" in top.columns:
            motivos = top["principal_motivo_prioridade"].astype(str).value_counts().reset_index()
            motivos.columns = ["motivo", "municipios"]
            fig = px.bar(motivos.head(10), x="municipios", y="motivo", orientation="h", title="Motivo predominante no Top 30")
            fig.update_layout(yaxis={"categoryorder": "total ascending"}, height=420)
            _plotly_chart(fig, "grafico_tatico_score_regiao")
    with col2:
        if "acao_sugerida" in top.columns:
            acoes = top["acao_sugerida"].astype(str).value_counts().reset_index()
            acoes.columns = ["encaminhamento", "municipios"]
            render_dataframe(acoes.head(10), use_container_width=True, hide_index=True)


def _render_validacao_dados(df: pd.DataFrame):
    if df.empty:
        return
    st.markdown("#### Qualidade dos dados e validação necessária")
    col1, col2 = st.columns([.8, 1.2])
    with col1:
        if "classe_qualidade_dados" in df.columns:
            dist = df["classe_qualidade_dados"].astype(str).value_counts().reset_index()
            dist.columns = ["classe", "municipios"]
            fig = px.pie(dist, names="classe", values="municipios", hole=.48, title="Cobertura técnica dos dados")
            _plotly_chart(fig, "grafico_tatico_populacao_regiao")
    with col2:
        cols = ["posicao_prioridade", "municipio", "regiao_saude", "classe_prioridade", "classe_qualidade_dados", "validacao_recomendada"]
        cols = [c for c in cols if c in df.columns]
        render_dataframe(df.sort_values("score_prioridade_integrada", ascending=False)[cols].head(25), use_container_width=True, hide_index=True)


def _render_territorios_desassistidos(terr):
    """Renderização segura dos territórios potencialmente desassistidos.

    Hotfix V50.1:
    - evita erro de indentação deixado por patch anterior;
    - evita KeyError quando latitude/longitude não existem;
    - mantém a seção visível mesmo quando a camada territorial não tem coordenadas;
    - preserva uma leitura executiva para apresentação no Streamlit Cloud.
    """
    import pandas as pd
    import streamlit as st
    import plotly.express as px

    st.markdown("### Quem pode estar desassistido — territórios, localidades e zona rural")
    st.caption(
        "Leitura territorial preliminar: distância geodésica, ruralidade, vulnerabilidade e população ajudam a indicar "
        "quem pode exigir validação local, visita técnica, reorganização de equipes ou apoio de transporte sanitário."
    )

    if terr is None:
        st.info("A base territorial ainda não está disponível para esta visualização.")
        return

    try:
        df = terr.copy()
    except Exception:
        st.info("Não foi possível preparar a base territorial para esta visualização.")
        return

    if df.empty:
        st.info("Não há territórios/localidades disponíveis para esta seleção.")
        return

    # Normaliza possíveis nomes de colunas usados ao longo das versões do sistema.
    rename_map = {
        "lat": "latitude",
        "lng": "longitude",
        "lon": "longitude",
        "distancia_ubs_km": "distancia_ubs_mais_proxima_km",
        "distancia_km": "distancia_ubs_mais_proxima_km",
        "distancia_hospitalar_km": "distancia_hospital_km",
    }
    for origem, destino in rename_map.items():
        if origem in df.columns and destino not in df.columns:
            df[destino] = df[origem]

    # Garante colunas mínimas para não quebrar a tela.
    for col in ["municipio", "territorio", "tipo_territorial", "classificacao_distancia", "latitude", "longitude"]:
        if col not in df.columns:
            df[col] = "" if col not in ["latitude", "longitude"] else pd.NA

    # Distância preferencial: UBS; se não existir, usa hospital; se não existir, fica vazio.
    dist_col = None
    for c in [
        "distancia_ubs_mais_proxima_km",
        "distancia_hospital_km",
        "distancia_hospitalar_km",
        "distancia_media_km",
        "distancia_km",
    ]:
        if c in df.columns:
            dist_col = c
            break

    if dist_col:
        df[dist_col] = pd.to_numeric(df[dist_col], errors="coerce")
        df = df.sort_values(dist_col, ascending=False, na_position="last")

    lat = pd.to_numeric(df.get("latitude"), errors="coerce")
    lon = pd.to_numeric(df.get("longitude"), errors="coerce")
    tem_geo = lat.notna() & lon.notna() & lat.between(-35, 6) & lon.between(-75, -45) & (lat != 0) & (lon != 0)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Territórios analisados", f"{len(df):,.0f}".replace(",", "."))
    if dist_col:
        c2.metric("Maior distância", f"{df[dist_col].max(skipna=True):.1f} km".replace(".", ","))
        c3.metric("Distância média", f"{df[dist_col].mean(skipna=True):.1f} km".replace(".", ","))
    else:
        c2.metric("Maior distância", "—")
        c3.metric("Distância média", "—")
    c4.metric("Com coordenadas", f"{int(tem_geo.sum()):,.0f}".replace(",", "."))

    st.info(
        "Como ler: cada linha representa território/localidade/setor ou assentamento. Distâncias altas indicam barreira "
        "territorial provável, mas não substituem rota real, tempo de deslocamento, validação da ERS e informação municipal."
    )

    if tem_geo.any():
        mapa = df.loc[tem_geo].copy()
        mapa["latitude"] = lat.loc[tem_geo]
        mapa["longitude"] = lon.loc[tem_geo]
        if dist_col:
            mapa["distancia_plot"] = pd.to_numeric(mapa[dist_col], errors="coerce").fillna(0)
        else:
            mapa["distancia_plot"] = 1

        hover_cols = [c for c in ["municipio", "territorio", "tipo_territorial", "classificacao_distancia", dist_col] if c and c in mapa.columns]
        fig = px.scatter_mapbox(
            mapa.head(1500),
            lat="latitude",
            lon="longitude",
            size="distancia_plot",
            color="classificacao_distancia" if "classificacao_distancia" in mapa.columns else None,
            hover_data=hover_cols,
            zoom=4,
            height=520,
            title="Territórios/localidades com coordenadas — leitura preliminar de distância"
        )
        fig.update_layout(mapbox_style="open-street-map", margin=dict(l=0, r=0, t=45, b=0))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning(
            "A camada territorial carregada não possui latitude/longitude válidas suficientes para desenhar o mapa nesta seção. "
            "A tabela abaixo permanece disponível para análise e validação dos dados."
        )

    # Tabela executiva com colunas existentes.
    cols_preferidas = [
        "municipio", "territorio", "tipo_territorial", "classificacao_distancia",
        "populacao", "populacao_estimada", "vulnerabilidade_social",
        "distancia_ubs_mais_proxima_km", "distancia_hospital_km",
        "latitude", "longitude"
    ]
    cols = [c for c in cols_preferidas if c in df.columns]
    if not cols:
        cols = list(df.columns[:12])

    st.dataframe(df[cols].head(300), use_container_width=True, hide_index=True)

    try:
        csv = df[cols].to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "Baixar territórios potencialmente desassistidos",
            data=csv,
            file_name="territorios_potencialmente_desassistidos.csv",
            mime="text/csv",
            use_container_width=True,
        )
    except Exception:
        pass

def _classificar_prioridade_territorial(row: pd.Series) -> str:
    classe = str(row.get("classe_distancia_aps", "")).strip().lower()
    distancia = float(pd.to_numeric(pd.Series([row.get("distancia_ubs_mais_proxima_km", 0)]), errors="coerce").fillna(0).iloc[0])
    tipo = str(row.get("tipo_analise", "")).lower()
    populacao = float(pd.to_numeric(pd.Series([row.get("populacao", 0)]), errors="coerce").fillna(0).iloc[0])
    if "crítico" in classe or "critico" in classe or distancia >= 15:
        return "Prioridade crítica"
    if "distante" in classe or distancia >= 10:
        return "Alta prioridade"
    if "assentamento" in tipo and distancia >= 7:
        return "Alta prioridade rural"
    if "atenção" in classe or "atencao" in classe or distancia >= 5:
        return "Monitoramento territorial"
    if populacao > 0 and distancia >= 3:
        return "Atenção preventiva"
    return "Monitoramento regular"


def _render_painel_territorios_desassistidos(base: pd.DataFrame):
    st.markdown("### Territórios potencialmente desassistidos")
    st.markdown(
        """
        <div style="background:#fff8ee;border-left:5px solid #d97706;border-radius:14px;padding:14px 18px;margin:8px 0 16px 0;color:#17324d;">
        <b>Foco da leitura:</b> identificar bairros, localidades, setores censitários e assentamentos rurais com maior distância até UBS/APS.
        A camada ajuda a responder <b>quem pode estar desassistido, onde está, qual UBS está mais próxima e qual distância precisa ser validada</b>.
        A distância é geodésica em linha reta, portanto deve ser conferida com rota real, ERS e município antes de decisão administrativa.
        </div>
        """,
        unsafe_allow_html=True,
    )

    colf1, colf2, colf3, colf4 = st.columns([1.1, 1.1, 1.0, 1.0])
    with colf1:
        regioes = ["Todas"]
        if "regiao_saude" in base.columns:
            regioes += sorted([r for r in base["regiao_saude"].dropna().astype(str).unique() if r and r.lower() != "none"])
        regiao_sel = st.selectbox("Região de Saúde", regioes, key="territorios_desassistidos_regiao")
    with colf2:
        base_mun = base.copy()
        if regiao_sel != "Todas" and "regiao_saude" in base_mun.columns:
            base_mun = base_mun[base_mun["regiao_saude"].astype(str).eq(regiao_sel)]
        municipios = ["Todos"] + sorted(base_mun["municipio"].dropna().astype(str).unique())
        municipio_sel = st.selectbox("Município", municipios, key="territorios_desassistidos_municipio")
    with colf3:
        tipo_sel = st.selectbox(
            "Tipo territorial",
            ["Todos", "Assentamento rural", "Bairro/localidade/setor"],
            key="territorios_desassistidos_tipo",
        )
    with colf4:
        classe_sel = st.selectbox(
            "Classe de distância",
            ["Todas", "Crítico", "Distante", "Atenção", "Próximo", "Sem cálculo"],
            key="territorios_desassistidos_classe",
        )

    municipio_param = None if municipio_sel == "Todos" else municipio_sel
    territorios = carregar_territorios_desassistidos(municipio=municipio_param, limite=2000)
    if territorios.empty:
        st.warning("A camada de territórios desassistidos ainda não retornou registros para esta seleção.")
        return

    if regiao_sel != "Todas" and "regiao_saude" in territorios.columns:
        territorios = territorios[territorios["regiao_saude"].astype(str).eq(regiao_sel)]
    if tipo_sel != "Todos" and "tipo_analise" in territorios.columns:
        territorios = territorios[territorios["tipo_analise"].astype(str).eq(tipo_sel)]
    if classe_sel != "Todas" and "classe_distancia_aps" in territorios.columns:
        territorios = territorios[territorios["classe_distancia_aps"].astype(str).eq(classe_sel)]

    if territorios.empty:
        st.info("Nenhum território encontrado após a aplicação dos filtros.")
        return

    territorios = territorios.copy()
    territorios["distancia_ubs_mais_proxima_km"] = pd.to_numeric(territorios.get("distancia_ubs_mais_proxima_km", 0), errors="coerce").fillna(0)
    territorios["populacao"] = pd.to_numeric(territorios.get("populacao", 0), errors="coerce").fillna(0)
    territorios["prioridade_territorial"] = territorios.apply(_classificar_prioridade_territorial, axis=1)
    territorios["populacao_x_distancia"] = territorios["populacao"] * territorios["distancia_ubs_mais_proxima_km"]

    criticos = territorios[territorios["prioridade_territorial"].isin(["Prioridade crítica", "Alta prioridade", "Alta prioridade rural"])]
    assentamentos = territorios[territorios.get("tipo_analise", pd.Series(dtype=str)).astype(str).str.contains("Assentamento", case=False, na=False)] if "tipo_analise" in territorios.columns else territorios.head(0)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        _card_html("Territórios em alerta", _fmt_int(len(criticos)), "crítica + alta prioridade", "validar com ERS/município")
    with c2:
        _card_html("População exposta", _fmt_int(criticos["populacao"].sum()), "nos territórios em alerta", "estimativa territorial")
    with c3:
        _card_html("Assentamentos rurais", _fmt_int(len(assentamentos)), "na seleção atual", "atenção à zona rural")
    with c4:
        _card_html("Maior distância", f"{_fmt_num(territorios['distancia_ubs_mais_proxima_km'].max(), 1)} km", "território → UBS/APS", "distância geodésica")

    st.markdown("#### Mapa dos territórios mais críticos")
    mapa = territorios[(pd.to_numeric(territorios.get("latitude", 0), errors="coerce") != 0) & (pd.to_numeric(territorios.get("longitude", 0), errors="coerce") != 0)].copy()
    colm1, colm2 = st.columns([1.35, 1])
    with colm1:
        if not mapa.empty:
            mapa_plot = mapa.sort_values(["prioridade_territorial", "distancia_ubs_mais_proxima_km"], ascending=[True, False]).head(500)
            fig = px.scatter_mapbox(
                mapa_plot,
                lat="latitude",
                lon="longitude",
                size="populacao",
                color="distancia_ubs_mais_proxima_km",
                hover_name="territorio",
                hover_data=[c for c in ["municipio", "regiao_saude", "tipo_analise", "tipo_territorio", "classe_distancia_aps", "prioridade_territorial", "ubs_mais_proxima", "populacao"] if c in mapa_plot.columns],
                zoom=4.6 if municipio_sel == "Todos" else 8.5,
                height=560,
                title="Distância territorial até a UBS/APS mais próxima",
                color_continuous_scale="YlOrRd",
                size_max=30,
            )
            fig.update_layout(mapbox_style="open-street-map", margin={"r": 0, "t": 42, "l": 0, "b": 0})
            fig.update_coloraxes(colorbar_title_text="Distância até UBS (km)")
            _plotly_chart(fig, "painel_territorios_desassistidos_mapa")
        else:
            st.info("Sem coordenadas suficientes para montar o mapa desta seleção.")
    with colm2:
        st.markdown("##### Distribuição por prioridade territorial")
        dist_prio = territorios["prioridade_territorial"].value_counts().reset_index()
        dist_prio.columns = ["prioridade", "territorios"]
        fig = px.bar(dist_prio.sort_values("territorios"), x="territorios", y="prioridade", orientation="h", text="territorios", title="Quantidade de territórios por prioridade")
        fig.update_layout(yaxis_title="", xaxis_title="Territórios")
        fig.update_layout(height=330, yaxis={"categoryorder": "total ascending"})
        _plotly_chart(fig, "painel_territorios_desassistidos_prioridade")

        st.markdown("##### Zona rural em destaque")
        if assentamentos.empty:
            st.info("Nenhum assentamento rural retornado para a seleção atual.")
        else:
            cols_ass = [c for c in ["municipio", "territorio", "classe_distancia_aps", "distancia_ubs_mais_proxima_km", "ubs_mais_proxima"] if c in assentamentos.columns]
            render_dataframe(assentamentos.sort_values("distancia_ubs_mais_proxima_km", ascending=False)[cols_ass].head(12), use_container_width=True, hide_index=True)

    st.markdown("#### Ranking dos territórios potencialmente desassistidos")
    cols_rank = [c for c in [
        "municipio", "regiao_saude", "tipo_analise", "territorio", "tipo_territorio", "populacao",
        "classe_distancia_aps", "prioridade_territorial", "distancia_ubs_mais_proxima_km",
        "ubs_mais_proxima", "municipio_ubs_mais_proxima", "metodo_calculo",
    ] if c in territorios.columns]
    ordenada = territorios.sort_values(["prioridade_territorial", "distancia_ubs_mais_proxima_km", "populacao_x_distancia"], ascending=[True, False, False])
    render_dataframe(ordenada[cols_rank].head(300), use_container_width=True, hide_index=True)

    st.markdown("#### Municípios e regiões com maior concentração de territórios distantes")
    colr1, colr2 = st.columns(2)
    with colr1:
        if "municipio" in territorios.columns:
            mun = territorios.groupby(["municipio", "regiao_saude" if "regiao_saude" in territorios.columns else "municipio"], dropna=False).agg(
                territorios=("territorio", "count"),
                populacao_exposta=("populacao", "sum"),
                distancia_media_km=("distancia_ubs_mais_proxima_km", "mean"),
                distancia_maxima_km=("distancia_ubs_mais_proxima_km", "max"),
            ).reset_index()
            mun = mun.sort_values(["distancia_maxima_km", "territorios"], ascending=False).head(20)
            render_dataframe(mun, use_container_width=True, hide_index=True)
    with colr2:
        if "regiao_saude" in territorios.columns:
            reg = territorios.groupby("regiao_saude", dropna=False).agg(
                territorios=("territorio", "count"),
                populacao_exposta=("populacao", "sum"),
                distancia_media_km=("distancia_ubs_mais_proxima_km", "mean"),
                distancia_maxima_km=("distancia_ubs_mais_proxima_km", "max"),
            ).reset_index().sort_values(["distancia_maxima_km", "territorios"], ascending=False)
            fig = px.bar(reg.sort_values("distancia_maxima_km"), y="regiao_saude", x="distancia_maxima_km", orientation="h", text="distancia_maxima_km", title="Maior distância por Região de Saúde")
            fig.update_layout(yaxis_title="", xaxis_title="Maior distância identificada (km)")
            fig.update_layout(height=460, yaxis={"categoryorder": "total ascending"})
            _plotly_chart(fig, "painel_territorios_desassistidos_regiao")

    _baixar_csv(ordenada[cols_rank], "painel_territorios_potencialmente_desassistidos.csv", "Baixar painel de territórios desassistidos")

def _render_vazio_integrado(base: pd.DataFrame):
    st.markdown("### Vazio Assistencial Integrado — leitura cruzada para priorização")
    _explicacao_vazio_integrado()

    filtrada = _filtrar_base(base, prefixo_key="dashboard_aps_vazio_integrado")
    resumo = _resumo_vazio_integrado(filtrada)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        _card_html("Municípios prioritários", _fmt_int(resumo.get("municipios_prioritarios", 0)), "crítica + alta prioridade", "fila estadual de validação")
    with c2:
        _card_html("População prioritária", _fmt_int(resumo.get("populacao_prioritaria", 0)), "em municípios prioritários", "estimativa municipal")
    with c3:
        _card_html("Sem equipe / sem UBS", f"{_fmt_int(resumo.get('sem_equipe', 0))} / {_fmt_int(resumo.get('sem_ubs', 0))}", "na seleção atual", "alerta cadastral/assistencial")
    with c4:
        _card_html("Região mais presente", str(resumo.get("regiao_top", "-")), "entre os prioritários", "pauta para pactuação")

    a1, a2, a3, a4 = st.columns(4)
    with a1:
        _card_html("Territórios distantes", _fmt_int(resumo.get("territorios_criticos_distantes", 0)), "críticos + distantes", "bairros/localidades/setores")
    with a2:
        _card_html("População exposta", _fmt_int(resumo.get("populacao_critica_distante", 0)), "em territórios críticos/distantes", "referência territorial")
    with a3:
        _card_html("Assentamentos distantes", _fmt_int(resumo.get("assentamentos_criticos_distantes", 0)), "críticos + distantes", "foco rural")
    with a4:
        _card_html("Maior distância", f"{_fmt_num(resumo.get('maior_distancia_territorial', 0), 1)} km", "território → UBS/APS", "linha reta; validar rota real")

    st.markdown("---")
    _render_top_prioritarios(filtrada, n=5)

    st.markdown("---")
    _render_territorios_desassistidos(filtrada)

    st.markdown("---")
    _render_pesos_score()

    col1, col2 = st.columns([1.15, 1])
    with col1:
        _grafico_quadrante_vazio(filtrada)
    with col2:
        top = filtrada.sort_values("score_prioridade_integrada", ascending=False).head(20).copy()
        if top.empty:
            st.info("Sem dados para o ranking nesta seleção.")
        else:
            fig = px.bar(
                top.sort_values("score_prioridade_integrada"),
                x="score_prioridade_integrada",
                y="municipio",
                color="classe_prioridade",
                orientation="h",
                title="Top 20 — prioridade integrada APS",
                hover_data=[c for c in ["principal_motivo_prioridade", "score_vazio_assistencial", "score_fragilidade_capacidade", "score_vulnerabilidade_social", "fatores_prioritarios"] if c in top.columns],
            )
            fig.update_layout(height=520, legend_title_text="Classe")
            _plotly_chart(fig, "grafico_operacional_municipal")

    _grafico_componentes_top(filtrada.sort_values("score_prioridade_integrada", ascending=False), n=15)
    _render_leitura_dos_fatores(filtrada)
    _render_ranking_regional_prioridade(filtrada)
    _render_validacao_dados(filtrada)

    st.markdown("#### Tabela de decisão — municípios, fatores, ação sugerida e validação")
    cols = [
        "posicao_prioridade", "municipio", "regiao_saude", "populacao", "total_ubs", "total_equipes_aps",
        "populacao_por_equipe", "populacao_por_ubs", "distancia_media_territorios_km", "distancia_maxima_territorios_km",
        "territorios_criticos_distantes", "populacao_territorios_criticos_distantes", "assentamentos_criticos_distantes",
        "score_acesso_territorial", "score_vazio_assistencial", "score_fragilidade_capacidade",
        "score_vulnerabilidade_social", "score_equidade_territorial", "score_prioridade_integrada", "classe_prioridade",
        "principal_motivo_prioridade", "fatores_prioritarios", "acao_sugerida", "qualidade_dados_score",
        "classe_qualidade_dados", "validacao_recomendada", "alerta_acao",
    ]
    cols = [c for c in cols if c in filtrada.columns]
    render_dataframe(filtrada.sort_values("score_prioridade_integrada", ascending=False)[cols], use_container_width=True, hide_index=True)
    _baixar_csv(filtrada[cols], "vazio_assistencial_integrado_aps.csv", "Baixar vazio assistencial integrado")



def _render_perfis_alertas(base: pd.DataFrame):
    st.markdown("### Perfis municipais APS e alertas analíticos")
    st.markdown(
        """
        Esta aba fecha a primeira fase analítica usando apenas os dados já integrados ao sistema.
        O objetivo é transformar o ranking em **tipologias de problema**, evidenciar desequilíbrios internos e
        apontar municípios que podem estar com o risco subestimado pelo ranking geral.
        """
    )

    filtrada = _filtrar_base(base, prefixo_key="perfis_alertas_aps")
    perfis = construir_perfis_alertas_aps(filtrada)
    if perfis.empty:
        st.warning("Não foi possível montar os perfis analíticos para a seleção atual.")
        return

    criticos = perfis[perfis["classe_prioridade"].isin(["Prioridade crítica", "Alta prioridade"])]
    risco_sub = perfis[perfis["risco_subestimado"].eq("Sim")]
    desequilibrio_alto = perfis[perfis["classe_desequilibrio_intramunicipal"].isin(["Desequilíbrio intramunicipal crítico", "Desequilíbrio intramunicipal alto"])]

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        _card_html("Perfis classificados", _fmt_int(len(perfis)), "municípios na seleção", "tipologia gerencial")
    with c2:
        _card_html("Prioridade alta/crítica", _fmt_int(len(criticos)), "ranking estadual", "demanda decisão")
    with c3:
        _card_html("Risco subestimado", _fmt_int(len(risco_sub)), "fora do topo, mas com alerta", "não perder municípios invisíveis")
    with c4:
        _card_html("Desequilíbrio interno", _fmt_int(len(desequilibrio_alto)), "alto ou crítico", "média municipal pode esconder vazios")

    st.markdown("#### Distribuição dos perfis municipais")
    col1, col2 = st.columns([1.1, 1])
    with col1:
        dist = perfis.groupby("perfil_municipal_aps", dropna=False).size().reset_index(name="municípios").sort_values("municípios", ascending=True)
        fig = px.bar(dist, x="municípios", y="perfil_municipal_aps", orientation="h", text="municípios", title="Municípios por perfil analítico APS")
        fig.update_layout(yaxis_title="", xaxis_title="Municípios")
        fig.update_layout(height=520, yaxis={"categoryorder": "total ascending"})
        _plotly_chart(fig, "perfis_municipais_distribuicao")
    with col2:
        comp = perfis.groupby("componente_dominante", dropna=False).size().reset_index(name="municípios").sort_values("municípios", ascending=False)
        fig = px.pie(comp, names="componente_dominante", values="municípios", hole=.45, title="Componente dominante do problema")
        _plotly_chart(fig, "perfis_componentes_dominantes")

    st.markdown("#### Municípios com risco subestimado")
    st.caption("Municípios que não estão necessariamente em prioridade crítica/alta, mas possuem sinais territoriais relevantes: grande distância, assentamento em alerta, desequilíbrio intramunicipal, território crítico ou baixa qualidade de dados.")
    if risco_sub.empty:
        st.success("Na seleção atual, não há municípios com risco subestimado segundo os gatilhos atuais.")
    else:
        cols_sub = [
            "municipio", "regiao_saude", "classe_prioridade", "score_prioridade_integrada",
            "perfil_municipal_aps", "classe_desequilibrio_intramunicipal", "sinais_alerta_oculto",
            "distancia_maxima_territorios_km", "territorios_criticos_distantes",
            "assentamentos_criticos_distantes", "qualidade_dados_score", "resposta_recomendada",
        ]
        cols_sub = [c for c in cols_sub if c in risco_sub.columns]
        render_dataframe(risco_sub[cols_sub].head(80), use_container_width=True, hide_index=True)

    st.markdown("#### Desequilíbrio intramunicipal")
    col3, col4 = st.columns([1, 1])
    with col3:
        top_des = perfis.sort_values("desequilibrio_intramunicipal_score", ascending=False).head(20)
        fig = px.bar(
            top_des.sort_values("desequilibrio_intramunicipal_score"),
            x="desequilibrio_intramunicipal_score",
            y="municipio",
            color="classe_desequilibrio_intramunicipal",
            orientation="h",
            title="Top 20 — desequilíbrio intramunicipal",
            hover_data=["distancia_maxima_territorios_km", "territorios_criticos_distantes", "assentamentos_criticos_distantes"],
        )
        fig.update_layout(height=540, legend_title_text="Classe")
        _plotly_chart(fig, "perfis_desequilibrio_intramunicipal")
    with col4:
        st.info(
            "A leitura de desequilíbrio intramunicipal procura identificar municípios em que o dado agregado pode parecer razoável, "
            "mas há bairros, localidades, setores, comunidades rurais ou assentamentos muito distantes da UBS/APS de referência."
        )
        matriz = pd.DataFrame([
            {"problema identificado": "Vazio territorial rural", "resposta estratégica": "validar rotas reais, equipe volante, agenda itinerante, transporte sanitário, unidade de apoio ou estudo locacional de UBS"},
            {"problema identificado": "Pressão população/equipe", "resposta estratégica": "avaliar suficiência de equipes, adscrição, cobertura real e eventual ampliação/reorganização da APS"},
            {"problema identificado": "Fragilidade de capacidade", "resposta estratégica": "verificar UBS, estrutura física, profissionais, CNES/INE e capacidade operacional"},
            {"problema identificado": "Vulnerabilidade social", "resposta estratégica": "integrar APS com vigilância, assistência social, busca ativa e ações intersetoriais"},
            {"problema identificado": "Risco subestimado", "resposta estratégica": "não descartar pelo ranking geral; validar alerta territorial com ERS e município"},
            {"problema identificado": "Baixa qualidade de dados", "resposta estratégica": "priorizar saneamento de base antes de decisão administrativa"},
        ])
        render_dataframe(matriz, use_container_width=True, hide_index=True)

    st.markdown("#### Tabela final de perfis e alertas")
    cols = [
        "municipio", "regiao_saude", "populacao", "classe_prioridade", "score_prioridade_integrada",
        "posicao_prioridade", "perfil_municipal_aps", "componente_dominante", "justificativa_perfil",
        "desequilibrio_intramunicipal_score", "classe_desequilibrio_intramunicipal", "risco_subestimado",
        "sinais_alerta_oculto", "resposta_recomendada", "validacao_recomendada",
    ]
    cols = [c for c in cols if c in perfis.columns]
    render_dataframe(perfis[cols], use_container_width=True, hide_index=True)
    _baixar_csv(perfis[cols], "perfis_alertas_municipais_aps.csv", "Baixar perfis e alertas")


def _render_carteira_acoes(base: pd.DataFrame):
    st.markdown("### Carteira preliminar de ações APS")
    st.markdown(
        """
        Esta aba transforma o diagnóstico integrado em uma **carteira gerencial de encaminhamentos**.
        Não substitui análise normativa, projeto técnico ou pactuação formal; ela organiza prioridades para reunião com ERS,
        município, coordenadoria e gestão estadual.
        """
    )

    filtrada = _filtrar_base(base, prefixo_key="carteira_acoes_aps")
    carteira = construir_carteira_intervencoes_aps(filtrada)
    if carteira.empty:
        st.warning("Não foi possível montar a carteira de ações para a seleção atual.")
        return

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        _card_html("Ações mapeadas", _fmt_int(len(carteira)), "encaminhamentos sugeridos", "não normativo")
    with c2:
        _card_html("Muito alta/Alta", _fmt_int(carteira["urgencia"].isin(["Muito alta", "Alta"]).sum()), "ações prioritárias", "pactuação regional")
    with c3:
        rural = carteira[carteira["eixo_intervencao"].astype(str).str.contains("Acesso territorial|Equidade", case=False, na=False)]
        _card_html("Acesso/equidade", _fmt_int(len(rural)), "zona rural e territórios especiais", "validar com ERS")
    with c4:
        pop = pd.to_numeric(carteira.get("populacao_territorios_criticos_distantes", 0), errors="coerce").fillna(0).sum()
        _card_html("População exposta", _fmt_int(pop), "em territórios críticos/distantes", "estimativa territorial")

    st.markdown("#### Distribuição dos encaminhamentos")
    col1, col2 = st.columns(2)
    with col1:
        eixo = carteira.groupby("eixo_intervencao", dropna=False).size().reset_index(name="ações").sort_values("ações", ascending=True)
        fig = px.bar(eixo, x="ações", y="eixo_intervencao", orientation="h", text="ações", title="Ações sugeridas por eixo de intervenção")
        fig.update_layout(yaxis_title="", xaxis_title="Ações sugeridas")
        _plotly_chart(fig, "carteira_acoes_eixo")
    with col2:
        urg = carteira.groupby("urgencia", dropna=False).size().reset_index(name="ações")
        ordem = ["Muito alta", "Alta", "Média", "Baixa"]
        urg["ordem"] = urg["urgencia"].map({v: i for i, v in enumerate(ordem)}).fillna(9)
        urg = urg.sort_values("ordem")
        fig = px.bar(urg, x="urgencia", y="ações", text="ações", title="Ações por urgência sugerida")
        _plotly_chart(fig, "carteira_acoes_urgencia")

    st.markdown("#### Top encaminhamentos para decisão")
    cols = [
        "municipio", "regiao_saude", "classe_prioridade", "score_prioridade_integrada", "urgencia",
        "eixo_intervencao", "tipo_acao", "acao_recomendada", "evidencia", "prazo_sugerido",
        "territorios_criticos_distantes", "assentamentos_criticos_distantes", "distancia_maxima_territorios_km",
    ]
    cols = [c for c in cols if c in carteira.columns]
    render_dataframe(carteira[cols].head(120), use_container_width=True, hide_index=True)

    st.markdown("#### Síntese regional da carteira")
    regional = carteira.groupby(["regiao_saude", "urgencia"], dropna=False).size().reset_index(name="ações")
    if not regional.empty:
        fig = px.bar(regional, x="regiao_saude", y="ações", color="urgencia", title="Ações sugeridas por Região de Saúde e urgência")
        fig.update_layout(xaxis_tickangle=-45)
        _plotly_chart(fig, "carteira_acoes_regiao")

    st.markdown("#### Leitura técnica")
    st.info(
        "Use esta carteira como lista preliminar de discussão. Antes de encaminhar decisão, validar distância real, rota de acesso, "
        "situação do CNES/INE, cobertura efetiva, adscrição territorial, capacidade física da UBS e evidências locais informadas pelo município/ERS."
    )
    _baixar_csv(carteira[cols], "carteira_preliminar_acoes_aps.csv", "Baixar carteira preliminar de ações")


# -----------------------------------------------------------------------------
# ETAPA 13-A — Camada visual executiva e infográficos
# -----------------------------------------------------------------------------

def _to_num(valor, default: float = 0.0) -> float:
    try:
        if pd.isna(valor):
            return default
        if isinstance(valor, str):
            valor = valor.strip().replace("R$", "").replace("%", "").replace(".", "").replace(",", ".")
            if not valor:
                return default
        return float(valor)
    except Exception:
        return default


def _col_num(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if df is None or df.empty or col not in df.columns:
        return pd.Series([default] * (len(df) if isinstance(df, pd.DataFrame) else 0), dtype="float64")
    return pd.to_numeric(df[col], errors="coerce").fillna(default)


def _fmt_moeda(valor) -> str:
    try:
        return "R$ " + f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def _fmt_pct(valor, casas: int = 1) -> str:
    try:
        return f"{float(valor):.{casas}f}%".replace(".", ",")
    except Exception:
        return "-"


def _safe_div(num, den) -> float:
    try:
        num = float(num or 0)
        den = float(den or 0)
        return (num / den * 100) if den else 0.0
    except Exception:
        return 0.0


def _merge_mds_dashboard(base: pd.DataFrame) -> pd.DataFrame:
    """Acopla o consolidado MDS ao dashboard, quando a tabela já existir.

    O dashboard antigo não dependia do MDS. Esta função é defensiva: se a tabela
    ainda não existir, ou se algum campo não estiver disponível, a tela continua
    funcionando com os dados já existentes.
    """
    if base is None or base.empty:
        return pd.DataFrame()
    out = base.copy()
    try:
        mds = read_table("mds_cadunico_bolsa_familia_municipal")
    except Exception:
        mds = pd.DataFrame()
    if mds.empty:
        return out

    # Normaliza chaves para cruzamento por código IBGE ou nome do município.
    if "codigo_ibge" in out.columns and "codigo_ibge" in mds.columns:
        out["_codigo_ibge_join"] = out["codigo_ibge"].astype(str).str.replace(r"\D", "", regex=True).str[:7]
        mds["_codigo_ibge_join"] = mds["codigo_ibge"].astype(str).str.replace(r"\D", "", regex=True).str[:7]
        key = "_codigo_ibge_join"
    elif "municipio" in out.columns and "municipio" in mds.columns:
        out["_municipio_join"] = out["municipio"].astype(str).str.upper().str.strip()
        mds["_municipio_join"] = mds["municipio"].astype(str).str.upper().str.strip()
        key = "_municipio_join"
    else:
        return out

    keep = [key]
    wanted = [
        "cadunico_familias", "cadunico_pessoas", "percentual_populacao_cadunico",
        "bolsa_familia_familias", "bolsa_familia_pessoas", "percentual_populacao_bolsa_familia",
        "bolsa_familia_valor_repassado", "bolsa_familia_valor_medio_informado",
        "cadunico_familias_pobreza_extrema", "cadunico_familias_baixa_renda",
        "bpc_total", "bpc_pcd", "bpc_idoso", "bpc_cadunico_total",
        "familias_pbf_esgotamento_inadequado", "cadunico_rua_familias_total",
        "score_vulnerabilidade_mds", "ranking_vulnerabilidade_mds", "classificacao_vulnerabilidade_mds",
    ]
    keep += [c for c in wanted if c in mds.columns]
    mds = mds[keep].drop_duplicates(key)
    merged = out.merge(mds, on=key, how="left", suffixes=("", "_mds"))

    # Quando a base municipal já tiver colunas antigas de MDS vazias, prioriza o consolidado novo.
    for c in wanted:
        cm = f"{c}_mds"
        if cm in merged.columns:
            if c in merged.columns:
                merged[c] = merged[c].where(pd.to_numeric(merged[c], errors="coerce").notna() & (pd.to_numeric(merged[c], errors="coerce") != 0), merged[cm])
                merged = merged.drop(columns=[cm])
            else:
                merged = merged.rename(columns={cm: c})
    return merged.drop(columns=[c for c in ["_codigo_ibge_join", "_municipio_join"] if c in merged.columns], errors="ignore")


def _inject_visual_dashboard_css():
    st.markdown(
        """
        <style>
        .dash13-hero {
            background: radial-gradient(circle at 12% 20%, rgba(32,167,201,.22), transparent 32%),
                        linear-gradient(135deg, #17206A 0%, #23378E 47%, #0C7897 100%);
            border-radius: 26px;
            padding: 26px 28px;
            color: white;
            box-shadow: 0 16px 38px rgba(17, 24, 39, .18);
            margin: 6px 0 18px 0;
        }
        .dash13-hero h2 {margin:0;font-size:2.0rem;line-height:1.08;font-weight:950;letter-spacing:-.03em;}
        .dash13-hero p {margin:10px 0 0 0;max-width:980px;font-size:1rem;line-height:1.5;color:#EAF7FF;}
        .dash13-pill {display:inline-block;background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.22);border-radius:999px;padding:7px 11px;font-size:.78rem;font-weight:850;margin:0 7px 8px 0;color:#fff;}
        .dash13-card {
            background: linear-gradient(180deg,#FFFFFF 0%,#F8FBFF 100%);
            border: 1px solid #E4ECF7;
            border-radius: 22px;
            padding: 17px 18px;
            min-height: 152px;
            box-shadow: 0 10px 24px rgba(15,43,71,.065);
            position: relative;
            overflow: hidden;
        }
        .dash13-card:before {content:"";position:absolute;right:-18px;top:-18px;width:78px;height:78px;border-radius:50%;background:rgba(32,167,201,.10);}
        .dash13-icon {font-size:1.45rem;margin-bottom:7px;}
        .dash13-title {font-size:.76rem;color:#60748A;text-transform:uppercase;letter-spacing:.055em;font-weight:900;}
        .dash13-value {font-size:1.82rem;color:#1E276F;font-weight:950;line-height:1.05;margin-top:5px;}
        .dash13-sub {font-size:.86rem;color:#425466;margin-top:7px;line-height:1.35;}
        .dash13-badge {display:inline-block;margin-top:10px;border-radius:999px;padding:4px 9px;font-size:.73rem;font-weight:900;}
        .dash13-ok {background:#E8F7EF;color:#168A5B;}.dash13-warn {background:#FFF5DF;color:#A65F00;}.dash13-danger {background:#FFE8E8;color:#BD3E3E;}.dash13-info {background:#EEF1FF;color:#1E276F;}
        .dash13-section-title {font-size:1.25rem;font-weight:950;color:#1E276F;margin:22px 0 4px 0;}
        .dash13-section-sub {font-size:.92rem;color:#667085;margin:0 0 14px 0;}
        .dash13-note {background:#EEF7FF;border-left:5px solid #20A7C9;border-radius:16px;padding:14px 16px;margin:12px 0;color:#17324D;line-height:1.45;}
        .dash13-warning {background:#FFF8EE;border-left:5px solid #D98A14;border-radius:16px;padding:14px 16px;margin:12px 0;color:#17324D;line-height:1.45;}
        .dash13-timeline {position:relative;padding:8px 0 0 0;margin:8px 0 16px 0;}
        .dash13-step {display:flex;align-items:flex-start;gap:14px;margin-bottom:12px;}
        .dash13-num {min-width:42px;height:42px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#20A7C9,#1E276F);color:white;font-weight:950;box-shadow:0 8px 18px rgba(30,39,111,.18);}
        .dash13-stepbox {background:white;border:1px solid #E4ECF7;border-radius:16px;padding:12px 14px;box-shadow:0 8px 20px rgba(15,43,71,.05);width:100%;}
        .dash13-stepbox b {color:#1E276F;}.dash13-stepbox span {display:block;color:#667085;font-size:.86rem;margin-top:3px;line-height:1.35;}
        .dash13-thermo {background:white;border:1px solid #E4ECF7;border-radius:22px;padding:18px;box-shadow:0 10px 24px rgba(15,43,71,.06);}
        .dash13-barbg {height:22px;background:linear-gradient(90deg,#16A36A 0%,#F2C94C 45%,#F2994A 70%,#D64545 100%);border-radius:999px;position:relative;margin-top:16px;}
        .dash13-pointer {position:absolute;top:-9px;width:3px;height:40px;background:#1E276F;border-radius:3px;box-shadow:0 0 0 5px rgba(30,39,111,.12);}
        .dash13-labels {display:flex;justify-content:space-between;font-size:.73rem;color:#667085;margin-top:8px;font-weight:750;}
        .dash13-mini-grid {display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-top:10px;}
        .dash13-mini {background:#F8FBFF;border:1px solid #E4ECF7;border-radius:15px;padding:12px;text-align:center;min-height:112px;}
        .dash13-mini strong {display:block;color:#1E276F;font-size:1.06rem;}.dash13-mini span {font-size:.72rem;color:#667085;line-height:1.35;}
        .dash13-mini em {display:block;font-style:normal;color:#1E276F;font-weight:900;font-size:.82rem;margin-top:7px;}
        .dash13-mini small {display:block;color:#98A2B3;font-size:.68rem;margin-top:2px;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _visual_card(titulo: str, valor: str, subtitulo: str, status: str = "info", icon: str = "📌", badge: str = "Leitura gerencial"):
    classe = {"ok": "dash13-ok", "warn": "dash13-warn", "danger": "dash13-danger", "info": "dash13-info"}.get(status, "dash13-info")
    st.markdown(
        f"""
        <div class="dash13-card">
            <div class="dash13-icon">{icon}</div>
            <div class="dash13-title">{titulo}</div>
            <div class="dash13-value">{valor}</div>
            <div class="dash13-sub">{subtitulo}</div>
            <span class="dash13-badge {classe}">{badge}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_timeline_leitura():
    etapas = [
        ("1", "Território e população", "Localiza municípios, regiões, população e pressão territorial inicial."),
        ("2", "Capacidade APS", "Cruza UBS, equipes, profissionais e códigos CNES/INE da Atenção Primária."),
        ("3", "Vulnerabilidade social", "Integra CadÚnico, Bolsa Família, BPC, pobreza, baixa renda e situação de rua."),
        ("4", "Eventos de saúde", "Acopla SINASC, SIM e SINAN para evidenciar riscos materno-infantis, mortalidade e agravos."),
        ("5", "Prioridade técnica", "Transforma os sinais em ranking, classificação, alerta e sugestão preliminar de encaminhamento."),
    ]
    html = '<div class="dash13-timeline">'
    for num, titulo, texto in etapas:
        html += f'<div class="dash13-step"><div class="dash13-num">{num}</div><div class="dash13-stepbox"><b>{titulo}</b><span>{texto}</span></div></div>'
    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)


def _render_termometro_score(score: float, titulo: str = "Termômetro da prioridade integrada"):
    score = max(0, min(100, _to_num(score)))
    if score >= 75:
        cls, txt = "dash13-danger", "Prioridade crítica"
    elif score >= 50:
        cls, txt = "dash13-warn", "Alta prioridade / atenção forte"
    elif score >= 25:
        cls, txt = "dash13-info", "Monitoramento intensivo"
    else:
        cls, txt = "dash13-ok", "Monitoramento regular"
    st.markdown(
        f"""
        <div class="dash13-thermo">
            <div class="dash13-title">{titulo}</div>
            <div class="dash13-value">{_fmt_num(score, 1)}</div>
            <div class="dash13-sub">O ponteiro mostra a posição média da seleção na régua estadual de priorização APS.</div>
            <div class="dash13-barbg"><div class="dash13-pointer" style="left:calc({score}% - 1px);"></div></div>
            <div class="dash13-labels"><span>Baixa</span><span>Média</span><span>Alta</span><span>Crítica</span></div>
            <span class="dash13-badge {cls}">{txt}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _metric_sum(df: pd.DataFrame, col: str) -> float:
    return float(_col_num(df, col).sum()) if col in df.columns else 0.0


def _render_mds_snapshot(base: pd.DataFrame):
    campos = [
        ("cadunico_pessoas", "Pessoas CadÚnico", "pessoas em base social"),
        ("bolsa_familia_pessoas", "Pessoas PBF", "beneficiárias diretas"),
        ("bolsa_familia_valor_repassado", "Valor PBF", "transferência mensal"),
        ("bpc_total", "Beneficiários BPC", "idosos e PCD"),
        ("cadunico_familias_pobreza_extrema", "Famílias pobreza/extrema", "maior alerta social"),
    ]
    disponiveis = [c for c, _, _ in campos if c in base.columns]
    if not disponiveis:
        st.info("O consolidado MDS ainda não foi encontrado no dashboard. Após importar a Etapa 12-A, este bloco exibirá CadÚnico, Bolsa Família e BPC.")
        return

    # Denominadores: usa população estadual quando disponível e, para pobreza/extrema, usa famílias do CadÚnico.
    pop_total = _metric_sum(base, "populacao") if "populacao" in base.columns else 0.0
    cad_pessoas = _metric_sum(base, "cadunico_pessoas")
    pbf_pessoas = _metric_sum(base, "bolsa_familia_pessoas")
    bpc_total = _metric_sum(base, "bpc_total")
    cad_familias = _metric_sum(base, "cadunico_familias")
    pobreza_familias = _metric_sum(base, "cadunico_familias_pobreza_extrema")
    valor_pbf = _metric_sum(base, "bolsa_familia_valor_repassado")
    pbf_familias = _metric_sum(base, "bolsa_familia_familias")

    extras = {
        "cadunico_pessoas": (_fmt_pct(_safe_div(cad_pessoas, pop_total)), "da população estadual"),
        "bolsa_familia_pessoas": (_fmt_pct(_safe_div(pbf_pessoas, pop_total)), "da população estadual"),
        "bpc_total": (_fmt_pct(_safe_div(bpc_total, pop_total)), "da população estadual"),
        "cadunico_familias_pobreza_extrema": (_fmt_pct(_safe_div(pobreza_familias, cad_familias)), "das famílias CadÚnico"),
    }
    if valor_pbf and pbf_pessoas:
        extras["bolsa_familia_valor_repassado"] = (_fmt_moeda(valor_pbf / max(pbf_pessoas, 1)), "por beneficiário/mês")
    elif valor_pbf and pbf_familias:
        extras["bolsa_familia_valor_repassado"] = (_fmt_moeda(valor_pbf / max(pbf_familias, 1)), "por família/mês")
    else:
        extras["bolsa_familia_valor_repassado"] = ("-", "média não disponível")

    st.markdown('<div class="dash13-section-title">Infográfico social — CadÚnico, Bolsa Família e BPC</div>', unsafe_allow_html=True)
    st.markdown('<p class="dash13-section-sub">Leitura estadual da vulnerabilidade social integrada à priorização territorial da APS. Cada card mostra número absoluto e percentual/razão com o denominador usado.</p>', unsafe_allow_html=True)
    cols = st.columns(5)
    for col, (campo, rotulo, desc) in zip(cols, campos):
        with col:
            valor = _metric_sum(base, campo)
            if "valor" in campo:
                valor_fmt = _fmt_moeda(valor)
            else:
                valor_fmt = _fmt_int(valor)
            extra, denom = extras.get(campo, ("-", "denominador indisponível"))
            st.markdown(
                f'<div class="dash13-mini"><strong>{valor_fmt}</strong><span>{rotulo}<br>{desc}</span><em>{extra}</em><small>{denom}</small></div>',
                unsafe_allow_html=True,
            )
    st.markdown(
        '<div class="dash13-note"><b>Como ler:</b> os percentuais usam a população estadual quando o indicador é de pessoas. Para famílias em pobreza/extrema pobreza, o denominador preferencial é o total de famílias no CadÚnico. O valor do PBF é apresentado também como média mensal por beneficiário ou família quando houver denominador disponível.</div>',
        unsafe_allow_html=True,
    )


def _render_resumo_automatico(base: pd.DataFrame, resumo: dict):
    top = base.sort_values("score_prioridade_integrada", ascending=False).head(1) if "score_prioridade_integrada" in base.columns else base.head(1)
    mun_top = str(top.iloc[0].get("municipio", "-")) if not top.empty else "-"
    reg_top = str(top.iloc[0].get("regiao_saude", "-")) if not top.empty else "-"
    score_top = _fmt_num(top.iloc[0].get("score_prioridade_integrada", 0), 1) if not top.empty else "-"
    prioritarios = int(resumo.get("prioridade_critica", 0) or 0) + int(resumo.get("alta_prioridade", 0) or 0)
    mds_txt = ""
    if "cadunico_pessoas" in base.columns and _metric_sum(base, "cadunico_pessoas") > 0:
        mds_txt = f" A camada MDS aponta {_fmt_int(_metric_sum(base, 'cadunico_pessoas'))} pessoas no CadÚnico e {_fmt_int(_metric_sum(base, 'bolsa_familia_pessoas'))} pessoas beneficiárias do Bolsa Família."
    st.markdown(
        f"""
        <div class="dash13-note">
        <b>Síntese executiva automática:</b> a base atual monitora <b>{_fmt_int(resumo.get('municipios', 0))} municípios</b> e identifica
        <b>{_fmt_int(prioritarios)} municípios</b> em prioridade crítica ou alta prioridade. O município com maior score na régua integrada é
        <b>{mun_top}</b>, na Região de Saúde <b>{reg_top}</b>, com score <b>{score_top}</b>.{mds_txt}
        Esta leitura organiza a triagem técnica; a decisão final deve considerar validação da APS, ERS, CNES, território e pactuação local.
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_grafico_ranking_visual(base: pd.DataFrame, n: int = 12):
    if base.empty or "score_prioridade_integrada" not in base.columns:
        return
    top = base.sort_values("score_prioridade_integrada", ascending=False).head(n).copy()
    if top.empty:
        return
    fig = px.bar(
        top.sort_values("score_prioridade_integrada"),
        x="score_prioridade_integrada",
        y="municipio",
        orientation="h",
        color="classe_prioridade" if "classe_prioridade" in top.columns else None,
        text=top["score_prioridade_integrada"].round(1),
        hover_data=[c for c in ["regiao_saude", "populacao", "total_ubs", "total_equipes_aps", "score_vulnerabilidade_social", "score_acesso_territorial"] if c in top.columns],
        title="Top municípios por prioridade integrada APS",
    )
    fig.update_layout(height=520, xaxis_title="Score integrado", yaxis_title="", legend_title_text="Classe")
    fig.update_layout(yaxis={"categoryorder": "total ascending"})
    _plotly_chart(fig, "grafico_dashboard13_ranking_visual")


def _render_grafico_social_x_capacidade(base: pd.DataFrame):
    if base.empty:
        return
    xcol = "score_fragilidade_capacidade" if "score_fragilidade_capacidade" in base.columns else "populacao_por_equipe"
    ycol = "score_vulnerabilidade_social" if "score_vulnerabilidade_social" in base.columns else "percentual_populacao_bolsa_familia"
    if xcol not in base.columns or ycol not in base.columns:
        return
    fig = px.scatter(
        base,
        x=xcol,
        y=ycol,
        size="populacao" if "populacao" in base.columns else None,
        color="score_prioridade_integrada" if "score_prioridade_integrada" in base.columns else None,
        hover_name="municipio",
        hover_data=[c for c in ["regiao_saude", "classe_prioridade", "total_ubs", "total_equipes_aps", "cadunico_pessoas", "bolsa_familia_pessoas"] if c in base.columns],
        title="Matriz visual: vulnerabilidade social x capacidade instalada APS",
        color_continuous_scale="YlOrRd",
        size_max=34,
    )
    fig.add_hline(y=50, line_dash="dash", line_width=1)
    if xcol.startswith("score"):
        fig.add_vline(x=50, line_dash="dash", line_width=1)
    fig.update_layout(height=520, xaxis_title="Fragilidade da capacidade / pressão", yaxis_title="Vulnerabilidade social")
    _plotly_chart(fig, "grafico_dashboard13_social_capacidade")


def _render_infografico_fontes():
    st.markdown('<div class="dash13-section-title">Linha do tempo da inteligência territorial</div>', unsafe_allow_html=True)
    st.markdown('<p class="dash13-section-sub">O sistema deixa de ser apenas uma base de dados e passa a conduzir a leitura técnica.</p>', unsafe_allow_html=True)
    _render_timeline_leitura()


def _render_cards_top_prioritarios_visual(base: pd.DataFrame, n: int = 4):
    if base.empty or "score_prioridade_integrada" not in base.columns:
        return
    top = base.sort_values("score_prioridade_integrada", ascending=False).head(n).reset_index(drop=True)
    st.markdown('<div class="dash13-section-title">Municípios em destaque para decisão</div>', unsafe_allow_html=True)
    st.markdown('<p class="dash13-section-sub">Cards explicativos com motivo, score e encaminhamento preliminar.</p>', unsafe_allow_html=True)
    cols = st.columns(n)
    for i, row in top.iterrows():
        with cols[i]:
            municipio = str(row.get("municipio", "-"))
            score = _fmt_num(row.get("score_prioridade_integrada", 0), 1)
            classe = str(row.get("classe_prioridade", "Prioridade"))
            regiao = str(row.get("regiao_saude", "-"))
            motivo = str(row.get("principal_motivo_prioridade", row.get("fatores_prioritarios", "Ver componentes do score")))
            if len(motivo) > 100:
                motivo = motivo[:100] + "…"
            status = "danger" if "crítica" in classe.lower() or "critica" in classe.lower() else "warn" if "alta" in classe.lower() else "info"
            _visual_card(f"#{i+1} {municipio}", score, f"{regiao}. {motivo}", status=status, icon="📍", badge=classe)


def _render_dashboard_estrategico_visual(base_original: pd.DataFrame):
    _inject_visual_dashboard_css()
    base = _merge_mds_dashboard(base_original)
    resumo = resumo_estadual(base)

    st.markdown(
        """
        <div class="dash13-hero">
            <span class="dash13-pill">Etapa 13-A</span>
            <span class="dash13-pill">Dashboard estratégico</span>
            <span class="dash13-pill">Inteligência Territorial APS</span>
            <h2>Painel executivo que transforma dados em decisão</h2>
            <p>Visão integrada de território, vulnerabilidade social, capacidade APS, vazios assistenciais e prioridade técnica municipal. A tela foi redesenhada para reduzir a frieza das tabelas e conduzir a leitura da gestão.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _render_resumo_automatico(base, resumo)

    st.markdown('<div class="dash13-section-title">Sinais executivos do Estado</div>', unsafe_allow_html=True)
    st.markdown('<p class="dash13-section-sub">Cards explicativos para leitura rápida antes dos gráficos e rankings.</p>', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        _visual_card("Municípios monitorados", _fmt_int(resumo.get("municipios", 0)), "Cobertura estadual da base municipal consolidada.", "ok", "🗺️", "142 municípios esperados")
    with c2:
        _visual_card("População analisada", _fmt_int(resumo.get("populacao", 0)), "Base populacional usada para calcular pressão, cobertura e percentuais.", "info", "👥", "denominador oficial")
    with c3:
        _visual_card("UBS / estabelecimentos", _fmt_int(resumo.get("ubs", 0)), f"Média estimada: {_fmt_num(resumo.get('pop_por_ubs', 0), 1)} hab./UBS.", "info", "🏥", "capacidade instalada")
    with c4:
        _visual_card("Equipes APS", _fmt_int(resumo.get("equipes", 0)), f"Códigos 70, 71, 72, 73, 74 e 76; {_fmt_num(resumo.get('pop_por_equipe', 0), 1)} hab./equipe.", "info", "👩‍⚕️", "CNES/INE")

    c5, c6, c7, c8 = st.columns(4)
    crit = resumo.get("prioridade_critica", 0)
    alta = resumo.get("alta_prioridade", 0)
    score_medio = _col_num(base, "score_prioridade_integrada").mean() if "score_prioridade_integrada" in base.columns else 0
    with c5:
        _visual_card("Prioridade crítica", _fmt_int(crit), "Municípios no topo da régua integrada. Exigem leitura imediata.", "danger", "🚨", "despacho prioritário")
    with c6:
        _visual_card("Alta prioridade", _fmt_int(alta), "Municípios com alerta forte para pactuação regional e validação técnica.", "warn", "⚠️", "atenção regional")
    with c7:
        _visual_card("Score médio APS", _fmt_num(score_medio, 1), "Média estadual da prioridade integrada considerando acesso, capacidade e vulnerabilidade.", "info", "📊", "régua 0 a 100")
    with c8:
        prof = resumo.get("profissionais", 0)
        _visual_card("Profissionais CNES", _fmt_int(prof), "Vínculos profissionais usados para leitura de capacidade de resposta da APS.", "ok", "🧑‍💼", "força de trabalho")

    _render_mds_snapshot(base)

    st.markdown('<div class="dash13-section-title">Mapa, termômetro e distribuição de prioridade</div>', unsafe_allow_html=True)
    colmap, colside = st.columns([1.35, 1])
    with colmap:
        _grafico_mapa(base, "Mapa estratégico — prioridade integrada municipal")
    with colside:
        _render_termometro_score(score_medio)
        st.markdown("<br>", unsafe_allow_html=True)
        if "classe_prioridade" in base.columns:
            dist = base["classe_prioridade"].astype(str).value_counts().reset_index()
            dist.columns = ["classe", "municipios"]
            fig = px.pie(dist, names="classe", values="municipios", hole=.55, title="Distribuição dos municípios por classe")
            fig.update_layout(height=380)
            _plotly_chart(fig, "grafico_dashboard13_distribuicao_classe")

    _render_cards_top_prioritarios_visual(base, n=4)

    colA, colB = st.columns([1.05, 1])
    with colA:
        _render_grafico_ranking_visual(base, n=12)
    with colB:
        _render_grafico_social_x_capacidade(base)

    _render_infografico_fontes()

    st.markdown('<div class="dash13-section-title">Tabela técnica de apoio</div>', unsafe_allow_html=True)
    st.markdown('<p class="dash13-section-sub">A tabela continua disponível, mas passa a ser apoio técnico — não o centro da experiência visual.</p>', unsafe_allow_html=True)
    top_cols = [
        "posicao_prioridade", "municipio", "regiao_saude", "populacao", "total_ubs", "total_equipes_aps",
        "populacao_por_equipe", "score_prioridade_integrada", "classe_prioridade", "score_vulnerabilidade_social",
        "score_acesso_territorial", "cadunico_pessoas", "bolsa_familia_pessoas", "bpc_total", "fatores_prioritarios", "alerta_acao",
    ]
    top_cols = [c for c in top_cols if c in base.columns]
    if "score_prioridade_integrada" in base.columns:
        tabela = base.sort_values("score_prioridade_integrada", ascending=False)[top_cols].head(30)
    else:
        tabela = base[top_cols].head(30)
    render_dataframe(tabela, use_container_width=True, hide_index=True)
    _baixar_csv(tabela, "dashboard_estrategico_top_prioridades.csv", "Baixar Top prioridades estratégicas")



def _render_inteligencia_cruzada_aps(base_original: pd.DataFrame):
    """Etapa 13-B — cruzamentos estratégicos para decisão APS."""
    _inject_visual_dashboard_css()
    st.markdown(
        """
        <div class="dash13-hero">
            <span class="dash13-pill">Etapa 13-B</span>
            <span class="dash13-pill">Inteligência cruzada</span>
            <span class="dash13-pill">Decisão APS</span>
            <h2>Cruzamentos que transformam base de dados em decisão estratégica</h2>
            <p>Esta leitura combina vulnerabilidade social, capacidade instalada da APS, pressão assistencial, mortalidade, nascimentos, SINAN, educação e território. O objetivo é indicar onde a gestão deve olhar primeiro, por quê e com qual tipo de resposta.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    try:
        decisao = carregar_inteligencia_cruzada_aps(base_original)
    except Exception as e:
        st.error(f"Não foi possível montar a inteligência cruzada APS: {e}")
        return

    if decisao.empty:
        st.warning("A base de inteligência cruzada ainda não pôde ser montada. Verifique se a base municipal e as importações MDS/DATASUS/CNES estão disponíveis.")
        return

    resumo = resumo_inteligencia_cruzada(decisao)
    st.markdown('<div class="dash13-section-title">Síntese estratégica automática</div>', unsafe_allow_html=True)
    st.markdown(f"""
    <div class="dash13-insight">
        {gerar_sintese_decisao(decisao)}
    </div>
    """, unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        _visual_card("Municípios avaliados", _fmt_int(resumo.get("municipios", 0)), "Municípios com leitura integrada de dados sociais, sanitários e estrutura APS.", "ok", "🗺️", "base cruzada")
    with c2:
        _visual_card("Prioridade crítica", _fmt_int(resumo.get("prioridade_critica", 0)), "Municípios no topo da régua integrada de decisão.", "danger", "🚨", "validar primeiro")
    with c3:
        _visual_card("Alta prioridade", _fmt_int(resumo.get("alta_prioridade", 0)), "Municípios com alerta forte para apoio técnico e pactuação regional.", "warn", "⚠️", "atenção regional")
    with c4:
        _visual_card("Score médio integrado", _fmt_num(resumo.get("score_medio", 0), 1), "Média estadual da régua composta de decisão APS.", "info", "📊", "0 a 100")

    c5, c6, c7, c8 = st.columns(4)
    with c5:
        _visual_card("Vulnerabilidade social", _fmt_num(resumo.get("vulnerabilidade_media", 0), 1), "Síntese MDS: CadÚnico, PBF, BPC e pobreza/extrema pobreza.", "info", "🤝", "determinantes sociais")
    with c6:
        _visual_card("Pressão assistencial", _fmt_num(resumo.get("pressao_media", 0), 1), "População, nascimentos, óbitos e demanda potencial sobre UBS/equipes.", "info", "🧭", "demanda sobre rede")
    with c7:
        _visual_card("Fragilidade de capacidade", _fmt_num(resumo.get("fragilidade_media", 0), 1), "Leitura relativa de suficiência de equipes, UBS e profissionais APS.", "warn", "🏥", "estrutura APS")
    with c8:
        _visual_card("Alerta sanitário", _fmt_num(resumo.get("alerta_sanitario_medio", 0), 1), "Sinais de SINAN, mortalidade infantil e perfil materno-infantil.", "warn", "🦠", "vigilância + APS")

    st.markdown('<div class="dash13-section-title">Matriz de decisão: vulnerabilidade social x capacidade APS</div>', unsafe_allow_html=True)
    st.markdown('<p class="dash13-section-sub">Quadrantes para orientar o tipo de resposta: expansão/reorganização de capacidade, apoio técnico, monitoramento ou validação territorial.</p>', unsafe_allow_html=True)
    col1, col2 = st.columns([1.15, 1])
    with col1:
        fig = px.scatter(
            decisao,
            x="score_capacidade_aps_decisao",
            y="score_vulnerabilidade_social_decisao",
            size="populacao" if "populacao" in decisao.columns else None,
            color="classificacao_prioridade_decisao",
            hover_name="municipio",
            hover_data=[c for c in ["regiao_saude", "ranking_prioridade_decisao", "score_prioridade_integrada_decisao", "pct_pop_cadunico_decisao", "pct_pop_pbf_decisao", "pop_por_equipe_decisao"] if c in decisao.columns],
            title="Quadrante de decisão — Vulnerabilidade social x Capacidade APS",
        )
        fig.add_vline(x=45, line_dash="dash", opacity=.4)
        fig.add_hline(y=55, line_dash="dash", opacity=.4)
        fig.update_layout(height=520, xaxis_title="Capacidade APS relativa", yaxis_title="Vulnerabilidade social relativa")
        _plotly_chart(fig, "grafico_inteligencia_quadrante_social_capacidade")
    with col2:
        matriz = matriz_decisao_estrategica(decisao)
        if not matriz.empty:
            fig = px.bar(matriz, y="quadrante_decisao", x="municipios", orientation="h", text="municipios", color="score_medio", title="Municípios por quadrante")
            fig.update_layout(height=520, yaxis_title="", xaxis_title="Municípios")
            _plotly_chart(fig, "grafico_inteligencia_quadrantes_barras")

    st.markdown('<div class="dash13-section-title">Ranking integrado de decisão APS</div>', unsafe_allow_html=True)
    st.markdown('<p class="dash13-section-sub">O ranking combina pressão social, capacidade APS, alertas sanitários e acesso territorial. Ele é preliminar e serve para organizar validação técnica.</p>', unsafe_allow_html=True)
    top = decisao.sort_values("score_prioridade_integrada_decisao", ascending=False).head(15).copy()
    fig = px.bar(
        top.sort_values("score_prioridade_integrada_decisao"),
        y="municipio",
        x="score_prioridade_integrada_decisao",
        orientation="h",
        color="classificacao_prioridade_decisao",
        text="score_prioridade_integrada_decisao",
        hover_data=[c for c in ["regiao_saude", "motivo_prioridade_decisao", "encaminhamento_decisao"] if c in top.columns],
        title="Top 15 municípios — prioridade integrada para decisão APS",
    )
    fig.update_layout(height=620, yaxis_title="", xaxis_title="Score integrado")
    fig.update_xaxes(range=[0, 100])
    _plotly_chart(fig, "grafico_inteligencia_top15_decisao")

    st.markdown('<div class="dash13-section-title">Componentes que puxam a prioridade</div>', unsafe_allow_html=True)
    st.markdown('<p class="dash13-section-sub">Mostra se a prioridade é mais social, assistencial, estrutural, sanitária ou territorial.</p>', unsafe_allow_html=True)
    comp_cols = [
        "score_vulnerabilidade_social_decisao",
        "score_pressao_assistencial_decisao",
        "score_fragilidade_capacidade_decisao",
        "score_alerta_sanitario_decisao",
        "score_acesso_territorial_decisao",
    ]
    top_comp = decisao.head(12)[["municipio"] + [c for c in comp_cols if c in decisao.columns]].copy()
    if not top_comp.empty:
        nomes = {
            "score_vulnerabilidade_social_decisao": "Vulnerabilidade social",
            "score_pressao_assistencial_decisao": "Pressão assistencial",
            "score_fragilidade_capacidade_decisao": "Fragilidade capacidade APS",
            "score_alerta_sanitario_decisao": "Alerta sanitário",
            "score_acesso_territorial_decisao": "Acesso territorial",
        }
        long = top_comp.melt(id_vars="municipio", var_name="componente", value_name="score")
        long["componente"] = long["componente"].map(nomes).fillna(long["componente"])
        fig = px.bar(long, x="municipio", y="score", color="componente", title="Composição dos municípios prioritários", barmode="stack")
        fig.update_layout(height=520, xaxis_tickangle=-35, xaxis_title="", yaxis_title="Soma dos componentes")
        _plotly_chart(fig, "grafico_inteligencia_componentes_prioridade")

    st.markdown('<div class="dash13-section-title">Leitura regional para pactuação</div>', unsafe_allow_html=True)
    regional = ranking_regional_decisao(decisao)
    if not regional.empty:
        colr1, colr2 = st.columns(2)
        with colr1:
            fig = px.bar(regional.sort_values("score_medio"), y="regiao_saude", x="score_medio", orientation="h", text="score_medio", title="Score médio integrado por Região de Saúde")
            fig.update_layout(height=520, yaxis_title="", xaxis_title="Score médio")
            _plotly_chart(fig, "grafico_inteligencia_regional_score")
        with colr2:
            fig = px.bar(regional.sort_values("municipios_prioridade_alta_critica"), y="regiao_saude", x="municipios_prioridade_alta_critica", orientation="h", text="municipios_prioridade_alta_critica", title="Municípios em alta/crítica por Região de Saúde")
            fig.update_layout(height=520, yaxis_title="", xaxis_title="Municípios")
            _plotly_chart(fig, "grafico_inteligencia_regional_prioritarios")
        render_dataframe(regional, use_container_width=True, hide_index=True)
        _baixar_csv(regional, "inteligencia_cruzada_aps_resumo_regional.csv", "Baixar resumo regional da inteligência cruzada")

    st.markdown('<div class="dash13-section-title">Tabela executiva com motivo e encaminhamento</div>', unsafe_allow_html=True)
    cols = [
        "ranking_prioridade_decisao", "municipio", "regiao_saude", "populacao",
        "score_prioridade_integrada_decisao", "classificacao_prioridade_decisao", "quadrante_decisao",
        "score_vulnerabilidade_social_decisao", "score_pressao_assistencial_decisao", "score_fragilidade_capacidade_decisao",
        "score_alerta_sanitario_decisao", "score_acesso_territorial_decisao",
        "pct_pop_cadunico_decisao", "pct_pop_pbf_decisao", "bpc_por_mil_hab_decisao",
        "pop_por_equipe_decisao", "pop_por_ubs_decisao", "sinan_registros_por_10mil_decisao",
        "motivo_prioridade_decisao", "encaminhamento_decisao",
    ]
    cols = [c for c in cols if c in decisao.columns]
    tabela = decisao[cols].copy()
    render_dataframe(tabela, use_container_width=True, hide_index=True)
    _baixar_csv(tabela, "inteligencia_cruzada_aps_ranking_decisao.csv", "Baixar ranking de decisão APS")



def _render_motor_decisao_aps(base_original: pd.DataFrame):
    """Etapa 13-C — motor avançado de inteligência estratégica APS."""
    _inject_visual_dashboard_css()
    st.markdown(
        """
        <div class="dash13-hero">
            <span class="dash13-pill">Etapa 13-C</span>
            <span class="dash13-pill">Motor de decisão</span>
            <span class="dash13-pill">Pesos recalibrados</span>
            <h2>Motor de Inteligência Estratégica APS</h2>
            <p>Esta camada reorganiza os pesos considerando todas as bases já consolidadas: IBGE, MDS, CNES, SINASC, SIM, SINAN, INEP e georreferenciamento. O objetivo é gerar uma leitura mais justa entre risco relativo, volume absoluto, capacidade de resposta e alertas sanitários.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    try:
        motor = carregar_motor_inteligencia_estrategica(base_original)
    except Exception as e:
        st.error(f"Não foi possível montar o motor de decisão APS: {e}")
        return

    if motor.empty:
        st.warning("O motor de decisão ainda não pôde ser montado. Verifique se as bases MDS, CNES, DATASUS e consolidado municipal estão disponíveis.")
        return

    resumo = resumo_motor_inteligencia(motor)
    st.markdown('<div class="dash13-section-title">Síntese executiva do motor</div>', unsafe_allow_html=True)
    st.markdown(f"""
    <div class="dash13-insight">
        {gerar_sintese_motor(motor)}
    </div>
    """, unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        _visual_card("Municípios avaliados", _fmt_int(resumo.get("municipios", 0)), "Base municipal cruzada com MDS, CNES, DATASUS, INEP e demais fontes disponíveis.", "ok", "🗺️", "cobertura")
    with c2:
        _visual_card("Crítica integrada", _fmt_int(resumo.get("criticos", 0)), "Municípios que combinam forte risco relativo, fragilidade de resposta e/ou alertas sanitários.", "danger", "🚨", "priorizar")
    with c3:
        _visual_card("Alta prioridade", _fmt_int(resumo.get("alta", 0)), "Municípios que devem entrar em agenda regional de validação e apoio técnico.", "warn", "⚠️", "pactuação")
    with c4:
        _visual_card("Score médio do motor", _fmt_num(resumo.get("score_medio", 0), 1), "Média estadual na régua integrada de decisão APS, de 0 a 100.", "info", "📊", "régua técnica")

    c5, c6, c7, c8 = st.columns(4)
    with c5:
        _visual_card("Social médio", _fmt_num(resumo.get("social_medio", 0), 1), "Eixo MDS: CadÚnico, PBF, BPC e pobreza/extrema pobreza.", "info", "🤝", "25% do motor")
    with c6:
        _visual_card("Fragilidade APS", _fmt_num(resumo.get("capacidade_medio", 0), 1), "Eixo CNES: leitura da suficiência relativa de UBS, equipes e profissionais.", "warn", "🏥", "20% do motor")
    with c7:
        _visual_card("Pressão assistencial", _fmt_num(resumo.get("pressao_medio", 0), 1), "Combina pressão relativa e volume absoluto para não invisibilizar polos populosos.", "info", "🧭", "15% do motor")
    with c8:
        _visual_card("Cobertura analítica", _fmt_num(resumo.get("cobertura_media", 0), 1) + "%", "Percentual médio de eixos com dados úteis na leitura municipal.", "ok", "✅", "transparência")

    with st.expander("Metodologia dos pesos — régua preliminar para validação das áreas", expanded=True):
        st.markdown(
            """
            A ponderação foi redesenhada para a primeira versão estável da base. Ela não é uma regra oficial de financiamento; é uma régua técnica preliminar para organizar validação com áreas e chefias.

            **Princípio adotado:** priorizar equidade e capacidade de resposta, sem perder municípios-polo que concentram grande volume absoluto de pessoas vulneráveis. A régua final combina 55% do score bruto ponderado e 45% da posição relativa estadual, para funcionar como fila técnica de validação.
            """
        )
        pesos = metodologia_pesos_motor_aps()
        render_dataframe(pesos, use_container_width=True, hide_index=True)

    st.markdown('<div class="dash13-section-title">Ranking principal — prioridade integrada APS</div>', unsafe_allow_html=True)
    st.markdown('<p class="dash13-section-sub">O ranking final combina pesos temáticos e deve ser lido como fila técnica de validação, não como decisão automática.</p>', unsafe_allow_html=True)
    top = motor.sort_values("score_motor_prioridade_aps", ascending=False).head(20).copy()
    fig = px.bar(
        top.sort_values("score_motor_prioridade_aps"),
        y="municipio",
        x="score_motor_prioridade_aps",
        orientation="h",
        color="classificacao_motor_prioridade",
        text="score_motor_prioridade_aps",
        hover_data=[c for c in ["regiao_saude", "perfil_estrategico", "alertas_automaticos", "recomendacao_motor"] if c in top.columns],
        title="Top 20 municípios — motor de prioridade integrada APS",
    )
    fig.update_layout(height=720, yaxis_title="", xaxis_title="Score do motor")
    fig.update_xaxes(range=[0, 100])
    _plotly_chart(fig, "grafico_motor_top20_prioridade")

    st.markdown('<div class="dash13-section-title">Risco relativo x volume absoluto</div>', unsafe_allow_html=True)
    st.markdown('<p class="dash13-section-sub">Essa matriz diferencia municípios pequenos de altíssimo risco proporcional e municípios-polo que concentram grande volume de pessoas vulneráveis.</p>', unsafe_allow_html=True)
    col1, col2 = st.columns([1.2, 1])
    with col1:
        fig = px.scatter(
            motor,
            x="score_volume_absoluto",
            y="eixo_vulnerabilidade_social",
            size="populacao" if "populacao" in motor.columns else None,
            color="perfil_estrategico",
            hover_name="municipio",
            hover_data=[c for c in ["regiao_saude", "ranking_motor_prioridade", "score_motor_prioridade_aps", "classificacao_motor_prioridade", "pct_pop_cadunico_decisao", "pct_pop_pbf_decisao"] if c in motor.columns],
            title="Matriz de decisão — volume absoluto x vulnerabilidade relativa",
        )
        fig.add_vline(x=60, line_dash="dash", opacity=.35)
        fig.add_hline(y=60, line_dash="dash", opacity=.35)
        fig.update_layout(height=620, xaxis_title="Volume absoluto de demanda", yaxis_title="Vulnerabilidade relativa")
        _plotly_chart(fig, "grafico_motor_risco_volume")
    with col2:
        perfis = resumo_perfis_motor(motor)
        if not perfis.empty:
            fig = px.bar(
                perfis.sort_values("municipios"),
                y="perfil_estrategico",
                x="municipios",
                orientation="h",
                color="score_medio",
                text="municipios",
                title="Perfis estratégicos encontrados",
            )
            fig.update_layout(height=620, yaxis_title="", xaxis_title="Municípios")
            _plotly_chart(fig, "grafico_motor_perfis")

    st.markdown('<div class="dash13-section-title">Componentes explicativos do motor</div>', unsafe_allow_html=True)
    st.markdown('<p class="dash13-section-sub">Mostra quais eixos puxam a prioridade dos municípios no topo. Isso ajuda a diferenciar resposta social, assistencial, vigilância, materno-infantil ou estrutural.</p>', unsafe_allow_html=True)
    comp_cols = {
        "eixo_vulnerabilidade_social": "Social",
        "eixo_fragilidade_capacidade_aps": "Fragilidade APS",
        "eixo_pressao_assistencial": "Pressão",
        "eixo_vigilancia_agravos": "Vigilância",
        "eixo_materno_infantil": "Materno-infantil",
        "eixo_mortalidade": "Mortalidade",
        "eixo_acesso_territorial": "Acesso",
        "eixo_intersetorial_educacao": "Intersetorial",
    }
    top_comp = motor.head(12)[["municipio"] + [c for c in comp_cols if c in motor.columns]].copy()
    if not top_comp.empty:
        long = top_comp.melt(id_vars="municipio", var_name="eixo", value_name="score")
        long["eixo"] = long["eixo"].map(comp_cols).fillna(long["eixo"])
        fig = px.bar(long, x="municipio", y="score", color="eixo", barmode="stack", title="Composição temática dos municípios prioritários")
        fig.update_layout(height=560, xaxis_title="", yaxis_title="Soma dos scores dos eixos", xaxis_tickangle=-35)
        _plotly_chart(fig, "grafico_motor_componentes_stack")

    st.markdown('<div class="dash13-section-title">Ranking relativo x ranking absoluto x ranking final</div>', unsafe_allow_html=True)
    st.markdown('<p class="dash13-section-sub">Serve para explicar por que municípios grandes podem ser estratégicos pelo volume e municípios pequenos podem ser estratégicos pela proporção de vulnerabilidade.</p>', unsafe_allow_html=True)
    cols_rank = ["municipio", "regiao_saude", "ranking_motor_prioridade", "ranking_risco_relativo", "ranking_volume_absoluto", "score_motor_prioridade_aps", "eixo_vulnerabilidade_social", "score_volume_absoluto", "perfil_estrategico"]
    cols_rank = [c for c in cols_rank if c in motor.columns]
    render_dataframe(motor[cols_rank].head(40), use_container_width=True, hide_index=True)

    st.markdown('<div class="dash13-section-title">Leitura regional para pactuação</div>', unsafe_allow_html=True)
    regional = ranking_regional_motor(motor)
    if not regional.empty:
        colr1, colr2 = st.columns(2)
        with colr1:
            fig = px.bar(regional.sort_values("score_medio"), y="regiao_saude", x="score_medio", orientation="h", text="score_medio", title="Score médio do motor por Região de Saúde")
            fig.update_layout(height=540, yaxis_title="", xaxis_title="Score médio")
            _plotly_chart(fig, "grafico_motor_regional_score")
        with colr2:
            fig = px.bar(regional.sort_values("municipios_alta_critica"), y="regiao_saude", x="municipios_alta_critica", orientation="h", text="municipios_alta_critica", title="Alta/crítica integrada por Região de Saúde")
            fig.update_layout(height=540, yaxis_title="", xaxis_title="Municípios")
            _plotly_chart(fig, "grafico_motor_regional_alta_critica")
        render_dataframe(regional, use_container_width=True, hide_index=True)
        _baixar_csv(regional, "motor_inteligencia_aps_resumo_regional.csv", "Baixar leitura regional do motor")

    st.markdown('<div class="dash13-section-title">Tabela executiva para decisão</div>', unsafe_allow_html=True)
    tabela_cols = [
        "ranking_motor_prioridade", "municipio", "regiao_saude", "populacao", "score_motor_prioridade_aps",
        "classificacao_motor_prioridade", "perfil_estrategico", "alertas_automaticos", "recomendacao_motor",
        "eixo_vulnerabilidade_social", "eixo_fragilidade_capacidade_aps", "eixo_pressao_assistencial",
        "eixo_vigilancia_agravos", "eixo_materno_infantil", "eixo_mortalidade", "eixo_acesso_territorial",
        "eixo_intersetorial_educacao", "score_volume_absoluto", "ranking_risco_relativo", "ranking_volume_absoluto",
        "leitura_automatica_motor",
    ]
    tabela_cols = [c for c in tabela_cols if c in motor.columns]
    tabela = motor[tabela_cols].copy()
    render_dataframe(tabela, use_container_width=True, hide_index=True)
    _baixar_csv(tabela, "motor_inteligencia_aps_ranking_completo.csv", "Baixar ranking completo do motor APS")


def render():
    st.subheader("Dashboard de Inteligência Territorial da APS")
    _painel_metodologia_compacto()
    st.markdown(
        """
        <div class="aviso-metodologico">
            <strong>Leitura preliminar para priorização técnica.</strong>
            Os gráficos e scores organizam sinais de acesso, capacidade, vulnerabilidade e território com base nas fontes já integradas.
            Eles não substituem validação da equipe APS, dos Escritórios Regionais de Saúde e dos municípios antes de qualquer decisão administrativa.
        </div>
        """,
        unsafe_allow_html=True,
    )

    base = carregar_base_dashboard()
    if base.empty:
        st.warning("A base municipal ainda não está disponível. Vá em Base de Dados > Consolidação e gere a base municipal consolidada.")
        return

    abas = st.tabs([
        "Estratégico",
        "Inteligência cruzada",
        "Motor de decisão",
        "Vazio integrado",
        "Territórios desassistidos",
        "Carteira de ações",
        "Perfis e alertas",
        "Tático regional",
        "Operacional municipal",
        "Ranking integrado",
        "Metodologia e fontes",
    ])

    with abas[0]:
        _render_dashboard_estrategico_visual(base)

    with abas[1]:
        _render_inteligencia_cruzada_aps(base)

    with abas[2]:
        _render_motor_decisao_aps(base)

    with abas[3]:
        _render_vazio_integrado(base)

    with abas[4]:
        _render_painel_territorios_desassistidos(base)

    with abas[5]:
        _render_carteira_acoes(base)

    with abas[6]:
        _render_perfis_alertas(base)

    with abas[7]:
        st.markdown("### Visão tática — Regiões de Saúde e pactuação")
        filtrada = _filtrar_base(base, prefixo_key="dashboard_aps_tatico")
        regional = resumo_regional_dashboard(filtrada)
        if regional.empty:
            st.warning("Não foi possível montar o resumo regional para a seleção atual.")
        else:
            col1, col2 = st.columns(2)
            with col1:
                fig = px.bar(regional, y="regiao_saude", x="score_medio", orientation="h", text="score_medio", title="Score médio de prioridade por Região de Saúde")
                fig.update_layout(yaxis_title="", xaxis_title="Score médio")
                fig.update_layout(yaxis={"categoryorder": "total ascending"})
                _plotly_chart(fig, "grafico_vazio_pesos_score")
            with col2:
                fig = px.scatter(
                    regional,
                    x="populacao_por_equipe",
                    y="vulnerabilidade_media",
                    size="populacao",
                    color="score_medio",
                    hover_name="regiao_saude",
                    title="Pressão assistencial x vulnerabilidade social",
                    color_continuous_scale="YlOrRd",
                )
                _plotly_chart(fig, "grafico_vazio_top_componentes")

            col3, col4 = st.columns(2)
            with col3:
                fig = px.bar(regional, x="regiao_saude", y="municipios_prioritarios", text="municipios_prioritarios", title="Municípios prioritários por região")
                fig.update_layout(xaxis_title="", yaxis_title="Municípios prioritários")
                fig.update_layout(xaxis_tickangle=-45)
                _plotly_chart(fig, "grafico_vazio_prioridade_regiao")
            with col4:
                fig = px.bar(regional, x="regiao_saude", y=["vazio_medio", "vulnerabilidade_media", "equidade_media"], title="Composição média da prioridade regional")
                fig.update_layout(xaxis_tickangle=-45, legend_title_text="Componente")
                _plotly_chart(fig, "grafico_vazio_qualidade_dados")

            st.markdown("### Matriz regional para reunião de pactuação")
            render_dataframe(regional, use_container_width=True, hide_index=True)
            _baixar_csv(regional, "dashboard_aps_resumo_regional.csv", "Baixar resumo regional")

        st.markdown("### Leitura dos municípios da seleção")
        cols = [
            "municipio", "regiao_saude", "populacao", "total_ubs", "total_equipes_aps", "total_profissionais_aps",
            "populacao_por_equipe", "populacao_por_ubs", "score_vazio_assistencial", "score_vulnerabilidade_social",
            "score_equidade_territorial", "score_prioridade_integrada", "classe_prioridade", "alerta_acao",
        ]
        cols = [c for c in cols if c in filtrada.columns]
        render_dataframe(filtrada[cols], use_container_width=True, hide_index=True)

    with abas[8]:
        st.markdown("### Visão operacional — diagnóstico municipal e territórios")
        municipios = sorted(base["municipio"].dropna().astype(str).unique())
        default_idx = 0
        municipio = st.selectbox(
            "Município para análise operacional",
            municipios,
            index=default_idx,
            key="dashboard_aps_operacional_municipio",
        )
        linha = base[base["municipio"].astype(str).eq(municipio)].head(1)
        if linha.empty:
            st.info("Selecione um município válido.")
        else:
            r = linha.iloc[0]
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                _card_html("Prioridade integrada", _fmt_num(r.get("score_prioridade_integrada", 0)), str(r.get("classe_prioridade", "")), str(r.get("regiao_saude", "")))
            with c2:
                _card_html("Pop./Equipe", _fmt_num(r.get("populacao_por_equipe", 0)), f"{_fmt_int(r.get('total_equipes_aps', 0))} equipes APS", "capacidade cadastrada")
            with c3:
                _card_html("Pop./UBS", _fmt_num(r.get("populacao_por_ubs", 0)), f"{_fmt_int(r.get('total_ubs', 0))} UBS/estab.", "pressão territorial")
            with c4:
                _card_html("Territórios mapeados", _fmt_int(r.get("territorios_mapeados", 0)), f"{_fmt_int(r.get('territorios_pressao_alta', 0))} com pressão alta", "setores/localidades")

            st.markdown(f"**Alertas operacionais:** {r.get('alerta_acao', '-')}")

            componentes = pd.DataFrame([
                {"componente": "Acesso territorial à UBS", "score": r.get("score_acesso_territorial", 0)},
                {"componente": "Pressão assistencial", "score": r.get("score_vazio_assistencial", 0)},
                {"componente": "Vulnerabilidade social", "score": r.get("score_vulnerabilidade_social", 0)},
                {"componente": "Fragilidade de capacidade", "score": r.get("score_fragilidade_capacidade", 0)},
                {"componente": "Equidade territorial", "score": r.get("score_equidade_territorial", 0)},
            ])
            fig = px.bar(componentes, x="componente", y="score", text="score", title=f"Composição da prioridade — {municipio}")
            fig.update_layout(xaxis_title="", yaxis_title="Score")
            fig.update_yaxes(range=[0, 100])
            _plotly_chart(fig, "grafico_metodologia_fontes")

            sub1, sub2, sub3 = st.tabs(["Territórios prioritários", "Unidades e equipes", "Encaminhamentos sugeridos"])
            with sub1:
                terr = carregar_territorios_prioritarios(municipio, limite=100)
                if terr.empty:
                    st.info("Não há base territorial detalhada para este município.")
                else:
                    mapa_terr = terr[(pd.to_numeric(terr.get("latitude", 0), errors="coerce") != 0) & (pd.to_numeric(terr.get("longitude", 0), errors="coerce") != 0)]
                    if not mapa_terr.empty:
                        fig = px.scatter_mapbox(
                            mapa_terr,
                            lat="latitude",
                            lon="longitude",
                            size="populacao",
                            color="indicador_pressao_aps",
                            hover_name="bairro_ou_localidade",
                            hover_data=[c for c in ["populacao", "percentual_baixa_renda", "percentual_baixa_escolaridade", "percentual_saneamento_inadequado"] if c in mapa_terr.columns],
                            zoom=10,
                            height=430,
                            title="Territórios com maior pressão APS",
                            color_continuous_scale="YlOrRd",
                        )
                        fig.update_layout(mapbox_style="open-street-map", margin={"r": 0, "t": 42, "l": 0, "b": 0})
                        fig.update_coloraxes(colorbar_title_text="Pressão APS")
                        _plotly_chart(fig, "grafico_metodologia_confiabilidade")
                    render_dataframe(terr, use_container_width=True, hide_index=True)
                    _baixar_csv(terr, f"territorios_prioritarios_{municipio}.csv", "Baixar territórios do município")

            with sub2:
                colu1, colu2 = st.columns(2)
                with colu1:
                    unidades = carregar_unidades_municipio(municipio)
                    st.markdown("#### Unidades/estabelecimentos")
                    if unidades.empty:
                        st.info("Sem unidades na base para este município.")
                    else:
                        cols_u = [c for c in ["cnes", "nome_unidade", "tipo_unidade", "bairro", "endereco", "latitude", "longitude"] if c in unidades.columns]
                        render_dataframe(unidades[cols_u], use_container_width=True, hide_index=True)
                with colu2:
                    equipes = carregar_equipes_municipio(municipio)
                    st.markdown("#### Equipes APS")
                    if equipes.empty:
                        st.info("Sem equipes na base para este município.")
                    else:
                        resumo_eq = equipes.groupby(["codigo_tipo_equipe", "tipo_equipe"], dropna=False).agg(
                            equipes=("ine", "nunique"),
                            estabelecimentos=("cnes", "nunique"),
                        ).reset_index().sort_values("codigo_tipo_equipe")
                        render_dataframe(resumo_eq, use_container_width=True, hide_index=True)

            with sub3:
                sugestoes = []
                if r.get("score_acesso_territorial", 0) >= 55 or r.get("assentamentos_criticos_distantes", 0) > 0:
                    sugestoes.append("Priorizar validação de acesso territorial até UBS, com atenção a comunidades rurais, assentamentos, rotas reais e referência APS.")
                if r.get("populacao_por_equipe", 0) > 4000:
                    sugestoes.append("Reavaliar suficiência de equipes APS e priorizar expansão/reorganização de cobertura.")
                if r.get("populacao_por_ubs", 0) > 12000:
                    sugestoes.append("Verificar distribuição territorial das UBS e estudar vazios assistenciais intramunicipais.")
                if r.get("score_vulnerabilidade_social", 0) >= 60:
                    sugestoes.append("Associar ações de APS a estratégias de comunicação acessível, busca ativa e articulação intersetorial.")
                if r.get("score_equidade_territorial", 0) >= 60:
                    sugestoes.append("Priorizar análise de acesso para povos/comunidades tradicionais, assentamentos ou territórios especiais.")
                if not sugestoes:
                    sugestoes.append("Manter monitoramento regular, validando dados com ERS e município antes de qualquer decisão de expansão.")
                st.markdown("#### Encaminhamentos técnicos sugeridos")
                for i, s in enumerate(sugestoes, start=1):
                    st.markdown(f"**{i}.** {s}")

    with abas[9]:
        st.markdown("### Ranking integrado de prioridade APS")
        filtrada = _filtrar_base(base, prefixo_key="dashboard_aps_ranking")
        _grafico_componentes_top(filtrada, n=min(15, len(filtrada)))
        cols = [
            "municipio", "regiao_saude", "populacao", "total_ubs", "total_equipes_aps", "total_profissionais_aps",
            "populacao_por_equipe", "populacao_por_ubs", "taxa_analfabetismo_estimado_pct", "nivel_instrucao_baixo_pct",
            "pct_rdpc_ate_1_2_sm_2022", "indice_vulnerabilidade_saneamento_2022", "terras_indigenas_qtd_registros",
            "assentamentos_qtd_registros", "distancia_media_territorios_km", "distancia_maxima_territorios_km",
            "territorios_criticos_distantes", "assentamentos_criticos_distantes", "score_acesso_territorial", "score_vazio_assistencial", "score_vulnerabilidade_social", "score_fragilidade_capacidade",
            "score_equidade_territorial", "score_prioridade_integrada", "classe_prioridade", "alerta_acao",
        ]
        cols = [c for c in cols if c in filtrada.columns]
        render_dataframe(filtrada[cols], use_container_width=True, hide_index=True)
        _baixar_csv(filtrada[cols], "dashboard_aps_ranking_integrado.csv", "Baixar ranking integrado")

    with abas[10]:
        st.markdown("### Metodologia de leitura")
        st.markdown(
            """
            O painel foi estruturado em três níveis de decisão:

            **Estratégico:** visão estadual para Governador, Secretário e alta gestão. Mostra volume geral, mapa de prioridade, concentração regional e municípios que exigem decisão.

            **Tático:** visão por Região de Saúde, útil para Superintendências, ERS e coordenadorias. Ajuda a organizar pactuação, apoio técnico e validação de dados.

            **Operacional:** visão municipal/territorial, voltada ao técnico que precisa estudar um município, suas unidades, equipes e territórios de maior pressão.

            **Score de prioridade integrada:** composição gerencial entre acesso territorial à UBS, pressão assistencial, vulnerabilidade social, fragilidade da capacidade instalada e equidade territorial. A distância dos bairros/localidades/setores e assentamentos até UBS/APS passa a ser componente explícito. Não é indicador normativo oficial; é ferramenta de triagem e inteligência para orientar investigação técnica.

            **Classificação relativa:** a prioridade crítica e a alta prioridade são organizadas pelo ranking estadual. Isso evita que cortes absolutos escondam municípios que, mesmo sem alcançar um patamar fixo, estão no topo relativo do risco em Mato Grosso.
            """
        )
        st.markdown("#### Códigos de equipes APS considerados")
        render_dataframe(pd.DataFrame([{"codigo": k, "descricao": v} for k, v in CODIGOS_EQUIPES_APS.items()]), use_container_width=True, hide_index=True)

        cobertura = pd.DataFrame([
            {"camada": "Base municipal consolidada", "uso": "população, região, UBS, equipes e profissionais", "situação": "principal"},
            {"camada": "CNES/INE", "uso": "códigos 70, 71, 72, 73, 74 e 76", "situação": "capacidade APS"},
            {"camada": "Distância territorial até UBS", "uso": "bairros, localidades, setores e assentamentos rurais até UBS/APS mais próxima", "situação": "acesso territorial"},
            {"camada": "Territórios/setores", "uso": "pressão APS, renda, saneamento e baixa escolaridade territorial", "situação": "apoio intramunicipal"},
            {"camada": "Determinantes sociais", "uso": "analfabetismo, instrução, renda e saneamento", "situação": "recuperação pela base disponível"},
            {"camada": "Terras indígenas/assentamentos/áreas contaminadas", "uso": "equidade territorial e atenção diferenciada", "situação": "camadas especiais"},
        ])
        st.markdown("#### Fontes usadas pelo painel")
        render_dataframe(cobertura, use_container_width=True, hide_index=True)
