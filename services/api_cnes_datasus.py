"""Conectores CNES/DATASUS migrados para a arquitetura nova.

Nesta v07, as rotinas funcionais do sistema antigo são chamadas por uma ponte
controlada. Assim o novo sistema passa a executar o bloco CNES/DATASUS pelo
catálogo de APIs sem copiar a tela antiga para o menu.
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
        raise ImportError("Não foi possível carregar o arquivo legado de conectores CNES/DATASUS.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call_legacy(nome_funcao: str, **kwargs: Any) -> pd.DataFrame:
    modulo = _load_legacy_connectors()
    funcao = getattr(modulo, nome_funcao, None)
    if not callable(funcao):
        raise AttributeError(f"Função {nome_funcao} não encontrada em legacy/conectores_apis_ubs_antigo.py.")
    resultado = funcao(**kwargs)
    if isinstance(resultado, pd.DataFrame):
        return resultado.copy()
    if isinstance(resultado, list):
        return pd.DataFrame(resultado)
    if isinstance(resultado, dict):
        return pd.DataFrame([resultado])
    return pd.DataFrame({"resultado": [str(resultado)]})


def carregar_cnes_estabelecimentos_ubs_mt() -> pd.DataFrame:
    """Carrega estabelecimentos/UBS do CNES usando a rotina validada no sistema antigo."""
    return _call_legacy("carregar_cnes_estabelecimentos_ubs_mt")


def carregar_leitos_sus_mt() -> pd.DataFrame:
    """Carrega leitos SUS do CNES/Dados Abertos usando a rotina validada no sistema antigo."""
    return _call_legacy("carregar_leitos_sus_mt")


def carregar_sinasc_nascidos_vivos_mt(ano: int = 2024) -> pd.DataFrame:
    """Carrega e agrega SINASC por município de Mato Grosso."""
    return _call_legacy("carregar_sinasc_nascidos_vivos_mt", ano=ano)


def carregar_sim_mortalidade_mt(ano: int = 2024) -> pd.DataFrame:
    """Carrega e agrega SIM por município de Mato Grosso."""
    return _call_legacy("carregar_sim_mortalidade_mt", ano=ano)
