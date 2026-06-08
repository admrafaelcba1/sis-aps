
from __future__ import annotations

from pathlib import Path
import re
import sqlite3
import unicodedata
from datetime import datetime
from typing import Any

import pandas as pd

from database.connection import get_connection
from database.queries import read_table
from config.municipios_mt import DEFAULT_MUNICIPIOS

IMPORT_DIR = Path("data/imports/socioeducacional")
IMPORT_DIR.mkdir(parents=True, exist_ok=True)


def _normalizar_texto(valor: Any) -> str:
    texto = "" if valor is None else str(valor)
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-zA-Z0-9]+", " ", texto).strip().lower()
    return re.sub(r"\s+", " ", texto)


MUNICIPIOS_MT_DF = pd.DataFrame(DEFAULT_MUNICIPIOS)
if "nome" in MUNICIPIOS_MT_DF.columns and "municipio" not in MUNICIPIOS_MT_DF.columns:
    MUNICIPIOS_MT_DF = MUNICIPIOS_MT_DF.rename(columns={"nome": "municipio"})
if "codigo_ibge" not in MUNICIPIOS_MT_DF.columns:
    for cand in ["codigo", "id", "cod_ibge"]:
        if cand in MUNICIPIOS_MT_DF.columns:
            MUNICIPIOS_MT_DF["codigo_ibge"] = MUNICIPIOS_MT_DF[cand]
            break
MUNICIPIOS_MT_DF["municipio_norm"] = MUNICIPIOS_MT_DF.get("municipio", pd.Series(dtype=str)).astype(str).map(_normalizar_texto)
MUNICIPIOS_MT_DF["codigo_ibge"] = MUNICIPIOS_MT_DF.get("codigo_ibge", pd.Series(dtype=str)).astype(str).str.replace(r"\D", "", regex=True).str[:7]

MAPA_MUN = dict(zip(MUNICIPIOS_MT_DF["municipio_norm"], MUNICIPIOS_MT_DF["municipio"]))
MAPA_COD = dict(zip(MUNICIPIOS_MT_DF["codigo_ibge"], MUNICIPIOS_MT_DF["municipio"]))


def _ler_planilha(caminho: str | Path) -> pd.DataFrame:
    caminho = Path(caminho)
    if caminho.suffix.lower() in [".xlsx", ".xls"]:
        return pd.read_excel(caminho, dtype=str)
    try:
        return pd.read_csv(caminho, dtype=str, encoding="utf-8-sig")
    except Exception:
        try:
            return pd.read_csv(caminho, dtype=str, sep=";", encoding="utf-8-sig")
        except Exception:
            return pd.read_csv(caminho, dtype=str, sep=",", encoding="latin1")


def _converter_numericos(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    ignorar = {
        "municipio", "municipio_norm", "codigo_ibge", "setor_censitario", "fonte",
        "ano_referencia", "data_importacao", "tipo_base", "observacao", "status_validacao"
    }
    for c in out.columns:
        if c in ignorar:
            continue
        serie = out[c].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
        conv = pd.to_numeric(serie, errors="coerce")
        # Só converte quando há pelo menos algum número válido e não destrói códigos/nomes.
        if conv.notna().sum() > 0 and conv.notna().sum() >= max(1, int(len(out) * 0.30)):
            out[c] = conv
    return out


def _padronizar_municipios(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    # detectar município e código
    mun_col = None
    for cand in ["municipio", "Município", "nome_municipio", "NM_MUN", "NM_MUNICIP", "localidade"]:
        if cand in out.columns:
            mun_col = cand
            break
    cod_col = None
    for cand in ["codigo_ibge", "cod_ibge", "CD_MUN", "CD_MUNICIP", "codigo_municipio", "id_municipio"]:
        if cand in out.columns:
            cod_col = cand
            break

    if mun_col:
        out["municipio"] = out[mun_col].astype(str).str.strip()
    elif cod_col:
        cod = out[cod_col].astype(str).str.replace(r"\D", "", regex=True).str[:7]
        out["municipio"] = cod.map(MAPA_COD).fillna("")
    else:
        out["municipio"] = ""

    if cod_col:
        out["codigo_ibge"] = out[cod_col].astype(str).str.replace(r"\D", "", regex=True).str[:7]
    else:
        norm = out["municipio"].map(_normalizar_texto)
        inv = {v: k for k, v in MAPA_COD.items()}
        out["codigo_ibge"] = norm.map({k: v for k, v in zip(MUNICIPIOS_MT_DF["municipio_norm"], MUNICIPIOS_MT_DF["codigo_ibge"])}).fillna("")

    out["municipio_norm"] = out["municipio"].map(_normalizar_texto)
    # Corrige grafia pelo cadastro MT quando possível
    out["municipio"] = out["municipio_norm"].map(MAPA_MUN).fillna(out["municipio"])
    out["municipio_mt_encontrado"] = out["municipio_norm"].isin(set(MAPA_MUN.keys())) | out["codigo_ibge"].isin(set(MAPA_COD.keys()))
    return out


def preparar_base_socioeducacional(df: pd.DataFrame, tipo_base: str, fonte: str, ano_referencia: str | int | None = None) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    out = _padronizar_municipios(out)
    out["tipo_base"] = tipo_base
    out["fonte"] = fonte
    out["ano_referencia"] = "" if ano_referencia is None else str(ano_referencia)
    out["data_importacao"] = datetime.now().isoformat(timespec="seconds")
    out = _converter_numericos(out)
    return out


def salvar_tabela(df: pd.DataFrame, tabela: str) -> dict:
    if df is None or df.empty:
        return {"ok": False, "linhas": 0, "mensagem": "Base vazia."}
    with get_connection() as con:
        df.to_sql(tabela, con, if_exists="replace", index=False)
    return {
        "ok": True,
        "linhas": int(len(df)),
        "municipios_mt": int(df.get("municipio_mt_encontrado", pd.Series(dtype=bool)).astype(bool).sum()) if "municipio_mt_encontrado" in df.columns else 0,
        "tabela": tabela,
    }


def importar_arquivo_socioeducacional(caminho: str | Path, tipo_base: str, fonte: str, ano_referencia: str | int | None = None) -> dict:
    bruto = _ler_planilha(caminho)
    prep = preparar_base_socioeducacional(bruto, tipo_base, fonte, ano_referencia)
    tabela = {
        "IBGE Setores 2022": "socio_ibge_setores_2022",
        "INEP Municipal": "socio_inep_municipal",
        "Atlas/IDHM Municipal": "socio_atlas_municipal",
        "Indicadores municipais gerais": "socio_indicadores_municipais",
    }.get(tipo_base, "socio_base_importada")
    info = salvar_tabela(prep, tabela)
    info["colunas"] = list(prep.columns)
    info["preview"] = prep.head(10)
    return info


def carregar_socioeducacional_consolidado() -> dict:
    tabelas = {
        "IBGE Setores 2022": "socio_ibge_setores_2022",
        "INEP Municipal": "socio_inep_municipal",
        "Atlas/IDHM Municipal": "socio_atlas_municipal",
        "Indicadores municipais gerais": "socio_indicadores_municipais",
    }
    bases = {}
    resumo = []
    for nome, tabela in tabelas.items():
        df = read_table(tabela)
        bases[nome] = df
        resumo.append({
            "base": nome,
            "tabela": tabela,
            "linhas": len(df),
            "municipios_identificados": int(df["municipio"].nunique()) if not df.empty and "municipio" in df.columns else 0,
            "colunas": len(df.columns) if not df.empty else 0,
            "status": "Carregada" if not df.empty else "Não carregada",
        })
    return {"bases": bases, "resumo": pd.DataFrame(resumo)}


def consolidar_indicadores_municipais() -> pd.DataFrame:
    """Cria uma visão municipal resumida a partir das bases socioeducacionais importadas."""
    dados = carregar_socioeducacional_consolidado()["bases"]
    base_mun = MUNICIPIOS_MT_DF[["municipio", "codigo_ibge"]].drop_duplicates().copy()

    # Agrega bases de setores para município, pegando médias/somas conforme coluna.
    ibge = dados.get("IBGE Setores 2022", pd.DataFrame())
    if not ibge.empty and "municipio" in ibge.columns:
        num_cols = [c for c in ibge.select_dtypes(include="number").columns if c not in ["codigo_ibge"]]
        if num_cols:
            agg = ibge.groupby("municipio", dropna=False)[num_cols].mean(numeric_only=True).reset_index()
            agg = agg.add_prefix("ibge_setores_media_")
            agg = agg.rename(columns={"ibge_setores_media_municipio": "municipio"})
            base_mun = base_mun.merge(agg, on="municipio", how="left")

    for nome_base, prefixo in [("INEP Municipal", "inep"), ("Atlas/IDHM Municipal", "atlas"), ("Indicadores municipais gerais", "geral")]:
        df = dados.get(nome_base, pd.DataFrame())
        if df.empty or "municipio" not in df.columns:
            continue
        num_cols = [c for c in df.select_dtypes(include="number").columns if c not in ["codigo_ibge"]]
        if not num_cols:
            continue
        agg = df.groupby("municipio", dropna=False)[num_cols].mean(numeric_only=True).reset_index()
        rename = {c: f"{prefixo}_{c}" for c in agg.columns if c != "municipio"}
        agg = agg.rename(columns=rename)
        base_mun = base_mun.merge(agg, on="municipio", how="left")

    return base_mun



def existem_bases_socioeducacionais_importadas() -> dict:
    """Verifica se há base socioeducacional real carregada antes de consolidar."""
    info = carregar_socioeducacional_consolidado()
    resumo = info.get("resumo", pd.DataFrame())
    if resumo is None or resumo.empty:
        return {"ok": False, "bases_carregadas": 0, "linhas": 0, "mensagem": "Nenhuma base socioeducacional foi carregada."}
    carregadas = resumo[pd.to_numeric(resumo.get("linhas", 0), errors="coerce").fillna(0).gt(0)].copy()
    linhas = int(pd.to_numeric(carregadas.get("linhas", pd.Series(dtype=int)), errors="coerce").fillna(0).sum()) if not carregadas.empty else 0
    return {
        "ok": not carregadas.empty and linhas > 0,
        "bases_carregadas": int(len(carregadas)),
        "linhas": linhas,
        "mensagem": "Há bases socioeducacionais carregadas." if not carregadas.empty and linhas > 0 else "Nenhuma base socioeducacional real foi importada. O consolidado não será gerado para evitar tabela vazia."
    }


def salvar_consolidado_municipal() -> dict:
    disponibilidade = existem_bases_socioeducacionais_importadas()
    if not disponibilidade.get("ok"):
        return {
            "ok": False,
            "linhas": 0,
            "colunas": 0,
            "tabela": "socio_consolidado_municipal",
            "mensagem": disponibilidade.get("mensagem", "Importe uma base socioeducacional antes de consolidar."),
        }

    df = consolidar_indicadores_municipais()
    if df.empty:
        return {"ok": False, "linhas": 0, "colunas": 0, "mensagem": "Consolidado vazio."}

    # Evita salvar uma tabela apenas estrutural, com município/código e nenhum indicador.
    colunas_indicadores = [c for c in df.columns if c not in ["municipio", "codigo_ibge"]]
    if len(colunas_indicadores) == 0:
        return {
            "ok": False,
            "linhas": int(len(df)),
            "colunas": int(len(df.columns)),
            "mensagem": "Consolidado não gerado: há apenas colunas estruturais, sem indicadores socioeducacionais importados.",
        }

    with get_connection() as con:
        df.to_sql("socio_consolidado_municipal", con, if_exists="replace", index=False)
    return {"ok": True, "linhas": len(df), "colunas": len(df.columns), "tabela": "socio_consolidado_municipal", "mensagem": "Consolidado municipal socioeducacional gerado com indicadores importados."}


def gerar_modelos_socioeducacionais() -> dict[str, pd.DataFrame]:
    ibge = pd.DataFrame([{
        "setor_censitario": "510340310000001",
        "codigo_ibge": "5103403",
        "municipio": "Cuiabá",
        "populacao_total": 0,
        "responsaveis_renda_ate_1_2_sm_pct": "",
        "renda_media_responsavel": "",
        "taxa_nao_alfabetizados_pct": "",
        "domicilios_sem_esgotamento_pct": "",
        "domicilios_sem_abastecimento_agua_pct": "",
        "observacao": "Modelo para dados do IBGE por setor censitário",
    }])
    inep = pd.DataFrame([{
        "codigo_ibge": "5103403",
        "municipio": "Cuiabá",
        "ano_referencia": "2024",
        "ideb_anos_iniciais": "",
        "ideb_anos_finais": "",
        "inse_medio": "",
        "distorcao_idade_serie_pct": "",
        "taxa_aprovacao_pct": "",
        "observacao": "Modelo para indicadores municipais do INEP",
    }])
    atlas = pd.DataFrame([{
        "codigo_ibge": "5103403",
        "municipio": "Cuiabá",
        "ano_referencia": "",
        "idhm": "",
        "idhm_educacao": "",
        "idhm_renda": "",
        "idhm_longevidade": "",
        "vulnerabilidade_pct": "",
        "observacao": "Modelo para indicadores municipais Atlas/IDHM",
    }])
    return {
        "modelo_ibge_setores_2022.csv": ibge,
        "modelo_inep_indicadores_municipais.csv": inep,
        "modelo_atlas_indicadores_municipais.csv": atlas,
    }


def _classificar_por_percentil(valor: float, serie: pd.Series, invertido: bool = False) -> str:
    try:
        v = float(valor)
    except Exception:
        return "Sem dado"
    s = pd.to_numeric(serie, errors="coerce").dropna()
    if s.empty:
        return "Sem dado"
    q75 = s.quantile(0.75)
    q50 = s.quantile(0.50)
    q25 = s.quantile(0.25)
    if invertido:
        if v <= q25:
            return "Alto alerta"
        if v <= q50:
            return "Atenção"
        if v <= q75:
            return "Moderado"
        return "Melhor condição relativa"
    if v >= q75:
        return "Alto alerta"
    if v >= q50:
        return "Atenção"
    if v >= q25:
        return "Moderado"
    return "Menor alerta relativo"


def carregar_visao_socio_municipal() -> pd.DataFrame:
    """Retorna visão municipal dos indicadores socioeducacionais importados."""
    cons = read_table("socio_consolidado_municipal")
    if cons.empty:
        cons = consolidar_indicadores_municipais()
    if cons.empty:
        return pd.DataFrame()

    out = cons.copy()
    # Detecta colunas por palavras-chave para facilitar leitura, mesmo que a planilha venha com nomes variados.
    cols = list(out.columns)
    def _achar(*termos):
        termos_norm = [_normalizar_texto(t) for t in termos]
        candidatos = []
        for c in cols:
            cn = _normalizar_texto(c)
            if all(t in cn for t in termos_norm):
                candidatos.append(c)
        return candidatos[0] if candidatos else None

    mapa = {
        "renda_baixa": _achar("renda", "1", "2") or _achar("baixa", "renda") or _achar("vulnerabilidade", "renda"),
        "renda_media": _achar("renda", "media") or _achar("renda", "responsavel"),
        "nao_alfabetizados": _achar("nao", "alfabet") or _achar("analfabet"),
        "saneamento": _achar("saneamento") or _achar("esgotamento") or _achar("abastecimento"),
        "inse": _achar("inse"),
        "ideb_iniciais": _achar("ideb", "iniciais"),
        "ideb_finais": _achar("ideb", "finais"),
        "distorcao": _achar("distorcao"),
        "idhm": _achar("idhm"),
        "idhm_educacao": _achar("idhm", "educacao"),
        "idhm_renda": _achar("idhm", "renda"),
    }

    vis = out[["municipio", "codigo_ibge"]].copy() if "codigo_ibge" in out.columns else out[["municipio"]].copy()
    for nome, col in mapa.items():
        if col and col in out.columns:
            vis[nome] = pd.to_numeric(out[col], errors="coerce")
        else:
            vis[nome] = pd.NA

    # Índice preliminar: usa dados disponíveis, sem substituir score oficial.
    componentes = []
    for col in ["renda_baixa", "nao_alfabetizados", "saneamento", "distorcao"]:
        if col in vis.columns and pd.to_numeric(vis[col], errors="coerce").notna().sum() > 0:
            s = pd.to_numeric(vis[col], errors="coerce")
            mn, mx = s.min(), s.max()
            if mx > mn:
                componentes.append(((s - mn) / (mx - mn) * 100).fillna(0))
    for col in ["ideb_iniciais", "ideb_finais", "inse", "idhm", "idhm_educacao", "idhm_renda"]:
        if col in vis.columns and pd.to_numeric(vis[col], errors="coerce").notna().sum() > 0:
            s = pd.to_numeric(vis[col], errors="coerce")
            mn, mx = s.min(), s.max()
            if mx > mn:
                componentes.append(((mx - s) / (mx - mn) * 100).fillna(0))

    if componentes:
        matriz = pd.concat(componentes, axis=1)
        vis["indice_socioeducacional_preliminar"] = matriz.mean(axis=1).round(1)
    else:
        vis["indice_socioeducacional_preliminar"] = pd.NA

    vis["classe_socioeducacional"] = vis["indice_socioeducacional_preliminar"].apply(
        lambda v: "Alto alerta" if pd.notna(v) and v >= 75 else
                  "Atenção" if pd.notna(v) and v >= 60 else
                  "Moderado" if pd.notna(v) and v >= 40 else
                  "Menor alerta relativo" if pd.notna(v) else "Sem dado"
    )

    vis["leitura_socioeducacional"] = vis.apply(_gerar_leitura_socio, axis=1)
    return vis


def _gerar_leitura_socio(row: pd.Series) -> str:
    if pd.isna(row.get("indice_socioeducacional_preliminar")):
        return "Sem base socioeducacional consolidada para leitura automática."
    alertas = []
    if pd.notna(row.get("renda_baixa")):
        alertas.append(f"renda/vulnerabilidade: {row.get('renda_baixa'):.1f}")
    if pd.notna(row.get("nao_alfabetizados")):
        alertas.append(f"alfabetização/analfabetismo: {row.get('nao_alfabetizados'):.1f}")
    if pd.notna(row.get("saneamento")):
        alertas.append(f"saneamento/domicílios: {row.get('saneamento'):.1f}")
    if pd.notna(row.get("distorcao")):
        alertas.append(f"distorção educacional: {row.get('distorcao'):.1f}")
    base = "; ".join(alertas[:4]) if alertas else "indicadores importados disponíveis"
    return f"Classe {row.get('classe_socioeducacional')}: leitura preliminar baseada em {base}. Não substitui o score oficial nem validação técnica."


def carregar_socio_municipio(municipio: str) -> dict:
    vis = carregar_visao_socio_municipal()
    if vis.empty:
        return {"ok": False, "dados": pd.DataFrame(), "leitura": "Nenhuma base socioeducacional consolidada foi carregada."}
    alvo = _normalizar_texto(municipio)
    achado = vis[vis["municipio"].map(_normalizar_texto).eq(alvo)].copy()
    if achado.empty:
        return {"ok": False, "dados": pd.DataFrame(), "leitura": "Município não encontrado no consolidado socioeducacional."}
    row = achado.iloc[0]
    itens = []
    rotulos = {
        "renda_baixa": "Renda/vulnerabilidade econômica",
        "renda_media": "Renda média/responsável",
        "nao_alfabetizados": "Alfabetização/analfabetismo",
        "saneamento": "Saneamento/domicílios",
        "inse": "INSE",
        "ideb_iniciais": "IDEB anos iniciais",
        "ideb_finais": "IDEB anos finais",
        "distorcao": "Distorção idade-série",
        "idhm": "IDHM",
        "idhm_educacao": "IDHM Educação",
        "idhm_renda": "IDHM Renda",
    }
    for col, rot in rotulos.items():
        val = row.get(col)
        if pd.notna(val):
            itens.append({"indicador": rot, "valor": round(float(val), 2), "origem": "Base socioeducacional importada"})
    dados = pd.DataFrame(itens)
    return {
        "ok": True,
        "dados": dados,
        "classe": row.get("classe_socioeducacional"),
        "indice": row.get("indice_socioeducacional_preliminar"),
        "leitura": row.get("leitura_socioeducacional"),
    }
