"""株価アラート通知(前場引け・後場引けに定時実行する想定)
 
動作:
  1. スプレッドシートの settings / holdings / watch を読む
  2. 名義ごとに、前日比がしきい値(%)以上の銘柄を抽出(上昇・下落どちらも)
  3. 該当があった名義にだけ、1通にまとめて Make(Webhook) 経由でLINE送信
 
使い方:
  python notify.py --dry-run          # 送信せず、内容を画面に表示(まずこれで確認)
  python notify.py --dry-run --force  # 休場日・時間外でも判定する(テスト用)
  python notify.py                    # 本番(LINEへ送信)
 
環境変数:
  MAKE_WEBHOOK_URL            MakeのWebhook URL(本番送信時に必須)
  GCP_SERVICE_ACCOUNT_JSON    サービスアカウントのJSONの中身(未設定なら service_account.json を読む)
  SPREADSHEET_URL            スプレッドシートのURL(推奨。画面アプリのSecretsと同じもの)
  SPREADSHEET_NAME            URLを使わない場合のスプレッドシート名(省略時は「優待管理」)
 
必要: pip install gspread yfinance pandas requests
"""
import argparse
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
 
import gspread
import pandas as pd
import requests
import yfinance as yf
 
JST = ZoneInfo("Asia/Tokyo")
SPREADSHEET_URL = os.environ.get("SPREADSHEET_URL", "")
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "優待管理")
WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL", "")
 
DEFAULT_MEMBERS = ["夫", "妻", "長男", "長女", "その他"]  # ページ2(2_stocks.py)と同じ
DEFAULT_THRESHOLD = 3.0
COLUMNS = {
    "holdings": ["名義", "銘柄コード", "銘柄名", "取得単価", "株数"],
    "watch": ["名義", "銘柄コード", "銘柄名", "目標価格"],
    "settings": ["名義", "しきい値(%)", "通知ON"],
}
NUMERIC_COLUMNS = ["取得単価", "株数", "目標価格", "しきい値(%)"]
KIND_LABEL = {"holdings": "保有", "watch": "注目"}
 
 
# ------------------------------------------------------------
# スプレッドシート読み込み
# ------------------------------------------------------------
def open_spreadsheet():
    info = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
    if info:
        client = gspread.service_account_from_dict(json.loads(info))
    else:
        client = gspread.service_account(filename="service_account.json")
    return client.open_by_url(SPREADSHEET_URL) if SPREADSHEET_URL else client.open(SPREADSHEET_NAME)
 
 
def read_tab(ss, kind: str) -> pd.DataFrame:
    cols = COLUMNS[kind]
    values = ss.worksheet(kind).get_all_values()
 
    if len(values) < 2:  # ヘッダーのみ・空のタブ
        if kind == "settings":  # 画面アプリと同じ初期値
            return pd.DataFrame(
                {"名義": DEFAULT_MEMBERS, "しきい値(%)": DEFAULT_THRESHOLD, "通知ON": True}
            )
        return pd.DataFrame(columns=cols)
 
    header, body = values[0], values[1:]
    missing = [c for c in cols if c not in header]
    if missing:
        raise ValueError(f"「{kind}」タブの1行目に次の列が必要です: {', '.join(missing)}")
 
    df = pd.DataFrame(body, columns=header)[cols]
    df = df[(df != "").any(axis=1)].reset_index(drop=True)
    for c in ["名義", "銘柄コード", "銘柄名"]:
        if c in df:
            df[c] = df[c].astype(str).str.strip()
    for c in NUMERIC_COLUMNS:
        if c in df:
            df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ""), errors="coerce")
    if "通知ON" in df:
        df["通知ON"] = df["通知ON"].astype(str).str.strip().str.upper().isin(["TRUE", "1", "はい", "ON"])
    return df
 
 
# ------------------------------------------------------------
# 株価取得・休場判定
# ------------------------------------------------------------
def fetch_prices(codes) -> dict:
    """銘柄コード -> {'last': 現在値, 'prev': 前日終値}。取得できない銘柄は含めない。"""
    prices = {}
    for code in sorted(set(codes)):
        try:
            closes = yf.Ticker(code).history(period="5d")["Close"].dropna()
            if len(closes) >= 2:
                prices[code] = {"last": float(closes.iloc[-1]), "prev": float(closes.iloc[-2])}
        except Exception as e:  # 1銘柄の失敗で全体を止めない
            print(f"[警告] {code} の株価を取得できませんでした: {e}", file=sys.stderr)
    return prices
 
 
def market_is_open_today() -> bool:
    """基準銘柄の最新の足が今日の日付なら、今日は取引があった(休場日ではない)と判断する。"""
    hist = yf.Ticker("7203.T").history(period="5d")
    return (not hist.empty) and hist.index[-1].date() == datetime.now(JST).date()
 
 
# ------------------------------------------------------------
# 判定とメッセージ作成(ネットワーク不要の部分)
# ------------------------------------------------------------
def fmt_price(v: float) -> str:
    return f"{v:,.1f}".rstrip("0").rstrip(".")
 
 
def collect_alerts(df: pd.DataFrame, kind: str, threshold: float, prices: dict) -> list:
    alerts = []
    for _, r in df.iterrows():
        p = prices.get(r["銘柄コード"])
        if not p or not p["prev"]:
            continue
        pct = (p["last"] - p["prev"]) / p["prev"] * 100
        if abs(pct) >= threshold:
            alerts.append((kind, r["銘柄名"], r["銘柄コード"], p["last"], pct))
    return alerts
 
 
def build_messages(session: str, holdings, watch, settings, prices: dict, now=None) -> dict:
    """名義 -> 送信メッセージ。該当銘柄がない名義・通知OFFの名義は含めない。"""
    now = now or datetime.now(JST)
    messages = {}
    for _, s in settings[settings["通知ON"]].iterrows():
        owner, threshold = s["名義"], float(s["しきい値(%)"])
        alerts = collect_alerts(holdings[holdings["名義"] == owner], "holdings", threshold, prices)
        alerts += collect_alerts(watch[watch["名義"] == owner], "watch", threshold, prices)
        if not alerts:
            continue
        alerts.sort(key=lambda a: abs(a[4]), reverse=True)  # 動きが大きい順
        lines = [f"📈 株価アラート {now:%m/%d} {session}(前日比±{threshold:g}%以上)"]
        for kind, name, code, last, pct in alerts:
            lines.append(f"【{KIND_LABEL[kind]}】{name}({code.removesuffix('.T')}) {fmt_price(last)}円 {pct:+.1f}%")
        messages[owner] = "\n".join(lines)
    return messages
 
 
# ------------------------------------------------------------
# 送信
# ------------------------------------------------------------
def send_line(owner: str, text: str) -> None:
    """Make の Webhook に {owner, message} をPOSTする。
    MakeのシナリオでownerごとにLINEの宛先を振り分ける想定(ここは既存の送信方法に合わせて調整)。"""
    res = requests.post(WEBHOOK_URL, json={"owner": owner, "message": text}, timeout=15)
    res.raise_for_status()
 
 
# ------------------------------------------------------------
# メイン
# ------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="送信せず内容を表示する")
    ap.add_argument("--force", action="store_true", help="休場日チェックを行わない")
    ap.add_argument("--session", help="メッセージの見出し(省略時は時刻から自動:前場引け/後場引け)")
    args = ap.parse_args(argv)
 
    now = datetime.now(JST)
    session = args.session or ("前場引け" if now.hour < 13 else "後場引け")
 
    if not args.force and not market_is_open_today():
        print("今日は休場日(または株価データなし)のため、通知しません。")
        return 0
 
    ss = open_spreadsheet()
    settings = read_tab(ss, "settings")
    holdings = read_tab(ss, "holdings")
    watch = read_tab(ss, "watch")
 
    active = set(settings.loc[settings["通知ON"], "名義"])
    codes = list(holdings.loc[holdings["名義"].isin(active), "銘柄コード"])
    codes += list(watch.loc[watch["名義"].isin(active), "銘柄コード"])
    prices = fetch_prices(codes)
 
    messages = build_messages(session, holdings, watch, settings, prices, now)
    if not messages:
        print("しきい値を超えた銘柄はありませんでした。")
        return 0
 
    failed = 0
    for owner, text in messages.items():
        if args.dry_run or not WEBHOOK_URL:
            print(f"--- {owner} 宛て{'(dry-run)' if args.dry_run else '(MAKE_WEBHOOK_URL未設定のため表示のみ)'} ---\n{text}\n")
            continue
        try:
            send_line(owner, text)
            print(f"{owner} 宛てに送信しました。")
        except Exception as e:
            failed += 1
            print(f"[エラー] {owner} 宛ての送信に失敗: {e}", file=sys.stderr)
    return 1 if failed else 0
 
 
if __name__ == "__main__":
    sys.exit(main())
 
