from __future__ import annotations
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st
from components.ui_elements import render_html_table
from database.queries import read_table
from services.georreferenciamento_service import diagnosticar_camadas_geograficas, gerar_georreferencia_municipal_mt, importar_georreferencia_municipal, montar_base_mapa_municipal, obter_geojson_municipal_filtrado, montar_pontos_multicamadas, resumo_municipio_geografico, obter_pontos_camada, obter_inconsistencias_pontos_mapa, qualidade_georreferencia, identificar_vazios_assistenciais, resumo_vazios_por_regiao, calcular_distancias_assentamentos_ubs, qualificar_unidades_aps_georreferenciadas, diagnosticar_pendencias_geograficas, enriquecer_ubs_com_coordenadas_oficiais_ms, diagnosticar_json_api_ubs_ms, importar_coordenadas_ubs_sistema_antigo, classificar_estabelecimentos_elegiveis_aps, importar_coordenadas_cnes_tbestabelecimento, montar_acesso_rural_aps, diagnosticar_base_bairros_localidades, calcular_distancias_bairros_localidades_aps, diagnosticar_territorios_suspeitos_divisa, carregar_ajustes_territoriais_manuais, gerar_modelo_ajustes_territoriais, montar_painel_vazios_intramunicipais, montar_painel_determinantes_sociais_aps, montar_painel_vazios_determinantes_sociais_aps, nomear_bairros_localidades_por_osm, atualizar_nomes_bairros_ibge_2022_mt
from services.consolidacao_service import atualizar_base_municipal
from services.geointeligencia_aps_service import carregar_geointeligencia_aps, resumo_geointeligencia, componentes_geointeligencia_municipio
from services.inteligencia_avancada_aps_service import georreferenciamento_insights, glossario_decisorio_aps
from services.plano_diretor_georreferenciamento_service import montar_plano_diretor_geo, garantir_tabela_cadastro_territorial, carregar_cadastro_ubs_editavel
from services.importadores_plano_diretor_service import status_bases_plano_diretor, consolidar_inep_existente_para_socio, consolidar_ibge_e_mds_para_socio_indicadores, gerar_consolidado_socioeducacional_final, importar_hospitais_retaguarda_ms, importar_inep_microdados_oficial, calcular_distancias_territorios_hospitais_retaguarda, geocodificar_hospitais_retaguarda_por_endereco_api, preparar_cadastro_manual_hospitais_retaguarda, importar_cadastro_manual_hospitais_df, ativar_geo_hospitais_validados, resumo_cadastro_manual_hospitais, carregar_hospitais_cadastro_editavel, salvar_edicao_hospital_retaguarda, estatisticas_fluxo_validacao_hospitais

def _num(df: pd.DataFrame, coluna: str) -> pd.Series:
    if coluna not in df.columns:
        return pd.Series(dtype='float64')
    return pd.to_numeric(df[coluna], errors='coerce')

# Compatibilidade Etapa 4-F: garante disponibilidade da função da planilha de validação.
try:
    gerar_planilha_validacao_ubs
except NameError:
    try:
        from services.georreferenciamento_service import gerar_planilha_validacao_ubs
    except Exception:
        try:
            from services.georreferenciamento_service import gerar_planilha_validacao_ubs as gerar_planilha_validacao_ubs
        except Exception:
            gerar_planilha_validacao_ubs = None

# Compatibilidade Etapa 4-F.2: garante disponibilidade das funções de validação manual de UBS.
try:
    carregar_coordenadas_ubs_validadas
except NameError:
    try:
        from services.georreferenciamento_service import carregar_coordenadas_ubs_validadas
    except Exception:
        carregar_coordenadas_ubs_validadas = None

try:
    gerar_planilha_validacao_ubs
except NameError:
    try:
        from services.georreferenciamento_service import gerar_planilha_validacao_ubs
    except Exception:
        gerar_planilha_validacao_ubs = None

from services.confiabilidade_service import montar_confiabilidade_base
from services.socioeducacional_service import importar_arquivo_socioeducacional, carregar_socioeducacional_consolidado, salvar_consolidado_municipal, gerar_modelos_socioeducacionais, carregar_visao_socio_municipal, existem_bases_socioeducacionais_importadas

from services.parametros_ms_service import calcular_parametros_ms, resumo_parametros_ms, calcular_parametros_ms_gerencial, resumo_gerencial_parametros_ms

from services.ranking_expansao_service import resumo_ranking_expansao_aps

from services.catalogo_bases_publicas_service import carregar_catalogo_bases_publicas, salvar_catalogo_padrao, carregar_bases_publicas_importadas, matriz_priorizacao_importacao, gerar_modelo_registro_fonte_publica, registrar_base_publica_importada


CORES_SITUACAO_GEO = {
    'Bom': '#12B76A',
    'Regular': '#FEC84B',
    'Atenção': '#FEC84B',
    'Distante': '#F79009',
    'Ruim': '#F79009',
    'Crítico': '#F04438',
    'Muito alta': '#F04438',
    'Alta': '#F79009',
    'Média': '#FEC84B',
    'Media': '#FEC84B',
    'Baixa': '#12B76A',
    'Sem dado': '#98A2B3',
}


def _pct(valor, total, casas: int = 1) -> str:
    try:
        total = float(total)
        if total == 0:
            return '0,0%'
        return f"{(float(valor) / total * 100):.{casas}f}%".replace('.', ',')
    except Exception:
        return '-'


def _render_legenda_cores_decisoria(titulo: str = 'Legenda padrão de situação'):
    chips = []
    for nome in ['Bom', 'Regular', 'Ruim', 'Crítico', 'Sem dado']:
        cor = CORES_SITUACAO_GEO.get(nome, '#98A2B3')
        desc = {
            'Bom': 'situação relativamente favorável',
            'Regular': 'atenção preventiva',
            'Ruim': 'alerta técnico/territorial',
            'Crítico': 'prioridade máxima de validação',
            'Sem dado': 'pendente de base/validação',
        }[nome]
        chips.append(f"<div style='display:flex;align-items:center;gap:.5rem;background:#fff;border:1px solid #D9E4F2;border-radius:14px;padding:.55rem .75rem'><span style='width:13px;height:13px;border-radius:50%;background:{cor};display:inline-block'></span><b style='color:#1E276F'>{nome}</b><span style='color:#667085;font-size:.84rem'>{desc}</span></div>")
    st.markdown(f"<div style='font-weight:900;color:#1E276F;margin:.7rem 0 .4rem'>{titulo}</div><div style='display:flex;flex-wrap:wrap;gap:.55rem;margin-bottom:1rem'>{''.join(chips)}</div>", unsafe_allow_html=True)


def _classe_distancia_padrao(distancia):
    try:
        d = float(distancia)
    except Exception:
        return 'Sem dado'
    if d <= 1.5:
        return 'Bom'
    if d <= 3:
        return 'Regular'
    if d <= 5:
        return 'Ruim'
    return 'Crítico'

def _fmt_int(valor) -> str:
    try:
        return f'{int(float(valor)):,}'.replace(',', '.')
    except Exception:
        return '0'

def _fmt_float(valor, casas: int=2) -> str:
    try:
        return f'{float(valor):,.{casas}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return '-'

def _fmt_moeda(valor) -> str:
    try:
        return 'R$ ' + f'{float(valor):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return '-'

def _class_color(classe: str) -> list[int]:
    classe = str(classe or '').lower()
    if 'crít' in classe or 'crit' in classe or 'muito' in classe:
        return [240, 68, 56, 190]  # vermelho = crítico/alerta máximo
    if classe in ['alta', 'ruim', 'distante']:
        return [247, 144, 9, 180]  # laranja = ruim/distante
    if classe in ['média', 'media', 'regular', 'atenção', 'atencao']:
        return [254, 200, 75, 175]  # amarelo = regular/atenção
    if classe in ['baixa', 'bom', 'próximo', 'proximo']:
        return [18, 183, 106, 170]  # verde = bom
    return [152, 162, 179, 150]

def _ponto_color(camada: str) -> list[int]:
    camada = str(camada or '').lower()
    if 'estabelecimentos' in camada or 'saude' in camada:
        return [0, 120, 190, 180]
    if 'assent' in camada:
        return [80, 150, 80, 180]
    if 'terra' in camada or 'indigena' in camada:
        return [130, 80, 170, 180]
    if 'ambient' in camada or 'contamin' in camada:
        return [200, 85, 45, 180]
    return [90, 90, 90, 160]

def _rgba_to_css(color: list[int]) -> str:
    try:
        r, g, b, a = color
        return f'rgba({int(r)}, {int(g)}, {int(b)}, {float(a) / 255:.2f})'
    except Exception:
        return 'rgba(90, 90, 90, .70)'

def _render_legenda_pontos_estrategicos(camadas_sel: list[str], pontos: pd.DataFrame):
    if not camadas_sel:
        return
    counts = {}
    if pontos is not None and (not pontos.empty) and ('camada_nome' in pontos.columns):
        counts = pontos['camada_nome'].astype(str).value_counts().to_dict()
    st.markdown('#### Legenda dos pontos do mapa')
    st.caption('Os polígonos municipais representam a classe geográfica preliminar. Os pontinhos coloridos representam as camadas territoriais selecionadas abaixo.')
    chips = []
    for camada in camadas_sel:
        color = _rgba_to_css(_ponto_color(camada))
        total = counts.get(camada, 0)
        chips.append(f"<div style='display:flex;align-items:center;gap:.55rem;padding:.55rem .75rem;border:1px solid #E5EBF5;border-radius:14px;background:#fff;'><span style='width:12px;height:12px;border-radius:50%;display:inline-block;background:{color};border:1px solid rgba(0,0,0,.08)'></span><span style='font-weight:700;color:#1E276F'>{camada}</span><span style='color:#667085;font-size:.84rem'>({total} pontos)</span></div>")
    st.markdown("<div style='display:flex;flex-wrap:wrap;gap:.7rem;margin:0 0 1rem 0'>" + ''.join(chips) + '</div>', unsafe_allow_html=True)

def _download_csv(df: pd.DataFrame, nome: str, label: str):
    if df.empty:
        return
    st.download_button(label, data=df.to_csv(index=False).encode('utf-8-sig'), file_name=nome, mime='text/csv', use_container_width=True)




def _nota_metodologica(titulo: str, texto: str, tipo: str = "info"):
    cores = {
        "info": ("#EAF2FF", "#1E276F", "#2F80ED"),
        "alerta": ("#FFF7E6", "#7A4E00", "#F59E0B"),
        "ok": ("#ECFDF3", "#05603A", "#12B76A"),
    }
    bg, fg, border = cores.get(tipo, cores["info"])
    st.markdown(
        f"""
        <div style="background:{bg};border-left:5px solid {border};padding:0.95rem 1rem;border-radius:12px;margin:0.6rem 0 1rem 0;">
            <div style="font-weight:800;color:{fg};font-size:0.98rem;margin-bottom:.25rem;">{titulo}</div>
            <div style="color:{fg};font-size:0.92rem;line-height:1.45;">{texto}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )



def _geo_infografico_css():
    st.markdown(
        """
        <style>
        .geo-plus-box{background:linear-gradient(135deg,#F8FBFF 0%,#EEF5FF 100%);border:1px solid #D8E7FF;border-radius:22px;padding:16px;margin:12px 0 16px;box-shadow:0 8px 26px rgba(16,24,40,.06)}
        .geo-plus-title{font-size:1.1rem;font-weight:950;color:#0B2B63;margin:0 0 8px}.geo-plus-sub{color:#475467;font-size:.92rem;margin:0 0 12px;line-height:1.45}
        .geo-flow{display:flex;gap:12px;align-items:stretch;overflow-x:auto;padding:2px 0 4px}.geo-step{min-width:200px;flex:1;background:#fff;border:1px solid #D9E4F2;border-radius:18px;padding:14px;position:relative;box-shadow:0 6px 18px rgba(16,24,40,.05)}
        .geo-step:after{content:'➜';position:absolute;right:-10px;top:50%;transform:translateY(-50%);background:#fff;color:#98A2B3;font-weight:900}.geo-step:last-child:after{display:none}
        .geo-step .n{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:900;color:#fff;margin-bottom:8px}.geo-step h4{margin:0 0 5px;color:#101828;font-size:1rem}.geo-step p{margin:0;color:#475467;font-size:.88rem;line-height:1.42}
        .geo-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:12px;margin:10px 0}.geo-mini{background:#fff;border:1px solid #D9E4F2;border-radius:18px;padding:14px;box-shadow:0 6px 18px rgba(16,24,40,.05)}
        .geo-mini .k{font-size:.76rem;font-weight:850;text-transform:uppercase;letter-spacing:.04em;color:#667085}.geo-mini .v{font-size:1.35rem;font-weight:950;color:#1E276F;margin:.2rem 0}.geo-mini .s{font-size:.88rem;color:#475467;line-height:1.42}
        .geo-pill{display:inline-block;padding:4px 10px;border-radius:999px;font-size:.78rem;font-weight:850;margin:0 6px 6px 0;color:#fff}.geo-ribbon{display:flex;gap:0;overflow:hidden;border-radius:18px;border:1px solid #D9E4F2;background:#fff;margin:10px 0 14px}.geo-ribbon .seg{flex:1;padding:12px 14px;color:#fff;font-weight:850;line-height:1.32;font-size:.9rem}.geo-ribbon .seg small{display:block;font-weight:600;opacity:.94;font-size:.78rem;margin-top:4px}
        .geo-dist-hero{background:linear-gradient(135deg,#061B3A 0%,#0B3C7D 54%,#1685FF 100%);border-radius:28px;padding:24px 26px;margin:8px 0 18px;box-shadow:0 18px 44px rgba(6,27,58,.26);color:#fff;position:relative;overflow:hidden}
        .geo-dist-hero:after{content:'';position:absolute;right:-70px;top:-70px;width:260px;height:260px;border-radius:50%;background:rgba(255,255,255,.10)}
        .geo-dist-hero h2{color:#fff;margin:0;font-size:2rem;letter-spacing:-.02em}.geo-dist-hero p{margin:.45rem 0 0;max-width:980px;opacity:.95;line-height:1.48;font-size:1rem}
        .geo-dist-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin:14px 0 4px}.geo-dist-kpi{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.22);border-radius:18px;padding:13px 14px;backdrop-filter:blur(4px)}
        .geo-dist-kpi .k{font-size:.76rem;text-transform:uppercase;font-weight:850;letter-spacing:.05em;opacity:.9}.geo-dist-kpi .v{font-size:1.6rem;font-weight:950;margin:.15rem 0}.geo-dist-kpi .s{font-size:.84rem;opacity:.92;line-height:1.32}
        .geo-distance-callout{background:#F8FAFC;border:1px solid #D9E4F2;border-left:6px solid #2F80ED;border-radius:18px;padding:14px 16px;margin:12px 0;color:#1E276F;line-height:1.48;box-shadow:0 8px 22px rgba(16,24,40,.05)}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _geo_serie(df: pd.DataFrame, col: str) -> pd.Series:
    if df is None or df.empty or col not in df.columns:
        return pd.Series(dtype='float64')
    return pd.to_numeric(df[col], errors='coerce')


def _geo_val(v, sufixo: str = '', casas: int = 1) -> str:
    try:
        if pd.isna(v):
            return '-'
        f = float(v)
        if abs(f) >= 1000:
            txt = f'{f:,.0f}'.replace(',', '.')
        else:
            txt = f'{f:.{casas}f}'.replace('.', ',')
        return txt + sufixo
    except Exception:
        return '-'


def _render_geo_infografico_abertura(meta: dict, territorios: pd.DataFrame, resumo: pd.DataFrame):
    _geo_infografico_css()
    qtd_terr = int(meta.get('territorios_mapeados', 0) or (0 if territorios is None else len(territorios)))
    qtd_ubs = int(meta.get('ubs_com_coordenada', 0) or 0)
    qtd_hosp = int(meta.get('hospitais_com_coordenada', 0) or 0)
    crit = int((territorios.get('classificacao_idt_aps', pd.Series(dtype=str)).astype(str).eq('Crítico')).sum()) if territorios is not None and not territorios.empty else 0
    status_hosp = 'validada' if qtd_hosp else 'pendente'
    if crit > 0:
        leitura = f'{crit} áreas aparecem como críticas no IDT-APS e precisam de validação territorial.'
    elif qtd_terr > 0:
        leitura = 'Há territórios mapeados, mas sem classe crítica dominante pela régua atual.'
    else:
        leitura = 'Ainda faltam territórios georreferenciados suficientes para leitura intramunicipal robusta.'
    st.markdown("""
    <div class='geo-plus-box'>
      <div class='geo-plus-title'>🧭 Infográfico territorial — da visualização à decisão</div>
      <div class='geo-plus-sub'>Este bloco organiza a leitura do georreferenciamento como laboratório territorial: onde estão os vazios, quem pode estar desassistido e qual resposta pública faz sentido.</div>
    """, unsafe_allow_html=True)
    html = f"""
      <div class='geo-flow'>
        <div class='geo-step'><div class='n' style='background:#2F80ED'>1</div><h4>Camadas disponíveis</h4><p>{qtd_terr} territórios, {qtd_ubs} UBS/APS e camada hospitalar {status_hosp}.</p></div>
        <div class='geo-step'><div class='n' style='background:#F59E0B'>2</div><h4>Vazio assistencial</h4><p>{leitura}</p></div>
        <div class='geo-step'><div class='n' style='background:#7A5AF8'>3</div><h4>Quem pode estar desassistido</h4><p>Rurais, assentamentos, indígenas, quilombolas, bairros distantes e população vulnerável.</p></div>
        <div class='geo-step'><div class='n' style='background:#12B76A'>4</div><h4>Resposta possível</h4><p>Reorganizar equipes, validar rotas, implantar ação extramuros, transporte sanitário ou ponto de apoio.</p></div>
      </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def _render_geo_cards_situacao(territorios: pd.DataFrame):
    if territorios is None or territorios.empty:
        return
    dist_ubs = _geo_serie(territorios, 'distancia_ubs_km')
    score = _geo_serie(territorios, 'score_idt_aps')
    rurais = territorios.get('tipo_territorio', pd.Series(dtype=str)).astype(str).str.lower().str.contains('rural|assent|indigen|quilomb|tradicional', regex=True, na=False).sum() if 'tipo_territorio' in territorios.columns else 0
    classe_idt = territorios.get('classificacao_idt_aps', pd.Series(dtype=str)).astype(str) if 'classificacao_idt_aps' in territorios.columns else pd.Series(dtype=str)
    score_idt = pd.to_numeric(territorios.get('score_idt_aps', pd.Series(dtype=float)), errors='coerce')
    crit = int(classe_idt.str.contains('Crítico|Muito alta|Alta|Ruim', regex=True, na=False).sum())
    if crit == 0 and not score_idt.dropna().empty:
        crit = int((score_idt >= score_idt.quantile(0.80)).sum())
    st.markdown("<div class='geo-grid'>", unsafe_allow_html=True)
    cards = [
        ('Áreas em alerta', f"{_fmt_int(crit)} ({_pct(crit, len(territorios))})", 'Territórios no grupo de maior alerta relativo: distância, vulnerabilidade ou ruralidade. Se não houver Crítico formal, usa o quintil superior como triagem.', '#F04438'),
        ('Distância média até UBS', _geo_val(dist_ubs.mean(), ' km'), 'Distância geodésica média; não substitui rota real, mas revela barreira territorial.', '#F59E0B'),
        ('Maior distância encontrada', _geo_val(dist_ubs.max(), ' km'), 'Ajuda a localizar comunidades potencialmente mais penalizadas pelo deslocamento.', '#7A5AF8'),
        ('Territórios rurais/especiais', _fmt_int(rurais), 'Inclui sinais de ruralidade, assentamentos, povos tradicionais ou áreas dispersas.', '#12B76A'),
    ]
    html=''
    for k,v,s,c in cards:
        html += f"<div class='geo-mini'><span class='geo-pill' style='background:{c}'>{k}</span><div class='v'>{v}</div><div class='s'>{s}</div></div>"
    st.markdown(html + '</div>', unsafe_allow_html=True)


def _render_geo_ribbon_decisao(titulo: str, achado: str, publico: str, risco: str, acao: str):
    st.markdown(f"""
    <div class='geo-ribbon'>
      <div class='seg' style='background:#2F80ED'>{titulo}<small>{achado}</small></div>
      <div class='seg' style='background:#7A5AF8'>Quem sente mais<small>{publico}</small></div>
      <div class='seg' style='background:#F59E0B'>Risco territorial<small>{risco}</small></div>
      <div class='seg' style='background:#12B76A'>Encaminhamento<small>{acao}</small></div>
    </div>
    """, unsafe_allow_html=True)


def _render_geo_infografico_idt(territorios: pd.DataFrame, resumo: pd.DataFrame):
    if territorios is None or territorios.empty:
        return
    _render_geo_cards_situacao(territorios)
    top_mun = ''
    if resumo is not None and not resumo.empty and 'municipio' in resumo.columns:
        score_col = 'score_idt_aps' if 'score_idt_aps' in resumo.columns else None
        if score_col:
            top_mun = ', '.join(resumo.sort_values(score_col, ascending=False)['municipio'].astype(str).head(5).tolist())
    achado = top_mun or 'o ranking de áreas abaixo indica os pontos de maior alerta territorial.'
    _render_geo_ribbon_decisao(
        'Leitura consolidada IDT-APS',
        achado,
        'comunidades distantes, zona rural, assentamentos, indígenas, quilombolas e famílias vulneráveis',
        'acesso tardio, baixa prevenção, dificuldade de busca ativa e maior dependência de deslocamento',
        'validar áreas críticas, rotas reais, UBS de referência e necessidade de transporte/equipe itinerante'
    )
    _nota_metodologica('Como ler o infográfico IDT-APS', 'O IDT-APS não é apenas distância. Ele organiza distância até UBS, ruralidade, vulnerabilidade social e camadas especiais. Quando a camada hospitalar estiver validada, também poderá entrar como componente de retaguarda. Use como triagem para decidir onde aprofundar.', 'info')


def _render_geo_infografico_distancias(dist_mapa: pd.DataFrame, tipo_mapa: str):
    if dist_mapa is None or dist_mapa.empty:
        return
    if tipo_mapa.startswith('Hospitalar'):
        dist_col = 'distancia_hospital_km'
        classe_col = 'classe_distancia_hospital'
        destino = 'hospital/retaguarda'
        publico = 'territórios sem retaguarda próxima, especialmente rurais e vulneráveis'
        acao = 'validar hospitais, coordenadas e pactuação regional antes de decisão oficial'
    else:
        dist_col = 'distancia_ubs_mais_proxima_km'
        classe_col = 'classe_distancia_aps'
        destino = 'UBS/APS'
        publico = 'bairros, comunidades, assentamentos e localidades distantes da APS'
        acao = 'validar rota real, microárea, UBS de referência e necessidade de equipe/rota extramuros'
    dist = _geo_serie(dist_mapa, dist_col)
    crit = dist_mapa.get(classe_col, pd.Series(dtype=str)).astype(str).str.contains('Crítico|Distante|Ruim', regex=True, na=False).sum() if classe_col in dist_mapa.columns else 0
    municipios = ', '.join(dist_mapa.sort_values(dist_col, ascending=False).get('municipio', pd.Series(dtype=str)).astype(str).head(5).dropna().unique().tolist()) if dist_col in dist_mapa.columns else ''
    st.markdown("<div class='geo-plus-box'><div class='geo-plus-title'>🧭 Infográfico de distância e acesso</div><div class='geo-plus-sub'>A distância mostra quem pode estar mais penalizado territorialmente. A linha é geodésica; a decisão final depende de rota real, estrada, rio, período chuvoso e validação municipal/ERS.</div>", unsafe_allow_html=True)
    html = f"""
    <div class='geo-flow'>
      <div class='geo-step'><div class='n' style='background:#2F80ED'>1</div><h4>Camada analisada</h4><p>{tipo_mapa}</p></div>
      <div class='geo-step'><div class='n' style='background:#F59E0B'>2</div><h4>Distância média</h4><p>{_geo_val(dist.mean(), ' km')} até {destino}; maior distância: {_geo_val(dist.max(), ' km')}.</p></div>
      <div class='geo-step'><div class='n' style='background:#F04438'>3</div><h4>Territórios em alerta</h4><p>{_fmt_int(crit)} registros ({_pct(crit, len(dist_mapa))}) aparecem como distantes/críticos pela régua atual.</p></div>
      <div class='geo-step'><div class='n' style='background:#7A5AF8'>4</div><h4>Municípios a validar</h4><p>{municipios or 'Use o ranking abaixo para identificar municípios e territórios.'}</p></div>
      <div class='geo-step'><div class='n' style='background:#12B76A'>5</div><h4>Resposta sugerida</h4><p>{acao}</p></div>
    </div></div>
    """
    st.markdown(html, unsafe_allow_html=True)
    _render_geo_ribbon_decisao('Leitura da distância', f'{_fmt_int(len(dist_mapa))} territórios analisados', publico, 'deslocamento pode atrasar prevenção, pré-natal, crônicos, vacinação e urgência', acao)




def _render_hero_mapa_distancias(dist_mapa: pd.DataFrame, tipo_mapa: str, diag_mapa: dict):
    """Cabeçalho executivo premium do Mapa de Distâncias."""
    _geo_infografico_css()
    if dist_mapa is None or dist_mapa.empty:
        return
    is_hosp = str(tipo_mapa).startswith('Hospitalar')
    dist_col = 'distancia_hospital_km' if is_hosp else 'distancia_ubs_mais_proxima_km'
    classe_col = 'classe_distancia_hospital' if is_hosp else 'classe_distancia_aps'
    dist = _geo_serie(dist_mapa, dist_col)
    media = _geo_val(dist.mean(), ' km')
    maxima = _geo_val(dist.max(), ' km')
    criticos = int(dist_mapa.get(classe_col, pd.Series(dtype=str)).astype(str).str.contains('Crítico|Distante|Ruim', regex=True, na=False).sum()) if classe_col in dist_mapa.columns else 0
    municipios = dist_mapa.get('municipio', pd.Series(dtype=str)).dropna().astype(str).nunique()
    refs = int(diag_mapa.get('referencias_usadas', 0) or dist_mapa.get('cnes_ubs_mais_proxima', pd.Series(dtype=str)).nunique())
    destino = 'hospital/retaguarda mais próximo' if is_hosp else 'UBS/APS de referência'
    frase = 'onde o território fica distante da retaguarda regional' if is_hosp else 'onde bairros, setores, comunidades e assentamentos estão mais longe da APS'
    st.markdown(f"""
    <div class='geo-dist-hero'>
      <h2>🗺️ Mapa de Distâncias — vazios assistenciais e acesso real</h2>
      <p><b>Leitura para decisão:</b> esta é a camada mais importante para visualizar {frase}. O mapa mostra quem pode estar territorialmente penalizado e onde a gestão deve validar rota, transporte, equipe, UBS de referência ou estratégia extramuros.</p>
      <div class='geo-dist-kpis'>
        <div class='geo-dist-kpi'><div class='k'>Camada ativa</div><div class='v'>{tipo_mapa}</div><div class='s'>Cada linha liga território ao {destino}.</div></div>
        <div class='geo-dist-kpi'><div class='k'>Territórios analisados</div><div class='v'>{_fmt_int(len(dist_mapa))}</div><div class='s'>Registros com coordenadas válidas na base atual.</div></div>
        <div class='geo-dist-kpi'><div class='k'>Distância média</div><div class='v'>{media}</div><div class='s'>Distância geodésica em linha reta, não rota viária.</div></div>
        <div class='geo-dist-kpi'><div class='k'>Maior distância</div><div class='v'>{maxima}</div><div class='s'>Ponto de maior alerta territorial no recorte.</div></div>
        <div class='geo-dist-kpi'><div class='k'>Distantes/críticos</div><div class='v'>{_fmt_int(criticos)} ({_pct(criticos, len(dist_mapa))})</div><div class='s'>Registros que exigem validação operacional.</div></div>
        <div class='geo-dist-kpi'><div class='k'>Municípios envolvidos</div><div class='v'>{_fmt_int(municipios)}</div><div class='s'>Escala da análise territorial no estado.</div></div>
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("""
    <div class='geo-distance-callout'>
      <b>Como apresentar ao governador:</b> este mapa não é apenas um painel técnico. Ele mostra visualmente quem está mais distante da porta de entrada do cuidado. Linhas longas e pontos críticos indicam possíveis vazios assistenciais, necessidade de reorganização de equipes, transporte sanitário, unidade móvel, UBS satélite ou pactuação regional.
    </div>
    """, unsafe_allow_html=True)


def _garantir_colunas(df: pd.DataFrame, colunas: list[str], valor=pd.NA) -> pd.DataFrame:
    """Garante colunas opcionais para evitar KeyError em camadas incompletas."""
    if df is None:
        return pd.DataFrame()
    out = df.copy()
    for c in colunas:
        if c not in out.columns:
            out[c] = valor
    return out


def _coluna_distancia_da_camada(tipo_mapa: str, df: pd.DataFrame | None = None) -> str:
    """Retorna a coluna de distância adequada sem quebrar quando a camada ainda não existe."""
    if str(tipo_mapa).startswith('Hospitalar'):
        return 'distancia_hospital_km'
    return 'distancia_ubs_mais_proxima_km'


def _sort_seguro(df: pd.DataFrame, coluna: str, ascending: bool = False) -> pd.DataFrame:
    """Ordena somente quando a coluna existe; evita erro em camadas pendentes."""
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df
    if coluna not in df.columns:
        return df.copy()
    out = df.copy()
    out[coluna] = pd.to_numeric(out[coluna], errors='coerce')
    return out.sort_values(coluna, ascending=ascending)


def _preparar_modo_referencia_distancia(df: pd.DataFrame, modo: str) -> pd.DataFrame:
    """Seleciona a referência exibida no mapa de distâncias.

    Padrão gerencial: referência municipal, isto é, UBS/APS mais próxima dentro
    do próprio município do território. A referência física intermunicipal fica
    preservada em colunas comparativas.
    """
    if df is None or df.empty:
        return df
    out = df.copy()
    out = _garantir_colunas(out, [
        "ubs_mais_proxima", "municipio_ubs_mais_proxima", "cnes_ubs_mais_proxima",
        "distancia_ubs_mais_proxima_km", "classe_distancia_aps",
        "ubs_municipal_mais_proxima", "municipio_ubs_municipal", "cnes_ubs_municipal_mais_proxima",
        "distancia_ubs_municipal_km", "classe_distancia_aps_municipal",
        "lat_ubs", "lon_ubs", "lat_ubs_municipal", "lon_ubs_municipal"
    ])
    out["ubs_fisicamente_mais_proxima"] = out.get("ubs_mais_proxima", "")
    out["municipio_ubs_fisicamente_mais_proxima"] = out.get("municipio_ubs_mais_proxima", "")
    out["distancia_ubs_fisica_km"] = out.get("distancia_ubs_mais_proxima_km", pd.NA)
    out["classe_distancia_aps_fisica"] = out.get("classe_distancia_aps", "")

    if modo.startswith("Referência municipal"):
        tem_mun = (
            out.get("ubs_municipal_mais_proxima", pd.Series("", index=out.index)).astype(str).str.strip().ne("")
            & pd.to_numeric(out.get("distancia_ubs_municipal_km", pd.Series(pd.NA, index=out.index)), errors="coerce").notna()
        )
        out.loc[tem_mun, "ubs_mais_proxima"] = out.loc[tem_mun, "ubs_municipal_mais_proxima"]
        out.loc[tem_mun, "municipio_ubs_mais_proxima"] = out.loc[tem_mun, "municipio_ubs_municipal"]
        out.loc[tem_mun, "cnes_ubs_mais_proxima"] = out.loc[tem_mun, "cnes_ubs_municipal_mais_proxima"]
        out.loc[tem_mun, "distancia_ubs_mais_proxima_km"] = out.loc[tem_mun, "distancia_ubs_municipal_km"]
        if "classe_distancia_aps_municipal" in out.columns:
            out.loc[tem_mun, "classe_distancia_aps"] = out.loc[tem_mun, "classe_distancia_aps_municipal"]
        if "lat_ubs_municipal" in out.columns and "lon_ubs_municipal" in out.columns:
            out.loc[tem_mun, "lat_ubs"] = out.loc[tem_mun, "lat_ubs_municipal"]
            out.loc[tem_mun, "lon_ubs"] = out.loc[tem_mun, "lon_ubs_municipal"]
        out["modo_referencia_mapa"] = "Referência municipal"
        out["alerta_comparacao_fisica"] = out.apply(
            lambda r: (
                "Há UBS fisicamente mais próxima fora do município; avaliar apenas como alerta técnico/intermunicipal."
                if bool(r.get("referencia_fora_municipio")) else
                "Referência física e municipal coincidem ou não geram alerta intermunicipal."
            ),
            axis=1,
        )
    else:
        out["modo_referencia_mapa"] = "Referência física"
        out["alerta_comparacao_fisica"] = out.apply(
            lambda r: (
                "UBS fisicamente mais próxima está fora do município informado; exige validação/pactuação se houver fluxo real."
                if bool(r.get("referencia_fora_municipio")) else
                "UBS fisicamente mais próxima está no mesmo município informado."
            ),
            axis=1,
        )
    return out


def _normalizar_busca_geo(valor) -> str:
    import unicodedata
    import re
    texto = str(valor or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _aplicar_busca_distancia(df: pd.DataFrame, termo: str, campo_nome: str) -> tuple[pd.DataFrame, str]:
    """Aplica busca do mapa de distâncias sem confundir município do território com UBS de outro município.

    Se o termo digitado corresponder a um município existente no dataframe, filtra
    exclusivamente pela coluna municipio do território. Isso evita que "Cuiabá"
    traga pontos de Campo Verde, Chapada etc. apenas porque a UBS física mais
    próxima fica em Cuiabá.
    """
    if df is None or df.empty or not termo:
        return df, ""
    termo_norm = _normalizar_busca_geo(termo)
    out = df.copy()
    municipios_norm = out.get("municipio", pd.Series(dtype=str)).astype(str).map(_normalizar_busca_geo)
    municipio_match = municipios_norm.eq(termo_norm)
    if municipio_match.any():
        return out[municipio_match].copy(), f"Filtro municipal aplicado: exibindo apenas territórios cujo município informado é {termo}."

    # Se não for nome exato de município, busca apenas em campos do território e da UBS,
    # mas o resultado será sinalizado como busca textual.
    mask = pd.Series(False, index=out.index)
    campos_busca = ["municipio", campo_nome, "bairro_ou_localidade", "territorio_exibicao", "ubs_mais_proxima", "cnes_ubs_mais_proxima"]
    for c in campos_busca:
        if c in out.columns:
            mask = mask | out[c].astype(str).str.contains(termo, case=False, na=False)
    return out[mask].copy(), "Busca textual aplicada. Para análise municipal, digite o nome exato do município."


def _render_mapa_municipal(df: pd.DataFrame):
    mapa = df.copy()
    mapa['latitude'] = pd.to_numeric(mapa.get('latitude'), errors='coerce')
    mapa['longitude'] = pd.to_numeric(mapa.get('longitude'), errors='coerce')
    mapa = mapa.dropna(subset=['latitude', 'longitude'])
    mapa = mapa[mapa['latitude'].between(-25, 5) & mapa['longitude'].between(-75, -45)]
    if mapa.empty:
        st.warning('Não há coordenadas municipais válidas para desenhar o mapa.')
        return
    mapa['cor'] = mapa.get('classe_geo_preliminar', 'Bom/regular').map(_class_color)
    mapa['raio'] = (pd.to_numeric(mapa.get('indice_geo_preliminar'), errors='coerce').fillna(20) + 30) * 85
    layer = pdk.Layer('ScatterplotLayer', data=mapa, get_position='[longitude, latitude]', get_fill_color='cor', get_radius='raio', radius_min_pixels=4, radius_max_pixels=26, pickable=True, auto_highlight=True)
    view_state = pdk.ViewState(latitude=-13.5, longitude=-56.0, zoom=5.0, pitch=0)
    tooltip = {'html': '\n        <b>{municipio}</b><br/>\n        Região: {regiao_saude}<br/>\n        Índice geo preliminar: {indice_geo_preliminar}<br/>\n        Classe: {classe_geo_preliminar}<br/>\n        População: {populacao}<br/>\n        Equipes APS: {total_equipes_aps}<br/>\n        UBS: {total_ubs}<br/>\n        Assentamentos: {qtd_assentamentos}<br/>\n        Terras indígenas/interseções: {qtd_terras_indigenas_intersecoes}<br/>\n        Ocorrências ambientais: {qtd_ocorrencias_ambientais}\n        ', 'style': {'backgroundColor': '#102A43', 'color': 'white'}}
    st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state, tooltip=tooltip), use_container_width=True)

def _render_mapa_pontos(pontos: pd.DataFrame):
    if pontos.empty:
        st.warning('A camada selecionada não possui pontos com latitude/longitude válidos.')
        return
    pontos = pontos.copy()
    pontos['cor'] = pontos['camada'].map(_ponto_color)
    pontos['raio'] = 800
    layer = pdk.Layer('ScatterplotLayer', data=pontos, get_position='[lon, lat]', get_fill_color='cor', get_radius='raio', radius_min_pixels=3, radius_max_pixels=18, pickable=True, auto_highlight=True)
    view_state = pdk.ViewState(latitude=-13.5, longitude=-56.0, zoom=5.0, pitch=0)
    tooltip = {'html': '<b>{rotulo}</b><br/>Município: {municipio}<br/>Camada: {camada}', 'style': {'backgroundColor': '#102A43', 'color': 'white'}}
    st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state, tooltip=tooltip), use_container_width=True)

def _render_mapa_estrategico(base_mapa: pd.DataFrame, geojson: dict, pontos: pd.DataFrame, municipio_foco: str | None=None):
    """Mapa multicamadas: polígonos municipais + pontos selecionados."""
    camadas = []
    if geojson and geojson.get('features'):
        camadas.append(pdk.Layer('GeoJsonLayer', data=geojson, opacity=0.72, stroked=True, filled=True, extruded=False, get_fill_color='properties.fill_color', get_line_color='properties.line_color', line_width_min_pixels=1, pickable=True, auto_highlight=True))
    if pontos is not None and (not pontos.empty):
        pts = pontos.copy()
        pts['cor'] = pts.get('camada_nome', pts.get('camada', '')).map(_ponto_color)
        pts['raio'] = pts.get('camada_nome', '').astype(str).map(lambda x: 1000 if 'Estabelecimentos' in x else 1600)
        for _col, _default in {
            'regiao_saude': '',
            'indice_geo_preliminar': '',
            'classe_geo_preliminar': '',
            'total_equipes_aps': '',
            'total_ubs': '',
            'qtd_assentamentos': '',
            'qtd_terras_indigenas_intersecoes': '',
            'status_validacao_geografica': 'Validado para mapa principal',
        }.items():
            if _col not in pts.columns:
                pts[_col] = _default
        camadas.append(pdk.Layer('ScatterplotLayer', data=pts, get_position='[lon, lat]', get_fill_color='cor', get_radius='raio', radius_min_pixels=3, radius_max_pixels=14, pickable=True, auto_highlight=True))
    lat0, lon0, zoom0 = (-13.5, -56.0, 5.0)
    if municipio_foco and (not base_mapa.empty):
        foco = base_mapa[base_mapa['municipio'].astype(str) == municipio_foco]
        if not foco.empty:
            lat = pd.to_numeric(foco.iloc[0].get('latitude'), errors='coerce')
            lon = pd.to_numeric(foco.iloc[0].get('longitude'), errors='coerce')
            if pd.notna(lat) and pd.notna(lon):
                lat0, lon0, zoom0 = (float(lat), float(lon), 7.0)
    if not camadas:
        st.warning('Não há camadas geográficas válidas para renderizar o mapa estratégico.')
        return
    tooltip = {
        'html': '''
        <b>{municipio}</b><br/>
        Camada: {camada_nome}<br/>
        Registro: {rotulo}<br/>
        Município informado: {municipio}<br/>
        Status geográfico: {status_validacao_geografica}<br/>
        <hr/>
        Região: {regiao_saude}<br/>
        Índice geo preliminar: {indice_geo_preliminar}<br/>
        Classe: {classe_geo_preliminar}<br/>
        Equipes APS: {total_equipes_aps}<br/>
        UBS: {total_ubs}<br/>
        ''',
        'style': {'backgroundColor': '#102A43', 'color': 'white'}
    }
    deck = pdk.Deck(layers=camadas, initial_view_state=pdk.ViewState(latitude=lat0, longitude=lon0, zoom=zoom0, pitch=0), tooltip=tooltip, map_style='light')
    st.pydeck_chart(deck, use_container_width=True)

def _render_card_municipio_geo(info: dict):
    if not info:
        st.info('Selecione um município para ver a leitura territorial.')
        return
    st.markdown(f"#### {info.get('municipio', 'Município')}")
    st.caption(f"Região de Saúde: {info.get('regiao_saude', 'Não informada')}")
    a, b = st.columns(2)
    a.metric('Índice geo', _fmt_float(info.get('indice_geo_preliminar'), 2))
    b.metric('Classe', str(info.get('classe_geo_preliminar', '-')))
    c, d = st.columns(2)
    c.metric('Equipes APS', _fmt_int(info.get('total_equipes_aps')))
    d.metric('UBS/Estab. APS', _fmt_int(info.get('total_ubs')))
    e, f = st.columns(2)
    e.metric('Pop./equipe', _fmt_float(info.get('pop_por_equipe'), 0))
    f.metric('Pop./UBS', _fmt_float(info.get('pop_por_ubs'), 0))
    st.markdown('**Camadas territoriais:**')
    st.write(f"Assentamentos: **{_fmt_int(info.get('qtd_assentamentos'))}** | Terras indígenas/interseções: **{_fmt_int(info.get('qtd_terras_indigenas_intersecoes'))}** | Ocorrências ambientais: **{_fmt_int(info.get('qtd_ocorrencias_ambientais'))}**")
    st.markdown('**Alertas:**')
    st.info(str(info.get('alertas_geograficos', 'sem alerta territorial crítico na régua preliminar')))

def _nivel_vazio_color(nivel: str) -> list[int]:
    nivel = str(nivel or '').lower()
    if 'cr' in nivel:
        return [150, 35, 35, 210]
    if 'alto' in nivel:
        return [220, 95, 35, 195]
    if 'm' in nivel:
        return [230, 180, 45, 170]
    return [55, 130, 170, 150]

def _render_mapa_vazios(vazios: pd.DataFrame, geojson: dict):
    df = vazios.copy()
    if not df.empty:
        df['lat'] = pd.to_numeric(df.get('latitude'), errors='coerce')
        df['lon'] = pd.to_numeric(df.get('longitude'), errors='coerce')
        df['score_vazio_assistencial'] = pd.to_numeric(df.get('score_vazio_assistencial'), errors='coerce').fillna(0)
        df = df.dropna(subset=['lat', 'lon'])
    if df.empty and (not geojson.get('features')):
        st.info('Não há coordenadas ou polígonos suficientes para desenhar o mapa de vazios.')
        return
    layers = []
    if geojson.get('features'):
        layers.append(pdk.Layer('GeoJsonLayer', geojson, opacity=0.36, stroked=True, filled=True, get_fill_color='properties.fill_color', get_line_color='properties.line_color', line_width_min_pixels=1, pickable=True))
    if not df.empty:
        heat = df[df['score_vazio_assistencial'] > 0].copy()
        if not heat.empty:
            layers.append(pdk.Layer('HeatmapLayer', heat, get_position='[lon, lat]', get_weight='score_vazio_assistencial', radius_pixels=55, intensity=1.1, threshold=0.08))
        df['cor'] = df['nivel_vazio_assistencial'].map(_nivel_vazio_color)
        df['raio'] = (df['score_vazio_assistencial'].clip(10, 100) * 180).astype(float)
        layers.append(pdk.Layer('ScatterplotLayer', df, get_position='[lon, lat]', get_radius='raio', get_fill_color='cor', get_line_color=[255, 255, 255, 190], line_width_min_pixels=1, pickable=True, auto_highlight=True))
    lat = float(df['lat'].mean()) if not df.empty else -13.4
    lon = float(df['lon'].mean()) if not df.empty else -56.1
    view = pdk.ViewState(latitude=lat, longitude=lon, zoom=5.2, pitch=0)
    tooltip = {'html': '<b>{municipio}</b><br/>Nível: {nivel_vazio_assistencial}<br/>Score: {score_vazio_assistencial}<br/>Tipo: {tipo_vazio_predominante}<br/>{motivos_vazio}', 'style': {'backgroundColor': '#0f172a', 'color': 'white'}}
    st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=view, tooltip=tooltip), use_container_width=True)

def _render_legenda_vazios():
    st.markdown('\n        <div style="display:flex; gap:12px; flex-wrap:wrap; margin: 0.5rem 0 1rem 0;">\n            <div style="padding:8px 12px; border-radius:12px; background:#fee2e2; border:1px solid #fecaca;"><b>Crítico</b> — múltiplos alertas simultâneos</div>\n            <div style="padding:8px 12px; border-radius:12px; background:#ffedd5; border:1px solid #fed7aa;"><b>Alto</b> — pressão territorial relevante</div>\n            <div style="padding:8px 12px; border-radius:12px; background:#fef9c3; border:1px solid #fde68a;"><b>Médio</b> — exige ação preventiva e validação local</div>\n            <div style="padding:8px 12px; border-radius:12px; background:#e0f2fe; border:1px solid #bae6fd;"><b>Bom/regular</b> — sem alerta forte pela régua atual</div>\n        </div>\n        ', unsafe_allow_html=True)

def _classe_distancia_color(classe: str) -> list[int]:
    classe = str(classe or '').lower()
    if 'cr' in classe:
        return [150, 35, 35, 220]
    if 'dist' in classe:
        return [220, 95, 35, 200]
    if 'aten' in classe:
        return [230, 180, 45, 180]
    if 'pr' in classe:
        return [45, 145, 90, 170]
    return [110, 110, 110, 150]

def _render_mapa_distancias_assentamentos(df: pd.DataFrame):
    # Correção V37: esta função pode ser chamada em contextos antigos sem a variável externa tipo_mapa.
    tipo_mapa = 'Assentamentos → UBS/APS'
    if df.empty:
        st.info('Não há dados suficientes para desenhar o mapa de distância dos assentamentos.')
        return
    mapa = df.copy()
    for col in ['lat_assentamento', 'lon_assentamento', 'lat_ubs', 'lon_ubs', ('distancia_hospital_km' if tipo_mapa.startswith('Hospitalar') else 'distancia_ubs_mais_proxima_km')]:
        mapa[col] = pd.to_numeric(mapa.get(col), errors='coerce')
    mapa = mapa.dropna(subset=['lat_assentamento', 'lon_assentamento', 'lat_ubs', 'lon_ubs'])
    mapa = mapa[mapa['lat_assentamento'].between(-25, 5) & mapa['lon_assentamento'].between(-75, -45) & mapa['lat_ubs'].between(-25, 5) & mapa['lon_ubs'].between(-75, -45)].copy()
    if mapa.empty:
        st.info('Os registros filtrados não possuem coordenadas válidas para mapa.')
        return
    mapa['cor'] = mapa['classe_distancia_aps'].map(_classe_distancia_color)
    mapa['distancia_label'] = mapa[('distancia_hospital_km' if tipo_mapa.startswith('Hospitalar') else 'distancia_ubs_mais_proxima_km')].round(2).astype(str) + ' km'
    mapa['source'] = mapa.apply(lambda r: [float(r['lon_assentamento']), float(r['lat_assentamento'])], axis=1)
    mapa['target'] = mapa.apply(lambda r: [float(r['lon_ubs']), float(r['lat_ubs'])], axis=1)
    assent_layer = pdk.Layer('ScatterplotLayer', mapa, get_position='[lon_assentamento, lat_assentamento]', get_fill_color='cor', get_radius=1400, radius_min_pixels=5, radius_max_pixels=18, pickable=True, auto_highlight=True)
    ubs_layer = pdk.Layer('ScatterplotLayer', mapa.drop_duplicates(['lat_ubs', 'lon_ubs']), get_position='[lon_ubs, lat_ubs]', get_fill_color=[0, 105, 180, 210], get_radius=900, radius_min_pixels=4, radius_max_pixels=12, pickable=True, auto_highlight=True)
    line_layer = pdk.Layer('LineLayer', mapa, get_source_position='source', get_target_position='target', get_color='cor', get_width=2, pickable=True)
    view = pdk.ViewState(latitude=float(mapa['lat_assentamento'].mean()), longitude=float(mapa['lon_assentamento'].mean()), zoom=5.2, pitch=0)
    tooltip = {'html': '\n        <b>{assentamento}</b><br/>\n        Município: {municipio}<br/>\n        UBS/ref. mais próxima: {ubs_mais_proxima}<br/>\n        Município da UBS/ref.: {municipio_ubs_mais_proxima}<br/>\n        Distância: {distancia_label}<br/>\n        Classe: {classe_distancia_aps}<br/>\n        Modo: {modo_calculo}\n        ', 'style': {'backgroundColor': '#102A43', 'color': 'white'}}
    st.pydeck_chart(pdk.Deck(layers=[line_layer, ubs_layer, assent_layer], initial_view_state=view, tooltip=tooltip, map_style='light'), use_container_width=True)

def _render_mapa_distancias_bairros_localidades(df: pd.DataFrame):
    # Correção V37: esta função pode ser chamada em contextos antigos sem a variável externa tipo_mapa.
    tipo_mapa = 'Bairros/localidades/setores → UBS/APS'
    if df.empty:
        st.info('Não há dados suficientes para desenhar o mapa de bairros/localidades/setores.')
        return
    mapa = df.copy()
    required = ['latitude', 'longitude', 'lat_ubs', 'lon_ubs']
    faltantes = [c for c in required if c not in mapa.columns]
    if faltantes:
        st.info('A tabela de distâncias foi calculada, mas ainda não contém as coordenadas necessárias para desenhar as linhas no mapa: ' + ', '.join(faltantes) + '. Recalcule a aba após aplicar o patch atual.')
        return
    for col in ['latitude', 'longitude', 'lat_ubs', 'lon_ubs', ('distancia_hospital_km' if tipo_mapa.startswith('Hospitalar') else 'distancia_ubs_mais_proxima_km')]:
        mapa[col] = pd.to_numeric(mapa.get(col), errors='coerce')
    mapa = mapa.dropna(subset=['latitude', 'longitude', 'lat_ubs', 'lon_ubs'])
    mapa = mapa[mapa['latitude'].between(-25, 5) & mapa['longitude'].between(-75, -45) & mapa['lat_ubs'].between(-25, 5) & mapa['lon_ubs'].between(-75, -45)].copy()
    if mapa.empty:
        st.info('Os registros filtrados não possuem coordenadas válidas para mapa.')
        return
    mapa = _sort_seguro(mapa, _coluna_distancia_da_camada(tipo_mapa), ascending=False).head(800).copy()
    mapa['cor'] = mapa['classe_distancia_aps'].map(_classe_distancia_color)
    mapa['distancia_label'] = mapa[('distancia_hospital_km' if tipo_mapa.startswith('Hospitalar') else 'distancia_ubs_mais_proxima_km')].round(2).astype(str) + ' km'
    for col in ['indice_determinantes_sociais_aps', 'classe_determinantes_sociais_aps', 'taxa_analfabetismo_estimado_pct', 'renda_indicador', 'prioridade_integrada_territorio', 'classe_prioridade_integrada']:
        if col not in mapa.columns:
            mapa[col] = '-'
    mapa['determinantes_label'] = mapa['indice_determinantes_sociais_aps'].apply(lambda x: _fmt_float(x, 1) if str(x) != '-' else '-')
    mapa['analfabetismo_label'] = mapa['taxa_analfabetismo_estimado_pct'].apply(lambda x: _fmt_float(x, 1) + '%' if str(x) != '-' else '-')
    mapa['renda_label'] = mapa['renda_indicador'].apply(lambda x: _fmt_moeda(x) if str(x) != '-' else '-')
    mapa['prioridade_integrada_label'] = mapa['prioridade_integrada_territorio'].apply(lambda x: _fmt_float(x, 1) if str(x) != '-' else '-')
    mapa['source'] = mapa.apply(lambda r: [float(r['longitude']), float(r['latitude'])], axis=1)
    mapa['target'] = mapa.apply(lambda r: [float(r['lon_ubs']), float(r['lat_ubs'])], axis=1)
    territorios_layer = pdk.Layer('ScatterplotLayer', mapa, get_position='[longitude, latitude]', get_fill_color='cor', get_radius=550, radius_min_pixels=3, radius_max_pixels=11, pickable=True, auto_highlight=True)
    ubs_layer = pdk.Layer('ScatterplotLayer', mapa.drop_duplicates(['lat_ubs', 'lon_ubs']), get_position='[lon_ubs, lat_ubs]', get_fill_color=[0, 105, 180, 220], get_radius=850, radius_min_pixels=4, radius_max_pixels=13, pickable=True, auto_highlight=True)
    line_layer = pdk.Layer('LineLayer', mapa, get_source_position='source', get_target_position='target', get_color='cor', get_width=1.5, pickable=True)
    view = pdk.ViewState(latitude=float(mapa['latitude'].mean()), longitude=float(mapa['longitude'].mean()), zoom=5.2, pitch=0)
    tooltip = {'html': '\n        <b>{territorio_exibicao}</b><br/>\n        Município: {municipio}<br/>\n        População ref.: {populacao}<br/>\n        UBS/APS mais próxima: {ubs_mais_proxima}<br/>\n        Município da UBS: {municipio_ubs_mais_proxima}<br/>\n        Distância: {distancia_label}<br/>\n        Classe: {classe_distancia_aps}<br/>\n        <hr/>\n        Índice social APS: {determinantes_label}<br/>\n        Classe social: {classe_determinantes_sociais_aps}<br/>\n        Analfabetismo estimado: {analfabetismo_label}<br/>\n        Renda indicadora: {renda_label}<br/>\n        Prioridade integrada: {prioridade_integrada_label} — {classe_prioridade_integrada}\n        ', 'style': {'backgroundColor': '#102A43', 'color': 'white'}}
    st.pydeck_chart(pdk.Deck(layers=[line_layer, ubs_layer, territorios_layer], initial_view_state=view, tooltip=tooltip, map_style='light'), use_container_width=True)


def _render_mapa_distancias_hospitalar_retaguarda(df: pd.DataFrame):
    """Desenha linhas entre bairros/localidades/setores e hospital/retaguarda mais próxima.

    Esta camada só deve ser considerada oficial quando a tabela geo_hospitais_retaguarda
    tiver sido criada por API/base oficial e possuir coordenadas plausíveis de MT.
    """
    if df is None or df.empty:
        st.info('Não há dados suficientes para desenhar a camada hospitalar. Importe/valide a base de Hospitais e Leitos/MS ou CNES com coordenadas.')
        return
    mapa = df.copy()
    required = ['latitude', 'longitude', 'lat_hospital', 'lon_hospital']
    faltantes = [c for c in required if c not in mapa.columns]
    if faltantes:
        st.info('A camada hospitalar foi calculada, mas ainda não contém coordenadas suficientes para desenhar as linhas: ' + ', '.join(faltantes))
        return
    for col in required + ['distancia_hospital_km']:
        mapa[col] = pd.to_numeric(mapa.get(col), errors='coerce')
    mapa = mapa.dropna(subset=['latitude', 'longitude', 'lat_hospital', 'lon_hospital']).copy()
    mapa = mapa[
        mapa['latitude'].between(-25, 5) & mapa['longitude'].between(-75, -45) &
        mapa['lat_hospital'].between(-25, 5) & mapa['lon_hospital'].between(-75, -45)
    ].copy()
    if mapa.empty:
        st.info('Os registros hospitalares/territoriais não possuem coordenadas válidas para mapa.')
        return
    def _cor_hosp(classe):
        c = str(classe or '').lower()
        if 'crítico' in c or 'critico' in c or 'péssimo' in c or 'pessimo' in c:
            return [180, 35, 35, 210]
        if 'ruim' in c:
            return [230, 120, 35, 200]
        if 'regular' in c:
            return [235, 190, 55, 190]
        return [45, 170, 110, 190]
    mapa['cor'] = mapa.get('classe_distancia_hospital', pd.Series([''] * len(mapa))).map(_cor_hosp)
    mapa['distancia_label'] = mapa['distancia_hospital_km'].round(2).astype(str) + ' km'
    mapa['source'] = mapa.apply(lambda r: [float(r['longitude']), float(r['latitude'])], axis=1)
    mapa['target'] = mapa.apply(lambda r: [float(r['lon_hospital']), float(r['lat_hospital'])], axis=1)
    line_layer = pdk.Layer('LineLayer', mapa, get_source_position='source', get_target_position='target', get_color='cor', get_width=2, pickable=True)
    territ_layer = pdk.Layer('ScatterplotLayer', mapa, get_position='[longitude, latitude]', get_fill_color='cor', get_radius=650, radius_min_pixels=3, radius_max_pixels=12, pickable=True, auto_highlight=True)
    hospitais = mapa.drop_duplicates(['lat_hospital','lon_hospital']).copy()
    hosp_layer = pdk.Layer('ScatterplotLayer', hospitais, get_position='[lon_hospital, lat_hospital]', get_fill_color=[30, 80, 190, 230], get_radius=1400, radius_min_pixels=5, radius_max_pixels=18, pickable=True, auto_highlight=True)
    view = pdk.ViewState(latitude=float(mapa['latitude'].mean()), longitude=float(mapa['longitude'].mean()), zoom=5.2, pitch=0)
    tooltip = {'html': '<b>{territorio_exibicao}</b><br/>Município: {municipio}<br/>Hospital/retaguarda: {hospital_mais_proximo}<br/>Município do hospital: {municipio_hospital}<br/>Distância: {distancia_label}<br/>Classe: {classe_distancia_hospital}', 'style': {'backgroundColor': '#102A43', 'color': 'white'}}
    st.pydeck_chart(pdk.Deck(layers=[line_layer, hosp_layer, territ_layer], initial_view_state=view, tooltip=tooltip, map_style='light'), use_container_width=True)


def _render_legenda_distancias_hospitalar():
    st.markdown('#### Legenda hospitalar/retaguarda')
    st.markdown('<div style="display:flex;gap:10px;flex-wrap:wrap;">'
                '<span style="background:#d9fbe8;border:1px solid #9de7bd;padding:10px;border-radius:10px;"><b>Bom</b> — até 20 km</span>'
                '<span style="background:#fff3bf;border:1px solid #ffd43b;padding:10px;border-radius:10px;"><b>Regular</b> — 20 a 50 km</span>'
                '<span style="background:#ffe8cc;border:1px solid #ffa94d;padding:10px;border-radius:10px;"><b>Ruim</b> — 50 a 100 km</span>'
                '<span style="background:#ffe3e3;border:1px solid #ff8787;padding:10px;border-radius:10px;"><b>Crítico</b> — acima de 100 km</span>'
                '</div>', unsafe_allow_html=True)

def _render_legenda_distancias_bairros():
    st.markdown('\n        <div style="display:flex; gap:12px; flex-wrap:wrap; margin: 0.5rem 0 1rem 0;">\n            <div style="padding:8px 12px; border-radius:12px; background:#dcfce7; border:1px solid #bbf7d0;"><b>Próximo</b> — até 1,5 km</div>\n            <div style="padding:8px 12px; border-radius:12px; background:#fef9c3; border:1px solid #fde68a;"><b>Atenção</b> — 1,5 a 3 km</div>\n            <div style="padding:8px 12px; border-radius:12px; background:#ffedd5; border:1px solid #fed7aa;"><b>Distante</b> — 3 a 5 km</div>\n            <div style="padding:8px 12px; border-radius:12px; background:#fee2e2; border:1px solid #fecaca;"><b>Crítico</b> — acima de 5 km</div>\n        </div>\n        ', unsafe_allow_html=True)

def _render_legenda_distancias():
    st.markdown('\n        <div style="display:flex; gap:12px; flex-wrap:wrap; margin: 0.5rem 0 1rem 0;">\n            <div style="padding:8px 12px; border-radius:12px; background:#dcfce7; border:1px solid #bbf7d0;"><b>Próximo</b> — até 5 km</div>\n            <div style="padding:8px 12px; border-radius:12px; background:#fef9c3; border:1px solid #fde68a;"><b>Atenção</b> — 5 a 15 km</div>\n            <div style="padding:8px 12px; border-radius:12px; background:#ffedd5; border:1px solid #fed7aa;"><b>Distante</b> — 15 a 30 km</div>\n            <div style="padding:8px 12px; border-radius:12px; background:#fee2e2; border:1px solid #fecaca;"><b>Crítico</b> — acima de 30 km</div>\n        </div>\n        ', unsafe_allow_html=True)


def _class_color_geo_inteligente(classe: str) -> list[int]:
    classe = str(classe or '').lower()
    if 'crítica' in classe or 'critica' in classe:
        return [205, 45, 45, 185]
    if 'alta' in classe:
        return [235, 135, 30, 175]
    if 'média' in classe or 'media' in classe:
        return [245, 205, 60, 165]
    return [35, 150, 180, 155]


def _render_mapa_geointeligencia(df: pd.DataFrame):
    mapa = df.copy()
    if mapa.empty:
        st.info('Sem dados para o mapa de geointeligência.')
        return
    mapa['latitude'] = pd.to_numeric(mapa.get('latitude'), errors='coerce')
    mapa['longitude'] = pd.to_numeric(mapa.get('longitude'), errors='coerce')
    mapa = mapa.dropna(subset=['latitude', 'longitude'])
    mapa = mapa[mapa['latitude'].between(-25, 5) & mapa['longitude'].between(-75, -45)]
    if mapa.empty:
        st.warning('Não há coordenadas válidas para desenhar o mapa de geointeligência.')
        return
    mapa['cor_geo_int'] = mapa.get('classe_geointeligencia', '').map(_class_color_geo_inteligente)
    mapa['raio_geo_int'] = (pd.to_numeric(mapa.get('score_geointeligencia_aps'), errors='coerce').fillna(20) + 20) * 95
    layer = pdk.Layer(
        'ScatterplotLayer',
        data=mapa,
        get_position='[longitude, latitude]',
        get_fill_color='cor_geo_int',
        get_radius='raio_geo_int',
        radius_min_pixels=5,
        radius_max_pixels=32,
        pickable=True,
        auto_highlight=True,
    )
    view_state = pdk.ViewState(latitude=-13.5, longitude=-56.0, zoom=5.0, pitch=0)
    tooltip = {
        'html': '<b>{municipio}</b><br/>Região: {regiao_saude}<br/>Score geo: {score_geointeligencia_aps}<br/>Classe: {classe_geointeligencia}<br/>Motivo: {motivo_geointeligencia}',
        'style': {'backgroundColor': '#0B3C7D', 'color': 'white'}
    }
    st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state, tooltip=tooltip), use_container_width=True)



def _render_cruzamentos_geo_decisorios():
    st.markdown("### Cruzamentos decisórios de acesso, distância e vazios")
    st.caption("Esta leitura cruza vulnerabilidade social, capacidade APS, capacidade/necessidade de equipe, barreiras territoriais e risco sanitário para separar vazios que exigem visita técnica, reorganização de equipes, estratégia extramuros ou estudo de expansão.")
    res = georreferenciamento_insights()
    if not res.get("ok"):
        st.info(res.get("mensagem", "Cruzamentos geográficos indisponíveis."))
        return
    base = res.get("base", pd.DataFrame())
    resumo = res.get("resumo_quadrantes", pd.DataFrame())
    if base.empty:
        st.info("Sem base para cruzamento geográfico avançado.")
        return

    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Municípios avaliados", _fmt_int(len(base)), help="Total de municípios com informações suficientes para a leitura geoterritorial.")
    q2.metric("Barreira territorial alta", _fmt_int((pd.to_numeric(base.get("score_acesso_territorial"), errors="coerce").fillna(0) >= 65).sum()), help="Municípios em que acesso/distância/dispersão territorial exigem validação mais cuidadosa.")
    q3.metric("Casos com ação forte", _fmt_int(base.get("quadrante_decisao", pd.Series(dtype=str)).astype(str).str.contains("Péssimo|Ruim|urgente", case=False, na=False).sum()), help="Municípios classificados em quadrantes que sugerem intervenção, reorganização ou estudo de expansão.")
    q4.metric("Score geo médio", _fmt_float(pd.to_numeric(base.get("score_geointeligencia_aps"), errors="coerce").fillna(0).mean(), 1), help="Média do score geoterritorial no filtro atual.")

    _nota_metodologica(
        "ℹ️ Como ler os cruzamentos decisórios",
        "Esta aba não mostra apenas distância. Ela cruza distância/acesso, vulnerabilidade social, capacidade APS, pressão assistencial e risco sanitário. A saída deve responder: o problema parece ser falta de unidade, falta/reorganização de equipe, barreira rural/territorial, vulnerabilidade social ou combinação desses fatores?",
        "info",
    )
    _nota_metodologica(
        "Quando o sistema fala em nova UBS ou UBS satélite",
        "Leia como hipótese técnica de expansão. Antes de propor obra, valide distância real, bairros/localidades descobertos, população afetada, capacidade das UBS atuais, produção assistencial, terreno, custeio, CNES e pactuação regional.",
        "alerta",
    )

    if not resumo.empty:
        fig_q = px.bar(resumo.sort_values("score_medio"), x="score_medio", y="quadrante_decisao", orientation="h", text="municipios", title="Quadrantes territoriais mais relevantes")
        fig_q.update_layout(height=390, xaxis_title="Score médio de geointeligência", yaxis_title="")
        st.plotly_chart(fig_q, use_container_width=True, key="geo_cruz_quad")
        render_html_table(resumo, titulo="Resumo por quadrante territorial", max_rows=20, max_text=180)

    st.markdown("#### Municípios com maior necessidade de aprofundamento territorial")
    cols = [c for c in [
        "ranking_geointeligencia", "municipio", "regiao_saude", "quadrante_decisao", "score_geointeligencia_aps", "score_acesso_territorial", "score_social_geo", "score_fragilidade_capacidade", "score_pressao_assistencial", "score_risco_sanitario", "total_ubs", "total_equipes_aps", "geo_pop_por_equipe", "geo_pop_por_ubs", "insight_territorial", "acao_geo_sugerida"
    ] if c in base.columns]
    tabela = base[cols].sort_values(["score_geointeligencia_aps", "score_acesso_territorial"], ascending=False).head(80)
    render_html_table(tabela, titulo="Ranking de cruzamentos geoterritoriais", subtitulo="Foco em vazios, acesso, concentração, vulnerabilidade e capacidade de resposta.", max_rows=80, max_text=240)
    _download_csv(base[cols], "cruzamentos_geoterritoriais_aps.csv", "Baixar cruzamentos geoterritoriais")

    st.markdown("#### Como transformar em decisão")
    _nota_metodologica("Pergunta prática para o técnico", "Para cada município do ranking, a pergunta central deve ser: a população está distante da UBS, a UBS está sobrecarregada, as equipes são insuficientes, ou existe vulnerabilidade social/ruralidade que exige estratégia específica?", "info")
    st.markdown("""
    | Situação encontrada | Interpretação | Decisão técnica provável |
    |---|---|---|
    | Vulnerabilidade alta + capacidade frágil | Demanda social elevada coincide com menor capacidade relativa de resposta | Revisar equipes, UBS, adscrição, carga horária e pactuar apoio prioritário |
    | Barreira territorial + vulnerabilidade | Distância/dispersão aumenta o risco de população vulnerável ficar sem acompanhamento | Validar rotas, organizar busca ativa, unidade móvel, ações extramuros ou nova referência territorial |
    | Barreira territorial predominante | O problema principal parece ser acesso físico/territorial | Estudar vazios, transporte, rotas, ruralidade, assentamentos e localização das UBS |
    | Fragilidade estrutural predominante | A geografia pode não ser o principal problema; a capacidade instalada pesa mais | Auditar CNES/INE, equipes, profissionais e estrutura física |
    """)
    with st.expander("ℹ️ Glossário de decisão territorial", expanded=False):
        render_html_table(glossario_decisorio_aps(), titulo="Régua de leitura para georreferenciamento", max_rows=20, max_text=260)

def _render_geointeligencia_integrada():
    st.markdown('### Geointeligência integrada APS')
    st.caption('Camada territorial que cruza georreferenciamento com MDS, CNES, SINASC, SIM, SINAN, INEP e camadas territoriais. A pontuação é orientativa e deve ser validada com rotas reais, ERS e município.')
    df_geoi = carregar_geointeligencia_aps()
    if df_geoi.empty:
        st.warning('Ainda não foi possível montar a geointeligência integrada. Atualize a base municipal consolidada e confira as cargas MDS/CNES/DATASUS/INEP.')
        return
    resumo_geoi = resumo_geointeligencia(df_geoi)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Municípios avaliados', _fmt_int(resumo_geoi.get('municipios')))
    c2.metric('Prioridade crítica', _fmt_int(resumo_geoi.get('criticos')))
    c3.metric('Alta prioridade', _fmt_int(resumo_geoi.get('alta')))
    c4.metric('Score médio geo', _fmt_float(resumo_geoi.get('score_medio'), 1))

    _nota_metodologica(
        'Como ler esta aba',
        'O score geoterritorial combina vulnerabilidade social, acesso territorial, fragilidade da capacidade APS, capacidade/necessidade de equipe e risco sanitário/intersetorial. Ele ajuda a priorizar estudos territoriais, não substitui rota real, adscrição, capacidade física ou decisão de obra.',
        'info'
    )

    regioes = sorted([r for r in df_geoi.get('regiao_saude', pd.Series()).dropna().astype(str).unique()])
    classes = ['Todas'] + sorted([c for c in df_geoi.get('classe_geointeligencia', pd.Series()).dropna().astype(str).unique()])
    f1, f2, f3 = st.columns([1.2, 1.2, 2])
    reg = f1.selectbox('Região de Saúde', ['Todas'] + regioes, key='geo_int_regiao')
    cla = f2.selectbox('Classe geoterritorial', classes, key='geo_int_classe')
    busca = f3.text_input('Buscar município', placeholder='Ex.: Cuiabá, Acorizal, Rondonópolis', key='geo_int_busca')
    filtrado = df_geoi.copy()
    if reg != 'Todas' and 'regiao_saude' in filtrado.columns:
        filtrado = filtrado[filtrado['regiao_saude'].astype(str).str.strip() == str(reg).strip()]
    if cla != 'Todas' and 'classe_geointeligencia' in filtrado.columns:
        filtrado = filtrado[filtrado['classe_geointeligencia'].astype(str).str.strip() == str(cla).strip()]
    if busca and 'municipio' in filtrado.columns:
        filtrado = filtrado[filtrado['municipio'].astype(str).str.contains(busca, case=False, na=False)]

    mapa_col, rank_col = st.columns([1.55, 1])
    with mapa_col:
        st.markdown('#### Mapa de prioridade geoterritorial')
        _render_mapa_geointeligencia(filtrado)
    with rank_col:
        st.markdown('#### Top prioridades no filtro')
        top = filtrado.sort_values('score_geointeligencia_aps', ascending=False).head(10)
        for _, r in top.iterrows():
            cor = _rgba_to_css(_class_color_geo_inteligente(r.get('classe_geointeligencia')))
            st.markdown(
                f"""
                <div style='background:#fff;border-left:6px solid {cor};border-radius:14px;padding:.7rem .8rem;margin-bottom:.55rem;box-shadow:0 4px 14px rgba(16,24,40,.06);'>
                    <div style='font-weight:900;color:#101828;'>{int(r.get('ranking_geointeligencia',0))}º — {r.get('municipio','-')}</div>
                    <div style='font-size:.85rem;color:#475467;'>{r.get('regiao_saude','-')} • Score {r.get('score_geointeligencia_aps','-')} • {r.get('classe_geointeligencia','-')}</div>
                    <div style='font-size:.78rem;color:#667085;margin-top:.25rem;'>{r.get('motivo_geointeligencia','')}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    st.markdown('#### Matriz territorial: vulnerabilidade social x fragilidade APS')
    matriz = filtrado.copy()
    if not matriz.empty:
        fig = px.scatter(
            matriz,
            x='score_social_geo',
            y='score_fragilidade_capacidade',
            size='populacao' if 'populacao' in matriz.columns else None,
            color='classe_geointeligencia',
            hover_name='municipio',
            hover_data=[c for c in ['regiao_saude','score_acesso_territorial','score_pressao_assistencial','score_risco_sanitario','score_geointeligencia_aps'] if c in matriz.columns],
            title='Quanto mais alto e à direita, maior a coincidência entre vulnerabilidade e fragilidade estrutural.',
        )
        fig.update_layout(height=460, xaxis_title='Vulnerabilidade social', yaxis_title='Fragilidade da capacidade APS')
        st.plotly_chart(fig, use_container_width=True, key='geo_int_matriz')

    st.markdown('#### Eixos geoterritoriais do município')
    municipios = sorted(df_geoi.get('municipio', pd.Series()).dropna().astype(str).unique())
    mun_sel = st.selectbox('Selecionar município para leitura geoterritorial', municipios, key='geo_int_mun')
    comp = componentes_geointeligencia_municipio(mun_sel, df_geoi)
    linha = df_geoi[df_geoi['municipio'].astype(str).str.strip() == str(mun_sel).strip()].head(1)
    if not linha.empty:
        r = linha.iloc[0]
        q1, q2, q3, q4 = st.columns(4)
        q1.metric('Ranking geo', _fmt_int(r.get('ranking_geointeligencia')))
        q2.metric('Score geo', _fmt_float(r.get('score_geointeligencia_aps'), 1))
        q3.metric('Classe', str(r.get('classe_geointeligencia', '-')))
        q4.metric('População', _fmt_int(r.get('populacao')))
        st.info(f"Motivo principal: {r.get('motivo_geointeligencia','-')}")
        st.success(f"Recomendação: {r.get('recomendacao_geointeligencia','-')}")
    if not comp.empty:
        comp['Score'] = pd.to_numeric(comp['Score'], errors='coerce').fillna(0)
        fig_comp = px.bar(comp.sort_values('Score'), x='Score', y='Eixo', orientation='h', text='Score', hover_data=['Peso'], title='Componentes do score geoterritorial')
        fig_comp.update_layout(height=360, xaxis_title='Score 0-100', yaxis_title='')
        st.plotly_chart(fig_comp, use_container_width=True, key='geo_int_comp')

    # A aba '⭐ Cruzamentos decisórios' já renderiza esta seção separadamente.
    # Evita duplicidade de elementos Streamlit/Plotly com a mesma key.

    st.markdown('#### Ranking geoterritorial completo')
    cols = [c for c in [
        'ranking_geointeligencia','municipio','regiao_saude','score_geointeligencia_aps','classe_geointeligencia',
        'score_social_geo','score_acesso_territorial','score_fragilidade_capacidade','score_pressao_assistencial','score_risco_sanitario',
        'populacao','total_ubs','total_equipes_aps','geo_pop_por_equipe','geo_pop_por_ubs','pct_cadunico_geo','pct_pbf_geo',
        'qtd_assentamentos','qtd_terras_indigenas_intersecoes','populacao_indigena','populacao_quilombola','motivo_geointeligencia','recomendacao_geointeligencia'
    ] if c in filtrado.columns]
    tabela = filtrado[cols].sort_values('ranking_geointeligencia') if cols else filtrado
    render_html_table(tabela, titulo='Ranking geoterritorial integrado', subtitulo='Cruza a base completa já importada com leitura territorial orientativa.', max_rows=80, max_text=180)
    _download_csv(tabela, 'ranking_geointeligencia_integrada_aps.csv', 'Baixar ranking geoterritorial integrado')



def _mapa_pontos_plano_diretor(df: pd.DataFrame, titulo: str, color_col: str = 'classificacao_idt_aps', size_col: str | None = 'score_idt_aps', hover_cols: list[str] | None = None, key: str = 'plano_diretor_mapa'):
    if df is None or df.empty or not {'lat','lon'}.issubset(df.columns):
        st.info('Não há pontos georreferenciados suficientes para este mapa.')
        return
    hover_cols = hover_cols or [c for c in ['municipio','nome_area','tipo_territorio','distancia_ubs_km','distancia_hospital_km','decisao_sugerida','tipo_transporte_sugerido'] if c in df.columns]
    plot_df = df.copy()
    plot_df['lat'] = pd.to_numeric(plot_df['lat'], errors='coerce')
    plot_df['lon'] = pd.to_numeric(plot_df['lon'], errors='coerce')
    plot_df = plot_df.dropna(subset=['lat','lon']).head(2500)
    if plot_df.empty:
        st.info('Os registros existem, mas sem coordenadas válidas para plotagem.')
        return
    if size_col and size_col in plot_df.columns:
        plot_df['_size_plot'] = pd.to_numeric(plot_df[size_col], errors='coerce').fillna(8).clip(8, 35)
        size_arg = '_size_plot'
    else:
        plot_df['_size_plot'] = 10
        size_arg = '_size_plot'
    fig = px.scatter_mapbox(
        plot_df,
        lat='lat', lon='lon', color=color_col if color_col in plot_df.columns else None,
        size=size_arg,
        color_discrete_map=CORES_SITUACAO_GEO if color_col in plot_df.columns else None,
        hover_name='nome_area' if 'nome_area' in plot_df.columns else None,
        hover_data=hover_cols,
        zoom=5,
        height=620,
        title=titulo,
    )
    fig.update_layout(mapbox_style='open-street-map', margin=dict(l=0, r=0, t=45, b=0), legend_title_text='Legenda')
    st.plotly_chart(fig, use_container_width=True, key=key)
    _render_legenda_cores_decisoria('Como ler as cores deste mapa')


def _mapa_linhas_distancia_plano(df: pd.DataFrame, destino: str, titulo: str, key: str):
    if df is None or df.empty:
        st.info('Não há dados de distância para desenhar as linhas.')
        return
    lat_dest = f'lat_{destino}'
    lon_dest = f'lon_{destino}'
    dist_col = f'distancia_{destino}_km'
    if not {'lat','lon',lat_dest,lon_dest,dist_col}.issubset(df.columns):
        st.info('A base ainda não possui os campos necessários para desenhar linhas de distância nesta camada.')
        return
    tmp = df.copy()
    for c in ['lat','lon',lat_dest,lon_dest,dist_col]:
        tmp[c] = pd.to_numeric(tmp[c], errors='coerce')
    tmp = tmp.dropna(subset=['lat','lon',lat_dest,lon_dest,dist_col]).sort_values(dist_col, ascending=False).head(120)
    if tmp.empty:
        st.info('Não há registros com origem e destino georreferenciados para este mapa.')
        return
    fig = go.Figure()
    for _, r in tmp.iterrows():
        fig.add_trace(go.Scattermapbox(
            lat=[r['lat'], r[lat_dest]],
            lon=[r['lon'], r[lon_dest]],
            mode='lines',
            line=dict(width=1),
            hoverinfo='text',
            text=f"{r.get('nome_area','')} → {destino.upper()}<br>{_fmt_float(r.get(dist_col),1)} km<br>{r.get('decisao_sugerida','')}",
            showlegend=False,
        ))
    fig.add_trace(go.Scattermapbox(
        lat=tmp['lat'], lon=tmp['lon'], mode='markers',
        marker=dict(size=9), text=tmp.get('nome_area',''), name='Comunidades/territórios',
        hoverinfo='text'
    ))
    fig.add_trace(go.Scattermapbox(
        lat=tmp[lat_dest], lon=tmp[lon_dest], mode='markers',
        marker=dict(size=11), text=tmp.get(f'{destino}_mais_proximo',''), name=destino.upper(),
        hoverinfo='text'
    ))
    fig.update_layout(mapbox_style='open-street-map', height=620, title=titulo, margin=dict(l=0,r=0,t=45,b=0), mapbox=dict(zoom=5, center=dict(lat=-14.5, lon=-56.0)))
    st.plotly_chart(fig, use_container_width=True, key=key)


def _render_plano_diretor_georreferenciamento():
    st.markdown('### 🧭 Georreferenciamento robusto para Plano Diretor da Atenção à Saúde')
    _nota_metodologica(
        'Frase orientadora da gestão',
        '“Quero ver visualmente onde estão os vazios assistenciais e quem está desassistido.” Esta seção cruza distância, ruralidade, territórios tradicionais, vulnerabilidade, saneamento, escolaridade, UBS, hospitais e transporte sanitário.',
        'info'
    )
    try:
        dados = montar_plano_diretor_geo()
    except Exception as exc:
        st.error('Não foi possível montar o Plano Diretor APS nesta execução. O mapa de distâncias original permanece preservado abaixo.')
        st.exception(exc)
        return
    territorios = dados.get('territorios', pd.DataFrame())
    resumo = dados.get('resumo_municipal', pd.DataFrame())
    hospitais = dados.get('hospitais', pd.DataFrame())
    meta = dados.get('metadados', {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Territórios mapeados', _fmt_int(meta.get('territorios_mapeados', 0)), help='Inclui assentamentos, terras indígenas/territórios tradicionais e outras camadas com coordenada disponível no banco.')
    c2.metric('UBS/APS com coordenada', _fmt_int(meta.get('ubs_com_coordenada', 0)), help='Pontos de referência da APS usados no cálculo de proximidade territorial.')
    c3.metric('Hospitais/retaguarda', _fmt_int(meta.get('hospitais_com_coordenada', 0)), help='Quando a base CNES hospitalar estiver qualificada, mede distância até hospitais/UPA/maternidade/retaguarda.')
    classe_tmp = territorios.get('classificacao_idt_aps', pd.Series(dtype=str)).astype(str) if not territorios.empty else pd.Series(dtype=str)
    score_tmp = pd.to_numeric(territorios.get('score_idt_aps', pd.Series(dtype=float)), errors='coerce') if not territorios.empty else pd.Series(dtype=float)
    criticos = int(classe_tmp.str.contains('Crítico|Muito alta|Alta|Ruim', regex=True, na=False).sum()) if not territorios.empty else 0
    if criticos == 0 and not score_tmp.dropna().empty:
        criticos = int((score_tmp >= score_tmp.quantile(0.80)).sum())
    c4.metric('Áreas em alerta IDT-APS', f"{_fmt_int(criticos)} ({_pct(criticos, len(territorios))})", help='Áreas com maior desassistência territorial estimada no índice consolidado. Se não houver Crítico formal, o sistema usa o grupo de maior alerta relativo como triagem.')
    _render_geo_infografico_abertura(meta, territorios, resumo)

    if meta.get('hospital_base_status') != 'qualificada':
        st.warning('Camada hospitalar ainda não qualificada: o sistema NÃO deve interpretar distância hospitalar como oficial. O IDT-APS foi recalculado sem hospital, usando distância até UBS, ruralidade e vulnerabilidade social. Para ativar a distância hospitalar, importe uma base validada de hospitais/UPA/maternidades/retaguarda com coordenadas.')
    if meta.get('inep_base_status') != 'carregada':
        st.info('Camada socioeducacional municipal/INEP ainda não carregada para georreferenciamento. O sistema preserva o espaço para escolaridade, alfabetização e escolas rurais, mas não deve usar esses campos como critério até a importação/validação.')

    tabs = st.tabs(['🧭 Mapa consolidado IDT-APS', '🏥 Distância até hospital', '🚜 Ruralidade, indígenas e quilombolas', '📉 Vulnerabilidade territorial', '🚌 Transporte sanitário', '🗂 Cadastro UBS editável', '📄 Relatório territorial'])

    st.info('Importação e consolidação de bases foram migradas para a Central da Base de Dados → Plano Diretor / Geo. Esta tela fica focada em análise espacial, mapas de distância e validação territorial.')

    with tabs[0]:
        st.markdown('#### Índice de Desassistência Territorial da APS — IDT-APS')
        st.caption('Índice preliminar para priorização. Se a camada hospitalar não estiver qualificada, o cálculo NÃO usa distância hospitalar. O mapa consolidado deve ser lido como triagem territorial e não substitui validação de campo.')
        if territorios.empty:
            st.warning('Ainda não há pontos territoriais suficientes para consolidar o IDT-APS.')
        else:
            _render_geo_infografico_idt(territorios, resumo)
            _mapa_pontos_plano_diretor(territorios, 'Mapa consolidado de desassistência territorial — IDT-APS', key='plano_diretor_idt_mapa')
            cols = [c for c in ['municipio','nome_area','tipo_territorio','score_idt_aps','classificacao_idt_aps','distancia_ubs_km','classe_distancia_ubs','distancia_hospital_km','classe_distancia_hospital','score_vulnerabilidade_mds','decisao_sugerida','tipo_transporte_sugerido'] if c in territorios.columns]
            render_html_table(territorios[cols].head(80), titulo='Ranking das áreas mais desassistidas', subtitulo='Quanto maior o score IDT-APS, maior a prioridade para validação e plano territorial.', max_rows=80, max_text=180)
            _download_csv(territorios[cols], 'idt_aps_areas_desassistidas.csv', 'Baixar ranking IDT-APS')
        if not resumo.empty:
            cols_res = [c for c in ['municipio','regiao_saude','prioridade_plano_diretor','classificacao_idt_aps','score_idt_aps','territorios_mapeados','areas_criticas','areas_rurais','territorios_tradicionais','pct_populacao_cadunico','pct_populacao_bolsa_familia','perc_escolas_rurais','populacao_indigena','populacao_quilombola'] if c in resumo.columns]
            render_html_table(resumo[cols_res].head(60), titulo='Prioridade municipal para Plano Diretor', subtitulo='Consolida leitura por município para decisão estratégica estadual.', max_rows=60, max_text=140)


    with tabs[1]:
        st.markdown('#### Distância dos bairros/comunidades/assentamentos até hospital/retaguarda mais próxima')
        if hospitais.empty:
            st.warning('A base atual ainda não possui uma camada hospitalar CNES suficientemente qualificada com coordenadas. A estrutura já está pronta para calcular assim que hospitais, UPA, maternidades e retaguarda regional forem importados/validados.')
            st.info('Encaminhamento: importar CNES hospitalar completo ou planilha estadual com hospitais de referência, maternidades, UPA, pronto atendimento e unidades com internação.')
        else:
            _mapa_linhas_distancia_plano(territorios, 'hospital', 'Linhas de distância até hospital/retaguarda mais próxima', 'plano_diretor_linhas_hospital')
            cols_h = [c for c in ['municipio','nome_area','tipo_territorio','hospital_mais_proximo','municipio_hospital_mais_proximo','distancia_hospital_km','classe_distancia_hospital','tipo_transporte_sugerido','decisao_sugerida'] if c in territorios.columns]
            render_html_table(territorios[cols_h].sort_values('distancia_hospital_km', ascending=False).head(80), titulo='Maiores distâncias até hospital/retaguarda', subtitulo='Apoia análise de fluxo regional, urgência, parto, transporte sanitário e retaguarda clínica.', max_rows=80, max_text=180)

    with tabs[1]:
        st.markdown('#### Ruralidade, assentamentos, indígenas, quilombolas e territórios tradicionais')
        st.caption('Esta visão reforça que distância rural não equivale à distância urbana. Estrada, rios, dispersão, sazonalidade e vulnerabilidade podem ampliar a barreira real de acesso.')
        if territorios.empty:
            st.info('Sem territórios georreferenciados para esta análise.')
        else:
            filtro = territorios[territorios.get('score_ruralidade', pd.Series(dtype=float)).fillna(0) >= 70].copy()
            _mapa_pontos_plano_diretor(filtro if not filtro.empty else territorios, 'Mapa de ruralidade e territórios tradicionais', color_col='tipo_territorio', size_col='score_idt_aps', key='plano_diretor_ruralidade_mapa')
            cols_r = [c for c in ['municipio','nome_area','tipo_territorio','distancia_ubs_km','classe_distancia_ubs','score_ruralidade','score_idt_aps','classificacao_idt_aps','decisao_sugerida'] if c in territorios.columns]
            render_html_table((filtro if not filtro.empty else territorios)[cols_r].head(100), titulo='Territórios rurais/tradicionais priorizados', subtitulo='Inclui assentamentos e terras indígenas disponíveis. Quilombolas/ribeirinhos serão incorporados quando houver camada com coordenada ou base municipal validada.', max_rows=100, max_text=180)
            st.info('Espaço reservado: quando a SES receber bases municipais de comunidades quilombolas, ribeirinhas, aldeias, comunidades rurais e rotas, elas devem entrar nesta camada sem recriar o sistema.')

    with tabs[1]:
        st.markdown('#### Vulnerabilidade territorial cruzada com acesso')
        if territorios.empty:
            st.info('Sem pontos territoriais para cruzar vulnerabilidade.')
        else:
            _mapa_pontos_plano_diretor(territorios.sort_values('score_vulnerabilidade_mds', ascending=False), 'Vulnerabilidade social + distância + ruralidade', color_col='classificacao_idt_aps', size_col='score_vulnerabilidade_mds', key='plano_diretor_vulnerabilidade_mapa')
            cols_v = [c for c in ['municipio','nome_area','tipo_territorio','score_vulnerabilidade_mds','distancia_ubs_km','distancia_hospital_km','score_idt_aps','classificacao_idt_aps','decisao_sugerida'] if c in territorios.columns]
            render_html_table(territorios[cols_v].head(100), titulo='Áreas onde vulnerabilidade aumenta o peso da distância', subtitulo='Prioriza locais onde pobreza, CadÚnico/Bolsa Família, saneamento e ruralidade tornam a distância mais grave.', max_rows=100, max_text=180)

    with tabs[1]:
        st.markdown('#### Indicação preliminar de transporte sanitário e logística territorial')
        if territorios.empty:
            st.info('Sem dados para sugerir transporte.')
        else:
            trans = territorios.copy()
            if 'tipo_transporte_sugerido' in trans.columns:
                cont = trans['tipo_transporte_sugerido'].value_counts().reset_index()
                cont.columns = ['tipo_transporte_sugerido','areas']
                fig = px.bar(cont, x='areas', y='tipo_transporte_sugerido', orientation='h', title='Tipos de apoio logístico sugeridos pela leitura territorial')
                fig.update_layout(height=420, margin=dict(l=0,r=0,t=45,b=0))
                st.plotly_chart(fig, use_container_width=True, key='plano_diretor_transporte_bar')
            cols_t = [c for c in ['municipio','nome_area','tipo_territorio','distancia_ubs_km','distancia_hospital_km','tipo_transporte_sugerido','decisao_sugerida'] if c in trans.columns]
            render_html_table(trans[cols_t].head(120), titulo='Carteira preliminar de transporte sanitário territorial', subtitulo='Base para estudar ônibus, micro-ônibus, veículo leve, 4x4, veículo aquático, unidade móvel ou rota rural programada.', max_rows=120, max_text=200)

    with tabs[1]:
        st.markdown('#### Cadastro territorial editável das UBS')
        st.caption('Espaço preparado para receber correções dos municípios: endereço, bairro, zona, coordenada corrigida, fonte da coordenada e status de validação.')
        info = garantir_tabela_cadastro_territorial()
        st.success(f"Tabela técnica preparada no banco: ubs_cadastro_editavel. Registros atuais: {info.get('registros', 0)}.")
        cadastro = carregar_cadastro_ubs_editavel()
        if cadastro.empty:
            st.info('Ainda não há correções manuais registradas. Quando os municípios enviarem informações por sistema, e-mail ou planilha, os registros poderão ser gravados aqui sem alterar a base bruta.')
        else:
            render_html_table(cadastro.head(100), titulo='Registros de UBS corrigidos/validados', subtitulo='Controle de governança cadastral das coordenadas e endereços.', max_rows=100, max_text=160)
        st.markdown('**Campos previstos:** município, CNES, nome, endereço original/corrigido, bairro, zona, latitude/longitude original, latitude/longitude corrigida, fonte, status, observação, informado por e data.')

    with tabs[1]:
        st.markdown('#### Relatório territorial para Plano Diretor')
        texto = dados.get('relatorio', '')
        st.text_area('Texto-base territorial', value=texto, height=260, key='plano_diretor_relatorio_texto')
        st.markdown('##### Como usar')
        st.write('Use esta síntese para abrir relatório técnico, despacho ou apresentação. A tabela do IDT-APS deve ser validada com coordenadorias, ERS, municípios e conhecimento local antes de virar decisão de financiamento.')



def render():
    st.subheader('Georreferenciamento da Saúde')
    st.markdown('\n        <div class="info-box">\n        Esta etapa organiza as camadas espaciais da plataforma e prepara o sistema para mapas de vazios assistenciais, oferta de serviços, territórios especiais e riscos ambientais. Nesta primeira versão, o foco é diagnosticar a qualidade das camadas e iniciar uma visualização territorial segura.\n        </div>\n        ', unsafe_allow_html=True)
    status = qualidade_georreferencia()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Municípios', status.get('municipios', 0))
    c2.metric('Com coordenadas', status.get('coordenadas', 0))
    c3.metric('Com área', status.get('area', 0))
    c4.metric('Cache local', 'Sim' if status.get('cache_existe') else 'Não')
    with st.expander('Atualizar georreferenciamento municipal IBGE/Malhas', expanded=False):
        st.info('Use esta rotina apenas quando precisar reconstruir a camada municipal de coordenadas, área e densidade. As análises abaixo usam preferencialmente as bases já carregadas no banco.')
        forcar = st.checkbox('Forçar novo download das malhas IBGE', value=False)
        if st.button('Carregar camada municipal IBGE/Malhas', type='primary', use_container_width=True):
            with st.spinner('Gerando georreferenciamento municipal. Aguarde alguns minutos na primeira execução...'):
                df_geo = gerar_georreferencia_municipal_mt(forcar_download=bool(forcar))
                info = importar_georreferencia_municipal(df_geo)
                atualizacao = atualizar_base_municipal()
            st.success(f"Camada territorial atualizada: {info['coordenadas_atualizadas']} municípios com coordenadas, {info['areas_preenchidas']} áreas preenchidas e {info['indicadores_inseridos']} indicadores territoriais inseridos.")
            st.caption(f"Base consolidada atualizada: {atualizacao['municipios']} municípios em {atualizacao['atualizado_em']}.")

    st.markdown('---')
    _render_plano_diretor_georreferenciamento()
    st.markdown('---')
    diag = diagnosticar_camadas_geograficas()
    resumo = diag['resumo']
    base_mapa = montar_base_mapa_municipal()
    tab_mapa_distancias, tab_estrategico, tab_cruzamentos_decisorios, tab_validacao_territorial, tab_confiabilidade, tab_parametros_ms, tab_catalogo_bases, tab_vazios, tab_qualificacao_ubs, tab_coord_ubs, tab_pendencias, tab_distancias, tab_acesso_rural, tab_bairros, tab_vazios_intra, tab_determinantes, tab_diag, tab_mapa, tab_camadas, tab_pontos, tab_base, tab_metodo = st.tabs(['🗺️ Mapa de Distâncias', 'Mapa estratégico', '⭐ Cruzamentos decisórios', 'Validação territorial', 'Confiabilidade da base', 'Parâmetros MS', 'Catálogo bases públicas', 'Vazios assistenciais', 'Qualificação UBS', 'Coordenadas UBS', 'Pendências geográficas', 'Distâncias e acesso', 'Acesso rural APS', 'Bairros/localidades', 'Vazios intramunicipais', 'Determinantes sociais', 'Diagnóstico de camadas', 'Mapa preliminar', 'Camadas territoriais', 'Unidades e pontos', 'Base municipal geográfica', 'Metodologia e próximos passos'])
    with tab_estrategico:
        st.markdown('### Mapa estratégico da APS')
        st.caption('Mapa multicamadas com polígonos municipais e pontos territoriais. Use esta visão para enxergar vazios, capacidade/necessidade de equipe e territórios especiais no mesmo espaço.')
        if base_mapa.empty:
            st.warning('A base municipal geográfica ainda não está disponível.')
        else:
            regioes = sorted([r for r in base_mapa.get('regiao_saude', pd.Series()).dropna().unique()])
            col1, col2, col3 = st.columns([1.1, 1.2, 1.7])
            regiao_sel = col1.selectbox('Região', ['Todas'] + regioes, key='geo_v2_regiao')
            classes = ['Todas'] + sorted([c for c in base_mapa.get('classe_geo_preliminar', pd.Series()).dropna().unique()])
            classe_sel = col2.selectbox('Classe de prioridade', classes, key='geo_v2_classe')
            camadas_sel = col3.multiselect('Camadas de pontos', ['Estabelecimentos de saúde', 'Assentamentos', 'Terras Indígenas', 'Ocorrências ambientais'], default=['Estabelecimentos de saúde', 'Assentamentos', 'Terras Indígenas', 'Ocorrências ambientais'], key='geo_v2_camadas')
            df_geo = base_mapa.copy()
            if regiao_sel != 'Todas' and 'regiao_saude' in df_geo.columns:
                df_geo = df_geo[df_geo['regiao_saude'] == regiao_sel]
            if classe_sel != 'Todas' and 'classe_geo_preliminar' in df_geo.columns:
                df_geo = df_geo[df_geo['classe_geo_preliminar'] == classe_sel]
            municipios_opcoes = ['Visão estadual'] + sorted(df_geo['municipio'].dropna().astype(str).unique().tolist())
            municipio_foco = st.selectbox('Foco territorial', municipios_opcoes, key='geo_v2_foco')
            foco_real = None if municipio_foco == 'Visão estadual' else municipio_foco
            pontos = montar_pontos_multicamadas(camadas_sel)
            if not pontos.empty and (not df_geo.empty):
                muni_validos = set(df_geo['municipio'].astype(str))
                pontos = pontos[pontos.get('municipio', pd.Series(dtype=str)).astype(str).isin(muni_validos) | pontos.get('municipio', pd.Series(dtype=str)).astype(str).eq('')]
            geojson = obter_geojson_municipal_filtrado(df_geo)
            m1, m2, m3, m4 = st.columns(4)
            m1.metric('Municípios no mapa', len(df_geo))
            m2.metric('Polígonos', len(geojson.get('features', [])))
            m3.metric('Pontos válidos', len(pontos))
            m4.metric('Muito alta/Alta', int(df_geo.get('classe_geo_preliminar', pd.Series()).isin(['Muito alta', 'Alta']).sum()))

            inconsistencias_pontos = obter_inconsistencias_pontos_mapa(limite=2000)
            if isinstance(inconsistencias_pontos, pd.DataFrame) and not inconsistencias_pontos.empty:
                st.warning(
                    f"Controle de qualidade geográfica: {len(inconsistencias_pontos)} pontos foram bloqueados do mapa principal por estarem fora da faixa de MT ou divergirem da malha do município informado."
                )
                with st.expander('Ver pontos bloqueados do mapa estratégico', expanded=False):
                    cols_inc = [c for c in [
                        'camada', 'rotulo', 'municipio', 'codigo_ibge', 'lat', 'lon',
                        'status_validacao_geografica', 'municipio_geografico_estimado',
                        'alerta_municipio_geografico'
                    ] if c in inconsistencias_pontos.columns]
                    render_html_table(
                        inconsistencias_pontos[cols_inc].head(500),
                        titulo='Pontos bloqueados por inconsistência geográfica',
                        subtitulo='Esses registros não aparecem no mapa principal até validação/correção da coordenada ou do município informado.',
                        max_rows=500,
                        max_text=160,
                    )
                    _download_csv(inconsistencias_pontos[cols_inc], 'pontos_bloqueados_mapa_estrategico.csv', 'Baixar pontos bloqueados')
            mapa_col, painel_col = st.columns([2.2, 1])
            with mapa_col:
                _render_mapa_estrategico(df_geo, geojson, pontos, foco_real)
            with painel_col:
                info = resumo_municipio_geografico(foco_real) if foco_real else {}
                if not info and foco_real is None:
                    st.markdown('#### Leitura estadual')
                    st.write('Use os filtros para alternar entre regiões, classes de prioridade e camadas territoriais.')
                    st.metric('Municípios com assentamentos', int((pd.to_numeric(df_geo.get('qtd_assentamentos'), errors='coerce').fillna(0) > 0).sum()))
                    st.metric('Municípios com terras indígenas', int((pd.to_numeric(df_geo.get('qtd_terras_indigenas_intersecoes'), errors='coerce').fillna(0) > 0).sum()))
                    st.metric('Municípios com ocorrências ambientais', int((pd.to_numeric(df_geo.get('qtd_ocorrencias_ambientais'), errors='coerce').fillna(0) > 0).sum()))
                else:
                    _render_card_municipio_geo(info)
            _render_legenda_pontos_estrategicos(camadas_sel, pontos)
            st.markdown('#### Ranking georreferenciado filtrado')
            cols_rank = [c for c in ['municipio', 'regiao_saude', 'indice_geo_preliminar', 'classe_geo_preliminar', 'populacao', 'total_equipes_aps', 'total_ubs', 'pop_por_equipe', 'pop_por_ubs', 'area_km2', 'qtd_assentamentos', 'qtd_terras_indigenas_intersecoes', 'qtd_ocorrencias_ambientais'] if c in df_geo.columns]
            ranking_geo = df_geo[cols_rank].sort_values('indice_geo_preliminar', ascending=False)
            render_html_table(ranking_geo, titulo='Ranking georreferenciado filtrado', subtitulo='Colunas renomeadas para leitura executiva. Use o CSV para análise completa.', max_rows=20)
            _download_csv(df_geo[cols_rank], 'ranking_georreferenciamento_estrategico.csv', 'Baixar ranking georreferenciado')
    with tab_cruzamentos_decisorios:
        st.success('Patch V2 aplicado: esta aba concentra os cruzamentos decisórios de acesso, distância, vazios, concentração, vulnerabilidade e capacidade APS.')
        _render_cruzamentos_geo_decisorios()
    with tab_mapa_distancias:
        st.markdown('### 🗺️ Mapa de Distâncias — vazios assistenciais e acesso territorial')
        _nota_metodologica('Distância não é rota real', 'As linhas representam distância geodésica em linha reta conforme o modo selecionado. Elas não representam tempo de deslocamento, rota viária, adscrição oficial ou responsabilidade administrativa automática.', 'info')
        st.caption('Aba prioritária do georreferenciamento: mostra visualmente as distâncias entre territórios e pontos de cuidado. Use esta tela para discutir vazios assistenciais, população desassistida, transporte sanitário, rota rural, UBS satélite, unidade móvel e reorganização da APS.')
        tipo_mapa = st.radio('Escolha a camada principal do Mapa de Distâncias', ['Bairros/localidades/setores → UBS/APS', 'Assentamentos → UBS/APS', 'Hospitalar/leitos → hospital/retaguarda'], horizontal=True, key='geo_v19_tipo_mapa_distancia')
        inconsistencias_geo = pd.DataFrame()
        if tipo_mapa.startswith('Bairros'):
            resultado_b = calcular_distancias_bairros_localidades_aps()
            dist_mapa = resultado_b.get('distancias', pd.DataFrame()).copy()
            inconsistencias_geo = resultado_b.get('inconsistencias_geograficas', pd.DataFrame()).copy()
            diag_mapa = resultado_b.get('diagnostico', {})
            legenda_func = _render_legenda_distancias_bairros
            render_func = _render_mapa_distancias_bairros_localidades
            campo_nome = 'territorio_exibicao'
            nome_territorio = 'territórios intramunicipais'
            limite_padrao = 'Todas'
        elif tipo_mapa.startswith('Assentamentos'):
            resultado_a = calcular_distancias_assentamentos_ubs(usar_aproximacao_municipal=False)
            dist_mapa = resultado_a.get('distancias', pd.DataFrame()).copy()
            diag_mapa = resultado_a.get('diagnostico', {})
            legenda_func = _render_legenda_distancias
            render_func = _render_mapa_distancias_assentamentos
            campo_nome = 'assentamento'
            nome_territorio = 'assentamentos'
            limite_padrao = 'Todas'
        else:
            resultado_h = calcular_distancias_territorios_hospitais_retaguarda()
            dist_mapa = resultado_h.get('distancias', pd.DataFrame()).copy()
            diag_mapa = resultado_h.get('diagnostico', {})
            legenda_func = _render_legenda_distancias_hospitalar
            render_func = _render_mapa_distancias_hospitalar_retaguarda
            campo_nome = 'territorio_exibicao'
            nome_territorio = 'territórios até hospital/retaguarda'
            limite_padrao = 'Todas'
            if (not resultado_h.get('ok', False)) or ('distancia_hospital_km' not in dist_mapa.columns):
                st.warning(resultado_h.get('mensagem', 'Camada hospitalar ainda não está disponível. Importe/valide hospitais com coordenadas para ativar o mapa hospitalar.'))
                dist_mapa = pd.DataFrame()
        if tipo_mapa.startswith('Hospitalar'):
            modo_referencia_md = 'Referência física — hospital/retaguarda mais próximo, inclusive em outro município'
            st.info('Na camada hospitalar, a referência padrão é física/regional: o território é ligado ao hospital/retaguarda georreferenciado mais próximo. Essa leitura apoia regionalização, transporte sanitário e análise de retaguarda, mas não substitui pactuação oficial de referência.')
        else:
            modo_referencia_md = st.radio(
                'Referência para desenhar as linhas do mapa',
                ['Referência municipal — UBS/APS mais próxima dentro do próprio município', 'Referência física — UBS/APS fisicamente mais próxima, mesmo em outro município'],
                index=0,
                horizontal=False,
                key='geo_v20_modo_referencia_distancia',
                help='Para apresentação gerencial, use a referência municipal. A referência física serve para análise técnica de proximidade, fluxo intermunicipal e possível pactuação.'
            )
            if not dist_mapa.empty:
                dist_mapa = _preparar_modo_referencia_distancia(dist_mapa, modo_referencia_md)
        if dist_mapa.empty:
            st.warning('Não há distâncias calculadas para esta camada. Verifique se existem territórios com coordenadas e UBS/APS elegíveis georreferenciadas.')
            if isinstance(inconsistencias_geo, pd.DataFrame) and not inconsistencias_geo.empty:
                st.warning(f"{len(inconsistencias_geo)} registros foram bloqueados por divergência entre município textual e malha geográfica. Eles não entram no mapa principal nem nos indicadores de distância.")
                with st.expander('Ver registros bloqueados por divergência territorial', expanded=False):
                    cols_inc = [c for c in ['municipio', 'municipio_geografico_estimado', 'territorio_exibicao', 'bairro_ou_localidade', 'latitude', 'longitude', 'alerta_municipio_geografico'] if c in inconsistencias_geo.columns]
                    render_html_table(inconsistencias_geo[cols_inc].head(500))
        else:
            if isinstance(inconsistencias_geo, pd.DataFrame) and not inconsistencias_geo.empty:
                st.warning(f"Controle de qualidade territorial: {len(inconsistencias_geo)} registros foram bloqueados por divergência entre município textual e malha geográfica. Eles não entram no mapa principal nem nos indicadores de distância.")
                with st.expander('Ver registros bloqueados por divergência territorial', expanded=False):
                    cols_inc = [c for c in ['municipio', 'municipio_geografico_estimado', 'territorio_exibicao', 'bairro_ou_localidade', 'latitude', 'longitude', 'alerta_municipio_geografico'] if c in inconsistencias_geo.columns]
                    render_html_table(inconsistencias_geo[cols_inc].head(500))
            _render_hero_mapa_distancias(dist_mapa, tipo_mapa, diag_mapa)
            _render_geo_infografico_distancias(dist_mapa, tipo_mapa)
            colm1, colm2, colm3, colm4 = st.columns(4)
            colm1.metric('Territórios analisados', _fmt_int(len(dist_mapa)))
            colm2.metric('UBS/APS referência', _fmt_int(diag_mapa.get('referencias_usadas', dist_mapa.get('cnes_ubs_mais_proxima', pd.Series(dtype=str)).nunique())))
            colm3.metric('Distância média', f"{_fmt_float(pd.to_numeric(dist_mapa.get(_coluna_distancia_da_camada(tipo_mapa), pd.Series(dtype=float)), errors='coerce').mean(), 1)} km")
            colm4.metric('Críticos + distantes', _fmt_int(int((dist_mapa.get('classe_distancia_aps', pd.Series(dtype=str)) == 'Crítico').sum()) + int((dist_mapa.get('classe_distancia_aps', pd.Series(dtype=str)) == 'Distante').sum())))
            regioes_md = sorted([r for r in dist_mapa.get('regiao_saude', pd.Series(dtype=str)).dropna().unique()])
            col1, col2, col3 = st.columns([1.1, 1.1, 2.0])
            reg_md = col1.selectbox('Região de Saúde', ['Todas'] + regioes_md, key='geo_v19_mapa_dist_regiao')
            classe_md = col2.selectbox('Classe', ['Todas', 'Crítico', 'Distante', 'Atenção', 'Próximo'], key='geo_v19_mapa_dist_classe')
            busca_md = col3.text_input('Buscar município do território ou termo específico', key='geo_v19_mapa_dist_busca', help='Quando o termo for o nome exato de um município, o sistema filtra apenas os territórios desse município. Não usa o município da UBS mais próxima para evitar confusão.')
            df_md = dist_mapa.copy()
            if reg_md != 'Todas' and 'regiao_saude' in df_md.columns:
                df_md = df_md[df_md['regiao_saude'] == reg_md]
            if classe_md != 'Todas' and 'classe_distancia_aps' in df_md.columns:
                df_md = df_md[df_md['classe_distancia_aps'] == classe_md]
            aviso_busca_md = ''
            if busca_md:
                df_md, aviso_busca_md = _aplicar_busca_distancia(df_md, busca_md, campo_nome)
                if aviso_busca_md:
                    st.info(aviso_busca_md)
            st.markdown('#### Legenda')
            legenda_func()
            st.markdown('#### Mapa com linhas de distância')
            st.caption(f'O mapa mostra até os registros mais distantes/filtrados de {nome_territorio}. As linhas representam distância geodésica em linha reta conforme o modo selecionado. No modo gerencial padrão, cada território é conectado à UBS/APS mais próxima dentro do próprio município. No modo físico, a linha aponta para a UBS/APS fisicamente mais próxima, ainda que fique em município vizinho, apenas como alerta técnico. As linhas não representam rota viária ou tempo de deslocamento.')
            _nota_metodologica('Como ler este mapa de distâncias', 'Cada ponto representa um território da camada selecionada. Cada linha liga o território ao ponto de cuidado de referência. Verde indica proximidade, amarelo atenção, laranja distância relevante e vermelho situação crítica. A distância é geodésica e serve para triagem; rota real, estrada, rio, sazonalidade e transporte precisam de validação local.', 'info')
            render_func(df_md)
            st.markdown('#### Tabela do mapa')
            cols_base = [c for c in ['municipio', 'municipio_original_base', 'status_validacao_territorial', 'regiao_saude', campo_nome, 'tipo_territorio', 'populacao', 'modo_referencia_mapa', 'ubs_mais_proxima', 'cnes_ubs_mais_proxima', 'municipio_ubs_mais_proxima', ('distancia_hospital_km' if tipo_mapa.startswith('Hospitalar') else 'distancia_ubs_mais_proxima_km'), 'classe_distancia_aps', 'ubs_fisicamente_mais_proxima', 'municipio_ubs_fisicamente_mais_proxima', 'distancia_ubs_fisica_km', 'referencia_fora_municipio', 'diferenca_km_para_ubs_municipal', 'alerta_comparacao_fisica', 'ajuste_manual_municipio', 'motivo_ajuste_manual', 'qtd_equipes_aps_ubs', 'nome_territorio_ibge_original', 'alerta_nome_territorio'] if c in df_md.columns]
            if cols_base:
                render_html_table(_sort_seguro(df_md[cols_base], _coluna_distancia_da_camada(tipo_mapa), ascending=False).head(500))
            with st.expander('Como interpretar este mapa', expanded=False):
                st.markdown('\n                    - **Pontos territoriais**: assentamentos, bairros, localidades ou setores censitários, conforme a camada selecionada.\n                    - **Pontos azuis**: UBS/APS elegíveis, com CNES único vinculado a equipes APS/INE e coordenada válida.\n                    - **Linhas**: ligação entre o território e a UBS/APS elegível mais próxima.\n                    - **Cores**: indicam a classe de distância. Para bairros/localidades, a régua é mais sensível; para assentamentos, a régua considera distâncias rurais maiores.\n                    - **Limite metodológico**: a distância é em linha reta. Para estimar tempo de deslocamento, rota viária, estrada de chão, barreiras sazonais ou transporte sanitário, seria necessária outra camada de rede viária/roteamento.\n                    ')

    with tab_validacao_territorial:
        st.markdown('### Validação territorial assistida')
        _nota_metodologica('Validação territorial', 'Casos suspeitos não são necessariamente erro. Eles indicam divergência potencial entre município textual, setor/localidade, proximidade física e referência administrativa. O ajuste deve ser validado pela equipe APS/ERS/município.', 'alerta')
        st.caption('Use esta aba para localizar setores/localidades em divisa ou com possível município textual impreciso. Esses registros precisam de validação da equipe APS/ERS/município antes de uso gerencial definitivo.')

        st.markdown(
            """
            <div class="info-box">
            Quando a equipe local souber que um setor/localidade pertence, na prática territorial, a outro município, 
            registre o ajuste no arquivo <b>data/reference/ajustes_territorios_municipio.csv</b>. 
            O sistema preserva o município original, mas passa a usar o município validado para filtros, referência municipal e leitura gerencial.
            </div>
            """,
            unsafe_allow_html=True,
        )

        c1, c2 = st.columns([1, 1])
        limite_alerta = c1.number_input('Diferença mínima para alerta de divisa/intermunicipal (km)', min_value=0.0, max_value=200.0, value=20.0, step=5.0, key='geo_validacao_limite_divisa')
        somente_sem_ajuste = c2.checkbox('Mostrar apenas sem ajuste manual', value=True, key='geo_validacao_sem_ajuste')

        suspeitos = diagnosticar_territorios_suspeitos_divisa(float(limite_alerta))
        if isinstance(suspeitos, pd.DataFrame) and not suspeitos.empty and somente_sem_ajuste and 'ajuste_manual_municipio' in suspeitos.columns:
            suspeitos = suspeitos[~suspeitos['ajuste_manual_municipio'].astype(bool)].copy()

        m1, m2 = st.columns(2)
        m1.metric('Territórios suspeitos para validação', _fmt_int(len(suspeitos) if isinstance(suspeitos, pd.DataFrame) else 0))
        ajustes = carregar_ajustes_territoriais_manuais()
        m2.metric('Ajustes manuais cadastrados', _fmt_int(len(ajustes) if isinstance(ajustes, pd.DataFrame) else 0))

        if isinstance(suspeitos, pd.DataFrame) and not suspeitos.empty:
            st.warning('Atenção: esses casos não são necessariamente erro. Eles indicam que a UBS fisicamente mais próxima fora do município é muito mais próxima que a UBS municipal, ou que o município textual pode estar impreciso.')
            render_html_table(
                suspeitos.head(500),
                titulo='Fila de validação territorial',
                subtitulo='Priorize os casos com maior diferença entre UBS física e UBS municipal. Use o setor censitário como chave para ajuste manual.',
                max_rows=500,
                max_text=160,
            )
            _download_csv(suspeitos, 'territorios_suspeitos_validacao.csv', 'Baixar fila de validação territorial')
        else:
            st.success('Nenhum território suspeito encontrado com os filtros atuais.')

        modelo = gerar_modelo_ajustes_territoriais()
        st.markdown('#### Modelo para ajustes manuais')
        st.caption('Baixe o modelo, preencha os setores validados e salve como data/reference/ajustes_territorios_municipio.csv no projeto.')
        render_html_table(modelo, titulo='Modelo de arquivo de ajustes territoriais', max_rows=10, max_text=160)
        _download_csv(modelo, 'ajustes_territorios_municipio_modelo.csv', 'Baixar modelo de ajustes territoriais')

        if isinstance(ajustes, pd.DataFrame) and not ajustes.empty:
            st.markdown('#### Ajustes manuais já cadastrados')
            render_html_table(ajustes, titulo='Ajustes territoriais carregados', max_rows=200, max_text=160)



    with tab_confiabilidade:
        st.markdown('### Confiabilidade da base e saneamento')
        _nota_metodologica('Como interpretar esta aba', 'Esta aba mede a confiabilidade dos dados usados no georreferenciamento. Municípios com baixa confiabilidade devem entrar primeiro na fila de saneamento antes de apoiar decisão isolada sobre expansão, reorganização ou pactuação.', 'alerta')
        st.caption('Esta aba consolida o estado de confiabilidade dos dados usados no georreferenciamento e organiza as filas de saneamento.')

        conf = montar_confiabilidade_base()
        indicadores = conf.get('indicadores', pd.DataFrame())
        confiabilidade_municipal = conf.get('confiabilidade_municipal', pd.DataFrame())
        pendencias = conf.get('pendencias', {})

        if isinstance(indicadores, pd.DataFrame) and not indicadores.empty:
            c1, c2, c3, c4 = st.columns(4)
            def _valor_ind(nome):
                achado = indicadores[indicadores['indicador'].astype(str).eq(nome)]
                if achado.empty:
                    return '-'
                return achado.iloc[0]['valor']
            c1.metric('UBS/unidades únicas', _valor_ind('UBS/unidades únicas por CNES'))
            c2.metric('UBS com coordenada', _valor_ind('UBS com coordenada válida'))
            c3.metric('UBS pendentes', _valor_ind('UBS pendentes de coordenada'))
            c4.metric('Pontos bloqueados', _valor_ind('Pontos bloqueados do mapa estratégico'))

            render_html_table(
                indicadores,
                titulo='Indicadores gerais de confiabilidade',
                subtitulo='Leitura consolidada da qualidade geográfica atual da base.',
                max_rows=20,
                max_text=180,
            )

        st.markdown('#### Selo de confiabilidade por município')
        if isinstance(confiabilidade_municipal, pd.DataFrame) and not confiabilidade_municipal.empty:
            filtro_selo = st.multiselect(
                'Filtrar selo de confiabilidade',
                options=sorted(confiabilidade_municipal['selo_confiabilidade'].dropna().astype(str).unique().tolist()),
                default=[],
                key='geo_conf_filtro_selo'
            )
            tabela_conf = confiabilidade_municipal.copy()
            if filtro_selo:
                tabela_conf = tabela_conf[tabela_conf['selo_confiabilidade'].isin(filtro_selo)]
            render_html_table(
                tabela_conf.head(500),
                titulo='Confiabilidade municipal',
                subtitulo='Mostra a situação de UBS georreferenciadas, pendências e territórios que exigem validação.',
                max_rows=500,
                max_text=160,
            )
            _download_csv(tabela_conf, 'confiabilidade_municipal.csv', 'Baixar confiabilidade municipal')
        else:
            st.info('Confiabilidade municipal indisponível.')

        st.markdown('#### Filas de saneamento')
        abas_san = st.tabs(['UBS sem coordenada', 'Territórios suspeitos', 'Pontos bloqueados', 'Coordenadas validadas', 'Ajustes territoriais'])
        with abas_san[0]:
            df = pendencias.get('ubs_sem_coordenada', pd.DataFrame())
            if isinstance(df, pd.DataFrame) and not df.empty:
                render_html_table(df.head(500), titulo='UBS pendentes de coordenada', max_rows=500, max_text=150)
                _download_csv(df, 'ubs_sem_coordenada.csv', 'Baixar UBS sem coordenada')
            else:
                st.success('Nenhuma UBS sem coordenada encontrada na leitura atual.')
        with abas_san[1]:
            df = pendencias.get('territorios_suspeitos', pd.DataFrame())
            if isinstance(df, pd.DataFrame) and not df.empty:
                render_html_table(df.head(500), titulo='Territórios suspeitos para validação', max_rows=500, max_text=150)
                _download_csv(df, 'territorios_suspeitos_validacao.csv', 'Baixar territórios suspeitos')
            else:
                st.success('Nenhum território suspeito encontrado no critério atual.')
        with abas_san[2]:
            df = pendencias.get('pontos_bloqueados', pd.DataFrame())
            if isinstance(df, pd.DataFrame) and not df.empty:
                render_html_table(df.head(500), titulo='Pontos bloqueados do mapa estratégico', max_rows=500, max_text=150)
                _download_csv(df, 'pontos_bloqueados_mapa.csv', 'Baixar pontos bloqueados')
            else:
                st.success('Nenhum ponto bloqueado encontrado.')
        with abas_san[3]:
            df = pendencias.get('coordenadas_validadas', pd.DataFrame())
            if isinstance(df, pd.DataFrame) and not df.empty:
                render_html_table(df.head(500), titulo='Coordenadas de UBS validadas manualmente', max_rows=500, max_text=150)
                _download_csv(df, 'coordenadas_ubs_validadas.csv', 'Baixar coordenadas validadas')
            else:
                st.info('Nenhum arquivo de coordenadas validadas foi carregado ainda.')
        with abas_san[4]:
            df = pendencias.get('ajustes_territoriais', pd.DataFrame())
            if isinstance(df, pd.DataFrame) and not df.empty:
                render_html_table(df.head(500), titulo='Ajustes territoriais manuais', max_rows=500, max_text=150)
                _download_csv(df, 'ajustes_territoriais_manuais.csv', 'Baixar ajustes territoriais')
            else:
                st.info('Nenhum ajuste territorial manual foi carregado ainda.')

        with st.expander('Como interpretar a confiabilidade', expanded=False):
            st.markdown("""
            - **Alta confiabilidade:** a maioria das UBS está georreferenciada e há poucos alertas territoriais.
            - **Média confiabilidade:** há boa base para análise, mas existem pendências que devem ser revisadas.
            - **Baixa confiabilidade / em validação:** exige saneamento antes de apoiar decisão isolada.
            - **Pontos bloqueados** não aparecem no mapa principal até correção/validação.
            - **Coordenadas manuais validadas** têm prioridade sobre API/base automática.
            """)




    with tab_parametros_ms:
        st.markdown('### Parâmetros Ministério da Saúde — equipes e UBS necessárias')
        _nota_metodologica('Leitura correta dos parâmetros MS', 'A estimativa indica necessidade potencial de equipes e UBS. Ela não define automaticamente construção de unidade. A decisão depende de validação territorial, capacidade física real, rotas, adscrição, ruralidade, vulnerabilidade e planejamento regional.', 'info')
        st.caption('Estimativa gerencial baseada nos parâmetros populacionais de pessoas vinculadas por eSF. Não substitui planejamento de obra, rota real, adscrição ou validação técnica.')

        st.markdown(
            """
            <div class="info-box">
            A estimativa de equipes utiliza os parâmetros populacionais de pessoas vinculadas por eSF por porte municipal.
            A estimativa de UBS usa uma capacidade média configurável de equipes por UBS, apenas como referência gerencial.
            </div>
            """,
            unsafe_allow_html=True,
        )

        capacidade_ubs = st.number_input(
            'Capacidade média adotada de equipes por UBS',
            min_value=1,
            max_value=8,
            value=4,
            step=1,
            key='param_ms_capacidade_ubs',
            help='Parâmetro gerencial configurável. A PNAB menciona 4 equipes por UBS como referência de potencial resolutivo, mas a decisão real depende da estrutura física, território, fluxo e validação local.'
        )
        resumo_ms = resumo_parametros_ms(int(capacidade_ubs))
        gerencial_ms = resumo_gerencial_parametros_ms(int(capacidade_ubs))
        base_ms = gerencial_ms.get('base', pd.DataFrame())

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric('Municípios analisados', _fmt_int(resumo_ms.get('municipios', 0)))
        m2.metric('Déficit total estimado de equipes', _fmt_int(resumo_ms.get('deficit_total_equipes', 0)))
        m3.metric('Déficit total estimado de UBS', _fmt_int(resumo_ms.get('deficit_total_ubs', 0)))
        m4.metric('Municípios com déficit de equipes', _fmt_int(resumo_ms.get('municipios_com_deficit_equipes', 0)))
        m5.metric('Municípios com déficit de UBS', _fmt_int(resumo_ms.get('municipios_com_deficit_ubs', 0)))


        st.markdown('#### Leitura gerencial dos déficits')
        resumo_tipo_ms = gerencial_ms.get('resumo_tipo', pd.DataFrame())
        if isinstance(resumo_tipo_ms, pd.DataFrame) and not resumo_tipo_ms.empty:
            render_html_table(
                resumo_tipo_ms,
                titulo='Resumo por tipo de déficit',
                subtitulo='Agrupa os municípios conforme a natureza do déficit estimado: equipes, UBS, ambos ou nenhum.',
                max_rows=10,
                max_text=160,
            )

        col_top1, col_top2 = st.columns(2)
        top_ubs = gerencial_ms.get('top_ubs', pd.DataFrame())
        top_eq = gerencial_ms.get('top_equipes', pd.DataFrame())
        with col_top1:
            st.markdown('##### Maiores déficits estimados de UBS')
            if isinstance(top_ubs, pd.DataFrame) and not top_ubs.empty:
                cols_top = [c for c in ['municipio', 'regiao_saude', 'ubs_existentes', 'ubs_necessarias_estimadas', 'deficit_estimado_ubs', 'sintese_gerencial_parametro_ms'] if c in top_ubs.columns]
                render_html_table(top_ubs[cols_top], max_rows=10, max_text=130)
            else:
                st.success('Nenhum déficit estimado de UBS pelos parâmetros adotados.')
        with col_top2:
            st.markdown('##### Maiores déficits estimados de equipes')
            if isinstance(top_eq, pd.DataFrame) and not top_eq.empty:
                cols_top = [c for c in ['municipio', 'regiao_saude', 'equipes_aps_existentes', 'equipes_esf_necessarias_estimadas', 'deficit_estimado_equipes', 'sintese_gerencial_parametro_ms'] if c in top_eq.columns]
                render_html_table(top_eq[cols_top], max_rows=10, max_text=130)
            else:
                st.success('Nenhum déficit estimado de equipes pelos parâmetros adotados.')


        st.markdown('#### Tabela de estimativa por município')
        if isinstance(base_ms, pd.DataFrame) and not base_ms.empty:
            filtro_reg = st.multiselect(
                'Filtrar Região de Saúde',
                options=sorted(base_ms['regiao_saude'].dropna().astype(str).unique().tolist()) if 'regiao_saude' in base_ms.columns else [],
                default=[],
                key='param_ms_filtro_regiao'
            )
            filtro_prior = st.multiselect(
                'Filtrar prioridade da análise',
                options=sorted(base_ms['prioridade_parametro_ms'].dropna().astype(str).unique().tolist()) if 'prioridade_parametro_ms' in base_ms.columns else [],
                default=[],
                key='param_ms_filtro_prioridade'
            )
            filtro_tipo_def = st.multiselect(
                'Filtrar tipo de déficit',
                options=sorted(base_ms['tipo_deficit_parametro_ms'].dropna().astype(str).unique().tolist()) if 'tipo_deficit_parametro_ms' in base_ms.columns else [],
                default=[],
                key='param_ms_filtro_tipo_deficit'
            )
            tabela = base_ms.copy()
            if filtro_reg and 'regiao_saude' in tabela.columns:
                tabela = tabela[tabela['regiao_saude'].isin(filtro_reg)]
            if filtro_prior and 'prioridade_parametro_ms' in tabela.columns:
                tabela = tabela[tabela['prioridade_parametro_ms'].isin(filtro_prior)]
            if filtro_tipo_def and 'tipo_deficit_parametro_ms' in tabela.columns:
                tabela = tabela[tabela['tipo_deficit_parametro_ms'].isin(filtro_tipo_def)]

            cols = [c for c in [
                'municipio', 'regiao_saude', 'populacao', 'faixa_porte_ms',
                'parametro_pessoas_por_esf', 'equipes_aps_existentes',
                'equipes_esf_necessarias_estimadas', 'deficit_estimado_equipes',
                'ubs_existentes', 'ubs_necessarias_estimadas', 'deficit_estimado_ubs',
                'populacao_por_equipe_existente', 'populacao_por_ubs_existente',
                'tipo_deficit_parametro_ms', 'prioridade_parametro_ms', 'leitura_parametro_ms', 'sintese_gerencial_parametro_ms'
            ] if c in tabela.columns]
            render_html_table(
                tabela[cols].head(500),
                titulo='Equipes e UBS necessárias — estimativa preliminar',
                subtitulo='Baseada nos parâmetros populacionais de eSF e capacidade média de equipes por UBS adotada na tela.',
                max_rows=500,
                max_text=180,
            )
            _download_csv(tabela[cols], 'parametros_ms_equipes_ubs_necessarias.csv', 'Baixar estimativa MS')
        else:
            st.warning('Base municipal indisponível para cálculo dos parâmetros.')

        with st.expander('Metodologia usada nesta estimativa', expanded=False):
            st.markdown("""
            **Equipes necessárias estimadas:**
            - até 20 mil habitantes: população ÷ 2.000;
            - 20.001 a 50 mil habitantes: população ÷ 2.500;
            - 50.001 a 100 mil habitantes: população ÷ 2.750;
            - acima de 100 mil habitantes: população ÷ 3.000.

            **UBS necessárias estimadas:**
            - equipes necessárias estimadas ÷ capacidade média de equipes por UBS adotada na tela.

            **Atenção:** esta leitura é preliminar e gerencial. Não define automaticamente construção de UBS. Use a coluna 'tipo de déficit' para separar: déficit de equipe, déficit de UBS, ambos ou nenhum. A decisão depende de distribuição territorial, rotas, estrutura física, adscrição, população rural, vulnerabilidade, equipes existentes e validação local.
            """)




    with tab_catalogo_bases:
        st.markdown('### Catálogo de bases públicas oficiais')
        st.caption('Área para organizar fontes planilhadas/downloadáveis: IBGE, INEP, DATASUS, Atlas/IDHM e outras bases públicas já existentes.')

        st.markdown(
            """
            <div class="info-box">
            Esta etapa não depende de preenchimento manual de dados. A ideia é localizar bases públicas oficiais já disponíveis,
            importar os arquivos e registrar a fonte, ano, tema e tabela de destino para rastreabilidade.
            </div>
            """,
            unsafe_allow_html=True,
        )

        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button('Carregar catálogo padrão de fontes', use_container_width=True, key='catalogo_btn_padrao'):
                try:
                    info = salvar_catalogo_padrao()
                    st.success(f"Catálogo padrão carregado: {info.get('linhas')} fontes.")
                except Exception as e:
                    st.error(f"Falha ao carregar catálogo padrão: {e}")
        with c2:
            modelo_fonte = gerar_modelo_registro_fonte_publica()
            st.download_button(
                'Baixar modelo de registro de fonte',
                data=modelo_fonte.to_csv(index=False).encode('utf-8-sig'),
                file_name='modelo_registro_fonte_publica.csv',
                mime='text/csv',
                use_container_width=True,
                key='catalogo_dl_modelo_fonte'
            )

        st.markdown('#### Matriz de priorização das bases')
        matriz = matriz_priorizacao_importacao()
        if isinstance(matriz, pd.DataFrame) and not matriz.empty:
            filtro_eixo = st.multiselect(
                'Filtrar eixo',
                options=sorted(matriz['eixo'].dropna().astype(str).unique().tolist()) if 'eixo' in matriz.columns else [],
                default=[],
                key='catalogo_filtro_eixo'
            )
            tabela_matriz = matriz.copy()
            if filtro_eixo and 'eixo' in tabela_matriz.columns:
                tabela_matriz = tabela_matriz[tabela_matriz['eixo'].isin(filtro_eixo)]
            cols_matriz = [c for c in [
                'eixo', 'base', 'temas', 'nivel_territorial', 'formato_esperado',
                'uso_no_sistema', 'prioridade', 'recomendacao', 'status', 'observacao'
            ] if c in tabela_matriz.columns]
            render_html_table(
                tabela_matriz[cols_matriz],
                titulo='Fontes públicas recomendadas',
                subtitulo='Lista orientativa de bases públicas para baixar/importar, sem preenchimento manual.',
                max_rows=100,
                max_text=180,
            )
            _download_csv(tabela_matriz[cols_matriz], 'catalogo_bases_publicas.csv', 'Baixar catálogo de bases públicas')
        else:
            st.warning('Catálogo de bases públicas indisponível.')

        st.markdown('#### Registrar base pública já importada')
        r1, r2, r3 = st.columns(3)
        eixo_reg = r1.selectbox('Eixo', ['IBGE', 'INEP', 'DATASUS', 'Atlas Brasil', 'Outro'], key='catalogo_reg_eixo')
        base_reg = r2.text_input('Nome da base', key='catalogo_reg_base')
        ano_reg = r3.text_input('Ano de referência', key='catalogo_reg_ano')
        arquivo_reg = st.text_input('Nome do arquivo baixado/importado', key='catalogo_reg_arquivo')
        tabela_reg = st.text_input('Tabela destino no sistema', key='catalogo_reg_tabela')
        fonte_reg = st.text_input('Link ou referência oficial da fonte', key='catalogo_reg_fonte')
        obs_reg = st.text_area('Observação', key='catalogo_reg_obs')
        if st.button('Registrar fonte importada', use_container_width=True, key='catalogo_btn_registrar'):
            if not base_reg or not tabela_reg:
                st.warning('Informe ao menos o nome da base e a tabela destino.')
            else:
                try:
                    info = registrar_base_publica_importada(
                        eixo=eixo_reg,
                        base=base_reg,
                        arquivo_nome=arquivo_reg,
                        tabela_destino=tabela_reg,
                        fonte_url=fonte_reg,
                        ano_referencia=ano_reg,
                        observacao=obs_reg,
                    )
                    st.success(f"Fonte registrada. Total de registros: {info.get('linhas')}.")
                except Exception as e:
                    st.error(f"Falha ao registrar fonte: {e}")

        st.markdown('#### Bases públicas registradas como importadas')
        importadas = carregar_bases_publicas_importadas()
        if isinstance(importadas, pd.DataFrame) and not importadas.empty:
            render_html_table(importadas, titulo='Rastreabilidade das bases importadas', max_rows=200, max_text=160)
            _download_csv(importadas, 'bases_publicas_importadas.csv', 'Baixar registro de bases importadas')
        else:
            st.info('Nenhuma base pública foi registrada como importada ainda.')

        with st.expander('Ordem prática recomendada', expanded=False):
            st.markdown("""
            1. **IBGE Censo 2022**: renda, alfabetização, domicílios, saneamento e entorno.
            2. **INEP**: INSE, IDEB, distorção idade-série e rendimento.
            3. **DATASUS/SINAN/SIM/SINASC**: agravos, mortalidade e nascidos vivos.
            4. **Atlas/IDHM**: camada contextual municipal.
            5. **SIH/SIA**: produção e internações, etapa posterior por ser mais pesada.
            """)



    with tab_vazios:
        st.markdown('### Mapa de calor e vazios assistenciais')
        st.caption('Camada de leitura territorial para identificar municípios com maior combinação de pressão por equipes, pressão por UBS, dispersão territorial, territórios especiais e risco ambiental.')
        if base_mapa.empty:
            st.warning('A base municipal geográfica ainda não está disponível.')
        else:
            vazios = identificar_vazios_assistenciais(base_mapa)
            regioes_v = sorted([r for r in vazios.get('regiao_saude', pd.Series()).dropna().unique()])
            col1, col2, col3 = st.columns([1.1, 1.1, 1.8])
            regiao_v = col1.selectbox('Região', ['Todas'] + regioes_v, key='geo_v3_vazios_regiao')
            niveis = ['Todos', 'Crítico', 'Alto', 'Médio', 'Bom/regular']
            nivel_v = col2.selectbox('Nível do vazio', niveis, key='geo_v3_vazios_nivel')
            tipo_v = col3.multiselect('Tipo predominante', sorted([t for t in vazios.get('tipo_vazio_predominante', pd.Series()).dropna().unique()]), default=[], key='geo_v3_vazios_tipo')
            df_v = vazios.copy()
            if regiao_v != 'Todas':
                df_v = df_v[df_v['regiao_saude'] == regiao_v]
            if nivel_v != 'Todos':
                df_v = df_v[df_v['nivel_vazio_assistencial'] == nivel_v]
            if tipo_v:
                df_v = df_v[df_v['tipo_vazio_predominante'].isin(tipo_v)]
            k1, k2, k3, k4 = st.columns(4)
            k1.metric('Municípios filtrados', len(df_v))
            k2.metric('Crítico/Alto', int(df_v.get('nivel_vazio_assistencial', pd.Series()).isin(['Crítico', 'Alto']).sum()))
            k3.metric('Score médio', _fmt_float(pd.to_numeric(df_v.get('score_vazio_assistencial'), errors='coerce').mean(), 1))
            k4.metric('Com território especial', int(df_v.get('flag_territorios_especiais', pd.Series(dtype=bool)).astype(bool).sum()))
            _render_legenda_vazios()
            geojson_v = obter_geojson_municipal_filtrado(df_v)
            mapa_col, tabela_col = st.columns([2.1, 1])
            with mapa_col:
                _render_mapa_vazios(df_v, geojson_v)
            with tabela_col:
                st.markdown('#### Top vazios assistenciais')
                cols_top = [c for c in ['municipio', 'regiao_saude', 'score_vazio_assistencial', 'nivel_vazio_assistencial', 'tipo_vazio_predominante'] if c in df_v.columns]
                render_html_table(df_v[cols_top].head(15))
            st.markdown('#### Ranking detalhado de vazios assistenciais')
            cols = [c for c in ['municipio', 'regiao_saude', 'score_vazio_assistencial', 'nivel_vazio_assistencial', 'tipo_vazio_predominante', 'motivos_vazio', 'populacao', 'total_equipes_aps', 'total_ubs', 'pop_por_equipe', 'pop_por_ubs', 'area_km2', 'qtd_assentamentos', 'qtd_terras_indigenas_intersecoes', 'qtd_ocorrencias_ambientais'] if c in df_v.columns]
            render_html_table(df_v[cols])
            _download_csv(df_v[cols], 'vazios_assistenciais_georreferenciados.csv', 'Baixar vazios assistenciais')
            st.markdown('#### Resumo regional dos vazios')
            resumo_reg = resumo_vazios_por_regiao(vazios)
            if regiao_v != 'Todas' and (not resumo_reg.empty):
                resumo_reg = resumo_reg[resumo_reg['regiao_saude'] == regiao_v]
            render_html_table(resumo_reg)
            _download_csv(resumo_reg, 'resumo_regional_vazios_assistenciais.csv', 'Baixar resumo regional')
    with tab_qualificacao_ubs:
        st.markdown('### Qualificação geográfica das UBS')
        _nota_metodologica('Coordenadas e confiabilidade', 'Coordenadas vindas de API/base automática devem ser tratadas como ponto de partida. Coordenadas validadas pela SES/ERS/município têm maior confiabilidade e devem prevalecer quando disponíveis.', 'info')
        st.caption('Esta etapa verifica se a tabela de estabelecimentos possui latitude/longitude oficial suficiente para cálculos reais de distância. O sistema não usa coordenadas aproximadas nesta análise.')
        qual = qualificar_unidades_aps_georreferenciadas()
        diag_ubs = qual.get('diagnostico', {})
        unidades_geo = qual.get('unidades', pd.DataFrame())
        sem_geo = qual.get('sem_coordenadas', pd.DataFrame())
        resumo_geo = qual.get('resumo_municipal', pd.DataFrame())
        q1, q2, q3, q4 = st.columns(4)
        q1.metric('Unidades/UBS na base', _fmt_int(diag_ubs.get('total_unidades')))
        q2.metric('Com coordenada oficial válida', _fmt_int(diag_ubs.get('com_coordenadas_validas')))
        q3.metric('Pendentes de coordenada', _fmt_int(diag_ubs.get('sem_coordenadas_validas')))
        q4.metric('Georreferenciamento', f"{_fmt_float(diag_ubs.get('percentual_georreferenciado'), 1)}%")
        st.info(str(diag_ubs.get('mensagem', '')))

        st.markdown("#### Plano de qualificação das UBS sem coordenada")
        st.markdown(
            """
            <div class="info-box">
            As unidades sem latitude/longitude válida não devem entrar no cálculo definitivo de distância.
            A ordem recomendada de qualificação é: <b>1)</b> buscar coordenada oficial CNES/MS; 
            <b>2)</b> cruzar com bases SES/ERS/município; <b>3)</b> usar endereço apenas como sugestão de triagem; 
            <b>4)</b> validar manualmente no mapa antes de liberar para cálculo.
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("#### Planilha de validação manual das UBS")
        st.markdown(
            """
            <div class="info-box">
            Para elevar a confiabilidade, baixe a planilha de validação, envie para APS/ERS/municípios e preencha 
            <b>latitude_validada</b>, <b>longitude_validada</b>, <b>fonte_validacao</b>, <b>validado_por</b> e <b>usar_no_calculo = Sim</b>. 
            Depois salve o arquivo validado em <b>data/reference/ubs_coordenadas_validadas.csv</b>. 
            As coordenadas validadas terão prioridade sobre API/base automática.
            </div>
            """,
            unsafe_allow_html=True,
        )
        planilha_validacao = gerar_planilha_validacao_ubs() if callable(gerar_planilha_validacao_ubs) else pd.DataFrame()
        validadas_manual = carregar_coordenadas_ubs_validadas() if callable(carregar_coordenadas_ubs_validadas) else pd.DataFrame()
        v1, v2 = st.columns(2)
        v1.metric("Registros na planilha de validação", _fmt_int(len(planilha_validacao) if isinstance(planilha_validacao, pd.DataFrame) else 0))
        v2.metric("Coordenadas manuais carregadas", _fmt_int(int(validadas_manual.get("coordenada_validada_ok", pd.Series(dtype=bool)).astype(bool).sum()) if isinstance(validadas_manual, pd.DataFrame) and not validadas_manual.empty else 0))
        if isinstance(planilha_validacao, pd.DataFrame) and not planilha_validacao.empty:
            _download_csv(planilha_validacao, "ubs_coordenadas_validacao_modelo.csv", "Baixar planilha de validação das UBS")
            with st.expander("Prévia da planilha de validação", expanded=False):
                render_html_table(planilha_validacao.head(200), max_rows=200, max_text=140)
        if isinstance(validadas_manual, pd.DataFrame) and not validadas_manual.empty:
            with st.expander("Coordenadas manuais carregadas no sistema", expanded=False):
                cols_val = [c for c in ["municipio", "cnes", "nome_unidade", "latitude_validada", "longitude_validada", "fonte_validacao", "validado_por", "data_validacao", "usar_no_calculo", "coordenada_validada_ok"] if c in validadas_manual.columns]
                render_html_table(validadas_manual[cols_val].head(300), max_rows=300, max_text=140)


        if str(diag_ubs.get('status_prontidao', '')).startswith('Indisponível'):
            st.warning('Conclusão: ainda não há base exata para calcular distância assentamento → UBS. A próxima necessidade técnica é obter coordenadas oficiais das unidades, preferencialmente por fonte pública ou base institucional validada.')
        else:
            st.success(str(diag_ubs.get('status_prontidao', '')))
        st.markdown('#### Colunas de coordenada detectadas')
        c1, c2 = st.columns(2)
        c1.write(f"Latitude: **{diag_ubs.get('coluna_latitude')}**")
        c2.write(f"Longitude: **{diag_ubs.get('coluna_longitude')}**")
        st.markdown('#### Resumo municipal da qualificação')
        if resumo_geo.empty:
            st.caption('Resumo municipal indisponível.')
        else:
            render_html_table(resumo_geo)
            _download_csv(resumo_geo, 'qualificacao_ubs_resumo_municipal.csv', 'Baixar resumo municipal')
        st.markdown('#### Fila técnica de unidades pendentes de coordenadas oficiais')
        if sem_geo.empty:
            st.success('Não há unidades pendentes de coordenadas oficiais válidas.')
        else:
            cols = [c for c in ['cnes', 'nome_unidade', 'tipo_unidade', 'municipio', 'codigo_ibge', 'endereco', 'status_georreferencia', 'status_confiabilidade_coordenada', 'prioridade_georreferenciamento', 'acao_recomendada_georreferenciamento', 'chave_busca_coordenada'] if c in sem_geo.columns]
            render_html_table(sem_geo[cols])
            _download_csv(sem_geo[cols], 'ubs_pendentes_georreferenciamento.csv', 'Baixar fila de UBS pendentes')
        with st.expander('Diretriz institucional para esta camada', expanded=False):
            st.markdown('\n                Para manter a confiabilidade do sistema, esta plataforma só deve calcular distância real até UBS quando houver latitude/longitude oficial da unidade.\n\n                Fontes possíveis para qualificação futura:\n                - base oficial institucional da SES/municípios com coordenadas validadas;\n                - serviço público oficial que exponha coordenadas das unidades;\n                - geocodificação controlada dos endereços, com validação técnica posterior.\n\n                Enquanto essa camada não estiver qualificada, a análise de distâncias permanece bloqueada para evitar interpretação incorreta.\n                ')
    with tab_coord_ubs:
        st.markdown('### Coordenadas oficiais das UBS')
        st.caption('Esta rotina tenta corrigir a lacuna principal do georreferenciamento: latitude e longitude reais das UBS. Ela coleta a API nacional de UBS do Ministério da Saúde, filtra localmente registros de Mato Grosso e, se a paginação nacional não alcançar MT, tenta busca direcionada por CNES e por código IBGE municipal. Atualiza a tabela de estabelecimentos por CNES ou, de forma conservadora, por município + nome da unidade. Não usa centroide nem aproximação.')
        st.warning('Use esta ação para qualificar a base antes de calcular distâncias assentamento → UBS. Se a fonte oficial não retornar coordenadas, o sistema manterá a distância bloqueada.')
        st.markdown('#### 0. Recuperar coordenadas reais já existentes na versão antiga')
        st.caption('A versão antiga do sistema continha uma base `geo_ubs_df.csv` com CNES, município, latitude e longitude. Esta rotina reaproveita apenas registros com CNES, município/IBGE de Mato Grosso e coordenada válida dentro do território estadual. Não usa centroide, não usa aproximação e não geocodifica endereço.')
        recuperar_antiga = st.button('Importar coordenadas recuperadas do sistema antigo', type='primary', use_container_width=True, key='geo_importar_ubs_sistema_antigo')
        if recuperar_antiga:
            with st.spinner('Importando coordenadas reais recuperadas da versão antiga e validando por CNES/MT...'):
                res_antigo = importar_coordenadas_ubs_sistema_antigo()
            if res_antigo.get('ok'):
                st.success(res_antigo.get('mensagem'))
            else:
                st.error(res_antigo.get('mensagem'))
            a0, a1, a2, a3, a4 = st.columns(5)
            a0.metric('Linhas lidas', _fmt_int(res_antigo.get('linhas_lidas')))
            a1.metric('Linhas válidas MT', _fmt_int(res_antigo.get('linhas_validas')))
            a2.metric('Atualizadas por CNES', _fmt_int(res_antigo.get('atualizadas_por_cnes')))
            a3.metric('Novas inseridas', _fmt_int(res_antigo.get('inseridas_novas')))
            a4.metric('Ignoradas', _fmt_int(res_antigo.get('ignoradas')))
            diag_antigo = res_antigo.get('diagnostico_pos') or {}
            if diag_antigo:
                st.markdown('##### Diagnóstico após recuperação')
                b1, b2, b3, b4 = st.columns(4)
                b1.metric('Unidades/UBS', _fmt_int(diag_antigo.get('total_unidades')))
                b2.metric('Com coordenada', _fmt_int(diag_antigo.get('com_coordenadas_validas')))
                b3.metric('Pendentes', _fmt_int(diag_antigo.get('sem_coordenadas_validas')))
                b4.metric('Georreferenciamento', f"{_fmt_float(diag_antigo.get('percentual_georreferenciado'), 1)}%")
                st.info(str(diag_antigo.get('mensagem', '')))
            amostra_validas = res_antigo.get('amostra_validas')
            if isinstance(amostra_validas, pd.DataFrame) and (not amostra_validas.empty):
                with st.expander('Amostra de coordenadas recuperadas e aceitas', expanded=False):
                    render_html_table(amostra_validas)
                    _download_csv(amostra_validas, 'ubs_coordenadas_recuperadas_aceitas.csv', 'Baixar amostra aceita')
            ignoradas_df = res_antigo.get('ignoradas_df')
            if isinstance(ignoradas_df, pd.DataFrame) and (not ignoradas_df.empty):
                with st.expander('Registros ignorados pela validação de segurança', expanded=False):
                    render_html_table(ignoradas_df)
                    _download_csv(ignoradas_df, 'ubs_coordenadas_recuperadas_ignoradas.csv', 'Baixar ignoradas')
        st.divider()
        st.markdown('#### 1. Separar UBS/APS elegíveis e tentar CNES/tbEstabelecimento')
        st.caption('Antes de buscar 100% das coordenadas, esta etapa separa quais estabelecimentos realmente devem entrar no cálculo de distância da APS e tenta uma fonte oficial do CNES/tbEstabelecimento com campos de latitude/longitude. Não usa centroide, não usa aproximação e não geocodifica endereço.')
        col_e1, col_e2 = st.columns([1, 1])
        with col_e1:
            classificar = st.button('Classificar elegibilidade APS', type='secondary', use_container_width=True, key='geo_classificar_elegibilidade_aps')
        with col_e2:
            importar_cnes = st.button('Tentar coordenadas oficiais CNES/tbEstabelecimento', type='primary', use_container_width=True, key='geo_importar_cnes_tbestabelecimento')
        if classificar:
            eleg = classificar_estabelecimentos_elegiveis_aps()
            if eleg.get('ok'):
                st.success(eleg.get('mensagem'))
            else:
                st.error(eleg.get('mensagem'))
            diag_e = eleg.get('diagnostico', {})
            e1, e2, e3, e4, e5 = st.columns(5)
            e1.metric('Total estabelecimentos', _fmt_int(diag_e.get('total_estabelecimentos')))
            e2.metric('Linhas elegíveis brutas', _fmt_int(diag_e.get('linhas_elegiveis_brutas')))
            e3.metric('Elegíveis APS únicos', _fmt_int(diag_e.get('elegiveis_aps')))
            e4.metric('Elegíveis com coordenada', _fmt_int(diag_e.get('elegiveis_com_coordenada')))
            e5.metric('Pendentes reais APS', _fmt_int(diag_e.get('elegiveis_pendentes')))
            st.info(f"Georreferenciamento entre elegíveis APS únicos: {_fmt_float(diag_e.get('percentual_elegiveis_georreferenciados'), 1)}%. Duplicidades removidas por CNES: {_fmt_int(diag_e.get('duplicidades_elegiveis_removidas'))}. Essa régua evita tratar hospitais, laboratórios, secretarias e duplicidades de estabelecimento como UBS para análise de distância.")
            resumo_e = eleg.get('resumo_municipal')
            pend_e = eleg.get('pendentes_elegiveis')
            with st.expander('Resumo municipal das UBS/APS elegíveis', expanded=True):
                if isinstance(resumo_e, pd.DataFrame) and (not resumo_e.empty):
                    render_html_table(resumo_e)
                    _download_csv(resumo_e, 'resumo_municipal_elegibilidade_aps.csv', 'Baixar resumo municipal')
                else:
                    st.caption('Resumo municipal indisponível.')
            with st.expander('Pendências reais de UBS/APS para distância', expanded=False):
                if isinstance(pend_e, pd.DataFrame) and (not pend_e.empty):
                    render_html_table(pend_e.head(500))
                    _download_csv(pend_e, 'ubs_aps_elegiveis_pendentes_coordenada.csv', 'Baixar pendências reais')
                else:
                    st.success('Não há pendências entre os estabelecimentos elegíveis APS pela regra atual.')
        if importar_cnes:
            with st.spinner('Buscando recursos públicos do CNES/tbEstabelecimento e tentando importar coordenadas oficiais por CNES...'):
                res_cnes = importar_coordenadas_cnes_tbestabelecimento()
            if res_cnes.get('ok'):
                st.success(res_cnes.get('mensagem'))
            else:
                st.warning(res_cnes.get('mensagem'))
            c1, c2, c3, c4 = st.columns(4)
            diag_pos = res_cnes.get('diagnostico_pos', {}) or {}
            eleg_pos = res_cnes.get('elegibilidade', {}) or {}
            c1.metric('Atualizadas por CNES', _fmt_int(res_cnes.get('atualizadas_por_cnes')))
            c2.metric('Com coordenada total', _fmt_int(diag_pos.get('com_coordenadas_validas')))
            c3.metric('Elegíveis APS', _fmt_int(eleg_pos.get('elegiveis_aps')))
            c4.metric('Elegíveis pendentes', _fmt_int(eleg_pos.get('elegiveis_pendentes')))
            with st.expander('Recursos CNES detectados', expanded=False):
                df_rec = res_cnes.get('recursos_detectados')
                if isinstance(df_rec, pd.DataFrame) and (not df_rec.empty):
                    render_html_table(df_rec)
                    _download_csv(df_rec, 'recursos_cnes_detectados.csv', 'Baixar recursos detectados')
                else:
                    st.caption('Nenhum recurso detectado.')
            with st.expander('Tentativas de leitura CNES/tbEstabelecimento', expanded=True):
                df_tent = res_cnes.get('tentativas')
                if isinstance(df_tent, pd.DataFrame) and (not df_tent.empty):
                    render_html_table(df_tent)
                    _download_csv(df_tent, 'tentativas_cnes_tbestabelecimento.csv', 'Baixar tentativas')
                else:
                    st.caption('Sem tentativas registradas.')
            with st.expander('Amostra de coordenadas CNES candidatas', expanded=False):
                df_cand = res_cnes.get('candidatos')
                if isinstance(df_cand, pd.DataFrame) and (not df_cand.empty):
                    render_html_table(df_cand.head(500))
                    _download_csv(df_cand, 'cnes_tbestabelecimento_coordenadas_candidatas.csv', 'Baixar candidatas')
                else:
                    st.caption('Nenhuma coordenada candidata validada para MT.')
        st.divider()
        st.markdown('#### 2. Diagnosticar a estrutura real do JSON')
        st.caption('Antes de tentar atualizar o banco novamente, esta rotina lê a resposta real da API e mostra quais campos vieram. Ela não altera dados e não cria coordenadas aproximadas.')
        diagnosticar = st.button('Diagnosticar JSON real da API UBS', type='secondary', use_container_width=True, key='geo_diagnosticar_json_ubs_ms')
        if diagnosticar:
            with st.spinner('Lendo e inspecionando o JSON da API pública de UBS. Aguarde...'):
                diag_json = diagnosticar_json_api_ubs_ms()
            st.info(diag_json.get('conclusao', 'Diagnóstico concluído.'))
            resumo = diag_json.get('resumo', {})
            j1, j2, j3, j4, j5, j6 = st.columns(6)
            j1.metric('Endpoints', _fmt_int(resumo.get('endpoints_testados')))
            j2.metric('Listas JSON', _fmt_int(resumo.get('listas_detectadas')))
            j3.metric('Registros detectados', _fmt_int(resumo.get('registros_detectados_em_listas')))
            j4.metric('Amostras com coord.', _fmt_int(resumo.get('amostras_com_campos_de_coordenada')))
            j5.metric('Coords. diretas MT', _fmt_int(resumo.get('registros_normalizados_com_coord_valida')))
            j6.metric('Coords. recuperáveis', _fmt_int(resumo.get('coordenadas_recuperaveis_mt')))
            endpoints = diag_json.get('endpoints')
            listas = diag_json.get('listas')
            campos = diag_json.get('campos')
            coordenadas = diag_json.get('coordenadas')
            recuperaveis = diag_json.get('coordenadas_recuperaveis')
            amostras = diag_json.get('amostras')
            normalizados = diag_json.get('registros_normalizados')
            with st.expander('Resumo dos endpoints testados', expanded=False):
                if isinstance(endpoints, pd.DataFrame) and (not endpoints.empty):
                    render_html_table(endpoints)
                    _download_csv(endpoints, 'diagnostico_api_ubs_endpoints.csv', 'Baixar endpoints')
                else:
                    st.caption('Nenhum endpoint retornado no diagnóstico.')
            with st.expander('Listas de registros detectadas no JSON', expanded=True):
                if isinstance(listas, pd.DataFrame) and (not listas.empty):
                    render_html_table(listas)
                    _download_csv(listas, 'diagnostico_api_ubs_listas_json.csv', 'Baixar listas detectadas')
                else:
                    st.warning('Nenhuma lista de registros foi reconhecida no JSON.')
            with st.expander('Campos de coordenada encontrados na amostra', expanded=True):
                if isinstance(coordenadas, pd.DataFrame) and (not coordenadas.empty):
                    render_html_table(coordenadas)
                    _download_csv(coordenadas, 'diagnostico_api_ubs_campos_coordenada.csv', 'Baixar campos de coordenada')
                else:
                    st.warning('A amostra não trouxe campos claros de latitude/longitude.')
            with st.expander('Coordenadas potencialmente recuperáveis', expanded=True):
                if isinstance(recuperaveis, pd.DataFrame) and (not recuperaveis.empty):
                    st.warning('Foram encontradas coordenadas potencialmente recuperáveis por ajuste de sinal, escala ou inversão. Isto ainda é diagnóstico: revise os campos e os valores antes de permitir atualização automática do banco.')
                    render_html_table(recuperaveis.head(100))
                    _download_csv(recuperaveis, 'diagnostico_api_ubs_coordenadas_recuperaveis.csv', 'Baixar coordenadas recuperáveis')
                else:
                    st.caption('Nenhuma coordenada recuperável para Mato Grosso foi identificada nesta amostra.')
            with st.expander('Campos mais frequentes no JSON', expanded=False):
                if isinstance(campos, pd.DataFrame) and (not campos.empty):
                    render_html_table(campos.head(120))
                    _download_csv(campos, 'diagnostico_api_ubs_campos.csv', 'Baixar campos')
            with st.expander('Amostra de registros úteis', expanded=False):
                if isinstance(amostras, pd.DataFrame) and (not amostras.empty):
                    render_html_table(amostras.head(50))
                    _download_csv(amostras, 'diagnostico_api_ubs_amostra.csv', 'Baixar amostra')
                else:
                    st.caption('Sem amostra tabular disponível.')
            if isinstance(normalizados, pd.DataFrame) and (not normalizados.empty):
                st.success('Foram encontrados registros normalizados com coordenadas válidas para MT. Agora a atualização pode ser executada.')
                render_html_table(normalizados.head(50))
                _download_csv(normalizados, 'ubs_api_ms_normalizadas_com_coordenada.csv', 'Baixar UBS normalizadas')
        st.divider()
        st.markdown('#### 3. Atualizar pela API pública de UBS somente se houver coordenada oficial válida')
        col_a, col_b = st.columns([1.2, 1])
        with col_a:
            executar = st.button('Coletar/filtrar API e tentar busca direcionada por CNES/IBGE', type='primary', use_container_width=True, key='geo_buscar_coord_ubs_ms')
        with col_b:
            st.caption('Fonte: API pública nacional de UBS do Ministério da Saúde. Segurança: filtro local por UF/IBGE de MT + coordenada dentro do território de Mato Grosso.')
        if executar:
            with st.spinner('Coletando API, aplicando filtro local MT e tentando busca direcionada por CNES/IBGE. Aguarde...'):
                resultado = enriquecer_ubs_com_coordenadas_oficiais_ms()
            if resultado.get('ok'):
                st.success(resultado.get('mensagem'))
            else:
                st.error(resultado.get('mensagem'))
            r1, r2, r3, r4 = st.columns(4)
            r1.metric('UBS oficiais MT lidas', _fmt_int(resultado.get('oficiais_lidas')))
            r2.metric('Atualizadas por CNES', _fmt_int(resultado.get('atualizadas_por_cnes')))
            r3.metric('Atualizadas por nome', _fmt_int(resultado.get('atualizadas_por_nome')))
            r4.metric('Novas inseridas', _fmt_int(resultado.get('inseridas_novas')))
            diag_pos = resultado.get('diagnostico_pos') or {}
            if diag_pos:
                st.markdown('#### Diagnóstico após qualificação')
                d1, d2, d3, d4 = st.columns(4)
                d1.metric('Unidades/UBS', _fmt_int(diag_pos.get('total_unidades')))
                d2.metric('Com coordenada', _fmt_int(diag_pos.get('com_coordenadas_validas')))
                d3.metric('Pendentes', _fmt_int(diag_pos.get('sem_coordenadas_validas')))
                d4.metric('Georreferenciamento', f"{_fmt_float(diag_pos.get('percentual_georreferenciado'), 1)}%")
                st.info(str(diag_pos.get('mensagem', '')))
            tentativas = resultado.get('tentativas') or []
            with st.expander('Detalhe técnico das tentativas de conexão', expanded=False):
                if tentativas:
                    for item in tentativas:
                        st.code(str(item))
                else:
                    st.caption('Sem detalhe técnico retornado.')
        st.markdown('#### Situação atual das UBS')
        qual_atual = qualificar_unidades_aps_georreferenciadas()
        diag_atual = qual_atual.get('diagnostico', {})
        a1, a2, a3, a4 = st.columns(4)
        a1.metric('Unidades/UBS na base', _fmt_int(diag_atual.get('total_unidades')))
        a2.metric('Com coordenada válida', _fmt_int(diag_atual.get('com_coordenadas_validas')))
        a3.metric('Pendentes', _fmt_int(diag_atual.get('sem_coordenadas_validas')))
        a4.metric('% georreferenciado', f"{_fmt_float(diag_atual.get('percentual_georreferenciado'), 1)}%")
        st.info(str(diag_atual.get('mensagem', '')))
        sem_geo_atual = qual_atual.get('sem_coordenadas', pd.DataFrame())
        if not sem_geo_atual.empty:
            st.markdown('#### UBS ainda sem coordenada oficial válida')
            cols = [c for c in ['cnes', 'nome_unidade', 'tipo_unidade', 'municipio', 'codigo_ibge', 'endereco', 'status_georreferencia'] if c in sem_geo_atual.columns]
            render_html_table(sem_geo_atual[cols])
            _download_csv(sem_geo_atual[cols], 'ubs_ainda_sem_coordenada_oficial.csv', 'Baixar UBS ainda pendentes')
        else:
            st.success('Todas as unidades avaliadas possuem coordenada válida na base estruturada.')
    with tab_pendencias:
        st.markdown('### Painel de pendências geográficas')
        st.caption('Esta visão transforma as limitações geográficas em uma fila técnica de saneamento da base. Ela não cria coordenadas aproximadas; apenas mostra o que precisa ser corrigido para viabilizar mapas e distâncias exatas.')
        pend_info = diagnosticar_pendencias_geograficas()
        pendencias = pend_info.get('pendencias', pd.DataFrame())
        resumo_prioridade = pend_info.get('resumo_prioridade', pd.DataFrame())
        resumo_eixo = pend_info.get('resumo_eixo', pd.DataFrame())
        ubs_pendentes = pend_info.get('ubs_pendentes', pd.DataFrame())
        ubs_resumo = pend_info.get('ubs_resumo_municipal', pd.DataFrame())
        diag_ubs_p = pend_info.get('diagnostico_ubs', {})
        total_pend = len(pendencias)
        criticas = int((pendencias.get('prioridade', pd.Series(dtype=str)) == 'Crítica').sum()) if not pendencias.empty else 0
        altas = int((pendencias.get('prioridade', pd.Series(dtype=str)) == 'Alta').sum()) if not pendencias.empty else 0
        registros_afetados = int(pd.to_numeric(pendencias.get('quantidade', pd.Series(dtype=float)), errors='coerce').fillna(0).sum()) if not pendencias.empty else 0
        p1, p2, p3, p4 = st.columns(4)
        p1.metric('Pendências mapeadas', _fmt_int(total_pend))
        p2.metric('Críticas', _fmt_int(criticas))
        p3.metric('Altas', _fmt_int(altas))
        p4.metric('Registros afetados', _fmt_int(registros_afetados))
        if pendencias.empty:
            st.success('Nenhuma pendência geográfica relevante foi identificada nas camadas avaliadas.')
        else:
            if int(diag_ubs_p.get('sem_coordenadas_validas', 0) or 0) > 0:
                st.warning('Principal gargalo atual: UBS/unidades sem latitude e longitude oficial válida. Enquanto essa camada não for qualificada, o sistema não deve calcular distância real até UBS.')
            col_a, col_b = st.columns([1, 1])
            with col_a:
                st.markdown('#### Pendências por prioridade')
                if resumo_prioridade.empty:
                    st.caption('Sem resumo por prioridade.')
                else:
                    render_html_table(resumo_prioridade)
            with col_b:
                st.markdown('#### Pendências por eixo')
                if resumo_eixo.empty:
                    st.caption('Sem resumo por eixo.')
                else:
                    render_html_table(resumo_eixo)
            st.markdown('#### Fila técnica consolidada')
            prioridade_filtro = st.multiselect('Filtrar por prioridade', ['Crítica', 'Alta', 'Média', 'Baixa'], default=['Crítica', 'Alta', 'Média'], key='geo_v6_pend_prioridade')
            eixo_filtro = st.multiselect('Filtrar por eixo', sorted(pendencias.get('eixo', pd.Series(dtype=str)).dropna().unique().tolist()), default=[], key='geo_v6_pend_eixo')
            df_p = pendencias.copy()
            if prioridade_filtro:
                df_p = df_p[df_p['prioridade'].isin(prioridade_filtro)]
            if eixo_filtro:
                df_p = df_p[df_p['eixo'].isin(eixo_filtro)]
            cols_p = [c for c in ['prioridade', 'eixo', 'pendencia', 'quantidade', 'tabela_relacionada', 'impacto_analitico', 'encaminhamento_recomendado'] if c in df_p.columns]
            render_html_table(df_p[cols_p])
            _download_csv(df_p, 'pendencias_geograficas_consolidadas.csv', 'Baixar fila consolidada de pendências')
            st.markdown('#### UBS pendentes de coordenadas oficiais')
            if ubs_pendentes.empty:
                st.success('Não há UBS/unidades pendentes de coordenadas oficiais válidas.')
            else:
                busca_ubs = st.text_input('Buscar UBS/município/endereço', key='geo_v6_busca_ubs_pendente')
                df_ubs = ubs_pendentes.copy()
                if busca_ubs:
                    mask = pd.Series(False, index=df_ubs.index)
                    for col in ['nome_unidade', 'municipio', 'endereco', 'cnes']:
                        if col in df_ubs.columns:
                            mask = mask | df_ubs[col].astype(str).str.contains(busca_ubs, case=False, na=False)
                    df_ubs = df_ubs[mask]
                cols_ubs = [c for c in ['cnes', 'nome_unidade', 'tipo_unidade', 'municipio', 'codigo_ibge', 'endereco', 'status_georreferencia'] if c in df_ubs.columns]
                render_html_table(df_ubs[cols_ubs])
                _download_csv(df_ubs[cols_ubs], 'ubs_pendentes_coordenadas_oficiais.csv', 'Baixar UBS pendentes')
            st.markdown('#### Municípios com maior volume de UBS pendentes')
            if ubs_resumo.empty:
                st.caption('Resumo municipal de UBS pendentes indisponível.')
            else:
                cols_res = [c for c in ['municipio', 'unidades', 'georreferenciadas', 'pendentes', 'percentual_georreferenciado'] if c in ubs_resumo.columns]
                render_html_table(ubs_resumo[cols_res].head(30))
                _download_csv(ubs_resumo[cols_res], 'resumo_municipal_ubs_pendencias_geo.csv', 'Baixar resumo municipal de UBS')
        with st.expander('Como usar este painel', expanded=False):
            st.markdown('\n                Este painel deve orientar a qualificação da base antes de análises espaciais mais sensíveis.\n\n                **Regra adotada no sistema:** distância real até UBS só deve ser calculada quando a unidade tiver latitude/longitude oficial válida.\n\n                A fila técnica ajuda a separar:\n                - o que impede cálculo de distância;\n                - o que prejudica filtros municipais/regionais;\n                - o que pode ser usado apenas como camada agregada;\n                - o que deve permanecer fora de conclusões oficiais até ser validado.\n                ')
    with tab_distancias:
        st.markdown('### Distâncias e acesso — Assentamentos até UBS')
        st.caption('Esta leitura mede apenas a distância real geodésica entre o centroide dos assentamentos e a UBS/unidade georreferenciada mais próxima. Se não houver UBS com latitude/longitude oficial válida, o cálculo não é executado.')
        resultado_dist = calcular_distancias_assentamentos_ubs(usar_aproximacao_municipal=False)
        diag_dist = resultado_dist.get('diagnostico', {})
        dist = resultado_dist.get('distancias', pd.DataFrame())
        resumo_mun = resultado_dist.get('resumo_municipal', pd.DataFrame())
        resumo_reg = resultado_dist.get('resumo_regional', pd.DataFrame())
        d1, d2, d3, d4 = st.columns(4)
        d1.metric('Assentamentos com coordenadas', _fmt_int(diag_dist.get('assentamentos_com_coordenadas')))
        d2.metric('UBS georreferenciadas', _fmt_int(diag_dist.get('ubs_com_coordenadas')))
        d3.metric('Referências usadas', _fmt_int(diag_dist.get('referencias_usadas')))
        d4.metric('Modo', str(diag_dist.get('modo_calculo', '-')))
        st.info(str(diag_dist.get('observacao', '')))
        if dist.empty:
            st.warning('Distância exata indisponível: não há UBS/unidades com latitude/longitude oficial válida suficiente para o cálculo. A aba Qualificação UBS mostra a fila técnica de unidades que precisam receber coordenadas oficiais antes desta análise.')
        else:
            regioes_d = sorted([r for r in dist.get('regiao_saude', pd.Series()).dropna().unique()])
            col1, col2, col3 = st.columns([1.1, 1.1, 1.8])
            regiao_d = col1.selectbox('Região', ['Todas'] + regioes_d, key='geo_v4_dist_regiao')
            classe_d = col2.selectbox('Classe de distância', ['Todas', 'Crítico', 'Distante', 'Atenção', 'Próximo'], key='geo_v4_dist_classe')
            municipio_busca = col3.text_input('Buscar município/assentamento', key='geo_v4_dist_busca')
            df_d = dist.copy()
            if regiao_d != 'Todas':
                df_d = df_d[df_d['regiao_saude'] == regiao_d]
            if classe_d != 'Todas':
                df_d = df_d[df_d['classe_distancia_aps'] == classe_d]
            if municipio_busca:
                mask = df_d['municipio'].astype(str).str.contains(municipio_busca, case=False, na=False) | df_d['assentamento'].astype(str).str.contains(municipio_busca, case=False, na=False)
                df_d = df_d[mask]
            a, b, c, d = st.columns(4)
            a.metric('Assentamentos filtrados', len(df_d))
            b.metric('Críticos', int((df_d.get('classe_distancia_aps', pd.Series()) == 'Crítico').sum()))
            c.metric('Distantes', int((df_d.get('classe_distancia_aps', pd.Series()) == 'Distante').sum()))
            d.metric('Distância média', f"{_fmt_float(pd.to_numeric(df_d.get(('distancia_hospital_km' if tipo_mapa.startswith('Hospitalar') else 'distancia_ubs_mais_proxima_km')), errors='coerce').mean(), 1)} km")
            _render_legenda_distancias()
            mapa_col, resumo_col = st.columns([2.1, 1])
            with mapa_col:
                _render_mapa_distancias_assentamentos(df_d)
            with resumo_col:
                st.markdown('#### Maiores distâncias')
                cols_top = [c for c in ['assentamento', 'municipio', ('distancia_hospital_km' if tipo_mapa.startswith('Hospitalar') else 'distancia_ubs_mais_proxima_km'), 'classe_distancia_aps'] if c in df_d.columns]
                render_html_table(df_d[cols_top].head(12))
            st.markdown('#### Tabela detalhada — assentamento x UBS/ref. mais próxima')
            cols = [c for c in ['assentamento', 'municipio', 'regiao_saude', 'ubs_mais_proxima', 'cnes_ubs_mais_proxima', 'municipio_ubs_mais_proxima', ('distancia_hospital_km' if tipo_mapa.startswith('Hospitalar') else 'distancia_ubs_mais_proxima_km'), 'classe_distancia_aps', 'ubs_mais_proxima_mesmo_municipio', 'distancia_ubs_mesmo_municipio_km', 'classe_distancia_mesmo_municipio', 'lat_assentamento', 'lon_assentamento', 'lat_ubs', 'lon_ubs', 'modo_calculo'] if c in df_d.columns]
            render_html_table(df_d[cols])
            _download_csv(df_d[cols], 'distancias_assentamentos_ubs.csv', 'Baixar distâncias assentamento x UBS')
            st.markdown('#### Resumo municipal')
            render_html_table(resumo_mun)
            _download_csv(resumo_mun, 'resumo_municipal_distancias_assentamentos_ubs.csv', 'Baixar resumo municipal')
            st.markdown('#### Resumo regional')
            render_html_table(resumo_reg)
            _download_csv(resumo_reg, 'resumo_regional_distancias_assentamentos_ubs.csv', 'Baixar resumo regional')
    with tab_acesso_rural:
        st.markdown('### Acesso rural à APS')
        st.caption('Esta leitura transforma a distância assentamento → UBS/APS em informação gerencial: ranking de maiores distâncias, municípios com maior alerta rural, regiões prioritárias e unidades APS mais demandadas como referência territorial.')
        acesso = montar_acesso_rural_aps()
        diag_a = acesso.get('diagnostico', {})
        dist_a = acesso.get('distancias', pd.DataFrame())
        rank_ass = acesso.get('ranking_assentamentos', pd.DataFrame())
        rank_mun = acesso.get('ranking_municipios', pd.DataFrame())
        rank_reg = acesso.get('ranking_regioes', pd.DataFrame())
        ubs_dem = acesso.get('ubs_mais_demandadas', pd.DataFrame())
        matriz_alertas = acesso.get('matriz_alertas', pd.DataFrame())
        mensagens = acesso.get('mensagens_chave', [])
        if dist_a.empty:
            st.warning('Acesso rural à APS indisponível: não há distâncias reais calculadas com unidades APS georreferenciadas elegíveis.')
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric('Assentamentos analisados', _fmt_int(diag_a.get('total_assentamentos_analisados')))
            c2.metric('Distância média', f"{_fmt_float(diag_a.get('distancia_media_km'), 1)} km")
            c3.metric('Críticos', _fmt_int(diag_a.get('criticos')))
            c4.metric('Distantes', _fmt_int(diag_a.get('distantes')))
            c5, c6, c7, c8 = st.columns(4)
            c5.metric('Acima de 15 km', _fmt_int(diag_a.get('acima_15km')))
            c6.metric('Acima de 30 km', _fmt_int(diag_a.get('acima_30km')))
            c7.metric('Acima de 50 km', _fmt_int(diag_a.get('acima_50km')))
            c8.metric('Maior distância', f"{_fmt_float(diag_a.get('distancia_maxima_km'), 1)} km")
            st.markdown('#### Mensagens-chave')
            for msg in mensagens:
                st.info(msg)
            regioes_ar = sorted([r for r in dist_a.get('regiao_saude', pd.Series()).dropna().unique()])
            col1, col2, col3 = st.columns([1.1, 1.1, 1.7])
            regiao_ar = col1.selectbox('Região', ['Todas'] + regioes_ar, key='geo_v16_acesso_regiao')
            classe_ar = col2.selectbox('Classe de distância', ['Todas', 'Crítico', 'Distante', 'Atenção', 'Próximo'], key='geo_v16_acesso_classe')
            busca_ar = col3.text_input('Buscar município/assentamento/UBS', key='geo_v16_acesso_busca')
            df_ar = dist_a.copy()
            if regiao_ar != 'Todas' and 'regiao_saude' in df_ar.columns:
                df_ar = df_ar[df_ar['regiao_saude'] == regiao_ar]
            if classe_ar != 'Todas' and 'classe_distancia_aps' in df_ar.columns:
                df_ar = df_ar[df_ar['classe_distancia_aps'] == classe_ar]
            if busca_ar:
                termo = busca_ar
                mask = pd.Series(False, index=df_ar.index)
                for c in ['municipio', 'assentamento', 'ubs_mais_proxima', 'municipio_ubs_mais_proxima']:
                    if c in df_ar.columns:
                        mask = mask | df_ar[c].astype(str).str.contains(termo, case=False, na=False)
                df_ar = df_ar[mask]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric('Assentamentos filtrados', _fmt_int(len(df_ar)))
            m2.metric('Críticos filtrados', _fmt_int((df_ar.get('classe_distancia_aps', pd.Series()) == 'Crítico').sum()))
            m3.metric('Distantes filtrados', _fmt_int((df_ar.get('classe_distancia_aps', pd.Series()) == 'Distante').sum()))
            m4.metric('Distância média filtrada', f"{_fmt_float(pd.to_numeric(df_ar.get(('distancia_hospital_km' if tipo_mapa.startswith('Hospitalar') else 'distancia_ubs_mais_proxima_km')), errors='coerce').mean(), 1)} km")
            st.markdown('#### Mapa de acesso rural')
            _render_mapa_distancias_assentamentos(df_ar)
            st.markdown('#### Ranking dos assentamentos mais distantes')
            cols_rank_ass = [c for c in ['assentamento', 'municipio', 'regiao_saude', 'ubs_mais_proxima', 'cnes_ubs_mais_proxima', 'municipio_ubs_mais_proxima', ('distancia_hospital_km' if tipo_mapa.startswith('Hospitalar') else 'distancia_ubs_mais_proxima_km'), 'classe_distancia_aps', 'ubs_mais_proxima_mesmo_municipio', 'distancia_ubs_mesmo_municipio_km'] if c in df_ar.columns]
            render_html_table(_sort_seguro(df_ar, _coluna_distancia_da_camada(tipo_mapa), ascending=False)[cols_rank_ass].head(40))
            _download_csv(df_ar[cols_rank_ass], 'acesso_rural_assentamentos_ubs.csv', 'Baixar ranking de acesso rural')
            left, right = st.columns(2)
            with left:
                st.markdown('#### Municípios com maior alerta rural')
                if not rank_mun.empty:
                    cols_mun = [c for c in ['municipio', 'regiao_saude', 'assentamentos', 'criticos', 'distantes', 'criticos_distantes', 'percentual_critico_distante', 'distancia_media_km', 'distancia_maxima_km'] if c in rank_mun.columns]
                    render_html_table(rank_mun[cols_mun].head(25))
                    _download_csv(rank_mun[cols_mun], 'acesso_rural_resumo_municipal.csv', 'Baixar resumo municipal')
                else:
                    st.info('Resumo municipal indisponível.')
            with right:
                st.markdown('#### UBS/APS mais demandadas como referência')
                if not ubs_dem.empty:
                    cols_ubs = [c for c in ['cnes_ubs_mais_proxima', 'ubs_mais_proxima', 'municipio_ubs_mais_proxima', 'assentamentos_referenciados', 'criticos', 'distantes', 'distancia_media_km', 'distancia_maxima_km'] if c in ubs_dem.columns]
                    render_html_table(ubs_dem[cols_ubs].head(25))
                    _download_csv(ubs_dem[cols_ubs], 'acesso_rural_ubs_mais_demandadas.csv', 'Baixar UBS mais demandadas')
                else:
                    st.info('Não foi possível consolidar as UBS mais demandadas.')
            st.markdown('#### Resumo por Região de Saúde')
            if not rank_reg.empty:
                render_html_table(rank_reg)
                _download_csv(rank_reg, 'acesso_rural_resumo_regional.csv', 'Baixar resumo regional')
            else:
                st.info('Resumo regional indisponível.')
            st.markdown('#### Matriz de alerta e encaminhamento técnico')
            if not matriz_alertas.empty:
                cols_alerta = [c for c in ['municipio', 'regiao_saude', 'nivel_alerta_acesso_rural', 'assentamentos', 'criticos', 'distantes', 'distancia_media_km', 'distancia_maxima_km', 'encaminhamento_sugerido'] if c in matriz_alertas.columns]
                render_html_table(matriz_alertas[cols_alerta])
                _download_csv(matriz_alertas[cols_alerta], 'acesso_rural_matriz_alertas.csv', 'Baixar matriz de alertas')
            else:
                st.info('Nenhum alerta municipal específico foi gerado.')
            st.markdown('#### Síntese técnica por município')
            municipios_rurais = sorted([m for m in rank_mun.get('municipio', pd.Series(dtype=str)).dropna().unique()]) if not rank_mun.empty else []
            if municipios_rurais:
                mun_sel = st.selectbox('Município para síntese', municipios_rurais, key='geo_v16_mun_sintese')
                linha = rank_mun[rank_mun['municipio'] == mun_sel].head(1)
                if not linha.empty:
                    r = linha.iloc[0]
                    st.text_area('Texto-base copiável', value=f"O município de {mun_sel}, na Região de Saúde {r.get('regiao_saude', 'não informada')}, possui {int(r.get('assentamentos', 0) or 0)} assentamento(s) analisado(s) na camada de acesso rural à APS. A distância média até a unidade APS georreferenciada mais próxima é de {_fmt_float(r.get('distancia_media_km'), 1)} km, com maior distância de {_fmt_float(r.get('distancia_maxima_km'), 1)} km. Foram identificados {int(r.get('criticos', 0) or 0)} assentamento(s) em faixa crítica e {int(r.get('distantes', 0) or 0)} em faixa distante. Recomenda-se validar o fluxo real de referência APS, avaliar a existência de barreiras de deslocamento e discutir estratégia territorial específica para a população rural.", height=170)
            with st.expander('Observação metodológica', expanded=False):
                st.markdown('\n                    A distância é geodésica em linha reta entre o centroide do assentamento e a unidade APS/UBS elegível mais próxima.\n                    A camada de unidades APS usa CNES único vinculado a equipes APS/INE e coordenada válida em Mato Grosso.\n                    Esta leitura não substitui rota viária, tempo real de deslocamento, condições de estrada, sazonalidade ou validação local do fluxo assistencial.\n                    ')
    with tab_bairros:
        st.markdown('### Bairros, localidades e setores censitários até UBS/APS')
        st.caption('Camada fina recuperada do sistema anterior para leitura intramunicipal. A base usa setores censitários/territórios do IBGE 2022 como unidade territorial; não é cadastro oficial de bairros, mas permite enxergar vazios urbanos, periurbanos e rurais com mais detalhe.')
        diag_b = diagnosticar_base_bairros_localidades()
        info_b = diag_b.get('diagnostico', {})
        territorios_ref = diag_b.get('territorios', pd.DataFrame())
        resumo_base = diag_b.get('resumo_municipal', pd.DataFrame())
        c1, c2, c3, c4 = st.columns(4)
        c1.metric('Territórios/setores', _fmt_int(info_b.get('registros', 0)))
        c2.metric('Municípios cobertos', _fmt_int(info_b.get('municipios_cobertos', 0)))
        c3.metric('Com coordenada válida', _fmt_int(info_b.get('com_coordenadas_validas', 0)))
        c4.metric('População ref.', _fmt_int(info_b.get('populacao_total_referencia', 0)))
        if territorios_ref.empty:
            st.warning('A base de bairros/localidades/setores ainda não está disponível em data/reference.')
        else:
            st.info(f"Nomes de bairro/localidade já identificados: **{_fmt_int(info_b.get('territorios_com_nome_validado', 0))}**. Pendentes de validação: **{_fmt_int(info_b.get('territorios_sem_nome_validado', 0))}**. Quando não houver nome validado, o sistema mantém o setor censitário como fallback para não inventar bairro.")
            with st.expander('Nomeação oficial de bairros/localidades pelo IBGE', expanded=False):
                st.markdown('\n                    A plataforma agora tenta usar a **Malha de Setores Censitários 2022 do IBGE**, que possui atributos como\n                    `CD_BAIRRO`, `NM_BAIRRO`, `NM_NU`, `NM_FCU`, `NM_AGLOM`, `NM_SUBDIST` e `NM_DIST`.\n\n                    Essa é a fonte correta para âmbito estadual, porque cruza o próprio `setor_censitario` da base com o nome territorial oficial disponível no IBGE.\n                    Não usa o bairro da UBS mais próxima e não depende apenas de Cuiabá.\n\n                    O arquivo manual `data/reference/depara_setor_bairro_localidade.csv` continua existindo apenas para correções pontuais validadas pela SES/ERS/município.\n                    ')
                if st.button('Atualizar nomes oficiais pela malha IBGE 2022', use_container_width=True, key='geo_nomear_ibge_btn'):
                    with st.spinner('Baixando/lendo a malha oficial de setores do IBGE 2022 e atualizando o cache local...'):
                        ret = atualizar_nomes_bairros_ibge_2022_mt()
                    if ret.get('ok'):
                        st.success(f"Cache oficial atualizado. Registros nomeados: {_fmt_int(ret.get('registros', 0))} | Municípios cobertos: {_fmt_int(ret.get('municipios', 0))}. Arquivo: {ret.get('cache')}")
                        st.info('Recarregue a página para que os novos nomes apareçam nos mapas e tabelas.')
                    else:
                        st.error(ret.get('mensagem', 'Não foi possível atualizar os nomes pelo IBGE.'))
                        st.code('geopandas\npyogrio\nshapely', language='text')
            with st.expander('Diagnóstico da base territorial fina', expanded=False):
                st.markdown('Esta camada foi recuperada da versão anterior do sistema. Ela deve ser apresentada tecnicamente como setores censitários/localidades, não como cadastro oficial universal de bairros.')
                if not resumo_base.empty:
                    render_html_table(resumo_base.head(40))
                    _download_csv(resumo_base, 'diagnostico_bairros_localidades_municipios.csv', 'Baixar diagnóstico municipal da base')
            resultado_b = calcular_distancias_bairros_localidades_aps()
            d_b = resultado_b.get('diagnostico', {})
            dist_b = resultado_b.get('distancias', pd.DataFrame())
            res_mun_b = resultado_b.get('resumo_municipal', pd.DataFrame())
            res_reg_b = resultado_b.get('resumo_regional', pd.DataFrame())
            ubs_dem_b = resultado_b.get('ubs_mais_demandadas', pd.DataFrame())
            a, b, c, d = st.columns(4)
            a.metric('Territórios analisados', _fmt_int(d_b.get('territorios_analisados', 0)))
            b.metric('UBS/APS referências', _fmt_int(d_b.get('referencias_usadas', 0)))
            c.metric('Distância média', f"{_fmt_float(d_b.get('distancia_media_km'), 1)} km")
            d.metric('Críticos + distantes', _fmt_int(int(d_b.get('criticos', 0) or 0) + int(d_b.get('distantes', 0) or 0)))
            st.info('A distância é em linha reta entre o centroide do setor/localidade e a UBS/APS elegível mais próxima. Não usa centroide municipal, nem geocodificação por endereço.')
            _render_legenda_distancias_bairros()
            if dist_b.empty:
                st.warning('Não foi possível calcular as distâncias. Verifique se há territórios com coordenadas e UBS/APS elegíveis georreferenciadas.')
            else:
                regioes = sorted([r for r in dist_b.get('regiao_saude', pd.Series()).dropna().unique()])
                col1, col2, col3 = st.columns([1.1, 1.1, 1.8])
                reg_sel = col1.selectbox('Região de Saúde', ['Todas'] + regioes, key='geo_v17_bairros_regiao')
                classe_sel = col2.selectbox('Classe de distância', ['Todas', 'Crítico', 'Distante', 'Atenção', 'Próximo'], key='geo_v17_bairros_classe')
                busca_mun = col3.text_input('Buscar município', placeholder='Ex.: Cuiabá, Rondonópolis, Sinop', key='geo_v17_bairros_busca')
                df_b = dist_b.copy()
                if reg_sel != 'Todas' and 'regiao_saude' in df_b.columns:
                    df_b = df_b[df_b['regiao_saude'] == reg_sel]
                if classe_sel != 'Todas':
                    df_b = df_b[df_b['classe_distancia_aps'] == classe_sel]
                if busca_mun:
                    df_b = df_b[df_b['municipio'].astype(str).str.contains(busca_mun, case=False, na=False)]
                st.markdown('#### Mapa de acesso fino até UBS/APS')
                _render_mapa_distancias_bairros_localidades(df_b)
                st.markdown('#### Territórios mais distantes da UBS/APS')
                cols_dist = [x for x in ['municipio', 'regiao_saude', 'territorio_exibicao', 'bairro_ou_localidade_original', 'tipo_territorio', 'populacao', 'ubs_mais_proxima', 'cnes_ubs_mais_proxima', 'municipio_ubs_mais_proxima', ('distancia_hospital_km' if tipo_mapa.startswith('Hospitalar') else 'distancia_ubs_mais_proxima_km'), 'classe_distancia_aps', 'qtd_equipes_aps_ubs'] if x in df_b.columns]
                render_html_table(_sort_seguro(df_b[cols_dist], _coluna_distancia_da_camada(tipo_mapa), ascending=False).head(300))
                _download_csv(df_b[cols_dist], 'distancias_bairros_localidades_ubs_aps.csv', 'Baixar distâncias bairro/localidade x UBS/APS')
                left, right = st.columns(2)
                with left:
                    st.markdown('#### Municípios com maior alerta intramunicipal')
                    if not res_mun_b.empty:
                        cols_m = [x for x in ['municipio', 'regiao_saude', 'territorios', 'populacao_referencia', 'criticos', 'distantes', 'percentual_critico_distante', 'distancia_media_km', 'distancia_maxima_km', 'populacao_critica', 'populacao_distante'] if x in res_mun_b.columns]
                        render_html_table(res_mun_b[cols_m].head(40))
                        _download_csv(res_mun_b[cols_m], 'resumo_municipal_bairros_localidades_aps.csv', 'Baixar resumo municipal')
                with right:
                    st.markdown('#### UBS/APS mais demandadas')
                    if not ubs_dem_b.empty:
                        cols_u = [x for x in ['cnes_ubs_mais_proxima', 'ubs_mais_proxima', 'municipio_ubs_mais_proxima', 'territorios_referenciados', 'populacao_referenciada', 'distancia_media_km', 'distancia_maxima_km', 'criticos', 'distantes'] if x in ubs_dem_b.columns]
                        render_html_table(ubs_dem_b[cols_u].head(40))
                        _download_csv(ubs_dem_b[cols_u], 'ubs_aps_referencia_bairros_localidades.csv', 'Baixar UBS/APS de referência')
                st.markdown('#### Resumo por Região de Saúde')
                if not res_reg_b.empty:
                    render_html_table(res_reg_b)
                    _download_csv(res_reg_b, 'resumo_regional_bairros_localidades_aps.csv', 'Baixar resumo regional')
                st.markdown('#### Síntese técnica por município')
                municipios_b = sorted([m for m in res_mun_b.get('municipio', pd.Series(dtype=str)).dropna().unique()]) if not res_mun_b.empty else []
                if municipios_b:
                    mun_b = st.selectbox('Município para síntese', municipios_b, key='geo_v17_bairros_mun_sintese')
                    lin = res_mun_b[res_mun_b['municipio'] == mun_b].head(1)
                    if not lin.empty:
                        r = lin.iloc[0]
                        st.text_area('Texto-base copiável', value=f"O município de {mun_b}, na Região de Saúde {r.get('regiao_saude', 'não informada')}, possui {int(r.get('territorios', 0) or 0)} território(s)/setor(es) analisado(s) na camada intramunicipal. A distância média até a UBS/APS georreferenciada mais próxima é de {_fmt_float(r.get('distancia_media_km'), 1)} km, com maior distância de {_fmt_float(r.get('distancia_maxima_km'), 1)} km. Foram identificados {int(r.get('criticos', 0) or 0)} território(s) em faixa crítica e {int(r.get('distantes', 0) or 0)} em faixa distante. Recomenda-se validar com o município a correspondência entre setores/localidades, bairros reconhecidos localmente e referência real da equipe APS.", height=170, key='geo_v17_bairros_texto_sintese')
                with st.expander('Observação metodológica importante', expanded=False):
                    st.markdown('\n                        Esta camada usa setores censitários/localidades recuperados da versão anterior do sistema. Ela é adequada para evidenciar vazios intramunicipais, mas não substitui uma base oficial de bairros de cada prefeitura. Para apresentação institucional, recomenda-se usar a expressão **bairros/localidades/setores censitários** ou **territórios intramunicipais**.\n                        ')
    with tab_vazios_intra:
        st.markdown('### Painel de Vazios Intramunicipais')
        st.caption('Leitura executiva dos bairros, localidades e setores censitários mais distantes da UBS/APS elegível mais próxima. Esta aba consolida a análise fina para apoiar priorização municipal e apresentação de vazios assistenciais.')
        painel_intra = montar_painel_vazios_intramunicipais()
        diag_i = painel_intra.get('diagnostico', {})
        dist_i = painel_intra.get('distancias', pd.DataFrame())
        matriz_i = painel_intra.get('matriz_executiva', pd.DataFrame())
        crit_i = painel_intra.get('territorios_criticos', pd.DataFrame())
        reg_i = painel_intra.get('ranking_regional', pd.DataFrame())
        ubs_i = painel_intra.get('ubs_referencia', pd.DataFrame())
        pop_i = painel_intra.get('populacao_exposta', pd.DataFrame())
        if dist_i.empty:
            st.warning('Ainda não foi possível montar o painel intramunicipal. Verifique se a base de bairros/localidades/setores e as UBS/APS elegíveis georreferenciadas estão disponíveis.')
        else:
            a, b, c, d = st.columns(4)
            a.metric('Territórios analisados', _fmt_int(diag_i.get('territorios_analisados', 0)))
            b.metric('Municípios cobertos', _fmt_int(diag_i.get('municipios_cobertos', 0)))
            c.metric('Críticos + distantes', _fmt_int(int(diag_i.get('territorios_criticos', 0) or 0) + int(diag_i.get('territorios_distantes', 0) or 0)))
            d.metric('População em alerta', _fmt_int(diag_i.get('populacao_critica_distante', 0)))
            e, f, g, h = st.columns(4)
            e.metric('Distância média', f"{_fmt_float(diag_i.get('distancia_media_km'), 1)} km")
            f.metric('Maior distância', f"{_fmt_float(diag_i.get('distancia_maxima_km'), 1)} km")
            g.metric('Municípios alerta muito alto', _fmt_int(diag_i.get('municipios_muito_alto', 0)))
            h.metric('Municípios alerta alto', _fmt_int(diag_i.get('municipios_alto', 0)))
            st.markdown('#### Mensagens-chave')
            mensagens = []
            if int(diag_i.get('territorios_criticos', 0) or 0) > 0:
                mensagens.append(f"Foram identificados **{_fmt_int(diag_i.get('territorios_criticos', 0))} territórios em faixa crítica**, acima de 5 km da UBS/APS elegível mais próxima.")
            if int(diag_i.get('populacao_critica_distante', 0) or 0) > 0:
                mensagens.append(f"A população de referência em territórios críticos ou distantes soma aproximadamente **{_fmt_int(diag_i.get('populacao_critica_distante', 0))} pessoas** na base analisada.")
            if int(diag_i.get('municipios_muito_alto', 0) or 0) + int(diag_i.get('municipios_alto', 0) or 0) > 0:
                mensagens.append(f"Há **{_fmt_int(int(diag_i.get('municipios_muito_alto', 0) or 0) + int(diag_i.get('municipios_alto', 0) or 0))} municípios** em alerta alto ou muito alto para vazios intramunicipais.")
            mensagens.append('O cálculo usa CNES único vinculado a equipes APS/INE e coordenadas reais válidas, sem centroide municipal e sem geocodificação por endereço.')
            for msg in mensagens:
                st.markdown(f'- {msg}')
            st.markdown('#### Mapa executivo dos vazios intramunicipais')
            regioes_i = sorted([r for r in dist_i.get('regiao_saude', pd.Series()).dropna().unique()])
            col1, col2, col3 = st.columns([1.1, 1.1, 1.8])
            reg_i_sel = col1.selectbox('Região de Saúde', ['Todas'] + regioes_i, key='geo_v18_regiao')
            classe_i_sel = col2.selectbox('Classe', ['Todas', 'Crítico', 'Distante', 'Atenção', 'Próximo'], key='geo_v18_classe')
            mun_i_busca = col3.text_input('Buscar município', placeholder='Ex.: Cuiabá, Sinop, Cáceres', key='geo_v18_busca_mun')
            df_map_i = dist_i.copy()
            if reg_i_sel != 'Todas' and 'regiao_saude' in df_map_i.columns:
                df_map_i = df_map_i[df_map_i['regiao_saude'] == reg_i_sel]
            if classe_i_sel != 'Todas' and 'classe_distancia_aps' in df_map_i.columns:
                df_map_i = df_map_i[df_map_i['classe_distancia_aps'] == classe_i_sel]
            if mun_i_busca and 'municipio' in df_map_i.columns:
                df_map_i = df_map_i[df_map_i['municipio'].astype(str).str.contains(mun_i_busca, case=False, na=False)]
            _render_mapa_distancias_bairros_localidades(df_map_i)
            st.markdown('#### Ranking executivo municipal')
            if not matriz_i.empty:
                cols_matriz = [x for x in ['municipio', 'regiao_saude', 'alerta_intramunicipal', 'territorios', 'populacao_referencia', 'criticos', 'distantes', 'percentual_critico_distante', 'distancia_media_km', 'distancia_maxima_km', 'populacao_critica', 'populacao_distante', 'populacao_critica_distante', 'encaminhamento_sugerido'] if x in matriz_i.columns]
                render_html_table(matriz_i[cols_matriz].head(80))
                _download_csv(matriz_i[cols_matriz], 'painel_vazios_intramunicipais_municipios.csv', 'Baixar ranking municipal intramunicipal')
            left, right = st.columns(2)
            with left:
                st.markdown('#### Territórios críticos mais distantes')
                if not crit_i.empty:
                    cols_c = [x for x in ['municipio', 'regiao_saude', 'territorio_exibicao', 'bairro_ou_localidade_original', 'tipo_territorio', 'populacao', 'ubs_mais_proxima', 'cnes_ubs_mais_proxima', ('distancia_hospital_km' if tipo_mapa.startswith('Hospitalar') else 'distancia_ubs_mais_proxima_km'), 'classe_distancia_aps'] if x in crit_i.columns]
                    render_html_table(crit_i[cols_c].head(80))
                    _download_csv(crit_i[cols_c], 'territorios_intramunicipais_criticos.csv', 'Baixar territórios críticos')
                else:
                    st.success('Não há territórios na faixa crítica com os critérios atuais.')
            with right:
                st.markdown('#### População de referência por faixa')
                if not pop_i.empty:
                    render_html_table(pop_i)
                st.markdown('#### UBS/APS mais demandadas')
                if not ubs_i.empty:
                    cols_u = [x for x in ['cnes_ubs_mais_proxima', 'ubs_mais_proxima', 'municipio_ubs_mais_proxima', 'territorios_referenciados', 'populacao_referenciada', 'distancia_media_km', 'distancia_maxima_km', 'criticos', 'distantes'] if x in ubs_i.columns]
                    render_html_table(ubs_i[cols_u].head(30))
                    _download_csv(ubs_i[cols_u], 'ubs_aps_mais_demandadas_vazios_intramunicipais.csv', 'Baixar UBS/APS mais demandadas')
            st.markdown('#### Resumo por Região de Saúde')
            if not reg_i.empty:
                render_html_table(reg_i)
                _download_csv(reg_i, 'vazios_intramunicipais_regioes_saude.csv', 'Baixar resumo regional intramunicipal')
            st.markdown('#### Texto executivo copiável')
            municipios_texto = sorted([m for m in matriz_i.get('municipio', pd.Series(dtype=str)).dropna().unique()]) if not matriz_i.empty else []
            if municipios_texto:
                mun_txt = st.selectbox('Município', municipios_texto, key='geo_v18_municipio_texto')
                linha_txt = matriz_i[matriz_i['municipio'] == mun_txt].head(1)
                if not linha_txt.empty:
                    r = linha_txt.iloc[0]
                    st.text_area('Síntese para relatório/apresentação', value=f"O município de {mun_txt}, na Região de Saúde {r.get('regiao_saude', 'não informada')}, apresenta alerta intramunicipal classificado como {r.get('alerta_intramunicipal', 'não classificado')}. Foram analisados {int(r.get('territorios', 0) or 0)} territórios/localidades/setores, com distância média de {_fmt_float(r.get('distancia_media_km'), 1)} km até a UBS/APS elegível mais próxima e distância máxima de {_fmt_float(r.get('distancia_maxima_km'), 1)} km. A análise identificou {int(r.get('criticos', 0) or 0)} território(s) em faixa crítica e {int(r.get('distantes', 0) or 0)} em faixa distante. Encaminhamento sugerido: {r.get('encaminhamento_sugerido', 'validar tecnicamente o território com a equipe municipal e regional.')}", height=190, key='geo_v18_texto_executivo')
            with st.expander('Observação metodológica', expanded=False):
                st.markdown('\n                    A leitura intramunicipal utiliza a base de bairros/localidades/setores censitários recuperada da versão anterior do sistema e unidades APS elegíveis vinculadas a equipes APS/INE. A distância calculada é geodésica em linha reta; não representa rota viária, tempo de deslocamento, barreiras sazonais ou fluxo real de adscrição. Para apresentação institucional, recomenda-se tratar a camada como **territórios intramunicipais** ou **bairros/localidades/setores censitários**.\n                    ')
    with tab_determinantes:
        st.markdown('### Determinantes sociais cruzados com vazios APS')
        st.caption('Esta aba cruza a distância intramunicipal até UBS/APS com variáveis municipais de escolaridade, renda, saneamento, rede escolar INEP e territórios de equidade. A leitura social é municipal; os pontos continuam sendo bairros/localidades/setores com coordenadas reais.')
        col_at1, col_at2 = st.columns([1, 1])
        with col_at1:
            if st.button('Atualizar base municipal consolidada', key='geo_det_atualizar_base', use_container_width=True):
                with st.spinner('Atualizando base consolidada com IBGE, INEP, CNES e demais indicadores já carregados...'):
                    ret = atualizar_base_municipal()
                st.success(f"Base atualizada. Registros: {ret.get('registros', '-')}")
        with col_at2:
            st.info('Antes desta aba, carregue/atualize as fontes na área de Base de Dados: IBGE determinantes sociais e INEP/Censo Escolar.')
        painel_det = montar_painel_determinantes_sociais_aps()
        painel_int = montar_painel_vazios_determinantes_sociais_aps()
        diag_det = painel_det.get('diagnostico', {})
        municipios_det = painel_det.get('municipios', pd.DataFrame())
        resumo_reg_det = painel_det.get('resumo_regional', pd.DataFrame())
        cobertura_det = painel_det.get('cobertura', pd.DataFrame())
        matriz_int = painel_int.get('matriz_integrada', pd.DataFrame())
        territ_int = painel_int.get('territorios_prioritarios', pd.DataFrame())
        dist_int = painel_int.get('distancias_enriquecidas', pd.DataFrame())
        if not diag_det.get('ok', False) or municipios_det.empty:
            st.warning('Ainda não há base suficiente para montar os determinantes sociais. Atualize a base municipal consolidada e confira as cargas IBGE/INEP.')
        else:
            a, b, c, d = st.columns(4)
            a.metric('Municípios com leitura social', _fmt_int(diag_det.get('municipios_com_indice', 0)))
            b.metric('Prioridade social muito alta', _fmt_int(diag_det.get('prioridade_muito_alta', 0)))
            c.metric('Prioridade social alta', _fmt_int(diag_det.get('prioridade_alta', 0)))
            if 'indice_determinantes_sociais_aps' in municipios_det.columns:
                d.metric('Índice médio social APS', _fmt_float(pd.to_numeric(municipios_det['indice_determinantes_sociais_aps'], errors='coerce').mean(), 1))
            st.markdown('#### Mensagens-chave')
            for msg in painel_det.get('mensagens_chave', []):
                st.markdown(f'- {msg}')
            st.markdown('#### Mapa: distância até UBS/APS + contexto social municipal')
            if dist_int.empty:
                st.info('A camada intramunicipal ainda não está disponível para cruzamento. Verifique a aba Bairros/localidades e as UBS georreferenciadas.')
            else:
                regioes_det = sorted([r for r in dist_int.get('regiao_saude', pd.Series()).dropna().unique()])
                c1, c2, c3, c4 = st.columns([1.1, 1.1, 1.2, 1.8])
                reg_det = c1.selectbox('Região', ['Todas'] + regioes_det, key='geo_det_regiao')
                classe_dist = c2.selectbox('Distância', ['Todas', 'Crítico', 'Distante', 'Atenção', 'Próximo'], key='geo_det_classe_dist')
                classe_soc = c3.selectbox('Classe social', ['Todas', 'Muito alta', 'Alta', 'Média', 'Bom/regular'], key='geo_det_classe_soc')
                busca_det = c4.text_input('Buscar município', placeholder='Ex.: Cuiabá, Sinop, Cáceres', key='geo_det_busca')
                df_map_det = dist_int.copy()
                if reg_det != 'Todas' and 'regiao_saude' in df_map_det.columns:
                    df_map_det = df_map_det[df_map_det['regiao_saude'] == reg_det]
                if classe_dist != 'Todas' and 'classe_distancia_aps' in df_map_det.columns:
                    df_map_det = df_map_det[df_map_det['classe_distancia_aps'] == classe_dist]
                if classe_soc != 'Todas' and 'classe_determinantes_sociais_aps' in df_map_det.columns:
                    df_map_det = df_map_det[df_map_det['classe_determinantes_sociais_aps'] == classe_soc]
                if busca_det and 'municipio' in df_map_det.columns:
                    df_map_det = df_map_det[df_map_det['municipio'].astype(str).str.contains(busca_det, case=False, na=False)]
                _render_mapa_distancias_bairros_localidades(df_map_det)
            st.markdown('#### Ranking integrado municipal')
            if not matriz_int.empty:
                cols_rank = [x for x in ['municipio', 'regiao_saude', 'prioridade_integrada_municipal', 'classe_prioridade_integrada_municipal', 'alerta_intramunicipal', 'territorios', 'criticos', 'distantes', 'populacao_critica_distante', 'indice_determinantes_sociais_aps', 'classe_determinantes_sociais_aps', 'taxa_analfabetismo_estimado_pct', 'baixa_instrucao_pct', 'renda_indicador', 'escolas_total', 'escolas_rurais', 'escolas_indigenas', 'escolas_quilombolas', 'populacao_indigena', 'populacao_quilombola', 'qtd_terras_indigenas_intersecoes', 'qtd_assentamentos', 'encaminhamento_sugerido'] if x in matriz_int.columns]
                render_html_table(matriz_int[cols_rank].head(80))
                _download_csv(matriz_int[cols_rank], 'ranking_integrado_vazios_determinantes_sociais_aps.csv', 'Baixar ranking integrado municipal')
            left, right = st.columns(2)
            with left:
                st.markdown('#### Territórios prioritários')
                if not territ_int.empty:
                    cols_territ = [x for x in ['municipio', 'regiao_saude', 'territorio_exibicao', 'bairro_ou_localidade', 'tipo_territorio', 'populacao', 'ubs_mais_proxima', ('distancia_hospital_km' if tipo_mapa.startswith('Hospitalar') else 'distancia_ubs_mais_proxima_km'), 'classe_distancia_aps', 'prioridade_integrada_territorio', 'classe_prioridade_integrada', 'indice_determinantes_sociais_aps', 'classe_determinantes_sociais_aps', 'taxa_analfabetismo_estimado_pct', 'renda_indicador'] if x in territ_int.columns]
                    render_html_table(territ_int[cols_territ].head(100))
                    _download_csv(territ_int[cols_territ], 'territorios_prioritarios_distancia_social_aps.csv', 'Baixar territórios prioritários')
                else:
                    st.info('Sem territórios priorizados para exibir.')
            with right:
                st.markdown('#### Cobertura das fontes')
                if not cobertura_det.empty:
                    render_html_table(cobertura_det)
                    _download_csv(cobertura_det, 'cobertura_fontes_determinantes_sociais.csv', 'Baixar cobertura das fontes')
            st.markdown('#### Ranking social municipal')
            cols_mun_det = [x for x in ['municipio', 'regiao_saude', 'populacao', 'indice_determinantes_sociais_aps', 'classe_determinantes_sociais_aps', 'taxa_alfabetizacao_base', 'taxa_analfabetismo_estimado_pct', 'baixa_instrucao_pct', 'renda_indicador', 'saneamento_indicador', 'escolas_total', 'escolas_rurais', 'percentual_escolas_rurais', 'escolas_indigenas', 'escolas_quilombolas', 'matriculas_total', 'matriculas_educacao_especial', 'populacao_indigena', 'populacao_quilombola'] if x in municipios_det.columns]
            render_html_table(municipios_det[cols_mun_det].head(142))
            _download_csv(municipios_det[cols_mun_det], 'determinantes_sociais_municipios_aps.csv', 'Baixar determinantes sociais por município')
            st.markdown('#### Resumo regional')
            if not resumo_reg_det.empty:
                render_html_table(resumo_reg_det)
                _download_csv(resumo_reg_det, 'determinantes_sociais_regioes_saude.csv', 'Baixar resumo regional')
            st.markdown('#### Texto executivo copiável')
            municipios_txt_det = sorted([m for m in matriz_int.get('municipio', pd.Series(dtype=str)).dropna().unique()]) if not matriz_int.empty else []
            if municipios_txt_det:
                mun_txt_det = st.selectbox('Município', municipios_txt_det, key='geo_det_municipio_texto')
                linha = matriz_int[matriz_int['municipio'] == mun_txt_det].head(1)
                if not linha.empty:
                    r = linha.iloc[0]
                    st.text_area('Síntese integrada para relatório/apresentação', value=f"O município de {mun_txt_det}, na Região de Saúde {r.get('regiao_saude', 'não informada')}, apresenta prioridade integrada {r.get('classe_prioridade_integrada_municipal', 'não classificada')} ao cruzar vazios intramunicipais e determinantes sociais. Foram identificados {int(r.get('criticos', 0) or 0)} território(s) críticos e {int(r.get('distantes', 0) or 0)} distante(s) da UBS/APS elegível mais próxima, com população de referência em alerta de aproximadamente {_fmt_int(r.get('populacao_critica_distante', 0))} pessoas. O índice social APS foi {_fmt_float(r.get('indice_determinantes_sociais_aps'), 1)}, classificado como {r.get('classe_determinantes_sociais_aps', 'não classificado')}. A análise recomenda validar localmente os territórios priorizados, a referência das equipes APS, barreiras reais de deslocamento e estratégias intersetoriais com educação e assistência social.", height=210, key='geo_det_texto_executivo')
            with st.expander('Observação metodológica', expanded=False):
                st.markdown('\n                    Esta aba não substitui estudo epidemiológico completo. Ela organiza uma régua de priorização para gestão: distância até UBS/APS, população de referência e determinantes sociais municipais. As variáveis de escolaridade, renda, saneamento e educação dependem das cargas disponíveis no banco. Para uso oficial, recomenda-se validar fonte, competência, metodologia e eventuais ausências antes da publicação externa.\n                    ')
    with tab_diag:
        st.markdown('### Diagnóstico técnico das camadas geográficas')
        st.caption('Esta visão mostra quais bases já podem ser usadas em mapa, quais estão apenas municipalizadas e quais precisam de tratamento adicional.')
        if resumo.empty:
            st.warning('Não foi possível diagnosticar as camadas geográficas.')
        else:
            total_camadas = len(resumo)
            disponiveis = int((resumo['existe'] == 'Sim').sum())
            boas = int(resumo['qualidade'].isin(['Boa', 'Municipalizada']).sum())
            mapa_ok = int(resumo['pronto_para_mapa'].isin(['Sim', 'Com agregação municipal']).sum())
            a, b, c, d = st.columns(4)
            a.metric('Camadas previstas', total_camadas)
            b.metric('Camadas disponíveis', disponiveis)
            c.metric('Boas/municipalizadas', boas)
            d.metric('Usáveis no mapa', mapa_ok)
            ordem_cols = ['camada', 'tipo', 'existe', 'registros', 'com_coordenadas', '% coordenadas', 'com_geometria', '% geometria', 'vinculados_municipio', '% vínculo municipal', 'qualidade', 'pronto_para_mapa', 'uso_recomendado']
            render_html_table(resumo[ordem_cols])
            _download_csv(resumo, 'diagnostico_camadas_georreferenciamento.csv', 'Baixar diagnóstico das camadas')
            st.markdown('#### Leitura rápida')
            for _, row in resumo.iterrows():
                qualidade = row.get('qualidade')
                icone = '✅' if qualidade in ['Boa', 'Municipalizada'] else '⚠️' if qualidade in ['Parcial', 'Limitada'] else '◻️'
                st.markdown(f"{icone} **{row.get('camada')}** — {row.get('qualidade')} | {_fmt_int(row.get('registros'))} registros | {row.get('pronto_para_mapa')} para mapa.")
    with tab_mapa:
        st.markdown('### Mapa preliminar municipal')
        st.caption('Mapa por centroide municipal. A cor representa a classe geográfica preliminar calculada a partir de pressão por equipe/UBS, dispersão territorial, territórios especiais e risco ambiental.')
        if base_mapa.empty:
            st.warning('A base municipal geográfica ainda não está disponível.')
        else:
            regioes = sorted([r for r in base_mapa.get('regiao_saude', pd.Series()).dropna().unique()])
            colf1, colf2 = st.columns([1, 2])
            regiao_sel = colf1.selectbox('Região de Saúde', ['Todas'] + regioes)
            busca = colf2.text_input('Buscar município', placeholder='Ex.: Cuiabá, Sinop, Cáceres')
            df = base_mapa.copy()
            if regiao_sel != 'Todas' and 'regiao_saude' in df.columns:
                df = df[df['regiao_saude'] == regiao_sel]
            if busca:
                df = df[df['municipio'].astype(str).str.contains(busca, case=False, na=False)]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric('Municípios filtrados', len(df))
            c2.metric('Muito alta/Alta', int(df.get('classe_geo_preliminar', pd.Series()).isin(['Muito alta', 'Alta']).sum()))
            c3.metric('Com assentamentos', int((pd.to_numeric(df.get('qtd_assentamentos'), errors='coerce').fillna(0) > 0).sum()))
            c4.metric('Com terras indígenas', int((pd.to_numeric(df.get('qtd_terras_indigenas_intersecoes'), errors='coerce').fillna(0) > 0).sum()))
            _render_mapa_municipal(df)
            cols = [c for c in ['municipio', 'regiao_saude', 'indice_geo_preliminar', 'classe_geo_preliminar', 'populacao', 'total_equipes_aps', 'total_ubs', 'pop_por_equipe', 'pop_por_ubs', 'qtd_assentamentos', 'qtd_terras_indigenas_intersecoes', 'qtd_ocorrencias_ambientais', 'area_km2', 'densidade_hab_km2', 'latitude', 'longitude'] if c in df.columns]
            render_html_table(df[cols].sort_values('indice_geo_preliminar', ascending=False))
            _download_csv(df[cols], 'mapa_municipal_georreferenciamento.csv', 'Baixar base filtrada do mapa')
    with tab_camadas:
        st.markdown('### Camadas territoriais especiais')
        st.caption('Resumo municipal das camadas estaduais que funcionaram: assentamentos, terras indígenas e ocorrências ambientais.')
        if base_mapa.empty:
            st.info('Base municipal indisponível.')
        else:
            cols = [c for c in ['municipio', 'regiao_saude', 'qtd_assentamentos', 'qtd_terras_indigenas_intersecoes', 'qtd_ocorrencias_ambientais', 'total_equipes_aps', 'total_ubs', 'populacao', 'area_km2', 'indice_geo_preliminar', 'classe_geo_preliminar'] if c in base_mapa.columns]
            especial = base_mapa[cols].copy()
            for col in ['qtd_assentamentos', 'qtd_terras_indigenas_intersecoes', 'qtd_ocorrencias_ambientais']:
                if col in especial.columns:
                    especial[col] = pd.to_numeric(especial[col], errors='coerce').fillna(0)
            especial = especial[(especial.get('qtd_assentamentos', 0) > 0) | (especial.get('qtd_terras_indigenas_intersecoes', 0) > 0) | (especial.get('qtd_ocorrencias_ambientais', 0) > 0)].sort_values(['qtd_terras_indigenas_intersecoes', 'qtd_assentamentos', 'qtd_ocorrencias_ambientais'], ascending=False)
            a, b, c = st.columns(3)
            a.metric('Municípios com assentamentos', int((base_mapa.get('qtd_assentamentos', pd.Series()).fillna(0) > 0).sum()))
            b.metric('Municípios com terras indígenas', int((base_mapa.get('qtd_terras_indigenas_intersecoes', pd.Series()).fillna(0) > 0).sum()))
            c.metric('Municípios com ocorrências ambientais', int((base_mapa.get('qtd_ocorrencias_ambientais', pd.Series()).fillna(0) > 0).sum()))
            render_html_table(especial)
            _download_csv(especial, 'camadas_territoriais_especiais.csv', 'Baixar camadas territoriais especiais')
    with tab_pontos:
        st.markdown('### Unidades e pontos georreferenciados')
        st.caption('Esta aba permite testar as camadas que possuem latitude/longitude individual. Algumas bases são melhores para agregação municipal do que para pontos no mapa.')
        opcoes = {'Estabelecimentos de saúde': 'estabelecimentos_saude', 'Assentamentos': 'dados_mt_assentamentos', 'Terras Indígenas': 'dados_mt_terras_indigenas', 'Ocorrências ambientais': 'dados_mt_areas_contaminadas'}
        escolha = st.selectbox('Camada de pontos', list(opcoes.keys()))
        pontos = obter_pontos_camada(opcoes[escolha])
        c1, c2 = st.columns(2)
        c1.metric('Pontos válidos', len(pontos))
        c2.metric('Camada', escolha)
        _render_mapa_pontos(pontos)
        if not pontos.empty:
            cols = [c for c in ['rotulo', 'municipio', 'lat', 'lon', 'camada'] if c in pontos.columns]
            extra = [c for c in pontos.columns if c not in cols][:8]
            render_html_table(pontos[cols + extra])
            _download_csv(pontos, f'pontos_{opcoes[escolha]}.csv', 'Baixar pontos da camada')
    with tab_base:
        st.markdown('### Base municipal geográfica')
        st.caption('Base de apoio para a etapa visual do georreferenciamento. Ela não substitui a Base Completa; é uma visão espacial focada em mapa e camadas territoriais.')
        if base_mapa.empty:
            st.warning('Base municipal geográfica indisponível.')
        else:
            cols = [c for c in ['municipio', 'codigo_ibge', 'regiao_saude', 'latitude', 'longitude', 'populacao', 'area_km2', 'densidade_hab_km2', 'total_ubs', 'total_equipes_aps', 'total_profissionais_aps', 'pop_por_equipe', 'pop_por_ubs', 'qtd_assentamentos', 'qtd_terras_indigenas_intersecoes', 'qtd_ocorrencias_ambientais', 'indice_geo_preliminar', 'classe_geo_preliminar'] if c in base_mapa.columns]
            render_html_table(base_mapa[cols])
            _download_csv(base_mapa[cols], 'base_municipal_geografica.csv', 'Baixar base municipal geográfica')
    with tab_metodo:
        _nota_metodologica('Mensagem central da plataforma', 'A plataforma organiza evidências para apoiar a priorização técnica da APS. Ela não substitui validação da APS, ERS, municípios, rotas reais, capacidade física das unidades, adscrição, pactuação regional e análise de viabilidade.', 'ok')
        st.markdown('### Metodologia e próximos passos')
        st.markdown('\n            **Status desta etapa:** diagnóstico, mapa estratégico e primeira leitura de vazios assistenciais.\n\n            Esta versão organiza as camadas disponíveis e separa três tipos de uso:\n\n            1. **Camadas municipais** — usadas para mapa por município, rankings e filtros regionais.\n            2. **Camadas de pontos** — usadas quando a base possui latitude e longitude individual.\n            3. **Camadas territorializadas por município** — úteis para agregação, mesmo quando a geometria detalhada ainda precisa de refinamento.\n\n            O **índice geográfico preliminar** exibido no mapa é apenas uma régua visual para orientar investigação. Ele considera pressão por equipes, pressão por UBS, dispersão territorial, territórios especiais e risco ambiental. Não é regra normativa e não substitui validação técnica.\n\n            **Próxima versão recomendada:** evoluir para análise de raio territorial e proximidade entre unidades, territórios especiais e municípios prioritários, permitindo simular cobertura territorial e deslocamento.\n            ')
        if not resumo.empty:
            st.markdown('#### Campos detectados por camada')
            render_html_table(resumo[['tabela', 'coluna_municipio', 'coluna_codigo', 'coluna_latitude', 'coluna_longitude', 'coluna_geometria', 'qualidade']])
