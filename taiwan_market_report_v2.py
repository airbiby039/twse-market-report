#!/usr/bin/env python3
"""台股盤後籌碼報告產生器（TWSE 上市個股）v3 整合鉅額交易版。"""

import argparse
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
import requests

TWSE_BASE = "https://www.twse.com.tw"
REPORT_DIR = Path(__file__).resolve().parent / "reports"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Referer": "https://www.twse.com.tw/zh/page/trading/fund/BFI82U.html",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "X-Requested-With": "XMLHttpRequest",
}

session = requests.Session()
session.headers.update(HEADERS)


def to_ad_compact(day):
    return day.strftime("%Y%m%d")


def to_roc(day):
    return f"{day.year - 1911}{day.month:02d}{day.day:02d}"


def parse_num(value):
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("--", "0").strip()
    if text in {"", "-", "N/A", "nan", "None"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def fmt_bill(value, signed=False):
    if value is None:
        return "-"
    prefix = "+" if signed else ""
    return f"{value / 1e8:{prefix},.2f}"


def fetch_json(url, timeout=8, retries=2):
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            if attempt == retries - 1:
                return None
            time.sleep(1 + random.uniform(0.2, 0.5))
    return None


def returned_date_matches(data, ad_date):
    returned = str(data.get("date", "")).replace("/", "").replace("-", "")
    if returned:
        return returned == ad_date
    title = " ".join(
        str(data.get(key, "")) for key in ("title", "stat", "description")
    )
    match = re.search(r"(\d{2,3})年(\d{1,2})月(\d{1,2})日", title)
    if not match:
        return True
    year, month, day = map(int, match.groups())
    if year < 1911:
        year += 1911
    return f"{year}{month:02d}{day:02d}" == ad_date


def clean_field(name):
    return re.sub(r"[\s\u3000（）()]", "", str(name))


def build_index(fields):
    return {clean_field(field): index for index, field in enumerate(fields)}


def find_index(index, candidates):
    for candidate in candidates:
        key = clean_field(candidate)
        if key in index:
            return index[key]
    for candidate in candidates:
        key = clean_field(candidate)
        for field, position in index.items():
            if key in field:
                return position
    return None


def get_value(row, index, candidates):
    position = find_index(index, candidates)
    if position is None or position >= len(row):
        return 0.0
    return parse_num(row[position])


def fetch_twse_market_institutional(ad_date):
    url = f"{TWSE_BASE}/fund/BFI82U?response=json&dayDate={ad_date}&type=day"
    data = fetch_json(url)
    if not data or data.get("stat") != "OK" or not returned_date_matches(data, ad_date):
        return None

    result = {"foreign": 0.0, "trust": 0.0, "dealer_prop": 0.0, "dealer_hedge": 0.0, "total": 0.0}
    for row in data.get("data", []):
        if len(row) < 4:
            continue
        name = str(row[0])
        net = parse_num(row[3])
        if "外資及陸資" in name:
            result["foreign"] += net
        elif "投信" in name:
            result["trust"] += net
        elif "自營商(自行買賣)" in name:
            result["dealer_prop"] += net
        elif "自營商(避險)" in name:
            result["dealer_hedge"] += net
        elif name.strip() == "合計":
            result["total"] = net
    return result


def fetch_twse_institutional_stocks(ad_date):
    url = f"{TWSE_BASE}/rwd/zh/fund/T86?date={ad_date}&selectType=ALL&response=json"
    data = fetch_json(url)
    if not data or data.get("stat") != "OK" or not returned_date_matches(data, ad_date):
        return []

    fields = data.get("fields", [])
    index = build_index(fields)
    stocks = []

    for row in data.get("data", []):
        code_pos = find_index(index, ["證券代號", "股票代號"])
        name_pos = find_index(index, ["證券名稱", "股票名稱"])
        if code_pos is None or name_pos is None or max(code_pos, name_pos) >= len(row):
            continue
        code = str(row[code_pos]).strip()
        name = str(row[name_pos]).strip()
        if not code or not name or name in {"合計", "總計"}:
            continue

        foreign = get_value(index=index, row=row, candidates=[
            "外資及陸資買賣超金額", "外資買賣超金額", "外陸資買賣超金額"
        ])
        trust = get_value(index=index, row=row, candidates=["投信買賣超金額"])

        unit = "元"
        if find_index(index, ["外資及陸資買賣超金額", "外資買賣超金額", "外陸資買賣超金額"]) is None:
            foreign = get_value(index=index, row=row, candidates=[
                "外資及陸資買賣超股數", "外資買賣超股數", "外陸資買賣超股數"
            ])
            trust = get_value(index=index, row=row, candidates=["投信買賣超股數"])
            unit = "股"

        stocks.append({"code": code, "name": name, "foreign": foreign, "trust": trust, "unit": unit})
    return stocks


def fetch_twse_margin_day(ad_date):
    url = f"{TWSE_BASE}/rwd/zh/marginTrading/MI_MARGN?date={ad_date}&selectType=MS&response=json"
    data = fetch_json(url)
    if not data or data.get("stat") != "OK" or not returned_date_matches(data, ad_date):
        return None

    tables = data.get("tables", [])
    if not tables:
        return None

    result = {
        "margin_balance_yuan": 0.0,
        "margin_balance_lots": 0.0,
        "short_balance_lots": 0.0,
        "margin_yuan_change": 0.0,
        "margin_lots_change": 0.0,
        "short_lots_change": 0.0,
    }

    for row in tables[0].get("data", []):
        if len(row) < 6:
            continue
        label = str(row[0]).strip()
        prev, today = parse_num(row[4]), parse_num(row[5])
        if label == "融資金額(仟元)":
            result["margin_balance_yuan"] = today * 1000
            result["margin_yuan_change"] = (today - prev) * 1000
        elif label == "融資(交易單位)":
            result["margin_balance_lots"] = today
            result["margin_lots_change"] = today - prev
        elif label == "融券(交易單位)":
            result["short_balance_lots"] = today
            result["short_lots_change"] = today - prev
    return result


def fetch_twse_margin_stocks(ad_date):
    """抓取證交所當日個股融資融券明細，並計算前日/今日餘額與增減"""

    url = (
        f"{TWSE_BASE}/rwd/zh/marginTrading/"
        f"MI_MARGN?date={ad_date}&selectType=ALL&response=json"
    )

    data = fetch_json(url)

    if not data:
        return []

    if data.get("stat") != "OK":
        return []

    if not returned_date_matches(data, ad_date):
        return []

    # =====================================================
    # MI_MARGN 的個股資料位於 tables[1]
    # tables[0] 通常是市場信用交易統計
    # tables[1] 才是個股融資融券明細
    # =====================================================

    tables = data.get("tables", [])

    if len(tables) < 2:
        return []

    table = tables[1]

    fields = table.get("fields", [])
    rows = table.get("data", [])

    if not fields or not rows:
        return []

    # -----------------------------------------------------
    # TWSE 實際欄位：
    #
    # 0 代號
    # 1 名稱
    # 2 融資買進
    # 3 融資賣出
    # 4 融資現金償還
    # 5 融資前日餘額
    # 6 融資今日餘額
    # 7 融資次一營業日限額
    #
    # 8  融券買進
    # 9  融券賣出
    # 10 融券現券償還
    # 11 融券前日餘額
    # 12 融券今日餘額
    # 13 融券次一營業日限額
    #
    # 14 資券互抵
    # 15 註記
    # -----------------------------------------------------

    stocks = []

    for row in rows:

        if len(row) < 13:
            continue

        code = str(row[0]).strip()
        name = str(row[1]).strip()

        # 排除合計等非個股資料
        if not code or not name:
            continue

        if name in {"合計", "總計", "全市場"}:
            continue

        # 股票代號 / ETF / 特殊代號
        if not re.match(r"^[0-9A-Za-z]{4,6}$", code):
            continue

        # -----------------------------
        # 融資
        # -----------------------------
        margin_prev = parse_num(row[5])
        margin_today = parse_num(row[6])

        # -----------------------------
        # 融券
        # -----------------------------
        short_prev = parse_num(row[11])
        short_today = parse_num(row[12])

        # -----------------------------
        # 計算增減
        # -----------------------------
        margin_change = margin_today - margin_prev
        short_change = short_today - short_prev

        stocks.append({
            "code": code,
            "name": name,

            "margin_prev": margin_prev,
            "margin_today": margin_today,
            "margin_change": margin_change,

            "short_prev": short_prev,
            "short_today": short_today,
            "short_change": short_change,
        })

    return stocks


def fetch_twse_block_trades(ad_date):
    """抓取證交所當日盤後個股鉅額交易明細（依總成交金額由高至低排序）"""
    url = f"{TWSE_BASE}/rwd/zh/block/BFIAUU?date={ad_date}&selectType=S&response=json"
    data = fetch_json(url)
    if not data or data.get("stat") != "OK" or not returned_date_matches(data, ad_date):
        return []

    fields = data.get("fields", [])
    index = build_index(fields)
    trades = []

    for row in data.get("data", []):
        code_pos = find_index(index, ["證券代號", "股票代號"])
        name_pos = find_index(index, ["證券名稱", "股票名稱"])
        if code_pos is None or name_pos is None or max(code_pos, name_pos) >= len(row):
            continue

        code = str(row[code_pos]).strip()
        name = str(row[name_pos]).strip()
        if not code or not name or name in {"合計", "總計"}:
            continue

        # 抓取股數與總金額
        shares = get_value(row, index, ["成交股數", "股數"])
        amount = get_value(row, index, ["總成交金額", "成交金額", "金額"])

        # 擴充每股成交價的候選欄位
        price = get_value(row, index, ["每股成交價", "成交價格", "成交單價", "每股價格", "單價"])
        
        # 防呆機制：若欄位為 0，但有成交金額與股數，直接精準回推每股價格
        if price == 0.0 and shares > 0 and amount > 0:
            price = amount / shares

        # 交易方式欄位
        trade_type_pos = find_index(index, ["交易方式", "種類", "交易種類"])
        trade_type = str(row[trade_type_pos]).strip() if trade_type_pos is not None and trade_type_pos < len(row) else "配對交易"

        trades.append({
            "code": code,
            "name": name,
            "lots": shares / 1000,
            "price": price,
            "amount_bill": amount / 1e8,
            "trade_type": trade_type,
        })

    trades.sort(key=lambda x: x["amount_bill"], reverse=True)
    return trades


def is_valid_trading_day(ad_date):
    return fetch_twse_market_institutional(ad_date) is not None


def get_recent_trading_days(count, end_date):
    days = []
    cursor = end_date
    attempts = 0
    while len(days) < count and attempts < 30:
        attempts += 1
        if cursor.weekday() < 5:
            date_str = to_ad_compact(cursor)
            print(f"  -> 檢查 {cursor.strftime('%Y/%m/%d')} 是否有交易資料...", end="", flush=True)
            if is_valid_trading_day(date_str):
                print(" [開盤日]")
                days.append(cursor)
            else:
                print(" [無資料/休市]")
        cursor -= timedelta(days=1)
        time.sleep(random.uniform(0.3, 0.6))
    if len(days) < count:
        raise RuntimeError("無法取得足夠的有效交易日，請確認網路或以 --date 指定日期。")
    return list(reversed(days))


def rank_institutional_stocks(history, investor, days, top_n=10):
    dates = sorted(date for date, rows in history.items() if rows)
    if len(dates) < days:
        return {"available": False, "buy": [], "sell": [], "unit": None}

    selected = dates[-days:]
    aggregate = {}
    unit = None
    for date in selected:
        for stock in history[date]:
            unit = stock["unit"]
            record = aggregate.setdefault(stock["code"], {
                "code": stock["code"], "name": stock["name"], "value": 0.0
            })
            record["value"] += parse_num(stock.get(investor))

    values = list(aggregate.values())
    return {
        "available": True,
        "unit": unit,
        "buy": sorted((x for x in values if x["value"] > 0), key=lambda x: x["value"], reverse=True)[:top_n],
        "sell": sorted((x for x in values if x["value"] < 0), key=lambda x: x["value"])[:top_n],
    }


def append_rank_table(lines, title, rows, unit, direction):
    lines.append(f"#### {title}")
    lines.append("")
    label = "買賣超金額（億元）" if unit == "元" else "買賣超（張）"
    lines.append(f"| 排名 | 股票代號/名稱 | {label} |")
    lines.append("|---:|---|---:|")
    if not rows:
        lines.append("| - | 尚無可用資料 | - |")
    else:
        for rank, stock in enumerate(rows, 1):
            value = fmt_bill(stock["value"], signed=True) if unit == "元" else f"{stock['value'] / 1000:+,.0f}"
            lines.append(f"| {rank} | {stock['code']} {stock['name']} | {value} |")
    lines.append("")


def generate_report(trading_days, market_data, institutional_history, margin_data, margin_stocks, block_trades):
    current = trading_days[-1]
    lines = [
        f"# 台股盤後籌碼報告 — {current:%Y-%m-%d}",
        "",
        f"> 資料期間：{trading_days[0]:%Y/%m/%d} 至 {current:%Y/%m/%d}（最近五個有效交易日）",
        "> 資料來源：TWSE 官方公開資料；本報告僅涵蓋上市市場。",
        "",
        "## 一、連續五日三大法人買賣超金額（億元）",
        "",
        "| 日期 | 外資及陸資 | 投信 | 自營商自行買賣 | 自營商避險 | 合計 |",
        "|---|---:|---:|---:|---:|---:|",
    ]

    for day in trading_days:
        ad_date = to_ad_compact(day)
        item = market_data.get(ad_date)
        label = f"{day:%m/%d}（{to_roc(day)}）"
        if item is None:
            lines.append(f"| {label} | 尚未公布或資料無法取得 | - | - | - | - |")
        else:
            lines.append(
                f"| {label} | {fmt_bill(item['foreign'], True)} | {fmt_bill(item['trust'], True)} | "
                f"{fmt_bill(item['dealer_prop'], True)} | {fmt_bill(item['dealer_hedge'], True)} | {fmt_bill(item['total'], True)} |"
            )

    # ---------------- 二、當日盤後鉅額交易清單 ----------------
    lines += [
        "",
        "## 二、當日盤後鉅額交易清單",
        "",
        "依成交金額排序（上市市場）：",
        "",
        "| 股票代號/名稱 | 成交股數(張) | 每股成交價 | 總成交金額(億元) | 交易方式 |",
        "|---|---:|---:|---:|---|",
    ]
    if not block_trades:
        lines.append("| 尚無當日鉅額交易資料 | - | - | - | - |")
    else:
        for t in block_trades:
            lines.append(
                f"| {t['code']} {t['name']} | {t['lots']:,.0f} | {t['price']:.2f} | {t['amount_bill']:.2f} | {t['trade_type']} |"
            )
    lines.append("")

    # ---------------- 三、外資與投信排名 ----------------
    lines += ["## 三、外資與投信個股買賣超排名（上市 TWSE）", ""]
    for investor_name, investor_key in (("外資及陸資", "foreign"), ("投信", "trust")):
        lines += [f"### {investor_name}", ""]
        for period, days in (("當日", 1), ("連續 3 日", 3), ("連續 5 日", 5)):
            ranking = rank_institutional_stocks(institutional_history, investor_key, days)
            if not ranking["available"]:
                lines += [f"#### {period}", "", "資料不足或尚未公布。", ""]
                continue
            append_rank_table(lines, f"{period}買超前 10 名", ranking["buy"], ranking["unit"], "buy")
            append_rank_table(lines, f"{period}賣超前 10 名", ranking["sell"], ranking["unit"], "sell")

    # ---------------- 四、融資融券餘額 ----------------
    lines += ["## 四、連續五日融資融券餘額（上市 TWSE）", "", "| 日期 | 融資餘額（億元） | 較前日（億元） | 融資餘額（張） | 較前日（張） | 融券餘額（張） | 較前日（張） |", "|---|---:|---:|---:|---:|---:|---:|"]
    for day in trading_days:
        item = margin_data.get(to_ad_compact(day))
        label = f"{day:%m/%d}（{to_roc(day)}）"
        if item is None:
            lines.append(f"| {label} | 尚未公布或資料無法取得 | - | - | - | - | - |")
        else:
            lines.append(
                f"| {label} | {fmt_bill(item['margin_balance_yuan'])} | {fmt_bill(item['margin_yuan_change'], True)} | "
                f"{item['margin_balance_lots']:,.0f} | {item['margin_lots_change']:+,.0f} | "
                f"{item['short_balance_lots']:,.0f} | {item['short_lots_change']:+,.0f} |"
            )

    # ---------------- 五、融資融券增減排行 ----------------
    lines += ["", "## 五、當日融資融券餘額增減前十名個股（上市 TWSE）", ""]
    groups = [
        ("融資餘額增加前 10 名", "margin_change", True),
        ("融資餘額減少前 10 名", "margin_change", False),
        ("融券餘額增加前 10 名", "short_change", True),
        ("融券餘額減少前 10 名", "short_change", False),
    ]
    for title, key, positive in groups:
        rows = [stock for stock in margin_stocks if (stock[key] > 0 if positive else stock[key] < 0)]
        rows = sorted(rows, key=lambda x: x[key], reverse=positive)[:10]
        lines += [f"### {title}", "", "| 排名 | 股票代號/名稱 | 前日餘額（張） | 今日餘額（張） | 增減（張） |", "|---:|---|---:|---:|---:|"]
        if not rows:
            lines.append("| - | 尚未公布或資料無法取得 | - | - | - |")
        else:
            balance_prefix = "margin" if key == "margin_change" else "short"
            for rank, stock in enumerate(rows, 1):
                lines.append(f"| {rank} | {stock['code']} {stock['name']} | {stock[balance_prefix + '_prev']:,.0f} | {stock[balance_prefix + '_today']:,.0f} | {stock[key]:+,.0f} |")
        lines.append("")

    lines += [
        "---",
        f"*報告產生時間：{datetime.now():%Y-%m-%d %H:%M:%S}*",
        "*資料來源：臺灣證券交易所（TWSE）。本報告僅供研究參考，不構成投資建議。*",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="台股盤後籌碼報告產生器 v3")
    parser.add_argument("--date", help="指定最新交易日，格式 YYYY/MM/DD；預設今天")
    parser.add_argument("--output", help="指定 Markdown 輸出路徑")
    args = parser.parse_args()

    end_date = datetime.strptime(args.date, "%Y/%m/%d") if args.date else datetime.now()
    print(f"正在判斷有效交易日（基準日：{end_date.strftime('%Y/%m/%d')}）...")
    trading_days = get_recent_trading_days(5, end_date)
    print("有效交易日：", ", ".join(day.strftime("%Y/%m/%d") for day in trading_days))

    market_data = {}
    institutional_history = {}
    margin_data = {}

    for day in trading_days:
        ad_date = to_ad_compact(day)
        print(f"抓取 {day:%Y/%m/%d} 市場法人總表與個股法人資料...")
        market_data[ad_date] = fetch_twse_market_institutional(ad_date)
        time.sleep(random.uniform(0.8, 1.5))
        institutional_history[ad_date] = fetch_twse_institutional_stocks(ad_date)
        time.sleep(random.uniform(0.8, 1.5))
        margin_data[ad_date] = fetch_twse_margin_day(ad_date)
        time.sleep(random.uniform(0.8, 1.5))

    current_date = trading_days[-1]
    current_compact = to_ad_compact(current_date)

    print(f"抓取 {current_date:%Y/%m/%d} 個股融資融券資料...")
    margin_stocks = fetch_twse_margin_stocks(current_compact)
    time.sleep(random.uniform(0.8, 1.5))

    print(f"抓取 {current_date:%Y/%m/%d} 當日盤後鉅額交易資料...")
    block_trades = fetch_twse_block_trades(current_compact)

    report = generate_report(
        trading_days, market_data, institutional_history, margin_data, margin_stocks, block_trades
    )
    output = Path(args.output) if args.output else REPORT_DIR / f"{current_date:%Y-%m-%d}.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(f"報告已儲存：{output}")


if __name__ == "__main__":
    main()
