from pathlib import Path
import re

ROOT = Path.cwd()
BACKUP_DIR = ROOT / "_backup_hotfix_v49"
BACKUP_DIR.mkdir(exist_ok=True)
alterados = []
avisos = []

def backup(path: Path):
    rel = path.relative_to(ROOT)
    dest = BACKUP_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(path.read_bytes())

def patch_text_file(rel_path: str, transform):
    path = ROOT / rel_path
    if not path.exists():
        avisos.append(f"Arquivo não encontrado: {rel_path}")
        return
    original = path.read_text(encoding="utf-8", errors="ignore")
    novo = transform(original)
    if novo != original:
        backup(path)
        path.write_text(novo, encoding="utf-8")
        alterados.append(rel_path)
    else:
        avisos.append(f"Sem alteração automática em: {rel_path}")

def patch_app(text: str) -> str:
    novo = text.replace(".style.applymap(", ".style.map(")
    marcador = "# HOTFIX V49 - compatibilidade Streamlit Cloud"
    if marcador not in novo:
        bloco = """
# HOTFIX V49 - compatibilidade Streamlit Cloud
# Garante que o banco principal seja encontrado quando o arquivo foi enviado como .db.
try:
    from pathlib import Path as _APSPath
    _db_sem_ext = _APSPath("data/aps_inteligencia")
    _db_com_ext = _APSPath("data/aps_inteligencia.db")
    if _db_com_ext.exists() and not _db_sem_ext.exists():
        try:
            _db_sem_ext.symlink_to(_db_com_ext.name)
        except Exception:
            try:
                _db_sem_ext.write_bytes(_db_com_ext.read_bytes())
            except Exception:
                pass
except Exception:
    pass
# FIM HOTFIX V49

"""
        m = re.search(r"(import\s+streamlit\s+as\s+st\s*\n)", novo)
        if m:
            novo = novo[:m.end()] + bloco + novo[m.end():]
        else:
            novo = bloco + novo
    return novo

patch_text_file("app.py", patch_app)

def patch_dashboard_executivo(text: str) -> str:
    old = '''mapa = terr[(pd.to_numeric(terr.get("latitude", 0), errors="coerce") != 0) & (pd.to_numeric(terr.get("longitude", 0), errors="coerce") != 0)].copy()'''
    new = '''# HOTFIX V49 - evita KeyError: False quando latitude/longitude não existem no dataframe
    if "latitude" not in terr.columns or "longitude" not in terr.columns:
        mapa = terr.iloc[0:0].copy()
    else:
        _lat = pd.to_numeric(terr["latitude"], errors="coerce")
        _lon = pd.to_numeric(terr["longitude"], errors="coerce")
        mapa = terr[_lat.notna() & _lon.notna() & _lat.ne(0) & _lon.ne(0)].copy()'''
    if old in text:
        return text.replace(old, new)
    pattern = re.compile(r"mapa\s*=\s*terr\s*\[\s*\(\s*pd\.to_numeric\(\s*terr\.get\(\s*['\"]latitude['\"]\s*,\s*0\s*\)\s*,\s*errors\s*=\s*['\"]coerce['\"]\s*\)\s*!=\s*0\s*\)\s*&\s*\(\s*pd\.to_numeric\(\s*terr\.get\(\s*['\"]longitude['\"]\s*,\s*0\s*\)\s*,\s*errors\s*=\s*['\"]coerce['\"]\s*\)\s*!=\s*0\s*\)\s*\]\.copy\(\)", re.MULTILINE)
    return pattern.sub(new, text)

patch_text_file("ui/dashboard_executivo.py", patch_dashboard_executivo)

def patch_dashboards_service(text: str) -> str:
    old = "base.loc[base['pop_por_ubs_ref'].fillna(0).eq(0) & ubs.notna(), 'pop_por_ubs_ref'] = (pop / ubs)"
    new = '''# HOTFIX V49 - pandas cloud: garante dtype float antes de preencher população por UBS
    _calc_pop_por_ubs = (pop / ubs).replace([float("inf"), -float("inf")], pd.NA)
    if "pop_por_ubs_ref" not in base.columns:
        base["pop_por_ubs_ref"] = pd.Series(index=base.index, dtype="float64")
    base["pop_por_ubs_ref"] = pd.to_numeric(base["pop_por_ubs_ref"], errors="coerce").astype("float64")
    _mask_pop_por_ubs = base["pop_por_ubs_ref"].fillna(0).eq(0) & _calc_pop_por_ubs.notna()
    base.loc[_mask_pop_por_ubs, "pop_por_ubs_ref"] = _calc_pop_por_ubs.loc[_mask_pop_por_ubs].astype("float64")'''
    if old in text:
        return text.replace(old, new)
    pattern = re.compile(r"base\.loc\s*\[\s*base\[['\"]pop_por_ubs_ref['\"]\]\.fillna\(0\)\.eq\(0\)\s*&\s*ubs\.notna\(\)\s*,\s*['\"]pop_por_ubs_ref['\"]\s*\]\s*=\s*\(\s*pop\s*/\s*ubs\s*\)", re.MULTILINE)
    return pattern.sub(new, text)

patch_text_file("services/dashboards_relatorios_service.py", patch_dashboards_service)

req = ROOT / "requirements.txt"
if req.exists():
    backup(req)
req.write_text("""streamlit==1.37.1
pandas==2.2.2
numpy==1.26.4
plotly==5.24.1
folium==0.17.0
streamlit-folium==0.22.0
requests==2.32.3
openpyxl==3.1.5
xlrd==2.0.1
sqlalchemy==2.0.32
""", encoding="utf-8")
alterados.append("requirements.txt")

if not (ROOT / "data" / "aps_inteligencia").exists() and not (ROOT / "data" / "aps_inteligencia.db").exists():
    avisos.append("Banco principal não encontrado em data/aps_inteligencia nem data/aps_inteligencia.db")

print("\nHOTFIX V49 concluído.")
print("Arquivos alterados:")
for a in alterados:
    print(f" - {a}")
if avisos:
    print("\nAvisos:")
    for a in avisos:
        print(f" - {a}")
print("\nBackups salvos em:", BACKUP_DIR)
print("\nPróximo passo: commit/push no GitHub e redeploy no Streamlit Cloud usando Python 3.11 ou 3.12.")
