from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any

import pandas as pd
import requests

try:
    from config.ibge_estimativas_2025_mt import ESTIMATIVAS_POPULACAO_2025_MT
except Exception:  # pragma: no cover
    ESTIMATIVAS_POPULACAO_2025_MT = []

BASES_IPEA = [
    "https://www.ipeadata.gov.br/api/odata4",
    "http://www.ipeadata.gov.br/api/odata4",
]

ALVOS_IVS = {
    "ipea_ivs": ["índice de vulnerabilidade social", "ivs - índice", "ivs índice"],
    "ipea_ivs_infraestrutura_urbana": ["ivs infraestrutura", "infraestrutura urbana"],
    "ipea_ivs_capital_humano": ["ivs capital humano", "capital humano"],
    "ipea_ivs_renda_trabalho": ["ivs renda", "renda e trabalho"],
}


def _normalizar_texto(valor: Any) -> str:
    texto = str(valor or "").strip()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    return " ".join(texto.lower().split())


def _canonico_municipios_mt() -> pd.DataFrame:
    registros = []
    for item in ESTIMATIVAS_POPULACAO_2025_MT:
        if not isinstance(item, dict):
            continue
        codigo = str(item.get("codigo_ibge") or item.get("codigo") or "").strip()
        municipio = item.get("municipio") or item.get("nome") or item.get("nome_municipio")
        if re.fullmatch(r"51\d{5}", codigo) and municipio:
            registros.append({"codigo_ibge": codigo, "municipio": str(municipio).strip()})
    df = pd.DataFrame(registros).drop_duplicates("codigo_ibge")
    if df.empty:
        raise RuntimeError("Não consegui montar a lista canônica de municípios MT para filtrar o IPEA.")
    df["chave_municipio"] = df["municipio"].map(_normalizar_texto)
    return df


def _get_json(path: str, params: dict[str, Any] | None = None, timeout: int = 60) -> dict:
    erros = []
    for base in BASES_IPEA:
        url = f"{base.rstrip('/')}/{path.lstrip('/')}"
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.SSLError:
            try:
                r = requests.get(url, params=params, timeout=timeout, verify=False)
                r.raise_for_status()
                return r.json()
            except Exception as exc:
                erros.append(f"{url}: {exc}")
        except Exception as exc:
            erros.append(f"{url}: {exc}")
    raise RuntimeError(" | ".join(erros[-4:]))


def _odata_values(payload: Any) -> list[dict]:
    if isinstance(payload, dict):
        if isinstance(payload.get("value"), list):
            return payload["value"]
        # Algumas respostas antigas podem vir embrulhadas de outro modo.
        for v in payload.values():
            if isinstance(v, list) and (not v or isinstance(v[0], dict)):
                return v
    if isinstance(payload, list):
        return payload
    return []


def _buscar_metadados_iv() -> pd.DataFrame:
    tentativas = [
        ("Metadados", {"$filter": "contains(SERNOME,'Vulnerabilidade')", "$top": "500"}),
        ("Metadados", {"$filter": "contains(SERNOME,'IVS')", "$top": "500"}),
        ("Metadados", {"$filter": "contains(SERCOMENTARIO,'Vulnerabilidade Social')", "$top": "500"}),
        ("Metadados", {"$top": "5000"}),
    ]
    erros = []
    partes = []
    for path, params in tentativas:
        try:
            rows = _odata_values(_get_json(path, params=params, timeout=90))
            if rows:
                partes.append(pd.DataFrame(rows))
        except Exception as exc:
            erros.append(str(exc))
    if not partes:
        raise RuntimeError("Não foi possível consultar Metadados do Ipeadata. " + " | ".join(erros[-3:]))
    meta = pd.concat(partes, ignore_index=True).drop_duplicates()
    if meta.empty:
        raise RuntimeError("Metadados do Ipeadata retornaram vazios.")
    return meta


def _selecionar_series_iv(meta: pd.DataFrame) -> dict[str, str]:
    cols = {c.lower(): c for c in meta.columns}
    col_codigo = cols.get("sercodigo") or cols.get("ser_codigo") or cols.get("codigo")
    col_nome = cols.get("sernome") or cols.get("ser_nome") or cols.get("nome")
    col_coment = cols.get("sercomentario") or cols.get("comentario") or col_nome
    if not col_codigo or not col_nome:
        raise RuntimeError(f"Metadados IPEA sem colunas esperadas de código/nome. Colunas: {list(meta.columns)[:20]}")

    work = meta.copy()
    work["_texto"] = (work[col_nome].astype(str) + " " + work.get(col_coment, "").astype(str)).map(_normalizar_texto)
    selecionadas: dict[str, str] = {}
    for indicador, termos in ALVOS_IVS.items():
        candidatas = work[work["_texto"].apply(lambda t: any(_normalizar_texto(term) in t for term in termos))]
        # Preferir séries do Atlas da Vulnerabilidade Social, se houver.
        if not candidatas.empty:
            atlas = candidatas[candidatas["_texto"].str.contains("atlas", na=False)]
            escolhida = atlas.iloc[0] if not atlas.empty else candidatas.iloc[0]
            selecionadas[indicador] = str(escolhida[col_codigo])
    if not selecionadas:
        amostra = work[[col_codigo, col_nome]].head(20).to_dict("records")
        raise RuntimeError(f"Não encontrei séries IVS nos metadados IPEA. Amostra: {amostra}")
    return selecionadas


def _carregar_valores_serie(sercodigo: str) -> pd.DataFrame:
    caminhos = [
        f"Metadados('{sercodigo}')/Valores",
        f"ValoresSerie(SERCODIGO='{sercodigo}')",
        f"ValoresSerie('{sercodigo}')",
    ]
    erros = []
    for path in caminhos:
        try:
            rows = _odata_values(_get_json(path, timeout=120))
            if rows:
                df = pd.DataFrame(rows)
                df["_sercodigo_usado"] = sercodigo
                return df
        except Exception as exc:
            erros.append(f"{path}: {exc}")
    raise RuntimeError(" | ".join(erros))


def _extrair_coluna(df: pd.DataFrame, candidatos: list[str]) -> str | None:
    mapa = {_normalizar_texto(c).replace(" ", "_"): c for c in df.columns}
    for cand in candidatos:
        chave = _normalizar_texto(cand).replace(" ", "_")
        if chave in mapa:
            return mapa[chave]
    for c in df.columns:
        cn = _normalizar_texto(c)
        if any(_normalizar_texto(cand) in cn for cand in candidatos):
            return c
    return None


def _normalizar_valores_ipea(df: pd.DataFrame, indicador: str, ano: int) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    canon = _canonico_municipios_mt()
    col_valor = _extrair_coluna(df, ["VALVALOR", "valor", "valvalor"])
    col_data = _extrair_coluna(df, ["VALDATA", "data", "ano"])
    col_territorio_codigo = _extrair_coluna(df, ["TERCODIGO", "codigo_ibge", "cod_municipio", "municipio codigo", "codigo"])
    col_territorio_nome = _extrair_coluna(df, ["TERNOME", "municipio", "nome", "territorio"])
    if not col_valor:
        raise RuntimeError(f"Série IPEA sem coluna de valor reconhecida. Colunas: {list(df.columns)[:30]}")

    out = df.copy()
    # Ano.
    if col_data:
        data_txt = out[col_data].astype(str)
        out["ano"] = data_txt.str.extract(r"(\d{4})", expand=False)
    else:
        out["ano"] = str(ano)
    out["ano"] = pd.to_numeric(out["ano"], errors="coerce")
    out = out[out["ano"].fillna(0).astype(int) == int(ano)]
    if out.empty:
        return pd.DataFrame()

    # Código IBGE ou nome do município.
    if col_territorio_codigo:
        out["codigo_ibge"] = out[col_territorio_codigo].astype(str).str.extract(r"(51\d{5})", expand=False)
    else:
        out["codigo_ibge"] = None
    if col_territorio_nome:
        out["chave_municipio"] = out[col_territorio_nome].map(_normalizar_texto)
    else:
        out["chave_municipio"] = None

    por_codigo = out.merge(canon[["codigo_ibge", "municipio"]], on="codigo_ibge", how="inner") if out["codigo_ibge"].notna().any() else pd.DataFrame()
    por_nome = out.merge(canon[["chave_municipio", "codigo_ibge", "municipio"]], on="chave_municipio", how="inner") if out["chave_municipio"].notna().any() else pd.DataFrame()
    unido = pd.concat([por_codigo, por_nome], ignore_index=True, sort=False)
    if unido.empty:
        return pd.DataFrame()
    unido = unido.drop_duplicates(subset=["codigo_ibge", col_valor])
    unido["valor"] = pd.to_numeric(unido[col_valor].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    unido = unido.dropna(subset=["valor"])
    return pd.DataFrame({
        "codigo_ibge": unido["codigo_ibge"],
        "municipio": unido["municipio"],
        "ano": int(ano),
        "competencia": str(ano),
        "indicador": indicador,
        "valor": unido["valor"],
        "fonte": "IPEADATA_ATLAS_IVS",
    }).drop_duplicates(subset=["codigo_ibge", "indicador", "ano"])


def carregar_ipea_ivs_mt(ano: int = 2022, **kwargs) -> pd.DataFrame:
    """Carrega IVS e subíndices do IPEA/Ipeadata para municípios de MT.

    A rotina é defensiva: se o Ipeadata não retornar dados municipais de MT, falha em
    vez de criar sucesso falso. Os dados entram em indicadores_municipais como staging.
    """
    meta = _buscar_metadados_iv()
    series = _selecionar_series_iv(meta)
    partes = []
    erros = []
    for indicador, sercodigo in series.items():
        try:
            raw = _carregar_valores_serie(sercodigo)
            norm = _normalizar_valores_ipea(raw, indicador=indicador, ano=ano)
            if not norm.empty:
                partes.append(norm)
            else:
                erros.append(f"{indicador}/{sercodigo}: sem dados municipais MT para {ano}")
        except Exception as exc:
            erros.append(f"{indicador}/{sercodigo}: {exc}")
    if not partes:
        raise RuntimeError("IPEA/IVS não retornou dados municipais válidos para MT. " + " | ".join(erros[-6:]))
    df = pd.concat(partes, ignore_index=True)
    if df["valor"].notna().sum() == 0 or df["municipio"].nunique() == 0:
        raise RuntimeError("IPEA/IVS retornou sem valores úteis; carga bloqueada para evitar falso sucesso.")
    df["observacao"] = "Indicador do Ipeadata/Atlas IVS; validar cobertura municipal antes de uso oficial."
    return df


def testar_ipea_ivs_mt(ano: int = 2022, **kwargs) -> dict:
    df = carregar_ipea_ivs_mt(ano=ano, **kwargs)
    return {
        "ok": True,
        "ano": ano,
        "linhas": int(len(df)),
        "municipios": int(df["municipio"].nunique()),
        "indicadores": sorted(df["indicador"].dropna().unique().tolist()),
        "amostra": df.head(10).to_dict("records"),
    }
