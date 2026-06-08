
from __future__ import annotations

from pathlib import Path
import io
import re
import zipfile
import tempfile
import pandas as pd
from services.datasus_dbc_service import converter_dbc_para_csv

from database.connection import get_connection
from database.queries import read_table


TABELA_RAW = "base_publica_sinasc_raw"
TABELA_MUNICIPAL = "base_publica_sinasc_municipal"
TABELA_RELATORIO = "base_publica_sinasc_relatorio"


def _norm(s) -> str:
    s = "" if s is None else str(s)
    s = s.lower()
    s = s.replace("ç", "c").replace("ã", "a").replace("á", "a").replace("à", "a").replace("â", "a")
    s = s.replace("é", "e").replace("ê", "e").replace("í", "i").replace("ó", "o").replace("ô", "o").replace("ú", "u")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_")


def _try_read_csv_bytes(data: bytes, nrows=None) -> pd.DataFrame:
    encodings = ["utf-8-sig", "latin1", "cp1252"]
    seps = [";", ",", "\t", "|"]
    last_err = None
    for enc in encodings:
        for sep in seps:
            try:
                df = pd.read_csv(io.BytesIO(data), sep=sep, encoding=enc, dtype=str, low_memory=False, nrows=nrows)
                if len(df.columns) > 1:
                    return df
            except Exception as e:
                last_err = e
    raise last_err


def _read_file_bytes(nome: str, data: bytes, nrows=None) -> pd.DataFrame:
    low = nome.lower()
    if low.endswith((".csv", ".txt")):
        return _try_read_csv_bytes(data, nrows=nrows)

    if low.endswith(".dbf"):
        try:
            from dbfread import DBF
            table = DBF(io.BytesIO(data), encoding="latin1", char_decode_errors="ignore")
            rows = []
            for i, rec in enumerate(table):
                if nrows and i >= nrows:
                    break
                rows.append(dict(rec))
            return pd.DataFrame(rows).astype(str)
        except Exception as e:
            raise RuntimeError(f"Falha ao ler DBF. Instale dbfread ou converta para CSV. Detalhe: {e}")

    if low.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(data), dtype=str, nrows=nrows)

    if low.endswith(".dbc"):
        # DBC é o formato comprimido do DATASUS. Tentamos converter para CSV
        # usando bibliotecas opcionais instaladas no ambiente (ex.: PySUS/read_dbc).
        with tempfile.NamedTemporaryFile(delete=False, suffix=".dbc") as tmp:
            tmp.write(data)
            caminho_tmp = tmp.name
        saida_tmp = caminho_tmp.replace(".dbc", ".csv")
        info = converter_dbc_para_csv(caminho_tmp, saida_tmp)
        if not info.get("ok"):
            raise RuntimeError(info.get("mensagem", "Falha ao converter DBC."))
        return pd.read_csv(saida_tmp, sep=";", encoding="utf-8-sig", dtype=str, low_memory=False, nrows=nrows)

    raise RuntimeError("Formato não suportado.")


def _iter_data_files(path: Path):
    path = Path(path)
    if path.is_dir():
        for p in path.rglob("*"):
            if p.suffix.lower() in [".csv", ".txt", ".dbf", ".dbc", ".zip", ".xlsx", ".xls"]:
                yield str(p.relative_to(path)), p.read_bytes()
    elif path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            for zi in z.infolist():
                if zi.is_dir():
                    continue
                name = zi.filename
                if name.lower().endswith(".zip"):
                    data = z.read(name)
                    try:
                        with zipfile.ZipFile(io.BytesIO(data)) as inner:
                            for zj in inner.infolist():
                                if zj.is_dir():
                                    continue
                                if zj.filename.lower().endswith((".csv", ".txt", ".dbf", ".dbc", ".xlsx", ".xls")):
                                    yield f"{name}::{zj.filename}", inner.read(zj.filename)
                    except Exception:
                        continue
                elif name.lower().endswith((".csv", ".txt", ".dbf", ".dbc", ".xlsx", ".xls")):
                    yield name, z.read(name)
    else:
        yield path.name, path.read_bytes()


def _detectar_coluna(df: pd.DataFrame, candidatos: list[str]) -> str | None:
    mapa = {_norm(c): c for c in df.columns}
    for cand in candidatos:
        nc = _norm(cand)
        if nc in mapa:
            return mapa[nc]
    for c in df.columns:
        nc = _norm(c)
        for cand in candidatos:
            if _norm(cand) in nc:
                return c
    return None


def _num(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _filtrar_mt(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    # SINASC costuma usar CODMUNRES para município de residência da mãe.
    col_mun = _detectar_coluna(out, ["CODMUNRES", "CO_MUN_RES", "MUNRES", "codmunres", "codigo_municipio_residencia", "municipio_residencia"])
    if col_mun:
        cod = out[col_mun].astype(str).str.replace(r"\D", "", regex=True)
        # DATASUS pode usar 6 dígitos ou 7 dígitos. Em MT começa por 51.
        filtrado = out[cod.str.startswith("51", na=False)].copy()
        if not filtrado.empty:
            return filtrado

    col_uf = _detectar_coluna(out, ["UF", "SG_UF", "CO_UF", "uf_residencia"])
    if col_uf:
        serie = out[col_uf].astype(str).str.strip().str.upper()
        filtrado = out[serie.isin(["MT", "51"])].copy()
        if not filtrado.empty:
            return filtrado

    return pd.DataFrame()


def _mapa_municipios() -> pd.DataFrame:
    muni = read_table("municipios")
    if muni.empty:
        muni = read_table("malhas_geograficas_municipais")
    if muni.empty:
        return pd.DataFrame(columns=["codigo_ibge", "municipio", "regiao_saude"])

    out = muni.copy()
    if "codigo_ibge" not in out.columns:
        return pd.DataFrame(columns=["codigo_ibge", "municipio", "regiao_saude"])

    out["codigo_ibge_7"] = out["codigo_ibge"].astype(str).str.replace(r"\D", "", regex=True).str[:7]
    out["codigo_ibge_6"] = out["codigo_ibge_7"].str[:6]
    cols = ["codigo_ibge_7", "codigo_ibge_6"]
    if "municipio" in out.columns:
        cols.append("municipio")
    if "regiao_saude" in out.columns:
        cols.append("regiao_saude")
    else:
        out["regiao_saude"] = ""
        cols.append("regiao_saude")
    return out[cols].drop_duplicates()


def _enriquecer_municipio(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    col_mun = _detectar_coluna(out, ["CODMUNRES", "CO_MUN_RES", "MUNRES", "codmunres", "codigo_municipio_residencia", "municipio_residencia"])
    if not col_mun:
        out["codigo_municipio_residencia"] = ""
        out["municipio"] = ""
        out["regiao_saude"] = ""
        return out

    out["codigo_municipio_residencia"] = out[col_mun].astype(str).str.replace(r"\D", "", regex=True)
    mapa = _mapa_municipios()
    if not mapa.empty:
        out = out.merge(mapa, left_on="codigo_municipio_residencia", right_on="codigo_ibge_7", how="left")
        sem = out["municipio"].isna() if "municipio" in out.columns else pd.Series([True]*len(out), index=out.index)
        if sem.any():
            aux = out[sem].drop(columns=[c for c in ["codigo_ibge_7", "codigo_ibge_6", "municipio", "regiao_saude"] if c in out.columns], errors="ignore")
            aux = aux.merge(mapa, left_on="codigo_municipio_residencia", right_on="codigo_ibge_6", how="left")
            for col in ["codigo_ibge_7", "codigo_ibge_6", "municipio", "regiao_saude"]:
                if col in aux.columns:
                    out.loc[sem, col] = aux[col].values
    if "municipio" not in out.columns:
        out["municipio"] = out["codigo_municipio_residencia"]
    if "regiao_saude" not in out.columns:
        out["regiao_saude"] = ""
    return out


def diagnosticar_sinasc_local(caminho: str) -> dict:
    path = Path(caminho)
    if not path.exists():
        return {"ok": False, "mensagem": "Caminho não encontrado.", "arquivos": pd.DataFrame()}

    registros = []
    for nome, data in _iter_data_files(path):
        if not nome.lower().endswith((".csv", ".txt", ".dbf", ".dbc", ".xlsx", ".xls")):
            continue
        try:
            sample = _read_file_bytes(nome, data, nrows=2000)
            mt = _filtrar_mt(sample)
            registros.append({
                "arquivo": nome,
                "colunas": len(sample.columns),
                "linhas_amostra": len(sample),
                "linhas_mt_amostra": len(mt),
                "possui_mt": "Sim" if len(mt) > 0 else "Não",
                "colunas_exemplo": ", ".join(list(sample.columns[:15])),
            })
        except Exception as e:
            registros.append({
                "arquivo": nome,
                "colunas": 0,
                "linhas_amostra": 0,
                "linhas_mt_amostra": 0,
                "possui_mt": "Erro",
                "colunas_exemplo": str(e)[:250],
            })

    df = pd.DataFrame(registros)
    return {"ok": not df.empty, "mensagem": f"{len(df)} arquivo(s) analisado(s).", "arquivos": df}


def importar_sinasc_local(caminho: str, ano_referencia: str = "") -> dict:
    path = Path(caminho)
    if not path.exists():
        return {"ok": False, "mensagem": "Caminho não encontrado.", "linhas": 0}

    frames = []
    rel = []
    for nome, data in _iter_data_files(path):
        if not nome.lower().endswith((".csv", ".txt", ".dbf", ".dbc", ".xlsx", ".xls")):
            continue
        try:
            df = _read_file_bytes(nome, data)
            mt = _filtrar_mt(df)
            if mt.empty:
                rel.append({"arquivo": nome, "status": "Ignorado: sem MT detectado", "linhas_mt": 0})
                continue
            mt = _enriquecer_municipio(mt)
            mt["arquivo_origem"] = nome
            mt["fonte"] = "DATASUS/SINASC"
            mt["ano_referencia"] = ano_referencia
            frames.append(mt)
            rel.append({"arquivo": nome, "status": "Importado", "linhas_mt": len(mt)})
        except Exception as e:
            rel.append({"arquivo": nome, "status": f"Erro: {e}", "linhas_mt": 0})

    if not frames:
        return {"ok": False, "mensagem": "Nenhuma linha de Mato Grosso foi importada.", "linhas": 0, "relatorio": pd.DataFrame(rel)}

    raw = pd.concat(frames, ignore_index=True)
    with get_connection() as con:
        raw.to_sql(TABELA_RAW, con, if_exists="replace", index=False)
        pd.DataFrame(rel).to_sql(TABELA_RELATORIO, con, if_exists="replace", index=False)

    cons = consolidar_sinasc_municipal()
    return {
        "ok": True,
        "mensagem": "SINASC importado.",
        "linhas": int(len(raw)),
        "arquivos": int(len(frames)),
        "relatorio": pd.DataFrame(rel),
        "consolidado": cons.get("df", pd.DataFrame()),
    }


def _prenatal_insuficiente(df: pd.DataFrame) -> pd.Series:
    # CONSPRENAT = número de consultas pré-natal; CONSULTAS pode ser categoria.
    col_num = _detectar_coluna(df, ["CONSPRENAT", "QT_CONS_PRENATAL", "consultas_prenatal"])
    if col_num:
        n = _num(df[col_num])
        return (n < 7).fillna(False).astype(int)

    col_cat = _detectar_coluna(df, ["CONSULTAS", "TP_CONSULTAS", "consultas"])
    if col_cat:
        s = df[col_cat].astype(str).str.strip()
        # SINASC antigo: 1 nenhuma; 2 1-3; 3 4-6; 4 7+; 9 ignorado.
        return s.isin(["1", "2", "3"]).astype(int)
    return pd.Series([0] * len(df), index=df.index)


def _baixo_peso(df: pd.DataFrame) -> pd.Series:
    col = _detectar_coluna(df, ["PESO", "peso_nascer", "peso_ao_nascer"])
    if col:
        return (_num(df[col]) < 2500).fillna(False).astype(int)
    return pd.Series([0] * len(df), index=df.index)


def _prematuro(df: pd.DataFrame) -> pd.Series:
    col_num = _detectar_coluna(df, ["SEMAGESTAC", "semanas_gestacao"])
    if col_num:
        return (_num(df[col_num]) < 37).fillna(False).astype(int)

    col_cat = _detectar_coluna(df, ["GESTACAO", "TP_GESTACAO"])
    if col_cat:
        s = df[col_cat].astype(str).str.strip()
        # 1 <22; 2 22-27; 3 28-31; 4 32-36; 5 37-41; 6 42+.
        return s.isin(["1", "2", "3", "4"]).astype(int)
    return pd.Series([0] * len(df), index=df.index)


def _mae_adolescente(df: pd.DataFrame) -> pd.Series:
    col = _detectar_coluna(df, ["IDADEMAE", "idade_mae"])
    if col:
        return (_num(df[col]) < 20).fillna(False).astype(int)
    return pd.Series([0] * len(df), index=df.index)


def _cesarea(df: pd.DataFrame) -> pd.Series:
    col = _detectar_coluna(df, ["PARTO", "TP_PARTO"])
    if col:
        s = df[col].astype(str).str.strip().str.upper()
        # SINASC: 1 vaginal; 2 cesáreo; 9 ignorado. Também aceita texto.
        return (s.isin(["2", "CESAREO", "CESÁREO", "CESARIANA"])).astype(int)
    return pd.Series([0] * len(df), index=df.index)


def consolidar_sinasc_municipal() -> dict:
    raw = read_table(TABELA_RAW)
    if raw.empty:
        return {"ok": False, "mensagem": "Tabela SINASC raw vazia.", "df": pd.DataFrame()}

    df = _enriquecer_municipio(raw)
    if "municipio" not in df.columns:
        return {"ok": False, "mensagem": "Município não identificado no SINASC.", "df": pd.DataFrame()}

    df["_nascidos_vivos"] = 1
    df["_baixo_peso"] = _baixo_peso(df)
    df["_prematuro"] = _prematuro(df)
    df["_mae_adolescente"] = _mae_adolescente(df)
    df["_prenatal_insuficiente"] = _prenatal_insuficiente(df)
    df["_cesarea"] = _cesarea(df)

    agg = df.groupby("municipio", dropna=False).agg(
        nascidos_vivos=("_nascidos_vivos", "sum"),
        baixo_peso=("_baixo_peso", "sum"),
        prematuros=("_prematuro", "sum"),
        maes_adolescentes=("_mae_adolescente", "sum"),
        prenatal_insuficiente=("_prenatal_insuficiente", "sum"),
        cesareas=("_cesarea", "sum"),
        registros_base=("_nascidos_vivos", "sum"),
    ).reset_index()

    for col in ["baixo_peso", "prematuros", "maes_adolescentes", "prenatal_insuficiente", "cesareas"]:
        agg[f"perc_{col}"] = (agg[col] / agg["nascidos_vivos"].replace({0: pd.NA}) * 100).round(2)

    if "ano_referencia" in df.columns:
        anos_ref = ", ".join(sorted([str(a) for a in df["ano_referencia"].dropna().unique() if str(a).strip()]))
    else:
        anos_ref = ""
    agg["ano_referencia"] = anos_ref
    agg["fonte"] = "DATASUS/SINASC"
    agg["observacao_metodologica"] = (
        "Indicadores calculados por município de residência da mãe. "
        "Valide nomes/códigos de variáveis conforme layout do arquivo importado."
    )

    with get_connection() as con:
        agg.to_sql(TABELA_MUNICIPAL, con, if_exists="replace", index=False)

    return {"ok": True, "mensagem": "Consolidado municipal SINASC gerado.", "df": agg, "linhas": int(len(agg)), "colunas": int(len(agg.columns))}


def carregar_sinasc_municipal() -> pd.DataFrame:
    return read_table(TABELA_MUNICIPAL)


def relatorio_importacao_sinasc() -> pd.DataFrame:
    return read_table(TABELA_RELATORIO)


def _fmt_num(v, casas: int = 1):
    try:
        x = float(v)
    except Exception:
        return "-"
    if abs(x) >= 1000:
        return f"{x:,.0f}".replace(",", ".")
    return f"{x:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_perc(v):
    try:
        return _fmt_num(float(v), 1) + "%"
    except Exception:
        return "-"


def perfil_sinasc_municipio(municipio: str) -> dict:
    df = carregar_sinasc_municipal()
    if df.empty or "municipio" not in df.columns:
        return {"ok": False, "mensagem": "Consolidado SINASC ainda não foi gerado.", "resumo": pd.DataFrame(), "alertas": pd.DataFrame()}

    alvo = str(municipio).strip().lower()
    linha = df[df["municipio"].astype(str).str.strip().str.lower().eq(alvo)]
    if linha.empty:
        return {"ok": False, "mensagem": "Município não encontrado no consolidado SINASC.", "resumo": pd.DataFrame(), "alertas": pd.DataFrame()}

    r = linha.iloc[0]
    resumo = pd.DataFrame([
        {"Indicador-chave": "Nascidos vivos", "Indicador": "Total de nascidos vivos", "Valor": _fmt_num(r.get("nascidos_vivos"), 0), "Leitura": "Dimensiona o volume de nascimentos no município de residência da mãe."},
        {"Indicador-chave": "Baixo peso", "Indicador": "Percentual de baixo peso ao nascer", "Valor": _fmt_perc(r.get("perc_baixo_peso")), "Leitura": "Sinaliza risco materno-infantil e necessidade de atenção pré-natal qualificada."},
        {"Indicador-chave": "Prematuridade", "Indicador": "Percentual de prematuros", "Valor": _fmt_perc(r.get("perc_prematuros")), "Leitura": "Apoia leitura da qualidade do cuidado gestacional e risco neonatal."},
        {"Indicador-chave": "Mães adolescentes", "Indicador": "Percentual de mães adolescentes", "Valor": _fmt_perc(r.get("perc_maes_adolescentes")), "Leitura": "Indica vulnerabilidade social e necessidade de ações intersetoriais."},
        {"Indicador-chave": "Pré-natal insuficiente", "Indicador": "Percentual com menos de 7 consultas ou categoria insuficiente", "Valor": _fmt_perc(r.get("perc_prenatal_insuficiente")), "Leitura": "Apoia avaliação da captação e acompanhamento gestacional pela APS."},
        {"Indicador-chave": "Cesáreas", "Indicador": "Percentual de partos cesáreos", "Valor": _fmt_perc(r.get("perc_cesareas")), "Leitura": "Complementa análise da assistência ao parto e rede materno-infantil."},
    ])

    alertas = []
    def add(cond, alerta, interp, acao):
        if cond:
            alertas.append({"Alerta": alerta, "Interpretação": interp, "Ação sugerida": acao})

    def val(col):
        try:
            return float(r.get(col, 0) or 0)
        except Exception:
            return 0.0

    add(val("perc_baixo_peso") >= 10, "Baixo peso elevado", f"{_fmt_perc(r.get('perc_baixo_peso'))} dos nascidos vivos com baixo peso.", "Cruzar com pré-natal, vulnerabilidade social e cobertura APS.")
    add(val("perc_prematuros") >= 12, "Prematuridade elevada", f"{_fmt_perc(r.get('perc_prematuros'))} de prematuros.", "Validar rede materno-infantil e acompanhamento gestacional.")
    add(val("perc_maes_adolescentes") >= 15, "Maternidade adolescente relevante", f"{_fmt_perc(r.get('perc_maes_adolescentes'))} de mães adolescentes.", "Planejar ações intersetoriais com educação, assistência social e APS.")
    add(val("perc_prenatal_insuficiente") >= 25, "Pré-natal insuficiente", f"{_fmt_perc(r.get('perc_prenatal_insuficiente'))} com pré-natal insuficiente.", "Investigar captação precoce, acesso territorial e qualidade do acompanhamento.")
    add(val("perc_cesareas") >= 60, "Percentual elevado de cesáreas", f"{_fmt_perc(r.get('perc_cesareas'))} de cesáreas.", "Analisar perfil da rede obstétrica e protocolos de parto.")

    return {
        "ok": True,
        "mensagem": "Perfil SINASC gerado.",
        "resumo": resumo,
        "alertas": pd.DataFrame(alertas),
        "tecnica": linha.copy(),
    }


def resumo_validacao_sinasc() -> dict:
    """Resumo estadual e checagens do consolidado SINASC."""
    cons = carregar_sinasc_municipal()
    raw = read_table(TABELA_RAW)

    if cons.empty:
        return {
            "ok": False,
            "mensagem": "Consolidado SINASC ainda não foi gerado.",
            "resumo": pd.DataFrame(),
            "ranking_alertas": pd.DataFrame(),
            "municipios_sem_registro": pd.DataFrame(),
        }

    total_nv = float(cons.get("nascidos_vivos", pd.Series(dtype=float)).sum())
    total_mun = int(cons["municipio"].nunique()) if "municipio" in cons.columns else int(len(cons))
    ano_ref = ""
    if not raw.empty and "ano_referencia" in raw.columns:
        anos = sorted([str(a) for a in raw["ano_referencia"].dropna().unique() if str(a).strip()])
        ano_ref = ", ".join(anos)

    resumo = pd.DataFrame([
        {"Indicador": "Ano de referência informado", "Valor": ano_ref or "Não informado", "Leitura": "Ano registrado no momento da importação."},
        {"Indicador": "Municípios com registros SINASC", "Valor": _fmt_num(total_mun, 0), "Leitura": "Quantidade de municípios presentes no consolidado."},
        {"Indicador": "Total estadual de nascidos vivos importados", "Valor": _fmt_num(total_nv, 0), "Leitura": "Soma dos nascidos vivos importados para Mato Grosso."},
        {"Indicador": "Base de consolidação", "Valor": "Município de residência da mãe", "Leitura": "Critério usado para leitura municipal."},
        {"Indicador": "Fonte", "Valor": "DATASUS/SINASC", "Leitura": "Base administrativa de nascidos vivos."},
    ])

    work = cons.copy()
    for col in ["perc_baixo_peso", "perc_prematuros", "perc_maes_adolescentes", "perc_prenatal_insuficiente", "perc_cesareas"]:
        if col not in work.columns:
            work[col] = 0
        work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0)

    work["pontos_alerta"] = 0
    work["pontos_alerta"] += (work["perc_baixo_peso"] >= 10).astype(int)
    work["pontos_alerta"] += (work["perc_prematuros"] >= 12).astype(int)
    work["pontos_alerta"] += (work["perc_maes_adolescentes"] >= 15).astype(int)
    work["pontos_alerta"] += (work["perc_prenatal_insuficiente"] >= 25).astype(int)
    work["pontos_alerta"] += (work["perc_cesareas"] >= 60).astype(int)

    work["alertas_identificados"] = work.apply(
        lambda r: "; ".join([
            nome for nome, cond in [
                ("baixo peso >=10%", r["perc_baixo_peso"] >= 10),
                ("prematuridade >=12%", r["perc_prematuros"] >= 12),
                ("mães adolescentes >=15%", r["perc_maes_adolescentes"] >= 15),
                ("pré-natal insuficiente >=25%", r["perc_prenatal_insuficiente"] >= 25),
                ("cesáreas >=60%", r["perc_cesareas"] >= 60),
            ] if cond
        ]) or "Sem alerta pelos limiares atuais",
        axis=1
    )

    ranking_cols = [
        "municipio", "nascidos_vivos", "pontos_alerta", "alertas_identificados",
        "perc_baixo_peso", "perc_prematuros", "perc_maes_adolescentes",
        "perc_prenatal_insuficiente", "perc_cesareas"
    ]
    ranking = work[[c for c in ranking_cols if c in work.columns]].sort_values(
        ["pontos_alerta", "nascidos_vivos"], ascending=[False, False]
    ).reset_index(drop=True)

    # Busca lista oficial de municípios, se existir, para identificar ausentes.
    muni = read_table("municipios")
    if muni.empty:
        muni = read_table("malhas_geograficas_municipais")
    sem = pd.DataFrame()
    if not muni.empty and "municipio" in muni.columns and "municipio" in cons.columns:
        base_m = muni[["municipio"]].drop_duplicates().copy()
        base_m["_key"] = base_m["municipio"].astype(str).str.strip().str.lower()
        cons_keys = set(cons["municipio"].astype(str).str.strip().str.lower())
        sem = base_m[~base_m["_key"].isin(cons_keys)].drop(columns=["_key"]).reset_index(drop=True)
        sem["observacao"] = "Município não localizado no consolidado SINASC importado."

    return {
        "ok": True,
        "mensagem": "Validação SINASC gerada.",
        "resumo": resumo,
        "ranking_alertas": ranking,
        "municipios_sem_registro": sem,
        "consolidado": cons,
    }
