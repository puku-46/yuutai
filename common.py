"""
全ページ共通の処理（認証・Googleスプレッドシート接続・ユーティリティ）。
優待 / 保有株管理 / 解析 の各ページから import して使う。
"""
import hmac
import html
from datetime import date, datetime
from zoneinfo import ZoneInfo
 
import pandas as pd
import streamlit as st
from streamlit_gsheets import GSheetsConnection
 
MAX_LOGIN_ATTEMPTS = 5
 
 
# ---------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------
def today_jst() -> date:
    """Streamlit CloudはUTCで動くため、日本時間の「今日」を返す"""
    return datetime.now(ZoneInfo("Asia/Tokyo")).date()
 
 
def parse_date(value):
    """'2027-12-31' 形式（時刻付きでも可）を date に変換。失敗・空欄は None"""
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
 
 
def esc(value) -> str:
    """HTMLエスケープ（カード表示用）"""
    return html.escape(str(value))
 
 
def get_secret(key):
    try:
        return st.secrets.get(key, None)
    except Exception:
        return None
 
 
# ---------------------------------------------------------
# 🔒 ログイン・パスワード認証
# ---------------------------------------------------------
def check_password() -> bool:
    correct_password = get_secret("PASSWORD")
    if not correct_password:
        st.error(
            "パスワードが設定されていません。Streamlit CloudのSecretsに "
            "`PASSWORD = \"任意のパスワード\"` を追加してください。"
        )
        return False
 
    if st.session_state.get("password_correct"):
        return True
 
    def password_entered():
        if st.session_state.get("fail_count", 0) >= MAX_LOGIN_ATTEMPTS:
            return
        entered = str(st.session_state.get("password_input", ""))
        if hmac.compare_digest(entered.encode("utf-8"), str(correct_password).encode("utf-8")):
            st.session_state["password_correct"] = True
            st.session_state["fail_count"] = 0
        else:
            st.session_state["password_correct"] = False
            st.session_state["fail_count"] = st.session_state.get("fail_count", 0) + 1
        st.session_state.pop("password_input", None)  # メモリから消去
 
    st.markdown("### 🔒 ログインが必要です")
    st.caption("ご家族専用のパスワードを入力してください。")
 
    if st.session_state.get("fail_count", 0) >= MAX_LOGIN_ATTEMPTS:
        st.error("❌ 試行回数の上限に達しました。ブラウザを再読み込みしてやり直してください。")
        return False
 
    st.text_input("パスワード", type="password", on_change=password_entered, key="password_input")
    if st.session_state.get("password_correct") is False:
        st.error("❌ パスワードが違います")
    return False
 
 
def setup_page(title: str, icon: str, layout: str = "wide"):
    """
    各ページの先頭で必ず呼ぶ。
    ページ設定 → ログイン確認（URL直打ちでも必ず認証）→ ログアウトボタン → 保存完了メッセージ
    """
    st.set_page_config(
        page_title=title,
        page_icon=icon,
        layout=layout,
        initial_sidebar_state="expanded",
    )
    if not check_password():
        st.stop()
 
    if st.sidebar.button("🔒 ログアウト"):
        st.session_state.clear()
        st.rerun()
 
    # 保存後のメッセージ（rerunしても消えないようsession_stateで持ち越す）
    if "flash" in st.session_state:
        st.success(st.session_state.pop("flash"))
 
 
# ---------------------------------------------------------
# Googleスプレッドシート入出力（シート名を指定して使う）
# ---------------------------------------------------------
def get_connection():
    return st.connection("gsheets", type=GSheetsConnection)
 
 
def normalize(raw: pd.DataFrame, columns: list) -> pd.DataFrame:
    """空欄が 'nan' 文字列にならないよう整形する"""
    df = raw.copy()
    for c in columns:
        if c not in df.columns:
            df[c] = ""
    df = df[columns].fillna("").astype(str)
    df = df.replace({"nan": "", "NaN": "", "None": "", "NaT": ""})
    # 数値として読まれた ID / 銘柄コード の「.0」を除去
    for c in ("ID", "銘柄コード"):
        if c in df.columns:
            df[c] = df[c].str.strip().str.replace(r"\.0$", "", regex=True)
    if not df.empty:
        df = df[~df.apply(lambda r: all(v.strip() == "" for v in r), axis=1)]
    return df.reset_index(drop=True)
 
 
def fetch_sheet(worksheet: str, columns: list):
    """(DataFrame, 成功フラグ, エラーメッセージ)。失敗時に空DFを成功扱いにしない"""
    try:
        raw = get_connection().read(worksheet=worksheet, ttl=0)
    except Exception as e:
        return pd.DataFrame(columns=columns), False, str(e)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=columns), True, ""
    return normalize(raw, columns), True, ""
 
 
def apply_and_save(worksheet: str, columns: list, modifier, success_message: str):
    """
    保存直前にシートの最新データを読み直し、その上で変更を適用して書き戻す。
    （読み込み失敗時は保存しない／他の家族の直前の変更を消しにくくする）
    """
    latest, ok, err = fetch_sheet(worksheet, columns)
    if not ok:
        st.error(f"最新データの取得に失敗したため、保存を中止しました（既存データは変更されていません）: {err}")
        return
    try:
        new_df = modifier(latest)
    except ValueError as e:
        st.error(str(e))
        return
    try:
        get_connection().update(worksheet=worksheet, data=new_df[columns])
    except Exception as e:
        st.error(f"スプレッドシートへの保存に失敗しました: {e}")
        return
    st.cache_data.clear()
    st.session_state["flash"] = success_message
    st.rerun()
 
