from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional

import pandas as pd
import streamlit as st

# Usa caminho absoluto baseado na pasta do projeto, não no diretório atual do PowerShell/Streamlit.
# Isso evita o bug de salvar o EQUIPES BRASIL em um data_cache e depois o app procurar em outro.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = PROJECT_ROOT / "data_cache"
CACHE_META = CACHE_DIR / "cache_info.json"

# Bases principais que o Dashboard Executivo APS e o Georreferenciamento usam.
CACHE_KEYS = [
    "ubs_api_municipios",
    "ubs_api_distritos",
    "ubs_api_populacao",
    "ubs_api_area_densidade",
    "ubs_api_urbano_rural",
    "ubs_api_regioes_saude",
    "ubs_api_demografia_9515",
    "ubs_api_alfabetizacao_9543",
    "ubs_api_regioes_geograficas_ibge",
    "ubs_api_saneamento",
    "ubs_api_instrucao_10061",
    "ubs_api_bpc",
    "ubs_api_renda_ibge",
    "ubs_api_deficiencia_autismo_ibge",
    "ubs_api_inep_censo_escolar",
    "ubs_api_inep_educacao_especial",
    "ubs_api_cnes_ubs_lista",
    "ubs_api_leitos_lista",
    "ubs_api_sinasc_municipal",
    "ubs_api_sim_municipal",
    "ubs_api_povos_tradicionais",
    "ubs_base_automatica_ibge",
    "geo_ubs_df",
    "geo_equipes_ine_df",
    "geo_profissionais_equipes_df",
    "geo_profissionais_ine_resumo_df",
    "geo_territorios_df",
    "geo_resultado_df",
]

EQUIPE_CODIGOS_INE_PERMITIDOS = {"70", "71", "72", "73", "74", "76"}


def _arquivo_da_chave(chave: str) -> Path:
    return CACHE_DIR / f"{chave}.csv"


def _normalizar_codigo_equipe(valor) -> str:
    texto = "" if valor is None else str(valor).strip()
    # Evita transformar 70.0 em 700.
    if texto.endswith(".0"):
        texto = texto[:-2]
    import re
    achado = re.search(r"(70|71|72|73|74|75|76)\b", texto)
    if achado:
        return achado.group(1)
    digitos = re.sub(r"\D", "", texto)
    if digitos in EQUIPE_CODIGOS_INE_PERMITIDOS:
        return digitos
    return digitos[:2] if digitos[:2] in EQUIPE_CODIGOS_INE_PERMITIDOS else ""


def filtrar_equipes_ine_aps(df: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Mantém apenas equipes INE APS de interesse da SES/MT: 70, 71, 72, 73, 74 e 76.

    A função é tolerante a nomes de colunas diferentes e não quebra caso a base
    ainda não tenha tipo_equipe_codigo. Nesses casos, tenta inferir pelo texto.
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame() if df is None else df

    out = df.copy()
    col_codigo = None
    for candidato in ["tipo_equipe_codigo", "co_tipo_equipe", "codigo_tipo_equipe", "tp_equipe", "tipo_codigo"]:
        if candidato in out.columns:
            col_codigo = candidato
            break

    if col_codigo:
        cod = out[col_codigo].map(_normalizar_codigo_equipe)
    else:
        col_tipo = None
        for candidato in ["tipo_equipe", "descricao_tipo_equipe", "ds_tipo_equipe", "no_tipo_equipe", "tipo"]:
            if candidato in out.columns:
                col_tipo = candidato
                break
        if col_tipo is None:
            return out
        cod = out[col_tipo].map(_normalizar_codigo_equipe)

    out["tipo_equipe_codigo"] = cod
    out = out[out["tipo_equipe_codigo"].isin(EQUIPE_CODIGOS_INE_PERMITIDOS)].copy()
    return out.reset_index(drop=True)


def salvar_cache_aps(keys: Optional[Iterable[str]] = None, origem: str = "salvamento manual") -> Dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    keys = list(keys or CACHE_KEYS)
    salvos = []
    ignorados = []

    for chave in keys:
        valor = st.session_state.get(chave)
        if not isinstance(valor, pd.DataFrame) or valor.empty:
            ignorados.append(chave)
            continue
        df = valor.copy()
        if chave == "geo_equipes_ine_df":
            df = filtrar_equipes_ine_aps(df)
        try:
            df.to_csv(_arquivo_da_chave(chave), index=False, encoding="utf-8-sig")
            salvos.append({"chave": chave, "linhas": int(len(df)), "arquivo": str(_arquivo_da_chave(chave))})
        except Exception as exc:
            ignorados.append(f"{chave}: {exc}")

    meta = {
        "atualizado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "origem": origem,
        "total_bases_salvas": len(salvos),
        "bases": salvos,
        "ignorados": ignorados,
        "codigos_ine_permitidos": sorted(EQUIPE_CODIGOS_INE_PERMITIDOS),
    }
    CACHE_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def carregar_cache_aps_para_session_state(keys: Optional[Iterable[str]] = None, sobrescrever: bool = False) -> Dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    keys = list(keys or CACHE_KEYS)
    carregados = []
    ignorados = []

    for chave in keys:
        if not sobrescrever:
            atual = st.session_state.get(chave)
            if isinstance(atual, pd.DataFrame) and not atual.empty:
                ignorados.append(f"{chave}: já estava carregada")
                continue
        arquivo = _arquivo_da_chave(chave)
        if not arquivo.exists():
            continue
        try:
            df = pd.read_csv(arquivo, dtype=str, encoding="utf-8-sig", keep_default_na=False)
            if chave == "geo_equipes_ine_df":
                df = filtrar_equipes_ine_aps(df)
            st.session_state[chave] = df
            carregados.append({"chave": chave, "linhas": int(len(df))})
        except Exception as exc:
            ignorados.append(f"{chave}: {exc}")

    st.session_state["aps_cache_carregado"] = True
    return {"carregados": carregados, "ignorados": ignorados, "metadata": ler_metadata_cache_aps()}


def ler_metadata_cache_aps() -> Dict:
    if not CACHE_META.exists():
        return {}
    try:
        return json.loads(CACHE_META.read_text(encoding="utf-8"))
    except Exception:
        return {}


def limpar_cache_aps() -> None:
    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
