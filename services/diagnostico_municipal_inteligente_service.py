from __future__ import annotations

import math
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


DB_NAME = "aps_inteligencia.db"


def base_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def db_path() -> Path:
    return base_dir() / "data" / DB_NAME


def conectar() -> sqlite3.Connection:
    con = sqlite3.connect(db_path())
    con.row_factory = sqlite3.Row
    return con


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _norm(txt: Any) -> str:
    if txt is None:
        return ""
    s = str(txt).strip().upper()
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode("ASCII")
    return re.sub(r"\s+", " ", s)


def _to_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    if v is None:
        return default
    try:
        if isinstance(v, str):
            v = v.strip().replace(".", "").replace(",", ".") if re.search(r"\d,\d", v) else v.strip()
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except Exception:
        return default


def _fmt(v: Any, dec: int = 1, suffix: str = "") -> str:
    f = _to_float(v)
    if f is None:
        return "Não disponível"
    if abs(f) >= 1_000_000:
        s = f"{f/1_000_000:.{dec}f} mi"
    elif abs(f) >= 1_000:
        s = f"{f:,.0f}".replace(",", ".")
    elif abs(f - round(f)) < 0.05:
        s = f"{f:.0f}"
    else:
        s = f"{f:.{dec}f}".replace(".", ",")
    return s + suffix


def listar_municipios() -> List[str]:
    if not db_path().exists():
        return []
    with conectar() as con:
        rows = con.execute("SELECT municipio FROM municipios ORDER BY municipio").fetchall()
    return [r[0] for r in rows]


def tabelas_disponiveis() -> List[str]:
    if not db_path().exists():
        return []
    with conectar() as con:
        return [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


def tabela_existe(tabela: str) -> bool:
    return tabela in tabelas_disponiveis()


def _read_one(tabela: str, municipio: str) -> Dict[str, Any]:
    if not tabela_existe(tabela):
        return {}
    with conectar() as con:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({_q(tabela)})").fetchall()]
        mun_col = None
        for c in cols:
            if _norm(c) in {"MUNICIPIO", "MUNICIPIO NOME", "NM MUN", "NO MUNICIPIO"} or "MUNICIP" in _norm(c):
                mun_col = c
                break
        if not mun_col:
            return {}
        df = pd.read_sql_query(f"SELECT * FROM {_q(tabela)} WHERE UPPER({ _q(mun_col) }) = UPPER(?) LIMIT 1", con, params=(municipio,))
    if df.empty:
        # tenta normalizado por remoção de acentos
        try:
            with conectar() as con:
                all_df = pd.read_sql_query(f"SELECT * FROM {_q(tabela)}", con)
            if mun_col in all_df.columns:
                target = _norm(municipio)
                all_df["__norm"] = all_df[mun_col].map(_norm)
                hit = all_df[all_df["__norm"] == target]
                if not hit.empty:
                    return hit.drop(columns=["__norm"]).iloc[0].to_dict()
        except Exception:
            pass
        return {}
    return df.iloc[0].to_dict()


def _read_many(tabela: str, municipio: str) -> pd.DataFrame:
    if not tabela_existe(tabela):
        return pd.DataFrame()
    with conectar() as con:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({_q(tabela)})").fetchall()]
        mun_col = None
        for c in cols:
            if _norm(c) in {"MUNICIPIO", "NM MUN", "NO MUNICIPIO"} or "MUNICIP" in _norm(c):
                mun_col = c
                break
        if not mun_col:
            return pd.DataFrame()
        try:
            return pd.read_sql_query(f"SELECT * FROM {_q(tabela)} WHERE UPPER({_q(mun_col)}) = UPPER(?)", con, params=(municipio,))
        except Exception:
            return pd.DataFrame()


def _classificar(valor: Optional[float], cortes: Tuple[float, float, float], inverso: bool = False) -> str:
    if valor is None:
        return "Sem dado"
    a, b, c = cortes
    if not inverso:
        if valor <= a: return "Bom"
        if valor <= b: return "Regular"
        if valor <= c: return "Ruim"
        return "Crítico"
    else:
        if valor >= c: return "Bom"
        if valor >= b: return "Regular"
        if valor >= a: return "Ruim"
        return "Crítico"


def _score_por_classe(classe: str) -> int:
    return {"Bom": 20, "Regular": 45, "Ruim": 70, "Crítico": 90, "Sem dado": 0}.get(classe, 0)


def _indicador(nome: str, valor: Any, unidade: str, leitura: str, parametro: str, fonte: str, publico: str = "") -> Dict[str, Any]:
    return {
        "Indicador": nome,
        "Valor": _fmt(valor, suffix=unidade) if isinstance(valor, (int, float)) or _to_float(valor) is not None else (valor if valor not in [None, ""] else "Não disponível"),
        "valor_num": _to_float(valor),
        "Leitura": leitura,
        "Parâmetro/Referência": parametro,
        "Fonte": fonte,
        "Público/território relacionado": publico or "População municipal",
    }


def _top_sinan(municipio: str) -> List[Dict[str, Any]]:
    df = _read_many("base_publica_sinan_municipal", municipio)
    if df.empty:
        return []
    for col in ["notificacoes", "casos_confirmados_provaveis", "hospitalizacoes", "obitos"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    sort_col = "notificacoes" if "notificacoes" in df.columns else df.select_dtypes("number").columns[0]
    top = df.sort_values(sort_col, ascending=False).head(8)
    out = []
    for _, r in top.iterrows():
        out.append({
            "Agravo": r.get("agravo", "Agravo"),
            "Ano": r.get("ano_referencia", ""),
            "Notificações": int(_to_float(r.get("notificacoes"), 0) or 0),
            "Hospitalizações": int(_to_float(r.get("hospitalizacoes"), 0) or 0),
            "Óbitos": int(_to_float(r.get("obitos"), 0) or 0),
            "Leitura": "Priorizar integração APS + Vigilância" if (_to_float(r.get("notificacoes"), 0) or 0) > 0 else "Sem registro no recorte importado",
        })
    return out


def _contar_por_municipio(tabela: str, municipio: str) -> int:
    df = _read_many(tabela, municipio)
    return 0 if df.empty else len(df)


def _capacidade_por_equipes(mun: Dict[str, Any], equipes_df: pd.DataFrame) -> Dict[str, Any]:
    pop = _to_float(mun.get("populacao"))
    total_equipes = _to_float(mun.get("total_equipes_aps"))
    if total_equipes is None or total_equipes == 0:
        total_equipes = len(equipes_df) if not equipes_df.empty else None
    if pop is None or not total_equipes:
        razao = None
    else:
        razao = pop / total_equipes
    classe = _classificar(razao, (2500, 3500, 4500)) if razao is not None else "Sem dado"
    return {"pop": pop, "equipes": total_equipes, "pop_por_equipe": razao, "classe": classe}


def _eixo(titulo: str, subtitulo: str, classe: str, indicadores: List[Dict[str, Any]], por_que: List[str], consequencias: List[str], politicas: List[str], publicos: List[str]) -> Dict[str, Any]:
    return {
        "titulo": titulo,
        "subtitulo": subtitulo,
        "classe": classe,
        "score": _score_por_classe(classe),
        "indicadores": indicadores,
        "por_que": por_que,
        "consequencias": consequencias,
        "politicas": politicas,
        "publicos": publicos,
    }


def coletar_diagnostico_municipal(municipio: str) -> Dict[str, Any]:
    mun = _read_one("base_municipal_consolidada", municipio) or _read_one("municipios", municipio)
    mds = _read_one("mds_cadunico_bolsa_familia_municipal", municipio)
    inep = _read_one("base_publica_inep_censo_escolar_municipal", municipio)
    sinasc = _read_one("base_publica_sinasc_municipal", municipio)
    sim = _read_one("base_publica_sim_mortalidade_municipal", municipio)
    equipes = _read_many("equipes_aps", municipio)
    profs = _read_many("profissionais_cnes", municipio)
    ubs = _read_many("estabelecimentos_saude", municipio)
    sinan_top = _top_sinan(municipio)
    assent = _contar_por_municipio("dados_mt_assentamentos", municipio)
    terras = _contar_por_municipio("dados_mt_terras_indigenas", municipio)

    pop = _to_float(mds.get("populacao")) or _to_float(mun.get("populacao"))
    aps = _capacidade_por_equipes({**mun, "populacao": pop}, equipes)
    total_ubs = _to_float(mun.get("total_ubs")) or (len(ubs) if not ubs.empty else None)
    prof_total = len(profs) if not profs.empty else _to_float(mun.get("total_profissionais_aps"))

    pct_cad = _to_float(mds.get("pct_populacao_cadunico"))
    pct_pbf = _to_float(mds.get("pct_populacao_bolsa_familia"))
    score_mds = _to_float(mds.get("score_vulnerabilidade_mds"))
    pct_pobreza_ext = _to_float(mds.get("pct_familias_pobreza_extrema_sobre_cadunico"))
    vuln_ref = max([x for x in [pct_cad, pct_pbf*1.4 if pct_pbf is not None else None, score_mds] if x is not None] or [None])
    classe_vuln = _classificar(vuln_ref, (30, 50, 70)) if vuln_ref is not None else "Sem dado"

    escolas_rurais = _to_float(inep.get("perc_escolas_rurais"))
    escolas_internet = _to_float(inep.get("perc_escolas_com_internet"))
    escolas_esgoto = _to_float(inep.get("perc_escolas_com_esgoto"))
    escolas_bib = _to_float(inep.get("perc_escolas_com_biblioteca_sala_leitura"))
    educ_risco = max([x for x in [escolas_rurais, 100-escolas_internet if escolas_internet is not None else None, 100-escolas_esgoto if escolas_esgoto is not None else None] if x is not None] or [None])
    classe_educ = _classificar(educ_risco, (25, 50, 75)) if educ_risco is not None else "Sem dado"

    prenatal_insuf = _to_float(sinasc.get("perc_prenatal_insuficiente"))
    mae_adol = _to_float(sinasc.get("perc_maes_adolescentes"))
    baixo_peso = _to_float(sinasc.get("perc_baixo_peso"))
    prematuro = _to_float(sinasc.get("perc_prematuros"))
    materno_risco = max([x for x in [prenatal_insuf, mae_adol, baixo_peso, prematuro] if x is not None] or [None])
    classe_materno = _classificar(materno_risco, (8, 15, 25)) if materno_risco is not None else "Sem dado"

    cardio = _to_float(sim.get("perc_obitos_cardiovasculares"))
    externas = _to_float(sim.get("perc_obitos_causas_externas"))
    resp = _to_float(sim.get("perc_obitos_respiratorias"))
    infantil = _to_float(sim.get("perc_obitos_infantis"))
    mortal_risco = max([x for x in [cardio, externas, resp, infantil] if x is not None] or [None])
    classe_mortal = _classificar(mortal_risco, (10, 20, 35)) if mortal_risco is not None else "Sem dado"

    n_sinan = sum([r.get("Notificações", 0) for r in sinan_top])
    hosp_sinan = sum([r.get("Hospitalizações", 0) for r in sinan_top])
    ob_sinan = sum([r.get("Óbitos", 0) for r in sinan_top])
    vigil_score = min(100, n_sinan / max(pop or 1, 1) * 10000) if n_sinan else 0
    classe_vigil = _classificar(vigil_score, (10, 30, 60)) if n_sinan else "Bom"

    ruralidade = max([x for x in [escolas_rurais, 80 if assent else None, 85 if terras else None] if x is not None] or [None])
    classe_territ = _classificar(ruralidade, (25, 50, 75)) if ruralidade is not None else "Sem dado"

    eixos: List[Dict[str, Any]] = []
    eixos.append(_eixo(
        "Território, população e ruralidade",
        "Mostra se o município é concentrado, disperso, ruralizado ou com territórios que dificultam o acesso físico à APS.",
        classe_territ,
        [
            _indicador("População estimada/base usada", pop, "", "Dimensiona demanda potencial por serviços de saúde.", "Usar para calcular razão por equipe, UBS e serviços de retaguarda.", "Base municipal/MDS", "População total"),
            _indicador("Área territorial km²", mun.get("area_km2"), " km²", "Área grande tende a ampliar barreiras logísticas, mesmo com população pequena.", "Quanto maior a área e menor a densidade, maior a importância de estratégias itinerantes.", "Base municipal/IBGE", "Zona rural e localidades dispersas"),
            _indicador("Escolas rurais", escolas_rurais, "%", "Proxy de ruralidade e dispersão de famílias com crianças/adolescentes.", "Acima de 50% sugere forte necessidade de estratégia extramuros e intersetorial.", "INEP/Censo Escolar", "Famílias rurais, estudantes e comunidades escolares"),
            _indicador("Assentamentos mapeados", assent, "", "Assentamentos podem representar população rural dispersa e demanda por rota programada.", "Qualquer presença exige validação de acesso, estrada e vínculo com UBS.", "Dados Abertos MT/INTERMAT", "Assentados e comunidades rurais"),
            _indicador("Terras indígenas mapeadas", terras, "", "Indica necessidade de leitura diferenciada de equidade territorial e cultural.", "Quando presente, acionar validação com área técnica de saúde indígena e município.", "Dados Abertos MT", "Povos indígenas e territórios tradicionais"),
        ],
        [
            "A ruralidade amplia a distância real entre usuário e serviço, mesmo quando a distância em linha reta parece moderada.",
            "Escolas rurais, assentamentos e terras indígenas funcionam como sinais de população dispersa que pode não aparecer nos indicadores urbanos tradicionais.",
        ],
        [
            "Maior risco de baixa frequência em consultas, atraso em pré-natal, baixa adesão a ações preventivas e dificuldade de busca ativa.",
            "Possível necessidade de transporte sanitário, equipe itinerante, UBS satélite ou agenda extramuros.",
        ],
        [
            "Criar rota rural programada de APS com calendário mensal.",
            "Cruzar UBS de referência, escolas rurais, assentamentos e agentes comunitários para redesenhar microterritórios.",
            "Validar com ERS e município quais comunidades ficam isoladas em período chuvoso.",
        ],
        ["População rural", "assentamentos", "comunidades escolares rurais", "povos indígenas/tradicionais quando presentes"],
    ))

    eixos.append(_eixo(
        "APS, CNES e capacidade instalada",
        "Avalia se a capacidade de resposta da APS parece compatível com população e território.",
        aps["classe"],
        [
            _indicador("UBS/estabelecimentos APS", total_ubs, "", "Oferta física disponível para atendimento territorial.", "Município com muita população e poucas UBS exige análise de expansão ou reorganização.", "CNES/estabelecimentos", "Usuários vinculados à APS"),
            _indicador("Equipes APS", aps["equipes"], "", "Capacidade assistencial mínima para cobertura territorial.", "A leitura deve considerar eSF, eAP, eSB, eMulti/NASF e especificidades territoriais.", "CNES/equipes", "Equipes e usuários adscritos"),
            _indicador("População por equipe", aps["pop_por_equipe"], "", "Quanto maior, maior o risco de sobrecarga e desassistência.", "Referência preliminar: até 2.500 bom; 2.500–3.500 regular; 3.500–4.500 ruim; acima disso crítico.", "Cálculo sistema", "População cadastrável/acompanhável"),
            _indicador("Profissionais vinculados CNES", prof_total, "", "Indica força de trabalho registrada, mas precisa validar carga horária e vínculo real.", "Número alto não garante cobertura se houver baixa carga horária ou concentração em poucos serviços.", "CNES/profissionais", "Trabalhadores da APS"),
        ],
        [
            "A razão população/equipe indica se a APS pode acompanhar o território sem sobrecarga.",
            "A análise precisa cruzar equipe, UBS, população vulnerável, ruralidade e distância, porque cobertura nominal pode esconder vazios reais.",
        ],
        [
            "Equipe insuficiente tende a reduzir busca ativa, acompanhamento de crônicos, visitas domiciliares e resposta a agravos.",
            "Pode haver concentração de serviço na sede municipal enquanto comunidades rurais permanecem distantes.",
        ],
        [
            "Auditar CNES/INE, carga horária e equipe por UBS.",
            "Avaliar ampliação de equipes, reorganização de microáreas ou implantação de ponto de apoio rural.",
            "Priorizar municípios com alta vulnerabilidade + alta população por equipe.",
        ],
        ["Usuários da APS", "equipes de saúde", "ACS/ACE", "população vulnerável dependente do SUS"],
    ))

    eixos.append(_eixo(
        "Renda, vulnerabilidade e proteção social",
        "Mostra a dependência de políticas sociais e o peso dos determinantes sociais sobre a saúde.",
        classe_vuln,
        [
            _indicador("População no CadÚnico", pct_cad, "%", "Quanto maior, maior a presença de famílias em condição socioeconômica vulnerável.", "Acima de 50% sugere prioridade intersetorial APS + assistência social.", "MDS/CadÚnico", "Famílias de baixa renda"),
            _indicador("População beneficiária do Bolsa Família", pct_pbf, "%", "Mostra população com transferência de renda e vulnerabilidade social acompanhável.", "Acima de 30% indica público prioritário para busca ativa e condicionalidades.", "MDS/Bolsa Família", "Famílias PBF, crianças, gestantes e adolescentes"),
            _indicador("Famílias CadÚnico", mds.get("cadunico_familias"), "", "Quantidade de famílias que podem orientar ações territoriais com CRAS e APS.", "Usar para estimar demanda de busca ativa e educação em saúde.", "MDS", "Famílias vulneráveis"),
            _indicador("Famílias em pobreza/extrema pobreza", mds.get("cadunico_familias_pobreza_extrema"), "", "Núcleo de maior vulnerabilidade dentro do CadÚnico.", "Quanto maior o volume, maior a necessidade de ações intersetoriais e cuidado longitudinal.", "MDS", "Famílias pobres/extremamente pobres"),
            _indicador("BPC total", mds.get("bpc_total"), "", "Indica presença de idosos e pessoas com deficiência em benefício assistencial.", "Requer atenção domiciliar, reabilitação, transporte e cuidado continuado.", "MDS/BPC", "Idosos e pessoas com deficiência"),
            _indicador("Score MDS de vulnerabilidade", score_mds, "", "Índice interno da base MDS para ordenar vulnerabilidade relativa.", "Usar como fila de priorização, não como decisão isolada.", "MDS consolidado", "População vulnerável"),
        ],
        [
            "Alta presença no CadÚnico/Bolsa Família indica que parte relevante da população depende de políticas públicas e pode ter barreiras para cuidado preventivo.",
            "BPC aumenta a necessidade de olhar para idosos, pessoas com deficiência, cuidado domiciliar e transporte sanitário.",
        ],
        [
            "Maior risco de adoecimento evitável, atraso no cuidado, baixa adesão a tratamento e maior pressão sobre a UBS.",
            "Pode exigir agenda integrada com CRAS, escolas, vigilância e equipes de saúde da família.",
        ],
        [
            "Implantar agenda APS + CRAS para famílias PBF e BPC.",
            "Criar busca ativa de gestantes, crianças e idosos em famílias vulneráveis.",
            "Usar CadÚnico como camada de priorização para campanhas, vacinação e cuidado de crônicos.",
        ],
        ["Famílias CadÚnico", "Bolsa Família", "idosos BPC", "pessoas com deficiência", "crianças e gestantes vulneráveis"],
    ))

    eixos.append(_eixo(
        "Educação, escolaridade e intersetorialidade",
        "Mostra como escola, infraestrutura educacional e ruralidade ajudam a orientar promoção da saúde e busca ativa.",
        classe_educ,
        [
            _indicador("Escolas totais", inep.get("escolas_total"), "", "Rede escolar funciona como ponto de contato com famílias e crianças/adolescentes.", "Usar para PSE, vacinação, saúde bucal, alimentação e educação em saúde.", "INEP", "Estudantes e famílias"),
            _indicador("Matrículas", inep.get("matriculas_total"), "", "Volume de estudantes potencialmente alcançáveis por ações intersetoriais.", "Quanto maior, maior o potencial de impacto do PSE e educação em saúde.", "INEP", "Crianças e adolescentes"),
            _indicador("Escolas rurais", escolas_rurais, "%", "Mostra dispersão territorial da população escolar.", "Acima de 50% exige desenho de ações extramuros e logística rural.", "INEP", "Estudantes rurais"),
            _indicador("Escolas com internet", escolas_internet, "%", "Indica potencial de comunicação, teleorientação e apoio digital.", "Baixa internet dificulta estratégias digitais e educação remota em saúde.", "INEP", "Escolas e comunidades"),
            _indicador("Escolas com esgoto", escolas_esgoto, "%", "Infraestrutura escolar se relaciona a saneamento, higiene e risco de agravos.", "Baixo percentual exige integração com saneamento, educação e vigilância.", "INEP", "Estudantes e trabalhadores da educação"),
            _indicador("Escolas com biblioteca/sala de leitura", escolas_bib, "%", "Proxy de infraestrutura educacional e potencial de ações educativas continuadas.", "Quanto menor, maior a necessidade de material educativo simples e ação presencial.", "INEP", "Comunidade escolar"),
        ],
        [
            "Escola é porta estratégica para encontrar crianças, adolescentes e famílias antes que o problema vire demanda assistencial.",
            "Ruralidade escolar e baixa infraestrutura dificultam comunicação, campanhas e acompanhamento preventivo.",
        ],
        [
            "Maior risco de baixa adesão a orientações de saúde, vacinação incompleta, baixa prevenção em saúde bucal e dificuldades na vigilância escolar.",
        ],
        [
            "Fortalecer Programa Saúde na Escola com foco em ruralidade e vulnerabilidade.",
            "Planejar campanhas de vacinação, saúde bucal, alimentação saudável e prevenção de violências usando escolas como pontos de apoio.",
            "Priorizar escolas rurais com baixa infraestrutura para ação conjunta APS + Educação + Vigilância.",
        ],
        ["Crianças", "adolescentes", "famílias rurais", "profissionais da educação"],
    ))

    eixos.append(_eixo(
        "Materno-infantil, nascimentos e primeira infância",
        "Avalia sinais do ciclo gravídico-puerperal e da saúde infantil inicial.",
        classe_materno,
        [
            _indicador("Nascidos vivos", sinasc.get("nascidos_vivos"), "", "Dimensiona demanda anual da linha materno-infantil.", "Usar para planejar pré-natal, puericultura, vacinação e busca ativa.", "SINASC", "Gestantes, puérperas e crianças"),
            _indicador("Pré-natal insuficiente", prenatal_insuf, "%", "Sinaliza risco de acesso tardio ou baixa continuidade do cuidado.", "Acima de 15% exige revisão da busca ativa de gestantes.", "SINASC", "Gestantes"),
            _indicador("Mães adolescentes", mae_adol, "%", "Indica vulnerabilidade social e necessidade de cuidado intersetorial.", "Acima de 15% requer estratégia com escola, CRAS e APS.", "SINASC", "Adolescentes gestantes"),
            _indicador("Baixo peso ao nascer", baixo_peso, "%", "Pode indicar risco gestacional, vulnerabilidade ou falhas de acompanhamento.", "Acima de 10% merece análise com pré-natal e condições sociais.", "SINASC", "Recém-nascidos"),
            _indicador("Prematuridade", prematuro, "%", "Sinaliza risco de maior demanda assistencial no início da vida.", "Acima de 10% demanda investigação de fatores maternos e acesso.", "SINASC", "Bebês prematuros"),
            _indicador("Cesáreas", sinasc.get("perc_cesareas"), "%", "Ajuda a qualificar rede de parto e práticas obstétricas.", "Interpretar conforme perfil da rede e referência regional.", "SINASC", "Gestantes e rede de parto"),
        ],
        [
            "Pré-natal insuficiente, gravidez na adolescência e baixo peso se conectam a vulnerabilidade, acesso territorial e qualidade da APS.",
            "Mesmo poucos casos em municípios pequenos podem indicar alerta relevante quando o percentual é alto.",
        ],
        [
            "Risco de aumento de demanda neonatal, atraso vacinal, maior vulnerabilidade da primeira infância e complicações evitáveis.",
        ],
        [
            "Implantar lista nominal de gestantes com busca ativa mensal.",
            "Cruzar gestantes PBF/CadÚnico com início de pré-natal e território rural.",
            "Criar fluxo APS + maternidade/referência para gestantes de risco.",
        ],
        ["Gestantes", "adolescentes", "recém-nascidos", "primeira infância"],
    ))

    eixos.append(_eixo(
        "Mortalidade e condições crônicas/externas",
        "Mostra quais grupos de causas podem pressionar a rede e orientar prevenção.",
        classe_mortal,
        [
            _indicador("Óbitos totais", sim.get("obitos_total"), "", "Dimensiona carga geral de mortalidade registrada.", "Usar com cautela em municípios pequenos; observar perfil de causas.", "SIM", "População geral"),
            _indicador("Óbitos cardiovasculares", cardio, "%", "Sinaliza peso de hipertensão, diabetes, cuidado crônico e risco cardiovascular.", "Percentual alto orienta linha de cuidado de crônicos.", "SIM", "Adultos e idosos"),
            _indicador("Óbitos por causas externas", externas, "%", "Indica acidentes, violências e eventos evitáveis com ação intersetorial.", "Percentual alto exige articulação com vigilância, trânsito, segurança e assistência social.", "SIM", "Jovens, adultos, população exposta a violência/acidentes"),
            _indicador("Óbitos respiratórios", resp, "%", "Pode orientar vacinação, manejo de crônicos respiratórios e sazonalidade.", "Interpretar com idade, vacinação e acesso a cuidado oportuno.", "SIM", "Idosos, crianças e crônicos"),
            _indicador("Óbitos infantis", infantil, "%", "Evento sensível à linha materno-infantil e vigilância do óbito.", "Qualquer ocorrência exige qualificação e investigação, especialmente em município pequeno.", "SIM", "Crianças menores de 1 ano"),
        ],
        [
            "O perfil de mortalidade ajuda a escolher quais linhas de cuidado precisam de reforço: crônicos, violência/acidentes, respiratórios ou materno-infantil.",
            "Percentuais altos em municípios pequenos precisam ser interpretados junto ao número absoluto.",
        ],
        [
            "Pode indicar necessidade de manejo mais ativo de hipertensão/diabetes, prevenção de violências/acidentes e vigilância de óbitos evitáveis.",
        ],
        [
            "Criar painel de crônicos priorizando hipertensos/diabéticos com maior risco.",
            "Integrar APS + Vigilância para investigação de óbitos infantis, maternos e causas externas.",
            "Usar ACS para busca ativa de crônicos sem consulta recente.",
        ],
        ["Idosos", "adultos com crônicos", "crianças", "jovens expostos a violência/acidentes"],
    ))

    eixos.append(_eixo(
        "Vigilância, agravos e resposta territorial",
        "Mostra quais agravos notificados exigem ação integrada entre APS e Vigilância.",
        classe_vigil,
        [
            _indicador("Notificações SINAN no recorte", n_sinan, "", "Carga de agravos notificados disponível para o município.", "Usar para priorizar integração APS + Vigilância por agravo.", "SINAN", "População exposta a agravos"),
            _indicador("Hospitalizações registradas nos agravos", hosp_sinan, "", "Indica gravidade/pressão assistencial associada aos agravos importados.", "Hospitalização em agravo notificável exige revisão de prevenção e manejo oportuno.", "SINAN", "Casos graves"),
            _indicador("Óbitos nos agravos", ob_sinan, "", "Sinaliza evento extremo e necessidade de investigação/mitigação.", "Qualquer óbito em agravo sensível deve acionar vigilância e linha de cuidado.", "SINAN", "Casos fatais e seus territórios"),
        ],
        [
            "A vigilância territorial mostra problemas que a UBS pode antecipar com busca ativa, educação em saúde, vacinação e controle ambiental.",
            "A lista de agravos deve ser lida com o contexto local: área rural, saneamento, ambiente, trabalho e acesso.",
        ],
        [
            "Sem integração APS + Vigilância, os agravos podem virar atendimento tardio, hospitalização ou surtos locais.",
        ],
        [
            "Definir top 3 agravos do município para plano trimestral APS + Vigilância.",
            "Usar ACS/ACE para busca territorial e educação em saúde por microárea.",
            "Cruzar agravos com saneamento, ruralidade, escolas e áreas de difícil acesso.",
        ],
        ["Famílias em áreas de risco", "trabalhadores rurais", "crianças", "idosos", "populações em territórios com saneamento precário"],
    ))

    # score geral: média dos eixos com dados
    scores = [e["score"] for e in eixos if e["classe"] != "Sem dado"]
    score_geral = round(sum(scores) / len(scores), 1) if scores else None
    classe_geral = _classificar(score_geral, (30, 50, 70)) if score_geral is not None else "Sem dado"

    # Insights narrativos baseados nos eixos críticos
    insights = []
    for e in eixos:
        if e["classe"] in {"Ruim", "Crítico"}:
            evs = [i for i in e["indicadores"] if i.get("valor_num") is not None][:3]
            evidencia = "; ".join([f"{i['Indicador']}: {i['Valor']}" for i in evs]) or "indicadores qualitativos/territoriais disponíveis"
            insights.append({
                "eixo": e["titulo"],
                "situacao": e["classe"],
                "causa": e["por_que"][0] if e["por_que"] else "Cruzamento de indicadores aponta fragilidade.",
                "evidencia": evidencia,
                "consequencia": e["consequencias"][0] if e["consequencias"] else "Pode ampliar desassistência ou demanda tardia.",
                "acao": e["politicas"][0] if e["politicas"] else "Validar com área técnica e município.",
            })
    if not insights:
        insights.append({
            "eixo": "Leitura geral",
            "situacao": classe_geral,
            "causa": "Não há eixo crítico forte pela régua atual, mas a leitura deve ser validada com dados locais.",
            "evidencia": "Indicadores disponíveis não acionaram alerta crítico automático.",
            "consequencia": "Manter monitoramento e qualificar dados faltantes.",
            "acao": "Validar CNES, território, séries históricas e situação local com ERS/município.",
        })

    # séries históricas reais disponíveis
    tendencias = detectar_tendencias(municipio)

    return {
        "municipio": municipio,
        "perfil": {
            "municipio": municipio,
            "regiao_saude": mun.get("regiao_saude") or mds.get("regiao_saude"),
            "populacao": pop,
            "codigo_ibge": mun.get("codigo_ibge") or mds.get("codigo_ibge"),
            "latitude": mun.get("latitude"),
            "longitude": mun.get("longitude"),
            "score_geral": score_geral,
            "classe_geral": classe_geral,
            "decisao": decisao_geral(classe_geral, eixos),
        },
        "eixos": eixos,
        "insights": insights,
        "sinan_top": sinan_top,
        "tendencias": tendencias,
        "fontes": inventario_fontes_municipio(municipio),
    }


def decisao_geral(classe: str, eixos: List[Dict[str, Any]]) -> str:
    crit = [e["titulo"] for e in eixos if e["classe"] == "Crítico"]
    ruim = [e["titulo"] for e in eixos if e["classe"] == "Ruim"]
    if crit:
        return "Resposta prioritária: plano municipal integrado com validação técnica imediata dos eixos críticos."
    if ruim:
        return "Intervenção programada: reorganizar ações e pactuar plano de 90 dias nos eixos ruins."
    if classe == "Regular":
        return "Ação preventiva: monitorar, qualificar dados e evitar piora dos determinantes."
    return "Manter acompanhamento e qualificar bases faltantes."


def detectar_tendencias(municipio: str) -> List[Dict[str, Any]]:
    out = []
    if tabela_existe("indicadores_municipais"):
        try:
            with conectar() as con:
                df = pd.read_sql_query("SELECT ano, indicador, valor, fonte FROM indicadores_municipais WHERE UPPER(municipio)=UPPER(?)", con, params=(municipio,))
            if not df.empty:
                df["ano"] = pd.to_numeric(df["ano"], errors="coerce")
                df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
                df = df.dropna(subset=["ano", "valor"])
                for ind, g in df.groupby("indicador"):
                    anos = sorted(g["ano"].dropna().unique())
                    if len(anos) >= 2:
                        g2 = g.groupby("ano", as_index=False)["valor"].mean().sort_values("ano")
                        v0, v1 = g2["valor"].iloc[0], g2["valor"].iloc[-1]
                        delta = v1 - v0
                        out.append({
                            "Indicador": ind,
                            "Período": f"{int(g2['ano'].iloc[0])}–{int(g2['ano'].iloc[-1])}",
                            "Inicial": round(v0, 2),
                            "Atual": round(v1, 2),
                            "Variação": round(delta, 2),
                            "Tendência": "Piorou/subiu" if delta > 0 else "Melhorou/caiu" if delta < 0 else "Estável",
                            "Fonte": g.get("fonte", pd.Series(["indicadores_municipais"])).iloc[0] if "fonte" in g.columns else "indicadores_municipais",
                        })
        except Exception:
            pass
    # SINAN por agravo/ano
    df_sinan = _read_many("base_publica_sinan_municipal", municipio)
    if not df_sinan.empty and {"agravo", "ano_referencia", "notificacoes"}.issubset(df_sinan.columns):
        try:
            df_sinan["ano_referencia"] = pd.to_numeric(df_sinan["ano_referencia"], errors="coerce")
            df_sinan["notificacoes"] = pd.to_numeric(df_sinan["notificacoes"], errors="coerce").fillna(0)
            for agr, g in df_sinan.groupby("agravo"):
                g2 = g.groupby("ano_referencia", as_index=False)["notificacoes"].sum().dropna().sort_values("ano_referencia")
                if len(g2) >= 2:
                    delta = g2["notificacoes"].iloc[-1] - g2["notificacoes"].iloc[0]
                    out.append({
                        "Indicador": f"SINAN — {agr}",
                        "Período": f"{int(g2['ano_referencia'].iloc[0])}–{int(g2['ano_referencia'].iloc[-1])}",
                        "Inicial": int(g2["notificacoes"].iloc[0]),
                        "Atual": int(g2["notificacoes"].iloc[-1]),
                        "Variação": int(delta),
                        "Tendência": "Aumento de notificações" if delta > 0 else "Redução de notificações" if delta < 0 else "Estável",
                        "Fonte": "SINAN",
                    })
        except Exception:
            pass
    return out[:20]


def inventario_fontes_municipio(municipio: str) -> List[Dict[str, Any]]:
    fontes = []
    candidatas = [
        ("base_municipal_consolidada", "Cadastro municipal consolidado"),
        ("mds_cadunico_bolsa_familia_municipal", "MDS/CadÚnico/Bolsa Família/BPC"),
        ("base_publica_inep_censo_escolar_municipal", "INEP/Censo Escolar"),
        ("base_publica_sinasc_municipal", "SINASC/Nascidos vivos"),
        ("base_publica_sim_mortalidade_municipal", "SIM/Mortalidade"),
        ("base_publica_sinan_municipal", "SINAN/Agravos"),
        ("equipes_aps", "CNES/Equipes APS"),
        ("profissionais_cnes", "CNES/Profissionais"),
        ("estabelecimentos_saude", "CNES/Estabelecimentos/UBS"),
        ("dados_mt_assentamentos", "Assentamentos"),
        ("dados_mt_terras_indigenas", "Terras indígenas"),
    ]
    for t, desc in candidatas:
        if not tabela_existe(t):
            fontes.append({"Base": desc, "Tabela": t, "Status": "Ausente", "Registros do município": 0})
        else:
            n = len(_read_many(t, municipio)) or (1 if _read_one(t, municipio) else 0)
            fontes.append({"Base": desc, "Tabela": t, "Status": "Carregada" if n else "Sem registro para o município", "Registros do município": n})
    return fontes


def montar_relatorio_textual(diag: Dict[str, Any]) -> str:
    p = diag.get("perfil", {})
    linhas = []
    linhas.append(f"DIAGNÓSTICO MUNICIPAL INTELIGENTE — {p.get('municipio', '')}")
    linhas.append("")
    linhas.append(f"Classificação geral: {p.get('classe_geral', 'Sem dado')} | Score técnico: {_fmt(p.get('score_geral'))}.")
    linhas.append(f"Decisão sugerida: {p.get('decisao', '')}")
    linhas.append("")
    linhas.append("Principais achados interpretativos:")
    for ins in diag.get("insights", [])[:8]:
        linhas.append(f"- [{ins.get('situacao')}] {ins.get('eixo')}: {ins.get('causa')} Evidência: {ins.get('evidencia')}. Consequência provável: {ins.get('consequencia')} Ação: {ins.get('acao')}")
    linhas.append("")
    linhas.append("Leitura por eixo:")
    for e in diag.get("eixos", []):
        linhas.append(f"- {e['titulo']}: {e['classe']}. {e['subtitulo']}")
    linhas.append("")
    linhas.append("Observação metodológica: este relatório cruza bases disponíveis no banco local e serve como apoio técnico para validação com município, ERS e áreas temáticas. Dados ausentes não devem ser interpretados como zero.")
    return "\n".join(linhas)
