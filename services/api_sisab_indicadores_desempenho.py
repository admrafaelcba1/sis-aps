from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

import pandas as pd
import requests
from config.municipios_mt import DEFAULT_MUNICIPIOS

TIMEOUT = 60
BASE_URL = "https://sisab.saude.gov.br"
INDICADORES_URL = f"{BASE_URL}/paginas/acessoRestrito/relatorio/federal/indicadores/indicadorPainel.xhtml"
USER_AGENT = "aps-inteligencia-ses-mt/0.24 (+https://ses.mt.gov.br)"


def _normalizar_texto(valor: Any) -> str:
    return " ".join(str(valor or "").strip().split())


def _chave(valor: Any) -> str:
    import unicodedata

    texto = _normalizar_texto(valor).upper()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    return texto


def _municipios_mt() -> set[str]:
    return {_chave(item.get("municipio")) for item in DEFAULT_MUNICIPIOS if item.get("municipio")}


def _extrair_tabelas_html(html: str) -> list[pd.DataFrame]:
    try:
        return pd.read_html(html)
    except Exception:
        return []


def _eh_tabela_municipal_mt(df: pd.DataFrame) -> bool:
    if df is None or df.empty:
        return False
    colunas = [str(c).lower() for c in df.columns]
    texto_colunas = " ".join(colunas)
    if not any(p in texto_colunas for p in ["munic", "ibge", "uf", "estado"]):
        return False
    municipios = _municipios_mt()
    amostra = df.astype(str).head(300).to_string(index=False)
    encontrados = sum(1 for m in municipios if m in _chave(amostra))
    return encontrados >= 3


def _padronizar_tabela_sisab(df: pd.DataFrame, url_origem: str) -> pd.DataFrame:
    work = df.copy()
    work.columns = [re.sub(r"\s+", "_", str(c).strip().lower()) for c in work.columns]

    municipio_col = None
    for col in work.columns:
        if "munic" in col:
            municipio_col = col
            break
    if not municipio_col:
        # Última tentativa: procura coluna com nomes de municípios de MT.
        municipios = _municipios_mt()
        for col in work.columns:
            valores = work[col].astype(str).head(500).map(_chave)
            if valores.isin(municipios).sum() >= 3:
                municipio_col = col
                break
    if not municipio_col:
        raise RuntimeError("Tabela SISAB localizada, mas sem coluna municipal reconhecível.")

    out = work.copy()
    out["municipio"] = out[municipio_col].astype(str).map(_normalizar_texto)
    out["fonte_url"] = url_origem
    out["fonte_sistema"] = "SISAB_INDICADORES_DESEMPENHO"

    # Mantém a tabela em formato tabular bruto. A importação estruturada genérica
    # transformará as colunas numéricas em indicadores municipais, quando possível.
    return out


def _buscar_links_candidatos(html: str, pagina_url: str) -> list[str]:
    """Extrai links candidatos sem depender de bs4/BeautifulSoup."""
    links: list[str] = []
    if not html:
        return links

    padrao = re.compile(r"""(?:href|src)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
    for match in padrao.finditer(html):
        href = (match.group(1) or "").strip()
        if not href:
            continue
        texto = href.lower()
        if any(p in texto for p in ["csv", "excel", "xls", "ods", "download", "indicador", "desempenho", "relatorio"]):
            links.append(urljoin(pagina_url, href))

    # Remove duplicados preservando ordem.
    vistos: set[str] = set()
    saida: list[str] = []
    for url in links:
        if url not in vistos:
            vistos.add(url)
            saida.append(url)
    return saida


def testar_sisab_indicadores_desempenho_mt() -> dict[str, Any]:
    try:
        df = carregar_sisab_indicadores_desempenho_mt()
        return {"ok": True, "linhas": len(df), "colunas": len(df.columns), "colunas_detectadas": list(df.columns)}
    except Exception as exc:
        return {"ok": False, "erro": str(exc)}


def carregar_sisab_indicadores_desempenho_mt() -> pd.DataFrame:
    """Tenta consumir o relatório público de Indicadores de Desempenho do SISAB.

    Observação técnica: o SISAB disponibiliza visualização e botões de download no
    portal público, mas a tela é JSF/PrimeFaces e pode exigir postback dinâmico.
    Por isso, este conector faz uma tentativa controlada: baixa a página, procura
    tabelas já renderizadas e possíveis links diretos de exportação. Se o dado não
    estiver exposto como HTML/CSV direto, retorna erro claro para evitar raspagem
    frágil no sistema.
    """

    sess = requests.Session()
    sess.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})

    resp = sess.get(INDICADORES_URL, timeout=TIMEOUT)
    resp.raise_for_status()
    html = resp.text or ""
    detalhes: list[str] = []

    tabelas = _extrair_tabelas_html(html)
    for i, tabela in enumerate(tabelas):
        if _eh_tabela_municipal_mt(tabela):
            return _padronizar_tabela_sisab(tabela, f"{INDICADORES_URL}#table_{i}")
    detalhes.append(f"página inicial lida, mas sem tabela municipal renderizada; tabelas_html={len(tabelas)}")

    links = _buscar_links_candidatos(html, INDICADORES_URL)
    for url in links[:20]:
        try:
            r = sess.get(url, timeout=TIMEOUT)
            content_type = r.headers.get("content-type", "").lower()
            texto_ini = (r.text or "")[:200].replace("\n", " ") if "text" in content_type or "html" in content_type or "csv" in content_type else "binário"
            if r.ok and ("csv" in content_type or url.lower().endswith(".csv")):
                from io import StringIO

                for sep in [";", ",", "\t"]:
                    try:
                        tmp = pd.read_csv(StringIO(r.text), sep=sep)
                        if _eh_tabela_municipal_mt(tmp):
                            return _padronizar_tabela_sisab(tmp, url)
                    except Exception:
                        pass
            if r.ok and ("html" in content_type or "text" in content_type):
                for j, tabela in enumerate(_extrair_tabelas_html(r.text)):
                    if _eh_tabela_municipal_mt(tabela):
                        return _padronizar_tabela_sisab(tabela, f"{url}#table_{j}")
            detalhes.append(f"{url} -> sem tabela municipal reconhecível; content-type={content_type}; início={texto_ini[:80]}")
        except Exception as exc:
            detalhes.append(f"{url} -> erro: {exc}")

    raise RuntimeError(
        "Não foi possível importar automaticamente os Indicadores de Desempenho do SISAB. "
        "O relatório público existe e possui opções de download, mas não expôs tabela/CSV direto estável nesta tentativa. "
        "Detalhe: " + " | ".join(detalhes[:8])
    )
