from datetime import datetime
import unicodedata

import pandas as pd

from config.municipios_mt import DEFAULT_MUNICIPIOS
from config.ibge_estimativas_2025_mt import (
    POPULACAO_2025_MT_POR_MUNICIPIO,
    CODIGO_IBGE_2025_MT_POR_MUNICIPIO,
    TOTAL_POPULACAO_2025_MT,
)
from config.parametros import TIPOS_EQUIPE_CNES
from database.connection import db_session, get_connection
from services.ibge_area_oficial_service import baixar_areas_oficiais_ibge_2025_mt


def _chave_municipio(valor) -> str:
    texto = str(valor or "").strip().upper()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    texto = " ".join(texto.split())
    return texto

ALIASES_CHAVE_MUNICIPIO = {
    "SANTO ANTONIO DE LEVERGER": "SANTO ANTONIO DO LEVERGER",
}


def _normalizar_alias_chave(chave: str) -> str:
    return ALIASES_CHAVE_MUNICIPIO.get(chave, chave)


_MUNICIPIOS_MT_OFICIAIS = {_chave_municipio(item.get("municipio")): item.get("municipio") for item in DEFAULT_MUNICIPIOS}
_POPULACAO_2025_POR_CHAVE = {_chave_municipio(k): v for k, v in POPULACAO_2025_MT_POR_MUNICIPIO.items()}
_CODIGO_2025_POR_CHAVE = {_chave_municipio(k): v for k, v in CODIGO_IBGE_2025_MT_POR_MUNICIPIO.items()}


def _filtrar_municipios_mt(df: pd.DataFrame, coluna_municipio: str = "municipio") -> pd.DataFrame:
    if df.empty or coluna_municipio not in df.columns:
        return df
    aux = df.copy()
    aux["_chave_municipio_mt"] = aux[coluna_municipio].map(_chave_municipio).map(_normalizar_alias_chave)
    aux = aux[aux["_chave_municipio_mt"].isin(_MUNICIPIOS_MT_OFICIAIS.keys())].copy()
    if not aux.empty:
        aux[coluna_municipio] = aux["_chave_municipio_mt"].map(_MUNICIPIOS_MT_OFICIAIS)
    return aux.drop(columns=["_chave_municipio_mt"], errors="ignore")


def _codigo_limpo(valor) -> str:
    import re
    digitos = re.sub(r"\D", "", "" if valor is None else str(valor))
    if len(digitos) >= 7:
        return digitos[:7]
    if len(digitos) == 6:
        return digitos
    return digitos


def _enriquecer_municipio_por_codigo_ibge(df: pd.DataFrame, municipios: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "codigo_ibge" not in df.columns or municipios.empty:
        return df
    out = df.copy()
    if "municipio" not in out.columns:
        out["municipio"] = ""
    mapa = {}
    for _, row in municipios.iterrows():
        cod = _codigo_limpo(row.get("codigo_ibge"))
        mun = row.get("municipio")
        if cod and mun:
            mapa[cod] = mun
            mapa[cod[:6]] = mun
    codigos = out["codigo_ibge"].map(_codigo_limpo)
    vazio = out["municipio"].isna() | (out["municipio"].astype(str).str.strip().isin(["", "None", "nan"]))
    out.loc[vazio, "municipio"] = codigos.map(mapa)[vazio]
    out["codigo_ibge"] = codigos
    return out


def _serie_ultimo_indicador(indicadores: pd.DataFrame, nomes_indicador: list[str], nome_coluna: str) -> pd.DataFrame:
    if indicadores.empty or "indicador" not in indicadores.columns:
        return pd.DataFrame(columns=["municipio", nome_coluna])
    nomes = [n.lower() for n in nomes_indicador]
    aux = indicadores[indicadores["indicador"].astype(str).str.lower().isin(nomes)].copy()
    if aux.empty:
        return pd.DataFrame(columns=["municipio", nome_coluna])
    aux["ano"] = pd.to_numeric(aux.get("ano"), errors="coerce")
    aux["valor"] = pd.to_numeric(aux.get("valor"), errors="coerce")
    aux = aux.sort_values(["municipio", "ano", "atualizado_em"], na_position="first")
    return aux.groupby("municipio").tail(1)[["municipio", "valor"]].rename(columns={"valor": nome_coluna})


def _serie_indicador_contem(indicadores: pd.DataFrame, termos: list[str], nome_coluna: str) -> pd.DataFrame:
    if indicadores.empty or "indicador" not in indicadores.columns:
        return pd.DataFrame(columns=["municipio", nome_coluna])
    indicador_norm = indicadores["indicador"].astype(str).str.lower()
    mask = pd.Series(False, index=indicadores.index)
    for termo in termos:
        mask = mask | indicador_norm.str.contains(termo.lower(), na=False)
    aux = indicadores[mask].copy()
    if aux.empty:
        return pd.DataFrame(columns=["municipio", nome_coluna])
    aux["ano"] = pd.to_numeric(aux.get("ano"), errors="coerce")
    aux["valor"] = pd.to_numeric(aux.get("valor"), errors="coerce")
    aux = aux.sort_values(["municipio", "ano", "atualizado_em"], na_position="first")
    return aux.groupby("municipio").tail(1)[["municipio", "valor"]].rename(columns={"valor": nome_coluna})


def _juntar_indicador(base: pd.DataFrame, indicadores: pd.DataFrame, nomes: list[str], coluna: str, contem: bool = False) -> pd.DataFrame:
    serie = _serie_indicador_contem(indicadores, nomes, coluna) if contem else _serie_ultimo_indicador(indicadores, nomes, coluna)
    if serie.empty:
        base[coluna] = None
    else:
        base = base.merge(serie, on="municipio", how="left")
    return base


def _coluna_tem_valor_util(df: pd.DataFrame, coluna: str) -> bool:
    """Indica se uma coluna consolidada possui ao menos um valor aproveitável.

    O banco pode conter registros de rastreabilidade com valor nulo quando uma
    API está instável. Esses registros não devem transformar uma camada em
    "consolidada" nem aparecer como informação analítica final.
    """
    if df is None or df.empty or coluna not in df.columns:
        return False
    serie = pd.to_numeric(df[coluna], errors="coerce")
    return bool(serie.notna().any())


INDICADORES_INEP_CONSOLIDACAO = [
    "escolas_total",
    "escolas_urbanas",
    "escolas_rurais",
    "escolas_indigenas",
    "escolas_quilombolas",
    "escolas_educacao_especial_aee",
    "matriculas_total",
    "matriculas_educacao_especial",
]


def _juntar_inep_por_codigo_ou_municipio(base: pd.DataFrame, indicadores: pd.DataFrame) -> pd.DataFrame:
    """Integra indicadores INEP à base consolidada de forma robusta.

    A auditoria do INEP usa a própria tabela indicadores_municipais e já mostrou
    dados corretos. Na consolidação, porém, alguns bancos ficaram com município
    em grafia/código diferente e a junção direta por nome não preencheu os
    campos. Esta rotina prioriza o código IBGE; se ele não existir no indicador,
    usa chave normalizada do município.
    """
    out = base.copy()
    for col in INDICADORES_INEP_CONSOLIDACAO:
        if col not in out.columns:
            out[col] = pd.NA

    if indicadores is None or indicadores.empty or "indicador" not in indicadores.columns:
        return out

    ind = indicadores[indicadores["indicador"].astype(str).str.lower().isin(INDICADORES_INEP_CONSOLIDACAO)].copy()
    if ind.empty:
        return out

    ind["indicador"] = ind["indicador"].astype(str).str.lower()
    ind["valor"] = pd.to_numeric(ind.get("valor"), errors="coerce")
    if "atualizado_em" not in ind.columns:
        ind["atualizado_em"] = ""
    if "ano" not in ind.columns:
        ind["ano"] = pd.NA
    ind["ano"] = pd.to_numeric(ind.get("ano"), errors="coerce")

    # 1) Junção preferencial por código IBGE, quando disponível.
    base_key = out["codigo_ibge"].map(_codigo_limpo) if "codigo_ibge" in out.columns else pd.Series("", index=out.index)
    out["_codigo_ibge_join"] = base_key

    merged_any = False
    if "codigo_ibge" in ind.columns:
        ind_code = ind.copy()
        ind_code["_codigo_ibge_join"] = ind_code["codigo_ibge"].map(_codigo_limpo)
        ind_code = ind_code[ind_code["_codigo_ibge_join"].astype(str).str.len() > 0]
        if not ind_code.empty:
            ind_code = ind_code.sort_values(["_codigo_ibge_join", "indicador", "ano", "atualizado_em"], na_position="first")
            ind_code = ind_code.drop_duplicates(subset=["_codigo_ibge_join", "indicador"], keep="last")
            piv = ind_code.pivot_table(index="_codigo_ibge_join", columns="indicador", values="valor", aggfunc="sum").reset_index()
            piv.columns.name = None
            out = out.merge(piv, on="_codigo_ibge_join", how="left", suffixes=("", "_inep_code"))
            for col in INDICADORES_INEP_CONSOLIDACAO:
                code_col = f"{col}_inep_code"
                if code_col in out.columns:
                    out[col] = out[col].combine_first(out[code_col])
                    out = out.drop(columns=[code_col], errors="ignore")
            merged_any = True

    # 2) Fallback por nome normalizado/canônico.
    if "municipio" in ind.columns:
        ind_nome = ind.copy()
        ind_nome["_chave_municipio_mt"] = ind_nome["municipio"].map(_chave_municipio).map(_normalizar_alias_chave)
        ind_nome = ind_nome[ind_nome["_chave_municipio_mt"].astype(str).str.len() > 0]
        if not ind_nome.empty:
            ind_nome = ind_nome.sort_values(["_chave_municipio_mt", "indicador", "ano", "atualizado_em"], na_position="first")
            ind_nome = ind_nome.drop_duplicates(subset=["_chave_municipio_mt", "indicador"], keep="last")
            piv = ind_nome.pivot_table(index="_chave_municipio_mt", columns="indicador", values="valor", aggfunc="sum").reset_index()
            piv.columns.name = None
            out["_chave_municipio_mt"] = out["municipio"].map(_chave_municipio).map(_normalizar_alias_chave)
            out = out.merge(piv, on="_chave_municipio_mt", how="left", suffixes=("", "_inep_nome"))
            for col in INDICADORES_INEP_CONSOLIDACAO:
                nome_col = f"{col}_inep_nome"
                if nome_col in out.columns:
                    out[col] = out[col].combine_first(out[nome_col])
                    out = out.drop(columns=[nome_col], errors="ignore")
            merged_any = True

    out = out.drop(columns=["_codigo_ibge_join", "_chave_municipio_mt"], errors="ignore")
    for col in INDICADORES_INEP_CONSOLIDACAO:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def atualizar_base_municipal() -> dict:
    agora = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        municipios = pd.read_sql_query("SELECT codigo_ibge, municipio, regiao_saude, latitude, longitude FROM municipios", conn)
        estabelecimentos = pd.read_sql_query("SELECT * FROM estabelecimentos_saude", conn)
        equipes = pd.read_sql_query("SELECT * FROM equipes_aps", conn)
        profissionais = pd.read_sql_query("SELECT * FROM profissionais_cnes", conn)
        indicadores = pd.read_sql_query("SELECT * FROM indicadores_municipais", conn)

    municipios_db = _filtrar_municipios_mt(municipios, "municipio").drop_duplicates(subset=["municipio"], keep="last")

    # A base consolidada deve partir da lista oficial interna de municípios MT,
    # não apenas dos municípios que já existem no banco. Isso evita que um
    # município novo/sem carga anterior fique fora do painel (ex.: Lambari
    # D'Oeste ou Boa Esperança do Norte em bases recém-atualizadas).
    base = pd.DataFrame(DEFAULT_MUNICIPIOS)[["municipio", "regiao_saude"]].drop_duplicates(subset=["municipio"])
    base["_chave_municipio_mt"] = base["municipio"].map(_chave_municipio).map(_normalizar_alias_chave)
    base["codigo_ibge"] = base["_chave_municipio_mt"].map(_CODIGO_2025_POR_CHAVE)

    if not municipios_db.empty:
        mun_db = municipios_db[["municipio", "codigo_ibge", "latitude", "longitude"]].copy()
        base = base.merge(mun_db, on="municipio", how="left", suffixes=("", "_db"))
        base["codigo_ibge"] = base["codigo_ibge"].fillna(base.get("codigo_ibge_db"))
        base = base.drop(columns=["codigo_ibge_db", "_chave_municipio_mt"], errors="ignore")
    else:
        base["latitude"] = None
        base["longitude"] = None
        base = base.drop(columns=["_chave_municipio_mt"], errors="ignore")

    municipios = base[["codigo_ibge", "municipio", "regiao_saude", "latitude", "longitude"]].copy()

    if not estabelecimentos.empty:
        estabelecimentos = _enriquecer_municipio_por_codigo_ibge(estabelecimentos, municipios)
    if not estabelecimentos.empty and "municipio" in estabelecimentos.columns:
        estabelecimentos = _filtrar_municipios_mt(estabelecimentos, "municipio")
        estabelecimentos = deduplicar_estabelecimentos_saude(estabelecimentos)
        ubs = estabelecimentos.groupby("municipio").size().reset_index(name="total_ubs")
        base = base.merge(ubs, on="municipio", how="left")
    else:
        base["total_ubs"] = 0

    if not equipes.empty:
        equipes = _enriquecer_municipio_por_codigo_ibge(equipes, municipios)
    if not equipes.empty and "municipio" in equipes.columns:
        equipes = _filtrar_municipios_mt(equipes, "municipio")
        equipes["codigo_tipo_equipe"] = equipes.get("codigo_tipo_equipe", "").astype(str).str.extract(r"(\d+)", expand=False).fillna("")
        total = equipes.groupby("municipio").size().reset_index(name="total_equipes_aps")
        base = base.merge(total, on="municipio", how="left")
        for codigo in TIPOS_EQUIPE_CNES.keys():
            tmp = equipes[equipes["codigo_tipo_equipe"] == codigo].groupby("municipio").size().reset_index(name=f"total_equipes_{codigo}")
            base = base.merge(tmp, on="municipio", how="left")
    else:
        base["total_equipes_aps"] = 0
        for codigo in TIPOS_EQUIPE_CNES.keys():
            base[f"total_equipes_{codigo}"] = 0

    if not profissionais.empty:
        profissionais = _enriquecer_municipio_por_codigo_ibge(profissionais, municipios)
    if not profissionais.empty and "municipio" in profissionais.columns:
        profissionais = _filtrar_municipios_mt(profissionais, "municipio")
        prof = profissionais.groupby("municipio").size().reset_index(name="total_profissionais_aps")
        base = base.merge(prof, on="municipio", how="left")
    else:
        base["total_profissionais_aps"] = 0

    if not indicadores.empty:
        indicadores["municipio"] = indicadores["municipio"].astype(str).str.strip()
        indicadores = _filtrar_municipios_mt(indicadores, "municipio")

    base = _juntar_indicador(base, indicadores, ["populacao", "população", "populacao_estimada"], "populacao")

    # Área territorial: a fonte oficial não deve ser o cálculo geográfico local.
    # Primeiro tenta usar a planilha oficial IBGE Áreas Territoriais 2025; se
    # ainda não houver internet/xlrd/cache, cai para o indicador previamente
    # carregado, mantendo rastreabilidade pela tela de qualidade dos dados.
    areas_oficiais = baixar_areas_oficiais_ibge_2025_mt(forcar=False)
    if not areas_oficiais.empty:
        areas_oficiais = areas_oficiais[["codigo_ibge", "area_km2"]].copy()
        areas_oficiais["codigo_ibge"] = areas_oficiais["codigo_ibge"].astype(str).str[:7]
        base["codigo_ibge"] = base["codigo_ibge"].astype(str).str[:7]
        base = base.merge(areas_oficiais, on="codigo_ibge", how="left")
    else:
        base = _juntar_indicador(base, indicadores, ["area_territorial_km2", "area_densidade_territorial"], "area_km2")

    # A densidade consolidada será recalculada no final com população oficial
    # e área oficial. Não reaproveitar densidade antiga derivada de fontes
    # mistas para evitar divergência.
    base["densidade_hab_km2"] = None

    base = _juntar_indicador(base, indicadores, ["perfil_urbano_rural"], "perfil_urbano_rural")
    base = _juntar_indicador(base, indicadores, ["indicadores_demograficos_9515"], "indicador_demografico")
    base = _juntar_indicador(base, indicadores, ["alfabetizacao_9543"], "taxa_alfabetizacao")
    base = _juntar_indicador(base, indicadores, ["nivel_instrucao_10061"], "nivel_instrucao")
    base = _juntar_indicador(base, indicadores, ["renda_censo_2022"], "renda_censo_2022")
    base = _juntar_indicador(base, indicadores, ["saneamento_censo_2022"], "saneamento_censo_2022")
    base = _juntar_indicador(base, indicadores, ["pib_municipal_precos_correntes"], "pib_municipal_precos_correntes")
    base = _juntar_indicador(base, indicadores, ["pib_per_capita"], "pib_per_capita")
    base = _juntar_indicador(base, indicadores, ["populacao_indigena_censo_2022"], "populacao_indigena")
    base = _juntar_indicador(base, indicadores, ["populacao_quilombola_censo_2022"], "populacao_quilombola")

    # Camada CNES/DATASUS: indicadores complementares agregados por município.
    base = _juntar_indicador(base, indicadores, ["leitos_sus_total"], "total_leitos_sus", contem=False)
    base = _juntar_indicador(base, indicadores, ["nascidos_vivos_sinasc"], "nascidos_vivos", contem=True)
    base = _juntar_indicador(base, indicadores, ["obitos_sim"], "obitos", contem=True)
    base = _juntar_indicador(base, indicadores, ["obitos_infantis_sim"], "obitos_infantis", contem=True)

    # Camada MDS/socioassistencial: insumos brutos para análise de vulnerabilidade.
    # Não calculamos índice automaticamente nesta etapa; os campos entram como variáveis auditáveis.
    # Para MDS, usar correspondência EXATA. Se usarmos contains,
    # cadunico_familias pode capturar cadunico_familias_pobreza/extrema_pobreza e distorcer a base.
    base = _juntar_indicador(base, indicadores, ["cadunico_familias"], "cadunico_familias", contem=False)
    base = _juntar_indicador(base, indicadores, ["cadunico_pessoas"], "cadunico_pessoas", contem=False)
    base = _juntar_indicador(base, indicadores, ["cadunico_familias_pobreza"], "cadunico_familias_pobreza", contem=False)
    base = _juntar_indicador(base, indicadores, ["cadunico_pessoas_pobreza"], "cadunico_pessoas_pobreza", contem=False)
    base = _juntar_indicador(base, indicadores, ["cadunico_familias_extrema_pobreza"], "cadunico_familias_extrema_pobreza", contem=False)
    base = _juntar_indicador(base, indicadores, ["cadunico_pessoas_extrema_pobreza"], "cadunico_pessoas_extrema_pobreza", contem=False)
    base = _juntar_indicador(base, indicadores, ["bolsa_familia_familias"], "bolsa_familia_familias", contem=False)
    base = _juntar_indicador(base, indicadores, ["bolsa_familia_valor_repassado"], "bolsa_familia_valor_repassado", contem=False)
    base = _juntar_indicador(base, indicadores, ["bpc_total"], "bpc_total", contem=False)
    base = _juntar_indicador(base, indicadores, ["bpc_idoso"], "bpc_idoso", contem=False)
    base = _juntar_indicador(base, indicadores, ["bpc_pcd"], "bpc_pcd", contem=False)

    # Camada INEP/Censo Escolar: integrar usando primeiro código IBGE e,
    # como fallback, nome canônico do município. Isso evita campos vazios na
    # base consolidada quando a auditoria INEP está preenchida, mas há diferença
    # de grafia ou chave entre tabelas.
    base = _juntar_inep_por_codigo_ou_municipio(base, indicadores)

    # Índice de vulnerabilidade: não usar renda/saneamento diretamente como se fossem
    # um índice composto. Esses campos entram como insumos; o índice será calculado
    # em etapa própria quando a metodologia estiver definida.
    vul = pd.DataFrame()
    if not indicadores.empty and "indicador" in indicadores.columns:
        indicador_norm = indicadores["indicador"].astype(str).str.lower()
        vul = indicadores[indicador_norm.str.contains("indice_vulnerabilidade|índice_vulnerabilidade|vulnerabilidade_composta", na=False)].copy()
    if not vul.empty:
        vul["ano"] = pd.to_numeric(vul.get("ano"), errors="coerce")
        vul["valor"] = pd.to_numeric(vul.get("valor"), errors="coerce")
        vul = vul.sort_values(["municipio", "ano", "atualizado_em"], na_position="first")
        vul = vul.groupby("municipio").tail(1)[["municipio", "valor"]].rename(columns={"valor": "indice_vulnerabilidade"})
        base = base.merge(vul, on="municipio", how="left")
    else:
        base["indice_vulnerabilidade"] = None

    for col in ["total_ubs", "total_equipes_aps", "total_profissionais_aps"] + [f"total_equipes_{c}" for c in TIPOS_EQUIPE_CNES.keys()]:
        if col not in base.columns:
            base[col] = 0
        base[col] = base[col].fillna(0).astype(int)

    base["populacao"] = pd.to_numeric(base.get("populacao"), errors="coerce")
    # Para o ciclo atual, a população municipal é travada na estimativa oficial
    # IBGE 2025. Isso evita distorções de escala vindas do SIDRA/PDF parser
    # (ex.: 16.839 lido como 168.390) e garante total estadual de 3.893.659.
    chaves_pop = base["municipio"].map(_chave_municipio).map(_normalizar_alias_chave)
    pop_oficial = chaves_pop.map(_POPULACAO_2025_POR_CHAVE)
    base.loc[pop_oficial.notna(), "populacao"] = pop_oficial[pop_oficial.notna()].astype(float)
    if base["populacao"].notna().any() and float(base["populacao"].max()) > 1_000_000:
        # Fallback para bases futuras sem tabela oficial parametrizada.
        base["populacao"] = base["populacao"] / 10
    base["populacao"] = base["populacao"].round().astype("Int64")

    # Correção defensiva para cargas antigas de PIB importadas antes do ajuste
    # do parser numérico. Nessas cargas, valores como 169373.0 viraram 1693730
    # porque o ponto decimal foi removido. Como o PIB municipal do SIDRA fica
    # em mil reais, um per capita estadual mediano acima de R$ 250 mil indica
    # escala incompatível e aplicamos correção única dividindo por 10.
    if "pib_municipal_precos_correntes" in base.columns:
        pib = pd.to_numeric(base["pib_municipal_precos_correntes"], errors="coerce")
        pop = pd.to_numeric(base["populacao"], errors="coerce")
        pib_pc_estimado = (pib * 1000) / pop.replace({0: pd.NA})
        if pib_pc_estimado.notna().sum() >= 20 and float(pib_pc_estimado.median()) > 250_000:
            base["pib_municipal_precos_correntes"] = (pib / 10).round(3)

    for col in [
        "area_km2", "densidade_hab_km2", "indice_vulnerabilidade", "perfil_urbano_rural",
        "indicador_demografico", "taxa_alfabetizacao", "nivel_instrucao", "renda_censo_2022",
        "saneamento_censo_2022", "pib_municipal_precos_correntes", "pib_per_capita",
        "populacao_indigena", "populacao_quilombola",
        "total_leitos_sus", "nascidos_vivos", "obitos", "obitos_infantis",
        "cadunico_familias", "cadunico_pessoas", "cadunico_familias_pobreza", "cadunico_pessoas_pobreza",
        "cadunico_familias_extrema_pobreza", "cadunico_pessoas_extrema_pobreza",
        "bolsa_familia_familias", "bolsa_familia_valor_repassado", "bpc_total", "bpc_idoso", "bpc_pcd",
        "escolas_total", "escolas_urbanas", "escolas_rurais", "escolas_indigenas", "escolas_quilombolas",
        "escolas_educacao_especial_aee", "matriculas_total", "matriculas_educacao_especial",
    ]:
        if col in base.columns:
            base[col] = pd.to_numeric(base[col], errors="coerce")

    # Higienização defensiva de campos socioeconômicos importados de tabelas SIDRA
    # ainda não totalmente parametrizadas. Algumas consultas retornam a população
    # total ou um valor escalado por 10 em vez de recortes urbano/rural, indígena
    # ou quilombola. Para não induzir interpretação errada, valores incompatíveis
    # com a população municipal são preservados no histórico bruto, mas não entram
    # na base consolidada final.
    if "populacao" in base.columns:
        pop_num = pd.to_numeric(base["populacao"], errors="coerce")
        for col in ["populacao_indigena", "populacao_quilombola"]:
            if col in base.columns:
                serie = pd.to_numeric(base[col], errors="coerce")
                suspeito = serie.notna() & pop_num.notna() & ((serie > pop_num) | (serie < 0))
                base.loc[suspeito, col] = pd.NA
        if "perfil_urbano_rural" in base.columns:
            serie = pd.to_numeric(base["perfil_urbano_rural"], errors="coerce")
            # O campo consolidado só deve receber percentual/índice já tratado.
            # Contagens absolutas retornadas pelo SIDRA ficam pendentes até a
            # modelagem detalhada urbano/rural por categoria.
            suspeito = serie.notna() & ((serie > 100) | (serie < 0))
            base.loc[suspeito, "perfil_urbano_rural"] = pd.NA
        if "pib_per_capita" in base.columns:
            serie = pd.to_numeric(base["pib_per_capita"], errors="coerce")
            # A carga atual do PIB trouxe PIB corrente, mas PIB per capita veio
            # nulo no SIDRA. Não estimar automaticamente; manter em branco até
            # a variável oficial ser corrigida no conector.
            base.loc[serie.isna(), "pib_per_capita"] = pd.NA

    mask_calc = base["densidade_hab_km2"].isna() & base["populacao"].notna() & base["area_km2"].notna() & (base["area_km2"] > 0)
    base.loc[mask_calc, "densidade_hab_km2"] = base.loc[mask_calc, "populacao"].astype(float) / base.loc[mask_calc, "area_km2"].astype(float)

    def prioridade(row):
        score = 0
        if row.get("total_ubs", 0) == 0:
            score += 3
        if row.get("total_equipes_aps", 0) == 0:
            score += 3
        if pd.notna(row.get("indice_vulnerabilidade")):
            try:
                if float(row.get("indice_vulnerabilidade")) >= 0.7:
                    score += 2
            except Exception:
                pass
        if score >= 5:
            return "Crítica"
        if score >= 3:
            return "Alta"
        if score >= 1:
            return "Média"
        return "Monitoramento"

    base["nivel_prioridade"] = base.apply(prioridade, axis=1)
    base["observacao"] = "Base consolidada automaticamente a partir das tabelas estruturantes e indicadores territoriais/socioeconômicos importados."
    base["atualizado_em"] = agora

    cols = [
        "codigo_ibge", "municipio", "regiao_saude", "populacao", "area_km2", "densidade_hab_km2",
        "total_ubs", "total_equipes_aps", "total_profissionais_aps",
    ] + [f"total_equipes_{c}" for c in TIPOS_EQUIPE_CNES.keys()] + [
        "indice_vulnerabilidade", "perfil_urbano_rural", "indicador_demografico", "taxa_alfabetizacao",
        "nivel_instrucao", "renda_censo_2022", "saneamento_censo_2022",
        "pib_municipal_precos_correntes", "pib_per_capita", "populacao_indigena",
        "populacao_quilombola", "total_leitos_sus", "nascidos_vivos", "obitos", "obitos_infantis",
        "cadunico_familias", "cadunico_pessoas", "cadunico_familias_pobreza", "cadunico_pessoas_pobreza",
        "cadunico_familias_extrema_pobreza", "cadunico_pessoas_extrema_pobreza",
        "bolsa_familia_familias", "bolsa_familia_valor_repassado", "bpc_total", "bpc_idoso", "bpc_pcd",
        "escolas_total", "escolas_urbanas", "escolas_rurais", "escolas_indigenas", "escolas_quilombolas",
        "escolas_educacao_especial_aee", "matriculas_total", "matriculas_educacao_especial",
        "latitude", "longitude", "nivel_prioridade", "observacao", "atualizado_em",
    ]
    for col in cols:
        if col not in base.columns:
            base[col] = None
    base = base[cols]

    with db_session() as conn:
        conn.execute("DELETE FROM base_municipal_consolidada")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'base_municipal_consolidada'")
        base.to_sql("base_municipal_consolidada", conn, if_exists="append", index=False)
    return {"municipios": len(base), "atualizado_em": agora}
