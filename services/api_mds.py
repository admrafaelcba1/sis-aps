from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

from config.municipios_mt import DEFAULT_MUNICIPIOS
from config.settings import UPLOADS_DIR

MDS_UPLOAD_DIR = UPLOADS_DIR / "mds"
MDS_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _normalizar_texto(valor) -> str:
    texto = str(valor or "").strip().upper()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    texto = re.sub(r"[^A-Z0-9]+", " ", texto)
    return " ".join(texto.split())


def _municipios_base() -> pd.DataFrame:
    return pd.DataFrame(DEFAULT_MUNICIPIOS)[["municipio", "regiao_saude"]].copy()


def _municipios_mapas():
    base = _municipios_base()
    nome_por_chave = {_normalizar_texto(m): m for m in base["municipio"].astype(str)}
    return nome_por_chave


def _codigo_limpo(valor) -> str:
    digitos = re.sub(r"\D", "", "" if valor is None else str(valor))
    if len(digitos) >= 7:
        return digitos[:7]
    return digitos


def _to_num(serie: pd.Series | None) -> pd.Series:
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


def _ler_arquivo(caminho: Path) -> pd.DataFrame:
    sufixo = caminho.suffix.lower()
    if sufixo in [".xlsx", ".xls"]:
        return pd.read_excel(caminho)
    for sep in [";", ",", "\t", "|"]:
        try:
            df = pd.read_csv(caminho, sep=sep, encoding="utf-8", dtype=str)
            if len(df.columns) > 1:
                return df
        except Exception:
            pass
        try:
            df = pd.read_csv(caminho, sep=sep, encoding="latin1", dtype=str)
            if len(df.columns) > 1:
                return df
        except Exception:
            pass
    return pd.read_csv(caminho, dtype=str)


def _arquivos_locais(tokens: Iterable[str]) -> list[Path]:
    tokens_norm = [_normalizar_texto(t) for t in tokens]
    pastas = [MDS_UPLOAD_DIR, UPLOADS_DIR]
    encontrados: list[Path] = []
    for pasta in pastas:
        if not pasta.exists():
            continue
        for caminho in pasta.rglob("*"):
            if not caminho.is_file() or caminho.suffix.lower() not in [".csv", ".txt", ".xlsx", ".xls"]:
                continue
            nome = _normalizar_texto(caminho.name)
            if all(tok in nome for tok in tokens_norm if tok):
                encontrados.append(caminho)
    return sorted(encontrados, key=lambda p: p.stat().st_mtime, reverse=True)


def _baixar_csv_tentativas(urls: list[str]) -> pd.DataFrame:
    erros = []
    for url in urls:
        try:
            r = requests.get(url, timeout=40, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            ctype = r.headers.get("content-type", "").lower()
            if "html" in ctype and "<html" in r.text[:500].lower():
                raise RuntimeError("resposta HTML; endpoint exige seleção manual ou mudou formato")
            from io import StringIO
            for sep in [";", ",", "\t", "|"]:
                try:
                    df = pd.read_csv(StringIO(r.text), sep=sep, dtype=str)
                    if len(df.columns) > 1:
                        return df
                except Exception:
                    continue
            raise RuntimeError("não foi possível interpretar CSV retornado")
        except Exception as exc:
            erros.append(f"{url} -> {exc}")
    raise RuntimeError("Nenhum endpoint automático MDS retornou CSV tabular. Últimos erros: " + " | ".join(erros[-3:]))


def _achar_coluna(df: pd.DataFrame, padroes: list[str]) -> str | None:
    colunas_norm = {col: _normalizar_texto(col) for col in df.columns}
    for col, norm in colunas_norm.items():
        for pad in padroes:
            if _normalizar_texto(pad) in norm:
                return col
    return None


def _padronizar_mds(df: pd.DataFrame, tipo: str, fonte: str, competencia: str | None = None) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    col_mun = _achar_coluna(out, ["municipio", "nome municipio", "nome do municipio", "ente", "localidade"])
    col_cod = _achar_coluna(out, ["codigo ibge", "cod ibge", "ibge", "codigo municipio", "cod municipio"])
    if not col_mun and not col_cod:
        raise ValueError("Não foi possível identificar município/código IBGE no arquivo MDS.")

    base = pd.DataFrame()
    if col_mun:
        base["municipio"] = out[col_mun].astype(str).str.strip()
        mapa = _municipios_mapas()
        base["municipio"] = base["municipio"].map(lambda x: mapa.get(_normalizar_texto(x), x))
    else:
        base["municipio"] = ""
    if col_cod:
        base["codigo_ibge"] = out[col_cod].map(_codigo_limpo)

    def num_por_padroes(padroes: list[str]):
        col = _achar_coluna(out, padroes)
        return _to_num(out[col]) if col else pd.Series([pd.NA] * len(out))

    if tipo == "cadunico":
        base["cadunico_familias"] = num_por_padroes(["cadunico qtd familias", "cadastro unico qtd familias", "qtd familias", "familias cadastradas"])
        base["cadunico_pessoas"] = num_por_padroes(["cadunico qtd pessoas", "cadastro unico qtd pessoas", "qtd pessoas", "pessoas cadastradas"])
        base["cadunico_familias_pobreza"] = num_por_padroes(["familias em situacao de pobreza", "familias pobreza"])
        base["cadunico_pessoas_pobreza"] = num_por_padroes(["pessoas em situacao de pobreza", "pessoas pobreza"])
        base["cadunico_familias_extrema_pobreza"] = num_por_padroes(["familias em situacao de extrema pobreza", "familias extrema pobreza"])
        base["cadunico_pessoas_extrema_pobreza"] = num_por_padroes(["pessoas em situacao de extrema pobreza", "pessoas extrema pobreza"])
    elif tipo == "bolsa_familia":
        base["bolsa_familia_familias"] = num_por_padroes(["bolsa familia quantidade familias", "total familias beneficiarias", "familias beneficiarias", "qtd familias"])
        base["bolsa_familia_valor_repassado"] = num_por_padroes(["bolsa familia valor repassado", "valor repassado", "valor total", "recurso"])
    elif tipo == "bpc":
        base["bpc_total"] = num_por_padroes(["bpc beneficiario total", "bpc total", "quantidade bpc", "qtd bpc"])
        base["bpc_idoso"] = num_por_padroes(["bpc idoso", "idoso beneficiario"])
        base["bpc_pcd"] = num_por_padroes(["bpc portador deficiencia", "bpc pcd", "deficiencia beneficiario"])

    base["fonte_mds"] = fonte
    base["competencia"] = competencia or "última disponível/arquivo local"
    mapa_mun = _municipios_mapas()
    base["_chave"] = base["municipio"].map(_normalizar_texto)
    base = base[base["_chave"].isin(mapa_mun.keys())].copy()
    base["municipio"] = base["_chave"].map(mapa_mun)
    base = base.drop(columns=["_chave"], errors="ignore")
    base = base.drop_duplicates(subset=["municipio"], keep="last")
    return base


def _placeholder_pendente(tipo: str, motivo: str) -> pd.DataFrame:
    base = _municipios_base()
    base["competencia"] = "pendente"
    base["fonte_mds"] = "pendente de carga MDS"
    base["observacao_mds"] = motivo
    if tipo == "cadunico":
        for col in ["cadunico_familias", "cadunico_pessoas", "cadunico_familias_pobreza", "cadunico_pessoas_pobreza", "cadunico_familias_extrema_pobreza", "cadunico_pessoas_extrema_pobreza"]:
            base[col] = pd.NA
    elif tipo == "bolsa_familia":
        base["bolsa_familia_familias"] = pd.NA
        base["bolsa_familia_valor_repassado"] = pd.NA
    elif tipo == "bpc":
        base["bpc_total"] = pd.NA
        base["bpc_idoso"] = pd.NA
        base["bpc_pcd"] = pd.NA
    return base


def _carregar_mds_generico(tipo: str, tokens: list[str], competencia: str | None = None) -> pd.DataFrame:
    # 1) Fallback preferencial: arquivo exportado manualmente do Painel Dados Abertos/MDS ou VIS DATA.
    arquivos = _arquivos_locais(tokens)
    if arquivos:
        df = _ler_arquivo(arquivos[0])
        return _padronizar_mds(df, tipo=tipo, fonte=f"arquivo local: {arquivos[0].name}", competencia=competencia)

    # 2) Tentativas automáticas: mantidas de forma conservadora, pois os painéis MDS/VIS DATA podem exigir seleção manual.
    # Se mudarem o endpoint, a falha vira base pendente rastreável, sem quebrar o bloco.
    urls = [
        "https://aplicacoes.mds.gov.br/sagi-paineis/analise_dados_abertos/",
        "https://aplicacoes.cidadania.gov.br/vis/data3/data-explorer.php",
    ]
    try:
        df = _baixar_csv_tentativas(urls)
        padronizado = _padronizar_mds(df, tipo=tipo, fonte="MDS/VIS DATA automático", competencia=competencia)
    except Exception as exc:
        raise RuntimeError(
            "A carga automática do MDS não retornou CSV tabular confiável. "
            "Baixe/exporte a planilha no Painel Dados Abertos/MDS ou VIS DATA e coloque em data/uploads/mds/. "
            "Use nomes como cadunico.csv, bolsa_familia.csv ou bpc.csv. Detalhe: " + str(exc)[:700]
        )

    # Validação defensiva: não considerar como sucesso uma base sem nenhum valor numérico útil.
    colunas_valor = [c for c in padronizado.columns if c not in ["municipio", "codigo_ibge", "fonte_mds", "competencia", "observacao_mds"]]
    if not colunas_valor or not any(pd.to_numeric(padronizado[c], errors="coerce").notna().any() for c in colunas_valor):
        raise RuntimeError(
            "A fonte MDS foi acessada, mas não trouxe valores municipais numéricos úteis. "
            "Use arquivo local exportado do MDS/VIS DATA em data/uploads/mds/."
        )
    return padronizado


def carregar_mds_cadunico_mt(competencia: str | None = None) -> pd.DataFrame:
    """Carrega CadÚnico agregado por município.

    Aceita arquivo local com nome contendo, por exemplo: cadunico, cadastro_unico, pobreza.
    """
    return _carregar_mds_generico("cadunico", ["cad"], competencia=competencia)


def carregar_mds_bolsa_familia_mt(competencia: str | None = None) -> pd.DataFrame:
    """Carrega Bolsa Família agregado por município."""
    return _carregar_mds_generico("bolsa_familia", ["bolsa"], competencia=competencia)


def carregar_mds_bpc_mt(competencia: str | None = None) -> pd.DataFrame:
    """Carrega BPC agregado por município."""
    return _carregar_mds_generico("bpc", ["bpc"], competencia=competencia)
