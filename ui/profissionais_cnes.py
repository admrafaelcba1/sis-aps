import pandas as pd
import plotly.express as px
import streamlit as st
from components.ui_elements import render_html_table

from config.parametros import TIPOS_EQUIPE_CNES
from services.auditoria_cnes_service import (
    auditoria_inconsistencias,
    base_detalhada,
    resumo_estadual,
    resumo_por_municipio,
    resumo_por_tipo,
)


def _download_csv(df: pd.DataFrame, nome: str, label: str):
    if df.empty:
        return
    st.download_button(
        label=label,
        data=df.to_csv(index=False, sep=";").encode("utf-8-sig"),
        file_name=nome,
        mime="text/csv",
        use_container_width=True,
    )


def _filtrar(df: pd.DataFrame, busca: str, regiao: str) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if busca and "municipio" in out.columns:
        out = out[out["municipio"].astype(str).str.contains(busca, case=False, na=False)]
    if regiao and regiao != "Todas" and "regiao_saude" in out.columns:
        out = out[out["regiao_saude"].astype(str).eq(regiao)]
    return out


def _render_filtros(df: pd.DataFrame):
    col1, col2 = st.columns([2, 1])
    with col1:
        busca = st.text_input("Buscar município", placeholder="Ex.: Cuiabá, Sinop, Cáceres")
    with col2:
        regioes = ["Todas"]
        if not df.empty and "regiao_saude" in df.columns:
            regioes += sorted([r for r in df["regiao_saude"].dropna().astype(str).unique() if r and r != "None"])
        regiao = st.selectbox("Região de Saúde", regioes)
    return busca, regiao


def render():
    st.subheader("Profissionais CNES/INE")
    st.markdown(
        """
        <div class="info-box">
        Painel de auditoria das equipes APS/INE e dos profissionais vinculados às equipes. A contagem de profissionais representa <b>vínculos profissional-equipe</b>, podendo haver diferença em relação à contagem de CPF/profissional único quando uma pessoa atua em mais de uma equipe.
        </div>
        """,
        unsafe_allow_html=True,
    )

    resumo = resumo_estadual()
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Municípios", f"{resumo['municipios']:,}".replace(",", "."))
    col2.metric("Equipes APS/INE", f"{resumo['total_equipes']:,}".replace(",", "."))
    col3.metric("Vínculos profissionais", f"{resumo['total_profissionais']:,}".replace(",", "."))
    col4.metric("Municípios sem equipes", f"{resumo['municipios_sem_equipes']:,}".replace(",", "."))

    abas = st.tabs([
        "Resumo estadual",
        "Por município",
        "Por tipo de equipe",
        "Auditoria de inconsistências",
        "Base detalhada",
        "Critérios de contagem",
    ])

    with abas[0]:
        st.markdown("### Distribuição estadual por código CNES/INE")
        por_codigo = pd.DataFrame([
            {
                "codigo": codigo,
                "tipo_equipe": TIPOS_EQUIPE_CNES.get(codigo, ""),
                "equipes": resumo["equipes_por_codigo"].get(codigo, 0),
                "vinculos_profissionais": resumo["profissionais_por_codigo"].get(codigo, 0),
            }
            for codigo in TIPOS_EQUIPE_CNES.keys()
        ])
        col_graf1, col_graf2 = st.columns(2)
        with col_graf1:
            fig_eq = px.bar(por_codigo, x="codigo", y="equipes", text="equipes", title="Equipes por código")
            st.plotly_chart(fig_eq, use_container_width=True)
        with col_graf2:
            fig_prof = px.bar(por_codigo, x="codigo", y="vinculos_profissionais", text="vinculos_profissionais", title="Vínculos profissionais por código")
            st.plotly_chart(fig_prof, use_container_width=True)
        render_html_table(por_codigo, titulo='Profissionais por código CNES/INE', subtitulo='Resumo estadual dos vínculos por código de equipe.')
        _download_csv(por_codigo, "auditoria_cnes_resumo_estadual.csv", "Baixar resumo estadual em CSV")

    with abas[1]:
        municipal = resumo_por_municipio()
        if municipal.empty:
            st.warning("A base municipal consolidada ainda não está disponível.")
        else:
            busca, regiao = _render_filtros(municipal)
            filtrada = _filtrar(municipal, busca, regiao)
            st.markdown(f"Exibindo {len(filtrada)} município(s).")
            top = filtrada.sort_values("vinculos_profissionais", ascending=False).head(20)
            if not top.empty:
                fig = px.bar(top, x="municipio", y="vinculos_profissionais", color="regiao_saude", title="Top 20 municípios por vínculos profissionais")
                fig.update_layout(xaxis_tickangle=-45)
                st.plotly_chart(fig, use_container_width=True)
            render_html_table(filtrada, titulo='Profissionais por município', subtitulo='Vínculos profissionais filtrados por município.')
            _download_csv(filtrada, "auditoria_cnes_por_municipio.csv", "Baixar tabela municipal em CSV")

    with abas[2]:
        tipo = resumo_por_tipo()
        st.markdown("### Leitura por tipo de equipe")
        render_html_table(tipo, titulo='Profissionais por tipo de equipe', subtitulo='Leitura agregada por modalidade/código CNES.')
        if not tipo.empty:
            fig = px.bar(tipo, x="codigo", y="equipes", color="tipo_equipe", title="Equipes por tipo")
            st.plotly_chart(fig, use_container_width=True)
        _download_csv(tipo, "auditoria_cnes_por_tipo_equipe.csv", "Baixar por tipo em CSV")

    with abas[3]:
        alertas, eq_sem_prof, prof_sem_eq = auditoria_inconsistencias()
        st.markdown("### Alertas municipais")
        if alertas.empty:
            st.success("Nenhum alerta municipal crítico identificado com os critérios atuais.")
        else:
            render_html_table(alertas, titulo='Auditoria de inconsistências', subtitulo='Registros que exigem validação técnica antes de uso decisório.')
            _download_csv(alertas, "auditoria_cnes_alertas_municipais.csv", "Baixar alertas municipais em CSV")

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("### Equipes sem profissional vinculado")
            st.caption("Equipes com CNES/INE sem correspondência na base ProfissionaisEquipesBrasil.")
            render_html_table(eq_sem_prof.head(500), titulo='Equipes sem profissionais vinculados', max_rows=500)
            _download_csv(eq_sem_prof, "auditoria_equipes_sem_profissionais.csv", "Baixar equipes sem profissionais")
        with col_b:
            st.markdown("### Profissionais sem equipe correspondente")
            st.caption("Vínculos profissionais cujo CNES/INE não encontrou equipe correspondente na base de equipes.")
            render_html_table(prof_sem_eq.head(500), titulo='Profissionais sem equipe identificada', max_rows=500)
            _download_csv(prof_sem_eq, "auditoria_profissionais_sem_equipes.csv", "Baixar profissionais sem equipe")

    with abas[4]:
        escolha = st.radio("Escolha a base detalhada", ["profissionais", "equipes"], horizontal=True)
        detalhe = base_detalhada(escolha)
        if detalhe.empty:
            st.warning("Base detalhada ainda não carregada.")
        else:
            col1, col2, col3 = st.columns([2, 1, 1])
            with col1:
                busca = st.text_input("Buscar município na base detalhada", key="busca_detalhada")
            with col2:
                codigos = ["Todos"] + list(TIPOS_EQUIPE_CNES.keys())
                codigo = st.selectbox("Código", codigos)
            with col3:
                limite = st.number_input("Limite de linhas", min_value=100, max_value=10000, value=1000, step=100)
            filtrada = detalhe.copy()
            if busca and "municipio" in filtrada.columns:
                filtrada = filtrada[filtrada["municipio"].astype(str).str.contains(busca, case=False, na=False)]
            if codigo != "Todos" and "codigo_tipo_equipe" in filtrada.columns:
                filtrada = filtrada[filtrada["codigo_tipo_equipe"].astype(str).eq(codigo)]
            render_html_table(filtrada.head(int(limite)), titulo='Base detalhada de profissionais CNES/INE', max_rows=int(limite))
            _download_csv(filtrada, f"auditoria_cnes_base_{escolha}.csv", "Baixar base filtrada em CSV")

    with abas[5]:
        st.markdown("### Critérios utilizados nesta tela")
        st.markdown(
            """
            - **Fonte principal:** arquivo oficial `EQUIPESBRASIL`, especialmente `EQUIPESValidasBrasil.txt` e `ProfissionaisEquipesBrasil.txt`.
            - **Equipes APS/INE consideradas:** códigos 70, 71, 72, 73, 74 e 76.
            - **Código 74:** tratado como eAPP/prisional.
            - **Código 76:** tratado como eAP.
            - **Profissionais:** a contagem principal é de vínculos profissional-equipe. Um mesmo profissional pode aparecer mais de uma vez se estiver vinculado a mais de uma equipe.
            - **Profissional único estimado:** quando houver nome disponível, a tela tenta estimar por município, mas a contagem oficial mais segura para auditoria inicial é a de vínculos.
            - **Alertas:** servem para orientar conferência técnica. Não significam, isoladamente, irregularidade.
            """
        )
        st.markdown("### Dicionário de códigos")
        render_html_table(
            pd.DataFrame([{"codigo": k, "descricao": v} for k, v in TIPOS_EQUIPE_CNES.items()]),
            titulo="Dicionário de códigos CNES/INE",
            max_rows=50,
        )
