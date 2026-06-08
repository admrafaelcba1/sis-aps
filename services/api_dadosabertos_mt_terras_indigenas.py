from __future__ import annotations

import io
import json
import re
import struct
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

import pandas as pd
import requests

TERRAS_INDIGENAS_URL = "https://dadosabertos.mt.gov.br/dataset/fa90c57d-37ad-43a3-acf7-a1b1bf381a53/resource/d2150e19-9518-447b-9dac-f5eb6dae7566/download/lim_terra_indigena_a.zip"
KMZ_URL = "https://dadosabertos.mt.gov.br/dataset/fa90c57d-37ad-43a3-acf7-a1b1bf381a53/resource/fa9b58e3-3eed-43fa-9d95-da51d567071b/download/ti.kmz"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Plataforma-APS-SES-MT/1.0",
    "Accept": "application/zip,application/octet-stream,application/vnd.google-earth.kmz,*/*",
}


def _parece_zip(content: bytes) -> bool:
    return bool(content and content[:2] == b"PK")


def _inicio_texto(content: bytes, limite: int = 180) -> str:
    try:
        return content[:limite].decode("utf-8", errors="ignore").replace("\n", " ").replace("\r", " ").strip()
    except Exception:
        return ""


def _normalizar_coluna(nome: str) -> str:
    import unicodedata
    texto = unicodedata.normalize("NFKD", str(nome or "")).encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-zA-Z0-9]+", "_", texto.strip().lower())
    return re.sub(r"_+", "_", texto).strip("_") or "campo"


def _baixar_zip(url: str) -> bytes:
    resp = requests.get(url, headers=HEADERS, timeout=60, allow_redirects=True)
    resp.raise_for_status()
    content = resp.content or b""
    if len(content) < 100:
        raise RuntimeError(f"arquivo muito pequeno retornado por {url}")
    if not _parece_zip(content):
        ct = resp.headers.get("content-type", "")
        inicio = _inicio_texto(content)
        raise RuntimeError(f"resposta não é ZIP/KMZ válido; content-type={ct}; início={inicio}")
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
            if shape_type in (1, 11, 21) and len(content) >= 20:
                x, y = struct.unpack("<2d", content[4:20])
                bbox.update({"min_longitude": x, "max_longitude": x, "min_latitude": y, "max_latitude": y, "longitude_centroide": x, "latitude_centroide": y})
            elif shape_type in (3, 5, 8, 13, 15, 18, 23, 25, 28, 31) and len(content) >= 36:
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


def _melhor_coluna_textual(df: pd.DataFrame, preferidos: list[str], proibidos: set[str] | None = None) -> str | None:
    col = _primeira_coluna(df, preferidos)
    if col:
        valores = _serie_texto_valida(df[col])
        if valores.replace("", pd.NA).notna().sum() > 0:
            return col
    proibidos = proibidos or set()
    melhores: list[tuple[float, str]] = []
    for c in df.columns:
        cn = _normalizar_coluna(c)
        if cn in proibidos or any(x in cn for x in ["lat", "lon", "coord", "geom", "shape"]):
            continue
        serie = _serie_texto_valida(df[c])
        preenchidos = serie.replace("", pd.NA).dropna()
        if preenchidos.empty:
            continue
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



def _texto_no(elemento: ET.Element | None) -> str:
    if elemento is None or elemento.text is None:
        return ""
    return str(elemento.text).strip()


def _filhos_por_nome(elemento: ET.Element, nome_local: str) -> list[ET.Element]:
    return [el for el in elemento.iter() if str(el.tag).split("}")[-1].lower() == nome_local.lower()]


def _primeiro_filho_texto(elemento: ET.Element, nome_local: str) -> str:
    filhos = _filhos_por_nome(elemento, nome_local)
    return _texto_no(filhos[0]) if filhos else ""


def _extrair_extended_data(placemark: ET.Element) -> dict[str, str]:
    dados: dict[str, str] = {}
    for data_el in _filhos_por_nome(placemark, "Data"):
        nome = data_el.attrib.get("name") or data_el.attrib.get("Name") or "campo"
        valor = _primeiro_filho_texto(data_el, "value")
        dados[_normalizar_coluna(nome)] = valor
    for simple in _filhos_por_nome(placemark, "SimpleData"):
        nome = simple.attrib.get("name") or simple.attrib.get("Name") or "campo"
        dados[_normalizar_coluna(nome)] = _texto_no(simple)
    return dados


def _coordenadas_kml(placemark: ET.Element) -> list[tuple[float, float]]:
    pontos: list[tuple[float, float]] = []
    for coord_el in _filhos_por_nome(placemark, "coordinates"):
        texto = _texto_no(coord_el)
        if not texto:
            continue
        for parte in re.split(r"\s+", texto.strip()):
            if not parte or "," not in parte:
                continue
            valores = parte.split(",")
            if len(valores) < 2:
                continue
            try:
                lon = float(valores[0])
                lat = float(valores[1])
            except Exception:
                continue
            # Coordenadas plausíveis para MT/Brasil continental.
            if -75 <= lon <= -30 and -35 <= lat <= 10:
                pontos.append((lon, lat))
    return pontos


def _montar_dataframe_kml(kml_bytes: bytes, fonte_url: str, arquivo_origem: str) -> pd.DataFrame:
    texto = _decode_bytes(kml_bytes)
    if not texto:
        raise RuntimeError("KML vazio dentro do KMZ.")
    try:
        root = ET.fromstring(texto.encode("utf-8"))
    except Exception:
        root = ET.fromstring(texto)

    placemarks = _filhos_por_nome(root, "Placemark")
    registros: list[dict[str, Any]] = []
    for idx, pm in enumerate(placemarks, start=1):
        ext = _extrair_extended_data(pm)
        nome = _primeiro_filho_texto(pm, "name")
        if not nome:
            for chave in ["nome", "nm_ti", "terra_indigena", "terrai", "denominacao", "etnia", "povo"]:
                if ext.get(chave):
                    nome = ext.get(chave, "")
                    break
        if not nome:
            nome = f"Terra indígena {idx}"

        pontos = _coordenadas_kml(pm)
        lons = [p[0] for p in pontos]
        lats = [p[1] for p in pontos]
        bbox = {
            "min_longitude": min(lons) if lons else None,
            "max_longitude": max(lons) if lons else None,
            "min_latitude": min(lats) if lats else None,
            "max_latitude": max(lats) if lats else None,
            "longitude_centroide": (min(lons) + max(lons)) / 2 if lons else None,
            "latitude_centroide": (min(lats) + max(lats)) / 2 if lats else None,
        }

        etnia = ""
        for chave in ["etnia", "etnias", "povo", "povos", "grupo", "grupo_etnico"]:
            if ext.get(chave):
                etnia = ext[chave]
                break
        municipio = ""
        for chave in ["municipio", "municipios", "mun", "nm_mun", "cidade"]:
            if ext.get(chave):
                municipio = ext[chave]
                break
        codigo_ibge = ""
        for chave in ["codigo_ibge", "cod_ibge", "geocodigo", "codmun", "cd_mun", "ibge"]:
            if ext.get(chave):
                codigo_ibge = re.sub(r"\D", "", str(ext[chave]))
                break

        area_ha = None
        for chave in ["area_ha", "area", "hectares", "ha", "shape_area"]:
            if ext.get(chave):
                area_ha = pd.to_numeric(str(ext[chave]).replace(".", "").replace(",", "."), errors="coerce")
                break

        registros.append({
            "nome_terra_indigena": str(nome).strip(),
            "etnia": etnia,
            "municipio": municipio,
            "codigo_ibge": codigo_ibge,
            "municipios_intersectados": "",
            "area_ha": area_ha,
            "situacao": ext.get("situacao") or ext.get("status") or ext.get("fase") or "",
            **bbox,
            "fonte_url": fonte_url,
            "arquivo_origem": arquivo_origem,
            "observacao": "Camada territorial estadual de terras indígenas lida via KML/KMZ do Dados Abertos MT; não consolidada automaticamente na Base Completa.",
            "atributos_json": json.dumps(ext, ensure_ascii=False),
        })
    df = pd.DataFrame(registros)
    if df.empty:
        raise RuntimeError("KMZ/KML lido, mas nenhum Placemark foi encontrado.")
    return df.drop_duplicates(subset=["nome_terra_indigena", "latitude_centroide", "longitude_centroide"], keep="last")


def _montar_dataframe_kmz(zip_bytes: bytes, fonte_url: str) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        nomes = zf.namelist()
        kml_name = next((n for n in nomes if n.lower().endswith(".kml")), None)
        if not kml_name:
            raise RuntimeError(f"KMZ lido, mas nenhum arquivo .kml foi encontrado. Arquivos: {nomes[:10]}")
        return _montar_dataframe_kml(zf.read(kml_name), fonte_url=fonte_url, arquivo_origem=kml_name)

def _montar_dataframe_terras_indigenas(zip_bytes: bytes, fonte_url: str) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        nomes = zf.namelist()
        dbf_name = next((n for n in nomes if n.lower().endswith(".dbf")), None)
        shp_name = next((n for n in nomes if n.lower().endswith(".shp")), None)
        kml_name = next((n for n in nomes if n.lower().endswith(".kml")), None)
        if not dbf_name and kml_name:
            return _montar_dataframe_kml(zf.read(kml_name), fonte_url=fonte_url, arquivo_origem=kml_name)
        if not dbf_name:
            raise RuntimeError(f"ZIP lido, mas nenhum arquivo .dbf/.kml foi encontrado. Arquivos: {nomes[:10]}")
        df = _ler_dbf_bytes(zf.read(dbf_name))
        bboxes = _ler_shp_bboxes(zf.read(shp_name)) if shp_name else []

    if df.empty:
        raise RuntimeError("Shapefile de terras indígenas foi lido, mas não retornou registros de atributos.")

    if bboxes:
        geo = pd.DataFrame(bboxes)
        for col in geo.columns:
            if col not in df.columns:
                df[col] = None
        limite = min(len(df), len(geo))
        for col in geo.columns:
            df.loc[:limite - 1, col] = geo.loc[:limite - 1, col].values

    nome_col = _melhor_coluna_textual(df, [
        "nome_terra_indigena", "terra_indigena", "terras_indigenas", "nome_ti", "nm_ti", "ti_nome",
        "denominacao", "denomina", "nome", "nm", "area", "descricao", "localidade"
    ], proibidos={"id", "fid", "objectid", "codigo", "cod", "municipio", "mun", "cod_ibge", "shape_leng", "shape_area"})
    etnia_col = _primeira_coluna(df, ["etnia", "etnias", "povo", "povos", "grupo", "grupo_etnico", "etnico"])
    mun_col = _primeira_coluna(df, ["municipio", "município", "nome_municipio", "nome_munic", "municip", "munic", "mun", "nm_mun", "nm_munic", "cidade"])
    cod_col = _primeira_coluna(df, ["codigo_ibge", "cod_ibge", "geocodigo", "codmun", "cd_mun", "cod_mun", "ibge"])
    area_col = _primeira_coluna(df, ["area_ha", "area_hectares", "hectares", "area", "ha", "shape_area"])
    situacao_col = _primeira_coluna(df, ["situacao", "situação", "status", "fase", "condicao", "condição", "situac"])

    out = pd.DataFrame()
    out["nome_terra_indigena"] = _serie_texto_valida(df[nome_col]) if nome_col else [f"Terra indígena sem nome {i + 1}" for i in range(len(df))]
    out["etnia"] = _serie_texto_valida(df[etnia_col]) if etnia_col else ""
    out["municipio"] = _serie_texto_valida(df[mun_col]) if mun_col else ""
    out["codigo_ibge"] = df[cod_col].astype(str).str.replace(r"\D", "", regex=True) if cod_col else ""
    out["municipios_intersectados"] = ""
    out["area_ha"] = pd.to_numeric(df[area_col], errors="coerce") if area_col else None
    out["situacao"] = _serie_texto_valida(df[situacao_col]) if situacao_col else ""
    out["latitude_centroide"] = pd.to_numeric(df.get("latitude_centroide"), errors="coerce") if "latitude_centroide" in df.columns else None
    out["longitude_centroide"] = pd.to_numeric(df.get("longitude_centroide"), errors="coerce") if "longitude_centroide" in df.columns else None
    out["min_latitude"] = pd.to_numeric(df.get("min_latitude"), errors="coerce") if "min_latitude" in df.columns else None
    out["max_latitude"] = pd.to_numeric(df.get("max_latitude"), errors="coerce") if "max_latitude" in df.columns else None
    out["min_longitude"] = pd.to_numeric(df.get("min_longitude"), errors="coerce") if "min_longitude" in df.columns else None
    out["max_longitude"] = pd.to_numeric(df.get("max_longitude"), errors="coerce") if "max_longitude" in df.columns else None
    out["fonte_url"] = fonte_url
    out["arquivo_origem"] = "lim_terra_indigena_a.zip"
    out["observacao"] = "Camada territorial estadual de terras indígenas; não consolidada automaticamente na Base Completa. Usar como camada de equidade territorial e acesso APS."
    if nome_col:
        out["observacao"] = out["observacao"] + f" Coluna de nome detectada: {nome_col}."
    if etnia_col:
        out["observacao"] = out["observacao"] + f" Coluna de etnia/povo detectada: {etnia_col}."

    atributos = df.copy()
    for col in ["latitude_centroide", "longitude_centroide", "min_latitude", "max_latitude", "min_longitude", "max_longitude"]:
        if col in atributos.columns:
            atributos = atributos.drop(columns=[col])
    out["atributos_json"] = atributos.fillna("").astype(str).apply(lambda r: json.dumps(r.to_dict(), ensure_ascii=False), axis=1)
    out = out.drop_duplicates(subset=["nome_terra_indigena", "latitude_centroide", "longitude_centroide"], keep="last")
    return out


def carregar_terras_indigenas_intermt_mt() -> pd.DataFrame:
    erros: list[str] = []
    # 1) tenta shapefile ZIP; 2) se o portal devolver HTML ou arquivo inválido, usa o KMZ público da mesma base.
    for url in [TERRAS_INDIGENAS_URL, KMZ_URL]:
        try:
            dados = _baixar_zip(url)
            df = _montar_dataframe_terras_indigenas(dados, url)
            if not df.empty:
                return df
            erros.append(f"{url} -> retornou DataFrame vazio")
        except Exception as exc:
            erros.append(f"{url} -> {exc}")
    raise RuntimeError("Não foi possível carregar Terras Indígenas INTERMAT. Detalhe: " + " | ".join(erros))


def testar_terras_indigenas_intermt_mt() -> dict[str, Any]:
    inicio = datetime.now()
    try:
        df = carregar_terras_indigenas_intermt_mt()
        return {
            "ok": True,
            "linhas": int(len(df)),
            "colunas": int(len(df.columns)),
            "nomes_preenchidos": int(df["nome_terra_indigena"].astype(str).str.strip().replace("", pd.NA).notna().sum()) if "nome_terra_indigena" in df.columns else 0,
            "com_centroide": int(df[["latitude_centroide", "longitude_centroide"]].notna().all(axis=1).sum()) if {"latitude_centroide", "longitude_centroide"}.issubset(df.columns) else 0,
            "duracao_segundos": round((datetime.now() - inicio).total_seconds(), 2),
            "amostra_nomes": df["nome_terra_indigena"].dropna().astype(str).head(5).tolist() if "nome_terra_indigena" in df.columns else [],
            "observacao": "Teste apenas lê a base estadual de terras indígenas e prepara a camada territorial; não consolida na Base Completa.",
        }
    except Exception as exc:
        return {"ok": False, "erro": str(exc), "duracao_segundos": round((datetime.now() - inicio).total_seconds(), 2)}
