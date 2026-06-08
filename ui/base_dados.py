import re
from datetime import datetime
import pandas as pd
import streamlit as st
from components.ui_elements import render_html_table
from database.queries import read_table, insert_importacao
from database.connection import db_session
from services.upload_service import ler_arquivo_upload, salvar_upload, padronizar_dataframe
from services.importacao_service import importar_dataframe_estruturado
from services.api_cnes_profissionais import carregar_cnes_equipes_ine_arquivo
from config.settings import UPLOADS_DIR
from services.consolidacao_service import atualizar_base_municipal
from services.auditoria_leitos_service import montar_auditoria_leitos
from services.indicadores_derivados_service import montar_indicadores_derivados, ranking_prioridades_derivadas, resumo_indicadores_derivados
from services.governanca_base_service import montar_governanca_base
from services.auditoria_inep_service import montar_visao_inep
from services.auditoria_ibge_censo_service import montar_visao_ibge_censo2022
from services.auditoria_regionalizacao_service import montar_auditoria_regionalizacao_ms
from services.georreferenciamento_service import gerar_georreferencia_municipal_mt, importar_georreferencia_municipal, qualidade_georreferencia
from services.api_registry import listar_catalogo, legacy_status, testar_api, carregar_api, listar_blocos, carregar_bloco
from services.legacy_analyzer import inventariar_arquivos_legacy, inventariar_funcoes_legacy, resumo_migracao_legacy, buscar_funcoes_legacy
from services.catalogo_bases_publicas_service import (
    carregar_catalogo_bases_publicas,
    salvar_catalogo_padrao,
    carregar_bases_publicas_importadas,
    matriz_priorizacao_importacao,
    gerar_modelo_registro_fonte_publica,
    registrar_base_publica_importada,
)
from services.socioeducacional_service import (
    importar_arquivo_socioeducacional,
    carregar_socioeducacional_consolidado,
    salvar_consolidado_municipal,
    existem_bases_socioeducacionais_importadas,
)
from services.bases_publicas_importador_service import (
    preparar_base_publica,
    importar_base_publica_universal,
    listar_bases_publicas_salvas,
    consolidar_resumo_bases_publicas,
    sugerir_tabela_destino,
)
from services.bases_publicas_analise_service import (
    inventariar_indicadores_bases_publicas,
    consolidar_bases_publicas_municipal,
    carregar_consolidado_bases_publicas,
    carregar_metadados_indicadores_publicos,
    resumo_categorias_bases_publicas,
    matriz_disponibilidade_tematica_municipal,
    resumo_disponibilidade_tematica,
    lacunas_bases_publicas_por_municipio,
    carregar_relatorio_consolidacao_bases_publicas,
)
from services.ibge_censo2022_setores_service import roteiro_ibge_censo2022_setores
from services.ibge_setores_zip_service import diagnosticar_pacote_ibge_setores, importar_pacote_ibge_setores_mt, diagnosticar_pacote_ibge_setores_local, importar_pacote_ibge_setores_mt_local
from services.ibge_dicionario_variaveis_service import carregar_dicionario_ibge, gerar_dicionario_automatico_ibge, importar_dicionario_ibge, resumo_dicionario_ibge, criar_modelo_dicionario_ibge, importar_dicionario_oficial_ibge_url, importar_dicionario_oficial_ibge_flexivel, diagnosticar_dicionario_oficial_ibge, URL_DICIONARIO_OFICIAL_IBGE_20250417
from services.ibge_curadoria_indicadores_service import carregar_curadoria_ibge, gerar_curadoria_automatica_ibge, importar_curadoria_ibge, resumo_curadoria_ibge
from services.inep_censo_escolar_service import diagnosticar_inep_censo_escolar_local, importar_inep_censo_escolar_local, carregar_inep_municipal, relatorio_importacao_inep
from services.sinasc_service import diagnosticar_sinasc_local, importar_sinasc_local, carregar_sinasc_municipal, relatorio_importacao_sinasc, resumo_validacao_sinasc
from services.datasus_dbc_service import diagnosticar_dbc, converter_dbc_para_csv, comandos_instalacao_dbc
from services.sim_service import diagnosticar_sim_local, importar_sim_local, carregar_sim_municipal, relatorio_importacao_sim, resumo_validacao_sim
from services.sinan_service import diagnosticar_sinan_local, importar_sinan_local, carregar_sinan_municipal, relatorio_importacao_sinan, resumo_validacao_sinan, carregar_sinan_municipal_gerencial
from services.mds_visdata_service import diagnosticar_arquivo_mds, diagnosticar_pasta_mds_local, importar_mds_visdata, importar_mds_pasta_local, carregar_mds_municipal, resumo_validacao_mds, INDICADORES_MDS
from services.auditoria_service import carregar_auditoria, valores_distintos, resumo_auditoria, registrar_evento

from services.importadores_plano_diretor_service import (
    status_bases_plano_diretor,
    consolidar_inep_existente_para_socio,
    consolidar_ibge_e_mds_para_socio_indicadores,
    gerar_consolidado_socioeducacional_final,
    importar_hospitais_retaguarda_ms,
    importar_inep_microdados_oficial,
    geocodificar_hospitais_retaguarda_por_endereco_api,
    preparar_cadastro_manual_hospitais_retaguarda,
    importar_cadastro_manual_hospitais_df,
    ativar_geo_hospitais_validados,
    resumo_cadastro_manual_hospitais,
)
TIPOS_BASE = ['estabelecimentos', 'estabelecimentos_gerais', 'equipes', 'profissionais', 'populacao', 'vulnerabilidade', 'indicadores']


def _download_csv_base(df: pd.DataFrame, nome_arquivo: str, label: str):
    if df is None or df.empty:
        st.caption("Sem dados para download.")
        return
    st.download_button(
        label,
        data=df.to_csv(index=False).encode("utf-8-sig"),
        file_name=nome_arquivo,
        mime="text/csv",
        use_container_width=True,
        key=f"dl_{nome_arquivo}",
    )


def _render_status_legacy():
    status = legacy_status()
    if status['connectors_exists']:
        st.success('Arquivo principal de APIs legado encontrado em legacy/conectores_apis_ubs_antigo.py.')
    else:
        st.warning('Arquivo principal de APIs legado ainda não encontrado. Copie o arquivo antigo para legacy/conectores_apis_ubs_antigo.py.')
    df_status = pd.DataFrame(status['arquivos'])
    render_html_table(df_status)

def _render_catalogo_apis():
    st.markdown('### Catálogo de APIs migráveis')
    st.caption('Esta tela não recria as APIs do zero. Ela lê o arquivo legado colocado em `legacy/`, identifica as funções disponíveis e permite testar/carregar as bases uma a uma, com rastreabilidade.')
    _render_status_legacy()
    catalogo = listar_catalogo()
    col_filtro1, col_filtro2 = st.columns([1, 1])
    with col_filtro1:
        grupos = ['Todos'] + sorted(catalogo['grupo'].dropna().unique().tolist())
        grupo = st.selectbox('Filtrar por grupo', grupos)
    with col_filtro2:
        somente_encontradas = st.checkbox('Mostrar apenas funções encontradas no legacy', value=False)
    visao = catalogo.copy()
    if grupo != 'Todos':
        visao = visao[visao['grupo'] == grupo]
    if somente_encontradas:
        visao = visao[visao['função_encontrada']]
    render_html_table(visao)
    if catalogo.empty:
        st.info('Nenhuma API cadastrada no catálogo.')
        return
    opcoes = [f'{row.codigo} — {row.grupo} — {row.base}' for row in catalogo.itertuples()]
    selecionada = st.selectbox('Selecionar API para teste/carga', opcoes)
    codigo = selecionada.split(' — ')[0]
    item = catalogo[catalogo['codigo'] == codigo].iloc[0].to_dict()
    st.markdown(f"**Base selecionada:** {item['base']}")
    st.caption(f"Executor: `{item.get('executor', '—')}` | Nativa: `{item.get('função_nativa', '—')}` | Legacy: `{item.get('função_carregar_legacy', '—')}` | Tipo de base: `{item['tipo_base']}`")
    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        testar = st.button('Testar API', use_container_width=True)
    with col2:
        carregar = st.button('Carregar e salvar CSV bruto', type='primary', use_container_width=True)
    with col3:
        importar = st.button('Carregar e importar para banco', use_container_width=True)
    if testar:
        with st.spinner('Testando API...'):
            resultado = testar_api(codigo)
        if resultado.get('ok'):
            st.success('Teste concluído.')
        else:
            st.error('Falha no teste da API.')
        st.json({k: v for k, v in resultado.items() if k != 'df'})
    if carregar or importar:
        with st.spinner('Carregando base pela função legada...'):
            resultado = carregar_api(codigo, importar_para_banco=bool(importar))
        if resultado.get('ok'):
            st.success(f"Base carregada: {resultado['linhas']} linhas e {resultado['colunas']} colunas. Executor usado: {resultado.get('executor_usado', '—')}.")
            st.caption(f"CSV bruto salvo em: {resultado['caminho']}")
            if resultado.get('importacao_estruturada'):
                info = resultado['importacao_estruturada']
                st.info(f"Importado para `{info['tabela']}`: {info['linhas']} linhas.")
            df = resultado.get('df')
            if isinstance(df, pd.DataFrame) and (not df.empty):
                render_html_table(df.head(100))
        else:
            st.error('Não foi possível carregar a API.')
            st.code(resultado.get('erro', 'Erro não informado.'))

def _render_blocos_migracao():
    st.markdown('### Blocos de migração de APIs')
    st.caption('Esta área executa grupos de APIs em sequência. A v04 inaugura o primeiro bloco real: IBGE/SIDRA territorial, usando conectores nativos na nova arquitetura e fallback para o legacy quando necessário.')
    blocos = listar_blocos()
    if blocos.empty:
        st.info('Nenhum bloco cadastrado.')
        return
    render_html_table(blocos)
    opcoes = [f'{row.codigo} — {row.bloco}' for row in blocos.itertuples()]
    selecionado = st.selectbox('Selecionar bloco para execução', opcoes)
    codigo_bloco = selecionado.split(' — ')[0]
    importar = st.checkbox('Após carregar, tentar importar automaticamente para o banco estruturado', value=False, help='Para bases de referência, o sistema salva apenas CSV bruto e registro de importação. Para população, vulnerabilidade e indicadores, tenta gravar em indicadores_municipais.')
    col1, col2 = st.columns([1, 2])
    with col1:
        executar = st.button('Executar bloco selecionado', type='primary', use_container_width=True)
    with col2:
        st.info('Recomendação: comece pelo bloco IBGE/SIDRA — Base territorial mínima.')
    if executar:
        with st.spinner('Executando bloco de APIs...'):
            resultado = carregar_bloco(codigo_bloco, importar_para_banco=importar)
        if resultado.get('ok'):
            st.success(f"Bloco concluído: {resultado['sucesso']} de {resultado['total']} APIs carregadas.")
        else:
            st.warning(f"Bloco concluído com falhas: {resultado['sucesso']} sucesso(s) e {resultado['falhas']} falha(s).")
        st.caption(f"Duração: {resultado.get('duracao_segundos', 0)} segundos.")
        render_html_table(pd.DataFrame(resultado.get('resultados', [])))

def _render_inventario_legacy():
    st.markdown('### Inventário técnico do sistema antigo')
    st.caption('Esta aba lê os arquivos colocados em `legacy/` sem executar o código antigo. Ela serve para enxergar o que já existe, quais funções já estão mapeadas no catálogo e quais funções ainda podem ser reaproveitadas nas próximas migrações.')
    resumo = resumo_migracao_legacy()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Arquivos legacy', f"{resumo['arquivos_encontrados']}/{resumo['arquivos_esperados']}")
    c2.metric('Funções encontradas', resumo['total_funcoes'])
    c3.metric('Carga/teste', resumo['funcoes_carga_teste'])
    c4.metric('Regras/classificações', resumo['funcoes_regras'])
    st.info(f"Funções já referenciadas no Catálogo de APIs: {resumo['funcoes_catalogo_encontradas']} de {resumo['funcoes_catalogo_total']}.")
    tab_a, tab_b, tab_c = st.tabs(['Arquivos', 'Funções', 'Busca'])
    with tab_a:
        arquivos = inventariar_arquivos_legacy()
        render_html_table(arquivos)
    with tab_b:
        funcoes = inventariar_funcoes_legacy()
        if funcoes.empty:
            st.warning('Nenhuma função encontrada. Confirme se os arquivos `.py` foram copiados para a pasta `legacy/`.')
            return
        col1, col2, col3 = st.columns([1, 1, 1])
        with col1:
            grupos = ['Todos'] + sorted(funcoes['grupo_sugerido'].dropna().unique().tolist())
            grupo = st.selectbox('Grupo sugerido', grupos, key='legacy_grupo')
        with col2:
            tipos = ['Todos'] + sorted(funcoes['tipo_sugerido'].dropna().unique().tolist())
            tipo = st.selectbox('Tipo sugerido', tipos, key='legacy_tipo')
        with col3:
            apenas_catalogo = st.checkbox('Somente funções já no catálogo', value=False)
        visao = funcoes.copy()
        if grupo != 'Todos':
            visao = visao[visao['grupo_sugerido'] == grupo]
        if tipo != 'Todos':
            visao = visao[visao['tipo_sugerido'] == tipo]
        if apenas_catalogo:
            visao = visao[visao['chamada_por_catalogo']]
        render_html_table(visao)
        csv = visao.to_csv(index=False).encode('utf-8-sig')
        st.download_button('Baixar inventário filtrado em CSV', data=csv, file_name='inventario_funcoes_legacy.csv', mime='text/csv', use_container_width=True)
    with tab_c:
        termo = st.text_input('Buscar função, fonte ou palavra-chave', placeholder='Ex.: cnes, sidra, bolsa, pni, render, classificar')
        if termo:
            resultado = buscar_funcoes_legacy(termo)
            st.caption(f'Resultado da busca: {len(resultado)} função(ões).')
            render_html_table(resultado)

def _colunas_existentes(df: pd.DataFrame, colunas: list[str]) -> list[str]:
    return [c for c in colunas if c in df.columns]

def _coluna_tem_dado(df: pd.DataFrame, coluna: str) -> bool:
    if df is None or df.empty or coluna not in df.columns:
        return False
    serie = df[coluna]
    ausente = serie.isna() | serie.astype(str).str.strip().isin(['', 'None', 'nan', '<NA>'])
    return bool((~ausente).any())

def _colunas_com_dados(df: pd.DataFrame, colunas: list[str], obrigatorias: list[str] | None=None) -> list[str]:
    obrigatorias = obrigatorias or []
    saida: list[str] = []
    for col in colunas:
        if col not in df.columns:
            continue
        if col in obrigatorias or _coluna_tem_dado(df, col):
            saida.append(col)
    return saida

def _base_sem_colunas_vazias(df: pd.DataFrame, obrigatorias: list[str] | None=None) -> pd.DataFrame:
    obrigatorias = obrigatorias or []
    cols = _colunas_com_dados(df, df.columns.tolist(), obrigatorias=obrigatorias)
    return df[cols].copy() if cols else df.copy()

def _serie_numerica(df: pd.DataFrame, coluna: str):
    if coluna not in df.columns:
        return pd.Series(dtype='float64')
    return pd.to_numeric(df[coluna], errors='coerce')

def _formatar_inteiro(valor) -> str:
    try:
        if pd.isna(valor):
            return '—'
        return f'{int(round(float(valor))):,}'.replace(',', '.')
    except Exception:
        return '—'

def _formatar_moeda(valor) -> str:
    try:
        if pd.isna(valor):
            return '—'
        txt = f'R$ {float(valor):,.2f}'
        return txt.replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return '—'

def _mapear_pendencias_consolidacao(base: pd.DataFrame) -> pd.DataFrame:
    campos = [('populacao', 'População'), ('area_km2', 'Área territorial'), ('densidade_hab_km2', 'Densidade demográfica'), ('latitude', 'Latitude'), ('longitude', 'Longitude'), ('pib_municipal_precos_correntes', 'PIB municipal'), ('total_ubs', 'UBS/CNES'), ('total_equipes_aps', 'Equipes APS/INE'), ('total_profissionais_aps', 'Profissionais vinculados ao INE'), ('nascidos_vivos', 'SINASC'), ('obitos', 'SIM')]
    total = len(base) if base is not None else 0
    linhas = []
    for coluna, rotulo in campos:
        if coluna not in base.columns:
            linhas.append({'campo': rotulo, 'coluna': coluna, 'preenchidos': 0, 'pendentes': total, 'cobertura_%': 0.0, 'status': 'Ausente'})
            continue
        serie = base[coluna]
        pendente = serie.isna() | serie.astype(str).str.strip().isin(['', 'None', 'nan', '<NA>'])
        preenchidos = int((~pendente).sum())
        pendentes = int(pendente.sum())
        cobertura = round(preenchidos / total * 100, 1) if total else 0.0
        if cobertura >= 95:
            status = 'OK'
        elif cobertura > 0:
            status = 'Parcial'
        else:
            status = 'Pendente'
        linhas.append({'campo': rotulo, 'coluna': coluna, 'preenchidos': preenchidos, 'pendentes': pendentes, 'cobertura_%': cobertura, 'status': status})
    return pd.DataFrame(linhas)

def _render_alertas_qualidade(base: pd.DataFrame):
    pend = _mapear_pendencias_consolidacao(base)
    criticos = pend[pend['status'].isin(['Pendente', 'Ausente'])]
    if not criticos.empty:
        campos = ', '.join(criticos['campo'].head(5).tolist())
        st.warning(f'Há campos estruturantes pendentes ou parcialmente carregados. Principais pontos: {campos}. Camadas ainda não finalizadas não são exibidas nas visões analíticas.')
    return pend

def _render_consolidacao():
    st.markdown('A consolidação cruza municípios, UBS, equipes, profissionais e indicadores em uma base municipal única.')
    if st.button('Gerar/atualizar base municipal consolidada', type='primary', use_container_width=True):
        info = atualizar_base_municipal()
        st.success(f"Base consolidada atualizada: {info['municipios']} municípios em {info['atualizado_em']}.")
    base = read_table('base_municipal_consolidada')
    if base.empty:
        st.info('A base consolidada ainda está vazia. Execute os blocos de APIs e depois clique em gerar/atualizar.')
        return
    pop = _serie_numerica(base, 'populacao')
    sem_pop = int(pop.isna().sum()) if not pop.empty else 0
    total_pop = pop.sum(skipna=True) if not pop.empty else None
    colunas_socio_ativas = ['pib_municipal_precos_correntes', 'perfil_urbano_rural', 'taxa_alfabetizacao', 'renda_censo_2022', 'saneamento_censo_2022', 'populacao_indigena', 'populacao_quilombola']
    tem_socio = any((_coluna_tem_dado(base, c) for c in colunas_socio_ativas))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Municípios', len(base))
    c2.metric('População total', _formatar_inteiro(total_pop))
    c3.metric('Sem população', sem_pop)
    c4.metric('Camada socioeconômica', 'Ativa' if tem_socio else 'Pendente')
    st.caption('As visões mostram apenas camadas com dado aproveitável. Bases ainda pendentes ou carregadas somente para rastreabilidade não aparecem como informação final da Base Completa.')
    geo_status = qualidade_georreferencia()
    if geo_status.get('coordenadas', 0) < geo_status.get('municipios', 141):
        with st.expander('Camada territorial/georreferenciamento municipal', expanded=False):
            st.info('A v09 preenche latitude, longitude, área territorial aproximada e densidade demográfica dos municípios usando IBGE Localidades/Malhas. O processamento pode demorar alguns minutos na primeira execução.')
            gc1, gc2, gc3, gc4 = st.columns(4)
            gc1.metric('Municípios', geo_status.get('municipios', 0))
            gc2.metric('Com coordenadas', geo_status.get('coordenadas', 0))
            gc3.metric('Com área', geo_status.get('area', 0))
            gc4.metric('Cache local', 'Sim' if geo_status.get('cache_existe') else 'Não')
            forcar_geo = st.checkbox('Forçar novo download das malhas IBGE', value=False)
            if st.button('Atualizar georreferenciamento municipal', type='primary', use_container_width=True):
                with st.spinner('Atualizando georreferenciamento municipal. Aguarde; a primeira execução consulta as malhas dos 141 municípios...'):
                    df_geo = gerar_georreferencia_municipal_mt(forcar_download=bool(forcar_geo))
                    info_geo = importar_georreferencia_municipal(df_geo)
                st.success(f"Georreferenciamento atualizado: {info_geo['coordenadas_atualizadas']} coordenadas, {info_geo['areas_preenchidas']} áreas e {info_geo['indicadores_inseridos']} indicadores inseridos.")
                st.caption(f"Cache: {info_geo.get('cache')}")
                st.info('Agora clique novamente em Gerar/atualizar base municipal consolidada.')
    pendencias_df = _render_alertas_qualidade(base)
    col_f1, col_f2, col_f3 = st.columns([1.2, 1, 1])
    with col_f1:
        busca = st.text_input('Buscar município', placeholder='Ex.: Cuiabá, Sinop, Cáceres')
    with col_f2:
        regioes = ['Todas']
        if 'regiao_saude' in base.columns:
            regioes += sorted([r for r in base['regiao_saude'].dropna().astype(str).unique().tolist() if r.strip()])
        regiao = st.selectbox('Região de Saúde', regioes)
    with col_f3:
        modo = st.selectbox('Visão da tabela', ['Resumo executivo', 'Georreferenciamento', 'Socioeconômico', 'Educação/INEP', 'APS/CNES', 'Contexto CNES/DATASUS', 'Qualidade dos dados', 'Base completa'])
    visao = base.copy()
    if busca:
        if 'municipio' in visao.columns:
            visao = visao[visao['municipio'].astype(str).str.contains(busca, case=False, na=False)]
    if regiao != 'Todas' and 'regiao_saude' in visao.columns:
        visao = visao[visao['regiao_saude'].astype(str) == regiao]
    colunas_resumo = ['id', 'municipio', 'regiao_saude', 'populacao', 'area_km2', 'densidade_hab_km2', 'pib_municipal_precos_correntes', 'nivel_prioridade']
    colunas_socio = ['id', 'municipio', 'regiao_saude', 'populacao', 'pib_municipal_precos_correntes', 'pib_per_capita', 'perfil_urbano_rural', 'indicador_demografico', 'taxa_alfabetizacao', 'nivel_instrucao', 'renda_censo_2022', 'saneamento_censo_2022', 'populacao_indigena', 'populacao_quilombola', 'indice_vulnerabilidade']
    colunas_inep = ['id', 'municipio', 'regiao_saude', 'populacao', 'escolas_total', 'escolas_urbanas', 'escolas_rurais', 'escolas_indigenas', 'escolas_quilombolas', 'escolas_educacao_especial_aee', 'matriculas_total', 'matriculas_educacao_especial', 'nivel_prioridade']
    colunas_aps = ['id', 'municipio', 'regiao_saude', 'total_ubs', 'total_equipes_aps', 'total_profissionais_aps', 'total_equipes_70', 'total_equipes_71', 'total_equipes_72', 'total_equipes_73', 'total_equipes_74', 'total_equipes_76', 'latitude', 'longitude', 'nivel_prioridade']
    colunas_contexto = ['id', 'municipio', 'regiao_saude', 'populacao', 'total_ubs', 'total_leitos_sus', 'nascidos_vivos', 'obitos', 'obitos_infantis', 'nivel_prioridade']
    colunas_geo = ['id', 'municipio', 'regiao_saude', 'codigo_ibge', 'populacao', 'area_km2', 'densidade_hab_km2', 'latitude', 'longitude', 'nivel_prioridade']
    obrigatorias = ['id', 'codigo_ibge', 'municipio', 'regiao_saude', 'populacao']
    if modo == 'Resumo executivo':
        cols = _colunas_com_dados(visao, colunas_resumo, obrigatorias=['id', 'municipio', 'regiao_saude', 'populacao', 'nivel_prioridade'])
    elif modo == 'Georreferenciamento':
        cols = _colunas_com_dados(visao, colunas_geo, obrigatorias=['id', 'municipio', 'regiao_saude', 'codigo_ibge', 'populacao'])
    elif modo == 'Socioeconômico':
        cols = _colunas_com_dados(visao, colunas_socio, obrigatorias=['id', 'municipio', 'regiao_saude', 'populacao'])
    elif modo == 'Educação/INEP':
        cols = _colunas_com_dados(visao, colunas_inep, obrigatorias=['id', 'municipio', 'regiao_saude', 'populacao', 'nivel_prioridade'])
    elif modo == 'APS/CNES':
        cols = _colunas_com_dados(visao, colunas_aps, obrigatorias=['id', 'municipio', 'regiao_saude', 'nivel_prioridade'])
    elif modo == 'Contexto CNES/DATASUS':
        cols = _colunas_com_dados(visao, colunas_contexto, obrigatorias=['id', 'municipio', 'regiao_saude', 'populacao', 'nivel_prioridade'])
    elif modo == 'Qualidade dos dados':
        cols = []
    else:
        visao = _base_sem_colunas_vazias(visao, obrigatorias=obrigatorias + ['id', 'nivel_prioridade', 'observacao', 'atualizado_em'])
        cols = visao.columns.tolist()
    st.caption(f'Exibindo {len(visao)} município(s). Visão: {modo}.')
    if modo == 'Qualidade dos dados':
        render_html_table(pendencias_df)
        st.info('Esta visão acompanha apenas campos estruturantes ou camadas com dado final aproveitável. Registros de API carregados com valor nulo, erro temporário ou somente rastreabilidade permanecem no histórico bruto, mas não são exibidos como base analítica final.')
    else:
        render_html_table(visao[cols])
    csv = visao.to_csv(index=False).encode('utf-8-sig')
    st.download_button('Baixar base consolidada filtrada em CSV', data=csv, file_name='base_municipal_consolidada.csv', mime='text/csv', use_container_width=True)
    with st.expander('Ver diagnóstico técnico da consolidação'):
        st.write('Cobertura por campo:')
        render_html_table(pendencias_df)
        st.write('Colunas disponíveis na tabela física:')
        st.code('\n'.join(base.columns.astype(str).tolist()))
        st.write('Colunas exibidas na visão atual:')
        st.code('\n'.join(cols))
        if 'observacao' in base.columns:
            obs = base[['municipio', 'observacao']].copy() if 'municipio' in base.columns else base[['observacao']].copy()
            render_html_table(obs.head(50))

def _render_upload_manual_equipes_cnes():
    st.markdown('#### Carga manual — Equipes CNES/INE')
    st.info('Os links automáticos do CNES/CKAN para equipes podem retornar 403/404. Quando isso acontecer, baixe o arquivo oficial de equipes no CNES/DATASUS e envie aqui. O sistema aceita ZIP, TXT, CSV, XLSX ou XLS e importa os tipos 70, 71, 72, 73, 74 e 76.')
    col_a, col_b = st.columns([1, 1])
    with col_a:
        competencia_eq = st.text_input('Competência/referência do arquivo de equipes', placeholder='Ex.: 2026-12 ou 2026', key='competencia_equipes_cnes_manual')
    with col_b:
        st.caption('Destino interno do arquivo:')
        st.code('data/uploads/cnes_equipes/')
    arquivo_eq = st.file_uploader('Enviar arquivo oficial de equipes CNES/INE', type=['zip', 'txt', 'csv', 'xlsx', 'xls'], key='arquivo_equipes_cnes_manual')
    if arquivo_eq is not None:
        st.caption(f'Arquivo selecionado: {arquivo_eq.name}')
        if st.button('Salvar, ler e importar equipes CNES/INE', type='primary', use_container_width=True):
            pasta = UPLOADS_DIR / 'cnes_equipes'
            pasta.mkdir(parents=True, exist_ok=True)
            nome_seguro = re.sub('[^A-Za-z0-9_.-]+', '_', arquivo_eq.name)
            destino = pasta / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{nome_seguro}"
            destino.write_bytes(arquivo_eq.getbuffer())
            try:
                df_eq = carregar_cnes_equipes_ine_arquivo(destino)
                if df_eq.empty:
                    st.warning('O arquivo foi lido, mas nenhuma equipe dos tipos 70, 71, 72, 73, 74 ou 76 foi identificada.')
                    return
                importacao_id = insert_importacao(fonte_codigo='CNES_EQUIPE_UPLOAD_MANUAL', nome_arquivo=arquivo_eq.name, tipo_base='equipes', competencia=competencia_eq, linhas=len(df_eq), colunas=len(df_eq.columns), status='Arquivo manual lido', mensagem='Equipes CNES/INE lidas a partir de upload manual.', caminho_arquivo=str(destino), criado_em=datetime.now().isoformat(timespec='seconds'))
                resultado = importar_dataframe_estruturado(df_eq, 'equipes', importacao_id=importacao_id, fonte='CNES_EQUIPE_UPLOAD_MANUAL')
                st.success(f"Equipes importadas para {resultado['tabela']}: {resultado['linhas']} linhas.")
                st.caption('Prévia da base normalizada:')
                render_html_table(df_eq.head(50))
                st.info('Agora vá em Consolidação e clique em Gerar/atualizar base municipal consolidada.')
            except Exception as exc:
                st.error('Não foi possível importar o arquivo manual de equipes CNES/INE.')
                st.exception(exc)

def _formatar_decimal(valor, casas=2):
    try:
        if pd.isna(valor):
            return '—'
        return f'{float(valor):,.{casas}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return '—'

def _render_auditoria_leitos():
    st.markdown('### Auditoria da camada de leitos CNES')
    st.caption('Esta aba não cria novo indicador oficial. Ela verifica a consistência da camada de leitos atualmente importada, mostra a origem dos indicadores no banco e sinaliza municípios que precisam de conferência antes de usar leitos no dashboard.')
    auditoria = montar_auditoria_leitos()
    resumo = auditoria.get('resumo', {})
    por_municipio = auditoria.get('por_municipio', pd.DataFrame())
    indicadores = auditoria.get('indicadores_leitos', pd.DataFrame())
    agregados = auditoria.get('agregados_por_indicador', pd.DataFrame())
    alertas = auditoria.get('alertas', [])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Leitos consolidados', _formatar_inteiro(resumo.get('total_leitos_consolidado')))
    c2.metric('Municípios com leitos', _formatar_inteiro(resumo.get('municipios_com_leitos')))
    c3.metric('Municípios sem leitos', _formatar_inteiro(resumo.get('municipios_sem_leitos')))
    c4.metric('Leitos por 10 mil hab.', _formatar_decimal(resumo.get('leitos_por_10mil'), 2))
    if alertas:
        for alerta in alertas:
            st.warning(alerta)
    else:
        st.success('Nenhum alerta crítico detectado na auditoria interna da camada de leitos.')
    st.info('Interpretação provisória: `total_leitos_sus` deve permanecer como **camada em auditoria** até a fonte/critério serem confirmados. O campo pode representar leitos SUS agregados, leitos existentes ou outro recorte retornado pela rotina antiga.')
    tab_a, tab_b, tab_c, tab_d = st.tabs(['Por município', 'Indicadores encontrados', 'Agregação por indicador', 'Critérios de auditoria'])
    with tab_a:
        if por_municipio.empty:
            st.warning('A base consolidada ainda não possui coluna `total_leitos_sus` preenchida.')
        else:
            col1, col2, col3 = st.columns([1.2, 1, 1])
            with col1:
                busca = st.text_input('Buscar município', placeholder='Ex.: Cuiabá, Rondonópolis, Sinop', key='busca_leitos')
            with col2:
                somente_alertas = st.checkbox('Mostrar somente alertas', value=False)
            with col3:
                ordenar = st.selectbox('Ordenar por', ['leitos_desc', 'populacao_desc', 'leitos_por_10mil_desc', 'municipio'])
            visao = por_municipio.copy()
            if busca:
                visao = visao[visao['municipio'].astype(str).str.contains(busca, case=False, na=False)]
            if somente_alertas and 'status_auditoria' in visao.columns:
                visao = visao[visao['status_auditoria'].astype(str) != 'OK']
            if ordenar == 'leitos_desc' and 'total_leitos_sus' in visao.columns:
                visao = visao.sort_values('total_leitos_sus', ascending=False)
            elif ordenar == 'populacao_desc' and 'populacao' in visao.columns:
                visao = visao.sort_values('populacao', ascending=False)
            elif ordenar == 'leitos_por_10mil_desc' and 'leitos_por_10mil_hab' in visao.columns:
                visao = visao.sort_values('leitos_por_10mil_hab', ascending=False)
            else:
                visao = visao.sort_values('municipio')
            render_html_table(visao)
            st.download_button('Baixar auditoria municipal de leitos em CSV', data=visao.to_csv(index=False).encode('utf-8-sig'), file_name='auditoria_leitos_cnes_municipios.csv', mime='text/csv', use_container_width=True)
    with tab_b:
        if indicadores.empty:
            st.warning('Nenhum indicador com termo `leito` foi encontrado em `indicadores_municipais`.')
        else:
            st.caption('Registros brutos/longos relacionados a leitos encontrados na tabela `indicadores_municipais`.')
            render_html_table(indicadores)
            st.download_button('Baixar indicadores de leitos em CSV', data=indicadores.to_csv(index=False).encode('utf-8-sig'), file_name='indicadores_leitos_origem.csv', mime='text/csv', use_container_width=True)
    with tab_c:
        if agregados.empty:
            st.info('Sem indicadores de leitos para agregar.')
        else:
            st.caption('Esta tabela ajuda a identificar qual coluna/indicador está alimentando o total de leitos.')
            render_html_table(agregados)
    with tab_d:
        st.markdown('\n            **Critério atual da auditoria**\n\n            - A auditoria usa a coluna `total_leitos_sus` da base consolidada e todos os registros de `indicadores_municipais` cujo nome contenha `leito`.\n            - Zero pode ser real em municípios pequenos, mas municípios de maior porte com valor muito baixo são sinalizados para conferência.\n            - O sistema não assume, nesta etapa, que o campo representa leito hospitalar SUS oficial. A camada fica marcada como **em auditoria**.\n            - Antes de usar no Dashboard Executivo, precisamos confirmar se a fonte está trazendo leitos existentes, leitos SUS, leitos complementares ou outro recorte.\n            ')

def _render_indicadores_derivados():
    st.markdown('### Indicadores derivados APS')
    st.caption('Esta camada não adiciona uma nova API. Ela transforma as bases já carregadas em indicadores operacionais para análise territorial da APS. Os resultados são derivados da base consolidada e devem ser interpretados como apoio técnico, não como índice oficial de vulnerabilidade.')
    df = montar_indicadores_derivados()
    if df.empty:
        st.info('A base consolidada ainda está vazia. Gere a consolidação antes de calcular indicadores derivados.')
        return
    resumo = resumo_indicadores_derivados(df)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric('Municípios', _formatar_inteiro(resumo.get('municipios')))
    c2.metric('Sem equipe APS/INE', _formatar_inteiro(resumo.get('sem_equipe')))
    c3.metric('Sem UBS', _formatar_inteiro(resumo.get('sem_ubs')))
    c4.metric('Média pop./equipe', _formatar_decimal(resumo.get('pop_media_por_equipe'), 1))
    c5.metric('Mortalidade infantil', _formatar_decimal(resumo.get('mortalidade_infantil'), 2))
    st.info('Os indicadores usam denominadores protegidos: quando não há equipe, UBS, nascidos vivos ou população, o sistema mantém o indicador em branco em vez de forçar zero.')
    tab_a, tab_b, tab_c, tab_d = st.tabs(['Ranking de pressão APS', 'Indicadores por município', 'Regiões de Saúde', 'Critérios de cálculo'])
    with tab_a:
        st.markdown('#### Municípios com maior pressão assistencial derivada')
        ranking = ranking_prioridades_derivadas(df, limite=40)
        render_html_table(ranking)
        st.download_button('Baixar ranking em CSV', data=ranking.to_csv(index=False).encode('utf-8-sig'), file_name='ranking_pressao_aps.csv', mime='text/csv', use_container_width=True)
    with tab_b:
        col1, col2, col3 = st.columns([1.2, 1, 1])
        with col1:
            busca = st.text_input('Buscar município', placeholder='Ex.: Cuiabá, Sinop, Cáceres', key='busca_indicadores_derivados')
        with col2:
            regioes = ['Todas']
            if 'regiao_saude' in df.columns:
                regioes += sorted([r for r in df['regiao_saude'].dropna().astype(str).unique().tolist() if r.strip()])
            regiao = st.selectbox('Região de Saúde', regioes, key='regiao_indicadores_derivados')
        with col3:
            modo = st.selectbox('Foco', ['APS', 'Eventos vitais', 'Capacidade instalada', 'Base completa'], key='modo_indicadores_derivados')
        visao = df.copy()
        if busca and 'municipio' in visao.columns:
            visao = visao[visao['municipio'].astype(str).str.contains(busca, case=False, na=False)]
        if regiao != 'Todas' and 'regiao_saude' in visao.columns:
            visao = visao[visao['regiao_saude'].astype(str) == regiao]
        colunas_aps = ['municipio', 'regiao_saude', 'populacao', 'total_ubs', 'total_equipes_aps', 'total_profissionais_aps', 'pop_por_equipe_aps', 'pop_por_ubs', 'profissionais_por_equipe', 'equipes_aps_por_10mil_hab', 'ubs_por_10mil_hab', 'profissionais_por_10mil_hab', 'classificacao_pressao_aps']
        colunas_eventos = ['municipio', 'regiao_saude', 'populacao', 'nascidos_vivos', 'obitos', 'obitos_infantis', 'nascidos_vivos_por_1000_hab', 'obitos_por_1000_hab', 'mortalidade_infantil_por_1000_nv']
        colunas_capacidade = ['municipio', 'regiao_saude', 'populacao', 'total_ubs', 'total_leitos_sus', 'total_equipes_aps', 'ubs_por_10mil_hab', 'leitos_sus_por_10mil_hab', 'pop_por_ubs', 'sem_ubs', 'sem_equipe_aps']
        if modo == 'APS':
            cols = _colunas_existentes(visao, colunas_aps)
        elif modo == 'Eventos vitais':
            cols = _colunas_existentes(visao, colunas_eventos)
        elif modo == 'Capacidade instalada':
            cols = _colunas_existentes(visao, colunas_capacidade)
        else:
            cols = visao.columns.tolist()
        st.caption(f'Exibindo {len(visao)} município(s).')
        render_html_table(visao[cols])
        st.download_button('Baixar indicadores derivados filtrados em CSV', data=visao.to_csv(index=False).encode('utf-8-sig'), file_name='indicadores_derivados_aps.csv', mime='text/csv', use_container_width=True)
    with tab_c:
        st.markdown('#### Agregação por Região de Saúde')
        if 'regiao_saude' not in df.columns:
            st.warning('A base não possui coluna de Região de Saúde.')
        else:
            tmp = df.copy()
            for col in ['populacao', 'total_ubs', 'total_equipes_aps', 'total_profissionais_aps', 'total_leitos_sus', 'nascidos_vivos', 'obitos', 'obitos_infantis']:
                if col in tmp.columns:
                    tmp[col] = pd.to_numeric(tmp[col], errors='coerce').fillna(0)
            agg = tmp.groupby('regiao_saude', dropna=False).agg(municipios=('municipio', 'count'), populacao=('populacao', 'sum'), total_ubs=('total_ubs', 'sum'), total_equipes_aps=('total_equipes_aps', 'sum'), total_profissionais_aps=('total_profissionais_aps', 'sum'), total_leitos_sus=('total_leitos_sus', 'sum'), nascidos_vivos=('nascidos_vivos', 'sum'), obitos=('obitos', 'sum'), obitos_infantis=('obitos_infantis', 'sum')).reset_index()
            agg['pop_por_equipe_aps'] = (agg['populacao'] / agg['total_equipes_aps'].replace({0: pd.NA})).round(2)
            agg['pop_por_ubs'] = (agg['populacao'] / agg['total_ubs'].replace({0: pd.NA})).round(2)
            agg['equipes_aps_por_10mil_hab'] = (agg['total_equipes_aps'] / agg['populacao'].replace({0: pd.NA}) * 10000).round(2)
            agg['ubs_por_10mil_hab'] = (agg['total_ubs'] / agg['populacao'].replace({0: pd.NA}) * 10000).round(2)
            agg['mortalidade_infantil_por_1000_nv'] = (agg['obitos_infantis'] / agg['nascidos_vivos'].replace({0: pd.NA}) * 1000).round(2)
            render_html_table(agg)
            st.download_button('Baixar agregação regional em CSV', data=agg.to_csv(index=False).encode('utf-8-sig'), file_name='indicadores_derivados_regioes.csv', mime='text/csv', use_container_width=True)
    with tab_d:
        st.markdown('\n            **Critérios usados nesta camada**\n\n            - `pop_por_equipe_aps` = população / total de equipes APS/INE.\n            - `pop_por_ubs` = população / total de UBS/estabelecimentos APS importados.\n            - `profissionais_por_equipe` = vínculos profissional-equipe / equipes APS/INE.\n            - `equipes_aps_por_10mil_hab` = equipes APS/INE / população × 10.000.\n            - `ubs_por_10mil_hab` = UBS / população × 10.000.\n            - `leitos_sus_por_10mil_hab` = leitos SUS / população × 10.000.\n            - `mortalidade_infantil_por_1000_nv` = óbitos infantis / nascidos vivos × 1.000.\n\n            **Observação:** esta camada não substitui metodologia oficial de financiamento, avaliação ou premiação. Ela serve para triagem técnica, análise territorial e priorização preliminar.\n            ')

def _render_governanca_base():
    st.markdown('### Governança da base de dados')
    st.caption('Esta aba consolida o status técnico das camadas de dados. Ela não carrega novas APIs; serve para registrar o que está validado, em auditoria, pendente ou suspenso antes de usar no dashboard.')
    gov = montar_governanca_base()
    resumo = gov.get('resumo', {})
    camadas = gov.get('camadas', pd.DataFrame())
    uso_dashboard = gov.get('uso_dashboard', pd.DataFrame())
    importacoes = gov.get('importacoes', pd.DataFrame())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Municípios', _formatar_inteiro(resumo.get('municipios')))
    c2.metric('População', _formatar_inteiro(resumo.get('populacao')))
    c3.metric('Equipes APS/INE', _formatar_inteiro(resumo.get('equipes_aps')))
    c4.metric('Vínculos profissionais', _formatar_inteiro(resumo.get('profissionais_vinculos')))
    c5, c6, c7, c8 = st.columns(4)
    c5.metric('UBS/unidades APS', _formatar_inteiro(resumo.get('ubs')))
    c6.metric('Leitos SUS', _formatar_inteiro(resumo.get('leitos_sus')))
    c7.metric('Coordenadas', f"{_formatar_inteiro(resumo.get('coordenadas'))}/{_formatar_inteiro(resumo.get('municipios'))}")
    c8.metric('Área oficial km²', _formatar_decimal(resumo.get('area_km2'), 3))
    st.info('Regra de uso: camadas **validadas** ou **funcionais** podem alimentar o dashboard; camadas **em auditoria** podem aparecer com ressalva; camadas **pendentes/suspensas** não devem ser usadas como indicador oficial.')
    tab_a, tab_b, tab_c, tab_d = st.tabs(['Status das camadas', 'Uso no dashboard', 'Histórico de importações', 'Próximas bases'])
    with tab_a:
        if camadas.empty:
            st.warning('Não foi possível montar o status das camadas.')
        else:
            filtro_status = st.multiselect('Filtrar status', sorted(camadas['status'].dropna().unique().tolist()), default=sorted(camadas['status'].dropna().unique().tolist()))
            visao = camadas[camadas['status'].isin(filtro_status)].copy() if filtro_status else camadas.copy()
            render_html_table(visao)
            st.download_button('Baixar governança das camadas em CSV', data=camadas.to_csv(index=False).encode('utf-8-sig'), file_name='governanca_camadas_base.csv', mime='text/csv', use_container_width=True)
    with tab_b:
        st.markdown('#### Orientação de uso por camada')
        if uso_dashboard.empty:
            st.info('Sem orientação disponível.')
        else:
            render_html_table(uso_dashboard)
        st.markdown('\n            **Leitura recomendada**\n\n            - **IBGE territorial, equipes APS/INE, profissionais vinculados, SINASC e SIM**: usar normalmente, com fonte e competência.\n            - **Leitos CNES**: usar como capacidade instalada com auditoria por extremos.\n            - **MDS, PNI e SIDRA socioeconômico**: não usar na primeira versão enquanto não houver fonte estável e validação.\n            - **Índice de vulnerabilidade**: ainda não calcular automaticamente; primeiro consolidar variáveis brutas e metodologia.\n            ')
    with tab_c:
        st.markdown('#### Histórico de importações registradas')
        if importacoes.empty:
            st.warning('Nenhuma importação registrada.')
        else:
            colunas_pref = [c for c in ['id', 'fonte_codigo', 'tipo_base', 'competencia', 'linhas', 'colunas', 'status', 'criado_em', 'nome_arquivo', 'mensagem'] if c in importacoes.columns]
            visao_imp = importacoes[colunas_pref].copy() if colunas_pref else importacoes.copy()
            render_html_table(visao_imp.sort_values(colunas_pref[0] if colunas_pref else visao_imp.columns[0], ascending=False))
            st.download_button('Baixar histórico de importações em CSV', data=importacoes.to_csv(index=False).encode('utf-8-sig'), file_name='historico_importacoes.csv', mime='text/csv', use_container_width=True)
    with tab_d:
        st.markdown('#### Fila técnica de novas bases')
        proximas = pd.DataFrame([{'prioridade': 1, 'base': 'INEP / Censo Escolar', 'dados': 'escolas urbanas, rurais, indígenas, quilombolas e educação especial', 'estrategia': 'Testar API/CSV oficial e importar primeiro em staging', 'status': 'Próxima'}, {'prioridade': 2, 'base': 'IBGE Censo 2022 — populações específicas', 'dados': 'população indígena, quilombola, PCD e possíveis recortes territoriais', 'estrategia': 'Retomar SIDRA com validação por tabela e município', 'status': 'Planejada'}, {'prioridade': 3, 'base': 'IBGE Censo 2022 — escolaridade/renda/saneamento', 'dados': 'alfabetização, instrução, renda, saneamento e vulnerabilidade econômica', 'estrategia': 'Usar staging; não consolidar sem checagem de coerência', 'status': 'Planejada'}, {'prioridade': 4, 'base': 'IPEA', 'dados': 'indicadores socioeconômicos e vulnerabilidade', 'estrategia': 'Entrar somente se houver endpoint/CSV estável', 'status': 'A avaliar'}, {'prioridade': 5, 'base': 'PNI', 'dados': 'imunização', 'estrategia': 'Suspenso até surgir fonte sem bloqueio', 'status': 'Suspensa'}, {'prioridade': 6, 'base': 'MDS', 'dados': 'CadÚnico, Bolsa Família e BPC', 'estrategia': 'Suspenso até haver exportação tabular/API confiável', 'status': 'Suspensa'}])
        render_html_table(proximas)
        st.warning('A próxima expansão recomendada é INEP/Censo Escolar, porque conversa diretamente com território, ruralidade, populações indígenas/quilombolas e vazios assistenciais, sem depender de índice de vulnerabilidade pronto.')

def _render_ibge_censo2022():
    st.markdown('### IBGE Censo 2022 — populações específicas')
    st.caption('Esta aba audita dados agregados por município do Censo 2022, como população indígena, população quilombola, pessoas com deficiência e pessoas diagnosticadas com autismo. A camada entra primeiro como staging/auditoria, antes de virar indicador oficial.')
    visao = montar_visao_ibge_censo2022()
    resumo = visao['resumo']
    municipios = visao['municipios']
    agregacao = visao['agregacao']
    qualidade = visao['qualidade']
    indicadores = visao['indicadores']
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Municípios', _formatar_inteiro(resumo.get('municipios')))
    c2.metric('População indígena', _formatar_inteiro(resumo.get('populacao_indigena')))
    c3.metric('População quilombola', _formatar_inteiro(resumo.get('populacao_quilombola')))
    c4.metric('Pessoas com deficiência', _formatar_inteiro(resumo.get('pessoas_com_deficiencia')))
    c5, c6 = st.columns(2)
    c5.metric('Municípios com indígenas', _formatar_inteiro(resumo.get('municipios_com_indigenas')))
    c6.metric('Pessoas diagnosticadas com autismo', _formatar_inteiro(resumo.get('pessoas_autismo')))
    if indicadores.empty:
        st.warning('Ainda não há indicadores IBGE/Censo 2022 importados para esta camada. Execute em Base de Dados → Blocos de migração o bloco `bloco_ibge_censo2022_populacoes_especificas`.')
    tab_a, tab_b, tab_c, tab_d, tab_e = st.tabs(['Por município', 'Regiões de Saúde', 'Qualidade dos dados', 'Indicadores brutos', 'Critérios'])
    with tab_a:
        busca = st.text_input('Buscar município', placeholder='Ex.: Cuiabá, Campinápolis, Comodoro', key='ibge_censo_busca')
        vis = municipios.copy()
        if busca and 'municipio' in vis.columns:
            vis = vis[vis['municipio'].astype(str).str.contains(busca, case=False, na=False)]
        preferidas = ['codigo_ibge', 'municipio', 'regiao_saude', 'populacao', 'pessoas_indigenas_2022', 'pessoas_quilombolas_2022', 'pessoas_tradicionais_total_2022', 'pessoas_com_deficiencia_2022', 'pct_pessoas_com_deficiencia_2022', 'pessoas_diagnosticadas_autismo_2022', 'pct_pessoas_diagnosticadas_autismo_2022']
        cols = _colunas_existentes(vis, preferidas)
        render_html_table(vis[cols] if cols else vis)
        st.download_button('Baixar visão municipal IBGE Censo 2022 em CSV', data=vis.to_csv(index=False).encode('utf-8-sig'), file_name='ibge_censo2022_populacoes_especificas_municipios.csv', mime='text/csv', use_container_width=True)
    with tab_b:
        if agregacao.empty:
            st.info('Sem agregação regional disponível.')
        else:
            render_html_table(agregacao)
            st.download_button('Baixar agregação regional IBGE Censo 2022 em CSV', data=agregacao.to_csv(index=False).encode('utf-8-sig'), file_name='ibge_censo2022_populacoes_especificas_regioes.csv', mime='text/csv', use_container_width=True)
    with tab_c:
        render_html_table(qualidade)
    with tab_d:
        if indicadores.empty:
            st.info('Sem indicadores brutos desta camada.')
        else:
            render_html_table(indicadores)
            st.download_button('Baixar indicadores brutos IBGE Censo 2022 em CSV', data=indicadores.to_csv(index=False).encode('utf-8-sig'), file_name='ibge_censo2022_indicadores_brutos.csv', mime='text/csv', use_container_width=True)
    with tab_e:
        st.markdown('\n            **Fonte e critério**\n\n            - Fonte: IBGE/SIDRA — Censo Demográfico 2022.\n            - Povos tradicionais: tentativas em tabelas SIDRA de população indígena e quilombola por município.\n            - Deficiência/autismo: tenta ler tabelas do Censo 2022 voltadas a pessoas com deficiência e TEA.\n            - Esta camada é **staging/auditoria**: os dados devem ser conferidos antes de alimentar um índice oficial.\n            - A contagem é agregada por município; não há dado pessoal ou identificável.\n            ')

def _render_inep_censo_escolar():
    st.markdown('### INEP / Censo Escolar — camada socioeducacional')
    st.caption('Esta aba audita os indicadores do Censo Escolar importados pelo bloco INEP. A camada entra primeiro como staging/auditoria, antes de virar indicador oficial do dashboard.')
    dados = montar_visao_inep()
    resumo = dados.get('resumo', {})
    municipios = dados.get('municipios', pd.DataFrame())
    indicadores = dados.get('indicadores', pd.DataFrame())
    agregacao = dados.get('agregacao', pd.DataFrame())
    qualidade = dados.get('qualidade', pd.DataFrame())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Municípios com escolas', _formatar_inteiro(resumo.get('municipios_com_base')))
    c2.metric('Escolas totais', _formatar_inteiro(resumo.get('escolas_total')))
    c3.metric('Escolas rurais', _formatar_inteiro(resumo.get('escolas_rurais')))
    c4.metric('Ed. especial/AEE', _formatar_inteiro(resumo.get('escolas_educacao_especial_aee')))
    c5, c6 = st.columns(2)
    c5.metric('Escolas indígenas', _formatar_inteiro(resumo.get('escolas_indigenas')))
    c6.metric('Escolas quilombolas', _formatar_inteiro(resumo.get('escolas_quilombolas')))
    if indicadores.empty:
        st.warning('Ainda não há indicadores INEP importados. Execute em Base de Dados → Blocos de migração o bloco `bloco_inep_educacao`. Se o download automático for pesado ou falhar, coloque o ZIP oficial dos Microdados do Censo Escolar em `data/uploads/inep/` e execute novamente.')
    tab_a, tab_b, tab_c, tab_d, tab_e = st.tabs(['Por município', 'Regiões de Saúde', 'Qualidade dos dados', 'Indicadores brutos', 'Critérios'])
    with tab_a:
        if municipios.empty:
            st.info('Sem dados municipais do INEP para exibir.')
        else:
            busca = st.text_input('Buscar município', placeholder='Ex.: Cuiabá, Sinop, Cáceres', key='inep_busca_mun')
            visao = municipios.copy()
            if busca and 'municipio' in visao.columns:
                visao = visao[visao['municipio'].astype(str).str.contains(busca, case=False, na=False)]
            preferidas = ['codigo_ibge', 'municipio', 'regiao_saude', 'populacao', 'escolas_total', 'escolas_urbanas', 'escolas_rurais', 'escolas_indigenas', 'escolas_quilombolas', 'escolas_educacao_especial_aee', 'matriculas_total', 'matriculas_educacao_especial', 'escolas_por_10mil_hab', 'matriculas_por_1000_hab']
            cols = _colunas_existentes(visao, preferidas)
            render_html_table(visao[cols] if cols else visao)
            st.download_button('Baixar visão municipal INEP em CSV', data=visao.to_csv(index=False).encode('utf-8-sig'), file_name='inep_censo_escolar_municipios.csv', mime='text/csv', use_container_width=True)
    with tab_b:
        if agregacao.empty:
            st.info('Sem agregação regional disponível.')
        else:
            render_html_table(agregacao)
            st.download_button('Baixar agregação regional INEP em CSV', data=agregacao.to_csv(index=False).encode('utf-8-sig'), file_name='inep_censo_escolar_regioes.csv', mime='text/csv', use_container_width=True)
    with tab_c:
        render_html_table(qualidade)
    with tab_d:
        if indicadores.empty:
            st.info('Sem indicadores brutos do INEP importados.')
        else:
            render_html_table(indicadores)
            st.download_button('Baixar indicadores brutos INEP em CSV', data=indicadores.to_csv(index=False).encode('utf-8-sig'), file_name='inep_indicadores_brutos.csv', mime='text/csv', use_container_width=True)
    with tab_e:
        st.markdown('\n            **Fonte e critério**\n\n            - Fonte: Microdados do Censo Escolar da Educação Básica/INEP.\n            - A carga tenta baixar o ZIP oficial do INEP; se falhar ou for pesado, aceita o ZIP em `data/uploads/inep/`.\n            - A agregação conta escolas por município a partir de `CO_ENTIDADE`.\n            - Escola urbana/rural usa `TP_LOCALIZACAO`, quando disponível.\n            - Escola indígena/quilombola usa campos de localização diferenciada e/ou indicadores declaratórios disponíveis no ano.\n            - Educação especial/AEE usa campos que contenham AEE ou educação especial, quando disponíveis.\n            - Esta camada é **staging/auditoria**. Ela não calcula vulnerabilidade automaticamente.\n            ')

def _render_regionalizacao_ms():
    st.markdown('### Dados Abertos/MS — Regionalização de Saúde')
    st.caption('Esta aba lê o CSV bruto salvo pelo bloco Dados Abertos/MS e compara a regionalização oficial encontrada com a região de saúde atualmente usada na base consolidada do sistema.')
    auditoria = montar_auditoria_regionalizacao_ms()
    if not auditoria.get('ok'):
        st.warning(auditoria.get('mensagem', 'Regionalização MS ainda não carregada.'))
        st.code('Base de Dados → Blocos de migração → bloco_dadosabertos_ms_regionalizacao')
        return
    resumo = auditoria.get('resumo', {})
    comparacao = auditoria.get('comparacao', pd.DataFrame())
    regionalizacao = auditoria.get('regionalizacao_ms', pd.DataFrame())
    agregado = auditoria.get('agregado_regioes', pd.DataFrame())
    divergencias = auditoria.get('divergencias', pd.DataFrame())
    raw = auditoria.get('raw', pd.DataFrame())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Municípios na fonte MS', _formatar_inteiro(resumo.get('municipios_ms')))
    c2.metric('Regiões de Saúde MS', _formatar_inteiro(resumo.get('regioes_ms')))
    c3.metric('Macrorregiões MS', _formatar_inteiro(resumo.get('macrorregioes_ms')))
    c4.metric('Divergências', _formatar_inteiro(resumo.get('divergencias')))
    st.info(f"Arquivo bruto analisado: `{resumo.get('arquivo', '—')}`")
    tab_a, tab_b, tab_c, tab_d = st.tabs(['Comparação com sistema', 'Regionalização MS', 'Agregado por região', 'CSV bruto'])
    with tab_a:
        if comparacao.empty:
            st.warning('Sem dados para comparação.')
        else:
            col1, col2 = st.columns([1.2, 1])
            with col1:
                busca = st.text_input('Buscar município', placeholder='Ex.: Cuiabá, Sinop, Cáceres', key='busca_regionalizacao_ms')
            with col2:
                somente_div = st.checkbox('Mostrar somente divergências', value=False, key='regionalizacao_somente_div')
            visao = comparacao.copy()
            if busca:
                alvo = visao.get('municipio_comparacao', pd.Series(dtype=str)).astype(str)
                visao = visao[alvo.str.contains(busca, case=False, na=False)]
            if somente_div and 'status_comparacao' in visao.columns:
                visao = visao[visao['status_comparacao'] != 'OK']
            cols = [c for c in ['codigo_ibge', 'municipio_sistema', 'municipio_ms', 'regiao_saude_sistema', 'regiao_saude_ms', 'macrorregiao_saude_ms', 'codigo_regiao_saude_ms', 'codigo_macrorregiao_saude_ms', 'status_comparacao'] if c in visao.columns]
            render_html_table(visao[cols])
            st.download_button('Baixar comparação regionalização MS x sistema em CSV', data=visao[cols].to_csv(index=False).encode('utf-8-sig'), file_name='regionalizacao_ms_comparacao.csv', mime='text/csv', use_container_width=True)
    with tab_b:
        if regionalizacao.empty:
            st.warning('Sem regionalização MS normalizada.')
        else:
            render_html_table(regionalizacao)
            st.download_button('Baixar regionalização MS normalizada em CSV', data=regionalizacao.to_csv(index=False).encode('utf-8-sig'), file_name='regionalizacao_ms_normalizada.csv', mime='text/csv', use_container_width=True)
    with tab_c:
        if agregado.empty:
            st.warning('Sem agregação regional disponível.')
        else:
            render_html_table(agregado)
    with tab_d:
        if raw.empty:
            st.warning('Sem CSV bruto disponível.')
        else:
            st.caption(f'{len(raw)} registros no arquivo bruto. Colunas: {len(raw.columns)}')
            render_html_table(raw.head(500))

def _listar_tabelas_disponiveis() -> list[str]:
    preferidas = ['municipios', 'base_municipal_consolidada', 'estabelecimentos_saude', 'cnes_estabelecimentos_gerais', 'equipes_aps', 'profissionais_cnes', 'indicadores_municipais', 'malhas_geograficas_municipais', 'dados_abertos_mt_catalogo', 'dados_mt_icqv_explorador', 'importacoes']
    try:
        with db_session() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        existentes = [r[0] for r in rows]
    except Exception:
        existentes = preferidas
    ordenadas = [t for t in preferidas if t in existentes]
    extras = [t for t in existentes if t not in ordenadas]
    return ordenadas + extras

def _render_icqv_dados_mt():
    st.markdown('#### Explorador ICQV-MT / Dados MT')
    st.caption('Esta visão não é a base municipal do ICQV. Ela mostra se o produto ICQV-MT publicado no Dados MT/SEPLAG possui links Power BI e pistas técnicas de endpoint aproveitável para uma próxima integração.')
    try:
        icqv = read_table('dados_mt_icqv_explorador')
    except Exception as exc:
        st.warning('A tabela `dados_mt_icqv_explorador` ainda não está disponível. Execute o bloco Dados MT / SEPLAG — Explorador ICQV-MT.')
        st.code(str(exc))
        return
    if icqv.empty:
        st.info('Nenhum registro encontrado. Execute o bloco Dados MT / SEPLAG — Explorador ICQV-MT com importação estruturada marcada.')
        return
    c1, c2, c3 = st.columns(3)
    c1.metric('Produtos encontrados', icqv.get('produto', pd.Series(dtype=str)).nunique())
    c2.metric('Links Power BI', icqv.get('url_powerbi', pd.Series(dtype=str)).nunique())
    possivel = icqv.get('endpoint_dados_detectado', pd.Series(dtype=str)).astype(str).str.contains('Poss', case=False, na=False).sum()
    c3.metric('Endpoint possível', int(possivel))
    cols = _colunas_existentes(icqv, ['produto', 'titulo_contexto', 'origem_pagina', 'url_powerbi', 'chave_publicacao', 'tenant_id', 'status_http', 'content_type', 'endpoint_dados_detectado', 'endpoints_candidatos', 'observacao', 'atualizado_em'])
    render_html_table(icqv[cols])
    st.download_button('Baixar diagnóstico ICQV-MT em CSV', data=icqv[cols].to_csv(index=False).encode('utf-8-sig'), file_name='diagnostico_icqv_mt_powerbi.csv', mime='text/csv', use_container_width=True)
    if possivel:
        st.success('Há pista de endpoint interno no Power BI. Próximo passo: análise técnica específica do painel antes de tentar importar indicadores municipais.')
    else:
        st.warning('Não há endpoint de dados municipal claramente reutilizável nesta tentativa. O ICQV pode continuar como referência metodológica, ou exigir obtenção institucional da planilha/base oficial.')

def _render_dados_abertos_mt():
    st.markdown('### Dados Abertos MT — Catálogo estadual')
    st.caption('Esta aba explora o inventário CKAN do Portal de Dados Abertos de Mato Grosso. Ela não consolida dados municipais ainda; serve para escolher quais bases estaduais realmente valem virar conexão específica.')
    try:
        catalogo = read_table('dados_abertos_mt_catalogo')
    except Exception as exc:
        st.warning('A tabela `dados_abertos_mt_catalogo` ainda não está disponível. Execute primeiro o bloco Dados Abertos MT — Catálogo estadual CKAN.')
        st.code(str(exc))
        return
    if catalogo.empty:
        st.info('Nenhum registro encontrado no catálogo estadual. Execute o bloco Dados Abertos MT — Catálogo estadual CKAN com importação estruturada.')
        return
    df = catalogo.copy()
    for col in ['dataset_titulo', 'organizacao_nome', 'grupos', 'tags', 'formato', 'url', 'relevancia_aps']:
        if col not in df.columns:
            df[col] = ''
    if 'pontuacao_aps' not in df.columns:
        df['pontuacao_aps'] = 0
    df['pontuacao_aps'] = pd.to_numeric(df['pontuacao_aps'], errors='coerce').fillna(0)
    datasets_unicos = int(df['dataset_nome'].nunique()) if 'dataset_nome' in df.columns else int(df['dataset_titulo'].nunique())
    recursos = len(df)
    recursos_alta = int((df['pontuacao_aps'] >= 14).sum())
    recursos_media = int(((df['pontuacao_aps'] >= 8) & (df['pontuacao_aps'] < 14)).sum())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Datasets', datasets_unicos)
    c2.metric('Recursos', recursos)
    c3.metric('Alta relevância APS', recursos_alta)
    c4.metric('Média relevância APS', recursos_media)
    st.divider()
    f1, f2, f3, f4 = st.columns([1.2, 1.2, 1.2, 2])
    with f1:
        formatos = ['Todos'] + sorted([x for x in df['formato'].dropna().astype(str).unique().tolist() if x.strip()])
        formato = st.selectbox('Formato', formatos, key='dados_mt_formato')
    with f2:
        organizacoes = ['Todas'] + sorted([x for x in df['organizacao_nome'].dropna().astype(str).unique().tolist() if x.strip()])
        org = st.selectbox('Organização', organizacoes, key='dados_mt_org')
    with f3:
        relevancia = st.selectbox('Relevância', ['Todos', 'Alta', 'Média ou alta', 'Baixa ou maior'], key='dados_mt_relevancia')
    with f4:
        termo = st.text_input('Buscar', placeholder='Ex.: saúde, ICQV, saneamento, município, educação, infraestrutura', key='dados_mt_busca')
    visao = df.copy()
    if formato != 'Todos':
        visao = visao[visao['formato'].astype(str) == formato]
    if org != 'Todas':
        visao = visao[visao['organizacao_nome'].astype(str) == org]
    if relevancia == 'Alta':
        visao = visao[visao['pontuacao_aps'] >= 14]
    elif relevancia == 'Média ou alta':
        visao = visao[visao['pontuacao_aps'] >= 8]
    elif relevancia == 'Baixa ou maior':
        visao = visao[visao['pontuacao_aps'] >= 4]
    if termo.strip():
        t = termo.strip().lower()
        texto = (visao['dataset_titulo'].astype(str) + ' ' + visao['dataset_descricao'].astype(str) + ' ' + visao['recurso_nome'].astype(str) + ' ' + visao['grupos'].astype(str) + ' ' + visao['tags'].astype(str) + ' ' + visao['organizacao_nome'].astype(str)).str.lower()
        visao = visao[texto.str.contains(t, na=False)]
    visao = visao.sort_values(['pontuacao_aps', 'dataset_titulo', 'formato'], ascending=[False, True, True])
    tab_a, tab_b, tab_c, tab_d = st.tabs(['Prioridades APS', 'Catálogo filtrável', 'Resumo técnico', 'Explorador ICQV-MT'])
    with tab_a:
        st.markdown('#### Candidatos mais promissores')
        st.caption('Use esta lista para escolher a próxima integração específica. Priorize formatos CSV, JSON, XLSX e bases municipais.')
        candidatos = visao.copy()
        formatos_bons = {'CSV', 'JSON', 'XLSX', 'XLS', 'GEOJSON'}
        if 'formato' in candidatos.columns:
            candidatos = candidatos[candidatos['formato'].astype(str).str.upper().isin(formatos_bons)]
        candidatos = candidatos.head(50)
        colunas = _colunas_existentes(candidatos, ['dataset_titulo', 'recurso_nome', 'organizacao_nome', 'grupos', 'formato', 'pontuacao_aps', 'relevancia_aps', 'url', 'url_dataset_portal'])
        if candidatos.empty:
            st.warning('Nenhum candidato encontrado com os filtros atuais.')
        else:
            render_html_table(candidatos[colunas])
            st.download_button('Baixar candidatos filtrados em CSV', data=candidatos[colunas].to_csv(index=False).encode('utf-8-sig'), file_name='candidatos_dados_abertos_mt_aps.csv', mime='text/csv', use_container_width=True)
    with tab_b:
        st.markdown('#### Catálogo completo filtrado')
        st.caption(f'{len(visao)} recurso(s) encontrado(s) com os filtros atuais.')
        colunas = _colunas_existentes(visao, ['dataset_titulo', 'recurso_nome', 'organizacao_nome', 'grupos', 'tags', 'formato', 'pontuacao_aps', 'relevancia_aps', 'ultima_modificacao', 'url', 'url_dataset_portal'])
        render_html_table(visao[colunas])
    with tab_c:
        st.markdown('#### Distribuição dos recursos')
        col1, col2 = st.columns(2)
        with col1:
            formatos_df = df['formato'].replace('', pd.NA).dropna().value_counts().reset_index()
            formatos_df.columns = ['formato', 'quantidade']
            render_html_table(formatos_df)
        with col2:
            org_df = df['organizacao_nome'].replace('', pd.NA).dropna().value_counts().head(20).reset_index()
            org_df.columns = ['organizacao', 'quantidade']
            render_html_table(org_df)
        st.info('Próximo passo recomendado: escolher 1 ou 2 candidatos de alta relevância, com URL em CSV/JSON/XLSX, e transformar em conectores específicos. Bases em PDF devem ficar como referência documental, não como API automática.')
    with tab_d:
        _render_icqv_dados_mt()


def _render_bases_publicas_oficiais():
    st.markdown("### Bases públicas oficiais")
    st.caption(
        "Catálogo de fontes planilhadas/downloadáveis para enriquecer o sistema com escolaridade, renda, trabalho, vulnerabilidade social e epidemiologia. "
        "Esta área fica na Central da Base de Dados, não no mapa, porque é governança e ingestão de dados."
    )
    st.markdown(
        """
        <div class="info-box">
        Prioridade desta fase: usar bases públicas já existentes, sem preenchimento manual. 
        O catálogo abaixo organiza IBGE, INEP, DATASUS e Atlas/IDHM para orientar importação, rastreabilidade e tratamento dentro do sistema.
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("Carregar/atualizar catálogo padrão", type="primary", use_container_width=True, key="bd_catalogo_padrao"):
            try:
                info = salvar_catalogo_padrao()
                st.success(f"Catálogo padrão atualizado: {info.get('linhas')} fonte(s).")
            except Exception as e:
                st.error(f"Falha ao atualizar catálogo: {e}")
    with col2:
        modelo = gerar_modelo_registro_fonte_publica()
        st.download_button(
            "Baixar modelo de registro de fonte",
            data=modelo.to_csv(index=False).encode("utf-8-sig"),
            file_name="modelo_registro_fonte_publica.csv",
            mime="text/csv",
            use_container_width=True,
            key="bd_catalogo_modelo_fonte",
        )

    matriz = matriz_priorizacao_importacao()
    if matriz is None or matriz.empty:
        st.warning("Catálogo de bases públicas ainda não foi carregado.")
        return

    c1, c2, c3 = st.columns([1, 1, 1])
    eixo = c1.multiselect("Eixo", sorted(matriz["eixo"].dropna().astype(str).unique().tolist()), key="bd_catalogo_eixo")
    prioridade = c2.multiselect("Prioridade", sorted(matriz["prioridade"].dropna().astype(str).unique().tolist()), key="bd_catalogo_prioridade")
    termo = c3.text_input("Buscar tema", placeholder="Ex.: renda, escolaridade, SINAN, mortalidade", key="bd_catalogo_busca")

    visao = matriz.copy()
    if eixo:
        visao = visao[visao["eixo"].isin(eixo)]
    if prioridade:
        visao = visao[visao["prioridade"].isin(prioridade)]
    if termo.strip():
        t = termo.strip().lower()
        blob = (
            visao.get("base", "").astype(str) + " " +
            visao.get("temas", "").astype(str) + " " +
            visao.get("uso_no_sistema", "").astype(str) + " " +
            visao.get("observacao", "").astype(str)
        ).str.lower()
        visao = visao[blob.str.contains(t, na=False)]

    cols = _colunas_existentes(visao, [
        "eixo", "base", "temas", "nivel_territorial", "formato_esperado",
        "uso_no_sistema", "prioridade", "recomendacao", "status", "observacao"
    ])
    render_html_table(visao[cols], max_rows=100, max_text=180)
    _download_csv_base(visao[cols], "catalogo_bases_publicas_oficiais.csv", "Baixar catálogo filtrado")

    with st.expander("Ordem prática recomendada", expanded=False):
        st.markdown(
            """
            1. **IBGE Censo 2022** — renda, alfabetização, escolaridade, domicílios, saneamento e entorno.
            2. **INEP** — INSE, IDEB, distorção idade-série e rendimento escolar.
            3. **DATASUS/SINAN/SIM/SINASC** — agravos, mortalidade e nascidos vivos.
            4. **Atlas Brasil/IDHM** — desenvolvimento humano e vulnerabilidade municipal.
            5. **SIH/SIA** — produção e internações, etapa posterior por ser mais pesada.
            """
        )


def _render_importador_bases_publicas():
    st.markdown("### Importar bases públicas planilhadas")
    st.caption(
        "Use esta área para importar arquivos oficiais já baixados, como IBGE, INEP, Atlas ou exportações do DATASUS/TABNET. "
        "Não é uma área de preenchimento manual."
    )

    st.markdown(
        """
        <div class="warn-box">
        Esta tela importa bases públicas disponíveis. Se ainda não houver arquivo oficial baixado, use primeiro a aba 
        <b>Bases públicas oficiais</b> para escolher a fonte e registrar a origem.
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns([1.1, 1.2, .8])
    tipo_base = c1.selectbox(
        "Tipo de base",
        ["IBGE Setores 2022", "INEP Municipal", "Atlas/IDHM Municipal", "Indicadores municipais gerais"],
        key="bd_pub_tipo_base",
    )
    fonte = c2.text_input("Fonte/descrição oficial", value="Base pública oficial", key="bd_pub_fonte")
    ano = c3.text_input("Ano", value="", key="bd_pub_ano")
    arquivo = st.file_uploader("Enviar arquivo oficial CSV/XLSX", type=["csv", "xlsx", "xls"], key="bd_pub_upload")

    if arquivo is not None:
        import tempfile
        suffix = "." + arquivo.name.split(".")[-1].lower()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(arquivo.getvalue())
            caminho_tmp = tmp.name

        if st.button("Importar base pública para o banco", type="primary", use_container_width=True, key="bd_pub_btn_importar"):
            try:
                info = importar_arquivo_socioeducacional(caminho_tmp, tipo_base, fonte, ano)
                if info.get("ok"):
                    st.success(f"Base importada: {info.get('linhas')} linha(s) na tabela `{info.get('tabela')}`.")
                    try:
                        registrar_base_publica_importada(
                            eixo=tipo_base.split()[0],
                            base=tipo_base,
                            arquivo_nome=arquivo.name,
                            tabela_destino=info.get("tabela", ""),
                            fonte_url=fonte,
                            ano_referencia=ano,
                            observacao="Importada pela Central da Base de Dados.",
                            linhas=info.get("linhas", 0),
                            municipios_identificados=info.get("municipios_mt", 0),
                        )
                    except Exception:
                        pass
                    preview = info.get("preview")
                    if isinstance(preview, pd.DataFrame) and not preview.empty:
                        render_html_table(preview, max_rows=20, max_text=140)
                else:
                    st.error(info.get("mensagem", "Falha ao importar base."))
            except Exception as e:
                st.error(f"Falha na importação: {e}")

    st.markdown("#### Bases socioeducacionais carregadas")
    socio = carregar_socioeducacional_consolidado()
    resumo = socio.get("resumo", pd.DataFrame())
    if isinstance(resumo, pd.DataFrame) and not resumo.empty:
        render_html_table(resumo, max_rows=20, max_text=160)

    disponibilidade = existem_bases_socioeducacionais_importadas()
    if disponibilidade.get("ok"):
        st.success(f"Há {disponibilidade.get('bases_carregadas')} base(s) pública(s) carregada(s), com {disponibilidade.get('linhas')} linha(s).")
    else:
        st.info("Nenhuma base pública socioeducacional real foi importada ainda.")

    if st.button("Gerar consolidado municipal socioeducacional", use_container_width=True, key="bd_pub_btn_consolidar"):
        try:
            info = salvar_consolidado_municipal()
            if info.get("ok"):
                st.success(f"Consolidado gerado: {info.get('linhas')} municípios e {info.get('colunas')} colunas.")
            else:
                st.warning(info.get("mensagem", "Não foi possível gerar o consolidado."))
        except Exception as e:
            st.error(f"Falha ao consolidar: {e}")





def _render_dicionario_ibge_variaveis():
    st.markdown("### Dicionário das variáveis IBGE")
    st.caption(
        "Camada de apresentação para transformar códigos como V00644, V00645 e V0001 em nomes mais compreensíveis no Diagnóstico Municipal."
    )

    st.markdown(
        """
        <div class="info-box">
        O dicionário automático cria nomes provisórios por tema/tabela, como "Alfabetização — V00644".
        Quando houver dicionário oficial detalhado do IBGE, importe uma planilha com as colunas:
        <b>tabela_origem</b>, <b>indicador_original</b>, <b>nome_amigavel</b>, <b>categoria_revisada</b>, <b>descricao_oficial</b> e <b>observacao</b>.
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Gerar dicionário automático IBGE", type="primary", use_container_width=True, key="dic_ibge_gerar_auto"):
            try:
                info = gerar_dicionario_automatico_ibge()
                if info.get("ok"):
                    st.success(f"Dicionário automático criado: {info.get('linhas')} variável(eis).")
                else:
                    st.warning(info.get("mensagem", "Não foi possível gerar o dicionário."))
            except Exception as e:
                st.error(f"Falha ao gerar dicionário: {e}")

    with c2:
        modelo = criar_modelo_dicionario_ibge()
        if isinstance(modelo, pd.DataFrame) and not modelo.empty:
            st.download_button(
                "Baixar modelo preenchível",
                data=modelo.to_csv(index=False).encode("utf-8-sig"),
                file_name="modelo_dicionario_ibge_variaveis.csv",
                mime="text/csv",
                use_container_width=True,
                key="dic_ibge_dl_modelo",
            )
        else:
            st.button("Baixar modelo preenchível", disabled=True, use_container_width=True, key="dic_ibge_dl_modelo_disabled")

    with c3:
        dic = carregar_dicionario_ibge()
        st.metric("Variáveis no dicionário", _formatar_inteiro(len(dic) if isinstance(dic, pd.DataFrame) else 0))


    st.markdown("#### Dicionário oficial do IBGE")
    st.caption("Fonte oficial esperada: dicionário de dados dos Agregados por Setores Censitários do Censo 2022.")

    st.code(URL_DICIONARIO_OFICIAL_IBGE_20250417, language="text")

    c_of1, c_of2 = st.columns(2)
    with c_of1:
        if st.button("Tentar importar dicionário oficial pelo FTP do IBGE", use_container_width=True, key="dic_ibge_importar_url"):
            try:
                with st.spinner("Tentando ler o dicionário oficial diretamente do FTP do IBGE..."):
                    info = importar_dicionario_oficial_ibge_url()
                if info.get("ok"):
                    st.success(f"Dicionário oficial importado: {info.get('linhas')} variável(eis).")
                else:
                    st.warning(info.get("mensagem", "Não foi possível importar pelo FTP."))
            except Exception as e:
                st.error(
                    "Não foi possível ler o arquivo diretamente pela internet. "
                    "Baixe o XLSX oficial e use a opção de caminho local abaixo. "
                    f"Detalhe: {e}"
                )

    with c_of2:
        caminho_oficial = st.text_input(
            "Caminho local do dicionário oficial XLSX",
            placeholder=r"Ex.: C:\Users\rafaeloliveira\Downloads\dicionario_de_dados_agregados_por_setores_censitarios_20250417.xlsx",
            key="dic_ibge_caminho_oficial",
        )
        if st.button("Importar dicionário oficial por caminho local", use_container_width=True, key="dic_ibge_importar_local_oficial"):
            if not caminho_oficial.strip():
                st.warning("Informe o caminho local do arquivo XLSX do dicionário oficial.")
            else:
                try:
                    info = importar_dicionario_oficial_ibge_flexivel(caminho_oficial)
                    if info.get("ok"):
                        st.success(f"Dicionário oficial importado: {info.get('linhas')} variável(eis).")
                    else:
                        st.warning(info.get("mensagem", "Não foi possível importar o dicionário."))
                except Exception as e:
                    st.error(f"Falha ao importar dicionário oficial local: {e}")

    with st.expander("Diagnosticar dicionário oficial local antes de importar", expanded=False):
        caminho_diag = st.text_input(
            "Caminho local para diagnóstico",
            placeholder=r"Ex.: C:\Users\rafaeloliveira\Downloads\dicionario_de_dados_agregados_por_setores_censitarios_20250417.xlsx",
            key="dic_ibge_caminho_diag",
        )
        if st.button("Diagnosticar dicionário oficial", use_container_width=True, key="dic_ibge_btn_diag_oficial"):
            if not caminho_diag.strip():
                st.warning("Informe o caminho local do dicionário.")
            else:
                try:
                    diag = diagnosticar_dicionario_oficial_ibge(caminho_diag)
                    if isinstance(diag, pd.DataFrame) and not diag.empty:
                        st.success(f"Diagnóstico concluído: {len(diag)} grupo(s) identificado(s).")
                        render_html_table(diag, max_rows=100, max_text=220)
                        _download_csv_base(diag, "diagnostico_dicionario_oficial_ibge.csv", "Baixar diagnóstico do dicionário")
                    else:
                        st.warning("Nenhuma variável Vxxxx foi identificada no dicionário informado.")
                except Exception as e:
                    st.error(f"Falha no diagnóstico do dicionário: {e}")


    st.markdown("#### Importar dicionário revisado/oficial")
    arq_dic = st.file_uploader("Enviar CSV/XLSX do dicionário IBGE", type=["csv", "xlsx", "xls"], key="dic_ibge_upload")
    if arq_dic is not None:
        import tempfile
        suffix = "." + arq_dic.name.split(".")[-1].lower()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(arq_dic.getvalue())
            caminho_dic = tmp.name
        if st.button("Importar dicionário IBGE", use_container_width=True, key="dic_ibge_importar"):
            try:
                info = importar_dicionario_ibge(caminho_dic)
                if info.get("ok"):
                    st.success(f"Dicionário importado: {info.get('linhas')} variável(eis).")
                else:
                    st.warning(info.get("mensagem", "Não foi possível importar o dicionário."))
            except Exception as e:
                st.error(f"Falha ao importar dicionário: {e}")

    st.markdown("#### Resumo do dicionário")
    resumo = resumo_dicionario_ibge()
    if isinstance(resumo, pd.DataFrame) and not resumo.empty:
        render_html_table(resumo, max_rows=50, max_text=160)

    dic_atual = carregar_dicionario_ibge()
    if isinstance(dic_atual, pd.DataFrame) and not dic_atual.empty:
        render_html_table(
            dic_atual,
            titulo="Dicionário atual das variáveis IBGE",
            subtitulo="A coluna nome_amigavel será usada no Diagnóstico Municipal.",
            max_rows=500,
            max_text=180,
        )
        _download_csv_base(dic_atual, "dicionario_ibge_variaveis_atual.csv", "Baixar dicionário atual")
    else:
        st.info("Dicionário vazio. Gere primeiro o consolidado municipal das bases públicas e depois clique em 'Gerar dicionário automático IBGE'.")



def _render_curadoria_ibge_indicadores():
    st.markdown("### Curadoria dos indicadores IBGE")
    st.caption(
        "Classifica as variáveis do IBGE em essenciais, complementares ou ocultas, para deixar o Diagnóstico Municipal mais útil para gestores."
    )

    st.markdown(
        """
        <div class="info-box">
        Esta curadoria não apaga dados do banco. Ela apenas controla o que aparece no Diagnóstico Municipal e quais indicadores são candidatos ao futuro índice socioeducacional.
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Gerar curadoria automática IBGE", type="primary", use_container_width=True, key="cur_ibge_gerar_auto"):
            try:
                info = gerar_curadoria_automatica_ibge()
                if info.get("ok"):
                    st.success(f"Curadoria automática criada: {info.get('linhas')} variável(eis).")
                else:
                    st.warning(info.get("mensagem", "Não foi possível gerar a curadoria."))
            except Exception as e:
                st.error(f"Falha ao gerar curadoria: {e}")

    cur = carregar_curadoria_ibge()
    with c2:
        if isinstance(cur, pd.DataFrame) and not cur.empty:
            st.download_button(
                "Baixar curadoria para revisão",
                data=cur.to_csv(index=False).encode("utf-8-sig"),
                file_name="curadoria_indicadores_ibge.csv",
                mime="text/csv",
                use_container_width=True,
                key="cur_ibge_dl",
            )
        else:
            st.button("Baixar curadoria para revisão", disabled=True, use_container_width=True, key="cur_ibge_dl_disabled")
    with c3:
        st.metric("Variáveis curadas", _formatar_inteiro(len(cur) if isinstance(cur, pd.DataFrame) else 0))

    st.markdown("#### Importar curadoria revisada")
    arq = st.file_uploader("Enviar CSV/XLSX de curadoria revisada", type=["csv", "xlsx", "xls"], key="cur_ibge_upload")
    if arq is not None:
        import tempfile
        suffix = "." + arq.name.split(".")[-1].lower()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(arq.getvalue())
            caminho = tmp.name
        if st.button("Importar curadoria revisada", use_container_width=True, key="cur_ibge_importar"):
            try:
                info = importar_curadoria_ibge(caminho)
                if info.get("ok"):
                    st.success(f"Curadoria importada: {info.get('linhas')} variável(eis).")
                else:
                    st.warning(info.get("mensagem", "Não foi possível importar."))
            except Exception as e:
                st.error(f"Falha ao importar curadoria: {e}")

    resumo = resumo_curadoria_ibge()
    if isinstance(resumo, pd.DataFrame) and not resumo.empty:
        render_html_table(
            resumo,
            titulo="Resumo da curadoria",
            subtitulo="Distribuição das variáveis por status e grupo analítico.",
            max_rows=100,
            max_text=180,
        )

    cur_atual = carregar_curadoria_ibge()
    if isinstance(cur_atual, pd.DataFrame) and not cur_atual.empty:
        filtro_status = st.multiselect(
            "Filtrar status",
            options=sorted(cur_atual["status_exibicao"].dropna().astype(str).unique().tolist()) if "status_exibicao" in cur_atual.columns else [],
            default=[],
            key="cur_ibge_status",
        )
        visao = cur_atual.copy()
        if filtro_status:
            visao = visao[visao["status_exibicao"].isin(filtro_status)]
        render_html_table(
            visao,
            titulo="Curadoria atual dos indicadores IBGE",
            subtitulo="Indicadores ocultos não aparecem no Diagnóstico Municipal; essenciais aparecem primeiro.",
            max_rows=700,
            max_text=220,
        )
    else:
        st.info("Curadoria vazia. Gere o dicionário IBGE e depois clique em 'Gerar curadoria automática IBGE'.")






def _render_conversor_dbc_datasus_generico(prefixo: str, titulo: str, exemplo: str):
    st.markdown(f"### {titulo}")
    st.caption("Use esta área para converter arquivos .dbc do DATASUS para .csv antes da importação da base específica.")

    caminho_dbc = st.text_input(
        "Caminho local do arquivo .dbc do DATASUS",
        value="",
        placeholder=exemplo,
        key=f"{prefixo}_dbc_caminho",
    )

    c1, c2, c3 = st.columns(3)

    with c1:
        if st.button("Diagnosticar DBC", use_container_width=True, key=f"{prefixo}_dbc_diag"):
            if not caminho_dbc.strip():
                st.warning("Informe o caminho do arquivo .dbc.")
            else:
                try:
                    info = diagnosticar_dbc(caminho_dbc)
                    if info.get("ok"):
                        st.success("Arquivo DBC localizado.")
                    else:
                        st.warning(info.get("mensagem", "Não foi possível localizar o DBC."))
                    libs = info.get("bibliotecas")
                    if isinstance(libs, pd.DataFrame) and not libs.empty:
                        with st.expander("Bibliotecas de conversão DBC", expanded=False):
                            render_html_table(libs, max_rows=20, max_text=180)
                except Exception as e:
                    st.error(f"Falha ao diagnosticar DBC: {e}")

    with c2:
        if st.button("Converter DBC para CSV", use_container_width=True, key=f"{prefixo}_dbc_converter"):
            if not caminho_dbc.strip():
                st.warning("Informe o caminho do arquivo .dbc.")
            else:
                try:
                    info = converter_dbc_para_csv(caminho_dbc)
                    if info.get("ok"):
                        st.success("DBC convertido para CSV com sucesso.")
                        csv_convertido = (
                            info.get("arquivo_csv")
                            or info.get("caminho_csv")
                            or info.get("csv_path")
                            or info.get("arquivo_saida")
                            or ""
                        )
                        if csv_convertido:
                            st.session_state[f"{prefixo}_csv_convertido"] = csv_convertido
                            st.info(f"Use este caminho no importador correspondente: {csv_convertido}")
                        else:
                            st.warning("A conversão informou sucesso, mas não retornou o caminho do CSV. Verifique se foi criado um .csv na mesma pasta do .dbc.")
                    else:
                        st.warning(info.get("mensagem", "Não foi possível converter automaticamente."))
                        detalhe = info.get("detalhe")
                        if detalhe:
                            st.caption(str(detalhe))
                        libs = info.get("bibliotecas")
                        if isinstance(libs, pd.DataFrame) and not libs.empty:
                            render_html_table(libs, max_rows=20, max_text=180)
                except Exception as e:
                    st.error(f"Falha ao converter DBC: {e}")

    with c3:
        if st.button("Ver comando de instalação", use_container_width=True, key=f"{prefixo}_dbc_cmd"):
            st.code(comandos_instalacao_dbc(), language="bash")

def _render_sim_local():
    st.markdown("### SIM / Mortalidade — importação local")
    st.caption(
        "Importa bases locais do SIM/DATASUS, filtra Mato Grosso e consolida indicadores de mortalidade por município de residência."
    )

    st.markdown(
        """
        <div class="info-box">
        Esta etapa aceita CSV/TXT/XLSX/ZIP/DBF. Se o arquivo vier em .dbc, use antes a conversão DBC disponível nesta própria aba do SIM para gerar o CSV.
        Priorize arquivos com campos como CODMUNRES, IDADE e CAUSABAS.
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("Conversão opcional de DBC do DATASUS para SIM", expanded=False):
        _render_conversor_dbc_datasus_generico(
            prefixo="sim",
            titulo="Conversão de DBC do DATASUS — SIM / Mortalidade",
            exemplo=r"C:\Users\rafaeloliveira\Projetos\plataforma_aps_inteligencia\arquivos\DATASUS\SIM\DATASUS_SIM_Mortalidade_MT_2024_DOMT2024.dbc",
        )

    caminho = st.text_input(
        "Caminho local do ZIP, CSV/TXT/XLSX/DBF ou pasta do SIM",
        placeholder=r"Ex.: C:\Users\rafaeloliveira\Projetos\plataforma_aps_inteligencia\arquivos\DATASUS\SIM\SIM_Mortalidade_MT_2024.csv",
        key="sim_caminho_local",
    )

    ano = st.text_input("Ano de referência", value="", placeholder="Ex.: 2024", key="sim_ano_ref")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Diagnosticar caminho SIM", use_container_width=True, key="sim_diag"):
            if not caminho.strip():
                st.warning("Informe o caminho local.")
            else:
                try:
                    info = diagnosticar_sim_local(caminho)
                    if info.get("ok"):
                        st.success(info.get("mensagem", "Diagnóstico concluído."))
                        arqs = info.get("arquivos")
                        if isinstance(arqs, pd.DataFrame) and not arqs.empty:
                            render_html_table(arqs, max_rows=100, max_text=220)
                    else:
                        st.warning(info.get("mensagem", "Não foi possível diagnosticar."))
                except Exception as e:
                    st.error(f"Falha no diagnóstico SIM: {e}")

    with c2:
        if st.button("Importar SIM filtrando MT", use_container_width=True, type="primary", key="sim_importar"):
            if not caminho.strip():
                st.warning("Informe o caminho local.")
            else:
                try:
                    with st.spinner("Importando SIM/DATASUS e filtrando Mato Grosso..."):
                        info = importar_sim_local(caminho, ano_referencia=ano)
                    if info.get("ok"):
                        st.success(f"Importação concluída: {info.get('linhas')} linha(s) de MT.")
                        cons = info.get("consolidado")
                        if isinstance(cons, pd.DataFrame) and not cons.empty:
                            render_html_table(cons, titulo="Consolidado municipal SIM", max_rows=150, max_text=180)
                    else:
                        st.warning(info.get("mensagem", "Não foi possível importar."))
                        rel = info.get("relatorio")
                        if isinstance(rel, pd.DataFrame) and not rel.empty:
                            render_html_table(rel, max_rows=100, max_text=220)
                except Exception as e:
                    st.error(f"Falha ao importar SIM: {e}")

    rel = relatorio_importacao_sim()
    if isinstance(rel, pd.DataFrame) and not rel.empty:
        with st.expander("Relatório da última importação SIM", expanded=False):
            render_html_table(rel, max_rows=100, max_text=220)

    cons_atual = carregar_sim_municipal()
    if isinstance(cons_atual, pd.DataFrame) and not cons_atual.empty:
        st.markdown("#### Consolidado municipal atual")
        c1, c2, c3 = st.columns(3)
        c1.metric("Municípios", _formatar_inteiro(cons_atual["municipio"].nunique() if "municipio" in cons_atual.columns else len(cons_atual)))
        c2.metric("Óbitos", _formatar_inteiro(cons_atual.get("obitos_total", pd.Series(dtype=float)).sum()))
        c3.metric("Registros", _formatar_inteiro(cons_atual.get("registros_base", pd.Series(dtype=float)).sum()))
        render_html_table(cons_atual, max_rows=150, max_text=180)
        _download_csv_base(cons_atual, "consolidado_municipal_sim_mortalidade.csv", "Baixar consolidado SIM municipal")

        st.markdown("#### Validação e ranking SIM")
        valid = resumo_validacao_sim()
        if valid.get("ok"):
            resumo = valid.get("resumo")
            ranking = valid.get("ranking_alertas")
            sem = valid.get("municipios_sem_registro")

            if isinstance(resumo, pd.DataFrame) and not resumo.empty:
                render_html_table(
                    resumo,
                    titulo="Resumo de validação SIM",
                    subtitulo="Checagens gerais da base importada e critério de consolidação.",
                    max_rows=20,
                    max_text=220,
                )

            if isinstance(ranking, pd.DataFrame) and not ranking.empty:
                with st.expander("Ranking municipal de alertas de mortalidade", expanded=False):
                    render_html_table(ranking, max_rows=60, max_text=160)
                    _download_csv_base(ranking, "ranking_alertas_sim_municipal.csv", "Baixar ranking de alertas SIM")

            if isinstance(sem, pd.DataFrame) and not sem.empty:
                with st.expander("Municípios sem registro no consolidado SIM", expanded=False):
                    render_html_table(sem, max_rows=150, max_text=220)

def _render_sinasc_local():
    st.markdown("### SINASC / Nascidos Vivos — importação local")
    st.caption(
        "Importa bases locais do SINASC/DATASUS, filtra Mato Grosso e consolida indicadores materno-infantis por município de residência da mãe."
    )

    st.markdown(
        """
        <div class="info-box">
        Esta etapa aceita CSV/TXT/XLSX/ZIP. Para DBF, o sistema tentará ler se a dependência estiver disponível; se falhar, converta o DBF para CSV.
        Priorize arquivos com campos como CODMUNRES, IDADEMAE, PESO, SEMAGESTAC/GESTACAO, CONSPRENAT/CONSULTAS e PARTO.
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("#### Conversão opcional de DBC do DATASUS")
    st.caption("Use esta área quando o arquivo baixado no DATASUS vier em formato .dbc, como DNMT2024.dbc.")

    caminho_dbc = st.text_input(
        "Caminho local do arquivo .dbc ou pasta com DBC",
        value="",
        placeholder=r"Ex.: C:\Users\rafaeloliveira\Projetos\plataforma_aps_inteligencia\arquivos\DATASUS\SINASC\SINASC_Nascidos_Vivos_MT_2024_DNMT2024.dbc",
        key="sinasc_caminho_dbc",
    )
    cdbc1, cdbc2, cdbc3 = st.columns(3)
    with cdbc1:
        if st.button("Diagnosticar DBC", use_container_width=True, key="sinasc_diag_dbc"):
            if not caminho_dbc.strip():
                st.warning("Informe o caminho do arquivo .dbc ou pasta.")
            else:
                try:
                    info = diagnosticar_dbc(caminho_dbc)
                    if info.get("ok"):
                        st.success(info.get("mensagem", "DBC detectado."))
                        arqs = info.get("arquivos")
                        if isinstance(arqs, pd.DataFrame) and not arqs.empty:
                            render_html_table(arqs, max_rows=50, max_text=240)
                    else:
                        st.warning(info.get("mensagem", "Nenhum DBC detectado."))
                except Exception as e:
                    st.error(f"Falha ao diagnosticar DBC: {e}")
    with cdbc2:
        if st.button("Converter DBC para CSV", use_container_width=True, type="primary", key="sinasc_converter_dbc"):
            if not caminho_dbc.strip():
                st.warning("Informe o caminho do arquivo .dbc.")
            else:
                try:
                    with st.spinner("Tentando converter DBC para CSV..."):
                        info = converter_dbc_para_csv(caminho_dbc)
                    if info.get("ok"):
                        st.success(info.get("mensagem", "DBC convertido."))
                        csv_convertido = (info.get("arquivo_csv") or info.get("caminho_csv") or info.get("csv_path") or info.get("arquivo_saida") or "")
                        if csv_convertido:
                            st.session_state["sinasc_csv_convertido"] = csv_convertido
                            st.info(f"Agora use este caminho no importador SINASC: {csv_convertido}")
                        else:
                            st.warning("A conversão informou sucesso, mas não retornou o caminho do CSV. Verifique se foi criado um arquivo .csv na mesma pasta do .dbc.")
                    else:
                        st.warning(info.get("mensagem", "Não foi possível converter automaticamente."))
                        st.code(comandos_instalacao_dbc(), language="bash")
                except Exception as e:
                    st.error(f"Falha na conversão DBC: {e}")
                    st.code(comandos_instalacao_dbc(), language="bash")
    with cdbc3:
        if st.button("Ver comando de instalação", use_container_width=True, key="sinasc_cmd_dbc"):
            st.code(comandos_instalacao_dbc(), language="bash")

    caminho = st.text_input(
        "Caminho local do ZIP, CSV/TXT/XLSX/DBF/DBC ou pasta do SINASC",
        placeholder=r"Ex.: C:\Users\rafaeloliveira\Downloads\SINASC_MT_2024",
        key="sinasc_caminho_local",
    )

    ano = st.text_input("Ano de referência", value="", placeholder="Ex.: 2024", key="sinasc_ano_ref")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Diagnosticar caminho SINASC", use_container_width=True, key="sinasc_diag"):
            if not caminho.strip():
                st.warning("Informe o caminho local.")
            else:
                try:
                    info = diagnosticar_sinasc_local(caminho)
                    if info.get("ok"):
                        st.success(info.get("mensagem", "Diagnóstico concluído."))
                        arqs = info.get("arquivos")
                        if isinstance(arqs, pd.DataFrame) and not arqs.empty:
                            render_html_table(arqs, max_rows=100, max_text=220)
                    else:
                        st.warning(info.get("mensagem", "Não foi possível diagnosticar."))
                except Exception as e:
                    st.error(f"Falha no diagnóstico SINASC: {e}")

    with c2:
        if st.button("Importar SINASC filtrando MT", use_container_width=True, type="primary", key="sinasc_importar"):
            if not caminho.strip():
                st.warning("Informe o caminho local.")
            else:
                try:
                    with st.spinner("Importando SINASC/DATASUS e filtrando Mato Grosso..."):
                        info = importar_sinasc_local(caminho, ano_referencia=ano)
                    if info.get("ok"):
                        st.success(f"Importação concluída: {info.get('linhas')} linha(s) de MT.")
                        cons = info.get("consolidado")
                        if isinstance(cons, pd.DataFrame) and not cons.empty:
                            render_html_table(cons, titulo="Consolidado municipal SINASC", max_rows=150, max_text=180)
                    else:
                        st.warning(info.get("mensagem", "Não foi possível importar."))
                        rel = info.get("relatorio")
                        if isinstance(rel, pd.DataFrame) and not rel.empty:
                            render_html_table(rel, max_rows=100, max_text=220)
                except Exception as e:
                    st.error(f"Falha ao importar SINASC: {e}")

    rel = relatorio_importacao_sinasc()
    if isinstance(rel, pd.DataFrame) and not rel.empty:
        with st.expander("Relatório da última importação SINASC", expanded=False):
            render_html_table(rel, max_rows=100, max_text=220)

    cons_atual = carregar_sinasc_municipal()
    if isinstance(cons_atual, pd.DataFrame) and not cons_atual.empty:
        st.markdown("#### Consolidado municipal atual")
        c1, c2, c3 = st.columns(3)
        c1.metric("Municípios", _formatar_inteiro(cons_atual["municipio"].nunique() if "municipio" in cons_atual.columns else len(cons_atual)))
        c2.metric("Nascidos vivos", _formatar_inteiro(cons_atual.get("nascidos_vivos", pd.Series(dtype=float)).sum()))
        c3.metric("Registros", _formatar_inteiro(cons_atual.get("registros_base", pd.Series(dtype=float)).sum()))
        render_html_table(cons_atual, max_rows=150, max_text=180)
        _download_csv_base(cons_atual, "consolidado_municipal_sinasc.csv", "Baixar consolidado SINASC municipal")

        st.markdown("#### Validação e ranking SINASC")
        valid = resumo_validacao_sinasc()
        if valid.get("ok"):
            resumo = valid.get("resumo")
            ranking = valid.get("ranking_alertas")
            sem = valid.get("municipios_sem_registro")

            if isinstance(resumo, pd.DataFrame) and not resumo.empty:
                render_html_table(
                    resumo,
                    titulo="Resumo de validação SINASC",
                    subtitulo="Checagens gerais da base importada e critério de consolidação.",
                    max_rows=20,
                    max_text=220,
                )

            if isinstance(ranking, pd.DataFrame) and not ranking.empty:
                with st.expander("Ranking municipal de alertas materno-infantis", expanded=False):
                    render_html_table(
                        ranking,
                        max_rows=150,
                        max_text=220,
                    )
                    _download_csv_base(ranking, "ranking_alertas_sinasc_municipal.csv", "Baixar ranking de alertas SINASC")

            if isinstance(sem, pd.DataFrame) and not sem.empty:
                with st.expander("Municípios sem registro no consolidado SINASC", expanded=False):
                    render_html_table(sem, max_rows=150, max_text=220)

def _render_inep_censo_escolar_local():
    st.markdown("### INEP / Censo Escolar — importação local")
    st.caption(
        "Use esta área para importar bases planilhadas do Censo Escolar. O sistema lê CSV/TXT/ZIP, filtra Mato Grosso e gera um consolidado municipal educacional."
    )

    st.markdown(
        """
        <div class="info-box">
        Recomendação: baixe os microdados do Censo Escolar no portal do INEP e aponte para a pasta local extraída ou para o arquivo ZIP.
        O sistema tenta identificar automaticamente colunas como CO_UF, SG_UF, CO_MUNICIPIO, NO_MUNICIPIO, CO_ENTIDADE e campos de infraestrutura escolar.
        </div>
        """,
        unsafe_allow_html=True,
    )

    caminho = st.text_input(
        "Caminho local do ZIP, CSV/TXT ou pasta do Censo Escolar",
        placeholder=r"Ex.: C:\Users\rafaeloliveira\Downloads\microdados_censo_escolar_2024",
        key="inep_caminho_local",
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Diagnosticar caminho INEP", use_container_width=True, key="inep_diag"):
            if not caminho.strip():
                st.warning("Informe o caminho local.")
            else:
                try:
                    info = diagnosticar_inep_censo_escolar_local(caminho)
                    if info.get("ok"):
                        st.success(info.get("mensagem", "Diagnóstico concluído."))
                        arqs = info.get("arquivos")
                        if isinstance(arqs, pd.DataFrame) and not arqs.empty:
                            render_html_table(arqs, max_rows=100, max_text=220)
                    else:
                        st.warning(info.get("mensagem", "Não foi possível diagnosticar."))
                except Exception as e:
                    st.error(f"Falha no diagnóstico INEP: {e}")

    with c2:
        if st.button("Importar INEP filtrando MT", use_container_width=True, type="primary", key="inep_importar"):
            if not caminho.strip():
                st.warning("Informe o caminho local.")
            else:
                try:
                    with st.spinner("Importando Censo Escolar/INEP e filtrando Mato Grosso..."):
                        info = importar_inep_censo_escolar_local(caminho)
                    if info.get("ok"):
                        st.success(f"Importação concluída: {info.get('linhas')} linha(s) de MT.")
                        cons = info.get("consolidado")
                        if isinstance(cons, pd.DataFrame) and not cons.empty:
                            render_html_table(cons, titulo="Consolidado municipal INEP", max_rows=150, max_text=180)
                    else:
                        st.warning(info.get("mensagem", "Não foi possível importar."))
                        rel = info.get("relatorio")
                        if isinstance(rel, pd.DataFrame) and not rel.empty:
                            render_html_table(rel, max_rows=100, max_text=220)
                except Exception as e:
                    st.error(f"Falha ao importar INEP: {e}")

    rel = relatorio_importacao_inep()
    if isinstance(rel, pd.DataFrame) and not rel.empty:
        with st.expander("Relatório da última importação INEP", expanded=False):
            render_html_table(rel, max_rows=100, max_text=220)

    cons_atual = carregar_inep_municipal()
    if isinstance(cons_atual, pd.DataFrame) and not cons_atual.empty:
        st.markdown("#### Consolidado municipal atual")
        c1, c2, c3 = st.columns(3)
        c1.metric("Municípios", _formatar_inteiro(cons_atual["municipio"].nunique() if "municipio" in cons_atual.columns else len(cons_atual)))
        c2.metric("Escolas", _formatar_inteiro(cons_atual.get("escolas_total", pd.Series(dtype=float)).sum()))
        c3.metric("Matrículas/registros", _formatar_inteiro(cons_atual.get("matriculas_total", pd.Series(dtype=float)).sum()))
        render_html_table(cons_atual, max_rows=150, max_text=180)
        _download_csv_base(cons_atual, "consolidado_municipal_inep_censo_escolar.csv", "Baixar consolidado INEP municipal")

def _render_roteiro_ibge_censo2022_setores():
    st.markdown("### Roteiro IBGE Censo 2022 — setores censitários")
    st.caption(
        "Ordem exata de importação dos 13 conjuntos de agregados por setores censitários do IBGE, com parâmetros prontos para usar no Importador Universal."
    )

    roteiro = roteiro_ibge_censo2022_setores()
    render_html_table(
        roteiro,
        titulo="Checklist de importação IBGE Censo 2022 por setores",
        subtitulo="Use a coluna 'tipo_descricao_sistema' e 'tabela_destino_sugerida' ao importar cada arquivo no Importador Universal.",
        max_rows=50,
        max_text=180,
    )
    _download_csv_base(roteiro, "roteiro_ibge_censo2022_setores.csv", "Baixar roteiro IBGE Censo 2022")


    st.markdown("#### Importar pacote completo do IBGE por setores")
    st.caption(
        "Use esta área quando você tiver baixado um ZIP contendo os 13 arquivos do IBGE. "
        "O sistema reconhece ZIP dentro de ZIP, identifica os CSVs, filtra apenas Mato Grosso e salva cada conjunto na tabela correta."
    )

    pacote_ibge = st.file_uploader(
        "Enviar pacote ZIP do IBGE Censo 2022 por setores",
        type=["zip"],
        key="ibge_setores_pacote_zip",
    )

    if pacote_ibge is not None:
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            tmp.write(pacote_ibge.getvalue())
            caminho_zip = tmp.name

        col_diag, col_imp = st.columns(2)
        with col_diag:
            if st.button("Diagnosticar pacote IBGE", use_container_width=True, key="ibge_zip_diagnosticar"):
                try:
                    diag = diagnosticar_pacote_ibge_setores(caminho_zip)
                    st.success(f"Diagnóstico concluído: {len(diag)} CSV(s) encontrado(s).")
                    render_html_table(diag, max_rows=30, max_text=160)
                    _download_csv_base(diag, "diagnostico_pacote_ibge_setores.csv", "Baixar diagnóstico do pacote")
                except Exception as e:
                    st.error(f"Falha no diagnóstico do pacote: {e}")

        with col_imp:
            if st.button("Importar pacote IBGE filtrando MT", type="primary", use_container_width=True, key="ibge_zip_importar"):
                try:
                    with st.spinner("Importando pacote IBGE. Isso pode levar alguns minutos, pois os arquivos são grandes..."):
                        info = importar_pacote_ibge_setores_mt(caminho_zip)
                    resultados = info.get("resultados")
                    if info.get("ok"):
                        st.success(
                            f"Pacote processado: {info.get('arquivos_processados')} arquivo(s), "
                            f"{info.get('linhas_mt_total')} linha(s) de MT importada(s)."
                        )
                    else:
                        st.warning(info.get("mensagem", "Pacote processado sem linhas MT."))
                    if isinstance(resultados, pd.DataFrame) and not resultados.empty:
                        render_html_table(resultados, max_rows=30, max_text=160)
                        _download_csv_base(resultados, "resultado_importacao_pacote_ibge_setores.csv", "Baixar resultado da importação")
                    st.info("Depois da importação, vá em 'Análise bases públicas' e clique em 'Gerar consolidado municipal das bases públicas'.")
                except Exception as e:
                    st.error(f"Falha na importação do pacote: {e}")



    st.markdown("#### Alternativa recomendada: importar por caminho local")
    st.caption(
        "Use esta opção quando o arquivo ou pasta ultrapassar o limite de 200 MB do upload do Streamlit. "
        "Como o sistema está rodando no seu computador, ele pode ler diretamente uma pasta local, por exemplo C:\\Users\\rafaeloliveira\\Downloads."
    )

    caminho_local = st.text_input(
        "Caminho local do ZIP, CSV ou pasta extraída",
        placeholder=r"Ex.: C:\Users\rafaeloliveira\Downloads\Agregados_IBGE",
        key="ibge_setores_caminho_local",
    )

    col_local1, col_local2 = st.columns(2)
    with col_local1:
        if st.button("Diagnosticar caminho local", use_container_width=True, key="ibge_local_diagnosticar"):
            if not caminho_local.strip():
                st.warning("Informe o caminho local da pasta, ZIP ou CSV.")
            else:
                try:
                    diag = diagnosticar_pacote_ibge_setores_local(caminho_local)
                    if diag.empty:
                        st.warning("Nenhum CSV encontrado no caminho informado.")
                    else:
                        st.success(f"Diagnóstico concluído: {len(diag)} CSV(s) encontrado(s).")
                        render_html_table(diag, max_rows=40, max_text=160)
                        _download_csv_base(diag, "diagnostico_local_ibge_setores.csv", "Baixar diagnóstico local")
                except Exception as e:
                    st.error(f"Falha no diagnóstico local: {e}")

    with col_local2:
        if st.button("Importar caminho local filtrando MT", type="primary", use_container_width=True, key="ibge_local_importar"):
            if not caminho_local.strip():
                st.warning("Informe o caminho local da pasta, ZIP ou CSV.")
            else:
                try:
                    with st.spinner("Importando arquivos locais do IBGE. Pode levar alguns minutos..."):
                        info = importar_pacote_ibge_setores_mt_local(caminho_local)
                    resultados = info.get("resultados")
                    if info.get("ok"):
                        st.success(
                            f"Importação local concluída: {info.get('arquivos_processados')} arquivo(s), "
                            f"{info.get('linhas_mt_total')} linha(s) de MT importada(s)."
                        )
                    else:
                        st.warning(info.get("mensagem", "Importação local concluída sem linhas MT."))
                    if isinstance(resultados, pd.DataFrame) and not resultados.empty:
                        render_html_table(resultados, max_rows=40, max_text=160)
                        _download_csv_base(resultados, "resultado_importacao_local_ibge_setores.csv", "Baixar resultado da importação local")
                    st.info("Depois da importação, vá em 'Análise bases públicas' e clique em 'Gerar consolidado municipal das bases públicas'.")
                except Exception as e:
                    st.error(f"Falha na importação local: {e}")


    st.markdown("#### Como preencher no Importador Universal")
    st.markdown(
        """
        Para cada arquivo baixado do FTP do IBGE:

        1. Extraia o ZIP.
        2. Envie o CSV no **Importador Universal**.
        3. Use:
           - **Eixo:** IBGE
           - **Tipo/descrição:** copie o valor da coluna `tipo_descricao_sistema`
           - **Ano:** 2022
           - **Fonte:** IBGE Censo 2022 — Agregados por Setores Censitários
           - **Tabela destino:** copie o valor da coluna `tabela_destino_sugerida`
        4. Clique em **Pré-analisar arquivo**.
        5. Se reconhecer municípios, clique em **Importar e salvar no banco**.
        6. Depois vá em **Análise bases públicas** e gere o consolidado municipal.
        """
    )


def _render_importador_universal_bases_publicas():
    st.markdown("### Importador universal de bases públicas")
    st.caption(
        "Importa arquivos oficiais já baixados em CSV/XLSX, padroniza colunas, tenta reconhecer municípios de MT, "
        "sugere tabela de destino e salva a base no banco para uso posterior nas análises."
    )

    st.markdown(
        """
        <div class="info-box">
        Use esta tela para bases de escolaridade, renda, trabalho, vulnerabilidade social e epidemiologia. 
        Exemplos: IBGE Censo 2022, INEP Indicadores Educacionais, DATASUS/TABNET exportado, Atlas/IDHM e planilhas públicas oficiais.
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns([1, 1.4, .8])
    eixo = c1.selectbox("Eixo da base", ["IBGE", "INEP", "DATASUS", "Atlas Brasil", "Outro"], key="imp_univ_eixo")
    tipo = c2.text_input("Tipo/descrição da base", value="Indicadores públicos", key="imp_univ_tipo")
    ano = c3.text_input("Ano", value="", key="imp_univ_ano")
    fonte = st.text_input("Fonte oficial / observação", value="Base pública oficial", key="imp_univ_fonte")
    arquivo = st.file_uploader("Enviar CSV/XLSX público", type=["csv", "xlsx", "xls"], key="imp_univ_upload")

    if arquivo is not None:
        import tempfile
        suffix = "." + arquivo.name.split(".")[-1].lower()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(arquivo.getvalue())
            caminho_tmp = tmp.name

        tabela_sugerida = sugerir_tabela_destino(eixo, tipo, arquivo.name)
        tabela_destino = st.text_input("Tabela destino sugerida", value=tabela_sugerida, key="imp_univ_tabela_destino")

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("Pré-analisar arquivo", use_container_width=True, key="imp_univ_pre_analise"):
                try:
                    prep = preparar_base_publica(caminho_tmp, eixo, tipo, fonte, ano, tabela_destino)
                    st.success(
                        f"Arquivo lido: {prep.get('linhas')} linhas, {prep.get('colunas')} colunas, "
                        f"{prep.get('municipios_distintos_mt')} município(s) de MT identificados."
                    )
                    st.metric("Cobertura municipal MT", f"{prep.get('cobertura_municipal_percentual')}%")
                    preview = prep.get("preview")
                    if isinstance(preview, pd.DataFrame) and not preview.empty:
                        render_html_table(preview, max_rows=20, max_text=120)
                    with st.expander("Colunas padronizadas", expanded=False):
                        st.write(prep.get("colunas_padronizadas", []))
                except Exception as e:
                    st.error(f"Falha na pré-análise: {e}")

        with col_b:
            if st.button("Importar e salvar no banco", type="primary", use_container_width=True, key="imp_univ_importar"):
                try:
                    info = importar_base_publica_universal(caminho_tmp, eixo, tipo, fonte, ano, tabela_destino)
                    if info.get("ok"):
                        st.success(
                            f"Base importada para `{info.get('tabela')}`: {info.get('linhas')} linhas e {info.get('colunas')} colunas."
                        )
                        st.info(
                            f"Municípios distintos de MT identificados: {info.get('municipios_distintos_mt')} "
                            f"({info.get('cobertura_municipal_percentual')}%)."
                        )
                        try:
                            registrar_base_publica_importada(
                                eixo=eixo,
                                base=tipo,
                                arquivo_nome=arquivo.name,
                                tabela_destino=info.get("tabela", tabela_destino),
                                fonte_url=fonte,
                                ano_referencia=ano,
                                observacao="Importada pelo importador universal de bases públicas.",
                                linhas=info.get("linhas", 0),
                                municipios_identificados=info.get("municipios_distintos_mt", 0),
                            )
                        except Exception:
                            pass
                        preview = info.get("preview")
                        if isinstance(preview, pd.DataFrame) and not preview.empty:
                            render_html_table(preview, max_rows=20, max_text=120)
                    else:
                        st.error(info.get("mensagem", "Falha ao importar."))
                except Exception as e:
                    st.error(f"Falha na importação: {e}")

    st.divider()
    st.markdown("#### Bases públicas já salvas pelo importador")
    resumo = consolidar_resumo_bases_publicas()
    if isinstance(resumo, pd.DataFrame) and not resumo.empty:
        render_html_table(resumo, max_rows=100, max_text=160)
        _download_csv_base(resumo, "resumo_bases_publicas_importadas.csv", "Baixar resumo das bases públicas")
    else:
        st.info("Nenhuma tabela `base_publica_*` foi importada ainda.")



def _render_analise_bases_publicas_importadas():
    st.markdown("### Análise das bases públicas importadas")
    st.caption(
        "Transforma as tabelas `base_publica_*` em inventário de indicadores e em um consolidado municipal para uso nas análises."
    )

    st.markdown(
        """
        <div class="info-box">
        Depois de importar bases públicas pelo Importador Universal, use esta aba para verificar quais indicadores foram encontrados,
        agrupar por categoria e gerar uma visão municipal consolidada.
        </div>
        """,
        unsafe_allow_html=True,
    )

    resumo_cat = resumo_categorias_bases_publicas()
    inventario = inventariar_indicadores_bases_publicas()

    c1, c2, c3 = st.columns(3)
    c1.metric("Indicadores encontrados", _formatar_inteiro(len(inventario) if isinstance(inventario, pd.DataFrame) else 0))
    c2.metric("Categorias", _formatar_inteiro(resumo_cat["categoria_sugerida"].nunique() if isinstance(resumo_cat, pd.DataFrame) and not resumo_cat.empty else 0))
    c3.metric("Tabelas públicas", _formatar_inteiro(inventario["tabela"].nunique() if isinstance(inventario, pd.DataFrame) and not inventario.empty else 0))

    if isinstance(resumo_cat, pd.DataFrame) and not resumo_cat.empty:
        render_html_table(
            resumo_cat,
            max_rows=50,
            max_text=160,
        )
    else:
        st.info("Nenhum indicador público numérico foi identificado ainda. Importe uma base pública primeiro.")

    st.markdown("#### Inventário de indicadores públicos")
    if isinstance(inventario, pd.DataFrame) and not inventario.empty:
        filtro_cat = st.multiselect(
            "Filtrar categoria",
            options=sorted(inventario["categoria_sugerida"].dropna().astype(str).unique().tolist()),
            default=[],
            key="analise_bp_categoria",
        )
        visao = inventario.copy()
        if filtro_cat:
            visao = visao[visao["categoria_sugerida"].isin(filtro_cat)]
        render_html_table(visao, max_rows=500, max_text=160)
        _download_csv_base(visao, "inventario_indicadores_bases_publicas.csv", "Baixar inventário de indicadores")
    else:
        st.warning("Ainda não há inventário de indicadores.")

    st.markdown("#### Consolidado municipal")
    col1, col2 = st.columns([1, 2])
    with col1:
        if st.button("Gerar consolidado municipal das bases públicas", type="primary", use_container_width=True, key="bp_btn_consolidar_municipal"):
            try:
                info = consolidar_bases_publicas_municipal()
                if info.get("ok"):
                    st.success(
                        f"Consolidado gerado: {info.get('linhas')} municípios, {info.get('indicadores')} indicador(es), {info.get('colunas')} coluna(s)."
                    )
                else:
                    st.warning(info.get("mensagem", "Não foi possível gerar o consolidado."))
            except Exception as e:
                st.error(f"Falha ao consolidar bases públicas: {e}")
    with col2:
        st.info(
            "O consolidado calcula médias municipais dos indicadores numéricos encontrados nas tabelas `base_publica_*`. "
            "Ele não altera o score oficial; apenas prepara os dados para leitura no Diagnóstico Municipal e futuras análises."
        )

    cons = carregar_consolidado_bases_publicas()
    if isinstance(cons, pd.DataFrame) and not cons.empty:
        st.caption(f"Consolidado atual: {len(cons)} município(s), {len(cons.columns)-1} indicador(es).")
        render_html_table(cons.head(100), max_rows=100, max_text=120)
        _download_csv_base(cons, "base_publica_consolidado_municipal.csv", "Baixar consolidado municipal")
        meta = carregar_metadados_indicadores_publicos()
        if isinstance(meta, pd.DataFrame) and not meta.empty:
            with st.expander("Metadados dos indicadores consolidados", expanded=False):
                render_html_table(meta, max_rows=500, max_text=160)

        relatorio_consolidacao = carregar_relatorio_consolidacao_bases_publicas()
        if isinstance(relatorio_consolidacao, pd.DataFrame) and not relatorio_consolidacao.empty:
            with st.expander("Relatório técnico da consolidação", expanded=False):
                render_html_table(
                    relatorio_consolidacao,
                    titulo="Como cada tabela foi consolidada",
                    subtitulo="Para bases IBGE por setores, o método correto é soma municipal. Para bases genéricas, o sistema mantém média municipal como aproximação inicial.",
                    max_rows=200,
                    max_text=180,
                )
                _download_csv_base(relatorio_consolidacao, "relatorio_consolidacao_bases_publicas.csv", "Baixar relatório da consolidação")

    else:
        st.info("O consolidado municipal de bases públicas ainda não foi gerado.")


    st.divider()
    st.markdown("#### Matriz de disponibilidade temática")
    st.caption("Mostra, por município, quais temas já possuem dados públicos importados e quais ainda estão pendentes.")

    resumo_disp = resumo_disponibilidade_tematica()
    matriz_disp = matriz_disponibilidade_tematica_municipal()
    lacunas = lacunas_bases_publicas_por_municipio()

    if isinstance(resumo_disp, pd.DataFrame) and not resumo_disp.empty:
        render_html_table(
            resumo_disp,
            titulo="Cobertura temática das bases públicas",
            subtitulo="Quantidade de municípios com dados disponíveis por categoria temática.",
            max_rows=50,
            max_text=160,
        )
        _download_csv_base(resumo_disp, "resumo_disponibilidade_tematica_bases_publicas.csv", "Baixar resumo temático")
    else:
        st.info("Resumo temático indisponível. Gere primeiro o consolidado municipal das bases públicas.")

    if isinstance(matriz_disp, pd.DataFrame) and not matriz_disp.empty:
        filtro_classe = st.multiselect(
            "Filtrar classe de disponibilidade",
            options=sorted(matriz_disp["classe_disponibilidade_publica"].dropna().astype(str).unique().tolist()),
            default=[],
            key="bp_disp_classe",
        )
        visao_disp = matriz_disp.copy()
        if filtro_classe:
            visao_disp = visao_disp[visao_disp["classe_disponibilidade_publica"].isin(filtro_classe)]
        render_html_table(
            visao_disp,
            titulo="Matriz município x temas disponíveis",
            subtitulo="Cada número indica a quantidade de indicadores disponíveis naquele tema para o município.",
            max_rows=500,
            max_text=120,
        )
        _download_csv_base(visao_disp, "matriz_disponibilidade_tematica_municipal.csv", "Baixar matriz temática")

    if isinstance(lacunas, pd.DataFrame) and not lacunas.empty:
        with st.expander("Lacunas por município", expanded=False):
            render_html_table(
                lacunas,
                titulo="Temas ausentes por município",
                subtitulo="Ajuda a priorizar quais bases públicas ainda precisam ser buscadas/importadas.",
                max_rows=500,
                max_text=180,
            )
            _download_csv_base(lacunas, "lacunas_bases_publicas_por_municipio.csv", "Baixar lacunas por município")



def _render_rastreabilidade_bases_publicas():
    st.markdown("### Rastreabilidade das bases públicas")
    st.caption("Registra quais arquivos públicos foram importados, de onde vieram e em qual tabela do sistema foram gravados.")

    importadas = carregar_bases_publicas_importadas()
    if importadas is None or importadas.empty:
        st.info("Nenhuma base pública foi registrada como importada ainda.")
    else:
        render_html_table(importadas, max_rows=300, max_text=160)
        _download_csv_base(importadas, "bases_publicas_importadas.csv", "Baixar rastreabilidade")

    st.markdown("#### Registrar fonte manualmente")
    c1, c2, c3 = st.columns(3)
    eixo = c1.selectbox("Eixo", ["IBGE", "INEP", "DATASUS", "Atlas Brasil", "Outro"], key="bd_reg_eixo")
    base = c2.text_input("Nome da base", key="bd_reg_base")
    ano = c3.text_input("Ano", key="bd_reg_ano")
    arquivo_nome = st.text_input("Arquivo", key="bd_reg_arquivo")
    tabela_destino = st.text_input("Tabela destino", key="bd_reg_tabela")
    fonte_url = st.text_input("Link/referência oficial", key="bd_reg_fonte")
    obs = st.text_area("Observação", key="bd_reg_obs")
    if st.button("Registrar base pública", use_container_width=True, key="bd_reg_btn"):
        if not base or not tabela_destino:
            st.warning("Informe ao menos o nome da base e a tabela destino.")
        else:
            info = registrar_base_publica_importada(
                eixo=eixo, base=base, arquivo_nome=arquivo_nome, tabela_destino=tabela_destino,
                fonte_url=fonte_url, ano_referencia=ano, observacao=obs
            )
            st.success(f"Registro salvo. Total de registros: {info.get('linhas')}.")



def _render_mds_visdata_local():
    st.markdown("### Etapa 12-A — MDS / CadÚnico / Bolsa Família / BPC")
    st.caption(
        "Importa arquivos CSV/XLSX baixados manualmente do VIS DATA 3 / MDS, filtra Mato Grosso, consolida por município e calcula percentuais sobre a população já existente no sistema."
    )

    st.markdown(
        """
        <div class="info-box">
        <b>Fluxo recomendado:</b> manter os arquivos baixados em <code>arquivos/MDS/</code>, separados em
        <code>BOLSA_FAMILIA</code>, <code>CADUNICO</code> e <code>BPC</code>. A tela abaixo também mantém o upload manual
        como alternativa, mas a leitura pela pasta local é o caminho mais estável para o projeto.
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("Indicadores aceitos pelo importador", expanded=False):
        df_ind = pd.DataFrame([
            {"campo_sistema": chave, "indicador": meta.get("label"), "tipo": meta.get("tipo")}
            for chave, meta in INDICADORES_MDS.items()
        ])
        render_html_table(df_ind, max_rows=80, max_text=260)

    competencia = st.text_input(
        "Competência/referência desejada",
        placeholder="Ex.: 2026-05. Se deixar vazio, o sistema usará a última competência disponível em cada arquivo.",
        key="mds_competencia",
    )

    aba_local, aba_upload = st.tabs(["Importar da pasta arquivos/MDS", "Upload manual"])

    with aba_local:
        st.markdown("#### Arquivos locais encontrados")
        st.caption("Caminho esperado: `arquivos/MDS/BOLSA_FAMILIA`, `arquivos/MDS/CADUNICO` e `arquivos/MDS/BPC`.")
        try:
            diag_local = diagnosticar_pasta_mds_local()
        except Exception as exc:
            diag_local = pd.DataFrame([{"situacao": "Erro", "erro": str(exc)[:600]}])

        if isinstance(diag_local, pd.DataFrame) and not diag_local.empty:
            render_html_table(diag_local, max_rows=80, max_text=240)
            if st.button("Importar arquivos da pasta local arquivos/MDS", type="primary", use_container_width=True, key="mds_importar_pasta_local"):
                try:
                    with st.spinner("Lendo arquivos em arquivos/MDS e consolidando municípios de Mato Grosso..."):
                        resultado = importar_mds_pasta_local(competencia=competencia.strip() or None, salvar=True)
                    st.success(
                        f"Importação local concluída: {resultado.get('linhas')} municípios na tabela `{resultado.get('tabela')}`; "
                        f"{resultado.get('municipios_com_algum_dado')} com algum dado MDS."
                    )
                    if resultado.get("erros"):
                        st.warning("Alguns arquivos não foram aproveitados: " + " | ".join(resultado.get("erros")[:5]))
                    diag = resultado.get("diagnostico")
                    if isinstance(diag, pd.DataFrame) and not diag.empty:
                        with st.expander("Relatório técnico da importação local", expanded=False):
                            render_html_table(diag, max_rows=100, max_text=240)
                    cobertura = resultado.get("cobertura_campos")
                    if isinstance(cobertura, pd.DataFrame) and not cobertura.empty:
                        with st.expander("Validação de cobertura dos principais campos", expanded=True):
                            st.caption("Mostra quantos municípios ficaram preenchidos em cada indicador essencial. Campo ausente indica que o arquivo correspondente ainda não foi baixado/importado.")
                            render_html_table(cobertura, max_rows=30, max_text=220)
                    cons = resultado.get("consolidado")
                    if isinstance(cons, pd.DataFrame) and not cons.empty:
                        render_html_table(
                            cons.head(60),
                            titulo="Prévia do consolidado municipal MDS",
                            subtitulo="Tabela salva como mds_cadunico_bolsa_familia_municipal.",
                            max_rows=60,
                            max_text=180,
                        )
                        _download_csv_base(cons, "consolidado_mds_cadunico_bolsa_familia_mt.csv", "Baixar consolidado MDS municipal")
                except Exception as exc:
                    st.error(f"Falha ao importar MDS pela pasta local: {exc}")
        else:
            st.info("Nenhum CSV/XLSX encontrado em `arquivos/MDS/`. Confira a pasta ou use a aba de upload manual.")

    with aba_upload:
        arquivos = st.file_uploader(
            "Enviar CSV/XLSX do VIS DATA 3 / MDS",
            type=["csv", "txt", "xlsx", "xls"],
            accept_multiple_files=True,
            key="mds_uploads",
        )

        if arquivos:
            st.markdown("#### Diagnóstico dos arquivos enviados")
            diagnosticos = []
            for arq in arquivos:
                try:
                    diagnosticos.append(diagnosticar_arquivo_mds(arq))
                except Exception as exc:
                    diagnosticos.append({"arquivo": getattr(arq, "name", "arquivo"), "situacao": "Erro", "erro": str(exc)[:500]})
            df_diag = pd.DataFrame(diagnosticos)
            render_html_table(df_diag, max_rows=100, max_text=240)

            if st.button("Importar MDS / CadÚnico / Bolsa Família / BPC", type="primary", use_container_width=True, key="mds_importar"):
                try:
                    with st.spinner("Importando arquivos MDS e consolidando municípios de Mato Grosso..."):
                        resultado = importar_mds_visdata(arquivos, competencia=competencia.strip() or None, salvar=True)
                    st.success(
                        f"Importação concluída: {resultado.get('linhas')} municípios na tabela `{resultado.get('tabela')}`; "
                        f"{resultado.get('municipios_com_algum_dado')} com algum dado MDS."
                    )
                    if resultado.get("erros"):
                        st.warning("Alguns arquivos não foram aproveitados: " + " | ".join(resultado.get("erros")[:3]))
                    diag = resultado.get("diagnostico")
                    if isinstance(diag, pd.DataFrame) and not diag.empty:
                        with st.expander("Relatório técnico da importação", expanded=False):
                            render_html_table(diag, max_rows=100, max_text=240)
                    cobertura = resultado.get("cobertura_campos")
                    if isinstance(cobertura, pd.DataFrame) and not cobertura.empty:
                        with st.expander("Validação de cobertura dos principais campos", expanded=True):
                            st.caption("Mostra quantos municípios ficaram preenchidos em cada indicador essencial. Campo ausente indica que o arquivo correspondente ainda não foi baixado/importado.")
                            render_html_table(cobertura, max_rows=30, max_text=220)
                    cons = resultado.get("consolidado")
                    if isinstance(cons, pd.DataFrame) and not cons.empty:
                        render_html_table(
                            cons.head(60),
                            titulo="Prévia do consolidado municipal MDS",
                            subtitulo="Tabela salva como mds_cadunico_bolsa_familia_municipal.",
                            max_rows=60,
                            max_text=180,
                        )
                        _download_csv_base(cons, "consolidado_mds_cadunico_bolsa_familia_mt.csv", "Baixar consolidado MDS municipal")
                except Exception as exc:
                    st.error(f"Falha ao importar MDS: {exc}")
        else:
            st.info("Use esta aba apenas se preferir enviar arquivos manualmente em vez de ler a pasta `arquivos/MDS/`.")

    atual = carregar_mds_municipal()
    if isinstance(atual, pd.DataFrame) and not atual.empty:
        st.markdown("#### Consolidado MDS atual")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Municípios", _formatar_inteiro(atual["municipio"].nunique() if "municipio" in atual.columns else len(atual)))
        c2.metric("Pessoas CadÚnico", _formatar_inteiro(pd.to_numeric(atual.get("cadunico_pessoas", pd.Series(dtype=float)), errors="coerce").sum()))
        c3.metric("Famílias PBF", _formatar_inteiro(pd.to_numeric(atual.get("bolsa_familia_familias", pd.Series(dtype=float)), errors="coerce").sum()))
        c4.metric("Valor PBF", _formatar_moeda(pd.to_numeric(atual.get("bolsa_familia_valor_repassado", pd.Series(dtype=float)), errors="coerce").sum()))

        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Pessoas PBF", _formatar_inteiro(pd.to_numeric(atual.get("bolsa_familia_pessoas", pd.Series(dtype=float)), errors="coerce").sum()))
        c6.metric("BPC total", _formatar_inteiro(pd.to_numeric(atual.get("bpc_total", pd.Series(dtype=float)), errors="coerce").sum()))
        c7.metric("BPC PCD", _formatar_inteiro(pd.to_numeric(atual.get("bpc_pcd", pd.Series(dtype=float)), errors="coerce").sum()))
        c8.metric("BPC idoso", _formatar_inteiro(pd.to_numeric(atual.get("bpc_idoso", pd.Series(dtype=float)), errors="coerce").sum()))

        valid = resumo_validacao_mds()
        if valid.get("ok"):
            resumo = valid.get("resumo")
            ranking = valid.get("ranking")
            if isinstance(resumo, pd.DataFrame) and not resumo.empty:
                render_html_table(resumo, titulo="Resumo de validação MDS", max_rows=30, max_text=220)
            if isinstance(ranking, pd.DataFrame) and not ranking.empty:
                with st.expander("Ranking preliminar de vulnerabilidade social — MDS", expanded=False):
                    cols = [
                        "ranking_vulnerabilidade_mds", "municipio", "regiao_saude", "populacao",
                        "pct_populacao_cadunico", "pct_populacao_bolsa_familia",
                        "pct_familias_pobreza_extrema_sobre_cadunico", "score_vulnerabilidade_mds",
                        "classificacao_vulnerabilidade_mds",
                    ]
                    cols = [c for c in cols if c in ranking.columns]
                    render_html_table(ranking[cols], max_rows=60, max_text=180)
                    _download_csv_base(ranking, "ranking_vulnerabilidade_mds_mt.csv", "Baixar ranking MDS")
        _download_csv_base(atual, "mds_cadunico_bolsa_familia_municipal.csv", "Baixar tabela MDS atual")
    else:
        st.info("Nenhum consolidado MDS importado ainda.")


def _render_plano_diretor_geo_importacoes():
    st.markdown("### Plano Diretor / Georreferenciamento — bases e validações")
    st.caption(
        "Área centralizada para importar, consolidar e validar as bases usadas no Mapa de Distâncias e Vazios Assistenciais. "
        "O módulo de mapa fica reservado para análise visual; ingestão e governança ficam aqui."
    )

    st.markdown(
        """
        <div class="info-box">
        <b>Fluxo recomendado:</b> primeiro consolide as bases socioeducacionais já existentes; depois trate hospitais/leitos; por fim valide coordenadas e ative apenas camadas confiáveis para o mapa.
        </div>
        """,
        unsafe_allow_html=True,
    )

    try:
        status_pd = status_bases_plano_diretor()
        render_html_table(status_pd, titulo="Status das bases essenciais do Plano Diretor", subtitulo="Camadas usadas pelo Mapa de Distâncias, Diagnóstico Municipal e Laboratório Digital.", max_rows=40, max_text=180)
    except Exception as exc:
        st.warning(f"Não foi possível carregar o status das bases do Plano Diretor: {exc}")

    aba1, aba2, aba3, aba4 = st.tabs([
        "Consolidar bases já carregadas",
        "Hospitais/leitos e retaguarda",
        "INEP / Censo Escolar",
        "Validação manual e governança",
    ])

    with aba1:
        st.markdown("#### Consolidar bases socioeducacionais/territoriais já existentes")
        st.info("Use estes botões para gerar as camadas consolidadas usadas no Diagnóstico Municipal e no Mapa de Distâncias. A rotina não deve ficar no mapa; fica registrada aqui na Central da Base.")
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("Consolidar INEP já carregado", use_container_width=True, key="bd_pd_inep_existente"):
                info = consolidar_inep_existente_para_socio()
                st.success(info.get("mensagem", "Processo concluído.")) if info.get("ok") else st.warning(info.get("mensagem", "Não foi possível concluir."))
                st.json(info)
        with c2:
            if st.button("Consolidar IBGE + MDS + INEP", type="primary", use_container_width=True, key="bd_pd_socio_geral"):
                info = consolidar_ibge_e_mds_para_socio_indicadores()
                st.success(info.get("mensagem", "Processo concluído.")) if info.get("ok") else st.warning(info.get("mensagem", "Não foi possível concluir."))
                st.json(info)
        with c3:
            if st.button("Gerar socio_consolidado_municipal", use_container_width=True, key="bd_pd_socio_final"):
                info = gerar_consolidado_socioeducacional_final()
                st.success(info.get("mensagem", "Consolidado gerado.")) if info.get("ok") else st.warning(info.get("mensagem", "Não foi possível gerar."))
                st.json(info)

    with aba2:
        st.markdown("#### Hospitais, leitos, UPA, maternidades e retaguarda")
        st.warning("A camada hospitalar só deve ser usada no mapa quando houver coordenadas validadas ou habilitadas para uso técnico.")
        c1, c2 = st.columns(2)
        with c1:
            ano_h = st.number_input("Ano para Hospitais e Leitos/MS", min_value=2020, max_value=2030, value=2026, step=1, key="bd_pd_ano_hospitais")
            if st.button("Importar Hospitais/Leitos MS", use_container_width=True, type="primary", key="bd_pd_import_hospitais"):
                with st.spinner("Baixando e qualificando camada hospitalar..."):
                    info = importar_hospitais_retaguarda_ms(int(ano_h))
                st.success(info.get("mensagem", "Importação concluída.")) if info.get("ok") else st.error(info.get("mensagem", "Falha na importação."))
                st.json(info)
        with c2:
            limite_geo = st.number_input("Limite de hospitais para geocodificar por tentativa", min_value=5, max_value=500, value=80, step=5, key="bd_pd_limite_geocode")
            if st.button("Geocodificar hospitais via API / centroide municipal", use_container_width=True, key="bd_pd_geocode_hospitais"):
                with st.spinner("Consultando geocodificação e aplicando fallback seguro quando possível..."):
                    info = geocodificar_hospitais_retaguarda_por_endereco_api(int(limite_geo))
                st.success(info.get("mensagem", "Geocodificação concluída.")) if info.get("ok") else st.error(info.get("mensagem", "Falha na geocodificação."))
                st.json(info)

        st.markdown("#### Cadastro manual/validado de hospitais")
        try:
            resumo_h = resumo_cadastro_manual_hospitais()
            st.info(f"Cadastro manual: {resumo_h.get('linhas', 0)} registros | {resumo_h.get('com_coordenada', 0)} com coordenada | {resumo_h.get('habilitados_mapa', 0)} habilitados para mapa")
        except Exception:
            pass
        c3, c4 = st.columns(2)
        with c3:
            if st.button("Preparar cadastro manual de hospitais/retaguarda", use_container_width=True, key="bd_pd_preparar_hospitais"):
                info = preparar_cadastro_manual_hospitais_retaguarda()
                st.success(info.get("mensagem", "Cadastro preparado.")) if info.get("ok") else st.warning(info.get("mensagem", "Não foi possível preparar."))
                st.json(info)
        with c4:
            if st.button("Ativar camada hospitalar apenas com registros validados", use_container_width=True, key="bd_pd_ativar_hospitais"):
                info = ativar_geo_hospitais_validados()
                st.success(info.get("mensagem", "Camada ativada.")) if info.get("ok") else st.warning(info.get("mensagem", "Camada não ativada."))
                st.json(info)

        arq_hosp = st.file_uploader("Importar planilha validada de hospitais/retaguarda", type=["csv", "xlsx", "xls"], key="bd_pd_upload_hospitais")
        if arq_hosp is not None and st.button("Importar cadastro manual validado", use_container_width=True, key="bd_pd_importar_hosp_manual"):
            try:
                df_hosp = pd.read_csv(arq_hosp, sep=None, engine="python") if arq_hosp.name.lower().endswith(".csv") else pd.read_excel(arq_hosp)
                info = importar_cadastro_manual_hospitais_df(df_hosp)
                st.success(info.get("mensagem", "Importado.")) if info.get("ok") else st.error(info.get("mensagem", "Falha ao importar."))
                st.json(info)
            except Exception as exc:
                st.error("Não foi possível importar a planilha manual de hospitais.")
                st.exception(exc)

    with aba3:
        st.markdown("#### Microdados INEP / Censo Escolar")
        st.caption("Use quando a camada escolar/ruralidade ainda não estiver consolidada ou precisar atualização.")
        ano_i = st.number_input("Ano para Microdados INEP", min_value=2020, max_value=2030, value=2024, step=1, key="bd_pd_ano_inep")
        if st.button("Baixar Microdados INEP/Censo Escolar", use_container_width=True, key="bd_pd_import_inep_micro"):
            with st.spinner("Baixando microdados do INEP e filtrando Mato Grosso. Pode demorar..."):
                info = importar_inep_microdados_oficial(int(ano_i))
            st.success(info.get("mensagem", "Importação concluída.")) if info.get("ok") else st.error(info.get("mensagem", "Falha na importação."))
            st.json(info)

    with aba4:
        st.markdown("#### O que ainda pode exigir validação manual")
        st.write("- Hospitais de referência regional, maternidades, UPA, pronto atendimento e unidades estratégicas, quando a base pública não trouxer coordenadas confiáveis.")
        st.write("- Comunidades quilombolas, ribeirinhas, aldeias, comunidades rurais locais e rotas reais de acesso.")
        st.write("- Correção de endereço/coordenada de UBS pelo município ou ERS.")
        st.info("Regra de governança: nenhuma coordenada inferida deve substituir validação oficial da SES, ERS ou município.")


def _render_trilha_auditoria_sistema():
    st.markdown("### 🧾 Trilha de Auditoria e Rastreabilidade")
    st.caption("Consulta de eventos registrados pelo sistema: login, gestão de usuários, navegação, importações e validações técnicas.")

    st.markdown(
        """
        <div class="info-box">
        A auditoria registra quem fez, quando fez, qual módulo foi acessado ou alterado, qual tabela/registro foi afetado e o status da ação.
        Ela serve para rastreabilidade institucional e apoio à governança dos dados. Não substitui política formal de segurança da informação.
        </div>
        """,
        unsafe_allow_html=True,
    )

    resumo = resumo_auditoria()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Eventos registrados", resumo.get("eventos_total", 0))
    c2.metric("Eventos hoje", resumo.get("eventos_hoje", 0))
    c3.metric("Eventos de login", resumo.get("eventos_login", 0))
    c4.metric("Falhas registradas", resumo.get("falhas", 0))

    st.markdown("#### Filtros")
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        data_inicio = st.date_input("Data inicial", value=None, key="audit_dt_ini")
    with f2:
        data_fim = st.date_input("Data final", value=None, key="audit_dt_fim")
    with f3:
        usuarios = ["Todos"] + valores_distintos("usuario_login")
        usuario = st.selectbox("Usuário", usuarios, key="audit_usuario")
    with f4:
        modulos = ["Todos"] + valores_distintos("modulo")
        modulo = st.selectbox("Módulo", modulos, key="audit_modulo")

    f5, f6, f7, f8 = st.columns(4)
    with f5:
        acoes = ["Todos"] + valores_distintos("acao")
        acao = st.selectbox("Ação", acoes, key="audit_acao")
    with f6:
        status_opts = ["Todos"] + valores_distintos("status")
        status = st.selectbox("Status", status_opts, key="audit_status")
    with f7:
        tabelas = ["Todas"] + valores_distintos("tabela_afetada")
        tabela = st.selectbox("Tabela afetada", tabelas, key="audit_tabela")
    with f8:
        limite = st.number_input("Limite", min_value=100, max_value=10000, value=2000, step=100, key="audit_limite")

    df = carregar_auditoria(
        data_inicio=str(data_inicio) if data_inicio else None,
        data_fim=str(data_fim) if data_fim else None,
        usuario=usuario,
        modulo=modulo,
        acao=acao,
        status=status,
        tabela=tabela,
        limite=int(limite),
    )

    st.markdown("#### Eventos registrados")
    if df.empty:
        st.info("Nenhum evento encontrado para os filtros selecionados.")
        return

    colunas_pref = [
        "data_hora", "usuario_login", "usuario_nome", "perfil", "modulo", "acao", "status",
        "tabela_afetada", "registro_id", "campo_alterado", "valor_anterior", "valor_novo", "justificativa", "detalhes"
    ]
    cols = [c for c in colunas_pref if c in df.columns]
    render_html_table(df[cols], max_rows=500, max_text=220)

    st.download_button(
        "Baixar trilha de auditoria em CSV",
        data=df.to_csv(index=False).encode("utf-8-sig"),
        file_name="trilha_auditoria_sistema_aps.csv",
        mime="text/csv",
        use_container_width=True,
        key="download_trilha_auditoria",
    )

    with st.expander("Critérios mínimos de auditoria recomendados", expanded=False):
        st.markdown(
            """
            - Registrar login, logout e tentativa de login inválida.
            - Registrar criação, edição, inativação e redefinição de senha de usuários.
            - Registrar importação e consolidação de bases.
            - Registrar validação manual de coordenadas de UBS, hospitais e territórios.
            - Registrar exportações relevantes para despacho ou apresentação.
            - Registrar alterações futuras de pesos, cortes, regras e parâmetros dos scores.
            """
        )

def render():
    st.subheader('Central da Base de Dados')
    st.markdown(
        "<span class='pill'>Bases públicas</span><span class='pill'>Importação</span><span class='pill'>Consolidação</span><span class='pill'>Rastreabilidade</span>",
        unsafe_allow_html=True,
    )
    st.info(
        "Esta central concentra ingestão, governança e rastreabilidade das bases. "
        "As novas bases de escolaridade, renda, trabalho, vulnerabilidade social e epidemiologia devem ser tratadas aqui, não nas telas de mapa."
    )

    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10, tab11, tab12, tab13, tab14, tab15, tab16, tab17, tab18, tab19, tab20, tab21, tab22 = st.tabs([
        'Bases públicas oficiais',
        'Roteiro IBGE Censo 2022',
        'Dicionário IBGE',
        'Curadoria IBGE',
        'INEP / Censo Escolar',
        'SINASC / Nascidos Vivos',
        'SIM / Mortalidade',
        'SINAN / Agravos',
        'MDS / CadÚnico / Bolsa Família',
        'Plano Diretor / Geo',
        'Importador universal',
        'Análise bases públicas',
        'Importar bases públicas',
        'Rastreabilidade',
        'Consolidação',
        'Governança da base',
        'Auditorias',
        'Dados Abertos MT',
        'Uploads manuais',
        'APIs legadas',
        'Tabelas',
        '🧾 Trilha de Auditoria'
    ])

    with tab1:
        _render_bases_publicas_oficiais()
    with tab2:
        _render_roteiro_ibge_censo2022_setores()
    with tab3:
        _render_dicionario_ibge_variaveis()
    with tab4:
        _render_curadoria_ibge_indicadores()
    with tab5:
        _render_inep_censo_escolar_local()
    with tab6:
        _render_sinasc_local()
    with tab7:
        _render_sim_local()
    with tab8:
        _render_sinan_local()
    with tab9:
        _render_mds_visdata_local()
    with tab10:
        _render_plano_diretor_geo_importacoes()
    with tab11:
        _render_importador_universal_bases_publicas()
    with tab12:
        _render_analise_bases_publicas_importadas()
    with tab13:
        _render_importador_bases_publicas()
    with tab14:
        _render_rastreabilidade_bases_publicas()
    with tab15:
        _render_consolidacao()
    with tab16:
        _render_governanca_base()
    with tab17:
        sub1, sub2, sub3 = st.tabs(['INEP / Censo Escolar', 'IBGE Censo 2022', 'Regionalização / Leitos'])
        with sub1:
            _render_inep_censo_escolar()
        with sub2:
            _render_ibge_censo2022()
        with sub3:
            _render_regionalizacao_ms()
            st.divider()
            _render_auditoria_leitos()
    with tab18:
        _render_dados_abertos_mt()
    with tab19:
        st.markdown('### Uploads manuais e planilhas internas')
        st.caption('Área mantida para exceções: CNES/INE, validações internas e bases da SES que não estejam disponíveis publicamente.')
        _render_upload_manual_equipes_cnes()
        st.divider()
        st.markdown('#### Upload genérico de planilhas')
        tipo_base = st.selectbox('Tipo de base', TIPOS_BASE)
        competencia = st.text_input('Competência/referência', placeholder='Ex.: 2026-12, 2026, dezembro/2026')
        arquivo = st.file_uploader('Enviar planilha ou CSV', type=['xlsx', 'xls', 'csv'])
        if arquivo:
            abas = ler_arquivo_upload(arquivo)
            aba = st.selectbox('Aba/arquivo', list(abas.keys()))
            df = padronizar_dataframe(abas[aba])
            st.caption(f'Prévia: {len(df)} linhas e {len(df.columns)} colunas')
            render_html_table(df.head(50))
            if st.button('Salvar e importar para a base estruturada', type='primary', use_container_width=True):
                _, importacao_id = salvar_upload(arquivo, tipo_base, competencia)
                resultado = importar_dataframe_estruturado(df, tipo_base, importacao_id=importacao_id)
                st.success(f"Importado para {resultado['tabela']}: {resultado['linhas']} linhas.")
                st.info('Agora gere novamente a base municipal consolidada.')
    with tab20:
        st.markdown('### APIs e inventário legado')
        st.warning(
            'Área mantida apenas para manutenção técnica. Como várias APIs falharam ou não retornaram dados suficientes, '
            'a prioridade atual passou a ser importar bases públicas planilhadas oficiais.'
        )
        sub_api1, sub_api2, sub_api3 = st.tabs(['Catálogo de APIs', 'Blocos de migração', 'Inventário Legacy'])
        with sub_api1:
            _render_catalogo_apis()
        with sub_api2:
            _render_blocos_migracao()
        with sub_api3:
            _render_inventario_legacy()
    with tab21:
        tabela = st.selectbox('Selecionar tabela', _listar_tabelas_disponiveis())
        df = read_table(tabela)
        st.caption(f'{len(df)} registros')
        render_html_table(df)
    with tab22:
        _render_trilha_auditoria_sistema()

def _render_sinan_local():
    st.markdown("### SINAN / Agravos de Notificação — importação local")
    st.caption(
        "Importa bases locais do SINAN/DATASUS, filtra Mato Grosso e consolida notificações por município e agravo."
    )

    st.markdown(
        """
        <div class="info-box">
        Esta etapa aceita CSV/TXT/XLSX/ZIP/DBF. Se o arquivo vier em .dbc, use antes a conversão DBC nesta própria aba.
        Para a primeira versão, recomenda-se começar por Dengue. Depois podem ser importados outros agravos, como tuberculose, hanseníase, violência e acidentes por animais peçonhentos.
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("Conversão opcional de DBC do DATASUS para SINAN", expanded=False):
        _render_conversor_dbc_datasus_generico(
            prefixo="sinan",
            titulo="Conversão de DBC do DATASUS — SINAN / Agravos",
            exemplo=r"C:\\Users\\rafaeloliveira\\Projetos\\plataforma_aps_inteligencia\\arquivos\\DATASUS\\SINAN\\DENGUE_MT_2024.dbc",
        )

    agravo = st.text_input("Nome do agravo", value="Dengue", placeholder="Ex.: Dengue, Tuberculose, Hanseníase, Violência", key="sinan_agravo")
    caminho = st.text_input(
        "Caminho local do ZIP, CSV/TXT/XLSX/DBF ou pasta do SINAN",
        placeholder=r"Ex.: C:\Users\rafaeloliveira\Projetos\plataforma_aps_inteligencia\arquivos\DATASUS\SINAN\DENGUE_MT_2024.csv",
        key="sinan_caminho_local",
    )
    ano = st.text_input("Ano de referência", value="", placeholder="Ex.: 2024", key="sinan_ano_ref")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Diagnosticar caminho SINAN", use_container_width=True, key="sinan_diag"):
            if not caminho.strip():
                st.warning("Informe o caminho local.")
            else:
                try:
                    info = diagnosticar_sinan_local(caminho)
                    if info.get("ok"):
                        st.success(info.get("mensagem", "Diagnóstico concluído."))
                        arqs = info.get("arquivos")
                        if isinstance(arqs, pd.DataFrame) and not arqs.empty:
                            render_html_table(arqs, max_rows=100, max_text=220)
                    else:
                        st.warning(info.get("mensagem", "Não foi possível diagnosticar."))
                except Exception as e:
                    st.error(f"Falha no diagnóstico SINAN: {e}")

    with c2:
        if st.button("Importar SINAN filtrando MT", use_container_width=True, type="primary", key="sinan_importar"):
            if not caminho.strip():
                st.warning("Informe o caminho local.")
            else:
                try:
                    with st.spinner("Importando SINAN/DATASUS e filtrando Mato Grosso..."):
                        info = importar_sinan_local(caminho, agravo=agravo, ano_referencia=ano)
                    if info.get("ok"):
                        st.success(f"Importação concluída: {info.get('linhas')} linha(s) de MT.")
                        cons = info.get("consolidado")
                        if isinstance(cons, pd.DataFrame) and not cons.empty:
                            st.info("Consolidado SINAN gerado. A prévia e os downloads aparecem abaixo em 'Consolidado municipal atual'.")
                    else:
                        st.warning(info.get("mensagem", "Não foi possível importar."))
                        rel = info.get("relatorio")
                        if isinstance(rel, pd.DataFrame) and not rel.empty:
                            render_html_table(rel, max_rows=100, max_text=220)
                except Exception as e:
                    st.error(f"Falha ao importar SINAN: {e}")

    rel = relatorio_importacao_sinan()
    if isinstance(rel, pd.DataFrame) and not rel.empty:
        with st.expander("Relatório da última importação SINAN", expanded=False):
            render_html_table(rel, max_rows=100, max_text=220)

    cons_atual = carregar_sinan_municipal()
    if isinstance(cons_atual, pd.DataFrame) and not cons_atual.empty:
        st.markdown("#### Consolidado municipal atual")
        c1, c2, c3 = st.columns(3)
        c1.metric("Municípios", _formatar_inteiro(cons_atual["municipio"].nunique() if "municipio" in cons_atual.columns else len(cons_atual)))
        c2.metric("Notificações", _formatar_inteiro(cons_atual.get("notificacoes", pd.Series(dtype=float)).sum()))
        c3.metric("Agravos", _formatar_inteiro(cons_atual["agravo"].nunique() if "agravo" in cons_atual.columns else 0))
        # Para performance, não renderiza o consolidado completo automaticamente.
        cons_gerencial = carregar_sinan_municipal_gerencial()
        if isinstance(cons_gerencial, pd.DataFrame) and not cons_gerencial.empty:
            with st.expander("Prévia gerencial do consolidado SINAN", expanded=False):
                render_html_table(cons_gerencial, max_rows=40, max_text=160)
        _download_csv_base(cons_atual, "consolidado_municipal_sinan_completo.csv", "Baixar consolidado SINAN completo")
        if isinstance(cons_gerencial, pd.DataFrame) and not cons_gerencial.empty:
            _download_csv_base(cons_gerencial, "consolidado_municipal_sinan_gerencial.csv", "Baixar visão gerencial SINAN")

        st.markdown("#### Validação e ranking SINAN")
        valid = resumo_validacao_sinan()
        if valid.get("ok"):
            resumo = valid.get("resumo")
            ranking = valid.get("ranking_alertas")
            if isinstance(resumo, pd.DataFrame) and not resumo.empty:
                render_html_table(
                    resumo,
                    titulo="Resumo de validação SINAN",
                    subtitulo="Checagens gerais da base importada.",
                    max_rows=20,
                    max_text=220,
                )
            if isinstance(ranking, pd.DataFrame) and not ranking.empty:
                with st.expander("Ranking municipal de alertas SINAN", expanded=False):
                    render_html_table(ranking, max_rows=60, max_text=160)
                    _download_csv_base(ranking, "ranking_alertas_sinan_municipal.csv", "Baixar ranking de alertas SINAN")
