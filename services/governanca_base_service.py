from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

import pandas as pd

from database.queries import read_table


def _safe_read_table(nome: str) -> pd.DataFrame:
    try:
        return read_table(nome)
    except Exception:
        return pd.DataFrame()


def _to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _coverage(df: pd.DataFrame, colunas: List[str]) -> float:
    if df.empty or not colunas:
        return 0.0
    existentes = [c for c in colunas if c in df.columns]
    if not existentes:
        return 0.0
    total = len(df) * len(existentes)
    if total == 0:
        return 0.0
    preenchidos = 0
    for col in existentes:
        serie = df[col]
        pend = serie.isna() | serie.astype(str).str.strip().isin(["", "None", "nan", "<NA>"])
        preenchidos += int((~pend).sum())
    return round((preenchidos / total) * 100, 1)


def _sum_col(df: pd.DataFrame, coluna: str) -> float:
    if df.empty or coluna not in df.columns:
        return 0.0
    return float(_to_num(df[coluna]).sum(skipna=True))


def _count_non_null(df: pd.DataFrame, coluna: str) -> int:
    if df.empty or coluna not in df.columns:
        return 0
    serie = df[coluna]
    pend = serie.isna() | serie.astype(str).str.strip().isin(["", "None", "nan", "<NA>"])
    return int((~pend).sum())


def _last_import(importacoes: pd.DataFrame, terms: List[str]) -> str:
    if importacoes.empty:
        return "—"
    df = importacoes.copy()
    text_cols = [c for c in ["fonte_codigo", "nome_arquivo", "tipo_base", "status", "mensagem", "caminho_arquivo"] if c in df.columns]
    if not text_cols:
        return "—"
    mask = pd.Series(False, index=df.index)
    for col in text_cols:
        s = df[col].astype(str).str.lower()
        for termo in terms:
            mask = mask | s.str.contains(str(termo).lower(), na=False)
    achados = df[mask].copy()
    if achados.empty:
        return "—"
    data_col = None
    for candidato in ["criado_em", "data_importacao", "atualizado_em"]:
        if candidato in achados.columns:
            data_col = candidato
            break
    if data_col:
        achados[data_col] = achados[data_col].astype(str)
        achados = achados.sort_values(data_col, ascending=False)
        return str(achados.iloc[0].get(data_col, "—"))
    return "registrado"



def _inep_stats(indicadores: pd.DataFrame) -> dict:
    if indicadores is None or indicadores.empty or "indicador" not in indicadores.columns:
        return {"municipios": 0, "escolas": 0, "aee": 0, "cobertura": 0.0, "status": "Pendente"}
    df = indicadores[indicadores["indicador"].astype(str).isin([
        "escolas_total", "escolas_urbanas", "escolas_rurais", "escolas_indigenas", "escolas_quilombolas",
        "escolas_educacao_especial_aee", "matriculas_total", "matriculas_educacao_especial",
    ])].copy()
    if df.empty:
        return {"municipios": 0, "escolas": 0, "aee": 0, "cobertura": 0.0, "status": "Pendente"}
    df["valor"] = pd.to_numeric(df.get("valor"), errors="coerce").fillna(0)
    escolas = df[df["indicador"].eq("escolas_total")].copy()
    municipios = int((escolas["valor"] > 0).sum()) if not escolas.empty else 0
    total_escolas = float(escolas["valor"].sum()) if not escolas.empty else 0.0
    aee = float(df[df["indicador"].eq("escolas_educacao_especial_aee")]["valor"].sum())
    cobertura = round((municipios / 142) * 100, 1) if municipios else 0.0
    status = "Funcional" if municipios >= 140 and total_escolas > 0 else "Em auditoria" if total_escolas > 0 else "Pendente"
    return {"municipios": municipios, "escolas": total_escolas, "aee": aee, "cobertura": cobertura, "status": status}

def montar_governanca_base() -> Dict[str, Any]:
    base = _safe_read_table("base_municipal_consolidada")
    municipios = _safe_read_table("municipios")
    equipes = _safe_read_table("equipes_aps")
    profissionais = _safe_read_table("profissionais_cnes")
    indicadores = _safe_read_table("indicadores_municipais")
    estabelecimentos = _safe_read_table("estabelecimentos_saude")
    importacoes = _safe_read_table("importacoes")
    inep_stats = _inep_stats(indicadores)

    total_municipios = len(base) if not base.empty else len(municipios)
    pop_total = _sum_col(base, "populacao")
    area_total = _sum_col(base, "area_km2")
    equipes_total = _sum_col(base, "total_equipes_aps")
    prof_total = _sum_col(base, "total_profissionais_aps")
    ubs_total = _sum_col(base, "total_ubs")
    leitos_total = _sum_col(base, "total_leitos_sus")
    nv_total = _sum_col(base, "nascidos_vivos")
    obitos_total = _sum_col(base, "obitos")

    coords = 0
    if not base.empty and "latitude" in base.columns and "longitude" in base.columns:
        coords = int((base["latitude"].notna() & base["longitude"].notna()).sum())

    camadas = [
        {
            "camada": "IBGE territorial municipal",
            "fonte_principal": "IBGE 2025 / lista canônica MT",
            "competencia": "2025",
            "status": "Validada" if total_municipios == 142 and int(round(pop_total)) == 3893659 else "Atenção",
            "cobertura_%": _coverage(base, ["codigo_ibge", "municipio", "populacao", "area_km2", "densidade_hab_km2"]),
            "ultimo_processamento": _last_import(importacoes, ["ibge", "populacao", "area"]),
            "uso_recomendado": "Pode usar em dashboard, diagnóstico e indicadores derivados.",
            "observacao": f"Municípios: {total_municipios}; população: {int(round(pop_total)) if pop_total else 0}; área: {round(area_total, 3) if area_total else 0} km².",
        },
        {
            "camada": "Georreferenciamento municipal",
            "fonte_principal": "IBGE Localidades/Malhas + centroides municipais",
            "competencia": "2025/atual",
            "status": "Parcial" if coords < total_municipios else "Validada",
            "cobertura_%": _coverage(base, ["latitude", "longitude"]),
            "ultimo_processamento": _last_import(importacoes, ["geo", "malha", "georrefer"]),
            "uso_recomendado": "Pode usar em mapas municipais; manter alerta se houver município sem coordenada.",
            "observacao": f"Coordenadas preenchidas: {coords}/{total_municipios}.",
        },
        {
            "camada": "CNES estabelecimentos/UBS",
            "fonte_principal": "CNES/DATASUS",
            "competencia": "conforme última carga",
            "status": "Funcional" if ubs_total > 0 else "Pendente",
            "cobertura_%": _coverage(base, ["total_ubs"]),
            "ultimo_processamento": _last_import(importacoes, ["cnes", "ubs", "estabelecimento"]),
            "uso_recomendado": "Pode usar com rótulo técnico claro: UBS/unidades APS importadas.",
            "observacao": f"Total consolidado: {int(round(ubs_total)) if ubs_total else 0}.",
        },
        {
            "camada": "CNES equipes APS/INE",
            "fonte_principal": "EQUIPESBRASIL_202605.ZIP / EQUIPESValidasBrasil.txt",
            "competencia": "2026/05",
            "status": "Validada" if int(round(equipes_total)) == 2040 else "Atenção",
            "cobertura_%": _coverage(base, ["total_equipes_aps", "total_equipes_70", "total_equipes_71", "total_equipes_72", "total_equipes_73", "total_equipes_74", "total_equipes_76"]),
            "ultimo_processamento": _last_import(importacoes, ["equipe", "cnes_equipes", "equipesbrasil"]),
            "uso_recomendado": "Pode usar. Representa equipes válidas por INE nos códigos 70, 71, 72, 73, 74 e 76.",
            "observacao": f"Equipes consolidadas: {int(round(equipes_total)) if equipes_total else 0}; registros na tabela equipes_aps: {len(equipes)}.",
        },
        {
            "camada": "CNES profissionais vinculados às equipes",
            "fonte_principal": "EQUIPESBRASIL_202605.ZIP / ProfissionaisEquipesBrasil.txt",
            "competencia": "2026/05",
            "status": "Validada" if prof_total > 0 else "Pendente",
            "cobertura_%": _coverage(base, ["total_profissionais_aps"]),
            "ultimo_processamento": _last_import(importacoes, ["profissionais", "equipesbrasil"]),
            "uso_recomendado": "Pode usar como vínculos profissional-equipe, não como profissional único.",
            "observacao": f"Vínculos consolidados: {int(round(prof_total)) if prof_total else 0}; registros na tabela profissionais_cnes: {len(profissionais)}.",
        },
        {
            "camada": "DATASUS SINASC",
            "fonte_principal": "DATASUS/SINASC",
            "competencia": "conforme última carga",
            "status": "Funcional" if nv_total > 0 else "Pendente",
            "cobertura_%": _coverage(base, ["nascidos_vivos"]),
            "ultimo_processamento": _last_import(importacoes, ["sinasc", "nascidos"]),
            "uso_recomendado": "Pode usar em indicadores derivados e contexto municipal.",
            "observacao": f"Nascidos vivos consolidados: {int(round(nv_total)) if nv_total else 0}.",
        },
        {
            "camada": "DATASUS SIM",
            "fonte_principal": "DATASUS/SIM",
            "competencia": "conforme última carga",
            "status": "Funcional" if obitos_total > 0 else "Pendente",
            "cobertura_%": _coverage(base, ["obitos", "obitos_infantis"]),
            "ultimo_processamento": _last_import(importacoes, ["sim", "obitos", "mortalidade"]),
            "uso_recomendado": "Pode usar em indicadores derivados; conferir competência antes de relatório oficial.",
            "observacao": f"Óbitos consolidados: {int(round(obitos_total)) if obitos_total else 0}.",
        },
        {
            "camada": "CNES leitos",
            "fonte_principal": "CNES/DATASUS",
            "competencia": "conforme última carga",
            "status": "Em auditoria" if leitos_total > 0 else "Pendente",
            "cobertura_%": _coverage(base, ["total_leitos_sus"]),
            "ultimo_processamento": _last_import(importacoes, ["leitos", "cnes"]),
            "uso_recomendado": "Usar como capacidade instalada/leitos SUS com identificação da fonte e auditoria por extremos.",
            "observacao": f"Leitos SUS consolidados: {int(round(leitos_total)) if leitos_total else 0}.",
        },
        {
            "camada": "INEP / Censo Escolar",
            "fonte_principal": "INEP Microdados do Censo Escolar",
            "competencia": "2024 ou última carga",
            "status": inep_stats.get("status", "Pendente"),
            "cobertura_%": inep_stats.get("cobertura", 0.0),
            "ultimo_processamento": _last_import(importacoes, ["inep", "censo_escolar"]),
            "uso_recomendado": "Usar como camada socioeducacional/intersetorial em diagnóstico territorial e indicadores derivados, com ressalva para município novo sem escola na competência.",
            "observacao": f"Cobertura: {inep_stats.get('municipios', 0)}/142 municípios; escolas: {int(round(inep_stats.get('escolas', 0))) if inep_stats.get('escolas', 0) else 0}; AEE/educação especial: {int(round(inep_stats.get('aee', 0))) if inep_stats.get('aee', 0) else 0}.",
        },
        {
            "camada": "MDS socioassistencial",
            "fonte_principal": "MDS / CadÚnico / Bolsa Família / BPC",
            "competencia": "—",
            "status": "Suspensa",
            "cobertura_%": _coverage(base, ["cadunico_familias", "bolsa_familia_familias", "bpc_total"]),
            "ultimo_processamento": _last_import(importacoes, ["mds", "cadunico", "bolsa", "bpc"]),
            "uso_recomendado": "Não usar na primeira versão; só retomar com API/arquivo tabular estável.",
            "observacao": "Carga automática não retornou dados tabulares confiáveis.",
        },
        {
            "camada": "IBGE/SIDRA socioeconômico",
            "fonte_principal": "IBGE/SIDRA Censo 2022",
            "competencia": "2022",
            "status": "Pendente",
            "cobertura_%": _coverage(base, ["perfil_urbano_rural", "taxa_alfabetizacao", "renda_censo_2022", "saneamento_censo_2022"]),
            "ultimo_processamento": _last_import(importacoes, ["sidra", "renda", "saneamento", "alfabetizacao"]),
            "uso_recomendado": "Não consolidar até validar formato e coerência por município.",
            "observacao": "Retornos anteriores vieram instáveis ou incompatíveis. Deve entrar via staging/validação.",
        },
        {
            "camada": "PNI/imunização",
            "fonte_principal": "Dados abertos/PNI",
            "competencia": "—",
            "status": "Suspensa",
            "cobertura_%": 0.0,
            "ultimo_processamento": _last_import(importacoes, ["pni", "imunizacao", "vacina"]),
            "uso_recomendado": "Não usar agora; fonte automática instável/bloqueada.",
            "observacao": "Retomar apenas com endpoint ou CSV estável.",
        },
    ]
    camadas_df = pd.DataFrame(camadas)

    resumo = {
        "municipios": total_municipios,
        "populacao": int(round(pop_total)) if pop_total else 0,
        "area_km2": round(area_total, 3) if area_total else 0,
        "equipes_aps": int(round(equipes_total)) if equipes_total else 0,
        "profissionais_vinculos": int(round(prof_total)) if prof_total else 0,
        "ubs": int(round(ubs_total)) if ubs_total else 0,
        "leitos_sus": int(round(leitos_total)) if leitos_total else 0,
        "coordenadas": coords,
        "camadas_validas": int(camadas_df["status"].isin(["Validada", "Funcional"]).sum()),
        "camadas_auditoria": int(camadas_df["status"].isin(["Em auditoria", "Parcial", "Atenção"]).sum()),
        "camadas_pendentes": int(camadas_df["status"].isin(["Pendente", "Suspensa"]).sum()),
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
    }

    uso_dashboard = camadas_df[["camada", "status", "uso_recomendado", "observacao"]].copy()
    uso_dashboard["pode_usar_dashboard"] = uso_dashboard["status"].isin(["Validada", "Funcional", "Em auditoria", "Parcial"])

    return {
        "resumo": resumo,
        "camadas": camadas_df,
        "uso_dashboard": uso_dashboard,
        "importacoes": importacoes,
        "indicadores": indicadores,
        "base": base,
    }
