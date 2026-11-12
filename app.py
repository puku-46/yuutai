import hmac
import html
from datetime import date, datetime
from zoneinfo import ZoneInfo
 
import pandas as pd
import streamlit as st
from streamlit_gsheets import GSheetsConnection
 
# ---------------------------------------------------------
# ページ基本設定
# ---------------------------------------------------------
st.set_page_config(
    page_title="株主優待管理",
    page_icon="🎁",
    layout="wide",
    initial_sidebar_state="expanded",
)
 
# ---------------------------------------------------------
# 定数
# ---------------------------------------------------------
COLUMNS = [
    "ID", "銘柄コード", "銘柄名", "名義", "優待内容",
    "カテゴリ", "数量/金額", "権利確定月", "到着日",
    "有効期限", "ステータス", "メモ",
]
STATUS_LIST = ["未到着", "保有中（未使用）", "利用手続き中", "一部利用", "利用完了", "期限切れ"]
DONE_STATUSES = ("利用完了", "期限切れ")  # 期限アラートの対象外にするステータス
OWNERS = ["夫", "妻", "長男", "長女", "その他"]
CATEGORIES = ["飲食・お食事券", "買い物・割引券", "QUOカード・金券", "カタログギフト", "自社商品", "その他"]
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
 
 
if not check_password():
    st.stop()
 
# ---------------------------------------------------------
# Googleスプレッドシート接続・データ入出力
# ---------------------------------------------------------
conn = st.connection("gsheets", type=GSheetsConnection)
 
 
def normalize(raw: pd.DataFrame) -> pd.DataFrame:
    """空欄が 'nan' 文字列にならないよう整形する"""
    df = raw.copy()
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = ""
    df = df[COLUMNS].fillna("").astype(str)
    df = df.replace({"nan": "", "NaN": "", "None": "", "NaT": ""})
    # 数値として読まれた ID / 銘柄コード の「.0」を除去
    for c in ("ID", "銘柄コード"):
        df[c] = df[c].str.strip().str.replace(r"\.0$", "", regex=True)
    if not df.empty:
        df = df[~df.apply(lambda r: all(v.strip() == "" for v in r), axis=1)]
    return df.reset_index(drop=True)
 
 
def fetch_data():
    """(DataFrame, 成功フラグ, エラーメッセージ) を返す。失敗時に空DFを成功扱いにしない"""
    try:
        raw = conn.read(ttl=0)
    except Exception as e:
        return pd.DataFrame(columns=COLUMNS), False, str(e)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=COLUMNS), True, ""
    return normalize(raw), True, ""
 
 
def apply_and_save(modifier, success_message: str):
    """
    保存直前にシートの最新データを読み直し、その上で変更を適用して書き戻す。
    （読み込み失敗時は保存しない／他の家族の直前の変更を消しにくくする）
    """
    latest, ok, err = fetch_data()
    if not ok:
        st.error(f"最新データの取得に失敗したため、保存を中止しました（既存データは変更されていません）: {err}")
        return
    try:
        new_df = modifier(latest)
    except ValueError as e:
        st.error(str(e))
        return
    try:
        conn.update(data=new_df[COLUMNS])
    except Exception as e:
        st.error(f"スプレッドシートへの保存に失敗しました: {e}")
        return
    st.cache_data.clear()
    st.session_state["flash"] = success_message
    st.rerun()
 
 
# ---------------------------------------------------------
# メイン画面
# ---------------------------------------------------------
st.title("🎁 我が家の株主優待管理アプリ")
st.caption("Googleスプレッドシートとリアルタイム同期中！家族みんなで使える株主優待ダッシュボード")
 
# 保存後のメッセージ（rerunしても消えないようsession_stateで持ち越す）
if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))
 
df, load_ok, load_err = fetch_data()
if not load_ok:
    st.error(
        "スプレッドシートの読み込みに失敗しました。Secretsの設定や共有設定を確認してください。"
        f"\n\n詳細: {load_err}"
    )
    st.info("データ保護のため、読み込みに成功するまで登録・編集はできません。")
    if st.button("🔄 再読み込み"):
        st.cache_data.clear()
        st.rerun()
    st.stop()
 
today = today_jst()
 
# ---------------------------------------------------------
# サイドバー
# ---------------------------------------------------------
st.sidebar.header("🔍 フィルター & 操作")
 
selected_status = st.sidebar.selectbox("ステータスで絞り込み", ["すべて"] + STATUS_LIST)
 
owner_values = sorted({o for o in df["名義"] if o.strip()}) if not df.empty else []
selected_owner = st.sidebar.selectbox("名義で絞り込み", ["すべて"] + owner_values)
 
sort_mode = st.sidebar.selectbox("並び順", ["有効期限が近い順", "登録順（ID）"])
 
if st.sidebar.button("🔄 スプレッドシートの最新データを再読み込み"):
    st.cache_data.clear()
    st.rerun()
 
# 期限が過ぎているのに「利用完了/期限切れ」になっていないもの
overdue_ids = []
if not df.empty:
    for _, r in df.iterrows():
        exp = parse_date(r["有効期限"])
        if exp and exp < today and r["ステータス"] not in DONE_STATUSES:
            overdue_ids.append(r["ID"])
 
st.sidebar.divider()
if st.sidebar.button(
    f"⏰ 期限超過を「期限切れ」に更新（{len(overdue_ids)}件）",
    disabled=not overdue_ids,
):
    def mark_expired(d):
        d.loc[d["ID"].isin(overdue_ids), "ステータス"] = "期限切れ"
        return d
 
    apply_and_save(mark_expired, f"{len(overdue_ids)}件を「期限切れ」に更新しました。")
 
if st.sidebar.button("🔒 ログアウト"):
    st.session_state.clear()
    st.rerun()
 
#
