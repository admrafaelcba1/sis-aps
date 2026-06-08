
from __future__ import annotations

from pathlib import Path
import importlib
import shutil
import subprocess
import sys
import tempfile
import pandas as pd



def _sucesso_csv_payload(base: dict, csv_path: Path) -> dict:
    """Padroniza chaves para a UI não exibir None."""
    base["arquivo_csv"] = str(csv_path)
    base["caminho_csv"] = str(csv_path)
    base["csv_path"] = str(csv_path)
    return base

def diagnosticar_dbc(caminho: str) -> dict:
    p = Path(caminho)
    if not p.exists():
        return {
            "ok": False,
            "mensagem": "Arquivo DBC não encontrado.",
            "arquivo": str(p),
            "existe": "Não",
            "tamanho_mb": 0,
            "bibliotecas": _diagnosticar_bibliotecas(),
        }

    return {
        "ok": True,
        "mensagem": "Arquivo DBC localizado.",
        "arquivo": str(p),
        "existe": "Sim",
        "tamanho_mb": round(p.stat().st_size / (1024 * 1024), 2),
        "bibliotecas": _diagnosticar_bibliotecas(),
    }


def _tem_modulo(nome: str) -> bool:
    try:
        importlib.import_module(nome)
        return True
    except Exception:
        return False


def _diagnosticar_bibliotecas() -> pd.DataFrame:
    libs = [
        ("datasus_dbc", "Preferencial para descompactar DBC no Windows"),
        ("dbfread", "Leitura do DBF após conversão"),
        ("pysus", "Alternativa; algumas versões mudaram a API"),
        ("read_dbf", "Alternativa antiga"),
        ("read_dbc", "Alternativa antiga"),
    ]
    return pd.DataFrame([
        {
            "biblioteca": nome,
            "instalada": "Sim" if _tem_modulo(nome) else "Não",
            "uso": uso,
        }
        for nome, uso in libs
    ])


def comando_instalacao_dbc() -> str:
    return (
        "python -m pip install --upgrade pip setuptools wheel\n"
        "python -m pip install datasus-dbc dbfread\n"
    )


def comandos_instalacao_dbc() -> str:
    """Alias de compatibilidade usado pela tela Base de Dados."""
    return comando_instalacao_dbc()


def _read_dbf_to_csv(dbf_path: Path, csv_path: Path) -> dict:
    try:
        from dbfread import DBF
    except Exception as e:
        return {
            "ok": False,
            "mensagem": "DBF foi gerado, mas não foi possível ler. Instale dbfread.",
            "detalhe": str(e),
        }

    rows = []
    table = DBF(str(dbf_path), encoding="latin1", char_decode_errors="ignore")
    for rec in table:
        rows.append(dict(rec))
    df = pd.DataFrame(rows)
    df.to_csv(csv_path, index=False, sep=";", encoding="utf-8-sig")
    return {
        "ok": True,
        "linhas": len(df),
        "colunas": len(df.columns),
    }


def _converter_com_datasus_dbc(dbc_path: Path, dbf_path: Path) -> tuple[bool, str]:
    """Tenta usar o pacote datasus-dbc.

    O pacote já teve variações de API entre versões. Por isso tentamos:
    1. função Python, se existir;
    2. execução via módulo/CLI, se disponível.
    """
    try:
        mod = importlib.import_module("datasus_dbc")
    except Exception as e:
        return False, f"datasus_dbc não disponível: {e}"

    # Tentativas por função Python.
    candidatos_funcoes = [
        "decompress",
        "decompress_dbc",
        "dbc2dbf",
        "convert",
        "convert_dbc",
    ]
    for fname in candidatos_funcoes:
        fn = getattr(mod, fname, None)
        if callable(fn):
            try:
                result = fn(str(dbc_path), str(dbf_path))
                if dbf_path.exists() and dbf_path.stat().st_size > 0:
                    return True, f"Convertido com datasus_dbc.{fname}"
                # algumas funções retornam bytes
                if isinstance(result, (bytes, bytearray)):
                    dbf_path.write_bytes(result)
                    return True, f"Convertido com datasus_dbc.{fname} retornando bytes"
            except Exception as e:
                last = f"datasus_dbc.{fname}: {e}"
        else:
            last = f"datasus_dbc.{fname}: função não encontrada"

    # Tentativas por submódulos conhecidos.
    submods = [
        "datasus_dbc.dbc",
        "datasus_dbc.decompress",
        "datasus_dbc.converter",
    ]
    for sm in submods:
        try:
            m = importlib.import_module(sm)
            for fname in candidatos_funcoes:
                fn = getattr(m, fname, None)
                if callable(fn):
                    try:
                        result = fn(str(dbc_path), str(dbf_path))
                        if dbf_path.exists() and dbf_path.stat().st_size > 0:
                            return True, f"Convertido com {sm}.{fname}"
                        if isinstance(result, (bytes, bytearray)):
                            dbf_path.write_bytes(result)
                            return True, f"Convertido com {sm}.{fname} retornando bytes"
                    except Exception as e:
                        last = f"{sm}.{fname}: {e}"
        except Exception as e:
            last = f"{sm}: {e}"

    # Tentativas por CLI/módulo.
    cli_cmds = [
        [sys.executable, "-m", "datasus_dbc", str(dbc_path), str(dbf_path)],
        [sys.executable, "-m", "datasus_dbc", "decompress", str(dbc_path), str(dbf_path)],
        [sys.executable, "-m", "datasus_dbc", "convert", str(dbc_path), str(dbf_path)],
    ]
    for cmd in cli_cmds:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if dbf_path.exists() and dbf_path.stat().st_size > 0:
                return True, "Convertido via CLI: " + " ".join(cmd)
            last = (r.stderr or r.stdout or "sem saída")[:500]
        except Exception as e:
            last = str(e)

    return False, f"datasus-dbc instalado, mas não consegui acionar API/CLI automaticamente. Última tentativa: {last}"


def _converter_com_pysus(dbc_path: Path, dbf_path: Path) -> tuple[bool, str]:
    # Mantém tentativas antigas como fallback.
    tentativas = []

    try:
        from pysus.utilities.readdbc import read_dbc
        df = read_dbc(str(dbc_path))
        if isinstance(df, pd.DataFrame):
            # Se o PySUS já ler direto para DataFrame, não gera DBF. Sinaliza via CSV temporário em outro fluxo.
            csv_tmp = dbf_path.with_suffix(".csv")
            df.to_csv(csv_tmp, index=False, sep=";", encoding="utf-8-sig")
            return True, f"PYSUS_DATAFRAME::{csv_tmp}"
    except Exception as e:
        tentativas.append(f"pysus.utilities.readdbc.read_dbc: {e}")

    try:
        from read_dbc import read_dbc
        df = read_dbc(str(dbc_path))
        if isinstance(df, pd.DataFrame):
            csv_tmp = dbf_path.with_suffix(".csv")
            df.to_csv(csv_tmp, index=False, sep=";", encoding="utf-8-sig")
            return True, f"PYSUS_READ_DBC_DATAFRAME::{csv_tmp}"
    except Exception as e:
        tentativas.append(f"read_dbc.read_dbc: {e}")

    return False, " | ".join(tentativas)


def converter_dbc_para_csv(caminho_dbc: str, caminho_csv: str | None = None) -> dict:
    dbc_path = Path(caminho_dbc)
    if not dbc_path.exists():
        return {
            "ok": False,
            "mensagem": "Arquivo DBC não encontrado.",
            "detalhe": str(dbc_path),
            "bibliotecas": _diagnosticar_bibliotecas(),
            "comando": comando_instalacao_dbc(),
        }

    if caminho_csv:
        csv_path = Path(caminho_csv)
    else:
        csv_path = dbc_path.with_suffix(".csv")
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    dbf_path = dbc_path.with_suffix(".dbf")

    detalhes = []

    ok, msg = _converter_com_datasus_dbc(dbc_path, dbf_path)
    detalhes.append(msg)

    if not ok:
        ok, msg = _converter_com_pysus(dbc_path, dbf_path)
        detalhes.append(msg)
        if ok and msg.startswith("PYSUS") and "::" in msg:
            csv_tmp = Path(msg.split("::", 1)[1])
            if csv_tmp.exists():
                if csv_tmp.resolve() != csv_path.resolve():
                    shutil.copy2(csv_tmp, csv_path)
                return _sucesso_csv_payload({
                    "ok": True,
                    "mensagem": "DBC lido diretamente para CSV.",
                    "arquivo_dbc": str(dbc_path),
                    "detalhe": " | ".join(detalhes),
                    "bibliotecas": _diagnosticar_bibliotecas(),
                }, csv_path)

    if not ok:
        return {
            "ok": False,
            "mensagem": "Não foi possível converter o DBC automaticamente.",
            "detalhe": " | ".join(detalhes),
            "bibliotecas": _diagnosticar_bibliotecas(),
            "comando": comando_instalacao_dbc(),
        }

    if not dbf_path.exists() or dbf_path.stat().st_size == 0:
        return {
            "ok": False,
            "mensagem": "Conversão para DBF não gerou arquivo válido.",
            "detalhe": " | ".join(detalhes),
            "bibliotecas": _diagnosticar_bibliotecas(),
            "comando": comando_instalacao_dbc(),
        }

    read = _read_dbf_to_csv(dbf_path, csv_path)
    if not read.get("ok"):
        return {
            "ok": False,
            "mensagem": read.get("mensagem", "Falha ao ler DBF convertido."),
            "detalhe": read.get("detalhe", "") + " | " + " | ".join(detalhes),
            "bibliotecas": _diagnosticar_bibliotecas(),
            "comando": comando_instalacao_dbc(),
        }

    return _sucesso_csv_payload({
        "ok": True,
        "mensagem": "DBC convertido para CSV com sucesso.",
        "arquivo_dbc": str(dbc_path),
        "arquivo_dbf": str(dbf_path),
        "linhas": read.get("linhas", 0),
        "colunas": read.get("colunas", 0),
        "detalhe": " | ".join(detalhes),
        "bibliotecas": _diagnosticar_bibliotecas(),
    }, csv_path)
