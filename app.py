import streamlit as st
import pandas as pd
from datetime import datetime, date
from streamlit_gsheets import GSheetsConnection

# ---------------------------------------------------------
# ページ基本設定
# ---------------------------------------------------------
st.set_page_config(
    page_title="株主優待管理",
    page_icon="🎁",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ---------------------------------------------------------
# 🔒 ログイン・パスワード認証機能
# ---------------------------------------------------------
def check_password():
    """パスワードが正しいか確認する関数"""
    def password_entered():
        # Streamlit CloudのSecretsに設定されたパスワード（無ければデフォルト 'yutai2026'）
        correct_password = st.secrets.get("PASSWORD", "yutai2026")
        
        if st.session_state["password_input"] == correct_password:
            st.session_state["password_correct"] = True
            del st.session_state["password_input"]  # メモリから消去
        else:
            st.session_state["password_correct"] = False

    # 初回アクセス時
    if "password_correct" not in st.session_state:
        st.markdown("### 🔒 ログインが必要です")
        st.caption("ご家族専用のパスワードを入力してください。")
        st.text_input("パスワード", type="password", on_change=password_entered, key="password_input")
        return False

    # パスワードが間違っている場合
    elif not st.session_state["password_correct"]:
        st.markdown("### 🔒 ログインが必要です")
        st.caption("ご家族専用のパスワードを入力してください。")
        st.text_input("パスワード", type="password", on_change=password_entered, key="password_input")
        st.error("❌ パスワードが違います")
        return False

    # パスワードが正しい場合
    else:
        return True

# パスワード認証をチェック（未認証ならここで処理停止）
if not check_password():
    st.stop()

# ---------------------------------------------------------
# メインアプリケーション（認証成功後に実行される）
# ---------------------------------------------------------
st.title("🎁 我が家の株主優待管理アプリ")
st.caption("Googleスプレッドシートとリアルタイム同期中！家族みんなで使える株主優待ダッシュボード")

# ログアウトボタンをサイドバーに追加
if st.sidebar.button("🔒 ログアウト"):
    del st.session_state["password_correct"]
    st.rerun()  # 修正箇所 1

# Googleスプレッドシートへの接続
@st.cache_resource
def get_connection():
    return st.connection("gsheets", type=GSheetsConnection)

conn = get_connection()

# データ取得
def load_data():
    try:
        df = conn.read(ttl="0d")
        if df is None or df.empty:
            df = pd.DataFrame(columns=[
                "ID", "銘柄コード", "銘柄名", "名義", "優待内容", 
                "カテゴリ", "数量/金額", "権利確定月", "到着日", 
                "有効期限", "ステータス", "メモ"
            ])
        else:
            df = df.astype(str)
        return df
    except Exception as e:
        st.error(f"スプレッドシートの読み込みに失敗しました。Secretsの設定を確認してください: {e}")
        return pd.DataFrame()

# データ保存
def save_data(df):
    try:
        conn.update(data=df)
        st.success("Googleスプレッドシートに同期されました！")
        st.cache_data.clear()
    except Exception as e:
        st.error(f"スプレッドシートへの保存に失敗しました: {e}")

df = load_data()

# ---------------------------------------------------------
# サイドバー：フィルター & 操作
# ---------------------------------------------------------
st.sidebar.header("🔍 フィルター & 操作")

status_list = ["すべて", "未到着", "保有中（未使用）", "利用手続き中", "一部利用", "利用完了", "期限切れ"]
selected_status = st.sidebar.selectbox("ステータスで絞り込み", status_list)

owner_list = ["すべて"] + (list(df["名義"].dropna().unique()) if not df.empty and "名義" in df.columns else [])
selected_owner = st.sidebar.selectbox("名義で絞り込み", owner_list)

if st.sidebar.button("🔄 スプレッドシートの最新データを再読み込み"):
    st.cache_data.clear()
    st.rerun()  # 修正箇所 2

# フィルタリング
filtered_df = df.copy()
if not filtered_df.empty:
    if selected_status != "すべて":
        filtered_df = filtered_df[filtered_df["ステータス"] == selected_status]
    if selected_owner != "すべて":
        filtered_df = filtered_df[filtered_df["名義"] == selected_owner]

# ---------------------------------------------------------
# KPIサマリー
# ---------------------------------------------------------
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("総登録銘柄数", f"{len(df)} 件")
with col2:
    unused_count = len(df[df["ステータス"].isin(["未到着", "保有中（未使用）"])]) if not df.empty and "ステータス" in df.columns else 0
    st.metric("未使用・到着待ち優待", f"{unused_count} 件")
with col3:
    today = date.today()
    expiring_soon = 0
    if not df.empty and "有効期限" in df.columns:
        for exp in df["有効期限"].dropna():
            try:
                exp_date = datetime.strptime(str(exp).strip(), "%Y-%m-%d").date()
                if 0 <= (exp_date - today).days <= 30:
                    expiring_soon += 1
            except ValueError:
                pass
    st.metric("⏰ 30日以内に期限切れ", f"{expiring_soon} 件")
with col4:
    completed_count = len(df[df["ステータス"] == "利用完了"]) if not df.empty and "ステータス" in df.columns else 0
    st.metric("利用完了", f"{completed_count} 件")

st.divider()

# タブ構成
tab1, tab2, tab3, tab4 = st.tabs(["📋 優待一覧", "➕ 新規登録", "✏️ 編集・削除", "📊 カテゴリ・統計"])

# TAB 1: 優待一覧
with tab1:
    st.subheader("保有・獲得株主優待 一覧")
    if filtered_df.empty:
        st.info("該当するデータがありません。スプレッドシートにデータを入力するか、新規登録タブから追加してください。")
    else:
        view_mode = st.radio("表示モード", ["カード表示", "テーブル表示"], horizontal=True)
        if view_mode == "テーブル表示":
            st.dataframe(filtered_df, use_container_width=True)
        else:
            cols = st.columns(3)
            for idx, row in filtered_df.reset_index(drop=True).iterrows():
                with cols[idx % 3]:
                    st.markdown(f"""
                    <div style="border:1px solid #ddd; border-radius:10px; padding:15px; margin-bottom:15px; background-color:#ffffff; box-shadow: 2px 2px 5px rgba(0,0,0,0.05);">
                        <h4 style="margin-top:0;">[{row.get('銘柄コード', '')}] {row.get('銘柄名', '')}</h4>
                        <p>👤 <b>名義:</b> {row.get('名義', '')}</p>
                        <p>🎁 <b>内容:</b> {row.get('優待内容', '')}</p>
                        <p>🏷️ <b>カテゴリ:</b> {row.get('カテゴリ', '')}</p>
                        <p>📅 <b>有効期限:</b> <span style="color:#d9534f; font-weight:bold;">{row.get('有効期限', 'なし')}</span></p>
                        <p>📌 <b>ステータス:</b> {row.get('ステータス', '')}</p>
                        <p>📝 <i>{row.get('メモ', '')}</i></p>
                    </div>
                    """, unsafe_allow_html=True)

# TAB 2: 新規登録
with tab2:
    st.subheader("新しい株主優待情報を登録")
    with st.form("add_form", clear_on_submit=True):
        col_a, col_b = st.columns(2)
        with col_a:
            code = st.text_input("銘柄コード (例: 7550)")
            name = st.text_input("銘柄名 (例: ゼンショーHD)")
            owner = st.selectbox("名義", ["夫", "妻", "長男", "長女", "その他"])
            category = st.selectbox("カテゴリ", ["飲食・お食事券", "買い物・割引券", "QUOカード・金券", "カタログギフト", "自社商品", "その他"])
            value = st.text_input("数量/金額 (例: 3,000円分)")
        with col_b:
            rights_month = st.text_input("権利確定月 (例: 3月/9月)")
            arrival_date = st.date_input("到着日 / 到着予定日", value=date.today())
            expiry_date = st.date_input("有効期限", value=date(2027, 12, 31))
            status = st.selectbox("ステータス", ["未到着", "保有中（未使用）", "利用手続き中", "一部利用", "利用完了", "期限切れ"])
            memo = st.text_area("メモ・使い道など")
            gift_detail = st.text_input("優待内容詳細 (例: 食事優待券 500円×6枚)")

        submit_btn = st.form_submit_button("優待情報を追加する")
        
        if submit_btn:
            if not df.empty and "ID" in df.columns:
                valid_ids = [int(i) for i in df["ID"] if str(i).isdigit()]
                new_id = str(max(valid_ids) + 1) if valid_ids else "1"
            else:
                new_id = "1"

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
            st.rerun()  # 修正箇所 3

# TAB 3: 編集・削除
with tab3:
    st.subheader("登録済み優待の更新・削除")
    if df.empty:
        st.info("編集対象のデータがありません。")
    else:
        options = [f"{row['ID']} : [{row.get('銘柄コード','')}] {row.get('銘柄名','')} ({row.get('名義','')})" for _, row in df.iterrows()]
        selected_option = st.selectbox("編集・削除する優待を選択", options)
        target_id = selected_option.split(" : ")[0]
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
                
                status_opts = ["未到着", "保有中（未使用）", "利用手続き中", "一部利用", "利用完了", "期限切れ"]
                curr_status = target_row.get("ステータス", "保有中（未使用）")
                st_idx = status_opts.index(curr_status) if curr_status in status_opts else 1
                e_status = st.selectbox("ステータス", status_opts, index=st_idx)
                
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
                st.rerun()  # 修正箇所 4

            if delete_btn:
                updated_df = df[df["ID"] != target_id]
                save_data(updated_df)
                st.rerun()  # 修正箇所 5

# TAB 4: 統計
with tab4:
    st.subheader("ポートフォリオ・カテゴリ集計")
    if not df.empty:
        col_g1, col_g2 = st.columns(2)
        with col_g1:
            st.write("▼ カテゴリ別割合")
            if "カテゴリ" in df.columns:
                st.bar_chart(df["カテゴリ"].value_counts())
        with col_g2:
            st.write("▼ 名義別所有比率")
            if "名義" in df.columns:
                st.bar_chart(df["名義"].value_counts())
