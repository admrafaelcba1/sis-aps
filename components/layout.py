import base64
from pathlib import Path

import streamlit as st

from config.settings import APP_NAME, APP_SUBTITLE, APP_VERSION


def aplicar_estilo():
    st.markdown(
        """
        <style>
        :root {
            --ses-blue-900: #062E4A;
            --ses-blue-800: #073B5F;
            --ses-blue-700: #0B5278;
            --ses-blue-600: #0F6B93;
            --ses-cyan-500: #21A1B7;
            --ses-cyan-100: #E9F8FB;
            --ses-bg: #F4F8FB;
            --ses-card: #FFFFFF;
            --ses-border: #D8E9F0;
            --ses-border-soft: #E8F2F6;
            --ses-text: #17324D;
            --ses-muted: #5C7184;
            --ses-green: #218A5A;
            --ses-orange: #D88910;
            --ses-red: #B8443D;
        }

        html, body, [data-testid="stAppViewContainer"] {
            background:
                radial-gradient(circle at 4% 8%, rgba(33,161,183,.10), transparent 30%),
                radial-gradient(circle at 96% 16%, rgba(15,107,147,.10), transparent 32%),
                linear-gradient(180deg, #F7FBFD 0%, #F3F8FB 55%, #EEF6FA 100%);
            color: var(--ses-text);
        }

        .main .block-container {
            padding-top: 1.05rem;
            padding-bottom: 3rem;
            max-width: 1500px;
        }

        .hero {
            position: relative;
            overflow: hidden;
            background:
                radial-gradient(circle at 92% 18%, rgba(255,255,255,.28), transparent 26%),
                radial-gradient(circle at 16% 120%, rgba(33,161,183,.42), transparent 36%),
                linear-gradient(135deg, #062E4A 0%, #073B5F 40%, #0F6B93 76%, #21A1B7 100%);
            color: white;
            padding: 34px 38px 32px;
            border-radius: 30px;
            box-shadow: 0 18px 44px rgba(7,59,95,.24);
            margin-bottom: 24px;
            border: 1px solid rgba(255,255,255,.20);
        }

        .hero:after {
            content: "";
            position: absolute;
            right: -80px;
            top: -90px;
            width: 260px;
            height: 260px;
            border-radius: 50%;
            border: 34px solid rgba(255,255,255,.08);
        }



        .hero-brand-row {
            position: relative;
            z-index: 2;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 20px;
            margin-bottom: 18px;
        }

        .hero-logo {
            display: block;
            max-height: 68px;
            max-width: 520px;
            background: #FFFFFF;
            border: 1px solid rgba(255,255,255,.65);
            border-radius: 0;
            padding: 8px 12px;
            box-shadow: 0 10px 22px rgba(0,0,0,.12);
        }

        @media (max-width: 900px) {
            .hero-brand-row { flex-direction: column; align-items: flex-start; }
            .hero-logo { max-width: 100%; height: auto; }
        }
        .hero h1 {
            margin: 0;
            font-size: 38px;
            line-height: 1.05;
            font-weight: 900;
            letter-spacing: -.04em;
            max-width: 1080px;
        }

        .hero p {
            margin: 12px 0 0;
            font-size: 16px;
            opacity: .96;
            max-width: 1180px;
            line-height: 1.5;
        }

        .hero .version-tag {
            display: inline-block;
            margin-top: 16px;
            padding: 7px 12px;
            border-radius: 999px;
            background: rgba(255,255,255,.16);
            border: 1px solid rgba(255,255,255,.24);
            font-size: 12px;
            font-weight: 750;
            opacity: .98;
        }

        .section-title {
            font-size: 1.38rem;
            font-weight: 880;
            color: var(--ses-text);
            margin: 1.3rem 0 .38rem;
            letter-spacing: -.015em;
        }

        .section-subtitle {
            color: var(--ses-muted);
            font-size: .99rem;
            margin-bottom: 1.05rem;
            line-height: 1.55;
        }

        .pill, .status-pill {
            display: inline-block;
            padding: 6px 11px;
            border-radius: 999px;
            background: #EAF6FA;
            color: #075070;
            font-weight: 780;
            font-size: 12px;
            margin: 0 6px 7px 0;
            border: 1px solid #D5EBF2;
            letter-spacing: .01em;
        }

        .status-pill.good { background: #EFFAF5; color: #145C3D; border-color: #C9EBD9; }
        .status-pill.warn { background: #FFF8EA; color: #6D4700; border-color: #F3D59A; }
        .status-pill.info { background: #EEF7FF; color: #0A4F7A; border-color: #CFE7F7; }

        .info-box, .warn-box, .success-box, .decision-box {
            background: #FFFFFF;
            border: 1px solid var(--ses-border);
            padding: 15px 17px;
            border-radius: 18px;
            margin: 11px 0;
            color: var(--ses-text);
            box-shadow: 0 8px 24px rgba(7,59,95,.055);
            line-height: 1.55;
        }

        .info-box { border-left: 5px solid var(--ses-blue-600); background: #F8FCFE; }
        .warn-box { border-left: 5px solid var(--ses-orange); background: #FFF9EE; color: #4B340A; }
        .success-box { border-left: 5px solid var(--ses-green); background: #F2FBF7; color: #123B2A; }
        .decision-box { border-left: 5px solid var(--ses-cyan-500); background: linear-gradient(180deg, #FFFFFF 0%, #F8FCFE 100%); }

        .nav-card, .analysis-card {
            background: var(--ses-card);
            border: 1px solid var(--ses-border-soft);
            border-radius: 24px;
            padding: 21px 21px 19px;
            min-height: 190px;
            box-shadow: 0 12px 28px rgba(7,59,95,.08);
            margin-bottom: 13px;
            transition: transform .16s ease, box-shadow .16s ease, border-color .16s ease;
        }

        .nav-card:hover, .analysis-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 18px 36px rgba(7,59,95,.12);
            border-color: #BFDDE8;
        }

        .nav-card .icon, .analysis-card .icon {
            font-size: 28px;
            margin-bottom: 9px;
        }

        .nav-card h3, .analysis-card h3 {
            margin: 0 0 8px;
            color: var(--ses-text);
            font-size: 1.08rem;
            font-weight: 880;
            letter-spacing: -.015em;
        }

        .nav-card p, .analysis-card p {
            margin: 0;
            color: var(--ses-muted);
            font-size: .94rem;
            line-height: 1.48;
        }

        .analysis-step {
            background: #FFFFFF;
            border: 1px solid var(--ses-border-soft);
            border-radius: 20px;
            padding: 16px 17px;
            min-height: 132px;
            box-shadow: 0 8px 22px rgba(7,59,95,.055);
            margin-bottom: 12px;
        }

        .analysis-step .step-number {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 30px;
            height: 30px;
            border-radius: 999px;
            background: linear-gradient(135deg, var(--ses-blue-700), var(--ses-cyan-500));
            color: #FFFFFF;
            font-weight: 900;
            font-size: 13px;
            margin-bottom: 9px;
        }

        .analysis-step h4 {
            margin: 0 0 7px;
            font-size: 1rem;
            color: var(--ses-text);
            font-weight: 850;
        }

        .analysis-step p {
            margin: 0;
            color: var(--ses-muted);
            font-size: .91rem;
            line-height: 1.45;
        }

        .mini-note {
            color: var(--ses-muted);
            font-size: .88rem;
            line-height: 1.45;
        }

        [data-testid="stMetric"] {
            background: #FFFFFF;
            border: 1px solid #E0EEF4;
            border-radius: 20px;
            padding: 15px 17px;
            box-shadow: 0 9px 24px rgba(7,59,95,.065);
        }

        [data-testid="stMetricValue"] {
            font-size: 30px;
            font-weight: 900;
            color: var(--ses-blue-800);
        }

        [data-testid="stMetricLabel"] {
            color: var(--ses-muted);
            font-weight: 750;
        }

        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #F8FCFE 0%, #EEF7FA 100%);
            border-right: 1px solid #D8E9F0;
        }

        section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
            line-height: 1.45;
        }

        section[data-testid="stSidebar"] .stRadio label,
        section[data-testid="stSidebar"] .stSelectbox label,
        section[data-testid="stSidebar"] .stCheckbox label {
            color: var(--ses-text);
            font-weight: 700;
        }

        div.stButton > button {
            border-radius: 15px;
            border: 1px solid #CFE4EC;
            background: #FFFFFF;
            color: var(--ses-blue-800);
            font-weight: 800;
            min-height: 43px;
            box-shadow: 0 7px 17px rgba(7,59,95,.065);
            transition: all .16s ease;
        }

        div.stButton > button:hover {
            border-color: var(--ses-blue-600);
            color: var(--ses-blue-600);
            box-shadow: 0 10px 22px rgba(7,59,95,.10);
            transform: translateY(-1px);
        }

        .stTabs [data-baseweb="tab-list"] {
            gap: 8px;
            background: rgba(255,255,255,.72);
            border: 1px solid #E2EEF4;
            border-radius: 18px;
            padding: 7px;
            box-shadow: 0 8px 20px rgba(7,59,95,.045);
        }

        .stTabs [data-baseweb="tab"] {
            border-radius: 13px;
            padding: 8px 14px;
            color: var(--ses-muted);
            font-weight: 750;
        }

        .stTabs [aria-selected="true"] {
            background: linear-gradient(135deg, #0F6B93, #21A1B7);
            color: #FFFFFF !important;
        }

        div[data-testid="stDataFrame"], div[data-testid="stTable"] {
            border-radius: 18px;
            overflow: hidden;
            border: 1px solid #E1EEF3;
            box-shadow: 0 8px 22px rgba(7,59,95,.045);
        }

        hr {
            border-color: #E1EEF3;
            margin: 1.25rem 0;
        }

        /* GovMT — camada de acabamento institucional para tabelas e gráficos */
        :root {
            --govmt-blue: #252E7F;
            --govmt-blue-2: #29358A;
            --govmt-cyan: #1494B8;
            --govmt-bg: #F6F8FC;
            --govmt-line: #DDE3F0;
            --govmt-soft: #EEF1FF;
            --govmt-text: #1F2A44;
            --govmt-muted: #667085;
        }

        .main .block-container {
            max-width: 1540px;
        }

        h1, h2, h3, h4 {
            letter-spacing: -0.025em;
        }

        .hero {
            background:
                radial-gradient(circle at 0% 0%, rgba(20,148,184,.16), transparent 26%),
                radial-gradient(circle at 98% 14%, rgba(255,255,255,.12), transparent 24%),
                linear-gradient(135deg, #252E7F 0%, #29358A 48%, #1494B8 100%) !important;
            border-radius: 0 0 34px 34px !important;
            border-top: 5px solid #252E7F !important;
            border-left: 1px solid rgba(255,255,255,.26) !important;
            border-right: 1px solid rgba(255,255,255,.18) !important;
            box-shadow: 0 18px 46px rgba(37,46,127,.22) !important;
        }

        .hero h1 {
            font-weight: 900 !important;
            letter-spacing: -.045em !important;
        }

        .version-tag {
            background: rgba(255,255,255,.18) !important;
            border: 1px solid rgba(255,255,255,.28) !important;
        }

        [data-testid="stMetric"] {
            border-radius: 18px !important;
            border: 1px solid #E1E7F2 !important;
            background: linear-gradient(180deg, #FFFFFF 0%, #FBFCFF 100%) !important;
            box-shadow: 0 8px 22px rgba(37,46,127,.065) !important;
        }

        [data-testid="stMetricValue"] {
            color: #252E7F !important;
            letter-spacing: -.04em !important;
        }

        [data-testid="stMetricLabel"] {
            color: #5C6478 !important;
            text-transform: uppercase;
            letter-spacing: .055em;
            font-size: .76rem !important;
        }

        .stTabs [data-baseweb="tab-list"] {
            background: #FFFFFF !important;
            border: 1px solid #E1E7F2 !important;
            box-shadow: 0 8px 24px rgba(37,46,127,.05) !important;
        }

        .stTabs [aria-selected="true"] {
            background: linear-gradient(135deg, #252E7F, #1494B8) !important;
            color: #FFFFFF !important;
            box-shadow: 0 8px 18px rgba(37,46,127,.16);
        }

        /* Tabelas Streamlit / Glide */
        div[data-testid="stDataFrame"] {
            border-radius: 16px !important;
            border: 1px solid #DDE3F0 !important;
            box-shadow: 0 10px 28px rgba(37,46,127,.065) !important;
            overflow: hidden !important;
            background: #FFFFFF !important;
        }

        div[data-testid="stDataFrame"] div[role="columnheader"],
        div[data-testid="stDataFrame"] [data-testid="stDataFrameResizable"] div[role="columnheader"] {
            background: #252E7F !important;
            color: #FFFFFF !important;
            font-weight: 800 !important;
            text-transform: none !important;
            letter-spacing: .01em !important;
        }

        div[data-testid="stDataFrame"] div[role="gridcell"] {
            font-size: .88rem !important;
            color: #1F2A44 !important;
        }

        .table-title {
            margin: 18px 0 5px;
            color: #252E7F;
            font-weight: 900;
            font-size: 1.04rem;
            letter-spacing: -.015em;
        }

        .table-subtitle {
            margin: 0 0 9px;
            color: #667085;
            font-size: .91rem;
            line-height: 1.4;
        }

        /* Plotly */
        .js-plotly-plot, .plot-container {
            border-radius: 18px;
        }

        [data-testid="stPlotlyChart"] {
            background: #FFFFFF;
            border: 1px solid #E1E7F2;
            border-radius: 18px;
            padding: 10px 10px 4px;
            box-shadow: 0 10px 28px rgba(37,46,127,.055);
            margin-bottom: 14px;
        }

        .info-box, .decision-box {
            border-color: #DDE3F0 !important;
            border-left-color: #252E7F !important;
        }

        .warn-box { border-left-color: #D98A14 !important; }
        .success-box { border-left-color: #168A5B !important; }

        .grafico-explicacao {
            margin: .65rem 0 1.15rem 0;
            padding: .95rem 1.1rem;
            border-radius: 16px;
            background: linear-gradient(180deg, #FFFFFF 0%, #F8FAFF 100%);
            border: 1px solid #DDE6F5;
            box-shadow: 0 8px 20px rgba(30, 39, 111, .05);
            color: #1F2A44;
        }

        .grafico-explicacao-titulo {
            font-weight: 800;
            color: #1E276F;
            margin-bottom: .45rem;
            font-size: .98rem;
        }

        .grafico-explicacao ul {
            margin: 0;
            padding-left: 1.15rem;
        }

        .grafico-explicacao li {
            margin: .22rem 0;
            line-height: 1.42;
            color: #344054;
            font-size: .91rem;
        }

        .aviso-metodologico {
            margin: .8rem 0 1.1rem 0;
            padding: 1rem 1.1rem;
            border-left: 5px solid #1E276F;
            border-radius: 14px;
            background: #F8FAFF;
            border-top: 1px solid #DDE6F5;
            border-right: 1px solid #DDE6F5;
            border-bottom: 1px solid #DDE6F5;
            color: #1F2A44;
            box-shadow: 0 6px 18px rgba(30, 39, 111, .04);
        }

        .aviso-metodologico strong {
            color: #1E276F;
        }

        </style>
        """,
        unsafe_allow_html=True,
    )


def _logo_govmt_ses_base64() -> str:
    caminho = Path("assets/govmt_ses_horizontal.png")
    if not caminho.exists():
        return ""
    try:
        return base64.b64encode(caminho.read_bytes()).decode("utf-8")
    except Exception:
        return ""


def render_header():
    logo_b64 = _logo_govmt_ses_base64()
    logo_html = ""
    if logo_b64:
        logo_html = f'<img class="hero-logo" src="data:image/png;base64,{logo_b64}" alt="SES/MT - Governo de Mato Grosso" />'
    st.markdown(
        f"""
        <div class="hero govmt-hero">
            <div class="hero-brand-row">
                <div>{logo_html}</div>
                <span class="version-tag">Versão {APP_VERSION} · primeira fase analítica consolidada · SES/MT</span>
            </div>
            <h1>{APP_NAME}</h1>
            <p>{APP_SUBTITLE}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar_footer(show_technical: bool = False):
    modo = "técnico completo" if show_technical else "institucional"
    st.sidebar.caption(f"Modo atual: {modo}.")
    st.sidebar.caption(
        "Fase atual: análises APS consolidadas, com menu institucional, indicadores governados e áreas técnicas ocultáveis."
    )
    st.sidebar.caption("SES/MT · Inteligência Territorial APS")
