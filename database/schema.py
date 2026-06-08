from datetime import datetime
from database.connection import db_session
from config.municipios_mt import DEFAULT_MUNICIPIOS
from config.fontes_dados import FONTES_DADOS_PADRAO
from config.ibge_estimativas_2025_mt import CODIGO_IBGE_2025_MT_POR_MUNICIPIO
import unicodedata


def _chave_municipio(valor) -> str:
    texto = str(valor or "").strip().upper()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    texto = " ".join(texto.split())
    return texto


def init_db():
    with db_session() as conn:
        cur = conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS municipios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_ibge TEXT,
                municipio TEXT NOT NULL UNIQUE,
                regiao_saude TEXT,
                escritorio_regional TEXT,
                porte TEXT,
                latitude REAL,
                longitude REAL,
                atualizado_em TEXT
            );

            CREATE TABLE IF NOT EXISTS fontes_dados (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo TEXT NOT NULL UNIQUE,
                nome TEXT NOT NULL,
                tipo TEXT,
                finalidade TEXT,
                status TEXT,
                atualizado_em TEXT
            );

            CREATE TABLE IF NOT EXISTS importacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fonte_codigo TEXT,
                nome_arquivo TEXT,
                tipo_base TEXT,
                competencia TEXT,
                linhas INTEGER DEFAULT 0,
                colunas INTEGER DEFAULT 0,
                status TEXT,
                mensagem TEXT,
                caminho_arquivo TEXT,
                criado_em TEXT
            );

            CREATE TABLE IF NOT EXISTS base_municipal_consolidada (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_ibge TEXT,
                municipio TEXT NOT NULL UNIQUE,
                regiao_saude TEXT,
                populacao INTEGER,
                area_km2 REAL,
                densidade_hab_km2 REAL,
                total_ubs INTEGER DEFAULT 0,
                total_equipes_aps INTEGER DEFAULT 0,
                total_profissionais_aps INTEGER DEFAULT 0,
                total_equipes_70 INTEGER DEFAULT 0,
                total_equipes_71 INTEGER DEFAULT 0,
                total_equipes_72 INTEGER DEFAULT 0,
                total_equipes_73 INTEGER DEFAULT 0,
                total_equipes_74 INTEGER DEFAULT 0,
                total_equipes_76 INTEGER DEFAULT 0,
                indice_vulnerabilidade REAL,
                perfil_urbano_rural REAL,
                indicador_demografico REAL,
                taxa_alfabetizacao REAL,
                nivel_instrucao REAL,
                renda_censo_2022 REAL,
                saneamento_censo_2022 REAL,
                pib_municipal_precos_correntes REAL,
                pib_per_capita REAL,
                populacao_indigena REAL,
                populacao_quilombola REAL,
                total_leitos_sus REAL,
                nascidos_vivos REAL,
                obitos REAL,
                obitos_infantis REAL,
                cadunico_familias REAL,
                cadunico_pessoas REAL,
                cadunico_familias_pobreza REAL,
                cadunico_pessoas_pobreza REAL,
                cadunico_familias_extrema_pobreza REAL,
                cadunico_pessoas_extrema_pobreza REAL,
                bolsa_familia_familias REAL,
                bolsa_familia_valor_repassado REAL,
                bpc_total REAL,
                bpc_idoso REAL,
                bpc_pcd REAL,
                escolas_total REAL,
                escolas_urbanas REAL,
                escolas_rurais REAL,
                escolas_indigenas REAL,
                escolas_quilombolas REAL,
                escolas_educacao_especial_aee REAL,
                matriculas_total REAL,
                matriculas_educacao_especial REAL,
                latitude REAL,
                longitude REAL,
                nivel_prioridade TEXT,
                observacao TEXT,
                atualizado_em TEXT
            );

            CREATE TABLE IF NOT EXISTS estabelecimentos_saude (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                municipio TEXT,
                codigo_ibge TEXT,
                cnes TEXT,
                nome_unidade TEXT,
                tipo_unidade TEXT,
                endereco TEXT,
                latitude REAL,
                longitude REAL,
                fonte TEXT,
                importacao_id INTEGER,
                atualizado_em TEXT
            );

            CREATE TABLE IF NOT EXISTS equipes_aps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                municipio TEXT,
                codigo_ibge TEXT,
                cnes TEXT,
                ine TEXT,
                codigo_tipo_equipe TEXT,
                tipo_equipe TEXT,
                carga_horaria REAL,
                fonte TEXT,
                importacao_id INTEGER,
                atualizado_em TEXT
            );

            CREATE TABLE IF NOT EXISTS profissionais_cnes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                municipio TEXT,
                codigo_ibge TEXT,
                cnes TEXT,
                ine TEXT,
                codigo_tipo_equipe TEXT,
                tipo_equipe TEXT,
                cbo TEXT,
                nome_profissional TEXT,
                carga_horaria REAL,
                fonte TEXT,
                importacao_id INTEGER,
                atualizado_em TEXT
            );

            CREATE TABLE IF NOT EXISTS indicadores_municipais (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                municipio TEXT,
                ano INTEGER,
                competencia TEXT,
                indicador TEXT,
                valor REAL,
                fonte TEXT,
                importacao_id INTEGER,
                atualizado_em TEXT
            );



            CREATE TABLE IF NOT EXISTS cnes_estabelecimentos_gerais (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_ibge TEXT,
                municipio TEXT,
                cnes TEXT,
                nome_unidade TEXT,
                razao_social TEXT,
                tipo_unidade TEXT,
                natureza_juridica TEXT,
                gestao TEXT,
                atende_sus TEXT,
                endereco TEXT,
                bairro TEXT,
                cep TEXT,
                telefone TEXT,
                latitude REAL,
                longitude REAL,
                fonte_url TEXT,
                fonte TEXT,
                importacao_id INTEGER,
                atualizado_em TEXT
            );




            CREATE TABLE IF NOT EXISTS dados_abertos_mt_catalogo (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id TEXT,
                dataset_nome TEXT,
                dataset_titulo TEXT,
                dataset_descricao TEXT,
                organizacao_id TEXT,
                organizacao_nome TEXT,
                grupos TEXT,
                tags TEXT,
                recurso_id TEXT,
                recurso_nome TEXT,
                formato TEXT,
                url TEXT,
                mimetype TEXT,
                ultima_modificacao TEXT,
                criado_em TEXT,
                api_ckan_package_show TEXT,
                url_dataset_portal TEXT,
                pontuacao_aps REAL,
                relevancia_aps TEXT,
                fonte_consulta TEXT,
                fonte TEXT,
                importacao_id INTEGER,
                atualizado_em TEXT
            );

            CREATE TABLE IF NOT EXISTS dados_mt_icqv_explorador (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                produto TEXT,
                origem_pagina TEXT,
                titulo_contexto TEXT,
                descricao_contexto TEXT,
                url_powerbi TEXT,
                chave_publicacao TEXT,
                tenant_id TEXT,
                status_http INTEGER,
                content_type TEXT,
                endpoint_dados_detectado TEXT,
                endpoints_candidatos TEXT,
                observacao TEXT,
                fonte TEXT,
                importacao_id INTEGER,
                atualizado_em TEXT
            );


            CREATE TABLE IF NOT EXISTS dados_mt_assentamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome_assentamento TEXT,
                municipio TEXT,
                codigo_ibge TEXT,
                area_ha REAL,
                modalidade TEXT,
                situacao TEXT,
                latitude_centroide REAL,
                longitude_centroide REAL,
                min_latitude REAL,
                max_latitude REAL,
                min_longitude REAL,
                max_longitude REAL,
                fonte_url TEXT,
                arquivo_origem TEXT,
                observacao TEXT,
                atributos_json TEXT,
                fonte TEXT,
                importacao_id INTEGER,
                atualizado_em TEXT
            );

            CREATE TABLE IF NOT EXISTS dados_mt_terras_indigenas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome_terra_indigena TEXT,
                etnia TEXT,
                municipio TEXT,
                codigo_ibge TEXT,
                municipios_intersectados TEXT,
                area_ha REAL,
                situacao TEXT,
                latitude_centroide REAL,
                longitude_centroide REAL,
                min_latitude REAL,
                max_latitude REAL,
                min_longitude REAL,
                max_longitude REAL,
                fonte_url TEXT,
                arquivo_origem TEXT,
                observacao TEXT,
                atributos_json TEXT,
                fonte TEXT,
                importacao_id INTEGER,
                atualizado_em TEXT
            );


            CREATE TABLE IF NOT EXISTS dados_mt_areas_contaminadas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                municipio TEXT,
                codigo_ibge TEXT,
                data_ocorrencia TEXT,
                ano INTEGER,
                tipo_ocorrencia TEXT,
                produto_residuo TEXT,
                situacao TEXT,
                descricao TEXT,
                latitude REAL,
                longitude REAL,
                fonte_url TEXT,
                dataset_titulo TEXT,
                recurso_nome TEXT,
                formato TEXT,
                url_dataset_portal TEXT,
                observacao TEXT,
                atributos_json TEXT,
                fonte TEXT,
                importacao_id INTEGER,
                atualizado_em TEXT
            );


            CREATE TABLE IF NOT EXISTS dados_mt_compensacao_ambiental (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                municipio TEXT,
                codigo_ibge TEXT,
                processo TEXT,
                empreendedor TEXT,
                empreendimento TEXT,
                tipo_compensacao TEXT,
                valor REAL,
                situacao TEXT,
                ano INTEGER,
                descricao TEXT,
                latitude REAL,
                longitude REAL,
                fonte_url TEXT,
                dataset_titulo TEXT,
                recurso_nome TEXT,
                formato TEXT,
                url_dataset_portal TEXT,
                observacao TEXT,
                atributos_json TEXT,
                fonte TEXT,
                importacao_id INTEGER,
                atualizado_em TEXT
            );


            CREATE TABLE IF NOT EXISTS malhas_geograficas_municipais (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_ibge TEXT,
                municipio TEXT,
                nivel_geografico TEXT,
                latitude_centroide REAL,
                longitude_centroide REAL,
                min_latitude REAL,
                max_latitude REAL,
                min_longitude REAL,
                max_longitude REAL,
                quantidade_pontos INTEGER,
                geometry_json TEXT,
                fonte_url TEXT,
                fonte TEXT,
                importacao_id INTEGER,
                atualizado_em TEXT
            );
            """
        )
        _migrar_colunas(conn)
        _seed_municipios(conn)
        _limpar_municipios_fora_mt(conn)
        _seed_fontes(conn)


def _colunas_tabela(conn, tabela: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({tabela})").fetchall()}


def _adicionar_coluna(conn, tabela: str, coluna: str, tipo_sql: str):
    if coluna not in _colunas_tabela(conn, tabela):
        conn.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo_sql}")


def _migrar_colunas(conn):
    """Pequenas migrações defensivas para bancos criados nas versões v01-v04."""
    _adicionar_coluna(conn, "base_municipal_consolidada", "codigo_ibge", "TEXT")
    _adicionar_coluna(conn, "base_municipal_consolidada", "area_km2", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "densidade_hab_km2", "REAL")
    _adicionar_coluna(conn, "municipios", "codigo_ibge", "TEXT")
    _adicionar_coluna(conn, "estabelecimentos_saude", "codigo_ibge", "TEXT")
    _adicionar_coluna(conn, "equipes_aps", "codigo_ibge", "TEXT")
    _adicionar_coluna(conn, "profissionais_cnes", "codigo_ibge", "TEXT")

    for coluna, tipo_sql in [
        ("codigo_ibge", "TEXT"),
        ("municipio", "TEXT"),
        ("cnes", "TEXT"),
        ("nome_unidade", "TEXT"),
        ("razao_social", "TEXT"),
        ("tipo_unidade", "TEXT"),
        ("natureza_juridica", "TEXT"),
        ("gestao", "TEXT"),
        ("atende_sus", "TEXT"),
        ("endereco", "TEXT"),
        ("bairro", "TEXT"),
        ("cep", "TEXT"),
        ("telefone", "TEXT"),
        ("latitude", "REAL"),
        ("longitude", "REAL"),
        ("fonte_url", "TEXT"),
        ("fonte", "TEXT"),
        ("importacao_id", "INTEGER"),
        ("atualizado_em", "TEXT"),
    ]:
        _adicionar_coluna(conn, "cnes_estabelecimentos_gerais", coluna, tipo_sql)


    for coluna, tipo_sql in [
        ("dataset_id", "TEXT"),
        ("dataset_nome", "TEXT"),
        ("dataset_titulo", "TEXT"),
        ("dataset_descricao", "TEXT"),
        ("organizacao_id", "TEXT"),
        ("organizacao_nome", "TEXT"),
        ("grupos", "TEXT"),
        ("tags", "TEXT"),
        ("recurso_id", "TEXT"),
        ("recurso_nome", "TEXT"),
        ("formato", "TEXT"),
        ("url", "TEXT"),
        ("mimetype", "TEXT"),
        ("ultima_modificacao", "TEXT"),
        ("criado_em", "TEXT"),
        ("api_ckan_package_show", "TEXT"),
        ("url_dataset_portal", "TEXT"),
        ("pontuacao_aps", "REAL"),
        ("relevancia_aps", "TEXT"),
        ("fonte_consulta", "TEXT"),
        ("fonte", "TEXT"),
        ("importacao_id", "INTEGER"),
        ("atualizado_em", "TEXT"),
    ]:
        _adicionar_coluna(conn, "dados_abertos_mt_catalogo", coluna, tipo_sql)

    for coluna, tipo_sql in [
        ("produto", "TEXT"),
        ("origem_pagina", "TEXT"),
        ("titulo_contexto", "TEXT"),
        ("descricao_contexto", "TEXT"),
        ("url_powerbi", "TEXT"),
        ("chave_publicacao", "TEXT"),
        ("tenant_id", "TEXT"),
        ("status_http", "INTEGER"),
        ("content_type", "TEXT"),
        ("endpoint_dados_detectado", "TEXT"),
        ("endpoints_candidatos", "TEXT"),
        ("observacao", "TEXT"),
        ("fonte", "TEXT"),
        ("importacao_id", "INTEGER"),
        ("atualizado_em", "TEXT"),
    ]:
        _adicionar_coluna(conn, "dados_mt_icqv_explorador", coluna, tipo_sql)

    for coluna, tipo_sql in [
        ("nome_assentamento", "TEXT"),
        ("municipio", "TEXT"),
        ("codigo_ibge", "TEXT"),
        ("area_ha", "REAL"),
        ("modalidade", "TEXT"),
        ("situacao", "TEXT"),
        ("latitude_centroide", "REAL"),
        ("longitude_centroide", "REAL"),
        ("min_latitude", "REAL"),
        ("max_latitude", "REAL"),
        ("min_longitude", "REAL"),
        ("max_longitude", "REAL"),
        ("fonte_url", "TEXT"),
        ("arquivo_origem", "TEXT"),
        ("observacao", "TEXT"),
        ("atributos_json", "TEXT"),
        ("fonte", "TEXT"),
        ("importacao_id", "INTEGER"),
        ("atualizado_em", "TEXT"),
    ]:
        _adicionar_coluna(conn, "dados_mt_assentamentos", coluna, tipo_sql)


    for coluna, tipo_sql in [
        ("nome_terra_indigena", "TEXT"),
        ("etnia", "TEXT"),
        ("municipio", "TEXT"),
        ("codigo_ibge", "TEXT"),
        ("municipios_intersectados", "TEXT"),
        ("area_ha", "REAL"),
        ("situacao", "TEXT"),
        ("latitude_centroide", "REAL"),
        ("longitude_centroide", "REAL"),
        ("min_latitude", "REAL"),
        ("max_latitude", "REAL"),
        ("min_longitude", "REAL"),
        ("max_longitude", "REAL"),
        ("fonte_url", "TEXT"),
        ("arquivo_origem", "TEXT"),
        ("observacao", "TEXT"),
        ("atributos_json", "TEXT"),
        ("fonte", "TEXT"),
        ("importacao_id", "INTEGER"),
        ("atualizado_em", "TEXT"),
    ]:
        _adicionar_coluna(conn, "dados_mt_terras_indigenas", coluna, tipo_sql)

    _adicionar_coluna(conn, "base_municipal_consolidada", "perfil_urbano_rural", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "indicador_demografico", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "taxa_alfabetizacao", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "nivel_instrucao", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "renda_censo_2022", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "saneamento_censo_2022", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "pib_municipal_precos_correntes", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "pib_per_capita", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "populacao_indigena", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "populacao_quilombola", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "total_leitos_sus", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "nascidos_vivos", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "obitos", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "obitos_infantis", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "cadunico_familias", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "cadunico_pessoas", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "cadunico_familias_pobreza", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "cadunico_pessoas_pobreza", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "cadunico_familias_extrema_pobreza", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "cadunico_pessoas_extrema_pobreza", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "bolsa_familia_familias", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "bolsa_familia_valor_repassado", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "bpc_total", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "bpc_idoso", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "bpc_pcd", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "escolas_total", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "escolas_urbanas", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "escolas_rurais", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "escolas_indigenas", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "escolas_quilombolas", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "escolas_educacao_especial_aee", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "matriculas_total", "REAL")
    _adicionar_coluna(conn, "base_municipal_consolidada", "matriculas_educacao_especial", "REAL")


    for coluna, tipo_sql in {
        "municipio": "TEXT",
        "codigo_ibge": "TEXT",
        "data_ocorrencia": "TEXT",
        "ano": "INTEGER",
        "tipo_ocorrencia": "TEXT",
        "produto_residuo": "TEXT",
        "situacao": "TEXT",
        "descricao": "TEXT",
        "latitude": "REAL",
        "longitude": "REAL",
        "fonte_url": "TEXT",
        "dataset_titulo": "TEXT",
        "recurso_nome": "TEXT",
        "formato": "TEXT",
        "url_dataset_portal": "TEXT",
        "observacao": "TEXT",
        "atributos_json": "TEXT",
        "fonte": "TEXT",
        "importacao_id": "INTEGER",
        "atualizado_em": "TEXT",
    }.items():
        _adicionar_coluna(conn, "dados_mt_areas_contaminadas", coluna, tipo_sql)

    for coluna, tipo_sql in {
        "municipio": "TEXT",
        "codigo_ibge": "TEXT",
        "processo": "TEXT",
        "empreendedor": "TEXT",
        "empreendimento": "TEXT",
        "tipo_compensacao": "TEXT",
        "valor": "REAL",
        "situacao": "TEXT",
        "ano": "INTEGER",
        "descricao": "TEXT",
        "latitude": "REAL",
        "longitude": "REAL",
        "fonte_url": "TEXT",
        "dataset_titulo": "TEXT",
        "recurso_nome": "TEXT",
        "formato": "TEXT",
        "url_dataset_portal": "TEXT",
        "observacao": "TEXT",
        "atributos_json": "TEXT",
        "fonte": "TEXT",
        "importacao_id": "INTEGER",
        "atualizado_em": "TEXT",
    }.items():
        _adicionar_coluna(conn, "dados_mt_compensacao_ambiental", coluna, tipo_sql)

def _seed_municipios(conn):
    agora = datetime.now().isoformat(timespec="seconds")
    for item in DEFAULT_MUNICIPIOS:
        municipio = item.get("municipio")
        codigo = CODIGO_IBGE_2025_MT_POR_MUNICIPIO.get(municipio)
        conn.execute(
            """
            INSERT OR IGNORE INTO municipios (codigo_ibge, municipio, regiao_saude, escritorio_regional, porte, atualizado_em)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (codigo, municipio, item.get("regiao_saude"), item.get("escritorio_regional"), item.get("porte", ""), agora),
        )
        # Também atualiza registros que já existiam antes do patch, inclusive
        # código IBGE e região de saúde. Mantém latitude/longitude se já foram
        # carregadas pela camada de georreferenciamento.
        conn.execute(
            """
            UPDATE municipios
               SET codigo_ibge = COALESCE(?, codigo_ibge),
                   regiao_saude = ?,
                   escritorio_regional = ?,
                   porte = ?,
                   atualizado_em = ?
             WHERE municipio = ?
            """,
            (codigo, item.get("regiao_saude"), item.get("escritorio_regional"), item.get("porte", ""), agora, municipio),
        )


def _limpar_municipios_fora_mt(conn):
    """Remove registros territoriais indevidos trazidos por APIs agregadas.

    A plataforma é estadual/MT. O cadastro oficial de municípios deve permanecer
    restrito aos municípios oficiais do Estado.
    """
    oficiais = {_chave_municipio(item.get("municipio")) for item in DEFAULT_MUNICIPIOS}
    rows = conn.execute("SELECT id, municipio FROM municipios").fetchall()
    for row in rows:
        municipio = row[1]
        if _chave_municipio(municipio) not in oficiais:
            conn.execute("DELETE FROM municipios WHERE id = ?", (row[0],))


def _seed_fontes(conn):
    agora = datetime.now().isoformat(timespec="seconds")
    for fonte in FONTES_DADOS_PADRAO:
        conn.execute(
            """
            INSERT OR IGNORE INTO fontes_dados (codigo, nome, tipo, finalidade, status, atualizado_em)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (fonte["codigo"], fonte["nome"], fonte["tipo"], fonte["finalidade"], fonte["status"], agora),
        )
