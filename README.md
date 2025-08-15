# 簡介:  

本系統致力於將LLM與運動情蒐流程作結合，針對使用者輸入的問題自動生成情蒐報告，輔助提升情蒐效率與品質。  
  
現階段是針對2023年世界棒球經典賽美國隊的全部15位投手進行情蒐，使用者透過LINE傳送訊息至本系統後，  
LLM(Gemini 2.5 Pro)會針對訊息中單一個別球員前一年(2022年)的賽季相關資料進行分析，生成詳盡的情蒐報告。  
  
下圖為本系統的LINE聊天機器人之QRcode，加好友後即可開始使用本系統:  
  
<img width="260" height="265" alt="image" src="https://github.com/user-attachments/assets/a7d6aa2d-e056-4c96-967c-e26daf3c66cc" />  


下圖為系統架構:  
  
![architecture](https://github.com/user-attachments/assets/66ee1821-942f-49a5-a6ec-c2100acb3517)  
  
# 使用說明:  
  
本系統能針對數據表現、投球特點與情境策略等不同問題題型，分析現有資料庫的資訊生成專業合理的情蒐報告。  
  
輸入訊息時，請指名單一球員完整且正確的姓名或姓氏作為處理問題之依據 (可無視大小寫)，以獲得最佳的回覆。  
  
例如:  
  
「想了解Devin Williams各個球種的進壘點分佈情形？」  

「告訴我Adam Wainwright在一好兩壞面對左打時，會如何執行投球計畫?」  

「右打在面對Mikolas時，該如何擬定打擊策略？」  
  
於LINE輸入 "名單" 兩個字並傳送訊息會回覆2023年WBC美國隊參賽投手的完整名單供使用者作查詢。  
  
本系統模型目前是基於下圖提示工程的原則生成回答，可視使用者回饋來調整提示工程的內容以滿足需求。  
  
下圖為提示工程的內容:  
  
<img width="1744" height="300" alt="image" src="https://github.com/user-attachments/assets/c9b28d7c-b147-4d10-9b61-310eaaeba28b" />   
  
  
<h4>單輪問答:</h4>    
  
系統接收訊息後，會以訊息中單一個別球員完整且正確的姓名或姓氏作為檢索資訊的依據。  
  
生成的情蒐報告會最先列出重點摘要，讓使用者能快速掌握核心資訊。  
  
<img width="1920" height="1026" alt="image" src="https://github.com/user-attachments/assets/2205b52d-766f-433e-9676-917354c47342" />
  
<h4>多輪問答:</h4>  
  
當使用者於後續送出的訊息中未提及球員名字時，系統會於記憶中補足資訊(最近一次使用者訊息中的球員名字)，  
  
並接續針對使用者問題檢索資料以生成回答，實現基於單一球員的多輪問答。  
  
<img width="1920" height="1028" alt="image" src="https://github.com/user-attachments/assets/b5d4fa74-85fd-415f-b39f-149211b75a5a" />  
  
<h4>記憶重置:</h4>  

當使用者於當前訊息中提及的球員名字與最近一次訊息的球員名字不同時，為避免上下文的記憶錯亂，  
  
系統內部會偵測到並進行球員名字的切換，當前的記憶會完全清除，以重新開啟新一輪的問答。  
  
<img width="1920" height="1031" alt="image" src="https://github.com/user-attachments/assets/332eca70-697a-42b4-93cd-45dd4bd0d483" />   
  
# 使用限制:  

由於本系統皆由免費資源所建置完成，故有以下使用限制:  
  
1. 僅支援回答單一投手的問題，未提供正確回答涉及多球員的問題或比較 (Gemini的tokens限制)
  
2. 雖有球員整季的逐場數據，但目前生成的情蒐報告是由系統檢索最相關的幾場比賽數據所構成，而非整季資料
   (Gemini的tokens限制)  
  
3. 使用者第一則留言的回覆會有較久的延遲，依訊息難度與複雜度2~5分鐘不等 (Render Web Service的休眠限制)  

4. 以球員完整且正確的姓名或姓氏作為處理問題之依據 (可無視大小寫)

5. 目前僅支援2023經典賽美國隊投手2022年賽季的MLB公開數據作回答 (可再擴充)  
  
# 球員名單:   
  
目前本系統資料庫針對2023年經典賽美國隊投手群所建置的球員名單如下:    
    
姓名    
Adam Ottavino    
Kyle Freeland    
Nick Martinez   
Aaron Loup   
Merrill Kelly   
Lance Lynn    
Devin Williams   
Miles Mikolas   
Jason Adam   
Kendall Graveman    
Adam Wainwright   
Brady Singer    
Daniel Bard    
David Bednar    
Ryan Pressly  
