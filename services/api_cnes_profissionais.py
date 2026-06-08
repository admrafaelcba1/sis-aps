"""Conectores CNES para equipes APS/INE.

v08.3: corrige a leitura do arquivo oficial EQUIPESBRASIL em largura fixa.
- tenta primeiro o caminho oficial do CNES/DATASUS presente no legado;
- depois tenta o caminho CKAN/S3 antigo;
- por fim procura arquivo local/manual em data/uploads, data/raw ou legacy.

Observação: os links S3 do OpenDataSUS/CNES podem retornar 403/404. Nesses casos,
o caminho mais estável é baixar no portal oficial do CNES o pacote "EQUIPES BRASIL"
e colocá-lo em data/uploads/cnes_equipes/ ou enviar pela tela de upload.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from config.settings import ROOT_DIR, DATA_DIR, UPLOADS_DIR, RAW_DIR
try:
    from config.municipios_mt import DEFAULT_MUNICIPIOS
except Exception:
    DEFAULT_MUNICIPIOS = []
try:
    from config.ibge_estimativas_2025_mt import ESTIMATIVAS_POPULACAO_2025_MT
except Exception:
    ESTIMATIVAS_POPULACAO_2025_MT = []

LEGACY_GEO_PATH = ROOT_DIR / "legacy" / "georreferenciamento_aps_antigo.py"

TIPOS_EQUIPE = {
    "70": "eSF / Equipe de Saúde da Família",
    "71": "eSB / Equipe de Saúde Bucal",
    "72": "eMulti / Equipe Multiprofissional",
    "73": "eCR / Consultório na Rua",
    "74": "eAPP / Equipe de Atenção Primária Prisional",
    "76": "eAP / Equipe de Atenção Primária",
}

UF_MT = "51"


def _mapa_municipios_por_cod6() -> tuple[dict[str, str], dict[str, str]]:
    """Mapeia código IBGE de 6 dígitos para código completo de 7 dígitos e nome oficial.

    Os arquivos oficiais EQUIPESBRASIL usam o município com 6 dígitos no início
    da linha. A base consolidada do sistema usa o código IBGE completo de 7 dígitos.
    """
    mapa_codigo: dict[str, str] = {}
    mapa_nome: dict[str, str] = {}
    for item in ESTIMATIVAS_POPULACAO_2025_MT or []:
        codigo = re.sub(r"\D", "", str(item.get("codigo_ibge", "")))
        municipio = str(item.get("municipio", "") or "").strip()
        if len(codigo) >= 6 and municipio:
            mapa_codigo[codigo[:6]] = codigo[:7]
            mapa_nome[codigo[:6]] = municipio
    for item in DEFAULT_MUNICIPIOS or []:
        codigo = re.sub(r"\D", "", str(item.get("codigo_ibge", "")))
        municipio = str(item.get("municipio", "") or "").strip()
        if len(codigo) >= 6 and municipio:
            mapa_codigo.setdefault(codigo[:6], codigo[:7])
            mapa_nome.setdefault(codigo[:6], municipio)
    return mapa_codigo, mapa_nome


def _codigo_ibge_completo_por_cod6(cod6: str) -> str:
    mapa_codigo, _ = _mapa_municipios_por_cod6()
    return mapa_codigo.get(str(cod6)[:6], str(cod6)[:6])


def _municipio_por_cod6(cod6: str) -> str:
    _, mapa_nome = _mapa_municipios_por_cod6()
    return mapa_nome.get(str(cod6)[:6], "")


def _texto_busca(valor: Any) -> str:
    texto = str(valor or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _load_legacy_geo():
    if not LEGACY_GEO_PATH.exists():
        raise FileNotFoundError(
            "Arquivo legacy/georreferenciamento_aps_antigo.py não encontrado. "
            "Copie o arquivo antigo ui/georreferenciamento_aps.py para a pasta legacy com esse nome."
        )
    spec = importlib.util.spec_from_file_location("legacy_georreferenciamento_aps_antigo", LEGACY_GEO_PATH)
    if spec is None or spec.loader is None:
        raise ImportError("Não foi possível carregar o arquivo legado de georreferenciamento APS.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call_legacy(nome_funcao: str, **kwargs: Any) -> pd.DataFrame:
    modulo = _load_legacy_geo()
    funcao = getattr(modulo, nome_funcao, None)
    if not callable(funcao):
        raise AttributeError(f"Função {nome_funcao} não encontrada em legacy/georreferenciamento_aps_antigo.py.")
    resultado = funcao(**kwargs)
    if isinstance(resultado, pd.DataFrame):
        return resultado.copy()
    if isinstance(resultado, list):
        return pd.DataFrame(resultado)
    if isinstance(resultado, dict):
        return pd.DataFrame([resultado])
    return pd.DataFrame({"resultado": [str(resultado)]})


def _primeira_coluna(df: pd.DataFrame, opcoes: list[str]) -> str | None:
    cols_norm = {_texto_busca(c).replace(" ", "_"): c for c in df.columns}
    for col in opcoes:
        if col in df.columns:
            return col
        chave = _texto_busca(col).replace(" ", "_")
        if chave in cols_norm:
            return cols_norm[chave]
    return None


def _ler_csv_bytes(bruto: bytes) -> pd.DataFrame:
    ultimo = None
    for enc in ["utf-8-sig", "utf-8", "latin1", "cp1252"]:
        for sep in [None, ";", ",", "\t", "|"]:
            try:
                return pd.read_csv(io.BytesIO(bruto), sep=sep, engine="python", encoding=enc, dtype=str, on_bad_lines="skip")
            except Exception as exc:
                ultimo = exc
    raise ValueError(f"Não foi possível ler CSV/TXT: {ultimo}")


def _ler_dataframe_por_arquivo(caminho: Path, bruto: bytes | None = None) -> pd.DataFrame:
    nome = caminho.name.lower()
    data = bruto if bruto is not None else caminho.read_bytes()
    if nome.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(data), dtype=str)
    if nome.endswith((".csv", ".txt")):
        # Caso oficial de largura fixa.
        if "equipesvalidasbrasil" in _texto_busca(caminho.name):
            return _parse_equipes_validas_brasil_fixed_width(data, caminho.name)
        return _ler_csv_bytes(data)
    raise ValueError(f"Formato não suportado para leitura tabular: {caminho.name}")


def _raiz_ine_para_chave_profissional(ine: Any) -> str:
    """Retorna a raiz usada no arquivo ProfissionaisEquipesBrasil.

    No EQUIPESValidasBrasil, o identificador pode vir com zeros à esquerda,
    por exemplo 0002489848. No arquivo de profissionais, a chave equivalente
    aparece como 2489. Por isso removemos zeros à esquerda antes de pegar
    os 4 primeiros dígitos.
    """
    digitos = re.sub(r"\D", "", "" if ine is None else str(ine))
    digitos = digitos.lstrip("0")
    return digitos[:4] if digitos else ""



def _classificar_tipo_equipe_oficial(tipo3: str, nome_equipe: str = "", desc_equipe: str = "") -> str:
    """Classifica o registro do arquivo oficial EQUIPESValidasBrasil.txt.

    O arquivo EQUIPES BRASIL do CNES vem em largura fixa. Na competência testada
    (EQUIPESBRASIL_202605.ZIP), o campo posicional 14:17 não traz diretamente
    os códigos 70, 71, 72, 73, 74 e 76. Ele aparece como famílias/códigos internos
    como 000/001/020 para ESF, 100/101/130 para ESB, 200/201/230 para eMulti/NASF,
    330/301 para Consultório na Rua, 400/401 para equipes prisionais e 600/601/602
    para eAP/PACS. Por isso a classificação combina posição + descrição.
    """
    tipo3 = str(tipo3 or "").strip()
    texto = _texto_busca(f"{nome_equipe} {desc_equipe}")

    # Regras textuais mais específicas primeiro.
    if any(t in texto for t in ["consultorio na rua", "ecr "]):
        return "73"

    if any(t in texto for t in ["prisional", "eapp", "cadeia", "penitenciaria", "ressocializacao", "colonia penal"]):
        return "74"

    if any(t in texto for t in ["saude bucal", "esb", "odontologia", "odonto"]):
        return "71"

    if any(t in texto for t in ["emulti", "nasf", "multiprofissional", "multi profissional"]):
        return "72"

    if any(t in texto for t in ["saude da familia", "esf", "psf", "usf"]):
        return "70"

    if any(t in texto for t in ["atencao primaria", "eap", "pacs"]):
        return "76"

    # Regras posicionais como fallback.
    if re.match(r"^1\d\d$", tipo3):
        return "71"
    if re.match(r"^2\d\d$", tipo3):
        return "72"
    if re.match(r"^4\d\d$", tipo3):
        return "74"
    if re.match(r"^6\d\d$", tipo3):
        return "76"
    if re.match(r"^0\d\d$", tipo3):
        return "70"
    if re.match(r"^3\d\d$", tipo3):
        # Família 3 pode conter EMAP/EMAD; só classifica como eCR quando o texto já indicou.
        return ""

    return ""


def _parse_equipes_validas_brasil_fixed_width(bruto: bytes, nome_origem: str = "EQUIPESValidasBrasil.txt") -> pd.DataFrame:
    """Lê o arquivo oficial EQUIPESValidasBrasil.txt do pacote EQUIPESBRASIL.

    Layout observado no arquivo oficial EQUIPESBRASIL_202605.ZIP:
    - 0:6   código IBGE do município sem dígito verificador;
    - 6:13  CNES;
    - 13:15 código do tipo de equipe (70, 71, 72, 73, 74, 76 etc.);
    - 15:17 sequencial/área curta;
    - 17:77 nome da equipe;
    - 77:81 área da equipe usada no vínculo profissional;
    - 81:141 descrição da equipe.

    Versões anteriores interpretavam o início da linha como código municipal de 7 dígitos.
    Isso reduzia artificialmente a contagem de equipes porque misturava o 6º dígito do
    município com o primeiro dígito do CNES. Esta versão usa o código de 6 dígitos e
    converte para o código IBGE completo da base canônica do sistema.
    """
    texto = None
    ultimo = None
    for enc in ["latin1", "cp1252", "utf-8-sig", "utf-8"]:
        try:
            texto = bruto.decode(enc)
            break
        except Exception as exc:
            ultimo = exc
    if texto is None:
        raise ValueError(f"Não foi possível decodificar {nome_origem}: {ultimo}")

    registros = []
    for linha in texto.splitlines():
        if len(linha) < 90:
            continue

        cod6 = linha[0:6].strip()
        cnes = linha[6:13].strip()
        codigo_tipo = linha[13:15].strip()
        sequencial_equipe = linha[15:17].strip()
        nome_equipe = linha[17:77].strip()
        area_equipe = linha[77:81].strip() if len(linha) >= 81 else ""
        desc_equipe = linha[81:141].strip() if len(linha) >= 141 else ""

        if not (cod6.isdigit() and cod6.startswith(UF_MT) and cnes.isdigit()):
            continue
        if codigo_tipo not in TIPOS_EQUIPE:
            continue

        ine_match = re.findall(r"\d{10}", linha)
        ine = ine_match[-1] if ine_match else ""

        registros.append({
            "codigo_ibge": _codigo_ibge_completo_por_cod6(cod6),
            "codigo_municipio_6": cod6,
            "municipio": _municipio_por_cod6(cod6),
            "cnes": cnes,
            "ine": ine,
            "nome_equipe": nome_equipe or desc_equipe,
            "codigo_tipo_equipe": codigo_tipo,
            "tipo_equipe": TIPOS_EQUIPE.get(codigo_tipo, f"Tipo CNES {codigo_tipo}"),
            "codigo_tipo_origem": codigo_tipo,
            "sequencial_equipe": sequencial_equipe,
            "area_equipe": area_equipe,
            "descricao_equipe": desc_equipe,
            "situacao_equipe": "Válida",
            "fonte": f"CNES/DATASUS oficial - {nome_origem}",
        })

    return pd.DataFrame(registros)

def _arquivos_locais_candidatos() -> Iterable[Path]:
    pastas = [
        UPLOADS_DIR / "cnes_equipes",
        UPLOADS_DIR,
        RAW_DIR / "apis",
        RAW_DIR,
        DATA_DIR,
        ROOT_DIR / "legacy",
    ]
    exts = {".zip", ".csv", ".txt", ".xlsx", ".xls"}
    for pasta in pastas:
        if not pasta.exists():
            continue
        for item in pasta.rglob("*"):
            if not item.is_file() or item.suffix.lower() not in exts:
                continue
            busca = _texto_busca(item.name)
            if any(t in busca for t in ["equipe", "equipes", "ine", "equipesvalidasbrasil", "equipesbrasil"]):
                yield item


def _carregar_arquivo_local_equipes() -> pd.DataFrame:
    erros = []
    for caminho in _arquivos_locais_candidatos():
        try:
            if caminho.suffix.lower() == ".zip":
                with zipfile.ZipFile(caminho, "r") as zf:
                    nomes = [n for n in zf.namelist() if not n.endswith("/")]
                    # Preferência para arquivo oficial.
                    oficiais = [n for n in nomes if "equipesvalidasbrasil" in _texto_busca(n) and n.lower().endswith(".txt")]
                    if oficiais:
                        escolhido = max(oficiais, key=lambda n: zf.getinfo(n).file_size)
                        df = _parse_equipes_validas_brasil_fixed_width(zf.read(escolhido), escolhido)
                        if not df.empty:
                            return df
                    tabulares = [n for n in nomes if n.lower().endswith((".csv", ".txt", ".xlsx", ".xls"))]
                    if tabulares:
                        def score(n: str):
                            busca = _texto_busca(n)
                            bonus = 1 if any(t in busca for t in ["equipe", "equipes", "ine"]) else 0
                            return (bonus, zf.getinfo(n).file_size)
                        escolhido = max(tabulares, key=score)
                        fake = Path(escolhido)
                        df = _ler_dataframe_por_arquivo(fake, zf.read(escolhido))
                        if not df.empty:
                            return df
            else:
                df = _ler_dataframe_por_arquivo(caminho)
                if not df.empty:
                    return df
        except Exception as exc:
            erros.append(f"{caminho.name}: {exc}")
    detalhe = " | ".join(erros[-5:]) if erros else "nenhum arquivo local encontrado"
    raise ValueError(
        "Não encontrei arquivo local de equipes CNES/INE. Coloque o ZIP/CSV/TXT/XLSX de EQUIPES BRASIL em "
        "data/uploads/cnes_equipes/ ou use Upload de planilhas com tipo de base 'equipes'. "
        f"Detalhe: {detalhe}"
    )


def _normalizar_equipes_para_importacao(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["municipio", "cnes", "ine", "codigo_tipo_equipe", "tipo_equipe", "carga_horaria"])

    work = df.copy()
    municipio = _primeira_coluna(work, ["municipio", "no_municipio", "cidade", "nome_municipio"])
    cod_ibge = _primeira_coluna(work, ["codigo_ibge", "co_municipio", "cod_municipio", "codigo_municipio", "cod_ibge"])
    cnes = _primeira_coluna(work, ["cnes", "co_cnes", "codigo_cnes", "cod_cnes"])
    ine = _primeira_coluna(work, ["ine", "co_ine", "codigo_ine", "cod_ine", "co_equipe", "codigo_equipe"])
    codigo = _primeira_coluna(work, ["codigo_tipo_equipe", "tipo_equipe_codigo", "co_tipo_equipe", "tipo_codigo", "codigo", "tp_equipe"])
    tipo = _primeira_coluna(work, ["tipo_equipe", "ds_tipo_equipe", "descricao_tipo_equipe", "no_tipo_equipe"])
    carga = _primeira_coluna(work, ["carga_horaria", "qt_carga_horaria", "ch"])

    out = pd.DataFrame(index=work.index)
    out["municipio"] = work[municipio].astype(str).str.strip() if municipio else ""
    if cod_ibge:
        out["codigo_ibge"] = work[cod_ibge].astype(str).str.extract(r"(\d{6,7})", expand=False).fillna("").str[:7]
        out = out[out["codigo_ibge"].astype(str).str.startswith(UF_MT, na=False) | out["codigo_ibge"].eq("")].copy()
    else:
        out["codigo_ibge"] = ""
    out["cnes"] = work[cnes].astype(str).str.extract(r"(\d+)", expand=False).fillna("") if cnes else ""
    out["ine"] = work[ine].astype(str).str.extract(r"(\d+)", expand=False).fillna("") if ine else ""

    if codigo:
        out["codigo_tipo_equipe"] = work[codigo].astype(str).str.extract(r"(70|71|72|73|74|76)", expand=False).fillna("")
    else:
        txt = work[tipo].astype(str).str.lower() if tipo else pd.Series([""] * len(work), index=work.index)
        out["codigo_tipo_equipe"] = ""
        out.loc[txt.str.contains("saude da familia|saúde da família|\\besf\\b", regex=True, na=False), "codigo_tipo_equipe"] = "70"
        out.loc[txt.str.contains("saude bucal|saúde bucal|\\besb\\b", regex=True, na=False), "codigo_tipo_equipe"] = "71"
        out.loc[txt.str.contains("multi|multiprof", regex=True, na=False), "codigo_tipo_equipe"] = "72"
        out.loc[txt.str.contains("consultorio na rua|consultório na rua", regex=True, na=False), "codigo_tipo_equipe"] = "73"
        out.loc[txt.str.contains("prisional|eapp", regex=True, na=False), "codigo_tipo_equipe"] = "74"
        out.loc[txt.str.contains("atencao primaria|atenção primária|\\beap\\b", regex=True, na=False), "codigo_tipo_equipe"] = "76"

    out = out[out["codigo_tipo_equipe"].isin(TIPOS_EQUIPE.keys())].copy()
    out["tipo_equipe"] = out["codigo_tipo_equipe"].map(TIPOS_EQUIPE)
    out["carga_horaria"] = pd.to_numeric(work[carga], errors="coerce") if carga else None

    for extra in ["nome_equipe", "area_equipe", "sequencial_equipe", "situacao_equipe", "fonte"]:
        if extra in work.columns and extra not in out.columns:
            out[extra] = work[extra]

    out = out[(out["municipio"].astype(str).str.strip() != "") | (out["codigo_ibge"].astype(str).str.strip() != "")].copy()
    # Importação estruturada atual consolida por município; se o arquivo oficial vier apenas com código IBGE de 6 dígitos,
    # ele ficará disponível para auditoria, mas a contagem por município dependerá de enriquecimento posterior.
    out = out.drop_duplicates(subset=["municipio", "codigo_ibge", "cnes", "ine", "codigo_tipo_equipe"], keep="last")
    return out.reset_index(drop=True)




def _montar_chave_profissionais(codigo_municipio_6: Any, area_equipe: Any, raiz_ine: Any) -> str:
    """Chave técnica, anonimizada, usada para cruzar ProfissionaisEquipesBrasil com EQUIPESValidasBrasil."""
    cod = re.sub(r"\D", "", "" if codigo_municipio_6 is None else str(codigo_municipio_6))[:6]
    area = re.sub(r"\D", "", "" if area_equipe is None else str(area_equipe)).zfill(4)[:4]
    raiz = re.sub(r"\D", "", "" if raiz_ine is None else str(raiz_ine))[:4]
    if not (cod and area and raiz):
        return ""
    return f"{cod}|{area}|{raiz}"


def _parse_chaves_equipes_validas_para_profissionais(bruto: bytes, nome_origem: str = "EQUIPESValidasBrasil.txt") -> dict[str, dict[str, str]]:
    """Lê EQUIPESValidasBrasil no layout usado para cruzar com ProfissionaisEquipesBrasil.

    O arquivo de profissionais usa município com 6 dígitos e uma chave técnica formada por
    município + área da equipe + raiz do identificador da equipe. Esta rotina monta esse mapa
    sem expor CPF/CNS e usando o layout correto do EQUIPESBRASIL_202605.
    """
    texto = None
    ultimo = None
    for enc in ["latin1", "cp1252", "utf-8-sig", "utf-8"]:
        try:
            texto = bruto.decode(enc)
            break
        except Exception as exc:
            ultimo = exc
    if texto is None:
        raise ValueError(f"Não foi possível decodificar {nome_origem}: {ultimo}")

    mapa: dict[str, dict[str, str]] = {}
    for linha in texto.splitlines():
        if len(linha) < 90:
            continue

        cod6 = linha[0:6].strip()
        cnes = linha[6:13].strip()
        codigo_tipo = linha[13:15].strip()
        sequencial_equipe = linha[15:17].strip()
        nome_equipe = linha[17:77].strip()
        area_equipe = linha[77:81].strip() if len(linha) >= 81 else ""
        desc_equipe = linha[81:141].strip() if len(linha) >= 141 else ""
        ine_match = re.findall(r"\d{10}", linha)
        ine = ine_match[-1] if ine_match else ""

        if not (cod6.isdigit() and cod6.startswith(UF_MT) and cnes.isdigit() and ine):
            continue
        if codigo_tipo not in TIPOS_EQUIPE:
            continue

        raiz = _raiz_ine_para_chave_profissional(ine)
        chave = _montar_chave_profissionais(cod6, area_equipe, raiz)
        if not chave:
            continue

        mapa[chave] = {
            "codigo_ibge": _codigo_ibge_completo_por_cod6(cod6),
            "codigo_municipio_6": cod6,
            "municipio": _municipio_por_cod6(cod6),
            "cnes": cnes,
            "ine": ine,
            "codigo_tipo_equipe": codigo_tipo,
            "tipo_equipe": TIPOS_EQUIPE.get(codigo_tipo, f"Tipo CNES {codigo_tipo}"),
            "nome_equipe": nome_equipe or desc_equipe,
            "sequencial_equipe": sequencial_equipe,
            "area_equipe": area_equipe,
        }
    return mapa

def _parse_profissionais_equipes_brasil_fixed_width(
    bruto: bytes,
    mapa_chaves_equipes: dict[str, dict[str, str]] | None = None,
    nome_origem: str = "ProfissionaisEquipesBrasil.txt",
) -> pd.DataFrame:
    """Lê ProfissionaisEquipesBrasil.txt de forma anonimizada.

    A rotina não armazena CPF/CNS. Ela cria apenas um hash técnico curto para contagem
    agregada de vínculos profissionais por município/equipe.
    """
    texto = None
    ultimo = None
    for enc in ["latin1", "cp1252", "utf-8-sig", "utf-8"]:
        try:
            texto = bruto.decode(enc)
            break
        except Exception as exc:
            ultimo = exc
    if texto is None:
        raise ValueError(f"Não foi possível decodificar {nome_origem}: {ultimo}")

    registros = []
    mapa = mapa_chaves_equipes or {}
    for linha in texto.splitlines():
        if len(linha) < 24:
            continue
        co_mun_6 = linha[0:6].strip()
        cbo = linha[6:12].strip()
        codigo_vinculo = re.sub(r"\D", "", linha[12:22])
        if not (co_mun_6.isdigit() and co_mun_6.startswith(UF_MT) and cbo.isdigit() and len(codigo_vinculo) >= 8):
            continue

        area_equipe = codigo_vinculo[0:4]
        raiz_ine = codigo_vinculo[4:8]
        chave = _montar_chave_profissionais(co_mun_6, area_equipe, raiz_ine)
        if not chave:
            continue

        dados_equipe = mapa.get(chave, {})
        # Quando existe mapa de equipes, mantém apenas profissionais vinculados às equipes prioritárias 70, 71, 72, 73, 74 e 76.
        if mapa and not dados_equipe:
            continue

        token_bruto = re.sub(r"\D", "", linha[22:34])
        if not token_bruto:
            token_bruto = hashlib.sha256(linha.encode("utf-8", errors="ignore")).hexdigest()
        profissional_hash = hashlib.sha256(f"{co_mun_6}|{cbo}|{chave}|{token_bruto}".encode("utf-8")).hexdigest()[:20]

        registros.append({
            "codigo_ibge": dados_equipe.get("codigo_ibge", _codigo_ibge_completo_por_cod6(co_mun_6)),
            "codigo_municipio_6": co_mun_6,
            "municipio": dados_equipe.get("municipio", _municipio_por_cod6(co_mun_6)),
            "cnes": dados_equipe.get("cnes", ""),
            "ine": dados_equipe.get("ine", ""),
            "codigo_tipo_equipe": dados_equipe.get("codigo_tipo_equipe", ""),
            "tipo_equipe": dados_equipe.get("tipo_equipe", ""),
            "cbo": cbo,
            "nome_profissional": profissional_hash,
            "profissional_hash": profissional_hash,
            "area_equipe": area_equipe,
            "raiz_ine_profissionais": raiz_ine,
            "chave_profissionais_equipe": chave,
            "fonte": f"CNES/DATASUS oficial - {nome_origem}",
        })

    if not registros:
        return pd.DataFrame(columns=[
            "codigo_ibge", "municipio", "cnes", "ine", "codigo_tipo_equipe", "tipo_equipe",
            "cbo", "nome_profissional", "profissional_hash", "area_equipe", "raiz_ine_profissionais",
            "chave_profissionais_equipe", "fonte"
        ])

    df = pd.DataFrame(registros)
    df = df.drop_duplicates(subset=["codigo_ibge", "profissional_hash", "chave_profissionais_equipe"], keep="last")
    return df.reset_index(drop=True)


def _carregar_arquivo_local_profissionais_equipes() -> pd.DataFrame:
    """Procura EQUIPESBRASIL local e extrai ProfissionaisEquipesBrasil.txt."""
    erros = []
    for caminho in _arquivos_locais_candidatos():
        try:
            if caminho.suffix.lower() == ".zip":
                with zipfile.ZipFile(caminho, "r") as zf:
                    nomes = [n for n in zf.namelist() if not n.endswith("/")]
                    arq_equipes = [n for n in nomes if "equipesvalidasbrasil" in _texto_busca(n) and n.lower().endswith(".txt")]
                    arq_prof = [n for n in nomes if "profissionaisequipesbrasil" in _texto_busca(n) and n.lower().endswith(".txt")]
                    if arq_prof:
                        mapa = {}
                        if arq_equipes:
                            escolhido_eq = max(arq_equipes, key=lambda n: zf.getinfo(n).file_size)
                            mapa = _parse_chaves_equipes_validas_para_profissionais(zf.read(escolhido_eq), escolhido_eq)
                        escolhido_prof = max(arq_prof, key=lambda n: zf.getinfo(n).file_size)
                        df = _parse_profissionais_equipes_brasil_fixed_width(zf.read(escolhido_prof), mapa, escolhido_prof)
                        if not df.empty:
                            return df
            elif "profissionaisequipesbrasil" in _texto_busca(caminho.name):
                df = _parse_profissionais_equipes_brasil_fixed_width(caminho.read_bytes(), {}, caminho.name)
                if not df.empty:
                    return df
        except Exception as exc:
            erros.append(f"{caminho.name}: {exc}")
    detalhe = " | ".join(erros[-5:]) if erros else "nenhum arquivo local com ProfissionaisEquipesBrasil encontrado"
    raise ValueError(
        "Não encontrei ProfissionaisEquipesBrasil.txt. Coloque o ZIP oficial EQUIPESBRASIL em "
        f"data/uploads/cnes_equipes/. Detalhe: {detalhe}"
    )


def carregar_cnes_profissionais_equipes_mt() -> pd.DataFrame:
    """Carrega profissionais vinculados às equipes CNES/INE do arquivo ProfissionaisEquipesBrasil.txt."""
    df = _carregar_arquivo_local_profissionais_equipes()
    if df.empty:
        raise ValueError("ProfissionaisEquipesBrasil foi localizado, mas retornou vazio após leitura.")
    return df

def carregar_cnes_equipes_ine_arquivo(caminho: str | Path) -> pd.DataFrame:
    """Lê diretamente um arquivo manual de equipes CNES/INE.

    Aceita ZIP, TXT, CSV, XLSX e XLS. Foi criada para o fluxo de upload manual
    quando os endpoints S3/CKAN retornarem 403/404.
    """
    caminho = Path(caminho)
    if not caminho.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho}")

    if caminho.suffix.lower() == ".zip":
        with zipfile.ZipFile(caminho, "r") as zf:
            nomes = [n for n in zf.namelist() if not n.endswith("/")]
            oficiais = [n for n in nomes if "equipesvalidasbrasil" in _texto_busca(n) and n.lower().endswith(".txt")]
            if oficiais:
                escolhido = max(oficiais, key=lambda n: zf.getinfo(n).file_size)
                df = _parse_equipes_validas_brasil_fixed_width(zf.read(escolhido), escolhido)
                return _normalizar_equipes_para_importacao(df)
            tabulares = [n for n in nomes if n.lower().endswith((".csv", ".txt", ".xlsx", ".xls"))]
            if not tabulares:
                raise ValueError("O ZIP não contém arquivo TXT/CSV/XLSX/XLS tabular de equipes.")
            def score(n: str):
                busca = _texto_busca(n)
                bonus = 1 if any(t in busca for t in ["equipe", "equipes", "ine"]) else 0
                return (bonus, zf.getinfo(n).file_size)
            escolhido = max(tabulares, key=score)
            df = _ler_dataframe_por_arquivo(Path(escolhido), zf.read(escolhido))
            return _normalizar_equipes_para_importacao(df)

    df = _ler_dataframe_por_arquivo(caminho)
    return _normalizar_equipes_para_importacao(df)


def carregar_cnes_equipes_ine_mt() -> pd.DataFrame:
    """Carrega equipes APS/INE, mantendo somente os códigos 70, 71, 72, 73, 74 e 76.

    v11.4: prioriza o ZIP oficial EQUIPESBRASIL salvo localmente.
    Motivo: as funções legacy podem retornar uma base agregada/colapsada com cerca
    de 345 registros, enquanto o arquivo oficial EQUIPESValidasBrasil.txt da
    competência 202605 possui cerca de 2.040 equipes APS/INE em Mato Grosso.
    """
    erros = []

    # 1) Caminho manual/local OFICIAL: deve ser prioridade quando o arquivo
    # EQUIPESBRASIL_YYYYMM.ZIP está em data/uploads/cnes_equipes/.
    try:
        df = _carregar_arquivo_local_equipes()
        normalizado = _normalizar_equipes_para_importacao(df)
        if not normalizado.empty:
            normalizado["fonte"] = "CNES/DATASUS oficial - EQUIPESBRASIL local"
            return normalizado
        erros.append("arquivo local/manual: retornou vazio após normalização")
    except Exception as exc:
        erros.append(f"arquivo local/manual: {exc}")

    # 2) Fallback legado, apenas se o arquivo local não existir ou falhar.
    for funcao in ["carregar_equipes_ine_cnes_datasus_oficial", "carregar_cnes_equipes_ine_mt"]:
        try:
            df = _call_legacy(funcao)
            normalizado = _normalizar_equipes_para_importacao(df)
            if not normalizado.empty:
                normalizado["fonte"] = f"legacy:{funcao}"
                return normalizado
            erros.append(f"{funcao}: retornou vazio")
        except Exception as exc:
            erros.append(f"{funcao}: {exc}")

    detalhe = " | ".join(erros[-8:])
    raise ValueError(
        "Não foi possível carregar a base de equipes CNES/INE. "
        "Coloque o ZIP oficial EQUIPESBRASIL em data/uploads/cnes_equipes/. "
        f"Últimos erros: {detalhe}"
    )
