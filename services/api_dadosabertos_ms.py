"""Conectores do Ministério da Saúde/Dados Abertos para regionalização.

v19 — Regionalização de Saúde
-----------------------------
Este conector prioriza a rotina legada que já funcionava no sistema antigo
para baixar o ZIP público de Macrorregião/Região de Saúde do SUS. A saída é
validada contra a lista canônica de municípios de Mato Grosso e devolvida em
formato municipal.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pandas as pd

from config.settings import ROOT_DIR

LEGACY_CONNECTORS_PATH = ROOT_DIR / "legacy" / "conectores_apis_ubs_antigo.py"


def _load_legacy_connectors():
    if not LEGACY_CONNECTORS_PATH.exists():
        raise FileNotFoundError(
            "Arquivo legacy/conectores_apis_ubs_antigo.py não encontrado. "
            "Copie o arquivo antigo ui/conectores_apis_ubs.py para a pasta legacy com esse nome."
        )
    spec = importlib.util.spec_from_file_location("legacy_conectores_apis_ubs_antigo", LEGACY_CONNECTORS_PATH)
    if spec is None or spec.loader is None:
        raise ImportError("Não foi possível carregar o arquivo legado de conectores Dados Abertos/MS.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normalizar_colunas(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    # Padroniza os nomes mais importantes, sem exigir layout único da fonte.
    ren = {}
    for col in out.columns:
        c = str(col).strip().lower()
        if c in {"municipio_regiao_saude", "municipio", "nome_municipio", "no_municipio"}:
            ren[col] = "municipio"
        elif c in {"regiao_saude_sus", "regiao_saude", "regiao_de_saude", "nome_regiao_saude", "no_regiao_saude"}:
            ren[col] = "regiao_saude_ms"
        elif c in {"macrorregiao_saude_sus", "macrorregiao_saude", "macro_regiao_saude", "macrorregiao", "macroregiao"}:
            ren[col] = "macrorregiao_saude_ms"
        elif c in {"codigo_regiao_saude", "co_regiao_saude", "cod_regiao_saude"}:
            ren[col] = "codigo_regiao_saude_ms"
        elif c in {"codigo_macrorregiao_saude", "co_macro_regiao_saude", "cod_macro_regiao_saude"}:
            ren[col] = "codigo_macrorregiao_saude_ms"
    out = out.rename(columns=ren)
    return out


def carregar_regioes_saude_dadosabertos_mt() -> pd.DataFrame:
    """Carrega regionalização SUS de MT usando a rotina validada do sistema antigo.

    Retorna uma linha por município com código IBGE, região e macrorregião.
    O resultado não é aceito como sucesso se não trouxer municípios de MT.
    """
    modulo = _load_legacy_connectors()
    funcao = getattr(modulo, "carregar_regioes_saude_dadosabertos_mt", None)
    if not callable(funcao):
        raise AttributeError("Função carregar_regioes_saude_dadosabertos_mt não encontrada no legacy.")

    resultado: Any = funcao()
    if isinstance(resultado, pd.DataFrame):
        df = resultado.copy()
    elif isinstance(resultado, list):
        df = pd.DataFrame(resultado)
    elif isinstance(resultado, dict):
        df = pd.DataFrame([resultado])
    else:
        raise ValueError(f"Retorno inesperado da regionalização MS: {type(resultado).__name__}")

    if df.empty:
        raise ValueError("Regionalização de Saúde retornou DataFrame vazio.")

    df = _normalizar_colunas(df)
    if "codigo_ibge" not in df.columns:
        raise ValueError(f"Regionalização MS sem coluna codigo_ibge. Colunas: {list(df.columns)[:20]}")
    if "municipio" not in df.columns:
        # Algumas versões retornam municipio_regiao_saude; a normalização acima deveria capturar.
        raise ValueError(f"Regionalização MS sem coluna municipio. Colunas: {list(df.columns)[:20]}")

    df["codigo_ibge"] = df["codigo_ibge"].astype(str).str.extract(r"(\d{7})", expand=False).fillna(df["codigo_ibge"].astype(str))
    df = df[df["codigo_ibge"].astype(str).str.startswith("51", na=False)].copy()
    df = df.drop_duplicates(subset=["codigo_ibge"]).sort_values("municipio")

    if len(df) < 120:
        raise ValueError(
            f"Regionalização MS retornou apenas {len(df)} municípios de MT. "
            "A fonte pode ter mudado ou a leitura do ZIP não está correta."
        )

    # Campos auxiliares para auditoria: o importador estruturado genérico preservará apenas métricas numéricas,
    # mas o CSV bruto salvo pelo catálogo terá estes campos textuais completos.
    df["municipios_regionalizacao_ms"] = 1
    df["fonte"] = "Dados Abertos/MS - Macrorregião e Região de Saúde"
    df["competencia"] = "fonte_atual"
    df["ano"] = 2026
    return df.reset_index(drop=True)
