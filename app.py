from __future__ import annotations

import importlib.util
import sys
import traceback
import sqlite3
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st


# HOTFIX V49 - compatibilidade Streamlit Cloud
# Garante que o banco principal seja encontrado quando o arquivo foi enviado como .db.
try:
    from pathlib import Path as _APSPath
    _db_sem_ext = _APSPath("data/aps_inteligencia")
    _db_com_ext = _APSPath("data/aps_inteligencia.db")
    if _db_com_ext.exists() and not _db_sem_ext.exists():
        try:
            _db_sem_ext.symlink_to(_db_com_ext.name)
        except Exception:
            try:
                _db_sem_ext.write_bytes(_db_com_ext.read_bytes())
            except Exception:
                pass
except Exception:
    pass
# FIM HOTFIX V49

try:
    from services.auditoria_service import registrar_evento, garantir_tabela_auditoria
except Exception:
    def registrar_evento(**kwargs):
        return None
    def garantir_tabela_auditoria():
        return None



# ============================================================
# CONFIGURAÇÃO GERAL
# ============================================================

APP_TITLE = "Sistema de Inteligência Territorial APS"
APP_SUBTITLE = "Secretaria de Estado de Saúde de Mato Grosso — SES/MT"


st.set_page_config(
    page_title=APP_TITLE,
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# ESTILO VISUAL GLOBAL
# ============================================================

def aplicar_estilo_global():
    st.markdown(
        """
        <style>
            .main {
                background: #EFF8FB;
            }

            [data-testid="stSidebar"] {
                background: linear-gradient(180deg, #0B1F3A 0%, #102A43 100%);
            }

            [data-testid="stSidebar"] * {
                color: #FFFFFF;
            }

            .block-container {
                padding-top: 3.8rem !important;
                padding-bottom: 2rem;
                max-width: 100%;
            }

            header[data-testid="stHeader"] {
                background: rgba(255,255,255,0.92);
                backdrop-filter: blur(6px);
            }

            [data-testid="stAppViewContainer"] > .main {
                padding-top: 0.4rem;
            }

            .element-container:first-child {
                margin-top: 0.25rem;
            }

            h1, h2, h3 {
                color: #102A43;
                font-weight: 800;
            }

            .info-box {
                background: #EAF2FF;
                border-left: 5px solid #2F80ED;
                padding: 1rem;
                border-radius: 12px;
                color: #1E276F;
                margin: 0.75rem 0 1rem 0;
                line-height: 1.45;
            }

            .success-box {
                background: #ECFDF3;
                border-left: 5px solid #12B76A;
                padding: 1rem;
                border-radius: 12px;
                color: #05603A;
                margin: 0.75rem 0 1rem 0;
                line-height: 1.45;
            }

            .warning-box {
                background: #FFF7E6;
                border-left: 5px solid #F59E0B;
                padding: 1rem;
                border-radius: 12px;
                color: #7A4E00;
                margin: 0.75rem 0 1rem 0;
                line-height: 1.45;
            }

            div[data-testid="stMetric"] {
                background: #FFFFFF;
                border: 1px solid #D9E4F2;
                border-radius: 16px;
                padding: 1rem;
                box-shadow: 0 4px 16px rgba(16, 24, 40, 0.05);
            }

            div[data-testid="stMetricLabel"] {
                color: #667085;
                font-size: 0.78rem;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 0.04em;
            }

            div[data-testid="stMetricValue"] {
                color: #1E276F;
                font-weight: 900;
            }

            .stTabs [data-baseweb="tab-list"] {
                gap: 0.35rem;
                background: #FFFFFF;
                padding: 0.45rem;
                border-radius: 14px;
                border: 1px solid #D9E4F2;
            }

            .stTabs [data-baseweb="tab"] {
                border-radius: 10px;
                padding: 0.45rem 0.9rem;
                font-weight: 700;
            }

            .stTabs [aria-selected="true"] {
                background: #1565C0;
                color: #FFFFFF;
            }

            .app-header {
                background: linear-gradient(135deg, #0B3C7D 0%, #1565C0 52%, #41B6E6 100%);
                color: #FFFFFF;
                border-radius: 22px;
                padding: 1.2rem 1.4rem;
                margin-bottom: 1rem;
                box-shadow: 0 10px 32px rgba(21, 101, 192, 0.22);
            }

            .app-header-title {
                font-size: 1.7rem;
                font-weight: 900;
                margin-bottom: 0.25rem;
            }

            .app-header-subtitle {
                font-size: 0.95rem;
                opacity: 0.94;
                line-height: 1.45;
            }

            .home-hero {
                background: linear-gradient(135deg, #061A33 0%, #0B3C7D 48%, #1E88E5 100%);
                color: #FFFFFF;
                border-radius: 26px;
                padding: 1.7rem 1.8rem;
                margin-bottom: 1rem;
                box-shadow: 0 16px 42px rgba(11,60,125,.26);
            }
            .home-hero h1 {color:#FFFFFF;margin:0;font-size:2rem;font-weight:950;letter-spacing:-.02em;}
            .home-hero p {margin:.7rem 0 0;opacity:.96;line-height:1.55;font-size:1rem;max-width:1100px;}
            .home-chip{display:inline-block;padding:.35rem .7rem;border-radius:999px;background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.22);font-weight:800;font-size:.78rem;margin:.3rem .35rem .1rem 0;color:#fff;}
            .home-card{background:#FFFFFF;border:1px solid #D9E4F2;border-radius:20px;padding:1rem;box-shadow:0 8px 24px rgba(16,24,40,.06);height:100%;}
            .home-card .emoji{font-size:1.7rem}.home-card h3{margin:.25rem 0 .35rem;color:#102A43;font-size:1.05rem}.home-card p{color:#475467;font-size:.9rem;line-height:1.42;margin:0}
            .method-box{background:#FFF7E6;border-left:6px solid #F59E0B;border-radius:18px;padding:1rem 1.1rem;color:#7A4E00;line-height:1.5;margin:1rem 0;box-shadow:0 8px 22px rgba(245,158,11,.08)}
            .validation-box{background:#F8FAFC;border:1px solid #CBD5E1;border-radius:20px;padding:1rem;margin:.75rem 0;box-shadow:0 8px 22px rgba(15,23,42,.05)}
            .status-pill{display:inline-block;padding:.35rem .65rem;border-radius:999px;font-weight:900;font-size:.78rem;margin:.1rem .25rem .1rem 0;}
            .pill-ok{background:#D1FADF;color:#05603A}.pill-warn{background:#FEF0C7;color:#B54708}.pill-bad{background:#FEE4E2;color:#B42318}.pill-info{background:#EAF2FF;color:#1E276F}
            .login-wrap{min-height:72vh;display:flex;align-items:center;justify-content:center;padding:1rem 1rem 2rem 1rem;background:radial-gradient(circle at 20% 10%,#E0F2FE 0,#F8FAFC 38%,#EFF8FB 100%)}
            .login-card{width:min(1100px,100%);display:grid;grid-template-columns:1.05fr .95fr;gap:0;background:#fff;border:1px solid #D9E4F2;border-radius:30px;box-shadow:0 22px 70px rgba(11,60,125,.20);overflow:hidden}
            .login-left{padding:2rem 2.2rem;background:linear-gradient(135deg,#061A33 0%,#0B3C7D 55%,#1E88E5 100%);color:#fff}
            .login-left h1{color:#fff;margin:.6rem 0;font-size:2rem;line-height:1.08}.login-left p{opacity:.95;line-height:1.55;font-size:.98rem}
            .login-chip{display:inline-block;padding:.35rem .65rem;border-radius:999px;background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.22);font-weight:850;font-size:.78rem;margin:.15rem .25rem .15rem 0;color:#fff}
            .login-right{padding:2rem 2.2rem;background:#FFFFFF}.login-right h2{margin:0 0 .25rem;color:#102A43;font-weight:950}.login-muted{color:#667085;font-size:.92rem;line-height:1.45;margin-bottom:1rem}
            .gota-tech{width:170px;height:210px;margin:1rem auto;position:relative;filter:drop-shadow(0 14px 18px rgba(0,0,0,.22))}
            .gota-body{position:absolute;left:25px;top:0;width:120px;height:170px;background:linear-gradient(160deg,#fff 0%,#EAF6FF 65%,#BFE8FF 100%);border-radius:65% 65% 58% 58%;transform:rotate(45deg);border:2px solid rgba(255,255,255,.75)}
            .gota-face{position:absolute;left:45px;top:62px;width:80px;text-align:center;color:#0B3C7D;font-weight:950}.gota-face:before{content:'•  •';font-size:30px;letter-spacing:16px}.gota-smile{font-size:24px;margin-top:-8px}.gota-plus{position:absolute;left:64px;top:113px;color:#1565C0;font-size:34px;font-weight:950}.gota-hud{position:absolute;border:2px solid #41B6E6;border-radius:50%;opacity:.9}.hud1{width:190px;height:190px;left:-10px;top:5px;border-left-color:transparent}.hud2{width:135px;height:135px;left:18px;top:34px;border-right-color:transparent}.gota-label{text-align:center;color:#fff;font-weight:900;font-size:.85rem;margin-top:.2rem;opacity:.95}
            .login-page-override .block-container{padding-top:1rem!important;}
            .login-top-note{background:#EAF2FF;border:1px solid #D9E4F2;border-radius:18px;padding:.85rem 1rem;color:#1E276F;margin-bottom:1rem;font-weight:700;}
            .login-compact-left{min-height:560px;border-radius:28px;padding:2.2rem 2.4rem;background:linear-gradient(135deg,#061A33 0%,#0B3C7D 52%,#1E88E5 100%);color:#fff;box-shadow:0 18px 54px rgba(11,60,125,.22);display:flex;flex-direction:column;justify-content:space-between;}
            .login-compact-left h1{color:#fff;margin:.8rem 0 .8rem;font-size:2.15rem;line-height:1.06;font-weight:950;letter-spacing:-.025em;}
            .login-compact-left p{opacity:.96;line-height:1.58;font-size:1rem;max-width:760px;}
            .login-compact-right{border:1px solid #D9E4F2;border-radius:28px;padding:2rem 2.2rem;background:#fff;box-shadow:0 18px 54px rgba(16,24,40,.10);}
            .login-compact-right h2{font-size:2rem;margin:0 0 .4rem;color:#102A43;font-weight:950;letter-spacing:-.02em;}
            .login-footer-note{font-size:.8rem;color:#D9EAF7;font-weight:800;margin-top:.8rem;text-align:center;}
            .login-safe-note{background:#F8FAFC;border:1px solid #E4E7EC;border-radius:14px;padding:.85rem 1rem;color:#475467;font-size:.86rem;line-height:1.4;margin-top:.8rem;}
            @media(max-width:900px){.login-card{grid-template-columns:1fr}.login-left{display:none}.login-compact-left{min-height:auto}.login-compact-right{min-height:auto}}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# IMPORTAÇÃO SEGURA DOS MÓDULOS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
UI_DIR = BASE_DIR / "ui"



DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "aps_inteligencia.db"
USERS_PATH = DATA_DIR / "usuarios_teste.json"


# ============================================================
# AUTENTICAÇÃO SIMPLES PARA DEMONSTRAÇÃO/TESTE
# ============================================================


def _hash_senha(senha: str, salt: str = "aps_ses_mt_v43") -> str:
    return hashlib.sha256(f"{salt}:{senha}".encode("utf-8")).hexdigest()


def _usuarios_padrao() -> list[dict]:
    return [
        {
            "nome": "Administrador SES/MT",
            "login": "admin",
            "senha_hash": _hash_senha("apsmt2026"),
            "perfil": "Administrador",
            "ativo": True,
            "criado_em": datetime.now().isoformat(timespec="seconds"),
        }
    ]


def _carregar_usuarios() -> list[dict]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not USERS_PATH.exists():
        USERS_PATH.write_text(json.dumps(_usuarios_padrao(), ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        data = json.loads(USERS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else _usuarios_padrao()
    except Exception:
        return _usuarios_padrao()


def _salvar_usuarios(usuarios: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    USERS_PATH.write_text(json.dumps(usuarios, ensure_ascii=False, indent=2), encoding="utf-8")


def _validar_login(login: str, senha: str) -> dict | None:
    login_norm = str(login or "").strip().lower()
    senha_hash = _hash_senha(senha or "")
    for u in _carregar_usuarios():
        if str(u.get("login", "")).strip().lower() == login_norm and u.get("senha_hash") == senha_hash and bool(u.get("ativo", True)):
            return u
    return None


def _usuario_atual() -> dict | None:
    return st.session_state.get("usuario_logado")


def _logout():
    usuario = _usuario_atual() or {}
    registrar_evento(
        usuario_login=usuario.get("login"), usuario_nome=usuario.get("nome"), perfil=usuario.get("perfil"),
        modulo="autenticacao", acao="logout", status="sucesso"
    )
    st.session_state.pop("usuario_logado", None)
    st.rerun()


def render_login() -> bool:
    """Retorna True quando autenticado."""
    if _usuario_atual():
        return True

    aplicar_estilo_global()
    st.markdown(
        """
        <style>
            .block-container {padding-top: 1.05rem !important; max-width: 1320px !important;}
            header[data-testid="stHeader"] {background: rgba(255,255,255,0.86) !important;}
        </style>
        """,
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns([1.05, .95], gap="large")

    with col1:
        st.markdown(
            """
            <div class="login-compact-left">
                <div>
                    <span class="login-chip">SES/MT</span>
                    <span class="login-chip">Atenção Primária</span>
                    <span class="login-chip">Inteligência territorial</span>
                    <span class="login-chip">Vazios assistenciais</span>
                    <h1>Sistema de Inteligência Territorial da APS</h1>
                    <p>
                    Plataforma para visualizar vazios assistenciais, identificar populações potencialmente desassistidas
                    e apoiar decisões de expansão, reorganização e financiamento da Atenção Primária em Mato Grosso.
                    </p>
                </div>
                <div>
                    <div class="gota-tech">
                        <div class="gota-body"></div><div class="gota-hud hud1"></div><div class="gota-hud hud2"></div>
                        <div class="gota-face"><div class="gota-smile">⌣</div></div><div class="gota-plus">✚</div>
                    </div>
                    <div class="login-footer-note">Zé Gotinha Digital • saúde, dados e território</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col2:
        # IMPORTANTE: não envolver widgets Streamlit dentro de <div> HTML aberto.
        # Isso criava um card branco vazio acima do formulário em algumas versões do Streamlit.
        st.markdown("""
        <div style="height:1.2rem"></div>
        <div style="background:#fff;border:1px solid #D9E4F2;border-radius:26px;padding:1.8rem 2rem;box-shadow:0 18px 54px rgba(16,24,40,.10);">
          <h2 style="margin:0 0 .35rem;color:#102A43;font-weight:950;font-size:2rem;letter-spacing:-.02em;">Entrar no sistema</h2>
          <p style="color:#667085;font-size:.92rem;line-height:1.45;margin:0 0 1rem;">Acesso restrito para testes internos, validação técnica e demonstração institucional.</p>
        </div>
        """, unsafe_allow_html=True)
        with st.form("form_login"):
            login = st.text_input("Usuário", placeholder="Digite seu usuário")
            senha = st.text_input("Senha", type="password", placeholder="Digite sua senha")
            entrar = st.form_submit_button("Entrar", use_container_width=True)
        if entrar:
            u = _validar_login(login, senha)
            if u:
                st.session_state["usuario_logado"] = {k: v for k, v in u.items() if k != "senha_hash"}
                registrar_evento(
                    usuario_login=u.get("login"), usuario_nome=u.get("nome"), perfil=u.get("perfil"),
                    modulo="autenticacao", acao="login", status="sucesso"
                )
                st.success("Login realizado com sucesso.")
                st.rerun()
            else:
                registrar_evento(
                    usuario_login=str(login or "").strip(), usuario_nome=None, perfil=None,
                    modulo="autenticacao", acao="login_invalido", status="falha", detalhes={"motivo":"Usuário/senha inválidos ou usuário inativo"}
                )
                st.error("Usuário ou senha inválidos, ou usuário inativo.")

        st.markdown(
            """
            <div class="login-safe-note">
            <b>Ambiente de demonstração.</b> O cadastro local serve apenas para testes internos.
            Para uso oficial, recomenda-se autenticação institucional e política formal de perfis de acesso.
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.caption("O cadastro de usuários fica disponível apenas após login de administrador, no menu lateral.")

    return False



def _tabelas_banco() -> set[str]:
    if not DB_PATH.exists():
        return set()
    try:
        with sqlite3.connect(DB_PATH) as con:
            return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    except Exception:
        return set()


def _contar_linhas(tabela: str) -> int:
    if not DB_PATH.exists():
        return 0
    try:
        with sqlite3.connect(DB_PATH) as con:
            return int(con.execute(f'SELECT COUNT(*) FROM "{tabela}"').fetchone()[0])
    except Exception:
        return 0


def _contar_municipios(tabela: str) -> int:
    if not DB_PATH.exists():
        return 0
    try:
        with sqlite3.connect(DB_PATH) as con:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{tabela}")').fetchall()]
            mun_col = None
            for c in cols:
                if 'municip' in c.lower() or c.lower() in ['nm_mun','no_municipio']:
                    mun_col = c
                    break
            if not mun_col:
                return 0
            return int(con.execute(f'SELECT COUNT(DISTINCT "{mun_col}") FROM "{tabela}" WHERE "{mun_col}" IS NOT NULL').fetchone()[0])
    except Exception:
        return 0


def pendencias_validacao() -> pd.DataFrame:
    tabs = _tabelas_banco()
    itens = []

    def add(item, status, gravidade, encaminhamento):
        itens.append({
            'Item de validação': item,
            'Status': status,
            'Prioridade': gravidade,
            'Encaminhamento sugerido': encaminhamento,
        })

    if not tabs:
        add('Banco de dados local', 'Não localizado/carregado', 'Crítica', 'Verificar pasta data e arquivo aps_inteligencia.db.')
        return pd.DataFrame(itens)

    # Hospitais
    if 'geo_hospitais_retaguarda' not in tabs or _contar_linhas('geo_hospitais_retaguarda') == 0:
        add('Coordenadas de hospitais/retaguarda', 'Pendente', 'Alta', 'Validar manualmente hospitais prioritários para habilitar distância hospitalar confiável.')
    else:
        add('Coordenadas de hospitais/retaguarda', f'{_contar_linhas("geo_hospitais_retaguarda")} registros georreferenciados', 'Acompanhamento', 'Manter validação SES/município e revisar estabelecimentos estratégicos.')

    # Socio consolidado
    if 'socio_consolidado_municipal' in tabs:
        add('Base socioeducacional consolidada', f'{_contar_municipios("socio_consolidado_municipal")} municípios identificados', 'Acompanhamento', 'Usar no Diagnóstico Municipal e Georreferenciamento; revisar municípios sem preenchimento.')
    else:
        add('Base socioeducacional consolidada', 'Pendente', 'Alta', 'Gerar socio_consolidado_municipal na Central/Base ou Georreferenciamento.')

    # UBS/coordenadas / estabelecimentos
    geo_candidates = [t for t in ['geo_ubs', 'estabelecimentos_saude', 'geo_estabelecimentos_ubs'] if t in tabs]
    if geo_candidates:
        add('UBS/APS georreferenciadas', f'Base encontrada: {geo_candidates[0]}', 'Acompanhamento', 'Validar coordenadas e elegibilidade das UBS usadas no mapa de distâncias.')
    else:
        add('UBS/APS georreferenciadas', 'Não identificada', 'Alta', 'Conferir camada de estabelecimentos/UBS com latitude e longitude.')

    # Distâncias
    dist_candidates = [t for t in tabs if 'dist' in t.lower() or 'bairro' in t.lower() or 'assent' in t.lower()]
    if dist_candidates:
        add('Camadas de distância territorial', f'{len(dist_candidates)} tabela(s) candidata(s)', 'Acompanhamento', 'Validar se as distâncias são geodésicas/linha reta ou rotas reais antes de decisão final.')
    else:
        add('Camadas de distância territorial', 'Pendente', 'Alta', 'Gerar/validar mapa de distâncias para bairros, localidades e assentamentos.')

    # Histórica
    hist = False
    try:
        for t in tabs:
            with sqlite3.connect(DB_PATH) as con:
                cols = [r[1].lower() for r in con.execute(f'PRAGMA table_info("{t}")').fetchall()]
            if any(c in cols for c in ['ano','ano_referencia','competencia']) and _contar_linhas(t) > 0:
                hist = True
                break
    except Exception:
        hist = False
    if hist:
        add('Séries históricas/tendências', 'Há tabelas com ano/competência', 'Acompanhamento', 'Padronizar indicador, ano, valor e fonte para tendência confiável.')
    else:
        add('Séries históricas/tendências', 'Pouco estruturada', 'Média', 'Criar tabela histórica municipal para evolução de indicadores.')

    return pd.DataFrame(itens)


def carregar_modulo_ui(nome_modulo: str):
    """
    Carrega módulos da pasta ui a partir do arquivo real.

    Esta versão evita confundir dois problemas diferentes:
    1) arquivo ui/<modulo>.py realmente ausente;
    2) arquivo existe, mas algum import interno do módulo falhou.

    No segundo caso, o sistema mostra o traceback real em vez de dizer
    incorretamente que o módulo não existe.
    """
    caminho_modulo = UI_DIR / f"{nome_modulo}.py"

    if not caminho_modulo.exists():
        return None, f"O arquivo `{caminho_modulo}` não foi encontrado."

    nome_import = f"ui.{nome_modulo}"

    try:
        spec = importlib.util.spec_from_file_location(nome_import, caminho_modulo)
        if spec is None or spec.loader is None:
            return None, f"Não foi possível criar o carregador para `{caminho_modulo}`."

        modulo = importlib.util.module_from_spec(spec)
        sys.modules[nome_import] = modulo
        spec.loader.exec_module(modulo)
        return modulo, None

    except Exception:
        st.error(f"Erro ao carregar o módulo `ui.{nome_modulo}`.")
        with st.expander("Ver detalhe técnico do erro", expanded=True):
            st.code(traceback.format_exc())
        return None, "Erro interno ao importar o módulo. Veja o traceback acima."


def executar_modulo(nome_modulo: str):
    """
    Executa a função render() de um módulo da pasta ui.
    """
    modulo, erro = carregar_modulo_ui(nome_modulo)

    if modulo is None:
        st.warning(erro or f"O módulo `ui/{nome_modulo}.py` ainda não foi encontrado no projeto.")
        return

    if not hasattr(modulo, "render"):
        st.error(
            f"O módulo `ui/{nome_modulo}.py` foi encontrado, mas não possui a função `render()`."
        )
        return

    try:
        modulo.render()
    except Exception:
        st.error(f"Falha ao executar a página `{nome_modulo}`.")
        with st.expander("Ver traceback completo", expanded=True):
            st.code(traceback.format_exc())


# ============================================================
# PÁGINA INICIAL
# ============================================================

def render_inicio():
    st.markdown(
        """
        <div class="home-hero">
            <span class="home-chip">SES/MT</span>
            <span class="home-chip">Atenção Primária</span>
            <span class="home-chip">Inteligência territorial</span>
            <span class="home-chip">Vazios assistenciais</span>
            <h1>Sistema de Inteligência Territorial da APS</h1>
            <p>
            Visualizar vazios assistenciais, identificar populações potencialmente desassistidas
            e apoiar decisões de expansão, reorganização e financiamento da Atenção Primária em Mato Grosso.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="method-box">
        <b>Observação metodológica institucional</b><br>
        Os indicadores, rankings, mapas e classificações são instrumentos técnicos de apoio à decisão.
        Não substituem validação municipal, análise da ERS, rotas reais, capacidade física das unidades,
        pactuação regional, normativas vigentes ou critérios oficiais de financiamento.
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("### Acesso rápido aos produtos principais")
    c1, c2, c3, c4 = st.columns(4)
    cards = [
        (c1, "🏙️", "Diagnóstico Municipal", "Abre o município em profundidade: território, APS, renda, educação, epidemiologia, públicos afetados e ações sugeridas."),
        (c2, "🗺️", "Mapa de Distâncias e Vazios", "Mostra visualmente quem está longe da UBS/APS, onde há vazios e quais territórios podem exigir resposta diferenciada."),
        (c3, "🧪", "Laboratório Digital APS", "Cruza dados, gera leitura estadual, ranking, motores de prioridade, tendências e insights para decisão."),
        (c4, "🗄️", "Central da Base", "Organiza importações, consolida bases, valida dados e mantém o histórico técnico do sistema."),
    ]
    for col, emoji, titulo, texto_card in cards:
        with col:
            st.markdown(f"""
            <div class="home-card">
                <div class="emoji">{emoji}</div>
                <h3>{titulo}</h3>
                <p>{texto_card}</p>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("### Leitura executiva da versão atual")
    a, b, c, d = st.columns(4)
    tabs = _tabelas_banco()
    municipios = _contar_municipios('municipios') if 'municipios' in tabs else 142
    socio = _contar_municipios('socio_consolidado_municipal') if 'socio_consolidado_municipal' in tabs else 0
    hospitais_geo = _contar_linhas('geo_hospitais_retaguarda') if 'geo_hospitais_retaguarda' in tabs else 0
    tabelas = len(tabs)
    a.metric("Municípios MT", f"{municipios or 142}")
    b.metric("Bases/tabelas no banco", f"{tabelas}")
    c.metric("Socioeducacional consolidado", f"{socio or 'pendente'}")
    d.metric("Hospitais com coordenada", f"{hospitais_geo}")

    st.markdown("### Pendências de validação para fechamento técnico")
    pend = pendencias_validacao()
    if not pend.empty:
        def _style_status(v):
            s = str(v).lower()
            if 'crítica' in s or 'alta' in s:
                return 'background-color:#FEE4E2;color:#B42318;font-weight:800;'
            if 'média' in s:
                return 'background-color:#FEF0C7;color:#B54708;font-weight:800;'
            if 'acompanhamento' in s:
                return 'background-color:#D1FADF;color:#05603A;font-weight:800;'
            return ''
        st.dataframe(
            pend.style.map(_style_status, subset=['Prioridade']),
            use_container_width=True,
            hide_index=True,
            height=300,
        )
        st.download_button(
            "Baixar pendências de validação",
            data=pend.to_csv(index=False).encode('utf-8-sig'),
            file_name="pendencias_validacao_sistema_aps.csv",
            mime="text/csv",
            use_container_width=True,
        )

    st.markdown("### Como esta versão deve ser apresentada")
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown(
            """
            <div class="validation-box">
            <span class="status-pill pill-info">1</span> <b>Comece pelo Mapa de Distâncias</b><br>
            Mostre visualmente os vazios assistenciais, as maiores distâncias e os territórios que podem estar desassistidos.
            </div>
            <div class="validation-box">
            <span class="status-pill pill-info">2</span> <b>Abra o Diagnóstico Municipal</b><br>
            Use o município prioritário para explicar o porquê: renda, educação, APS, epidemiologia, território e públicos afetados.
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col_b:
        st.markdown(
            """
            <div class="validation-box">
            <span class="status-pill pill-info">3</span> <b>Use o Laboratório Digital</b><br>
            Mostre ranking, motores de prioridade, tendências e insights integrados para orientar pactuação e decisão.
            </div>
            <div class="validation-box">
            <span class="status-pill pill-info">4</span> <b>Finalize com pendências</b><br>
            Demonstre maturidade metodológica: o sistema informa o que está validado e o que ainda precisa de confirmação técnica.
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("### Fontes atualmente consideradas")
    fontes = [
        ["IBGE Censo 2022", "População, setores, socioeducacional, renda, saneamento e equidade territorial"],
        ["MDS / VIS DATA", "CadÚnico, Bolsa Família, BPC, pobreza/extrema pobreza"],
        ["CNES / EquipesBrasil", "UBS, estabelecimentos, equipes, profissionais e INE"],
        ["DATASUS / SINASC", "Nascidos vivos e perfil materno-infantil"],
        ["DATASUS / SIM", "Mortalidade geral, infantil, causas externas, crônicas e maternas"],
        ["DATASUS / SINAN", "Tuberculose, hanseníase, violência, animais peçonhentos e demais agravos importados"],
        ["INEP / Censo Escolar", "Escolas, matrículas, ruralidade escolar e infraestrutura educacional"],
        ["Camadas territoriais", "Distâncias, bairros/localidades, assentamentos, UBS, hospitais validados e vazios assistenciais"],
    ]
    st.dataframe(fontes, use_container_width=True, hide_index=True, column_config={0: "Fonte", 1: "Uso no sistema"})


# ============================================================
# MENU / ROTEAMENTO
# ============================================================

PAGINAS = {
    "Início": {
        "tipo": "interno",
        "icone": "🏠",
        "descricao": "Apresentação geral do sistema",
    },
    "Visão Executiva": {
        "tipo": "modulo",
        "modulo": "dashboard_executivo",
        "icone": "📊",
        "descricao": "Dashboard estratégico, motor de decisão e inteligência cruzada",
    },
    "Diagnóstico Municipal": {
        "tipo": "modulo",
        "modulo": "diagnostico_municipal",
        "icone": "🏙️",
        "descricao": "Relatório municipal visual e análise integrada",
    },
    "Mapa de Distâncias e Vazios Assistenciais": {
        "tipo": "modulo",
        "modulo": "georreferenciamento",
        "icone": "🗺️",
        "descricao": "Distâncias até UBS/APS, vazios assistenciais e populações potencialmente desassistidas",
    },
    "Laboratório Digital APS": {
        "tipo": "modulo",
        "modulo": "relatorios_dashboards",
        "icone": "🧪",
        "descricao": "Inteligência integrada, dashboards, tendências, insights e relatórios",
    },
    "Central da Base de Dados": {
        "tipo": "modulo",
        "modulo": "base_dados",
        "icone": "🗄️",
        "descricao": "Importação, diagnóstico e validação das bases",
    },
}




def _render_admin_usuarios_sidebar():
    usuario = _usuario_atual() or {}
    if str(usuario.get("perfil", "")).strip().lower() != "administrador":
        return

    with st.sidebar.expander("🔐 Administração de usuários", expanded=False):
        st.caption("Área interna. Não aparece na tela pública de login.")
        usuarios = _carregar_usuarios()

        with st.form("sidebar_criar_usuario"):
            st.markdown("**Cadastrar novo acesso**")
            nome = st.text_input("Nome", key="adm_nome_usuario")
            novo_login = st.text_input("Login", key="adm_login_usuario")
            perfil = st.selectbox(
                "Perfil",
                ["Administrador", "Equipe técnica SES", "Usuário demonstração"],
                key="adm_perfil_usuario",
            )
            senha = st.text_input("Senha provisória", type="password", key="adm_senha_usuario")
            criar = st.form_submit_button("Criar usuário", use_container_width=True)

        if criar:
            login_norm = str(novo_login or "").strip().lower()
            if not login_norm or not senha:
                st.warning("Informe login e senha provisória.")
            elif any(str(u.get("login", "")).strip().lower() == login_norm for u in usuarios):
                st.warning("Já existe usuário com esse login.")
            else:
                usuarios.append({
                    "nome": nome.strip() or novo_login.strip(),
                    "login": novo_login.strip(),
                    "senha_hash": _hash_senha(senha),
                    "perfil": perfil,
                    "ativo": True,
                    "criado_em": datetime.now().isoformat(timespec="seconds"),
                })
                _salvar_usuarios(usuarios)
                adm = _usuario_atual() or {}
                registrar_evento(
                    usuario_login=adm.get("login"), usuario_nome=adm.get("nome"), perfil=adm.get("perfil"),
                    modulo="usuarios", acao="criar_usuario", tabela_afetada="usuarios_teste.json",
                    registro_id=login_norm, valor_novo={"login": login_norm, "perfil": perfil, "ativo": True},
                    status="sucesso"
                )
                st.success("Usuário criado.")
                st.rerun()

        st.markdown("---")
        st.markdown("**Gerenciar acessos existentes**")
        usuarios = _carregar_usuarios()
        if usuarios:
            opcoes = [f"{u.get('login','')} — {u.get('nome','')}" for u in usuarios]
            escolha = st.selectbox("Selecionar usuário", opcoes, key="adm_select_usuario")
            idx = opcoes.index(escolha)
            u = usuarios[idx]

            ativo = st.checkbox("Usuário ativo", value=bool(u.get("ativo", True)), key="adm_usuario_ativo")
            novo_perfil = st.selectbox(
                "Perfil do usuário",
                ["Administrador", "Equipe técnica SES", "Usuário demonstração"],
                index=["Administrador", "Equipe técnica SES", "Usuário demonstração"].index(u.get("perfil", "Usuário demonstração")) if u.get("perfil") in ["Administrador", "Equipe técnica SES", "Usuário demonstração"] else 2,
                key="adm_usuario_perfil_editar",
            )
            nova_senha = st.text_input("Redefinir senha (opcional)", type="password", key="adm_usuario_reset_senha")
            if st.button("Salvar alterações do usuário", use_container_width=True, key="adm_salvar_usuario"):
                usuarios[idx]["ativo"] = bool(ativo)
                usuarios[idx]["perfil"] = novo_perfil
                usuarios[idx]["atualizado_em"] = datetime.now().isoformat(timespec="seconds")
                if nova_senha:
                    usuarios[idx]["senha_hash"] = _hash_senha(nova_senha)
                _salvar_usuarios(usuarios)
                adm = _usuario_atual() or {}
                registrar_evento(
                    usuario_login=adm.get("login"), usuario_nome=adm.get("nome"), perfil=adm.get("perfil"),
                    modulo="usuarios", acao="alterar_usuario", tabela_afetada="usuarios_teste.json",
                    registro_id=str(u.get("login", "")),
                    valor_novo={"perfil": novo_perfil, "ativo": bool(ativo), "senha_redefinida": bool(nova_senha)},
                    status="sucesso"
                )
                st.success("Usuário atualizado.")
                st.rerun()

            st.caption(f"Usuários cadastrados: {len(usuarios)}")

def render_sidebar() -> str:
    st.sidebar.markdown(
        f"""
        <div style="padding:0.5rem 0 1rem 0;">
            <div style="font-size:1.05rem;font-weight:900;color:#FFFFFF;line-height:1.25;">
                {APP_TITLE}
            </div>
            <div style="font-size:0.78rem;color:#D9EAF7;margin-top:0.35rem;line-height:1.35;">
                {APP_SUBTITLE}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    nomes_paginas = list(PAGINAS.keys())

    pagina = st.sidebar.radio(
        "Menu principal",
        nomes_paginas,
        format_func=lambda p: f"{PAGINAS[p]['icone']} {p}",
        label_visibility="collapsed",
    )

    usuario = _usuario_atual() or {}
    st.sidebar.markdown(
        f"""
        <div style="background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.14);border-radius:14px;padding:.65rem;margin:.2rem 0 .8rem 0;">
            <div style="font-size:.72rem;color:#D9EAF7;font-weight:800;text-transform:uppercase;">Usuário logado</div>
            <div style="font-size:.9rem;font-weight:900;color:#FFFFFF;">{usuario.get('nome','Usuário')}</div>
            <div style="font-size:.74rem;color:#D9EAF7;">{usuario.get('perfil','')}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.sidebar.button("Sair", use_container_width=True):
        _logout()

    _render_admin_usuarios_sidebar()

    st.sidebar.markdown("---")

    st.sidebar.markdown(
        f"""
        <div style="background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.14);
        border-radius:14px;padding:0.8rem;margin-bottom:1rem;">
            <div style="font-size:0.78rem;font-weight:800;color:#FFFFFF;margin-bottom:0.3rem;">
                Página selecionada
            </div>
            <div style="font-size:0.95rem;font-weight:900;color:#FFFFFF;">
                {PAGINAS[pagina]['icone']} {pagina}
            </div>
            <div style="font-size:0.78rem;color:#D9EAF7;line-height:1.35;margin-top:0.35rem;">
                {PAGINAS[pagina]['descricao']}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.sidebar.markdown(
        """
        <div style="font-size:0.72rem;color:#D9EAF7;line-height:1.35;">
        <b>Observação metodológica:</b><br>
        Os indicadores, rankings e classificações são instrumentos de apoio à decisão.
        Devem ser validados com as áreas técnicas, ERS, municípios, rotas reais, capacidade física,
        contexto local e pactuação regional.
        </div>
        """,
        unsafe_allow_html=True,
    )

    return pagina


def render_page(pagina: str):
    usuario = _usuario_atual() or {}
    registrar_evento(
        usuario_login=usuario.get("login"), usuario_nome=usuario.get("nome"), perfil=usuario.get("perfil"),
        modulo="navegacao", acao="acessar_pagina", registro_id=pagina, status="registrado"
    )
    config = PAGINAS.get(pagina)

    if not config:
        st.error("Página não encontrada.")
        return

    if config["tipo"] == "interno":
        render_inicio()
        return

    if config["tipo"] == "modulo":
        executar_modulo(config["modulo"])
        return

    st.error("Tipo de página inválido.")


# ============================================================
# MAIN
# ============================================================

def main():
    garantir_tabela_auditoria()
    if not render_login():
        return
    aplicar_estilo_global()
    pagina = render_sidebar()
    render_page(pagina)


if __name__ == "__main__":
    main()