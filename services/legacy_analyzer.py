from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from config.api_catalog import API_CATALOG
from config.settings import ROOT_DIR

LEGACY_DIR = ROOT_DIR / "legacy"

ARQUIVOS_LEGACY_ESPERADOS = [
    "conectores_apis_ubs_antigo.py",
    "cache_dados_aps_antigo.py",
    "georreferenciamento_aps_antigo.py",
    "dashboard_executivo_aps_antigo.py",
    "dashboard_profissionais_aps_antigo.py",
    "diagnostico_ubs_ses_antigo.py",
    "data_municipios_antigo.py",
    "utils_municipios_antigo.py",
]

PALAVRAS_CHAVE_GRUPOS = {
    "IBGE/SIDRA": ["sidra", "ibge", "censo", "municipio", "distrito", "renda", "saneamento", "alfabetizacao", "instrucao", "demograf"],
    "CNES": ["cnes", "estabelecimento", "ubs", "leito", "profissional", "equipe"],
    "DATASUS": ["datasus", "sinasc", "sim", "mortalidade", "nascidos"],
    "INEP": ["inep", "escolar", "educacao", "escola", "matricula"],
    "MDS/Assistência Social": ["mds", "bpc", "bolsa", "familia", "portal_transparencia", "beneficio"],
    "PNI": ["pni", "vacina", "vacinacao", "dose"],
    "Georreferenciamento": ["geo", "mapa", "malha", "latitude", "longitude", "folium", "geojson"],
    "Visualização/UI": ["render", "dashboard", "grafico", "st.", "streamlit"],
}


@dataclass
class FuncaoLegacy:
    arquivo: str
    funcao: str
    linha: int
    argumentos: str
    grupo_sugerido: str
    tipo_sugerido: str
    chamada_por_catalogo: bool
    observacao: str


def _ler_texto(caminho: Path) -> str:
    try:
        return caminho.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _argumentos_funcao(node: ast.FunctionDef) -> str:
    nomes = [arg.arg for arg in node.args.args]
    if node.args.vararg:
        nomes.append("*" + node.args.vararg.arg)
    nomes.extend(["*" + arg.arg for arg in node.args.kwonlyargs])
    if node.args.kwarg:
        nomes.append("**" + node.args.kwarg.arg)
    return ", ".join(nomes)


def _grupo_sugerido(nome_funcao: str, texto_funcao: str) -> str:
    alvo = f"{nome_funcao} {texto_funcao}".lower()
    pontos = []
    for grupo, termos in PALAVRAS_CHAVE_GRUPOS.items():
        score = sum(1 for termo in termos if termo in alvo)
        if score:
            pontos.append((score, grupo))
    if not pontos:
        return "Outros/apoio"
    return sorted(pontos, reverse=True)[0][1]


def _tipo_sugerido(nome_funcao: str) -> str:
    n = nome_funcao.lower()
    if n.startswith("testar_"):
        return "teste"
    if n.startswith("carregar_"):
        return "carga/api"
    if n.startswith("consolidar_"):
        return "consolidação"
    if n.startswith("classificar_"):
        return "classificação/regra"
    if n.startswith("preparar_"):
        return "preparação/exibição"
    if n.startswith("render_"):
        return "interface"
    if n.startswith("_"):
        return "função auxiliar"
    return "apoio"


def _observacao(nome_funcao: str, chamada_por_catalogo: bool) -> str:
    n = nome_funcao.lower()
    if chamada_por_catalogo:
        return "Já mapeada no Catálogo de APIs."
    if n.startswith("carregar_") or n.startswith("testar_"):
        return "Candidata a entrar no Catálogo de APIs."
    if n.startswith("consolidar_"):
        return "Útil para a etapa de base consolidada."
    if n.startswith("classificar_"):
        return "Regra de classificação aproveitável nos indicadores."
    if n.startswith("render_"):
        return "Tela/visual antigo; usar apenas como referência visual."
    if n.startswith("_"):
        return "Função auxiliar; migrar junto com a função principal que depende dela."
    return "Avaliar utilidade durante a migração."


def _catalogo_funcoes() -> set[str]:
    funcoes: set[str] = set()
    for item in API_CATALOG:
        for chave in ("funcao_carregar", "funcao_testar"):
            valor = item.get(chave)
            if valor:
                funcoes.add(str(valor))
    return funcoes


def _extrair_texto_funcao(texto: str, node: ast.FunctionDef) -> str:
    linhas = texto.splitlines()
    inicio = max(node.lineno - 1, 0)
    fim = getattr(node, "end_lineno", None)
    if fim is None:
        fim = min(inicio + 80, len(linhas))
    return "\n".join(linhas[inicio:fim])


def inventariar_funcoes_legacy() -> pd.DataFrame:
    """Gera inventário das funções existentes nos arquivos copiados para legacy/.

    A leitura usa AST e não importa/executa os arquivos antigos. Assim, mesmo que falte
    alguma dependência do sistema antigo, o inventário continua funcionando.
    """
    catalogadas = _catalogo_funcoes()
    registros: list[FuncaoLegacy] = []

    if not LEGACY_DIR.exists():
        return pd.DataFrame(columns=[
            "arquivo", "funcao", "linha", "argumentos", "grupo_sugerido",
            "tipo_sugerido", "chamada_por_catalogo", "observacao"
        ])

    arquivos_py = sorted(LEGACY_DIR.glob("*.py"))
    for caminho in arquivos_py:
        texto = _ler_texto(caminho)
        if not texto.strip():
            continue
        try:
            arvore = ast.parse(texto)
        except SyntaxError:
            # fallback textual simples se houver algum problema de sintaxe no legado
            for match in re.finditer(r"^def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([^)]*)\)", texto, flags=re.MULTILINE):
                nome = match.group(1)
                chamada = nome in catalogadas
                registros.append(FuncaoLegacy(
                    arquivo=caminho.name,
                    funcao=nome,
                    linha=texto[:match.start()].count("\n") + 1,
                    argumentos=match.group(2),
                    grupo_sugerido=_grupo_sugerido(nome, ""),
                    tipo_sugerido=_tipo_sugerido(nome),
                    chamada_por_catalogo=chamada,
                    observacao=_observacao(nome, chamada),
                ))
            continue

        for node in ast.walk(arvore):
            if isinstance(node, ast.FunctionDef):
                nome = node.name
                trecho = _extrair_texto_funcao(texto, node)
                chamada = nome in catalogadas
                registros.append(FuncaoLegacy(
                    arquivo=caminho.name,
                    funcao=nome,
                    linha=int(node.lineno),
                    argumentos=_argumentos_funcao(node),
                    grupo_sugerido=_grupo_sugerido(nome, trecho),
                    tipo_sugerido=_tipo_sugerido(nome),
                    chamada_por_catalogo=chamada,
                    observacao=_observacao(nome, chamada),
                ))

    df = pd.DataFrame([r.__dict__ for r in registros])
    if not df.empty:
        df = df.sort_values(["arquivo", "linha", "funcao"], ignore_index=True)
    return df


def inventariar_arquivos_legacy() -> pd.DataFrame:
    linhas: list[dict[str, Any]] = []
    for nome in ARQUIVOS_LEGACY_ESPERADOS:
        caminho = LEGACY_DIR / nome
        texto = _ler_texto(caminho) if caminho.exists() else ""
        funcoes = len(re.findall(r"^def\s+[a-zA-Z_]", texto, flags=re.MULTILINE)) if texto else 0
        classes = len(re.findall(r"^class\s+[a-zA-Z_]", texto, flags=re.MULTILINE)) if texto else 0
        linhas.append({
            "arquivo": nome,
            "encontrado": caminho.exists(),
            "tamanho_kb": round(caminho.stat().st_size / 1024, 1) if caminho.exists() else 0,
            "linhas_codigo": len(texto.splitlines()) if texto else 0,
            "funcoes": funcoes,
            "classes": classes,
        })
    outros = []
    if LEGACY_DIR.exists():
        esperados = set(ARQUIVOS_LEGACY_ESPERADOS)
        for caminho in sorted(LEGACY_DIR.glob("*.py")):
            if caminho.name not in esperados:
                texto = _ler_texto(caminho)
                outros.append({
                    "arquivo": caminho.name,
                    "encontrado": True,
                    "tamanho_kb": round(caminho.stat().st_size / 1024, 1),
                    "linhas_codigo": len(texto.splitlines()),
                    "funcoes": len(re.findall(r"^def\s+[a-zA-Z_]", texto, flags=re.MULTILINE)),
                    "classes": len(re.findall(r"^class\s+[a-zA-Z_]", texto, flags=re.MULTILINE)),
                })
    return pd.DataFrame(linhas + outros)


def resumo_migracao_legacy() -> dict[str, Any]:
    arquivos = inventariar_arquivos_legacy()
    funcoes = inventariar_funcoes_legacy()
    total_catalogo = len(_catalogo_funcoes())
    catalogadas_encontradas = 0
    if not funcoes.empty:
        catalogadas_encontradas = int(funcoes["chamada_por_catalogo"].sum())
    return {
        "arquivos_encontrados": int(arquivos["encontrado"].sum()) if not arquivos.empty else 0,
        "arquivos_esperados": len(ARQUIVOS_LEGACY_ESPERADOS),
        "total_funcoes": int(len(funcoes)),
        "funcoes_catalogo_total": total_catalogo,
        "funcoes_catalogo_encontradas": catalogadas_encontradas,
        "funcoes_carga_teste": int(funcoes[funcoes["tipo_sugerido"].isin(["carga/api", "teste"])].shape[0]) if not funcoes.empty else 0,
        "funcoes_regras": int(funcoes[funcoes["tipo_sugerido"].eq("classificação/regra")].shape[0]) if not funcoes.empty else 0,
    }


def buscar_funcoes_legacy(termo: str) -> pd.DataFrame:
    df = inventariar_funcoes_legacy()
    if df.empty or not termo.strip():
        return df
    termo = termo.strip().lower()
    mask = df.apply(lambda row: termo in " ".join(map(str, row.values)).lower(), axis=1)
    return df[mask].reset_index(drop=True)
