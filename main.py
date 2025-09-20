import os
import re
import time
import threading
from collections import defaultdict
from typing import List
from dotenv import load_dotenv
from langchain_huggingface import HuggingFaceEndpointEmbeddings
from langchain_chroma import Chroma
from langchain.schema import Document
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.prompts import PromptTemplate
from langchain.memory import ConversationSummaryBufferMemory
from langchain.chains import ConversationalRetrievalChain
from google.api_core.exceptions import ResourceExhausted

# load env & HF caches
load_dotenv()

# --- HF cache & model setup 
HF_CACHE_DIR = os.getenv("HF_CACHE_DIR", "./hf_cache")
HF_LOCAL_MODEL_DIR = os.getenv("HF_LOCAL_MODEL_DIR", "")  # optional: "./models/all-MiniLM-L6-v2"
HF_MODEL_NAME = os.getenv("HF_MODEL_NAME", "sentence-transformers/paraphrase-MiniLM-L3-v2")
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./chromadb_wbc_usa")

os.makedirs(HF_CACHE_DIR, exist_ok=True)
# set env vars so HF/transformers use persistent cache (important on Render)
os.environ["HF_HOME"] = HF_CACHE_DIR
os.environ["TRANSFORMERS_CACHE"] = HF_CACHE_DIR
os.environ["HF_DATASETS_CACHE"] = HF_CACHE_DIR

# GOOGLE API key for Gemini
os.environ["GOOGLE_API_KEY"] = os.getenv("GOOGLE_API_KEY", "")

# user memory
user_memory_store = {}
user_last_player = {}

# players list
all_players = [
    "Brady Singer", "Lance Lynn", "Devin Williams", "Adam Wainwright",
    "Daniel Bard", "Jason Adam", "David Bednar", "Nick Martinez",
    "Miles Mikolas", "Kendall Graveman", "Ryan Pressly", "Aaron Loup",
    "Kyle Freeland", "Adam Ottavino", "Merrill Kelly"
]

# lazy globals
embedding = None
vectordb = None
_vectordb_lock = None

# prompt
template = """
你是一位專業的棒球情蒐分析師，請根據美國隊投手的 2022 年資料，對使用者的問題全面分析與說明。

【內容原則】
- 結論優先，請先摘要出重點總結或建議（可條列），讓讀者能快速掌握核心資訊
- 僅依據提供的內容回答，**不得捏造任何未存在的資訊**
- 如有需進行推論的必要性，**請明確指出屬於推論的部分**
- 回答清楚、專業、易懂
- 回答後的內容依據從簡附註

【資料紀錄】
{context}

【問題】
{question}

【請輸出你的回答】
"""
prompt = PromptTemplate(template=template, input_variables=["context", "question"])

def init_vectordb_if_needed():
    global embedding, vectordb, _vectordb_lock
    if _vectordb_lock is None:
        _vectordb_lock = threading.Lock()
    with _vectordb_lock:
        if vectordb is not None:
            return

        print("🔄 初次載入向量庫中（lazy init）...")

        try:
            embedding = HuggingFaceEndpointEmbeddings(
                model=os.getenv("HF_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2"),
                huggingfacehub_api_token=os.getenv("HF_API_TOKEN")
            )
        except Exception as e:
            # make error explicit and re-raise for caller to catch and push to LINE
            print("❌ HuggingFaceEmbeddings 初始化失敗:", e)
            raise RuntimeError(f"HuggingFaceEmbeddings init failed: {e}")

        try:
            # If Chroma DB folder exists -> load; otherwise try to load but warn (prefill recommended)
            if os.path.exists(CHROMA_PERSIST_DIR) and os.listdir(CHROMA_PERSIST_DIR):
                vectordb = Chroma(persist_directory=CHROMA_PERSIST_DIR, embedding_function=embedding)
                print("✅ 已載入現有向量庫")
            else:
                # 如果沒有預先建好的 DB，先嘗試載入）
                print("⚠️ chroma persist dir 空或不存在，會在第一次 run 時建立。建議預先建立以避免 cold-start 建庫延遲。")
                vectordb = Chroma(embedding_function=embedding, persist_directory=CHROMA_PERSIST_DIR)
                print("ℹ️ 已建立 Chroma handle（但未新增 documents）。若向量庫為空，檢索將找不到文件。")
        except Exception as e:
            print("❌ Chroma 載入/建立失敗:", e)
            raise RuntimeError(f"Chroma init failed: {e}")

        print("✅ 向量庫載入完成")

def extract_player_name(question: str, all_players: List[str]) -> List[str]:
    matched = []
    q_lower = question.lower()
    for full_name in all_players:
        if full_name.lower() in q_lower:
            matched.append(full_name)
    if matched:
        print(f"🎯 問題中明確指定球員：{matched}")
        return matched

    words = re.findall(r"[a-zA-Z]+", question)
    if len(words) == 1:
        last_candidate = words[0].lower()
        for full_name in all_players:
            _, last_name = map(str.lower, full_name.split())
            if last_candidate == last_name:
                matched.append(full_name)
        if matched:
            print(f"🎯 問題中以姓氏判斷球員：{matched}")
    return matched

def estimate_token_count(text: str) -> int:
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    non_chinese = len(text) - chinese_chars
    return int(chinese_chars * 1.2 + non_chinese * 0.75)

def get_answer(question: str, player_name: list = None, user_id: str = "default") -> str:
    # lazy init vectordb
    try:
        init_vectordb_if_needed()
    except Exception as e:
        err = f"❌ 初始化向量庫失敗: {e}"
        print(err)
        return err

    extracted_players = extract_player_name(question, all_players)
    if extracted_players:
        player_name = extracted_players
    else:
        if user_id in user_memory_store:
            memory = user_memory_store[user_id]
            history = memory.load_memory_variables({})["chat_history"]
            if history:
                for msg in reversed(history[-4:]):
                    names = extract_player_name(msg.content, all_players)
                    if names:
                        player_name = names
                        print(f"🧠 從記憶補足 player_name: {player_name}")
                        break

    if user_id in user_last_player:
        if sorted(player_name or []) != sorted(user_last_player[user_id]):
            print(f"🔄 偵測到球員切換，重置 {user_id} 記憶。從 {user_last_player[user_id]} -> {player_name}")
            try:
                del user_memory_store[user_id]
            except KeyError:
                pass
            user_last_player[user_id] = player_name or []
    else:
        user_last_player[user_id] = player_name or []

    MAX_TOKENS = 125000
    MAX_K = 20
    MIN_K = 1
    if player_name:
        num_players = len(player_name)
        k_per_player = max(1, MAX_K // num_players)
    else:
        k_per_player = MAX_K

    print(f"⚾️ 抽取球員：{player_name if player_name else '未指定'}，每人最多取 {k_per_player} 筆")

    low = MIN_K
    high = k_per_player
    best_k = None
    best_context_text = ""

    while low <= high:
        mid = (low + high) // 2
        search_kwargs = {"k": mid}
        if player_name:
            search_kwargs["filter"] = {"player_name": {"$in": player_name}}

        temp_retriever = vectordb.as_retriever(search_kwargs=search_kwargs)
        try:
            docs = temp_retriever.invoke(question)
        except Exception as e:
            print(f"檢索時發生例外: {e}")
            docs = []

        if not docs:
            print(f"❗️ k={mid} 無檢索到文件，往更大 k 嘗試")
            low = mid + 1
            continue

        context_text = "\n\n".join(doc.page_content for doc in docs)
        estimated_tokens = estimate_token_count(context_text) + estimate_token_count(question)
        print(f"🧮 預估 tokens: {estimated_tokens} (k={mid})")

        if estimated_tokens <= MAX_TOKENS:
            best_k = mid
            best_context_text = context_text
            print(f"✅ k={mid} 符合限制，嘗試更大 k")
            low = mid + 1
        else:
            print(f"❌ k={mid} 超過 token 限制，嘗試更小 k")
            high = mid - 1

    if best_k is None:
        return "⚠️ 找不到符合 token 限制或向量庫沒有相關文件。"

    print(f"🔍 最終選擇 k={best_k} 進行回答生成")

    search_kwargs_final = {"k": best_k}
    if player_name:
        search_kwargs_final["filter"] = {"player_name": {"$in": player_name}}

    retriever = vectordb.as_retriever(search_kwargs=search_kwargs_final)

    # memory init
    if user_id not in user_memory_store:
        print(f"🔰 為使用者 {user_id} 建立新的記憶池")
        memory = ConversationSummaryBufferMemory(
            llm=ChatGoogleGenerativeAI(model="gemini-2.5-pro", temperature=0),
            memory_key="chat_history",
            return_messages=True
        )
        user_memory_store[user_id] = memory
    else:
        print(f"🗄️ 使用者 {user_id} 使用舊有記憶池")
        memory = user_memory_store[user_id]

    llm = ChatGoogleGenerativeAI(model="gemini-2.5-pro", temperature=0)
    qa_chain = ConversationalRetrievalChain.from_llm(
        llm=llm,
        retriever=retriever,
        memory=memory,
        combine_docs_chain_kwargs={"prompt": prompt},
    )

    for attempt in range(9):
        try:
            print(f"🚀 問題：{question}（Player: {player_name}） 第 {attempt+1} 次嘗試")
            result = qa_chain.invoke({"question": question})
            answer = result.get("answer", "") if isinstance(result, dict) else ""
            if not answer or not answer.strip():
                print("⚠️ 回答為空，稍等 3 秒再試")
                time.sleep(3)
                continue
            print("✅ 成功取得回答")
            return answer
        except ResourceExhausted:
            print(f"⚠️ API 配額限制，等待 61 秒後重試...（第 {attempt+1} 次）")
            time.sleep(61)
        except Exception as e:
            print(f"❌ 發生錯誤：{e}")
            return f"❌ 發生錯誤：{e}"
    return "❌ 多次嘗試仍失敗，請稍後再試或檢查配額。"
'''
# test
if __name__ == "__main__":
    test_questions = [
        ("Singer的控球如何？", "user_test1"),
        ("lynn的球路品質如何？", "user_test2"),
        ("Devin Williams 2022 最常用球種？", "user_test3"),
        ("Pressly 的救援成功率？", "user_test4"),
        ("Mikolas 表現分析", "user_test5"),
    ]

    for q, uid in test_questions:
        print("="*50)
        print(f"🔹 測試問題：{q} (使用者: {uid})")
        ans = get_answer(q, user_id=uid)
        print(f"💡 回答：{ans}\n")
'''