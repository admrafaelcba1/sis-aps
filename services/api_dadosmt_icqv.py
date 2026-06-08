from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd
import requests

PAGINAS_INICIAIS = [
    "https://dados.mt.gov.br/produtos/tema/socioeconomico/",
    "https://dados.mt.gov.br/produtos/tema/socioeconomico/subtema/desenvolvimento-regional/",
    "https://www.seplag.mt.gov.br/w/icqv-%C3%ADndice-de-condi%C3%A7%C3%A3o-e-qualidade-de-vida",
]

# Links oficiais conhecidos capturados das páginas públicas. Servem como fallback
# quando a renderização do portal muda ou oculta o href no HTML bruto.
LINKS_POWERBI_FALLBACK = [
    "https://app.powerbi.com/view?r=eyJrIjoiYzEyNDRhM2YtZmM0Yy00MDI3LWEyNmItNGQ2MWRhNjk5ZTMyIiwidCI6IjIxODk0ZWU5LWI1NzctNDNjYS04ZmY1LTQ5YjIzYzVhZDM3NSJ9",
    "https://app.powerbi.com/view?r=eyJrIjoiMGRlOGNmNDctNjI0My00NjM4LWE5NmYtZDMwMTA5YTA5YjJiIiwidCI6ImUzNjU1YzNkLWM4NDEtNGZjMC1iYTYzLTM3ZjI1Y2RhZTkwYiJ9",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PlataformaAPS/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _limpar_html(texto: str) -> str:
    texto = re.sub(r"<script[\s\S]*?</script>", " ", texto, flags=re.I)
    texto = re.sub(r"<style[\s\S]*?</style>", " ", texto, flags=re.I)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def _base64_json_r(url_powerbi: str) -> dict[str, Any]:
    try:
        qs = parse_qs(urlparse(url_powerbi).query)
        r = qs.get("r", [""])[0]
        r = unquote(r)
        if not r:
            return {}
        padding = "=" * ((4 - len(r) % 4) % 4)
        raw = base64.urlsafe_b64decode((r + padding).encode("utf-8"))
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _extrair_powerbi(html: str, origem: str) -> list[dict[str, str]]:
    candidatos: list[dict[str, str]] = []
    padroes = [
        r"https?://app\.powerbi\.com/view\?r=[^\"'<>\s]+",
        r"href=[\"']([^\"']*app\.powerbi\.com/view\?r=[^\"']+)[\"']",
    ]
    for padrao in padroes:
        for m in re.finditer(padrao, html, flags=re.I):
            url = m.group(1) if m.groups() else m.group(0)
            url = url.replace("&amp;", "&").strip()
            ini = max(0, m.start() - 450)
            fim = min(len(html), m.end() + 450)
            contexto_html = html[ini:fim]
            contexto = _limpar_html(contexto_html)
            candidatos.append({"url_powerbi": url, "origem_pagina": origem, "contexto": contexto})
    return candidatos


def _buscar_paginas() -> tuple[list[dict[str, str]], list[str]]:
    candidatos: list[dict[str, str]] = []
    logs: list[str] = []
    for url in PAGINAS_INICIAIS:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=35)
            logs.append(f"{url} -> status={resp.status_code}; ct={resp.headers.get('content-type','')}")
            texto = resp.text or ""
            candidatos.extend(_extrair_powerbi(texto, url))
        except Exception as exc:
            logs.append(f"{url} -> erro={exc}")
    for url in LINKS_POWERBI_FALLBACK:
        candidatos.append({"url_powerbi": url, "origem_pagina": "fallback_oficial_conhecido", "contexto": "Link Power BI oficial conhecido do produto ICQV/Dados MT."})
    # dedup preservando contexto
    vistos = set()
    unicos = []
    for item in candidatos:
        chave = item.get("url_powerbi")
        if chave and chave not in vistos:
            vistos.add(chave)
            unicos.append(item)
    return unicos, logs


def _diagnosticar_powerbi(url_powerbi: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "status_http": None,
        "content_type": "",
        "endpoints_candidatos": "",
        "endpoint_dados_detectado": "Não",
        "observacao": "",
    }
    try:
        resp = requests.get(url_powerbi, headers=HEADERS, timeout=35)
        out["status_http"] = int(resp.status_code)
        out["content_type"] = resp.headers.get("content-type", "")
        html = resp.text or ""
        termos = sorted(set(re.findall(r"https?://[^\"'<>\s]+|/[A-Za-z0-9_./-]*(?:querydata|modelsAndExploration|explore|metadata|reportEmbed)[^\"'<>\s]*", html, flags=re.I)))
        # Não força consumo não documentado. Apenas registra pistas técnicas.
        filtrados = [t[:300] for t in termos if any(p in t.lower() for p in ["querydata", "modelsandexploration", "reportembed", "metadata", "explore"])]
        out["endpoints_candidatos"] = " | ".join(filtrados[:15])
        if filtrados:
            out["endpoint_dados_detectado"] = "Possível"
            out["observacao"] = "A página do Power BI contém pistas de endpoints internos; exige análise específica antes de usar como integração oficial."
        else:
            out["observacao"] = "Power BI carregou, mas sem endpoint de dados claro no HTML inicial. Pode depender de JavaScript e chamadas internas."
    except Exception as exc:
        out["observacao"] = f"Falha ao abrir Power BI: {exc}"
    return out


def carregar_explorador_icqv_dados_mt() -> pd.DataFrame:
    candidatos, logs = _buscar_paginas()
    linhas = []
    agora = datetime.now().isoformat(timespec="seconds")
    for item in candidatos:
        url = item["url_powerbi"]
        payload = _base64_json_r(url)
        diag = _diagnosticar_powerbi(url)
        contexto = item.get("contexto", "")
        produto = "ICQV-MT" if "icqv" in contexto.lower() or url in LINKS_POWERBI_FALLBACK else "Produto Dados MT / Power BI"
        linhas.append({
            "produto": produto,
            "origem_pagina": item.get("origem_pagina", ""),
            "titulo_contexto": "ICQV - MT" if produto == "ICQV-MT" else "Produto Power BI Dados MT",
            "descricao_contexto": contexto[:900],
            "url_powerbi": url,
            "chave_publicacao": payload.get("k", ""),
            "tenant_id": payload.get("t", ""),
            "status_http": diag.get("status_http"),
            "content_type": diag.get("content_type", ""),
            "endpoint_dados_detectado": diag.get("endpoint_dados_detectado", "Não"),
            "endpoints_candidatos": diag.get("endpoints_candidatos", ""),
            "observacao": diag.get("observacao", ""),
            "logs_busca": " | ".join(logs),
            "atualizado_em": agora,
        })
    if not linhas:
        raise RuntimeError("Nenhum link Power BI/ICQV foi encontrado nas páginas Dados MT/SEPLAG pesquisadas.")
    return pd.DataFrame(linhas)


def testar_explorador_icqv_dados_mt() -> dict[str, Any]:
    try:
        df = carregar_explorador_icqv_dados_mt()
        return {
            "ok": True,
            "linhas": int(len(df)),
            "colunas": int(len(df.columns)),
            "produtos": df["produto"].dropna().unique().tolist() if "produto" in df.columns else [],
            "powerbi_status": df[["produto", "status_http", "endpoint_dados_detectado", "observacao"]].to_dict("records") if not df.empty else [],
            "mensagem": "Exploração concluída. Esta etapa identifica o painel ICQV/Power BI; ainda não importa indicadores municipais.",
        }
    except Exception as exc:
        return {"ok": False, "erro": str(exc)}
