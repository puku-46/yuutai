from datetime import date
 
import pandas as pd
import streamlit as st
 
from common import (
    apply_and_save,
    esc,
    fetch_sheet,
    parse_date,
    setup_page,
    today_jst,
)
 
st.page_link("pages/1_優待.py", label="優待管理", icon="🎁")
 
# ---------------------------------------------------------
# 定数
# ---------------------------------------------------------
WORKSHEET = "優待"  # スプレッドシート内のシート（タブ）名
COLUMNS = [
    "ID", "銘柄コード", "銘柄名", "名義", "優待内容",
    "カテゴリ", "数量/金額", "権利確定月", "到着日",
    "有効期限", "ステータス", "メモ",
]
STATUS_LIST = ["未到着", "保有中（未使用）", "利用手続き中", "一部利用", "利用完了", "期限切れ"]
DONE_STATUSES = ("利用完了", "期限切れ")  # 期限アラートの対象外にするステータス
OWNERS = ["夫", "妻", "長男", "長女", "その他"]
CATEGORIES = ["飲食・お食事券", "買い物・割引券", "QUOカード・金券", "カタログギフト", "自社商品", "その他"]
 
 
def save(modifier, message):
    apply_and_save(WORKSHEET, COLUMNS, modifier, message)
 
 
# ---------------------------------------------------------
# データ読み込み
# ---------------------------------------------------------
st.title("🎁 我が家の株主優待管理")
st.caption("Googleスプレッドシートとリアルタイム同期中！")
 
df, load_ok, load_err = fetch_sheet(WORKSHEET, COLUMNS)
if not load_ok:
    st.error(
        f"スプレッドシートの「{WORKSHEET}」シートの読み込みに失敗しました。"
        "シート名・Secretsの設定・共有設定を確認してください。"
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
 
    save(mark_expired, f"{len(overdue_ids)}件を「期限切れ」に更新しました。")
 
# フィルタリング
filtered_df = df.copy()
if not filtered_df.empty:
    if selected_status != "すべて":
        filtered_df = filtered_df[filtered_df["ステータス"] == selected_status]
    if selected_owner != "すべて":
        filtered_df = filtered_df[filtered_df["名義"] == selected_owner]
    if sort_mode == "有効期限が近い順":
        sort_key = filtered_df["有効期限"].map(lambda s: parse_date(s) or date.max)
        filtered_df = (
            filtered_df.assign(_k=sort_key).sort_values("_k", kind="stable").drop(columns="_k")
        )
 
# ---------------------------------------------------------
# KPIサマリー
# ---------------------------------------------------------
unused_count = int(df["ステータス"].isin(["未到着", "保有中（未使用）"]).sum()) if not df.empty else 0
completed_count = int((df["ステータス"] == "利用完了").sum()) if not df.empty else 0
 
expiring_soon = 0
for _, r in df.iterrows():
    if r["ステータス"] in DONE_STATUSES:
        continue  # 利用完了・期限切れは数えない
    exp = parse_date(r["有効期限"])
    if exp and 0 <= (exp - today).days <= 30:
        expiring_soon += 1
 
col1, col2, col3, col4 = st.columns(4)
col1.metric("総登録件数", f"{len(df)} 件")
col2.metric("未使用・到着待ち優待", f"{unused_count} 件")
col3.metric("⏰ 30日以内に期限切れ", f"{expiring_soon} 件")
col4.metric("利用完了", f"{completed_count} 件")
 
st.divider()
 
tab1, tab2, tab3, tab4 = st.tabs(["📋 優待一覧", "➕ 新規登録", "✏️ 編集・削除", "📊 カテゴリ・統計"])
 
# ---------------------------------------------------------
# TAB 1: 優待一覧
# ---------------------------------------------------------
with tab1:
    st.subheader("保有・獲得株主優待 一覧")
    if filtered_df.empty:
        st.info("該当するデータがありません。スプレッドシートにデータを入力するか、新規登録タブから追加してください。")
    else:
        view_mode = st.radio("表示モード", ["カード表示", "テーブル表示"], horizontal=True)
        if view_mode == "テーブル表示":
            st.dataframe(filtered_df)
        else:
            cols = st.columns(3)
            for idx, row in filtered_df.reset_index(drop=True).iterrows():
                exp = parse_date(row["有効期限"])
                is_urgent = (
                    exp is not None
                    and row["ステータス"] not in DONE_STATUSES
                    and (exp - today).days <= 30
                )
                exp_color = "#d9534f" if is_urgent else "inherit"
                exp_text = esc(row["有効期限"]) if row["有効期限"] else "なし"
                memo_html = esc(row["メモ"]).replace("\n", "<br>")
                with cols[idx % 3]:
                    # 背景は半透明・文字色は継承 → ダークモードでも読める
                    st.markdown(
                        f"""
                        <div style="border:1px solid rgba(128,128,128,0.4); border-radius:10px;
                                    padding:15px; margin-bottom:15px;
                                    background-color:rgba(128,128,128,0.08);">
                            <h4 style="margin-top:0;">[{esc(row['銘柄コード'])}] {esc(row['銘柄名'])}</h4>
                            <p>👤 <b>名義:</b> {esc(row['名義'])}</p>
                            <p>🎁 <b>内容:</b> {esc(row['優待内容'])}</p>
                            <p>🏷️ <b>カテゴリ:</b> {esc(row['カテゴリ'])}</p>
                            <p>📅 <b>有効期限:</b>
                               <span style="color:{exp_color}; font-weight:bold;">{exp_text}</span></p>
                            <p>📌 <b>ステータス:</b> {esc(row['ステータス'])}</p>
                            <p>📝 <i>{memo_html}</i></p>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
 
# ---------------------------------------------------------
# TAB 2: 新規登録
# ---------------------------------------------------------
with tab2:
    st.subheader("新しい株主優待情報を登録")
    # 登録成功時にバージョンを進めて、フォームを空にする（失敗時は入力内容を残す）
    form_ver = st.session_state.get("yutai_add_form_ver", 0)
    with st.form(f"yutai_add_form_{form_ver}"):
        col_a, col_b = st.columns(2)
        with col_a:
            code = st.text_input("銘柄コード (例: 7550)")
            name = st.text_input("銘柄名 (例: ゼンショーHD) ※必須")
            owner = st.selectbox("名義", OWNERS)
            category = st.selectbox("カテゴリ", CATEGORIES)
            value = st.text_input("数量/金額 (例: 3,000円分)")
        with col_b:
            rights_month = st.text_input("権利確定月 (例: 3月/9月)")
            arrival_date = st.date_input("到着日 / 到着予定日", value=today)
            no_expiry = st.checkbox("有効期限なし")
            expiry_date = st.date_input("有効期限", value=date(today.year + 1, 12, 31))
            status = st.selectbox("ステータス", STATUS_LIST)
            memo = st.text_area("メモ・使い道など")
            gift_detail = st.text_input("優待内容詳細 (例: 食事優待券 500円×6枚)")
 
        submit_btn = st.form_submit_button("優待情報を追加する")
 
    if submit_btn:
        if not name.strip():
            st.error("銘柄名を入力してください。")
        else:
            def add_row(d):
                ids = [int(i) for i in d["ID"] if str(i).isdigit()]
                new_id = str(max(ids) + 1) if ids else "1"
                new_row = {
                    "ID": new_id,
                    "銘柄コード": code.strip(),
                    "銘柄名": name.strip(),
                    "名義": owner,
                    "優待内容": gift_detail,
                    "カテゴリ": category,
                    "数量/金額": value,
                    "権利確定月": rights_month,
                    "到着日": str(arrival_date),
                    "有効期限": "" if no_expiry else str(expiry_date),
                    "ステータス": status,
                    "メモ": memo,
                }
                return pd.concat([d, pd.DataFrame([new_row])], ignore_index=True)
 
            st.session_state["yutai_add_form_ver"] = form_ver + 1
            save(add_row, "優待情報を追加し、Googleスプレッドシートに同期しました！")
 
# ---------------------------------------------------------
# TAB 3: 編集・削除
# ---------------------------------------------------------
with tab3:
    st.subheader("登録済み優待の更新・削除")
    if df.empty:
        st.info("編集対象のデータがありません。")
    else:
        options = {
            f"{r['ID']} : [{r['銘柄コード']}] {r['銘柄名']} ({r['名義']})": r["ID"]
            for _, r in df.iterrows()
        }
        selected_label = st.selectbox("編集・削除する優待を選択", list(options.keys()))
        target_id = options[selected_label]
        target_row = df[df["ID"] == target_id].iloc[0]
 
        def with_current(base, current):
            """既存値が選択肢に無い場合も選べるように追加"""
            return base + [current] if current and current not in base else base
 
        owner_opts = with_current(OWNERS, target_row["名義"])
        category_opts = with_current(CATEGORIES, target_row["カテゴリ"])
        cur_owner_idx = owner_opts.index(target_row["名義"]) if target_row["名義"] in owner_opts else 0
        cur_cat_idx = category_opts.index(target_row["カテゴリ"]) if target_row["カテゴリ"] in category_opts else 0
        cur_status_idx = (
            STATUS_LIST.index(target_row["ステータス"]) if target_row["ステータス"] in STATUS_LIST else 1
        )
        cur_arrival = parse_date(target_row["到着日"])
        cur_expiry = parse_date(target_row["有効期限"])
 
        k = target_id  # 選択を切り替えたときに入力欄が正しく更新されるようキーに含める
        with st.form("yutai_edit_form"):
            col_ea, col_eb = st.columns(2)
            with col_ea:
                e_code = st.text_input("銘柄コード", value=target_row["銘柄コード"], key=f"e_code_{k}")
                e_name = st.text_input("銘柄名", value=target_row["銘柄名"], key=f"e_name_{k}")
                e_owner = st.selectbox("名義", owner_opts, index=cur_owner_idx, key=f"e_owner_{k}")
                e_category = st.selectbox("カテゴリ", category_opts, index=cur_cat_idx, key=f"e_cat_{k}")
                e_value = st.text_input("数量/金額", value=target_row["数量/金額"], key=f"e_val_{k}")
            with col_eb:
                e_rights_month = st.text_input("権利確定月", value=target_row["権利確定月"], key=f"e_rm_{k}")
                e_arrival_date = st.date_input("到着日", value=cur_arrival or today, key=f"e_arr_{k}")
                e_no_expiry = st.checkbox("有効期限なし", value=cur_expiry is None, key=f"e_noexp_{k}")
                e_expiry_date = st.date_input("有効期限", value=cur_expiry or today, key=f"e_exp_{k}")
                e_status = st.selectbox("ステータス", STATUS_LIST, index=cur_status_idx, key=f"e_st_{k}")
                e_memo = st.text_area("メモ", value=target_row["メモ"], key=f"e_memo_{k}")
                e_gift_detail = st.text_input("優待内容詳細", value=target_row["優待内容"], key=f"e_gift_{k}")
 
            confirm_delete = st.checkbox("削除する場合はここにチェック（元に戻せません）", key=f"e_del_{k}")
 
            col_btn1, col_btn2 = st.columns(2)
            with col_btn1:
                update_btn = st.form_submit_button("✏️ 変更内容を保存")
            with col_btn2:
                delete_btn = st.form_submit_button("🗑️ データを削除", type="primary")
 
        if update_btn:
            new_values = {
                "銘柄コード": e_code.strip(),
                "銘柄名": e_name.strip(),
                "名義": e_owner,
                "優待内容": e_gift_detail,
                "カテゴリ": e_category,
                "数量/金額": e_value,
                "権利確定月": e_rights_month,
                "到着日": str(e_arrival_date),
                "有効期限": "" if e_no_expiry else str(e_expiry_date),
                "ステータス": e_status,
                "メモ": e_memo,
            }
 
            def update_row(d):
                idx = d.index[d["ID"] == target_id]
                if len(idx) == 0:
                    raise ValueError("対象のデータが見つかりません（他の方が削除した可能性があります）。")
                for col_name, v in new_values.items():
                    d.loc[idx, col_name] = v
                return d
 
            save(update_row, "変更を保存し、Googleスプレッドシートに同期しました！")
 
        if delete_btn:
            if not confirm_delete:
                st.warning("削除するには、上の確認チェックボックスにチェックを入れてください。")
            else:
                def delete_row(d):
                    if not (d["ID"] == target_id).any():
                        raise ValueError("対象のデータが見つかりません（すでに削除されている可能性があります）。")
                    return d[d["ID"] != target_id]
 
                save(delete_row, "データを削除し、Googleスプレッドシートに同期しました！")
 
# ---------------------------------------------------------
# TAB 4: 統計
# ---------------------------------------------------------
with tab4:
    st.subheader("ポートフォリオ・カテゴリ集計")
    if df.empty:
        st.info("集計対象のデータがありません。")
    else:
        col_g1, col_g2, col_g3 = st.columns(3)
        with col_g1:
            st.write("▼ カテゴリ別 件数")
            s = df.loc[df["カテゴリ"].str.strip() != "", "カテゴリ"].value_counts()
            if not s.empty:
                st.bar_chart(s)
        with col_g2:
            st.write("▼ 名義別 件数")
            s = df.loc[df["名義"].str.strip() != "", "名義"].value_counts()
            if not s.empty:
                st.bar_chart(s)
        with col_g3:
            st.write("▼ ステータス別 件数")
            s = df.loc[df["ステータス"].str.strip() != "", "ステータス"].value_counts()
            if not s.empty:
                st.bar_chart(s)
 
