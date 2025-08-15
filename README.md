# 簡介:  

本系統致力於將LLM與運動情蒐流程結合，針對使用者輸入的問題自動生成情蒐報告，輔助提升情蒐效率與品質。  
  
現階段是針對2023年世界棒球經典賽美國隊的全部15位投手進行情蒐，使用者透過LINE傳送訊息至本系統後，  
系統會針對訊息中單一個別球員前一年(2022年)的賽季表現進行分析，生成詳盡的情蒐報告。  
  
下圖為LINE聊天機器人的QRcode，加好友後即可開始使用:  
  
<img width="260" height="265" alt="image" src="https://github.com/user-attachments/assets/a7d6aa2d-e056-4c96-967c-e26daf3c66cc" />  
  
下圖為專案架構:  
  
![architecture](https://github.com/user-attachments/assets/66ee1821-942f-49a5-a6ec-c2100acb3517)  
  
# 使用說明:  
  
本系統能針對數據表現、投球特點與情境策略等不同問題題型，分析現有資料庫的資訊生成專業合理的情蒐報告。  
  
本系統模型目前是基於下圖提示工程的原則生成回答，可視使用者回饋來調整提示工程的內容以滿足需求。  
  
下圖為提示工程的內容:  
  
<img width="1744" height="300" alt="image" src="https://github.com/user-attachments/assets/c9b28d7c-b147-4d10-9b61-310eaaeba28b" />  
  
<h4>單輪問答:</h4>    
  
系統接收訊息後，會以訊息中單一個別球員完整且正確的姓名或姓氏作為檢索資訊的依據。  
  
生成的情蒐報告會最先列出重點摘要，讓使用者能快速掌握核心資訊。  
  
<img width="1920" height="1026" alt="image" src="https://github.com/user-attachments/assets/2205b52d-766f-433e-9676-917354c47342" />
  
<h4>多輪問答:</h4>  
  
當使用者於後續送出的訊息中未提及球員名字時，系統會於記憶中補足資訊(前一次使用者訊息中的球員名字)，以進行分析與回答。  
  
<img width="1920" height="1028" alt="image" src="https://github.com/user-attachments/assets/b5d4fa74-85fd-415f-b39f-149211b75a5a" />  
  
<h4>記憶重置:</h4>  
  
<img width="1920" height="1031" alt="image" src="https://github.com/user-attachments/assets/332eca70-697a-42b4-93cd-45dd4bd0d483" />   

# 使用限制:  

