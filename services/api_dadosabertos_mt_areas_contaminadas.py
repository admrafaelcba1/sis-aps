"""Conector Dados Abertos MT/SEMA — Gestão de Áreas Contaminadas.

Camada ambiental de apoio à APS. A base atualmente mais promissora no catálogo
estadual é o arquivo XLSX "Acidentes com resíduos perigosos - 2024.xlsx".
Este conector mantém a integração como camada própria, sem consolidar na base
municipal completa automaticamente.
"""

from __future__ import annotations

import io
import json
import re
from datetime import datetime
from typing import Any

import pandas as pd
import requests

from database.connection import db_session

FONTE = "DADOSABERTOS_MT_AREAS_CONTAMINADAS_SEMA"
URL_FALLBACK = (
    "https://dadosabertos.mt.gov.br/dataset/f6eae5fa-321d-4f86-9e16-8a91f4c55425/"
    "resource/cc4b6137-3eeb-4af3-a3b3-a6aed4297f26/download/"
    "acidentes-com-residuos-perigosos-2024.xlsx"
)
DATASET_PORTAL = "https://dadosabertos.mt.gov.br/dataset/gestao-de-areas-contaminadas"

PALAVRAS_DATASET = ["gestão de áreas contaminadas", "areas contaminadas", "áreas contaminadas", "resíduos perigosos", "residuos perigosos"]


def _normalizar_coluna(nome: Any) -> str:
    import unicodedata
    txt = str(nome or "").strip().lower()
    txt = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode("ascii")
    txt = re.sub(r"[^a-z0-9]+", "_", txt).strip("_")
    return txt or "coluna"


def _normalizar_municipio(valor: Any) -> str:
    if valor is None or pd.isna(valor):
        return ""
    txt = str(valor).replace("\xa0", " ").strip()
    txt = re.sub(r"\s+", " ", txt)
    txt = re.sub(r"\s*[-,]\s*MT$", "", txt, flags=re.IGNORECASE).strip()
    txt = re.sub(r"\bIRFORMADO\b", "INFORMADO", txt, flags=re.IGNORECASE)
    if txt.upper() in {"NAO INFORMADO", "NÃO INFORMADO", "SEM INFORMACAO", "SEM INFORMAÇÃO", "NAN", "NONE"}:
        return ""
    return txt


def _graus_para_decimal(grau: float, minuto: float, segundo: float, hemisferio: str) -> float:
    val = abs(float(grau)) + float(minuto) / 60.0 + float(segundo) / 3600.0
    hemisferio = (hemisferio or "").upper()
    if hemisferio in {"S", "O", "W"} or float(grau) < 0:
        val = -val
    return round(val, 6)


def _extrair_coordenadas(valor: Any) -> tuple[float | None, float | None]:
    if valor is None or pd.isna(valor):
        return None, None
    txt = str(valor).replace("\xa0", " ").strip()
    if not txt or "INFORMADO" in txt.upper():
        return None, None

    # Formato DMS: -14°31'58.65"S - 56°13'50.48"O
    dms = re.findall(r"(-?\d+(?:[,.]\d+)?)\s*[°º]\s*(\d+(?:[,.]\d+)?)?\s*['’]?\s*(\d+(?:[,.]\d+)?)?\s*(?:[\"])?\s*([NSLOEW])", txt, flags=re.IGNORECASE)
    if len(dms) >= 2:
        coords = []
        for g, m, sec, hemi in dms[:2]:
            coords.append(_graus_para_decimal(float(g.replace(",", ".")), float((m or "0").replace(",", ".")), float((sec or "0").replace(",", ".")), hemi))
        lat, lon = coords[0], coords[1]
        if lat is not None and lon is not None:
            if abs(lat) > 35 and abs(lon) < 35:
                lat, lon = lon, lat
            return lat, lon

    # Formato decimal: -16,5356769561768 -54,6653785705566
    nums = re.findall(r"-?\d+(?:[,.]\d+)?", txt)
    vals = []
    for n in nums:
        try:
            vals.append(float(n.replace(",", ".")))
        except Exception:
            pass
    if len(vals) >= 2:
        lat, lon = vals[0], vals[1]
        if abs(lat) > 35 and abs(lon) < 35:
            lat, lon = lon, lat
        if -35 <= lat <= 10 and -75 <= lon <= -40:
            return round(lat, 6), round(lon, 6)
    return None, None


def _to_float(valor: Any):
    if valor is None or pd.isna(valor):
        return None
    txt = str(valor).strip()
    if not txt:
        return None
    txt = txt.replace("\xa0", " ")
    # aceita vírgula decimal brasileira
    if "," in txt and "." in txt:
        txt = txt.replace(".", "").replace(",", ".")
    elif "," in txt:
        txt = txt.replace(",", ".")
    try:
        return float(txt)
    except Exception:
        return None


def _primeira_coluna(df: pd.DataFrame, candidatos: list[str]) -> str | None:
    cols_norm = {_normalizar_coluna(c): c for c in df.columns}
    for cand in candidatos:
        c = cols_norm.get(_normalizar_coluna(cand))
        if c:
            return c
    # busca flexível por pedaço
    for cand in candidatos:
        cand_norm = _normalizar_coluna(cand)
        for norm, original in cols_norm.items():
            if cand_norm in norm or norm in cand_norm:
                return original
    return None


def _ler_catalogo_local() -> pd.DataFrame:
    try:
        with db_session() as conn:
            return pd.read_sql_query("SELECT * FROM dados_abertos_mt_catalogo", conn)
    except Exception:
        return pd.DataFrame()


def _urls_candidatas() -> list[dict[str, str]]:
    candidatos: list[dict[str, str]] = []
    cat = _ler_catalogo_local()
    if not cat.empty:
        cols = {c.lower(): c for c in cat.columns}
        titulo_col = cols.get("dataset_titulo")
        recurso_col = cols.get("recurso_nome")
        formato_col = cols.get("formato")
        url_col = cols.get("url")
        portal_col = cols.get("url_dataset_portal")
        if url_col:
            for _, row in cat.iterrows():
                texto = " ".join(str(row.get(c) or "") for c in [titulo_col, recurso_col] if c)
                texto_norm = texto.lower()
                if any(p in texto_norm for p in PALAVRAS_DATASET):
                    url = str(row.get(url_col) or "").strip()
                    if url:
                        candidatos.append({
                            "url": url,
                            "formato": str(row.get(formato_col) or "").strip() if formato_col else "",
                            "recurso_nome": str(row.get(recurso_col) or "").strip() if recurso_col else "",
                            "dataset_titulo": str(row.get(titulo_col) or "").strip() if titulo_col else "",
                            "url_dataset_portal": str(row.get(portal_col) or "").strip() if portal_col else DATASET_PORTAL,
                        })
    # fallback oficial observado no catálogo exportado
    candidatos.append({
        "url": URL_FALLBACK,
        "formato": "XLSX",
        "recurso_nome": "Acidentes com resíduos perigosos - 2024.xlsx",
        "dataset_titulo": "Gestão de Áreas Contaminadas",
        "url_dataset_portal": DATASET_PORTAL,
    })
    # remove duplicados preservando ordem
    vistos = set()
    out = []
    for item in candidatos:
        u = item.get("url") or ""
        if u and u not in vistos:
            vistos.add(u)
            out.append(item)
    return out


def _baixar(url: str) -> bytes:
    headers = {
        "User-Agent": "Mozilla/5.0 Plataforma APS SES-MT",
        "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel,text/csv,*/*",
    }
    r = requests.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    conteudo = r.content or b""
    if not conteudo:
        raise ValueError("download vazio")
    inicio = conteudo[:200].lower()
    if b"<html" in inicio or b"<!doctype" in inicio:
        raise ValueError("download retornou HTML, não arquivo de dados")
    return conteudo


def _ler_arquivo(conteudo: bytes, url: str) -> pd.DataFrame:
    url_low = url.lower()
    if url_low.endswith(".csv"):
        for enc in ["utf-8-sig", "latin1", "cp1252"]:
            try:
                return pd.read_csv(io.BytesIO(conteudo), sep=None, engine="python", encoding=enc)
            except Exception:
                continue
        raise ValueError("não foi possível ler CSV")
    # XLS/XLSX
    try:
        sheets = pd.read_excel(io.BytesIO(conteudo), sheet_name=None)
    except Exception as exc:
        raise ValueError(f"não foi possível ler XLS/XLSX: {exc}") from exc
    melhores = []
    for nome, df in sheets.items():
        if df is None or df.empty:
            continue
        # remove linhas/colunas totalmente vazias
        df = df.dropna(how="all").dropna(axis=1, how="all")
        if not df.empty:
            melhores.append((nome, df))
    if not melhores:
        raise ValueError("planilha sem dados tabulares úteis")
    # maior aba em número de células úteis
    nome, df = sorted(melhores, key=lambda x: x[1].shape[0] * max(x[1].shape[1], 1), reverse=True)[0]
    df = df.copy()
    df["arquivo_origem_aba"] = nome
    return df


def _montar_saida(df: pd.DataFrame, meta: dict[str, str]) -> pd.DataFrame:
    original_cols = list(df.columns)
    # remove cabeçalhos duplicados ou colunas vazias comuns
    df = df.copy().dropna(how="all")
    df.columns = [_normalizar_coluna(c) for c in df.columns]

    municipio_col = _primeira_coluna(df, ["municipio", "município", "cidade", "localidade", "local", "nome_municipio"])
    data_col = _primeira_coluna(df, ["data", "data_ocorrencia", "data_do_acidente", "data_evento", "ano"])
    tipo_col = _primeira_coluna(df, ["tipo", "tipo_ocorrencia", "classe", "categoria", "evento"])
    produto_col = _primeira_coluna(df, ["produto", "residuo", "resíduo", "substancia", "substância", "material"])
    status_col = _primeira_coluna(df, ["situacao", "situação", "status", "andamento"])
    lat_col = _primeira_coluna(df, ["latitude", "lat", "y"])
    lon_col = _primeira_coluna(df, ["longitude", "long", "lon", "x"])
    coord_col = _primeira_coluna(df, ["coordenadas_geograficas", "coordenada", "coordenadas", "georreferencia", "georreferenciamento", "localizacao"])
    desc_col = _primeira_coluna(df, ["descricao", "descrição", "observacao", "observação", "detalhe", "historico"])

    registros = []
    for idx, row in df.iterrows():
        attrs = {}
        for c in df.columns:
            v = row.get(c)
            if pd.isna(v):
                continue
            attrs[c] = str(v)[:1000]
        municipio = _normalizar_municipio(row.get(municipio_col)) if municipio_col else ""
        data_ocorrencia = str(row.get(data_col) or "").strip() if data_col else ""
        ano = None
        m = re.search(r"(20\d{2}|19\d{2})", data_ocorrencia)
        if m:
            ano = int(m.group(1))
        elif data_col:
            try:
                ano_val = int(float(row.get(data_col)))
                if 1900 <= ano_val <= 2100:
                    ano = ano_val
            except Exception:
                pass
        lat = _to_float(row.get(lat_col)) if lat_col else None
        lon = _to_float(row.get(lon_col)) if lon_col else None
        if (lat is None or lon is None) and coord_col:
            lat_coord, lon_coord = _extrair_coordenadas(row.get(coord_col))
            lat = lat if lat is not None else lat_coord
            lon = lon if lon is not None else lon_coord
        registros.append({
            "municipio": municipio,
            "codigo_ibge": "",
            "data_ocorrencia": data_ocorrencia,
            "ano": ano,
            "tipo_ocorrencia": str(row.get(tipo_col) or "").strip() if tipo_col else "",
            "produto_residuo": str(row.get(produto_col) or "").strip() if produto_col else "",
            "situacao": str(row.get(status_col) or "").strip() if status_col else "",
            "descricao": str(row.get(desc_col) or "").strip()[:2000] if desc_col else "",
            "latitude": lat,
            "longitude": lon,
            "fonte_url": meta.get("url", ""),
            "dataset_titulo": meta.get("dataset_titulo", "Gestão de Áreas Contaminadas"),
            "recurso_nome": meta.get("recurso_nome", ""),
            "formato": meta.get("formato", ""),
            "url_dataset_portal": meta.get("url_dataset_portal", DATASET_PORTAL),
            "observacao": "Base ambiental estadual importada para camada própria; conferir atributos_json para colunas originais.",
            "atributos_json": json.dumps(attrs, ensure_ascii=False),
        })
    out = pd.DataFrame(registros)
    # remove linhas totalmente vazias do ponto de vista analítico
    if not out.empty:
        analiticas = ["municipio", "data_ocorrencia", "tipo_ocorrencia", "produto_residuo", "descricao", "latitude", "longitude"]
        mask = out[analiticas].astype(str).apply(lambda s: s.str.strip().replace("None", "").replace("nan", "")).ne("").any(axis=1)
        out = out[mask].copy()
    return out


def carregar_areas_contaminadas_sema_mt() -> pd.DataFrame:
    erros = []
    for meta in _urls_candidatas():
        url = meta.get("url") or ""
        try:
            conteudo = _baixar(url)
            bruto = _ler_arquivo(conteudo, url)
            out = _montar_saida(bruto, meta)
            if out.empty:
                erros.append(f"{url} -> arquivo lido, mas sem registros aproveitáveis")
                continue
            return out
        except Exception as exc:
            erros.append(f"{url} -> {exc}")
    raise RuntimeError("Não foi possível carregar Gestão de Áreas Contaminadas/SEMA. Detalhe: " + " | ".join(erros[:8]))


def testar_areas_contaminadas_sema_mt() -> dict:
    df = carregar_areas_contaminadas_sema_mt()
    return {
        "ok": True,
        "fonte": FONTE,
        "linhas": int(len(df)),
        "colunas": int(len(df.columns)),
        "municipios_preenchidos": int(df.get("municipio", pd.Series(dtype=str)).astype(str).str.strip().replace("", pd.NA).notna().sum()) if not df.empty else 0,
        "com_coordenadas": int((pd.to_numeric(df.get("latitude"), errors="coerce").notna() & pd.to_numeric(df.get("longitude"), errors="coerce").notna()).sum()) if not df.empty and "latitude" in df.columns else 0,
        "observacao": "Camada ambiental estadual pronta para importação em tabela própria dados_mt_areas_contaminadas.",
    }
