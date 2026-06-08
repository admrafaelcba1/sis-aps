import streamlit as st

from database.queries import read_table


def _safe_read_count(table_name: str) -> int:
    try:
        df = read_table(table_name)
        return len(df)
    except Exception:
        return 0


def _go_to(page_name: str):
    st.session_state["aps_nav_target"] = page_name
    try:
        st.rerun()
    except AttributeError:
        st.experimental_rerun()


def _render_card(icon: str, title: str, description: str):
    st.markdown(
        f"""
        <div class="nav-card">
            <div class="icon">{icon}</div>
            <h3>{title}</h3>
            <p>{description}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_step(number: int, title: str, description: str):
    st.markdown(
        f"""
        <div class="analysis-step">
            <div class="step-number">{number}</div>
            <h4>{title}</h4>
            <p>{description}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render():
    st.markdown("<div class='section-title'>Central de Inteligência da Atenção Primária à Saúde</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-subtitle'>Ambiente analítico para apoiar a tomada de decisão estadual sobre vazio assistencial, acesso territorial, capacidade APS, vulnerabilidade social, regionalização e priorização de ações em Mato Grosso.</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="decision-box">
            <b>Primeira fase analítica consolidada.</b><br>
            Esta versão organiza as bases disponíveis, cruza dados territoriais, CNES/INE, população, vulnerabilidade e distância até UBS/APS para apoiar a discussão inicial com a equipe APS, Secretaria Adjunta de Atenção e Vigilância em Saúde e áreas técnicas da SES/MT.
        </div>
        """,
        unsafe_allow_html=True,
    )

    municipios = _safe_read_count("municipios")
    base = _safe_read_count("base_municipal_consolidada")
    importacoes = _safe_read_count("importacoes")
    fontes = _safe_read_count("fontes_dados")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Municípios cadastrados", municipios)
    c2.metric("Municípios consolidados", base)
    c3.metric("Importações registradas", importacoes)
    c4.metric("Fontes mapeadas", fontes)

    st.markdown(
        """
        <span class="status-pill good">Vazio integrado</span>
        <span class="status-pill good">Territórios desassistidos</span>
        <span class="status-pill good">Carteira de ações</span>
        <span class="status-pill good">Perfis e alertas</span>
        <span class="status-pill info">Governança dos indicadores</span>
        <span class="status-pill warn">Expansível com novas bases na fase 2</span>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("<div class='section-title'>Fluxo recomendado de análise</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-subtitle'>Sequência sugerida para apresentação e validação técnica da primeira versão do sistema.</div>",
        unsafe_allow_html=True,
    )

    s1, s2, s3, s4 = st.columns(4)
    with s1:
        _render_step(1, "Visão Executiva", "Comece pelo ranking integrado, vazio assistencial, perfis municipais e síntese regional.")
    with s2:
        _render_step(2, "Territórios Desassistidos", "Aprofunde em bairros, localidades, setores e assentamentos com maior distância até UBS/APS.")
    with s3:
        _render_step(3, "Análise Municipal", "Valide o município com capacidade instalada, equipes, profissionais e vulnerabilidade.")
    with s4:
        _render_step(4, "Encaminhamento Técnico", "Use a carteira de ações e a matriz problema x resposta como base para discussão com APS e ERS.")

    st.markdown("<div class='section-title'>Acessos principais</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-subtitle'>Áreas centrais da primeira versão apresentável do sistema.</div>",
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        _render_card(
            "📊",
            "Painel Executivo APS",
            "Vazio integrado, territórios desassistidos, carteira de ações, perfis municipais, ranking e leitura estadual/regional.",
        )
        if st.button("Abrir Painel Executivo APS", use_container_width=True):
            _go_to("Painel Executivo APS")

    with col2:
        _render_card(
            "🗺️",
            "Inteligência Territorial",
            "Mapas de distância, vazios assistenciais, acesso rural, bairros/localidades/setores e georreferenciamento APS.",
        )
        if st.button("Abrir Georreferenciamento", use_container_width=True):
            _go_to("Georreferenciamento da Saúde")

    with col3:
        _render_card(
            "🏙️",
            "Diagnóstico Municipal",
            "Leitura técnica por município, relatório inteligente, equipes, profissionais, vulnerabilidade e contexto territorial.",
        )
        if st.button("Abrir Diagnóstico Municipal", use_container_width=True):
            _go_to("Diagnóstico Municipal")

    col4, col5, col6 = st.columns(3)
    with col4:
        _render_card(
            "👥",
            "CNES e Profissionais",
            "Profissionais vinculados, códigos de equipe, inconsistências, composição CNES/INE e base detalhada.",
        )
        if st.button("Abrir Profissionais CNES/INE", use_container_width=True):
            _go_to("Profissionais CNES/INE")

    with col5:
        _render_card(
            "📚",
            "Governança dos Indicadores",
            "Dicionário técnico com finalidade, cálculo, fonte, interpretação, confiabilidade e limitações dos indicadores.",
        )
        if st.button("Abrir Governança dos Indicadores", use_container_width=True):
            _go_to("Governança dos Indicadores")

    with col6:
        _render_card(
            "🧭",
            "Determinantes Sociais",
            "Cruzamento entre vulnerabilidade social, acesso rural, territórios especiais e capacidade da APS.",
        )
        if st.button("Abrir Determinantes Sociais", use_container_width=True):
            _go_to("Determinantes Sociais e APS")

    st.markdown("<div class='section-title'>Escopo desta primeira entrega</div>", unsafe_allow_html=True)
    left, right = st.columns([1.15, 1])
    with left:
        st.markdown(
            """
            <div class="success-box">
                <b>O que esta versão já entrega:</b><br>
                leitura integrada de vazio assistencial, distância territorial até UBS/APS, pressão população/equipe, capacidade instalada, vulnerabilidade, territórios rurais/especiais, perfis municipais, alertas de risco subestimado e carteira preliminar de ações.
            </div>
            """,
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            """
            <div class="warn-box">
                <b>O que pode ser expandido na fase 2:</b><br>
                rotas viárias reais, tempo de deslocamento, produção assistencial, indicadores epidemiológicos, DW da SES, bases internas, validação municipal/ERS e novas APIs oficiais.
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown(
        """
        <span class="pill">Dashboard preservado</span>
        <span class="pill">Georreferenciamento preservado</span>
        <span class="pill">APIs e bases preservadas</span>
        <span class="pill">Análises consolidadas</span>
        <span class="pill">Pronto para qualificação visual final</span>
        """,
        unsafe_allow_html=True,
    )
