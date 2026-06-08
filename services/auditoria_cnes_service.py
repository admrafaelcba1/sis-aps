from __future__ import annotations

import unicodedata
from typing import Dict, List, Tuple

import pandas as pd

from config.parametros import TIPOS_EQUIPE_CNES
from database.connection import get_connection

CODIGOS_PRIORITARIOS = [str(c) for c in TIPOS_EQUIPE_CNES.keys()]


def _normalizar_texto(valor) -> str:
    texto = str(valor or "").strip().upper()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    return " ".join(texto.split())


def _read_sql(tabela: str) -> pd.DataFrame:
    with get_connection() as conn:
        try:
            return pd.read_sql_query(f"SELECT * FROM {tabela}", conn)
        except Exception:
            return pd.DataFrame()


def _codigo_tipo(serie: pd.Series) -> pd.Series:
    if serie is None:
        return pd.Series(dtype="object")
    return serie.astype(str).str.extract(r"(\d+)", expand=False).fillna("").astype(str)


def _serie_numero(serie: pd.Series, default: float | int = 0) -> pd.Series:
    """Converte coluna para número com segurança, inclusive quando vier como object/string."""
    if serie is None:
        return pd.Series(dtype="float64")
    return pd.to_numeric(serie, errors="coerce").fillna(default)


def _serie_int(serie: pd.Series, default: int = 0) -> pd.Series:
    return _serie_numero(serie, default=default).astype(int)


def _media_segura(numerador: pd.Series, denominador: pd.Series) -> pd.Series:
    num = pd.to_numeric(numerador, errors="coerce")
    den = pd.to_numeric(denominador, errors="coerce")
    media = num / den.where(den.ne(0))
    return media.round(2)


def _preparar_equipes(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["municipio", "codigo_ibge", "cnes", "ine", "codigo_tipo_equipe", "tipo_equipe"])
    out = df.copy()
    for col in ["municipio", "codigo_ibge", "cnes", "ine", "codigo_tipo_equipe", "tipo_equipe"]:
        if col not in out.columns:
            out[col] = ""
    out["codigo_tipo_equipe"] = _codigo_tipo(out["codigo_tipo_equipe"])
    out = out[out["codigo_tipo_equipe"].isin(CODIGOS_PRIORITARIOS)].copy()
    out["tipo_equipe"] = out["codigo_tipo_equipe"].map(TIPOS_EQUIPE_CNES).fillna(out["tipo_equipe"])
    out["municipio"] = out["municipio"].fillna("").astype(str).str.strip()
    out["municipio_chave"] = out["municipio"].map(_normalizar_texto)
    out["cnes"] = out["cnes"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(7)
    out["ine"] = out["ine"].astype(str).str.strip()
    out["chave_equipe"] = out["municipio_chave"] + "|" + out["cnes"] + "|" + out["ine"].astype(str)
    return out


def _preparar_profissionais(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["municipio", "codigo_ibge", "cnes", "ine", "codigo_tipo_equipe", "tipo_equipe", "cbo", "nome_profissional"])
    out = df.copy()
    for col in ["municipio", "codigo_ibge", "cnes", "ine", "codigo_tipo_equipe", "tipo_equipe", "cbo", "nome_profissional"]:
        if col not in out.columns:
            out[col] = ""
    out["codigo_tipo_equipe"] = _codigo_tipo(out["codigo_tipo_equipe"])
    out = out[out["codigo_tipo_equipe"].isin(CODIGOS_PRIORITARIOS)].copy()
    out["tipo_equipe"] = out["codigo_tipo_equipe"].map(TIPOS_EQUIPE_CNES).fillna(out["tipo_equipe"])
    out["municipio"] = out["municipio"].fillna("").astype(str).str.strip()
    out["municipio_chave"] = out["municipio"].map(_normalizar_texto)
    out["cnes"] = out["cnes"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(7)
    out["ine"] = out["ine"].astype(str).str.strip()
    out["chave_equipe"] = out["municipio_chave"] + "|" + out["cnes"] + "|" + out["ine"].astype(str)
    return out


def carregar_bases() -> Dict[str, pd.DataFrame]:
    base = _read_sql("base_municipal_consolidada")
    equipes = _preparar_equipes(_read_sql("equipes_aps"))
    profissionais = _preparar_profissionais(_read_sql("profissionais_cnes"))
    return {"base": base, "equipes": equipes, "profissionais": profissionais}


def resumo_estadual() -> Dict[str, object]:
    dados = carregar_bases()
    base, equipes, profissionais = dados["base"], dados["equipes"], dados["profissionais"]
    municipios = int(base["municipio"].nunique()) if not base.empty and "municipio" in base.columns else 0
    total_equipes = int(len(equipes))
    total_profissionais = int(len(profissionais))
    municipios_sem_equipes = 0
    municipios_sem_profissionais = 0
    if not base.empty and "municipio" in base.columns:
        mun_base = set(base["municipio"].dropna().astype(str))
        mun_eq = set(equipes["municipio"].dropna().astype(str)) if not equipes.empty else set()
        mun_prof = set(profissionais["municipio"].dropna().astype(str)) if not profissionais.empty else set()
        municipios_sem_equipes = len(mun_base - mun_eq)
        municipios_sem_profissionais = len(mun_base - mun_prof)
    por_codigo = equipes.groupby("codigo_tipo_equipe").size().to_dict() if not equipes.empty else {}
    prof_por_codigo = profissionais.groupby("codigo_tipo_equipe").size().to_dict() if not profissionais.empty else {}
    return {
        "municipios": municipios,
        "total_equipes": total_equipes,
        "total_profissionais": total_profissionais,
        "municipios_sem_equipes": municipios_sem_equipes,
        "municipios_sem_profissionais": municipios_sem_profissionais,
        "equipes_por_codigo": {c: int(por_codigo.get(c, 0)) for c in CODIGOS_PRIORITARIOS},
        "profissionais_por_codigo": {c: int(prof_por_codigo.get(c, 0)) for c in CODIGOS_PRIORITARIOS},
    }


def resumo_por_municipio() -> pd.DataFrame:
    dados = carregar_bases()
    base, equipes, profissionais = dados["base"], dados["equipes"], dados["profissionais"]
    if base.empty:
        return pd.DataFrame()
    colunas_base = [c for c in ["municipio", "regiao_saude", "populacao", "total_ubs", "nivel_prioridade"] if c in base.columns]
    out = base[colunas_base].drop_duplicates(subset=["municipio"]).copy()

    if not equipes.empty:
        total_eq = equipes.groupby("municipio").size().reset_index(name="equipes_aps")
        out = out.merge(total_eq, on="municipio", how="left")
        for codigo in CODIGOS_PRIORITARIOS:
            tmp = equipes[equipes["codigo_tipo_equipe"] == codigo].groupby("municipio").size().reset_index(name=f"equipes_{codigo}")
            out = out.merge(tmp, on="municipio", how="left")
    else:
        out["equipes_aps"] = 0
        for codigo in CODIGOS_PRIORITARIOS:
            out[f"equipes_{codigo}"] = 0

    if not profissionais.empty:
        prof = profissionais.groupby("municipio").size().reset_index(name="vinculos_profissionais")
        out = out.merge(prof, on="municipio", how="left")
        prof_unicos = profissionais.copy()
        if "nome_profissional" in prof_unicos.columns:
            prof_unicos["profissional_chave"] = prof_unicos["nome_profissional"].fillna("").astype(str).str.strip().str.upper()
            prof_unicos.loc[prof_unicos["profissional_chave"].eq(""), "profissional_chave"] = prof_unicos.index.astype(str)
            unico = prof_unicos.drop_duplicates(subset=["municipio", "profissional_chave"]).groupby("municipio").size().reset_index(name="profissionais_unicos_estimados")
            out = out.merge(unico, on="municipio", how="left")
    else:
        out["vinculos_profissionais"] = 0
        out["profissionais_unicos_estimados"] = 0

    colunas_numericas = ["equipes_aps", "vinculos_profissionais", "profissionais_unicos_estimados", "total_ubs"] + [f"equipes_{c}" for c in CODIGOS_PRIORITARIOS]
    for col in colunas_numericas:
        if col not in out.columns:
            out[col] = 0
        out[col] = _serie_int(out[col], default=0)

    out["media_vinculos_por_equipe"] = _media_segura(out["vinculos_profissionais"], out["equipes_aps"])
    return out.sort_values(["vinculos_profissionais", "equipes_aps"], ascending=[False, False])


def resumo_por_tipo() -> pd.DataFrame:
    dados = carregar_bases()
    equipes, profissionais = dados["equipes"], dados["profissionais"]
    linhas: List[dict] = []
    for codigo, descricao in TIPOS_EQUIPE_CNES.items():
        qtd_eq = int((equipes["codigo_tipo_equipe"] == codigo).sum()) if not equipes.empty and "codigo_tipo_equipe" in equipes.columns else 0
        qtd_prof = int((profissionais["codigo_tipo_equipe"] == codigo).sum()) if not profissionais.empty and "codigo_tipo_equipe" in profissionais.columns else 0
        municipios_com_eq = int(equipes.loc[equipes["codigo_tipo_equipe"] == codigo, "municipio"].nunique()) if not equipes.empty and "municipio" in equipes.columns else 0
        linhas.append({
            "codigo": codigo,
            "tipo_equipe": descricao,
            "equipes": qtd_eq,
            "vinculos_profissionais": qtd_prof,
            "municipios_com_equipes": municipios_com_eq,
            "media_vinculos_por_equipe": round(qtd_prof / qtd_eq, 2) if qtd_eq else None,
        })
    return pd.DataFrame(linhas)


def auditoria_inconsistencias() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dados = carregar_bases()
    equipes, profissionais = dados["equipes"], dados["profissionais"]
    municipal = resumo_por_municipio()
    alertas = []
    if not municipal.empty:
        for _, row in municipal.iterrows():
            problemas = []
            equipes_aps = int(pd.to_numeric(pd.Series([row.get("equipes_aps", 0)]), errors="coerce").fillna(0).iloc[0])
            vinculos = int(pd.to_numeric(pd.Series([row.get("vinculos_profissionais", 0)]), errors="coerce").fillna(0).iloc[0])
            total_ubs = int(pd.to_numeric(pd.Series([row.get("total_ubs", 0)]), errors="coerce").fillna(0).iloc[0])
            if equipes_aps == 0:
                problemas.append("Sem equipe APS/INE nos códigos 70, 71, 72, 73, 74 e 76")
            if vinculos == 0:
                problemas.append("Sem profissionais vinculados às equipes")
            if total_ubs == 0:
                problemas.append("Sem UBS/estabelecimento na consolidação")
            media_valor = pd.to_numeric(pd.Series([row.get("media_vinculos_por_equipe")]), errors="coerce").iloc[0]
            if pd.notna(media_valor):
                if float(media_valor) < 2 and equipes_aps > 0:
                    problemas.append("Média muito baixa de vínculos por equipe")
                if float(media_valor) > 40:
                    problemas.append("Média muito alta de vínculos por equipe; verificar duplicidade/critério")
            if problemas:
                alertas.append({
                    "municipio": row.get("municipio"),
                    "regiao_saude": row.get("regiao_saude"),
                    "nivel_prioridade": row.get("nivel_prioridade"),
                    "equipes_aps": equipes_aps,
                    "vinculos_profissionais": vinculos,
                    "alertas": "; ".join(problemas),
                })
    alertas_df = pd.DataFrame(alertas)

    eq_sem_prof = pd.DataFrame()
    prof_sem_eq = pd.DataFrame()
    if not equipes.empty and not profissionais.empty:
        chaves_prof = set(profissionais["chave_equipe"].dropna().astype(str))
        chaves_eq = set(equipes["chave_equipe"].dropna().astype(str))
        eq_sem_prof = equipes[~equipes["chave_equipe"].isin(chaves_prof)].copy()
        prof_sem_eq = profissionais[~profissionais["chave_equipe"].isin(chaves_eq)].copy()
        eq_sem_prof = eq_sem_prof[[c for c in ["municipio", "cnes", "ine", "codigo_tipo_equipe", "tipo_equipe"] if c in eq_sem_prof.columns]]
        prof_sem_eq = prof_sem_eq[[c for c in ["municipio", "cnes", "ine", "codigo_tipo_equipe", "tipo_equipe", "cbo", "nome_profissional"] if c in prof_sem_eq.columns]]
    return alertas_df, eq_sem_prof, prof_sem_eq


def base_detalhada(tipo: str = "profissionais") -> pd.DataFrame:
    dados = carregar_bases()
    df = dados["profissionais"] if tipo == "profissionais" else dados["equipes"]
    if df.empty:
        return df
    remover = ["municipio_chave", "chave_equipe"]
    return df.drop(columns=[c for c in remover if c in df.columns], errors="ignore")
