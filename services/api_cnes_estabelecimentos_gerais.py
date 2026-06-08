"""Conector CNES — Estabelecimentos gerais via Portal de Dados Abertos do SUS.

v23: API-only. Corrige a identificação de Mato Grosso quando o CSV do CNES
traz códigos municipais em 6 dígitos, nomes de colunas variados ou município
apenas por nome. Mantém a tabela separada `cnes_estabelecimentos_gerais`.
"""
from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from config.settings import RAW_DIR

URL_CNES_ESTABELECIMENTOS_CSV_ZIP = (
    "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/CNES/cnes_estabelecimentos_csv.zip"
)
UF_MT = "51"


def _texto_busca(valor: Any) -> str:
    texto = str(valor or "").replace("\ufeff", " ").replace("\xa0", " ").strip().lower()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    texto = re.sub(r"[^a-z0-9]+", "_", texto)
    return re.sub(r"_+", "_", texto).strip("_")


def _normalizar_nome_chave(valor: Any) -> str:
    texto = str(valor or "").replace("\xa0", " ").strip().lower()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    texto = re.sub(r"\bmunicipio\b|\bmunic\b|\bde\b", " ", texto)
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _primeira_coluna(df: pd.DataFrame, opcoes: list[str]) -> str | None:
    mapa = {_texto_busca(c): c for c in df.columns}
    for opcao in opcoes:
        chave = _texto_busca(opcao)
        if opcao in df.columns:
            return opcao
        if chave in mapa:
            return mapa[chave]
    # busca por contém, útil para cabeçalhos longos do CKAN/DATASUS
    for opcao in opcoes:
        chave = _texto_busca(opcao)
        for col_chave, col_real in mapa.items():
            if chave and (chave in col_chave or col_chave in chave):
                return col_real
    return None


def _normalizar_municipio(valor: Any) -> str:
    texto = str(valor or "").replace("\xa0", " ").strip()
    texto = re.sub(r"^MT\s*[-/]\s*", "", texto, flags=re.I)
    texto = re.sub(r"^Munic[ií]pio de\s+", "", texto, flags=re.I)
    return " ".join(texto.split()).title()


def _to_numeric(serie: pd.Series | None) -> pd.Series:
    if serie is None:
        return pd.Series(dtype="float64")
    texto = (
        serie.astype(str)
        .str.strip()
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .str.replace(r"[^0-9\.\-]", "", regex=True)
    )
    texto = texto.mask(texto.isin(["", "-", ".", "nan", "None", "<NA>"]), None)
    return pd.to_numeric(texto, errors="coerce")


def _baixar_zip() -> bytes:
    destino = RAW_DIR / "apis" / "cnes_estabelecimentos_csv.zip"
    destino.parent.mkdir(parents=True, exist_ok=True)

    if destino.exists() and destino.stat().st_size > 100_000:
        try:
            bruto = destino.read_bytes()
            if zipfile.is_zipfile(io.BytesIO(bruto)):
                return bruto
        except Exception:
            pass

    resp = requests.get(
        URL_CNES_ESTABELECIMENTOS_CSV_ZIP,
        timeout=180,
        headers={"User-Agent": "aps-inteligencia-ses-mt/0.23", "Accept": "application/zip,*/*"},
    )
    resp.raise_for_status()
    bruto = resp.content
    if not zipfile.is_zipfile(io.BytesIO(bruto)):
        raise ValueError("A resposta do Portal de Dados Abertos/MS não é um ZIP válido.")
    try:
        destino.write_bytes(bruto)
    except Exception:
        pass
    return bruto


def _detectar_csvs_no_zip(bruto: bytes) -> tuple[zipfile.ZipFile, list[str]]:
    zf = zipfile.ZipFile(io.BytesIO(bruto), "r")
    candidatos = [n for n in zf.namelist() if n.lower().endswith((".csv", ".txt"))]
    if not candidatos:
        zf.close()
        raise ValueError("O ZIP CNES não trouxe arquivo CSV/TXT reconhecível.")
    candidatos = sorted(candidatos, key=lambda n: zf.getinfo(n).file_size, reverse=True)
    return zf, candidatos


def _detectar_separador(amostra: str) -> str:
    linhas = [ln for ln in amostra.splitlines() if ln.strip()]
    primeira_linha = linhas[0] if linhas else amostra
    candidatos = [";", ",", "\t", "|"]
    contagens = {sep: primeira_linha.count(sep) for sep in candidatos}
    return max(contagens, key=contagens.get) if max(contagens.values() or [0]) > 0 else ";"


def _mapa_municipios_mt() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Retorna mapas 7 dígitos, 6 dígitos e nome normalizado -> (codigo/nome).

    A principal correção está aqui: bases CNES frequentemente trazem o código
    municipal sem o dígito verificador (6 dígitos). Não se deve completar com
    zero; é preciso mapear para o código IBGE oficial de 7 dígitos.
    """
    linhas: list[dict[str, Any]] = []

    # 1) Preferir a base local do próprio sistema, pois já inclui Boa Esperança do Norte.
    try:
        from database.connection import get_connection

        with get_connection() as conn:
            for tabela in ["base_municipal_consolidada", "municipios", "malhas_geograficas_municipais"]:
                try:
                    rows = conn.execute(f"SELECT codigo_ibge, municipio FROM {tabela}").fetchall()
                    for row in rows:
                        codigo = str(row[0] or "").strip()
                        municipio = str(row[1] or "").strip()
                        if codigo and municipio:
                            linhas.append({"codigo_ibge": codigo, "municipio": municipio})
                except Exception:
                    continue
    except Exception:
        pass

    # 2) Fallback: API IBGE Localidades, caso a base local ainda não exista.
    if not linhas:
        try:
            from services.api_ibge_sidra import carregar_municipios_ibge_mt

            df_ibge = carregar_municipios_ibge_mt()
            linhas.extend(df_ibge[["codigo_ibge", "municipio"]].dropna().to_dict("records"))
        except Exception:
            pass

    # 3) Fallback mínimo para o novo município, se a malha antiga/IBGE endpoint vier com 141.
    linhas.append({"codigo_ibge": "5101837", "municipio": "Boa Esperança do Norte"})

    mapa7: dict[str, str] = {}
    mapa6: dict[str, str] = {}
    mapa_nome_codigo: dict[str, str] = {}
    mapa_codigo_nome: dict[str, str] = {}

    for item in linhas:
        dig = re.sub(r"\D", "", str(item.get("codigo_ibge") or ""))
        nome = _normalizar_municipio(item.get("municipio"))
        if not dig or not nome:
            continue
        if len(dig) >= 7:
            cod7 = dig[:7]
            mapa7[cod7] = nome
            mapa6[cod7[:6]] = cod7
            mapa_codigo_nome[cod7] = nome
            mapa_nome_codigo[_normalizar_nome_chave(nome)] = cod7
        elif len(dig) == 6:
            # Só mantém 6 dígitos como chave se não houver 7; não inventa DV.
            mapa6.setdefault(dig, dig)
            mapa_codigo_nome.setdefault(dig, nome)
            mapa_nome_codigo.setdefault(_normalizar_nome_chave(nome), dig)

    return mapa7, mapa6, mapa_nome_codigo | {f"_nome_{k}": v for k, v in mapa_codigo_nome.items()}


def _resolver_codigo_serie(serie: pd.Series, mapa7: dict[str, str], mapa6: dict[str, str]) -> pd.Series:
    def resolver(valor: Any) -> str | None:
        dig = re.sub(r"\D", "", str(valor or ""))
        if not dig:
            return None
        # Procura primeiro um código MT de 7 dígitos em qualquer parte do valor.
        for m in re.finditer(r"51\d{5}", dig):
            cod = m.group(0)
            if cod in mapa7:
                return cod
        # Depois procura chave de 6 dígitos e mapeia para 7.
        for m in re.finditer(r"51\d{4}", dig):
            cod6 = m.group(0)
            if cod6 in mapa6:
                return mapa6[cod6]
        # Alguns arquivos podem trazer código já no início, sem estar no mapa local.
        if len(dig) >= 7 and dig[:2] == UF_MT:
            return dig[:7]
        if len(dig) >= 6 and dig[:2] == UF_MT and dig[:6] in mapa6:
            return mapa6[dig[:6]]
        return None

    return serie.map(resolver)


def _identificar_codigo_municipio(chunk: pd.DataFrame) -> pd.Series:
    mapa7, mapa6, mapa_nome_codigo = _mapa_municipios_mt()
    mapa_codigo_nome = {k.replace("_nome_", ""): v for k, v in mapa_nome_codigo.items() if k.startswith("_nome_")}
    mapa_nome = {k: v for k, v in mapa_nome_codigo.items() if not k.startswith("_nome_")}

    candidatos_codigo = [
        "codigo_ibge", "cod_ibge", "co_ibge", "ibge", "codigo_municipio", "cod_municipio",
        "co_municipio", "co_mun", "cod_mun", "municipio_ibge", "co_municipio_gestor",
        "codufmun", "cod_uf_mun", "codigo_uf_municipio", "co_municipio_estabelecimento",
        "co_municipio_residencia", "municipio_residencia", "munic_res", "CO_MUNICIPIO", "CO_MUNICIPIO_GESTOR",
    ]
    candidatos_nome = [
        "municipio", "nome_municipio", "no_municipio", "no_municipio_gestor", "cidade",
        "município", "nome_do_municipio", "municipio_gestor", "no_municipio_estabelecimento",
    ]

    codigo_final = pd.Series([None] * len(chunk), index=chunk.index, dtype="object")

    # Tenta várias colunas de código, não apenas a primeira.
    cols_norm = {_texto_busca(c): c for c in chunk.columns}
    codigo_cols: list[str] = []
    for cand in candidatos_codigo:
        chave = _texto_busca(cand)
        for col_chave, col_real in cols_norm.items():
            if chave == col_chave or (chave and chave in col_chave):
                if col_real not in codigo_cols:
                    codigo_cols.append(col_real)

    for col in codigo_cols:
        resolvido = _resolver_codigo_serie(chunk[col], mapa7, mapa6)
        codigo_final = codigo_final.fillna(resolvido)
        if codigo_final.notna().any():
            break

    # Complementa por nome do município.
    nome_col = _primeira_coluna(chunk, candidatos_nome)
    if nome_col:
        nomes = chunk[nome_col].map(lambda x: mapa_nome.get(_normalizar_nome_chave(x)))
        codigo_final = codigo_final.fillna(nomes)

    # Se houver UF=MT e uma coluna de município por nome com grafia do mapa, mantém.
    uf_col = _primeira_coluna(chunk, ["uf", "sg_uf", "sigla_uf", "estado", "co_uf", "codigo_uf"])
    if uf_col and nome_col:
        uf = chunk[uf_col].astype(str).map(_normalizar_nome_chave)
        mascara_mt = uf.isin(["mt", "mato grosso", "51"])
        nomes = chunk.loc[mascara_mt, nome_col].map(lambda x: mapa_nome.get(_normalizar_nome_chave(x)))
        codigo_final.loc[mascara_mt] = codigo_final.loc[mascara_mt].fillna(nomes)

    return codigo_final


def _filtrar_chunk_mt(chunk: pd.DataFrame) -> pd.DataFrame:
    chunk = chunk.copy()
    chunk.columns = [str(c).replace("\ufeff", "").replace("\xa0", " ").strip() for c in chunk.columns]
    codigo = _identificar_codigo_municipio(chunk)
    filtrado = chunk[codigo.astype(str).str.startswith(UF_MT, na=False)].copy()
    if not filtrado.empty:
        filtrado["codigo_ibge_identificado"] = codigo.loc[filtrado.index].astype(str)
    return filtrado


def _ler_csv_filtrado_mt(bruto: bytes) -> pd.DataFrame:
    zf, nomes_csv = _detectar_csvs_no_zip(bruto)
    frames: list[pd.DataFrame] = []
    erros: list[str] = []
    diagnosticos: list[str] = []
    encodings = ["latin1", "cp1252", "utf-8-sig", "utf-8"]

    try:
        for nome_csv in nomes_csv:
            arquivo_bytes = zf.read(nome_csv)
            for enc in encodings:
                try:
                    amostra = arquivo_bytes[:120_000].decode(enc, errors="replace")
                    sep = _detectar_separador(amostra)
                    leitor = pd.read_csv(
                        io.BytesIO(arquivo_bytes),
                        sep=sep,
                        dtype=str,
                        encoding=enc,
                        encoding_errors="replace",
                        chunksize=40_000,
                        low_memory=False,
                        on_bad_lines="skip",
                    )

                    encontrou_no_arquivo = False
                    primeira_colunas: list[str] | None = None
                    linhas_lidas = 0
                    for chunk in leitor:
                        linhas_lidas += len(chunk)
                        chunk.columns = [str(c).replace("\ufeff", "").replace("\xa0", " ").strip() for c in chunk.columns]
                        if primeira_colunas is None:
                            primeira_colunas = list(chunk.columns)[:12]
                        filtrado = _filtrar_chunk_mt(chunk)
                        if not filtrado.empty:
                            frames.append(filtrado)
                            encontrou_no_arquivo = True

                    diagnosticos.append(
                        f"{Path(nome_csv).name} / {enc} / sep='{sep}' / linhas={linhas_lidas} / colunas={primeira_colunas}"
                    )
                    if encontrou_no_arquivo:
                        break

                except TypeError:
                    # Compatibilidade com pandas antigos sem encoding_errors.
                    try:
                        amostra = arquivo_bytes[:120_000].decode(enc, errors="replace")
                        sep = _detectar_separador(amostra)
                        texto = arquivo_bytes.decode(enc, errors="replace")
                        leitor = pd.read_csv(
                            io.StringIO(texto),
                            sep=sep,
                            dtype=str,
                            chunksize=40_000,
                            low_memory=False,
                            on_bad_lines="skip",
                        )
                        encontrou_no_arquivo = False
                        primeira_colunas = None
                        linhas_lidas = 0
                        for chunk in leitor:
                            linhas_lidas += len(chunk)
                            chunk.columns = [str(c).replace("\ufeff", "").replace("\xa0", " ").strip() for c in chunk.columns]
                            if primeira_colunas is None:
                                primeira_colunas = list(chunk.columns)[:12]
                            filtrado = _filtrar_chunk_mt(chunk)
                            if not filtrado.empty:
                                frames.append(filtrado)
                                encontrou_no_arquivo = True
                        diagnosticos.append(
                            f"{Path(nome_csv).name} / {enc} / sep='{sep}' / linhas={linhas_lidas} / colunas={primeira_colunas}"
                        )
                        if encontrou_no_arquivo:
                            break
                    except Exception as exc:
                        erros.append(f"{Path(nome_csv).name} / {enc}: {exc}")
                        continue

                except Exception as exc:
                    erros.append(f"{Path(nome_csv).name} / {enc}: {exc}")
                    continue
    finally:
        zf.close()

    if not frames:
        detalhe = " | ".join((diagnosticos[-3:] + erros[-3:])) or "sem detalhe técnico disponível"
        raise ValueError(
            "CNES Estabelecimentos foi lido, mas nenhum registro de MT foi identificado. "
            f"Detalhe: {detalhe}"
        )

    return pd.concat(frames, ignore_index=True, sort=False)


def _normalizar_estabelecimentos(df: pd.DataFrame) -> pd.DataFrame:
    mapa7, mapa6, mapa_nome_codigo = _mapa_municipios_mt()
    mapa_codigo_nome = {k.replace("_nome_", ""): v for k, v in mapa_nome_codigo.items() if k.startswith("_nome_")}

    cod_col = _primeira_coluna(df, [
        "codigo_ibge", "cod_ibge", "co_municipio", "cod_municipio", "codigo_municipio",
        "CO_MUNICIPIO_GESTOR", "CO_MUNICIPIO", "IBGE", "CODUFMUN", "codufmun",
    ])
    mun_col = _primeira_coluna(df, ["municipio", "no_municipio", "nome_municipio", "cidade", "NO_MUNICIPIO", "no_municipio_gestor"])
    cnes_col = _primeira_coluna(df, ["cnes", "co_cnes", "codigo_cnes", "cod_cnes", "CNES", "CO_CNES"])
    nome_col = _primeira_coluna(df, ["nome_unidade", "nome_fantasia", "no_fantasia", "estabelecimento", "nome_estabelecimento", "NO_FANTASIA", "no_estabelecimento"])
    razao_col = _primeira_coluna(df, ["razao_social", "no_razao_social", "NO_RAZAO_SOCIAL"])
    tipo_col = _primeira_coluna(df, ["tipo_unidade", "ds_tipo_unidade", "descricao_tipo_unidade", "tipo_estabelecimento", "ds_tipo_estabelecimento", "TP_UNIDADE", "DS_TIPO_UNIDADE"])
    natureza_col = _primeira_coluna(df, ["natureza_juridica", "ds_natureza_juridica", "NO_NATUREZA_JURIDICA", "natureza", "natureza_organizacao"])
    gestao_col = _primeira_coluna(df, ["gestao", "tp_gestao", "tipo_gestao", "TP_GESTAO", "ds_gestao"])
    atende_sus_col = _primeira_coluna(df, ["atende_sus", "st_atende_sus", "convenio_sus", "ST_ATENDE_SUS", "sus", "vinculo_sus"])
    endereco_col = _primeira_coluna(df, ["endereco", "logradouro", "no_logradouro", "NO_LOGRADOURO", "endereco_estabelecimento"])
    bairro_col = _primeira_coluna(df, ["bairro", "no_bairro", "NO_BAIRRO", "bairro_estabelecimento"])
    cep_col = _primeira_coluna(df, ["cep", "co_cep", "nu_cep", "CO_CEP"])
    telefone_col = _primeira_coluna(df, ["telefone", "nu_telefone", "NU_TELEFONE", "tel", "fone"])
    lat_col = _primeira_coluna(df, ["latitude", "lat", "vl_latitude", "VL_LATITUDE"])
    lon_col = _primeira_coluna(df, ["longitude", "lon", "long", "vl_longitude", "VL_LONGITUDE"])

    if "codigo_ibge_identificado" in df.columns:
        codigo = df["codigo_ibge_identificado"].astype(str)
    elif cod_col:
        codigo = _resolver_codigo_serie(df[cod_col], mapa7, mapa6)
    else:
        codigo = pd.Series([None] * len(df), index=df.index, dtype="object")

    if mun_col:
        municipio = df[mun_col].map(_normalizar_municipio)
    else:
        municipio = codigo.map(mapa_codigo_nome).fillna("")

    # Complementa nome por código oficial quando a coluna de município não existe ou vem vazia.
    municipio = municipio.mask(municipio.astype(str).str.strip().isin(["", "None", "Nan", "Na", "N/A"]), codigo.map(mapa_codigo_nome))

    out = pd.DataFrame(index=df.index)
    out["codigo_ibge"] = codigo.astype(str).str.extract(r"(51\d{5})", expand=False).fillna(codigo.astype(str))
    out["municipio"] = municipio.fillna("").astype(str)
    out["cnes"] = df[cnes_col].astype(str).str.extract(r"(\d+)", expand=False).fillna("") if cnes_col else ""
    out["nome_unidade"] = df[nome_col].astype(str).str.replace("\xa0", " ").str.strip() if nome_col else ""
    out["razao_social"] = df[razao_col].astype(str).str.replace("\xa0", " ").str.strip() if razao_col else ""
    out["tipo_unidade"] = df[tipo_col].astype(str).str.replace("\xa0", " ").str.strip() if tipo_col else ""
    out["natureza_juridica"] = df[natureza_col].astype(str).str.replace("\xa0", " ").str.strip() if natureza_col else ""
    out["gestao"] = df[gestao_col].astype(str).str.strip() if gestao_col else ""
    out["atende_sus"] = df[atende_sus_col].astype(str).str.strip() if atende_sus_col else ""
    out["endereco"] = df[endereco_col].astype(str).str.replace("\xa0", " ").str.strip() if endereco_col else ""
    out["bairro"] = df[bairro_col].astype(str).str.replace("\xa0", " ").str.strip() if bairro_col else ""
    out["cep"] = df[cep_col].astype(str).str.extract(r"(\d+)", expand=False).fillna("") if cep_col else ""
    out["telefone"] = df[telefone_col].astype(str).str.strip() if telefone_col else ""
    out["latitude"] = _to_numeric(df[lat_col]) if lat_col else None
    out["longitude"] = _to_numeric(df[lon_col]) if lon_col else None
    out["fonte_url"] = URL_CNES_ESTABELECIMENTOS_CSV_ZIP

    out = out[out["codigo_ibge"].astype(str).str.startswith(UF_MT, na=False)].copy()
    out = out[out["municipio"].astype(str).str.strip() != ""].copy()
    out = out.drop_duplicates(subset=["codigo_ibge", "cnes", "nome_unidade"], keep="last")
    if out.empty:
        raise ValueError("A base CNES Estabelecimentos ficou vazia após normalização/filtro MT.")
    return out.reset_index(drop=True)


def carregar_cnes_estabelecimentos_gerais_mt() -> pd.DataFrame:
    bruto = _baixar_zip()
    df = _ler_csv_filtrado_mt(bruto)
    return _normalizar_estabelecimentos(df)


def testar_cnes_estabelecimentos_gerais_mt() -> pd.DataFrame:
    return carregar_cnes_estabelecimentos_gerais_mt().head(100).copy()
