import pandas as pd
from database.connection import get_connection, db_session


def read_table(table: str) -> pd.DataFrame:
    with get_connection() as conn:
        try:
            return pd.read_sql_query(f"SELECT * FROM {table}", conn)
        except Exception:
            return pd.DataFrame()


def execute_scalar(sql: str, params: tuple = ()):
    with get_connection() as conn:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None


def insert_importacao(**kwargs) -> int:
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join(["?"] * len(kwargs))
    with db_session() as conn:
        cur = conn.execute(f"INSERT INTO importacoes ({cols}) VALUES ({placeholders})", tuple(kwargs.values()))
        return int(cur.lastrowid)


def replace_dataframe(table: str, df: pd.DataFrame):
    with get_connection() as conn:
        df.to_sql(table, conn, if_exists="replace", index=False)
