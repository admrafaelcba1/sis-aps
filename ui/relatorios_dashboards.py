from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from components.ui_elements import render_html_table
from services.dashboards_relatorios_service import (
    montar_base_dashboard_integrado,
    resumo_executivo,
    gerar_relatorio_municipal_texto,
    gerar_relatorio_regional_texto,
    gerar_insights_cruzados,
    referencias_tecnicas,
    detectar_series_historicas,
    montar_tendencias_municipais,
    calcular_evidencias_municipio,
    consequencias_provaveis,
    principal_motor_prioridade,
    classificar_situacao,
)



CORES_SITUACAO = {
    'Bom': '#12B76A',
    'Regular': '#FEC84B',
    'Ruim': '#F79009',
    'Péssimo': '#F04438',
    'Crítico': '#B42318',
    'Não classificado': '#98A2B3',
}

ORDEM_SITUACAO = ['Bom', 'Regular', 'Ruim', 'Péssimo', 'Crítico']


def _pct(valor, total, casas: int = 1) -> str:
    try:
        total = float(total)
        if total == 0:
            return '0,0%'
        return f"{(float(valor) / total * 100):.{casas}f}%".replace('.', ',')
    except Exception:
        return '-'


def _legenda_regua_score(titulo: str = 'Régua oficial do score integrado APS'):
    _nota_leitura(titulo, [
        'Todos os scores variam de 0 a 100. Quanto maior, maior a prioridade/fragilidade relativa na régua atual.',
        'Bom: 0 a 19,9 | Regular: 20 a 39,9 | Ruim: 40 a 59,9 | Péssimo: 60 a 79,9 | Crítico: 80 a 100.',
        'O Ranking Mestre usa o Score Integrado APS. Rankings temáticos devem ser lidos como recortes complementares, não como ranking oficial principal.',
    ], tipo='info')

# -----------------------------------------------------------------------------
# Componentes visuais
# -----------------------------------------------------------------------------

def _fmt_int(v) -> str:
    try:
        return f"{int(float(v)):,}".replace(',', '.')
    except Exception:
        return '0'


def _fmt_float(v, casas: int = 1) -> str:
    try:
        return f"{float(v):,.{casas}f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return '-'


def _tipo_por_classe(classe: str) -> str:
    c = str(classe or '').lower()
    if 'crít' in c or 'crit' in c:
        return 'critico'
    if 'péss' in c or 'pess' in c:
        return 'critico'
    if 'ruim' in c:
        return 'alerta'
    if 'regular' in c:
        return 'info'
    return 'ok'


def _info_card(titulo: str, valor: str, texto: str, tipo: str = 'info'):
    cores = {
        'info': ('#EAF2FF', '#1E276F', '#2F80ED'),
        'ok': ('#ECFDF3', '#05603A', '#12B76A'),
        'alerta': ('#FFF7E6', '#7A4E00', '#F59E0B'),
        'critico': ('#FEECEC', '#8A1F1F', '#EF4444'),
        'neutro': ('#F8FAFC', '#0F172A', '#94A3B8'),
    }
    bg, fg, border = cores.get(tipo, cores['info'])
    st.markdown(f"""
    <div style="background:{bg};border-left:5px solid {border};padding:1rem;border-radius:18px;margin:.35rem 0 1rem 0;min-height:150px;box-shadow:0 8px 22px rgba(15,23,42,.06);">
        <div style="font-size:.82rem;font-weight:850;color:{fg};letter-spacing:.04em;text-transform:uppercase;opacity:.9;">{titulo}</div>
        <div style="font-size:2rem;font-weight:950;color:{fg};line-height:1.15;margin:.25rem 0;">{valor}</div>
        <div style="font-size:.92rem;color:{fg};line-height:1.42;">ℹ️ {texto}</div>
    </div>
    """, unsafe_allow_html=True)


def _nota_leitura(titulo: str, itens: list[str], tipo: str = 'info'):
    cores = {
        'info': ('#EAF2FF', '#1E276F', '#2F80ED'),
        'ok': ('#ECFDF3', '#05603A', '#12B76A'),
        'alerta': ('#FFF7E6', '#7A4E00', '#F59E0B'),
        'critico': ('#FEECEC', '#8A1F1F', '#EF4444'),
        'neutro': ('#F8FAFC', '#0F172A', '#94A3B8'),
    }
    bg, fg, border = cores.get(tipo, cores['info'])
    lis = ''.join([f'<li style="margin:.25rem 0;">{item}</li>' for item in itens])
    st.markdown(f"""
    <div style="background:{bg};border-left:5px solid {border};padding:.9rem 1rem;border-radius:15px;margin:.5rem 0 1.15rem 0;color:{fg};">
        <div style="font-weight:900;margin-bottom:.35rem;">ℹ️ {titulo}</div>
        <ul style="margin:.1rem 0 .1rem 1.1rem;padding:0;line-height:1.48;font-size:.93rem;">{lis}</ul>
    </div>
    """, unsafe_allow_html=True)


def _download_csv(df: pd.DataFrame, nome: str, label: str):
    if df is None or df.empty:
        return
    st.download_button(label, df.to_csv(index=False).encode('utf-8-sig'), file_name=nome, mime='text/csv', use_container_width=True)


def _classe_style(val):
    s = str(val).lower()
    if 'crít' in s or 'crit' in s or 'péss' in s or 'pess' in s:
        return 'background-color:#FEECEC;color:#8A1F1F;font-weight:700;'
    if 'ruim' in s:
        return 'background-color:#FFF7E6;color:#7A4E00;font-weight:700;'
    if 'regular' in s:
        return 'background-color:#EAF2FF;color:#1E276F;font-weight:700;'
    if 'bom' in s:
        return 'background-color:#ECFDF3;color:#05603A;font-weight:700;'
    return ''


def _tabela_colorida(df: pd.DataFrame, altura: int = 420):
    if df is None or df.empty:
        st.info('Sem dados suficientes para tabela.')
        return
    show = df.copy()
    for c in show.columns:
        if pd.api.types.is_float_dtype(show[c]) or pd.api.types.is_integer_dtype(show[c]):
            show[c] = pd.to_numeric(show[c], errors='coerce')
    
    style_cols = [c for c in show.columns if 'class' in c.lower() or 'situa' in c.lower() or 'prioridade' in c.lower()]
    try:
        if style_cols:
            styler = show.style.map(_classe_style, subset=style_cols)
        else:
            styler = show.style
    except Exception:
        st.dataframe(show, use_container_width=True, height=altura)
        return
    st.dataframe(styler, use_container_width=True, height=altura)


def _termometro(valor: float, titulo: str = 'Termômetro da prioridade integrada'):
    try:
        v = float(valor)
    except Exception:
        v = 0.0
    fig = go.Figure(go.Indicator(
        mode='gauge+number',
        value=v,
        number={'suffix': ' / 100'},
        title={'text': titulo},
        gauge={
            'axis': {'range': [0, 100]},
            'bar': {'color': '#1E276F'},
            'steps': [
                {'range': [0, 20], 'color': '#D1FAE5'},
                {'range': [20, 40], 'color': '#DBEAFE'},
                {'range': [40, 60], 'color': '#FEF3C7'},
                {'range': [60, 80], 'color': '#FED7AA'},
                {'range': [80, 100], 'color': '#FECACA'},
            ],
            'threshold': {'line': {'color': '#111827', 'width': 4}, 'thickness': .75, 'value': v}
        }
    ))
    fig.update_layout(height=265, margin=dict(l=20, r=20, t=55, b=10))
    st.plotly_chart(fig, use_container_width=True)
    _nota_leitura('Como ler o termômetro', [
        'O ponteiro mostra a posição média ou municipal na régua integrada de prioridade APS.',
        'A régua combina vulnerabilidade social, capacidade APS, acesso territorial, ruralidade, saneamento e volume de demanda quando disponíveis.',
        'Quanto mais próximo do vermelho, maior a necessidade de validação técnica, pactuação e possível resposta de gestão.',
        'A régua é uma fila técnica de priorização; não substitui decisão formal, visita técnica ou validação municipal/ERS.'
    ])



def _texto_faixas() -> list[str]:
    return [
        'Bom: score integrado de 0 a 19,9 — indica menor pressão relativa na régua atual.',
        'Regular: 20 a 39,9 — exige monitoramento e prevenção para não piorar.',
        'Ruim: 40 a 59,9 — já combina fragilidades relevantes e deve entrar na agenda técnica.',
        'Péssimo: 60 a 79,9 — prioridade alta para validação, pactuação e resposta coordenada.',
        'Crítico: 80 a 100 — alerta máximo na régua preliminar; exige validação imediata.',
    ]


def _texto_ponderacao() -> list[str]:
    return [
        '25% vulnerabilidade social (CadÚnico, Bolsa Família, MDS, saneamento e escolaridade baixa).',
        '25% fragilidade da capacidade APS (principalmente população por equipe e por UBS).',
        '20% acesso territorial (distância média/máxima até UBS e barreiras de deslocamento).',
        '15% ruralidade e territórios especiais (escolas rurais, assentamentos, indígenas, quilombolas).',
        '10% saneamento inadequado.',
        '5% volume absoluto de população vulnerável (CadÚnico + Bolsa Família).',
    ]


def _hover_padrao(campos: list[str]) -> str:
    base = '<b>%{y}</b><br>'
    mapa = {
        'score_prioridade': 'Score integrado',
        'classificacao_prioridade': 'Classificação',
        'regiao_saude': 'Região de saúde',
        'principal_motor': 'Principal motor',
        'por_que_ruim': 'Por que entrou no radar',
        'decisao_sugerida': 'Leitura/decisão sugerida',
    }
    for i, c in enumerate(campos):
        rot = mapa.get(c, c)
        base += f'{rot}: %{{customdata[{i}]}}<br>'
    base += '<extra></extra>'
    return base


def _grafico_barras(df: pd.DataFrame, x: str, y: str, titulo: str, nota: list[str] | None = None, cor: str | None = None):
    if df is None or df.empty or x not in df.columns or y not in df.columns:
        st.info('Base insuficiente para este gráfico.')
        return
    base = df.copy()
    base[x] = pd.to_numeric(base[x], errors='coerce')
    base = base.dropna(subset=[x, y])
    color_arg = cor if cor in base.columns else None
    custom_cols = [c for c in ['regiao_saude', 'classificacao_prioridade', 'principal_motor', 'decisao_sugerida', 'por_que_ruim'] if c in base.columns]
    fig = px.bar(
        base, x=x, y=y, orientation='h', title=titulo, color=color_arg, text=x,
        hover_data=custom_cols,
        color_discrete_map=CORES_SITUACAO if color_arg else None,
        category_orders={'classificacao_prioridade': ORDEM_SITUACAO}
    )
    fig.update_traces(
        texttemplate='%{text:.1f}', textposition='outside', cliponaxis=False,
        customdata=base[custom_cols].to_numpy() if custom_cols else None,
        hovertemplate=_hover_padrao(custom_cols) if custom_cols else None,
    )
    fig.update_layout(
        yaxis={'categoryorder': 'total ascending', 'title': 'Município'},
        xaxis_title='Score integrado de prioridade (0 a 100)',
        legend_title='Classificação',
        height=max(420, min(900, 30 * len(base))),
        margin=dict(l=10, r=20, t=80, b=20)
    )
    st.plotly_chart(fig, use_container_width=True)
    if nota:
        _nota_leitura(f'Como ler: {titulo}', nota, tipo='info')


def _grafico_componentes(df: pd.DataFrame, titulo: str = 'Por que os municípios estão prioritários'):
    cols = ['score_vulnerabilidade_social', 'score_fragilidade_aps', 'score_acesso_territorial', 'score_ruralidade', 'score_saneamento']
    cols = [c for c in cols if c in df.columns]
    if not cols or df.empty:
        st.info('Sem componentes suficientes para o gráfico.')
        return
    top = df.head(15).copy()
    melt = top[['municipio'] + cols].melt(id_vars='municipio', var_name='componente', value_name='score')
    nomes = {
        'score_vulnerabilidade_social': 'Vulnerabilidade social',
        'score_fragilidade_aps': 'Capacidade APS',
        'score_acesso_territorial': 'Acesso territorial',
        'score_ruralidade': 'Ruralidade/territórios',
        'score_saneamento': 'Saneamento/escolaridade',
    }
    melt['componente'] = melt['componente'].map(nomes).fillna(melt['componente'])
    fig = px.bar(melt, x='municipio', y='score', color='componente', title=titulo)
    fig.update_layout(height=520, xaxis_tickangle=-35, margin=dict(l=10, r=20, t=60, b=80))
    st.plotly_chart(fig, use_container_width=True)
    _nota_leitura('Como ler este gráfico de composição', [
        'Cada componente é um score de 0 a 100 daquele eixo específico. Exemplo: 83,5 em Capacidade APS significa alta fragilidade relativa daquele eixo, não 83,5% de cobertura.',
        'Cada barra empilhada mostra quais componentes explicam a prioridade de cada município.',
        'Se a parte de capacidade APS for dominante, o problema tende a ser oferta/equipe/UBS.',
        'Se vulnerabilidade, saneamento ou ruralidade forem dominantes, a resposta precisa ser intersetorial e territorial.',
        'Este gráfico responde ao “por que está ruim?”, evitando olhar apenas o ranking final.'
    ])


def _matriz_quadrantes(base: pd.DataFrame):
    needed = ['score_vulnerabilidade_social', 'score_fragilidade_aps', 'municipio', 'score_prioridade']
    if any(c not in base.columns for c in needed):
        st.info('Base insuficiente para matriz de quadrantes.')
        return
    df = base.copy()
    hover_cols = [c for c in ['regiao_saude', 'classificacao_prioridade', 'principal_motor', 'por_que_ruim'] if c in df.columns]
    fig = px.scatter(
        df,
        x='score_fragilidade_aps',
        y='score_vulnerabilidade_social',
        size='populacao_ref' if 'populacao_ref' in df.columns else None,
        color='classificacao_prioridade' if 'classificacao_prioridade' in df.columns else 'score_prioridade',
        color_discrete_map=CORES_SITUACAO if 'classificacao_prioridade' in df.columns else None,
        category_orders={'classificacao_prioridade': ORDEM_SITUACAO},
        hover_name='municipio',
        hover_data=hover_cols,
        title='Matriz estadual — vulnerabilidade social x fragilidade da capacidade APS'
    )
    fig.add_hline(y=60, line_dash='dash', annotation_text='Alta vulnerabilidade social', annotation_position='top left')
    fig.add_vline(x=60, line_dash='dash', annotation_text='Alta fragilidade APS', annotation_position='top right')
    fig.add_annotation(x=78, y=78, text='Prioridade mais alta<br>(alta vulnerabilidade + baixa capacidade)', showarrow=False, bgcolor='#FEECEC')
    fig.add_annotation(x=20, y=78, text='Vulnerabilidade alta<br>com capacidade relativamente melhor', showarrow=False, bgcolor='#FFF7E6')
    fig.add_annotation(x=78, y=20, text='Capacidade APS frágil<br>mesmo com menor vulnerabilidade', showarrow=False, bgcolor='#FFF7E6')
    fig.add_annotation(x=18, y=18, text='Situação relativamente mais favorável', showarrow=False, bgcolor='#ECFDF3')
    fig.update_layout(height=620, margin=dict(l=10, r=20, t=80, b=20), xaxis_title='Fragilidade da capacidade APS (0 a 100)', yaxis_title='Vulnerabilidade social (0 a 100)', legend_title='Classificação')
    st.plotly_chart(fig, use_container_width=True)
    _nota_leitura('Como ler a matriz de quadrantes', [
        'Eixo vertical: vulnerabilidade social. Quanto mais alto, maior o peso de pobreza, CadÚnico, Bolsa Família, saneamento e escolaridade baixa.',
        'Eixo horizontal: fragilidade da capacidade APS. Quanto mais à direita, maior o indício de pressão sobre equipes, UBS e cobertura.',
        'O quadrante superior direito é o mais preocupante porque junta alta vulnerabilidade com menor capacidade de resposta.',
        'O tamanho da bolha, quando disponível, representa população: bolha grande indica mais pessoas potencialmente afetadas; bolha pequena não elimina prioridade se o risco relativo for alto.'
    ], tipo='alerta')


# -----------------------------------------------------------------------------
# Páginas internas
# -----------------------------------------------------------------------------

def _painel_estrategico(base: pd.DataFrame, resumo: dict):
    st.markdown('## 📌 Estratégico — leitura de gestão')
    st.caption('Resposta rápida para: está bom ou ruim, onde está pior e por que isso deve entrar na agenda estadual.')
    _legenda_regua_score()

    _nota_leitura('Antes de ler este painel', [
        'Escopo: esta leitura é ESTADUAL, baseada nos 142 municípios disponíveis na base consolidada.',
        'Quando o termômetro mostrar 40,5/100, isso significa a MÉDIA estadual do score integrado — não é a nota de um município específico.',
        'Para ver o detalhe municipal, use o ranking abaixo e depois abra o Diagnóstico Municipal do município de interesse.'
    ], tipo='info')

    c1, c2 = st.columns([.95, 1.35])
    with c1:
        _termometro(resumo.get('score_medio', 0), 'Termômetro estadual da prioridade integrada')
    with c2:
        st.markdown('### Narrativa executiva')
        top_txt = ', '.join(resumo.get('top_municipios', []))
        st.markdown(f"""
        <div style="background:#F8FAFC;border:1px solid #CBD5E1;border-radius:16px;padding:1rem;line-height:1.55;">
        <b>Leitura central:</b> o laboratório analisou <b>{resumo.get('municipios', 0)}</b> municípios. Há <b>{resumo.get('criticos_pessimos', 0)}</b> em situação crítica/péssima e <b>{resumo.get('ruins', 0)}</b> em situação ruim pela régua integrada.<br><br>
        <b>Municípios que exigem primeira validação:</b> {top_txt}.<br><br>
        <b>Como usar:</b> este painel não decide sozinho; ele organiza a fila técnica para visita, pactuação regional, validação de rotas e eventual priorização de financiamento.
        </div>
        """, unsafe_allow_html=True)
        _nota_leitura('O que torna um município ruim/crítico', [
            'Não é um único número isolado. A prioridade aumenta quando vulnerabilidade social, baixa capacidade APS, distância, ruralidade e saneamento se somam.',
            'Um município pequeno pode ser crítico por vulnerabilidade e distância mesmo sem grande população absoluta.',
            'Um município grande pode ser prioritário por volume de pessoas vulneráveis e pressão assistencial mesmo com melhor estrutura relativa.',
            'Faixas da régua: Bom 0–19,9 | Regular 20–39,9 | Ruim 40–59,9 | Péssimo 60–79,9 | Crítico 80–100.'
        ], tipo='alerta')

    top = base.head(20).copy()
    st.markdown('### Ranking estadual preliminar com leitura decisória')
    _nota_leitura('Baseado em que este ranking é calculado?', _texto_ponderacao(), tipo='info')
    _grafico_barras(top, 'score_prioridade', 'municipio', 'Top 20 do ranking estadual — prioridade integrada APS', cor='classificacao_prioridade', nota=[
        'A barra mostra o score integrado de prioridade (0 a 100), calculado pela ponderação dos componentes listados acima.',
        'O ranking é ESTADUAL: ele compara os municípios entre si para indicar onde validar primeiro.',
        'Use o ranking como porta de entrada para entender onde validar primeiro.',
        'Município no topo não significa automaticamente construção; pode significar reorganização de equipe, transporte, ação rural ou busca ativa.'
    ])

    st.markdown('### Distribuição estadual por situação')
    if 'classificacao_prioridade' in base.columns:
        cont = base['classificacao_prioridade'].value_counts().reset_index()
        cont.columns = ['Situação', 'Municípios']
        cont['Percentual'] = (cont['Municípios'] / cont['Municípios'].sum() * 100).round(1)
        fig = px.bar(cont, x='Municípios', y='Situação', orientation='h', color='Situação', text='Percentual', title='Quantos municípios caíram em cada faixa da régua integrada', color_discrete_map=CORES_SITUACAO, category_orders={'Situação': ORDEM_SITUACAO})
        fig.update_traces(texttemplate='%{text:.1f}%')
        fig.update_layout(height=420, xaxis_title='Quantidade de municípios', yaxis_title='Faixa de situação', legend_title='Situação')
        st.plotly_chart(fig, use_container_width=True)
        _nota_leitura('Como ler a distribuição', [
            'A distribuição é ESTADUAL e baseada no score integrado final de cada município.',
            'Critérios da faixa: Bom 0–19,9 | Regular 20–39,9 | Ruim 40–59,9 | Péssimo 60–79,9 | Crítico 80–100.',
            'Se predomina Ruim/Péssimo/Crítico, o problema deixa de ser pontual e passa a exigir resposta coordenada do estado e das regiões.',
            'Se predomina Regular, ainda há janela de prevenção para evitar piora.'
        ], tipo='info')


def _painel_tatico(base: pd.DataFrame):
    st.markdown('## 🧩 Tático — por que está ruim?')
    st.caption('Aqui o sistema deixa de mostrar dado solto e explica quais componentes estão puxando a prioridade.')
    _nota_leitura('Ranking mestre x leituras temáticas', [
        'O Ranking Mestre do sistema é sempre o Score Integrado APS.',
        'Matrizes e gráficos por componente podem ter ordem diferente porque respondem outra pergunta: vulnerabilidade, capacidade, distância, ruralidade ou saneamento.',
        'Quando um município muda de posição em um gráfico temático, isso não é contradição: significa que ele se destaca naquele eixo específico.'
    ], tipo='alerta')

    _grafico_componentes(base)
    _matriz_quadrantes(base)

    st.markdown('### Motores predominantes')
    if 'principal_motor' in base.columns:
        motores = base['principal_motor'].value_counts().reset_index()
        motores.columns = ['Motor predominante', 'Municípios']
        motores['Percentual'] = (motores['Municípios'] / motores['Municípios'].sum() * 100).round(1)
        fig = px.bar(motores, x='Municípios', y='Motor predominante', orientation='h', text='Percentual', title='Qual tipo de problema aparece mais')
        fig.update_traces(texttemplate='%{text:.1f}%')
        fig.update_layout(yaxis={'categoryorder': 'total ascending'}, height=420, xaxis_title='Quantidade de municípios')
        st.plotly_chart(fig, use_container_width=True)
        _nota_leitura('Como usar os motores predominantes', [
            'O motor predominante indica a natureza principal do problema em cada município.',
            'Capacidade APS sugere revisar equipes, CNES, UBS e cobertura.',
            'Vulnerabilidade/saneamento exige busca ativa e articulação com assistência social, educação, vigilância e saneamento.',
            'Acesso/ruralidade exige olhar territorial: transporte sanitário, unidade móvel, UBS satélite, equipe itinerante ou ponto de apoio.'
        ], tipo='alerta')

    st.markdown('### Tabela colorida — leitura técnica municipal')
    cols = [c for c in ['municipio', 'regiao_saude', 'classificacao_prioridade', 'score_prioridade', 'principal_motor', 'por_que_ruim', 'acao_recomendada_curta', 'decisao_sugerida'] if c in base.columns]
    _tabela_colorida(base[cols].head(80), altura=520)
    _nota_leitura('Como ler esta tabela', [
        'A tabela já traz a resposta “por que está ruim?” em linguagem técnica curta.',
        'Use a coluna Motor principal para separar problemas estruturais, sociais, territoriais e sanitários.',
        'Use a ação curta para reunião de encaminhamento; use a decisão sugerida para compor minuta de despacho ou plano de ação.'
    ])


def _laboratorio_insights(base: pd.DataFrame):
    st.markdown('## 🧠 Laboratório de insights cruzados')
    st.caption('Motor interpretativo: cruza indicadores e transforma dados em leitura técnica, consequência provável e ação sugerida.')

    municipios = ['Todos'] + sorted(base['municipio'].dropna().astype(str).unique().tolist()) if 'municipio' in base.columns else ['Todos']
    col1, col2 = st.columns([1, 1])
    with col1:
        municipio = st.selectbox('Município', municipios, key='lab_insight_municipio')
    with col2:
        limite = st.slider('Quantidade de insights', 5, 30, 12)

    insights = gerar_insights_cruzados(base, municipio=None if municipio == 'Todos' else municipio, limite=limite)
    if not insights:
        st.info('Sem insights disponíveis para a seleção.')
        return

    for i, item in enumerate(insights, 1):
        tipo = _tipo_por_classe(item.get('Situação'))
        st.markdown(f"""
        <div style="background:#FFFFFF;border:1px solid #CBD5E1;border-left:7px solid {'#EF4444' if tipo=='critico' else '#F59E0B' if tipo=='alerta' else '#2F80ED'};border-radius:16px;padding:1rem;margin:.75rem 0;box-shadow:0 8px 20px rgba(15,23,42,.05);">
            <div style="font-size:.9rem;font-weight:900;color:#1E276F;">Insight {i} — {item.get('Município')} | Situação: {item.get('Situação')}</div>
            <div style="margin-top:.45rem;"><b>Motor principal:</b> {item.get('Motor principal')}</div>
            <div style="margin-top:.35rem;"><b>Por que:</b> {item.get('Por que está assim')}</div>
            <div style="margin-top:.35rem;"><b>Consequência provável:</b> {item.get('Consequência provável')}</div>
            <div style="margin-top:.35rem;"><b>Ação sugerida:</b> {item.get('Ação sugerida')}</div>
        </div>
        """, unsafe_allow_html=True)

    _nota_leitura('Como usar o laboratório de insights', [
        'Cada insight combina dados sociais, territoriais, sanitários e de capacidade APS quando disponíveis.',
        'A leitura evita conclusões soltas: o sistema diz o problema, a evidência, a consequência e a ação sugerida.',
        'A regra não substitui análise humana; ela acelera triagem, relatório, despacho e preparação de reunião técnica.'
    ], tipo='ok')


def _painel_tendencias(base: pd.DataFrame):
    st.markdown('## 📈 Séries históricas e tendências')
    st.caption('Esta aba verifica se existem tabelas com ano/competência no banco e prepara leitura de melhora, estabilidade ou piora.')

    status = detectar_series_historicas()
    encontrados = pd.DataFrame(status.get('tabelas_encontradas', []))
    if not encontrados.empty:
        st.markdown('### Tabelas históricas detectadas')
        _tabela_colorida(encontrados, altura=220)
    else:
        st.warning('Ainda não foram detectadas tabelas históricas úteis com coluna de ano/competência.')

    tendencias = montar_tendencias_municipais(base)
    if tendencias.empty:
        _nota_leitura('Por que ainda não aparece série histórica forte?', [
            'O painel só mostra tendência quando encontra tabela com município e ano/competência.',
            'Se a base atual estiver consolidada apenas no ano mais recente, o sistema não deve inventar tendência.',
            'O próximo avanço é organizar uma tabela histórica municipal padrão com ano, indicador, valor e fonte.',
            'Quando essa tabela existir, o laboratório poderá mostrar setas: melhorando, estável ou piorando.'
        ], tipo='alerta')
        modelo = pd.DataFrame(columns=['municipio', 'regiao_saude', 'ano', 'indicador', 'valor', 'fonte'])
        _download_csv(modelo, 'modelo_serie_historica_indicadores_aps.csv', 'Baixar modelo de série histórica')
        return

    st.markdown('### Tendências detectadas automaticamente')
    _tabela_colorida(tendencias.head(200), altura=520)
    _download_csv(tendencias, 'tendencias_detectadas_aps.csv', 'Baixar tendências detectadas')
    _nota_leitura('Como interpretar tendências', [
        'A tendência mostra se um indicador aumentou, reduziu ou ficou estável entre o primeiro e o último ano disponível.',
        'Nem todo aumento é ruim: nascidos vivos, matrículas ou registros podem ter interpretações diferentes conforme o indicador.',
        'Use tendência como sinal de investigação; a leitura final depende do tipo do indicador e da série histórica completa.'
    ])


def _painel_operacional(base: pd.DataFrame):
    st.markdown('## 🛠️ Operacional — carteira de ação')
    st.caption('Transforma achados em tarefas: validar, reorganizar, ampliar, pactuar, acompanhar.')

    filtro_classe = st.multiselect('Filtrar situação', ['Crítico', 'Péssimo', 'Ruim', 'Regular', 'Bom'], default=['Crítico', 'Péssimo', 'Ruim'])
    df = base[base['classificacao_prioridade'].isin(filtro_classe)].copy() if filtro_classe else base.copy()
    df['0–30 dias'] = 'Validar CNES/INE, UBS, rotas reais, áreas descobertas, coordenadas e dados sociais.'
    df['31–60 dias'] = df['decisao_sugerida']
    df['61–90 dias'] = 'Consolidar proposta regional: expansão/reorganização APS, transporte sanitário, equipe itinerante ou ação intersetorial.'
    cols = [c for c in ['municipio', 'regiao_saude', 'classificacao_prioridade', 'score_prioridade', 'principal_motor', '0–30 dias', '31–60 dias', '61–90 dias', 'pendencia_hospitalar'] if c in df.columns]
    _tabela_colorida(df[cols].head(140), altura=560)
    _nota_leitura('Como ler a carteira operacional', [
        'A carteira organiza o trabalho em três ciclos: validação, intervenção e consolidação regional.',
        'A primeira etapa evita erro de decisão: confirmar dados, rotas, equipe, UBS e coordenadas.',
        'A segunda etapa define o tipo de resposta: equipe, UBS, unidade móvel, transporte, busca ativa ou intersetorialidade.',
        'A terceira etapa transforma a análise em proposta para Plano Diretor, pactuação regional ou financiamento.'
    ], tipo='alerta')
    _download_csv(df[cols], 'carteira_operacional_laboratorio_aps.csv', 'Baixar carteira operacional')


def _painel_relatorios(base: pd.DataFrame):
    st.markdown('## 📄 Relatórios inteligentes')
    sub1, sub2, sub3 = st.tabs(['Municipal', 'Regional', 'Base consolidada'])
    with sub1:
        municipios = sorted(base['municipio'].dropna().astype(str).unique()) if 'municipio' in base.columns else []
        if not municipios:
            st.warning('Não há municípios disponíveis para relatório.')
        else:
            municipio = st.selectbox('Município para relatório', municipios, key='relatorio_municipio_v29')
            texto = gerar_relatorio_municipal_texto(base, municipio)
            st.text_area('Relatório municipal copiável', texto, height=470, key='texto_relatorio_municipal_v29')
            st.download_button('Baixar relatório municipal TXT', texto.encode('utf-8'), file_name=f'relatorio_inteligente_{municipio}.txt', mime='text/plain', use_container_width=True)
            _nota_leitura('Como usar o relatório municipal', [
                'O relatório já organiza: situação geral, por que está ruim, consequência provável, ação sugerida e validações necessárias.',
                'É texto-base para reunião, despacho ou nota técnica; revise antes de enviar oficialmente.',
                'As conclusões dependem da qualidade das bases carregadas e da validação local.'
            ])
    with sub2:
        regioes = sorted(base['regiao_saude'].dropna().astype(str).unique()) if 'regiao_saude' in base.columns else []
        regiao = st.selectbox('Região de Saúde', ['Todas'] + regioes, key='relatorio_regional_v29')
        texto = gerar_relatorio_regional_texto(base, regiao)
        st.text_area('Relatório regional copiável', texto, height=380, key='texto_relatorio_regional_v29')
        st.download_button('Baixar relatório regional TXT', texto.encode('utf-8'), file_name=f'relatorio_regional_inteligente_{regiao}.txt', mime='text/plain', use_container_width=True)
    with sub3:
        cols = [c for c in ['municipio', 'regiao_saude', 'populacao_ref', 'classificacao_prioridade', 'score_prioridade', 'principal_motor', 'por_que_ruim', 'acao_recomendada_curta', 'decisao_sugerida', 'score_vulnerabilidade_social', 'score_fragilidade_aps', 'score_acesso_territorial', 'score_ruralidade', 'score_saneamento', 'pendencia_hospitalar'] if c in base.columns]
        _tabela_colorida(base[cols].head(142), altura=560)
        _download_csv(base[cols], 'base_laboratorio_inteligencia_aps.csv', 'Baixar base do laboratório')


def _painel_metodologia():
    st.markdown('## ℹ️ Critérios, parâmetros e cautelas')
    st.caption('A régua é preliminar, técnica e ajustável. Ela serve para priorização e investigação, não para decisão automática.')
    ref = referencias_tecnicas()
    _tabela_colorida(ref, altura=360)
    _nota_leitura('Princípio metodológico', [
        'O sistema deve responder: está ruim, por que está ruim, qual evidência mostra isso e qual ação é mais provável.',
        'Scores são relativos à base estadual disponível e podem mudar quando novas bases forem carregadas.',
        'Dado ausente não deve ser tratado como bom; deve aparecer como pendência técnica.',
        'Distância em linha reta não substitui rota real, tempo de deslocamento, sazonalidade, transporte disponível ou pactuação regional.',
        'Hospital/retaguarda só entra como camada oficial após validação de coordenadas.'
    ], tipo='alerta')


# -----------------------------------------------------------------------------
# Render principal
# -----------------------------------------------------------------------------

def render():
    st.title('🧪 Laboratório Digital de Inteligência APS')
    st.success('VERSÃO V31 carregada — dashboards explicativos, motor de insights, leitura por causa/evidência/consequência/ação e sem termos antigos de monitoramento.')
    st.caption('Dashboard narrativo, explicativo, visual e decisório para cruzar dados, gerar insights, orientar priorização e apoiar relatórios do Plano Diretor da Atenção à Saúde.')

    base = montar_base_dashboard_integrado()
    if base.empty:
        st.warning('Não foi possível montar a base integrada. Verifique se o banco consolidado está disponível e se as principais tabelas já foram geradas.')
        return

    resumo = resumo_executivo(base)

    st.markdown('### Painel de controle')
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        _info_card('Municípios analisados', _fmt_int(resumo.get('municipios', 0)), 'Total de municípios com leitura integrada no laboratório.', 'info')
    with c2:
        _info_card('Crítico/Péssimo', _fmt_int(resumo.get('criticos_pessimos', 0)), 'Municípios que exigem primeira validação técnica e possível resposta de gestão.', 'critico')
    with c3:
        _info_card('Score médio', _fmt_float(resumo.get('score_medio', 0), 1), 'Média da régua integrada: quanto maior, maior alerta estadual.', 'alerta')
    with c4:
        _info_card('Hospitais validados', _fmt_int(resumo.get('hospitais_validados', 0)), 'Registros hospitalares com coordenada validada/habilitada para mapa.', 'ok')

    st.markdown(
        """
        <div style="background:#EAF2FF;border-left:5px solid #2F80ED;padding:1rem;border-radius:14px;margin:.6rem 0 1rem 0;color:#1E276F;">
        <b>Frase orientadora da gestão:</b><br>
        “Quero ver visualmente onde estão os vazios assistenciais e quem está desassistido.”<br>
        O laboratório organiza os dados em uma sequência única: <b>situação → causa → evidência → consequência → ação</b>.
        </div>
        """,
        unsafe_allow_html=True,
    )

    tab_est, tab_tat, tab_lab, tab_trend, tab_op, tab_rel, tab_met = st.tabs([
        '📌 Estratégico', '🧩 Tático', '🧠 Insights', '📈 Tendências', '🛠️ Operacional', '📄 Relatórios', 'ℹ️ Critérios'
    ])

    with tab_est:
        _painel_estrategico(base, resumo)
    with tab_tat:
        _painel_tatico(base)
    with tab_lab:
        _laboratorio_insights(base)
    with tab_trend:
        _painel_tendencias(base)
    with tab_op:
        _painel_operacional(base)
    with tab_rel:
        _painel_relatorios(base)
    with tab_met:
        _painel_metodologia()
