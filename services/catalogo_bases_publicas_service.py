
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import pandas as pd

from database.connection import get_connection
from database.queries import read_table

CATALOGO_PADRAO = [
    {
        "eixo": "IBGE",
        "base": "Censo 2022 — Agregados por Setores Censitários",
        "temas": "renda, alfabetização, domicílios, saneamento, entorno, idade, raça/cor, povos tradicionais",
        "nivel_territorial": "setor censitário / município",
        "formato_esperado": "CSV/ZIP/XLSX",
        "uso_no_sistema": "vulnerabilidade territorial, vazios assistenciais, determinantes sociais",
        "prioridade": "Alta",
        "status": "Pendente de importação",
        "observacao": "Priorizar arquivos já filtrados para MT ou com código de setor/código IBGE.",
    },
    {
        "eixo": "IBGE",
        "base": "Censo 2022 — Trabalho e Rendimento",
        "temas": "renda, trabalho, ocupação, rendimento domiciliar",
        "nivel_territorial": "município / UF / conforme divulgação",
        "formato_esperado": "XLSX/CSV",
        "uso_no_sistema": "vulnerabilidade econômica e contexto de trabalho/renda",
        "prioridade": "Alta",
        "status": "Pendente de importação",
        "observacao": "Usar como camada municipal quando não houver dado por setor.",
    },
    {
        "eixo": "INEP",
        "base": "Indicadores Educacionais",
        "temas": "INSE, IDEB, distorção idade-série, rendimento, fluxo escolar",
        "nivel_territorial": "município / escola / rede",
        "formato_esperado": "XLSX/CSV",
        "uso_no_sistema": "contexto educacional e vulnerabilidade socioeducacional",
        "prioridade": "Alta",
        "status": "Pendente de importação",
        "observacao": "Priorizar indicadores municipais ou agregáveis por município.",
    },
    {
        "eixo": "DATASUS",
        "base": "SINAN — agravos de notificação",
        "temas": "dengue, tuberculose, hanseníase, violências, acidentes, outros agravos",
        "nivel_territorial": "município de residência/notificação",
        "formato_esperado": "DBC/DBF/CSV/TABNET exportado",
        "uso_no_sistema": "perfil epidemiológico e vigilância",
        "prioridade": "Alta",
        "status": "Pendente de importação",
        "observacao": "Começar por arquivos exportados/planilhados; DBC exige tratamento específico.",
    },
    {
        "eixo": "DATASUS",
        "base": "SIM — Mortalidade",
        "temas": "óbitos, causas, faixa etária, município de residência",
        "nivel_territorial": "município",
        "formato_esperado": "DBC/DBF/CSV/TABNET exportado",
        "uso_no_sistema": "perfil epidemiológico, mortalidade e vigilância",
        "prioridade": "Alta",
        "status": "Pendente de importação",
        "observacao": "Começar por agregados por município/ano/causa.",
    },
    {
        "eixo": "DATASUS",
        "base": "SINASC — Nascidos vivos",
        "temas": "nascidos vivos, pré-natal, idade materna, baixo peso",
        "nivel_territorial": "município",
        "formato_esperado": "DBC/DBF/CSV/TABNET exportado",
        "uso_no_sistema": "saúde materno-infantil e planejamento APS",
        "prioridade": "Média/Alta",
        "status": "Pendente de importação",
        "observacao": "Útil para cruzar com indicadores de gestantes, crianças e cobertura APS.",
    },
    {
        "eixo": "DATASUS",
        "base": "SIH/SIA — Produção e internações",
        "temas": "internações, procedimentos, produção ambulatorial",
        "nivel_territorial": "município / estabelecimento",
        "formato_esperado": "DBC/DBF/CSV/TABNET exportado",
        "uso_no_sistema": "pressão assistencial, internações sensíveis, produção",
        "prioridade": "Média",
        "status": "Pendente de importação",
        "observacao": "Mais pesado; recomendado após consolidar IBGE/INEP/SINAN/SIM.",
    },
    {
        "eixo": "Atlas Brasil",
        "base": "IDHM e vulnerabilidade municipal",
        "temas": "IDHM, renda, educação, longevidade, vulnerabilidade",
        "nivel_territorial": "município",
        "formato_esperado": "CSV/XLSX",
        "uso_no_sistema": "camada contextual municipal de desenvolvimento humano",
        "prioridade": "Média/Alta",
        "status": "Pendente de importação",
        "observacao": "Usar como camada complementar; verificar ano e metodologia.",
    },
]


def catalogo_bases_publicas_padrao() -> pd.DataFrame:
    return pd.DataFrame(CATALOGO_PADRAO)


def salvar_catalogo_padrao() -> dict:
    df = catalogo_bases_publicas_padrao()
    with get_connection() as con:
        df.to_sql("catalogo_bases_publicas", con, if_exists="replace", index=False)
    return {"ok": True, "linhas": len(df), "tabela": "catalogo_bases_publicas"}


def carregar_catalogo_bases_publicas() -> pd.DataFrame:
    df = read_table("catalogo_bases_publicas")
    if df.empty:
        return catalogo_bases_publicas_padrao()
    return df


def registrar_base_publica_importada(
    eixo: str,
    base: str,
    arquivo_nome: str,
    tabela_destino: str,
    fonte_url: str = "",
    ano_referencia: str = "",
    observacao: str = "",
    linhas: int = 0,
    municipios_identificados: int = 0,
) -> dict:
    registro = pd.DataFrame([{
        "eixo": eixo,
        "base": base,
        "arquivo_nome": arquivo_nome,
        "tabela_destino": tabela_destino,
        "fonte_url": fonte_url,
        "ano_referencia": ano_referencia,
        "observacao": observacao,
        "linhas": int(linhas or 0),
        "municipios_identificados": int(municipios_identificados or 0),
        "data_registro": datetime.now().isoformat(timespec="seconds"),
    }])
    atual = read_table("bases_publicas_importadas")
    final = pd.concat([atual, registro], ignore_index=True) if not atual.empty else registro
    with get_connection() as con:
        final.to_sql("bases_publicas_importadas", con, if_exists="replace", index=False)
    return {"ok": True, "linhas": len(final), "tabela": "bases_publicas_importadas"}


def carregar_bases_publicas_importadas() -> pd.DataFrame:
    return read_table("bases_publicas_importadas")


def matriz_priorizacao_importacao() -> pd.DataFrame:
    cat = carregar_catalogo_bases_publicas().copy()
    if cat.empty:
        return cat
    score_pri = {"Alta": 3, "Média/Alta": 2.5, "Média": 2, "Baixa": 1}
    cat["ordem_sugerida"] = cat["prioridade"].map(score_pri).fillna(1)
    cat["recomendacao"] = cat.apply(
        lambda r: (
            "Importar primeiro" if r.get("prioridade") == "Alta" and r.get("eixo") in ["IBGE", "INEP", "DATASUS"]
            else "Importar após bases prioritárias" if r.get("prioridade") in ["Média/Alta", "Média"]
            else "Camada complementar"
        ),
        axis=1,
    )
    return cat.sort_values(["ordem_sugerida", "eixo"], ascending=[False, True]).reset_index(drop=True)


def gerar_modelo_registro_fonte_publica() -> pd.DataFrame:
    return pd.DataFrame([{
        "eixo": "IBGE",
        "base": "Censo 2022 — Agregados por Setores Censitários",
        "arquivo_nome": "nome_do_arquivo_baixado.csv",
        "tabela_destino": "socio_ibge_setores_2022",
        "fonte_url": "colar link oficial da página de download",
        "ano_referencia": "2022",
        "observacao": "Base pública oficial importada sem preenchimento manual",
    }])
