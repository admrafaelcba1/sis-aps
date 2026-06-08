"""Serviços de apoio à governança dos indicadores.

Não altera dados nem cria tabelas. Apenas organiza catálogo, verifica a presença de
campos no DataFrame do dashboard e gera resumos para a tela de metodologia.
"""

from __future__ import annotations

import pandas as pd

from config.indicadores_catalogo import (
    CLASSES_PRIORIDADE,
    FONTES_CATALOGO,
    INDICADORES_CATALOGO,
    PESOS_SCORE_ATUAL,
)
from database.queries import read_table


def catalogo_indicadores() -> pd.DataFrame:
    return pd.DataFrame(INDICADORES_CATALOGO)


def catalogo_fontes() -> pd.DataFrame:
    return pd.DataFrame(FONTES_CATALOGO)


def pesos_score() -> pd.DataFrame:
    return pd.DataFrame(PESOS_SCORE_ATUAL)


def classes_prioridade() -> pd.DataFrame:
    return pd.DataFrame(CLASSES_PRIORIDADE)


def _coluna_existe_em_base(campo: str, colunas: set[str]) -> bool:
    if not campo:
        return False
    # Campos alternativos podem vir separados por barra no catálogo.
    alternativas = [c.strip() for c in str(campo).replace("|", "/").split("/") if c.strip()]
    return any(c in colunas for c in alternativas)


def diagnostico_cobertura_indicadores(base_dashboard: pd.DataFrame | None = None) -> pd.DataFrame:
    """Verifica se os campos documentados existem na base carregada.

    A função aceita uma base já carregada para evitar recomputar. Se não for enviada,
    tenta ler a tabela consolidada diretamente do banco.
    """
    catalogo = catalogo_indicadores()
    if base_dashboard is None:
        try:
            base_dashboard = read_table("base_municipal_consolidada")
        except Exception:
            base_dashboard = pd.DataFrame()
    colunas = set(base_dashboard.columns) if isinstance(base_dashboard, pd.DataFrame) and not base_dashboard.empty else set()
    out = catalogo[["eixo", "indicador", "campo_sistema", "confiabilidade", "fonte"]].copy()
    out["campo_encontrado_na_base"] = out["campo_sistema"].apply(lambda c: _coluna_existe_em_base(str(c), colunas))
    out["situacao_operacional"] = out["campo_encontrado_na_base"].map({True: "Campo localizado", False: "Documentado / derivado / verificar"})
    return out


def resumo_governanca(base_dashboard: pd.DataFrame | None = None) -> dict[str, int]:
    ind = catalogo_indicadores()
    fontes = catalogo_fontes()
    diag = diagnostico_cobertura_indicadores(base_dashboard)
    return {
        "indicadores_catalogados": int(len(ind)),
        "fontes_mapeadas": int(len(fontes)),
        "indicadores_consolidados": int((ind["confiabilidade"] == "Consolidado").sum()),
        "indicadores_gerenciais": int(ind["confiabilidade"].astype(str).str.contains("Gerencial", case=False, na=False).sum()),
        "campos_localizados": int(diag["campo_encontrado_na_base"].sum()),
    }


def matriz_fontes_por_status() -> pd.DataFrame:
    fontes = catalogo_fontes()
    if fontes.empty:
        return pd.DataFrame()
    return fontes.groupby(["status"], dropna=False).size().reset_index(name="quantidade").sort_values("quantidade", ascending=False)
