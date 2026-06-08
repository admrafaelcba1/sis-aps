import unicodedata
import pandas as pd


def remover_acentos(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto))
    return "".join(char for char in texto if not unicodedata.combining(char))


def chave_municipio(texto) -> str:
    """
    Cria uma chave padronizada para cruzamento de nomes de municípios.

    Resolve diferenças como:
    - ACORIZAL x Acorizal
    - Pontes e Lacerda x PONTES E LACERDA
    - acentos
    - apóstrofos
    - espaços duplicados
    """
    if pd.isna(texto):
        return ""

    texto = remover_acentos(str(texto))
    texto = texto.upper().strip()

    substituicoes = {
        "’": "'",
        "`": "'",
        "´": "'",
        " D OESTE": " D'OESTE",
        " D OESTE": " D'OESTE",
    }

    for antigo, novo in substituicoes.items():
        texto = texto.replace(antigo, novo)

    texto = " ".join(texto.split())
    return texto


def anexar_chave_municipio(df: pd.DataFrame, coluna: str = "municipio") -> pd.DataFrame:
    df = df.copy()
    df["_chave_municipio"] = df[coluna].apply(chave_municipio)
    return df


def cruzar_com_municipios(df_base: pd.DataFrame, municipios: pd.DataFrame) -> pd.DataFrame:
    """
    Cruza qualquer base com o cadastro de municípios usando chave normalizada,
    evitando perda de Região/ERS quando o nome vem em caixa alta ou sem acento.
    """
    df = anexar_chave_municipio(df_base, "municipio")

    municipios_base = municipios.copy()
    municipios_base["_chave_municipio"] = municipios_base["municipio"].apply(chave_municipio)

    municipios_base = municipios_base[
        ["_chave_municipio", "municipio", "regiao_saude", "escritorio_regional"]
    ].drop_duplicates("_chave_municipio")

    resultado = df.merge(
        municipios_base,
        on="_chave_municipio",
        how="left",
        suffixes=("", "_cadastro"),
    )

    resultado["municipio_exibicao"] = resultado["municipio_cadastro"].fillna(resultado["municipio"])
    resultado["municipio_original"] = resultado["municipio"]
    resultado["municipio"] = resultado["municipio_exibicao"]

    resultado = resultado.drop(
        columns=[
            col for col in ["_chave_municipio", "municipio_cadastro", "municipio_exibicao"]
            if col in resultado.columns
        ]
    )

    return resultado