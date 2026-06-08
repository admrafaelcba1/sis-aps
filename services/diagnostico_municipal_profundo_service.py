
from __future__ import annotations

import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd


def _base_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def db_path() -> Path:
    return _base_dir() / "data" / "aps_inteligencia.db"


def conectar() -> sqlite3.Connection:
    con = sqlite3.connect(db_path())
    con.row_factory = sqlite3.Row
    return con


def normalizar_texto(txt: Any) -> str:
    if txt is None:
        return ""
    s = str(txt).strip().upper()
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode("ASCII")
    s = re.sub(r"\s+", " ", s)
    return s


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def listar_tabelas() -> List[str]:
    if not db_path().exists():
        return []
    with conectar() as con:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    return [r[0] for r in rows]


def colunas_tabela(tabela: str) -> List[str]:
    try:
        with conectar() as con:
            rows = con.execute(f"PRAGMA table_info({_q(tabela)})").fetchall()
        return [r[1] for r in rows]
    except Exception:
        return []


def contar_linhas(tabela: str) -> int:
    try:
        with conectar() as con:
            return int(con.execute(f"SELECT COUNT(*) FROM {_q(tabela)}").fetchone()[0])
    except Exception:
        return 0


def encontrar_coluna_municipio(cols: List[str]) -> str | None:
    preferidas = [
        "municipio", "município", "nome_municipio", "nome município", "no_municipio",
        "no município", "nm_mun", "nm_municipio", "municipio_nome", "cidade",
        "nome_do_municipio", "nome do município", "municipio_ibge", "municipio_cnes"
    ]
    norm = {normalizar_texto(c).replace("_", " "): c for c in cols}
    for p in preferidas:
        key = normalizar_texto(p).replace("_", " ")
        if key in norm:
            return norm[key]
    for c in cols:
        nc = normalizar_texto(c)
        if "MUNICIP" in nc or nc in {"NM_MUN", "NO_MUN"}:
            return c
    return None


def encontrar_coluna_ano(cols: List[str]) -> str | None:
    for c in cols:
        nc = normalizar_texto(c)
        if nc in {"ANO", "ANO_REFERENCIA", "ANO REF", "NU_ANO", "COMPETENCIA_ANO"} or "ANO" == nc[-3:]:
            return c
    for c in cols:
        if "ANO" in normalizar_texto(c):
            return c
    return None


def encontrar_coluna_mes(cols: List[str]) -> str | None:
    for c in cols:
        nc = normalizar_texto(c)
        if nc in {"MES", "MÊS", "MES_REFERENCIA", "COMPETENCIA_MES"} or "MES" in nc:
            return c
    return None


def tabelas_por_eixo() -> Dict[str, List[Dict[str, Any]]]:
    eixos = {
        "Demografia e território": ["ibge", "setor", "territ", "popul", "geo_municip", "assent", "indig", "quilomb", "rural", "area"],
        "APS, CNES e capacidade instalada": ["cnes", "ubs", "estabelec", "equipe", "profission", "esf", "esb", "aps", "ine", "leito", "hospital"],
        "Renda, vulnerabilidade e proteção social": ["mds", "cadun", "bolsa", "bpc", "pobreza", "renda", "socio", "atlas", "vulner"],
        "Educação e intersetorialidade": ["inep", "escola", "educ", "alfabet", "escolar", "matric", "pse"],
        "Epidemiologia, nascimentos e mortalidade": ["sinan", "sim", "sinasc", "tuberc", "hansen", "viol", "obito", "óbito", "mortal", "nasc", "agravo", "doenca"],
        "Georreferenciamento e acesso": ["dist", "georef", "vazio", "idt", "bairro", "localidade", "rota", "hospital", "retaguarda"],
    }
    out = {k: [] for k in eixos}
    for t in listar_tabelas():
        nt = normalizar_texto(t).lower()
        cols = colunas_tabela(t)
        info = {"tabela": t, "linhas": contar_linhas(t), "colunas": len(cols), "coluna_municipio": encontrar_coluna_municipio(cols), "coluna_ano": encontrar_coluna_ano(cols)}
        placed = False
        for eixo, pats in eixos.items():
            if any(p in nt for p in pats):
                out[eixo].append(info)
                placed = True
        if not placed and info["coluna_municipio"]:
            out.setdefault("Outras bases municipais", []).append(info)
    return out


def listar_municipios() -> List[str]:
    candidatos = ["socio_consolidado_municipal", "socio_indicadores_municipais", "base_municipal_consolidada", "dim_municipio", "municipios", "geo_municipios"]
    encontrados = []
    with conectar() as con:
        for t in candidatos + listar_tabelas():
            cols = colunas_tabela(t)
            col = encontrar_coluna_municipio(cols)
            if not col:
                continue
            try:
                sql = f"SELECT DISTINCT {_q(col)} AS municipio FROM {_q(t)} WHERE {_q(col)} IS NOT NULL LIMIT 300"
                vals = [r[0] for r in con.execute(sql).fetchall()]
                encontrados.extend([v for v in vals if v and str(v).strip()])
                if len(set(map(normalizar_texto, encontrados))) >= 120:
                    break
            except Exception:
                pass
    # dedup mantendo forma original mais legível
    seen, out = set(), []
    for v in encontrados:
        n = normalizar_texto(v)
        if n not in seen:
            seen.add(n); out.append(str(v).strip().title())
    return sorted(out)


def _read_municipio_rows(tabela: str, municipio: str, limite: int = 5000) -> pd.DataFrame:
    cols = colunas_tabela(tabela)
    if not cols:
        return pd.DataFrame()
    col_mun = encontrar_coluna_municipio(cols)
    with conectar() as con:
        try:
            if col_mun:
                sql = f"SELECT * FROM {_q(tabela)} WHERE UPPER(TRIM(CAST({_q(col_mun)} AS TEXT))) = UPPER(TRIM(?)) LIMIT {int(limite)}"
                df = pd.read_sql_query(sql, con, params=[municipio])
                if df.empty:
                    # fallback por normalização em amostra maior, útil para acentos/capitalização
                    sql2 = f"SELECT * FROM {_q(tabela)} LIMIT {min(int(limite), 12000)}"
                    tmp = pd.read_sql_query(sql2, con)
                    if col_mun in tmp.columns:
                        df = tmp[tmp[col_mun].map(normalizar_texto) == normalizar_texto(municipio)].copy()
                return df
            else:
                return pd.DataFrame()
        except Exception:
            return pd.DataFrame()


def _numeric_series(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def resumo_tabela_municipio(tabela: str, municipio: str) -> Dict[str, Any]:
    df = _read_municipio_rows(tabela, municipio)
    cols = colunas_tabela(tabela)
    if df.empty:
        return {"tabela": tabela, "linhas_municipio": 0, "colunas": len(cols), "indicadores": [], "anos": [], "preview": pd.DataFrame()}
    indicadores = []
    for c in df.columns:
        if c.lower() in {"id", "codigo", "cod", "municipio"}:
            continue
        s = _numeric_series(df, c)
        valid = s.dropna()
        if len(valid) == 0:
            continue
        nome = str(c)
        # Para tabelas com várias linhas, soma contagens e média percentuais/scores/taxas.
        nc = normalizar_texto(nome)
        if any(x in nc for x in ["PERC", "%", "TAXA", "RAZAO", "INDICE", "SCORE", "COBERTURA", "PROPORCAO"]):
            valor = float(valid.mean())
            modo = "média"
        else:
            valor = float(valid.sum()) if len(valid) > 1 else float(valid.iloc[0])
            modo = "soma" if len(valid) > 1 else "valor"
        indicadores.append({"indicador": nome, "valor": valor, "modo": modo, "preenchimento": int(len(valid))})
    indicadores = sorted(indicadores, key=lambda x: (x["preenchimento"], abs(x["valor"])), reverse=True)[:35]
    ano_col = encontrar_coluna_ano(list(df.columns))
    anos = []
    if ano_col:
        anos = sorted([str(x) for x in df[ano_col].dropna().unique()])[-10:]
    return {"tabela": tabela, "linhas_municipio": len(df), "colunas": len(df.columns), "indicadores": indicadores, "anos": anos, "preview": df.head(100)}


def coletar_diagnostico_municipal(municipio: str) -> Dict[str, Any]:
    eixos = tabelas_por_eixo()
    dados = {}
    for eixo, tabs in eixos.items():
        itens = []
        for info in tabs:
            # priorizar tabelas com coluna municipal; se não tiver, só mostra inventário
            if info.get("coluna_municipio"):
                resumo = resumo_tabela_municipio(info["tabela"], municipio)
                if resumo["linhas_municipio"] > 0:
                    itens.append({**info, **resumo})
        dados[eixo] = itens
    insights = gerar_insights(municipio, dados)
    historico = detectar_series_historicas(municipio, dados)
    return {"municipio": municipio, "eixos": dados, "insights": insights, "historico": historico}


def _buscar_indicador(dados: Dict[str, List[Dict[str, Any]]], termos: List[str]) -> Tuple[str, float] | None:
    termos_n = [normalizar_texto(t) for t in termos]
    for eixo, tabs in dados.items():
        for t in tabs:
            for ind in t.get("indicadores", []):
                ni = normalizar_texto(ind["indicador"])
                if all(term in ni for term in termos_n):
                    try:
                        return ind["indicador"], float(ind["valor"])
                    except Exception:
                        pass
    return None


def gerar_insights(municipio: str, dados: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    insights: List[Dict[str, str]] = []
    def add(eixo, situacao, evidencia, leitura, acao):
        insights.append({"eixo": eixo, "situação": situacao, "evidência": evidencia, "leitura": leitura, "ação sugerida": acao})

    achados = [
        ("Renda e vulnerabilidade", ["CADUNICO"], 40, "Ruim", "Alta proporção de população no CadÚnico sugere maior dependência de políticas públicas e necessidade de busca ativa integrada APS + Assistência Social."),
        ("Renda e vulnerabilidade", ["BOLSA", "FAMILIA"], 25, "Ruim", "Percentual elevado de beneficiários do Bolsa Família indica vulnerabilidade social relevante e necessidade de priorização de ações preventivas."),
        ("Saneamento", ["ESGOTAMENTO", "INADEQUADO"], 30, "Crítico", "Saneamento inadequado eleva risco de agravos evitáveis, parasitoses, diarreias e pressão sobre APS."),
        ("Capacidade APS", ["POPULACAO", "EQUIPE"], 3500, "Ruim", "População por equipe elevada indica possível sobrecarga e menor capacidade de acompanhamento longitudinal."),
        ("Georreferenciamento", ["DISTANCIA"], 5, "Ruim", "Distância elevada até UBS/APS sugere barreira territorial, especialmente em zonas rurais e comunidades dispersas."),
        ("Vulnerabilidade integrada", ["VULNERABILIDADE"], 60, "Crítico", "Score de vulnerabilidade alto exige análise intersetorial e priorização no planejamento municipal/regional."),
    ]
    for eixo, termos, limiar, sit, leitura in achados:
        got = _buscar_indicador(dados, termos)
        if got and got[1] >= limiar:
            nome, val = got
            add(eixo, sit, f"{nome}: {val:,.1f}".replace(",", "X").replace(".", ",").replace("X", "."), leitura,
                "Validar território, público afetado e estruturar plano com responsável, prazo, evidência e acompanhamento mensal.")

    # Cruzamentos compostos
    cad = _buscar_indicador(dados, ["CADUNICO"])
    san = _buscar_indicador(dados, ["ESGOTAMENTO", "INADEQUADO"])
    dist = _buscar_indicador(dados, ["DISTANCIA"])
    if cad and san and cad[1] >= 40 and san[1] >= 30:
        add("Cruzamento social-sanitário", "Crítico", f"CadÚnico elevado + saneamento inadequado elevado em {municipio}.",
            "A combinação de pobreza/vulnerabilidade e saneamento precário aumenta risco de adoecimento evitável e demanda ações de educação em saúde, vigilância e infraestrutura.",
            "Criar agenda intersetorial APS + Vigilância + Assistência Social + Obras/Saneamento, com microterritórios prioritários.")
    if cad and dist and cad[1] >= 40 and dist[1] >= 5:
        add("Cruzamento acesso-vulnerabilidade", "Crítico", f"Vulnerabilidade social elevada + distância territorial relevante em {municipio}.",
            "A população vulnerável tende a sofrer mais com deslocamento, custo de transporte e perda de continuidade do cuidado.",
            "Avaliar equipe itinerante, agenda rural, transporte sanitário, ponto de apoio ou reorganização de microáreas.")
    if not insights:
        add("Leitura geral", "Atenção", "Não foram encontrados gatilhos críticos pela régua automática atual.",
            "Isso não significa ausência de problemas; indica que a base precisa ser lida com validação local e análise dos detalhes por eixo.",
            "Revisar as abas por eixo, conferir dados ausentes e comparar o município com região de saúde/estado.")
    return insights


def detectar_series_historicas(municipio: str, dados: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    series = []
    for eixo, tabs in dados.items():
        for t in tabs:
            tabela = t["tabela"]
            cols = colunas_tabela(tabela)
            ano = encontrar_coluna_ano(cols)
            if not ano:
                continue
            df = _read_municipio_rows(tabela, municipio, limite=30000)
            if df.empty or ano not in df.columns:
                continue
            anos = sorted(pd.to_numeric(df[ano], errors="coerce").dropna().astype(int).unique().tolist())
            if len(anos) >= 2:
                # escolher 3 colunas numéricas mais úteis
                nums = []
                for c in df.columns:
                    if c == ano: continue
                    s = pd.to_numeric(df[c], errors="coerce")
                    if s.notna().sum() >= 2:
                        nums.append(c)
                series.append({"eixo": eixo, "tabela": tabela, "ano_col": ano, "anos": anos, "indicadores_possiveis": nums[:8]})
    return series[:20]


def montar_relatorio_textual(diag: Dict[str, Any]) -> str:
    municipio = diag["municipio"]
    linhas = [f"Diagnóstico municipal aprofundado — {municipio}", "", "1. Síntese executiva", ""]
    for ins in diag.get("insights", [])[:6]:
        linhas.append(f"- {ins['situação']} | {ins['eixo']}: {ins['leitura']} Evidência: {ins['evidência']}. Ação: {ins['ação sugerida']}")
    linhas += ["", "2. Bases consultadas por eixo", ""]
    for eixo, tabs in diag.get("eixos", {}).items():
        if not tabs:
            continue
        linhas.append(f"{eixo}: {len(tabs)} base(s) com registros para o município.")
        for t in tabs[:5]:
            linhas.append(f"  - {t['tabela']}: {t['linhas_municipio']} registro(s), {len(t.get('indicadores', []))} indicador(es) numéricos resumidos.")
    linhas += ["", "3. Observação metodológica", "O diagnóstico cruza as bases efetivamente carregadas no banco local. Valores ausentes não devem ser interpretados como zero. Indicadores automáticos servem como apoio à decisão e devem ser validados com ERS, município e áreas técnicas."]
    return "\n".join(linhas)
