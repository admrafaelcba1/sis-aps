from pathlib import Path

APP_NAME = "Plataforma de Inteligência Territorial da APS"
APP_SUBTITLE = "SES/MT — Base de dados, georreferenciamento, CNES, DATASUS, INEP, indicadores derivados e governança da informação."
APP_VERSION = "0.20.0-dadosabertos-ms-hospitais-leitos"

# Mantém compatibilidade com serviços antigos e novos.
# Alguns módulos usam ROOT_DIR; outros usam BASE_DIR.
ROOT_DIR = Path(__file__).resolve().parents[1]
BASE_DIR = ROOT_DIR

DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
UPLOADS_DIR = DATA_DIR / "uploads"
GEO_DIR = DATA_DIR / "geo"
DB_PATH = DATA_DIR / "aps_inteligencia.db"

for path in [
    DATA_DIR,
    RAW_DIR,
    RAW_DIR / "apis",
    RAW_DIR / "inep",
    PROCESSED_DIR,
    UPLOADS_DIR,
    UPLOADS_DIR / "mds",
    UPLOADS_DIR / "cnes_equipes",
    UPLOADS_DIR / "inep",
    UPLOADS_DIR / "hospitais_leitos",
    GEO_DIR,
]:
    path.mkdir(parents=True, exist_ok=True)
