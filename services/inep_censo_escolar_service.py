
from __future__ import annotations

from pathlib import Path
import io
import re
import zipfile
import pandas as pd

from database.connection import get_connection
from database.queries import read_table


TABELA_RAW = "base_publica_inep_censo_escolar_raw"
TABELA_MUNICIPAL = "base_publica_inep_censo_escolar_municipal"
TABELA_RELATORIO = "base_publica_inep_censo_escolar_relatorio"


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
                return pd.read_csv(io.BytesIO(data), sep=sep, encoding=enc, dtype=str, low_memory=False, nrows=nrows)
            except Exception as e:
                last_err = e
    raise last_err


def _read_table_file(path: Path, nrows=None) -> pd.DataFrame:
    suf = path.suffix.lower()
    if suf in [".xlsx", ".xls"]:
        return pd.read_excel(path, dtype=str, nrows=nrows)
    data = path.read_bytes()
    return _try_read_csv_bytes(data, nrows=nrows)


def _iter_data_files(path: Path):
    path = Path(path)
    if path.is_dir():
        for p in path.rglob("*"):
            if p.suffix.lower() in [".csv", ".txt", ".zip", ".xlsx", ".xls"]:
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
                                if zj.filename.lower().endswith((".csv", ".txt")):
                                    yield f"{name}::{zj.filename}", inner.read(zj.filename)
                    except Exception:
                        continue
                elif name.lower().endswith((".csv", ".txt")):
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


def _filtrar_mt(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    col_uf = _detectar_coluna(out, ["CO_UF", "SG_UF", "UF", "sigla_uf"])
    if col_uf:
        serie = out[col_uf].astype(str).str.strip().str.upper()
        mask = serie.eq("MT") | serie.eq("51")
        filtrado = out[mask].copy()
        if not filtrado.empty:
            return filtrado

    col_mun = _detectar_coluna(out, ["CO_MUNICIPIO", "CO_MUNICIPIO_ESC", "codigo_municipio", "codigo_ibge", "CO_MUN"])
    if col_mun:
        cod = out[col_mun].astype(str).str.replace(r"\D", "", regex=True).str[:2]
        filtrado = out[cod.eq("51")].copy()
        if not filtrado.empty:
            return filtrado

    return pd.DataFrame()


def diagnosticar_inep_censo_escolar_local(caminho: str) -> dict:
    path = Path(caminho)
    if not path.exists():
        return {"ok": False, "mensagem": "Caminho não encontrado.", "arquivos": pd.DataFrame()}

    registros = []
    for nome, data in _iter_data_files(path):
        if not nome.lower().endswith((".csv", ".txt")):
            continue
        try:
            sample = _try_read_csv_bytes(data, nrows=2000)
            mt = _filtrar_mt(sample)
            registros.append({
                "arquivo": nome,
                "colunas": len(sample.columns),
                "linhas_amostra": len(sample),
                "linhas_mt_amostra": len(mt),
                "possui_mt": "Sim" if len(mt) > 0 else "Não",
                "colunas_exemplo": ", ".join(list(sample.columns[:12])),
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
    return {
        "ok": not df.empty,
        "mensagem": f"{len(df)} arquivo(s) analisado(s).",
        "arquivos": df,
    }


def importar_inep_censo_escolar_local(caminho: str, limite_arquivos: int | None = None) -> dict:
    path = Path(caminho)
    if not path.exists():
        return {"ok": False, "mensagem": "Caminho não encontrado.", "linhas": 0}

    frames = []
    rel = []
    qtd = 0
    for nome, data in _iter_data_files(path):
        if limite_arquivos and qtd >= limite_arquivos:
            break
        if not nome.lower().endswith((".csv", ".txt")):
            continue
        qtd += 1
        try:
            df = _try_read_csv_bytes(data)
            mt = _filtrar_mt(df)
            if mt.empty:
                rel.append({"arquivo": nome, "status": "Ignorado: sem MT detectado", "linhas_mt": 0})
                continue
            mt = mt.copy()
            mt["arquivo_origem"] = nome
            mt["fonte"] = "INEP - Censo Escolar"
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

    cons = consolidar_inep_censo_escolar_municipal()
    return {
        "ok": True,
        "mensagem": "INEP/Censo Escolar importado.",
        "linhas": int(len(raw)),
        "arquivos": int(len(frames)),
        "relatorio": pd.DataFrame(rel),
        "consolidado": cons.get("df", pd.DataFrame()),
    }


def _num(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0)


def _col(df, candidatos):
    return _detectar_coluna(df, candidatos)


def _flag_sum(df: pd.DataFrame, candidatos: list[str], valor_positivo: str | int = 1):
    c = _col(df, candidatos)
    if not c:
        return None
    s = df[c].astype(str).str.strip().str.upper()
    vals_pos = {str(valor_positivo).upper(), "1", "SIM", "S", "TRUE", "VERDADEIRO"}
    return s.isin(vals_pos).astype(int)


def _sum_numeric_cols(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    total = pd.Series([0] * len(df), index=df.index, dtype=float)
    for c in cols:
        if c in df.columns:
            total = total + _num(df[c])
    return total



def _coluna_exata_norm(df: pd.DataFrame, nome_norm: str) -> str | None:
    for c in df.columns:
        if _norm(c) == nome_norm:
            return c
    return None


def _calcular_matriculas_seguro(df: pd.DataFrame) -> tuple[pd.Series, str]:
    """Calcula matrículas evitando dupla contagem.

    No arquivo de escolas do Censo Escolar há várias colunas QT_MAT_*.
    Muitas são subtotais/recortes. Somar todas superestima o total.

    Regra:
    1. Se existir QT_MAT_BAS, usa apenas ela.
    2. Se não existir, soma apenas etapas principais controladas.
    3. Se não existir nenhuma coluna de matrícula, cada linha vira 1 registro.
    """
    prioridade_total = [
        "qt_mat_bas",
        "qt_mat_basica",
        "qt_matriculas",
        "qt_matricula",
        "matriculas_total",
        "matricula_total",
    ]

    for nome in prioridade_total:
        c = _coluna_exata_norm(df, nome)
        if c:
            return _num(df[c]), f"usou coluna total principal: {c}"

    etapas_controladas = [
        "qt_mat_inf",
        "qt_mat_cre",
        "qt_mat_pre",
        "qt_mat_fund",
        "qt_mat_fund_ai",
        "qt_mat_fund_af",
        "qt_mat_med",
        "qt_mat_prof",
        "qt_mat_eja",
        "qt_mat_esp",
    ]

    cols = []
    for nome in etapas_controladas:
        c = _coluna_exata_norm(df, nome)
        if c and c not in cols:
            cols.append(c)

    # Evita somar simultaneamente total do fundamental e anos iniciais/finais, caso todos existam.
    norm_cols = {_norm(c): c for c in cols}
    if "qt_mat_fund" in norm_cols:
        cols = [c for c in cols if _norm(c) not in ["qt_mat_fund_ai", "qt_mat_fund_af"]]

    if cols:
        return _sum_numeric_cols(df, cols), "somou etapas controladas: " + ", ".join(cols)

    return pd.Series([1] * len(df), index=df.index, dtype=float), "sem coluna de matrícula detectada; contou registros"

def consolidar_inep_censo_escolar_municipal() -> dict:
    raw = read_table(TABELA_RAW)
    if raw.empty:
        return {"ok": False, "mensagem": "Tabela INEP raw vazia.", "df": pd.DataFrame()}

    df = raw.copy()
    col_mun = _col(df, ["NO_MUNICIPIO", "NO_MUNICIPIO_ESC", "nome_municipio", "municipio"])
    col_cod = _col(df, ["CO_MUNICIPIO", "CO_MUNICIPIO_ESC", "codigo_municipio", "codigo_ibge", "CO_MUN"])
    if col_mun:
        df["municipio"] = df[col_mun].astype(str).str.strip()
    elif col_cod:
        df["municipio"] = df[col_cod].astype(str).str.strip()
    else:
        return {"ok": False, "mensagem": "Não foi possível identificar município na base INEP.", "df": pd.DataFrame()}

    col_escola = _col(df, ["CO_ENTIDADE", "CO_ESCOLA", "codigo_escola", "ID_ESCOLA"])
    if col_escola:
        df["_id_escola"] = df[col_escola].astype(str).str.strip()
    else:
        # Fallback: sem identificador de escola, usa linha como unidade.
        df["_id_escola"] = df.index.astype(str)

    # Matrículas com regra segura.
    df["_matriculas_total"], metodo_matriculas = _calcular_matriculas_seguro(df)

    # Dependência administrativa.
    col_dep = _col(df, ["TP_DEPENDENCIA", "TP_DEPENDENCIA_ADM", "dependencia_administrativa"])
    if col_dep:
        dep = df[col_dep].astype(str).str.strip().str.upper()
        df["_dep_publica"] = dep.isin(["1", "2", "3", "FEDERAL", "ESTADUAL", "MUNICIPAL"]).astype(int)
        df["_dep_privada"] = dep.isin(["4", "PRIVADA"]).astype(int)
    else:
        df["_dep_publica"] = 0
        df["_dep_privada"] = 0

    # Localização.
    col_loc = _col(df, ["TP_LOCALIZACAO", "localizacao", "TP_ZONA_RESIDENCIAL"])
    if col_loc:
        loc = df[col_loc].astype(str).str.strip().str.upper()
        df["_loc_urbana"] = loc.isin(["1", "URBANA"]).astype(int)
        df["_loc_rural"] = loc.isin(["2", "RURAL"]).astype(int)
    else:
        df["_loc_urbana"] = 0
        df["_loc_rural"] = 0

    # Infraestrutura escolar — colunas típicas do Censo Escolar.
    infra = {
        "_internet": ["IN_INTERNET", "IN_INTERNET_ALUNOS", "internet"],
        "_agua_rede": ["IN_AGUA_REDE_PUBLICA", "IN_AGUA_POTAVEL", "agua_rede"],
        "_esgoto": ["IN_ESGOTO_REDE_PUBLICA", "IN_ESGOTO", "esgoto"],
        "_energia": ["IN_ENERGIA_REDE_PUBLICA", "energia"],
        "_biblioteca": ["IN_BIBLIOTECA", "IN_SALA_LEITURA", "biblioteca"],
        "_lab_info": ["IN_LABORATORIO_INFORMATICA", "laboratorio_informatica"],
        "_quadra": ["IN_QUADRA_ESPORTES", "quadra"],
    }
    for new_col, candidatos in infra.items():
        flag = _flag_sum(df, candidatos, 1)
        df[new_col] = flag if flag is not None else 0

    # Consolidação por escola única.
    # A base pode ter mais de uma linha por escola. A contagem de escolas deve usar CO_ENTIDADE único.
    escola = (
        df.groupby(["municipio", "_id_escola"], dropna=False)
        .agg(
            matriculas_total=("_matriculas_total", "max"),
            escola_publica=("_dep_publica", "max"),
            escola_privada=("_dep_privada", "max"),
            escola_urbana=("_loc_urbana", "max"),
            escola_rural=("_loc_rural", "max"),
            escola_com_internet=("_internet", "max"),
            escola_com_agua_rede=("_agua_rede", "max"),
            escola_com_esgoto=("_esgoto", "max"),
            escola_com_energia=("_energia", "max"),
            escola_com_biblioteca_sala_leitura=("_biblioteca", "max"),
            escola_com_lab_informatica=("_lab_info", "max"),
            escola_com_quadra=("_quadra", "max"),
            registros_escola=("_id_escola", "count"),
        )
        .reset_index()
    )

    agg = escola.groupby("municipio", dropna=False).agg(
        escolas_total=("_id_escola", "nunique"),
        matriculas_total=("matriculas_total", "sum"),
        escolas_publicas=("escola_publica", "sum"),
        escolas_privadas=("escola_privada", "sum"),
        escolas_urbanas=("escola_urbana", "sum"),
        escolas_rurais=("escola_rural", "sum"),
        escolas_com_internet=("escola_com_internet", "sum"),
        escolas_com_agua_rede=("escola_com_agua_rede", "sum"),
        escolas_com_esgoto=("escola_com_esgoto", "sum"),
        escolas_com_energia=("escola_com_energia", "sum"),
        escolas_com_biblioteca_sala_leitura=("escola_com_biblioteca_sala_leitura", "sum"),
        escolas_com_lab_informatica=("escola_com_lab_informatica", "sum"),
        escolas_com_quadra=("escola_com_quadra", "sum"),
        registros_base=("registros_escola", "sum"),
    ).reset_index()

    agg["metodo_matriculas"] = metodo_matriculas
    agg["metodo_escolas"] = "contagem por escola única: CO_ENTIDADE/CO_ESCOLA"

    # Percentuais com denominador de escolas_total.
    for col in [
        "escolas_publicas", "escolas_privadas", "escolas_urbanas", "escolas_rurais",
        "escolas_com_internet", "escolas_com_agua_rede", "escolas_com_esgoto",
        "escolas_com_energia", "escolas_com_biblioteca_sala_leitura",
        "escolas_com_lab_informatica", "escolas_com_quadra"
    ]:
        agg[f"perc_{col}"] = (agg[col] / agg["escolas_total"].replace({0: pd.NA}) * 100).round(2)

    # Indicadores de checagem.
    agg["checagem_rede_total"] = agg["escolas_publicas"] + agg["escolas_privadas"]
    agg["checagem_localizacao_total"] = agg["escolas_urbanas"] + agg["escolas_rurais"]
    agg["alerta_rede"] = agg.apply(
        lambda r: "OK" if r["checagem_rede_total"] <= r["escolas_total"] else "Revisar: públicas + privadas > total",
        axis=1
    )
    agg["alerta_localizacao"] = agg.apply(
        lambda r: "OK" if r["checagem_localizacao_total"] <= r["escolas_total"] else "Revisar: urbanas + rurais > total",
        axis=1
    )

    with get_connection() as con:
        agg.to_sql(TABELA_MUNICIPAL, con, if_exists="replace", index=False)

    return {"ok": True, "mensagem": "Consolidado municipal INEP gerado por escola única.", "df": agg, "linhas": int(len(agg)), "colunas": int(len(agg.columns))}

def carregar_inep_municipal() -> pd.DataFrame:
    return read_table(TABELA_MUNICIPAL)


def perfil_inep_municipio(municipio: str) -> dict:
    df = carregar_inep_municipal()
    if df.empty or "municipio" not in df.columns:
        return {"ok": False, "mensagem": "Consolidado INEP ainda não foi gerado.", "dados": pd.DataFrame()}
    alvo = str(municipio).strip().lower()
    linha = df[df["municipio"].astype(str).str.strip().str.lower().eq(alvo)]
    if linha.empty:
        return {"ok": False, "mensagem": "Município não encontrado no consolidado INEP.", "dados": pd.DataFrame()}

    row = linha.iloc[0]
    itens = [
        ("Escolas", "Total de escolas", row.get("escolas_total")),
        ("Matrículas", "Total de matrículas", row.get("matriculas_total")),
        ("Matrículas", "Método de cálculo das matrículas", row.get("metodo_matriculas")),
        ("Escolas", "Método de contagem das escolas", row.get("metodo_escolas")),
        ("Rede", "Escolas públicas", row.get("escolas_publicas")),
        ("Rede", "Escolas privadas", row.get("escolas_privadas")),
        ("Localização", "Escolas urbanas", row.get("escolas_urbanas")),
        ("Localização", "Escolas rurais", row.get("escolas_rurais")),
        ("Infraestrutura", "Escolas com internet", row.get("escolas_com_internet")),
        ("Infraestrutura", "Escolas com água/rede", row.get("escolas_com_agua_rede")),
        ("Infraestrutura", "Escolas com esgoto", row.get("escolas_com_esgoto")),
        ("Infraestrutura", "Escolas com energia", row.get("escolas_com_energia")),
        ("Infraestrutura", "Escolas com biblioteca/sala de leitura", row.get("escolas_com_biblioteca_sala_leitura")),
        ("Infraestrutura", "Escolas com laboratório de informática", row.get("escolas_com_lab_informatica")),
        ("Infraestrutura", "Escolas com quadra", row.get("escolas_com_quadra")),
        ("Validação", "Checagem rede", row.get("alerta_rede")),
        ("Validação", "Checagem localização", row.get("alerta_localizacao")),
    ]
    out = pd.DataFrame([{"grupo": g, "indicador": i, "valor": v} for g, i, v in itens])
    return {"ok": True, "mensagem": "Perfil INEP encontrado.", "dados": out}


def relatorio_importacao_inep() -> pd.DataFrame:
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


def perfil_educacional_gerencial_inep(municipio: str) -> dict:
    """Gera leitura gerencial do Censo Escolar/INEP para o Diagnóstico Municipal."""
    df = carregar_inep_municipal()
    if df.empty or "municipio" not in df.columns:
        return {
            "ok": False,
            "mensagem": "Consolidado INEP ainda não foi gerado.",
            "resumo": pd.DataFrame(),
            "alertas": pd.DataFrame(),
            "tecnica": pd.DataFrame(),
        }

    alvo = str(municipio).strip().lower()
    linha = df[df["municipio"].astype(str).str.strip().str.lower().eq(alvo)]
    if linha.empty:
        return {
            "ok": False,
            "mensagem": "Município não encontrado no consolidado INEP.",
            "resumo": pd.DataFrame(),
            "alertas": pd.DataFrame(),
            "tecnica": pd.DataFrame(),
        }

    r = linha.iloc[0]

    escolas_total = float(r.get("escolas_total", 0) or 0)
    matriculas_total = float(r.get("matriculas_total", 0) or 0)

    resumo_rows = [
        {
            "Indicador-chave": "Escolas",
            "Indicador selecionado": "Total de escolas únicas",
            "Valor": _fmt_num(escolas_total, 0),
            "Fonte": "INEP / Censo Escolar",
            "Leitura": "Dimensiona a rede escolar existente no município.",
        },
        {
            "Indicador-chave": "Matrículas",
            "Indicador selecionado": "Total de matrículas da educação básica",
            "Valor": _fmt_num(matriculas_total, 0),
            "Fonte": "INEP / Censo Escolar",
            "Leitura": "Indica o volume de estudantes atendidos na rede local.",
        },
        {
            "Indicador-chave": "Escolas públicas",
            "Indicador selecionado": "Percentual de escolas públicas",
            "Valor": _fmt_perc(r.get("perc_escolas_publicas")),
            "Fonte": "Indicador derivado INEP",
            "Leitura": "Ajuda a compreender o peso da rede pública na oferta educacional.",
        },
        {
            "Indicador-chave": "Ruralidade escolar",
            "Indicador selecionado": "Percentual de escolas rurais",
            "Valor": _fmt_perc(r.get("perc_escolas_rurais")),
            "Fonte": "Indicador derivado INEP",
            "Leitura": "Sinaliza desafios territoriais de acesso, deslocamento e integração com saúde.",
        },
        {
            "Indicador-chave": "Internet escolar",
            "Indicador selecionado": "Percentual de escolas com internet",
            "Valor": _fmt_perc(r.get("perc_escolas_com_internet")),
            "Fonte": "Indicador derivado INEP",
            "Leitura": "Apoia leitura de infraestrutura digital e capacidade de comunicação institucional.",
        },
        {
            "Indicador-chave": "Água na escola",
            "Indicador selecionado": "Percentual de escolas com água/rede",
            "Valor": _fmt_perc(r.get("perc_escolas_com_agua_rede")),
            "Fonte": "Indicador derivado INEP",
            "Leitura": "Indica condição básica de infraestrutura escolar.",
        },
        {
            "Indicador-chave": "Esgoto na escola",
            "Indicador selecionado": "Percentual de escolas com esgoto",
            "Valor": _fmt_perc(r.get("perc_escolas_com_esgoto")),
            "Fonte": "Indicador derivado INEP",
            "Leitura": "Sinaliza infraestrutura sanitária escolar, relevante para saúde e dignidade.",
        },
        {
            "Indicador-chave": "Energia elétrica",
            "Indicador selecionado": "Percentual de escolas com energia",
            "Valor": _fmt_perc(r.get("perc_escolas_com_energia")),
            "Fonte": "Indicador derivado INEP",
            "Leitura": "Condição básica para funcionamento escolar e inclusão tecnológica.",
        },
        {
            "Indicador-chave": "Biblioteca/sala de leitura",
            "Indicador selecionado": "Percentual de escolas com biblioteca ou sala de leitura",
            "Valor": _fmt_perc(r.get("perc_escolas_com_biblioteca_sala_leitura")),
            "Fonte": "Indicador derivado INEP",
            "Leitura": "Complementa a leitura socioeducacional do território.",
        },
        {
            "Indicador-chave": "Laboratório de informática",
            "Indicador selecionado": "Percentual de escolas com laboratório de informática",
            "Valor": _fmt_perc(r.get("perc_escolas_com_lab_informatica")),
            "Fonte": "Indicador derivado INEP",
            "Leitura": "Ajuda a avaliar capacidade de inclusão digital e infraestrutura pedagógica.",
        },
        {
            "Indicador-chave": "Quadra esportiva",
            "Indicador selecionado": "Percentual de escolas com quadra",
            "Valor": _fmt_perc(r.get("perc_escolas_com_quadra")),
            "Fonte": "Indicador derivado INEP",
            "Leitura": "Apoia leitura de espaços escolares para promoção da saúde e atividade física.",
        },
    ]

    resumo = pd.DataFrame(resumo_rows)

    alertas = []
    def add_alerta(cond, alerta, interpretacao, acao):
        if cond:
            alertas.append({
                "Alerta": alerta,
                "Interpretação": interpretacao,
                "Ação sugerida": acao,
            })

    add_alerta(
        str(r.get("alerta_rede", "")).upper() != "OK",
        "Checagem de rede escolar",
        str(r.get("alerta_rede", "Revisar contagem de escolas por dependência.")),
        "Validar dependência administrativa no arquivo do INEP.",
    )
    add_alerta(
        str(r.get("alerta_localizacao", "")).upper() != "OK",
        "Checagem de localização escolar",
        str(r.get("alerta_localizacao", "Revisar contagem de escolas por localização.")),
        "Validar localização urbana/rural no arquivo do INEP.",
    )

    # Alertas gerenciais por infraestrutura. Limiares são apenas triagem.
    try:
        pct_rural = float(r.get("perc_escolas_rurais", 0) or 0)
        pct_internet = float(r.get("perc_escolas_com_internet", 0) or 0)
        pct_agua = float(r.get("perc_escolas_com_agua_rede", 0) or 0)
        pct_esgoto = float(r.get("perc_escolas_com_esgoto", 0) or 0)
        pct_energia = float(r.get("perc_escolas_com_energia", 0) or 0)
    except Exception:
        pct_rural = pct_internet = pct_agua = pct_esgoto = pct_energia = 0

    add_alerta(
        pct_rural >= 50,
        "Alta ruralidade escolar",
        f"{_fmt_perc(pct_rural)} das escolas estão em área rural.",
        "Cruzar com distância até UBS, transporte escolar, equipes APS e territórios vulneráveis.",
    )
    add_alerta(
        pct_internet < 70,
        "Baixa conectividade escolar",
        f"Apenas {_fmt_perc(pct_internet)} das escolas têm internet registrada.",
        "Considerar conectividade como fator de vulnerabilidade educacional e comunicação em saúde.",
    )
    add_alerta(
        pct_agua < 90,
        "Infraestrutura de água escolar a validar",
        f"{_fmt_perc(pct_agua)} das escolas possuem água/rede registrada.",
        "Validar qualidade da variável e priorizar análise das escolas sem infraestrutura básica.",
    )
    add_alerta(
        pct_esgoto < 70,
        "Infraestrutura sanitária escolar crítica",
        f"{_fmt_perc(pct_esgoto)} das escolas possuem esgoto registrado.",
        "Cruzar com vulnerabilidade sanitária e condições domiciliares do território.",
    )
    add_alerta(
        pct_energia < 95,
        "Energia escolar a validar",
        f"{_fmt_perc(pct_energia)} das escolas possuem energia registrada.",
        "Validar cadastro e identificar escolas sem condição básica de funcionamento.",
    )

    alertas_df = pd.DataFrame(alertas)

    tecnica_cols = [
        "municipio", "escolas_total", "matriculas_total", "escolas_publicas", "escolas_privadas",
        "escolas_urbanas", "escolas_rurais", "escolas_com_internet", "escolas_com_agua_rede",
        "escolas_com_esgoto", "escolas_com_energia", "escolas_com_biblioteca_sala_leitura",
        "escolas_com_lab_informatica", "escolas_com_quadra", "metodo_matriculas", "metodo_escolas",
        "alerta_rede", "alerta_localizacao"
    ]
    tecnica = linha[[c for c in tecnica_cols if c in linha.columns]].copy()

    return {
        "ok": True,
        "mensagem": "Perfil educacional gerencial gerado.",
        "resumo": resumo,
        "alertas": alertas_df,
        "tecnica": tecnica,
        "total_indicadores": int(len(resumo)),
    }
