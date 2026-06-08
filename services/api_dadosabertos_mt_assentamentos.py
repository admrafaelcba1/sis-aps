from __future__ import annotations

import io
import json
import math
import re
import struct
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

ASSENTAMENTOS_URL = "https://dadosabertos.mt.gov.br/dataset/a6a35c28-b9b6-4a3d-ae6f-8b9ab07934fd/resource/a1e38467-c99c-4fb0-890c-748f25d0ce80/download/assentamentos-julho-2024.zip"
KMZ_URL = "https://dadosabertos.mt.gov.br/dataset/a6a35c28-b9b6-4a3d-ae6f-8b9ab07934fd/resource/7399a95f-9e0f-4e68-b538-61215be2bca6/download/intermat_lim_assentamento_a.kmz"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Plataforma-APS-SES-MT/1.0",
    "Accept": "application/zip,application/octet-stream,*/*",
}


def _normalizar_coluna(nome: str) -> str:
    import unicodedata
    texto = unicodedata.normalize("NFKD", str(nome or "")).encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-zA-Z0-9]+", "_", texto.strip().lower())
    return re.sub(r"_+", "_", texto).strip("_") or "campo"


def _baixar_zip(url: str) -> bytes:
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    content = resp.content or b""
    if len(content) < 100:
        raise RuntimeError(f"arquivo muito pequeno retornado por {url}")
    return content


def _decode_bytes(b: bytes) -> str:
    for enc in ("utf-8", "latin1", "cp1252"):
        try:
            return b.decode(enc).strip()
        except Exception:
            continue
    return b.decode("latin1", errors="ignore").strip()


def _ler_dbf_bytes(data: bytes) -> pd.DataFrame:
    if len(data) < 32:
        raise RuntimeError("DBF vazio ou inválido.")
    num_records = struct.unpack("<I", data[4:8])[0]
    header_len = struct.unpack("<H", data[8:10])[0]
    record_len = struct.unpack("<H", data[10:12])[0]
    fields = []
    pos = 32
    while pos + 32 <= len(data) and data[pos] != 0x0D:
        desc = data[pos:pos + 32]
        raw_name = desc[0:11].split(b"\x00", 1)[0]
        name = _normalizar_coluna(_decode_bytes(raw_name))
        ftype = chr(desc[11]) if desc[11] else "C"
        flen = desc[16]
        fdec = desc[17]
        fields.append((name, ftype, flen, fdec))
        pos += 32
    registros = []
    offset = header_len
    for _ in range(num_records):
        rec = data[offset:offset + record_len]
        offset += record_len
        if not rec or rec[0:1] == b"*":
            continue
        cursor = 1
        row = {}
        for name, ftype, flen, fdec in fields:
            raw = rec[cursor:cursor + flen]
            cursor += flen
            txt = _decode_bytes(raw).strip()
            if ftype in ("N", "F", "B", "I"):
                val = txt.replace(".", "").replace(",", ".") if ("," in txt and "." in txt and txt.rfind(",") > txt.rfind(".")) else txt.replace(",", ".")
                try:
                    row[name] = float(val) if val not in ("", ".", "-") else None
                except Exception:
                    row[name] = txt
            elif ftype == "D" and len(txt) == 8:
                row[name] = f"{txt[0:4]}-{txt[4:6]}-{txt[6:8]}"
            else:
                row[name] = txt
        registros.append(row)
    return pd.DataFrame(registros)


def _ler_shp_bboxes(data: bytes) -> list[dict[str, float | None]]:
    bboxes: list[dict[str, float | None]] = []
    if len(data) < 100:
        return bboxes
    pos = 100
    while pos + 8 <= len(data):
        try:
            _rec_no, content_len_words = struct.unpack(">2i", data[pos:pos + 8])
        except Exception:
            break
        pos += 8
        content_len = int(content_len_words) * 2
        content = data[pos:pos + content_len]
        pos += content_len
        if len(content) < 4:
            continue
        shape_type = struct.unpack("<i", content[0:4])[0]
        bbox = {"min_longitude": None, "min_latitude": None, "max_longitude": None, "max_latitude": None, "longitude_centroide": None, "latitude_centroide": None}
        try:
            if shape_type in (1, 11, 21) and len(content) >= 20:  # Point
                x, y = struct.unpack("<2d", content[4:20])
                bbox.update({"min_longitude": x, "max_longitude": x, "min_latitude": y, "max_latitude": y, "longitude_centroide": x, "latitude_centroide": y})
            elif shape_type in (3, 5, 8, 13, 15, 18, 23, 25, 28, 31) and len(content) >= 36:  # Polyline/Polygon/MultiPoint
                xmin, ymin, xmax, ymax = struct.unpack("<4d", content[4:36])
                bbox.update({"min_longitude": xmin, "min_latitude": ymin, "max_longitude": xmax, "max_latitude": ymax, "longitude_centroide": (xmin + xmax) / 2, "latitude_centroide": (ymin + ymax) / 2})
        except Exception:
            pass
        bboxes.append(bbox)
    return bboxes


def _primeira_coluna(df: pd.DataFrame, termos: list[str]) -> str | None:
    norm_cols = {_normalizar_coluna(c): c for c in df.columns}
    termos_norm = [_normalizar_coluna(x) for x in termos]
    for termo in termos_norm:
        if termo in norm_cols:
            return norm_cols[termo]
    for c in df.columns:
        cn = _normalizar_coluna(c)
        if any(t in cn for t in termos_norm):
            return c
    return None


def _serie_texto_valida(s: pd.Series) -> pd.Series:
    return s.fillna("").astype(str).str.strip().replace({"nan": "", "None": "", "NULL": "", "0": ""})


def _inferir_coluna_nome_assentamento(df: pd.DataFrame) -> str | None:
    """
    Shapefiles do INCRA/INTERMAT frequentemente trazem nomes truncados no DBF
    (ex.: NOMEPROJ, NM_PROJ, DENOMINA, NOME_PA). Esta função tenta primeiro
    os nomes esperados e, se não encontrar, escolhe a melhor coluna textual.
    """
    candidatos = [
        "nome_assentamento", "nome_assen", "nome_ass", "nm_assent", "assentamento",
        "nome_projeto", "nome_proj", "nomeprojeto", "nomeproje", "nomeproj",
        "nm_projeto", "nm_proj", "projeto", "proj_assent", "projeto_assentamento",
        "nome_pa", "nm_pa", "denominacao", "denominaca", "denomina", "gleba",
        "descricao", "descrição", "desc", "nome", "nm"
    ]
    col = _primeira_coluna(df, candidatos)
    if col:
        valores = _serie_texto_valida(df[col])
        if valores.replace("", pd.NA).notna().sum() > 0:
            return col

    # Fallback: melhor coluna textual, evitando campos técnicos/códigos.
    proibidos = {
        "id", "fid", "objectid", "codigo", "cod", "cd_mun", "codmun", "cod_ibge",
        "codigo_ibge", "geocodigo", "municipio", "mun", "nm_mun", "shape_leng",
        "shape_area", "area", "area_ha", "ha", "perimetro"
    }
    melhores: list[tuple[float, str]] = []
    for c in df.columns:
        cn = _normalizar_coluna(c)
        if cn in proibidos or any(x in cn for x in ["lat", "lon", "coord", "geom", "shape"]):
            continue
        serie = _serie_texto_valida(df[c])
        preenchidos = serie.replace("", pd.NA).dropna()
        if preenchidos.empty:
            continue
        # Evita colunas majoritariamente numéricas.
        numericos = pd.to_numeric(preenchidos.str.replace(",", ".", regex=False), errors="coerce").notna().mean()
        if numericos > 0.70:
            continue
        unicidade = preenchidos.nunique() / max(len(preenchidos), 1)
        tamanho_medio = preenchidos.str.len().mean()
        score = float(unicidade * 10 + min(tamanho_medio, 80) / 10)
        melhores.append((score, c))
    if melhores:
        return sorted(melhores, reverse=True)[0][1]
    return None


def _montar_dataframe_assentamentos(zip_bytes: bytes, fonte_url: str) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        nomes = zf.namelist()
        dbf_name = next((n for n in nomes if n.lower().endswith(".dbf")), None)
        shp_name = next((n for n in nomes if n.lower().endswith(".shp")), None)
        if not dbf_name:
            raise RuntimeError(f"ZIP lido, mas nenhum arquivo .dbf foi encontrado. Arquivos: {nomes[:10]}")
        df = _ler_dbf_bytes(zf.read(dbf_name))
        bboxes = _ler_shp_bboxes(zf.read(shp_name)) if shp_name else []

    if df.empty:
        raise RuntimeError("Shapefile de assentamentos foi lido, mas não retornou registros de atributos.")

    # Anexa bbox/centroide por ordem dos registros do shapefile. Se o número divergir, preenche o que for possível.
    if bboxes:
        geo = pd.DataFrame(bboxes)
        for col in geo.columns:
            if col not in df.columns:
                df[col] = None
        limite = min(len(df), len(geo))
        for col in geo.columns:
            df.loc[:limite - 1, col] = geo.loc[:limite - 1, col].values

    nome_col = _inferir_coluna_nome_assentamento(df)
    mun_col = _primeira_coluna(df, [
        "municipio", "município", "nome_municipio", "nome_munic", "municip", "munic",
        "mun", "nm_mun", "nm_munic", "nm_municip", "cidade"
    ])
    area_col = _primeira_coluna(df, ["area_ha", "area_hectares", "hectares", "area", "ha", "area_pa", "shape_area"])
    modalidade_col = _primeira_coluna(df, ["modalidade", "mod", "tipo", "categoria", "classificacao", "classe"])
    situacao_col = _primeira_coluna(df, ["situacao", "situação", "status", "situacao_pa", "situac", "situacao_p"])
    cod_col = _primeira_coluna(df, ["codigo_ibge", "cod_ibge", "geocodigo", "codmun", "cd_mun", "cod_mun", "ibge"])

    out = pd.DataFrame()
    out["nome_assentamento"] = _serie_texto_valida(df[nome_col]) if nome_col else [f"Assentamento sem nome {i + 1}" for i in range(len(df))]
    out["municipio"] = df[mun_col].astype(str).str.strip() if mun_col else ""
    out["codigo_ibge"] = df[cod_col].astype(str).str.replace(r"\D", "", regex=True) if cod_col else ""
    out["area_ha"] = pd.to_numeric(df[area_col], errors="coerce") if area_col else None
    out["modalidade"] = df[modalidade_col].astype(str).str.strip() if modalidade_col else ""
    out["situacao"] = df[situacao_col].astype(str).str.strip() if situacao_col else ""
    out["latitude_centroide"] = pd.to_numeric(df.get("latitude_centroide"), errors="coerce") if "latitude_centroide" in df.columns else None
    out["longitude_centroide"] = pd.to_numeric(df.get("longitude_centroide"), errors="coerce") if "longitude_centroide" in df.columns else None
    out["min_latitude"] = pd.to_numeric(df.get("min_latitude"), errors="coerce") if "min_latitude" in df.columns else None
    out["max_latitude"] = pd.to_numeric(df.get("max_latitude"), errors="coerce") if "max_latitude" in df.columns else None
    out["min_longitude"] = pd.to_numeric(df.get("min_longitude"), errors="coerce") if "min_longitude" in df.columns else None
    out["max_longitude"] = pd.to_numeric(df.get("max_longitude"), errors="coerce") if "max_longitude" in df.columns else None
    out["fonte_url"] = fonte_url
    out["arquivo_origem"] = "assentamentos-julho-2024.zip"
    nomes_genericos = out["nome_assentamento"].astype(str).str.startswith("Assentamento sem nome").sum()
    out["observacao"] = "Base territorial estadual de assentamentos; não consolidada automaticamente na Base Completa. Usar como camada de vulnerabilidade/ruralidade."
    if nome_col:
        out["observacao"] = out["observacao"] + f" Coluna de nome detectada: {nome_col}."
    if mun_col:
        out["observacao"] = out["observacao"] + f" Coluna de município detectada: {mun_col}."
    if nomes_genericos:
        out["observacao"] = out["observacao"] + " Atenção: alguns nomes não vieram explícitos no DBF e foram identificados genericamente."

    # Guarda os atributos originais para auditoria, sem depender de conhecer todos os nomes da base.
    atributos = df.copy()
    for col in ["latitude_centroide", "longitude_centroide", "min_latitude", "max_latitude", "min_longitude", "max_longitude"]:
        if col in atributos.columns:
            atributos = atributos.drop(columns=[col])
    out["atributos_json"] = atributos.fillna("").astype(str).apply(lambda r: json.dumps(r.to_dict(), ensure_ascii=False), axis=1)
    out = out.drop_duplicates(subset=["nome_assentamento", "municipio", "latitude_centroide", "longitude_centroide"], keep="last")
    return out


def carregar_assentamentos_intermt_mt() -> pd.DataFrame:
    erros: list[str] = []
    for url in [ASSENTAMENTOS_URL]:
        try:
            dados = _baixar_zip(url)
            return _montar_dataframe_assentamentos(dados, url)
        except Exception as exc:
            erros.append(f"{url} -> {exc}")
    raise RuntimeError("Não foi possível carregar Assentamentos INTERMAT. Detalhe: " + " | ".join(erros))


def testar_assentamentos_intermt_mt() -> dict[str, Any]:
    inicio = datetime.now()
    try:
        df = carregar_assentamentos_intermt_mt()
        return {
            "ok": True,
            "linhas": int(len(df)),
            "colunas": int(len(df.columns)),
            "municipios_informados": int(df["municipio"].astype(str).str.strip().replace("", pd.NA).notna().sum()) if "municipio" in df.columns else 0,
            "com_centroide": int(df[["latitude_centroide", "longitude_centroide"]].notna().all(axis=1).sum()) if {"latitude_centroide", "longitude_centroide"}.issubset(df.columns) else 0,
            "duracao_segundos": round((datetime.now() - inicio).total_seconds(), 2),
            "amostra_nomes": df["nome_assentamento"].dropna().astype(str).head(5).tolist() if "nome_assentamento" in df.columns else [],
            "municipios_inferidos_ou_informados": int(df["municipio"].astype(str).str.strip().replace("", pd.NA).notna().sum()) if "municipio" in df.columns else 0,
            "observacao": "Teste apenas lê a base estadual de assentamentos e prepara a camada territorial; não consolida na Base Completa.",
        }
    except Exception as exc:
        return {"ok": False, "erro": str(exc), "duracao_segundos": round((datetime.now() - inicio).total_seconds(), 2)}
