from __future__ import annotations

import math
from datetime import datetime

import pandas as pd

from database.connection import get_connection
from database.queries import read_table


def _read(table: str) -> pd.DataFrame:
    try:
        df = read_table(table)
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    if df is None or df.empty:
        return None
    lower = {str(c).lower(): c for c in df.columns}
    for c in candidates:
        if c in df.columns:
            return c
        if c.lower() in lower:
            return lower[c.lower()]
    return None


def _series(df: pd.DataFrame, candidates: list[str], default='') -> pd.Series:
    c = _col(df, candidates)
    if c is None:
        return pd.Series([default] * len(df), index=df.index)
    return df[c]


def _num_series(df: pd.DataFrame, candidates: list[str]) -> pd.Series:
    return pd.to_numeric(_series(df, candidates, default=pd.NA), errors='coerce')


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    try:
        lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
        if any(math.isnan(v) for v in [lat1, lon1, lat2, lon2]):
            return float('nan')
    except Exception:
        return float('nan')
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlambda/2)**2
    return round(2*r*math.asin(math.sqrt(a)), 2)


def classificar_distancia_ubs(km) -> str:
    try:
        km = float(km)
    except Exception:
        return 'Sem cálculo'
    if km <= 3:
        return 'Bom'
    if km <= 7:
        return 'Regular'
    if km <= 15:
        return 'Ruim'
    return 'Crítico'


def classificar_distancia_hospital(km) -> str:
    try:
        km = float(km)
    except Exception:
        return 'Base hospitalar pendente'
    if km <= 20:
        return 'Bom'
    if km <= 50:
        return 'Regular'
    if km <= 100:
        return 'Ruim'
    return 'Crítico'


def classificar_score(score) -> str:
    try:
        score = float(score)
    except Exception:
        return 'Sem cálculo'
    if score <= 25:
        return 'Bom'
    if score <= 50:
        return 'Regular'
    if score <= 75:
        return 'Ruim'
    return 'Crítico'


def _score_distancia_ubs(km) -> float:
    try:
        return max(0.0, min(100.0, float(km)/25.0*100.0))
    except Exception:
        return 0.0


def _score_distancia_hospital(km) -> float:
    try:
        return max(0.0, min(100.0, float(km)/150.0*100.0))
    except Exception:
        return 0.0


def _score_ruralidade(row) -> float:
    txt = ' '.join(str(row.get(c, '')) for c in ['tipo_territorio','camada','zona','situacao','localidade','nome_area','modalidade']).lower()
    score = 0.0
    if any(x in txt for x in ['rural','assent','indígen','indigena','quilomb','ribeir','aldeia','terra indígena']):
        score += 70
    if 'assent' in txt:
        score += 20
    if 'indig' in txt or 'aldeia' in txt:
        score += 20
    if 'quilomb' in txt or 'ribeir' in txt:
        score += 20
    return min(100.0, score)


def _score_vulnerabilidade_municipal(df_mds: pd.DataFrame, municipio: str) -> float:
    if df_mds is None or df_mds.empty or 'municipio' not in df_mds.columns:
        return 0.0
    rec = df_mds[df_mds['municipio'].astype(str).str.upper().str.strip() == str(municipio).upper().strip()]
    if rec.empty:
        return 0.0
    r = rec.iloc[0]
    for c in ['score_vulnerabilidade_mds','score_vulnerabilidade_social','pct_populacao_cadunico','pct_populacao_bolsa_familia']:
        if c in rec.columns and pd.notna(r.get(c)):
            try:
                return min(100.0, max(0.0, float(r.get(c))))
            except Exception:
                pass
    return 0.0


def _norm_mun(s: pd.Series) -> pd.Series:
    return s.astype(str).str.upper().str.strip()


def _carregar_socio_consolidado() -> pd.DataFrame:
    """Camada socioeducacional preferencial.

    Prioridade: socio_consolidado_municipal, depois socio_indicadores_municipais,
    depois MDS puro. Assim os mapas passam a usar a base consolidada gerada no V17.
    """
    for tabela in ['socio_consolidado_municipal', 'socio_indicadores_municipais', 'mds_cadunico_bolsa_familia_municipal']:
        df = _read(tabela)
        if df is not None and not df.empty:
            mun = _col(df, ['municipio','NO_MUNICIPIO','NM_MUN','nome_municipio'])
            if mun and mun != 'municipio':
                df = df.rename(columns={mun:'municipio'})
            if 'municipio' in df.columns:
                df['municipio_key'] = _norm_mun(df['municipio'])
                df['fonte_socio_consolidada'] = tabela
                return df
    return pd.DataFrame()


def _valor_socio(socio: pd.DataFrame, municipio: str, candidatos: list[str], default=pd.NA):
    if socio is None or socio.empty or 'municipio_key' not in socio.columns:
        return default
    rec = socio[socio['municipio_key'] == str(municipio).upper().strip()]
    if rec.empty:
        return default
    r = rec.iloc[0]
    for c in candidatos:
        if c in rec.columns and pd.notna(r.get(c)):
            return r.get(c)
    return default


def _num_socio(socio: pd.DataFrame, municipio: str, candidatos: list[str], default=0.0) -> float:
    v = _valor_socio(socio, municipio, candidatos, default)
    try:
        if pd.isna(v):
            return default
        return float(str(v).replace('%','').replace('.','').replace(',','.'))
    except Exception:
        return default


def _score_saneamento_municipal(socio: pd.DataFrame, municipio: str) -> float:
    return min(100.0, max(0.0, _num_socio(socio, municipio, ['perc_esgotamento_inadequado_proxy','pct_esgotamento_inadequado','saneamento_inadequado_pct'], 0.0)))


def _score_escolaridade_municipal(socio: pd.DataFrame, municipio: str) -> float:
    # Quando houver alfabetização/analfabetismo, usa como risco direto; senão usa ruralidade escolar como proxy de barreira socioeducacional.
    analf = _num_socio(socio, municipio, ['perc_analfabetismo_15mais','taxa_analfabetismo','analfabetismo_15_mais'], -1)
    if analf >= 0:
        return min(100.0, max(0.0, analf * 3))
    rur = _num_socio(socio, municipio, ['perc_escolas_rurais','pct_escolas_rurais'], 0.0)
    sem_internet = _num_socio(socio, municipio, ['pct_escolas_sem_internet','perc_escolas_sem_internet'], 0.0)
    return min(100.0, max(0.0, rur * 0.6 + sem_internet * 0.4))


def _append_frame(frames, df: pd.DataFrame, tipo: str, nome_candidates: list[str], lat_candidates: list[str], lon_candidates: list[str], extra_candidates: list[str] | None = None):
    if df is None or df.empty:
        return
    lat = _num_series(df, lat_candidates)
    lon = _num_series(df, lon_candidates)
    tmp = pd.DataFrame({
        'municipio': _series(df, ['municipio','NM_MUN','NO_MUNICIPIO','nome_municipio'], ''),
        'codigo_ibge': _series(df, ['codigo_ibge','CD_MUN','CO_MUNICIPIO','cod_mun'], ''),
        'nome_area': _series(df, nome_candidates, ''),
        'tipo_territorio': tipo,
        'lat': lat,
        'lon': lon,
    })
    for c in (extra_candidates or []):
        real = _col(df, [c])
        if real is not None:
            tmp[c] = df[real]
    tmp = tmp.dropna(subset=['lat','lon'])
    if tmp.empty:
        return
    tmp = tmp[(tmp['lat'].between(-19.5, -7.0)) & (tmp['lon'].between(-62.5, -49.0))]
    if not tmp.empty:
        frames.append(tmp)


def _carregar_territorios_basicos() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    _append_frame(frames, _read('dados_mt_assentamentos'), 'Assentamento rural', ['nome_assentamento','nome','nome_area'], ['latitude_centroide','lat','latitude'], ['longitude_centroide','lon','longitude'], ['area_ha','modalidade','situacao'])
    _append_frame(frames, _read('dados_mt_terras_indigenas'), 'Terra indígena / território tradicional', ['nome_terra_indigena','nome','nome_area'], ['latitude_centroide','lat','latitude'], ['longitude_centroide','lon','longitude'], ['area_ha','etnia','situacao'])
    _append_frame(frames, _read('base_publica_ibge_setores_basico'), 'Bairro/localidade/setor censitário', ['NM_BAIRRO','NM_NU','CD_SETOR','nome_area'], ['lat','latitude','latitude_centroide','centroide_lat'], ['lon','lng','longitude','longitude_centroide','centroide_lon'], ['SITUACAO','AREA_KM2'])
    if not frames:
        return pd.DataFrame(columns=['municipio','codigo_ibge','nome_area','tipo_territorio','lat','lon'])
    out = pd.concat(frames, ignore_index=True, sort=False)
    out['nome_area'] = out['nome_area'].fillna('').astype(str)
    out.loc[out['nome_area'].str.strip().eq(''), 'nome_area'] = out['tipo_territorio']
    return out.drop_duplicates(subset=['municipio','nome_area','tipo_territorio','lat','lon']).reset_index(drop=True)


def _carregar_ubs() -> pd.DataFrame:
    frames=[]
    for table in ['estabelecimentos_saude','cnes_estabelecimentos_gerais']:
        df=_read(table)
        if df.empty:
            continue
        tmp=pd.DataFrame({
            'municipio': _series(df,['municipio','NM_MUN','NO_MUNICIPIO'],''),
            'codigo_ibge': _series(df,['codigo_ibge','CD_MUN','CO_MUNICIPIO'],''),
            'cnes': _series(df,['cnes','CNES','co_cnes'],''),
            'nome_unidade': _series(df,['nome_unidade','nome_estabelecimento','NO_FANTASIA','no_fantasia'],''),
            'tipo_unidade': _series(df,['tipo_unidade','tipo_estabelecimento'],''),
            'endereco': _series(df,['endereco','endereco_original','logradouro'],''),
            'latitude': _num_series(df,['latitude','lat','latitude_corrigida']),
            'longitude': _num_series(df,['longitude','lon','lng','longitude_corrigida']),
        }).dropna(subset=['latitude','longitude'])
        tmp=tmp[(tmp['latitude'].between(-19.5,-7.0)) & (tmp['longitude'].between(-62.5,-49.0))]
        if not tmp.empty:
            frames.append(tmp)
    if not frames:
        return pd.DataFrame(columns=['municipio','codigo_ibge','cnes','nome_unidade','tipo_unidade','endereco','latitude','longitude'])
    return pd.concat(frames, ignore_index=True, sort=False).drop_duplicates().reset_index(drop=True)


def _carregar_hospitais() -> pd.DataFrame:
    """Carrega APENAS camada hospitalar qualificada.

    Importante: não inferir hospital por nome dentro de bases gerais de UBS/estabelecimentos,
    porque isso pode gerar poucos pontos falsos e produzir mapa enganoso. A distância hospitalar
    só deve ser calculada quando houver tabela específica validada pela SES/ERS ou CNES hospitalar
    qualificado com coordenadas.
    """
    tabelas_validas = [
        'hospitais_referencia_georreferenciados',
        'cnes_hospitais_qualificados',
        'rede_hospitalar_mt_georreferenciada',
        'rede_hospitalar_mt',
        'estabelecimentos_hospitalares_geo',
        'geo_hospitais_retaguarda',
    ]
    frames=[]
    for table in tabelas_validas:
        df=_read(table)
        if df.empty:
            continue
        tmp=pd.DataFrame({
            'municipio': _series(df,['municipio','NM_MUN','NO_MUNICIPIO'],'').astype(str),
            'codigo_ibge': _series(df,['codigo_ibge','CD_MUN','CO_MUNICIPIO'],'').astype(str),
            'cnes': _series(df,['cnes','CNES','co_cnes'],'').astype(str),
            'nome_unidade': _series(df,['nome_unidade','nome_estabelecimento','NO_FANTASIA','hospital','nome_hospital'],'').astype(str),
            'tipo_unidade': _series(df,['tipo_unidade','tipo_estabelecimento','tipo_hospital','natureza'],'').astype(str),
            'endereco': _series(df,['endereco','logradouro'],'').astype(str),
            'latitude': _num_series(df,['latitude','lat','latitude_corrigida','LATITUDE']),
            'longitude': _num_series(df,['longitude','lon','lng','longitude_corrigida','LONGITUDE']),
            'fonte_camada': table,
        }).dropna(subset=['latitude','longitude'])
        tmp=tmp[(tmp['latitude'].between(-19.5,-7.0)) & (tmp['longitude'].between(-62.5,-49.0))]
        if not tmp.empty:
            frames.append(tmp)
    if not frames:
        return pd.DataFrame(columns=['municipio','codigo_ibge','cnes','nome_unidade','tipo_unidade','endereco','latitude','longitude','fonte_camada'])
    return pd.concat(frames, ignore_index=True, sort=False).drop_duplicates().reset_index(drop=True)

def _nearest(points: pd.DataFrame, refs: pd.DataFrame, prefix: str) -> pd.DataFrame:
    out = points.copy()
    if out.empty or refs.empty:
        out[f'{prefix}_mais_proximo']=''
        out[f'cnes_{prefix}_mais_proximo']=''
        out[f'municipio_{prefix}_mais_proximo']=''
        out[f'distancia_{prefix}_km']=pd.NA
        out[f'lat_{prefix}']=pd.NA
        out[f'lon_{prefix}']=pd.NA
        return out
    ref_records=refs.to_dict('records')
    vals=[]
    for _, p in out.iterrows():
        best=None; best_d=float('inf')
        for r in ref_records:
            d=_haversine_km(p.get('lat'), p.get('lon'), r.get('latitude'), r.get('longitude'))
            if pd.notna(d) and d < best_d:
                best_d=d; best=r
        vals.append((best or {}, best_d if math.isfinite(best_d) else pd.NA))
    out[f'{prefix}_mais_proximo']=[v[0].get('nome_unidade','') for v in vals]
    out[f'cnes_{prefix}_mais_proximo']=[v[0].get('cnes','') for v in vals]
    out[f'municipio_{prefix}_mais_proximo']=[v[0].get('municipio','') for v in vals]
    out[f'distancia_{prefix}_km']=[v[1] for v in vals]
    out[f'lat_{prefix}']=[v[0].get('latitude',pd.NA) for v in vals]
    out[f'lon_{prefix}']=[v[0].get('longitude',pd.NA) for v in vals]
    return out


def _decisao_area(r) -> str:
    ubs=r.get('classe_distancia_ubs')
    hosp=r.get('classe_distancia_hospital')
    rural=float(r.get('score_ruralidade') or 0)>=70
    vuln=float(r.get('score_vulnerabilidade_mds') or 0)>=60
    if ubs == 'Crítico' and rural and vuln:
        return 'Prioridade máxima: estudar UBS satélite, equipe itinerante, rota rural e transporte sanitário.'
    if ubs in ['Crítico','Ruim'] and rural:
        return 'Prioridade alta: estruturar rota rural, unidade móvel, ponto de apoio ou reorganização territorial.'
    if hosp == 'Crítico':
        return 'Prioridade hospitalar: avaliar fluxo regional, transporte sanitário e retaguarda de urgência.'
    if vuln and ubs in ['Regular','Ruim','Crítico']:
        return 'Prioridade social: busca ativa APS + CRAS + Vigilância + Educação.'
    if ubs == 'Bom':
        return 'Manter monitoramento territorial e validar adscrição/microáreas.'
    return 'Validar em campo e definir resposta proporcional ao acesso real.'


def _transporte_area(r) -> str:
    txt=' '.join([str(r.get('nome_area','')), str(r.get('tipo_territorio',''))]).lower()
    d_ubs=pd.to_numeric(pd.Series([r.get('distancia_ubs_km')]), errors='coerce').iloc[0]
    d_h=pd.to_numeric(pd.Series([r.get('distancia_hospital_km')]), errors='coerce').iloc[0]
    if any(x in txt for x in ['rio','ribeir','água','agua','fluvial']):
        return 'Veículo aquático / rota fluvial'
    if pd.notna(d_ubs) and d_ubs >= 30:
        return 'Micro-ônibus/ônibus sanitário ou rota programada'
    if pd.notna(d_h) and d_h >= 100:
        return 'Transporte sanitário regional para retaguarda hospitalar'
    if any(x in txt for x in ['assent','rural','indig','quilomb']):
        return 'Veículo 4x4, equipe itinerante ou rota rural APS'
    if pd.notna(d_ubs) and d_ubs >= 15:
        return 'Veículo leve/equipe volante conforme validação local'
    return 'Sem indicação automática forte'


def montar_plano_diretor_geo() -> dict:
    territorios=_carregar_territorios_basicos()
    ubs=_carregar_ubs()
    hospitais=_carregar_hospitais()
    socio=_carregar_socio_consolidado()
    mds=_read('mds_cadunico_bolsa_familia_municipal')
    inep=_read('base_publica_inep_censo_escolar_municipal')
    base=_read('base_municipal_consolidada')
    dist=territorios.copy()
    if not dist.empty:
        dist=_nearest(dist, ubs, 'ubs')
        dist['classe_distancia_ubs']=dist['distancia_ubs_km'].apply(classificar_distancia_ubs)
        if not hospitais.empty:
            dist=_nearest(dist, hospitais, 'hospital')
            dist['classe_distancia_hospital']=dist['distancia_hospital_km'].apply(classificar_distancia_hospital)
            dist['score_distancia_hospital']=dist['distancia_hospital_km'].apply(_score_distancia_hospital)
            dist['metodologia_idt_aps']='Com camada hospitalar qualificada: UBS 25%, hospital 15%, ruralidade 20%, vulnerabilidade social 20%, saneamento 10%, escolaridade/educação 10%.'
        else:
            dist['hospital_mais_proximo']='Camada hospitalar pendente'
            dist['cnes_hospital_mais_proximo']=''
            dist['municipio_hospital_mais_proximo']=''
            dist['distancia_hospital_km']=pd.NA
            dist['lat_hospital']=pd.NA
            dist['lon_hospital']=pd.NA
            dist['classe_distancia_hospital']='Não calculado — importar base hospitalar qualificada'
            dist['score_distancia_hospital']=pd.NA
            dist['metodologia_idt_aps']='Sem camada hospitalar qualificada: IDT-APS recalculado com UBS 35%, ruralidade 25%, vulnerabilidade social 25%, saneamento 10% e escolaridade/educação 5%.'
        dist['score_distancia_ubs']=dist['distancia_ubs_km'].apply(_score_distancia_ubs)
        dist['score_ruralidade']=dist.apply(_score_ruralidade, axis=1)
        # A partir do V18, a vulnerabilidade vem preferencialmente da socio_consolidado_municipal.
        dist['score_vulnerabilidade_mds']=dist['municipio'].apply(lambda m: _score_vulnerabilidade_municipal(socio if not socio.empty else mds, m))
        dist['score_saneamento']=dist['municipio'].apply(lambda m: _score_saneamento_municipal(socio, m))
        dist['score_escolaridade_educacao']=dist['municipio'].apply(lambda m: _score_escolaridade_municipal(socio, m))
        # Injeta campos socioeducacionais úteis para hover, ranking e relatórios.
        for campo, candidatos in {
            'pct_populacao_cadunico':['pct_populacao_cadunico','ia_pct_cadunico'],
            'pct_populacao_bolsa_familia':['pct_populacao_bolsa_familia','ia_pct_bolsa_familia'],
            'perc_esgotamento_inadequado_proxy':['perc_esgotamento_inadequado_proxy','pct_esgotamento_inadequado'],
            'domicilios_esgotamento_inadequado_proxy':['domicilios_esgotamento_inadequado_proxy'],
            'perc_escolas_rurais':['perc_escolas_rurais','pct_escolas_rurais'],
            'escolas_rurais':['escolas_rurais'],
            'escolas_indigenas':['escolas_indigenas'],
            'escolas_quilombolas':['escolas_quilombolas'],
            'populacao_indigena':['populacao_indigena','pessoas_indigenas_2022'],
            'populacao_quilombola':['populacao_quilombola','pessoas_quilombolas_2022'],
            'fonte_socio_consolidada':['fonte_socio_consolidada'],
        }.items():
            dist[campo]=dist['municipio'].apply(lambda m, cand=candidatos: _valor_socio(socio, m, cand, pd.NA))
        if not hospitais.empty:
            dist['score_idt_aps']=(dist['score_distancia_ubs']*.25 + dist['score_distancia_hospital'].fillna(0)*.15 + dist['score_ruralidade']*.20 + dist['score_vulnerabilidade_mds']*.20 + dist['score_saneamento']*.10 + dist['score_escolaridade_educacao']*.10).round(1)
        else:
            dist['score_idt_aps']=(dist['score_distancia_ubs']*.35 + dist['score_ruralidade']*.25 + dist['score_vulnerabilidade_mds']*.25 + dist['score_saneamento']*.10 + dist['score_escolaridade_educacao']*.05).round(1)
        dist['classificacao_idt_aps']=dist['score_idt_aps'].apply(classificar_score)
        dist['decisao_sugerida']=dist.apply(_decisao_area, axis=1)
        dist['tipo_transporte_sugerido']=dist.apply(_transporte_area, axis=1)
        dist['alerta_populacao_tradicional']=dist['tipo_territorio'].astype(str).str.contains('indígen|indigena|tradicional|quilomb|ribeir', case=False, regex=True, na=False)
        # reforça alerta quando a camada socioeducacional trouxer população/escola indígena ou quilombola.
        for c in ['populacao_indigena','populacao_quilombola','escolas_indigenas','escolas_quilombolas']:
            if c in dist.columns:
                dist['alerta_populacao_tradicional'] = dist['alerta_populacao_tradicional'] | (pd.to_numeric(dist[c], errors='coerce').fillna(0) > 0)
        dist=dist.sort_values(['score_idt_aps','distancia_ubs_km'], ascending=False)
    resumo=_resumo_municipal(dist, mds, inep, base, socio)
    return {'territorios':dist, 'ubs':ubs, 'hospitais':hospitais, 'resumo_municipal':resumo, 'relatorio':_montar_relatorio_geo(dist,resumo,hospitais,socio), 'metadados':{'territorios_mapeados':len(dist), 'ubs_com_coordenada':len(ubs), 'hospitais_com_coordenada':len(hospitais), 'hospital_base_status': 'qualificada' if not hospitais.empty else 'pendente', 'socio_consolidado_status': 'carregada' if not socio.empty else 'pendente', 'inep_base_status': 'carregada' if not inep.empty else 'não carregada', 'base_municipal_status': 'carregada' if not base.empty else 'não carregada', 'gerado_em':datetime.now().isoformat(timespec='seconds')}}

def _resumo_municipal(dist: pd.DataFrame, mds: pd.DataFrame, inep: pd.DataFrame, base: pd.DataFrame, socio: pd.DataFrame | None = None) -> pd.DataFrame:
    if dist is None or dist.empty:
        return pd.DataFrame()
    g=dist.groupby('municipio', dropna=False).agg(territorios_mapeados=('nome_area','count'), distancia_media_ubs_km=('distancia_ubs_km','mean'), maior_distancia_ubs_km=('distancia_ubs_km','max'), score_idt_aps=('score_idt_aps','mean'), areas_criticas=('classificacao_idt_aps', lambda s:int((s=='Crítico').sum())), areas_ruins=('classificacao_idt_aps', lambda s:int((s=='Ruim').sum())), areas_rurais=('score_ruralidade', lambda s:int((pd.to_numeric(s, errors='coerce')>=70).sum())), territorios_tradicionais=('alerta_populacao_tradicional', lambda s:int(pd.Series(s).fillna(False).sum()))).reset_index()
    g['classificacao_idt_aps']=g['score_idt_aps'].apply(classificar_score)
    g['prioridade_plano_diretor']=g.apply(lambda r: 'Urgente' if r['classificacao_idt_aps']=='Crítico' or r['areas_criticas']>0 else ('Alta' if r['classificacao_idt_aps']=='Ruim' or r['areas_ruins']>0 else ('Média' if r['classificacao_idt_aps']=='Regular' else 'Baixa')), axis=1)
    for source, cols in [(socio,['score_vulnerabilidade_mds','score_vulnerabilidade_social','classificacao_mds','classificacao_vulnerabilidade_mds','pct_populacao_cadunico','pct_populacao_bolsa_familia','perc_esgotamento_inadequado_proxy','domicilios_esgotamento_inadequado_proxy','perc_escolas_rurais','escolas_rurais','escolas_total','escolas_indigenas','escolas_quilombolas','populacao_indigena','populacao_quilombola','fonte_socio_consolidada']), (mds,['score_vulnerabilidade_mds','classificacao_vulnerabilidade_mds','pct_populacao_cadunico','pct_populacao_bolsa_familia']), (inep,['perc_escolas_rurais','escolas_rurais','escolas_total']), (base,['regiao_saude','populacao','total_ubs','total_equipes_aps','pop_por_equipe','pop_por_ubs','populacao_indigena','populacao_quilombola','escolas_indigenas','escolas_quilombolas'])]:
        if source is not None and not source.empty and 'municipio' in source.columns:
            keep=['municipio']+[c for c in cols if c in source.columns]
            if len(keep)>1:
                g=g.merge(source[keep].drop_duplicates('municipio'), on='municipio', how='left')
    return g.sort_values(['prioridade_plano_diretor','score_idt_aps'], ascending=[False,False])


def _montar_relatorio_geo(dist: pd.DataFrame, resumo: pd.DataFrame, hospitais: pd.DataFrame, socio: pd.DataFrame | None = None) -> str:
    if resumo is None or resumo.empty:
        return 'A base territorial ainda não possui pontos suficientes para gerar relatório consolidado.'
    crit=int(resumo.get('areas_criticas',pd.Series(dtype=float)).sum()) if 'areas_criticas' in resumo.columns else 0
    rur=int(resumo.get('areas_rurais',pd.Series(dtype=float)).sum()) if 'areas_rurais' in resumo.columns else 0
    hosp_txt='A base hospitalar georreferenciada ainda precisa ser importada/qualificada para medir distâncias hospitalares de forma robusta.' if hospitais is None or hospitais.empty else f'Foram localizados {len(hospitais)} pontos hospitalares/retaguarda de camada qualificada para cálculo preliminar.'
    socio_txt='A camada socioeducacional consolidada está carregada e foi incorporada ao IDT-APS.' if socio is not None and not socio.empty else 'A camada socioeducacional consolidada ainda está pendente; a leitura social usa apenas bases parciais disponíveis.'
    top=resumo.head(5)['municipio'].astype(str).tolist()
    return ('Frase orientadora: “Quero ver visualmente onde estão os vazios assistenciais e quem está desassistido.”\n\n'
            f'O painel territorial consolidou {len(dist) if dist is not None else 0} pontos territoriais com coordenadas, incluindo assentamentos e territórios tradicionais quando disponíveis. '
            f'Foram identificadas {crit} áreas críticas pelo Índice de Desassistência Territorial da APS e {rur} áreas com forte componente rural/tradicional. {hosp_txt} {socio_txt}\n\n'
            f'Municípios que exigem leitura prioritária no Plano Diretor: {", ".join(top) if top else "não calculado"}. A decisão deve cruzar distância, ruralidade, vulnerabilidade, saneamento, escolaridade, capacidade APS e fluxos regionais.')


def garantir_tabela_cadastro_territorial() -> dict:
    sql="""CREATE TABLE IF NOT EXISTS ubs_cadastro_editavel (id INTEGER PRIMARY KEY AUTOINCREMENT, municipio TEXT, codigo_ibge TEXT, cnes TEXT, nome_estabelecimento TEXT, tipo_estabelecimento TEXT, endereco_original TEXT, endereco_corrigido TEXT, bairro TEXT, zona TEXT, latitude_original REAL, longitude_original REAL, latitude_corrigida REAL, longitude_corrigida REAL, fonte_coordenada TEXT, status_validacao TEXT, observacao_tecnica TEXT, informado_por TEXT, data_atualizacao TEXT);"""
    try:
        with get_connection() as conn:
            conn.execute(sql)
            conn.commit()
            n=conn.execute('SELECT COUNT(*) FROM ubs_cadastro_editavel').fetchone()[0]
        return {'ok': True, 'registros': int(n)}
    except Exception as e:
        return {'ok': False, 'registros': 0, 'erro': str(e)}


def carregar_cadastro_ubs_editavel() -> pd.DataFrame:
    garantir_tabela_cadastro_territorial()
    return _read('ubs_cadastro_editavel')
