import math
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

try:
    from utils.cache_dados_aps import filtrar_equipes_ine_aps, ler_metadata_cache_aps
except Exception:
    def filtrar_equipes_ine_aps(df):
        return df

    def ler_metadata_cache_aps():
        return {}

try:
    from database import read_table
except Exception:  # pragma: no cover
    read_table = None

try:
    from data_municipios import DEFAULT_MUNICIPIOS
except Exception:  # pragma: no cover
    DEFAULT_MUNICIPIOS = []


PARAMETROS_MS_ESF = [
    {"faixa": "Até 20.000 habitantes", "limite_inferior": 0, "limite_superior": 20_000, "pessoas_por_esf": 2_000},
    {"faixa": "20.001 a 50.000 habitantes", "limite_inferior": 20_001, "limite_superior": 50_000, "pessoas_por_esf": 2_500},
    {"faixa": "50.001 a 100.000 habitantes", "limite_inferior": 50_001, "limite_superior": 100_000, "pessoas_por_esf": 2_750},
    {"faixa": "Acima de 100.000 habitantes", "limite_inferior": 100_001, "limite_superior": None, "pessoas_por_esf": 3_000},
]

CHAVES_APIS_STATUS = [
    ("Base municipal consolidada", "ubs_base_automatica_ibge"),
    ("Regiões de Saúde", "ubs_api_regioes_saude"),
    ("População municipal", "ubs_api_populacao"),
    ("Área, densidade e ruralidade", "ubs_api_area_densidade / ubs_api_urbano_rural"),
    ("Renda IBGE", "ubs_api_renda_ibge"),
    ("Alfabetização", "ubs_api_alfabetizacao_9543"),
    ("Instrução/escolaridade", "ubs_api_instrucao_10061"),
    ("Saneamento", "ubs_api_saneamento"),
    ("BPC", "ubs_api_bpc"),
    ("Deficiência/autismo", "ubs_api_deficiencia_autismo_ibge"),
    ("INEP Censo Escolar", "ubs_api_inep_censo_escolar"),
    ("INEP Educação Especial", "ubs_api_inep_educacao_especial"),
    ("SINASC", "ubs_api_sinasc_municipal"),
    ("SIM", "ubs_api_sim_municipal"),
    ("Povos tradicionais", "ubs_api_povos_tradicionais"),
    ("CNES UBS", "geo_ubs_df / ubs_api_cnes_ubs_lista"),
    ("CNES/INE Equipes", "geo_equipes_ine_df"),
    ("Setores/territórios", "geo_territorios_df"),
    ("Resultado georreferenciamento", "geo_resultado_df"),
]


def _css_dashboard_aps() -> None:
    st.markdown(
        """
        <style>
            .aps-hero {
                background: linear-gradient(135deg, #072F4F 0%, #0D5E8C 55%, #1597B8 100%);
                border-radius: 24px;
                padding: 26px 30px;
                color: #fff;
                box-shadow: 0 16px 34px rgba(7, 47, 79, .20);
                margin-bottom: 22px;
            }
            .aps-hero h2 { margin: 0; font-size: 30px; line-height: 1.15; font-weight: 850; letter-spacing: -0.03em; }
            .aps-hero p { margin: 9px 0 0 0; font-size: 15.5px; opacity: .94; max-width: 1100px; }
            .aps-card {
                background: #FFFFFF; border: 1px solid #E3EDF5; border-radius: 20px;
                padding: 18px 18px; box-shadow: 0 8px 22px rgba(15, 60, 90, .07); margin-bottom: 14px;
            }
            .aps-card h4 { margin: 0 0 8px 0; color: #0B3558; font-size: 17px; }
            .aps-card p { margin: 0; color: #36556E; font-size: 14px; }
            .aps-badge {
                display: inline-block; padding: 5px 10px; border-radius: 999px; font-size: 12px; font-weight: 750;
                margin-right: 6px; border: 1px solid #DCEAF3; background: #F3F8FC; color: #0B4D73;
            }
            .aps-note { background: #F4F9FC; border-left: 5px solid #0D5E8C; color: #17324D; border-radius: 14px; padding: 14px 16px; margin: 12px 0 18px 0; }
            .aps-alert { background: #FFF8E8; border-left: 5px solid #D68910; color: #4D3410; border-radius: 14px; padding: 14px 16px; margin: 12px 0 18px 0; }
            .aps-good { background: #EAF8F1; border-left: 5px solid #198754; color: #133B2A; border-radius: 14px; padding: 14px 16px; margin: 12px 0 18px 0; }
            .dataframe tbody tr th, .dataframe tbody tr td { font-size: 13px; }
        </style>
        """,
        unsafe_allow_html=True,
    )


# -------------------------
# Normalização e leitura
# -------------------------

def _normalizar_texto(valor: Any) -> str:
    texto = str(valor or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-z0-9]+", "_", texto).strip("_")
    return texto


def _limpar_nome_municipio(valor: Any) -> str:
    texto = str(valor or "").strip()
    texto = re.sub(r"\s+", " ", texto)
    texto = re.sub(r"\s*[-/]\s*MT\s*$", "", texto, flags=re.I).strip()
    texto = re.sub(r"\s*\(MT\)\s*$", "", texto, flags=re.I).strip()
    return texto


def _codigo_ibge(valor: Any) -> str:
    dig = re.sub(r"\D", "", str(valor or ""))
    if len(dig) >= 7 and dig.startswith("51"):
        return dig[:7]
    if len(dig) == 6 and dig.startswith("51"):
        return dig
    return ""


def _eh_codigo_mt(valor: Any) -> bool:
    return bool(_codigo_ibge(valor))


def _chave_municipio(codigo: Any = "", municipio: Any = "") -> str:
    cod = _codigo_ibge(codigo)
    if cod:
        return f"ibge_{cod}"
    nome = _normalizar_texto(_limpar_nome_municipio(municipio))
    return f"nome_{nome}" if nome else ""


def _normalizar_colunas(df: Optional[pd.DataFrame]) -> pd.DataFrame:
    if df is None or not isinstance(df, pd.DataFrame):
        return pd.DataFrame()
    out = df.copy()
    out.columns = [_normalizar_texto(c) for c in out.columns]
    return out


def _to_numero(valor: Any, padrao: float = 0.0) -> float:
    try:
        if valor is None or (isinstance(valor, float) and math.isnan(valor)):
            return padrao
        if isinstance(valor, str):
            v = valor.strip()
            if re.match(r"^-?\d{1,3}(\.\d{3})+,\d+$", v):
                v = v.replace(".", "").replace(",", ".")
            else:
                v = v.replace(",", ".")
            valor = v
        numero = pd.to_numeric(valor, errors="coerce")
        if pd.isna(numero):
            return padrao
        return float(numero)
    except Exception:
        return padrao


def _get_session_df(chave: str) -> pd.DataFrame:
    valor = st.session_state.get(chave)
    if isinstance(valor, pd.DataFrame):
        return _normalizar_colunas(valor)
    return pd.DataFrame()


def _read_table_safe(nome: str) -> pd.DataFrame:
    if read_table is None:
        return pd.DataFrame()
    try:
        return _normalizar_colunas(read_table(nome))
    except Exception:
        return pd.DataFrame()


def _primeira_coluna_existente(df: pd.DataFrame, candidatos: List[str]) -> Optional[str]:
    if df is None or df.empty:
        return None
    for c in candidatos:
        chave = _normalizar_texto(c)
        if chave in df.columns:
            return chave
    return None


def _serie_texto(df: pd.DataFrame, candidatos: List[str], padrao: str = "") -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=str)
    col = _primeira_coluna_existente(df, candidatos)
    if col is None:
        return pd.Series([padrao] * len(df), index=df.index, dtype=object)
    return df[col].fillna(padrao).astype(str)


def _serie_numero(df: pd.DataFrame, candidatos: List[str], padrao: float = 0.0) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=float)
    col = _primeira_coluna_existente(df, candidatos)
    if col is None:
        return pd.Series([padrao] * len(df), index=df.index, dtype=float)
    return pd.to_numeric(df[col].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False), errors="coerce").fillna(padrao)


def _preparar_df_municipal(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    out = df.copy()
    municipio = _serie_texto(out, ["municipio", "nome_municipio", "nome"])
    codigo = _serie_texto(out, ["codigo_ibge", "cod_ibge", "ibge", "id_municipio", "codigo"])
    out["codigo_ibge_norm"] = codigo.map(_codigo_ibge)
    out["municipio_limpo"] = municipio.map(_limpar_nome_municipio)
    out["municipio_key"] = [_chave_municipio(c, m) for c, m in zip(out["codigo_ibge_norm"], out["municipio_limpo"])]
    out = out[out["municipio_key"].astype(str).str.len() > 0].copy()
    # Se existir código, filtra MT. Se não existir, mantém para possibilitar cruzamento por nome.
    tem_algum_codigo = out["codigo_ibge_norm"].astype(str).str.len().gt(0).any()
    if tem_algum_codigo:
        out = out[(out["codigo_ibge_norm"].astype(str).str.startswith("51")) | (out["codigo_ibge_norm"].astype(str).eq(""))].copy()
    return out


def _base_referencia_default() -> pd.DataFrame:
    linhas = []
    for item in DEFAULT_MUNICIPIOS or []:
        if isinstance(item, dict):
            nome = item.get("municipio") or item.get("nome") or item.get("nome_municipio")
            regiao = item.get("regiao_saude") or item.get("regional_saude") or "Não informada"
            ers = item.get("escritorio_regional") or item.get("ers") or ""
        else:
            nome, regiao, ers = item, "Não informada", ""
        nome = _limpar_nome_municipio(nome)
        if nome:
            linhas.append({"municipio": nome, "regiao_saude": regiao or "Não informada", "escritorio_regional": ers or "", "municipio_key": _chave_municipio("", nome)})
    return pd.DataFrame(linhas).drop_duplicates("municipio_key") if linhas else pd.DataFrame()


# -------------------------
# Consolidação municipal
# -------------------------

def _base_municipal_apis() -> pd.DataFrame:
    """Consolida as APIs/base municipal em uma linha por município.

    Correção central: a chave passa a priorizar código IBGE. Isso evita que
    "Cáceres" e "Cáceres - MT" virem dois municípios diferentes, causa do
    painel mostrar 283/284 municípios.
    """
    base_auto = _preparar_df_municipal(_get_session_df("ubs_base_automatica_ibge"))

    if not base_auto.empty:
        acumulado = base_auto.sort_values("municipio_key").drop_duplicates("municipio_key", keep="first").copy()
    else:
        acumulado = _base_referencia_default()

    candidatos = [
        "ubs_api_municipios",
        "ubs_api_regioes_saude",
        "ubs_api_populacao",
        "ubs_api_area_densidade",
        "ubs_api_urbano_rural",
        "ubs_api_demografia_9515",
        "ubs_api_alfabetizacao_9543",
        "ubs_api_instrucao_10061",
        "ubs_api_renda_ibge",
        "ubs_api_saneamento",
        "ubs_api_bpc",
        "ubs_api_deficiencia_autismo_ibge",
        "ubs_api_inep_censo_escolar",
        "ubs_api_inep_educacao_especial",
        "ubs_api_sinasc_municipal",
        "ubs_api_sim_municipal",
        "ubs_api_povos_tradicionais",
    ]

    for chave in candidatos:
        df = _preparar_df_municipal(_get_session_df(chave))
        if df.empty:
            continue
        df = df.drop_duplicates("municipio_key", keep="first").copy()
        cols_para_entrar = [c for c in df.columns if c not in acumulado.columns or c == "municipio_key"]
        if "municipio_key" not in cols_para_entrar:
            cols_para_entrar = ["municipio_key"] + cols_para_entrar
        acumulado = acumulado.merge(df[cols_para_entrar], on="municipio_key", how="outer", suffixes=("", f"_{chave}"))

    if acumulado.empty:
        return acumulado

    # Campos canônicos.
    if "codigo_ibge" not in acumulado.columns:
        acumulado["codigo_ibge"] = ""
    cod_base = _serie_texto(acumulado, ["codigo_ibge", "codigo_ibge_norm", "cod_ibge", "ibge", "id_municipio", "codigo"])
    acumulado["codigo_ibge"] = cod_base.map(_codigo_ibge)

    nome_cols = ["municipio", "municipio_limpo", "nome_municipio", "nome"]
    nome = _serie_texto(acumulado, nome_cols)
    acumulado["municipio"] = nome.map(_limpar_nome_municipio)
    acumulado.loc[acumulado["municipio"].astype(str).str.strip().eq(""), "municipio"] = acumulado["municipio_key"].str.replace("nome_", "", regex=False).str.replace("_", " ").str.title()

    regiao_col = _primeira_coluna_existente(acumulado, ["regiao_saude_sus", "regiao_saude", "regiao_de_saude", "nome_regiao_saude", "regional_saude"])
    if regiao_col:
        acumulado["regiao_saude"] = acumulado[regiao_col].fillna("").astype(str).replace("", "Não informada")
    else:
        acumulado["regiao_saude"] = "Não informada"

    ers_col = _primeira_coluna_existente(acumulado, ["escritorio_regional", "ers", "regional"])
    acumulado["escritorio_regional"] = acumulado[ers_col].fillna("").astype(str) if ers_col else ""

    # Remove duplicidades remanescentes: primeiro por código, depois por nome.
    acumulado["municipio_key_nome"] = acumulado["municipio"].map(lambda x: _chave_municipio("", x))
    com_codigo = acumulado[acumulado["codigo_ibge"].astype(str).str.len() > 0].copy()
    sem_codigo = acumulado[acumulado["codigo_ibge"].astype(str).str.len() == 0].copy()
    if not com_codigo.empty:
        com_codigo = com_codigo.sort_values("municipio").drop_duplicates("codigo_ibge", keep="first")
    if not sem_codigo.empty:
        codigos_nomes = set(com_codigo["municipio_key_nome"].dropna().astype(str).tolist()) if not com_codigo.empty else set()
        sem_codigo = sem_codigo[~sem_codigo["municipio_key_nome"].isin(codigos_nomes)].drop_duplicates("municipio_key_nome", keep="first")
    acumulado = pd.concat([com_codigo, sem_codigo], ignore_index=True)

    # Reescreve chave final para garantir uma linha por município.
    acumulado["municipio_key"] = [_chave_municipio(c, m) for c, m in zip(acumulado["codigo_ibge"], acumulado["municipio"])]
    acumulado = acumulado.drop_duplicates("municipio_key", keep="first")
    return acumulado.reset_index(drop=True)


def _parametro_ms(populacao: float) -> Tuple[str, int]:
    pop = int(round(_to_numero(populacao, 0)))
    for regra in PARAMETROS_MS_ESF:
        sup = regra["limite_superior"]
        if sup is None and pop >= regra["limite_inferior"]:
            return regra["faixa"], int(regra["pessoas_por_esf"])
        if sup is not None and regra["limite_inferior"] <= pop <= sup:
            return regra["faixa"], int(regra["pessoas_por_esf"])
    return "Não classificado", 0


def _agregar_ubs(ubs: pd.DataFrame) -> pd.DataFrame:
    if ubs.empty:
        return pd.DataFrame(columns=["municipio_key", "ubs_postos_usf", "unidades_sem_coordenada", "cnes_unicos", "esf_por_ubs", "esb_por_ubs"])
    u = _preparar_df_municipal(ubs)
    if u.empty:
        return pd.DataFrame(columns=["municipio_key", "ubs_postos_usf", "unidades_sem_coordenada", "cnes_unicos", "esf_por_ubs", "esb_por_ubs"])
    u["cnes_norm"] = _serie_texto(u, ["cnes", "codigo_cnes", "co_cnes"]).str.replace(r"\.0$", "", regex=True).str.strip()
    lat = _serie_numero(u, ["latitude", "lat"], 0)
    lon = _serie_numero(u, ["longitude", "lon", "lng"], 0)
    u["coord_valida_calc"] = (lat != 0) & (lon != 0)
    esf_cols = [c for c in u.columns if c in ["qtd_esf", "quantidade_esf", "esf", "equipes_esf", "qtd_esf_vinculadas"]]
    esb_cols = [c for c in u.columns if c in ["qtd_esb", "quantidade_esb", "esb", "equipes_esb", "qtd_esb_vinculadas"]]
    ag = (
        u.groupby("municipio_key", as_index=False)
        .agg(
            ubs_postos_usf=("cnes_norm", lambda s: int(s.replace("", pd.NA).dropna().nunique()) if s.replace("", pd.NA).dropna().size else int(len(s))),
            unidades_sem_coordenada=("coord_valida_calc", lambda s: int((~s).sum())),
            cnes_unicos=("cnes_norm", lambda s: ", ".join(sorted([x for x in s.dropna().astype(str).unique() if x.strip()])[:10])),
        )
    )
    ag["esf_por_ubs"] = 0
    ag["esb_por_ubs"] = 0
    if esf_cols:
        tmp = u.groupby("municipio_key")[esf_cols[0]].apply(lambda s: pd.to_numeric(s, errors="coerce").fillna(0).sum()).reset_index(name="esf_por_ubs")
        ag = ag.drop(columns=["esf_por_ubs"]).merge(tmp, on="municipio_key", how="left")
    if esb_cols:
        tmp = u.groupby("municipio_key")[esb_cols[0]].apply(lambda s: pd.to_numeric(s, errors="coerce").fillna(0).sum()).reset_index(name="esb_por_ubs")
        ag = ag.drop(columns=["esb_por_ubs"]).merge(tmp, on="municipio_key", how="left")
    return ag


def _contar_equipes(equipes: pd.DataFrame, ubs: pd.DataFrame) -> pd.DataFrame:
    if equipes.empty:
        return pd.DataFrame(columns=["municipio_key", "esf_identificadas", "esb_identificadas", "emulti_identificadas", "eap_identificadas", "equipes_identificadas"])
    eq = filtrar_equipes_ine_aps(_normalizar_colunas(equipes))
    if eq.empty:
        return pd.DataFrame(columns=["municipio_key", "esf_identificadas", "esb_identificadas", "emulti_identificadas", "eap_identificadas", "equipes_identificadas"])

    # Se a equipe não tiver município/código, tenta buscar pelo CNES da UBS.
    municipio = _serie_texto(eq, ["municipio", "nome_municipio"])
    codigo = _serie_texto(eq, ["codigo_ibge", "cod_ibge", "ibge", "id_municipio", "codigo"])
    if (municipio.str.strip().eq("").all() or codigo.map(_codigo_ibge).eq("").all()) and not ubs.empty:
        tmp = eq.copy()
        tmp["_cnes_join"] = _serie_texto(tmp, ["cnes", "codigo_cnes", "co_cnes"]).str.replace(r"\.0$", "", regex=True).str.strip()
        u = _normalizar_colunas(ubs)
        u["_cnes_join"] = _serie_texto(u, ["cnes", "codigo_cnes", "co_cnes"]).str.replace(r"\.0$", "", regex=True).str.strip()
        u["municipio_ubs_tmp"] = _serie_texto(u, ["municipio", "nome_municipio"])
        u["codigo_ibge_ubs_tmp"] = _serie_texto(u, ["codigo_ibge", "cod_ibge", "ibge", "id_municipio", "codigo"])
        tmp = tmp.merge(u[["_cnes_join", "municipio_ubs_tmp", "codigo_ibge_ubs_tmp"]].drop_duplicates("_cnes_join"), on="_cnes_join", how="left")
        municipio = municipio.mask(municipio.str.strip().eq(""), tmp["municipio_ubs_tmp"].fillna(""))
        codigo = codigo.mask(codigo.map(_codigo_ibge).eq(""), tmp["codigo_ibge_ubs_tmp"].fillna(""))
        eq = tmp

    eq["codigo_ibge_norm"] = codigo.map(_codigo_ibge)
    eq["municipio_limpo"] = municipio.map(_limpar_nome_municipio)
    eq["municipio_key"] = [_chave_municipio(c, m) for c, m in zip(eq["codigo_ibge_norm"], eq["municipio_limpo"])]
    eq["ine_busca"] = _serie_texto(eq, ["ine", "codigo_ine", "co_ine", "cod_ine"]).str.strip()
    eq["tipo_equipe_codigo"] = _serie_texto(eq, ["tipo_equipe_codigo", "co_tipo_equipe", "codigo_tipo_equipe", "tp_equipe"]).str.replace(r"\.0$", "", regex=True).str.strip()
    eq = eq[eq["municipio_key"].astype(str).str.len() > 0].copy()

    linhas = []
    for mun, g in eq.groupby("municipio_key"):
        def n_ines(codigos):
            sub = g[g["tipo_equipe_codigo"].isin(codigos)]
            ines = sub["ine_busca"].replace("", pd.NA).dropna()
            return int(ines.nunique()) if not ines.empty else int(len(sub))
        ines_total = g["ine_busca"].replace("", pd.NA).dropna()
        linhas.append({
            "municipio_key": mun,
            "esf_identificadas": n_ines(["70"]),
            "esb_identificadas": n_ines(["71"]),
            "emulti_identificadas": n_ines(["72"]),
            "ecr_identificadas": n_ines(["73"]),
            "eap_identificadas": n_ines(["74"]),
            "domiciliar_identificadas": n_ines(["76"]),
            "equipes_identificadas": int(ines_total.nunique()) if not ines_total.empty else int(len(g)),
        })
    return pd.DataFrame(linhas)


def _agregar_georreferenciamento(resultado: pd.DataFrame) -> pd.DataFrame:
    if resultado.empty:
        return pd.DataFrame(columns=[
            "municipio_key", "setores_analisados", "populacao_setores", "distancia_media_ubs_km",
            "maior_distancia_ubs_km", "setores_criticos", "setores_provaveis", "indice_vazio_medio",
            "indice_vazio_maximo", "indice_vulnerabilidade_media", "indice_demanda_media",
            "populacao_em_setores_criticos",
        ])
    g = _preparar_df_municipal(resultado)
    if g.empty:
        return pd.DataFrame()
    g["populacao_num"] = _serie_numero(g, ["populacao", "populacao_setor", "populacao_real"], 0)
    g["distancia_num"] = _serie_numero(g, ["distancia_ubs_km", "distancia_ate_ubs_km", "distancia_km"], 0)
    g["vazio_num"] = _serie_numero(g, ["indice_vazio_assistencial", "indice_vazio", "vazio_assistencial"], 0)
    g["vulnerabilidade_num"] = _serie_numero(g, ["indice_vulnerabilidade_social", "indice_vulnerabilidade", "vulnerabilidade"], 0)
    g["demanda_num"] = _serie_numero(g, ["indice_demanda_sus_ajustada", "indice_demanda_sus", "demanda_sus"], 0)
    classificacao = _serie_texto(g, ["classificacao", "classificacao_territorial", "classe"])
    g["critico"] = (g["vazio_num"] >= 80) | classificacao.map(_normalizar_texto).str.contains("critico|muito_alta|alta", na=False)
    g["provavel"] = ((g["vazio_num"] >= 60) & (g["vazio_num"] < 80)) | classificacao.map(_normalizar_texto).str.contains("provavel|media", na=False)

    ag = (
        g.groupby("municipio_key", as_index=False)
        .agg(
            setores_analisados=("municipio_key", "size"),
            populacao_setores=("populacao_num", "sum"),
            distancia_media_ubs_km=("distancia_num", "mean"),
            maior_distancia_ubs_km=("distancia_num", "max"),
            setores_criticos=("critico", "sum"),
            setores_provaveis=("provavel", "sum"),
            indice_vazio_medio=("vazio_num", "mean"),
            indice_vazio_maximo=("vazio_num", "max"),
            indice_vulnerabilidade_media=("vulnerabilidade_num", "mean"),
            indice_demanda_media=("demanda_num", "mean"),
        )
    )
    pop_crit = g[g["critico"]].groupby("municipio_key")["populacao_num"].sum().reset_index(name="populacao_em_setores_criticos")
    ag = ag.merge(pop_crit, on="municipio_key", how="left")
    ag["populacao_em_setores_criticos"] = ag["populacao_em_setores_criticos"].fillna(0)
    return ag


def _preparar_base_dashboard() -> Dict[str, pd.DataFrame]:
    resultado = _get_session_df("geo_resultado_df")
    territorios = _get_session_df("geo_territorios_df")
    ubs = _get_session_df("geo_ubs_df")
    if ubs.empty:
        ubs = _get_session_df("ubs_api_cnes_ubs_lista")
    equipes = filtrar_equipes_ine_aps(_get_session_df("geo_equipes_ine_df"))

    apis = _base_municipal_apis()
    geo_ag = _agregar_georreferenciamento(resultado)
    ubs_ag = _agregar_ubs(ubs)
    eq_ag = _contar_equipes(equipes, ubs)

    # A base municipal/API é a referência. Se ela existir, não se cria município a partir de linhas soltas de UBS/equipes.
    if not apis.empty:
        base = apis.copy()
    else:
        chaves = set()
        for df in [geo_ag, ubs_ag, eq_ag]:
            if not df.empty and "municipio_key" in df.columns:
                chaves.update([x for x in df["municipio_key"].dropna().astype(str).tolist() if x.strip()])
        base = pd.DataFrame({"municipio_key": sorted(chaves)})
        base["municipio"] = base["municipio_key"].str.replace("nome_", "", regex=False).str.replace("ibge_", "", regex=False).str.replace("_", " ").str.title()
        base["codigo_ibge"] = ""
        base["regiao_saude"] = "Não informada"
        base["escritorio_regional"] = ""

    for df in [geo_ag, ubs_ag, eq_ag]:
        if not df.empty:
            base = base.merge(df, on="municipio_key", how="left")

    # População oficial de referência: prioriza campos do ubs_base_automatica_ibge.
    pop_col = _primeira_coluna_existente(base, [
        "populacao_ibge", "populacao_estimada", "populacao_residente", "populacao_total_censo_2022", "populacao", "valor"
    ])
    if pop_col:
        base["populacao_referencia"] = pd.to_numeric(base[pop_col], errors="coerce")
    else:
        base["populacao_referencia"] = pd.NA
    base["populacao_referencia"] = base["populacao_referencia"].fillna(pd.to_numeric(base.get("populacao_setores", 0), errors="coerce")).fillna(0)

    # Campos numéricos relevantes que podem vir das APIs.
    for col in [
        "area_km2", "densidade_demografica", "densidade_calculada_atual", "percentual_rural_2022",
        "pct_rdpc_ate_1_4_sm_2022", "pct_rdpc_ate_1_2_sm_2022", "taxa_analfabetismo_15mais_2022",
        "pct_sem_instrucao_fund_incompleto_25mais_2022", "indice_vulnerabilidade_saneamento_2022",
        "bpc_total_por_1000_hab", "percentual_escolas_rurais_inep", "pessoas_tradicionais_por_1000_hab_2022",
        "taxa_mortalidade_infantil_por_1000_nv_2024", "pct_pre_natal_7mais_sinasc_2024", "pct_baixo_peso_sinasc_2024",
        "pct_prematuridade_sinasc_2024", "pct_pessoas_com_deficiencia_2022", "pct_pessoas_diagnosticadas_autismo_2022",
    ]:
        if col in base.columns:
            base[col] = pd.to_numeric(base[col], errors="coerce")

    base["municipio"] = base.get("municipio", "").fillna("").astype(str).map(_limpar_nome_municipio)
    base.loc[base["municipio"].str.strip().eq(""), "municipio"] = base["municipio_key"].str.replace("nome_", "", regex=False).str.replace("_", " ").str.title()
    base["regiao_saude"] = base.get("regiao_saude", "Não informada")
    base["regiao_saude"] = base["regiao_saude"].fillna("Não informada").replace("", "Não informada")

    for col in [
        "setores_analisados", "populacao_setores", "distancia_media_ubs_km", "maior_distancia_ubs_km",
        "setores_criticos", "setores_provaveis", "indice_vazio_medio", "indice_vazio_maximo",
        "indice_vulnerabilidade_media", "indice_demanda_media", "populacao_em_setores_criticos",
        "ubs_postos_usf", "unidades_sem_coordenada", "esf_identificadas", "esb_identificadas",
        "emulti_identificadas", "ecr_identificadas", "eap_identificadas", "domiciliar_identificadas",
        "equipes_identificadas", "esf_por_ubs", "esb_por_ubs",
    ]:
        if col not in base.columns:
            base[col] = 0
        base[col] = pd.to_numeric(base[col], errors="coerce").fillna(0)

    base["esf_total"] = base[["esf_identificadas", "esf_por_ubs"]].max(axis=1)
    base["esb_total"] = base[["esb_identificadas", "esb_por_ubs"]].max(axis=1)

    faixas, parametros, esf_necessarias, ubs_estimadas = [], [], [], []
    for pop in base["populacao_referencia"]:
        faixa, parametro = _parametro_ms(pop)
        faixas.append(faixa)
        parametros.append(parametro)
        esf_ms = math.ceil(_to_numero(pop, 0) / parametro) if parametro else 0
        esf_necessarias.append(esf_ms)
        ubs_estimadas.append(math.ceil(esf_ms / 2) if esf_ms else 0)
    base["faixa_populacional_ms"] = base.get("faixa_populacional_ms", faixas)
    base["parametro_pessoas_por_esf"] = base.get("parametro_pessoas_por_esf", parametros)
    esf_base = pd.to_numeric(base.get("esf_necessarias_ms", pd.Series(esf_necessarias, index=base.index)), errors="coerce")
    base["esf_necessarias_ms"] = esf_base.fillna(pd.Series(esf_necessarias, index=base.index))
    base["ubs_estimadas_ms_referencia"] = ubs_estimadas
    base["deficit_esf_ms"] = (base["esf_necessarias_ms"] - base["esf_total"]).clip(lower=0)
    base["deficit_ubs_ms"] = (base["ubs_estimadas_ms_referencia"] - base["ubs_postos_usf"]).clip(lower=0)
    base["populacao_por_ubs"] = base.apply(lambda r: r["populacao_referencia"] / r["ubs_postos_usf"] if r["ubs_postos_usf"] else 0, axis=1)
    base["populacao_por_esf"] = base.apply(lambda r: r["populacao_referencia"] / r["esf_total"] if r["esf_total"] else 0, axis=1)

    # Vulnerabilidade municipal complementar quando não houver georreferenciamento territorial.
    saneamento = pd.to_numeric(base.get("indice_vulnerabilidade_saneamento_2022", 0), errors="coerce").fillna(0)
    renda = pd.to_numeric(base.get("pct_rdpc_ate_1_2_sm_2022", 0), errors="coerce").fillna(0)
    instrucao = pd.to_numeric(base.get("pct_sem_instrucao_fund_incompleto_25mais_2022", 0), errors="coerce").fillna(0)
    bpc = pd.to_numeric(base.get("bpc_total_por_1000_hab", 0), errors="coerce").fillna(0)
    rural = pd.to_numeric(base.get("percentual_rural_2022", 0), errors="coerce").fillna(0)
    vulnerabilidade_api = ((saneamento.clip(0, 100) * 0.30) + (renda.clip(0, 100) * 0.25) + (instrucao.clip(0, 100) * 0.20) + (bpc.clip(0, 100) * 0.15) + (rural.clip(0, 100) * 0.10)).round(1)
    base["vulnerabilidade_api_media"] = vulnerabilidade_api
    base["indice_vulnerabilidade_media"] = base["indice_vulnerabilidade_media"].where(base["indice_vulnerabilidade_media"] > 0, base["vulnerabilidade_api_media"])

    base["score_ms"] = (base["deficit_esf_ms"] * 7 + base["deficit_ubs_ms"] * 10).clip(0, 100)
    base["score_oferta"] = (base["populacao_por_ubs"] / 70).clip(0, 100)
    base["score_distancia"] = (base["maior_distancia_ubs_km"] * 6).clip(0, 100)
    base["score_territorial"] = base["indice_vazio_medio"].fillna(0)
    base["score_vulnerabilidade"] = base["indice_vulnerabilidade_media"].fillna(0)
    base["prioridade_ses_mt"] = (
        base["score_ms"] * 0.25
        + base["score_oferta"] * 0.15
        + base["score_distancia"] * 0.15
        + base["score_territorial"] * 0.30
        + base["score_vulnerabilidade"] * 0.15
    ).round(1)

    def classificar_prioridade(v: float) -> str:
        if v >= 75:
            return "Muito alta"
        if v >= 55:
            return "Alta"
        if v >= 35:
            return "Média"
        if v > 0:
            return "Baixa"
        return "Sem dados suficientes"

    base["classificacao_prioridade"] = base["prioridade_ses_mt"].apply(classificar_prioridade)

    def recomendacao(row: pd.Series) -> str:
        recs = []
        if row["deficit_ubs_ms"] > 0 or row["maior_distancia_ubs_km"] >= 15 or row["setores_criticos"] > 0:
            recs.append("avaliar construção de UBS ou unidade de apoio")
        if row["deficit_esf_ms"] > 0 or row["populacao_por_esf"] > 3500:
            recs.append("reorganizar/reforçar eSF")
        if row["esb_total"] == 0 and row["populacao_referencia"] > 0:
            recs.append("avaliar oferta de eSB")
        if row["unidades_sem_coordenada"] > 0:
            recs.append("qualificar coordenadas CNES")
        if row["setores_analisados"] == 0:
            recs.append("carregar setores censitários/população real para validar vazio assistencial")
        if not recs:
            recs.append("manter monitoramento e validar território com município")
        return "; ".join(dict.fromkeys(recs)).capitalize() + "."

    base["recomendacao_preliminar"] = base.apply(recomendacao, axis=1)
    base = base.sort_values(["prioridade_ses_mt", "indice_vazio_maximo"], ascending=False).reset_index(drop=True)

    return {"municipios": base, "resultado": resultado, "territorios": territorios, "ubs": ubs, "equipes": equipes, "apis": apis}


# -------------------------
# Formatação
# -------------------------

def _fmt_int(v: Any) -> str:
    return f"{int(round(_to_numero(v, 0))):,}".replace(",", ".")


def _fmt_float(v: Any, casas: int = 1) -> str:
    return f"{_to_numero(v, 0):,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _metricas_base(base: pd.DataFrame) -> None:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Municípios analisados", _fmt_int(base["municipio_key"].nunique() if not base.empty else 0))
    c2.metric("UBS/Postos/USF mapeados", _fmt_int(base["ubs_postos_usf"].sum() if not base.empty else 0))
    c3.metric("Equipes APS INE", _fmt_int(base["equipes_identificadas"].sum() if not base.empty else 0))
    c4.metric("População territorial analisada", _fmt_int(base["populacao_setores"].sum() if not base.empty else 0))
    c5.metric("Setores críticos", _fmt_int(base["setores_criticos"].sum() if not base.empty else 0))


def _filtros(base: pd.DataFrame) -> pd.DataFrame:
    if base.empty:
        return base
    c1, c2, c3 = st.columns([1.2, 1, 1])
    regioes = ["Todas"] + sorted([r for r in base["regiao_saude"].dropna().astype(str).unique().tolist() if r.strip()])
    regiao = c1.selectbox("Região de Saúde", regioes)
    classificacoes = ["Todas", "Muito alta", "Alta", "Média", "Baixa", "Sem dados suficientes"]
    classe = c2.selectbox("Classificação de prioridade", classificacoes)
    somente_criticos = c3.toggle("Somente com vazio crítico", value=False)
    out = base.copy()
    if regiao != "Todas":
        out = out[out["regiao_saude"] == regiao]
    if classe != "Todas":
        out = out[out["classificacao_prioridade"] == classe]
    if somente_criticos:
        out = out[out["setores_criticos"] > 0]
    return out


def _ranking_cols() -> List[str]:
    return [
        "municipio", "codigo_ibge", "regiao_saude", "populacao_referencia", "prioridade_ses_mt", "classificacao_prioridade",
        "deficit_ubs_ms", "deficit_esf_ms", "ubs_postos_usf", "esf_total", "esb_total",
        "equipes_identificadas", "setores_analisados", "setores_criticos", "maior_distancia_ubs_km",
        "indice_vazio_medio", "indice_vulnerabilidade_media", "recomendacao_preliminar",
    ]


# -------------------------
# Abas
# -------------------------


def _resultado_eh_preliminar_sem_setores(df: pd.DataFrame) -> bool:
    """Identifica ranking municipal preliminar gerado sem setores censitários."""
    if not isinstance(df, pd.DataFrame) or df.empty:
        return False
    for col in ["resultado_tipo", "tipo_territorio", "observacao_validacao"]:
        if col in df.columns:
            serie = df[col].fillna("").astype(str).str.lower()
            if serie.str.contains("preliminar", na=False).any() or serie.str.contains("sem setores", na=False).any():
                return True
    return False

def _texto_distancia_dashboard(valor: Any, preliminar: bool = False) -> str:
    if preliminar:
        return "Pendente"
    return f"{_fmt_float(valor)} km"

def _render_estrategico(dados: Dict[str, pd.DataFrame]) -> None:
    base = dados["municipios"]
    st.markdown("### Visão estratégica estadual")
    st.caption("Síntese para tomada de decisão sobre investimento, pactuação e reorganização da Atenção Primária.")
    if base.empty:
        st.warning("Ainda não há base suficiente carregada para montar o dashboard. Carregue as APIs e/ou o Georreferenciamento APS.")
        return
    _metricas_base(base)
    filtrado = _filtros(base)
    st.markdown("#### Ranking de prioridade municipal")
    cols = [c for c in _ranking_cols() if c in filtrado.columns]
    st.dataframe(
        filtrado[cols].head(40),
        use_container_width=True,
        hide_index=True,
        column_config={
            "populacao_referencia": st.column_config.NumberColumn("População", format="%d"),
            "prioridade_ses_mt": st.column_config.ProgressColumn("Prioridade SES/MT", min_value=0, max_value=100, format="%.1f"),
            "maior_distancia_ubs_km": st.column_config.NumberColumn("Maior distância até UBS (km)", format="%.1f"),
            "indice_vazio_medio": st.column_config.NumberColumn("Vazio médio", format="%.1f"),
            "indice_vulnerabilidade_media": st.column_config.NumberColumn("Vulnerabilidade média", format="%.1f"),
        },
    )

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### Prioridade média por Região de Saúde")
        reg = (
            filtrado.groupby("regiao_saude", as_index=False)
            .agg(
                municipios=("municipio", "nunique"),
                prioridade_media=("prioridade_ses_mt", "mean"),
                ubs=("ubs_postos_usf", "sum"),
                equipes_aps=("equipes_identificadas", "sum"),
                setores_criticos=("setores_criticos", "sum"),
            )
            .sort_values("prioridade_media", ascending=False)
        )
        st.dataframe(reg, use_container_width=True, hide_index=True)
    with c2:
        st.markdown("#### Top municípios por vazio assistencial")
        if filtrado["indice_vazio_maximo"].max() > 0:
            top = filtrado.nlargest(10, "indice_vazio_maximo")[["municipio", "indice_vazio_maximo", "maior_distancia_ubs_km"]]
            st.bar_chart(top.set_index("municipio")["indice_vazio_maximo"])
        else:
            st.info("O gráfico de vazio assistencial será preenchido após calcular o ranking no Georreferenciamento APS.")


def _tabela_equipes_ine_prioritarias(equipes: pd.DataFrame, base_municipios: pd.DataFrame) -> pd.DataFrame:
    if equipes is None or equipes.empty:
        return pd.DataFrame()
    eq = filtrar_equipes_ine_aps(_normalizar_colunas(equipes))
    if eq.empty:
        return pd.DataFrame()
    eq = _preparar_df_municipal(eq)
    for col in ["codigo_ibge", "municipio", "cnes", "ine", "nome_equipe", "tipo_equipe", "tipo_equipe_codigo"]:
        if col not in eq.columns:
            eq[col] = ""
    eq["municipio"] = eq["municipio_limpo"].where(eq["municipio_limpo"].astype(str).str.len() > 0, eq["municipio"])
    eq["codigo_ibge"] = eq["codigo_ibge_norm"].where(eq["codigo_ibge_norm"].astype(str).str.len() > 0, eq["codigo_ibge"])
    mapa = base_municipios[["municipio_key", "regiao_saude"]].drop_duplicates() if base_municipios is not None and not base_municipios.empty else pd.DataFrame()
    if not mapa.empty:
        eq = eq.merge(mapa, on="municipio_key", how="left")
    else:
        eq["regiao_saude"] = ""
    tabela = (
        eq.groupby(["municipio", "regiao_saude", "codigo_ibge", "cnes", "ine", "tipo_equipe_codigo", "tipo_equipe"], dropna=False)
        .agg(nome_equipe=("nome_equipe", lambda s: next((str(v).strip() for v in s if str(v).strip()), "")), registros_equipe=("ine", "size"))
        .reset_index()
        .sort_values(["municipio", "tipo_equipe_codigo", "ine"])
    )
    return tabela


def _render_tatico(dados: Dict[str, pd.DataFrame]) -> None:
    base = dados["municipios"]
    if base.empty:
        st.warning("Ainda não há dados suficientes para a visão tática.")
        return
    st.markdown("### Visão tática por Região de Saúde")
    st.caption("Compara municípios da mesma região e explica por que determinado município aparece como prioridade.")
    regioes = ["Todas"] + sorted(base["regiao_saude"].dropna().astype(str).unique().tolist())
    regiao = st.selectbox("Selecionar Região de Saúde para comparação", regioes, key="tatico_regiao")
    df = base.copy()
    if regiao != "Todas":
        df = df[df["regiao_saude"] == regiao]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Municípios no recorte", _fmt_int(df["municipio"].nunique()))
    c2.metric("Prioridade média", _fmt_float(df["prioridade_ses_mt"].mean()))
    c3.metric("Déficit eSF MS", _fmt_int(df["deficit_esf_ms"].sum()))
    c4.metric("Déficit UBS MS", _fmt_int(df["deficit_ubs_ms"].sum()))

    st.markdown("#### Comparativo municipal")
    cols = [
        "municipio", "regiao_saude", "populacao_referencia", "ubs_postos_usf", "populacao_por_ubs",
        "esf_total", "esb_total", "equipes_identificadas", "populacao_por_esf", "distancia_media_ubs_km", "maior_distancia_ubs_km",
        "percentual_rural_2022", "indice_vulnerabilidade_saneamento_2022", "bpc_total_por_1000_hab",
        "taxa_analfabetismo_15mais_2022", "indice_vulnerabilidade_media", "prioridade_ses_mt",
    ]
    st.dataframe(
        df[[c for c in cols if c in df.columns]].sort_values("prioridade_ses_mt", ascending=False),
        use_container_width=True,
        hide_index=True,
        column_config={
            "populacao_referencia": st.column_config.NumberColumn("População", format="%d"),
            "populacao_por_ubs": st.column_config.NumberColumn("Pop./UBS", format="%.0f"),
            "populacao_por_esf": st.column_config.NumberColumn("Pop./eSF", format="%.0f"),
            "prioridade_ses_mt": st.column_config.ProgressColumn("Prioridade", min_value=0, max_value=100, format="%.1f"),
        },
    )

    st.markdown("#### Equipes INE prioritárias para análise da coordenadoria")
    st.caption("Filtro aplicado: tipos de equipe CNES 70, 71, 72, 73, 74 e 76. O tipo 75 fica fora desta visão.")
    tabela_ine = _tabela_equipes_ine_prioritarias(dados.get("equipes", pd.DataFrame()), base)
    if not tabela_ine.empty:
        if regiao != "Todas" and "regiao_saude" in tabela_ine.columns:
            tabela_ine = tabela_ine[tabela_ine["regiao_saude"] == regiao].copy()
        c_ine1, c_ine2, c_ine3, c_ine4 = st.columns(4)
        c_ine1.metric("Municípios com INE", _fmt_int(tabela_ine["municipio"].nunique()))
        c_ine2.metric("INEs identificados", _fmt_int(tabela_ine["ine"].nunique()))
        c_ine3.metric("CNES com equipe", _fmt_int(tabela_ine["cnes"].nunique()))
        c_ine4.metric("Tipos considerados", "70, 71, 72, 73, 74, 76")
        st.dataframe(tabela_ine[[c for c in ["municipio", "regiao_saude", "cnes", "ine", "tipo_equipe_codigo", "tipo_equipe", "nome_equipe"] if c in tabela_ine.columns]], use_container_width=True, hide_index=True)
    else:
        st.info("Nenhuma base de equipes INE prioritárias foi carregada ainda. Envie o EQUIPES BRASIL no Georreferenciamento APS ou carregue o cache salvo.")

    st.markdown("#### Leitura técnica do recorte")
    pior = df.sort_values("prioridade_ses_mt", ascending=False).head(1)
    if not pior.empty:
        r = pior.iloc[0]
        st.markdown(
            f"""
            <div class="aps-note">
            <b>Município de maior atenção no recorte:</b> {r['municipio']}. 
            A prioridade calculada foi de <b>{_fmt_float(r['prioridade_ses_mt'])}</b>, com 
            <b>{_fmt_int(r['deficit_ubs_ms'])}</b> déficit preliminar de UBS pela referência MS, 
            <b>{_fmt_int(r['deficit_esf_ms'])}</b> déficit preliminar de eSF e 
            <b>{_fmt_int(r['setores_criticos'])}</b> setores/territórios classificados como críticos.
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_operacional(dados: Dict[str, pd.DataFrame]) -> None:
    resultado = dados["resultado"]
    ubs = dados["ubs"]
    st.markdown("### Visão operacional do território")
    st.caption("Apoia a análise concreta dos setores censitários, unidades, equipes, coordenadas e recomendações preliminares.")
    if resultado.empty:
        st.warning("O resultado do georreferenciamento ainda não foi calculado.")
        st.markdown(
            """
            <div class="aps-alert">
            Para liberar esta aba, vá em <b>Estudo Territorial | Georreferenciamento APS</b> e siga exatamente este caminho:<br><br>
            <b>1.</b> Aba <b>CNES/INE automático</b>: conferir UBS/CNES e subir o arquivo <b>EQUIPESBRASIL</b> se necessário.<br>
            <b>2.</b> Aba <b>Setores IBGE/população real</b>: clicar em <b>Carregar setores IBGE 2022 - MT</b> ou fazer upload de base territorial com população real.<br>
            <b>3.</b> Aba <b>Ranking de vazios assistenciais</b>: clicar/calcular o ranking. Ao final, o sistema grava <b>geo_resultado_df</b>.<br>
            <b>4.</b> Salvar o <b>Cache APS</b> para manter os dados nas próximas aberturas.
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    res = _preparar_df_municipal(resultado)
    preliminar_sem_setores = _resultado_eh_preliminar_sem_setores(res)
    if preliminar_sem_setores:
        st.warning(
            "Este é um ranking municipal preliminar, gerado sem setores censitários/população real. "
            "Ele serve para triagem inicial, mas ainda não mede distância real até UBS nem setores críticos territoriais."
        )
    municipios = ["Todos"] + sorted(res["municipio_limpo"].dropna().astype(str).unique().tolist())
    municipio = st.selectbox("Município", municipios, key="operacional_municipio")
    df = res.copy()
    if municipio != "Todos":
        df = df[df["municipio_limpo"] == municipio]
    classes = ["Todas"] + sorted(_serie_texto(df, ["classificacao"]).dropna().unique().tolist())
    classe = st.selectbox("Classificação territorial", classes, key="operacional_classe")
    if classe != "Todas":
        df = df[_serie_texto(df, ["classificacao"]) == classe]
    if preliminar_sem_setores and "distancia_ubs_km" in df.columns:
        df = df.copy()
        df["distancia_ubs_km"] = "Pendente"
    cols = [
        "municipio", "codigo_ibge", "bairro_ou_localidade", "tipo_territorio", "populacao",
        "ubs_mais_proxima", "distancia_ubs_km", "qtd_esf_ubs_proxima", "qtd_esb_ubs_proxima",
        "indice_vazio_assistencial", "indice_demanda_sus_ajustada", "classificacao", "recomendacao_tecnica",
    ]
    st.dataframe(df[[c for c in cols if c in df.columns]], use_container_width=True, hide_index=True)
    st.markdown("#### Qualidade cadastral das UBS/CNES")
    if ubs.empty:
        st.info("Nenhuma base de UBS/CNES carregada em sessão.")
    else:
        u = _preparar_df_municipal(ubs)
        if municipio != "Todos":
            u = u[u["municipio_limpo"] == municipio]
        cols_ubs = ["municipio", "codigo_ibge", "cnes", "nome_unidade", "tipo_unidade", "latitude", "longitude", "ines_vinculados", "observacao_validacao"]
        st.dataframe(u[[c for c in cols_ubs if c in u.columns]], use_container_width=True, hide_index=True)


def _texto_despacho(row: pd.Series, setores_mun: pd.DataFrame) -> str:
    setores = int(row.get("setores_analisados", 0))
    pop_critica = _fmt_int(row.get("populacao_em_setores_criticos", 0))
    preliminar_sem_setores = _resultado_eh_preliminar_sem_setores(setores_mun)
    maior_dist = "pendente de cálculo territorial" if preliminar_sem_setores else f"{_fmt_float(row.get('maior_distancia_ubs_km', 0))} km"
    prioridade = _fmt_float(row.get("prioridade_ses_mt", 0))
    trecho_territorial = (
        "Até o momento, o resultado disponível é municipal preliminar, sem setores censitários/população real detalhada; portanto, a distância real até UBS, setores críticos e vazio territorial ainda dependem de validação georreferenciada."
        if preliminar_sem_setores
        else f"Na análise territorial da SES/MT, foram considerados {setores} setores/territórios, com maior distância estimada até UBS de {maior_dist}, {_fmt_int(row.get('setores_criticos', 0))} setores/territórios críticos e população estimada em setores críticos de {pop_critica} pessoas."
    )
    return (
        f"O município de {row.get('municipio', '')}, integrante da Região de Saúde {row.get('regiao_saude', 'não informada')}, "
        f"apresenta população de referência estimada em {_fmt_int(row.get('populacao_referencia', 0))} habitantes, "
        f"com {_fmt_int(row.get('ubs_postos_usf', 0))} UBS/Postos/USF mapeados, "
        f"{_fmt_int(row.get('esf_total', 0))} equipes eSF, {_fmt_int(row.get('esb_total', 0))} equipes eSB e "
        f"{_fmt_int(row.get('equipes_identificadas', 0))} equipes APS/INE dos tipos 70, 71, 72, 73, 74 e 76 identificadas. "
        f"Pela referência metodológica federal utilizada como linha de base, estima-se necessidade de "
        f"{_fmt_int(row.get('esf_necessarias_ms', 0))} eSF e {_fmt_int(row.get('ubs_estimadas_ms_referencia', 0))} UBS de referência, "
        f"resultando em déficit preliminar de {_fmt_int(row.get('deficit_esf_ms', 0))} eSF e {_fmt_int(row.get('deficit_ubs_ms', 0))} UBS. "
        f"{trecho_territorial} "
        f"O índice preliminar de prioridade SES/MT foi de {prioridade}, classificado como {row.get('classificacao_prioridade', '')}. "
        f"Diante do conjunto de evidências, recomenda-se {str(row.get('recomendacao_preliminar', '')).lower()} "
        f"A conclusão deve ser validada tecnicamente com o município, considerando a distribuição real da população, a organização das equipes, "
        f"a disponibilidade de imóveis/equipamentos, a malha viária, a ruralidade e demais especificidades territoriais."
    )


def _render_estudo_municipal(dados: Dict[str, pd.DataFrame]) -> None:
    base = dados["municipios"]
    resultado = dados["resultado"]
    equipes = dados["equipes"]
    st.markdown("### Estudo Municipal")
    st.caption("Tela para o técnico da SES estudar um município específico e gerar texto técnico para despacho.")
    if base.empty:
        st.warning("Ainda não há municípios carregados para estudo.")
        return
    municipios = sorted(base["municipio"].dropna().astype(str).unique().tolist())
    municipio = st.selectbox("Selecionar município", municipios, key="estudo_municipio")
    row = base[base["municipio"] == municipio].sort_values("prioridade_ses_mt", ascending=False).iloc[0]
    st.markdown(
        f"""
        <div class="aps-card">
            <h4>{row['municipio']} — {row.get('regiao_saude', 'Região não informada')}</h4>
            <p>
                <span class="aps-badge">Prioridade SES/MT: {_fmt_float(row.get('prioridade_ses_mt', 0))}</span>
                <span class="aps-badge">Classificação: {row.get('classificacao_prioridade', '')}</span>
                <span class="aps-badge">Déficit UBS MS: {_fmt_int(row.get('deficit_ubs_ms', 0))}</span>
                <span class="aps-badge">Déficit eSF MS: {_fmt_int(row.get('deficit_esf_ms', 0))}</span>
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("População", _fmt_int(row.get("populacao_referencia", 0)))
    c2.metric("UBS/Postos/USF", _fmt_int(row.get("ubs_postos_usf", 0)))
    c3.metric("Equipes APS INE", _fmt_int(row.get("equipes_identificadas", 0)))
    c4.metric("Setores críticos", _fmt_int(row.get("setores_criticos", 0)))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("População por UBS", _fmt_int(row.get("populacao_por_ubs", 0)))
    c2.metric("População por eSF", _fmt_int(row.get("populacao_por_esf", 0)))
    resultado_municipio_preliminar = False
    if not resultado.empty:
        res_tmp = _preparar_df_municipal(resultado)
        resultado_municipio_preliminar = _resultado_eh_preliminar_sem_setores(res_tmp[res_tmp.get("municipio_limpo", "") == municipio])
    c3.metric("Maior distância até UBS", _texto_distancia_dashboard(row.get('maior_distancia_ubs_km', 0), resultado_municipio_preliminar))
    c4.metric("Vulnerabilidade média", _fmt_float(row.get("indice_vulnerabilidade_media", 0)))
    if resultado_municipio_preliminar:
        st.warning("O estudo municipal está usando resultado preliminar sem setores censitários. Distância real até UBS, setores críticos e vazio territorial dependem da carga territorial completa no Georreferenciamento APS.")

    with st.expander("Camadas municipais das APIs", expanded=True):
        api_cols = [
            "area_km2", "densidade_demografica", "percentual_rural_2022", "taxa_analfabetismo_15mais_2022",
            "pct_rdpc_ate_1_2_sm_2022", "indice_vulnerabilidade_saneamento_2022", "bpc_total_por_1000_hab",
            "escolas_rurais_inep", "nascidos_vivos_sinasc_2024", "taxa_mortalidade_infantil_por_1000_nv_2024",
            "pessoas_tradicionais_total_2022", "pct_pessoas_com_deficiencia_2022",
        ]
        tabela_api = pd.DataFrame([{"Indicador": c, "Valor": row.get(c, "") } for c in api_cols if c in base.columns])
        st.dataframe(tabela_api, use_container_width=True, hide_index=True)

    st.markdown("#### Equipes INE do município")
    tabela_ine = _tabela_equipes_ine_prioritarias(equipes, base)
    if not tabela_ine.empty:
        st.dataframe(tabela_ine[tabela_ine["municipio"] == municipio], use_container_width=True, hide_index=True)
    else:
        st.info("Sem base de equipes INE carregada para o município.")

    st.markdown("#### Recomendações preliminares")
    st.success(row.get("recomendacao_preliminar", "Validar tecnicamente com o município."))
    st.markdown("#### Setores/territórios do município")
    setores_mun = pd.DataFrame()
    if not resultado.empty:
        res = _preparar_df_municipal(resultado)
        setores_mun = res[res["municipio_limpo"] == municipio].copy()
    if setores_mun.empty:
        st.info("Não há setores/territórios detalhados para este município na sessão atual. Para preencher distância, vazio assistencial e setores críticos, calcule o ranking em Georreferenciamento APS.")
    else:
        cols = ["bairro_ou_localidade", "tipo_territorio", "populacao", "ubs_mais_proxima", "distancia_ubs_km", "qtd_esf_ubs_proxima", "qtd_esb_ubs_proxima", "indice_vazio_assistencial", "classificacao", "recomendacao_tecnica"]
        st.dataframe(setores_mun[[c for c in cols if c in setores_mun.columns]], use_container_width=True, hide_index=True)
    st.markdown("#### Texto técnico automático para despacho")
    texto = _texto_despacho(row, setores_mun)
    st.text_area("Copiar texto técnico", value=texto, height=230)
    st.download_button("Baixar texto técnico (.txt)", data=texto.encode("utf-8"), file_name=f"estudo_municipal_{_normalizar_texto(municipio)}.txt", mime="text/plain", use_container_width=True)


# -------------------------
# Status das fontes
# -------------------------

def _df_status_fonte(nome: str, chave: str) -> Dict[str, Any]:
    if "/" in chave:
        partes = [p.strip() for p in chave.split("/")]
        dfs = [_get_session_df(p) for p in partes]
        df = next((d for d in dfs if not d.empty), pd.DataFrame())
    else:
        df = _get_session_df(chave)
    if chave == "geo_ubs_df / ubs_api_cnes_ubs_lista" and df.empty:
        df = _get_session_df("ubs_api_cnes_ubs_lista")
    if chave == "ubs_api_area_densidade / ubs_api_urbano_rural":
        d1 = _get_session_df("ubs_api_area_densidade")
        d2 = _get_session_df("ubs_api_urbano_rural")
        df = d1 if not d1.empty else d2
    if chave == "geo_equipes_ine_df":
        df = filtrar_equipes_ine_aps(df)
    mun_unicos = 0
    if isinstance(df, pd.DataFrame) and not df.empty:
        prep = _preparar_df_municipal(df)
        mun_unicos = prep["municipio_key"].nunique() if not prep.empty and "municipio_key" in prep.columns else 0
    linhas = len(df) if isinstance(df, pd.DataFrame) else 0
    situacao = "Carregado" if linhas else "Pendente"
    return {"Fonte": nome, "Chave/Origem": chave, "Situação": situacao, "Registros brutos": linhas, "Municípios únicos": int(mun_unicos)}


def _render_fontes_status(dados: Dict[str, pd.DataFrame]) -> None:
    with st.expander("Status das fontes usadas nesta primeira versão", expanded=False):
        status = [_df_status_fonte(nome, chave) for nome, chave in CHAVES_APIS_STATUS]
        st.dataframe(pd.DataFrame(status), use_container_width=True, hide_index=True)
        base = dados.get("municipios", pd.DataFrame())
        if not base.empty:
            st.caption(f"Base consolidada do dashboard: {base['municipio_key'].nunique()} municípios únicos. A contagem usa código IBGE quando disponível e remove duplicidades como 'Município' versus 'Município - MT'.")
        faltas = []
        if dados.get("territorios", pd.DataFrame()).empty:
            faltas.append("Setores/territórios com população real")
        if dados.get("resultado", pd.DataFrame()).empty:
            faltas.append("Resultado do georreferenciamento/ranking de vazios")
        if faltas:
            st.warning("Para completar distância, setores críticos e vazios assistenciais, ainda falta: " + "; ".join(faltas) + ".")


def render_dashboard_executivo_aps() -> None:
    _css_dashboard_aps()
    st.markdown(
        """
        <div class="aps-hero">
            <h2>Dashboard Executivo APS</h2>
            <p>
                Inteligência territorial da Atenção Primária à Saúde em Mato Grosso:
                metodologia federal como linha de base, qualificada pela oferta real CNES/INE,
                população territorial, distância até UBS, vulnerabilidade social, ruralidade,
                saneamento, escolaridade e vazios assistenciais.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    meta_cache = ler_metadata_cache_aps()
    if meta_cache:
        st.caption(f"Cache APS carregado/salvo em: {meta_cache.get('atualizado_em', 'sem data')}. Códigos INE considerados: 70, 71, 72, 73, 74 e 76.")

    dados = _preparar_base_dashboard()
    if dados["municipios"].empty:
        st.markdown(
            """
            <div class="aps-alert">
                <b>Base ainda não carregada.</b> Para habilitar a primeira versão do dashboard,
                carregue as bases em <b>Conectores APIs UBS</b> e/ou execute o cálculo em
                <b>Georreferenciamento APS</b>. A tela foi preparada para funcionar com dados parciais,
                sem depender da pasta data completa.
            </div>
            """,
            unsafe_allow_html=True,
        )
        _render_fontes_status(dados)
        return

    _render_fontes_status(dados)
    abas = st.tabs(["1. Estratégico", "2. Tático", "3. Operacional", "4. Estudo Municipal"])
    with abas[0]:
        _render_estrategico(dados)
    with abas[1]:
        _render_tatico(dados)
    with abas[2]:
        _render_operacional(dados)
    with abas[3]:
        _render_estudo_municipal(dados)
