from __future__ import annotations

import io
import re
import sqlite3
import unicodedata
import zipfile
from datetime import datetime
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from database.connection import get_connection
from database.queries import read_table
from config.municipios_mt import DEFAULT_MUNICIPIOS

RAW_DIR = Path('data/raw/apis')
RAW_DIR.mkdir(parents=True, exist_ok=True)


def _norm_txt(v: Any) -> str:
    txt = '' if v is None else str(v)
    txt = unicodedata.normalize('NFKD', txt).encode('ascii', 'ignore').decode('ascii')
    txt = re.sub(r'[^a-zA-Z0-9]+', ' ', txt).strip().lower()
    return re.sub(r'\s+', ' ', txt)


def _num(s) -> pd.Series:
    if s is None:
        return pd.Series(dtype='float64')
    return pd.to_numeric(
        pd.Series(s).astype(str).str.replace('.', '', regex=False).str.replace(',', '.', regex=False).str.replace(r'[^0-9\.\-]', '', regex=True),
        errors='coerce'
    )


def _col(df: pd.DataFrame, *nomes: str) -> str | None:
    if df is None or df.empty:
        return None
    mapa = {_norm_txt(c).replace(' ', '_'): c for c in df.columns}
    for n in nomes:
        if n in df.columns:
            return n
        key = _norm_txt(n).replace(' ', '_')
        if key in mapa:
            return mapa[key]
    # busca parcial
    for n in nomes:
        termos = _norm_txt(n).split()
        if not termos:
            continue
        for c in df.columns:
            cn = _norm_txt(c)
            if all(t in cn for t in termos):
                return c
    return None



def _dedupe_columns_for_sql(df: pd.DataFrame) -> pd.DataFrame:
    """Remove/renomeia colunas duplicadas antes de gravar no SQLite.

    Alguns CSVs oficiais trazem colunas equivalentes após merge, como municipio,
    municipio_leitos, municipio_x/municipio_y ou até duas colunas com o mesmo nome.
    O pandas aceita isso, mas o SQLite não cria tabela com nomes duplicados.
    """
    if df is None or df.empty:
        return df
    out = df.copy()

    # 1) força nomes de coluna para string e remove espaços extremos
    cols = [str(c).strip() if str(c).strip() else f"coluna_{i}" for i, c in enumerate(out.columns)]

    # 2) se houver duplicidade exata, renomeia com sufixo técnico
    vistos: dict[str, int] = {}
    novos = []
    for c in cols:
        base = c
        if base not in vistos:
            vistos[base] = 0
            novos.append(base)
        else:
            vistos[base] += 1
            novos.append(f"{base}_dup{vistos[base]}")
    out.columns = novos

    # 3) se houver variações de município criadas por merge, preserva municipio principal
    # e mantém as demais com nomes distintos, sem apagar informação de validação.
    if 'municipio' not in out.columns:
        for cand in ['municipio_x', 'municipio_y', 'municipio_leitos', 'nome_municipio', 'NO_MUNICIPIO', 'NM_MUN']:
            if cand in out.columns:
                out['municipio'] = out[cand]
                break

    # 4) SQLite também pode reclamar de nomes iguais ignorando maiúsculas/minúsculas.
    vistos_norm: dict[str, int] = {}
    finais = []
    for c in out.columns:
        key = str(c).lower()
        if key not in vistos_norm:
            vistos_norm[key] = 0
            finais.append(c)
        else:
            vistos_norm[key] += 1
            finais.append(f"{c}_sql{vistos_norm[key]}")
    out.columns = finais
    return out


def _municipios_df() -> pd.DataFrame:
    df = pd.DataFrame(DEFAULT_MUNICIPIOS)
    if 'nome' in df.columns and 'municipio' not in df.columns:
        df = df.rename(columns={'nome': 'municipio'})
    if 'codigo_ibge' not in df.columns:
        for c in ['codigo', 'cod_ibge', 'id']:
            if c in df.columns:
                df['codigo_ibge'] = df[c]
                break
    df['municipio'] = df.get('municipio', pd.Series(dtype=str)).astype(str)
    df['codigo_ibge'] = df.get('codigo_ibge', pd.Series(dtype=str)).astype(str).str.replace(r'\D', '', regex=True).str[:7]
    df['municipio_norm'] = df['municipio'].map(_norm_txt)
    return df[['municipio','codigo_ibge','municipio_norm']].drop_duplicates()


def _map_municipio(df: pd.DataFrame) -> pd.DataFrame:
    muni = _municipios_df()
    mapa_nome = dict(zip(muni['municipio_norm'], muni['municipio']))
    mapa_cod_nome = dict(zip(muni['codigo_ibge'], muni['municipio']))
    out = df.copy()
    cod_col = _col(out, 'codigo_ibge', 'CO_MUNICIPIO', 'CD_MUN', 'cod_municipio', 'codigo_municipio')
    mun_col = _col(out, 'municipio', 'NO_MUNICIPIO', 'NM_MUN', 'nome_municipio')
    if cod_col:
        out['codigo_ibge'] = out[cod_col].astype(str).str.replace(r'\D', '', regex=True).str[:7]
    else:
        out['codigo_ibge'] = ''
    if mun_col:
        out['municipio'] = out[mun_col].astype(str).str.strip()
    else:
        out['municipio'] = out['codigo_ibge'].map(mapa_cod_nome).fillna('')
    out['municipio_norm'] = out['municipio'].map(_norm_txt)
    out['municipio'] = out['municipio_norm'].map(mapa_nome).fillna(out['codigo_ibge'].map(mapa_cod_nome)).fillna(out['municipio'])
    out['codigo_ibge'] = out['codigo_ibge'].where(out['codigo_ibge'].astype(str).str.len().ge(7), out['municipio_norm'].map(dict(zip(muni['municipio_norm'], muni['codigo_ibge']))).fillna(''))
    return out


def _count_table(tabela: str) -> int:
    try:
        with get_connection() as con:
            return int(pd.read_sql_query(f'SELECT COUNT(*) AS n FROM "{tabela}"', con).iloc[0]['n'])
    except Exception:
        return 0


def status_bases_plano_diretor() -> pd.DataFrame:
    itens = [
        ('Hospitais/retaguarda georreferenciada', 'geo_hospitais_retaguarda', 'Distância até hospital, UPA, maternidade e retaguarda regional'),
        ('INEP municipal já tratado', 'base_publica_inep_censo_escolar_municipal', 'Escolas rurais, infraestrutura escolar e indicadores educacionais'),
        ('INEP socioeducacional consolidado', 'socio_inep_municipal', 'Cópia padronizada para diagnóstico/georreferenciamento'),
        ('IBGE setores básicos', 'base_publica_ibge_setores_basico', 'Ruralidade, setores, bairros/localidades e base territorial'),
        ('IBGE domicílios/saneamento', 'base_publica_ibge_setores_domicilios_1', 'Domicílios e saneamento por setores censitários'),
        ('IBGE alfabetização', 'base_publica_ibge_setores_alfabetizacao', 'Alfabetização/analfabetismo por setores censitários'),
        ('Indicadores socioeducacionais consolidados', 'socio_indicadores_municipais', 'Indicadores municipais derivados para mapa/relatório'),
        ('MDS CadÚnico/Bolsa Família/BPC', 'mds_cadunico_bolsa_familia_municipal', 'Vulnerabilidade social'),
        ('Assentamentos MT', 'dados_mt_assentamentos', 'Comunidades rurais/assentamentos'),
        ('Terras indígenas MT', 'dados_mt_terras_indigenas', 'Territórios indígenas/interseções disponíveis'),
    ]
    rows = []
    for nome, tabela, uso in itens:
        n = _count_table(tabela)
        rows.append({'Base': nome, 'Tabela': tabela, 'Linhas': n, 'Status': 'Carregada' if n > 0 else 'Pendente', 'Uso no georreferenciamento': uso})
    return pd.DataFrame(rows)


def consolidar_inep_existente_para_socio() -> dict:
    df = read_table('base_publica_inep_censo_escolar_municipal')
    if df.empty:
        return {'ok': False, 'mensagem': 'A tabela base_publica_inep_censo_escolar_municipal está vazia. Use primeiro o importador do INEP/Censo Escolar.', 'linhas': 0}
    out = _map_municipio(df)
    out['tipo_base'] = 'INEP Municipal'
    out['fonte'] = 'Base pública INEP/Censo Escolar já carregada no sistema'
    out['ano_referencia'] = out.get('ano_referencia', pd.Series([''] * len(out))) if 'ano_referencia' in out.columns else ''
    out['data_importacao'] = datetime.now().isoformat(timespec='seconds')
    with get_connection() as con:
        out.to_sql('socio_inep_municipal', con, if_exists='replace', index=False)
    # também grava em indicadores gerais, preservando colunas úteis.
    return {'ok': True, 'mensagem': 'INEP municipal consolidado para socio_inep_municipal.', 'linhas': int(len(out)), 'tabela': 'socio_inep_municipal'}


def consolidar_ibge_e_mds_para_socio_indicadores() -> dict:
    muni = _municipios_df()[['municipio','codigo_ibge']].copy()
    base = muni.copy()

    # INEP municipal já agregado.
    inep = read_table('base_publica_inep_censo_escolar_municipal')
    if not inep.empty:
        inep = _map_municipio(inep)
        cols = [c for c in inep.columns if c in [
            'municipio','escolas_total','matriculas_total','escolas_publicas','escolas_privadas','escolas_urbanas','escolas_rurais',
            'perc_escolas_rurais','perc_escolas_urbanas','perc_escolas_com_internet','perc_escolas_com_agua_rede','perc_escolas_com_esgoto',
            'perc_escolas_com_energia','perc_escolas_com_biblioteca_sala_leitura','perc_escolas_com_lab_informatica','perc_escolas_com_quadra'
        ]]
        if 'municipio' in cols:
            tmp = inep[cols].drop_duplicates(subset=['municipio'])
            base = base.merge(tmp, on='municipio', how='left')

    # IBGE setores básico: ruralidade, setores e população setorial.
    basico = read_table('base_publica_ibge_setores_basico')
    if not basico.empty:
        basico = _map_municipio(basico)
        pop_col = _col(basico, 'v0001', 'populacao', 'populacao_total')
        area_col = _col(basico, 'AREA_KM2', 'area_km2')
        sit_col = _col(basico, 'SITUACAO', 'situacao')
        g = basico[['municipio']].copy()
        g['setores_censitarios'] = 1
        if pop_col: g['populacao_setores_censo2022'] = _num(basico[pop_col])
        if area_col: g['area_setores_km2'] = _num(basico[area_col])
        if sit_col:
            sit = basico[sit_col].astype(str).map(_norm_txt)
            g['setor_rural'] = sit.str.contains('rural').astype(int)
            g['setor_urbano'] = sit.str.contains('urbana|urbano').astype(int)
        agg = g.groupby('municipio', dropna=False).sum(numeric_only=True).reset_index()
        if 'setor_rural' in agg.columns and 'setores_censitarios' in agg.columns:
            agg['perc_setores_rurais'] = (agg['setor_rural'] / agg['setores_censitarios'].replace(0, pd.NA) * 100).round(2)
        base = base.merge(agg, on='municipio', how='left')

    # Saneamento já calculável pela base domicílios 1, quando disponível.
    # Correção V17: algumas bases chegam sem a coluna literal "municipio" após o merge
    # por causa de variações como municipio_x/municipio_y, código IBGE ou nomes de setor.
    # Esta rotina normaliza o vínculo antes de agrupar, evitando KeyError: 'municipio'.
    dom1 = read_table('base_publica_ibge_setores_domicilios_1')
    if not dom1.empty and not basico.empty:
        setor_dom = _col(dom1, 'CD_SETOR', 'cod_setor', 'codigo_setor', 'setor')
        setor_bas = _col(basico, 'CD_SETOR', 'cod_setor', 'codigo_setor', 'setor')
        if setor_dom and setor_bas and 'municipio' in basico.columns:
            d0 = dom1.copy()
            b0 = basico[[setor_bas, 'municipio']].copy().rename(columns={setor_bas: '__setor_join__'})
            d0['__setor_join__'] = d0[setor_dom].astype(str).str.replace(r'\D', '', regex=True)
            b0['__setor_join__'] = b0['__setor_join__'].astype(str).str.replace(r'\D', '', regex=True)
            d = d0.merge(b0.drop_duplicates('__setor_join__'), on='__setor_join__', how='left', suffixes=('', '_ibge'))
        else:
            d = _map_municipio(dom1)

        # Garante uma coluna municipal única e válida.
        if 'municipio' not in d.columns:
            for possivel in ['municipio_ibge', 'municipio_y', 'nome_municipio', 'NO_MUNICIPIO', 'NM_MUN']:
                if possivel in d.columns:
                    d['municipio'] = d[possivel]
                    break
        if 'municipio' in d.columns:
            d = _map_municipio(d)
            # Convenção usada no sistema: V00001 como domicílios avaliados; esgotamento inadequado aproximado por colunas não rede/adequadas quando identificadas.
            total_col = _col(d, 'V00001')
            d['domicilios_avaliados_saneamento'] = _num(d[total_col]) if total_col else pd.NA
            candidatos_inadequados = [c for c in ['V00143','V00144','V00145','V00147','V00148'] if c in d.columns]
            if candidatos_inadequados:
                d['domicilios_esgotamento_inadequado_proxy'] = sum((_num(d[c]).fillna(0) for c in candidatos_inadequados), start=pd.Series(0, index=d.index))
            else:
                d['domicilios_esgotamento_inadequado_proxy'] = pd.NA
            agg = d.groupby('municipio', dropna=False)[['domicilios_avaliados_saneamento','domicilios_esgotamento_inadequado_proxy']].sum(min_count=1).reset_index()
            agg['perc_esgotamento_inadequado_proxy'] = (agg['domicilios_esgotamento_inadequado_proxy'] / agg['domicilios_avaliados_saneamento'].replace(0, pd.NA) * 100).round(2)
            base = base.merge(agg, on='municipio', how='left')

    # MDS.
    mds = read_table('mds_cadunico_bolsa_familia_municipal')
    if not mds.empty:
        mds = _map_municipio(mds)
        cols_mds = ['municipio'] + [c for c in mds.columns if c in [
            'cadunico_familias','cadunico_pessoas','cadunico_familias_pobreza_extrema','bolsa_familia_familias','bolsa_familia_pessoas',
            'pct_populacao_cadunico','pct_populacao_bolsa_familia','bpc_total','bpc_idoso','bpc_pcd','score_vulnerabilidade_mds','classificacao_mds'
        ]]
        if len(cols_mds) > 1:
            base = base.merge(mds[cols_mds].drop_duplicates(subset=['municipio']), on='municipio', how='left')

    base['tipo_base'] = 'Indicadores municipais gerais'
    base['fonte'] = 'Consolidação interna: IBGE setores + INEP + MDS já carregados'
    base['ano_referencia'] = '2022/2024'
    base['data_importacao'] = datetime.now().isoformat(timespec='seconds')
    indicador_cols = [c for c in base.columns if c not in ['municipio','codigo_ibge','tipo_base','fonte','ano_referencia','data_importacao']]
    if not indicador_cols:
        return {'ok': False, 'mensagem': 'Não foram encontradas colunas de indicadores para consolidar.', 'linhas': 0}
    with get_connection() as con:
        base.to_sql('socio_indicadores_municipais', con, if_exists='replace', index=False)
    return {'ok': True, 'mensagem': 'Indicadores socioeducacionais/territoriais consolidados.', 'linhas': int(len(base)), 'colunas': int(len(base.columns)), 'tabela': 'socio_indicadores_municipais'}


def gerar_consolidado_socioeducacional_final() -> dict:
    try:
        from services.socioeducacional_service import salvar_consolidado_municipal
        return salvar_consolidado_municipal()
    except Exception as exc:
        return {'ok': False, 'mensagem': f'Não foi possível gerar socio_consolidado_municipal: {exc}'}


def importar_hospitais_retaguarda_ms(ano: int = 2026) -> dict:
    """Importa camada hospitalar/retaguarda a partir do conector oficial disponível.

    A fonte prioritária é a API de Dados Abertos/MS - Hospitais e Leitos; se a rotina local
    baixar ZIP/JSON do MS, o resultado é tratado como camada em validação. Só registros com
    latitude/longitude são liberados para distância hospitalar.
    """
    try:
        from services.api_hospitais_leitos_ms import carregar_hospitais_leitos_ms_mt
        df = carregar_hospitais_leitos_ms_mt(ano=ano)
    except Exception as exc:
        return {'ok': False, 'mensagem': f'Falha ao baixar/normalizar Hospitais e Leitos MS: {exc}', 'linhas': 0}
    if df is None or df.empty:
        return {'ok': False, 'mensagem': 'Hospitais e Leitos retornou vazio.', 'linhas': 0}
    out = _map_municipio(df)
    if 'latitude' not in out.columns or 'longitude' not in out.columns:
        out['latitude'] = pd.NA; out['longitude'] = pd.NA
    out['latitude'] = _num(out['latitude'])
    out['longitude'] = _num(out['longitude'])
    # Mantém só MT e coordenadas plausíveis de MT para a camada geográfica.
    out_geo = out[out['codigo_ibge'].astype(str).str.startswith('51')].copy()
    out_geo = out_geo[out_geo['latitude'].between(-19, -7) & out_geo['longitude'].between(-62, -50)].copy()
    out['status_geocamada'] = 'sem coordenada valida'
    if not out_geo.empty:
        out_geo['status_geocamada'] = 'coordenada valida - requer validacao tecnica'
    with get_connection() as con:
        out.to_sql('hospitais_leitos_ms_raw', con, if_exists='replace', index=False)
        if not out_geo.empty:
            out__dedupe_columns_for_sql(geo).to_sql('geo_hospitais_retaguarda', con, if_exists='replace', index=False)
    return {
        'ok': True,
        'mensagem': 'Base MS importada. A camada de distância hospitalar só usa registros com coordenada plausível.',
        'linhas_raw': int(len(out)),
        'linhas_georreferenciadas': int(len(out_geo)),
        'tabela_raw': 'hospitais_leitos_ms_raw',
        'tabela_geo': 'geo_hospitais_retaguarda' if not out_geo.empty else 'não criada/sem coordenadas válidas',
    }


def importar_inep_microdados_oficial(ano: int = 2024, limite_arquivos: int = 4) -> dict:
    """Baixa microdados do Censo Escolar do INEP e consolida MT por município.

    Observação: é pesado. Por isso fica como ação manual, com cache do ZIP em data/raw/apis.
    """
    url = f'https://download.inep.gov.br/microdados/microdados_censo_escolar_{ano}.zip'
    destino = RAW_DIR / f'microdados_censo_escolar_{ano}.zip'
    try:
        if not destino.exists() or destino.stat().st_size < 1024:
            r = requests.get(url, timeout=180)
            r.raise_for_status()
            destino.write_bytes(r.content)
        frames = []
        with zipfile.ZipFile(destino, 'r') as zf:
            nomes = [n for n in zf.namelist() if n.lower().endswith(('.csv','.txt')) and ('microdados' in n.lower() or 'escola' in n.lower())]
            nomes = sorted(nomes, key=lambda n: zf.getinfo(n).file_size, reverse=True)[:limite_arquivos]
            for nome in nomes:
                raw = zf.open(nome)
                try:
                    chunks = pd.read_csv(raw, sep=';', encoding='latin1', dtype=str, chunksize=50000, low_memory=False)
                    for ch in chunks:
                        uf = _col(ch, 'SG_UF')
                        if uf:
                            mt = ch[ch[uf].astype(str).str.upper().eq('MT')].copy()
                            if not mt.empty:
                                frames.append(mt)
                except Exception:
                    continue
        if not frames:
            return {'ok': False, 'mensagem': 'ZIP do INEP baixado, mas não foi possível localizar registros de MT.', 'linhas': 0}
        raw_mt = pd.concat(frames, ignore_index=True, sort=False).drop_duplicates()
        raw_mt = _map_municipio(raw_mt)
        mun_col = 'municipio'
        ent_col = _col(raw_mt, 'CO_ENTIDADE', 'CO_ESCOLA')
        mat_col = _col(raw_mt, 'QT_MAT_BAS', 'QT_MAT_MED', 'QT_MAT_FUND')
        loc_col = _col(raw_mt, 'TP_LOCALIZACAO')
        dep_col = _col(raw_mt, 'TP_DEPENDENCIA')
        agg = raw_mt[[mun_col]].drop_duplicates().copy()
        g = raw_mt.copy()
        g['_escolas_total'] = 1
        if ent_col:
            escolas = g.groupby('municipio')[ent_col].nunique().reset_index(name='escolas_total')
        else:
            escolas = g.groupby('municipio')['_escolas_total'].sum().reset_index(name='escolas_total')
        agg = escolas
        if mat_col:
            mats = g.assign(_mat=_num(g[mat_col]).fillna(0)).groupby('municipio')['_mat'].sum().reset_index(name='matriculas_total')
            agg = agg.merge(mats, on='municipio', how='left')
        if loc_col:
            g['_rural'] = g[loc_col].astype(str).str.strip().eq('2').astype(int)
            g['_urbana'] = g[loc_col].astype(str).str.strip().eq('1').astype(int)
            if ent_col:
                loc = g.groupby('municipio').agg(escolas_rurais=('_rural','sum'), escolas_urbanas=('_urbana','sum')).reset_index()
            else:
                loc = g.groupby('municipio').agg(escolas_rurais=('_rural','sum'), escolas_urbanas=('_urbana','sum')).reset_index()
            agg = agg.merge(loc, on='municipio', how='left')
        agg['perc_escolas_rurais'] = (pd.to_numeric(agg.get('escolas_rurais'), errors='coerce') / pd.to_numeric(agg.get('escolas_total'), errors='coerce').replace(0, pd.NA) * 100).round(2) if 'escolas_rurais' in agg.columns else pd.NA
        agg['fonte'] = f'INEP Microdados Censo Escolar {ano}'
        agg['ano_referencia'] = str(ano)
        with get_connection() as con:
            raw_mt.to_sql('base_publica_inep_censo_escolar_raw', con, if_exists='replace', index=False)
            agg.to_sql('base_publica_inep_censo_escolar_municipal', con, if_exists='replace', index=False)
        return {'ok': True, 'mensagem': 'INEP importado e consolidado por município.', 'linhas_raw_mt': int(len(raw_mt)), 'municipios': int(agg['municipio'].nunique()), 'tabela': 'base_publica_inep_censo_escolar_municipal'}
    except Exception as exc:
        return {'ok': False, 'mensagem': f'Falha ao importar INEP: {exc}', 'linhas': 0}


# -----------------------------
# V19 - Hospitais/leitos por API e camada de distância hospitalar
# -----------------------------

HOSPITAIS_API_ENDPOINTS = [
    'https://apidadosabertos.saude.gov.br/assistencia-a-saude/hospitais-e-leitos',
    'https://apidadosabertos.saude.gov.br/assistencia-a-saude/hospitais-leitos',
]


def _extrair_lista_json(obj: Any) -> list[dict]:
    """Extrai lista de registros de respostas JSON comuns em APIs públicas."""
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if not isinstance(obj, dict):
        return []
    for key in ['data', 'dados', 'items', 'itens', 'results', 'resultado', 'content', 'records']:
        val = obj.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
        if isinstance(val, dict):
            nested = _extrair_lista_json(val)
            if nested:
                return nested
    # Algumas APIs retornam dicionário indexado por registros.
    vals = [v for v in obj.values() if isinstance(v, dict)]
    if vals and len(vals) > 5:
        return vals
    return []


def _normalizar_hospitalar_api(df: pd.DataFrame, fonte: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    out = _map_municipio(out)
    # UF/MT
    uf_col = _col(out, 'uf', 'sigla_uf', 'SG_UF', 'estado')
    if uf_col:
        out = out[out[uf_col].astype(str).str.upper().str.contains('MT|MATO GROSSO', na=False)].copy()
    else:
        out = out[out.get('codigo_ibge', pd.Series(dtype=str)).astype(str).str.startswith('51')].copy()
    if out.empty:
        return out
    # Colunas padrão.
    cnes_col = _col(out, 'cnes', 'CNES', 'co_cnes', 'codigo_cnes')
    nome_col = _col(out, 'nome_fantasia', 'nome_estabelecimento', 'estabelecimento', 'no_fantasia', 'razao_social')
    tipo_col = _col(out, 'tipo_unidade', 'tipo_estabelecimento', 'ds_tipo_unidade', 'descricao_tipo_unidade', 'natureza')
    end_col = _col(out, 'endereco', 'logradouro', 'ds_logradouro', 'endereco_estabelecimento')
    bairro_col = _col(out, 'bairro', 'no_bairro')
    cep_col = _col(out, 'cep', 'co_cep')
    lat_col = _col(out, 'latitude', 'lat', 'nu_latitude', 'vl_latitude')
    lon_col = _col(out, 'longitude', 'lon', 'lng', 'nu_longitude', 'vl_longitude')
    leitos_col = _col(out, 'leitos_existentes', 'qt_leitos_existentes', 'leitos', 'qtd_leitos')
    leitos_sus_col = _col(out, 'leitos_sus', 'qt_leitos_sus', 'qtd_leitos_sus')
    out['cnes'] = out[cnes_col].astype(str).str.replace(r'\D', '', regex=True) if cnes_col else ''
    out['nome_estabelecimento'] = out[nome_col].astype(str).str.strip() if nome_col else ''
    out['tipo_estabelecimento'] = out[tipo_col].astype(str).str.strip() if tipo_col else ''
    out['endereco'] = out[end_col].astype(str).str.strip() if end_col else ''
    out['bairro'] = out[bairro_col].astype(str).str.strip() if bairro_col else ''
    out['cep'] = out[cep_col].astype(str).str.replace(r'\D', '', regex=True) if cep_col else ''
    out['latitude'] = _num(out[lat_col]) if lat_col else pd.NA
    out['longitude'] = _num(out[lon_col]) if lon_col else pd.NA
    out['leitos_existentes'] = _num(out[leitos_col]) if leitos_col else pd.NA
    out['leitos_sus'] = _num(out[leitos_sus_col]) if leitos_sus_col else pd.NA
    texto = (out['nome_estabelecimento'].fillna('') + ' ' + out['tipo_estabelecimento'].fillna('')).map(_norm_txt)
    padrao = r'hospital|maternidade|pronto atendimento|upa|unidade mista|internacao|internação|urgencia|emergencia|retaguarda'
    out['e_retaguarda_hospitalar'] = texto.str.contains(padrao, regex=True, na=False)
    # Se a API é Hospitais e Leitos, mantém todos, mas marca elegibilidade.
    out = out[out['e_retaguarda_hospitalar'] | out['leitos_existentes'].fillna(0).gt(0) | out['leitos_sus'].fillna(0).gt(0)].copy()
    out['fonte'] = fonte
    out['data_importacao'] = datetime.now().isoformat(timespec='seconds')
    out['status_validacao'] = 'API MS - requer validacao SES/municipio'
    return out


def _baixar_hospitais_leitos_api_ms(ano: int = 2026, max_paginas: int = 30) -> pd.DataFrame:
    """Tenta baixar Hospitais/Leitos pela API de Dados Abertos do MS.

    A API pública pode variar parâmetros/paginação. Por isso a rotina testa combinações
    conservadoras, filtra MT e salva o retorno bruto normalizado.
    """
    registros: list[dict] = []
    fonte_usada = ''
    headers = {'Accept': 'application/json', 'User-Agent': 'APS-MT-Georreferenciamento/1.0'}
    tentativas_base = [
        {'uf': 'MT', 'ano': ano, 'limit': 5000},
        {'sigla_uf': 'MT', 'ano': ano, 'limit': 5000},
        {'estado': 'MT', 'ano': ano, 'limit': 5000},
        {'uf': 'MT', 'limit': 5000},
        {'sigla_uf': 'MT', 'limit': 5000},
        {'limit': 5000},
    ]
    for endpoint in HOSPITAIS_API_ENDPOINTS:
        for params in tentativas_base:
            try:
                r = requests.get(endpoint, params=params, headers=headers, timeout=90)
                if r.status_code >= 400:
                    continue
                obj = r.json()
                lista = _extrair_lista_json(obj)
                if lista:
                    registros.extend(lista)
                    fonte_usada = r.url
                    break
            except Exception:
                continue
        if registros:
            break
    # Tentativa simples de paginação offset/page, se a primeira resposta vier limitada.
    if registros and len(registros) >= 1000 and fonte_usada:
        endpoint = fonte_usada.split('?')[0]
        for page in range(2, max_paginas + 1):
            for params in [{'uf':'MT','ano':ano,'page':page,'limit':5000}, {'uf':'MT','pagina':page,'tamanho':5000}, {'uf':'MT','offset':(page-1)*5000,'limit':5000}]:
                try:
                    r = requests.get(endpoint, params=params, headers=headers, timeout=90)
                    if r.status_code >= 400:
                        continue
                    lista = _extrair_lista_json(r.json())
                    if not lista:
                        continue
                    registros.extend(lista)
                    break
                except Exception:
                    continue
    if not registros:
        return pd.DataFrame()
    df = pd.DataFrame(registros).drop_duplicates()
    return _normalizar_hospitalar_api(df, fonte_usada or HOSPITAIS_API_ENDPOINTS[0])


# Sobrescreve/qualifica a função anterior mantendo o mesmo nome usado pela interface.
def importar_hospitais_retaguarda_ms(ano: int = 2026) -> dict:  # type: ignore[no-redef]
    try:
        df = _baixar_hospitais_leitos_api_ms(ano=ano)
    except Exception as exc:
        return {'ok': False, 'mensagem': f'Falha ao consultar API Hospitais e Leitos/MS: {exc}', 'linhas': 0}
    if df is None or df.empty:
        return {
            'ok': False,
            'mensagem': 'A API Hospitais e Leitos/MS não retornou registros hospitalares de MT nesta tentativa. Verifique conexão/endpoint ou use base CNES/planilha validada como fallback.',
            'linhas': 0,
            'fonte_tentada': ', '.join(HOSPITAIS_API_ENDPOINTS),
        }
    raw = df.copy()
    raw['latitude'] = _num(raw.get('latitude'))
    raw['longitude'] = _num(raw.get('longitude'))
    geo = raw[raw.get('codigo_ibge', pd.Series(dtype=str)).astype(str).str.startswith('51')].copy()
    geo = geo[geo['latitude'].between(-19, -7) & geo['longitude'].between(-62, -50)].copy()
    if not geo.empty:
        geo['status_geocamada'] = 'coordenada valida por API - requer validacao tecnica'
    raw['status_geocamada'] = raw.apply(lambda r: 'coordenada valida por API - requer validacao tecnica' if pd.notna(r.get('latitude')) and pd.notna(r.get('longitude')) and -19 <= float(r.get('latitude')) <= -7 and -62 <= float(r.get('longitude')) <= -50 else 'sem coordenada valida', axis=1)
    with get_connection() as con:
        raw.to_sql('hospitais_leitos_ms_raw', con, if_exists='replace', index=False)
        if not geo.empty:
            _dedupe_columns_for_sql(geo).to_sql('geo_hospitais_retaguarda', con, if_exists='replace', index=False)
    return {
        'ok': True,
        'mensagem': 'Hospitais/Leitos MS importados via API. A camada hospitalar foi ativada apenas para registros com coordenadas plausíveis em MT.',
        'linhas_raw': int(len(raw)),
        'linhas_georreferenciadas': int(len(geo)),
        'municipios_com_retaguarda': int(geo['municipio'].nunique()) if not geo.empty and 'municipio' in geo.columns else 0,
        'tabela_raw': 'hospitais_leitos_ms_raw',
        'tabela_geo': 'geo_hospitais_retaguarda' if not geo.empty else 'não criada/sem coordenadas válidas',
    }


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    import math
    try:
        lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    except Exception:
        return float('nan')
    R = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))


def _classe_hospital_km(km: float) -> str:
    try:
        km = float(km)
    except Exception:
        return 'Não calculado'
    if km <= 20:
        return 'Bom'
    if km <= 50:
        return 'Regular'
    if km <= 100:
        return 'Ruim'
    return 'Crítico'


def calcular_distancias_territorios_hospitais_retaguarda(limite_territorios: int | None = None) -> dict:
    """Calcula distância geodésica entre bairros/localidades/setores e hospital/retaguarda mais próximo."""
    hospitais = read_table('geo_hospitais_retaguarda')
    if hospitais.empty:
        return {'ok': False, 'mensagem': 'A camada geo_hospitais_retaguarda ainda não existe. Importe Hospitais/Leitos MS via API e valide coordenadas.', 'distancias': pd.DataFrame(), 'diagnostico': {}}
    try:
        from services.georreferenciamento_service import calcular_distancias_bairros_localidades_aps
        res = calcular_distancias_bairros_localidades_aps()
        territ = res.get('distancias', pd.DataFrame()).copy()
    except Exception as exc:
        return {'ok': False, 'mensagem': f'Não foi possível carregar territórios/bairros para cálculo hospitalar: {exc}', 'distancias': pd.DataFrame(), 'diagnostico': {}}
    if territ.empty:
        return {'ok': False, 'mensagem': 'Não há territórios/bairros/localidades com coordenadas para calcular distância hospitalar.', 'distancias': pd.DataFrame(), 'diagnostico': {}}
    # Normaliza hospitais.
    lat_h = _col(hospitais, 'latitude', 'lat', 'nu_latitude') or 'latitude'
    lon_h = _col(hospitais, 'longitude', 'lon', 'lng', 'nu_longitude') or 'longitude'
    nome_h = _col(hospitais, 'nome_estabelecimento', 'nome_fantasia', 'estabelecimento') or 'nome_estabelecimento'
    mun_h = _col(hospitais, 'municipio', 'NO_MUNICIPIO', 'NM_MUN') or 'municipio'
    hospitais = hospitais.copy()
    hospitais['lat_hospital'] = _num(hospitais[lat_h]) if lat_h in hospitais.columns else pd.NA
    hospitais['lon_hospital'] = _num(hospitais[lon_h]) if lon_h in hospitais.columns else pd.NA
    hospitais['hospital_mais_proximo'] = hospitais[nome_h].astype(str) if nome_h in hospitais.columns else 'Hospital/retaguarda'
    hospitais['municipio_hospital'] = hospitais[mun_h].astype(str) if mun_h in hospitais.columns else ''
    hospitais = hospitais.dropna(subset=['lat_hospital','lon_hospital'])
    hospitais = hospitais[hospitais['lat_hospital'].between(-19, -7) & hospitais['lon_hospital'].between(-62, -50)].copy()
    if hospitais.empty:
        return {'ok': False, 'mensagem': 'A camada hospitalar existe, mas não tem coordenadas plausíveis de MT.', 'distancias': pd.DataFrame(), 'diagnostico': {}}
    for c in ['latitude','longitude']:
        territ[c] = pd.to_numeric(territ.get(c), errors='coerce')
    territ = territ.dropna(subset=['latitude','longitude']).copy()
    if limite_territorios:
        territ = territ.head(int(limite_territorios)).copy()
    rows = []
    hosp_records = hospitais[['hospital_mais_proximo','municipio_hospital','lat_hospital','lon_hospital','cnes','tipo_estabelecimento','leitos_existentes','leitos_sus'] if 'cnes' in hospitais.columns else ['hospital_mais_proximo','municipio_hospital','lat_hospital','lon_hospital']].to_dict('records')
    for _, t in territ.iterrows():
        best = None
        best_km = float('inf')
        for h in hosp_records:
            km = _haversine_km(t.get('latitude'), t.get('longitude'), h.get('lat_hospital'), h.get('lon_hospital'))
            if pd.notna(km) and km < best_km:
                best_km = km
                best = h
        if best is None:
            continue
        row = t.to_dict()
        row.update(best)
        row['distancia_hospital_km'] = round(best_km, 2)
        row['classe_distancia_hospital'] = _classe_hospital_km(best_km)
        rows.append(row)
    out = pd.DataFrame(rows)
    diag = {
        'territorios_analisados': int(len(out)),
        'hospitais_retaguarda': int(len(hospitais)),
        'distancia_media_km': round(float(out['distancia_hospital_km'].mean()), 2) if not out.empty else None,
        'criticos_ou_ruins': int(out['classe_distancia_hospital'].isin(['Ruim','Crítico']).sum()) if not out.empty else 0,
    }
    return {'ok': True, 'mensagem': 'Distâncias hospitalares calculadas a partir da camada geo_hospitais_retaguarda.', 'distancias': out.sort_values('distancia_hospital_km', ascending=False), 'diagnostico': diag}

# -----------------------------
# V20 - Correção do importador hospitalar: usa recursos oficiais do Portal de Dados Abertos/SUS
# -----------------------------
# Observação: o endpoint interativo da API de Dados Abertos pode não retornar registros diretamente
# quando chamado como rota simples. Por isso esta rotina prioriza os recursos oficiais publicados
# no próprio Portal de Dados Abertos do SUS: Hospitais/Leitos e CNES Estabelecimentos em CSV/ZIP.

HOSPITAIS_LEITOS_CSV_ZIP_URLS = {
    2026: 'https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/Leitos_SUS/Leitos_csv_2026.zip',
    2025: 'https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/Leitos_SUS/Leitos_csv_2025.zip',
    2024: 'https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/Leitos_SUS/Leitos_csv_2024.zip',
}
CNES_ESTABELECIMENTOS_CSV_ZIP_URL = 'https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/CNES/cnes_estabelecimentos_csv.zip'


def _read_csv_zip_filtrando_mt(url: str, cache_name: str, max_files: int = 8, chunksize: int = 60000) -> pd.DataFrame:
    """Baixa ZIP CSV oficial, lê arquivos CSV/TXT e mantém registros de Mato Grosso.

    A função é propositalmente flexível porque os recursos do MS podem variar nomes de
    colunas/arquivos. Ela salva o ZIP em cache local e tenta detectar UF/código IBGE.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    destino = RAW_DIR / cache_name
    headers = {'User-Agent': 'APS-MT-Georreferenciamento/1.0'}
    if not destino.exists() or destino.stat().st_size < 1024:
        r = requests.get(url, headers=headers, timeout=240)
        r.raise_for_status()
        destino.write_bytes(r.content)
    frames: list[pd.DataFrame] = []
    with zipfile.ZipFile(destino, 'r') as zf:
        nomes = [n for n in zf.namelist() if n.lower().endswith(('.csv', '.txt'))]
        nomes = sorted(nomes, key=lambda n: zf.getinfo(n).file_size, reverse=True)[:max_files]
        for nome in nomes:
            for enc in ['utf-8-sig', 'latin1']:
                try:
                    with zf.open(nome) as raw:
                        chunks = pd.read_csv(raw, sep=';', encoding=enc, dtype=str, chunksize=chunksize, low_memory=False)
                        for ch in chunks:
                            ch.columns = [str(c).strip() for c in ch.columns]
                            uf_col = _col(ch, 'uf', 'sigla_uf', 'SG_UF', 'estado', 'UF')
                            cod_col = _col(ch, 'codigo_ibge', 'CO_MUNICIPIO', 'CO_MUNICIPIO_GESTOR', 'CO_MUNICIPIO_IBGE', 'cod_municipio', 'codigo_municipio')
                            mun_col = _col(ch, 'municipio', 'NO_MUNICIPIO', 'NM_MUN', 'nome_municipio')
                            mask = pd.Series(False, index=ch.index)
                            if uf_col:
                                mask = mask | ch[uf_col].astype(str).str.upper().str.contains('MT|MATO GROSSO', na=False)
                            if cod_col:
                                mask = mask | ch[cod_col].astype(str).str.replace(r'\D', '', regex=True).str.startswith('51')
                            if mun_col:
                                nomes_mt = set(_municipios_df()['municipio_norm'])
                                mask = mask | ch[mun_col].map(_norm_txt).isin(nomes_mt)
                            mt = ch[mask].copy()
                            if not mt.empty:
                                mt['arquivo_origem_zip'] = nome
                                frames.append(mt)
                    break
                except UnicodeDecodeError:
                    continue
                except Exception:
                    # tenta próximo arquivo sem derrubar a importação inteira
                    break
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False).drop_duplicates()


def _padronizar_cnes_estabelecimentos(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = _map_municipio(df)
    cnes_col = _col(out, 'cnes', 'CNES', 'CO_CNES', 'co_cnes', 'codigo_cnes')
    nome_col = _col(out, 'nome_fantasia', 'NO_FANTASIA', 'nome_estabelecimento', 'estabelecimento', 'razao_social', 'NO_RAZAO_SOCIAL')
    tipo_col = _col(out, 'tipo_unidade', 'DS_TIPO_UNIDADE', 'tipo_estabelecimento', 'descricao_tipo_unidade', 'CO_TIPO_UNIDADE')
    end_col = _col(out, 'endereco', 'logradouro', 'NO_LOGRADOURO', 'ds_logradouro', 'endereco_estabelecimento')
    num_col = _col(out, 'numero', 'NU_ENDERECO', 'numero_endereco')
    bairro_col = _col(out, 'bairro', 'NO_BAIRRO')
    cep_col = _col(out, 'cep', 'CO_CEP')
    lat_col = _col(out, 'latitude', 'lat', 'NU_LATITUDE', 'vl_latitude')
    lon_col = _col(out, 'longitude', 'lon', 'lng', 'NU_LONGITUDE', 'vl_longitude')
    out['cnes'] = out[cnes_col].astype(str).str.replace(r'\D', '', regex=True).str.zfill(7) if cnes_col else ''
    out['nome_estabelecimento'] = out[nome_col].astype(str).str.strip() if nome_col else ''
    out['tipo_estabelecimento'] = out[tipo_col].astype(str).str.strip() if tipo_col else ''
    out['endereco'] = out[end_col].astype(str).str.strip() if end_col else ''
    if num_col:
        out['endereco'] = (out['endereco'].fillna('') + ', ' + out[num_col].astype(str).str.strip()).str.strip(' ,')
    out['bairro'] = out[bairro_col].astype(str).str.strip() if bairro_col else ''
    out['cep'] = out[cep_col].astype(str).str.replace(r'\D', '', regex=True) if cep_col else ''
    out['latitude'] = _num(out[lat_col]) if lat_col else pd.NA
    out['longitude'] = _num(out[lon_col]) if lon_col else pd.NA
    texto = (out['nome_estabelecimento'].fillna('') + ' ' + out['tipo_estabelecimento'].fillna('')).map(_norm_txt)
    padrao = r'hospital|maternidade|pronto atendimento|upa|unidade mista|internacao|internação|urgencia|emergencia|retaguarda|hospitalar'
    out['e_retaguarda_hospitalar'] = texto.str.contains(padrao, regex=True, na=False)
    out['fonte_cadastro'] = 'CNES Estabelecimentos - Portal Dados Abertos SUS'
    return out


def _padronizar_leitos_ms(df: pd.DataFrame, ano: int) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = _map_municipio(df)
    cnes_col = _col(out, 'cnes', 'CNES', 'CO_CNES', 'co_cnes', 'codigo_cnes')
    nome_col = _col(out, 'nome_fantasia', 'NO_FANTASIA', 'nome_estabelecimento', 'estabelecimento', 'razao_social')
    leitos_col = _col(out, 'leitos_existentes', 'QT_EXISTENTE', 'qt_leitos_existentes', 'leitos', 'qtd_leitos')
    leitos_sus_col = _col(out, 'leitos_sus', 'QT_SUS', 'qt_leitos_sus', 'qtd_leitos_sus')
    tipo_leito_col = _col(out, 'tipo_leito', 'DS_TIPO_LEITO', 'especialidade_leito', 'ds_especialidade')
    out['cnes'] = out[cnes_col].astype(str).str.replace(r'\D', '', regex=True).str.zfill(7) if cnes_col else ''
    out['nome_estabelecimento_leitos'] = out[nome_col].astype(str).str.strip() if nome_col else ''
    out['leitos_existentes'] = _num(out[leitos_col]) if leitos_col else pd.NA
    out['leitos_sus'] = _num(out[leitos_sus_col]) if leitos_sus_col else pd.NA
    out['tipo_leito'] = out[tipo_leito_col].astype(str).str.strip() if tipo_leito_col else ''
    out['ano_referencia'] = str(ano)
    out['fonte_leitos'] = f'Hospitais e Leitos/MS - Leitos CSV {ano}'
    return out


def importar_hospitais_retaguarda_ms(ano: int = 2026) -> dict:  # type: ignore[no-redef]
    """Importa hospitais/retaguarda usando fonte oficial do Portal de Dados Abertos/SUS.

    Estratégia V20:
    1) baixa Leitos CSV do ano selecionado;
    2) baixa CNES Estabelecimentos CSV, atualizado diariamente;
    3) filtra Mato Grosso;
    4) cruza por CNES;
    5) grava raw e camada geo somente quando houver coordenada plausível.
    """
    erros: list[str] = []
    leitos = pd.DataFrame()
    cnes = pd.DataFrame()

    url_leitos = HOSPITAIS_LEITOS_CSV_ZIP_URLS.get(int(ano))
    if url_leitos:
        try:
            leitos_raw = _read_csv_zip_filtrando_mt(url_leitos, f'Leitos_csv_{ano}.zip', max_files=12)
            leitos = _padronizar_leitos_ms(leitos_raw, ano=ano)
        except Exception as exc:
            erros.append(f'Leitos {ano}: {exc}')
    else:
        erros.append(f'Não há URL de Leitos pré-configurada para {ano}.')

    try:
        cnes_raw = _read_csv_zip_filtrando_mt(CNES_ESTABELECIMENTOS_CSV_ZIP_URL, 'cnes_estabelecimentos_csv.zip', max_files=6)
        cnes = _padronizar_cnes_estabelecimentos(cnes_raw)
    except Exception as exc:
        erros.append(f'CNES Estabelecimentos: {exc}')

    if cnes.empty and leitos.empty:
        return {
            'ok': False,
            'mensagem': 'Não foi possível importar hospitais/retaguarda pelas fontes oficiais nesta tentativa.',
            'linhas': 0,
            'erros': erros,
            'fontes_tentadas': [url_leitos, CNES_ESTABELECIMENTOS_CSV_ZIP_URL],
        }

    # Agrega leitos por CNES para evitar múltiplas linhas por tipo de leito.
    leitos_agg = pd.DataFrame()
    if not leitos.empty and 'cnes' in leitos.columns:
        leitos_num_cols = [c for c in ['leitos_existentes', 'leitos_sus'] if c in leitos.columns]
        if leitos_num_cols:
            leitos_agg = leitos.groupby('cnes', dropna=False)[leitos_num_cols].sum(min_count=1).reset_index()
        else:
            leitos_agg = leitos[['cnes']].drop_duplicates()
        nomes = leitos[['cnes','nome_estabelecimento_leitos','municipio']].drop_duplicates(subset=['cnes']) if 'nome_estabelecimento_leitos' in leitos.columns else leitos[['cnes']].drop_duplicates()
        leitos_agg = leitos_agg.merge(nomes, on='cnes', how='left')

    if not cnes.empty:
        # filtra estabelecimentos hospitalares ou com leitos após o cruzamento.
        raw = cnes.copy()
        if not leitos_agg.empty:
            raw = raw.merge(leitos_agg, on='cnes', how='left', suffixes=('', '_leitos'))
            raw['tem_leitos_ms'] = raw[[c for c in ['leitos_existentes','leitos_sus'] if c in raw.columns]].fillna(0).sum(axis=1).gt(0) if any(c in raw.columns for c in ['leitos_existentes','leitos_sus']) else False
        else:
            raw['tem_leitos_ms'] = False
        raw = raw[raw.get('e_retaguarda_hospitalar', pd.Series(False, index=raw.index)).fillna(False) | raw['tem_leitos_ms']].copy()
    else:
        raw = leitos_agg.copy()
        raw['latitude'] = pd.NA
        raw['longitude'] = pd.NA
        raw['tipo_estabelecimento'] = 'Retaguarda hospitalar/leitos - sem CNES cadastral importado'
        raw['nome_estabelecimento'] = raw.get('nome_estabelecimento_leitos', '')

    if raw.empty:
        return {
            'ok': False,
            'mensagem': 'As bases oficiais foram baixadas, mas não foi possível identificar estabelecimentos hospitalares/leitos de MT pelos filtros atuais.',
            'linhas': 0,
            'erros': erros,
            'fontes_tentadas': [url_leitos, CNES_ESTABELECIMENTOS_CSV_ZIP_URL],
        }

    raw['latitude'] = _num(raw.get('latitude'))
    raw['longitude'] = _num(raw.get('longitude'))
    raw['status_validacao'] = 'Fonte oficial MS/CNES - requer validação SES/município'
    raw['fonte'] = 'Portal de Dados Abertos SUS: CNES Estabelecimentos + Hospitais e Leitos'
    raw['data_importacao'] = datetime.now().isoformat(timespec='seconds')

    geo = raw.copy()
    if 'codigo_ibge' in geo.columns:
        geo = geo[geo['codigo_ibge'].astype(str).str.startswith('51')].copy()
    geo = geo[geo['latitude'].between(-19, -7) & geo['longitude'].between(-62, -50)].copy()
    if not geo.empty:
        geo['status_geocamada'] = 'coordenada CNES/MS plausível - validar tecnicamente'
    raw['status_geocamada'] = raw.apply(
        lambda r: 'coordenada CNES/MS plausível - validar tecnicamente'
        if pd.notna(r.get('latitude')) and pd.notna(r.get('longitude')) and -19 <= float(r.get('latitude')) <= -7 and -62 <= float(r.get('longitude')) <= -50
        else 'sem coordenada válida para mapa',
        axis=1,
    )

    with get_connection() as con:
        if not leitos.empty:
            _dedupe_columns_for_sql(leitos).to_sql('hospitais_leitos_ms_raw', con, if_exists='replace', index=False)
        _dedupe_columns_for_sql(raw).to_sql('hospitais_retaguarda_cnes_ms_validacao', con, if_exists='replace', index=False)
        if not geo.empty:
            _dedupe_columns_for_sql(geo).to_sql('geo_hospitais_retaguarda', con, if_exists='replace', index=False)

    return {
        'ok': True,
        'mensagem': 'Hospitais/retaguarda importados por fontes oficiais do SUS. A camada de distância foi ativada apenas para registros com coordenadas plausíveis.',
        'linhas_raw': int(len(raw)),
        'linhas_leitos_raw': int(len(leitos)) if not leitos.empty else 0,
        'linhas_georreferenciadas': int(len(geo)),
        'municipios_com_retaguarda': int(geo['municipio'].nunique()) if not geo.empty and 'municipio' in geo.columns else 0,
        'tabela_raw': 'hospitais_retaguarda_cnes_ms_validacao',
        'tabela_leitos': 'hospitais_leitos_ms_raw' if not leitos.empty else 'não carregada',
        'tabela_geo': 'geo_hospitais_retaguarda' if not geo.empty else 'não criada/sem coordenadas válidas',
        'fontes': [url_leitos, CNES_ESTABELECIMENTOS_CSV_ZIP_URL],
        'avisos': erros,
    }




def _centroides_municipais_base_local() -> pd.DataFrame:
    """Carrega centroides municipais já existentes no banco para fallback preliminar.

    Uso: quando a API de endereço não localiza hospital, ainda é possível criar uma
    camada estadual preliminar pela sede/centroide municipal. Essa coordenada NÃO é
    coordenada do hospital; serve apenas para análise macro de retaguarda por município
    até validação SES/município.
    """
    frames = []
    try:
        m = read_table('municipios')
        if not m.empty:
            lat = _col(m, 'latitude', 'lat')
            lon = _col(m, 'longitude', 'lon', 'lng')
            mun = _col(m, 'municipio', 'nome_municipio')
            cod = _col(m, 'codigo_ibge', 'cod_ibge')
            if lat and lon and mun:
                tmp = pd.DataFrame({
                    'municipio': m[mun].astype(str).str.strip(),
                    'codigo_ibge': m[cod].astype(str).str.replace(r'\D','',regex=True).str[:7] if cod else '',
                    'latitude_centroide_municipal': _num(m[lat]),
                    'longitude_centroide_municipal': _num(m[lon]),
                    'fonte_centroide': 'tabela municipios'
                })
                frames.append(tmp)
    except Exception:
        pass
    try:
        g = read_table('malhas_geograficas_municipais')
        if not g.empty:
            lat = _col(g, 'latitude_centroide', 'latitude', 'lat')
            lon = _col(g, 'longitude_centroide', 'longitude', 'lon')
            mun = _col(g, 'municipio', 'nome_municipio')
            cod = _col(g, 'codigo_ibge', 'cod_ibge')
            if lat and lon and mun:
                tmp = pd.DataFrame({
                    'municipio': g[mun].astype(str).str.strip(),
                    'codigo_ibge': g[cod].astype(str).str.replace(r'\D','',regex=True).str[:7] if cod else '',
                    'latitude_centroide_municipal': _num(g[lat]),
                    'longitude_centroide_municipal': _num(g[lon]),
                    'fonte_centroide': 'malhas_geograficas_municipais'
                })
                frames.append(tmp)
    except Exception:
        pass
    if not frames:
        return pd.DataFrame(columns=['municipio','codigo_ibge','latitude_centroide_municipal','longitude_centroide_municipal','fonte_centroide','municipio_norm'])
    out = pd.concat(frames, ignore_index=True, sort=False)
    out = _map_municipio(out)
    out['latitude_centroide_municipal'] = _num(out['latitude_centroide_municipal'])
    out['longitude_centroide_municipal'] = _num(out['longitude_centroide_municipal'])
    out = out[out['latitude_centroide_municipal'].between(-19,-7) & out['longitude_centroide_municipal'].between(-62,-50)].copy()
    out['municipio_norm'] = out['municipio'].map(_norm_txt)
    return out.drop_duplicates(subset=['municipio_norm'], keep='first')


def _inferir_municipio_hospital(row: pd.Series) -> str:
    """Tenta descobrir o município do hospital a partir de nome, código IBGE ou colunas duplicadas."""
    direto = _row_get_any(row, 'municipio', 'municipio_leitos', 'municipio_cnes', 'no_municipio', 'nome_municipio', 'nm_mun', 'municipio_x', 'municipio_y')
    if direto:
        muni = _map_municipio(pd.DataFrame([{'municipio': direto}]))
        val = str(muni.iloc[0].get('municipio','')).strip() if not muni.empty else direto
        if val:
            return val
    cod = _row_get_any(row, 'codigo_ibge', 'co_municipio', 'co_municipio_gestor', 'co_municipio_ibge', 'cod_municipio', 'codigo_municipio')
    cod = re.sub(r'\D', '', str(cod or ''))[:7]
    if cod:
        muni = _municipios_df()
        mapa = dict(zip(muni['codigo_ibge'], muni['municipio']))
        if cod in mapa:
            return mapa[cod]
    return ''


def _row_get_any(row: pd.Series, *nomes: str) -> str:
    """Lê um valor da linha aceitando variações de nomes de coluna vindas do CNES/MS."""
    if row is None:
        return ''
    mapa = {_norm_txt(c).replace(' ', '_'): c for c in row.index}
    for n in nomes:
        key = _norm_txt(n).replace(' ', '_')
        col = mapa.get(key)
        if col is not None:
            v = row.get(col)
            if pd.notna(v) and str(v).strip().lower() not in {'', 'nan', 'none', 'null'}:
                return str(v).strip()
    # busca parcial: útil para colunas do tipo NO_LOGRADOURO, DS_LOGRADOURO, MUNICIPIO_LEITOS etc.
    for n in nomes:
        termos = [t for t in _norm_txt(n).split() if t]
        for c in row.index:
            cn = _norm_txt(c)
            if termos and all(t in cn for t in termos):
                v = row.get(c)
                if pd.notna(v) and str(v).strip().lower() not in {'', 'nan', 'none', 'null'}:
                    return str(v).strip()
    return ''


def _limpar_endereco_geocode(v: str) -> str:
    txt = '' if v is None else str(v)
    txt = re.sub(r'\b(AVENIDA|AV\.?|RUA|R\.?|RODOVIA|ROD\.?|TRAVESSA|TV\.?|ESTRADA|PRACA|PRAÇA)\b', lambda m: m.group(0), txt, flags=re.I)
    txt = re.sub(r'\s+', ' ', txt).strip(' ,;-')
    return txt


def _hospital_campos_geocode(row: pd.Series) -> dict:
    """Padroniza campos para geocodificação, mesmo quando a base oficial vem com nomes diferentes."""
    nome = _row_get_any(row, 'nome_estabelecimento', 'nome_fantasia', 'no_fantasia', 'estabelecimento', 'razao_social', 'no_razao_social', 'nome')
    logradouro = _row_get_any(row, 'endereco', 'logradouro', 'no_logradouro', 'ds_logradouro', 'endereco_estabelecimento', 'logradouro_estabelecimento')
    numero = _row_get_any(row, 'numero', 'nu_endereco', 'num_endereco', 'numero_endereco', 'nr_endereco')
    bairro = _row_get_any(row, 'bairro', 'no_bairro', 'ds_bairro')
    municipio = _row_get_any(row, 'municipio', 'municipio_leitos', 'municipio_cnes', 'no_municipio', 'nome_municipio', 'nm_mun', 'municipio_x', 'municipio_y')
    if not municipio:
        municipio = _inferir_municipio_hospital(row)
    cep = _row_get_any(row, 'cep', 'co_cep', 'nu_cep')
    cnes = _row_get_any(row, 'cnes', 'co_cnes', 'codigo_cnes', 'cod_cnes')
    logradouro = _limpar_endereco_geocode(logradouro)
    numero = re.sub(r'[^0-9A-Za-z\-/]', '', str(numero or '')).strip()
    cep = re.sub(r'\D', '', str(cep or ''))[:8]
    return {'nome': nome, 'logradouro': logradouro, 'numero': numero, 'bairro': bairro, 'municipio': municipio, 'cep': cep, 'cnes': cnes}


def _consultar_nominatim(params: dict, headers: dict, timeout: int = 25) -> dict | None:
    url = 'https://nominatim.openstreetmap.org/search'
    base = {
        'format': 'json',
        'limit': 1,
        'countrycodes': 'br',
        'addressdetails': 1,
        # viewbox aproximado de Mato Grosso: left,top,right,bottom
        'viewbox': '-62.2,-7.0,-49.8,-18.8',
        'bounded': 1,
    }
    base.update({k: v for k, v in params.items() if v is not None and str(v).strip()})
    resp = requests.get(url, params=base, headers=headers, timeout=timeout)
    if not resp.ok:
        return None
    data = resp.json()
    if not data:
        return None
    return data[0]


def _geocodificar_hospital_multietapas(row: pd.Series, headers: dict) -> dict:
    """Tenta geocodificar por endereço, nome e, por último, sede municipal.

    A sede municipal é marcada como aproximação preliminar, não como coordenada oficial.
    """
    c = _hospital_campos_geocode(row)
    street = ' '.join([c.get('logradouro',''), c.get('numero','')]).strip()
    city = c.get('municipio','')
    name = c.get('nome','')
    bairro = c.get('bairro','')
    cep = c.get('cep','')

    tentativas = []
    if street and city:
        tentativas.append(('endereco_estruturado', {'street': street, 'city': city, 'state': 'Mato Grosso', 'country': 'Brasil'}))
    if street and bairro and city:
        tentativas.append(('endereco_bairro_texto', {'q': f'{street}, {bairro}, {city}, MT, Brasil'}))
    if street and city:
        tentativas.append(('endereco_texto', {'q': f'{street}, {city}, MT, Brasil'}))
    if name and city:
        tentativas.append(('nome_estabelecimento_municipio', {'q': f'{name}, {city}, Mato Grosso, Brasil'}))
    if cep:
        tentativas.append(('cep', {'postalcode': cep, 'country': 'Brasil'}))
    if city:
        tentativas.append(('aproximacao_sede_municipal', {'q': f'{city}, Mato Grosso, Brasil'}))

    ultimo = {}
    for metodo, params in tentativas:
        try:
            item = _consultar_nominatim(params, headers=headers)
        except Exception as exc:
            ultimo = {'erro': str(exc), 'metodo': metodo}
            item = None
        if not item:
            time.sleep(0.35)
            continue
        lat = pd.to_numeric(item.get('lat'), errors='coerce')
        lon = pd.to_numeric(item.get('lon'), errors='coerce')
        if _coords_validas_mt(lat, lon):
            qualidade = 'aproximada_sede_municipal_api' if metodo == 'aproximacao_sede_municipal' else 'inferida_por_endereco_api'
            status = 'coordenada aproximada pela sede municipal - validar SES/município' if metodo == 'aproximacao_sede_municipal' else 'coordenada inferida por endereço via API - validar SES/município'
            return {
                'latitude': float(lat),
                'longitude': float(lon),
                'osm_display_name': item.get('display_name', ''),
                'osm_importance': item.get('importance', None),
                'osm_place_id': item.get('place_id', None),
                'metodo_geocodificacao': metodo,
                'qualidade_coordenada': qualidade,
                'status_geocamada': status,
                'campos_geocodificacao': c,
            }
        ultimo = {'metodo': metodo, 'lat': lat, 'lon': lon, 'display_name': item.get('display_name', '')}
        time.sleep(0.35)
    return {
        'latitude': pd.NA,
        'longitude': pd.NA,
        'metodo_geocodificacao': ultimo.get('metodo', 'nao_localizada'),
        'qualidade_coordenada': 'nao_localizada',
        'status_geocamada': 'não localizado pela API de geocodificação ou fora da faixa plausível de MT',
        'campos_geocodificacao': c,
    }


def _endereco_hospital_para_busca(row: pd.Series) -> str:
    partes = []
    for c in ['nome_estabelecimento', 'endereco', 'bairro', 'municipio']:
        v = row.get(c)
        if pd.notna(v) and str(v).strip() and str(v).strip().lower() not in {'nan', 'none'}:
            partes.append(str(v).strip())
    partes.extend(['Mato Grosso', 'Brasil'])
    return ', '.join(dict.fromkeys(partes))


def _coords_validas_mt(lat: Any, lon: Any) -> bool:
    try:
        lat_f = float(lat)
        lon_f = float(lon)
        return -19 <= lat_f <= -7 and -62 <= lon_f <= -50
    except Exception:
        return False


def geocodificar_hospitais_retaguarda_por_endereco_api(limite: int = 80, pausa_segundos: float = 1.1) -> dict:
    """Geocodifica hospitais/retaguarda por API com múltiplas estratégias.

    V23: além de nome+endereço, padroniza colunas oficiais do CNES/MS e tenta:
    1) endereço estruturado;
    2) endereço textual;
    3) nome do estabelecimento + município;
    4) CEP;
    5) aproximação pela sede municipal, marcada como preliminar.

    A saída continua exigindo validação técnica e não substitui o CNES/SES/município.
    """
    try:
        raw = read_table('hospitais_retaguarda_cnes_ms_validacao')
    except Exception:
        raw = pd.DataFrame()
    if raw.empty:
        return {
            'ok': False,
            'mensagem': 'Não há tabela hospitais_retaguarda_cnes_ms_validacao. Importe Hospitais/Leitos MS antes de geocodificar por endereço.',
            'linhas': 0,
        }

    raw = _dedupe_columns_for_sql(raw)
    raw['latitude'] = _num(raw.get('latitude')) if 'latitude' in raw.columns else pd.NA
    raw['longitude'] = _num(raw.get('longitude')) if 'longitude' in raw.columns else pd.NA
    pend = raw[~(raw['latitude'].between(-19, -7) & raw['longitude'].between(-62, -50))].copy()
    if pend.empty:
        return {'ok': True, 'mensagem': 'Todos os registros já possuem coordenada plausível para MT.', 'linhas_geocodificadas': 0}

    limite = max(1, min(int(limite or 80), 500))
    pend = pend.head(limite).copy()
    resultados = []
    headers = {'User-Agent': 'SES-MT-APS-Georreferenciamento/1.0 (validacao territorial; contato institucional)'}

    for _, row in pend.iterrows():
        registro = row.to_dict()
        campos = _hospital_campos_geocode(row)
        consulta_resumo = ', '.join([v for v in [campos.get('nome'), campos.get('logradouro'), campos.get('numero'), campos.get('bairro'), campos.get('municipio'), 'MT', 'Brasil'] if v])
        registro['consulta_geocodificacao'] = consulta_resumo
        registro['fonte_geocodificacao'] = 'OpenStreetMap/Nominatim - inferida por endereço ou aproximada por sede municipal'
        registro['data_geocodificacao'] = datetime.now().isoformat(timespec='seconds')
        resultado = _geocodificar_hospital_multietapas(row, headers)
        registro['latitude'] = resultado.get('latitude')
        registro['longitude'] = resultado.get('longitude')
        registro['osm_display_name'] = resultado.get('osm_display_name', '')
        registro['osm_importance'] = resultado.get('osm_importance', None)
        registro['osm_place_id'] = resultado.get('osm_place_id', None)
        registro['metodo_geocodificacao'] = resultado.get('metodo_geocodificacao', '')
        registro['qualidade_coordenada'] = resultado.get('qualidade_coordenada', 'nao_localizada')
        registro['status_geocamada'] = resultado.get('status_geocamada', '')
        # guarda campos usados, útil para auditoria sem quebrar SQLite
        campos_usados = resultado.get('campos_geocodificacao', {}) or {}
        for k, v in campos_usados.items():
            registro[f'geo_campo_{k}'] = v
        resultados.append(registro)
        time.sleep(float(pausa_segundos or 1.1))

    geo_api = pd.DataFrame(resultados)
    geo_api['latitude'] = _num(geo_api.get('latitude')) if 'latitude' in geo_api.columns else pd.NA
    geo_api['longitude'] = _num(geo_api.get('longitude')) if 'longitude' in geo_api.columns else pd.NA
    geo_valida = geo_api[geo_api['latitude'].between(-19, -7) & geo_api['longitude'].between(-62, -50)].copy()

    # Junta coordenadas oficiais/plausíveis já existentes com coordenadas inferidas válidas.
    oficiais = raw[raw['latitude'].between(-19, -7) & raw['longitude'].between(-62, -50)].copy()
    if not oficiais.empty:
        if 'qualidade_coordenada' not in oficiais.columns:
            oficiais['qualidade_coordenada'] = 'oficial_ou_plausivel_ms_cnes'
        if 'status_geocamada' not in oficiais.columns:
            oficiais['status_geocamada'] = 'coordenada CNES/MS plausível - validar tecnicamente'

    # Fallback V24: se a API não localizou endereço/hospital, usa o centroide/sede municipal
    # já existente no banco para criar uma camada preliminar estadual. É útil para enxergar
    # vazios de retaguarda por município, mas NÃO substitui coordenada oficial do hospital.
    centroides = _centroides_municipais_base_local()
    geo_sede = pd.DataFrame()
    if not geo_api.empty and not centroides.empty:
        tmp = geo_api.copy()
        if 'municipio' not in tmp.columns or tmp['municipio'].astype(str).str.strip().eq('').all():
            tmp['municipio'] = tmp.apply(_inferir_municipio_hospital, axis=1)
        else:
            tmp['municipio'] = tmp['municipio'].where(tmp['municipio'].astype(str).str.strip().ne(''), tmp.apply(_inferir_municipio_hospital, axis=1))
        tmp = _map_municipio(tmp)
        tmp['municipio_norm'] = tmp['municipio'].map(_norm_txt)
        tmp = tmp.merge(centroides[['municipio_norm','latitude_centroide_municipal','longitude_centroide_municipal','fonte_centroide']], on='municipio_norm', how='left')
        precisa_fallback = ~(tmp['latitude'].between(-19, -7) & tmp['longitude'].between(-62, -50))
        geo_sede = tmp[precisa_fallback & tmp['latitude_centroide_municipal'].notna() & tmp['longitude_centroide_municipal'].notna()].copy()
        if not geo_sede.empty:
            geo_sede['latitude'] = geo_sede['latitude_centroide_municipal']
            geo_sede['longitude'] = geo_sede['longitude_centroide_municipal']
            geo_sede['qualidade_coordenada'] = 'aproximada_centroide_municipal_base_local'
            geo_sede['metodo_geocodificacao'] = 'fallback_centroide_municipal_base_local'
            geo_sede['status_geocamada'] = 'coordenada preliminar pela sede/centroide municipal - NÃO é coordenada do hospital; validar endereço na SES/município'

    geo_final = pd.concat([oficiais, geo_valida, geo_sede], ignore_index=True, sort=False)
    if not geo_final.empty:
        if 'cnes' in geo_final.columns:
            geo_final = geo_final.drop_duplicates(subset=['cnes'], keep='first')
        else:
            geo_final = geo_final.drop_duplicates(subset=['latitude', 'longitude'], keep='first')

    with get_connection() as con:
        _dedupe_columns_for_sql(geo_api).to_sql('hospitais_geocodificacao_api_validacao', con, if_exists='replace', index=False)
        if not geo_final.empty:
            _dedupe_columns_for_sql(geo_final).to_sql('geo_hospitais_retaguarda', con, if_exists='replace', index=False)

    n_precisa = int((geo_valida.get('qualidade_coordenada', pd.Series(dtype=str)).astype(str).eq('inferida_por_endereco_api')).sum()) if not geo_valida.empty else 0
    n_sede_api = int((geo_valida.get('qualidade_coordenada', pd.Series(dtype=str)).astype(str).eq('aproximada_sede_municipal_api')).sum()) if not geo_valida.empty else 0
    n_sede_local = int(len(geo_sede)) if 'geo_sede' in locals() and not geo_sede.empty else 0
    msg = 'Geocodificação por API concluída. Coordenadas foram gravadas como preliminares e exigem validação técnica.'
    if n_sede_local and not len(geo_valida):
        msg = 'A API não encontrou coordenadas por endereço, mas foi criada camada preliminar pela sede/centroide municipal para análise estadual. Validar endereços reais antes de decisão final.'
    return {
        'ok': True,
        'mensagem': msg,
        'registros_processados': int(len(geo_api)),
        'geocodificados_validos_mt': int(len(geo_valida)),
        'validos_por_endereco': n_precisa,
        'validos_por_sede_municipal_api': n_sede_api,
        'validos_por_centroide_municipal_local': n_sede_local,
        'total_geo_hospitais_retaguarda': int(len(geo_final)),
        'tabela_validacao': 'hospitais_geocodificacao_api_validacao',
        'tabela_geo': 'geo_hospitais_retaguarda' if not geo_final.empty else 'não criada/sem coordenadas válidas',
        'cautela': 'Coordenadas inferidas por endereço ou sede municipal não substituem validação oficial do CNES/SES/município.',
    }


# V25 - Cadastro manual/validado de hospitais e retaguarda
HOSPITAIS_CADASTRO_MANUAL_COLS = [
    'cnes', 'nome_estabelecimento', 'municipio', 'codigo_ibge', 'tipo_estabelecimento',
    'endereco_original', 'bairro_original', 'cep_original', 'telefone',
    'leitos_existentes', 'leitos_sus',
    'endereco_corrigido', 'bairro_corrigido', 'zona',
    'latitude_manual', 'longitude_manual',
    'tipo_retaguarda_validado', 'atende_sus_validado', 'fonte_coordenada',
    'status_validacao', 'usar_no_mapa', 'observacao_tecnica', 'informado_por', 'data_atualizacao'
]


def _serie_vazia(n: int, valor='') -> pd.Series:
    return pd.Series([valor] * n)


def _pegar_coluna_texto(df: pd.DataFrame, *candidatas: str, default: str = '') -> pd.Series:
    c = _col(df, *candidatas)
    if c and c in df.columns:
        return df[c].fillna('').astype(str)
    return _serie_vazia(len(df), default)


def _pegar_coluna_num(df: pd.DataFrame, *candidatas: str) -> pd.Series:
    c = _col(df, *candidatas)
    if c and c in df.columns:
        return _num(df[c])
    return pd.Series([pd.NA] * len(df), dtype='float64')


def _normalizar_bool_mapa(v) -> bool:
    txt = _norm_txt(v)
    if txt in {'1', 'sim', 's', 'true', 'verdadeiro', 'yes', 'y'}:
        return True
    if txt in {'0', 'nao', 'n', 'false', 'falso', 'no'}:
        return False
    return False


def preparar_cadastro_manual_hospitais_retaguarda() -> dict:
    """Cria/atualiza tabela editável para validação manual de hospitais/retaguarda.

    A tabela preserva a base oficial importada, mas cria campos próprios para a SES/municípios
    informarem latitude/longitude, endereço corrigido e status de validação. A camada de mapa
    só deve usar registros manualmente validados ou marcados para uso.
    """
    try:
        raw = read_table('hospitais_retaguarda_cnes_ms_validacao')
    except Exception:
        raw = pd.DataFrame()

    if raw.empty:
        base = pd.DataFrame(columns=HOSPITAIS_CADASTRO_MANUAL_COLS)
    else:
        raw = _dedupe_columns_for_sql(raw)
        base = pd.DataFrame()
        base['cnes'] = _pegar_coluna_texto(raw, 'cnes', 'CO_CNES', 'co_cnes', 'CNES')
        base['nome_estabelecimento'] = _pegar_coluna_texto(raw, 'nome_estabelecimento', 'NO_FANTASIA', 'no_fantasia', 'NO_RAZAO_SOCIAL', 'nome', 'estabelecimento')
        base['municipio'] = raw.apply(_inferir_municipio_hospital, axis=1) if not raw.empty else pd.Series(dtype=str)
        base['codigo_ibge'] = _pegar_coluna_texto(raw, 'codigo_ibge', 'CO_MUNICIPIO', 'co_municipio', 'IBGE', 'cod_municipio')
        base['tipo_estabelecimento'] = _pegar_coluna_texto(raw, 'tipo_estabelecimento', 'DS_TIPO_UNIDADE', 'tipo_unidade', 'tipo')
        logradouro = _pegar_coluna_texto(raw, 'endereco', 'endereco_original', 'NO_LOGRADOURO', 'DS_LOGRADOURO', 'logradouro')
        numero = _pegar_coluna_texto(raw, 'NU_ENDERECO', 'numero', 'num_endereco')
        complemento = _pegar_coluna_texto(raw, 'DS_COMPLEMENTO', 'complemento')
        base['endereco_original'] = (logradouro + ' ' + numero + ' ' + complemento).str.replace(r'\s+', ' ', regex=True).str.strip()
        base['bairro_original'] = _pegar_coluna_texto(raw, 'bairro', 'NO_BAIRRO', 'no_bairro')
        base['cep_original'] = _pegar_coluna_texto(raw, 'cep', 'CO_CEP', 'nu_cep')
        base['telefone'] = _pegar_coluna_texto(raw, 'telefone', 'NU_TELEFONE', 'telefone_estabelecimento')
        base['leitos_existentes'] = _pegar_coluna_num(raw, 'leitos_existentes', 'QT_EXISTENTE', 'qt_existente')
        base['leitos_sus'] = _pegar_coluna_num(raw, 'leitos_sus', 'QT_SUS', 'qt_sus')

        # Campos editáveis/validáveis, inicialmente em branco.
        base['endereco_corrigido'] = ''
        base['bairro_corrigido'] = ''
        base['zona'] = ''
        base['latitude_manual'] = pd.NA
        base['longitude_manual'] = pd.NA
        base['tipo_retaguarda_validado'] = ''
        base['atende_sus_validado'] = ''
        base['fonte_coordenada'] = ''
        base['status_validacao'] = 'pendente_validacao'
        base['usar_no_mapa'] = False
        base['observacao_tecnica'] = 'Coordenada ausente nas bases abertas. Preencher manualmente/validar com SES ou município.'
        base['informado_por'] = ''
        base['data_atualizacao'] = ''

    # Preserva preenchimentos manuais já existentes, quando houver.
    try:
        existente = read_table('hospitais_retaguarda_cadastro_editavel')
    except Exception:
        existente = pd.DataFrame()
    if not existente.empty and not base.empty:
        existente = _dedupe_columns_for_sql(existente)
        chave = 'cnes' if 'cnes' in existente.columns and 'cnes' in base.columns else None
        if chave:
            manuais = [c for c in HOSPITAIS_CADASTRO_MANUAL_COLS if c in existente.columns and c not in {'nome_estabelecimento','municipio','tipo_estabelecimento','endereco_original','bairro_original','cep_original','telefone','leitos_existentes','leitos_sus'}]
            base = base.merge(existente[[chave] + manuais].drop_duplicates(subset=[chave]), on=chave, how='left', suffixes=('', '_existente'))
            for c in manuais:
                ce = f'{c}_existente'
                if ce in base.columns:
                    base[c] = base[ce].where(base[ce].notna() & base[ce].astype(str).str.strip().ne(''), base[c])
                    base.drop(columns=[ce], inplace=True)

    for c in HOSPITAIS_CADASTRO_MANUAL_COLS:
        if c not in base.columns:
            base[c] = pd.NA if c in {'latitude_manual','longitude_manual','leitos_existentes','leitos_sus'} else ''
    base = base[HOSPITAIS_CADASTRO_MANUAL_COLS].copy()

    with get_connection() as con:
        _dedupe_columns_for_sql(base).to_sql('hospitais_retaguarda_cadastro_editavel', con, if_exists='replace', index=False)

    return {
        'ok': True,
        'mensagem': 'Cadastro manual de hospitais/retaguarda preparado. Preencha latitude/longitude e valide os registros antes de ativar no mapa.',
        'linhas': int(len(base)),
        'tabela': 'hospitais_retaguarda_cadastro_editavel',
        'campos_editaveis': ['endereco_corrigido','bairro_corrigido','zona','latitude_manual','longitude_manual','tipo_retaguarda_validado','atende_sus_validado','fonte_coordenada','status_validacao','usar_no_mapa','observacao_tecnica','informado_por','data_atualizacao']
    }


def importar_cadastro_manual_hospitais_df(df: pd.DataFrame) -> dict:
    """Importa planilha CSV/XLSX preenchida com coordenadas validadas dos hospitais."""
    if df is None or df.empty:
        return {'ok': False, 'mensagem': 'Planilha vazia ou inválida.', 'linhas': 0}
    df = _dedupe_columns_for_sql(df.copy())
    # Normaliza nomes de colunas para o modelo.
    mapa = {}
    for c in df.columns:
        n = _norm_txt(c).replace(' ', '_')
        mapa[c] = n
    df = df.rename(columns=mapa)
    aliases = {
        'latitude': 'latitude_manual', 'lat': 'latitude_manual', 'lat_manual': 'latitude_manual',
        'longitude': 'longitude_manual', 'lon': 'longitude_manual', 'lng': 'longitude_manual', 'long': 'longitude_manual', 'long_manual': 'longitude_manual',
        'nome': 'nome_estabelecimento', 'hospital': 'nome_estabelecimento', 'estabelecimento': 'nome_estabelecimento',
        'endereco': 'endereco_corrigido', 'bairro': 'bairro_corrigido',
        'validacao': 'status_validacao', 'status': 'status_validacao',
        'usar': 'usar_no_mapa', 'usar_mapa': 'usar_no_mapa', 'ativo_mapa': 'usar_no_mapa',
    }
    for a, b in aliases.items():
        if a in df.columns and b not in df.columns:
            df[b] = df[a]
    for c in HOSPITAIS_CADASTRO_MANUAL_COLS:
        if c not in df.columns:
            df[c] = pd.NA if c in {'latitude_manual','longitude_manual','leitos_existentes','leitos_sus'} else ''
    df['latitude_manual'] = _num(df['latitude_manual'])
    df['longitude_manual'] = _num(df['longitude_manual'])
    df['usar_no_mapa'] = df['usar_no_mapa'].apply(_normalizar_bool_mapa)
    df['data_atualizacao'] = df['data_atualizacao'].where(df['data_atualizacao'].astype(str).str.strip().ne(''), datetime.now().isoformat(timespec='seconds'))
    df = df[HOSPITAIS_CADASTRO_MANUAL_COLS].copy()

    # Mescla com cadastro existente, preservando base oficial e atualizando campos manuais.
    try:
        atual = read_table('hospitais_retaguarda_cadastro_editavel')
    except Exception:
        atual = pd.DataFrame()
    if not atual.empty and 'cnes' in df.columns and 'cnes' in atual.columns:
        atual = _dedupe_columns_for_sql(atual)
        atual = atual[~atual['cnes'].astype(str).isin(df['cnes'].astype(str))].copy()
        final = pd.concat([atual, df], ignore_index=True, sort=False)
    else:
        final = df
    for c in HOSPITAIS_CADASTRO_MANUAL_COLS:
        if c not in final.columns:
            final[c] = ''
    final = final[HOSPITAIS_CADASTRO_MANUAL_COLS]

    with get_connection() as con:
        _dedupe_columns_for_sql(final).to_sql('hospitais_retaguarda_cadastro_editavel', con, if_exists='replace', index=False)
    ativacao = ativar_geo_hospitais_validados()
    return {
        'ok': True,
        'mensagem': 'Planilha manual importada para o cadastro editável de hospitais/retaguarda.',
        'linhas_importadas': int(len(df)),
        'linhas_total_cadastro': int(len(final)),
        'ativacao_geo': ativacao,
    }


def ativar_geo_hospitais_validados() -> dict:
    """Gera geo_hospitais_retaguarda apenas com registros manuais validados/plausíveis."""
    try:
        cad = read_table('hospitais_retaguarda_cadastro_editavel')
    except Exception:
        return {'ok': False, 'mensagem': 'Tabela hospitais_retaguarda_cadastro_editavel não encontrada. Prepare o cadastro manual primeiro.', 'linhas_geo': 0}
    if cad.empty:
        return {'ok': False, 'mensagem': 'Cadastro manual de hospitais está vazio.', 'linhas_geo': 0}
    cad = _dedupe_columns_for_sql(cad)
    for c in HOSPITAIS_CADASTRO_MANUAL_COLS:
        if c not in cad.columns:
            cad[c] = ''
    cad['latitude'] = _num(cad['latitude_manual'])
    cad['longitude'] = _num(cad['longitude_manual'])
    cad['usar_no_mapa_bool'] = cad['usar_no_mapa'].apply(_normalizar_bool_mapa)
    status_norm = cad['status_validacao'].map(_norm_txt)
    validado_status = status_norm.str.contains('validado|corrigido|ses|municipio|oficial', regex=True, na=False)
    geo = cad[cad['latitude'].between(-19, -7) & cad['longitude'].between(-62, -50) & (cad['usar_no_mapa_bool'] | validado_status)].copy()
    if geo.empty:
        return {
            'ok': False,
            'mensagem': 'Nenhum hospital possui latitude/longitude plausível para MT e status/uso habilitado para mapa.',
            'linhas_geo': 0,
            'orientacao': 'Preencha latitude_manual, longitude_manual, status_validacao e usar_no_mapa=Sim para ativar a camada.'
        }
    geo['hospital_mais_proximo'] = geo['nome_estabelecimento']
    geo['municipio_hospital'] = geo['municipio']
    geo['lat_hospital'] = geo['latitude']
    geo['lon_hospital'] = geo['longitude']
    geo['qualidade_coordenada'] = geo['fonte_coordenada'].where(geo['fonte_coordenada'].astype(str).str.strip().ne(''), 'manual_validada_ses_municipio')
    geo['status_geocamada'] = geo['status_validacao'].where(geo['status_validacao'].astype(str).str.strip().ne(''), 'validado_manual')
    geo['origem_camada'] = 'cadastro_manual_hospitais_retaguarda'
    with get_connection() as con:
        _dedupe_columns_for_sql(geo).to_sql('geo_hospitais_retaguarda', con, if_exists='replace', index=False)
    return {
        'ok': True,
        'mensagem': 'Camada geo_hospitais_retaguarda ativada com hospitais/retaguarda validados manualmente.',
        'linhas_geo': int(len(geo)),
        'municipios_com_retaguarda': int(geo['municipio'].nunique()) if 'municipio' in geo.columns else 0,
        'tabela_geo': 'geo_hospitais_retaguarda'
    }


def resumo_cadastro_manual_hospitais() -> dict:
    try:
        cad = read_table('hospitais_retaguarda_cadastro_editavel')
    except Exception:
        return {'existe': False, 'linhas': 0, 'validados': 0, 'com_coordenada': 0}
    if cad.empty:
        return {'existe': True, 'linhas': 0, 'validados': 0, 'com_coordenada': 0}
    cad['lat'] = _num(cad.get('latitude_manual')) if 'latitude_manual' in cad.columns else pd.NA
    cad['lon'] = _num(cad.get('longitude_manual')) if 'longitude_manual' in cad.columns else pd.NA
    com_coord = cad['lat'].between(-19, -7) & cad['lon'].between(-62, -50)
    status = cad.get('status_validacao', pd.Series([''] * len(cad))).map(_norm_txt)
    return {
        'existe': True,
        'linhas': int(len(cad)),
        'com_coordenada': int(com_coord.sum()),
        'validados': int(status.str.contains('validado|corrigido|ses|municipio|oficial', regex=True, na=False).sum()),
        'habilitados_mapa': int(cad.get('usar_no_mapa', pd.Series([False]*len(cad))).apply(_normalizar_bool_mapa).sum()),
        'tabela': 'hospitais_retaguarda_cadastro_editavel'
    }


def carregar_hospitais_cadastro_editavel(filtro_municipio: str = '', busca: str = '', status: str = 'Todos', limite: int = 300) -> pd.DataFrame:
    """Carrega o cadastro hospitalar editável para uso em formulário interno do Streamlit.

    Evita depender de CSV para validação municipal: o técnico pode selecionar um hospital,
    preencher coordenadas e salvar diretamente no banco.
    """
    try:
        df = read_table('hospitais_retaguarda_cadastro_editavel')
    except Exception:
        return pd.DataFrame(columns=HOSPITAIS_CADASTRO_MANUAL_COLS)
    if df.empty:
        return df
    df = _dedupe_columns_for_sql(df)
    for c in HOSPITAIS_CADASTRO_MANUAL_COLS:
        if c not in df.columns:
            df[c] = pd.NA if c in {'latitude_manual','longitude_manual','leitos_existentes','leitos_sus'} else ''
    df = df[HOSPITAIS_CADASTRO_MANUAL_COLS].copy()
    if filtro_municipio and filtro_municipio != 'Todos' and 'municipio' in df.columns:
        df = df[df['municipio'].astype(str).str.upper().eq(str(filtro_municipio).upper())].copy()
    if busca:
        b = _norm_txt(busca)
        if b:
            mask = pd.Series(False, index=df.index)
            for c in ['cnes','nome_estabelecimento','municipio','endereco_original','endereco_corrigido','bairro_original','bairro_corrigido']:
                if c in df.columns:
                    mask = mask | df[c].astype(str).map(_norm_txt).str.contains(b, na=False)
            df = df[mask].copy()
    if status and status != 'Todos':
        stn = _norm_txt(status)
        if status == 'Com coordenada':
            lat = _num(df.get('latitude_manual'))
            lon = _num(df.get('longitude_manual'))
            df = df[lat.between(-19, -7) & lon.between(-62, -50)].copy()
        elif status == 'Pendentes':
            sn = df.get('status_validacao', pd.Series([''] * len(df))).map(_norm_txt)
            lat = _num(df.get('latitude_manual'))
            lon = _num(df.get('longitude_manual'))
            df = df[~(sn.str.contains('validado|corrigido|ses|municipio|oficial', regex=True, na=False) & lat.between(-19, -7) & lon.between(-62, -50))].copy()
        else:
            df = df[df.get('status_validacao', pd.Series([''] * len(df))).map(_norm_txt).str.contains(stn, na=False)].copy()
    df['_label_edicao'] = (
        df['municipio'].fillna('').astype(str) + ' — ' +
        df['nome_estabelecimento'].fillna('').astype(str) + ' — CNES ' +
        df['cnes'].fillna('').astype(str)
    )
    return df.head(int(limite)).copy()


def salvar_edicao_hospital_retaguarda(cnes: str, nome_estabelecimento: str, campos: dict) -> dict:
    """Atualiza um registro do cadastro editável diretamente pelo formulário do sistema."""
    try:
        df = read_table('hospitais_retaguarda_cadastro_editavel')
    except Exception:
        prep = preparar_cadastro_manual_hospitais_retaguarda()
        try:
            df = read_table('hospitais_retaguarda_cadastro_editavel')
        except Exception:
            return {'ok': False, 'mensagem': 'Não foi possível preparar/carregar o cadastro editável de hospitais.', 'preparacao': prep}
    if df.empty:
        return {'ok': False, 'mensagem': 'Cadastro editável de hospitais vazio. Prepare o cadastro após importar Hospitais/Leitos MS.'}
    df = _dedupe_columns_for_sql(df)
    for c in HOSPITAIS_CADASTRO_MANUAL_COLS:
        if c not in df.columns:
            df[c] = pd.NA if c in {'latitude_manual','longitude_manual','leitos_existentes','leitos_sus'} else ''
    cnes_txt = '' if cnes is None else str(cnes).strip()
    nome_txt = '' if nome_estabelecimento is None else str(nome_estabelecimento).strip()
    mask = pd.Series(False, index=df.index)
    if cnes_txt and 'cnes' in df.columns:
        mask = df['cnes'].astype(str).str.strip().eq(cnes_txt)
    if not mask.any() and nome_txt and 'nome_estabelecimento' in df.columns:
        mask = df['nome_estabelecimento'].astype(str).str.strip().eq(nome_txt)
    if not mask.any():
        return {'ok': False, 'mensagem': 'Registro não localizado no cadastro editável.', 'cnes': cnes_txt, 'nome': nome_txt}
    idx = df[mask].index[0]
    atualizaveis = {
        'endereco_corrigido','bairro_corrigido','zona','latitude_manual','longitude_manual',
        'tipo_retaguarda_validado','atende_sus_validado','fonte_coordenada','status_validacao',
        'usar_no_mapa','observacao_tecnica','informado_por','data_atualizacao'
    }
    for k, v in (campos or {}).items():
        if k in atualizaveis:
            df.loc[idx, k] = v
    if not str(df.loc[idx, 'data_atualizacao'] or '').strip():
        df.loc[idx, 'data_atualizacao'] = datetime.now().isoformat(timespec='seconds')
    # Normalizações mínimas
    df['latitude_manual'] = _num(df['latitude_manual'])
    df['longitude_manual'] = _num(df['longitude_manual'])
    df['usar_no_mapa'] = df['usar_no_mapa'].apply(_normalizar_bool_mapa)
    df = df[HOSPITAIS_CADASTRO_MANUAL_COLS].copy()
    with get_connection() as con:
        _dedupe_columns_for_sql(df).to_sql('hospitais_retaguarda_cadastro_editavel', con, if_exists='replace', index=False)
    ativacao = ativar_geo_hospitais_validados()
    return {
        'ok': True,
        'mensagem': 'Hospital/retaguarda atualizado no cadastro editável. A camada de mapa foi reprocessada com registros validados.',
        'cnes': cnes_txt,
        'nome': nome_txt,
        'ativacao_geo': ativacao,
    }


def estatisticas_fluxo_validacao_hospitais() -> dict:
    """Resumo para orientar fluxo sem CSV: pendências, coordenadas e validação."""
    base = resumo_cadastro_manual_hospitais()
    try:
        df = read_table('hospitais_retaguarda_cadastro_editavel')
    except Exception:
        return base
    if df.empty:
        return base
    df = _dedupe_columns_for_sql(df)
    lat = _num(df.get('latitude_manual'))
    lon = _num(df.get('longitude_manual'))
    status = df.get('status_validacao', pd.Series([''] * len(df))).map(_norm_txt)
    base.update({
        'municipios_cadastro': int(df.get('municipio', pd.Series(dtype=str)).astype(str).replace('', pd.NA).dropna().nunique()),
        'pendentes': int((~(lat.between(-19, -7) & lon.between(-62, -50))).sum()),
        'validados_com_coordenada': int((lat.between(-19, -7) & lon.between(-62, -50) & status.str.contains('validado|corrigido|ses|municipio|oficial', regex=True, na=False)).sum()),
    })
    return base
