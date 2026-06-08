from __future__ import annotations

import csv
import re
import sqlite3
import unicodedata
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import numpy as np

from config.municipios_mt import DEFAULT_MUNICIPIOS
from config.settings import DB_PATH, ROOT_DIR, UPLOADS_DIR
from database.queries import read_table

MDS_UPLOAD_DIR = UPLOADS_DIR / "mds"
MDS_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
MDS_LOCAL_DIR = ROOT_DIR / "arquivos" / "MDS"

TABELA_MDS = "mds_cadunico_bolsa_familia_municipal"
TABELA_LOG = "mds_importacoes_log"

INDICADORES_MDS: dict[str, dict[str, Any]] = {
    "cadunico_familias": {
        "label": "Famílias inscritas no Cadastro Único",
        "tokens": [["cadunico", "familias"], ["cadastro", "unico", "familias"], ["familias", "cadastradas"]],
        "tipo": "quantidade",
    },
    "cadunico_pessoas": {
        "label": "Pessoas inscritas no Cadastro Único",
        "tokens": [["cadunico", "pessoas"], ["cadastro", "unico", "pessoas"], ["pessoas", "cadastradas"]],
        "tipo": "quantidade",
    },
    "bolsa_familia_familias": {
        "label": "Famílias beneficiárias do Bolsa Família",
        "tokens": [["bolsa", "familia", "familias"], ["familias", "beneficiarias"], ["quantidade", "familias"]],
        "tipo": "quantidade",
    },
    "bolsa_familia_pessoas": {
        "label": "Pessoas beneficiárias do Bolsa Família",
        "tokens": [["bolsa", "familia", "pessoas"], ["pessoas", "beneficiarias"]],
        "tipo": "quantidade",
    },
    "bolsa_familia_valor_repassado": {
        "label": "Valor repassado do Bolsa Família",
        "tokens": [["bolsa", "familia", "valor"], ["valor", "repassado"], ["recurso", "repassado"]],
        "tipo": "moeda",
    },
    "bolsa_familia_valor_medio_informado": {
        "label": "Valor médio informado do benefício do Bolsa Família",
        "tokens": [["bolsa", "familia", "valor", "medio"]],
        "tipo": "moeda",
    },
    "cadunico_familias_baixa_renda": {
        "label": "Famílias de baixa renda no CadÚnico",
        "tokens": [["familias", "baixa", "renda"], ["familias", "meio", "salario"], ["familias", "renda", "per", "capita"]],
        "tipo": "quantidade",
    },
    "cadunico_pessoas_baixa_renda": {
        "label": "Pessoas de baixa renda no CadÚnico",
        "tokens": [["pessoas", "baixa", "renda"], ["pessoas", "meio", "salario"], ["pessoas", "renda", "per", "capita"]],
        "tipo": "quantidade",
    },
    "cadunico_familias_pobreza": {
        "label": "Famílias em pobreza no CadÚnico",
        "tokens": [["familias", "pobreza"]],
        "tipo": "quantidade",
    },
    "cadunico_pessoas_pobreza": {
        "label": "Pessoas em pobreza no CadÚnico",
        "tokens": [["pessoas", "pobreza"]],
        "tipo": "quantidade",
    },
    "cadunico_familias_extrema_pobreza": {
        "label": "Famílias em extrema pobreza no CadÚnico",
        "tokens": [["familias", "extrema", "pobreza"]],
        "tipo": "quantidade",
    },
    "cadunico_pessoas_extrema_pobreza": {
        "label": "Pessoas em extrema pobreza no CadÚnico",
        "tokens": [["pessoas", "extrema", "pobreza"]],
        "tipo": "quantidade",
    },
    "cadunico_familias_pobreza_extrema": {
        "label": "Famílias em pobreza ou extrema pobreza no CadÚnico",
        "tokens": [["familias", "pobreza", "extrema"], ["familias", "pobreza", "e", "extrema"]],
        "tipo": "quantidade",
    },
    "cadunico_pessoas_pobreza_extrema": {
        "label": "Pessoas em pobreza ou extrema pobreza no CadÚnico",
        "tokens": [["pessoas", "pobreza", "extrema"], ["pessoas", "pobreza", "e", "extrema"]],
        "tipo": "quantidade",
    },
    "cadunico_familias_acima_meio_sm": {
        "label": "Famílias no CadÚnico acima de meio salário mínimo",
        "tokens": [["familias", "acima", "meio", "salario"]],
        "tipo": "quantidade",
    },
    "cadunico_rua_familias_pobreza": {"label": "Famílias em situação de rua em pobreza", "tokens": [["situacao", "rua", "pobreza"]], "tipo": "quantidade"},
    "cadunico_rua_familias_baixa_renda": {"label": "Famílias em situação de rua até meio salário mínimo", "tokens": [["situacao", "rua", "meio", "salario"]], "tipo": "quantidade"},
    "cadunico_rua_familias_acima_meio_sm": {"label": "Famílias em situação de rua acima de meio salário mínimo", "tokens": [["situacao", "rua", "acima", "meio"]], "tipo": "quantidade"},
    "pbf_esgoto_rede": {"label": "Famílias PBF com esgoto por rede coletora/pluvial", "tokens": [["escoamento", "rede", "coletora"]], "tipo": "quantidade"},
    "pbf_esgoto_fossa_septica": {"label": "Famílias PBF com fossa séptica", "tokens": [["escoamento", "fossa", "septica"]], "tipo": "quantidade"},
    "pbf_esgoto_fossa_rudimentar": {"label": "Famílias PBF com fossa rudimentar", "tokens": [["escoamento", "fossa", "rudimentar"]], "tipo": "quantidade"},
    "pbf_esgoto_vala": {"label": "Famílias PBF com esgoto por vala a céu aberto", "tokens": [["escoamento", "vala"]], "tipo": "quantidade"},
    "pbf_esgoto_rio_lago_mar": {"label": "Famílias PBF com esgoto direto para rio/lago/mar", "tokens": [["escoamento", "rio", "lago", "mar"]], "tipo": "quantidade"},
    "pbf_esgoto_outras_formas": {"label": "Famílias PBF com outras formas de escoamento", "tokens": [["escoamento", "outras", "formas"]], "tipo": "quantidade"},
    "pbf_esgoto_sem_info": {"label": "Famílias PBF sem informação de escoamento", "tokens": [["escoamento", "sem", "informacao"]], "tipo": "quantidade"},
    "bpc_pcd": {"label": "Beneficiários BPC — PCD", "tokens": [["pcd", "beneficiarias", "bpc"]], "tipo": "quantidade"},
    "bpc_idoso": {"label": "Beneficiários BPC — idosos", "tokens": [["idosos", "beneficiarios", "bpc"]], "tipo": "quantidade"},
    "bpc_total": {"label": "Total de beneficiários do BPC", "tokens": [["total", "beneficiarios", "bpc"]], "tipo": "quantidade"},
    "bpc_valor_pcd": {"label": "Valor BPC repassado a PCD", "tokens": [["valor", "repassado", "pcd", "bpc"]], "tipo": "moeda"},
    "bpc_valor_idoso": {"label": "Valor BPC repassado a idosos", "tokens": [["valor", "repassado", "idosos", "bpc"]], "tipo": "moeda"},
    "bpc_valor_total": {"label": "Valor total repassado ao BPC", "tokens": [["valor", "total", "bpc"]], "tipo": "moeda"},
    "bpc_cadunico_total": {"label": "Beneficiários BPC no CadÚnico", "tokens": [["beneficiarios", "bpc", "cadastro", "unico"]], "tipo": "quantidade"},
    "bpc_cadunico_pcd": {"label": "Beneficiários BPC PCD no CadÚnico", "tokens": [["bpc", "cadastro", "unico", "pcd"]], "tipo": "quantidade"},
    "bpc_cadunico_idoso": {"label": "Beneficiários BPC idoso no CadÚnico", "tokens": [["bpc", "cadastro", "unico", "idoso"]], "tipo": "quantidade"},
}
COLUNAS_FINAIS = [
    "codigo_ibge",
    "municipio",
    "regiao_saude",
    "populacao",
    "mes_referencia",
    "ano_referencia",
    "competencia",
    *INDICADORES_MDS.keys(),
    "pct_populacao_cadunico",
    "pct_populacao_bolsa_familia",
    "pct_familias_pbf_sobre_cadunico",
    "pct_familias_baixa_renda_sobre_cadunico",
    "pct_familias_pobreza_extrema_sobre_cadunico",
    "valor_medio_bolsa_familia_por_familia",
    "score_vulnerabilidade_mds",
    "classificacao_vulnerabilidade_mds",
    "ranking_vulnerabilidade_mds",
    "arquivos_origem",
    "data_importacao",
]


def _normalizar_texto(valor: Any) -> str:
    if valor is None or pd.isna(valor):
        texto = ""
    else:
        texto = str(valor).strip().lower()
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    texto = " ".join(texto.split())
    # Compatibiliza grafias do VIS DATA como DOESTE com nomes oficiais D'Oeste.
    texto = texto.replace(" d oeste", " doeste")
    return texto


@lru_cache(maxsize=1)
def _codigos_ibge_oficiais() -> pd.DataFrame:
    """Carrega códigos IBGE oficiais de 7 dígitos disponíveis no próprio projeto.

    O VIS DATA costuma exportar códigos municipais com 6 dígitos. Para manter
    compatibilidade com IBGE/CNES/georreferenciamento, o consolidado final deve
    guardar o código oficial de 7 dígitos sempre que possível.
    """
    candidatos = [
        ROOT_DIR / "data" / "geo" / "municipios_mt_georreferencia.csv",
        ROOT_DIR / "data" / "geo" / "areas_territoriais_ibge_2025_mt.csv",
    ]
    for caminho in candidatos:
        try:
            if not caminho.exists():
                continue
            df = pd.read_csv(caminho, dtype=str)
            col_mun = _achar_coluna(df, ["municipio", "município", "nome municipio"])
            col_cod = _achar_coluna(df, ["codigo_ibge", "código ibge", "cod ibge", "ibge"])
            if col_mun and col_cod:
                out = df[[col_mun, col_cod]].copy()
                out.columns = ["municipio", "codigo_ibge"]
                out["municipio_norm"] = out["municipio"].map(_normalizar_texto)
                out["codigo_ibge"] = out["codigo_ibge"].map(_codigo_limpo_basico)
                out = out[out["codigo_ibge"].astype(str).str.len().ge(6)]
                if not out.empty:
                    return out.drop_duplicates("municipio_norm")
        except Exception:
            continue
    return pd.DataFrame(columns=["municipio", "codigo_ibge", "municipio_norm"])


def _codigo_limpo_basico(valor: Any) -> str:
    digitos = re.sub(r"\D", "", "" if valor is None else str(valor))
    if len(digitos) >= 7:
        return digitos[:7]
    if len(digitos) >= 6:
        return digitos[:6]
    return digitos


def _municipios_base() -> pd.DataFrame:
    base = pd.DataFrame(DEFAULT_MUNICIPIOS).copy()
    if "codigo_ibge" not in base.columns:
        base["codigo_ibge"] = pd.NA
    base["municipio_norm"] = base["municipio"].map(_normalizar_texto)

    oficiais = _codigos_ibge_oficiais()
    if not oficiais.empty:
        mapa_cod = dict(zip(oficiais["municipio_norm"], oficiais["codigo_ibge"]))
        base["codigo_ibge"] = base["codigo_ibge"].where(
            base["codigo_ibge"].notna() & base["codigo_ibge"].astype(str).str.len().ge(6),
            base["municipio_norm"].map(mapa_cod),
        )

    cols = [c for c in ["codigo_ibge", "municipio", "regiao_saude"] if c in base.columns]
    return base[cols].drop_duplicates("municipio")


def _mapas_municipios():
    base = _municipios_base()
    por_nome = {_normalizar_texto(m): m for m in base["municipio"].dropna().astype(str)}
    regiao = dict(zip(base["municipio"], base["regiao_saude"])) if "regiao_saude" in base.columns else {}
    return por_nome, regiao


def _codigo_limpo(valor: Any) -> str:
    return _codigo_limpo_basico(valor)


def _resolver_codigo_ibge(municipio: Any, codigo_visdata: Any = None) -> Any:
    """Resolve o código IBGE final, preferindo o código oficial de 7 dígitos.

    1) Se o município existir na base geográfica do sistema, usa o código oficial.
    2) Se o código VIS DATA tiver 6 dígitos, tenta localizar o código oficial pelo prefixo.
    3) Se não houver correspondência, preserva o código limpo recebido.
    """
    cod = _codigo_limpo_basico(codigo_visdata)
    oficiais = _codigos_ibge_oficiais()
    if not oficiais.empty:
        nome_norm = _normalizar_texto(municipio)
        por_nome = oficiais.set_index("municipio_norm")["codigo_ibge"].to_dict()
        if nome_norm in por_nome:
            return por_nome[nome_norm]
        if cod and len(cod) == 6:
            candidatos = oficiais[oficiais["codigo_ibge"].astype(str).str.startswith(cod)]
            if len(candidatos) == 1:
                return candidatos.iloc[0]["codigo_ibge"]
    return cod if cod else pd.NA


def _to_num(serie: pd.Series | None) -> pd.Series:
    if serie is None:
        return pd.Series(dtype="float64")
    txt = serie.astype(str).str.strip()
    # Remove marcações comuns de nulo antes de limpar sinais monetários.
    txt = txt.mask(txt.str.lower().isin(["", "nan", "none", "null", "-", "--"]), None)
    txt = txt.str.replace("R$", "", regex=False).str.replace("%", "", regex=False)
    # Padrão brasileiro: 1.234,56. Também preserva números já em 1234.56.
    txt = txt.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    txt = txt.str.replace(r"[^0-9\.\-]", "", regex=True)
    return pd.to_numeric(txt, errors="coerce")


def _ler_arquivo_mds(arquivo: Any, nrows: int | None = None) -> pd.DataFrame:
    nome = getattr(arquivo, "name", None) or str(arquivo)
    sufixo = Path(nome).suffix.lower()
    if sufixo in [".xlsx", ".xls"]:
        return pd.read_excel(arquivo, dtype=str, nrows=nrows)

    if hasattr(arquivo, "seek"):
        arquivo.seek(0)

    # VIS DATA normalmente exporta CSV separado por vírgula com aspas e codificação latin1/cp1252.
    # Detectar pelo cabeçalho evita ler arquivos grandes várias vezes com separador errado.
    encodings = ["utf-8-sig", "utf-8", "latin1", "cp1252"]
    seps = [",", ";", "\t", "|"]
    melhor_erro: Exception | None = None

    for enc in encodings:
        try:
            if hasattr(arquivo, "seek"):
                arquivo.seek(0)
            if isinstance(arquivo, (str, Path)):
                with open(arquivo, "r", encoding=enc, errors="strict") as f:
                    cab = f.readline()
            else:
                pos = arquivo.tell() if hasattr(arquivo, "tell") else 0
                bruto = arquivo.read(4096)
                if hasattr(arquivo, "seek"):
                    arquivo.seek(pos)
                if isinstance(bruto, bytes):
                    cab = bruto.decode(enc, errors="strict")
                else:
                    cab = str(bruto)
            sep_preferido = max(seps, key=lambda sep: cab.count(sep))
            if cab.count(sep_preferido) == 0:
                sep_preferido = ","
            if hasattr(arquivo, "seek"):
                arquivo.seek(0)
            df = pd.read_csv(arquivo, sep=sep_preferido, encoding=enc, dtype=str, nrows=nrows)
            if len(df.columns) > 1:
                return df
        except Exception as exc:
            melhor_erro = exc
            continue

    # Último fallback: pandas tenta inferir. Mantido para formatos inesperados.
    if hasattr(arquivo, "seek"):
        arquivo.seek(0)
    try:
        return pd.read_csv(arquivo, sep=None, engine="python", dtype=str, nrows=nrows)
    except Exception:
        if melhor_erro:
            raise melhor_erro
        raise


def _competencia_por_nome(nome: str) -> tuple[int | None, int | None]:
    # Padrão usado no projeto: *_YYYY_MM.csv
    m = re.search(r"(20\d{2})[_\-](0?[1-9]|1[0-2])", str(nome))
    if m:
        return int(m.group(2)), int(m.group(1))
    return None, None


def _detectar_csv_mds(arquivo: Any) -> tuple[str, str]:
    """Detecta separador e codificação do CSV VIS DATA com leitura mínima."""
    encodings = ["utf-8-sig", "utf-8", "latin1", "cp1252"]
    seps = [",", ";", "\t", "|"]
    amostra_bytes = b""
    if isinstance(arquivo, (str, Path)):
        with open(arquivo, "rb") as f:
            amostra_bytes = f.read(8192)
    else:
        pos = arquivo.tell() if hasattr(arquivo, "tell") else 0
        bruto = arquivo.read(8192)
        if hasattr(arquivo, "seek"):
            arquivo.seek(pos)
        amostra_bytes = bruto if isinstance(bruto, bytes) else str(bruto).encode("utf-8", errors="ignore")
    melhor_enc = "latin1"
    texto = ""
    for enc in encodings:
        try:
            texto = amostra_bytes.decode(enc)
            melhor_enc = enc
            break
        except Exception:
            continue
    sep = max(seps, key=lambda x: texto.count(x)) if texto else ","
    return melhor_enc, sep


def _ler_csv_mds_filtrado(arquivo: Any, competencia_manual: str | None = None) -> pd.DataFrame:
    """Leitura rápida para CSVs VIS DATA: mantém apenas MT e a competência alvo.

    Usa chunks do pandas, que é muito mais rápido do que percorrer arquivos grandes
    linha a linha em Python.
    """
    nome = getattr(arquivo, "name", None) or str(arquivo)
    alvo_mes, alvo_ano = _extrair_mes_ano_referencia(competencia_manual) if competencia_manual else _competencia_por_nome(nome)
    enc, sep = _detectar_csv_mds(arquivo)

    partes: list[pd.DataFrame] = []
    try:
        if hasattr(arquivo, "seek"):
            arquivo.seek(0)
        leitor = pd.read_csv(arquivo, sep=sep, encoding=enc, dtype=str, chunksize=120000, low_memory=False)
        for chunk in leitor:
            if chunk.empty:
                continue
            col_uf = _achar_coluna(chunk, ["uf", "estado"])
            if col_uf:
                chunk = chunk[chunk[col_uf].map(_normalizar_texto).isin({"mt", "mato grosso"})].copy()
                if chunk.empty:
                    continue
            col_ref = _achar_coluna(chunk, ["referencia", "referência", "competencia", "mês", "mes"])
            if col_ref and alvo_mes and alvo_ano:
                refs = chunk[col_ref].map(_extrair_mes_ano_referencia)
                ref_df = pd.DataFrame(refs.tolist(), columns=["_mes_ref", "_ano_ref"], index=chunk.index)
                chunk = chunk[(ref_df["_mes_ref"] == alvo_mes) & (ref_df["_ano_ref"] == alvo_ano)].copy()
                if chunk.empty:
                    continue
            partes.append(chunk)
    except Exception:
        raise

    if not partes:
        return pd.DataFrame()
    df = pd.concat(partes, ignore_index=True, sort=False)
    if not (alvo_mes and alvo_ano):
        df = _filtrar_competencia_mds(df, competencia_manual=None)
    return df


def _ler_arquivo_mds_importacao(arquivo: Any, competencia_manual: str | None = None) -> pd.DataFrame:
    nome = getattr(arquivo, "name", None) or str(arquivo)
    sufixo = Path(nome).suffix.lower()
    if sufixo in [".csv", ".txt", ""]:
        try:
            df = _ler_csv_mds_filtrado(arquivo, competencia_manual=competencia_manual)
            if not df.empty:
                return df
        except Exception:
            pass
    df = _ler_arquivo_mds(arquivo)
    return _filtrar_competencia_mds(df, competencia_manual)

def _achar_coluna(df: pd.DataFrame, padroes: Iterable[str]) -> str | None:
    norm_cols = {col: _normalizar_texto(col) for col in df.columns}
    for col, norm in norm_cols.items():
        for padrao in padroes:
            if _normalizar_texto(padrao) in norm:
                return col
    return None


def _colunas_municipio(df: pd.DataFrame) -> tuple[str | None, str | None]:
    col_cod = _achar_coluna(df, ["codigo ibge", "cod ibge", "ibge", "codigo municipio", "cod municipio", "cod mun", "codmun", "codigo"])
    col_mun = _achar_coluna(df, ["municipio", "nome municipio", "nome do municipio", "localidade", "unidade territorial", "ente", "nome"])
    return col_mun, col_cod


def _inferir_competencia(df: pd.DataFrame, competencia_manual: str | None = None) -> tuple[Any, Any, str]:
    if competencia_manual:
        nums = re.findall(r"\d+", str(competencia_manual))
        ano = next((int(n) for n in nums if len(n) == 4), None)
        mes = next((int(n) for n in nums if len(n) <= 2 and 1 <= int(n) <= 12), None)
        return mes, ano, str(competencia_manual)

    col_ano = _achar_coluna(df, ["ano", "ano referencia", "ano competencia"])
    col_mes = _achar_coluna(df, ["mes", "mês", "mes referencia", "competencia", "referencia"])
    ano = None
    mes = None
    if col_ano:
        vals = pd.to_numeric(df[col_ano].astype(str).str.extract(r"(\d{4})")[0], errors="coerce").dropna()
        if not vals.empty:
            ano = int(vals.max())
    if col_mes:
        vals = pd.to_numeric(df[col_mes].astype(str).str.extract(r"(\d{1,2})")[0], errors="coerce").dropna()
        vals = vals[(vals >= 1) & (vals <= 12)]
        if not vals.empty:
            mes = int(vals.max())
        if ano is None:
            anos = pd.to_numeric(df[col_mes].astype(str).str.extract(r"(20\d{2})")[0], errors="coerce").dropna()
            if not anos.empty:
                ano = int(anos.max())

    comp = f"{ano:04d}-{mes:02d}" if ano and mes else (str(ano) if ano else "arquivo importado")
    return mes, ano, comp



def _extrair_mes_ano_referencia(valor: Any) -> tuple[int | None, int | None]:
    texto = str(valor or "").strip()
    nums = re.findall(r"\d+", texto)
    if not nums:
        return None, None
    ano = next((int(n) for n in nums if len(n) == 4 and 1900 <= int(n) <= 2100), None)
    mes = None
    # VIS DATA costuma vir como MM/AAAA.
    if nums and len(nums[0]) <= 2:
        try:
            cand = int(nums[0])
            if 1 <= cand <= 12:
                mes = cand
        except Exception:
            pass
    if mes is None:
        mes = next((int(n) for n in nums if len(n) <= 2 and 1 <= int(n) <= 12), None)
    return mes, ano


def _filtrar_competencia_mds(df: pd.DataFrame, competencia_manual: str | None = None) -> pd.DataFrame:
    """Mantém somente a competência desejada ou a última competência disponível do arquivo.

    Os CSVs do VIS DATA costumam baixar a série histórica inteira. Sem esse filtro, o sistema
    somaria todos os anos do município. Esta função evita esse erro e preserva apenas o mês/ano
    mais recente ou o mês/ano informado manualmente.
    """
    col_ref = _achar_coluna(df, ["referencia", "referência", "competencia", "mês", "mes"])
    if not col_ref:
        return df

    refs = df[col_ref].map(_extrair_mes_ano_referencia)
    ref_df = pd.DataFrame(refs.tolist(), columns=["_mes_ref", "_ano_ref"], index=df.index)
    validas = ref_df["_mes_ref"].notna() & ref_df["_ano_ref"].notna()
    if not validas.any():
        return df

    alvo_mes = alvo_ano = None
    if competencia_manual:
        alvo_mes, alvo_ano = _extrair_mes_ano_referencia(competencia_manual)
    if alvo_mes and alvo_ano:
        mask = (ref_df["_mes_ref"] == alvo_mes) & (ref_df["_ano_ref"] == alvo_ano)
        if mask.any():
            return df.loc[mask].copy()

    ref_df_validas = ref_df[validas].copy()
    ref_df_validas["_ordem_ref"] = ref_df_validas["_ano_ref"].astype(int) * 100 + ref_df_validas["_mes_ref"].astype(int)
    ultima = ref_df_validas["_ordem_ref"].max()
    return df.loc[ref_df_validas.index[ref_df_validas["_ordem_ref"].eq(ultima)]].copy()

def _inferir_indicador_por_texto(texto: str) -> str | None:
    texto_n = _normalizar_texto(texto)

    # BPC precisa vir antes de Bolsa Família/CadÚnico porque vários nomes contêm "Cadastro Único".
    if "bpc" in texto_n:
        if "cadastro unico" in texto_n or "cadunico" in texto_n:
            if "pcd" in texto_n:
                return "bpc_cadunico_pcd"
            if "idoso" in texto_n:
                return "bpc_cadunico_idoso"
            return "bpc_cadunico_total"
        if "valor" in texto_n and "pcd" in texto_n:
            return "bpc_valor_pcd"
        if "valor" in texto_n and ("idoso" in texto_n or "idosos" in texto_n):
            return "bpc_valor_idoso"
        if "valor" in texto_n and "total" in texto_n:
            return "bpc_valor_total"
        if "pcd" in texto_n or "deficiencia" in texto_n:
            return "bpc_pcd"
        if "idoso" in texto_n or "idosos" in texto_n:
            return "bpc_idoso"
        if "total" in texto_n and "beneficiarios" in texto_n:
            return "bpc_total"

    # Situação de rua no CadÚnico.
    if "situacao de rua" in texto_n or ("situacao" in texto_n and "rua" in texto_n):
        if "acima" in texto_n and "meio" in texto_n:
            return "cadunico_rua_familias_acima_meio_sm"
        if "meio" in texto_n or "baixa renda" in texto_n:
            return "cadunico_rua_familias_baixa_renda"
        if "pobreza" in texto_n:
            return "cadunico_rua_familias_pobreza"

    # Saneamento/esgotamento em famílias beneficiárias do PBF.
    if "escoamento" in texto_n or "esgoto" in texto_n or "sanitario" in texto_n:
        if "rede coletora" in texto_n or "pluvial" in texto_n:
            return "pbf_esgoto_rede"
        if "fossa septica" in texto_n:
            return "pbf_esgoto_fossa_septica"
        if "fossa rudimentar" in texto_n:
            return "pbf_esgoto_fossa_rudimentar"
        if "vala" in texto_n:
            return "pbf_esgoto_vala"
        if all(t in texto_n for t in ["rio", "lago", "mar"]):
            return "pbf_esgoto_rio_lago_mar"
        if "outras formas" in texto_n:
            return "pbf_esgoto_outras_formas"
        if "sem informacao" in texto_n:
            return "pbf_esgoto_sem_info"

    # CadÚnico por faixa de renda precisa vir antes do PBF, pois algumas colunas citam a faixa do Programa Bolsa Família.
    if ("cadunico" in texto_n or "cadastro unico" in texto_n or "cadastradas no cadastro unico" in texto_n):
        if "acima" in texto_n and "meio" in texto_n and "famil" in texto_n:
            return "cadunico_familias_acima_meio_sm"
        if "pobreza baixa renda" in texto_n and "famil" in texto_n:
            return "cadunico_familias_baixa_renda"
        if "situacao de pobreza" in texto_n and "famil" in texto_n:
            return "cadunico_familias_pobreza_extrema"
        if "extrema pobreza" in texto_n and "famil" in texto_n:
            return "cadunico_familias_extrema_pobreza"
        if "extrema pobreza" in texto_n and "pessoa" in texto_n:
            return "cadunico_pessoas_extrema_pobreza"
        if "pobreza" in texto_n and "famil" in texto_n:
            return "cadunico_familias_pobreza"
        if "pobreza" in texto_n and "pessoa" in texto_n:
            return "cadunico_pessoas_pobreza"
        if ("baixa renda" in texto_n or "meio salario" in texto_n or "meio sm" in texto_n) and "famil" in texto_n:
            return "cadunico_familias_baixa_renda"
        if ("baixa renda" in texto_n or "meio salario" in texto_n or "meio sm" in texto_n) and "pessoa" in texto_n:
            return "cadunico_pessoas_baixa_renda"
        if "pessoa" in texto_n:
            return "cadunico_pessoas"
        if "famil" in texto_n:
            return "cadunico_familias"

    # Bolsa Família atual. Colunas antigas ficam vazias nas competências atuais; a seleção posterior escolhe a coluna com mais valores.
    if ("bolsa" in texto_n or "pbf" in texto_n) and not ("cadastro unico" in texto_n or "cadunico" in texto_n):
        if "valor medio" in texto_n:
            return "bolsa_familia_valor_medio_informado"
        if "valor" in texto_n:
            return "bolsa_familia_valor_repassado"
        if "pessoa" in texto_n:
            return "bolsa_familia_pessoas"
        if "famil" in texto_n:
            return "bolsa_familia_familias"

    # CadÚnico por faixa de renda.
    if "acima" in texto_n and "meio" in texto_n and "famil" in texto_n:
        return "cadunico_familias_acima_meio_sm"
    if "pobreza baixa renda" in texto_n and "famil" in texto_n:
        return "cadunico_familias_baixa_renda"
    if "situacao de pobreza" in texto_n and "famil" in texto_n:
        return "cadunico_familias_pobreza_extrema"
    if "extrema pobreza" in texto_n and "famil" in texto_n:
        return "cadunico_familias_extrema_pobreza"
    if "extrema pobreza" in texto_n and "pessoa" in texto_n:
        return "cadunico_pessoas_extrema_pobreza"
    if "pobreza" in texto_n and "famil" in texto_n:
        return "cadunico_familias_pobreza"
    if "pobreza" in texto_n and "pessoa" in texto_n:
        return "cadunico_pessoas_pobreza"
    if ("baixa renda" in texto_n or "meio salario" in texto_n or "meio sm" in texto_n) and "famil" in texto_n:
        return "cadunico_familias_baixa_renda"
    if ("baixa renda" in texto_n or "meio salario" in texto_n or "meio sm" in texto_n) and "pessoa" in texto_n:
        return "cadunico_pessoas_baixa_renda"
    if ("cadunico" in texto_n or "cadastro unico" in texto_n) and "pessoa" in texto_n:
        return "cadunico_pessoas"
    if ("cadunico" in texto_n or "cadastro unico" in texto_n or "familias cadastradas" in texto_n) and "famil" in texto_n:
        return "cadunico_familias"

    for chave, meta in INDICADORES_MDS.items():
        for grupo in meta["tokens"]:
            if all(tok in texto_n for tok in grupo):
                return chave
    return None

def _colunas_indicadores_consolidados(df: pd.DataFrame) -> dict[str, str]:
    """Mapeia colunas para campos internos, preferindo a coluna com valores na competência filtrada.

    Alguns downloads VIS DATA trazem coluna antiga e coluna atual do PBF no mesmo arquivo
    (até Out/2021 e a partir de Mar/2023). Por isso, quando duas colunas apontam para o
    mesmo indicador, a coluna com maior quantidade de valores numéricos válidos é mantida.
    """
    candidatas: dict[str, list[tuple[str, int, int]]] = {}
    for ordem, col in enumerate(df.columns):
        indicador = _inferir_indicador_por_texto(str(col))
        if not indicador:
            continue
        qtd_validos = int(_to_num(df[col]).notna().sum())
        candidatas.setdefault(indicador, []).append((col, qtd_validos, ordem))

    achadas: dict[str, str] = {}
    for indicador, cols in candidatas.items():
        # Mais valores válidos primeiro; em empate, coluna mais à direita tende a ser a versão atual.
        melhor = sorted(cols, key=lambda item: (item[1], item[2]), reverse=True)[0]
        if melhor[1] > 0:
            achadas[indicador] = melhor[0]
    return achadas

def _coluna_valor_generica(df: pd.DataFrame, ignorar: set[str]) -> str | None:
    preferencias = ["valor", "quantidade", "qtd", "total", "vl", "val"]
    for padrao in preferencias:
        col = _achar_coluna(df[[c for c in df.columns if c not in ignorar]], [padrao])
        if col:
            return col
    candidatas = []
    for col in df.columns:
        if col in ignorar:
            continue
        nums = _to_num(df[col])
        if nums.notna().sum() > 0:
            candidatas.append((col, nums.notna().sum()))
    if candidatas:
        return sorted(candidatas, key=lambda x: x[1], reverse=True)[0][0]
    return None


def diagnosticar_arquivo_mds(arquivo: Any) -> dict[str, Any]:
    nome = getattr(arquivo, "name", None) or Path(str(arquivo)).name

    # Para arquivos locais grandes do VIS DATA, usa a mesma rotina otimizada da importação.
    # Isso evita diagnósticos enganosos com amostras parciais da série histórica nacional.
    try:
        df = _ler_arquivo_mds_importacao(arquivo)
    except Exception:
        df = _ler_arquivo_mds(arquivo, nrows=5000)
        df = _filtrar_competencia_mds(df)

    col_mun, col_cod = _colunas_municipio(df)
    cols_ind = _colunas_indicadores_consolidados(df)
    indicador_nome = _inferir_indicador_por_texto(nome)

    col_ref = _achar_coluna(df, ["referencia", "referência", "competencia", "mês", "mes"])
    col_uf = _achar_coluna(df, ["uf", "estado"])
    ignorar = {c for c in [col_mun, col_cod, col_ref, col_uf] if c}
    valor = None if cols_ind else _coluna_valor_generica(df, ignorar)
    mes, ano, competencia = _inferir_competencia(df)

    municipios_mt = 0
    try:
        por_nome, _ = _mapas_municipios()
        nomes = df[col_mun].astype(str).map(_normalizar_texto) if col_mun else pd.Series(dtype=str)
        codigos = df[col_cod].map(_codigo_limpo_basico) if col_cod else pd.Series(dtype=str)
        mask_nome = nomes.isin(por_nome.keys()) if col_mun else pd.Series([False] * len(df), index=df.index)
        mask_cod = codigos.astype(str).str.startswith("51", na=False) if col_cod else pd.Series([False] * len(df), index=df.index)
        municipios_mt = int((mask_nome | mask_cod).sum())
    except Exception:
        municipios_mt = 0

    campos_detectados = list(cols_ind.keys())
    situacao = "OK" if (col_mun or col_cod) and (cols_ind or indicador_nome or valor) else "Verificar colunas/arquivo"
    observacao = ""
    if situacao == "OK" and municipios_mt and municipios_mt < 142:
        observacao = "Arquivo válido, mas com menos de 142 linhas municipais na competência detectada; pode ser indicador categorizado ou parcial."
    if situacao == "OK" and not campos_detectados and indicador_nome and valor:
        observacao = "Indicador identificado pelo nome do arquivo e coluna de valor genérica."

    return {
        "arquivo": nome,
        "linhas_competencia": int(len(df)),
        "municipios_mt_detectados": municipios_mt,
        "colunas": int(len(df.columns)),
        "coluna_municipio": col_mun,
        "coluna_codigo_ibge": col_cod,
        "indicador_inferido_pelo_nome": indicador_nome or "-",
        "coluna_valor_generica": valor or "-",
        "indicadores_detectados_em_colunas": ", ".join(campos_detectados) if campos_detectados else "-",
        "competencia_detectada": competencia,
        "ano_referencia_detectado": ano,
        "mes_referencia_detectado": mes,
        "situacao": situacao,
        "observacao": observacao,
    }


def _preparar_base_arquivo(arquivo: Any, competencia_manual: str | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    nome = getattr(arquivo, "name", None) or Path(str(arquivo)).name
    df = _ler_arquivo_mds_importacao(arquivo, competencia_manual)
    if df.empty:
        raise ValueError(f"Arquivo vazio ou sem registros de MT na competência esperada: {nome}")

    col_mun, col_cod = _colunas_municipio(df)
    if not col_mun and not col_cod:
        raise ValueError(f"Não identifiquei município ou código IBGE no arquivo {nome}.")

    por_nome, regiao = _mapas_municipios()
    base = pd.DataFrame(index=df.index)
    if col_mun:
        nomes = df[col_mun].astype(str).str.strip()
        base["municipio"] = nomes.map(lambda x: por_nome.get(_normalizar_texto(x), x))
    else:
        base["municipio"] = pd.NA

    if col_cod:
        base["codigo_ibge"] = df[col_cod].map(_codigo_limpo)
    else:
        base["codigo_ibge"] = pd.NA

    # Filtro MT: quando houver nome, usa a lista oficial dos 142 municípios para evitar
    # linhas agregadas de UF/região. Sem nome, usa código começando por 51 como fallback.
    chave_mun = base["municipio"].map(_normalizar_texto)
    mask_mt_nome = chave_mun.isin(por_nome.keys())
    mask_mt_cod = base["codigo_ibge"].astype(str).str.startswith("51", na=False)
    mask_mt = mask_mt_nome if col_mun else mask_mt_cod
    base = base[mask_mt].copy()
    df_mt = df.loc[base.index].copy()
    if base.empty:
        raise ValueError(f"O arquivo {nome} não trouxe municípios reconhecidos de Mato Grosso.")

    base["municipio"] = base["municipio"].map(lambda x: por_nome.get(_normalizar_texto(x), x))
    base["regiao_saude"] = base["municipio"].map(regiao)
    base["codigo_ibge"] = [
        _resolver_codigo_ibge(mun, cod) for mun, cod in zip(base["municipio"], base["codigo_ibge"])
    ]

    mes, ano, competencia = _inferir_competencia(df_mt, competencia_manual)
    base["mes_referencia"] = mes
    base["ano_referencia"] = ano
    base["competencia"] = competencia

    colunas_ind = _colunas_indicadores_consolidados(df_mt)
    indicador_nome = _inferir_indicador_por_texto(nome)
    ignorar = {c for c in [col_mun, col_cod] if c}

    if colunas_ind:
        for indicador, col in colunas_ind.items():
            base[indicador] = _to_num(df_mt[col])
    else:
        col_valor = _coluna_valor_generica(df_mt, ignorar)
        if not indicador_nome:
            raise ValueError(f"Não consegui inferir o indicador pelo nome do arquivo {nome}. Renomeie conforme o padrão mds_*.csv.")
        if not col_valor:
            raise ValueError(f"Não consegui identificar a coluna de valor/quantidade no arquivo {nome}.")
        base[indicador_nome] = _to_num(df_mt[col_valor])

    meta = {
        "arquivo": nome,
        "linhas_competencia": int(len(df_mt)),
        "municipios_mt_detectados": int(base["municipio"].nunique()),
        "colunas": int(len(df_mt.columns)),
        "coluna_municipio": col_mun,
        "coluna_codigo_ibge": col_cod,
        "indicador_inferido_pelo_nome": indicador_nome or "-",
        "coluna_valor_generica": "-" if colunas_ind else (col_valor if 'col_valor' in locals() else "-"),
        "indicadores_detectados_em_colunas": ", ".join(colunas_ind.keys()) if colunas_ind else "-",
        "competencia_detectada": competencia,
        "ano_referencia_detectado": ano,
        "mes_referencia_detectado": mes,
        "situacao": "OK",
        "observacao": "",
    }
    return base, meta


def _carregar_populacao_base() -> pd.DataFrame:
    for tabela in ["base_municipal_consolidada", "aps_estrutural", "municipios", "populacao_municipal"]:
        df = read_table(tabela)
        if not df.empty and "municipio" in df.columns and "populacao" in df.columns:
            out = df[["municipio", "populacao"]].drop_duplicates("municipio").copy()
            out["populacao"] = _to_num(out["populacao"])
            return out
    return pd.DataFrame(columns=["municipio", "populacao"])


def _safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    """Divisão segura sempre retornando float/NaN.

    Em alguns ambientes, Series com pd.NA ficam com dtype object e o pandas chama
    round() sobre NAType, gerando: "type NAType doesn't define __round__ method".
    Por isso, esta função converte tudo para float e usa np.nan para ausências.
    """
    num = pd.to_numeric(num, errors="coerce").astype("float64")
    den = pd.to_numeric(den, errors="coerce").astype("float64")
    den = den.mask(den == 0, np.nan)
    out = num.divide(den)
    return out.replace([np.inf, -np.inf], np.nan).astype("float64")


def _normalizar_0_100(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce").astype("float64")
    if s.dropna().empty:
        return pd.Series([np.nan] * len(s), index=s.index, dtype="float64")
    mn = s.min(skipna=True)
    mx = s.max(skipna=True)
    if pd.isna(mn) or pd.isna(mx) or mx == mn:
        return pd.Series([50.0 if pd.notna(v) else np.nan for v in s], index=s.index, dtype="float64")
    return ((s - mn) / (mx - mn) * 100).clip(lower=0, upper=100).astype("float64")


def _score_ponderado_componentes(componentes: list[tuple[pd.Series, float]], index: pd.Index) -> pd.Series:
    """Calcula score ponderado sem anular o resultado quando um componente faltar.

    Exemplo: se pobreza/extrema pobreza ainda não foi importada para um município,
    o score é calculado com os demais componentes disponíveis, reponderando os pesos.
    Se nenhum componente existir, retorna NaN e a classificação fica sem classificação.
    """
    numerador = pd.Series(0.0, index=index, dtype="float64")
    denominador = pd.Series(0.0, index=index, dtype="float64")
    for serie, peso in componentes:
        s = pd.to_numeric(serie, errors="coerce").astype("float64")
        valido = s.notna()
        numerador = numerador.add(s.fillna(0.0) * peso, fill_value=0.0)
        denominador = denominador.add(pd.Series(np.where(valido, peso, 0.0), index=index, dtype="float64"), fill_value=0.0)
    return numerador.divide(denominador.replace(0, np.nan)).astype("float64")


def _classificar_score(score: Any) -> str:
    try:
        v = float(score)
    except Exception:
        return "Sem classificação"
    if v >= 75:
        return "Muito alta vulnerabilidade relativa"
    if v >= 50:
        return "Alta vulnerabilidade relativa"
    if v >= 25:
        return "Média vulnerabilidade relativa"
    return "Baixa vulnerabilidade relativa"


def _round_num(s: pd.Series, casas: int = 2) -> pd.Series:
    """Arredondamento seguro para evitar erro de NAType em Series object."""
    return pd.to_numeric(s, errors="coerce").astype("float64").round(casas)

def _finalizar_consolidado(df: pd.DataFrame, arquivos: list[str]) -> pd.DataFrame:
    base_mun = _municipios_base()
    por_nome, regiao = _mapas_municipios()
    out = base_mun.copy()

    ind_cols = [c for c in INDICADORES_MDS if c in df.columns]
    agg = {c: "sum" for c in ind_cols}
    for col in ["codigo_ibge", "regiao_saude", "competencia", "mes_referencia", "ano_referencia"]:
        if col in df.columns:
            agg[col] = "last"
    if not ind_cols:
        raise ValueError("Nenhum indicador MDS foi identificado nos arquivos enviados.")

    consolidado = df.groupby("municipio", dropna=False).agg(agg).reset_index()
    out = out.merge(consolidado, on="municipio", how="left", suffixes=("", "_imp"))
    if "codigo_ibge_imp" in out.columns:
        out["codigo_ibge"] = out["codigo_ibge"].where(
            out["codigo_ibge"].notna() & out["codigo_ibge"].astype(str).str.len().ge(6),
            out["codigo_ibge_imp"],
        )
    if "regiao_saude_imp" in out.columns:
        out["regiao_saude"] = out["regiao_saude"].fillna(out["regiao_saude_imp"])
    out["codigo_ibge"] = [_resolver_codigo_ibge(m, c) for m, c in zip(out["municipio"], out.get("codigo_ibge", pd.Series([pd.NA] * len(out))))]
    out = out.drop(columns=[c for c in out.columns if c.endswith("_imp")], errors="ignore")

    for col in INDICADORES_MDS:
        if col not in out.columns:
            out[col] = pd.NA

    pop = _carregar_populacao_base()
    if not pop.empty:
        out = out.drop(columns=["populacao"], errors="ignore").merge(pop, on="municipio", how="left")
    elif "populacao" not in out.columns:
        out["populacao"] = pd.NA

    # Indicadores derivados.
    out["pct_populacao_cadunico"] = _round_num(_safe_div(out["cadunico_pessoas"], out["populacao"]) * 100, 2)
    out["pct_populacao_bolsa_familia"] = _round_num(_safe_div(out["bolsa_familia_pessoas"], out["populacao"]) * 100, 2)
    # Fallback quando não houver pessoas PBF: aproximação por famílias, sem substituir o campo oficial.
    sem_pessoas_pbf = out["pct_populacao_bolsa_familia"].isna()
    out.loc[sem_pessoas_pbf, "pct_populacao_bolsa_familia"] = _round_num(_safe_div(out.loc[sem_pessoas_pbf, "bolsa_familia_familias"], out.loc[sem_pessoas_pbf, "populacao"]) * 100, 2)
    out["pct_familias_pbf_sobre_cadunico"] = _round_num(_safe_div(out["bolsa_familia_familias"], out["cadunico_familias"]) * 100, 2)
    out["pct_familias_baixa_renda_sobre_cadunico"] = _round_num(_safe_div(out["cadunico_familias_baixa_renda"], out["cadunico_familias"]) * 100, 2)

    pobreza_extrema = pd.to_numeric(out["cadunico_familias_pobreza_extrema"], errors="coerce")
    pobreza = pd.to_numeric(out["cadunico_familias_pobreza"], errors="coerce")
    extrema = pd.to_numeric(out["cadunico_familias_extrema_pobreza"], errors="coerce")
    soma_pobreza_extrema = pobreza.fillna(0) + extrema.fillna(0)
    soma_pobreza_extrema = soma_pobreza_extrema.mask(pobreza.isna() & extrema.isna())
    pobreza_extrema = pobreza_extrema.fillna(soma_pobreza_extrema)
    out["cadunico_familias_pobreza_extrema"] = pobreza_extrema
    out["pct_familias_pobreza_extrema_sobre_cadunico"] = _round_num(_safe_div(pobreza_extrema, out["cadunico_familias"]) * 100, 2)
    out["valor_medio_bolsa_familia_por_familia"] = _round_num(_safe_div(out["bolsa_familia_valor_repassado"], out["bolsa_familia_familias"]), 2)

    comp_cad = _normalizar_0_100(out["pct_populacao_cadunico"])
    comp_pbf = _normalizar_0_100(out["pct_populacao_bolsa_familia"])
    comp_pobreza = _normalizar_0_100(out["pct_familias_pobreza_extrema_sobre_cadunico"])
    comp_valor = _normalizar_0_100(out["valor_medio_bolsa_familia_por_familia"])
    score = _score_ponderado_componentes(
        [(comp_cad, 0.30), (comp_pbf, 0.30), (comp_pobreza, 0.25), (comp_valor, 0.15)],
        out.index,
    )
    out["score_vulnerabilidade_mds"] = _round_num(score, 1)
    out["classificacao_vulnerabilidade_mds"] = out["score_vulnerabilidade_mds"].map(_classificar_score)
    out["ranking_vulnerabilidade_mds"] = out["score_vulnerabilidade_mds"].rank(method="min", ascending=False, na_option="bottom").astype("Int64")
    out["arquivos_origem"] = "; ".join(arquivos)
    out["data_importacao"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for col in ["codigo_ibge", "regiao_saude", "mes_referencia", "ano_referencia", "competencia"]:
        if col not in out.columns:
            out[col] = pd.NA
    return out[[c for c in COLUNAS_FINAIS if c in out.columns]].sort_values("ranking_vulnerabilidade_mds", na_position="last")


def _salvar_tabelas(consolidado: pd.DataFrame, diagnostico: pd.DataFrame) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        consolidado.to_sql(TABELA_MDS, conn, if_exists="replace", index=False)
        diagnostico.to_sql(TABELA_LOG, conn, if_exists="replace", index=False)


def validar_cobertura_campos_mds(df: pd.DataFrame) -> pd.DataFrame:
    """Resumo de completude dos principais campos importados.

    Ajuda a identificar rapidamente se falta baixar algum arquivo do VIS DATA, como
    Pessoas CadÚnico, sem impedir que os demais indicadores sejam usados.
    """
    campos = [
        ("codigo_ibge", "Código IBGE oficial"),
        ("cadunico_familias", "Famílias CadÚnico"),
        ("cadunico_pessoas", "Pessoas CadÚnico"),
        ("bolsa_familia_familias", "Famílias Bolsa Família"),
        ("bolsa_familia_pessoas", "Pessoas Bolsa Família"),
        ("bolsa_familia_valor_repassado", "Valor PBF"),
        ("bpc_total", "BPC total"),
        ("bpc_pcd", "BPC PCD"),
        ("bpc_idoso", "BPC idoso"),
        ("cadunico_familias_pobreza_extrema", "Famílias pobreza/extrema"),
        ("score_vulnerabilidade_mds", "Score MDS"),
    ]
    registros = []
    total = len(df)
    for campo, rotulo in campos:
        preenchidos = int(df[campo].notna().sum()) if campo in df.columns else 0
        registros.append({
            "campo": campo,
            "indicador": rotulo,
            "municipios_preenchidos": preenchidos,
            "municipios_esperados": total,
            "percentual_preenchimento": round((preenchidos / total * 100), 1) if total else 0,
            "situacao": "OK" if preenchidos == total else ("Parcial" if preenchidos > 0 else "Ausente"),
        })
    return pd.DataFrame(registros)


def importar_mds_visdata(arquivos: list[Any], competencia: str | None = None, salvar: bool = True) -> dict[str, Any]:
    if not arquivos:
        raise ValueError("Envie ao menos um CSV/XLSX do VIS DATA 3/MDS.")

    partes: list[pd.DataFrame] = []
    diagnosticos: list[dict[str, Any]] = []
    nomes: list[str] = []
    erros: list[str] = []
    for arquivo in arquivos:
        nome = getattr(arquivo, "name", None) or Path(str(arquivo)).name
        try:
            parte, meta = _preparar_base_arquivo(arquivo, competencia_manual=competencia)
            partes.append(parte)
            diagnosticos.append(meta)
            nomes.append(nome)
        except Exception as exc:
            erros.append(f"{nome}: {exc}")
            diagnosticos.append({"arquivo": nome, "situacao": "Erro", "erro": str(exc)[:600]})

    if not partes:
        raise ValueError("Nenhum arquivo MDS pôde ser importado. " + " | ".join(erros[:4]))

    bruto = pd.concat(partes, ignore_index=True, sort=False)
    consolidado = _finalizar_consolidado(bruto, nomes)
    diag = pd.DataFrame(diagnosticos)
    cobertura = validar_cobertura_campos_mds(consolidado)
    if salvar:
        _salvar_tabelas(consolidado, diag)

    return {
        "ok": True,
        "tabela": TABELA_MDS,
        "linhas": int(len(consolidado)),
        "municipios_com_algum_dado": int(consolidado[[c for c in INDICADORES_MDS if c in consolidado.columns]].notna().any(axis=1).sum()),
        "arquivos_processados": len(nomes),
        "erros": erros,
        "diagnostico": diag,
        "cobertura_campos": cobertura,
        "consolidado": consolidado,
    }



def listar_arquivos_mds_locais(pasta_base: str | Path | None = None) -> list[Path]:
    """Lista arquivos MDS organizados em arquivos/MDS/**.

    Aceita subpastas como BOLSA_FAMILIA, CADUNICO e BPC. Arquivos temporários do Excel
    e documentos de leitura são ignorados.
    """
    base = Path(pasta_base) if pasta_base else MDS_LOCAL_DIR
    if not base.exists():
        return []
    extensoes = {".csv", ".txt", ".xlsx", ".xls"}
    arquivos = []
    for caminho in base.rglob("*"):
        if caminho.is_file() and caminho.suffix.lower() in extensoes and not caminho.name.startswith("~$"):
            arquivos.append(caminho)
    return sorted(arquivos, key=lambda p: str(p).lower())


def diagnosticar_pasta_mds_local(pasta_base: str | Path | None = None) -> pd.DataFrame:
    registros: list[dict[str, Any]] = []
    for caminho in listar_arquivos_mds_locais(pasta_base):
        try:
            diag = diagnosticar_arquivo_mds(caminho)
            diag["caminho"] = str(caminho)
            registros.append(diag)
        except Exception as exc:
            registros.append({"arquivo": caminho.name, "caminho": str(caminho), "situacao": "Erro", "erro": str(exc)[:600]})
    return pd.DataFrame(registros)


def importar_mds_pasta_local(competencia: str | None = None, salvar: bool = True, pasta_base: str | Path | None = None) -> dict[str, Any]:
    arquivos = listar_arquivos_mds_locais(pasta_base)
    if not arquivos:
        base = Path(pasta_base) if pasta_base else MDS_LOCAL_DIR
        raise ValueError(f"Nenhum CSV/XLSX encontrado em {base}.")
    resultado = importar_mds_visdata(arquivos, competencia=competencia, salvar=salvar)
    resultado["pasta_local"] = str(Path(pasta_base) if pasta_base else MDS_LOCAL_DIR)
    return resultado

def carregar_mds_municipal() -> pd.DataFrame:
    return read_table(TABELA_MDS)


def carregar_mds_municipio(municipio: str) -> dict[str, Any]:
    df = carregar_mds_municipal()
    if df.empty or "municipio" not in df.columns:
        return {"ok": False, "mensagem": "Base MDS ainda não importada."}
    alvo = _normalizar_texto(municipio)
    row = df[df["municipio"].map(_normalizar_texto).eq(alvo)]
    if row.empty:
        return {"ok": False, "mensagem": "Município sem dados MDS consolidados."}
    return {"ok": True, "linha": row.iloc[0].to_dict(), "tabela": row}


def perfil_mds_municipio(municipio: str) -> dict[str, Any]:
    info = carregar_mds_municipio(municipio)
    if not info.get("ok"):
        return info
    r = info["linha"]
    resumo = pd.DataFrame([
        {"Indicador-chave": "Famílias inscritas no CadÚnico", "Valor": r.get("cadunico_familias")},
        {"Indicador-chave": "Pessoas inscritas no CadÚnico", "Valor": r.get("cadunico_pessoas")},
        {"Indicador-chave": "% da população no CadÚnico", "Valor": r.get("pct_populacao_cadunico")},
        {"Indicador-chave": "Famílias beneficiárias do Bolsa Família", "Valor": r.get("bolsa_familia_familias")},
        {"Indicador-chave": "Pessoas beneficiárias do Bolsa Família", "Valor": r.get("bolsa_familia_pessoas")},
        {"Indicador-chave": "% da população beneficiária do Bolsa Família", "Valor": r.get("pct_populacao_bolsa_familia")},
        {"Indicador-chave": "Valor repassado do Bolsa Família", "Valor": r.get("bolsa_familia_valor_repassado")},
        {"Indicador-chave": "Famílias em pobreza/extrema pobreza", "Valor": r.get("cadunico_familias_pobreza_extrema")},
        {"Indicador-chave": "Famílias de baixa renda", "Valor": r.get("cadunico_familias_baixa_renda")},
        {"Indicador-chave": "Beneficiários BPC", "Valor": r.get("bpc_total")},
        {"Indicador-chave": "BPC — PCD", "Valor": r.get("bpc_pcd")},
        {"Indicador-chave": "BPC — idoso", "Valor": r.get("bpc_idoso")},
        {"Indicador-chave": "BPC no CadÚnico", "Valor": r.get("bpc_cadunico_total")},
        {"Indicador-chave": "Famílias PBF com esgotamento inadequado", "Valor": (pd.to_numeric(pd.Series([r.get("pbf_esgoto_fossa_rudimentar")]), errors="coerce").iloc[0] if pd.notna(pd.to_numeric(pd.Series([r.get("pbf_esgoto_fossa_rudimentar")]), errors="coerce").iloc[0]) else 0) + (pd.to_numeric(pd.Series([r.get("pbf_esgoto_vala")]), errors="coerce").iloc[0] if pd.notna(pd.to_numeric(pd.Series([r.get("pbf_esgoto_vala")]), errors="coerce").iloc[0]) else 0) + (pd.to_numeric(pd.Series([r.get("pbf_esgoto_rio_lago_mar")]), errors="coerce").iloc[0] if pd.notna(pd.to_numeric(pd.Series([r.get("pbf_esgoto_rio_lago_mar")]), errors="coerce").iloc[0]) else 0)},
        {"Indicador-chave": "Famílias em situação de rua no CadÚnico", "Valor": r.get("cadunico_rua_familias_baixa_renda")},
        {"Indicador-chave": "Score de vulnerabilidade MDS", "Valor": r.get("score_vulnerabilidade_mds")},
        {"Indicador-chave": "Classificação preliminar", "Valor": r.get("classificacao_vulnerabilidade_mds")},
        {"Indicador-chave": "Ranking estadual", "Valor": r.get("ranking_vulnerabilidade_mds")},
    ])
    tecnica = pd.DataFrame([r])
    alertas = []
    if pd.isna(pd.to_numeric(pd.Series([r.get("populacao")]), errors="coerce")).iloc[0]:
        alertas.append({"Alerta": "População ausente", "Leitura": "Percentuais populacionais não puderam ser calculados para este município."})
    if pd.isna(pd.to_numeric(pd.Series([r.get("bolsa_familia_pessoas")]), errors="coerce")).iloc[0]:
        alertas.append({"Alerta": "Pessoas PBF ausente", "Leitura": "O percentual do Bolsa Família pode ter usado aproximação por famílias ou ficar vazio."})
    return {"ok": True, "resumo": resumo, "tecnica": tecnica, "alertas": pd.DataFrame(alertas), "linha": r}


def resumo_validacao_mds() -> dict[str, Any]:
    df = carregar_mds_municipal()
    if df.empty:
        return {"ok": False, "mensagem": "Base MDS ainda não importada."}
    ind_cols = [c for c in INDICADORES_MDS if c in df.columns]
    resumo = pd.DataFrame([
        {"Indicador": "Municípios na base", "Valor": df["municipio"].nunique() if "municipio" in df.columns else len(df)},
        {"Indicador": "Municípios com algum dado MDS", "Valor": df[ind_cols].notna().any(axis=1).sum() if ind_cols else 0},
        {"Indicador": "Competência predominante", "Valor": df["competencia"].mode().iloc[0] if "competencia" in df.columns and not df["competencia"].dropna().empty else "-"},
        {"Indicador": "População estadual usada", "Valor": pd.to_numeric(df.get("populacao", pd.Series(dtype=float)), errors="coerce").sum()},
        {"Indicador": "Pessoas CadÚnico", "Valor": pd.to_numeric(df.get("cadunico_pessoas", pd.Series(dtype=float)), errors="coerce").sum()},
        {"Indicador": "Famílias PBF", "Valor": pd.to_numeric(df.get("bolsa_familia_familias", pd.Series(dtype=float)), errors="coerce").sum()},
        {"Indicador": "Valor PBF", "Valor": pd.to_numeric(df.get("bolsa_familia_valor_repassado", pd.Series(dtype=float)), errors="coerce").sum()},
        {"Indicador": "Beneficiários BPC", "Valor": pd.to_numeric(df.get("bpc_total", pd.Series(dtype=float)), errors="coerce").sum()},
        {"Indicador": "BPC PCD", "Valor": pd.to_numeric(df.get("bpc_pcd", pd.Series(dtype=float)), errors="coerce").sum()},
        {"Indicador": "BPC idoso", "Valor": pd.to_numeric(df.get("bpc_idoso", pd.Series(dtype=float)), errors="coerce").sum()},
    ])
    ranking = df.sort_values("score_vulnerabilidade_mds", ascending=False, na_position="last").head(50).copy()
    return {"ok": True, "resumo": resumo, "ranking": ranking}
