"""Conector Dados Abertos/MS — Hospitais e Leitos.

v20: lê o conjunto público "Hospitais e Leitos" do Portal de Dados Abertos do SUS.
A rotina prioriza arquivo local em data/uploads/hospitais_leitos/ ou data/raw/apis/ e,
na ausência de arquivo, tenta baixar o ZIP JSON anual publicado no S3/CKAN do Ministério da Saúde.

A saída é agregável pelo importador de leitos do sistema e mantém somente campos técnicos,
sem qualquer dado individual de pessoa.
"""
from __future__ import annotations

import io
import json
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests

from config.settings import RAW_DIR, UPLOADS_DIR

URLS_HOSPITAIS_LEITOS = [
    "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/Leitos_SUS/json/Leitos_json_{ano}.zip",
    "https://ckan.saude.gov.br/Leitos_SUS/json/Leitos_json_{ano}.zip",
]


def _texto_busca(valor: Any) -> str:
    texto = str(valor or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _primeira_coluna(df: pd.DataFrame, opcoes: list[str]) -> str | None:
    cols_norm = {_texto_busca(c).replace(" ", "_"): c for c in df.columns}
    for col in opcoes:
        if col in df.columns:
            return col
        chave = _texto_busca(col).replace(" ", "_")
        if chave in cols_norm:
            return cols_norm[chave]
    return None


def _to_numeric(serie: pd.Series | None) -> pd.Series:
    if serie is None:
        return pd.Series(dtype="float64")
    txt = (
        serie.astype(str)
        .str.strip()
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .str.replace(r"[^0-9\.\-]", "", regex=True)
    )
    txt = txt.mask(txt.isin(["", "-", ".", "nan", "None"]), None)
    return pd.to_numeric(txt, errors="coerce")


def _codigo_ibge(valor: Any) -> str:
    dig = re.sub(r"\D", "", "" if valor is None else str(valor))
    if len(dig) >= 7:
        return dig[:7]
    return dig


def _normalizar_municipio_nome(valor: Any) -> str:
    txt = str(valor or "").strip()
    txt = re.sub(r"^MT\s*[-/]\s*", "", txt, flags=re.I)
    return " ".join(txt.split()).title()


def _arquivos_locais(ano: int) -> Iterable[Path]:
    pastas = [UPLOADS_DIR / "hospitais_leitos", RAW_DIR / "apis", RAW_DIR]
    termos = ["leitos_json", "leitos", "hospitais"]
    for pasta in pastas:
        if not pasta.exists():
            continue
        for item in pasta.rglob("*"):
            if not item.is_file() or item.suffix.lower() not in {".zip", ".json", ".csv", ".xlsx", ".xls"}:
                continue
            busca = _texto_busca(item.name)
            if any(t in busca for t in termos) and (str(ano) in busca or "leitos" in busca):
                yield item


def _baixar_zip(ano: int) -> tuple[bytes, str]:
    erros = []
    for url_tpl in URLS_HOSPITAIS_LEITOS:
        url = url_tpl.format(ano=ano)
        try:
            resp = requests.get(url, timeout=90, verify=False)
            resp.raise_for_status()
            bruto = resp.content
            if not zipfile.is_zipfile(io.BytesIO(bruto)):
                raise ValueError("resposta não é ZIP válido")
            return bruto, url
        except Exception as exc:
            erros.append(f"{url} -> {exc}")
    raise ValueError("Não foi possível baixar o ZIP anual de Hospitais e Leitos. Últimos erros: " + " | ".join(erros[-4:]))


def _ler_json_bytes(bruto: bytes) -> pd.DataFrame:
    texto = None
    ultimo = None
    for enc in ["utf-8-sig", "utf-8", "latin1", "cp1252"]:
        try:
            texto = bruto.decode(enc)
            break
        except Exception as exc:
            ultimo = exc
    if texto is None:
        raise ValueError(f"Não foi possível decodificar JSON: {ultimo}")

    texto_strip = texto.strip()
    if not texto_strip:
        return pd.DataFrame()

    try:
        obj = json.loads(texto_strip)
        if isinstance(obj, list):
            return pd.json_normalize(obj)
        if isinstance(obj, dict):
            for chave in ["data", "dados", "items", "result", "results", "registros"]:
                if isinstance(obj.get(chave), list):
                    return pd.json_normalize(obj[chave])
            return pd.json_normalize(obj)
    except Exception:
        pass

    try:
        return pd.read_json(io.StringIO(texto_strip), lines=True)
    except Exception as exc:
        raise ValueError(f"JSON não reconhecido: {exc}")


def _ler_csv_bytes(bruto: bytes) -> pd.DataFrame:
    ultimo = None
    for enc in ["utf-8-sig", "utf-8", "latin1", "cp1252"]:
        for sep in [None, ";", ",", "\t", "|"]:
            try:
                return pd.read_csv(io.BytesIO(bruto), sep=sep, engine="python", encoding=enc, dtype=str, on_bad_lines="skip")
            except Exception as exc:
                ultimo = exc
    raise ValueError(f"Não foi possível ler CSV: {ultimo}")


def _ler_arquivo_tabular(caminho: Path, bruto: bytes | None = None) -> pd.DataFrame:
    nome = caminho.name.lower()
    data = bruto if bruto is not None else caminho.read_bytes()
    if nome.endswith(".json"):
        return _ler_json_bytes(data)
    if nome.endswith((".csv", ".txt")):
        return _ler_csv_bytes(data)
    if nome.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(data), dtype=str)
    raise ValueError(f"Formato não suportado: {caminho.name}")


def _ler_zip_hospitais_leitos(bruto: bytes, origem: str) -> pd.DataFrame:
    frames = []
    with zipfile.ZipFile(io.BytesIO(bruto), "r") as zf:
        nomes = [n for n in zf.namelist() if not n.endswith("/")]
        candidatos = [n for n in nomes if n.lower().endswith((".json", ".csv", ".txt", ".xlsx", ".xls"))]
        candidatos = sorted(candidatos, key=lambda n: zf.getinfo(n).file_size, reverse=True)
        erros = []
        for nome in candidatos[:6]:
            busca = _texto_busca(nome)
            if any(t in busca for t in ["dicionario", "readme", "layout"]):
                continue
            try:
                df = _ler_arquivo_tabular(Path(nome), zf.read(nome))
                if isinstance(df, pd.DataFrame) and not df.empty:
                    df["arquivo_origem_leitos"] = nome
                    frames.append(df)
            except Exception as exc:
                erros.append(f"{nome}: {exc}")
        if not frames:
            raise ValueError(f"ZIP {origem} não trouxe arquivo tabular reconhecível. Erros: {' | '.join(erros[-4:])}")
    return pd.concat(frames, ignore_index=True, sort=False)


def _carregar_bruto(ano: int) -> tuple[pd.DataFrame, str]:
    for caminho in _arquivos_locais(ano):
        try:
            if caminho.suffix.lower() == ".zip":
                df = _ler_zip_hospitais_leitos(caminho.read_bytes(), str(caminho))
            else:
                df = _ler_arquivo_tabular(caminho)
            if not df.empty:
                return df, str(caminho)
        except Exception:
            pass
    bruto, url = _baixar_zip(ano)
    df = _ler_zip_hospitais_leitos(bruto, url)
    try:
        destino = RAW_DIR / "apis" / f"dadosabertos_ms_hospitais_leitos_{ano}.zip"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(bruto)
    except Exception:
        pass
    return df, url


def _normalizar_hospitais_leitos(df: pd.DataFrame, ano: int, origem: str) -> pd.DataFrame:
    if df is None or df.empty:
        raise ValueError("Hospitais e Leitos retornou dataframe vazio.")
    work = df.copy()

    uf_col = _primeira_coluna(work, ["uf", "sg_uf", "estado", "sigla_uf", "UF"])
    cod_col = _primeira_coluna(work, ["codigo_ibge", "cod_ibge", "co_municipio", "cod_municipio", "codigo_municipio", "municipio_ibge"])
    mun_col = _primeira_coluna(work, ["municipio", "no_municipio", "nome_municipio", "cidade"])

    if cod_col:
        work["codigo_ibge"] = work[cod_col].map(_codigo_ibge)
    else:
        work["codigo_ibge"] = ""

    if uf_col:
        uf = work[uf_col].astype(str).str.upper().str.strip()
        work = work[(uf == "MT") | (work["codigo_ibge"].astype(str).str.startswith("51"))].copy()
    else:
        work = work[work["codigo_ibge"].astype(str).str.startswith("51")].copy()

    if work.empty:
        raise ValueError("Hospitais e Leitos foi lido, mas não restaram registros de Mato Grosso após filtro UF/código IBGE.")

    cnes_col = _primeira_coluna(work, ["cnes", "co_cnes", "codigo_cnes", "cod_cnes"])
    nome_col = _primeira_coluna(work, ["nome_estabelecimento", "nome_fantasia", "no_fantasia", "estabelecimento", "nome_unidade"])
    tipo_un_col = _primeira_coluna(work, ["tipo_unidade", "ds_tipo_unidade", "tp_unidade", "descricao_tipo_unidade"])
    nat_col = _primeira_coluna(work, ["natureza_juridica", "natureza", "ds_natureza_juridica"])
    tipo_leito_col = _primeira_coluna(work, ["tipo_leito", "ds_tipo_leito", "descricao_tipo_leito"])
    exist_col = _primeira_coluna(work, ["leitos_existentes", "qt_existente", "qtd_existente", "quantidade_existente", "qt_leitos_existentes"])
    sus_col = _primeira_coluna(work, ["leitos_sus", "qt_sus", "qtd_sus", "quantidade_sus", "qt_leitos_sus"])
    lat_col = _primeira_coluna(work, ["latitude", "lat"])
    lon_col = _primeira_coluna(work, ["longitude", "long", "lon"])

    out = pd.DataFrame(index=work.index)
    out["codigo_ibge"] = work["codigo_ibge"].astype(str).str[:7]
    out["municipio"] = work[mun_col].map(_normalizar_municipio_nome) if mun_col else ""
    out["cnes"] = work[cnes_col].astype(str).str.extract(r"(\d+)", expand=False).fillna("") if cnes_col else ""
    out["nome_estabelecimento"] = work[nome_col].astype(str).str.strip() if nome_col else ""
    out["tipo_unidade"] = work[tipo_un_col].astype(str).str.strip() if tipo_un_col else ""
    out["natureza_juridica"] = work[nat_col].astype(str).str.strip() if nat_col else ""
    out["tipo_leito"] = work[tipo_leito_col].astype(str).str.strip() if tipo_leito_col else ""
    out["leitos_existentes"] = _to_numeric(work[exist_col]).fillna(0) if exist_col else 0
    out["leitos_sus"] = _to_numeric(work[sus_col]).fillna(0) if sus_col else 0
    out["latitude"] = _to_numeric(work[lat_col]) if lat_col else None
    out["longitude"] = _to_numeric(work[lon_col]) if lon_col else None
    out["ano"] = ano
    out["competencia"] = str(ano)
    out["fonte"] = f"Dados Abertos/MS — Hospitais e Leitos ({origem})"

    soma_sus = float(pd.to_numeric(out["leitos_sus"], errors="coerce").fillna(0).sum())
    soma_exist = float(pd.to_numeric(out["leitos_existentes"], errors="coerce").fillna(0).sum())
    if soma_sus <= 0 and soma_exist <= 0:
        raise ValueError("Hospitais e Leitos foi lido, mas não foi possível identificar colunas numéricas de leitos.")

    out = out.drop_duplicates(subset=["codigo_ibge", "cnes", "nome_estabelecimento", "tipo_leito", "leitos_existentes", "leitos_sus"], keep="last")
    return out.reset_index(drop=True)


def carregar_hospitais_leitos_ms_mt(ano: int = 2026) -> pd.DataFrame:
    df, origem = _carregar_bruto(ano)
    out = _normalizar_hospitais_leitos(df, ano=ano, origem=origem)
    if out.empty:
        raise ValueError("Hospitais e Leitos retornou vazio após normalização.")
    return out


def testar_hospitais_leitos_ms(ano: int = 2026) -> pd.DataFrame:
    df = carregar_hospitais_leitos_ms_mt(ano=ano)
    return df.head(50).copy()
