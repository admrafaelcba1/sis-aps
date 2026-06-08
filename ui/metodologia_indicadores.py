from __future__ import annotations
import pandas as pd
import plotly.express as px
import streamlit as st
from components.ui_elements import render_html_table
from services.dashboard_aps_service import carregar_base_dashboard
from services.indicadores_governanca_service import catalogo_fontes, catalogo_indicadores, classes_prioridade, diagnostico_cobertura_indicadores, matriz_fontes_por_status, pesos_score, resumo_governanca

def _metric_card(label: str, value, help_text: str=''):
    st.metric(label, value, help=help_text if help_text else None)

def _download_csv(df: pd.DataFrame, filename: str, label: str):
    if df is None or df.empty:
        return
    st.download_button(label=label, data=df.to_csv(index=False, sep=';').encode('utf-8-sig'), file_name=filename, mime='text/csv', use_container_width=True, key=f'download_{filename}')

def _render_score_formula():
    pesos = pesos_score()
    st.markdown('#### Composição atual do score integrado')
    st.markdown('\n        O score integrado é uma **régua gerencial de priorização**, criada para orientar investigação técnica,\n        pactuação regional e leitura executiva. Ele não substitui norma oficial, habilitação federal, critério\n        legal de financiamento ou validação municipal.\n        ')
    if not pesos.empty:
        fig = px.bar(pesos, x='componente', y='peso', text='peso', title='Peso relativo dos componentes no score integrado atual')
        fig.update_yaxes(title='Peso (%)', range=[0, 40])
        fig.update_xaxes(title='')
        st.plotly_chart(fig, use_container_width=True)
        render_html_table(pesos)
    st.markdown('\n        **Leitura técnica:** o vazio assistencial não deve ser entendido apenas como distância física.\n        A versão atual já trabalha com pressão populacional por equipe/UBS, vulnerabilidade social,\n        fragilidade de capacidade instalada e camadas de equidade territorial. A próxima evolução natural\n        é incorporar, com mais força, distância territorial até UBS, rotas reais, tempo de deslocamento e\n        validação municipal das áreas descobertas.\n        ')

def _render_indicador_detalhado(row: pd.Series):
    with st.expander(f"{row.get('eixo', '')} · {row.get('indicador', '')}"):
        c1, c2 = st.columns([1.1, 1])
        with c1:
            st.markdown(f"**Finalidade:** {row.get('finalidade', '-')}")
            st.markdown(f"**Fórmula / tratamento:** {row.get('formula', '-')}")
            st.markdown(f"**Interpretação:** {row.get('interpretacao', '-')}")
            st.markdown(f"**Uso recomendado:** {row.get('uso_recomendado', '-')}")
        with c2:
            st.markdown(f"**Campo no sistema:** `{row.get('campo_sistema', '-')}`")
            st.markdown(f"**Fonte:** {row.get('fonte', '-')}")
            st.markdown(f"**Confiabilidade:** {row.get('confiabilidade', '-')}")
            st.markdown(f"**Periodicidade:** {row.get('periodicidade', '-')}")
            st.markdown(f"**Direção:** {row.get('direcao', '-')}")
            st.markdown(f"**Peso/uso no score:** {row.get('peso_score', '-')}")
        st.warning(f"Limitação: {row.get('limitacoes', '-')}")

def render():
    st.subheader('Governança dos Indicadores e Metodologia')
    st.markdown('\n        Esta área documenta como o sistema transforma bases de dados em indicadores de inteligência da APS.\n        O objetivo é dar segurança técnica para apresentação institucional, deixando claro o que está consolidado,\n        o que é gerencial, o que é experimental e quais limitações precisam acompanhar cada leitura.\n        ')
    try:
        base = carregar_base_dashboard()
    except Exception:
        base = pd.DataFrame()
    resumo = resumo_governanca(base)
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        _metric_card('Indicadores catalogados', resumo.get('indicadores_catalogados', 0))
    with c2:
        _metric_card('Fontes mapeadas', resumo.get('fontes_mapeadas', 0))
    with c3:
        _metric_card('Consolidados', resumo.get('indicadores_consolidados', 0))
    with c4:
        _metric_card('Gerenciais', resumo.get('indicadores_gerenciais', 0))
    with c5:
        _metric_card('Campos localizados', resumo.get('campos_localizados', 0), 'Campos encontrados na base carregada do dashboard.')
    abas = st.tabs(['Dicionário dos indicadores', 'Score integrado', 'Fontes e confiabilidade', 'Cobertura técnica', 'Limitações e próximos passos'])
    with abas[0]:
        st.markdown('### Dicionário técnico dos indicadores')
        catalogo = catalogo_indicadores()
        if catalogo.empty:
            st.info('Catálogo de indicadores não encontrado.')
        else:
            col1, col2, col3 = st.columns([1.2, 1, 1])
            with col1:
                busca = st.text_input('Buscar indicador', placeholder='Ex.: vazio, vulnerabilidade, CNES, distância', key='busca_indicador_governanca')
            with col2:
                eixos = ['Todos'] + sorted(catalogo['eixo'].dropna().unique().tolist())
                eixo = st.selectbox('Eixo', eixos, key='eixo_indicador_governanca')
            with col3:
                confiancas = ['Todas'] + sorted(catalogo['confiabilidade'].dropna().unique().tolist())
                confianca = st.selectbox('Confiabilidade', confiancas, key='confianca_indicador_governanca')
            filtrado = catalogo.copy()
            if busca:
                texto = busca.strip().lower()
                cols_busca = ['indicador', 'eixo', 'campo_sistema', 'finalidade', 'fonte']
                mask = False
                for c in cols_busca:
                    mask = mask | filtrado[c].astype(str).str.lower().str.contains(texto, na=False)
                filtrado = filtrado[mask]
            if eixo != 'Todos':
                filtrado = filtrado[filtrado['eixo'] == eixo]
            if confianca != 'Todas':
                filtrado = filtrado[filtrado['confiabilidade'] == confianca]
            resumo_cols = ['eixo', 'indicador', 'campo_sistema', 'confiabilidade', 'direcao', 'peso_score']
            render_html_table(filtrado[resumo_cols])
            _download_csv(filtrado, 'catalogo_indicadores_aps.csv', 'Baixar catálogo completo')
            st.markdown('### Leitura detalhada')
            for _, row in filtrado.iterrows():
                _render_indicador_detalhado(row)
    with abas[1]:
        _render_score_formula()
        st.markdown('#### Classes de prioridade')
        classes = classes_prioridade()
        render_html_table(classes)
        if not base.empty and 'classe_prioridade' in base.columns:
            dist = base['classe_prioridade'].value_counts().reset_index()
            dist.columns = ['classe', 'municipios']
            fig = px.bar(dist, x='classe', y='municipios', text='municipios', title='Municípios por classe de prioridade na base atual')
            fig.update_xaxes(title='')
            st.plotly_chart(fig, use_container_width=True)
    with abas[2]:
        st.markdown('### Fontes de dados por situação de uso')
        fontes = catalogo_fontes()
        if fontes.empty:
            st.info('Catálogo de fontes não encontrado.')
        else:
            matriz = matriz_fontes_por_status()
            if not matriz.empty:
                fig = px.bar(matriz, x='status', y='quantidade', text='quantidade', title='Fontes por status técnico')
                fig.update_xaxes(title='Status')
                fig.update_yaxes(title='Quantidade')
                st.plotly_chart(fig, use_container_width=True)
            status = st.multiselect('Filtrar por status', sorted(fontes['status'].dropna().unique().tolist()), default=sorted(fontes['status'].dropna().unique().tolist()))
            filtradas = fontes[fontes['status'].isin(status)] if status else fontes
            render_html_table(filtradas)
            _download_csv(filtradas, 'catalogo_fontes_aps.csv', 'Baixar catálogo de fontes')
    with abas[3]:
        st.markdown('### Cobertura técnica dos campos')
        st.markdown('\n            Esta tabela verifica se os campos documentados no catálogo aparecem na base carregada pelo dashboard.\n            Alguns indicadores são derivados em tempo de execução e, por isso, podem aparecer como "documentado / derivado / verificar".\n            ')
        diag = diagnostico_cobertura_indicadores(base)
        render_html_table(diag)
        _download_csv(diag, 'diagnostico_cobertura_indicadores.csv', 'Baixar diagnóstico de cobertura')
    with abas[4]:
        st.markdown('### Limitações que devem acompanhar a apresentação')
        st.info('O sistema deve ser apresentado como ferramenta de inteligência, triagem e priorização técnica. Ele não deve ser vendido como verdade absoluta, nem como substituto de validação regional, municipal ou normativa oficial.')
        st.markdown('\n            **Pontos que precisam ficar claros:**\n\n            1. Distância até UBS ainda não representa rota viária real, tempo de deslocamento ou sazonalidade.\n            2. População por equipe/UBS é estimativa de pressão, não adscrição real.\n            3. CNES expressa cadastro, mas pode conter defasagem, inconsistência ou equipe incompleta.\n            4. Indicadores sociais podem ter defasagem censitária ou granularidade limitada.\n            5. Territórios intramunicipais devem ser tratados como bairros/localidades/setores, não necessariamente bairros oficiais.\n            6. Score integrado é uma régua gerencial de priorização, não um critério legal automático.\n\n            **Próxima evolução recomendada:** criar o Índice Integrado de Vazio Assistencial APS com maior peso para georreferenciamento,\n            distância territorial, territórios vulneráveis, capacidade instalada, ruralidade, povos/comunidades tradicionais e validação regional.\n            ')
