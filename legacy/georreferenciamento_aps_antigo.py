import io
import hashlib
import math
import os
import re
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urljoin
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st

try:
    from utils.cache_dados_aps import filtrar_equipes_ine_aps, salvar_cache_aps, ler_metadata_cache_aps
except Exception:  # fallback para evitar quebra se o módulo ainda não existir
    def filtrar_equipes_ine_aps(df):
        return df
    def salvar_cache_aps(*args, **kwargs):
        return {}
    def ler_metadata_cache_aps():
        return {}

# Versão v19: mantém população real e corrige diagnóstico/carregamento da malha IBGE sem cache.


URL_CNES_ESTABELECIMENTOS_ZIP = "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/CNES/cnes_estabelecimentos_csv.zip"
# Endereços conhecidos do arquivo público de equipes CNES/INE.
# Alguns endpoints do OpenDataSUS/CKAN podem retornar 403 temporariamente; por isso
# o sistema também tenta descobrir recursos via API CKAN antes de desistir.
URL_CNES_EQUIPES_CANDIDATOS = [
    "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/CNES/cnes_equipes_csv.zip",
    "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/CNES/cnes_equipe_csv.zip",
    "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/CNES/cnes_equipes.csv",
    "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/CNES/cnes_equipe.csv",
    "https://s3.sa-east-1.amazonaws.com/ckan-dadosabertos.saude.gov.br/CNES/cnes_equipes_csv.zip",
    "https://s3.sa-east-1.amazonaws.com/ckan-dadosabertos.saude.gov.br/CNES/cnes_equipe_csv.zip",
]


# Caminho 2: CNES/DATASUS oficial. Algumas URLs mudam por competência ou são
# disponibilizadas por página HTML; por isso o sistema tenta descobrir links
# e também aceita upload manual do arquivo EQUIPES BRASIL / base oficial.
URL_CNES_ARQUIVOS_APLICACAO = "https://cnes.datasus.gov.br/pages/downloads/arquivosAplicacao.jsp"
URL_CNES_BASE_DADOS = "https://cnes.datasus.gov.br/pages/downloads/arquivosBaseDados.jsp"
URL_CNES_EQUIPES_OFICIAL_CANDIDATOS = [
    # Candidatos comuns. Nem todos estarão disponíveis; o sistema registra o diagnóstico.
    "https://cnes.datasus.gov.br/pages/downloads/arquivosAplicacao.jsp",
    "https://cnes.datasus.gov.br/pages/downloads/arquivosBaseDados.jsp",
]

URLS_CKAN_DADOS_ABERTOS_SUS = [
    "https://dadosabertos.saude.gov.br/api/3/action/package_search",
    "https://ckan.saude.gov.br/api/3/action/package_search",
    "https://ckan-dadosabertos.saude.gov.br/api/3/action/package_search",
]
UF_MT = "51"
TIPOS_EQUIPE_INE_PRIORITARIOS = {"70", "71", "72", "73", "74", "76"}


URL_IBGE_MUNICIPIOS_MT = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/51/municipios"
URL_SIDRA_POPULACAO_MT_TEMPLATE = "https://apisidra.ibge.gov.br/values/t/6579/n6/in%20n3%2051/v/9324/p/{ano}"


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def _carregar_populacao_sidra_mt_georef(ano: int = 2025) -> pd.DataFrame:
    """Carrega população municipal estimada do SIDRA/IBGE para usar como proxy.

    Esta função não cria população oficial por bairro. Ela apenas permite que a
    pré-base territorial gerada a partir do CNES deixe de ficar zerada quando a
    base municipal consolidada das APIs ainda não tiver sido carregada na sessão.
    """
    url = URL_SIDRA_POPULACAO_MT_TEMPLATE.format(ano=int(ano))
    resp = requests.get(url, timeout=90, headers={"User-Agent": "SES-MT-Georreferenciamento-APS/1.0"})
    resp.raise_for_status()
    dados = resp.json()
    registros: List[Dict[str, Any]] = []
    for item in dados[1:]:
        codigo = str(item.get("D1C", "")).strip()
        municipio = str(item.get("D1N", "")).strip()
        valor = str(item.get("V", "")).replace(".", "").replace(",", ".").strip()
        if not codigo or not municipio or valor in {"", "...", "-"}:
            continue
        try:
            pop = int(round(float(valor)))
        except Exception:
            continue
        registros.append({
            "codigo_ibge": codigo,
            "municipio": municipio,
            "populacao_ibge": pop,
            "populacao": pop,
            "ano_referencia_populacao": int(ano),
            "fonte_populacao": "IBGE/SIDRA - Tabela 6579",
        })
    return pd.DataFrame(registros)


def _carregar_populacao_sidra_mt_fallback() -> pd.DataFrame:
    """Tenta anos recentes do SIDRA para evitar pré-base territorial zerada."""
    erros = []
    for ano in [2025, 2024, 2023, 2022]:
        try:
            df = _carregar_populacao_sidra_mt_georef(ano)
            if isinstance(df, pd.DataFrame) and not df.empty:
                return df
        except Exception as exc:
            erros.append(f"{ano}: {exc}")
    st.session_state["geo_sidra_populacao_erros"] = erros
    return pd.DataFrame()


# -----------------------------------------------------------------------------
# IBGE/Censo 2022 - setores censitários com população real
# -----------------------------------------------------------------------------
# O módulo tenta automatizar a camada de demanda real por setor censitário.
# A estrutura do portal/FTP do IBGE pode mudar; por isso há candidatos de URL
# e também uma tentativa de descoberta de links nas páginas oficiais.
URL_IBGE_CENSO_DOWNLOADS = "https://censo2022.ibge.gov.br/panorama/downloads.html"
URL_IBGE_MALHA_SETORES = "https://www.ibge.gov.br/geociencias/organizacao-do-territorio/estrutura-territorial/26565-malhas-de-setores-censitarios-divisoes-intramunicipais.html"

URL_IBGE_AGREGADOS_SETORES_DIR = "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/Agregados_por_Setor_xlsx/"
URL_IBGE_AGREGADOS_SETORES_CSV_DIR = "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/Agregados_por_Setor_csv/"
URL_IBGE_AGREGADOS_SETORES_MT_CANDIDATOS = [
    # O produto definitivo do Censo 2022 costuma vir como BR e é filtrado por CD_SETOR iniciado em 51.
    "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/Agregados_por_Setor_xlsx/Agregados_por_setores_basico_BR.zip",
    "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/Agregados_por_Setor_xlsx/Agregados_por_setores_Basico_BR.zip",
    "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/Agregados_por_Setor_csv/Agregados_por_setores_basico_BR.zip",
    # Candidatos antigos/alternativos mantidos para compatibilidade caso o IBGE reorganize a pasta.
    "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/Resultados_do_universo/CSV/Agregados_por_setores_censitarios_MT.zip",
    "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/Resultados_do_Universo/CSV/Agregados_por_setores_censitarios_MT.zip",
    "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/Resultados_do_universo/CSV/MT.zip",
    "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/Resultados_do_Universo/CSV/MT.zip",
]

URL_IBGE_MALHA_SETORES_MT_DIR = "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/malha_com_atributos/setores/shp/UF/MT/"
URL_IBGE_MALHA_SETORES_MT_GPKG_DIR = "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/malha_com_atributos/setores/gpkg/UF/MT/"
URL_IBGE_MALHA_SETORES_MT_CANDIDATOS = [
    # v15: URL direta confirmada no FTP do IBGE.
    # A versão anterior tentava primeiro a pasta/diretório, mas o leitor interno
    # tratava a listagem HTML como erro e não chegava ao arquivo ZIP.
    "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/malha_com_atributos/setores/shp/UF/MT/MT_setores_CD2022.zip",
    # Fallback da área de geociências do IBGE.
    "https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/malhas_de_setores_censitarios__divisoes_intramunicipais/censo_2022/setores/shp/UF/MT_setores_CD2022.zip",
    # Pastas oficiais do produto Agregados por Setores Censitários 2022 com malha já compatível com os atributos.
    URL_IBGE_MALHA_SETORES_MT_DIR,
    URL_IBGE_MALHA_SETORES_MT_GPKG_DIR,
    # Candidatos antigos/alternativos mantidos como fallback.
    "https://geoftp.ibge.gov.br/organizacao_do_territorio/estrutura_territorial/malhas_de_setores_censitarios__divisoes_intramunicipais/2022/Malha_de_setores_censitarios_(shp)_por_UFs/MT/MT_setores_CD2022.zip",
    "https://geoftp.ibge.gov.br/organizacao_do_territorio/estrutura_territorial/malhas_de_setores_censitarios__divisoes_intramunicipais/2022/Malha_de_setores_censitarios_(gpkg)_por_UFs/MT/MT_setores_CD2022.gpkg",
]


def _requests_get_bytes(url: str, timeout: int = 180) -> bytes:
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": "SES-MT-Georreferenciamento-APS/1.0"})
    resp.raise_for_status()
    ctype = (resp.headers.get("Content-Type") or "").lower()
    # Evita tratar página HTML como arquivo de dados.
    if "text/html" in ctype and not url.lower().endswith((".html", ".jsp")):
        raise RuntimeError(f"A URL retornou HTML, não arquivo de dados: {url}")
    return resp.content


def _descobrir_links_ibge(paginas: List[str], termos_obrigatorios: List[str], extensoes: Tuple[str, ...]) -> List[str]:
    links: List[str] = []
    for pagina in paginas:
        try:
            # v15: diretórios do FTP do IBGE são páginas HTML de listagem.
            # Para descoberta de links, não usamos _requests_get_bytes porque ela
            # bloqueia HTML propositalmente quando espera arquivo de dados.
            resp = requests.get(pagina, timeout=90, headers={"User-Agent": "SES-MT-Georreferenciamento-APS/1.0"})
            resp.raise_for_status()
            raw = resp.content
            html = raw.decode("utf-8", errors="ignore")
            if "href" not in html.lower():
                html = raw.decode("latin1", errors="ignore")
        except Exception:
            continue
        for href in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.I):
            if href in ("../", "./") or href.lower().startswith(("?", "mailto:")):
                continue
            url = urljoin(pagina if pagina.endswith("/") else pagina + "/", href)
            alvo = url.lower()
            if not any(alvo.endswith(ext.lower()) for ext in extensoes):
                continue
            if all(t.lower() in alvo for t in termos_obrigatorios):
                links.append(url)
    # remove duplicados preservando ordem
    out = []
    for l in links:
        if l not in out:
            out.append(l)
    return out


def _baixar_primeiro_arquivo_disponivel(candidatos: List[str], diagnostico_key: str) -> Tuple[Optional[str], Optional[bytes]]:
    tentativas = []
    expandido: List[str] = []
    for url in candidatos:
        # Se a URL for diretório FTP/HTML, descobre automaticamente arquivos de dados dentro dela.
        if url.endswith("/") or url.lower().endswith((".html", ".jsp")):
            ext = (".zip", ".xlsx", ".csv", ".gpkg")
            termos = []
            if "malha" in url.lower() or "shp" in url.lower() or "gpkg" in url.lower():
                termos = []
            elif "setor" in url.lower():
                termos = ["basico"]
            achados = _descobrir_links_ibge([url], termos, ext)
            # Prioriza básico e arquivos de MT, quando houver.
            achados = sorted(achados, key=lambda x: (0 if "basico" in x.lower() else 1, 0 if "/mt" in x.lower() or "_mt" in x.lower() else 1, x))
            expandido.extend(achados)
        else:
            expandido.append(url)
    # remove duplicados preservando ordem
    vistos = []
    for u in expandido:
        if u not in vistos:
            vistos.append(u)
    for url in vistos:
        try:
            conteudo = _requests_get_bytes(url, timeout=240)
            if conteudo and not (b"<html" in conteudo[:500].lower() and not url.lower().endswith((".html", ".jsp"))):
                st.session_state[diagnostico_key] = {"ok": True, "url": url, "tentativas": tentativas}
                return url, conteudo
        except Exception as exc:
            tentativas.append({"url": url, "erro": str(exc)[:500]})
    st.session_state[diagnostico_key] = {"ok": False, "url": None, "tentativas": tentativas}
    return None, None


def _ler_csv_zip_generico(conteudo_zip: bytes, max_arquivos: int = 20) -> List[Tuple[str, pd.DataFrame]]:
    dfs: List[Tuple[str, pd.DataFrame]] = []
    # Arquivo XLSX direto, sem ZIP.
    if not zipfile.is_zipfile(io.BytesIO(conteudo_zip)):
        try:
            df = pd.read_excel(io.BytesIO(conteudo_zip), dtype=str)
            if df is not None and not df.empty:
                dfs.append(("arquivo_xlsx_direto.xlsx", df))
        except Exception:
            pass
        return dfs
    with zipfile.ZipFile(io.BytesIO(conteudo_zip)) as zf:
        nomes = [n for n in zf.namelist() if n.lower().endswith((".csv", ".txt", ".xlsx", ".xls"))]
        for nome in nomes[:max_arquivos]:
            raw = zf.read(nome)
            if nome.lower().endswith((".xlsx", ".xls")):
                try:
                    df = pd.read_excel(io.BytesIO(raw), dtype=str)
                    if df is not None and not df.empty:
                        dfs.append((nome, df))
                        continue
                except Exception:
                    pass
            for enc in ["latin1", "utf-8", "cp1252"]:
                try:
                    texto = raw.decode(enc, errors="replace")
                    # A maioria dos produtos IBGE vem com ;, mas há variações.
                    sep = ";" if texto[:10000].count(";") >= texto[:10000].count(",") else ","
                    df = pd.read_csv(io.StringIO(texto), sep=sep, dtype=str, low_memory=False)
                    if df is not None and not df.empty:
                        dfs.append((nome, df))
                        break
                except Exception:
                    continue
    return dfs


def _inferir_coluna_setor(df: pd.DataFrame) -> Optional[str]:
    cols = list(df.columns)

    # No arquivo nacional BR do IBGE, as primeiras linhas podem ser de outras UFs.
    # Por isso, não podemos exigir que a amostra inicial comece com 51/MT.
    # Se a coluna se chama CD_SETOR/cd_setor, ela deve prevalecer diretamente.
    for col in cols:
        c = str(col).lower().strip()
        if c in ["cd_setor", "cod_setor", "codigo_setor", "codigo_do_setor", "setor_censitario", "setor"]:
            return col

    candidatos_nome = [c for c in cols if any(k in str(c).lower() for k in ["cd_setor", "cod_setor", "codigo_setor", "geocod", "setor"])]
    candidatos = candidatos_nome + cols
    for col in candidatos:
        s = df[col].astype(str).str.replace(r"\D", "", regex=True)
        amostra = s[s.str.len().between(12, 20)].head(1000)
        if len(amostra) >= 5:
            # Aceita colunas nacionais BR mesmo que a amostra inicial não seja de MT.
            if amostra.str.startswith("51").sum() >= 1 or str(col).lower() in ["cd_setor", "cod_setor"]:
                return col
    return None


def _inferir_coluna_populacao(df: pd.DataFrame) -> Optional[str]:
    cols = list(df.columns)
    nomes_preferidos = [
        "v00001", "v0001", "total_de_pessoas", "total_pessoas", "pessoas", "populacao", "população", "moradores", "residentes", "pop_residente"
    ]
    for col in cols:
        c = str(col).lower()
        if any(k in c for k in nomes_preferidos):
            valores = pd.to_numeric(df[col].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False), errors="coerce")
            if valores.notna().sum() > 10 and valores.max(skipna=True) > 0:
                return col
    # fallback: primeira coluna numérica plausível que não seja código/setor
    for col in cols:
        c = str(col).lower()
        if any(k in c for k in ["setor", "cod", "geocod", "uf", "mun"]):
            continue
        valores = pd.to_numeric(df[col].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False), errors="coerce")
        if valores.notna().sum() > 10 and 0 < valores.quantile(0.95) < 20000:
            return col
    return None


def _extrair_populacao_real_setores_de_zip(conteudo_zip: bytes) -> pd.DataFrame:
    tabelas = _ler_csv_zip_generico(conteudo_zip)
    st.session_state["geo_ibge_setores_pop_parse_diagnostico"] = {
        "arquivos_lidos_no_zip": [nome for nome, _ in tabelas[:30]],
        "total_tabelas_lidas": len(tabelas),
        "observacao": "A v17 reconhece cd_setor/CD_SETOR mesmo quando o arquivo nacional BR começa por outras UFs e depois filtra MT pelo prefixo 51."
    }
    mapa_nome, mapa_codigo7 = _mapas_municipios_mt_ibge()
    melhores: List[pd.DataFrame] = []
    tentativas_parse: List[Dict[str, Any]] = []
    for nome, df0 in tabelas:
        df = _normalizar_colunas(df0)
        col_setor = _inferir_coluna_setor(df)
        col_pop = _inferir_coluna_populacao(df)
        tentativas_parse.append({
            "arquivo": nome,
            "linhas": int(len(df)),
            "colunas_amostra": list(map(str, df.columns[:20])),
            "coluna_setor_detectada": str(col_setor) if col_setor else None,
            "coluna_populacao_detectada": str(col_pop) if col_pop else None,
        })
        if not col_setor or not col_pop:
            continue
        setor = df[col_setor].astype(str).str.replace(r"\D", "", regex=True)
        pop = pd.to_numeric(df[col_pop].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False), errors="coerce").fillna(0)
        tmp = pd.DataFrame({
            "setor_censitario": setor,
            "populacao": pop.round().astype(int),
        })
        tmp = tmp[(tmp["setor_censitario"].str.startswith("51")) & (tmp["setor_censitario"].str.len() >= 12) & (tmp["populacao"] >= 0)]
        if tmp.empty:
            continue
        tmp["codigo_ibge"] = tmp["setor_censitario"].str[:7]
        tmp["municipio"] = tmp["codigo_ibge"].map(mapa_nome).fillna("")
        tmp["bairro_ou_localidade"] = "Setor " + tmp["setor_censitario"].astype(str)
        tmp["tipo_territorio"] = "Setor censitário IBGE"
        tmp["fonte_populacao"] = f"IBGE/Censo 2022 - Agregados por Setores Censitários ({nome})"
        tmp["observacao_validacao"] = "População real por setor censitário. Coordenadas dependem da malha IBGE 2022."
        melhores.append(tmp)
    st.session_state["geo_ibge_setores_pop_parse_tentativas"] = tentativas_parse[:20]
    if not melhores:
        st.session_state["geo_ibge_setores_pop_erro_processamento"] = (
            "Arquivo baixado, mas ainda não foi possível identificar simultaneamente cd_setor/CD_SETOR e V00001/V0001 com setores de MT."
        )
        return pd.DataFrame()
    # escolhe a tabela com mais setores de MT.
    out = sorted(melhores, key=lambda x: len(x), reverse=True)[0]
    return out.drop_duplicates(subset=["setor_censitario"]).reset_index(drop=True)


@st.cache_data(ttl=60 * 60 * 24 * 30, show_spinner=False)
def carregar_populacao_real_setores_ibge_2022_mt() -> pd.DataFrame:
    candidatos = list(URL_IBGE_AGREGADOS_SETORES_MT_CANDIDATOS)
    # Tenta descobrir links adicionais nas páginas oficiais.
    descobertos = _descobrir_links_ibge(
        [URL_IBGE_AGREGADOS_SETORES_DIR, URL_IBGE_AGREGADOS_SETORES_CSV_DIR, URL_IBGE_CENSO_DOWNLOADS],
        termos_obrigatorios=["basico"],
        extensoes=(".zip", ".csv", ".xlsx"),
    )
    for url in descobertos:
        # O arquivo BR é aceito porque será filtrado por setores iniciados em 51.
        if ("br" in url.lower() or "mt" in url.lower() or "mato" in url.lower()) and url not in candidatos:
            candidatos.append(url)
    url, conteudo = _baixar_primeiro_arquivo_disponivel(candidatos, "geo_ibge_setores_pop_diagnostico")
    if not conteudo:
        return pd.DataFrame()
    try:
        df = _extrair_populacao_real_setores_de_zip(conteudo)
        if not df.empty:
            df["url_fonte_populacao"] = url
        return df
    except Exception as exc:
        st.session_state["geo_ibge_setores_pop_erro_processamento"] = str(exc)
        return pd.DataFrame()


def _inferir_coluna_setor_malha(gdf) -> Optional[str]:
    cols = list(gdf.columns)
    for col in cols:
        c = str(col).lower()
        if any(k in c for k in ["cd_setor", "cod_setor", "cd_geocodi", "geocodigo", "setor"]):
            return col
    return _inferir_coluna_setor(pd.DataFrame(gdf.drop(columns=["geometry"], errors="ignore")))


def carregar_centroides_setores_ibge_2022_mt(versao_cache: str = "v19_malha_sem_cache_diagnostico") -> pd.DataFrame:
    """Carrega a malha dos setores censitários de MT e calcula centroides.

    v18: força quebra de cache e melhora o diagnóstico. A população já vem do
    arquivo Agregados_por_setores_basico_BR.zip; aqui o objetivo é baixar a
    malha oficial para obter latitude/longitude dos setores e permitir cálculo
    de distância até a UBS mais próxima.
    """
    try:
        import geopandas as gpd  # type: ignore
    except Exception as exc:
        st.session_state["geo_ibge_malha_erro_dependencia"] = str(exc)
        st.session_state["geo_ibge_malha_diagnostico"] = {
            "ok": False,
            "fase": "importar_geopandas",
            "erro": str(exc),
            "orientacao": "Instale/atualize geopandas, pyogrio e shapely para ler a malha dos setores."
        }
        return pd.DataFrame()

    candidatos = [
        "https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/malhas_de_setores_censitarios__divisoes_intramunicipais/censo_2022/setores/shp/UF/MT_setores_CD2022.zip",
        "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/malha_com_atributos/setores/shp/UF/MT/MT_setores_CD2022.zip",
        "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/malha_com_atributos/setores/gpkg/UF/MT/MT_setores_CD2022.gpkg",
        "https://geoftp.ibge.gov.br/organizacao_do_territorio/estrutura_territorial/malhas_de_setores_censitarios__divisoes_intramunicipais/2022/Malha_de_setores_censitarios_(shp)_por_UFs/MT/MT_setores_CD2022.zip",
        "https://geoftp.ibge.gov.br/organizacao_do_territorio/estrutura_territorial/malhas_de_setores_censitarios__divisoes_intramunicipais/2022/Malha_de_setores_censitarios_(gpkg)_por_UFs/MT/MT_setores_CD2022.gpkg",
    ]

    # Inicializa diagnóstico antes de qualquer download. Nas versões anteriores,
    # quando a descoberta de links falhava antes do loop, a tela mostrava apenas {}.
    st.session_state["geo_ibge_malha_diagnostico"] = {
        "ok": False,
        "fase": "preparando_urls",
        "observacao": "v19: malha sem cache; tentativa direta no FTP oficial do IBGE e descoberta complementar de links.",
        "candidatos_iniciais": candidatos,
    }

    tentativas = []
    try:
        descobertos = _descobrir_links_ibge(
            [URL_IBGE_MALHA_SETORES_MT_DIR, URL_IBGE_MALHA_SETORES_MT_GPKG_DIR, URL_IBGE_MALHA_SETORES],
            termos_obrigatorios=[],
            extensoes=(".zip", ".gpkg"),
        )
        for url in descobertos:
            if ("mt" in url.lower() or "setores" in url.lower()) and url not in candidatos:
                candidatos.append(url)
    except Exception as exc_desc:
        tentativas.append({"fase": "descobrir_links_ibge", "erro": str(exc_desc)[:800]})
        st.session_state["geo_ibge_malha_diagnostico"] = {
            "ok": False,
            "fase": "descoberta_de_links_falhou_mas_vai_tentar_urls_diretas",
            "candidatos_iniciais": candidatos,
            "tentativas": tentativas,
        }
    for url in candidatos:
        try:
            resp = requests.get(url, timeout=300, headers={"User-Agent": "Mozilla/5.0 SES-MT-Georreferenciamento-APS/1.0"})
            ctype = (resp.headers.get("Content-Type") or "").lower()
            tamanho = len(resp.content or b"")
            info = {"url": url, "status": resp.status_code, "content_type": ctype, "bytes": tamanho}
            if resp.status_code != 200:
                info["erro"] = f"HTTP {resp.status_code}"
                tentativas.append(info)
                continue
            if b"<html" in (resp.content or b"")[:500].lower():
                info["erro"] = "retornou HTML/listagem, não arquivo de malha"
                tentativas.append(info)
                continue

            with tempfile.TemporaryDirectory() as td:
                td_path = Path(td)
                conteudo = resp.content
                if url.lower().endswith(".gpkg"):
                    arq = td_path / "MT_setores_CD2022.gpkg"
                    arq.write_bytes(conteudo)
                    gdf = gpd.read_file(arq)
                elif zipfile.is_zipfile(io.BytesIO(conteudo)):
                    with zipfile.ZipFile(io.BytesIO(conteudo)) as zf:
                        nomes_zip = zf.namelist()[:30]
                        zf.extractall(td_path)
                    arquivos = list(td_path.rglob("*.shp")) + list(td_path.rglob("*.gpkg"))
                    if not arquivos:
                        info["erro"] = "ZIP baixado, mas sem SHP/GPKG dentro"
                        info["nomes_zip_amostra"] = nomes_zip
                        tentativas.append(info)
                        continue
                    arquivos = sorted(arquivos, key=lambda p: (0 if "setor" in p.name.lower() else 1, p.name.lower()))
                    info["arquivo_geografico_lido"] = str(arquivos[0].name)
                    gdf = gpd.read_file(arquivos[0])
                else:
                    info["erro"] = "arquivo baixado não é ZIP nem GPKG"
                    tentativas.append(info)
                    continue

                info["linhas_malha"] = int(len(gdf))
                info["colunas_malha_amostra"] = [str(c) for c in list(gdf.columns)[:25]]
                if gdf.empty or "geometry" not in gdf.columns:
                    info["erro"] = "malha vazia ou sem geometria"
                    tentativas.append(info)
                    continue

                col_setor = _inferir_coluna_setor_malha(gdf)
                info["coluna_setor_detectada"] = str(col_setor) if col_setor else None
                if not col_setor:
                    info["erro"] = "não identificou coluna do setor na malha"
                    tentativas.append(info)
                    continue

                try:
                    if gdf.crs is None:
                        gdf = gdf.set_crs(4674, allow_override=True)
                    gdf_m = gdf.to_crs(5880)
                    cent = gdf_m.geometry.centroid
                    cent = gpd.GeoSeries(cent, crs=gdf_m.crs).to_crs(4326)
                except Exception as exc_cent:
                    info["aviso_centroide"] = f"centroide em CRS original: {exc_cent}"
                    try:
                        gdf = gdf.to_crs(4326)
                    except Exception:
                        pass
                    cent = gdf.geometry.centroid

                setor = gdf[col_setor].astype(str).str.replace(r"\D", "", regex=True).str.zfill(15)
                out = pd.DataFrame({
                    "setor_censitario": setor,
                    "latitude": cent.y,
                    "longitude": cent.x,
                })
                out = out[(out["setor_censitario"].str.startswith("51")) & (out["setor_censitario"].str.len() >= 12)]
                out = out.dropna(subset=["latitude", "longitude"])
                out["url_fonte_malha"] = url
                out = out.drop_duplicates(subset=["setor_censitario"]).reset_index(drop=True)

                info["setores_mt_com_centroide"] = int(len(out))
                if not out.empty:
                    st.session_state["geo_ibge_malha_diagnostico"] = {"ok": True, "url": url, "tentativas": tentativas + [info]}
                    st.session_state.pop("geo_ibge_malha_erro_processamento", None)
                    st.session_state.pop("geo_ibge_malha_erro_dependencia", None)
                    return out
                info["erro"] = "malha lida, mas sem setores MT após filtro 51"
                tentativas.append(info)
        except Exception as exc:
            tentativas.append({"url": url, "erro": str(exc)[:800]})

    st.session_state["geo_ibge_malha_diagnostico"] = {
        "ok": False,
        "url": None,
        "candidatos_testados": candidatos,
        "tentativas": tentativas,
        "orientacao": "Se todos os links falharem, verifique se geopandas/pyogrio/shapely estão instalados e se o ambiente consegue acessar o FTP do IBGE."
    }
    return pd.DataFrame()


def carregar_setores_ibge_2022_mt_completo() -> pd.DataFrame:
    pop = carregar_populacao_real_setores_ibge_2022_mt()
    if pop.empty:
        return pd.DataFrame()
    malha = carregar_centroides_setores_ibge_2022_mt()
    if not malha.empty:
        out = pop.merge(malha, on="setor_censitario", how="left")
    else:
        out = pop.copy()
        out["latitude"] = 0.0
        out["longitude"] = 0.0
        out["observacao_validacao"] = out.get("observacao_validacao", "").astype(str) + " Malha/centroide não carregado automaticamente."
    # Garante colunas usadas pelo cálculo.
    for col, val in {
        "renda_media": 0,
        "percentual_baixa_renda": 0,
        "percentual_bolsa_familia": 0,
        "percentual_cadunico": 0,
        "percentual_bpc": 0,
        "percentual_baixa_escolaridade": 0,
        "percentual_saneamento_inadequado": 0,
        "percentual_rural": 0,
        "indicador_pressao_aps": 0,
        "percentual_plano_saude_estimado": 0,
    }.items():
        if col not in out.columns:
            out[col] = val
    out["fonte_populacao"] = out.get("fonte_populacao", "IBGE/Censo 2022 - Setores Censitários")
    return out.reset_index(drop=True)

@st.cache_data(ttl=60 * 60 * 24 * 30, show_spinner=False)
def _mapas_municipios_mt_ibge() -> Tuple[Dict[str, str], Dict[str, str]]:
    """Retorna dois mapas: codigo -> nome e codigo -> codigo_ibge_7.

    O CNES pode trazer município em formatos diferentes: 7 dígitos IBGE
    (ex.: 5103403) ou 6 dígitos DATASUS/IBGE sem dígito final (ex.: 510340).
    Por isso o mapa registra as duas chaves.
    """
    mapa_nome: Dict[str, str] = {}
    mapa_codigo7: Dict[str, str] = {}
    try:
        resp = requests.get(
            URL_IBGE_MUNICIPIOS_MT,
            timeout=60,
            headers={"User-Agent": "SES-MT-Georreferenciamento-APS/1.0"},
        )
        resp.raise_for_status()
        dados = resp.json()
        for item in dados:
            cod7 = str(item.get("id", "")).strip()
            nome = str(item.get("nome", "")).strip()
            if not cod7 or not nome:
                continue
            for chave in {cod7, cod7[:6]}:
                mapa_nome[chave] = nome
                mapa_codigo7[chave] = cod7
    except Exception:
        # Fallback mínimo para não quebrar a tela se o IBGE estiver indisponível.
        # A lista completa é carregada automaticamente quando a API IBGE responde.
        fallback = {
            "5103403": "Cuiabá", "510340": "Cuiabá",
            "5108402": "Várzea Grande", "510840": "Várzea Grande",
            "5102702": "Canarana", "510270": "Canarana",
            "5107602": "Rondonópolis", "510760": "Rondonópolis",
            "5102504": "Cáceres", "510250": "Cáceres",
            "5107909": "Sinop", "510790": "Sinop",
            "5107958": "Tangará da Serra", "510795": "Tangará da Serra",
            "5101803": "Barra do Garças", "510180": "Barra do Garças",
            "5107040": "Primavera do Leste", "510704": "Primavera do Leste",
            "5105259": "Lucas do Rio Verde", "510525": "Lucas do Rio Verde",
            "5106224": "Nova Mutum", "510622": "Nova Mutum",
        }
        for chave, nome in fallback.items():
            mapa_nome[chave] = nome
            mapa_codigo7[chave] = chave
    return mapa_nome, mapa_codigo7


def _extrair_codigo_municipio(valor: Any) -> str:
    texto = str(valor or "").strip()
    m = re.search(r"(\d{6,7})", texto)
    return m.group(1) if m else ""


def _enriquecer_municipio_por_codigo(df: pd.DataFrame, coluna_codigo: str = "codigo_ibge", coluna_municipio: str = "municipio") -> pd.DataFrame:
    """Completa/normaliza município pelo código IBGE.

    Observação importante: em algumas versões do CNES a coluna detectada como
    município vem, na prática, com o BAIRRO do estabelecimento. Por isso, quando
    houver código IBGE válido, o nome do município deve prevalecer sobre qualquer
    texto capturado automaticamente. O bairro continua sendo preservado na coluna
    própria `bairro`.
    """
    out = df.copy()
    mapa_nome, mapa_codigo7 = _mapas_municipios_mt_ibge()
    if coluna_codigo not in out.columns:
        out[coluna_codigo] = ""

    cod_extraido = out[coluna_codigo].map(_extrair_codigo_municipio)
    cod7 = cod_extraido.map(lambda c: mapa_codigo7.get(c, c)).astype(str)
    out[coluna_codigo] = cod7

    if coluna_municipio not in out.columns:
        out[coluna_municipio] = ""

    municipio_map = cod_extraido.map(lambda c: mapa_nome.get(c, ""))
    tem_mapa = municipio_map.astype(str).str.strip().ne("")

    # Regra v5: se o código IBGE for reconhecido, o nome oficial do município
    # substitui o texto detectado. Isso corrige casos em que a coluna do CNES
    # traz bairro/localidade no lugar de município.
    out.loc[tem_mapa, coluna_municipio] = municipio_map[tem_mapa].values

    return out


COLUNAS_UBS_MODELO = [
    "municipio",
    "codigo_ibge",
    "cnes",
    "nome_unidade",
    "tipo_unidade",
    "bairro",
    "logradouro",
    "latitude",
    "longitude",
    "qtd_esf",
    "qtd_esb",
    "ines_vinculados",
    "fonte",
    "observacao_validacao",
]

COLUNAS_TERRITORIOS_MODELO = [
    "municipio",
    "codigo_ibge",
    "bairro_ou_localidade",
    "tipo_territorio",
    "populacao",
    "latitude",
    "longitude",
    "renda_media",
    "percentual_baixa_renda",
    "percentual_bolsa_familia",
    "percentual_cadunico",
    "percentual_bpc",
    "percentual_baixa_escolaridade",
    "percentual_saneamento_inadequado",
    "percentual_rural",
    "indicador_pressao_aps",
    "percentual_plano_saude_estimado",
    "ubs_referencia",
    "distancia_ubs_km",
    "observacao_validacao",
]


def _normalizar_texto(valor: Any) -> str:
    texto = str(valor or "").strip().lower()
    mapa = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçñ", "aaaaaeeeeiiiiooooouuuucn")
    texto = texto.translate(mapa)
    texto = re.sub(r"[^a-z0-9]+", "_", texto)
    texto = re.sub(r"_+", "_", texto).strip("_")
    return texto


def _normalizar_colunas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [_normalizar_texto(c) for c in df.columns]
    return df


def _identificar_coluna(df: pd.DataFrame, candidatos: List[str]) -> Optional[str]:
    if df is None or df.empty:
        return None
    mapa = {_normalizar_texto(c): c for c in df.columns}
    for candidato in candidatos:
        chave = _normalizar_texto(candidato)
        if chave in mapa:
            return mapa[chave]
    # segunda tentativa: contém todos os termos do candidato
    for candidato in candidatos:
        termos = [t for t in _normalizar_texto(candidato).split("_") if t]
        for chave, original in mapa.items():
            if all(t in chave for t in termos):
                return original
    return None


def _to_numero(valor: Any, padrao: Optional[float] = 0.0) -> Optional[float]:
    if valor is None:
        return padrao
    try:
        if pd.isna(valor):
            return padrao
    except Exception:
        pass
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip()
    if texto == "" or texto.lower() in {"nan", "none", "null", "<na>"}:
        return padrao
    texto = texto.replace("%", "").replace(" ", "")
    # trata padrão brasileiro 1.234,56
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    else:
        texto = texto.replace(",", ".")
    texto = re.sub(r"[^0-9.\-]", "", texto)
    try:
        return float(texto)
    except Exception:
        return padrao


def _clamp(valor: Any, minimo: float = 0.0, maximo: float = 100.0) -> float:
    num = _to_numero(valor, 0.0)
    if num is None:
        num = 0.0
    return max(minimo, min(maximo, float(num)))


def _ler_upload(uploaded_file) -> pd.DataFrame:
    nome = uploaded_file.name.lower()
    if nome.endswith(".csv"):
        ultimo_erro = None
        for enc in ["utf-8-sig", "utf-8", "latin1", "cp1252"]:
            try:
                uploaded_file.seek(0)
                return pd.read_csv(uploaded_file, sep=None, engine="python", encoding=enc, dtype=str)
            except Exception as exc:
                ultimo_erro = exc
        raise ValueError(f"Não foi possível ler o CSV: {ultimo_erro}")
    return pd.read_excel(uploaded_file, dtype=str)


def _excel_bytes(dfs: Dict[str, pd.DataFrame]) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for nome, df in dfs.items():
            df.to_excel(writer, sheet_name=nome[:31], index=False)
    buffer.seek(0)
    return buffer.getvalue()


@st.cache_data(ttl=60 * 60 * 8, show_spinner=False)

def _detectar_coluna_codigo_municipio_por_conteudo(df: pd.DataFrame) -> Optional[str]:
    """Detecta coluna de código municipal por conteúdo, mesmo quando o nome da coluna vem fora do padrão.

    Algumas versões públicas do CNES trazem o município em campos com nomes pouco previsíveis.
    Esta função procura uma coluna cujo conteúdo tenha códigos de 6 ou 7 dígitos iniciando por 51.
    """
    melhor_coluna = None
    melhor_score = 0
    total = max(len(df), 1)
    for col in df.columns:
        serie = df[col].dropna().astype(str).str.strip()
        if serie.empty:
            continue
        amostra = serie.head(5000)
        codigos = amostra.str.extract(r"(51\d{4,5})")[0].dropna()
        score = len(codigos)
        nome_col = _texto_busca(col)
        if "mun" in nome_col or "ibge" in nome_col or "gestor" in nome_col:
            score += 50
        if score > melhor_score and len(codigos) >= max(3, min(20, int(len(amostra) * 0.05))):
            melhor_score = score
            melhor_coluna = col
    return melhor_coluna


def _detectar_coluna_nome_municipio_por_conteudo(df: pd.DataFrame) -> Optional[str]:
    """Detecta coluna de nome municipal por conteúdo usando a lista de municípios de MT do IBGE."""
    mapa_nome, _ = _mapas_municipios_mt_ibge()
    nomes = {_texto_busca(v) for v in mapa_nome.values() if v}
    if not nomes:
        return None
    melhor_coluna = None
    melhor_score = 0
    for col in df.columns:
        serie = df[col].dropna().astype(str).str.strip()
        if serie.empty:
            continue
        amostra = serie.head(5000).map(_texto_busca)
        score = int(amostra.isin(nomes).sum())
        nome_col = _texto_busca(col)
        if "mun" in nome_col:
            score += 50
        if score > melhor_score and score >= 3:
            melhor_score = score
            melhor_coluna = col
    return melhor_coluna


def _ler_csv_bytes(bruto: bytes) -> pd.DataFrame:
    ultimo_erro = None
    for enc in ["utf-8-sig", "utf-8", "latin1", "cp1252"]:
        for sep in [None, ";", ",", "|"]:
            try:
                return pd.read_csv(
                    io.BytesIO(bruto),
                    sep=sep,
                    engine="python",
                    dtype=str,
                    encoding=enc,
                    on_bad_lines="skip",
                )
            except Exception as exc:
                ultimo_erro = exc
    raise ValueError(f"Não foi possível ler o CSV: {ultimo_erro}")


def _baixar_csv_zip(url: str, timeout: int = 120) -> pd.DataFrame:
    resposta = requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": "Mozilla/5.0 SES-MT-Georreferenciamento-APS/1.0",
            "Accept": "application/zip,text/csv,application/octet-stream,*/*",
            "Referer": "https://dadosabertos.saude.gov.br/",
        },
    )
    resposta.raise_for_status()

    content_type = resposta.headers.get("content-type", "").lower()
    conteudo = resposta.content
    url_lower = url.lower()

    # Alguns recursos do CKAN podem ser CSV direto; outros vêm zipados.
    if url_lower.endswith(".csv") or "text/csv" in content_type:
        return _ler_csv_bytes(conteudo)

    with zipfile.ZipFile(io.BytesIO(conteudo)) as zf:
        nomes = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not nomes:
            raise ValueError("O ZIP baixado não contém CSV.")
        nome_csv = max(nomes, key=lambda n: zf.getinfo(n).file_size)
        bruto = zf.read(nome_csv)

    return _ler_csv_bytes(bruto)


def _descobrir_urls_equipes_ine_ckan() -> List[str]:
    """Tenta descobrir recursos de equipes CNES/INE via API CKAN do Dados Abertos SUS.

    O endpoint S3 pode bloquear acesso direto. A busca CKAN aumenta a chance de
    encontrar uma URL atualizada do mesmo recurso sem depender de caminho fixo.
    """
    urls: List[str] = []
    consultas = [
        "CNES equipes",
        "CNES equipe",
        "cnes_equipes",
        "cnes equipe csv",
        "equipes CNES INE",
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 SES-MT-Georreferenciamento-APS/1.0",
        "Accept": "application/json,*/*",
    }
    for api in URLS_CKAN_DADOS_ABERTOS_SUS:
        for q in consultas:
            try:
                resp = requests.get(api, params={"q": q, "rows": 20}, timeout=45, headers=headers)
                resp.raise_for_status()
                payload = resp.json()
                resultados = (payload.get("result") or {}).get("results") or []
            except Exception:
                continue
            for pacote in resultados:
                texto_pacote = _texto_busca(" ".join([
                    str(pacote.get("title", "")),
                    str(pacote.get("name", "")),
                    str(pacote.get("notes", "")),
                ]))
                for recurso in pacote.get("resources") or []:
                    url = str(recurso.get("url") or recurso.get("download_url") or "").strip()
                    if not url:
                        continue
                    nome = _texto_busca(" ".join([
                        str(recurso.get("name", "")),
                        str(recurso.get("description", "")),
                        str(recurso.get("format", "")),
                        texto_pacote,
                    ]))
                    url_busca = _texto_busca(url)
                    tem_cnes = "cnes" in nome or "cnes" in url_busca
                    tem_equipe = any(t in nome or t in url_busca for t in ["equipe", "equipes", "ine"])
                    eh_csv_zip = url.lower().endswith((".zip", ".csv")) or any(t in nome for t in ["csv", "zip"])
                    if tem_cnes and tem_equipe and eh_csv_zip:
                        urls.append(url)
    # Preserva ordem e remove duplicados.
    vistos = set()
    unicas = []
    for u in urls:
        if u not in vistos:
            vistos.add(u)
            unicas.append(u)
    return unicas


def _serie(df: pd.DataFrame, coluna: Optional[str], padrao: Any = "") -> pd.Series:
    if coluna and coluna in df.columns:
        return df[coluna]
    return pd.Series([padrao] * len(df), index=df.index)


def _texto_busca(valor: Any) -> str:
    texto = str(valor or "").strip().lower()
    mapa = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçñ", "aaaaaeeeeiiiiooooouuuucn")
    texto = texto.translate(mapa)
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _classificar_unidade_aps(tipo: Any, subtipo: Any, nome: Any) -> str:
    texto = _texto_busca(f"{tipo} {subtipo} {nome}")
    if any(t in texto for t in ["saude da familia", "unidade de saude da familia", "estrategia saude da familia", "usf"]):
        return "USF / Saúde da Família"
    if any(t in texto for t in ["unidade basica", "ubs", "centro de saude"]):
        return "UBS / Centro de Saúde"
    if "posto de saude" in texto:
        return "Posto de Saúde"
    return "Unidade APS compatível"


def _latlon_valida(lat: Any, lon: Any) -> bool:
    latn = _to_numero(lat, None)
    lonn = _to_numero(lon, None)
    if latn is None or lonn is None:
        return False
    if latn == 0 or lonn == 0:
        return False
    # Mato Grosso aproximadamente: lat -18 a -7, lon -62 a -50. Faixa folgada para evitar falso negativo.
    return -25 <= latn <= 0 and -70 <= lonn <= -45


@st.cache_data(ttl=60 * 60 * 8, show_spinner=False)
def carregar_cnes_ubs_geo_mt() -> pd.DataFrame:
    bruto = _baixar_csv_zip(URL_CNES_ESTABELECIMENTOS_ZIP)
    if bruto.empty:
        raise ValueError("A base CNES foi lida, mas veio vazia.")

    col_codigo_ibge = _identificar_coluna(bruto, [
        "codigo_ibge", "co_municipio_gestor", "co_municipio", "cod_municipio", "codigo_municipio", "cod_ibge", "municipio_ibge",
        "co_municipio_ibge", "codmun", "cod_mun", "co_mun", "ibge", "codigo_mun", "municipio_codigo"
    ])
    col_municipio = _identificar_coluna(bruto, ["municipio", "nome_municipio", "no_municipio", "no_municipio_gestor", "mun_nome", "nm_municipio"])
    if col_codigo_ibge is None:
        col_codigo_ibge = _detectar_coluna_codigo_municipio_por_conteudo(bruto)
    if col_municipio is None:
        col_municipio = _detectar_coluna_nome_municipio_por_conteudo(bruto)
    col_uf = _identificar_coluna(bruto, ["uf", "sg_uf", "sigla_uf", "estado", "co_uf", "codigo_uf"])
    col_cnes = _identificar_coluna(bruto, ["cnes", "co_cnes", "codigo_cnes", "cod_cnes"])
    col_nome = _identificar_coluna(bruto, ["nome_fantasia", "no_fantasia", "nome_unidade", "no_estabelecimento", "nome_estabelecimento", "estabelecimento"])
    col_tipo = _identificar_coluna(bruto, ["tipo_estabelecimento", "ds_tipo_estabelecimento", "tipo_unidade", "ds_tipo_unidade", "descricao_tipo_unidade", "no_tipo_unidade"])
    col_subtipo = _identificar_coluna(bruto, ["subtipo", "subtipo_estabelecimento", "ds_subtipo_estabelecimento", "subtipo_unidade"])
    col_situacao = _identificar_coluna(bruto, ["situacao", "st_ativo", "ativo", "situacao_cadastral", "ds_situacao"])
    col_bairro = _identificar_coluna(bruto, ["bairro", "no_bairro", "bairro_estabelecimento"])
    col_logradouro = _identificar_coluna(bruto, ["logradouro", "endereco", "no_logradouro", "endereco_estabelecimento"])
    col_lat = _identificar_coluna(bruto, ["latitude", "lat", "nu_latitude", "vl_latitude", "latitude_estabelecimento", "nu_latitude_estabelecimento"])
    col_lon = _identificar_coluna(bruto, ["longitude", "lon", "lng", "nu_longitude", "vl_longitude", "longitude_estabelecimento", "nu_longitude_estabelecimento"])

    df = bruto.copy()
    if col_uf:
        uf = df[col_uf].map(_texto_busca)
        mask_uf = uf.isin(["mt", "mato grosso", "51"])
        if mask_uf.any():
            df = df[mask_uf].copy()

    if col_codigo_ibge:
        cod = df[col_codigo_ibge].astype(str).str.extract(r"(\d+)")[0].fillna("")
        df["codigo_ibge"] = cod.where(cod.str.len() >= 6, "").str[:7]
        df = df[df["codigo_ibge"].str.startswith(UF_MT, na=False)].copy()
    elif col_municipio is None and col_uf is None:
        raise ValueError("Não localizei coluna de município/UF para filtrar Mato Grosso na base CNES.")

    texto_tipo = _serie(df, col_tipo, "").map(_texto_busca)
    texto_subtipo = _serie(df, col_subtipo, "").map(_texto_busca)
    texto_nome = _serie(df, col_nome, "").map(_texto_busca)
    texto_geral = (texto_tipo + " " + texto_subtipo + " " + texto_nome).str.strip()
    padrao_aps = "|".join([
        "unidade basica", "centro de saude", "posto de saude", "saude da familia", "unidade de saude da familia", "estrategia saude da familia", "ubs", "usf"
    ])
    df = df[texto_geral.str.contains(padrao_aps, na=False)].copy()

    if col_situacao:
        situacao = df[col_situacao].map(_texto_busca)
        df = df[~situacao.str.contains("inativ|desativ|baixad|suspens|encerrad", na=False)].copy()

    if df.empty:
        raise ValueError("Nenhuma UBS/USF/Posto de Saúde de Mato Grosso foi identificado no CNES.")

    saida = pd.DataFrame()
    saida["municipio"] = _serie(df, col_municipio, "") if col_municipio else ""
    saida["codigo_ibge"] = df.get("codigo_ibge", pd.Series([""] * len(df), index=df.index)).astype(str).str[:7]
    saida["cnes"] = _serie(df, col_cnes, "").astype(str).str.strip()
    saida["nome_unidade"] = _serie(df, col_nome, "").astype(str).str.strip()
    saida["tipo_unidade"] = [
        _classificar_unidade_aps(t, s, n)
        for t, s, n in zip(_serie(df, col_tipo, ""), _serie(df, col_subtipo, ""), _serie(df, col_nome, ""))
    ]
    saida["bairro"] = _serie(df, col_bairro, "").astype(str).str.strip()
    saida["logradouro"] = _serie(df, col_logradouro, "").astype(str).str.strip()
    saida["latitude"] = _serie(df, col_lat, "").map(lambda x: _to_numero(x, None)) if col_lat else None
    saida["longitude"] = _serie(df, col_lon, "").map(lambda x: _to_numero(x, None)) if col_lon else None
    saida["qtd_esf"] = 0
    saida["qtd_esb"] = 0
    saida["ines_vinculados"] = ""
    saida["fonte"] = "Dados Abertos SUS - CNES Estabelecimentos"
    saida["observacao_validacao"] = "Validar funcionamento, localização e vínculo territorial com APS municipal/ERS."
    saida["coordenada_valida"] = [
        _latlon_valida(lat, lon) for lat, lon in zip(saida["latitude"], saida["longitude"])
    ]
    # O arquivo público do CNES frequentemente vem com código do município, mas sem nome.
    # Aqui completamos o nome pela API de Localidades do IBGE, aceitando código de 6 ou 7 dígitos.
    saida = _enriquecer_municipio_por_codigo(saida, "codigo_ibge", "municipio")

    subset = ["cnes"] if saida["cnes"].astype(str).str.strip().ne("").any() else ["codigo_ibge", "nome_unidade"]
    saida = saida.drop_duplicates(subset=subset).sort_values(["municipio", "nome_unidade"], na_position="last")
    return saida.reset_index(drop=True)



def _ler_dataframe_bytes_por_nome(nome: str, bruto: bytes) -> pd.DataFrame:
    """Lê CSV/TXT/XLSX a partir de bytes, com separadores e encodings flexíveis."""
    nome_lower = (nome or "").lower()
    if nome_lower.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(bruto), dtype=str)
    # TXT/CSV oficiais podem vir com ; , | ou tabulação.
    ultimo_erro = None
    for enc in ["utf-8-sig", "utf-8", "latin1", "cp1252"]:
        for sep in [None, ";", ",", "|", "\t"]:
            try:
                return pd.read_csv(
                    io.BytesIO(bruto),
                    sep=sep,
                    engine="python",
                    dtype=str,
                    encoding=enc,
                    on_bad_lines="skip",
                )
            except Exception as exc:
                ultimo_erro = exc
    raise ValueError(f"Não foi possível ler o arquivo {nome}: {ultimo_erro}")


def _raiz_ine_para_chave_profissional(ine: Any) -> str:
    """Extrai uma raiz técnica do INE para cruzar com o arquivo ProfissionaisEquipesBrasil.

    O arquivo de profissionais do pacote EQUIPES BRASIL não traz o CNES de forma direta em
    todos os registros. A chave mais estável observada no arquivo oficial é: município + área
    da equipe + raiz do identificador da equipe. A raiz é usada apenas para contagem agregada,
    sem exposição de CPF/CNS.
    """
    digitos = re.sub(r"\D", "", "" if ine is None else str(ine))
    if not digitos:
        return ""
    digitos = digitos.zfill(10)
    significativo = digitos.lstrip("0")
    if not significativo:
        return ""
    return significativo[:4]


def _montar_chave_profissionais(codigo_municipio_6: Any, area_equipe: Any, raiz_ine: Any) -> str:
    cod = re.sub(r"\D", "", "" if codigo_municipio_6 is None else str(codigo_municipio_6))[:6]
    area = re.sub(r"\D", "", "" if area_equipe is None else str(area_equipe)).zfill(4)[:4]
    raiz = re.sub(r"\D", "", "" if raiz_ine is None else str(raiz_ine))[:4]
    if not (cod and area and raiz):
        return ""
    return f"{cod}|{area}|{raiz}"


def _parse_profissionais_equipes_brasil_fixed_width(bruto: bytes, nome_origem: str = "ProfissionaisEquipesBrasil.txt") -> pd.DataFrame:
    """Lê o arquivo ProfissionaisEquipesBrasil.txt do pacote EQUIPES BRASIL.

    Saída deliberadamente anonimizada: não armazena CPF/CNS. O identificador do profissional
    é transformado em hash curto apenas para permitir contagem distinta agregada por equipe.
    """
    texto = None
    ultimo_erro = None
    for enc in ["latin1", "cp1252", "utf-8-sig", "utf-8"]:
        try:
            texto = bruto.decode(enc)
            break
        except Exception as exc:
            ultimo_erro = exc
    if texto is None:
        raise ValueError(f"Não foi possível decodificar {nome_origem}: {ultimo_erro}")

    registros = []
    for linha in texto.splitlines():
        if len(linha) < 24:
            continue
        co_mun_6 = linha[0:6].strip()
        cbo = linha[6:12].strip()
        codigo_vinculo = re.sub(r"\D", "", linha[12:22])
        if not (co_mun_6.isdigit() and co_mun_6.startswith(UF_MT) and cbo.isdigit() and len(codigo_vinculo) >= 8):
            continue
        area_equipe = codigo_vinculo[0:4]
        raiz_ine = codigo_vinculo[4:8]
        chave = _montar_chave_profissionais(co_mun_6, area_equipe, raiz_ine)
        if not chave:
            continue

        # O trecho seguinte ao vínculo costuma trazer identificador profissional antes do bloco criptografado.
        # Não armazenamos esse identificador. Usamos hash curto para contagem distinta.
        token_bruto = re.sub(r"\D", "", linha[22:34])
        if not token_bruto:
            token_bruto = hashlib.sha256(linha.encode("utf-8", errors="ignore")).hexdigest()
        profissional_hash = hashlib.sha256(f"{co_mun_6}|{cbo}|{chave}|{token_bruto}".encode("utf-8")).hexdigest()[:20]

        registros.append({
            "codigo_ibge": co_mun_6,
            "municipio": "",
            "cbo": cbo,
            "area_equipe": area_equipe,
            "raiz_ine_profissionais": raiz_ine,
            "chave_profissionais_equipe": chave,
            "profissional_hash": profissional_hash,
            "fonte": f"CNES/DATASUS oficial - {nome_origem}",
        })

    if not registros:
        return pd.DataFrame(columns=[
            "codigo_ibge", "municipio", "cbo", "area_equipe", "raiz_ine_profissionais",
            "chave_profissionais_equipe", "profissional_hash", "fonte"
        ])
    df = pd.DataFrame(registros)
    df = _enriquecer_municipio_por_codigo(df, "codigo_ibge", "municipio")
    return df.reset_index(drop=True)


def _ler_upload_profissionais_equipes_brasil(uploaded_file) -> pd.DataFrame:
    """Tenta extrair ProfissionaisEquipesBrasil.txt do mesmo ZIP EQUIPES BRASIL enviado."""
    if uploaded_file is None:
        return pd.DataFrame()
    nome = uploaded_file.name
    nome_lower = nome.lower()
    uploaded_file.seek(0)
    bruto = uploaded_file.read()
    uploaded_file.seek(0)

    if nome_lower.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(bruto)) as zf:
            candidatos = [
                n for n in zf.namelist()
                if "profissionaisequipesbrasil" in _texto_busca(n) and n.lower().endswith(".txt") and not n.endswith("/")
            ]
            if not candidatos:
                return pd.DataFrame()
            escolhido = max(candidatos, key=lambda n: zf.getinfo(n).file_size)
            return _parse_profissionais_equipes_brasil_fixed_width(zf.read(escolhido), escolhido)

    if "profissionaisequipesbrasil" in _texto_busca(nome) and nome_lower.endswith(".txt"):
        return _parse_profissionais_equipes_brasil_fixed_width(bruto, nome)
    return pd.DataFrame()


def consolidar_profissionais_por_equipe(equipes: Optional[pd.DataFrame], profissionais: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Cruza equipes válidas com profissionais agregados, sem expor CPF/CNS."""
    if equipes is None or not isinstance(equipes, pd.DataFrame) or equipes.empty:
        return pd.DataFrame()
    eq = filtrar_equipes_ine_aps(equipes.copy())
    if eq.empty:
        return pd.DataFrame()

    eq = _normalizar_colunas(eq)
    if "codigo_ibge" in eq.columns:
        eq["codigo_ibge"] = eq["codigo_ibge"].astype(str).str.extract(r"(\d{6,7})")[0].fillna("").str[:6]
    if "ine" not in eq.columns:
        eq["ine"] = ""
    if "area_equipe" not in eq.columns:
        eq["area_equipe"] = ""
    if "raiz_ine_profissionais" not in eq.columns:
        eq["raiz_ine_profissionais"] = eq["ine"].map(_raiz_ine_para_chave_profissional)
    if "chave_profissionais_equipe" not in eq.columns:
        eq["chave_profissionais_equipe"] = eq.apply(
            lambda r: _montar_chave_profissionais(r.get("codigo_ibge"), r.get("area_equipe"), r.get("raiz_ine_profissionais")), axis=1
        )

    prof = profissionais.copy() if isinstance(profissionais, pd.DataFrame) else pd.DataFrame()
    if prof.empty or "chave_profissionais_equipe" not in prof.columns:
        eq["total_profissionais"] = 0
        eq["cbos_distintos"] = 0
        eq["fonte_profissionais"] = "ProfissionaisEquipesBrasil não carregado"
        return eq.reset_index(drop=True)

    prof = _normalizar_colunas(prof)
    agg = (
        prof.groupby("chave_profissionais_equipe", dropna=False)
        .agg(
            total_profissionais=("profissional_hash", lambda x: int(pd.Series(x).astype(str).replace("", pd.NA).dropna().nunique())),
            cbos_distintos=("cbo", lambda x: int(pd.Series(x).astype(str).replace("", pd.NA).dropna().nunique())),
        )
        .reset_index()
    )
    out = eq.merge(agg, on="chave_profissionais_equipe", how="left")
    out["total_profissionais"] = pd.to_numeric(out["total_profissionais"], errors="coerce").fillna(0).astype(int)
    out["cbos_distintos"] = pd.to_numeric(out["cbos_distintos"], errors="coerce").fillna(0).astype(int)
    out["fonte_profissionais"] = "ProfissionaisEquipesBrasil.txt, contagem agregada/anônima"
    return out.reset_index(drop=True)



def _parse_equipes_validas_brasil_fixed_width(bruto: bytes, nome_origem: str = "EQUIPESValidasBrasil.txt") -> pd.DataFrame:
    """Lê o arquivo oficial EQUIPESValidasBrasil.txt do CNES/DATASUS.

    Esse arquivo não é CSV delimitado; ele é de largura fixa. A rotina extrai os campos
    essenciais para o georreferenciamento: município, CNES, INE, nome da equipe e tipo.
    """
    texto = None
    ultimo_erro = None
    for enc in ["latin1", "cp1252", "utf-8-sig", "utf-8"]:
        try:
            texto = bruto.decode(enc)
            break
        except Exception as exc:
            ultimo_erro = exc
    if texto is None:
        raise ValueError(f"Não foi possível decodificar {nome_origem}: {ultimo_erro}")

    registros = []
    for linha in texto.splitlines():
        if len(linha) < 25:
            continue
        co_mun_6 = linha[0:6].strip()
        cnes = linha[6:13].strip()
        tipo_codigo = linha[13:15].strip()
        seq_equipe = linha[15:17].strip()
        nome_equipe = linha[17:77].strip()
        area_equipe = linha[77:81].strip() if len(linha) >= 81 else ""
        desc_equipe = linha[81:141].strip() if len(linha) >= 141 else ""
        ine_match = re.findall(r"\d{10}", linha)
        ine = ine_match[-1] if ine_match else ""
        raiz_ine_prof = _raiz_ine_para_chave_profissional(ine)
        chave_profissionais = _montar_chave_profissionais(co_mun_6, area_equipe, raiz_ine_prof)

        # Ignora cabeçalho/controle e linhas que não tenham estrutura mínima.
        if not (co_mun_6.isdigit() and cnes.isdigit() and ine.isdigit()):
            continue

        # Neste projeto estamos filtrando Mato Grosso. O arquivo usa o código municipal
        # de 6 dígitos; a função de enriquecimento converte para 7 dígitos quando possível.
        if not co_mun_6.startswith(UF_MT):
            continue

        mapa_tipo = {
            "70": "eSF / Equipe de Saúde da Família",
            "71": "eSB / Equipe de Saúde Bucal",
            "72": "eMulti / Equipe Multiprofissional",
            "73": "eCR / Consultório na Rua",
            "74": "eAPP / Equipe de Atenção Primária Prisional",
            "75": "Tipo 75 / Não utilizado neste painel",
            "76": "eAP / Equipe de Atenção Primária",
        }
        # Diretriz SES/MT: para o painel da coordenadoria, considerar apenas
        # equipes INE dos tipos 70, 71, 72, 73, 74 e 76.
        if tipo_codigo not in TIPOS_EQUIPE_INE_PRIORITARIOS:
            continue

        tipo_desc = mapa_tipo.get(tipo_codigo, f"Tipo CNES {tipo_codigo}" if tipo_codigo else "")

        registros.append({
            "codigo_ibge": co_mun_6,
            "municipio": "",
            "cnes": cnes,
            "ine": ine,
            "nome_equipe": nome_equipe or desc_equipe,
            "tipo_equipe": tipo_desc,
            "tipo_equipe_codigo": tipo_codigo,
            "sequencial_equipe": seq_equipe,
            "area_equipe": area_equipe,
            "raiz_ine_profissionais": raiz_ine_prof,
            "chave_profissionais_equipe": chave_profissionais,
            "situacao_equipe": "Válida",
            "fonte": f"CNES/DATASUS oficial - {nome_origem}",
        })

    if not registros:
        raise ValueError(
            "O arquivo EQUIPESValidasBrasil foi localizado, mas não encontrei registros de Mato Grosso com CNES e INE. "
            "Verifique se a competência enviada é nacional e contém equipes válidas."
        )

    return pd.DataFrame(registros)


def _ler_upload_equipes_cnes_oficial(uploaded_file) -> pd.DataFrame:
    """Lê arquivo oficial de equipes do CNES/DATASUS.

    Aceita CSV/TXT/XLSX ou ZIP contendo esses formatos. Se o ZIP trouxer DBF/DBC,
    o sistema orienta a converter/exportar para CSV, porque esta versão evita
    depender de pacotes externos pesados no Streamlit.
    """
    nome = uploaded_file.name
    nome_lower = nome.lower()
    uploaded_file.seek(0)
    bruto = uploaded_file.read()

    if nome_lower.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(bruto)) as zf:
            nomes = zf.namelist()

            # Caso oficial mais comum: EQUIPESBRASIL_YYYYMM.ZIP contendo EQUIPESValidasBrasil.txt.
            candidatos_equipes_validas = [
                n for n in nomes
                if "equipesvalidasbrasil" in _texto_busca(n) and n.lower().endswith(".txt") and not n.endswith("/")
            ]
            if candidatos_equipes_validas:
                escolhido = max(candidatos_equipes_validas, key=lambda n: zf.getinfo(n).file_size)
                return _parse_equipes_validas_brasil_fixed_width(zf.read(escolhido), escolhido)

            candidatos_tabulares = [
                n for n in nomes
                if n.lower().endswith((".csv", ".txt", ".xlsx", ".xls"))
                and not n.endswith("/")
            ]
            if candidatos_tabulares:
                # Prefere arquivos cujo nome lembre equipe/INE; senão pega o maior.
                def score(n: str) -> tuple:
                    nl = _texto_busca(n)
                    bonus = 1 if any(t in nl for t in ["equipe", "equipes", "ine"]) else 0
                    return (bonus, zf.getinfo(n).file_size)
                escolhido = max(candidatos_tabulares, key=score)
                return _ler_dataframe_bytes_por_nome(escolhido, zf.read(escolhido))

            candidatos_dbf_dbc = [n for n in nomes if n.lower().endswith((".dbf", ".dbc"))]
            if candidatos_dbf_dbc:
                raise ValueError(
                    "O ZIP oficial contém DBF/DBC. Para esta versão do sistema, exporte/converta o arquivo EQUIPES BRASIL para CSV/TXT "
                    "e envie novamente, ou disponibilize uma planilha extraída do CNES com CNES, INE e tipo de equipe."
                )
            raise ValueError("O ZIP enviado não contém CSV, TXT, XLSX/XLS, DBF ou DBC reconhecível.")

    if "equipesvalidasbrasil" in _texto_busca(nome) and nome_lower.endswith(".txt"):
        return _parse_equipes_validas_brasil_fixed_width(bruto, nome)

    if nome_lower.endswith((".csv", ".txt", ".xlsx", ".xls")):
        return _ler_dataframe_bytes_por_nome(nome, bruto)

    raise ValueError("Formato não reconhecido. Envie CSV, TXT, XLSX/XLS ou ZIP oficial contendo arquivo tabular.")


def _normalizar_equipes_cnes_oficial(bruto: pd.DataFrame, fonte: str = "CNES/DATASUS oficial") -> pd.DataFrame:
    """Transforma a base oficial de equipes em colunas padronizadas para vínculo por CNES."""
    if bruto is None or bruto.empty:
        raise ValueError("A base oficial de equipes foi lida, mas veio vazia.")

    df = _normalizar_colunas(bruto)

    col_codigo_ibge = _identificar_coluna(df, [
        "codigo_ibge", "co_municipio", "cod_municipio", "co_municipio_gestor", "cod_ibge",
        "municipio_ibge", "ibge", "codmun", "cod_mun", "co_mun", "co_ibge"
    ])
    if col_codigo_ibge is None:
        col_codigo_ibge = _detectar_coluna_codigo_municipio_por_conteudo(df)

    col_uf = _identificar_coluna(df, ["uf", "sg_uf", "sigla_uf", "estado", "co_uf", "codigo_uf"])
    col_municipio = _identificar_coluna(df, ["municipio", "nome_municipio", "no_municipio", "no_municipio_gestor", "mun_nome", "nm_municipio"])
    col_cnes = _identificar_coluna(df, ["cnes", "co_cnes", "codigo_cnes", "cod_cnes", "estabelecimento_cnes"])
    col_ine = _identificar_coluna(df, [
        "ine", "co_ine", "codigo_ine", "cod_ine", "identificador_nacional_equipe",
        "identificador_nacional_de_equipe", "nu_ine", "numero_ine", "co_equipe", "codigo_equipe"
    ])
    col_nome_equipe = _identificar_coluna(df, ["nome_equipe", "no_equipe", "equipe", "nome", "ds_equipe", "descricao_equipe"])
    col_tipo_equipe = _identificar_coluna(df, [
        "tipo_equipe", "ds_tipo_equipe", "no_tipo_equipe", "tp_equipe", "descricao_tipo_equipe",
        "tipo", "co_tipo_equipe", "codigo_tipo_equipe"
    ])
    col_situacao = _identificar_coluna(df, ["situacao", "st_ativo", "ativo", "ds_situacao", "situacao_equipe", "st_equipe"])

    if col_cnes is None:
        raise ValueError("Não localizei coluna de CNES na base oficial de equipes.")
    if col_ine is None:
        raise ValueError("Não localizei coluna de INE/código da equipe na base oficial de equipes.")

    work = df.copy()
    if col_uf:
        uf = work[col_uf].map(_texto_busca)
        mask_uf = uf.isin(["mt", "mato grosso", "51"])
        if mask_uf.any():
            work = work[mask_uf].copy()

    if col_codigo_ibge:
        cod = work[col_codigo_ibge].astype(str).str.extract(r"(\d{6,7})")[0].fillna("")
        work["codigo_ibge"] = cod.where(cod.str.len() >= 6, "").str[:7]
        # Se o arquivo for Brasil, filtra MT. Se não houver código reconhecível, mantém para vínculo por CNES.
        if work["codigo_ibge"].str.startswith(UF_MT, na=False).any():
            work = work[work["codigo_ibge"].str.startswith(UF_MT, na=False)].copy()

    if col_situacao:
        situacao = work[col_situacao].map(_texto_busca)
        work = work[~situacao.str.contains("inativ|desativ|baixad|suspens|encerrad|excluid", na=False)].copy()

    saida = pd.DataFrame(index=work.index)
    saida["codigo_ibge"] = work.get("codigo_ibge", pd.Series([""] * len(work), index=work.index)).astype(str).str[:7]
    saida["municipio"] = _serie(work, col_municipio, "").astype(str).str.strip()
    saida["cnes"] = _serie(work, col_cnes, "").astype(str).str.extract(r"(\d+)")[0].fillna("").str.strip()
    saida["ine"] = _serie(work, col_ine, "").astype(str).str.extract(r"(\d+)")[0].fillna("").str.strip()
    saida["nome_equipe"] = _serie(work, col_nome_equipe, "").astype(str).str.strip() if col_nome_equipe else ""
    saida["tipo_equipe"] = _serie(work, col_tipo_equipe, "").astype(str).str.strip() if col_tipo_equipe else ""
    saida["tipo_equipe_normalizado"] = saida["tipo_equipe"].map(_texto_busca)

    # Mapeamento conservador: se vier descrição textual, usa texto; se vier código,
    # considera os códigos mais comuns da APS no CNES para eSF/eSB, mantendo revisão local.
    tipo_codigo = saida["tipo_equipe"].astype(str).str.extract(r"(\d+)")[0].fillna("").str.strip()
    if "tipo_equipe_codigo" in work.columns:
        tipo_codigo_oficial = work["tipo_equipe_codigo"].astype(str).str.extract(r"(\d+)")[0].fillna("").str.strip()
        tipo_codigo = tipo_codigo.where(tipo_codigo.ne(""), tipo_codigo_oficial)
    saida["tipo_equipe_codigo"] = tipo_codigo

    # Diretriz SES/MT: a coordenadoria quer analisar apenas os INEs
    # dos tipos 70, 71, 72, 73, 74 e 76. O tipo 75 fica fora do painel.
    saida = saida[saida["tipo_equipe_codigo"].isin(TIPOS_EQUIPE_INE_PRIORITARIOS)].copy()
    tipo_codigo = saida["tipo_equipe_codigo"]

    saida["eh_esf"] = (
        saida["tipo_equipe_normalizado"].str.contains("saude da familia|esf|equipe de saude da familia", na=False)
        | tipo_codigo.isin(["70"])
    )
    saida["eh_esb"] = (
        saida["tipo_equipe_normalizado"].str.contains("saude bucal|esb|equipe de saude bucal", na=False)
        | tipo_codigo.isin(["71"])
    )
    saida["fonte"] = fonte
    saida = saida[(saida["cnes"].ne("")) & (saida["ine"].ne(""))].copy()
    if saida.empty:
        raise ValueError("A base oficial foi lida, mas não restaram registros com CNES e INE preenchidos.")
    saida = _enriquecer_municipio_por_codigo(saida, "codigo_ibge", "municipio")
    return saida.drop_duplicates(subset=["cnes", "ine"]).reset_index(drop=True)


def _descobrir_links_cnes_datasus_oficial() -> List[str]:
    """Tenta descobrir links de arquivo de equipes nas páginas oficiais do CNES/DATASUS."""
    urls: List[str] = []
    paginas = [URL_CNES_ARQUIVOS_APLICACAO, URL_CNES_BASE_DADOS]
    headers = {"User-Agent": "Mozilla/5.0 SES-MT-Georreferenciamento-APS/1.0"}
    for pagina in paginas:
        try:
            resp = requests.get(pagina, timeout=60, headers=headers)
            resp.raise_for_status()
            html = resp.text
        except Exception:
            continue
        for href in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.I):
            url = urljoin(pagina, href)
            busca = _texto_busca(url)
            if any(t in busca for t in ["equipe", "equipes", "ine", "eqbrasil", "equipesbrasil"]):
                if url.lower().endswith((".zip", ".csv", ".txt", ".xlsx", ".xls", ".dbf", ".dbc")) or "download" in busca:
                    urls.append(url)
    # Remove duplicados.
    unicas = []
    vistos = set()
    for u in urls:
        if u not in vistos:
            vistos.add(u)
            unicas.append(u)
    return unicas


@st.cache_data(ttl=60 * 60 * 8, show_spinner=False)
def carregar_equipes_ine_cnes_datasus_oficial() -> pd.DataFrame:
    """Tenta carregar equipes/INE a partir do caminho oficial CNES/DATASUS."""
    erros: List[str] = []
    urls = []
    try:
        urls.extend(_descobrir_links_cnes_datasus_oficial())
    except Exception as exc:
        erros.append(f"Descoberta de links CNES/DATASUS: {exc}")

    # Mantém candidatos explícitos por diagnóstico, mesmo quando forem páginas HTML.
    for u in URL_CNES_EQUIPES_OFICIAL_CANDIDATOS:
        if u not in urls:
            urls.append(u)

    st.session_state["geo_ine_cnes_datasus_urls_tentadas"] = urls

    for url in urls:
        try:
            resp = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0 SES-MT-Georreferenciamento-APS/1.0"})
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").lower()
            # Se veio HTML, não é arquivo direto; registra e segue.
            if "text/html" in content_type and not url.lower().endswith((".csv", ".txt", ".zip", ".xlsx", ".xls")):
                erros.append(f"{url} -> página HTML; requer seleção manual no site do CNES")
                continue
            nome = url.split("?")[0].rstrip("/").split("/")[-1] or "arquivo_cnes_datasus"
            bruto_df = _ler_dataframe_bytes_por_nome(nome, resp.content) if not nome.lower().endswith(".zip") else None
            if nome.lower().endswith(".zip"):
                fake = io.BytesIO(resp.content)
                fake.name = nome
                bruto_df = _ler_upload_equipes_cnes_oficial(fake)
            saida = _normalizar_equipes_cnes_oficial(bruto_df, fonte=f"CNES/DATASUS oficial ({url})")
            st.session_state["geo_ine_cnes_datasus_url_usada"] = url
            return saida
        except Exception as exc:
            erros.append(f"{url} -> {exc}")

    st.session_state["geo_ine_cnes_datasus_ultimo_erro"] = " | ".join(erros[-8:])
    detalhe = " | ".join(erros[-8:]) if erros else "sem detalhe retornado"
    raise ValueError(
        "Não foi possível carregar automaticamente o arquivo oficial de equipes pelo CNES/DATASUS. "
        "Use o upload manual do arquivo EQUIPES BRASIL/base oficial exportado para CSV/TXT/XLSX. "
        f"Últimos erros: {detalhe}"
    )


@st.cache_data(ttl=60 * 60 * 8, show_spinner=False)
def carregar_cnes_equipes_ine_mt() -> pd.DataFrame:
    erros: List[str] = []
    bruto = None
    url_usada = ""
    urls_tentadas = list(URL_CNES_EQUIPES_CANDIDATOS)

    # Tenta descobrir URLs atualizadas no catálogo CKAN.
    try:
        urls_tentadas.extend(_descobrir_urls_equipes_ine_ckan())
    except Exception as exc:
        erros.append(f"Busca CKAN: {exc}")

    # Remove duplicados preservando a ordem.
    urls_limpa = []
    vistos = set()
    for url in urls_tentadas:
        if url and url not in vistos:
            vistos.add(url)
            urls_limpa.append(url)

    for url in urls_limpa:
        try:
            bruto = _baixar_csv_zip(url)
            url_usada = url
            if bruto is not None and not bruto.empty:
                break
        except Exception as exc:
            erros.append(f"{url} -> {exc}")

    st.session_state["geo_ine_urls_tentadas"] = urls_limpa
    st.session_state["geo_ine_ultimo_erro"] = " | ".join(erros[-5:])

    if bruto is None or bruto.empty:
        detalhe = " | ".join(erros[-5:]) if erros else "sem detalhe retornado pela fonte"
        raise ValueError(f"Não foi possível carregar base de equipes CNES/INE após tentativa multiorigem. Últimos erros: {detalhe}")

    col_codigo_ibge = _identificar_coluna(bruto, ["codigo_ibge", "co_municipio", "cod_municipio", "co_municipio_gestor", "cod_ibge"])
    col_uf = _identificar_coluna(bruto, ["uf", "sg_uf", "sigla_uf", "co_uf"])
    col_municipio = _identificar_coluna(bruto, ["municipio", "nome_municipio", "no_municipio", "no_municipio_gestor"])
    col_cnes = _identificar_coluna(bruto, ["cnes", "co_cnes", "codigo_cnes", "cod_cnes"])
    col_ine = _identificar_coluna(bruto, ["ine", "co_ine", "codigo_ine", "cod_ine"])
    col_tipo_equipe = _identificar_coluna(bruto, ["tipo_equipe", "ds_tipo_equipe", "no_tipo_equipe", "tp_equipe", "descricao_tipo_equipe"])
    col_situacao = _identificar_coluna(bruto, ["situacao", "st_ativo", "ativo", "ds_situacao"])

    df = bruto.copy()
    if col_uf:
        uf = df[col_uf].map(_texto_busca)
        mask_uf = uf.isin(["mt", "mato grosso", "51"])
        if mask_uf.any():
            df = df[mask_uf].copy()
    if col_codigo_ibge:
        cod = df[col_codigo_ibge].astype(str).str.extract(r"(\d+)")[0].fillna("")
        df["codigo_ibge"] = cod.where(cod.str.len() >= 6, "").str[:7]
        df = df[df["codigo_ibge"].str.startswith(UF_MT, na=False)].copy()

    if col_situacao:
        situacao = df[col_situacao].map(_texto_busca)
        df = df[~situacao.str.contains("inativ|desativ|baixad|suspens|encerrad", na=False)].copy()

    saida = pd.DataFrame()
    saida["codigo_ibge"] = df.get("codigo_ibge", pd.Series([""] * len(df), index=df.index)).astype(str).str[:7]
    saida["municipio"] = _serie(df, col_municipio, "").astype(str).str.strip()
    saida["cnes"] = _serie(df, col_cnes, "").astype(str).str.strip()
    saida["ine"] = _serie(df, col_ine, "").astype(str).str.strip()
    saida["tipo_equipe"] = _serie(df, col_tipo_equipe, "").astype(str).str.strip()
    saida["tipo_equipe_normalizado"] = saida["tipo_equipe"].map(_texto_busca)
    saida["eh_esf"] = saida["tipo_equipe_normalizado"].str.contains("saude da familia|esf|equipe de saude da familia", na=False)
    saida["eh_esb"] = saida["tipo_equipe_normalizado"].str.contains("saude bucal|esb|equipe de saude bucal", na=False)
    saida["fonte"] = f"Dados Abertos SUS - CNES Equipes ({url_usada})"
    st.session_state["geo_ine_url_usada"] = url_usada
    saida = _enriquecer_municipio_por_codigo(saida, "codigo_ibge", "municipio")
    return saida.reset_index(drop=True)


def consolidar_equipes_por_cnes(equipes: pd.DataFrame) -> pd.DataFrame:
    if equipes is None or equipes.empty or "cnes" not in equipes.columns:
        return pd.DataFrame(columns=["cnes", "qtd_esf", "qtd_esb", "ines_vinculados"])
    df = filtrar_equipes_ine_aps(equipes.copy())
    if df.empty:
        return pd.DataFrame(columns=["cnes", "qtd_esf", "qtd_esb", "ines_vinculados"])
    df["cnes"] = df["cnes"].astype(str).str.strip()
    df = df[df["cnes"].ne("")].copy()
    if df.empty:
        return pd.DataFrame(columns=["cnes", "qtd_esf", "qtd_esb", "ines_vinculados"])
    return (
        df.groupby("cnes")
        .agg(
            qtd_esf=("eh_esf", lambda x: int(pd.Series(x).fillna(False).sum())),
            qtd_esb=("eh_esb", lambda x: int(pd.Series(x).fillna(False).sum())),
            ines_vinculados=("ine", lambda x: "; ".join(sorted({str(v).strip() for v in x if str(v).strip() and str(v).strip().lower() != "nan"}))[:1200]),
        )
        .reset_index()
    )


def juntar_ubs_equipes(ubs: pd.DataFrame, equipes: Optional[pd.DataFrame]) -> pd.DataFrame:
    if ubs is None or ubs.empty:
        return pd.DataFrame(columns=COLUNAS_UBS_MODELO)
    out = _normalizar_colunas(ubs)
    for col in COLUNAS_UBS_MODELO:
        if col not in out.columns:
            out[col] = "" if col not in ["qtd_esf", "qtd_esb", "latitude", "longitude"] else 0
    if equipes is not None and not equipes.empty and "cnes" in out.columns:
        eq = consolidar_equipes_por_cnes(equipes)
        out["cnes"] = out["cnes"].astype(str).str.strip()
        out = out.drop(columns=[c for c in ["qtd_esf", "qtd_esb", "ines_vinculados"] if c in out.columns], errors="ignore")
        out = out.merge(eq, on="cnes", how="left")
        out["qtd_esf"] = out["qtd_esf"].fillna(0).astype(int)
        out["qtd_esb"] = out["qtd_esb"].fillna(0).astype(int)
        out["ines_vinculados"] = out["ines_vinculados"].fillna("")
    return out


def _template_ubs() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "municipio": "Canarana", "codigo_ibge": "5102702", "cnes": "0000001",
            "nome_unidade": "UBS Centro - Exemplo", "tipo_unidade": "UBS / Centro de Saúde", "bairro": "Centro",
            "logradouro": "Rua Exemplo, 100", "latitude": -13.5510, "longitude": -52.2700,
            "qtd_esf": 2, "qtd_esb": 1, "ines_vinculados": "0000000001", "fonte": "Demonstração",
            "observacao_validacao": "Bairro com menor dependência SUS estimada.",
        },
        {
            "municipio": "Canarana", "codigo_ibge": "5102702", "cnes": "0000002",
            "nome_unidade": "UBS Setor Sul - Exemplo", "tipo_unidade": "USF / Saúde da Família", "bairro": "Setor Sul",
            "logradouro": "Av. Exemplo, 200", "latitude": -13.5750, "longitude": -52.3000,
            "qtd_esf": 1, "qtd_esb": 0, "ines_vinculados": "0000000002", "fonte": "Demonstração",
            "observacao_validacao": "UBS periférica demonstrativa.",
        },
    ])


def _template_territorios() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "municipio": "Canarana", "codigo_ibge": "5102702", "bairro_ou_localidade": "Centro / área nobre", "tipo_territorio": "Urbano consolidado",
            "populacao": 8000, "latitude": -13.5520, "longitude": -52.2710, "renda_media": 5200,
            "percentual_baixa_renda": 8, "percentual_bolsa_familia": 5, "percentual_cadunico": 12,
            "percentual_bpc": 1.5, "percentual_baixa_escolaridade": 8, "percentual_saneamento_inadequado": 4,
            "percentual_rural": 0, "indicador_pressao_aps": 20, "percentual_plano_saude_estimado": 55,
            "ubs_referencia": "UBS Centro - Exemplo", "distancia_ubs_km": 0.8,
            "observacao_validacao": "Área com maior renda e menor dependência estimada do SUS.",
        },
        {
            "municipio": "Canarana", "codigo_ibge": "5102702", "bairro_ou_localidade": "Setor Sul / periferia", "tipo_territorio": "Urbano vulnerável",
            "populacao": 12000, "latitude": -13.5900, "longitude": -52.3150, "renda_media": 1350,
            "percentual_baixa_renda": 55, "percentual_bolsa_familia": 42, "percentual_cadunico": 64,
            "percentual_bpc": 7, "percentual_baixa_escolaridade": 48, "percentual_saneamento_inadequado": 38,
            "percentual_rural": 18, "indicador_pressao_aps": 72, "percentual_plano_saude_estimado": 8,
            "ubs_referencia": "UBS Setor Sul - Exemplo", "distancia_ubs_km": 3.4,
            "observacao_validacao": "Área vulnerável e populosa com maior demanda SUS ajustada.",
        },
        {
            "municipio": "Canarana", "codigo_ibge": "5102702", "bairro_ou_localidade": "Comunidade Rural Exemplo", "tipo_territorio": "Rural disperso",
            "populacao": 2500, "latitude": -13.7200, "longitude": -52.5000, "renda_media": 1200,
            "percentual_baixa_renda": 62, "percentual_bolsa_familia": 50, "percentual_cadunico": 70,
            "percentual_bpc": 8, "percentual_baixa_escolaridade": 52, "percentual_saneamento_inadequado": 60,
            "percentual_rural": 100, "indicador_pressao_aps": 80, "percentual_plano_saude_estimado": 3,
            "ubs_referencia": "", "distancia_ubs_km": 18.5,
            "observacao_validacao": "Área rural distante; pode demandar UBS, unidade de apoio, rota ou equipe volante.",
        },
    ])


def _distancia_km(lat1: Any, lon1: Any, lat2: Any, lon2: Any) -> Optional[float]:
    lat1n = _to_numero(lat1, None)
    lon1n = _to_numero(lon1, None)
    lat2n = _to_numero(lat2, None)
    lon2n = _to_numero(lon2, None)
    if lat1n is None or lon1n is None or lat2n is None or lon2n is None:
        return None
    if any(v == 0 for v in [lat1n, lon1n, lat2n, lon2n]):
        return None
    raio_terra = 6371.0
    phi1, phi2 = math.radians(lat1n), math.radians(lat2n)
    d_phi = math.radians(lat2n - lat1n)
    d_lambda = math.radians(lon2n - lon1n)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return raio_terra * c


def _score_distancia(distancia_km: Any) -> float:
    d = _to_numero(distancia_km, 0.0) or 0.0
    if d <= 1:
        return 10
    if d <= 2:
        return 25
    if d <= 4:
        return 50
    if d <= 8:
        return 75
    return 100


def _score_pop_por_esf(populacao: Any, qtd_esf: Any) -> float:
    pop = max(0.0, _to_numero(populacao, 0.0) or 0.0)
    esf = max(0.0, _to_numero(qtd_esf, 0.0) or 0.0)
    if esf <= 0:
        return 100
    razao = pop / esf
    if razao <= 2500:
        return 15
    if razao <= 3500:
        return 35
    if razao <= 4500:
        return 60
    if razao <= 6000:
        return 80
    return 100


def _score_renda(renda_media: Any) -> float:
    renda = _to_numero(renda_media, 0.0) or 0.0
    if renda <= 0:
        return 50
    if renda <= 1000:
        return 100
    if renda <= 1500:
        return 85
    if renda <= 2500:
        return 60
    if renda <= 4000:
        return 35
    return 10


def _classificar_indice(valor: Any) -> str:
    v = _to_numero(valor, 0.0) or 0.0
    if v < 40:
        return "Baixa pressão"
    if v < 60:
        return "Atenção / validação"
    if v < 80:
        return "Vazio assistencial provável"
    return "Vazio assistencial crítico"


def _recomendacao(vazio: float, demanda: float, deficit_oferta: bool) -> str:
    if vazio < 40 and demanda < 50 and not deficit_oferta:
        return "Sem indicativo imediato de nova UBS; manter monitoramento."
    if vazio < 60:
        return "Validar território com APS municipal/ERS; pode demandar reorganização territorial ou ajuste de equipe."
    if vazio < 80:
        return "Priorizar validação técnica; avaliar nova UBS, unidade de apoio, ampliação de equipe ou atendimento programado."
    return "Prioridade crítica para análise de vazio assistencial; avaliar nova UBS ou solução territorial equivalente."


def _encontrar_ubs_mais_proxima(linha: Dict[str, Any], ubs: pd.DataFrame) -> Tuple[str, float, float, float]:
    if ubs is None or ubs.empty:
        return str(linha.get("ubs_referencia", "")), _to_numero(linha.get("distancia_ubs_km"), 0.0) or 0.0, 0.0, 0.0
    municipio = str(linha.get("municipio", "")).strip().lower()
    candidatas = ubs[ubs.get("municipio", pd.Series(index=ubs.index, dtype=str)).astype(str).str.strip().str.lower() == municipio].copy()
    if candidatas.empty:
        candidatas = ubs.copy()

    melhor_nome = str(linha.get("ubs_referencia", ""))
    menor_dist = None
    melhor_esf = 0.0
    melhor_esb = 0.0
    for _, u in candidatas.iterrows():
        dist = _distancia_km(linha.get("latitude"), linha.get("longitude"), u.get("latitude"), u.get("longitude"))
        if dist is None:
            continue
        if menor_dist is None or dist < menor_dist:
            menor_dist = dist
            melhor_nome = str(u.get("nome_unidade", ""))
            melhor_esf = _to_numero(u.get("qtd_esf"), 0.0) or 0.0
            melhor_esb = _to_numero(u.get("qtd_esb"), 0.0) or 0.0
    if menor_dist is None:
        menor_dist = _to_numero(linha.get("distancia_ubs_km"), 0.0) or 0.0
    return melhor_nome, round(float(menor_dist), 2), melhor_esf, melhor_esb




def _obter_base_municipal_unica_para_resultado() -> pd.DataFrame:
    """Monta uma base municipal única para apoiar ranking preliminar quando os setores ainda não foram carregados.

    Esta função não substitui o georreferenciamento por setor censitário. Ela serve para não deixar
    o dashboard completamente bloqueado enquanto a base territorial oficial é preparada.
    """
    base = _base_municipal_apis_normalizada()
    if base is None or not isinstance(base, pd.DataFrame) or base.empty:
        return pd.DataFrame()
    df = _normalizar_colunas(base)
    if "codigo_ibge" in df.columns:
        df["codigo_ibge"] = df["codigo_ibge"].astype(str).str.extract(r"(\d{6,7})")[0].fillna("")
        df = df[df["codigo_ibge"].astype(str).str.startswith("51", na=False)].copy()
    if "municipio" in df.columns:
        df["municipio"] = df["municipio"].astype(str).str.replace(r"\s*-\s*MT$", "", regex=True).str.strip()
    chaves = [c for c in ["codigo_ibge", "municipio"] if c in df.columns]
    if chaves:
        df = df.drop_duplicates(subset=chaves[:1], keep="first")
    return df.reset_index(drop=True)


def _valor_municipal(info: Optional[pd.Series], candidatos: List[str], padrao: Any = 0) -> Any:
    if info is None:
        return padrao
    return _primeiro_valor_coluna(info, candidatos, padrao)


def gerar_resultado_municipal_preliminar(ubs: pd.DataFrame) -> pd.DataFrame:
    """Gera resultado municipal preliminar quando ainda não há setores/territórios.

    Correção importante: o ranking preliminar deve partir da base municipal/API única
    e não apenas dos municípios que aparecem na base UBS/CNES. Assim o painel mantém
    os 142 municípios de MT e evidencia municípios sem UBS/CNES mapeada na carga atual.

    Este resultado NÃO calcula distância real até UBS. Distância, setores críticos e
    vazio territorial real continuam dependendo da base de setores censitários ou de
    outra base territorial com população/coordenadas.
    """
    ubs = _normalizar_colunas(ubs) if isinstance(ubs, pd.DataFrame) else pd.DataFrame()
    base_mun = _obter_base_municipal_unica_para_resultado()

    if ubs.empty and (base_mun is None or base_mun.empty):
        return pd.DataFrame()

    if not ubs.empty:
        for col in ["codigo_ibge", "municipio", "qtd_esf", "qtd_esb", "latitude", "longitude", "nome_unidade"]:
            if col not in ubs.columns:
                ubs[col] = "" if col in ["codigo_ibge", "municipio", "nome_unidade"] else 0
        ubs["codigo_ibge"] = ubs["codigo_ibge"].astype(str).str.extract(r"(\d{6,7})")[0].fillna("")
        ubs = ubs[ubs["codigo_ibge"].astype(str).str.startswith("51", na=False)].copy()
        ubs = _enriquecer_municipio_por_codigo(ubs, "codigo_ibge", "municipio")
        ubs["municipio_norm"] = ubs["municipio"].astype(str).str.replace(r"\s*-\s*MT$", "", regex=True).str.strip().str.lower()
        ubs["qtd_esf_num"] = pd.to_numeric(ubs["qtd_esf"], errors="coerce").fillna(0)
        ubs["qtd_esb_num"] = pd.to_numeric(ubs["qtd_esb"], errors="coerce").fillna(0)
        ubs["lat_num"] = pd.to_numeric(ubs["latitude"], errors="coerce")
        ubs["lon_num"] = pd.to_numeric(ubs["longitude"], errors="coerce")

    # Base de referência do ranking preliminar: municípios/APIs primeiro; UBS apenas como fallback.
    if base_mun is not None and isinstance(base_mun, pd.DataFrame) and not base_mun.empty:
        referencia = _normalizar_colunas(base_mun).copy()
        if "codigo_ibge" not in referencia.columns:
            referencia["codigo_ibge"] = ""
        referencia["codigo_ibge"] = referencia["codigo_ibge"].astype(str).str.extract(r"(\d{6,7})")[0].fillna("")
        referencia = referencia[referencia["codigo_ibge"].astype(str).str.startswith("51", na=False)].copy()
        if "municipio" not in referencia.columns:
            referencia["municipio"] = ""
        referencia = _enriquecer_municipio_por_codigo(referencia, "codigo_ibge", "municipio")
        referencia["municipio"] = referencia["municipio"].astype(str).str.replace(r"\s*-\s*MT$", "", regex=True).str.strip()
        referencia = referencia.drop_duplicates(subset=["codigo_ibge"], keep="first").reset_index(drop=True)
    else:
        referencia = ubs[["codigo_ibge", "municipio"]].drop_duplicates().reset_index(drop=True)

    linhas = []
    for _, ref in referencia.iterrows():
        codigo = str(ref.get("codigo_ibge", "") or "").strip()
        municipio = str(ref.get("municipio", "") or "").strip()
        info = _linha_indicadores_municipais(codigo, municipio, base_mun)

        if not ubs.empty and codigo:
            g = ubs[ubs["codigo_ibge"].astype(str) == codigo].copy()
        elif not ubs.empty:
            municipio_norm = municipio.lower().strip()
            g = ubs[ubs["municipio_norm"] == municipio_norm].copy()
        else:
            g = pd.DataFrame()

        populacao = _to_numero(_valor_municipal(info, ["populacao_ibge", "populacao_estimada_ibge", "populacao_estimada", "populacao"], ref.get("populacao", 0)), 0) or 0
        renda = _to_numero(_valor_municipal(info, ["renda_media", "rendimento_medio", "renda_domiciliar_media"], 0), 0) or 0
        baixa_renda = _clamp(_valor_municipal(info, ["percentual_baixa_renda", "baixa_renda_percentual", "perc_baixa_renda", "pct_rdpc_ate_1_2_sm_2022"], 0))
        bpc = _clamp((_to_numero(_valor_municipal(info, ["percentual_bpc", "bpc_total_por_1000_hab"], 0), 0) or 0) * 8)
        baixa_escolaridade = _clamp(_valor_municipal(info, ["percentual_baixa_escolaridade", "percentual_nao_alfabetizados", "taxa_analfabetismo", "pct_sem_instrucao_fund_incompleto_25mais_2022"], 0))
        saneamento = _clamp(_valor_municipal(info, ["percentual_saneamento_inadequado", "saneamento_inadequado_percentual", "percentual_sem_esgoto", "percentual_esgoto_inadequado", "indice_vulnerabilidade_saneamento_2022"], 0))
        ruralidade = _clamp(_valor_municipal(info, ["percentual_rural", "populacao_rural_percentual", "perc_rural", "percentual_rural_2022"], 0))
        pressao_aps = _clamp(_valor_municipal(info, ["indicador_pressao_aps", "indice_pressao_aps", "score_pressao_aps"], 0))
        plano_saude = _estimar_plano_saude_por_renda(renda)
        renda_score = _score_renda(renda)

        if g.empty:
            qtd_esf = 0.0
            qtd_esb = 0.0
            qtd_ubs = 0
            ubs_ref = "Sem UBS/CNES mapeada na base atual"
            lat = None
            lon = None
        else:
            qtd_esf = float(g["qtd_esf_num"].sum())
            qtd_esb = float(g["qtd_esb_num"].sum())
            qtd_ubs = int(len(g))
            try:
                ubs_ref = str(g["nome_unidade"].dropna().astype(str).iloc[0])
            except Exception:
                ubs_ref = "UBS/CNES mapeada"
            lat = g["lat_num"].dropna().mean() if g["lat_num"].dropna().shape[0] else None
            lon = g["lon_num"].dropna().mean() if g["lon_num"].dropna().shape[0] else None

        indice_vulnerabilidade_social = round((baixa_renda * 0.25) + (bpc * 0.20) + (renda_score * 0.25) + (baixa_escolaridade * 0.15) + (saneamento * 0.15), 2)
        indice_dependencia_sus = round((indice_vulnerabilidade_social * 0.70) + ((100 - plano_saude) * 0.30), 2)
        indice_socioeducacional = round((baixa_escolaridade * 0.60) + (indice_vulnerabilidade_social * 0.40), 2)
        indice_oferta_insuficiente = round(_score_pop_por_esf(populacao, qtd_esf), 2)
        indice_acesso_territorial = round((ruralidade * 0.65) + (10 * 0.35), 2)  # distância real pendente
        indice_demanda_sus = round(
            (indice_dependencia_sus * 0.35)
            + (indice_socioeducacional * 0.15)
            + (saneamento * 0.12)
            + (pressao_aps * 0.18)
            + (ruralidade * 0.12)
            + (indice_oferta_insuficiente * 0.08),
            2,
        )
        indice_vazio = round(
            (indice_oferta_insuficiente * 0.35)
            + (indice_dependencia_sus * 0.20)
            + (ruralidade * 0.15)
            + (saneamento * 0.12)
            + (pressao_aps * 0.10)
            + (indice_acesso_territorial * 0.08),
            2,
        )
        linhas.append({
            "municipio": municipio,
            "codigo_ibge": codigo,
            "bairro_ou_localidade": "Leitura municipal preliminar",
            "tipo_territorio": "Município - preliminar sem setores censitários",
            "resultado_tipo": "municipal_preliminar_sem_setores",
            "populacao": populacao,
            "latitude": lat,
            "longitude": lon,
            "ubs_mais_proxima": ubs_ref,
            "distancia_ubs_km": pd.NA,
            "distancia_pendente": True,
            "qtd_esf_ubs_proxima": qtd_esf,
            "qtd_esb_ubs_proxima": qtd_esb,
            "qtd_ubs_municipio": qtd_ubs,
            "indice_dependencia_sus_estimado": indice_dependencia_sus,
            "indice_vulnerabilidade_social": indice_vulnerabilidade_social,
            "indice_socioeducacional": indice_socioeducacional,
            "indice_acesso_territorial": indice_acesso_territorial,
            "indice_oferta_insuficiente": indice_oferta_insuficiente,
            "indice_demanda_sus_ajustada": indice_demanda_sus,
            "indice_vazio_assistencial": indice_vazio,
            "classificacao": _classificar_indice(indice_vazio),
            "recomendacao_tecnica": "Resultado municipal preliminar: validar setores censitários/população real para medir distância e vazio territorial. Enquanto isso, avaliar oferta de UBS/eSF/eSB e vulnerabilidades municipais.",
            "observacao_validacao": "Ranking preliminar gerado sem setores censitários; não mede distância real até UBS. Use para triagem inicial e não como diagnóstico territorial definitivo.",
        })
    out = pd.DataFrame(linhas)
    if not out.empty:
        out = out.sort_values(["indice_vazio_assistencial", "indice_demanda_sus_ajustada"], ascending=False)
    return out.reset_index(drop=True)


def _mostrar_resultado_preliminar_ou_cache(resultado: pd.DataFrame, ubs: pd.DataFrame, titulo: str = "Resultado disponível") -> None:
    st.success(titulo)
    _render_cards(resultado, ubs)
    st.dataframe(resultado, use_container_width=True, hide_index=True)
    st.download_button(
        "Exportar resultado disponível",
        data=_excel_bytes({"resultado_georreferenciamento": resultado, "ubs_cnes_ine": _normalizar_colunas(ubs)}),
        file_name="resultado_georreferenciamento_aps.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
    st.info(
        "Atenção: se este resultado estiver marcado como preliminar sem setores censitários, ele não mede distância real até UBS. "
        "Para completar o dashboard operacional com setores críticos e maiores distâncias, carregue a base territorial na aba 2."
    )

def calcular_vazios_assistenciais(territorios: pd.DataFrame, ubs: pd.DataFrame) -> pd.DataFrame:
    territorios = _normalizar_colunas(territorios)
    ubs = _normalizar_colunas(ubs) if ubs is not None else pd.DataFrame(columns=COLUNAS_UBS_MODELO)

    for col in COLUNAS_TERRITORIOS_MODELO:
        if col not in territorios.columns:
            territorios[col] = "" if col in ["municipio", "codigo_ibge", "bairro_ou_localidade", "tipo_territorio", "ubs_referencia", "observacao_validacao"] else 0
    for col in COLUNAS_UBS_MODELO:
        if col not in ubs.columns:
            ubs[col] = "" if col in ["municipio", "codigo_ibge", "cnes", "nome_unidade", "tipo_unidade", "bairro", "logradouro", "ines_vinculados", "fonte", "observacao_validacao"] else 0

    resultados = []
    for _, row in territorios.iterrows():
        item = row.to_dict()
        ubs_proxima, distancia, qtd_esf_proxima, qtd_esb_proxima = _encontrar_ubs_mais_proxima(item, ubs)

        baixa_renda = _clamp(item.get("percentual_baixa_renda"))
        bolsa = _clamp(item.get("percentual_bolsa_familia"))
        cadunico = _clamp(item.get("percentual_cadunico"))
        bpc = _clamp((_to_numero(item.get("percentual_bpc"), 0.0) or 0.0) * 8)  # BPC é percentual menor; escala técnica preliminar.
        baixa_escolaridade = _clamp(item.get("percentual_baixa_escolaridade"))
        saneamento = _clamp(item.get("percentual_saneamento_inadequado"))
        ruralidade = _clamp(item.get("percentual_rural"))
        pressao_aps = _clamp(item.get("indicador_pressao_aps"))
        plano_saude = _clamp(item.get("percentual_plano_saude_estimado"))
        renda_score = _score_renda(item.get("renda_media"))
        populacao = _to_numero(item.get("populacao"), 0.0) or 0.0

        indice_vulnerabilidade_social = round((baixa_renda * 0.25) + (bolsa * 0.20) + (cadunico * 0.25) + (bpc * 0.15) + (renda_score * 0.15), 2)
        indice_dependencia_sus = round((indice_vulnerabilidade_social * 0.70) + ((100 - plano_saude) * 0.30), 2)
        indice_socioeducacional = round((baixa_escolaridade * 0.60) + (indice_vulnerabilidade_social * 0.40), 2)
        indice_acesso_territorial = round((_score_distancia(distancia) * 0.60) + (ruralidade * 0.40), 2)
        indice_oferta_insuficiente = round(_score_pop_por_esf(populacao, qtd_esf_proxima), 2)
        indice_demanda_sus = round(
            (indice_dependencia_sus * 0.35)
            + (indice_socioeducacional * 0.15)
            + (saneamento * 0.12)
            + (pressao_aps * 0.18)
            + (ruralidade * 0.12)
            + (indice_oferta_insuficiente * 0.08),
            2,
        )
        indice_vazio = round(
            (indice_oferta_insuficiente * 0.25)
            + (indice_acesso_territorial * 0.20)
            + (indice_dependencia_sus * 0.20)
            + (ruralidade * 0.12)
            + (saneamento * 0.10)
            + (pressao_aps * 0.13),
            2,
        )
        deficit_oferta = qtd_esf_proxima <= 0 or _score_pop_por_esf(populacao, qtd_esf_proxima) >= 60

        resultados.append({
            "municipio": item.get("municipio", ""),
            "codigo_ibge": str(item.get("codigo_ibge", "")),
            "bairro_ou_localidade": item.get("bairro_ou_localidade", ""),
            "tipo_territorio": item.get("tipo_territorio", ""),
            "populacao": populacao,
            "latitude": item.get("latitude", None),
            "longitude": item.get("longitude", None),
            "ubs_mais_proxima": ubs_proxima,
            "distancia_ubs_km": distancia,
            "qtd_esf_ubs_proxima": qtd_esf_proxima,
            "qtd_esb_ubs_proxima": qtd_esb_proxima,
            "indice_dependencia_sus_estimado": indice_dependencia_sus,
            "indice_vulnerabilidade_social": indice_vulnerabilidade_social,
            "indice_socioeducacional": indice_socioeducacional,
            "indice_acesso_territorial": indice_acesso_territorial,
            "indice_oferta_insuficiente": indice_oferta_insuficiente,
            "indice_demanda_sus_ajustada": indice_demanda_sus,
            "indice_vazio_assistencial": indice_vazio,
            "classificacao": _classificar_indice(indice_vazio),
            "recomendacao_tecnica": _recomendacao(indice_vazio, indice_demanda_sus, deficit_oferta),
            "observacao_validacao": item.get("observacao_validacao", ""),
        })

    out = pd.DataFrame(resultados)
    if not out.empty:
        out = out.sort_values(["indice_vazio_assistencial", "indice_demanda_sus_ajustada"], ascending=False)
    return out.reset_index(drop=True)


def _serie_numerica_segura(df: pd.DataFrame, coluna: str) -> pd.Series:
    """Converte coluna em número sem quebrar quando o cache CSV devolve texto/objeto."""
    if df is None or df.empty or coluna not in df.columns:
        return pd.Series(dtype="float64")
    serie = df[coluna]
    return serie.map(lambda v: _to_numero(v, None)).astype("float64")


def _render_cards(resultado: pd.DataFrame, ubs: pd.DataFrame):
    c1, c2, c3, c4, c5 = st.columns(5)
    total = len(resultado) if resultado is not None else 0

    indice_vazio = _serie_numerica_segura(resultado, "indice_vazio_assistencial") if total else pd.Series(dtype="float64")
    indice_demanda = _serie_numerica_segura(resultado, "indice_demanda_sus_ajustada") if total else pd.Series(dtype="float64")

    criticos = int((indice_vazio >= 80).sum()) if total and not indice_vazio.empty else 0
    provaveis = int(((indice_vazio >= 60) & (indice_vazio < 80)).sum()) if total and not indice_vazio.empty else 0
    media_demanda = round(float(indice_demanda.dropna().mean()), 1) if total and not indice_demanda.dropna().empty else 0
    ubs_total = len(ubs) if ubs is not None else 0
    c1.metric("Territórios", total)
    c2.metric("Vazios críticos", criticos)
    c3.metric("Vazios prováveis", provaveis)
    c4.metric("Demanda média", media_demanda)
    c5.metric("UBS/USF mapeadas", ubs_total)


def _render_mapa(resultado: pd.DataFrame, ubs: pd.DataFrame):
    st.subheader("Mapa preliminar")
    st.caption("Usa coordenadas de UBS e territórios. Onde não houver latitude/longitude, o território entra apenas no ranking.")
    pontos = []
    if resultado is not None and not resultado.empty:
        for _, r in resultado.iterrows():
            lat = _to_numero(r.get("latitude"), None)
            lon = _to_numero(r.get("longitude"), None)
            if lat is not None and lon is not None and lat != 0 and lon != 0:
                pontos.append({"lat": lat, "lon": lon, "tipo": "Território", "nome": r.get("bairro_ou_localidade", "")})
    if ubs is not None and not ubs.empty:
        for _, u in _normalizar_colunas(ubs).iterrows():
            lat = _to_numero(u.get("latitude"), None)
            lon = _to_numero(u.get("longitude"), None)
            if lat is not None and lon is not None and lat != 0 and lon != 0:
                pontos.append({"lat": lat, "lon": lon, "tipo": "UBS/USF", "nome": u.get("nome_unidade", "")})
    if not pontos:
        st.info("Nenhum ponto com latitude/longitude válido foi encontrado. O ranking continua funcionando sem mapa.")
        return
    pts = pd.DataFrame(pontos)
    st.map(pts[["lat", "lon"]], latitude="lat", longitude="lon", size=42)
    with st.expander("Ver pontos do mapa"):
        st.dataframe(pts, use_container_width=True, hide_index=True)


def _carregar_base_municipal_para_territorio() -> pd.DataFrame:
    base = st.session_state.get("ubs_base_automatica_ibge")
    if base is None or not isinstance(base, pd.DataFrame) or base.empty:
        return pd.DataFrame(columns=COLUNAS_TERRITORIOS_MODELO)
    df = _normalizar_colunas(base)
    out = pd.DataFrame()
    out["municipio"] = df.get("municipio", "")
    out["codigo_ibge"] = df.get("codigo_ibge", "")
    out["bairro_ou_localidade"] = "Município - leitura agregada"
    out["tipo_territorio"] = "Agregado municipal"
    out["populacao"] = df.get("populacao", df.get("populacao_estimada", 0))
    out["latitude"] = ""
    out["longitude"] = ""
    out["renda_media"] = df.get("renda_media", df.get("rendimento_medio", 0))
    out["percentual_baixa_renda"] = df.get("percentual_baixa_renda", 0)
    out["percentual_bolsa_familia"] = df.get("percentual_bolsa_familia", 0)
    out["percentual_cadunico"] = df.get("percentual_cadunico", 0)
    out["percentual_bpc"] = df.get("percentual_bpc", 0)
    out["percentual_baixa_escolaridade"] = df.get("percentual_baixa_escolaridade", df.get("percentual_nao_alfabetizados", 0))
    out["percentual_saneamento_inadequado"] = df.get("percentual_saneamento_inadequado", 0)
    out["percentual_rural"] = df.get("percentual_rural", 0)
    out["indicador_pressao_aps"] = 0
    out["percentual_plano_saude_estimado"] = 0
    out["ubs_referencia"] = ""
    out["distancia_ubs_km"] = 0
    out["observacao_validacao"] = "Base municipal agregada; não substitui bairro/setor censitário."
    return out



def _primeiro_valor_coluna(row: pd.Series, candidatos: List[str], padrao: Any = 0) -> Any:
    """Busca o primeiro valor disponível em uma linha usando possíveis nomes de coluna."""
    for col in candidatos:
        chave = _normalizar_texto(col)
        if chave in row.index:
            valor = row.get(chave)
            try:
                if pd.notna(valor) and str(valor).strip() != "":
                    return valor
            except Exception:
                if valor not in [None, ""]:
                    return valor
    return padrao


def _base_municipal_apis_normalizada() -> pd.DataFrame:
    """Localiza a base municipal das APIs apenas para enriquecer indicadores.

    A partir da versão v12, esta função NÃO usa população municipal/SIDRA para
    preencher população de bairros/localidades. A população territorial deve vir
    de fonte real por setor censitário, bairro oficial, microárea ou base local
    validada. A base municipal pode enriquecer renda, saneamento, ruralidade e
    outros indicadores, mas não cria população por território.
    """
    chaves_possiveis = [
        "ubs_base_automatica_ibge",
        "base_automatica_ubs",
        "base_ubs_automatica",
        "df_ubs_base_automatica",
        "geo_base_municipal_df",
    ]

    for chave in chaves_possiveis:
        base = st.session_state.get(chave)
        if isinstance(base, pd.DataFrame) and not base.empty:
            df = _normalizar_colunas(base)
            if "codigo_ibge" not in df.columns:
                col_cod = _identificar_coluna(df, ["codigo_ibge", "cod_ibge", "municipio_ibge", "ibge"])
                if col_cod:
                    df["codigo_ibge"] = df[col_cod]
            if "municipio" not in df.columns:
                col_mun = _identificar_coluna(df, ["municipio", "nome_municipio", "no_municipio"])
                if col_mun:
                    df["municipio"] = df[col_mun]
            if "codigo_ibge" in df.columns:
                df["codigo_ibge"] = df["codigo_ibge"].astype(str).str.extract(r"(\d{6,7})")[0].fillna("")
                df = _enriquecer_municipio_por_codigo(df, "codigo_ibge", "municipio")
            return df

    return pd.DataFrame()

def _linha_indicadores_municipais(codigo_ibge: str, municipio: str, base_mun: pd.DataFrame) -> Optional[pd.Series]:
    if base_mun is None or base_mun.empty:
        return None
    df = base_mun.copy()
    codigo = str(codigo_ibge or "").strip()
    if codigo and "codigo_ibge" in df.columns:
        achou = df[df["codigo_ibge"].astype(str).str.startswith(codigo[:6], na=False)]
        if not achou.empty:
            return achou.iloc[0]
    mun = _normalizar_texto(municipio)
    if mun and "municipio" in df.columns:
        achou = df[df["municipio"].map(_normalizar_texto) == mun]
        if not achou.empty:
            return achou.iloc[0]
    return None


def _estimar_plano_saude_por_renda(renda_media: Any) -> float:
    renda = _to_numero(renda_media, 0.0) or 0.0
    if renda <= 0:
        return 0.0
    if renda >= 6000:
        return 55.0
    if renda >= 4000:
        return 35.0
    if renda >= 2500:
        return 20.0
    if renda >= 1500:
        return 10.0
    return 5.0


def _gerar_pre_base_territorial_por_cnes(ubs: pd.DataFrame) -> pd.DataFrame:
    """Gera estrutura territorial preliminar a partir do CNES, sem população estimada.

    Esta rotina serve apenas para listar bairros/localidades que aparecem no CNES
    e apoiar a validação territorial. Ela NÃO distribui população municipal e NÃO
    gera dado populacional por proxy. Para o ranking oficial, a aba 3 exige
    população real por setor censitário, bairro oficial, microárea ou base local
    validada.
    """
    if ubs is None or not isinstance(ubs, pd.DataFrame) or ubs.empty:
        return pd.DataFrame(columns=COLUNAS_TERRITORIOS_MODELO)

    df = _normalizar_colunas(ubs)
    for col in ["municipio", "codigo_ibge", "bairro", "nome_unidade", "tipo_unidade", "latitude", "longitude", "qtd_esf", "qtd_esb"]:
        if col not in df.columns:
            df[col] = "" if col not in ["latitude", "longitude", "qtd_esf", "qtd_esb"] else 0

    df = _enriquecer_municipio_por_codigo(df, "codigo_ibge", "municipio")
    df["bairro"] = df["bairro"].fillna("").astype(str).str.strip()
    df.loc[df["bairro"].eq(""), "bairro"] = "Localidade não informada no CNES"
    df["qtd_esf"] = df["qtd_esf"].map(lambda v: _to_numero(v, 0) or 0)
    df["qtd_esb"] = df["qtd_esb"].map(lambda v: _to_numero(v, 0) or 0)
    df["lat_num"] = df["latitude"].map(lambda v: _to_numero(v, None))
    df["lon_num"] = df["longitude"].map(lambda v: _to_numero(v, None))

    grupos = []
    chaves = ["codigo_ibge", "municipio", "bairro"]
    for (codigo, municipio, bairro), g in df.groupby(chaves, dropna=False):
        lat = g["lat_num"].dropna().mean() if g["lat_num"].dropna().shape[0] else ""
        lon = g["lon_num"].dropna().mean() if g["lon_num"].dropna().shape[0] else ""
        qtd_ubs = len(g)
        qtd_esf = int(pd.to_numeric(g["qtd_esf"], errors="coerce").fillna(0).sum())
        qtd_esb = int(pd.to_numeric(g["qtd_esb"], errors="coerce").fillna(0).sum())
        rural = 100.0 if any(t in _normalizar_texto(str(v)) for v in list(g["bairro"]) + list(g["tipo_unidade"]) for t in ["rural", "zona_rural", "assent", "aldeia", "comun"]) else 0.0
        tipo_territorio = "Rural/localidade dispersa" if rural >= 100 else "Bairro/localidade CNES"
        ubs_ref = str(g["nome_unidade"].dropna().astype(str).iloc[0]) if not g["nome_unidade"].dropna().empty else ""
        grupos.append({
            "municipio": municipio,
            "codigo_ibge": codigo,
            "bairro_ou_localidade": bairro,
            "tipo_territorio": tipo_territorio,
            "populacao": 0,
            "latitude": lat,
            "longitude": lon,
            "renda_media": 0,
            "percentual_baixa_renda": 0,
            "percentual_bolsa_familia": 0,
            "percentual_cadunico": 0,
            "percentual_bpc": 0,
            "percentual_baixa_escolaridade": 0,
            "percentual_saneamento_inadequado": 0,
            "percentual_rural": rural,
            "indicador_pressao_aps": 0,
            "percentual_plano_saude_estimado": 0,
            "ubs_referencia": ubs_ref,
            "distancia_ubs_km": 0,
            "qtd_ubs_cnes_no_territorio": qtd_ubs,
            "qtd_esf_no_territorio": qtd_esf,
            "qtd_esb_no_territorio": qtd_esb,
            "fonte_populacao": "Sem população real carregada",
            "observacao_validacao": "Estrutura gerada a partir do CNES. Não contém população real; carregar setores censitários, bairros oficiais ou microáreas para calcular ranking.",
        })

    out = pd.DataFrame(grupos)
    if out.empty:
        return pd.DataFrame(columns=COLUNAS_TERRITORIOS_MODELO)

    base_mun = _base_municipal_apis_normalizada()
    # Enriquecimento por município com indicadores das APIs já carregadas, sem criar população territorial.
    for idx, row in out.iterrows():
        info = _linha_indicadores_municipais(str(row.get("codigo_ibge", "")), str(row.get("municipio", "")), base_mun)
        if info is None:
            continue
        renda = _to_numero(_primeiro_valor_coluna(info, ["renda_media", "rendimento_medio", "renda_domiciliar_media"], 0), 0) or 0
        out.at[idx, "renda_media"] = renda
        out.at[idx, "percentual_baixa_renda"] = _to_numero(_primeiro_valor_coluna(info, ["percentual_baixa_renda", "baixa_renda_percentual", "perc_baixa_renda"], 0), 0) or 0
        out.at[idx, "percentual_bolsa_familia"] = _to_numero(_primeiro_valor_coluna(info, ["percentual_bolsa_familia", "bolsa_familia_percentual", "perc_bolsa_familia"], 0), 0) or 0
        out.at[idx, "percentual_cadunico"] = _to_numero(_primeiro_valor_coluna(info, ["percentual_cadunico", "cadunico_percentual", "perc_cadunico"], 0), 0) or 0
        out.at[idx, "percentual_bpc"] = _to_numero(_primeiro_valor_coluna(info, ["percentual_bpc", "bpc_total_por_1000_hab"], 0), 0) or 0
        out.at[idx, "percentual_baixa_escolaridade"] = _to_numero(_primeiro_valor_coluna(info, ["percentual_baixa_escolaridade", "percentual_nao_alfabetizados", "nao_alfabetizados_percentual", "taxa_analfabetismo"], 0), 0) or 0
        out.at[idx, "percentual_saneamento_inadequado"] = _to_numero(_primeiro_valor_coluna(info, ["percentual_saneamento_inadequado", "saneamento_inadequado_percentual", "percentual_sem_esgoto", "percentual_esgoto_inadequado"], 0), 0) or 0
        if out.at[idx, "percentual_rural"] <= 0:
            out.at[idx, "percentual_rural"] = _to_numero(_primeiro_valor_coluna(info, ["percentual_rural", "populacao_rural_percentual", "perc_rural"], 0), 0) or 0
        out.at[idx, "indicador_pressao_aps"] = _to_numero(_primeiro_valor_coluna(info, ["indicador_pressao_aps", "indice_pressao_aps", "score_pressao_aps"], 0), 0) or 0
        out.at[idx, "percentual_plano_saude_estimado"] = _estimar_plano_saude_por_renda(renda)

    for col in COLUNAS_TERRITORIOS_MODELO:
        if col not in out.columns:
            out[col] = "" if col in ["municipio", "codigo_ibge", "bairro_ou_localidade", "tipo_territorio", "ubs_referencia", "observacao_validacao"] else 0
    extras = [c for c in out.columns if c not in COLUNAS_TERRITORIOS_MODELO]
    return out[COLUNAS_TERRITORIOS_MODELO + extras].reset_index(drop=True)


def _tem_populacao_real_territorial(territorios: pd.DataFrame) -> Tuple[bool, float, int]:
    """Verifica se a base territorial possui população real informada.

    A validação considera população > 0. Se a coluna fonte_populacao ou
    observacao_validacao indicar proxy/estimativa distribuída, bloqueia o cálculo.
    """
    if territorios is None or not isinstance(territorios, pd.DataFrame) or territorios.empty:
        return False, 0.0, 0
    df = _normalizar_colunas(territorios)
    if "populacao" not in df.columns:
        return False, 0.0, len(df)
    pop = pd.to_numeric(df["populacao"], errors="coerce").fillna(0)
    total = float(pop.sum())
    linhas_com_pop = int((pop > 0).sum())
    texto_fontes = " ".join(
        df[[c for c in ["fonte_populacao", "observacao_validacao"] if c in df.columns]]
        .astype(str)
        .head(500)
        .fillna("")
        .agg(" ".join, axis=1)
        .tolist()
    ).lower()
    termos_bloqueio = ["proxy", "distribu", "estimativa municipal", "sem população real", "agregado municipal", "base municipal agregada"]
    if any(t in texto_fontes for t in termos_bloqueio):
        return False, total, linhas_com_pop
    return total > 0 and linhas_com_pop > 0, total, linhas_com_pop

def _render_metodologia():
    st.markdown(
        """
        ### Lógica da automação

        A tela trabalha com a seguinte arquitetura:

        **1. Oferta georreferenciada:** UBS/USF/Postos de Saúde extraídos do CNES, com CNES, nome, endereço, bairro e coordenadas quando a base pública trouxer latitude/longitude.

        **2. Equipes/INE:** tentativa de carregar a base pública de equipes CNES/INE e consolidar quantas eSF/eSB estão vinculadas a cada estabelecimento.

        **3. Território de demanda:** setores censitários, bairros oficiais, microáreas ou localidades com população real informada. A estrutura gerada pelo CNES serve apenas para validação de nomes e oferta, sem estimar população. O ranking oficial só roda quando houver população territorial real carregada.

        **4. Índice de Demanda SUS Ajustada:** estima onde a população tende a depender mais do SUS. A lógica reduz o peso relativo de áreas com maior renda/plano estimado e aumenta o peso de baixa renda, CadÚnico/Bolsa Família/BPC, baixa escolaridade, saneamento ruim, ruralidade e pressão assistencial.

        **5. Índice de Vazio Assistencial:** cruza demanda ajustada, distância até UBS, ruralidade, saneamento, oferta insuficiente de eSF/eSB e pressão assistencial.

        A automação é híbrida: o CNES/INE entra automaticamente quando a fonte pública estiver acessível; a camada fina de bairro/microárea deve ser alimentada por setores censitários IBGE, planilha municipal oficial ou base local validada pela APS/ERS. O sistema bloqueia o ranking quando a população territorial não é real.
        """
    )



def _garantir_vinculo_cnes_ine_persistente(salvar: bool = False) -> tuple[bool, str]:
    """Reaplica o vínculo CNES + INE quando as duas bases já existem.

    Esse ajuste evita que a base EQUIPES BRASIL pareça "sumir" ao trocar de tela
    ou quando a base de UBS/CNES é recarregada sem os campos qtd_esf, qtd_esb e
    ines_vinculados. A função não depende do file_uploader; ela usa o que está no
    session_state/cache.
    """
    ubs = st.session_state.get("geo_ubs_df")
    equipes = st.session_state.get("geo_equipes_ine_df")

    if not isinstance(ubs, pd.DataFrame) or ubs.empty:
        return False, "Base UBS/CNES ainda não carregada."
    if not isinstance(equipes, pd.DataFrame) or equipes.empty:
        return False, "Base EQUIPES BRASIL/INE ainda não carregada."

    # Verifica se a base de UBS já tem vínculo útil. Se não tiver, reprocessa.
    precisa_recruzar = False
    for col in ["qtd_esf", "qtd_esb", "ines_vinculados"]:
        if col not in ubs.columns:
            precisa_recruzar = True
            break

    if not precisa_recruzar:
        try:
            qtd_esf = pd.to_numeric(ubs.get("qtd_esf", 0), errors="coerce").fillna(0).sum()
            qtd_esb = pd.to_numeric(ubs.get("qtd_esb", 0), errors="coerce").fillna(0).sum()
            ines_preenchidos = ubs.get("ines_vinculados", pd.Series(dtype=str)).astype(str).str.strip().replace("nan", "").ne("").sum()
            if int(qtd_esf + qtd_esb) <= 0 and int(ines_preenchidos) <= 0:
                precisa_recruzar = True
        except Exception:
            precisa_recruzar = True

    if not precisa_recruzar:
        return False, "Vínculo CNES + INE já estava aplicado."

    try:
        st.session_state["geo_equipes_ine_df"] = filtrar_equipes_ine_aps(equipes)
        st.session_state["geo_ubs_df"] = juntar_ubs_equipes(ubs, st.session_state["geo_equipes_ine_df"])
        if salvar:
            salvar_cache_aps(
                keys=["geo_equipes_ine_df", "geo_ubs_df"],
                origem="reaplicação automática do vínculo CNES + INE no Georreferenciamento APS",
            )
        return True, "Vínculo CNES + INE reaplicado automaticamente."
    except Exception as exc:
        return False, f"Não foi possível reaplicar o vínculo CNES + INE: {exc}"

def render_georreferenciamento_aps():
    st.title("Georreferenciamento APS")
    st.caption("Automação preliminar para mapear UBS/equipes e identificar vazios assistenciais por bairro, localidade ou território.")

    # Se CNES e EQUIPES BRASIL já estiverem no cache/session_state, reatribui o vínculo
    # sempre que necessário. Isso impede que a leitura do upload fique presa apenas ao
    # componente file_uploader, que some visualmente quando o usuário troca de tela.
    vinculo_reaplicado, msg_vinculo = _garantir_vinculo_cnes_ine_persistente(salvar=True)
    if vinculo_reaplicado:
        st.success(msg_vinculo)

    st.markdown(
        """
        <div class="info-box">
        <b>Ideia central:</b> a metodologia federal mostra a cobertura populacional, mas o georreferenciamento mostra o acesso real. 
        Bairros nobres podem ter população relevante e baixa dependência do SUS, enquanto bairros vulneráveis podem gerar demanda muito maior sobre a APS.
        </div>
        """,
        unsafe_allow_html=True,
    )

    aba1, aba2, aba3, aba4 = st.tabs([
        "1. CNES/INE automático",
        "2. Setores IBGE/população real",
        "3. Ranking e mapa",
        "4. Metodologia",
    ])

    with aba1:
        st.subheader("Oferta georreferenciada: UBS, CNES e INE")
        st.caption("O sistema tenta automatizar a base CNES/INE. Quando a fonte não trouxer coordenadas confiáveis, a tela sinaliza necessidade de validação ou complementação.")
        st.info("Fluxo validado nesta versão: carregar UBS/USF pelo CNES e subir manualmente o arquivo EQUIPES BRASIL. Os botões automáticos de INE/CNES-DATASUS foram mantidos desabilitados para não confundir a operação.")

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            if st.button("Carregar UBS/USF pelo CNES", use_container_width=True):
                try:
                    with st.spinner("Baixando e filtrando CNES de Mato Grosso..."):
                        ubs = carregar_cnes_ubs_geo_mt()
                    st.session_state["geo_ubs_df"] = ubs
                    # Se as equipes já estavam salvas/carregadas, recarregar UBS não pode apagar o vínculo.
                    _garantir_vinculo_cnes_ine_persistente(salvar=False)
                    salvar_cache_aps(keys=["geo_ubs_df", "geo_equipes_ine_df"], origem="carga UBS/USF pelo CNES no Georreferenciamento APS")
                    total = len(st.session_state.get("geo_ubs_df", ubs))
                    coords = int(ubs.get("coordenada_valida", pd.Series(dtype=bool)).sum()) if "coordenada_valida" in ubs.columns else 0
                    st.success(f"CNES carregado: {total} unidades APS compatíveis. Coordenadas válidas identificadas: {coords}. Base salva no cache local.")
                    if coords == 0:
                        st.warning("A base carregada não trouxe coordenadas válidas. Será necessário complementar latitude/longitude ou usar outra extração CNES com geolocalização.")
                except Exception as exc:
                    st.error(f"Falha ao carregar CNES: {exc}")
        with col2:
            if st.button("Carregar equipes INE automático (indisponível)", use_container_width=True, disabled=True):
                try:
                    with st.spinner("Tentando carregar base pública de equipes CNES/INE..."):
                        equipes = carregar_cnes_equipes_ine_mt()
                    equipes = filtrar_equipes_ine_aps(equipes)
                    st.session_state["geo_equipes_ine_df"] = equipes
                    salvar_cache_aps(keys=["geo_equipes_ine_df"], origem="carga automática de equipes INE no Georreferenciamento APS")
                    st.success(f"Equipes carregadas: {len(equipes)} registros e salvas no cache local. Agora clique em Vincular CNES + INE.")
                    if st.session_state.get("geo_ine_url_usada"):
                        st.caption(f"Fonte utilizada: {st.session_state.get('geo_ine_url_usada')}")
                except Exception as exc:
                    st.warning("Tentativa automática de equipes/INE executada, mas a fonte pública não liberou o arquivo neste momento.")
                    st.caption(str(exc))
                    st.info(
                        "A tentativa automática será mantida no sistema. Quando o OpenDataSUS/CKAN voltar a liberar o arquivo de equipes, "
                        "este mesmo botão deverá carregar os INEs. Enquanto isso, a base de UBS/CNES já está válida para a oferta georreferenciada. "
                        "Para não travar o projeto, complemente temporariamente as colunas qtd_esf, qtd_esb e ines_vinculados pela planilha UBS/CNES/INE."
                    )
                    with st.expander("Diagnóstico técnico da tentativa automática de equipes/INE"):
                        urls = st.session_state.get("geo_ine_urls_tentadas", [])
                        if urls:
                            st.write(pd.DataFrame({"url_tentada": urls}))
                            st.download_button(
                                "Baixar diagnóstico das URLs tentadas",
                                data=pd.DataFrame({"url_tentada": urls}).to_csv(index=False).encode("utf-8-sig"),
                                file_name="diagnostico_urls_ine_tentadas.csv",
                                mime="text/csv",
                                use_container_width=True,
                            )
                        else:
                            st.caption("Nenhuma URL foi registrada nesta tentativa.")
                        st.markdown(
                            "Fonte oficial relacionada: DATASUS/CNES mantém consulta de **Equipes de Saúde** e o CNES utiliza o "
                            "**Identificador Nacional de Equipes (INE)** como código individual da equipe. O problema atual é de acesso "
                            "ao arquivo público massivo, não de conceito metodológico."
                        )
        with col3:
            if st.button("CNES/DATASUS oficial automático (indisponível)", use_container_width=True, disabled=True):
                try:
                    with st.spinner("Tentando localizar e carregar equipes/INE pela base oficial CNES/DATASUS..."):
                        equipes = carregar_equipes_ine_cnes_datasus_oficial()
                    equipes = filtrar_equipes_ine_aps(equipes)
                    st.session_state["geo_equipes_ine_df"] = equipes
                    salvar_cache_aps(keys=["geo_equipes_ine_df"], origem="carga CNES/DATASUS oficial no Georreferenciamento APS")
                    st.success(f"Equipes carregadas via CNES/DATASUS oficial: {len(equipes)} registros e salvas no cache local. Agora clique em Vincular CNES + INE.")
                    if st.session_state.get("geo_ine_cnes_datasus_url_usada"):
                        st.caption(f"Fonte utilizada: {st.session_state.get('geo_ine_cnes_datasus_url_usada')}")
                except Exception as exc:
                    st.warning("Não foi possível automatizar o CNES/DATASUS oficial nesta tentativa.")
                    st.caption(str(exc))
                    st.info(
                        "O caminho oficial continua sendo o preferencial. Quando o site exigir seleção manual, baixe o arquivo EQUIPES BRASIL/Base de Equipes no CNES/DATASUS, "
                        "exporte para CSV/TXT/XLSX se necessário e envie no campo de upload abaixo."
                    )
                    with st.expander("Diagnóstico técnico CNES/DATASUS oficial"):
                        urls = st.session_state.get("geo_ine_cnes_datasus_urls_tentadas", [])
                        if urls:
                            st.write(pd.DataFrame({"url_tentada": urls}))
                            st.download_button(
                                "Baixar diagnóstico CNES/DATASUS oficial",
                                data=pd.DataFrame({"url_tentada": urls}).to_csv(index=False).encode("utf-8-sig"),
                                file_name="diagnostico_urls_cnes_datasus_oficial.csv",
                                mime="text/csv",
                                use_container_width=True,
                            )
                        erro = st.session_state.get("geo_ine_cnes_datasus_ultimo_erro", "")
                        if erro:
                            st.code(erro)
        with col4:
            if st.button("Vincular CNES + INE", use_container_width=True):
                ubs = st.session_state.get("geo_ubs_df")
                equipes = st.session_state.get("geo_equipes_ine_df")
                if ubs is None or not isinstance(ubs, pd.DataFrame) or ubs.empty:
                    st.warning("Carregue primeiro a base de UBS/USF pelo CNES ou faça upload da planilha de UBS.")
                else:
                    st.session_state["geo_ubs_df"] = juntar_ubs_equipes(ubs, equipes)
                    salvar_cache_aps(origem="vínculo CNES + INE no Georreferenciamento APS")
                    st.success("Base de UBS atualizada com equipes/INE e salva no cache local.")

        with st.expander("Caminho oficial: enviar arquivo EQUIPES BRASIL / base de equipes do CNES/DATASUS"):
            st.markdown(
                "Use este campo quando o site do CNES exigir download manual. Envie CSV, TXT, XLSX ou ZIP contendo arquivo tabular com, no mínimo, CNES e INE. "
                "Se o ZIP oficial vier em DBF/DBC, exporte/converta para CSV antes de enviar."
            )
            arq_eq_oficial = st.file_uploader(
                "Upload EQUIPES BRASIL / equipes CNES-DATASUS (.zip, .csv, .txt, .xlsx)",
                type=["zip", "csv", "txt", "xlsx", "xls"],
                key="geo_upload_equipes_cnes_oficial",
            )
            if arq_eq_oficial is not None:
                try:
                    bruto_eq = _ler_upload_equipes_cnes_oficial(arq_eq_oficial)
                    equipes = _normalizar_equipes_cnes_oficial(bruto_eq, fonte=f"CNES/DATASUS oficial - upload: {arq_eq_oficial.name}")
                    equipes = filtrar_equipes_ine_aps(equipes)
                    st.session_state["geo_equipes_ine_df"] = equipes

                    profissionais = _ler_upload_profissionais_equipes_brasil(arq_eq_oficial)
                    if isinstance(profissionais, pd.DataFrame) and not profissionais.empty:
                        st.session_state["geo_profissionais_equipes_df"] = profissionais
                        resumo_profissionais = consolidar_profissionais_por_equipe(equipes, profissionais)
                        st.session_state["geo_profissionais_ine_resumo_df"] = resumo_profissionais
                    else:
                        resumo_profissionais = pd.DataFrame()

                    # Se a base UBS/CNES já estiver carregada, vincula automaticamente.
                    # Isso evita o problema de o usuário subir o EQUIPES BRASIL, sair da tela
                    # e o dashboard ainda enxergar a base de UBS sem os INEs vinculados.
                    ubs_atual = st.session_state.get("geo_ubs_df")
                    vinculou_automatico = False
                    if isinstance(ubs_atual, pd.DataFrame) and not ubs_atual.empty:
                        st.session_state["geo_ubs_df"] = juntar_ubs_equipes(ubs_atual, equipes)
                        vinculou_automatico = True

                    salvar_cache_aps(
                        keys=["geo_equipes_ine_df", "geo_profissionais_equipes_df", "geo_profissionais_ine_resumo_df", "geo_ubs_df"],
                        origem=f"upload {arq_eq_oficial.name} no Georreferenciamento APS",
                    )
                    st.session_state["geo_equipes_ine_ultima_fonte"] = arq_eq_oficial.name
                    st.session_state["geo_equipes_ine_total_upload"] = int(len(equipes))

                    if vinculou_automatico:
                        extra_prof = f" Profissionais vinculados identificados: {len(profissionais)} registros." if isinstance(profissionais, pd.DataFrame) and not profissionais.empty else ""
                        st.success(
                            f"Equipes/INE carregadas do arquivo oficial: {len(equipes)} registros." + extra_prof +
                            " A base UBS/CNES foi vinculada automaticamente e tudo foi salvo no cache local."
                        )
                    else:
                        extra_prof = f" Profissionais vinculados identificados: {len(profissionais)} registros." if isinstance(profissionais, pd.DataFrame) and not profissionais.empty else ""
                        st.success(
                            f"Equipes/INE carregadas do arquivo oficial: {len(equipes)} registros e salvas no cache local." + extra_prof +
                            " Agora carregue a base UBS/CNES e clique em Vincular CNES + INE."
                        )
                    if isinstance(resumo_profissionais, pd.DataFrame) and not resumo_profissionais.empty:
                        st.caption("Resumo de profissionais por CNES/INE criado em: geo_profissionais_ine_resumo_df")
                        st.dataframe(
                            resumo_profissionais[[c for c in ["municipio", "cnes", "ine", "tipo_equipe_codigo", "tipo_equipe", "nome_equipe", "total_profissionais", "cbos_distintos"] if c in resumo_profissionais.columns]].head(100),
                            use_container_width=True,
                            hide_index=True,
                        )
                    else:
                        st.dataframe(equipes.head(100), use_container_width=True, hide_index=True)
                except Exception as exc:
                    st.error(f"Falha ao processar arquivo oficial de equipes: {exc}")

        with st.expander("Alternativa: carregar planilha própria de UBS/CNES/INE"):
            st.download_button(
                "Baixar modelo UBS/CNES/INE",
                data=_excel_bytes({"ubs": _template_ubs()}),
                file_name="modelo_ubs_cnes_ine_georreferenciamento.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
            arq_ubs = st.file_uploader("Upload UBS/CNES/INE (.xlsx ou .csv)", type=["xlsx", "xls", "csv"], key="geo_upload_ubs")
            if arq_ubs is not None:
                try:
                    st.session_state["geo_ubs_df"] = _normalizar_colunas(_ler_upload(arq_ubs))
                    salvar_cache_aps(keys=["geo_ubs_df"], origem="upload de planilha própria UBS/CNES/INE no Georreferenciamento APS")
                    st.success("Planilha de UBS/CNES/INE carregada e salva no cache local.")
                except Exception as exc:
                    st.error(f"Falha ao carregar planilha: {exc}")

        if st.checkbox("Usar UBS demonstrativas de Canarana", value=False):
            st.session_state["geo_ubs_df"] = _template_ubs()
            salvar_cache_aps(keys=["geo_ubs_df"], origem="UBS demonstrativas de Canarana carregadas no Georreferenciamento APS")
            st.success("UBS demonstrativas carregadas e salvas no cache local.")

        ubs_preview = st.session_state.get("geo_ubs_df")
        if isinstance(ubs_preview, pd.DataFrame) and not ubs_preview.empty:
            st.markdown("#### Prévia da base de oferta")
            cols = [c for c in ["municipio", "codigo_ibge", "cnes", "nome_unidade", "tipo_unidade", "bairro", "latitude", "longitude", "qtd_esf", "qtd_esb", "ines_vinculados", "fonte"] if c in _normalizar_colunas(ubs_preview).columns]
            st.dataframe(_normalizar_colunas(ubs_preview)[cols].head(200), use_container_width=True, hide_index=True)
            st.download_button(
                "Exportar base UBS/CNES/INE",
                data=_excel_bytes({"ubs_cnes_ine": _normalizar_colunas(ubs_preview)}),
                file_name="ubs_cnes_ine_georreferenciamento.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

    with aba2:
        st.subheader("População real: setores censitários IBGE/Censo 2022")
        st.caption("Caminho principal: baixar automaticamente a população real por setor censitário do IBGE e cruzar com a oferta CNES/INE. Não há proxy municipal.")

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            if st.button("Carregar setores IBGE 2022 - MT", use_container_width=True):
                with st.spinner("Buscando população real por setor censitário no IBGE/Censo 2022 e tentando carregar a malha/centroides..."):
                    setores = carregar_setores_ibge_2022_mt_completo()
                if setores.empty:
                    st.error("Não foi possível carregar automaticamente os setores censitários do IBGE neste momento.")
                    diag_pop = st.session_state.get("geo_ibge_setores_pop_diagnostico", {})
                    diag_malha = st.session_state.get("geo_ibge_malha_diagnostico", {})
                    with st.expander("Diagnóstico das tentativas IBGE"):
                        st.write("População/agregados:")
                        st.json(diag_pop)
                        if st.session_state.get("geo_ibge_setores_pop_erro_processamento"):
                            st.warning("Erro de processamento da população/agregados:")
                            st.write(st.session_state.get("geo_ibge_setores_pop_erro_processamento"))
                        if st.session_state.get("geo_ibge_setores_pop_parse_diagnostico"):
                            st.write("Diagnóstico de leitura dos arquivos do ZIP:")
                            st.json(st.session_state.get("geo_ibge_setores_pop_parse_diagnostico"))
                        if st.session_state.get("geo_ibge_setores_pop_parse_tentativas"):
                            st.write("Tentativas de identificação de colunas:")
                            st.json(st.session_state.get("geo_ibge_setores_pop_parse_tentativas"))
                        st.write("Malha/centroides:")
                        st.json(diag_malha)
                        if st.session_state.get("geo_ibge_malha_erro_dependencia"):
                            st.warning("Dependência geoespacial ausente: adicione geopandas/pyogrio/shapely ao requirements.txt para ler a malha automaticamente.")
                            st.code("geopandas\npyogrio\nshapely", language="text")
                else:
                    st.session_state["geo_territorios_df"] = setores
                    salvar_cache_aps(origem="setores IBGE 2022 carregados no Georreferenciamento APS")
                    ok_pop, total_pop, linhas_pop = _tem_populacao_real_territorial(setores)
                    st.success(f"Setores censitários carregados: {len(setores):,} registros; população total: {total_pop:,.0f}. Base salva no cache local.")
                    if {"latitude", "longitude"}.issubset(setores.columns):
                        coords_validas = ((pd.to_numeric(setores["latitude"], errors="coerce").fillna(0) != 0) & (pd.to_numeric(setores["longitude"], errors="coerce").fillna(0) != 0)).sum()
                        if coords_validas == 0:
                            st.warning("A população real foi carregada, mas a malha/centroides não veio. O ranking ficará sem cálculo adequado de distância até UBS enquanto a malha não for carregada.")
                        else:
                            st.info(f"Setores com coordenadas/centroides: {coords_validas:,}.")
        with c2:
            if st.button("Gerar estrutura CNES sem população", use_container_width=True):
                ubs_atual = st.session_state.get("geo_ubs_df")
                if not isinstance(ubs_atual, pd.DataFrame) or ubs_atual.empty:
                    st.warning("Carregue primeiro a base de UBS/CNES/INE na aba 1.")
                else:
                    pre_base = _gerar_pre_base_territorial_por_cnes(ubs_atual)
                    if pre_base.empty:
                        st.warning("Não foi possível gerar pré-base territorial a partir do CNES.")
                    else:
                        st.session_state["geo_territorios_df"] = pre_base
                        salvar_cache_aps(origem="estrutura territorial CNES sem população gerada no Georreferenciamento APS")
                        st.success(f"Estrutura territorial gerada a partir do CNES: {len(pre_base)} bairros/localidades para conferência. Base salva no cache local.")
                        st.warning("Esta estrutura NÃO contém população real e não será usada para ranking oficial. Use apenas para conferir nomes de bairros/localidades do CNES.")
        with c3:
            st.download_button(
                "Baixar modelo fallback",
                data=_excel_bytes({"territorios": _template_territorios()}),
                file_name="modelo_fallback_populacao_real_aps.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        with c4:
            if st.button("Limpar territórios", use_container_width=True):
                st.session_state.pop("geo_territorios_df", None)
                st.success("Base territorial removida da sessão.")

        # Diagnóstico sempre visível após uma tentativa IBGE.
        # Nas versões anteriores, o diagnóstico só aparecia quando TODO o carregamento falhava.
        # Agora ele também aparece quando a população vem, mas a malha/centroides não vem.
        diag_pop = st.session_state.get("geo_ibge_setores_pop_diagnostico", {})
        diag_malha = st.session_state.get("geo_ibge_malha_diagnostico", {})
        tem_diag_ibge = bool(diag_pop) or bool(diag_malha) or bool(st.session_state.get("geo_ibge_setores_pop_parse_diagnostico"))
        if tem_diag_ibge:
            with st.expander("Diagnóstico das tentativas IBGE", expanded=True):
                st.write("População/agregados:")
                st.json(diag_pop if diag_pop else {"observacao": "Sem diagnóstico registrado para população/agregados nesta sessão."})

                if st.session_state.get("geo_ibge_setores_pop_erro_processamento"):
                    st.warning("Erro de processamento da população/agregados:")
                    st.write(st.session_state.get("geo_ibge_setores_pop_erro_processamento"))

                if st.session_state.get("geo_ibge_setores_pop_parse_diagnostico"):
                    st.write("Diagnóstico de leitura dos arquivos do ZIP:")
                    st.json(st.session_state.get("geo_ibge_setores_pop_parse_diagnostico"))

                if st.session_state.get("geo_ibge_setores_pop_parse_tentativas"):
                    st.write("Tentativas de identificação de colunas:")
                    st.json(st.session_state.get("geo_ibge_setores_pop_parse_tentativas"))

                st.write("Malha/centroides:")
                st.json(diag_malha if diag_malha else {"observacao": "Sem diagnóstico registrado para malha/centroides nesta sessão."})

                if st.session_state.get("geo_ibge_malha_erro_dependencia"):
                    st.warning("Dependência geoespacial ausente: adicione geopandas/pyogrio/shapely ao requirements.txt para ler a malha automaticamente.")
                    st.code("geopandas\npyogrio\nshapely", language="text")

        with st.expander("Metodologia da automação IBGE", expanded=False):
            st.markdown(
                """
                **Regra:** o ranking não usa população por proxy.

                O caminho principal agora é: **IBGE/Censo 2022 → agregados por setores censitários → população real por setor → malha/centroide do setor → distância até UBS/CNES/INE**.

                Se o IBGE ou o ambiente local não permitir a leitura automática da malha, o sistema apresenta diagnóstico. Para calcular distância com precisão, a malha setorial exige biblioteca geoespacial no ambiente (`geopandas`, `pyogrio`, `shapely`).
                """
            )

        arq_territorios = st.file_uploader("Upload população real por setor/bairro/microárea/localidade (.xlsx ou .csv)", type=["xlsx", "xls", "csv"], key="geo_upload_territorios")
        if arq_territorios is not None:
            try:
                territorios_upload = _normalizar_colunas(_ler_upload(arq_territorios))
                st.session_state["geo_territorios_df"] = territorios_upload
                salvar_cache_aps(origem="upload de base territorial no Georreferenciamento APS")
                ok_pop, total_pop, linhas_pop = _tem_populacao_real_territorial(territorios_upload)
                if ok_pop:
                    st.success(f"Base territorial com população real carregada: {linhas_pop} território(s), população total {total_pop:,.0f}.")
                else:
                    st.warning("Base carregada, mas ainda não há população real válida. O ranking ficará bloqueado até a coluna populacao estar preenchida com dados reais.")
            except Exception as exc:
                st.error(f"Falha ao carregar base territorial: {exc}")

        territorios_preview = st.session_state.get("geo_territorios_df")
        if isinstance(territorios_preview, pd.DataFrame) and not territorios_preview.empty:
            st.markdown("#### Prévia da base territorial")
            st.dataframe(_normalizar_colunas(territorios_preview).head(200), use_container_width=True, hide_index=True)
            st.download_button(
                "Exportar base territorial carregada",
                data=_excel_bytes({"territorios": _normalizar_colunas(territorios_preview)}),
                file_name="territorios_georreferenciamento_aps.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

    with aba3:
        st.subheader("Ranking de vazios assistenciais")
        ubs = st.session_state.get("geo_ubs_df")
        territorios = st.session_state.get("geo_territorios_df")

        if not isinstance(ubs, pd.DataFrame) or ubs.empty:
            st.warning("Carregue a base de UBS/CNES/INE na aba 1.")
            return
        if not isinstance(territorios, pd.DataFrame) or territorios.empty:
            resultado_cache = st.session_state.get("geo_resultado_df")
            tem_cache = isinstance(resultado_cache, pd.DataFrame) and not resultado_cache.empty

            st.warning("A base de setores censitários/população real ainda não foi carregada na aba 2.")
            st.info("Você pode gerar ou regerar um resultado municipal preliminar com UBS/CNES/INE + APIs para destravar o Dashboard Executivo APS. Esse resultado não calcula distância real até UBS; distância e setores críticos só serão completos após carregar setores/territórios.")

            if tem_cache:
                st.success("Já existe um resultado preliminar salvo no cache/session_state. Você pode mantê-lo ou regerá-lo para atualizar com as bases atuais.")
                _mostrar_resultado_preliminar_ou_cache(resultado_cache, ubs, "Resultado do georreferenciamento recuperado do cache/session_state.")
                rotulo_botao = "Regerar ranking municipal preliminar com as bases atuais"
            else:
                rotulo_botao = "Gerar ranking municipal preliminar com as bases disponíveis"

            if st.button(rotulo_botao, use_container_width=True):
                resultado_preliminar = gerar_resultado_municipal_preliminar(ubs)
                if resultado_preliminar.empty:
                    st.error("Não foi possível gerar o ranking preliminar. Verifique se a base UBS/CNES está carregada.")
                else:
                    st.session_state["geo_resultado_df"] = resultado_preliminar
                    salvar_cache_aps(keys=["geo_resultado_df", "geo_ubs_df", "geo_equipes_ine_df"], origem="ranking municipal preliminar gerado sem setores no Georreferenciamento APS")
                    st.success("Ranking municipal preliminar gerado/regerado e salvo no cache.")
                    _mostrar_resultado_preliminar_ou_cache(resultado_preliminar, ubs, "Ranking municipal preliminar gerado e salvo no cache.")
                return
            return

        ok_pop, total_pop, linhas_pop = _tem_populacao_real_territorial(territorios)
        if not ok_pop:
            st.error("Ranking oficial bloqueado: carregue população real por setor censitário IBGE ou base territorial validada.")
            st.info(
                "A base gerada a partir do CNES serve apenas para mapear a oferta e conferir bairros/localidades. "
                "Ela não contém população real. Para calcular vazios assistenciais com distância e setores críticos, faça upload de uma base territorial com a coluna populacao preenchida por dado real."
            )
            st.dataframe(_normalizar_colunas(territorios).head(100), use_container_width=True, hide_index=True)
            if st.button("Gerar ranking municipal preliminar mesmo sem setores", use_container_width=True):
                resultado_preliminar = gerar_resultado_municipal_preliminar(ubs)
                if resultado_preliminar.empty:
                    st.error("Não foi possível gerar o ranking preliminar. Verifique se a base UBS/CNES está carregada.")
                else:
                    st.session_state["geo_resultado_df"] = resultado_preliminar
                    salvar_cache_aps(keys=["geo_resultado_df", "geo_ubs_df", "geo_equipes_ine_df", "geo_territorios_df"], origem="ranking municipal preliminar gerado com territórios sem população real")
                    _mostrar_resultado_preliminar_ou_cache(resultado_preliminar, ubs, "Ranking municipal preliminar gerado e salvo no cache.")
            return

        coords_territorios = 0
        terr_norm = _normalizar_colunas(territorios)
        if {"latitude", "longitude"}.issubset(terr_norm.columns):
            coords_territorios = int(((pd.to_numeric(terr_norm["latitude"], errors="coerce").fillna(0) != 0) & (pd.to_numeric(terr_norm["longitude"], errors="coerce").fillna(0) != 0)).sum())
        if coords_territorios == 0:
            st.warning("A população real está carregada, mas os territórios não possuem coordenadas/centroides válidos. O ranking será calculado, porém a distância até UBS pode ficar imprecisa. Carregue a malha IBGE para qualificar o vazio assistencial.")

        try:
            resultado = calcular_vazios_assistenciais(territorios, ubs)
            st.session_state["geo_resultado_df"] = resultado
            salvar_cache_aps(origem="ranking de vazios assistenciais calculado no Georreferenciamento APS")
        except Exception as exc:
            st.error(f"Falha ao calcular vazios assistenciais: {exc}")
            return

        _render_cards(resultado, ubs)

        municipios = sorted([m for m in resultado.get("municipio", pd.Series(dtype=str)).dropna().astype(str).unique() if m.strip()])
        municipio_sel = st.selectbox("Filtrar município", ["Todos"] + municipios)
        res = resultado.copy()
        if municipio_sel != "Todos":
            res = res[res["municipio"].astype(str) == municipio_sel]

        classificacoes = ["Todos"] + sorted([c for c in res.get("classificacao", pd.Series(dtype=str)).dropna().astype(str).unique() if c.strip()])
        classe_sel = st.selectbox("Filtrar classificação", classificacoes)
        if classe_sel != "Todos":
            res = res[res["classificacao"].astype(str) == classe_sel]

        st.dataframe(res, use_container_width=True, hide_index=True)
        st.download_button(
            "Exportar ranking completo",
            data=_excel_bytes({
                "ranking_vazios": res,
                "ubs_cnes_ine": _normalizar_colunas(ubs),
                "territorios": _normalizar_colunas(territorios),
            }),
            file_name="ranking_vazios_assistenciais_aps.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

        _render_mapa(res, ubs)

        with st.expander("Texto técnico para despacho"):
            st.markdown(
                """
                A análise georreferenciada da APS permite identificar vazios assistenciais a partir do cruzamento entre a localização das UBS/equipes, a distribuição territorial da população e os fatores de vulnerabilidade que aumentam a dependência do SUS. A metodologia não se limita à população total do município: ela diferencia áreas de maior renda e menor uso estimado da rede pública de áreas periféricas, rurais ou vulneráveis, onde a demanda real por equipes e serviços da Atenção Primária tende a ser maior.

                O ranking gerado deve ser tratado como evidência preliminar para validação com a Coordenadoria APS, Escritórios Regionais de Saúde e municípios. O resultado pode orientar construção de nova UBS, unidade de apoio, ampliação de equipe, atendimento itinerante ou reorganização territorial, conforme a realidade local e a viabilidade técnica/orçamentária.
                """
            )

    with aba4:
        _render_metodologia()
