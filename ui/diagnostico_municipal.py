from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from services.diagnostico_municipal_inteligente_service import (
    coletar_diagnostico_municipal,
    listar_municipios,
    montar_relatorio_textual,
)


CORES = {
    "Bom": "#12B76A",
    "Regular": "#FEC84B",
    "Ruim": "#F79009",
    "Crítico": "#F04438",
    "Sem dado": "#98A2B3",
}


def _css():
    st.markdown(
        """
        <style>
        .dm-hero{background:linear-gradient(135deg,#081E3F 0%,#0B3C7D 55%,#1E88E5 100%);color:#fff;padding:24px;border-radius:22px;margin-bottom:16px;box-shadow:0 12px 34px rgba(11,60,125,.25)}
        .dm-hero h1{color:#fff;margin:0;font-size:1.8rem}.dm-hero p{margin:.45rem 0 0;opacity:.96;line-height:1.45}
        .dm-card{background:#fff;border:1px solid #D9E4F2;border-radius:18px;padding:15px;box-shadow:0 6px 22px rgba(16,24,40,.06);height:100%}
        .dm-card small{color:#667085;font-weight:800;text-transform:uppercase;letter-spacing:.04em}.dm-card .valor{font-size:1.65rem;color:#1E276F;font-weight:900;margin-top:.1rem}.dm-card .txt{color:#344054;font-size:.9rem;line-height:1.35;margin-top:.25rem}
        .dm-info{background:#EAF2FF;border-left:5px solid #2F80ED;border-radius:14px;padding:14px 16px;margin:12px 0;color:#1E276F;line-height:1.48}
        .dm-warn{background:#FFF7E6;border-left:5px solid #F59E0B;border-radius:14px;padding:14px 16px;margin:12px 0;color:#7A4E00;line-height:1.48}
        .dm-bad{background:#FEF3F2;border-left:5px solid #F04438;border-radius:14px;padding:14px 16px;margin:12px 0;color:#7A271A;line-height:1.48}
        .dm-good{background:#ECFDF3;border-left:5px solid #12B76A;border-radius:14px;padding:14px 16px;margin:12px 0;color:#05603A;line-height:1.48}
        .chip{display:inline-block;padding:5px 10px;border-radius:999px;font-weight:800;font-size:.82rem;margin:2px 4px 2px 0}.chip-Crítico{background:#FEE4E2;color:#B42318}.chip-Ruim{background:#FEF0C7;color:#B54708}.chip-Regular{background:#FEF7C3;color:#854A0E}.chip-Bom{background:#D1FADF;color:#05603A}.chip-Sem{background:#EAECF0;color:#475467}
        .axis-title{font-size:1.5rem;font-weight:900;color:#101828;margin:.5rem 0 .2rem}.muted{color:#667085;font-size:.9rem;line-height:1.4}
        .dm-section-title{font-size:1.1rem;font-weight:900;color:#1E276F;margin:.35rem 0 .6rem}
        .dm-flow{display:flex;gap:12px;align-items:stretch;overflow-x:auto;padding:4px 0 6px}
        .dm-step{min-width:190px;flex:1;background:#fff;border:1px solid #D9E4F2;border-radius:18px;padding:14px;position:relative;box-shadow:0 6px 20px rgba(16,24,40,.05)}
        .dm-step:after{content:'➜';position:absolute;right:-10px;top:50%;transform:translateY(-50%);background:#fff;color:#98A2B3;font-weight:900}
        .dm-step:last-child:after{display:none}
        .dm-step .n{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:900;color:#fff;margin-bottom:10px}
        .dm-step h4{margin:0 0 6px;color:#101828;font-size:1rem}.dm-step p{margin:0;color:#475467;font-size:.89rem;line-height:1.42}
        .dm-mini-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:8px}
        .dm-mini{background:#fff;border:1px solid #D9E4F2;border-radius:18px;padding:14px;box-shadow:0 6px 20px rgba(16,24,40,.05)}
        .dm-mini .k{font-size:.78rem;font-weight:800;text-transform:uppercase;letter-spacing:.04em;color:#667085}.dm-mini .v{font-size:1.4rem;font-weight:900;color:#1E276F;margin:.2rem 0}.dm-mini .s{color:#475467;font-size:.88rem;line-height:1.4}
        .dm-infobox{background:linear-gradient(135deg,#F9FBFF 0%,#EEF4FF 100%);border:1px solid #D6E4FF;border-radius:22px;padding:16px;margin:10px 0 14px}
        .dm-pill{display:inline-block;padding:4px 10px;border-radius:999px;font-size:.78rem;font-weight:800;margin:0 6px 6px 0;color:#fff}
        .dm-iconcard{background:#fff;border-radius:18px;padding:14px;border:1px solid #D9E4F2;box-shadow:0 6px 20px rgba(16,24,40,.05);height:100%}
        .dm-iconcard .emoji{font-size:1.6rem;line-height:1}.dm-iconcard .title{font-weight:900;color:#101828;margin:.35rem 0 .3rem}.dm-iconcard .body{color:#475467;font-size:.89rem;line-height:1.43}
        .dm-ribbon{display:flex;gap:0;align-items:stretch;overflow:hidden;border-radius:18px;border:1px solid #D9E4F2;margin:12px 0;background:#fff}
        .dm-ribbon .seg{flex:1;padding:12px 14px;color:#fff;font-weight:800;line-height:1.3;font-size:.9rem}
        .dm-ribbon .seg small{display:block;font-weight:600;opacity:.92;font-size:.78rem;margin-top:4px}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _fmt(v: Any, dec: int = 1) -> str:
    try:
        if pd.isna(v):
            return "Não disponível"
        f = float(v)
        if abs(f) >= 1_000_000:
            return f"{f/1_000_000:.{dec}f} mi".replace('.', ',')
        if abs(f) >= 1000:
            return f"{f:,.0f}".replace(',', '.')
        if abs(f - round(f)) < 0.05:
            return f"{f:.0f}"
        return f"{f:.{dec}f}".replace('.', ',')
    except Exception:
        return str(v) if v not in [None, ""] else "Não disponível"


def _info(titulo: str, itens: List[str], tipo: str = "info"):
    cls = {"info": "dm-info", "warn": "dm-warn", "bad": "dm-bad", "good": "dm-good"}.get(tipo, "dm-info")
    lis = "".join(f"<li>{i}</li>" for i in itens)
    st.markdown(f"<div class='{cls}'><b>ℹ️ {titulo}</b><ul>{lis}</ul></div>", unsafe_allow_html=True)


def _card(titulo: str, valor: str, texto: str):
    st.markdown(f"<div class='dm-card'><small>{titulo}</small><div class='valor'>{valor}</div><div class='txt'>{texto}</div></div>", unsafe_allow_html=True)


def _chip(classe: str):
    cls = "Sem" if classe == "Sem dado" else classe
    st.markdown(f"<span class='chip chip-{cls}'>{classe}</span>", unsafe_allow_html=True)


def _termometro(score: Any, classe: str):
    score = 0 if score is None else float(score)
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=score,
        number={"suffix": " pts"},
        title={"text": f"Situação geral: {classe}"},
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": CORES.get(classe, "#475467")},
            "steps": [
                {"range": [0, 30], "color": "#D1FADF"},
                {"range": [30, 50], "color": "#FEF7C3"},
                {"range": [50, 70], "color": "#FEF0C7"},
                {"range": [70, 100], "color": "#FEE4E2"},
            ],
            "threshold": {"line": {"color": "#101828", "width": 3}, "thickness": .75, "value": score},
        },
    ))
    fig.update_layout(height=260, margin=dict(l=20, r=20, t=50, b=20))
    st.plotly_chart(fig, use_container_width=True, key="dm_termometro")
    _info("Como ler este termômetro", [
        "Quanto mais próximo de 100, maior a combinação de fragilidades identificadas nos eixos do diagnóstico.",
        "A classificação não é decisão automática de financiamento; ela organiza uma fila técnica de validação.",
        "O motivo da classificação aparece nos cards e nos insights por eixo logo abaixo.",
    ])


def _grafico_eixos(diag: Dict[str, Any]):
    df = pd.DataFrame([{"Eixo": e["titulo"], "Score": e["score"], "Classe": e["classe"]} for e in diag.get("eixos", [])])
    if df.empty:
        return
    fig = px.bar(df, x="Score", y="Eixo", color="Classe", orientation="h", text="Classe", color_discrete_map=CORES,
                 title="Por que o município foi classificado assim — leitura por eixo")
    fig.update_layout(height=420, xaxis_title="Score de fragilidade do eixo", yaxis_title="", legend_title="Situação", margin=dict(l=10, r=10, t=55, b=30))
    fig.update_traces(textposition="inside")
    st.plotly_chart(fig, use_container_width=True, key="dm_eixos_score")
    _info("Como ler este gráfico", [
        "Cada barra representa um eixo do diagnóstico municipal; barras maiores indicam maior fragilidade naquele eixo.",
        "O gráfico mostra o motivo da situação geral: por exemplo, o município pode estar ruim por vulnerabilidade social, por APS insuficiente, por ruralidade ou por indicadores materno-infantis.",
        "Use este gráfico para escolher onde aprofundar: o eixo mais crítico deve virar pauta de reunião técnica com o município e a ERS.",
    ])


def _render_insights(diag: Dict[str, Any]):
    st.subheader("🧠 Insights integrados — o sistema cruza dados e explica")
    _info("Como usar os insights", [
        "Cada linha segue a lógica: situação → causa → evidência → consequência → ação sugerida.",
        "A intenção é transformar dado bruto em leitura técnica para decisão, despacho e plano de ação.",
        "Quando aparecer 'Crítico' ou 'Ruim', valide com a área técnica, mas trate como sinal de prioridade.",
    ])
    rows = diag.get("insights", [])
    if not rows:
        st.info("Nenhum insight automático gerado com as bases atuais.")
        return
    for i, ins in enumerate(rows, 1):
        tipo = "bad" if ins.get("situacao") == "Crítico" else "warn" if ins.get("situacao") == "Ruim" else "info"
        st.markdown(f"#### {i}. {ins.get('eixo')} — {ins.get('situacao')}")
        _info("Leitura técnica", [
            f"Causa provável: {ins.get('causa')}",
            f"Evidência: {ins.get('evidencia')}",
            f"Consequência provável: {ins.get('consequencia')}",
            f"Ação sugerida: {ins.get('acao')}",
        ], tipo=tipo)


def _indicadores_df(eixo: Dict[str, Any]) -> pd.DataFrame:
    df = pd.DataFrame(eixo.get("indicadores", []))
    if df.empty:
        return df
    return df[["Indicador", "Valor", "Leitura", "Parâmetro/Referência", "Público/território relacionado", "Fonte"]]


def _render_eixo(eixo: Dict[str, Any], key: str):
    st.markdown(f"<div class='axis-title'>{eixo['titulo']}</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='muted'>{eixo['subtitulo']}</div>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 2, 2])
    with c1:
        _card("Situação do eixo", eixo.get("classe", "Sem dado"), "Classificação baseada nos indicadores interpretáveis disponíveis.")
    with c2:
        _info("Por que importa", eixo.get("por_que", []), tipo="info")
    with c3:
        _info("Consequência se não agir", eixo.get("consequencias", []), tipo="warn" if eixo.get("classe") in ["Ruim", "Crítico"] else "info")

    st.markdown("### Indicadores que realmente ajudam a entender este eixo")
    df = _indicadores_df(eixo)
    if not df.empty:
        st.dataframe(df, use_container_width=True, hide_index=True, key=f"df_{key}")

        chart_df = pd.DataFrame(eixo.get("indicadores", []))
        chart_df = chart_df.dropna(subset=["valor_num"])
        if not chart_df.empty:
            # Remove indicadores cujo significado é só código/ano solto por segurança
            chart_df = chart_df[~chart_df["Indicador"].str.lower().str.contains("código|ano", regex=True)]
            chart_df = chart_df.head(8)
            if not chart_df.empty:
                fig = px.bar(chart_df, x="valor_num", y="Indicador", orientation="h", text="Valor", title="Indicadores selecionados do eixo")
                fig.update_layout(height=360, xaxis_title="Valor", yaxis_title="", margin=dict(l=10, r=10, t=50, b=30))
                fig.update_traces(textposition="outside")
                st.plotly_chart(fig, use_container_width=True, key=f"graf_{key}")
                _info("Como ler este gráfico", [
                    "As barras mostram apenas indicadores curados para este eixo, evitando códigos administrativos e campos sem significado sanitário direto.",
                    "Nem todo valor alto é ruim: a interpretação está na coluna 'Leitura' e no parâmetro de referência da tabela.",
                    "Use a combinação entre valor, público relacionado e sugestão de política para entender o que fazer.",
                ])
    else:
        st.info("Não foram encontrados indicadores interpretáveis para este eixo na base atual.")

    c4, c5 = st.columns(2)
    with c4:
        _info("Públicos e territórios que podem ser mais afetados", eixo.get("publicos", []), tipo="info")
    with c5:
        _info("Sugestões de políticas/ações", eixo.get("politicas", []), tipo="good")


def _render_sinan(diag: Dict[str, Any]):
    top = pd.DataFrame(diag.get("sinan_top", []))
    if top.empty:
        return
    st.markdown("### Agravos prioritários do SINAN")
    st.dataframe(top, use_container_width=True, hide_index=True)
    fig = px.bar(top, x="Notificações", y="Agravo", orientation="h", title="Agravos com maior volume de notificações")
    fig.update_layout(height=380, yaxis_title="", xaxis_title="Notificações")
    st.plotly_chart(fig, use_container_width=True, key="sinan_top")
    _info("Como interpretar os agravos", [
        "O maior número de notificações não significa necessariamente pior assistência; pode indicar maior ocorrência, melhor vigilância ou ambos.",
        "A leitura correta cruza agravo com território, saneamento, ruralidade, escolas, vulnerabilidade e capacidade da APS.",
        "Ação recomendada: escolher os três agravos de maior relevância e pactuar agenda APS + Vigilância + ACE/ACS.",
    ])


def _render_tendencias(diag: Dict[str, Any]):
    st.subheader("📈 Séries históricas e tendências")
    _info("Por que tendência importa", [
        "Um município pode estar ruim, mas melhorando; ou parecer regular, mas piorando rapidamente.",
        "Esta aba só usa séries com ano/competência reconhecidos. Dados de um único ano aparecem como fotografia, não tendência.",
        "A próxima evolução do sistema deve ampliar séries por APS, MDS, SINAN, SINASC e SIM.",
    ])
    df = pd.DataFrame(diag.get("tendencias", []))
    if df.empty:
        st.warning("Ainda não há série histórica suficiente estruturada para este município nas tabelas reconhecidas.")
        st.markdown("**Pendência técnica:** consolidar indicadores anuais/competência para permitir tendência real por eixo.")
        return
    st.dataframe(df, use_container_width=True, hide_index=True)
    top = df.head(12)
    fig = px.bar(top, x="Variação", y="Indicador", orientation="h", color="Tendência", title="Variações detectadas nas séries disponíveis")
    fig.update_layout(height=420, yaxis_title="", xaxis_title="Variação no período")
    st.plotly_chart(fig, use_container_width=True, key="tendencias_var")




def _cor_classe(classe: str) -> str:
    return CORES.get(classe or 'Sem dado', '#98A2B3')


def _extrair_top_indicadores(eixo: Dict[str, Any], limite: int = 3) -> List[Dict[str, Any]]:
    base = []
    for item in eixo.get('indicadores', []):
        nome = str(item.get('Indicador', ''))
        if not nome:
            continue
        # evita indicadores puramente estruturais sem leitura forte
        if any(t in nome.lower() for t in ['área territorial']):
            continue
        val_num = item.get('valor_num')
        leitura = item.get('Leitura', '')
        base.append({
            'nome': nome,
            'valor': item.get('Valor', 'Não disponível'),
            'leitura': leitura,
            'parametro': item.get('Parâmetro/Referência', ''),
            'publico': item.get('Público/território relacionado', ''),
            'peso': 1 if val_num is not None else 0,
        })
    return base[:limite]


def _render_infografico_sintese(diag: Dict[str, Any]):
    perfil = diag.get('perfil', {})
    classe = perfil.get('classe_geral', 'Sem dado')
    score = _fmt(perfil.get('score_geral'))
    eixos = diag.get('eixos', [])
    piores = [e for e in eixos if e.get('classe') in ['Crítico', 'Ruim']]
    motores = ', '.join([e.get('titulo', '').split(',')[0] for e in piores[:3]]) or 'Sem eixo crítico destacado pela régua atual'
    publicos = []
    for e in piores[:3]:
        publicos.extend(e.get('publicos', [])[:2])
    publicos = ', '.join(list(dict.fromkeys(publicos))[:5]) or 'População geral'
    acoes = []
    for e in piores[:2]:
        acoes.extend(e.get('politicas', [])[:1])
    acao = ' | '.join(acoes) or perfil.get('decisao', 'Validar com a área técnica.')
    st.markdown("<div class='dm-section-title'>Infográfico-resumo — leitura rápida do município</div>", unsafe_allow_html=True)
    html = f"""
    <div class='dm-flow'>
      <div class='dm-step'><div class='n' style='background:{_cor_classe(classe)}'>1</div><h4>Situação geral</h4><p><b>{classe}</b> com score técnico de <b>{score}</b>. Esta é a leitura sintética do município no conjunto dos eixos.</p></div>
      <div class='dm-step'><div class='n' style='background:#2F80ED'>2</div><h4>Por que entrou no radar</h4><p>{motores}</p></div>
      <div class='dm-step'><div class='n' style='background:#F59E0B'>3</div><h4>Quem tende a sentir mais</h4><p>{publicos}</p></div>
      <div class='dm-step'><div class='n' style='background:#7A5AF8'>4</div><h4>Risco para a saúde</h4><p>Sem resposta oportuna, pode haver maior desassistência, atraso no cuidado, baixa prevenção e pressão sobre a APS.</p></div>
      <div class='dm-step'><div class='n' style='background:#12B76A'>5</div><h4>Primeira agenda sugerida</h4><p>{acao}</p></div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)
    _info('Como ler este infográfico-resumo', [
        'Ele transforma o diagnóstico técnico em uma sequência lógica: situação → causa → público afetado → risco → primeira resposta.',
        'Use como leitura de abertura em reunião com gestão municipal, ERS e áreas técnicas.',
        'O detalhamento de cada eixo aparece logo abaixo, com indicadores, políticas e públicos relacionados.'
    ])


def _render_infografico_eixo(eixo: Dict[str, Any], key: str):
    classe = eixo.get('classe', 'Sem dado')
    cor = _cor_classe(classe)
    top_inds = _extrair_top_indicadores(eixo, 3)
    publicos = eixo.get('publicos', [])[:4]
    politicas = eixo.get('politicas', [])[:3]
    consequencias = eixo.get('consequencias', [])[:2]
    st.markdown("<div class='dm-section-title'>Infográfico do eixo — do achado à ação</div>", unsafe_allow_html=True)
    ribbon = f"""
    <div class='dm-ribbon'>
      <div class='seg' style='background:{cor}'>Situação do eixo<small>{classe}</small></div>
      <div class='seg' style='background:#2F80ED'>Por que importa<small>{(eixo.get('por_que') or ['Eixo estratégico para o cuidado municipal.'])[0]}</small></div>
      <div class='seg' style='background:#F59E0B'>Quem pode sentir mais<small>{', '.join(publicos) if publicos else 'População geral'}</small></div>
      <div class='seg' style='background:#12B76A'>Primeira resposta<small>{politicas[0] if politicas else 'Validar e pactuar resposta técnica.'}</small></div>
    </div>
    """
    st.markdown(ribbon, unsafe_allow_html=True)
    cards = []
    emojis = ['📌','📊','🎯']
    for i, ind in enumerate(top_inds):
        cards.append(f"<div class='dm-iconcard'><div class='emoji'>{emojis[i % len(emojis)]}</div><div class='title'>{ind['nome']}</div><div class='body'><b>Valor:</b> {ind['valor']}<br><b>O que isso sugere:</b> {ind['leitura']}<br><b>Referência:</b> {ind['parametro']}</div></div>")
    while len(cards) < 3:
        cards.append("<div class='dm-iconcard'><div class='emoji'>ℹ️</div><div class='title'>Sem indicador adicional</div><div class='body'>Este eixo depende de validação complementar ou possui menos indicadores interpretáveis na base atual.</div></div>")
    row2 = [
        f"<div class='dm-iconcard'><div class='emoji'>👥</div><div class='title'>Públicos/territórios prioritários</div><div class='body'>{'; '.join(publicos) if publicos else 'População geral.'}</div></div>",
        f"<div class='dm-iconcard'><div class='emoji'>⚠️</div><div class='title'>Se nada for feito</div><div class='body'>{'; '.join(consequencias) if consequencias else 'Pode haver piora da situação e pressão sobre a APS.'}</div></div>",
        f"<div class='dm-iconcard'><div class='emoji'>✅</div><div class='title'>Ação imediata sugerida</div><div class='body'>{'; '.join(politicas[:2]) if politicas else 'Validar e pactuar ação técnica.'}</div></div>",
    ]
    st.markdown(f"<div class='dm-mini-grid'>{''.join(cards)}</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='dm-mini-grid'>{''.join(row2)}</div>", unsafe_allow_html=True)
    _info('Como usar este infográfico do eixo', [
        'Leia da esquerda para a direita: primeiro o eixo mostra a situação, depois os indicadores-chave, os públicos atingidos e a ação sugerida.',
        'A intenção é permitir que qualquer gestor entenda rapidamente o sentido do dado, sem precisar interpretar tabelas soltas.',
        'Depois do infográfico, a tabela técnica continua disponível para aprofundamento e validação.'
    ], tipo='good')


def _render_painel_publicos(diag: Dict[str, Any]):
    mapa = {}
    for e in diag.get('eixos', []):
        classe = e.get('classe', 'Sem dado')
        for p in e.get('publicos', []):
            mapa.setdefault(p, []).append(classe)
    if not mapa:
        return
    linhas = []
    for publico, classes in mapa.items():
        peso = sum({'Crítico':4, 'Ruim':3, 'Regular':2, 'Bom':1}.get(c,0) for c in classes)
        if peso >= 10: leitura = 'Alta prioridade transversal'
        elif peso >= 6: leitura = 'Prioridade importante'
        else: leitura = 'Monitoramento'
        linhas.append((publico, leitura, len(classes)))
    linhas = sorted(linhas, key=lambda x:(-x[2], x[0]))[:8]
    boxes=[]
    colors={'Alta prioridade transversal':'#F04438','Prioridade importante':'#F59E0B','Monitoramento':'#2F80ED'}
    for pub, leit, n in linhas:
        boxes.append(f"<div class='dm-mini'><span class='dm-pill' style='background:{colors[leit]}'>{leit}</span><div class='v' style='font-size:1.05rem'>{pub}</div><div class='s'>Aparece em <b>{n}</b> eixo(s) do diagnóstico, indicando necessidade de leitura integrada.</div></div>")
    st.markdown("<div class='dm-section-title'>Infográfico de públicos que merecem atenção</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='dm-mini-grid'>{''.join(boxes)}</div>", unsafe_allow_html=True)
    _info('Como ler este painel', [
        'Ele mostra quais grupos aparecem repetidamente nos eixos do diagnóstico e, por isso, merecem plano integrado.',
        'Quando um público surge em vários eixos, a gestão deve evitar ação isolada e pensar em resposta territorial e intersetorial.'
    ])


def render():
    _css()
    st.markdown("<div class='dm-hero'><h1>🏙️ Diagnóstico Municipal Inteligente</h1><p>Leitura profunda por município: cada eixo transforma dados em evidência, interpretação, público afetado, consequência provável e ação sugerida.</p></div>", unsafe_allow_html=True)

    municipios = listar_municipios()
    if not municipios:
        st.error("Nenhum município encontrado no banco de dados.")
        return
    municipio = st.selectbox("Selecione o município", municipios, index=0, key="dm_municipio_select")
    diag = coletar_diagnostico_municipal(municipio)
    perfil = diag.get("perfil", {})

    c1, c2, c3, c4 = st.columns(4)
    with c1: _card("Município", municipio, f"Região: {perfil.get('regiao_saude') or 'não informada'}")
    with c2: _card("População", _fmt(perfil.get("populacao")), "Base populacional usada nos cruzamentos.")
    with c3: _card("Situação geral", perfil.get("classe_geral", "Sem dado"), "Resumo dos eixos com dados interpretáveis.")
    with c4: _card("Decisão sugerida", "Plano técnico", perfil.get("decisao", "Validar com a área técnica."))

    abas = st.tabs([
        "📌 Síntese",
        "🌍 Território",
        "🏥 APS/capacidade",
        "💰 Renda/vulnerabilidade",
        "🎓 Educação",
        "👶 Materno-infantil",
        "⚰️ Mortalidade",
        "🦠 Vigilância/SINAN",
        "📈 Tendências",
        "📄 Relatório",
        "🧾 Fontes",
    ])

    eixos = {e["titulo"]: e for e in diag.get("eixos", [])}

    with abas[0]:
        col_a, col_b = st.columns([1, 1])
        with col_a:
            _termometro(perfil.get("score_geral"), perfil.get("classe_geral", "Sem dado"))
        with col_b:
            _grafico_eixos(diag)
        _render_infografico_sintese(diag)
        _render_painel_publicos(diag)
        _render_insights(diag)

    eixo_ordem = [
        (1, "Território, população e ruralidade", "territorio"),
        (2, "APS, CNES e capacidade instalada", "aps"),
        (3, "Renda, vulnerabilidade e proteção social", "renda"),
        (4, "Educação, escolaridade e intersetorialidade", "educacao"),
        (5, "Materno-infantil, nascimentos e primeira infância", "materno"),
        (6, "Mortalidade e condições crônicas/externas", "mortalidade"),
        (7, "Vigilância, agravos e resposta territorial", "vigilancia"),
    ]
    for aba_idx, eixo_nome, key in eixo_ordem:
        with abas[aba_idx]:
            eixo = eixos.get(eixo_nome)
            if eixo:
                _render_infografico_eixo(eixo, key)
                _render_eixo(eixo, key)
                if key == "vigilancia":
                    _render_sinan(diag)
            else:
                st.info("Eixo não encontrado na composição atual.")

    with abas[8]:
        _render_tendencias(diag)

    with abas[9]:
        st.subheader("📄 Relatório narrativo copiável")
        rel = montar_relatorio_textual(diag)
        st.text_area("Texto para despacho/relatório técnico", rel, height=520)
        st.download_button("Baixar relatório TXT", data=rel.encode("utf-8"), file_name=f"diagnostico_municipal_{municipio}.txt", mime="text/plain")
        _info("Como usar este relatório", [
            "Use como minuta técnica inicial; complemente com validação local, rotas reais, capacidade física das UBS e pactuações regionais.",
            "O texto já organiza situação, causas, evidências, consequências e ações sugeridas.",
            "Dados ausentes não devem ser tratados como zero; são pendências de integração ou validação.",
        ])

    with abas[10]:
        st.subheader("🧾 Inventário das fontes usadas no município")
        fontes = pd.DataFrame(diag.get("fontes", []))
        st.dataframe(fontes, use_container_width=True, hide_index=True)
        _info("Como ler o inventário", [
            "Mostra se a base existe e se há registro específico para o município selecionado.",
            "Ajuda a separar ausência real de informação de falha de integração.",
            "Quando uma base aparecer sem registro, o relatório deve mencionar cautela metodológica.",
        ])
