from datetime import datetime
import unicodedata
import re

import pandas as pd

from config.municipios_mt import DEFAULT_MUNICIPIOS
from config.parametros import TIPOS_EQUIPE_CNES
from database.connection import db_session
from services.upload_service import padronizar_dataframe


def _chave_municipio(valor) -> str:
    texto = str(valor or "").strip().upper()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    texto = " ".join(texto.split())
    return texto


_MUNICIPIOS_MT_OFICIAIS = {_chave_municipio(item.get("municipio")): item.get("municipio") for item in DEFAULT_MUNICIPIOS}


def _filtrar_municipios_mt(df: pd.DataFrame, coluna_municipio: str = "municipio") -> pd.DataFrame:
    if df.empty or coluna_municipio not in df.columns:
        return df
    aux = df.copy()
    aux["_chave_municipio_mt"] = aux[coluna_municipio].map(_chave_municipio)
    aux = aux[aux["_chave_municipio_mt"].isin(_MUNICIPIOS_MT_OFICIAIS.keys())].copy()
    if not aux.empty:
        aux[coluna_municipio] = aux["_chave_municipio_mt"].map(_MUNICIPIOS_MT_OFICIAIS)
    return aux.drop(columns=["_chave_municipio_mt"], errors="ignore")


def _primeira_coluna_existente(df: pd.DataFrame, opcoes: list[str]) -> str | None:
    for col in opcoes:
        if col in df.columns:
            return col
    return None


def _normalizar_municipio(serie: pd.Series) -> pd.Series:
    return (
        serie.astype(str)
        .str.strip()
        .str.replace(r"\s+-\s+MT$", "", regex=True)
        .str.replace(r"^Município de\s+", "", regex=True)
        .str.replace(r"^Municipio de\s+", "", regex=True)
    )

def _normalizar_municipio_ambiental(valor) -> str:
    texto = str(valor or "").replace("\xa0", " ").strip()
    texto = re.sub(r"\s+", " ", texto)
    texto = re.sub(r"\s*[-,]\s*MT$", "", texto, flags=re.IGNORECASE).strip()
    texto = texto.replace("IRFORMADO", "INFORMADO").replace("irformado", "informado")
    chave = _chave_municipio(texto)
    if chave in {"", "NAO INFORMADO", "SEM INFORMACAO", "NONE", "NAN"}:
        return ""
    return _MUNICIPIOS_MT_OFICIAIS.get(chave, texto.title())


def _mapa_municipio_para_codigo() -> dict[str, str]:
    mapa: dict[str, str] = {}
    try:
        with db_session() as conn:
            rows = conn.execute("SELECT codigo_ibge, municipio FROM municipios").fetchall()
        for codigo, municipio in rows:
            chave = _chave_municipio(municipio)
            cod = _codigo_limpo(codigo)
            if chave and cod:
                mapa[chave] = cod
    except Exception:
        pass
    for item in DEFAULT_MUNICIPIOS:
        chave = _chave_municipio(item.get("municipio"))
        cod = _codigo_limpo(item.get("codigo_ibge"))
        if chave and cod:
            mapa[chave] = cod
    return mapa


def _enriquecer_areas_ambientais(out: pd.DataFrame) -> pd.DataFrame:
    if out.empty:
        return out
    out = out.copy()
    if "municipio" not in out.columns:
        out["municipio"] = ""
    out["municipio"] = out["municipio"].map(_normalizar_municipio_ambiental)
    mapa_codigo = _mapa_municipio_para_codigo()
    if "codigo_ibge" not in out.columns:
        out["codigo_ibge"] = ""
    cod_atual = out["codigo_ibge"].map(_codigo_limpo)
    cod_por_nome = out["municipio"].map(lambda x: mapa_codigo.get(_chave_municipio(x), ""))
    out["codigo_ibge"] = cod_atual.where(cod_atual.astype(str).str.strip() != "", cod_por_nome).fillna("")
    for col in ["tipo_ocorrencia", "produto_residuo", "situacao", "descricao"]:
        if col in out.columns:
            out[col] = out[col].fillna("").astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    return out


def _codigo_limpo(valor) -> str:
    import re
    digitos = re.sub(r"\D", "", "" if valor is None else str(valor))
    if len(digitos) >= 7:
        return digitos[:7]
    if len(digitos) == 6:
        return digitos
    return digitos


def _mapa_codigo_para_municipio() -> dict[str, str]:
    mapa: dict[str, str] = {}
    try:
        with db_session() as conn:
            rows = conn.execute("SELECT codigo_ibge, municipio FROM municipios").fetchall()
        for codigo, municipio in rows:
            cod = _codigo_limpo(codigo)
            if cod and municipio:
                mapa[cod] = municipio
                if len(cod) >= 6:
                    mapa[cod[:6]] = municipio
    except Exception:
        pass
    for item in DEFAULT_MUNICIPIOS:
        codigo = _codigo_limpo(item.get("codigo_ibge"))
        municipio = item.get("municipio")
        if codigo and municipio:
            mapa[codigo] = municipio
            mapa[codigo[:6]] = municipio
    return mapa


def _preencher_municipio_por_codigo_ibge(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "codigo_ibge" not in df.columns:
        return df
    out = df.copy()
    if "municipio" not in out.columns:
        out["municipio"] = ""
    mapa = _mapa_codigo_para_municipio()
    codigos = out["codigo_ibge"].map(_codigo_limpo)
    municipios_por_codigo = codigos.map(mapa)
    vazio = out["municipio"].isna() | (out["municipio"].astype(str).str.strip().isin(["", "None", "nan"]))
    out.loc[vazio, "municipio"] = municipios_por_codigo[vazio]
    out["codigo_ibge"] = codigos
    return out


def _to_numeric(serie: pd.Series | None):
    if serie is None:
        return None

    # Se o pandas já reconheceu a coluna como numérica, não converter para string.
    # A rotina antiga removia todos os pontos e transformava 169373.0 em 1693730.
    if pd.api.types.is_numeric_dtype(serie):
        return pd.to_numeric(serie, errors="coerce")

    def converter(valor):
        if valor is None or pd.isna(valor):
            return None
        texto = str(valor).strip()
        if texto in {"", "-", ".", "...", "X", "x", "None", "nan", "NaN"}:
            return None
        texto = re.sub(r"[^0-9,\.\-]", "", texto)
        # Formato brasileiro completo: 1.234,56 -> 1234.56
        if "," in texto and "." in texto:
            if texto.rfind(",") > texto.rfind("."):
                texto = texto.replace(".", "").replace(",", ".")
            else:
                texto = texto.replace(",", "")
        elif "," in texto:
            texto = texto.replace(",", ".")
        # Se houver apenas ponto, preservar como decimal.
        try:
            return float(texto)
        except Exception:
            return None

    return serie.map(converter)


def _corrigir_populacao_sidra(valor: pd.Series) -> pd.Series:
    serie = pd.to_numeric(valor, errors="coerce")
    # Correção defensiva para CSV/SIDRA lido com uma casa decimal implícita.
    if serie.notna().any() and float(serie.max()) > 1_000_000:
        serie = serie / 10
    return serie


def _montar_area_densidade(df: pd.DataFrame, fonte: str, importacao_id: int | None, agora: str) -> pd.DataFrame:
    municipio_col = _primeira_coluna_existente(df, ["municipio", "no_municipio", "cidade", "nome_municipio"])
    ano_col = _primeira_coluna_existente(df, ["ano", "ano_referencia"])
    competencia_col = _primeira_coluna_existente(df, ["competencia", "referencia", "periodo"])
    indicador_col = _primeira_coluna_existente(df, ["indicador", "variavel", "nome_indicador"])
    valor_col = _primeira_coluna_existente(df, ["valor", "vl_indicador", "resultado"])
    if not municipio_col:
        raise ValueError("Não foi possível identificar a coluna de município na base de área/densidade.")

    if indicador_col and valor_col:
        out = pd.DataFrame({
            "municipio": _normalizar_municipio(df[municipio_col]),
            "ano": pd.to_numeric(df[ano_col], errors="coerce") if ano_col else 2022,
            "competencia": df[competencia_col] if competencia_col else "2022",
            "indicador": df[indicador_col].astype(str),
            "valor": _to_numeric(df[valor_col]),
        })
        ind_norm = out["indicador"].astype(str).str.lower()
        out.loc[ind_norm.str.contains("area|área", na=False), "indicador"] = "area_territorial_km2"
        out.loc[ind_norm.str.contains("densidade", na=False), "indicador"] = "densidade_demografica_hab_km2"
    else:
        area_col = _primeira_coluna_existente(df, ["area_km2", "area_territorial_km2", "area", "area_territorial"])
        dens_col = _primeira_coluna_existente(df, ["densidade_hab_km2", "densidade_demografica_hab_km2", "densidade_calculada_hab_km2", "densidade"])
        registros = []
        base_mun = _normalizar_municipio(df[municipio_col])
        ano = pd.to_numeric(df[ano_col], errors="coerce") if ano_col else pd.Series([2022] * len(df))
        comp = df[competencia_col] if competencia_col else pd.Series(["2022"] * len(df))
        if area_col:
            registros.append(pd.DataFrame({"municipio": base_mun, "ano": ano, "competencia": comp, "indicador": "area_territorial_km2", "valor": _to_numeric(df[area_col])}))
        if dens_col:
            registros.append(pd.DataFrame({"municipio": base_mun, "ano": ano, "competencia": comp, "indicador": "densidade_demografica_hab_km2", "valor": _to_numeric(df[dens_col])}))
        out = pd.concat(registros, ignore_index=True) if registros else pd.DataFrame(columns=["municipio", "ano", "competencia", "indicador", "valor"])

    out["fonte"] = fonte
    out["importacao_id"] = importacao_id
    out["atualizado_em"] = agora
    out = _filtrar_municipios_mt(out, "municipio")
    out = out.dropna(subset=["municipio", "indicador"], how="any")
    return out


def _inferir_ano_por_coluna(nome_coluna: str, ano_padrao=None):
    import re
    m = re.search(r"(20\d{2}|19\d{2})", str(nome_coluna))
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass
    return ano_padrao


def _montar_indicadores_genericos(df: pd.DataFrame, tipo: str) -> pd.DataFrame:
    """Converte bases municipais variadas em formato longo de indicadores.

    A versão anterior capturava apenas uma coluna `valor`. Para CNES/DATASUS,
    muitas rotinas antigas retornam várias métricas em colunas diferentes
    (ex.: nascidos_vivos_sinasc_2024, obitos_sim_2024, leitos_sus_total).
    Por isso, quando não houver coluna explícita de indicador/valor, fazemos
    uma conversão tipo melt para preservar todas as métricas numéricas úteis.
    """
    municipio = _primeira_coluna_existente(df, ["municipio", "no_municipio", "cidade", "nome_municipio"])
    ano = _primeira_coluna_existente(df, ["ano", "ano_referencia", "ano_base", "ano_base_sinasc", "ano_base_sim"])
    competencia = _primeira_coluna_existente(df, ["competencia", "referencia", "periodo"])
    valor = _primeira_coluna_existente(df, ["valor", "populacao", "populacao_ibge", "indice", "taxa", "resultado"])
    indicador = _primeira_coluna_existente(df, ["indicador", "variavel", "nome_indicador"])

    if not municipio:
        raise ValueError("Não foi possível identificar a coluna de município na base de indicadores.")

    ano_base = pd.to_numeric(df[ano], errors="coerce") if ano else pd.Series([None] * len(df))
    competencia_base = df[competencia] if competencia else pd.Series([""] * len(df))
    municipio_base = _normalizar_municipio(df[municipio])

    # Caso clássico: colunas indicador + valor.
    if indicador and valor:
        out = pd.DataFrame({
            "municipio": municipio_base,
            "ano": ano_base,
            "competencia": competencia_base,
            "indicador": df[indicador].astype(str),
            "valor": _to_numeric(df[valor]),
        })
    # Caso população simples.
    elif tipo == "populacao" and valor:
        out = pd.DataFrame({
            "municipio": municipio_base,
            "ano": ano_base,
            "competencia": competencia_base,
            "indicador": "populacao_estimada",
            "valor": _to_numeric(df[valor]),
        })
    # Caso CNES/DATASUS/SIDRA aberto: várias métricas em colunas.
    else:
        colunas_ignorar = {
            municipio, ano, competencia,
            "codigo_ibge", "cod_ibge", "codigo_municipio", "co_municipio",
            "fonte", "origem", "arquivo", "url", "observacao", "metodo", "alerta",
            "fonte_sinasc", "fonte_sim", "fonte_leitos", "arquivo_sinasc", "url_sim",
        }
        registros = []
        for col in df.columns:
            if col in colunas_ignorar:
                continue
            serie_num = _to_numeric(df[col])
            if serie_num is None or not serie_num.notna().any():
                continue
            ano_col = _inferir_ano_por_coluna(col, None)
            tmp = pd.DataFrame({
                "municipio": municipio_base,
                "ano": ano_col if ano_col is not None else ano_base,
                "competencia": competencia_base,
                "indicador": str(col),
                "valor": serie_num,
            })
            registros.append(tmp)
        out = pd.concat(registros, ignore_index=True) if registros else pd.DataFrame(columns=["municipio", "ano", "competencia", "indicador", "valor"])

    out = _filtrar_municipios_mt(out, "municipio")
    if tipo == "populacao" and "valor" in out.columns:
        out["valor"] = _corrigir_populacao_sidra(out["valor"])
        out["indicador"] = "populacao_estimada"
    return out


def _importar_municipios(df: pd.DataFrame, importacao_id: int | None, fonte: str, agora: str) -> dict:
    codigo = _primeira_coluna_existente(df, ["codigo_ibge", "cod_ibge", "id", "codigo_municipio"])
    municipio = _primeira_coluna_existente(df, ["municipio", "nome", "no_municipio", "cidade"])
    if not municipio:
        raise ValueError("Não foi possível identificar a coluna de município na base IBGE.")

    out = pd.DataFrame({
        "codigo_ibge": df[codigo].astype(str) if codigo else "",
        "municipio": _normalizar_municipio(df[municipio]),
    })
    out["codigo_ibge"] = out["codigo_ibge"].astype(str).str.extract(r"(\d{7})", expand=False).fillna(out["codigo_ibge"].astype(str))
    out = _filtrar_municipios_mt(out, "municipio")
    out = out[out["codigo_ibge"].astype(str).str.match(r"^51\d{5}$", na=False)]
    out = out.dropna(subset=["municipio"]).drop_duplicates(subset=["municipio"])

    with db_session() as conn:
        for row in out.to_dict("records"):
            conn.execute(
                """
                INSERT OR IGNORE INTO municipios (municipio, codigo_ibge, atualizado_em)
                VALUES (?, ?, ?)
                """,
                (row["municipio"], row.get("codigo_ibge"), agora),
            )
            conn.execute(
                """
                UPDATE municipios
                   SET codigo_ibge = COALESCE(NULLIF(?, ''), codigo_ibge),
                       atualizado_em = ?
                 WHERE municipio = ?
                """,
                (row.get("codigo_ibge", ""), agora, row["municipio"]),
            )
    return {"tabela": "municipios", "linhas": len(out), "colunas": len(out.columns)}



def _importar_leitos_cnes(df: pd.DataFrame, importacao_id: int | None, fonte: str, agora: str) -> dict:
    """Importa a base detalhada de leitos CNES como indicadores municipais agregados.

    A rotina antiga retorna uma linha por estabelecimento/tipo de leito. Tratar essa base
    como indicadores genéricos fazia o sistema somar telefone, bairro e outros campos
    numéricos por engano. Aqui mantemos apenas as métricas úteis:
      - leitos_sus_total
      - leitos_existentes_total
      - qtd_hospitais_leitos
      - registros_leitos_cnes
    """
    if df is None or df.empty:
        out = pd.DataFrame(columns=["municipio", "ano", "competencia", "indicador", "valor", "fonte", "importacao_id", "atualizado_em"])
    else:
        work = df.copy()
        municipio_col = _primeira_coluna_existente(work, ["municipio", "no_municipio", "cidade", "nome_municipio"])
        codigo_col = _primeira_coluna_existente(work, ["codigo_ibge", "cod_ibge", "co_municipio", "codigo_municipio", "cod_municipio"])
        if not municipio_col and not codigo_col:
            raise ValueError("Não foi possível identificar município/código IBGE na base de leitos CNES.")

        if municipio_col:
            work["municipio"] = _normalizar_municipio(work[municipio_col])
        else:
            work["municipio"] = ""
        if codigo_col:
            work["codigo_ibge"] = work[codigo_col].map(_codigo_limpo)
        work = _preencher_municipio_por_codigo_ibge(work)
        work = _filtrar_municipios_mt(work, "municipio")
        if work.empty:
            raise ValueError("A base de leitos foi lida, mas nenhum município de Mato Grosso foi identificado após o tratamento.")

        col_leitos_sus = _primeira_coluna_existente(work, ["leitos_sus", "leitos_sus_total", "qt_sus", "qtd_sus", "quantidade_sus", "qt_leitos_sus"])
        col_leitos_exist = _primeira_coluna_existente(work, ["leitos_existentes", "leitos_existentes_total", "qt_existente", "qtd_existente", "quantidade_existente", "qt_leitos_existentes"])
        col_cnes = _primeira_coluna_existente(work, ["cnes", "co_cnes", "codigo_cnes"])
        col_nome = _primeira_coluna_existente(work, ["nome_estabelecimento_leitos", "nome_estabelecimento", "nome_unidade", "nome_fantasia", "no_fantasia"])

        work["_leitos_sus"] = _to_numeric(work[col_leitos_sus]) if col_leitos_sus else 0
        work["_leitos_existentes"] = _to_numeric(work[col_leitos_exist]) if col_leitos_exist else 0
        work["_leitos_sus"] = pd.to_numeric(work["_leitos_sus"], errors="coerce").fillna(0)
        work["_leitos_existentes"] = pd.to_numeric(work["_leitos_existentes"], errors="coerce").fillna(0)

        if col_cnes:
            ident = work[col_cnes].astype(str).str.strip()
        else:
            ident = pd.Series([""] * len(work), index=work.index)
        if col_nome:
            nome = work[col_nome].astype(str).str.strip()
            ident = ident.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "<NA>": pd.NA}).fillna(nome)
        work["_identificador_estab"] = ident
        work["_identificador_estab"] = work["_identificador_estab"].replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "<NA>": pd.NA})

        def _nunique_util(serie: pd.Series) -> int:
            vals = serie.astype(str).str.strip()
            vals = vals[~vals.str.lower().isin(["", "nan", "none", "<na>"])]
            return int(vals.nunique()) if not vals.empty else 0

        agg = (
            work.groupby("municipio", dropna=False)
            .agg(
                leitos_sus_total=("_leitos_sus", "sum"),
                leitos_existentes_total=("_leitos_existentes", "sum"),
                qtd_hospitais_leitos=("_identificador_estab", _nunique_util),
                registros_leitos_cnes=("municipio", "size"),
            )
            .reset_index()
        )
        registros = []
        for indicador in ["leitos_sus_total", "leitos_existentes_total", "qtd_hospitais_leitos", "registros_leitos_cnes"]:
            tmp = agg[["municipio", indicador]].rename(columns={indicador: "valor"})
            tmp["indicador"] = indicador
            registros.append(tmp)
        out = pd.concat(registros, ignore_index=True)
        out["ano"] = None
        out["competencia"] = "CNES_LEITOS"
        out["valor"] = pd.to_numeric(out["valor"], errors="coerce")
        out = out[["municipio", "ano", "competencia", "indicador", "valor"]]
        out["fonte"] = fonte
        out["importacao_id"] = importacao_id
        out["atualizado_em"] = agora
        out = out.dropna(subset=["municipio", "indicador"], how="any")

    with db_session() as conn:
        # Remove leituras antigas da mesma fonte, inclusive a versão bugada que
        # transformava telefone/bairro/nome em indicadores numéricos.
        if fonte:
            conn.execute("DELETE FROM indicadores_municipais WHERE fonte = ?", (fonte,))
        if importacao_id is not None:
            conn.execute("DELETE FROM indicadores_municipais WHERE importacao_id = ?", (importacao_id,))
        out.to_sql("indicadores_municipais", conn, if_exists="append", index=False)
    return {"tabela": "indicadores_municipais", "linhas": len(out), "colunas": len(out.columns), "observacao": "Leitos CNES agregados por município; campos textuais/telefone foram ignorados."}


def _importar_indicadores(df: pd.DataFrame, tipo: str, importacao_id: int | None, fonte: str, agora: str) -> dict:
    if tipo == "area_densidade":
        out = _montar_area_densidade(df, fonte=fonte, importacao_id=importacao_id, agora=agora)
    else:
        out = _montar_indicadores_genericos(df, tipo)
        out["fonte"] = fonte
        out["importacao_id"] = importacao_id
        out["atualizado_em"] = agora
        out = _filtrar_municipios_mt(out, "municipio")
        out = out.dropna(subset=["municipio", "indicador"], how="any")

    with db_session() as conn:
        if importacao_id is not None:
            conn.execute("DELETE FROM indicadores_municipais WHERE importacao_id = ?", (importacao_id,))
        out.to_sql("indicadores_municipais", conn, if_exists="append", index=False)
    return {"tabela": "indicadores_municipais", "linhas": len(out), "colunas": len(out.columns)}



def _importar_malhas_geograficas(df: pd.DataFrame, importacao_id: int | None, fonte: str, agora: str) -> dict:
    mapa = {
        "codigo_ibge": _primeira_coluna_existente(df, ["codigo_ibge", "cod_ibge", "codarea", "codigo_municipio"]),
        "municipio": _primeira_coluna_existente(df, ["municipio", "nome", "no_municipio"]),
        "nivel_geografico": _primeira_coluna_existente(df, ["nivel_geografico", "nivel"]),
        "latitude_centroide": _primeira_coluna_existente(df, ["latitude_centroide", "lat_centroide", "latitude"]),
        "longitude_centroide": _primeira_coluna_existente(df, ["longitude_centroide", "lon_centroide", "longitude"]),
        "min_latitude": _primeira_coluna_existente(df, ["min_latitude"]),
        "max_latitude": _primeira_coluna_existente(df, ["max_latitude"]),
        "min_longitude": _primeira_coluna_existente(df, ["min_longitude"]),
        "max_longitude": _primeira_coluna_existente(df, ["max_longitude"]),
        "quantidade_pontos": _primeira_coluna_existente(df, ["quantidade_pontos", "qtd_pontos"]),
        "geometry_json": _primeira_coluna_existente(df, ["geometry_json", "geometria", "geometry"]),
        "fonte_url": _primeira_coluna_existente(df, ["fonte_url", "url"]),
    }
    out = pd.DataFrame({k: df[v] if v else None for k, v in mapa.items()})
    out["codigo_ibge"] = out["codigo_ibge"].map(_codigo_limpo)
    out["municipio"] = _normalizar_municipio(out["municipio"])
    out = _preencher_municipio_por_codigo_ibge(out)
    out = _filtrar_municipios_mt(out, "municipio")
    for col in ["latitude_centroide", "longitude_centroide", "min_latitude", "max_latitude", "min_longitude", "max_longitude", "quantidade_pontos"]:
        if col in out.columns:
            out[col] = _to_numeric(out[col])
    out["nivel_geografico"] = out["nivel_geografico"].fillna("municipio")
    out["fonte"] = fonte
    out["importacao_id"] = importacao_id
    out["atualizado_em"] = agora
    out = out.dropna(subset=["municipio", "codigo_ibge"], how="any").drop_duplicates(subset=["codigo_ibge"], keep="first")

    with db_session() as conn:
        conn.execute("DELETE FROM malhas_geograficas_municipais WHERE fonte = ?", (fonte,))
        out.to_sql("malhas_geograficas_municipais", conn, if_exists="append", index=False)

    return {"tabela": "malhas_geograficas_municipais", "linhas": len(out), "colunas": len(out.columns)}


def _importar_estabelecimentos_gerais_cnes(df: pd.DataFrame, importacao_id: int | None, fonte: str, agora: str) -> dict:
    mapa = {
        "codigo_ibge": _primeira_coluna_existente(df, ["codigo_ibge", "cod_ibge", "co_municipio", "codigo_municipio", "cod_municipio"]),
        "municipio": _primeira_coluna_existente(df, ["municipio", "no_municipio", "cidade", "nome_municipio"]),
        "cnes": _primeira_coluna_existente(df, ["cnes", "co_cnes", "codigo_cnes", "cod_cnes"]),
        "nome_unidade": _primeira_coluna_existente(df, ["nome_unidade", "nome_fantasia", "no_fantasia", "estabelecimento", "nome_estabelecimento"]),
        "razao_social": _primeira_coluna_existente(df, ["razao_social", "no_razao_social"]),
        "tipo_unidade": _primeira_coluna_existente(df, ["tipo_unidade", "ds_tipo_unidade", "descricao_tipo_unidade"]),
        "natureza_juridica": _primeira_coluna_existente(df, ["natureza_juridica", "ds_natureza_juridica", "natureza"]),
        "gestao": _primeira_coluna_existente(df, ["gestao", "tp_gestao", "tipo_gestao"]),
        "atende_sus": _primeira_coluna_existente(df, ["atende_sus", "st_atende_sus", "convenio_sus"]),
        "endereco": _primeira_coluna_existente(df, ["endereco", "logradouro", "no_logradouro"]),
        "bairro": _primeira_coluna_existente(df, ["bairro", "no_bairro"]),
        "cep": _primeira_coluna_existente(df, ["cep", "co_cep"]),
        "telefone": _primeira_coluna_existente(df, ["telefone", "nu_telefone"]),
        "latitude": _primeira_coluna_existente(df, ["latitude", "lat"]),
        "longitude": _primeira_coluna_existente(df, ["longitude", "long", "lon"]),
        "fonte_url": _primeira_coluna_existente(df, ["fonte_url", "url"]),
    }
    out = pd.DataFrame({k: df[v] if v else None for k, v in mapa.items()})
    if "codigo_ibge" in out.columns:
        out["codigo_ibge"] = out["codigo_ibge"].map(_codigo_limpo)
    if "municipio" in out.columns:
        out["municipio"] = _normalizar_municipio(out["municipio"])
    out = _preencher_municipio_por_codigo_ibge(out)
    out = _filtrar_municipios_mt(out, "municipio")
    for col in ["latitude", "longitude"]:
        if col in out.columns:
            out[col] = _to_numeric(out[col])
    out["fonte"] = fonte
    out["importacao_id"] = importacao_id
    out["atualizado_em"] = agora
    out = out.dropna(how="all")
    out = out[out["municipio"].astype(str).str.strip() != ""]
    out = out.drop_duplicates(subset=["codigo_ibge", "cnes", "nome_unidade"], keep="last")

    with db_session() as conn:
        if fonte:
            conn.execute("DELETE FROM cnes_estabelecimentos_gerais WHERE fonte = ?", (fonte,))
        if importacao_id is not None:
            conn.execute("DELETE FROM cnes_estabelecimentos_gerais WHERE importacao_id = ?", (importacao_id,))
        out.to_sql("cnes_estabelecimentos_gerais", conn, if_exists="append", index=False)
    return {"tabela": "cnes_estabelecimentos_gerais", "linhas": len(out), "colunas": len(out.columns)}


def _importar_catalogo_dados_abertos_mt(df: pd.DataFrame, importacao_id: int | None, fonte: str, agora: str) -> dict:
    """Importa inventário CKAN do Portal de Dados Abertos MT para tabela própria."""
    colunas = [
        "dataset_id", "dataset_nome", "dataset_titulo", "dataset_descricao",
        "organizacao_id", "organizacao_nome", "grupos", "tags",
        "recurso_id", "recurso_nome", "formato", "url", "mimetype",
        "ultima_modificacao", "criado_em", "api_ckan_package_show", "url_dataset_portal",
        "pontuacao_aps", "relevancia_aps", "fonte_consulta", "atualizado_em",
    ]
    out = df.copy()
    for col in colunas:
        if col not in out.columns:
            out[col] = None
    out = out[colunas]
    out["pontuacao_aps"] = _to_numeric(out["pontuacao_aps"])
    out["fonte"] = fonte
    out["importacao_id"] = importacao_id
    out["atualizado_em"] = agora
    out = out.dropna(how="all")
    if "url" in out.columns:
        out = out.drop_duplicates(subset=["dataset_nome", "recurso_id", "url"], keep="last")

    with db_session() as conn:
        if fonte:
            conn.execute("DELETE FROM dados_abertos_mt_catalogo WHERE fonte = ?", (fonte,))
        if importacao_id is not None:
            conn.execute("DELETE FROM dados_abertos_mt_catalogo WHERE importacao_id = ?", (importacao_id,))
        out.to_sql("dados_abertos_mt_catalogo", conn, if_exists="append", index=False)
    return {"tabela": "dados_abertos_mt_catalogo", "linhas": len(out), "colunas": len(out.columns), "observacao": "Inventário CKAN; não entra na Base Completa."}



def _importar_dados_mt_icqv_explorador(df: pd.DataFrame, importacao_id: int | None, fonte: str, agora: str) -> dict:
    colunas = [
        "produto", "origem_pagina", "titulo_contexto", "descricao_contexto",
        "url_powerbi", "chave_publicacao", "tenant_id", "status_http",
        "content_type", "endpoint_dados_detectado", "endpoints_candidatos", "observacao"
    ]
    out = pd.DataFrame()
    for col in colunas:
        out[col] = df[col] if col in df.columns else ""
    if "status_http" in out.columns:
        out["status_http"] = pd.to_numeric(out["status_http"], errors="coerce")
    out["fonte"] = fonte
    out["importacao_id"] = importacao_id
    out["atualizado_em"] = agora
    out = out.drop_duplicates(subset=["url_powerbi", "origem_pagina"], keep="last")

    with db_session() as conn:
        if fonte:
            conn.execute("DELETE FROM dados_mt_icqv_explorador WHERE fonte = ?", (fonte,))
        if importacao_id is not None:
            conn.execute("DELETE FROM dados_mt_icqv_explorador WHERE importacao_id = ?", (importacao_id,))
        out.to_sql("dados_mt_icqv_explorador", conn, if_exists="append", index=False)
    return {"tabela": "dados_mt_icqv_explorador", "linhas": len(out), "colunas": len(out.columns)}



def _enriquecer_assentamentos_por_malha(out: pd.DataFrame) -> pd.DataFrame:
    """Preenche município/código IBGE usando a malha municipal já carregada.

    A base de assentamentos pode vir apenas com geometria, sem atributo municipal.
    Para a camada territorial ficar útil, inferimos o município quando o centroide
    do assentamento cai dentro do bbox da malha municipal. Em caso de empate,
    usa o centroide municipal mais próximo.
    """
    if out.empty or not {"latitude_centroide", "longitude_centroide"}.issubset(out.columns):
        return out
    precisa = (out.get("municipio", "").astype(str).str.strip() == "") | (out.get("codigo_ibge", "").astype(str).str.strip() == "")
    if not bool(precisa.any()):
        return out
    try:
        with db_session() as conn:
            malhas = pd.read_sql_query(
                """
                SELECT codigo_ibge, municipio, latitude_centroide, longitude_centroide,
                       min_latitude, max_latitude, min_longitude, max_longitude
                FROM malhas_geograficas_municipais
                WHERE latitude_centroide IS NOT NULL AND longitude_centroide IS NOT NULL
                """,
                conn,
            )
    except Exception:
        return out
    if malhas.empty:
        return out
    for c in ["latitude_centroide", "longitude_centroide", "min_latitude", "max_latitude", "min_longitude", "max_longitude"]:
        if c in malhas.columns:
            malhas[c] = pd.to_numeric(malhas[c], errors="coerce")

    for idx, row in out.loc[precisa].iterrows():
        lat = pd.to_numeric(row.get("latitude_centroide"), errors="coerce")
        lon = pd.to_numeric(row.get("longitude_centroide"), errors="coerce")
        if pd.isna(lat) or pd.isna(lon):
            continue
        candidatos = malhas[
            (malhas["min_latitude"] <= lat) & (malhas["max_latitude"] >= lat) &
            (malhas["min_longitude"] <= lon) & (malhas["max_longitude"] >= lon)
        ].copy()
        if candidatos.empty:
            candidatos = malhas.copy()
        candidatos["_dist"] = (candidatos["latitude_centroide"] - lat).abs() + (candidatos["longitude_centroide"] - lon).abs()
        melhor = candidatos.sort_values("_dist").iloc[0]
        if not str(out.at[idx, "municipio"] or "").strip():
            out.at[idx, "municipio"] = str(melhor.get("municipio") or "").strip()
        if not str(out.at[idx, "codigo_ibge"] or "").strip():
            out.at[idx, "codigo_ibge"] = str(melhor.get("codigo_ibge") or "").strip()
        obs = str(out.at[idx, "observacao"] or "")
        if "Município inferido pela malha" not in obs:
            out.at[idx, "observacao"] = (obs + " Município inferido pela malha municipal a partir do centroide.").strip()
    return out


def _importar_assentamentos_intermt(df: pd.DataFrame, importacao_id: int | None, fonte: str, agora: str) -> dict:
    colunas = [
        "nome_assentamento", "municipio", "codigo_ibge", "area_ha", "modalidade", "situacao",
        "latitude_centroide", "longitude_centroide", "min_latitude", "max_latitude",
        "min_longitude", "max_longitude", "fonte_url", "arquivo_origem", "observacao", "atributos_json"
    ]
    out = pd.DataFrame()
    for col in colunas:
        out[col] = df[col] if col in df.columns else None
    for col in ["area_ha", "latitude_centroide", "longitude_centroide", "min_latitude", "max_latitude", "min_longitude", "max_longitude"]:
        out[col] = _to_numeric(out[col])
    for col in ["nome_assentamento", "municipio", "codigo_ibge", "modalidade", "situacao", "fonte_url", "arquivo_origem", "observacao", "atributos_json"]:
        out[col] = out[col].fillna("").astype(str).str.strip()

    # Quando a base territorial não informa município, usa a malha municipal já carregada
    # para inferir município/código IBGE pelo centroide.
    out = _enriquecer_assentamentos_por_malha(out)

    out["fonte"] = fonte
    out["importacao_id"] = importacao_id
    out["atualizado_em"] = agora
    out = out.dropna(how="all")
    if "nome_assentamento" in out.columns:
        out = out[out["nome_assentamento"].astype(str).str.strip() != ""]
    out = out.drop_duplicates(subset=["nome_assentamento", "municipio", "latitude_centroide", "longitude_centroide"], keep="last")
    with db_session() as conn:
        if fonte:
            conn.execute("DELETE FROM dados_mt_assentamentos WHERE fonte = ?", (fonte,))
        if importacao_id is not None:
            conn.execute("DELETE FROM dados_mt_assentamentos WHERE importacao_id = ?", (importacao_id,))
        out.to_sql("dados_mt_assentamentos", conn, if_exists="append", index=False)
    return {
        "tabela": "dados_mt_assentamentos",
        "linhas": len(out),
        "colunas": len(out.columns),
        "municipios_preenchidos": int(out["municipio"].astype(str).str.strip().replace("", pd.NA).notna().sum()) if "municipio" in out.columns else 0,
        "nomes_genericos": int(out["nome_assentamento"].astype(str).str.startswith("Assentamento sem nome").sum()) if "nome_assentamento" in out.columns else 0,
        "observacao": "Camada territorial de assentamentos importada em tabela própria. Não entra automaticamente na Base Completa."
    }


def _enriquecer_terras_indigenas_por_malha(out: pd.DataFrame) -> pd.DataFrame:
    """Preenche município principal/código IBGE e lista municípios possivelmente abrangidos.

    Como a camada de terras indígenas pode abranger mais de um município, usamos a
    sobreposição entre o bbox da terra indígena e o bbox da malha municipal. A
    coluna municipio/codigo_ibge recebe um município de referência pelo centroide,
    enquanto municipios_intersectados guarda a lista para leitura territorial.
    """
    if out.empty:
        return out
    try:
        with db_session() as conn:
            malhas = pd.read_sql_query(
                """
                SELECT codigo_ibge, municipio, latitude_centroide, longitude_centroide,
                       min_latitude, max_latitude, min_longitude, max_longitude
                FROM malhas_geograficas_municipais
                WHERE latitude_centroide IS NOT NULL AND longitude_centroide IS NOT NULL
                """,
                conn,
            )
    except Exception:
        return out
    if malhas.empty:
        return out
    for c in ["latitude_centroide", "longitude_centroide", "min_latitude", "max_latitude", "min_longitude", "max_longitude"]:
        if c in malhas.columns:
            malhas[c] = pd.to_numeric(malhas[c], errors="coerce")
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")

    for idx, row in out.iterrows():
        lat = pd.to_numeric(row.get("latitude_centroide"), errors="coerce")
        lon = pd.to_numeric(row.get("longitude_centroide"), errors="coerce")
        min_lat = pd.to_numeric(row.get("min_latitude"), errors="coerce")
        max_lat = pd.to_numeric(row.get("max_latitude"), errors="coerce")
        min_lon = pd.to_numeric(row.get("min_longitude"), errors="coerce")
        max_lon = pd.to_numeric(row.get("max_longitude"), errors="coerce")

        intersectados = pd.DataFrame()
        if not any(pd.isna(x) for x in [min_lat, max_lat, min_lon, max_lon]):
            intersectados = malhas[
                (malhas["min_latitude"] <= max_lat) & (malhas["max_latitude"] >= min_lat) &
                (malhas["min_longitude"] <= max_lon) & (malhas["max_longitude"] >= min_lon)
            ].copy()
        if not intersectados.empty:
            nomes = sorted(set(intersectados["municipio"].fillna("").astype(str).str.strip()) - {""})
            out.at[idx, "municipios_intersectados"] = "; ".join(nomes)

        candidatos = intersectados.copy() if not intersectados.empty else malhas.copy()
        if not pd.isna(lat) and not pd.isna(lon) and not candidatos.empty:
            candidatos["_dist"] = (candidatos["latitude_centroide"] - lat).abs() + (candidatos["longitude_centroide"] - lon).abs()
            melhor = candidatos.sort_values("_dist").iloc[0]
            if not str(out.at[idx, "municipio"] or "").strip():
                out.at[idx, "municipio"] = str(melhor.get("municipio") or "").strip()
            if not str(out.at[idx, "codigo_ibge"] or "").strip():
                out.at[idx, "codigo_ibge"] = str(melhor.get("codigo_ibge") or "").strip()
            obs = str(out.at[idx, "observacao"] or "")
            if "Município de referência inferido" not in obs:
                out.at[idx, "observacao"] = (obs + " Município de referência inferido pela malha municipal a partir do centroide; conferir municípios_intersectados para abrangência territorial.").strip()
    return out


def _importar_terras_indigenas_intermt(df: pd.DataFrame, importacao_id: int | None, fonte: str, agora: str) -> dict:
    colunas = [
        "nome_terra_indigena", "etnia", "municipio", "codigo_ibge", "municipios_intersectados",
        "area_ha", "situacao", "latitude_centroide", "longitude_centroide",
        "min_latitude", "max_latitude", "min_longitude", "max_longitude",
        "fonte_url", "arquivo_origem", "observacao", "atributos_json"
    ]
    out = pd.DataFrame()
    for col in colunas:
        out[col] = df[col] if col in df.columns else None
    for col in ["area_ha", "latitude_centroide", "longitude_centroide", "min_latitude", "max_latitude", "min_longitude", "max_longitude"]:
        out[col] = _to_numeric(out[col])
    for col in ["nome_terra_indigena", "etnia", "municipio", "codigo_ibge", "municipios_intersectados", "situacao", "fonte_url", "arquivo_origem", "observacao", "atributos_json"]:
        out[col] = out[col].fillna("").astype(str).str.strip()

    out = _enriquecer_terras_indigenas_por_malha(out)
    out["fonte"] = fonte
    out["importacao_id"] = importacao_id
    out["atualizado_em"] = agora
    out = out.dropna(how="all")
    if "nome_terra_indigena" in out.columns:
        out = out[out["nome_terra_indigena"].astype(str).str.strip() != ""]
    out = out.drop_duplicates(subset=["nome_terra_indigena", "latitude_centroide", "longitude_centroide"], keep="last")
    with db_session() as conn:
        if fonte:
            conn.execute("DELETE FROM dados_mt_terras_indigenas WHERE fonte = ?", (fonte,))
        if importacao_id is not None:
            conn.execute("DELETE FROM dados_mt_terras_indigenas WHERE importacao_id = ?", (importacao_id,))
        out.to_sql("dados_mt_terras_indigenas", conn, if_exists="append", index=False)
    return {
        "tabela": "dados_mt_terras_indigenas",
        "linhas": len(out),
        "colunas": len(out.columns),
        "municipios_referencia_preenchidos": int(out["municipio"].astype(str).str.strip().replace("", pd.NA).notna().sum()) if "municipio" in out.columns else 0,
        "com_municipios_intersectados": int(out["municipios_intersectados"].astype(str).str.strip().replace("", pd.NA).notna().sum()) if "municipios_intersectados" in out.columns else 0,
        "observacao": "Camada territorial de terras indígenas importada em tabela própria. Não entra automaticamente na Base Completa."
    }


def _importar_areas_contaminadas_sema(df: pd.DataFrame, importacao_id: int | None, fonte: str, agora: str) -> dict:
    colunas = [
        "municipio", "codigo_ibge", "data_ocorrencia", "ano", "tipo_ocorrencia",
        "produto_residuo", "situacao", "descricao", "latitude", "longitude",
        "fonte_url", "dataset_titulo", "recurso_nome", "formato", "url_dataset_portal",
        "observacao", "atributos_json"
    ]
    out = pd.DataFrame()
    for col in colunas:
        out[col] = df[col] if col in df.columns else None
    for col in ["latitude", "longitude"]:
        out[col] = _to_numeric(out[col])
    if "ano" in out.columns:
        out["ano"] = pd.to_numeric(out["ano"], errors="coerce").astype("Int64")
    for col in ["municipio", "codigo_ibge", "data_ocorrencia", "tipo_ocorrencia", "produto_residuo", "situacao", "descricao", "fonte_url", "dataset_titulo", "recurso_nome", "formato", "url_dataset_portal", "observacao", "atributos_json"]:
        out[col] = out[col].fillna("").astype(str).str.strip()
    out = _enriquecer_areas_ambientais(out)
    out["fonte"] = fonte
    out["importacao_id"] = importacao_id
    out["atualizado_em"] = agora
    out = out.dropna(how="all")
    # Mantém apenas registros com algum conteúdo útil.
    uteis = ["municipio", "data_ocorrencia", "tipo_ocorrencia", "produto_residuo", "descricao", "latitude", "longitude"]
    mask = out[uteis].astype(str).apply(lambda s: s.str.strip().replace("None", "").replace("nan", "")).ne("").any(axis=1)
    out = out[mask].copy()
    out = out.drop_duplicates(subset=["municipio", "data_ocorrencia", "tipo_ocorrencia", "produto_residuo", "descricao"], keep="last")
    with db_session() as conn:
        if fonte:
            conn.execute("DELETE FROM dados_mt_areas_contaminadas WHERE fonte = ?", (fonte,))
        if importacao_id is not None:
            conn.execute("DELETE FROM dados_mt_areas_contaminadas WHERE importacao_id = ?", (importacao_id,))
        out.to_sql("dados_mt_areas_contaminadas", conn, if_exists="append", index=False)
    return {
        "tabela": "dados_mt_areas_contaminadas",
        "linhas": len(out),
        "colunas": len(out.columns),
        "municipios_preenchidos": int(out["municipio"].astype(str).str.strip().replace("", pd.NA).notna().sum()) if "municipio" in out.columns else 0,
        "com_coordenadas": int((pd.to_numeric(out.get("latitude"), errors="coerce").notna() & pd.to_numeric(out.get("longitude"), errors="coerce").notna()).sum()) if not out.empty else 0,
        "observacao": "Camada ambiental estadual importada em tabela própria. Não entra automaticamente na Base Completa."
    }


def _importar_compensacao_ambiental_sema(df: pd.DataFrame, importacao_id: int | None, fonte: str, agora: str) -> dict:
    colunas = [
        "municipio", "codigo_ibge", "processo", "empreendedor", "empreendimento",
        "tipo_compensacao", "valor", "situacao", "ano", "descricao",
        "latitude", "longitude", "fonte_url", "dataset_titulo", "recurso_nome",
        "formato", "url_dataset_portal", "observacao", "atributos_json"
    ]
    out = pd.DataFrame()
    for col in colunas:
        out[col] = df[col] if col in df.columns else None
    for col in ["valor", "latitude", "longitude"]:
        out[col] = _to_numeric(out[col])
    if "ano" in out.columns:
        out["ano"] = pd.to_numeric(out["ano"], errors="coerce").astype("Int64")
    texto_cols = [
        "municipio", "codigo_ibge", "processo", "empreendedor", "empreendimento",
        "tipo_compensacao", "situacao", "descricao", "fonte_url", "dataset_titulo",
        "recurso_nome", "formato", "url_dataset_portal", "observacao", "atributos_json"
    ]
    for col in texto_cols:
        out[col] = out[col].fillna("").astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    out = _enriquecer_areas_ambientais(out)
    out["fonte"] = fonte
    out["importacao_id"] = importacao_id
    out["atualizado_em"] = agora
    uteis = ["municipio", "processo", "empreendedor", "empreendimento", "tipo_compensacao", "situacao", "descricao"]
    mask = out[uteis].astype(str).apply(lambda s: s.str.strip().replace("None", "").replace("nan", "")).ne("").any(axis=1)
    out = out[mask].copy()
    out = out.drop_duplicates(subset=["processo", "empreendedor", "empreendimento", "municipio"], keep="last")
    with db_session() as conn:
        if fonte:
            conn.execute("DELETE FROM dados_mt_compensacao_ambiental WHERE fonte = ?", (fonte,))
        if importacao_id is not None:
            conn.execute("DELETE FROM dados_mt_compensacao_ambiental WHERE importacao_id = ?", (importacao_id,))
        out.to_sql("dados_mt_compensacao_ambiental", conn, if_exists="append", index=False)
    return {
        "tabela": "dados_mt_compensacao_ambiental",
        "linhas": len(out),
        "colunas": len(out.columns),
        "municipios_preenchidos": int(out["municipio"].astype(str).str.strip().replace("", pd.NA).notna().sum()) if "municipio" in out.columns else 0,
        "com_valor": int(pd.to_numeric(out.get("valor"), errors="coerce").notna().sum()) if not out.empty else 0,
        "observacao": "Camada de compensação ambiental importada em tabela própria. Não entra automaticamente na Base Completa."
    }


def importar_dataframe_estruturado(df: pd.DataFrame, tipo_base: str, importacao_id: int | None = None, fonte: str = "PLANILHAS_SES") -> dict:
    df = padronizar_dataframe(df)
    agora = datetime.now().isoformat(timespec="seconds")
    tipo = (tipo_base or "").lower()

    if tipo == "municipios":
        return _importar_municipios(df, importacao_id=importacao_id, fonte=fonte, agora=agora)

    if tipo == "leitos":
        return _importar_leitos_cnes(df, importacao_id=importacao_id, fonte=fonte, agora=agora)

    if tipo in ["populacao", "vulnerabilidade", "indicadores", "area_densidade"]:
        return _importar_indicadores(df, tipo=tipo, importacao_id=importacao_id, fonte=fonte, agora=agora)

    if tipo in ["malhas", "malhas_geograficas", "malhas_geograficas_municipais"]:
        return _importar_malhas_geograficas(df, importacao_id=importacao_id, fonte=fonte, agora=agora)

    if tipo in ["catalogo_dados_mt", "dados_abertos_mt_catalogo", "catalogo_ckan_mt"]:
        return _importar_catalogo_dados_abertos_mt(df, importacao_id=importacao_id, fonte=fonte, agora=agora)

    if tipo in ["dados_mt_icqv_explorador", "icqv_powerbi_explorador"]:
        return _importar_dados_mt_icqv_explorador(df, importacao_id=importacao_id, fonte=fonte, agora=agora)

    if tipo in ["dados_mt_assentamentos", "intermat_assentamentos", "assentamentos"]:
        return _importar_assentamentos_intermt(df, importacao_id=importacao_id, fonte=fonte, agora=agora)


    if tipo in ["dados_mt_terras_indigenas", "intermat_terras_indigenas", "terras_indigenas"]:
        return _importar_terras_indigenas_intermt(df, importacao_id=importacao_id, fonte=fonte, agora=agora)

    if tipo in ["dados_mt_areas_contaminadas", "sema_areas_contaminadas", "areas_contaminadas"]:
        return _importar_areas_contaminadas_sema(df, importacao_id=importacao_id, fonte=fonte, agora=agora)

    if tipo in ["dados_mt_compensacao_ambiental", "sema_compensacao_ambiental", "compensacao_ambiental"]:
        return _importar_compensacao_ambiental_sema(df, importacao_id=importacao_id, fonte=fonte, agora=agora)

    if tipo == "estabelecimentos":
        mapa = {
            "municipio": _primeira_coluna_existente(df, ["municipio", "no_municipio", "cidade"]),
            "codigo_ibge": _primeira_coluna_existente(df, ["codigo_ibge", "cod_ibge", "co_municipio", "codigo_municipio", "cod_municipio"]),
            "cnes": _primeira_coluna_existente(df, ["cnes", "co_cnes", "codigo_cnes"]),
            "nome_unidade": _primeira_coluna_existente(df, ["nome_unidade", "nome_unidade_cnes", "no_fantasia", "nome_fantasia", "estabelecimento", "unidade"]),
            "tipo_unidade": _primeira_coluna_existente(df, ["tipo_unidade", "tipo_unidade_cnes", "ds_tipo_unidade", "categoria_preliminar_cnes", "tipo"]),
            "endereco": _primeira_coluna_existente(df, ["endereco", "endereco_cnes", "logradouro", "endereco_unidade"]),
            "latitude": _primeira_coluna_existente(df, ["latitude", "lat"]),
            "longitude": _primeira_coluna_existente(df, ["longitude", "long", "lon"]),
        }
        tabela = "estabelecimentos_saude"

    elif tipo == "equipes":
        mapa = {
            "municipio": _primeira_coluna_existente(df, ["municipio", "no_municipio", "cidade"]),
            "codigo_ibge": _primeira_coluna_existente(df, ["codigo_ibge", "cod_ibge", "co_municipio", "codigo_municipio", "cod_municipio"]),
            "cnes": _primeira_coluna_existente(df, ["cnes", "co_cnes", "codigo_cnes"]),
            "ine": _primeira_coluna_existente(df, ["ine", "co_ine", "codigo_ine"]),
            "codigo_tipo_equipe": _primeira_coluna_existente(df, ["codigo_tipo_equipe", "co_tipo_equipe", "tipo_equipe_codigo", "codigo"]),
            "tipo_equipe": _primeira_coluna_existente(df, ["tipo_equipe", "ds_tipo_equipe", "descricao_tipo_equipe"]),
            "carga_horaria": _primeira_coluna_existente(df, ["carga_horaria", "ch", "qt_carga_horaria"]),
        }
        tabela = "equipes_aps"

    elif tipo == "profissionais":
        mapa = {
            "municipio": _primeira_coluna_existente(df, ["municipio", "no_municipio", "cidade"]),
            "codigo_ibge": _primeira_coluna_existente(df, ["codigo_ibge", "cod_ibge", "co_municipio", "codigo_municipio", "cod_municipio"]),
            "cnes": _primeira_coluna_existente(df, ["cnes", "co_cnes", "codigo_cnes"]),
            "ine": _primeira_coluna_existente(df, ["ine", "co_ine", "codigo_ine"]),
            "codigo_tipo_equipe": _primeira_coluna_existente(df, ["codigo_tipo_equipe", "co_tipo_equipe", "tipo_equipe_codigo", "codigo"]),
            "tipo_equipe": _primeira_coluna_existente(df, ["tipo_equipe", "ds_tipo_equipe", "descricao_tipo_equipe"]),
            "cbo": _primeira_coluna_existente(df, ["cbo", "co_cbo", "codigo_cbo"]),
            "nome_profissional": _primeira_coluna_existente(df, ["nome_profissional", "profissional", "no_profissional", "nome"]),
            "carga_horaria": _primeira_coluna_existente(df, ["carga_horaria", "ch", "qt_carga_horaria"]),
        }
        tabela = "profissionais_cnes"
    else:
        raise ValueError(f"Tipo de base não reconhecido: {tipo_base}")

    out = pd.DataFrame({k: df[v] if v else None for k, v in mapa.items()})
    if "codigo_ibge" in out.columns:
        out["codigo_ibge"] = out["codigo_ibge"].map(_codigo_limpo)
    if "municipio" in out.columns:
        out["municipio"] = _normalizar_municipio(out["municipio"])
    out = _preencher_municipio_por_codigo_ibge(out)
    if "municipio" in out.columns:
        out = _filtrar_municipios_mt(out, "municipio")
    if "codigo_tipo_equipe" in out.columns:
        out["codigo_tipo_equipe"] = out["codigo_tipo_equipe"].astype(str).str.extract(r"(\d+)", expand=False).fillna("")
        out["tipo_equipe"] = out.apply(lambda r: TIPOS_EQUIPE_CNES.get(str(r["codigo_tipo_equipe"]), r.get("tipo_equipe", "")), axis=1)

    out = out.dropna(how="all")
    if "municipio" in out.columns:
        out = out[out["municipio"].astype(str).str.strip() != ""]

    out["fonte"] = fonte
    out["importacao_id"] = importacao_id
    out["atualizado_em"] = agora

    with db_session() as conn:
        # Para cargas CNES recorrentes, substitui a carga anterior da mesma fonte
        # evitando duplicidade quando o bloco é reprocessado.
        if tabela in ["equipes_aps", "profissionais_cnes"] and fonte:
            try:
                conn.execute(f"DELETE FROM {tabela} WHERE fonte = ?", (fonte,))
            except Exception:
                pass
        out.to_sql(tabela, conn, if_exists="append", index=False)
    return {"tabela": tabela, "linhas": len(out), "colunas": len(out.columns)}
