
from __future__ import annotations

from pathlib import Path
import re
import pandas as pd

from database.connection import get_connection
from database.queries import read_table


TABELA_DICIONARIO = "ibge_variaveis_dicionario"


MAPA_TABELA_TEMA = {
    "base_publica_ibge_setores_alfabetizacao": ("Alfabetização", "Escolaridade e educação"),
    "base_publica_ibge_setores_basico": ("Básico territorial", "Base territorial e demográfica"),
    "base_publica_ibge_setores_domicilios_1": ("Domicílios 1", "Saneamento, domicílios e entorno"),
    "base_publica_ibge_setores_domicilios_2": ("Domicílios 2", "Saneamento, domicílios e entorno"),
    "base_publica_ibge_setores_domicilios_3": ("Domicílios 3", "Saneamento, domicílios e entorno"),
    "base_publica_ibge_setores_cor_raca": ("Cor ou raça", "Demografia e equidade"),
    "base_publica_ibge_setores_demografia": ("Demografia", "Demografia e equidade"),
    "base_publica_ibge_setores_domicilios_indigenas": ("Domicílios indígenas", "Populações específicas"),
    "base_publica_ibge_setores_domicilios_quilombolas": ("Domicílios quilombolas", "Populações específicas"),
    "base_publica_ibge_setores_obitos": ("Óbitos", "Epidemiologia — mortalidade"),
    "base_publica_ibge_setores_parentesco": ("Parentesco", "Demografia e composição domiciliar"),
    "base_publica_ibge_setores_pessoas_indigenas": ("Pessoas indígenas", "Populações específicas"),
    "base_publica_ibge_setores_pessoas_quilombolas": ("Pessoas quilombolas", "Populações específicas"),
}


def _norm_col(c: str) -> str:
    c = str(c).strip().lower()
    c = re.sub(r"[^a-z0-9]+", "_", c)
    return re.sub(r"_+", "_", c).strip("_")


def criar_modelo_dicionario_ibge() -> pd.DataFrame:
    metadados = read_table("base_publica_indicadores_metadados")
    if metadados.empty:
        return pd.DataFrame(columns=[
            "tabela_origem", "indicador_original", "nome_amigavel",
            "categoria_revisada", "descricao_oficial", "observacao"
        ])

    registros = []
    for _, row in metadados.iterrows():
        tabela = str(row.get("tabela_origem", ""))
        indicador = str(row.get("indicador_original", ""))
        if not tabela.startswith("base_publica_ibge_setores_"):
            continue
        tema, categoria = MAPA_TABELA_TEMA.get(tabela, ("IBGE Censo 2022", row.get("categoria_sugerida", "Indicadores IBGE Censo 2022")))
        registros.append({
            "tabela_origem": tabela,
            "indicador_original": indicador,
            "nome_amigavel": f"{tema} — {indicador}",
            "categoria_revisada": categoria,
            "descricao_oficial": "",
            "observacao": "Nome automático provisório. Substituir pela descrição oficial do dicionário IBGE quando disponível.",
        })
    return pd.DataFrame(registros).drop_duplicates(["tabela_origem", "indicador_original"]).reset_index(drop=True)


def carregar_dicionario_ibge() -> pd.DataFrame:
    atual = read_table(TABELA_DICIONARIO)
    if atual.empty:
        return criar_modelo_dicionario_ibge()
    return atual


def salvar_dicionario_ibge(df: pd.DataFrame) -> dict:
    if df is None or df.empty:
        return {"ok": False, "mensagem": "Dicionário vazio.", "linhas": 0}
    out = df.copy()
    out.columns = [_norm_col(c) for c in out.columns]
    obrig = ["tabela_origem", "indicador_original"]
    for c in obrig:
        if c not in out.columns:
            return {"ok": False, "mensagem": f"Coluna obrigatória ausente: {c}", "linhas": 0}

    if "nome_amigavel" not in out.columns:
        out["nome_amigavel"] = out["indicador_original"]
    if "categoria_revisada" not in out.columns:
        out["categoria_revisada"] = ""
    if "descricao_oficial" not in out.columns:
        out["descricao_oficial"] = ""
    if "observacao" not in out.columns:
        out["observacao"] = ""

    out = out[["tabela_origem", "indicador_original", "nome_amigavel", "categoria_revisada", "descricao_oficial", "observacao"]].copy()
    out = out.drop_duplicates(["tabela_origem", "indicador_original"], keep="last")
    with get_connection() as con:
        out.to_sql(TABELA_DICIONARIO, con, if_exists="replace", index=False)
    return {"ok": True, "mensagem": "Dicionário IBGE salvo.", "linhas": int(len(out))}


def gerar_dicionario_automatico_ibge() -> dict:
    modelo = criar_modelo_dicionario_ibge()
    if modelo.empty:
        return {"ok": False, "mensagem": "Gere o consolidado municipal antes de criar o dicionário.", "linhas": 0}
    return salvar_dicionario_ibge(modelo)


def importar_dicionario_ibge(caminho: str | Path) -> dict:
    caminho = Path(caminho)
    if caminho.suffix.lower() in [".xlsx", ".xls"]:
        df = pd.read_excel(caminho, dtype=str)
    else:
        try:
            df = pd.read_csv(caminho, dtype=str, sep=None, engine="python", encoding="utf-8-sig")
        except Exception:
            df = pd.read_csv(caminho, dtype=str, sep=";", encoding="latin1")
    return salvar_dicionario_ibge(df)


def aplicar_dicionario_linha(tabela_origem: str, indicador_original: str, categoria_padrao: str = "") -> dict:
    """Aplica o dicionário IBGE ao indicador.

    Correções importantes:
    - compara V0001/v0001 sem diferenciar maiúsculas/minúsculas;
    - tenta primeiro tabela + variável;
    - se não achar, tenta variável sozinha;
    - se o dicionário oficial tiver sido importado sem tabela_origem perfeita, ainda assim usa a descrição oficial;
    - mantém fallback automático por tema quando não houver correspondência.
    """
    indicador_raw = "" if indicador_original is None else str(indicador_original).strip()
    indicador_upper = indicador_raw.upper()
    tabela_raw = "" if tabela_origem is None else str(tabela_origem).strip()

    dic = carregar_dicionario_ibge()

    def fallback() -> dict:
        tema, categoria = MAPA_TABELA_TEMA.get(
            tabela_raw,
            ("IBGE Censo 2022", categoria_padrao or "Indicadores IBGE Censo 2022")
        )
        return {
            "nome_amigavel": f"{tema} — {indicador_raw}",
            "categoria_revisada": categoria,
            "descricao_oficial": "",
            "observacao": "Nome automático provisório. Sem correspondência direta no dicionário importado.",
        }

    if dic.empty:
        return fallback()

    # Normaliza nomes de colunas do dicionário se necessário.
    cols_norm = {str(c).strip().lower(): c for c in dic.columns}
    tabela_col = cols_norm.get("tabela_origem")
    ind_col = cols_norm.get("indicador_original")
    nome_col = cols_norm.get("nome_amigavel")
    cat_col = cols_norm.get("categoria_revisada")
    desc_col = cols_norm.get("descricao_oficial")
    obs_col = cols_norm.get("observacao")

    if not ind_col:
        return fallback()

    dic_work = dic.copy()
    dic_work["_indicador_upper"] = dic_work[ind_col].astype(str).str.strip().str.upper()

    if tabela_col:
        dic_work["_tabela_norm"] = dic_work[tabela_col].astype(str).str.strip()
        # 1) Correspondência mais forte: tabela + variável, ignorando caixa da variável.
        achado = dic_work[
            dic_work["_indicador_upper"].eq(indicador_upper)
            & dic_work["_tabela_norm"].eq(tabela_raw)
        ]
    else:
        achado = dic_work.iloc[0:0]

    # 2) Fallback: variável sozinha. Isso é essencial porque alguns layouts oficiais
    # do IBGE não deixam a tabela_origem no mesmo padrão das tabelas do sistema.
    if achado.empty:
        achado = dic_work[dic_work["_indicador_upper"].eq(indicador_upper)]

    if achado.empty:
        return fallback()

    # Preferir linha com descrição oficial preenchida.
    if desc_col and desc_col in achado.columns:
        descr_preenchida = achado[achado[desc_col].astype(str).str.strip().replace({"nan": "", "None": ""}).ne("")]
        if not descr_preenchida.empty:
            achado = descr_preenchida

    row = achado.iloc[0]

    descricao = ""
    if desc_col and desc_col in achado.columns:
        descricao = "" if pd.isna(row.get(desc_col)) else str(row.get(desc_col)).strip()

    nome = ""
    if nome_col and nome_col in achado.columns:
        nome = "" if pd.isna(row.get(nome_col)) else str(row.get(nome_col)).strip()

    # Se nome_amigavel estiver vazio ou só repetir V0001, usa descrição oficial.
    if (not nome) or nome.upper() == indicador_upper:
        nome = descricao or indicador_raw

    categoria = categoria_padrao
    if cat_col and cat_col in achado.columns:
        cat_val = "" if pd.isna(row.get(cat_col)) else str(row.get(cat_col)).strip()
        if cat_val:
            categoria = cat_val

    if not categoria:
        categoria = MAPA_TABELA_TEMA.get(tabela_raw, ("IBGE Censo 2022", "Indicadores IBGE Censo 2022"))[1]

    obs = ""
    if obs_col and obs_col in achado.columns:
        obs = "" if pd.isna(row.get(obs_col)) else str(row.get(obs_col)).strip()

    return {
        "nome_amigavel": nome,
        "categoria_revisada": categoria,
        "descricao_oficial": descricao,
        "observacao": obs or "Dicionário aplicado por correspondência de variável.",
    }

def resumo_dicionario_ibge() -> pd.DataFrame:
    dic = carregar_dicionario_ibge()
    if dic.empty:
        return pd.DataFrame()
    return (
        dic.groupby("categoria_revisada", dropna=False)
        .agg(
            variaveis=("indicador_original", "count"),
            tabelas=("tabela_origem", "nunique"),
        )
        .reset_index()
        .sort_values(["variaveis"], ascending=False)
    )


URL_DICIONARIO_OFICIAL_IBGE_20250417 = "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/dicionario_de_dados_agregados_por_setores_censitarios_20250417.xlsx"


def _achar_coluna_flexivel(df: pd.DataFrame, candidatos: list[str]) -> str | None:
    def norm(s):
        s = str(s).lower()
        s = s.replace("ç", "c").replace("ã", "a").replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
        s = re.sub(r"[^a-z0-9]+", "_", s)
        return re.sub(r"_+", "_", s).strip("_")
    mapa = {norm(c): c for c in df.columns}
    for cand in candidatos:
        nc = norm(cand)
        if nc in mapa:
            return mapa[nc]
    for c in df.columns:
        nc = norm(c)
        for cand in candidatos:
            if norm(cand) in nc:
                return c
    return None


def _identificar_tabela_por_tema(tema: str, arquivo: str = "") -> str:
    blob = _norm_col(f"{tema} {arquivo}")
    if "alfabet" in blob:
        return "base_publica_ibge_setores_alfabetizacao"
    if "basico" in blob or "basica" in blob:
        return "base_publica_ibge_setores_basico"
    if "domicilio_1" in blob or "domicilios_1" in blob or "domicilio1" in blob:
        return "base_publica_ibge_setores_domicilios_1"
    if "domicilio_2" in blob or "domicilios_2" in blob or "domicilio2" in blob:
        return "base_publica_ibge_setores_domicilios_2"
    if "domicilio_3" in blob or "domicilios_3" in blob or "domicilio3" in blob:
        return "base_publica_ibge_setores_domicilios_3"
    if "cor" in blob and "raca" in blob:
        return "base_publica_ibge_setores_cor_raca"
    if "demografia" in blob:
        return "base_publica_ibge_setores_demografia"
    if "domicilios_indigenas" in blob or ("domicilio" in blob and "indigena" in blob):
        return "base_publica_ibge_setores_domicilios_indigenas"
    if "domicilios_quilombolas" in blob or ("domicilio" in blob and "quilombola" in blob):
        return "base_publica_ibge_setores_domicilios_quilombolas"
    if "obito" in blob:
        return "base_publica_ibge_setores_obitos"
    if "parentesco" in blob:
        return "base_publica_ibge_setores_parentesco"
    if "pessoas_indigenas" in blob or ("pessoa" in blob and "indigena" in blob):
        return "base_publica_ibge_setores_pessoas_indigenas"
    if "pessoas_quilombolas" in blob or ("pessoa" in blob and "quilombola" in blob):
        return "base_publica_ibge_setores_pessoas_quilombolas"
    return ""


def _categoria_por_tabela(tabela: str) -> str:
    return MAPA_TABELA_TEMA.get(str(tabela), ("IBGE Censo 2022", "Indicadores IBGE Censo 2022"))[1]


def _ler_planilha_dicionario_flexivel(caminho_ou_url: str | Path) -> pd.DataFrame:
    # Lê todas as abas porque o dicionário oficial pode vir separado por tema.
    planilhas = pd.read_excel(caminho_ou_url, sheet_name=None, dtype=str)
    registros = []

    for nome_aba, df in planilhas.items():
        if df is None or df.empty:
            continue
        df = df.dropna(how="all").copy()
        # Remove cabeçalhos repetidos/linhas vazias extremas.
        df.columns = [str(c).strip() for c in df.columns]

        col_var = _achar_coluna_flexivel(df, [
            "variavel", "variável", "codigo_variavel", "código da variável", "cod_variavel",
            "var", "nome_variavel", "codigo", "código", "coluna"
        ])
        col_desc = _achar_coluna_flexivel(df, [
            "descricao", "descrição", "descricao_variavel", "descrição da variável",
            "nome", "rotulo", "rótulo", "denominacao", "denominação", "quesito"
        ])
        col_tema = _achar_coluna_flexivel(df, [
            "tema", "assunto", "grupo", "arquivo", "conjunto", "tabela", "base"
        ])
        col_univ = _achar_coluna_flexivel(df, [
            "universo", "unidade", "tipo", "categoria"
        ])

        # Se não achou coluna de variável, tenta localizar qualquer coluna que contenha V0001 etc.
        if not col_var:
            for c in df.columns:
                if df[c].astype(str).str.match(r"^V\d{4,}$", na=False).any():
                    col_var = c
                    break
        if not col_desc:
            # usa a primeira coluna textual longa que não seja variável
            for c in df.columns:
                if c == col_var:
                    continue
                media_len = df[c].dropna().astype(str).str.len().mean()
                if pd.notna(media_len) and media_len > 15:
                    col_desc = c
                    break

        if not col_var:
            continue

        for _, row in df.iterrows():
            variavel = str(row.get(col_var, "")).strip()
            if not re.fullmatch(r"V\d{4,}", variavel, flags=re.IGNORECASE):
                continue
            variavel = variavel.upper()
            descricao = str(row.get(col_desc, "")).strip() if col_desc else ""
            tema = str(row.get(col_tema, "")).strip() if col_tema else str(nome_aba).strip()
            universo = str(row.get(col_univ, "")).strip() if col_univ else ""
            tabela = _identificar_tabela_por_tema(tema, nome_aba)
            categoria = _categoria_por_tabela(tabela) if tabela else "Indicadores IBGE Censo 2022"
            nome_amigavel = descricao if descricao and descricao.lower() not in ["nan", "none"] else f"{tema or nome_aba} — {variavel}"
            registros.append({
                "tabela_origem": tabela,
                "indicador_original": variavel,
                "nome_amigavel": nome_amigavel,
                "categoria_revisada": categoria,
                "descricao_oficial": descricao,
                "observacao": f"Dicionário oficial/flexível IBGE. Aba: {nome_aba}. Tema: {tema}. Universo: {universo}.",
                "aba_origem": nome_aba,
                "tema_origem": tema,
            })

    out = pd.DataFrame(registros)
    if out.empty:
        return out

    # Alguns dicionários não deixam claro a tabela de origem; nesse caso, permite casamento por variável apenas.
    out["indicador_original"] = out["indicador_original"].astype(str).str.upper()
    out = out.drop_duplicates(["tabela_origem", "indicador_original", "nome_amigavel"], keep="last")
    return out.reset_index(drop=True)


def importar_dicionario_oficial_ibge_flexivel(caminho_ou_url: str | Path) -> dict:
    bruto = _ler_planilha_dicionario_flexivel(caminho_ou_url)
    if bruto.empty:
        return {"ok": False, "mensagem": "Não foi possível identificar variáveis Vxxxx no dicionário informado.", "linhas": 0}

    # Expande para tabelas já existentes quando a tabela_origem não foi identificada.
    existentes = read_table("base_publica_indicadores_metadados")
    if not existentes.empty:
        existentes = existentes[["tabela_origem", "indicador_original"]].drop_duplicates()
        existentes["indicador_original"] = existentes["indicador_original"].astype(str).str.upper()

        sem_tabela = bruto[bruto["tabela_origem"].astype(str).str.strip().eq("")].copy()
        com_tabela = bruto[~bruto["tabela_origem"].astype(str).str.strip().eq("")].copy()

        expandidos = []
        if not sem_tabela.empty:
            for _, row in sem_tabela.iterrows():
                matches = existentes[existentes["indicador_original"].eq(str(row["indicador_original"]).upper())]
                if matches.empty:
                    expandidos.append(row.to_dict())
                else:
                    for _, m in matches.iterrows():
                        novo = row.to_dict()
                        novo["tabela_origem"] = m["tabela_origem"]
                        novo["categoria_revisada"] = _categoria_por_tabela(m["tabela_origem"])
                        expandidos.append(novo)
        bruto = pd.concat([com_tabela, pd.DataFrame(expandidos)], ignore_index=True) if expandidos else com_tabela

    if bruto.empty:
        return {"ok": False, "mensagem": "Dicionário lido, mas sem correspondência com as tabelas importadas.", "linhas": 0}

    salvar = bruto[["tabela_origem", "indicador_original", "nome_amigavel", "categoria_revisada", "descricao_oficial", "observacao"]].copy()
    # Se o dicionário oficial tiver apenas descrição genérica de V0001 que aparece em várias tabelas, mantém por tabela.
    salvar = salvar.drop_duplicates(["tabela_origem", "indicador_original"], keep="last")
    return salvar_dicionario_ibge(salvar)


def importar_dicionario_oficial_ibge_url() -> dict:
    return importar_dicionario_oficial_ibge_flexivel(URL_DICIONARIO_OFICIAL_IBGE_20250417)


def diagnosticar_dicionario_oficial_ibge(caminho_ou_url: str | Path) -> pd.DataFrame:
    bruto = _ler_planilha_dicionario_flexivel(caminho_ou_url)
    if bruto.empty:
        return pd.DataFrame()
    return (
        bruto.groupby(["tabela_origem", "categoria_revisada"], dropna=False)
        .agg(
            variaveis=("indicador_original", "nunique"),
            exemplos=("nome_amigavel", lambda s: " | ".join(s.dropna().astype(str).head(3))),
        )
        .reset_index()
        .sort_values(["tabela_origem", "variaveis"], ascending=[True, False])
    )
