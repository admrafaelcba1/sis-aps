from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "data" / "aps_inteligencia.db"


def _conectar():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def garantir_tabela_auditoria() -> None:
    with _conectar() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS auditoria_sistema (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data_hora TEXT NOT NULL,
                usuario_login TEXT,
                usuario_nome TEXT,
                perfil TEXT,
                modulo TEXT,
                acao TEXT NOT NULL,
                tabela_afetada TEXT,
                registro_id TEXT,
                campo_alterado TEXT,
                valor_anterior TEXT,
                valor_novo TEXT,
                justificativa TEXT,
                status TEXT DEFAULT 'registrado',
                detalhes TEXT
            )
            """
        )
        con.commit()


def _serializar(valor: Any) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, str):
        return valor[:5000]
    try:
        return json.dumps(valor, ensure_ascii=False, default=str)[:5000]
    except Exception:
        return str(valor)[:5000]


def registrar_evento(
    *,
    usuario_login: str | None = None,
    usuario_nome: str | None = None,
    perfil: str | None = None,
    modulo: str | None = None,
    acao: str,
    tabela_afetada: str | None = None,
    registro_id: str | None = None,
    campo_alterado: str | None = None,
    valor_anterior: Any = None,
    valor_novo: Any = None,
    justificativa: str | None = None,
    status: str = "registrado",
    detalhes: Any = None,
) -> None:
    try:
        garantir_tabela_auditoria()
        with _conectar() as con:
            con.execute(
                """
                INSERT INTO auditoria_sistema (
                    data_hora, usuario_login, usuario_nome, perfil, modulo, acao,
                    tabela_afetada, registro_id, campo_alterado, valor_anterior,
                    valor_novo, justificativa, status, detalhes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    usuario_login,
                    usuario_nome,
                    perfil,
                    modulo,
                    acao,
                    tabela_afetada,
                    registro_id,
                    campo_alterado,
                    _serializar(valor_anterior),
                    _serializar(valor_novo),
                    justificativa,
                    status,
                    _serializar(detalhes),
                ),
            )
            con.commit()
    except Exception:
        # Auditoria nunca deve derrubar o sistema principal.
        pass


def carregar_auditoria(
    data_inicio: str | None = None,
    data_fim: str | None = None,
    usuario: str | None = None,
    modulo: str | None = None,
    acao: str | None = None,
    status: str | None = None,
    tabela: str | None = None,
    limite: int = 2000,
) -> pd.DataFrame:
    garantir_tabela_auditoria()
    where = []
    params: list[Any] = []
    if data_inicio:
        where.append("date(data_hora) >= date(?)")
        params.append(data_inicio)
    if data_fim:
        where.append("date(data_hora) <= date(?)")
        params.append(data_fim)
    if usuario and usuario != "Todos":
        where.append("usuario_login = ?")
        params.append(usuario)
    if modulo and modulo != "Todos":
        where.append("modulo = ?")
        params.append(modulo)
    if acao and acao != "Todos":
        where.append("acao = ?")
        params.append(acao)
    if status and status != "Todos":
        where.append("status = ?")
        params.append(status)
    if tabela and tabela != "Todas":
        where.append("tabela_afetada = ?")
        params.append(tabela)
    sql = "SELECT * FROM auditoria_sistema"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY data_hora DESC, id DESC LIMIT ?"
    params.append(int(limite or 2000))
    with _conectar() as con:
        return pd.read_sql_query(sql, con, params=params)


def valores_distintos(campo: str) -> list[str]:
    if campo not in {"usuario_login", "modulo", "acao", "status", "tabela_afetada"}:
        return []
    garantir_tabela_auditoria()
    with _conectar() as con:
        rows = con.execute(f'SELECT DISTINCT "{campo}" FROM auditoria_sistema WHERE "{campo}" IS NOT NULL AND "{campo}" <> "" ORDER BY "{campo}"').fetchall()
    return [r[0] for r in rows]


def resumo_auditoria() -> dict[str, int]:
    garantir_tabela_auditoria()
    with _conectar() as con:
        total = con.execute("SELECT COUNT(*) FROM auditoria_sistema").fetchone()[0]
        hoje = con.execute("SELECT COUNT(*) FROM auditoria_sistema WHERE date(data_hora)=date('now','localtime')").fetchone()[0]
        logins = con.execute("SELECT COUNT(*) FROM auditoria_sistema WHERE acao LIKE 'login%'").fetchone()[0]
        falhas = con.execute("SELECT COUNT(*) FROM auditoria_sistema WHERE status='falha'").fetchone()[0]
    return {"eventos_total": int(total), "eventos_hoje": int(hoje), "eventos_login": int(logins), "falhas": int(falhas)}
