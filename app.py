import streamlit as st
from datetime import datetime
import taiwan_market_report_v2 as twse

# 設定網頁標題與寬度版面
st.set_page_config(page_title="台股盤後籌碼報告", layout="wide")

st.title("📈 台股盤後籌碼報告（TWSE 上市）")
st.caption("透過 TWSE 官方公開數據即時產生盤後法人、融資券與鉅額交易分析")

# 側邊欄控制項
with st.sidebar:
    st.header("⚙️ 查詢設定")
    selected_date = st.date_input("選擇基準日期", value=datetime.now())
    run_button = st.button("🚀 開始產生報告", type="primary")

if run_button:
    with st.spinner("正在向證交所抓取與計算資料，請稍候（約需 30~60 秒）..."):
        try:
            # 轉換為 datetime 物件
            base_date = datetime.combine(selected_date, datetime.min.time())
            
            # 1. 取得近 5 個開盤日
            trading_days = twse.get_recent_trading_days(5, base_date)
            
            market_data = {}
            institutional_history = {}
            margin_data = {}

            # 2. 抓取法人與市場融資券
            for day in trading_days:
                ad_date = twse.to_ad_compact(day)
                market_data[ad_date] = twse.fetch_twse_market_institutional(ad_date)
                institutional_history[ad_date] = twse.fetch_twse_institutional_stocks(ad_date)
                margin_data[ad_date] = twse.fetch_twse_margin_day(ad_date)

            # 3. 抓取個股融資券與當日盤後鉅額交易
            current_date = trading_days[-1]
            current_compact = twse.to_ad_compact(current_date)
            margin_stocks = twse.fetch_twse_margin_stocks(current_compact)
            block_trades = twse.fetch_twse_block_trades(current_compact)

            # 4. 產生報告內容（包含鉅額交易清單）
            report_markdown = twse.generate_report(
                trading_days, market_data, institutional_history, margin_data, margin_stocks, block_trades
            )

            st.success(f"✅ 報告產生成功！（最新有效交易日：{current_date.strftime('%Y/%m/%d')}）")
            
            # 5. 在網頁上直接渲染 Markdown 報告
            st.markdown(report_markdown)

        except Exception as e:
            st.error(f"執行時發生錯誤：{e}")
else:
    st.info("👈 請在左側選擇基準日期，並點擊「開始產生報告」。")
