from __future__ import annotations

import json
import math
import re
import unicodedata
from functools import lru_cache
from typing import Any

import pandas as pd

try:
    from shapely.geometry import Point, shape
except Exception:  # pragma: no cover
    Point = None
    shape = None

from database.queries import read_table


def _normalizar_texto(valor: Any) -> str:
    texto = "" if valor is None else str(valor)
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-zA-Z0-9]+", " ", texto).strip().lower()
    return re.sub(r"\s+", " ", texto)


def normalizar_cnes(valor: Any) -> str:
    digitos = re.sub(r"\D", "", "" if valor is None else str(valor))
    return digitos.zfill(7)[-7:] if digitos else ""


def _lat_lon_valida_mt(lat: Any, lon: Any) -> bool:
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except Exception:
        return False
    if math.isnan(lat_f) or math.isnan(lon_f):
        return False
    return -19.8 <= lat_f <= -7.0 and -62.0 <= lon_f <= -50.0


def deduplicar_estabelecimentos_saude(df: pd.DataFrame) -> pd.DataFrame:
    """Remove duplicidades de estabelecimentos por CNES, priorizando a melhor linha.

    Critério de retenção:
    1. CNES válido;
    2. coordenada válida em Mato Grosso;
    3. registro com mais campos preenchidos;
    4. registro mais recente, quando houver atualizado_em;
    5. primeira ocorrência.
    """
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df

    out = df.copy()
    if "cnes" not in out.columns:
        return out

    out["cnes_norm"] = out["cnes"].map(normalizar_cnes)
    out = out[out["cnes_norm"].astype(str).str.len() > 0].copy()
    if out.empty:
        return out

    lat_col = "latitude" if "latitude" in out.columns else None
    lon_col = "longitude" if "longitude" in out.columns else None
    if lat_col and lon_col:
        out["_lat_num"] = pd.to_numeric(out[lat_col], errors="coerce")
        out["_lon_num"] = pd.to_numeric(out[lon_col], errors="coerce")
        out["_coord_valida"] = [_lat_lon_valida_mt(a, b) for a, b in zip(out["_lat_num"], out["_lon_num"])]
    else:
        out["_coord_valida"] = False

    campos_relevantes = [c for c in ["nome_unidade", "tipo_unidade", "endereco", "municipio", "codigo_ibge", "latitude", "longitude"] if c in out.columns]
    if campos_relevantes:
        out["_campos_preenchidos"] = out[campos_relevantes].notna().sum(axis=1)
        for c in campos_relevantes:
            out["_campos_preenchidos"] += out[c].astype(str).str.strip().replace({"": pd.NA, "nan": pd.NA, "None": pd.NA}).notna().astype(int)
    else:
        out["_campos_preenchidos"] = 0

    if "atualizado_em" in out.columns:
        out["_dt_atualizado"] = pd.to_datetime(out["atualizado_em"], errors="coerce")
    else:
        out["_dt_atualizado"] = pd.NaT

    out = out.sort_values(
        ["cnes_norm", "_coord_valida", "_campos_preenchidos", "_dt_atualizado"],
        ascending=[True, False, False, False],
        na_position="last",
    )
    out = out.drop_duplicates("cnes_norm", keep="first")
    return out.drop(columns=["_lat_num", "_lon_num", "_coord_valida", "_campos_preenchidos", "_dt_atualizado"], errors="ignore").reset_index(drop=True)


def resumo_duplicidades_estabelecimentos(df: pd.DataFrame) -> dict:
    if df is None or df.empty or "cnes" not in df.columns:
        return {
            "linhas_brutas": 0,
            "cnes_unicos": 0,
            "linhas_duplicadas_removiveis": 0,
            "municipios_com_duplicidade": 0,
            "duplicidades_por_municipio": pd.DataFrame(),
        }
    aux = df.copy()
    aux["cnes_norm"] = aux["cnes"].map(normalizar_cnes)
    aux = aux[aux["cnes_norm"].astype(str).str.len() > 0].copy()
    grp = aux.groupby(["municipio", "codigo_ibge", "cnes_norm"], dropna=False).size().reset_index(name="linhas")
    dup = grp[grp["linhas"] > 1].copy()
    por_mun = pd.DataFrame()
    if not dup.empty:
        por_mun = (
            dup.assign(linhas_excedentes=dup["linhas"] - 1)
            .groupby(["municipio", "codigo_ibge"], dropna=False)
            .agg(cnes_duplicados=("cnes_norm", "nunique"), linhas_excedentes=("linhas_excedentes", "sum"))
            .reset_index()
            .sort_values(["linhas_excedentes", "cnes_duplicados"], ascending=[False, False])
        )
    return {
        "linhas_brutas": int(len(aux)),
        "cnes_unicos": int(aux["cnes_norm"].nunique()),
        "linhas_duplicadas_removiveis": int((dup["linhas"] - 1).sum()) if not dup.empty else 0,
        "municipios_com_duplicidade": int(por_mun["municipio"].nunique()) if not por_mun.empty else 0,
        "duplicidades_por_municipio": por_mun,
    }


@lru_cache(maxsize=1)
def _carregar_geometrias_municipais() -> list[dict[str, Any]]:
    if shape is None:
        return []
    malhas = read_table("malhas_geograficas_municipais")
    if malhas.empty or "geometry_json" not in malhas.columns:
        return []

    registros: list[dict[str, Any]] = []
    for _, row in malhas.iterrows():
        try:
            geom_raw = row.get("geometry_json")
            if not geom_raw:
                continue
            geom_json = json.loads(geom_raw) if isinstance(geom_raw, str) else geom_raw
            geom = shape(geom_json)
            if not geom.is_valid:
                geom = geom.buffer(0)
            registros.append({
                "municipio": row.get("municipio", ""),
                "codigo_ibge": str(row.get("codigo_ibge", "")).strip(),
                "municipio_norm": _normalizar_texto(row.get("municipio", "")),
                "geometry": geom,
                "bounds": geom.bounds,
            })
        except Exception:
            continue
    return registros


def validar_municipio_geografico_pontos(
    df: pd.DataFrame,
    lat_col: str = "latitude",
    lon_col: str = "longitude",
    municipio_col: str = "municipio",
    codigo_col: str = "codigo_ibge",
) -> pd.DataFrame:
    """Valida se a coordenada do ponto cai dentro da malha do município informado.

    Não corrige automaticamente o município textual. Apenas cria colunas de
    auditoria e define se o registro deve entrar no cálculo principal.
    """
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df
    out = df.copy()
    if lat_col not in out.columns or lon_col not in out.columns or municipio_col not in out.columns:
        out["municipio_textual_confere_geometria"] = True
        out["municipio_geografico_estimado"] = ""
        out["codigo_ibge_geografico_estimado"] = ""
        out["alerta_municipio_geografico"] = ""
        out["registro_utilizado_no_calculo"] = True
        return out

    geoms = _carregar_geometrias_municipais()
    if not geoms or Point is None:
        out["municipio_textual_confere_geometria"] = True
        out["municipio_geografico_estimado"] = ""
        out["codigo_ibge_geografico_estimado"] = ""
        out["alerta_municipio_geografico"] = "Validação espacial não executada: malha municipal indisponível."
        out["registro_utilizado_no_calculo"] = True
        return out

    # Índices simples por nome/código para testar primeiro o município declarado.
    por_nome = {g["municipio_norm"]: g for g in geoms}
    por_cod = {str(g["codigo_ibge"]).strip(): g for g in geoms if str(g["codigo_ibge"]).strip()}

    resultados = []
    for _, row in out.iterrows():
        lat = pd.to_numeric(pd.Series([row.get(lat_col)]), errors="coerce").iloc[0]
        lon = pd.to_numeric(pd.Series([row.get(lon_col)]), errors="coerce").iloc[0]
        declarado = str(row.get(municipio_col) or "").strip()
        cod = str(row.get(codigo_col) or "").strip()
        if pd.isna(lat) or pd.isna(lon) or not _lat_lon_valida_mt(lat, lon):
            resultados.append((False, "", "", "Coordenada ausente ou fora da faixa esperada para MT.", False))
            continue

        ponto = Point(float(lon), float(lat))
        esperado = por_cod.get(cod) or por_nome.get(_normalizar_texto(declarado))
        confere = False
        if esperado is not None:
            try:
                confere = bool(esperado["geometry"].contains(ponto) or esperado["geometry"].touches(ponto))
            except Exception:
                confere = False

        encontrado = None
        if not confere:
            for g in geoms:
                try:
                    minx, miny, maxx, maxy = g["bounds"]
                    if not (minx <= float(lon) <= maxx and miny <= float(lat) <= maxy):
                        continue
                    if g["geometry"].contains(ponto) or g["geometry"].touches(ponto):
                        encontrado = g
                        break
                except Exception:
                    continue

        if confere:
            resultados.append((True, declarado, cod, "", True))
        elif encontrado:
            msg = f"Divergência territorial: registro associado a {declarado or 'município não informado'}, mas a coordenada cai em {encontrado['municipio']}."
            resultados.append((False, encontrado["municipio"], encontrado["codigo_ibge"], msg, False))
        else:
            msg = f"Divergência territorial: coordenada não localizada na malha do município informado ({declarado or 'não informado'})."
            resultados.append((False, "", "", msg, False))

    out["municipio_textual_confere_geometria"] = [r[0] for r in resultados]
    out["municipio_geografico_estimado"] = [r[1] for r in resultados]
    out["codigo_ibge_geografico_estimado"] = [r[2] for r in resultados]
    out["alerta_municipio_geografico"] = [r[3] for r in resultados]
    out["registro_utilizado_no_calculo"] = [r[4] for r in resultados]
    return out



def validar_pontos_mapa_estrategico(
    df: pd.DataFrame,
    lat_col: str = "latitude",
    lon_col: str = "longitude",
    municipio_col: str = "municipio",
    codigo_col: str = "codigo_ibge",
) -> pd.DataFrame:
    """Valida pontos exibidos no mapa estratégico.

    Regras:
    - ponto fora da faixa esperada de MT: bloqueia;
    - ponto dentro de MT, mas fora da malha do município informado: bloqueia do mapa principal;
    - ponto coerente com município/malha: mantém.
    """
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df
    out = validar_municipio_geografico_pontos(
        df,
        lat_col=lat_col,
        lon_col=lon_col,
        municipio_col=municipio_col,
        codigo_col=codigo_col,
    ).copy()

    if lat_col in out.columns and lon_col in out.columns:
        fora_faixa = []
        for a, b in zip(out[lat_col], out[lon_col]):
            fora_faixa.append(not _lat_lon_valida_mt(a, b))
        out["coordenada_fora_faixa_mt"] = fora_faixa
    else:
        out["coordenada_fora_faixa_mt"] = True

    out["status_validacao_geografica"] = "Válido no Estado e no município"
    out.loc[out["coordenada_fora_faixa_mt"].astype(bool), "status_validacao_geografica"] = "Fora da faixa geográfica esperada para MT"
    mask_div = (~out["coordenada_fora_faixa_mt"].astype(bool)) & (~out["municipio_textual_confere_geometria"].astype(bool))
    out.loc[mask_div, "status_validacao_geografica"] = "Divergente: coordenada não confere com município informado"
    out["ponto_utilizado_mapa_principal"] = (
        (~out["coordenada_fora_faixa_mt"].astype(bool))
        & (out["municipio_textual_confere_geometria"].astype(bool))
    )
    return out

def diagnosticar_qualidade_geografica_territorios(df: pd.DataFrame) -> dict:
    if df is None or df.empty:
        return {
            "territorios_avaliados": 0,
            "territorios_consistentes": 0,
            "territorios_com_divergencia": 0,
            "percentual_consistente": 0.0,
        }
    if "municipio_textual_confere_geometria" not in df.columns:
        df = validar_municipio_geografico_pontos(df)
    ok = df["municipio_textual_confere_geometria"].astype(bool)
    total = int(len(df))
    consistentes = int(ok.sum())
    divergentes = total - consistentes
    return {
        "territorios_avaliados": total,
        "territorios_consistentes": consistentes,
        "territorios_com_divergencia": divergentes,
        "percentual_consistente": round(consistentes / total * 100, 1) if total else 0.0,
    }
