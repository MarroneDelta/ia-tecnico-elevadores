import streamlit as st
from supabase import create_client

# Supabase (pode ficar aqui ou importar de um módulo separado)
supabase = create_client(
    st.secrets["SUPABASE_URL"],
    st.secrets["SUPABASE_ANON_KEY"]
)

st.title("🔐 Login Técnico")

# Form de login
with st.form("login_form",width=500):
    email = st.text_input("Email",placeholder="Email")
    password = st.text_input("Senha", type="password",placeholder='Senha')
    submit = st.form_submit_button("Entrar",type='tertiary')

if submit:
    try:
        auth = supabase.auth.sign_in_with_password({"email": email, "password": password})
        st.session_state.user = auth.user
        st.session_state.access_token = auth.session.access_token
        st.success("Login realizado! Redirecionando...")
        st.rerun()          # ← força o main.py a recarregar e mostrar a página correta
    except Exception as e:
        st.error("Email ou senha inválidos")

# Mensagem inicial (só aparece se não logado)
if st.session_state.user is None:
    st.info("Faça login para acessar o sistema de elevador.")