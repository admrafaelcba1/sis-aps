from __future__ import annotations

import math
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd

from database.queries import read_table
from services.qualidade_dados_service import deduplicar_estabelecimentos_saude

CODIGOS_EQUIPES_APS = {
    "70": "eSF — Estratégia Saúde da Família",
    "71": "eSB — Equipe de Saúde Bucal",
    "72": "eNASF-AP / Multiprofissional",
    "73": "eCR — Consultório na Rua",
    "74": "eAPP — Atenção Primária Prisional",
    "76": "eAP — Equipe de Atenção Primária",
}

REF_DIR = Path("data/reference")
ARQ_DETERMINANTES = REF_DIR / "determinantes_sociais_sistema_antigo.csv"
ARQ_TERRITORIOS = REF_DIR / "bairros_localidades_setores_sistema_antigo.csv"
ARQ_UBS = REF_DIR / "ubs_georreferenciadas_sistema_antigo.csv"


def _chave(valor: Any) -> str:
    texto = str(valor or "").strip().upper()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    return " ".join(texto.split())


def _num(valor: Any, default: float = 0.0) -> float:
    try:
        if valor is None:
            return default
        if isinstance(valor, str):
            valor = valor.strip().replace("%", "").replace(".", "").replace(",", ".")
            if not valor:
                return default
        out = float(valor)
        if math.isnan(out):
            return default
        return out
    except Exception:
        return default


def _serie_num(s: pd.Series | Any, default: float = 0.0) -> pd.Series:
    if isinstance(s, pd.Series):
        return pd.to_numeric(s, errors="coerce").fillna(default)
    return pd.Series(dtype="float64")


def _ler_csv_flex(caminho: Path) -> pd.DataFrame:
    if not caminho.exists():
        return pd.DataFrame()
    for sep in [";", ",", "\t"]:
        try:
            df = pd.read_csv(caminho, sep=sep, encoding="utf-8-sig")
            if len(df.columns) > 1:
                df.columns = [str(c).replace("\ufeff", "").strip() for c in df.columns]
                return df
        except Exception:
            pass
    try:
        df = pd.read_csv(caminho, sep=None, engine="python", encoding="utf-8-sig")
        df.columns = [str(c).replace("\ufeff", "").strip() for c in df.columns]
        return df
    except Exception:
        return pd.DataFrame()


def _codigo7(valor: Any) -> str:
    import re
    dig = re.sub(r"\D", "", str(valor or ""))
    if len(dig) >= 7:
        return dig[:7]
    return dig


def _safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    num = pd.to_numeric(num, errors="coerce")
    den = pd.to_numeric(den, errors="coerce")
    return num.divide(den.where(den != 0)).replace([float("inf"), -float("inf")], pd.NA)


def _normalizar_0_100(s: pd.Series, inverter: bool = False) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    if s.dropna().empty:
        return pd.Series([0] * len(s), index=s.index, dtype="float64")
    mn = s.min(skipna=True)
    mx = s.max(skipna=True)
    if pd.isna(mn) or pd.isna(mx) or mx == mn:
        out = pd.Series([50] * len(s), index=s.index, dtype="float64")
    else:
        out = ((s - mn) / (mx - mn)) * 100
    if inverter:
        out = 100 - out
    return out.fillna(0).clip(0, 100)


def _carregar_determinantes_ref() -> pd.DataFrame:
    det = _ler_csv_flex(ARQ_DETERMINANTES)
    if det.empty:
        return pd.DataFrame()
    det["codigo_ibge"] = det.get("codigo_ibge", "").map(_codigo7)
    det["chave_municipio"] = det.get("municipio", "").astype(str).str.replace(" - MT", "", regex=False).map(_chave)
    cols = [
        "codigo_ibge", "chave_municipio", "taxa_alfabetizacao_pct", "taxa_analfabetismo_estimado_pct",
        "nivel_instrucao_baixo_pct", "renda_censo_2022", "pct_rdpc_ate_1_4_sm_2022",
        "pct_rdpc_ate_1_2_sm_2022", "indice_vulnerabilidade_saneamento_2022", "percentual_rural_2022",
    ]
    cols = [c for c in cols if c in det.columns]
    out = det[cols].copy()
    for c in out.columns:
        if c not in {"codigo_ibge", "chave_municipio"}:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out.drop_duplicates("chave_municipio")


def _carregar_territorios_ref() -> pd.DataFrame:
    ter = _ler_csv_flex(ARQ_TERRITORIOS)
    if ter.empty:
        return pd.DataFrame()
    ter["codigo_ibge"] = ter.get("codigo_ibge", "").map(_codigo7)
    ter["chave_municipio"] = ter.get("municipio", "").astype(str).str.replace(" - MT", "", regex=False).map(_chave)
    for c in [
        "populacao", "renda_media", "percentual_baixa_renda", "percentual_bolsa_familia", "percentual_cadunico",
        "percentual_bpc", "percentual_baixa_escolaridade", "percentual_saneamento_inadequado", "percentual_rural",
        "indicador_pressao_aps", "percentual_plano_saude_estimado", "latitude", "longitude",
    ]:
        if c in ter.columns:
            ter[c] = pd.to_numeric(ter[c], errors="coerce")
    agg = {
        "setor_censitario": "count" if "setor_censitario" in ter.columns else "size",
    }
    # Agregação robusta por município.
    g = ter.groupby("chave_municipio", dropna=False)
    out = pd.DataFrame({
        "chave_municipio": list(g.groups.keys()),
        "territorios_mapeados": g.size().values,
    })
    metricas_media = [
        "indicador_pressao_aps", "percentual_baixa_renda", "percentual_bolsa_familia", "percentual_cadunico",
        "percentual_bpc", "percentual_baixa_escolaridade", "percentual_saneamento_inadequado", "percentual_rural",
        "percentual_plano_saude_estimado", "renda_media",
    ]
    for c in metricas_media:
        if c in ter.columns:
            out[c + "_territorios"] = g[c].mean().values
    if "populacao" in ter.columns:
        out["populacao_territorios"] = g["populacao"].sum().values
    if "indicador_pressao_aps" in ter.columns:
        crit = ter.assign(_crit=(pd.to_numeric(ter["indicador_pressao_aps"], errors="coerce") >= 70).astype(int))
        out = out.merge(crit.groupby("chave_municipio")['_crit'].sum().reset_index().rename(columns={"_crit": "territorios_pressao_alta"}), on="chave_municipio", how="left")
    return out



def _carregar_acesso_territorial_aps() -> pd.DataFrame:
    """Agrega a distância de bairros/localidades/setores e assentamentos até UBS/APS.

    A função usa as rotinas já existentes do georreferenciamento. O objetivo aqui
    não é recalcular outra metodologia, mas trazer para o dashboard executivo uma
    síntese municipal do acesso territorial, especialmente zonas rurais e
    comunidades/localidades distantes.
    """
    try:
        from services.georreferenciamento_service import (
            calcular_distancias_bairros_localidades_aps,
            montar_acesso_rural_aps,
        )
    except Exception:
        return pd.DataFrame()

    partes: list[pd.DataFrame] = []

    try:
        geo = calcular_distancias_bairros_localidades_aps()
        mun = geo.get("resumo_municipal", pd.DataFrame()).copy()
        if not mun.empty and "municipio" in mun.columns:
            mun["chave_municipio"] = mun["municipio"].map(_chave)
            mun = mun.rename(columns={
                "territorios": "territorios_com_distancia_aps",
                "populacao_referencia": "populacao_referencia_territorial",
                "distancia_media_km": "distancia_media_territorios_km",
                "distancia_mediana_km": "distancia_mediana_territorios_km",
                "distancia_maxima_km": "distancia_maxima_territorios_km",
                "criticos": "territorios_criticos_distancia",
                "distantes": "territorios_distantes_distancia",
                "atencao": "territorios_atencao_distancia",
                "proximos": "territorios_proximos_distancia",
                "criticos_distantes": "territorios_criticos_distantes",
                "percentual_critico_distante": "percentual_territorios_criticos_distantes",
                "populacao_critica": "populacao_territorios_criticos",
                "populacao_distante": "populacao_territorios_distantes",
            })
            keep = [c for c in [
                "chave_municipio", "territorios_com_distancia_aps", "populacao_referencia_territorial",
                "distancia_media_territorios_km", "distancia_mediana_territorios_km", "distancia_maxima_territorios_km",
                "territorios_criticos_distancia", "territorios_distantes_distancia", "territorios_atencao_distancia",
                "territorios_proximos_distancia", "territorios_criticos_distantes",
                "percentual_territorios_criticos_distantes", "populacao_territorios_criticos", "populacao_territorios_distantes",
            ] if c in mun.columns]
            partes.append(mun[keep])
    except Exception:
        pass

    try:
        rural = montar_acesso_rural_aps()
        mat = rural.get("matriz_alertas", pd.DataFrame()).copy()
        if not mat.empty and "municipio" in mat.columns:
            mat["chave_municipio"] = mat["municipio"].map(_chave)
            mat = mat.rename(columns={
                "assentamentos": "assentamentos_com_distancia_aps",
                "criticos": "assentamentos_criticos_distancia",
                "distantes": "assentamentos_distantes_distancia",
                "distancia_media_km": "distancia_media_assentamentos_km",
                "distancia_maxima_km": "distancia_maxima_assentamentos_km",
            })
            keep = [c for c in [
                "chave_municipio", "nivel_alerta_acesso_rural", "assentamentos_com_distancia_aps",
                "assentamentos_criticos_distancia", "assentamentos_distantes_distancia",
                "distancia_media_assentamentos_km", "distancia_maxima_assentamentos_km", "encaminhamento_sugerido",
            ] if c in mat.columns]
            partes.append(mat[keep])
    except Exception:
        pass

    if not partes:
        return pd.DataFrame()

    out = partes[0].copy()
    for parte in partes[1:]:
        out = out.merge(parte, on="chave_municipio", how="outer")

    for c in out.columns:
        if c not in {"chave_municipio", "nivel_alerta_acesso_rural", "encaminhamento_sugerido"}:
            out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0)

    if {"populacao_territorios_criticos", "populacao_territorios_distantes"}.issubset(out.columns):
        out["populacao_territorios_criticos_distantes"] = out["populacao_territorios_criticos"] + out["populacao_territorios_distantes"]
    else:
        out["populacao_territorios_criticos_distantes"] = 0

    if {"populacao_territorios_criticos_distantes", "populacao_referencia_territorial"}.issubset(out.columns):
        out["percentual_populacao_critica_distante"] = _safe_div(
            out["populacao_territorios_criticos_distantes"] * 100,
            out["populacao_referencia_territorial"],
        ).fillna(0)
    else:
        out["percentual_populacao_critica_distante"] = 0

    if {"assentamentos_criticos_distancia", "assentamentos_distantes_distancia"}.issubset(out.columns):
        out["assentamentos_criticos_distantes"] = out["assentamentos_criticos_distancia"] + out["assentamentos_distantes_distancia"]
    else:
        out["assentamentos_criticos_distantes"] = 0

    return out.drop_duplicates("chave_municipio")



def _aplicar_total_ubs_unicas(base: pd.DataFrame) -> pd.DataFrame:
    """Corrige total_ubs usando CNES único, sem alterar o banco físico."""
    if base is None or base.empty:
        return base
    try:
        ubs = read_table("estabelecimentos_saude")
        if ubs.empty or "municipio" not in ubs.columns or "cnes" not in ubs.columns:
            return base
        ubs = deduplicar_estabelecimentos_saude(ubs)
        if ubs.empty:
            return base
        ubs["chave_municipio"] = ubs["municipio"].map(_chave)
        total = ubs.groupby("chave_municipio").size().reset_index(name="total_ubs_corrigido")
        out = base.merge(total, on="chave_municipio", how="left")
        out["total_ubs_original_base"] = out.get("total_ubs", 0)
        out["total_ubs"] = pd.to_numeric(out["total_ubs_corrigido"], errors="coerce").fillna(pd.to_numeric(out.get("total_ubs", 0), errors="coerce").fillna(0))
        out["alerta_duplicidade_ubs"] = ""
        mask = pd.to_numeric(out["total_ubs_original_base"], errors="coerce").fillna(0) != pd.to_numeric(out["total_ubs"], errors="coerce").fillna(0)
        out.loc[mask, "alerta_duplicidade_ubs"] = "Total de UBS corrigido por CNES único; havia duplicidade na base bruta."
        return out.drop(columns=["total_ubs_corrigido"], errors="ignore")
    except Exception:
        return base

PESOS_SCORE_INTEGRADO = {
    "score_acesso_territorial": {
        "rotulo": "Acesso territorial à UBS/APS",
        "peso": 0.30,
        "max_pontos": 30,
        "descricao": "Distância entre territórios e UBS/APS, presença de localidades críticas, população em áreas distantes e assentamentos com dificuldade territorial.",
    },
    "score_vazio_assistencial": {
        "rotulo": "Necessidade assistencial estimada",
        "peso": 0.20,
        "max_pontos": 20,
        "descricao": "Relação entre população e oferta disponível de UBS/equipes, além de sinais territoriais de maior necessidade potencial.",
    },
    "score_vulnerabilidade_social": {
        "rotulo": "Vulnerabilidade social",
        "peso": 0.20,
        "max_pontos": 20,
        "descricao": "Marcadores sociais disponíveis, como renda, escolaridade, saneamento e condições territoriais associadas a maior sensibilidade social.",
    },
    "score_fragilidade_capacidade": {
        "rotulo": "Fragilidade da capacidade instalada",
        "peso": 0.20,
        "max_pontos": 20,
        "descricao": "Disponibilidade relativa de UBS, equipes APS e profissionais em relação à população.",
    },
    "score_equidade_territorial": {
        "rotulo": "Equidade territorial",
        "peso": 0.10,
        "max_pontos": 10,
        "descricao": "Presença de áreas rurais, assentamentos, terras indígenas, territórios especiais e desigualdades territoriais relevantes.",
    },
}

FAIXAS_COMPONENTE_SCORE = [
    (75, "Crítico", "forte sinal de problema nesta dimensão"),
    (60, "Alto", "alerta relevante que exige análise técnica"),
    (40, "Moderado", "sinal intermediário que deve ser acompanhado"),
    (0, "Baixo/monitoramento", "menor intensidade relativa na base atual"),
]


def classificar_score_componente(score: float) -> tuple[str, str]:
    v = _num(score)
    for limite, classe, interpretacao in FAIXAS_COMPONENTE_SCORE:
        if v >= limite:
            return classe, interpretacao
    return "Baixo/monitoramento", "menor intensidade relativa na base atual"


def _fmt_pontos(valor: float) -> str:
    try:
        return f"{float(valor):.1f}".replace(".", ",")
    except Exception:
        return "0,0"


def explicar_componente_score(row: pd.Series, componente: str) -> dict[str, Any]:
    cfg = PESOS_SCORE_INTEGRADO[componente]
    bruto = _num(row.get(componente))
    pontos = round(bruto * cfg["peso"], 1)
    classe, interpretacao = classificar_score_componente(bruto)
    max_pontos = cfg["max_pontos"]

    municipio = str(row.get("municipio", "município")).strip() or "município"
    detalhes = []
    if componente == "score_acesso_territorial":
        dist_max = _num(row.get("distancia_maxima_territorios_km"))
        dist_med = _num(row.get("distancia_media_territorios_km"))
        terr_cd = int(_num(row.get("territorios_criticos_distantes")))
        ass_cd = int(_num(row.get("assentamentos_criticos_distantes")))
        detalhes = [
            f"maior distância territorial registrada: {_fmt_pontos(dist_max)} km",
            f"distância média dos territórios: {_fmt_pontos(dist_med)} km",
            f"territórios críticos/distantes: {terr_cd}",
            f"assentamentos críticos/distantes: {ass_cd}",
        ]
        if classe in ["Crítico", "Alto"]:
            leitura = "indica dificuldade territorial relevante de acesso à UBS/APS, com necessidade de validar áreas rurais, localidades distantes e referência territorial."
        else:
            leitura = "indica que a dificuldade territorial não é o principal fator relativo neste momento, embora pontos específicos ainda possam exigir validação."
    elif componente == "score_vazio_assistencial":
        pop_eq = _num(row.get("populacao_por_equipe"))
        pop_ubs = _num(row.get("populacao_por_ubs"))
        terr_pressao = int(_num(row.get("territorios_pressao_alta")))
        detalhes = [
            f"população por equipe APS: {_fmt_pontos(pop_eq)}",
            f"população por UBS: {_fmt_pontos(pop_ubs)}",
            f"territórios com maior necessidade potencial: {terr_pressao}",
        ]
        if classe in ["Crítico", "Alto"]:
            leitura = "indica maior necessidade assistencial estimada em relação à oferta disponível, sugerindo avaliação de cobertura, adscrição, suficiência de equipes e distribuição de UBS."
        else:
            leitura = "indica menor sinal relativo de necessidade assistencial estimada na régua estadual atual."
    elif componente == "score_vulnerabilidade_social":
        renda = _num(row.get("pct_rdpc_ate_1_2_sm_2022"))
        escolaridade = _num(row.get("nivel_instrucao_baixo_pct"))
        saneamento = _num(row.get("indice_vulnerabilidade_saneamento_2022"))
        detalhes = [
            f"baixa renda: {_fmt_pontos(renda)}%",
            f"baixa escolaridade/instrução: {_fmt_pontos(escolaridade)}%",
            f"sinal de vulnerabilidade de saneamento: {_fmt_pontos(saneamento)}",
        ]
        if classe in ["Crítico", "Alto"]:
            leitura = "indica sensibilidade social relevante, recomendando leitura articulada com vigilância, determinantes sociais e ações intersetoriais."
        else:
            leitura = "indica vulnerabilidade social em nível não predominante na classificação atual."
    elif componente == "score_fragilidade_capacidade":
        eq10 = _num(row.get("equipes_por_10mil_hab"))
        ubs10 = _num(row.get("ubs_por_10mil_hab"))
        prof_eq = _num(row.get("profissionais_por_equipe"))
        detalhes = [
            f"equipes por 10 mil habitantes: {_fmt_pontos(eq10)}",
            f"UBS por 10 mil habitantes: {_fmt_pontos(ubs10)}",
            f"profissionais por equipe: {_fmt_pontos(prof_eq)}",
        ]
        if classe in ["Crítico", "Alto"]:
            leitura = "indica menor capacidade instalada relativa, exigindo verificação de UBS, equipes APS, profissionais e distribuição da rede."
        else:
            leitura = "indica que a capacidade instalada não aparece como principal fator de risco relativo neste momento."
    else:
        terras = int(_num(row.get("terras_indigenas_qtd_registros")))
        assent = int(_num(row.get("assentamentos_qtd_registros")))
        areas = int(_num(row.get("areas_contaminadas_qtd_registros")))
        detalhes = [
            f"registros de terras indígenas/interseções: {terras}",
            f"registros de assentamentos: {assent}",
            f"ocorrências/áreas especiais mapeadas: {areas}",
        ]
        if classe in ["Crítico", "Alto"]:
            leitura = "indica necessidade de atenção diferenciada a populações e territórios especiais, com validação local e regional."
        else:
            leitura = "indica menor sinal relativo de desigualdade territorial especial na base atual."

    return {
        "componente": cfg["rotulo"],
        "score_bruto_0_100": round(bruto, 1),
        "peso_percentual": int(cfg["peso"] * 100),
        "pontos_obtidos": pontos,
        "maximo_pontos": max_pontos,
        "leitura": classe,
        "descricao_criterio": cfg["descricao"],
        "interpretacao": leitura,
        "evidencias": "; ".join(detalhes),
        "frase_gestao": (
            f"{municipio} obteve {_fmt_pontos(pontos)} de {max_pontos} pontos possíveis em {cfg['rotulo']}. "
            f"A leitura é {classe.lower()} e {leitura}"
        ),
    }


def montar_explicabilidade_municipio(municipio: str | None = None, row: pd.Series | None = None) -> dict[str, Any]:
    base = carregar_base_dashboard()
    if base.empty:
        return {"componentes": pd.DataFrame(), "sintese": "Base indisponível."}

    if row is None:
        if municipio:
            achado = base[base["municipio"].map(_chave).eq(_chave(municipio))]
            if achado.empty:
                return {"componentes": pd.DataFrame(), "sintese": "Município não encontrado na base atual."}
            row = achado.iloc[0]
        else:
            row = base.iloc[0]

    rows = [explicar_componente_score(row, c) for c in PESOS_SCORE_INTEGRADO.keys()]
    comp = pd.DataFrame(rows)
    comp = comp.sort_values("pontos_obtidos", ascending=False).reset_index(drop=True)
    score_final = _num(row.get("score_prioridade_integrada"))
    classe_final = str(row.get("classe_prioridade", "Sem classificação"))
    municipio_nome = str(row.get("municipio", "Município")).strip() or "Município"
    top = comp.head(2)
    motivos = " e ".join(top["componente"].tolist()) if not top.empty else "componentes avaliados"
    sintese = (
        f"{municipio_nome} está classificado como {classe_final}, com score integrado de {_fmt_pontos(score_final)} de 100 pontos. "
        f"A classificação é puxada principalmente por {motivos}. "
        f"A leitura deve ser validada pela equipe APS/ERS e pelo município, especialmente quando houver pendência de georreferenciamento ou divergência territorial."
    )
    return {
        "municipio": municipio_nome,
        "score_integrado": round(score_final, 1),
        "classe_prioridade": classe_final,
        "componentes": comp,
        "sintese": sintese,
    }


def parametros_score_integrado() -> pd.DataFrame:
    rows = []
    for campo, cfg in PESOS_SCORE_INTEGRADO.items():
        rows.append({
            "componente": cfg["rotulo"],
            "campo_tecnico": campo,
            "peso_percentual": int(cfg["peso"] * 100),
            "maximo_pontos_no_score": cfg["max_pontos"],
            "escala_do_componente": "0 a 100",
            "descricao": cfg["descricao"],
        })
    return pd.DataFrame(rows)


def carregar_territorios_desassistidos(municipio: str | None = None, limite: int = 300) -> pd.DataFrame:
    """Lista territórios potencialmente desassistidos por distância até UBS/APS.

    A lista combina bairros/localidades/setores e, quando disponível, assentamentos
    rurais. A prioridade é evidenciar quem está distante, especialmente áreas rurais.
    """
    registros: list[pd.DataFrame] = []
    try:
        from services.georreferenciamento_service import (
            calcular_distancias_bairros_localidades_aps,
            montar_acesso_rural_aps,
        )
        geo = calcular_distancias_bairros_localidades_aps()
        dist = geo.get("distancias", pd.DataFrame()).copy()
        if not dist.empty:
            if "registro_utilizado_no_calculo" in dist.columns:
                dist = dist[dist["registro_utilizado_no_calculo"].astype(bool)].copy()
            if municipio and "municipio" in dist.columns:
                dist = dist[dist["municipio"].map(_chave).eq(_chave(municipio))]
            dist["tipo_analise"] = "Bairro/localidade/setor"
            dist["territorio"] = dist.get("territorio_exibicao", dist.get("bairro_ou_localidade", ""))
            cols = [c for c in [
                "municipio", "regiao_saude", "tipo_analise", "territorio", "tipo_territorio", "populacao",
                "classe_distancia_aps", "distancia_ubs_mais_proxima_km", "ubs_mais_proxima", "municipio_ubs_mais_proxima",
                "latitude", "longitude", "metodo_calculo", "municipio_textual_confere_geometria", "municipio_geografico_estimado",
                "alerta_municipio_geografico", "registro_utilizado_no_calculo", "referencia_fora_municipio",
            ] if c in dist.columns]
            registros.append(dist[cols])

        rural = montar_acesso_rural_aps()
        ass = rural.get("distancias", pd.DataFrame()).copy()
        if not ass.empty:
            if municipio and "municipio" in ass.columns:
                ass = ass[ass["municipio"].map(_chave).eq(_chave(municipio))]
            ass["tipo_analise"] = "Assentamento rural"
            ass["territorio"] = ass.get("assentamento", ass.get("nome", ""))
            if "populacao" not in ass.columns:
                ass["populacao"] = 0
            if "tipo_territorio" not in ass.columns:
                ass["tipo_territorio"] = "Assentamento rural"
            cols = [c for c in [
                "municipio", "regiao_saude", "tipo_analise", "territorio", "tipo_territorio", "populacao",
                "classe_distancia_aps", "distancia_ubs_mais_proxima_km", "ubs_mais_proxima", "municipio_ubs_mais_proxima",
                "latitude", "longitude", "metodo_calculo",
            ] if c in ass.columns]
            registros.append(ass[cols])
    except Exception:
        return pd.DataFrame()

    if not registros:
        return pd.DataFrame()
    out = pd.concat(registros, ignore_index=True, sort=False)
    if out.empty:
        return out
    if "classe_distancia_aps" not in out.columns:
        out["classe_distancia_aps"] = "Sem cálculo"
    out["distancia_ubs_mais_proxima_km"] = pd.to_numeric(out.get("distancia_ubs_mais_proxima_km"), errors="coerce").fillna(0)
    out["populacao"] = pd.to_numeric(out.get("populacao"), errors="coerce").fillna(0)
    ordem = {"Crítico": 1, "Distante": 2, "Atenção": 3, "Próximo": 4, "Sem cálculo": 9}
    out["ordem_prioridade"] = out["classe_distancia_aps"].map(ordem).fillna(9)
    out["populacao_exposta_ponderada"] = out["populacao"] * out["distancia_ubs_mais_proxima_km"]
    out = out.sort_values(["ordem_prioridade", "distancia_ubs_mais_proxima_km", "populacao_exposta_ponderada"], ascending=[True, False, False])
    return out.drop(columns=["ordem_prioridade", "populacao_exposta_ponderada"], errors="ignore").head(limite).reset_index(drop=True)

def _agregar_tabela(nome: str, chave: str = "municipio", prefixo: str = "") -> pd.DataFrame:
    df = read_table(nome)
    if df.empty or chave not in df.columns:
        return pd.DataFrame()
    df["chave_municipio"] = df[chave].map(_chave)
    out = df.groupby("chave_municipio").size().reset_index(name=f"{prefixo}qtd_registros")
    return out


def carregar_base_dashboard() -> pd.DataFrame:
    base = read_table("base_municipal_consolidada")
    if base.empty:
        municipios = read_table("municipios")
        if municipios.empty:
            return pd.DataFrame()
        base = municipios.copy()
    base = base.copy()
    base.columns = [str(c).strip() for c in base.columns]
    if "municipio" not in base.columns:
        return pd.DataFrame()
    base["chave_municipio"] = base["municipio"].map(_chave)
    base = _aplicar_total_ubs_unicas(base)
    if "codigo_ibge" in base.columns:
        base["codigo_ibge"] = base["codigo_ibge"].map(_codigo7)

    # Referências socioeconômicas recuperadas do próprio sistema antigo.
    det = _carregar_determinantes_ref()
    if not det.empty:
        base = base.merge(det.drop(columns=["codigo_ibge"], errors="ignore"), on="chave_municipio", how="left", suffixes=("", "_ref"))

    ter = _carregar_territorios_ref()
    if not ter.empty:
        base = base.merge(ter, on="chave_municipio", how="left")

    acesso = _carregar_acesso_territorial_aps()
    if not acesso.empty:
        base = base.merge(acesso, on="chave_municipio", how="left")

    # Camadas territoriais especiais.
    extras = [
        ("dados_mt_terras_indigenas", "terras_indigenas_"),
        ("dados_mt_assentamentos", "assentamentos_"),
        ("dados_mt_areas_contaminadas", "areas_contaminadas_"),
    ]
    for tabela, prefixo in extras:
        agg = _agregar_tabela(tabela, prefixo=prefixo)
        if not agg.empty:
            base = base.merge(agg, on="chave_municipio", how="left")

    # Campos numéricos mínimos.
    for c in [
        "populacao", "area_km2", "densidade_hab_km2", "total_ubs", "total_equipes_aps", "total_profissionais_aps",
        "indice_vulnerabilidade", "taxa_analfabetismo_estimado_pct", "nivel_instrucao_baixo_pct", "renda_censo_2022",
        "pct_rdpc_ate_1_4_sm_2022", "pct_rdpc_ate_1_2_sm_2022", "indice_vulnerabilidade_saneamento_2022",
        "territorios_mapeados", "indicador_pressao_aps_territorios", "percentual_baixa_renda_territorios",
        "percentual_baixa_escolaridade_territorios", "percentual_saneamento_inadequado_territorios", "territorios_pressao_alta",
        "territorios_com_distancia_aps", "populacao_referencia_territorial", "distancia_media_territorios_km",
        "distancia_mediana_territorios_km", "distancia_maxima_territorios_km", "territorios_criticos_distancia",
        "territorios_distantes_distancia", "territorios_criticos_distantes", "percentual_territorios_criticos_distantes",
        "populacao_territorios_criticos", "populacao_territorios_distantes", "populacao_territorios_criticos_distantes",
        "percentual_populacao_critica_distante", "assentamentos_com_distancia_aps", "assentamentos_criticos_distancia",
        "assentamentos_distantes_distancia", "assentamentos_criticos_distantes", "distancia_media_assentamentos_km",
        "distancia_maxima_assentamentos_km",
        "terras_indigenas_qtd_registros", "assentamentos_qtd_registros", "areas_contaminadas_qtd_registros",
        "latitude", "longitude",
    ]:
        if c not in base.columns:
            base[c] = 0
        base[c] = pd.to_numeric(base[c], errors="coerce").fillna(0)

    for codigo in CODIGOS_EQUIPES_APS:
        c = f"total_equipes_{codigo}"
        if c not in base.columns:
            base[c] = 0
        base[c] = pd.to_numeric(base[c], errors="coerce").fillna(0)

    base["populacao_por_equipe"] = _safe_div(base["populacao"], base["total_equipes_aps"]).fillna(0)
    base["populacao_por_ubs"] = _safe_div(base["populacao"], base["total_ubs"]).fillna(0)
    base["equipes_por_10mil_hab"] = _safe_div(base["total_equipes_aps"] * 10000, base["populacao"]).fillna(0)
    base["ubs_por_10mil_hab"] = _safe_div(base["total_ubs"] * 10000, base["populacao"]).fillna(0)
    base["profissionais_por_equipe"] = _safe_div(base["total_profissionais_aps"], base["total_equipes_aps"]).fillna(0)

    # Componentes de prioridade. Sempre que um dado não existe, ele não derruba o sistema.
    # A partir da Etapa 2-E, a distância territorial entra explicitamente no score.
    comp_acesso = (
        _normalizar_0_100(base["distancia_media_territorios_km"]) * 0.20
        + _normalizar_0_100(base["distancia_maxima_territorios_km"]) * 0.20
        + _normalizar_0_100(base["percentual_territorios_criticos_distantes"]) * 0.20
        + _normalizar_0_100(base["percentual_populacao_critica_distante"]) * 0.20
        + _normalizar_0_100(base["assentamentos_criticos_distantes"]) * 0.10
        + _normalizar_0_100(base["distancia_maxima_assentamentos_km"]) * 0.10
    )
    acesso_sinal = (
        base["distancia_media_territorios_km"].abs()
        + base["distancia_maxima_territorios_km"].abs()
        + base["territorios_criticos_distantes"].abs()
        + base["assentamentos_criticos_distantes"].abs()
    )
    comp_acesso = comp_acesso.where(acesso_sinal > 0, 0)
    comp_vazio = (
        _normalizar_0_100(base["populacao_por_equipe"]) * 0.45
        + _normalizar_0_100(base["populacao_por_ubs"]) * 0.35
        + _normalizar_0_100(base["indicador_pressao_aps_territorios"]) * 0.20
    )
    comp_social = (
        _normalizar_0_100(base["taxa_analfabetismo_estimado_pct"]) * 0.20
        + _normalizar_0_100(base["nivel_instrucao_baixo_pct"]) * 0.15
        + _normalizar_0_100(base["pct_rdpc_ate_1_2_sm_2022"]) * 0.20
        + _normalizar_0_100(base["indice_vulnerabilidade_saneamento_2022"]) * 0.20
        + _normalizar_0_100(base["percentual_baixa_renda_territorios"]) * 0.15
        + _normalizar_0_100(base["percentual_saneamento_inadequado_territorios"]) * 0.10
    )
    comp_equidade = (
        _normalizar_0_100(base["terras_indigenas_qtd_registros"]) * 0.30
        + _normalizar_0_100(base["assentamentos_qtd_registros"]) * 0.20
        + _normalizar_0_100(base["areas_contaminadas_qtd_registros"]) * 0.10
        + _normalizar_0_100(base["territorios_pressao_alta"]) * 0.20
        + _normalizar_0_100(base["assentamentos_criticos_distantes"]) * 0.20
    )
    comp_capacidade = (
        _normalizar_0_100(base["equipes_por_10mil_hab"], inverter=True) * 0.55
        + _normalizar_0_100(base["ubs_por_10mil_hab"], inverter=True) * 0.25
        + _normalizar_0_100(base["profissionais_por_equipe"], inverter=True) * 0.20
    )
    base["score_acesso_territorial"] = comp_acesso.round(1)
    base["score_vazio_assistencial"] = comp_vazio.round(1)
    base["score_vulnerabilidade_social"] = comp_social.round(1)
    base["score_equidade_territorial"] = comp_equidade.round(1)
    base["score_fragilidade_capacidade"] = comp_capacidade.round(1)
    base["score_prioridade_integrada"] = (
        comp_acesso * 0.30 + comp_vazio * 0.20 + comp_social * 0.20 + comp_capacidade * 0.20 + comp_equidade * 0.10
    ).round(1)

    # Classificação relativa estadual.
    # Em painéis de inteligência territorial, usar apenas cortes absolutos pode esconder
    # prioridades quando a base estadual tem distribuição comprimida. A classificação
    # abaixo preserva o score, mas organiza a fila de decisão por posição relativa em MT.
    base = base.sort_values("score_prioridade_integrada", ascending=False).reset_index(drop=True)
    n = len(base)
    if n:
        base["posicao_prioridade"] = range(1, n + 1)
        base["percentil_prioridade"] = (100 - ((base["posicao_prioridade"] - 1) / max(n - 1, 1) * 100)).round(1)
        corte_critico = max(1, math.ceil(n * 0.10))
        corte_alto = max(corte_critico + 1, math.ceil(n * 0.30))
        corte_intensivo = max(corte_alto + 1, math.ceil(n * 0.60))

        def _classe_relativa(posicao: int) -> str:
            if posicao <= corte_critico:
                return "Prioridade crítica"
            if posicao <= corte_alto:
                return "Alta prioridade"
            if posicao <= corte_intensivo:
                return "Monitoramento intensivo"
            return "Monitoramento regular"

        base["classe_prioridade"] = base["posicao_prioridade"].map(_classe_relativa)
    else:
        base["posicao_prioridade"] = 0
        base["percentil_prioridade"] = 0
        base["classe_prioridade"] = "Monitoramento regular"

    base["qualidade_dados_score"] = base.apply(_qualidade_dados_score, axis=1).round(1)
    base["classe_qualidade_dados"] = base["qualidade_dados_score"].map(_classe_qualidade_dados)
    base["alerta_acao"] = base.apply(_alerta_acao, axis=1)
    base["fatores_prioritarios"] = base.apply(_fatores_prioritarios, axis=1)
    base["principal_motivo_prioridade"] = base.apply(_principal_motivo_prioridade, axis=1)
    base["acao_sugerida"] = base.apply(_acao_sugerida, axis=1)
    base["validacao_recomendada"] = base.apply(_validacao_recomendada, axis=1)
    return base


def _qualidade_dados_score(row: pd.Series) -> float:
    pontos = 0.0
    total = 0.0
    criterios = [
        ("populacao", 15),
        ("total_ubs", 15),
        ("total_equipes_aps", 20),
        ("total_profissionais_aps", 15),
        ("territorios_mapeados", 10),
        ("territorios_com_distancia_aps", 10),
        ("distancia_media_territorios_km", 5),
        ("taxa_analfabetismo_estimado_pct", 5),
        ("pct_rdpc_ate_1_2_sm_2022", 5),
        ("indice_vulnerabilidade_saneamento_2022", 5),
        ("latitude", 2.5),
        ("longitude", 2.5),
    ]
    for campo, peso in criterios:
        total += peso
        if abs(_num(row.get(campo))) > 0:
            pontos += peso
    return (pontos / total * 100) if total else 0


def _classe_qualidade_dados(v: float) -> str:
    v = _num(v)
    if v >= 80:
        return "Boa cobertura técnica"
    if v >= 60:
        return "Cobertura parcial"
    if v >= 40:
        return "Requer validação"
    return "Base frágil"


def _fatores_prioritarios(row: pd.Series) -> str:
    fatores = []
    componentes = [
        ("acesso territorial à UBS", _num(row.get("score_acesso_territorial"))),
        ("vazio assistencial", _num(row.get("score_vazio_assistencial"))),
        ("fragilidade de capacidade", _num(row.get("score_fragilidade_capacidade"))),
        ("vulnerabilidade social", _num(row.get("score_vulnerabilidade_social"))),
        ("equidade territorial", _num(row.get("score_equidade_territorial"))),
    ]
    for nome, valor in sorted(componentes, key=lambda x: x[1], reverse=True)[:2]:
        if valor >= 45:
            fatores.append(f"{nome} ({valor:.1f})")
    if _num(row.get("populacao_por_equipe")) > 4000:
        fatores.append("população/equipe elevada")
    if _num(row.get("populacao_por_ubs")) > 12000:
        fatores.append("população/UBS elevada")
    if _num(row.get("territorios_pressao_alta")) > 0:
        fatores.append("territórios com pressão APS")
    if _num(row.get("territorios_criticos_distantes")) > 0:
        fatores.append("territórios distantes da UBS")
    if _num(row.get("assentamentos_criticos_distantes")) > 0:
        fatores.append("assentamentos rurais distantes")
    return "; ".join(fatores[:5]) if fatores else "monitoramento regular"


def _alerta_acao(row: pd.Series) -> str:
    alertas = []
    if _num(row.get("total_equipes_aps")) <= 0:
        alertas.append("sem equipe APS na base")
    elif _num(row.get("populacao_por_equipe")) > 4000:
        alertas.append("população/equipe elevada")
    if _num(row.get("total_ubs")) <= 0:
        alertas.append("sem UBS na base")
    elif _num(row.get("populacao_por_ubs")) > 12000:
        alertas.append("pressão populacional por UBS")
    if _num(row.get("territorios_criticos_distantes")) > 0:
        alertas.append("bairros/localidades/setores distantes da UBS")
    if _num(row.get("assentamentos_criticos_distantes")) > 0:
        alertas.append("assentamentos rurais com acesso crítico/distante")
    if _num(row.get("indicador_pressao_aps_territorios")) >= 70:
        alertas.append("territórios com pressão APS alta")
    if _num(row.get("taxa_analfabetismo_estimado_pct")) > 10:
        alertas.append("vulnerabilidade educacional")
    if _num(row.get("pct_rdpc_ate_1_2_sm_2022")) > 35 or _num(row.get("percentual_baixa_renda_territorios")) > 35:
        alertas.append("baixa renda relevante")
    if _num(row.get("terras_indigenas_qtd_registros")) > 0:
        alertas.append("território indígena/interseção")
    if _num(row.get("assentamentos_qtd_registros")) > 0:
        alertas.append("assentamentos rurais")
    return "; ".join(alertas) if alertas else "sem alerta crítico nos critérios atuais"



def _principal_motivo_prioridade(row: pd.Series) -> str:
    componentes = [
        ("Acesso territorial à UBS", _num(row.get("score_acesso_territorial"))),
        ("Vazio assistencial", _num(row.get("score_vazio_assistencial"))),
        ("Fragilidade da capacidade instalada", _num(row.get("score_fragilidade_capacidade"))),
        ("Vulnerabilidade social", _num(row.get("score_vulnerabilidade_social"))),
        ("Equidade territorial", _num(row.get("score_equidade_territorial"))),
    ]
    nome, valor = max(componentes, key=lambda item: item[1])
    if valor < 35:
        return "Prioridade relativa no ranking estadual"
    return f"{nome} é o componente predominante ({valor:.1f})"


def _acao_sugerida(row: pd.Series) -> str:
    if _num(row.get("total_equipes_aps")) <= 0:
        return "Validar CNES/INE e verificar necessidade de implantação/regularização de equipe APS."
    if _num(row.get("total_ubs")) <= 0:
        return "Validar base de estabelecimentos e avaliar acesso físico à APS no território."

    acesso = _num(row.get("score_acesso_territorial"))
    vazio = _num(row.get("score_vazio_assistencial"))
    capacidade = _num(row.get("score_fragilidade_capacidade"))
    social = _num(row.get("score_vulnerabilidade_social"))
    equidade = _num(row.get("score_equidade_territorial"))
    pop_equipe = _num(row.get("populacao_por_equipe"))
    pop_ubs = _num(row.get("populacao_por_ubs"))

    if acesso >= 60 or _num(row.get("assentamentos_criticos_distantes")) > 0:
        return "Priorizar validação territorial do acesso à UBS, com foco em zonas rurais, assentamentos, rotas de atendimento, unidade volante e reorganização da referência APS."
    if vazio >= 60 and capacidade >= 55:
        return "Priorizar estudo de expansão/reorganização de equipes e validação de vazios intramunicipais."
    if pop_equipe > 4000:
        return "Avaliar ampliação ou redistribuição de equipes APS, com checagem da adscrição e cobertura real."
    if pop_ubs > 12000:
        return "Avaliar pressão sobre UBS, necessidade de qualificação da rede física ou nova unidade conforme estudo técnico."
    if social >= 60:
        return "Articular APS com vulnerabilidade social, CadÚnico, assistência social e ações territoriais intersetoriais."
    if equidade >= 55:
        return "Validar territórios especiais com ERS/município e planejar resposta diferenciada de acesso."
    return "Manter monitoramento regional e validar dados sensíveis antes de decisão administrativa."


def _validacao_recomendada(row: pd.Series) -> str:
    pendencias = []
    if _num(row.get("qualidade_dados_score")) < 60:
        pendencias.append("qualidade dos dados")
    if _num(row.get("territorios_mapeados")) <= 0:
        pendencias.append("territórios intramunicipais")
    if _num(row.get("territorios_com_distancia_aps")) <= 0:
        pendencias.append("distância territorial até UBS")
    if _num(row.get("latitude")) == 0 or _num(row.get("longitude")) == 0:
        pendencias.append("coordenadas municipais")
    if _num(row.get("total_profissionais_aps")) <= 0:
        pendencias.append("profissionais CNES/INE")
    if not pendencias:
        return "Sem pendência técnica crítica nos campos atuais."
    return "Validar: " + ", ".join(pendencias[:4]) + "."

def resumo_estadual(df: pd.DataFrame) -> dict[str, float]:
    if df.empty:
        return {}
    return {
        "municipios": df["municipio"].nunique(),
        "populacao": df["populacao"].sum(),
        "ubs": df["total_ubs"].sum(),
        "equipes": df["total_equipes_aps"].sum(),
        "profissionais": df["total_profissionais_aps"].sum(),
        "prioridade_critica": (df["classe_prioridade"] == "Prioridade crítica").sum(),
        "alta_prioridade": (df["classe_prioridade"] == "Alta prioridade").sum(),
        "pop_por_equipe": df["populacao"].sum() / df["total_equipes_aps"].sum() if df["total_equipes_aps"].sum() else 0,
        "pop_por_ubs": df["populacao"].sum() / df["total_ubs"].sum() if df["total_ubs"].sum() else 0,
    }


def resumo_regional_dashboard(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "regiao_saude" not in df.columns:
        return pd.DataFrame()
    tmp = df.copy()
    tmp["mun_prioritario"] = tmp["classe_prioridade"].isin(["Prioridade crítica", "Alta prioridade"]).astype(int)
    reg = tmp.groupby("regiao_saude", dropna=False).agg(
        municipios=("municipio", "nunique"),
        populacao=("populacao", "sum"),
        ubs=("total_ubs", "sum"),
        equipes=("total_equipes_aps", "sum"),
        profissionais=("total_profissionais_aps", "sum"),
        municipios_prioritarios=("mun_prioritario", "sum"),
        score_medio=("score_prioridade_integrada", "mean"),
        acesso_medio=("score_acesso_territorial", "mean"),
        vazio_medio=("score_vazio_assistencial", "mean"),
        vulnerabilidade_media=("score_vulnerabilidade_social", "mean"),
        equidade_media=("score_equidade_territorial", "mean"),
    ).reset_index()
    reg["populacao_por_equipe"] = _safe_div(reg["populacao"], reg["equipes"]).fillna(0).round(1)
    reg["populacao_por_ubs"] = _safe_div(reg["populacao"], reg["ubs"]).fillna(0).round(1)
    reg["score_medio"] = reg["score_medio"].round(1)
    return reg.sort_values(["score_medio", "municipios_prioritarios"], ascending=False)




def construir_carteira_intervencoes_aps(df: pd.DataFrame) -> pd.DataFrame:
    """Monta uma carteira gerencial de ações a partir do score integrado.

    A função não altera a base nem cria obrigação normativa. Ela organiza
    encaminhamentos preliminares para apoiar reunião técnica, pactuação regional
    e validação com ERS/município.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    rows = []
    base = df.copy()
    for _, row in base.iterrows():
        acesso = _num(row.get("score_acesso_territorial"))
        pressao = _num(row.get("score_vazio_assistencial"))
        capacidade = _num(row.get("score_fragilidade_capacidade"))
        social = _num(row.get("score_vulnerabilidade_social"))
        equidade = _num(row.get("score_equidade_territorial"))
        score = _num(row.get("score_prioridade_integrada"))
        dist_max = _num(row.get("distancia_maxima_territorios_km"))
        dist_media = _num(row.get("distancia_media_territorios_km"))
        territorios = _num(row.get("territorios_criticos_distantes"))
        assentamentos = _num(row.get("assentamentos_criticos_distantes"))
        pop_exposta = _num(row.get("populacao_territorios_criticos_distantes"))
        pop_equipe = _num(row.get("populacao_por_equipe"))
        pop_ubs = _num(row.get("populacao_por_ubs"))

        acoes = []
        if acesso >= 60 or territorios > 0 or assentamentos > 0 or dist_max >= 15:
            acoes.append({
                "eixo_intervencao": "Acesso territorial e zona rural",
                "tipo_acao": "Validar territórios desassistidos e organizar resposta territorial",
                "acao_recomendada": "Mapear rotas reais, validar comunidades distantes com ERS/município e avaliar unidade volante, agenda itinerante, transporte sanitário ou reorganização da referência APS.",
                "evidencia": f"{int(territorios)} territórios críticos/distantes; {int(assentamentos)} assentamentos em alerta; distância máxima {dist_max:.1f} km.",
                "urgencia": "Muito alta" if score >= 80 or assentamentos > 0 else "Alta",
                "prazo_sugerido": "0 a 30 dias",
            })
        if pressao >= 55 or pop_equipe > 4000:
            acoes.append({
                "eixo_intervencao": "Cobertura e equipes APS",
                "tipo_acao": "Reavaliar suficiência de equipes e adscrição territorial",
                "acao_recomendada": "Conferir CNES/INE, população adscrita, áreas descobertas e possibilidade de ampliação, redistribuição ou qualificação de equipes APS.",
                "evidencia": f"População/equipe: {pop_equipe:.1f}; score de pressão assistencial: {pressao:.1f}.",
                "urgencia": "Alta" if pressao >= 70 or pop_equipe > 5000 else "Média",
                "prazo_sugerido": "30 a 60 dias",
            })
        if capacidade >= 60 or pop_ubs > 12000:
            acoes.append({
                "eixo_intervencao": "Capacidade instalada",
                "tipo_acao": "Avaliar rede física, UBS de referência e capacidade operacional",
                "acao_recomendada": "Verificar distribuição das UBS, capacidade de atendimento, estrutura física, localização e necessidade de qualificação, expansão ou nova unidade conforme estudo técnico.",
                "evidencia": f"População/UBS: {pop_ubs:.1f}; score de fragilidade de capacidade: {capacidade:.1f}.",
                "urgencia": "Alta" if capacidade >= 75 else "Média",
                "prazo_sugerido": "60 a 120 dias",
            })
        if social >= 60:
            acoes.append({
                "eixo_intervencao": "Vulnerabilidade social",
                "tipo_acao": "Integrar APS com busca ativa e ações intersetoriais",
                "acao_recomendada": "Cruzar CadÚnico, Bolsa Família/BPC e determinantes sociais com a agenda da APS, priorizando busca ativa, educação em saúde e comunicação territorializada.",
                "evidencia": f"Score de vulnerabilidade social: {social:.1f}.",
                "urgencia": "Média",
                "prazo_sugerido": "60 a 120 dias",
            })
        if equidade >= 55:
            acoes.append({
                "eixo_intervencao": "Equidade e territórios especiais",
                "tipo_acao": "Validar resposta diferenciada para territórios especiais",
                "acao_recomendada": "Verificar presença de assentamentos, terras indígenas, comunidades rurais e outros territórios que demandem arranjo específico de acesso à APS.",
                "evidencia": f"Score de equidade territorial: {equidade:.1f}; distância média territorial {dist_media:.1f} km.",
                "urgencia": "Alta" if assentamentos > 0 else "Média",
                "prazo_sugerido": "30 a 90 dias",
            })

        if not acoes:
            acoes.append({
                "eixo_intervencao": "Monitoramento",
                "tipo_acao": "Manter acompanhamento regional",
                "acao_recomendada": "Manter município em monitoramento, validando dados de CNES, equipes, UBS e territórios antes de propor intervenção administrativa.",
                "evidencia": f"Score integrado {score:.1f}; sem gatilho crítico nos critérios atuais.",
                "urgencia": "Baixa",
                "prazo_sugerido": "Monitoramento contínuo",
            })

        for acao in acoes:
            rows.append({
                "municipio": row.get("municipio", ""),
                "regiao_saude": row.get("regiao_saude", ""),
                "classe_prioridade": row.get("classe_prioridade", ""),
                "score_prioridade_integrada": round(score, 1),
                "populacao": _num(row.get("populacao")),
                "territorios_criticos_distantes": int(territorios),
                "populacao_territorios_criticos_distantes": int(pop_exposta),
                "assentamentos_criticos_distantes": int(assentamentos),
                "distancia_maxima_territorios_km": round(dist_max, 1),
                "principal_motivo_prioridade": row.get("principal_motivo_prioridade", ""),
                **acao,
            })

    carteira = pd.DataFrame(rows)
    if carteira.empty:
        return carteira
    ordem_urgencia = {"Muito alta": 1, "Alta": 2, "Média": 3, "Baixa": 4}
    carteira["ordem_urgencia"] = carteira["urgencia"].map(ordem_urgencia).fillna(9)
    carteira = carteira.sort_values(
        ["ordem_urgencia", "score_prioridade_integrada", "territorios_criticos_distantes", "assentamentos_criticos_distantes"],
        ascending=[True, False, False, False],
    ).drop(columns=["ordem_urgencia"])
    return carteira.reset_index(drop=True)

def carregar_territorios_prioritarios(municipio: str | None = None, limite: int = 200) -> pd.DataFrame:
    ter = _ler_csv_flex(ARQ_TERRITORIOS)
    if ter.empty:
        return pd.DataFrame()
    ter["chave_municipio"] = ter.get("municipio", "").astype(str).str.replace(" - MT", "", regex=False).map(_chave)
    if municipio:
        ter = ter[ter["chave_municipio"].eq(_chave(municipio))]
    for c in ["populacao", "latitude", "longitude", "indicador_pressao_aps", "percentual_baixa_renda", "percentual_baixa_escolaridade", "percentual_saneamento_inadequado", "renda_media"]:
        if c not in ter.columns:
            ter[c] = 0
        ter[c] = pd.to_numeric(ter[c], errors="coerce").fillna(0)
    if "bairro_ou_localidade" not in ter.columns:
        ter["bairro_ou_localidade"] = ter.get("setor_censitario", "")
    cols = [
        "municipio", "bairro_ou_localidade", "tipo_territorio", "populacao", "indicador_pressao_aps",
        "percentual_baixa_renda", "percentual_baixa_escolaridade", "percentual_saneamento_inadequado", "renda_media", "latitude", "longitude",
    ]
    cols = [c for c in cols if c in ter.columns]
    return ter.sort_values("indicador_pressao_aps", ascending=False)[cols].head(limite)


def carregar_unidades_municipio(municipio: str | None = None) -> pd.DataFrame:
    ubs = read_table("estabelecimentos_saude")
    if ubs.empty:
        ubs = _ler_csv_flex(ARQ_UBS)
    if ubs.empty:
        return pd.DataFrame()
    ubs = deduplicar_estabelecimentos_saude(ubs)
    if municipio and "municipio" in ubs.columns:
        ubs = ubs[ubs["municipio"].map(_chave).eq(_chave(municipio))]
    for c in ["latitude", "longitude"]:
        if c in ubs.columns:
            ubs[c] = pd.to_numeric(ubs[c], errors="coerce")
    return ubs.drop(columns=["cnes_norm"], errors="ignore").reset_index(drop=True)


def carregar_equipes_municipio(municipio: str | None = None) -> pd.DataFrame:
    equipes = read_table("equipes_aps")
    if equipes.empty:
        return pd.DataFrame()
    if municipio and "municipio" in equipes.columns:
        equipes = equipes[equipes["municipio"].map(_chave).eq(_chave(municipio))]
    if "codigo_tipo_equipe" in equipes.columns:
        equipes["codigo_tipo_equipe"] = equipes["codigo_tipo_equipe"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(2).str[-2:]
    return equipes


def matriz_equipes_por_codigo(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    rows = []
    for cod, desc in CODIGOS_EQUIPES_APS.items():
        col = f"total_equipes_{cod}"
        total = pd.to_numeric(df.get(col, 0), errors="coerce").fillna(0).sum() if col in df.columns else 0
        rows.append({"codigo": cod, "tipo_equipe": desc, "total_equipes": int(total)})
    return pd.DataFrame(rows)


def _perfil_municipal_aps(row: pd.Series) -> tuple[str, str, str]:
    """Classifica o município por perfil analítico predominante.

    A tipologia é gerencial e usa apenas os campos já produzidos pelo sistema.
    Ela ajuda a transformar ranking em leitura de problema: distância, equipe,
    capacidade, vulnerabilidade, equidade ou qualidade dos dados.
    """
    acesso = _num(row.get("score_acesso_territorial"))
    pressao = _num(row.get("score_vazio_assistencial"))
    capacidade = _num(row.get("score_fragilidade_capacidade"))
    social = _num(row.get("score_vulnerabilidade_social"))
    equidade = _num(row.get("score_equidade_territorial"))
    qualidade = _num(row.get("qualidade_dados_score"), 100)
    dist_max = _num(row.get("distancia_maxima_territorios_km"))
    terr = _num(row.get("territorios_criticos_distantes"))
    ass = _num(row.get("assentamentos_criticos_distantes"))
    pop_equipe = _num(row.get("populacao_por_equipe"))
    pop_ubs = _num(row.get("populacao_por_ubs"))

    if qualidade < 45:
        return (
            "Dados insuficientes para decisão",
            "Baixa qualidade ou ausência de campos-chave limita a leitura do município.",
            "Priorizar validação CNES, georreferenciamento, territorialização e base municipal antes de decisão administrativa.",
        )
    if acesso >= 60 or dist_max >= 15 or ass > 0:
        return (
            "Vazio territorial rural / acesso crítico",
            "Distância territorial elevada, territórios críticos ou assentamentos distantes indicam possível desassistência física.",
            "Validar rotas reais, comunidades rurais e referência APS; avaliar equipe volante, agenda itinerante, transporte sanitário, unidade de apoio ou nova UBS conforme estudo locacional.",
        )
    if pressao >= 60 or pop_equipe > 4500:
        return (
            "Pressão populacional sobre a APS",
            "A população por equipe/UBS ou a pressão APS nos territórios sugere sobrecarga assistencial.",
            "Validar adscrição, cobertura efetiva e suficiência de equipes; avaliar ampliação, redistribuição ou qualificação da cobertura APS.",
        )
    if capacidade >= 60 or pop_ubs > 12000:
        return (
            "Fragilidade de capacidade instalada",
            "A relação entre população, UBS, equipes e profissionais sugere menor capacidade operacional relativa.",
            "Avaliar estrutura física, distribuição de UBS, composição de equipes e capacidade operacional antes de definir expansão ou qualificação da rede.",
        )
    if social >= 60:
        return (
            "Vulnerabilidade social elevada",
            "Determinantes sociais indicam maior risco social associado à APS.",
            "Articular APS, vigilância, assistência social, busca ativa, educação em saúde e comunicação territorializada.",
        )
    if equidade >= 55 or terr > 0:
        return (
            "Território especial / equidade territorial",
            "Camadas de assentamentos, terras indígenas, áreas especiais ou territórios com pressão exigem resposta diferenciada.",
            "Validar a realidade local com ERS/município e planejar arranjos diferenciados de acesso, vínculo e cuidado.",
        )
    return (
        "Situação relativamente equilibrada / monitoramento",
        "Os indicadores atuais não apontam predominância crítica em uma dimensão específica.",
        "Manter monitoramento regional, atualizar bases e validar mudanças no CNES, população, UBS e territórios.",
    )


def _calcular_desequilibrio_intramunicipal(row: pd.Series) -> tuple[float, str]:
    dist_max = _num(row.get("distancia_maxima_territorios_km"))
    dist_media = _num(row.get("distancia_media_territorios_km"))
    pct_territ = _num(row.get("percentual_territorios_criticos_distantes"))
    pct_pop = _num(row.get("percentual_populacao_critica_distante"))
    terr = _num(row.get("territorios_criticos_distantes"))
    ass = _num(row.get("assentamentos_criticos_distantes"))

    score = 0.0
    score += min(dist_max / 25 * 30, 30)
    score += min(dist_media / 10 * 15, 15)
    score += min(pct_territ * 0.25, 25)
    score += min(pct_pop * 0.20, 20)
    score += 10 if ass > 0 else min(terr * 1.5, 10)
    score = round(min(score, 100), 1)

    if score >= 70:
        classe = "Desequilíbrio intramunicipal crítico"
    elif score >= 50:
        classe = "Desequilíbrio intramunicipal alto"
    elif score >= 30:
        classe = "Desequilíbrio intramunicipal moderado"
    elif score > 0:
        classe = "Desequilíbrio intramunicipal baixo"
    else:
        classe = "Sem sinal territorial suficiente"
    return score, classe


def construir_perfis_alertas_aps(df: pd.DataFrame) -> pd.DataFrame:
    """Constrói a camada final de perfis, alertas ocultos e matriz problema-resposta.

    Esta função usa somente dados já disponíveis no dashboard. Não consulta novas APIs,
    não altera banco e não muda o score principal. A finalidade é fechar a primeira
    fase analítica com uma leitura qualitativa estruturada dos municípios.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    registros = []
    for _, row in df.copy().iterrows():
        perfil, justificativa, resposta = _perfil_municipal_aps(row)
        desequilibrio_score, desequilibrio_classe = _calcular_desequilibrio_intramunicipal(row)

        score = _num(row.get("score_prioridade_integrada"))
        classe_prioridade = str(row.get("classe_prioridade", ""))
        qualidade = _num(row.get("qualidade_dados_score"), 100)
        dist_max = _num(row.get("distancia_maxima_territorios_km"))
        terr = _num(row.get("territorios_criticos_distantes"))
        ass = _num(row.get("assentamentos_criticos_distantes"))
        pop_exposta = _num(row.get("populacao_territorios_criticos_distantes"))
        acesso = _num(row.get("score_acesso_territorial"))
        pressao = _num(row.get("score_vazio_assistencial"))
        capacidade = _num(row.get("score_fragilidade_capacidade"))
        social = _num(row.get("score_vulnerabilidade_social"))
        equidade = _num(row.get("score_equidade_territorial"))

        eh_prioritario = classe_prioridade in {"Prioridade crítica", "Alta prioridade"}
        gatilho_oculto = (
            (not eh_prioritario)
            and (
                desequilibrio_score >= 55
                or dist_max >= 15
                or terr >= 3
                or ass > 0
                or acesso >= 65
                or qualidade < 50
            )
        )

        sinais = []
        if dist_max >= 15:
            sinais.append(f"distância máxima {dist_max:.1f} km")
        if terr > 0:
            sinais.append(f"{int(terr)} território(s) crítico(s)/distante(s)")
        if ass > 0:
            sinais.append(f"{int(ass)} assentamento(s) em alerta")
        if pop_exposta > 0:
            sinais.append(f"{int(pop_exposta):,} pessoas em áreas críticas".replace(",", "."))
        if qualidade < 60:
            sinais.append("qualidade dos dados requer validação")
        if not sinais:
            sinais.append("sem gatilho oculto relevante nos dados atuais")

        componente_dominante = max(
            [
                ("Acesso territorial", acesso),
                ("Pressão assistencial", pressao),
                ("Fragilidade de capacidade", capacidade),
                ("Vulnerabilidade social", social),
                ("Equidade territorial", equidade),
            ],
            key=lambda x: x[1],
        )[0]

        registros.append({
            "municipio": row.get("municipio", ""),
            "regiao_saude": row.get("regiao_saude", ""),
            "populacao": _num(row.get("populacao")),
            "classe_prioridade": classe_prioridade,
            "score_prioridade_integrada": round(score, 1),
            "posicao_prioridade": int(_num(row.get("posicao_prioridade"))),
            "perfil_municipal_aps": perfil,
            "componente_dominante": componente_dominante,
            "justificativa_perfil": justificativa,
            "resposta_recomendada": resposta,
            "desequilibrio_intramunicipal_score": desequilibrio_score,
            "classe_desequilibrio_intramunicipal": desequilibrio_classe,
            "risco_subestimado": "Sim" if gatilho_oculto else "Não",
            "sinais_alerta_oculto": "; ".join(sinais),
            "territorios_criticos_distantes": int(terr),
            "populacao_territorios_criticos_distantes": int(pop_exposta),
            "assentamentos_criticos_distantes": int(ass),
            "distancia_maxima_territorios_km": round(dist_max, 1),
            "score_acesso_territorial": round(acesso, 1),
            "score_vazio_assistencial": round(pressao, 1),
            "score_fragilidade_capacidade": round(capacidade, 1),
            "score_vulnerabilidade_social": round(social, 1),
            "score_equidade_territorial": round(equidade, 1),
            "qualidade_dados_score": round(qualidade, 1),
            "validacao_recomendada": row.get("validacao_recomendada", ""),
        })

    out = pd.DataFrame(registros)
    if out.empty:
        return out
    out["ordem_risco_subestimado"] = out["risco_subestimado"].map({"Sim": 0, "Não": 1}).fillna(1)
    out = out.sort_values(
        ["ordem_risco_subestimado", "desequilibrio_intramunicipal_score", "score_prioridade_integrada"],
        ascending=[True, False, False],
    ).drop(columns=["ordem_risco_subestimado"])
    return out.reset_index(drop=True)
