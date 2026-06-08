from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

import pandas as pd

from database.queries import read_table


def _norm_txt(v: Any) -> str:
    s = str(v or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or pd.isna(v):
            return default
        if isinstance(v, str):
            v = v.replace("%", "").replace("R$", "").replace(".", "").replace(",", ".").strip()
            if not v:
                return default
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def _snum(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype="float64")
    if col not in df.columns:
        return pd.Series(default, index=df.index, dtype="float64")
    s = df[col]
    if s.dtype == object:
        s = s.astype(str).str.replace("%", "", regex=False).str.replace("R$", "", regex=False)
        s = s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce").fillna(default).astype(float)


def _safe_div(a: float, b: float, mult: float = 1.0) -> float:
    a = _num(a)
    b = _num(b)
    if b == 0:
        return 0.0
    return a / b * mult


def _read(nome: str) -> pd.DataFrame:
    try:
        df = read_table(nome)
        return df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _prep_mun(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if "municipio" not in out.columns:
        cand = [c for c in out.columns if "municip" in str(c).lower()]
        if cand:
            out = out.rename(columns={cand[0]: "municipio"})
    if "municipio" not in out.columns:
        return pd.DataFrame()
    out["municipio_norm"] = out["municipio"].map(_norm_txt)
    return out


def _merge(base: pd.DataFrame, tabela: str, prefixo: str = "") -> tuple[pd.DataFrame, bool]:
    df = _prep_mun(_read(tabela))
    if df.empty:
        return base, False
    cols = [c for c in df.columns if c in ["municipio_norm"] or c not in base.columns]
    aux = df[cols].drop_duplicates("municipio_norm")
    if prefixo:
        ren = {c: f"{prefixo}_{c}" for c in aux.columns if c != "municipio_norm" and c in base.columns}
        aux = aux.rename(columns=ren)
    return base.merge(aux, on="municipio_norm", how="left"), True


def _base_integrada() -> tuple[pd.DataFrame, list[str]]:
    base = _prep_mun(_read("base_municipal_consolidada"))
    fontes = ["base_municipal_consolidada"] if not base.empty else []
    if base.empty:
        base = _prep_mun(_read("municipios"))
        if not base.empty:
            fontes.append("municipios")
    if base.empty:
        return pd.DataFrame(), fontes

    for tabela, prefixo in [
        ("socio_consolidado_municipal", "socio"),
        ("socio_indicadores_municipais", "socio_ind"),
        ("mds_cadunico_bolsa_familia_municipal", "mds"),
        ("base_publica_inep_censo_escolar_municipal", "inep"),
        ("base_publica_sinasc_municipal", "sinasc"),
        ("base_publica_sim_mortalidade_municipal", "sim"),
    ]:
        base, ok = _merge(base, tabela, prefixo)
        if ok:
            fontes.append(tabela)

    sinan = _prep_mun(_read("base_publica_sinan_municipal"))
    if not sinan.empty and "notificacoes" in sinan.columns:
        agg = sinan.groupby("municipio_norm", as_index=False).agg(
            sinan_notificacoes=("notificacoes", lambda x: pd.to_numeric(x, errors="coerce").fillna(0).sum()),
            sinan_agravos=("agravo", "nunique") if "agravo" in sinan.columns else ("notificacoes", "size"),
            sinan_obitos=("obitos", lambda x: pd.to_numeric(x, errors="coerce").fillna(0).sum()) if "obitos" in sinan.columns else ("notificacoes", lambda x: 0),
            sinan_hospitalizacoes=("hospitalizacoes", lambda x: pd.to_numeric(x, errors="coerce").fillna(0).sum()) if "hospitalizacoes" in sinan.columns else ("notificacoes", lambda x: 0),
        )
        base = base.merge(agg, on="municipio_norm", how="left")
        fontes.append("base_publica_sinan_municipal")

    # Tentativa de incorporar geointeligência e risco sem quebrar a tela caso os módulos não estejam disponíveis.
    try:
        from services.geointeligencia_aps_service import carregar_geointeligencia_aps
        geo = _prep_mun(carregar_geointeligencia_aps())
        if not geo.empty:
            keep = [c for c in ["municipio_norm", "score_geointeligencia_aps", "classe_geointeligencia", "ranking_geointeligencia", "score_social_geo", "score_acesso_territorial", "score_fragilidade_capacidade", "score_pressao_assistencial", "score_risco_sanitario", "motivo_geointeligencia", "recomendacao_geointeligencia"] if c in geo.columns]
            base = base.merge(geo[keep].drop_duplicates("municipio_norm"), on="municipio_norm", how="left")
            fontes.append("geointeligencia_aps_service")
    except Exception:
        pass

    try:
        from services.gestao_risco_service import carregar_gestao_risco_aps
        res = carregar_gestao_risco_aps()
        risco = _prep_mun(res.get("base", pd.DataFrame())) if isinstance(res, dict) else pd.DataFrame()
        if not risco.empty:
            keep = [c for c in ["municipio_norm", "score_risco_integrado_aps", "classificacao_risco_integrado", "ranking_risco_integrado_aps", "risco_social", "risco_capacidade_aps", "risco_acesso_territorial", "risco_materno_infantil", "risco_mortalidade", "risco_vigilancia", "risco_intersetorial", "risco_equidade_territorial", "principal_fator_risco", "prioridade_mitigacao", "plano_mitigacao_resumido", "alertas_risco"] if c in risco.columns]
            base = base.merge(risco[keep].drop_duplicates("municipio_norm"), on="municipio_norm", how="left")
            fontes.append("gestao_risco_service")
    except Exception:
        pass

    base = _calcular_derivados(base)
    return base, fontes


def _calcular_derivados(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    pop = _snum(out, "populacao")
    equipes = _snum(out, "total_equipes_aps")
    ubs = _snum(out, "total_ubs")
    prof = _snum(out, "total_profissionais_aps")
    out["ia_pop_por_equipe"] = (pop / equipes.replace(0, pd.NA)).fillna(0).round(1)
    out["ia_pop_por_ubs"] = (pop / ubs.replace(0, pd.NA)).fillna(0).round(1)
    out["ia_prof_por_equipe"] = (prof / equipes.replace(0, pd.NA)).fillna(0).round(1)
    out["ia_equipes_10mil"] = (equipes / pop.replace(0, pd.NA) * 10000).fillna(0).round(2)
    out["ia_ubs_10mil"] = (ubs / pop.replace(0, pd.NA) * 10000).fillna(0).round(2)
    out["ia_densidade"] = _snum(out, "densidade_hab_km2")

    cad = _snum(out, "cadunico_pessoas")
    if cad.sum() == 0:
        cad = _snum(out, "mds_cadunico_pessoas")
    pbf = _snum(out, "bolsa_familia_pessoas")
    if pbf.sum() == 0:
        pbf = _snum(out, "mds_bolsa_familia_pessoas")
    if pbf.sum() == 0:
        pbf = _snum(out, "bolsa_familia_familias") * 3
    out["ia_pct_cadunico"] = (cad / pop.replace(0, pd.NA) * 100).fillna(0).round(2)
    out["ia_pct_bolsa_familia"] = (pbf / pop.replace(0, pd.NA) * 100).fillna(0).round(2)

    nasc = _snum(out, "nascidos_vivos")
    if nasc.sum() == 0:
        nasc = _snum(out, "sinasc_nascidos_vivos")
    obitos = _snum(out, "obitos")
    if obitos.sum() == 0:
        obitos = _snum(out, "sim_obitos_total")
    obitos_inf = _snum(out, "obitos_infantis")
    if obitos_inf.sum() == 0:
        obitos_inf = _snum(out, "sim_obitos_infantis")
    out["ia_taxa_mortalidade_1000"] = (obitos / pop.replace(0, pd.NA) * 1000).fillna(0).round(2)
    out["ia_mortalidade_infantil_1000_nv"] = (obitos_inf / nasc.replace(0, pd.NA) * 1000).fillna(0).round(2)
    out["ia_sinan_notificacoes_1000"] = (_snum(out, "sinan_notificacoes") / pop.replace(0, pd.NA) * 1000).fillna(0).round(2)

    esc = _snum(out, "escolas_total")
    if esc.sum() == 0:
        esc = _snum(out, "inep_escolas_total")
    rur = _snum(out, "escolas_rurais")
    if rur.sum() == 0:
        rur = _snum(out, "inep_escolas_rurais")
    out["ia_pct_escolas_rurais"] = (rur / esc.replace(0, pd.NA) * 100).fillna(0).round(2)
    if "inep_perc_escolas_com_internet" in out.columns:
        out["ia_pct_escolas_sem_internet"] = (100 - _snum(out, "inep_perc_escolas_com_internet")).clip(0, 100).round(2)
    else:
        out["ia_pct_escolas_sem_internet"] = 0.0
    if "inep_perc_escolas_com_esgoto" in out.columns:
        out["ia_pct_escolas_sem_esgoto"] = (100 - _snum(out, "inep_perc_escolas_com_esgoto")).clip(0, 100).round(2)
    else:
        out["ia_pct_escolas_sem_esgoto"] = 0.0

    # V18: usa a camada socioeducacional consolidada quando disponível.
    for origem in ["socio", "socio_ind"]:
        for campo in ["perc_esgotamento_inadequado_proxy", "domicilios_esgotamento_inadequado_proxy", "perc_escolas_rurais", "escolas_rurais", "escolas_indigenas", "escolas_quilombolas", "populacao_indigena", "populacao_quilombola"]:
            pref = f"{origem}_{campo}"
            if pref in out.columns and campo not in out.columns:
                out[campo] = out[pref]
    out["ia_pct_esgotamento_inadequado"] = _snum(out, "perc_esgotamento_inadequado_proxy")
    return out


def _percentil_0_100(df: pd.DataFrame, coluna: str, valor: float, maior_pior: bool = True) -> float:
    if coluna not in df.columns or df.empty:
        return 0.0
    s = pd.to_numeric(df[coluna], errors="coerce").dropna()
    if s.empty:
        return 0.0
    rank = (s <= valor).mean() * 100
    return round(rank if maior_pior else 100 - rank, 1)


def _classif(valor: float, alto: float = 70, medio: float = 45) -> str:
    """Classificação simples, objetiva e compreensível para uso técnico-decisório."""
    valor = _num(valor)
    if valor >= 85:
        return "Péssimo — intervenção urgente"
    if valor >= alto:
        return "Ruim — alta prioridade"
    if valor >= medio:
        return "Regular — atenção técnica"
    return "Bom — sem alerta forte"


def _acao_por_score(valor: float, eixo: str = "") -> str:
    valor = _num(valor)
    eixo = str(eixo or "").lower()
    if valor >= 85:
        if "capacidade" in eixo or "equipe" in eixo:
            return "Necessidade urgente de ampliar equipes, revisar CNES/INE, adscrição e carga horária."
        if "territ" in eixo or "acesso" in eixo or "geo" in eixo:
            return "Necessidade urgente de estudo para nova UBS, UBS satélite, unidade móvel ou rota assistencial."
        if "social" in eixo or "vulner" in eixo:
            return "Necessidade urgente de programa de busca ativa com APS, Assistência Social e Vigilância."
        return "Necessidade urgente de plano de ação com responsável, prazo e evidência de mitigação."
    if valor >= 70:
        if "capacidade" in eixo or "equipe" in eixo:
            return "Alta prioridade para reorganizar equipes, ampliar cobertura e validar suficiência de UBS."
        if "territ" in eixo or "acesso" in eixo or "geo" in eixo:
            return "Alta prioridade para validar vazios assistenciais, distâncias e acesso real às UBS."
        if "social" in eixo or "vulner" in eixo:
            return "Alta prioridade para ações intersetoriais e estratificação de famílias vulneráveis."
        return "Alta prioridade para mitigação dirigida no eixo."
    if valor >= 45:
        return "Situação regular: requer acompanhamento, validação local e ação preventiva direcionada."
    return "Situação boa pela régua atual: manter acompanhamento e atualização das bases."


def _perfil_ruralidade(r: Any) -> str:
    pct_rural = _num(r.get("ia_pct_escolas_rurais") if hasattr(r, 'get') else 0)
    if pct_rural >= 70:
        return "Município extremamente ruralizado — recomenda programa territorial com ações extramuros, calendário itinerante, rota APS-vigilância e articulação com escolas rurais/assentamentos."
    if pct_rural >= 40:
        return "Município com ruralidade elevada — recomenda reforço de busca ativa, ações extramuros e análise de deslocamento até UBS."
    return "Ruralidade sem alerta forte pela régua atual."


def analise_municipal_profunda(municipio: str) -> dict[str, Any]:
    base, fontes = _base_integrada()
    if base.empty:
        return {"ok": False, "mensagem": "Base municipal integrada indisponível."}
    mun_norm = _norm_txt(municipio)
    linha_df = base[base["municipio_norm"] == mun_norm]
    if linha_df.empty:
        return {"ok": False, "mensagem": f"Município não encontrado: {municipio}."}
    r = linha_df.iloc[0].to_dict()

    indicadores = [
        ("População por equipe APS", "ia_pop_por_equipe", "maior", "Capacidade APS", "Indica se a equipe pode estar sobrecarregada. Valores altos sugerem necessidade de ampliação/reorganização de equipes e revisão da adscrição."),
        ("População por UBS/estabelecimento", "ia_pop_por_ubs", "maior", "Capacidade física", "Ajuda a avaliar concentração de população por ponto de atenção."),
        ("Profissionais por equipe", "ia_prof_por_equipe", "menor", "Força de trabalho", "Valor baixo pode indicar equipe menos robusta ou necessidade de auditar vínculos."),
        ("% população CadÚnico", "ia_pct_cadunico", "maior", "Vulnerabilidade social", "Indica dependência de políticas sociais e necessidade de ações intersetoriais, busca ativa e estratificação familiar."),
        ("% população Bolsa Família", "ia_pct_bolsa_familia", "maior", "Vulnerabilidade social", "Ajuda a identificar territórios com maior dependência de proteção social."),
        ("Mortalidade geral por 1.000 hab.", "ia_taxa_mortalidade_1000", "maior", "Mortalidade", "Exige leitura por idade, causas e linhas de cuidado."),
        ("Mortalidade infantil por 1.000 NV", "ia_mortalidade_infantil_1000_nv", "maior", "Materno-infantil", "Sinaliza necessidade de qualificar pré-natal, parto, puerpério e puericultura."),
        ("Notificações SINAN por 1.000 hab.", "ia_sinan_notificacoes_1000", "maior", "Vigilância", "Pode indicar maior carga de agravos ou maior capacidade de notificar."),
        ("% escolas rurais", "ia_pct_escolas_rurais", "maior", "Território/intersetorial", "Apoia leitura de ruralidade, deslocamento e busca ativa."),
        ("% escolas sem internet", "ia_pct_escolas_sem_internet", "maior", "Infraestrutura social", "Sinaliza fragilidade territorial/intersetorial para ações digitais e educação em saúde."),
        ("Score geointeligência", "score_geointeligencia_aps", "maior", "Território", "Integra acesso, vulnerabilidade, capacidade e risco sanitário."),
        ("Score risco APS", "score_risco_integrado_aps", "maior", "Gestão de risco", "Sintetiza exposição atual e necessidade de mitigação."),
    ]
    rows = []
    for nome, col, direcao, eixo, leitura in indicadores:
        valor = _num(r.get(col))
        if valor == 0 and col not in r:
            continue
        pct = _percentil_0_100(base, col, valor, maior_pior=(direcao == "maior"))
        rows.append({
            "Eixo": eixo,
            "Indicador": nome,
            "Valor do município": round(valor, 2),
            "Posição relativa 0-100": pct,
            "Classificação": _classif(pct),
            "Decisão sugerida": _acao_por_score(pct, eixo),
            "Leitura técnica": leitura,
        })
    matriz = pd.DataFrame(rows).sort_values("Posição relativa 0-100", ascending=False) if rows else pd.DataFrame()

    insights = []
    def add(cond: bool, eixo: str, achado: str, porque: str, encaminhamento: str, prioridade: str):
        if cond:
            insights.append({"Prioridade": prioridade, "Eixo": eixo, "Achado relevante": achado, "Por que importa": porque, "Encaminhamento sugerido": encaminhamento})

    add(_num(r.get("ia_pop_por_equipe")) > 3500, "Capacidade APS", f"População por equipe estimada em {r.get('ia_pop_por_equipe', 0)}.", "Quando a população por equipe fica elevada, a APS tende a perder capacidade de acompanhamento longitudinal, visita domiciliar, busca ativa e coordenação do cuidado.", "Necessidade urgente de ampliar/reorganizar equipes APS, revisar CNES/INE, território adscrito, carga horária e distribuição de profissionais.", "Ruim — ampliar equipes")
    add(_num(r.get("ia_pop_por_ubs")) > 12000, "Capacidade física", f"População por UBS estimada em {r.get('ia_pop_por_ubs', 0)}.", "Concentração populacional por UBS pode indicar unidade sobrecarregada, barreira de acesso e pior tempo-resposta da APS.", "Avaliar necessidade de ampliação física, nova UBS, UBS satélite, horário estendido ou redistribuição territorial das equipes.", "Ruim — avaliar UBS")
    add(_num(r.get("ia_pct_cadunico")) >= 50, "Vulnerabilidade social", f"CadÚnico alcança aproximadamente {r.get('ia_pct_cadunico', 0)}% da população.", "Alta proporção no CadÚnico indica maior vulnerabilidade social, risco de adoecimento evitável e necessidade de acompanhamento territorial ativo.", "Implantar programa específico de busca ativa de famílias vulneráveis, integrando APS, CRAS/Assistência Social, Vigilância, Educação e saneamento.", "Ruim — ação intersetorial")
    add(_num(r.get("ia_mortalidade_infantil_1000_nv")) >= 12, "Materno-infantil", f"Mortalidade infantil estimada em {r.get('ia_mortalidade_infantil_1000_nv', 0)} por 1.000 nascidos vivos.", "Pode revelar fragilidades no pré-natal, parto, puerpério, puericultura ou vigilância do recém-nascido.", "Revisar linha materno-infantil, captação precoce, consultas, exames, visita puerperal e óbitos evitáveis.", "Crítica")
    add(_num(r.get("ia_sinan_notificacoes_1000")) >= 10, "Vigilância", f"Notificações SINAN em {r.get('ia_sinan_notificacoes_1000', 0)} por 1.000 habitantes.", "Carga relevante de notificações pode indicar agravos ativos no território ou maior capacidade de notificação; exige leitura por tipo de agravo.", "Criar plano APS-Vigilância por agravo prioritário, com busca ativa, investigação, tratamento oportuno e monitoramento mensal.", "Regular — qualificar vigilância")
    add(_num(r.get("ia_pct_escolas_rurais")) >= 40, "Ruralidade/intersetorial", f"Escolas rurais representam {r.get('ia_pct_escolas_rurais', 0)}% das escolas.", "Ruralidade elevada aumenta distância, dispersão populacional, dificuldade de transporte e risco de população invisível para a APS.", _perfil_ruralidade(r), "Ruim — programa rural")
    add(_num(r.get("score_geointeligencia_aps")) >= 65, "Georreferenciamento", f"Score geoterritorial de {r.get('score_geointeligencia_aps', 0)}.", "O território combina barreiras de acesso, vulnerabilidade social e capacidade APS insuficiente, podendo indicar vazio assistencial ou concentração inadequada de oferta.", "Avaliar necessidade de nova UBS, UBS satélite, unidade móvel, roteirização de atendimento, reorganização de equipes e validação de distâncias reais.", "Ruim — vazio/acesso")
    add(_num(r.get("score_risco_integrado_aps")) >= 65, "Gestão de risco", f"Score de risco APS de {r.get('score_risco_integrado_aps', 0)}.", "Risco integrado elevado indica que o município pode piorar seus indicadores se não houver intervenção preventiva e coordenação das áreas técnicas.", "Abrir plano de mitigação por eixo com responsáveis, prazos, evidências, metas intermediárias e acompanhamento mensal.", "Ruim — plano de mitigação")

    if not insights:
        insights.append({"Prioridade": "Bom/regular", "Eixo": "Síntese", "Achado relevante": "Não houve gatilho crítico automático nos principais cruzamentos.", "Por que importa": "A situação não indica intervenção urgente pela régua atual, mas ainda depende de validação local, séries históricas e qualidade das bases.", "Encaminhamento sugerido": "Manter acompanhamento técnico, atualizar bases e abrir análise específica se houver demanda regional ou alteração dos indicadores."})
    insights_df = pd.DataFrame(insights)

    texto = gerar_texto_relatorio_municipal(r, insights_df)
    cards = {
        "Municipio": r.get("municipio", municipio),
        "Região": r.get("regiao_saude", "-"),
        "População": _num(r.get("populacao")),
        "Equipes APS": _num(r.get("total_equipes_aps")),
        "UBS": _num(r.get("total_ubs")),
        "Profissionais": _num(r.get("total_profissionais_aps")),
        "Score Geo": _num(r.get("score_geointeligencia_aps")),
        "Score Risco": _num(r.get("score_risco_integrado_aps")),
    }
    return {"ok": True, "linha": r, "cards": cards, "cards_explicativos": cards_explicativos_municipio(r), "glossario_decisorio": glossario_decisorio_aps(), "matriz": matriz, "insights": insights_df, "texto": texto, "fontes": fontes, "base_integrada": base}


def gerar_texto_relatorio_municipal(r: dict[str, Any], insights: pd.DataFrame) -> str:
    mun = r.get("municipio", "município")
    reg = r.get("regiao_saude", "-")
    partes = [
        f"O município de {mun}, integrante da Região de Saúde {reg}, apresenta população estimada de {int(_num(r.get('populacao'))):,} habitantes, com {int(_num(r.get('total_equipes_aps')))} equipes APS, {int(_num(r.get('total_ubs')))} UBS/estabelecimentos e {int(_num(r.get('total_profissionais_aps')))} vínculos profissionais registrados nas bases consolidadas.",
        f"A leitura integrada aponta aproximadamente {_num(r.get('ia_pop_por_equipe')):.1f} habitantes por equipe e {_num(r.get('ia_pop_por_ubs')):.1f} habitantes por UBS, devendo estes indicadores ser confrontados com adscrição real, capacidade física, carga horária, produção assistencial e localização das unidades.",
    ]
    if _num(r.get("ia_pct_cadunico")) > 0:
        partes.append(f"No eixo social, estima-se que {_num(r.get('ia_pct_cadunico')):.1f}% da população esteja registrada no CadÚnico e {_num(r.get('ia_pct_bolsa_familia')):.1f}% vinculada ao Bolsa Família, sinalizando a importância de uma resposta integrada entre APS, assistência social, vigilância e educação.")
    if _num(r.get("score_geointeligencia_aps")) > 0:
        partes.append(f"No eixo territorial, o score de geointeligência é {_num(r.get('score_geointeligencia_aps')):.1f}, com leitura: {r.get('motivo_geointeligencia', '-')}")
    if _num(r.get("score_risco_integrado_aps")) > 0:
        partes.append(f"Na gestão de risco APS, o score integrado é {_num(r.get('score_risco_integrado_aps')):.1f}, classificado como {r.get('classificacao_risco_integrado', '-')}. Principal fator indicado: {r.get('principal_fator_risco', '-')}")
    if isinstance(insights, pd.DataFrame) and not insights.empty:
        principais = insights.head(3)
        achados = "; ".join([f"{x['Eixo']}: {x['Achado relevante']}" for _, x in principais.iterrows()])
        partes.append(f"Principais achados automáticos: {achados}")
    partes.append("Recomenda-se utilizar esta análise como peça técnica orientativa para aprofundamento municipal, priorização de visitas, pactuação regional e definição de plano de ação com responsáveis, prazos e evidências de mitigação.")
    return "\n\n".join(partes).replace(",", ".", 1) if False else "\n\n".join(partes)


def georreferenciamento_insights() -> dict[str, Any]:
    try:
        from services.geointeligencia_aps_service import carregar_geointeligencia_aps
        df = carregar_geointeligencia_aps()
    except Exception:
        df = pd.DataFrame()
    if df is None or df.empty:
        return {"ok": False, "mensagem": "Geointeligência integrada indisponível."}
    out = df.copy()
    for c in ["score_social_geo", "score_acesso_territorial", "score_fragilidade_capacidade", "score_pressao_assistencial", "score_risco_sanitario", "score_geointeligencia_aps"]:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0)
        else:
            out[c] = 0.0
    out["quadrante_decisao"] = out.apply(lambda r: _quadrante_geo(r), axis=1)
    out["insight_territorial"] = out.apply(lambda r: _insight_geo(r), axis=1)
    out["acao_geo_sugerida"] = out.apply(lambda r: _acao_geo(r), axis=1)
    resumo = out.groupby("quadrante_decisao", as_index=False).agg(
        municipios=("municipio", "count"),
        score_medio=("score_geointeligencia_aps", "mean"),
        acesso_medio=("score_acesso_territorial", "mean"),
        social_medio=("score_social_geo", "mean"),
        capacidade_media=("score_fragilidade_capacidade", "mean"),
    ).sort_values("score_medio", ascending=False)
    return {"ok": True, "base": out, "resumo_quadrantes": resumo.round(1)}


def _quadrante_geo(r: pd.Series) -> str:
    social = _num(r.get("score_social_geo"))
    cap = _num(r.get("score_fragilidade_capacidade"))
    acesso = _num(r.get("score_acesso_territorial"))
    if acesso >= 70 and social >= 55 and cap >= 55:
        return "Péssimo — estudar nova UBS/UBS satélite ou unidade móvel"
    if social >= 60 and cap >= 60:
        return "Ruim — ampliar/reorganizar equipes em território vulnerável"
    if acesso >= 65 and social >= 50:
        return "Ruim — acesso difícil com vulnerabilidade social"
    if acesso >= 65:
        return "Regular/ruim — barreira territorial predominante"
    if cap >= 65:
        return "Ruim — capacidade APS insuficiente"
    if social >= 65:
        return "Regular/ruim — vulnerabilidade social predominante"
    return "Bom/regular — sem vazio forte pela régua atual"


def _insight_geo(r: pd.Series) -> str:
    return f"{r.get('municipio','-')} combina acesso {float(r.get('score_acesso_territorial',0)):.1f}, vulnerabilidade {float(r.get('score_social_geo',0)):.1f} e capacidade {float(r.get('score_fragilidade_capacidade',0)):.1f}. {r.get('motivo_geointeligencia','')}"


def _acao_geo(r: pd.Series) -> str:
    q = str(r.get("quadrante_decisao", ""))
    if "nova UBS" in q or "unidade móvel" in q:
        return "Prioridade máxima: realizar estudo de implantação de nova UBS, UBS satélite, unidade móvel ou rota assistencial, validando população descoberta, distância real e capacidade das unidades existentes."
    if "ampliar/reorganizar equipes" in q or "capacidade" in q:
        return "Necessidade urgente de ampliar ou reorganizar equipes APS, auditar CNES/INE, carga horária, microáreas, população adscrita e localização das UBS."
    if "acesso difícil" in q or "barreira territorial" in q:
        return "Validar rotas reais, transporte, distância até UBS, ruralidade, bairros/localidades e implantar estratégia extramuros quando couber."
    if "vulnerabilidade" in q:
        return "Criar programa territorial de busca ativa com CadÚnico/PBF/BPC, APS, CRAS, Vigilância e Educação."
    return "Situação boa/regular: manter acompanhamento territorial e aprofundar quando houver mudança de base, demanda regional ou alerta local."


def risco_eixos_explicados(municipio: str | None = None) -> dict[str, Any]:
    try:
        from services.gestao_risco_service import carregar_gestao_risco_aps
        res = carregar_gestao_risco_aps()
        df = res.get("base", pd.DataFrame()) if isinstance(res, dict) else pd.DataFrame()
    except Exception:
        df = pd.DataFrame()
    if df is None or df.empty:
        return {"ok": False, "mensagem": "Base de risco indisponível."}
    base = df.copy()
    if municipio:
        base = base[base["municipio"].map(_norm_txt) == _norm_txt(municipio)]
    if base.empty:
        return {"ok": False, "mensagem": "Município não encontrado na base de risco."}
    r = base.iloc[0]
    eixos = [
        ("Vulnerabilidade social", "risco_social", "Risco de maior demanda social, insegurança de renda e necessidade de ação intersetorial.", "Busca ativa com CadÚnico/PBF/BPC, integração APS-Assistência Social e estratificação familiar."),
        ("Capacidade APS", "risco_capacidade_aps", "Risco de insuficiência de equipes, UBS ou força de trabalho diante da população.", "Auditar CNES/INE, profissionais, carga horária, território adscrito e suficiência de UBS/equipes."),
        ("Acesso territorial", "risco_acesso_territorial", "Risco de barreiras de distância, dispersão, ruralidade e vazios assistenciais.", "Validar rotas, mapa de distâncias, bairros/localidades, assentamentos e UBS de referência."),
        ("Materno-infantil", "risco_materno_infantil", "Risco de eventos evitáveis no pré-natal, nascimento e primeira infância.", "Fortalecer captação precoce, 7 consultas, exames, visita puerperal e puericultura."),
        ("Mortalidade", "risco_mortalidade", "Risco associado ao perfil de óbitos e possíveis fragilidades nas linhas de cuidado.", "Analisar causas, idade, evitabilidade e conexão APS-urgência-regulação."),
        ("Vigilância e agravos", "risco_vigilancia", "Risco de agravos transmissíveis, violências, acidentes e falhas de acompanhamento oportuno.", "Integrar APS e vigilância para notificação, investigação, tratamento e busca ativa."),
        ("Intersetorial/educação", "risco_intersetorial", "Risco de determinantes sociais que extrapolam a unidade de saúde.", "Articular educação, saneamento, assistência social e gestão municipal."),
        ("Equidade territorial", "risco_equidade_territorial", "Risco de invisibilizar povos tradicionais, ruralidade e populações dispersas.", "Priorizar estratégias diferenciadas para indígenas, quilombolas, assentamentos e áreas remotas."),
    ]
    rows = []
    for eixo, col, risco, mitig in eixos:
        score = _num(r.get(col))
        if score >= 85:
            tendencia = "PÉSSIMO: risco atual crítico; exige intervenção imediata para evitar agravamento dos indicadores."
        elif score >= 70:
            tendencia = "RUIM: risco alto; pode pressionar a rede em curto prazo se não houver mitigação."
        elif score >= 45:
            tendencia = "REGULAR: risco moderado; exige ação preventiva e acompanhamento técnico."
        else:
            tendencia = "BOM: sem alerta forte pela régua atual; manter vigilância e atualização das bases."
        rows.append({"Eixo": eixo, "Score": round(score, 1), "Classificação": _classif(score, 70, 45), "Risco atual": risco, "Risco futuro/tendência": tendencia, "Decisão sugerida": _acao_por_score(score, eixo), "Como mitigar": mitig})
    eixos_df = pd.DataFrame(rows).sort_values("Score", ascending=False)
    return {"ok": True, "linha": r.to_dict(), "eixos": eixos_df}

# --- Complementos V7: explicações, notas e linguagem de decisão ---

def classificar_conceito_decisorio(score: float) -> dict[str, str]:
    """Régua objetiva para substituir termos genéricos por conceitos acionáveis."""
    score = _num(score)
    if score >= 85:
        return {
            "conceito": "Péssimo",
            "tom": "critico",
            "explicacao": "O indicador está em patamar muito desfavorável no conjunto analisado. Exige intervenção imediata ou estudo técnico urgente.",
            "decisao": "Abrir plano de ação prioritário com responsável, prazo curto, evidência de execução e reavaliação mensal.",
        }
    if score >= 70:
        return {
            "conceito": "Ruim",
            "tom": "alto",
            "explicacao": "O indicador está desfavorável e tende a pressionar a APS se não houver correção ou reorganização.",
            "decisao": "Priorizar visita técnica, validação local e intervenção dirigida no eixo que puxou o alerta.",
        }
    if score >= 45:
        return {
            "conceito": "Regular",
            "tom": "moderado",
            "explicacao": "Há sinal de atenção. A situação não é crítica, mas pode piorar se os fatores de risco se acumularem.",
            "decisao": "Manter acompanhamento técnico, ação preventiva e comparação com municípios da mesma região/porte.",
        }
    return {
        "conceito": "Bom",
        "tom": "baixo",
        "explicacao": "Não há alerta forte pela régua atual, embora a leitura dependa da qualidade das bases e validação territorial.",
        "decisao": "Manter monitoramento e atualização das bases, sem priorização urgente pela régua automática.",
    }


def glossario_decisorio_aps() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Termo": "Bom",
            "Significado prático": "Sem alerta forte pela régua automática atual.",
            "Conduta sugerida": "Manter acompanhamento, atualizar bases e validar se há demanda local não captada pelo dado.",
        },
        {
            "Termo": "Regular",
            "Significado prático": "Existe ponto de atenção, mas ainda não caracteriza urgência automática.",
            "Conduta sugerida": "Ação preventiva, validação local, comparação regional e acompanhamento periódico.",
        },
        {
            "Termo": "Ruim",
            "Significado prático": "Situação desfavorável em um ou mais eixos. Pode exigir reorganização, ampliação ou programa específico.",
            "Conduta sugerida": "Priorizar análise técnica, visita/validação, plano de intervenção e pactuação com município/ERS.",
        },
        {
            "Termo": "Péssimo",
            "Significado prático": "Situação crítica pela régua de priorização. O risco de manutenção ou agravamento é elevado.",
            "Conduta sugerida": "Estudo urgente e plano de ação com responsável, prazo, evidência e monitoramento mensal.",
        },
        {
            "Termo": "Necessidade urgente de construção/implantação",
            "Significado prático": "Indica hipótese técnica de nova UBS, UBS satélite, unidade móvel ou estrutura complementar.",
            "Conduta sugerida": "Não é decisão automática de obra: exige validar população descoberta, distância real, capacidade das UBS existentes, terreno, custeio, CNES e pactuação.",
        },
        {
            "Termo": "Necessidade urgente de ampliação de equipes",
            "Significado prático": "A capacidade APS parece insuficiente frente à população, vulnerabilidade ou pressão territorial.",
            "Conduta sugerida": "Revisar equipes CNES/INE, carga horária, profissionais, microáreas, adscrição e possibilidade de novas equipes ou reordenamento.",
        },
        {
            "Termo": "Programa específico territorial",
            "Significado prático": "O problema não se resolve apenas com indicador; exige estratégia para público ou território específico.",
            "Conduta sugerida": "Exemplos: programa rural itinerante, busca ativa CadÚnico/PBF/BPC, rota APS-Vigilância, cuidado materno-infantil, ações com escolas rurais e assentamentos.",
        },
    ])


def _programas_indicados(r: dict[str, Any]) -> list[str]:
    programas = []
    if _num(r.get("ia_pct_escolas_rurais")) >= 70 or _num(r.get("score_acesso_territorial")) >= 70:
        programas.append("Programa APS Itinerante/Rural: calendário extramuros, rota com escolas rurais, assentamentos e localidades distantes.")
    elif _num(r.get("ia_pct_escolas_rurais")) >= 40:
        programas.append("Estratégia de busca ativa rural: visitas programadas, ações em escolas rurais e validação de transporte/rotas.")
    if _num(r.get("ia_pct_cadunico")) >= 50 or _num(r.get("ia_pct_bolsa_familia")) >= 30:
        programas.append("Programa de Busca Ativa Intersetorial: APS + CRAS + Vigilância + Educação para famílias CadÚnico/PBF/BPC.")
    if _num(r.get("score_fragilidade_capacidade")) >= 70 or _num(r.get("ia_pop_por_equipe")) > 3500:
        programas.append("Plano de Reorganização de Equipes APS: revisão de CNES/INE, microáreas, carga horária, população adscrita e suficiência de equipes.")
    if _num(r.get("score_vazio_assistencial")) >= 70 or _num(r.get("score_geointeligencia_aps")) >= 75:
        programas.append("Estudo de Expansão Territorial: nova UBS, UBS satélite, unidade móvel ou redefinição de referência territorial.")
    if _num(r.get("ia_mortalidade_infantil_1000_nv")) >= 12:
        programas.append("Plano Materno-Infantil Prioritário: captação precoce, pré-natal, exames, puerpério, puericultura e investigação de óbitos evitáveis.")
    if _num(r.get("ia_sinan_notificacoes_1000")) >= 10:
        programas.append("Plano APS-Vigilância por Agravo: investigação, busca ativa, tratamento oportuno e acompanhamento mensal.")
    if not programas:
        programas.append("Manutenção qualificada: atualizar bases, monitorar tendência e aprofundar apenas eixos com alerta local.")
    return programas


def cards_explicativos_municipio(r: dict[str, Any]) -> pd.DataFrame:
    score = _num(r.get("score_risco_integrado_aps") or r.get("score_geointeligencia_aps") or r.get("score_prioridade_integrada"))
    conceito = classificar_conceito_decisorio(score)
    decisao = _acao_por_score(score, "gestao de risco")
    if _num(r.get("score_vazio_assistencial")) >= 70 and _num(r.get("score_fragilidade_capacidade")) >= 60:
        decisao = "Estudar com urgência nova UBS/UBS satélite, unidade móvel ou reorganização territorial com ampliação de equipes."
    elif _num(r.get("score_fragilidade_capacidade")) >= 70:
        decisao = "Priorizar ampliação/reorganização de equipes APS e auditoria CNES/INE/carga horária."
    elif _num(r.get("ia_pct_escolas_rurais")) >= 70:
        decisao = "Implantar estratégia rural/itinerante com calendário extramuros e rotas assistenciais."

    validar = [
        "Distância real até UBS de referência e tempo de deslocamento.",
        "População adscrita por equipe e microárea.",
        "CNES/INE, carga horária e profissionais ativos.",
        "Capacidade física da UBS e possibilidade de ampliação.",
        "Demandas locais informadas pelo município e ERS.",
    ]
    return pd.DataFrame([
        {"Card": "Classificação simples", "Valor": conceito["conceito"], "Informação": conceito["explicacao"], "Nota técnica": conceito["decisao"]},
        {"Card": "Decisão mais provável", "Valor": "Encaminhamento", "Informação": decisao, "Nota técnica": "É uma recomendação orientativa. A decisão final depende de validação local, orçamento, custeio e pactuação."},
        {"Card": "O que validar antes de decidir", "Valor": "Checklist", "Informação": " • ".join(validar), "Nota técnica": "Evita transformar score em decisão automática e reduz risco de erro por base incompleta."},
        {"Card": "Programas indicados", "Valor": "Ações possíveis", "Informação": " | ".join(_programas_indicados(r)), "Nota técnica": "Programas devem ser adaptados ao porte, ruralidade, vulnerabilidade e capacidade instalada do município."},
    ])
