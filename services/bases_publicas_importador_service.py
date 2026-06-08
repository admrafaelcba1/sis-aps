
from __future__ import annotations

from pathlib import Path
from datetime import datetime
from typing import Any
import re
import unicodedata

import pandas as pd

from database.connection import get_connection
from database.queries import read_table
from config.municipios_mt import DEFAULT_MUNICIPIOS


def _normalizar_texto(valor: Any) -> str:
    texto = "" if valor is None else str(valor)
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-zA-Z0-9]+", " ", texto).strip().lower()
    return re.sub(r"\s+", " ", texto)


MUNICIPIOS = pd.DataFrame(DEFAULT_MUNICIPIOS)
MUNICIPIOS["municipio_norm"] = MUNICIPIOS["municipio"].map(_normalizar_texto)
MUNICIPIOS_LOOKUP = dict(zip(MUNICIPIOS["municipio_norm"], MUNICIPIOS["municipio"]))
REGIAO_LOOKUP = dict(zip(MUNICIPIOS["municipio_norm"], MUNICIPIOS.get("regiao_saude", "")))


def _ler_arquivo(caminho: str | Path) -> pd.DataFrame:
    caminho = Path(caminho)
    suf = caminho.suffix.lower()
    if suf in [".xlsx", ".xls"]:
        # Lê a primeira aba. A UI pode evoluir para escolha de aba depois.
        return pd.read_excel(caminho, dtype=str)
    # tenta csv com diferentes separadores/encodings
    tentativas = [
        {"sep": None, "engine": "python", "encoding": "utf-8-sig"},
        {"sep": ";", "encoding": "utf-8-sig"},
        {"sep": ",", "encoding": "utf-8-sig"},
        {"sep": ";", "encoding": "latin1"},
        {"sep": ",", "encoding": "latin1"},
    ]
    ultimo = None
    for kw in tentativas:
        try:
            return pd.read_csv(caminho, dtype=str, **kw)
        except Exception as e:
            ultimo = e
    raise ultimo or ValueError("Não foi possível ler o arquivo.")


def _sanitizar_colunas(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    novas = []
    usados = {}
    for c in out.columns:
        nome = _normalizar_texto(c).replace(" ", "_")
        if not nome:
            nome = "coluna"
        if nome in usados:
            usados[nome] += 1
            nome = f"{nome}_{usados[nome]}"
        else:
            usados[nome] = 1
        novas.append(nome)
    out.columns = novas
    return out


def _detectar_coluna(df: pd.DataFrame, candidatos: list[str]) -> str | None:
    cols_norm = {_normalizar_texto(c): c for c in df.columns}
    for cand in candidatos:
        cn = _normalizar_texto(cand)
        if cn in cols_norm:
            return cols_norm[cn]
    # busca por contenção
    for c in df.columns:
        cn = _normalizar_texto(c)
        for cand in candidatos:
            if _normalizar_texto(cand) in cn:
                return c
    return None


def _padronizar_municipio(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    mun_col = _detectar_coluna(out, ["municipio", "município", "nome_municipio", "nm_mun", "nm_municipio", "localidade", "nome"])
    cod_col = _detectar_coluna(out, ["codigo_ibge", "cod_ibge", "cod_mun", "cd_mun", "id_municipio", "codigo_municipio", "ibge"])

    if mun_col:
        out["municipio_origem"] = out[mun_col].astype(str).str.strip()
    else:
        out["municipio_origem"] = ""

    out["municipio_norm"] = out["municipio_origem"].map(_normalizar_texto)
    out["municipio"] = out["municipio_norm"].map(MUNICIPIOS_LOOKUP).fillna(out["municipio_origem"])
    out["regiao_saude"] = out["municipio_norm"].map(REGIAO_LOOKUP).fillna("")

    if cod_col:
        out["codigo_ibge"] = out[cod_col].astype(str).str.replace(r"\D", "", regex=True).str[:7]
    elif "codigo_ibge" not in out.columns:
        out["codigo_ibge"] = ""

    out["municipio_mt_encontrado"] = out["municipio_norm"].isin(set(MUNICIPIOS_LOOKUP.keys()))
    return out


def _converter_numericos(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    ignorar = {
        "municipio", "municipio_origem", "municipio_norm", "regiao_saude", "codigo_ibge",
        "fonte", "eixo", "tipo_base", "arquivo_origem", "data_importacao", "tabela_destino",
        "observacao", "status", "ano_referencia", "municipio_mt_encontrado"
    }
    for c in out.columns:
        if c in ignorar:
            continue
        s = out[c].astype(str).str.strip()
        # evita converter códigos longos como setor censitário, CNES etc.
        digitos = s.str.replace(r"\D", "", regex=True)
        if digitos.str.len().median() >= 7 and not s.str.contains(r"[,.]", regex=True, na=False).any():
            continue
        conv = pd.to_numeric(s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False), errors="coerce")
        if conv.notna().sum() >= max(1, int(len(out) * 0.25)):
            out[c] = conv
    return out


def sugerir_tabela_destino(eixo: str, tipo_base: str, arquivo_nome: str = "") -> str:
    blob = _normalizar_texto(f"{eixo} {tipo_base} {arquivo_nome}")
    if "ibge" in blob and ("setor" in blob or "censo" in blob):
        return "base_publica_ibge_censo_setores"
    if "ibge" in blob and ("renda" in blob or "trabalho" in blob):
        return "base_publica_ibge_renda_trabalho"
    if "inep" in blob or "ideb" in blob or "inse" in blob or "distorcao" in blob:
        return "base_publica_inep_indicadores"
    if "sinan" in blob or "agravo" in blob or "dengue" in blob or "tuberculose" in blob or "hanseniase" in blob:
        return "base_publica_datasus_sinan"
    if "sim" in blob or "mortalidade" in blob or "obito" in blob or "obitos" in blob:
        return "base_publica_datasus_sim"
    if "sinasc" in blob or "nascido" in blob or "nascidos" in blob:
        return "base_publica_datasus_sinasc"
    if "sih" in blob or "sia" in blob or "internacao" in blob or "procedimento" in blob:
        return "base_publica_datasus_sih_sia"
    if "atlas" in blob or "idhm" in blob:
        return "base_publica_atlas_idhm"
    return "base_publica_indicadores_gerais"


def preparar_base_publica(
    caminho: str | Path,
    eixo: str,
    tipo_base: str,
    fonte: str = "",
    ano_referencia: str = "",
    tabela_destino: str | None = None,
) -> dict:
    bruto = _ler_arquivo(caminho)
    bruto_original_cols = list(bruto.columns)
    df = _sanitizar_colunas(bruto)
    df = _padronizar_municipio(df)
    df = _converter_numericos(df)

    arquivo_nome = Path(caminho).name
    tabela = tabela_destino or sugerir_tabela_destino(eixo, tipo_base, arquivo_nome)

    df["eixo"] = eixo
    df["tipo_base"] = tipo_base
    df["fonte"] = fonte
    df["ano_referencia"] = ano_referencia
    df["arquivo_origem"] = arquivo_nome
    df["tabela_destino"] = tabela
    df["data_importacao"] = datetime.now().isoformat(timespec="seconds")

    municipios_encontrados = int(df["municipio_mt_encontrado"].astype(bool).sum()) if "municipio_mt_encontrado" in df.columns else 0
    municipios_distintos = int(df.loc[df["municipio_mt_encontrado"].astype(bool), "municipio"].nunique()) if municipios_encontrados else 0
    cobertura = round(municipios_distintos / 142 * 100, 1) if municipios_distintos else 0.0

    diagnostico = {
        "linhas": int(len(df)),
        "colunas": int(len(df.columns)),
        "municipios_encontrados_linhas": municipios_encontrados,
        "municipios_distintos_mt": municipios_distintos,
        "cobertura_municipal_percentual": cobertura,
        "tabela_destino": tabela,
        "colunas_originais": bruto_original_cols,
        "colunas_padronizadas": list(df.columns),
        "preview": df.head(20),
        "df": df,
    }
    return diagnostico


def salvar_base_publica_preparada(df: pd.DataFrame, tabela_destino: str) -> dict:
    if df is None or df.empty:
        return {"ok": False, "mensagem": "Base vazia.", "linhas": 0}
    tabela = re.sub(r"[^a-zA-Z0-9_]+", "_", str(tabela_destino).strip().lower()).strip("_")
    if not tabela:
        tabela = "base_publica_importada"
    with get_connection() as con:
        df.to_sql(tabela, con, if_exists="replace", index=False)
    return {"ok": True, "tabela": tabela, "linhas": int(len(df)), "colunas": int(len(df.columns))}


def importar_base_publica_universal(
    caminho: str | Path,
    eixo: str,
    tipo_base: str,
    fonte: str = "",
    ano_referencia: str = "",
    tabela_destino: str | None = None,
) -> dict:
    prep = preparar_base_publica(caminho, eixo, tipo_base, fonte, ano_referencia, tabela_destino)
    info = salvar_base_publica_preparada(prep["df"], prep["tabela_destino"])
    prep.update(info)
    return prep


def listar_bases_publicas_salvas() -> pd.DataFrame:
    """Lista tabelas importadas por este módulo."""
    tabelas = []
    with get_connection() as con:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        for r in rows:
            nome = r[0]
            if nome.startswith("base_publica_"):
                try:
                    qtd = con.execute(f"SELECT COUNT(*) FROM {nome}").fetchone()[0]
                except Exception:
                    qtd = None
                tabelas.append({"tabela": nome, "linhas": qtd})
    return pd.DataFrame(tabelas)


def consolidar_resumo_bases_publicas() -> pd.DataFrame:
    tabelas = listar_bases_publicas_salvas()
    if tabelas.empty:
        return tabelas
    registros = []
    for tabela in tabelas["tabela"].tolist():
        df = read_table(tabela)
        if df.empty:
            continue
        registros.append({
            "tabela": tabela,
            "linhas": len(df),
            "colunas": len(df.columns),
            "municipios_distintos_mt": int(df["municipio"].nunique()) if "municipio" in df.columns else 0,
            "eixo": df["eixo"].iloc[0] if "eixo" in df.columns and len(df) else "",
            "tipo_base": df["tipo_base"].iloc[0] if "tipo_base" in df.columns and len(df) else "",
            "ano_referencia": df["ano_referencia"].iloc[0] if "ano_referencia" in df.columns and len(df) else "",
            "fonte": df["fonte"].iloc[0] if "fonte" in df.columns and len(df) else "",
        })
    return pd.DataFrame(registros)
