import streamlit as st
import pandas as pd
from datetime import datetime, date
from streamlit_gsheets import GSheetsConnection

# ページ基本設定
st.set_page_config(
    page_title="我が家の株主優待管理",
    page_icon="🎁",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.title("🎁 我が家の株主優待管理アプリ")
st.caption("家族全員でいつでも・どこでも共有・管理できる株主優待ダッシュボード")

# Googleスプレッドシートへの接続設定
# ※ローカルテスト時はSQLiteまたはPandasデータフレームにフォールバック可能
@st.cache_resource
def get_connection():
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        return conn
    except Exception as e:
        st.warning("Googleスプレッドシート接続未設定のため、ローカルデータモードで動作します。")
        return None

conn = get_connection()

# データ取得用関数
def load_data():
    if conn:
        try:
            df = conn.read(ttl="0d")
            # 空データの場合の標準カラム設定
            if df.empty:
                df = pd.DataFrame(columns=[
                    "ID", "銘柄コード", "銘柄名", "名義", "優待内容", 
                    "カテゴリ", "数量/金額", "権利確定月", "到着日", 
                    "有効期限", "ステータス", "メモ"
                ])
            return df
        except Exception:
            pass
    
    # セッション状態での保持（フォールバック）
    if "data" not in st.session_state:
        st.session_state["data"] = pd.DataFrame([
            {
                "ID": "1",
                "銘柄コード": "7550",
                "銘柄名": "ゼンショーHD",
                "名義": "夫",
                "優待内容": "食事ご優待券 3,000円分",
                "カテゴリ": "飲食・お食事券",
                "数量/金額": "3000円",
                "権利確定月": "3月/9月",
                "到着日": "2026-06-25",
                "有効期限": "2026-12-31",
                "ステータス": "保有中（未使用）",
                "メモ": "すき家、はま寿司で利用可能"
            },
            {
                "ID": "2",
                "銘柄コード": "8591",
                "銘柄名": "オリックス",
                "名義": "妻",
                "優待内容": "ふるさと優待（カタログ）",
                "カテゴリ": "カタログギフト",
                "数量/金額": "1個",
                "権利確定月": "3月",
                "到着日": "2026-07-01",
                "有効期限": "2026-10-31",
                "ステータス": "利用手続き中",
                "メモ": "ハンバーグセット申込済み"
            }
        ])
    return st.session_state["data"]

# データ保存関数
def save_data(df):
    if conn:
        try:
            conn.update(data=df)
            st.success("データがクラウドに保存されました！")
            st.cache_data.clear()
            return
        except Exception as e:
            st.error(f"スプレッドシートへの保存に失敗しました: {e}")
    
    st.session_state["data"] = df
    st.success("ローカルデータを更新しました。")

df = load_data()

# ---------------------------------------------------------
# サイドバー：新規登録 & フィルター
# ---------------------------------------------------------
st.sidebar.header("🔍 フィルター & 操作")

status_list = ["すべて", "未到着", "保有中（未使用）", "利用手続き中", "一部利用", "利用完了", "期限切れ"]
selected_status = st.sidebar.selectbox("ステータスで絞り込み", status_list)

owner_list = ["すべて"] + list(df["名義"].unique()) if not df.empty and "名義" in df.columns else ["すべて"]
selected_owner = st.sidebar.selectbox("名義で絞り込み", owner_list)

# フィルタリング
filtered_df = df.copy()
if selected_status != "すべて":
    filtered_df = filtered_df[filtered_df["ステータス"] == selected_status]
if selected_owner != "すべて":
    filtered_df = filtered_df[filtered_df["名義"] == selected_owner]

# ---------------------------------------------------------
# メインコンテンツ：サマリー KPI
# ---------------------------------------------------------
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("総登録銘柄数", f"{len(df)} 件")
with col2:
    unused_count = len(df[df["ステータス"].isin(["未到着", "保有中（未使用）"])])
    st.metric("未使用・到着待ち優待", f"{unused_count} 件")
with col3:
    # 有効期限注意（30日以内）
    today = date.today()
    expiring_soon = 0
    if not df.empty and "有効期限" in df.columns:
        for exp in df["有効期限"].dropna():
            try:
                exp_date = datetime.strptime(str(exp), "%Y-%m-%d").date()
                if 0 <= (exp_date - today).days <= 30:
                    expiring_soon += 1
            except ValueError:
                pass
    st.metric("⏰ 30日以内に期限切れ", f"{expiring_soon} 件", delta_color="inverse")
with col4:
    completed_count = len(df[df["ステータス"] == "利用完了"])
    st.metric("利用完了", f"{completed_count} 件")

st.divider()

# タブ構成（一覧、新規追加、編集・削除、分析）
tab1, tab2, tab3, tab4 = st.tabs(["📋 優待一覧", "➕ 新規登録", "✏️ 編集・削除", "📊 カテゴリ・統計"])

# ---------------------------------------------------------
# TAB 1: 優待一覧
# ---------------------------------------------------------
with tab1:
    st.subheader("保有・獲得株主優待 一覧")
    
    if filtered_df.empty:
        st.info("該当するデータがありません。")
    else:
        # カード形式表示切り替えチェック
        view_mode = st.radio("表示モード", ["カード表示", "テーブル表示"], horizontal=True)
        
        if view_mode == "テーブル表示":
            st.dataframe(filtered_df, use_container_width=True)
        else:
            # カード形式
            cols = st.columns(3)
            for idx, row in filtered_df.reset_index(drop=True).iterrows():
                with cols[idx % 3]:
                    st.markdown(f"""
                    <div style="border:1px solid #ddd; border-radius:10px; padding:15px; margin-bottom:15px; background-color:#f9f9f9;">
                        <h4>[{row.get('銘柄コード', '')}] {row.get('銘柄名', '')}</h4>
                        <p>👤 <b>名義:</b> {row.get('名義', '')}</p>
                        <p>🎁 <b>内容:</b> {row.get('優待内容', '')}</p>
                        <p>🏷️ <b>カテゴリ:</b> {row.get('カテゴリ', '')}</p>
                        <p>📅 <b>有効期限:</b> <span style="color:red; font-weight:bold;">{row.get('有効期限', 'なし')}</span></p>
                        <p>📌 <b>ステータス:</b> {row.get('ステータス', '')}</p>
                        <p>📝 <i>{row.get('メモ', '')}</i></p>
                    </div>
                    """, unsafe_allow_html=True)

# ---------------------------------------------------------
# TAB 2: 新規登録
# ---------------------------------------------------------
with tab2:
    st.subheader("新しい株主優待情報を登録")
    with st.form("add_form", clear_on_submit=True):
        col_a, col_b = st.columns(2)
        with col_a:
            code = st.text_input("銘柄コード (例: 7550)")
            name = st.text_input("銘柄名 (例: ゼンショーHD)")
            owner = st.selectbox("名義", ["夫", "妻", "長男", "長女", "その他"])
            category = st.selectbox("カテゴリ", ["飲食・お食事券", "買い物・割引券", "QUOカード・金券", "カタログギフト", "自社商品", "その他"])
            value = st.text_input("数量/金額 (例: 3,000円分 / 1セット)")
        with col_b:
            rights_month = st.text_input("権利確定月 (例: 3月/9月)")
            arrival_date = st.date_input("到着日 / 到着予定日", value=date.today())
            expiry_date = st.date_input("有効期限 (なしの場合は遠い未来を入力)", value=date(2027, 12, 31))
            status = st.selectbox("ステータス", ["未到着", "保有中（未使用）", "利用手続き中", "一部利用", "利用完了", "期限切れ"])
            memo = st.text_area("メモ・使い道など")
            gift_detail = st.text_input("優待内容詳細 (例: 食事優待券 500円×6枚)")

        submit_btn = st.form_submit_button("優待情報を追加する")
        
        if submit_btn:
            new_id = str(int(df["ID"].max()) + 1) if not df.empty and "ID" in df.columns and str(df["ID"].max()).isdigit() else "1"
            new_row = {
                "ID": new_id,
                "銘柄コード": code,
                "銘柄名": name,
                "名義": owner,
                "優待内容": gift_detail,
                "カテゴリ": category,
                "数量/金額": value,
                "権利確定月": rights_month,
                "到着日": str(arrival_date),
                "有効期限": str(expiry_date),
                "ステータス": status,
                "メモ": memo
            }
            updated_df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            save_data(updated_df)
            st.experimental_rerun()

# ---------------------------------------------------------
# TAB 3: 編集・削除
# ---------------------------------------------------------
with tab3:
    st.subheader("登録済み優待の更新・削除")
    if df.empty:
        st.info("編集対象のデータがありません。")
    else:
        selected_id = st.selectbox("編集・削除する優待を選択", df["ID"] + " : [" + df["銘柄コード"] + "] " + df["銘柄名"] + " (" + df["名義"] + ")")
        target_id = selected_id.split(" : ")[0]
        target_row = df[df["ID"] == target_id].iloc[0]

        with st.form("edit_form"):
            col_ea, col_eb = st.columns(2)
            with col_ea:
                e_code = st.text_input("銘柄コード", value=str(target_row.get("銘柄コード", "")))
                e_name = st.text_input("銘柄名", value=str(target_row.get("銘柄名", "")))
                e_owner = st.text_input("名義", value=str(target_row.get("名義", "")))
                e_category = st.text_input("カテゴリ", value=str(target_row.get("カテゴリ", "")))
                e_value = st.text_input("数量/金額", value=str(target_row.get("数量/金額", "")))
            with col_eb:
                e_rights_month = st.text_input("権利確定月", value=str(target_row.get("権利確定月", "")))
                e_arrival_date = st.text_input("到着日", value=str(target_row.get("到着日", "")))
                e_expiry_date = st.text_input("有効期限", value=str(target_row.get("有効期限", "")))
                e_status = st.selectbox("ステータス", ["未到着", "保有中（未使用）", "利用手続き中", "一部利用", "利用完了", "期限切れ"], index=["未到着", "保有中（未使用）", "利用手続き中", "一部利用", "利用完了", "期限切れ"].index(target_row.get("ステータス", "保有中（未使用）")) if target_row.get("ステータス") in ["未到着", "保有中（未使用）", "利用手続き中", "一部利用", "利用完了", "期限切れ"] else 0)
                e_memo = st.text_area("メモ", value=str(target_row.get("メモ", "")))
                e_gift_detail = st.text_input("優待内容詳細", value=str(target_row.get("優待内容", "")))

            col_btn1, col_btn2 = st.columns([1, 1])
            with col_btn1:
                update_btn = st.form_submit_button("✏️ 変更内容を保存")
            with col_btn2:
                delete_btn = st.form_submit_button("🗑️ データを削除", type="primary")

            if update_btn:
                df.loc[df["ID"] == target_id, ["銘柄コード", "銘柄名", "名義", "優待内容", "カテゴリ", "数量/金額", "権利確定月", "到着日", "有効期限", "ステータス", "メモ"]] = [
                    e_code, e_name, e_owner, e_gift_detail, e_category, e_value, e_rights_month, e_arrival_date, e_expiry_date, e_status, e_memo
                ]
                save_data(df)
                st.experimental_rerun()

            if delete_btn:
                updated_df = df[df["ID"] != target_id]
                save_data(updated_df)
                st.experimental_rerun()

# ---------------------------------------------------------
# TAB 4: 統計・グラフ
# ---------------------------------------------------------
with tab4:
    st.subheader("ポートフォリオ・カテゴリ集計")
    if not df.empty:
        col_g1, col_g2 = st.columns(2)
        with col_g1:
            st.write("▼ カテゴリ別割合")
            cat_counts = df["カテゴリ"].value_counts()
            st.bar_chart(cat_counts)
        with col_g2:
            st.write("▼ 名義別所有比率")
            owner_counts = df["名義"].value_counts()
            st.bar_chart(owner_counts)
