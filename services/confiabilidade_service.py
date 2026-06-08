
from __future__ import annotations

import pandas as pd

from database.queries import read_table
from services.georreferenciamento_service import (
    qualificar_unidades_aps_georreferenciadas,
    carregar_coordenadas_ubs_validadas,
    carregar_ajustes_territoriais_manuais,
    diagnosticar_territorios_suspeitos_divisa,
)
from services.qualidade_dados_service import deduplicar_estabelecimentos_saude


def _num(valor, default: float = 0.0) -> float:
    try:
        if pd.isna(valor):
            return default
        return float(valor)
    except Exception:
        return default


def _pct(parte: float, total: float) -> float:
    total = _num(total)
    if total <= 0:
        return 0.0
    return round(_num(parte) / total * 100, 1)


def _classificar_confiabilidade(pct_ubs_geo: float, pendencias_ubs: int, suspeitos: int, ajustes: int) -> tuple[str, str]:
    pct_ubs_geo = _num(pct_ubs_geo)
    pendencias_ubs = int(_num(pendencias_ubs))
    suspeitos = int(_num(suspeitos))
    if pct_ubs_geo >= 90 and pendencias_ubs <= 2 and suspeitos <= 3:
        return "Alta confiabilidade", "Dados aptos para leitura gerencial inicial, mantendo validação contínua."
    if pct_ubs_geo >= 70 and pendencias_ubs <= 10 and suspeitos <= 15:
        return "Média confiabilidade", "Usar com atenção; existem pendências ou alertas que devem ser saneados."
    if pct_ubs_geo <= 0 and pendencias_ubs == 0 and suspeitos == 0:
        return "Sem avaliação", "Não há dados suficientes para classificar a confiabilidade."
    return "Baixa confiabilidade / em validação", "Evitar decisão isolada; priorizar saneamento de coordenadas e validação territorial."


def montar_confiabilidade_base() -> dict:
    qual = qualificar_unidades_aps_georreferenciadas()
    unidades = qual.get("unidades", pd.DataFrame()).copy()
    sem_geo = qual.get("sem_coordenadas", pd.DataFrame()).copy()
    resumo_ubs = qual.get("resumo_municipal", pd.DataFrame()).copy()
    diag_ubs = qual.get("diagnostico", {}) or {}

    validadas = carregar_coordenadas_ubs_validadas()
    ajustes = carregar_ajustes_territoriais_manuais()

    try:
        suspeitos = diagnosticar_territorios_suspeitos_divisa(20.0)
    except Exception:
        suspeitos = pd.DataFrame()

    # Pontos bloqueados do mapa estratégico, se a função estiver disponível.
    try:
        from services.georreferenciamento_service import obter_inconsistencias_pontos_mapa
        pontos_bloqueados = obter_inconsistencias_pontos_mapa(limite=10000)
    except Exception:
        pontos_bloqueados = pd.DataFrame()

    total_unidades = int(_num(diag_ubs.get("total_unidades")))
    com_geo = int(_num(diag_ubs.get("com_coordenadas_validas")))
    sem_geo_total = int(_num(diag_ubs.get("sem_coordenadas_validas")))
    pct_geo = _pct(com_geo, total_unidades)
    validadas_ok = 0
    if isinstance(validadas, pd.DataFrame) and not validadas.empty and "coordenada_validada_ok" in validadas.columns:
        validadas_ok = int(validadas["coordenada_validada_ok"].astype(bool).sum())

    indicadores = pd.DataFrame([
        {"indicador": "UBS/unidades únicas por CNES", "valor": total_unidades, "leitura": "Base deduplicada por CNES."},
        {"indicador": "UBS com coordenada válida", "valor": com_geo, "leitura": "Entram no cálculo de distância, observada a validação de município/malha."},
        {"indicador": "UBS pendentes de coordenada", "valor": sem_geo_total, "leitura": "Devem ser priorizadas na planilha de validação."},
        {"indicador": "Percentual georreferenciado", "valor": f"{pct_geo:.1f}%", "leitura": "Quanto maior, mais segura a leitura territorial."},
        {"indicador": "Coordenadas manuais validadas carregadas", "valor": validadas_ok, "leitura": "Têm prioridade sobre API/base automática."},
        {"indicador": "Territórios suspeitos para validação", "valor": len(suspeitos), "leitura": "Possíveis casos de divisa, referência intermunicipal ou município textual impreciso."},
        {"indicador": "Ajustes territoriais manuais cadastrados", "valor": len(ajustes), "leitura": "Correções rastreáveis aplicadas por setor/localidade."},
        {"indicador": "Pontos bloqueados do mapa estratégico", "valor": len(pontos_bloqueados), "leitura": "Não entram no mapa principal até validação/correção."},
    ])

    # Confiabilidade por município
    municipios = pd.DataFrame()
    if not resumo_ubs.empty:
        municipios = resumo_ubs.copy()
        # Padroniza nomes esperados do resumo.
        col_mun = "municipio" if "municipio" in municipios.columns else municipios.columns[0]
        municipios["municipio"] = municipios[col_mun].astype(str)

        # Identifica totais por nomes prováveis.
        total_col = next((c for c in municipios.columns if c in ["total_unidades", "unidades_total", "ubs_total", "total"]), None)
        geo_col = next((c for c in municipios.columns if c in ["com_coordenadas_validas", "unidades_com_coordenada", "georreferenciadas"]), None)
        pend_col = next((c for c in municipios.columns if c in ["sem_coordenadas_validas", "sem_coordenada", "pendentes"]), None)

        if total_col is None:
            if not unidades.empty and "municipio" in unidades.columns:
                total_m = unidades.groupby("municipio").size().reset_index(name="total_unidades_calc")
                municipios = municipios.merge(total_m, on="municipio", how="outer")
                total_col = "total_unidades_calc"
            else:
                municipios["total_unidades_calc"] = 0
                total_col = "total_unidades_calc"

        if geo_col is None:
            if not unidades.empty and "municipio" in unidades.columns and "status_georreferencia" in unidades.columns:
                geo_m = unidades[unidades["status_georreferencia"].astype(str).str.contains("Georreferenciada", na=False)].groupby("municipio").size().reset_index(name="com_coordenadas_validas_calc")
                municipios = municipios.merge(geo_m, on="municipio", how="left")
                geo_col = "com_coordenadas_validas_calc"
            else:
                municipios["com_coordenadas_validas_calc"] = 0
                geo_col = "com_coordenadas_validas_calc"

        if pend_col is None:
            municipios["sem_coordenadas_validas_calc"] = pd.to_numeric(municipios[total_col], errors="coerce").fillna(0) - pd.to_numeric(municipios[geo_col], errors="coerce").fillna(0)
            pend_col = "sem_coordenadas_validas_calc"

        municipios["total_ubs_unicas"] = pd.to_numeric(municipios[total_col], errors="coerce").fillna(0).astype(int)
        municipios["ubs_com_coordenada"] = pd.to_numeric(municipios[geo_col], errors="coerce").fillna(0).astype(int)
        municipios["ubs_pendentes_coordenada"] = pd.to_numeric(municipios[pend_col], errors="coerce").fillna(0).astype(int)
        municipios["percentual_ubs_georreferenciadas"] = municipios.apply(lambda r: _pct(r["ubs_com_coordenada"], r["total_ubs_unicas"]), axis=1)

        if isinstance(suspeitos, pd.DataFrame) and not suspeitos.empty and "municipio" in suspeitos.columns:
            sus_m = suspeitos.groupby("municipio").size().reset_index(name="territorios_suspeitos_validacao")
            municipios = municipios.merge(sus_m, on="municipio", how="left")
        else:
            municipios["territorios_suspeitos_validacao"] = 0

        if isinstance(ajustes, pd.DataFrame) and not ajustes.empty and "municipio_validado" in ajustes.columns:
            aj_m = ajustes.groupby("municipio_validado").size().reset_index(name="ajustes_territoriais_manuais")
            aj_m = aj_m.rename(columns={"municipio_validado": "municipio"})
            municipios = municipios.merge(aj_m, on="municipio", how="left")
        else:
            municipios["ajustes_territoriais_manuais"] = 0

        municipios["territorios_suspeitos_validacao"] = pd.to_numeric(municipios["territorios_suspeitos_validacao"], errors="coerce").fillna(0).astype(int)
        municipios["ajustes_territoriais_manuais"] = pd.to_numeric(municipios["ajustes_territoriais_manuais"], errors="coerce").fillna(0).astype(int)
        cls = municipios.apply(
            lambda r: _classificar_confiabilidade(
                r["percentual_ubs_georreferenciadas"],
                r["ubs_pendentes_coordenada"],
                r["territorios_suspeitos_validacao"],
                r["ajustes_territoriais_manuais"],
            ),
            axis=1,
        )
        municipios["selo_confiabilidade"] = [c[0] for c in cls]
        municipios["leitura_confiabilidade"] = [c[1] for c in cls]
        municipios = municipios[[
            "municipio", "total_ubs_unicas", "ubs_com_coordenada", "ubs_pendentes_coordenada",
            "percentual_ubs_georreferenciadas", "territorios_suspeitos_validacao",
            "ajustes_territoriais_manuais", "selo_confiabilidade", "leitura_confiabilidade"
        ]].sort_values(
            ["selo_confiabilidade", "ubs_pendentes_coordenada", "territorios_suspeitos_validacao"],
            ascending=[True, False, False]
        ).reset_index(drop=True)

    pendencias = {
        "ubs_sem_coordenada": sem_geo,
        "territorios_suspeitos": suspeitos,
        "pontos_bloqueados": pontos_bloqueados,
        "coordenadas_validadas": validadas,
        "ajustes_territoriais": ajustes,
    }

    return {
        "indicadores": indicadores,
        "confiabilidade_municipal": municipios,
        "pendencias": pendencias,
        "diagnostico_ubs": diag_ubs,
    }
