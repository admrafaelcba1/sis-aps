
from __future__ import annotations

from pathlib import Path
from datetime import datetime
import io
import re
import zipfile

import pandas as pd

from database.connection import get_connection
from database.queries import read_table


ARQUIVOS_IBGE_SETOR = [
    {
        "ordem": 1,
        "match": ["alfabetizacao"],
        "conjunto": "Alfabetização",
        "tabela": "base_publica_ibge_setores_alfabetizacao",
        "categoria": "Escolaridade e educação",
    },
    {
        "ordem": 2,
        "match": ["basico"],
        "conjunto": "Agregados por setores — básico",
        "tabela": "base_publica_ibge_setores_basico",
        "categoria": "Base territorial e demográfica",
    },
    {
        "ordem": 3,
        "match": ["domicilio1", "domicilios1", "caracteristicas_domicilio1"],
        "conjunto": "Características dos domicílios 1",
        "tabela": "base_publica_ibge_setores_domicilios_1",
        "categoria": "Saneamento, domicílios e entorno",
    },
    {
        "ordem": 4,
        "match": ["domicilio2", "domicilios2", "caracteristicas_domicilio2"],
        "conjunto": "Características dos domicílios 2",
        "tabela": "base_publica_ibge_setores_domicilios_2",
        "categoria": "Saneamento, domicílios e entorno",
    },
    {
        "ordem": 5,
        "match": ["domicilio3", "domicilios3", "caracteristicas_domicilio3"],
        "conjunto": "Características dos domicílios 3",
        "tabela": "base_publica_ibge_setores_domicilios_3",
        "categoria": "Saneamento, domicílios e entorno",
    },
    {
        "ordem": 6,
        "match": ["cor_ou_raca", "cor_raca"],
        "conjunto": "Cor ou raça",
        "tabela": "base_publica_ibge_setores_cor_raca",
        "categoria": "Demografia e equidade",
    },
    {
        "ordem": 7,
        "match": ["demografia"],
        "conjunto": "Demografia",
        "tabela": "base_publica_ibge_setores_demografia",
        "categoria": "Demografia e equidade",
    },
    {
        "ordem": 8,
        "match": ["domicilios_indigenas", "domicilio_indigena"],
        "conjunto": "Domicílios indígenas",
        "tabela": "base_publica_ibge_setores_domicilios_indigenas",
        "categoria": "Populações específicas",
    },
    {
        "ordem": 9,
        "match": ["domicilios_quilombolas", "domicilio_quilombola"],
        "conjunto": "Domicílios quilombolas",
        "tabela": "base_publica_ibge_setores_domicilios_quilombolas",
        "categoria": "Populações específicas",
    },
    {
        "ordem": 10,
        "match": ["obitos"],
        "conjunto": "Óbitos",
        "tabela": "base_publica_ibge_setores_obitos",
        "categoria": "Epidemiologia — mortalidade",
    },
    {
        "ordem": 11,
        "match": ["parentesco"],
        "conjunto": "Parentesco",
        "tabela": "base_publica_ibge_setores_parentesco",
        "categoria": "Demografia e composição domiciliar",
    },
    {
        "ordem": 12,
        "match": ["pessoas_indigenas"],
        "conjunto": "Pessoas indígenas",
        "tabela": "base_publica_ibge_setores_pessoas_indigenas",
        "categoria": "Populações específicas",
    },
    {
        "ordem": 13,
        "match": ["pessoas_quilombolas"],
        "conjunto": "Pessoas quilombolas",
        "tabela": "base_publica_ibge_setores_pessoas_quilombolas",
        "categoria": "Populações específicas",
    },
]


def _norm(s: str) -> str:
    s = str(s).lower()
    s = s.replace("ç", "c").replace("ã", "a").replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_")


def identificar_conjunto_ibge(nome_arquivo: str) -> dict:
    n = _norm(nome_arquivo)
    for item in ARQUIVOS_IBGE_SETOR:
        for token in item["match"]:
            if token in n:
                return item
    return {
        "ordem": 99,
        "conjunto": "IBGE Censo 2022 setores — não classificado",
        "tabela": "base_publica_ibge_setores_nao_classificado",
        "categoria": "Outros indicadores públicos",
    }


def _mapa_municipios() -> pd.DataFrame:
    municipios = read_table("municipios")
    if municipios.empty:
        municipios = read_table("malhas_geograficas_municipais")
    if municipios.empty:
        return pd.DataFrame(columns=["codigo_ibge", "municipio", "regiao_saude"])
    cols = [c for c in ["codigo_ibge", "municipio", "regiao_saude"] if c in municipios.columns]
    out = municipios[cols].drop_duplicates().copy()
    if "codigo_ibge" in out.columns:
        out["codigo_ibge"] = out["codigo_ibge"].astype(str).str.replace(r"\D", "", regex=True).str[:7]
    return out


def _achar_coluna_setor(cols: list[str]) -> str | None:
    candidatos = ["CD_SETOR", "CD_setor", "cd_setor", "setor", "SETOR", "Cod_setor", "cod_setor"]
    for c in candidatos:
        if c in cols:
            return c
    for c in cols:
        cn = _norm(c)
        if cn in ["cd_setor", "setor"]:
            return c
    return None


def _normalizar_chunk_ibge(df: pd.DataFrame, conjunto: dict, mapa: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    # Normaliza coluna de setor, preservando variáveis Vxxxxx.
    setor_col = _achar_coluna_setor(list(out.columns))
    if setor_col and setor_col != "CD_SETOR":
        out = out.rename(columns={setor_col: "CD_SETOR"})
    if "CD_SETOR" not in out.columns:
        return pd.DataFrame()

    out["CD_SETOR"] = out["CD_SETOR"].astype(str).str.replace(r"\D", "", regex=True)
    out = out[out["CD_SETOR"].str.startswith("51", na=False)].copy()
    if out.empty:
        return out

    if "CD_MUN" not in out.columns:
        out["CD_MUN"] = out["CD_SETOR"].str[:7]
    else:
        out["CD_MUN"] = out["CD_MUN"].astype(str).str.replace(r"\D", "", regex=True).str[:7]

    out["codigo_ibge"] = out["CD_MUN"]

    if not mapa.empty and "codigo_ibge" in mapa.columns:
        out = out.merge(mapa, on="codigo_ibge", how="left", suffixes=("", "_mapa"))
    else:
        out["municipio"] = out.get("NM_MUN", "")
        out["regiao_saude"] = ""

    if "municipio" not in out.columns:
        if "NM_MUN" in out.columns:
            out["municipio"] = out["NM_MUN"]
        elif "NM_MUN_mapa" in out.columns:
            out["municipio"] = out["NM_MUN_mapa"]
        else:
            out["municipio"] = ""

    if "regiao_saude" not in out.columns:
        out["regiao_saude"] = ""

    # Se vier NM_MUN no básico e merge criou municipio vazio, usa NM_MUN.
    if "NM_MUN" in out.columns:
        out["municipio"] = out["municipio"].fillna(out["NM_MUN"])
        out.loc[out["municipio"].astype(str).str.strip().eq(""), "municipio"] = out["NM_MUN"]

    out["eixo"] = "IBGE"
    out["tipo_base"] = f"IBGE Censo 2022 setores — {conjunto['conjunto']}"
    out["categoria_principal"] = conjunto["categoria"]
    out["fonte"] = "IBGE Censo 2022 — Agregados por Setores Censitários"
    out["ano_referencia"] = "2022"
    out["data_importacao"] = datetime.now().isoformat(timespec="seconds")
    out["tabela_destino"] = conjunto["tabela"]

    # X = supressão/desidentificação estatística; mantém como nulo para cálculo numérico.
    out = out.replace({"X": pd.NA, "x": pd.NA})
    return out


def _csvs_em_zip_bytes(zip_bytes: bytes, prefixo: str = "") -> list[tuple[str, bytes]]:
    encontrados: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            nome = f"{prefixo}{info.filename}"
            low = info.filename.lower()
            data = z.read(info.filename)
            if low.endswith(".csv"):
                encontrados.append((nome, data))
            elif low.endswith(".zip"):
                encontrados.extend(_csvs_em_zip_bytes(data, prefixo=f"{nome}::"))
    return encontrados


def diagnosticar_pacote_ibge_setores(caminho_zip: str | Path) -> pd.DataFrame:
    caminho_zip = Path(caminho_zip)
    with open(caminho_zip, "rb") as f:
        zip_bytes = f.read()
    csvs = _csvs_em_zip_bytes(zip_bytes)
    linhas = []
    for nome, data in csvs:
        conjunto = identificar_conjunto_ibge(nome)
        try:
            # Lê só cabeçalho e primeira linha
            amostra = pd.read_csv(io.BytesIO(data), sep=";", dtype=str, encoding="latin1", nrows=5)
            setor_col = _achar_coluna_setor(list(amostra.columns))
            primeiro_setor = ""
            if setor_col:
                primeiro_setor = str(amostra[setor_col].dropna().iloc[0]) if not amostra[setor_col].dropna().empty else ""
            linhas.append({
                "arquivo_csv": nome,
                "conjunto_identificado": conjunto["conjunto"],
                "tabela_destino": conjunto["tabela"],
                "categoria": conjunto["categoria"],
                "colunas": len(amostra.columns),
                "coluna_setor": setor_col or "",
                "primeiro_setor": primeiro_setor,
                "cd_mun_derivado": re.sub(r"\D", "", primeiro_setor)[:7] if primeiro_setor else "",
                "parece_conter_mt": "Sim, via filtro por CD_SETOR/CD_MUN" if setor_col else "Não identificado",
            })
        except Exception as e:
            linhas.append({
                "arquivo_csv": nome,
                "conjunto_identificado": conjunto["conjunto"],
                "tabela_destino": conjunto["tabela"],
                "categoria": conjunto["categoria"],
                "colunas": 0,
                "coluna_setor": "",
                "primeiro_setor": "",
                "cd_mun_derivado": "",
                "parece_conter_mt": f"Erro leitura: {e}",
            })
    return pd.DataFrame(linhas).sort_values(["tabela_destino", "arquivo_csv"]).reset_index(drop=True)


def importar_pacote_ibge_setores_mt(caminho_zip: str | Path, chunksize: int = 50000) -> dict:
    caminho_zip = Path(caminho_zip)
    mapa = _mapa_municipios()

    with open(caminho_zip, "rb") as f:
        zip_bytes = f.read()
    csvs = _csvs_em_zip_bytes(zip_bytes)
    if not csvs:
        return {"ok": False, "mensagem": "Nenhum CSV encontrado no pacote ZIP.", "resultados": pd.DataFrame()}

    resultados = []
    manifestos = []

    with get_connection() as con:
        for nome, data in csvs:
            conjunto = identificar_conjunto_ibge(nome)
            tabela = conjunto["tabela"]
            total_mt = 0
            colunas = 0
            primeiro_chunk = True

            try:
                buffer = io.BytesIO(data)
                reader = pd.read_csv(buffer, sep=";", dtype=str, encoding="latin1", chunksize=chunksize)
                for chunk in reader:
                    tratado = _normalizar_chunk_ibge(chunk, conjunto, mapa)
                    if tratado.empty:
                        continue
                    colunas = len(tratado.columns)
                    total_mt += len(tratado)
                    tratado.to_sql(tabela, con, if_exists="replace" if primeiro_chunk else "append", index=False)
                    primeiro_chunk = False

                status = "Importado" if total_mt > 0 else "Sem linhas MT identificadas"
                resultados.append({
                    "arquivo": nome,
                    "conjunto": conjunto["conjunto"],
                    "tabela": tabela,
                    "categoria": conjunto["categoria"],
                    "linhas_mt": int(total_mt),
                    "colunas": int(colunas),
                    "status": status,
                })
                manifestos.append({
                    "arquivo": nome,
                    "conjunto": conjunto["conjunto"],
                    "tabela": tabela,
                    "categoria": conjunto["categoria"],
                    "linhas_mt": int(total_mt),
                    "colunas": int(colunas),
                    "data_importacao": datetime.now().isoformat(timespec="seconds"),
                    "observacao": "Importação automática do pacote IBGE Censo 2022 setores, filtrando Mato Grosso por CD_SETOR/CD_MUN iniciando com 51.",
                })
            except Exception as e:
                resultados.append({
                    "arquivo": nome,
                    "conjunto": conjunto["conjunto"],
                    "tabela": tabela,
                    "categoria": conjunto["categoria"],
                    "linhas_mt": 0,
                    "colunas": 0,
                    "status": f"Erro: {e}",
                })

        pd.DataFrame(manifestos).to_sql("ibge_setores_importacao_manifesto", con, if_exists="replace", index=False)

    res = pd.DataFrame(resultados)
    return {
        "ok": bool(not res.empty and (res["linhas_mt"] > 0).any()),
        "mensagem": "Pacote IBGE processado.",
        "arquivos_processados": int(len(res)),
        "linhas_mt_total": int(res["linhas_mt"].sum()) if not res.empty and "linhas_mt" in res.columns else 0,
        "resultados": res,
    }


def _csvs_em_diretorio(caminho_dir: str | Path) -> list[tuple[str, bytes]]:
    """Localiza CSVs em uma pasta local e também CSVs dentro de ZIPs existentes nela."""
    base = Path(caminho_dir)
    encontrados: list[tuple[str, bytes]] = []
    if not base.exists() or not base.is_dir():
        return encontrados

    # Primeiro CSVs extraídos.
    for csv_path in base.rglob("*.csv"):
        try:
            encontrados.append((str(csv_path), csv_path.read_bytes()))
        except Exception:
            pass

    # Depois ZIPs dentro da pasta.
    for zip_path in base.rglob("*.zip"):
        try:
            encontrados.extend(_csvs_em_zip_bytes(zip_path.read_bytes(), prefixo=f"{zip_path.name}::"))
        except Exception:
            pass

    # Remove duplicidades simples por nome+tamanho.
    vistos = set()
    unicos = []
    for nome, data in encontrados:
        chave = (Path(nome.split("::")[-1]).name.lower(), len(data))
        if chave in vistos:
            continue
        vistos.add(chave)
        unicos.append((nome, data))
    return unicos


def diagnosticar_pacote_ibge_setores_local(caminho: str | Path) -> pd.DataFrame:
    caminho = Path(str(caminho).strip().strip('"'))
    if caminho.is_dir():
        csvs = _csvs_em_diretorio(caminho)
    elif caminho.is_file() and caminho.suffix.lower() == ".zip":
        csvs = _csvs_em_zip_bytes(caminho.read_bytes())
    elif caminho.is_file() and caminho.suffix.lower() == ".csv":
        csvs = [(str(caminho), caminho.read_bytes())]
    else:
        return pd.DataFrame([{
            "arquivo_csv": str(caminho),
            "conjunto_identificado": "",
            "tabela_destino": "",
            "categoria": "",
            "colunas": 0,
            "coluna_setor": "",
            "primeiro_setor": "",
            "cd_mun_derivado": "",
            "parece_conter_mt": "Caminho não encontrado ou formato não suportado.",
        }])

    linhas = []
    for nome, data in csvs:
        conjunto = identificar_conjunto_ibge(nome)
        try:
            amostra = pd.read_csv(io.BytesIO(data), sep=";", dtype=str, encoding="latin1", nrows=5)
            if amostra.shape[1] == 1:
                amostra = pd.read_csv(io.BytesIO(data), sep=",", dtype=str, encoding="latin1", nrows=5)
            setor_col = _achar_coluna_setor(list(amostra.columns))
            primeiro_setor = ""
            if setor_col and not amostra[setor_col].dropna().empty:
                primeiro_setor = str(amostra[setor_col].dropna().iloc[0])
            linhas.append({
                "arquivo_csv": nome,
                "conjunto_identificado": conjunto["conjunto"],
                "tabela_destino": conjunto["tabela"],
                "categoria": conjunto["categoria"],
                "colunas": len(amostra.columns),
                "coluna_setor": setor_col or "",
                "primeiro_setor": primeiro_setor,
                "cd_mun_derivado": re.sub(r"\D", "", primeiro_setor)[:7] if primeiro_setor else "",
                "parece_conter_mt": "Sim, será filtrado por CD_SETOR/CD_MUN iniciando com 51" if setor_col else "Coluna de setor não identificada",
            })
        except Exception as e:
            linhas.append({
                "arquivo_csv": nome,
                "conjunto_identificado": conjunto["conjunto"],
                "tabela_destino": conjunto["tabela"],
                "categoria": conjunto["categoria"],
                "colunas": 0,
                "coluna_setor": "",
                "primeiro_setor": "",
                "cd_mun_derivado": "",
                "parece_conter_mt": f"Erro leitura: {e}",
            })
    return pd.DataFrame(linhas).sort_values(["tabela_destino", "arquivo_csv"]).reset_index(drop=True)


def importar_pacote_ibge_setores_mt_local(caminho: str | Path, chunksize: int = 50000) -> dict:
    caminho = Path(str(caminho).strip().strip('"'))
    if caminho.is_dir():
        csvs = _csvs_em_diretorio(caminho)
    elif caminho.is_file() and caminho.suffix.lower() == ".zip":
        csvs = _csvs_em_zip_bytes(caminho.read_bytes())
    elif caminho.is_file() and caminho.suffix.lower() == ".csv":
        csvs = [(str(caminho), caminho.read_bytes())]
    else:
        return {"ok": False, "mensagem": "Caminho não encontrado ou formato não suportado.", "resultados": pd.DataFrame()}

    if not csvs:
        return {"ok": False, "mensagem": "Nenhum CSV encontrado no caminho informado.", "resultados": pd.DataFrame()}

    mapa = _mapa_municipios()
    resultados = []
    manifestos = []

    with get_connection() as con:
        for nome, data in csvs:
            conjunto = identificar_conjunto_ibge(nome)
            tabela = conjunto["tabela"]
            total_mt = 0
            colunas = 0
            primeiro_chunk = True
            try:
                # tenta ; primeiro, pois é padrão do IBGE
                try:
                    reader = pd.read_csv(io.BytesIO(data), sep=";", dtype=str, encoding="latin1", chunksize=chunksize)
                    first = next(reader)
                    chunks_iter = [first]
                    chunks_iter.extend(reader)
                except Exception:
                    reader = pd.read_csv(io.BytesIO(data), sep=",", dtype=str, encoding="latin1", chunksize=chunksize)
                    first = next(reader)
                    chunks_iter = [first]
                    chunks_iter.extend(reader)

                for chunk in chunks_iter:
                    tratado = _normalizar_chunk_ibge(chunk, conjunto, mapa)
                    if tratado.empty:
                        continue
                    colunas = len(tratado.columns)
                    total_mt += len(tratado)
                    tratado.to_sql(tabela, con, if_exists="replace" if primeiro_chunk else "append", index=False)
                    primeiro_chunk = False

                status = "Importado" if total_mt > 0 else "Sem linhas MT identificadas"
                resultados.append({
                    "arquivo": nome,
                    "conjunto": conjunto["conjunto"],
                    "tabela": tabela,
                    "categoria": conjunto["categoria"],
                    "linhas_mt": int(total_mt),
                    "colunas": int(colunas),
                    "status": status,
                })
                manifestos.append({
                    "arquivo": nome,
                    "conjunto": conjunto["conjunto"],
                    "tabela": tabela,
                    "categoria": conjunto["categoria"],
                    "linhas_mt": int(total_mt),
                    "colunas": int(colunas),
                    "data_importacao": datetime.now().isoformat(timespec="seconds"),
                    "observacao": f"Importação local de {caminho}, filtrando Mato Grosso por CD_SETOR/CD_MUN iniciando com 51.",
                })
            except Exception as e:
                resultados.append({
                    "arquivo": nome,
                    "conjunto": conjunto["conjunto"],
                    "tabela": tabela,
                    "categoria": conjunto["categoria"],
                    "linhas_mt": 0,
                    "colunas": 0,
                    "status": f"Erro: {e}",
                })

        pd.DataFrame(manifestos).to_sql("ibge_setores_importacao_manifesto", con, if_exists="replace", index=False)

    res = pd.DataFrame(resultados)
    return {
        "ok": bool(not res.empty and (res["linhas_mt"] > 0).any()),
        "mensagem": "Importação local IBGE processada.",
        "arquivos_processados": int(len(res)),
        "linhas_mt_total": int(res["linhas_mt"].sum()) if not res.empty and "linhas_mt" in res.columns else 0,
        "resultados": res,
    }
