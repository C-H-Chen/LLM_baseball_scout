import mysql.connector
import pandas as pd
import os
from tqdm import tqdm
from dotenv import load_dotenv

from langchain_google_genai.embeddings import GoogleGenerativeAIEmbeddings
from langchain_chroma import Chroma
from langchain.schema import Document

# SQL設定
load_dotenv()
DB_CONFIG = {
    'host': os.getenv("DB_HOST"),
    'port': int(os.getenv("DB_PORT", 4000)),
    'user': os.getenv("DB_USER"),
    'password': os.getenv("DB_PASSWORD"),
    'database': os.getenv("DB_DATABASE"),
    'charset': 'utf8mb4',
    'ssl_ca': os.getenv("DB_SSL_CA"),
    'ssl_verify_cert': os.getenv("DB_SSL_VERIFY_CERT", "True") == "True",
    'ssl_verify_identity': os.getenv("DB_SSL_VERIFY_IDENTITY", "True") == "True",
    'connection_timeout': 10
}
TABLE_NAME = "pitching_data"
TEXT_FILE = "wbc_usa_pitchers_2022.txt"
PERSIST_DIR = "./chromadb_wbc_usa"

# 讀取資料
conn = mysql.connector.connect(**DB_CONFIG)
query = f"SELECT * FROM {TABLE_NAME} ORDER BY player_name, game_date ASC"
df = pd.read_sql(query, conn)
conn.close()
print(f"✅ 已讀取資料，共 {len(df)} 筆紀錄，{df['player_name'].nunique()} 位球員。")

# 分割chunk並生成自然語言描述
def split_by_player_and_game_with_metadata(df, text_file_path=None):
    chunks = []
    full_text_output = []

    grouped = df.groupby(['player_name', 'game_date'])
    for (player_name, game_date), group in grouped:
        content_lines = []
        for _, row in group.iterrows():
            line = []
            for col in df.columns:
                if pd.notnull(row[col]):
                    val = round(row[col], 2) if isinstance(row[col], float) else row[col]
                    line.append(f"{col.replace('_', ' ')}: {val}")
            content_lines.append(" | ".join(line))

            # 產生文字檔格式
            text_items = []
            for col in df.columns:
                if pd.notnull(row[col]):
                    value = round(row[col], 2) if isinstance(row[col], float) else row[col]
                    text_items.append(f"{col.replace('_', ' ')} was {value}")
            line_text = f"Pitch event for {row['player_name']}: " + "; ".join(text_items) + "."
            full_text_output.append(line_text)

        full_content = "\n".join(content_lines)
        chunk_text = f"【球員：{player_name}】【比賽日期：{game_date}】\n{full_content}"
        metadata = {
            "player_name": player_name,
            "game_date": str(game_date)
        }
        chunks.append(Document(page_content=chunk_text, metadata=metadata))

    # 寫出文字檔
    if text_file_path:
        with open(text_file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(full_text_output))
        print(f"✅ 已輸出為 {text_file_path}")

    return chunks

# 呼叫上面函式，同時切 chunk 並產出自然語言文字檔
documents = split_by_player_and_game_with_metadata(df, text_file_path=TEXT_FILE)

# 設定Embedding
os.environ["GOOGLE_API_KEY"] = os.getenv("GOOGLE_API_KEY")
embedding = GoogleGenerativeAIEmbeddings(model="models/embedding-001")

# 建立或載入向量庫
if os.path.exists(PERSIST_DIR) and os.listdir(PERSIST_DIR):
    vectordb = Chroma(persist_directory=PERSIST_DIR, embedding_function=embedding)
    print("📁 已載入現有向量庫，不重複新增文件")
else:
    vectordb = Chroma(embedding_function=embedding, persist_directory=PERSIST_DIR)
    print("🆕 尚無向量庫，開始分批新增 documents...")

    BATCH_SIZE = 500
    for i in tqdm(range(0, len(documents), BATCH_SIZE), desc="🔄 加入中"):
        batch = documents[i:i + BATCH_SIZE]
        vectordb.add_documents(batch)

    print("✅ 已完成向量庫建立並儲存。")

# 檢查文件數量
docs = vectordb.get()
print(f"✅ 向量庫文件總數：{len(docs['ids'])}")
