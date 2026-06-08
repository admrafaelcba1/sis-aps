import io
import math
import re
import unicodedata
from datetime import datetime
from typing import Optional

import pandas as pd
import streamlit as st

from ui.necessidade_ubs_ms import (
    PARAMETROS_MS_ESF,
    _normalizar_texto,
    obter_base_populacional,
    calcular_necessidade_ms,
)


COLUNAS_EXPORTACAO = [
    "codigo_ibge",
    "municipio",
    "regiao_saude",
    "populacao_ibge",
    "faixa_populacional_ms",
    "parametro_pessoas_por_esf",
    "esf_necessarias_ms",
    "esf_existentes",
    "deficit_esf",
    "ubs_existentes_informadas",
    "populacao_por_ubs",
    "ubs_estimadas_ms_referencia",
    "necessidade_ubs_preliminar",
    "pontuacao_preliminar",
    "classificacao_preliminar",
    "situacao_analise",
    "observacao_tecnica",
]


def _padronizar_colunas(df: pd.DataFrame) -> dict:
    mapa = {}
    for col in df.columns:
        chave = str(col).strip().lower()
        chave = unicodedata.normalize("NFKD", chave)
        chave = "".join(ch for ch in chave if not unicodedata.combining(ch))
        chave = re.sub(r"[^a-z0-9]+", "_", chave).strip("_")
        mapa[chave] = col
    return mapa


def _ler_arquivo_tabela(arquivo) -> pd.DataFrame:
    nome = arquivo.name.lower()
    if nome.endswith(".csv"):
        return pd.read_csv(arquivo)
    return pd.read_excel(arquivo)



def _ler_tabela_colada(texto: str) -> pd.DataFrame:
    """Lê tabela copiada do Excel/e-Gestor/CNES.

    Aceita conteúdo separado por tabulação, ponto e vírgula ou vírgula.
    A primeira linha deve conter os cabeçalhos.
    """
    texto = (texto or "").strip()
    if not texto:
        return pd.DataFrame()

    # Excel/LibreOffice geralmente cola como TSV.
    for sep in ["\t", ";", ","]:
        try:
            df = pd.read_csv(io.StringIO(texto), sep=sep)
            if len(df.columns) >= 2 and not df.empty:
                return df
        except Exception:
            pass
    raise ValueError("Não consegui ler a tabela colada. Copie com cabeçalho e colunas separadas, preferencialmente direto do Excel.")


def preparar_texto_colado_esf(texto: str) -> pd.DataFrame:
    """Prepara eSF a partir de texto colado na tela."""
    if not texto or not texto.strip():
        return pd.DataFrame(columns=["codigo_ibge", "municipio", "esf_existentes", "fonte_esf"])

    class ArquivoTexto:
        name = "tabela_colada_esf"

    df = _ler_tabela_colada(texto)
    # Reaproveita a mesma lógica do upload criando uma leitura em memória.
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    buffer.seek(0)
    fake = ArquivoTexto()
    fake.name = "tabela_colada_esf.xlsx"
    fake.read = buffer.read
    # pandas/openpyxl precisa de arquivo com interface completa; usar BytesIO diretamente com name.
    buffer.name = fake.name
    saida = preparar_upload_esf(buffer)
    saida["fonte_esf"] = "Tabela colada manualmente"
    return saida

def preparar_upload_esf(arquivo) -> pd.DataFrame:
    if arquivo is None:
        return pd.DataFrame(columns=["codigo_ibge", "municipio", "esf_existentes", "fonte_esf"])

    df = _ler_arquivo_tabela(arquivo)
    cols = _padronizar_colunas(df)

    col_codigo = None
    for candidato in ["codigo_ibge", "cod_ibge", "ibge", "codigo_municipio", "cod_municipio", "codigo"]:
        if candidato in cols:
            col_codigo = cols[candidato]
            break

    col_municipio = None
    for candidato in ["municipio", "nome_municipio", "nome_do_municipio"]:
        if candidato in cols:
            col_municipio = cols[candidato]
            break

    col_esf = None
    for candidato in [
        "esf_existentes",
        "qtd_esf",
        "quantidade_esf",
        "equipes_esf",
        "equipes_saude_familia",
        "equipes_de_saude_da_familia",
        "esf",
        "e_sf",
    ]:
        if candidato in cols:
            col_esf = cols[candidato]
            break

    if col_codigo is None and col_municipio is None:
        raise ValueError("A planilha de eSF precisa ter Código IBGE ou Município.")
    if col_esf is None:
        raise ValueError("A planilha de eSF precisa ter uma coluna de quantidade de equipes, como esf_existentes ou qtd_esf.")

    saida = pd.DataFrame()
    saida["codigo_ibge"] = df[col_codigo].astype(str).str.replace(r"\D", "", regex=True) if col_codigo else ""
    saida["municipio"] = df[col_municipio].astype(str).str.strip() if col_municipio else ""
    saida["municipio_normalizado"] = saida["municipio"].map(_normalizar_texto)
    saida["esf_existentes"] = pd.to_numeric(df[col_esf], errors="coerce").fillna(0).astype(int)
    saida["fonte_esf"] = f"Upload: {arquivo.name}"
    saida = saida[(saida["codigo_ibge"].astype(str).str.len() > 0) | (saida["municipio"].astype(str).str.len() > 0)].copy()

    if saida.empty:
        raise ValueError("A planilha de eSF foi lida, mas não gerou registros válidos.")

    return saida


def preparar_upload_ubs(arquivo) -> pd.DataFrame:
    if arquivo is None:
        return pd.DataFrame(columns=["codigo_ibge", "municipio", "ubs_existentes_informadas", "fonte_ubs"])

    df = _ler_arquivo_tabela(arquivo)
    cols = _padronizar_colunas(df)

    col_codigo = None
    for candidato in ["codigo_ibge", "cod_ibge", "ibge", "codigo_municipio", "cod_municipio", "codigo"]:
        if candidato in cols:
            col_codigo = cols[candidato]
            break

    col_municipio = None
    for candidato in ["municipio", "nome_municipio", "nome_do_municipio"]:
        if candidato in cols:
            col_municipio = cols[candidato]
            break

    col_qtd = None
    for candidato in [
        "ubs_existentes",
        "qtd_ubs",
        "quantidade_ubs",
        "ubs",
        "unidades_basicas",
        "unidades_basicas_de_saude",
        "quantidade_unidades",
    ]:
        if candidato in cols:
            col_qtd = cols[candidato]
            break

    # Caso o usuário mande uma base CNES/unidade a unidade, contar linhas por município.
    if col_qtd is None:
        if col_codigo is None and col_municipio is None:
            raise ValueError("A planilha de UBS/CNES precisa ter Código IBGE ou Município.")
        tmp = pd.DataFrame()
        tmp["codigo_ibge"] = df[col_codigo].astype(str).str.replace(r"\D", "", regex=True) if col_codigo else ""
        tmp["municipio"] = df[col_municipio].astype(str).str.strip() if col_municipio else ""
        tmp["municipio_normalizado"] = tmp["municipio"].map(_normalizar_texto)
        agrupadores = ["codigo_ibge"] if tmp["codigo_ibge"].astype(str).str.len().gt(0).any() else ["municipio_normalizado"]
        saida = tmp.groupby(agrupadores, as_index=False).size().rename(columns={"size": "ubs_existentes_informadas"})
        if "municipio_normalizado" in saida.columns:
            nomes = tmp.drop_duplicates("municipio_normalizado")[["municipio_normalizado", "municipio"]]
            saida = saida.merge(nomes, on="municipio_normalizado", how="left")
            saida["codigo_ibge"] = ""
        else:
            nomes = tmp.drop_duplicates("codigo_ibge")[["codigo_ibge", "municipio"]]
            saida = saida.merge(nomes, on="codigo_ibge", how="left")
        saida["fonte_ubs"] = f"Upload contado por linhas: {arquivo.name}"
        return saida

    if col_codigo is None and col_municipio is None:
        raise ValueError("A planilha de UBS precisa ter Código IBGE ou Município.")

    saida = pd.DataFrame()
    saida["codigo_ibge"] = df[col_codigo].astype(str).str.replace(r"\D", "", regex=True) if col_codigo else ""
    saida["municipio"] = df[col_municipio].astype(str).str.strip() if col_municipio else ""
    saida["municipio_normalizado"] = saida["municipio"].map(_normalizar_texto)
    saida["ubs_existentes_informadas"] = pd.to_numeric(df[col_qtd], errors="coerce").fillna(0).astype(int)
    saida["fonte_ubs"] = f"Upload: {arquivo.name}"
    saida = saida[(saida["codigo_ibge"].astype(str).str.len() > 0) | (saida["municipio"].astype(str).str.len() > 0)].copy()
    return saida


def _mesclar_por_codigo_ou_municipio(base: pd.DataFrame, complemento: pd.DataFrame, colunas: list[str]) -> pd.DataFrame:
    if complemento is None or complemento.empty:
        for col in colunas:
            if col not in base.columns:
                base[col] = 0 if col.endswith("existentes") or col.endswith("informadas") else ""
        return base

    df = base.copy()
    comp = complemento.copy()

    if "municipio_normalizado" not in df.columns:
        df["municipio_normalizado"] = df["municipio"].map(_normalizar_texto)
    if "municipio_normalizado" not in comp.columns and "municipio" in comp.columns:
        comp["municipio_normalizado"] = comp["municipio"].map(_normalizar_texto)

    usar_codigo = (
        "codigo_ibge" in comp.columns
        and comp["codigo_ibge"].astype(str).str.len().gt(0).any()
        and "codigo_ibge" in df.columns
    )

    if usar_codigo:
        comp_codigo = comp[comp["codigo_ibge"].astype(str).str.len() > 0].copy()
        df = df.merge(comp_codigo[["codigo_ibge"] + colunas], on="codigo_ibge", how="left")
    else:
        df = df.merge(comp[["municipio_normalizado"] + colunas], on="municipio_normalizado", how="left")

    return df


def classificar_prioridade(pontos: int) -> str:
    if pontos >= 80:
        return "Crítica / validar urgente"
    if pontos >= 60:
        return "Alta prioridade preliminar"
    if pontos >= 40:
        return "Média prioridade preliminar"
    return "Baixa prioridade preliminar"


def calcular_diagnostico_preliminar(
    df_ms: pd.DataFrame,
    df_esf: pd.DataFrame,
    df_ubs: pd.DataFrame,
    capacidade_referencia_ubs: int,
    limite_pop_ubs_medio: int,
    limite_pop_ubs_alto: int,
) -> pd.DataFrame:
    df = df_ms.copy()
    df["municipio_normalizado"] = df["municipio"].map(_normalizar_texto)

    df = _mesclar_por_codigo_ou_municipio(df, df_esf, ["esf_existentes", "fonte_esf"])
    df = _mesclar_por_codigo_ou_municipio(df, df_ubs, ["ubs_existentes_informadas", "fonte_ubs"])

    df["esf_existentes"] = pd.to_numeric(df.get("esf_existentes", 0), errors="coerce").fillna(0).astype(int)
    df["ubs_existentes_informadas"] = pd.to_numeric(df.get("ubs_existentes_informadas", 0), errors="coerce").fillna(0).astype(int)
    df["fonte_esf"] = df.get("fonte_esf", "").fillna("")
    df["fonte_ubs"] = df.get("fonte_ubs", "").fillna("")

    df["deficit_esf"] = (df["esf_necessarias_ms"] - df["esf_existentes"]).clip(lower=0)
    df["populacao_por_ubs"] = df.apply(
        lambda row: int(math.ceil(row["populacao_ibge"] / row["ubs_existentes_informadas"]))
        if row["ubs_existentes_informadas"] > 0
        else 0,
        axis=1,
    )
    df["necessidade_ubs_preliminar"] = df.apply(
        lambda row: int(math.ceil(row["deficit_esf"] / capacidade_referencia_ubs))
        if row["deficit_esf"] > 0 and capacidade_referencia_ubs > 0
        else 0,
        axis=1,
    )

    def pontuar(row) -> int:
        pontos = 0
        if row["deficit_esf"] > 0:
            pontos += 40
        if row["deficit_esf"] >= 3:
            pontos += 10
        if row["ubs_existentes_informadas"] == 0:
            pontos += 50
        elif row["populacao_por_ubs"] >= limite_pop_ubs_alto:
            pontos += 30
        elif row["populacao_por_ubs"] >= limite_pop_ubs_medio:
            pontos += 20
        if row["populacao_ibge"] <= 20_000 and row["ubs_existentes_informadas"] <= 1 and row["deficit_esf"] > 0:
            pontos += 10
        return min(int(pontos), 100)

    df["pontuacao_preliminar"] = df.apply(pontuar, axis=1)
    df["classificacao_preliminar"] = df["pontuacao_preliminar"].apply(classificar_prioridade)
    df["situacao_analise"] = df.apply(
        lambda row: "Aguardando dados de eSF" if not row["fonte_esf"] else (
            "Aguardando dados de UBS/CNES" if not row["fonte_ubs"] else "Diagnóstico preliminar calculado"
        ),
        axis=1,
    )
    df["observacao_tecnica"] = df.apply(
        lambda row: (
            "Resultado preliminar. A indicação de construção depende de adesão municipal, terreno disponível, validação territorial e decisão institucional da SES."
        ),
        axis=1,
    )

    df = df.sort_values(["pontuacao_preliminar", "deficit_esf", "populacao_ibge"], ascending=[False, False, False]).reset_index(drop=True)
    return df



def validar_fonte_esf(df_base: pd.DataFrame, df_esf: pd.DataFrame) -> dict:
    """Retorna indicadores simples de qualidade da base de eSF importada."""
    if df_esf is None or df_esf.empty:
        return {
            "registros_esf": 0,
            "municipios_com_esf": 0,
            "municipios_sem_esf": len(df_base),
            "duplicidades": 0,
            "total_esf_importadas": 0,
        }

    temp = df_esf.copy()
    if "municipio_normalizado" not in temp.columns and "municipio" in temp.columns:
        temp["municipio_normalizado"] = temp["municipio"].map(_normalizar_texto)

    chave = "codigo_ibge" if "codigo_ibge" in temp.columns and temp["codigo_ibge"].astype(str).str.len().gt(0).any() else "municipio_normalizado"
    duplicidades = int(temp.duplicated(subset=[chave]).sum()) if chave in temp.columns else 0
    municipios_com_esf = temp[chave].astype(str).replace("", pd.NA).dropna().nunique() if chave in temp.columns else 0
    return {
        "registros_esf": int(len(temp)),
        "municipios_com_esf": int(municipios_com_esf),
        "municipios_sem_esf": int(max(len(df_base) - municipios_com_esf, 0)),
        "duplicidades": duplicidades,
        "total_esf_importadas": int(pd.to_numeric(temp.get("esf_existentes", 0), errors="coerce").fillna(0).sum()),
    }

def gerar_modelo_esf() -> bytes:
    modelo = pd.DataFrame(
        {
            "codigo_ibge": ["5103403"],
            "municipio": ["Cuiabá"],
            "esf_existentes": [0],
            "observacao": ["Preencher com a quantidade validada pela Coordenadoria APS"],
        }
    )
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        modelo.to_excel(writer, sheet_name="Modelo eSF", index=False)
    buffer.seek(0)
    return buffer.getvalue()


def gerar_modelo_ubs() -> bytes:
    modelo = pd.DataFrame(
        {
            "codigo_ibge": ["5103403"],
            "municipio": ["Cuiabá"],
            "ubs_existentes": [0],
            "observacao": ["Preencher com UBS cadastradas/validadas ou enviar base CNES unidade a unidade"],
        }
    )
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        modelo.to_excel(writer, sheet_name="Modelo UBS", index=False)
    buffer.seek(0)
    return buffer.getvalue()


def gerar_excel_download(df: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df[COLUNAS_EXPORTACAO].to_excel(writer, sheet_name="Diagnostico preliminar", index=False)
        pd.DataFrame(PARAMETROS_MS_ESF).to_excel(writer, sheet_name="Parametros MS", index=False)
        pd.DataFrame(
            [
                {"classificacao": "Baixa prioridade preliminar", "pontuacao": "0 a 39"},
                {"classificacao": "Média prioridade preliminar", "pontuacao": "40 a 59"},
                {"classificacao": "Alta prioridade preliminar", "pontuacao": "60 a 79"},
                {"classificacao": "Crítica / validar urgente", "pontuacao": "80 a 100"},
            ]
        ).to_excel(writer, sheet_name="Legenda", index=False)
    buffer.seek(0)
    return buffer.getvalue()


def render_diagnostico_ubs_ses():
    st.subheader("Diagnóstico preliminar SES/MT - Necessidade de UBS")

    st.markdown(
        """
        <div class="info-box">
        Esta tela é a ponte entre a visão teórica do Ministério da Saúde e a análise estadual. Ela cruza população IBGE,
        eSF necessárias pelo parâmetro federal, eSF existentes informadas pela Coordenadoria e UBS existentes informadas por upload/CNES.
        O resultado é apenas um <b>ranking preliminar</b>, antes da adesão municipal e da validação territorial.
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("Baixar modelos de preenchimento", expanded=False):
        c1, c2 = st.columns(2)
        c1.download_button(
            "Baixar modelo de eSF existentes",
            data=gerar_modelo_esf(),
            file_name="modelo_esf_existentes.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        c2.download_button(
            "Baixar modelo de UBS existentes",
            data=gerar_modelo_ubs(),
            file_name="modelo_ubs_existentes.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    st.markdown("### 1. Parâmetros do diagnóstico")
    p1, p2, p3, p4 = st.columns(4)
    ano = p1.number_input("Ano da população IBGE/SIDRA", min_value=2024, max_value=2026, value=2025, step=1)
    equipes_por_ubs = p2.number_input(
        "eSF por UBS de referência",
        min_value=1,
        max_value=6,
        value=2,
        step=1,
        help="Premissa operacional para estimar quantas UBS novas seriam necessárias para absorver déficit de equipes.",
    )
    limite_pop_ubs_medio = p3.number_input("Alerta médio: pop/UBS", min_value=1000, max_value=20000, value=4000, step=500)
    limite_pop_ubs_alto = p4.number_input("Alerta alto: pop/UBS", min_value=1000, max_value=30000, value=6000, step=500)

    st.markdown("### 2. Bases de entrada")
    c1, c2, c3 = st.columns(3)
    arquivo_pop = c1.file_uploader(
        "Opcional: população IBGE manual",
        type=["xlsx", "xls", "csv"],
        help="Use apenas se a API SIDRA não carregar. Colunas mínimas: Município e População.",
        key="pop_diag_ubs",
    )
    arquivo_esf = c2.file_uploader(
        "eSF existentes - Coordenadoria APS",
        type=["xlsx", "xls", "csv"],
        help="Colunas esperadas: codigo_ibge/municipio e esf_existentes. Também aceita exportações com nomes parecidos, como qtd_esf ou equipes_esf.",
        key="esf_diag_ubs",
    )
    arquivo_ubs = c3.file_uploader(
        "UBS existentes/CNES - opcional",
        type=["xlsx", "xls", "csv"],
        help="Pode ser uma planilha resumida com qtd_ubs ou uma base unidade a unidade para contagem automática.",
        key="ubs_diag_ubs",
    )

    with st.expander("Alternativa rápida: colar tabela de eSF exportada/copiar-colar", expanded=False):
        st.caption(
            "Use esta opção quando a Coordenadoria enviar os dados no corpo do e-mail, em tabela copiada do Excel, ou quando você conseguir copiar dados de relatório público/consulta CNES. A primeira linha precisa ser o cabeçalho."
        )
        texto_esf_colado = st.text_area(
            "Cole aqui uma tabela com Município/Código IBGE e quantidade de eSF",
            height=140,
            placeholder="codigo_ibge\tmunicipio\tesf_existentes\n5103403\tCuiabá\t0",
            key="texto_esf_colado_diag_ubs",
        )

    st.markdown(
        """
        <div class="warning-box">
        Nesta versão, distância, comunidades rurais, expansão urbana e terrenos ainda não entram no cálculo. Esses dados ficam para a etapa futura de adesão municipal.
        </div>
        """,
        unsafe_allow_html=True,
    )

    if arquivo_esf is None and not texto_esf_colado.strip():
        st.info("Para gerar um diagnóstico mais útil, envie a planilha de eSF existentes da Coordenadoria APS ou cole uma tabela de eSF. Sem esse dado, o déficit ficará superestimado.")

    try:
        with st.spinner("Calculando diagnóstico preliminar..."):
            df_pop = obter_base_populacional(int(ano), arquivo_pop)
            df_ms = calcular_necessidade_ms(df_pop, int(equipes_por_ubs))
            if texto_esf_colado.strip():
                df_esf = preparar_texto_colado_esf(texto_esf_colado)
            else:
                df_esf = preparar_upload_esf(arquivo_esf) if arquivo_esf is not None else pd.DataFrame()
            df_ubs = preparar_upload_ubs(arquivo_ubs) if arquivo_ubs is not None else pd.DataFrame()
            df = calcular_diagnostico_preliminar(
                df_ms=df_ms,
                df_esf=df_esf,
                df_ubs=df_ubs,
                capacidade_referencia_ubs=int(equipes_por_ubs),
                limite_pop_ubs_medio=int(limite_pop_ubs_medio),
                limite_pop_ubs_alto=int(limite_pop_ubs_alto),
            )
    except Exception as exc:
        st.error("Não foi possível gerar o diagnóstico preliminar.")
        st.exception(exc)
        return

    qualidade_esf = validar_fonte_esf(df_ms, df_esf)

    with st.expander("Qualidade da base de eSF importada", expanded=False):
        q1, q2, q3, q4, q5 = st.columns(5)
        q1.metric("Registros lidos", qualidade_esf["registros_esf"])
        q2.metric("Municípios com eSF", qualidade_esf["municipios_com_esf"])
        q3.metric("Municípios sem eSF", qualidade_esf["municipios_sem_esf"])
        q4.metric("Duplicidades", qualidade_esf["duplicidades"])
        q5.metric("Total eSF importadas", qualidade_esf["total_esf_importadas"])
        if qualidade_esf["duplicidades"] > 0:
            st.warning("A base de eSF possui possíveis duplicidades por município/código IBGE. Revise antes de usar como referência decisória.")
        if qualidade_esf["municipios_sem_esf"] > 0:
            st.caption("Municípios sem eSF importada serão tratados como 0 na análise preliminar, o que pode superestimar o déficit.")

    total_municipios = len(df)
    total_esf_ms = int(df["esf_necessarias_ms"].sum())
    total_esf_exist = int(df["esf_existentes"].sum())
    total_deficit = int(df["deficit_esf"].sum())
    total_ubs_preliminar = int(df["necessidade_ubs_preliminar"].sum())

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Municípios", total_municipios)
    m2.metric("eSF necessárias MS", f"{total_esf_ms:,.0f}".replace(",", "."))
    m3.metric("eSF existentes", f"{total_esf_exist:,.0f}".replace(",", "."))
    m4.metric("Déficit preliminar eSF", f"{total_deficit:,.0f}".replace(",", "."))
    m5.metric("UBS novas preliminares", f"{total_ubs_preliminar:,.0f}".replace(",", "."))

    st.markdown("### 3. Filtros")
    regioes = ["Todas"] + sorted(df["regiao_saude"].dropna().unique().tolist())
    classificacoes = ["Todas"] + [
        "Crítica / validar urgente",
        "Alta prioridade preliminar",
        "Média prioridade preliminar",
        "Baixa prioridade preliminar",
    ]

    f1, f2, f3 = st.columns(3)
    filtro_regiao = f1.selectbox("Região de Saúde", regioes, key="regiao_diag_ubs")
    filtro_classificacao = f2.selectbox("Classificação", classificacoes, key="classe_diag_ubs")
    busca = f3.text_input("Buscar município", key="busca_diag_ubs")

    tabela = df.copy()
    if filtro_regiao != "Todas":
        tabela = tabela[tabela["regiao_saude"] == filtro_regiao]
    if filtro_classificacao != "Todas":
        tabela = tabela[tabela["classificacao_preliminar"] == filtro_classificacao]
    if busca.strip():
        termo = _normalizar_texto(busca)
        tabela = tabela[tabela["municipio"].map(_normalizar_texto).str.contains(termo, na=False)]

    colunas_visiveis = [
        "municipio",
        "regiao_saude",
        "populacao_ibge",
        "esf_necessarias_ms",
        "esf_existentes",
        "deficit_esf",
        "ubs_existentes_informadas",
        "populacao_por_ubs",
        "necessidade_ubs_preliminar",
        "pontuacao_preliminar",
        "classificacao_preliminar",
        "situacao_analise",
    ]

    st.markdown("### 4. Ranking preliminar")
    st.dataframe(
        tabela[colunas_visiveis],
        use_container_width=True,
        hide_index=True,
        column_config={
            "municipio": "Município",
            "regiao_saude": "Região de Saúde",
            "populacao_ibge": st.column_config.NumberColumn("População IBGE", format="%d"),
            "esf_necessarias_ms": st.column_config.NumberColumn("eSF MS", format="%d"),
            "esf_existentes": st.column_config.NumberColumn("eSF existentes", format="%d"),
            "deficit_esf": st.column_config.NumberColumn("Déficit eSF", format="%d"),
            "ubs_existentes_informadas": st.column_config.NumberColumn("UBS existentes", format="%d"),
            "populacao_por_ubs": st.column_config.NumberColumn("Pop/UBS", format="%d"),
            "necessidade_ubs_preliminar": st.column_config.NumberColumn("UBS novas preliminares", format="%d"),
            "pontuacao_preliminar": st.column_config.ProgressColumn("Pontuação", min_value=0, max_value=100, format="%d"),
            "classificacao_preliminar": "Classificação",
            "situacao_analise": "Situação",
        },
    )

    st.download_button(
        "Baixar diagnóstico preliminar em Excel",
        data=gerar_excel_download(tabela),
        file_name=f"diagnostico_preliminar_ubs_ses_{int(ano)}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.markdown("### 5. Leitura institucional")
    st.markdown(
        """
        <div class="info-box">
        A coluna <b>UBS novas preliminares</b> não representa autorização de obra. Ela indica quantas estruturas poderiam ser necessárias
        para absorver o déficit de eSF, considerando a capacidade de referência definida no sistema. A decisão final deverá ocorrer após
        adesão municipal, comprovação de terreno, validação territorial e análise institucional da SES/MT.
        </div>
        """,
        unsafe_allow_html=True,
    )
