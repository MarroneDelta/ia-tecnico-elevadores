import streamlit as st
from supabase import create_client

# Config global (título, ícone, etc.)
st.set_page_config(
    page_title="IA Elevador",
    layout="centered",
    initial_sidebar_state="collapsed"   # ajuda a esconder visualmente no início
)

# Supabase (igual você tinha)
supabase = create_client(
    st.secrets["SUPABASE_URL"],
    st.secrets["SUPABASE_ANON_KEY"]
)

# Inicializa session state
if "user" not in st.session_state:
    st.session_state.user = None
if "access_token" not in st.session_state:
    st.session_state.access_token = None

# Páginas como objetos st.Page
login_page = st.Page(
    "home.py",
    title="Login Técnico",
    icon="🔐",
    default=(st.session_state.user is None)   # ← abre login por padrão se não logado
)

elevador_page = st.Page(
    "pages/app.py",          # ou "pages/app.py" se manteve o nome
    title="IA do Elevador",
    icon="🤖"
)

# Navegação CONDICIONAL
if st.session_state.user is not None:
    # Usuário logado → só mostra a página do elevador
    pg = st.navigation([elevador_page])
else:
    # Não logado → só mostra login (sem outras páginas)
    pg = st.navigation([login_page])

# Executa a página selecionada
pg.run()