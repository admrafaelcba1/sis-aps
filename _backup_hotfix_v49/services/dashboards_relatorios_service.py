from __future__ import annotations

import re
from typing import Any

import pandas as pd

from database.connection import get_connection
from database.queries import read_table


# -----------------------------------------------------------------------------
# Utilidades de banco e normalização
# -----------------------------------------------------------------------------

def _tables() -> set[str]:
    try:
        with get_connection() as conn:
            return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    except Exception:
        return set()


def _read_first(names: list[str]) -> pd.DataFrame:
    tabs = _tables()
    for name in names:
        if name in tabs:
            try:
                df = read_table(name)
                if df is not None and not df.empty:
                    return df
            except Exception:
                continue
    return pd.DataFrame()


def _norm_col(c: str) -> str:
    c = str(c or '').strip().lower()
    c = (
        c.replace('á', 'a').replace('à', 'a').replace('ã', 'a').replace('â', 'a')
         .replace('é', 'e').replace('ê', 'e')
         .replace('í', 'i')
         .replace('ó', 'o').replace('ô', 'o').replace('õ', 'o')
         .replace('ú', 'u').replace('ç', 'c')
    )
    c = re.sub(r'[^a-z0-9_]+', '_', c)
    return re.sub(r'_+', '_', c).strip('_')


def _normalizar_municipio_col(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    mapa = {}
    for c in out.columns:
        n = _norm_col(c)
        if n in ['municipio', 'nome_municipio', 'no_municipio', 'nm_mun', 'nm_municipio', 'municipio_nome']:
            mapa[c] = 'municipio'
        elif n in ['codigo_ibge', 'cod_ibge', 'co_municipio', 'ibge', 'cod_municipio', 'cod_mun']:
            mapa[c] = 'codigo_ibge'
        elif n in ['regiao_saude', 'regiao_de_saude', 'nome_regiao_saude', 'regional_saude']:
            mapa[c] = 'regiao_saude'
        elif n in ['ano', 'nu_ano', 'ano_referencia', 'ano_ref']:
            mapa[c] = 'ano'
        elif n in ['mes', 'competencia_mes']:
            mapa[c] = 'mes'
    if mapa:
        out = out.rename(columns=mapa)
    out = out.loc[:, ~out.columns.duplicated()]
    return out


def _get_numeric(df: pd.DataFrame, nomes: list[str], default: float = 0.0) -> pd.Series:
    for n in nomes:
        if n in df.columns:
            return pd.to_numeric(df[n], errors='coerce')
    return pd.Series(default, index=df.index, dtype='float64')


def _get_text(df: pd.DataFrame, nomes: list[str], default: str = '') -> pd.Series:
    for n in nomes:
        if n in df.columns:
            return df[n].astype(str)
    return pd.Series(default, index=df.index, dtype='object')


def _norm_score(s: pd.Series, reverse: bool = False) -> pd.Series:
    x = pd.to_numeric(s, errors='coerce')
    if x.dropna().empty:
        return pd.Series(0.0, index=s.index)
    mn, mx = float(x.min()), float(x.max())
    if mx == mn:
        out = pd.Series(50.0, index=s.index)
    else:
        out = (x - mn) / (mx - mn) * 100
    if reverse:
        out = 100 - out
    return out.fillna(0).clip(0, 100)


def classificar_situacao(nota: float) -> str:
    """Classificação decisória final, sem termos genéricos como 'monitoramento'."""
    try:
        v = float(nota)
    except Exception:
        return 'Não classificado'
    if v >= 80:
        return 'Crítico'
    if v >= 60:
        return 'Péssimo'
    if v >= 40:
        return 'Ruim'
    if v >= 20:
        return 'Regular'
    return 'Bom'


def _faixa_score(valor: float) -> str:
    try:
        v = float(valor)
    except Exception:
        return 'sem dado suficiente'
    if v >= 80:
        return 'crítico'
    if v >= 60:
        return 'péssimo'
    if v >= 40:
        return 'ruim'
    if v >= 20:
        return 'regular'
    return 'bom'


def _decisao(row: pd.Series) -> str:
    geral = float(row.get('score_prioridade', 0) or 0)
    acesso = float(row.get('score_acesso_territorial', 0) or 0)
    social = float(row.get('score_vulnerabilidade_social', 0) or 0)
    capacidade = float(row.get('score_fragilidade_aps', 0) or 0)
    rural = float(row.get('score_ruralidade', 0) or 0)
    saneamento = float(row.get('score_saneamento', 0) or 0)
    if geral >= 80:
        return 'Resposta urgente: plano territorial específico, validação local imediata e pactuação regional de investimento.'
    if acesso >= 70 and rural >= 60:
        return 'Estratégia rural prioritária: equipe itinerante, rota programada, transporte sanitário e ponto de apoio quando necessário.'
    if acesso >= 70 and capacidade >= 60:
        return 'Avaliar expansão/reorganização da oferta: nova UBS, UBS satélite, unidade móvel ou redistribuição de equipes.'
    if capacidade >= 70:
        return 'Ampliar/reorganizar equipes APS, revisar CNES/INE/carga horária e validar suficiência de UBS frente à população.'
    if social >= 70 or saneamento >= 70:
        return 'Busca ativa intersetorial: APS, CRAS, Vigilância, Educação, saneamento e educação em saúde territorializada.'
    if geral >= 40:
        return 'Intervenção técnica programada: validar causas, priorizar microterritórios e acompanhar mensalmente.'
    return 'Manter acompanhamento preventivo, validar dados periodicamente e preservar capacidade de resposta.'


# -----------------------------------------------------------------------------
# Base integrada
# -----------------------------------------------------------------------------

def montar_base_dashboard_integrado() -> pd.DataFrame:
    mun = _normalizar_municipio_col(_read_first(['base_municipal_consolidada', 'municipios']))
    if mun.empty or 'municipio' not in mun.columns:
        return pd.DataFrame()

    keep = [c for c in [
        'municipio', 'codigo_ibge', 'regiao_saude', 'populacao', 'area_km2', 'densidade_hab_km2',
        'total_ubs', 'total_equipes_aps', 'total_profissionais_aps', 'pop_por_equipe', 'pop_por_ubs',
        'latitude', 'longitude'
    ] if c in mun.columns]
    base = mun[keep].drop_duplicates(subset=['municipio']).copy()

    # Socioeducacional consolidado
    socio = _normalizar_municipio_col(_read_first(['socio_consolidado_municipal', 'socio_indicadores_municipais', 'mds_cadunico_bolsa_familia_municipal']))
    if not socio.empty and 'municipio' in socio.columns:
        socio_cols = [c for c in socio.columns if c not in ['codigo_ibge', 'regiao_saude']]
        base = base.merge(socio[socio_cols].drop_duplicates(subset=['municipio']), on='municipio', how='left', suffixes=('', '_socio'))

    # Georreferenciamento/IDT se existir
    geo = _normalizar_municipio_col(_read_first(['idt_aps_municipal', 'plano_diretor_geo_municipal', 'geo_idt_aps_municipal', 'base_municipal_geografica']))
    if not geo.empty and 'municipio' in geo.columns:
        geo_cols = [c for c in geo.columns if c not in ['codigo_ibge', 'regiao_saude']]
        base = base.merge(geo[geo_cols].drop_duplicates(subset=['municipio']), on='municipio', how='left', suffixes=('', '_geo'))

    # MDS fallback
    mds = _normalizar_municipio_col(_read_first(['mds_cadunico_bolsa_familia_municipal']))
    if not mds.empty and 'municipio' in mds.columns:
        mds_cols = [c for c in mds.columns if c not in base.columns or c == 'municipio']
        if len(mds_cols) > 1:
            base = base.merge(mds[mds_cols].drop_duplicates(subset=['municipio']), on='municipio', how='left', suffixes=('', '_mds'))

    # Equipes/estabelecimentos se base consolidada não tiver
    if 'total_equipes_aps' not in base.columns or _get_numeric(base, ['total_equipes_aps'], 0).fillna(0).sum() == 0:
        equipes = _normalizar_municipio_col(_read_first(['equipes_aps']))
        if not equipes.empty and 'municipio' in equipes.columns:
            eq = equipes.groupby('municipio').size().reset_index(name='equipes_aps_calculadas')
            base = base.merge(eq, on='municipio', how='left')
    if 'total_ubs' not in base.columns or _get_numeric(base, ['total_ubs'], 0).fillna(0).sum() == 0:
        ubs = _normalizar_municipio_col(_read_first(['estabelecimentos_saude', 'cnes_estabelecimentos_gerais']))
        if not ubs.empty and 'municipio' in ubs.columns:
            uu = ubs.groupby('municipio').size().reset_index(name='estabelecimentos_saude_calculados')
            base = base.merge(uu, on='municipio', how='left')

    pop = _get_numeric(base, ['populacao', 'populacao_total', 'populacao_ibge'], 0).replace(0, pd.NA)
    equipes = _get_numeric(base, ['total_equipes_aps', 'equipes_aps_calculadas', 'equipes_aps', 'n_equipes'], 0).replace(0, pd.NA)
    ubs = _get_numeric(base, ['total_ubs', 'estabelecimentos_saude_calculados', 'ubs', 'qtd_ubs'], 0).replace(0, pd.NA)
    base['populacao_ref'] = pop
    base['equipes_aps_ref'] = equipes.fillna(0)
    base['ubs_ref'] = ubs.fillna(0)
    base['pop_por_equipe_ref'] = _get_numeric(base, ['pop_por_equipe'], 0)
    base.loc[base['pop_por_equipe_ref'].fillna(0).eq(0) & equipes.notna(), 'pop_por_equipe_ref'] = (pop / equipes)
    base['pop_por_ubs_ref'] = _get_numeric(base, ['pop_por_ubs'], 0)
    base.loc[base['pop_por_ubs_ref'].fillna(0).eq(0) & ubs.notna(), 'pop_por_ubs_ref'] = (pop / ubs)

    base['cadunico_pessoas'] = _get_numeric(base, ['cadunico_pessoas', 'pessoas_cadunico', 'total_pessoas_cadunico'], 0)
    base['bolsa_familia_pessoas'] = _get_numeric(base, ['bolsa_familia_pessoas', 'pessoas_bolsa_familia', 'total_pessoas_bolsa_familia'], 0)
    base['cadunico_pct'] = _get_numeric(base, ['perc_pop_cadunico', 'percentual_populacao_cadunico', 'pct_pop_cadunico', 'pop_cadunico_pct'], 0)
    base['bolsa_familia_pct'] = _get_numeric(base, ['perc_pop_bolsa_familia', 'percentual_populacao_bolsa_familia', 'pct_pop_bolsa_familia'], 0)
    base['score_mds'] = _get_numeric(base, ['score_vulnerabilidade_mds', 'score_vulnerabilidade_social_mds', 'indice_vulnerabilidade_mds'], 0)
    base['saneamento_inadequado_pct'] = _get_numeric(base, ['saneamento_inadequado_pct', 'percentual_esgotamento_inadequado', 'pct_esgotamento_inadequado', 'domicilios_esgotamento_inadequado_pct'], 0)
    base['analfabetismo_pct'] = _get_numeric(base, ['analfabetismo_pct', 'taxa_analfabetismo', 'pct_analfabetismo'], 0)
    base['escolaridade_baixa_pct'] = _get_numeric(base, ['escolaridade_baixa_pct', 'pct_baixa_escolaridade'], 0)
    base['escolas_rurais'] = _get_numeric(base, ['escolas_rurais', 'qtd_escolas_rurais', 'total_escolas_rurais'], 0)
    base['escolas_indigenas'] = _get_numeric(base, ['escolas_indigenas', 'qtd_escolas_indigenas'], 0)
    base['escolas_quilombolas'] = _get_numeric(base, ['escolas_quilombolas', 'qtd_escolas_quilombolas'], 0)
    base['pop_indigena'] = _get_numeric(base, ['populacao_indigena', 'pessoas_indigenas', 'pop_indigena'], 0)
    base['pop_quilombola'] = _get_numeric(base, ['populacao_quilombola', 'pessoas_quilombolas', 'pop_quilombola'], 0)
    base['assentamentos'] = _get_numeric(base, ['qtd_assentamentos', 'assentamentos'], 0)
    base['terras_indigenas'] = _get_numeric(base, ['qtd_terras_indigenas_intersecoes', 'terras_indigenas'], 0)

    dist = _normalizar_municipio_col(_read_first(['distancias_bairros_localidades_aps', 'distancias_bairros_localidades_ubs', 'geo_distancias_bairros_ubs']))
    if not dist.empty and 'municipio' in dist.columns:
        col_dist = next((c for c in ['distancia_ubs_mais_proxima_km', 'distancia_ubs_municipal_km', 'distancia_km'] if c in dist.columns), None)
        if col_dist:
            d = dist.copy()
            d[col_dist] = pd.to_numeric(d[col_dist], errors='coerce')
            agg = d.groupby('municipio').agg(
                territorios_analisados=(col_dist, 'count'),
                distancia_media_ubs_km=(col_dist, 'mean'),
                distancia_maxima_ubs_km=(col_dist, 'max'),
                territorios_criticos_ubs=(col_dist, lambda x: int((pd.to_numeric(x, errors='coerce') > 5).sum())),
            ).reset_index()
            base = base.merge(agg, on='municipio', how='left')

    hosp = _normalizar_municipio_col(_read_first(['geo_hospitais_retaguarda', 'hospitais_retaguarda_cadastro_editavel']))
    if not hosp.empty and 'municipio' in hosp.columns:
        h = hosp.copy()
        h['usar'] = _get_text(h, ['usar_no_mapa', 'status_validacao'], '').str.upper().str.contains('SIM|VALID', regex=True, na=False)
        agg_h = h.groupby('municipio').agg(
            hospitais_retaguarda=('municipio', 'count'),
            hospitais_validados=('usar', 'sum'),
        ).reset_index()
        base = base.merge(agg_h, on='municipio', how='left')

    # Scores de leitura integrada
    base['score_fragilidade_aps'] = (
        _norm_score(base['pop_por_equipe_ref'].fillna(0)) * 0.65 +
        _norm_score(base['pop_por_ubs_ref'].fillna(0)) * 0.35
    ).clip(0, 100)
    base['score_vulnerabilidade_social'] = (
        _norm_score(base['cadunico_pct']) * 0.25 +
        _norm_score(base['bolsa_familia_pct']) * 0.20 +
        _norm_score(base['score_mds']) * 0.25 +
        _norm_score(base['saneamento_inadequado_pct']) * 0.20 +
        _norm_score(base['analfabetismo_pct'] + base['escolaridade_baixa_pct']) * 0.10
    ).clip(0, 100)
    base['score_ruralidade'] = (
        _norm_score(base['escolas_rurais']) * 0.30 +
        _norm_score(base['assentamentos']) * 0.30 +
        _norm_score(base['terras_indigenas'] + base['pop_indigena'] + base['pop_quilombola'] + base['escolas_indigenas'] + base['escolas_quilombolas']) * 0.40
    ).clip(0, 100)
    base['score_saneamento'] = _norm_score(base['saneamento_inadequado_pct']).clip(0, 100)
    base['score_acesso_territorial'] = _norm_score(_get_numeric(base, ['distancia_media_ubs_km', 'distancia_maxima_ubs_km'], 0)).clip(0, 100)
    base.loc[base['score_acesso_territorial'].fillna(0).eq(0), 'score_acesso_territorial'] = base['score_ruralidade'] * 0.65

    base['score_prioridade'] = (
        base['score_vulnerabilidade_social'] * 0.25 +
        base['score_fragilidade_aps'] * 0.25 +
        base['score_acesso_territorial'] * 0.20 +
        base['score_ruralidade'] * 0.15 +
        base['score_saneamento'] * 0.10 +
        _norm_score(base['cadunico_pessoas'] + base['bolsa_familia_pessoas']) * 0.05
    ).clip(0, 100)
    base['classificacao_prioridade'] = base['score_prioridade'].apply(classificar_situacao)
    base['decisao_sugerida'] = base.apply(_decisao, axis=1)
    base['tem_hospital_validado'] = _get_numeric(base, ['hospitais_validados'], 0).fillna(0).gt(0)
    base['pendencia_hospitalar'] = base['tem_hospital_validado'].map(lambda x: 'Camada hospitalar validada' if x else 'Hospital/retaguarda pendente de coordenada validada')

    base['principal_motor'] = base.apply(principal_motor_prioridade, axis=1)
    base['por_que_ruim'] = base.apply(lambda r: ' | '.join(calcular_evidencias_municipio(r)[:3]), axis=1)
    base['acao_recomendada_curta'] = base.apply(acao_curta, axis=1)

    if 'regiao_saude' not in base.columns:
        base['regiao_saude'] = 'Não informada'
    return base.sort_values('score_prioridade', ascending=False).reset_index(drop=True)


# -----------------------------------------------------------------------------
# Motor explicativo: por que está ruim? o que significa? o que fazer?
# -----------------------------------------------------------------------------

def principal_motor_prioridade(row: pd.Series) -> str:
    motores = {
        'Vulnerabilidade social': float(row.get('score_vulnerabilidade_social', 0) or 0),
        'Fragilidade da capacidade APS': float(row.get('score_fragilidade_aps', 0) or 0),
        'Acesso territorial/distância': float(row.get('score_acesso_territorial', 0) or 0),
        'Ruralidade e territórios especiais': float(row.get('score_ruralidade', 0) or 0),
        'Saneamento/escolaridade': max(float(row.get('score_saneamento', 0) or 0), float(row.get('analfabetismo_pct', 0) or 0)),
    }
    return max(motores, key=motores.get)


def calcular_evidencias_municipio(row: pd.Series) -> list[str]:
    evid = []
    s_social = float(row.get('score_vulnerabilidade_social', 0) or 0)
    s_aps = float(row.get('score_fragilidade_aps', 0) or 0)
    s_acesso = float(row.get('score_acesso_territorial', 0) or 0)
    s_rural = float(row.get('score_ruralidade', 0) or 0)
    s_saneamento = float(row.get('score_saneamento', 0) or 0)
    cad = float(row.get('cadunico_pct', 0) or 0)
    bf = float(row.get('bolsa_familia_pct', 0) or 0)
    pop_eq = float(row.get('pop_por_equipe_ref', 0) or 0)
    dist = row.get('distancia_media_ubs_km', None)
    dist_max = row.get('distancia_maxima_ubs_km', None)

    if s_social >= 60 or cad >= 40 or bf >= 25:
        evid.append(f"Vulnerabilidade social {_faixa_score(s_social)}: CadÚnico {cad:.1f}% e Bolsa Família {bf:.1f}% indicam dependência social relevante e necessidade de busca ativa.")
    if s_aps >= 60 or pop_eq >= 3500:
        evid.append(f"Capacidade APS {_faixa_score(s_aps)}: população por equipe estimada em {pop_eq:,.0f}, sugerindo pressão sobre cobertura, agenda e acompanhamento longitudinal.".replace(',', '.'))
    if s_acesso >= 60 or (pd.notna(dist) and float(dist or 0) > 7):
        if pd.notna(dist):
            evid.append(f"Acesso territorial {_faixa_score(s_acesso)}: distância média até UBS/APS de {float(dist or 0):.1f} km e máxima de {float(dist_max or 0):.1f} km, exigindo validação de rota real.")
        else:
            evid.append(f"Acesso territorial {_faixa_score(s_acesso)}: proxy territorial indica barreira de deslocamento e necessidade de validação geográfica.")
    if s_rural >= 50:
        evid.append(f"Ruralidade/territórios especiais {_faixa_score(s_rural)}: presença de escolas rurais, assentamentos, indígenas, quilombolas ou áreas dispersas exige estratégia extramuros.")
    if s_saneamento >= 50 or float(row.get('saneamento_inadequado_pct', 0) or 0) >= 30:
        evid.append(f"Saneamento {_faixa_score(s_saneamento)}: esgotamento inadequado estimado em {float(row.get('saneamento_inadequado_pct', 0) or 0):.1f}%, elevando risco sanitário e demanda por vigilância/educação em saúde.")
    if not bool(row.get('tem_hospital_validado', False)):
        evid.append("Retaguarda hospitalar ainda não validada no mapa: a distância hospitalar deve permanecer como pendência técnica até confirmar coordenadas.")
    if not evid:
        evid.append("Sem alerta forte pela régua atual; manter acompanhamento preventivo e validar a qualidade das bases.")
    return evid


def consequencias_provaveis(row: pd.Series) -> list[str]:
    cons = []
    if float(row.get('score_vulnerabilidade_social', 0) or 0) >= 60:
        cons.append('maior necessidade de busca ativa, acompanhamento familiar, educação em saúde e articulação com assistência social')
    if float(row.get('score_fragilidade_aps', 0) or 0) >= 60:
        cons.append('maior risco de filas, baixa longitudinalidade, dificuldade de acompanhamento de crônicos e sobrecarga das equipes')
    if float(row.get('score_acesso_territorial', 0) or 0) >= 60:
        cons.append('risco de deslocamento tardio, abandono de cuidado e menor acesso a consultas preventivas')
    if float(row.get('score_ruralidade', 0) or 0) >= 50:
        cons.append('necessidade de agenda rural, equipe itinerante, transporte sanitário e pactuação de rotas')
    if float(row.get('score_saneamento', 0) or 0) >= 50:
        cons.append('maior demanda potencial por vigilância, prevenção de agravos e ações intersetoriais de saneamento')
    return cons or ['manter vigilância e monitorar se novos dados indicam mudança de cenário']


def acao_curta(row: pd.Series) -> str:
    motor = principal_motor_prioridade(row)
    if 'Capacidade' in motor:
        return 'Revisar equipes/UBS, CNES/INE e possibilidade de ampliação ou reorganização.'
    if 'Acesso' in motor:
        return 'Validar rotas e priorizar solução territorial: UBS satélite, unidade móvel ou transporte sanitário.'
    if 'Ruralidade' in motor:
        return 'Implantar/fortalecer estratégia rural, itinerante e extramuros.'
    if 'Saneamento' in motor:
        return 'Integrar APS, Vigilância, Educação e saneamento com ações de prevenção e educação em saúde.'
    return 'Organizar busca ativa intersetorial e acompanhamento das famílias vulneráveis.'


def gerar_insights_cruzados(base: pd.DataFrame, municipio: str | None = None, limite: int = 12) -> list[dict[str, str]]:
    if base is None or base.empty:
        return []
    df = base.copy()
    if municipio and municipio != 'Todos' and 'municipio' in df.columns:
        df = df[df['municipio'].astype(str) == str(municipio)]
    insights: list[dict[str, str]] = []
    for _, r in df.head(limite).iterrows():
        evid = calcular_evidencias_municipio(r)
        cons = consequencias_provaveis(r)
        insights.append({
            'Município': str(r.get('municipio', '-')),
            'Situação': str(r.get('classificacao_prioridade', '-')),
            'Motor principal': principal_motor_prioridade(r),
            'Por que está assim': ' '.join(evid[:2]),
            'Consequência provável': '; '.join(cons[:2]),
            'Ação sugerida': str(r.get('decisao_sugerida', acao_curta(r))),
        })
    return insights


def referencias_tecnicas() -> pd.DataFrame:
    return pd.DataFrame([
        {'Eixo': 'Prioridade integrada', 'Bom': '0–19', 'Regular': '20–39', 'Ruim': '40–59', 'Péssimo': '60–79', 'Crítico': '80–100', 'Como interpretar': 'Score composto: quanto maior, maior a combinação de vulnerabilidade, acesso difícil e fragilidade de resposta.'},
        {'Eixo': 'Distância até UBS/APS', 'Bom': 'até 1,5 km', 'Regular': '1,5–3 km', 'Ruim': '3–5 km', 'Crítico': 'acima de 5 km', 'Como interpretar': 'Distância em linha reta é alerta inicial; rota real e tempo de deslocamento precisam ser validados.'},
        {'Eixo': 'Distância hospitalar', 'Bom': 'até 20 km', 'Regular': '20–50 km', 'Ruim': '50–100 km', 'Crítico': 'acima de 100 km', 'Como interpretar': 'Só usar como evidência quando hospital/retaguarda tiver coordenada validada.'},
        {'Eixo': 'Vulnerabilidade social', 'Bom': 'baixo score relativo', 'Regular': 'atenção', 'Ruim': 'alto CadÚnico/BF/pobreza', 'Crítico': 'vulnerabilidade alta + outros alertas', 'Como interpretar': 'Quanto maior, maior necessidade de busca ativa e ação intersetorial.'},
        {'Eixo': 'Capacidade APS', 'Bom': 'oferta compatível', 'Regular': 'acompanhar', 'Ruim': 'pressão sobre equipes/UBS', 'Crítico': 'necessidade urgente de revisão', 'Como interpretar': 'Usa população por equipe/UBS como proxy; validar CNES, INE e carga horária.'},
        {'Eixo': 'Saneamento/escolaridade', 'Bom': 'baixo alerta', 'Regular': 'acompanhar', 'Ruim': 'risco sanitário/intersetorial', 'Crítico': 'alto risco territorial', 'Como interpretar': 'Apoia ações de educação em saúde, vigilância e articulação com saneamento/educação.'},
    ])


# -----------------------------------------------------------------------------
# Tendências/série histórica: usa o que existir e sinaliza pendência quando faltar
# -----------------------------------------------------------------------------

def detectar_series_historicas() -> dict[str, Any]:
    tabs = _tables()
    candidatos = [
        'serie_historica_aps_municipal',
        'historico_indicadores_municipais',
        'mds_cadunico_bolsa_familia_municipal',
        'base_publica_inep_censo_escolar_municipal',
        'sinasc_municipal',
        'sim_municipal',
        'sinan_agravos_municipal',
    ]
    encontrados = []
    for t in candidatos:
        if t in tabs:
            try:
                df = _normalizar_municipio_col(read_table(t))
                if not df.empty:
                    encontrados.append({'tabela': t, 'linhas': len(df), 'tem_ano': 'ano' in df.columns, 'colunas': len(df.columns)})
            except Exception:
                continue
    return {'tabelas_encontradas': encontrados, 'tem_series_uteis': any(x['tem_ano'] for x in encontrados)}


def montar_tendencias_municipais(base: pd.DataFrame) -> pd.DataFrame:
    """Monta tendência simplificada se existirem tabelas com ano. Não inventa série quando não há ano."""
    tabs = _tables()
    linhas = []
    candidatos = ['mds_cadunico_bolsa_familia_municipal', 'base_publica_inep_censo_escolar_municipal', 'sinasc_municipal', 'sim_municipal', 'sinan_agravos_municipal']
    for t in candidatos:
        if t not in tabs:
            continue
        try:
            df = _normalizar_municipio_col(read_table(t))
        except Exception:
            continue
        if df.empty or 'municipio' not in df.columns or 'ano' not in df.columns:
            continue
        num_cols = [c for c in df.columns if c not in ['municipio', 'codigo_ibge', 'regiao_saude', 'ano', 'mes']]
        for c in num_cols[:12]:
            serie = pd.to_numeric(df[c], errors='coerce')
            if serie.notna().sum() == 0:
                continue
            tmp = df[['municipio', 'ano']].copy()
            tmp['valor'] = serie
            agg = tmp.groupby(['municipio', 'ano'])['valor'].sum().reset_index()
            for mun, g in agg.groupby('municipio'):
                g = g.sort_values('ano')
                if len(g) < 2:
                    continue
                ini, fim = float(g.iloc[0]['valor'] or 0), float(g.iloc[-1]['valor'] or 0)
                if fim > ini * 1.05:
                    tend = 'piorando/aumentando'
                elif fim < ini * 0.95:
                    tend = 'melhorando/reduzindo'
                else:
                    tend = 'estável'
                linhas.append({'municipio': mun, 'fonte': t, 'indicador': c, 'ano_inicial': int(g.iloc[0]['ano']), 'valor_inicial': ini, 'ano_final': int(g.iloc[-1]['ano']), 'valor_final': fim, 'tendencia': tend})
    return pd.DataFrame(linhas)


# -----------------------------------------------------------------------------
# Resumos e relatórios
# -----------------------------------------------------------------------------

def resumo_executivo(base: pd.DataFrame) -> dict[str, Any]:
    if base is None or base.empty:
        return {}
    total = len(base)
    criticos = int(base['classificacao_prioridade'].isin(['Crítico', 'Péssimo']).sum())
    ruim = int(base['classificacao_prioridade'].eq('Ruim').sum())
    bom_regular = int(base['classificacao_prioridade'].isin(['Bom', 'Regular']).sum())
    top = base.head(5)['municipio'].astype(str).tolist() if 'municipio' in base.columns else []
    return {
        'municipios': total,
        'criticos_pessimos': criticos,
        'ruins': ruim,
        'bom_regular': bom_regular,
        'score_medio': float(base['score_prioridade'].mean()),
        'top_municipios': top,
        'hospitais_validados': int(_get_numeric(base, ['hospitais_validados'], 0).fillna(0).sum()) if 'hospitais_validados' in base.columns else 0,
        'municipios_hospital_validado': int(base.get('tem_hospital_validado', pd.Series(False, index=base.index)).sum()),
    }


def gerar_relatorio_municipal_texto(base: pd.DataFrame, municipio: str) -> str:
    if base is None or base.empty or not municipio:
        return 'Base insuficiente para gerar relatório.'
    filtro = base[base['municipio'].astype(str).str.upper() == str(municipio).upper()]
    if filtro.empty:
        return 'Município não encontrado na base consolidada.'
    r = filtro.iloc[0]
    evid = calcular_evidencias_municipio(r)
    cons = consequencias_provaveis(r)
    partes = []
    partes.append(f"RELATÓRIO INTELIGENTE APS — {r.get('municipio', municipio)}")
    partes.append("")
    partes.append("1. Leitura rápida")
    partes.append(f"Situação geral: {r.get('classificacao_prioridade', 'não classificada')} | Score integrado: {float(r.get('score_prioridade', 0) or 0):.1f}/100 | Motor principal: {principal_motor_prioridade(r)}.")
    partes.append("")
    partes.append("2. Por que está assim")
    for e in evid[:6]:
        partes.append(f"- {e}")
    partes.append("")
    partes.append("3. O que isso pode provocar")
    for c in cons[:5]:
        partes.append(f"- {c}.")
    partes.append("")
    partes.append("4. Encaminhamento sugerido")
    partes.append(str(r.get('decisao_sugerida', 'Validar dados com área técnica e município.')))
    partes.append("")
    partes.append("5. Validações necessárias")
    partes.append("- Confirmar CNES/INE, equipes, carga horária e unidades APS ativas.")
    partes.append("- Validar rotas reais, sazonalidade, estrada, transporte e tempo de deslocamento.")
    partes.append("- Validar coordenadas de hospitais/retaguarda antes de usar distância hospitalar como evidência oficial.")
    partes.append("- Conferir dados socioeducacionais, saneamento, indígenas, quilombolas e assentamentos antes de despacho externo.")
    partes.append("")
    partes.append("6. Uso recomendado")
    partes.append("Este relatório deve apoiar reunião técnica, pactuação regional e triagem de prioridades. Não substitui análise local, mas organiza a fila técnica de validação.")
    return "\n".join(partes)


def gerar_relatorio_regional_texto(base: pd.DataFrame, regiao: str) -> str:
    if base is None or base.empty or not regiao:
        return 'Base insuficiente para gerar relatório regional.'
    df = base[base['regiao_saude'].astype(str) == str(regiao)] if regiao != 'Todas' else base.copy()
    if df.empty:
        return 'Região não encontrada na base consolidada.'
    crit = int(df['classificacao_prioridade'].isin(['Crítico', 'Péssimo']).sum())
    top = ', '.join(df.head(5)['municipio'].astype(str).tolist())
    motores = df['principal_motor'].value_counts().head(3).to_dict() if 'principal_motor' in df.columns else {}
    motores_txt = '; '.join([f'{k}: {v} município(s)' for k, v in motores.items()]) or 'sem motor predominante identificado'
    return (
        f"RELATÓRIO REGIONAL INTELIGENTE APS — {regiao}\n\n"
        f"Foram analisados {len(df)} municípios. O score médio integrado é {float(df['score_prioridade'].mean()):.1f}/100. "
        f"A região possui {crit} município(s) em situação crítica ou péssima pela régua atual. "
        f"Municípios prioritários: {top}.\n\n"
        f"Motores predominantes na região: {motores_txt}.\n\n"
        "Leitura estratégica: priorizar municípios onde vulnerabilidade social, distância territorial, ruralidade e fragilidade APS se somam. "
        "A gestão regional deve validar rotas reais, coordenadas hospitalares, cobertura de equipes e capacidade física das UBS antes da decisão final de investimento.\n\n"
        "Encaminhamento: organizar carteira regional com três frentes: expansão/reorganização APS, transporte sanitário/logística territorial e busca ativa intersetorial."
    )
