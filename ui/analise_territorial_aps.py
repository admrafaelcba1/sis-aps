from __future__ import annotations
import pandas as pd
import plotly.express as px
import streamlit as st
from components.ui_elements import render_html_table
from services.analise_territorial_service import carregar_analise_territorial_aps, resumo_executivo_territorial, resumo_regional_territorial, detalhar_municipio_territorial, gerar_matriz_oportunidades_aps, resumo_camadas_territoriais, gerar_carteira_acoes_aps, gerar_perfis_intervencao_aps, resumo_perfis_intervencao_aps, recalcular_indice_prioridade_aps, resumo_simulacao_prioridade, gerar_relatorio_municipal_aps, gerar_painel_executivo_aps

def _fmt_int(valor) -> str:
    try:
        if pd.isna(valor):
            return '0'
        return f'{int(round(float(valor))):,}'.replace(',', '.')
    except Exception:
        return '0'

def _fmt_decimal(valor, casas: int=1) -> str:
    try:
        if pd.isna(valor):
            return '-'
        return f'{float(valor):,.{casas}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return '-'

def _download_csv(df: pd.DataFrame, nome: str, label: str='Baixar CSV'):
    if df is None or df.empty:
        return
    st.download_button(label=label, data=df.to_csv(index=False, sep=';').encode('utf-8-sig'), file_name=nome, mime='text/csv', use_container_width=True)

def _colunas_existentes(df: pd.DataFrame, colunas: list[str]) -> list[str]:
    return [c for c in colunas if c in df.columns]

def _filtrar_base(df: pd.DataFrame, prefixo: str='') -> pd.DataFrame:
    col1, col2, col3 = st.columns([1.4, 1.2, 1.1])
    with col1:
        busca = st.text_input('Buscar município', placeholder='Ex.: Cuiabá, Sinop, Rondonópolis', key=f'{prefixo}_busca')
    with col2:
        regioes = ['Todas'] + sorted([r for r in df.get('regiao_saude', pd.Series(dtype=str)).dropna().astype(str).unique() if r and r != 'None'])
        regiao = st.selectbox('Região de Saúde', regioes, key=f'{prefixo}_regiao')
    with col3:
        classes = ['Todas', 'Muito alta', 'Alta', 'Média', 'Monitoramento']
        classe = st.selectbox('Classificação de prioridade', classes, key=f'{prefixo}_classe')
    out = df.copy()
    if busca:
        out = out[out['municipio'].astype(str).str.contains(busca, case=False, na=False)]
    if regiao != 'Todas' and 'regiao_saude' in out.columns:
        out = out[out['regiao_saude'].astype(str).eq(regiao)]
    if classe != 'Todas' and 'classificacao_prioridade_aps' in out.columns:
        out = out[out['classificacao_prioridade_aps'].astype(str).eq(classe)]
    return out

def _colunas_ranking() -> list[str]:
    return ['municipio', 'regiao_saude', 'populacao', 'area_km2', 'densidade_hab_km2', 'total_ubs', 'total_equipes_aps', 'total_profissionais_aps', 'populacao_por_equipe', 'populacao_por_ubs', 'profissionais_por_equipe', 'qtd_assentamentos', 'qtd_terras_indigenas', 'qtd_ocorrencias_ambientais', 'indice_prioridade_aps', 'classificacao_prioridade_aps', 'alertas_territoriais']

def _config_dataframe():
    return {'indice_prioridade_aps': st.column_config.ProgressColumn('Índice preliminar', min_value=0, max_value=100, format='%.1f'), 'populacao': st.column_config.NumberColumn('População', format='%d'), 'area_km2': st.column_config.NumberColumn('Área km²', format='%.1f'), 'densidade_hab_km2': st.column_config.NumberColumn('Densidade', format='%.1f'), 'populacao_por_equipe': st.column_config.NumberColumn('Pop./equipe', format='%.1f'), 'populacao_por_ubs': st.column_config.NumberColumn('Pop./UBS', format='%.1f'), 'profissionais_por_equipe': st.column_config.NumberColumn('Prof./equipe', format='%.1f')}

def _render_cards_resumo(resumo: dict):
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Municípios analisados', _fmt_int(resumo.get('municipios', 0)))
    c2.metric('População total', _fmt_int(resumo.get('populacao', 0)))
    c3.metric('Equipes APS', _fmt_int(resumo.get('equipes', 0)))
    c4.metric('UBS/estabelecimentos', _fmt_int(resumo.get('ubs', 0)))
    c5, c6, c7, c8 = st.columns(4)
    c5.metric('Municípios em alta prioridade', _fmt_int(resumo.get('prioridade_alta', 0)))
    c6.metric('Assentamentos', _fmt_int(resumo.get('assentamentos', 0)))
    c7.metric('Terras indígenas/interseções', _fmt_int(resumo.get('terras_indigenas', 0)))
    c8.metric('Ocorrências ambientais', _fmt_int(resumo.get('ocorrencias', 0)))

def _render_fechamento_versao(df: pd.DataFrame, resumo: dict, matriz: pd.DataFrame, carteira: pd.DataFrame):
    """Bloco institucional de fechamento da primeira versão da Análise Territorial APS."""
    municipios = int(df['municipio'].nunique()) if 'municipio' in df.columns else len(df)
    prioridade_alta = int(resumo.get('prioridade_alta', 0) or 0)
    regioes = int(df.get('regiao_saude', pd.Series(dtype=str)).dropna().astype(str).nunique()) if 'regiao_saude' in df.columns else 0
    acoes = int(len(carteira)) if isinstance(carteira, pd.DataFrame) else 0
    with st.expander('Fechamento da Análise Territorial APS — status da versão atual', expanded=False):
        st.markdown('\n            Esta versão consolida a primeira leitura gerencial das bases disponíveis para apoiar a identificação de\n            prioridades territoriais da Atenção Primária. O módulo está apto para **uso exploratório, reuniões técnicas,\n            priorização preliminar e geração de hipóteses de intervenção**, sem substituir validações oficiais ou\n            pactuações formais da gestão.\n            ')
        c1, c2, c3, c4 = st.columns(4)
        c1.metric('Municípios na análise', _fmt_int(municipios))
        c2.metric('Regiões de Saúde', _fmt_int(regioes))
        c3.metric('Alta/muito alta prioridade', _fmt_int(prioridade_alta))
        c4.metric('Ações sugeridas', _fmt_int(acoes))
        st.markdown('#### O que esta versão já entrega')
        st.markdown('\n            - Painel executivo e ranking territorial para leitura rápida da gestão;\n            - Matriz de decisão, carteira preliminar de ações e perfis de intervenção;\n            - Análise por Região de Saúde, comparação entre municípios e simulador de pesos;\n            - Relatório municipal automático para apoiar despacho, estudo técnico ou reunião;\n            - Metodologia transparente, com indicação expressa de que o índice é preliminar.\n            ')
        st.markdown('#### Limites metodológicos assumidos')
        st.markdown('\n            - A análise depende da qualidade das bases já carregadas e saneadas;\n            - O índice não é norma oficial, não define financiamento e não substitui critério pactuado;\n            - Bases de desempenho, cobertura oficial, produção APS e ICSAP ainda não foram incorporadas;\n            - Camadas estaduais ambientais e territoriais devem ser usadas como sinal de contexto, não como diagnóstico isolado.\n            ')
        st.markdown('#### Próximo módulo recomendado')
        st.info('Avançar para o Georreferenciamento da Saúde, começando por diagnóstico de camadas geográficas e, em seguida, construção de mapa territorial premium com municípios, UBS, equipes APS, assentamentos, terras indígenas, ocorrências ambientais e prioridade territorial.')

def render():
    st.subheader('Análise Territorial APS')
    st.markdown('\n        <div class="info-box">\n        Esta tela consolida a primeira leitura gerencial das bases territoriais disponíveis para a Atenção Primária:\n        estrutura APS, população, território, assentamentos, terras indígenas e ocorrências ambientais.\n        O índice apresentado é uma <b>ferramenta preliminar de triagem e priorização técnica</b>, não uma regra normativa oficial.\n        </div>\n        ', unsafe_allow_html=True)
    df = carregar_analise_territorial_aps()
    if df.empty:
        st.warning('A base municipal consolidada ainda não foi gerada ou não há dados estruturados suficientes.')
        return
    resumo = resumo_executivo_territorial(df)
    matriz = gerar_matriz_oportunidades_aps(df)
    carteira = gerar_carteira_acoes_aps(df)
    _render_fechamento_versao(df, resumo, matriz, carteira)
    tabs = st.tabs(['Painel executivo', 'Resumo executivo', 'Ranking e gráficos', 'Matriz de decisão', 'Carteira de ações', 'Perfis de intervenção', 'Regiões de Saúde', 'Camadas territoriais', 'Análise municipal', 'Simulador', 'Metodologia', 'Comparador', 'Relatório municipal'])
    with tabs[1]:
        _render_cards_resumo(resumo)
        st.divider()
        col_a, col_b = st.columns([1.25, 1])
        with col_a:
            top = df.sort_values('indice_prioridade_aps', ascending=False).head(15)
            fig = px.bar(top, x='indice_prioridade_aps', y='municipio', orientation='h', color='classificacao_prioridade_aps', title='Top 15 municípios por prioridade territorial preliminar', labels={'indice_prioridade_aps': 'Índice preliminar', 'municipio': 'Município'})
            fig.update_layout(yaxis={'categoryorder': 'total ascending'}, height=520, legend_title_text='Classificação')
            st.plotly_chart(fig, use_container_width=True)
        with col_b:
            contagem = df['classificacao_prioridade_aps'].value_counts().reset_index()
            contagem.columns = ['classificacao', 'municipios']
            fig = px.pie(contagem, names='classificacao', values='municipios', title='Distribuição dos municípios por classificação', hole=0.45)
            st.plotly_chart(fig, use_container_width=True)
            camadas = resumo_camadas_territoriais(df)
            st.markdown('#### Cobertura das camadas especiais')
            render_html_table(camadas)
        st.markdown('#### Leitura executiva inicial')
        qtd_muito_alta = int((df['classificacao_prioridade_aps'] == 'Muito alta').sum())
        qtd_alta = int((df['classificacao_prioridade_aps'] == 'Alta').sum())
        st.info(f'A triagem preliminar aponta {qtd_muito_alta} municípios em prioridade muito alta e {qtd_alta} em prioridade alta. A leitura deve orientar conferência técnica, não substituir o julgamento das áreas responsáveis nem os critérios oficiais de financiamento.')
        if not matriz.empty:
            st.markdown('#### Primeiras oportunidades de investigação técnica')
            render_html_table(matriz.head(12))
    with tabs[2]:
        st.markdown('### Ranking e gráficos comparativos')
        filtrado = _filtrar_base(df, 'ranking')
        cols = _colunas_existentes(filtrado, _colunas_ranking())
        visao = filtrado[cols].sort_values('indice_prioridade_aps', ascending=False)
        render_html_table(visao)
        _download_csv(visao, 'ranking_prioridade_territorial_aps.csv', 'Baixar ranking filtrado')
        st.divider()
        c1, c2 = st.columns(2)
        with c1:
            top_pop_equipe = filtrado[filtrado['total_equipes_aps'] > 0].sort_values('populacao_por_equipe', ascending=False).head(15)
            if not top_pop_equipe.empty:
                fig = px.bar(top_pop_equipe, x='populacao_por_equipe', y='municipio', orientation='h', title='Maior população por equipe APS', labels={'populacao_por_equipe': 'População por equipe', 'municipio': 'Município'})
                fig.update_layout(yaxis={'categoryorder': 'total ascending'}, height=480)
                st.plotly_chart(fig, use_container_width=True)
        with c2:
            top_pop_ubs = filtrado[filtrado['total_ubs'] > 0].sort_values('populacao_por_ubs', ascending=False).head(15)
            if not top_pop_ubs.empty:
                fig = px.bar(top_pop_ubs, x='populacao_por_ubs', y='municipio', orientation='h', title='Maior população por UBS/estabelecimento APS', labels={'populacao_por_ubs': 'População por UBS', 'municipio': 'Município'})
                fig.update_layout(yaxis={'categoryorder': 'total ascending'}, height=480)
                st.plotly_chart(fig, use_container_width=True)
        c3, c4 = st.columns(2)
        with c3:
            fig = px.scatter(filtrado, x='populacao', y='total_equipes_aps', size='indice_prioridade_aps', color='classificacao_prioridade_aps', hover_name='municipio', title='População x Equipes APS', labels={'populacao': 'População', 'total_equipes_aps': 'Equipes APS'})
            st.plotly_chart(fig, use_container_width=True)
        with c4:
            fig = px.scatter(filtrado, x='area_km2', y='total_ubs', size='indice_prioridade_aps', color='classificacao_prioridade_aps', hover_name='municipio', title='Área territorial x UBS', labels={'area_km2': 'Área km²', 'total_ubs': 'UBS/estabelecimentos'})
            st.plotly_chart(fig, use_container_width=True)
    with tabs[3]:
        st.markdown('### Matriz de decisão')
        st.caption('A matriz organiza os municípios por tipo de alerta. Ela serve como fila de investigação técnica, não como decisão automática.')
        if matriz.empty:
            st.warning('Não foi possível montar a matriz de decisão.')
        else:
            eixo = st.selectbox('Filtrar por eixo', ['Todos'] + sorted(matriz['eixo'].dropna().unique().tolist()))
            mat = matriz.copy()
            if eixo != 'Todos':
                mat = mat[mat['eixo'].eq(eixo)]
            render_html_table(mat)
            _download_csv(mat, 'matriz_decisao_aps.csv', 'Baixar matriz filtrada')
            resumo_eixo = matriz.groupby('eixo', dropna=False).agg(municipios=('municipio', 'nunique'), prioridade_media=('indice_prioridade_aps', 'mean')).reset_index().sort_values('prioridade_media', ascending=False)
            resumo_eixo['prioridade_media'] = resumo_eixo['prioridade_media'].round(1)
            fig = px.bar(resumo_eixo, x='prioridade_media', y='eixo', orientation='h', title='Prioridade média por eixo de investigação', labels={'prioridade_media': 'Prioridade média', 'eixo': 'Eixo'})
            fig.update_layout(yaxis={'categoryorder': 'total ascending'}, height=420)
            st.plotly_chart(fig, use_container_width=True)
    with tabs[4]:
        st.markdown('### Carteira preliminar de ações')
        st.caption('Esta carteira traduz os alertas territoriais em oportunidades de ação. Ela não substitui decisão técnica ou pactuação institucional.')
        if carteira.empty:
            st.warning('Não foi possível montar a carteira preliminar de ações.')
        else:
            c1, c2, c3 = st.columns([1.1, 1.1, 1.2])
            with c1:
                tipos = ['Todos'] + sorted(carteira['tipo_acao'].dropna().astype(str).unique().tolist())
                tipo = st.selectbox('Tipo de ação', tipos, key='carteira_tipo')
            with c2:
                urgencias = ['Todas'] + [u for u in ['Crítica', 'Alta', 'Média', 'Monitoramento'] if u in carteira['nivel_urgencia'].astype(str).unique()]
                urgencia = st.selectbox('Urgência', urgencias, key='carteira_urgencia')
            with c3:
                busca_acao = st.text_input('Buscar município ou ação', key='carteira_busca')
            cart = carteira.copy()
            if tipo != 'Todos':
                cart = cart[cart['tipo_acao'].eq(tipo)]
            if urgencia != 'Todas':
                cart = cart[cart['nivel_urgencia'].eq(urgencia)]
            if busca_acao:
                mask = cart['municipio'].astype(str).str.contains(busca_acao, case=False, na=False) | cart['acao_sugerida'].astype(str).str.contains(busca_acao, case=False, na=False) | cart['justificativa'].astype(str).str.contains(busca_acao, case=False, na=False)
                cart = cart[mask]
            cards = st.columns(4)
            cards[0].metric('Ações mapeadas', _fmt_int(len(cart)))
            cards[1].metric('Municípios envolvidos', _fmt_int(cart['municipio'].nunique() if not cart.empty else 0))
            cards[2].metric('Ações críticas/altas', _fmt_int(cart['nivel_urgencia'].isin(['Crítica', 'Alta']).sum() if not cart.empty else 0))
            cards[3].metric('Tipos de ação', _fmt_int(cart['tipo_acao'].nunique() if not cart.empty else 0))
            render_html_table(cart)
            _download_csv(cart, 'carteira_preliminar_acoes_aps.csv', 'Baixar carteira filtrada')
            col1, col2 = st.columns(2)
            with col1:
                por_tipo = carteira.groupby('tipo_acao', dropna=False).agg(acoes=('municipio', 'count'), municipios=('municipio', 'nunique')).reset_index().sort_values('acoes', ascending=False)
                fig = px.bar(por_tipo, x='acoes', y='tipo_acao', orientation='h', title='Ações por tipo', labels={'acoes': 'Ações', 'tipo_acao': 'Tipo'})
                fig.update_layout(yaxis={'categoryorder': 'total ascending'}, height=420)
                st.plotly_chart(fig, use_container_width=True)
            with col2:
                por_regiao = carteira.groupby('regiao_saude', dropna=False).agg(acoes=('municipio', 'count'), municipios=('municipio', 'nunique')).reset_index().sort_values('acoes', ascending=False).head(15)
                fig = px.bar(por_regiao, x='acoes', y='regiao_saude', orientation='h', title='Regiões com mais ações mapeadas', labels={'acoes': 'Ações', 'regiao_saude': 'Região'})
                fig.update_layout(yaxis={'categoryorder': 'total ascending'}, height=420)
                st.plotly_chart(fig, use_container_width=True)
            st.markdown('#### Como usar')
            st.info('Use esta carteira como lista inicial de investigação: priorize ações críticas/altas, valide os dados com as áreas responsáveis e transforme apenas os casos confirmados em proposta, despacho ou projeto.')
    with tabs[5]:
        st.markdown('### Perfis de intervenção')
        st.caption('Esta leitura organiza cada município pelo tipo de intervenção que parece mais dominante: equipes, infraestrutura, territórios especiais, acesso/logística, vigilância ambiental ou monitoramento integrado.')
        perfis = gerar_perfis_intervencao_aps(df)
        resumo_perfis = resumo_perfis_intervencao_aps(df)
        if perfis.empty:
            st.info('Ainda não há perfis de intervenção calculados.')
        else:
            c1, c2 = st.columns([1.05, 1])
            with c1:
                fig = px.bar(resumo_perfis, x='municipios', y='perfil_intervencao', orientation='h', color='municipios_alta_prioridade', title='Municípios por perfil dominante de intervenção', labels={'municipios': 'Municípios', 'perfil_intervencao': 'Perfil de intervenção', 'municipios_alta_prioridade': 'Alta prioridade'})
                fig.update_layout(yaxis={'categoryorder': 'total ascending'}, height=470)
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                fig = px.scatter(resumo_perfis, x='prioridade_media', y='score_medio_perfil', size='municipios', color='perfil_intervencao', title='Prioridade média x intensidade do perfil', labels={'prioridade_media': 'Prioridade média', 'score_medio_perfil': 'Score médio do perfil', 'municipios': 'Municípios'})
                st.plotly_chart(fig, use_container_width=True)
            st.markdown('#### Resumo dos perfis')
            render_html_table(resumo_perfis)
            st.markdown('#### Municípios e encaminhamento dominante')
            col1, col2, col3 = st.columns([1.2, 1.1, 1])
            with col1:
                perfis_lista = ['Todos'] + sorted(perfis['perfil_intervencao'].dropna().astype(str).unique())
                perfil_sel = st.selectbox('Perfil', perfis_lista, key='perfil_intervencao_filtro')
            with col2:
                prioridade_lista = ['Todas', 'Muito alta', 'Alta', 'Média', 'Monitoramento']
                prioridade_sel = st.selectbox('Prioridade', prioridade_lista, key='perfil_prioridade_filtro')
            with col3:
                somente_alta = st.checkbox('Mostrar apenas alta/muito alta', value=False, key='perfil_somente_alta')
            perfis_f = perfis.copy()
            if perfil_sel != 'Todos':
                perfis_f = perfis_f[perfis_f['perfil_intervencao'].astype(str).eq(perfil_sel)]
            if prioridade_sel != 'Todas':
                perfis_f = perfis_f[perfis_f['classificacao_prioridade_aps'].astype(str).eq(prioridade_sel)]
            if somente_alta:
                perfis_f = perfis_f[perfis_f['classificacao_prioridade_aps'].isin(['Muito alta', 'Alta'])]
            cols = _colunas_existentes(perfis_f, ['municipio', 'regiao_saude', 'classificacao_prioridade_aps', 'indice_prioridade_aps', 'perfil_intervencao', 'score_perfil_intervencao', 'evidencia_perfil', 'encaminhamento_perfil', 'populacao', 'total_equipes_aps', 'total_ubs', 'populacao_por_equipe', 'populacao_por_ubs', 'qtd_assentamentos', 'qtd_terras_indigenas', 'qtd_ocorrencias_ambientais'])
            render_html_table(perfis_f[cols])
            _download_csv(perfis_f[cols], 'perfis_intervencao_aps.csv', 'Baixar perfis filtrados')
    with tabs[6]:
        st.markdown('### Regiões de Saúde')
        regional = resumo_regional_territorial(df)
        if regional.empty:
            st.warning('Não foi possível montar resumo regional.')
        else:
            c1, c2 = st.columns([1.15, 1])
            with c1:
                fig = px.bar(regional.sort_values('prioridade_media', ascending=False), x='prioridade_media', y='regiao_saude', orientation='h', title='Prioridade média por Região de Saúde', labels={'prioridade_media': 'Prioridade média', 'regiao_saude': 'Região de Saúde'})
                fig.update_layout(yaxis={'categoryorder': 'total ascending'}, height=560)
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                fig = px.scatter(regional, x='populacao', y='equipes_aps', size='municipios', color='municipios_alta_prioridade', hover_name='regiao_saude', title='População x Equipes APS por Região', labels={'populacao': 'População', 'equipes_aps': 'Equipes APS', 'municipios_alta_prioridade': 'Municípios alta prioridade'})
                st.plotly_chart(fig, use_container_width=True)
            render_html_table(regional)
            _download_csv(regional, 'resumo_regional_territorial_aps.csv', 'Baixar resumo regional')
    with tabs[7]:
        st.markdown('### Camadas territoriais estratégicas')
        camadas = resumo_camadas_territoriais(df)
        render_html_table(camadas)
        mapa = df.copy()
        if 'latitude' in mapa.columns and 'longitude' in mapa.columns:
            mapa['latitude'] = pd.to_numeric(mapa['latitude'], errors='coerce')
            mapa['longitude'] = pd.to_numeric(mapa['longitude'], errors='coerce')
            mapa = mapa.dropna(subset=['latitude', 'longitude'])
        else:
            mapa = pd.DataFrame()
        if not mapa.empty:
            fig = px.scatter_mapbox(mapa, lat='latitude', lon='longitude', size='indice_prioridade_aps', color='classificacao_prioridade_aps', hover_name='municipio', hover_data={'regiao_saude': True, 'populacao': True, 'total_equipes_aps': True, 'total_ubs': True, 'qtd_assentamentos': True, 'qtd_terras_indigenas': True, 'qtd_ocorrencias_ambientais': True, 'indice_prioridade_aps': True, 'latitude': False, 'longitude': False}, zoom=4.4, height=620, title='Mapa preliminar de prioridade territorial APS')
            fig.update_layout(mapbox_style='open-street-map', margin={'r': 0, 't': 45, 'l': 0, 'b': 0})
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning('Não há coordenadas suficientes para o mapa.')
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            ass = df[df['qtd_assentamentos'] > 0].sort_values('qtd_assentamentos', ascending=False).head(20)
            st.markdown('#### Assentamentos por município')
            cols = _colunas_existentes(ass, ['municipio', 'regiao_saude', 'qtd_assentamentos', 'indice_prioridade_aps'])
            render_html_table(ass[cols])
        with col_b:
            tis = df[df['qtd_terras_indigenas'] > 0].sort_values('qtd_terras_indigenas', ascending=False).head(20)
            st.markdown('#### Terras indígenas/interseções')
            cols = _colunas_existentes(tis, ['municipio', 'regiao_saude', 'qtd_terras_indigenas', 'nomes_terras_indigenas'])
            render_html_table(tis[cols])
        with col_c:
            amb = df[df['qtd_ocorrencias_ambientais'] > 0].sort_values('qtd_ocorrencias_ambientais', ascending=False).head(20)
            st.markdown('#### Ocorrências ambientais')
            cols = _colunas_existentes(amb, ['municipio', 'regiao_saude', 'qtd_ocorrencias_ambientais', 'principais_produtos_ambientais'])
            render_html_table(amb[cols])
    with tabs[8]:
        st.markdown('### Análise municipal')
        municipios = sorted(df['municipio'].dropna().astype(str).unique())
        municipio = st.selectbox('Selecionar município', municipios)
        detalhes = detalhar_municipio_territorial(municipio)
        linha = detalhes.get('linha', {})
        if not linha:
            st.warning('Município não localizado.')
        else:
            st.markdown(f'## {municipio}')
            st.caption(f"Região de Saúde: {linha.get('regiao_saude', '-')}")
            classe = linha.get('classificacao_prioridade_aps', 'Monitoramento')
            texto = f"Índice preliminar: {_fmt_decimal(linha.get('indice_prioridade_aps'))} | Classificação: {classe}"
            if classe in ['Muito alta', 'Alta']:
                st.error(texto)
            elif classe == 'Média':
                st.warning(texto)
            else:
                st.success(texto)
            m1, m2, m3, m4 = st.columns(4)
            m1.metric('População', _fmt_int(linha.get('populacao', 0)))
            m2.metric('Equipes APS', _fmt_int(linha.get('total_equipes_aps', 0)))
            m3.metric('UBS', _fmt_int(linha.get('total_ubs', 0)))
            m4.metric('Profissionais APS', _fmt_int(linha.get('total_profissionais_aps', 0)))
            m5, m6, m7, m8 = st.columns(4)
            m5.metric('População/equipe', _fmt_decimal(linha.get('populacao_por_equipe')))
            m6.metric('População/UBS', _fmt_decimal(linha.get('populacao_por_ubs')))
            m7.metric('Área km²', _fmt_decimal(linha.get('area_km2')))
            m8.metric('Densidade', _fmt_decimal(linha.get('densidade_hab_km2')))
            st.markdown('#### Alertas')
            st.info(linha.get('alertas_territoriais', 'Sem alerta.'))
            comp = pd.DataFrame([{'componente': 'Déficit de equipes APS', 'score': linha.get('score_deficit_equipes', 0), 'peso': '30%'}, {'componente': 'Déficit de UBS', 'score': linha.get('score_deficit_ubs', 0), 'peso': '20%'}, {'componente': 'Pressão populacional', 'score': linha.get('score_pressao_populacional', 0), 'peso': '15%'}, {'componente': 'Dispersão territorial', 'score': linha.get('score_dispersao_territorial', 0), 'peso': '15%'}, {'componente': 'Camadas especiais', 'score': linha.get('score_camadas_especiais', 0), 'peso': '15%'}, {'componente': 'Risco ambiental', 'score': linha.get('score_risco_ambiental', 0), 'peso': '5%'}])
            fig = px.bar(comp, x='score', y='componente', orientation='h', text='peso', title='Composição do índice')
            fig.update_layout(yaxis={'categoryorder': 'total ascending'}, height=380)
            st.plotly_chart(fig, use_container_width=True)
            sub1, sub2, sub3 = st.tabs(['Assentamentos', 'Terras indígenas', 'Ocorrências ambientais'])
            with sub1:
                ass = detalhes.get('assentamentos', pd.DataFrame())
                if ass.empty:
                    st.caption('Sem assentamentos vinculados a este município na camada carregada.')
                else:
                    cols = _colunas_existentes(ass, ['nome_assentamento', 'municipio', 'area_ha', 'modalidade', 'situacao', 'observacao'])
                    render_html_table(ass[cols])
            with sub2:
                tis = detalhes.get('terras_indigenas', pd.DataFrame())
                if tis.empty:
                    st.caption('Sem terras indígenas/interseções identificadas para este município na camada carregada.')
                else:
                    cols = _colunas_existentes(tis, ['nome_terra_indigena', 'etnia', 'municipios_intersectados', 'situacao', 'observacao'])
                    render_html_table(tis[cols])
            with sub3:
                ocorr = detalhes.get('ocorrencias', pd.DataFrame())
                if ocorr.empty:
                    st.caption('Sem ocorrências ambientais vinculadas a este município na camada carregada.')
                else:
                    cols = _colunas_existentes(ocorr, ['data_ocorrencia', 'ano', 'tipo_ocorrencia', 'produto_residuo', 'latitude', 'longitude', 'observacao'])
                    render_html_table(ocorr[cols])
            st.markdown('#### Texto-base para análise técnica')
            texto_base = f"O município de {municipio}, pertencente à Região de Saúde {linha.get('regiao_saude', '-')}, apresenta população estimada de {_fmt_int(linha.get('populacao', 0))} habitantes, {_fmt_int(linha.get('total_equipes_aps', 0))} equipes APS, {_fmt_int(linha.get('total_ubs', 0))} UBS/estabelecimentos e {_fmt_int(linha.get('total_profissionais_aps', 0))} vínculos profissionais nas equipes. A triagem territorial preliminar classificou o município como {classe}, com índice {_fmt_decimal(linha.get('indice_prioridade_aps'))}. Os principais alertas são: {linha.get('alertas_territoriais', 'sem alerta territorial crítico nos critérios atuais')}."
            st.text_area('Copiar texto', texto_base, height=150)
    with tabs[9]:
        st.markdown('### Simulador de cenários')
        st.caption('Ajuste pesos e limites para testar como a priorização muda. Esta simulação não altera a metodologia oficial da tela; ela serve para discussão técnica e pactuação de critérios.')
        st.markdown('#### Pesos dos componentes')
        c1, c2, c3 = st.columns(3)
        with c1:
            peso_equipes = st.slider('Déficit de equipes APS', 0, 60, 30, key='sim_peso_equipes')
            peso_ubs = st.slider('Déficit de UBS', 0, 60, 20, key='sim_peso_ubs')
        with c2:
            peso_pop = st.slider('Pressão populacional', 0, 60, 15, key='sim_peso_pop')
            peso_disp = st.slider('Dispersão territorial', 0, 60, 15, key='sim_peso_disp')
        with c3:
            peso_camadas = st.slider('Camadas especiais', 0, 60, 15, key='sim_peso_camadas')
            peso_amb = st.slider('Risco ambiental', 0, 60, 5, key='sim_peso_amb')
        total_pesos = peso_equipes + peso_ubs + peso_pop + peso_disp + peso_camadas + peso_amb
        if total_pesos <= 0:
            st.warning('Informe pelo menos um peso maior que zero para simular.')
        else:
            st.caption(f'Soma dos pesos: {total_pesos}. O sistema normaliza automaticamente para 100% na simulação.')
        st.markdown('#### Limites de classificação')
        l1, l2, l3 = st.columns(3)
        with l1:
            limite_muito_alta = st.slider('Muito alta a partir de', 0, 100, 70, key='sim_limite_muito_alta')
        with l2:
            limite_alta = st.slider('Alta a partir de', 0, 100, 50, key='sim_limite_alta')
        with l3:
            limite_media = st.slider('Média a partir de', 0, 100, 30, key='sim_limite_media')
        if not limite_muito_alta >= limite_alta >= limite_media:
            st.error('Os limites precisam obedecer: Muito alta ≥ Alta ≥ Média.')
        else:
            pesos = {'score_deficit_equipes': peso_equipes, 'score_deficit_ubs': peso_ubs, 'score_pressao_populacional': peso_pop, 'score_dispersao_territorial': peso_disp, 'score_camadas_especiais': peso_camadas, 'score_risco_ambiental': peso_amb}
            sim = recalcular_indice_prioridade_aps(df, pesos=pesos, limite_muito_alta=float(limite_muito_alta), limite_alta=float(limite_alta), limite_media=float(limite_media))
            resumo_sim = resumo_simulacao_prioridade(sim)
            a, b, c, d = st.columns(4)
            a.metric('Muito alta', _fmt_int((sim['classificacao_simulada'] == 'Muito alta').sum()))
            b.metric('Alta', _fmt_int((sim['classificacao_simulada'] == 'Alta').sum()))
            c.metric('Média', _fmt_int((sim['classificacao_simulada'] == 'Média').sum()))
            d.metric('Monitoramento', _fmt_int((sim['classificacao_simulada'] == 'Monitoramento').sum()))
            col1, col2 = st.columns([1.15, 1])
            with col1:
                top_sim = sim.sort_values('indice_prioridade_simulado', ascending=False).head(20)
                fig = px.bar(top_sim, x='indice_prioridade_simulado', y='municipio', orientation='h', color='classificacao_simulada', title='Top 20 no cenário simulado', labels={'indice_prioridade_simulado': 'Índice simulado', 'municipio': 'Município'})
                fig.update_layout(yaxis={'categoryorder': 'total ascending'}, height=600)
                st.plotly_chart(fig, use_container_width=True)
            with col2:
                st.markdown('##### Resumo por classificação')
                render_html_table(resumo_sim)
                _download_csv(resumo_sim, 'resumo_simulacao_prioridade_aps.csv', 'Baixar resumo')
            st.markdown('#### Municípios que mais mudaram de posição')
            mudancas = sim.copy()
            mudancas['mudanca_abs'] = mudancas['mudanca_ranking'].abs()
            mudancas = mudancas.sort_values(['mudanca_abs', 'indice_prioridade_simulado'], ascending=[False, False]).head(30)
            cols = _colunas_existentes(mudancas, ['municipio', 'regiao_saude', 'ranking_original', 'ranking_simulado', 'mudanca_ranking', 'indice_prioridade_aps', 'indice_prioridade_simulado', 'variacao_indice', 'classificacao_prioridade_aps', 'classificacao_simulada', 'score_deficit_equipes', 'score_deficit_ubs', 'score_pressao_populacional', 'score_dispersao_territorial', 'score_camadas_especiais', 'score_risco_ambiental'])
            render_html_table(mudancas[cols])
            _download_csv(sim, 'simulacao_prioridade_aps.csv', 'Baixar simulação completa')
            st.info('Use o simulador para testar sensibilidade dos critérios. Quando uma mudança de peso altera muito o ranking, isso indica que a decisão precisa ser validada com mais cuidado pelas áreas técnicas.')
    with tabs[10]:
        st.markdown('### Metodologia da triagem')
        st.markdown('\n            A Análise Territorial APS organiza uma leitura preliminar das bases já carregadas para apoiar a gestão estadual\n            na identificação de municípios que merecem investigação técnica prioritária. O resultado deve ser entendido como\n            **ferramenta de inteligência e triagem**, não como norma oficial de cobertura, critério automático de repasse ou\n            substituição da avaliação das áreas técnicas.\n\n            **Componentes e pesos atuais do índice:**\n\n            - Déficit de equipes APS: 30%\n            - Déficit de UBS/estabelecimentos APS: 20%\n            - Pressão populacional: 15%\n            - Dispersão territorial: 15%\n            - Camadas especiais: 15% — assentamentos e terras indígenas/interseções\n            - Risco ambiental: 5% — ocorrências ambientais/produtos perigosos\n\n            **Interpretação institucional recomendada:**\n\n            - Muito alta: prioridade para análise técnica imediata e conferência das bases locais.\n            - Alta: sinal relevante de pressão territorial/assistencial, com necessidade de estudo dirigido.\n            - Média: acompanhamento técnico, especialmente em ciclos de planejamento regional.\n            - Monitoramento: sem alerta prioritário pelos critérios atuais, mantendo atualização periódica.\n\n            **Uso adequado do módulo:**\n\n            - Apoiar reuniões técnicas e leitura comparativa entre municípios e regiões;\n            - Sugerir hipóteses de intervenção para carteira preliminar de ações;\n            - Orientar diagnóstico municipal, sem dispensar checagem de CNES, informações locais e pactuações regionais;\n            - Testar sensibilidade de pesos pelo simulador antes de formalizar qualquer critério.\n\n            A metodologia pode ser aprimorada posteriormente com cobertura oficial, produção APS, desempenho SISAB/e-Gestor,\n            ICSAP, indicadores de vulnerabilidade validados e critérios pactuados pela gestão.\n            ')
        bases = pd.DataFrame([{'base': 'base_municipal_consolidada', 'uso': 'população, região, APS estrutural, UBS, equipes, profissionais, área, densidade'}, {'base': 'dados_mt_assentamentos', 'uso': 'camada territorial de assentamentos/ruralidade'}, {'base': 'dados_mt_terras_indigenas', 'uso': 'camada territorial de terras indígenas e interseções municipais'}, {'base': 'dados_mt_areas_contaminadas', 'uso': 'ocorrências ambientais/produtos perigosos'}, {'base': 'malhas_geograficas_municipais', 'uso': 'suporte indireto para georreferenciamento e inferências territoriais'}])
        render_html_table(bases)
        st.markdown('#### Status de fechamento da versão')
        st.success('A Análise Territorial APS está fechada como primeira versão gerencial. As próximas melhorias devem priorizar validação metodológica com a equipe técnica e integração com o módulo de Georreferenciamento da Saúde.')
        _download_csv(df, 'base_analise_territorial_aps.csv', 'Baixar base analítica completa')
    with tabs[11]:
        st.markdown('### Comparador municipal')
        st.caption('Compare municípios lado a lado para apoiar reuniões técnicas, priorização regional e discussão de carteira de ações.')
        municipios = sorted(df['municipio'].dropna().astype(str).unique())
        padrao = municipios[:3] if len(municipios) >= 3 else municipios
        selecionados = st.multiselect('Selecionar municípios para comparação', municipios, default=padrao, max_selections=8, key='comparador_municipios')
        if not selecionados:
            st.info('Selecione pelo menos um município para comparar.')
        else:
            comp = df[df['municipio'].astype(str).isin(selecionados)].copy()
            cols = _colunas_existentes(comp, ['municipio', 'regiao_saude', 'classificacao_prioridade_aps', 'indice_prioridade_aps', 'populacao', 'area_km2', 'densidade_hab_km2', 'total_ubs', 'total_equipes_aps', 'total_profissionais_aps', 'populacao_por_equipe', 'populacao_por_ubs', 'profissionais_por_equipe', 'qtd_assentamentos', 'qtd_terras_indigenas', 'qtd_ocorrencias_ambientais'])
            render_html_table(comp[cols].sort_values('indice_prioridade_aps', ascending=False))
            st.markdown('#### Componentes do índice')
            score_cols = ['score_deficit_equipes', 'score_deficit_ubs', 'score_pressao_populacional', 'score_dispersao_territorial', 'score_camadas_especiais', 'score_risco_ambiental']
            score_cols = _colunas_existentes(comp, score_cols)
            if score_cols:
                long = comp[['municipio'] + score_cols].melt('municipio', var_name='componente', value_name='score')
                nomes = {'score_deficit_equipes': 'Déficit equipes', 'score_deficit_ubs': 'Déficit UBS', 'score_pressao_populacional': 'Pressão populacional', 'score_dispersao_territorial': 'Dispersão territorial', 'score_camadas_especiais': 'Camadas especiais', 'score_risco_ambiental': 'Risco ambiental'}
                long['componente'] = long['componente'].map(nomes).fillna(long['componente'])
                fig = px.bar(long, x='componente', y='score', color='municipio', barmode='group', title='Comparação dos componentes do índice preliminar', labels={'score': 'Score', 'componente': 'Componente'})
                fig.update_layout(height=480)
                st.plotly_chart(fig, use_container_width=True)
            c1, c2 = st.columns(2)
            with c1:
                fig = px.scatter(comp, x='populacao_por_equipe', y='populacao_por_ubs', size='indice_prioridade_aps', color='classificacao_prioridade_aps', hover_name='municipio', title='Pressão por equipe x pressão por UBS', labels={'populacao_por_equipe': 'População por equipe', 'populacao_por_ubs': 'População por UBS'})
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                camadas_cols = _colunas_existentes(comp, ['municipio', 'qtd_assentamentos', 'qtd_terras_indigenas', 'qtd_ocorrencias_ambientais'])
                if len(camadas_cols) > 1:
                    camadas = comp[camadas_cols].melt('municipio', var_name='camada', value_name='quantidade')
                    camadas['camada'] = camadas['camada'].replace({'qtd_assentamentos': 'Assentamentos', 'qtd_terras_indigenas': 'Terras indígenas', 'qtd_ocorrencias_ambientais': 'Ocorrências ambientais'})
                    fig = px.bar(camadas, x='municipio', y='quantidade', color='camada', barmode='group', title='Camadas territoriais especiais')
                    fig.update_layout(height=430)
                    st.plotly_chart(fig, use_container_width=True)
            st.markdown('#### Leitura rápida comparativa')
            top = comp.sort_values('indice_prioridade_aps', ascending=False).iloc[0]
            st.info(f"Entre os municípios selecionados, {top.get('municipio', '-')} aparece com maior índice preliminar ({_fmt_decimal(top.get('indice_prioridade_aps'))}) e classificação {top.get('classificacao_prioridade_aps', '-')}. Use esta comparação como apoio para discussão técnica, sempre conferindo as bases originais e o contexto local.")
            _download_csv(comp[cols], 'comparador_municipal_aps.csv', 'Baixar comparação')
    with tabs[12]:
        st.markdown('### Relatório municipal automático')
        st.caption('Gere um texto técnico preliminar para apoiar despacho, estudo municipal, reunião técnica ou relatório interno. O texto usa apenas as bases já carregadas e deve ser validado pelas áreas responsáveis.')
        municipios_rel = sorted(df['municipio'].dropna().astype(str).unique())
        municipio_rel = st.selectbox('Selecionar município', municipios_rel, key='relatorio_municipal_select')
        incluir_metodologia = st.checkbox('Incluir observação metodológica no texto', value=True, key='relatorio_municipal_metodologia')
        texto_relatorio = gerar_relatorio_municipal_aps(municipio_rel, incluir_metodologia=incluir_metodologia)
        col1, col2 = st.columns([1.4, 1])
        with col1:
            st.markdown('#### Prévia do relatório')
            st.markdown(texto_relatorio)
        with col2:
            st.markdown('#### Texto copiável')
            st.text_area('Copie e cole em despacho, nota técnica, e-mail ou documento interno', value=texto_relatorio, height=520, key='relatorio_municipal_textarea')
            st.download_button('Baixar relatório em TXT', data=texto_relatorio.encode('utf-8'), file_name=f"relatorio_aps_{municipio_rel.lower().replace(' ', '_')}.txt", mime='text/plain', use_container_width=True)
        st.info('Este relatório é uma minuta automática. Antes de usar institucionalmente, confira CNES, dados locais, produção assistencial e eventuais atualizações enviadas pelo município ou pelo Escritório Regional de Saúde.')
    with tabs[0]:
        st.markdown('### Painel executivo para apresentação')
        st.caption('Síntese gerencial da análise territorial APS, indicada para apresentação rápida à gestão.')
        painel = gerar_painel_executivo_aps(df)
        kpis = painel.get('kpis', {}) or {}
        c1, c2, c3, c4 = st.columns(4)
        c1.metric('Municípios', _fmt_int(kpis.get('municipios', 0)))
        c2.metric('População', _fmt_int(kpis.get('populacao', 0)))
        c3.metric('Alta prioridade', _fmt_int(kpis.get('prioridade_alta', 0)))
        c4.metric('Equipes APS', _fmt_int(kpis.get('equipes', 0)))
        c5, c6, c7, c8 = st.columns(4)
        c5.metric('UBS/estab. APS', _fmt_int(kpis.get('ubs', 0)))
        c6.metric('Mun. com assentamentos', _fmt_int(kpis.get('municipios_assentamentos', 0)))
        c7.metric('Mun. com TI/interseções', _fmt_int(kpis.get('municipios_ti', 0)))
        c8.metric('Mun. com risco ambiental', _fmt_int(kpis.get('municipios_ambiental', 0)))
        st.divider()
        st.markdown('#### Mensagens-chave')
        for msg in painel.get('mensagens', []):
            st.markdown(f'- {msg}')
        st.divider()
        col_a, col_b = st.columns([1.15, 1])
        with col_a:
            top = painel.get('top_prioridade', pd.DataFrame())
            if not top.empty:
                fig = px.bar(top.head(10).sort_values('indice_prioridade_aps'), x='indice_prioridade_aps', y='municipio', orientation='h', title='Top 10 municípios por prioridade territorial APS', labels={'indice_prioridade_aps': 'Índice', 'municipio': 'Município'})
                st.plotly_chart(fig, use_container_width=True)
        with col_b:
            regional = painel.get('regional', pd.DataFrame())
            if not regional.empty and 'municipios_alta_prioridade' in regional.columns:
                fig = px.bar(regional.sort_values('municipios_alta_prioridade', ascending=True), x='municipios_alta_prioridade', y='regiao_saude', orientation='h', title='Regiões com mais municípios em alta prioridade', labels={'municipios_alta_prioridade': 'Municípios', 'regiao_saude': 'Região'})
                st.plotly_chart(fig, use_container_width=True)
        st.divider()
        st.markdown('#### Quadros executivos')
        sub1, sub2 = st.tabs(['Ranking geral', 'Pressões e territórios especiais'])
        with sub1:
            top = painel.get('top_prioridade', pd.DataFrame())
            if not top.empty:
                render_html_table(top[_colunas_existentes(top, _colunas_ranking())])
                _download_csv(top, 'painel_executivo_top_prioridade_aps.csv', 'Baixar ranking executivo')
        with sub2:
            c1, c2 = st.columns(2)
            with c1:
                st.markdown('##### Maior população por equipe')
                top_eq = painel.get('top_pressao_equipes', pd.DataFrame())
                if not top_eq.empty:
                    render_html_table(top_eq[_colunas_existentes(top_eq, ['municipio', 'regiao_saude', 'populacao_por_equipe', 'total_equipes_aps', 'indice_prioridade_aps'])])
                st.markdown('##### Maior população por UBS')
                top_ubs = painel.get('top_pressao_ubs', pd.DataFrame())
                if not top_ubs.empty:
                    render_html_table(top_ubs[_colunas_existentes(top_ubs, ['municipio', 'regiao_saude', 'populacao_por_ubs', 'total_ubs', 'indice_prioridade_aps'])])
            with c2:
                st.markdown('##### Territórios especiais')
                top_terr = painel.get('top_territorios_especiais', pd.DataFrame())
                if not top_terr.empty:
                    render_html_table(top_terr[_colunas_existentes(top_terr, ['municipio', 'regiao_saude', 'qtd_assentamentos', 'qtd_terras_indigenas', 'indice_prioridade_aps'])])
                st.markdown('##### Dispersão territorial')
                top_disp = painel.get('top_dispersao', pd.DataFrame())
                if not top_disp.empty:
                    render_html_table(top_disp[_colunas_existentes(top_disp, ['municipio', 'regiao_saude', 'area_km2', 'densidade_hab_km2', 'indice_prioridade_aps'])])
        st.divider()
        st.info('Uso recomendado: esta aba é uma síntese executiva para reunião. A decisão final deve considerar validação CNES/INE, informações locais, produção assistencial, cobertura oficial e pactuação regional.')
