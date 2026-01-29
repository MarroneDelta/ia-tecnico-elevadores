import streamlit as st
import PyPDF2
import re
import google.generativeai as genai
from supabase import create_client
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging
import hashlib
import uuid

# ================= CONFIGURAÇÃO DE LOGGING =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ================= CONFIGURAÇÃO INICIAL =================
def init_page_config():
    """Configura a página do Streamlit"""
    st.set_page_config(
        page_title="🤖 Chat Técnico de Elevadores",
        layout="wide",
        initial_sidebar_state="expanded"
    )


@st.cache_data
def load_css():
    """Carrega CSS customizado com cache"""
    try:
        with open("style.css", encoding="utf-8") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
    except FileNotFoundError:
        logger.warning("Arquivo style.css não encontrado")


def init_apis():
    """Inicializa APIs do Gemini e Supabase"""
    try:
        genai.configure(api_key=st.secrets.get("GEMINI_API_KEY"))
        model = genai.GenerativeModel("gemini-2.5-flash")
        
        supabase = create_client(
            st.secrets["SUPABASE_URL"],
            st.secrets["SUPABASE_ANON_KEY"]
        )
        
        return model, supabase
    except Exception as e:
        st.error(f"❌ Erro ao inicializar APIs: {str(e)}")
        st.stop()


# ================= AUTENTICAÇÃO =================
def verificar_autenticacao() -> str:
    """Verifica se o usuário está autenticado e retorna o user_id"""
    if "user" not in st.session_state or st.session_state.user is None:
        st.error("🚫 Acesso negado. Volte e faça login.")
        st.stop()
    
    user_id = st.session_state.user.id
    if not user_id:
        st.error("❌ ID de usuário inválido")
        st.stop()
    
    return user_id


# ================= GERENCIAMENTO DE CONVERSAS =================
def gerar_id_conversa(primeira_pergunta: str = "", timestamp: datetime = None) -> str:
    """Gera um ID único para a conversa usando UUID"""
    return str(uuid.uuid4())[:12]


def criar_titulo_conversa(primeira_pergunta: str) -> str:
    """Cria um título resumido para a conversa"""
    # Limita a 50 caracteres
    titulo = primeira_pergunta[:50]
    if len(primeira_pergunta) > 50:
        titulo += "..."
    return titulo


def carregar_conversas(supabase, user_id: str) -> List[Dict]:
    """
    Carrega todas as conversas do usuário agrupadas
    Retorna lista de conversas com: {id, titulo, timestamp, mensagens}
    """
    try:
        response = supabase.table("consultations") \
            .select("id, question, answer, created_at") \
            .eq("technician_id", user_id) \
            .order("created_at", desc=False) \
            .execute()
        
        # Agrupa mensagens por sessão (usando timestamps próximos)
        conversas = []
        conversa_atual = None
        ultima_data = None
        
        for row in response.data:
            created_at = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            
            # Nova conversa se passou mais de 30 minutos ou é a primeira
            if ultima_data is None or (created_at - ultima_data).total_seconds() > 1800:
                if conversa_atual:
                    conversas.append(conversa_atual)
                
                conversa_atual = {
                    "id": gerar_id_conversa(row["question"], created_at),
                    "titulo": criar_titulo_conversa(row["question"]),
                    "timestamp": created_at,
                    "mensagens": []
                }
            
            # Adiciona mensagens à conversa atual
            conversa_atual["mensagens"].append({"role": "user", "content": row["question"]})
            conversa_atual["mensagens"].append({"role": "assistant", "content": row["answer"]})
            
            ultima_data = created_at
        
        # Adiciona última conversa
        if conversa_atual:
            conversas.append(conversa_atual)
        
        # Retorna conversas mais recentes primeiro
        return list(reversed(conversas))
        
    except Exception as e:
        logger.warning(f"Não foi possível carregar conversas: {str(e)}")
        return []


def obter_conversa_ativa() -> Optional[Dict]:
    """Retorna a conversa atualmente ativa"""
    if "conversa_ativa_id" in st.session_state:
        for conversa in st.session_state.get("conversas", []):
            if conversa["id"] == st.session_state["conversa_ativa_id"]:
                return conversa
    return None


def criar_nova_conversa():
    """Cria uma nova conversa vazia"""
    nova_conversa = {
        "id": gerar_id_conversa(),
        "titulo": "Nova conversa",
        "timestamp": datetime.now(),
        "mensagens": [],
        "nova": True  # Flag para indicar que ainda não tem título definitivo
    }
    
    if "conversas" not in st.session_state:
        st.session_state["conversas"] = []
    
    # Adiciona no início da lista
    st.session_state["conversas"].insert(0, nova_conversa)
    st.session_state["conversa_ativa_id"] = nova_conversa["id"]
    st.session_state["historico"] = []


# ================= PROCESSAMENTO DE PDF =================
@st.cache_data(show_spinner="Extraindo texto dos PDFs...")
def extrair_texto_pdf(file_bytes: bytes, filename: str) -> List[Dict]:
    """Extrai texto de um PDF usando cache"""
    try:
        from io import BytesIO
        reader = PyPDF2.PdfReader(BytesIO(file_bytes))
        paginas = []
        
        for i, page in enumerate(reader.pages):
            texto = page.extract_text()
            if texto and texto.strip():
                paginas.append({
                    "pagina": i + 1,
                    "texto": texto,
                    "arquivo": filename
                })
        
        return paginas
    except Exception as e:
        logger.error(f"Erro ao processar {filename}: {str(e)}")
        return []


def dividir_em_blocos_paginas(
    paginas: List[Dict],
    tamanho: int = 1500,
    overlap: int = 300
) -> List[Dict]:
    """Divide texto das páginas em blocos menores com sobreposição"""
    blocos = []
    
    for p in paginas:
        texto = p["texto"]
        i = 0
        
        while i < len(texto):
            fim = min(i + tamanho, len(texto))
            
            blocos.append({
                "pagina": p["pagina"],
                "arquivo": p.get("arquivo", ""),
                "texto": texto[i:fim]
            })
            
            if fim >= len(texto):
                break
            
            i = fim - overlap
    
    return blocos


def buscar_blocos_relevantes(
    pergunta: str,
    blocos: List[Dict],
    top_k: int = 5
) -> List[Dict]:
    """Busca os blocos mais relevantes usando scoring de palavras-chave"""
    if not blocos:
        return []
    
    stopwords = {'o', 'a', 'de', 'da', 'do', 'e', 'é', 'para', 'com', 'um', 'uma', 'os', 'as'}
    palavras = set(
        p.lower() for p in re.findall(r"\w+", pergunta.lower())
        if len(p) > 2 and p not in stopwords
    )
    
    scores = []
    
    for bloco in blocos:
        texto_lower = bloco["texto"].lower()
        score = sum(texto_lower.count(p) for p in palavras)
        
        if score > 0:
            scores.append((score, bloco))
    
    scores.sort(key=lambda x: x[0], reverse=True)
    return [b for _, b in scores[:top_k]]


# ================= CONTROLE DE USO =================
def verificar_limite_uso(supabase, user_id: str) -> bool:
    """Verifica se o usuário atingiu o limite de uso"""
    try:
        response = supabase.rpc(
            "check_usage_limit_user",
            {"p_user_uuid": user_id}
        ).execute()
        
        result = response.data
        
        if isinstance(result, bool):
            return result
        
        if isinstance(result, list) and len(result) > 0:
            return bool(list(result[0].values())[0])
        
        return True
        
    except Exception as e:
        logger.error(f"Erro ao verificar limite: {str(e)}")
        return True


def incrementar_uso(supabase, user_id: str):
    """Incrementa o contador de uso do usuário"""
    try:
        supabase.rpc(
            "increment_usage_user",
            {"p_user_uuid": user_id}
        ).execute()
    except Exception as e:
        logger.error(f"Erro ao incrementar uso: {str(e)}")


# ================= SALVAR CONSULTA =================
def salvar_consulta(supabase, user_id: str, pergunta: str, resposta: str) -> bool:
    """Salva uma consulta no Supabase"""
    try:
        supabase.table("consultations").insert({
            "technician_id": user_id,
            "question": pergunta,
            "answer": resposta
        }).execute()
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar consulta: {str(e)}")
        return False


# ================= GERAÇÃO DE RESPOSTA =================
def gerar_resposta(model, pergunta: str, blocos: List[Dict]) -> tuple[str, set]:
    """Gera resposta usando o modelo Gemini"""
    contexto = ""
    paginas_usadas = set()
    arquivos_usados = set()
    
    for b in blocos:
        contexto += f"\n[Arquivo: {b.get('arquivo', 'N/A')} - Página {b['pagina']}]\n{b['texto']}\n"
        paginas_usadas.add(b['pagina'])
        if b.get('arquivo'):
            arquivos_usados.add(b['arquivo'])
    
    prompt = f"""Você é um técnico especialista em elevadores com anos de experiência prática.

INSTRUÇÕES IMPORTANTES:
- Use o manual como referência principal
- Explique procedimentos passo a passo de forma clara e didática
- Interprete códigos de falha detalhadamente (formato 0X-XX ou 0XXX)
- Use conhecimento técnico comum quando o manual não for explícito
- Avise quando procedimentos variarem por fabricante ou modelo
- NÃO copie tabelas literalmente - explique o conteúdo
- NÃO diga "informação não encontrada" se for possível inferir tecnicamente
- NÃO sugira procurar técnico mais experiente
- Se não houver manual específico, responda: "Não posso fornecer outros detalhes sem o manual específico"
- Seja conciso mas completo
- Use marcadores e formatação quando apropriado

CONTEXTO DOS MANUAIS:
{contexto}

PERGUNTA DO TÉCNICO:
{pergunta}

RESPOSTA TÉCNICA:"""
    
    try:
        resposta = model.generate_content(prompt).text.strip()
        
        # Adiciona rodapé com fontes
        if arquivos_usados or paginas_usadas:
            rodape = "\n\n---\n📚 **Fontes consultadas:**\n"
            if arquivos_usados:
                rodape += f"📄 Arquivos: {', '.join(sorted(arquivos_usados))}\n"
            if paginas_usadas:
                rodape += f"📖 Páginas: {', '.join(map(str, sorted(paginas_usadas)))}"
            resposta_final = resposta + rodape
        else:
            resposta_final = resposta
        
        return resposta_final, paginas_usadas
        
    except Exception as e:
        logger.error(f"Erro ao gerar resposta: {str(e)}")
        raise


# ================= INTERFACE - SIDEBAR =================
def renderizar_sidebar_conversas(supabase, user_id: str):
    """Renderiza a sidebar com lista de conversas estilo ChatGPT"""
    
    st.sidebar.title("💬 Conversas")
    
    # Botão Nova Conversa
    if st.sidebar.button("➕ Nova Conversa", use_container_width=True, type="primary"):
        criar_nova_conversa()
        st.rerun()
    
    st.sidebar.divider()
    
    # Lista de conversas
    conversas = st.session_state.get("conversas", [])
    conversa_ativa_id = st.session_state.get("conversa_ativa_id")
    
    if not conversas:
        st.sidebar.info("Nenhuma conversa ainda.\nClique em 'Nova Conversa' para começar!")
    else:
        # Agrupa conversas por data
        hoje = datetime.now().date()
        ontem = hoje - timedelta(days=1)
        esta_semana = hoje - timedelta(days=7)
        este_mes = hoje - timedelta(days=30)
        
        grupos = {
            "Hoje": [],
            "Ontem": [],
            "Esta semana": [],
            "Este mês": [],
            "Mais antigas": []
        }
        
        for conversa in conversas:
            data_conversa = conversa["timestamp"].date()
            
            if data_conversa == hoje:
                grupos["Hoje"].append(conversa)
            elif data_conversa == ontem:
                grupos["Ontem"].append(conversa)
            elif data_conversa > esta_semana:
                grupos["Esta semana"].append(conversa)
            elif data_conversa > este_mes:
                grupos["Este mês"].append(conversa)
            else:
                grupos["Mais antigas"].append(conversa)
        
        # Renderiza grupos
        for grupo_nome, grupo_conversas in grupos.items():
            if grupo_conversas:
                st.sidebar.markdown(f"**{grupo_nome}**")
                
                for conversa in grupo_conversas:
                    is_active = conversa["id"] == conversa_ativa_id
                    
                    # Container para cada conversa
                    col1, col2 = st.sidebar.columns([5, 1])
                    
                    with col1:
                        # Botão da conversa
                        button_type = "primary" if is_active else "secondary"
                        if st.button(
                            f"💬 {conversa['titulo']}", 
                            key=f"conv_{conversa['id']}",
                            use_container_width=True,
                            type=button_type if is_active else "secondary",
                            disabled=is_active
                        ):
                            st.session_state["conversa_ativa_id"] = conversa["id"]
                            st.session_state["historico"] = conversa["mensagens"].copy()
                            st.rerun()
                    
                    with col2:
                        # Botão de deletar
                        if st.button("🗑️", key=f"del_{conversa['id']}", help="Deletar conversa"):
                            # Remove a conversa
                            st.session_state["conversas"] = [
                                c for c in st.session_state["conversas"] 
                                if c["id"] != conversa["id"]
                            ]
                            
                            # Se era a ativa, limpa
                            if conversa["id"] == conversa_ativa_id:
                                st.session_state["conversa_ativa_id"] = None
                                st.session_state["historico"] = []
                            
                            st.rerun()
                
                st.sidebar.markdown("")  # Espaçamento
    
    st.sidebar.divider()
    
    # Seção de Manuais (collapse)
    with st.sidebar.expander("📚 Gerenciar Manuais", expanded=False):
        st.markdown("### 📤 Enviar Manuais")
        st.caption("Arraste ou selecione arquivos PDF")
        
        pdfs = st.file_uploader(
            "Carregar arquivos",
            type="pdf",
            accept_multiple_files=True,
            label_visibility='collapsed',
            key="pdf_uploader"
        )
        
        if pdfs:
            with st.spinner("⚙️ Processando manuais..."):
                todas_paginas = []
                
                for pdf in pdfs:
                    file_bytes = pdf.read()
                    paginas = extrair_texto_pdf(file_bytes, pdf.name)
                    todas_paginas.extend(paginas)
                    
                st.session_state["blocos"] = dividir_em_blocos_paginas(todas_paginas)
            
            st.success(f"✅ {len(st.session_state['blocos'])} blocos indexados")
        
        # Mostra manuais carregados
        if st.session_state.get("blocos"):
            arquivos = set(b.get("arquivo", "") for b in st.session_state["blocos"])
            arquivos = [a for a in arquivos if a]
            
            if arquivos:
                st.markdown("**Manuais carregados:**")
                for arq in arquivos:
                    st.caption(f"📄 {arq}")
    
    st.sidebar.divider()
    
    # Estatísticas e Logout
    with st.sidebar.expander("⚙️ Configurações", expanded=False):
        if st.button("📊 Ver Estatísticas", use_container_width=True):
            try:
                response = supabase.table("consultations") \
                    .select("id", count="exact") \
                    .eq("technician_id", user_id) \
                    .execute()
                
                total = response.count if hasattr(response, 'count') else len(response.data)
                st.metric("Total de Consultas", total)
            except:
                pass
        
        if st.button("🔄 Recarregar Conversas", use_container_width=True):
            st.session_state["conversas"] = carregar_conversas(supabase, user_id)
            st.rerun()
        
        if st.button("🚪 Sair", use_container_width=True, type="primary"):
            st.session_state.clear()
            st.rerun()


# ================= INTERFACE - CHAT =================
def renderizar_chat(model, supabase, user_id: str):
    """Renderiza a interface de chat"""
    
    # Título da conversa ativa
    conversa_ativa = obter_conversa_ativa()
    if conversa_ativa:
        st.caption(f"📝 {conversa_ativa['titulo']}")
    
    # Exibe histórico
    for msg in st.session_state.get("historico", []):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    
    # Input do usuário
    pergunta = st.chat_input("💬 Digite sua pergunta técnica...")
    
    if pergunta:
        # Se não tem conversa ativa, cria uma nova
        if not conversa_ativa:
            criar_nova_conversa()
            conversa_ativa = obter_conversa_ativa()
        
        # Atualiza título da conversa se for a primeira mensagem
        if conversa_ativa.get("nova", False):
            conversa_ativa["titulo"] = criar_titulo_conversa(pergunta)
            conversa_ativa["nova"] = False
        
        # Adiciona pergunta ao histórico
        st.session_state["historico"].append({"role": "user", "content": pergunta})
        conversa_ativa["mensagens"].append({"role": "user", "content": pergunta})
        
        with st.chat_message("user"):
            st.markdown(pergunta)
        
        # Verifica limite de uso
        if not verificar_limite_uso(supabase, user_id):
            with st.chat_message("assistant"):
                st.error("🚫 **Limite mensal de uso atingido**\n\nEntre em contato com sua empresa.")
            st.stop()
        
        # Incrementa contador
        incrementar_uso(supabase, user_id)
        
        # Busca blocos relevantes
        blocos = buscar_blocos_relevantes(
            pergunta,
            st.session_state.get("blocos", []),
            top_k=5
        )
        
        if not blocos:
            resposta_aviso = "⚠️ Não encontrei informações relevantes nos manuais carregados. Tente reformular sua pergunta ou envie manuais mais específicos."
            
            with st.chat_message("assistant"):
                st.warning(resposta_aviso)
            
            # Adiciona ao histórico
            st.session_state["historico"].append({"role": "assistant", "content": resposta_aviso})
            conversa_ativa["mensagens"].append({"role": "assistant", "content": resposta_aviso})
            return
        
        # Gera resposta
        with st.chat_message("assistant"):
            with st.spinner("🤔 Analisando Pergunta e gerando resposta..."):
                try:
                    resposta_final, _ = gerar_resposta(model, pergunta, blocos)
                    
                    # Salva no Supabase
                    if salvar_consulta(supabase, user_id, pergunta, resposta_final):
                        # Adiciona ao histórico
                        st.session_state["historico"].append({
                            "role": "assistant",
                            "content": resposta_final
                        })
                        conversa_ativa["mensagens"].append({
                            "role": "assistant",
                            "content": resposta_final
                        })
                        
                        st.markdown(resposta_final)
                        
                        # Feedback
                        col1, col2 = st.columns([1, 9])
                        with col1:
                            if st.button("👍", key=f"up_{len(st.session_state['historico'])}"):
                                st.success("✓")
                                st.write('Obrigado por seu FeedBack')
                        with col2:
                            if st.button("👎", key=f"down_{len(st.session_state['historico'])}"):
                                st.info("Feedback registrado")
                                st.write('Desculpe por falhar,melhoraremos...')
                    else:
                        st.error("❌ Erro ao salvar resposta")
                        
                except Exception as e:
                    st.error(f"❌ Erro ao gerar resposta: {str(e)}")
                    logger.error(f"Erro: {e}", exc_info=True)


# ================= INICIALIZAÇÃO =================
def inicializar_session_state(supabase, user_id: str):
    """Inicializa variáveis de session state"""
    if "blocos" not in st.session_state:
        st.session_state["blocos"] = []
    
    if "conversas" not in st.session_state:
        st.session_state["conversas"] = carregar_conversas(supabase, user_id)
    
    if "historico" not in st.session_state:
        st.session_state["historico"] = []
    
    # Define conversa ativa (a mais recente se existir)
    if "conversa_ativa_id" not in st.session_state:
        if st.session_state["conversas"]:
            primeira_conversa = st.session_state["conversas"][0]
            st.session_state["conversa_ativa_id"] = primeira_conversa["id"]
            st.session_state["historico"] = primeira_conversa["mensagens"].copy()


# ================= MAIN =================
def main():
    """Função principal da aplicação"""
    
    # Configuração inicial
    init_page_config()
    load_css()
    
    # Inicializa APIs
    model, supabase = init_apis()
    
    # Verifica autenticação
    user_id = verificar_autenticacao()
    
    # Inicializa session state
    inicializar_session_state(supabase, user_id)
    
    # Título
    st.title("🤖 Chat Técnico de Elevadores")
    st.caption("Assistente inteligente com análise de manuais técnicos")
    
    # Renderiza sidebar com conversas
    renderizar_sidebar_conversas(supabase, user_id)
    
    # Verifica se há manuais carregados
    if not st.session_state["blocos"]:
        st.info("👆 **Comece enviando manuais técnicos**")
        st.markdown("""
        ### 📋 Como usar:
        1. Clique em **"Gerenciar Manuais"** na barra lateral
        2. Faça upload de um ou mais manuais técnicos em PDF
        3. Aguarde o processamento
        4. Faça suas perguntas no chat!
        
        💡 Suas conversas ficam salvas na barra lateral para fácil acesso.
        """)
    else:
        # Renderiza chat
        renderizar_chat(model, supabase, user_id)


if __name__ == "__main__":
    main()