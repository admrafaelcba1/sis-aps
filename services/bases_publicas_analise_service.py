
from __future__ import annotations

import re
from typing import Any

import pandas as pd

from database.connection import get_connection
from database.queries import read_table
from services.ibge_dicionario_variaveis_service import aplicar_dicionario_linha
from services.ibge_curadoria_indicadores_service import aplicar_curadoria_indicador


def _normalizar_nome(valor: Any) -> str:
    texto = "" if valor is None else str(valor)
    texto = texto.strip().lower()
    texto = re.sub(r"[^a-z0-9]+", "_", texto)
    return re.sub(r"_+", "_", texto).strip("_")


def listar_tabelas_base_publica() -> pd.DataFrame:
    registros = []
    with get_connection() as con:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        for row in rows:
            tabela = row[0]
            if not str(tabela).startswith("base_publica_"):
                continue
            try:
                qtd = con.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]
            except Exception:
                qtd = 0
            registros.append({"tabela": tabela, "linhas": int(qtd or 0)})
    return pd.DataFrame(registros)


def classificar_indicador_publico(coluna: str) -> str:
    c = _normalizar_nome(coluna)
    if any(k in c for k in ["renda", "rendimento", "salario", "pobreza", "baixa_renda"]):
        return "Renda e vulnerabilidade econômica"
    if any(k in c for k in ["trabalho", "ocupacao", "emprego", "desocupacao", "pea"]):
        return "Trabalho e ocupação"
    if any(k in c for k in ["alfabet", "escolar", "instrucao", "ideb", "inse", "distorcao", "aprovacao", "educacao"]):
        return "Escolaridade e educação"
    if any(k in c for k in ["saneamento", "esgoto", "agua", "abastecimento", "lixo", "domicilio", "domicilios", "entorno"]):
        return "Saneamento, domicílios e entorno"
    if any(k in c for k in ["obito", "mortalidade", "morte", "sim"]):
        return "Epidemiologia — mortalidade"
    if any(k in c for k in ["nascido", "nascidos", "sinasc", "pre_natal", "baixo_peso", "mae"]):
        return "Epidemiologia — materno-infantil"
    if any(k in c for k in ["sinan", "dengue", "tuberculose", "hanseniase", "agravo", "violencia", "notificacao"]):
        return "Epidemiologia — agravos"
    if any(k in c for k in ["idhm", "vulnerabilidade", "desenvolvimento"]):
        return "Desenvolvimento humano"
    if any(k in c for k in ["populacao", "idade", "idoso", "crianca", "jovem", "sexo", "raca", "cor"]):
        return "Demografia e equidade"
    if re.fullmatch(r"v\d{4,}", c):
        return "Indicadores IBGE Censo 2022"
    return "Outros indicadores públicos"


def _is_ibge_setores(tabela: str) -> bool:
    return str(tabela).startswith("base_publica_ibge_setores_")


def _colunas_numericas_validas(df: pd.DataFrame, tabela: str = "") -> list[str]:
    """Seleciona somente colunas que são indicadores.

    Regra especial IBGE setores:
    - Indicadores oficiais entram quase sempre como V0001, V0002, V00644 etc.
    - Códigos territoriais iniciados por CD_, NM_, AREA_, SITUAÇÃO etc. NÃO entram.
    - Isso evita somar códigos como CD_REGIAO, CD_RGI, CD_AGLOM e similares.
    """
    ignorar = {
        "municipio", "municipio_origem", "municipio_norm", "regiao_saude", "codigo_ibge",
        "municipio_mt_encontrado", "eixo", "tipo_base", "fonte", "ano_referencia",
        "arquivo_origem", "tabela_destino", "data_importacao", "categoria_principal",
    }

    cols = []
    for c in df.columns:
        cn = _normalizar_nome(c)

        if cn in ignorar:
            continue

        if _is_ibge_setores(tabela):
            # No IBGE setores, para a consolidação analítica, só entram variáveis Vxxxx.
            # Identificadores e classificações territoriais não são indicadores.
            if re.fullmatch(r"v\d{4,}", cn):
                cols.append(c)
            continue

        # Bases genéricas: tenta detectar numéricos, mas ignora identificadores óbvios.
        if (
            cn.startswith("cd_") or cn.startswith("cod_") or cn.startswith("codigo_")
            or cn.startswith("nm_") or cn.startswith("nome_")
            or cn in {"id", "setor", "cd_setor", "geocodigo"}
        ):
            continue

        s = pd.to_numeric(df[c].replace({"X": pd.NA, "x": pd.NA}) if isinstance(df[c], pd.Series) else df[c], errors="coerce")
        if s.notna().sum() > 0:
            cols.append(c)
    return cols


def _codigo_municipio_por_setor(df: pd.DataFrame) -> pd.Series:
    if "codigo_ibge" in df.columns:
        cod = df["codigo_ibge"].astype(str).str.replace(r"\D", "", regex=True).str[:7]
        if cod.str.len().ge(7).any():
            return cod
    for col in ["CD_MUN", "cd_mun", "Cd_mun"]:
        if col in df.columns:
            return df[col].astype(str).str.replace(r"\D", "", regex=True).str[:7]
    for col in ["CD_SETOR", "CD_setor", "cd_setor", "setor", "SETOR"]:
        if col in df.columns:
            return df[col].astype(str).str.replace(r"\D", "", regex=True).str[:7]
    return pd.Series([""] * len(df), index=df.index)


def _mapa_municipio_ibge_basico() -> pd.DataFrame:
    basico = read_table("base_publica_ibge_setores_basico")
    if basico.empty:
        return pd.DataFrame(columns=["codigo_ibge", "municipio", "regiao_saude"])
    df = basico.copy()
    df["codigo_ibge"] = _codigo_municipio_por_setor(df)
    if "NM_MUN" in df.columns:
        df["municipio_ref"] = df["NM_MUN"].astype(str)
    elif "municipio" in df.columns:
        df["municipio_ref"] = df["municipio"].astype(str)
    else:
        df["municipio_ref"] = ""
    if "regiao_saude" in df.columns:
        df["regiao_saude_ref"] = df["regiao_saude"].astype(str)
    else:
        df["regiao_saude_ref"] = ""
    mapa = (
        df[["codigo_ibge", "municipio_ref", "regiao_saude_ref"]]
        .dropna(subset=["codigo_ibge"])
        .drop_duplicates(subset=["codigo_ibge"])
        .rename(columns={"municipio_ref": "municipio", "regiao_saude_ref": "regiao_saude"})
    )
    mapa = mapa[mapa["codigo_ibge"].astype(str).str.startswith("51", na=False)]
    return mapa


def _enriquecer_municipio(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["codigo_ibge"] = _codigo_municipio_por_setor(out)
    mapa = _mapa_municipio_ibge_basico()
    if not mapa.empty:
        # remove colunas conflitantes vazias/frágeis
        for col in ["municipio", "regiao_saude"]:
            if col in out.columns:
                out = out.drop(columns=[col])
        out = out.merge(mapa, on="codigo_ibge", how="left")
    else:
        if "municipio" not in out.columns:
            out["municipio"] = ""
        if "regiao_saude" not in out.columns:
            out["regiao_saude"] = ""
    if "NM_MUN" in out.columns:
        out["municipio"] = out["municipio"].fillna(out["NM_MUN"].astype(str))
        out.loc[out["municipio"].astype(str).str.strip().eq(""), "municipio"] = out["NM_MUN"].astype(str)
    out = out[out["codigo_ibge"].astype(str).str.startswith("51", na=False)].copy()
    return out


def inventariar_indicadores_bases_publicas() -> pd.DataFrame:
    tabelas = listar_tabelas_base_publica()
    registros = []
    if tabelas.empty:
        return pd.DataFrame()
    for tabela in tabelas["tabela"].tolist():
        df = read_table(tabela)
        if df.empty:
            continue
        if _is_ibge_setores(tabela):
            df = _enriquecer_municipio(df)
        eixo = df["eixo"].iloc[0] if "eixo" in df.columns and len(df) else ""
        tipo_base = df["tipo_base"].iloc[0] if "tipo_base" in df.columns and len(df) else ""
        fonte = df["fonte"].iloc[0] if "fonte" in df.columns and len(df) else ""
        ano = df["ano_referencia"].iloc[0] if "ano_referencia" in df.columns and len(df) else ""
        municipios = int(df["municipio"].nunique()) if "municipio" in df.columns else 0
        for col in _colunas_numericas_validas(df, tabela):
            serie = pd.to_numeric(df[col].replace({"X": pd.NA, "x": pd.NA}), errors="coerce")
            registros.append({
                "tabela": tabela,
                "eixo": eixo,
                "tipo_base": tipo_base,
                "fonte": fonte,
                "ano_referencia": ano,
                "indicador_coluna": col,
                "categoria_sugerida": classificar_indicador_publico(col if not _is_ibge_setores(tabela) else f"{tipo_base} {col}"),
                "linhas_com_dado": int(serie.notna().sum()),
                "municipios_com_dado": municipios,
                "media": round(float(serie.mean()), 4) if serie.notna().sum() else None,
                "minimo": round(float(serie.min()), 4) if serie.notna().sum() else None,
                "maximo": round(float(serie.max()), 4) if serie.notna().sum() else None,
            })
    return pd.DataFrame(registros)


def _agregar_municipal(df: pd.DataFrame, tabela: str, nums: list[str]) -> pd.DataFrame:
    base = df.copy()
    if _is_ibge_setores(tabela):
        base = _enriquecer_municipio(base)
        for col in nums:
            base[col] = pd.to_numeric(base[col].replace({"X": pd.NA, "x": pd.NA}), errors="coerce").fillna(0)
        # Censo por setor: contagens devem ser somadas por município.
        return base.groupby("municipio", dropna=False)[nums].sum(numeric_only=True).reset_index()
    else:
        for col in nums:
            base[col] = pd.to_numeric(base[col], errors="coerce")
        # Bases genéricas: média municipal como aproximação inicial.
        return base.groupby("municipio", dropna=False)[nums].mean(numeric_only=True).reset_index()


def consolidar_bases_publicas_municipal() -> dict:
    tabelas = listar_tabelas_base_publica()
    if tabelas.empty:
        return {"ok": False, "mensagem": "Nenhuma tabela base_publica_* encontrada.", "df": pd.DataFrame()}
    consolidado = None
    metadados = []
    relatorio = []

    for tabela in tabelas["tabela"].tolist():
        # ignora produtos derivados
        if tabela in ["base_publica_consolidado_municipal", "base_publica_indicadores_metadados"]:
            continue

        df = read_table(tabela)
        if df.empty:
            continue

        if _is_ibge_setores(tabela):
            df = _enriquecer_municipio(df)

        if "municipio" not in df.columns:
            relatorio.append({"tabela": tabela, "status": "Ignorada: sem coluna município", "indicadores": 0, "municipios": 0})
            continue

        nums = _colunas_numericas_validas(df, tabela)
        if not nums:
            relatorio.append({"tabela": tabela, "status": "Ignorada: sem indicadores numéricos", "indicadores": 0, "municipios": 0})
            continue

        agg = _agregar_municipal(df, tabela, nums)
        agg = agg[agg["municipio"].astype(str).str.strip().ne("")].copy()

        prefixo = tabela.replace("base_publica_", "bp_")
        rename = {c: f"{prefixo}__{c}" for c in nums}
        agg = agg.rename(columns=rename)

        metadados.extend([
            {
                "tabela_origem": tabela,
                "indicador_original": c,
                "indicador_consolidado": rename[c],
                "categoria_sugerida": classificar_indicador_publico(f"{tabela} {c}"),
                "metodo_agregacao": "soma municipal" if _is_ibge_setores(tabela) else "média municipal",
            }
            for c in nums
        ])

        relatorio.append({
            "tabela": tabela,
            "status": "Consolidada",
            "indicadores": len(nums),
            "municipios": int(agg["municipio"].nunique()),
            "metodo": "soma municipal" if _is_ibge_setores(tabela) else "média municipal",
        })

        if consolidado is None:
            consolidado = agg
        else:
            consolidado = consolidado.merge(agg, on="municipio", how="outer")

    if consolidado is None or consolidado.empty:
        return {"ok": False, "mensagem": "Nenhum indicador numérico municipalizável encontrado.", "df": pd.DataFrame()}

    with get_connection() as con:
        consolidado.to_sql("base_publica_consolidado_municipal", con, if_exists="replace", index=False)
        pd.DataFrame(metadados).to_sql("base_publica_indicadores_metadados", con, if_exists="replace", index=False)
        pd.DataFrame(relatorio).to_sql("base_publica_consolidacao_relatorio", con, if_exists="replace", index=False)

    return {
        "ok": True,
        "mensagem": "Consolidado municipal de bases públicas gerado.",
        "linhas": int(len(consolidado)),
        "colunas": int(len(consolidado.columns)),
        "indicadores": int(len(consolidado.columns) - 1),
        "df": consolidado,
        "metadados": pd.DataFrame(metadados),
        "relatorio": pd.DataFrame(relatorio),
    }


def carregar_consolidado_bases_publicas() -> pd.DataFrame:
    return read_table("base_publica_consolidado_municipal")


def carregar_metadados_indicadores_publicos() -> pd.DataFrame:
    return read_table("base_publica_indicadores_metadados")


def carregar_relatorio_consolidacao_bases_publicas() -> pd.DataFrame:
    return read_table("base_publica_consolidacao_relatorio")


def resumo_categorias_bases_publicas() -> pd.DataFrame:
    inv = inventariar_indicadores_bases_publicas()
    if inv.empty:
        return pd.DataFrame()
    return (
        inv.groupby("categoria_sugerida", dropna=False)
        .agg(
            indicadores=("indicador_coluna", "count"),
            tabelas=("tabela", "nunique"),
            municipios_com_dado_max=("municipios_com_dado", "max"),
            linhas_com_dado=("linhas_com_dado", "sum"),
        )
        .reset_index()
        .sort_values(["indicadores", "linhas_com_dado"], ascending=[False, False])
    )


def perfil_publico_municipio(municipio: str, limite_indicadores: int = 80) -> dict:
    cons = carregar_consolidado_bases_publicas()
    meta = carregar_metadados_indicadores_publicos()
    if cons.empty or "municipio" not in cons.columns:
        return {"ok": False, "mensagem": "Consolidado de bases públicas ainda não foi gerado.", "indicadores": pd.DataFrame()}
    alvo = str(municipio).strip().lower()
    linha = cons[cons["municipio"].astype(str).str.strip().str.lower().eq(alvo)]
    if linha.empty:
        return {"ok": False, "mensagem": "Município não encontrado no consolidado de bases públicas.", "indicadores": pd.DataFrame()}
    row = linha.iloc[0]
    registros = []
    for col in cons.columns:
        if col == "municipio":
            continue
        valor = pd.to_numeric(pd.Series([row[col]]), errors="coerce").iloc[0]
        if pd.isna(valor):
            continue
        categoria = ""
        origem = ""
        original = col
        metodo = ""
        if not meta.empty and "indicador_consolidado" in meta.columns:
            m = meta[meta["indicador_consolidado"].astype(str).eq(col)]
            if not m.empty:
                categoria = m.iloc[0].get("categoria_sugerida", "")
                origem = m.iloc[0].get("tabela_origem", "")
                original = m.iloc[0].get("indicador_original", col)
                metodo = m.iloc[0].get("metodo_agregacao", "")
        dic = aplicar_dicionario_linha(origem, original, categoria or classificar_indicador_publico(col))
        cur = aplicar_curadoria_indicador(origem, original)
        if str(cur.get("status_exibicao", "")).lower().startswith("ocultar"):
            continue
        registros.append({
            "categoria": cur.get("grupo_analitico") or dic.get("categoria_revisada") or categoria or classificar_indicador_publico(col),
            "indicador": dic.get("nome_amigavel") or original,
            "codigo_variavel": original,
            "valor": round(float(valor), 4),
            "tabela_origem": origem,
            "metodo": metodo,
            "status_exibicao": cur.get("status_exibicao", "Complementar"),
            "candidato_indice": cur.get("candidato_indice", "Não"),
            "justificativa_curadoria": cur.get("justificativa", ""),
            "descricao_oficial": dic.get("descricao_oficial", ""),
        })
    out = pd.DataFrame(registros)
    if not out.empty:
        ordem_status = {"Essencial": 0, "Complementar": 1}
        out["_ordem_status"] = out["status_exibicao"].map(ordem_status).fillna(9)
        out = out.sort_values(["_ordem_status", "categoria", "indicador"]).drop(columns=["_ordem_status"]).head(limite_indicadores)
    return {"ok": True, "mensagem": "Perfil encontrado.", "indicadores": out}


def matriz_disponibilidade_tematica_municipal() -> pd.DataFrame:
    cons = carregar_consolidado_bases_publicas()
    meta = carregar_metadados_indicadores_publicos()
    if cons.empty or meta.empty or "municipio" not in cons.columns:
        return pd.DataFrame()

    registros = []
    categorias = sorted(meta.get("categoria_sugerida", pd.Series(dtype=str)).dropna().astype(str).unique().tolist())
    for _, row in cons.iterrows():
        municipio = row.get("municipio", "")
        base = {"municipio": municipio}
        total_indicadores = 0
        total_categorias = 0

        for cat in categorias:
            inds = meta[meta["categoria_sugerida"].astype(str).eq(cat)]["indicador_consolidado"].dropna().astype(str).tolist()
            inds = [c for c in inds if c in cons.columns]
            qtd = 0
            for col in inds:
                val = pd.to_numeric(pd.Series([row.get(col)]), errors="coerce").iloc[0]
                if pd.notna(val):
                    qtd += 1
            base[cat] = qtd
            if qtd > 0:
                total_categorias += 1
                total_indicadores += qtd

        base["total_indicadores_publicos"] = total_indicadores
        base["total_categorias_com_dado"] = total_categorias
        base["classe_disponibilidade_publica"] = (
            "Alta disponibilidade" if total_categorias >= 5 else
            "Média disponibilidade" if total_categorias >= 3 else
            "Baixa disponibilidade" if total_categorias >= 1 else
            "Sem dados públicos consolidados"
        )
        registros.append(base)

    out = pd.DataFrame(registros)
    if not out.empty:
        out = out.sort_values(["total_categorias_com_dado", "total_indicadores_publicos"], ascending=[False, False]).reset_index(drop=True)
    return out


def resumo_disponibilidade_tematica() -> pd.DataFrame:
    matriz = matriz_disponibilidade_tematica_municipal()
    if matriz.empty:
        return pd.DataFrame()
    categorias = [c for c in matriz.columns if c not in ["municipio", "total_indicadores_publicos", "total_categorias_com_dado", "classe_disponibilidade_publica"]]
    registros = []
    for cat in categorias:
        serie = pd.to_numeric(matriz[cat], errors="coerce").fillna(0)
        registros.append({
            "categoria": cat,
            "municipios_com_dado": int((serie > 0).sum()),
            "municipios_sem_dado": int((serie <= 0).sum()),
            "cobertura_municipal_%": round(float((serie > 0).mean() * 100), 1) if len(serie) else 0.0,
            "total_indicadores_disponiveis": int(serie.sum()),
        })
    return pd.DataFrame(registros).sort_values(["cobertura_municipal_%", "total_indicadores_disponiveis"], ascending=[False, False]).reset_index(drop=True)


def lacunas_bases_publicas_por_municipio() -> pd.DataFrame:
    matriz = matriz_disponibilidade_tematica_municipal()
    if matriz.empty:
        return pd.DataFrame()
    categorias = [c for c in matriz.columns if c not in ["municipio", "total_indicadores_publicos", "total_categorias_com_dado", "classe_disponibilidade_publica"]]
    registros = []
    for _, row in matriz.iterrows():
        ausentes = [cat for cat in categorias if pd.to_numeric(pd.Series([row.get(cat)]), errors="coerce").fillna(0).iloc[0] <= 0]
        presentes = [cat for cat in categorias if cat not in ausentes]
        registros.append({
            "municipio": row.get("municipio", ""),
            "categorias_com_dado": int(row.get("total_categorias_com_dado", 0)),
            "indicadores_publicos": int(row.get("total_indicadores_publicos", 0)),
            "classe_disponibilidade_publica": row.get("classe_disponibilidade_publica", ""),
            "temas_presentes": "; ".join(presentes),
            "temas_ausentes": "; ".join(ausentes),
            "prioridade_busca_dados": (
                "Alta" if len(presentes) <= 1 else
                "Média" if len(presentes) <= 3 else
                "Baixa"
            ),
        })
    return pd.DataFrame(registros).sort_values(["prioridade_busca_dados", "categorias_com_dado"], ascending=[True, True]).reset_index(drop=True)


def disponibilidade_tematica_municipio(municipio: str) -> dict:
    matriz = matriz_disponibilidade_tematica_municipal()
    lacunas = lacunas_bases_publicas_por_municipio()
    if matriz.empty:
        return {"ok": False, "mensagem": "Matriz de disponibilidade temática ainda não foi gerada.", "dados": pd.DataFrame()}
    alvo = str(municipio).strip().lower()
    linha = matriz[matriz["municipio"].astype(str).str.strip().str.lower().eq(alvo)]
    lac = lacunas[lacunas["municipio"].astype(str).str.strip().str.lower().eq(alvo)] if not lacunas.empty else pd.DataFrame()
    if linha.empty:
        return {"ok": False, "mensagem": "Município não encontrado na matriz temática.", "dados": pd.DataFrame()}

    row = linha.iloc[0]
    categorias = [c for c in matriz.columns if c not in ["municipio", "total_indicadores_publicos", "total_categorias_com_dado", "classe_disponibilidade_publica"]]
    dados = []
    for cat in categorias:
        qtd = int(pd.to_numeric(pd.Series([row.get(cat)]), errors="coerce").fillna(0).iloc[0])
        dados.append({
            "tema": cat,
            "indicadores_disponiveis": qtd,
            "status": "Disponível" if qtd > 0 else "Pendente",
        })
    return {
        "ok": True,
        "classe": row.get("classe_disponibilidade_publica", ""),
        "total_categorias": int(row.get("total_categorias_com_dado", 0)),
        "total_indicadores": int(row.get("total_indicadores_publicos", 0)),
        "temas_ausentes": lac.iloc[0].get("temas_ausentes", "") if not lac.empty else "",
        "dados": pd.DataFrame(dados),
    }
