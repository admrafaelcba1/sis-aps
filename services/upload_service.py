from datetime import datetime
from pathlib import Path
import re
import pandas as pd
from config.settings import UPLOADS_DIR
from database.queries import insert_importacao


def normalizar_coluna(col: str) -> str:
    col = str(col).strip().lower()
    mapa = str.maketrans("áàãâäéèêëíìîïóòõôöúùûüç", "aaaaaeeeeiiiiooooouuuuc")
    col = col.translate(mapa)
    col = re.sub(r"[^a-z0-9]+", "_", col).strip("_")
    return col


def ler_arquivo_upload(uploaded_file) -> dict[str, pd.DataFrame]:
    nome = uploaded_file.name
    suffix = Path(nome).suffix.lower()
    if suffix in [".xlsx", ".xls"]:
        return pd.read_excel(uploaded_file, sheet_name=None)
    if suffix == ".csv":
        return {"csv": pd.read_csv(uploaded_file)}
    raise ValueError("Formato não suportado. Use .xlsx, .xls ou .csv.")


def salvar_upload(uploaded_file, tipo_base: str, competencia: str = "") -> tuple[Path, int]:
    agora = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome_seguro = re.sub(r"[^A-Za-z0-9_.-]+", "_", uploaded_file.name)
    destino = UPLOADS_DIR / f"{agora}_{nome_seguro}"
    destino.write_bytes(uploaded_file.getbuffer())
    importacao_id = insert_importacao(
        fonte_codigo="PLANILHAS_SES",
        nome_arquivo=uploaded_file.name,
        tipo_base=tipo_base,
        competencia=competencia,
        linhas=0,
        colunas=0,
        status="Arquivo salvo",
        mensagem="Upload recebido. Pronto para tratamento.",
        caminho_arquivo=str(destino),
        criado_em=datetime.now().isoformat(timespec="seconds"),
    )
    return destino, importacao_id


def padronizar_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [normalizar_coluna(c) for c in df.columns]
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].astype(str).str.strip()
        df.loc[df[col].isin(["nan", "None", "NaT"]), col] = ""
    return df
