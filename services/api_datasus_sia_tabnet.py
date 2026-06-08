from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from io import StringIO
from typing import Any

import pandas as pd
import requests

from config.municipios_mt import DEFAULT_MUNICIPIOS

# SIA/SUS - Produção Ambulatorial por local de atendimento, Mato Grosso.
# O TABNET não é uma API moderna; por isso usamos múltiplos endpoints/cargas
# e retornamos diagnóstico claro quando a página não entrega tabela HTML estável.
TABNET_URLS = [
    "https://tabnet.datasus.gov.br/cgi/tabcgi.exe?sia/cnv/qamt.def",
    "http://tabnet.datasus.gov.br/cgi/tabcgi.exe?sia/cnv/qamt.def",
    "https://tabnet.datasus.gov.br/cgi/tabcgi.exe?sia/cnv/pamt.def",
    "http://tabnet.datasus.gov.br/cgi/tabcgi.exe?sia/cnv/pamt.def",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (APS-SES-MT; compatível com TABNET/DATASUS)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded",
}


def _chave(texto: Any) -> str:
    s = str(texto or "").strip().upper()
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode("ASCII")
    return re.sub(r"\s+", " ", s)


_MUNICIPIOS_POR_CHAVE = {_chave(m["municipio"]): m for m in DEFAULT_MUNICIPIOS}
_MUNICIPIOS_POR_COD6: dict[str, dict[str, Any]] = {}
for item in DEFAULT_MUNICIPIOS:
    cod = re.sub(r"\D", "", str(item.get("codigo_ibge", "")))
    if len(cod) >= 6:
        _MUNICIPIOS_POR_COD6[cod[:6]] = item


def _numero(valor: Any) -> float | None:
    if valor is None:
        return None
    s = str(valor).replace("\xa0", " ").strip()
    if not s or s in {"-", "...", "nan", "None"}:
        return None
    s = re.sub(r"[^0-9,\.\-]", "", s)
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except Exception:
        return None


def _limpar_nome_municipio(valor: Any) -> tuple[str, str | None]:
    texto = str(valor or "").replace("\xa0", " ").strip()
    codigo = None
    m = re.match(r"^\s*(\d{6,7})\s+(.+?)\s*$", texto)
    if m:
        codigo = m.group(1)[:6]
        texto = m.group(2).strip()
    texto = re.sub(r"\s+-\s+MT$", "", texto, flags=re.I).strip()
    texto = re.sub(r"^Munic[ií]pio\s+", "", texto, flags=re.I).strip()
    return texto, codigo


def _identificar_coluna_municipio(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        if "MUNIC" in _chave(col):
            return col
    return df.columns[0] if len(df.columns) > 0 else None


def _extrair_tabelas(html: str) -> list[pd.DataFrame]:
    try:
        return pd.read_html(StringIO(html), decimal=",", thousands=".")
    except Exception:
        try:
            return pd.read_html(StringIO(html))
        except Exception:
            return []


def _converter_tabela_tabnet(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [" ".join([str(x) for x in tup if str(x) != "nan"]).strip() for tup in df.columns]
    else:
        df.columns = [str(c).strip() for c in df.columns]

    col_mun = _identificar_coluna_municipio(df)
    if not col_mun:
        return pd.DataFrame()

    registros: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        municipio_raw, cod6 = _limpar_nome_municipio(row.get(col_mun))
        if not municipio_raw or _chave(municipio_raw) in {"TOTAL", "IGNORADO", "MUNICIPIO"}:
            continue

        item_oficial = _MUNICIPIOS_POR_COD6.get(cod6) if cod6 else None
        if not item_oficial:
            item_oficial = _MUNICIPIOS_POR_CHAVE.get(_chave(municipio_raw))
        if not item_oficial:
            continue

        municipio = item_oficial["municipio"]
        codigo_ibge = str(item_oficial.get("codigo_ibge", ""))

        for col in df.columns:
            if col == col_mun:
                continue
            nome_col = str(col).strip()
            if not nome_col or _chave(nome_col) == "TOTAL":
                indicador = "producao_ambulatorial_sia_total_periodo"
                ano = None
            else:
                m_ano = re.search(r"(20\d{2}|19\d{2})", nome_col)
                if m_ano:
                    ano = int(m_ano.group(1))
                    indicador = "producao_ambulatorial_sia"
                else:
                    ano = None
                    indicador = "producao_ambulatorial_sia_" + re.sub(r"[^a-z0-9]+", "_", _chave(nome_col).lower()).strip("_")

            valor = _numero(row.get(col))
            if valor is None:
                continue
            registros.append({
                "codigo_ibge": codigo_ibge,
                "municipio": municipio,
                "ano": ano,
                "competencia": str(ano or "periodo_tabnet"),
                "indicador": indicador,
                "valor": valor,
                "fonte": "DATASUS_TABNET_SIA",
                "atualizado_em": datetime.now().isoformat(timespec="seconds"),
            })

    out = pd.DataFrame(registros)
    if out.empty:
        return out
    return out.drop_duplicates(subset=["municipio", "ano", "indicador"], keep="last")


def _payloads(anos_recentes: int = 3) -> list[dict[str, Any]]:
    base = {
        "Arquivos": "Todos",
        "pesqmes1": "",
        "pesqmes2": "",
        "SMunic%EDpio": "TODAS_AS_CATEGORIAS__",
        "SMunicpio": "TODAS_AS_CATEGORIAS__",
    }
    return [
        {**base, "Incremento": "Quantidade aprovada", "Linha": "Município", "Coluna": "Ano atendimento"},
        {**base, "Incremento": "Quantidade aprovada", "Linha": "Município", "Coluna": "Ano processamento"},
        {**base, "Incremento": "Valor aprovado", "Linha": "Município", "Coluna": "Ano atendimento"},
        {**base, "Incremento": "Valor aprovado", "Linha": "Município", "Coluna": "Ano processamento"},
        {**base, "Incremento": "Quantidade apresentada", "Linha": "Município", "Coluna": "Ano atendimento"},
        {**base, "Incremento": "Qtde.aprovada", "Linha": "Município", "Coluna": "Ano atendimento"},
        {**base, "Incremento": "Valor.aprovado", "Linha": "Município", "Coluna": "Ano atendimento"},
    ]


def carregar_sia_producao_ambulatorial_mt(anos_recentes: int = 3) -> pd.DataFrame:
    detalhes: list[str] = []
    for url in TABNET_URLS:
        for payload in _payloads(anos_recentes=anos_recentes):
            try:
                resp = requests.post(url, data=payload, headers=HEADERS, timeout=45)
                detalhes.append(
                    f"{url} | status={resp.status_code} | {payload.get('Incremento')} | {payload.get('Linha')} x {payload.get('Coluna')} | ct={resp.headers.get('content-type','')}"
                )
                if resp.status_code >= 400:
                    continue
                html = resp.text or ""
                if "TabNet" not in html and "Munic" not in html and "Ambulator" not in html and "SIA" not in html:
                    detalhes.append("resposta sem marcações esperadas de TABNET/SIA")
                    continue
                tabelas = _extrair_tabelas(html)
                detalhes.append(f"tabelas_html={len(tabelas)}")
                for tabela in tabelas:
                    out = _converter_tabela_tabnet(tabela)
                    if not out.empty and out["municipio"].nunique() >= 50:
                        return out
            except Exception as exc:
                detalhes.append(f"{url} | erro={exc}")

    resumo = " | ".join(detalhes[-14:]) if detalhes else "sem resposta dos endpoints testados"
    raise RuntimeError(
        "Não foi possível importar automaticamente o SIA/SUS pelo TABNET nesta tentativa. "
        "O DATASUS/TABNET existe, mas o formulário pode exigir parâmetros dinâmicos, nomes internos diferentes ou bloqueio temporário. "
        f"Detalhe: {resumo}"
    )


def testar_sia_producao_ambulatorial_mt(anos_recentes: int = 3) -> dict[str, Any]:
    df = carregar_sia_producao_ambulatorial_mt(anos_recentes=anos_recentes)
    return {
        "ok": True,
        "linhas": int(len(df)),
        "municipios": int(df["municipio"].nunique()) if "municipio" in df.columns else 0,
        "indicadores": sorted(df["indicador"].dropna().unique().tolist()) if "indicador" in df.columns else [],
        "anos": sorted([int(x) for x in df["ano"].dropna().unique().tolist()]) if "ano" in df.columns else [],
    }
