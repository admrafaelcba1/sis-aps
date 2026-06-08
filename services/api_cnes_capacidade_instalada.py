
"""Conector experimental CNES — capacidade instalada.

Objetivo: tentar localizar, no catálogo CKAN/Dados Abertos do SUS, recursos
CSV/ZIP públicos ligados ao CNES para equipamentos, serviços especializados e
habilitações. A saída é propositalmente agregada por município, em formato longo
(municipio, indicador, valor), para entrar em indicadores_municipais sem criar
novas tabelas nesta etapa.

Não usa bs4 nem dependências extras.
"""
from __future__ import annotations

import io
import json
import re
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests

from config.municipios_mt import DEFAULT_MUNICIPIOS
from config.settings import RAW_DIR

TIMEOUT = 45
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) APS-SES-MT/1.0"
CKAN_BASES = [
    "https://apidadosabertos.saude.gov.br/api/3/action/package_search?q={q}",
    "https://dadosabertos.saude.gov.br/api/3/action/package_search?q={q}",
]

CONSULTAS = [
    ("equipamentos", "CNES equipamentos"),
    ("servicos", "CNES serviços especializados"),
    ("habilitacoes", "CNES habilitações"),
]

# URLs diretas prováveis. Mantemos como tentativa complementar, porque a
# organização dos recursos do portal pode variar por versão.
URLS_DIRETAS = {
    "equipamentos": [
        "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/CNES/cnes_equipamentos.csv.zip",
        "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/CNES/equipamentos.csv.zip",
    ],
    "servicos": [
        "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/CNES/cnes_servicos.csv.zip",
        "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/CNES/servicos.csv.zip",
    ],
    "habilitacoes": [
        "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/CNES/cnes_habilitacoes.csv.zip",
        "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/CNES/habilitacoes.csv.zip",
    ],
}


def _norm(txt: Any) -> str:
    s = str(txt or "").strip().lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return " ".join(s.split())


def _somente_digitos(v: Any) -> str:
    return re.sub(r"\D", "", "" if v is None else str(v))


def _codigo7(v: Any) -> str:
    d = _somente_digitos(v)
    if len(d) >= 7:
        return d[:7]
    if len(d) == 6:
        # CNES costuma usar código municipal de 6 dígitos. Para MT, prefixo 51.
        return d + "0" if False else d
    return d


def _mapas_municipios():
    por_nome = {}
    por_codigo = {}
    for item in DEFAULT_MUNICIPIOS:
        mun = item.get("municipio") or item.get("nome")
        cod = _somente_digitos(item.get("codigo_ibge"))
        if mun:
            por_nome[_norm(mun)] = mun
        if cod and mun:
            por_codigo[cod] = mun
            por_codigo[cod[:6]] = mun
    return por_nome, por_codigo


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json,text/csv,text/html,*/*"})
    return s


def _baixar(url: str) -> bytes:
    r = _session().get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content


def _decode_bytes(b: bytes) -> str:
    for enc in ["utf-8-sig", "utf-8", "latin1", "cp1252"]:
        try:
            return b.decode(enc)
        except Exception:
            pass
    return b.decode("latin1", errors="ignore")


def _ler_csv_bytes(b: bytes, origem: str) -> pd.DataFrame:
    texto = _decode_bytes(b).replace("\xa0", " ")
    for sep in [";", ",", "\t", "|"]:
        try:
            df = pd.read_csv(io.StringIO(texto), sep=sep, dtype=str, low_memory=False)
            if len(df.columns) >= 2 and len(df) > 0:
                df["fonte_url"] = origem
                return df
        except Exception:
            continue
    return pd.DataFrame()


def _ler_recurso(url: str) -> pd.DataFrame:
    b = _baixar(url)
    nome = url.lower()
    if zipfile.is_zipfile(io.BytesIO(b)) or nome.endswith(".zip"):
        frames = []
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            for nm in z.namelist():
                if nm.lower().endswith((".csv", ".txt")):
                    try:
                        df = _ler_csv_bytes(z.read(nm), f"{url}::{nm}")
                        if not df.empty:
                            frames.append(df)
                    except Exception:
                        pass
        if frames:
            # prioriza o maior arquivo útil
            frames.sort(key=lambda x: len(x), reverse=True)
            return frames[0]
        return pd.DataFrame()
    return _ler_csv_bytes(b, url)


def _recursos_por_ckan(termo: str) -> list[str]:
    urls = []
    detalhes = []
    for tmpl in CKAN_BASES:
        url = tmpl.format(q=quote(termo))
        try:
            r = _session().get(url, timeout=TIMEOUT)
            detalhes.append(f"{url} status={r.status_code}")
            if r.status_code != 200:
                continue
            data = r.json()
            for pkg in (data.get("result", {}) or {}).get("results", []) or []:
                for res in pkg.get("resources", []) or []:
                    u = res.get("url") or res.get("download_url") or ""
                    fmt = _norm(res.get("format") or res.get("mimetype") or u)
                    nome = _norm(" ".join([pkg.get("title", ""), res.get("name", ""), u]))
                    if not u:
                        continue
                    if not any(x in nome for x in ["cnes", "cadastro nacional", "estabelecimento"]):
                        continue
                    if not any(x in fmt or u.lower().endswith(x) for x in ["csv", "zip", ".txt"]):
                        continue
                    urls.append(u)
        except Exception as exc:
            detalhes.append(f"{url} erro={exc}")
    # preserva ordem e remove duplicidades
    vistos = set()
    unicos = []
    for u in urls:
        if u not in vistos:
            vistos.add(u)
            unicos.append(u)
    return unicos


def _col(df: pd.DataFrame, opcoes: list[str]) -> str | None:
    cols = {c.lower(): c for c in df.columns}
    norm_cols = {_norm(c).replace(" ", "_"): c for c in df.columns}
    for op in opcoes:
        if op in df.columns:
            return op
        if op.lower() in cols:
            return cols[op.lower()]
        key = _norm(op).replace(" ", "_")
        if key in norm_cols:
            return norm_cols[key]
    return None


def _num(s: pd.Series | None) -> pd.Series:
    if s is None:
        return pd.Series(dtype="float64")
    txt = s.astype(str).str.strip().str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    txt = txt.str.replace(r"[^0-9\.\-]", "", regex=True)
    txt = txt.mask(txt.isin(["", ".", "-", "nan", "None"]), None)
    return pd.to_numeric(txt, errors="coerce")


def _preparar_municipio(df: pd.DataFrame) -> pd.DataFrame:
    por_nome, por_codigo = _mapas_municipios()
    work = df.copy()
    c_mun = _col(work, ["municipio", "no_municipio", "nome_municipio", "cidade"])
    c_cod = _col(work, ["codigo_ibge", "cod_ibge", "co_municipio", "codigo_municipio", "cod_municipio", "ibge"])
    if c_mun:
        work["municipio"] = work[c_mun].map(lambda x: por_nome.get(_norm(x), str(x).strip()))
    else:
        work["municipio"] = ""
    if c_cod:
        cod = work[c_cod].map(_somente_digitos)
        work["codigo_ibge"] = cod
        vazio = work["municipio"].astype(str).str.strip().isin(["", "nan", "None"])
        work.loc[vazio, "municipio"] = cod.map(por_codigo)
        # filtra por codificação MT: código IBGE 7 ou código CNES 6 iniciado com 51.
        mask_cod_mt = cod.str.startswith("51", na=False)
    else:
        mask_cod_mt = pd.Series([False] * len(work), index=work.index)
    mask_nome_mt = work["municipio"].map(lambda x: _norm(x) in por_nome)
    out = work[mask_cod_mt | mask_nome_mt].copy()
    if out.empty:
        return out
    out["municipio"] = out["municipio"].map(lambda x: por_nome.get(_norm(x), x))
    return out


def _agregar_base(df: pd.DataFrame, tipo: str) -> pd.DataFrame:
    work = _preparar_municipio(df)
    if work.empty:
        return pd.DataFrame()

    if tipo == "equipamentos":
        qt_total = _col(work, ["qt_equipamento", "qt_equip", "quantidade", "qt_existente", "existente", "qtde"])
        qt_uso = _col(work, ["qt_uso", "qt_em_uso", "em_uso", "qt_equipamento_em_uso"])
        work["_total"] = _num(work[qt_total]) if qt_total else 1
        work["_uso"] = _num(work[qt_uso]) if qt_uso else None
        work["_total"] = pd.to_numeric(work["_total"], errors="coerce").fillna(0)
        if "_uso" in work and work["_uso"].notna().any():
            agg = work.groupby("municipio", dropna=False).agg(
                equipamentos_cnes_total=("_total", "sum"),
                equipamentos_cnes_em_uso=("_uso", "sum"),
                registros_equipamentos_cnes=("municipio", "size"),
            ).reset_index()
        else:
            agg = work.groupby("municipio", dropna=False).agg(
                equipamentos_cnes_total=("_total", "sum"),
                registros_equipamentos_cnes=("municipio", "size"),
            ).reset_index()
    elif tipo == "servicos":
        serv = _col(work, ["co_servico", "codigo_servico", "servico", "ds_servico", "servico_especializado"])
        if serv:
            work["_servico"] = work[serv].astype(str).str.strip()
            agg = work.groupby("municipio", dropna=False).agg(
                servicos_cnes_total=("_servico", lambda x: int(x[x != ""].nunique())),
                registros_servicos_cnes=("municipio", "size"),
            ).reset_index()
        else:
            agg = work.groupby("municipio", dropna=False).size().reset_index(name="registros_servicos_cnes")
    else:
        hab = _col(work, ["co_habilitacao", "codigo_habilitacao", "habilitacao", "ds_habilitacao"])
        if hab:
            work["_hab"] = work[hab].astype(str).str.strip()
            agg = work.groupby("municipio", dropna=False).agg(
                habilitacoes_cnes_total=("_hab", lambda x: int(x[x != ""].nunique())),
                registros_habilitacoes_cnes=("municipio", "size"),
            ).reset_index()
        else:
            agg = work.groupby("municipio", dropna=False).size().reset_index(name="registros_habilitacoes_cnes")

    registros = []
    for col in agg.columns:
        if col == "municipio":
            continue
        tmp = agg[["municipio", col]].rename(columns={col: "valor"})
        tmp["indicador"] = col
        registros.append(tmp)
    return pd.concat(registros, ignore_index=True) if registros else pd.DataFrame()


def _buscar_e_processar(tipo: str, termo: str) -> tuple[pd.DataFrame, list[str]]:
    tentativas = []
    candidatos = []
    candidatos.extend(URLS_DIRETAS.get(tipo, []))
    try:
        candidatos.extend(_recursos_por_ckan(termo))
    except Exception as exc:
        tentativas.append(f"ckan {termo}: {exc}")
    vistos = set()
    candidatos = [u for u in candidatos if not (u in vistos or vistos.add(u))]
    if not candidatos:
        tentativas.append(f"{tipo}: nenhum recurso CSV/ZIP candidato encontrado")
        return pd.DataFrame(), tentativas
    for url in candidatos[:8]:
        try:
            df = _ler_recurso(url)
            if df.empty:
                tentativas.append(f"{tipo}: {url} -> arquivo vazio/sem CSV útil")
                continue
            agg = _agregar_base(df, tipo)
            if agg.empty:
                tentativas.append(f"{tipo}: {url} -> lido {len(df)} linhas, mas sem municípios de MT ou colunas reconhecidas; colunas={list(df.columns)[:12]}")
                continue
            agg["fonte_url" ] = url
            return agg, tentativas
        except Exception as exc:
            tentativas.append(f"{tipo}: {url} -> erro={exc}")
    return pd.DataFrame(), tentativas


def carregar_cnes_capacidade_instalada_mt() -> pd.DataFrame:
    frames = []
    detalhes = []
    for tipo, termo in CONSULTAS:
        df, det = _buscar_e_processar(tipo, termo)
        detalhes.extend(det)
        if not df.empty:
            frames.append(df)
    if not frames:
        msg = "Não foi possível importar automaticamente CNES capacidade instalada. O CNES é fonte oficial de estabelecimentos, recursos físicos, trabalhadores e serviços, mas nesta tentativa não houve CSV/ZIP público estável reconhecível para equipamentos/serviços/habilitações. Detalhe: " + " | ".join(detalhes[:12])
        raise RuntimeError(msg)
    out = pd.concat(frames, ignore_index=True)
    out["ano"] = datetime.now().year
    out["competencia"] = "CNES_CAPACIDADE_INSTALADA"
    out["fonte"] = "CNES_CAPACIDADE_INSTALADA"
    out["valor"] = pd.to_numeric(out["valor"], errors="coerce")
    out = out.dropna(subset=["municipio", "indicador", "valor"], how="any")
    # salva cópia bruta/útil para auditoria leve
    try:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        out.to_csv(RAW_DIR / "apis" / f"cnes_capacidade_instalada_{datetime.now():%Y%m%d_%H%M%S}.csv", index=False, encoding="utf-8-sig")
    except Exception:
        pass
    return out[["municipio", "ano", "competencia", "indicador", "valor", "fonte"]]


def testar_cnes_capacidade_instalada_mt() -> pd.DataFrame:
    return carregar_cnes_capacidade_instalada_mt()
