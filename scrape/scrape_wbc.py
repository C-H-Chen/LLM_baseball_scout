import os
import pandas as pd
import mysql.connector
import math
from dotenv import load_dotenv
from pybaseball import statcast_pitcher
from pybaseball.playerid_lookup import playerid_lookup
from datetime import datetime
from tqdm import tqdm

# 環境變數
load_dotenv()

# 投手名單
players = [
    {"first": "Devin", "last": "Williams"},
    {"first": "Ryan", "last": "Pressly"},
    {"first": "Daniel", "last": "Bard"},
    {"first": "David", "last": "Bednar"},
    {"first": "Adam", "last": "Wainwright"},
    {"first": "Lance", "last": "Lynn"},
    {"first": "Adam", "last": "Ottavino"},
    {"first": "Kendall", "last": "Graveman"},
    {"first": "Kyle", "last": "Freeland"},
    {"first": "Merrill", "last": "Kelly"},
    {"first": "Jason", "last": "Adam"},
    {"first": "Brady", "last": "Singer"},
    {"first": "Aaron", "last": "Loup"},
    {"first": "Miles", "last": "Mikolas"},
    {"first": "Nick", "last": "Martinez", "mlbam_id": 607259}
]
start_date = '2022-04-07'
end_date = '2022-11-05'
TABLE_NAME = "pitching_data"
MERGED_CSV = "wbc_usa_pitchers_2022.csv"

# 資料庫設定
DB_CONFIG = {
    'host': os.getenv("DB_HOST"),
    'port': int(os.getenv("DB_PORT", 4000)),
    'user': os.getenv("DB_USER"),
    'password': os.getenv("DB_PASSWORD"),
    'database': os.getenv("DB_NAME"),
    'charset': 'utf8mb4',
    'ssl_ca': os.getenv("DB_SSL_CA"),
    'ssl_verify_cert': os.getenv("DB_SSL_VERIFY_CERT", "True") == "True",
    'ssl_verify_identity': os.getenv("DB_SSL_VERIFY_IDENTITY", "True") == "True",
    'connection_timeout': 10
}
# 爬蟲
all_dfs = []
for player in players:
    try:
        print(f"🔍 抓取 {player['first']} {player['last']} 資料中...")
        if "mlbam_id" in player:
            pitcher_id = player["mlbam_id"]
        else:
            pid_lookup = playerid_lookup(player['last'], player['first'])
            pitcher_id = pid_lookup['key_mlbam'].values[0]

        df = statcast_pitcher(start_date, end_date, pitcher_id)
        df['game_date'] = pd.to_datetime(df['game_date'])
        df = df.dropna(axis=1)
        df["player_name"] = f"{player['first']} {player['last']}"
        all_dfs.append(df)

    except Exception as e:
        print(f"⚠️ 無法處理 {player['first']} {player['last']}: {e}")

# 合併與儲存 CSV
if not all_dfs:
    print("❌ 沒有成功抓取任何資料，終止匯入")
    exit()

merged_df = pd.concat(all_dfs, ignore_index=True).dropna(axis=1)
merged_df.to_csv(MERGED_CSV, index=False)
print(f"✅ 合併資料已儲存為：{MERGED_CSV}")

# 匯入SQL
df = pd.read_csv(MERGED_CSV)
conn = mysql.connector.connect(**DB_CONFIG)
cursor = conn.cursor()

cursor.execute("CREATE DATABASE IF NOT EXISTS test;")
cursor.execute("USE test;")

# 自動建表
def map_dtype(dtype):
    if pd.api.types.is_integer_dtype(dtype):
        return "INT"
    elif pd.api.types.is_float_dtype(dtype):
        return "FLOAT"
    elif pd.api.types.is_datetime64_any_dtype(dtype):
        return "DATETIME"
    else:
        return "TEXT"

columns_sql = ", ".join([
    f"`{col}` {map_dtype(dtype)}"
    for col, dtype in df.dtypes.items()
])

create_sql = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    {columns_sql}
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""
cursor.execute(create_sql)

# 插入資料（分批 + 進度條）
placeholders = ", ".join(["%s"] * len(df.columns))
columns = ", ".join([f"`{col}`" for col in df.columns])
insert_sql = f"INSERT INTO {TABLE_NAME} ({columns}) VALUES ({placeholders})"

values_list = [
    tuple(None if (isinstance(v, float) and math.isnan(v)) else v for v in row.values)
    for _, row in df.iterrows()
]

batch_size = 500
print(f"🚀 正在分批插入 {len(values_list)} 筆資料，每批 {batch_size} 筆...")

for i in tqdm(range(0, len(values_list), batch_size), desc="匯入中"):
    batch = values_list[i:i+batch_size]
    cursor.executemany(insert_sql, batch)
    conn.commit()

cursor.close()
conn.close()

print(f"✅ 資料已成功匯入 MySQL 資料表 `{TABLE_NAME}`")
