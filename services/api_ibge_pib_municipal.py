from __future__ import annotations

import re
from typing import Any

import pandas as pd
import requests

from config.municipios_mt import DEFAULT_MUNICIPIOS

UF_MT = "51"
TIMEOUT = 60
SIDRA_CLASSICA = "https://apisidra.ibge.gov.br/values"


def _somente_numero(valor: Any) -> str:
    return re.sub(r"\D+", "", "" if valor is None else str(valor))


def _normalizar_texto(valor: Any) -> str:
    return " ".join(str(valor or "").strip().split())


def _to_float(valor: Any) -> float | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    if texto in {"", "-", "...", "X", "x", "NaN", "nan", "None"}:
        return None
    # SIDRA geralmente retorna decimal com ponto, mas mantemos tolerância para formato BR.
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")
    texto = re.sub(r"[^0-9\.\-]", "", texto)
    try:
        return float(texto)
    except Exception:
        return None


def _codigo_municipio_mt(valor: Any) -> str:
    codigo = _somente_numero(valor)
    if len(codigo) >= 7:
        return codigo[:7]
    return codigo


def _municipios_oficiais_mt() -> dict[str, str]:
    mapa: dict[str, str] = {}
    for item in DEFAULT_MUNICIPIOS:
        codigo = _codigo_municipio_mt(item.get("codigo_ibge"))
        municipio = _normalizar_texto(item.get("municipio"))
        if codigo and municipio:
            mapa[codigo] = municipio
    return mapa


def _baixar_sidra_pib(periodo: str = "last") -> tuple[list[dict[str, Any]], str]:
    # Tabela 5938: PIB dos Municípios. Variáveis: 37 = PIB a preços correntes; 543 = PIB per capita.
    # n6/all baixa todos os municípios brasileiros; o filtro MT é aplicado localmente por código IBGE iniciado em 51.
    url = f"{SIDRA_CLASSICA}/t/5938/n6/all/v/37,543/p/{periodo}"
    resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "aps-inteligencia-ses-mt/0.23"})
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list) or len(payload) < 2:
        raise RuntimeError("SIDRA retornou payload vazio ou fora do padrão para PIB Municipal.")
    return payload, url


def _cabecalho_sidra(payload: list[dict[str, Any]]) -> dict[str, str]:
    header = payload[0] if payload else {}
    if not isinstance(header, dict):
        return {}
    return {str(k): str(v) for k, v in header.items()}


def _valor_linha(row: dict[str, Any], opcoes: list[str]) -> Any:
    for col in opcoes:
        if col in row:
            return row.get(col)
    return None


def _detectar_colunas(header: dict[str, str], sample: dict[str, Any]) -> dict[str, str | None]:
    # SIDRA clássica costuma trazer códigos D1C/D1N, D2C/D2N..., V/Valor etc.
    todas = set(header.keys()) | set(sample.keys())
    valor_col = "V" if "V" in todas else ("Valor" if "Valor" in todas else None)

    periodo_col = None
    var_codigo_col = None
    var_nome_col = None
    municipio_codigo_col = None
    municipio_nome_col = None

    for col in todas:
        nome = header.get(col, col).lower()
        col_l = col.lower()
        if periodo_col is None and ("ano" in nome or "período" in nome or "periodo" in nome):
            periodo_col = col
        if var_codigo_col is None and ("variável" in nome or "variavel" in nome) and (col_l.endswith("c") or "codigo" in col_l or "código" in nome):
            var_codigo_col = col
        if var_nome_col is None and ("variável" in nome or "variavel" in nome) and (col_l.endswith("n") or "nome" in col_l):
            var_nome_col = col
        if municipio_codigo_col is None and ("município" in nome or "municipio" in nome) and (col_l.endswith("c") or "codigo" in col_l or "código" in nome):
            municipio_codigo_col = col
        if municipio_nome_col is None and ("município" in nome or "municipio" in nome) and (col_l.endswith("n") or "nome" in col_l):
            municipio_nome_col = col

    # Fallback para layout mais comum em /values.
    if periodo_col is None:
        periodo_col = "D1C" if "D1C" in todas else None
    if var_codigo_col is None:
        var_codigo_col = "D2C" if "D2C" in todas else None
    if var_nome_col is None:
        var_nome_col = "D2N" if "D2N" in todas else None

    # Município costuma ser D3 em tabela com período e variável antes da localidade.
    candidatos_cod_mun = [c for c in ["D3C", "D4C", "D5C", "D6C", "D1C", "D2C"] if c in todas]
    candidatos_nom_mun = [c for c in ["D3N", "D4N", "D5N", "D6N", "D1N", "D2N"] if c in todas]
    if municipio_codigo_col is None:
        for c in candidatos_cod_mun:
            codigo = _codigo_municipio_mt(sample.get(c))
            if codigo.startswith(UF_MT) and len(codigo) == 7:
                municipio_codigo_col = c
                break
    if municipio_nome_col is None and municipio_codigo_col:
        possivel = municipio_codigo_col[:-1] + "N" if municipio_codigo_col.endswith("C") else None
        if possivel in todas:
            municipio_nome_col = possivel
    if municipio_nome_col is None:
        for c in candidatos_nom_mun:
            if sample.get(c):
                municipio_nome_col = c
                break

    return {
        "valor": valor_col,
        "periodo": periodo_col,
        "variavel_codigo": var_codigo_col,
        "variavel_nome": var_nome_col,
        "municipio_codigo": municipio_codigo_col,
        "municipio_nome": municipio_nome_col,
    }


def carregar_pib_municipal_ibge_mt(periodo: str = "last") -> pd.DataFrame:
    payload, url = _baixar_sidra_pib(periodo=periodo)
    header = _cabecalho_sidra(payload)
    dados = [row for row in payload[1:] if isinstance(row, dict)]
    if not dados:
        raise RuntimeError("SIDRA PIB Municipal retornou sem linhas de dados.")

    colunas = _detectar_colunas(header, dados[0])
    if not colunas.get("valor") or not colunas.get("municipio_codigo"):
        raise RuntimeError(f"Não foi possível detectar colunas essenciais do SIDRA. Header: {header}. Amostra: {dados[0]}")

    municipios_oficiais = _municipios_oficiais_mt()
    linhas: list[dict[str, Any]] = []

    for row in dados:
        codigo = _codigo_municipio_mt(row.get(colunas["municipio_codigo"]))
        if not codigo.startswith(UF_MT) or len(codigo) != 7:
            continue

        municipio = municipios_oficiais.get(codigo) or _normalizar_texto(row.get(colunas.get("municipio_nome") or ""))
        if not municipio:
            continue

        var_codigo = _somente_numero(row.get(colunas.get("variavel_codigo") or ""))
        var_nome = _normalizar_texto(row.get(colunas.get("variavel_nome") or ""))
        if var_codigo == "37" or "produto interno bruto" in var_nome.lower() and "per capita" not in var_nome.lower():
            indicador = "pib_municipal_precos_correntes"
        elif var_codigo == "543" or "per capita" in var_nome.lower():
            indicador = "pib_per_capita"
        else:
            indicador = f"pib_variavel_{var_codigo or var_nome}".strip("_")

        ano_txt = _somente_numero(row.get(colunas.get("periodo") or ""))
        ano = int(ano_txt[:4]) if len(ano_txt) >= 4 else None
        linhas.append({
            "municipio": municipio,
            "codigo_ibge": codigo,
            "ano": ano,
            "competencia": str(ano or periodo),
            "indicador": indicador,
            "valor": _to_float(row.get(colunas["valor"])),
            "fonte": "IBGE/SIDRA - PIB dos Municípios",
            "url_origem": url,
        })

    out = pd.DataFrame(linhas)
    if out.empty:
        raise RuntimeError("PIB Municipal foi lido, mas nenhum registro de Mato Grosso foi identificado.")

    # Garante rastreabilidade para município novo que ainda pode não constar na tabela SIDRA do PIB.
    presentes = set(out["codigo_ibge"].astype(str))
    ano_ref = int(out["ano"].dropna().max()) if "ano" in out.columns and out["ano"].notna().any() else None
    pendentes = []
    for codigo, municipio in municipios_oficiais.items():
        if codigo not in presentes:
            for indicador in ["pib_municipal_precos_correntes", "pib_per_capita"]:
                pendentes.append({
                    "municipio": municipio,
                    "codigo_ibge": codigo,
                    "ano": ano_ref,
                    "competencia": str(ano_ref or periodo),
                    "indicador": indicador,
                    "valor": None,
                    "fonte": "IBGE/SIDRA - PIB dos Municípios",
                    "url_origem": url,
                    "observacao": "Município ausente no retorno do SIDRA para esta competência; manter como pendente de atualização.",
                    "status_api": "pendente_sem_dado_sidra",
                })
    if pendentes:
        out = pd.concat([out, pd.DataFrame(pendentes)], ignore_index=True)

    out = out.drop_duplicates(subset=["codigo_ibge", "indicador", "competencia"], keep="first")
    out = out.sort_values(["municipio", "indicador"]).reset_index(drop=True)
    return out


def testar_pib_municipal_ibge_mt(periodo: str = "last") -> pd.DataFrame:
    return carregar_pib_municipal_ibge_mt(periodo=periodo)
