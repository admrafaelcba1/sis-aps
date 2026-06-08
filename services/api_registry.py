from __future__ import annotations

import importlib
import importlib.util
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from config.api_catalog import API_BLOCKS, API_CATALOG
from config.settings import PROCESSED_DIR, RAW_DIR, ROOT_DIR
from database.connection import db_session
from services.importacao_service import importar_dataframe_estruturado

LEGACY_DIR = ROOT_DIR / "legacy"
LEGACY_CONNECTORS_PATH = LEGACY_DIR / "conectores_apis_ubs_antigo.py"


def legacy_status() -> dict[str, Any]:
    """Verifica se os arquivos legados esperados foram copiados."""
    esperados = [
        "conectores_apis_ubs_antigo.py",
        "cache_dados_aps_antigo.py",
        "georreferenciamento_aps_antigo.py",
        "dashboard_executivo_aps_antigo.py",
        "dashboard_profissionais_aps_antigo.py",
        "diagnostico_ubs_ses_antigo.py",
        "data_municipios_antigo.py",
        "utils_municipios_antigo.py",
    ]
    arquivos = []
    for nome in esperados:
        caminho = LEGACY_DIR / nome
        arquivos.append(
            {
                "arquivo": nome,
                "encontrado": caminho.exists(),
                "tamanho_kb": round(caminho.stat().st_size / 1024, 1) if caminho.exists() else 0,
            }
        )
    return {
        "legacy_dir": str(LEGACY_DIR),
        "connectors_path": str(LEGACY_CONNECTORS_PATH),
        "connectors_exists": LEGACY_CONNECTORS_PATH.exists(),
        "arquivos": arquivos,
    }


def _load_legacy_module():
    if not LEGACY_CONNECTORS_PATH.exists():
        raise FileNotFoundError(
            "Arquivo legacy/conectores_apis_ubs_antigo.py não encontrado. "
            "Copie o arquivo antigo ui/conectores_apis_ubs.py para a pasta legacy com esse nome."
        )
    spec = importlib.util.spec_from_file_location("legacy_conectores_apis_ubs_antigo", LEGACY_CONNECTORS_PATH)
    if spec is None or spec.loader is None:
        raise ImportError("Não foi possível preparar a importação do arquivo legado de APIs.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _funcoes_definidas_no_arquivo(caminho: Path) -> set[str]:
    if not caminho.exists():
        return set()
    try:
        texto = caminho.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return set()
    return set(re.findall(r"^def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", texto, flags=re.MULTILINE))


def _funcoes_legacy_disponiveis() -> set[str]:
    funcoes = _funcoes_definidas_no_arquivo(LEGACY_CONNECTORS_PATH)
    if LEGACY_CONNECTORS_PATH.exists():
        try:
            modulo = _load_legacy_module()
            funcoes = funcoes.union({nome for nome in dir(modulo) if callable(getattr(modulo, nome, None))})
        except Exception:
            pass
    return funcoes


def _funcao_nativa_disponivel(item: dict[str, Any]) -> bool:
    modulo_nome = item.get("modulo_nativo")
    funcao_nome = item.get("funcao_nativa")
    if not modulo_nome or not funcao_nome:
        return False
    try:
        modulo = importlib.import_module(modulo_nome)
        return callable(getattr(modulo, funcao_nome, None))
    except Exception:
        return False


def listar_catalogo() -> pd.DataFrame:
    conectores_ok = legacy_status()["connectors_exists"]
    funcoes_legacy = _funcoes_legacy_disponiveis()
    linhas = []

    for item in API_CATALOG:
        funcao_carregar = item.get("funcao_carregar")
        funcao_testar = item.get("funcao_testar")
        nativa_ok = _funcao_nativa_disponivel(item)
        legacy_ok = bool(conectores_ok and funcao_carregar in funcoes_legacy)
        executor = item.get("executor", "legacy")
        linhas.append(
            {
                "codigo": item["codigo"],
                "grupo": item["grupo"],
                "base": item["nome"],
                "tipo_base": item.get("tipo_base") or "referência/apoio",
                "executor": "nativo" if executor == "nativo" and nativa_ok else "legacy",
                "função_nativa": item.get("funcao_nativa") or "—",
                "função_carregar_legacy": funcao_carregar,
                "função_testar": funcao_testar or "—",
                "nativa_encontrada": bool(nativa_ok),
                "legacy_encontrada": bool(legacy_ok),
                "função_encontrada": bool(nativa_ok or legacy_ok),
                "fallback_legacy": bool(item.get("fallback_legacy", True)),
                "situação": item.get("status_migracao", "Mapeada"),
            }
        )
    return pd.DataFrame(linhas)


def listar_blocos() -> pd.DataFrame:
    linhas = []
    catalogo = {item["codigo"]: item for item in API_CATALOG}
    status = listar_catalogo().set_index("codigo") if not listar_catalogo().empty else pd.DataFrame()
    for bloco in API_BLOCKS:
        apis = bloco.get("apis", [])
        encontradas = 0
        nativas = 0
        for codigo in apis:
            if not status.empty and codigo in status.index:
                if bool(status.loc[codigo, "função_encontrada"]):
                    encontradas += 1
                if bool(status.loc[codigo, "nativa_encontrada"]):
                    nativas += 1
        linhas.append(
            {
                "codigo": bloco["codigo"],
                "bloco": bloco["nome"],
                "descrição": bloco["descricao"],
                "apis": len(apis),
                "apis_encontradas": encontradas,
                "apis_nativas": nativas,
                "itens": ", ".join([catalogo.get(c, {}).get("nome", c) for c in apis]),
            }
        )
    return pd.DataFrame(linhas)


def obter_api(codigo: str) -> dict[str, Any]:
    for item in API_CATALOG:
        if item["codigo"] == codigo:
            return item
    raise KeyError(f"API não cadastrada no catálogo: {codigo}")


def obter_bloco(codigo: str) -> dict[str, Any]:
    for item in API_BLOCKS:
        if item["codigo"] == codigo:
            return item
    raise KeyError(f"Bloco não cadastrado: {codigo}")


def _chamar_funcao_legada(nome_funcao: str, kwargs: dict[str, Any] | None = None) -> Any:
    modulo = _load_legacy_module()
    funcao = getattr(modulo, nome_funcao, None)
    if not callable(funcao):
        raise AttributeError(f"Função {nome_funcao} não encontrada no arquivo legado.")
    return funcao(**(kwargs or {}))


def _chamar_funcao_nativa(item: dict[str, Any]) -> Any:
    modulo_nome = item.get("modulo_nativo")
    funcao_nome = item.get("funcao_nativa")
    if not modulo_nome or not funcao_nome:
        raise AttributeError("API sem módulo/função nativa cadastrada.")
    modulo = importlib.import_module(modulo_nome)
    funcao = getattr(modulo, funcao_nome, None)
    if not callable(funcao):
        raise AttributeError(f"Função nativa {funcao_nome} não encontrada em {modulo_nome}.")
    return funcao(**(item.get("kwargs_carregar") or {}))


def _executar_item_api(item: dict[str, Any]) -> tuple[Any, str]:
    """Executa primeiro o conector nativo quando existir; se falhar, tenta legacy."""
    erros = []
    if item.get("executor") == "nativo":
        try:
            return _chamar_funcao_nativa(item), "nativo"
        except Exception as exc:
            erros.append(f"nativo: {exc}")
            if not item.get("fallback_legacy", True):
                raise

    try:
        return _chamar_funcao_legada(item["funcao_carregar"], item.get("kwargs_carregar")), "legacy"
    except Exception as exc:
        erros.append(f"legacy: {exc}")
        raise RuntimeError(" | ".join(erros))


def testar_api(codigo: str) -> dict[str, Any]:
    item = obter_api(codigo)
    inicio = datetime.now()

    # Na v04, APIs nativas são testadas com a própria carga, sem gravar/importar.
    if item.get("executor") == "nativo":
        try:
            resultado, origem = _executar_item_api(item)
            df = _resultado_para_dataframe(resultado)
            duracao = (datetime.now() - inicio).total_seconds()
            return {
                "ok": True,
                "codigo": codigo,
                "base": item["nome"],
                "executor_usado": origem,
                "linhas": int(len(df)),
                "colunas": int(len(df.columns)),
                "colunas_detectadas": list(df.columns),
                "duracao_segundos": round(duracao, 2),
            }
        except Exception as exc:
            return {"ok": False, "codigo": codigo, "base": item["nome"], "erro": str(exc)}

    funcao_testar = item.get("funcao_testar")
    if not funcao_testar:
        return {
            "ok": True,
            "mensagem": "Esta API não possui função específica de teste no legado. Use 'Carregar API' para validar.",
            "codigo": codigo,
            "base": item["nome"],
        }
    try:
        resultado = _chamar_funcao_legada(funcao_testar, item.get("kwargs_testar"))
        duracao = (datetime.now() - inicio).total_seconds()
        saida = dict(resultado) if isinstance(resultado, dict) else {"resultado": str(resultado)}
        saida.setdefault("ok", True)
        saida["codigo"] = codigo
        saida["base"] = item["nome"]
        saida["executor_usado"] = "legacy"
        saida["duracao_segundos"] = round(duracao, 2)
        return saida
    except Exception as exc:
        return {"ok": False, "codigo": codigo, "base": item["nome"], "erro": str(exc)}


def _normalizar_nome_arquivo(texto: str) -> str:
    texto = re.sub(r"[^a-zA-Z0-9_\-]+", "_", texto.strip().lower())
    return re.sub(r"_+", "_", texto).strip("_") or "api"


def _resultado_para_dataframe(resultado: Any) -> pd.DataFrame:
    if isinstance(resultado, pd.DataFrame):
        return resultado.copy()
    if isinstance(resultado, list):
        return pd.DataFrame(resultado)
    if isinstance(resultado, dict):
        return pd.DataFrame([{"resultado_json": json.dumps(resultado, ensure_ascii=False)}])
    return pd.DataFrame([{"resultado": str(resultado)}])


def salvar_dataframe_api(df: pd.DataFrame, codigo: str, camada: str = "raw") -> Path:
    pasta_base = RAW_DIR if camada == "raw" else PROCESSED_DIR
    pasta = pasta_base / "apis"
    pasta.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome = f"{_normalizar_nome_arquivo(codigo)}_{timestamp}.csv"
    caminho = pasta / nome
    df.to_csv(caminho, index=False, encoding="utf-8-sig")
    return caminho


def registrar_importacao_api(codigo: str, tipo_base: str | None, df: pd.DataFrame, caminho: Path, status: str = "SUCESSO", mensagem: str = "") -> int:
    agora = datetime.now().isoformat(timespec="seconds")
    with db_session() as conn:
        cur = conn.execute(
            """
            INSERT INTO importacoes
                (fonte_codigo, nome_arquivo, tipo_base, competencia, linhas, colunas, status, mensagem, caminho_arquivo, criado_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                codigo,
                caminho.name,
                tipo_base or "api_referencia",
                "",
                int(len(df)),
                int(len(df.columns)),
                status,
                mensagem,
                str(caminho),
                agora,
            ),
        )
        return int(cur.lastrowid)


def carregar_api(codigo: str, importar_para_banco: bool = False) -> dict[str, Any]:
    item = obter_api(codigo)
    inicio = datetime.now()
    try:
        resultado, origem = _executar_item_api(item)
        df = _resultado_para_dataframe(resultado)
        caminho = salvar_dataframe_api(df, codigo, camada="raw")
        importacao_id = registrar_importacao_api(codigo, item.get("tipo_base"), df, caminho, mensagem=f"Executor usado: {origem}")
        importacao_estruturada = None

        if importar_para_banco and item.get("tipo_base"):
            importacao_estruturada = importar_dataframe_estruturado(
                df,
                item["tipo_base"],
                importacao_id=importacao_id,
                fonte=codigo.upper(),
            )

        duracao = (datetime.now() - inicio).total_seconds()
        return {
            "ok": True,
            "codigo": codigo,
            "base": item["nome"],
            "executor_usado": origem,
            "linhas": int(len(df)),
            "colunas": int(len(df.columns)),
            "caminho": str(caminho),
            "importacao_id": importacao_id,
            "importacao_estruturada": importacao_estruturada,
            "duracao_segundos": round(duracao, 2),
            "df": df,
        }
    except Exception as exc:
        return {"ok": False, "codigo": codigo, "base": item["nome"], "erro": str(exc)}


def carregar_bloco(codigo_bloco: str, importar_para_banco: bool = False) -> dict[str, Any]:
    bloco = obter_bloco(codigo_bloco)
    resultados = []
    inicio = datetime.now()
    for codigo_api in bloco.get("apis", []):
        resultado = carregar_api(codigo_api, importar_para_banco=importar_para_banco)
        resultados.append({k: v for k, v in resultado.items() if k != "df"})
    sucesso = sum(1 for r in resultados if r.get("ok"))
    falhas = len(resultados) - sucesso
    return {
        "ok": falhas == 0,
        "bloco": bloco["nome"],
        "codigo_bloco": codigo_bloco,
        "total": len(resultados),
        "sucesso": sucesso,
        "falhas": falhas,
        "duracao_segundos": round((datetime.now() - inicio).total_seconds(), 2),
        "resultados": resultados,
    }
