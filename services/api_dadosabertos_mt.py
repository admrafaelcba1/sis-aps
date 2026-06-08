"""Conector do Portal de Dados Abertos de Mato Grosso.

Objetivo inicial: inventariar bases disponíveis no portal CKAN do Estado,
sem ainda misturar esses dados na Base Completa. Essa camada ajuda a decidir
quais conjuntos valem uma integração específica depois.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import requests

CKAN_BASE = "https://dadosabertos.mt.gov.br/api/3/action"
PORTAL_BASE = "https://dadosabertos.mt.gov.br"

TEMAS_PRIORITARIOS = {
    "saude": 5,
    "educacao": 4,
    "infraestrutura": 4,
    "ambiental": 3,
    "economico": 3,
    "seguranca-publica": 2,
    "pessoas": 1,
}

FORMATOS_PRIORITARIOS = {
    "CSV": 5,
    "XLSX": 4,
    "XLS": 4,
    "JSON": 5,
    "GEOJSON": 5,
    "ZIP": 3,
    "SHP": 3,
    "KMZ": 2,
    "PDF": 0,
}

PALAVRAS_APS = [
    "saude", "saúde", "municipio", "município", "municipios", "municípios",
    "saneamento", "agua", "água", "esgoto", "educacao", "educação",
    "vulnerabilidade", "qualidade de vida", "icqv", "indicador", "territorial",
    "populacao", "população", "escola", "seguranca", "segurança", "ambiental",
]


def _ckan_get(action: str, params: dict[str, Any] | None = None, timeout: int = 45) -> dict[str, Any]:
    url = f"{CKAN_BASE}/{action}"
    headers = {
        "User-Agent": "Mozilla/5.0 Plataforma APS SES-MT",
        "Accept": "application/json,text/plain,*/*",
    }
    resp = requests.get(url, params=params or {}, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"CKAN retornou success=false para {action}: {data}")
    return data


def _texto(obj: Any) -> str:
    return "" if obj is None else str(obj).strip()


def _normalizar_grupos(dataset: dict[str, Any]) -> list[str]:
    grupos = []
    for g in dataset.get("groups") or []:
        nome = g.get("name") or g.get("title")
        if nome:
            grupos.append(str(nome).strip())
    return grupos


def _normalizar_tags(dataset: dict[str, Any]) -> list[str]:
    tags = []
    for t in dataset.get("tags") or []:
        nome = t.get("name") or t.get("display_name")
        if nome:
            tags.append(str(nome).strip())
    return tags


def _pontuar_relevancia_aps(dataset: dict[str, Any], recurso: dict[str, Any]) -> tuple[int, str]:
    grupos = _normalizar_grupos(dataset)
    tags = _normalizar_tags(dataset)
    formato = _texto(recurso.get("format") or recurso.get("mimetype") or "").upper()
    texto_busca = " ".join([
        _texto(dataset.get("title")),
        _texto(dataset.get("name")),
        _texto(dataset.get("notes")),
        _texto(recurso.get("name")),
        _texto(recurso.get("description")),
        " ".join(tags),
        " ".join(grupos),
    ]).lower()

    pontos = 0
    motivos = []
    for grupo in grupos:
        p = TEMAS_PRIORITARIOS.get(grupo.lower(), 0)
        if p:
            pontos += p
            motivos.append(f"grupo:{grupo}")

    p_formato = FORMATOS_PRIORITARIOS.get(formato, 1 if formato else 0)
    pontos += p_formato
    if formato:
        motivos.append(f"formato:{formato}")

    achadas = []
    for palavra in PALAVRAS_APS:
        if palavra in texto_busca:
            achadas.append(palavra)
    if achadas:
        pontos += min(8, len(set(achadas)))
        motivos.append("termos:" + ",".join(sorted(set(achadas))[:6]))

    if formato == "PDF":
        pontos = max(0, pontos - 3)
        motivos.append("pdf_baixa_automacao")

    if pontos >= 14:
        classe = "Alta"
    elif pontos >= 8:
        classe = "Média"
    elif pontos >= 4:
        classe = "Baixa"
    else:
        classe = "Muito baixa"
    return pontos, f"{classe} | " + "; ".join(motivos)


def carregar_catalogo_dadosabertos_mt(rows: int = 1000) -> pd.DataFrame:
    """Carrega inventário de datasets/recursos do Portal de Dados Abertos MT.

    Não baixa cada recurso ainda. Apenas cataloga o que existe, com URL,
    formato, organização, grupos e uma pontuação preliminar de utilidade para APS.
    """
    payload = _ckan_get("package_search", {"q": "", "rows": rows, "start": 0})
    result = payload.get("result") or {}
    datasets = result.get("results") or []
    agora = datetime.now().isoformat(timespec="seconds")
    linhas: list[dict[str, Any]] = []

    for ds in datasets:
        org = ds.get("organization") or {}
        grupos = _normalizar_grupos(ds)
        tags = _normalizar_tags(ds)
        recursos = ds.get("resources") or []
        if not recursos:
            recursos = [{}]
        for res in recursos:
            pontos, obs = _pontuar_relevancia_aps(ds, res)
            formato = _texto(res.get("format") or res.get("mimetype") or "").upper()
            dataset_nome = _texto(ds.get("name"))
            linhas.append({
                "dataset_id": _texto(ds.get("id")),
                "dataset_nome": dataset_nome,
                "dataset_titulo": _texto(ds.get("title")) or dataset_nome,
                "dataset_descricao": _texto(ds.get("notes")),
                "organizacao_id": _texto(org.get("name")),
                "organizacao_nome": _texto(org.get("title")) or _texto(org.get("name")),
                "grupos": ", ".join(grupos),
                "tags": ", ".join(tags),
                "recurso_id": _texto(res.get("id")),
                "recurso_nome": _texto(res.get("name")),
                "formato": formato,
                "url": _texto(res.get("url")),
                "mimetype": _texto(res.get("mimetype")),
                "ultima_modificacao": _texto(res.get("last_modified") or ds.get("metadata_modified")),
                "criado_em": _texto(res.get("created") or ds.get("metadata_created")),
                "api_ckan_package_show": f"{CKAN_BASE}/package_show?id={dataset_nome}" if dataset_nome else "",
                "url_dataset_portal": f"{PORTAL_BASE}/dataset/{dataset_nome}" if dataset_nome else "",
                "pontuacao_aps": pontos,
                "relevancia_aps": obs,
                "fonte_consulta": "Portal de Dados Abertos MT / CKAN package_search",
                "atualizado_em": agora,
            })

    df = pd.DataFrame(linhas)
    if df.empty:
        raise RuntimeError("Portal de Dados Abertos MT respondeu, mas nenhum dataset/recurso foi retornado pelo CKAN.")

    df = df.sort_values(["pontuacao_aps", "dataset_titulo", "formato"], ascending=[False, True, True]).reset_index(drop=True)
    return df


def testar_catalogo_dadosabertos_mt() -> dict[str, Any]:
    df = carregar_catalogo_dadosabertos_mt(rows=200)
    grupos = sorted({g.strip() for item in df["grupos"].dropna().astype(str) for g in item.split(",") if g.strip()})
    formatos = df["formato"].replace("", pd.NA).dropna().value_counts().head(10).to_dict()
    alta = int((df["pontuacao_aps"] >= 14).sum())
    media = int(((df["pontuacao_aps"] >= 8) & (df["pontuacao_aps"] < 14)).sum())
    return {
        "ok": True,
        "linhas": int(len(df)),
        "datasets_unicos": int(df["dataset_nome"].nunique()),
        "grupos_detectados": grupos,
        "formatos_top": formatos,
        "recursos_alta_relevancia_aps": alta,
        "recursos_media_relevancia_aps": media,
        "amostra_prioritaria": df[["dataset_titulo", "organizacao_nome", "grupos", "formato", "pontuacao_aps"]].head(10).to_dict("records"),
    }
