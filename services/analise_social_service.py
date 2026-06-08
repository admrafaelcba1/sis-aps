from __future__ import annotations

import sqlite3
import unicodedata
from pathlib import Path
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from database.connection import get_connection


SOCIAL_COLUMNS = [
    "indice_vulnerabilidade",
    "perfil_urbano_rural",
    "indicador_demografico",
    "taxa_alfabetizacao",
    "nivel_instrucao",
    "renda_censo_2022",
    "saneamento_censo_2022",
    "populacao_indigena",
    "populacao_quilombola",
    "pib_municipal_precos_correntes",
    "pib_per_capita",
    "agua_indicador",
    "esgoto_indicador",
    "lixo_indicador",
    "taxa_alfabetizacao_pct",
    "taxa_analfabetismo_estimada",
    "taxa_analfabetismo_estimado_pct",
    "educacao_indicador",
    "abastecimento_agua_rede_pct",
    "esgotamento_rede_pct",
    "esgotamento_adequado_rede_ou_fossa_pct",
    "lixo_coletado_pct",
    "nivel_instrucao_baixo_pct",
    "nivel_instrucao_medio_ou_superior_pct",
    "nivel_instrucao_superior_completo_pct",
]


def _norm_texto(valor: object) -> str:
    texto = str(valor or "").strip().upper()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    return " ".join(texto.split())


def _safe_read_sql(sql: str, params: tuple | None = None) -> pd.DataFrame:
    try:
        conn = get_connection()
        return pd.read_sql_query(sql, conn, params=params or ())
    except Exception:
        return pd.DataFrame()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _table_exists(table: str) -> bool:
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return bool(row)
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass



REFERENCE_DETERMINANTES_PATHS = [
    Path("data/reference/determinantes_sociais_sistema_antigo.csv"),
    Path(__file__).resolve().parents[1] / "data" / "reference" / "determinantes_sociais_sistema_antigo.csv",
]


def _read_determinantes_referencia_antiga() -> pd.DataFrame:
    """Lê determinantes sociais recuperados da versão antiga do sistema.

    Esta fonte é usada como fallback rastreável quando a consulta SIDRA atual
    não separa água/esgoto/lixo na base nova. Não cria dados; reaproveita o
    cache antigo que já trazia colunas de saneamento, educação e renda com
    referência IBGE/Censo 2022.
    """
    for caminho in REFERENCE_DETERMINANTES_PATHS:
        try:
            if caminho.exists():
                df = pd.read_csv(caminho)
                if "municipio" in df.columns:
                    df["municipio"] = df["municipio"].astype(str).str.replace(r"\s*-\s*MT$", "", regex=True).str.strip()
                if "codigo_ibge" in df.columns:
                    df["codigo_ibge"] = df["codigo_ibge"].astype(str).str.extract(r"(51\d{5})", expand=False)
                return df
        except Exception:
            continue
    return pd.DataFrame()


def _merge_determinantes_referencia_antiga(df: pd.DataFrame) -> pd.DataFrame:
    ref = _read_determinantes_referencia_antiga()
    if ref.empty:
        return df
    out = df.copy()
    chave = "codigo_ibge" if "codigo_ibge" in out.columns and "codigo_ibge" in ref.columns else "municipio"
    if chave == "codigo_ibge":
        out["codigo_ibge"] = out["codigo_ibge"].astype(str).str.extract(r"(51\d{5})", expand=False)
        ref["codigo_ibge"] = ref["codigo_ibge"].astype(str).str.extract(r"(51\d{5})", expand=False)
    else:
        out["municipio"] = out["municipio"].astype(str).str.strip()
        ref["municipio"] = ref["municipio"].astype(str).str.strip()
    ref = ref.dropna(subset=[chave]).drop_duplicates(subset=[chave], keep="last")
    merged = out.merge(ref, on=chave, how="left", suffixes=("", "__ref_antiga"))
    preenchidas = {}
    for col in ref.columns:
        if col == chave:
            continue
        ref_col = f"{col}__ref_antiga" if col in out.columns else col
        if ref_col not in merged.columns:
            continue
        if col in merged.columns and ref_col != col:
            atual = merged[col]
            vazio = atual.isna() | atual.astype(str).str.strip().isin(["", "None", "nan", "NaN"])
            antes = int(pd.to_numeric(merged[col], errors="coerce").notna().sum()) if col not in {"municipio", "fonte_saneamento", "fonte_alfabetizacao", "fonte_instrucao", "fonte_renda", "fonte_recuperacao", "observacao_recuperacao"} else int((~vazio).sum())
            merged.loc[vazio, col] = merged.loc[vazio, ref_col]
            depois = int(pd.to_numeric(merged[col], errors="coerce").notna().sum()) if col not in {"municipio", "fonte_saneamento", "fonte_alfabetizacao", "fonte_instrucao", "fonte_renda", "fonte_recuperacao", "observacao_recuperacao"} else int((~(merged[col].isna() | merged[col].astype(str).str.strip().isin(["", "None", "nan", "NaN"]))).sum())
            if depois > antes:
                preenchidas[col] = depois - antes
            merged = merged.drop(columns=[ref_col])
        elif col not in out.columns:
            # Coluna nova vinda da referência antiga.
            preenchidas[col] = int(merged[ref_col].notna().sum())
    if preenchidas:
        merged.attrs["determinantes_referencia_antiga"] = preenchidas
    return merged


def _to_num(serie: pd.Series) -> pd.Series:
    if serie is None:
        return pd.Series(dtype="float64")
    if pd.api.types.is_numeric_dtype(serie):
        return pd.to_numeric(serie, errors="coerce")
    s = serie.astype(str).str.strip()
    s = s.str.replace("R$", "", regex=False).str.replace("%", "", regex=False)
    s = s.str.replace(" ", "", regex=False)
    # Trata padrão brasileiro quando aparece vírgula decimal.
    mask_br = s.str.contains(",", na=False)
    s.loc[mask_br] = s.loc[mask_br].str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")


def _percent_rank_risk(serie: pd.Series, low_is_bad: bool = False) -> pd.Series:
    x = _to_num(serie)
    if x.notna().sum() <= 1:
        return pd.Series(np.nan, index=serie.index)
    rank = x.rank(pct=True, method="average") * 100
    return 100 - rank if low_is_bad else rank


def _first_existing(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    cols = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in cols:
            return cols[c.lower()]
    return None

def _norm_col(valor: object) -> str:
    texto = _norm_texto(valor)
    return texto.replace("_", " ").replace("-", " ")


def _find_col_keywords(df: pd.DataFrame, any_keywords: Iterable[str], exclude: Iterable[str] | None = None) -> str | None:
    """Localiza colunas importadas do SIDRA/IBGE por palavras-chave.

    É intencionalmente conservador: evita colunas de risco/score e retorna
    a primeira coluna numérica aproveitável.
    """
    exc = [_norm_col(e) for e in (exclude or [])]
    keys = [_norm_col(k) for k in any_keywords]
    candidatos = []
    for col in df.columns:
        n = _norm_col(col)
        if any(e and e in n for e in exc):
            continue
        if any(k and k in n for k in keys):
            serie = _to_num(df[col])
            preenchidos = int(serie.notna().sum())
            candidatos.append((preenchidos, len(str(col)), col))
    candidatos = [c for c in candidatos if c[0] > 0]
    if not candidatos:
        return None
    candidatos.sort(key=lambda x: (-x[0], x[1]))
    return candidatos[0][2]


def _serie_percentual(serie: pd.Series) -> pd.Series:
    x = _to_num(serie)
    # Bases do SIDRA às vezes vêm como proporção 0-1. Se for o caso, converte para %.
    if x.dropna().empty:
        return x
    vmax = x.dropna().max()
    if vmax <= 1.5:
        x = x * 100
    return x


def _preencher_determinantes_explicitos(df: pd.DataFrame) -> pd.DataFrame:
    """Cria colunas analíticas explícitas para água, esgoto e educação.

    O objetivo é recuperar a leitura do sistema antigo: analfabetismo/educação
    e saneamento deixam de ficar escondidos dentro de indicadores genéricos.
    A função não inventa dados; apenas organiza colunas já carregadas.
    """
    out = df.copy()

    # Prioriza os nomes exatos gravados pelo novo conector IBGE/SIDRA — Determinantes sociais básicos.
    # Só depois usa busca por palavras-chave para manter compatibilidade com cargas antigas.
    agua_col = _first_existing(out, ["abastecimento_agua_rede_pct", "agua_indicador", "abastecimento_agua", "agua"]) or _find_col_keywords(out, ["agua", "abastecimento"], exclude=["risco", "score", "indice_social"])
    esgoto_col = _first_existing(out, ["esgotamento_adequado_rede_ou_fossa_pct", "esgotamento_rede_pct", "esgoto_indicador", "esgotamento_sanitario", "esgoto"]) or _find_col_keywords(out, ["esgoto", "esgotamento", "rede esgoto"], exclude=["risco", "score", "indice_social"])
    lixo_col = _first_existing(out, ["lixo_coletado_pct", "lixo_indicador", "destino_lixo", "coleta_lixo"]) or _find_col_keywords(out, ["lixo", "residuo", "coleta"], exclude=["risco", "score", "indice_social"])
    saneamento_col = _first_existing(out, ["saneamento_censo_2022", "saneamento", "saneamento_indicador"])
    alfabetizacao_col = _first_existing(out, ["taxa_alfabetizacao_pct", "taxa_alfabetizacao", "alfabetizacao"]) or _find_col_keywords(out, ["alfabetizacao", "alfabetizacao_pct", "taxa_alfabetizacao"], exclude=["analfabet", "risco", "score"])
    analfabetismo_col = _first_existing(out, ["taxa_analfabetismo_estimado_pct", "taxa_analfabetismo_estimada", "taxa_analfabetismo", "analfabetismo"]) or _find_col_keywords(out, ["analfabet", "analfabetismo"], exclude=["risco", "score"])
    instrucao_baixa_col = _first_existing(out, ["nivel_instrucao_baixo_pct", "baixa_instrucao_pct", "sem_instrucao_fundamental_incompleto_pct"])
    instrucao_qualificada_col = _first_existing(out, ["nivel_instrucao_medio_ou_superior_pct", "nivel_instrucao_superior_completo_pct"])
    instrucao_col = instrucao_baixa_col or instrucao_qualificada_col or _find_col_keywords(out, ["instrucao", "escolaridade", "sem instrucao", "fundamental"], exclude=["risco", "score"])
    renda_col = _first_existing(out, ["renda_censo_2022", "rendimento_medio", "renda", "pib_per_capita"])

    if agua_col:
        out["agua_indicador"] = _serie_percentual(out[agua_col])
    if esgoto_col:
        out["esgoto_indicador"] = _serie_percentual(out[esgoto_col])
    if lixo_col:
        out["lixo_indicador"] = _serie_percentual(out[lixo_col])
    if saneamento_col and "saneamento_indicador" not in out.columns:
        out["saneamento_indicador"] = _serie_percentual(out[saneamento_col])

    if alfabetizacao_col:
        out["taxa_alfabetizacao_pct"] = _serie_percentual(out[alfabetizacao_col])
    if analfabetismo_col:
        out["taxa_analfabetismo_estimada"] = _serie_percentual(out[analfabetismo_col])
    elif "taxa_alfabetizacao_pct" in out.columns:
        alf = _serie_percentual(out["taxa_alfabetizacao_pct"])
        out["taxa_analfabetismo_estimada"] = np.where((alf >= 0) & (alf <= 100), 100 - alf, np.nan)

    if instrucao_col:
        out["educacao_indicador"] = _serie_percentual(out[instrucao_col])
        out["educacao_indicador_tipo"] = "baixo_nivel_instrucao" if instrucao_col == instrucao_baixa_col else "instrucao_media_ou_superior"
    if renda_col:
        out["renda_indicador"] = _to_num(out[renda_col])

    fontes = {
        "agua_indicador_fonte": agua_col,
        "esgoto_indicador_fonte": esgoto_col,
        "lixo_indicador_fonte": lixo_col,
        "saneamento_indicador_fonte": saneamento_col,
        "taxa_alfabetizacao_fonte": alfabetizacao_col,
        "taxa_analfabetismo_fonte": analfabetismo_col or ("100 - taxa_alfabetizacao" if alfabetizacao_col else None),
        "educacao_indicador_fonte": instrucao_col,
        "educacao_indicador_tipo": "baixo_nivel_instrucao" if instrucao_col == instrucao_baixa_col else ("instrucao_media_ou_superior" if instrucao_col else None),
        "renda_indicador_fonte": renda_col,
    }
    out.attrs["fontes_determinantes"] = fontes
    return out


def _read_base() -> pd.DataFrame:
    if _table_exists("base_municipal_consolidada"):
        df = _safe_read_sql("SELECT * FROM base_municipal_consolidada")
    else:
        df = _safe_read_sql("SELECT * FROM municipios")
    if df.empty:
        return df
    if "municipio" not in df.columns:
        return pd.DataFrame()
    df["municipio"] = df["municipio"].astype(str).str.strip()
    if "codigo_ibge" in df.columns:
        df["codigo_ibge"] = df["codigo_ibge"].astype(str).str.replace(r"\D", "", regex=True).str[:7]
    return df


def _read_indicadores_pivot() -> pd.DataFrame:
    if not _table_exists("indicadores_municipais"):
        return pd.DataFrame()
    ind = _safe_read_sql("SELECT * FROM indicadores_municipais")
    if ind.empty or "municipio" not in ind.columns or "indicador" not in ind.columns or "valor" not in ind.columns:
        return pd.DataFrame()
    if "ano" in ind.columns:
        ind["_ano_sort"] = pd.to_numeric(ind["ano"], errors="coerce")
    else:
        ind["_ano_sort"] = np.nan
    ind["municipio"] = ind["municipio"].astype(str).str.strip()
    ind["indicador"] = ind["indicador"].astype(str).str.strip()
    ind = ind.sort_values(["municipio", "indicador", "_ano_sort"], na_position="first")
    ind = ind.drop_duplicates(["municipio", "indicador"], keep="last")
    piv = ind.pivot_table(index="municipio", columns="indicador", values="valor", aggfunc="last").reset_index()
    piv.columns = [str(c) for c in piv.columns]
    return piv


def _enriquecer_com_indicadores(df: pd.DataFrame) -> pd.DataFrame:
    piv = _read_indicadores_pivot()
    if piv.empty:
        return df
    out = df.copy()
    aux = piv.copy()
    # Merge para manter todo indicador importado disponível com prefixo quando não há coluna equivalente.
    out = out.merge(aux, on="municipio", how="left", suffixes=("", "__indicador"))
    for col in list(out.columns):
        if col.endswith("__indicador"):
            base_col = col.replace("__indicador", "")
            if base_col in out.columns:
                vazio = out[base_col].isna() | (out[base_col].astype(str).str.strip().isin(["", "None", "nan"]))
                out.loc[vazio, base_col] = out.loc[vazio, col]
                out = out.drop(columns=[col])
            else:
                out = out.rename(columns={col: base_col})
    return out


def _count_by_municipio(table: str, nome_coluna_saida: str) -> pd.DataFrame:
    if not _table_exists(table):
        return pd.DataFrame(columns=["municipio", nome_coluna_saida])
    df = _safe_read_sql(f"SELECT * FROM {table}")
    if df.empty or "municipio" not in df.columns:
        return pd.DataFrame(columns=["municipio", nome_coluna_saida])
    df["municipio"] = df["municipio"].astype(str).str.strip()
    df = df[df["municipio"].notna() & ~df["municipio"].astype(str).str.upper().isin(["", "NONE", "NAN", "NÃO INFORMADO", "NAO INFORMADO"])]
    if df.empty:
        return pd.DataFrame(columns=["municipio", nome_coluna_saida])
    return df.groupby("municipio", as_index=False).size().rename(columns={"size": nome_coluna_saida})


def _calcular_indice(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in ["populacao", "area_km2", "densidade_hab_km2", "total_equipes_aps", "total_ubs", "total_profissionais_aps", "escolas_total", "escolas_rurais", "escolas_indigenas", "escolas_quilombolas", "matriculas_total", "matriculas_educacao_especial", "assentamentos", "terras_indigenas_intersecoes", "ocorrencias_ambientais"]:
        if c not in out.columns:
            out[c] = 0
        out[c] = _to_num(out[c]).fillna(0)

    out["pop_por_equipe_aps"] = np.where(out["total_equipes_aps"] > 0, out["populacao"] / out["total_equipes_aps"], np.nan)
    out["pop_por_ubs"] = np.where(out["total_ubs"] > 0, out["populacao"] / out["total_ubs"], np.nan)
    out["profissionais_por_equipe"] = np.where(out["total_equipes_aps"] > 0, out["total_profissionais_aps"] / out["total_equipes_aps"], np.nan)
    out["pct_escolas_rurais"] = np.where(out["escolas_total"] > 0, out["escolas_rurais"] / out["escolas_total"] * 100, np.nan)
    out["matriculas_educacao_especial_por_mil"] = np.where(out["matriculas_total"] > 0, out["matriculas_educacao_especial"] / out["matriculas_total"] * 1000, np.nan)

    renda_col = _first_existing(out, ["renda_censo_2022", "rendimento_medio", "renda", "pib_per_capita"])
    agua_col = _first_existing(out, ["agua_indicador", "abastecimento_agua", "agua", "abastecimento"])
    esgoto_col = _first_existing(out, ["esgoto_indicador", "esgotamento_sanitario", "esgoto", "saneamento_censo_2022", "saneamento_indicador", "saneamento"])
    alfabetizacao_col = _first_existing(out, ["taxa_alfabetizacao_pct", "taxa_alfabetizacao", "alfabetizacao", "taxa_de_alfabetizacao"])
    analfabetismo_col = _first_existing(out, ["taxa_analfabetismo_estimada", "analfabetismo", "taxa_analfabetismo"])
    instrucao_baixa_col = _first_existing(out, ["nivel_instrucao_baixo_pct", "educacao_indicador"]) if str(out.get("educacao_indicador_tipo", pd.Series([""])).iloc[0] if "educacao_indicador_tipo" in out.columns and len(out) else "") == "baixo_nivel_instrucao" else _first_existing(out, ["nivel_instrucao_baixo_pct"])
    instrucao_qualificada_col = _first_existing(out, ["nivel_instrucao_medio_ou_superior_pct", "nivel_instrucao_superior_completo_pct"])
    instrucao_col = instrucao_baixa_col or instrucao_qualificada_col or _first_existing(out, ["educacao_indicador", "nivel_instrucao", "instrucao", "escolaridade"])
    envelhecimento_col = _first_existing(out, ["indicador_demografico", "envelhecimento", "idade_mediana"])

    componentes = []
    nomes_componentes = []

    def add(nome: str, serie: pd.Series, peso: float, low_is_bad: bool = False):
        risco = _percent_rank_risk(serie, low_is_bad=low_is_bad)
        out[f"risco_{nome}"] = risco
        if risco.notna().sum() > 0:
            componentes.append((risco, peso))
            nomes_componentes.append(nome)

    if renda_col:
        add("renda", out[renda_col], 1.3, low_is_bad=True)
    if agua_col:
        add("agua", out[agua_col], 0.8, low_is_bad=True)
    if esgoto_col:
        add("esgoto", out[esgoto_col], 1.0, low_is_bad=True)
    if analfabetismo_col:
        add("analfabetismo", out[analfabetismo_col], 1.0, low_is_bad=False)
    elif alfabetizacao_col:
        add("alfabetizacao", out[alfabetizacao_col], 1.0, low_is_bad=True)
    if instrucao_col:
        # Se o indicador for baixo nível de instrução, valor maior é pior.
        # Se for médio/superior ou superior completo, valor menor é pior.
        instrucao_low_is_bad = False if instrucao_col == instrucao_baixa_col else True
        add("instrucao", out[instrucao_col], 0.7, low_is_bad=instrucao_low_is_bad)
    if envelhecimento_col:
        add("demografia", out[envelhecimento_col], 0.7, low_is_bad=False)

    add("pressao_equipe", out["pop_por_equipe_aps"], 1.2, low_is_bad=False)
    add("pressao_ubs", out["pop_por_ubs"], 1.0, low_is_bad=False)
    add("dispersao", out["area_km2"] / (out["densidade_hab_km2"].replace(0, np.nan) ** 0.25), 0.9, low_is_bad=False)
    add("ruralidade_escolar", out["pct_escolas_rurais"], 0.7, low_is_bad=False)
    add("territorios_especiais", out["assentamentos"] + out["terras_indigenas_intersecoes"] + out["escolas_indigenas"] + out["escolas_quilombolas"], 1.0, low_is_bad=False)
    add("ambiental", out["ocorrencias_ambientais"], 0.5, low_is_bad=False)

    if componentes:
        total_peso = sum(p for _, p in componentes)
        score = sum(s.fillna(s.median()) * p for s, p in componentes) / total_peso
        out["indice_social_aps"] = score.clip(0, 100).round(1)
    else:
        out["indice_social_aps"] = np.nan

    def classe(v):
        if pd.isna(v):
            return "Sem dados suficientes"
        if v >= 80:
            return "Muito alta"
        if v >= 65:
            return "Alta"
        if v >= 45:
            return "Média"
        return "Monitoramento"

    out["classe_vulnerabilidade_social_aps"] = out["indice_social_aps"].map(classe)

    def eixo(row):
        valores = {
            "Renda/condição econômica": row.get("risco_renda", np.nan),
            "Água e esgoto": np.nanmean([row.get("risco_agua", np.nan), row.get("risco_esgoto", np.nan)]),
            "Educação/analfabetismo": np.nanmean([row.get("risco_analfabetismo", np.nan), row.get("risco_alfabetizacao", np.nan), row.get("risco_instrucao", np.nan), row.get("risco_ruralidade_escolar", np.nan)]),
            "Pressão sobre a APS": np.nanmean([row.get("risco_pressao_equipe", np.nan), row.get("risco_pressao_ubs", np.nan)]),
            "Território/dispersão": np.nanmean([row.get("risco_dispersao", np.nan), row.get("risco_territorios_especiais", np.nan)]),
            "Risco ambiental": row.get("risco_ambiental", np.nan),
        }
        valores = {k: v for k, v in valores.items() if pd.notna(v)}
        if not valores:
            return "Sem eixo dominante"
        return max(valores, key=valores.get)

    out["eixo_social_dominante"] = out.apply(eixo, axis=1)
    out.attrs["componentes_usados"] = nomes_componentes
    return out


def carregar_analise_social() -> dict:
    base = _read_base()
    if base.empty:
        return {"ok": False, "mensagem": "Base municipal consolidada não encontrada.", "df": pd.DataFrame()}
    df = _enriquecer_com_indicadores(base)
    df = _merge_determinantes_referencia_antiga(df)
    ref_antiga_preenchidas = df.attrs.get("determinantes_referencia_antiga", {})

    for table, col in [
        ("dados_mt_assentamentos", "assentamentos"),
        ("dados_mt_terras_indigenas", "terras_indigenas_intersecoes"),
        ("dados_mt_areas_contaminadas", "ocorrencias_ambientais"),
    ]:
        cont = _count_by_municipio(table, col)
        if not cont.empty:
            df = df.merge(cont, on="municipio", how="left")
        if col not in df.columns:
            df[col] = 0
        df[col] = _to_num(df[col]).fillna(0)

    df = _preencher_determinantes_explicitos(df)
    fontes_determinantes = df.attrs.get("fontes_determinantes", {})
    if ref_antiga_preenchidas:
        fontes_determinantes["referencia_sistema_antigo"] = "data/reference/determinantes_sociais_sistema_antigo.csv"
        for _col_ref in ref_antiga_preenchidas:
            fontes_determinantes[f"{_col_ref}_fallback"] = "sistema antigo / IBGE Censo 2022"
    df = _calcular_indice(df)
    df.attrs["fontes_determinantes"] = fontes_determinantes
    df = df.sort_values("indice_social_aps", ascending=False, na_position="last").reset_index(drop=True)
    df["ranking_social_aps"] = np.arange(1, len(df) + 1)

    social_cols_preenchidas = {}
    for c in SOCIAL_COLUMNS:
        if c in df.columns:
            social_cols_preenchidas[c] = int(df[c].notna().sum() - df[c].astype(str).isin(["", "None", "nan"]).sum())

    resumo = {
        "municipios": int(len(df)),
        "populacao_total": int(_to_num(df.get("populacao", pd.Series())).fillna(0).sum()),
        "muito_alta": int((df["classe_vulnerabilidade_social_aps"] == "Muito alta").sum()),
        "alta": int((df["classe_vulnerabilidade_social_aps"] == "Alta").sum()),
        "assentamentos": int(df["assentamentos"].sum()),
        "terras_indigenas_intersecoes": int(df["terras_indigenas_intersecoes"].sum()),
        "ocorrencias_ambientais": int(df["ocorrencias_ambientais"].sum()),
        "componentes_usados": df.attrs.get("componentes_usados", []),
        "social_cols_preenchidas": social_cols_preenchidas,
        "fontes_determinantes": df.attrs.get("fontes_determinantes", {}),
        "determinantes_referencia_antiga": ref_antiga_preenchidas,
    }
    return {"ok": True, "mensagem": "Análise social APS carregada.", "df": df, "resumo": resumo}


def resumo_regional(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "regiao_saude" not in df.columns:
        return pd.DataFrame()
    agg = df.groupby("regiao_saude", as_index=False).agg(
        municipios=("municipio", "count"),
        populacao=("populacao", "sum"),
        indice_medio=("indice_social_aps", "mean"),
        muito_alta=("classe_vulnerabilidade_social_aps", lambda s: (s == "Muito alta").sum()),
        alta=("classe_vulnerabilidade_social_aps", lambda s: (s == "Alta").sum()),
        assentamentos=("assentamentos", "sum"),
        terras_indigenas_intersecoes=("terras_indigenas_intersecoes", "sum"),
        ocorrencias_ambientais=("ocorrencias_ambientais", "sum"),
        pop_por_equipe_media=("pop_por_equipe_aps", "mean"),
        pop_por_ubs_media=("pop_por_ubs", "mean"),
    )
    agg["indice_medio"] = agg["indice_medio"].round(1)
    return agg.sort_values("indice_medio", ascending=False)



def integrar_acesso_rural_social(df: pd.DataFrame) -> pd.DataFrame:
    """Integra, quando disponível, a leitura de acesso rural assentamento -> UBS/APS.

    Não recalcula coordenadas nem cria aproximações. Usa a rotina oficial do módulo de
    Georreferenciamento, que trabalha apenas com UBS/APS elegíveis e coordenadas válidas.
    """
    out = df.copy()
    for col in [
        "assentamentos_analisados_acesso",
        "assentamentos_criticos_acesso",
        "assentamentos_distantes_acesso",
        "distancia_media_assentamento_ubs_km",
        "distancia_maxima_assentamento_ubs_km",
        "alerta_acesso_rural",
    ]:
        if col not in out.columns:
            out[col] = 0 if col != "alerta_acesso_rural" else "Sem assentamento analisado"
    try:
        from services.georreferenciamento_service import montar_acesso_rural_aps
        acesso = montar_acesso_rural_aps()
        mun = acesso.get("ranking_municipios", pd.DataFrame())
    except Exception:
        mun = pd.DataFrame()
    if mun is None or mun.empty or "municipio" not in mun.columns:
        out["tem_acesso_rural_calculado"] = False
        return out
    aux = mun.copy()
    rename = {
        "assentamentos": "assentamentos_analisados_acesso",
        "criticos": "assentamentos_criticos_acesso",
        "distantes": "assentamentos_distantes_acesso",
        "distancia_media_km": "distancia_media_assentamento_ubs_km",
        "distancia_maxima_km": "distancia_maxima_assentamento_ubs_km",
    }
    aux = aux.rename(columns={k: v for k, v in rename.items() if k in aux.columns})
    keep = ["municipio"] + [v for v in rename.values() if v in aux.columns]
    aux = aux[keep].drop_duplicates("municipio")
    out = out.merge(aux, on="municipio", how="left", suffixes=("", "__acesso"))
    for col in [
        "assentamentos_analisados_acesso",
        "assentamentos_criticos_acesso",
        "assentamentos_distantes_acesso",
        "distancia_media_assentamento_ubs_km",
        "distancia_maxima_assentamento_ubs_km",
    ]:
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
    def alerta(row):
        if row.get("assentamentos_analisados_acesso", 0) <= 0:
            return "Sem assentamento analisado"
        if row.get("assentamentos_criticos_acesso", 0) > 0:
            return "Crítico"
        if row.get("assentamentos_distantes_acesso", 0) > 0:
            return "Alto"
        if row.get("distancia_media_assentamento_ubs_km", 0) > 15:
            return "Médio"
        return "Monitoramento"
    out["alerta_acesso_rural"] = out.apply(alerta, axis=1)
    out["tem_acesso_rural_calculado"] = out["assentamentos_analisados_acesso"] > 0
    return out


def montar_matriz_social_acesso(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    out = integrar_acesso_rural_social(df)
    def prioridade(row):
        classe = str(row.get("classe_vulnerabilidade_social_aps", ""))
        acesso = str(row.get("alerta_acesso_rural", ""))
        if classe == "Muito alta" and acesso in ["Crítico", "Alto"]:
            return "Prioridade máxima"
        if classe in ["Muito alta", "Alta"] and acesso in ["Crítico", "Alto", "Médio"]:
            return "Alta prioridade"
        if classe in ["Muito alta", "Alta"]:
            return "Prioridade social"
        if acesso in ["Crítico", "Alto"]:
            return "Prioridade territorial"
        return "Monitoramento"
    def encaminhamento(row):
        p = row.get("prioridade_social_acesso", "Monitoramento")
        if p == "Prioridade máxima":
            return "Abrir análise técnica integrada: vulnerabilidade social, acesso rural, referência APS e eventual estratégia territorial específica."
        if p == "Alta prioridade":
            return "Priorizar validação municipal e regional; cruzar com equipes, UBS, escolas rurais, assentamentos e distância até APS."
        if p == "Prioridade social":
            return "Aprofundar determinantes sociais e capacidade de resposta da APS."
        if p == "Prioridade territorial":
            return "Aprofundar barreiras de acesso, logística rural e referência APS dos assentamentos."
        return "Manter em monitoramento e atualizar conforme novas bases sociais/geográficas."
    out["prioridade_social_acesso"] = out.apply(prioridade, axis=1)
    out["encaminhamento_integrado"] = out.apply(encaminhamento, axis=1)
    ordem = {"Prioridade máxima": 1, "Alta prioridade": 2, "Prioridade social": 3, "Prioridade territorial": 4, "Monitoramento": 5}
    out["ordem_prioridade_integrada"] = out["prioridade_social_acesso"].map(ordem).fillna(9).astype(int)
    cols = [
        "municipio", "regiao_saude", "prioridade_social_acesso", "indice_social_aps",
        "classe_vulnerabilidade_social_aps", "alerta_acesso_rural", "assentamentos_analisados_acesso",
        "assentamentos_criticos_acesso", "assentamentos_distantes_acesso",
        "distancia_media_assentamento_ubs_km", "distancia_maxima_assentamento_ubs_km",
        "pop_por_equipe_aps", "pop_por_ubs", "eixo_social_dominante", "encaminhamento_integrado",
        "ordem_prioridade_integrada",
    ]
    cols = [c for c in cols if c in out.columns]
    return out[cols].sort_values(["ordem_prioridade_integrada", "indice_social_aps", "distancia_maxima_assentamento_ubs_km"], ascending=[True, False, False]).reset_index(drop=True)


def montar_carteira_social_aps(df: pd.DataFrame) -> pd.DataFrame:
    matriz = montar_matriz_social_acesso(df)
    if matriz.empty:
        return pd.DataFrame()
    rows = []
    for _, r in matriz.iterrows():
        municipio = r.get("municipio")
        regiao = r.get("regiao_saude")
        classe = r.get("classe_vulnerabilidade_social_aps")
        acesso = r.get("alerta_acesso_rural")
        eixo = r.get("eixo_social_dominante")
        if classe in ["Muito alta", "Alta"]:
            rows.append({
                "municipio": municipio, "regiao_saude": regiao, "linha_acao": "Determinantes sociais",
                "nivel": classe, "justificativa": f"Vulnerabilidade social APS {classe.lower()} e eixo dominante: {eixo}.",
                "encaminhamento": "Validar renda, saneamento, educação, ruralidade e capacidade APS com a área técnica e município.",
            })
        if acesso in ["Crítico", "Alto"]:
            rows.append({
                "municipio": municipio, "regiao_saude": regiao, "linha_acao": "Acesso rural à APS",
                "nivel": acesso, "justificativa": f"Há assentamentos em alerta de acesso rural {str(acesso).lower()} até UBS/APS.",
                "encaminhamento": "Avaliar rota, equipe de referência, agenda rural programada e estratégia de atendimento territorial.",
            })
        if r.get("pop_por_equipe_aps", 0) and float(r.get("pop_por_equipe_aps", 0) or 0) > 4000:
            rows.append({
                "municipio": municipio, "regiao_saude": regiao, "linha_acao": "Pressão assistencial",
                "nivel": "Atenção", "justificativa": "População por equipe APS elevada no cruzamento social.",
                "encaminhamento": "Verificar composição de equipes, cadastro territorial, áreas descobertas e necessidade de reorganização.",
            })
    cart = pd.DataFrame(rows)
    if not cart.empty:
        prioridade = {"Muito alta": 1, "Crítico": 1, "Alta": 2, "Alto": 2, "Atenção": 3}
        cart["ordem"] = cart["nivel"].map(prioridade).fillna(9).astype(int)
        cart = cart.sort_values(["ordem", "regiao_saude", "municipio", "linha_acao"]).drop(columns=["ordem"])
    return cart

def texto_municipal_social(df: pd.DataFrame, municipio: str) -> str:
    if df.empty or not municipio:
        return ""
    sel = df[df["municipio"] == municipio]
    if sel.empty:
        return ""
    r = sel.iloc[0]
    linhas = [
        f"Síntese social e APS — {municipio}",
        "",
        f"O município apresenta classificação preliminar '{r.get('classe_vulnerabilidade_social_aps', 'não classificada')}' no Índice Social APS, com pontuação {r.get('indice_social_aps', np.nan)}.",
        f"O eixo dominante identificado é: {r.get('eixo_social_dominante', 'não identificado')}.",
        "",
        f"População estimada/consolidada: {int(r.get('populacao', 0) or 0):,}.".replace(',', '.'),
        f"População por equipe APS: {r.get('pop_por_equipe_aps', np.nan):.1f} habitantes/equipe." if pd.notna(r.get('pop_por_equipe_aps', np.nan)) else "População por equipe APS: não calculada.",
        f"População por UBS/estabelecimento: {r.get('pop_por_ubs', np.nan):.1f} habitantes/unidade." if pd.notna(r.get('pop_por_ubs', np.nan)) else "População por UBS/estabelecimento: não calculada.",
        f"Taxa estimada de analfabetismo: {r.get('taxa_analfabetismo_estimada', np.nan):.1f}%.",
        f"Indicador de água/abastecimento detectado: {r.get('agua_indicador', np.nan):.1f}%.",
        f"Indicador de esgoto/saneamento detectado: {r.get('esgoto_indicador', np.nan):.1f}%.",
        f"Assentamentos vinculados ao município: {int(r.get('assentamentos', 0) or 0)}.",
        f"Interseções/registros de terras indígenas: {int(r.get('terras_indigenas_intersecoes', 0) or 0)}.",
        f"Ocorrências ambientais vinculadas: {int(r.get('ocorrencias_ambientais', 0) or 0)}.",
        "",
        "Leitura técnica preliminar: a classificação deve orientar investigação e priorização, não substituir validação técnica da área responsável. Recomenda-se cruzar esta leitura com a análise territorial, distância rural até UBS/APS e dados locais atualizados.",
    ]
    return "\n".join(linhas)
