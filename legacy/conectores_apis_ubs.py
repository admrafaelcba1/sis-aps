import io
import json
import math
import re
import zipfile
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin
import html as html_lib

import pandas as pd
import requests
import streamlit as st

try:
    from utils.cache_dados_aps import salvar_cache_aps, ler_metadata_cache_aps
except Exception:
    def salvar_cache_aps(*args, **kwargs):
        return {}
    def ler_metadata_cache_aps():
        return {}


TIMEOUT_PADRAO = 35
UF_MT = "51"
INEP_CENSO_ESCOLAR_ANO = 2024
PNI_ANO_REFERENCIA = 2025
PNI_MAX_RECURSOS_PROCESSAR = 1  # mantém a carga leve: usa a competência mais recente disponível do ano
INEP_CENSO_ESCOLAR_URLS = {
    2024: "https://download.inep.gov.br/dados_abertos/microdados_censo_escolar_2024.zip",
    2023: "https://download.inep.gov.br/microdados/microdados_censo_escolar_2023.zip",
}



def normalizar_texto(valor: Any) -> str:
    """Normaliza texto para comparação flexível em leituras SIDRA/CSV.

    Mantida como função global porque algumas rotinas antigas usam
    normalizar_texto(), enquanto outras usam _normalizar_texto_busca().
    """
    texto = str(valor or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


FONTES_PLANEJADAS = [
    {
        "fonte": "IBGE Localidades",
        "tipo": "API pública oficial",
        "situacao": "Funcional no módulo",
        "dados": "municípios, códigos IBGE, microrregião, mesorregião e UF",
        "uso": "base cadastral dos 142 municípios de Mato Grosso",
        "observacao": "Fonte mais estável para identificação municipal.",
    },
    {
        "fonte": "IBGE Localidades - Regiões Geográficas",
        "tipo": "API pública oficial",
        "situacao": "Funcional no módulo",
        "dados": "regiões geográficas imediatas e intermediárias do IBGE vinculadas aos municípios",
        "uso": "recorte territorial complementar para análises por polos regionais, fluxos urbanos e organização territorial",
        "observacao": "Não substitui a regionalização de saúde do SUS; é uma camada territorial adicional do IBGE para leitura comparativa.",
    },
    {
        "fonte": "IBGE Localidades - Distritos",
        "tipo": "API pública oficial",
        "situacao": "Funcional no módulo",
        "dados": "distritos oficiais vinculados aos municípios",
        "uso": "indicador territorial complementar para identificar municípios com mais núcleos/localidades oficiais",
        "observacao": "Não substitui comunidades rurais, assentamentos ou validação municipal, mas ajuda a enriquecer a análise territorial sem depender de dados manuais.",
    },
    {
        "fonte": "IBGE/SIDRA - Tabela 6579",
        "tipo": "API pública oficial",
        "situacao": "Funcional no módulo",
        "dados": "população residente estimada por município",
        "uso": "base populacional para cálculo MS e priorização preliminar",
        "observacao": "Se o ano selecionado ainda não estiver disponível, o sistema avisará.",
    },
    {
        "fonte": "IBGE/SIDRA - Tabela 1301",
        "tipo": "API pública oficial",
        "situacao": "Funcional no módulo, com fallback se a API mudar o formato",
        "dados": "área territorial e densidade demográfica",
        "uso": "indicador territorial preliminar para diferenciar municípios compactos e dispersos",
        "observacao": "A tabela 1301 é histórica; o sistema trata automaticamente área e densidade quando disponíveis.",
    },

    {
        "fonte": "IBGE/SIDRA - Tabela 9923",
        "tipo": "API pública oficial",
        "situacao": "Funcional no módulo, com rotina de fallback",
        "dados": "perfil urbano/rural e percentual rural do Censo 2022",
        "uso": "indicador estrutural de ruralidade para priorização territorial de UBS",
        "observacao": "Usa Censo Demográfico 2022 apenas como perfil territorial. O cálculo MS continua usando a população estimada do ano selecionado, por exemplo 2025.",
    },
    {
        "fonte": "IBGE/SIDRA - Tabela 9515",
        "tipo": "API pública oficial",
        "situacao": "Funcional no módulo",
        "dados": "índice de envelhecimento, idade mediana e razão de sexo da população no Censo 2022",
        "uso": "perfil demográfico complementar para qualificar municípios com maior pressão potencial sobre a APS",
        "observacao": "Não substitui a população estimada usada no cálculo MS; entra apenas como perfil demográfico estrutural do Censo 2022.",
    },
    {
        "fonte": "IBGE/SIDRA - Tabela 9543",
        "tipo": "API pública oficial",
        "situacao": "Funcional no módulo",
        "dados": "taxa de alfabetização das pessoas de 15 anos ou mais de idade no Censo 2022",
        "uso": "indicador socioeducacional para qualificar vulnerabilidade, comunicação em saúde e necessidade de ações integradas APS-Educação",
        "observacao": "Não define construção de UBS isoladamente; entra como camada socioeducacional para priorização e desenho de políticas públicas integradas.",
    },

    {
        "fonte": "IBGE/SIDRA - Tabelas 10295 e 10296",
        "tipo": "API pública oficial",
        "situacao": "Funcional no módulo",
        "dados": "rendimento domiciliar per capita médio e distribuição por classes de rendimento domiciliar per capita",
        "uso": "camada socioeconômica para qualificar vulnerabilidade de renda e planejamento intersetorial da APS",
        "observacao": "Usa dados agregados do Censo 2022, sem dados pessoais; não define construção de UBS isoladamente.",
    },
    {
        "fonte": "IBGE/SIDRA - Tabela 10061",
        "tipo": "API pública oficial",
        "situacao": "Funcional no módulo, com leitura por metadados",
        "dados": "nível de instrução da população de 25 anos ou mais no Censo 2022",
        "uso": "indicador socioeducacional complementar para identificar baixa escolaridade estrutural e apoiar políticas integradas APS-Educação",
        "observacao": "Não define construção de UBS isoladamente; qualifica territórios onde comunicação em saúde, educação em saúde e busca ativa podem exigir maior adaptação.",
    },

    {
        "fonte": "INEP - Censo Escolar da Educação Básica",
        "tipo": "microdados oficiais / ZIP público",
        "situacao": "Funcional no módulo como integração educacional-territorial",
        "dados": "escolas ativas por município, escolas rurais/urbanas, escolas indígenas, escolas quilombolas e matrículas da educação básica",
        "uso": "camada territorial e intersetorial para identificar dispersão educacional, escolas rurais e potenciais territórios de busca ativa APS-Educação",
        "observacao": "A leitura é agregada por município e não usa dados pessoais de estudantes, profissionais ou responsáveis.",
    },
    {
        "fonte": "INEP - Censo Escolar / Educação Especial e AEE",
        "tipo": "microdados oficiais / ZIP público",
        "situacao": "Funcional no módulo como camada APS-Educação e cuidado continuado",
        "dados": "matrículas da educação especial, escolas com matrícula de educação especial e atendimento educacional especializado, quando as colunas estiverem disponíveis no microdado",
        "uso": "camada intersetorial para qualificar demanda potencial de cuidado continuado, deficiência, TEA, reabilitação, comunicação em saúde e articulação APS-Educação",
        "observacao": "A leitura é agregada por município e não usa dados pessoais. O indicador não substitui BPC/MDS nem cadastro clínico; apenas qualifica o território.",
    },
    {
        "fonte": "Portal da Transparência - Benefício de Prestação Continuada (BPC)",
        "tipo": "download público oficial / ZIP mensal",
        "situacao": "Funcional no módulo como camada de vulnerabilidade social e cuidado continuado",
        "dados": "benefícios BPC agregados por município, com separação preliminar entre idoso, pessoa com deficiência e registros não identificados conforme colunas disponíveis",
        "uso": "medir pressão assistencial vinculada a idosos vulneráveis e pessoas com deficiência, qualificando planejamento da APS, cuidado longitudinal e articulação intersetorial",
        "observacao": "A base original é individualizada, mas o sistema consolida por município e não exibe CPF, NIS, nome, representante legal ou número de benefício.",
    },
    {
        "fonte": "IBGE/SIDRA - Censo 2022: populações indígena e quilombola",
        "tipo": "API pública oficial",
        "situacao": "Funcional no módulo como camada de equidade territorial",
        "dados": "população indígena e quilombola por município, quando disponível nas tabelas SIDRA do Censo 2022",
        "uso": "camada de equidade para sinalizar territórios que podem exigir validação específica, ações interculturais e articulação APS-Educação/assistência social",
        "observacao": "Não define construção de UBS isoladamente; qualifica a análise de acesso e vulnerabilidade territorial. A base é agregada por município.",
    },
    {
        "fonte": "IBGE/SIDRA - Tabelas 6909, 9397 e 9541",
        "tipo": "API pública oficial",
        "situacao": "Funcional no módulo, com parser conservador",
        "dados": "abastecimento de água, esgotamento sanitário, banheiro/sanitário e destino do lixo no Censo 2022",
        "uso": "camada de condições de vida e saneamento para qualificar vulnerabilidade sanitária territorial da APS",
        "observacao": "Não define construção de UBS isoladamente; indica territórios onde condições domiciliares podem aumentar riscos sanitários e necessidade de ações intersetoriais.",
    },
    {
        "fonte": "IBGE Malhas Geográficas",
        "tipo": "API pública oficial",
        "situacao": "Funcional para teste/validação",
        "dados": "geometria territorial do Estado ou municípios",
        "uso": "verificar disponibilidade para uso posterior no Dashboard Executivo",
        "observacao": "A tela de APIs apenas testa a fonte. A visualização em mapa deve ficar no Dashboard Executivo.",
    },
    {
        "fonte": "Dados Abertos SUS - Macrorregião e Região de Saúde",
        "tipo": "base pública oficial / CSV em ZIP",
        "situacao": "Funcional no módulo",
        "dados": "macrorregião, região de saúde e município",
        "uso": "regionalização oficial do SUS para organizar o estudo por recorte sanitário",
        "observacao": "O módulo baixa o arquivo CSV compactado publicado no Portal de Dados Abertos do SUS e filtra Mato Grosso.",
    },
    {
        "fonte": "CNES - Dados Abertos SUS",
        "tipo": "base pública oficial / CSV em ZIP",
        "situacao": "Funcional no módulo para leitura preliminar e detalhamento de estabelecimentos",
        "dados": "estabelecimentos cadastrados no CNES, município, tipo de unidade, natureza jurídica, gestão, atendimento SUS, identificação CNES e endereço quando disponível",
        "uso": "contagem preliminar de UBS/Unidades Básicas cadastradas por município e conferência detalhada das unidades",
        "observacao": "O dado é cadastral e deve ser validado pela SES/Coordenadoria APS antes de virar conclusão final sobre UBS efetivamente funcionando.",
    },
    {
        "fonte": "Dados Abertos SUS - Hospitais e Leitos",
        "tipo": "base pública oficial / CSV em ZIP",
        "situacao": "Funcional no módulo para leitura preliminar",
        "dados": "estabelecimentos hospitalares, leitos existentes e leitos SUS por município, quando a estrutura do arquivo permitir leitura automática",
        "uso": "contexto assistencial complementar para identificar municípios com pouca retaguarda hospitalar",
        "observacao": "Não define necessidade de UBS nova. Entra apenas como camada de contexto da rede assistencial municipal/regional.",
    },
    {
        "fonte": "Dados Abertos SUS - SINASC",
        "tipo": "base pública oficial / CSV em ZIP",
        "situacao": "Funcional no módulo para leitura agregada municipal",
        "dados": "nascidos vivos por município de residência, pré-natal, baixo peso, prematuridade e idade materna quando as colunas estiverem disponíveis",
        "uso": "contexto sanitário materno-infantil para qualificar pressão potencial sobre a APS",
        "observacao": "Usa a base de nascidos vivos mais recente consolidada/preliminar configurada no módulo. Não define construção de UBS sozinha.",
    },
    {
        "fonte": "Dados Abertos SUS - SIM",
        "tipo": "base pública oficial / CSV",
        "situacao": "Funcional no módulo para leitura agregada municipal, quando o arquivo anual estiver disponível",
        "dados": "óbitos por município de residência, óbitos infantis, óbitos menores de 5 anos e óbitos de idosos quando as colunas estiverem disponíveis",
        "uso": "contexto sanitário de mortalidade para qualificar pressão potencial sobre a APS e cruzar com o SINASC na mortalidade infantil",
        "observacao": "Usa arquivo nacional anual do SIM publicado em base aberta. Não define construção de UBS sozinha; serve como sinalizador sanitário complementar.",
    },
    {
        "fonte": "e-Gestor APS / Relatórios Públicos",
        "tipo": "relatórios públicos oficiais",
        "situacao": "Disponível para consulta, integração direta não garantida",
        "dados": "cobertura, financiamento e estratégias APS",
        "uso": "futura validação de cobertura APS/ESF",
        "observacao": "Não será usado como fonte automática oficial até termos extração estável e conferência com a Coordenadoria APS.",
    },
]

PARAMETROS_MS_ESF = [
    (0, 20_000, "Até 20.000 habitantes", 2_000),
    (20_001, 50_000, "20.001 a 50.000 habitantes", 2_500),
    (50_001, 100_000, "50.001 a 100.000 habitantes", 2_750),
    (100_001, None, "Acima de 100.000 habitantes", 3_000),
]


def _request_json(url: str, timeout: int = TIMEOUT_PADRAO) -> Any:
    headers = {
        "User-Agent": "SES-MT-Estudo-UBS/1.0",
        "Accept": "application/json,text/plain,*/*",
    }
    resposta = requests.get(url, timeout=timeout, headers=headers)
    resposta.raise_for_status()
    return resposta.json()


def _request_status(url: str, timeout: int = TIMEOUT_PADRAO) -> Dict[str, Any]:
    headers = {"User-Agent": "SES-MT-Estudo-UBS/1.0"}
    resposta = requests.get(url, timeout=timeout, headers=headers)
    return {
        "status_code": resposta.status_code,
        "ok": 200 <= resposta.status_code < 400,
        "content_type": resposta.headers.get("content-type", ""),
        "tamanho_bytes": len(resposta.content or b""),
    }


def _limpar_nome_municipio(nome: str) -> str:
    nome = str(nome or "").strip()
    nome = re.sub(r"\s*\([A-Z]{2}\)\s*$", "", nome)
    return nome


def _parametro_ms(populacao: int) -> tuple[str, int]:
    for minimo, maximo, faixa, parametro in PARAMETROS_MS_ESF:
        if maximo is None and populacao >= minimo:
            return faixa, parametro
        if maximo is not None and minimo <= populacao <= maximo:
            return faixa, parametro
    return "Não classificado", 0


def _excel_bytes(df: pd.DataFrame, nome_aba: str = "Dados") -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=nome_aba[:31], index=False)
    buffer.seek(0)
    return buffer.getvalue()



def _normalizar_chave_municipio(valor: Any) -> str:
    """Normaliza nomes de municípios para cruzamentos entre bases públicas."""
    texto = str(valor or "").strip().lower()
    mapa = str.maketrans(
        "áàãâäéèêëíìîïóòõôöúùûüçñ",
        "aaaaaeeeeiiiiooooouuuucn",
    )
    texto = texto.translate(mapa)
    texto = re.sub(r"\s*-\s*[a-z]{2}$", "", texto)
    texto = re.sub(r"\s+", " ", texto)
    texto = re.sub(r"[^a-z0-9 ]+", "", texto)
    return texto.strip()


def _identificar_coluna(colunas: List[str], candidatos: List[str]) -> Optional[str]:
    """Encontra uma coluna por lista de nomes candidatos, ignorando acento, caixa e separadores."""
    if not colunas:
        return None

    def norm(x: Any) -> str:
        texto = str(x or "").strip().lower()
        mapa = str.maketrans(
            "áàãâäéèêëíìîïóòõôöúùûüçñ",
            "aaaaaeeeeiiiiooooouuuucn",
        )
        texto = texto.translate(mapa)
        texto = re.sub(r"[^a-z0-9]+", "_", texto)
        return texto.strip("_")

    mapa_colunas = {norm(c): c for c in colunas}
    for cand in candidatos:
        n = norm(cand)
        if n in mapa_colunas:
            return mapa_colunas[n]
    for cand in candidatos:
        n = norm(cand)
        for nc, original in mapa_colunas.items():
            if n and (n in nc or nc in n):
                return original
    return None


def _portal_transparencia_competencias_recentes(meses: int = 18) -> List[str]:
    """Gera competências AAAAMM recentes para tentar localizar o ZIP mensal do Portal."""
    hoje = datetime.now()
    comps: List[str] = []
    ano = hoje.year
    mes = hoje.month
    # O Portal costuma atrasar alguns meses; começamos no mês anterior e voltamos.
    mes -= 1
    if mes == 0:
        mes = 12
        ano -= 1
    for _ in range(meses):
        comps.append(f"{ano}{mes:02d}")
        mes -= 1
        if mes == 0:
            mes = 12
            ano -= 1
    return comps


def _baixar_zip_portal_bolsa_familia(competencia: str) -> bytes:
    """Baixa o ZIP mensal do Novo Bolsa Família no MDS/SAGICAD."""
    competencia = str(competencia).strip()
    urls = [
        f"https://portaldatransparencia.gov.br/download-de-dados/novo-bolsa-familia/{competencia}",
        f"https://www.portaldatransparencia.gov.br/download-de-dados/novo-bolsa-familia/{competencia}",
    ]
    headers = {
        "User-Agent": "SES-MT-Estudo-UBS/1.0",
        "Accept": "application/zip,application/octet-stream,text/csv,*/*",
    }
    erros = []
    for url in urls:
        try:
            resp = requests.get(url, timeout=120, headers=headers, allow_redirects=True)
            content_type = (resp.headers.get("content-type") or "").lower()
            if resp.status_code == 200 and resp.content and (resp.content[:2] == b"PK" or "zip" in content_type):
                return resp.content
            erros.append(f"{url} -> HTTP {resp.status_code}, content-type={content_type}, bytes={len(resp.content or b'')}")
        except Exception as exc:
            erros.append(f"{url} -> {exc}")
    raise ValueError("Não foi possível baixar ZIP do Novo Bolsa Família. " + " | ".join(erros[:3]))


def _ler_primeiro_csv_de_zip_bytes(conteudo_zip: bytes) -> pd.DataFrame:
    """Lê o primeiro CSV dentro de um ZIP, tentando encodings comuns do MDS/SAGICAD."""
    with zipfile.ZipFile(io.BytesIO(conteudo_zip)) as zf:
        nomes = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not nomes:
            raise ValueError("ZIP baixado não contém arquivo CSV.")
        nome_csv = nomes[0]
        bruto = zf.read(nome_csv)

    ultimo_erro = None
    for enc in ["latin1", "cp1252", "utf-8-sig", "utf-8"]:
        try:
            return pd.read_csv(io.BytesIO(bruto), sep=";", encoding=enc, dtype=str, low_memory=False)
        except Exception as exc:
            ultimo_erro = exc
    raise ValueError(f"Não foi possível ler o CSV do ZIP: {ultimo_erro}")


def _resolver_competencia_bolsa_familia() -> tuple[str, pd.DataFrame]:
    """Tenta competências recentes até encontrar arquivo funcional do Novo Bolsa Família."""
    erros = []
    for comp in _portal_transparencia_competencias_recentes(18):
        try:
            zip_bytes = _baixar_zip_portal_bolsa_familia(comp)
            df = _ler_primeiro_csv_de_zip_bytes(zip_bytes)
            if df is not None and not df.empty:
                return comp, df
            erros.append(f"{comp}: CSV vazio")
        except Exception as exc:
            erros.append(f"{comp}: {exc}")
    raise ValueError("Nenhuma competência recente do Novo Bolsa Família pôde ser carregada. " + " | ".join(erros[:5]))


def _valor_monetario_brasileiro(valor: Any) -> float:
    texto = str(valor or "").strip()
    if not texto:
        return 0.0
    texto = texto.replace("R$", "").replace(" ", "")
    # Portal costuma vir com vírgula decimal.
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except Exception:
        return 0.0


def classificar_vulnerabilidade_social_bolsa_familia(valor_por_1000: Any) -> str:
    valor = _parse_numero_sidra(valor_por_1000)
    if valor is None:
        return "Sem informação"
    if valor >= 240:
        return "Muito alta vulnerabilidade social"
    if valor >= 160:
        return "Alta vulnerabilidade social"
    if valor >= 80:
        return "Vulnerabilidade social moderada"
    return "Menor vulnerabilidade social relativa"


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def testar_bolsa_familia_portal_transparencia() -> Dict[str, Any]:
    """Testa se o download mensal do Novo Bolsa Família está acessível e legível."""
    competencia, df = _resolver_competencia_bolsa_familia()
    return {
        "ok": True,
        "competencia": competencia,
        "linhas": int(len(df)),
        "colunas": list(df.columns)[:30],
        "observacao": "Arquivo mensal do Novo Bolsa Família baixado do MDS/SAGICAD e lido com sucesso.",
    }


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def carregar_bolsa_familia_portal_transparencia_mt() -> pd.DataFrame:
    """Carrega pagamentos do Novo Bolsa Família e agrega por município de Mato Grosso.

    A base original do Portal é individualizada. Para o estudo de UBS, o sistema mantém
    apenas indicadores municipais agregados, sem exibir CPF, NIS ou nome de beneficiários.
    """
    competencia, df = _resolver_competencia_bolsa_familia()

    col_uf = _identificar_coluna(list(df.columns), ["UF", "SIGLA UF", "SG UF"])
    col_mun = _identificar_coluna(list(df.columns), ["NOME MUNICÍPIO", "NOME MUNICIPIO", "MUNICÍPIO", "MUNICIPIO"])
    col_valor = _identificar_coluna(list(df.columns), ["VALOR PARCELA", "VALOR", "VALOR BENEFÍCIO", "VALOR BENEFICIO"])
    col_nis = _identificar_coluna(list(df.columns), ["NIS FAVORECIDO", "NIS", "NIS BENEFICIÁRIO", "NIS BENEFICIARIO"])
    col_cpf = _identificar_coluna(list(df.columns), ["CPF FAVORECIDO", "CPF", "CPF BENEFICIÁRIO", "CPF BENEFICIARIO"])

    if not col_uf or not col_mun or not col_valor:
        raise ValueError(f"Colunas essenciais não localizadas no arquivo do Bolsa Família. Colunas: {list(df.columns)[:25]}")

    trabalho = df.copy()
    trabalho = trabalho[trabalho[col_uf].astype(str).str.upper().str.strip().eq("MT")].copy()
    if trabalho.empty:
        raise ValueError("Arquivo do Bolsa Família foi lido, mas nenhum registro de Mato Grosso foi identificado.")

    trabalho["municipio_chave"] = trabalho[col_mun].apply(_normalizar_chave_municipio)
    trabalho["valor_bolsa_familia_total_mes"] = trabalho[col_valor].apply(_valor_monetario_brasileiro)

    col_benef = col_nis or col_cpf
    if col_benef:
        trabalho["beneficiario_id"] = trabalho[col_benef].astype(str).str.strip()
    else:
        trabalho["beneficiario_id"] = None

    agg = (
        trabalho.groupby("municipio_chave", as_index=False)
        .agg(
            beneficios_bolsa_familia_registros=(col_mun, "size"),
            beneficiarios_unicos_bolsa_familia=("beneficiario_id", lambda s: s.replace({"": pd.NA, "nan": pd.NA}).dropna().nunique()),
            valor_bolsa_familia_total_mes=("valor_bolsa_familia_total_mes", "sum"),
        )
    )

    municipios = carregar_municipios_ibge_mt()[["codigo_ibge", "municipio"]].copy()
    municipios["municipio_chave"] = municipios["municipio"].apply(_normalizar_chave_municipio)
    saida = municipios.merge(agg, on="municipio_chave", how="left")
    saida["beneficios_bolsa_familia_registros"] = saida["beneficios_bolsa_familia_registros"].fillna(0).astype(int)
    saida["beneficiarios_unicos_bolsa_familia"] = saida["beneficiarios_unicos_bolsa_familia"].fillna(0).astype(int)
    saida["valor_bolsa_familia_total_mes"] = saida["valor_bolsa_familia_total_mes"].fillna(0.0).round(2)
    saida["competencia_bolsa_familia"] = competencia
    saida["fonte_bolsa_familia"] = "MDS/SAGICAD - Novo Bolsa Família"
    saida["observacao_bolsa_familia"] = "Dados agregados por município; dados pessoais não são exibidos no sistema."
    return saida.drop(columns=["municipio_chave"]).sort_values("municipio").reset_index(drop=True)


# -----------------------------------------------------------------------------
# MDS / SAGICAD - RI Social / Matriz Social - integração candidata
# -----------------------------------------------------------------------------

# Observação técnica:
# O MDS disponibiliza painéis e relatórios públicos agregados, mas o RI Social
# pode renderizar parte dos indicadores dinamicamente. Por isso, esta integração
# não deve quebrar o sistema caso os números não sejam encontrados no HTML.
# Mantemos as colunas preparadas e registramos alerta para validação/ajuste do endpoint.

def _mds_codigo_ri_social(codigo_ibge: Any) -> str:
    """O RI Social costuma usar o código municipal com 6 dígitos (sem o dígito final do IBGE)."""
    codigo = re.sub(r"\D+", "", str(codigo_ibge or ""))
    if len(codigo) >= 6:
        return codigo[:6]
    return codigo


def _extrair_numero_mds_por_padroes(texto: str, padroes: List[str]) -> Optional[float]:
    if not texto:
        return None
    texto_norm = re.sub(r"\s+", " ", str(texto))
    for padrao in padroes:
        m = re.search(padrao, texto_norm, flags=re.IGNORECASE)
        if not m:
            continue
        trecho = texto_norm[m.end(): m.end() + 500]
        nums = re.findall(r"(?:R\$\s*)?[-+]?\d{1,3}(?:\.\d{3})*(?:,\d+)?|(?:R\$\s*)?[-+]?\d+(?:,\d+)?", trecho)
        for n in nums:
            valor = _valor_monetario_brasileiro(n)
            if valor is not None:
                return float(valor)
    return None


def _extrair_referencia_mds(texto: str) -> Optional[str]:
    if not texto:
        return None
    m = re.search(r"(janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\s*/?\s*\d{4}", texto, flags=re.IGNORECASE)
    if m:
        return m.group(0).strip()
    m = re.search(r"(?:refer[êe]ncia|última referência disponível)[:\s]+([^\.\n]{4,60})", texto, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


def _mds_url_candidatas(codigo_ri: str) -> List[str]:
    return [
        f"https://aplicacoes.cidadania.gov.br/ri/ri/relatorios/cidadania/?aM=0&codigo={codigo_ri}",
        f"https://aplicacoes.cidadania.gov.br/ri/ri/relatorios/cidadania/?codigo={codigo_ri}&aM=0",
        f"https://aplicacoes.mds.gov.br/sagi/ri/relatorios/cidadania/?codigo={codigo_ri}&aM=0",
        f"https://aplicacoes.mds.gov.br/sagi/RIv3/geral/relatorio.php?p_ibge={codigo_ri}&area=0",
        f"https://aplicacoes.mds.gov.br/sagi/RIv3/geral/relatorio_form.php?p_ibge={codigo_ri}&area=0",
    ]


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def _carregar_mds_ri_social_municipio(codigo_ibge: str, municipio: str = "") -> Dict[str, Any]:
    """Tenta ler relatório público agregado do MDS/SAGICAD para um município.

    Se o relatório for acessado, mas os indicadores não vierem no HTML estático,
    retorna as colunas vazias com alerta em vez de derrubar o módulo.
    """
    codigo_ri = _mds_codigo_ri_social(codigo_ibge)
    headers = {"User-Agent": "SES-MT-Estudo-UBS/1.0", "Accept": "text/html,application/xhtml+xml,*/*"}
    erros = []
    html_final = ""
    url_ok = None
    for url in _mds_url_candidatas(codigo_ri):
        try:
            resp = requests.get(url, timeout=45, headers=headers)
            if resp.status_code >= 400:
                erros.append(f"{url} -> HTTP {resp.status_code}")
                continue
            html_final = resp.text or ""
            url_ok = url
            break
        except Exception as exc:
            erros.append(f"{url} -> {exc}")

    if not url_ok:
        raise ValueError("Não foi possível acessar os relatórios públicos MDS/SAGICAD. " + " | ".join(erros[:3]))

    texto = re.sub(r"<script[\s\S]*?</script>", " ", html_final, flags=re.IGNORECASE)
    texto = re.sub(r"<style[\s\S]*?</style>", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = re.sub(r"\s+", " ", texto)

    familias_bf = _extrair_numero_mds_por_padroes(texto, [
        r"Quantidade\s+de\s+Fam[ií]lias\s+benefici[aá]rias\s+do\s+Programa\s+Bolsa\s+Fam[ií]lia",
        r"Fam[ií]lias\s+benefici[aá]rias\s+do\s+Programa\s+Bolsa\s+Fam[ií]lia",
        r"Quantidade\s+de\s+Fam[ií]lias\s+beneficiadas\s+pelo\s+Programa\s+Bolsa\s+Fam[ií]lia",
        r"Bolsa\s+Fam[ií]lia\s+-\s+Quantidade\s+Fam[ií]lias",
    ])
    pessoas_bf = _extrair_numero_mds_por_padroes(texto, [
        r"Pessoas\s+benefici[aá]rias\s+do\s+Programa\s+Bolsa\s+Fam[ií]lia",
        r"pessoas\s+beneficiadas\s+pelo\s+Programa\s+Bolsa\s+Fam[ií]lia",
        r"Benefici[aá]rias\s+do\s+Programa\s+Bolsa\s+Fam[ií]lia",
    ])
    valor_bf = _extrair_numero_mds_por_padroes(texto, [
        r"Bolsa\s+Fam[ií]lia\s+-\s+Valor\s+repassado",
        r"Valor\s+repassado\s*\(R\$\)",
        r"foram\s+transferidos\s+R\$",
    ])
    familias_cad = _extrair_numero_mds_por_padroes(texto, [
        r"Cadastro\s+[UÚ]nico\s+-\s+Qtd\s+Fam[ií]lias",
        r"fam[ií]lias\s+inscritas\s+no\s+Cadastro\s+[UÚ]nico",
        r"fam[ií]lias\s+cadastradas\s+no\s+Cadastro\s+[UÚ]nico",
    ])
    pessoas_cad = _extrair_numero_mds_por_padroes(texto, [
        r"Cadastro\s+[UÚ]nico\s+-\s+Qtd\s+Pessoas",
        r"pessoas\s+inscritas\s+no\s+Cadastro\s+[UÚ]nico",
        r"pessoas\s+cadastradas\s+no\s+Cadastro\s+[UÚ]nico",
    ])
    familias_extrema = _extrair_numero_mds_por_padroes(texto, [
        r"Total\s+de\s+fam[ií]lias\s+em\s+situa[cç][aã]o\s+de\s+extrema\s+pobreza",
        r"fam[ií]lias\s+em\s+situa[cç][aã]o\s+de\s+extrema\s+pobreza",
    ])
    pessoas_extrema = _extrair_numero_mds_por_padroes(texto, [
        r"Total\s+de\s+pessoas\s+em\s+situa[cç][aã]o\s+de\s+extrema\s+pobreza",
        r"pessoas\s+em\s+situa[cç][aã]o\s+de\s+extrema\s+pobreza",
    ])
    familias_pobreza = _extrair_numero_mds_por_padroes(texto, [
        r"Total\s+de\s+fam[ií]lias\s+em\s+situa[cç][aã]o\s+de\s+pobreza(?!\s+e)",
        r"fam[ií]lias\s+em\s+situa[cç][aã]o\s+de\s+pobreza(?!\s+e)",
    ])
    pessoas_pobreza = _extrair_numero_mds_por_padroes(texto, [
        r"Total\s+de\s+pessoas\s+em\s+situa[cç][aã]o\s+de\s+pobreza(?!\s+e)",
        r"pessoas\s+em\s+situa[cç][aã]o\s+de\s+pobreza(?!\s+e)",
    ])

    valores = [familias_bf, pessoas_bf, valor_bf, familias_cad, pessoas_cad, familias_extrema, pessoas_extrema, familias_pobreza, pessoas_pobreza]
    alerta = None
    if not any(v is not None for v in valores):
        alerta = "Relatório MDS acessado, mas os indicadores agregados não vieram no HTML estático. Fonte mantida como candidata; usar painel/exportação MDS ou ajustar endpoint se disponível."

    return {
        "codigo_ibge": str(codigo_ibge),
        "municipio": municipio,
        "codigo_ri_social_mds": codigo_ri,
        "familias_bolsa_familia_mds": familias_bf,
        "pessoas_bolsa_familia_mds": pessoas_bf,
        "valor_bolsa_familia_mds": valor_bf,
        "familias_cadunico_mds": familias_cad,
        "pessoas_cadunico_mds": pessoas_cad,
        "familias_extrema_pobreza_mds": familias_extrema,
        "pessoas_extrema_pobreza_mds": pessoas_extrema,
        "familias_pobreza_mds": familias_pobreza,
        "pessoas_pobreza_mds": pessoas_pobreza,
        "referencia_mds_social": _extrair_referencia_mds(texto),
        "fonte_bolsa_familia": "MDS/SAGICAD - RI Social / Matriz Social agregado municipal",
        "observacao_bolsa_familia": alerta or "Leitura agregada municipal; não usa CPF, NIS nem dados individualizados.",
        "url_mds_testada": url_ok,
        "alerta_mds_social_municipio": alerta,
    }


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def carregar_bolsa_familia_portal_transparencia_mt() -> pd.DataFrame:
    municipios = carregar_municipios_ibge_mt()[["codigo_ibge", "municipio"]].copy()
    registros: List[Dict[str, Any]] = []
    erros: List[str] = []
    for _, linha in municipios.iterrows():
        try:
            registros.append(_carregar_mds_ri_social_municipio(str(linha["codigo_ibge"]), str(linha["municipio"])))
        except Exception as exc:
            erros.append(f"{linha['municipio']}: {exc}")
            registros.append({
                "codigo_ibge": str(linha["codigo_ibge"]),
                "municipio": str(linha["municipio"]),
                "codigo_ri_social_mds": _mds_codigo_ri_social(linha["codigo_ibge"]),
                "familias_bolsa_familia_mds": None,
                "pessoas_bolsa_familia_mds": None,
                "valor_bolsa_familia_mds": None,
                "familias_cadunico_mds": None,
                "pessoas_cadunico_mds": None,
                "familias_extrema_pobreza_mds": None,
                "pessoas_extrema_pobreza_mds": None,
                "familias_pobreza_mds": None,
                "pessoas_pobreza_mds": None,
                "referencia_mds_social": None,
                "fonte_bolsa_familia": "MDS/SAGICAD - RI Social / Matriz Social agregado municipal",
                "observacao_bolsa_familia": f"Falha parcial: {exc}",
                "alerta_mds_social_municipio": str(exc),
            })
    saida = pd.DataFrame(registros)
    saida["beneficios_bolsa_familia_registros"] = pd.to_numeric(saida["familias_bolsa_familia_mds"], errors="coerce")
    saida["beneficiarios_unicos_bolsa_familia"] = pd.to_numeric(saida["pessoas_bolsa_familia_mds"], errors="coerce")
    saida["valor_bolsa_familia_total_mes"] = pd.to_numeric(saida["valor_bolsa_familia_mds"], errors="coerce")
    saida["competencia_bolsa_familia"] = saida["referencia_mds_social"].fillna("Última referência MDS disponível")
    saida["alerta_mds_social"] = "; ".join(erros[:8]) if erros else None
    return saida.sort_values("municipio").reset_index(drop=True)


def testar_bolsa_familia_portal_transparencia() -> Dict[str, Any]:
    municipios = carregar_municipios_ibge_mt()[["codigo_ibge", "municipio"]].copy()
    amostra = municipios[municipios["municipio"].str.contains("Cuiab", case=False, na=False)]
    if amostra.empty:
        amostra = municipios.head(1)
    linha = amostra.iloc[0]
    dado = _carregar_mds_ri_social_municipio(str(linha["codigo_ibge"]), str(linha["municipio"]))
    tem_indicador = any(dado.get(c) is not None for c in [
        "familias_bolsa_familia_mds", "pessoas_bolsa_familia_mds", "valor_bolsa_familia_mds",
        "familias_cadunico_mds", "pessoas_cadunico_mds", "familias_extrema_pobreza_mds", "pessoas_extrema_pobreza_mds",
    ])
    return {
        "ok": bool(tem_indicador),
        "fonte": "MDS/SAGICAD - RI Social / Matriz Social agregado municipal",
        "municipio_teste": dado.get("municipio"),
        "codigo_ibge": dado.get("codigo_ibge"),
        "codigo_ri_social_mds": dado.get("codigo_ri_social_mds"),
        "url_mds_testada": dado.get("url_mds_testada"),
        "referencia": dado.get("referencia_mds_social"),
        "familias_bolsa_familia_mds": dado.get("familias_bolsa_familia_mds"),
        "pessoas_bolsa_familia_mds": dado.get("pessoas_bolsa_familia_mds"),
        "familias_cadunico_mds": dado.get("familias_cadunico_mds"),
        "pessoas_cadunico_mds": dado.get("pessoas_cadunico_mds"),
        "observacao": dado.get("observacao_bolsa_familia"),
    }




# -----------------------------------------------------------------------------
# BPC - Benefício de Prestação Continuada (Portal da Transparência)
# -----------------------------------------------------------------------------

def _baixar_zip_portal_bpc(competencia: str) -> bytes:
    """Baixa o ZIP mensal do BPC no Portal da Transparência.

    Competência no formato AAAAMM. O endpoint público de download segue o mesmo
    padrão dos demais arquivos mensais do Portal da Transparência.
    """
    competencia = str(competencia).strip()
    urls = [
        f"https://portaldatransparencia.gov.br/download-de-dados/bpc/{competencia}",
        f"https://www.portaldatransparencia.gov.br/download-de-dados/bpc/{competencia}",
    ]
    headers = {
        "User-Agent": "SES-MT-Estudo-UBS/1.0",
        "Accept": "application/zip,application/octet-stream,text/csv,*/*",
    }
    erros: List[str] = []
    for url in urls:
        try:
            resp = requests.get(url, timeout=140, headers=headers, allow_redirects=True)
            content_type = (resp.headers.get("content-type") or "").lower()
            if resp.status_code == 200 and resp.content and (resp.content[:2] == b"PK" or "zip" in content_type):
                return resp.content
            erros.append(f"{url} -> HTTP {resp.status_code}, content-type={content_type}, bytes={len(resp.content or b'')}")
        except Exception as exc:
            erros.append(f"{url} -> {exc}")
    raise ValueError("Não foi possível baixar ZIP do BPC. " + " | ".join(erros[:3]))


def _resolver_competencia_bpc(max_meses: int = 18) -> tuple[str, pd.DataFrame]:
    """Tenta competências recentes até encontrar arquivo mensal funcional do BPC."""
    erros: List[str] = []
    for comp in _portal_transparencia_competencias_recentes(max_meses):
        try:
            zip_bytes = _baixar_zip_portal_bpc(comp)
            df = _ler_primeiro_csv_de_zip_bytes(zip_bytes)
            if df is not None and not df.empty:
                return comp, df
            erros.append(f"{comp}: CSV vazio")
        except Exception as exc:
            erros.append(f"{comp}: {exc}")
    raise ValueError("Nenhuma competência recente do BPC pôde ser carregada. " + " | ".join(erros[:6]))


def _identificar_tipo_bpc(row: pd.Series, col_tipo: Optional[str], col_especie: Optional[str], col_beneficio: Optional[str]) -> str:
    """Classifica a linha do BPC como Idoso, PCD ou Não identificado de forma flexível."""
    textos = []
    for col in [col_tipo, col_especie, col_beneficio]:
        if col and col in row.index:
            textos.append(str(row.get(col) or ""))
    texto = normalizar_texto(" ".join(textos))
    if any(chave in texto for chave in ["idoso", "idade avancada", "amparo social ao idoso", "loas idoso"]):
        return "Idoso"
    if any(chave in texto for chave in ["deficiencia", "deficiente", "pessoa com deficiencia", "pcd", "amparo social a pessoa portadora"]):
        return "Pessoa com Deficiência"
    return "Não identificado"


def classificar_pressao_bpc(valor_por_1000: Any) -> str:
    valor = _parse_numero_sidra(valor_por_1000)
    if valor is None:
        return "Sem informação"
    if valor >= 45:
        return "Muito alta pressão assistencial BPC"
    if valor >= 30:
        return "Alta pressão assistencial BPC"
    if valor >= 15:
        return "Pressão assistencial BPC moderada"
    if valor > 0:
        return "Menor pressão assistencial BPC relativa"
    return "Sem beneficiários BPC identificados"


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def testar_bpc_portal_transparencia() -> Dict[str, Any]:
    """Testa se o arquivo mensal do BPC está acessível e se possui colunas mínimas."""
    competencia, df = _resolver_competencia_bpc(18)
    return {
        "ok": True,
        "competencia": competencia,
        "linhas": int(len(df)),
        "colunas": list(df.columns)[:40],
        "fonte": "Portal da Transparência - Download de Dados - BPC",
        "observacao": "Arquivo mensal do BPC baixado e lido com sucesso. O processamento do sistema consolida os dados por município, sem exibir dados pessoais.",
    }


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def carregar_bpc_portal_transparencia_mt() -> pd.DataFrame:
    """Carrega BPC do Portal da Transparência e agrega por município de Mato Grosso.

    A base original é individualizada. Para o estudo de UBS/APS, o sistema mantém
    somente indicadores municipais agregados, sem CPF, NIS, nome ou número de benefício.
    """
    competencia, df = _resolver_competencia_bpc(18)
    if df is None or df.empty:
        raise ValueError("Arquivo BPC lido, mas sem registros.")

    col_uf = _identificar_coluna(list(df.columns), ["UF", "SIGLA UF", "SG UF", "UF BENEFICIÁRIO", "UF BENEFICIARIO"])
    col_mun = _identificar_coluna(list(df.columns), [
        "NOME MUNICÍPIO", "NOME MUNICIPIO", "MUNICÍPIO", "MUNICIPIO", "MUNICÍPIO BENEFICIÁRIO", "MUNICIPIO BENEFICIARIO",
    ])
    col_cod_ibge = _identificar_coluna(list(df.columns), [
        "CÓDIGO MUNICÍPIO IBGE", "CODIGO MUNICIPIO IBGE", "CÓDIGO IBGE", "CODIGO IBGE", "IBGE",
    ])
    col_cod_siafi = _identificar_coluna(list(df.columns), [
        "CÓDIGO MUNICÍPIO SIAFI", "CODIGO MUNICIPIO SIAFI", "CÓDIGO SIAFI MUNICÍPIO", "CODIGO SIAFI MUNICIPIO", "SIAFI",
    ])
    col_valor = _identificar_coluna(list(df.columns), [
        "VALOR PARCELA", "VALOR DO BENEFÍCIO", "VALOR BENEFÍCIO", "VALOR BENEFICIO", "VALOR", "VALOR DISPONIBILIZADO",
    ])
    col_tipo = _identificar_coluna(list(df.columns), [
        "TIPO BENEFÍCIO", "TIPO BENEFICIO", "TIPO", "MODALIDADE", "GRUPO BENEFÍCIO", "GRUPO BENEFICIO",
    ])
    col_especie = _identificar_coluna(list(df.columns), [
        "ESPÉCIE BENEFÍCIO", "ESPECIE BENEFICIO", "ESPÉCIE", "ESPECIE", "NOME BENEFÍCIO", "NOME BENEFICIO",
    ])
    col_beneficio = _identificar_coluna(list(df.columns), [
        "BENEFÍCIO", "BENEFICIO", "DESCRIÇÃO BENEFÍCIO", "DESCRICAO BENEFICIO", "NÚMERO BENEFÍCIO", "NUMERO BENEFICIO",
    ])

    trabalho = df.copy()
    if col_uf:
        trabalho = trabalho[trabalho[col_uf].astype(str).str.upper().str.strip().eq("MT")].copy()
    elif col_mun:
        municipios_mt = carregar_municipios_ibge_mt()[["codigo_ibge", "municipio"]].copy()
        chaves_mt = set(municipios_mt["municipio"].map(_normalizar_chave_municipio))
        trabalho["_chave_municipio_bpc"] = trabalho[col_mun].map(_normalizar_chave_municipio)
        trabalho = trabalho[trabalho["_chave_municipio_bpc"].isin(chaves_mt)].copy()
    else:
        raise ValueError("Não foi possível identificar coluna de UF ou município no arquivo BPC.")

    if trabalho.empty:
        raise ValueError("Arquivo BPC acessado, mas nenhum registro de Mato Grosso foi identificado.")

    municipios = carregar_municipios_ibge_mt()[["codigo_ibge", "municipio"]].copy()
    municipios["_chave_municipio_bpc"] = municipios["municipio"].map(_normalizar_chave_municipio)

    if col_mun:
        trabalho["_chave_municipio_bpc"] = trabalho[col_mun].map(_normalizar_chave_municipio)
    else:
        trabalho["_chave_municipio_bpc"] = None

    if col_cod_ibge:
        trabalho["codigo_ibge"] = trabalho[col_cod_ibge].astype(str).str.extract(r"(\d+)")[0].str.zfill(7)
    else:
        trabalho = trabalho.merge(municipios[["codigo_ibge", "_chave_municipio_bpc"]], on="_chave_municipio_bpc", how="left")

    if "codigo_ibge" not in trabalho.columns or trabalho["codigo_ibge"].isna().all():
        trabalho = trabalho.drop(columns=["codigo_ibge"], errors="ignore").merge(
            municipios[["codigo_ibge", "_chave_municipio_bpc"]], on="_chave_municipio_bpc", how="left"
        )

    trabalho = trabalho[trabalho["codigo_ibge"].notna()].copy()
    trabalho["codigo_ibge"] = trabalho["codigo_ibge"].astype(str).str.extract(r"(\d+)")[0].str.zfill(7)
    trabalho["tipo_bpc_sistema"] = trabalho.apply(lambda row: _identificar_tipo_bpc(row, col_tipo, col_especie, col_beneficio), axis=1)
    trabalho["valor_bpc_linha"] = trabalho[col_valor].map(_valor_monetario_brasileiro) if col_valor else 0.0
    trabalho["registro_bpc_linha"] = 1

    agg = trabalho.groupby("codigo_ibge", dropna=False).agg(
        bpc_total_qtd=("registro_bpc_linha", "sum"),
        bpc_valor_total_mes=("valor_bpc_linha", "sum"),
        bpc_idoso_qtd=("tipo_bpc_sistema", lambda s: int((s == "Idoso").sum())),
        bpc_pcd_qtd=("tipo_bpc_sistema", lambda s: int((s == "Pessoa com Deficiência").sum())),
        bpc_tipo_nao_identificado_qtd=("tipo_bpc_sistema", lambda s: int((s == "Não identificado").sum())),
    ).reset_index()

    base = municipios.drop(columns=["_chave_municipio_bpc"], errors="ignore").merge(agg, on="codigo_ibge", how="left")
    for col in ["bpc_total_qtd", "bpc_idoso_qtd", "bpc_pcd_qtd", "bpc_tipo_nao_identificado_qtd"]:
        base[col] = pd.to_numeric(base[col], errors="coerce").fillna(0).astype(int)
    base["bpc_valor_total_mes"] = pd.to_numeric(base["bpc_valor_total_mes"], errors="coerce").fillna(0.0)
    base["competencia_bpc"] = competencia
    base["fonte_bpc"] = "Portal da Transparência - Download de Dados - Benefício de Prestação Continuada (BPC)"
    base["observacao_bpc"] = (
        "Dados agregados por município. O sistema não exibe CPF, NIS, nome, representante legal ou número do benefício. "
        "Classificação Idoso/PCD depende da descrição disponível no arquivo mensal; quando a coluna não permite inferência, o registro fica como não identificado."
    )
    base["colunas_bpc_utilizadas"] = ", ".join([str(c) for c in [col_uf, col_mun, col_cod_ibge, col_cod_siafi, col_valor, col_tipo, col_especie, col_beneficio] if c])
    return base.sort_values("municipio").reset_index(drop=True)

@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def carregar_municipios_ibge_mt() -> pd.DataFrame:
    url = f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{UF_MT}/municipios"
    dados = _request_json(url)

    registros: List[Dict[str, Any]] = []
    for item in dados:
        microrregiao = item.get("microrregiao") or {}
        mesorregiao = microrregiao.get("mesorregiao") or {}
        uf = (mesorregiao.get("UF") or {})
        registros.append(
            {
                "codigo_ibge": str(item.get("id", "")),
                "municipio": item.get("nome", ""),
                "microrregiao_ibge": microrregiao.get("nome", ""),
                "mesorregiao_ibge": mesorregiao.get("nome", ""),
                "uf": uf.get("sigla", "MT"),
                "fonte": "IBGE Localidades",
            }
        )
    df = pd.DataFrame(registros)
    return df.sort_values("municipio").reset_index(drop=True)



@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def carregar_regioes_geograficas_ibge_mt() -> pd.DataFrame:
    """
    Carrega as regiões geográficas imediatas e intermediárias do IBGE para os municípios de Mato Grosso.
    Essa camada não substitui as Regiões de Saúde do SUS; serve como recorte territorial adicional,
    útil para observar polos regionais e agrupamentos territoriais reconhecidos pelo IBGE.
    """
    url_imediatas = f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{UF_MT}/regioes-imediatas"
    imediatas = _request_json(url_imediatas)

    registros: List[Dict[str, Any]] = []
    for imediata in imediatas:
        id_imediata = str(imediata.get("id", "")).strip()
        nome_imediata = imediata.get("nome", "")
        intermediaria = imediata.get("regiao-intermediaria") or imediata.get("regiao_intermediaria") or {}
        id_intermediaria = str(intermediaria.get("id", "")).strip()
        nome_intermediaria = intermediaria.get("nome", "")

        if not id_imediata:
            continue

        url_municipios = f"https://servicodados.ibge.gov.br/api/v1/localidades/regioes-imediatas/{id_imediata}/municipios"
        municipios = _request_json(url_municipios)
        for municipio in municipios:
            codigo_municipio = str(municipio.get("id", "")).strip()
            if not codigo_municipio or not codigo_municipio.startswith(UF_MT):
                continue
            registros.append(
                {
                    "codigo_ibge": codigo_municipio,
                    "municipio": municipio.get("nome", ""),
                    "codigo_regiao_imediata_ibge": id_imediata,
                    "regiao_imediata_ibge": nome_imediata,
                    "codigo_regiao_intermediaria_ibge": id_intermediaria,
                    "regiao_intermediaria_ibge": nome_intermediaria,
                    "fonte_regioes_geograficas_ibge": "IBGE Localidades - Regiões Geográficas",
                }
            )

    df = pd.DataFrame(registros)
    if df.empty:
        raise ValueError("A API de regiões geográficas do IBGE não retornou municípios de Mato Grosso.")
    return df.drop_duplicates(subset=["codigo_ibge"]).sort_values("municipio").reset_index(drop=True)


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def carregar_distritos_ibge_mt() -> pd.DataFrame:
    """
    Carrega os distritos oficiais dos municípios de Mato Grosso pela API de Localidades do IBGE.
    Este dado serve como proxy territorial simples: municípios com vários distritos podem exigir
    validação territorial mais cuidadosa antes de definir construção de UBS.
    """
    url = f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{UF_MT}/distritos"
    dados = _request_json(url)

    registros: List[Dict[str, Any]] = []
    for item in dados:
        municipio = item.get("municipio") or {}
        microrregiao = municipio.get("microrregiao") or {}
        mesorregiao = microrregiao.get("mesorregiao") or {}
        uf = mesorregiao.get("UF") or {}
        codigo_municipio = str(municipio.get("id", "")).strip()
        nome_municipio = municipio.get("nome", "")
        if not codigo_municipio or not str(codigo_municipio).startswith(UF_MT):
            continue
        registros.append(
            {
                "codigo_ibge": codigo_municipio,
                "municipio": nome_municipio,
                "codigo_distrito_ibge": str(item.get("id", "")),
                "distrito_ibge": item.get("nome", ""),
                "uf": uf.get("sigla", "MT"),
                "fonte_distritos": "IBGE Localidades - Distritos",
            }
        )

    df = pd.DataFrame(registros)
    if df.empty:
        raise ValueError("A API de distritos do IBGE não retornou registros de Mato Grosso.")
    return df.sort_values(["municipio", "distrito_ibge"]).reset_index(drop=True)


def consolidar_distritos_por_municipio(df_distritos: pd.DataFrame) -> pd.DataFrame:
    if df_distritos is None or df_distritos.empty:
        return pd.DataFrame(columns=["codigo_ibge", "qtd_distritos_ibge", "distritos_ibge", "fonte_distritos"])
    consolidado = (
        df_distritos.groupby("codigo_ibge")
        .agg(
            qtd_distritos_ibge=("codigo_distrito_ibge", "nunique"),
            distritos_ibge=("distrito_ibge", lambda x: "; ".join(sorted({str(v).strip() for v in x if str(v).strip()}))),
            fonte_distritos=("fonte_distritos", "first"),
        )
        .reset_index()
    )
    consolidado["classificacao_distritos_preliminar"] = consolidado["qtd_distritos_ibge"].apply(classificar_distritos)
    return consolidado


def classificar_distritos(qtd: Any) -> str:
    valor = _parse_numero_sidra(qtd)
    if valor is None:
        return "Sem informação"
    if valor <= 1:
        return "Sede única/sem distritos adicionais"
    if valor <= 3:
        return "Possui distritos oficiais"
    return "Múltiplos distritos oficiais"



def classificar_prioridade_automatica(pontos: Any) -> str:
    valor = _parse_numero_sidra(pontos)
    if valor is None:
        return "Sem pontuação"
    if valor < 30:
        return "Baixa pressão automática"
    if valor < 55:
        return "Média pressão automática"
    if valor < 75:
        return "Alta pressão automática"
    return "Pressão crítica automática"


def gerar_indice_automatico_ubs(base: pd.DataFrame) -> pd.DataFrame:
    """
    Gera uma pontuação preliminar usando somente dados automatizados por API/base pública.
    Esta pontuação NÃO substitui a validação da Coordenadoria APS, da SES/ERS ou do município.
    Ela serve para ordenar municípios que merecem análise mais rápida na etapa seguinte.
    """
    if base is None or base.empty:
        return base

    df = base.copy()

    def pontuar(linha: pd.Series) -> pd.Series:
        pontos = 0
        criterios: List[str] = []

        populacao = _parse_numero_sidra(linha.get("populacao_ibge")) or 0
        esf_ms = _parse_numero_sidra(linha.get("esf_necessarias_ms")) or 0
        densidade = _parse_numero_sidra(linha.get("densidade_calculada_atual"))
        ruralidade = _parse_numero_sidra(linha.get("percentual_rural_2022"))
        distritos = _parse_numero_sidra(linha.get("qtd_distritos_ibge"))
        qtd_ubs_cnes = _parse_numero_sidra(linha.get("qtd_ubs_cnes_automatico"))
        pop_por_ubs_cnes = _parse_numero_sidra(linha.get("populacao_por_ubs_cnes_automatico"))

        # Peso 1: porte/pressão assistencial conforme eSF necessárias pelo parâmetro MS.
        if esf_ms >= 20:
            pontos += 30
            criterios.append("muito alta necessidade teórica de eSF pelo parâmetro MS")
        elif esf_ms >= 10:
            pontos += 24
            criterios.append("alta necessidade teórica de eSF pelo parâmetro MS")
        elif esf_ms >= 5:
            pontos += 16
            criterios.append("necessidade intermediária de eSF pelo parâmetro MS")
        elif esf_ms > 0:
            pontos += 8
            criterios.append("necessidade básica de eSF pelo parâmetro MS")

        # Peso 2: baixa densidade pode indicar dispersão territorial.
        if densidade is not None:
            if densidade < 2:
                pontos += 22
                criterios.append("densidade demográfica muito baixa")
            elif densidade < 5:
                pontos += 16
                criterios.append("baixa densidade demográfica")
            elif densidade < 15:
                pontos += 8
                criterios.append("densidade intermediária")

        # Peso 3: ruralidade pelo Censo 2022.
        if ruralidade is not None:
            if ruralidade >= 50:
                pontos += 22
                criterios.append("muito alta ruralidade no Censo 2022")
            elif ruralidade >= 25:
                pontos += 16
                criterios.append("alta ruralidade no Censo 2022")
            elif ruralidade >= 10:
                pontos += 8
                criterios.append("ruralidade moderada no Censo 2022")

        # Peso 4: existência de múltiplos distritos oficiais.
        if distritos is not None:
            if distritos >= 4:
                pontos += 16
                criterios.append("múltiplos distritos oficiais IBGE")
            elif distritos >= 2:
                pontos += 8
                criterios.append("possui distritos oficiais além da sede")

        # Peso 5: pressão preliminar pela rede física cadastrada no CNES.
        # CNES é base cadastral: aumenta a prioridade de validação, mas não define construção sozinho.
        if qtd_ubs_cnes is not None:
            if qtd_ubs_cnes == 0 and populacao > 0:
                pontos += 18
                criterios.append("nenhuma UBS/USF identificada automaticamente no CNES")
            elif pop_por_ubs_cnes is not None:
                if pop_por_ubs_cnes >= 8000:
                    pontos += 18
                    criterios.append("muita população por UBS/USF cadastrada no CNES")
                elif pop_por_ubs_cnes >= 6000:
                    pontos += 14
                    criterios.append("alta população por UBS/USF cadastrada no CNES")
                elif pop_por_ubs_cnes >= 4000:
                    pontos += 8
                    criterios.append("população por UBS/USF cadastrada no CNES acima de referência preliminar")

        # Peso 6: perfil demográfico do Censo 2022.
        # Envelhecimento não define UBS sozinho, mas sinaliza maior necessidade de acompanhamento longitudinal pela APS.
        indice_envelhecimento = _parse_numero_sidra(linha.get("indice_envelhecimento_2022"))
        idade_mediana = _parse_numero_sidra(linha.get("idade_mediana_2022"))
        if indice_envelhecimento is not None:
            if indice_envelhecimento >= 100:
                pontos += 8
                criterios.append("envelhecimento populacional muito alto")
            elif indice_envelhecimento >= 60:
                pontos += 5
                criterios.append("envelhecimento populacional alto")
        elif idade_mediana is not None and idade_mediana >= 35:
            pontos += 4
            criterios.append("idade mediana elevada")

        # Peso 7: vulnerabilidade educacional. Baixa alfabetização não define UBS sozinha,
        # mas indica maior necessidade de comunicação em saúde, busca ativa e integração APS-Educação.
        taxa_analfabetismo = _parse_numero_sidra(linha.get("taxa_analfabetismo_15mais_2022"))
        if taxa_analfabetismo is not None:
            if taxa_analfabetismo >= 10:
                pontos += 10
                criterios.append("muito alta vulnerabilidade educacional/analfabetismo")
            elif taxa_analfabetismo >= 7:
                pontos += 7
                criterios.append("alta vulnerabilidade educacional/analfabetismo")
            elif taxa_analfabetismo >= 4:
                pontos += 4
                criterios.append("vulnerabilidade educacional moderada")

        # Peso 7b: nível de instrução estrutural da população adulta.
        pct_baixa_instrucao = _parse_numero_sidra(linha.get("pct_sem_instrucao_fund_incompleto_25mais_2022"))
        if pct_baixa_instrucao is not None:
            if pct_baixa_instrucao >= 60:
                pontos += 8
                criterios.append("muito alta proporção de adultos sem instrução/fundamental incompleto")
            elif pct_baixa_instrucao >= 45:
                pontos += 5
                criterios.append("alta proporção de adultos sem instrução/fundamental incompleto")
            elif pct_baixa_instrucao >= 30:
                pontos += 3
                criterios.append("baixa escolaridade adulta moderada")

        # Peso 7c: dispersão educacional-territorial pelo Censo Escolar/INEP.
        # Muitas escolas rurais/indígenas/quilombolas sinalizam territórios com maior necessidade de busca ativa e integração APS-Educação.
        escolas_rurais = _parse_numero_sidra(linha.get("escolas_rurais_inep"))
        escolas_indigenas = _parse_numero_sidra(linha.get("escolas_indigenas_inep"))
        escolas_quilombolas = _parse_numero_sidra(linha.get("escolas_quilombolas_inep"))
        if escolas_rurais is not None:
            if escolas_rurais >= 10:
                pontos += 10
                criterios.append("muitas escolas rurais no Censo Escolar/INEP")
            elif escolas_rurais >= 5:
                pontos += 7
                criterios.append("presença relevante de escolas rurais")
            elif escolas_rurais >= 1:
                pontos += 3
                criterios.append("possui escola rural")
        if escolas_indigenas is not None and escolas_indigenas >= 1:
            pontos += 4
            criterios.append("possui escola indígena registrada no Censo Escolar")
        if escolas_quilombolas is not None and escolas_quilombolas >= 1:
            pontos += 3
            criterios.append("possui escola quilombola registrada no Censo Escolar")

        # Peso 7d: presença de populações tradicionais pelo Censo 2022.
        # Não define UBS isoladamente; sinaliza necessidade de análise de equidade, acesso e adequação cultural/territorial.
        povos_1000 = _parse_numero_sidra(linha.get("pessoas_tradicionais_por_1000_hab_2022"))
        if povos_1000 is not None:
            if povos_1000 >= 100:
                pontos += 8
                criterios.append("alta presença proporcional de população indígena/quilombola")
            elif povos_1000 >= 30:
                pontos += 5
                criterios.append("presença relevante de população indígena/quilombola")
            elif povos_1000 > 0:
                pontos += 2
                criterios.append("possui população indígena/quilombola registrada no Censo 2022")

        # Peso 8: porte populacional absoluto.
        if populacao >= 100000:
            pontos += 10
            criterios.append("município de grande porte populacional")
        elif populacao >= 50000:
            pontos += 8
            criterios.append("município de médio/grande porte populacional")
        elif populacao >= 20000:
            pontos += 5
            criterios.append("município acima de 20 mil habitantes")

        pontos = min(int(pontos), 100)
        return pd.Series(
            {
                "pontuacao_automatica_ubs": pontos,
                "classificacao_automatica_ubs": classificar_prioridade_automatica(pontos),
                "criterios_automaticos_ubs": "; ".join(criterios) if criterios else "sem critério automático relevante",
            }
        )

    pontuacao = df.apply(pontuar, axis=1)
    for coluna in pontuacao.columns:
        df[coluna] = pontuacao[coluna]
    return df.sort_values(["pontuacao_automatica_ubs", "populacao_ibge"], ascending=[False, False]).reset_index(drop=True)


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def carregar_populacao_sidra_mt(ano: int) -> pd.DataFrame:
    filtro_territorial = quote("in n3 51")
    url = f"https://apisidra.ibge.gov.br/values/t/6579/n6/{filtro_territorial}/v/9324/p/{int(ano)}"
    dados = _request_json(url)
    if not dados or len(dados) < 2:
        raise ValueError("A API SIDRA retornou resposta vazia para o ano selecionado.")

    registros: List[Dict[str, Any]] = []
    for item in dados[1:]:
        codigo = str(item.get("D1C", "")).strip()
        municipio = _limpar_nome_municipio(item.get("D1N", ""))
        valor = str(item.get("V", "")).replace(".", "").replace(",", ".").strip()
        if not codigo or not municipio or valor in {"", "...", "-"}:
            continue
        try:
            populacao = int(round(float(valor)))
        except ValueError:
            continue
        faixa, parametro = _parametro_ms(populacao)
        registros.append(
            {
                "codigo_ibge": codigo,
                "municipio": municipio,
                "populacao_ibge": populacao,
                "ano_referencia": int(ano),
                "faixa_populacional_ms": faixa,
                "parametro_pessoas_por_esf": parametro,
                "esf_necessarias_ms": int(math.ceil(populacao / parametro)) if parametro else 0,
                "fonte": "IBGE/SIDRA - Tabela 6579",
            }
        )
    if not registros:
        raise ValueError("Não foi possível interpretar os dados de população retornados pelo SIDRA.")
    return pd.DataFrame(registros).sort_values("municipio").reset_index(drop=True)




def _parse_numero_sidra(valor: Any) -> Optional[float]:
    texto = str(valor or "").strip()
    if texto in {"", "...", "-", "X", "x"}:
        return None
    texto = texto.replace(" ", "")
    # SIDRA pode retornar 903,207 ou 903.207,019 dependendo da tabela/locale.
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def carregar_area_densidade_sidra_mt() -> pd.DataFrame:
    """
    Busca área territorial e densidade demográfica pela Tabela SIDRA 1301.
    A tabela 1301 é uma base territorial histórica do IBGE. O formato do retorno pode variar;
    por isso o parser identifica as variáveis pelo nome e mantém fallback seguro.
    """
    filtro_territorial = quote("in n3 51")
    url = f"https://apisidra.ibge.gov.br/values/t/1301/n6/{filtro_territorial}/v/all/p/all"
    dados = _request_json(url)
    if not dados or len(dados) < 2:
        raise ValueError("A API SIDRA retornou resposta vazia para a tabela 1301.")

    linhas: Dict[str, Dict[str, Any]] = {}
    for item in dados[1:]:
        codigo = str(item.get("D1C", "")).strip()
        municipio = _limpar_nome_municipio(item.get("D1N", ""))
        variavel = str(item.get("D2N", item.get("D3N", ""))).lower()
        valor = _parse_numero_sidra(item.get("V"))
        if not codigo or not municipio or valor is None:
            continue

        registro = linhas.setdefault(
            codigo,
            {
                "codigo_ibge": codigo,
                "municipio": municipio,
                "area_km2": None,
                "densidade_demografica": None,
                "fonte_area_densidade": "IBGE/SIDRA - Tabela 1301",
            },
        )

        if "área" in variavel or "area" in variavel:
            registro["area_km2"] = valor
        elif "densidade" in variavel:
            registro["densidade_demografica"] = valor

    df = pd.DataFrame(linhas.values())
    if df.empty:
        raise ValueError("Não foi possível interpretar área/densidade da tabela SIDRA 1301.")
    return df.sort_values("municipio").reset_index(drop=True)


def classificar_densidade(densidade: Any) -> str:
    valor = _parse_numero_sidra(densidade)
    if valor is None:
        return "Sem informação"
    if valor < 2:
        return "Muito baixa densidade"
    if valor < 5:
        return "Baixa densidade"
    if valor < 15:
        return "Densidade intermediária"
    return "Maior concentração populacional"



@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def carregar_urbano_rural_sidra_mt(ano_censo: int = 2022) -> pd.DataFrame:
    """
    Busca população urbana/rural pela Tabela SIDRA 9923.
    Como a API SIDRA pode variar a posição dos metadados D2/D3 conforme a consulta,
    o parser identifica automaticamente as categorias Total, Urbana e Rural.
    """
    filtro_territorial = quote("in n3 51")
    erros: List[str] = []

    # A classificação costuma ser c1, mas mantemos tentativas para evitar quebra se o SIDRA alterar metadados.
    tentativas = [
        f"https://apisidra.ibge.gov.br/values/t/9923/n6/{filtro_territorial}/v/93/p/{int(ano_censo)}/c1/all",
        f"https://apisidra.ibge.gov.br/values/t/9923/n6/{filtro_territorial}/v/all/p/{int(ano_censo)}/c1/all",
        f"https://apisidra.ibge.gov.br/values/t/9923/n6/{filtro_territorial}/v/93/p/{int(ano_censo)}/c2/all",
    ]

    for url in tentativas:
        try:
            dados = _request_json(url)
            if not dados or len(dados) < 2:
                erros.append(f"Resposta vazia: {url}")
                continue

            linhas: Dict[str, Dict[str, Any]] = {}
            for item in dados[1:]:
                codigo = str(item.get("D1C", "")).strip()
                municipio = _limpar_nome_municipio(item.get("D1N", ""))
                valor = _parse_numero_sidra(item.get("V"))
                if not codigo or not municipio or valor is None:
                    continue

                categoria = None
                for chave, texto in item.items():
                    if not chave.endswith("N"):
                        continue
                    texto_limpo = str(texto or "").strip().lower()
                    if texto_limpo in {"total", "urbana", "rural"}:
                        categoria = texto_limpo
                        break

                if categoria not in {"total", "urbana", "rural"}:
                    continue

                registro = linhas.setdefault(
                    codigo,
                    {
                        "codigo_ibge": codigo,
                        "municipio": municipio,
                        "populacao_total_censo_2022": None,
                        "populacao_urbana_2022": None,
                        "populacao_rural_2022": None,
                        "fonte_urbano_rural": "IBGE/SIDRA - Tabela 9923 (Censo 2022)",
                        "ano_base_ruralidade": int(ano_censo),
                    },
                )

                if categoria == "total":
                    registro["populacao_total_censo_2022"] = int(round(valor))
                elif categoria == "urbana":
                    registro["populacao_urbana_2022"] = int(round(valor))
                elif categoria == "rural":
                    registro["populacao_rural_2022"] = int(round(valor))

            df = pd.DataFrame(linhas.values())
            if df.empty:
                erros.append(f"Sem categorias interpretáveis: {url}")
                continue

            # Se total não vier ou vier inconsistente, recalcula pelo urbano + rural quando possível.
            if "populacao_total_censo_2022" in df.columns:
                total_recalculado = df[["populacao_urbana_2022", "populacao_rural_2022"]].fillna(0).sum(axis=1)
                df["populacao_total_censo_2022"] = df["populacao_total_censo_2022"].fillna(total_recalculado).astype(int)

            df["percentual_rural_2022"] = df.apply(
                lambda linha: round((linha.get("populacao_rural_2022") or 0) / linha.get("populacao_total_censo_2022") * 100, 2)
                if linha.get("populacao_total_censo_2022") else None,
                axis=1,
            )
            df["classificacao_ruralidade_preliminar"] = df["percentual_rural_2022"].apply(classificar_ruralidade)
            return df.sort_values("municipio").reset_index(drop=True)
        except Exception as exc:
            erros.append(str(exc))
            continue

    raise ValueError("Não foi possível carregar urbano/rural pela SIDRA 9923. Tentativas: " + " | ".join(erros[:3]))


def classificar_ruralidade(percentual: Any) -> str:
    valor = _parse_numero_sidra(percentual)
    if valor is None:
        return "Sem informação"
    if valor < 10:
        return "Predominantemente urbano"
    if valor < 25:
        return "Ruralidade moderada"
    if valor < 50:
        return "Alta ruralidade"
    return "Muito alta ruralidade"


def classificar_envelhecimento(indice: Any) -> str:
    valor = _parse_numero_sidra(indice)
    if valor is None:
        return "Sem informação"
    if valor < 30:
        return "Baixo envelhecimento"
    if valor < 60:
        return "Envelhecimento moderado"
    if valor < 100:
        return "Alto envelhecimento"
    return "Muito alto envelhecimento"


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def carregar_indicadores_demograficos_sidra_mt(ano_censo: int = 2022) -> pd.DataFrame:
    """
    Busca indicadores demográficos sintéticos pela Tabela SIDRA 9515:
    índice de envelhecimento, idade mediana e razão de sexo.
    Esses indicadores entram como perfil demográfico estrutural do Censo 2022,
    sem substituir a população estimada usada no cálculo MS.
    """
    filtro_territorial = quote("in n3 51")
    url = f"https://apisidra.ibge.gov.br/values/t/9515/n6/{filtro_territorial}/v/all/p/{int(ano_censo)}"
    dados = _request_json(url)
    if not dados or len(dados) < 2:
        raise ValueError("A API SIDRA retornou resposta vazia para a tabela 9515.")

    linhas: Dict[str, Dict[str, Any]] = {}
    for item in dados[1:]:
        codigo = str(item.get("D1C", "")).strip()
        municipio = _limpar_nome_municipio(item.get("D1N", ""))
        valor = _parse_numero_sidra(item.get("V"))
        if not codigo or not municipio or valor is None:
            continue

        nome_variavel = " ".join(
            str(v or "") for k, v in item.items()
            if k.endswith("N") and str(v or "").strip()
        ).lower()

        registro = linhas.setdefault(
            codigo,
            {
                "codigo_ibge": codigo,
                "municipio": municipio,
                "indice_envelhecimento_2022": None,
                "idade_mediana_2022": None,
                "razao_sexo_2022": None,
                "ano_base_demografia": int(ano_censo),
                "fonte_demografia": "IBGE/SIDRA - Tabela 9515 (Censo 2022)",
            },
        )

        if "indice de envelhecimento" in nome_variavel or "índice de envelhecimento" in nome_variavel:
            registro["indice_envelhecimento_2022"] = round(valor, 2)
        elif "idade mediana" in nome_variavel:
            registro["idade_mediana_2022"] = round(valor, 2)
        elif "razao de sexo" in nome_variavel or "razão de sexo" in nome_variavel:
            registro["razao_sexo_2022"] = round(valor, 2)

    df = pd.DataFrame(linhas.values())
    if df.empty:
        raise ValueError("Não foi possível interpretar os indicadores demográficos da tabela 9515.")

    df["classificacao_envelhecimento_preliminar"] = df["indice_envelhecimento_2022"].apply(classificar_envelhecimento)
    return df.sort_values("municipio").reset_index(drop=True)





@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def classificar_vulnerabilidade_educacional(taxa_alfabetizacao: Any) -> str:
    valor = _parse_numero_sidra(taxa_alfabetizacao)
    if valor is None:
        return "Sem informação"
    taxa_analfabetismo = max(0.0, 100.0 - float(valor))
    if taxa_analfabetismo < 4:
        return "Baixa vulnerabilidade educacional"
    if taxa_analfabetismo < 7:
        return "Vulnerabilidade educacional moderada"
    if taxa_analfabetismo < 10:
        return "Alta vulnerabilidade educacional"
    return "Muito alta vulnerabilidade educacional"


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def carregar_alfabetizacao_sidra_mt(ano_censo: int = 2022) -> pd.DataFrame:
    """Busca taxa de alfabetização 15+ pela SIDRA 9543, com recorte total municipal."""
    filtro_territorial = quote("in n3 51")
    urls = [
        f"https://apisidra.ibge.gov.br/values/t/9543/n6/{filtro_territorial}/v/all/p/{int(ano_censo)}/c2/0/c86/0/c287/0?formato=json",
        f"https://apisidra.ibge.gov.br/values/t/9543/n6/{filtro_territorial}/v/all/p/{int(ano_censo)}?formato=json",
    ]
    erros: List[str] = []
    for url in urls:
        try:
            dados = _request_json(url, timeout=60)
            if not dados or len(dados) < 2:
                erros.append("resposta vazia")
                continue
            registros: List[Dict[str, Any]] = []
            vistos = set()
            for item in dados[1:]:
                codigo = str(item.get("D1C", "")).strip()
                municipio = _limpar_nome_municipio(item.get("D1N", ""))
                valor = _parse_numero_sidra(item.get("V"))
                if not codigo or not municipio or valor is None:
                    continue

                nomes_categorias = " | ".join(
                    str(v or "") for k, v in item.items()
                    if k.endswith("N") and k not in {"D1N", "D2N"}
                ).lower()
                categorias_desagregadas = [
                    "homens", "mulheres", "branca", "preta", "parda", "amarela",
                    "indígena", "indigena", "15 a 19", "20 a 24", "25 a 34",
                    "35 a 44", "45 a 54", "55 a 64", "65 anos"
                ]
                if any(palavra in nomes_categorias for palavra in categorias_desagregadas):
                    continue
                if codigo in vistos:
                    continue
                vistos.add(codigo)
                taxa_alfabetizacao = round(float(valor), 2)
                taxa_analfabetismo = round(max(0.0, 100.0 - taxa_alfabetizacao), 2)
                registros.append(
                    {
                        "codigo_ibge": codigo,
                        "municipio": municipio,
                        "taxa_alfabetizacao_15mais_2022": taxa_alfabetizacao,
                        "taxa_analfabetismo_15mais_2022": taxa_analfabetismo,
                        "classificacao_vulnerabilidade_educacional": classificar_vulnerabilidade_educacional(taxa_alfabetizacao),
                        "ano_base_alfabetizacao": int(ano_censo),
                        "fonte_alfabetizacao": "IBGE/SIDRA - Tabela 9543 (Censo 2022)",
                    }
                )
            df = pd.DataFrame(registros)
            if not df.empty:
                return df.sort_values("municipio").reset_index(drop=True)
            erros.append("sem registros interpretáveis")
        except Exception as exc:
            erros.append(str(exc))
            continue
    raise ValueError("Não foi possível carregar alfabetização pela SIDRA 9543. Tentativas: " + " | ".join(erros[:3]))


def classificar_vulnerabilidade_instrucao(pct_sem_instrucao_fund_incompleto: Any) -> str:
    valor = _parse_numero_sidra(pct_sem_instrucao_fund_incompleto)
    if valor is None:
        return "Sem informação"
    if valor < 30:
        return "Baixa vulnerabilidade por nível de instrução"
    if valor < 45:
        return "Vulnerabilidade por nível de instrução moderada"
    if valor < 60:
        return "Alta vulnerabilidade por nível de instrução"
    return "Muito alta vulnerabilidade por nível de instrução"


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def carregar_nivel_instrucao_sidra_mt(ano_censo: int = 2022) -> pd.DataFrame:
    """
    Busca nível de instrução pelo SIDRA 10061, usando metadados para localizar categorias.
    Foco inicial: população de 25 anos ou mais com sem instrução/fundamental incompleto
    e população com médio completo ou mais.
    """
    base = carregar_municipios_ibge_mt()[["codigo_ibge", "municipio"]].copy()
    erros: List[str] = []
    alertas: List[str] = []

    try:
        df_baixa, desc_baixa = _carregar_indicador_saneamento_sidra(
            tabela=10061,
            ano=ano_censo,
            palavras_any=["sem instrucao", "sem instrução", "fundamental incompleto"],
            palavras_all=None,
            nome_coluna="pct_sem_instrucao_fund_incompleto_25mais_2022",
        )
        base = base.merge(df_baixa[["codigo_ibge", "pct_sem_instrucao_fund_incompleto_25mais_2022"]], on="codigo_ibge", how="left")
        alertas.append("baixa instrução: " + desc_baixa)
    except Exception as exc:
        base["pct_sem_instrucao_fund_incompleto_25mais_2022"] = None
        erros.append(f"sem instrução/fundamental incompleto SIDRA 10061: {exc}")

    try:
        df_medio, desc_medio = _carregar_indicador_saneamento_sidra(
            tabela=10061,
            ano=ano_censo,
            palavras_any=["medio completo", "médio completo", "superior incompleto", "superior completo"],
            palavras_all=None,
            nome_coluna="pct_medio_completo_ou_mais_25mais_2022",
        )
        base = base.merge(df_medio[["codigo_ibge", "pct_medio_completo_ou_mais_25mais_2022"]], on="codigo_ibge", how="left")
        alertas.append("médio ou mais: " + desc_medio)
    except Exception as exc:
        base["pct_medio_completo_ou_mais_25mais_2022"] = None
        erros.append(f"médio completo ou mais SIDRA 10061: {exc}")

    def calcular_indice(row: pd.Series) -> Optional[float]:
        baixa = _parse_numero_sidra(row.get("pct_sem_instrucao_fund_incompleto_25mais_2022"))
        medio_mais = _parse_numero_sidra(row.get("pct_medio_completo_ou_mais_25mais_2022"))
        componentes = []
        if baixa is not None:
            componentes.append(float(baixa))
        if medio_mais is not None:
            componentes.append(max(0.0, 100.0 - float(medio_mais)))
        if not componentes:
            return None
        return round(sum(componentes) / len(componentes), 2)

    base["indice_vulnerabilidade_instrucao_2022"] = base.apply(calcular_indice, axis=1)
    base["classificacao_vulnerabilidade_instrucao"] = base["indice_vulnerabilidade_instrucao_2022"].apply(classificar_vulnerabilidade_instrucao)
    base["ano_base_instrucao"] = int(ano_censo)
    base["fonte_instrucao"] = "IBGE/SIDRA - Tabela 10061 (Censo 2022), com leitura por metadados"
    if erros:
        base["alerta_instrucao"] = " | ".join(erros[:5])
    if alertas:
        base["metodo_instrucao"] = " || ".join(alertas[:5])
    return base.sort_values("municipio").reset_index(drop=True)



def classificar_vulnerabilidade_renda(valor_rdpc: Any, pct_ate_meio_sm: Any = None) -> str:
    """Classificação simples de vulnerabilidade econômica a partir do RDPC Censo 2022."""
    rdpc = _parse_numero_sidra(valor_rdpc)
    pct_baixa = _parse_numero_sidra(pct_ate_meio_sm)
    if rdpc is None and pct_baixa is None:
        return "Sem informação"
    pontos = 0
    if rdpc is not None:
        if rdpc < 700:
            pontos += 3
        elif rdpc < 1000:
            pontos += 2
        elif rdpc < 1400:
            pontos += 1
    if pct_baixa is not None:
        if pct_baixa >= 45:
            pontos += 3
        elif pct_baixa >= 30:
            pontos += 2
        elif pct_baixa >= 20:
            pontos += 1
    if pontos >= 5:
        return "Muito alta vulnerabilidade de renda"
    if pontos >= 3:
        return "Alta vulnerabilidade de renda"
    if pontos >= 1:
        return "Vulnerabilidade de renda moderada"
    return "Baixa vulnerabilidade de renda"


def _extrair_sidra_municipio_valor(item: Dict[str, Any]) -> tuple[Optional[str], Optional[str], Optional[float], str]:
    codigo = None
    municipio = None
    for k, v in item.items():
        sv = str(v or "").strip()
        if k.endswith("C") and sv.isdigit() and len(sv) == 7 and sv.startswith(UF_MT):
            codigo = sv
            municipio = _limpar_nome_municipio(item.get(k[:-1] + "N", ""))
            break
    if not codigo:
        codigo = str(item.get("D1C", "")).strip()
        municipio = _limpar_nome_municipio(item.get("D1N", ""))
    valor = _parse_numero_sidra(item.get("V"))
    desc = _descricao_sidra_item(item)
    return codigo, municipio, valor, desc


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def carregar_renda_censo_sidra_mt(ano_censo: int = 2022) -> pd.DataFrame:
    """Carrega indicadores preliminares de renda do Censo 2022 em nível municipal."""
    base = carregar_municipios_ibge_mt()[["codigo_ibge", "municipio"]].copy()
    erros: List[str] = []
    metodos: List[str] = []

    try:
        filtro_territorial = quote("in n3 51")
        urls = [
            f"https://apisidra.ibge.gov.br/values/t/10295/n6/{filtro_territorial}/v/all/p/{int(ano_censo)}/c2/0/c86/0?formato=json",
            f"https://apisidra.ibge.gov.br/values/t/10295/n6/{filtro_territorial}/v/all/p/{int(ano_censo)}?formato=json",
        ]
        registros = []
        for url in urls:
            dados = _request_json(url, timeout=90)
            temporarios = []
            for item in (dados or [])[1:]:
                codigo, municipio, valor, desc = _extrair_sidra_municipio_valor(item)
                if not codigo or not municipio or valor is None:
                    continue
                desc_norm = _normalizar_texto_busca(desc)
                if "coeficiente" in desc_norm or "cv" in desc_norm:
                    continue
                if any(p in desc_norm for p in ["homens", "mulheres", "branca", "preta", "parda", "amarela", "indigena"]):
                    continue
                temporarios.append({
                    "codigo_ibge": codigo,
                    "municipio": municipio,
                    "rendimento_domiciliar_per_capita_medio_2022": round(float(valor), 2),
                })
            if temporarios:
                registros = temporarios
                metodos.append("RDPC médio: SIDRA 10295")
                break
        df_rdpc = pd.DataFrame(registros).drop_duplicates(subset=["codigo_ibge"], keep="first") if registros else pd.DataFrame()
        if df_rdpc.empty:
            raise ValueError("Tabela 10295 acessada, mas sem registros municipais interpretáveis.")
        base = base.merge(df_rdpc[["codigo_ibge", "rendimento_domiciliar_per_capita_medio_2022"]], on="codigo_ibge", how="left")
    except Exception as exc:
        base["rendimento_domiciliar_per_capita_medio_2022"] = None
        erros.append(f"RDPC médio SIDRA 10295: {exc}")

    try:
        df_quarto, desc_quarto = _carregar_indicador_saneamento_sidra(
            tabela=10296,
            ano=ano_censo,
            palavras_any=["ate 1/4", "até 1/4", "1/4 de salario", "1/4 de salário"],
            palavras_all=None,
            nome_coluna="pct_rdpc_ate_1_4_sm_2022",
        )
        base = base.merge(df_quarto[["codigo_ibge", "pct_rdpc_ate_1_4_sm_2022"]], on="codigo_ibge", how="left")
        metodos.append("RDPC até 1/4 SM: " + desc_quarto)
    except Exception as exc:
        base["pct_rdpc_ate_1_4_sm_2022"] = None
        erros.append(f"RDPC até 1/4 SM SIDRA 10296: {exc}")

    try:
        df_q, _ = _carregar_indicador_saneamento_sidra(10296, ano_censo, ["ate 1/4", "até 1/4", "1/4 de salario", "1/4 de salário"], None, "_pct_q")
        df_m, _ = _carregar_indicador_saneamento_sidra(10296, ano_censo, ["mais de 1/4 a 1/2", "1/4 a 1/2", "ate 1/2", "até 1/2"], None, "_pct_m")
        aux = df_q[["codigo_ibge", "_pct_q"]].merge(df_m[["codigo_ibge", "_pct_m"]], on="codigo_ibge", how="outer")
        aux["pct_rdpc_ate_1_2_sm_2022"] = (
            pd.to_numeric(aux["_pct_q"], errors="coerce").fillna(0)
            + pd.to_numeric(aux["_pct_m"], errors="coerce").fillna(0)
        ).clip(0, 100).round(2)
        base = base.merge(aux[["codigo_ibge", "pct_rdpc_ate_1_2_sm_2022"]], on="codigo_ibge", how="left")
        metodos.append("RDPC até 1/2 SM: SIDRA 10296")
    except Exception as exc:
        base["pct_rdpc_ate_1_2_sm_2022"] = None
        erros.append(f"RDPC até 1/2 SM SIDRA 10296: {exc}")

    base["classificacao_vulnerabilidade_renda"] = base.apply(
        lambda linha: classificar_vulnerabilidade_renda(
            linha.get("rendimento_domiciliar_per_capita_medio_2022"),
            linha.get("pct_rdpc_ate_1_2_sm_2022"),
        ),
        axis=1,
    )
    base["ano_base_renda"] = int(ano_censo)
    base["fonte_renda"] = "IBGE/SIDRA - Censo 2022: Tabelas 10295 e 10296"
    base["observacao_renda"] = "Camada socioeconômica agregada por município; não usa dados pessoais e não define construção de UBS isoladamente."
    if erros:
        base["alerta_renda"] = " | ".join(erros[:5])
    if metodos:
        base["metodo_renda"] = " || ".join(metodos[:5])
    return base.sort_values("municipio").reset_index(drop=True)


def testar_renda_ibge(ano_censo: int = 2022) -> Dict[str, Any]:
    try:
        df = carregar_renda_censo_sidra_mt(ano_censo)
        col = "rendimento_domiciliar_per_capita_medio_2022"
        qtd_rdpc = int(df[col].notna().sum()) if col in df.columns else 0
        media_rdpc = float(df[col].dropna().mean()) if col in df.columns and df[col].notna().any() else None
        qtd_baixa = int(df["classificacao_vulnerabilidade_renda"].astype(str).str.contains("Alta|Muito alta", regex=True, na=False).sum()) if "classificacao_vulnerabilidade_renda" in df.columns else 0
        top = []
        if col in df.columns:
            top = df.sort_values(col, ascending=True, na_position="last")[["municipio", col, "classificacao_vulnerabilidade_renda"]].head(5).to_dict("records")
        return {
            "ok": qtd_rdpc > 0,
            "ano": ano_censo,
            "municipios_lidos": int(len(df)),
            "municipios_com_rdpc": qtd_rdpc,
            "rdpc_medio_mt": round(media_rdpc, 2) if media_rdpc is not None else None,
            "municipios_alta_ou_muito_alta_vulnerabilidade_renda": qtd_baixa,
            "top_menor_rdpc": top,
            "fonte": "IBGE/SIDRA - Censo 2022: Trabalho e Rendimento",
        }
    except Exception as exc:
        return {"ok": False, "ano": ano_censo, "erro": str(exc), "fonte": "IBGE/SIDRA - Censo 2022: Trabalho e Rendimento"}



def classificar_pressao_deficiencia(valor_pct: Any) -> str:
    valor = _parse_numero_sidra(valor_pct)
    if valor is None:
        return "Sem informação"
    if valor < 5:
        return "Baixa pressão por deficiência/autismo"
    if valor < 8:
        return "Pressão moderada por deficiência/autismo"
    if valor < 12:
        return "Alta pressão por deficiência/autismo"
    return "Muito alta pressão por deficiência/autismo"


def _carregar_sidra_populacao_alvo_total(
    tabela: int,
    ano: int,
    palavras_alvo: List[str],
    palavras_excluir_alvo: Optional[List[str]] = None,
    timeout: int = 120,
) -> pd.DataFrame:
    """Leitura flexível de tabelas SIDRA com total e grupo-alvo por município.

    A função não depende rigidamente de categorias específicas: ela lê v/all e usa
    descrições retornadas pelo SIDRA para separar linhas de total e linhas do grupo-alvo.
    """
    filtro_territorial = quote("in n3 51")
    urls = [
        f"https://apisidra.ibge.gov.br/values/t/{int(tabela)}/n6/{filtro_territorial}/v/all/p/{int(ano)}?formato=json",
    ]
    alvo_norm = [_normalizar_texto_busca(x) for x in palavras_alvo]
    excluir_norm = [_normalizar_texto_busca(x) for x in (palavras_excluir_alvo or [])]
    registros = []
    for url in urls:
        dados = _request_json(url, timeout=timeout)
        for item in (dados or [])[1:]:
            codigo, municipio, valor, desc = _extrair_sidra_municipio_valor(item)
            if not codigo or not str(codigo).startswith(UF_MT) or valor is None:
                continue
            desc_norm = _normalizar_texto_busca(desc)
            if not desc_norm:
                continue
            eh_alvo = any(p in desc_norm for p in alvo_norm) and not any(p in desc_norm for p in excluir_norm)
            # Linhas de total normalmente vêm com categoria "Total" sem o texto específico do alvo.
            eh_total = (
                "total" in desc_norm
                and not any(p in desc_norm for p in alvo_norm)
                and not any(x in desc_norm for x in ["coeficiente", "cv", "%"])
            )
            if eh_alvo or eh_total:
                registros.append({
                    "codigo_ibge": str(codigo),
                    "municipio": municipio,
                    "valor": float(valor),
                    "tipo": "alvo" if eh_alvo else "total",
                    "descricao_sidra": desc_norm[:400],
                })
    if not registros:
        return pd.DataFrame(columns=["codigo_ibge", "municipio", "valor_alvo", "valor_total", "metodo"])
    df = pd.DataFrame(registros)
    # Quando há múltiplas linhas por município (sexo/faixa/cor), usar a maior linha total/alvo evita somar recortes.
    alvo = df[df["tipo"] == "alvo"].sort_values("valor", ascending=False).drop_duplicates("codigo_ibge")
    total = df[df["tipo"] == "total"].sort_values("valor", ascending=False).drop_duplicates("codigo_ibge")
    out = alvo[["codigo_ibge", "municipio", "valor", "descricao_sidra"]].rename(columns={"valor": "valor_alvo", "descricao_sidra": "metodo_alvo"})
    if not total.empty:
        out = out.merge(total[["codigo_ibge", "valor", "descricao_sidra"]].rename(columns={"valor": "valor_total", "descricao_sidra": "metodo_total"}), on="codigo_ibge", how="left")
    else:
        out["valor_total"] = None
        out["metodo_total"] = None
    out["metodo"] = out.apply(lambda r: f"alvo: {r.get('metodo_alvo')} | total: {r.get('metodo_total')}", axis=1)
    return out


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def carregar_deficiencia_autismo_ibge_mt(ano_censo: int = 2022) -> pd.DataFrame:
    """Carrega pessoas com deficiência e pessoas diagnosticadas com TEA pelo Censo 2022/SIDRA.

    Fonte preliminar: tabelas 10125 (pessoas com deficiência) e 10145 (diagnóstico de autismo).
    """
    base = carregar_municipios_ibge_mt()[["codigo_ibge", "municipio"]].copy()
    erros: List[str] = []
    metodos: List[str] = []

    try:
        df_def = _carregar_sidra_populacao_alvo_total(
            tabela=10125,
            ano=ano_censo,
            palavras_alvo=["pessoas com deficiência", "com deficiência", "existencia de deficiencia", "existência de deficiência"],
            palavras_excluir_alvo=["sem deficiência", "sem deficiencia", "não tinha deficiência", "nao tinha deficiencia"],
        )
        if df_def.empty:
            raise ValueError("Tabela 10125 acessada, mas sem registros-alvo interpretáveis.")
        df_def = df_def.rename(columns={"valor_alvo": "pessoas_com_deficiencia_2022", "valor_total": "populacao_2mais_referencia_deficiencia_2022", "metodo": "metodo_deficiencia"})
        base = base.merge(df_def[["codigo_ibge", "pessoas_com_deficiencia_2022", "populacao_2mais_referencia_deficiencia_2022", "metodo_deficiencia"]], on="codigo_ibge", how="left")
        metodos.append("deficiência: SIDRA 10125")
    except Exception as exc:
        base["pessoas_com_deficiencia_2022"] = None
        base["populacao_2mais_referencia_deficiencia_2022"] = None
        base["metodo_deficiencia"] = None
        erros.append(f"deficiência SIDRA 10125: {exc}")

    try:
        df_tea = _carregar_sidra_populacao_alvo_total(
            tabela=10145,
            ano=ano_censo,
            palavras_alvo=["diagnosticada com autismo", "diagnosticado com autismo", "transtorno do espectro autista", "autismo"],
            palavras_excluir_alvo=["sem diagnóstico", "sem diagnostico", "não diagnosticada", "nao diagnosticada"],
        )
        if df_tea.empty:
            raise ValueError("Tabela 10145 acessada, mas sem registros-alvo interpretáveis.")
        df_tea = df_tea.rename(columns={"valor_alvo": "pessoas_diagnosticadas_autismo_2022", "valor_total": "populacao_referencia_autismo_2022", "metodo": "metodo_autismo"})
        base = base.merge(df_tea[["codigo_ibge", "pessoas_diagnosticadas_autismo_2022", "populacao_referencia_autismo_2022", "metodo_autismo"]], on="codigo_ibge", how="left")
        metodos.append("autismo: SIDRA 10145")
    except Exception as exc:
        base["pessoas_diagnosticadas_autismo_2022"] = None
        base["populacao_referencia_autismo_2022"] = None
        base["metodo_autismo"] = None
        erros.append(f"autismo SIDRA 10145: {exc}")

    for col in ["pessoas_com_deficiencia_2022", "pessoas_diagnosticadas_autismo_2022", "populacao_2mais_referencia_deficiencia_2022", "populacao_referencia_autismo_2022"]:
        base[col] = pd.to_numeric(base.get(col), errors="coerce")

    base["pct_pessoas_com_deficiencia_2022"] = base.apply(
        lambda r: round((r.get("pessoas_com_deficiencia_2022") / r.get("populacao_2mais_referencia_deficiencia_2022")) * 100, 2)
        if pd.notna(r.get("pessoas_com_deficiencia_2022")) and pd.notna(r.get("populacao_2mais_referencia_deficiencia_2022")) and r.get("populacao_2mais_referencia_deficiencia_2022") else None,
        axis=1,
    )
    base["pct_pessoas_diagnosticadas_autismo_2022"] = base.apply(
        lambda r: round((r.get("pessoas_diagnosticadas_autismo_2022") / r.get("populacao_referencia_autismo_2022")) * 100, 2)
        if pd.notna(r.get("pessoas_diagnosticadas_autismo_2022")) and pd.notna(r.get("populacao_referencia_autismo_2022")) and r.get("populacao_referencia_autismo_2022") else None,
        axis=1,
    )
    base["classificacao_pressao_deficiencia_autismo"] = base["pct_pessoas_com_deficiencia_2022"].apply(classificar_pressao_deficiencia)
    base["ano_base_deficiencia_autismo"] = int(ano_censo)
    base["fonte_deficiencia_autismo"] = "IBGE/SIDRA - Censo 2022: tabelas 10125 e 10145"
    base["observacao_deficiencia_autismo"] = "Camada de cuidado continuado e equidade. Não substitui BPC/MDS; qualifica demanda potencial para APS, reabilitação, cuidado domiciliar e articulação intersetorial."
    if erros:
        base["alerta_deficiencia_autismo"] = " | ".join(erros[:5])
    if metodos:
        base["metodo_deficiencia_autismo"] = " || ".join(metodos[:5])
    return base.sort_values("municipio").reset_index(drop=True)


def testar_deficiencia_autismo_ibge(ano_censo: int = 2022) -> Dict[str, Any]:
    try:
        df = carregar_deficiencia_autismo_ibge_mt(ano_censo)
        qtd_def = int(pd.to_numeric(df.get("pessoas_com_deficiencia_2022"), errors="coerce").fillna(0).gt(0).sum()) if "pessoas_com_deficiencia_2022" in df.columns else 0
        qtd_tea = int(pd.to_numeric(df.get("pessoas_diagnosticadas_autismo_2022"), errors="coerce").fillna(0).gt(0).sum()) if "pessoas_diagnosticadas_autismo_2022" in df.columns else 0
        total_def = int(pd.to_numeric(df.get("pessoas_com_deficiencia_2022"), errors="coerce").fillna(0).sum()) if "pessoas_com_deficiencia_2022" in df.columns else 0
        total_tea = int(pd.to_numeric(df.get("pessoas_diagnosticadas_autismo_2022"), errors="coerce").fillna(0).sum()) if "pessoas_diagnosticadas_autismo_2022" in df.columns else 0
        top_def = []
        if "pessoas_com_deficiencia_2022" in df.columns:
            top_def = df.sort_values("pessoas_com_deficiencia_2022", ascending=False, na_position="last")[["municipio", "pessoas_com_deficiencia_2022", "pct_pessoas_com_deficiencia_2022"]].head(5).to_dict("records")
        return {
            "ok": total_def > 0 or total_tea > 0,
            "ano": ano_censo,
            "municipios_lidos": int(len(df)),
            "municipios_com_pessoas_com_deficiencia": qtd_def,
            "municipios_com_pessoas_diagnosticadas_autismo": qtd_tea,
            "pessoas_com_deficiencia_total_mt": total_def,
            "pessoas_diagnosticadas_autismo_total_mt": total_tea,
            "top_deficiencia": top_def,
            "fonte": "IBGE/SIDRA - Censo 2022: pessoas com deficiência e TEA",
        }
    except Exception as exc:
        return {"ok": False, "ano": ano_censo, "erro": str(exc), "fonte": "IBGE/SIDRA - Censo 2022: pessoas com deficiência e TEA"}


def classificar_vulnerabilidade_saneamento(valor_indice: Any) -> str:
    valor = _parse_numero_sidra(valor_indice)
    if valor is None:
        return "Sem informação"
    if valor < 15:
        return "Baixa vulnerabilidade de saneamento"
    if valor < 30:
        return "Vulnerabilidade de saneamento moderada"
    if valor < 50:
        return "Alta vulnerabilidade de saneamento"
    return "Muito alta vulnerabilidade de saneamento"


def _normalizar_texto_busca(texto: Any) -> str:
    texto = str(texto or "").lower()
    mapa = str.maketrans(
        "áàãâäéèêëíìîïóòõôöúùûüçñ",
        "aaaaaeeeeiiiiooooouuuucn",
    )
    return texto.translate(mapa)


def _descricao_sidra_item(item: Dict[str, Any]) -> str:
    partes = []
    for k, v in item.items():
        if k.endswith("N") or k in {"MN", "NC", "NN"}:
            valor = str(v or "").strip()
            if valor:
                partes.append(valor)
    return _normalizar_texto_busca(" | ".join(partes))


def _carregar_tabela_sidra_municipal_total(tabela: int, ano: int = 2022, timeout: int = 90) -> List[Dict[str, Any]]:
    filtro_territorial = quote("in n3 51")
    url = f"https://apisidra.ibge.gov.br/values/t/{int(tabela)}/n6/{filtro_territorial}/v/all/p/{int(ano)}?formato=json"
    dados = _request_json(url, timeout=timeout)
    if not dados or len(dados) < 2:
        raise ValueError(f"SIDRA {tabela} retornou resposta vazia.")
    return dados[1:]



def _sidra_meta_agregado(tabela: int) -> Dict[str, Any]:
    """Carrega metadados do agregado no serviço oficial de agregados do IBGE."""
    urls = [
        f"https://servicodados.ibge.gov.br/api/v3/agregados/{int(tabela)}/metadados",
        f"https://servicodados.ibge.gov.br/api/v3/agregados/{int(tabela)}/metadados?localidades=N6[all]",
    ]
    ultimo_erro = None
    for url in urls:
        try:
            dados = _request_json(url, timeout=45)
            if isinstance(dados, list) and dados:
                return dados[0]
            if isinstance(dados, dict) and dados:
                return dados
        except Exception as exc:
            ultimo_erro = exc
    raise ValueError(f"Não foi possível carregar metadados da tabela SIDRA {tabela}: {ultimo_erro}")


def _meta_variaveis(meta: Dict[str, Any]) -> List[Dict[str, str]]:
    variaveis = meta.get("variaveis") or meta.get("variables") or []
    saida = []
    for var in variaveis:
        vid = str(var.get("id") or var.get("codigo") or var.get("cod") or "").strip()
        nome = str(var.get("nome") or var.get("name") or "").strip()
        unidade = str(var.get("unidade") or var.get("unit") or "").strip()
        if vid:
            saida.append({"id": vid, "nome": nome, "unidade": unidade})
    return saida


def _meta_classificacoes(meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    classificacoes = meta.get("classificacoes") or meta.get("classifications") or []
    saida = []
    for cla in classificacoes:
        cid = str(cla.get("id") or cla.get("codigo") or cla.get("cod") or "").strip()
        nome = str(cla.get("nome") or cla.get("name") or "").strip()
        cats_brutas = cla.get("categorias") or cla.get("categories") or []
        cats = []
        if isinstance(cats_brutas, dict):
            iter_cats = []
            for k, v in cats_brutas.items():
                if isinstance(v, dict):
                    item = dict(v)
                    item.setdefault("id", k)
                    iter_cats.append(item)
                else:
                    iter_cats.append({"id": k, "nome": str(v)})
        else:
            iter_cats = cats_brutas
        for cat in iter_cats:
            if not isinstance(cat, dict):
                continue
            kid = str(cat.get("id") or cat.get("codigo") or cat.get("cod") or "").strip()
            knome = str(cat.get("nome") or cat.get("name") or "").strip()
            if kid:
                cats.append({"id": kid, "nome": knome})
        if cid and cats:
            saida.append({"id": cid, "nome": nome, "categorias": cats})
    return saida


def _escolher_variavel_sidra(meta: Dict[str, Any]) -> tuple[str, bool, str]:
    """Retorna (id_variavel, eh_percentual, descricao)."""
    variaveis = _meta_variaveis(meta)
    if not variaveis:
        return "all", False, "v/all"
    # Evita usar coeficiente de variação como indicador.
    candidatas = [v for v in variaveis if "coeficiente" not in _normalizar_texto_busca(v.get("nome", ""))]
    if not candidatas:
        candidatas = variaveis
    for var in candidatas:
        txt = _normalizar_texto_busca(f"{var.get('nome','')} {var.get('unidade','')}")
        if "%" in var.get("unidade", "") or "percent" in txt or "proporcao" in txt or "proporção" in txt:
            return var["id"], True, f"{var.get('nome','')} ({var.get('unidade','')})"
    # Preferência por pessoas/moradores; caso contrário usa a primeira variável não-CV.
    for var in candidatas:
        txt = _normalizar_texto_busca(var.get("nome", ""))
        if any(p in txt for p in ["pessoas", "moradores", "domicilios", "domicílios"]):
            return var["id"], False, f"{var.get('nome','')} ({var.get('unidade','')})"
    var = candidatas[0]
    return var["id"], False, f"{var.get('nome','')} ({var.get('unidade','')})"


def _categoria_total_id(categorias: List[Dict[str, str]]) -> Optional[str]:
    for cat in categorias:
        nome = _normalizar_texto_busca(cat.get("nome", ""))
        if nome in {"total", "total geral"} or nome.startswith("total"):
            return cat.get("id")
    return None


def _selecionar_categorias_por_palavras(meta: Dict[str, Any], palavras_any: List[str], palavras_all: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Procura categorias no metadado do SIDRA por palavras-chave normalizadas."""
    any_norm = [_normalizar_texto_busca(p) for p in palavras_any if p]
    all_norm = [_normalizar_texto_busca(p) for p in (palavras_all or []) if p]
    resultados = []
    for cla in _meta_classificacoes(meta):
        cats_match = []
        for cat in cla["categorias"]:
            nome_norm = _normalizar_texto_busca(cat.get("nome", ""))
            if "total" == nome_norm or nome_norm.startswith("total"):
                continue
            atende_any = any(p in nome_norm for p in any_norm) if any_norm else True
            atende_all = all(p in nome_norm for p in all_norm) if all_norm else True
            if atende_any and atende_all:
                cats_match.append(cat)
        if cats_match:
            resultados.append({"classificacao_id": cla["id"], "classificacao_nome": cla["nome"], "categorias": cats_match, "total_id": _categoria_total_id(cla["categorias"])})
    return resultados


def _consulta_sidra_categoria(tabela: int, ano: int, variavel: str, classificacao_id: str, categorias: List[str], timeout: int = 90) -> pd.DataFrame:
    cats = ",".join(str(c) for c in categorias if str(c).strip())
    if not cats:
        return pd.DataFrame()
    filtro_territorial = quote("in n3 51")
    url = f"https://apisidra.ibge.gov.br/values/t/{int(tabela)}/n6/{filtro_territorial}/v/{variavel}/p/{int(ano)}/c{classificacao_id}/{cats}?formato=json"
    dados = _request_json(url, timeout=timeout)
    if not dados or len(dados) < 2:
        return pd.DataFrame()
    linhas = []
    for item in dados[1:]:
        codigo = str(item.get("D1C", "")).strip()
        municipio = _limpar_nome_municipio(item.get("D1N", ""))
        valor = _parse_numero_sidra(item.get("V"))
        if codigo and municipio and valor is not None:
            linhas.append({"codigo_ibge": codigo, "municipio": municipio, "valor": float(valor), "descricao": _descricao_sidra_item(item)[:250]})
    return pd.DataFrame(linhas)


def _carregar_indicador_saneamento_sidra(tabela: int, ano: int, palavras_any: List[str], palavras_all: Optional[List[str]], nome_coluna: str) -> tuple[pd.DataFrame, str]:
    """
    Carrega um indicador de saneamento tentando duas estratégias:
    1) se houver variável percentual/proporcional, usa diretamente a soma das categorias selecionadas;
    2) se a tabela trouxer quantidade absoluta, divide categoria selecionada pelo total da mesma classificação.
    """
    meta = _sidra_meta_agregado(tabela)
    variavel, eh_percentual, desc_var = _escolher_variavel_sidra(meta)
    selecoes = _selecionar_categorias_por_palavras(meta, palavras_any=palavras_any, palavras_all=palavras_all)
    if not selecoes:
        raise ValueError(f"Nenhuma categoria compatível encontrada na tabela {tabela} para {nome_coluna}.")

    partes = []
    descricoes = []
    for sel in selecoes:
        class_id = sel["classificacao_id"]
        cat_ids = [c["id"] for c in sel["categorias"]]
        df_num = _consulta_sidra_categoria(tabela, ano, variavel, class_id, cat_ids)
        if df_num.empty:
            continue
        # Quando a variável já é percentual, a soma por município é aceitável para categorias complementares.
        if eh_percentual:
            df = df_num.groupby(["codigo_ibge", "municipio"], as_index=False)["valor"].sum()
            df[nome_coluna] = df["valor"].clip(lower=0, upper=100).round(2)
        else:
            total_id = sel.get("total_id")
            if not total_id:
                continue
            df_den = _consulta_sidra_categoria(tabela, ano, variavel, class_id, [total_id])
            if df_den.empty:
                continue
            num = df_num.groupby(["codigo_ibge", "municipio"], as_index=False)["valor"].sum().rename(columns={"valor": "numerador"})
            den = df_den.groupby(["codigo_ibge", "municipio"], as_index=False)["valor"].sum().rename(columns={"valor": "denominador"})
            df = num.merge(den, on=["codigo_ibge", "municipio"], how="left")
            df[nome_coluna] = (df["numerador"] / df["denominador"].replace({0: pd.NA}) * 100).astype(float).round(2)
        df = df[["codigo_ibge", "municipio", nome_coluna]]
        partes.append(df)
        descricoes.append(f"Tabela {tabela}; variável {desc_var}; classificação {sel.get('classificacao_nome')}; categorias: " + ", ".join(c.get("nome", "") for c in sel["categorias"]))

    if not partes:
        raise ValueError(f"Não foi possível calcular {nome_coluna} na tabela {tabela}.")

    combinado = pd.concat(partes, ignore_index=True)
    # Se a categoria aparecer em mais de uma classificação, fica com o maior percentual plausível.
    combinado = combinado.groupby(["codigo_ibge", "municipio"], as_index=False)[nome_coluna].max()
    combinado[nome_coluna] = combinado[nome_coluna].clip(lower=0, upper=100).round(2)
    return combinado, " | ".join(descricoes[:2])


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def carregar_saneamento_sidra_mt(ano_censo: int = 2022) -> pd.DataFrame:
    """
    Carrega indicadores preliminares de saneamento/condições domiciliares do Censo 2022.
    A rotina usa metadados do SIDRA para descobrir as classificações/categorias corretas antes
    de consultar as tabelas municipais, evitando retorno vazio quando a tabela exige c<id>/<categoria>.
    """
    erros: List[str] = []
    alertas: List[str] = []
    base = carregar_municipios_ibge_mt()[["codigo_ibge", "municipio"]].copy()

    indicadores = [
        (6909, ["rede geral"], None, "pct_agua_rede_geral_2022"),
        (6909, ["canalizacao interna", "canalização interna"], None, "pct_agua_canalizacao_interna_2022"),
        (9397, ["uso exclusivo"], ["banheiro"], "pct_banheiro_exclusivo_2022"),
        (9397, ["rede geral", "rede pluvial", "fossa septica", "fossa séptica", "fossa filtro"], None, "pct_esgoto_rede_geral_ou_fossa_2022"),
        (9541, ["coletado", "caçamba", "cacamba"], None, "pct_lixo_coletado_2022"),
    ]

    for tabela, any_words, all_words, coluna in indicadores:
        try:
            df_ind, descricao = _carregar_indicador_saneamento_sidra(
                tabela=tabela,
                ano=ano_censo,
                palavras_any=any_words,
                palavras_all=all_words,
                nome_coluna=coluna,
            )
            base = base.merge(df_ind[["codigo_ibge", coluna]], on="codigo_ibge", how="left")
            alertas.append(f"{coluna}: {descricao}")
        except Exception as exc:
            erros.append(f"{coluna}/SIDRA {tabela}: {exc}")
            base[coluna] = None

    def calcular_indice(row: pd.Series) -> Optional[float]:
        componentes = []
        for col in [
            "pct_agua_rede_geral_2022",
            "pct_agua_canalizacao_interna_2022",
            "pct_banheiro_exclusivo_2022",
            "pct_esgoto_rede_geral_ou_fossa_2022",
            "pct_lixo_coletado_2022",
        ]:
            v = _parse_numero_sidra(row.get(col))
            if v is not None:
                componentes.append(max(0.0, min(100.0, 100.0 - float(v))))
        if not componentes:
            return None
        return round(sum(componentes) / len(componentes), 2)

    base["indice_vulnerabilidade_saneamento_2022"] = base.apply(calcular_indice, axis=1)
    base["classificacao_vulnerabilidade_saneamento"] = base["indice_vulnerabilidade_saneamento_2022"].apply(classificar_vulnerabilidade_saneamento)
    base["ano_base_saneamento"] = int(ano_censo)
    base["fonte_saneamento"] = "IBGE/SIDRA - Tabelas 6909, 9397 e 9541 (Censo 2022), com leitura por metadados"
    if erros:
        base["alerta_saneamento"] = " | ".join(erros[:5])
    if alertas:
        base["metodo_saneamento"] = " || ".join(alertas[:5])
    return base.sort_values("municipio").reset_index(drop=True)

def carregar_regioes_saude_dadosabertos_mt() -> pd.DataFrame:
    """
    Carrega a base de Macrorregião e Região de Saúde publicada no Portal de Dados Abertos do SUS.

    Correção importante:
    algumas versões dessa base trazem código municipal com 6 dígitos, outras com 7 dígitos,
    e algumas não permitem filtro simples por UF. Por isso, a rotina agora cruza a base do SUS
    com a lista oficial de municípios de Mato Grosso carregada do IBGE.
    """
    url = "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/dbgeral/macroregiao_de_saude_csv.zip"
    resposta = requests.get(
        url,
        timeout=60,
        headers={"User-Agent": "SES-MT-Estudo-UBS/1.0", "Accept": "application/zip,*/*"},
    )
    resposta.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resposta.content)) as arquivo_zip:
        nomes_csv = [nome for nome in arquivo_zip.namelist() if nome.lower().endswith(".csv")]
        if not nomes_csv:
            raise ValueError("O ZIP de regiões de saúde não trouxe arquivo CSV.")
        nome_csv = nomes_csv[0]
        bruto = arquivo_zip.read(nome_csv)

    ultimo_erro: Optional[Exception] = None
    df_original: Optional[pd.DataFrame] = None
    for encoding in ["utf-8", "utf-8-sig", "latin1", "cp1252"]:
        try:
            df_original = pd.read_csv(io.BytesIO(bruto), sep=None, engine="python", dtype=str, encoding=encoding)
            break
        except Exception as exc:
            ultimo_erro = exc
            continue
    if df_original is None:
        raise ValueError(f"Não foi possível ler o CSV de regiões de saúde: {ultimo_erro}")

    def _chave_nome(valor: Any) -> str:
        texto = str(valor or "").strip().lower()
        mapa = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçñ", "aaaaaeeeeiiiiooooouuuucn")
        texto = texto.translate(mapa)
        texto = re.sub(r"[^a-z0-9]+", " ", texto)
        texto = re.sub(r"\s+", " ", texto).strip()
        return texto

    municipios_mt = carregar_municipios_ibge_mt()[["codigo_ibge", "municipio"]].copy()
    municipios_mt["codigo_ibge"] = municipios_mt["codigo_ibge"].astype(str).str.zfill(7)
    municipios_mt["codigo_ibge_6"] = municipios_mt["codigo_ibge"].str[:6]
    municipios_mt["municipio_chave"] = municipios_mt["municipio"].map(_chave_nome)

    colunas = list(df_original.columns)
    col_cod_mun = _identificar_coluna(
        colunas,
        [
            "codigo_ibge", "cod_municipio", "co_municipio", "cod_mun", "municipio_ibge",
            "co_municipio_ibge", "cod_ibge", "ibge", "codigo_municipio", "codigo_municipio_ibge",
            "co_mun", "co_ibge", "codmun", "cod_municipio_ibge", "codigo_do_municipio",
        ],
    )
    col_municipio = _identificar_coluna(
        colunas,
        ["municipio", "nome_municipio", "no_municipio", "município", "nome_do_municipio", "no_mun", "municipio_nome"],
    )
    col_uf = _identificar_coluna(colunas, ["uf", "sigla_uf", "sg_uf", "estado", "unidade_federativa", "no_uf"])
    col_regiao = _identificar_coluna(
        colunas,
        ["regiao_saude", "região_saúde", "nome_regiao_saude", "no_regiao_saude", "regiao_de_saude", "nome_regiao", "regiao"],
    )
    col_cod_regiao = _identificar_coluna(
        colunas,
        ["codigo_regiao_saude", "co_regiao_saude", "cod_regiao_saude", "co_regiao", "codigo_regiao"],
    )
    col_macro = _identificar_coluna(
        colunas,
        [
            "macrorregiao_saude", "macro_regiao_saude", "nome_macrorregiao", "no_macro_regiao_saude",
            "macrorregiao_de_saude", "macroregiao", "macrorregiao", "nome_macro",
        ],
    )
    col_cod_macro = _identificar_coluna(
        colunas,
        ["codigo_macrorregiao", "co_macro_regiao_saude", "cod_macro_regiao_saude", "co_macro", "codigo_macro"],
    )

    if not col_cod_mun and not col_municipio and not col_uf:
        raise ValueError(
            "Não localizei coluna de município/UF na base de regiões de saúde. "
            f"Colunas encontradas: {', '.join(map(str, colunas[:20]))}"
        )

    df = df_original.copy()
    df["codigo_ibge"] = None

    # 1) Caminho preferencial: cruzar por código IBGE de 7 dígitos ou prefixo de 6 dígitos.
    if col_cod_mun:
        df["codigo_extraido"] = df[col_cod_mun].astype(str).str.extract(r"(\d+)")[0]
        df["codigo_ibge_7_tentativa"] = df["codigo_extraido"].where(df["codigo_extraido"].str.len() == 7)
        df["codigo_ibge_6_tentativa"] = df["codigo_extraido"].str[:6]

        mapa_7 = dict(zip(municipios_mt["codigo_ibge"], municipios_mt["codigo_ibge"]))
        mapa_6 = dict(zip(municipios_mt["codigo_ibge_6"], municipios_mt["codigo_ibge"]))
        df["codigo_ibge"] = df["codigo_ibge_7_tentativa"].map(mapa_7)
        df["codigo_ibge"] = df["codigo_ibge"].fillna(df["codigo_ibge_6_tentativa"].map(mapa_6))

    # 2) Fallback: cruzar por nome do município com a lista oficial do IBGE/MT.
    if col_municipio:
        df["municipio_chave"] = df[col_municipio].map(_chave_nome)
        mapa_nome = dict(zip(municipios_mt["municipio_chave"], municipios_mt["codigo_ibge"]))
        df["codigo_ibge"] = df["codigo_ibge"].fillna(df["municipio_chave"].map(mapa_nome))

    # 3) Fallback adicional: se existir UF, manter somente MT/Mato Grosso antes de montar a saída.
    if col_uf:
        uf_texto = df[col_uf].astype(str).map(_chave_nome)
        mascara_uf_mt = uf_texto.isin(["mt", "mato grosso", "51"])
        # Só aplica filtro de UF quando ele realmente encontra algum registro de MT.
        if mascara_uf_mt.any():
            df = df[mascara_uf_mt]

    df = df[df["codigo_ibge"].astype(str).str.startswith(UF_MT, na=False)].copy()

    if df.empty:
        amostra_cols = ", ".join(map(str, colunas[:20]))
        raise ValueError(
            "A base de regiões de saúde foi lida, mas nenhum município de Mato Grosso foi identificado. "
            "Ajustei a rotina para tentar cruzar por código e por nome do município, mas a estrutura retornada pela fonte pode ter mudado. "
            f"Colunas encontradas: {amostra_cols}"
        )

    saida = pd.DataFrame()
    saida["codigo_ibge"] = df["codigo_ibge"].astype(str).str.zfill(7)
    saida["municipio_regiao_saude"] = df[col_municipio].astype(str).str.strip() if col_municipio else None
    saida["codigo_regiao_saude"] = df[col_cod_regiao].astype(str).str.strip() if col_cod_regiao else None
    saida["regiao_saude_sus"] = df[col_regiao].astype(str).str.strip() if col_regiao else None
    saida["codigo_macrorregiao_saude"] = df[col_cod_macro].astype(str).str.strip() if col_cod_macro else None
    saida["macrorregiao_saude_sus"] = df[col_macro].astype(str).str.strip() if col_macro else None
    saida["fonte_regiao_saude"] = "Dados Abertos SUS - Macrorregião e Região de Saúde"

    # Completa nome oficial do município quando a base do SUS não trouxer nome.
    saida = saida.merge(municipios_mt[["codigo_ibge", "municipio"]], on="codigo_ibge", how="left")
    saida["municipio_regiao_saude"] = saida["municipio_regiao_saude"].fillna(saida["municipio"])
    saida = saida.drop(columns=["municipio"], errors="ignore")

    saida = saida.drop_duplicates(subset=["codigo_ibge"]).sort_values(["regiao_saude_sus", "municipio_regiao_saude"], na_position="last")
    if saida.empty:
        raise ValueError("A base de regiões de saúde foi lida, mas a saída final ficou vazia.")
    return saida.reset_index(drop=True)



URL_CNES_ESTABELECIMENTOS_ZIP = "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/CNES/cnes_estabelecimentos_csv.zip"
URL_LEITOS_SUS_2026_ZIP = "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/Leitos_SUS/Leitos_csv_2026.zip"
SINASC_ANO_REFERENCIA = 2024
URL_SINASC_ZIP_TEMPLATE = "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/SINASC/csv/SINASC_{ano}_csv.zip"
SIM_ANO_REFERENCIA = 2024
URLS_SIM_CSV_TEMPLATE = [
    "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/SIM/DO{ano2}OPEN.csv",
    "https://diaad.s3.sa-east-1.amazonaws.com/sim/Mortalidade_Geral_{ano}.csv",
]


def _ler_csv_zip_url(url: str, timeout: int = 90) -> pd.DataFrame:
    """Baixa um ZIP com CSV e retorna DataFrame. Tenta separadores/encodings comuns."""
    resposta = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "SES-MT-Estudo-UBS/1.0", "Accept": "application/zip,*/*"},
    )
    resposta.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resposta.content)) as arquivo_zip:
        nomes_csv = [nome for nome in arquivo_zip.namelist() if nome.lower().endswith(".csv")]
        if not nomes_csv:
            raise ValueError("O ZIP baixado não contém arquivo CSV.")
        # Preferir arquivo maior, pois tende a ser a base principal.
        nome_csv = max(nomes_csv, key=lambda n: arquivo_zip.getinfo(n).file_size)
        bruto = arquivo_zip.read(nome_csv)

    ultimo_erro: Optional[Exception] = None
    for encoding in ["utf-8", "utf-8-sig", "latin1", "cp1252"]:
        for sep in [None, ";", ",", "|"]:
            try:
                return pd.read_csv(
                    io.BytesIO(bruto),
                    sep=sep,
                    engine="python",
                    dtype=str,
                    encoding=encoding,
                    on_bad_lines="skip",
                )
            except Exception as exc:
                ultimo_erro = exc
                continue
    raise ValueError(f"Não foi possível ler o CSV do ZIP: {ultimo_erro}")


def _coluna_por_nome_flexivel(df: pd.DataFrame, candidatos: List[str]) -> Optional[str]:
    return _identificar_coluna(list(df.columns), candidatos)


def _normalizar_texto_busca(valor: Any) -> str:
    texto = str(valor or "").strip().lower()
    mapa = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçñ", "aaaaaeeeeiiiiooooouuuucn")
    texto = texto.translate(mapa)
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _valor_coluna(df: pd.DataFrame, coluna: Optional[str], padrao: Any = None) -> pd.Series:
    """Retorna uma série alinhada ao índice do DataFrame, mesmo quando a coluna não existe."""
    if coluna and coluna in df.columns:
        return df[coluna].astype(str).str.strip()
    return pd.Series([padrao] * len(df), index=df.index)


def _classificar_tipo_unidade_cnes(tipo: Any, subtipo: Any, nome: Any) -> str:
    texto = _normalizar_texto_busca(f"{tipo} {subtipo} {nome}")
    if any(t in texto for t in ["unidade de saude da familia", "saude da familia", "estrategia saude da familia", " usf "]):
        return "USF / Saúde da Família"
    if any(t in texto for t in ["unidade basica", "ubs", "centro de saude"]):
        return "UBS / Centro de Saúde"
    if "posto de saude" in texto:
        return "Posto de Saúde"
    return "Unidade APS compatível"


def _classificar_atende_sus(valor: Any) -> str:
    texto = _normalizar_texto_busca(valor)
    if not texto or texto in ["nan", "none"]:
        return "Não informado"
    if texto in ["s", "sim", "1", "true", "verdadeiro"] or "sim" in texto:
        return "Sim"
    if texto in ["n", "nao", "0", "false", "falso"] or "nao" in texto:
        return "Não"
    return str(valor).strip()


def _classificar_natureza_gestao_cnes(natureza: Any, gestao: Any, esfera: Any) -> str:
    texto = _normalizar_texto_busca(f"{natureza} {gestao} {esfera}")
    if not texto or texto.replace("nan", "").strip() == "":
        return "Não informado"
    if "municip" in texto:
        return "Pública municipal"
    if "estad" in texto:
        return "Pública estadual"
    if "federal" in texto or "uniao" in texto:
        return "Pública federal"
    if any(t in texto for t in ["public", "administracao publica", "secretaria"]):
        return "Pública - não especificada"
    if any(t in texto for t in ["filantrop", "sem fins lucrativos", "benefic"]):
        return "Filantrópica/sem fins lucrativos"
    if any(t in texto for t in ["privad", "empresa", "sociedade", "com fins lucrativos"]):
        return "Privada"
    return "Outra/não classificada"





@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def carregar_cnes_estabelecimentos_ubs_mt() -> pd.DataFrame:
    """
    Carrega o arquivo público de estabelecimentos CNES no Portal de Dados Abertos do SUS,
    filtra Mato Grosso e identifica de forma preliminar unidades compatíveis com UBS.

    Atenção: é base cadastral, não valida funcionamento real. O resultado deve ser conferido pela SES/APS.
    """
    bruto = _ler_csv_zip_url(URL_CNES_ESTABELECIMENTOS_ZIP)
    if bruto.empty:
        raise ValueError("A base CNES foi lida, mas veio vazia.")

    col_cod_mun = _coluna_por_nome_flexivel(
        bruto,
        [
            "codigo_ibge", "cod_municipio", "co_municipio", "co_municipio_gestor", "cod_mun",
            "municipio_ibge", "co_ibge", "cod_ibge", "codigo_municipio", "codigo_municipio_ibge",
        ],
    )
    col_municipio = _coluna_por_nome_flexivel(
        bruto,
        ["municipio", "nome_municipio", "no_municipio", "no_municipio_gestor", "município", "nome_do_municipio"],
    )
    col_uf = _coluna_por_nome_flexivel(bruto, ["uf", "sigla_uf", "sg_uf", "estado", "co_uf", "codigo_uf"])
    col_cnes = _coluna_por_nome_flexivel(bruto, ["cnes", "co_cnes", "codigo_cnes", "cod_cnes"])
    col_nome = _coluna_por_nome_flexivel(
        bruto,
        ["nome_fantasia", "no_fantasia", "nome_unidade", "no_estabelecimento", "nome_estabelecimento", "estabelecimento"],
    )
    col_tipo = _coluna_por_nome_flexivel(
        bruto,
        [
            "tipo_estabelecimento", "ds_tipo_estabelecimento", "tipo_unidade", "ds_tipo_unidade",
            "descricao_tipo_unidade", "no_tipo_unidade", "tipo_de_estabelecimento",
        ],
    )
    col_subtipo = _coluna_por_nome_flexivel(
        bruto,
        ["subtipo", "subtipo_estabelecimento", "ds_subtipo_estabelecimento", "sub_tipo", "subtipo_unidade"],
    )
    col_situacao = _coluna_por_nome_flexivel(bruto, ["situacao", "st_ativo", "ativo", "situacao_cadastral", "ds_situacao"])
    col_atende_sus = _coluna_por_nome_flexivel(bruto, ["atende_sus", "st_atende_sus", "sus", "vinculo_sus", "atendimento_sus"])
    col_endereco = _coluna_por_nome_flexivel(bruto, ["endereco", "logradouro", "no_logradouro", "endereco_estabelecimento"])
    col_bairro = _coluna_por_nome_flexivel(bruto, ["bairro", "no_bairro", "bairro_estabelecimento"])
    col_cep = _coluna_por_nome_flexivel(bruto, ["cep", "nu_cep", "cep_estabelecimento"])
    col_telefone = _coluna_por_nome_flexivel(bruto, ["telefone", "nu_telefone", "telefone_estabelecimento", "tel", "fone"])
    col_natureza = _coluna_por_nome_flexivel(
        bruto,
        [
            "natureza_juridica", "ds_natureza_juridica", "no_natureza_juridica",
            "natureza_organizacao", "natureza", "tipo_natureza",
        ],
    )
    col_gestao = _coluna_por_nome_flexivel(bruto, ["gestao", "tipo_gestao", "tp_gestao", "ds_gestao", "gestor", "co_gestao"])
    col_esfera = _coluna_por_nome_flexivel(bruto, ["esfera_administrativa", "ds_esfera_administrativa", "esfera", "administracao"])

    if not col_cod_mun and not col_municipio and not col_uf:
        raise ValueError("Não localizei colunas de município/UF na base CNES para filtrar Mato Grosso.")
    if not col_tipo and not col_nome:
        raise ValueError("Não localizei coluna de tipo/nome do estabelecimento para identificar UBS.")

    df = bruto.copy()
    municipios_mt = carregar_municipios_ibge_mt()[["codigo_ibge", "municipio"]].copy()
    municipios_mt["codigo_ibge"] = municipios_mt["codigo_ibge"].astype(str).str.zfill(7)
    municipios_mt["codigo_ibge_6"] = municipios_mt["codigo_ibge"].str[:6]
    municipios_mt["municipio_chave"] = municipios_mt["municipio"].map(_normalizar_texto_busca)

    df["codigo_ibge"] = None

    if col_cod_mun:
        cod = df[col_cod_mun].astype(str).str.extract(r"(\d+)")[0]
        mapa_7 = dict(zip(municipios_mt["codigo_ibge"], municipios_mt["codigo_ibge"]))
        mapa_6 = dict(zip(municipios_mt["codigo_ibge_6"], municipios_mt["codigo_ibge"]))
        df["codigo_ibge"] = cod.where(cod.str.len() == 7).map(mapa_7)
        df["codigo_ibge"] = df["codigo_ibge"].fillna(cod.str[:6].map(mapa_6))

    if col_municipio:
        mapa_nome = dict(zip(municipios_mt["municipio_chave"], municipios_mt["codigo_ibge"]))
        df["municipio_chave"] = df[col_municipio].map(_normalizar_texto_busca)
        df["codigo_ibge"] = df["codigo_ibge"].fillna(df["municipio_chave"].map(mapa_nome))

    if col_uf:
        uf = df[col_uf].astype(str).map(_normalizar_texto_busca)
        mascara_uf = uf.isin(["mt", "mato grosso", "51"])
        if mascara_uf.any():
            df = df[mascara_uf].copy()

    df = df[df["codigo_ibge"].astype(str).str.startswith(UF_MT, na=False)].copy()
    if df.empty:
        raise ValueError("A base CNES foi lida, mas nenhum estabelecimento de Mato Grosso foi identificado.")

    texto_tipo = df[col_tipo].map(_normalizar_texto_busca) if col_tipo else pd.Series("", index=df.index)
    texto_subtipo = df[col_subtipo].map(_normalizar_texto_busca) if col_subtipo else pd.Series("", index=df.index)
    texto_nome = df[col_nome].map(_normalizar_texto_busca) if col_nome else pd.Series("", index=df.index)
    texto_busca = (texto_tipo + " " + texto_subtipo + " " + texto_nome).str.strip()

    termos_ubs = [
        "unidade basica", "centro de saude", "posto de saude", "saude da familia",
        "estrategia saude da familia", "unidade de saude da familia", "ubs", "usf",
    ]
    padrao = "|".join(re.escape(t) for t in termos_ubs)
    df = df[texto_busca.str.contains(padrao, na=False)].copy()
    if df.empty:
        raise ValueError("Nenhuma unidade compatível com UBS/USF foi identificada na base CNES para MT.")

    # Filtro leve de ativos, quando a coluna permitir. Mantém registros quando a fonte não tiver situação clara.
    if col_situacao:
        situacao = df[col_situacao].map(_normalizar_texto_busca)
        mascara_inativa = situacao.str.contains("inativ|desativ|baixad|suspens|encerrad", na=False)
        df = df[~mascara_inativa].copy()

    saida = pd.DataFrame()
    saida["codigo_ibge"] = df["codigo_ibge"].astype(str).str.zfill(7)
    saida = saida.merge(municipios_mt[["codigo_ibge", "municipio"]], on="codigo_ibge", how="left")
    saida["cnes"] = _valor_coluna(df, col_cnes).values if col_cnes else None
    saida["nome_unidade_cnes"] = _valor_coluna(df, col_nome).values if col_nome else None
    saida["tipo_unidade_cnes"] = _valor_coluna(df, col_tipo).values if col_tipo else None
    saida["subtipo_unidade_cnes"] = _valor_coluna(df, col_subtipo).values if col_subtipo else None
    saida["categoria_preliminar_cnes"] = [
        _classificar_tipo_unidade_cnes(t, s, n)
        for t, s, n in zip(
            _valor_coluna(df, col_tipo),
            _valor_coluna(df, col_subtipo),
            _valor_coluna(df, col_nome),
        )
    ]
    saida["situacao_cnes"] = _valor_coluna(df, col_situacao).values if col_situacao else None
    saida["atende_sus_cnes"] = _valor_coluna(df, col_atende_sus).values if col_atende_sus else None
    saida["atende_sus_preliminar"] = saida["atende_sus_cnes"].map(_classificar_atende_sus)
    saida["natureza_juridica_cnes"] = _valor_coluna(df, col_natureza).values if col_natureza else None
    saida["gestao_cnes"] = _valor_coluna(df, col_gestao).values if col_gestao else None
    saida["esfera_administrativa_cnes"] = _valor_coluna(df, col_esfera).values if col_esfera else None
    saida["natureza_gestao_preliminar"] = [
        _classificar_natureza_gestao_cnes(n, g, e)
        for n, g, e in zip(
            saida["natureza_juridica_cnes"],
            saida["gestao_cnes"],
            saida["esfera_administrativa_cnes"],
        )
    ]
    saida["endereco_cnes"] = _valor_coluna(df, col_endereco).values if col_endereco else None
    saida["bairro_cnes"] = _valor_coluna(df, col_bairro).values if col_bairro else None
    saida["cep_cnes"] = _valor_coluna(df, col_cep).values if col_cep else None
    saida["telefone_cnes"] = _valor_coluna(df, col_telefone).values if col_telefone else None
    saida["fonte_cnes"] = "Dados Abertos SUS - CNES Estabelecimentos"

    # Se não houver CNES, deduplica por município/nome; se houver, pelo código CNES.
    subset = ["cnes"] if col_cnes else ["codigo_ibge", "nome_unidade_cnes"]
    saida = saida.drop_duplicates(subset=subset).sort_values(["municipio", "nome_unidade_cnes"], na_position="last")
    return saida.reset_index(drop=True)


def consolidar_cnes_ubs_por_municipio(df_cnes: pd.DataFrame) -> pd.DataFrame:
    if df_cnes is None or df_cnes.empty:
        return pd.DataFrame(columns=["codigo_ibge", "qtd_ubs_cnes_automatico", "ubs_cnes_lista", "fonte_cnes"])

    df = df_cnes.copy()
    df["cnes_limpo"] = df.get("cnes", pd.Series(index=df.index, dtype=str)).astype(str).str.strip().replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
    df["indicador_unidade"] = df["cnes_limpo"].fillna(df.get("nome_unidade_cnes", pd.Series(index=df.index, dtype=str)).astype(str))

    def _contar_unidades(grupo: pd.Series) -> int:
        valores = grupo.astype(str).str.strip()
        valores = valores[~valores.str.lower().isin(["", "nan", "none", "<na>"])]
        return int(valores.nunique()) if not valores.empty else 0

    agrupado = (
        df.groupby("codigo_ibge")
        .agg(
            qtd_ubs_cnes_automatico=("indicador_unidade", _contar_unidades),
            qtd_ubs_cnes_atende_sus=("atende_sus_preliminar", lambda x: int((x == "Sim").sum()) if "atende_sus_preliminar" in df.columns else 0),
            qtd_ubs_cnes_publicas=("natureza_gestao_preliminar", lambda x: int(x.astype(str).str.contains("Pública", na=False).sum()) if "natureza_gestao_preliminar" in df.columns else 0),
            tipos_unidades_cnes=("categoria_preliminar_cnes", lambda x: "; ".join(sorted({str(v).strip() for v in x if str(v).strip() and str(v).strip().lower() != "nan"}))[:800]),
            natureza_unidades_cnes=("natureza_gestao_preliminar", lambda x: "; ".join(sorted({str(v).strip() for v in x if str(v).strip() and str(v).strip().lower() != "nan"}))[:800]),
            ubs_cnes_lista=("nome_unidade_cnes", lambda x: "; ".join(sorted({str(v).strip() for v in x if str(v).strip() and str(v).strip().lower() != "nan"}))[:3000]),
            fonte_cnes=("fonte_cnes", "first"),
        )
        .reset_index()
    )
    return agrupado



def _serie_numerica_flexivel(serie: pd.Series) -> pd.Series:
    """Converte números vindos como texto, aceitando vírgula decimal e caracteres extras.

    Observação importante: bases salvas em CSV/cache podem voltar como object/string.
    Esta função evita que o pandas concatene textos ao fazer soma/média.
    """
    if serie is None:
        return pd.Series(dtype=float)
    if not isinstance(serie, pd.Series):
        serie = pd.Series(serie)
    if pd.api.types.is_numeric_dtype(serie):
        return pd.to_numeric(serie, errors="coerce").fillna(0)

    texto = serie.astype(str).str.strip()
    texto = texto.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "<NA>": pd.NA, "NaN": pd.NA})
    texto = texto.str.replace(r"[^0-9,.-]", "", regex=True)
    texto = texto.replace({"": pd.NA, ".": pd.NA, "-": pd.NA, ",": pd.NA})

    def _converter_valor_numero(valor: Any) -> Any:
        if not isinstance(valor, str) or not valor:
            return valor
        # Quando houver vírgula, assume vírgula como decimal e ponto como milhar.
        if "," in valor:
            return valor.replace(".", "").replace(",", ".")
        return valor

    texto = texto.apply(_converter_valor_numero)
    return pd.to_numeric(texto, errors="coerce").fillna(0)


def _codigo_ibge_texto_7(serie: pd.Series) -> pd.Series:
    """Padroniza código IBGE municipal como texto de 7 dígitos para merges seguros."""
    if serie is None:
        return pd.Series(dtype=str)
    if not isinstance(serie, pd.Series):
        serie = pd.Series(serie)
    saida = serie.astype(str).str.strip()
    saida = saida.str.replace(r"\.0$", "", regex=True)
    saida = saida.str.extract(r"(\d+)")[0]
    return saida.fillna("").str.zfill(7)


def _media_numerica_coluna(df: pd.DataFrame, coluna: str, somente_positivos: bool = False) -> Optional[float]:
    """Calcula média de coluna que pode ter voltado do cache como texto."""
    if df is None or coluna not in df.columns:
        return None
    serie = _serie_numerica_flexivel(df[coluna])
    if somente_positivos:
        serie = serie[serie > 0]
    else:
        serie = serie.dropna()
    if serie.empty:
        return None
    return float(serie.mean())


def _soma_numerica_coluna(df: pd.DataFrame, coluna: str) -> float:
    """Soma segura de coluna que pode ter voltado do cache como texto."""
    if df is None or coluna not in df.columns:
        return 0.0
    return float(_serie_numerica_flexivel(df[coluna]).sum())


def _classificar_contexto_leitos(qtd_hospitais: Any, leitos_sus: Any, leitos_total: Any) -> str:
    try:
        hospitais = int(float(qtd_hospitais or 0))
    except Exception:
        hospitais = 0
    try:
        sus = float(leitos_sus or 0)
    except Exception:
        sus = 0
    try:
        total = float(leitos_total or 0)
    except Exception:
        total = 0

    if hospitais == 0 and sus == 0 and total == 0:
        return "Sem leitos/hospitais identificados automaticamente"
    if hospitais == 0:
        return "Leitos sem hospital identificado — validar base"
    if sus <= 0 and total > 0:
        return "Hospital/leito sem leito SUS identificado"
    if sus <= 20:
        return "Baixa retaguarda hospitalar SUS preliminar"
    if sus <= 80:
        return "Retaguarda hospitalar SUS intermediária"
    return "Maior retaguarda hospitalar SUS"


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def carregar_leitos_sus_mt() -> pd.DataFrame:
    """
    Carrega a base pública de Hospitais e Leitos do SUS, filtra Mato Grosso e retorna detalhamento preliminar.

    O arquivo de leitos pode mudar a nomenclatura das colunas. Por isso a rotina é flexível e procura colunas por nomes prováveis.
    Esse dado é usado apenas como contexto assistencial complementar, não como critério direto de construção de UBS.
    """
    bruto = _ler_csv_zip_url(URL_LEITOS_SUS_2026_ZIP, timeout=120)
    if bruto.empty:
        raise ValueError("A base de Hospitais e Leitos foi lida, mas veio vazia.")

    col_cod_mun = _coluna_por_nome_flexivel(
        bruto,
        [
            "codigo_ibge", "cod_municipio", "co_municipio", "co_municipio_gestor", "cod_mun",
            "municipio_ibge", "co_ibge", "cod_ibge", "codigo_municipio", "codigo_municipio_ibge",
            "codufmun", "cod_ufmun", "co_ufmun", "ibge", "ibge_municipio",
        ],
    )
    col_municipio = _coluna_por_nome_flexivel(
        bruto,
        ["municipio", "nome_municipio", "no_municipio", "no_municipio_gestor", "município", "nome_do_municipio"],
    )
    col_uf = _coluna_por_nome_flexivel(bruto, ["uf", "sigla_uf", "sg_uf", "estado", "co_uf", "codigo_uf"])
    col_cnes = _coluna_por_nome_flexivel(bruto, ["cnes", "co_cnes", "codigo_cnes", "cod_cnes"])
    col_nome = _coluna_por_nome_flexivel(
        bruto,
        ["nome_fantasia", "no_fantasia", "nome_unidade", "no_estabelecimento", "nome_estabelecimento", "estabelecimento", "hospital"],
    )
    col_tipo_unidade = _coluna_por_nome_flexivel(
        bruto,
        ["tipo_estabelecimento", "ds_tipo_estabelecimento", "tipo_unidade", "ds_tipo_unidade", "descricao_tipo_unidade"],
    )
    col_tipo_leito = _coluna_por_nome_flexivel(
        bruto,
        ["tipo_leito", "ds_tipo_leito", "leito", "descricao_leito", "especialidade", "ds_especialidade", "leito_especialidade"],
    )
    col_qtd_existente = _coluna_por_nome_flexivel(
        bruto,
        [
            "qt_existente", "qtd_existente", "quantidade_existente", "leitos_existentes", "qt_leito_existente",
            "qt_leitos_existentes", "existente", "qt_total", "qtd_total", "quantidade", "qt_leitos",
        ],
    )
    col_qtd_sus = _coluna_por_nome_flexivel(
        bruto,
        [
            "qt_sus", "qtd_sus", "quantidade_sus", "leitos_sus", "qt_leito_sus", "qt_leitos_sus",
            "sus", "qtd_leitos_sus", "qt_existente_sus",
        ],
    )
    col_endereco = _coluna_por_nome_flexivel(bruto, ["endereco", "logradouro", "no_logradouro", "endereco_estabelecimento"])
    col_bairro = _coluna_por_nome_flexivel(bruto, ["bairro", "no_bairro", "bairro_estabelecimento"])
    col_telefone = _coluna_por_nome_flexivel(bruto, ["telefone", "nu_telefone", "telefone_estabelecimento", "tel", "fone"])

    if not col_cod_mun and not col_municipio and not col_uf:
        amostra = ", ".join(map(str, bruto.columns[:25]))
        raise ValueError(f"Não localizei colunas de município/UF na base de leitos. Colunas encontradas: {amostra}")

    df = bruto.copy()
    municipios_mt = carregar_municipios_ibge_mt()[["codigo_ibge", "municipio"]].copy()
    municipios_mt["codigo_ibge"] = municipios_mt["codigo_ibge"].astype(str).str.zfill(7)
    municipios_mt["codigo_ibge_6"] = municipios_mt["codigo_ibge"].str[:6]
    municipios_mt["municipio_chave"] = municipios_mt["municipio"].map(_normalizar_texto_busca)

    df["codigo_ibge"] = None
    if col_cod_mun:
        cod = df[col_cod_mun].astype(str).str.extract(r"(\d+)")[0]
        mapa_7 = dict(zip(municipios_mt["codigo_ibge"], municipios_mt["codigo_ibge"]))
        mapa_6 = dict(zip(municipios_mt["codigo_ibge_6"], municipios_mt["codigo_ibge"]))
        df["codigo_ibge"] = cod.where(cod.str.len() == 7).map(mapa_7)
        df["codigo_ibge"] = df["codigo_ibge"].fillna(cod.str[:6].map(mapa_6))

    if df["codigo_ibge"].isna().all() and col_municipio:
        mapa_nome = dict(zip(municipios_mt["municipio_chave"], municipios_mt["codigo_ibge"]))
        df["codigo_ibge"] = df[col_municipio].map(_normalizar_texto_busca).map(mapa_nome)

    if col_uf and df["codigo_ibge"].isna().any():
        uf = df[col_uf].astype(str).str.upper().str.strip()
        mascara_mt = uf.isin(["MT", "51", "MATO GROSSO"])
        if col_municipio:
            mapa_nome = dict(zip(municipios_mt["municipio_chave"], municipios_mt["codigo_ibge"]))
            df.loc[mascara_mt & df["codigo_ibge"].isna(), "codigo_ibge"] = df.loc[mascara_mt & df["codigo_ibge"].isna(), col_municipio].map(_normalizar_texto_busca).map(mapa_nome)

    df = df[df["codigo_ibge"].notna()].copy()
    if df.empty:
        amostra = ", ".join(map(str, bruto.columns[:25]))
        raise ValueError(
            "A base de Hospitais e Leitos foi lida, mas nenhum registro de Mato Grosso foi identificado. "
            f"Colunas encontradas: {amostra}"
        )

    df = df.merge(municipios_mt[["codigo_ibge", "municipio"]], on="codigo_ibge", how="left")

    saida = pd.DataFrame(index=df.index)
    saida["codigo_ibge"] = df["codigo_ibge"].astype(str).str.zfill(7)
    saida["municipio"] = df["municipio"].fillna(_valor_coluna(df, col_municipio))
    saida["cnes"] = _valor_coluna(df, col_cnes) if col_cnes else None
    saida["nome_estabelecimento_leitos"] = _valor_coluna(df, col_nome) if col_nome else None
    saida["tipo_unidade_leitos"] = _valor_coluna(df, col_tipo_unidade) if col_tipo_unidade else None
    saida["tipo_leito"] = _valor_coluna(df, col_tipo_leito) if col_tipo_leito else None
    saida["leitos_existentes"] = _serie_numerica_flexivel(df[col_qtd_existente]) if col_qtd_existente else 0
    saida["leitos_sus"] = _serie_numerica_flexivel(df[col_qtd_sus]) if col_qtd_sus else 0
    saida["endereco_leitos"] = _valor_coluna(df, col_endereco) if col_endereco else None
    saida["bairro_leitos"] = _valor_coluna(df, col_bairro) if col_bairro else None
    saida["telefone_leitos"] = _valor_coluna(df, col_telefone) if col_telefone else None
    saida["fonte_leitos"] = "Dados Abertos SUS - Hospitais e Leitos 2026"

    # Remove registros totalmente sem estabelecimento e sem leitos; mantém apenas linhas úteis.
    texto_estab = saida[[c for c in ["cnes", "nome_estabelecimento_leitos"] if c in saida.columns]].astype(str).agg(" ".join, axis=1).map(_normalizar_texto_busca)
    saida = saida[(texto_estab != "") | (saida["leitos_existentes"].fillna(0) > 0) | (saida["leitos_sus"].fillna(0) > 0)].copy()
    if saida.empty:
        raise ValueError("A base de Hospitais e Leitos foi lida, mas não restaram registros úteis após o tratamento.")

    subset = [col for col in ["codigo_ibge", "cnes", "nome_estabelecimento_leitos", "tipo_leito"] if col in saida.columns]
    return saida.drop_duplicates(subset=subset).sort_values(["municipio", "nome_estabelecimento_leitos"], na_position="last").reset_index(drop=True)


def consolidar_leitos_por_municipio(df_leitos: pd.DataFrame) -> pd.DataFrame:
    if df_leitos is None or df_leitos.empty:
        return pd.DataFrame(columns=["codigo_ibge", "qtd_hospitais_leitos", "leitos_existentes_total", "leitos_sus_total"])

    df = df_leitos.copy()
    df["identificador_estab"] = df.get("cnes", pd.Series(index=df.index, dtype=str)).astype(str).str.strip()
    nome = df.get("nome_estabelecimento_leitos", pd.Series(index=df.index, dtype=str)).astype(str).str.strip()
    df["identificador_estab"] = df["identificador_estab"].replace({"": pd.NA, "nan": pd.NA, "None": pd.NA}).fillna(nome)

    def _nunique_util(serie: pd.Series) -> int:
        valores = serie.astype(str).str.strip()
        valores = valores[~valores.str.lower().isin(["", "nan", "none", "<na>"])]
        return int(valores.nunique()) if not valores.empty else 0

    agrupado = (
        df.groupby("codigo_ibge", dropna=False)
        .agg(
            qtd_hospitais_leitos=("identificador_estab", _nunique_util),
            leitos_existentes_total=("leitos_existentes", "sum"),
            leitos_sus_total=("leitos_sus", "sum"),
            hospitais_leitos_lista=("nome_estabelecimento_leitos", lambda x: "; ".join(sorted({str(v).strip() for v in x if str(v).strip() and str(v).strip().lower() != "nan"}))[:3000]),
            tipos_leitos_lista=("tipo_leito", lambda x: "; ".join(sorted({str(v).strip() for v in x if str(v).strip() and str(v).strip().lower() != "nan"}))[:1500]),
            fonte_leitos=("fonte_leitos", "first"),
        )
        .reset_index()
    )
    agrupado["classificacao_contexto_leitos"] = [
        _classificar_contexto_leitos(h, s, t)
        for h, s, t in zip(
            agrupado["qtd_hospitais_leitos"],
            agrupado["leitos_sus_total"],
            agrupado["leitos_existentes_total"],
        )
    ]
    return agrupado


def preparar_leitos_detalhado_para_exibicao(df_leitos: pd.DataFrame, base: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    if df_leitos is None or df_leitos.empty:
        return pd.DataFrame()
    df = df_leitos.copy()
    if isinstance(base, pd.DataFrame) and not base.empty:
        colunas_base = [col for col in ["codigo_ibge", "regiao_saude_sus", "macrorregiao_saude_sus"] if col in base.columns]
        if "codigo_ibge" in colunas_base:
            df = df.merge(base[colunas_base].drop_duplicates("codigo_ibge"), on="codigo_ibge", how="left")
    ordem = [
        "codigo_ibge", "municipio", "regiao_saude_sus", "macrorregiao_saude_sus", "cnes",
        "nome_estabelecimento_leitos", "tipo_unidade_leitos", "tipo_leito", "leitos_existentes", "leitos_sus",
        "endereco_leitos", "bairro_leitos", "telefone_leitos", "fonte_leitos",
    ]
    colunas = [col for col in ordem if col in df.columns]
    demais = [col for col in df.columns if col not in colunas]
    return df[colunas + demais].sort_values(["municipio", "nome_estabelecimento_leitos"], na_position="last").reset_index(drop=True)


def testar_leitos_sus_dadosabertos() -> Dict[str, Any]:
    status = _request_status(URL_LEITOS_SUS_2026_ZIP, timeout=60)
    status["url_testada"] = URL_LEITOS_SUS_2026_ZIP
    status["observacao"] = "ZIP público de Hospitais e Leitos 2026 publicado no Portal de Dados Abertos do SUS."
    return status



def _classificar_pressao_nascimentos(taxa: Any, nascidos: Any) -> str:
    try:
        taxa_val = float(taxa or 0)
    except Exception:
        taxa_val = 0
    try:
        nascidos_val = int(float(nascidos or 0))
    except Exception:
        nascidos_val = 0

    if nascidos_val <= 0:
        return "Sem nascidos vivos identificados no SINASC"
    if nascidos_val >= 1500 or taxa_val >= 18:
        return "Alta pressão materno-infantil preliminar"
    if nascidos_val >= 500 or taxa_val >= 12:
        return "Pressão materno-infantil intermediária"
    return "Baixa pressão materno-infantil preliminar"


def _extrair_zip_csv_sinasc(url: str, timeout: int = 180) -> tuple[bytes, str]:
    resposta = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "SES-MT-Estudo-UBS/1.0", "Accept": "application/zip,*/*"},
    )
    resposta.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resposta.content)) as arquivo_zip:
        nomes_csv = [nome for nome in arquivo_zip.namelist() if nome.lower().endswith(".csv")]
        if not nomes_csv:
            raise ValueError("O ZIP do SINASC baixado não contém arquivo CSV.")
        nome_csv = max(nomes_csv, key=lambda n: arquivo_zip.getinfo(n).file_size)
        return arquivo_zip.read(nome_csv), nome_csv


def _identificar_coluna_sinasc(colunas: List[str], candidatos: List[str]) -> Optional[str]:
    mapa = {_normalizar_texto_busca(col): col for col in colunas}
    for candidato in candidatos:
        chave = _normalizar_texto_busca(candidato)
        if chave in mapa:
            return mapa[chave]
    for col in colunas:
        col_norm = _normalizar_texto_busca(col)
        for candidato in candidatos:
            cand_norm = _normalizar_texto_busca(candidato)
            if cand_norm and cand_norm in col_norm:
                return col
    return None


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def carregar_sinasc_nascidos_vivos_mt(ano: int = SINASC_ANO_REFERENCIA) -> pd.DataFrame:
    """
    Carrega a base pública SINASC em CSV/ZIP e consolida os nascidos vivos por município de residência em MT.
    A rotina é flexível porque o layout do arquivo pode variar entre anos.
    """
    url = URL_SINASC_ZIP_TEMPLATE.format(ano=int(ano))
    bruto, nome_csv = _extrair_zip_csv_sinasc(url)

    municipios_mt = carregar_municipios_ibge_mt()[["codigo_ibge", "municipio"]].copy()
    municipios_mt["codigo_ibge"] = municipios_mt["codigo_ibge"].astype(str).str.zfill(7)
    municipios_mt["codigo_ibge_6"] = municipios_mt["codigo_ibge"].str[:6]
    mapa_6_para_7 = dict(zip(municipios_mt["codigo_ibge_6"], municipios_mt["codigo_ibge"]))
    mapa_nome = dict(zip(municipios_mt["codigo_ibge"], municipios_mt["municipio"]))

    agregados: Dict[str, Dict[str, float]] = {}
    ultimo_erro: Optional[Exception] = None
    sucesso = False

    # SINASC costuma vir separado por ponto e vírgula e em latin1, mas mantemos tentativas.
    for encoding in ["latin1", "utf-8", "utf-8-sig", "cp1252"]:
        for sep in [";", ",", "|"]:
            try:
                leitor = pd.read_csv(
                    io.BytesIO(bruto),
                    sep=sep,
                    dtype=str,
                    encoding=encoding,
                    chunksize=200_000,
                    on_bad_lines="skip",
                    low_memory=False,
                )
                primeiro_chunk = True
                col_cod_mun = col_consultas = col_peso = col_semana = col_idade_mae = None
                parcial: Dict[str, Dict[str, float]] = {}

                for chunk in leitor:
                    if primeiro_chunk:
                        if len(chunk.columns) <= 1 and sep != ";":
                            raise ValueError("Separador possivelmente incorreto para SINASC.")
                        col_cod_mun = _identificar_coluna_sinasc(
                            list(chunk.columns),
                            [
                                "CODMUNRES", "COD_MUN_RES", "CO_MUN_RES", "codigo_municipio_residencia",
                                "mun_residencia", "municipio_residencia", "codmunres",
                            ],
                        )
                        col_consultas = _identificar_coluna_sinasc(list(chunk.columns), ["CONSULTAS", "consultas_pre_natal", "qtd_consultas", "qt_consultas"])
                        col_peso = _identificar_coluna_sinasc(list(chunk.columns), ["PESO", "peso_nascimento", "peso_ao_nascer"])
                        col_semana = _identificar_coluna_sinasc(list(chunk.columns), ["SEMAGESTAC", "semanas_gestacao", "idade_gestacional"])
                        col_idade_mae = _identificar_coluna_sinasc(list(chunk.columns), ["IDADEMAE", "idade_mae"])
                        if not col_cod_mun:
                            raise ValueError(f"Não localizei coluna de município de residência no SINASC. Colunas: {', '.join(map(str, chunk.columns[:30]))}")
                        primeiro_chunk = False

                    cod = chunk[col_cod_mun].astype(str).str.extract(r"(\d+)")[0]
                    codigo_ibge = cod.str[:6].map(mapa_6_para_7)
                    mt = chunk[codigo_ibge.notna()].copy()
                    mt["codigo_ibge"] = codigo_ibge[codigo_ibge.notna()].values
                    if mt.empty:
                        continue

                    mt["_n"] = 1
                    mt["_consultas_7mais"] = 0
                    if col_consultas and col_consultas in mt.columns:
                        consultas = mt[col_consultas].astype(str).str.extract(r"(\d+)")[0]
                        # No SINASC, a categoria 4 costuma representar 7 ou mais consultas.
                        mt["_consultas_7mais"] = (consultas == "4").astype(int)

                    mt["_baixo_peso"] = 0
                    if col_peso and col_peso in mt.columns:
                        peso = _serie_numerica_flexivel(mt[col_peso])
                        mt["_baixo_peso"] = ((peso > 0) & (peso < 2500)).astype(int)

                    mt["_prematuro"] = 0
                    if col_semana and col_semana in mt.columns:
                        semanas = _serie_numerica_flexivel(mt[col_semana])
                        mt["_prematuro"] = ((semanas > 0) & (semanas < 37)).astype(int)

                    mt["_idade_mae_soma"] = 0.0
                    mt["_idade_mae_count"] = 0
                    if col_idade_mae and col_idade_mae in mt.columns:
                        idade = _serie_numerica_flexivel(mt[col_idade_mae])
                        valida = (idade > 10) & (idade < 60)
                        mt["_idade_mae_soma"] = idade.where(valida, 0)
                        mt["_idade_mae_count"] = valida.astype(int)

                    grp = mt.groupby("codigo_ibge", dropna=False).agg(
                        nascidos_vivos=("_n", "sum"),
                        consultas_7mais=("_consultas_7mais", "sum"),
                        baixo_peso=("_baixo_peso", "sum"),
                        prematuros=("_prematuro", "sum"),
                        idade_mae_soma=("_idade_mae_soma", "sum"),
                        idade_mae_count=("_idade_mae_count", "sum"),
                    )
                    for codigo, linha in grp.iterrows():
                        d = parcial.setdefault(str(codigo), {
                            "nascidos_vivos": 0,
                            "consultas_7mais": 0,
                            "baixo_peso": 0,
                            "prematuros": 0,
                            "idade_mae_soma": 0.0,
                            "idade_mae_count": 0,
                        })
                        for campo in d.keys():
                            d[campo] += float(linha.get(campo, 0) or 0)

                agregados = parcial
                sucesso = True
                break
            except Exception as exc:
                ultimo_erro = exc
                continue
        if sucesso:
            break

    if not sucesso:
        raise ValueError(f"Não foi possível ler/consolidar o SINASC: {ultimo_erro}")
    if not agregados:
        raise ValueError("A base SINASC foi lida, mas nenhum nascido vivo de Mato Grosso foi identificado.")

    registros = []
    for codigo, valores in agregados.items():
        n = int(valores.get("nascidos_vivos", 0) or 0)
        idade_count = int(valores.get("idade_mae_count", 0) or 0)
        registros.append({
            "codigo_ibge": str(codigo).zfill(7),
            "municipio": mapa_nome.get(str(codigo).zfill(7)),
            f"nascidos_vivos_sinasc_{ano}": n,
            f"pre_natal_7mais_sinasc_{ano}": int(valores.get("consultas_7mais", 0) or 0),
            f"pct_pre_natal_7mais_sinasc_{ano}": round((valores.get("consultas_7mais", 0) / n) * 100, 2) if n else None,
            f"baixo_peso_sinasc_{ano}": int(valores.get("baixo_peso", 0) or 0),
            f"pct_baixo_peso_sinasc_{ano}": round((valores.get("baixo_peso", 0) / n) * 100, 2) if n else None,
            f"prematuros_sinasc_{ano}": int(valores.get("prematuros", 0) or 0),
            f"pct_prematuridade_sinasc_{ano}": round((valores.get("prematuros", 0) / n) * 100, 2) if n else None,
            f"idade_media_mae_sinasc_{ano}": round(valores.get("idade_mae_soma", 0) / idade_count, 1) if idade_count else None,
            "ano_base_sinasc": int(ano),
            "arquivo_sinasc": nome_csv,
            "fonte_sinasc": f"Dados Abertos SUS - SINASC {ano}",
        })
    saida = pd.DataFrame(registros).sort_values("municipio", na_position="last").reset_index(drop=True)
    return saida


def testar_sinasc_dadosabertos(ano: int = SINASC_ANO_REFERENCIA) -> Dict[str, Any]:
    url = URL_SINASC_ZIP_TEMPLATE.format(ano=int(ano))
    status = _request_status(url, timeout=60)
    status["url_testada"] = url
    status["observacao"] = f"ZIP público do SINASC {ano} publicado no Portal de Dados Abertos do SUS."
    return status



def _urls_sim_ano(ano: int = SIM_ANO_REFERENCIA) -> List[str]:
    ano_int = int(ano)
    ano2 = str(ano_int)[-2:]
    return [url.format(ano=ano_int, ano2=ano2) for url in URLS_SIM_CSV_TEMPLATE]


def _testar_url_csv_leve(url: str, timeout: int = 45) -> Dict[str, Any]:
    """Testa um CSV remoto sem baixar o arquivo inteiro, quando o servidor aceita Range."""
    headers = {
        "User-Agent": "SES-MT-Estudo-UBS/1.0",
        "Accept": "text/csv,text/plain,*/*",
        "Range": "bytes=0-2048",
    }
    resposta = requests.get(url, timeout=timeout, headers=headers, stream=True)
    tamanho_header = resposta.headers.get("content-length") or resposta.headers.get("Content-Length")
    content_range = resposta.headers.get("content-range") or resposta.headers.get("Content-Range")
    tamanho_total = None
    if content_range and "/" in content_range:
        try:
            tamanho_total = int(str(content_range).split("/")[-1])
        except Exception:
            tamanho_total = None
    elif tamanho_header:
        try:
            tamanho_total = int(tamanho_header)
        except Exception:
            tamanho_total = None
    return {
        "status_code": resposta.status_code,
        "ok": resposta.status_code in (200, 206),
        "content_type": resposta.headers.get("content-type", ""),
        "tamanho_bytes_aproximado": tamanho_total,
        "url_testada": url,
    }


def _resolver_url_sim_funcional(ano: int = SIM_ANO_REFERENCIA) -> str:
    erros = []
    for url in _urls_sim_ano(ano):
        try:
            status = _testar_url_csv_leve(url)
            if status.get("ok"):
                return url
            erros.append(f"{url} -> HTTP {status.get('status_code')}")
        except Exception as exc:
            erros.append(f"{url} -> {exc}")
    raise ValueError("Nenhuma URL funcional do SIM foi confirmada. Testes: " + " | ".join(erros))


def _calcular_idade_dias_sim(df: pd.DataFrame, col_dt_obito: Optional[str], col_dt_nasc: Optional[str], col_idade: Optional[str]) -> pd.Series:
    """Calcula idade em dias a partir de datas; usa campo IDADE do SIM como fallback aproximado."""
    idade_dias = pd.Series([pd.NA] * len(df), index=df.index, dtype="Float64")
    if col_dt_obito and col_dt_nasc and col_dt_obito in df.columns and col_dt_nasc in df.columns:
        obito = pd.to_datetime(df[col_dt_obito].astype(str).str.zfill(8), format="%d%m%Y", errors="coerce")
        nasc = pd.to_datetime(df[col_dt_nasc].astype(str).str.zfill(8), format="%d%m%Y", errors="coerce")
        dias = (obito - nasc).dt.days
        idade_dias = dias.where((dias >= 0) & (dias < 130 * 365), pd.NA).astype("Float64")
    if col_idade and col_idade in df.columns:
        idade_txt = df[col_idade].astype(str).str.extract(r"(\d+)")[0].fillna("").str.zfill(4)
        unidade = idade_txt.str[0]
        valor = pd.to_numeric(idade_txt.str[1:], errors="coerce")
        fallback = pd.Series([pd.NA] * len(df), index=df.index, dtype="Float64")
        fallback = fallback.mask(unidade.eq("1"), (valor / 24).astype("Float64"))  # horas
        fallback = fallback.mask(unidade.eq("2"), valor.astype("Float64"))  # dias
        fallback = fallback.mask(unidade.eq("3"), (valor * 30.4375).astype("Float64"))  # meses aproximados
        fallback = fallback.mask(unidade.eq("4"), (valor * 365.25).astype("Float64"))  # anos
        fallback = fallback.mask(unidade.eq("5"), ((valor + 100) * 365.25).astype("Float64"))  # 100 anos ou mais
        idade_dias = idade_dias.fillna(fallback)
    return idade_dias


def _classificar_mortalidade_infantil(taxa_mi: Any, obitos_infantis: Any) -> str:
    taxa = _parse_numero_sidra(taxa_mi)
    obitos = _parse_numero_sidra(obitos_infantis) or 0
    if taxa is None or obitos <= 0:
        return "Sem óbito infantil identificado ou sem denominador"
    if taxa >= 20 or obitos >= 10:
        return "Alta mortalidade infantil preliminar"
    if taxa >= 10 or obitos >= 3:
        return "Mortalidade infantil intermediária"
    return "Baixa mortalidade infantil preliminar"


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def carregar_sim_mortalidade_mt(ano: int = SIM_ANO_REFERENCIA) -> pd.DataFrame:
    """
    Carrega o SIM nacional em CSV aberto e consolida óbitos por município de residência em MT.
    A rotina processa em chunks para evitar carregar o arquivo inteiro em memória.
    """
    url = _resolver_url_sim_funcional(ano)
    municipios_mt = carregar_municipios_ibge_mt()[["codigo_ibge", "municipio"]].copy()
    municipios_mt["codigo_ibge"] = municipios_mt["codigo_ibge"].astype(str).str.zfill(7)
    municipios_mt["codigo_ibge_6"] = municipios_mt["codigo_ibge"].str[:6]
    mapa_6_para_7 = dict(zip(municipios_mt["codigo_ibge_6"], municipios_mt["codigo_ibge"]))
    mapa_nome = dict(zip(municipios_mt["codigo_ibge"], municipios_mt["municipio"]))

    agregados: Dict[str, Dict[str, float]] = {}
    ultimo_erro: Optional[Exception] = None
    sucesso = False

    for encoding in ["latin1", "utf-8", "utf-8-sig", "cp1252"]:
        try:
            leitor = pd.read_csv(
                url,
                sep=";",
                dtype=str,
                encoding=encoding,
                chunksize=200_000,
                on_bad_lines="skip",
                low_memory=False,
            )
            primeiro_chunk = True
            col_cod_mun = col_dt_obito = col_dt_nasc = col_idade = None
            parcial: Dict[str, Dict[str, float]] = {}

            for chunk in leitor:
                if primeiro_chunk:
                    col_cod_mun = _identificar_coluna_sinasc(
                        list(chunk.columns),
                        ["CODMUNRES", "COD_MUN_RES", "CO_MUN_RES", "municipio_residencia", "codmunres"],
                    )
                    col_dt_obito = _identificar_coluna_sinasc(list(chunk.columns), ["DTOBITO", "DT_OBITO", "data_obito"])
                    col_dt_nasc = _identificar_coluna_sinasc(list(chunk.columns), ["DTNASC", "DT_NASC", "data_nascimento"])
                    col_idade = _identificar_coluna_sinasc(list(chunk.columns), ["IDADE", "idade"])
                    if not col_cod_mun:
                        raise ValueError(f"Não localizei coluna de município de residência no SIM. Colunas: {', '.join(map(str, chunk.columns[:30]))}")
                    primeiro_chunk = False

                cod = chunk[col_cod_mun].astype(str).str.extract(r"(\d+)")[0]
                codigo_ibge = cod.str[:6].map(mapa_6_para_7)
                mt = chunk[codigo_ibge.notna()].copy()
                mt["codigo_ibge"] = codigo_ibge[codigo_ibge.notna()].values
                if mt.empty:
                    continue

                idade_dias = _calcular_idade_dias_sim(mt, col_dt_obito, col_dt_nasc, col_idade)
                mt["_n"] = 1
                mt["_obito_infantil"] = ((idade_dias.notna()) & (idade_dias < 365.25)).astype(int)
                mt["_obito_menor5"] = ((idade_dias.notna()) & (idade_dias < 5 * 365.25)).astype(int)
                mt["_obito_60mais"] = ((idade_dias.notna()) & (idade_dias >= 60 * 365.25)).astype(int)

                grp = mt.groupby("codigo_ibge", dropna=False).agg(
                    obitos=("_n", "sum"),
                    obitos_infantis=("_obito_infantil", "sum"),
                    obitos_menores_5=("_obito_menor5", "sum"),
                    obitos_60mais=("_obito_60mais", "sum"),
                )
                for codigo, linha in grp.iterrows():
                    d = parcial.setdefault(str(codigo), {
                        "obitos": 0,
                        "obitos_infantis": 0,
                        "obitos_menores_5": 0,
                        "obitos_60mais": 0,
                    })
                    for campo in d.keys():
                        d[campo] += float(linha.get(campo, 0) or 0)

            agregados = parcial
            sucesso = True
            break
        except Exception as exc:
            ultimo_erro = exc
            continue

    if not sucesso:
        raise ValueError(f"Não foi possível ler/consolidar o SIM: {ultimo_erro}")
    if not agregados:
        raise ValueError("A base SIM foi lida, mas nenhum óbito de Mato Grosso foi identificado.")

    registros = []
    for codigo, valores in agregados.items():
        registros.append({
            "codigo_ibge": str(codigo).zfill(7),
            "municipio": mapa_nome.get(str(codigo).zfill(7)),
            f"obitos_sim_{ano}": int(valores.get("obitos", 0) or 0),
            f"obitos_infantis_sim_{ano}": int(valores.get("obitos_infantis", 0) or 0),
            f"obitos_menores_5_sim_{ano}": int(valores.get("obitos_menores_5", 0) or 0),
            f"obitos_60mais_sim_{ano}": int(valores.get("obitos_60mais", 0) or 0),
            "ano_base_sim": int(ano),
            "url_sim": url,
            "fonte_sim": f"Dados Abertos SUS - SIM {ano}",
        })
    return pd.DataFrame(registros).sort_values("municipio", na_position="last").reset_index(drop=True)


def testar_sim_dadosabertos(ano: int = SIM_ANO_REFERENCIA) -> Dict[str, Any]:
    resultados = []
    for url in _urls_sim_ano(ano):
        try:
            status = _testar_url_csv_leve(url)
            resultados.append(status)
        except Exception as exc:
            resultados.append({"url_testada": url, "ok": False, "erro": str(exc)})
    funcional = next((r for r in resultados if r.get("ok")), None)
    return {
        "ok": funcional is not None,
        "ano": int(ano),
        "url_funcional": funcional.get("url_testada") if funcional else None,
        "tamanho_bytes_aproximado": funcional.get("tamanho_bytes_aproximado") if funcional else None,
        "endpoints_testados": resultados,
        "observacao": "Arquivo CSV anual do SIM em base aberta. O processamento completo é feito em chunks e filtrado para Mato Grosso.",
    }

def _excel_multiplas_abas(abas: Dict[str, pd.DataFrame]) -> bytes:
    """Gera um Excel com múltiplas abas, removendo abas vazias e ajustando nomes."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        escreveu = False
        for nome, df in abas.items():
            if isinstance(df, pd.DataFrame) and not df.empty:
                nome_aba = re.sub(r"[\\/*?:\[\]]", " ", str(nome))[:31].strip() or "Dados"
                df.to_excel(writer, sheet_name=nome_aba, index=False)
                escreveu = True
        if not escreveu:
            pd.DataFrame({"mensagem": ["Sem dados para exportar"]}).to_excel(writer, sheet_name="Dados", index=False)
    buffer.seek(0)
    return buffer.getvalue()


def preparar_cnes_detalhado_para_exibicao(df_cnes: pd.DataFrame, base: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Padroniza o detalhamento CNES para conferência em tela e exportação."""
    if df_cnes is None or df_cnes.empty:
        return pd.DataFrame()
    df = df_cnes.copy()
    if "codigo_ibge" in df.columns:
        df["codigo_ibge"] = _codigo_ibge_texto_7(df["codigo_ibge"])
    if isinstance(base, pd.DataFrame) and not base.empty:
        colunas_base = [col for col in ["codigo_ibge", "regiao_saude_sus", "macrorregiao_saude_sus"] if col in base.columns]
        if "codigo_ibge" in colunas_base:
            base_merge = base[colunas_base].copy()
            base_merge["codigo_ibge"] = _codigo_ibge_texto_7(base_merge["codigo_ibge"])
            base_merge = base_merge.drop_duplicates("codigo_ibge")
            df = df.merge(base_merge, on="codigo_ibge", how="left")
    ordem = [
        "codigo_ibge",
        "municipio",
        "regiao_saude_sus",
        "macrorregiao_saude_sus",
        "cnes",
        "nome_unidade_cnes",
        "tipo_unidade_cnes",
        "subtipo_unidade_cnes",
        "categoria_preliminar_cnes",
        "situacao_cnes",
        "atende_sus_cnes",
        "atende_sus_preliminar",
        "natureza_juridica_cnes",
        "gestao_cnes",
        "esfera_administrativa_cnes",
        "natureza_gestao_preliminar",
        "endereco_cnes",
        "bairro_cnes",
        "cep_cnes",
        "telefone_cnes",
        "fonte_cnes",
    ]
    colunas = [col for col in ordem if col in df.columns]
    demais = [col for col in df.columns if col not in colunas]
    return df[colunas + demais].sort_values(["municipio", "nome_unidade_cnes"], na_position="last").reset_index(drop=True)


def testar_cnes_estabelecimentos_dadosabertos() -> Dict[str, Any]:
    status = _request_status(URL_CNES_ESTABELECIMENTOS_ZIP, timeout=60)
    status["url_testada"] = URL_CNES_ESTABELECIMENTOS_ZIP
    status["observacao"] = "ZIP público de estabelecimentos CNES publicado no Portal de Dados Abertos do SUS."
    return status

@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def testar_malha_mt() -> Dict[str, Any]:
    url = "https://servicodados.ibge.gov.br/api/v3/malhas/estados/51?formato=application/vnd.geo+json&qualidade=minima"
    status = _request_status(url, timeout=45)
    status["url_testada"] = url
    return status


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def carregar_malha_municipal_ibge_mt() -> Dict[str, Any]:
    """
    Carrega a malha municipal simplificada de Mato Grosso em GeoJSON pela API do IBGE.
    A malha é usada apenas para visualização temática preliminar no sistema.
    """
    url = (
        "https://servicodados.ibge.gov.br/api/v3/malhas/estados/51"
        "?formato=application/vnd.geo+json&qualidade=minima&intrarregiao=municipio"
    )
    resposta = requests.get(
        url,
        timeout=90,
        headers={"User-Agent": "SES-MT-Estudo-UBS/1.0", "Accept": "application/geo+json,application/json,*/*"},
    )
    resposta.raise_for_status()
    geojson = resposta.json()
    if not isinstance(geojson, dict) or not geojson.get("features"):
        raise ValueError("A API de malhas do IBGE não retornou uma FeatureCollection válida.")
    return geojson


def _detectar_chave_geojson_municipio(geojson: Dict[str, Any]) -> str:
    """Identifica a propriedade mais provável do código municipal no GeoJSON do IBGE."""
    features = geojson.get("features") or []
    if not features:
        return "properties.codarea"
    props = features[0].get("properties") or {}
    candidatos = ["codarea", "CD_MUN", "CD_GEOCMU", "id", "codigo", "geocodigo"]
    props_norm = {_normalizar_nome_coluna(k): k for k in props.keys()}
    for candidato in candidatos:
        chave = props_norm.get(_normalizar_nome_coluna(candidato))
        if chave:
            return f"properties.{chave}"
    return "properties.codarea"




# =============================================================================
# INEP / Censo Escolar - camada educacional-territorial
# =============================================================================

def _inep_censo_escolar_url(ano: int = INEP_CENSO_ESCOLAR_ANO) -> str:
    if ano in INEP_CENSO_ESCOLAR_URLS:
        return INEP_CENSO_ESCOLAR_URLS[ano]
    return f"https://download.inep.gov.br/dados_abertos/microdados_censo_escolar_{ano}.zip"


def _requests_get_inep(url: str, **kwargs) -> tuple[requests.Response, bool]:
    """Faz requisição ao download.inep.gov.br com fallback para erro de cadeia SSL.

    Em alguns ambientes Windows/rede institucional, o certificado intermediário do host do INEP
    não é resolvido pelo pacote local de certificados. Primeiro o sistema tenta com validação SSL
    normal; se ocorrer SSLError, tenta novamente com verify=False apenas para este download público.
    O retorno booleano indica se o fallback sem verificação SSL foi usado.
    """
    try:
        return requests.get(url, **kwargs), False
    except requests.exceptions.SSLError:
        kwargs["verify"] = False
        return requests.get(url, **kwargs), True


def testar_inep_censo_escolar(ano: int = INEP_CENSO_ESCOLAR_ANO) -> Dict[str, Any]:
    """Testa disponibilidade do ZIP oficial de microdados do Censo Escolar.

    O teste usa leitura parcial/streaming para não baixar o arquivo inteiro apenas para verificar disponibilidade.
    """
    url = _inep_censo_escolar_url(ano)
    try:
        resp, ssl_fallback = _requests_get_inep(
            url,
            headers={"Range": "bytes=0-1023"},
            stream=True,
            timeout=TIMEOUT_PADRAO,
        )
        content_length = resp.headers.get("Content-Length") or resp.headers.get("content-length")
        content_range = resp.headers.get("Content-Range") or resp.headers.get("content-range")
        # Fecha o stream sem baixar o arquivo inteiro.
        resp.close()
        return {
            "ok": resp.status_code in (200, 206),
            "status_code": resp.status_code,
            "content_type": resp.headers.get("Content-Type"),
            "content_length_parcial": content_length,
            "content_range": content_range,
            "url_testada": url,
            "ano": ano,
            "ssl_fallback_usado": ssl_fallback,
            "observacao": "Microdados oficiais do Censo Escolar/INEP. A carga consolidada baixa o ZIP apenas quando necessário e agrega por município, sem usar dados pessoais. Se ssl_fallback_usado=True, a rede local não validou a cadeia de certificados do INEP e o sistema usou fallback apenas para este arquivo público.",
        }
    except Exception as exc:
        return {"ok": False, "url_testada": url, "ano": ano, "erro": str(exc)}


def _baixar_zip_inep_censo_escolar(ano: int = INEP_CENSO_ESCOLAR_ANO, forcar_atualizacao: bool = False) -> Path:
    """Baixa e armazena em cache local o ZIP de microdados do Censo Escolar."""
    cache_dir = Path("data") / "cache" / "inep"
    cache_dir.mkdir(parents=True, exist_ok=True)
    destino = cache_dir / f"microdados_censo_escolar_{ano}.zip"
    if destino.exists() and destino.stat().st_size > 1024 * 1024 and not forcar_atualizacao:
        return destino

    url = _inep_censo_escolar_url(ano)
    resp, ssl_fallback = _requests_get_inep(url, stream=True, timeout=max(TIMEOUT_PADRAO, 120))
    with resp:
        resp.raise_for_status()
        with open(destino, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    if not destino.exists() or destino.stat().st_size == 0:
        raise ValueError("Arquivo do Censo Escolar/INEP foi baixado, mas está vazio.")
    if ssl_fallback:
        (cache_dir / f"microdados_censo_escolar_{ano}_ssl_fallback.txt").write_text(
            "Download realizado com verify=False porque a cadeia de certificados do INEP não foi validada neste ambiente local. Fonte pública: download.inep.gov.br",
            encoding="utf-8",
        )
    return destino


def _encontrar_csv_escolas_inep(zip_path: Path) -> str:
    with zipfile.ZipFile(zip_path, "r") as zf:
        nomes = zf.namelist()
        candidatos = [
            n for n in nomes
            if n.lower().endswith(".csv") and (
                "microdados_ed_basica" in n.lower()
                or "escolas" in n.lower()
                or "escola" in n.lower()
            )
        ]
        if not candidatos:
            candidatos = [n for n in nomes if n.lower().endswith(".csv")]
        if not candidatos:
            raise ValueError("Nenhum CSV encontrado dentro do ZIP do Censo Escolar.")
        # Prefere o arquivo de microdados principal.
        candidatos = sorted(candidatos, key=lambda n: ("microdados_ed_basica" not in n.lower(), len(n)))
        return candidatos[0]


def _coluna_existente(df: pd.DataFrame, nomes: List[str]) -> Optional[str]:
    mapa = {str(c).strip().upper(): c for c in df.columns}
    for nome in nomes:
        if nome.strip().upper() in mapa:
            return mapa[nome.strip().upper()]
    return None


def _to_num_inep(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(",", ".", regex=False).str.strip(), errors="coerce").fillna(0)


def carregar_censo_escolar_inep_mt(ano: int = INEP_CENSO_ESCOLAR_ANO, forcar_atualizacao: bool = False) -> pd.DataFrame:
    """Carrega microdados do Censo Escolar/INEP e consolida indicadores por município de MT.

    A consolidação é agregada por município e não expõe dados pessoais.
    """
    zip_path = _baixar_zip_inep_censo_escolar(ano, forcar_atualizacao=forcar_atualizacao)
    csv_name = _encontrar_csv_escolas_inep(zip_path)

    acumulados: List[pd.DataFrame] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        # Lê em blocos para reduzir uso de memória.
        with zf.open(csv_name) as arquivo:
            leitor = pd.read_csv(
                arquivo,
                sep=";",
                encoding="latin1",
                dtype=str,
                chunksize=50000,
                low_memory=False,
            )
            for chunk in leitor:
                col_mun = _coluna_existente(chunk, ["CO_MUNICIPIO", "CO_MUNICIPIO_ESC", "COD_MUNICIPIO"])
                col_nome = _coluna_existente(chunk, ["NO_MUNICIPIO", "NO_MUNICIPIO_ESC", "NOME_MUNICIPIO"])
                col_uf = _coluna_existente(chunk, ["CO_UF", "SG_UF"])
                if not col_mun:
                    raise ValueError(f"Coluna de município não localizada no Censo Escolar. Colunas: {list(chunk.columns)[:30]}")

                trabalho = chunk.copy()
                trabalho["codigo_ibge"] = trabalho[col_mun].astype(str).str.extract(r"(\d+)")[0].str[:7]
                trabalho = trabalho[trabalho["codigo_ibge"].str.startswith(UF_MT, na=False)]
                if trabalho.empty:
                    continue

                col_situacao = _coluna_existente(trabalho, ["TP_SITUACAO_FUNCIONAMENTO"])
                if col_situacao:
                    # 1 costuma representar escola em atividade.
                    trabalho = trabalho[trabalho[col_situacao].astype(str).str.strip().isin(["1", "1.0"])]
                    if trabalho.empty:
                        continue

                col_local = _coluna_existente(trabalho, ["TP_LOCALIZACAO"])
                col_dep = _coluna_existente(trabalho, ["TP_DEPENDENCIA"])
                col_indigena = _coluna_existente(trabalho, ["IN_ESCOLA_INDIGENA", "IN_EDUCACAO_INDIGENA"])
                col_quilombola = _coluna_existente(trabalho, ["IN_AREA_REMANESCENTE_QUILOMBOLA", "IN_COMUNIDADE_QUILOMBOLA"])
                col_mat_bas = _coluna_existente(trabalho, ["QT_MAT_BAS", "QT_MAT_BASICA"])
                col_mat_inf = _coluna_existente(trabalho, ["QT_MAT_INF"])
                col_mat_fund = _coluna_existente(trabalho, ["QT_MAT_FUND"])
                col_mat_med = _coluna_existente(trabalho, ["QT_MAT_MED"])
                col_mat_eja = _coluna_existente(trabalho, ["QT_MAT_EJA"])

                out = pd.DataFrame({
                    "codigo_ibge": trabalho["codigo_ibge"],
                    "municipio_inep": trabalho[col_nome] if col_nome else None,
                    "escola_linha": 1,
                    "escola_rural": trabalho[col_local].astype(str).str.strip().isin(["2", "2.0"]).astype(int) if col_local else 0,
                    "escola_urbana": trabalho[col_local].astype(str).str.strip().isin(["1", "1.0"]).astype(int) if col_local else 0,
                    "escola_indigena": trabalho[col_indigena].astype(str).str.strip().isin(["1", "1.0", "S", "SIM"]).astype(int) if col_indigena else 0,
                    "escola_quilombola": trabalho[col_quilombola].astype(str).str.strip().isin(["1", "1.0", "S", "SIM"]).astype(int) if col_quilombola else 0,
                    "escola_publica": trabalho[col_dep].astype(str).str.strip().isin(["1", "2", "3", "1.0", "2.0", "3.0"]).astype(int) if col_dep else 0,
                    "matriculas_basica": _to_num_inep(trabalho[col_mat_bas]) if col_mat_bas else 0,
                    "matriculas_infantil": _to_num_inep(trabalho[col_mat_inf]) if col_mat_inf else 0,
                    "matriculas_fundamental": _to_num_inep(trabalho[col_mat_fund]) if col_mat_fund else 0,
                    "matriculas_medio": _to_num_inep(trabalho[col_mat_med]) if col_mat_med else 0,
                    "matriculas_eja": _to_num_inep(trabalho[col_mat_eja]) if col_mat_eja else 0,
                })
                acumulados.append(out)

    if not acumulados:
        raise ValueError("Censo Escolar lido, mas nenhum registro de Mato Grosso foi encontrado.")

    df = pd.concat(acumulados, ignore_index=True)
    agg = df.groupby("codigo_ibge", as_index=False).agg(
        escolas_total_inep=("escola_linha", "sum"),
        escolas_rurais_inep=("escola_rural", "sum"),
        escolas_urbanas_inep=("escola_urbana", "sum"),
        escolas_indigenas_inep=("escola_indigena", "sum"),
        escolas_quilombolas_inep=("escola_quilombola", "sum"),
        escolas_publicas_inep=("escola_publica", "sum"),
        matriculas_basica_inep=("matriculas_basica", "sum"),
        matriculas_infantil_inep=("matriculas_infantil", "sum"),
        matriculas_fundamental_inep=("matriculas_fundamental", "sum"),
        matriculas_medio_inep=("matriculas_medio", "sum"),
        matriculas_eja_inep=("matriculas_eja", "sum"),
    )
    for c in [c for c in agg.columns if c != "codigo_ibge"]:
        agg[c] = pd.to_numeric(agg[c], errors="coerce").fillna(0).astype(int)

    agg["percentual_escolas_rurais_inep"] = agg.apply(
        lambda r: round((r["escolas_rurais_inep"] / r["escolas_total_inep"]) * 100, 2) if r["escolas_total_inep"] else None,
        axis=1,
    )
    agg["classificacao_dispersao_escolar_inep"] = agg["escolas_rurais_inep"].apply(classificar_dispersao_escolar_inep)
    agg["ano_base_inep_censo_escolar"] = ano
    agg["fonte_inep_censo_escolar"] = f"INEP - Microdados do Censo Escolar da Educação Básica {ano}"
    agg["observacao_inep_censo_escolar"] = "Dados agregados por município; não usa dados pessoais. Escolas rurais/indígenas/quilombolas sinalizam dispersão territorial e potencial para ações APS-Educação."
    return agg


def classificar_dispersao_escolar_inep(escolas_rurais: Any) -> str:
    valor = _parse_numero_sidra(escolas_rurais)
    if valor is None:
        return "Sem informação"
    if valor >= 10:
        return "Muito alta dispersão escolar rural"
    if valor >= 5:
        return "Alta dispersão escolar rural"
    if valor >= 1:
        return "Possui escolas rurais"
    return "Sem escolas rurais identificadas"



def _coluna_por_regex_inep(df: pd.DataFrame, padroes: List[str], excluir: Optional[List[str]] = None) -> Optional[str]:
    """Localiza coluna do INEP por regex, de forma tolerante a mudanças de nomenclatura."""
    excluir = excluir or []
    for col in df.columns:
        nome = str(col).strip().upper()
        if any(re.search(p, nome) for p in padroes) and not any(re.search(p, nome) for p in excluir):
            return col
    return None


def classificar_pressao_educacao_especial_inep(percentual: Any) -> str:
    valor = _parse_numero_sidra(percentual)
    if valor is None:
        return "Sem informação"
    if valor >= 10:
        return "Muito alta demanda educacional especial"
    if valor >= 6:
        return "Alta demanda educacional especial"
    if valor >= 3:
        return "Demanda moderada educacional especial"
    if valor > 0:
        return "Baixa demanda educacional especial"
    return "Sem demanda identificada no microdado"


def carregar_educacao_especial_inep_mt(ano: int = INEP_CENSO_ESCOLAR_ANO, forcar_atualizacao: bool = False) -> pd.DataFrame:
    """Consolida educação especial/AEE por município a partir do Censo Escolar.

    A base é agregada por município e não expõe dados pessoais. Usa o mesmo ZIP oficial do INEP
    já utilizado pelo módulo de Censo Escolar, mas gera um cache próprio para não reprocessar
    o arquivo pesado em todas as execuções.
    """
    cache_dir = Path("data") / "cache" / "inep"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_csv = cache_dir / f"educacao_especial_aee_mt_{ano}.csv"
    if cache_csv.exists() and cache_csv.stat().st_size > 0 and not forcar_atualizacao:
        return pd.read_csv(cache_csv, dtype={"codigo_ibge": str})

    zip_path = _baixar_zip_inep_censo_escolar(ano, forcar_atualizacao=forcar_atualizacao)
    csv_name = _encontrar_csv_escolas_inep(zip_path)
    acumulados: List[pd.DataFrame] = []
    colunas_usadas = set()

    with zipfile.ZipFile(zip_path, "r") as zf:
        with zf.open(csv_name) as arquivo:
            leitor = pd.read_csv(
                arquivo,
                sep=";",
                encoding="latin1",
                dtype=str,
                chunksize=50000,
                low_memory=False,
            )
            for chunk in leitor:
                col_mun = _coluna_existente(chunk, ["CO_MUNICIPIO", "CO_MUNICIPIO_ESC", "COD_MUNICIPIO"])
                if not col_mun:
                    raise ValueError("Coluna de município não localizada no microdado do INEP.")

                trabalho = chunk.copy()
                trabalho["codigo_ibge"] = trabalho[col_mun].astype(str).str.extract(r"(\d+)")[0].str[:7]
                trabalho = trabalho[trabalho["codigo_ibge"].str.startswith(UF_MT, na=False)]
                if trabalho.empty:
                    continue

                col_situacao = _coluna_existente(trabalho, ["TP_SITUACAO_FUNCIONAMENTO"])
                if col_situacao:
                    trabalho = trabalho[trabalho[col_situacao].astype(str).str.strip().isin(["1", "1.0"])]
                    if trabalho.empty:
                        continue

                col_mat_bas = _coluna_existente(trabalho, ["QT_MAT_BAS", "QT_MAT_BASICA"])
                col_mat_esp = _coluna_existente(trabalho, [
                    "QT_MAT_ESP", "QT_MAT_EDUC_ESP", "QT_MAT_EDUCACAO_ESPECIAL", "QT_MAT_BAS_ESP"
                ])
                if not col_mat_esp:
                    col_mat_esp = _coluna_por_regex_inep(trabalho, [r"^QT_MAT_ESP$", r"^QT_MAT_.*ESP"], excluir=[r"EJA", r"PROF"])

                col_mat_aee = _coluna_existente(trabalho, [
                    "QT_MAT_AEE", "QT_MAT_ATIV_COMP_AEE", "QT_MAT_ESP_AEE", "QT_MAT_AEE_BAS"
                ])
                if not col_mat_aee:
                    col_mat_aee = _coluna_por_regex_inep(trabalho, [r"AEE"], excluir=[r"DOC", r"TUR"])

                col_sala_aee = _coluna_existente(trabalho, ["IN_SALA_AEE", "IN_SALA_RECURSO", "IN_ATENDIMENTO_ESPECIALIZADO"])
                col_acessibilidade = _coluna_existente(trabalho, [
                    "IN_ACESSIBILIDADE", "IN_BANHEIRO_PNE", "IN_DEPENDENCIAS_PNE", "IN_RECURSOS_ACESSIBILIDADE"
                ])

                for c in [col_mat_bas, col_mat_esp, col_mat_aee, col_sala_aee, col_acessibilidade]:
                    if c:
                        colunas_usadas.add(str(c))

                mat_bas = _to_num_inep(trabalho[col_mat_bas]) if col_mat_bas else pd.Series([0] * len(trabalho), index=trabalho.index)
                mat_esp = _to_num_inep(trabalho[col_mat_esp]) if col_mat_esp else pd.Series([0] * len(trabalho), index=trabalho.index)
                mat_aee = _to_num_inep(trabalho[col_mat_aee]) if col_mat_aee else pd.Series([0] * len(trabalho), index=trabalho.index)

                escola_com_sala_aee = trabalho[col_sala_aee].astype(str).str.strip().isin(["1", "1.0", "S", "SIM"]).astype(int) if col_sala_aee else pd.Series([0] * len(trabalho), index=trabalho.index)
                escola_com_acess = trabalho[col_acessibilidade].astype(str).str.strip().isin(["1", "1.0", "S", "SIM"]).astype(int) if col_acessibilidade else pd.Series([0] * len(trabalho), index=trabalho.index)

                out = pd.DataFrame({
                    "codigo_ibge": trabalho["codigo_ibge"],
                    "escola_linha": 1,
                    "matriculas_basica": mat_bas,
                    "matriculas_educacao_especial": mat_esp,
                    "matriculas_aee": mat_aee,
                    "escola_com_educacao_especial": (mat_esp > 0).astype(int),
                    "escola_com_aee": ((mat_aee > 0) | (escola_com_sala_aee > 0)).astype(int),
                    "escola_com_indicador_acessibilidade": escola_com_acess,
                })
                acumulados.append(out)

    if not acumulados:
        raise ValueError("Censo Escolar lido, mas nenhum registro de Mato Grosso foi encontrado para educação especial/AEE.")

    df = pd.concat(acumulados, ignore_index=True)
    agg = df.groupby("codigo_ibge", as_index=False).agg(
        escolas_total_ref_educacao_especial_inep=("escola_linha", "sum"),
        escolas_com_matricula_educacao_especial_inep=("escola_com_educacao_especial", "sum"),
        escolas_com_aee_inep=("escola_com_aee", "sum"),
        escolas_com_indicador_acessibilidade_inep=("escola_com_indicador_acessibilidade", "sum"),
        matriculas_basica_ref_educacao_especial_inep=("matriculas_basica", "sum"),
        matriculas_educacao_especial_inep=("matriculas_educacao_especial", "sum"),
        matriculas_aee_inep=("matriculas_aee", "sum"),
    )
    for c in [c for c in agg.columns if c != "codigo_ibge"]:
        agg[c] = pd.to_numeric(agg[c], errors="coerce").fillna(0)

    agg["pct_matriculas_educacao_especial_inep"] = agg.apply(
        lambda r: round((r["matriculas_educacao_especial_inep"] / r["matriculas_basica_ref_educacao_especial_inep"]) * 100, 2)
        if r["matriculas_basica_ref_educacao_especial_inep"] else None,
        axis=1,
    )
    agg["pct_escolas_com_educacao_especial_inep"] = agg.apply(
        lambda r: round((r["escolas_com_matricula_educacao_especial_inep"] / r["escolas_total_ref_educacao_especial_inep"]) * 100, 2)
        if r["escolas_total_ref_educacao_especial_inep"] else None,
        axis=1,
    )
    agg["classificacao_pressao_educacao_especial_inep"] = agg["pct_matriculas_educacao_especial_inep"].apply(classificar_pressao_educacao_especial_inep)
    agg["ano_base_educacao_especial_inep"] = ano
    agg["fonte_educacao_especial_inep"] = f"INEP - Microdados do Censo Escolar da Educação Básica {ano}"
    agg["observacao_educacao_especial_inep"] = "Camada agregada por município. Não substitui BPC/MDS nem cadastro clínico; qualifica demanda potencial para cuidado continuado, reabilitação, deficiência/TEA e articulação APS-Educação."
    agg["metodo_educacao_especial_inep"] = "Colunas usadas: " + (", ".join(sorted(colunas_usadas)) if colunas_usadas else "não localizadas; consolidado sem informação útil")

    agg.to_csv(cache_csv, index=False, encoding="utf-8-sig")
    return agg


def testar_educacao_especial_inep(ano: int = INEP_CENSO_ESCOLAR_ANO) -> Dict[str, Any]:
    try:
        df = carregar_educacao_especial_inep_mt(ano)
        total_matriculas = int(pd.to_numeric(df.get("matriculas_educacao_especial_inep"), errors="coerce").fillna(0).sum()) if "matriculas_educacao_especial_inep" in df.columns else 0
        total_aee = int(pd.to_numeric(df.get("matriculas_aee_inep"), errors="coerce").fillna(0).sum()) if "matriculas_aee_inep" in df.columns else 0
        mun_com_esp = int(pd.to_numeric(df.get("matriculas_educacao_especial_inep"), errors="coerce").fillna(0).gt(0).sum()) if "matriculas_educacao_especial_inep" in df.columns else 0
        top = []
        if "matriculas_educacao_especial_inep" in df.columns:
            top = df.sort_values("matriculas_educacao_especial_inep", ascending=False, na_position="last").head(5).to_dict("records")
        return {
            "ok": total_matriculas > 0,
            "ano": ano,
            "municipios_lidos": int(len(df)),
            "municipios_com_educacao_especial": mun_com_esp,
            "matriculas_educacao_especial_total_mt": total_matriculas,
            "matriculas_aee_total_mt": total_aee,
            "top_municipios": top,
            "fonte": f"INEP - Microdados do Censo Escolar da Educação Básica {ano}",
            "observacao": "Consolidado agregado por município; não usa dados pessoais.",
        }
    except Exception as exc:
        return {"ok": False, "ano": ano, "erro": str(exc), "fonte": "INEP - Censo Escolar / Educação Especial e AEE"}




# IBGE / SIDRA - populações indígena e quilombola (Censo 2022)

POVOS_TRADICIONAIS_CACHE_VERSION = 4


def classificar_equidade_povos_tradicionais(valor_por_1000: Any) -> str:
    valor = _parse_numero_sidra(valor_por_1000)
    if valor is None:
        return "Sem informação"
    if valor <= 0:
        return "Sem população indígena/quilombola registrada"
    if valor < 10:
        return "Baixa presença proporcional registrada"
    if valor < 30:
        return "Presença proporcional moderada registrada"
    if valor < 100:
        return "Alta presença proporcional registrada"
    return "Muito alta presença proporcional registrada"


def _sidra_municipio_codigo_nome(item: Dict[str, Any]) -> tuple[str, str]:
    """Localiza dinamicamente o código/nome do município numa resposta SIDRA.

    Em algumas consultas SIDRA, o município vem como D1C/D1N; em outras, por causa
    de classificações/categorias, pode vir em D2C/D2N ou outro par. A rotina antiga
    assumia D1C e por isso podia ler a tabela corretamente, mas consolidar zero.
    """
    candidatos = []
    for chave, valor in item.items():
        if not str(chave).endswith("C"):
            continue
        codigo = str(valor or "").strip()
        if codigo.isdigit() and len(codigo) == 7 and codigo.startswith("51"):
            base = str(chave)[:-1]
            nome = str(item.get(base + "N", "") or "").strip()
            candidatos.append((codigo, _limpar_nome_municipio(nome)))
    if candidatos:
        return candidatos[0]
    return "", ""


def _extrair_populacao_tradicional_de_resposta_sidra(dados: list, grupo: str, metodo: str) -> pd.DataFrame:
    grupo_norm = normalizar_texto(grupo)
    registros: Dict[str, Dict[str, Any]] = {}
    if not dados or len(dados) < 2:
        return pd.DataFrame()

    for item in dados[1:]:
        codigo, municipio = _sidra_municipio_codigo_nome(item)
        valor = _parse_numero_sidra(item.get("V"))
        if not codigo or not municipio or valor is None:
            continue

        labels = " | ".join(str(v or "") for k, v in item.items() if str(k).endswith("N"))
        labels_norm = normalizar_texto(labels)
        unidade_norm = normalizar_texto(str(item.get("UM", "") or ""))
        variavel_norm = normalizar_texto(str(item.get("D2N", "") or item.get("D3N", "") or ""))

        # Evita percentuais e variáveis totais da população residente.
        if "percentual" in labels_norm or "percentual" in unidade_norm or "%" in str(item.get("V", "")):
            continue
        if grupo_norm.startswith("indig"):
            marcador_ok = ("pessoas indigenas" in labels_norm or "populacao indigena" in labels_norm or "indigena" in variavel_norm)
            marcador_ruim = "percentual" in labels_norm
        else:
            marcador_ok = ("pessoas quilombolas" in labels_norm or "populacao quilombola" in labels_norm or "quilombola" in variavel_norm)
            marcador_ruim = "percentual" in labels_norm
        if not marcador_ok or marcador_ruim:
            continue

        # Nas consultas com várias categorias, o total municipal costuma ser o maior valor.
        atual = registros.get(codigo, {}).get("pessoas")
        if atual is None or float(valor) > float(atual):
            registros[codigo] = {
                "codigo_ibge": codigo,
                "municipio": municipio,
                "pessoas": int(round(float(valor))),
                "descricao_pessoas": labels,
                "metodo_sidra": metodo,
            }

    return pd.DataFrame(list(registros.values()))


def _carregar_populacao_tradicional_sidra(tabela: int, grupo: str, ano: int = 2022) -> pd.DataFrame:
    """Carrega população indígena/quilombola municipal por SIDRA.

    Esta versão usa três estratégias:
    1) consultas explícitas nas tabelas principais 9718/9578;
    2) tabelas de sexo/idade 8175/8176, tomando o maior valor municipal como total;
    3) fallback v/all por rótulo com identificação dinâmica de município.
    """
    filtro_territorial = quote("in n3 51")
    grupo_norm = normalizar_texto(grupo)

    consultas: List[tuple[str, str]] = []
    if grupo_norm.startswith("indig"):
        consultas = [
            (f"https://apisidra.ibge.gov.br/values/t/9718/n6/{filtro_territorial}/v/350/p/{int(ano)}/c1714/all/c2661/all?formato=json", "SIDRA 9718 explícita c1714/all c2661/all"),
            (f"https://apisidra.ibge.gov.br/values/t/9718/n6/{filtro_territorial}/v/all/p/{int(ano)}/c1714/all/c2661/all?formato=json", "SIDRA 9718 v/all"),
            (f"https://apisidra.ibge.gov.br/values/t/8175/n6/{filtro_territorial}/v/350/p/{int(ano)}/c287/all/c2/all/c2661/all?formato=json", "SIDRA 8175 sexo/idade/localização"),
            (f"https://apisidra.ibge.gov.br/values/t/9971/n6/{filtro_territorial}/v/350/p/{int(ano)}/c2/all/c287/all/c2661/all?formato=json", "SIDRA 9971 sexo/grupos idade/localização"),
        ]
    elif grupo_norm.startswith("quilomb"):
        consultas = [
            (f"https://apisidra.ibge.gov.br/values/t/9578/n6/{filtro_territorial}/v/4709/p/{int(ano)}/c2661/all?formato=json", "SIDRA 9578 explícita c2661/all"),
            (f"https://apisidra.ibge.gov.br/values/t/9578/n6/{filtro_territorial}/v/all/p/{int(ano)}/c2661/all?formato=json", "SIDRA 9578 v/all"),
            (f"https://apisidra.ibge.gov.br/values/t/8176/n6/{filtro_territorial}/v/4709/p/{int(ano)}/c287/all/c2/all/c2661/all?formato=json", "SIDRA 8176 sexo/idade/localização"),
            (f"https://apisidra.ibge.gov.br/values/t/9805/n6/{filtro_territorial}/v/4709/p/{int(ano)}/c287/all/c2/all/c2661/all?formato=json", "SIDRA 9805 sexo/idade/localização"),
        ]

    erros: List[str] = []
    diagnosticos: List[str] = []
    melhor_df = pd.DataFrame()

    for url, metodo in consultas:
        try:
            dados = _request_json(url, timeout=120)
            df = _extrair_populacao_tradicional_de_resposta_sidra(dados, grupo, metodo)
            total = int(df["pessoas"].sum()) if not df.empty and "pessoas" in df.columns else 0
            diagnosticos.append(f"{metodo}: {len(df)} municípios, total {total}")
            if not df.empty and total > 0:
                return df
            if not df.empty and melhor_df.empty:
                melhor_df = df
        except Exception as exc:
            erros.append(f"{metodo}: {exc}")
            continue

    if not melhor_df.empty:
        return melhor_df

    raise ValueError(
        f"Nenhum registro municipal de {grupo} interpretável. "
        + " | ".join((diagnosticos + erros)[:10])
    )


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def carregar_povos_tradicionais_ibge_mt(ano_censo: int = 2022, _cache_version: int = POVOS_TRADICIONAIS_CACHE_VERSION) -> pd.DataFrame:
    """Carrega camada municipal agregada de população indígena e quilombola pelo IBGE/SIDRA."""
    base = carregar_municipios_ibge_mt()[["codigo_ibge", "municipio"]].copy()
    erros: List[str] = []
    alertas: List[str] = []

    try:
        ind = _carregar_populacao_tradicional_sidra(9718, "indigena", ano_censo)
        base = base.merge(
            ind[["codigo_ibge", "pessoas", "metodo_sidra"]].rename(columns={"pessoas": "pessoas_indigenas_2022", "metodo_sidra": "metodo_indigena_sidra"}),
            on="codigo_ibge",
            how="left",
        )
        alertas.append("indígena: SIDRA")
    except Exception as exc:
        base["pessoas_indigenas_2022"] = None
        base["metodo_indigena_sidra"] = None
        erros.append(f"população indígena: {exc}")

    try:
        quil = _carregar_populacao_tradicional_sidra(9578, "quilombola", ano_censo)
        base = base.merge(
            quil[["codigo_ibge", "pessoas", "metodo_sidra"]].rename(columns={"pessoas": "pessoas_quilombolas_2022", "metodo_sidra": "metodo_quilombola_sidra"}),
            on="codigo_ibge",
            how="left",
        )
        alertas.append("quilombola: SIDRA")
    except Exception as exc:
        base["pessoas_quilombolas_2022"] = None
        base["metodo_quilombola_sidra"] = None
        erros.append(f"população quilombola: {exc}")

    for col in ["pessoas_indigenas_2022", "pessoas_quilombolas_2022"]:
        base[col] = pd.to_numeric(base[col], errors="coerce").fillna(0).astype(int)

    base["pessoas_tradicionais_total_2022"] = base["pessoas_indigenas_2022"] + base["pessoas_quilombolas_2022"]
    base["ano_base_povos_tradicionais"] = int(ano_censo)
    base["fonte_povos_tradicionais"] = "IBGE/SIDRA - Censo 2022: populações indígena e quilombola"
    base["observacao_povos_tradicionais"] = "Camada agregada municipal para análise de equidade territorial; não usa dados pessoais e não define construção de UBS isoladamente."
    base["alerta_povos_tradicionais"] = " | ".join(erros[:4]) if erros else None
    base["metodo_povos_tradicionais"] = " | ".join(alertas) if alertas else None
    return base.sort_values("municipio").reset_index(drop=True)


def testar_povos_tradicionais_ibge(ano_censo: int = 2022) -> Dict[str, Any]:
    # Limpa apenas esta camada para evitar que um resultado zero anterior do Streamlit fique preso em cache.
    try:
        carregar_povos_tradicionais_ibge_mt.clear()
    except Exception:
        pass
    df = carregar_povos_tradicionais_ibge_mt(ano_censo, POVOS_TRADICIONAIS_CACHE_VERSION)
    return {
        "ok": bool((df["pessoas_tradicionais_total_2022"] > 0).any()),
        "ano": int(ano_censo),
        "municipios_lidos": int(len(df)),
        "municipios_com_populacao_indigena": int((df["pessoas_indigenas_2022"] > 0).sum()),
        "municipios_com_populacao_quilombola": int((df["pessoas_quilombolas_2022"] > 0).sum()),
        "pessoas_indigenas_total_mt": int(df["pessoas_indigenas_2022"].sum()),
        "pessoas_quilombolas_total_mt": int(df["pessoas_quilombolas_2022"].sum()),
        "top_indigenas": df.sort_values("pessoas_indigenas_2022", ascending=False).head(5)[["municipio", "pessoas_indigenas_2022"]].to_dict("records"),
        "top_quilombolas": df.sort_values("pessoas_quilombolas_2022", ascending=False).head(5)[["municipio", "pessoas_quilombolas_2022"]].to_dict("records"),
        "metodo_indigena": str(df.get("metodo_indigena_sidra", pd.Series(dtype=str)).dropna().head(1).iloc[0]) if "metodo_indigena_sidra" in df.columns and not df["metodo_indigena_sidra"].dropna().empty else None,
        "metodo_quilombola": str(df.get("metodo_quilombola_sidra", pd.Series(dtype=str)).dropna().head(1).iloc[0]) if "metodo_quilombola_sidra" in df.columns and not df["metodo_quilombola_sidra"].dropna().empty else None,
        "alerta": str(df["alerta_povos_tradicionais"].dropna().iloc[0]) if "alerta_povos_tradicionais" in df.columns and not df["alerta_povos_tradicionais"].dropna().empty else None,
        "fonte": "IBGE/SIDRA - Censo 2022: populações indígena e quilombola",
        "observacao": "Camada de equidade territorial agregada por município. Validar contra tabelas oficiais do IBGE antes de uso decisório final.",
    }


def _pni_dataset_url(ano: int = PNI_ANO_REFERENCIA) -> str:
    return f"https://dadosabertos.saude.gov.br/dataset/doses-aplicadas-pelo-programa-de-nacional-de-imunizacoes-pni-{ano}"


def _pni_headers() -> Dict[str, str]:
    return {
        "User-Agent": "SES-MT-Estudo-UBS/1.0",
        "Accept": "text/html,application/json,text/plain,*/*",
    }


def _pni_get_html(url: str, timeout: int = 80) -> str:
    """Baixa uma página pública do Portal de Dados Abertos SUS com fallback SSL.

    Alguns ambientes Windows/rede institucional apresentam problema de cadeia SSL
    em servidores públicos. O fallback é restrito a páginas públicas do domínio
    dadosabertos.saude.gov.br/apidadosabertos.saude.gov.br.
    """
    try:
        resp = requests.get(url, timeout=timeout, headers=_pni_headers(), allow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.SSLError:
        resp = requests.get(url, timeout=timeout, headers=_pni_headers(), allow_redirects=True, verify=False)
        resp.raise_for_status()
        return resp.text


def _pni_buscar_pacotes_ckan(ano: int = PNI_ANO_REFERENCIA) -> List[Dict[str, Any]]:
    """Descobre o dataset PNI lendo a página pública HTML, sem depender da API CKAN.

    O endpoint CKAN padrão (/api/3/action/package_search) não está ativo nesse
    portal. Por isso, a descoberta usa a página pública do dataset, que lista os
    recursos por ano/mês e os links de exploração.
    """
    url = _pni_dataset_url(ano)
    html = _pni_get_html(url, timeout=max(TIMEOUT_PADRAO, 80))
    titulo = f"Doses aplicadas pelo Programa de Nacional de Imunizações (PNI) - {ano}"
    if titulo.lower() not in html.lower() and f"pni) - {ano}" not in html.lower():
        raise ValueError(f"Página do dataset PNI {ano} acessada, mas o título esperado não foi identificado.")
    return [{
        "name": f"doses-aplicadas-pelo-programa-de-nacional-de-imunizacoes-pni-{ano}",
        "title": titulo,
        "url": url,
        "_score_pni": 100,
        "_consulta_pni": "HTML do dataset público",
    }]


def _pni_extraair_recursos_html_dataset(ano: int = PNI_ANO_REFERENCIA) -> List[Dict[str, Any]]:
    """Lê a página do dataset PNI e extrai links dos recursos 'Explorar'."""
    dataset_url = _pni_dataset_url(ano)
    html = _pni_get_html(dataset_url, timeout=max(TIMEOUT_PADRAO, 80))

    # Captura URLs de recursos publicadas no HTML.
    hrefs = re.findall(r'href=["\']([^"\']+/dataset/[^"\']+/resource/[^"\']+)["\']', html, flags=re.I)
    # O web/html renderizado também pode trazer links relativos.
    hrefs += re.findall(r'href=["\'](/dataset/[^"\']+/resource/[^"\']+)["\']', html, flags=re.I)
    recursos_urls = []
    vistos = set()
    for href in hrefs:
        url = urljoin(dataset_url, html_lib.unescape(href))
        if url not in vistos:
            vistos.add(url)
            recursos_urls.append(url)

    # Fallback: quando os links não aparecem por regex, usa UUIDs citados no HTML.
    if not recursos_urls:
        # Alguns HTMLs do portal apresentam apenas /resource/<uuid> ou resource/<uuid>.
        uuids = set(re.findall(r'(?:/resource/|resource/)([0-9a-fA-F-]{36})', html))
        for uuid in uuids:
            url = f"{dataset_url}/resource/{uuid}"
            if url not in vistos:
                vistos.add(url)
                recursos_urls.append(url)

    recursos: List[Dict[str, Any]] = []
    erros: List[str] = []
    for resource_url in recursos_urls[:80]:
        try:
            rhtml = _pni_get_html(resource_url, timeout=max(TIMEOUT_PADRAO, 80))
            titulo_match = re.search(r'<h1[^>]*>\s*([^<]+?)\s*</h1>', rhtml, flags=re.I | re.S)
            titulo = html_lib.unescape(titulo_match.group(1).strip()) if titulo_match else resource_url.rsplit('/', 1)[-1]
            formato_match = re.search(r'Formato\s*</[^>]+>\s*([^<\n]+)', rhtml, flags=re.I)
            formato = html_lib.unescape(formato_match.group(1).strip()) if formato_match else ""

            # A página de recurso traz uma linha do tipo URL: <a href="...">...</a>
            urls_publicas = re.findall(r'https?://[^\s"\'<>]+', rhtml)
            urls_publicas = [html_lib.unescape(u).rstrip('.,;)') for u in urls_publicas]
            # Prefere arquivos em S3/ZIP/CSV/JSON/XML; evita links do próprio portal e Swagger API.
            candidatos = []
            for u in urls_publicas:
                ul = u.lower()
                if "dadosabertos.saude.gov.br/dataset" in ul:
                    continue
                if "apidadosabertos.saude.gov.br/v1/#" in ul:
                    continue
                if any(x in ul for x in [".zip", ".csv", ".json", ".xml", "s3.sa-east-1.amazonaws.com"]):
                    candidatos.append(u)
            if not candidatos:
                # Guarda API/documentação só para transparência, mas com prioridade baixa.
                candidatos = [u for u in urls_publicas if "apidadosabertos.saude.gov.br" in u]
            for u in candidatos:
                recursos.append({
                    "nome": titulo,
                    "url": u,
                    "format": formato,
                    "package_id": f"pni-{ano}-html",
                    "package_title": f"Doses aplicadas pelo PNI - {ano}",
                    "resource_page": resource_url,
                    "descoberta": "HTML do dataset/recurso público",
                })
        except Exception as exc:
            erros.append(f"{resource_url}: {exc}")

    # Fallback oficial observado nas páginas reais dos recursos do portal.
    # Exemplo verificado na própria página do recurso 2025:
    # https://arquivosdadosabertos.saude.gov.br/dados/dbbni/vacinacao_jan_2025_csv.zip
    # O fallback anterior em S3 retornava 403; por isso foi substituído por este domínio.
    meses = [
        (12, "dez"), (11, "nov"), (10, "out"), (9, "set"), (8, "ago"), (7, "jul"),
        (6, "jun"), (5, "mai"), (4, "abr"), (3, "mar"), (2, "fev"), (1, "jan"),
    ]
    for _, abrev in meses:
        recursos.append({
            "nome": f"Vacinação - {abrev.upper()} {ano} (CSV - arquivosdadosabertos)",
            "url": f"https://arquivosdadosabertos.saude.gov.br/dados/dbbni/vacinacao_{abrev}_{ano}_csv.zip",
            "format": "CSV",
            "package_id": f"pni-{ano}-arquivosdadosabertos-fallback",
            "package_title": f"Doses aplicadas pelo PNI - {ano}",
            "resource_page": dataset_url,
            "descoberta": "Padrão oficial de arquivo identificado na página pública do recurso PNI",
        })

    # Remove duplicatas por URL.
    dedup = []
    vistos = set()
    for r in recursos:
        u = r.get("url")
        if not u or u in vistos:
            continue
        vistos.add(u)
        dedup.append(r)

    if not dedup:
        detalhe = " | ".join(erros[:5]) if erros else "nenhum link de recurso localizado"
        raise ValueError(f"Dataset PNI {ano} acessado, mas nenhum recurso foi extraído. {detalhe}")
    return dedup


def _pni_recurso_parece_util(r: Dict[str, Any], ano: int = PNI_ANO_REFERENCIA) -> bool:
    nome = str(r.get("nome") or r.get("name") or r.get("title") or "")
    url = str(r.get("url") or "")
    formato = str(r.get("format") or "").lower()
    texto = f"{nome} {url} {formato}".lower()
    if not url:
        return False
    if "dicion" in texto or "metad" in texto or "layout" in texto or "pdf" in texto:
        return False
    if "apidadosabertos.saude.gov.br/v1/#" in texto:
        return False
    if not (url.startswith("http://") or url.startswith("https://")):
        return False
    return any(x in texto for x in [".csv", ".zip", ".json", ".xml", "csv", "zip", "json", "xml"])


def _pni_recursos_csv(ano: int = PNI_ANO_REFERENCIA) -> List[Dict[str, Any]]:
    recursos = [r for r in _pni_extraair_recursos_html_dataset(ano) if _pni_recurso_parece_util(r, ano)]

    if not recursos:
        raise ValueError(f"PNI {ano}: dataset localizado, mas nenhum recurso CSV/ZIP/JSON/XML aproveitável foi identificado.")

    ordem_meses = {
        "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4, "maio": 5, "junho": 6,
        "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
        "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6, "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
    }

    def chave(r: Dict[str, Any]) -> tuple:
        nome = str(r.get("nome") or "").lower()
        url = str(r.get("url") or "").lower()
        formato = str(r.get("format") or "").lower()
        texto = f"{nome} {url} {formato}"
        mes = 0
        for m, n in ordem_meses.items():
            if re.search(rf"(^|[^a-z]){re.escape(m)}([^a-z]|$)", texto):
                mes = max(mes, n)
        # Prefere CSV, depois JSON, e meses mais recentes. Evita XML por ser mais pesado/verboso.
        tipo_score = 0
        if ".csv" in texto or "csv" in formato or "/csv/" in texto:
            tipo_score = 3
        elif ".json" in texto or "json" in formato or "/json/" in texto:
            tipo_score = 2
        elif ".zip" in texto:
            tipo_score = 1
        if "xml" in texto:
            tipo_score -= 1
        return (-tipo_score, -mes, nome)

    return sorted(recursos, key=chave)

def _pni_status_recurso(url: str, timeout: int = 60) -> Dict[str, Any]:
    """Verifica um recurso PNI sem baixar o arquivo inteiro."""
    headers = {"User-Agent": "SES-MT-Estudo-UBS/1.0"}
    try:
        resp = requests.get(url, timeout=timeout, headers=headers, stream=True, allow_redirects=True)
        status = {
            "status_code": resp.status_code,
            "ok": 200 <= resp.status_code < 400,
            "content_type": resp.headers.get("content-type", ""),
            "content_length": resp.headers.get("content-length", ""),
            "url_final": resp.url,
        }
        resp.close()
        return status
    except Exception as exc:
        return {"status_code": None, "ok": False, "erro": str(exc), "url_testada": url}


def testar_pni_doses_dadosabertos(ano: int = PNI_ANO_REFERENCIA) -> Dict[str, Any]:
    try:
        pacotes = _pni_buscar_pacotes_ckan(ano)
        recursos = _pni_recursos_csv(ano)
        if not recursos:
            return {"ok": False, "ano": ano, "erro": "Nenhum recurso CSV/ZIP/JSON de vacinação localizado no pacote PNI."}

        testes = []
        recurso_ok = None
        status_ok = None
        for recurso in recursos[:12]:
            status = _pni_status_recurso(recurso["url"], timeout=max(TIMEOUT_PADRAO, 60))
            testes.append({"recurso": recurso, "status": status})
            if status.get("ok"):
                recurso_ok = recurso
                status_ok = status
                break

        escolhido = recurso_ok or recursos[0]
        return {
            "ok": bool(recurso_ok),
            "ano": ano,
            "qtd_pacotes_candidatos": len(pacotes),
            "pacote_descoberto": {
                "name": escolhido.get("package_id"),
                "title": escolhido.get("package_title"),
            },
            "qtd_recursos_localizados": len(recursos),
            "recurso_teste": escolhido,
            "status_recurso_teste": status_ok or testes[0]["status"],
            "recursos_testados_amostra": testes[:5],
            "observacao": "PNI localizado por leitura da página pública do Dados Abertos SUS. O sistema agrega por município e processa o recurso/competência mais recente acessível.",
        }
    except Exception as exc:
        return {"ok": False, "ano": ano, "erro": str(exc)}

def _baixar_recurso_pni(url: str, nome: str, ano: int = PNI_ANO_REFERENCIA) -> Path:
    cache_dir = Path("data") / "cache" / "pni"
    cache_dir.mkdir(parents=True, exist_ok=True)
    ext = ".zip" if ".zip" in url.lower() or "zip" in nome.lower() else ".csv"
    nome_seguro = re.sub(r"[^a-zA-Z0-9_-]+", "_", nome or "pni")[:80]
    destino = cache_dir / f"pni_{ano}_{nome_seguro}{ext}"
    if destino.exists() and destino.stat().st_size > 1024:
        return destino
    headers = {"User-Agent": "SES-MT-Estudo-UBS/1.0", "Accept": "text/csv,application/zip,application/octet-stream,*/*"}
    resp = requests.get(url, timeout=max(TIMEOUT_PADRAO, 180), headers=headers, stream=True)
    resp.raise_for_status()
    with open(destino, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)
    if not destino.exists() or destino.stat().st_size == 0:
        raise ValueError("Recurso PNI baixado, mas arquivo ficou vazio.")
    return destino


def _abrir_csv_pni(path: Path) -> tuple[Any, str]:
    if path.suffix.lower() == ".zip":
        zf = zipfile.ZipFile(path, "r")
        candidatos = [n for n in zf.namelist() if n.lower().endswith((".csv", ".txt"))]
        if not candidatos:
            zf.close()
            raise ValueError("ZIP do PNI não contém CSV/TXT.")
        nome = sorted(candidatos, key=len)[0]
        return zf.open(nome), nome
    return open(path, "rb"), path.name


def _ler_chunks_pni(path: Path):
    ultimo_erro = None
    for enc in ["utf-8-sig", "utf-8", "latin1", "cp1252"]:
        for sep in [";", ","]:
            arquivo = None
            try:
                arquivo, nome = _abrir_csv_pni(path)
                leitor = pd.read_csv(
                    arquivo,
                    sep=sep,
                    encoding=enc,
                    dtype=str,
                    chunksize=150000,
                    low_memory=False,
                    on_bad_lines="skip",
                )
                primeiro = next(leitor)
                yield primeiro
                for chunk in leitor:
                    yield chunk
                if hasattr(arquivo, "close"):
                    arquivo.close()
                return
            except StopIteration:
                if arquivo and hasattr(arquivo, "close"):
                    arquivo.close()
                return
            except Exception as exc:
                ultimo_erro = exc
                try:
                    if arquivo and hasattr(arquivo, "close"):
                        arquivo.close()
                except Exception:
                    pass
                continue
    raise ValueError(f"Não foi possível ler CSV PNI {path.name}: {ultimo_erro}")


def _classificar_intensidade_vacinacao_pni(valor: Any) -> str:
    v = _parse_numero_sidra(valor)
    if v is None:
        return "Sem informação"
    if v >= 120:
        return "Alta intensidade recente de vacinação"
    if v >= 60:
        return "Média intensidade recente de vacinação"
    if v > 0:
        return "Baixa intensidade recente de vacinação"
    return "Sem doses identificadas na competência"


def carregar_pni_doses_mt(ano: int = PNI_ANO_REFERENCIA, max_recursos: int = PNI_MAX_RECURSOS_PROCESSAR) -> pd.DataFrame:
    recursos = _pni_recursos_csv(ano)
    if not recursos:
        raise ValueError(f"Nenhum recurso PNI encontrado para {ano}.")

    acumulados: List[pd.DataFrame] = []
    erros: List[str] = []
    recursos_processados = []

    for recurso in recursos[:max(1, int(max_recursos))]:
        nome = recurso.get("nome") or f"PNI {ano}"
        try:
            path = _baixar_recurso_pni(recurso["url"], nome, ano)
            partes: List[pd.DataFrame] = []
            for chunk in _ler_chunks_pni(path):
                col_codigo = _identificar_coluna(list(chunk.columns), [
                    "paciente_endereco_coibgemunicipio", "paciente_endereco_co_ibge_municipio", "paciente_endereco_codibgemunicipio",
                    "co_municipio", "codigo_municipio", "municipio_codigo", "id_municipio", "estabelecimento_municipio_codigo",
                    "co_mun_res", "cod_mun_res", "codigo_ibge",
                ])
                col_mun = _identificar_coluna(list(chunk.columns), [
                    "paciente_endereco_nmmunicipio", "paciente_endereco_nome_municipio", "municipio_nome", "nome_municipio",
                    "estabelecimento_municipio_nome", "no_municipio", "municipio",
                ])
                col_uf = _identificar_coluna(list(chunk.columns), [
                    "paciente_endereco_uf", "sg_uf", "uf", "estabelecimento_uf", "uf_estabelecimento",
                ])
                col_vacina = _identificar_coluna(list(chunk.columns), [
                    "vacina_nome", "imunobiologico", "no_imunobiologico", "vacina", "produto", "vacina_descricao",
                ])
                col_dose = _identificar_coluna(list(chunk.columns), [
                    "vacina_descricao_dose", "descricao_dose", "dose", "vacina_dose", "ds_dose",
                ])

                if not col_codigo and not col_uf:
                    continue
                trabalho = chunk.copy()
                if col_codigo:
                    trabalho["codigo_ibge"] = trabalho[col_codigo].astype(str).str.extract(r"(\d+)")[0].str[:7]
                    trabalho = trabalho[trabalho["codigo_ibge"].str.startswith(UF_MT, na=False)]
                elif col_uf:
                    trabalho = trabalho[trabalho[col_uf].astype(str).str.upper().str.strip().eq("MT")]
                    trabalho["codigo_ibge"] = None
                if trabalho.empty:
                    continue
                out = pd.DataFrame({
                    "codigo_ibge": trabalho["codigo_ibge"],
                    "municipio_pni": trabalho[col_mun] if col_mun else None,
                    "doses_pni_linha": 1,
                    "vacina_pni": trabalho[col_vacina].astype(str) if col_vacina else "Não identificada",
                    "dose_pni": trabalho[col_dose].astype(str) if col_dose else "Não identificada",
                })
                partes.append(out)
            if partes:
                df_recurso = pd.concat(partes, ignore_index=True)
                acumulados.append(df_recurso)
                recursos_processados.append(nome)
        except Exception as exc:
            erros.append(f"{nome}: {exc}")
            continue

    if not acumulados:
        raise ValueError("PNI acessado, mas nenhum registro de Mato Grosso foi consolidado. " + " | ".join(erros[:3]))

    df = pd.concat(acumulados, ignore_index=True)
    df = df[df["codigo_ibge"].notna() & df["codigo_ibge"].astype(str).str.startswith(UF_MT, na=False)].copy()
    if df.empty:
        raise ValueError("PNI lido, mas sem códigos IBGE de Mato Grosso após tratamento.")

    def lista_top(series: pd.Series, limite: int = 8) -> str:
        vals = series.dropna().astype(str)
        vals = vals[~vals.str.lower().isin(["nan", "none", "não identificada", "nao identificada", ""])].value_counts().head(limite).index.tolist()
        return "; ".join(vals)

    agg = df.groupby("codigo_ibge", as_index=False).agg(
        doses_pni_competencia=("doses_pni_linha", "sum"),
        imunobiologicos_distintos_pni=("vacina_pni", lambda s: int(s.dropna().astype(str).nunique())),
        vacinas_mais_frequentes_pni=("vacina_pni", lista_top),
        doses_pni_descricoes=("dose_pni", lista_top),
    )
    agg["ano_base_pni"] = int(ano)
    agg["competencias_processadas_pni"] = "; ".join(recursos_processados[:5])
    agg["fonte_pni"] = f"Dados Abertos SUS - Doses aplicadas pelo PNI {ano}"
    agg["observacao_pni"] = "Dados agregados por município. O sistema não exibe registros individualizados; processa a competência/recurso mais recente disponível do ano para manter a carga leve."
    return agg


def _pni_cache_path(ano: int = PNI_ANO_REFERENCIA) -> Path:
    cache_dir = Path("data") / "cache" / "pni"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"pni_{ano}_mt_consolidado.csv"


def pni_cache_status(ano: int = PNI_ANO_REFERENCIA) -> Dict[str, Any]:
    path = _pni_cache_path(ano)
    if not path.exists() or path.stat().st_size == 0:
        return {"existe": False, "ano": ano, "arquivo": str(path)}
    try:
        df = pd.read_csv(path, dtype={"codigo_ibge": str})
        return {
            "existe": True,
            "ano": ano,
            "arquivo": str(path),
            "linhas": int(len(df)),
            "tamanho_bytes": int(path.stat().st_size),
            "modificado_em": datetime.fromtimestamp(path.stat().st_mtime).strftime("%d/%m/%Y %H:%M:%S"),
            "doses_total": int(pd.to_numeric(df.get("doses_pni_competencia", 0), errors="coerce").fillna(0).sum()) if "doses_pni_competencia" in df.columns else None,
        }
    except Exception as exc:
        return {"existe": False, "ano": ano, "arquivo": str(path), "erro": str(exc)}


def carregar_pni_doses_cache(ano: int = PNI_ANO_REFERENCIA) -> pd.DataFrame:
    path = _pni_cache_path(ano)
    if not path.exists() or path.stat().st_size == 0:
        raise FileNotFoundError(
            f"Cache PNI não encontrado em {path}. Use primeiro o botão 'Processar PNI e salvar cache'."
        )
    df = pd.read_csv(path, dtype={"codigo_ibge": str})
    if "codigo_ibge" not in df.columns:
        raise ValueError("Cache PNI encontrado, mas sem coluna codigo_ibge.")
    df["codigo_ibge"] = df["codigo_ibge"].astype(str).str.extract(r"(\d+)")[0].str[:7]
    return df


def processar_pni_doses_e_salvar_cache(ano: int = PNI_ANO_REFERENCIA, max_recursos: int = PNI_MAX_RECURSOS_PROCESSAR) -> Dict[str, Any]:
    df = carregar_pni_doses_mt(ano, max_recursos)
    path = _pni_cache_path(ano)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return {
        "ok": True,
        "ano": ano,
        "arquivo": str(path),
        "linhas": int(len(df)),
        "tamanho_bytes": int(path.stat().st_size),
        "doses_total": int(pd.to_numeric(df.get("doses_pni_competencia", 0), errors="coerce").fillna(0).sum()) if "doses_pni_competencia" in df.columns else None,
        "observacao": "PNI processado uma vez e salvo em cache. A geração da base automática passará a ler este arquivo leve, sem reprocessar o ZIP nacional.",
    }

def render_mapa_tematico_ubs(base: pd.DataFrame) -> None:
    """Renderiza um mapa temático preliminar com a malha municipal do IBGE, se Plotly estiver disponível."""
    st.markdown("#### Mapa temático preliminar por município")
    st.caption(
        "Mapa gerado com a malha municipal simplificada do IBGE. Ele serve para leitura territorial preliminar "
        "e não substitui validação técnica da Coordenadoria APS ou dos municípios."
    )

    opcoes = [
        ("Pontuação automática UBS", "pontuacao_automatica_ubs"),
        ("eSF necessárias conforme MS", "esf_necessarias_ms"),
        ("População estimada IBGE", "populacao_ibge"),
        ("População por UBS CNES automática", "populacao_por_ubs_cnes_automatico"),
        ("Percentual rural Censo 2022", "percentual_rural_2022"),
        ("Área territorial km²", "area_km2"),
        ("Quantidade de distritos IBGE", "qtd_distritos_ibge"),
    ]
    opcoes = [(rotulo, coluna) for rotulo, coluna in opcoes if coluna in base.columns]
    if not opcoes:
        st.info("A base ainda não possui colunas numéricas suficientes para gerar mapa temático.")
        return

    rotulos = [item[0] for item in opcoes]
    escolha = st.selectbox("Indicador do mapa", rotulos, index=0)
    coluna = dict(opcoes)[escolha]

    try:
        import plotly.express as px  # type: ignore
    except Exception:
        st.warning(
            "O Plotly não está disponível neste ambiente. Para ativar o mapa, instale com: pip install plotly"
        )
        return

    try:
        geojson = carregar_malha_municipal_ibge_mt()
        featureidkey = _detectar_chave_geojson_municipio(geojson)
        mapa_df = base.copy()
        mapa_df["codigo_ibge"] = mapa_df["codigo_ibge"].astype(str)
        mapa_df[coluna] = pd.to_numeric(mapa_df[coluna], errors="coerce")
        mapa_df = mapa_df[mapa_df[coluna].notna()].copy()
        if mapa_df.empty:
            st.info("Não há valores válidos para o indicador selecionado.")
            return

        hover_cols = [
            col for col in [
                "municipio",
                "regiao_saude_sus",
                "macrorregiao_saude_sus",
                "populacao_ibge",
                "esf_necessarias_ms",
                "qtd_ubs_cnes_automatico",
                "populacao_por_ubs_cnes_automatico",
                "percentual_rural_2022",
                "qtd_distritos_ibge",
                "classificacao_automatica_ubs",
            ]
            if col in mapa_df.columns
        ]

        fig = px.choropleth_mapbox(
            mapa_df,
            geojson=geojson,
            locations="codigo_ibge",
            featureidkey=featureidkey,
            color=coluna,
            hover_name="municipio" if "municipio" in mapa_df.columns else None,
            hover_data=hover_cols,
            mapbox_style="carto-positron",
            center={"lat": -12.6, "lon": -55.8},
            zoom=4.2,
            opacity=0.65,
            height=620,
        )
        fig.update_layout(margin={"r": 0, "t": 0, "l": 0, "b": 0})
        st.plotly_chart(fig, use_container_width=True)

        st.download_button(
            "Baixar malha municipal IBGE/MT em GeoJSON",
            data=json.dumps(geojson).encode("utf-8"),
            file_name="malha_municipal_ibge_mt.geojson",
            mime="application/geo+json",
            use_container_width=True,
        )
    except Exception as exc:
        st.warning(f"Não foi possível renderizar o mapa temático com a malha IBGE: {exc}")


def testar_endpoint(nome: str, url: str) -> Dict[str, Any]:
    try:
        status = _request_status(url)
        return {
            "fonte": nome,
            "resultado": "OK" if status["ok"] else "Falhou",
            "status_http": status["status_code"],
            "tipo_conteudo": status["content_type"],
            "tamanho_kb": round(status["tamanho_bytes"] / 1024, 1),
        }
    except Exception as exc:
        return {
            "fonte": nome,
            "resultado": "Erro",
            "status_http": "-",
            "tipo_conteudo": "-",
            "tamanho_kb": "-",
            "mensagem": str(exc),
        }


def render_conectores_apis_ubs():
    st.subheader("Conectores e APIs para o Estudo de Necessidade de UBS")

    st.markdown(
        """
        <div class="info-box">
        Esta tela concentra as integrações mais seguras para automatizar o estudo de necessidade de construção de UBS.
        A prioridade, nesta fase, é usar fontes públicas oficiais e estáveis. Dados que dependem de validação local, como eSF existentes,
        terreno e infraestrutura, continuam sob responsabilidade da Coordenadoria APS ou da futura etapa de adesão municipal.
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.info(
        "Atenção metodológica: a população usada no cálculo do Ministério da Saúde continua sendo a "
        "estimativa IBGE/SIDRA do ano selecionado, por exemplo 2025. Os dados urbano/rural são do "
        "Censo 2022 e entram apenas como indicador estrutural de ruralidade, sem substituir a população "
        "de referência do cálculo."
    )

    st.markdown("### 1. Mapa das fontes")
    st.dataframe(pd.DataFrame(FONTES_PLANEJADAS), use_container_width=True, hide_index=True)

    st.markdown("### 2. Testes rápidos de conectividade")
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        if st.button("Testar IBGE Localidades", use_container_width=True):
            try:
                df_mun = carregar_municipios_ibge_mt()
                st.session_state["ubs_api_municipios"] = df_mun
                st.success(f"IBGE Localidades OK: {len(df_mun)} municípios carregados.")
            except Exception as exc:
                st.error(f"Falha ao testar IBGE Localidades: {exc}")

    with col2:
        if st.button("Testar distritos IBGE", use_container_width=True):
            try:
                df_dist = carregar_distritos_ibge_mt()
                st.session_state["ubs_api_distritos"] = df_dist
                municipios_com_distrito = df_dist["codigo_ibge"].nunique()
                st.success(f"Distritos IBGE OK: {len(df_dist)} distritos em {municipios_com_distrito} municípios de MT.")
            except Exception as exc:
                st.error(f"Falha ao testar distritos IBGE: {exc}")

    with col3:
        ano = st.number_input("Ano SIDRA", min_value=2024, max_value=2026, value=2025, step=1)
        if st.button("Testar população SIDRA", use_container_width=True):
            try:
                df_pop = carregar_populacao_sidra_mt(int(ano))
                st.session_state["ubs_api_populacao"] = df_pop
                st.success(f"SIDRA OK: {len(df_pop)} registros carregados para {ano}.")
            except Exception as exc:
                st.error(f"Falha ao testar população SIDRA: {exc}")

    with col4:
        if st.button("Testar área/densidade IBGE", use_container_width=True):
            try:
                df_area = carregar_area_densidade_sidra_mt()
                st.session_state["ubs_api_area_densidade"] = df_area
                campos_ok = df_area[["area_km2", "densidade_demografica"]].notna().sum().to_dict()
                st.success(f"SIDRA 1301 OK: {len(df_area)} municípios lidos. Área: {campos_ok.get('area_km2', 0)} | Densidade: {campos_ok.get('densidade_demografica', 0)}")
            except Exception as exc:
                st.error(f"Falha ao testar área/densidade SIDRA 1301: {exc}")

    col5, col6, col7, col8 = st.columns(4)

    with col5:
        if st.button("Testar perfil urbano/rural (Censo 2022)", use_container_width=True):
            try:
                df_rural = carregar_urbano_rural_sidra_mt(2022)
                st.session_state["ubs_api_urbano_rural"] = df_rural
                campos_ok = df_rural[["populacao_urbana_2022", "populacao_rural_2022", "percentual_rural_2022"]].notna().sum().to_dict()
                st.success(
                    f"SIDRA 9923 OK: {len(df_rural)} municípios lidos com perfil urbano/rural do Censo 2022. "
                    f"Urbana: {campos_ok.get('populacao_urbana_2022', 0)} | "
                    f"Rural: {campos_ok.get('populacao_rural_2022', 0)}"
                )
            except Exception as exc:
                st.error(f"Falha ao testar urbano/rural SIDRA 9923: {exc}")

    with col6:
        if st.button("Testar malha IBGE", use_container_width=True):
            try:
                status = testar_malha_mt()
                if status.get("ok"):
                    st.success(f"Malha IBGE OK: {round(status['tamanho_bytes'] / 1024, 1)} KB recebidos.")
                else:
                    st.warning(f"Malha IBGE respondeu HTTP {status.get('status_code')}.")
                st.json(status)
            except Exception as exc:
                st.error(f"Falha ao testar malha IBGE: {exc}")

    with col7:
        if st.button("Testar regiões de saúde SUS", use_container_width=True):
            try:
                df_regioes = carregar_regioes_saude_dadosabertos_mt()
                st.session_state["ubs_api_regioes_saude"] = df_regioes
                qtd_regioes = df_regioes["regiao_saude_sus"].dropna().nunique() if "regiao_saude_sus" in df_regioes.columns else 0
                qtd_macros = df_regioes["macrorregiao_saude_sus"].dropna().nunique() if "macrorregiao_saude_sus" in df_regioes.columns else 0
                st.success(f"Regiões de Saúde OK: {len(df_regioes)} municípios | {qtd_regioes} regiões | {qtd_macros} macrorregiões.")
            except Exception as exc:
                st.error(f"Falha ao testar regiões de saúde: {exc}")

    with col8:
        if st.button("Testar CNES UBS", use_container_width=True):
            try:
                status = testar_cnes_estabelecimentos_dadosabertos()
                if status.get("ok"):
                    st.success(f"CNES Dados Abertos OK: {round(status['tamanho_bytes'] / (1024 * 1024), 2)} MB disponíveis.")
                else:
                    st.warning(f"CNES respondeu HTTP {status.get('status_code')}.")
                st.json(status)
            except Exception as exc:
                st.error(f"Falha ao testar CNES Dados Abertos: {exc}")

    col9, col10, col11, col12 = st.columns(4)
    with col9:
        if st.button("Testar Hospitais e Leitos", use_container_width=True):
            try:
                status = testar_leitos_sus_dadosabertos()
                if status.get("ok"):
                    st.success(f"Hospitais e Leitos OK: {round(status['tamanho_bytes'] / (1024 * 1024), 2)} MB disponíveis.")
                else:
                    st.warning(f"Hospitais e Leitos respondeu HTTP {status.get('status_code')}.")
                st.json(status)
            except Exception as exc:
                st.error(f"Falha ao testar Hospitais e Leitos: {exc}")

    with col10:
        if st.button("Testar envelhecimento IBGE", use_container_width=True):
            try:
                df_demo = carregar_indicadores_demograficos_sidra_mt(2022)
                st.session_state["ubs_api_demografia_9515"] = df_demo
                campos_ok = df_demo[["indice_envelhecimento_2022", "idade_mediana_2022", "razao_sexo_2022"]].notna().sum().to_dict()
                st.success(
                    f"SIDRA 9515 OK: {len(df_demo)} municípios lidos. "
                    f"Índice envelhecimento: {campos_ok.get('indice_envelhecimento_2022', 0)} | "
                    f"Idade mediana: {campos_ok.get('idade_mediana_2022', 0)} | "
                    f"Razão de sexo: {campos_ok.get('razao_sexo_2022', 0)}"
                )
            except Exception as exc:
                st.error(f"Falha ao testar indicadores demográficos SIDRA 9515: {exc}")

    with col11:
        if st.button("Testar alfabetização IBGE", use_container_width=True):
            try:
                df_alf = carregar_alfabetizacao_sidra_mt(2022)
                st.session_state["ubs_api_alfabetizacao_9543"] = df_alf
                st.success(
                    f"SIDRA 9543 OK: {len(df_alf)} municípios lidos. "
                    f"Taxa média de alfabetização: {_media_numerica_coluna(df_alf, 'taxa_alfabetizacao_15mais_2022'):.2f}%"
                )
                st.dataframe(df_alf.head(20), use_container_width=True, hide_index=True)
            except Exception as exc:
                st.error(f"Falha ao testar alfabetização SIDRA 9543: {exc}")

    with col12:
        if st.button("Testar regiões IBGE", use_container_width=True):
            try:
                df_geo = carregar_regioes_geograficas_ibge_mt()
                st.session_state["ubs_api_regioes_geograficas_ibge"] = df_geo
                qtd_imediatas = df_geo["regiao_imediata_ibge"].dropna().nunique() if "regiao_imediata_ibge" in df_geo.columns else 0
                qtd_intermediarias = df_geo["regiao_intermediaria_ibge"].dropna().nunique() if "regiao_intermediaria_ibge" in df_geo.columns else 0
                st.success(
                    f"Regiões geográficas IBGE OK: {len(df_geo)} municípios | "
                    f"{qtd_imediatas} regiões imediatas | {qtd_intermediarias} regiões intermediárias."
                )
            except Exception as exc:
                st.error(f"Falha ao testar regiões geográficas IBGE: {exc}")

    col12, col13, col14, col15 = st.columns(4)
    with col12:
        if st.button("Testar SINASC", use_container_width=True):
            try:
                status = testar_sinasc_dadosabertos(SINASC_ANO_REFERENCIA)
                if status.get("ok"):
                    st.success(f"SINASC {SINASC_ANO_REFERENCIA} OK: {round(status['tamanho_bytes'] / (1024 * 1024), 2)} MB disponíveis.")
                else:
                    st.warning(f"SINASC respondeu HTTP {status.get('status_code')}.")
                st.json(status)
            except Exception as exc:
                st.error(f"Falha ao testar SINASC: {exc}")
    with col13:
        if st.button("Testar SIM", use_container_width=True):
            try:
                status = testar_sim_dadosabertos(SIM_ANO_REFERENCIA)
                if status.get("ok"):
                    tamanho = status.get("tamanho_bytes_aproximado")
                    if tamanho:
                        st.success(f"SIM {SIM_ANO_REFERENCIA} OK: {round(tamanho / (1024 * 1024), 2)} MB disponíveis.")
                    else:
                        st.success(f"SIM {SIM_ANO_REFERENCIA} OK: endpoint funcional identificado.")
                else:
                    st.warning("Não foi possível confirmar endpoint funcional do SIM neste momento.")
                st.json(status)
            except Exception as exc:
                st.error(f"Falha ao testar SIM: {exc}")

    with col14:
        if st.button("Testar saneamento IBGE", use_container_width=True):
            try:
                df_san = carregar_saneamento_sidra_mt(2022)
                st.session_state["ubs_api_saneamento"] = df_san
                st.success(
                    f"Saneamento IBGE OK: {len(df_san)} municípios. "
                    f"Índice médio de vulnerabilidade: {_media_numerica_coluna(df_san, 'indice_vulnerabilidade_saneamento_2022'):.2f}"
                )
                st.dataframe(df_san.head(20), use_container_width=True, hide_index=True)
            except Exception as exc:
                st.error(f"Falha ao testar saneamento IBGE: {exc}")

    with col15:
        if st.button("Testar nível de instrução IBGE", use_container_width=True):
            try:
                df_inst = carregar_nivel_instrucao_sidra_mt(2022)
                st.session_state["ubs_api_instrucao_10061"] = df_inst
                media_baixa = _media_numerica_coluna(df_inst, "pct_sem_instrucao_fund_incompleto_25mais_2022")
                st.success(
                    f"SIDRA 10061 OK: {len(df_inst)} municípios. "
                    f"Baixa instrução média: {media_baixa:.2f}%" if pd.notna(media_baixa) else f"SIDRA 10061 OK: {len(df_inst)} municípios."
                )
                st.dataframe(df_inst.head(20), use_container_width=True, hide_index=True)
            except Exception as exc:
                st.error(f"Falha ao testar nível de instrução SIDRA 10061: {exc}")


    col16, col17, col18, col19 = st.columns(4)
    with col16:
        if st.button("Testar INEP Censo Escolar", use_container_width=True):
            try:
                status = testar_inep_censo_escolar(INEP_CENSO_ESCOLAR_ANO)
                if status.get("ok"):
                    st.success(f"INEP Censo Escolar {INEP_CENSO_ESCOLAR_ANO} OK: microdados disponíveis para carga agregada municipal.")
                else:
                    st.warning("Não foi possível confirmar o ZIP do Censo Escolar/INEP neste momento.")
                st.json(status)
            except Exception as exc:
                st.error(f"Falha ao testar INEP Censo Escolar: {exc}")

    with col17:
        if st.button("Testar povos tradicionais IBGE", use_container_width=True):
            try:
                status = testar_povos_tradicionais_ibge(2022)
                if status.get("ok"):
                    st.success(
                        f"Povos tradicionais IBGE OK: {status.get('municipios_com_populacao_indigena', 0)} municípios com população indígena "
                        f"e {status.get('municipios_com_populacao_quilombola', 0)} com população quilombola."
                    )
                else:
                    st.warning("Fonte IBGE acessada, mas sem população indígena/quilombola consolidada para MT.")
                st.json(status)
            except Exception as exc:
                st.error(f"Falha ao testar povos tradicionais IBGE: {exc}")



    with col18:
        if st.button("Testar renda IBGE", use_container_width=True):
            try:
                status = testar_renda_ibge(2022)
                if status.get("ok"):
                    valor_rdpc = status.get("rdpc_medio_mt") or 0
                    st.success(
                        f"Renda IBGE OK: {status.get('municipios_com_rdpc', 0)} municípios com RDPC. "
                        f"RDPC médio MT: R$ {valor_rdpc:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                    )
                else:
                    st.warning("Fonte IBGE acessada, mas sem renda consolidada para MT.")
                st.json(status)
            except Exception as exc:
                st.error(f"Falha ao testar renda IBGE: {exc}")

    with col19:
        if st.button("Testar deficiência/autismo IBGE", use_container_width=True):
            try:
                status = testar_deficiencia_autismo_ibge(2022)
                if status.get("ok"):
                    st.success(
                        f"IBGE deficiência/TEA OK: {status.get('municipios_com_pessoas_com_deficiencia', 0)} municípios com pessoas com deficiência "
                        f"e {status.get('municipios_com_pessoas_diagnosticadas_autismo', 0)} com pessoas diagnosticadas com autismo."
                    )
                else:
                    st.warning("Fonte IBGE acessada, mas sem dados consolidados de deficiência/autismo para MT.")
                st.json(status)
            except Exception as exc:
                st.error(f"Falha ao testar deficiência/autismo IBGE: {exc}")


    col20, col21 = st.columns(2)
    with col20:
        if st.button("Testar BPC Portal da Transparência", use_container_width=True):
            try:
                status = testar_bpc_portal_transparencia()
                if status.get("ok"):
                    st.success(
                        f"BPC OK: competência {status.get('competencia')} com {status.get('linhas', 0):,} registros lidos.".replace(",", ".")
                    )
                else:
                    st.warning("Arquivo BPC acessado, mas sem confirmação de leitura.")
                st.json(status)
            except Exception as exc:
                st.error(f"Falha ao testar BPC Portal da Transparência: {exc}")

    with col21:
        if st.button("Carregar BPC MT agregado", use_container_width=True):
            try:
                bpc = carregar_bpc_portal_transparencia_mt()
                st.session_state["ubs_api_bpc"] = bpc
                total_bpc = int(pd.to_numeric(bpc.get("bpc_total_qtd"), errors="coerce").fillna(0).sum())
                total_idoso = int(pd.to_numeric(bpc.get("bpc_idoso_qtd"), errors="coerce").fillna(0).sum())
                total_pcd = int(pd.to_numeric(bpc.get("bpc_pcd_qtd"), errors="coerce").fillna(0).sum())
                competencia_bpc = bpc["competencia_bpc"].dropna().iloc[0] if "competencia_bpc" in bpc.columns and bpc["competencia_bpc"].notna().any() else "não identificada"
                st.success(f"BPC MT agregado: {total_bpc:,} benefícios | Idoso: {total_idoso:,} | PCD: {total_pcd:,} | competência {competencia_bpc}".replace(",", "."))
                st.dataframe(bpc.head(30), use_container_width=True, hide_index=True)
            except Exception as exc:
                st.error(f"Falha ao carregar BPC MT agregado: {exc}")


    if st.button("Testar educação especial/AEE INEP", use_container_width=True):
        try:
            status = testar_educacao_especial_inep(INEP_CENSO_ESCOLAR_ANO)
            if status.get("ok"):
                st.success(
                    f"INEP Educação Especial/AEE {INEP_CENSO_ESCOLAR_ANO} OK: "
                    f"{status.get('matriculas_educacao_especial_total_mt', 0):,} matrículas de educação especial em MT.".replace(",", ".")
                )
            else:
                st.warning("Fonte INEP acessada, mas sem matrícula de educação especial consolidada para MT.")
            st.json(status)
        except Exception as exc:
            st.error(f"Falha ao testar educação especial/AEE INEP: {exc}")

    st.markdown("### 3. Base automática IBGE para o módulo UBS")
    st.caption("Carrega municípios + população estimada do ano selecionado e calcula o parâmetro MS de eSF. Dados do Censo 2022 entram apenas como perfil territorial/ruralidade.")

    if st.button("Gerar base automática IBGE + cálculo MS", type="primary", use_container_width=True):
        try:
            municipios = carregar_municipios_ibge_mt()
            populacao = carregar_populacao_sidra_mt(int(ano))
            base = populacao.merge(
                municipios[["codigo_ibge", "microrregiao_ibge", "mesorregiao_ibge", "uf"]],
                on="codigo_ibge",
                how="left",
            )
            try:
                regioes_geograficas = carregar_regioes_geograficas_ibge_mt()
                base = base.merge(
                    regioes_geograficas[[
                        "codigo_ibge",
                        "codigo_regiao_imediata_ibge",
                        "regiao_imediata_ibge",
                        "codigo_regiao_intermediaria_ibge",
                        "regiao_intermediaria_ibge",
                        "fonte_regioes_geograficas_ibge",
                    ]],
                    on="codigo_ibge",
                    how="left",
                )
            except Exception as exc_geo:
                base["codigo_regiao_imediata_ibge"] = None
                base["regiao_imediata_ibge"] = None
                base["codigo_regiao_intermediaria_ibge"] = None
                base["regiao_intermediaria_ibge"] = None
                base["alerta_regioes_geograficas_ibge"] = f"Não foi possível carregar regiões geográficas IBGE: {exc_geo}"
            try:
                distritos = carregar_distritos_ibge_mt()
                distritos_consolidados = consolidar_distritos_por_municipio(distritos)
                base = base.merge(
                    distritos_consolidados[[
                        "codigo_ibge",
                        "qtd_distritos_ibge",
                        "distritos_ibge",
                        "classificacao_distritos_preliminar",
                        "fonte_distritos",
                    ]],
                    on="codigo_ibge",
                    how="left",
                )
            except Exception as exc_distritos:
                base["qtd_distritos_ibge"] = None
                base["distritos_ibge"] = None
                base["classificacao_distritos_preliminar"] = "Sem distritos IBGE"
                base["alerta_distritos_ibge"] = f"Não foi possível carregar distritos IBGE: {exc_distritos}"

            try:
                regioes_saude = carregar_regioes_saude_dadosabertos_mt()
                base = base.merge(
                    regioes_saude[[
                        "codigo_ibge",
                        "codigo_regiao_saude",
                        "regiao_saude_sus",
                        "codigo_macrorregiao_saude",
                        "macrorregiao_saude_sus",
                        "fonte_regiao_saude",
                    ]],
                    on="codigo_ibge",
                    how="left",
                )
            except Exception as exc_regiao:
                base["codigo_regiao_saude"] = None
                base["regiao_saude_sus"] = None
                base["codigo_macrorregiao_saude"] = None
                base["macrorregiao_saude_sus"] = None
                base["alerta_regiao_saude"] = f"Não foi possível carregar regiões de saúde SUS: {exc_regiao}"
            try:
                area_densidade = carregar_area_densidade_sidra_mt()
                base = base.merge(
                    area_densidade[["codigo_ibge", "area_km2", "densidade_demografica", "fonte_area_densidade"]],
                    on="codigo_ibge",
                    how="left",
                )
                base["densidade_calculada_atual"] = base.apply(
                    lambda linha: round(linha["populacao_ibge"] / linha["area_km2"], 2)
                    if pd.notna(linha.get("area_km2")) and linha.get("area_km2", 0) else None,
                    axis=1,
                )
                base["classificacao_territorial_preliminar"] = base["densidade_calculada_atual"].apply(classificar_densidade)
            except Exception as exc_area:
                base["area_km2"] = None
                base["densidade_demografica"] = None
                base["densidade_calculada_atual"] = None
                base["classificacao_territorial_preliminar"] = "Sem área/densidade"
                base["alerta_area_densidade"] = f"Não foi possível carregar SIDRA 1301: {exc_area}"
            try:
                urbano_rural = carregar_urbano_rural_sidra_mt(2022)
                base = base.merge(
                    urbano_rural[[
                        "codigo_ibge",
                        "populacao_total_censo_2022",
                        "populacao_urbana_2022",
                        "populacao_rural_2022",
                        "percentual_rural_2022",
                        "classificacao_ruralidade_preliminar",
                        "fonte_urbano_rural",
                        "ano_base_ruralidade",
                    ]],
                    on="codigo_ibge",
                    how="left",
                )
            except Exception as exc_rural:
                base["populacao_total_censo_2022"] = None
                base["populacao_urbana_2022"] = None
                base["populacao_rural_2022"] = None
                base["percentual_rural_2022"] = None
                base["classificacao_ruralidade_preliminar"] = "Sem urbano/rural"
                base["ano_base_ruralidade"] = None
                base["alerta_urbano_rural"] = f"Não foi possível carregar SIDRA 9923: {exc_rural}"

            try:
                demografia = carregar_indicadores_demograficos_sidra_mt(2022)
                base = base.merge(
                    demografia[[
                        "codigo_ibge",
                        "indice_envelhecimento_2022",
                        "idade_mediana_2022",
                        "razao_sexo_2022",
                        "classificacao_envelhecimento_preliminar",
                        "ano_base_demografia",
                        "fonte_demografia",
                    ]],
                    on="codigo_ibge",
                    how="left",
                )
            except Exception as exc_demo:
                base["indice_envelhecimento_2022"] = None
                base["idade_mediana_2022"] = None
                base["razao_sexo_2022"] = None
                base["classificacao_envelhecimento_preliminar"] = "Sem informação"
                base["alerta_demografia"] = f"Não foi possível carregar indicadores demográficos SIDRA 9515: {exc_demo}"

            try:
                alfabetizacao = carregar_alfabetizacao_sidra_mt(2022)
                base = base.merge(
                    alfabetizacao[[
                        "codigo_ibge",
                        "taxa_alfabetizacao_15mais_2022",
                        "taxa_analfabetismo_15mais_2022",
                        "classificacao_vulnerabilidade_educacional",
                        "ano_base_alfabetizacao",
                        "fonte_alfabetizacao",
                    ]],
                    on="codigo_ibge",
                    how="left",
                )
            except Exception as exc_alf:
                base["taxa_alfabetizacao_15mais_2022"] = None
                base["taxa_analfabetismo_15mais_2022"] = None
                base["classificacao_vulnerabilidade_educacional"] = "Sem informação"
                base["ano_base_alfabetizacao"] = None
                base["alerta_alfabetizacao"] = f"Não foi possível carregar alfabetização SIDRA 9543: {exc_alf}"

            try:
                instrucao = carregar_nivel_instrucao_sidra_mt(2022)
                base = base.merge(
                    instrucao[[
                        "codigo_ibge",
                        "pct_sem_instrucao_fund_incompleto_25mais_2022",
                        "pct_medio_completo_ou_mais_25mais_2022",
                        "indice_vulnerabilidade_instrucao_2022",
                        "classificacao_vulnerabilidade_instrucao",
                        "ano_base_instrucao",
                        "fonte_instrucao",
                    ]],
                    on="codigo_ibge",
                    how="left",
                )
            except Exception as exc_inst:
                base["pct_sem_instrucao_fund_incompleto_25mais_2022"] = None
                base["pct_medio_completo_ou_mais_25mais_2022"] = None
                base["indice_vulnerabilidade_instrucao_2022"] = None
                base["classificacao_vulnerabilidade_instrucao"] = "Sem informação"
                base["ano_base_instrucao"] = None
                base["alerta_instrucao"] = f"Não foi possível carregar nível de instrução SIDRA 10061: {exc_inst}"

            try:
                renda = carregar_renda_censo_sidra_mt(2022)
                st.session_state["ubs_api_renda_ibge"] = renda
                base = base.merge(
                    renda[[
                        "codigo_ibge",
                        "rendimento_domiciliar_per_capita_medio_2022",
                        "pct_rdpc_ate_1_4_sm_2022",
                        "pct_rdpc_ate_1_2_sm_2022",
                        "classificacao_vulnerabilidade_renda",
                        "ano_base_renda",
                        "fonte_renda",
                        "observacao_renda",
                    ]],
                    on="codigo_ibge",
                    how="left",
                )
            except Exception as exc_renda:
                base["rendimento_domiciliar_per_capita_medio_2022"] = None
                base["pct_rdpc_ate_1_4_sm_2022"] = None
                base["pct_rdpc_ate_1_2_sm_2022"] = None
                base["classificacao_vulnerabilidade_renda"] = "Sem informação"
                base["ano_base_renda"] = None
                base["alerta_renda"] = f"Não foi possível carregar renda IBGE/SIDRA 10295/10296: {exc_renda}"

            try:
                defaut = carregar_deficiencia_autismo_ibge_mt(2022)
                st.session_state["ubs_api_deficiencia_autismo_ibge"] = defaut
                base = base.merge(
                    defaut[[
                        "codigo_ibge",
                        "pessoas_com_deficiencia_2022",
                        "populacao_2mais_referencia_deficiencia_2022",
                        "pct_pessoas_com_deficiencia_2022",
                        "pessoas_diagnosticadas_autismo_2022",
                        "populacao_referencia_autismo_2022",
                        "pct_pessoas_diagnosticadas_autismo_2022",
                        "classificacao_pressao_deficiencia_autismo",
                        "ano_base_deficiencia_autismo",
                        "fonte_deficiencia_autismo",
                        "observacao_deficiencia_autismo",
                    ]],
                    on="codigo_ibge",
                    how="left",
                )
            except Exception as exc_defaut:
                base["pessoas_com_deficiencia_2022"] = None
                base["populacao_2mais_referencia_deficiencia_2022"] = None
                base["pct_pessoas_com_deficiencia_2022"] = None
                base["pessoas_diagnosticadas_autismo_2022"] = None
                base["populacao_referencia_autismo_2022"] = None
                base["pct_pessoas_diagnosticadas_autismo_2022"] = None
                base["classificacao_pressao_deficiencia_autismo"] = "Sem informação"
                base["ano_base_deficiencia_autismo"] = 2022
                base["alerta_deficiencia_autismo"] = f"Não foi possível carregar deficiência/autismo IBGE/SIDRA: {exc_defaut}"

            try:
                bpc = carregar_bpc_portal_transparencia_mt()
                st.session_state["ubs_api_bpc"] = bpc
                base = base.merge(
                    bpc[[
                        "codigo_ibge",
                        "bpc_total_qtd",
                        "bpc_idoso_qtd",
                        "bpc_pcd_qtd",
                        "bpc_tipo_nao_identificado_qtd",
                        "bpc_valor_total_mes",
                        "competencia_bpc",
                        "fonte_bpc",
                        "observacao_bpc",
                    ]],
                    on="codigo_ibge",
                    how="left",
                )
                for col_bpc_int in ["bpc_total_qtd", "bpc_idoso_qtd", "bpc_pcd_qtd", "bpc_tipo_nao_identificado_qtd"]:
                    if col_bpc_int in base.columns:
                        base[col_bpc_int] = pd.to_numeric(base[col_bpc_int], errors="coerce").fillna(0).astype(int)
                if "bpc_valor_total_mes" in base.columns:
                    base["bpc_valor_total_mes"] = pd.to_numeric(base["bpc_valor_total_mes"], errors="coerce").fillna(0.0)
                base["bpc_total_por_1000_hab"] = base.apply(
                    lambda linha: round((linha.get("bpc_total_qtd", 0) / linha["populacao_ibge"]) * 1000, 2)
                    if linha.get("populacao_ibge", 0) else None,
                    axis=1,
                )
                base["bpc_idoso_por_1000_hab"] = base.apply(
                    lambda linha: round((linha.get("bpc_idoso_qtd", 0) / linha["populacao_ibge"]) * 1000, 2)
                    if linha.get("populacao_ibge", 0) else None,
                    axis=1,
                )
                base["bpc_pcd_por_1000_hab"] = base.apply(
                    lambda linha: round((linha.get("bpc_pcd_qtd", 0) / linha["populacao_ibge"]) * 1000, 2)
                    if linha.get("populacao_ibge", 0) else None,
                    axis=1,
                )
                base["classificacao_pressao_bpc"] = base["bpc_total_por_1000_hab"].apply(classificar_pressao_bpc)
            except Exception as exc_bpc:
                base["bpc_total_qtd"] = None
                base["bpc_idoso_qtd"] = None
                base["bpc_pcd_qtd"] = None
                base["bpc_tipo_nao_identificado_qtd"] = None
                base["bpc_valor_total_mes"] = None
                base["bpc_total_por_1000_hab"] = None
                base["bpc_idoso_por_1000_hab"] = None
                base["bpc_pcd_por_1000_hab"] = None
                base["classificacao_pressao_bpc"] = "Sem leitura automática BPC"
                base["competencia_bpc"] = None
                base["alerta_bpc"] = f"Não foi possível carregar BPC/Portal da Transparência: {exc_bpc}"

            try:
                saneamento = carregar_saneamento_sidra_mt(2022)
                base = base.merge(
                    saneamento[[
                        "codigo_ibge",
                        "pct_agua_rede_geral_2022",
                        "pct_agua_canalizacao_interna_2022",
                        "pct_banheiro_exclusivo_2022",
                        "pct_esgoto_rede_geral_ou_fossa_2022",
                        "pct_lixo_coletado_2022",
                        "indice_vulnerabilidade_saneamento_2022",
                        "classificacao_vulnerabilidade_saneamento",
                        "ano_base_saneamento",
                        "fonte_saneamento",
                    ]],
                    on="codigo_ibge",
                    how="left",
                )
            except Exception as exc_san:
                base["pct_agua_rede_geral_2022"] = None
                base["pct_agua_canalizacao_interna_2022"] = None
                base["pct_banheiro_exclusivo_2022"] = None
                base["pct_esgoto_rede_geral_ou_fossa_2022"] = None
                base["pct_lixo_coletado_2022"] = None
                base["indice_vulnerabilidade_saneamento_2022"] = None
                base["classificacao_vulnerabilidade_saneamento"] = "Sem informação"
                base["ano_base_saneamento"] = None
                base["alerta_saneamento"] = f"Não foi possível carregar saneamento SIDRA 6909/9397/9541: {exc_san}"


            try:
                inep = carregar_censo_escolar_inep_mt(INEP_CENSO_ESCOLAR_ANO)
                st.session_state["ubs_api_inep_censo_escolar"] = inep
                base = base.merge(
                    inep[[
                        "codigo_ibge",
                        "escolas_total_inep",
                        "escolas_rurais_inep",
                        "escolas_urbanas_inep",
                        "escolas_indigenas_inep",
                        "escolas_quilombolas_inep",
                        "escolas_publicas_inep",
                        "matriculas_basica_inep",
                        "matriculas_infantil_inep",
                        "matriculas_fundamental_inep",
                        "matriculas_medio_inep",
                        "matriculas_eja_inep",
                        "percentual_escolas_rurais_inep",
                        "classificacao_dispersao_escolar_inep",
                        "ano_base_inep_censo_escolar",
                        "fonte_inep_censo_escolar",
                        "observacao_inep_censo_escolar",
                    ]],
                    on="codigo_ibge",
                    how="left",
                )
                for col_inep_int in [
                    "escolas_total_inep", "escolas_rurais_inep", "escolas_urbanas_inep",
                    "escolas_indigenas_inep", "escolas_quilombolas_inep", "escolas_publicas_inep",
                    "matriculas_basica_inep", "matriculas_infantil_inep", "matriculas_fundamental_inep",
                    "matriculas_medio_inep", "matriculas_eja_inep",
                ]:
                    if col_inep_int in base.columns:
                        base[col_inep_int] = pd.to_numeric(base[col_inep_int], errors="coerce").fillna(0).astype(int)
            except Exception as exc_inep:
                base["escolas_total_inep"] = None
                base["escolas_rurais_inep"] = None
                base["escolas_urbanas_inep"] = None
                base["escolas_indigenas_inep"] = None
                base["escolas_quilombolas_inep"] = None
                base["escolas_publicas_inep"] = None
                base["matriculas_basica_inep"] = None
                base["matriculas_infantil_inep"] = None
                base["matriculas_fundamental_inep"] = None
                base["matriculas_medio_inep"] = None
                base["matriculas_eja_inep"] = None
                base["percentual_escolas_rurais_inep"] = None
                base["classificacao_dispersao_escolar_inep"] = "Sem informação"
                base["alerta_inep_censo_escolar"] = f"Não foi possível carregar Censo Escolar/INEP: {exc_inep}"

            try:
                edu_esp = carregar_educacao_especial_inep_mt(INEP_CENSO_ESCOLAR_ANO)
                st.session_state["ubs_api_inep_educacao_especial"] = edu_esp
                base = base.merge(
                    edu_esp[[
                        "codigo_ibge",
                        "escolas_total_ref_educacao_especial_inep",
                        "escolas_com_matricula_educacao_especial_inep",
                        "escolas_com_aee_inep",
            "qtd_ubs_cnes_automatico",
            "qtd_ubs_cnes_atende_sus",
            "qtd_ubs_cnes_publicas",
            "populacao_por_ubs_cnes_automatico",
                        "escolas_com_indicador_acessibilidade_inep",
                        "matriculas_basica_ref_educacao_especial_inep",
                        "matriculas_educacao_especial_inep",
                        "matriculas_aee_inep",
                        "pct_matriculas_educacao_especial_inep",
                        "pct_escolas_com_educacao_especial_inep",
                        "classificacao_pressao_educacao_especial_inep",
                        "ano_base_educacao_especial_inep",
                        "fonte_educacao_especial_inep",
                        "observacao_educacao_especial_inep",
                    ]],
                    on="codigo_ibge",
                    how="left",
                )
                for col_edu_esp_int in [
                    "escolas_total_ref_educacao_especial_inep", "escolas_com_matricula_educacao_especial_inep",
                    "escolas_com_aee_inep", "escolas_com_indicador_acessibilidade_inep",
                    "matriculas_basica_ref_educacao_especial_inep", "matriculas_educacao_especial_inep", "matriculas_aee_inep",
                ]:
                    if col_edu_esp_int in base.columns:
                        base[col_edu_esp_int] = pd.to_numeric(base[col_edu_esp_int], errors="coerce").fillna(0).astype(int)
            except Exception as exc_edu_esp:
                base["matriculas_educacao_especial_inep"] = None
                base["matriculas_aee_inep"] = None
                base["escolas_com_matricula_educacao_especial_inep"] = None
                base["escolas_com_aee_inep"] = None
                base["pct_matriculas_educacao_especial_inep"] = None
                base["pct_escolas_com_educacao_especial_inep"] = None
                base["classificacao_pressao_educacao_especial_inep"] = "Sem informação"
                base["alerta_educacao_especial_inep"] = f"Não foi possível carregar educação especial/AEE INEP: {exc_edu_esp}"


            try:
                cnes_ubs = carregar_cnes_estabelecimentos_ubs_mt()
                cnes_consolidado = consolidar_cnes_ubs_por_municipio(cnes_ubs)
                st.session_state["ubs_api_cnes_ubs_lista"] = cnes_ubs
                colunas_cnes_merge = [
                    col for col in [
                        "codigo_ibge",
                        "qtd_ubs_cnes_automatico",
                        "qtd_ubs_cnes_atende_sus",
                        "qtd_ubs_cnes_publicas",
                        "tipos_unidades_cnes",
                        "natureza_unidades_cnes",
                        "ubs_cnes_lista",
                        "fonte_cnes",
                    ]
                    if col in cnes_consolidado.columns
                ]
                base = base.merge(cnes_consolidado[colunas_cnes_merge], on="codigo_ibge", how="left")
                base["qtd_ubs_cnes_automatico"] = base["qtd_ubs_cnes_automatico"].fillna(0).astype(int)
                for col_cnes_extra in ["qtd_ubs_cnes_atende_sus", "qtd_ubs_cnes_publicas"]:
                    if col_cnes_extra in base.columns:
                        base[col_cnes_extra] = base[col_cnes_extra].fillna(0).astype(int)
                base["populacao_por_ubs_cnes_automatico"] = base.apply(
                    lambda linha: round(linha["populacao_ibge"] / linha["qtd_ubs_cnes_automatico"], 2)
                    if linha.get("qtd_ubs_cnes_automatico", 0) else None,
                    axis=1,
                )
                base["observacao_cnes_automatico"] = "CNES automático preliminar; validar funcionamento real com SES/Coordenadoria APS"
            except Exception as exc_cnes:
                base["qtd_ubs_cnes_automatico"] = None
                base["ubs_cnes_lista"] = None
                base["populacao_por_ubs_cnes_automatico"] = None
                base["alerta_cnes_ubs"] = f"Não foi possível carregar CNES UBS automaticamente: {exc_cnes}"


            try:
                leitos = carregar_leitos_sus_mt()
                leitos_consolidado = consolidar_leitos_por_municipio(leitos)
                st.session_state["ubs_api_leitos_lista"] = leitos
                base = base.merge(leitos_consolidado, on="codigo_ibge", how="left")
                for col_leitos in ["qtd_hospitais_leitos", "leitos_existentes_total", "leitos_sus_total"]:
                    if col_leitos in base.columns:
                        base[col_leitos] = base[col_leitos].fillna(0)
                if "leitos_sus_total" in base.columns:
                    base["leitos_sus_por_10mil_hab"] = base.apply(
                        lambda linha: round((linha.get("leitos_sus_total", 0) / linha["populacao_ibge"]) * 10000, 2)
                        if linha.get("populacao_ibge", 0) else None,
                        axis=1,
                    )
                base["observacao_leitos_automatico"] = "Hospitais e Leitos automático preliminar; contexto assistencial complementar, não critério direto de construção de UBS"
            except Exception as exc_leitos:
                base["qtd_hospitais_leitos"] = None
                base["leitos_existentes_total"] = None
                base["leitos_sus_total"] = None
                base["leitos_sus_por_10mil_hab"] = None
                base["classificacao_contexto_leitos"] = "Sem leitura automática de leitos"
                base["alerta_leitos"] = f"Não foi possível carregar Hospitais e Leitos automaticamente: {exc_leitos}"

            try:
                sinasc = carregar_sinasc_nascidos_vivos_mt(SINASC_ANO_REFERENCIA)
                st.session_state["ubs_api_sinasc_municipal"] = sinasc
                colunas_sinasc_merge = [col for col in sinasc.columns if col != "municipio"]
                base = base.merge(sinasc[colunas_sinasc_merge], on="codigo_ibge", how="left")
                col_nasc = f"nascidos_vivos_sinasc_{SINASC_ANO_REFERENCIA}"
                if col_nasc in base.columns:
                    base[col_nasc] = base[col_nasc].fillna(0).astype(int)
                    base[f"taxa_nascidos_vivos_por_1000_hab_{SINASC_ANO_REFERENCIA}"] = base.apply(
                        lambda linha: round((linha.get(col_nasc, 0) / linha["populacao_ibge"]) * 1000, 2)
                        if linha.get("populacao_ibge", 0) else None,
                        axis=1,
                    )
                    base["classificacao_pressao_materno_infantil"] = base.apply(
                        lambda linha: _classificar_pressao_nascimentos(
                            linha.get(f"taxa_nascidos_vivos_por_1000_hab_{SINASC_ANO_REFERENCIA}"),
                            linha.get(col_nasc),
                        ),
                        axis=1,
                    )
                base["observacao_sinasc"] = "SINASC automático preliminar; contexto materno-infantil para qualificar pressão sobre a APS"
            except Exception as exc_sinasc:
                base[f"nascidos_vivos_sinasc_{SINASC_ANO_REFERENCIA}"] = None
                base[f"taxa_nascidos_vivos_por_1000_hab_{SINASC_ANO_REFERENCIA}"] = None
                base["classificacao_pressao_materno_infantil"] = "Sem leitura automática do SINASC"
                base["alerta_sinasc"] = f"Não foi possível carregar SINASC automaticamente: {exc_sinasc}"

            try:
                sim = carregar_sim_mortalidade_mt(SIM_ANO_REFERENCIA)
                st.session_state["ubs_api_sim_municipal"] = sim
                colunas_sim_merge = [col for col in sim.columns if col != "municipio"]
                base = base.merge(sim[colunas_sim_merge], on="codigo_ibge", how="left")
                col_obitos = f"obitos_sim_{SIM_ANO_REFERENCIA}"
                col_obitos_inf = f"obitos_infantis_sim_{SIM_ANO_REFERENCIA}"
                col_nasc = f"nascidos_vivos_sinasc_{SINASC_ANO_REFERENCIA}"
                if col_obitos in base.columns:
                    base[col_obitos] = base[col_obitos].fillna(0).astype(int)
                    base[f"taxa_obitos_por_1000_hab_sim_{SIM_ANO_REFERENCIA}"] = base.apply(
                        lambda linha: round((linha.get(col_obitos, 0) / linha["populacao_ibge"]) * 1000, 2)
                        if linha.get("populacao_ibge", 0) else None,
                        axis=1,
                    )
                if col_obitos_inf in base.columns:
                    base[col_obitos_inf] = base[col_obitos_inf].fillna(0).astype(int)
                    if col_nasc in base.columns:
                        base[f"taxa_mortalidade_infantil_por_1000_nv_{SIM_ANO_REFERENCIA}"] = base.apply(
                            lambda linha: round((linha.get(col_obitos_inf, 0) / linha.get(col_nasc, 0)) * 1000, 2)
                            if linha.get(col_nasc, 0) else None,
                            axis=1,
                        )
                        base["classificacao_mortalidade_infantil_preliminar"] = base.apply(
                            lambda linha: _classificar_mortalidade_infantil(
                                linha.get(f"taxa_mortalidade_infantil_por_1000_nv_{SIM_ANO_REFERENCIA}"),
                                linha.get(col_obitos_inf),
                            ),
                            axis=1,
                        )
                base["observacao_sim"] = "SIM automático preliminar; contexto de mortalidade para qualificar pressão sanitária sobre a APS"
            except Exception as exc_sim:
                base[f"obitos_sim_{SIM_ANO_REFERENCIA}"] = None
                base[f"obitos_infantis_sim_{SIM_ANO_REFERENCIA}"] = None
                base[f"taxa_obitos_por_1000_hab_sim_{SIM_ANO_REFERENCIA}"] = None
                base[f"taxa_mortalidade_infantil_por_1000_nv_{SIM_ANO_REFERENCIA}"] = None
                base["classificacao_mortalidade_infantil_preliminar"] = "Sem leitura automática do SIM"
                base["alerta_sim"] = f"Não foi possível carregar SIM automaticamente: {exc_sim}"

            try:
                povos = carregar_povos_tradicionais_ibge_mt(2022)
                st.session_state["ubs_api_povos_tradicionais"] = povos
                base = base.merge(
                    povos[[
                        "codigo_ibge",
                        "pessoas_indigenas_2022",
                        "pessoas_quilombolas_2022",
                        "pessoas_tradicionais_total_2022",
                        "ano_base_povos_tradicionais",
                        "fonte_povos_tradicionais",
                        "observacao_povos_tradicionais",
                        "alerta_povos_tradicionais",
                    ]],
                    on="codigo_ibge",
                    how="left",
                )
                for col_povos in ["pessoas_indigenas_2022", "pessoas_quilombolas_2022", "pessoas_tradicionais_total_2022"]:
                    if col_povos in base.columns:
                        base[col_povos] = pd.to_numeric(base[col_povos], errors="coerce").fillna(0).astype(int)
                base["pessoas_tradicionais_por_1000_hab_2022"] = base.apply(
                    lambda linha: round((linha.get("pessoas_tradicionais_total_2022", 0) / linha["populacao_ibge"]) * 1000, 2)
                    if linha.get("populacao_ibge", 0) else None,
                    axis=1,
                )
                base["classificacao_equidade_povos_tradicionais"] = base["pessoas_tradicionais_por_1000_hab_2022"].apply(
                    classificar_equidade_povos_tradicionais
                )
            except Exception as exc_povos:
                base["pessoas_indigenas_2022"] = None
                base["pessoas_quilombolas_2022"] = None
                base["pessoas_tradicionais_total_2022"] = None
                base["pessoas_tradicionais_por_1000_hab_2022"] = None
                base["classificacao_equidade_povos_tradicionais"] = "Sem leitura automática IBGE"
                base["ano_base_povos_tradicionais"] = 2022
                base["alerta_povos_tradicionais"] = f"Não foi possível carregar povos tradicionais IBGE/SIDRA: {exc_povos}"

            base = gerar_indice_automatico_ubs(base)
            st.session_state["ubs_base_automatica_ibge"] = base
            salvar_cache_aps(origem="geração da base automática IBGE/API em Conectores APIs UBS")
            st.success("Base automática gerada com sucesso e salva no cache local.")
        except Exception as exc:
            st.error(f"Não foi possível gerar a base automática: {exc}")

    base = st.session_state.get("ubs_base_automatica_ibge")
    if isinstance(base, pd.DataFrame) and not base.empty:
        # Normalização defensiva: quando a base vem do cache CSV, números podem voltar como texto.
        # Sem isso, o pandas concatena strings na soma e o int('2022.0') quebra a tela.
        base = base.copy()
        _colunas_numericas_cache = [
            "populacao_ibge",
            "esf_necessarias_ms",
            "ano_referencia",
            "ano_base_ruralidade",
            "indice_envelhecimento_2022",
            "idade_mediana_2022",
            "taxa_alfabetizacao_15mais_2022",
            "taxa_analfabetismo_15mais_2022",
            "pct_sem_instrucao_fund_incompleto_2022",
            "pct_sem_instrucao_fund_incompleto_25mais_2022",
            "pct_medio_completo_ou_mais_25mais_2022",
            "indice_vulnerabilidade_instrucao_2022",
            "pct_superior_completo_2022",
            "pct_domicilios_rede_agua_2022",
            "pct_domicilios_esgoto_rede_2022",
            "pct_domicilios_lixo_coletado_2022",
            "rendimento_domiciliar_per_capita_medio_2022",
            "pct_rdpc_ate_1_4_sm_2022",
            "pct_rdpc_ate_1_2_sm_2022",
            "pessoas_bpc_total",
            "pessoas_com_deficiencia_2022",
            "pessoas_diagnosticadas_autismo_2022",
            "pct_pessoas_com_deficiencia_2022",
            "pct_pessoas_diagnosticadas_autismo_2022",
            "nascidos_vivos_total",
            "obitos_total",
            "pessoas_indigenas_2022",
            "pessoas_quilombolas_2022",
            "pessoas_tradicionais_total_2022",
            "pessoas_tradicionais_por_1000_hab_2022",
            "pct_agua_rede_geral_2022",
            "pct_esgoto_rede_geral_ou_fossa_2022",
            "pct_lixo_coletado_2022",
            "indice_vulnerabilidade_saneamento_2022",
            "escolas_total_inep",
            "escolas_rurais_inep",
            "percentual_escolas_rurais_inep",
            "escolas_indigenas_inep",
            "escolas_quilombolas_inep",
            "matriculas_basica_inep",
            "matriculas_educacao_especial_inep",
            "matriculas_aee_inep",
            "pct_matriculas_educacao_especial_inep",
            "escolas_com_matricula_educacao_especial_inep",
            "escolas_com_aee_inep",
            "qtd_ubs_cnes_automatico",
            "qtd_ubs_cnes_atende_sus",
            "qtd_ubs_cnes_publicas",
            "populacao_por_ubs_cnes_automatico",
            "qtd_hospitais_leitos",
            "leitos_existentes_total",
            "leitos_sus_total",
            "leitos_sus_por_10mil_hab",
        ]
        if "codigo_ibge" in base.columns:
            base["codigo_ibge"] = _codigo_ibge_texto_7(base["codigo_ibge"])

        for _col_num in _colunas_numericas_cache:
            if _col_num in base.columns:
                base[_col_num] = _serie_numerica_flexivel(base[_col_num])

        # Atualiza a sessão já normalizada para evitar que outras seções leiam strings do cache.
        st.session_state["ubs_base_automatica_ibge"] = base

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Municípios", int(base["codigo_ibge"].nunique()) if "codigo_ibge" in base.columns else len(base))
        c2.metric("População estimada IBGE", f"{int(base['populacao_ibge'].fillna(0).sum()):,}".replace(",", "."))
        c3.metric("eSF necessárias MS", int(base["esf_necessarias_ms"].fillna(0).sum()))
        ano_calc = base["ano_referencia"].dropna().iloc[0] if "ano_referencia" in base.columns and base["ano_referencia"].notna().any() else 0
        c4.metric("Ano do cálculo MS", int(float(ano_calc)) if ano_calc else "-")

        if "ano_base_ruralidade" in base.columns and base["ano_base_ruralidade"].notna().any():
            ano_rural = base["ano_base_ruralidade"].dropna().iloc[0]
            ano_ref = base["ano_referencia"].dropna().iloc[0] if "ano_referencia" in base.columns and base["ano_referencia"].notna().any() else 0
            st.caption(
                f"Perfil urbano/rural utilizado apenas como indicador estrutural: Censo {int(float(ano_rural))}. "
                f"O cálculo MS permanece baseado na população estimada de {int(float(ano_ref)) if ano_ref else '-'} ."
            )

        if "regiao_saude_sus" in base.columns and base["regiao_saude_sus"].notna().any():
            resumo_regioes = (
                base.groupby(["macrorregiao_saude_sus", "regiao_saude_sus"], dropna=False)
                .agg(
                    municipios=("codigo_ibge", "count"),
                    populacao=("populacao_ibge", "sum"),
                    esf_necessarias_ms=("esf_necessarias_ms", "sum"),
                )
                .reset_index()
                .sort_values(["macrorregiao_saude_sus", "regiao_saude_sus"], na_position="last")
            )
            st.markdown("#### Resumo por Macrorregião e Região de Saúde")
            st.dataframe(resumo_regioes, use_container_width=True, hide_index=True)

        if "regiao_imediata_ibge" in base.columns and base["regiao_imediata_ibge"].notna().any():
            resumo_geo_ibge = (
                base.groupby(["regiao_intermediaria_ibge", "regiao_imediata_ibge"], dropna=False)
                .agg(
                    municipios=("codigo_ibge", "count"),
                    populacao=("populacao_ibge", "sum"),
                    esf_necessarias_ms=("esf_necessarias_ms", "sum"),
                )
                .reset_index()
                .sort_values(["regiao_intermediaria_ibge", "regiao_imediata_ibge"], na_position="last")
            )
            st.markdown("#### Resumo por Regiões Geográficas IBGE")
            st.caption(
                "Camada territorial complementar do IBGE. Não substitui as Regiões de Saúde do SUS, "
                "mas ajuda a analisar polos e agrupamentos territoriais oficiais."
            )
            st.dataframe(resumo_geo_ibge, use_container_width=True, hide_index=True)

        if "classificacao_territorial_preliminar" in base.columns:
            resumo_territorial = (
                base["classificacao_territorial_preliminar"]
                .fillna("Sem informação")
                .value_counts()
                .rename_axis("Classificação territorial")
                .reset_index(name="Municípios")
            )
            st.markdown("#### Resumo territorial preliminar")
            st.dataframe(resumo_territorial, use_container_width=True, hide_index=True)

        if "classificacao_ruralidade_preliminar" in base.columns:
            resumo_ruralidade = (
                base["classificacao_ruralidade_preliminar"]
                .fillna("Sem informação")
                .value_counts()
                .rename_axis("Classificação de ruralidade")
                .reset_index(name="Municípios")
            )
            st.markdown("#### Resumo de ruralidade preliminar")
            st.dataframe(resumo_ruralidade, use_container_width=True, hide_index=True)

        if "classificacao_envelhecimento_preliminar" in base.columns:
            st.markdown("#### Perfil demográfico automático — Censo 2022")
            st.caption(
                "Indicadores sintéticos do IBGE/SIDRA 9515. Eles não substituem a população estimada usada no cálculo MS; "
                "servem para qualificar municípios com maior envelhecimento populacional e possível maior pressão sobre a APS."
            )
            col_demo1, col_demo2, col_demo3 = st.columns(3)
            _indice_env = _serie_numerica_flexivel(base["indice_envelhecimento_2022"]) if "indice_envelhecimento_2022" in base.columns else pd.Series(dtype=float)
            _idade_mediana = _serie_numerica_flexivel(base["idade_mediana_2022"]) if "idade_mediana_2022" in base.columns else pd.Series(dtype=float)
            _indice_env_validos = _indice_env[_indice_env > 0]
            _idade_mediana_validos = _idade_mediana[_idade_mediana > 0]
            col_demo1.metric(
                "Índice médio de envelhecimento",
                f"{_indice_env_validos.mean():.1f}" if not _indice_env_validos.empty else "-",
            )
            col_demo2.metric(
                "Idade mediana média",
                f"{_idade_mediana_validos.mean():.1f}" if not _idade_mediana_validos.empty else "-",
            )
            col_demo3.metric(
                "Municípios alto/muito alto envelhecimento",
                int(base["classificacao_envelhecimento_preliminar"].astype(str).str.contains("Alto|Muito alto", regex=True, na=False).sum()),
            )
            resumo_demo = (
                base["classificacao_envelhecimento_preliminar"]
                .fillna("Sem informação")
                .value_counts()
                .rename_axis("Classificação de envelhecimento")
                .reset_index(name="Municípios")
            )
            st.dataframe(resumo_demo, use_container_width=True, hide_index=True)

        if "classificacao_vulnerabilidade_educacional" in base.columns:
            st.markdown("#### Vulnerabilidade socioeducacional — Alfabetização Censo 2022")
            st.caption(
                "Indicador IBGE/SIDRA 9543. Não define construção de UBS sozinho; qualifica territórios onde a APS pode precisar "
                "de comunicação em saúde mais acessível, busca ativa e integração com a Educação."
            )
            taxa_alf_media = _media_numerica_coluna(base, "taxa_alfabetizacao_15mais_2022")
            taxa_analf_media = _media_numerica_coluna(base, "taxa_analfabetismo_15mais_2022")
            vuln_alta = int(base["classificacao_vulnerabilidade_educacional"].astype(str).str.contains("Alta|Muito alta", regex=True, na=False).sum())
            se1, se2, se3 = st.columns(3)
            se1.metric("Alfabetização média 15+", f"{taxa_alf_media:.2f}%" if pd.notna(taxa_alf_media) else "-")
            se2.metric("Analfabetismo médio 15+", f"{taxa_analf_media:.2f}%" if pd.notna(taxa_analf_media) else "-")
            se3.metric("Municípios alta/muito alta vulnerabilidade", vuln_alta)
            resumo_educacional = (
                base["classificacao_vulnerabilidade_educacional"]
                .fillna("Sem informação")
                .value_counts()
                .rename_axis("Classificação socioeducacional")
                .reset_index(name="Municípios")
            )
            st.dataframe(resumo_educacional, use_container_width=True, hide_index=True)
            colunas_educacao_top = [
                col for col in [
                    "municipio", "regiao_saude_sus", "populacao_ibge",
                    "taxa_alfabetizacao_15mais_2022", "taxa_analfabetismo_15mais_2022",
                    "classificacao_vulnerabilidade_educacional",
                ]
                if col in base.columns
            ]
            if colunas_educacao_top:
                st.dataframe(
                    base.sort_values("taxa_analfabetismo_15mais_2022", ascending=False, na_position="last")[colunas_educacao_top].head(20),
                    use_container_width=True,
                    hide_index=True,
                )

        if "classificacao_vulnerabilidade_instrucao" in base.columns:
            st.markdown("#### Vulnerabilidade socioeducacional — Nível de instrução Censo 2022")
            st.caption(
                "Indicador IBGE/SIDRA 10061. Complementa a taxa de alfabetização ao mostrar a escolaridade estrutural da população adulta; "
                "apoia políticas integradas APS-Educação, comunicação em saúde e busca ativa qualificada."
            )
            inst1, inst2, inst3 = st.columns(3)
            baixa_media = _media_numerica_coluna(base, "pct_sem_instrucao_fund_incompleto_25mais_2022")
            medio_media = _media_numerica_coluna(base, "pct_medio_completo_ou_mais_25mais_2022")
            alta_inst = int(base["classificacao_vulnerabilidade_instrucao"].astype(str).str.contains("Alta|Muito alta", regex=True, na=False).sum())
            inst1.metric("Sem instrução/fund. incompleto 25+", f"{baixa_media:.2f}%" if pd.notna(baixa_media) else "-")
            inst2.metric("Médio completo ou mais 25+", f"{medio_media:.2f}%" if pd.notna(medio_media) else "-")
            inst3.metric("Municípios alta/muito alta vulnerabilidade", alta_inst)
            resumo_instrucao = (
                base["classificacao_vulnerabilidade_instrucao"]
                .fillna("Sem informação")
                .value_counts()
                .rename_axis("Classificação por nível de instrução")
                .reset_index(name="Municípios")
            )
            st.dataframe(resumo_instrucao, use_container_width=True, hide_index=True)
            colunas_instrucao_top = [
                col for col in [
                    "municipio", "regiao_saude_sus", "populacao_ibge",
                    "pct_sem_instrucao_fund_incompleto_25mais_2022",
                    "pct_medio_completo_ou_mais_25mais_2022",
                    "indice_vulnerabilidade_instrucao_2022",
                    "classificacao_vulnerabilidade_instrucao",
                ]
                if col in base.columns
            ]
            if colunas_instrucao_top:
                st.dataframe(
                    base.sort_values("indice_vulnerabilidade_instrucao_2022", ascending=False, na_position="last")[colunas_instrucao_top].head(20),
                    use_container_width=True,
                    hide_index=True,
                )

        if "classificacao_vulnerabilidade_renda" in base.columns:
            st.markdown("#### Vulnerabilidade socioeconômica — Renda Censo 2022")
            st.caption(
                "Indicadores agregados do IBGE/SIDRA 10295 e 10296. Servem para qualificar vulnerabilidade de renda e desigualdades sociais; "
                "não definem construção de UBS isoladamente."
            )
            renda1, renda2, renda3 = st.columns(3)
            rdpc_media = _media_numerica_coluna(base, "rendimento_domiciliar_per_capita_medio_2022")
            pct_meio_media = _media_numerica_coluna(base, "pct_rdpc_ate_1_2_sm_2022")
            alta_renda = int(base["classificacao_vulnerabilidade_renda"].astype(str).str.contains("Alta|Muito alta", regex=True, na=False).sum())
            renda1.metric("RDPC médio", f"R$ {rdpc_media:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if pd.notna(rdpc_media) else "-")
            renda2.metric("Pop. até 1/2 SM média", f"{pct_meio_media:.2f}%" if pd.notna(pct_meio_media) else "-")
            renda3.metric("Municípios alta/muito alta vulnerabilidade", alta_renda)
            resumo_renda = (
                base["classificacao_vulnerabilidade_renda"]
                .fillna("Sem informação")
                .value_counts()
                .rename_axis("Classificação de vulnerabilidade de renda")
                .reset_index(name="Municípios")
            )
            st.dataframe(resumo_renda, use_container_width=True, hide_index=True)
            cols_renda = [
                "municipio", "regiao_saude_sus", "populacao_ibge",
                "rendimento_domiciliar_per_capita_medio_2022",
                "pct_rdpc_ate_1_4_sm_2022", "pct_rdpc_ate_1_2_sm_2022",
                "classificacao_vulnerabilidade_renda",
            ]
            st.dataframe(
                base[[c for c in cols_renda if c in base.columns]]
                .sort_values("rendimento_domiciliar_per_capita_medio_2022", ascending=True, na_position="last")
                .head(20),
                use_container_width=True,
                hide_index=True,
            )

        if "classificacao_pressao_deficiencia_autismo" in base.columns:
            st.markdown("#### Cuidado continuado e equidade — Deficiência e autismo Censo 2022")
            st.caption(
                "Dados agregados do IBGE/SIDRA sobre pessoas com deficiência e pessoas diagnosticadas com TEA. "
                "A camada não substitui BPC/MDS; qualifica a necessidade potencial de cuidado longitudinal, reabilitação, comunicação acessível e articulação intersetorial."
            )
            da1, da2, da3, da4 = st.columns(4)
            total_def = int(_soma_numerica_coluna(base, "pessoas_com_deficiencia_2022"))
            total_tea = int(_soma_numerica_coluna(base, "pessoas_diagnosticadas_autismo_2022"))
            pct_def_media = _media_numerica_coluna(base, "pct_pessoas_com_deficiencia_2022")
            alta_def = int(base["classificacao_pressao_deficiencia_autismo"].astype(str).str.contains("Alta|Muito alta", regex=True, na=False).sum())
            da1.metric("Pessoas com deficiência", f"{total_def:,}".replace(",", "."))
            da2.metric("Pessoas diagnosticadas com TEA", f"{total_tea:,}".replace(",", "."))
            da3.metric("% médio com deficiência", f"{pct_def_media:.2f}%" if pd.notna(pct_def_media) else "-")
            da4.metric("Municípios alta/muito alta pressão", alta_def)
            resumo_defaut = (
                base["classificacao_pressao_deficiencia_autismo"]
                .fillna("Sem informação")
                .value_counts()
                .rename_axis("Classificação deficiência/autismo")
                .reset_index(name="Municípios")
            )
            st.dataframe(resumo_defaut, use_container_width=True, hide_index=True)
            cols_defaut = [
                "municipio", "regiao_saude_sus", "populacao_ibge",
                "pessoas_com_deficiencia_2022", "pct_pessoas_com_deficiencia_2022",
                "pessoas_diagnosticadas_autismo_2022", "pct_pessoas_diagnosticadas_autismo_2022",
                "classificacao_pressao_deficiencia_autismo",
            ]
            st.dataframe(
                base[[c for c in cols_defaut if c in base.columns]]
                .sort_values("pessoas_com_deficiencia_2022", ascending=False, na_position="last")
                .head(20),
                use_container_width=True,
                hide_index=True,
            )

        if "classificacao_vulnerabilidade_saneamento" in base.columns:
            st.markdown("#### Integração APS-Educação — Censo Escolar INEP")
            st.caption("Dados agregados por município a partir dos microdados oficiais do Censo Escolar. Escolas rurais, indígenas e quilombolas ajudam a sinalizar dispersão territorial e oportunidades de busca ativa intersetorial APS-Educação.")
            if "escolas_total_inep" in base.columns:
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Escolas ativas INEP", int(_soma_numerica_coluna(base, "escolas_total_inep")))
                c2.metric("Escolas rurais", int(_soma_numerica_coluna(base, "escolas_rurais_inep")))
                c3.metric("Escolas indígenas", int(_soma_numerica_coluna(base, "escolas_indigenas_inep")))
                c4.metric("Matrículas educação básica", int(_soma_numerica_coluna(base, "matriculas_basica_inep")))
                resumo_inep = (
                    base.groupby("classificacao_dispersao_escolar_inep", dropna=False)
                    .agg(municipios=("codigo_ibge", "count"))
                    .reset_index()
                    .sort_values("municipios", ascending=False)
                )
                st.dataframe(resumo_inep, use_container_width=True, hide_index=True)
                cols_inep = [
                    "municipio", "regiao_saude_sus", "populacao_ibge",
                    "escolas_total_inep", "escolas_rurais_inep", "percentual_escolas_rurais_inep",
                    "escolas_indigenas_inep", "escolas_quilombolas_inep",
                    "matriculas_basica_inep", "classificacao_dispersao_escolar_inep",
                ]
                st.dataframe(
                    base[[c for c in cols_inep if c in base.columns]]
                    .sort_values(["escolas_rurais_inep", "escolas_indigenas_inep"], ascending=False)
                    .head(20),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("Censo Escolar/INEP ainda não carregado nesta geração.")

            if "classificacao_pressao_educacao_especial_inep" in base.columns:
                st.markdown("#### APS-Educação e cuidado continuado — Educação especial/AEE INEP")
                st.caption("Camada agregada por município a partir dos microdados oficiais do Censo Escolar. Ajuda a qualificar demanda potencial de cuidado continuado, deficiência/TEA, reabilitação, acessibilidade e articulação APS-Educação.")
                ee1, ee2, ee3, ee4 = st.columns(4)
                ee1.metric("Matrículas educação especial", f"{int(_soma_numerica_coluna(base, 'matriculas_educacao_especial_inep')):,}".replace(",", "."))
                ee2.metric("Matrículas AEE", f"{int(_soma_numerica_coluna(base, 'matriculas_aee_inep')):,}".replace(",", "."))
                ee3.metric("Escolas com educ. especial", f"{int(_soma_numerica_coluna(base, 'escolas_com_matricula_educacao_especial_inep')):,}".replace(",", "."))
                ee4.metric("Escolas com AEE", f"{int(_soma_numerica_coluna(base, 'escolas_com_aee_inep')):,}".replace(",", "."))
                resumo_ee = (
                    base["classificacao_pressao_educacao_especial_inep"]
                    .fillna("Sem informação")
                    .value_counts()
                    .rename_axis("Classificação educação especial/AEE")
                    .reset_index(name="Municípios")
                )
                st.dataframe(resumo_ee, use_container_width=True, hide_index=True)
                cols_ee = [
                    "municipio", "regiao_saude_sus", "populacao_ibge",
                    "matriculas_educacao_especial_inep", "matriculas_aee_inep",
                    "pct_matriculas_educacao_especial_inep", "escolas_com_matricula_educacao_especial_inep",
                    "escolas_com_aee_inep", "classificacao_pressao_educacao_especial_inep",
                ]
                st.dataframe(
                    base[[c for c in cols_ee if c in base.columns]]
                    .sort_values("matriculas_educacao_especial_inep", ascending=False, na_position="last")
                    .head(20),
                    use_container_width=True,
                    hide_index=True,
                )


            if "pessoas_tradicionais_total_2022" in base.columns:
                st.markdown("#### Equidade territorial — Populações indígena e quilombola IBGE")
                st.caption(
                    "Dados agregados do Censo 2022/IBGE por município. A camada não define construção de UBS isoladamente; "
                    "sinaliza territórios que podem exigir validação específica, adequação cultural, busca ativa e articulação intersetorial."
                )
                pov1, pov2, pov3, pov4 = st.columns(4)
                ind_total = int(_soma_numerica_coluna(base, "pessoas_indigenas_2022"))
                quil_total = int(_soma_numerica_coluna(base, "pessoas_quilombolas_2022"))
                mun_ind = int((_serie_numerica_flexivel(base["pessoas_indigenas_2022"]) > 0).sum()) if "pessoas_indigenas_2022" in base.columns else 0
                mun_quil = int((_serie_numerica_flexivel(base["pessoas_quilombolas_2022"]) > 0).sum()) if "pessoas_quilombolas_2022" in base.columns else 0
                pov1.metric("Pessoas indígenas", f"{ind_total:,}".replace(",", "."))
                pov2.metric("Pessoas quilombolas", f"{quil_total:,}".replace(",", "."))
                pov3.metric("Municípios com população indígena", mun_ind)
                pov4.metric("Municípios com população quilombola", mun_quil)
                resumo_povos = (
                    base["classificacao_equidade_povos_tradicionais"]
                    .fillna("Sem informação")
                    .value_counts()
                    .rename_axis("Classificação de equidade territorial")
                    .reset_index(name="Municípios")
                )
                st.dataframe(resumo_povos, use_container_width=True, hide_index=True)
                cols_povos = [
                    "municipio", "regiao_saude_sus", "populacao_ibge",
                    "pessoas_indigenas_2022", "pessoas_quilombolas_2022",
                    "pessoas_tradicionais_total_2022", "pessoas_tradicionais_por_1000_hab_2022",
                    "classificacao_equidade_povos_tradicionais",
                ]
                st.dataframe(
                    base[[c for c in cols_povos if c in base.columns]]
                    .sort_values("pessoas_tradicionais_total_2022", ascending=False, na_position="last")
                    .head(20),
                    use_container_width=True,
                    hide_index=True,
                )



            st.markdown("#### Condições de vida — Saneamento Censo 2022")
            st.caption(
                "Indicadores IBGE/SIDRA 6909, 9397 e 9541. Entram como camada de vulnerabilidade sanitária e condições domiciliares; "
                "não definem construção de UBS isoladamente, mas ajudam a qualificar riscos territoriais e ações intersetoriais."
            )
            san1, san2, san3, san4 = st.columns(4)
            san1.metric(
                "Água rede geral média",
                f"{_media_numerica_coluna(base, 'pct_agua_rede_geral_2022'):.2f}%" if _media_numerica_coluna(base, 'pct_agua_rede_geral_2022') is not None else "-",
            )
            san2.metric(
                "Esgoto/rede ou fossa média",
                f"{_media_numerica_coluna(base, 'pct_esgoto_rede_geral_ou_fossa_2022'):.2f}%" if _media_numerica_coluna(base, 'pct_esgoto_rede_geral_ou_fossa_2022') is not None else "-",
            )
            san3.metric(
                "Lixo coletado médio",
                f"{_media_numerica_coluna(base, 'pct_lixo_coletado_2022'):.2f}%" if _media_numerica_coluna(base, 'pct_lixo_coletado_2022') is not None else "-",
            )
            san4.metric(
                "Municípios alta/muito alta vulnerabilidade",
                int(base["classificacao_vulnerabilidade_saneamento"].astype(str).str.contains("Alta|Muito alta", regex=True, na=False).sum()),
            )
            resumo_saneamento = (
                base["classificacao_vulnerabilidade_saneamento"]
                .fillna("Sem informação")
                .value_counts()
                .rename_axis("Classificação de saneamento")
                .reset_index(name="Municípios")
            )
            st.dataframe(resumo_saneamento, use_container_width=True, hide_index=True)
            colunas_saneamento_top = [
                col for col in [
                    "municipio", "regiao_saude_sus", "populacao_ibge",
                    "pct_agua_rede_geral_2022", "pct_esgoto_rede_geral_ou_fossa_2022",
                    "pct_lixo_coletado_2022", "indice_vulnerabilidade_saneamento_2022",
                    "classificacao_vulnerabilidade_saneamento",
                ]
                if col in base.columns
            ]
            if colunas_saneamento_top:
                st.dataframe(
                    base.sort_values("indice_vulnerabilidade_saneamento_2022", ascending=False, na_position="last")[colunas_saneamento_top].head(20),
                    use_container_width=True,
                    hide_index=True,
                )

        if "classificacao_distritos_preliminar" in base.columns:
            resumo_distritos = (
                base["classificacao_distritos_preliminar"]
                .fillna("Sem informação")
                .value_counts()
                .rename_axis("Classificação por distritos IBGE")
                .reset_index(name="Municípios")
            )
            st.markdown("#### Resumo por distritos oficiais IBGE")
            st.caption("Distritos IBGE ajudam a sinalizar municípios que podem exigir validação territorial mais cuidadosa. Não substituem comunidades rurais, assentamentos ou informação municipal.")
            st.dataframe(resumo_distritos, use_container_width=True, hide_index=True)


        if "qtd_ubs_cnes_automatico" in base.columns and base["qtd_ubs_cnes_automatico"].notna().any():
            st.markdown("#### Resumo preliminar CNES/UBS")
            st.caption(
                "Contagem automática preliminar de estabelecimentos compatíveis com UBS/USF na base CNES. "
                "Esse dado é cadastral e deve ser validado pela SES/Coordenadoria APS antes de qualquer decisão final."
            )
            cnes_total = int(_soma_numerica_coluna(base, "qtd_ubs_cnes_automatico"))
            qtd_cnes_series = _serie_numerica_flexivel(base["qtd_ubs_cnes_automatico"])
            municipios_sem_cnes = int((qtd_cnes_series == 0).sum())
            pop_total_cnes = _soma_numerica_coluna(base, "populacao_ibge")
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("UBS/USF CNES preliminar", cnes_total)
            mc2.metric("Municípios sem UBS/USF identificada", municipios_sem_cnes)
            mc3.metric("População média por UBS CNES", f"{round(pop_total_cnes / cnes_total, 1):,.1f}".replace(',', '.') if cnes_total else "-")

            colunas_cnes_top = [col for col in ["municipio", "regiao_saude_sus", "populacao_ibge", "qtd_ubs_cnes_automatico", "qtd_ubs_cnes_atende_sus", "qtd_ubs_cnes_publicas", "populacao_por_ubs_cnes_automatico"] if col in base.columns]
            if colunas_cnes_top:
                st.dataframe(
                    base.sort_values("populacao_por_ubs_cnes_automatico", ascending=False, na_position="last")[colunas_cnes_top].head(20),
                    use_container_width=True,
                    hide_index=True,
                )

            df_cnes_detalhado = st.session_state.get("ubs_api_cnes_ubs_lista")
            if isinstance(df_cnes_detalhado, pd.DataFrame) and not df_cnes_detalhado.empty:
                with st.expander("Ver lista detalhada de UBS/USF identificadas no CNES", expanded=False):
                    cnes_exibicao = preparar_cnes_detalhado_para_exibicao(df_cnes_detalhado, base)
                    municipios_cnes = sorted(cnes_exibicao["municipio"].dropna().unique().tolist()) if "municipio" in cnes_exibicao.columns else []
                    filtro_municipios_cnes = st.multiselect(
                        "Filtrar municípios no detalhamento CNES",
                        options=municipios_cnes,
                        default=[],
                    )
                    cnes_filtrado = cnes_exibicao.copy()
                    if filtro_municipios_cnes and "municipio" in cnes_filtrado.columns:
                        cnes_filtrado = cnes_filtrado[cnes_filtrado["municipio"].isin(filtro_municipios_cnes)].copy()

                    termo_cnes = st.text_input("Buscar por nome da unidade, CNES ou endereço", value="")
                    if termo_cnes.strip():
                        termo_norm = _normalizar_texto_busca(termo_cnes)
                        texto_linha = cnes_filtrado.astype(str).agg(" ".join, axis=1).map(_normalizar_texto_busca)
                        cnes_filtrado = cnes_filtrado[texto_linha.str.contains(re.escape(termo_norm), na=False)].copy()

                    st.caption(
                        "Use este detalhamento para conferência preliminar. A lista vem do CNES/Dados Abertos e ainda precisa ser validada "
                        "pela SES/Coordenadoria APS quanto ao funcionamento real, tipo correto da unidade e eventual duplicidade cadastral."
                    )
                    st.dataframe(cnes_filtrado, use_container_width=True, hide_index=True)

                    colr1, colr2 = st.columns(2)
                    with colr1:
                        if "categoria_preliminar_cnes" in cnes_exibicao.columns:
                            st.markdown("##### Distribuição por categoria preliminar")
                            st.dataframe(
                                cnes_exibicao["categoria_preliminar_cnes"]
                                .fillna("Não informado")
                                .value_counts()
                                .rename_axis("Categoria preliminar")
                                .reset_index(name="Unidades"),
                                use_container_width=True,
                                hide_index=True,
                            )
                    with colr2:
                        if "natureza_gestao_preliminar" in cnes_exibicao.columns:
                            st.markdown("##### Distribuição por natureza/gestão")
                            st.dataframe(
                                cnes_exibicao["natureza_gestao_preliminar"]
                                .fillna("Não informado")
                                .value_counts()
                                .rename_axis("Natureza/gestão preliminar")
                                .reset_index(name="Unidades"),
                                use_container_width=True,
                                hide_index=True,
                            )

                    resumo_cnes_municipio = (
                        cnes_exibicao.groupby([col for col in ["macrorregiao_saude_sus", "regiao_saude_sus", "municipio"] if col in cnes_exibicao.columns], dropna=False)
                        .agg(qtd_unidades_cnes=("nome_unidade_cnes", "count"))
                        .reset_index()
                        .sort_values("qtd_unidades_cnes", ascending=False)
                    ) if "nome_unidade_cnes" in cnes_exibicao.columns else pd.DataFrame()

                    st.download_button(
                        "Baixar detalhamento CNES/UBS em Excel",
                        data=_excel_multiplas_abas({
                            "Detalhamento CNES": cnes_exibicao,
                            "Resumo por município": resumo_cnes_municipio,
                            "Resumo por categoria": (
                                cnes_exibicao["categoria_preliminar_cnes"].fillna("Não informado").value_counts().rename_axis("categoria_preliminar_cnes").reset_index(name="unidades")
                                if "categoria_preliminar_cnes" in cnes_exibicao.columns else pd.DataFrame()
                            ),
                            "Resumo natureza gestão": (
                                cnes_exibicao["natureza_gestao_preliminar"].fillna("Não informado").value_counts().rename_axis("natureza_gestao_preliminar").reset_index(name="unidades")
                                if "natureza_gestao_preliminar" in cnes_exibicao.columns else pd.DataFrame()
                            ),
                            "Resumo atende SUS": (
                                cnes_exibicao["atende_sus_preliminar"].fillna("Não informado").value_counts().rename_axis("atende_sus_preliminar").reset_index(name="unidades")
                                if "atende_sus_preliminar" in cnes_exibicao.columns else pd.DataFrame()
                            ),
                        }),
                        file_name="detalhamento_cnes_ubs_usf_mt.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                    )


        if "qtd_hospitais_leitos" in base.columns and base["qtd_hospitais_leitos"].notna().any():


            st.markdown("#### Contexto assistencial complementar — Hospitais e Leitos")
            st.caption(
                "Leitura automática preliminar da base Hospitais e Leitos. Esse bloco não define construção de UBS; "
                "serve apenas para contextualizar a retaguarda assistencial disponível no município/região."
            )
            # Colunas de leitos podem voltar do cache CSV como texto; somar direto concatena strings.
            leitos_total = _soma_numerica_coluna(base, "leitos_existentes_total")
            leitos_sus = _soma_numerica_coluna(base, "leitos_sus_total")
            hospitais_total = int(_soma_numerica_coluna(base, "qtd_hospitais_leitos"))
            if "qtd_hospitais_leitos" in base.columns:
                _serie_hospitais = _serie_numerica_flexivel(base["qtd_hospitais_leitos"])
                municipios_sem_leitos = int((_serie_hospitais.fillna(0) == 0).sum())
            else:
                municipios_sem_leitos = 0
            lh1, lh2, lh3, lh4 = st.columns(4)
            lh1.metric("Estabelecimentos com leitos", hospitais_total)
            lh2.metric("Leitos existentes", int(leitos_total))
            lh3.metric("Leitos SUS", int(leitos_sus))
            lh4.metric("Municípios sem leitos identificados", municipios_sem_leitos)

            colunas_leitos_top = [
                col for col in [
                    "municipio", "regiao_saude_sus", "populacao_ibge", "qtd_hospitais_leitos",
                    "leitos_existentes_total", "leitos_sus_total", "leitos_sus_por_10mil_hab", "classificacao_contexto_leitos",
                ]
                if col in base.columns
            ]
            if colunas_leitos_top:
                st.dataframe(
                    base.assign(
                        leitos_sus_total=_serie_numerica_flexivel(base["leitos_sus_total"]) if "leitos_sus_total" in base.columns else 0,
                        populacao_ibge=_serie_numerica_flexivel(base["populacao_ibge"]) if "populacao_ibge" in base.columns else 0,
                    ).sort_values(["leitos_sus_total", "populacao_ibge"], ascending=[True, False], na_position="last")[colunas_leitos_top].head(25),
                    use_container_width=True,
                    hide_index=True,
                )

            if "classificacao_contexto_leitos" in base.columns:
                resumo_contexto_leitos = (
                    base["classificacao_contexto_leitos"]
                    .fillna("Sem informação")
                    .value_counts()
                    .rename_axis("Classificação de contexto")
                    .reset_index(name="Municípios")
                )
                st.markdown("##### Resumo por contexto hospitalar/leitos")
                st.dataframe(resumo_contexto_leitos, use_container_width=True, hide_index=True)

            df_leitos_detalhado = st.session_state.get("ubs_api_leitos_lista")
            if isinstance(df_leitos_detalhado, pd.DataFrame) and not df_leitos_detalhado.empty:
                with st.expander("Ver lista detalhada de Hospitais e Leitos", expanded=False):
                    leitos_exibicao = preparar_leitos_detalhado_para_exibicao(df_leitos_detalhado, base)
                    municipios_leitos = sorted(leitos_exibicao["municipio"].dropna().unique().tolist()) if "municipio" in leitos_exibicao.columns else []
                    filtro_municipios_leitos = st.multiselect(
                        "Filtrar municípios no detalhamento de leitos",
                        options=municipios_leitos,
                        default=[],
                    )
                    leitos_filtrado = leitos_exibicao.copy()
                    if filtro_municipios_leitos and "municipio" in leitos_filtrado.columns:
                        leitos_filtrado = leitos_filtrado[leitos_filtrado["municipio"].isin(filtro_municipios_leitos)].copy()
                    termo_leitos = st.text_input("Buscar por hospital, CNES ou tipo de leito", value="")
                    if termo_leitos.strip():
                        termo_norm = _normalizar_texto_busca(termo_leitos)
                        texto_linha = leitos_filtrado.astype(str).agg(" ".join, axis=1).map(_normalizar_texto_busca)
                        leitos_filtrado = leitos_filtrado[texto_linha.str.contains(re.escape(termo_norm), na=False)].copy()
                    st.dataframe(leitos_filtrado, use_container_width=True, hide_index=True)

                    resumo_leitos_municipio = (
                        leitos_exibicao.groupby([col for col in ["macrorregiao_saude_sus", "regiao_saude_sus", "municipio"] if col in leitos_exibicao.columns], dropna=False)
                        .agg(
                            estabelecimentos=("nome_estabelecimento_leitos", "nunique"),
                            leitos_existentes=("leitos_existentes", "sum"),
                            leitos_sus=("leitos_sus", "sum"),
                        )
                        .reset_index()
                        .sort_values("leitos_sus", ascending=False)
                    ) if "nome_estabelecimento_leitos" in leitos_exibicao.columns else pd.DataFrame()

                    st.download_button(
                        "Baixar detalhamento Hospitais e Leitos em Excel",
                        data=_excel_multiplas_abas({
                            "Detalhamento leitos": leitos_exibicao,
                            "Resumo por município": resumo_leitos_municipio,
                            "Resumo contexto": resumo_contexto_leitos if "resumo_contexto_leitos" in locals() else pd.DataFrame(),
                            "Base municipal": base[[col for col in colunas_leitos_top if col in base.columns]] if colunas_leitos_top else pd.DataFrame(),
                        }),
                        file_name="detalhamento_hospitais_leitos_mt.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                    )

        col_nascidos_sinasc = f"nascidos_vivos_sinasc_{SINASC_ANO_REFERENCIA}"
        col_taxa_sinasc = f"taxa_nascidos_vivos_por_1000_hab_{SINASC_ANO_REFERENCIA}"
        # Bases recuperadas do cache CSV podem voltar como texto. Convertemos antes de somar,
        # comparar ou ordenar para evitar concatenação de strings e erros do tipo str > int.
        for _col_sinasc_num in [
            "populacao_ibge",
            col_nascidos_sinasc,
            col_taxa_sinasc,
            f"pct_pre_natal_7mais_sinasc_{SINASC_ANO_REFERENCIA}",
            f"pct_baixo_peso_sinasc_{SINASC_ANO_REFERENCIA}",
            f"pct_prematuridade_sinasc_{SINASC_ANO_REFERENCIA}",
            f"idade_media_mae_sinasc_{SINASC_ANO_REFERENCIA}",
        ]:
            if _col_sinasc_num in base.columns:
                base[_col_sinasc_num] = _serie_numerica_flexivel(base[_col_sinasc_num])
        if col_nascidos_sinasc in base.columns and base[col_nascidos_sinasc].notna().any():
            st.markdown("#### Contexto materno-infantil — SINASC")
            st.caption(
                "Leitura automática preliminar do Sistema de Informação sobre Nascidos Vivos. "
                "Esse bloco não define construção de UBS; qualifica a pressão materno-infantil potencial sobre a APS."
            )
            total_nascidos = int(_soma_numerica_coluna(base, col_nascidos_sinasc))
            _serie_nascidos = _serie_numerica_flexivel(base[col_nascidos_sinasc])
            municipios_com_nascidos = int((_serie_nascidos > 0).sum())
            _pop_total_sinasc = _soma_numerica_coluna(base, "populacao_ibge")
            taxa_media = round((total_nascidos / _pop_total_sinasc) * 1000, 2) if _pop_total_sinasc else 0
            classif_alta = int(base.get("classificacao_pressao_materno_infantil", pd.Series(dtype=str)).astype(str).str.contains("Alta", na=False).sum()) if "classificacao_pressao_materno_infantil" in base.columns else 0
            sm1, sm2, sm3, sm4 = st.columns(4)
            sm1.metric(f"Nascidos vivos {SINASC_ANO_REFERENCIA}", total_nascidos)
            sm2.metric("Municípios com registro", municipios_com_nascidos)
            sm3.metric("Taxa/1.000 hab.", taxa_media)
            sm4.metric("Alta pressão preliminar", classif_alta)

            colunas_sinasc_top = [
                col for col in [
                    "municipio", "regiao_saude_sus", "populacao_ibge", col_nascidos_sinasc,
                    col_taxa_sinasc, f"pct_pre_natal_7mais_sinasc_{SINASC_ANO_REFERENCIA}",
                    f"pct_baixo_peso_sinasc_{SINASC_ANO_REFERENCIA}",
                    f"pct_prematuridade_sinasc_{SINASC_ANO_REFERENCIA}",
                    f"idade_media_mae_sinasc_{SINASC_ANO_REFERENCIA}",
                    "classificacao_pressao_materno_infantil",
                ]
                if col in base.columns
            ]
            if colunas_sinasc_top:
                st.dataframe(
                    base.sort_values([col_nascidos_sinasc, "populacao_ibge"], ascending=[False, False], na_position="last")[colunas_sinasc_top].head(25),
                    use_container_width=True,
                    hide_index=True,
                )

            if "classificacao_pressao_materno_infantil" in base.columns:
                resumo_sinasc = (
                    base["classificacao_pressao_materno_infantil"]
                    .fillna("Sem informação")
                    .value_counts()
                    .rename_axis("Classificação materno-infantil")
                    .reset_index(name="Municípios")
                )
                st.markdown("##### Resumo por pressão materno-infantil")
                st.dataframe(resumo_sinasc, use_container_width=True, hide_index=True)

            sinasc_municipal = st.session_state.get("ubs_api_sinasc_municipal")
            if isinstance(sinasc_municipal, pd.DataFrame) and not sinasc_municipal.empty:
                st.download_button(
                    "Baixar consolidado SINASC em Excel",
                    data=_excel_multiplas_abas({
                        "SINASC municipal": sinasc_municipal,
                        "Base municipal": base[colunas_sinasc_top] if colunas_sinasc_top else pd.DataFrame(),
                        "Resumo classificação": resumo_sinasc if "resumo_sinasc" in locals() else pd.DataFrame(),
                    }),
                    file_name=f"consolidado_sinasc_{SINASC_ANO_REFERENCIA}_mt.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )

        col_obitos_sim = f"obitos_sim_{SIM_ANO_REFERENCIA}"
        col_obitos_inf_sim = f"obitos_infantis_sim_{SIM_ANO_REFERENCIA}"
        col_taxa_obitos_sim = f"taxa_obitos_por_1000_hab_sim_{SIM_ANO_REFERENCIA}"
        col_taxa_mi_sim = f"taxa_mortalidade_infantil_por_1000_nv_{SIM_ANO_REFERENCIA}"
        for _col_sim_num in [
            "populacao_ibge",
            col_obitos_sim,
            col_obitos_inf_sim,
            col_taxa_obitos_sim,
            col_taxa_mi_sim,
            f"obitos_menores_5_sim_{SIM_ANO_REFERENCIA}",
            f"obitos_60mais_sim_{SIM_ANO_REFERENCIA}",
        ]:
            if _col_sim_num in base.columns:
                base[_col_sim_num] = _serie_numerica_flexivel(base[_col_sim_num])
        if col_obitos_sim in base.columns and base[col_obitos_sim].notna().any():
            st.markdown("#### Contexto de mortalidade — SIM")
            st.caption(
                "Leitura automática preliminar do Sistema de Informação sobre Mortalidade. "
                "Esse bloco qualifica o contexto sanitário municipal e deve ser interpretado junto com o SINASC, sem definir construção de UBS sozinho."
            )
            total_obitos = int(_soma_numerica_coluna(base, col_obitos_sim))
            total_obitos_inf = int(_soma_numerica_coluna(base, col_obitos_inf_sim)) if col_obitos_inf_sim in base.columns else 0
            _pop_total_sim = _soma_numerica_coluna(base, "populacao_ibge")
            taxa_obitos_media = round((total_obitos / _pop_total_sim) * 1000, 2) if _pop_total_sim else 0
            classif_mi_alta = int(base.get("classificacao_mortalidade_infantil_preliminar", pd.Series(dtype=str)).astype(str).str.contains("Alta", na=False).sum()) if "classificacao_mortalidade_infantil_preliminar" in base.columns else 0
            si1, si2, si3, si4 = st.columns(4)
            si1.metric(f"Óbitos {SIM_ANO_REFERENCIA}", total_obitos)
            si2.metric("Óbitos infantis", total_obitos_inf)
            si3.metric("Óbitos/1.000 hab.", taxa_obitos_media)
            si4.metric("Alta mortalidade infantil preliminar", classif_mi_alta)

            colunas_sim_top = [
                col for col in [
                    "municipio", "regiao_saude_sus", "populacao_ibge", col_obitos_sim,
                    col_taxa_obitos_sim, col_obitos_inf_sim, col_taxa_mi_sim,
                    f"obitos_menores_5_sim_{SIM_ANO_REFERENCIA}",
                    f"obitos_60mais_sim_{SIM_ANO_REFERENCIA}",
                    "classificacao_mortalidade_infantil_preliminar",
                ]
                if col in base.columns
            ]
            if colunas_sim_top:
                st.dataframe(
                    base.sort_values([col_obitos_sim, "populacao_ibge"], ascending=[False, False], na_position="last")[colunas_sim_top].head(25),
                    use_container_width=True,
                    hide_index=True,
                )

            if "classificacao_mortalidade_infantil_preliminar" in base.columns:
                resumo_sim = (
                    base["classificacao_mortalidade_infantil_preliminar"]
                    .fillna("Sem informação")
                    .value_counts()
                    .rename_axis("Classificação mortalidade infantil")
                    .reset_index(name="Municípios")
                )
                st.markdown("##### Resumo por mortalidade infantil preliminar")
                st.dataframe(resumo_sim, use_container_width=True, hide_index=True)

            sim_municipal = st.session_state.get("ubs_api_sim_municipal")
            if isinstance(sim_municipal, pd.DataFrame) and not sim_municipal.empty:
                st.download_button(
                    "Baixar consolidado SIM em Excel",
                    data=_excel_multiplas_abas({
                        "SIM municipal": sim_municipal,
                        "Base municipal": base[colunas_sim_top] if colunas_sim_top else pd.DataFrame(),
                        "Resumo classificação": resumo_sim if "resumo_sim" in locals() else pd.DataFrame(),
                    }),
                    file_name=f"consolidado_sim_{SIM_ANO_REFERENCIA}_mt.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )

        if "classificacao_automatica_ubs" in base.columns:
            resumo_indice = (
                base["classificacao_automatica_ubs"]
                .fillna("Sem informação")
                .value_counts()
                .rename_axis("Classificação automática UBS")
                .reset_index(name="Municípios")
            )
            st.markdown("#### Ranking preliminar automático de UBS")
            st.caption(
                "Este ranking usa somente dados automatizados: população estimada, eSF necessárias pelo parâmetro MS, "
                "densidade territorial, ruralidade do Censo 2022, envelhecimento populacional, vulnerabilidade socioeducacional, dispersão escolar INEP, distritos oficiais IBGE e contagem preliminar CNES/UBS. "
                "Ele não substitui eSF existentes, validação do funcionamento real das UBS, terreno ou análise da Coordenadoria APS."
            )
            st.dataframe(resumo_indice, use_container_width=True, hide_index=True)

            colunas_ranking = [
                coluna for coluna in [
                    "municipio",
                    "regiao_saude_sus",
                    "regiao_imediata_ibge",
                    "populacao_ibge",
                    "esf_necessarias_ms",
                    "densidade_calculada_atual",
                    "percentual_rural_2022",
                    "indice_envelhecimento_2022",
                    "idade_mediana_2022",
                    "beneficiarios_bolsa_familia_por_1000_hab",
                    "classificacao_vulnerabilidade_social_bf",
                    "qtd_distritos_ibge",
                    "qtd_ubs_cnes_automatico",
                    "populacao_por_ubs_cnes_automatico",
                    "pontuacao_automatica_ubs",
                    "classificacao_automatica_ubs",
                    "criterios_automaticos_ubs",
                ]
                if coluna in base.columns
            ]
            st.markdown("##### Top 20 municípios para validação técnica")
            st.dataframe(
                base.sort_values("pontuacao_automatica_ubs", ascending=False)[colunas_ranking].head(20),
                use_container_width=True,
                hide_index=True,
            )


        st.dataframe(base, use_container_width=True, hide_index=True)
        st.download_button(
            "Baixar base automática em Excel",
            data=_excel_bytes(base, "Base UBS"),
            file_name=f"base_automatica_ubs_ibge_ms_{int(base['ano_referencia'].iloc[0])}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    st.markdown("### 4. Fontes para observar, mas não oficializar automaticamente ainda")
    st.caption("Estes testes verificam acesso às páginas/fontes. A extração dos dados ainda deve ser desenhada com validação técnica.")

    if st.button("Testar fontes complementares", use_container_width=True):
        testes = [
            ("CNES - página de documentação", "https://cnes.datasus.gov.br/pages/downloads/documentacao.jsp"),
            ("CNES - dados abertos MS", "https://dadosabertos.saude.gov.br/dataset/cnes-cadastro-nacional-de-estabelecimentos-de-saude"),
            ("CNES - base de dados mensal", "https://cnes.datasus.gov.br/pages/downloads/arquivosBaseDados.jsp"),
            ("API Dados Abertos Saúde", "https://apidadosabertos.saude.gov.br/"),
            ("DATASUS - transferência de arquivos", "https://datasus.saude.gov.br/transferencia-de-arquivos/"),
            ("e-Gestor APS - relatórios públicos", "https://egestorab.saude.gov.br/paginas/acessoPublico/relatorios/relatoriosPublicos.xhtml"),
        ]
        resultados = [testar_endpoint(nome, url) for nome, url in testes]
        st.dataframe(pd.DataFrame(resultados), use_container_width=True, hide_index=True)

    with st.expander("Decisão metodológica deste módulo", expanded=True):
        st.markdown(
            """
            - **Automatizar agora:** IBGE Localidades, regiões geográficas imediatas/intermediárias do IBGE, distritos oficiais do IBGE, IBGE/SIDRA população estimada do ano selecionado, área territorial, densidade demográfica, perfil urbano/rural do Censo 2022, indicadores demográficos sintéticos do Censo 2022, regiões de saúde do SUS, CNES/Dados Abertos para contagem preliminar e detalhamento de UBS/USF, Hospitais e Leitos como contexto assistencial complementar, SINASC como contexto materno-infantil, SIM como contexto de mortalidade e teste de disponibilidade da malha geográfica do IBGE.
            - **Manter por upload/validação:** eSF existentes, validação da rede CNES pela SES/Coordenadoria APS, informações de terreno e infraestrutura.
            - **Investigar depois:** rotina automática CNES/DATASUS para equipes, cobertura APS/e-Gestor e indicadores sanitários como ICSAP.
            - **Não oficializar sem validação:** qualquer extração automática de equipes que não esteja conferida com a Coordenadoria APS.
            - **Novas informações geradas:** classificação territorial preliminar por densidade, classificação de ruralidade preliminar pelo percentual de população rural do Censo 2022, classificação preliminar por quantidade de distritos oficiais IBGE, pressão preliminar por UBS/USF cadastrada no CNES, qualificação preliminar por tipo de unidade/natureza/atendimento SUS, contexto materno-infantil pelo SINASC, contexto de mortalidade pelo SIM e ranking automático preliminar para orientar a validação técnica. Esses dados não substituem a população estimada usada no cálculo MS nem dispensam validação pela Coordenadoria APS.
            """
        )
