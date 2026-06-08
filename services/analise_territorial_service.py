
from __future__ import annotations

import math
import unicodedata
from typing import Dict, List

import pandas as pd

from database.queries import read_table


def _num_series(s: pd.Series, default: float = 0.0) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(default)


def _safe_div(numerador, denominador):
    num = _num_series(pd.Series(numerador)) if not isinstance(numerador, pd.Series) else _num_series(numerador)
    den = _num_series(pd.Series(denominador)) if not isinstance(denominador, pd.Series) else _num_series(denominador)
    return num.divide(den.where(den != 0)).replace([float("inf"), -float("inf")], pd.NA)


def _normalizar_texto(valor: object) -> str:
    if valor is None:
        return ""
    texto = str(valor).strip().upper()
    texto = texto.replace("- MT", "").replace(", MT", "").replace("/MT", "")
    texto = " ".join(texto.split())
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return texto


def _municipios_base() -> pd.DataFrame:
    base = read_table("base_municipal_consolidada")
    if base.empty:
        base = read_table("municipios")
    if base.empty:
        return pd.DataFrame(columns=["codigo_ibge", "municipio"])

    colunas = [c for c in ["codigo_ibge", "municipio"] if c in base.columns]
    out = base[colunas].drop_duplicates().copy()
    out["codigo_ibge"] = out["codigo_ibge"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(7)
    out["municipio"] = out["municipio"].astype(str)
    out["municipio_norm"] = out["municipio"].map(_normalizar_texto)
    return out


def _agregar_assentamentos(municipios_ref: pd.DataFrame) -> pd.DataFrame:
    df = read_table("dados_mt_assentamentos")
    if df.empty:
        return pd.DataFrame(columns=["codigo_ibge", "qtd_assentamentos", "area_assentamentos_ha"])

    out = df.copy()
    if "codigo_ibge" not in out.columns:
        out["codigo_ibge"] = ""
    out["codigo_ibge"] = out["codigo_ibge"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(7)
    out.loc[out["codigo_ibge"].isin(["0000000", ""]), "codigo_ibge"] = pd.NA

    if out["codigo_ibge"].isna().any() and "municipio" in out.columns and not municipios_ref.empty:
        mapa = dict(zip(municipios_ref["municipio_norm"], municipios_ref["codigo_ibge"]))
        out["municipio_norm"] = out["municipio"].map(_normalizar_texto)
        out["codigo_ibge"] = out["codigo_ibge"].fillna(out["municipio_norm"].map(mapa))

    area_col = "area_ha" if "area_ha" in out.columns else None
    out["area_ha_num"] = _num_series(out[area_col]) if area_col else 0

    agg = out.dropna(subset=["codigo_ibge"]).groupby("codigo_ibge", dropna=False).agg(
        qtd_assentamentos=("id", "count"),
        area_assentamentos_ha=("area_ha_num", "sum"),
    ).reset_index()
    return agg


def _agregar_terras_indigenas(municipios_ref: pd.DataFrame) -> pd.DataFrame:
    df = read_table("dados_mt_terras_indigenas")
    if df.empty:
        return pd.DataFrame(columns=["codigo_ibge", "qtd_terras_indigenas", "nomes_terras_indigenas"])

    registros: List[Dict[str, str]] = []
    mapa_nome = {}
    if not municipios_ref.empty:
        mapa_nome = dict(zip(municipios_ref["municipio_norm"], municipios_ref["codigo_ibge"]))

    for _, row in df.iterrows():
        nome_ti = str(row.get("nome_terra_indigena", "") or "").strip()
        municipios_inter = str(row.get("municipios_intersectados", "") or "").strip()
        cod_ref = str(row.get("codigo_ibge", "") or "").strip()
        cod_ref = "".join(ch for ch in cod_ref if ch.isdigit()).zfill(7) if cod_ref else ""

        codigos = set()
        if municipios_inter:
            partes = [p.strip() for p in municipios_inter.replace(",", ";").split(";") if p.strip()]
            for parte in partes:
                cod = mapa_nome.get(_normalizar_texto(parte))
                if cod:
                    codigos.add(cod)
        if not codigos and cod_ref and cod_ref != "0000000":
            codigos.add(cod_ref)

        for cod in codigos:
            registros.append({"codigo_ibge": cod, "nome_terra_indigena": nome_ti})

    if not registros:
        return pd.DataFrame(columns=["codigo_ibge", "qtd_terras_indigenas", "nomes_terras_indigenas"])

    expanded = pd.DataFrame(registros).drop_duplicates()
    agg = expanded.groupby("codigo_ibge", dropna=False).agg(
        qtd_terras_indigenas=("nome_terra_indigena", "nunique"),
        nomes_terras_indigenas=("nome_terra_indigena", lambda s: "; ".join(sorted([x for x in s.dropna().astype(str).unique() if x])[:8])),
    ).reset_index()
    return agg


def _agregar_ocorrencias_ambientais(municipios_ref: pd.DataFrame) -> pd.DataFrame:
    df = read_table("dados_mt_areas_contaminadas")
    if df.empty:
        return pd.DataFrame(columns=["codigo_ibge", "qtd_ocorrencias_ambientais", "ultimo_ano_ocorrencia_ambiental"])

    out = df.copy()
    if "codigo_ibge" not in out.columns:
        out["codigo_ibge"] = ""
    out["codigo_ibge"] = out["codigo_ibge"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(7)
    out.loc[out["codigo_ibge"].isin(["0000000", ""]), "codigo_ibge"] = pd.NA

    if out["codigo_ibge"].isna().any() and "municipio" in out.columns and not municipios_ref.empty:
        mapa = dict(zip(municipios_ref["municipio_norm"], municipios_ref["codigo_ibge"]))
        out["municipio_norm"] = out["municipio"].map(_normalizar_texto)
        out["codigo_ibge"] = out["codigo_ibge"].fillna(out["municipio_norm"].map(mapa))

    out["ano_num"] = _num_series(out["ano"]) if "ano" in out.columns else 0
    out["produto_residuo"] = out.get("produto_residuo", pd.Series([""] * len(out))).fillna("").astype(str)

    agg = out.dropna(subset=["codigo_ibge"]).groupby("codigo_ibge", dropna=False).agg(
        qtd_ocorrencias_ambientais=("id", "count"),
        ultimo_ano_ocorrencia_ambiental=("ano_num", "max"),
        principais_produtos_ambientais=("produto_residuo", lambda s: "; ".join([x for x in s.value_counts().head(4).index.astype(str) if x.strip()])),
    ).reset_index()
    return agg


def _norm_01(s: pd.Series, invert: bool = False) -> pd.Series:
    s = _num_series(s)
    if s.max() == s.min():
        out = pd.Series([0.0] * len(s), index=s.index)
    else:
        out = (s - s.min()) / (s.max() - s.min())
    if invert:
        out = 1 - out
    return out.fillna(0).clip(0, 1)


def _classificar_prioridade(score: float) -> str:
    try:
        score = float(score)
    except Exception:
        score = 0
    if score >= 70:
        return "Muito alta"
    if score >= 50:
        return "Alta"
    if score >= 30:
        return "Média"
    return "Monitoramento"


def _gerar_alertas(row: pd.Series) -> str:
    alertas = []
    if float(row.get("total_equipes_aps", 0) or 0) <= 0:
        alertas.append("sem equipes APS na base")
    elif float(row.get("populacao_por_equipe", 0) or 0) > 4000:
        alertas.append("população por equipe elevada")

    if float(row.get("total_ubs", 0) or 0) <= 0:
        alertas.append("sem UBS/estabelecimento APS")
    elif float(row.get("populacao_por_ubs", 0) or 0) > 10000:
        alertas.append("população por UBS elevada")

    if float(row.get("qtd_assentamentos", 0) or 0) > 0:
        alertas.append("possui assentamentos")
    if float(row.get("qtd_terras_indigenas", 0) or 0) > 0:
        alertas.append("possui terras indígenas/interseções")
    if float(row.get("qtd_ocorrencias_ambientais", 0) or 0) > 0:
        alertas.append("ocorrências ambientais registradas")
    if float(row.get("area_km2", 0) or 0) > 10000 and float(row.get("densidade_hab_km2", 0) or 0) < 3:
        alertas.append("território extenso e baixa densidade")
    return "; ".join(alertas) if alertas else "Sem alerta territorial crítico nos critérios atuais"


def carregar_analise_territorial_aps() -> pd.DataFrame:
    base = read_table("base_municipal_consolidada")
    if base.empty:
        return pd.DataFrame()

    out = base.copy()
    out["codigo_ibge"] = out["codigo_ibge"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(7)

    municipios_ref = _municipios_base()

    agregados = [
        _agregar_assentamentos(municipios_ref),
        _agregar_terras_indigenas(municipios_ref),
        _agregar_ocorrencias_ambientais(municipios_ref),
    ]
    for agg in agregados:
        if not agg.empty and "codigo_ibge" in agg.columns:
            out = out.merge(agg, on="codigo_ibge", how="left")

    colunas_zero = [
        "qtd_assentamentos",
        "area_assentamentos_ha",
        "qtd_terras_indigenas",
        "qtd_ocorrencias_ambientais",
        "ultimo_ano_ocorrencia_ambiental",
    ]
    for col in colunas_zero:
        if col not in out.columns:
            out[col] = 0
        out[col] = _num_series(out[col])

    for col in ["nomes_terras_indigenas", "principais_produtos_ambientais"]:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].fillna("").astype(str)

    num_cols = [
        "populacao", "area_km2", "densidade_hab_km2", "total_ubs",
        "total_equipes_aps", "total_profissionais_aps", "latitude", "longitude",
        "total_leitos_sus", "nascidos_vivos", "obitos", "escolas_total",
        "matriculas_total", "pib_municipal_precos_correntes"
    ]
    for col in num_cols:
        if col not in out.columns:
            out[col] = 0
        out[col] = _num_series(out[col])

    out["populacao_por_equipe"] = _safe_div(out["populacao"], out["total_equipes_aps"]).fillna(0)
    out["populacao_por_ubs"] = _safe_div(out["populacao"], out["total_ubs"]).fillna(0)
    out["profissionais_por_equipe"] = _safe_div(out["total_profissionais_aps"], out["total_equipes_aps"]).fillna(0)
    out["equipes_por_10mil_hab"] = _safe_div(out["total_equipes_aps"] * 10000, out["populacao"]).fillna(0)
    out["ubs_por_10mil_hab"] = _safe_div(out["total_ubs"] * 10000, out["populacao"]).fillna(0)

    # Componentes de prioridade. Não são regra oficial; são triagem executiva para investigação técnica.
    deficit_equipes = (out["populacao_por_equipe"] / 4000).clip(0, 1)
    deficit_equipes = deficit_equipes.where(out["total_equipes_aps"] > 0, 1.0)

    deficit_ubs = (out["populacao_por_ubs"] / 10000).clip(0, 1)
    deficit_ubs = deficit_ubs.where(out["total_ubs"] > 0, 1.0)

    pressao_pop = _norm_01(out["populacao"])
    dispersao = ((_norm_01(out["area_km2"]) * 0.65) + (_norm_01(out["densidade_hab_km2"], invert=True) * 0.35)).clip(0, 1)

    camadas_especiais = (
        (out["qtd_assentamentos"].clip(0, 3) / 3) * 0.45
        + (out["qtd_terras_indigenas"].clip(0, 3) / 3) * 0.45
        + ((out["qtd_assentamentos"] + out["qtd_terras_indigenas"] > 0).astype(float) * 0.10)
    ).clip(0, 1)

    risco_ambiental = (out["qtd_ocorrencias_ambientais"].clip(0, 5) / 5).clip(0, 1)

    out["score_deficit_equipes"] = (deficit_equipes * 100).round(1)
    out["score_deficit_ubs"] = (deficit_ubs * 100).round(1)
    out["score_pressao_populacional"] = (pressao_pop * 100).round(1)
    out["score_dispersao_territorial"] = (dispersao * 100).round(1)
    out["score_camadas_especiais"] = (camadas_especiais * 100).round(1)
    out["score_risco_ambiental"] = (risco_ambiental * 100).round(1)

    out["indice_prioridade_aps"] = (
        out["score_deficit_equipes"] * 0.30
        + out["score_deficit_ubs"] * 0.20
        + out["score_pressao_populacional"] * 0.15
        + out["score_dispersao_territorial"] * 0.15
        + out["score_camadas_especiais"] * 0.15
        + out["score_risco_ambiental"] * 0.05
    ).round(1)

    out["classificacao_prioridade_aps"] = out["indice_prioridade_aps"].map(_classificar_prioridade)
    out["alertas_territoriais"] = out.apply(_gerar_alertas, axis=1)

    ordem = {"Muito alta": 0, "Alta": 1, "Média": 2, "Monitoramento": 3}
    out["ordem_prioridade"] = out["classificacao_prioridade_aps"].map(ordem).fillna(9)
    return out.sort_values(["ordem_prioridade", "indice_prioridade_aps"], ascending=[True, False]).drop(columns=["ordem_prioridade"], errors="ignore")


def resumo_executivo_territorial(df: pd.DataFrame | None = None) -> Dict[str, float]:
    if df is None:
        df = carregar_analise_territorial_aps()
    if df.empty:
        return {
            "municipios": 0, "populacao": 0, "ubs": 0, "equipes": 0,
            "prioridade_alta": 0, "assentamentos": 0, "terras_indigenas": 0, "ocorrencias": 0,
        }

    return {
        "municipios": int(df["municipio"].nunique()),
        "populacao": float(_num_series(df["populacao"]).sum()),
        "ubs": int(_num_series(df["total_ubs"]).sum()),
        "equipes": int(_num_series(df["total_equipes_aps"]).sum()),
        "prioridade_alta": int(df["classificacao_prioridade_aps"].isin(["Muito alta", "Alta"]).sum()),
        "assentamentos": int(_num_series(df["qtd_assentamentos"]).sum()),
        "terras_indigenas": int(_num_series(df["qtd_terras_indigenas"]).sum()),
        "ocorrencias": int(_num_series(df["qtd_ocorrencias_ambientais"]).sum()),
    }


def resumo_regional_territorial(df: pd.DataFrame | None = None) -> pd.DataFrame:
    if df is None:
        df = carregar_analise_territorial_aps()
    if df.empty or "regiao_saude" not in df.columns:
        return pd.DataFrame()

    temp = df.copy()
    temp["prioridade_alta_flag"] = temp["classificacao_prioridade_aps"].isin(["Muito alta", "Alta"]).astype(int)
    out = temp.groupby("regiao_saude", dropna=False).agg(
        municipios=("municipio", "nunique"),
        populacao=("populacao", "sum"),
        ubs=("total_ubs", "sum"),
        equipes_aps=("total_equipes_aps", "sum"),
        profissionais_aps=("total_profissionais_aps", "sum"),
        prioridade_media=("indice_prioridade_aps", "mean"),
        municipios_alta_prioridade=("prioridade_alta_flag", "sum"),
        assentamentos=("qtd_assentamentos", "sum"),
        terras_indigenas=("qtd_terras_indigenas", "sum"),
        ocorrencias_ambientais=("qtd_ocorrencias_ambientais", "sum"),
    ).reset_index()
    out["populacao_por_equipe"] = _safe_div(out["populacao"], out["equipes_aps"]).round(1)
    out["populacao_por_ubs"] = _safe_div(out["populacao"], out["ubs"]).round(1)
    out["prioridade_media"] = out["prioridade_media"].round(1)
    return out.sort_values(["municipios_alta_prioridade", "prioridade_media"], ascending=[False, False])


def detalhar_municipio_territorial(municipio: str) -> Dict[str, object]:
    df = carregar_analise_territorial_aps()
    if df.empty or not municipio:
        return {"linha": {}, "assentamentos": pd.DataFrame(), "terras_indigenas": pd.DataFrame(), "ocorrencias": pd.DataFrame()}

    row_df = df[df["municipio"].astype(str).eq(str(municipio))]
    linha = row_df.iloc[0].to_dict() if not row_df.empty else {}
    codigo = str(linha.get("codigo_ibge", "") or "")

    assent = read_table("dados_mt_assentamentos")
    if not assent.empty and "codigo_ibge" in assent.columns:
        assent["codigo_ibge"] = assent["codigo_ibge"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(7)
        assent = assent[assent["codigo_ibge"].eq(codigo)].copy()
    else:
        assent = pd.DataFrame()

    terras = read_table("dados_mt_terras_indigenas")
    if not terras.empty:
        muni_norm = _normalizar_texto(municipio)
        def contem_municipio(valor):
            partes = str(valor or "").replace(",", ";").split(";")
            return any(_normalizar_texto(p) == muni_norm for p in partes)
        mask = pd.Series([False] * len(terras))
        if "municipios_intersectados" in terras.columns:
            mask = terras["municipios_intersectados"].map(contem_municipio)
        if "codigo_ibge" in terras.columns:
            cods = terras["codigo_ibge"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(7)
            mask = mask | cods.eq(codigo)
        terras = terras[mask].copy()
    else:
        terras = pd.DataFrame()

    ocorr = read_table("dados_mt_areas_contaminadas")
    if not ocorr.empty and "codigo_ibge" in ocorr.columns:
        ocorr["codigo_ibge"] = ocorr["codigo_ibge"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(7)
        ocorr = ocorr[ocorr["codigo_ibge"].eq(codigo)].copy()
    else:
        ocorr = pd.DataFrame()

    return {"linha": linha, "assentamentos": assent, "terras_indigenas": terras, "ocorrencias": ocorr}


def gerar_matriz_oportunidades_aps(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Gera uma matriz executiva de oportunidades/alertas para orientar a análise técnica.

    A matriz não cria dado novo; ela organiza os indicadores já calculados em eixos de decisão.
    """
    if df is None:
        df = carregar_analise_territorial_aps()
    if df.empty:
        return pd.DataFrame(columns=[
            "eixo", "municipio", "regiao_saude", "prioridade", "indicador_chave",
            "evidencia", "encaminhamento_sugerido", "indice_prioridade_aps",
        ])

    base = df.copy()
    for col in [
        "populacao", "total_equipes_aps", "total_ubs", "populacao_por_equipe", "populacao_por_ubs",
        "area_km2", "densidade_hab_km2", "qtd_assentamentos", "qtd_terras_indigenas",
        "qtd_ocorrencias_ambientais", "indice_prioridade_aps",
    ]:
        if col not in base.columns:
            base[col] = 0
        base[col] = _num_series(base[col])

    registros: List[Dict[str, object]] = []

    def add_rows(temp: pd.DataFrame, eixo: str, indicador: str, evidencia_fn, encaminhamento: str, limite: int = 10):
        for _, row in temp.head(limite).iterrows():
            registros.append({
                "eixo": eixo,
                "municipio": row.get("municipio", ""),
                "regiao_saude": row.get("regiao_saude", ""),
                "prioridade": row.get("classificacao_prioridade_aps", "Monitoramento"),
                "indicador_chave": indicador,
                "evidencia": evidencia_fn(row),
                "encaminhamento_sugerido": encaminhamento,
                "indice_prioridade_aps": round(float(row.get("indice_prioridade_aps", 0) or 0), 1),
            })

    # 1) Pressão assistencial: muita população por equipe e/ou por UBS.
    pressao = base[(base["total_equipes_aps"] > 0)].sort_values(
        ["populacao_por_equipe", "indice_prioridade_aps"], ascending=[False, False]
    )
    add_rows(
        pressao,
        "Pressão assistencial",
        "População por equipe APS",
        lambda r: f"{int(round(r.get('populacao_por_equipe', 0))):,} hab./equipe".replace(",", "."),
        "Conferir suficiência de equipes, composição profissional, cobertura territorial e eventual necessidade de expansão/reorganização.",
    )

    ubs = base[(base["total_ubs"] > 0)].sort_values(
        ["populacao_por_ubs", "indice_prioridade_aps"], ascending=[False, False]
    )
    add_rows(
        ubs,
        "Pressão sobre UBS",
        "População por UBS/estabelecimento APS",
        lambda r: f"{int(round(r.get('populacao_por_ubs', 0))):,} hab./UBS".replace(",", "."),
        "Avaliar capacidade física, distribuição das UBS, necessidade de ampliação, reforma, nova unidade ou reterritorialização.",
    )

    # 2) Territórios especiais: assentamentos e terras indígenas.
    territorios = base[(base["qtd_assentamentos"] > 0) | (base["qtd_terras_indigenas"] > 0)].copy()
    territorios["camadas_especiais_total"] = territorios["qtd_assentamentos"] + territorios["qtd_terras_indigenas"]
    territorios = territorios.sort_values(["camadas_especiais_total", "indice_prioridade_aps"], ascending=[False, False])
    add_rows(
        territorios,
        "Territórios especiais",
        "Assentamentos e terras indígenas/interseções",
        lambda r: f"{int(r.get('qtd_assentamentos', 0))} assent.; {int(r.get('qtd_terras_indigenas', 0))} TI/interseções",
        "Priorizar análise territorial fina: acesso, transporte sanitário, equipes volantes, pontos de apoio e articulação com políticas específicas.",
    )

    # 3) Dispersão territorial: grande área e baixa densidade.
    dispersao = base[(base["area_km2"] > 0)].copy()
    dispersao["score_disp_simples"] = _norm_01(dispersao["area_km2"]) * 70 + _norm_01(dispersao["densidade_hab_km2"], invert=True) * 30
    dispersao = dispersao.sort_values(["score_disp_simples", "indice_prioridade_aps"], ascending=[False, False])
    add_rows(
        dispersao,
        "Dispersão territorial",
        "Área extensa e/ou baixa densidade",
        lambda r: f"Área {round(float(r.get('area_km2', 0)), 1)} km²; densidade {round(float(r.get('densidade_hab_km2', 0)), 1)} hab./km²",
        "Avaliar vazios assistenciais, distância entre comunidades e UBS, necessidade de unidades de apoio e estratégias itinerantes.",
    )

    # 4) Risco ambiental: ocorrências ambientais/produtos perigosos.
    ambiental = base[base["qtd_ocorrencias_ambientais"] > 0].sort_values(
        ["qtd_ocorrencias_ambientais", "indice_prioridade_aps"], ascending=[False, False]
    )
    add_rows(
        ambiental,
        "Risco ambiental",
        "Ocorrências ambientais/produtos perigosos",
        lambda r: f"{int(r.get('qtd_ocorrencias_ambientais', 0))} ocorrência(s); produtos: {str(r.get('principais_produtos_ambientais', '') or '-')[:90]}",
        "Conferir necessidade de vigilância em saúde ambiental, integração com vigilância sanitária/ambiental e preparação da rede local.",
    )

    # 5) Prioridade geral: ranking consolidado pelo índice.
    geral = base.sort_values("indice_prioridade_aps", ascending=False)
    add_rows(
        geral,
        "Prioridade geral",
        "Índice preliminar de prioridade APS",
        lambda r: f"Índice {round(float(r.get('indice_prioridade_aps', 0)), 1)}; alertas: {str(r.get('alertas_territoriais', '') or '-')[:100]}",
        "Realizar leitura integrada do município e validar com áreas técnicas antes de propor intervenção, investimento ou ação de apoio.",
    )

    out = pd.DataFrame(registros)
    if out.empty:
        return out
    return out.drop_duplicates(subset=["eixo", "municipio", "indicador_chave"]).sort_values(
        ["indice_prioridade_aps", "eixo"], ascending=[False, True]
    ).reset_index(drop=True)


def resumo_camadas_territoriais(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Resumo quantitativo das camadas especiais e seu grau de cobertura municipal."""
    if df is None:
        df = carregar_analise_territorial_aps()
    if df.empty:
        return pd.DataFrame(columns=["camada", "municipios_com_registro", "total_registros", "observacao"])

    def soma(col: str) -> int:
        return int(_num_series(df[col]).sum()) if col in df.columns else 0

    return pd.DataFrame([
        {
            "camada": "Assentamentos",
            "municipios_com_registro": int((_num_series(df.get("qtd_assentamentos", pd.Series(dtype=float))) > 0).sum()),
            "total_registros": soma("qtd_assentamentos"),
            "observacao": "Camada territorial útil para ruralidade, acesso e vazios assistenciais.",
        },
        {
            "camada": "Terras indígenas/interseções",
            "municipios_com_registro": int((_num_series(df.get("qtd_terras_indigenas", pd.Series(dtype=float))) > 0).sum()),
            "total_registros": soma("qtd_terras_indigenas"),
            "observacao": "Camada de equidade territorial; exige validação técnica antes de qualquer decisão sensível.",
        },
        {
            "camada": "Ocorrências ambientais/produtos perigosos",
            "municipios_com_registro": int((_num_series(df.get("qtd_ocorrencias_ambientais", pd.Series(dtype=float))) > 0).sum()),
            "total_registros": soma("qtd_ocorrencias_ambientais"),
            "observacao": "Camada complementar de risco ambiental; usar com cautela e validação da vigilância.",
        },
    ])


def gerar_carteira_acoes_aps(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Monta uma carteira preliminar de ações/projetos a partir da análise territorial.

    A carteira não define decisão automática. Ela traduz os alertas em linhas de ação para triagem,
    despacho técnico e discussão com áreas responsáveis.
    """
    if df is None:
        df = carregar_analise_territorial_aps()
    if df.empty:
        return pd.DataFrame(columns=[
            "municipio", "regiao_saude", "prioridade_territorial", "indice_prioridade_aps",
            "tipo_acao", "acao_sugerida", "justificativa", "encaminhamento", "nivel_urgencia",
            "indicador_referencia", "valor_referencia",
        ])

    base = df.copy()
    for col in [
        "populacao", "total_equipes_aps", "total_ubs", "populacao_por_equipe", "populacao_por_ubs",
        "area_km2", "densidade_hab_km2", "qtd_assentamentos", "qtd_terras_indigenas",
        "qtd_ocorrencias_ambientais", "indice_prioridade_aps", "total_profissionais_aps",
    ]:
        if col not in base.columns:
            base[col] = 0
        base[col] = _num_series(base[col])

    registros: List[Dict[str, object]] = []

    def urgencia(row: pd.Series, fator: float = 0) -> str:
        score = float(row.get("indice_prioridade_aps", 0) or 0) + fator
        classe = str(row.get("classificacao_prioridade_aps", "") or "")
        if classe == "Muito alta" or score >= 72:
            return "Crítica"
        if classe == "Alta" or score >= 55:
            return "Alta"
        if score >= 35:
            return "Média"
        return "Monitoramento"

    def add(row: pd.Series, tipo: str, acao: str, justificativa: str, encaminhamento: str,
            indicador: str, valor: object, fator: float = 0):
        registros.append({
            "municipio": row.get("municipio", ""),
            "regiao_saude": row.get("regiao_saude", ""),
            "prioridade_territorial": row.get("classificacao_prioridade_aps", "Monitoramento"),
            "indice_prioridade_aps": round(float(row.get("indice_prioridade_aps", 0) or 0), 1),
            "tipo_acao": tipo,
            "acao_sugerida": acao,
            "justificativa": justificativa,
            "encaminhamento": encaminhamento,
            "nivel_urgencia": urgencia(row, fator),
            "indicador_referencia": indicador,
            "valor_referencia": valor,
        })

    for _, row in base.iterrows():
        pop_equipe = float(row.get("populacao_por_equipe", 0) or 0)
        pop_ubs = float(row.get("populacao_por_ubs", 0) or 0)
        equipes = float(row.get("total_equipes_aps", 0) or 0)
        ubs = float(row.get("total_ubs", 0) or 0)
        assent = float(row.get("qtd_assentamentos", 0) or 0)
        ti = float(row.get("qtd_terras_indigenas", 0) or 0)
        ocorr = float(row.get("qtd_ocorrencias_ambientais", 0) or 0)
        area = float(row.get("area_km2", 0) or 0)
        dens = float(row.get("densidade_hab_km2", 0) or 0)

        if equipes <= 0:
            add(
                row,
                "Equipes APS",
                "Verificar ausência de equipes APS na base e avaliar necessidade de implantação/reorganização.",
                "Município sem equipes APS identificadas na base consolidada.",
                "Checar CNES/INE, validar com área técnica e avaliar plano de implantação, recomposição ou correção cadastral.",
                "total_equipes_aps",
                0,
                20,
            )
        elif pop_equipe > 4000:
            add(
                row,
                "Equipes APS",
                "Avaliar expansão, recomposição ou reterritorialização de equipes APS.",
                f"População por equipe estimada em {int(round(pop_equipe)):,} habitantes/equipe.".replace(",", "."),
                "Conferir suficiência de equipes, cobertura territorial, composição profissional e possibilidade de apoio estadual ao reordenamento.",
                "populacao_por_equipe",
                round(pop_equipe, 1),
                12,
            )

        if ubs <= 0:
            add(
                row,
                "Infraestrutura APS",
                "Verificar ausência de UBS/estabelecimento APS e avaliar necessidade de ponto de atenção.",
                "Município sem UBS/estabelecimento APS identificado na base consolidada.",
                "Validar cadastro de estabelecimentos, situação da rede física e eventual necessidade de unidade, ponto de apoio ou estratégia itinerante.",
                "total_ubs",
                0,
                18,
            )
        elif pop_ubs > 10000:
            add(
                row,
                "Infraestrutura APS",
                "Avaliar ampliação, reforma, nova UBS ou reorganização territorial da rede física.",
                f"População por UBS estimada em {int(round(pop_ubs)):,} habitantes/UBS.".replace(",", "."),
                "Conferir capacidade instalada, localização das UBS, área de abrangência e necessidade de expansão/requalificação.",
                "populacao_por_ubs",
                round(pop_ubs, 1),
                10,
            )

        if assent > 0 or ti > 0:
            add(
                row,
                "Territórios especiais",
                "Avaliar estratégia específica de acesso para comunidades rurais, assentamentos e/ou terras indígenas.",
                f"Camadas territoriais especiais: {int(assent)} assentamento(s) e {int(ti)} terra(s) indígena(s)/interseções.",
                "Mapear distância até UBS, rotas de acesso, equipes de referência, ações volantes, transporte sanitário e articulação intersetorial.",
                "qtd_assentamentos/qtd_terras_indigenas",
                f"{int(assent)} assent.; {int(ti)} TI",
                8,
            )

        if area > 10000 and dens < 3:
            add(
                row,
                "Acesso territorial",
                "Realizar estudo de vazios assistenciais e logística territorial.",
                f"Território extenso ({round(area, 1)} km²) e baixa densidade ({round(dens, 1)} hab./km²).",
                "Cruzar localização das UBS/equipes com comunidades, estradas, assentamentos e territórios especiais para definir pontos de apoio.",
                "area_km2/densidade_hab_km2",
                f"{round(area, 1)} km²; {round(dens, 1)} hab./km²",
                6,
            )

        if ocorr > 0:
            add(
                row,
                "Vigilância ambiental",
                "Encaminhar para análise integrada com vigilância em saúde ambiental.",
                f"Foram identificadas {int(ocorr)} ocorrência(s) ambiental(is)/produtos perigosos na camada carregada.",
                "Validar a ocorrência com a área de vigilância, verificar risco atual e avaliar necessidade de acompanhamento territorial pela APS.",
                "qtd_ocorrencias_ambientais",
                int(ocorr),
                4,
            )

    out = pd.DataFrame(registros)
    if out.empty:
        return out

    ordem = {"Crítica": 0, "Alta": 1, "Média": 2, "Monitoramento": 3}
    out["ordem_urgencia"] = out["nivel_urgencia"].map(ordem).fillna(9)
    return out.sort_values(
        ["ordem_urgencia", "indice_prioridade_aps", "municipio", "tipo_acao"],
        ascending=[True, False, True, True],
    ).drop(columns=["ordem_urgencia"], errors="ignore").reset_index(drop=True)


def _perfil_intervencao_linha(row: pd.Series) -> Dict[str, object]:
    """Classifica o perfil dominante de intervenção do município.

    A classificação é executiva e serve para organizar a carteira de análise. Não substitui
    decisão técnica nem critérios oficiais.
    """
    pop_equipe = float(row.get("populacao_por_equipe", 0) or 0)
    pop_ubs = float(row.get("populacao_por_ubs", 0) or 0)
    equipes = float(row.get("total_equipes_aps", 0) or 0)
    ubs = float(row.get("total_ubs", 0) or 0)
    area = float(row.get("area_km2", 0) or 0)
    dens = float(row.get("densidade_hab_km2", 0) or 0)
    assent = float(row.get("qtd_assentamentos", 0) or 0)
    ti = float(row.get("qtd_terras_indigenas", 0) or 0)
    ocorr = float(row.get("qtd_ocorrencias_ambientais", 0) or 0)

    candidatos = []

    score_equipes = 100 if equipes <= 0 else min(100, (pop_equipe / 4000) * 100)
    candidatos.append((
        score_equipes,
        "Reforço/reorganização de equipes APS",
        "Alta pressão populacional por equipe ou ausência de equipe APS na base.",
        "Validar CNES/INE, cobertura territorial, composição profissional e necessidade de expansão ou reterritorialização.",
    ))

    score_ubs = 100 if ubs <= 0 else min(100, (pop_ubs / 10000) * 100)
    candidatos.append((
        score_ubs,
        "Infraestrutura e rede física APS",
        "Alta população por UBS/estabelecimento APS ou ausência de UBS na base.",
        "Avaliar distribuição das unidades, capacidade física, reforma, ampliação, nova UBS ou ponto de apoio.",
    ))

    score_especiais = min(100, (min(assent, 3) / 3) * 45 + (min(ti, 3) / 3) * 45 + (10 if (assent + ti) > 0 else 0))
    candidatos.append((
        score_especiais,
        "Equidade e territórios especiais",
        "Presença de assentamentos, terras indígenas ou interseções territoriais especiais.",
        "Mapear comunidades, distância até UBS, rotas, pontos de apoio, equipes volantes e articulação intersetorial.",
    ))

    score_acesso = 0
    if area > 0:
        score_acesso = min(100, (area / 10000) * 65 + (35 if dens < 3 else 10 if dens < 10 else 0))
    candidatos.append((
        score_acesso,
        "Acesso territorial e logística",
        "Município extenso, disperso ou de baixa densidade, com potencial dificuldade de acesso à APS.",
        "Estudar vazios assistenciais, deslocamento, transporte sanitário, pontos de apoio e ações itinerantes.",
    ))

    score_amb = min(100, (ocorr / 5) * 100)
    candidatos.append((
        score_amb,
        "Vigilância ambiental integrada à APS",
        "Ocorrências ambientais/produtos perigosos registradas na camada estadual.",
        "Validar com vigilância ambiental/sanitária e avaliar necessidade de acompanhamento territorial pela APS.",
    ))

    candidatos.sort(key=lambda x: x[0], reverse=True)
    score, perfil, evidencia, encaminhamento = candidatos[0]
    if score < 25:
        perfil = "Monitoramento integrado"
        evidencia = "Nenhum eixo isolado apresentou alerta dominante pelos critérios atuais."
        encaminhamento = "Manter acompanhamento e revisar com novas bases de desempenho, cobertura oficial e produção APS."

    return {
        "perfil_intervencao": perfil,
        "score_perfil_intervencao": round(float(score), 1),
        "evidencia_perfil": evidencia,
        "encaminhamento_perfil": encaminhamento,
    }


def gerar_perfis_intervencao_aps(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Gera uma tabela municipal com o perfil dominante de intervenção."""
    if df is None:
        df = carregar_analise_territorial_aps()
    if df.empty:
        return pd.DataFrame(columns=[
            "municipio", "regiao_saude", "classificacao_prioridade_aps", "indice_prioridade_aps",
            "perfil_intervencao", "score_perfil_intervencao", "evidencia_perfil", "encaminhamento_perfil",
        ])

    base = df.copy()
    perfis = base.apply(_perfil_intervencao_linha, axis=1, result_type="expand")
    out = pd.concat([base.reset_index(drop=True), perfis.reset_index(drop=True)], axis=1)
    cols = [
        "municipio", "regiao_saude", "classificacao_prioridade_aps", "indice_prioridade_aps",
        "perfil_intervencao", "score_perfil_intervencao", "evidencia_perfil", "encaminhamento_perfil",
        "populacao", "total_equipes_aps", "total_ubs", "populacao_por_equipe", "populacao_por_ubs",
        "qtd_assentamentos", "qtd_terras_indigenas", "qtd_ocorrencias_ambientais", "area_km2", "densidade_hab_km2",
    ]
    cols = [c for c in cols if c in out.columns]
    ordem = {"Muito alta": 0, "Alta": 1, "Média": 2, "Monitoramento": 3}
    out["ordem_prioridade"] = out.get("classificacao_prioridade_aps", pd.Series(dtype=str)).map(ordem).fillna(9)
    return out[cols + ["ordem_prioridade"]].sort_values(
        ["ordem_prioridade", "score_perfil_intervencao", "indice_prioridade_aps"],
        ascending=[True, False, False],
    ).drop(columns=["ordem_prioridade"], errors="ignore").reset_index(drop=True)


def resumo_perfis_intervencao_aps(df: pd.DataFrame | None = None) -> pd.DataFrame:
    perfis = gerar_perfis_intervencao_aps(df)
    if perfis.empty:
        return pd.DataFrame(columns=["perfil_intervencao", "municipios", "prioridade_media", "municipios_alta_prioridade"])
    temp = perfis.copy()
    temp["alta_prioridade"] = temp["classificacao_prioridade_aps"].isin(["Muito alta", "Alta"]).astype(int)
    out = temp.groupby("perfil_intervencao", dropna=False).agg(
        municipios=("municipio", "nunique"),
        prioridade_media=("indice_prioridade_aps", "mean"),
        municipios_alta_prioridade=("alta_prioridade", "sum"),
        score_medio_perfil=("score_perfil_intervencao", "mean"),
    ).reset_index()
    out["prioridade_media"] = out["prioridade_media"].round(1)
    out["score_medio_perfil"] = out["score_medio_perfil"].round(1)
    return out.sort_values(["municipios_alta_prioridade", "prioridade_media"], ascending=[False, False])


def recalcular_indice_prioridade_aps(
    df: pd.DataFrame | None = None,
    pesos: Dict[str, float] | None = None,
    limite_muito_alta: float = 70.0,
    limite_alta: float = 50.0,
    limite_media: float = 30.0,
) -> pd.DataFrame:
    """Recalcula o índice preliminar com pesos ajustáveis para simulação de cenários.

    A função não altera a base oficial. Ela apenas cria uma visão simulada para testar
    sensibilidade dos pesos e apoiar pactuação metodológica.
    """
    if df is None:
        df = carregar_analise_territorial_aps()
    if df.empty:
        return pd.DataFrame()

    pesos_padrao = {
        "score_deficit_equipes": 30.0,
        "score_deficit_ubs": 20.0,
        "score_pressao_populacional": 15.0,
        "score_dispersao_territorial": 15.0,
        "score_camadas_especiais": 15.0,
        "score_risco_ambiental": 5.0,
    }
    if pesos:
        pesos_padrao.update({k: float(v or 0) for k, v in pesos.items() if k in pesos_padrao})

    total_pesos = sum(max(float(v), 0.0) for v in pesos_padrao.values())
    if total_pesos <= 0:
        total_pesos = 1.0

    out = df.copy()
    for col in pesos_padrao:
        if col not in out.columns:
            out[col] = 0.0
        out[col] = _num_series(out[col])

    indice = pd.Series([0.0] * len(out), index=out.index)
    for col, peso in pesos_padrao.items():
        indice += out[col] * (max(float(peso), 0.0) / total_pesos)

    def classificar_simulado(valor: float) -> str:
        try:
            valor = float(valor)
        except Exception:
            valor = 0.0
        if valor >= limite_muito_alta:
            return "Muito alta"
        if valor >= limite_alta:
            return "Alta"
        if valor >= limite_media:
            return "Média"
        return "Monitoramento"

    out["indice_prioridade_simulado"] = indice.round(1)
    out["classificacao_simulada"] = out["indice_prioridade_simulado"].map(classificar_simulado)
    out["variacao_indice"] = (out["indice_prioridade_simulado"] - _num_series(out.get("indice_prioridade_aps", 0))).round(1)

    out["ranking_original"] = _num_series(out.get("indice_prioridade_aps", 0)).rank(method="min", ascending=False).astype(int)
    out["ranking_simulado"] = out["indice_prioridade_simulado"].rank(method="min", ascending=False).astype(int)
    out["mudanca_ranking"] = (out["ranking_original"] - out["ranking_simulado"]).astype(int)

    ordem = {"Muito alta": 0, "Alta": 1, "Média": 2, "Monitoramento": 3}
    out["ordem_simulada"] = out["classificacao_simulada"].map(ordem).fillna(9)
    return out.sort_values(["ordem_simulada", "indice_prioridade_simulado"], ascending=[True, False]).drop(columns=["ordem_simulada"], errors="ignore")


def resumo_simulacao_prioridade(df_simulado: pd.DataFrame) -> pd.DataFrame:
    if df_simulado is None or df_simulado.empty:
        return pd.DataFrame(columns=["classificacao_simulada", "municipios", "populacao", "indice_medio"])
    temp = df_simulado.copy()
    out = temp.groupby("classificacao_simulada", dropna=False).agg(
        municipios=("municipio", "nunique"),
        populacao=("populacao", "sum"),
        indice_medio=("indice_prioridade_simulado", "mean"),
        equipes_aps=("total_equipes_aps", "sum"),
        ubs=("total_ubs", "sum"),
    ).reset_index()
    out["indice_medio"] = out["indice_medio"].round(1)
    ordem = {"Muito alta": 0, "Alta": 1, "Média": 2, "Monitoramento": 3}
    out["ordem"] = out["classificacao_simulada"].map(ordem).fillna(9)
    return out.sort_values("ordem").drop(columns=["ordem"], errors="ignore")



def gerar_painel_executivo_aps(df: pd.DataFrame | None = None) -> Dict[str, object]:
    """Organiza uma visão executiva sintética da análise territorial APS.

    A função prepara listas curtas e mensagens-chave para apresentação gerencial,
    sem substituir a análise técnica detalhada das abas específicas.
    """
    if df is None:
        df = carregar_analise_territorial_aps()
    if df.empty:
        return {
            "kpis": {},
            "mensagens": [],
            "top_prioridade": pd.DataFrame(),
            "top_pressao_equipes": pd.DataFrame(),
            "top_pressao_ubs": pd.DataFrame(),
            "top_territorios_especiais": pd.DataFrame(),
            "top_dispersao": pd.DataFrame(),
            "regional": pd.DataFrame(),
        }

    base = df.copy()
    for col in [
        "populacao", "total_equipes_aps", "total_ubs", "total_profissionais_aps",
        "populacao_por_equipe", "populacao_por_ubs", "area_km2", "densidade_hab_km2",
        "qtd_assentamentos", "qtd_terras_indigenas", "qtd_ocorrencias_ambientais",
        "indice_prioridade_aps", "score_deficit_equipes", "score_deficit_ubs",
        "score_dispersao_territorial", "score_camadas_especiais", "score_risco_ambiental",
    ]:
        if col not in base.columns:
            base[col] = 0
        base[col] = _num_series(base[col])

    base["territorios_especiais_total"] = base["qtd_assentamentos"] + base["qtd_terras_indigenas"]
    base["prioridade_alta_flag"] = base.get("classificacao_prioridade_aps", "").isin(["Muito alta", "Alta"]).astype(int)

    kpis = {
        "municipios": int(base["municipio"].nunique()),
        "populacao": float(base["populacao"].sum()),
        "equipes": int(base["total_equipes_aps"].sum()),
        "ubs": int(base["total_ubs"].sum()),
        "profissionais": int(base["total_profissionais_aps"].sum()),
        "prioridade_alta": int(base["prioridade_alta_flag"].sum()),
        "sem_equipes": int((base["total_equipes_aps"] <= 0).sum()),
        "sem_ubs": int((base["total_ubs"] <= 0).sum()),
        "municipios_assentamentos": int((base["qtd_assentamentos"] > 0).sum()),
        "municipios_ti": int((base["qtd_terras_indigenas"] > 0).sum()),
        "municipios_ambiental": int((base["qtd_ocorrencias_ambientais"] > 0).sum()),
    }

    mensagens = []
    mensagens.append(
        f"{kpis['prioridade_alta']} de {kpis['municipios']} municípios aparecem em prioridade alta ou muito alta na triagem territorial APS."
    )
    if kpis["sem_equipes"]:
        mensagens.append(f"{kpis['sem_equipes']} município(s) aparecem sem equipes APS na base consolidada e precisam de conferência CNES/INE.")
    if kpis["sem_ubs"]:
        mensagens.append(f"{kpis['sem_ubs']} município(s) aparecem sem UBS/estabelecimento APS na base consolidada e precisam de validação cadastral.")
    mensagens.append(
        f"{kpis['municipios_assentamentos']} município(s) possuem assentamentos e {kpis['municipios_ti']} possuem terras indígenas/interseções na camada territorial."
    )
    if kpis["municipios_ambiental"]:
        mensagens.append(f"{kpis['municipios_ambiental']} município(s) possuem ocorrência ambiental/produtos perigosos vinculados na base estadual tratada.")

    cols_top = [
        "municipio", "regiao_saude", "populacao", "total_equipes_aps", "total_ubs",
        "populacao_por_equipe", "populacao_por_ubs", "qtd_assentamentos",
        "qtd_terras_indigenas", "qtd_ocorrencias_ambientais", "indice_prioridade_aps",
        "classificacao_prioridade_aps", "alertas_territoriais",
    ]
    cols_top = [c for c in cols_top if c in base.columns]

    regional = resumo_regional_territorial(base)
    if not regional.empty:
        regional = regional.head(10)

    return {
        "kpis": kpis,
        "mensagens": mensagens,
        "top_prioridade": base.sort_values("indice_prioridade_aps", ascending=False)[cols_top].head(15),
        "top_pressao_equipes": base[base["total_equipes_aps"] > 0].sort_values("populacao_por_equipe", ascending=False)[cols_top].head(10),
        "top_pressao_ubs": base[base["total_ubs"] > 0].sort_values("populacao_por_ubs", ascending=False)[cols_top].head(10),
        "top_territorios_especiais": base[base["territorios_especiais_total"] > 0].sort_values(["territorios_especiais_total", "indice_prioridade_aps"], ascending=[False, False])[cols_top].head(10),
        "top_dispersao": base.sort_values(["score_dispersao_territorial", "indice_prioridade_aps"], ascending=[False, False])[cols_top].head(10),
        "regional": regional,
    }

def _fmt_rel_int(valor) -> str:
    try:
        if pd.isna(valor):
            return "0"
        return f"{int(round(float(valor))):,}".replace(",", ".")
    except Exception:
        return "0"


def _fmt_rel_num(valor, casas: int = 1) -> str:
    try:
        if pd.isna(valor):
            return "-"
        return f"{float(valor):,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "-"


def gerar_relatorio_municipal_aps(municipio: str, incluir_metodologia: bool = True) -> str:
    """Gera um texto técnico preliminar para apoiar relatório/despacho municipal.

    O texto é deliberadamente conservador: apresenta sinais de alerta e encaminhamentos
    para validação técnica, sem transformar a triagem em decisão automática.
    """
    detalhe = detalhar_municipio_territorial(municipio)
    linha = detalhe.get("linha", {}) or {}
    if not linha:
        return "Município não encontrado na base analítica territorial APS."

    nome = str(linha.get("municipio", municipio) or municipio)
    regiao = str(linha.get("regiao_saude", "Não informada") or "Não informada")
    classificacao = str(linha.get("classificacao_prioridade_aps", "Monitoramento") or "Monitoramento")
    indice = _fmt_rel_num(linha.get("indice_prioridade_aps", 0), 1)
    alertas = str(linha.get("alertas_territoriais", "") or "Sem alerta territorial crítico nos critérios atuais")

    populacao = _fmt_rel_int(linha.get("populacao", 0))
    area = _fmt_rel_num(linha.get("area_km2", 0), 1)
    densidade = _fmt_rel_num(linha.get("densidade_hab_km2", 0), 1)
    ubs = _fmt_rel_int(linha.get("total_ubs", 0))
    equipes = _fmt_rel_int(linha.get("total_equipes_aps", 0))
    profissionais = _fmt_rel_int(linha.get("total_profissionais_aps", 0))
    pop_equipe = _fmt_rel_num(linha.get("populacao_por_equipe", 0), 1)
    pop_ubs = _fmt_rel_num(linha.get("populacao_por_ubs", 0), 1)
    prof_equipe = _fmt_rel_num(linha.get("profissionais_por_equipe", 0), 1)

    assentamentos = _fmt_rel_int(linha.get("qtd_assentamentos", 0))
    terras = _fmt_rel_int(linha.get("qtd_terras_indigenas", 0))
    ocorrencias = _fmt_rel_int(linha.get("qtd_ocorrencias_ambientais", 0))
    nomes_ti = str(linha.get("nomes_terras_indigenas", "") or "")
    produtos = str(linha.get("principais_produtos_ambientais", "") or "")

    perfis = gerar_perfis_intervencao_aps()
    perfil_txt = "Monitoramento integrado"
    encaminhamento_perfil = "Manter acompanhamento e revisar com novas bases de desempenho, cobertura oficial e produção APS."
    evidencia_perfil = "Nenhum perfil dominante localizado."
    if not perfis.empty:
        alvo = perfis[perfis["municipio"].astype(str).eq(nome)]
        if not alvo.empty:
            p = alvo.iloc[0]
            perfil_txt = str(p.get("perfil_intervencao", perfil_txt) or perfil_txt)
            evidencia_perfil = str(p.get("evidencia_perfil", evidencia_perfil) or evidencia_perfil)
            encaminhamento_perfil = str(p.get("encaminhamento_perfil", encaminhamento_perfil) or encaminhamento_perfil)

    carteira = gerar_carteira_acoes_aps()
    acoes_txt = "Não foram mapeadas ações prioritárias automáticas para os critérios atuais."
    if not carteira.empty:
        acoes = carteira[carteira["municipio"].astype(str).eq(nome)].copy()
        if not acoes.empty:
            linhas = []
            for _, a in acoes.head(8).iterrows():
                linhas.append(
                    f"- {a.get('tipo_acao', '-')}: {a.get('justificativa', '-')}. Encaminhamento: {a.get('encaminhamento_sugerido', '-')}"
                )
            acoes_txt = "\n".join(linhas)

    extra_ti = f"\nTerras indígenas/interseções identificadas: {nomes_ti}." if nomes_ti else ""
    extra_amb = f"\nProdutos/resíduos mais recorrentes na camada ambiental: {produtos}." if produtos else ""

    metodologia = ""
    if incluir_metodologia:
        metodologia = """

Observação metodológica
O Índice Preliminar de Prioridade APS é uma triagem gerencial construída a partir das bases atualmente carregadas no sistema, incluindo estrutura APS, população, área territorial, densidade demográfica e camadas territoriais especiais. O resultado não substitui normas oficiais, critérios de financiamento, pactuações regionais ou validação das áreas técnicas. Sua finalidade é orientar a investigação, priorizar análises e apoiar a formulação de hipóteses para planejamento.
""".rstrip()

    texto = f"""# Relatório técnico preliminar — APS territorial

## Município
{nome} — Região de Saúde: {regiao}

## Síntese executiva
O município apresenta classificação preliminar **{classificacao}** no Índice Preliminar de Prioridade APS, com pontuação de **{indice}**. Os principais alertas identificados na base atual são: {alertas}.

## Estrutura e pressão assistencial
- População estimada/consolidada na base: {populacao} habitantes.
- UBS/estabelecimentos APS identificados: {ubs}.
- Equipes APS identificadas: {equipes}.
- Profissionais vinculados à APS: {profissionais}.
- População por equipe APS: {pop_equipe} habitantes/equipe.
- População por UBS/estabelecimento APS: {pop_ubs} habitantes/UBS.
- Profissionais por equipe APS: {prof_equipe}.

## Território e determinantes territoriais
- Área territorial: {area} km².
- Densidade demográfica: {densidade} hab./km².
- Assentamentos identificados: {assentamentos}.
- Terras indígenas/interseções territoriais: {terras}.
- Ocorrências ambientais/produtos perigosos vinculadas ao município: {ocorrencias}.{extra_ti}{extra_amb}

## Perfil dominante de intervenção
Perfil sugerido: **{perfil_txt}**.
Evidência principal: {evidencia_perfil}
Encaminhamento dominante: {encaminhamento_perfil}

## Carteira preliminar de ações
{acoes_txt}

## Encaminhamento técnico sugerido
Recomenda-se validar os dados com as áreas responsáveis, conferir a situação atual de CNES/INE, UBS, equipes e território adscrito, e cruzar a análise com informações locais sobre acesso, transporte, comunidades rurais, territórios tradicionais, produção assistencial e necessidades apontadas pelo Escritório Regional de Saúde.{metodologia}
"""
    return texto.strip()
