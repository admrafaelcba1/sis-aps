"""Conector experimental FNS — repasses federais consolidados.

Objetivo: tentar consumir automaticamente dados municipais do portal ConsultaFNS
sem upload manual. O portal é uma aplicação dinâmica; por isso o conector
prioriza diagnóstico claro quando não encontra endpoint tabular/JSON estável.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import pandas as pd
import requests

from config.municipios_mt import DEFAULT_MUNICIPIOS

BASE_URL = "https://consultafns.saude.gov.br/"
PORTAL_CONSULTAS_URL = "https://portalfns.saude.gov.br/consultas/"
TIMEOUT = 45

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


def _session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update(_HEADERS)
    return sess


def _norm(txt: Any) -> str:
    import unicodedata

    texto = str(txt or "").strip().upper()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    texto = re.sub(r"\s+", " ", texto)
    return texto


_MUNICIPIOS = { _norm(m.get("municipio")): m for m in DEFAULT_MUNICIPIOS }
_CODIGOS = { str(m.get("codigo_ibge", "")).strip(): m for m in DEFAULT_MUNICIPIOS }


def _codigo_6(codigo: Any) -> str:
    dig = re.sub(r"\D", "", str(codigo or ""))
    if len(dig) >= 7:
        return dig[:6]
    return dig


def _municipio_por_codigo(codigo: Any) -> str | None:
    dig = re.sub(r"\D", "", str(codigo or ""))
    if not dig:
        return None
    if len(dig) >= 7 and dig[:7] in _CODIGOS:
        return _CODIGOS[dig[:7]]["municipio"]
    for cod, item in _CODIGOS.items():
        if cod and cod[:6] == dig[:6]:
            return item["municipio"]
    return None


def _to_number(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    txt = str(v).strip()
    if not txt or txt.lower() in {"nan", "none", "null", "-"}:
        return None
    txt = txt.replace("R$", "").replace(" ", "")
    if "," in txt:
        txt = txt.replace(".", "").replace(",", ".")
    txt = re.sub(r"[^0-9.\-]", "", txt)
    try:
        return float(txt)
    except Exception:
        return None


def _flatten(obj: Any, prefix: str = "") -> list[dict[str, Any]]:
    if isinstance(obj, list):
        rows: list[dict[str, Any]] = []
        for item in obj:
            rows.extend(_flatten(item, prefix))
        return rows
    if isinstance(obj, dict):
        # Retorna dicionários folha que pareçam linhas tabulares.
        simple = {k: v for k, v in obj.items() if not isinstance(v, (dict, list))}
        nested_rows: list[dict[str, Any]] = []
        for k, v in obj.items():
            if isinstance(v, (list, dict)):
                nested_rows.extend(_flatten(v, f"{prefix}{k}."))
        if len(simple) >= 2:
            if nested_rows:
                for r in nested_rows:
                    r.update({f"{prefix}{k}": v for k, v in simple.items()})
                return nested_rows
            return [{f"{prefix}{k}": v for k, v in simple.items()}]
        return nested_rows
    return []


def _df_from_json(obj: Any) -> pd.DataFrame:
    linhas = _flatten(obj)
    if not linhas:
        return pd.DataFrame()
    return pd.DataFrame(linhas)


def _find_scripts(html: str) -> list[str]:
    srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, flags=re.I)
    return [urljoin(BASE_URL, s) for s in srcs]


def _candidate_api_paths_from_js(js_text: str) -> list[str]:
    candidatos: list[str] = []
    # Procura strings que pareçam rotas internas de API/serviços.
    for m in re.finditer(r'["\']([^"\']*(?:api|rest|service|consulta|pagamento|consolidada|detalhada|repasses?)[^"\']*)["\']', js_text, flags=re.I):
        val = m.group(1).strip()
        if not val or len(val) > 240:
            continue
        if any(x in val.lower() for x in [".png", ".css", ".svg", ".woff", "assets/"]):
            continue
        candidatos.append(val)
    # Dedup preservando ordem.
    seen = set()
    out = []
    for c in candidatos:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out[:80]


def _normalizar_fns_dataframe(df: pd.DataFrame, ano: int) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    work = df.copy()
    work.columns = [str(c).strip() for c in work.columns]

    def find_col(patterns: list[str]) -> str | None:
        for col in work.columns:
            n = _norm(col)
            if any(p in n for p in patterns):
                return col
        return None

    col_mun = find_col(["MUNICIPIO", "MUNICÍPIO", "NOME MUNICIPIO", "NO MUNICIPIO"])
    col_cod = find_col(["IBGE", "COD MUNIC", "CO MUNIC", "CODIGO MUNIC", "CODIGO_IBGE"])
    col_valor = find_col(["VALOR", "VL", "TOTAL", "PAGO", "REPASSE", "TRANSFER"])
    col_bloco = find_col(["BLOCO"])
    col_grupo = find_col(["GRUPO"])
    col_acao = find_col(["ACAO", "AÇÃO"])

    if not col_mun and not col_cod:
        return pd.DataFrame()
    if not col_valor:
        return pd.DataFrame()

    registros = []
    for _, row in work.iterrows():
        municipio = None
        if col_mun:
            chave = _norm(row.get(col_mun))
            if chave in _MUNICIPIOS:
                municipio = _MUNICIPIOS[chave]["municipio"]
        if not municipio and col_cod:
            municipio = _municipio_por_codigo(row.get(col_cod))
        if not municipio:
            continue
        valor = _to_number(row.get(col_valor))
        if valor is None:
            continue
        contexto = []
        for col in [col_bloco, col_grupo, col_acao]:
            if col and str(row.get(col, "")).strip():
                contexto.append(str(row.get(col)).strip())
        indicador = "fns_repasses_federais_total"
        if contexto:
            indicador = "fns_repasses_" + _norm("_".join(contexto)).lower().replace(" ", "_")[:90]
        registros.append({
            "municipio": municipio,
            "ano": int(ano),
            "competencia": str(ano),
            "indicador": indicador,
            "valor": valor,
            "fonte_url": BASE_URL,
            "observacao": "FNS ConsultaFNS - tentativa automatizada de pagamento consolidado",
        })
    if not registros:
        return pd.DataFrame()
    out = pd.DataFrame(registros)
    out = out.groupby(["municipio", "ano", "competencia", "indicador"], as_index=False)["valor"].sum()
    out["fonte_url"] = BASE_URL
    out["observacao"] = "FNS ConsultaFNS - pagamento consolidado por município"
    return out


def _try_known_urls(sess: requests.Session, ano: int) -> tuple[pd.DataFrame, list[str]]:
    detalhes: list[str] = []
    urls = [
        f"{BASE_URL}#/consolidada/0/detalhar",
        f"{BASE_URL}#/consolidada",
        f"{BASE_URL}#/detalhada/acao/pagamento",
        f"{BASE_URL}api/consolidada?uf=MT&ano={ano}",
        f"{BASE_URL}api/pagamento/consolidado?uf=MT&ano={ano}",
        f"{BASE_URL}api/pagamentos/consolidado?uf=MT&ano={ano}",
        f"{BASE_URL}rest/pagamento/consolidado?uf=MT&ano={ano}",
        f"{BASE_URL}consultafns/api/consolidada?uf=MT&ano={ano}",
        f"{BASE_URL}consultafns-service/api/consolidada?uf=MT&ano={ano}",
    ]
    for url in urls:
        try:
            r = sess.get(url, timeout=TIMEOUT)
            ct = r.headers.get("content-type", "")
            detalhes.append(f"{url} | status={r.status_code} | ct={ct}")
            if r.status_code >= 400:
                continue
            if "json" in ct.lower() or r.text.strip().startswith(("{", "[")):
                try:
                    df = _normalizar_fns_dataframe(_df_from_json(r.json()), ano)
                    if not df.empty:
                        return df, detalhes
                except Exception as exc:
                    detalhes.append(f"json_lido_mas_nao_normalizado={exc}")
            if "html" in ct.lower():
                try:
                    tabelas = pd.read_html(r.text)
                except Exception:
                    tabelas = []
                detalhes.append(f"tabelas_html={len(tabelas)}")
                for tabela in tabelas:
                    df = _normalizar_fns_dataframe(tabela, ano)
                    if not df.empty:
                        return df, detalhes
        except Exception as exc:
            detalhes.append(f"{url} | erro={exc}")
    return pd.DataFrame(), detalhes


def _try_discovered_endpoints(sess: requests.Session, ano: int) -> tuple[pd.DataFrame, list[str]]:
    detalhes: list[str] = []
    try:
        r = sess.get(BASE_URL, timeout=TIMEOUT)
        detalhes.append(f"index={r.status_code}; ct={r.headers.get('content-type','')}")
        scripts = _find_scripts(r.text)
        detalhes.append(f"scripts_detectados={len(scripts)}")
    except Exception as exc:
        return pd.DataFrame(), [f"falha_ao_ler_index={exc}"]

    candidatos: list[str] = []
    for script_url in scripts[:12]:
        try:
            js = sess.get(script_url, timeout=TIMEOUT).text
            achados = _candidate_api_paths_from_js(js)
            if achados:
                detalhes.append(f"{script_url} -> candidatos={len(achados)}")
            candidatos.extend(achados)
        except Exception as exc:
            detalhes.append(f"{script_url} -> erro={exc}")

    # Monta URLs absolutas para uma amostra de candidatos.
    seen = set()
    urls = []
    for c in candidatos:
        if c.startswith("http"):
            u = c
        elif c.startswith("/"):
            u = urljoin(BASE_URL, c)
        else:
            u = urljoin(BASE_URL, c)
        if "{" in u or "}" in u:
            continue
        if u not in seen:
            seen.add(u)
            urls.append(u)

    detalhes.append(f"endpoints_candidatos_unicos={len(urls)}")
    for url in urls[:20]:
        # adiciona parâmetros básicos só quando não houver query
        test_url = url
        if "?" not in test_url:
            test_url = f"{test_url}?uf=MT&ano={ano}"
        try:
            resp = sess.get(test_url, timeout=TIMEOUT)
            ct = resp.headers.get("content-type", "")
            detalhes.append(f"{test_url} | status={resp.status_code} | ct={ct}")
            if resp.status_code >= 400:
                continue
            if "json" in ct.lower() or resp.text.strip().startswith(("{", "[")):
                df = _normalizar_fns_dataframe(_df_from_json(resp.json()), ano)
                if not df.empty:
                    return df, detalhes
        except Exception as exc:
            detalhes.append(f"{test_url} | erro={exc}")
    return pd.DataFrame(), detalhes


def carregar_fns_repasses_consolidados_mt(ano: int = 2025) -> pd.DataFrame:
    sess = _session()
    detalhes: list[str] = []

    df, det = _try_known_urls(sess, ano)
    detalhes.extend(det)
    if not df.empty:
        return df

    df, det = _try_discovered_endpoints(sess, ano)
    detalhes.extend(det)
    if not df.empty:
        return df

    resumo = " | ".join(detalhes[-18:])
    raise RuntimeError(
        "Não foi possível importar automaticamente os repasses do FNS nesta tentativa. "
        "O portal ConsultaFNS existe, mas parece depender de aplicação dinâmica/endpoint interno não estável. "
        f"Detalhe: {resumo or 'sem detalhe técnico disponível'}"
    )


def testar_fns_repasses_consolidados_mt(ano: int = 2025) -> dict[str, Any]:
    try:
        df = carregar_fns_repasses_consolidados_mt(ano=ano)
        return {
            "ok": True,
            "ano": ano,
            "linhas": int(len(df)),
            "colunas": list(df.columns),
            "municipios": int(df["municipio"].nunique()) if "municipio" in df.columns else 0,
            "valor_total": float(pd.to_numeric(df.get("valor"), errors="coerce").sum()) if "valor" in df.columns else None,
        }
    except Exception as exc:
        return {"ok": False, "ano": ano, "erro": str(exc)}


if __name__ == "__main__":
    print(json.dumps(testar_fns_repasses_consolidados_mt(), ensure_ascii=False, indent=2))
