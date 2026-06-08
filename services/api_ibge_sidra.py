from __future__ import annotations

import re
from typing import Any

import pandas as pd
import requests

UF_MT = "51"
IBGE_LOCALIDADES = "https://servicodados.ibge.gov.br/api/v1/localidades"
IBGE_AGREGADOS = "https://servicodados.ibge.gov.br/api/v3/agregados"
TIMEOUT = 45


def _get_json(url: str) -> Any:
    resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "aps-inteligencia-ses-mt/0.4"})
    resp.raise_for_status()
    return resp.json()


def _somente_numero(valor: Any) -> str:
    return re.sub(r"\D+", "", str(valor or ""))


def _to_float(valor: Any) -> float | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    if texto in {"", "-", "...", "X", "x"}:
        return None
    texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def _limpar_nome_municipio(nome: str) -> str:
    nome = str(nome or "").strip()
    nome = re.sub(r"\s*-\s*MT$", "", nome, flags=re.I)
    return nome


def _series_sidra_para_dataframe(payload: Any, indicador: str, ano_padrao: int | None = None) -> pd.DataFrame:
    """Converte o formato padrão da API de agregados/SIDRA em uma tabela simples.

    Saída padronizada para importação como indicadores_municipais:
    municipio | codigo_ibge | ano | competencia | indicador | valor | fonte
    """
    if not payload:
        return pd.DataFrame(columns=["municipio", "codigo_ibge", "ano", "competencia", "indicador", "valor", "fonte"])

    bloco = payload[0]
    resultados = bloco.get("resultados", [])
    linhas: list[dict[str, Any]] = []

    for resultado in resultados:
        for serie in resultado.get("series", []):
            localidade = serie.get("localidade", {})
            codigo = _somente_numero(localidade.get("id"))
            if not codigo.startswith(UF_MT):
                continue
            municipio = _limpar_nome_municipio(localidade.get("nome", ""))
            valores = serie.get("serie", {}) or {}
            for ano, valor in valores.items():
                linhas.append(
                    {
                        "municipio": municipio,
                        "codigo_ibge": codigo,
                        "ano": int(_somente_numero(ano) or (ano_padrao or 0)),
                        "competencia": str(ano),
                        "indicador": indicador,
                        "valor": _to_float(valor),
                        "fonte": "IBGE/SIDRA",
                    }
                )
    return pd.DataFrame(linhas)


def _carregar_sidra(agregado: str, periodo: int, variavel: str, indicador: str, query_extra: str = "") -> pd.DataFrame:
    query_extra = query_extra or ""
    if query_extra and not query_extra.startswith("&"):
        query_extra = "&" + query_extra
    # N6[N3[51]] solicita municípios pertencentes ao estado de MT.
    url = f"{IBGE_AGREGADOS}/{agregado}/periodos/{periodo}/variaveis/{variavel}?localidades=N6[N3[{UF_MT}]]{query_extra}"
    payload = _get_json(url)
    df = _series_sidra_para_dataframe(payload, indicador=indicador, ano_padrao=periodo)
    df["url_origem"] = url
    return df



def _base_indicador_vazia_por_municipio(indicador: str, fonte: str, observacao: str, ano: int = 2022) -> pd.DataFrame:
    """Retorna uma base municipal completa, mesmo quando o endpoint externo falha.

    Uso: manter rastreabilidade e permitir que o bloco socioeconômico avance sem
    corromper a consolidação. Os valores ficam nulos e podem ser recarregados
    posteriormente quando o IBGE/SIDRA estabilizar.
    """
    municipios = carregar_municipios_ibge_mt()
    linhas: list[dict[str, Any]] = []
    for row in municipios.to_dict("records"):
        linhas.append({
            "municipio": row.get("municipio"),
            "codigo_ibge": str(row.get("codigo_ibge", "")),
            "ano": int(ano),
            "competencia": str(ano),
            "indicador": indicador,
            "valor": None,
            "fonte": fonte,
            "observacao": observacao,
            "status_api": "pendente_reprocessamento",
        })
    return pd.DataFrame(linhas)


def _carregar_sidra_resiliente(agregado: str, periodo: int, variavel: str, indicador: str, nome_base: str) -> pd.DataFrame:
    """Carrega SIDRA; se a API retornar 500/instabilidade, gera base nula rastreável.

    Isso evita que uma instabilidade temporária do IBGE bloqueie a reconstrução do
    sistema. A base fica marcada para reprocessamento posterior.
    """
    try:
        df = _carregar_sidra(agregado, int(periodo), variavel, indicador)
        if isinstance(df, pd.DataFrame) and not df.empty:
            df["status_api"] = "carregado"
            return df
        return _base_indicador_vazia_por_municipio(
            indicador=indicador,
            fonte="IBGE/SIDRA",
            observacao=f"{nome_base}: endpoint retornou vazio. Manter para reprocessamento posterior.",
            ano=int(periodo),
        )
    except Exception as exc:
        return _base_indicador_vazia_por_municipio(
            indicador=indicador,
            fonte="IBGE/SIDRA",
            observacao=f"{nome_base}: falha temporária no endpoint SIDRA/agregado {agregado}. Erro: {exc}",
            ano=int(periodo),
        )


def _buscar_numero_por_chave(obj: Any, termos: list[str]) -> float | None:
    """Busca de forma tolerante um número em estruturas JSON com chaves variadas."""
    if isinstance(obj, dict):
        for chave, valor in obj.items():
            chave_norm = str(chave).lower()
            if any(termo in chave_norm for termo in termos):
                convertido = _to_float(valor)
                if convertido is not None:
                    return convertido
            encontrado = _buscar_numero_por_chave(valor, termos)
            if encontrado is not None:
                return encontrado
    elif isinstance(obj, list):
        for item in obj:
            encontrado = _buscar_numero_por_chave(item, termos)
            if encontrado is not None:
                return encontrado
    return None


def _carregar_area_por_metadados_malha(populacao_2022: pd.DataFrame | None = None) -> pd.DataFrame:
    """Fallback oficial via metadados da malha do IBGE.

    O endpoint de Malhas Geográficas possui metadados por município. A estrutura do
    JSON pode variar, então a função procura campos relacionados a área de forma
    tolerante. Quando a área for encontrada e houver população de 2022 disponível,
    calcula também uma densidade aproximada.
    """
    municipios = carregar_municipios_ibge_mt()
    pop_map: dict[str, float] = {}
    if isinstance(populacao_2022, pd.DataFrame) and not populacao_2022.empty:
        aux = populacao_2022[populacao_2022.get("indicador", "") == "populacao_estimada"].copy() if "indicador" in populacao_2022.columns else populacao_2022.copy()
        for row in aux.to_dict("records"):
            codigo = str(row.get("codigo_ibge", ""))
            valor = _to_float(row.get("valor"))
            if codigo and valor is not None:
                pop_map[codigo] = valor

    linhas: list[dict[str, Any]] = []
    for row in municipios.to_dict("records"):
        codigo = str(row.get("codigo_ibge", ""))
        municipio = row.get("municipio", "")
        area = None
        url = f"https://servicodados.ibge.gov.br/api/v3/malhas/municipios/{codigo}/metadados"
        try:
            meta = _get_json(url)
            area = _buscar_numero_por_chave(meta, ["area", "área"])
        except Exception:
            area = None

        populacao = pop_map.get(codigo)
        densidade = None
        if area not in (None, 0) and populacao is not None:
            densidade = round(float(populacao) / float(area), 4)

        linhas.append({
            "municipio": municipio,
            "codigo_ibge": codigo,
            "ano": 2022,
            "competencia": "2022",
            "indicador": "area_territorial_km2",
            "valor": area,
            "area_km2": area,
            "populacao_referencia": populacao,
            "densidade_calculada_hab_km2": densidade,
            "fonte": "IBGE Malhas/Metadados",
            "url_origem": url,
            "observacao": "Fallback por metadados da malha; quando área não for localizada, manter para posterior complementação.",
        })

        linhas.append({
            "municipio": municipio,
            "codigo_ibge": codigo,
            "ano": 2022,
            "competencia": "2022",
            "indicador": "densidade_demografica_calculada_hab_km2",
            "valor": densidade,
            "area_km2": area,
            "populacao_referencia": populacao,
            "densidade_calculada_hab_km2": densidade,
            "fonte": "IBGE Malhas/Metadados + população IBGE",
            "url_origem": url,
            "observacao": "Densidade calculada somente quando área e população estiverem disponíveis.",
        })
    return pd.DataFrame(linhas)

def carregar_municipios_ibge_mt() -> pd.DataFrame:
    url = f"{IBGE_LOCALIDADES}/estados/{UF_MT}/municipios"
    dados = _get_json(url)
    linhas = []
    for item in dados:
        micro = item.get("microrregiao", {}) or {}
        meso = micro.get("mesorregiao", {}) or {}
        uf = meso.get("UF", {}) or {}
        regiao = uf.get("regiao", {}) or {}
        linhas.append(
            {
                "codigo_ibge": str(item.get("id", "")),
                "municipio": item.get("nome", ""),
                "microrregiao": micro.get("nome", ""),
                "mesorregiao": meso.get("nome", ""),
                "uf": uf.get("sigla", "MT"),
                "regiao_brasil": regiao.get("nome", ""),
                "fonte": "IBGE Localidades",
            }
        )
    return pd.DataFrame(linhas).sort_values("municipio")


def carregar_distritos_ibge_mt() -> pd.DataFrame:
    url = f"{IBGE_LOCALIDADES}/estados/{UF_MT}/distritos"
    dados = _get_json(url)
    linhas = []
    for item in dados:
        municipio = item.get("municipio", {}) or {}
        linhas.append(
            {
                "codigo_distrito": str(item.get("id", "")),
                "distrito": item.get("nome", ""),
                "codigo_ibge": str(municipio.get("id", "")),
                "municipio": municipio.get("nome", ""),
                "fonte": "IBGE Localidades",
            }
        )
    return pd.DataFrame(linhas).sort_values(["municipio", "distrito"])


def carregar_regioes_geograficas_ibge_mt() -> pd.DataFrame:
    """Retorna uma camada territorial IBGE compatível com versões antigas.

    Em alguns endpoints novos do IBGE, região imediata/intermediária nem sempre vem
    de forma estável para todos os municípios. Por isso a v04 usa microrregião e
    mesorregião como camada territorial inicial, suficiente para diagnóstico e
    conferência. A camada de Região de Saúde continuará vindo da base MS/SES.
    """
    municipios = carregar_municipios_ibge_mt()
    if municipios.empty:
        return municipios
    out = municipios[["codigo_ibge", "municipio", "microrregiao", "mesorregiao", "fonte"]].copy()
    out["ano"] = 2026
    out["competencia"] = "referencia"
    out["indicador"] = "regioes_geograficas_ibge"
    out["valor"] = None
    return out


def carregar_populacao_sidra_mt(ano: int = 2025) -> pd.DataFrame:
    # Agregado 6579, variável 9324: população residente estimada.
    return _carregar_sidra("6579", int(ano), "9324", "populacao_estimada")


def carregar_area_densidade_sidra_mt(ano_censo: int = 2022) -> pd.DataFrame:
    """Área territorial e densidade municipal com fallback resiliente.

    A v04.1 evita que o bloco territorial seja interrompido quando a tabela/variável
    SIDRA de área/densidade não responder ou mudar de estrutura. Primeiro tenta o
    caminho SIDRA usado na v04; se falhar, monta uma base territorial a partir dos
    metadados de malha do IBGE.
    """
    erros: list[str] = []

    try:
        df = _carregar_sidra("4713", int(ano_censo), "93", "area_densidade_territorial")
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df
        erros.append("SIDRA 4713 retornou vazio")
    except Exception as exc:
        erros.append(f"SIDRA 4713: {exc}")

    populacao_2022 = pd.DataFrame()
    try:
        populacao_2022 = carregar_populacao_sidra_mt(ano=2022)
    except Exception as exc:
        erros.append(f"população 2022 para densidade: {exc}")

    fallback = _carregar_area_por_metadados_malha(populacao_2022=populacao_2022)
    if not fallback.empty:
        fallback["erro_sidra_original"] = " | ".join(erros)
        return fallback

    raise RuntimeError("Não foi possível carregar área/densidade. " + " | ".join(erros))


def carregar_urbano_rural_sidra_mt(ano_censo: int = 2022) -> pd.DataFrame:
    # Estrutura inicial: população por situação do domicílio. Pode variar por disponibilidade do SIDRA.
    return _carregar_sidra("9514", int(ano_censo), "93", "perfil_urbano_rural")


def carregar_indicadores_demograficos_sidra_mt(ano_censo: int = 2022) -> pd.DataFrame:
    return _carregar_sidra_resiliente("9515", int(ano_censo), "93", "indicadores_demograficos_9515", "Demografia — envelhecimento e idade mediana")


def carregar_alfabetizacao_sidra_mt(ano_censo: int = 2022) -> pd.DataFrame:
    return _carregar_sidra_resiliente("9543", int(ano_censo), "93", "alfabetizacao_9543", "Taxa de alfabetização")


def carregar_nivel_instrucao_sidra_mt(ano_censo: int = 2022) -> pd.DataFrame:
    return _carregar_sidra_resiliente("10061", int(ano_censo), "93", "nivel_instrucao_10061", "Nível de instrução")


def carregar_renda_censo_sidra_mt(ano_censo: int = 2022) -> pd.DataFrame:
    return _carregar_sidra_resiliente("9517", int(ano_censo), "93", "renda_censo_2022", "Renda — Censo 2022")


def carregar_saneamento_sidra_mt(ano_censo: int = 2022) -> pd.DataFrame:
    return _carregar_sidra_resiliente("6732", int(ano_censo), "93", "saneamento_censo_2022", "Saneamento")


def carregar_povos_tradicionais_ibge_mt(ano_censo: int = 2022) -> pd.DataFrame:
    # Mantém saída padronizada; alguns municípios podem não ter valor por indisponibilidade agregada.
    try:
        indigenas = _carregar_sidra("9605", int(ano_censo), "93", "populacao_indigena_censo_2022")
    except Exception:
        indigenas = pd.DataFrame()
    try:
        quilombolas = _carregar_sidra("9606", int(ano_censo), "93", "populacao_quilombola_censo_2022")
    except Exception:
        quilombolas = pd.DataFrame()
    return pd.concat([indigenas, quilombolas], ignore_index=True)
