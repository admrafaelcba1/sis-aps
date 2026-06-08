from __future__ import annotations

from typing import Any, List, Optional

import pandas as pd
import streamlit as st

try:
    from utils.cache_dados_aps import carregar_cache_aps_para_session_state, salvar_cache_aps
except Exception:  # pragma: no cover
    carregar_cache_aps_para_session_state = None
    salvar_cache_aps = None

CODIGOS_EQUIPE_APS = {"70", "71", "72", "73", "74", "76"}
MAPA_TIPO_EQUIPE = {
    "70": "eSF / Equipe de Saúde da Família",
    "71": "eSB / Equipe de Saúde Bucal",
    "72": "eMulti / Equipe Multiprofissional",
    "73": "eCR / Consultório na Rua",
    "74": "eAPP / Equipe de Atenção Primária Prisional",
    "76": "eAP / Equipe de Atenção Primária",
}


def _normalizar_colunas(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = (
        out.columns.astype(str)
        .str.strip()
        .str.lower()
        .str.normalize("NFKD")
        .str.encode("ascii", errors="ignore")
        .str.decode("utf-8")
        .str.replace(r"[^a-z0-9]+", "_", regex=True)
        .str.strip("_")
    )
    return out


def _num(s: Any, default: float = 0.0) -> float:
    try:
        v = pd.to_numeric(s, errors="coerce")
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def _serie_num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([0] * len(df), index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce").fillna(0)


def _enriquecer_municipio_regiao(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    # 1) Usa base UBS/CNES por CNES quando disponível.
    ubs = st.session_state.get("geo_ubs_df")
    if isinstance(ubs, pd.DataFrame) and not ubs.empty and "cnes" in out.columns:
        u = _normalizar_colunas(ubs)
        if "cnes" in u.columns:
            cols = [c for c in ["cnes", "municipio", "codigo_ibge", "regiao_saude"] if c in u.columns]
            if len(cols) >= 2:
                u = u[cols].drop_duplicates(subset=["cnes"], keep="first")
                out["cnes"] = out["cnes"].astype(str).str.strip()
                u["cnes"] = u["cnes"].astype(str).str.strip()
                out = out.merge(u, on="cnes", how="left", suffixes=("", "_ubs"))
                for col in ["municipio", "codigo_ibge", "regiao_saude"]:
                    aux = f"{col}_ubs"
                    if aux in out.columns:
                        if col not in out.columns:
                            out[col] = ""
                        out[col] = out[col].where(out[col].astype(str).str.strip().ne(""), out[aux])
                        out = out.drop(columns=[aux])

    # 2) Usa base municipal/API por código IBGE quando disponível.
    base = st.session_state.get("ubs_base_automatica_ibge")
    if isinstance(base, pd.DataFrame) and not base.empty and "codigo_ibge" in out.columns:
        b = _normalizar_colunas(base)
        if "codigo_ibge" in b.columns:
            b["codigo_ibge"] = b["codigo_ibge"].astype(str).str.extract(r"(\d{6,7})")[0].fillna("").str[:6]
            out["codigo_ibge"] = out["codigo_ibge"].astype(str).str.extract(r"(\d{6,7})")[0].fillna("").str[:6]
            rename = {}
            if "regiao_saude" not in b.columns and "regiao_saude_sus" in b.columns:
                rename["regiao_saude_sus"] = "regiao_saude"
            b = b.rename(columns=rename)
            cols = [c for c in ["codigo_ibge", "municipio", "regiao_saude"] if c in b.columns]
            if len(cols) >= 2:
                b = b[cols].drop_duplicates(subset=["codigo_ibge"], keep="first")
                out = out.merge(b, on="codigo_ibge", how="left", suffixes=("", "_base"))
                for col in ["municipio", "regiao_saude"]:
                    aux = f"{col}_base"
                    if aux in out.columns:
                        if col not in out.columns:
                            out[col] = ""
                        out[col] = out[col].where(out[col].astype(str).str.strip().ne(""), out[aux])
                        out = out.drop(columns=[aux])
    return out


def _somar_profissionais(df: pd.DataFrame) -> int:
    if not isinstance(df, pd.DataFrame) or df.empty or "total_profissionais" not in df.columns:
        return 0
    return int(pd.to_numeric(df["total_profissionais"], errors="coerce").fillna(0).sum())


def _tentar_reconsolidar_profissionais() -> Optional[pd.DataFrame]:
    """Regera a base agregada de profissionais quando o cache antigo ficou zerado.

    A contagem correta depende de duas bases no session_state/cache:
    - geo_equipes_ine_df: equipes válidas filtradas;
    - geo_profissionais_equipes_df: registros extraídos de ProfissionaisEquipesBrasil.txt.

    Se o arquivo de profissionais ainda não tiver sido reprocessado depois do patch, a função
    devolve None e o painel continua exibindo as equipes, mas sem profissionais.
    """
    equipes = st.session_state.get("geo_equipes_ine_df")
    profissionais = st.session_state.get("geo_profissionais_equipes_df")
    if not isinstance(equipes, pd.DataFrame) or equipes.empty:
        return None
    if not isinstance(profissionais, pd.DataFrame) or profissionais.empty:
        return None
    try:
        from ui.georreferenciamento_aps import consolidar_profissionais_por_equipe
        novo = consolidar_profissionais_por_equipe(equipes, profissionais)
    except Exception:
        return None
    if isinstance(novo, pd.DataFrame) and not novo.empty:
        st.session_state["geo_profissionais_ine_resumo_df"] = novo
        if salvar_cache_aps is not None:
            try:
                salvar_cache_aps(keys=["geo_equipes_ine_df", "geo_profissionais_equipes_df", "geo_profissionais_ine_resumo_df"])
            except Exception:
                pass
        return novo
    return None


def _carregar_bases_profissionais() -> pd.DataFrame:
    if carregar_cache_aps_para_session_state is not None:
        try:
            carregar_cache_aps_para_session_state(
                keys=["geo_equipes_ine_df", "geo_profissionais_equipes_df", "geo_profissionais_ine_resumo_df"],
                sobrescrever=False,
            )
        except Exception:
            pass

    resumo = st.session_state.get("geo_profissionais_ine_resumo_df")
    # Se existir um resumo antigo zerado, tenta reconstruir usando o arquivo
    # ProfissionaisEquipesBrasil.txt já processado e salvo no cache.
    if isinstance(resumo, pd.DataFrame) and not resumo.empty and _somar_profissionais(resumo) == 0:
        reconstruido = _tentar_reconsolidar_profissionais()
        if isinstance(reconstruido, pd.DataFrame) and not reconstruido.empty and _somar_profissionais(reconstruido) > 0:
            resumo = reconstruido

    if isinstance(resumo, pd.DataFrame) and not resumo.empty:
        df = _normalizar_colunas(resumo)
    else:
        equipes = st.session_state.get("geo_equipes_ine_df")
        if not isinstance(equipes, pd.DataFrame) or equipes.empty:
            return pd.DataFrame()
        df = _normalizar_colunas(equipes)
        df["total_profissionais"] = 0
        df["cbos_distintos"] = 0

    if "tipo_equipe_codigo" in df.columns:
        df["tipo_equipe_codigo"] = df["tipo_equipe_codigo"].astype(str).str.replace(r"\.0$", "", regex=True).str.extract(r"(70|71|72|73|74|76)")[0]
    else:
        df["tipo_equipe_codigo"] = ""
    df = df[df["tipo_equipe_codigo"].isin(CODIGOS_EQUIPE_APS)].copy()
    if df.empty:
        return df

    df = _enriquecer_municipio_regiao(df)

    for col in ["municipio", "regiao_saude", "codigo_ibge", "cnes", "ine", "nome_equipe", "tipo_equipe"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].astype(str).replace({"nan": "", "None": ""}).str.strip()

    # Usa sempre a nomenclatura oficial definida para este painel, evitando
    # rótulos antigos salvos no cache, especialmente nos códigos 74 e 76.
    df["tipo_equipe"] = df["tipo_equipe_codigo"].map(MAPA_TIPO_EQUIPE).fillna(df.get("tipo_equipe", df["tipo_equipe_codigo"]))

    df["total_profissionais"] = _serie_num(df, "total_profissionais").astype(int)
    df["cbos_distintos"] = _serie_num(df, "cbos_distintos").astype(int)
    return df.reset_index(drop=True)


def _fmt_int(v: Any) -> str:
    try:
        return f"{int(round(float(v))):,}".replace(",", ".")
    except Exception:
        return "0"


def _filtrar(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    c1, c2, c3 = st.columns([1.2, 1.2, 1])
    regioes = sorted([x for x in df.get("regiao_saude", pd.Series(dtype=str)).astype(str).unique() if x and x.lower() not in ["nan", "none"]])
    municipios = sorted([x for x in df.get("municipio", pd.Series(dtype=str)).astype(str).unique() if x and x.lower() not in ["nan", "none"]])
    codigos = sorted(df["tipo_equipe_codigo"].dropna().astype(str).unique())

    with c1:
        regiao = st.selectbox("Região de Saúde", ["Todas"] + regioes)
    with c2:
        municipio = st.selectbox("Município", ["Todos"] + municipios)
    with c3:
        codigo = st.selectbox("Código de equipe CNES/INE", ["Todos"] + codigos, format_func=lambda x: x if x == "Todos" else f"{x} — {MAPA_TIPO_EQUIPE.get(x, '')}")

    out = df.copy()
    if regiao != "Todas":
        out = out[out["regiao_saude"].eq(regiao)].copy()
    if municipio != "Todos":
        out = out[out["municipio"].eq(municipio)].copy()
    if codigo != "Todos":
        out = out[out["tipo_equipe_codigo"].eq(codigo)].copy()
    return out


def _cards(df: pd.DataFrame):
    c1, c2, c3, c4, c5 = st.columns(5)
    profissionais = int(df["total_profissionais"].sum()) if "total_profissionais" in df.columns else 0
    equipes = len(df)
    equipes_com_prof = int((df["total_profissionais"] > 0).sum()) if "total_profissionais" in df.columns else 0
    municipios = df["municipio"].replace("", pd.NA).dropna().nunique() if "municipio" in df.columns else 0
    media = round(profissionais / equipes_com_prof, 1) if equipes_com_prof else 0
    c1.metric("Profissionais vinculados", _fmt_int(profissionais))
    c2.metric("Equipes/INE analisadas", _fmt_int(equipes))
    c3.metric("Equipes com profissional", _fmt_int(equipes_com_prof))
    c4.metric("Municípios", _fmt_int(municipios))
    c5.metric("Média prof./equipe", f"{media:.1f}".replace(".", ","))


def _tabela_por_codigo(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    tab = (
        df.groupby(["tipo_equipe_codigo", "tipo_equipe"], dropna=False)
        .agg(
            equipes_ine=("ine", "count"),
            profissionais=("total_profissionais", "sum"),
            equipes_com_profissionais=("total_profissionais", lambda x: int((pd.to_numeric(x, errors="coerce").fillna(0) > 0).sum())),
            municipios=("municipio", lambda x: int(pd.Series(x).replace("", pd.NA).dropna().nunique())),
            cnes=("cnes", lambda x: int(pd.Series(x).replace("", pd.NA).dropna().nunique())),
        )
        .reset_index()
    )
    tab["media_profissionais_por_equipe_com_profissional"] = tab.apply(
        lambda r: round(float(r["profissionais"]) / float(r["equipes_com_profissionais"]), 1) if r["equipes_com_profissionais"] else 0,
        axis=1,
    )
    return tab.sort_values(["tipo_equipe_codigo"]).reset_index(drop=True)


def _tabela_por_municipio_codigo(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    tab = (
        df.groupby(["regiao_saude", "municipio", "codigo_ibge", "tipo_equipe_codigo", "tipo_equipe"], dropna=False)
        .agg(
            equipes_ine=("ine", "count"),
            profissionais=("total_profissionais", "sum"),
            equipes_com_profissionais=("total_profissionais", lambda x: int((pd.to_numeric(x, errors="coerce").fillna(0) > 0).sum())),
            cnes_distintos=("cnes", lambda x: int(pd.Series(x).replace("", pd.NA).dropna().nunique())),
        )
        .reset_index()
        .sort_values(["profissionais", "equipes_ine"], ascending=False)
    )
    return tab.reset_index(drop=True)


def render_dashboard_profissionais_aps():
    st.markdown(
        """
        <div class="hero">
            <h1>Dashboard Profissionais APS — CNES/INE</h1>
            <p>Quantidade de profissionais vinculados às equipes APS dos tipos 70, 71, 72, 73, 74 e 76, a partir do arquivo EQUIPES BRASIL.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.info(
        "Onde esta informação é criada: **Estudo Territorial | Georreferenciamento APS → aba 1. CNES/INE automático → upload do arquivo EQUIPESBRASIL_YYYYMM.ZIP**. "
        "O sistema lê `EQUIPESValidasBrasil.txt` e `ProfissionaisEquipesBrasil.txt`, grava `geo_equipes_ine_df`, `geo_profissionais_equipes_df` e `geo_profissionais_ine_resumo_df` no cache."
    )

    df = _carregar_bases_profissionais()
    if df.empty:
        st.warning("Ainda não há base de profissionais por equipe carregada.")
        st.markdown(
            """
            Para alimentar este painel:
            1. Vá em **Estudo Territorial | Georreferenciamento APS**;
            2. Abra a aba **1. CNES/INE automático**;
            3. Faça upload do arquivo **EQUIPESBRASIL_YYYYMM.ZIP**;
            4. O ZIP precisa conter `EQUIPESValidasBrasil.txt` e `ProfissionaisEquipesBrasil.txt`;
            5. Volte para este dashboard.
            """
        )
        return

    st.caption("Códigos considerados nesta visão: 70, 71, 72, 73, 74 e 76. CPF/CNS não são exibidos; a contagem é agregada e anonimizada.")
    filtrado = _filtrar(df)
    _cards(filtrado)

    st.divider()
    st.subheader("1. Resumo por código de equipe")
    tab_codigo = _tabela_por_codigo(filtrado)
    st.dataframe(tab_codigo, use_container_width=True, hide_index=True)

    if not tab_codigo.empty:
        graf = tab_codigo.set_index("tipo_equipe_codigo")[["profissionais", "equipes_ine"]]
        st.bar_chart(graf, use_container_width=True)

    st.subheader("2. Profissionais por município e código de equipe")
    tab_mun = _tabela_por_municipio_codigo(filtrado)
    st.dataframe(tab_mun, use_container_width=True, hide_index=True)

    if not tab_mun.empty:
        top = tab_mun.groupby("municipio", dropna=False)["profissionais"].sum().sort_values(ascending=False).head(15)
        st.caption("Top 15 municípios por quantidade de profissionais vinculados às equipes filtradas")
        st.bar_chart(top, use_container_width=True)

    st.subheader("3. Detalhe por CNES, INE e equipe")
    cols = [
        "regiao_saude", "municipio", "codigo_ibge", "cnes", "ine", "tipo_equipe_codigo", "tipo_equipe",
        "nome_equipe", "total_profissionais", "cbos_distintos", "fonte_profissionais"
    ]
    detalhe = filtrado[[c for c in cols if c in filtrado.columns]].copy()
    detalhe = detalhe.sort_values(["municipio", "tipo_equipe_codigo", "cnes", "ine"])
    st.dataframe(detalhe, use_container_width=True, hide_index=True)

    csv = detalhe.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "Baixar detalhamento CNES/INE/profissionais (.csv)",
        data=csv,
        file_name="profissionais_aps_por_cnes_ine.csv",
        mime="text/csv",
        use_container_width=True,
    )

    with st.expander("Observação técnica sobre a metodologia de contagem"):
        st.markdown(
            """
            - A leitura usa o arquivo oficial **EQUIPES BRASIL** enviado manualmente.
            - `EQUIPESValidasBrasil.txt` identifica município, CNES, INE, tipo de equipe e nome da equipe.
            - `ProfissionaisEquipesBrasil.txt` é usado para contar profissionais vinculados às equipes.
            - Para proteção de dados pessoais, o painel não exibe CPF/CNS. O sistema usa hash técnico apenas para contagem distinta.
            - A contagem deve ser validada pela Coordenadoria APS quando for usada para decisão formal, porque depende da atualização correta do CNES na competência analisada.
            """
        )
