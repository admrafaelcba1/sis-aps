
from __future__ import annotations

import re
import pandas as pd

from database.queries import read_table
from services.ibge_dicionario_variaveis_service import aplicar_dicionario_linha
from services.ibge_curadoria_indicadores_service import aplicar_curadoria_indicador


def _norm(s) -> str:
    s = "" if s is None else str(s)
    s = s.lower()
    s = s.replace("ç", "c").replace("ã", "a").replace("á", "a").replace("à", "a").replace("â", "a")
    s = s.replace("é", "e").replace("ê", "e").replace("í", "i").replace("ó", "o").replace("ô", "o").replace("ú", "u")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _fmt_num(v, casas: int = 1):
    try:
        x = float(v)
    except Exception:
        return "-"
    if abs(x) >= 1000:
        return f"{x:,.0f}".replace(",", ".")
    return f"{x:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_percent(v, casas: int = 1):
    try:
        return _fmt_num(float(v), casas) + "%"
    except Exception:
        return "-"


def _fmt_valor(v):
    try:
        x = float(v)
    except Exception:
        return "-"
    if abs(x) >= 1000:
        return f"{x:,.0f}".replace(",", ".")
    if abs(x) >= 10:
        return f"{x:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pegar_linha_municipio(municipio: str) -> pd.Series | None:
    cons = read_table("base_publica_consolidado_municipal")
    if cons.empty or "municipio" not in cons.columns:
        return None
    alvo = str(municipio).strip().lower()
    linhas = cons[cons["municipio"].astype(str).str.strip().str.lower().eq(alvo)]
    if linhas.empty:
        return None
    return linhas.iloc[0]


def _metadados() -> pd.DataFrame:
    meta = read_table("base_publica_indicadores_metadados")
    if meta.empty:
        return pd.DataFrame()
    for col in ["tabela_origem", "indicador_original", "indicador_consolidado", "metodo_agregacao"]:
        if col not in meta.columns:
            meta[col] = ""
    return meta


def _tipo_medida(texto: str) -> str:
    t = _norm(texto)
    if any(x in t for x in ["percentual", "porcentagem", "taxa", "%"]):
        return "Percentual"
    if any(x in t for x in ["media", "média"]):
        return "Média"
    return "Contagem"


def _valor_apresentacao(valor: float, tipo_medida: str) -> str:
    if tipo_medida == "Percentual":
        return _fmt_percent(valor)
    return _fmt_valor(valor)


def _indicadores_municipio_raw(municipio: str) -> pd.DataFrame:
    row = _pegar_linha_municipio(municipio)
    meta = _metadados()
    if row is None or meta.empty:
        return pd.DataFrame()

    registros = []
    for _, m in meta.iterrows():
        col = str(m.get("indicador_consolidado", ""))
        if not col or col not in row.index:
            continue

        valor = pd.to_numeric(pd.Series([row[col]]), errors="coerce").iloc[0]
        if pd.isna(valor):
            continue

        tabela = str(m.get("tabela_origem", ""))
        cod = str(m.get("indicador_original", "")).upper()
        dic = aplicar_dicionario_linha(tabela, cod, str(m.get("categoria_sugerida", "")))
        cur = aplicar_curadoria_indicador(tabela, cod)

        if str(cur.get("status_exibicao", "")).lower().startswith("ocultar"):
            continue

        nome = dic.get("nome_amigavel") or cod
        desc = dic.get("descricao_oficial", "")
        texto = _norm(f"{nome} {desc} {cod} {tabela}")
        tipo = _tipo_medida(f"{nome} {desc}")

        recorte_especifico = any(tok in texto for tok in [
            "sexo masculino", "sexo feminino", "homens", "mulheres",
            "cor ou raca", "cor raca", "branca", "preta", "parda", "amarela",
            "pessoa responsavel", "responsavel pelo domicilio", "conjuge", "filho",
            "tipo de especie", "apartamento", "casa de vila", "cortico",
            "domicilios coletivos", "asilo", "clinica psiquiatrica", "comunidade terapeutica",
            "internacao de menores", "imputad", "vago", "sem morador",
            "falecida", "falecido", "falecimento", "obito", "obitos", "mortalidade", "morte",
        ])

        registros.append({
            "categoria": cur.get("grupo_analitico") or dic.get("categoria_revisada") or m.get("categoria_sugerida", ""),
            "indicador": nome,
            "codigo_variavel": cod,
            "valor": float(valor),
            "valor_formatado": _valor_apresentacao(valor, tipo),
            "tabela_origem": tabela,
            "metodo_agregacao": m.get("metodo_agregacao", ""),
            "tipo_medida": tipo,
            "status_exibicao": "Complementar" if recorte_especifico and cur.get("status_exibicao") == "Essencial" else cur.get("status_exibicao", "Complementar"),
            "candidato_indice": "Não" if recorte_especifico else cur.get("candidato_indice", "Não"),
            "justificativa": cur.get("justificativa", ""),
            "descricao_oficial": desc,
            "_texto_busca": texto,
            "_recorte_especifico": recorte_especifico,
        })

    out = pd.DataFrame(registros)
    if not out.empty:
        ordem = {"Essencial": 0, "Complementar": 1}
        out["_ordem"] = out["status_exibicao"].map(ordem).fillna(9)
        out = out.sort_values(["_ordem", "categoria", "indicador"]).drop(columns=["_ordem"])
    return out


def _penalidade_recorte(row: pd.Series) -> int:
    texto = row.get("_texto_busca", "")
    penalidade = 0
    proibidos_fortes = [
        "sexo masculino", "sexo feminino", "cor ou raca", "cor raca",
        "pessoa responsavel", "responsavel pelo domicilio",
        "tipo de especie", "apartamento", "domicilios coletivos",
        "asilo", "clinica", "internacao", "comunidade terapeutica",
        "indigena"
    ]
    for tok in proibidos_fortes:
        if tok in texto:
            penalidade += 40
    if row.get("_recorte_especifico"):
        penalidade += 50
    if row.get("tipo_medida") in ["Média", "Percentual"]:
        penalidade += 10
    return penalidade


def _score_item(row: pd.Series, regra: dict) -> int:
    texto = row.get("_texto_busca", "")
    score = 0

    for termo in regra.get("positivos", []):
        if _norm(termo) in texto:
            score += 15

    for termo in regra.get("fortes", []):
        if _norm(termo) in texto:
            score += 40

    for termo in regra.get("negativos", []):
        if _norm(termo) in texto:
            score -= 60

    tipo_desejado = regra.get("tipo_medida")
    if tipo_desejado:
        if row.get("tipo_medida") == tipo_desejado:
            score += 20
        else:
            score -= 30

    if not regra.get("permite_recorte", False):
        score -= _penalidade_recorte(row)

    if regra.get("permite_indigena", False):
        if "indigena" in texto and "base_publica_ibge_setores_pessoas_indigenas" in str(row.get("tabela_origem", "")):
            score += 80
    if regra.get("permite_quilombola", False):
        if "quilombola" in texto and "base_publica_ibge_setores_pessoas_quilombolas" in str(row.get("tabela_origem", "")):
            score += 80

    if row.get("status_exibicao") == "Essencial":
        score += 5

    try:
        if float(row.get("valor", 0)) > 0:
            score += 2
    except Exception:
        pass

    return score


def _selecionar_melhor(df: pd.DataFrame, regra: dict) -> dict | None:
    if df.empty:
        return None

    work = df.copy()
    tabela_pref = regra.get("tabela")
    if tabela_pref:
        pref = work[work["tabela_origem"].astype(str).eq(tabela_pref)]
        if not pref.empty:
            work = pref
        elif regra.get("tabela_obrigatoria", False):
            return None

    work["_score_sel"] = work.apply(lambda r: _score_item(r, regra), axis=1)
    work = work[work["_score_sel"] > 0].sort_values(["_score_sel", "valor"], ascending=[False, False])
    if work.empty:
        return None
    return work.iloc[0].drop(labels=["_score_sel"], errors="ignore").to_dict()



def _selecionar_agua_fallback(df: pd.DataFrame) -> dict | None:
    """Fallback específico para abastecimento de água.

    O dicionário do IBGE pode trazer muitas descrições específicas. Esta função:
    - procura apenas bases de domicílios;
    - rejeita recortes por pessoa responsável, cor/raça, indígena/quilombola, tipo de espécie, apartamento;
    - rejeita variáveis negativas como não possui ligação, outra forma, poço, carro-pipa etc.;
    - prioriza rede geral / canalizada / abastecimento adequado;
    - só retorna se houver valor positivo.
    """
    if df.empty:
        return None

    work = df.copy()
    work = work[work["tabela_origem"].astype(str).str.contains("domicilios", na=False)].copy()
    if work.empty:
        return None

    def agua_score(row):
        texto = row.get("_texto_busca", "")
        score = 0

        positivos_fortes = [
            "rede geral de distribuicao de agua",
            "ligacao a rede geral de distribuicao de agua",
            "abastecimento de agua da rede geral",
            "agua canalizada",
            "canalizada ate o domicilio",
            "rede geral",
        ]
        positivos = ["abastecimento de agua", "agua"]

        negativos_fortes = [
            "nao possui ligacao",
            "nao tinham canalizacao",
            "sem canalizacao",
            "outra forma",
            "poco", "poço",
            "nascente",
            "carro pipa",
            "carro pipa",
            "agua da chuva",
            "rio", "acude", "açude", "lago", "igarape",
            "cisterna",
            "fora do domicilio",
            "fora da propriedade",
        ]
        recortes = [
            "pessoa responsavel", "responsavel pelo domicilio",
            "cor ou raca", "sexo masculino", "sexo feminino",
            "indigena", "quilombola",
            "tipo de especie", "apartamento", "casa de vila", "cortico",
            "domicilios coletivos", "imputad", "vago", "sem morador",
        ]

        for p in positivos_fortes:
            if _norm(p) in texto:
                score += 60
        for p in positivos:
            if _norm(p) in texto:
                score += 15
        for n in negativos_fortes:
            if _norm(n) in texto:
                score -= 80
        for r in recortes:
            if _norm(r) in texto:
                score -= 80

        if row.get("tipo_medida") == "Contagem":
            score += 10
        try:
            if float(row.get("valor", 0)) > 0:
                score += 5
            else:
                score -= 20
        except Exception:
            score -= 20

        return score

    work["_score_agua"] = work.apply(agua_score, axis=1)
    work = work[work["_score_agua"] > 20].sort_values(["_score_agua", "valor"], ascending=[False, False])
    if work.empty:
        return None

    item = work.iloc[0].drop(labels=["_score_agua"], errors="ignore").to_dict()
    item["indicador"] = item.get("indicador", "")
    return item

REGRAS_CHAVE = [
    {
        "eixo": "População residente",
        "fortes": ["total de pessoas"],
        "positivos": ["populacao residente"],
        "negativos": ["domicilios", "media", "percentual", "imputad"],
        "tabela": "base_publica_ibge_setores_basico",
        "tipo_medida": "Contagem",
        "leitura": "Dimensiona a população residente considerada nas bases territoriais do Censo.",
    },
    {
        "eixo": "Domicílios particulares ocupados",
        "fortes": ["total de domicilios particulares ocupados"],
        "positivos": ["domicilios particulares ocupados"],
        "negativos": ["media", "percentual", "apartamento", "tipo de especie", "coletivos", "imputad"],
        "tabela": "base_publica_ibge_setores_basico",
        "tipo_medida": "Contagem",
        "leitura": "Indica a quantidade de domicílios habitados, útil para estimar demanda territorial da APS.",
    },
    {
        "eixo": "Alfabetização",
        "fortes": ["morador sabe ler e escrever", "pessoas alfabetizadas", "15 anos ou mais"],
        "positivos": ["alfabetiz"],
        "negativos": ["sexo masculino", "sexo feminino", "pessoa responsavel", "responsavel pelo domicilio", "cor ou raca", "domicilios coletivos"],
        "tabela": "base_publica_ibge_setores_alfabetizacao",
        "tipo_medida": "Contagem",
        "leitura": "Determinante socioeducacional relevante para comunicação em saúde e vulnerabilidade.",
    },
    {
        "eixo": "Esgotamento sanitário",
        "fortes": ["rede geral de esgoto", "esgotamento sanitario"],
        "positivos": ["banheiro", "fossa"],
        "negativos": ["apartamento", "tipo de especie", "coletivos", "imputad", "pessoa responsavel", "cor ou raca", "indigena", "quilombola"],
        "tabela": None,
        "tipo_medida": "Contagem",
        "leitura": "Determinante social relacionado a risco sanitário e condições de vida.",
    },
    {
        "eixo": "Abastecimento de água",
        "fortes": ["rede geral de distribuicao de agua", "ligacao a rede geral de distribuicao de agua", "agua canalizada", "abastecimento de agua da rede geral"],
        "positivos": ["abastecimento de agua"],
        "negativos": ["nao possui ligacao", "outra forma", "poco", "poço", "nascente", "carro pipa", "agua da chuva", "rio", "acude", "açude", "lago", "cisterna", "apartamento", "tipo de especie", "coletivos", "pessoa responsavel", "cor ou raca", "indigena", "quilombola"],
        "tabela": None,
        "tipo_medida": "Contagem",
        "fallback_agua": True,
        "leitura": "Sinaliza condição básica de infraestrutura domiciliar e risco sanitário.",
    },
    {
        "eixo": "Coleta/destino do lixo",
        "fortes": ["lixo coletado no domicilio por servico de limpeza", "coleta de lixo"],
        "positivos": ["destino do lixo", "lixo coletado"],
        "negativos": ["apartamento", "tipo de especie", "coletivos", "pessoa responsavel", "cor ou raca", "indigena", "quilombola"],
        "tabela": None,
        "tipo_medida": "Contagem",
        "leitura": "Apoia leitura de saneamento ambiental e vulnerabilidade territorial.",
    },
    {
        "eixo": "População indígena",
        "fortes": ["total de pessoas indigenas", "pessoas indigenas"],
        "positivos": ["populacao indigena", "moradores indigenas"],
        "negativos": ["domicilios", "tipo de especie", "coletivos", "pessoa responsavel"],
        "tabela": "base_publica_ibge_setores_pessoas_indigenas",
        "tipo_medida": "Contagem",
        "permite_indigena": True,
        "leitura": "Identifica população indígena para atenção diferenciada e equidade territorial.",
    },
    {
        "eixo": "População quilombola",
        "fortes": ["total de pessoas quilombolas", "pessoas quilombolas"],
        "positivos": ["populacao quilombola", "moradores quilombolas"],
        "negativos": ["domicilios", "tipo de especie", "coletivos", "pessoa responsavel"],
        "tabela": "base_publica_ibge_setores_pessoas_quilombolas",
        "tipo_medida": "Contagem",
        "permite_quilombola": True,
        "leitura": "Identifica população quilombola para atenção diferenciada e equidade territorial.",
    },
    {
        "eixo": "Crianças / infância",
        "fortes": ["0 a 4 anos", "0 a 9 anos"],
        "positivos": ["criancas", "zero a nove"],
        "negativos": ["domicilios coletivos", "asilo", "clinica", "internacao", "tipo de especie", "pessoa responsavel", "falecida", "falecido", "falecimento", "obito", "obitos", "morte", "mortalidade"],
        "tabela": "base_publica_ibge_setores_demografia",
        "tabela_obrigatoria": True,
        "tipo_medida": "Contagem",
        "leitura": "Apoia planejamento por ciclo de vida e ações prioritárias da APS.",
    },
    {
        "eixo": "Idosos",
        "fortes": ["60 anos", "65 anos"],
        "positivos": ["idosos"],
        "negativos": ["asilo", "domicilios coletivos", "clinica", "tipo de especie", "pessoa responsavel", "falecida", "falecido", "falecimento", "obito", "obitos", "morte", "mortalidade"],
        "tabela": "base_publica_ibge_setores_demografia",
        "tabela_obrigatoria": True,
        "tipo_medida": "Contagem",
        "leitura": "Apoia planejamento de cuidado longitudinal e condições crônicas.",
    },
]


def _linhas_base_resumo(df: pd.DataFrame) -> list[dict]:
    linhas = []
    usados = set()
    for regra in REGRAS_CHAVE:
        item = _selecionar_melhor(df, regra)
        if not item and regra.get("fallback_agua", False):
            item = _selecionar_agua_fallback(df)
        if not item:
            continue
        chave = (item.get("tabela_origem"), item.get("codigo_variavel"))
        if chave in usados:
            continue
        usados.add(chave)
        linhas.append({
            "Indicador-chave": regra["eixo"],
            "Indicador selecionado": item.get("indicador", ""),
            "Valor": item.get("valor_formatado", "-"),
            "Valor numérico": item.get("valor", None),
            "Código": item.get("codigo_variavel", ""),
            "Tipo": item.get("tipo_medida", ""),
            "Fonte": item.get("tabela_origem", ""),
            "Leitura": regra["leitura"],
            "_derivado": False,
        })
    return linhas


def _get_valor(linhas: list[dict], eixo: str):
    for item in linhas:
        if item.get("Indicador-chave") == eixo:
            try:
                return float(item.get("Valor numérico"))
            except Exception:
                return None
    return None


def _add_derivados(linhas: list[dict]) -> list[dict]:
    """Cria indicadores derivados gerenciais comparáveis."""
    out = list(linhas)

    pop = _get_valor(out, "População residente")
    dpo = _get_valor(out, "Domicílios particulares ocupados")
    alfabet = _get_valor(out, "Alfabetização")
    esgoto = _get_valor(out, "Esgotamento sanitário")
    agua = _get_valor(out, "Abastecimento de água")
    lixo = _get_valor(out, "Coleta/destino do lixo")
    indigena = _get_valor(out, "População indígena")
    quilombola = _get_valor(out, "População quilombola")

    derivs = []

    if pop and dpo and dpo > 0:
        derivs.append({
            "Indicador-chave": "Média calculada de moradores por domicílio",
            "Indicador selecionado": "População residente ÷ domicílios particulares ocupados",
            "Valor": _fmt_num(pop / dpo, 2),
            "Valor numérico": pop / dpo,
            "Código": "DER_POP_DPO",
            "Tipo": "Média calculada",
            "Fonte": "Indicador derivado",
            "Leitura": "Indicador calculado pelo sistema para evitar soma indevida de médias setoriais.",
            "_derivado": True,
        })

    if pop and alfabet and pop > 0:
        derivs.append({
            "Indicador-chave": "Alfabetização relativa",
            "Indicador selecionado": "Pessoas alfabetizadas selecionadas ÷ população residente",
            "Valor": _fmt_percent((alfabet / pop) * 100, 1),
            "Valor numérico": (alfabet / pop) * 100,
            "Código": "DER_ALF_POP",
            "Tipo": "Percentual calculado",
            "Fonte": "Indicador derivado",
            "Leitura": "Indicador aproximado para comparação territorial. Deve ser validado conforme denominador oficial da variável de alfabetização.",
            "_derivado": True,
        })

    if dpo and esgoto is not None and dpo > 0:
        derivs.append({
            "Indicador-chave": "Esgotamento sanitário relativo",
            "Indicador selecionado": "Domicílios selecionados com condição de esgotamento ÷ domicílios particulares ocupados",
            "Valor": _fmt_percent((esgoto / dpo) * 100, 1),
            "Valor numérico": (esgoto / dpo) * 100,
            "Código": "DER_ESG_DPO",
            "Tipo": "Percentual calculado",
            "Fonte": "Indicador derivado",
            "Leitura": "Permite comparação proporcional entre municípios, conforme variável de esgotamento selecionada.",
            "_derivado": True,
        })

    if dpo and agua is not None and dpo > 0:
        derivs.append({
            "Indicador-chave": "Abastecimento de água relativo",
            "Indicador selecionado": "Domicílios selecionados com abastecimento ÷ domicílios particulares ocupados",
            "Valor": _fmt_percent((agua / dpo) * 100, 1),
            "Valor numérico": (agua / dpo) * 100,
            "Código": "DER_AGUA_DPO",
            "Tipo": "Percentual calculado",
            "Fonte": "Indicador derivado",
            "Leitura": "Permite comparação proporcional entre municípios, conforme variável de abastecimento selecionada.",
            "_derivado": True,
        })

    if dpo and lixo is not None and dpo > 0:
        derivs.append({
            "Indicador-chave": "Coleta/destino do lixo relativo",
            "Indicador selecionado": "Domicílios selecionados com coleta/destino de lixo ÷ domicílios particulares ocupados",
            "Valor": _fmt_percent((lixo / dpo) * 100, 1),
            "Valor numérico": (lixo / dpo) * 100,
            "Código": "DER_LIXO_DPO",
            "Tipo": "Percentual calculado",
            "Fonte": "Indicador derivado",
            "Leitura": "Permite comparação proporcional entre municípios, conforme variável de lixo selecionada.",
            "_derivado": True,
        })

    if pop and indigena is not None and pop > 0:
        derivs.append({
            "Indicador-chave": "População indígena relativa",
            "Indicador selecionado": "População indígena selecionada ÷ população residente",
            "Valor": _fmt_percent((indigena / pop) * 100, 2),
            "Valor numérico": (indigena / pop) * 100,
            "Código": "DER_IND_POP",
            "Tipo": "Percentual calculado",
            "Fonte": "Indicador derivado",
            "Leitura": "Ajuda a comparar a presença relativa de população indígena no território.",
            "_derivado": True,
        })

    if pop and quilombola is not None and pop > 0:
        derivs.append({
            "Indicador-chave": "População quilombola relativa",
            "Indicador selecionado": "População quilombola selecionada ÷ população residente",
            "Valor": _fmt_percent((quilombola / pop) * 100, 2),
            "Valor numérico": (quilombola / pop) * 100,
            "Código": "DER_QUI_POP",
            "Tipo": "Percentual calculado",
            "Fonte": "Indicador derivado",
            "Leitura": "Ajuda a comparar a presença relativa de população quilombola no território.",
            "_derivado": True,
        })

    # Derivados entram antes dos dados absolutos, para leitura comparável.
    return derivs + [item for item in out if item.get("Indicador-chave") != "Média de moradores por domicílio"]


def _alertas(resumo: pd.DataFrame) -> pd.DataFrame:
    alertas = []
    if resumo.empty:
        alertas.append({
            "Alerta": "Sem indicadores-chave selecionados",
            "Interpretação": "Há dados públicos importados, mas a seleção gerencial não encontrou indicadores amplos com segurança.",
            "Ação sugerida": "Revisar dicionário, curadoria e nomes oficiais das variáveis."
        })
        return pd.DataFrame(alertas)

    eixos_esperados = [
        "População residente",
        "Domicílios particulares ocupados",
        "Alfabetização",
        "Esgotamento sanitário",
        "Abastecimento de água",
    ]
    faltantes = [e for e in eixos_esperados if e not in resumo["Indicador-chave"].astype(str).tolist()]
    if faltantes:
        alertas.append({
            "Alerta": "Indicadores-chave incompletos",
            "Interpretação": "Alguns eixos prioritários não foram selecionados automaticamente: " + "; ".join(faltantes),
            "Ação sugerida": "Validar se as bases correspondentes foram importadas e se o dicionário oficial foi aplicado."
        })

    suspeitos = resumo[
        resumo["Indicador selecionado"].astype(str).str.lower().str.contains(
            "responsável|sexo masculino|sexo feminino|cor ou raça|apartamento|tipo de espécie|não possui ligação|domicílios coletivos|falecid|falecimento|óbito|obito|morte",
            na=False,
            regex=True
        )
    ]
    if not suspeitos.empty:
        alertas.append({
            "Alerta": "Possível indicador específico no resumo",
            "Interpretação": "O resumo ainda selecionou indicador com recorte específico: " + "; ".join(suspeitos["Indicador-chave"].astype(str).tolist()),
            "Ação sugerida": "Revisar curadoria/seleção desse eixo."
        })

    derivados = resumo[resumo["Fonte"].astype(str).eq("Indicador derivado")]
    if not derivados.empty:
        alertas.append({
            "Alerta": "Indicadores derivados incluídos",
            "Interpretação": "Alguns indicadores foram calculados pelo sistema a partir de numeradores e denominadores selecionados.",
            "Ação sugerida": "Usar como leitura preliminar e validar denominadores antes de uso oficial."
        })

    return pd.DataFrame(alertas)


def perfil_socioeducacional_gerencial(municipio: str) -> dict:
    df = _indicadores_municipio_raw(municipio)
    if df.empty:
        return {
            "ok": False,
            "mensagem": "Sem indicadores públicos consolidados para perfil gerencial.",
            "resumo": pd.DataFrame(),
            "alertas": pd.DataFrame(),
            "detalhe_essencial": pd.DataFrame(),
            "tabela_tecnica": pd.DataFrame(),
        }

    linhas = _add_derivados(_linhas_base_resumo(df))
    resumo = pd.DataFrame(linhas)
    if not resumo.empty and "Valor numérico" in resumo.columns:
        resumo_exibir = resumo.drop(columns=["Valor numérico", "_derivado"], errors="ignore")
    else:
        resumo_exibir = resumo

    alertas_df = _alertas(resumo_exibir)

    essenciais = df[
        df["status_exibicao"].astype(str).eq("Essencial")
        & ~df["_recorte_especifico"].astype(bool)
    ].copy()

    detalhe = essenciais[[
        "categoria", "indicador", "codigo_variavel", "valor_formatado", "tipo_medida",
        "tabela_origem", "candidato_indice", "justificativa"
    ]].rename(columns={
        "categoria": "Grupo",
        "indicador": "Indicador",
        "codigo_variavel": "Código",
        "valor_formatado": "Valor",
        "tipo_medida": "Tipo",
        "tabela_origem": "Fonte",
        "candidato_indice": "Candidato ao índice",
        "justificativa": "Justificativa",
    }).head(50)

    tecnica = df[[
        "categoria", "indicador", "codigo_variavel", "valor_formatado", "tipo_medida",
        "tabela_origem", "status_exibicao", "candidato_indice", "justificativa"
    ]].rename(columns={
        "categoria": "Categoria",
        "indicador": "Indicador",
        "codigo_variavel": "Código",
        "valor_formatado": "Valor",
        "tipo_medida": "Tipo",
        "tabela_origem": "Tabela origem",
        "status_exibicao": "Status",
        "candidato_indice": "Candidato índice",
        "justificativa": "Justificativa",
    }).head(250)

    return {
        "ok": True,
        "mensagem": "Perfil gerencial gerado.",
        "resumo": resumo_exibir,
        "alertas": alertas_df,
        "detalhe_essencial": detalhe,
        "tabela_tecnica": tecnica,
        "total_indicadores": int(len(df)),
        "total_essenciais": int(len(essenciais)),
        "total_resumo": int(len(resumo_exibir)),
    }
