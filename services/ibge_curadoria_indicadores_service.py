
from __future__ import annotations

import re
import pandas as pd

from database.connection import get_connection
from database.queries import read_table
from services.ibge_dicionario_variaveis_service import carregar_dicionario_ibge


TABELA_CURADORIA = "ibge_indicadores_curadoria"


def _norm(s) -> str:
    s = "" if s is None else str(s)
    s = s.lower()
    s = s.replace("ç", "c").replace("ã", "a").replace("á", "a").replace("à", "a").replace("â", "a")
    s = s.replace("é", "e").replace("ê", "e").replace("í", "i").replace("ó", "o").replace("ô", "o").replace("ú", "u")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _classificar_por_texto(descricao: str, tabela: str) -> tuple[str, str, str]:
    """Classificação final para apresentação gerencial.

    Regra central:
    - Óbito/falecimento/mortalidade não entra como socioeducacional essencial.
    - Dados muito específicos ficam complementares ou ocultos.
    - Essencial fica reservado a indicadores amplos e explicáveis para gestor.
    """
    d = _norm(descricao)
    t = _norm(tabela)
    blob = f"{d} {t}"

    # Fora da camada socioeducacional essencial.
    # Pode ser usado depois em módulo epidemiológico próprio.
    if any(tok in blob for tok in [
        "falecida", "falecido", "falecimento", "obito", "obitos",
        "morte", "mortalidade", "pessoa falecida", "data de falecimento"
    ]):
        return "Ocultar do diagnóstico", "Epidemiologia — óbitos", "Variável de óbito/falecimento; não deve compor perfil socioeducacional gerencial."

    ocultar_tokens = [
        "imputad", "codigo", "situacao", "aglomerado", "concentracao urbana",
        "arranjo populacional", "regiao geografica", "rgint", "rgi",
        "tipo de especie e apartamento", "tipo de especie e casa",
        "sem morador", "vago", "uso ocasional", "recenseado por entrevista indireta",
    ]
    if any(tok in blob for tok in ocultar_tokens):
        return "Ocultar do diagnóstico", "Técnico/metodológico", "Variável técnica, metodológica ou pouco útil para abertura gerencial."

    if "domicilios coletivos" in blob or "domicilio coletivo" in blob:
        return "Complementar", "Domicílios coletivos", "Informação específica; manter para consulta técnica, não como indicador-chave."

    if d in ["total de pessoas", "populacao residente"] or "total de pessoas" in d:
        return "Essencial", "Demografia básica", "Indicador-chave para dimensionar população potencialmente dependente da APS."

    if "total de domicilios particulares ocupados" in d:
        return "Essencial", "Domicílios e moradia", "Indicador-chave para dimensionar território habitado e demanda domiciliar."

    if "media de moradores" in d and "domicilios particulares ocupados" in d:
        return "Complementar", "Domicílios e moradia", "Média setorial mantida como apoio; o resumo gerencial usa média calculada pelo sistema."

    if any(tok in blob for tok in ["alfabetiz", "nao alfabetiz", "analfabet", "sabe ler e escrever"]):
        if any(tok in blob for tok in ["sexo masculino", "sexo feminino", "pessoa responsavel", "responsavel pelo domicilio", "cor ou raca"]):
            return "Complementar", "Escolaridade e alfabetização", "Recorte específico de alfabetização; manter na consulta técnica."
        return "Essencial", "Escolaridade e alfabetização", "Determinante socioeducacional relevante para vulnerabilidade e comunicação em saúde."

    saneamento_tokens = [
        "esgotamento sanitario", "banheiro", "fossa", "rede geral de esgoto",
        "abastecimento de agua", "lixo coletado", "coleta de lixo", "destino do lixo"
    ]
    if any(tok in blob for tok in saneamento_tokens):
        if any(tok in blob for tok in ["apartamento", "tipo de especie", "pessoa responsavel", "cor ou raca", "indigena", "quilombola"]):
            return "Complementar", "Saneamento e infraestrutura domiciliar", "Recorte específico de saneamento; manter para consulta técnica."
        return "Essencial", "Saneamento e infraestrutura domiciliar", "Determinante social diretamente relacionado ao risco sanitário."

    if "indigena" in blob or "quilombola" in blob:
        if any(tok in blob for tok in ["total", "pessoas", "populacao", "moradores", "domicilios"]):
            return "Essencial", "Populações específicas e equidade", "Identifica populações com necessidades específicas de atenção e equidade territorial."
        return "Complementar", "Populações específicas e equidade", "Recorte específico de população tradicional."

    if any(tok in blob for tok in ["0 a 4 anos", "0 a 9 anos", "60 anos", "65 anos", "idosos", "criancas"]):
        if any(tok in blob for tok in ["asilo", "clinica", "internacao", "domicilios coletivos"]):
            return "Complementar", "Ciclo de vida e grupos prioritários", "Recorte institucional específico."
        return "Essencial", "Ciclo de vida e grupos prioritários", "Apoia planejamento por faixa etária e grupos prioritários da APS."

    if any(tok in blob for tok in ["cor ou raca", "branca", "preta", "parda", "amarela"]):
        return "Complementar", "Demografia e equidade", "Apoia leitura de equidade, com cuidado metodológico."

    if any(tok in blob for tok in ["parentesco", "responsavel", "conjuge", "filho"]):
        return "Complementar", "Composição domiciliar", "Pode apoiar leitura social, mas não é indicador central para decisão inicial."

    if any(tok in blob for tok in ["domicilio", "domicilios"]):
        return "Complementar", "Domicílios e moradia", "Informação domiciliar útil, mas não classificada como indicador-chave."

    return "Complementar", "Outros indicadores IBGE", "Indicador mantido como complementar até curadoria técnica posterior."

def criar_curadoria_automatica_ibge() -> pd.DataFrame:
    dic = carregar_dicionario_ibge()
    if dic.empty:
        return pd.DataFrame(columns=[
            "tabela_origem", "indicador_original", "nome_amigavel", "categoria_revisada",
            "status_exibicao", "grupo_analitico", "candidato_indice", "justificativa"
        ])

    df = dic.copy()
    for col in ["tabela_origem", "indicador_original", "nome_amigavel", "categoria_revisada", "descricao_oficial"]:
        if col not in df.columns:
            df[col] = ""

    registros = []
    for _, row in df.iterrows():
        tabela = str(row.get("tabela_origem", ""))
        ind = str(row.get("indicador_original", "")).upper()
        nome = str(row.get("nome_amigavel", "")) or str(row.get("descricao_oficial", "")) or ind
        desc = str(row.get("descricao_oficial", "")) or nome
        status, grupo, just = _classificar_por_texto(desc or nome, tabela)
        candidato = "Sim" if status == "Essencial" and grupo in [
            "Escolaridade e alfabetização",
            "Saneamento e infraestrutura domiciliar",
            "Domicílios e moradia",
            "Populações específicas e equidade",
            "Ciclo de vida e grupos prioritários",
        ] else "Não"

        registros.append({
            "tabela_origem": tabela,
            "indicador_original": ind,
            "nome_amigavel": nome,
            "categoria_revisada": row.get("categoria_revisada", ""),
            "status_exibicao": status,
            "grupo_analitico": grupo,
            "candidato_indice": candidato,
            "justificativa": just,
        })

    out = pd.DataFrame(registros).drop_duplicates(["tabela_origem", "indicador_original"], keep="last")
    return out.reset_index(drop=True)


def carregar_curadoria_ibge() -> pd.DataFrame:
    atual = read_table(TABELA_CURADORIA)
    if atual.empty:
        return criar_curadoria_automatica_ibge()
    return atual


def salvar_curadoria_ibge(df: pd.DataFrame) -> dict:
    if df is None or df.empty:
        return {"ok": False, "mensagem": "Curadoria vazia.", "linhas": 0}
    obrig = ["tabela_origem", "indicador_original", "status_exibicao", "grupo_analitico"]
    for c in obrig:
        if c not in df.columns:
            return {"ok": False, "mensagem": f"Coluna obrigatória ausente: {c}", "linhas": 0}
    out = df.copy()
    if "candidato_indice" not in out.columns:
        out["candidato_indice"] = "Não"
    if "justificativa" not in out.columns:
        out["justificativa"] = ""
    with get_connection() as con:
        out.to_sql(TABELA_CURADORIA, con, if_exists="replace", index=False)
    return {"ok": True, "mensagem": "Curadoria IBGE salva.", "linhas": int(len(out))}


def gerar_curadoria_automatica_ibge() -> dict:
    cur = criar_curadoria_automatica_ibge()
    if cur.empty:
        return {"ok": False, "mensagem": "Gere/importe o dicionário IBGE antes da curadoria.", "linhas": 0}
    return salvar_curadoria_ibge(cur)


def importar_curadoria_ibge(caminho) -> dict:
    caminho = str(caminho)
    if caminho.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(caminho, dtype=str)
    else:
        try:
            df = pd.read_csv(caminho, dtype=str, sep=None, engine="python", encoding="utf-8-sig")
        except Exception:
            df = pd.read_csv(caminho, dtype=str, sep=";", encoding="latin1")
    return salvar_curadoria_ibge(df)


def resumo_curadoria_ibge() -> pd.DataFrame:
    cur = carregar_curadoria_ibge()
    if cur.empty:
        return pd.DataFrame()
    return (
        cur.groupby(["status_exibicao", "grupo_analitico"], dropna=False)
        .agg(
            variaveis=("indicador_original", "count"),
            candidatos_indice=("candidato_indice", lambda s: int((s.astype(str).str.lower() == "sim").sum())),
        )
        .reset_index()
        .sort_values(["status_exibicao", "grupo_analitico"])
    )


def aplicar_curadoria_indicador(tabela_origem: str, indicador_original: str) -> dict:
    cur = carregar_curadoria_ibge()
    if cur.empty:
        return {
            "status_exibicao": "Complementar",
            "grupo_analitico": "Sem curadoria",
            "candidato_indice": "Não",
            "justificativa": "",
        }
    tabela = "" if tabela_origem is None else str(tabela_origem).strip()
    ind = "" if indicador_original is None else str(indicador_original).strip().upper()
    work = cur.copy()
    work["_tabela"] = work["tabela_origem"].astype(str).str.strip() if "tabela_origem" in work.columns else ""
    work["_ind"] = work["indicador_original"].astype(str).str.strip().str.upper() if "indicador_original" in work.columns else ""

    achado = work[work["_tabela"].eq(tabela) & work["_ind"].eq(ind)]
    if achado.empty:
        achado = work[work["_ind"].eq(ind)]
    if achado.empty:
        return {
            "status_exibicao": "Complementar",
            "grupo_analitico": "Outros indicadores IBGE",
            "candidato_indice": "Não",
            "justificativa": "Sem curadoria específica.",
        }
    row = achado.iloc[0]
    return {
        "status_exibicao": row.get("status_exibicao", "Complementar"),
        "grupo_analitico": row.get("grupo_analitico", "Outros indicadores IBGE"),
        "candidato_indice": row.get("candidato_indice", "Não"),
        "justificativa": row.get("justificativa", ""),
    }
