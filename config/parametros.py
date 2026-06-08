TIPOS_EQUIPE_CNES = {
    "70": "Equipe de Saúde da Família — eSF",
    "71": "Equipe de Saúde Bucal — eSB",
    "72": "Equipe do Núcleo Ampliado de Saúde da Família e Atenção Primária — eNASF-AP",
    "73": "Equipe dos Consultórios na Rua — eCR",
    "74": "Equipe de Atenção Primária Prisional — eAPP",
    "76": "Equipe de Atenção Primária — eAP",
}

COLUNAS_SUGERIDAS_UPLOAD = {
    "estabelecimentos": ["municipio", "cnes", "nome_unidade", "tipo_unidade", "latitude", "longitude"],
    "equipes": ["municipio", "cnes", "ine", "codigo_tipo_equipe", "tipo_equipe", "carga_horaria"],
    "profissionais": ["municipio", "cnes", "ine", "codigo_tipo_equipe", "cbo", "nome_profissional", "carga_horaria"],
    "populacao": ["municipio", "ano", "populacao"],
    "vulnerabilidade": ["municipio", "ano", "indicador", "valor"],
}
