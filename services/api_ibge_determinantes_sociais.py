from __future__ import annotations

import re
import unicodedata
from typing import Any

import pandas as pd
import requests

UF_MT = "51"
ANO_CENSO = 2022
BASE = "https://servicodados.ibge.gov.br/api/v3/agregados"
TIMEOUT = 60
HEADERS = {"User-Agent": "aps-inteligencia-ses-mt/ibge-determinantes-sociais/1.0"}


def _get_json(url: str) -> Any:
    resp = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()


def _num(valor: Any) -> float | None:
    if valor is None:
        return None
    txt = str(valor).strip()
    if txt in {"", "-", "...", "X", "x", "None", "nan"}:
        return None
    # SIDRA costuma devolver decimal com vírgula. Se houver ponto e vírgula, assume pt-BR.
    if "," in txt:
        txt = txt.replace(".", "").replace(",", ".")
    try:
        return float(txt)
    except Exception:
        return None


def _so_num(valor: Any) -> str:
    return re.sub(r"\D+", "", str(valor or ""))


def _norm(txt: Any) -> str:
    txt = str(txt or "").strip().lower()
    txt = unicodedata.normalize("NFKD", txt).encode("ASCII", "ignore").decode("ASCII")
    txt = re.sub(r"[^a-z0-9]+", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def _limpar_municipio(nome: Any) -> str:
    nome = str(nome or "").strip()
    nome = re.sub(r"\s*-\s*MT$", "", nome, flags=re.I)
    return nome


def _metadata(agregado: str) -> dict[str, Any]:
    url = f"{BASE}/{agregado}/metadados"
    meta = _get_json(url)
    if isinstance(meta, list) and meta:
        return meta[0]
    if isinstance(meta, dict):
        return meta
    return {}


def _variaveis(meta: dict[str, Any]) -> list[dict[str, Any]]:
    vars_ = meta.get("variaveis") or meta.get("variáveis") or []
    if isinstance(vars_, dict):
        return list(vars_.values())
    return vars_ if isinstance(vars_, list) else []


def _classificacoes(meta: dict[str, Any]) -> list[dict[str, Any]]:
    cls = meta.get("classificacoes") or meta.get("classificações") or []
    if isinstance(cls, dict):
        return list(cls.values())
    return cls if isinstance(cls, list) else []


def _pick_variavel(meta: dict[str, Any], prefer_percentual: bool = False, palavras: list[str] | None = None) -> str:
    palavras = palavras or []
    candidatos = []
    for v in _variaveis(meta):
        vid = str(v.get("id") or v.get("codigo") or v.get("cod") or "")
        nome = str(v.get("nome") or "")
        unidade = str(v.get("unidade") or v.get("medida") or "")
        n = _norm(nome + " " + unidade)
        if not vid:
            continue
        score = 0
        if prefer_percentual and ("%" in unidade or "percent" in n or "taxa" in n):
            score += 20
        if any(_norm(p) in n for p in palavras):
            score += 10
        if "total" in n:
            score += 2
        if "domicilio" in n or "pessoas" in n or "taxa" in n:
            score += 3
        candidatos.append((score, vid, nome, unidade))
    candidatos.sort(reverse=True, key=lambda x: x[0])
    if candidatos:
        return candidatos[0][1]
    return "93"  # variável frequentemente usada como total em tabelas do Censo/SIDRA


def _pick_classificacao(meta: dict[str, Any], termos_nome: list[str]) -> tuple[str | None, list[dict[str, Any]]]:
    termos = [_norm(t) for t in termos_nome]
    melhor = None
    melhor_score = -1
    for c in _classificacoes(meta):
        cid = str(c.get("id") or c.get("codigo") or c.get("cod") or "")
        nome = str(c.get("nome") or "")
        n = _norm(nome)
        score = sum(1 for t in termos if t in n)
        if score > melhor_score and cid:
            melhor = c
            melhor_score = score
    if not melhor or melhor_score <= 0:
        return None, []
    categorias = melhor.get("categorias") or melhor.get("category") or []
    if isinstance(categorias, dict):
        categorias = list(categorias.values())
    return str(melhor.get("id") or melhor.get("codigo") or melhor.get("cod")), categorias if isinstance(categorias, list) else []


def _categoria_nome(resultado: dict[str, Any]) -> str:
    # SIDRA costuma devolver classificacoes: [{id,nome,categoria:{id,nome}}]
    partes = []
    for c in resultado.get("classificacoes", []) or resultado.get("classificações", []) or []:
        cat = c.get("categoria") or {}
        if isinstance(cat, dict):
            nome = cat.get("nome") or cat.get("name")
            if nome:
                partes.append(str(nome))
    return " | ".join(partes) if partes else "Total"


def _query(agregado: str, variavel: str, classificacao: str | None = None) -> tuple[str, Any]:
    extra = f"&classificacao={classificacao}[all]" if classificacao else ""
    url = f"{BASE}/{agregado}/periodos/{ANO_CENSO}/variaveis/{variavel}?localidades=N6[N3[{UF_MT}]]{extra}"
    return url, _get_json(url)


def _query_raw_classificacao(agregado: str, variavel: str, classificacao_raw: str | None = None) -> tuple[str, Any]:
    """Consulta SIDRA aceitando classificação já no padrão 301[all]|1817[all]."""
    extra = f"&classificacao={classificacao_raw}" if classificacao_raw else ""
    url = f"{BASE}/{agregado}/periodos/{ANO_CENSO}/variaveis/{variavel}?localidades=N6[N3[{UF_MT}]]{extra}"
    return url, _get_json(url)


def _payload_categoria(payload: Any, indicador_base: str, url: str) -> pd.DataFrame:
    linhas = []
    if not payload:
        return pd.DataFrame()
    bloco = payload[0] if isinstance(payload, list) else payload
    for resultado in bloco.get("resultados", []) or []:
        categoria = _categoria_nome(resultado)
        for serie in resultado.get("series", []) or []:
            loc = serie.get("localidade", {}) or {}
            codigo = _so_num(loc.get("id"))
            if not codigo.startswith(UF_MT):
                continue
            municipio = _limpar_municipio(loc.get("nome"))
            for ano, valor in (serie.get("serie", {}) or {}).items():
                linhas.append({
                    "municipio": municipio,
                    "codigo_ibge": codigo,
                    "ano": int(_so_num(ano) or ANO_CENSO),
                    "competencia": str(ano),
                    "categoria": categoria,
                    "indicador_base": indicador_base,
                    "valor": _num(valor),
                    "url_origem": url,
                })
    return pd.DataFrame(linhas)


def _gerar_percentuais_por_categoria(df_cat: pd.DataFrame, mapa_indicadores: dict[str, list[str]], fonte_base: str) -> pd.DataFrame:
    if df_cat.empty:
        return pd.DataFrame()
    linhas = []
    df = df_cat.copy()
    df["cat_norm"] = df["categoria"].map(_norm)
    for (codigo, municipio), g in df.groupby(["codigo_ibge", "municipio"], dropna=False):
        total = None
        # Preferir categoria total explícita; senão soma categorias não-total.
        totais = g[g["cat_norm"].isin(["total", "total geral"])]
        if not totais.empty:
            total = pd.to_numeric(totais["valor"], errors="coerce").max()
        if not total or pd.isna(total) or total == 0:
            total = pd.to_numeric(g.loc[~g["cat_norm"].str.contains("total", na=False), "valor"], errors="coerce").sum()
        if not total or pd.isna(total) or total == 0:
            continue
        for indicador, termos in mapa_indicadores.items():
            termos_norm = [_norm(t) for t in termos]
            mask = g["cat_norm"].apply(lambda x: any(t in x for t in termos_norm))
            numerador = pd.to_numeric(g.loc[mask, "valor"], errors="coerce").sum()
            if numerador and not pd.isna(numerador):
                pct = round(float(numerador) / float(total) * 100.0, 4)
                linhas.append({
                    "municipio": municipio,
                    "codigo_ibge": codigo,
                    "ano": ANO_CENSO,
                    "competencia": str(ANO_CENSO),
                    "indicador": indicador,
                    "valor": pct,
                    "fonte": fonte_base,
                    "observacao": "Percentual calculado a partir das categorias da tabela SIDRA/Censo 2022.",
                })
    return pd.DataFrame(linhas)


def _carregar_taxa_alfabetizacao() -> pd.DataFrame:
    agregado = "9543"
    meta = _metadata(agregado)
    variavel = _pick_variavel(meta, prefer_percentual=True, palavras=["taxa", "alfabetizacao"])
    url, payload = _query(agregado, variavel, None)
    bruto = _payload_categoria(payload, "taxa_alfabetizacao_pct", url)
    linhas = []
    for row in bruto.to_dict("records"):
        val = row.get("valor")
        if val is None:
            continue
        linhas.append({
            "municipio": row.get("municipio"),
            "codigo_ibge": row.get("codigo_ibge"),
            "ano": ANO_CENSO,
            "competencia": str(ANO_CENSO),
            "indicador": "taxa_alfabetizacao_pct",
            "valor": val,
            "fonte": "IBGE/SIDRA Censo 2022 — Educação",
            "observacao": f"Tabela 9543; variável {variavel}.",
        })
        if 0 <= float(val) <= 100:
            linhas.append({
                "municipio": row.get("municipio"),
                "codigo_ibge": row.get("codigo_ibge"),
                "ano": ANO_CENSO,
                "competencia": str(ANO_CENSO),
                "indicador": "taxa_analfabetismo_estimado_pct",
                "valor": round(100.0 - float(val), 4),
                "fonte": "IBGE/SIDRA Censo 2022 — Educação",
                "observacao": "Calculado como 100 - taxa de alfabetização das pessoas de 15 anos ou mais.",
            })
    return pd.DataFrame(linhas)


def _carregar_nivel_instrucao() -> pd.DataFrame:
    agregado = "10061"
    meta = _metadata(agregado)
    variavel = _pick_variavel(meta, prefer_percentual=False, palavras=["pessoas", "18 anos"])
    class_id, _cats = _pick_classificacao(meta, ["nivel", "instrucao"])
    if not class_id:
        return pd.DataFrame()
    url, payload = _query(agregado, variavel, class_id)
    cats = _payload_categoria(payload, "nivel_instrucao", url)
    mapa = {
        "nivel_instrucao_baixo_pct": ["sem instrucao", "fundamental incompleto"],
        "nivel_instrucao_medio_ou_superior_pct": ["medio completo", "superior incompleto", "superior completo"],
        "nivel_instrucao_superior_completo_pct": ["superior completo"],
    }
    out = _gerar_percentuais_por_categoria(cats, mapa, "IBGE/SIDRA Censo 2022 — Educação")
    if not out.empty:
        out["observacao"] = out["observacao"] + f" Tabela 10061; variável {variavel}; classificação {class_id}."
    return out




def _categorias_meta(meta: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
    """Retorna {classificacao_id: [(categoria_id, nome)]} a partir do metadado SIDRA."""
    out: dict[str, list[tuple[str, str]]] = {}
    for c in _classificacoes(meta):
        cid = str(c.get("id") or c.get("codigo") or c.get("cod") or "").strip()
        if not cid:
            continue
        cats = c.get("categorias") or c.get("category") or []
        if isinstance(cats, dict):
            cats = list(cats.values())
        pares = []
        for cat in cats if isinstance(cats, list) else []:
            if not isinstance(cat, dict):
                continue
            cat_id = str(cat.get("id") or cat.get("codigo") or cat.get("cod") or "").strip()
            nome = str(cat.get("nome") or cat.get("name") or "").strip()
            if cat_id and nome:
                pares.append((cat_id, nome))
        out[cid] = pares
    return out


def _ids_variaveis(meta: dict[str, Any]) -> list[str]:
    """Lista variáveis tentando priorizar contagens absolutas de domicílios/pessoas."""
    candidatos = []
    for v in _variaveis(meta):
        vid = str(v.get("id") or v.get("codigo") or v.get("cod") or "").strip()
        if not vid:
            continue
        nome = str(v.get("nome") or "")
        unidade = str(v.get("unidade") or v.get("medida") or "")
        n = _norm(nome + " " + unidade)
        score = 0
        if "percent" in n or "%" in unidade:
            score -= 30
        if "domicilio" in n or "domicilios" in n:
            score += 20
        if "morador" in n or "moradores" in n or "pessoas" in n:
            score += 12
        if "total" in n:
            score += 6
        if "taxa" in n:
            score -= 10
        candidatos.append((score, vid, nome, unidade))
    candidatos.sort(key=lambda x: (-x[0], x[1]))
    ids = []
    for _score, vid, _nome, _unidade in candidatos:
        if vid not in ids:
            ids.append(vid)
    # Fallbacks frequentes no SIDRA/Censo.
    for fallback in ["93", "1000381"]:
        if fallback not in ids:
            ids.append(fallback)
    return ids[:10]


def _query_categoria_ids(agregado: str, variavel: str, class_id: str, cat_ids: list[str]) -> tuple[str, Any]:
    cats = ",".join([str(x) for x in cat_ids if str(x).strip()]) or "all"
    url = f"{BASE}/{agregado}/periodos/{ANO_CENSO}/variaveis/{variavel}?localidades=N6[N3[{UF_MT}]]&classificacao={class_id}[{cats}]"
    return url, _get_json(url)


def _gerar_percentuais_por_categoria_avancado(
    df_cat: pd.DataFrame,
    mapas: dict[str, list[str]],
    fonte_base: str,
    agregado: str,
    variavel: str,
    classificacao: str,
) -> pd.DataFrame:
    """Gera percentuais por categoria com diagnóstico mais explícito.

    Diferente da função genérica, esta aceita mapas com termos mais longos e
    registra a categoria usada na observação. Isso ajuda a auditar se o IBGE
    alterou o nome das categorias do SIDRA.
    """
    if df_cat.empty:
        return pd.DataFrame()
    df = df_cat.copy()
    df["cat_norm"] = df["categoria"].map(_norm)
    linhas = []
    for (codigo, municipio), g in df.groupby(["codigo_ibge", "municipio"], dropna=False):
        valores = pd.to_numeric(g["valor"], errors="coerce")
        total = None
        totais = g[g["cat_norm"].isin(["total", "total geral"])]
        if not totais.empty:
            total = pd.to_numeric(totais["valor"], errors="coerce").max()
        if total is None or pd.isna(total) or float(total) == 0:
            # Se não houver Total explícito, soma as categorias não-total.
            total = valores[g["cat_norm"].ne("total")].sum()
        if total is None or pd.isna(total) or float(total) == 0:
            continue
        for indicador, termos in mapas.items():
            termos_norm = [_norm(t) for t in termos]
            mask = g["cat_norm"].apply(lambda x: any(t and t in x for t in termos_norm))
            if not mask.any():
                continue
            numerador = pd.to_numeric(g.loc[mask, "valor"], errors="coerce").sum()
            if numerador is None or pd.isna(numerador):
                continue
            pct = round(float(numerador) / float(total) * 100.0, 4)
            categorias_usadas = "; ".join(g.loc[mask, "categoria"].astype(str).dropna().unique().tolist()[:8])
            linhas.append({
                "municipio": municipio,
                "codigo_ibge": codigo,
                "ano": ANO_CENSO,
                "competencia": str(ANO_CENSO),
                "indicador": indicador,
                "valor": pct,
                "fonte": fonte_base,
                "observacao": (
                    f"Percentual calculado a partir do SIDRA/Censo 2022. "
                    f"Agregado {agregado}; variável {variavel}; classificação {classificacao}; "
                    f"categorias usadas: {categorias_usadas}."
                ),
            })
    return pd.DataFrame(linhas)


def _tentar_saneamento_por_tabela(
    agregado: str,
    fonte: str,
    termos_classificacao: list[str],
    mapas: dict[str, list[str]],
) -> tuple[pd.DataFrame, list[str]]:
    """Tenta carregar saneamento usando metadados reais da tabela.

    O patch anterior hardcodava IDs de classificação. Em algumas tabelas do
    Censo 2022 isso pode variar ou a classificação esperada não ser a primeira.
    Agora o conector lê os metadados, procura categorias por nome e consulta só
    as classificações/categorias que realmente contêm termos como rede geral,
    fossa, coletado etc.
    """
    logs: list[str] = []
    meta = _metadata(agregado)
    variaveis = _ids_variaveis(meta)
    categorias_por_class = _categorias_meta(meta)
    termos_cls_norm = [_norm(t) for t in termos_classificacao]
    termos_mapa = [_norm(t) for termos in mapas.values() for t in termos]

    class_candidates: list[tuple[int, str, list[tuple[str, str]]]] = []
    for cid, cats in categorias_por_class.items():
        nomes = " ".join([nome for _cat_id, nome in cats])
        # Score por nome da classificação + categorias existentes.
        class_nome = ""
        for c in _classificacoes(meta):
            ccid = str(c.get("id") or c.get("codigo") or c.get("cod") or "").strip()
            if ccid == cid:
                class_nome = str(c.get("nome") or "")
                break
        n = _norm(class_nome + " " + nomes)
        score = 0
        score += sum(10 for t in termos_cls_norm if t and t in n)
        score += sum(3 for t in termos_mapa if t and t in n)
        if score > 0:
            class_candidates.append((score, cid, cats))
    class_candidates.sort(key=lambda x: -x[0])

    if not class_candidates:
        logs.append(f"{agregado}: nenhuma classificação compatível encontrada nos metadados")
        return pd.DataFrame(), logs

    for _score, class_id, cats in class_candidates[:5]:
        # Consulta todas as categorias da classificação candidata. Isso é mais seguro
        # que tentar adivinhar IDs. Se for grande, limita a 80 categorias.
        cat_ids = [cat_id for cat_id, _nome in cats][:80] or ["all"]
        for variavel in variaveis:
            try:
                url, payload = _query_categoria_ids(agregado, variavel, class_id, cat_ids)
                cats_df = _payload_categoria(payload, "saneamento_censo_2022", url)
                logs.append(f"{agregado}/{variavel}/class={class_id}: categorias={cats_df['categoria'].nunique() if not cats_df.empty else 0}; linhas={len(cats_df)}")
                out = _gerar_percentuais_por_categoria_avancado(
                    cats_df, mapas, fonte, agregado, variavel, class_id
                )
                if not out.empty:
                    return out, logs
            except Exception as exc:
                logs.append(f"{agregado}/{variavel}/class={class_id}: erro={exc}")
    return pd.DataFrame(), logs


def _carregar_saneamento_tabelas_oficiais() -> pd.DataFrame:
    """Carrega água, esgoto e lixo em tabelas explícitas do Censo 2022.

    Esta versão não depende mais de IDs fixos de classificação. Ela lê os
    metadados do SIDRA e identifica as categorias reais retornadas pelo IBGE.
    """
    specs = [
        {
            "agregado": "6804",
            "fonte": "IBGE/SIDRA Censo 2022 — Água/abastecimento",
            "termos_classificacao": ["abastecimento de agua", "forma de abastecimento", "canalizacao", "agua"],
            "mapa": {
                "abastecimento_agua_rede_pct": [
                    "rede geral de distribuicao", "rede geral", "rede de distribuicao", "rede publica"
                ],
            },
        },
        {
            "agregado": "6805",
            "fonte": "IBGE/SIDRA Censo 2022 — Esgotamento sanitário",
            "termos_classificacao": ["esgotamento sanitario", "tipo de esgotamento", "sanitario"],
            "mapa": {
                "esgotamento_rede_pct": [
                    "rede geral rede pluvial ou fossa ligada a rede",
                    "rede geral", "rede pluvial", "fossa ligada a rede", "rede coletora"
                ],
                "esgotamento_adequado_rede_ou_fossa_pct": [
                    "rede geral rede pluvial ou fossa ligada a rede",
                    "rede geral", "rede pluvial", "fossa ligada a rede", "rede coletora",
                    "fossa septica", "fossa filtro", "fossa séptica"
                ],
            },
        },
        {
            "agregado": "6892",
            "fonte": "IBGE/SIDRA Censo 2022 — Destino do lixo",
            "termos_classificacao": ["destino do lixo", "lixo", "coleta"],
            "mapa": {
                "lixo_coletado_pct": [
                    "coletado no domicilio por servico de limpeza",
                    "depositado em cacamba de servico de limpeza",
                    "coletado", "servico de limpeza", "cacamba", "caçamba"
                ],
            },
        },
        # Tabela 9397 é citada em materiais do IBGE para proporção de moradores
        # em domicílios com esgotamento por rede/fossa. Entra como reforço caso
        # 6805 não retorne o adequado esperado.
        {
            "agregado": "9397",
            "fonte": "IBGE/SIDRA Censo 2022 — Esgotamento sanitário",
            "termos_classificacao": ["tipo de esgotamento", "esgotamento sanitario"],
            "mapa": {
                "esgotamento_rede_pct": [
                    "rede geral rede pluvial ou fossa ligada a rede",
                    "rede geral", "rede pluvial", "fossa ligada a rede", "rede coletora"
                ],
                "esgotamento_adequado_rede_ou_fossa_pct": [
                    "rede geral rede pluvial ou fossa ligada a rede",
                    "fossa septica", "fossa filtro", "rede geral", "rede pluvial", "fossa ligada a rede"
                ],
            },
        },
    ]

    saidas = []
    logs = []
    # Carrega cada indicador/tabela e combina resultados, sem sobrescrever educação.
    indicadores_encontrados: set[str] = set()
    for spec in specs:
        out, lg = _tentar_saneamento_por_tabela(
            spec["agregado"], spec["fonte"], spec["termos_classificacao"], spec["mapa"]
        )
        logs.extend(lg)
        if not out.empty:
            # Evita duplicar o mesmo indicador se 9397 e 6805 retornarem a mesma coisa.
            novos = out[~out["indicador"].isin(indicadores_encontrados)].copy()
            if not novos.empty:
                saidas.append(novos)
                indicadores_encontrados.update(novos["indicador"].unique().tolist())

    if saidas:
        final = pd.concat(saidas, ignore_index=True)
        final["diagnostico_carga"] = "Saneamento explícito carregado. " + " | ".join(logs[:8])
        return final

    # Retorna vazio, mas com erro detalhado para a tela/bloco exibir caso nada carregue.
    return pd.DataFrame()

def _carregar_saneamento_por_agregado(agregado: str) -> pd.DataFrame:
    meta = _metadata(agregado)
    variavel = _pick_variavel(meta, prefer_percentual=False, palavras=["domicilios", "moradores", "pessoas"])
    saidas = []
    alvo = [
        (["abastecimento", "agua"], {"abastecimento_agua_rede_pct": ["rede geral"]}),
        (["esgotamento", "sanitario"], {
            "esgotamento_rede_pct": ["rede geral", "rede coletora", "rede pluvial"],
            "esgotamento_adequado_rede_ou_fossa_pct": ["rede geral", "rede coletora", "rede pluvial", "fossa septica", "fossa séptica"],
        }),
        (["lixo", "destino"], {"lixo_coletado_pct": ["coletado", "servico de limpeza", "cacamba"]}),
    ]
    for termos_class, mapa in alvo:
        class_id, _cats = _pick_classificacao(meta, termos_class)
        if not class_id:
            continue
        try:
            url, payload = _query(agregado, variavel, class_id)
            cats = _payload_categoria(payload, "saneamento", url)
            out = _gerar_percentuais_por_categoria(cats, mapa, "IBGE/SIDRA Censo 2022 — Domicílios/Saneamento")
            if not out.empty:
                out["observacao"] = out["observacao"] + f" Agregado {agregado}; variável {variavel}; classificação {class_id}."
                saidas.append(out)
        except Exception:
            continue
    return pd.concat(saidas, ignore_index=True) if saidas else pd.DataFrame()


def carregar_determinantes_sociais_basicos_ibge() -> pd.DataFrame:
    """Carrega determinantes sociais básicos do Censo 2022 via SIDRA.

    Produz indicadores longos para a tabela indicadores_municipais, sem consolidar
    automaticamente na Base Completa. O objetivo é alimentar o módulo social com
    água, esgoto, lixo, alfabetização/analfabetismo e nível de instrução.
    """
    partes = []
    erros = []

    # Educação — taxa de alfabetização/analfabetismo.
    try:
        partes.append(_carregar_taxa_alfabetizacao())
    except Exception as exc:
        erros.append(f"alfabetização: {exc}")

    # Educação — nível de instrução.
    try:
        partes.append(_carregar_nivel_instrucao())
    except Exception as exc:
        erros.append(f"nível de instrução: {exc}")

    # Saneamento/domicílios — primeiro usa tabelas explícitas do Censo 2022
    # para separar água, esgoto e lixo; se não retornar, cai no método genérico.
    try:
        saneamento_explicito = _carregar_saneamento_tabelas_oficiais()
        if not saneamento_explicito.empty:
            partes.append(saneamento_explicito)
    except Exception as exc:
        erros.append(f"saneamento explícito 6804/6805/6892: {exc}")

    if not any(isinstance(p, pd.DataFrame) and not p.empty and set(p.get("indicador", pd.Series(dtype=str)).astype(str)).intersection({"abastecimento_agua_rede_pct", "esgotamento_rede_pct", "esgotamento_adequado_rede_ou_fossa_pct", "lixo_coletado_pct"}) for p in partes):
        for agregado in ["6813", "6732", "9397"]:
            try:
                saneamento = _carregar_saneamento_por_agregado(agregado)
                if not saneamento.empty:
                    partes.append(saneamento)
                    break
            except Exception as exc:
                erros.append(f"saneamento agregado {agregado}: {exc}")

    partes = [p for p in partes if isinstance(p, pd.DataFrame) and not p.empty]
    if not partes:
        detalhe = " | ".join(erros) if erros else "Nenhum indicador retornado."
        raise RuntimeError("Não foi possível carregar determinantes sociais básicos do IBGE/SIDRA. " + detalhe)

    df = pd.concat(partes, ignore_index=True)
    # Segurança: mantém somente municípios de MT e indicadores válidos.
    df["codigo_ibge"] = df["codigo_ibge"].astype(str).str.extract(r"(51\d{5})", expand=False)
    df = df[df["codigo_ibge"].notna()].copy()
    df = df.dropna(subset=["municipio", "indicador", "valor"], how="any")
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
    df = df[df["valor"].notna()].copy()
    df = df.drop_duplicates(subset=["codigo_ibge", "indicador", "ano"], keep="last")
    df["status_api"] = "carregado"
    if erros:
        df["diagnostico_carga"] = "Algumas tentativas auxiliares falharam, mas a base final possui indicadores úteis. " + " | ".join(erros[:5])
    else:
        df["diagnostico_carga"] = "Carga concluída."
    return df.sort_values(["indicador", "municipio"]).reset_index(drop=True)


def testar_determinantes_sociais_basicos_ibge() -> pd.DataFrame:
    return carregar_determinantes_sociais_basicos_ibge()
