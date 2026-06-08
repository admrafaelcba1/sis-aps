"""Conectores IBGE/SIDRA - Censo 2022, populações específicas.

v17.14
------
Correção pragmática para estabilizar a camada:
- primeiro reaproveita arquivos locais já baixados/validados em data/raw/apis;
- depois tenta as consultas SIDRA usando a estratégia do sistema antigo;
- sempre retorna formato longo compatível com indicadores_municipais:
  codigo_ibge, municipio, ano, competencia, indicador, valor, fonte.

Motivo: a API SIDRA varia bastante o layout de resposta por tabela/categoria.
Esta versão evita falso sucesso e preserva os dados positivos já carregados quando
existirem caches CSV não zerados.
"""
from __future__ import annotations

import re
import time
import unicodedata
import warnings
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import pandas as pd
import requests

try:
    from config.settings import DATA_DIR, ROOT_DIR  # type: ignore
except Exception:  # pragma: no cover
    ROOT_DIR = Path(__file__).resolve().parents[1]
    DATA_DIR = ROOT_DIR / "data"

try:
    from config.ibge_estimativas_2025_mt import ESTIMATIVAS_POPULACAO_2025_MT  # type: ignore
except Exception:  # pragma: no cover
    ESTIMATIVAS_POPULACAO_2025_MT = []

UF_MT = "51"
TIMEOUT = 120
SIDRA_BASE = "https://apisidra.ibge.gov.br/values"

POVOS_METRICAS = [
    "pessoas_indigenas_2022",
    "pessoas_quilombolas_2022",
    "pessoas_tradicionais_total_2022",
]
DEF_METRICAS = [
    "pessoas_com_deficiencia_2022",
    "pessoas_diagnosticadas_autismo_2022",
    "pct_pessoas_com_deficiencia_2022",
    "pct_pessoas_diagnosticadas_autismo_2022",
]

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _somente_digitos(valor: Any) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    return re.sub(r"\D+", "", str(valor))


def _codigo_ibge_7(valor: Any) -> Optional[str]:
    s = _somente_digitos(valor)
    if not s:
        return None
    if len(s) == 7 and s.startswith("51"):
        return s
    m = re.search(r"51\d{5}", s)
    if m:
        return m.group(0)
    if len(s) >= 7 and s[-7:].startswith("51"):
        return s[-7:]
    return None


def _normalizar_texto(texto: Any) -> str:
    txt = str(texto or "").strip().lower()
    txt = "".join(c for c in unicodedata.normalize("NFKD", txt) if not unicodedata.combining(c))
    txt = re.sub(r"[^a-z0-9]+", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def _valor_num(valor: Any) -> Optional[float]:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    txt = str(valor).strip()
    if txt in {"", "-", "...", "X", "x", "NaN", "nan"}:
        return None
    txt = txt.replace(".", "").replace(",", ".")
    txt = re.sub(r"[^0-9\.\-]", "", txt)
    if txt in {"", ".", "-"}:
        return None
    try:
        return float(txt)
    except Exception:
        return None


def _base_municipios() -> pd.DataFrame:
    registros: List[Dict[str, Any]] = []
    for item in ESTIMATIVAS_POPULACAO_2025_MT or []:
        if not isinstance(item, dict):
            continue
        codigo = _codigo_ibge_7(item.get("codigo_ibge") or item.get("codigo") or item.get("cod_ibge"))
        nome = item.get("municipio") or item.get("nome")
        pop = item.get("populacao_2025") or item.get("populacao")
        if codigo and nome:
            registros.append({"codigo_ibge": codigo, "municipio": str(nome), "populacao": pop})

    if not registros:
        # Último recurso: banco local, caso exista.
        try:
            import sqlite3
            db = Path(DATA_DIR) / "aps_inteligencia.db"
            if db.exists():
                con = sqlite3.connect(db)
                for tabela in ["base_municipal_consolidada", "municipios"]:
                    try:
                        df = pd.read_sql_query(f"SELECT codigo_ibge, municipio FROM {tabela}", con)
                        for _, r in df.dropna().drop_duplicates().iterrows():
                            codigo = _codigo_ibge_7(r.get("codigo_ibge"))
                            nome = r.get("municipio")
                            if codigo and nome:
                                registros.append({"codigo_ibge": codigo, "municipio": str(nome)})
                        if registros:
                            break
                    except Exception:
                        pass
                con.close()
        except Exception:
            pass

    if not registros:
        url = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/51/municipios"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            resp = requests.get(url, timeout=TIMEOUT, verify=False)
        resp.raise_for_status()
        for item in resp.json():
            codigo = _codigo_ibge_7(item.get("id"))
            nome = item.get("nome")
            if codigo and nome:
                registros.append({"codigo_ibge": codigo, "municipio": str(nome)})

    df = pd.DataFrame(registros)
    if df.empty:
        raise RuntimeError("Não foi possível montar a lista canônica de municípios de Mato Grosso.")
    df["codigo_ibge"] = df["codigo_ibge"].map(_codigo_ibge_7)
    df = df.dropna(subset=["codigo_ibge"]).drop_duplicates("codigo_ibge")
    df = df[df["codigo_ibge"].astype(str).str.startswith("51")].copy()
    if "municipio" not in df.columns:
        raise RuntimeError("Base canônica sem coluna municipio.")
    return df[["codigo_ibge", "municipio"] + (["populacao"] if "populacao" in df.columns else [])]


def _wide_para_longo(wide: pd.DataFrame, metricas: List[str], fonte: str, ano: int = 2022) -> pd.DataFrame:
    base = _base_municipios()[["codigo_ibge", "municipio"]].copy()
    df = wide.copy() if wide is not None else pd.DataFrame()

    if df.empty:
        df = base.copy()
    if "codigo_ibge" not in df.columns:
        raise RuntimeError("Base sem coluna codigo_ibge.")
    df["codigo_ibge"] = df["codigo_ibge"].map(_codigo_ibge_7)
    df = df.dropna(subset=["codigo_ibge"]).copy()

    # Evita municipio_x/municipio_y: usa sempre o nome canônico.
    df = df.drop(columns=[c for c in ["municipio", "municipio_x", "municipio_y"] if c in df.columns], errors="ignore")
    merged = base.merge(df, on="codigo_ibge", how="left")

    registros: List[pd.DataFrame] = []
    for metrica in metricas:
        if metrica not in merged.columns:
            merged[metrica] = pd.NA
        tmp = merged[["codigo_ibge", "municipio"]].copy()
        tmp["ano"] = int(ano)
        tmp["competencia"] = str(ano)
        tmp["indicador"] = metrica
        tmp["valor"] = pd.to_numeric(merged[metrica], errors="coerce")
        tmp["fonte"] = fonte
        registros.append(tmp)
    out = pd.concat(registros, ignore_index=True)
    if "municipio" not in out.columns:
        raise RuntimeError("Falha interna: base final sem coluna municipio.")
    return out


def _local_raw_dir() -> Path:
    return Path(DATA_DIR) / "raw" / "apis"


def _ler_cache_wide_positivo(prefixo: str, metricas: List[str]) -> Optional[pd.DataFrame]:
    """Lê o último CSV local que contenha valores positivos reais.

    Isso aproveita os arquivos já baixados/importados nas versões anteriores,
    evitando nova instabilidade do SIDRA quando o dado local já é positivo.
    """
    raw = _local_raw_dir()
    if not raw.exists():
        return None
    arquivos = sorted(raw.glob(f"{prefixo}_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    candidatos: List[Tuple[float, Path, pd.DataFrame]] = []
    for arq in arquivos:
        try:
            df = pd.read_csv(arq, dtype=str)
        except Exception:
            continue
        if "codigo_ibge" not in df.columns:
            continue
        total = 0.0
        for col in metricas:
            col_alt = col
            if col == "pessoas_diagnosticadas_autismo_2022" and col_alt not in df.columns and "pessoas_autismo_2022" in df.columns:
                df["pessoas_diagnosticadas_autismo_2022"] = df["pessoas_autismo_2022"]
            if col in df.columns:
                total += pd.to_numeric(df[col], errors="coerce").fillna(0).sum()
        if total > 0:
            candidatos.append((total, arq, df))
    if not candidatos:
        return None
    # Usa o arquivo com maior total positivo, não necessariamente o mais recente,
    # porque versões de diagnóstico podem ter sido salvas depois com zeros.
    candidatos.sort(key=lambda x: x[0], reverse=True)
    return candidatos[0][2]


# ---------------------------------------------------------------------------
# SIDRA: estratégia herdada do sistema antigo
# ---------------------------------------------------------------------------

def _request_json(url: str, timeout: int = TIMEOUT) -> list:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        resp = requests.get(url, timeout=timeout, verify=False)
    resp.raise_for_status()
    return resp.json()


def _sidra_municipio_codigo_nome(item: Dict[str, Any]) -> Tuple[str, str]:
    for chave, valor in item.items():
        if not str(chave).endswith("C"):
            continue
        codigo = _codigo_ibge_7(valor)
        if codigo and codigo.startswith("51"):
            base = str(chave)[:-1]
            nome = str(item.get(base + "N", "") or "").strip()
            return codigo, nome
    return "", ""


def _extrair_populacao_tradicional_de_resposta_sidra(dados: list, grupo: str, metodo: str) -> pd.DataFrame:
    grupo_norm = _normalizar_texto(grupo)
    registros: Dict[str, Dict[str, Any]] = {}
    if not dados or len(dados) < 2:
        return pd.DataFrame()

    for item in dados[1:]:
        codigo, municipio = _sidra_municipio_codigo_nome(item)
        valor = _valor_num(item.get("V"))
        if not codigo or valor is None:
            continue
        labels = " | ".join(str(v or "") for k, v in item.items() if str(k).endswith("N"))
        labels_norm = _normalizar_texto(labels)
        unidade_norm = _normalizar_texto(str(item.get("UM", "") or ""))
        if "percentual" in labels_norm or "percentual" in unidade_norm or "%" in str(item.get("V", "")):
            continue
        if grupo_norm.startswith("indig"):
            marcador_ok = "indigena" in labels_norm
        else:
            marcador_ok = "quilombola" in labels_norm
        if not marcador_ok:
            continue
        atual = registros.get(codigo, {}).get("pessoas")
        if atual is None or float(valor) > float(atual):
            registros[codigo] = {
                "codigo_ibge": codigo,
                "pessoas": int(round(float(valor))),
                "metodo_sidra": metodo,
            }
    return pd.DataFrame(list(registros.values()))


def _carregar_populacao_tradicional_sidra(grupo: str, ano: int = 2022) -> pd.DataFrame:
    filtro_territorial = quote("in n3 51")
    grupo_norm = _normalizar_texto(grupo)
    if grupo_norm.startswith("indig"):
        consultas = [
            (f"{SIDRA_BASE}/t/9718/n6/{filtro_territorial}/v/350/p/{int(ano)}/c1714/all/c2661/all?formato=json", "SIDRA 9718 explícita"),
            (f"{SIDRA_BASE}/t/9718/n6/{filtro_territorial}/v/all/p/{int(ano)}/c1714/all/c2661/all?formato=json", "SIDRA 9718 v/all"),
            (f"{SIDRA_BASE}/t/8175/n6/{filtro_territorial}/v/350/p/{int(ano)}/c287/all/c2/all/c2661/all?formato=json", "SIDRA 8175"),
        ]
    else:
        consultas = [
            (f"{SIDRA_BASE}/t/9578/n6/{filtro_territorial}/v/4709/p/{int(ano)}/c2661/all?formato=json", "SIDRA 9578 explícita"),
            (f"{SIDRA_BASE}/t/9578/n6/{filtro_territorial}/v/all/p/{int(ano)}/c2661/all?formato=json", "SIDRA 9578 v/all"),
            (f"{SIDRA_BASE}/t/8176/n6/{filtro_territorial}/v/4709/p/{int(ano)}/c287/all/c2/all/c2661/all?formato=json", "SIDRA 8176"),
        ]

    erros: List[str] = []
    for url, metodo in consultas:
        try:
            dados = _request_json(url)
            df = _extrair_populacao_tradicional_de_resposta_sidra(dados, grupo, metodo)
            total = int(df["pessoas"].sum()) if not df.empty and "pessoas" in df.columns else 0
            if total > 0:
                return df
            erros.append(f"{metodo}: sem valores positivos")
        except Exception as exc:
            erros.append(f"{metodo}: {exc}")
            time.sleep(0.4)
    raise RuntimeError(" | ".join(erros[:6]))


def _extrair_sidra_municipio_valor(item: Dict[str, Any]) -> Tuple[str, str, Optional[float], str]:
    codigo, municipio = _sidra_municipio_codigo_nome(item)
    valor = _valor_num(item.get("V"))
    desc = " | ".join(str(v or "") for k, v in item.items() if str(k).endswith("N"))
    return codigo, municipio, valor, desc


def _carregar_sidra_populacao_alvo_total(
    tabela: int,
    ano: int,
    palavras_alvo: List[str],
    palavras_excluir_alvo: Optional[List[str]] = None,
) -> pd.DataFrame:
    filtro_territorial = quote("in n3 51")
    url = f"{SIDRA_BASE}/t/{int(tabela)}/n6/{filtro_territorial}/v/all/p/{int(ano)}?formato=json"
    alvo_norm = [_normalizar_texto(x) for x in palavras_alvo]
    excluir_norm = [_normalizar_texto(x) for x in (palavras_excluir_alvo or [])]
    registros: List[Dict[str, Any]] = []
    dados = _request_json(url)
    for item in (dados or [])[1:]:
        codigo, municipio, valor, desc = _extrair_sidra_municipio_valor(item)
        if not codigo or not str(codigo).startswith(UF_MT) or valor is None:
            continue
        desc_norm = _normalizar_texto(desc)
        eh_alvo = any(p in desc_norm for p in alvo_norm) and not any(p in desc_norm for p in excluir_norm)
        eh_total = "total" in desc_norm and not any(p in desc_norm for p in alvo_norm) and not any(x in desc_norm for x in ["coeficiente", "cv", "%"])
        if eh_alvo or eh_total:
            registros.append({
                "codigo_ibge": str(codigo),
                "valor": float(valor),
                "tipo": "alvo" if eh_alvo else "total",
                "descricao_sidra": desc_norm[:400],
            })
    if not registros:
        return pd.DataFrame(columns=["codigo_ibge", "valor_alvo", "valor_total", "metodo"])
    df = pd.DataFrame(registros)
    alvo = df[df["tipo"] == "alvo"].sort_values("valor", ascending=False).drop_duplicates("codigo_ibge")
    total = df[df["tipo"] == "total"].sort_values("valor", ascending=False).drop_duplicates("codigo_ibge")
    out = alvo[["codigo_ibge", "valor", "descricao_sidra"]].rename(columns={"valor": "valor_alvo", "descricao_sidra": "metodo_alvo"})
    if not total.empty:
        out = out.merge(total[["codigo_ibge", "valor", "descricao_sidra"]].rename(columns={"valor": "valor_total", "descricao_sidra": "metodo_total"}), on="codigo_ibge", how="left")
    else:
        out["valor_total"] = pd.NA
        out["metodo_total"] = pd.NA
    out["metodo"] = out.apply(lambda r: f"alvo: {r.get('metodo_alvo')} | total: {r.get('metodo_total')}", axis=1)
    return out


# ---------------------------------------------------------------------------
# Conectores públicos esperados pelo catálogo
# ---------------------------------------------------------------------------

def carregar_povos_tradicionais_censo2022_mt(ano_censo: int = 2022, **kwargs: Any) -> pd.DataFrame:
    # 1) Reaproveita cache positivo local, se houver.
    cached = _ler_cache_wide_positivo("ibge_censo2022_povos_tradicionais", POVOS_METRICAS)
    if cached is not None:
        return _wide_para_longo(cached, POVOS_METRICAS, "IBGE/SIDRA Censo 2022 - cache local validado", ano_censo)

    # 2) Consulta SIDRA pela estratégia do sistema antigo.
    base = _base_municipios()[["codigo_ibge", "municipio"]].copy()
    erros: List[str] = []
    try:
        ind = _carregar_populacao_tradicional_sidra("indigena", ano_censo)
        base = base.merge(ind[["codigo_ibge", "pessoas"]].rename(columns={"pessoas": "pessoas_indigenas_2022"}), on="codigo_ibge", how="left")
    except Exception as exc:
        base["pessoas_indigenas_2022"] = pd.NA
        erros.append(f"indígenas: {exc}")
    try:
        qui = _carregar_populacao_tradicional_sidra("quilombola", ano_censo)
        base = base.merge(qui[["codigo_ibge", "pessoas"]].rename(columns={"pessoas": "pessoas_quilombolas_2022"}), on="codigo_ibge", how="left")
    except Exception as exc:
        base["pessoas_quilombolas_2022"] = pd.NA
        erros.append(f"quilombolas: {exc}")

    for col in ["pessoas_indigenas_2022", "pessoas_quilombolas_2022"]:
        base[col] = pd.to_numeric(base[col], errors="coerce")
    base["pessoas_tradicionais_total_2022"] = base[["pessoas_indigenas_2022", "pessoas_quilombolas_2022"]].fillna(0).sum(axis=1)
    if float(base["pessoas_tradicionais_total_2022"].fillna(0).sum()) <= 0:
        raise RuntimeError("Censo 2022 povos tradicionais retornou sem valores positivos. " + " | ".join(erros[:6]))
    return _wide_para_longo(base, POVOS_METRICAS, "IBGE/SIDRA Censo 2022 - povos tradicionais", ano_censo)


def carregar_deficiencia_autismo_censo2022_mt(ano_censo: int = 2022, **kwargs: Any) -> pd.DataFrame:
    # 1) Reaproveita cache positivo local, se houver.
    cached = _ler_cache_wide_positivo("ibge_censo2022_deficiencia_autismo", DEF_METRICAS)
    if cached is not None:
        if "pessoas_diagnosticadas_autismo_2022" not in cached.columns and "pessoas_autismo_2022" in cached.columns:
            cached = cached.copy()
            cached["pessoas_diagnosticadas_autismo_2022"] = cached["pessoas_autismo_2022"]
        return _wide_para_longo(cached, DEF_METRICAS, "IBGE/SIDRA Censo 2022 - cache local validado", ano_censo)

    # 2) Consulta SIDRA pela estratégia do sistema antigo.
    base = _base_municipios()[["codigo_ibge", "municipio"]].copy()
    erros: List[str] = []

    try:
        df_def = _carregar_sidra_populacao_alvo_total(
            tabela=10125,
            ano=ano_censo,
            palavras_alvo=["pessoas com deficiência", "com deficiência", "existencia de deficiencia", "existência de deficiência"],
            palavras_excluir_alvo=["sem deficiência", "sem deficiencia", "não tinha deficiência", "nao tinha deficiencia"],
        )
        if df_def.empty:
            raise RuntimeError("Tabela 10125 sem registros-alvo interpretáveis.")
        df_def = df_def.rename(columns={"valor_alvo": "pessoas_com_deficiencia_2022", "valor_total": "populacao_referencia_deficiencia_2022"})
        base = base.merge(df_def[["codigo_ibge", "pessoas_com_deficiencia_2022", "populacao_referencia_deficiencia_2022"]], on="codigo_ibge", how="left")
    except Exception as exc:
        base["pessoas_com_deficiencia_2022"] = pd.NA
        base["populacao_referencia_deficiencia_2022"] = pd.NA
        erros.append(f"deficiência: {exc}")

    try:
        df_tea = _carregar_sidra_populacao_alvo_total(
            tabela=10145,
            ano=ano_censo,
            palavras_alvo=["diagnosticada com autismo", "diagnosticado com autismo", "transtorno do espectro autista", "autismo"],
            palavras_excluir_alvo=["sem diagnóstico", "sem diagnostico", "não diagnosticada", "nao diagnosticada"],
        )
        if df_tea.empty:
            raise RuntimeError("Tabela 10145 sem registros-alvo interpretáveis.")
        df_tea = df_tea.rename(columns={"valor_alvo": "pessoas_diagnosticadas_autismo_2022", "valor_total": "populacao_referencia_autismo_2022"})
        base = base.merge(df_tea[["codigo_ibge", "pessoas_diagnosticadas_autismo_2022", "populacao_referencia_autismo_2022"]], on="codigo_ibge", how="left")
    except Exception as exc:
        base["pessoas_diagnosticadas_autismo_2022"] = pd.NA
        base["populacao_referencia_autismo_2022"] = pd.NA
        erros.append(f"autismo: {exc}")

    base["pessoas_com_deficiencia_2022"] = pd.to_numeric(base["pessoas_com_deficiencia_2022"], errors="coerce")
    base["pessoas_diagnosticadas_autismo_2022"] = pd.to_numeric(base["pessoas_diagnosticadas_autismo_2022"], errors="coerce")
    base["populacao_referencia_deficiencia_2022"] = pd.to_numeric(base.get("populacao_referencia_deficiencia_2022"), errors="coerce")
    base["populacao_referencia_autismo_2022"] = pd.to_numeric(base.get("populacao_referencia_autismo_2022"), errors="coerce")
    base["pct_pessoas_com_deficiencia_2022"] = (base["pessoas_com_deficiencia_2022"] / base["populacao_referencia_deficiencia_2022"] * 100).round(2)
    base["pct_pessoas_diagnosticadas_autismo_2022"] = (base["pessoas_diagnosticadas_autismo_2022"] / base["populacao_referencia_autismo_2022"] * 100).round(2)

    total = base[["pessoas_com_deficiencia_2022", "pessoas_diagnosticadas_autismo_2022"]].fillna(0).sum().sum()
    if float(total) <= 0:
        raise RuntimeError("Censo 2022 deficiência/autismo retornou sem valores positivos. " + " | ".join(erros[:6]))
    return _wide_para_longo(base, DEF_METRICAS, "IBGE/SIDRA Censo 2022 - deficiência e autismo", ano_censo)


# Aliases de compatibilidade.
carregar_povos_tradicionais_censo2022 = carregar_povos_tradicionais_censo2022_mt
carregar_deficiencia_autismo_censo2022 = carregar_deficiencia_autismo_censo2022_mt
