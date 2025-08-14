import os
import re
import time
import uvicorn

from collections import defaultdict
from typing import List
from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel
from langchain_google_genai.embeddings import GoogleGenerativeAIEmbeddings
from langchain_chroma import Chroma
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.prompts import PromptTemplate
from langchain.memory import ConversationSummaryBufferMemory
from langchain.chains import ConversationalRetrievalChain
from google.api_core.exceptions import ResourceExhausted

# 環境變數
load_dotenv()
os.environ["GOOGLE_API_KEY"] = os.getenv("GOOGLE_API_KEY")

# FastAPI app
app = FastAPI()

# 使用者記憶池
user_memory_store = {}
user_last_player = {}

# 球員名單
all_players = [
    "Brady Singer", "Lance Lynn", "Devin Williams", "Adam Wainwright",
    "Daniel Bard", "Jason Adam", "David Bednar", "Nick Martinez",
    "Miles Mikolas", "Kendall Graveman", "Ryan Pressly", "Aaron Loup",
    "Kyle Freeland", "Adam Ottavino", "Merrill Kelly"
]

# lazy vector DB handle
embedding = None
vectordb = None
_vectordb_lock = None

# Prompt template
template = """
你是一位專業的棒球情蒐分析師，請根據美國隊投手的 2022 年資料，對使用者的問題全面分析與說明。

【內容原則】
- 結論優先，請先摘要出重點總結或建議（可條列），讓讀者能快速掌握核心資訊
- 僅依據提供的內容回答，**不得捏造任何未存在的資訊**
- 如有需進行推論的必要性，**請明確指出屬於推論的部分**
- 回答清楚、專業、易懂，適合球員、教練與管理層閱讀
- 回答後的內容依據從簡附註

【資料紀錄】
{context}

【問題】
{question}

【請輸出你的回答】
"""
prompt = PromptTemplate(
    template=template,
    input_variables=["context", "question"]
)
def init_vectordb_if_needed():
    """Lazy init embedding and vectordb (first call only)."""
    global embedding, vectordb, _vectordb_lock
    if _vectordb_lock is None:
        import threading
        _vectordb_lock = threading.Lock()
    with _vectordb_lock:
        if vectordb is None:
            print("🔄 初次載入向量庫中（lazy init）...")
            embedding = GoogleGenerativeAIEmbeddings(model="models/embedding-001")
            # persist_directory 與你的原始設定一致
            vectordb = Chroma(persist_directory="./chromadb_wbc_usa", embedding_function=embedding)
            print("✅ 向量庫載入完成")

# 功能函數（保持原有邏輯，僅在需要時才 init vectordb）
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
    # lazy init vectordb on first real request
    init_vectordb_if_needed()

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

    MAX_TOKENS = 225000
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
        docs = temp_retriever.invoke(question)
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
        return "⚠️ 找不到符合 token 限制的資料。"

    print(f"🔍 最終選擇 k={best_k} 進行回答生成")

    search_kwargs_final = {"k": best_k}
    if player_name:
        search_kwargs_final["filter"] = {"player_name": {"$in": player_name}}

    retriever = vectordb.as_retriever(search_kwargs=search_kwargs_final)

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
            answer = result["answer"]
            if not answer.strip():
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

# FastAPI API
class QuestionRequest(BaseModel):
    question: str
    user_id: str = "default"

@app.post("/ask")
def ask_question(req: QuestionRequest):
    print(f"📥 收到問題：{req.question} 來自使用者：{req.user_id}")
    answer = get_answer(req.question, user_id=req.user_id)
    print(f"📤 回答完成，回傳使用者：{req.user_id}")
    return {"answer": answer}

@app.get("/")
def home():
    return {"status": "ok", "message": "Baseball RAG API is running."}

# 啟動服務（若直接跑 main.py）
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 啟動 API 服務，埠號：{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
