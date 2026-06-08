from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from components.ui_elements import render_html_table
from services.gestao_risco_service import (
    carregar_gestao_risco_aps,
    componentes_risco_municipio,
    obter_leitura_risco_municipio,
    resumo_gestao_risco,
    resumo_regional_risco,
)
from services.inteligencia_avancada_aps_service import risco_eixos_explicados, glossario_decisorio_aps


def _fmt_int(v) -> str:
    try:
        return f"{int(float(v)):,}".replace(",", ".")
    except Exception:
        return "0"


def _fmt_float(v, casas: int = 1) -> str:
    try:
        return f"{float(v):,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "-"


def _tone(score: float) -> str:
    try:
        score = float(score)
    except Exception:
        return "info"
    if score >= 80:
        return "critico"
    if score >= 65:
        return "alto"
    if score >= 45:
        return "moderado"
    if score >= 25:
        return "baixo"
    return "residual"


def _colors(tone: str):
    return {
        "critico": ("#FFF1F1", "#A61B1B", "#D92D20"),
        "alto": ("#FFF7E6", "#7A4E00", "#F59E0B"),
        "moderado": ("#EEF4FF", "#1D4ED8", "#3B82F6"),
        "baixo": ("#ECFDF3", "#05603A", "#12B76A"),
        "residual": ("#F8FAFC", "#344054", "#98A2B3"),
        "info": ("#F8FAFC", "#344054", "#98A2B3"),
    }.get(tone, ("#F8FAFC", "#344054", "#98A2B3"))


def _card(title: str, value: str, text: str, badge: str = "", tone: str = "info"):
    bg, fg, bd = _colors(tone)
    st.markdown(
        f"""
        <div style="background:{bg};border:1px solid {bd};border-radius:18px;padding:1rem;min-height:155px;box-shadow:0 8px 24px rgba(16,24,40,.06);">
            <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;">
                <div style="font-size:.9rem;font-weight:900;color:{fg};line-height:1.25;">{title}</div>
                <div style="font-size:.68rem;font-weight:900;color:{fg};background:white;border:1px solid {bd};padding:.18rem .45rem;border-radius:999px;white-space:nowrap;">{badge}</div>
            </div>
            <div style="font-size:1.75rem;font-weight:950;color:{fg};margin:.45rem 0 .25rem 0;">{value}</div>
            <div style="font-size:.84rem;color:{fg};line-height:1.42;">{text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _hero():
    st.markdown(
        """
        <div style="background:linear-gradient(135deg,#111827 0%,#0B3C7D 48%,#14B8A6 100%);color:white;border-radius:24px;padding:1.35rem 1.45rem;margin-bottom:1rem;box-shadow:0 12px 35px rgba(15,23,42,.20);">
            <div style="font-size:.85rem;font-weight:900;letter-spacing:.05em;text-transform:uppercase;opacity:.9;">Módulo estratégico</div>
            <div style="font-size:2rem;font-weight:950;line-height:1.1;margin:.2rem 0 .35rem 0;">Gestão de Risco APS</div>
            <div style="font-size:.98rem;line-height:1.55;max-width:1100px;opacity:.98;">
                Este módulo cruza vulnerabilidade social, capacidade APS, acesso territorial, materno-infantil, mortalidade, vigilância, educação/intersetorialidade e equidade territorial para expor riscos, explicar os fatores predominantes e sugerir caminhos de mitigação. A leitura é orientativa e deve ser validada com as áreas técnicas, ERS e municípios.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _gauge(score: float, title: str):
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=float(score or 0),
        title={"text": f"<b>{title}</b>", "font": {"size": 15}},
        number={"font": {"size": 28}},
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": "#0EA5E9"},
            "steps": [
                {"range": [0, 25], "color": "#F1F5F9"},
                {"range": [25, 45], "color": "#D1FADF"},
                {"range": [45, 65], "color": "#FEF3C7"},
                {"range": [65, 80], "color": "#FED7AA"},
                {"range": [80, 100], "color": "#FECACA"},
            ],
        },
    ))
    fig.update_layout(height=245, margin=dict(l=15, r=15, t=45, b=5))
    return fig


def _download_csv(df: pd.DataFrame, nome: str, label: str):
    if isinstance(df, pd.DataFrame) and not df.empty:
        st.download_button(label, data=df.to_csv(index=False).encode("utf-8-sig"), file_name=nome, mime="text/csv", use_container_width=True)


def _metodologia_pesos():
    with st.expander("Metodologia de riscos e pesos", expanded=False):
        st.markdown(
            """
            **Score Integrado de Risco APS — 0 a 100**

            | Eixo | Peso | Leitura |
            |---|---:|---|
            | Vulnerabilidade social | 22% | CadÚnico, Bolsa Família, BPC e pobreza/extrema pobreza |
            | Fragilidade da capacidade APS | 18% | CNES, equipes, UBS, profissionais, população por equipe/UBS |
            | Acesso territorial | 16% | georreferenciamento, distâncias, vazios e assentamentos |
            | Materno-infantil | 12% | nascidos vivos, baixo peso, prematuridade, mães adolescentes, óbitos infantis |
            | Mortalidade | 11% | mortalidade geral, causas externas, cardiovasculares, respiratórias e maternas |
            | Vigilância e agravos | 10% | SINAN: tuberculose, hanseníase, violência e animais peçonhentos |
            | Intersetorial/educação | 7% | analfabetismo, baixa instrução, saneamento e infraestrutura escolar |
            | Equidade territorial | 4% | indígenas, quilombolas, ruralidade, assentamentos e situação de rua |

            **Importante:** o módulo organiza evidências para priorização e mitigação. Não substitui parecer técnico, estudo epidemiológico, validação geográfica, pactuação regional ou decisão da gestão.
            """
        )



def _render_risco_explicado_municipio(municipio: str, contexto: str = "principal"):
    st.markdown("### Risco explicado por eixo")
    st.caption("Cada eixo é interpretado com risco atual, tendência futura e mitigação sugerida. A ideia é que o técnico entenda o motivo do alerta e transforme o achado em plano de ação.")
    n1, n2, n3 = st.columns(3)
    with n1:
        _card("ℹ️ O que significa o score", "0–100", "Quanto maior o score, maior a exposição do município naquele eixo. A régua transforma o número em Bom, Regular, Ruim ou Péssimo.", "nota", "info")
    with n2:
        _card("Como usar", "Decisão", "Use para definir o tipo de resposta: monitorar, prevenir, reorganizar equipe, estudar UBS/unidade móvel ou criar programa específico.", "uso", "moderado")
    with n3:
        _card("Cuidado metodológico", "Validação", "O score orienta prioridade, mas não substitui validação com área técnica, ERS, município, território, CNES e produção real.", "valide", "alto")
    res = risco_eixos_explicados(municipio)
    if not res.get("ok"):
        st.info(res.get("mensagem", "Não foi possível explicar os riscos do município."))
        return
    eixos = res.get("eixos", pd.DataFrame())
    if eixos.empty:
        st.info("Sem eixos explicados para este município.")
        return
    fig = px.bar(eixos.sort_values("Score"), x="Score", y="Eixo", orientation="h", color="Classificação", text="Score", title="Eixos que mais pressionam o risco do município")
    fig.update_traces(texttemplate="%{text:.1f}", textposition="outside")
    fig.update_layout(height=420, xaxis_title="Score 0-100", yaxis_title="")
    st.plotly_chart(fig, use_container_width=True, key=f"risco_eixos_explicados_{contexto}_{municipio}")

    for _, eixo in eixos.iterrows():
        score = float(eixo.get("Score", 0) or 0)
        texto = (
            f"**Classificação:** {eixo.get('Classificação')}\n\n"
            f"**Risco atual:** {eixo.get('Risco atual')}\n\n"
            f"**Risco futuro/tendência:** {eixo.get('Risco futuro/tendência')}\n\n"
            f"**Decisão sugerida:** {eixo.get('Decisão sugerida')}\n\n"
            f"**Como mitigar:** {eixo.get('Como mitigar')}"
        )
        titulo = f"{eixo.get('Eixo')} — Score {score:.1f}"
        if score >= 85:
            st.error(f"**{titulo}**\n\n{texto}")
        elif score >= 65:
            st.warning(f"**{titulo}**\n\n{texto}")
        else:
            st.info(f"**{titulo}**\n\n{texto}")
    with st.expander("ℹ️ Glossário: como transformar risco em decisão", expanded=False):
        render_html_table(glossario_decisorio_aps(), titulo="Régua de leitura dos riscos", max_rows=20, max_text=260)
    render_html_table(eixos, titulo="Matriz explicativa dos riscos", max_rows=20, max_text=300)

def render():
    _hero()
    resultado = carregar_gestao_risco_aps()
    if not resultado.get("ok"):
        st.warning(resultado.get("mensagem", "Não foi possível montar a gestão de risco."))
        return
    df = resultado.get("base", pd.DataFrame())
    if df.empty:
        st.warning("A base de risco foi calculada, mas retornou vazia.")
        return
    resumo = resumo_gestao_risco(df)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Municípios avaliados", _fmt_int(resumo.get("municipios")))
    c2.metric("Risco crítico", _fmt_int(resumo.get("criticos")))
    c3.metric("Risco alto", _fmt_int(resumo.get("altos")))
    c4.metric("Risco moderado", _fmt_int(resumo.get("moderados")))
    c5.metric("Score médio", _fmt_float(resumo.get("score_medio"), 1))

    _metodologia_pesos()

    regioes = sorted(df.get("regiao_saude", pd.Series(dtype=str)).dropna().astype(str).unique().tolist()) if "regiao_saude" in df.columns else []
    classes = sorted(df.get("classificacao_risco_integrado", pd.Series(dtype=str)).dropna().astype(str).unique().tolist()) if "classificacao_risco_integrado" in df.columns else []
    f1, f2, f3 = st.columns([1.2, 1.2, 2])
    reg = f1.selectbox("Região de Saúde", ["Todas"] + regioes, key="risco_regiao")
    cla = f2.selectbox("Classificação de risco", ["Todas"] + classes, key="risco_classe")
    busca = f3.text_input("Buscar município", key="risco_busca", placeholder="Ex.: Cuiabá, Acorizal, Rondonópolis")

    base = df.copy()
    if reg != "Todas" and "regiao_saude" in base.columns:
        base = base[base["regiao_saude"].astype(str) == reg]
    if cla != "Todas" and "classificacao_risco_integrado" in base.columns:
        base = base[base["classificacao_risco_integrado"].astype(str) == cla]
    if busca and "municipio" in base.columns:
        base = base[base["municipio"].astype(str).str.contains(busca, case=False, na=False)]

    st.markdown("### Painel de exposição ao risco")
    top = base.sort_values("score_risco_integrado_aps", ascending=False).head(6)
    cols = st.columns(3)
    for idx, (_, r) in enumerate(top.iterrows()):
        with cols[idx % 3]:
            score = float(r.get("score_risco_integrado_aps", 0) or 0)
            _card(
                f"{int(r.get('ranking_risco_integrado_aps', 0))}º — {r.get('municipio', '-')}",
                _fmt_float(score, 1),
                f"{r.get('classificacao_risco_integrado', '-')} • {r.get('principal_fator_risco', '-')}",
                r.get("prioridade_mitigacao", ""),
                _tone(score),
            )

    tab1, tab2, tab3, tab_eixos, tab4, tab5 = st.tabs([
        "Mapa e matriz de risco",
        "Ranking e mitigação",
        "Município em foco",
        "⭐ Risco por eixo explicado",
        "Leitura regional",
        "Fontes e governança",
    ])

    with tab1:
        st.markdown("### Matriz de risco: social x capacidade APS")
        if not base.empty:
            fig = px.scatter(
                base,
                x="risco_social",
                y="risco_capacidade_aps",
                size="populacao_risco" if "populacao_risco" in base.columns else None,
                color="classificacao_risco_integrado",
                hover_name="municipio",
                hover_data=[c for c in ["regiao_saude", "score_risco_integrado_aps", "risco_acesso_territorial", "risco_vigilancia", "risco_materno_infantil"] if c in base.columns],
                title="Quanto mais alto e à direita, maior a combinação entre vulnerabilidade social e fragilidade da resposta APS.",
            )
            fig.update_layout(height=520, xaxis_title="Risco social", yaxis_title="Risco de capacidade APS")
            st.plotly_chart(fig, use_container_width=True)

        st.markdown("### Termômetro dos eixos estaduais")
        eixos = [
            ("Social", "risco_social"),
            ("Capacidade APS", "risco_capacidade_aps"),
            ("Acesso", "risco_acesso_territorial"),
            ("Materno-infantil", "risco_materno_infantil"),
            ("Mortalidade", "risco_mortalidade"),
            ("Vigilância", "risco_vigilancia"),
            ("Intersetorial", "risco_intersetorial"),
            ("Equidade", "risco_equidade_territorial"),
        ]
        media_eixos = pd.DataFrame([
            {"Eixo": nome, "Score médio": pd.to_numeric(base.get(col, pd.Series(dtype=float)), errors="coerce").fillna(0).mean()}
            for nome, col in eixos
        ])
        fig_e = px.bar(media_eixos.sort_values("Score médio"), x="Score médio", y="Eixo", orientation="h", text="Score médio", color="Score médio", color_continuous_scale="Reds", title="Eixos com maior exposição média no filtro")
        fig_e.update_traces(texttemplate="%{text:.1f}", textposition="outside")
        fig_e.update_layout(height=430, coloraxis_showscale=False, xaxis_title="Score 0-100", yaxis_title="")
        st.plotly_chart(fig_e, use_container_width=True)

    with tab2:
        st.markdown("### Ranking integrado de riscos e ações de mitigação")
        cols_rank = [c for c in [
            "ranking_risco_integrado_aps", "municipio", "regiao_saude", "populacao_risco", "score_risco_integrado_aps", "classificacao_risco_integrado", "prioridade_mitigacao", "principal_fator_risco", "alertas_risco", "plano_mitigacao_resumido"
        ] if c in base.columns]
        render_html_table(base[cols_rank].sort_values("ranking_risco_integrado_aps").head(100), titulo="Ranking de risco APS", subtitulo="Lista orientativa para priorização de mitigação e validação técnica.", max_rows=100, max_text=220)
        _download_csv(base[cols_rank], "ranking_gestao_risco_aps.csv", "Baixar ranking de risco APS")

        st.markdown("### Carteira de mitigação por eixo")
        st.markdown(
            """
            | Eixo de risco | Mitigações sugeridas |
            |---|---|
            | Vulnerabilidade social | busca ativa, integração APS/Assistência Social, priorização de famílias CadÚnico/PBF/BPC |
            | Capacidade APS | revisar CNES/INE, equipes, profissionais, horários, adscrição e distribuição territorial |
            | Acesso territorial | validar rotas, vazios intramunicipais, UBS de referência e barreiras rurais |
            | Materno-infantil | fortalecer pré-natal, puericultura, captação precoce e vigilância do recém-nascido |
            | Mortalidade | analisar causas predominantes e qualificar linhas de cuidado crônicas/urgências |
            | Vigilância | integrar APS e vigilância para tuberculose, hanseníase, violência e animais peçonhentos |
            | Intersetorial | articular educação, saneamento, assistência social e gestão municipal |
            | Equidade territorial | priorizar indígenas, quilombolas, assentamentos, ruralidade e populações dispersas |
            """
        )

    with tab3:
        st.markdown("### Município em foco")
        municipios = sorted(df.get("municipio", pd.Series(dtype=str)).dropna().astype(str).unique().tolist())
        municipio = st.selectbox("Selecionar município", municipios, key="risco_municipio_foco")
        leitura = obter_leitura_risco_municipio(municipio)
        if not leitura.get("ok"):
            st.warning(leitura.get("mensagem"))
        else:
            linha = leitura.get("linha", {})
            comp = leitura.get("componentes", pd.DataFrame())
            score = float(linha.get("score_risco_integrado_aps", 0) or 0)
            q1, q2, q3, q4 = st.columns(4)
            q1.metric("Ranking", _fmt_int(linha.get("ranking_risco_integrado_aps")))
            q2.metric("Score", _fmt_float(score, 1))
            q3.metric("Classificação", linha.get("classificacao_risco_integrado", "-"))
            q4.metric("População", _fmt_int(linha.get("populacao_risco")))
            st.info(f"**Principal fator:** {linha.get('principal_fator_risco', '-')}")
            st.warning(f"**Alertas:** {linha.get('alertas_risco', '-')}")
            st.success(f"**Mitigação sugerida:** {linha.get('plano_mitigacao_resumido', '-')}")

            c1, c2 = st.columns([1, 1.4])
            with c1:
                st.plotly_chart(_gauge(score, "Risco integrado"), use_container_width=True)
            with c2:
                if not comp.empty:
                    fig_radar = go.Figure()
                    fig_radar.add_trace(go.Scatterpolar(
                        r=comp["Score"].tolist() + [comp["Score"].iloc[0]],
                        theta=comp["Eixo"].tolist() + [comp["Eixo"].iloc[0]],
                        fill="toself",
                        line_color="#0B3C7D",
                        fillcolor="rgba(11,60,125,.25)",
                    ))
                    fig_radar.update_layout(height=360, polar=dict(radialaxis=dict(visible=True, range=[0, 100])), margin=dict(l=25, r=25, t=30, b=20))
                    st.plotly_chart(fig_radar, use_container_width=True)
            if not comp.empty:
                fig_comp = px.bar(comp.sort_values("Score"), x="Score", y="Eixo", orientation="h", text="Score", color="Score", color_continuous_scale="Reds", title="Eixos de risco do município")
                fig_comp.update_traces(texttemplate="%{text:.1f}", textposition="outside")
                fig_comp.update_layout(height=390, coloraxis_showscale=False, xaxis_title="Score 0-100", yaxis_title="")
                st.plotly_chart(fig_comp, use_container_width=True)
                render_html_table(comp, titulo="Componentes do risco municipal", max_rows=20)

            _render_risco_explicado_municipio(municipio, contexto="municipio_foco")

    with tab_eixos:
        st.success("Patch V2 aplicado: esta aba explica cada eixo de risco, o risco atual, o risco futuro e a mitigação sugerida.")
        municipios_eixo = sorted(df.get("municipio", pd.Series(dtype=str)).dropna().astype(str).unique().tolist()) if "municipio" in df.columns else []
        if municipios_eixo:
            municipio_eixo = st.selectbox("Selecionar município para explicar os eixos de risco", municipios_eixo, key="risco_eixos_municipio_v2")
            _render_risco_explicado_municipio(municipio_eixo, contexto="aba_eixos")
        else:
            st.info("Base municipal de risco indisponível para explicar os eixos.")

    with tab4:
        st.markdown("### Leitura regional para pactuação")
        regional = resumo_regional_risco(df)
        if not regional.empty:
            fig_reg = px.bar(regional.head(20), x="score_medio", y="regiao_saude", orientation="h", text="score_medio", color="score_medio", color_continuous_scale="Reds", title="Regiões com maior score médio de risco")
            fig_reg.update_traces(texttemplate="%{text:.1f}", textposition="outside")
            fig_reg.update_layout(height=460, coloraxis_showscale=False, xaxis_title="Score médio", yaxis_title="")
            st.plotly_chart(fig_reg, use_container_width=True)
            render_html_table(regional, titulo="Resumo regional de riscos", subtitulo="Apoia pactuação com ERS e priorização regional.", max_rows=50)
            _download_csv(regional, "resumo_regional_gestao_risco_aps.csv", "Baixar resumo regional")
        else:
            st.info("Não foi possível montar resumo regional.")

    with tab5:
        st.markdown("### Fontes cruzadas e governança do risco")
        fontes = resultado.get("fontes", {})
        fontes_df = pd.DataFrame([{"Eixo/Fonte": k, "Tabela usada": v} for k, v in fontes.items()])
        render_html_table(fontes_df, titulo="Fontes detectadas pelo módulo", subtitulo="O serviço tenta usar as tabelas disponíveis no banco e ignora fontes ausentes sem quebrar a tela.", max_rows=50)
        st.markdown(
            """
            #### Como usar institucionalmente
            1. Use o ranking para **priorizar análise**, não para decretar decisão automática.
            2. Valide os municípios de risco crítico/alto com as áreas técnicas responsáveis.
            3. Separe o plano por eixo: social, APS, acesso, vigilância, materno-infantil, mortalidade, educação/intersetorial e equidade.
            4. Registre decisão, responsável, prazo e evidência de mitigação.
            5. Recalcule após novas cargas aprovadas pelas chefias.
            """
        )


# -----------------------------------------------------------------------------
# PATCH V9 — Gestão de Risco organizada em Estratégico/Tático/Operacional/Relatório
# -----------------------------------------------------------------------------

def _risco_info_v9(titulo: str, texto: str):
    st.markdown(
        f"""
        <div style="background:#F8FAFC;border:1px solid #D0D5DD;border-radius:16px;padding:1rem;margin:.4rem 0 1rem 0;">
            <div style="font-size:1rem;font-weight:900;color:#101828;margin-bottom:.35rem;">ℹ️ {titulo}</div>
            <div style="font-size:.9rem;color:#344054;line-height:1.5;">{texto}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _classificar_risco_v9(score) -> str:
    try:
        s = float(score or 0)
    except Exception:
        s = 0
    if s >= 80:
        return 'Péssimo — risco crítico e necessidade urgente de plano de mitigação'
    if s >= 65:
        return 'Ruim — risco alto e necessidade de intervenção técnica'
    if s >= 45:
        return 'Regular — atenção preventiva e validação dos eixos frágeis'
    return 'Bom — sem alerta forte pela régua atual'


def render():
    st.subheader('Gestão de Risco APS')
    st.markdown(
        """
        <div class="info-box">
        Módulo organizado por nível de decisão: risco estratégico, explicação tática por eixo, mitigação operacional e relatório de risco. A leitura transforma scores em classificação clara: bom, regular, ruim e péssimo.
        </div>
        """,
        unsafe_allow_html=True,
    )

    resultado = carregar_gestao_risco_aps()
    df = resultado.get('base', pd.DataFrame()) if isinstance(resultado, dict) else pd.DataFrame()
    if df is None or df.empty:
        st.warning('Base de gestão de risco APS indisponível. Gere/atualize as bases consolidadas antes de usar este módulo.')
        return

    base = df.copy()
    regioes = sorted(base.get('regiao_saude', pd.Series(dtype=str)).dropna().astype(str).unique().tolist()) if 'regiao_saude' in base.columns else []
    col1, col2 = st.columns([1.5, 1])
    regiao = col1.selectbox('Região de Saúde', ['Todas'] + regioes, key='risco_v9_regiao')
    top_n = col2.slider('Municípios no ranking', 10, 100, 30, 10, key='risco_v9_top')
    if regiao != 'Todas' and 'regiao_saude' in base.columns:
        base = base[base['regiao_saude'] == regiao]

    resumo = resumo_gestao_risco(base)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Municípios avaliados', _fmt_int(resumo.get('municipios', len(base))), help='Municípios presentes na base de risco após filtros.')
    c2.metric('Risco médio', _fmt_float(resumo.get('score_medio', 0), 1), help='Média do score integrado de risco APS no filtro.')
    c3.metric('Risco alto/crítico', _fmt_int(resumo.get('alto_critico', 0)), help='Municípios classificados nas faixas mais graves.')
    c4.metric('Maior risco', _fmt_float(resumo.get('score_maximo', 0), 1), help='Maior score de risco encontrado no filtro.')

    abas = st.tabs(['📌 Estratégico', '🧩 Tático', '🛠️ Operacional', '📄 Relatório'])

    with abas[0]:
        st.markdown('### Painel estratégico de risco')
        _risco_info_v9('Como interpretar?', 'O risco integrado aponta onde a APS pode sofrer maior pressão atual ou futura. A classificação ajuda a priorizar mitigação, mas não substitui validação da área técnica.')
        if not base.empty:
            fig = px.scatter(
                base,
                x='risco_social',
                y='risco_capacidade_aps',
                size='populacao_risco' if 'populacao_risco' in base.columns else None,
                color='classificacao_risco_integrado' if 'classificacao_risco_integrado' in base.columns else None,
                hover_name='municipio',
                hover_data=[c for c in ['regiao_saude', 'score_risco_integrado_aps', 'risco_acesso_territorial', 'risco_vigilancia', 'risco_materno_infantil'] if c in base.columns],
                title='Matriz estratégica: vulnerabilidade social x capacidade APS',
            )
            fig.update_layout(height=500, xaxis_title='Risco social', yaxis_title='Risco de capacidade APS')
            st.plotly_chart(fig, use_container_width=True, key='risco_v9_matriz')
            cols_rank = [c for c in ['ranking_risco_integrado_aps', 'municipio', 'regiao_saude', 'populacao_risco', 'score_risco_integrado_aps', 'classificacao_risco_integrado', 'principal_fator_risco', 'prioridade_mitigacao'] if c in base.columns]
            render_html_table(base[cols_rank].sort_values('ranking_risco_integrado_aps').head(top_n), titulo='Ranking estratégico de risco APS', subtitulo='Prioriza municípios para avaliação técnica e mitigação.', max_rows=top_n, max_text=200)

    with abas[1]:
        st.markdown('### Explicação tática por eixo')
        _risco_info_v9('O que a camada tática responde?', 'Mostra qual eixo está puxando o risco: social, capacidade APS, acesso, materno-infantil, mortalidade, vigilância, intersetorialidade ou equidade territorial.')
        municipios = sorted(df.get('municipio', pd.Series(dtype=str)).dropna().astype(str).unique().tolist()) if 'municipio' in df.columns else []
        if municipios:
            municipio = st.selectbox('Selecionar município', municipios, key='risco_v9_municipio_tatico')
            leitura = obter_leitura_risco_municipio(municipio)
            if leitura.get('ok'):
                linha = leitura.get('linha', {})
                comp = leitura.get('componentes', pd.DataFrame())
                score = float(linha.get('score_risco_integrado_aps', 0) or 0)
                a, b, c = st.columns(3)
                a.metric('Score integrado', _fmt_float(score, 1), help='Score 0-100. Quanto maior, maior o risco integrado APS.')
                b.metric('Classificação simples', _classificar_risco_v9(score).split('—')[0].strip(), help='Tradução do score em linguagem clara.')
                c.metric('Principal fator', str(linha.get('principal_fator_risco', '-')), help='Eixo que mais contribui para o risco atual.')
                if not comp.empty:
                    fig_comp = px.bar(comp.sort_values('Score'), x='Score', y='Eixo', orientation='h', text='Score', color='Score', color_continuous_scale='Reds', title='Eixos de risco do município')
                    fig_comp.update_traces(texttemplate='%{text:.1f}', textposition='outside')
                    fig_comp.update_layout(height=420, coloraxis_showscale=False, xaxis_title='Score 0-100', yaxis_title='')
                    st.plotly_chart(fig_comp, use_container_width=True, key=f'risco_v9_eixos_{municipio}')
                _render_risco_explicado_municipio(municipio, contexto='v9_tatico')
            else:
                st.warning(leitura.get('mensagem', 'Leitura indisponível.'))

    with abas[2]:
        st.markdown('### Mitigação operacional')
        _risco_info_v9('Como usar operacionalmente?', 'Transforme cada risco em ação, responsável, prazo, evidência e indicador de acompanhamento. A mitigação deve ser monitorada mensalmente nos municípios mais críticos.')
        cols_rank = [c for c in ['ranking_risco_integrado_aps', 'municipio', 'regiao_saude', 'score_risco_integrado_aps', 'classificacao_risco_integrado', 'principal_fator_risco', 'alertas_risco', 'plano_mitigacao_resumido'] if c in base.columns]
        if cols_rank:
            render_html_table(base[cols_rank].sort_values('ranking_risco_integrado_aps').head(top_n), titulo='Carteira operacional de mitigação', subtitulo='Lista de municípios, risco, alerta e plano resumido.', max_rows=top_n, max_text=260)
            _download_csv(base[cols_rank], 'carteira_operacional_mitigacao_aps.csv', 'Baixar carteira de mitigação')
        st.markdown('#### Matriz padrão de mitigação')
        matriz = pd.DataFrame([
            {'Eixo': 'Vulnerabilidade social', 'Ação': 'Busca ativa APS + CRAS; priorizar CadÚnico/PBF/BPC.', 'Evidência': 'Lista nominal/territorial acompanhada e registro de visitas.'},
            {'Eixo': 'Capacidade APS', 'Ação': 'Revisar CNES/INE, equipes, carga horária, microáreas e população adscrita.', 'Evidência': 'Relatório de correção cadastral e plano de reorganização.'},
            {'Eixo': 'Acesso territorial', 'Ação': 'Validar rotas, localidades distantes, UBS de referência e necessidade de UBS satélite/unidade móvel.', 'Evidência': 'Mapa validado e lista de áreas prioritárias.'},
            {'Eixo': 'Materno-infantil', 'Ação': 'Fortalecer pré-natal, puericultura, captação precoce e vigilância do recém-nascido.', 'Evidência': 'Plano de busca ativa e indicadores de acompanhamento.'},
            {'Eixo': 'Vigilância', 'Ação': 'Integrar APS e Vigilância para agravos prioritários.', 'Evidência': 'Agenda integrada e monitoramento mensal por agravo.'},
            {'Eixo': 'Ruralidade/equidade', 'Ação': 'Criar programa APS rural/itinerante e ações para assentamentos/povos tradicionais.', 'Evidência': 'Calendário de atendimento territorial e cobertura das localidades.'},
        ])
        render_html_table(matriz, titulo='Plano de mitigação por eixo', max_rows=20, max_text=260)

    with abas[3]:
        st.markdown('### Relatório de risco')
        _risco_info_v9('Síntese para decisão', 'O relatório deve registrar risco atual, risco futuro, eixo crítico, impacto esperado, mitigação sugerida e responsável pela resposta.')
        st.text_area('Texto-base do relatório de risco', 'A gestão de risco APS deve ser utilizada para priorizar municípios e eixos com maior probabilidade de pressão assistencial, fragilidade de resposta e agravamento futuro. Municípios classificados como ruim ou péssimo devem ter plano de mitigação com responsável, prazo, evidência mínima e reavaliação mensal.', height=220, key='risco_v9_relatorio')
        regional = resumo_regional_risco(df)
        if isinstance(regional, pd.DataFrame) and not regional.empty:
            render_html_table(regional, titulo='Resumo regional de riscos', subtitulo='Apoia pactuação com ERS e priorização regional.', max_rows=50, max_text=180)
