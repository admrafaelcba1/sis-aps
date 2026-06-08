from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

import pandas as pd
import requests

from config.municipios_mt import DEFAULT_MUNICIPIOS

UF_MT = "51"
CODIGO_BOA_ESPERANCA_NORTE = "5101837"
MUNICIPIO_BOA_ESPERANCA_NORTE = "Boa Esperança do Norte"
URL_MALHA_MT_MUNICIPIOS = (
    "https://servicodados.ibge.gov.br/api/v3/malhas/estados/51"
    "?formato=application/vnd.geo+json&qualidade=minima&intrarregiao=municipio"
)
URL_BOA_ESPERANCA_NORTE_IBGE = "https://www.ibge.gov.br/cidades-e-estados/mt/boa-esperanca-do-norte.html"


def _codigo_limpo(valor: Any) -> str:
    import re

    digitos = re.sub(r"\D", "", "" if valor is None else str(valor))
    if len(digitos) >= 7:
        return digitos[:7]
    return digitos


def _normalizar_nome(valor: Any) -> str:
    import unicodedata

    texto = str(valor or "").strip().upper()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    return " ".join(texto.split())


def _municipios_mt_oficiais() -> set[str]:
    return {_normalizar_nome(item.get("municipio")) for item in DEFAULT_MUNICIPIOS}


def _mapa_municipios_esperados() -> dict[str, str]:
    mapa: dict[str, str] = {}
    for item in DEFAULT_MUNICIPIOS:
        codigo = _codigo_limpo(item.get("codigo_ibge"))
        municipio = str(item.get("municipio") or "").strip()
        if codigo and municipio:
            mapa[codigo] = municipio
    # Defesa para versões antigas de DEFAULT_MUNICIPIOS sem o código dentro do item.
    mapa.setdefault(CODIGO_BOA_ESPERANCA_NORTE, MUNICIPIO_BOA_ESPERANCA_NORTE)
    return mapa


def _iter_coords(geometry: dict[str, Any]) -> Iterable[tuple[float, float]]:
    """Itera coordenadas GeoJSON como pares (lon, lat), suportando Polygon/MultiPolygon."""
    if not geometry:
        return []

    coords = geometry.get("coordinates") or []
    pontos: list[tuple[float, float]] = []

    def walk(obj):
        if isinstance(obj, (list, tuple)):
            if len(obj) >= 2 and all(isinstance(v, (int, float)) for v in obj[:2]):
                lon = float(obj[0])
                lat = float(obj[1])
                pontos.append((lon, lat))
            else:
                for item in obj:
                    walk(item)

    walk(coords)
    return pontos


def _extrair_props(feature: dict[str, Any]) -> tuple[str, str]:
    props = feature.get("properties") or {}
    codigo = (
        props.get("codarea")
        or props.get("CD_MUN")
        or props.get("CD_GEOCMU")
        or props.get("id")
        or props.get("codigo")
        or props.get("geocodigo")
        or feature.get("id")
    )
    nome = (
        props.get("nome")
        or props.get("NM_MUN")
        or props.get("NM_MUNICIP")
        or props.get("municipio")
        or props.get("name")
        or ""
    )
    return _codigo_limpo(codigo), str(nome or "").strip()


def _linha_sem_malha(codigo_ibge: str, municipio: str, agora: str, motivo: str) -> dict[str, Any]:
    return {
        "codigo_ibge": codigo_ibge,
        "municipio": municipio,
        "nivel_geografico": "municipio_sem_malha_ibge",
        "latitude_centroide": None,
        "longitude_centroide": None,
        "min_latitude": None,
        "max_latitude": None,
        "min_longitude": None,
        "max_longitude": None,
        "quantidade_pontos": 0,
        "geometry_json": json.dumps({"status": "sem_malha", "motivo": motivo}, ensure_ascii=False),
        "fonte_url": URL_BOA_ESPERANCA_NORTE_IBGE,
        "atualizado_em": agora,
    }


def _montar_dataframe_geojson(data: dict[str, Any], fonte_url: str) -> pd.DataFrame:
    features = data.get("features") or []
    municipios_oficiais = _municipios_mt_oficiais()
    mapa_esperados = _mapa_municipios_esperados()
    agora = datetime.now().isoformat(timespec="seconds")
    registros: list[dict[str, Any]] = []

    for feature in features:
        codigo_ibge, municipio = _extrair_props(feature)
        geometry = feature.get("geometry") or {}
        pontos = list(_iter_coords(geometry))

        if not codigo_ibge.startswith(UF_MT):
            continue
        if municipio and _normalizar_nome(municipio) not in municipios_oficiais:
            # Mantém a validação por código como principal, mas evita nomes fora de MT.
            pass

        # Se o nome vier vazio/defasado na malha, usa o cadastro oficial interno pelo código.
        municipio = mapa_esperados.get(codigo_ibge, municipio)

        lons = [p[0] for p in pontos]
        lats = [p[1] for p in pontos]
        registros.append(
            {
                "codigo_ibge": codigo_ibge,
                "municipio": municipio,
                "nivel_geografico": "municipio",
                "latitude_centroide": sum(lats) / len(lats) if lats else None,
                "longitude_centroide": sum(lons) / len(lons) if lons else None,
                "min_latitude": min(lats) if lats else None,
                "max_latitude": max(lats) if lats else None,
                "min_longitude": min(lons) if lons else None,
                "max_longitude": max(lons) if lons else None,
                "quantidade_pontos": len(pontos),
                "geometry_json": json.dumps(geometry, ensure_ascii=False),
                "fonte_url": fonte_url,
                "atualizado_em": agora,
            }
        )

    df = pd.DataFrame(registros)
    if not df.empty:
        df = df.drop_duplicates(subset=["codigo_ibge"], keep="first")

    codigos_lidos = set(df["codigo_ibge"].astype(str).tolist()) if not df.empty else set()
    # A API de malhas do IBGE pode retornar 141 municípios enquanto o cadastro oficial já reconhece
    # Boa Esperança do Norte (5101837), instalado em 01/01/2025. Mantemos o município no sistema
    # como pendente de malha, sem inventar geometria/centroide.
    if CODIGO_BOA_ESPERANCA_NORTE not in codigos_lidos:
        df = pd.concat(
            [
                df,
                pd.DataFrame(
                    [
                        _linha_sem_malha(
                            CODIGO_BOA_ESPERANCA_NORTE,
                            MUNICIPIO_BOA_ESPERANCA_NORTE,
                            agora,
                            "Município já reconhecido pelo IBGE, mas ainda não retornado na API de malhas estadual.",
                        )
                    ]
                ),
            ],
            ignore_index=True,
        )

    if not df.empty:
        df = df.sort_values("municipio").reset_index(drop=True)
    return df


def carregar_malhas_geograficas_ibge_mt() -> pd.DataFrame:
    """Carrega a malha municipal de Mato Grosso via API oficial do IBGE.

    A carga retorna um registro por município, com geometria em GeoJSON e campos
    derivados de apoio ao mapa: centroide aproximado e bounding box.

    Observação técnica: se a API estadual de malhas ainda não retornar Boa Esperança
    do Norte, o município é incluído como registro pendente de malha, sem coordenadas
    inventadas, para manter o cadastro municipal de MT com 142 municípios.
    """
    response = requests.get(URL_MALHA_MT_MUNICIPIOS, timeout=60)
    response.raise_for_status()
    data = response.json()
    df = _montar_dataframe_geojson(data, URL_MALHA_MT_MUNICIPIOS)

    if df.empty:
        raise RuntimeError("A API de malhas do IBGE respondeu sem municípios de Mato Grosso.")
    if len(df) < 140:
        raise RuntimeError(f"A API de malhas retornou apenas {len(df)} município(s) de MT; esperado próximo de 142.")
    return df


def testar_malhas_geograficas_ibge_mt() -> dict[str, Any]:
    df = carregar_malhas_geograficas_ibge_mt()
    esperados = _mapa_municipios_esperados()
    codigos_lidos = set(df["codigo_ibge"].astype(str).tolist()) if not df.empty else set()
    ausentes = [nome for codigo, nome in esperados.items() if codigo not in codigos_lidos]
    pendentes_malha = df[df["nivel_geografico"].astype(str).str.contains("sem_malha", na=False)]
    return {
        "ok": True,
        "fonte": "IBGE Malhas Geográficas",
        "municipios_lidos": int(len(df)),
        "municipios_esperados_cadastro_mt": int(len(esperados)),
        "municipios_ausentes": ausentes,
        "municipios_sem_geometria": pendentes_malha["municipio"].dropna().tolist(),
        "colunas": list(df.columns),
        "url": URL_MALHA_MT_MUNICIPIOS,
        "observacao": "Carga direta por API, sem upload manual de arquivo. Município novo pode ser incluído como pendente de malha, sem coordenadas inventadas.",
    }
