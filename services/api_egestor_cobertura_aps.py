from __future__ import annotations

"""Conector experimental para Cobertura APS / e-Gestor / Relatório APS.

Objetivo: consumir fonte pública, sem upload manual, para trazer cobertura da APS
por município de Mato Grosso. O portal do Relatório APS/e-Gestor é uma aplicação
web e os endpoints de download podem mudar; por isso o conector combina:
  1) descoberta de arquivos/rotas a partir da página pública;
  2) tentativa de endpoints públicos conhecidos/prováveis;
  3) diagnóstico técnico claro quando o portal não expõe CSV/JSON direto.

Retorno esperado: DataFrame no formato longo aceito por indicadores_municipais:
municipio, ano, competencia, indicador, valor, fonte_url, observacao.
"""

import io
import json
import re
import unicodedata
from datetime import datetime
from typing import Iterable
from urllib.parse import urljoin

import pandas as pd
import requests

from config.municipios_mt import DEFAULT_MUNICIPIOS

BASE_URL = "https://relatorioaps.saude.gov.br"
PAGINA_COBERTURA_APS = f"{BASE_URL}/cobertura/aps"
UF = "MT"
TIMEOUT = 30


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 Plataforma-APS-SES-MT/1.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,text/csv,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    })
    return s


def _normalizar_texto(valor) -> str:
    texto = str(valor or "").strip().upper()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    return " ".join(texto.split())


_MUN_OFICIAIS = {_normalizar_texto(m.get("municipio")): m.get("municipio") for m in DEFAULT_MUNICIPIOS}
_CODIGOS_MT = {str(m.get("codigo_ibge", "")).strip(): m.get("municipio") for m in DEFAULT_MUNICIPIOS}


def _limpar_numero(serie: pd.Series) -> pd.Series:
    texto = serie.astype(str).str.strip()
    # Mantém decimal com vírgula quando vier em pt-BR; não remove ponto decimal simples.
    texto = texto.str.replace("%", "", regex=False)
    texto = texto.str.replace("\u00a0", " ", regex=False)
    # Se tiver vírgula decimal, remove pontos de milhar e troca vírgula por ponto.
    mask_virgula = texto.str.contains(",", na=False)
    texto.loc[mask_virgula] = texto.loc[mask_virgula].str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    texto = texto.str.replace(r"[^0-9\.\-]", "", regex=True)
    texto = texto.mask(texto.isin(["", "-", ".", "nan", "None"]), None)
    return pd.to_numeric(texto, errors="coerce")


def _primeira_coluna(df: pd.DataFrame, opcoes: Iterable[str]) -> str | None:
    normalizadas = {_normalizar_texto(c).replace(" ", "_"): c for c in df.columns}
    for opc in opcoes:
        chave = _normalizar_texto(opc).replace(" ", "_")
        if chave in normalizadas:
            return normalizadas[chave]
    for col in df.columns:
        col_norm = _normalizar_texto(col)
        for opc in opcoes:
            if _normalizar_texto(opc) in col_norm:
                return col
    return None


def _descobrir_urls(sess: requests.Session) -> list[str]:
    """Descobre links e scripts da página pública e cria candidatos CSV/JSON."""
    candidatos: list[str] = []
    try:
        resp = sess.get(PAGINA_COBERTURA_APS, timeout=TIMEOUT)
        if resp.ok:
            html = resp.text
            # Links diretos eventualmente expostos no HTML.
            for m in re.findall(r'''(?:href|src)=["']([^"']+)["']''', html, flags=re.I):
                url = urljoin(PAGINA_COBERTURA_APS, m)
                if any(p in url.lower() for p in ["cobertura", "aps", "download", "csv", "json", "xlsx", "api"]):
                    candidatos.append(url)
            # Busca assets JS e tenta extrair endpoints internos.
            js_urls = [urljoin(PAGINA_COBERTURA_APS, m) for m in re.findall(r'''src=["']([^"']+\.js[^"']*)["']''', html, flags=re.I)]
            for js_url in js_urls[:12]:
                try:
                    js = sess.get(js_url, timeout=TIMEOUT).text
                    for endpoint in re.findall(r'''["']([^"']*(?:cobertura|download|export|relatorio|aps)[^"']*)["']''', js, flags=re.I):
                        if len(endpoint) > 8 and not endpoint.startswith("data:"):
                            candidatos.append(urljoin(BASE_URL, endpoint))
                except Exception:
                    continue
    except Exception:
        pass

    # Candidatos conhecidos/prováveis. O portal pode alterar rotas; tentamos sem quebrar o app.
    candidatos.extend([
        f"{BASE_URL}/api/cobertura/aps?uf={UF}",
        f"{BASE_URL}/api/cobertura/aps/municipios?uf={UF}",
        f"{BASE_URL}/api/cobertura/aps/municipio?uf={UF}",
        f"{BASE_URL}/api/cobertura?uf={UF}",
        f"{BASE_URL}/cobertura/aps/download?uf={UF}",
        f"{BASE_URL}/cobertura/aps/export?uf={UF}",
        f"{BASE_URL}/cobertura/aps.csv?uf={UF}",
        f"{BASE_URL}/cobertura/aps.json?uf={UF}",
        f"{BASE_URL}/relatorio/cobertura/aps?uf={UF}",
    ])

    unicos = []
    vistos = set()
    for url in candidatos:
        if not url or url in vistos:
            continue
        vistos.add(url)
        unicos.append(url)
    return unicos


def _ler_resposta_para_df(resp: requests.Response) -> pd.DataFrame | None:
    ctype = (resp.headers.get("content-type") or "").lower()
    texto_inicio = resp.text[:200].strip() if resp.content else ""

    # JSON
    if "json" in ctype or texto_inicio.startswith("{") or texto_inicio.startswith("["):
        try:
            data = resp.json()
            # procura listas dentro de dicts comuns
            if isinstance(data, dict):
                for chave in ["data", "dados", "items", "content", "resultado", "results"]:
                    if isinstance(data.get(chave), list):
                        return pd.DataFrame(data[chave])
                # tenta normalizar o próprio dict se houver lista em algum nível
                for v in data.values():
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        return pd.DataFrame(v)
                return pd.DataFrame([data])
            if isinstance(data, list):
                return pd.DataFrame(data)
        except Exception:
            pass

    # CSV/TSV/texto tabular
    if any(x in ctype for x in ["csv", "text", "octet-stream", "excel"]) or ";" in texto_inicio or "," in texto_inicio:
        for enc in ["utf-8-sig", "utf-8", "latin1", "cp1252"]:
            try:
                content = resp.content.decode(enc, errors="replace")
                for sep in [";", ",", "\t", "|"]:
                    try:
                        df = pd.read_csv(io.StringIO(content), sep=sep, dtype=str)
                        if len(df.columns) >= 3 and len(df) > 0:
                            return df
                    except Exception:
                        continue
            except Exception:
                continue
    return None


def _padronizar_cobertura(df: pd.DataFrame, fonte_url: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    work = df.copy()
    work.columns = [str(c).strip() for c in work.columns]

    col_mun = _primeira_coluna(work, ["municipio", "nome_municipio", "no_municipio", "cidade", "município"])
    col_cod = _primeira_coluna(work, ["codigo_ibge", "cod_ibge", "ibge", "co_municipio", "codigo_municipio"])
    col_ano = _primeira_coluna(work, ["ano", "nu_ano", "ano_referencia", "ano_competencia"])
    col_mes = _primeira_coluna(work, ["mes", "mês", "competencia", "referencia", "periodo", "dt_competencia"])

    if col_mun:
        mun_norm = work[col_mun].map(_normalizar_texto)
        work["municipio"] = mun_norm.map(_MUN_OFICIAIS).fillna(work[col_mun].astype(str).str.strip())
    elif col_cod:
        cod = work[col_cod].astype(str).str.extract(r"(\d{6,7})", expand=False).fillna("")
        work["municipio"] = cod.map(_CODIGOS_MT).fillna(cod.str[:6].map({k[:6]: v for k, v in _CODIGOS_MT.items()}))
    else:
        return pd.DataFrame()

    work = work[work["municipio"].map(_normalizar_texto).isin(_MUN_OFICIAIS.keys())].copy()
    if work.empty:
        return pd.DataFrame()

    # Campos numéricos candidatos. Evita IDs/códigos.
    ignorar = {c for c in [col_mun, col_cod, col_ano, col_mes, "municipio"] if c}
    registros = []
    for col in work.columns:
        if col in ignorar:
            continue
        nome_norm = _normalizar_texto(col)
        if any(x in nome_norm for x in ["COD", "IBGE", "ID", "UF", "ESTADO"]):
            continue
        if not any(x in nome_norm for x in ["COBERT", "PERCENT", "%", "EQUIPE", "ESF", "EAP", "APS", "AB", "ACS", "BUCAL", "POPUL"]):
            continue
        valores = _limpar_numero(work[col])
        if not valores.notna().any():
            continue
        indicador = re.sub(r"[^a-z0-9]+", "_", unicodedata.normalize("NFKD", str(col).lower()).encode("ASCII", "ignore").decode("ASCII")).strip("_")
        if indicador:
            indicador = f"egestor_cobertura_{indicador}"
            registros.append(pd.DataFrame({
                "municipio": work["municipio"],
                "ano": pd.to_numeric(work[col_ano], errors="coerce") if col_ano else pd.NA,
                "competencia": work[col_mes].astype(str) if col_mes else "",
                "indicador": indicador,
                "valor": valores,
                "fonte_url": fonte_url,
                "observacao": "Cobertura APS/e-Gestor Relatório APS - importação API-only",
            }))

    if not registros:
        return pd.DataFrame()
    out = pd.concat(registros, ignore_index=True)
    out = out.dropna(subset=["municipio", "indicador", "valor"], how="any")
    out["fonte"] = "EGESTOR_COBERTURA_APS"
    return out


def carregar_cobertura_aps_egestor_mt(periodo: str = "mais_recente") -> pd.DataFrame:
    sess = _session()
    urls = _descobrir_urls(sess)
    erros = []

    for url in urls:
        try:
            resp = sess.get(url, timeout=TIMEOUT)
            if not resp.ok:
                erros.append(f"{url} -> HTTP {resp.status_code}")
                continue
            df_raw = _ler_resposta_para_df(resp)
            if df_raw is None or df_raw.empty:
                erros.append(f"{url} -> resposta sem tabela reconhecível")
                continue
            df = _padronizar_cobertura(df_raw, url)
            if not df.empty:
                df["atualizado_em"] = datetime.now().isoformat(timespec="seconds")
                if periodo == "mais_recente" and "ano" in df.columns and df["ano"].notna().any():
                    ano_max = pd.to_numeric(df["ano"], errors="coerce").max()
                    df = df[pd.to_numeric(df["ano"], errors="coerce") == ano_max].copy()
                return df.reset_index(drop=True)
            erros.append(f"{url} -> tabela lida, mas sem municípios de MT/indicadores de cobertura reconhecidos. Colunas: {list(df_raw.columns)[:20]}")
        except Exception as exc:
            erros.append(f"{url} -> {exc}")

    detalhe = " | ".join(erros[:12]) if erros else "nenhum endpoint candidato retornou dados tabulares"
    raise RuntimeError(
        "Não foi possível importar automaticamente a Cobertura APS do e-Gestor/Relatório APS. "
        "O portal público existe, mas pode não expor CSV/JSON direto estável para consumo automatizado. "
        f"Detalhe: {detalhe}"
    )


def testar_cobertura_aps_egestor_mt(periodo: str = "mais_recente") -> dict:
    try:
        df = carregar_cobertura_aps_egestor_mt(periodo=periodo)
        return {
            "ok": True,
            "fonte": "e-Gestor/Relatório APS — Cobertura APS",
            "linhas": int(len(df)),
            "municipios": int(df["municipio"].nunique()) if "municipio" in df.columns else 0,
            "indicadores": sorted(df["indicador"].dropna().unique().tolist())[:30] if "indicador" in df.columns else [],
            "colunas": list(df.columns),
        }
    except Exception as exc:
        return {"ok": False, "erro": str(exc)}
