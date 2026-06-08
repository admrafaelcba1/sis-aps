
from __future__ import annotations

from pathlib import Path
import io
import re
import zipfile
import pandas as pd

from database.connection import get_connection
from database.queries import read_table


TABELA_RAW = "base_publica_sim_mortalidade_raw"
TABELA_MUNICIPAL = "base_publica_sim_mortalidade_municipal"
TABELA_RELATORIO = "base_publica_sim_mortalidade_relatorio"


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
    if low.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(data), dtype=str, nrows=nrows)
    if low.endswith(".dbf"):
        try:
            from dbfread import DBF
            tmp = Path("_tmp_sim_read.dbf")
            tmp.write_bytes(data)
            rows = []
            table = DBF(str(tmp), encoding="latin1", char_decode_errors="ignore")
            for i, rec in enumerate(table):
                if nrows and i >= nrows:
                    break
                rows.append(dict(rec))
            try:
                tmp.unlink()
            except Exception:
                pass
            return pd.DataFrame(rows).astype(str)
        except Exception as e:
            raise RuntimeError(f"Falha ao ler DBF. Converta para CSV ou instale dbfread. Detalhe: {e}")
    raise RuntimeError("Formato não suportado.")


def _iter_data_files(path: Path):
    path = Path(path)
    if path.is_dir():
        for p in path.rglob("*"):
            if p.suffix.lower() in [".csv", ".txt", ".dbf", ".zip", ".xlsx", ".xls"]:
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
                                if zj.filename.lower().endswith((".csv", ".txt", ".dbf", ".xlsx", ".xls")):
                                    yield f"{name}::{zj.filename}", inner.read(zj.filename)
                    except Exception:
                        continue
                elif name.lower().endswith((".csv", ".txt", ".dbf", ".xlsx", ".xls")):
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
    col_mun = _detectar_coluna(out, ["CODMUNRES", "MUNRES", "CO_MUN_RES", "codigo_municipio_residencia", "municipio_residencia"])
    if col_mun:
        cod = out[col_mun].astype(str).str.replace(r"\D", "", regex=True)
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
    if muni.empty or "codigo_ibge" not in muni.columns:
        return pd.DataFrame(columns=["codigo_ibge_7", "codigo_ibge_6", "municipio", "regiao_saude"])

    out = muni.copy()
    out["codigo_ibge_7"] = out["codigo_ibge"].astype(str).str.replace(r"\D", "", regex=True).str[:7]
    out["codigo_ibge_6"] = out["codigo_ibge_7"].str[:6]
    if "municipio" not in out.columns:
        out["municipio"] = out["codigo_ibge_7"]
    if "regiao_saude" not in out.columns:
        out["regiao_saude"] = ""
    return out[["codigo_ibge_7", "codigo_ibge_6", "municipio", "regiao_saude"]].drop_duplicates()


def _enriquecer_municipio(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    col_mun = _detectar_coluna(out, ["CODMUNRES", "MUNRES", "CO_MUN_RES", "codigo_municipio_residencia", "municipio_residencia"])
    if not col_mun:
        out["codigo_municipio_residencia"] = ""
        out["municipio"] = ""
        out["regiao_saude"] = ""
        return out

    out["codigo_municipio_residencia"] = out[col_mun].astype(str).str.replace(r"\D", "", regex=True)
    mapa = _mapa_municipios()
    if not mapa.empty:
        out = out.merge(mapa, left_on="codigo_municipio_residencia", right_on="codigo_ibge_7", how="left")
        if "municipio" in out.columns:
            sem = out["municipio"].isna()
        else:
            sem = pd.Series([True] * len(out), index=out.index)

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




def _normalizar_colunas_sqlite(df: pd.DataFrame) -> pd.DataFrame:
    """Garante nomes de colunas únicos considerando SQLite/case-insensitive.

    Arquivos convertidos do DATASUS podem trazer FONTE, fonte ou campos duplicados.
    O SQLite pode acusar duplicate column name mesmo quando pandas aceita.
    """
    out = df.copy()
    novos = []
    vistos = {}
    for c in out.columns:
        base = str(c).strip() or "coluna"
        # evita nomes internos que o sistema acrescenta depois
        if base.lower() in ["fonte", "arquivo_origem", "ano_referencia"]:
            base = f"{base}_origem"
        key = base.lower()
        if key in vistos:
            vistos[key] += 1
            base = f"{base}_{vistos[key]}"
            key = base.lower()
        else:
            vistos[key] = 0
        novos.append(base)
    out.columns = novos
    return out

def diagnosticar_sim_local(caminho: str) -> dict:
    path = Path(caminho)
    if not path.exists():
        return {"ok": False, "mensagem": "Caminho não encontrado.", "arquivos": pd.DataFrame()}

    registros = []
    for nome, data in _iter_data_files(path):
        if not nome.lower().endswith((".csv", ".txt", ".dbf", ".xlsx", ".xls")):
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


def importar_sim_local(caminho: str, ano_referencia: str = "") -> dict:
    path = Path(caminho)
    if not path.exists():
        return {"ok": False, "mensagem": "Caminho não encontrado.", "linhas": 0}

    frames = []
    rel = []
    for nome, data in _iter_data_files(path):
        if not nome.lower().endswith((".csv", ".txt", ".dbf", ".xlsx", ".xls")):
            continue
        try:
            df = _normalizar_colunas_sqlite(_read_file_bytes(nome, data))
            mt = _filtrar_mt(df)
            if mt.empty:
                rel.append({"arquivo": nome, "status": "Ignorado: sem MT detectado", "linhas_mt": 0})
                continue
            mt = _enriquecer_municipio(mt)
            mt["arquivo_origem"] = nome
            mt["fonte"] = "DATASUS/SIM"
            mt["ano_referencia"] = ano_referencia
            frames.append(mt)
            rel.append({"arquivo": nome, "status": "Importado", "linhas_mt": len(mt)})
        except Exception as e:
            rel.append({"arquivo": nome, "status": f"Erro: {e}", "linhas_mt": 0})

    if not frames:
        return {"ok": False, "mensagem": "Nenhuma linha de Mato Grosso foi importada.", "linhas": 0, "relatorio": pd.DataFrame(rel)}

    raw = _normalizar_colunas_sqlite(pd.concat(frames, ignore_index=True))
    with get_connection() as con:
        raw.to_sql(TABELA_RAW, con, if_exists="replace", index=False)
        pd.DataFrame(rel).to_sql(TABELA_RELATORIO, con, if_exists="replace", index=False)

    cons = consolidar_sim_municipal()
    return {
        "ok": True,
        "mensagem": "SIM importado.",
        "linhas": int(len(raw)),
        "arquivos": int(len(frames)),
        "relatorio": pd.DataFrame(rel),
        "consolidado": cons.get("df", pd.DataFrame()),
    }


def _idade_anos(df: pd.DataFrame) -> pd.Series:
    # SIM codifica IDADE frequentemente: primeiro dígito unidade, demais quantidade.
    col = _detectar_coluna(df, ["IDADE", "idade"])
    if not col:
        return pd.Series([float("nan")] * len(df), index=df.index, dtype="float64")

    s = df[col].astype(str).str.replace(r"\D", "", regex=True).str.zfill(3)
    unidade = s.str[0]
    valor = pd.to_numeric(s.str[1:], errors="coerce")

    anos = pd.Series([float("nan")] * len(df), index=df.index, dtype="float64")
    # 4 = anos, 5 = 100 anos ou mais. 3 = meses; 2 = dias; 1 = horas; 0 = minutos.
    anos.loc[unidade.eq("4")] = valor.loc[unidade.eq("4")]
    anos.loc[unidade.eq("5")] = 100.0
    anos.loc[unidade.isin(["0", "1", "2", "3"])] = 0.0
    return anos.fillna(-1)


def _causa_basica(df: pd.DataFrame) -> pd.Series:
    col = _detectar_coluna(df, ["CAUSABAS", "causa_basica", "causabas"])
    if col:
        return df[col].astype(str).str.upper().str.strip()
    return pd.Series([""] * len(df), index=df.index)


def _morte_materna(df: pd.DataFrame, causa: pd.Series) -> pd.Series:
    # CID-10 O00-O99
    return causa.str.match(r"^O[0-9]{2}", na=False).astype(int)


def _causas_externas(causa: pd.Series) -> pd.Series:
    return causa.str.match(r"^[V-W-X-Y]", na=False).astype(int)


def _doencas_cardiovasculares(causa: pd.Series) -> pd.Series:
    return causa.str.match(r"^I", na=False).astype(int)


def _neoplasias(causa: pd.Series) -> pd.Series:
    return causa.str.match(r"^C|^D0|^D1|^D2|^D3|^D4", na=False).astype(int)


def _respiratorias(causa: pd.Series) -> pd.Series:
    return causa.str.match(r"^J", na=False).astype(int)


def consolidar_sim_municipal() -> dict:
    raw = read_table(TABELA_RAW)
    if raw.empty:
        return {"ok": False, "mensagem": "Tabela SIM raw vazia.", "df": pd.DataFrame()}

    df = _enriquecer_municipio(raw)
    if "municipio" not in df.columns:
        return {"ok": False, "mensagem": "Município não identificado no SIM.", "df": pd.DataFrame()}

    idade = _idade_anos(df)
    causa = _causa_basica(df)

    df["_obitos"] = 1
    df["_obitos_infantis"] = (idade < 1).fillna(False).astype(int)
    df["_obitos_menores_5"] = (idade < 5).fillna(False).astype(int)
    df["_obitos_idosos"] = (idade >= 60).fillna(False).astype(int)
    df["_causas_externas"] = _causas_externas(causa)
    df["_cardiovasculares"] = _doencas_cardiovasculares(causa)
    df["_neoplasias"] = _neoplasias(causa)
    df["_respiratorias"] = _respiratorias(causa)
    df["_morte_materna"] = _morte_materna(df, causa)

    agg = df.groupby("municipio", dropna=False).agg(
        obitos_total=("_obitos", "sum"),
        obitos_infantis=("_obitos_infantis", "sum"),
        obitos_menores_5=("_obitos_menores_5", "sum"),
        obitos_idosos=("_obitos_idosos", "sum"),
        obitos_causas_externas=("_causas_externas", "sum"),
        obitos_cardiovasculares=("_cardiovasculares", "sum"),
        obitos_neoplasias=("_neoplasias", "sum"),
        obitos_respiratorias=("_respiratorias", "sum"),
        mortes_maternas=("_morte_materna", "sum"),
        registros_base=("_obitos", "sum"),
    ).reset_index()

    for col in [
        "obitos_infantis", "obitos_menores_5", "obitos_idosos",
        "obitos_causas_externas", "obitos_cardiovasculares",
        "obitos_neoplasias", "obitos_respiratorias", "mortes_maternas"
    ]:
        denom = pd.to_numeric(agg["obitos_total"], errors="coerce").replace(0, float("nan"))
        agg[f"perc_{col}"] = (pd.to_numeric(agg[col], errors="coerce").fillna(0) / denom * 100).fillna(0).round(2)

    if "ano_referencia" in df.columns:
        anos_ref = ", ".join(sorted([str(a) for a in df["ano_referencia"].dropna().unique() if str(a).strip()]))
    else:
        anos_ref = ""
    agg["ano_referencia"] = anos_ref
    agg["fonte"] = "DATASUS/SIM"
    agg["observacao_metodologica"] = (
        "Indicadores calculados por município de residência. "
        "Óbitos infantis usam idade codificada no SIM; grupos de causa usam CAUSABAS/CID-10."
    )

    # Evita pd.NA/NAType no SQLite e nos cálculos posteriores.
    agg = agg.replace({pd.NA: None})
    for _c in agg.columns:
        if str(agg[_c].dtype).startswith(("Int", "Float")):
            agg[_c] = pd.to_numeric(agg[_c], errors="coerce")
    agg = agg.where(pd.notnull(agg), None)

    with get_connection() as con:
        agg.to_sql(TABELA_MUNICIPAL, con, if_exists="replace", index=False)

    return {"ok": True, "mensagem": "Consolidado municipal SIM gerado.", "df": agg, "linhas": int(len(agg)), "colunas": int(len(agg.columns))}


def carregar_sim_municipal() -> pd.DataFrame:
    return read_table(TABELA_MUNICIPAL)


def relatorio_importacao_sim() -> pd.DataFrame:
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


def perfil_sim_municipio(municipio: str) -> dict:
    df = carregar_sim_municipal()
    if df.empty or "municipio" not in df.columns:
        return {"ok": False, "mensagem": "Consolidado SIM ainda não foi gerado.", "resumo": pd.DataFrame(), "alertas": pd.DataFrame()}

    alvo = str(municipio).strip().lower()
    linha = df[df["municipio"].astype(str).str.strip().str.lower().eq(alvo)]
    if linha.empty:
        return {"ok": False, "mensagem": "Município não encontrado no consolidado SIM.", "resumo": pd.DataFrame(), "alertas": pd.DataFrame()}

    r = linha.iloc[0]
    total_estadual = float(pd.to_numeric(df.get("obitos_total", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    resumo = pd.DataFrame([
        {"Indicador-chave": "Metadados", "Indicador": "Ano de referência", "Valor": str(r.get("ano_referencia", "") or "Não informado"), "Leitura": "Ano registrado na importação do SIM."},
        {"Indicador-chave": "Metadados", "Indicador": "Total estadual importado", "Valor": _fmt_num(total_estadual, 0), "Leitura": "Total de óbitos importados no consolidado estadual."},
        {"Indicador-chave": "Óbitos", "Indicador": "Total de óbitos", "Valor": _fmt_num(r.get("obitos_total"), 0), "Leitura": "Dimensiona o volume de óbitos por município de residência."},
        {"Indicador-chave": "Óbitos infantis", "Indicador": "Percentual de óbitos menores de 1 ano", "Valor": _fmt_perc(r.get("perc_obitos_infantis")), "Leitura": "Sinaliza atenção materno-infantil e condições de vida."},
        {"Indicador-chave": "Óbitos menores de 5 anos", "Indicador": "Percentual de óbitos menores de 5 anos", "Valor": _fmt_perc(r.get("perc_obitos_menores_5")), "Leitura": "Apoia leitura de vulnerabilidade infantil."},
        {"Indicador-chave": "Óbitos de idosos", "Indicador": "Percentual de óbitos de 60 anos ou mais", "Valor": _fmt_perc(r.get("perc_obitos_idosos")), "Leitura": "Apoia planejamento de cuidado crônico e envelhecimento."},
        {"Indicador-chave": "Causas externas", "Indicador": "Percentual de óbitos por causas externas", "Valor": _fmt_perc(r.get("perc_obitos_causas_externas")), "Leitura": "Sinaliza violências, acidentes e causas evitáveis intersetoriais."},
        {"Indicador-chave": "Cardiovasculares", "Indicador": "Percentual de óbitos por doenças cardiovasculares", "Valor": _fmt_perc(r.get("perc_obitos_cardiovasculares")), "Leitura": "Indica peso das condições crônicas no perfil de mortalidade."},
        {"Indicador-chave": "Neoplasias", "Indicador": "Percentual de óbitos por neoplasias", "Valor": _fmt_perc(r.get("perc_obitos_neoplasias")), "Leitura": "Apoia leitura de linhas de cuidado e rastreamento."},
        {"Indicador-chave": "Respiratórias", "Indicador": "Percentual de óbitos por doenças respiratórias", "Valor": _fmt_perc(r.get("perc_obitos_respiratorias")), "Leitura": "Complementa análise de condições crônicas, sazonalidade e vulnerabilidade."},
        {"Indicador-chave": "Mortalidade materna", "Indicador": "Mortes maternas registradas", "Valor": _fmt_num(r.get("mortes_maternas"), 0), "Leitura": "Evento sentinela para rede materno-infantil e vigilância."},
    ])

    alertas = []
    def add(cond, alerta, interp, acao):
        if cond:
            alertas.append({"Alerta": alerta, "Interpretação": interp, "Ação sugerida": acao})

    def val(col):
        try:
            v = r.get(col, 0)
            if pd.isna(v):
                return 0.0
            return float(v or 0)
        except Exception:
            return 0.0

    add(val("obitos_total") == 0, "Sem óbitos registrados", "O município não possui óbitos no consolidado importado.", "Validar se a base/ano contém todos os registros esperados.")
    add(val("mortes_maternas") > 0, "Morte materna registrada", f"{_fmt_num(r.get('mortes_maternas'),0)} morte(s) materna(s) registrada(s).", "Tratar como evento sentinela e validar com vigilância.")
    add(val("perc_obitos_infantis") >= 5, "Participação relevante de óbitos infantis", f"{_fmt_perc(r.get('perc_obitos_infantis'))} dos óbitos são infantis.", "Cruzar com SINASC, pré-natal, baixo peso e cobertura APS.")
    add(val("perc_obitos_causas_externas") >= 15, "Causas externas relevantes", f"{_fmt_perc(r.get('perc_obitos_causas_externas'))} dos óbitos por causas externas.", "Cruzar com violências, acidentes, trânsito e políticas intersetoriais.")
    add(val("perc_obitos_cardiovasculares") >= 30, "Peso elevado de doenças cardiovasculares", f"{_fmt_perc(r.get('perc_obitos_cardiovasculares'))} dos óbitos por cardiovasculares.", "Cruzar com hipertensão, diabetes e linhas de cuidado da APS.")

    return {
        "ok": True,
        "mensagem": "Perfil SIM gerado.",
        "resumo": resumo,
        "alertas": pd.DataFrame(alertas),
        "tecnica": linha.copy(),
    }


def resumo_validacao_sim() -> dict:
    cons = carregar_sim_municipal()
    raw = read_table(TABELA_RAW)

    if cons.empty:
        return {
            "ok": False,
            "mensagem": "Consolidado SIM ainda não foi gerado.",
            "resumo": pd.DataFrame(),
            "ranking_alertas": pd.DataFrame(),
            "municipios_sem_registro": pd.DataFrame(),
        }

    total_obitos = float(cons.get("obitos_total", pd.Series(dtype=float)).sum())
    total_mun = int(cons["municipio"].nunique()) if "municipio" in cons.columns else int(len(cons))
    ano_ref = ""
    if not raw.empty and "ano_referencia" in raw.columns:
        anos = sorted([str(a) for a in raw["ano_referencia"].dropna().unique() if str(a).strip()])
        ano_ref = ", ".join(anos)

    resumo = pd.DataFrame([
        {"Indicador": "Ano de referência informado", "Valor": ano_ref or "Não informado", "Leitura": "Ano registrado no momento da importação."},
        {"Indicador": "Municípios com registros SIM", "Valor": _fmt_num(total_mun, 0), "Leitura": "Quantidade de municípios presentes no consolidado."},
        {"Indicador": "Total estadual de óbitos importados", "Valor": _fmt_num(total_obitos, 0), "Leitura": "Soma dos óbitos importados para Mato Grosso."},
        {"Indicador": "Base de consolidação", "Valor": "Município de residência", "Leitura": "Critério usado para leitura municipal."},
        {"Indicador": "Fonte", "Valor": "DATASUS/SIM", "Leitura": "Base administrativa de mortalidade."},
    ])

    work = cons.copy()
    for col in ["perc_obitos_infantis", "perc_obitos_causas_externas", "perc_obitos_cardiovasculares", "mortes_maternas"]:
        if col not in work.columns:
            work[col] = 0
        work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0)

    work["pontos_alerta"] = 0
    work["pontos_alerta"] += (work["mortes_maternas"] > 0).astype(int)
    work["pontos_alerta"] += (work["perc_obitos_infantis"] >= 5).astype(int)
    work["pontos_alerta"] += (work["perc_obitos_causas_externas"] >= 15).astype(int)
    work["pontos_alerta"] += (work["perc_obitos_cardiovasculares"] >= 30).astype(int)

    work["alertas_identificados"] = work.apply(
        lambda r: "; ".join([
            nome for nome, cond in [
                ("morte materna registrada", r["mortes_maternas"] > 0),
                ("óbitos infantis >=5%", r["perc_obitos_infantis"] >= 5),
                ("causas externas >=15%", r["perc_obitos_causas_externas"] >= 15),
                ("cardiovasculares >=30%", r["perc_obitos_cardiovasculares"] >= 30),
            ] if cond
        ]) or "Sem alerta pelos limiares atuais",
        axis=1
    )

    ranking_cols = [
        "municipio", "obitos_total", "pontos_alerta", "alertas_identificados",
        "obitos_infantis", "mortes_maternas", "perc_obitos_infantis",
        "perc_obitos_causas_externas", "perc_obitos_cardiovasculares",
    ]
    ranking = work[[c for c in ranking_cols if c in work.columns]].sort_values(
        ["pontos_alerta", "obitos_total"], ascending=[False, False]
    ).reset_index(drop=True)

    muni = read_table("municipios")
    if muni.empty:
        muni = read_table("malhas_geograficas_municipais")
    sem = pd.DataFrame()
    if not muni.empty and "municipio" in muni.columns and "municipio" in cons.columns:
        base_m = muni[["municipio"]].drop_duplicates().copy()
        base_m["_key"] = base_m["municipio"].astype(str).str.strip().str.lower()
        cons_keys = set(cons["municipio"].astype(str).str.strip().str.lower())
        sem = base_m[~base_m["_key"].isin(cons_keys)].drop(columns=["_key"]).reset_index(drop=True)
        sem["observacao"] = "Município não localizado no consolidado SIM importado."

    return {
        "ok": True,
        "mensagem": "Validação SIM gerada.",
        "resumo": resumo,
        "ranking_alertas": ranking,
        "municipios_sem_registro": sem,
        "consolidado": cons,
    }
