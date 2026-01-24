import streamlit as st
import PyPDF2
import re
import google.generativeai as genai
from supabase import create_client
from datetime import datetime

# ================= CONFIG =================
st.set_page_config(page_title="🤖 Chat Técnico de Elevadores", layout="wide")
st.title("🤖 Chat Técnico de Elevadores")

# Configure sua API KEY do Gemini
genai.configure(api_key=st.secrets.get("GEMINI_API_KEY", "SUA_API_KEY_AQUI"))
model = genai.GenerativeModel("gemini-2.5-flash-lite")

# Supabase
supabase = create_client(
    st.secrets["SUPABASE_URL"],
    st.secrets["SUPABASE_ANON_KEY"]
)

# ================= AUTENTICAÇÃO =================
if "user" not in st.session_state or st.session_state.user is None:
    st.error("Acesso negado. Volte e faça login.")
    st.stop()

# ================= FUNÇÕES =================
def extrair_texto_pdf(file):
    reader = PyPDF2.PdfReader(file)
    paginas = []
    for i, p in enumerate(reader.pages):
        texto = p.extract_text()
        if texto:
            paginas.append({"pagina": i + 1, "texto": texto})
    return paginas

# Verifica limite de uso por usuário
def check_usage_limit_for_user():
    user = st.session_state.get("user")

    if not user or not getattr(user, "id", None):
        st.error("🚫 Não foi possível identificar o usuário.")
        st.stop()

    response = supabase.rpc(
        "check_usage_limit_user",
        {"p_user_uuid": user.id}
    ).execute()

    result = response.data

    # DEBUG
    #st.write("DEBUG check_usage:", result)

    # CASO REAL: RPC retorna BOOLEAN
    if isinstance(result, bool):
        return result

    # fallback se virar tabela no futuro
    if isinstance(result, list) and len(result) > 0:
        return bool(list(result[0].values())[0])

    return True




# Incrementa uso por usuário
def increment_usage_for_user():
    user = st.session_state.get("user")

    if not user or not getattr(user, "id", None):
        return  # não quebra o app

    supabase.rpc(
        "increment_usage_user",
        {"p_user_uuid": user.id}
    ).execute()

# Divide texto do PDF em blocos
def dividir_em_blocos_paginas(paginas, tamanho=1200, overlap=200):
    blocos = []
    for p in paginas:
        texto = p["texto"]
        i = 0
        while i < len(texto):
            fim = i + tamanho
            blocos.append({
                "pagina": p["pagina"],
                "texto": texto[i:fim]
            })
            i = fim - overlap
    return blocos

# Busca blocos relevantes para a pergunta
def buscar_blocos_relevantes(pergunta, blocos, top_k=4):
    palavras = set(re.findall(r"\w+", pergunta.lower()))
    scores = []

    for bloco in blocos:
        score = sum(1 for p in palavras if p in bloco["texto"].lower())
        if score > 0:
            scores.append((score, bloco))

    scores.sort(key=lambda x: x[0], reverse=True)
    return [b for _, b in scores[:top_k]]

# ================= SESSION =================
if "blocos" not in st.session_state:
    st.session_state["blocos"] = []
if "historico" not in st.session_state:
    st.session_state["historico"] = []

# ================= SIDEBAR =================
st.sidebar.header("📄 Enviar Manuais PDF")

pdfs = st.sidebar.file_uploader(
    "Manuais técnicos",
    type="pdf",
    accept_multiple_files=True
)

if pdfs:
    with st.spinner("Processando manuais..."):
        todas_paginas = []
        for pdf in pdfs:
            todas_paginas.extend(extrair_texto_pdf(pdf))
        st.session_state["blocos"] = dividir_em_blocos_paginas(todas_paginas)
    st.sidebar.success(f"{len(st.session_state['blocos'])} blocos indexados")

if not st.session_state["blocos"]:
    st.info("Envie um ou mais manuais PDF para começar.")
    st.stop()

# ================= CHAT =================
for msg in st.session_state["historico"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

pergunta = st.chat_input("Pergunte algo relevante")

if pergunta:
    # Adiciona pergunta do usuário ao histórico
    st.session_state["historico"].append({"role": "user", "content": pergunta})
    with st.chat_message("user"):
        st.markdown(pergunta)

    # ====== VERIFICA E INCREMENTA USO ======
    if not check_usage_limit_for_user():
        st.error("🚫 Limite mensal de uso da IA atingido. Entre em contato com sua empresa.")
        st.stop()

    increment_usage_for_user()
    # =====================================

    # Buscar blocos relevantes no PDF
    blocos = buscar_blocos_relevantes(pergunta, st.session_state["blocos"])

    contexto = ""
    paginas_usadas = set()
    for b in blocos:
        contexto += f"\n[Página {b['pagina']}]\n{b['texto']}\n"
        paginas_usadas.add(b['pagina'])

    prompt = f"""
Você é um técnico especialista em elevadores.nao conte historias e nem piadas.

Use o manual como referência, mas responda como um humano experiente:
- Explique procedimentos passo a passo quando perguntarem "como"
- Interprete códigos de falha
- Falhas descritas 0X-XX ou 0XXX, procure sempre no manual, e seja bem didático.
- Use prática técnica comum quando o manual não for explícito
- Avise quando o procedimento variar por fabricante ou modelo
- NÃO copie tabelas literalmente
- NÃO diga "informação não encontrada" se for possível inferir
- NÃO DIGA PARA PROCURAR TECNICO MAIS EXPERIENTE
- Caso não haja manual especificado,responda sempre: "Não posso fornecer outros detalhes"

MANUAL (referência):
{contexto}

PERGUNTA DO TÉCNICO:
{pergunta}

RESPOSTA TÉCNICA CLARA E HUMANA:
"""

    with st.spinner("Consultando o especialista..."):
        resposta = model.generate_content(prompt).text

    rodape = f"\n\n📄 Páginas consultadas: {', '.join(map(str, sorted(paginas_usadas)))}"
    resposta_final = resposta.strip() + rodape

    st.session_state["historico"].append({"role": "assistant", "content": resposta_final})
    with st.chat_message("assistant"):
        st.markdown(resposta_final)
