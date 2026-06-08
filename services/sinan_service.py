
from __future__ import annotations

from pathlib import Path
import io
import re
import zipfile
import pandas as pd

from database.connection import get_connection
from database.queries import read_table


TABELA_RAW = "base_publica_sinan_raw"
TABELA_MUNICIPAL = "base_publica_sinan_municipal"
TABELA_RELATORIO = "base_publica_sinan_relatorio"


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
            tmp = Path("_tmp_sinan_read.dbf")
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


def _normalizar_colunas_sqlite(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    novos = []
    vistos = {}
    for c in out.columns:
        base = str(c).strip() or "coluna"
        # Preserva colunas internas adicionadas pelo sistema (agravo, ano_referencia, fonte).
        # Colunas realmente duplicadas continuam recebendo sufixo numérico abaixo.
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


def _filtrar_mt(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    col_mun = _detectar_coluna(out, [
        "ID_MN_RESI", "ID_MUNICIP", "MUN_RES", "CODMUNRES", "CO_MUN_RES",
        "municipio_residencia", "codigo_municipio_residencia"
    ])
    if col_mun:
        cod = out[col_mun].astype(str).str.replace(r"\D", "", regex=True)
        filtrado = out[cod.str.startswith("51", na=False)].copy()
        if not filtrado.empty:
            return filtrado

    col_uf = _detectar_coluna(out, ["SG_UF", "UF", "ID_UF", "CO_UF", "uf_residencia"])
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
    col_mun = _detectar_coluna(out, [
        "ID_MN_RESI", "ID_MUNICIP", "MUN_RES", "CODMUNRES", "CO_MUN_RES",
        "municipio_residencia", "codigo_municipio_residencia"
    ])
    if not col_mun:
        out["codigo_municipio_residencia"] = ""
        out["municipio"] = ""
        out["regiao_saude"] = ""
        return out

    out["codigo_municipio_residencia"] = out[col_mun].astype(str).str.replace(r"\D", "", regex=True)
    mapa = _mapa_municipios()
    if not mapa.empty:
        out = out.merge(mapa, left_on="codigo_municipio_residencia", right_on="codigo_ibge_7", how="left")
        sem = out["municipio"].isna() if "municipio" in out.columns else pd.Series([True] * len(out), index=out.index)
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


def diagnosticar_sinan_local(caminho: str) -> dict:
    path = Path(caminho)
    if not path.exists():
        return {"ok": False, "mensagem": "Caminho não encontrado.", "arquivos": pd.DataFrame()}

    registros = []
    for nome, data in _iter_data_files(path):
        if not nome.lower().endswith((".csv", ".txt", ".dbf", ".xlsx", ".xls")):
            continue
        try:
            sample = _normalizar_colunas_sqlite(_read_file_bytes(nome, data, nrows=2000))
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


def importar_sinan_local(caminho: str, agravo: str = "Agravos SINAN", ano_referencia: str = "") -> dict:
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
            mt["fonte"] = "DATASUS/SINAN"
            mt["ano_referencia"] = ano_referencia
            mt["agravo"] = agravo
            frames.append(mt)
            rel.append({"arquivo": nome, "status": "Importado", "linhas_mt": len(mt), "agravo": agravo})
        except Exception as e:
            rel.append({"arquivo": nome, "status": f"Erro: {e}", "linhas_mt": 0, "agravo": agravo})

    if not frames:
        return {"ok": False, "mensagem": "Nenhuma linha de Mato Grosso foi importada.", "linhas": 0, "relatorio": pd.DataFrame(rel)}

    raw_novo = pd.concat(frames, ignore_index=True)
    # Mantém histórico de agravos, substituindo o mesmo agravo/ano se já existir.
    raw_atual = read_table(TABELA_RAW)
    if not raw_atual.empty and "agravo" in raw_atual.columns:
        mask = ~(
            raw_atual["agravo"].astype(str).str.lower().eq(str(agravo).lower())
            & raw_atual.get("ano_referencia", "").astype(str).eq(str(ano_referencia))
        )
        raw = pd.concat([raw_atual[mask], raw_novo], ignore_index=True)
    else:
        raw = raw_novo

    raw = _normalizar_colunas_sqlite(raw)
    with get_connection() as con:
        raw.to_sql(TABELA_RAW, con, if_exists="replace", index=False)
        pd.DataFrame(rel).to_sql(TABELA_RELATORIO, con, if_exists="replace", index=False)

    cons = consolidar_sinan_municipal()
    return {
        "ok": True,
        "mensagem": "SINAN importado.",
        "linhas": int(len(raw_novo)),
        "arquivos": int(len(frames)),
        "relatorio": pd.DataFrame(rel),
        "consolidado": cons.get("df", pd.DataFrame()),
    }


def _num(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _evolucao_obito(df: pd.DataFrame) -> pd.Series:
    col = _detectar_coluna(df, ["EVOLUCAO", "evolucao", "TP_EVOLUCAO"])
    if col:
        s = df[col].astype(str).str.strip().str.upper()
        # Em muitos layouts SINAN, 2 = óbito pelo agravo; 3 = óbito por outras causas.
        return s.isin(["2", "3", "OBITO", "ÓBITO", "OBITO PELO AGRAVO", "OBITO POR OUTRAS CAUSAS"]).astype(int)
    return pd.Series([0] * len(df), index=df.index)


def _confirmado(df: pd.DataFrame) -> pd.Series:
    col = _detectar_coluna(df, ["CLASSI_FIN", "CLASSIFIN", "CLASSIFICACAO_FINAL"])
    if col:
        s = df[col].astype(str).str.strip()
        # Para dengue e várias fichas: 5 descartado; 8 inconclusivo. Mantém demais como prováveis/confirmados preliminares.
        return (~s.isin(["5", "8", "13"])).astype(int)
    return pd.Series([1] * len(df), index=df.index)


def _hospitalizado(df: pd.DataFrame) -> pd.Series:
    col = _detectar_coluna(df, ["HOSPITALIZ", "HOSPITALIZACAO", "IN_HOSPITALIZACAO"])
    if col:
        return df[col].astype(str).str.strip().isin(["1", "SIM", "S"]).astype(int)
    return pd.Series([0] * len(df), index=df.index)


def consolidar_sinan_municipal() -> dict:
    raw = read_table(TABELA_RAW)
    if raw.empty:
        return {"ok": False, "mensagem": "Tabela SINAN raw vazia.", "df": pd.DataFrame()}

    df = _enriquecer_municipio(raw)
    if "municipio" not in df.columns:
        return {"ok": False, "mensagem": "Município não identificado no SINAN.", "df": pd.DataFrame()}

    if "agravo" not in df.columns:
        df["agravo"] = "Agravos SINAN"
    if "ano_referencia" not in df.columns:
        df["ano_referencia"] = ""

    df["_notificacoes"] = 1
    df["_confirmados_provaveis"] = _confirmado(df)
    df["_obitos"] = _evolucao_obito(df)
    df["_hospitalizacoes"] = _hospitalizado(df)

    agg = df.groupby(["municipio", "agravo", "ano_referencia"], dropna=False).agg(
        notificacoes=("_notificacoes", "sum"),
        registros_considerados=("_confirmados_provaveis", "sum"),
        obitos=("_obitos", "sum"),
        hospitalizacoes=("_hospitalizacoes", "sum"),
        registros_base=("_notificacoes", "sum"),
    ).reset_index()

    # Coluna de compatibilidade com versões anteriores.
    agg["casos_confirmados_provaveis"] = agg["registros_considerados"]

    denom = pd.to_numeric(agg["notificacoes"], errors="coerce").replace(0, float("nan"))
    for col in ["registros_considerados", "obitos", "hospitalizacoes"]:
        agg[f"perc_{col}"] = (pd.to_numeric(agg[col], errors="coerce").fillna(0) / denom * 100).fillna(0).round(2)

    if "perc_registros_considerados" in agg.columns:
        agg["perc_casos_confirmados_provaveis"] = agg["perc_registros_considerados"]

    agg["fonte"] = "DATASUS/SINAN"
    agg["observacao_metodologica"] = (
        "Consolidação preliminar por município de residência/notificação conforme campo disponível. "
        "As variáveis do SINAN variam por agravo; valide o layout específico antes de uso oficial."
    )

    if "agravo" in agg.columns:
        agg["ordem_agravo"] = agg["agravo"].apply(_ordem_agravo)
        agg = agg.sort_values(["municipio", "ordem_agravo", "agravo"], na_position="last").drop(columns=["ordem_agravo"], errors="ignore")


    agg = agg.replace({pd.NA: None}).where(pd.notnull(agg), None)

    with get_connection() as con:
        agg.to_sql(TABELA_MUNICIPAL, con, if_exists="replace", index=False)

    return {"ok": True, "mensagem": "Consolidado municipal SINAN gerado.", "df": agg, "linhas": int(len(agg)), "colunas": int(len(agg.columns))}


def carregar_sinan_municipal() -> pd.DataFrame:
    return read_table(TABELA_MUNICIPAL)


def relatorio_importacao_sinan() -> pd.DataFrame:
    return read_table(TABELA_RELATORIO)


def _fmt_num(v, casas: int = 1):
    try:
        if pd.isna(v):
            return "-"
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



ORDEM_AGRAVOS_SINAN = {
    "ANIM": 1,
    "HANS": 2,
    "TUBE": 3,
    "VIOL": 4,
}


def _ordem_agravo(valor: str) -> int:
    v = str(valor or "").upper().strip()
    for pref, ordem in ORDEM_AGRAVOS_SINAN.items():
        if v.startswith(pref):
            return ordem
    return 99


def preparar_view_sinan_gerencial(df: pd.DataFrame) -> pd.DataFrame:
    """Retorna uma visão limpa para interface, ocultando colunas de compatibilidade."""
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()
    if "ordem_agravo" not in out.columns and "agravo" in out.columns:
        out["ordem_agravo"] = out["agravo"].apply(_ordem_agravo)

    cols = [
        "municipio",
        "agravo",
        "ano_referencia",
        "notificacoes",
        "registros_considerados",
        "obitos",
        "hospitalizacoes",
        "registros_base",
        "perc_registros_considerados",
        "perc_obitos",
        "perc_hospitalizacoes",
        "fonte",
        "observacao_metodologica",
    ]
    cols = [c for c in cols if c in out.columns]
    out = out[cols].copy()

    rename = {
        "municipio": "Município",
        "agravo": "Agravo",
        "ano_referencia": "Ano Referência",
        "notificacoes": "Notificações",
        "registros_considerados": "Registros Considerados",
        "obitos": "Óbitos",
        "hospitalizacoes": "Hospitalizações",
        "registros_base": "Registros Base",
        "perc_registros_considerados": "% Registros Considerados",
        "perc_obitos": "% Óbitos",
        "perc_hospitalizacoes": "% Hospitalizações",
        "fonte": "Fonte",
        "observacao_metodologica": "Observação Metodológica",
    }
    out = out.rename(columns=rename)
    return out


def carregar_sinan_municipal_gerencial() -> pd.DataFrame:
    df = carregar_sinan_municipal()
    if df.empty:
        return df
    if "agravo" in df.columns:
        df = df.copy()
        df["_ordem_agravo"] = df["agravo"].apply(_ordem_agravo)
        sort_cols = [c for c in ["municipio", "_ordem_agravo", "agravo"] if c in df.columns]
        df = df.sort_values(sort_cols).drop(columns=["_ordem_agravo"], errors="ignore")
    return preparar_view_sinan_gerencial(df)

def perfil_sinan_municipio(municipio: str) -> dict:
    df = carregar_sinan_municipal()
    if df.empty or "municipio" not in df.columns:
        return {"ok": False, "mensagem": "Consolidado SINAN ainda não foi gerado.", "resumo": pd.DataFrame(), "alertas": pd.DataFrame()}

    alvo = str(municipio).strip().lower()
    linhas = df[df["municipio"].astype(str).str.strip().str.lower().eq(alvo)]
    if linhas.empty:
        return {"ok": False, "mensagem": "Município não encontrado no consolidado SINAN.", "resumo": pd.DataFrame(), "alertas": pd.DataFrame()}

    resumo = linhas.copy()
    if "notificacoes" in resumo.columns:
        resumo = resumo.sort_values("notificacoes", ascending=False)

    tabela = pd.DataFrame([
        {
            "Agravo": r.get("agravo", "-"),
            "Ano": r.get("ano_referencia", "-") or "-",
            "Notificações": _fmt_num(r.get("notificacoes"), 0),
            "Registros considerados": _fmt_num(r.get("registros_considerados"), 0),
            "Óbitos": _fmt_num(r.get("obitos"), 0),
            "Hospitalizações": _fmt_num(r.get("hospitalizacoes"), 0),
            "% óbitos": _fmt_perc(r.get("perc_obitos")),
            "Leitura": "Agravos de notificação compulsória para leitura integrada entre vigilância e APS.",
        }
        for _, r in resumo.iterrows()
    ])

    alertas = []
    for _, r in resumo.iterrows():
        agr = r.get("agravo", "Agravo SINAN")
        try:
            notif = float(r.get("notificacoes", 0) or 0)
            ob = float(r.get("obitos", 0) or 0)
            hosp = float(r.get("hospitalizacoes", 0) or 0)
        except Exception:
            notif = ob = hosp = 0
        if ob > 0:
            alertas.append({"Alerta": f"Óbito registrado — {agr}", "Interpretação": f"{_fmt_num(ob,0)} óbito(s) informado(s).", "Ação sugerida": "Validar com Vigilância Epidemiológica e investigar oportunidade de cuidado."})
        if notif >= 100:
            alertas.append({"Alerta": f"Volume relevante de notificações — {agr}", "Interpretação": f"{_fmt_num(notif,0)} notificações no período.", "Ação sugerida": "Analisar distribuição territorial, sazonalidade e resposta da APS/Vigilância."})
        if hosp > 0:
            alertas.append({"Alerta": f"Hospitalizações registradas — {agr}", "Interpretação": f"{_fmt_num(hosp,0)} hospitalização(ões) informada(s).", "Ação sugerida": "Cruzar com gravidade, acesso oportuno e capacidade de resposta local."})

    return {
        "ok": True,
        "mensagem": "Perfil SINAN gerado.",
        "resumo": tabela,
        "alertas": pd.DataFrame(alertas),
        "tecnica": linhas.copy(),
    }


def resumo_validacao_sinan() -> dict:
    cons = carregar_sinan_municipal()
    raw = read_table(TABELA_RAW)
    if cons.empty:
        return {"ok": False, "mensagem": "Consolidado SINAN ainda não foi gerado.", "resumo": pd.DataFrame(), "ranking_alertas": pd.DataFrame()}

    total_not = float(pd.to_numeric(cons.get("notificacoes", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    total_mun = int(cons["municipio"].nunique()) if "municipio" in cons.columns else int(len(cons))
    agravos = ", ".join(sorted(cons["agravo"].astype(str).dropna().unique())) if "agravo" in cons.columns else "-"
    anos = ", ".join(sorted([str(a) for a in cons.get("ano_referencia", pd.Series(dtype=str)).dropna().unique() if str(a).strip()]))

    resumo = pd.DataFrame([
        {"Indicador": "Anos de referência", "Valor": anos or "Não informado", "Leitura": "Anos registrados no momento da importação."},
        {"Indicador": "Agravos importados", "Valor": agravos or "-", "Leitura": "Bases SINAN disponíveis no consolidado."},
        {"Indicador": "Municípios com registros SINAN", "Valor": _fmt_num(total_mun, 0), "Leitura": "Quantidade de municípios presentes no consolidado."},
        {"Indicador": "Total estadual de notificações importadas", "Valor": _fmt_num(total_not, 0), "Leitura": "Soma das notificações importadas."},
        {"Indicador": "Fonte", "Valor": "DATASUS/SINAN", "Leitura": "Base administrativa de agravos de notificação."},
    ])

    work = cons.copy()
    for c in ["notificacoes", "obitos", "hospitalizacoes"]:
        if c not in work.columns:
            work[c] = 0
        work[c] = pd.to_numeric(work[c], errors="coerce").fillna(0)
    work["pontos_alerta"] = 0
    work["pontos_alerta"] += (work["notificacoes"] >= 100).astype(int)
    work["pontos_alerta"] += (work["obitos"] > 0).astype(int)
    work["pontos_alerta"] += (work["hospitalizacoes"] > 0).astype(int)

    ranking = work.sort_values(["pontos_alerta", "notificacoes"], ascending=[False, False]).reset_index(drop=True)
    return {"ok": True, "mensagem": "Validação SINAN gerada.", "resumo": resumo, "ranking_alertas": ranking, "consolidado": cons}
