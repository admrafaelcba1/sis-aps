from __future__ import annotations

from datetime import datetime
from math import cos, pi
from pathlib import Path
import io
import json
import tempfile
import time
import unicodedata
import zipfile
from typing import Any

import pandas as pd
import numpy as np
import requests

from config.municipios_mt import DEFAULT_MUNICIPIOS
from config.settings import GEO_DIR
from database.connection import db_session, get_connection
from services.qualidade_dados_service import deduplicar_estabelecimentos_saude, validar_municipio_geografico_pontos, diagnosticar_qualidade_geografica_territorios, resumo_duplicidades_estabelecimentos, validar_pontos_mapa_estrategico

IBGE_UF_MT = "51"
MALHAS_URL = "https://servicodados.ibge.gov.br/api/v3/malhas/municipios/{codigo}"
LOCALIDADES_URL = f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{IBGE_UF_MT}/municipios"

CACHE_GEO_MUNICIPAL = GEO_DIR / "municipios_mt_georreferencia.csv"
UBS_COORDENADAS_SISTEMA_ANTIGO = Path("data/reference/ubs_georreferenciadas_sistema_antigo.csv")
UBS_COORDENADAS_VALIDADAS_MANUAIS = Path("data/reference/ubs_coordenadas_validadas.csv")
BAIRROS_LOCALIDADES_SISTEMA_ANTIGO = Path("data/reference/bairros_localidades_setores_sistema_antigo.csv")
BAIRROS_LOCALIDADES_NOMEADAS_CACHE = Path("data/reference/bairros_localidades_setores_nomeados.csv")

AJUSTES_TERRITORIAIS_MANUAIS = Path("data/reference/ajustes_territorios_municipio.csv")
DEPARA_SETOR_BAIRRO_LOCALIDADE = Path("data/reference/depara_setor_bairro_localidade.csv")
CACHE_IBGE_SETOR_BAIRRO_2022_MT = Path("data/reference/ibge_setores_bairros_2022_mt.csv")
OSM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"


# Ajustes de compatibilidade entre grafias oficiais em diferentes bases.
# Ex.: algumas bases do IBGE retornam "Santo Antônio de Leverger", enquanto
# a relação interna/SES usa "Santo Antônio do Leverger". A chave normalizada
# permite consolidar sem criar município duplicado ou deixar dados pendentes.
ALIASES_CHAVE_MUNICIPIO = {
    "SANTO ANTONIO DE LEVERGER": "SANTO ANTONIO DO LEVERGER",
}

CODIGOS_IBGE_FALLBACK = {
    "SANTO ANTONIO DO LEVERGER": "5107809",
}


def _normalizar_alias_chave(chave: str) -> str:
    return ALIASES_CHAVE_MUNICIPIO.get(chave, chave)


def _chave(valor: Any) -> str:
    texto = str(valor or "").strip().upper()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    return " ".join(texto.split())


def _codigo_limpo(valor: Any) -> str:
    import re
    digitos = re.sub(r"\D", "", "" if valor is None else str(valor))
    if len(digitos) >= 7:
        return digitos[:7]
    return digitos


def _texto_valido(valor: Any) -> str:
    texto = str(valor or "").strip()
    if texto.lower() in {"nan", "none", "null", "não informado", "nao informado"}:
        return ""
    return " ".join(texto.split())


def _eh_rotulo_generico_setor(valor: Any) -> bool:
    import re
    texto = _texto_valido(valor)
    if not texto:
        return True
    return bool(re.fullmatch(r"(?i)setor\s+\d+", texto)) or _chave(texto).startswith("SETOR CENSITARIO")


def _primeiro_valor_colunas(row: pd.Series, colunas: list[str]) -> str:
    for c in colunas:
        if c in row.index:
            v = _texto_valido(row.get(c))
            if v and not _eh_rotulo_generico_setor(v):
                return v
    return ""


def _padronizar_base_nomes_territorio(df: pd.DataFrame, fonte_padrao: str) -> pd.DataFrame:
    """Padroniza bases opcionais de de-para setor -> bairro/localidade.

    Aceita arquivos manuais com colunas flexíveis, por exemplo:
    setor_censitario; municipio; bairro
    setor_censitario; municipio; nome_bairro
    setor_censitario; municipio; nome_territorio
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["setor_censitario", "codigo_ibge", "municipio", "nome_territorio_exibicao", "fonte_nome_territorio", "tipo_territorio_ajustado"])
    base = df.copy()
    base.columns = [str(c).strip() for c in base.columns]
    for c in ["setor_censitario", "codigo_ibge", "municipio"]:
        if c not in base.columns:
            base[c] = ""
    base["setor_censitario"] = base["setor_censitario"].astype(str).str.strip()
    base["codigo_ibge"] = base["codigo_ibge"].map(_codigo_limpo)
    base["municipio"] = base["municipio"].astype(str).str.replace(" - MT", "", regex=False).str.strip()

    colunas_nome = [
        "nome_territorio_exibicao", "nome_territorio", "nome_bairro", "bairro", "bairro_ou_localidade",
        "localidade", "nm_bairro", "NM_BAIRRO", "nome", "rotulo", "territorio"
    ]
    base["nome_territorio_exibicao"] = base.apply(lambda r: _primeiro_valor_colunas(r, colunas_nome), axis=1)
    if "fonte_nome_territorio" not in base.columns:
        base["fonte_nome_territorio"] = fonte_padrao
    else:
        base["fonte_nome_territorio"] = base["fonte_nome_territorio"].map(lambda x: _texto_valido(x) or fonte_padrao)
    if "tipo_territorio_ajustado" not in base.columns:
        if "tipo_territorio" in base.columns:
            base["tipo_territorio_ajustado"] = base["tipo_territorio"].map(lambda x: _texto_valido(x) or "Bairro/localidade aproximado")
        else:
            base["tipo_territorio_ajustado"] = "Bairro/localidade aproximado"
    base = base[base["nome_territorio_exibicao"].astype(str).str.strip().ne("")].copy()
    return base[["setor_censitario", "codigo_ibge", "municipio", "nome_territorio_exibicao", "fonte_nome_territorio", "tipo_territorio_ajustado"]].drop_duplicates("setor_censitario", keep="last")




# URLs oficiais do IBGE para a malha de setores censitários de 2022 em Mato Grosso.
# A malha de setores possui os atributos CD_BAIRRO/NM_BAIRRO, além de núcleo urbano,
# distrito, subdistrito, favela/comunidade urbana e aglomerado rural quando disponíveis.
IBGE_SETOR_2022_MT_URLS = [
    "https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/malhas_de_setores_censitarios__divisoes_intramunicipais/censo_2022/setores/gpkg/UF/MT_setores_CD2022.gpkg",
    "https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/malhas_de_setores_censitarios__divisoes_intramunicipais/censo_2022/setores/shp/UF/MT_setores_CD2022.zip",
    "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/malha_com_atributos/setores/gpkg/UF/MT/MT_setores_CD2022.gpkg",
    "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/malha_com_atributos/setores/shp/UF/MT/MT_setores_CD2022.zip",
]


def _inferir_coluna_por_nomes(colunas: list[str], candidatos: list[str]) -> str | None:
    mapa = {str(c).strip().upper(): c for c in colunas}
    for cand in candidatos:
        if cand.upper() in mapa:
            return mapa[cand.upper()]
    for c in colunas:
        cu = str(c).strip().upper()
        if any(cand.upper() in cu for cand in candidatos):
            return c
    return None


def _primeiro_nome_territorial_ibge(row: pd.Series) -> tuple[str, str, str]:
    """Escolhe o melhor nome territorial oficial disponível na linha da malha IBGE."""
    ordem = [
        ("NM_BAIRRO", "CD_BAIRRO", "Bairro IBGE 2022"),
        ("NM_NU", "CD_NU", "Núcleo urbano IBGE 2022"),
        ("NM_FCU", "CD_FCU", "Favela/Comunidade urbana IBGE 2022"),
        ("NM_AGLOM", "CD_AGLOM", "Aglomerado rural IBGE 2022"),
        ("NM_AGLOMERADO", "CD_AGLOMERADO", "Aglomerado rural IBGE 2022"),
        ("NM_SUBDIST", "CD_SUBDIST", "Subdistrito IBGE 2022"),
        ("NM_DIST", "CD_DIST", "Distrito IBGE 2022"),
    ]
    municipio = _chave(row.get("NM_MUN", row.get("municipio", "")))
    for nome_col, cod_col, tipo in ordem:
        # Alguns shapefiles podem vir com variações de nome de coluna.
        nome = ""
        codigo = ""
        for c in row.index:
            cu = str(c).strip().upper().replace(" ", "")
            if cu == nome_col.replace(" ", ""):
                nome = _texto_valido(row.get(c))
            if cu == cod_col.replace(" ", ""):
                codigo = _texto_valido(row.get(c))
        if nome and not _eh_rotulo_generico_setor(nome) and _chave(nome) != municipio:
            return nome, tipo, codigo
    return "", "", ""


def _ler_malha_setores_ibge_2022_mt() -> pd.DataFrame:
    """Lê a malha de setores do IBGE 2022/MT e extrai setor -> bairro/localidade.

    A função tenta, nesta ordem:
    1) arquivos locais em data/geo ou data/reference;
    2) URLs oficiais do IBGE.
    Retorna DataFrame vazio se faltar geopandas/pyogrio/shapely ou se o ambiente
    estiver sem acesso aos arquivos oficiais.
    """
    try:
        import geopandas as gpd  # type: ignore
    except Exception:
        return pd.DataFrame()

    candidatos_locais: list[Path] = []
    for pasta in [Path("data/geo"), Path("data/reference")]:
        if pasta.exists():
            candidatos_locais.extend(sorted(pasta.glob("*setores*2022*.gpkg")))
            candidatos_locais.extend(sorted(pasta.glob("*setores*CD2022*.gpkg")))
            candidatos_locais.extend(sorted(pasta.glob("*setores*2022*.zip")))
            candidatos_locais.extend(sorted(pasta.glob("*setores*CD2022*.zip")))
            candidatos_locais.extend(sorted(pasta.glob("*setores*.shp")))

    def _processar_gdf(gdf) -> pd.DataFrame:
        if gdf is None or len(gdf) == 0:
            return pd.DataFrame()
        cols = list(gdf.columns)
        col_setor = _inferir_coluna_por_nomes(cols, ["CD_SETOR", "COD_SETOR", "GEOCODIGO", "SETOR"])
        col_mun = _inferir_coluna_por_nomes(cols, ["CD_MUN", "CD_MUNICIP", "COD_MUN", "CODMUN"])
        col_nm_mun = _inferir_coluna_por_nomes(cols, ["NM_MUN", "NM_MUNICIP", "MUNICIPIO"])
        if not col_setor:
            return pd.DataFrame()
        atributos = gdf.drop(columns=["geometry"], errors="ignore").copy()
        atributos["setor_censitario"] = atributos[col_setor].astype(str).str.replace(r"\D", "", regex=True).str.zfill(15)
        if col_mun:
            atributos["codigo_ibge"] = atributos[col_mun].astype(str).str.replace(r"\D", "", regex=True).str[:7]
        else:
            atributos["codigo_ibge"] = atributos["setor_censitario"].str[:7]
        if col_nm_mun:
            atributos["municipio"] = atributos[col_nm_mun].astype(str).str.strip()
        elif "municipio" not in atributos.columns:
            atributos["municipio"] = ""
        nomes = atributos.apply(_primeiro_nome_territorial_ibge, axis=1, result_type="expand")
        atributos["nome_territorio_exibicao"] = nomes[0]
        atributos["tipo_territorio_ajustado"] = nomes[1]
        atributos["codigo_territorio_ibge"] = nomes[2]
        atributos = atributos[atributos["nome_territorio_exibicao"].astype(str).str.strip().ne("")].copy()
        if atributos.empty:
            return pd.DataFrame()
        atributos["fonte_nome_territorio"] = "IBGE Malha de Setores Censitários 2022 — atributos territoriais CD_BAIRRO/NM_BAIRRO e correlatos"
        return atributos[[
            "setor_censitario", "codigo_ibge", "municipio", "nome_territorio_exibicao",
            "fonte_nome_territorio", "tipo_territorio_ajustado", "codigo_territorio_ibge"
        ]].drop_duplicates("setor_censitario", keep="last")

    for arq in candidatos_locais:
        try:
            if arq.suffix.lower() == ".zip":
                with tempfile.TemporaryDirectory() as td:
                    with zipfile.ZipFile(arq) as zf:
                        zf.extractall(td)
                    arquivos = list(Path(td).rglob("*.shp")) + list(Path(td).rglob("*.gpkg"))
                    if not arquivos:
                        continue
                    gdf = gpd.read_file(sorted(arquivos)[0])
            else:
                gdf = gpd.read_file(arq)
            out = _processar_gdf(gdf)
            if not out.empty:
                return out
        except Exception:
            continue

    headers = {"User-Agent": "plataforma-aps-inteligencia-ses-mt/1.0"}
    for url in IBGE_SETOR_2022_MT_URLS:
        try:
            resp = requests.get(url, timeout=300, headers=headers)
            if resp.status_code != 200 or not resp.content or b"<html" in resp.content[:500].lower():
                continue
            with tempfile.TemporaryDirectory() as td:
                td_path = Path(td)
                if url.lower().endswith(".gpkg"):
                    arq = td_path / "MT_setores_CD2022.gpkg"
                    arq.write_bytes(resp.content)
                    gdf = gpd.read_file(arq)
                elif zipfile.is_zipfile(io.BytesIO(resp.content)):
                    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                        zf.extractall(td_path)
                    arquivos = list(td_path.rglob("*.shp")) + list(td_path.rglob("*.gpkg"))
                    if not arquivos:
                        continue
                    arquivos = sorted(arquivos, key=lambda p: (0 if "setor" in p.name.lower() else 1, p.name.lower()))
                    gdf = gpd.read_file(arquivos[0])
                else:
                    continue
            out = _processar_gdf(gdf)
            if not out.empty:
                return out
        except Exception:
            continue
    return pd.DataFrame()


def atualizar_nomes_bairros_ibge_2022_mt() -> dict:
    """Atualiza o cache setor -> bairro/localidade a partir da malha oficial do IBGE."""
    df = _ler_malha_setores_ibge_2022_mt()
    if df.empty:
        return {
            "ok": False,
            "mensagem": "Não foi possível ler a malha oficial de setores do IBGE. Verifique internet e dependências geopandas/pyogrio/shapely.",
            "cache": str(CACHE_IBGE_SETOR_BAIRRO_2022_MT),
            "registros": 0,
        }
    CACHE_IBGE_SETOR_BAIRRO_2022_MT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(CACHE_IBGE_SETOR_BAIRRO_2022_MT, index=False, encoding="utf-8-sig")
    return {
        "ok": True,
        "mensagem": "Cache oficial IBGE atualizado com sucesso.",
        "cache": str(CACHE_IBGE_SETOR_BAIRRO_2022_MT),
        "registros": int(len(df)),
        "municipios": int(df["codigo_ibge"].nunique()) if "codigo_ibge" in df.columns else 0,
    }


def _carregar_nomes_bairros_ibge_2022_mt_cache(auto_atualizar: bool = True) -> pd.DataFrame:
    if CACHE_IBGE_SETOR_BAIRRO_2022_MT.exists():
        try:
            return _padronizar_base_nomes_territorio(
                pd.read_csv(CACHE_IBGE_SETOR_BAIRRO_2022_MT, dtype=str),
                "IBGE Malha de Setores Censitários 2022"
            )
        except Exception:
            pass
    if auto_atualizar:
        ret = atualizar_nomes_bairros_ibge_2022_mt()
        if ret.get("ok") and CACHE_IBGE_SETOR_BAIRRO_2022_MT.exists():
            try:
                return _padronizar_base_nomes_territorio(
                    pd.read_csv(CACHE_IBGE_SETOR_BAIRRO_2022_MT, dtype=str),
                    "IBGE Malha de Setores Censitários 2022"
                )
            except Exception:
                return pd.DataFrame(columns=["setor_censitario", "codigo_ibge", "municipio", "nome_territorio_exibicao", "fonte_nome_territorio", "tipo_territorio_ajustado"])
    return pd.DataFrame(columns=["setor_censitario", "codigo_ibge", "municipio", "nome_territorio_exibicao", "fonte_nome_territorio", "tipo_territorio_ajustado"])

def _carregar_nomes_territorios_opcionais() -> pd.DataFrame:
    bases = []

    # 1) Fonte oficial preferencial: atributos territoriais da malha de setores censitários do IBGE 2022.
    #    Essa fonte evita o erro de usar bairro da UBS mais próxima e permite uso em âmbito estadual.
    try:
        bases.append(_carregar_nomes_bairros_ibge_2022_mt_cache(auto_atualizar=True))
    except Exception:
        pass

    # 2) Fonte manual/validada: prevalece sobre o IBGE quando a equipe estadual/municipal corrigir nomes.
    if DEPARA_SETOR_BAIRRO_LOCALIDADE.exists():
        try:
            bases.append(_padronizar_base_nomes_territorio(pd.read_csv(DEPARA_SETOR_BAIRRO_LOCALIDADE, dtype=str), "de-para manual setor/bairro/localidade"))
        except Exception:
            pass

    bases = [b for b in bases if b is not None and not b.empty]
    if not bases:
        return pd.DataFrame(columns=["setor_censitario", "codigo_ibge", "municipio", "nome_territorio_exibicao", "fonte_nome_territorio", "tipo_territorio_ajustado"])
    return pd.concat(bases, ignore_index=True).drop_duplicates("setor_censitario", keep="last")



def _distancia_km_simples(lat1, lon1, lat2, lon2) -> float:
    try:
        lat1 = float(lat1); lon1 = float(lon1); lat2 = float(lat2); lon2 = float(lon2)
        if any(pd.isna(x) for x in [lat1, lon1, lat2, lon2]):
            return 0.0
        r = 6371.0088
        p1 = np.radians(lat1); p2 = np.radians(lat2)
        dlat = np.radians(lat2 - lat1); dlon = np.radians(lon2 - lon1)
        a = np.sin(dlat/2)**2 + np.cos(p1)*np.cos(p2)*np.sin(dlon/2)**2
        return float(2*r*np.arcsin(np.sqrt(a)))
    except Exception:
        return 0.0


def _marcar_nomes_ibge_amplos_para_validacao(out: pd.DataFrame) -> pd.DataFrame:
    """Evita que atributos amplos do IBGE sejam exibidos como bairro/localidade precisa.

    Algumas bases de setor trazem nomes em campos como NM_BAIRRO/CD_BAIRRO que
    podem representar um recorte territorial amplo, distrito, zona ou agrupamento
    cadastral. Quando o mesmo nome aparece em pontos muito distantes dentro do
    município, o sistema não deve apresentá-lo como se fosse bairro/localidade
    precisa. Nesses casos, volta para o identificador do setor e mantém o nome
    original apenas como informação para validação.
    """
    if out is None or out.empty:
        return out
    if not {"municipio", "nome_territorio_exibicao", "fonte_nome_territorio", "latitude", "longitude", "setor_censitario"}.issubset(out.columns):
        return out

    out = out.copy()
    out["nome_territorio_ibge_original"] = ""
    out["alerta_nome_territorio"] = out.get("alerta_nome_territorio", "")

    fonte_ibge = out["fonte_nome_territorio"].astype(str).str.contains("IBGE Malha de Setores", case=False, na=False)
    if not fonte_ibge.any():
        return out

    aux = out[fonte_ibge].copy()
    aux["_lat"] = pd.to_numeric(aux["latitude"], errors="coerce")
    aux["_lon"] = pd.to_numeric(aux["longitude"], errors="coerce")
    aux = aux[aux["_lat"].notna() & aux["_lon"].notna()].copy()
    if aux.empty:
        return out

    suspeitos = set()
    for (mun, nome), g in aux.groupby(["municipio", "nome_territorio_exibicao"], dropna=False):
        nome_txt = _texto_valido(nome)
        if not nome_txt or _eh_rotulo_generico_setor(nome_txt) or len(g) < 4:
            continue
        lat_min, lat_max = g["_lat"].min(), g["_lat"].max()
        lon_min, lon_max = g["_lon"].min(), g["_lon"].max()
        span = _distancia_km_simples(lat_min, lon_min, lat_max, lon_max)
        # Acima de 12 km de espalhamento, o nome deixa de ser tratado como
        # bairro/localidade precisa. Em Cuiabá isso corrige casos como
        # "Coxipó da Ponte" aparecendo em pontos rurais dispersos.
        if span >= 12:
            suspeitos.add((mun, nome))

    if not suspeitos:
        return out

    mask = pd.Series(False, index=out.index)
    for mun, nome in suspeitos:
        mask = mask | ((out["municipio"].astype(str) == str(mun)) & (out["nome_territorio_exibicao"].astype(str) == str(nome)) & fonte_ibge)

    out.loc[mask, "nome_territorio_ibge_original"] = out.loc[mask, "nome_territorio_exibicao"].astype(str)
    out.loc[mask, "alerta_nome_territorio"] = out.loc[mask, "nome_territorio_ibge_original"].map(
        lambda n: f"Nome territorial amplo do IBGE a validar: {n}. Exibição substituída pelo setor para evitar interpretação como bairro/localidade precisa."
    )
    out.loc[mask, "nome_territorio_exibicao"] = out.loc[mask, "setor_censitario"].map(
        lambda x: f"Setor censitário {x} — nome territorial a validar"
    )
    out.loc[mask, "fonte_nome_territorio"] = "IBGE: nome territorial amplo detectado automaticamente; validar com município/ERS"
    out.loc[mask, "nome_territorio_validado"] = False
    return out

def _aplicar_nome_amigavel_territorios(out: pd.DataFrame) -> pd.DataFrame:
    if out.empty:
        return out
    out = out.copy()
    nomes = _carregar_nomes_territorios_opcionais()
    out["bairro_ou_localidade_original"] = out.get("bairro_ou_localidade", "").astype(str)
    out["nome_territorio_exibicao"] = out["bairro_ou_localidade_original"].map(_texto_valido)
    out["fonte_nome_territorio"] = "base original"
    out["nome_territorio_validado"] = ~out["nome_territorio_exibicao"].map(_eh_rotulo_generico_setor)

    if not nomes.empty and "setor_censitario" in out.columns:
        nomes = nomes.rename(columns={
            "nome_territorio_exibicao": "_nome_externo",
            "fonte_nome_territorio": "_fonte_externa",
            "tipo_territorio_ajustado": "_tipo_externo",
        })
        out = out.merge(nomes[["setor_censitario", "_nome_externo", "_fonte_externa", "_tipo_externo"]], on="setor_censitario", how="left")
        tem_nome = out.get("_nome_externo", pd.Series(index=out.index, dtype=str)).map(_texto_valido).ne("")
        out.loc[tem_nome, "nome_territorio_exibicao"] = out.loc[tem_nome, "_nome_externo"].map(_texto_valido)
        out.loc[tem_nome, "fonte_nome_territorio"] = out.loc[tem_nome, "_fonte_externa"].map(lambda x: _texto_valido(x) or "base externa")
        if "tipo_territorio" in out.columns:
            out.loc[tem_nome & out.get("_tipo_externo", pd.Series(index=out.index, dtype=str)).map(_texto_valido).ne(""), "tipo_territorio"] = out.loc[tem_nome, "_tipo_externo"].map(_texto_valido)
        out["nome_territorio_validado"] = ~out["nome_territorio_exibicao"].map(_eh_rotulo_generico_setor)
        out = out.drop(columns=["_nome_externo", "_fonte_externa", "_tipo_externo"], errors="ignore")

    out = _marcar_nomes_ibge_amplos_para_validacao(out)

    generico = out["nome_territorio_exibicao"].map(_eh_rotulo_generico_setor)
    out.loc[generico, "nome_territorio_exibicao"] = out.loc[generico, "setor_censitario"].map(lambda x: f"Setor censitário {x} — bairro/localidade a validar")
    out.loc[generico, "fonte_nome_territorio"] = "sem nome de bairro/localidade na base; usando código do setor como fallback"
    return out


def _extrair_nome_osm(address: dict, municipio: str) -> tuple[str, str]:
    if not isinstance(address, dict):
        return "", ""
    prioridade = [
        ("suburb", "bairro"),
        ("neighbourhood", "vizinhança/bairro"),
        ("quarter", "bairro/região"),
        ("city_district", "distrito urbano"),
        ("village", "localidade/vila"),
        ("hamlet", "localidade"),
        ("residential", "área residencial"),
    ]
    chave_mun = _chave(municipio)
    for campo, tipo in prioridade:
        nome = _texto_valido(address.get(campo))
        if nome and _chave(nome) != chave_mun:
            return nome, tipo
    # Fallback prudente: quando o OSM não retorna bairro, usa distrito/localidade mais próxima, mas marca como aproximação.
    for campo in ["road", "municipality", "town", "city"]:
        nome = _texto_valido(address.get(campo))
        if nome and _chave(nome) != chave_mun:
            return f"Entorno de {nome}", "referência aproximada OSM"
    return "", ""


def nomear_bairros_localidades_por_osm(municipio: str | None = None, limite: int = 80, pausa_segundos: float = 1.05, sobrescrever: bool = False) -> dict:
    """Tenta atribuir nome de bairro/localidade por geocodificação reversa OSM/Nominatim.

    A rotina é opcional e incremental. Ela grava um cache em
    data/reference/bairros_localidades_setores_nomeados.csv, usado automaticamente
    nas análises e mapas. Para preservar qualidade, recomenda-se rodar por município
    e validar depois com a equipe municipal/ERS.
    """
    fonte = _carregar_bairros_localidades_referencia()
    if fonte.empty:
        return {"ok": False, "mensagem": "Base de setores/localidades não encontrada.", "processados": 0, "nomeados": 0, "cache": str(BAIRROS_LOCALIDADES_NOMEADAS_CACHE)}
    df = fonte.copy()
    if municipio:
        df = df[df["municipio"].astype(str).str.contains(str(municipio), case=False, na=False)].copy()
    if df.empty:
        return {"ok": False, "mensagem": "Nenhum território encontrado para o filtro informado.", "processados": 0, "nomeados": 0, "cache": str(BAIRROS_LOCALIDADES_NOMEADAS_CACHE)}

    if not sobrescrever:
        df = df[~df.get("nome_territorio_validado", pd.Series(False, index=df.index)).astype(bool)].copy()
    df = df[df.get("coord_valida_mt", False).astype(bool)].copy()
    df = df.head(max(1, int(limite or 1))).copy()
    if df.empty:
        return {"ok": True, "mensagem": "Não há registros pendentes de nomeação para o filtro atual.", "processados": 0, "nomeados": 0, "cache": str(BAIRROS_LOCALIDADES_NOMEADAS_CACHE)}

    registros = []
    erros = []
    headers = {"User-Agent": "plataforma-aps-inteligencia-ses-mt/1.0"}
    for _, row in df.iterrows():
        try:
            params = {"format": "jsonv2", "lat": float(row.get("latitude")), "lon": float(row.get("longitude")), "zoom": 16, "addressdetails": 1}
            resp = requests.get(OSM_REVERSE_URL, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            nome, tipo = _extrair_nome_osm(data.get("address", {}), str(row.get("municipio", "")))
            if nome:
                registros.append({
                    "setor_censitario": row.get("setor_censitario", ""),
                    "codigo_ibge": row.get("codigo_ibge", ""),
                    "municipio": row.get("municipio", ""),
                    "nome_territorio_exibicao": nome,
                    "tipo_territorio_ajustado": tipo,
                    "fonte_nome_territorio": "OpenStreetMap/Nominatim - geocodificação reversa do centroide do setor; validar com município",
                    "latitude": row.get("latitude", ""),
                    "longitude": row.get("longitude", ""),
                    "atualizado_em": datetime.now().isoformat(timespec="seconds"),
                })
        except Exception as exc:
            erros.append(f"{row.get('setor_censitario', '')}: {exc}")
        time.sleep(max(0.2, float(pausa_segundos or 1.05)))

    novo = pd.DataFrame(registros)
    if BAIRROS_LOCALIDADES_NOMEADAS_CACHE.exists():
        try:
            antigo = pd.read_csv(BAIRROS_LOCALIDADES_NOMEADAS_CACHE, dtype=str)
        except Exception:
            antigo = pd.DataFrame()
    else:
        antigo = pd.DataFrame()
    combinado = pd.concat([antigo, novo], ignore_index=True) if not antigo.empty or not novo.empty else pd.DataFrame()
    if not combinado.empty:
        combinado = combinado.drop_duplicates("setor_censitario", keep="last")
        BAIRROS_LOCALIDADES_NOMEADAS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        combinado.to_csv(BAIRROS_LOCALIDADES_NOMEADAS_CACHE, index=False, encoding="utf-8-sig")

    return {
        "ok": True,
        "mensagem": "Rotina concluída. Recalcule/atualize a tela para ver os nomes aplicados ao mapa e às tabelas.",
        "processados": int(len(df)),
        "nomeados": int(len(novo)),
        "erros": erros[:10],
        "cache": str(BAIRROS_LOCALIDADES_NOMEADAS_CACHE),
    }


def _flatten_coords(geometry: dict) -> list[list[float]]:
    """Extrai pares lon/lat de Polygon/MultiPolygon sem depender de shapely/geopandas."""
    coords = geometry.get("coordinates") or []
    tipo = geometry.get("type", "")
    pontos: list[list[float]] = []

    def walk(obj):
        if isinstance(obj, (list, tuple)) and len(obj) >= 2 and all(isinstance(x, (int, float)) for x in obj[:2]):
            lon, lat = float(obj[0]), float(obj[1])
            if -75 <= lon <= -45 and -25 <= lat <= 5:
                pontos.append([lon, lat])
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                walk(item)

    if tipo in {"Polygon", "MultiPolygon", "GeometryCollection"} or coords:
        walk(coords)
    return pontos


def _ring_area_km2(ring: list[list[float]]) -> float:
    """Área aproximada por projeção equiretangular local.

    É suficiente para preencher camada territorial preliminar e calcular densidade.
    Valores oficiais podem ser substituídos depois por base tabular do IBGE.
    """
    if len(ring) < 4:
        return 0.0
    lat0 = sum(p[1] for p in ring) / len(ring)
    r = 6371.0088
    fator_lon = r * cos(lat0 * pi / 180.0) * pi / 180.0
    fator_lat = r * pi / 180.0
    pts = [(p[0] * fator_lon, p[1] * fator_lat) for p in ring]
    area = 0.0
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def _area_geometry_km2(geometry: dict) -> float | None:
    tipo = geometry.get("type")
    coords = geometry.get("coordinates") or []
    total = 0.0
    try:
        if tipo == "Polygon":
            for idx, ring in enumerate(coords):
                area = _ring_area_km2(ring)
                total += area if idx == 0 else -area
        elif tipo == "MultiPolygon":
            for poly in coords:
                for idx, ring in enumerate(poly):
                    area = _ring_area_km2(ring)
                    total += area if idx == 0 else -area
        else:
            return None
        return round(abs(total), 3) if total else None
    except Exception:
        return None


def _centroide_bbox(pontos: list[list[float]]) -> tuple[float | None, float | None]:
    if not pontos:
        return None, None
    lons = [p[0] for p in pontos]
    lats = [p[1] for p in pontos]
    lon = (min(lons) + max(lons)) / 2
    lat = (min(lats) + max(lats)) / 2
    return round(lat, 6), round(lon, 6)


def _obter_municipios_ibge() -> pd.DataFrame:
    resp = requests.get(LOCALIDADES_URL, timeout=40)
    resp.raise_for_status()
    data = resp.json()
    linhas = []
    oficiais = {_chave(m["municipio"]): m for m in DEFAULT_MUNICIPIOS}
    for item in data:
        nome = item.get("nome")
        chave = _normalizar_alias_chave(_chave(nome))
        if chave not in oficiais:
            continue
        linhas.append({
            "codigo_ibge": str(item.get("id")),
            "municipio": oficiais[chave]["municipio"],
            "regiao_saude": oficiais[chave].get("regiao_saude"),
        })

    # Fallback defensivo para municípios cuja grafia pode variar entre bases.
    # Não cria duplicidade: só preenche se o município oficial ainda não entrou.
    presentes = {_chave(r.get("municipio")) for r in linhas}
    for chave_oficial, codigo in CODIGOS_IBGE_FALLBACK.items():
        if chave_oficial in presentes or chave_oficial not in oficiais:
            continue
        item = oficiais[chave_oficial]
        linhas.append({
            "codigo_ibge": codigo,
            "municipio": item["municipio"],
            "regiao_saude": item.get("regiao_saude"),
        })
    return pd.DataFrame(linhas)


def _baixar_geojson_municipio(codigo_ibge: str) -> dict:
    params = {
        "formato": "application/vnd.geo+json",
        "qualidade": "minima",
    }
    resp = requests.get(MALHAS_URL.format(codigo=codigo_ibge), params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _extrair_geometry(geojson: dict) -> dict | None:
    if geojson.get("type") == "FeatureCollection":
        features = geojson.get("features") or []
        if features:
            return features[0].get("geometry")
    if geojson.get("type") == "Feature":
        return geojson.get("geometry")
    if geojson.get("type") in {"Polygon", "MultiPolygon"}:
        return geojson
    return None


def gerar_georreferencia_municipal_mt(forcar_download: bool = False, pausa_segundos: float = 0.08) -> pd.DataFrame:
    """Gera/atualiza CSV local com latitude, longitude e área aproximada dos municípios de MT.

    Primeiro usa cache em data/geo. Se não existir ou se forçar download, consulta IBGE Localidades + Malhas.
    """
    GEO_DIR.mkdir(parents=True, exist_ok=True)
    if CACHE_GEO_MUNICIPAL.exists() and not forcar_download:
        df_cache = pd.read_csv(CACHE_GEO_MUNICIPAL, dtype={"codigo_ibge": str})
        if not df_cache.empty and {"municipio", "latitude", "longitude"}.issubset(df_cache.columns):
            return df_cache

    municipios = _obter_municipios_ibge()
    if municipios.empty:
        raise RuntimeError("Não foi possível obter a lista de municípios de MT no IBGE Localidades.")

    registros = []
    erros = []
    for _, row in municipios.iterrows():
        codigo = _codigo_limpo(row.get("codigo_ibge"))
        municipio = row.get("municipio")
        try:
            geojson = _baixar_geojson_municipio(codigo)
            geometry = _extrair_geometry(geojson)
            if not geometry:
                raise RuntimeError("GeoJSON sem geometria reconhecida.")
            pontos = _flatten_coords(geometry)
            lat, lon = _centroide_bbox(pontos)
            area_km2 = _area_geometry_km2(geometry)
            registros.append({
                "codigo_ibge": codigo,
                "municipio": municipio,
                "regiao_saude": row.get("regiao_saude"),
                "latitude": lat,
                "longitude": lon,
                "area_km2": area_km2,
                "fonte_geo": "IBGE Malhas v3 - qualidade mínima; centroide por bounding box; área aproximada calculada",
                "atualizado_em": datetime.now().isoformat(timespec="seconds"),
            })
        except Exception as exc:
            erros.append(f"{municipio}: {exc}")
            registros.append({
                "codigo_ibge": codigo,
                "municipio": municipio,
                "regiao_saude": row.get("regiao_saude"),
                "latitude": None,
                "longitude": None,
                "area_km2": None,
                "fonte_geo": f"Erro: {exc}",
                "atualizado_em": datetime.now().isoformat(timespec="seconds"),
            })
        time.sleep(pausa_segundos)

    df = pd.DataFrame(registros)
    df.to_csv(CACHE_GEO_MUNICIPAL, index=False, encoding="utf-8-sig")
    return df


def importar_georreferencia_municipal(df: pd.DataFrame | None = None, ano: int | None = None) -> dict:
    """Atualiza latitude/longitude na tabela municipios e grava área/densidade em indicadores_municipais."""
    agora = datetime.now().isoformat(timespec="seconds")
    ano = ano or datetime.now().year
    if df is None:
        df = gerar_georreferencia_municipal_mt(forcar_download=False)
    if df.empty:
        raise RuntimeError("Base georreferenciada vazia.")

    df = df.copy()
    df["codigo_ibge"] = df["codigo_ibge"].map(_codigo_limpo)
    for col in ["latitude", "longitude", "area_km2"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    with get_connection() as conn:
        municipios = pd.read_sql_query("SELECT codigo_ibge, municipio FROM municipios", conn)
        indicadores = pd.read_sql_query("SELECT municipio, indicador, ano, valor FROM indicadores_municipais", conn)

    pop = pd.DataFrame(columns=["municipio", "populacao"])
    if not indicadores.empty:
        aux = indicadores[indicadores["indicador"].astype(str).str.lower().isin(["populacao", "população", "populacao_estimada"])].copy()
        if not aux.empty:
            aux["ano"] = pd.to_numeric(aux["ano"], errors="coerce")
            aux["valor"] = pd.to_numeric(aux["valor"], errors="coerce")
            aux = aux.sort_values(["municipio", "ano"])
            pop = aux.groupby("municipio").tail(1)[["municipio", "valor"]].rename(columns={"valor": "populacao"})

    base = df.merge(pop, on="municipio", how="left")
    base["densidade_hab_km2"] = None
    mask = base["populacao"].notna() & base["area_km2"].notna() & (base["area_km2"] > 0)
    base.loc[mask, "densidade_hab_km2"] = base.loc[mask, "populacao"].astype(float) / base.loc[mask, "area_km2"].astype(float)

    atualizados_coord = 0
    indicadores_inseridos = 0
    with db_session() as conn:
        for _, row in base.iterrows():
            municipio = row.get("municipio")
            codigo = _codigo_limpo(row.get("codigo_ibge"))
            lat = row.get("latitude")
            lon = row.get("longitude")
            if codigo:
                conn.execute(
                    """
                    UPDATE municipios
                    SET codigo_ibge = COALESCE(NULLIF(codigo_ibge, ''), ?), atualizado_em = ?
                    WHERE municipio = ?
                    """,
                    (codigo, agora, municipio),
                )
            if pd.notna(lat) and pd.notna(lon):
                conn.execute(
                    """
                    UPDATE municipios
                    SET codigo_ibge = COALESCE(NULLIF(codigo_ibge, ''), ?), latitude = ?, longitude = ?, atualizado_em = ?
                    WHERE municipio = ?
                    """,
                    (codigo, float(lat), float(lon), agora, municipio),
                )
                atualizados_coord += 1
            if pd.notna(row.get("area_km2")):
                conn.execute(
                    """
                    INSERT INTO indicadores_municipais (municipio, ano, competencia, indicador, valor, fonte, importacao_id, atualizado_em)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (municipio, ano, str(ano), "area_territorial_km2", float(row.get("area_km2")), "IBGE_MALHAS_GEO", None, agora),
                )
                indicadores_inseridos += 1
            if pd.notna(row.get("densidade_hab_km2")):
                conn.execute(
                    """
                    INSERT INTO indicadores_municipais (municipio, ano, competencia, indicador, valor, fonte, importacao_id, atualizado_em)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (municipio, ano, str(ano), "densidade_demografica_calculada_hab_km2", float(row.get("densidade_hab_km2")), "IBGE_MALHAS_GEO", None, agora),
                )
                indicadores_inseridos += 1

    return {
        "municipios_lidos": int(len(df)),
        "coordenadas_atualizadas": int(atualizados_coord),
        "areas_preenchidas": int(pd.to_numeric(df.get("area_km2"), errors="coerce").notna().sum()) if "area_km2" in df.columns else 0,
        "indicadores_inseridos": int(indicadores_inseridos),
        "cache": str(CACHE_GEO_MUNICIPAL),
        "atualizado_em": agora,
    }


def qualidade_georreferencia() -> dict:
    with get_connection() as conn:
        municipios = pd.read_sql_query("SELECT municipio, latitude, longitude FROM municipios", conn)
        indicadores = pd.read_sql_query("SELECT municipio, indicador, valor FROM indicadores_municipais", conn)
    total = len(municipios)
    coords = 0
    if not municipios.empty:
        lat = pd.to_numeric(municipios.get("latitude"), errors="coerce")
        lon = pd.to_numeric(municipios.get("longitude"), errors="coerce")
        coords = int((lat.notna() & lon.notna()).sum())
    area = 0
    dens = 0
    if not indicadores.empty:
        ind = indicadores["indicador"].astype(str).str.lower()
        area = indicadores[ind.isin(["area_territorial_km2", "area_densidade_territorial"])].groupby("municipio").size().shape[0]
        dens = indicadores[ind.str.contains("densidade", na=False)].groupby("municipio").size().shape[0]
    return {
        "municipios": total,
        "coordenadas": coords,
        "area": int(area),
        "densidade": int(dens),
        "cache_existe": CACHE_GEO_MUNICIPAL.exists(),
        "cache": str(CACHE_GEO_MUNICIPAL),
    }

# ============================================================================
# Diagnóstico de camadas geográficas — v1
# ============================================================================

LAT_CANDIDATES = [
    "latitude", "lat", "latitude_centroid", "latitude_centroide", "lat_centroid",
    "nu_latitude", "vl_latitude", "coord_lat", "y",
]
LON_CANDIDATES = [
    "longitude", "lon", "lng", "long", "longitude_centroid", "longitude_centroide",
    "lon_centroid", "nu_longitude", "vl_longitude", "coord_lon", "x",
]
GEOM_CANDIDATES = [
    "geometry_json", "geometria_json", "geojson", "geometry", "geom", "wkt", "the_geom",
]
MUNICIPIO_CANDIDATES = [
    "municipio", "município", "nome_municipio", "nm_municipio", "nm_mun", "cidade",
]
CODIGO_CANDIDATES = [
    "codigo_ibge", "cod_ibge", "codigo_municipio", "cod_municipio", "cod_mun", "ibge",
]

CAMADAS_GEO_PADRAO = [
    {
        "tabela": "base_municipal_consolidada",
        "nome": "Base municipal consolidada",
        "tipo": "Município / indicadores consolidados",
        "uso": "Base principal para mapa municipal, filtros regionais e leitura executiva.",
    },
    {
        "tabela": "municipios",
        "nome": "Municípios oficiais SES/MT",
        "tipo": "Município / cadastro mestre",
        "uso": "Tabela de referência para padronizar nomes, códigos IBGE, regiões e coordenadas.",
    },
    {
        "tabela": "malhas_geograficas_municipais",
        "nome": "Malhas geográficas municipais",
        "tipo": "Polígono municipal / centroide",
        "uso": "Camada territorial de referência para mapas, áreas, centroides e limites municipais.",
    },
    {
        "tabela": "estabelecimentos_saude",
        "nome": "Estabelecimentos de saúde / UBS",
        "tipo": "Pontos de serviços de saúde",
        "uso": "Mapa de oferta física, UBS e distribuição territorial da rede assistencial.",
    },
    {
        "tabela": "equipes_aps",
        "nome": "Equipes APS/CNES/INE",
        "tipo": "Camada municipalizada / equipes",
        "uso": "Análise de cobertura estrutural por tipo de equipe e município.",
    },
    {
        "tabela": "profissionais_cnes",
        "nome": "Profissionais vinculados às equipes APS",
        "tipo": "Camada municipalizada / força de trabalho",
        "uso": "Leitura de composição profissional por município/equipe.",
    },
    {
        "tabela": "dados_mt_assentamentos",
        "nome": "Assentamentos — Dados Abertos MT/INTERMAT",
        "tipo": "Território especial / ruralidade",
        "uso": "Identificação de áreas rurais e potenciais vazios assistenciais ligados à logística territorial.",
    },
    {
        "tabela": "dados_mt_terras_indigenas",
        "nome": "Terras Indígenas — Dados Abertos MT/INTERMAT",
        "tipo": "Território especial / equidade",
        "uso": "Apoio à leitura de equidade territorial e priorização de políticas diferenciadas.",
    },
    {
        "tabela": "dados_mt_areas_contaminadas",
        "nome": "Ocorrências ambientais / produtos perigosos — SEMA",
        "tipo": "Risco ambiental / vigilância",
        "uso": "Camada complementar para diálogo entre APS, vigilância ambiental e gestão de riscos.",
    },
]


def _table_exists(conn, table_name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _read_table_safe(table_name: str) -> pd.DataFrame:
    with get_connection() as conn:
        if not _table_exists(conn, table_name):
            return pd.DataFrame()
        try:
            return pd.read_sql_query(f'SELECT * FROM "{table_name}"', conn)
        except Exception:
            return pd.DataFrame()


def _pick_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    if df.empty:
        return None
    lower_map = {str(c).strip().lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in lower_map:
            return lower_map[c.lower()]
    # tentativa por inclusão, para colunas longas vindas de fontes externas
    for c in candidates:
        token = c.lower()
        for low, original in lower_map.items():
            if token in low:
                return original
    return None


def _valid_lat_lon(lat: pd.Series, lon: pd.Series) -> pd.Series:
    latn = pd.to_numeric(lat, errors="coerce")
    lonn = pd.to_numeric(lon, errors="coerce")
    return latn.between(-25, 5, inclusive="both") & lonn.between(-75, -45, inclusive="both")


def _geometry_non_empty(series: pd.Series) -> pd.Series:
    return series.notna() & series.astype(str).str.strip().ne("") & series.astype(str).str.lower().ne("none")


def _municipios_referencia() -> pd.DataFrame:
    base = _read_table_safe("municipios")
    if base.empty:
        base = _read_table_safe("base_municipal_consolidada")
    if base.empty:
        return pd.DataFrame(columns=["municipio", "codigo_ibge", "regiao_saude"])
    mun_col = _pick_col(base, MUNICIPIO_CANDIDATES) or "municipio"
    cod_col = _pick_col(base, CODIGO_CANDIDATES)
    reg_col = "regiao_saude" if "regiao_saude" in base.columns else None
    out = pd.DataFrame()
    out["municipio"] = base[mun_col].astype(str).str.strip()
    out["municipio_chave"] = out["municipio"].map(_chave)
    out["codigo_ibge"] = base[cod_col].map(_codigo_limpo) if cod_col else ""
    out["regiao_saude"] = base[reg_col] if reg_col else None
    return out.drop_duplicates("municipio_chave")


def diagnosticar_camadas_geograficas() -> dict:
    """Avalia quais camadas geográficas existem e se estão prontas para mapa."""
    ref = _municipios_referencia()
    ref_chaves = set(ref["municipio_chave"].dropna()) if not ref.empty else set()
    ref_codigos = set(ref["codigo_ibge"].dropna().astype(str)) if not ref.empty else set()
    linhas = []
    detalhes: dict[str, pd.DataFrame] = {}

    with get_connection() as conn:
        tabelas_existentes = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

    for camada in CAMADAS_GEO_PADRAO:
        tabela = camada["tabela"]
        existe = tabela in tabelas_existentes
        df = _read_table_safe(tabela) if existe else pd.DataFrame()
        registros = int(len(df)) if not df.empty else 0
        lat_col = _pick_col(df, LAT_CANDIDATES)
        lon_col = _pick_col(df, LON_CANDIDATES)
        geom_col = _pick_col(df, GEOM_CANDIDATES)
        mun_col = _pick_col(df, MUNICIPIO_CANDIDATES)
        cod_col = _pick_col(df, CODIGO_CANDIDATES)
        com_coords = 0
        if lat_col and lon_col:
            try:
                com_coords = int(_valid_lat_lon(df[lat_col], df[lon_col]).sum())
            except Exception:
                com_coords = 0
        com_geom = 0
        if geom_col:
            try:
                com_geom = int(_geometry_non_empty(df[geom_col]).sum())
            except Exception:
                com_geom = 0
        vinculados = 0
        if registros:
            vinc_mun = pd.Series(False, index=df.index)
            if mun_col and ref_chaves:
                vinc_mun = df[mun_col].map(_chave).isin(ref_chaves)
            vinc_cod = pd.Series(False, index=df.index)
            if cod_col and ref_codigos:
                vinc_cod = df[cod_col].map(_codigo_limpo).isin(ref_codigos)
            vinculados = int((vinc_mun | vinc_cod).sum())
        pct_coords = round((com_coords / registros * 100), 1) if registros else 0.0
        pct_geom = round((com_geom / registros * 100), 1) if registros else 0.0
        pct_vinc = round((vinculados / registros * 100), 1) if registros else 0.0

        if not existe:
            qualidade = "Não disponível"
            pronto_mapa = "Não"
        elif registros == 0:
            qualidade = "Sem registros"
            pronto_mapa = "Não"
        elif pct_coords >= 80 or pct_geom >= 80:
            qualidade = "Boa"
            pronto_mapa = "Sim"
        elif pct_vinc >= 70:
            qualidade = "Municipalizada"
            pronto_mapa = "Com agregação municipal"
        elif pct_coords >= 30 or pct_geom >= 30:
            qualidade = "Parcial"
            pronto_mapa = "Com cautela"
        else:
            qualidade = "Limitada"
            pronto_mapa = "Não"

        linhas.append({
            "camada": camada["nome"],
            "tabela": tabela,
            "tipo": camada["tipo"],
            "existe": "Sim" if existe else "Não",
            "registros": registros,
            "com_coordenadas": com_coords,
            "% coordenadas": pct_coords,
            "com_geometria": com_geom,
            "% geometria": pct_geom,
            "vinculados_municipio": vinculados,
            "% vínculo municipal": pct_vinc,
            "coluna_municipio": mun_col or "",
            "coluna_codigo": cod_col or "",
            "coluna_latitude": lat_col or "",
            "coluna_longitude": lon_col or "",
            "coluna_geometria": geom_col or "",
            "qualidade": qualidade,
            "pronto_para_mapa": pronto_mapa,
            "uso_recomendado": camada["uso"],
        })

        if not df.empty:
            detalhes[tabela] = df.head(200).copy()

    resumo = pd.DataFrame(linhas)
    return {
        "resumo": resumo,
        "detalhes": detalhes,
        "municipios_referencia": ref,
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
    }


def _agregar_por_municipio(df: pd.DataFrame, nome_coluna: str, ref: pd.DataFrame, origem: str = "municipio") -> pd.DataFrame:
    if df.empty or ref.empty:
        return pd.DataFrame(columns=["municipio", nome_coluna])
    mun_col = _pick_col(df, MUNICIPIO_CANDIDATES)
    cod_col = _pick_col(df, CODIGO_CANDIDATES)
    linhas = []
    ref_by_chave = dict(zip(ref["municipio_chave"], ref["municipio"]));
    ref_by_cod = dict(zip(ref["codigo_ibge"].astype(str), ref["municipio"])) if "codigo_ibge" in ref.columns else {}

    if origem == "municipios_intersectados" and "municipios_intersectados" in df.columns:
        for valor in df["municipios_intersectados"].dropna().astype(str):
            partes = []
            for sep in [";", ",", "|"]:
                if sep in valor:
                    partes = [p.strip() for p in valor.split(sep) if p.strip()]
                    break
            if not partes:
                partes = [valor.strip()]
            for parte in partes:
                chave = _chave(parte)
                if chave in ref_by_chave:
                    linhas.append(ref_by_chave[chave])
    elif mun_col:
        for valor in df[mun_col].dropna().astype(str):
            chave = _chave(valor)
            if chave in ref_by_chave:
                linhas.append(ref_by_chave[chave])
    elif cod_col:
        for valor in df[cod_col].dropna().map(_codigo_limpo):
            if valor in ref_by_cod:
                linhas.append(ref_by_cod[valor])

    if not linhas:
        return pd.DataFrame(columns=["municipio", nome_coluna])
    out = pd.Series(linhas).value_counts().rename_axis("municipio").reset_index(name=nome_coluna)
    return out


def montar_base_mapa_municipal() -> pd.DataFrame:
    """Monta base municipal enriquecida para mapa e filtros do georreferenciamento."""
    base = _read_table_safe("base_municipal_consolidada")
    if base.empty:
        base = _read_table_safe("municipios")
    if base.empty:
        return pd.DataFrame()

    base = base.copy()
    mun_col = _pick_col(base, MUNICIPIO_CANDIDATES) or "municipio"
    if mun_col != "municipio":
        base = base.rename(columns={mun_col: "municipio"})
    if "codigo_ibge" not in base.columns:
        cod_col = _pick_col(base, CODIGO_CANDIDATES)
        base["codigo_ibge"] = base[cod_col].map(_codigo_limpo) if cod_col else ""
    if "regiao_saude" not in base.columns:
        base["regiao_saude"] = "Não informada"

    ref = _municipios_referencia()
    for col in ["populacao", "total_equipes_aps", "total_ubs", "total_profissionais_aps", "area_km2", "densidade_hab_km2", "latitude", "longitude"]:
        if col in base.columns:
            base[col] = pd.to_numeric(base[col], errors="coerce")
        else:
            base[col] = pd.NA


    # Corrige total_ubs a partir de CNES único para evitar duplicidade artificial
    # causada por linhas repetidas de um mesmo estabelecimento.
    try:
        est_ubs = _read_table_safe("estabelecimentos_saude")
        if not est_ubs.empty and {"municipio", "cnes"}.issubset(est_ubs.columns):
            est_ubs = deduplicar_estabelecimentos_saude(est_ubs)
            est_ubs["municipio"] = est_ubs["municipio"].astype(str)
            total_ubs_corr = est_ubs.groupby("municipio").size().reset_index(name="total_ubs_corrigido")
            base = base.merge(total_ubs_corr, on="municipio", how="left")
            base["total_ubs_original_base"] = base.get("total_ubs")
            base["total_ubs"] = pd.to_numeric(base["total_ubs_corrigido"], errors="coerce").fillna(pd.to_numeric(base["total_ubs"], errors="coerce"))
            base = base.drop(columns=["total_ubs_corrigido"], errors="ignore")
    except Exception:
        pass

    # Reforça coordenadas a partir da tabela municipios quando a base consolidada não tiver lat/lon.
    mun = _read_table_safe("municipios")
    if not mun.empty and {"municipio", "latitude", "longitude"}.issubset(mun.columns):
        aux = mun[["municipio", "latitude", "longitude"]].copy()
        aux["latitude_ref"] = pd.to_numeric(aux["latitude"], errors="coerce")
        aux["longitude_ref"] = pd.to_numeric(aux["longitude"], errors="coerce")
        aux = aux[["municipio", "latitude_ref", "longitude_ref"]]
        base = base.merge(aux, on="municipio", how="left")
        base["latitude"] = base["latitude"].fillna(base["latitude_ref"])
        base["longitude"] = base["longitude"].fillna(base["longitude_ref"])
        base = base.drop(columns=["latitude_ref", "longitude_ref"], errors="ignore")

    # Agrega camadas territoriais especiais.
    assent = _agregar_por_municipio(_read_table_safe("dados_mt_assentamentos"), "qtd_assentamentos", ref)
    terras = _agregar_por_municipio(_read_table_safe("dados_mt_terras_indigenas"), "qtd_terras_indigenas_intersecoes", ref, origem="municipios_intersectados")
    areas = _agregar_por_municipio(_read_table_safe("dados_mt_areas_contaminadas"), "qtd_ocorrencias_ambientais", ref)
    for aux in [assent, terras, areas]:
        if not aux.empty:
            base = base.merge(aux, on="municipio", how="left")
    for col in ["qtd_assentamentos", "qtd_terras_indigenas_intersecoes", "qtd_ocorrencias_ambientais"]:
        if col not in base.columns:
            base[col] = 0
        base[col] = pd.to_numeric(base[col], errors="coerce").fillna(0).astype(int)

    # Preenche determinantes sociais já carregados/importados que ainda não foram
    # incorporados fisicamente à base_municipal_consolidada. Isso evita que a aba
    # mostre None quando indicadores_municipais ou o cache de referência já têm dado.
    base = enriquecer_base_com_determinantes_importados(base)

    # Indicadores simples para mapa: não substituem a Análise Territorial; são sinais visuais.
    base["pop_por_equipe"] = pd.NA
    mask_eq = pd.to_numeric(base["total_equipes_aps"], errors="coerce").fillna(0) > 0
    base.loc[mask_eq, "pop_por_equipe"] = base.loc[mask_eq, "populacao"] / base.loc[mask_eq, "total_equipes_aps"]
    base["pop_por_ubs"] = pd.NA
    mask_ubs = pd.to_numeric(base["total_ubs"], errors="coerce").fillna(0) > 0
    base.loc[mask_ubs, "pop_por_ubs"] = base.loc[mask_ubs, "populacao"] / base.loc[mask_ubs, "total_ubs"]

    def _score_series(s: pd.Series, invert: bool = False) -> pd.Series:
        v = pd.to_numeric(s, errors="coerce").fillna(0)
        if len(v) == 0 or v.max() == v.min():
            return pd.Series(0, index=v.index)
        q = v.rank(pct=True) * 100
        return 100 - q if invert else q

    base["score_pressao_equipes"] = _score_series(base["pop_por_equipe"])
    base["score_pressao_ubs"] = _score_series(base["pop_por_ubs"])
    base["score_dispersao"] = _score_series(base["area_km2"])
    base["score_territorios_especiais"] = _score_series(base["qtd_assentamentos"] + base["qtd_terras_indigenas_intersecoes"])
    base["score_risco_ambiental"] = _score_series(base["qtd_ocorrencias_ambientais"])
    base["indice_geo_preliminar"] = (
        base["score_pressao_equipes"] * 0.30
        + base["score_pressao_ubs"] * 0.20
        + base["score_dispersao"] * 0.20
        + base["score_territorios_especiais"] * 0.20
        + base["score_risco_ambiental"] * 0.10
    ).round(2)

    def _classe(v):
        try:
            v = float(v)
        except Exception:
            return "Sem classificação"
        if v >= 75:
            return "Muito alta"
        if v >= 60:
            return "Alta"
        if v >= 40:
            return "Média"
        return "Monitoramento"

    base["classe_geo_preliminar"] = base["indice_geo_preliminar"].map(_classe)
    return base


def obter_pontos_camada(tabela: str, limite: int = 5000) -> pd.DataFrame:
    """Retorna registros com coordenadas válidas de uma tabela/camada."""
    df = _read_table_safe(tabela)
    if df.empty:
        return pd.DataFrame()
    if tabela == "estabelecimentos_saude":
        df = deduplicar_estabelecimentos_saude(df)
    lat_col = _pick_col(df, LAT_CANDIDATES)
    lon_col = _pick_col(df, LON_CANDIDATES)
    if not lat_col or not lon_col:
        return pd.DataFrame()
    out = df.copy()
    out["lat"] = pd.to_numeric(out[lat_col], errors="coerce")
    out["lon"] = pd.to_numeric(out[lon_col], errors="coerce")
    out = out[_valid_lat_lon(out["lat"], out["lon"])].copy()
    if out.empty:
        return out
    mun_col = _pick_col(out, MUNICIPIO_CANDIDATES)
    if mun_col and mun_col != "municipio":
        out["municipio"] = out[mun_col]
    elif "municipio" not in out.columns:
        out["municipio"] = ""
    nome_col = None
    for cand in ["nome", "nome_fantasia", "nome_estabelecimento", "estabelecimento", "nome_assentamento", "nome_terra_indigena", "tipo_ocorrencia"]:
        if cand in out.columns:
            nome_col = cand
            break
    out["rotulo"] = out[nome_col].astype(str) if nome_col else tabela
    out["camada"] = tabela

    # Validação espacial para evitar pontos fora de MT ou fora da malha do município
    # no mapa estratégico. Os pontos inválidos não são apagados; ficam disponíveis
    # em função própria de auditoria.
    cod_col = _pick_col(out, CODIGO_CANDIDATES)
    if cod_col and cod_col != "codigo_ibge":
        out["codigo_ibge"] = out[cod_col].astype(str)
    elif "codigo_ibge" not in out.columns:
        out["codigo_ibge"] = ""

    out = validar_pontos_mapa_estrategico(out, lat_col="lat", lon_col="lon", municipio_col="municipio", codigo_col="codigo_ibge")
    out = out[out["ponto_utilizado_mapa_principal"].astype(bool)].copy()
    return out.head(limite)



def obter_inconsistencias_pontos_mapa(tabelas: list[str] | None = None, limite: int = 1000) -> pd.DataFrame:
    """Retorna pontos bloqueados do mapa principal por inconsistência geográfica."""
    if tabelas is None:
        tabelas = [
            "estabelecimentos_saude",
            "dados_mt_assentamentos",
            "dados_mt_terras_indigenas",
            "dados_mt_areas_contaminadas",
        ]
    registros = []
    for tabela in tabelas:
        df = _read_table_safe(tabela)
        if df.empty:
            continue
        if tabela == "estabelecimentos_saude":
            df = deduplicar_estabelecimentos_saude(df)
        lat_col = _pick_col(df, LAT_CANDIDATES)
        lon_col = _pick_col(df, LON_CANDIDATES)
        if not lat_col or not lon_col:
            continue
        out = df.copy()
        out["lat"] = pd.to_numeric(out[lat_col], errors="coerce")
        out["lon"] = pd.to_numeric(out[lon_col], errors="coerce")
        mun_col = _pick_col(out, MUNICIPIO_CANDIDATES)
        if mun_col and mun_col != "municipio":
            out["municipio"] = out[mun_col]
        elif "municipio" not in out.columns:
            out["municipio"] = ""
        cod_col = _pick_col(out, CODIGO_CANDIDATES)
        if cod_col and cod_col != "codigo_ibge":
            out["codigo_ibge"] = out[cod_col].astype(str)
        elif "codigo_ibge" not in out.columns:
            out["codigo_ibge"] = ""
        nome_col = None
        for cand in ["nome", "nome_fantasia", "nome_estabelecimento", "estabelecimento", "nome_unidade", "nome_assentamento", "nome_terra_indigena", "tipo_ocorrencia"]:
            if cand in out.columns:
                nome_col = cand
                break
        out["rotulo"] = out[nome_col].astype(str) if nome_col else tabela
        out["camada"] = tabela
        val = validar_pontos_mapa_estrategico(out, lat_col="lat", lon_col="lon", municipio_col="municipio", codigo_col="codigo_ibge")
        inv = val[~val["ponto_utilizado_mapa_principal"].astype(bool)].copy()
        if inv.empty:
            continue
        keep = [c for c in [
            "camada", "rotulo", "municipio", "codigo_ibge", "lat", "lon",
            "status_validacao_geografica", "municipio_geografico_estimado",
            "codigo_ibge_geografico_estimado", "alerta_municipio_geografico",
            "coordenada_fora_faixa_mt",
        ] if c in inv.columns]
        registros.append(inv[keep])
    if not registros:
        return pd.DataFrame()
    return pd.concat(registros, ignore_index=True).head(limite)



# ============================================================================
# Proximidade territorial APS — v4
# ============================================================================

from math import asin, sqrt, radians, sin, cos as _cos


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância geodésica aproximada em quilômetros entre dois pontos."""
    r = 6371.0088
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + _cos(lat1) * _cos(lat2) * sin(dlon / 2) ** 2
    return float(2 * r * asin(sqrt(a)))


def _classificar_distancia_aps(dist_km: Any) -> str:
    try:
        d = float(dist_km)
    except Exception:
        return "Sem cálculo"
    if d <= 5:
        return "Próximo"
    if d <= 15:
        return "Atenção"
    if d <= 30:
        return "Distante"
    return "Crítico"


def _preparar_assentamentos_pontos() -> pd.DataFrame:
    df = _read_table_safe("dados_mt_assentamentos")
    if df.empty:
        return pd.DataFrame()
    lat_col = _pick_col(df, ["latitude_centroide", "latitude", "lat", "latitude_centroid"])
    lon_col = _pick_col(df, ["longitude_centroide", "longitude", "lon", "lng", "longitude_centroid"])
    if not lat_col or not lon_col:
        return pd.DataFrame()
    out = df.copy()
    out["lat_assentamento"] = pd.to_numeric(out[lat_col], errors="coerce")
    out["lon_assentamento"] = pd.to_numeric(out[lon_col], errors="coerce")
    out = out[_valid_lat_lon(out["lat_assentamento"], out["lon_assentamento"])].copy()
    if out.empty:
        return out
    if "municipio" not in out.columns:
        out["municipio"] = ""
    if "codigo_ibge" not in out.columns:
        out["codigo_ibge"] = ""
    nome_col = None
    for cand in ["nome_assentamento", "s_no", "nome", "rotulo"]:
        if cand in out.columns:
            nome_col = cand
            break
    out["assentamento"] = out[nome_col].astype(str).str.strip() if nome_col else "Assentamento"
    # Quando o nome principal veio como ato/portaria, tenta recuperar o nome real nos atributos_json.
    if "atributos_json" in out.columns:
        def _nome_atrib(row):
            atual = str(row.get("assentamento") or "").strip()
            if atual and not atual.lower().startswith("portaria"):
                return atual
            data = _safe_json_loads(row.get("atributos_json")) or {}
            for k in ["s_no", "nome", "nome_assentamento", "denominacao"]:
                v = str(data.get(k) or "").strip()
                if v:
                    return v
            return atual or "Assentamento"
        out["assentamento"] = out.apply(_nome_atrib, axis=1)
    keep = [c for c in [
        "id", "assentamento", "municipio", "codigo_ibge", "area_ha", "modalidade", "situacao",
        "lat_assentamento", "lon_assentamento", "fonte", "observacao",
    ] if c in out.columns]
    return out[keep].reset_index(drop=True)



def carregar_coordenadas_ubs_validadas() -> pd.DataFrame:
    """Carrega coordenadas validadas manualmente pela SES/ERS/município.

    Arquivo esperado: data/reference/ubs_coordenadas_validadas.csv

    Coordenadas deste arquivo têm prioridade sobre API/base automática quando:
    - CNES está preenchido;
    - usar_no_calculo = Sim;
    - latitude_validada/longitude_validada estão em faixa válida para MT.
    """
    arq = UBS_COORDENADAS_VALIDADAS_MANUAIS
    cols = [
        "regiao_saude", "municipio", "codigo_ibge", "cnes", "nome_unidade",
        "tipo_unidade", "endereco", "latitude_atual", "longitude_atual",
        "fonte_coordenada_atual", "status_atual", "latitude_validada",
        "longitude_validada", "fonte_validacao", "validado_por",
        "data_validacao", "observacao", "usar_no_calculo", "prioridade"
    ]
    if not arq.exists():
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_csv(arq, dtype=str, encoding="utf-8-sig").fillna("")
    except Exception:
        try:
            df = pd.read_csv(arq, dtype=str, sep=";", encoding="utf-8-sig").fillna("")
        except Exception:
            return pd.DataFrame(columns=cols)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df["cnes_norm"] = df["cnes"].map(_normalizar_cnes)
    df["lat_validada_num"] = pd.to_numeric(df["latitude_validada"].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    df["lon_validada_num"] = pd.to_numeric(df["longitude_validada"].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    df["usar_no_calculo_norm"] = df["usar_no_calculo"].astype(str).str.strip().str.lower().map({
        "sim": True, "s": True, "yes": True, "true": True, "1": True,
        "não": False, "nao": False, "n": False, "no": False, "false": False, "0": False,
    }).fillna(False)
    df["coordenada_validada_ok"] = [
        _lat_lon_mt_estrito(a, b) for a, b in zip(df["lat_validada_num"], df["lon_validada_num"])
    ]
    return df


def aplicar_coordenadas_ubs_validadas(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica coordenadas validadas por CNES, preservando coordenadas originais."""
    if df is None or df.empty:
        return df
    val = carregar_coordenadas_ubs_validadas()
    if val.empty or "cnes" not in df.columns:
        out = df.copy()
        out["coordenada_validada_manual"] = False
        out["fonte_coordenada_final"] = out.get("fonte", "base automática")
        out["status_confiabilidade_final"] = "Base automática/API — pendente de validação local"
        return out

    validas = val[
        val["cnes_norm"].astype(str).str.len().gt(0)
        & val["usar_no_calculo_norm"].astype(bool)
        & val["coordenada_validada_ok"].astype(bool)
    ].copy()
    if validas.empty:
        out = df.copy()
        out["coordenada_validada_manual"] = False
        out["fonte_coordenada_final"] = out.get("fonte", "base automática")
        out["status_confiabilidade_final"] = "Base automática/API — pendente de validação local"
        return out

    validas = validas.drop_duplicates("cnes_norm", keep="last")
    aux = validas[[
        "cnes_norm", "lat_validada_num", "lon_validada_num", "fonte_validacao",
        "validado_por", "data_validacao", "observacao"
    ]].copy()

    out = df.copy()
    out["cnes_norm"] = out["cnes"].map(_normalizar_cnes)
    out = out.merge(aux, on="cnes_norm", how="left")
    mask = out["lat_validada_num"].notna() & out["lon_validada_num"].notna()
    # Preserva coordenada original em colunas de auditoria.
    lat_col = _pick_col(out, LAT_CANDIDATES)
    lon_col = _pick_col(out, LON_CANDIDATES)
    if lat_col:
        out["latitude_original_base"] = pd.to_numeric(out[lat_col], errors="coerce")
        out.loc[mask, lat_col] = out.loc[mask, "lat_validada_num"]
    else:
        out["latitude_original_base"] = pd.NA
        out["latitude"] = pd.NA
        out.loc[mask, "latitude"] = out.loc[mask, "lat_validada_num"]
    if lon_col:
        out["longitude_original_base"] = pd.to_numeric(out[lon_col], errors="coerce")
        out.loc[mask, lon_col] = out.loc[mask, "lon_validada_num"]
    else:
        out["longitude_original_base"] = pd.NA
        out["longitude"] = pd.NA
        out.loc[mask, "longitude"] = out.loc[mask, "lon_validada_num"]

    out["coordenada_validada_manual"] = mask
    out["fonte_coordenada_final"] = out.get("fonte", "base automática").astype(str)
    out.loc[mask, "fonte_coordenada_final"] = "Planilha validada SES/ERS/município"
    out["status_confiabilidade_final"] = "Base automática/API — pendente de validação local"
    out.loc[mask, "status_confiabilidade_final"] = "Coordenada validada manualmente — prioridade máxima"
    return out.drop(columns=["lat_validada_num", "lon_validada_num"], errors="ignore")


def gerar_planilha_validacao_ubs() -> pd.DataFrame:
    """Gera base de validação de UBS/unidades com colunas para preenchimento manual."""
    qual = qualificar_unidades_aps_georreferenciadas()
    unidades = qual.get("unidades", pd.DataFrame()).copy()
    if unidades.empty:
        return pd.DataFrame(columns=[
            "regiao_saude", "municipio", "codigo_ibge", "cnes", "nome_unidade",
            "tipo_unidade", "endereco", "latitude_atual", "longitude_atual",
            "fonte_coordenada_atual", "status_atual", "latitude_validada",
            "longitude_validada", "fonte_validacao", "validado_por",
            "data_validacao", "observacao", "usar_no_calculo", "prioridade"
        ])
    out = pd.DataFrame()
    out["regiao_saude"] = unidades.get("regiao_saude", "")
    out["municipio"] = unidades.get("municipio", "")
    out["codigo_ibge"] = unidades.get("codigo_ibge", "")
    out["cnes"] = unidades.get("cnes", "")
    out["nome_unidade"] = unidades.get("nome_unidade", "")
    out["tipo_unidade"] = unidades.get("tipo_unidade", "")
    out["endereco"] = unidades.get("endereco", "")
    out["latitude_atual"] = unidades.get("latitude_decimal", "")
    out["longitude_atual"] = unidades.get("longitude_decimal", "")
    out["fonte_coordenada_atual"] = unidades.get("fonte_coordenada_final", unidades.get("fonte", "base automática/API"))
    out["status_atual"] = unidades.get("status_confiabilidade_coordenada", unidades.get("status_georreferencia", ""))
    out["latitude_validada"] = ""
    out["longitude_validada"] = ""
    out["fonte_validacao"] = ""
    out["validado_por"] = ""
    out["data_validacao"] = ""
    out["observacao"] = ""
    out["usar_no_calculo"] = ""
    out["prioridade"] = unidades.get("prioridade_georreferenciamento", "Média")
    # Prioriza pendentes, divergentes e sem validação manual.
    status = out["status_atual"].astype(str).str.lower()
    out.loc[status.str.contains("pendente|sem coordenada|divergente|fora", na=False), "prioridade"] = "Alta"
    return out.sort_values(["prioridade", "municipio", "nome_unidade"], ascending=[True, True, True]).reset_index(drop=True)


def _preparar_unidades_aps_georreferenciadas() -> pd.DataFrame:
    """Retorna unidades APS elegíveis com coordenadas confiáveis.

    Regra institucional: para distância da APS, usa apenas estabelecimentos com
    CNES vinculado a equipes APS/INE na tabela `equipes_aps` (70, 71, 72, 73, 74,
    76). Também deduplica por CNES, priorizando a linha com coordenada válida.
    """
    df = _read_table_safe("estabelecimentos_saude")
    if df.empty:
        return pd.DataFrame()
    df = aplicar_coordenadas_ubs_validadas(df)

    cnes_equipes, resumo_equipes = _resumo_cnes_equipes_aps()
    if not cnes_equipes:
        return pd.DataFrame()

    lat_col = _pick_col(df, LAT_CANDIDATES)
    lon_col = _pick_col(df, LON_CANDIDATES)
    if not lat_col or not lon_col:
        return pd.DataFrame()

    out = df.copy()
    if "cnes" not in out.columns:
        out["cnes"] = ""
    out["cnes_norm"] = out["cnes"].map(_normalizar_cnes)
    out = out[out["cnes_norm"].isin(cnes_equipes)].copy()
    if out.empty:
        return out

    if not resumo_equipes.empty:
        out = out.merge(resumo_equipes, on="cnes_norm", how="left")
    else:
        out["qtd_equipes_aps"] = 0
        out["tipos_equipes_aps"] = ""

    out["lat_ubs"] = pd.to_numeric(out[lat_col], errors="coerce")
    out["lon_ubs"] = pd.to_numeric(out[lon_col], errors="coerce")
    out["coord_valida_mt"] = [_lat_lon_mt_estrito(a, b) for a, b in zip(out["lat_ubs"], out["lon_ubs"])]
    out = _deduplicar_estabelecimentos_por_cnes(out)
    out = out[out["coord_valida_mt"].astype(bool)].copy()
    if out.empty:
        return out

    if "municipio" not in out.columns:
        out["municipio"] = ""
    if "codigo_ibge" not in out.columns:
        out["codigo_ibge"] = ""
    nome_col = None
    for cand in ["nome_unidade", "nome_fantasia", "nome_estabelecimento", "estabelecimento", "nome"]:
        if cand in out.columns:
            nome_col = cand
            break
    out["ubs_nome"] = out[nome_col].astype(str).str.strip() if nome_col else "Unidade APS"
    if "tipo_unidade" not in out.columns:
        out["tipo_unidade"] = ""
    keep = [c for c in [
        "cnes", "ubs_nome", "tipo_unidade", "municipio", "codigo_ibge", "lat_ubs", "lon_ubs",
        "qtd_equipes_aps", "tipos_equipes_aps", "endereco", "coordenada_validada_manual",
        "fonte_coordenada_final", "status_confiabilidade_final",
    ] if c in out.columns]
    return out[keep].reset_index(drop=True)


def _preparar_centroides_municipais() -> pd.DataFrame:
    base = montar_base_mapa_municipal()
    if base.empty:
        return pd.DataFrame()
    out = base.copy()
    out["lat_ubs"] = pd.to_numeric(out.get("latitude"), errors="coerce")
    out["lon_ubs"] = pd.to_numeric(out.get("longitude"), errors="coerce")
    out = out[_valid_lat_lon(out["lat_ubs"], out["lon_ubs"])].copy()
    if out.empty:
        return out
    out["cnes"] = ""
    out["ubs_nome"] = "Centroide municipal — aproximação, não UBS"
    out["tipo_unidade"] = "Referência territorial aproximada"
    return out[["cnes", "ubs_nome", "tipo_unidade", "municipio", "codigo_ibge", "lat_ubs", "lon_ubs"]].reset_index(drop=True)


def calcular_distancias_assentamentos_ubs(usar_aproximacao_municipal: bool = False) -> dict:
    """Calcula distância real de cada assentamento até a UBS georreferenciada mais próxima.

    A versão institucional do módulo não usa aproximação por centroide municipal.
    Se não houver UBS com latitude/longitude oficial válida, retorna tabelas vazias
    e diagnóstico de indisponibilidade para distância exata.
    """
    assent = _preparar_assentamentos_pontos()
    ubs = _preparar_unidades_aps_georreferenciadas()
    modo = "UBS georreferenciada"
    observacao = "Distância calculada por linha reta geodésica entre centroide do assentamento e unidade de saúde georreferenciada."
    diagnostico = {
        "assentamentos_com_coordenadas": int(len(assent)),
        "ubs_com_coordenadas": int(len(_preparar_unidades_aps_georreferenciadas())),
        "referencias_usadas": int(len(ubs)),
        "modo_calculo": modo,
        "observacao": observacao,
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
    }

    if assent.empty or ubs.empty:
        return {
            "distancias": pd.DataFrame(),
            "resumo_municipal": pd.DataFrame(),
            "resumo_regional": pd.DataFrame(),
            "diagnostico": diagnostico,
        }

    ref_mun = _municipios_referencia()[["municipio", "codigo_ibge", "regiao_saude"]].drop_duplicates("municipio")
    ubs_global = ubs.copy()
    linhas = []
    for _, a in assent.iterrows():
        lat_a = float(a.get("lat_assentamento"))
        lon_a = float(a.get("lon_assentamento"))
        municipio_a = str(a.get("municipio") or "").strip()
        ubs_tmp = ubs_global.copy()
        ubs_tmp["distancia_km"] = ubs_tmp.apply(
            lambda r: _haversine_km(lat_a, lon_a, float(r["lat_ubs"]), float(r["lon_ubs"])), axis=1
        )
        nearest = ubs_tmp.sort_values("distancia_km").head(1).iloc[0]

        mesma = ubs_tmp[ubs_tmp["municipio"].astype(str).map(_chave) == _chave(municipio_a)].copy()
        if not mesma.empty:
            nearest_mun = mesma.sort_values("distancia_km").head(1).iloc[0]
            dist_mun = round(float(nearest_mun["distancia_km"]), 2)
            ubs_mun = str(nearest_mun.get("ubs_nome") or "")
            cnes_mun = str(nearest_mun.get("cnes") or "")
        else:
            dist_mun = None
            ubs_mun = ""
            cnes_mun = ""

        dist = round(float(nearest["distancia_km"]), 2)
        linhas.append({
            "assentamento": a.get("assentamento"),
            "municipio": municipio_a,
            "codigo_ibge": str(a.get("codigo_ibge") or ""),
            "lat_assentamento": lat_a,
            "lon_assentamento": lon_a,
            "ubs_mais_proxima": nearest.get("ubs_nome"),
            "cnes_ubs_mais_proxima": nearest.get("cnes"),
            "municipio_ubs_mais_proxima": nearest.get("municipio"),
            "distancia_ubs_mais_proxima_km": dist,
            "classe_distancia_aps": _classificar_distancia_aps(dist),
            "ubs_mais_proxima_mesmo_municipio": ubs_mun,
            "cnes_ubs_mesmo_municipio": cnes_mun,
            "distancia_ubs_mesmo_municipio_km": dist_mun,
            "classe_distancia_mesmo_municipio": _classificar_distancia_aps(dist_mun),
            "lat_ubs": float(nearest.get("lat_ubs")),
            "lon_ubs": float(nearest.get("lon_ubs")),
            "modo_calculo": modo,
            "observacao_metodologica": observacao,
        })

    dist_df = pd.DataFrame(linhas)
    if not ref_mun.empty:
        dist_df = dist_df.merge(ref_mun[["municipio", "regiao_saude"]], on="municipio", how="left")
    else:
        dist_df["regiao_saude"] = "Não informada"

    ordem_classe = {"Próximo": 1, "Atenção": 2, "Distante": 3, "Crítico": 4, "Sem cálculo": 0}
    dist_df["ordem_classe"] = dist_df["classe_distancia_aps"].map(ordem_classe).fillna(0)
    dist_df = dist_df.sort_values(["ordem_classe", "distancia_ubs_mais_proxima_km"], ascending=[False, False]).reset_index(drop=True)

    resumo_mun = pd.DataFrame()
    if not dist_df.empty:
        resumo_mun = dist_df.groupby(["municipio", "regiao_saude"], dropna=False).agg(
            assentamentos=("assentamento", "count"),
            distancia_media_km=("distancia_ubs_mais_proxima_km", "mean"),
            distancia_maxima_km=("distancia_ubs_mais_proxima_km", "max"),
            criticos=("classe_distancia_aps", lambda s: int((s == "Crítico").sum())),
            distantes=("classe_distancia_aps", lambda s: int((s == "Distante").sum())),
            atencao=("classe_distancia_aps", lambda s: int((s == "Atenção").sum())),
            proximos=("classe_distancia_aps", lambda s: int((s == "Próximo").sum())),
        ).reset_index()
        resumo_mun["distancia_media_km"] = resumo_mun["distancia_media_km"].round(2)
        resumo_mun["distancia_maxima_km"] = resumo_mun["distancia_maxima_km"].round(2)
        resumo_mun["nivel_alerta_assentamentos"] = resumo_mun.apply(
            lambda r: "Crítico" if r["criticos"] > 0 else "Alto" if r["distantes"] > 0 else "Atenção" if r["atencao"] > 0 else "Monitoramento",
            axis=1,
        )
        resumo_mun = resumo_mun.sort_values(["criticos", "distancia_maxima_km", "assentamentos"], ascending=[False, False, False])

    resumo_reg = pd.DataFrame()
    if not resumo_mun.empty:
        resumo_reg = resumo_mun.groupby("regiao_saude", dropna=False).agg(
            municipios_com_assentamentos=("municipio", "count"),
            assentamentos=("assentamentos", "sum"),
            distancia_media_regional_km=("distancia_media_km", "mean"),
            maior_distancia_km=("distancia_maxima_km", "max"),
            assentamentos_criticos=("criticos", "sum"),
            assentamentos_distantes=("distantes", "sum"),
        ).reset_index()
        resumo_reg["distancia_media_regional_km"] = resumo_reg["distancia_media_regional_km"].round(2)
        resumo_reg = resumo_reg.sort_values(["assentamentos_criticos", "maior_distancia_km"], ascending=[False, False])

    return {
        "distancias": dist_df,
        "resumo_municipal": resumo_mun,
        "resumo_regional": resumo_reg,
        "diagnostico": diagnostico,
    }



# ============================================================================
# Qualificação geográfica das UBS — v5
# ============================================================================

def qualificar_unidades_aps_georreferenciadas() -> dict:
    """Diagnostica a prontidão da base de UBS/unidades para cálculos exatos de distância.

    Esta rotina não cria coordenadas aproximadas e não geocodifica endereços.
    Ela apenas informa o que já existe na tabela estruturada e prepara uma fila
    objetiva de unidades que precisam de coordenadas oficiais para permitir
    distância real assentamento/território especial -> UBS.
    """
    df = _read_table_safe("estabelecimentos_saude")
    if not df.empty:
        df = aplicar_coordenadas_ubs_validadas(df)
    vazio = {
        "diagnostico": {
            "total_unidades": 0,
            "com_coordenadas_validas": 0,
            "sem_coordenadas_validas": 0,
            "percentual_georreferenciado": 0.0,
            "coluna_latitude": "não localizada",
            "coluna_longitude": "não localizada",
            "status_prontidao": "Base não disponível",
            "mensagem": "A tabela estabelecimentos_saude não foi encontrada ou está vazia.",
        },
        "unidades": pd.DataFrame(),
        "sem_coordenadas": pd.DataFrame(),
        "resumo_municipal": pd.DataFrame(),
    }
    if df.empty:
        return vazio

    lat_col = _pick_col(df, LAT_CANDIDATES)
    lon_col = _pick_col(df, LON_CANDIDATES)
    out = deduplicar_estabelecimentos_saude(df.copy())
    mun_col = _pick_col(out, MUNICIPIO_CANDIDATES) or "municipio"
    cod_col = _pick_col(out, CODIGO_CANDIDATES)

    nome_col = None
    for cand in ["nome_unidade", "nome_fantasia", "nome_estabelecimento", "estabelecimento", "nome"]:
        if cand in out.columns:
            nome_col = cand
            break

    if lat_col and lon_col:
        out["latitude_decimal"] = pd.to_numeric(out[lat_col], errors="coerce")
        out["longitude_decimal"] = pd.to_numeric(out[lon_col], errors="coerce")
        valido = _valid_lat_lon(out["latitude_decimal"], out["longitude_decimal"])
    else:
        out["latitude_decimal"] = pd.NA
        out["longitude_decimal"] = pd.NA
        valido = pd.Series(False, index=out.index)

    out["status_georreferencia"] = valido.map(lambda x: "Georreferenciada" if bool(x) else "Sem coordenada oficial válida")
    out["status_confiabilidade_coordenada"] = valido.map(lambda x: "Coordenada existente na base — usar com validação de município/malha" if bool(x) else "Pendente de coordenada oficial ou validação técnica")
    out["prioridade_georreferenciamento"] = "Média"
    out["municipio"] = out[mun_col].astype(str).str.strip() if mun_col in out.columns else ""
    out["codigo_ibge"] = out[cod_col].astype(str).str.strip() if cod_col and cod_col in out.columns else ""
    out["nome_unidade"] = out[nome_col].astype(str).str.strip() if nome_col else "Unidade de saúde"
    if "cnes" not in out.columns:
        out["cnes"] = ""
    if "tipo_unidade" not in out.columns:
        out["tipo_unidade"] = ""
    if "endereco" not in out.columns:
        out["endereco"] = ""

    # Fila de trabalho: unidades sem coordenada em municípios com maior população/uso entram primeiro.
    out["chave_busca_coordenada"] = (
        out["nome_unidade"].astype(str).str.strip()
        + " | CNES " + out["cnes"].astype(str).str.strip()
        + " | " + out["endereco"].astype(str).str.strip()
        + " | " + out["municipio"].astype(str).str.strip()
        + " - MT"
    )
    out.loc[~valido, "prioridade_georreferenciamento"] = "Alta"
    out["acao_recomendada_georreferenciamento"] = valido.map(
        lambda x: "Manter em uso e validar periodicamente município/malha." if bool(x)
        else "Buscar coordenada oficial CNES/MS ou validar ponto com SES/ERS/município antes de usar no cálculo definitivo."
    )

    total = int(len(out))
    com_geo = int(valido.sum())
    sem_geo = total - com_geo
    pct = round((com_geo / total * 100), 2) if total else 0.0
    if com_geo == 0:
        prontidao = "Indisponível para distância exata"
        mensagem = "Nenhuma UBS/unidade da tabela estabelecimentos_saude possui latitude/longitude oficial válida. Distâncias reais até UBS não devem ser calculadas ainda."
    elif pct < 80:
        prontidao = "Parcial — exige qualificação antes de uso oficial"
        mensagem = "Há algumas unidades com coordenadas, mas a cobertura geográfica ainda é insuficiente para análise estadual oficial de distância."
    else:
        prontidao = "Apta para análise de distância exata"
        mensagem = "A maior parte das unidades possui coordenadas válidas. A camada pode ser usada para cálculo real de proximidade."

    cols_unidades = [c for c in [
        "cnes", "nome_unidade", "tipo_unidade", "municipio", "codigo_ibge", "endereco",
        "latitude_decimal", "longitude_decimal", "status_georreferencia", "status_confiabilidade_coordenada",
        "coordenada_validada_manual", "fonte_coordenada_final", "status_confiabilidade_final",
        "prioridade_georreferenciamento", "acao_recomendada_georreferenciamento", "chave_busca_coordenada", "fonte", "atualizado_em",
    ] if c in out.columns]
    unidades = out[cols_unidades].copy()
    sem = unidades[unidades["status_georreferencia"] != "Georreferenciada"].copy()

    resumo = pd.DataFrame()
    if "municipio" in unidades.columns and not unidades.empty:
        resumo = unidades.groupby("municipio", dropna=False).agg(
            unidades=("nome_unidade", "count"),
            georreferenciadas=("status_georreferencia", lambda s: int((s == "Georreferenciada").sum())),
            pendentes=("status_georreferencia", lambda s: int((s != "Georreferenciada").sum())),
        ).reset_index()
        resumo["percentual_georreferenciado"] = (resumo["georreferenciadas"] / resumo["unidades"] * 100).round(2)
        resumo = resumo.sort_values(["pendentes", "unidades"], ascending=[False, False])

    return {
        "diagnostico": {
            "total_unidades": total,
            "total_unidades_unicas_cnes": total,
            "com_coordenadas_validas": com_geo,
            "sem_coordenadas_validas": sem_geo,
            "percentual_georreferenciado": pct,
            "coluna_latitude": lat_col or "não localizada",
            "coluna_longitude": lon_col or "não localizada",
            "status_prontidao": prontidao,
            "mensagem": mensagem,
        },
        "unidades": unidades,
        "sem_coordenadas": sem,
        "resumo_municipal": resumo,
    }


# ============================================================================
# Qualificação oficial de UBS — tentativa via API pública de Dados Abertos MS
# ============================================================================

DADOS_ABERTOS_MS_UBS_URLS = [
    "https://apidadosabertos.saude.gov.br/assistencia-a-saude/unidade-basicas-de-saude",
    "https://apidadosabertos.saude.gov.br/v1/assistencia-a-saude/unidade-basicas-de-saude",
]


def _normalizar_numero_coord(valor: Any) -> float | None:
    """Converte coordenadas vindas como texto, vírgula decimal ou número.

    Não tenta corrigir coordenadas absurdas por inferência. Se não estiver na
    faixa esperada para Mato Grosso, retorna None.
    """
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto or texto.lower() in {"nan", "none", "null", "sem informação", "sem informacao"}:
        return None
    texto = texto.replace("º", "").replace("°", "").replace(";", " ")
    # Se vier no padrão brasileiro, troca vírgula por ponto. Se houver separador de milhar,
    # mantém a conversão conservadora para não fabricar coordenada.
    if "," in texto and "." not in texto:
        texto = texto.replace(",", ".")
    import re
    m = re.search(r"-?\d+(?:\.\d+)?", texto)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


def _coord_mt_valida(lat: Any, lon: Any) -> bool:
    """Valida coordenada dentro de Mato Grosso, com faixa conservadora.

    A versão anterior usava uma faixa muito ampla para o Brasil central e
    aceitava pontos de MS/SP como se fossem MT. Para atualização de UBS, a
    regra precisa ser conservadora: só entram coordenadas que caem no retângulo
    aproximado de Mato Grosso.
    """
    latf = _normalizar_numero_coord(lat)
    lonf = _normalizar_numero_coord(lon)
    if latf is None or lonf is None:
        return False
    return -19.8 <= latf <= -7.0 and -62.5 <= lonf <= -50.0


def _extract_records_from_json(payload: Any) -> list[dict[str, Any]]:
    """Extrai lista de registros de respostas JSON em formatos comuns."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ["data", "dados", "items", "results", "resultado", "content", "registros", "records"]:
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            nested = _extract_records_from_json(value)
            if nested:
                return nested
    # Alguns endpoints retornam dict indexado por strings numéricas.
    vals = list(payload.values())
    if vals and all(isinstance(x, dict) for x in vals):
        return vals
    return []


def _get_nested(row: dict[str, Any], candidates: list[str]) -> Any:
    """Busca campo por nome direto ou por busca rasa em subdicts."""
    low = {str(k).lower(): k for k in row.keys()}
    for cand in candidates:
        if cand.lower() in low:
            return row.get(low[cand.lower()])
    # busca por inclusão: útil para campos como nu_latitude, co_cnes etc.
    for cand in candidates:
        token = cand.lower()
        for lk, original in low.items():
            if token in lk:
                return row.get(original)
    for value in row.values():
        if isinstance(value, dict):
            found = _get_nested(value, candidates)
            if found not in (None, ""):
                return found
    return None


def _registro_api_ubs_eh_mt(row: dict[str, Any]) -> bool:
    """Confirma se o registro nacional da API de UBS pertence a Mato Grosso.

    A API pública respondeu JSON, mas ignorou os filtros por UF. Por isso a
    filtragem precisa ser local e objetiva. Só aceitamos registros quando:
    - uf/código UF indica 51 ou MT; ou
    - código IBGE do município começa com 51.
    """
    uf = _get_nested(row, ["uf", "sg_uf", "sigla_uf", "estado", "co_uf", "codigo_uf"])
    codigo_ibge = _get_nested(row, ["ibge", "codigo_ibge", "co_municipio_ibge", "cod_ibge", "codigo_municipio", "co_municipio"])
    uf_txt = _chave(uf)
    codigo = _codigo_limpo(codigo_ibge)
    if uf_txt in {"51", "MT", "MATO GROSSO"}:
        return True
    if codigo.startswith("51"):
        return True
    return False


def _normalizar_registro_ubs_oficial(row: dict[str, Any]) -> dict[str, Any] | None:
    """Normaliza campos de UBS vindos da API pública oficial.

    A rotina é conservadora e própria para a API nacional: primeiro confirma se
    o registro é de Mato Grosso pelo campo UF/IBGE; depois valida se a
    latitude/longitude caem no retângulo aproximado de MT. Não usa centroide,
    endereço geocodificado ou aproximação.
    """
    if not _registro_api_ubs_eh_mt(row):
        return None

    cnes = _get_nested(row, ["cnes", "co_cnes", "codigo_cnes", "cod_cnes", "codigoCNES"])
    nome = _get_nested(row, ["nome_unidade", "no_fantasia", "nome_fantasia", "no_estabelecimento", "nome", "estabelecimento"])
    municipio = _get_nested(row, ["municipio", "no_municipio", "nome_municipio", "cidade"])
    codigo_ibge = _get_nested(row, ["ibge", "codigo_ibge", "co_municipio_ibge", "cod_ibge", "codigo_municipio", "co_municipio"])
    uf = _get_nested(row, ["uf", "sg_uf", "sigla_uf", "estado", "co_uf", "codigo_uf"])
    endereco = _get_nested(row, ["endereco", "logradouro", "ds_logradouro", "endereço"])
    bairro = _get_nested(row, ["bairro", "no_bairro", "nome_bairro"])
    tipo = _get_nested(row, ["tipo_unidade", "tipo_estabelecimento", "ds_tipo_unidade", "natureza"])
    lat = _get_nested(row, ["latitude", "lat", "nu_latitude", "vl_latitude", "coord_lat", "y"])
    lon = _get_nested(row, ["longitude", "lon", "lng", "long", "nu_longitude", "vl_longitude", "coord_lon", "x"])

    latf = _normalizar_numero_coord(lat)
    lonf = _normalizar_numero_coord(lon)
    if not _coord_mt_valida(latf, lonf):
        return None

    codigo = _codigo_limpo(codigo_ibge)
    if codigo and not codigo.startswith("51"):
        return None
    uf_txt = _chave(uf)
    if uf_txt and uf_txt not in {"51", "MT", "MATO GROSSO"} and not codigo.startswith("51"):
        return None

    cnes_txt = str(cnes or "").strip().zfill(7) if str(cnes or "").strip().isdigit() else str(cnes or "").strip()
    nome_txt = str(nome or "").strip()
    municipio_txt = str(municipio or "").strip()
    if not cnes_txt and not (nome_txt and municipio_txt):
        return None

    endereco_txt = str(endereco or "").strip()
    bairro_txt = str(bairro or "").strip()
    if bairro_txt and bairro_txt.upper() not in endereco_txt.upper():
        endereco_composto = f"{endereco_txt} - {bairro_txt}" if endereco_txt else bairro_txt
    else:
        endereco_composto = endereco_txt

    return {
        "cnes": cnes_txt,
        "nome_unidade": nome_txt,
        "municipio": municipio_txt,
        "codigo_ibge": codigo,
        "tipo_unidade": str(tipo or "").strip(),
        "endereco": endereco_composto,
        "bairro": bairro_txt,
        "latitude": float(latf),
        "longitude": float(lonf),
        "fonte": "Dados Abertos MS — UBS/CNES com filtro local MT",
    }

def _buscar_ubs_oficiais_dados_abertos_ms(max_paginas: int = 80, page_size: int = 100) -> dict:
    """Busca UBS georreferenciadas na API nacional e filtra localmente MT.

    O diagnóstico mostrou que o endpoint retorna JSON com campos latitude e
    longitude, mas ignora filtros por UF. A solução segura é coletar registros
    nacionais, filtrar localmente por UF/IBGE de Mato Grosso e aceitar apenas
    coordenadas que caem no retângulo de MT.
    """
    registros: list[dict[str, Any]] = []
    tentativas: list[str] = []
    headers = {"Accept": "application/json", "User-Agent": "plataforma-aps-inteligencia/1.0"}

    # Mantemos filtros conhecidos só como tentativa, mas a segurança real é o filtro local.
    sementes = [
        {},
        {"uf": "MT"},
        {"sigla_uf": "MT"},
        {"estado": "MT"},
        {"co_uf": "51"},
        {"uf": "51"},
    ]
    paginadores = [
        ("sem_paginacao", lambda page: {} if page == 1 else None),
        ("page_per_page", lambda page: {"page": page, "per_page": page_size}),
        ("pagina_tamanho", lambda page: {"pagina": page, "tamanho": page_size}),
        ("offset_limit", lambda page: {"offset": (page - 1) * page_size, "limit": page_size}),
        ("limit_offset", lambda page: {"limit": page_size, "offset": (page - 1) * page_size}),
    ]

    total_rows_lidos = 0
    total_rows_mt_sem_coord = 0
    total_rows_mt_com_coord_invalida = 0
    fingerprints_globais: set[str] = set()

    def fingerprint_rows(rows: list[dict[str, Any]]) -> str:
        ids = []
        for r in rows[:30]:
            ids.append(str(_get_nested(r, ["cnes", "co_cnes", "codigo_cnes", "nome", "ibge", "uf"]) or ""))
        return f"{len(rows)}|" + "|".join(ids)

    for base_url in DADOS_ABERTOS_MS_UBS_URLS:
        for seed in sementes:
            for nome_paginador, pag_func in paginadores:
                fingerprints_variant: set[str] = set()
                for page in range(1, max_paginas + 1):
                    pag = pag_func(page)
                    if pag is None:
                        break
                    params = {**seed, **pag}
                    try:
                        resp = requests.get(base_url, params=params, headers=headers, timeout=60)
                        ct = resp.headers.get("content-type", "")
                        if page == 1 or page % 10 == 0:
                            tentativas.append(f"{resp.url} | status={resp.status_code} | ct={ct} | modo={nome_paginador}")
                        if not resp.ok or "json" not in ct.lower():
                            break
                        rows = _extract_records_from_json(resp.json())
                        if not rows:
                            break
                        total_rows_lidos += len(rows)
                        fp = fingerprint_rows(rows)
                        if fp in fingerprints_variant:
                            tentativas.append(f"{resp.url} | repetição detectada; paginação {nome_paginador} encerrada")
                            break
                        fingerprints_variant.add(fp)
                        fingerprints_globais.add(fp)

                        for row in rows:
                            if not _registro_api_ubs_eh_mt(row):
                                continue
                            lat = _get_nested(row, ["latitude", "lat", "nu_latitude", "vl_latitude", "coord_lat", "y"])
                            lon = _get_nested(row, ["longitude", "lon", "lng", "long", "nu_longitude", "vl_longitude", "coord_lon", "x"])
                            if lat in (None, "") or lon in (None, ""):
                                total_rows_mt_sem_coord += 1
                                continue
                            norm = _normalizar_registro_ubs_oficial(row)
                            if norm:
                                registros.append(norm)
                            else:
                                total_rows_mt_com_coord_invalida += 1

                        # Se a página veio menor que o tamanho pedido, acabou a paginação real.
                        if len(rows) < page_size:
                            break
                    except Exception as exc:
                        if page == 1:
                            tentativas.append(f"{base_url} params={params} | erro={exc}")
                        break

    dedup = _dedup_ubs_oficiais(registros)
    tentativas.append(
        f"Resumo coleta nacional: linhas_lidas={total_rows_lidos}; registros_mt_com_coord_valida={len(dedup)}; "
        f"mt_sem_coord={total_rows_mt_sem_coord}; mt_coord_invalida={total_rows_mt_com_coord_invalida}; fingerprints={len(fingerprints_globais)}"
    )
    if not dedup:
        tentativas.append("Coleta nacional não encontrou MT nos registros paginados. Iniciando busca direcionada por CNES/IBGE.")
        direcionada = _buscar_ubs_api_ms_direcionada_por_cnes_ibge()
        tentativas.extend(direcionada.get("tentativas", []))
        dedup = _dedup_ubs_oficiais(direcionada.get("registros", []))
    return {
        "registros": dedup,
        "tentativas": tentativas,
        "sucesso": bool(dedup),
        "linhas_lidas": total_rows_lidos,
        "mt_sem_coord": total_rows_mt_sem_coord,
        "mt_coord_invalida": total_rows_mt_com_coord_invalida,
    }


def _buscar_ubs_api_ms_direcionada_por_cnes_ibge(max_testes_cnes: int = 8, max_testes_municipio: int = 12) -> dict:
    """Tentativa complementar: consulta direcionada por CNES e por código IBGE municipal.

    O endpoint público de UBS respondeu JSON, mas ignorou filtros de UF. Esta rotina
    testa se o mesmo endpoint respeita filtros mais específicos, como CNES ou IBGE.
    Só retorna registros quando o CNES/IBGE é confirmado e a coordenada cai em MT.
    Não usa geocodificação, centroide ou aproximação.
    """
    tentativas: list[str] = []
    registros: list[dict[str, Any]] = []
    headers = {"Accept": "application/json", "User-Agent": "plataforma-aps-inteligencia/1.0"}

    locais = _read_table_safe("estabelecimentos_saude")
    if locais.empty:
        tentativas.append("Busca direcionada: tabela estabelecimentos_saude vazia.")
        return {"registros": [], "tentativas": tentativas, "sucesso": False}

    locais = locais.copy()
    if "cnes" not in locais.columns:
        locais["cnes"] = ""
    if "codigo_ibge" not in locais.columns:
        locais["codigo_ibge"] = ""
    if "municipio" not in locais.columns:
        locais["municipio"] = ""

    cnes_lista = []
    for v in locais["cnes"].dropna().astype(str):
        d = ''.join(ch for ch in v if ch.isdigit())
        if d:
            cnes_lista.append(d.zfill(7))
    cnes_lista = list(dict.fromkeys(cnes_lista))

    codigos_ibge = []
    for v in locais["codigo_ibge"].dropna().astype(str):
        c = _codigo_limpo(v)
        if c.startswith("51") and len(c) >= 6:
            codigos_ibge.append(c[:7] if len(c) >= 7 else c)
    if not codigos_ibge:
        # fallback na tabela de municípios, se a base de estabelecimentos não tiver código IBGE.
        mun_ref = _read_table_safe("municipios")
        if not mun_ref.empty and "codigo_ibge" in mun_ref.columns:
            for v in mun_ref["codigo_ibge"].dropna().astype(str):
                c = _codigo_limpo(v)
                if c.startswith("51") and len(c) >= 6:
                    codigos_ibge.append(c[:7] if len(c) >= 7 else c)
    codigos_ibge = list(dict.fromkeys(codigos_ibge))

    def _json_rows(resp) -> list[dict[str, Any]]:
        ct = resp.headers.get("content-type", "")
        if not resp.ok or "json" not in ct.lower():
            return []
        try:
            return _extract_records_from_json(resp.json())
        except Exception:
            return []

    def _registrar_rows_validas(rows: list[dict[str, Any]], cnes_alvo: str | None = None, ibge_alvo: str | None = None) -> int:
        antes = len(registros)
        for row in rows:
            # trava adicional: se a consulta foi por CNES, o CNES retornado precisa bater.
            if cnes_alvo:
                cnes_ret = str(_get_nested(row, ["cnes", "co_cnes", "codigo_cnes", "cod_cnes"]) or "")
                cnes_ret = ''.join(ch for ch in cnes_ret if ch.isdigit()).zfill(7) if any(ch.isdigit() for ch in cnes_ret) else cnes_ret
                if cnes_ret and cnes_ret != cnes_alvo:
                    continue
            # trava adicional: se a consulta foi por município, o IBGE retornado precisa bater ou iniciar pelo mesmo código.
            if ibge_alvo:
                cod_ret = _codigo_limpo(_get_nested(row, ["ibge", "codigo_ibge", "co_municipio_ibge", "cod_ibge", "codigo_municipio", "co_municipio"]))
                alvo6 = ibge_alvo[:6]
                alvo7 = ibge_alvo[:7]
                if cod_ret and not (cod_ret.startswith(alvo6) or cod_ret.startswith(alvo7)):
                    continue
            norm = _normalizar_registro_ubs_oficial(row)
            if norm:
                registros.append(norm)
        return len(registros) - antes

    # 1) Descobrir se algum modo por CNES é respeitado pelo endpoint.
    modos_cnes = [
        ("param_cnes", lambda base, c: (base, {"cnes": c})),
        ("param_co_cnes", lambda base, c: (base, {"co_cnes": c})),
        ("param_codigo_cnes", lambda base, c: (base, {"codigo_cnes": c})),
        ("path_cnes", lambda base, c: (base.rstrip("/") + "/" + c, {})),
    ]
    modos_cnes_funcionais = []
    for nome_modo, builder in modos_cnes:
        achou_no_modo = False
        for base_url in DADOS_ABERTOS_MS_UBS_URLS:
            for cnes in cnes_lista[:max_testes_cnes]:
                try:
                    url, params = builder(base_url, cnes)
                    resp = requests.get(url, params=params, headers=headers, timeout=25)
                    rows = _json_rows(resp)
                    qtd = _registrar_rows_validas(rows, cnes_alvo=cnes)
                    tentativas.append(f"CNES teste | modo={nome_modo} | cnes={cnes} | status={resp.status_code} | rows={len(rows)} | validos={qtd}")
                    if qtd > 0:
                        modos_cnes_funcionais.append((nome_modo, builder, base_url))
                        achou_no_modo = True
                        break
                except Exception as exc:
                    tentativas.append(f"CNES teste | modo={nome_modo} | cnes={cnes} | erro={exc}")
            if achou_no_modo:
                break

    # Se algum modo por CNES funcionou, usa-o para toda a base local.
    if modos_cnes_funcionais:
        tentativas.append(f"Modo(s) por CNES funcionais: {', '.join(sorted({m[0] for m in modos_cnes_funcionais}))}. Processando CNES locais.")
        for nome_modo, builder, base_url in modos_cnes_funcionais[:2]:
            for idx, cnes in enumerate(cnes_lista):
                try:
                    url, params = builder(base_url, cnes)
                    resp = requests.get(url, params=params, headers=headers, timeout=25)
                    rows = _json_rows(resp)
                    _registrar_rows_validas(rows, cnes_alvo=cnes)
                    if idx and idx % 250 == 0:
                        tentativas.append(f"CNES processamento | modo={nome_modo} | processados={idx} | registros_validos_acumulados={len(registros)}")
                except Exception as exc:
                    if idx < 10:
                        tentativas.append(f"CNES processamento | modo={nome_modo} | cnes={cnes} | erro={exc}")

    # 2) Se CNES não funcionou, tenta filtros por IBGE municipal.
    if not registros:
        modos_ibge = [
            ("param_ibge7", lambda base, cod: (base, {"ibge": cod[:7]})),
            ("param_codigo_ibge7", lambda base, cod: (base, {"codigo_ibge": cod[:7]})),
            ("param_co_municipio7", lambda base, cod: (base, {"co_municipio": cod[:7]})),
            ("param_codigo_municipio7", lambda base, cod: (base, {"codigo_municipio": cod[:7]})),
            ("param_co_municipio6", lambda base, cod: (base, {"co_municipio": cod[:6]})),
            ("param_codigo_municipio6", lambda base, cod: (base, {"codigo_municipio": cod[:6]})),
        ]
        modos_ibge_funcionais = []
        for nome_modo, builder in modos_ibge:
            achou_no_modo = False
            for base_url in DADOS_ABERTOS_MS_UBS_URLS:
                for cod in codigos_ibge[:max_testes_municipio]:
                    try:
                        url, params = builder(base_url, cod)
                        resp = requests.get(url, params=params, headers=headers, timeout=25)
                        rows = _json_rows(resp)
                        qtd = _registrar_rows_validas(rows, ibge_alvo=cod)
                        tentativas.append(f"IBGE teste | modo={nome_modo} | ibge={cod} | status={resp.status_code} | rows={len(rows)} | validos={qtd}")
                        if qtd > 0:
                            modos_ibge_funcionais.append((nome_modo, builder, base_url))
                            achou_no_modo = True
                            break
                    except Exception as exc:
                        tentativas.append(f"IBGE teste | modo={nome_modo} | ibge={cod} | erro={exc}")
                if achou_no_modo:
                    break
        if modos_ibge_funcionais:
            tentativas.append(f"Modo(s) por IBGE funcionais: {', '.join(sorted({m[0] for m in modos_ibge_funcionais}))}. Processando municípios de MT.")
            for nome_modo, builder, base_url in modos_ibge_funcionais[:2]:
                for idx, cod in enumerate(codigos_ibge):
                    try:
                        url, params = builder(base_url, cod)
                        resp = requests.get(url, params=params, headers=headers, timeout=25)
                        rows = _json_rows(resp)
                        _registrar_rows_validas(rows, ibge_alvo=cod)
                        if idx and idx % 40 == 0:
                            tentativas.append(f"IBGE processamento | modo={nome_modo} | processados={idx} | registros_validos_acumulados={len(registros)}")
                    except Exception as exc:
                        if idx < 10:
                            tentativas.append(f"IBGE processamento | modo={nome_modo} | ibge={cod} | erro={exc}")

    dedup = _dedup_ubs_oficiais(registros)
    tentativas.append(
        f"Resumo busca direcionada: cnes_locais={len(cnes_lista)}; municipios_testados={len(codigos_ibge)}; registros_validos={len(dedup)}"
    )
    return {
        "registros": dedup,
        "tentativas": tentativas,
        "sucesso": bool(dedup),
        "linhas_lidas": None,
        "mt_sem_coord": None,
        "mt_coord_invalida": None,
    }

def _dedup_ubs_oficiais(registros: list[dict[str, Any]]) -> list[dict[str, Any]]:
    vistos = set()
    out = []
    for r in registros:
        chave = r.get("cnes") or f"{_chave(r.get('municipio'))}|{_chave(r.get('nome_unidade'))}|{r.get('latitude')}|{r.get('longitude')}"
        if chave in vistos:
            continue
        vistos.add(chave)
        out.append(r)
    return out


def enriquecer_ubs_com_coordenadas_oficiais_ms() -> dict:
    """Atualiza estabelecimentos_saude com coordenadas oficiais de UBS, sem aproximação.

    A função tenta parear primeiro por CNES. Quando o CNES não existir na base
    local, tenta pareamento conservador por município + nome normalizado. Não
    altera coordenadas já válidas, exceto quando o CNES é o mesmo e a fonte
    oficial traz coordenadas válidas.
    """
    agora = datetime.now().isoformat(timespec="seconds")
    busca = _buscar_ubs_oficiais_dados_abertos_ms()
    oficiais = pd.DataFrame(busca.get("registros", []))
    if oficiais.empty:
        return {
            "ok": False,
            "mensagem": "Não foi possível obter UBS com latitude/longitude válida na API pública oficial nesta tentativa.",
            "tentativas": busca.get("tentativas", [])[:20],
            "oficiais_lidas": 0,
            "atualizadas_por_cnes": 0,
            "atualizadas_por_nome": 0,
            "inseridas_novas": 0,
        }

    locais = _read_table_safe("estabelecimentos_saude")
    if locais.empty:
        return {
            "ok": False,
            "mensagem": "A tabela estabelecimentos_saude está vazia. Carregue primeiro a base de UBS/estabelecimentos APS.",
            "tentativas": busca.get("tentativas", [])[:20],
            "oficiais_lidas": int(len(oficiais)),
            "atualizadas_por_cnes": 0,
            "atualizadas_por_nome": 0,
            "inseridas_novas": 0,
        }

    locais = locais.copy()
    locais["id_local"] = locais.get("id", pd.Series(range(len(locais)))).astype(str)
    if "cnes" not in locais.columns:
        locais["cnes"] = ""
    if "municipio" not in locais.columns:
        locais["municipio"] = ""
    if "nome_unidade" not in locais.columns:
        locais["nome_unidade"] = ""
    locais["cnes_chave"] = locais["cnes"].astype(str).str.strip()
    locais["nome_chave"] = locais["nome_unidade"].map(_chave)
    locais["mun_chave"] = locais["municipio"].map(_chave)

    oficiais["cnes_chave"] = oficiais["cnes"].astype(str).str.strip()
    oficiais["nome_chave"] = oficiais["nome_unidade"].map(_chave)
    oficiais["mun_chave"] = oficiais["municipio"].map(_chave)

    atualizadas_cnes = 0
    atualizadas_nome = 0
    inseridas = 0
    atualizados_ids: set[str] = set()

    with db_session() as conn:
        # Pareamento por CNES: critério mais seguro.
        for _, row in oficiais[oficiais["cnes_chave"].astype(str).str.len() > 0].iterrows():
            cnes = row["cnes_chave"]
            match = locais[locais["cnes_chave"] == cnes]
            if match.empty:
                continue
            for _, loc in match.iterrows():
                conn.execute(
                    """
                    UPDATE estabelecimentos_saude
                    SET latitude = ?, longitude = ?, codigo_ibge = COALESCE(NULLIF(codigo_ibge,''), ?),
                        municipio = COALESCE(NULLIF(municipio,''), ?), endereco = COALESCE(NULLIF(endereco,''), ?),
                        fonte = ?, atualizado_em = ?
                    WHERE cnes = ?
                    """,
                    (float(row["latitude"]), float(row["longitude"]), row.get("codigo_ibge", ""), row.get("municipio", ""), row.get("endereco", ""), row.get("fonte"), agora, cnes),
                )
                atualizadas_cnes += 1
                atualizados_ids.add(str(loc.get("id_local")))

        # Pareamento por município + nome, apenas para registros ainda não atualizados e com nome forte.
        for _, row in oficiais.iterrows():
            if not row.get("nome_chave") or not row.get("mun_chave"):
                continue
            match = locais[(locais["mun_chave"] == row["mun_chave"]) & (locais["nome_chave"] == row["nome_chave"])]
            if match.empty:
                continue
            for _, loc in match.iterrows():
                if str(loc.get("id_local")) in atualizados_ids:
                    continue
                conn.execute(
                    """
                    UPDATE estabelecimentos_saude
                    SET latitude = ?, longitude = ?, codigo_ibge = COALESCE(NULLIF(codigo_ibge,''), ?),
                        cnes = COALESCE(NULLIF(cnes,''), ?), endereco = COALESCE(NULLIF(endereco,''), ?),
                        fonte = ?, atualizado_em = ?
                    WHERE id = ?
                    """,
                    (float(row["latitude"]), float(row["longitude"]), row.get("codigo_ibge", ""), row.get("cnes", ""), row.get("endereco", ""), row.get("fonte"), agora, loc.get("id")),
                )
                atualizadas_nome += 1
                atualizados_ids.add(str(loc.get("id_local")))

        # Inserção opcional e conservadora: só se CNES oficial ainda não existir na tabela.
        cnes_locais = set(locais["cnes_chave"].dropna().astype(str))
        for _, row in oficiais.iterrows():
            cnes = str(row.get("cnes") or "").strip()
            if not cnes or cnes in cnes_locais:
                continue
            conn.execute(
                """
                INSERT INTO estabelecimentos_saude
                    (municipio, codigo_ibge, cnes, nome_unidade, tipo_unidade, endereco, latitude, longitude, fonte, importacao_id, atualizado_em)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get("municipio", ""), row.get("codigo_ibge", ""), cnes,
                    row.get("nome_unidade", ""), row.get("tipo_unidade", ""), row.get("endereco", ""),
                    float(row["latitude"]), float(row["longitude"]), row.get("fonte"), None, agora,
                ),
            )
            inseridas += 1
            cnes_locais.add(cnes)

    # Recalcula diagnóstico após atualização.
    qual = qualificar_unidades_aps_georreferenciadas()
    diag = qual.get("diagnostico", {})
    return {
        "ok": True,
        "mensagem": "Coordenadas oficiais importadas/pareadas sem uso de aproximação municipal.",
        "tentativas": busca.get("tentativas", [])[:20],
        "oficiais_lidas": int(len(oficiais)),
        "atualizadas_por_cnes": int(atualizadas_cnes),
        "atualizadas_por_nome": int(atualizadas_nome),
        "inseridas_novas": int(inseridas),
        "diagnostico_pos": diag,
        "atualizado_em": agora,
    }


# ============================================================================
# Georreferenciamento visual premium — v2
# ============================================================================




def importar_coordenadas_ubs_sistema_antigo(caminho_csv: str | Path | None = None) -> dict:
    """Recupera coordenadas reais das UBS a partir da base georreferenciada da versão antiga.

    Esta rotina NÃO cria coordenadas aproximadas e NÃO usa centroide municipal.
    Ela apenas reaproveita uma base tabular já existente no sistema antigo, com CNES,
    latitude e longitude originalmente identificadas como Dados Abertos SUS/CNES.

    Regras de segurança:
    - só aceita registros com CNES;
    - só aceita código IBGE de MT ou município presente na referência MT;
    - só aceita latitude/longitude dentro do retângulo conservador de Mato Grosso;
    - atualiza por CNES; inserção de nova unidade só ocorre quando o CNES não existe.
    """
    caminho = Path(caminho_csv or UBS_COORDENADAS_SISTEMA_ANTIGO)
    if not caminho.exists():
        return {
            "ok": False,
            "mensagem": f"Arquivo de coordenadas recuperadas não encontrado: {caminho}",
            "arquivo": str(caminho),
            "linhas_lidas": 0,
            "linhas_validas": 0,
            "atualizadas_por_cnes": 0,
            "inseridas_novas": 0,
            "ignoradas": 0,
            "diagnostico_pos": qualificar_unidades_aps_georreferenciadas().get("diagnostico", {}),
            "amostra_validas": pd.DataFrame(),
            "ignoradas_df": pd.DataFrame(),
        }

    try:
        base = pd.read_csv(caminho, dtype=str)
    except Exception as exc:
        return {
            "ok": False,
            "mensagem": f"Não foi possível ler o arquivo recuperado da versão antiga: {exc}",
            "arquivo": str(caminho),
            "linhas_lidas": 0,
            "linhas_validas": 0,
            "atualizadas_por_cnes": 0,
            "inseridas_novas": 0,
            "ignoradas": 0,
            "diagnostico_pos": qualificar_unidades_aps_georreferenciadas().get("diagnostico", {}),
            "amostra_validas": pd.DataFrame(),
            "ignoradas_df": pd.DataFrame(),
        }

    if base.empty:
        return {
            "ok": False,
            "mensagem": "O arquivo recuperado da versão antiga está vazio.",
            "arquivo": str(caminho),
            "linhas_lidas": 0,
            "linhas_validas": 0,
            "atualizadas_por_cnes": 0,
            "inseridas_novas": 0,
            "ignoradas": 0,
            "diagnostico_pos": qualificar_unidades_aps_georreferenciadas().get("diagnostico", {}),
            "amostra_validas": pd.DataFrame(),
            "ignoradas_df": pd.DataFrame(),
        }

    df = base.copy()
    # Normalização defensiva de nomes de colunas.
    df.columns = [str(c).strip().lower() for c in df.columns]
    for col in ["cnes", "codigo_ibge", "municipio", "nome_unidade", "tipo_unidade", "bairro", "logradouro", "latitude", "longitude", "fonte"]:
        if col not in df.columns:
            df[col] = ""

    df["cnes"] = df["cnes"].astype(str).str.extract(r"(\d+)")[0].fillna("").str.strip()
    df["codigo_ibge"] = df["codigo_ibge"].astype(str).str.extract(r"(\d{7})")[0].fillna("").str.strip()
    df["municipio"] = df["municipio"].astype(str).str.strip()
    df["nome_unidade"] = df["nome_unidade"].astype(str).str.strip()
    df["tipo_unidade"] = df["tipo_unidade"].astype(str).str.strip()
    df["endereco_recuperado"] = (
        df.get("logradouro", "").astype(str).str.strip()
        + " - "
        + df.get("bairro", "").astype(str).str.strip()
    ).str.strip(" -")

    df["latitude_num"] = pd.to_numeric(df["latitude"].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    df["longitude_num"] = pd.to_numeric(df["longitude"].astype(str).str.replace(",", ".", regex=False), errors="coerce")

    ref = _municipios_referencia()
    chaves_ref = set(ref["municipio"].astype(str).map(_chave).tolist()) if not ref.empty and "municipio" in ref.columns else set()
    codigos_ref = set(ref["codigo_ibge"].astype(str).str.extract(r"(\d{7})")[0].dropna().tolist()) if not ref.empty and "codigo_ibge" in ref.columns else set()

    df["coord_mt_valida"] = df.apply(lambda r: _coord_mt_valida(r.get("latitude_num"), r.get("longitude_num")), axis=1)
    df["municipio_mt_valido"] = df["municipio"].map(_chave).isin(chaves_ref)
    df["codigo_mt_valido"] = df["codigo_ibge"].isin(codigos_ref) | df["codigo_ibge"].astype(str).str.startswith("51")
    df["registro_valido"] = (
        df["cnes"].astype(str).str.len().gt(0)
        & df["coord_mt_valida"]
        & (df["codigo_mt_valido"] | df["municipio_mt_valido"])
    )

    validas = df[df["registro_valido"]].copy()
    ignoradas = df[~df["registro_valido"]].copy()

    if validas.empty:
        return {
            "ok": False,
            "mensagem": "A base recuperada foi lida, mas nenhum registro passou nas regras de CNES + município/IBGE MT + coordenada válida.",
            "arquivo": str(caminho),
            "linhas_lidas": int(len(df)),
            "linhas_validas": 0,
            "atualizadas_por_cnes": 0,
            "inseridas_novas": 0,
            "ignoradas": int(len(ignoradas)),
            "diagnostico_pos": qualificar_unidades_aps_georreferenciadas().get("diagnostico", {}),
            "amostra_validas": pd.DataFrame(),
            "ignoradas_df": ignoradas.head(200),
        }

    agora = datetime.now().isoformat(timespec="seconds")
    fonte_recuperada = "Sistema antigo IGD APS / Dados Abertos SUS - CNES Estabelecimentos"

    atualizadas = 0
    inseridas = 0
    with db_session() as conn:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='estabelecimentos_saude'")
        if not cur.fetchone():
            return {
                "ok": False,
                "mensagem": "A tabela estabelecimentos_saude não existe no banco atual.",
                "arquivo": str(caminho),
                "linhas_lidas": int(len(df)),
                "linhas_validas": int(len(validas)),
                "atualizadas_por_cnes": 0,
                "inseridas_novas": 0,
                "ignoradas": int(len(ignoradas)),
                "diagnostico_pos": qualificar_unidades_aps_georreferenciadas().get("diagnostico", {}),
                "amostra_validas": validas.head(200),
                "ignoradas_df": ignoradas.head(200),
            }

        for _, r in validas.iterrows():
            cnes = str(r.get("cnes") or "").strip()
            municipio = str(r.get("municipio") or "").strip()
            codigo_ibge = str(r.get("codigo_ibge") or "").strip()
            nome = str(r.get("nome_unidade") or "").strip()
            tipo = str(r.get("tipo_unidade") or "").strip()
            endereco = str(r.get("endereco_recuperado") or "").strip()
            lat = float(r.get("latitude_num"))
            lon = float(r.get("longitude_num"))

            cur.execute("SELECT id FROM estabelecimentos_saude WHERE TRIM(CAST(cnes AS TEXT)) = ? LIMIT 1", (cnes,))
            achado = cur.fetchone()
            if achado:
                cur.execute(
                    """
                    UPDATE estabelecimentos_saude
                       SET latitude = ?,
                           longitude = ?,
                           fonte = COALESCE(NULLIF(fonte, ''), ?),
                           atualizado_em = ?
                     WHERE id = ?
                    """,
                    (lat, lon, fonte_recuperada, agora, achado[0]),
                )
                atualizadas += 1
            else:
                cur.execute(
                    """
                    INSERT INTO estabelecimentos_saude
                        (municipio, codigo_ibge, cnes, nome_unidade, tipo_unidade, endereco, latitude, longitude, fonte, importacao_id, atualizado_em)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (municipio, codigo_ibge, cnes, nome, tipo, endereco, lat, lon, fonte_recuperada, agora),
                )
                inseridas += 1

        conn.commit()

    diag_pos = qualificar_unidades_aps_georreferenciadas().get("diagnostico", {})
    msg = (
        f"Coordenadas recuperadas da versão antiga aplicadas com segurança: "
        f"{atualizadas} atualizadas por CNES e {inseridas} novas inseridas. "
        f"Foram usadas apenas coordenadas válidas para Mato Grosso."
    )
    return {
        "ok": True,
        "mensagem": msg,
        "arquivo": str(caminho),
        "linhas_lidas": int(len(df)),
        "linhas_validas": int(len(validas)),
        "atualizadas_por_cnes": int(atualizadas),
        "inseridas_novas": int(inseridas),
        "ignoradas": int(len(ignoradas)),
        "diagnostico_pos": diag_pos,
        "amostra_validas": validas[[
            c for c in ["municipio", "codigo_ibge", "cnes", "nome_unidade", "tipo_unidade", "latitude_num", "longitude_num", "fonte"]
            if c in validas.columns
        ]].head(300),
        "ignoradas_df": ignoradas[[
            c for c in ["municipio", "codigo_ibge", "cnes", "nome_unidade", "latitude", "longitude", "coord_mt_valida", "municipio_mt_valido", "codigo_mt_valido"]
            if c in ignoradas.columns
        ]].head(300),
    }


def _safe_json_loads(valor: Any) -> Any:
    if valor is None:
        return None
    if isinstance(valor, (dict, list)):
        return valor
    texto = str(valor).strip()
    if not texto or texto.lower() in {"none", "nan", "null"}:
        return None
    try:
        return json.loads(texto)
    except Exception:
        return None


def _cor_classe_geo(classe: Any) -> list[int]:
    texto = str(classe or "").strip().lower()
    if "muito" in texto:
        return [165, 42, 42, 145]
    if texto == "alta":
        return [218, 124, 48, 135]
    if texto in {"média", "media"}:
        return [235, 188, 70, 125]
    return [45, 125, 170, 105]


def obter_geojson_municipal_filtrado(base_mapa: pd.DataFrame | None = None) -> dict:
    """Monta FeatureCollection municipal para PyDeck/GeoJsonLayer.

    Usa a tabela malhas_geograficas_municipais quando ela estiver disponível.
    Caso a geometria não exista, retorna FeatureCollection vazia. O mapa ainda pode
    funcionar com pontos/centroides.
    """
    malhas = _read_table_safe("malhas_geograficas_municipais")
    if malhas.empty:
        return {"type": "FeatureCollection", "features": []}

    geom_col = _pick_col(malhas, GEOM_CANDIDATES)
    mun_col = _pick_col(malhas, MUNICIPIO_CANDIDATES) or "municipio"
    cod_col = _pick_col(malhas, CODIGO_CANDIDATES)
    if not geom_col or mun_col not in malhas.columns:
        return {"type": "FeatureCollection", "features": []}

    props = pd.DataFrame()
    if base_mapa is not None and not base_mapa.empty:
        props = base_mapa.copy()
        props["municipio_chave"] = props["municipio"].map(_chave)
        props = props.drop_duplicates("municipio_chave")
        props = props.set_index("municipio_chave")

    features = []
    for _, row in malhas.iterrows():
        municipio = str(row.get(mun_col) or "").strip()
        if not municipio:
            continue
        geom = _safe_json_loads(row.get(geom_col))
        if not isinstance(geom, dict) or geom.get("type") not in {"Polygon", "MultiPolygon", "GeometryCollection"}:
            continue
        chave = _chave(municipio)
        info = props.loc[chave].to_dict() if not props.empty and chave in props.index else {}
        classe = info.get("classe_geo_preliminar", "Monitoramento")
        fill = _cor_classe_geo(classe)
        prop = {
            "municipio": municipio,
            "codigo_ibge": _codigo_limpo(row.get(cod_col)) if cod_col else info.get("codigo_ibge", ""),
            "regiao_saude": info.get("regiao_saude", "Não informada"),
            "indice_geo_preliminar": float(info.get("indice_geo_preliminar", 0) or 0),
            "classe_geo_preliminar": classe,
            "populacao": float(info.get("populacao", 0) or 0),
            "total_equipes_aps": float(info.get("total_equipes_aps", 0) or 0),
            "total_ubs": float(info.get("total_ubs", 0) or 0),
            "qtd_assentamentos": float(info.get("qtd_assentamentos", 0) or 0),
            "qtd_terras_indigenas_intersecoes": float(info.get("qtd_terras_indigenas_intersecoes", 0) or 0),
            "qtd_ocorrencias_ambientais": float(info.get("qtd_ocorrencias_ambientais", 0) or 0),
            "fill_color": fill,
            "line_color": [255, 255, 255, 120],
        }
        features.append({"type": "Feature", "geometry": geom, "properties": prop})
    return {"type": "FeatureCollection", "features": features}


def montar_pontos_multicamadas(camadas: list[str] | None = None, limite_por_camada: int = 2500) -> pd.DataFrame:
    """Une pontos válidos das camadas selecionadas para mapa multicamadas."""
    mapa = {
        "Estabelecimentos de saúde": "estabelecimentos_saude",
        "Assentamentos": "dados_mt_assentamentos",
        "Terras Indígenas": "dados_mt_terras_indigenas",
        "Ocorrências ambientais": "dados_mt_areas_contaminadas",
    }
    camadas = camadas or list(mapa.keys())
    frames = []
    for nome in camadas:
        tabela = mapa.get(nome, nome)
        df = obter_pontos_camada(tabela, limite=limite_por_camada)
        if df.empty:
            continue
        df = df.copy()
        df["camada_nome"] = nome
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True, sort=False)
    return out


def resumo_municipio_geografico(municipio: str) -> dict:
    base = montar_base_mapa_municipal()
    if base.empty or not municipio:
        return {}
    sel = base[base["municipio"].astype(str).str.lower() == str(municipio).lower()]
    if sel.empty:
        return {}
    row = sel.iloc[0].to_dict()
    alertas = []
    try:
        if float(row.get("pop_por_equipe") or 0) >= 3500:
            alertas.append("pressão populacional por equipe APS")
    except Exception:
        pass
    try:
        if float(row.get("pop_por_ubs") or 0) >= 12000:
            alertas.append("pressão populacional por UBS/estabelecimento APS")
    except Exception:
        pass
    if int(row.get("qtd_assentamentos") or 0) > 0:
        alertas.append("presença de assentamentos")
    if int(row.get("qtd_terras_indigenas_intersecoes") or 0) > 0:
        alertas.append("presença/interseção com terras indígenas")
    if int(row.get("qtd_ocorrencias_ambientais") or 0) > 0:
        alertas.append("ocorrências ambientais registradas")
    row["alertas_geograficos"] = "; ".join(alertas) if alertas else "sem alerta territorial crítico na régua preliminar"
    return row



def identificar_vazios_assistenciais(base_mapa: pd.DataFrame | None = None) -> pd.DataFrame:
    """Classifica municípios para leitura de vazios assistenciais territoriais.

    A função não cria regra normativa. Ela transforma a base geográfica em uma
    régua de investigação, destacando municípios com pressão por equipes, UBS,
    dispersão territorial e presença de camadas especiais.
    """
    df = montar_base_mapa_municipal() if base_mapa is None else base_mapa.copy()
    if df.empty:
        return pd.DataFrame()

    for col in [
        "populacao", "total_equipes_aps", "total_ubs", "area_km2", "densidade_hab_km2",
        "pop_por_equipe", "pop_por_ubs", "qtd_assentamentos", "qtd_terras_indigenas_intersecoes",
        "qtd_ocorrencias_ambientais", "latitude", "longitude", "indice_geo_preliminar",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = 0

    def q(col: str, quantil: float, default: float = 0.0) -> float:
        s = pd.to_numeric(df.get(col), errors="coerce").replace([float("inf"), float("-inf")], pd.NA).dropna()
        s = s[s > 0]
        if s.empty:
            return default
        try:
            return float(s.quantile(quantil))
        except Exception:
            return default

    lim_pop_eq_alta = q("pop_por_equipe", 0.75, 3500)
    lim_pop_ubs_alta = q("pop_por_ubs", 0.75, 10000)
    lim_area_extensa = q("area_km2", 0.75, 5000)
    lim_indice = q("indice_geo_preliminar", 0.70, 55)

    df["flag_sem_equipe"] = df["total_equipes_aps"].fillna(0) <= 0
    df["flag_sem_ubs"] = df["total_ubs"].fillna(0) <= 0
    df["flag_pressao_equipes"] = df["pop_por_equipe"].fillna(0) >= lim_pop_eq_alta
    df["flag_pressao_ubs"] = df["pop_por_ubs"].fillna(0) >= lim_pop_ubs_alta
    df["flag_area_extensa"] = df["area_km2"].fillna(0) >= lim_area_extensa
    df["flag_territorios_especiais"] = (
        df["qtd_assentamentos"].fillna(0) + df["qtd_terras_indigenas_intersecoes"].fillna(0)
    ) > 0
    df["flag_risco_ambiental"] = df["qtd_ocorrencias_ambientais"].fillna(0) > 0
    df["flag_indice_alto"] = df["indice_geo_preliminar"].fillna(0) >= lim_indice

    pesos = {
        "flag_sem_equipe": 25,
        "flag_pressao_equipes": 22,
        "flag_sem_ubs": 18,
        "flag_pressao_ubs": 15,
        "flag_area_extensa": 12,
        "flag_territorios_especiais": 16,
        "flag_risco_ambiental": 8,
        "flag_indice_alto": 10,
    }
    score = pd.Series(0.0, index=df.index)
    for flag, peso in pesos.items():
        score = score + df[flag].astype(bool).astype(float) * peso
    df["score_vazio_assistencial"] = score.clip(0, 100).round(2)

    def nivel(score: float) -> str:
        if score >= 65:
            return "Crítico"
        if score >= 45:
            return "Alto"
        if score >= 25:
            return "Médio"
        return "Monitoramento"

    def motivo(row: pd.Series) -> str:
        motivos = []
        if bool(row.get("flag_sem_equipe")):
            motivos.append("sem equipe APS identificada")
        elif bool(row.get("flag_pressao_equipes")):
            motivos.append("alta população por equipe APS")
        if bool(row.get("flag_sem_ubs")):
            motivos.append("sem UBS/estabelecimento APS identificado")
        elif bool(row.get("flag_pressao_ubs")):
            motivos.append("alta população por UBS")
        if bool(row.get("flag_area_extensa")):
            motivos.append("território extenso/disperso")
        if bool(row.get("flag_territorios_especiais")):
            motivos.append("assentamentos e/ou terras indígenas")
        if bool(row.get("flag_risco_ambiental")):
            motivos.append("ocorrência ambiental registrada")
        return "; ".join(motivos) if motivos else "sem alerta territorial forte pelos critérios atuais"

    def tipo(row: pd.Series) -> str:
        if bool(row.get("flag_sem_equipe")) or bool(row.get("flag_pressao_equipes")):
            return "Déficit/pressão de equipes APS"
        if bool(row.get("flag_sem_ubs")) or bool(row.get("flag_pressao_ubs")):
            return "Déficit/pressão de UBS"
        if bool(row.get("flag_territorios_especiais")):
            return "Território especial/equidade"
        if bool(row.get("flag_area_extensa")):
            return "Acesso territorial/logística"
        if bool(row.get("flag_risco_ambiental")):
            return "Vigilância ambiental integrada"
        return "Monitoramento territorial"

    df["nivel_vazio_assistencial"] = df["score_vazio_assistencial"].map(nivel)
    df["tipo_vazio_predominante"] = df.apply(tipo, axis=1)
    df["motivos_vazio"] = df.apply(motivo, axis=1)

    cols = [c for c in [
        "municipio", "codigo_ibge", "regiao_saude", "latitude", "longitude",
        "score_vazio_assistencial", "nivel_vazio_assistencial", "tipo_vazio_predominante", "motivos_vazio",
        "populacao", "total_equipes_aps", "total_ubs", "pop_por_equipe", "pop_por_ubs",
        "area_km2", "densidade_hab_km2", "qtd_assentamentos", "qtd_terras_indigenas_intersecoes",
        "qtd_ocorrencias_ambientais", "indice_geo_preliminar", "classe_geo_preliminar",
        "flag_sem_equipe", "flag_sem_ubs", "flag_pressao_equipes", "flag_pressao_ubs", "flag_area_extensa",
        "flag_territorios_especiais", "flag_risco_ambiental",
    ] if c in df.columns]
    return df[cols].sort_values("score_vazio_assistencial", ascending=False).reset_index(drop=True)


def resumo_vazios_por_regiao(vazios: pd.DataFrame | None = None) -> pd.DataFrame:
    df = identificar_vazios_assistenciais() if vazios is None else vazios.copy()
    if df.empty or "regiao_saude" not in df.columns:
        return pd.DataFrame()
    df["eh_critico_alto"] = df["nivel_vazio_assistencial"].isin(["Crítico", "Alto"])
    resumo = df.groupby("regiao_saude", dropna=False).agg(
        municipios=("municipio", "count"),
        critico_alto=("eh_critico_alto", "sum"),
        score_medio=("score_vazio_assistencial", "mean"),
        pop_total=("populacao", "sum"),
        equipes_total=("total_equipes_aps", "sum"),
        ubs_total=("total_ubs", "sum"),
        territorios_especiais=("flag_territorios_especiais", "sum"),
        risco_ambiental=("flag_risco_ambiental", "sum"),
    ).reset_index()
    resumo["% critico_alto"] = (resumo["critico_alto"] / resumo["municipios"].replace(0, pd.NA) * 100).round(1)
    resumo["score_medio"] = resumo["score_medio"].round(2)
    return resumo.sort_values(["critico_alto", "score_medio"], ascending=False).reset_index(drop=True)


# ============================================================================
# Painel de pendências geográficas — v6
# ============================================================================

def diagnosticar_pendencias_geograficas() -> dict:
    """Organiza uma fila técnica de pendências para qualificação geográfica.

    A função não altera dados e não cria coordenadas aproximadas. Ela consolida
    problemas que impedem análises exatas de acesso, distância e cobertura
    territorial, especialmente UBS sem latitude/longitude oficial.
    """
    gerado_em = datetime.now().isoformat(timespec="seconds")
    diag_camadas = diagnosticar_camadas_geograficas()
    resumo_camadas = diag_camadas.get("resumo", pd.DataFrame())
    qual_ubs = qualificar_unidades_aps_georreferenciadas()
    ubs_sem = qual_ubs.get("sem_coordenadas", pd.DataFrame())
    ubs_resumo = qual_ubs.get("resumo_municipal", pd.DataFrame())
    diag_ubs = qual_ubs.get("diagnostico", {})
    base_mapa = montar_base_mapa_municipal()

    linhas: list[dict[str, Any]] = []

    def add_pendencia(eixo: str, pendencia: str, quantidade: int, prioridade: str, impacto: str, acao: str, tabela: str = "", criterio: str = ""):
        if int(quantidade or 0) <= 0:
            return
        linhas.append({
            "prioridade": prioridade,
            "eixo": eixo,
            "pendencia": pendencia,
            "quantidade": int(quantidade or 0),
            "tabela_relacionada": tabela,
            "criterio": criterio,
            "impacto_analitico": impacto,
            "encaminhamento_recomendado": acao,
        })

    # 1) UBS sem latitude/longitude oficial válida.
    add_pendencia(
        eixo="UBS e estabelecimentos APS",
        pendencia="Unidades/UBS sem latitude e longitude oficial válida",
        quantidade=int(diag_ubs.get("sem_coordenadas_validas", 0) or 0),
        prioridade="Crítica",
        tabela="estabelecimentos_saude",
        criterio="latitude/longitude ausente ou fora da faixa geográfica esperada para Mato Grosso",
        impacto="Impede cálculo real de distância entre assentamentos, territórios especiais e UBS; limita análise de acesso territorial.",
        acao="Obter ou validar coordenadas oficiais das unidades, preferencialmente por base institucional ou fonte pública oficial; não usar centroide municipal como substituto.",
    )

    # 2) Estabelecimentos sem município/código.
    est = _read_table_safe("estabelecimentos_saude")
    if not est.empty:
        mun_col = _pick_col(est, MUNICIPIO_CANDIDATES)
        cod_col = _pick_col(est, CODIGO_CANDIDATES)
        sem_mun = int(est[mun_col].isna().sum() + est[mun_col].astype(str).str.strip().isin(["", "None", "nan"]).sum()) if mun_col else len(est)
        sem_cod = int(est[cod_col].isna().sum() + est[cod_col].astype(str).str.strip().isin(["", "None", "nan"]).sum()) if cod_col else len(est)
        add_pendencia(
            eixo="UBS e estabelecimentos APS",
            pendencia="Unidades sem município identificado",
            quantidade=sem_mun,
            prioridade="Alta",
            tabela="estabelecimentos_saude",
            criterio="coluna de município ausente/vazia",
            impacto="Reduz confiabilidade de filtros regionais e consolidação municipal de oferta APS.",
            acao="Padronizar município pelo código IBGE, CNES ou endereço da unidade antes de usar a camada em análises municipais.",
        )
        add_pendencia(
            eixo="UBS e estabelecimentos APS",
            pendencia="Unidades sem código IBGE municipal",
            quantidade=sem_cod,
            prioridade="Média",
            tabela="estabelecimentos_saude",
            criterio="código IBGE ausente/vazio",
            impacto="Dificulta cruzamentos robustos entre CNES, base consolidada, malhas e regionalização.",
            acao="Preencher código IBGE a partir do município padronizado e da tabela mestre de municípios.",
        )

    # 3) Municípios sem coordenadas/malha para mapa municipal.
    if not base_mapa.empty:
        lat = pd.to_numeric(base_mapa.get("latitude"), errors="coerce")
        lon = pd.to_numeric(base_mapa.get("longitude"), errors="coerce")
        sem_coord_mun = int((~_valid_lat_lon(lat, lon)).sum()) if len(base_mapa) else 0
        add_pendencia(
            eixo="Municípios e malhas",
            pendencia="Municípios sem coordenada municipal válida",
            quantidade=sem_coord_mun,
            prioridade="Alta",
            tabela="municipios / malhas_geograficas_municipais",
            criterio="latitude/longitude municipal ausente ou inválida",
            impacto="Limita visualização no mapa, foco territorial e cálculo de centroides municipais.",
            acao="Revisar malha municipal carregada e validar municípios novos que ainda não possuem geometria individualizada.",
        )

    # 4) Camadas com baixa prontidão para mapa.
    if not resumo_camadas.empty:
        camadas_problema = resumo_camadas[
            (resumo_camadas["existe"] == "Sim")
            & (resumo_camadas["registros"] > 0)
            & (~resumo_camadas["qualidade"].isin(["Boa", "Municipalizada"]))
        ].copy()
        add_pendencia(
            eixo="Camadas geográficas",
            pendencia="Camadas existentes com qualidade geográfica parcial ou limitada",
            quantidade=len(camadas_problema),
            prioridade="Média",
            tabela="múltiplas camadas",
            criterio="qualidade diferente de Boa/Municipalizada no diagnóstico de camadas",
            impacto="Pode gerar mapa incompleto, agregação parcial ou leitura territorial frágil.",
            acao="Avaliar camada a camada: priorizar as que impactam distância, acesso e vazios assistenciais; manter bases fracas apenas como referência bruta.",
        )

    # 5) Assentamentos sem vínculo municipal/coordenada.
    assent = _read_table_safe("dados_mt_assentamentos")
    if not assent.empty:
        lat_col = _pick_col(assent, LAT_CANDIDATES)
        lon_col = _pick_col(assent, LON_CANDIDATES)
        if lat_col and lon_col:
            sem_coord_assent = int((~_valid_lat_lon(assent[lat_col], assent[lon_col])).sum())
        else:
            sem_coord_assent = len(assent)
        mun_col = _pick_col(assent, MUNICIPIO_CANDIDATES)
        sem_mun_assent = int(assent[mun_col].isna().sum() + assent[mun_col].astype(str).str.strip().isin(["", "None", "nan"]).sum()) if mun_col else len(assent)
        add_pendencia(
            eixo="Assentamentos",
            pendencia="Assentamentos sem coordenada válida",
            quantidade=sem_coord_assent,
            prioridade="Alta",
            tabela="dados_mt_assentamentos",
            criterio="centroide/latitude/longitude ausente ou inválido",
            impacto="Impede cálculo exato de distância assentamento → UBS quando a camada de UBS estiver qualificada.",
            acao="Recarregar base estadual ajustada ou validar geometria/centroide dos assentamentos.",
        )
        add_pendencia(
            eixo="Assentamentos",
            pendencia="Assentamentos sem município vinculado",
            quantidade=sem_mun_assent,
            prioridade="Alta",
            tabela="dados_mt_assentamentos",
            criterio="município ausente/vazio",
            impacto="Impede resumo municipal e regional de territórios rurais especiais.",
            acao="Inferir município por interseção/centroide com a malha municipal e revisar manualmente casos ambíguos.",
        )

    # 6) Terras indígenas sem municípios intersectados/coordenadas.
    terras = _read_table_safe("dados_mt_terras_indigenas")
    if not terras.empty:
        lat_col = _pick_col(terras, LAT_CANDIDATES)
        lon_col = _pick_col(terras, LON_CANDIDATES)
        sem_coord_terras = int((~_valid_lat_lon(terras[lat_col], terras[lon_col])).sum()) if lat_col and lon_col else len(terras)
        if "municipios_intersectados" in terras.columns:
            sem_intersecao = int(terras["municipios_intersectados"].isna().sum() + terras["municipios_intersectados"].astype(str).str.strip().isin(["", "None", "nan"]).sum())
        else:
            sem_intersecao = len(terras)
        add_pendencia(
            eixo="Terras indígenas",
            pendencia="Terras indígenas sem centroide/coordenada válida",
            quantidade=sem_coord_terras,
            prioridade="Média",
            tabela="dados_mt_terras_indigenas",
            criterio="latitude/longitude ausente ou inválida",
            impacto="Limita mapa de pontos e futura análise de proximidade com UBS.",
            acao="Validar leitura de KML/KMZ e centroide da geometria.",
        )
        add_pendencia(
            eixo="Terras indígenas",
            pendencia="Terras indígenas sem municípios intersectados",
            quantidade=sem_intersecao,
            prioridade="Alta",
            tabela="dados_mt_terras_indigenas",
            criterio="campo municipios_intersectados ausente/vazio",
            impacto="Prejudica a leitura municipal de equidade territorial.",
            acao="Reprocessar interseção com malha municipal e revisar nomes de municípios.",
        )

    # 7) Ocorrências ambientais sem município/código/coordenada.
    areas = _read_table_safe("dados_mt_areas_contaminadas")
    if not areas.empty:
        lat_col = _pick_col(areas, LAT_CANDIDATES)
        lon_col = _pick_col(areas, LON_CANDIDATES)
        sem_coord_areas = int((~_valid_lat_lon(areas[lat_col], areas[lon_col])).sum()) if lat_col and lon_col else len(areas)
        mun_col = _pick_col(areas, MUNICIPIO_CANDIDATES)
        cod_col = _pick_col(areas, CODIGO_CANDIDATES)
        sem_mun_areas = int(areas[mun_col].isna().sum() + areas[mun_col].astype(str).str.strip().isin(["", "None", "nan", "NÃO INFORMADO", "NAO INFORMADO"]).sum()) if mun_col else len(areas)
        sem_cod_areas = int(areas[cod_col].isna().sum() + areas[cod_col].astype(str).str.strip().isin(["", "None", "nan"]).sum()) if cod_col else len(areas)
        add_pendencia(
            eixo="Ocorrências ambientais",
            pendencia="Ocorrências ambientais sem coordenada válida",
            quantidade=sem_coord_areas,
            prioridade="Média",
            tabela="dados_mt_areas_contaminadas",
            criterio="latitude/longitude ausente ou inválida",
            impacto="Limita leitura pontual de risco ambiental no mapa.",
            acao="Usar município como agregação quando coordenada não existir; buscar coordenadas somente em fonte validada.",
        )
        add_pendencia(
            eixo="Ocorrências ambientais",
            pendencia="Ocorrências ambientais sem município informado/padronizado",
            quantidade=sem_mun_areas,
            prioridade="Alta",
            tabela="dados_mt_areas_contaminadas",
            criterio="município ausente, não informado ou inconsistente",
            impacto="Prejudica agregação municipal de riscos ambientais.",
            acao="Padronizar nomes de municípios e cruzar com tabela mestre; manter casos não informados separados.",
        )
        add_pendencia(
            eixo="Ocorrências ambientais",
            pendencia="Ocorrências ambientais sem código IBGE",
            quantidade=sem_cod_areas,
            prioridade="Média",
            tabela="dados_mt_areas_contaminadas",
            criterio="código IBGE ausente/vazio",
            impacto="Reduz robustez do cruzamento com mapas e base consolidada.",
            acao="Preencher código IBGE a partir do município padronizado quando possível.",
        )

    pend = pd.DataFrame(linhas)
    if not pend.empty:
        ordem = {"Crítica": 1, "Alta": 2, "Média": 3, "Baixa": 4}
        pend["ordem_prioridade"] = pend["prioridade"].map(ordem).fillna(9)
        pend = pend.sort_values(["ordem_prioridade", "quantidade"], ascending=[True, False]).reset_index(drop=True)

    resumo_prioridade = pd.DataFrame()
    resumo_eixo = pd.DataFrame()
    if not pend.empty:
        resumo_prioridade = pend.groupby("prioridade", dropna=False).agg(
            pendencias=("pendencia", "count"),
            registros_afetados=("quantidade", "sum"),
        ).reset_index()
        resumo_prioridade["ordem_prioridade"] = resumo_prioridade["prioridade"].map({"Crítica": 1, "Alta": 2, "Média": 3, "Baixa": 4}).fillna(9)
        resumo_prioridade = resumo_prioridade.sort_values("ordem_prioridade").drop(columns="ordem_prioridade")
        resumo_eixo = pend.groupby("eixo", dropna=False).agg(
            pendencias=("pendencia", "count"),
            maior_prioridade=("prioridade", lambda s: sorted(s, key=lambda x: {"Crítica": 1, "Alta": 2, "Média": 3, "Baixa": 4}.get(x, 9))[0]),
            registros_afetados=("quantidade", "sum"),
        ).reset_index().sort_values("registros_afetados", ascending=False)

    return {
        "pendencias": pend,
        "resumo_prioridade": resumo_prioridade,
        "resumo_eixo": resumo_eixo,
        "ubs_pendentes": ubs_sem,
        "ubs_resumo_municipal": ubs_resumo,
        "camadas": resumo_camadas,
        "diagnostico_ubs": diag_ubs,
        "gerado_em": gerado_em,
    }

# ============================================================================
# Georreferenciamento v8 — diagnóstico real do JSON da API pública de UBS
# ============================================================================

COORD_LAT_TOKENS = ["latitude", "lat", "nu_latitude", "vl_latitude", "coord_lat", "geo_lat", "y"]
COORD_LON_TOKENS = ["longitude", "lon", "lng", "long", "nu_longitude", "vl_longitude", "coord_lon", "geo_lon", "x"]
IDENT_UBS_TOKENS = ["cnes", "co_cnes", "nome", "fantasia", "estabelecimento", "municipio", "uf", "endereco", "logradouro"]


def _json_lists_of_dicts(payload: Any, path: str = "$", depth: int = 0, max_depth: int = 6) -> list[dict[str, Any]]:
    """Localiza recursivamente listas de dicionários dentro de um JSON."""
    encontrados: list[dict[str, Any]] = []
    if depth > max_depth:
        return encontrados
    if isinstance(payload, list):
        dicts = [x for x in payload if isinstance(x, dict)]
        if dicts:
            keys = sorted({str(k) for item in dicts[:30] for k in item.keys()})
            encontrados.append({
                "caminho": path,
                "registros": len(dicts),
                "qtd_campos_amostra": len(keys),
                "campos_amostra": ", ".join(keys[:80]),
                "amostra": dicts[:5],
            })
        # Ainda assim desce, pois pode haver listas de objetos aninhados.
        for idx, item in enumerate(payload[:10]):
            encontrados.extend(_json_lists_of_dicts(item, f"{path}[{idx}]", depth + 1, max_depth))
        return encontrados
    if isinstance(payload, dict):
        for key, value in payload.items():
            encontrados.extend(_json_lists_of_dicts(value, f"{path}.{key}", depth + 1, max_depth))
    return encontrados


def _flatten_dict_limited(obj: Any, prefix: str = "", depth: int = 0, max_depth: int = 4) -> dict[str, Any]:
    """Achata um registro JSON para diagnóstico, sem explodir objetos grandes."""
    out: dict[str, Any] = {}
    if depth > max_depth:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                out.update(_flatten_dict_limited(v, key, depth + 1, max_depth))
            elif isinstance(v, list):
                out[key] = f"[lista com {len(v)} item(ns)]"
            else:
                out[key] = v
    return out


def _campos_coord_possiveis(flat: dict[str, Any]) -> tuple[list[str], list[str]]:
    lat_campos = []
    lon_campos = []
    for campo in flat.keys():
        c = _chave(campo).lower().replace(".", "_")
        if any(tok.lower() in c for tok in COORD_LAT_TOKENS):
            lat_campos.append(campo)
        if any(tok.lower() in c for tok in COORD_LON_TOKENS):
            lon_campos.append(campo)
    return lat_campos, lon_campos


def _campos_relevantes_ubs(flat: dict[str, Any]) -> list[str]:
    campos = []
    for campo in flat.keys():
        c = _chave(campo).lower().replace(".", "_")
        if any(tok.lower() in c for tok in IDENT_UBS_TOKENS + COORD_LAT_TOKENS + COORD_LON_TOKENS):
            campos.append(campo)
    return campos


def _coord_mt_bbox_estrita(lat: Any, lon: Any) -> bool:
    """Valida coordenada no retângulo aproximado de Mato Grosso.

    Usada apenas para diagnóstico de recuperação de coordenadas. Mantém faixa
    mais restrita que a validação ampla, para reduzir falso positivo quando a
    API retorna códigos numéricos confundidos com coordenadas.
    """
    try:
        latf = float(lat)
        lonf = float(lon)
    except Exception:
        return False
    return -19.8 <= latf <= -7.0 and -62.5 <= lonf <= -50.0


def _pares_coord_candidatos(flat: dict[str, Any]) -> list[tuple[str, str]]:
    """Monta pares candidatos de latitude/longitude para diagnóstico.

    Diferente da normalização conservadora, aqui testamos todos os pares
    plausíveis encontrados no JSON, incluindo x/y, lat/lon e campos aninhados.
    A função não grava nada no banco.
    """
    lat_campos, lon_campos = _campos_coord_possiveis(flat)
    pares: list[tuple[str, str]] = []
    for lc in lat_campos:
        for gc in lon_campos:
            if lc != gc:
                pares.append((lc, gc))
    # Reforço para padrão cartesiano comum em bases geográficas.
    campos_lower = {str(k).lower(): k for k in flat.keys()}
    for y_key in ["y", "coord_y", "coordenada_y", "latitude_y"]:
        for x_key in ["x", "coord_x", "coordenada_x", "longitude_x"]:
            y_real = campos_lower.get(y_key)
            x_real = campos_lower.get(x_key)
            if y_real and x_real and (y_real, x_real) not in pares:
                pares.append((y_real, x_real))
    # Remove duplicatas preservando ordem.
    vistos = set()
    out = []
    for par in pares:
        if par in vistos:
            continue
        vistos.add(par)
        out.append(par)
    return out


def _variantes_coord_recuperaveis(lat_raw: Any, lon_raw: Any) -> list[dict[str, Any]]:
    """Testa interpretações seguras para coordenadas retornadas pela API.

    Não usa endereço, não usa centroide e não cria coordenada por município.
    Apenas tenta corrigir representação do próprio valor retornado: sinal,
    inversão latitude/longitude e escala decimal.
    """
    lat0 = _normalizar_numero_coord(lat_raw)
    lon0 = _normalizar_numero_coord(lon_raw)
    if lat0 is None or lon0 is None:
        return []

    bases = [
        (lat0, lon0, "original"),
        (lon0, lat0, "lat/lon invertidos"),
        (-abs(lat0), -abs(lon0), "sinal negativo aplicado"),
        (-abs(lon0), -abs(lat0), "invertido + sinal negativo aplicado"),
    ]

    # Escalas comuns quando o decimal some: -151602 -> -15.1602; 15160222 -> -15.160222.
    escalas = [10, 100, 1000, 10000, 100000, 1000000, 10000000, 100000000]
    for escala in escalas:
        bases.extend([
            (lat0 / escala, lon0 / escala, f"escala /{escala}"),
            (lon0 / escala, lat0 / escala, f"invertido + escala /{escala}"),
            (-abs(lat0 / escala), -abs(lon0 / escala), f"sinal negativo + escala /{escala}"),
            (-abs(lon0 / escala), -abs(lat0 / escala), f"invertido + sinal negativo + escala /{escala}"),
        ])

    out = []
    vistos = set()
    for lat, lon, regra in bases:
        if not _coord_mt_bbox_estrita(lat, lon):
            continue
        chave = (round(float(lat), 7), round(float(lon), 7))
        if chave in vistos:
            continue
        vistos.add(chave)
        out.append({
            "latitude_interpretada": round(float(lat), 7),
            "longitude_interpretada": round(float(lon), 7),
            "regra_interpretacao": regra,
        })
    return out


def _identidade_ubs_api(row: dict[str, Any]) -> dict[str, Any]:
    """Extrai identificadores da UBS no JSON para exibir no diagnóstico."""
    return {
        "cnes": str(_get_nested(row, ["cnes", "co_cnes", "codigo_cnes", "cod_cnes", "codigoCNES"]) or "").strip(),
        "nome_unidade": str(_get_nested(row, ["nome_unidade", "no_fantasia", "nome_fantasia", "no_estabelecimento", "nome", "estabelecimento"]) or "").strip(),
        "municipio": str(_get_nested(row, ["municipio", "no_municipio", "nome_municipio", "cidade"]) or "").strip(),
        "codigo_ibge": _codigo_limpo(_get_nested(row, ["codigo_ibge", "co_municipio_ibge", "cod_ibge", "codigo_municipio", "co_municipio"])),
        "tipo_unidade": str(_get_nested(row, ["tipo_unidade", "tipo_estabelecimento", "ds_tipo_unidade", "natureza"]) or "").strip(),
        "endereco": str(_get_nested(row, ["endereco", "logradouro", "ds_logradouro", "endereço"]) or "").strip(),
    }


def diagnosticar_json_api_ubs_ms(max_registros_por_lista: int = 30) -> dict[str, Any]:
    """Inspeciona a resposta JSON real da API pública de UBS do MS.

    Esta rotina não altera o banco. Ela existe para descobrir se a API traz os
    campos necessários para georreferenciar UBS, ainda que com nomes diferentes
    ou dentro de objetos aninhados. Não cria coordenadas aproximadas.
    """
    headers = {"Accept": "application/json", "User-Agent": "plataforma-aps-inteligencia/1.0"}
    param_sets = [
        {"uf": "MT"},
        {"sigla_uf": "MT"},
        {"estado": "MT"},
        {"co_uf": "51"},
        {},
    ]
    paginadores = [
        {},
        {"page": 1, "per_page": 500},
        {"pagina": 1, "tamanho": 500},
        {"offset": 0, "limit": 500},
    ]

    resumo_endpoints: list[dict[str, Any]] = []
    listas_detectadas: list[dict[str, Any]] = []
    amostras: list[dict[str, Any]] = []
    campos_freq: dict[str, int] = {}
    coord_candidatos: list[dict[str, Any]] = []
    coord_recuperaveis: list[dict[str, Any]] = []
    melhores_registros_normalizados: list[dict[str, Any]] = []

    for base_url in DADOS_ABERTOS_MS_UBS_URLS:
        for base_params in param_sets:
            for pag in paginadores:
                params = {**base_params, **pag}
                try:
                    resp = requests.get(base_url, params=params, headers=headers, timeout=60)
                    ct = resp.headers.get("content-type", "")
                    row_ep = {
                        "url": resp.url,
                        "status": resp.status_code,
                        "content_type": ct,
                        "json": "sim" if "json" in ct.lower() else "não",
                        "tamanho_resposta": len(resp.content or b""),
                        "erro": "",
                    }
                    if not resp.ok or "json" not in ct.lower():
                        resumo_endpoints.append(row_ep)
                        continue
                    payload = resp.json()
                    listas = _json_lists_of_dicts(payload)
                    row_ep["listas_detectadas"] = len(listas)
                    row_ep["registros_detectados"] = sum(int(x.get("registros") or 0) for x in listas)
                    resumo_endpoints.append(row_ep)

                    for item in listas:
                        lista_id = f"{len(listas_detectadas) + 1}"
                        listas_detectadas.append({
                            "lista_id": lista_id,
                            "url": resp.url,
                            "caminho": item.get("caminho"),
                            "registros": item.get("registros"),
                            "qtd_campos_amostra": item.get("qtd_campos_amostra"),
                            "campos_amostra": item.get("campos_amostra"),
                        })
                        for rec in (item.get("amostra") or [])[:max_registros_por_lista]:
                            flat = _flatten_dict_limited(rec)
                            for campo in flat.keys():
                                campos_freq[campo] = campos_freq.get(campo, 0) + 1
                            lat_campos, lon_campos = _campos_coord_possiveis(flat)
                            norm = _normalizar_registro_ubs_oficial(rec)
                            if norm:
                                melhores_registros_normalizados.append(norm)
                            pares = _pares_coord_candidatos(flat)
                            lat_val = None
                            lon_val = None
                            lat_campo = pares[0][0] if pares else (lat_campos[0] if lat_campos else "")
                            lon_campo = pares[0][1] if pares else (lon_campos[0] if lon_campos else "")
                            if lat_campo:
                                lat_val = flat.get(lat_campo)
                            if lon_campo:
                                lon_val = flat.get(lon_campo)
                            recuperadas_registro = []
                            for lat_c, lon_c in pares:
                                for var in _variantes_coord_recuperaveis(flat.get(lat_c), flat.get(lon_c)):
                                    ident = _identidade_ubs_api(rec)
                                    coord_recuperaveis.append({
                                        "lista_id": lista_id,
                                        "caminho": item.get("caminho"),
                                        **ident,
                                        "campo_latitude": lat_c,
                                        "campo_longitude": lon_c,
                                        "valor_latitude_original": flat.get(lat_c),
                                        "valor_longitude_original": flat.get(lon_c),
                                        **var,
                                    })
                                    recuperadas_registro.append(var.get("regra_interpretacao"))
                            coord_candidatos.append({
                                "lista_id": lista_id,
                                "caminho": item.get("caminho"),
                                "tem_campo_latitude": "sim" if lat_campos else "não",
                                "campos_latitude": ", ".join(lat_campos[:10]),
                                "tem_campo_longitude": "sim" if lon_campos else "não",
                                "campos_longitude": ", ".join(lon_campos[:10]),
                                "pares_testados": len(pares),
                                "valor_latitude_amostra": lat_val,
                                "valor_longitude_amostra": lon_val,
                                "coord_mt_valida": "sim" if _coord_mt_valida(lat_val, lon_val) else "não",
                                "coord_recuperavel_mt": "sim" if recuperadas_registro else "não",
                                "regras_recuperacao": ", ".join(sorted(set(recuperadas_registro))[:6]),
                            })
                            relevantes = _campos_relevantes_ubs(flat)
                            amostras.append({
                                "lista_id": lista_id,
                                "caminho": item.get("caminho"),
                                **{c: flat.get(c) for c in relevantes[:40]},
                            })
                    # Para evitar muitas chamadas iguais: se já achou registros, não insiste nas demais variações do mesmo base_params.
                    if listas:
                        break
                except Exception as exc:
                    resumo_endpoints.append({
                        "url": base_url,
                        "status": "erro",
                        "content_type": "",
                        "json": "não",
                        "tamanho_resposta": 0,
                        "listas_detectadas": 0,
                        "registros_detectados": 0,
                        "erro": str(exc),
                    })

    df_endpoints = pd.DataFrame(resumo_endpoints)
    df_listas = pd.DataFrame(listas_detectadas)
    df_amostras = pd.DataFrame(amostras)
    df_coord = pd.DataFrame(coord_candidatos)
    df_recup = pd.DataFrame(coord_recuperaveis)
    if not df_recup.empty:
        # Evita multiplicar amostras idênticas quando a mesma lista aparece em endpoints equivalentes.
        dedup_cols = [c for c in ["cnes", "nome_unidade", "municipio", "campo_latitude", "campo_longitude", "latitude_interpretada", "longitude_interpretada", "regra_interpretacao"] if c in df_recup.columns]
        if dedup_cols:
            df_recup = df_recup.drop_duplicates(dedup_cols)
    df_campos = pd.DataFrame([
        {"campo": k, "frequencia_amostra": v, "parece_coordenada": ("sim" if any(tok.lower() in _chave(k).lower() for tok in COORD_LAT_TOKENS + COORD_LON_TOKENS) else "não")}
        for k, v in sorted(campos_freq.items(), key=lambda kv: (-kv[1], kv[0]))
    ])
    df_norm = pd.DataFrame(_dedup_ubs_oficiais(melhores_registros_normalizados))

    total_listas = int(len(df_listas)) if not df_listas.empty else 0
    total_registros = int(pd.to_numeric(df_listas.get("registros", pd.Series(dtype="float64")), errors="coerce").fillna(0).sum()) if not df_listas.empty else 0
    com_coord_campo = int((df_coord.get("tem_campo_latitude", pd.Series(dtype=str)).eq("sim") & df_coord.get("tem_campo_longitude", pd.Series(dtype=str)).eq("sim")).sum()) if not df_coord.empty else 0
    com_coord_valida = int(df_coord.get("coord_mt_valida", pd.Series(dtype=str)).eq("sim").sum()) if not df_coord.empty else 0

    if not df_norm.empty:
        conclusao = "A API contém registros normalizáveis com coordenadas válidas para Mato Grosso. A rotina de atualização pode ser ajustada para importá-los."
    elif not df_recup.empty:
        conclusao = "A API não trouxe coordenadas válidas no formato direto, mas há coordenadas potencialmente recuperáveis por correção de sinal, escala ou inversão lat/lon. Revise a amostra antes de atualizar o banco."
    elif com_coord_campo > 0:
        conclusao = "A API contém campos de coordenada, mas o endpoint parece ignorar filtros por UF. Use a coleta nacional com filtro local MT para atualizar somente registros validados por UF/IBGE e coordenada dentro de Mato Grosso."
    elif total_registros > 0:
        conclusao = "A API retorna registros, mas a amostra não apresentou campos claros de latitude/longitude. Provavelmente a fonte não entrega coordenada da UBS nessa resposta."
    else:
        conclusao = "A API respondeu, mas não foi encontrada lista de registros aproveitável na estrutura JSON inspecionada."

    return {
        "ok": True,
        "conclusao": conclusao,
        "resumo": {
            "endpoints_testados": int(len(df_endpoints)),
            "listas_detectadas": total_listas,
            "registros_detectados_em_listas": total_registros,
            "amostras_com_campos_de_coordenada": com_coord_campo,
            "amostras_com_coordenada_valida_mt": com_coord_valida,
            "registros_normalizados_com_coord_valida": int(len(df_norm)),
            "coordenadas_recuperaveis_mt": int(len(df_recup)),
        },
        "endpoints": df_endpoints,
        "listas": df_listas,
        "campos": df_campos,
        "coordenadas": df_coord,
        "coordenadas_recuperaveis": df_recup,
        "amostras": df_amostras,
        "registros_normalizados": df_norm,
    }


# ============================================================================
# Georreferenciamento v13 — elegibilidade APS e tentativa CNES/tbEstabelecimento
# ============================================================================

APS_INCLUDE_TOKENS = [
    "UNIDADE BASICA", "UNIDADE BÁSICA", "UBS", "POSTO DE SAUDE", "POSTO DE SAÚDE",
    "CENTRO DE SAUDE", "CENTRO DE SAÚDE", "SAUDE DA FAMILIA", "SAÚDE DA FAMÍLIA",
    "ESF", "USF", "EQUIPE DE SAUDE", "EQUIPE DE SAÚDE", "UNIDADE DE SAUDE DA FAMILIA",
    "UNIDADE DE SAÚDE DA FAMÍLIA", "PSF",
]

APS_EXCLUDE_TOKENS = [
    "HOSPITAL", "LABORATORIO", "LABORATÓRIO", "FARMACIA", "FARMÁCIA", "SECRETARIA",
    "VIGILANCIA", "VIGILÂNCIA", "SAMU", "UPA", "CAPS", "POLICLINICA", "POLICLÍNICA",
    "CLINICA", "CLÍNICA", "CONSULTORIO", "CONSULTÓRIO", "CENTRAL", "REGULACAO",
    "REGULAÇÃO", "HEMOCENTRO", "CEO ", "CENTRO DE ESPECIAL", "PRONTO ATENDIMENTO",
    "UNIDADE MOVEL", "UNIDADE MÓVEL", "AMBULATORIO", "AMBULATÓRIO",
]

CNES_RESOURCE_SEARCH_URLS = [
    "https://dadosabertos.saude.gov.br/api/3/action/package_search?q=CNES%20estabelecimento",
    "https://dadosabertos.saude.gov.br/api/3/action/package_search?q=tbEstabelecimento",
    "https://dadosabertos.saude.gov.br/api/3/action/package_search?q=Cadastro%20Nacional%20Estabelecimentos%20Sa%C3%BAde",
]

CNES_COORD_DIRECT_URLS = [
    # Tentativas conhecidas/possíveis; quando retornarem 403/404 ou conteúdo inválido, o diagnóstico registra e segue.
    "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/CNES/cnes_estabelecimentos.csv.zip",
    "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/CNES/estabelecimentos.csv.zip",
    "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/CNES/tbEstabelecimento.csv.zip",
]

CNES_COL_CNES = ["co_cnes", "cnes", "codigo_cnes", "cod_cnes"]
CNES_COL_NOME = ["no_fantasia", "nome_fantasia", "nome_unidade", "no_razao_social", "nome", "estabelecimento"]
CNES_COL_TIPO = ["ds_tipo_unidade", "tipo_unidade", "tp_unidade", "co_tipo_unidade"]
CNES_COL_MUN = ["no_municipio", "municipio", "nome_municipio", "nm_municipio"]
CNES_COL_COD_MUN = ["co_municipio_gestor", "co_municipio", "codigo_ibge", "cod_municipio", "ibge"]
CNES_COL_ENDERECO = ["no_logradouro", "logradouro", "endereco", "endereço", "ds_endereco"]


def _lat_lon_mt_estrito(lat: Any, lon: Any) -> bool:
    """Validação conservadora para coordenadas de Mato Grosso.

    Evita aceitar pontos de MS/SP/GO como se fossem MT. Mantém pequena margem para bordas.
    """
    try:
        latf = float(str(lat).replace(",", "."))
        lonf = float(str(lon).replace(",", "."))
    except Exception:
        return False
    return (-19.5 <= latf <= -7.0) and (-62.5 <= lonf <= -50.0)


def _normalizar_cnes(valor: Any) -> str:
    import re
    dig = re.sub(r"\D", "", "" if valor is None else str(valor))
    return dig.strip()


def _codigo_ibge_7_por_valor(valor: Any, mapa_6_para_7: dict[str, str] | None = None) -> str:
    import re
    dig = re.sub(r"\D", "", "" if valor is None else str(valor))
    if len(dig) >= 7:
        return dig[:7]
    if len(dig) == 6 and mapa_6_para_7:
        return mapa_6_para_7.get(dig, dig)
    return dig


def _texto_livre_estab(row: pd.Series) -> str:
    partes = []
    for c in ["nome_unidade", "tipo_unidade", "nome_fantasia", "tipo", "estabelecimento", "endereco"]:
        if c in row.index:
            partes.append(str(row.get(c) or ""))
    return _chave(" ".join(partes))


def _classificar_elegibilidade_linha(row: pd.Series) -> tuple[bool, str]:
    texto = _texto_livre_estab(row)
    if not texto:
        return False, "Sem nome/tipo suficiente para classificar"
    if any(_chave(tok) in texto for tok in APS_EXCLUDE_TOKENS):
        return False, "Excluído por tipo/nome não compatível com UBS/APS"
    if any(_chave(tok) in texto for tok in APS_INCLUDE_TOKENS):
        return True, "Elegível por tipo/nome compatível com UBS/APS"
    return False, "Não classificado como UBS/APS por regra textual conservadora"


def _resumo_cnes_equipes_aps() -> tuple[set[str], pd.DataFrame]:
    """Retorna CNES únicos com equipes APS/INE e resumo por CNES.

    A tabela `equipes_aps` pode ter várias linhas por estabelecimento. Para
    elegibilidade geográfica, o universo deve ser o CNES único do
    estabelecimento, não uma linha por equipe nem uma duplicidade da tabela
    `estabelecimentos_saude`.
    """
    eq = _read_table_safe("equipes_aps")
    if eq.empty or "cnes" not in eq.columns:
        return set(), pd.DataFrame(columns=["cnes_norm", "qtd_equipes_aps", "tipos_equipes_aps"])

    eq2 = eq.copy()
    eq2["cnes_norm"] = eq2["cnes"].map(_normalizar_cnes)
    eq2 = eq2[eq2["cnes_norm"].astype(str).str.strip().ne("")].copy()

    if "codigo_tipo_equipe" in eq2.columns:
        cod = eq2["codigo_tipo_equipe"].astype(str).str.extract(r"(\d+)")[0].fillna("")
        eq2 = eq2[cod.isin(["70", "71", "72", "73", "74", "76"])].copy()

    if eq2.empty:
        return set(), pd.DataFrame(columns=["cnes_norm", "qtd_equipes_aps", "tipos_equipes_aps"])

    tipo_col = "codigo_tipo_equipe" if "codigo_tipo_equipe" in eq2.columns else "cnes_norm"
    resumo = (
        eq2.groupby("cnes_norm", dropna=False)
        .agg(
            qtd_equipes_aps=("cnes_norm", "count"),
            tipos_equipes_aps=(tipo_col, lambda s: "; ".join(sorted(set(map(str, s.dropna()))))),
        )
        .reset_index()
    )
    cnes_equipes = set(resumo["cnes_norm"].dropna().astype(str)) - {""}
    return cnes_equipes, resumo


def _deduplicar_estabelecimentos_por_cnes(df: pd.DataFrame) -> pd.DataFrame:
    """Deduplica estabelecimentos por CNES, priorizando linha com coordenada válida.

    A base atual pode conter duas linhas para o mesmo CNES: uma herdada/antiga sem
    coordenada e outra recuperada com coordenada. Sem essa deduplicação, a régua de
    elegibilidade fica artificialmente em 50% em vários municípios.
    """
    if df.empty or "cnes_norm" not in df.columns:
        return df
    out = df.copy()
    out = out[out["cnes_norm"].astype(str).str.strip().ne("")].copy()
    if out.empty:
        return out
    if "coord_valida_mt" not in out.columns:
        out["coord_valida_mt"] = False
    if "qtd_equipes_aps" not in out.columns:
        out["qtd_equipes_aps"] = 0
    out["_prioridade_coord"] = out["coord_valida_mt"].astype(bool).astype(int)
    out["_prioridade_equipes"] = pd.to_numeric(out["qtd_equipes_aps"], errors="coerce").fillna(0).astype(int)
    # Mantém a melhor linha por CNES: coordenada válida > mais equipes > primeira ocorrência.
    out = out.sort_values(["cnes_norm", "_prioridade_coord", "_prioridade_equipes"], ascending=[True, False, False])
    out = out.drop_duplicates("cnes_norm", keep="first").drop(columns=["_prioridade_coord", "_prioridade_equipes"], errors="ignore")
    return out.reset_index(drop=True)


def classificar_estabelecimentos_elegiveis_aps() -> dict:
    """Classifica estabelecimentos que devem entrar no cálculo de distância APS.

    Versão refinada v15: usa CNES único vinculado a equipes APS/INE e remove
    duplicidades em `estabelecimentos_saude`, priorizando a linha com coordenada
    válida. Isso corrige o efeito artificial de 50% causado por duas linhas do
    mesmo CNES (uma com coordenada e outra sem).
    """
    df = _read_table_safe("estabelecimentos_saude")
    if df.empty:
        return {
            "ok": False,
            "mensagem": "Tabela estabelecimentos_saude não encontrada ou vazia.",
            "diagnostico": {},
            "estabelecimentos": pd.DataFrame(),
            "resumo_municipal": pd.DataFrame(),
        }

    out = df.copy()
    for col in ["cnes", "nome_unidade", "tipo_unidade", "municipio", "codigo_ibge", "endereco"]:
        if col not in out.columns:
            out[col] = ""
    if "nome_unidade" not in out.columns or out["nome_unidade"].astype(str).str.strip().eq("").all():
        for alt in ["nome_fantasia", "no_fantasia", "estabelecimento", "nome"]:
            if alt in out.columns:
                out["nome_unidade"] = out[alt]
                break

    out["cnes_norm"] = out["cnes"].map(_normalizar_cnes)
    cnes_equipes, resumo_equipes = _resumo_cnes_equipes_aps()

    if not resumo_equipes.empty:
        out = out.merge(resumo_equipes, on="cnes_norm", how="left")
    else:
        out["qtd_equipes_aps"] = 0
        out["tipos_equipes_aps"] = ""

    out["qtd_equipes_aps"] = pd.to_numeric(out.get("qtd_equipes_aps", 0), errors="coerce").fillna(0).astype(int)
    out["tipos_equipes_aps"] = out.get("tipos_equipes_aps", "").fillna("").astype(str)

    textual = out.apply(_classificar_elegibilidade_linha, axis=1)
    out["parece_ubs_por_nome"] = [bool(x[0]) for x in textual]
    out["motivo_textual_aps"] = [str(x[1]) for x in textual]

    out["elegivel_aps_distancia"] = out["cnes_norm"].isin(cnes_equipes)
    out["motivo_elegibilidade_aps"] = out.apply(
        lambda r: f"Elegível por vínculo CNES/INE com equipe APS ({r.get('tipos_equipes_aps','')})" if bool(r.get("elegivel_aps_distancia"))
        else "Não elegível para distância APS: sem equipe APS/INE vinculada no CNES local",
        axis=1,
    )

    lat_col = _pick_col(out, LAT_CANDIDATES)
    lon_col = _pick_col(out, LON_CANDIDATES)
    if lat_col and lon_col:
        out["latitude_decimal"] = pd.to_numeric(out[lat_col], errors="coerce")
        out["longitude_decimal"] = pd.to_numeric(out[lon_col], errors="coerce")
        out["coord_valida_mt"] = [_lat_lon_mt_estrito(a, b) for a, b in zip(out["latitude_decimal"], out["longitude_decimal"])]
    else:
        out["latitude_decimal"] = pd.NA
        out["longitude_decimal"] = pd.NA
        out["coord_valida_mt"] = False

    eleg_bruto = out[out["elegivel_aps_distancia"]].copy()
    eleg = _deduplicar_estabelecimentos_por_cnes(eleg_bruto)
    pend = eleg[~eleg["coord_valida_mt"].astype(bool)].copy()
    geo = eleg[eleg["coord_valida_mt"].astype(bool)].copy()
    pct_eleg = round(len(geo) / len(eleg) * 100, 2) if len(eleg) else 0.0
    pct_total = round(int(out["coord_valida_mt"].sum()) / len(out) * 100, 2) if len(out) else 0.0
    duplicidades_removidas = int(max(0, len(eleg_bruto) - len(eleg)))

    resumo = pd.DataFrame()
    if not eleg.empty and "municipio" in eleg.columns:
        resumo = eleg.groupby("municipio", dropna=False).agg(
            elegiveis_aps=("cnes_norm", "nunique"),
            equipes_aps_vinculadas=("qtd_equipes_aps", "sum"),
            elegiveis_georreferenciadas=("coord_valida_mt", lambda s: int(pd.Series(s).astype(bool).sum())),
            elegiveis_pendentes=("coord_valida_mt", lambda s: int((~pd.Series(s).astype(bool)).sum())),
        ).reset_index()
        resumo["percentual_georreferenciado"] = (resumo["elegiveis_georreferenciadas"] / resumo["elegiveis_aps"] * 100).round(2)
        resumo = resumo.sort_values(["elegiveis_pendentes", "elegiveis_aps"], ascending=[False, False])

    cols = [c for c in [
        "cnes", "nome_unidade", "tipo_unidade", "municipio", "codigo_ibge", "endereco",
        "qtd_equipes_aps", "tipos_equipes_aps", "parece_ubs_por_nome", "motivo_textual_aps",
        "latitude_decimal", "longitude_decimal", "coord_valida_mt", "elegivel_aps_distancia",
        "motivo_elegibilidade_aps", "fonte", "atualizado_em",
    ] if c in out.columns]

    return {
        "ok": True,
        "mensagem": "Elegibilidade APS recalculada por CNES único vinculado a equipes APS/INE, com deduplicação e preferência por coordenada válida.",
        "diagnostico": {
            "total_estabelecimentos": int(len(out)),
            "total_com_coordenada_mt": int(out["coord_valida_mt"].astype(bool).sum()),
            "percentual_total_georreferenciado": pct_total,
            "cnes_com_equipes_aps": int(len(cnes_equipes)),
            "linhas_elegiveis_brutas": int(len(eleg_bruto)),
            "duplicidades_elegiveis_removidas": duplicidades_removidas,
            "elegiveis_aps": int(len(eleg)),
            "elegiveis_com_coordenada": int(len(geo)),
            "elegiveis_pendentes": int(len(pend)),
            "percentual_elegiveis_georreferenciados": pct_eleg,
            "coluna_latitude": lat_col or "não localizada",
            "coluna_longitude": lon_col or "não localizada",
            "criterio_elegibilidade": "CNES único presente na tabela equipes_aps com códigos 70, 71, 72, 73, 74 ou 76; duplicidades de estabelecimento removidas",
        },
        "estabelecimentos": out[cols].copy(),
        "elegiveis_brutos": eleg_bruto[cols].copy(),
        "elegiveis": eleg[cols].copy(),
        "pendentes_elegiveis": pend[cols].copy(),
        "resumo_municipal": resumo,
    }


def _descobrir_recursos_cnes_coordenadas(max_recursos: int = 20) -> tuple[list[dict[str, Any]], list[str]]:
    recursos: list[dict[str, Any]] = []
    logs: list[str] = []
    for url in CNES_RESOURCE_SEARCH_URLS:
        try:
            r = requests.get(url, timeout=35)
            logs.append(f"{url} | status={r.status_code} | ct={r.headers.get('content-type','')}")
            if r.status_code != 200:
                continue
            data = r.json()
            for result in (data.get("result", {}).get("results") or []):
                for res in (result.get("resources") or []):
                    u = res.get("url") or ""
                    nome = " ".join([str(res.get("name") or ""), str(res.get("description") or ""), str(result.get("title") or "")])
                    fmt = str(res.get("format") or "").lower()
                    texto = _chave(nome + " " + u + " " + fmt)
                    if not u:
                        continue
                    if ("ESTAB" in texto or "CNES" in texto or "TBESTABELECIMENTO" in texto) and any(x in texto for x in ["CSV", "ZIP", "XLS", "JSON", "DBF"]):
                        recursos.append({
                            "url": u,
                            "nome": res.get("name") or result.get("title") or "Recurso CNES",
                            "dataset": result.get("title") or "",
                            "formato": res.get("format") or "",
                            "origem": "CKAN Dados Abertos Saúde",
                        })
        except Exception as exc:
            logs.append(f"{url} | erro={exc}")
    for u in CNES_COORD_DIRECT_URLS:
        recursos.append({"url": u, "nome": "Tentativa direta CNES estabelecimento", "dataset": "CNES", "formato": "ZIP/CSV", "origem": "URL direta"})
    # dedup
    vistos = set()
    out = []
    for r in recursos:
        if r["url"] in vistos:
            continue
        vistos.add(r["url"])
        out.append(r)
        if len(out) >= max_recursos:
            break
    return out, logs


def _ler_recurso_tabular_cnes(url: str, timeout: int = 60) -> tuple[pd.DataFrame, str]:
    import io
    import zipfile
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        ct = resp.headers.get("content-type", "")
        if resp.status_code != 200:
            return pd.DataFrame(), f"status={resp.status_code}; ct={ct}"
        content = resp.content
        if not content:
            return pd.DataFrame(), "conteúdo vazio"
        low_url = url.lower()
        if zipfile.is_zipfile(io.BytesIO(content)):
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                nomes = zf.namelist()
                candidatos = [n for n in nomes if n.lower().endswith((".csv", ".txt", ".tsv"))]
                if not candidatos:
                    return pd.DataFrame(), f"zip sem csv/txt; arquivos={nomes[:5]}"
                # prioriza arquivos de estabelecimento
                candidatos = sorted(candidatos, key=lambda n: (0 if "estab" in n.lower() else 1, n))
                for nome in candidatos[:4]:
                    raw = zf.read(nome)
                    for enc in ["utf-8", "latin1", "cp1252"]:
                        for sep in [";", ",", "\t", "|"]:
                            try:
                                df = pd.read_csv(io.BytesIO(raw), sep=sep, encoding=enc, dtype=str, nrows=None, low_memory=False)
                                if len(df.columns) >= 4:
                                    return df, f"zip={nome}; enc={enc}; sep={sep}; linhas={len(df)}; colunas={len(df.columns)}"
                            except Exception:
                                pass
                return pd.DataFrame(), "zip lido, mas nenhum csv/txt foi interpretado"
        if low_url.endswith((".csv", ".txt", ".tsv")) or "csv" in ct.lower() or "text" in ct.lower():
            for enc in ["utf-8", "latin1", "cp1252"]:
                for sep in [";", ",", "\t", "|"]:
                    try:
                        df = pd.read_csv(io.BytesIO(content), sep=sep, encoding=enc, dtype=str, low_memory=False)
                        if len(df.columns) >= 4:
                            return df, f"arquivo texto; enc={enc}; sep={sep}; linhas={len(df)}; colunas={len(df.columns)}"
                    except Exception:
                        pass
        return pd.DataFrame(), f"formato não tabular reconhecido; ct={ct}; início={content[:80]!r}"
    except Exception as exc:
        return pd.DataFrame(), f"erro={exc}"


def _normalizar_cnes_coord_df(df: pd.DataFrame, origem: str = "") -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    d = df.copy()
    d.columns = [str(c).strip().lower() for c in d.columns]
    cnes_col = _pick_col(d, CNES_COL_CNES)
    lat_col = _pick_col(d, LAT_CANDIDATES + ["nu_latitude", "vl_latitude"])
    lon_col = _pick_col(d, LON_CANDIDATES + ["nu_longitude", "vl_longitude"])
    cod_col = _pick_col(d, CNES_COL_COD_MUN + CODIGO_CANDIDATES)
    mun_col = _pick_col(d, CNES_COL_MUN + MUNICIPIO_CANDIDATES)
    nome_col = _pick_col(d, CNES_COL_NOME)
    tipo_col = _pick_col(d, CNES_COL_TIPO)
    end_col = _pick_col(d, CNES_COL_ENDERECO)
    if not cnes_col or not lat_col or not lon_col:
        return pd.DataFrame()

    ref = _municipios_referencia()
    mapa6 = {}
    mapa_nome = {}
    if not ref.empty:
        ref = ref.copy()
        ref["codigo_ibge"] = ref["codigo_ibge"].astype(str).str.extract(r"(\d+)")[0].fillna("")
        for _, r in ref.iterrows():
            cod = str(r.get("codigo_ibge") or "")
            if len(cod) >= 7:
                mapa6[cod[:6]] = cod[:7]
            mapa_nome[_chave(r.get("municipio"))] = cod[:7]

    out = pd.DataFrame()
    out["cnes"] = d[cnes_col].map(_normalizar_cnes)
    out["latitude"] = pd.to_numeric(d[lat_col].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    out["longitude"] = pd.to_numeric(d[lon_col].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    out["codigo_ibge"] = d[cod_col].map(lambda x: _codigo_ibge_7_por_valor(x, mapa6)) if cod_col else ""
    out["municipio"] = d[mun_col].astype(str).str.strip() if mun_col else ""
    if mun_col and not mapa_nome:
        pass
    if mun_col and "codigo_ibge" in out.columns:
        vazio = out["codigo_ibge"].astype(str).str.len() < 7
        out.loc[vazio, "codigo_ibge"] = out.loc[vazio, "municipio"].map(lambda x: mapa_nome.get(_chave(x), ""))
    out["nome_unidade"] = d[nome_col].astype(str).str.strip() if nome_col else ""
    out["tipo_unidade"] = d[tipo_col].astype(str).str.strip() if tipo_col else ""
    out["endereco"] = d[end_col].astype(str).str.strip() if end_col else ""
    out["fonte"] = f"CNES/tbEstabelecimento — {origem}"[:240]
    out["coord_valida_mt"] = [_lat_lon_mt_estrito(a, b) for a, b in zip(out["latitude"], out["longitude"])]
    out["registro_mt"] = out["codigo_ibge"].astype(str).str.startswith("51") | out["municipio"].map(lambda x: _chave(x) in mapa_nome)
    out = out[(out["cnes"].astype(str).str.len() > 0) & out["coord_valida_mt"] & out["registro_mt"]].copy()
    out = out.drop_duplicates(subset=["cnes"], keep="first")
    return out


def importar_coordenadas_cnes_tbestabelecimento(max_recursos: int = 12) -> dict:
    """Tenta obter coordenadas oficiais do CNES/tbEstabelecimento em recursos públicos.

    A rotina é conservadora: só atualiza por CNES, somente registros confirmados como MT
    por código/nome de município e com latitude/longitude dentro do território estadual.
    Não usa geocodificação nem centroide.
    """
    recursos, logs_busca = _descobrir_recursos_cnes_coordenadas(max_recursos=max_recursos)
    tentativas = []
    candidatos = []
    for rec in recursos:
        df_raw, detalhe = _ler_recurso_tabular_cnes(rec["url"])
        tentativas.append({
            "url": rec["url"],
            "nome": rec.get("nome", ""),
            "dataset": rec.get("dataset", ""),
            "formato": rec.get("formato", ""),
            "detalhe_leitura": detalhe,
            "linhas_lidas": int(len(df_raw)) if isinstance(df_raw, pd.DataFrame) else 0,
        })
        if df_raw.empty:
            continue
        norm = _normalizar_cnes_coord_df(df_raw, origem=rec.get("nome") or rec.get("dataset") or rec["url"])
        if not norm.empty:
            candidatos.append(norm)
            # já encontrou recurso aproveitável; evita downloads pesados adicionais
            break
    if not candidatos:
        return {
            "ok": False,
            "mensagem": "Não foi encontrado recurso público CNES/tbEstabelecimento com CNES + latitude/longitude válidos para MT nesta tentativa.",
            "recursos_detectados": pd.DataFrame(recursos),
            "logs_busca": pd.DataFrame({"log": logs_busca}),
            "tentativas": pd.DataFrame(tentativas),
            "candidatos": pd.DataFrame(),
            "atualizadas_por_cnes": 0,
            "diagnostico_pos": qualificar_unidades_aps_georreferenciadas().get("diagnostico", {}),
            "elegibilidade": classificar_estabelecimentos_elegiveis_aps().get("diagnostico", {}),
        }
    oficiais = pd.concat(candidatos, ignore_index=True).drop_duplicates(subset=["cnes"], keep="first")
    locais = _read_table_safe("estabelecimentos_saude")
    if locais.empty or "cnes" not in locais.columns:
        return {
            "ok": False,
            "mensagem": "A tabela estabelecimentos_saude está vazia ou sem coluna CNES.",
            "recursos_detectados": pd.DataFrame(recursos),
            "logs_busca": pd.DataFrame({"log": logs_busca}),
            "tentativas": pd.DataFrame(tentativas),
            "candidatos": oficiais.head(300),
            "atualizadas_por_cnes": 0,
            "diagnostico_pos": qualificar_unidades_aps_georreferenciadas().get("diagnostico", {}),
            "elegibilidade": classificar_estabelecimentos_elegiveis_aps().get("diagnostico", {}),
        }
    cnes_locais = set(locais["cnes"].astype(str).map(_normalizar_cnes))
    oficiais = oficiais[oficiais["cnes"].astype(str).isin(cnes_locais)].copy()
    atualizadas = 0
    agora = datetime.now().isoformat(timespec="seconds")
    with db_session() as conn:
        for _, row in oficiais.iterrows():
            cnes = str(row.get("cnes") or "").strip()
            if not cnes:
                continue
            conn.execute(
                """
                UPDATE estabelecimentos_saude
                SET latitude = ?, longitude = ?, codigo_ibge = COALESCE(NULLIF(codigo_ibge,''), ?),
                    municipio = COALESCE(NULLIF(municipio,''), ?), nome_unidade = COALESCE(NULLIF(nome_unidade,''), ?),
                    tipo_unidade = COALESCE(NULLIF(tipo_unidade,''), ?), endereco = COALESCE(NULLIF(endereco,''), ?),
                    fonte = ?, atualizado_em = ?
                WHERE cnes = ?
                """,
                (
                    float(row["latitude"]), float(row["longitude"]), row.get("codigo_ibge", ""), row.get("municipio", ""),
                    row.get("nome_unidade", ""), row.get("tipo_unidade", ""), row.get("endereco", ""),
                    row.get("fonte", "CNES/tbEstabelecimento"), agora, cnes,
                ),
            )
            atualizadas += int(conn.total_changes > 0)
    diag = qualificar_unidades_aps_georreferenciadas().get("diagnostico", {})
    eleg = classificar_estabelecimentos_elegiveis_aps().get("diagnostico", {})
    return {
        "ok": atualizadas > 0,
        "mensagem": f"Coordenadas CNES/tbEstabelecimento aplicadas por CNES: {atualizadas}." if atualizadas else "Recurso CNES foi lido, mas nenhum CNES local pendente foi atualizado.",
        "recursos_detectados": pd.DataFrame(recursos),
        "logs_busca": pd.DataFrame({"log": logs_busca}),
        "tentativas": pd.DataFrame(tentativas),
        "candidatos": oficiais.head(500),
        "atualizadas_por_cnes": int(atualizadas),
        "diagnostico_pos": diag,
        "elegibilidade": eleg,
    }



# ============================================================================
# Acesso rural à APS — v16
# ============================================================================

def montar_acesso_rural_aps() -> dict:
    """Monta leitura gerencial do acesso rural à APS a partir das distâncias assentamento -> UBS/APS.

    Usa exclusivamente as distâncias reais calculadas por `calcular_distancias_assentamentos_ubs`,
    que, na versão atual, considera apenas unidades APS elegíveis por vínculo CNES/INE e com
    coordenadas válidas em Mato Grosso.
    """
    resultado = calcular_distancias_assentamentos_ubs(usar_aproximacao_municipal=False)
    dist = resultado.get("distancias", pd.DataFrame()).copy()
    resumo_mun = resultado.get("resumo_municipal", pd.DataFrame()).copy()
    resumo_reg = resultado.get("resumo_regional", pd.DataFrame()).copy()
    diag = dict(resultado.get("diagnostico", {}))

    vazio = {
        "diagnostico": diag,
        "distancias": dist,
        "resumo_municipal": resumo_mun,
        "resumo_regional": resumo_reg,
        "ranking_assentamentos": pd.DataFrame(),
        "ranking_municipios": pd.DataFrame(),
        "ranking_regioes": pd.DataFrame(),
        "ubs_mais_demandadas": pd.DataFrame(),
        "matriz_alertas": pd.DataFrame(),
        "mensagens_chave": [],
    }
    if dist.empty:
        vazio["mensagens_chave"] = [
            "A análise de acesso rural não foi calculada porque não há assentamentos e/ou unidades APS georreferenciadas suficientes.",
            "A rotina não utiliza centroides municipais nem coordenadas aproximadas.",
        ]
        return vazio

    dist["distancia_ubs_mais_proxima_km"] = pd.to_numeric(dist.get("distancia_ubs_mais_proxima_km"), errors="coerce")
    dist["classe_distancia_aps"] = dist.get("classe_distancia_aps", "Sem cálculo").astype(str)
    total = int(len(dist))
    criticos = int((dist["classe_distancia_aps"] == "Crítico").sum())
    distantes = int((dist["classe_distancia_aps"] == "Distante").sum())
    atencao = int((dist["classe_distancia_aps"] == "Atenção").sum())
    proximos = int((dist["classe_distancia_aps"] == "Próximo").sum())
    media = round(float(dist["distancia_ubs_mais_proxima_km"].mean()), 2) if total else 0.0
    mediana = round(float(dist["distancia_ubs_mais_proxima_km"].median()), 2) if total else 0.0
    maxima = round(float(dist["distancia_ubs_mais_proxima_km"].max()), 2) if total else 0.0
    acima_15 = int((dist["distancia_ubs_mais_proxima_km"] > 15).sum())
    acima_30 = int((dist["distancia_ubs_mais_proxima_km"] > 30).sum())
    acima_50 = int((dist["distancia_ubs_mais_proxima_km"] > 50).sum())

    diag.update({
        "total_assentamentos_analisados": total,
        "distancia_media_km": media,
        "distancia_mediana_km": mediana,
        "distancia_maxima_km": maxima,
        "proximos": proximos,
        "atencao": atencao,
        "distantes": distantes,
        "criticos": criticos,
        "acima_15km": acima_15,
        "acima_30km": acima_30,
        "acima_50km": acima_50,
        "percentual_critico_distante": round(((criticos + distantes) / total * 100), 1) if total else 0.0,
    })

    ranking_ass = dist.sort_values("distancia_ubs_mais_proxima_km", ascending=False).copy()

    if resumo_mun.empty:
        ranking_mun = pd.DataFrame()
    else:
        ranking_mun = resumo_mun.copy()
        for c in ["assentamentos", "criticos", "distantes", "atencao", "proximos"]:
            if c in ranking_mun.columns:
                ranking_mun[c] = pd.to_numeric(ranking_mun[c], errors="coerce").fillna(0).astype(int)
        if "assentamentos" in ranking_mun.columns:
            ranking_mun["criticos_distantes"] = ranking_mun.get("criticos", 0) + ranking_mun.get("distantes", 0)
            ranking_mun["percentual_critico_distante"] = (ranking_mun["criticos_distantes"] / ranking_mun["assentamentos"].replace(0, pd.NA) * 100).fillna(0).round(1)
        ranking_mun = ranking_mun.sort_values(
            [c for c in ["criticos", "distantes", "distancia_maxima_km", "distancia_media_km"] if c in ranking_mun.columns],
            ascending=[False, False, False, False][:len([c for c in ["criticos", "distantes", "distancia_maxima_km", "distancia_media_km"] if c in ranking_mun.columns])]
        ).reset_index(drop=True)

    if resumo_reg.empty:
        ranking_reg = pd.DataFrame()
    else:
        ranking_reg = resumo_reg.copy().reset_index(drop=True)

    ubs_mais = pd.DataFrame()
    if {"cnes_ubs_mais_proxima", "ubs_mais_proxima"}.issubset(dist.columns):
        ubs_mais = (
            dist.groupby(["cnes_ubs_mais_proxima", "ubs_mais_proxima", "municipio_ubs_mais_proxima"], dropna=False)
            .agg(
                assentamentos_referenciados=("assentamento", "count"),
                distancia_media_km=("distancia_ubs_mais_proxima_km", "mean"),
                distancia_maxima_km=("distancia_ubs_mais_proxima_km", "max"),
                criticos=("classe_distancia_aps", lambda s: int((s == "Crítico").sum())),
                distantes=("classe_distancia_aps", lambda s: int((s == "Distante").sum())),
            )
            .reset_index()
        )
        ubs_mais["distancia_media_km"] = ubs_mais["distancia_media_km"].round(2)
        ubs_mais["distancia_maxima_km"] = ubs_mais["distancia_maxima_km"].round(2)
        ubs_mais = ubs_mais.sort_values(["assentamentos_referenciados", "criticos", "distancia_maxima_km"], ascending=[False, False, False])

    alertas = []
    if not ranking_mun.empty:
        for _, row in ranking_mun.iterrows():
            assentamentos = int(row.get("assentamentos", 0) or 0)
            if assentamentos <= 0:
                continue
            crit = int(row.get("criticos", 0) or 0)
            dista = int(row.get("distantes", 0) or 0)
            media_m = float(row.get("distancia_media_km", 0) or 0)
            max_m = float(row.get("distancia_maxima_km", 0) or 0)
            if crit > 0:
                nivel = "Crítico"
                prioridade = 1
                encaminhamento = "Avaliar estratégia específica de acesso rural: reorganização de referência APS, unidade volante, rota de atendimento, teleapoio e pactuação territorial."
            elif dista > 0:
                nivel = "Alto"
                prioridade = 2
                encaminhamento = "Avaliar agenda rural programada, reforço de vínculo com equipe APS e revisão da referência territorial dos assentamentos."
            elif media_m > 15:
                nivel = "Médio"
                prioridade = 3
                encaminhamento = "Monitorar distâncias médias e validar se a referência APS corresponde ao fluxo real da população rural."
            else:
                nivel = "Monitoramento"
                prioridade = 4
                encaminhamento = "Manter monitoramento e validar periodicamente a base geográfica."
            alertas.append({
                "municipio": row.get("municipio"),
                "regiao_saude": row.get("regiao_saude", "Não informada"),
                "nivel_alerta_acesso_rural": nivel,
                "prioridade_ordem": prioridade,
                "assentamentos": assentamentos,
                "criticos": crit,
                "distantes": dista,
                "distancia_media_km": round(media_m, 2),
                "distancia_maxima_km": round(max_m, 2),
                "encaminhamento_sugerido": encaminhamento,
            })
    matriz_alertas = pd.DataFrame(alertas)
    if not matriz_alertas.empty:
        matriz_alertas = matriz_alertas.sort_values(["prioridade_ordem", "criticos", "distancia_maxima_km"], ascending=[True, False, False]).reset_index(drop=True)

    mensagens = [
        f"Foram analisados {total} assentamentos com coordenadas e {int(diag.get('referencias_usadas', 0) or 0)} unidades APS georreferenciadas elegíveis.",
        f"A distância média assentamento → UBS/APS mais próxima é de {media} km; a maior distância observada é de {maxima} km.",
        f"{criticos} assentamentos estão em situação crítica (>30 km) e {distantes} em situação distante (15 a 30 km).",
        "O cálculo usa distância geodésica em linha reta; não substitui análise de rota viária, tempo de deslocamento ou validação local do fluxo assistencial.",
    ]

    return {
        "diagnostico": diag,
        "distancias": dist,
        "resumo_municipal": resumo_mun,
        "resumo_regional": resumo_reg,
        "ranking_assentamentos": ranking_ass,
        "ranking_municipios": ranking_mun,
        "ranking_regioes": ranking_reg,
        "ubs_mais_demandadas": ubs_mais,
        "matriz_alertas": matriz_alertas,
        "mensagens_chave": mensagens,
    }


# =============================================================================
# Bairros/localidades/setores censitários — diagnóstico e distância até APS
# =============================================================================

def _carregar_bairros_localidades_referencia(caminho_csv: str | Path | None = None) -> pd.DataFrame:
    """Carrega a base recuperada do sistema antigo com setores censitários/localidades.

    Observação metodológica: esta base usa setores censitários do IBGE 2022 como
    unidade territorial de granularidade fina. Ela não deve ser vendida como
    cadastro oficial de bairros, mas atende à demanda de enxergar vazios intra-
    municipais e áreas urbanas/rurais distantes da APS.
    """
    caminho = Path(caminho_csv) if caminho_csv else BAIRROS_LOCALIDADES_SISTEMA_ANTIGO
    if not caminho.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(caminho, dtype={"codigo_ibge": str, "setor_censitario": str})
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()

    out = df.copy()
    for col in ["codigo_ibge", "municipio", "bairro_ou_localidade", "tipo_territorio", "setor_censitario"]:
        if col not in out.columns:
            out[col] = ""
    out["codigo_ibge"] = out["codigo_ibge"].map(_codigo_limpo)
    out["municipio"] = out["municipio"].astype(str).str.replace(" - MT", "", regex=False).str.strip()
    out["bairro_ou_localidade"] = out["bairro_ou_localidade"].astype(str).str.strip()
    out["tipo_territorio"] = out["tipo_territorio"].astype(str).str.strip()
    out["latitude"] = pd.to_numeric(out.get("latitude"), errors="coerce")
    out["longitude"] = pd.to_numeric(out.get("longitude"), errors="coerce")
    out["populacao"] = pd.to_numeric(out.get("populacao"), errors="coerce").fillna(0)
    out["coord_valida_mt"] = [_lat_lon_mt_estrito(a, b) for a, b in zip(out["latitude"], out["longitude"])]
    out = _aplicar_nome_amigavel_territorios(out)
    return out.reset_index(drop=True)


def diagnosticar_base_bairros_localidades() -> dict:
    """Diagnostica a camada fina recuperada: setores/bairros/localidades."""
    df = _carregar_bairros_localidades_referencia()
    diag = {
        "base_encontrada": bool(not df.empty),
        "registros": int(len(df)),
        "municipios_cobertos": int(df["municipio"].nunique()) if not df.empty and "municipio" in df.columns else 0,
        "com_coordenadas_validas": int(df.get("coord_valida_mt", pd.Series(dtype=bool)).astype(bool).sum()) if not df.empty else 0,
        "populacao_total_referencia": int(pd.to_numeric(df.get("populacao", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not df.empty else 0,
        "territorios_com_nome_validado": int(df.get("nome_territorio_validado", pd.Series(dtype=bool)).astype(bool).sum()) if not df.empty else 0,
        "territorios_sem_nome_validado": int((~df.get("nome_territorio_validado", pd.Series(dtype=bool)).astype(bool)).sum()) if not df.empty else 0,
        "tipos_territorio": (df.get("tipo_territorio", pd.Series(dtype=str)).value_counts().head(10).rename_axis("tipo_territorio").reset_index(name="registros")) if not df.empty else pd.DataFrame(),
    }
    resumo_mun = pd.DataFrame()
    if not df.empty:
        resumo_mun = (
            df.groupby(["codigo_ibge", "municipio"], dropna=False)
            .agg(
                territorios=("bairro_ou_localidade", "count"),
                com_coordenadas=("coord_valida_mt", lambda s: int(pd.Series(s).astype(bool).sum())),
                populacao_referencia=("populacao", "sum"),
                territorios_com_nome_validado=("nome_territorio_validado", lambda s: int(pd.Series(s).astype(bool).sum())),
            )
            .reset_index()
        )
        resumo_mun["percentual_georreferenciado"] = (
            resumo_mun["com_coordenadas"] / resumo_mun["territorios"].replace(0, pd.NA) * 100
        ).fillna(0).round(1)
        resumo_mun = resumo_mun.sort_values(["territorios", "populacao_referencia"], ascending=[False, False])
    return {"diagnostico": diag, "territorios": df, "resumo_municipal": resumo_mun}


def _classificar_distancia_bairro_aps(dist_km: Any) -> str:
    """Classificação preliminar para setores/bairros/localidades até APS.

    Os limites são mais rígidos que assentamentos, pois a leitura pode envolver
    áreas urbanas/periurbanas. Mantém-se como triagem preliminar, não como regra
    normativa de cobertura.
    """
    try:
        d = float(dist_km)
    except Exception:
        return "Sem cálculo"
    if d <= 1.5:
        return "Próximo"
    if d <= 3:
        return "Atenção"
    if d <= 5:
        return "Distante"
    return "Crítico"



def carregar_ajustes_territoriais_manuais() -> pd.DataFrame:
    """Carrega ajustes manuais de município por setor/localidade.

    Arquivo esperado: data/reference/ajustes_territorios_municipio.csv

    Colunas aceitas:
    - setor_censitario
    - municipio_original
    - municipio_validado
    - codigo_ibge_validado
    - motivo_ajuste
    - validado_por
    - status_validacao
    """
    arq = AJUSTES_TERRITORIAIS_MANUAIS
    if not arq.exists():
        return pd.DataFrame(columns=[
            "setor_censitario", "municipio_original", "municipio_validado",
            "codigo_ibge_validado", "motivo_ajuste", "validado_por", "status_validacao"
        ])
    try:
        df = pd.read_csv(arq, dtype=str, encoding="utf-8-sig").fillna("")
    except Exception:
        try:
            df = pd.read_csv(arq, dtype=str, sep=";", encoding="utf-8-sig").fillna("")
        except Exception:
            return pd.DataFrame()
    for c in ["setor_censitario", "municipio_original", "municipio_validado", "codigo_ibge_validado", "motivo_ajuste", "validado_por", "status_validacao"]:
        if c not in df.columns:
            df[c] = ""
    df["setor_censitario"] = df["setor_censitario"].astype(str).str.strip()
    df = df[df["setor_censitario"].ne("")].copy()
    return df


def aplicar_ajustes_territoriais_manuais(territorios: pd.DataFrame) -> pd.DataFrame:
    """Aplica município validado manualmente em setores/localidades.

    A coluna original permanece registrada. O cálculo passa a usar o município
    validado quando houver ajuste aprovado/registrado.
    """
    if territorios is None or territorios.empty:
        return territorios
    ajustes = carregar_ajustes_territoriais_manuais()
    out = territorios.copy()
    out["municipio_original_base"] = out.get("municipio", "").astype(str)
    out["codigo_ibge_original_base"] = out.get("codigo_ibge", "").astype(str)
    out["municipio_validado_para_analise"] = out.get("municipio", "").astype(str)
    out["codigo_ibge_validado_para_analise"] = out.get("codigo_ibge", "").astype(str)
    out["ajuste_manual_municipio"] = False
    out["motivo_ajuste_manual"] = ""
    out["status_validacao_territorial"] = "Base original — não validado manualmente"

    if ajustes.empty or "setor_censitario" not in out.columns:
        return out

    aux = ajustes[[
        "setor_censitario", "municipio_validado", "codigo_ibge_validado",
        "motivo_ajuste", "validado_por", "status_validacao"
    ]].copy()
    aux = aux.drop_duplicates("setor_censitario", keep="last")
    out = out.merge(aux, on="setor_censitario", how="left", suffixes=("", "_ajuste"))
    mask = out["municipio_validado"].astype(str).str.strip().ne("")
    out.loc[mask, "municipio_validado_para_analise"] = out.loc[mask, "municipio_validado"].astype(str).str.strip()
    out.loc[mask & out["codigo_ibge_validado"].astype(str).str.strip().ne(""), "codigo_ibge_validado_para_analise"] = out.loc[mask & out["codigo_ibge_validado"].astype(str).str.strip().ne(""), "codigo_ibge_validado"].astype(str).str.strip()
    out.loc[mask, "municipio"] = out.loc[mask, "municipio_validado_para_analise"]
    out.loc[mask, "codigo_ibge"] = out.loc[mask, "codigo_ibge_validado_para_analise"]
    out.loc[mask, "ajuste_manual_municipio"] = True
    out.loc[mask, "motivo_ajuste_manual"] = out.loc[mask, "motivo_ajuste"].astype(str)
    out.loc[mask, "status_validacao_territorial"] = (
        "Município ajustado manualmente para análise — "
        + out.loc[mask, "municipio_original_base"].astype(str)
        + " → "
        + out.loc[mask, "municipio_validado_para_analise"].astype(str)
    )
    return out.drop(columns=["municipio_validado", "codigo_ibge_validado", "motivo_ajuste", "validado_por", "status_validacao"], errors="ignore")


def gerar_modelo_ajustes_territoriais() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "setor_censitario": "510340310000026",
            "municipio_original": "Cuiabá",
            "municipio_validado": "Santo Antônio de Leverger",
            "codigo_ibge_validado": "5107800",
            "motivo_ajuste": "Validação local: setor/ponto localizado em área territorial reconhecida como Santo Antônio do Leverger.",
            "validado_por": "Equipe APS/ERS/município",
            "status_validacao": "validado",
        }
    ])


def diagnosticar_territorios_suspeitos_divisa(limite_km_alerta: float = 20.0) -> pd.DataFrame:
    """Lista territórios onde a referência municipal é muito mais distante que a física.

    Esses casos não significam erro automaticamente, mas são prioridade para
    validação territorial local, pois podem indicar setor de divisa, município
    textual impreciso ou necessidade de ajuste manual.
    """
    res = calcular_distancias_bairros_localidades_aps()
    df = res.get("distancias", pd.DataFrame()).copy()
    if df.empty:
        return pd.DataFrame()
    df["diferenca_km_para_ubs_municipal"] = pd.to_numeric(df.get("diferenca_km_para_ubs_municipal"), errors="coerce")
    suspeitos = df[
        (df.get("referencia_fora_municipio", pd.Series(False, index=df.index)).astype(bool))
        & (df["diferenca_km_para_ubs_municipal"].fillna(0) >= float(limite_km_alerta))
    ].copy()
    cols = [c for c in [
        "setor_censitario", "municipio_original_base", "municipio", "territorio_exibicao",
        "populacao", "latitude", "longitude", "ubs_fisicamente_mais_proxima",
        "municipio_ubs_fisicamente_mais_proxima", "distancia_ubs_fisica_km",
        "ubs_municipal_mais_proxima", "distancia_ubs_municipal_km",
        "diferenca_km_para_ubs_municipal", "ajuste_manual_municipio",
        "status_validacao_territorial", "motivo_ajuste_manual"
    ] if c in suspeitos.columns]
    if not cols:
        return suspeitos
    return suspeitos[cols].sort_values("diferenca_km_para_ubs_municipal", ascending=False).reset_index(drop=True)


def calcular_distancias_bairros_localidades_aps() -> dict:
    """Calcula distância real em linha reta entre setores/localidades e UBS/APS elegível.

    Usa apenas unidades APS elegíveis por CNES vinculado a equipes APS/INE e
    coordenadas válidas em MT. Não usa centroide municipal, geocodificação por
    endereço nem coordenada aproximada.
    """
    fonte = _carregar_bairros_localidades_referencia()
    territorios = fonte[fonte.get("coord_valida_mt", False).astype(bool)].copy() if not fonte.empty else pd.DataFrame()
    territorios = aplicar_ajustes_territoriais_manuais(territorios)
    ubs = _preparar_unidades_aps_georreferenciadas()

    diag = {
        "territorios_base": int(len(fonte)),
        "territorios_com_coordenadas": int(len(territorios)),
        "ubs_aps_georreferenciadas": int(len(ubs)),
        "referencias_usadas": int(len(ubs)),
        "metodo": "distância geodésica em linha reta entre centroide do setor/localidade e UBS/APS elegível mais próxima",
        "aproximacao": "não usa centroide municipal, não geocodifica endereço e não inventa coordenada",
    }
    vazio = {
        "diagnostico": diag,
        "distancias": pd.DataFrame(),
        "resumo_municipal": pd.DataFrame(),
        "resumo_regional": pd.DataFrame(),
        "ubs_mais_demandadas": pd.DataFrame(),
    }
    if territorios.empty or ubs.empty:
        return vazio

    # Prepara arrays para cálculo vetorizado.
    lat_u = pd.to_numeric(ubs["lat_ubs"], errors="coerce").to_numpy(dtype=float)
    lon_u = pd.to_numeric(ubs["lon_ubs"], errors="coerce").to_numpy(dtype=float)
    valid_u = np.isfinite(lat_u) & np.isfinite(lon_u)
    ubs = ubs.loc[valid_u].reset_index(drop=True)
    lat_u = lat_u[valid_u]
    lon_u = lon_u[valid_u]
    if len(ubs) == 0:
        return vazio

    # Regra institucional revisada:
    # calcula duas referências:
    # 1) UBS/APS fisicamente mais próxima no território estadual;
    # 2) UBS/APS mais próxima dentro do município textual, quando existir.
    #
    # O mapa principal usa a referência física mais próxima, porque a pergunta visual
    # é "qual unidade está mais perto?". A referência municipal permanece registrada
    # para auditoria e análise de governança, pois a rede APS é organizada por município.
    ubs["_chave_municipio_ref"] = ubs.get("municipio", "").map(_chave).map(_normalizar_alias_chave)
    ubs["_codigo_ibge_ref"] = ubs.get("codigo_ibge", "").astype(str).str.replace(r"\D", "", regex=True).str[:7]
    indices_por_chave: dict[str, np.ndarray] = {}
    for chave_ref, idxs in ubs.groupby("_chave_municipio_ref").groups.items():
        indices_por_chave[str(chave_ref)] = np.array(list(idxs), dtype=int)
    indices_por_codigo: dict[str, np.ndarray] = {}
    for cod_ref, idxs in ubs.groupby("_codigo_ibge_ref").groups.items():
        if str(cod_ref).strip():
            indices_por_codigo[str(cod_ref)] = np.array(list(idxs), dtype=int)

    registros = []
    r = 6371.0088
    lat_u_rad_all = np.radians(lat_u)
    lon_u_rad_all = np.radians(lon_u)
    todos_idx = np.arange(len(ubs), dtype=int)

    for _, row in territorios.iterrows():
        lat = float(row.get("latitude"))
        lon = float(row.get("longitude"))
        lat1 = np.radians(lat)
        lon1 = np.radians(lon)

        # 1) UBS/APS fisicamente mais próxima, sem restringir município.
        dlat_all = lat_u_rad_all - lat1
        dlon_all = lon_u_rad_all - lon1
        a_all = np.sin(dlat_all / 2) ** 2 + np.cos(lat1) * np.cos(lat_u_rad_all) * np.sin(dlon_all / 2) ** 2
        dist_all = 2 * r * np.arcsin(np.sqrt(a_all))
        idx_global = int(np.nanargmin(dist_all))
        menor_global = float(dist_all[idx_global])
        u_global = ubs.iloc[idx_global]

        # 2) UBS/APS mais próxima dentro do município textual, quando houver.
        cod_terr = str(row.get("codigo_ibge", "") or "").replace(".", "")
        cod_terr = "".join(ch for ch in cod_terr if ch.isdigit())[:7]
        chave_terr = _normalizar_alias_chave(_chave(row.get("municipio", "")))
        candidatos_idx = indices_por_codigo.get(cod_terr)
        if candidatos_idx is None or len(candidatos_idx) == 0:
            candidatos_idx = indices_por_chave.get(chave_terr)

        u_municipal = None
        menor_municipal = np.nan
        tem_referencia_municipal = candidatos_idx is not None and len(candidatos_idx) > 0
        if tem_referencia_municipal:
            lat_u_rad_mun = lat_u_rad_all[candidatos_idx]
            lon_u_rad_mun = lon_u_rad_all[candidatos_idx]
            dlat_mun = lat_u_rad_mun - lat1
            dlon_mun = lon_u_rad_mun - lon1
            a_mun = np.sin(dlat_mun / 2) ** 2 + np.cos(lat1) * np.cos(lat_u_rad_mun) * np.sin(dlon_mun / 2) ** 2
            dist_mun = 2 * r * np.arcsin(np.sqrt(a_mun))
            pos_mun = int(np.nanargmin(dist_mun))
            idx_mun = int(candidatos_idx[pos_mun])
            menor_municipal = float(dist_mun[pos_mun])
            u_municipal = ubs.iloc[idx_mun]

        referencia_fora_municipio = _chave(u_global.get("municipio", "")) != _chave(row.get("municipio", ""))
        diferenca_para_referencia_municipal = None
        if tem_referencia_municipal and np.isfinite(menor_municipal):
            diferenca_para_referencia_municipal = round(float(menor_municipal - menor_global), 2)

        registros.append({
            "setor_censitario": row.get("setor_censitario", ""),
            "municipio": row.get("municipio", ""),
            "codigo_ibge": row.get("codigo_ibge", ""),
            "municipio_original_base": row.get("municipio_original_base", row.get("municipio", "")),
            "codigo_ibge_original_base": row.get("codigo_ibge_original_base", row.get("codigo_ibge", "")),
            "municipio_validado_para_analise": row.get("municipio_validado_para_analise", row.get("municipio", "")),
            "codigo_ibge_validado_para_analise": row.get("codigo_ibge_validado_para_analise", row.get("codigo_ibge", "")),
            "ajuste_manual_municipio": bool(row.get("ajuste_manual_municipio", False)),
            "status_validacao_territorial": row.get("status_validacao_territorial", "Base original — não validado manualmente"),
            "motivo_ajuste_manual": row.get("motivo_ajuste_manual", ""),
            "bairro_ou_localidade": row.get("bairro_ou_localidade", ""),
            "territorio_exibicao": row.get("nome_territorio_exibicao", row.get("bairro_ou_localidade", "")),
            "bairro_ou_localidade_original": row.get("bairro_ou_localidade_original", row.get("bairro_ou_localidade", "")),
            "fonte_nome_territorio": row.get("fonte_nome_territorio", ""),
            "nome_territorio_validado": row.get("nome_territorio_validado", False),
            "nome_territorio_ibge_original": row.get("nome_territorio_ibge_original", ""),
            "alerta_nome_territorio": row.get("alerta_nome_territorio", ""),
            "tipo_territorio": row.get("tipo_territorio", ""),
            "populacao": row.get("populacao", 0),
            "latitude": lat,
            "longitude": lon,

            # Referência principal do mapa: proximidade física.
            "cnes_ubs_mais_proxima": u_global.get("cnes", ""),
            "ubs_mais_proxima": u_global.get("ubs_nome", ""),
            "municipio_ubs_mais_proxima": u_global.get("municipio", ""),
            "lat_ubs": float(u_global.get("lat_ubs")),
            "lon_ubs": float(u_global.get("lon_ubs")),
            "distancia_ubs_mais_proxima_km": round(menor_global, 2),

            # Referência municipal para auditoria.
            "cnes_ubs_municipal_mais_proxima": u_municipal.get("cnes", "") if u_municipal is not None else "",
            "ubs_municipal_mais_proxima": u_municipal.get("ubs_nome", "") if u_municipal is not None else "",
            "municipio_ubs_municipal": u_municipal.get("municipio", "") if u_municipal is not None else "",
            "lat_ubs_municipal": float(u_municipal.get("lat_ubs")) if u_municipal is not None and pd.notna(u_municipal.get("lat_ubs")) else np.nan,
            "lon_ubs_municipal": float(u_municipal.get("lon_ubs")) if u_municipal is not None and pd.notna(u_municipal.get("lon_ubs")) else np.nan,
            "distancia_ubs_municipal_km": round(float(menor_municipal), 2) if tem_referencia_municipal and np.isfinite(menor_municipal) else np.nan,
            "classe_distancia_aps_municipal": _classificar_distancia_bairro_aps(menor_municipal) if tem_referencia_municipal and np.isfinite(menor_municipal) else "Sem referência municipal",
            "diferenca_km_para_ubs_municipal": diferenca_para_referencia_municipal,

            "classe_distancia_aps": _classificar_distancia_bairro_aps(menor_global),
            "qtd_equipes_aps_ubs": u_global.get("qtd_equipes_aps", 0),
            "tipos_equipes_aps_ubs": u_global.get("tipos_equipes_aps", ""),
            "referencia_fora_municipio": bool(referencia_fora_municipio),
            "metodo_calculo": "Haversine/linha reta; referência principal = UBS/APS fisicamente mais próxima; referência municipal mantida em campos próprios para auditoria",
        })
    distancias = pd.DataFrame(registros)
    if distancias.empty:
        return vazio

    # Controle de qualidade territorial:
    # remove do cálculo principal pontos cuja coordenada cai fora da malha do município textual.
    # Esses registros não são apagados; ficam disponíveis como inconsistências para validação.
    distancias = validar_municipio_geografico_pontos(
        distancias,
        lat_col="latitude",
        lon_col="longitude",
        municipio_col="municipio",
        codigo_col="codigo_ibge",
    )
    diag_geo = diagnosticar_qualidade_geografica_territorios(distancias)
    inconsistencias_geo = distancias[~distancias.get("registro_utilizado_no_calculo", True).astype(bool)].copy() if "registro_utilizado_no_calculo" in distancias.columns else pd.DataFrame()
    distancias = distancias[distancias.get("registro_utilizado_no_calculo", True).astype(bool)].copy() if "registro_utilizado_no_calculo" in distancias.columns else distancias

    if distancias.empty:
        diag.update(diag_geo)
        return {
            "diagnostico": diag,
            "distancias": pd.DataFrame(),
            "resumo_municipal": pd.DataFrame(),
            "resumo_regional": pd.DataFrame(),
            "ubs_mais_demandadas": pd.DataFrame(),
            "inconsistencias_geograficas": inconsistencias_geo.reset_index(drop=True),
        }

    distancias["distancia_ubs_mais_proxima_km"] = pd.to_numeric(distancias["distancia_ubs_mais_proxima_km"], errors="coerce")
    distancias["populacao"] = pd.to_numeric(distancias.get("populacao"), errors="coerce").fillna(0)
    distancias["populacao_ponderada_distancia"] = distancias["populacao"] * distancias["distancia_ubs_mais_proxima_km"].fillna(0)

    # Região de saúde a partir da base municipal consolidada, se disponível.
    base = montar_base_mapa_municipal()
    if not base.empty and {"municipio", "regiao_saude"}.issubset(base.columns):
        mapa_reg = base[["municipio", "regiao_saude"]].drop_duplicates()
        distancias = distancias.merge(mapa_reg, on="municipio", how="left")
    else:
        distancias["regiao_saude"] = "Não informada"

    resumo_mun = (
        distancias.groupby(["codigo_ibge", "municipio", "regiao_saude"], dropna=False)
        .agg(
            territorios=("bairro_ou_localidade", "count"),
            populacao_referencia=("populacao", "sum"),
            distancia_media_km=("distancia_ubs_mais_proxima_km", "mean"),
            distancia_mediana_km=("distancia_ubs_mais_proxima_km", "median"),
            distancia_maxima_km=("distancia_ubs_mais_proxima_km", "max"),
            criticos=("classe_distancia_aps", lambda s: int((s == "Crítico").sum())),
            distantes=("classe_distancia_aps", lambda s: int((s == "Distante").sum())),
            atencao=("classe_distancia_aps", lambda s: int((s == "Atenção").sum())),
            proximos=("classe_distancia_aps", lambda s: int((s == "Próximo").sum())),
            populacao_em_territorios_criticos=("populacao", lambda s: 0),
        )
        .reset_index()
    )
    # Calcula população em faixas após o groupby para evitar lambda dependente de outra coluna.
    pop_faixa = (
        distancias.assign(
            pop_critica=np.where(distancias["classe_distancia_aps"].eq("Crítico"), distancias["populacao"], 0),
            pop_distante=np.where(distancias["classe_distancia_aps"].eq("Distante"), distancias["populacao"], 0),
        )
        .groupby(["codigo_ibge", "municipio"], dropna=False)
        .agg(populacao_critica=("pop_critica", "sum"), populacao_distante=("pop_distante", "sum"))
        .reset_index()
    )
    resumo_mun = resumo_mun.drop(columns=["populacao_em_territorios_criticos"], errors="ignore").merge(pop_faixa, on=["codigo_ibge", "municipio"], how="left")
    for c in ["distancia_media_km", "distancia_mediana_km", "distancia_maxima_km"]:
        resumo_mun[c] = pd.to_numeric(resumo_mun[c], errors="coerce").round(2)
    resumo_mun["criticos_distantes"] = resumo_mun["criticos"] + resumo_mun["distantes"]
    resumo_mun["percentual_critico_distante"] = (resumo_mun["criticos_distantes"] / resumo_mun["territorios"].replace(0, pd.NA) * 100).fillna(0).round(1)
    resumo_mun = resumo_mun.sort_values(["criticos", "distantes", "distancia_maxima_km", "populacao_critica"], ascending=[False, False, False, False])

    resumo_reg = pd.DataFrame()
    if "regiao_saude" in distancias.columns:
        resumo_reg = (
            resumo_mun.groupby("regiao_saude", dropna=False)
            .agg(
                municipios=("municipio", "nunique"),
                territorios=("territorios", "sum"),
                populacao_referencia=("populacao_referencia", "sum"),
                criticos=("criticos", "sum"),
                distantes=("distantes", "sum"),
                distancia_media_km=("distancia_media_km", "mean"),
                distancia_maxima_km=("distancia_maxima_km", "max"),
                populacao_critica=("populacao_critica", "sum"),
            )
            .reset_index()
        )
        resumo_reg["distancia_media_km"] = pd.to_numeric(resumo_reg["distancia_media_km"], errors="coerce").round(2)
        resumo_reg = resumo_reg.sort_values(["criticos", "distantes", "distancia_maxima_km"], ascending=[False, False, False])

    ubs_dem = pd.DataFrame()
    if {"cnes_ubs_mais_proxima", "ubs_mais_proxima"}.issubset(distancias.columns):
        ubs_dem = (
            distancias.groupby(["cnes_ubs_mais_proxima", "ubs_mais_proxima", "municipio_ubs_mais_proxima"], dropna=False)
            .agg(
                territorios_referenciados=("bairro_ou_localidade", "count"),
                populacao_referenciada=("populacao", "sum"),
                distancia_media_km=("distancia_ubs_mais_proxima_km", "mean"),
                distancia_maxima_km=("distancia_ubs_mais_proxima_km", "max"),
                criticos=("classe_distancia_aps", lambda s: int((s == "Crítico").sum())),
                distantes=("classe_distancia_aps", lambda s: int((s == "Distante").sum())),
            )
            .reset_index()
        )
        ubs_dem["distancia_media_km"] = ubs_dem["distancia_media_km"].round(2)
        ubs_dem["distancia_maxima_km"] = ubs_dem["distancia_maxima_km"].round(2)
        ubs_dem = ubs_dem.sort_values(["territorios_referenciados", "populacao_referenciada", "criticos"], ascending=[False, False, False])

    total = int(len(distancias))
    criticos = int((distancias["classe_distancia_aps"] == "Crítico").sum())
    distantes = int((distancias["classe_distancia_aps"] == "Distante").sum())
    diag.update(diag_geo)
    diag["territorios_excluidos_divergencia_geografica"] = int(len(inconsistencias_geo)) if "inconsistencias_geo" in locals() else 0
    diag.update({
        "territorios_analisados": total,
        "municipios_cobertos": int(distancias["municipio"].nunique()),
        "distancia_media_km": round(float(distancias["distancia_ubs_mais_proxima_km"].mean()), 2),
        "distancia_mediana_km": round(float(distancias["distancia_ubs_mais_proxima_km"].median()), 2),
        "distancia_maxima_km": round(float(distancias["distancia_ubs_mais_proxima_km"].max()), 2),
        "criticos": criticos,
        "distantes": distantes,
        "percentual_critico_distante": round(((criticos + distantes) / total * 100), 1) if total else 0.0,
        "populacao_referencia_total": int(distancias["populacao"].sum()),
        "populacao_critica": int(distancias.loc[distancias["classe_distancia_aps"].eq("Crítico"), "populacao"].sum()),
        "populacao_distante": int(distancias.loc[distancias["classe_distancia_aps"].eq("Distante"), "populacao"].sum()),
    })

    return {
        "diagnostico": diag,
        "distancias": distancias.sort_values("distancia_ubs_mais_proxima_km", ascending=False).reset_index(drop=True),
        "resumo_municipal": resumo_mun.reset_index(drop=True),
        "resumo_regional": resumo_reg.reset_index(drop=True),
        "ubs_mais_demandadas": ubs_dem.reset_index(drop=True),
        "inconsistencias_geograficas": inconsistencias_geo.reset_index(drop=True) if "inconsistencias_geo" in locals() else pd.DataFrame(),
    }



def montar_painel_vazios_intramunicipais() -> dict:
    """Monta visão executiva dos vazios intramunicipais a partir da distância território -> UBS/APS.

    Esta função não cria novas coordenadas. Usa exclusivamente a camada de bairros/localidades/setores
    e as unidades APS elegíveis já georreferenciadas.
    """
    base = calcular_distancias_bairros_localidades_aps()
    dist = base.get("distancias", pd.DataFrame()).copy()
    mun = base.get("resumo_municipal", pd.DataFrame()).copy()
    reg = base.get("resumo_regional", pd.DataFrame()).copy()
    ubs = base.get("ubs_mais_demandadas", pd.DataFrame()).copy()
    diag = dict(base.get("diagnostico", {}))

    vazio = {
        "diagnostico": diag,
        "distancias": dist,
        "ranking_municipal": mun,
        "ranking_regional": reg,
        "ubs_referencia": ubs,
        "matriz_executiva": pd.DataFrame(),
        "territorios_criticos": pd.DataFrame(),
        "territorios_distantes": pd.DataFrame(),
        "populacao_exposta": pd.DataFrame(),
    }
    if dist.empty:
        return vazio

    for col in ["distancia_ubs_mais_proxima_km", "populacao"]:
        if col in dist.columns:
            dist[col] = pd.to_numeric(dist[col], errors="coerce").fillna(0)
    if not mun.empty:
        for col in ["territorios", "populacao_referencia", "criticos", "distantes", "percentual_critico_distante", "distancia_media_km", "distancia_maxima_km", "populacao_critica", "populacao_distante"]:
            if col in mun.columns:
                mun[col] = pd.to_numeric(mun[col], errors="coerce").fillna(0)

        def _classificar_alerta(row):
            crit = float(row.get("criticos", 0) or 0)
            distantes = float(row.get("distantes", 0) or 0)
            pct = float(row.get("percentual_critico_distante", 0) or 0)
            maxd = float(row.get("distancia_maxima_km", 0) or 0)
            if crit >= 10 or pct >= 35 or maxd >= 20:
                return "Muito alto"
            if crit >= 5 or pct >= 20 or maxd >= 12:
                return "Alto"
            if crit >= 1 or distantes >= 5 or pct >= 10:
                return "Médio"
            return "Monitoramento"

        def _encaminhar(row):
            alerta = str(row.get("alerta_intramunicipal", ""))
            crit = int(row.get("criticos", 0) or 0)
            distantes = int(row.get("distantes", 0) or 0)
            if alerta == "Muito alto":
                return "Priorizar validação territorial com o município, revisar adscrição/fluxo de referência e avaliar estratégia de acesso para territórios mais distantes."
            if alerta == "Alto":
                return "Analisar bairros/localidades críticos, conferir referência real das equipes APS e verificar necessidade de reorganização territorial."
            if crit or distantes:
                return "Monitorar territórios em faixa distante/crítica e validar barreiras locais de deslocamento."
            return "Manter acompanhamento como referência territorial municipal."

        mun["alerta_intramunicipal"] = mun.apply(_classificar_alerta, axis=1)
        mun["populacao_critica_distante"] = mun.get("populacao_critica", 0) + mun.get("populacao_distante", 0)
        mun["encaminhamento_sugerido"] = mun.apply(_encaminhar, axis=1)
        mun = mun.sort_values(["alerta_intramunicipal", "criticos", "distantes", "distancia_maxima_km", "populacao_critica_distante"], ascending=[True, False, False, False, False])
        ordem = {"Muito alto": 0, "Alto": 1, "Médio": 2, "Monitoramento": 3}
        mun["_ordem_alerta"] = mun["alerta_intramunicipal"].map(ordem).fillna(9)
        mun = mun.sort_values(["_ordem_alerta", "criticos", "distantes", "distancia_maxima_km", "populacao_critica_distante"], ascending=[True, False, False, False, False]).drop(columns=["_ordem_alerta"], errors="ignore")

    criticos = dist[dist.get("classe_distancia_aps", "").astype(str).eq("Crítico")].copy()
    distantes = dist[dist.get("classe_distancia_aps", "").astype(str).eq("Distante")].copy()

    pop_exposta = pd.DataFrame()
    if not dist.empty and "classe_distancia_aps" in dist.columns:
        pop_exposta = (
            dist.groupby("classe_distancia_aps", dropna=False)
            .agg(territorios=("bairro_ou_localidade", "count"), populacao_referencia=("populacao", "sum"), distancia_media_km=("distancia_ubs_mais_proxima_km", "mean"))
            .reset_index()
        )
        pop_exposta["distancia_media_km"] = pd.to_numeric(pop_exposta["distancia_media_km"], errors="coerce").round(2)
        ordem = {"Crítico": 0, "Distante": 1, "Atenção": 2, "Próximo": 3}
        pop_exposta["_ordem"] = pop_exposta["classe_distancia_aps"].map(ordem).fillna(9)
        pop_exposta = pop_exposta.sort_values("_ordem").drop(columns=["_ordem"], errors="ignore")

    diag.update({
        "municipios_muito_alto": int((mun.get("alerta_intramunicipal", pd.Series(dtype=str)) == "Muito alto").sum()) if not mun.empty else 0,
        "municipios_alto": int((mun.get("alerta_intramunicipal", pd.Series(dtype=str)) == "Alto").sum()) if not mun.empty else 0,
        "territorios_criticos": int(len(criticos)),
        "territorios_distantes": int(len(distantes)),
        "populacao_critica_distante": int(dist.loc[dist.get("classe_distancia_aps", "").astype(str).isin(["Crítico", "Distante"]), "populacao"].sum()) if "populacao" in dist.columns else 0,
    })

    return {
        "diagnostico": diag,
        "distancias": dist,
        "ranking_municipal": mun.reset_index(drop=True) if not mun.empty else mun,
        "ranking_regional": reg,
        "ubs_referencia": ubs,
        "matriz_executiva": mun.reset_index(drop=True) if not mun.empty else mun,
        "territorios_criticos": criticos.sort_values("distancia_ubs_mais_proxima_km", ascending=False).reset_index(drop=True) if not criticos.empty else criticos,
        "territorios_distantes": distantes.sort_values("distancia_ubs_mais_proxima_km", ascending=False).reset_index(drop=True) if not distantes.empty else distantes,
        "populacao_exposta": pop_exposta,
    }


# =============================================================================
# Determinantes sociais + vazios assistenciais — educação, renda e rede escolar
# =============================================================================

DETERMINANTES_SOCIAIS_COLUNAS = [
    "taxa_alfabetizacao", "taxa_alfabetizacao_pct", "taxa_analfabetismo_estimado_pct",
    "nivel_instrucao", "renda_censo_2022", "saneamento_censo_2022",
    "abastecimento_agua_rede_pct", "esgotamento_rede_pct", "lixo_coletado_pct",
    "escolas_total", "escolas_urbanas", "escolas_rurais", "escolas_indigenas", "escolas_quilombolas",
    "escolas_educacao_especial_aee", "matriculas_total", "matriculas_educacao_especial",
    "populacao_indigena", "populacao_quilombola", "qtd_terras_indigenas_intersecoes", "qtd_assentamentos",
]


def _serie_numerica(df: pd.DataFrame, col: str, default: float | None = None) -> pd.Series:
    if df is None or df.empty or col not in df.columns:
        return pd.Series(default, index=df.index if isinstance(df, pd.DataFrame) else [])
    return pd.to_numeric(df[col], errors="coerce")


def _primeira_coluna_valida(df: pd.DataFrame, candidatos: list[str]) -> pd.Series:
    out = pd.Series(pd.NA, index=df.index)
    for col in candidatos:
        if col in df.columns:
            out = out.combine_first(pd.to_numeric(df[col], errors="coerce"))
    return out


def _score_percentil(s: pd.Series, maior_pior: bool = True) -> pd.Series:
    v = pd.to_numeric(s, errors="coerce")
    if v.notna().sum() < 2:
        return pd.Series(0.0, index=s.index)
    ranks = v.rank(pct=True) * 100.0
    if not maior_pior:
        ranks = 100.0 - ranks
    return ranks.fillna(0.0)


# -----------------------------------------------------------------------------
# Fallback/ponte de dados sociais já importados
# -----------------------------------------------------------------------------
# Na prática, a base_municipal_consolidada pode estar com colunas sociais vazias
# mesmo quando os dados já existem em indicadores_municipais ou no cache do
# sistema antigo. Estas funções fazem a ponte sem inventar dados: primeiro leem
# indicadores estruturados no banco; depois, se necessário, usam o CSV de
# referência recuperado do sistema anterior.

DETERMINANTES_SOCIAIS_REFERENCIA_PATHS = [
    Path("data/reference/determinantes_sociais_sistema_antigo.csv"),
    Path(__file__).resolve().parents[1] / "data" / "reference" / "determinantes_sociais_sistema_antigo.csv",
]


def _normalizar_municipio_para_merge(valor: Any) -> str:
    texto = str(valor or "").replace("- MT", "").replace("/MT", "").strip()
    return _normalizar_alias_chave(_chave(texto))


def _preencher_coluna_vazia(out: pd.DataFrame, coluna: str, origem: str) -> pd.DataFrame:
    if origem not in out.columns:
        return out
    if coluna not in out.columns:
        out[coluna] = pd.NA
    vazio = out[coluna].isna() | out[coluna].astype(str).str.strip().isin(["", "None", "nan", "NaN", "<NA>"])
    out.loc[vazio, coluna] = out.loc[vazio, origem]
    return out


def _pivot_indicadores_municipais_para_determinantes() -> pd.DataFrame:
    try:
        conn = get_connection()
        ind = pd.read_sql_query(
            """
            SELECT municipio, ano, indicador, valor
            FROM indicadores_municipais
            WHERE indicador IS NOT NULL
            """,
            conn,
        )
    except Exception:
        return pd.DataFrame()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if ind.empty or not {"municipio", "indicador", "valor"}.issubset(ind.columns):
        return pd.DataFrame()

    ind = ind.copy()
    ind["municipio_chave"] = ind["municipio"].map(_normalizar_municipio_para_merge)
    ind["valor"] = pd.to_numeric(ind["valor"], errors="coerce")
    ind["ano"] = pd.to_numeric(ind.get("ano"), errors="coerce")
    ind = ind[ind["municipio_chave"].astype(str).str.len() > 0].copy()

    # Mantém apenas indicadores úteis para esta aba. Indicadores repetidos com
    # mesmo valor são consolidados por média; quando houver anos diferentes,
    # o valor mais recente tende a predominar por indicador/município.
    candidatos = {
        "taxa_alfabetizacao_pct",
        "taxa_analfabetismo_estimado_pct",
        "nivel_instrucao_baixo_pct",
        "nivel_instrucao_medio_ou_superior_pct",
        "nivel_instrucao_superior_completo_pct",
        "renda_censo_2022",
        "abastecimento_agua_rede_pct",
        "esgotamento_rede_pct",
        "esgotamento_adequado_rede_ou_fossa_pct",
        "lixo_coletado_pct",
        "pessoas_indigenas_2022",
        "pessoas_quilombolas_2022",
        "escolas_total",
        "escolas_urbanas",
        "escolas_rurais",
        "escolas_indigenas",
        "escolas_quilombolas",
        "escolas_educacao_especial_aee",
        "matriculas_total",
        "matriculas_educacao_especial",
        "pib_municipal_precos_correntes",
        "pib_per_capita",
    }
    ind = ind[ind["indicador"].astype(str).isin(candidatos)].copy()
    if ind.empty:
        return pd.DataFrame()

    ind = ind.sort_values(["municipio_chave", "indicador", "ano"], na_position="first")

    # Compatibilidade com versões recentes do pandas:
    # em algumas versões, groupby.apply pode retirar as colunas de agrupamento
    # do DataFrame passado para a função. Por isso, a agregação abaixo evita
    # depender de grupo["indicador"] e monta explicitamente a tabela final.
    linhas_agregadas = []
    for (municipio_chave, indicador), grupo in ind.groupby(["municipio_chave", "indicador"], dropna=False):
        indicador = str(indicador)
        vals = pd.to_numeric(grupo.get("valor"), errors="coerce").dropna()
        if vals.empty:
            valor_agregado = np.nan
        elif indicador in {"pessoas_indigenas_2022", "pessoas_quilombolas_2022"}:
            # Os conectores antigos de povos tradicionais podem gravar mais de uma
            # linha para o mesmo município, incluindo totalizações multiplicadas.
            # Para população indígena/quilombola, o menor valor não nulo/não negativo
            # é o mais conservador e evita inflar artificialmente o indicador.
            vals = vals[vals >= 0]
            valor_agregado = float(vals.min()) if not vals.empty else np.nan
        else:
            valor_agregado = float(vals.mean())

        linhas_agregadas.append({
            "municipio_chave": municipio_chave,
            "indicador": indicador,
            "valor": valor_agregado,
        })

    agregado = pd.DataFrame(linhas_agregadas)
    if agregado.empty or not {"municipio_chave", "indicador", "valor"}.issubset(agregado.columns):
        return pd.DataFrame()

    piv = agregado.pivot(index="municipio_chave", columns="indicador", values="valor").reset_index()
    piv.columns = [str(c) for c in piv.columns]
    return piv


def _ler_determinantes_referencia_antiga() -> pd.DataFrame:
    for caminho in DETERMINANTES_SOCIAIS_REFERENCIA_PATHS:
        try:
            if caminho.exists():
                df = pd.read_csv(caminho)
                if df.empty:
                    continue
                if "municipio" in df.columns:
                    df["municipio_chave"] = df["municipio"].map(_normalizar_municipio_para_merge)
                elif "codigo_ibge" in df.columns:
                    df["codigo_ibge"] = df["codigo_ibge"].map(_codigo_limpo)
                return df
        except Exception:
            continue
    return pd.DataFrame()


def enriquecer_base_com_determinantes_importados(base: pd.DataFrame) -> pd.DataFrame:
    """Preenche campos sociais do mapa a partir do banco e do cache validável.

    Prioridade:
    1) valores já existentes na base municipal;
    2) indicadores_municipais importados por APIs/blocos;
    3) data/reference/determinantes_sociais_sistema_antigo.csv.
    """
    if base is None or base.empty:
        return pd.DataFrame() if base is None else base

    out = base.copy()
    if "municipio" in out.columns:
        out["municipio_chave"] = out["municipio"].map(_normalizar_municipio_para_merge)
    if "codigo_ibge" in out.columns:
        out["codigo_ibge"] = out["codigo_ibge"].map(_codigo_limpo)

    # 1) Indicadores municipais já gravados no banco.
    piv = _pivot_indicadores_municipais_para_determinantes()
    if not piv.empty and "municipio_chave" in out.columns:
        out = out.merge(piv, on="municipio_chave", how="left", suffixes=("", "__indicador"))
        for col in list(piv.columns):
            if col == "municipio_chave":
                continue
            origem = f"{col}__indicador" if f"{col}__indicador" in out.columns else col
            out = _preencher_coluna_vazia(out, col, origem)
            if origem.endswith("__indicador"):
                out = out.drop(columns=[origem], errors="ignore")

    # 2) Cache do sistema antigo com Censo 2022 já consolidado.
    ref = _ler_determinantes_referencia_antiga()
    if not ref.empty:
        if "municipio_chave" in out.columns and "municipio_chave" in ref.columns:
            out = out.merge(ref, on="municipio_chave", how="left", suffixes=("", "__ref_soc"))
        elif "codigo_ibge" in out.columns and "codigo_ibge" in ref.columns:
            ref["codigo_ibge"] = ref["codigo_ibge"].map(_codigo_limpo)
            out = out.merge(ref, on="codigo_ibge", how="left", suffixes=("", "__ref_soc"))
        for col in list(ref.columns):
            if col in {"municipio_chave", "municipio", "codigo_ibge"}:
                continue
            origem = f"{col}__ref_soc" if f"{col}__ref_soc" in out.columns else col
            out = _preencher_coluna_vazia(out, col, origem)
            if origem.endswith("__ref_soc"):
                out = out.drop(columns=[origem], errors="ignore")

    # 3) Aliases usados pela aba de determinantes/georreferenciamento.
    aliases = {
        "taxa_alfabetizacao": ["taxa_alfabetizacao_pct"],
        "taxa_analfabetismo_estimado_pct": ["taxa_analfabetismo_estimada"],
        "baixa_instrucao_pct": ["nivel_instrucao_baixo_pct", "nivel_instrucao"],
        "renda_censo_2022": ["renda_indicador"],
        "populacao_indigena": ["pessoas_indigenas_2022"],
        "populacao_quilombola": ["pessoas_quilombolas_2022"],
        "saneamento_censo_2022": ["indice_vulnerabilidade_saneamento_2022"],
    }
    for destino, origens in aliases.items():
        for origem in origens:
            out = _preencher_coluna_vazia(out, destino, origem)

    out = out.drop(columns=[c for c in ["municipio_chave"] if c in out.columns], errors="ignore")
    return out


def _classificar_prioridade_social(valor: Any) -> str:
    try:
        v = float(valor)
    except Exception:
        return "Sem classificação"
    if v >= 75:
        return "Muito alta"
    if v >= 60:
        return "Alta"
    if v >= 40:
        return "Média"
    return "Monitoramento"


def _qualidade_cobertura(df: pd.DataFrame, colunas: list[str]) -> pd.DataFrame:
    linhas = []
    total = int(len(df)) if df is not None else 0
    for col in colunas:
        if df is None or df.empty or col not in df.columns:
            preenchidos = 0
        else:
            preenchidos = int(pd.to_numeric(df[col], errors="coerce").notna().sum())
        linhas.append({
            "campo": col,
            "municipios_com_dado": preenchidos,
            "municipios_total": total,
            "cobertura_pct": round((preenchidos / total * 100.0), 1) if total else 0.0,
        })
    return pd.DataFrame(linhas)


def montar_painel_determinantes_sociais_aps() -> dict:
    """Monta leitura municipal de determinantes sociais para cruzar com vazios APS.

    A função reaproveita a base_municipal_consolidada já enriquecida por IBGE/SIDRA,
    MDS e INEP. Não inventa dados: quando uma camada ainda não foi carregada, a
    cobertura aparece como zero e o painel sinaliza pendência.
    """
    base = montar_base_mapa_municipal()
    vazio = {
        "diagnostico": {},
        "municipios": pd.DataFrame(),
        "resumo_regional": pd.DataFrame(),
        "cobertura": pd.DataFrame(),
        "mensagens_chave": [],
    }
    if base.empty:
        vazio["diagnostico"] = {"ok": False, "mensagem": "Base municipal consolidada não encontrada."}
        return vazio

    df = base.copy()
    for col in DETERMINANTES_SOCIAIS_COLUNAS + ["populacao", "total_equipes_aps", "total_ubs"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["taxa_alfabetizacao_base"] = _primeira_coluna_valida(df, ["taxa_alfabetizacao_pct", "taxa_alfabetizacao"])
    df["taxa_analfabetismo_estimado_pct"] = _primeira_coluna_valida(df, ["taxa_analfabetismo_estimado_pct", "taxa_analfabetismo_estimada"])
    mask_alf = df["taxa_analfabetismo_estimado_pct"].isna() & df["taxa_alfabetizacao_base"].between(0, 100, inclusive="both")
    df.loc[mask_alf, "taxa_analfabetismo_estimado_pct"] = 100.0 - df.loc[mask_alf, "taxa_alfabetizacao_base"]

    df["baixa_instrucao_pct"] = _primeira_coluna_valida(df, ["baixa_instrucao_pct", "nivel_instrucao_baixo_pct", "nivel_instrucao"])
    df["renda_indicador"] = _primeira_coluna_valida(df, ["renda_per_capita", "renda_censo_2022", "rendimento_medio", "rendimento_medio_mensal"])
    df["saneamento_indicador"] = _primeira_coluna_valida(df, ["saneamento_censo_2022", "esgotamento_rede_pct", "abastecimento_agua_rede_pct", "lixo_coletado_pct"])

    escolas_total = _serie_numerica(df, "escolas_total").replace({0: pd.NA})
    df["percentual_escolas_rurais"] = (_serie_numerica(df, "escolas_rurais") / escolas_total * 100.0).round(2)
    df["percentual_matriculas_educacao_especial"] = (_serie_numerica(df, "matriculas_educacao_especial") / _serie_numerica(df, "matriculas_total").replace({0: pd.NA}) * 100.0).round(2)
    df["territorios_equidade_sinal"] = (
        _serie_numerica(df, "populacao_indigena").fillna(0)
        + _serie_numerica(df, "populacao_quilombola").fillna(0)
        + (_serie_numerica(df, "escolas_indigenas").fillna(0) * 100)
        + (_serie_numerica(df, "escolas_quilombolas").fillna(0) * 100)
        + (_serie_numerica(df, "qtd_terras_indigenas_intersecoes").fillna(0) * 100)
        + (_serie_numerica(df, "qtd_assentamentos").fillna(0) * 20)
    )

    componentes = pd.DataFrame(index=df.index)
    componentes["score_analfabetismo"] = _score_percentil(df["taxa_analfabetismo_estimado_pct"], maior_pior=True)
    componentes["score_baixa_instrucao"] = _score_percentil(df["baixa_instrucao_pct"], maior_pior=True)
    componentes["score_renda"] = _score_percentil(df["renda_indicador"], maior_pior=False)
    componentes["score_ruralidade_escolar"] = _score_percentil(df["percentual_escolas_rurais"], maior_pior=True)
    componentes["score_equidade_territorial"] = _score_percentil(df["territorios_equidade_sinal"], maior_pior=True)

    # Média ponderada só dos componentes que realmente têm algum dado preenchido.
    pesos = {
        "score_analfabetismo": 0.28,
        "score_baixa_instrucao": 0.22,
        "score_renda": 0.25,
        "score_ruralidade_escolar": 0.10,
        "score_equidade_territorial": 0.15,
    }
    numerador = pd.Series(0.0, index=df.index)
    denominador = pd.Series(0.0, index=df.index)
    origem_valores = {
        "score_analfabetismo": df["taxa_analfabetismo_estimado_pct"],
        "score_baixa_instrucao": df["baixa_instrucao_pct"],
        "score_renda": df["renda_indicador"],
        "score_ruralidade_escolar": df["percentual_escolas_rurais"],
        "score_equidade_territorial": df["territorios_equidade_sinal"],
    }
    for comp, peso in pesos.items():
        valid = pd.to_numeric(origem_valores[comp], errors="coerce").notna()
        numerador = numerador + componentes[comp].where(valid, 0.0) * peso
        denominador = denominador + valid.astype(float) * peso
    df["indice_determinantes_sociais_aps"] = (numerador / denominador.replace({0: pd.NA})).round(2)
    df["classe_determinantes_sociais_aps"] = df["indice_determinantes_sociais_aps"].map(_classificar_prioridade_social)

    for comp in componentes.columns:
        df[comp] = componentes[comp].round(2)

    # Densidades/leituras úteis para explicar no painel.
    pop = _serie_numerica(df, "populacao").replace({0: pd.NA})
    df["escolas_por_10mil_hab"] = (_serie_numerica(df, "escolas_total") / pop * 10000.0).round(2)
    df["ubs_por_10mil_hab"] = (_serie_numerica(df, "total_ubs") / pop * 10000.0).round(2)

    cols_saida = [c for c in [
        "codigo_ibge", "municipio", "regiao_saude", "populacao",
        "indice_determinantes_sociais_aps", "classe_determinantes_sociais_aps",
        "taxa_alfabetizacao_base", "taxa_analfabetismo_estimado_pct", "baixa_instrucao_pct", "renda_indicador", "saneamento_indicador",
        "escolas_total", "escolas_rurais", "percentual_escolas_rurais", "escolas_indigenas", "escolas_quilombolas",
        "matriculas_total", "matriculas_educacao_especial", "percentual_matriculas_educacao_especial",
        "populacao_indigena", "populacao_quilombola", "qtd_terras_indigenas_intersecoes", "qtd_assentamentos",
        "escolas_por_10mil_hab", "ubs_por_10mil_hab",
        "score_analfabetismo", "score_baixa_instrucao", "score_renda", "score_ruralidade_escolar", "score_equidade_territorial",
    ] if c in df.columns]
    municipios = df[cols_saida].copy()
    if "indice_determinantes_sociais_aps" in municipios.columns:
        municipios = municipios.sort_values("indice_determinantes_sociais_aps", ascending=False, na_position="last").reset_index(drop=True)

    resumo_reg = pd.DataFrame()
    if "regiao_saude" in municipios.columns and not municipios.empty:
        resumo_reg = (
            municipios.groupby("regiao_saude", dropna=False)
            .agg(
                municipios=("municipio", "nunique"),
                indice_medio_determinantes=("indice_determinantes_sociais_aps", "mean"),
                municipios_prioridade_alta=("classe_determinantes_sociais_aps", lambda s: int(pd.Series(s).isin(["Muito alta", "Alta"]).sum())),
                analfabetismo_medio_pct=("taxa_analfabetismo_estimado_pct", "mean"),
                renda_indicador_medio=("renda_indicador", "mean"),
                escolas_rurais=("escolas_rurais", "sum"),
                escolas_indigenas=("escolas_indigenas", "sum"),
                escolas_quilombolas=("escolas_quilombolas", "sum"),
            )
            .reset_index()
        )
        for col in ["indice_medio_determinantes", "analfabetismo_medio_pct", "renda_indicador_medio"]:
            resumo_reg[col] = pd.to_numeric(resumo_reg[col], errors="coerce").round(2)
        resumo_reg = resumo_reg.sort_values("indice_medio_determinantes", ascending=False, na_position="last")

    cobertura = _qualidade_cobertura(df, [
        "taxa_alfabetizacao_base", "taxa_analfabetismo_estimado_pct", "baixa_instrucao_pct", "renda_indicador", "saneamento_indicador",
        "escolas_total", "escolas_rurais", "escolas_indigenas", "escolas_quilombolas", "matriculas_total",
        "populacao_indigena", "populacao_quilombola",
    ])
    diag = {
        "ok": True,
        "municipios": int(len(municipios)),
        "municipios_com_indice": int(municipios["indice_determinantes_sociais_aps"].notna().sum()) if "indice_determinantes_sociais_aps" in municipios.columns else 0,
        "prioridade_muito_alta": int((municipios.get("classe_determinantes_sociais_aps", pd.Series(dtype=str)) == "Muito alta").sum()) if not municipios.empty else 0,
        "prioridade_alta": int((municipios.get("classe_determinantes_sociais_aps", pd.Series(dtype=str)) == "Alta").sum()) if not municipios.empty else 0,
        "fontes": "IBGE/SIDRA Censo 2022, INEP/Censo Escolar e bases territoriais já consolidadas no sistema.",
    }
    mensagens = [
        "A leitura social combina escolaridade/analfabetismo, renda, ruralidade escolar e sinais de equidade territorial já carregados na base municipal consolidada.",
        "O índice é uma régua técnica de priorização, não uma nota oficial do município; serve para cruzar vulnerabilidade social com distância até UBS/APS.",
        "Quando uma fonte ainda não foi carregada, o painel mostra a cobertura e não força preenchimento artificial.",
    ]
    return {"diagnostico": diag, "municipios": municipios, "resumo_regional": resumo_reg, "cobertura": cobertura, "mensagens_chave": mensagens}


def montar_painel_vazios_determinantes_sociais_aps() -> dict:
    """Cruza vazios intramunicipais com determinantes sociais municipais.

    O cruzamento é municipal porque as variáveis IBGE/INEP atualmente consolidadas
    estão no nível do município. Os pontos intramunicipais mantêm a distância real
    até UBS/APS e recebem o contexto social do município para priorização.
    """
    vazios = montar_painel_vazios_intramunicipais()
    det = montar_painel_determinantes_sociais_aps()
    dist = vazios.get("distancias", pd.DataFrame()).copy()
    matriz = vazios.get("matriz_executiva", pd.DataFrame()).copy()
    mun_det = det.get("municipios", pd.DataFrame()).copy()

    if mun_det.empty:
        return {"vazios": vazios, "determinantes": det, "distancias_enriquecidas": dist, "matriz_integrada": matriz, "territorios_prioritarios": pd.DataFrame()}

    cols_det = [c for c in [
        "municipio", "indice_determinantes_sociais_aps", "classe_determinantes_sociais_aps",
        "taxa_analfabetismo_estimado_pct", "baixa_instrucao_pct", "renda_indicador", "saneamento_indicador",
        "escolas_total", "escolas_rurais", "escolas_indigenas", "escolas_quilombolas",
        "populacao_indigena", "populacao_quilombola", "qtd_terras_indigenas_intersecoes", "qtd_assentamentos",
    ] if c in mun_det.columns]
    mun_det = mun_det[cols_det].drop_duplicates(subset=["municipio"], keep="first")

    if not dist.empty and "municipio" in dist.columns:
        dist = dist.merge(mun_det, on="municipio", how="left")
        dist["score_distancia_intramunicipal"] = _score_percentil(pd.to_numeric(dist.get("distancia_ubs_mais_proxima_km"), errors="coerce"), maior_pior=True).round(2)
        dist["prioridade_integrada_territorio"] = (
            pd.to_numeric(dist["score_distancia_intramunicipal"], errors="coerce").fillna(0) * 0.60
            + pd.to_numeric(dist.get("indice_determinantes_sociais_aps"), errors="coerce").fillna(0) * 0.40
        ).round(2)
        dist["classe_prioridade_integrada"] = dist["prioridade_integrada_territorio"].map(_classificar_prioridade_social)

    if not matriz.empty and "municipio" in matriz.columns:
        matriz = matriz.merge(mun_det, on="municipio", how="left")
        matriz["score_vazio_intramunicipal"] = _score_percentil(pd.to_numeric(matriz.get("populacao_critica_distante"), errors="coerce").fillna(0) + pd.to_numeric(matriz.get("criticos"), errors="coerce").fillna(0) * 100, maior_pior=True).round(2)
        matriz["prioridade_integrada_municipal"] = (
            pd.to_numeric(matriz["score_vazio_intramunicipal"], errors="coerce").fillna(0) * 0.55
            + pd.to_numeric(matriz.get("indice_determinantes_sociais_aps"), errors="coerce").fillna(0) * 0.45
        ).round(2)
        matriz["classe_prioridade_integrada_municipal"] = matriz["prioridade_integrada_municipal"].map(_classificar_prioridade_social)
        matriz = matriz.sort_values("prioridade_integrada_municipal", ascending=False, na_position="last").reset_index(drop=True)

    territorios_prioritarios = pd.DataFrame()
    if not dist.empty:
        territorios_prioritarios = dist.sort_values("prioridade_integrada_territorio", ascending=False, na_position="last").head(200).reset_index(drop=True)

    return {
        "vazios": vazios,
        "determinantes": det,
        "distancias_enriquecidas": dist,
        "matriz_integrada": matriz,
        "territorios_prioritarios": territorios_prioritarios,
    }

# Aliases de compatibilidade da Etapa 4-F para evitar NameError na UI.
try:
    gerar_planilha_validacao_ubs
except NameError:
    try:
        gerar_planilha_validacao_ubs = gerar_planilha_validacao_ubs
    except NameError:
        pass

try:
    gerar_planilha_validacao_ubs
except NameError:
    try:
        gerar_planilha_validacao_ubs = gerar_planilha_validacao_ubs
    except NameError:
        pass
