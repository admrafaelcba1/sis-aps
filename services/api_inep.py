from __future__ import annotations

import io
import os
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
import requests

try:
    from config.settings import DATA_DIR, UPLOADS_DIR, RAW_DIR
except Exception:
    ROOT_DIR = Path(__file__).resolve().parents[1]
    DATA_DIR = ROOT_DIR / "data"
    UPLOADS_DIR = DATA_DIR / "uploads"
    RAW_DIR = DATA_DIR / "raw"

INEP_ANO_PADRAO = 2024
URLS_CENSO_ESCOLAR = [
    "https://download.inep.gov.br/microdados/microdados_censo_escolar_2024.zip",
    "https://download.inep.gov.br/dados_abertos/microdados_censo_escolar_2024.zip",
    "https://download.inep.gov.br/microdados/microdados_educacao_basica_2024.zip",
    "https://download.inep.gov.br/microdados/microdados_ed_basica_2024.zip",
]


def _canonical_municipios_por_codigo() -> dict[str, str]:
    """Mapa de código IBGE -> nome canônico usado pelo sistema.

    Evita perda de municípios por diferença de grafia entre INEP/IBGE
    (ex.: Santo Antônio de/do Leverger).
    """
    try:
        from config.ibge_estimativas_2025_mt import ESTIMATIVAS_POPULACAO_2025_MT
        return {str(item.get("codigo_ibge")): str(item.get("municipio")) for item in ESTIMATIVAS_POPULACAO_2025_MT}
    except Exception:
        return {}


def _aplicar_municipio_canonico(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    mapa = _canonical_municipios_por_codigo()
    if mapa and "codigo_ibge" in out.columns:
        cod = pd.to_numeric(out["codigo_ibge"], errors="coerce").astype("Int64").astype(str)
        canon = cod.map(mapa)
        if "municipio" not in out.columns:
            out["municipio"] = ""
        out["municipio"] = canon.fillna(out["municipio"].astype(str))
    return out


def _root_data_dir() -> Path:
    p = Path(DATA_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _uploads_inep_dir() -> Path:
    p = Path(UPLOADS_DIR) / "inep"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _raw_apis_dir() -> Path:
    p = Path(RAW_DIR) / "apis"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _norm_col(c: str) -> str:
    c = str(c).strip().upper()
    c = unicodedata.normalize("NFKD", c).encode("ascii", "ignore").decode("ascii")
    c = re.sub(r"[^A-Z0-9_]+", "_", c)
    c = re.sub(r"_+", "_", c).strip("_")
    return c


def _norm_text(x) -> str:
    if pd.isna(x):
        return ""
    s = str(x).strip()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"\s+", " ", s).strip().upper()
    return s


def _as_int_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(r"\.0$", "", regex=True).str.extract(r"(\d+)", expand=False), errors="coerce")


def _find_local_zip() -> Optional[Path]:
    candidates = []
    for folder in [_uploads_inep_dir(), _raw_apis_dir(), Path(DATA_DIR)]:
        if folder.exists():
            candidates.extend(folder.glob("*.zip"))
            candidates.extend(folder.glob("*.ZIP"))
    if not candidates:
        return None
    scored = []
    for p in candidates:
        name = p.name.lower()
        score = 0
        if "censo" in name: score += 3
        if "escolar" in name or "educacao" in name or "basica" in name: score += 3
        if "2024" in name: score += 2
        scored.append((score, p.stat().st_mtime, p))
    scored.sort(reverse=True)
    return scored[0][2]


def _download_zip() -> Path:
    dest = _uploads_inep_dir() / f"microdados_censo_escolar_{INEP_ANO_PADRAO}.zip"
    if dest.exists() and dest.stat().st_size > 1024:
        return dest
    errors = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for url in URLS_CENSO_ESCOLAR:
        try:
            r = requests.get(url, headers=headers, timeout=120, verify=False)
            r.raise_for_status()
            content = r.content
            if not zipfile.is_zipfile(io.BytesIO(content)):
                raise ValueError("Resposta baixada não é ZIP válido")
            dest.write_bytes(content)
            return dest
        except Exception as e:
            errors.append(f"{url} -> {e}")
    raise RuntimeError("Não foi possível baixar automaticamente os microdados do Censo Escolar. " + " | ".join(errors))


def _get_zip_path() -> Path:
    local = _find_local_zip()
    if local:
        return local
    return _download_zip()


def _list_members(zf: zipfile.ZipFile) -> list[str]:
    return [n for n in zf.namelist() if not n.endswith("/") and n.lower().endswith((".csv", ".txt"))]


def _classify_members(members: Iterable[str]) -> tuple[list[str], list[str]]:
    school = []
    matric = []
    for n in members:
        base = Path(n).name.lower()
        if any(k in base for k in ["escola", "escolas", "cadastro"]):
            school.append(n)
        if any(k in base for k in ["matricula", "matriculas", "matr"]):
            matric.append(n)
        if "microdados_ed_basica" in base or "microdados_censo_escolar" in base:
            school.append(n)
    # prioriza arquivos de escola, depois arquivo geral
    school = sorted(set(school), key=lambda x: ("escola" not in Path(x).name.lower(), len(x)))
    matric = sorted(set(matric), key=lambda x: ("matricula" not in Path(x).name.lower(), len(x)))
    return school, matric


def _read_header(zf: zipfile.ZipFile, member: str) -> list[str]:
    with zf.open(member) as f:
        sample = f.read(200000)
    for enc in ["utf-8-sig", "latin1", "cp1252"]:
        try:
            txt = sample.decode(enc, errors="ignore")
            break
        except Exception:
            continue
    first = txt.splitlines()[0]
    sep = ";" if first.count(";") >= first.count(",") else ","
    return [_norm_col(c) for c in first.split(sep)]


def _read_csv_member(zf: zipfile.ZipFile, member: str, usecols: Optional[list[str]] = None, chunksize: Optional[int] = None):
    header = _read_header(zf, member)
    # usecols recebe colunas normalizadas; precisamos mapear para nomes originais
    with zf.open(member) as f:
        first_line = f.readline().decode("latin1", errors="ignore")
    sep = ";" if first_line.count(";") >= first_line.count(",") else ","
    original_cols = [c.strip() for c in first_line.strip().split(sep)]
    norm_map = {_norm_col(orig): orig for orig in original_cols}
    actual_usecols = None
    if usecols:
        actual_usecols = [norm_map[c] for c in usecols if c in norm_map]
        if not actual_usecols:
            actual_usecols = None
    return pd.read_csv(
        zf.open(member),
        sep=sep,
        encoding="latin1",
        dtype=str,
        low_memory=False,
        usecols=actual_usecols,
        chunksize=chunksize,
    )


def _standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [_norm_col(c) for c in out.columns]
    return out


def _uf_filter_mask(df: pd.DataFrame) -> pd.Series:
    cols = set(df.columns)
    mask = pd.Series(False, index=df.index)
    if "SG_UF" in cols:
        mask = mask | (df["SG_UF"].astype(str).str.upper().str.strip() == "MT")
    for c in ["CO_UF", "COD_UF", "UF"]:
        if c in cols:
            mask = mask | (_as_int_series(df[c]) == 51)
    if "NO_UF" in cols:
        mask = mask | (df["NO_UF"].map(_norm_text).isin(["MATO GROSSO", "MT"]))
    if "CO_MUNICIPIO" in cols:
        mask = mask | (_as_int_series(df["CO_MUNICIPIO"]).astype("Int64").astype(str).str.startswith("510"))
    return mask


def _first_existing(cols: set[str], candidates: list[str]) -> Optional[str]:
    for c in candidates:
        if c in cols:
            return c
    return None


def _is_yes(df: pd.DataFrame, col: Optional[str]) -> pd.Series:
    if not col or col not in df.columns:
        return pd.Series(False, index=df.index)
    s = df[col].astype(str).str.strip().str.upper()
    return s.isin(["1", "SIM", "S", "TRUE", "VERDADEIRO"])


def _aggregate_school_file(zf: zipfile.ZipFile, member: str) -> Optional[pd.DataFrame]:
    header = _read_header(zf, member)
    cols = set(header)
    mun_col = _first_existing(cols, ["CO_MUNICIPIO", "COD_MUNICIPIO", "CO_MUNICIPIO_END", "COD_MUN"])
    nome_col = _first_existing(cols, ["NO_MUNICIPIO", "NOME_MUNICIPIO", "NO_MUNICIPIO_END", "MUNICIPIO"])
    escola_col = _first_existing(cols, ["CO_ENTIDADE", "COD_ESCOLA", "ID_ESCOLA", "CO_ESCOLA"])
    if not mun_col or not escola_col:
        return None

    use = [c for c in [
        "SG_UF", "CO_UF", "NO_UF", mun_col, nome_col, escola_col,
        "TP_LOCALIZACAO", "TP_LOCALIZACAO_DIFERENCIADA",
        "IN_EDUCACAO_INDIGENA", "IN_EDUCACAO_ESCOLAR_INDIGENA",
        "IN_MATERIAL_PED_QUILOMBOLA", "IN_MATERIAL_ESP_QUILOMBOLA",
        "IN_ESPECIAL_EXCLUSIVA", "IN_REGULAR", "IN_AEE", "IN_MEDIACAO_PRESENCIAL", "IN_ATENDIMENTO_ESPECIALIZADO",
        "IN_COMUM_INCLUSIVA", "IN_ACESSIBILIDADE_INEXISTENTE", "TP_AEE",
        "QT_MAT_BAS", "QT_MAT_INF", "QT_MAT_FUND", "QT_MAT_MED", "QT_MAT_EJA", "QT_MAT_ESP", "QT_MAT_AEE",
        "QT_MAT_ESP_CC", "QT_MAT_ESP_CE", "QT_MAT_AEE_DV", "QT_MAT_AEE_SURDEZ", "QT_MAT_AEE_DI",
        "QT_MAT_AEE_TEA", "QT_MAT_AEE_ALTAS",
    ] if c in cols]

    acc = []
    reader = _read_csv_member(zf, member, usecols=use, chunksize=100000)
    if not hasattr(reader, "__iter__") or isinstance(reader, pd.DataFrame):
        reader = [reader]
    for chunk in reader:
        chunk = _standardize_columns(chunk)
        if chunk.empty:
            continue
        mask = _uf_filter_mask(chunk)
        mt = chunk.loc[mask].copy()
        if mt.empty:
            continue
        mt["codigo_ibge"] = _as_int_series(mt[mun_col]).astype("Int64")
        mt = mt[mt["codigo_ibge"].notna()]
        if mt.empty:
            continue
        mt["municipio"] = mt[nome_col] if nome_col and nome_col in mt.columns else ""
        mt["_escola"] = mt[escola_col].astype(str)
        loc = _as_int_series(mt["TP_LOCALIZACAO"]) if "TP_LOCALIZACAO" in mt.columns else pd.Series(pd.NA, index=mt.index)
        dif = _as_int_series(mt["TP_LOCALIZACAO_DIFERENCIADA"]) if "TP_LOCALIZACAO_DIFERENCIADA" in mt.columns else pd.Series(pd.NA, index=mt.index)
        mt["_urbana"] = loc.eq(1)
        mt["_rural"] = loc.eq(2)
        mt["_indigena"] = dif.eq(2) | _is_yes(mt, "IN_EDUCACAO_INDIGENA") | _is_yes(mt, "IN_EDUCACAO_ESCOLAR_INDIGENA")
        mt["_quilombola"] = dif.eq(3) | _is_yes(mt, "IN_MATERIAL_PED_QUILOMBOLA") | _is_yes(mt, "IN_MATERIAL_ESP_QUILOMBOLA")
        # Educação especial/AEE: em alguns layouts vem como flag; em outros,
        # principalmente como quantidade de matrículas por escola.
        special_cols = [c for c in ["IN_ESPECIAL_EXCLUSIVA", "IN_AEE", "IN_ATENDIMENTO_ESPECIALIZADO", "IN_COMUM_INCLUSIVA"] if c in mt.columns]
        mt["_especial"] = False
        for c in special_cols:
            mt["_especial"] = mt["_especial"] | _is_yes(mt, c)

        special_qtd_cols = [c for c in mt.columns if (c.startswith("QT_MAT_ESP") or c.startswith("QT_MAT_AEE"))]
        for c in special_qtd_cols:
            mt["_especial"] = mt["_especial"] | (pd.to_numeric(mt[c], errors="coerce").fillna(0) > 0)

        mat_cols = [c for c in ["QT_MAT_BAS", "QT_MAT_INF", "QT_MAT_FUND", "QT_MAT_MED", "QT_MAT_EJA"] if c in mt.columns]
        mt["_matriculas"] = 0
        if mat_cols:
            # Usa QT_MAT_BAS se existir; senão soma etapas principais
            if "QT_MAT_BAS" in mt.columns:
                mt["_matriculas"] = pd.to_numeric(mt["QT_MAT_BAS"], errors="coerce").fillna(0)
            else:
                mt["_matriculas"] = sum(pd.to_numeric(mt[c], errors="coerce").fillna(0) for c in mat_cols)
        mt["_mat_esp"] = 0
        for c in [c for c in mt.columns if (c.startswith("QT_MAT_ESP") or c.startswith("QT_MAT_AEE"))]:
            mt["_mat_esp"] = mt["_mat_esp"] + pd.to_numeric(mt[c], errors="coerce").fillna(0)
        acc.append(mt[["codigo_ibge", "municipio", "_escola", "_urbana", "_rural", "_indigena", "_quilombola", "_especial", "_matriculas", "_mat_esp"]])
    if not acc:
        return None
    df = pd.concat(acc, ignore_index=True)
    # uma linha por escola para não duplicar quando arquivo tiver múltiplas etapas
    df = df.sort_values(["codigo_ibge", "_escola"]).drop_duplicates(["codigo_ibge", "_escola"], keep="first")
    agg = df.groupby("codigo_ibge", dropna=False).agg(
        municipio=("municipio", lambda x: next((v for v in x if str(v).strip()), "")),
        escolas_total=("_escola", "nunique"),
        escolas_urbanas=("_urbana", "sum"),
        escolas_rurais=("_rural", "sum"),
        escolas_indigenas=("_indigena", "sum"),
        escolas_quilombolas=("_quilombola", "sum"),
        escolas_educacao_especial_aee=("_especial", "sum"),
        matriculas_total=("_matriculas", "sum"),
        matriculas_educacao_especial=("_mat_esp", "sum"),
    ).reset_index()
    agg = _aplicar_municipio_canonico(agg)
    return agg


def _aggregate_matriculas_if_available(zf: zipfile.ZipFile, members: list[str], base: pd.DataFrame) -> pd.DataFrame:
    # Se a base de escolas já trouxe matrículas, não força arquivo grande.
    if "matriculas_total" in base.columns and pd.to_numeric(base["matriculas_total"], errors="coerce").fillna(0).sum() > 0:
        return base
    for member in members:
        try:
            header = _read_header(zf, member)
            cols = set(header)
            mun_col = _first_existing(cols, ["CO_MUNICIPIO", "COD_MUNICIPIO", "CO_MUNICIPIO_END"])
            if not mun_col:
                continue
            use = [c for c in ["SG_UF", "CO_UF", "NO_UF", mun_col, "IN_ESPECIAL_EXCLUSIVA", "TP_TIPO_ATENDIMENTO_TURMA", "IN_NECESSIDADE_ESPECIAL"] if c in cols]
            reader = _read_csv_member(zf, member, usecols=use, chunksize=300000)
            if isinstance(reader, pd.DataFrame):
                reader = [reader]
            rows = []
            for chunk in reader:
                chunk = _standardize_columns(chunk)
                mt = chunk.loc[_uf_filter_mask(chunk)].copy()
                if mt.empty:
                    continue
                mt["codigo_ibge"] = _as_int_series(mt[mun_col]).astype("Int64")
                mt = mt[mt["codigo_ibge"].notna()]
                mt["matriculas_total"] = 1
                mt["matriculas_educacao_especial"] = False
                for c in ["IN_ESPECIAL_EXCLUSIVA", "IN_NECESSIDADE_ESPECIAL"]:
                    if c in mt.columns:
                        mt["matriculas_educacao_especial"] = mt["matriculas_educacao_especial"] | _is_yes(mt, c)
                rows.append(mt[["codigo_ibge", "matriculas_total", "matriculas_educacao_especial"]])
            if rows:
                mat = pd.concat(rows, ignore_index=True).groupby("codigo_ibge").agg(
                    matriculas_total=("matriculas_total", "sum"),
                    matriculas_educacao_especial=("matriculas_educacao_especial", "sum"),
                ).reset_index()
                base = base.drop(columns=[c for c in ["matriculas_total", "matriculas_educacao_especial"] if c in base.columns], errors="ignore")
                base = base.merge(mat, on="codigo_ibge", how="left")
                return base
        except Exception:
            continue
    return base


def carregar_censo_escolar_inep_mt(ano: int = INEP_ANO_PADRAO) -> pd.DataFrame:
    """Carrega Censo Escolar INEP para MT.

    Retorna uma linha por município com contagens de escolas por territorialidade.
    A função é tolerante a variações de layout dos microdados do INEP.
    """
    zip_path = _get_zip_path()
    if not zipfile.is_zipfile(zip_path):
        raise RuntimeError(f"Arquivo INEP encontrado não é ZIP válido: {zip_path}")
    with zipfile.ZipFile(zip_path) as zf:
        members = _list_members(zf)
        school_members, matric_members = _classify_members(members)
        if not school_members:
            raise RuntimeError("Não encontrei arquivo de escolas/cadastro dentro do ZIP do INEP.")
        last_errors = []
        agg = None
        for member in school_members:
            try:
                agg = _aggregate_school_file(zf, member)
                if agg is not None and not agg.empty:
                    break
            except Exception as e:
                last_errors.append(f"{member}: {e}")
        if agg is None or agg.empty:
            # Diagnóstico útil: mostra alguns arquivos do ZIP e cabeçalhos prováveis
            sample = school_members[:5] or members[:10]
            raise RuntimeError(
                "Após filtrar MT, não restaram registros válidos do INEP. "
                "O parser não encontrou colunas esperadas de UF/município/escola. "
                f"Arquivos candidatos: {sample}. Últimos erros: {' | '.join(last_errors[-3:])}"
            )
        agg = _aggregate_matriculas_if_available(zf, matric_members, agg)

    for c in [
        "escolas_total", "escolas_urbanas", "escolas_rurais", "escolas_indigenas",
        "escolas_quilombolas", "escolas_educacao_especial_aee", "matriculas_total", "matriculas_educacao_especial",
    ]:
        if c not in agg.columns:
            agg[c] = 0
        agg[c] = pd.to_numeric(agg[c], errors="coerce").fillna(0).astype(int)
    agg["codigo_ibge"] = pd.to_numeric(agg["codigo_ibge"], errors="coerce").astype("Int64")
    agg = agg[agg["codigo_ibge"].astype(str).str.startswith("51")].copy()
    if agg.empty:
        raise RuntimeError("Após agregação, não restaram municípios de Mato Grosso no Censo Escolar.")
    agg = _aplicar_municipio_canonico(agg)
    # Compatibilidade defensiva com versões anteriores do parser.
    if "escolas_educacao_especial" in agg.columns and "escolas_educacao_especial_aee" not in agg.columns:
        agg["escolas_educacao_especial_aee"] = agg["escolas_educacao_especial"]
    agg["ano"] = int(ano)
    agg["competencia"] = str(ano)
    agg["fonte"] = "INEP_CENSO_ESCOLAR"
    agg["arquivo_origem"] = str(zip_path)
    if "municipio" in agg.columns:
        agg["municipio"] = agg["municipio"].astype(str).str.strip()
    return agg.sort_values("codigo_ibge").reset_index(drop=True)

# aliases para compatibilidade com o catálogo/registry antigo
carregar_inep_censo_escolar_mt = carregar_censo_escolar_inep_mt
carregar_censo_escolar_mt = carregar_censo_escolar_inep_mt
carregar_inep_educacao_mt = carregar_censo_escolar_inep_mt
