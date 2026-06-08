from __future__ import annotations

import html
import math
import re
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

GOVMT_COLORS = {
    "blue_900": "#1E276F",
    "blue_800": "#252E7F",
    "blue_700": "#29358A",
    "blue_600": "#3343A2",
    "blue_100": "#EEF1FF",
    "cyan_600": "#1494B8",
    "cyan_500": "#20A7C9",
    "cyan_100": "#E9F8FC",
    "ink": "#111827",
    "text": "#1F2A44",
    "muted": "#667085",
    "line": "#DDE3F0",
    "line_soft": "#EAF0F7",
    "bg": "#F6F8FC",
    "white": "#FFFFFF",
    "green": "#168A5B",
    "orange": "#D98A14",
    "red": "#BD3E3E",
    "purple": "#6E4BD8",
}

GOVMT_SEQUENCE = [
    GOVMT_COLORS["blue_800"],
    GOVMT_COLORS["cyan_600"],
    GOVMT_COLORS["purple"],
    GOVMT_COLORS["orange"],
    GOVMT_COLORS["green"],
    GOVMT_COLORS["red"],
    "#6377B8",
    "#83B6C9",
]

COLUMN_LABELS = {
    "municipio": "Município",
    "regiao_saude": "Região de Saúde",
    "populacao": "População",
    "area_km2": "Área territorial (km²)",
    "total_ubs": "UBS / estab. APS",
    "total_equipes_aps": "Equipes APS",
    "profissionais_cnes": "Profissionais CNES",
    "total_profissionais": "Profissionais",
    "populacao_por_equipe": "População por equipe",
    "populacao_por_ubs": "População por UBS",
    "pop_por_equipe": "População por equipe",
    "pop_por_ubs": "População por UBS",
    "indice_geo_preliminar": "Índice geográfico preliminar",
    "classe_geo_preliminar": "Classe geográfica preliminar",
    "qtd_assentamentos": "Assentamentos",
    "qtd_terras_indigenas_intersecoes": "Terras indígenas / interseções",
    "qtd_ocorrencias_ambientais": "Ocorrências ambientais",
    "score_prioridade_integrada": "Score integrado",
    "score_acesso_territorial": "Acesso territorial",
    "score_vazio_assistencial": "Pressão assistencial",
    "score_vulnerabilidade_social": "Vulnerabilidade social",
    "score_fragilidade_capacidade": "Fragilidade da capacidade",
    "score_equidade_territorial": "Equidade territorial",
    "classe_prioridade": "Classe de prioridade",
    "classe_qualidade_dados": "Qualidade dos dados",
    "qualidade_dados_score": "Score qualidade dados",
    "fatores_prioritarios": "Fatores prioritários",
    "principal_motivo_prioridade": "Principal motivo",
    "acao_sugerida": "Ação sugerida",
    "validacao_recomendada": "Validação recomendada",
    "alerta_acao": "Alerta / ação",
    "posicao_prioridade": "Ranking estadual",
    "percentil_prioridade": "Percentil prioridade",
    "territorios_criticos_distantes": "Territórios distantes críticos",
    "populacao_territorios_criticos_distantes": "População em áreas críticas",
    "assentamentos_criticos_distantes": "Assentamentos críticos",
    "distancia_media_territorios_km": "Distância média até UBS (km)",
    "distancia_maxima_territorios_km": "Maior distância até UBS (km)",
    "distancia_ubs_mais_proxima_km": "Distância até UBS (km)",
    "ubs_mais_proxima": "UBS mais próxima",
    "nome_ubs_mais_proxima": "UBS mais próxima",
    "bairro_ou_localidade": "Bairro / localidade / setor",
    "tipo_territorio": "Tipo territorial",
    "classe_distancia": "Classe de distância",
    "prioridade_territorial": "Prioridade territorial",
    "perfil_municipal_aps": "Perfil APS do município",
    "componente_dominante": "Componente dominante",
    "risco_subestimado": "Risco subestimado",
    "desequilibrio_intramunicipal": "Desequilíbrio intramunicipal",
    "tipo_alerta": "Tipo de alerta",
    "problema_principal": "Problema principal",
    "resposta_sugerida": "Resposta sugerida",
    "eixo_intervencao": "Eixo de intervenção",
    "urgencia": "Urgência",
    "evidencia": "Evidência",
    "prazo_sugerido": "Prazo sugerido",
    "codigo": "Código CNES/INE",
    "tipo_equipe": "Tipo de equipe",
    "total_equipes": "Total de equipes",
    "score_medio": "Score médio",
    "vazio_medio": "Pressão assistencial média",
    "vulnerabilidade_media": "Vulnerabilidade média",
    "equidade_media": "Equidade territorial média",
    "municipios_prioritarios": "Municípios prioritários",
    "municipios": "Municípios",
    "ações": "Ações",
    "perfil": "Perfil",
    "classe": "Classe",
    "dimensão": "Dimensão",
    "peso no score integrado": "Peso no score integrado",
    "o que observa": "O que observa",
    "componente": "Componente",
    "score": "Score",
    "peso": "Peso",
    "interpretação": "Interpretação",
    "distancia_maxima_km": "Maior distância (km)",
    "distancia_media_km": "Distância média (km)",
    "territorios": "Territórios",
}


def _humanize_column_name(col: Any) -> str:
    original = str(col)
    if original in COLUMN_LABELS:
        return COLUMN_LABELS[original]
    text = original.replace("_", " ").strip()
    text = re.sub(r"\s+", " ", text)
    replacements = {
        "aps": "APS",
        "ubs": "UBS",
        "cnes": "CNES",
        "ine": "INE",
        "ibge": "IBGE",
        "km": "km",
        "id": "ID",
    }
    out = []
    for part in text.split(" "):
        low = part.lower()
        out.append(replacements.get(low, part.capitalize()))
    return " ".join(out)


def _format_number_br(value: Any, decimals: int = 1) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    try:
        number = float(value)
    except Exception:
        return str(value)
    return f"{number:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _format_int_br(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    try:
        return f"{int(round(float(value))):,}".replace(",", ".")
    except Exception:
        return str(value)


def _format_value(col: str, value: Any) -> Any:
    if value is None:
        return "-"
    if isinstance(value, str):
        return value
    col_l = str(col).lower()
    if any(token in col_l for token in ["populacao", "territorios", "assentamentos", "municipios", "total_", "quantidade", "ações", "qtd_"]):
        return _format_int_br(value)
    if "percent" in col_l or "percentual" in col_l:
        return f"{_format_number_br(value, 1)}%"
    if "km" in col_l or "distancia" in col_l:
        return f"{_format_number_br(value, 1)} km"
    if "score" in col_l or "media" in col_l or "médio" in col_l or "taxa" in col_l or "por_" in col_l or col_l.startswith("indice"):
        return _format_number_br(value, 1)
    if isinstance(value, (int, float)):
        return _format_number_br(value, 1)
    return value


def _ensure_dataframe(df) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()
    if isinstance(df, pd.DataFrame):
        return df
    try:
        return pd.DataFrame(df)
    except Exception:
        return pd.DataFrame()


def preparar_tabela(df: pd.DataFrame, max_text: int = 120) -> pd.DataFrame:
    df = _ensure_dataframe(df)
    if df.empty:
        return pd.DataFrame()
    out = df.copy()
    for col in out.columns:
        out[col] = out[col].map(lambda v, c=col: _format_value(c, v))
        if out[col].dtype == object:
            out[col] = out[col].map(lambda v: (v[:max_text] + "…") if isinstance(v, str) and len(v) > max_text else v)
    out = out.rename(columns={c: _humanize_column_name(c) for c in out.columns})
    return out


def render_dataframe(
    df: pd.DataFrame,
    *args,
    titulo: str | None = None,
    subtitulo: str | None = None,
    max_text: int = 120,
    **kwargs,
):
    if df is None or getattr(df, "empty", True):
        st.info("Não há registros para exibir nesta seleção.")
        return
    if titulo:
        st.markdown(f"<div class='table-title'>{titulo}</div>", unsafe_allow_html=True)
    if subtitulo:
        st.markdown(f"<div class='table-subtitle'>{subtitulo}</div>", unsafe_allow_html=True)
    display = preparar_tabela(df, max_text=max_text)
    kwargs.setdefault("use_container_width", True)
    kwargs.setdefault("hide_index", True)
    st.dataframe(display, *args, **kwargs)


def _badge_html(value: Any, col_name: str) -> str:
    raw = "-" if value is None else str(value)
    val = raw.lower()
    cls = ""
    if any(token in col_name.lower() for token in ["classe", "prioridade", "urgencia", "qualidade"]):
        if any(k in val for k in ["crít", "critic", "muito alta", "muito alta/alta", "muito alta", "muito alta/alta"]):
            cls = "badge-red"
        elif any(k in val for k in ["alta", "alto"]):
            cls = "badge-orange"
        elif any(k in val for k in ["média", "medio", "médio", "atenção"]):
            cls = "badge-yellow"
        elif any(k in val for k in ["baixa", "monitor", "adequada"]):
            cls = "badge-blue"
        elif any(k in val for k in ["boa", "alta confiabilidade"]):
            cls = "badge-green"
    if cls:
        return f"<span class='govmt-badge {cls}'>{html.escape(raw)}</span>"
    return html.escape(raw)


def render_html_table(
    df: pd.DataFrame,
    titulo: str | None = None,
    subtitulo: str | None = None,
    max_rows: int = 20,
    max_text: int = 100,
):
    df = _ensure_dataframe(df)
    if df.empty:
        st.info("Não há registros para exibir nesta seleção.")
        return
    display = preparar_tabela(df.head(max_rows), max_text=max_text)
    css = """
    <style>
    .govmt-table-wrap{background:#fff;border:1px solid #E5EBF5;border-radius:18px;overflow:hidden;box-shadow:0 8px 24px rgba(30,39,111,.06);margin:.25rem 0 1rem 0}
    .govmt-table-head{padding:1rem 1.1rem .35rem 1.1rem;background:linear-gradient(180deg,#F8FAFF 0%, #FFFFFF 100%);border-bottom:1px solid #EEF2FA}
    .govmt-table-title{font-size:1.02rem;font-weight:800;color:#1E276F;margin:0 0 .12rem 0}
    .govmt-table-subtitle{font-size:.86rem;color:#667085;margin:0}
    .govmt-table-scroll{overflow:auto;}
    table.govmt-html-table{border-collapse:separate;border-spacing:0;width:100%;font-size:.92rem;color:#22304A}
    table.govmt-html-table thead th{position:sticky;top:0;background:#1E276F;color:#fff;text-align:left;padding:.82rem .78rem;font-weight:700;white-space:nowrap;border-bottom:1px solid #22338A}
    table.govmt-html-table tbody td{padding:.72rem .78rem;border-bottom:1px solid #EEF2FA;vertical-align:top}
    table.govmt-html-table tbody tr:nth-child(even){background:#FAFBFE}
    table.govmt-html-table tbody tr:hover{background:#F2F6FF}
    table.govmt-html-table td.num{text-align:right;font-variant-numeric:tabular-nums}
    .govmt-badge{display:inline-block;padding:.28rem .56rem;border-radius:999px;font-size:.78rem;font-weight:700;white-space:nowrap}
    .badge-red{background:#FEE2E2;color:#A43131}
    .badge-orange{background:#FFF1DE;color:#B86507}
    .badge-yellow{background:#FEF3C7;color:#9A6700}
    .badge-blue{background:#E7F0FF;color:#2541A1}
    .badge-green{background:#DCFCE7;color:#177245}
    .govmt-table-foot{padding:.65rem 1rem .9rem 1rem;font-size:.8rem;color:#667085;background:#fff}
    </style>
    """
    header_html = ""
    if titulo or subtitulo:
        header_html = f"<div class='govmt-table-head'>" + (f"<div class='govmt-table-title'>{html.escape(titulo)}</div>" if titulo else "") + (f"<div class='govmt-table-subtitle'>{html.escape(subtitulo)}</div>" if subtitulo else "") + "</div>"
    headers = ''.join(f"<th>{html.escape(str(c))}</th>" for c in display.columns)
    body_rows = []
    for _, row in display.iterrows():
        tds = []
        for col, value in row.items():
            sval = "-" if pd.isna(value) else str(value)
            is_num = bool(re.search(r"^-?[\d\.]+(?:,\d+)?(?:\s?(?:km|%))?$", sval))
            content = _badge_html(sval, str(col))
            cls = 'num' if is_num and 'badge' not in content else ''
            tds.append(f"<td class='{cls}'>{content}</td>")
        body_rows.append("<tr>" + ''.join(tds) + "</tr>")
    foot = ""
    if len(df) > len(display):
        foot = f"<div class='govmt-table-foot'>Exibindo {len(display)} de {len(df)} registros nesta visualização.</div>"
    html_table = css + f"<div class='govmt-table-wrap'>{header_html}<div class='govmt-table-scroll'><table class='govmt-html-table'><thead><tr>{headers}</tr></thead><tbody>{''.join(body_rows)}</tbody></table></div>{foot}</div>"
    st.markdown(html_table, unsafe_allow_html=True)


def aplicar_tema_plotly(fig: go.Figure, title: str | None = None) -> go.Figure:
    if fig is None:
        return fig
    if title:
        fig.update_layout(title=title)
    fig.update_layout(
        template='plotly_white',
        paper_bgcolor='rgba(255,255,255,0)',
        plot_bgcolor='#FFFFFF',
        font=dict(family='Calibri, Arial, sans-serif', color=GOVMT_COLORS['text'], size=13),
        title=dict(font=dict(size=18, color=GOVMT_COLORS['blue_900'], family='Calibri, Arial, sans-serif'), x=0.01, xanchor='left'),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1, bgcolor='rgba(255,255,255,.72)', bordercolor=GOVMT_COLORS['line_soft'], borderwidth=1, font=dict(size=12)),
        margin=dict(l=20, r=20, t=58, b=26),
        colorway=GOVMT_SEQUENCE,
        hoverlabel=dict(bgcolor='#FFFFFF', bordercolor=GOVMT_COLORS['blue_800'], font_size=12, font_family='Calibri, Arial, sans-serif'),
    )
    fig.update_xaxes(showgrid=True, gridcolor=GOVMT_COLORS['line_soft'], zeroline=False, linecolor=GOVMT_COLORS['line'], tickfont=dict(color=GOVMT_COLORS['muted']), title_font=dict(color=GOVMT_COLORS['muted']))
    fig.update_yaxes(showgrid=False, zeroline=False, linecolor=GOVMT_COLORS['line'], tickfont=dict(color=GOVMT_COLORS['muted']), title_font=dict(color=GOVMT_COLORS['muted']))
    for i, trace in enumerate(fig.data):
        if hasattr(trace, 'marker'):
            try:
                if getattr(trace.marker, 'color', None) is None:
                    trace.marker.color = GOVMT_SEQUENCE[i % len(GOVMT_SEQUENCE)]
                trace.marker.line = dict(width=0.8, color='#FFFFFF')
            except Exception:
                pass
        if getattr(trace, 'type', None) == 'bar':
            try:
                trace.marker.line = dict(width=0)
                trace.textfont = dict(color='#FFFFFF', size=12)
            except Exception:
                pass
    return fig


def _shorten_axis_label(label: Any, max_len: int = 34) -> str:
    text = str(label)
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def aplicar_estilo_executivo_plotly(fig: go.Figure, title: str | None = None, subtitle: str | None = None) -> go.Figure:
    """Tema executivo GovMT para gráficos Plotly.

    Mantém os dados originais e melhora apenas hierarquia visual, tipografia,
    margens, legenda, rótulos e cores.
    """
    fig = aplicar_tema_plotly(fig, title=title)

    # Detecta tipos para ajustar legenda/margens conforme o gráfico.
    trace_types = {getattr(trace, "type", "") for trace in fig.data}
    has_pie = "pie" in trace_types
    has_map = "scattermapbox" in trace_types or getattr(fig.layout, "mapbox", None) is not None

    final_title = title or ""
    if subtitle:
        final_title = (
            f"{final_title}<br>"
            f"<span style='font-size:12px;color:{GOVMT_COLORS['muted']};font-weight:400'>{subtitle}</span>"
        )
    if final_title:
        fig.update_layout(title=final_title)

    fig.update_layout(
        height=max(410, fig.layout.height or 410),
        bargap=0.34,
        uniformtext_minsize=10,
        uniformtext_mode="hide",
        legend_title_text="",
        title_font_size=18,
        margin=dict(l=34, r=34, t=92 if has_pie else 76, b=50),
        title=dict(
            x=0.02,
            xanchor="left",
            y=0.98,
            yanchor="top",
            font=dict(size=18, color=GOVMT_COLORS["blue_900"], family="Calibri, Arial, sans-serif"),
        ),
    )

    # Legenda: nos donuts vai para baixo para não disputar espaço com o título.
    if has_pie:
        fig.update_layout(
            legend=dict(
                orientation="h",
                yanchor="top",
                y=-0.08,
                xanchor="center",
                x=0.5,
                bgcolor="rgba(255,255,255,0)",
                borderwidth=0,
                font=dict(size=11, color=GOVMT_COLORS["text"]),
            ),
            margin=dict(l=28, r=28, t=96, b=92),
        )
    else:
        fig.update_layout(
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.01,
                xanchor="right",
                x=1,
                bgcolor="rgba(255,255,255,.78)",
                bordercolor=GOVMT_COLORS["line_soft"],
                borderwidth=1,
                font=dict(size=11, color=GOVMT_COLORS["text"]),
            )
        )

    if has_map:
        fig.update_layout(
            margin=dict(l=8, r=8, t=58 if final_title else 8, b=8),
            height=max(430, fig.layout.height or 430),
        )
        # Renomeia barra de cor quando o gráfico usa score técnico.
        try:
            fig.update_coloraxes(
                colorbar=dict(
                    title=dict(text="Score integrado", font=dict(size=12, color=GOVMT_COLORS["muted"])),
                    tickfont=dict(size=10, color=GOVMT_COLORS["muted"]),
                    thickness=12,
                    len=0.72,
                    outlinewidth=0,
                )
            )
        except Exception:
            pass

    fig.update_xaxes(
        showline=False,
        mirror=False,
        gridwidth=1,
        gridcolor=GOVMT_COLORS["line_soft"],
        tickfont=dict(size=11, color=GOVMT_COLORS["muted"]),
        title_font=dict(size=12, color=GOVMT_COLORS["muted"]),
        automargin=True,
    )
    fig.update_yaxes(
        showline=False,
        mirror=False,
        tickfont=dict(size=11, color=GOVMT_COLORS["muted"]),
        title_font=dict(size=12, color=GOVMT_COLORS["muted"]),
        automargin=True,
    )

    for idx, trace in enumerate(fig.data):
        trace_type = getattr(trace, "type", "")
        if trace_type == "bar":
            try:
                trace.marker.line = dict(width=0)
                trace.opacity = 0.96
                if getattr(trace, "orientation", None) == "h":
                    trace.textposition = "outside"
                    trace.cliponaxis = False
                else:
                    trace.textposition = "outside"
                if getattr(trace, "text", None) is not None:
                    trace.textfont = dict(size=11, color=GOVMT_COLORS["blue_900"])
                if getattr(trace.marker, "color", None) is None:
                    trace.marker.color = GOVMT_SEQUENCE[idx % len(GOVMT_SEQUENCE)]
            except Exception:
                pass
        elif trace_type in {"scatter", "scattermapbox"}:
            try:
                trace.marker.size = getattr(trace.marker, "size", None) or 11
                trace.marker.opacity = 0.82
                trace.marker.line = dict(width=1.1, color="#FFFFFF")
            except Exception:
                pass
        elif trace_type == "pie":
            try:
                trace.marker = dict(colors=GOVMT_SEQUENCE, line=dict(color="#FFFFFF", width=3))
                trace.textinfo = "percent"
                trace.textposition = "inside"
                trace.hovertemplate = "%{label}<br>%{value}<br>%{percent}<extra></extra>"
                trace.insidetextorientation = "radial"
                trace.hole = getattr(trace, "hole", None) if getattr(trace, "hole", None) else 0.54
                trace.sort = False
                trace.pull = [0.01] * len(trace.labels) if getattr(trace, "labels", None) is not None else None
            except Exception:
                pass

    return fig

def grafico_barra_executiva(
    df: pd.DataFrame,
    x: str,
    y: str,
    titulo: str,
    subtitulo: str | None = None,
    cor: str | None = None,
    texto: str | None = None,
    horizontal: bool = True,
    top_n: int | None = None,
) -> go.Figure:
    import plotly.express as px

    data = _ensure_dataframe(df).copy()
    if top_n:
        data = data.head(top_n)
    if horizontal:
        fig = px.bar(data, x=x, y=y, color=cor, text=texto or x, orientation="h")
        fig.update_layout(yaxis=dict(autorange="reversed"))
    else:
        fig = px.bar(data, x=x, y=y, color=cor, text=texto or y)
    return aplicar_estilo_executivo_plotly(fig, titulo, subtitulo)


def grafico_donut_executivo(
    df: pd.DataFrame,
    names: str,
    values: str,
    titulo: str,
    subtitulo: str | None = None,
) -> go.Figure:
    import plotly.express as px

    data = _ensure_dataframe(df).copy()
    fig = px.pie(data, names=names, values=values, hole=0.52)
    return aplicar_estilo_executivo_plotly(fig, titulo, subtitulo)


def grafico_scatter_executivo(
    df: pd.DataFrame,
    x: str,
    y: str,
    titulo: str,
    subtitulo: str | None = None,
    cor: str | None = None,
    tamanho: str | None = None,
    hover_data: list[str] | None = None,
) -> go.Figure:
    import plotly.express as px

    data = _ensure_dataframe(df).copy()
    fig = px.scatter(data, x=x, y=y, color=cor, size=tamanho, hover_data=hover_data)
    return aplicar_estilo_executivo_plotly(fig, titulo, subtitulo)
