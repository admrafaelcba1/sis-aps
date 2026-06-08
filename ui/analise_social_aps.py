from __future__ import annotations
import pandas as pd
import plotly.express as px
import streamlit as st
from components.ui_elements import render_html_table
from services.analise_social_service import carregar_analise_social, resumo_regional, texto_municipal_social, montar_matriz_social_acesso, montar_carteira_social_aps

def _fmt_int(v):
    try:
        return f'{int(float(v)):,}'.replace(',', '.')
    except Exception:
        return '0'

def _fmt_float(v, casas=1):
    try:
        return f'{float(v):,.{casas}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return '-'

def _download(df: pd.DataFrame, label: str, nome: str):
    if df is None or df.empty:
        return
    import inspect
    caller = inspect.stack()[1]
    linha = getattr(caller, 'lineno', 0)
    chave_base = f'{label}_{nome}_{linha}'
    chave = 'download_' + ''.join((ch if ch.isalnum() else '_' for ch in str(chave_base)))[:180]
    st.download_button(label=label, data=df.to_csv(index=False).encode('utf-8-sig'), file_name=nome, mime='text/csv', use_container_width=True, key=chave)

def _filtrar(df: pd.DataFrame, key_prefix: str='social'):
    regioes = ['Todas'] + sorted([x for x in df.get('regiao_saude', pd.Series(dtype=str)).dropna().astype(str).unique() if x])
    classes = ['Todas'] + sorted([x for x in df.get('classe_vulnerabilidade_social_aps', pd.Series(dtype=str)).dropna().astype(str).unique() if x])
    c1, c2, c3 = st.columns([1, 1, 1.5])
    regiao = c1.selectbox('Região de Saúde', regioes, key=f'{key_prefix}_regiao')
    classe = c2.selectbox('Classe de vulnerabilidade', classes, key=f'{key_prefix}_classe')
    busca = c3.text_input('Buscar município', placeholder='Ex.: Cuiabá, Colniza, Peixoto de Azevedo', key=f'{key_prefix}_busca')
    out = df.copy()
    if regiao != 'Todas' and 'regiao_saude' in out.columns:
        out = out[out['regiao_saude'] == regiao]
    if classe != 'Todas' and 'classe_vulnerabilidade_social_aps' in out.columns:
        out = out[out['classe_vulnerabilidade_social_aps'] == classe]
    if busca:
        out = out[out['municipio'].astype(str).str.contains(busca, case=False, na=False)]
    return out

def _metricas(resumo: dict):
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Municípios', _fmt_int(resumo.get('municipios', 0)))
    c2.metric('População total', _fmt_int(resumo.get('populacao_total', 0)))
    c3.metric('Vulnerabilidade muito alta', _fmt_int(resumo.get('muito_alta', 0)))
    c4.metric('Vulnerabilidade alta', _fmt_int(resumo.get('alta', 0)))
    c5, c6, c7 = st.columns(3)
    c5.metric('Assentamentos', _fmt_int(resumo.get('assentamentos', 0)))
    c6.metric('Terras indígenas/interseções', _fmt_int(resumo.get('terras_indigenas_intersecoes', 0)))
    c7.metric('Ocorrências ambientais', _fmt_int(resumo.get('ocorrencias_ambientais', 0)))

def _render_prioridade_social(df: pd.DataFrame, resumo: dict):
    st.subheader('Painel de Prioridade Social APS')
    st.caption('Visão executiva para identificar municípios onde vulnerabilidade social, pressão da APS e barreiras territoriais se acumulam. A leitura é preliminar e deve orientar investigação técnica.')
    matriz = montar_matriz_social_acesso(df)
    carteira = montar_carteira_social_aps(df)
    base = matriz.copy() if matriz is not None and (not matriz.empty) else df.copy()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Municípios analisados', _fmt_int(len(df)))
    c2.metric('Vulnerabilidade muito alta', _fmt_int(resumo.get('muito_alta', 0)))
    c3.metric('Vulnerabilidade alta', _fmt_int(resumo.get('alta', 0)))
    if matriz is not None and (not matriz.empty) and ('prioridade_social_acesso' in matriz.columns):
        criticos_integrados = int(matriz['prioridade_social_acesso'].isin(['Prioridade máxima', 'Alta prioridade']).sum())
    else:
        criticos_integrados = 0
    c4.metric('Prioridade social integrada', _fmt_int(criticos_integrados))
    st.markdown('#### Mensagens-chave')
    msgs = []
    if resumo.get('muito_alta', 0):
        msgs.append(f"**{_fmt_int(resumo.get('muito_alta', 0))} municípios** estão em vulnerabilidade social APS muito alta.")
    if resumo.get('alta', 0):
        msgs.append(f"**{_fmt_int(resumo.get('alta', 0))} municípios** estão em vulnerabilidade social APS alta.")
    if criticos_integrados:
        msgs.append(f'**{_fmt_int(criticos_integrados)} municípios** combinam vulnerabilidade social elevada com alerta territorial/acesso rural relevante.')
    if not carteira.empty:
        linhas = carteira['linha_acao'].value_counts().head(3).index.tolist()
        msgs.append('Principais linhas preliminares de ação: **' + ', '.join(linhas) + '**.')
    if not msgs:
        msgs.append('A base atual permite triagem social, mas ainda exige validação técnica dos componentes disponíveis.')
    st.markdown('\n'.join([f'- {m}' for m in msgs]))
    st.markdown('#### Municípios para olhar primeiro')
    col1, col2 = st.columns([1.15, 1])
    with col1:
        if 'indice_social_aps' in df.columns:
            top = df.sort_values('indice_social_aps', ascending=False).head(15).copy()
            fig = px.bar(top.sort_values('indice_social_aps'), x='indice_social_aps', y='municipio', orientation='h', color='classe_vulnerabilidade_social_aps' if 'classe_vulnerabilidade_social_aps' in top.columns else None, title='Top 15 — prioridade social APS')
            st.plotly_chart(fig, use_container_width=True, key='social_plot_01')
    with col2:
        if matriz is not None and (not matriz.empty) and ('prioridade_social_acesso' in matriz.columns):
            dist = matriz['prioridade_social_acesso'].value_counts().reset_index()
            dist.columns = ['prioridade', 'municipios']
            fig = px.bar(dist, x='prioridade', y='municipios', title='Prioridade integrada social + acesso')
            st.plotly_chart(fig, use_container_width=True, key='social_plot_02')
        else:
            dist = df['classe_vulnerabilidade_social_aps'].value_counts().reset_index()
            dist.columns = ['classe', 'municipios']
            fig = px.pie(dist, names='classe', values='municipios', title='Classes de vulnerabilidade')
            st.plotly_chart(fig, use_container_width=True, key='social_plot_03')
    st.markdown('#### Ranking executivo')
    ranking = base.copy()
    if 'prioridade_social_acesso' not in ranking.columns:
        ranking['prioridade_social_acesso'] = ranking.get('classe_vulnerabilidade_social_aps', '-')
    cols = ['municipio', 'regiao_saude', 'prioridade_social_acesso', 'indice_social_aps', 'classe_vulnerabilidade_social_aps', 'eixo_social_dominante', 'pop_por_equipe_aps', 'pop_por_ubs', 'assentamentos_analisados_acesso', 'assentamentos_criticos_acesso', 'distancia_media_assentamento_ubs_km', 'distancia_maxima_assentamento_ubs_km', 'assentamentos', 'terras_indigenas_intersecoes', 'ocorrencias_ambientais']
    cols = [c for c in cols if c in ranking.columns]
    ordem = {'Prioridade máxima': 1, 'Alta prioridade': 2, 'Atenção': 3, 'Monitoramento': 4, 'Muito alta': 1, 'Alta': 2, 'Média': 3}
    if 'prioridade_social_acesso' in ranking.columns:
        ranking['_ordem'] = ranking['prioridade_social_acesso'].map(ordem).fillna(9)
        ranking = ranking.sort_values(['_ordem', 'indice_social_aps'], ascending=[True, False], na_position='last')
    else:
        ranking = ranking.sort_values('indice_social_aps', ascending=False, na_position='last')
    render_html_table(ranking[cols].head(50))
    _download(ranking[cols], 'Baixar ranking executivo social APS', 'ranking_executivo_social_aps.csv')
    st.markdown('#### Carteira preliminar por linha de ação')
    if carteira.empty:
        st.info('A carteira social ainda não gerou ações com os critérios atuais.')
    else:
        c1, c2 = st.columns(2)
        with c1:
            resumo_linha = carteira['linha_acao'].value_counts().reset_index()
            resumo_linha.columns = ['linha_acao', 'acoes']
            fig = px.bar(resumo_linha, x='linha_acao', y='acoes', title='Ações por linha')
            st.plotly_chart(fig, use_container_width=True, key='social_plot_04')
        with c2:
            resumo_reg = carteira.groupby('regiao_saude', dropna=False).size().reset_index(name='acoes').sort_values('acoes', ascending=False).head(12)
            fig = px.bar(resumo_reg.sort_values('acoes'), x='acoes', y='regiao_saude', orientation='h', title='Ações por Região de Saúde')
            st.plotly_chart(fig, use_container_width=True, key='social_plot_05')
        render_html_table(carteira.head(80))
        _download(carteira, 'Baixar carteira social APS', 'carteira_social_aps.csv')

def render():
    st.title('Determinantes Sociais e APS')
    st.info('Esta leitura cruza dados sociais, demográficos, educacionais, territoriais e estrutura APS para apoiar a identificação de municípios que exigem maior atenção técnica. O índice é preliminar e serve como triagem gerencial, não como regra normativa oficial.')
    dados = carregar_analise_social()
    if not dados.get('ok'):
        st.error(dados.get('mensagem', 'Não foi possível carregar a análise social.'))
        return
    df = dados['df']
    resumo = dados.get('resumo', {})
    abas = st.tabs(['Prioridade social APS', 'Painel social', 'Ranking', 'Social x APS', 'Social x Acesso rural', 'Carteira social APS', 'Água, esgoto e educação', 'Territórios vulneráveis', 'Regiões de Saúde', 'Município', 'Metodologia'])
    with abas[0]:
        _render_prioridade_social(df, resumo)
    with abas[1]:
        st.subheader('Painel social — síntese executiva')
        _metricas(resumo)
        componentes = resumo.get('componentes_usados', [])
        if componentes:
            st.caption('Componentes usados no índice nesta base: ' + ', '.join(componentes))
        else:
            st.warning('Não foram encontrados componentes sociais diretos suficientes; a leitura está usando apenas proxies territoriais/APS disponíveis.')
        col1, col2 = st.columns([1.2, 1])
        with col1:
            top = df.head(20).copy()
            fig = px.bar(top.sort_values('indice_social_aps'), x='indice_social_aps', y='municipio', orientation='h', title='Top 20 — Índice Social APS')
            st.plotly_chart(fig, use_container_width=True, key='social_plot_06')
        with col2:
            dist = df['classe_vulnerabilidade_social_aps'].value_counts().reset_index()
            dist.columns = ['classe', 'municipios']
            fig = px.pie(dist, names='classe', values='municipios', title='Distribuição por classe')
            st.plotly_chart(fig, use_container_width=True, key='social_plot_07')
        st.subheader('Mensagens-chave')
        muito_alta = int(resumo.get('muito_alta', 0))
        alta = int(resumo.get('alta', 0))
        st.markdown(f'- **{muito_alta} municípios** aparecem em vulnerabilidade social APS muito alta.\n- **{alta} municípios** aparecem em vulnerabilidade alta.\n- A leitura deve ser cruzada com o módulo de Georreferenciamento, especialmente a aba de **Acesso rural APS**.\n- Municípios com vulnerabilidade social elevada e pressão assistencial alta devem entrar na fila de investigação técnica.')
    with abas[2]:
        st.subheader('Ranking de vulnerabilidade social associada à APS')
        filtrado = _filtrar(df, 'ranking')
        cols = ['ranking_social_aps', 'municipio', 'regiao_saude', 'indice_social_aps', 'classe_vulnerabilidade_social_aps', 'eixo_social_dominante', 'populacao', 'pop_por_equipe_aps', 'pop_por_ubs', 'assentamentos', 'terras_indigenas_intersecoes', 'ocorrencias_ambientais']
        cols = [c for c in cols if c in filtrado.columns]
        render_html_table(filtrado[cols])
        _download(filtrado[cols], 'Baixar ranking social em CSV', 'ranking_social_aps.csv')
    with abas[3]:
        st.subheader('Cruzamento Social x Estrutura APS')
        st.caption('Ajuda a identificar municípios com vulnerabilidade elevada e maior pressão sobre equipes ou UBS.')
        filtrado = _filtrar(df, 'social_x_aps')
        col1, col2 = st.columns(2)
        with col1:
            fig = px.scatter(filtrado, x='pop_por_equipe_aps', y='indice_social_aps', size='populacao', color='classe_vulnerabilidade_social_aps', hover_name='municipio', title='Índice Social APS x População por equipe APS')
            st.plotly_chart(fig, use_container_width=True, key='social_plot_08')
        with col2:
            fig = px.scatter(filtrado, x='pop_por_ubs', y='indice_social_aps', size='populacao', color='classe_vulnerabilidade_social_aps', hover_name='municipio', title='Índice Social APS x População por UBS')
            st.plotly_chart(fig, use_container_width=True, key='social_plot_09')
        corte = filtrado.sort_values(['indice_social_aps', 'pop_por_equipe_aps'], ascending=False).head(30)
        render_html_table(corte[[c for c in ['municipio', 'regiao_saude', 'indice_social_aps', 'classe_vulnerabilidade_social_aps', 'pop_por_equipe_aps', 'pop_por_ubs', 'total_equipes_aps', 'total_ubs'] if c in corte.columns]])
    with abas[4]:
        st.subheader('Social x Acesso rural')
        st.caption('Cruza o Índice Social APS com a distância real dos assentamentos até UBS/APS, quando a camada de acesso rural já estiver calculável no módulo de Georreferenciamento.')
        matriz = montar_matriz_social_acesso(df)
        if matriz.empty:
            st.warning('Não foi possível montar a matriz Social x Acesso rural. Verifique se a aba Acesso rural APS do Georreferenciamento já está funcionando.')
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric('Municípios na matriz', _fmt_int(len(matriz)))
            c2.metric('Prioridade máxima', _fmt_int((matriz['prioridade_social_acesso'] == 'Prioridade máxima').sum() if 'prioridade_social_acesso' in matriz.columns else 0))
            c3.metric('Alta prioridade', _fmt_int((matriz['prioridade_social_acesso'] == 'Alta prioridade').sum() if 'prioridade_social_acesso' in matriz.columns else 0))
            c4.metric('Com assentamentos analisados', _fmt_int((matriz.get('assentamentos_analisados_acesso', pd.Series(dtype=float)) > 0).sum()))
            col1, col2 = st.columns(2)
            with col1:
                dist = matriz['prioridade_social_acesso'].value_counts().reset_index() if 'prioridade_social_acesso' in matriz.columns else pd.DataFrame()
                if not dist.empty:
                    dist.columns = ['prioridade', 'municipios']
                    fig = px.bar(dist, x='prioridade', y='municipios', title='Municípios por prioridade integrada')
                    st.plotly_chart(fig, use_container_width=True, key='social_plot_10')
            with col2:
                plot = matriz.copy()
                if {'distancia_maxima_assentamento_ubs_km', 'indice_social_aps'}.issubset(plot.columns):
                    fig = px.scatter(plot, x='distancia_maxima_assentamento_ubs_km', y='indice_social_aps', color='prioridade_social_acesso', size='assentamentos_analisados_acesso' if 'assentamentos_analisados_acesso' in plot.columns else None, hover_name='municipio', title='Vulnerabilidade social x distância rural máxima até APS')
                    st.plotly_chart(fig, use_container_width=True, key='social_plot_11')
            cols = [c for c in ['municipio', 'regiao_saude', 'prioridade_social_acesso', 'indice_social_aps', 'classe_vulnerabilidade_social_aps', 'alerta_acesso_rural', 'assentamentos_analisados_acesso', 'assentamentos_criticos_acesso', 'assentamentos_distantes_acesso', 'distancia_media_assentamento_ubs_km', 'distancia_maxima_assentamento_ubs_km', 'eixo_social_dominante', 'encaminhamento_integrado'] if c in matriz.columns]
            render_html_table(matriz[cols])
            _download(matriz[cols], 'Baixar matriz Social x Acesso rural', 'matriz_social_acesso_rural.csv')
    with abas[5]:
        st.subheader('Carteira social APS')
        st.caption('Converte a leitura social e territorial em linhas preliminares de ação para discussão técnica.')
        carteira = montar_carteira_social_aps(df)
        if carteira.empty:
            st.info('Nenhuma ação social preliminar foi gerada com os critérios atuais.')
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric('Ações mapeadas', _fmt_int(len(carteira)))
            c2.metric('Municípios envolvidos', _fmt_int(carteira['municipio'].nunique()))
            c3.metric('Linhas de ação', _fmt_int(carteira['linha_acao'].nunique()))
            col1, col2 = st.columns(2)
            with col1:
                resumo_linha = carteira['linha_acao'].value_counts().reset_index()
                resumo_linha.columns = ['linha_acao', 'acoes']
                fig = px.bar(resumo_linha, x='linha_acao', y='acoes', title='Ações por linha de atuação')
                st.plotly_chart(fig, use_container_width=True, key='social_plot_12')
            with col2:
                resumo_reg = carteira.groupby('regiao_saude', dropna=False).size().reset_index(name='acoes').sort_values('acoes', ascending=False).head(20)
                fig = px.bar(resumo_reg.sort_values('acoes'), x='acoes', y='regiao_saude', orientation='h', title='Ações por Região de Saúde')
                st.plotly_chart(fig, use_container_width=True, key='social_plot_13')
            render_html_table(carteira)
            _download(carteira, 'Baixar carteira social APS', 'carteira_social_aps.csv')
    with abas[6]:
        st.subheader('Água, esgoto e educação — determinantes sociais diretos')
        st.caption('Esta aba explicita componentes que antes ficavam diluídos no índice: abastecimento de água, esgoto/saneamento, alfabetização/analfabetismo e perfil educacional. Quando a fonte não estiver disponível, o campo permanece vazio; o sistema não inventa dado.')
        filtrado = _filtrar(df, 'agua_esgoto_educacao')
        fontes = resumo.get('fontes_determinantes', {}) or {}
        if fontes:
            with st.expander('Fontes/colunas detectadas para os determinantes'):
                fontes_df = pd.DataFrame([{'determinante': k, 'coluna_detectada': v or 'não localizada'} for k, v in fontes.items()])
                render_html_table(fontes_df)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric('Com água/abastecimento', _fmt_int(filtrado['agua_indicador'].notna().sum()) if 'agua_indicador' in filtrado.columns else '0')
        c2.metric('Com esgoto/saneamento', _fmt_int(filtrado['esgoto_indicador'].notna().sum()) if 'esgoto_indicador' in filtrado.columns else '0')
        c3.metric('Com analfabetismo', _fmt_int(filtrado['taxa_analfabetismo_estimada'].notna().sum()) if 'taxa_analfabetismo_estimada' in filtrado.columns else '0')
        if 'taxa_analfabetismo_estimada' in filtrado.columns and filtrado['taxa_analfabetismo_estimada'].notna().any():
            c4.metric('Analfabetismo médio', f"{filtrado['taxa_analfabetismo_estimada'].mean():.1f}%")
        else:
            c4.metric('Analfabetismo médio', '-')
        col1, col2 = st.columns(2)
        with col1:
            if 'taxa_analfabetismo_estimada' in filtrado.columns and filtrado['taxa_analfabetismo_estimada'].notna().any():
                top = filtrado.sort_values('taxa_analfabetismo_estimada', ascending=False).head(20)
                fig = px.bar(top.sort_values('taxa_analfabetismo_estimada'), x='taxa_analfabetismo_estimada', y='municipio', orientation='h', title='Maiores taxas estimadas de analfabetismo')
                st.plotly_chart(fig, use_container_width=True, key='social_plot_14')
            elif 'pct_escolas_rurais' in filtrado.columns:
                top = filtrado.sort_values('pct_escolas_rurais', ascending=False).head(20)
                fig = px.bar(top.sort_values('pct_escolas_rurais'), x='pct_escolas_rurais', y='municipio', orientation='h', title='Municípios com maior percentual de escolas rurais')
                st.plotly_chart(fig, use_container_width=True, key='social_plot_15')
        with col2:
            if 'esgoto_indicador' in filtrado.columns and filtrado['esgoto_indicador'].notna().any():
                top = filtrado.sort_values('esgoto_indicador', ascending=True).head(20)
                fig = px.bar(top.sort_values('esgoto_indicador', ascending=False), x='esgoto_indicador', y='municipio', orientation='h', title='Menores indicadores de esgoto/saneamento detectados')
                st.plotly_chart(fig, use_container_width=True, key='social_plot_16')
            elif 'agua_indicador' in filtrado.columns and filtrado['agua_indicador'].notna().any():
                top = filtrado.sort_values('agua_indicador', ascending=True).head(20)
                fig = px.bar(top.sort_values('agua_indicador', ascending=False), x='agua_indicador', y='municipio', orientation='h', title='Menores indicadores de água/abastecimento detectados')
                st.plotly_chart(fig, use_container_width=True, key='social_plot_17')
        st.markdown('#### Tabela dos determinantes sociais diretos')
        cols = [c for c in ['municipio', 'regiao_saude', 'indice_social_aps', 'classe_vulnerabilidade_social_aps', 'agua_indicador', 'esgoto_indicador', 'lixo_indicador', 'saneamento_indicador', 'taxa_alfabetizacao_pct', 'taxa_analfabetismo_estimada', 'educacao_indicador', 'renda_indicador', 'pop_por_equipe_aps', 'pop_por_ubs', 'escolas_total', 'escolas_rurais', 'pct_escolas_rurais', 'matriculas_total', 'matriculas_educacao_especial_por_mil'] if c in filtrado.columns]
        if cols:
            ordem = 'taxa_analfabetismo_estimada' if 'taxa_analfabetismo_estimada' in filtrado.columns else 'indice_social_aps'
            render_html_table(filtrado[cols].sort_values(ordem, ascending=False, na_position='last'))
            _download(filtrado[cols], 'Baixar água, esgoto e educação', 'agua_esgoto_educacao_aps.csv')
        else:
            st.warning('Nenhum determinante direto de água, esgoto ou educação foi encontrado na base atual.')
    with abas[7]:
        st.subheader('Territórios vulneráveis e determinantes territoriais')
        filtrado = _filtrar(df, 'territorios_vulneraveis')
        c1, c2, c3 = st.columns(3)
        c1.metric('Assentamentos no recorte', _fmt_int(filtrado.get('assentamentos', pd.Series()).sum()))
        c2.metric('Terras indígenas/interseções', _fmt_int(filtrado.get('terras_indigenas_intersecoes', pd.Series()).sum()))
        c3.metric('Ocorrências ambientais', _fmt_int(filtrado.get('ocorrencias_ambientais', pd.Series()).sum()))
        col1, col2 = st.columns(2)
        with col1:
            top = filtrado.sort_values('assentamentos', ascending=False).head(20)
            fig = px.bar(top.sort_values('assentamentos'), x='assentamentos', y='municipio', orientation='h', title='Assentamentos por município')
            st.plotly_chart(fig, use_container_width=True, key='social_plot_18')
        with col2:
            top = filtrado.sort_values('terras_indigenas_intersecoes', ascending=False).head(20)
            fig = px.bar(top.sort_values('terras_indigenas_intersecoes'), x='terras_indigenas_intersecoes', y='municipio', orientation='h', title='Terras indígenas/interseções por município')
            st.plotly_chart(fig, use_container_width=True, key='social_plot_19')
        cols = [c for c in ['municipio', 'regiao_saude', 'indice_social_aps', 'assentamentos', 'terras_indigenas_intersecoes', 'ocorrencias_ambientais', 'area_km2', 'densidade_hab_km2', 'pct_escolas_rurais'] if c in filtrado.columns]
        render_html_table(filtrado[cols].sort_values(['assentamentos', 'terras_indigenas_intersecoes'], ascending=False))
    with abas[8]:
        st.subheader('Resumo por Região de Saúde')
        reg = resumo_regional(df)
        if reg.empty:
            st.warning('Não foi possível montar resumo regional.')
        else:
            col1, col2 = st.columns(2)
            with col1:
                fig = px.bar(reg.sort_values('indice_medio'), x='indice_medio', y='regiao_saude', orientation='h', title='Índice médio por Região de Saúde')
                st.plotly_chart(fig, use_container_width=True, key='social_plot_20')
            with col2:
                fig = px.bar(reg.sort_values('alta', ascending=False).head(20), x='regiao_saude', y=['muito_alta', 'alta'], title='Municípios em alta/muito alta por Região')
                st.plotly_chart(fig, use_container_width=True, key='social_plot_21')
            render_html_table(reg)
            _download(reg, 'Baixar resumo regional', 'resumo_regional_social_aps.csv')
    with abas[9]:
        st.subheader('Leitura municipal')
        municipios = sorted(df['municipio'].dropna().astype(str).unique())
        municipio = st.selectbox('Selecionar município', municipios, key='social_municipio_select')
        linha = df[df['municipio'] == municipio]
        if not linha.empty:
            r = linha.iloc[0]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric('Índice Social APS', _fmt_float(r.get('indice_social_aps')))
            c2.metric('Classe', str(r.get('classe_vulnerabilidade_social_aps', '-')))
            c3.metric('Pop./equipe APS', _fmt_float(r.get('pop_por_equipe_aps')))
            c4.metric('Pop./UBS', _fmt_float(r.get('pop_por_ubs')))
            cols = [c for c in ['municipio', 'regiao_saude', 'populacao', 'indice_social_aps', 'classe_vulnerabilidade_social_aps', 'eixo_social_dominante', 'total_equipes_aps', 'total_ubs', 'assentamentos', 'terras_indigenas_intersecoes', 'ocorrencias_ambientais', 'escolas_total', 'escolas_rurais', 'matriculas_total'] if c in linha.columns]
            render_html_table(linha[cols])
            texto = texto_municipal_social(df, municipio)
            st.text_area('Síntese técnica copiável', texto, height=260)
            st.download_button('Baixar síntese municipal em TXT', texto.encode('utf-8'), f'sintese_social_{municipio}.txt', 'text/plain', use_container_width=True)
    with abas[10]:
        st.subheader('Metodologia e limites')
        st.markdown('\n            **Objetivo.** Transformar bases sociais, territoriais e de estrutura APS em uma leitura gerencial para priorização técnica.\n\n            **O índice é preliminar.** Ele não substitui norma, pactuação, deliberação técnica ou análise epidemiológica detalhada. Serve para triagem e comparação inicial.\n\n            **Componentes possíveis.** A rotina utiliza os indicadores disponíveis no banco. Quando dados diretos de renda, água, esgoto/saneamento, alfabetização/analfabetismo ou instrução estiverem preenchidos, eles entram no cálculo e aparecem de forma explícita na aba **Água, esgoto e educação**. Quando não estiverem disponíveis, a leitura usa proxies territoriais e APS, como pressão por equipe, pressão por UBS, ruralidade escolar, assentamentos, terras indígenas, dispersão territorial e ocorrências ambientais.\n\n            **Integração com georreferenciamento.** Municípios com vulnerabilidade social elevada devem ser analisados em conjunto com a aba de Acesso Rural APS, especialmente onde há assentamentos distantes da UBS/APS de referência. A aba **Social x Acesso rural** faz esse cruzamento quando as distâncias reais já estiverem disponíveis.\n\n            **Carteira social APS.** A carteira preliminar não determina intervenção automática; ela organiza linhas de ação para investigação técnica, combinando determinantes sociais, pressão assistencial e barreiras territoriais.\n\n            **Uso recomendado.** A tela deve apoiar discussão técnica, seleção de municípios para aprofundamento, elaboração de diagnósticos municipais e definição de carteiras de intervenção.\n            ')
        preench = pd.DataFrame([{'campo': k, 'municipios_preenchidos': v} for k, v in resumo.get('social_cols_preenchidas', {}).items()])
        if not preench.empty:
            st.caption('Cobertura dos principais campos sociais detectados na base.')
            render_html_table(preench)
