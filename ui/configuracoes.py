import streamlit as st
from components.ui_elements import render_html_table
from database.queries import read_table
from config.parametros import TIPOS_EQUIPE_CNES, COLUNAS_SUGERIDAS_UPLOAD

def render():
    st.subheader('Configurações e dicionário de dados')
    st.markdown('### Códigos CNES priorizados')
    render_html_table([{'codigo': k, 'descricao': v} for k, v in TIPOS_EQUIPE_CNES.items()])
    st.markdown('### Modelos mínimos de planilhas')
    for nome, cols in COLUNAS_SUGERIDAS_UPLOAD.items():
        with st.expander(nome):
            st.code(', '.join(cols))
    st.markdown('### Municípios')
    render_html_table(read_table('municipios'))
