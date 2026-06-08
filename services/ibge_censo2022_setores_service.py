
from __future__ import annotations

import pandas as pd


ROTEIRO_IBGE_CENSO_2022_SETORES = [
    {
        "ordem": 1,
        "conjunto": "Alfabetização",
        "tipo_descricao_sistema": "IBGE Censo 2022 setores — alfabetização",
        "tabela_destino_sugerida": "base_publica_ibge_setores_alfabetizacao",
        "categoria_principal": "Escolaridade e educação",
        "uso_no_sistema": "Analfabetismo, alfabetização e vulnerabilidade socioeducacional.",
        "prioridade": "Alta",
        "observacao": "Importar cedo porque fortalece diretamente a dimensão socioeducacional.",
    },
    {
        "ordem": 2,
        "conjunto": "Agregados por setores — básico",
        "tipo_descricao_sistema": "IBGE Censo 2022 setores — básico",
        "tabela_destino_sugerida": "base_publica_ibge_setores_basico",
        "categoria_principal": "Base territorial e demográfica",
        "uso_no_sistema": "Estrutura territorial, município, setor, população, domicílios e chaves de integração.",
        "prioridade": "Obrigatória",
        "observacao": "Base-chave para validar CD_MUN/NM_MUN/CD_SETOR e integrar os demais arquivos.",
    },
    {
        "ordem": 3,
        "conjunto": "Características dos domicílios 1",
        "tipo_descricao_sistema": "IBGE Censo 2022 setores — domicílios 1",
        "tabela_destino_sugerida": "base_publica_ibge_setores_domicilios_1",
        "categoria_principal": "Saneamento, domicílios e entorno",
        "uso_no_sistema": "Condições domiciliares e primeira camada de infraestrutura/saneamento.",
        "prioridade": "Alta",
        "observacao": "Usar para fortalecer determinantes sociais e vulnerabilidade territorial.",
    },
    {
        "ordem": 4,
        "conjunto": "Características dos domicílios 2",
        "tipo_descricao_sistema": "IBGE Censo 2022 setores — domicílios 2",
        "tabela_destino_sugerida": "base_publica_ibge_setores_domicilios_2",
        "categoria_principal": "Saneamento, domicílios e entorno",
        "uso_no_sistema": "Complemento das condições domiciliares, saneamento e infraestrutura.",
        "prioridade": "Alta",
        "observacao": "Importar após domicílios 1.",
    },
    {
        "ordem": 5,
        "conjunto": "Características dos domicílios 3",
        "tipo_descricao_sistema": "IBGE Censo 2022 setores — domicílios 3",
        "tabela_destino_sugerida": "base_publica_ibge_setores_domicilios_3",
        "categoria_principal": "Saneamento, domicílios e entorno",
        "uso_no_sistema": "Complemento das condições domiciliares, saneamento e infraestrutura.",
        "prioridade": "Alta",
        "observacao": "Importar após domicílios 1 e 2.",
    },
    {
        "ordem": 6,
        "conjunto": "Cor ou raça",
        "tipo_descricao_sistema": "IBGE Censo 2022 setores — cor ou raça",
        "tabela_destino_sugerida": "base_publica_ibge_setores_cor_raca",
        "categoria_principal": "Demografia e equidade",
        "uso_no_sistema": "Equidade, composição populacional e análise territorial sensível a raça/cor.",
        "prioridade": "Média/Alta",
        "observacao": "Usar com cuidado metodológico e finalidade de equidade em saúde.",
    },
    {
        "ordem": 7,
        "conjunto": "Demografia",
        "tipo_descricao_sistema": "IBGE Censo 2022 setores — demografia",
        "tabela_destino_sugerida": "base_publica_ibge_setores_demografia",
        "categoria_principal": "Demografia e equidade",
        "uso_no_sistema": "Crianças, idosos, sexo, estrutura etária e população prioritária.",
        "prioridade": "Alta",
        "observacao": "Importante para planejar APS por ciclo de vida.",
    },
    {
        "ordem": 8,
        "conjunto": "Domicílios indígenas",
        "tipo_descricao_sistema": "IBGE Censo 2022 setores — domicílios indígenas",
        "tabela_destino_sugerida": "base_publica_ibge_setores_domicilios_indigenas",
        "categoria_principal": "Populações específicas",
        "uso_no_sistema": "Domicílios indígenas e territórios com atenção diferenciada.",
        "prioridade": "Média/Alta",
        "observacao": "Cruzar com vazios assistenciais e territórios especiais.",
    },
    {
        "ordem": 9,
        "conjunto": "Domicílios quilombolas",
        "tipo_descricao_sistema": "IBGE Censo 2022 setores — domicílios quilombolas",
        "tabela_destino_sugerida": "base_publica_ibge_setores_domicilios_quilombolas",
        "categoria_principal": "Populações específicas",
        "uso_no_sistema": "Domicílios quilombolas e territórios com atenção diferenciada.",
        "prioridade": "Média/Alta",
        "observacao": "Cruzar com vazios assistenciais e territórios especiais.",
    },
    {
        "ordem": 10,
        "conjunto": "Óbitos",
        "tipo_descricao_sistema": "IBGE Censo 2022 setores — óbitos",
        "tabela_destino_sugerida": "base_publica_ibge_setores_obitos",
        "categoria_principal": "Epidemiologia — mortalidade",
        "uso_no_sistema": "Complemento territorial de óbitos informados no Censo.",
        "prioridade": "Média",
        "observacao": "Não substitui SIM/DATASUS, mas pode apoiar leitura territorial.",
    },
    {
        "ordem": 11,
        "conjunto": "Parentesco",
        "tipo_descricao_sistema": "IBGE Censo 2022 setores — parentesco",
        "tabela_destino_sugerida": "base_publica_ibge_setores_parentesco",
        "categoria_principal": "Demografia e composição domiciliar",
        "uso_no_sistema": "Composição dos domicílios e estrutura familiar.",
        "prioridade": "Baixa/Média",
        "observacao": "Importar depois das bases prioritárias.",
    },
    {
        "ordem": 12,
        "conjunto": "Pessoas indígenas",
        "tipo_descricao_sistema": "IBGE Censo 2022 setores — pessoas indígenas",
        "tabela_destino_sugerida": "base_publica_ibge_setores_pessoas_indigenas",
        "categoria_principal": "Populações específicas",
        "uso_no_sistema": "População indígena por setor/território.",
        "prioridade": "Média/Alta",
        "observacao": "Importante para equidade territorial e populações específicas.",
    },
    {
        "ordem": 13,
        "conjunto": "Pessoas quilombolas",
        "tipo_descricao_sistema": "IBGE Censo 2022 setores — pessoas quilombolas",
        "tabela_destino_sugerida": "base_publica_ibge_setores_pessoas_quilombolas",
        "categoria_principal": "Populações específicas",
        "uso_no_sistema": "População quilombola por setor/território.",
        "prioridade": "Média/Alta",
        "observacao": "Importante para equidade territorial e populações específicas.",
    },
]


def roteiro_ibge_censo2022_setores() -> pd.DataFrame:
    return pd.DataFrame(ROTEIRO_IBGE_CENSO_2022_SETORES)


def parametros_importacao_ibge_por_ordem(ordem: int) -> dict:
    df = roteiro_ibge_censo2022_setores()
    achado = df[df["ordem"].eq(int(ordem))]
    if achado.empty:
        return {}
    row = achado.iloc[0].to_dict()
    return {
        "eixo": "IBGE",
        "tipo_descricao": row["tipo_descricao_sistema"],
        "ano": "2022",
        "fonte": f"IBGE Censo 2022 — Agregados por Setores Censitários — {row['conjunto']}",
        "tabela_destino": row["tabela_destino_sugerida"],
    }
