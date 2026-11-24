import json
import re
import zlib
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

setup_page("優待管理", "🎁")

# ---------------------------------------------------------
# 定数
# ---------------------------------------------------------
WORKSHEET = None  # スプレッドシート内のシート（タブ）名
# 最後の「優待明細」が新設列（1社の複数優待をJSONで保持する）。
# 「優待内容」「カテゴリ」「数量/金額」「有効期限」「ステータス」は
# 優待明細から自動で作る"要約"として残す（LINE通知など他のプログラム用）。
COLUMNS = [
    "ID", "銘柄コード", "銘柄名", "名義", "優待内容",
    "カテゴリ", "数量/金額", "権利確定月", "到着日",
    "有効期限", "ステータス", "メモ", "優待明細",
]
STATUS_LIST = ["未到着", "保有中（未使用）", "利用手続き中", "一部利用", "利用完了", "期限切れ"]
DONE_STATUSES = ("利用完了", "期限切れ")  # 期限アラートの対象外にするステータス
ACTIVE_PRIORITY = ["利用手続き中", "一部利用", "保有中（未使用）", "未到着"]  # 会社全体のステータス決定用
OWNERS = ["夫", "妻", "長男", "長女", "その他"]
CATEGORIES = ["飲食・お食事券", "買い物・割引券", "QUOカード・金券", "カタログギフト", "自社商品", "その他"]
UNITS = ["円", "枚", "個", "冊", "回", "口", "ポイント", "なし"]
NO_UNIT = "なし"
MAX_BENEFITS = 6
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
BEN_KEYS = ["内容", "カテゴリ", "数量", "単位", "有効期限", "ステータス"]

today = today_jst()


def save(modifier, message):
    apply_and_save(WORKSHEET, COLUMNS, modifier, message)


# ---------------------------------------------------------
# 優待明細（1社に複数の優待）まわりの共通処理
# ---------------------------------------------------------
def blank_benefit():
    return {
        "内容": "", "カテゴリ": CATEGORIES[0], "数量": "", "単位": "円",
        "有効期限": "", "ステータス": STATUS_LIST[0], "_new": True,
    }


def num_to_str(n):
    """数値 → 保存用の文字列（1000.0 → '1000'）。None は空文字。"""
    if n is None:
        return ""
    n = float(n)
    return str(int(n)) if n.is_integer() else str(round(n, 6))


def to_float(s):
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


def fmt_amount(num_str, unit=""):
    """'1000','円' → '1,000円'。数値が空なら '未入力'。"""
    if str(num_str).strip() == "":
        return "未入力"
    n = to_float(num_str)
    if n is None:
        return f"{num_str}{unit}"
    body = f"{int(n):,}" if n.is_integer() else f"{n:,g}"
    return f"{body}{unit}"


_LEGACY = re.compile(r"^\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([^\d\s]*?)\s*分?\s*$")


def legacy_to_benefit(row):
    """旧形式（1社1優待・『3,000円分』のような文字）から優待1件を作る。"""
    raw = str(row.get("数量/金額", "") or "").strip()
    num, unit, extra = "", "", ""
    m = _LEGACY.match(raw)
    if m:
        num = num_to_str(float(m.group(1).replace(",", "")))
        unit = m.group(2)
    elif raw:
        extra = raw  # 解析できない文字は優待内容の後ろに残す
    content = str(row.get("優待内容", "") or "")
    if extra:
        content = f"{content}（{extra}）" if content else extra
    return {
        "内容": content,
        "カテゴリ": str(row.get("カテゴリ", "") or "") or CATEGORIES[-1],
        "数量": num,
        "単位": unit,
        "有効期限": str(row.get("有効期限", "") or ""),
        "ステータス": str(row.get("ステータス", "") or "") or "保有中（未使用）",
    }


def normalize(b):
    base = blank_benefit()
    out = {k: str(b.get(k, base[k]) if b.get(k) is not None else "") for k in BEN_KEYS}
    if not out["ステータス"]:
        out["ステータス"] = "保有中（未使用）"
    return out


def get_benefits(row):
    """行から優待のリストを取り出す。優待明細が空なら旧列から自動変換（移行）。"""
    raw = str(row.get("優待明細", "") or "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                items = [normalize(b) for b in data if isinstance(b, dict)]
                if items:
                    return items
        except json.JSONDecodeError:
            pass
    return [legacy_to_benefit(row)]


def is_active(b):
    return b["ステータス"] not in DONE_STATUSES


def next_expiry(bs):
    """未完了の優待のうち一番近い期限。全部完了なら一番遅い期限。"""
    active = [parse_date(b["有効期限"]) for b in bs if is_active(b)]
    active = [e for e in active if e]
    if active:
        return min(active)
    allx = [parse_date(b["有効期限"]) for b in bs]
    allx = [e for e in allx if e]
    return max(allx) if allx else None


def summarize(bs):
    """優待明細から、スプレッドシートの要約列を作る。"""
    multi = len(bs) > 1
    contents = [(CIRCLED[i] if multi else "") + b["内容"] for i, b in enumerate(bs)]
    amounts = [fmt_amount(b["数量"], b["単位"]) for b in bs if b["数量"] != ""]
    cats = list(dict.fromkeys(b["カテゴリ"] for b in bs if b["カテゴリ"]))
    active = [b for b in bs if is_active(b)]
    if active:
        present = {b["ステータス"] for b in active}
        status = next((s for s in ACTIVE_PRIORITY if s in present), active[0]["ステータス"])
    else:
        status = "利用完了" if any(b["ステータス"] == "利用完了" for b in bs) else "期限切れ"
    exp = next_expiry(bs)
    return {
        "優待内容": " / ".join(contents),
        "カテゴリ": "・".join(cats),
        "数量/金額": " / ".join(amounts),
        "有効期限": str(exp) if exp else "",
        "ステータス": status,
    }


def set_benefits(d, mask, bs):
    """DataFrame d の mask 該当行に、優待明細と要約列を書き込む。"""
    d.loc[mask, "優待明細"] = json.dumps(bs, ensure_ascii=False)
    for col, v in summarize(bs).items():
        d.loc[mask, col] = v


def with_current(base, current):
    """既存値が選択肢に無い場合も選べるように追加"""
    return base + [current] if current and current not in base else base


def ben_title(bs, i):
    return f"優待{CIRCLED[i]}（{bs[i]['内容'] or '内容未入力'}）"


# ---------------------------------------------------------
# 入力欄（登録・編集で共通）
# ---------------------------------------------------------
def benefit_inputs(prefix, base):
    """優待1件ぶんの入力欄を描画して、入力値(dict)を返す。"""
    is_new = base.get("_new", False)
    content = st.text_input("優待内容（例: 食事券 / 20%割引券）", value=base["内容"], key=f"{prefix}_content")

    cat_opts = with_current(CATEGORIES, base["カテゴリ"])
    cat = st.selectbox(
        "カテゴリ", cat_opts,
        index=cat_opts.index(base["カテゴリ"]) if base["カテゴリ"] in cat_opts else 0,
        key=f"{prefix}_cat",
    )

    col_n, col_u = st.columns([2, 1])
    with col_n:
        amount = st.number_input(
            "数量/金額（数字のみ）", min_value=0.0, value=to_float(base["数量"]),
            step=1.0, format="%g", key=f"{prefix}_amt",
        )
    unit_cur = base["単位"] or NO_UNIT
    unit_opts = with_current(UNITS, unit_cur)
    with col_u:
        unit_sel = st.selectbox("単位", unit_opts, index=unit_opts.index(unit_cur), key=f"{prefix}_unit")

    cur_exp = parse_date(base["有効期限"])
    no_exp = st.checkbox("有効期限なし", value=(not is_new and cur_exp is None), key=f"{prefix}_noexp")
    default_exp = date(today.year + 1, 12, 31) if is_new else today
    exp = st.date_input("有効期限", value=cur_exp or default_exp, key=f"{prefix}_exp")

    status = st.selectbox(
        "ステータス", STATUS_LIST,
        index=STATUS_LIST.index(base["ステータス"]) if base["ステータス"] in STATUS_LIST else 0,
        key=f"{prefix}_status",
    )
    return {
        "内容": content.strip(),
        "カテゴリ": cat,
        "数量": num_to_str(amount),
        "単位": "" if unit_sel == NO_UNIT else unit_sel,
        "有効期限": "" if no_exp else str(exp),
        "ステータス": status,
    }


def benefit_tabs(n):
    """優待が2件以上ならタブ（優待①②…）、1件ならタブなしの入れ物を返す。"""
    if n > 1:
        return st.tabs([f"優待{CIRCLED[i]}" for i in range(n)])
    return [st.container()]


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

if "優待明細" not in df.columns:
    df["優待明細"] = ""
# 画面表示用（スプレッドシートには書かない）: 各社の優待リスト
df["_b"] = [get_benefits(r) for _, r in df.iterrows()]

# 優待1件ごとの一覧（KPI・期限チェック用）
all_items = [(r["ID"], i, b) for _, r in df.iterrows() for i, b in enumerate(r["_b"])]

# ---------------------------------------------------------
# サイドバー
# ---------------------------------------------------------
st.sidebar.header("🔍 フィルター & 操作")

selected_status = st.sidebar.selectbox("ステータスで絞り込み", ["すべて"] + STATUS_LIST)

owner_values = sorted({o for o in df["名義"] if o.strip()}) if not df.empty else []
selected_owner = st.sidebar.selectbox("名義で絞り込み", ["すべて"] + owner_values)

extra_categories = sorted(
    {b["カテゴリ"] for _, _, b in all_items if b["カテゴリ"].strip() and b["カテゴリ"] not in CATEGORIES}
)
selected_category = st.sidebar.selectbox("カテゴリで絞り込み", ["すべて"] + CATEGORIES + extra_categories)

sort_mode = st.sidebar.selectbox("並び順", ["有効期限が近い順", "登録順（ID）"])

if st.sidebar.button("🔄 スプレッドシートの最新データを再読み込み"):
    st.cache_data.clear()
    st.rerun()

# 期限が過ぎているのに「利用完了/期限切れ」になっていない優待
overdue_count = 0
for _, _, b in all_items:
    exp = parse_date(b["有効期限"])
    if exp and exp < today and is_active(b):
        overdue_count += 1

st.sidebar.divider()
if st.sidebar.button(
    f"⏰ 期限超過を「期限切れ」に更新（{overdue_count}件）",
    disabled=not overdue_count,
):
    def mark_expired(d):
        for pos in d.index:
            bs = get_benefits(d.loc[pos])
            changed = False
            for b in bs:
                exp = parse_date(b["有効期限"])
                if exp and exp < today and is_active(b):
                    b["ステータス"] = "期限切れ"
                    changed = True
            if changed:
                set_benefits(d, d.index == pos, bs)
        return d

    save(mark_expired, f"{overdue_count}件の優待を「期限切れ」に更新しました。")

# フィルタリング（会社単位。条件に合う優待を1つでも持つ会社を表示）
filtered_df = df.copy()
if not filtered_df.empty:
    def company_matches(r):
        bs = r["_b"]
        if selected_status != "すべて" and not any(b["ステータス"] == selected_status for b in bs):
            return False
        if selected_category != "すべて" and not any(b["カテゴリ"] == selected_category for b in bs):
            return False
        if selected_owner != "すべて" and r["名義"] != selected_owner:
            return False
        return True

    filtered_df = filtered_df[filtered_df.apply(company_matches, axis=1)]
    if sort_mode == "有効期限が近い順" and not filtered_df.empty:
        sort_key = filtered_df["_b"].map(lambda bs: next_expiry(bs) or date.max)
        filtered_df = (
            filtered_df.assign(_k=sort_key).sort_values("_k", kind="stable").drop(columns="_k")
        )

# ---------------------------------------------------------
# KPIサマリー（優待1件ごとに数える）
# ---------------------------------------------------------
unused_count = sum(1 for _, _, b in all_items if b["ステータス"] in ("未到着", "保有中（未使用）"))
completed_count = sum(1 for _, _, b in all_items if b["ステータス"] == "利用完了")
expiring_soon = 0
for _, _, b in all_items:
    if not is_active(b):
        continue
    exp = parse_date(b["有効期限"])
    if exp and 0 <= (exp - today).days <= 30:
        expiring_soon += 1

col1, col2, col3, col4 = st.columns(4)
col1.metric("登録銘柄数", f"{len(df)} 社")
col2.metric("未使用・到着待ち優待", f"{unused_count} 件")
col3.metric("⏰ 30日以内に期限切れ", f"{expiring_soon} 件")
col4.metric("利用完了", f"{completed_count} 件")

st.divider()

tab1, tab_use, tab2, tab3, tab4 = st.tabs(
    ["📋 優待一覧", "🎫 使用を記録", "➕ 新規登録", "✏️ 編集・削除", "📊 カテゴリ・統計"]
)


# ---------------------------------------------------------
# TAB 1: 優待一覧
# ---------------------------------------------------------
def benefit_line_html(b, mark=""):
    exp = parse_date(b["有効期限"])
    urgent = exp is not None and is_active(b) and (exp - today).days <= 30
    exp_color = "#d9534f" if urgent else "inherit"
    exp_text = esc(b["有効期限"]) if b["有効期限"] else "期限なし"
    content = esc(b["内容"]) or "（内容未入力）"
    return (
        '<div style="padding:6px 0; border-top:1px solid rgba(128,128,128,0.25);">'
        f"<div><b>{mark}{content}</b></div>"
        '<div style="font-size:0.9em;">'
        f"🔢 残り <b>{esc(fmt_amount(b['数量'], b['単位']))}</b>　"
        f'📅 <span style="color:{exp_color}; font-weight:bold;">{exp_text}</span>　'
        f"📌 {esc(b['ステータス'])}"
        "</div></div>"
    )


with tab1:
    st.subheader("保有・獲得株主優待 一覧")
    if filtered_df.empty:
        st.info("該当するデータがありません。スプレッドシートにデータを入力するか、新規登録タブから追加してください。")
    else:
        view_mode = st.radio("表示モード", ["カード表示", "テーブル表示"], horizontal=True)
        if view_mode == "テーブル表示":
            rows = []
            for _, r in filtered_df.iterrows():
                bs = r["_b"]
                for i, b in enumerate(bs):
                    rows.append({
                        "銘柄コード": r["銘柄コード"], "銘柄名": r["銘柄名"], "名義": r["名義"],
                        "優待": f"優待{CIRCLED[i]}" if len(bs) > 1 else "",
                        "優待内容": b["内容"], "カテゴリ": b["カテゴリ"],
                        "残り": fmt_amount(b["数量"], b["単位"]),
                        "有効期限": b["有効期限"], "ステータス": b["ステータス"],
                        "権利確定月": r["権利確定月"], "到着日": r["到着日"],
                    })
            st.dataframe(pd.DataFrame(rows))
        else:
            cols = st.columns(3)
            for idx, row in filtered_df.reset_index(drop=True).iterrows():
                bs = row["_b"]
                memo_html = esc(row["メモ"]).replace("\n", "<br>")
                with cols[idx % 3]:
                    with st.container(border=True):
                        st.markdown(
                            f"<h4 style='margin-top:0;'>[{esc(row['銘柄コード'])}] {esc(row['銘柄名'])}</h4>"
                            f"<p>👤 <b>名義:</b> {esc(row['名義'])}</p>",
                            unsafe_allow_html=True,
                        )
                        lines = "".join(
                        benefit_line_html(b, f"{CIRCLED[i]} " if len(bs) > 1 else "")
                        for i, b in enumerate(bs)
                        )
                        st.markdown(lines, unsafe_allow_html=True)
                        
                        if memo_html:
                            st.markdown(f"<p>📝 <i>{memo_html}</i></p>", unsafe_allow_html=True)

# ---------------------------------------------------------
# TAB: 使用を記録
# ---------------------------------------------------------
def record_usage(target_id, bi, new_num=None):
    """new_num: 使用後の残り(数値)。None または 0 以下なら「全部使った」。"""
    def modifier(d):
        mask = d["ID"] == target_id
        if not mask.any():
            raise ValueError("対象のデータが見つかりません（他の方が削除した可能性があります）。")
        row = d[mask].iloc[0]
        bs = get_benefits(row)
        if bi >= len(bs):
            raise ValueError("対象の優待が見つかりません。編集された可能性があります。再読み込みしてください。")
        b = bs[bi]
        title = ben_title(bs, bi)
        old_txt = fmt_amount(b["数量"], b["単位"])
        if new_num is None or new_num <= 0:
            if b["数量"] != "":
                b["数量"] = "0"
            b["ステータス"] = "利用完了"
            log = f"{today} {title}: 全部使用（残り {old_txt} → 利用完了）"
        else:
            b["数量"] = num_to_str(new_num)
            b["ステータス"] = "一部利用"
            log = f"{today} {title}: 残り {old_txt} → {fmt_amount(b['数量'], b['単位'])}"
        memo = str(row["メモ"]).rstrip()
        d.loc[mask, "メモ"] = (memo + "\n" if memo else "") + "[使用] " + log
        set_benefits(d, mask, bs)
        return d

    return modifier


with tab_use:
    st.subheader("🎫 優待の使用を記録")
    st.caption("食事券など一度に使い切らない優待の「残り」を更新します。履歴はメモに残ります。")

    options = []
    for _, r in df.iterrows():
        bs = r["_b"]
        for i, b in enumerate(bs):
            if b["ステータス"] == "未到着" or not is_active(b):
                continue
            gift = b["内容"][:15] + ("…" if len(b["内容"]) > 15 else "")
            mark = CIRCLED[i] if len(bs) > 1 else ""
            label = (
                f"{r['銘柄名']} {mark}{gift}｜残り {fmt_amount(b['数量'], b['単位'])}"
                f"｜期限 {b['有効期限'] or 'なし'}｜{r['名義']}"
            )
            options.append((parse_date(b["有効期限"]) or date.max, f"{r['ID']}|{i}", label))

    if not options:
        st.info("使用を記録できる優待がありません（「未到着」「利用完了」「期限切れ」は対象外です）。")
    else:
        options.sort(key=lambda x: x[0])
        labels = {k: lab for _, k, lab in options}
        use_keys = list(labels)
        saved = st.session_state.get("use_target_key")
        use_key = st.selectbox(
            "使った優待を選択（期限が近い順）",
            use_keys,
            index=use_keys.index(saved) if saved in use_keys else 0,
            format_func=labels.get,
        )
        st.session_state["use_target_key"] = use_key

        use_id, use_bi = use_key.split("|")
        use_bi = int(use_bi)
        use_row = df[df["ID"] == use_id].iloc[0]
        b = use_row["_b"][use_bi]
        unit = b["単位"]
        cur = to_float(b["数量"]) if b["数量"] != "" else None
        st.markdown(f"**現在の残り:** {fmt_amount(b['数量'], unit)}")

        def all_used_button():
            if st.button("🍽️ 全部使った（利用完了にする）", key=f"use_all_{use_key}"):
                save(record_usage(use_id, use_bi, None), f"{use_row['銘柄名']}を「利用完了」にしました。")

        if cur is not None:
            kk = f"{use_key}_{b['数量']}"  # 残りが変わったら入力欄も初期化されるようキーに含める
            step = 100.0 if unit == "円" else 1.0
            suffix = f"（{unit}）" if unit else ""
            mode = st.radio("入力方法", ["使った分を入力", "残りを直接入力"], horizontal=True, key=f"use_mode_{kk}")

            new_num, can_record = None, False
            if mode == "使った分を入力":
                used = st.number_input(
                    f"使った分{suffix}", min_value=0.0, value=0.0, step=step, format="%g", key=f"use_n_{kk}"
                )
                if used <= 0:
                    st.info("使った分を入力してください。")
                elif used > cur:
                    st.error(f"残り（{fmt_amount(b['数量'], unit)}）より多くは使えません。")
                else:
                    new_num, can_record = round(cur - used, 6), True
            else:
                remain = st.number_input(
                    f"新しい残り{suffix}", min_value=0.0, value=cur, step=step, format="%g", key=f"use_r_{kk}"
                )
                if remain == cur:
                    st.info("残りが現在と同じです。")
                else:
                    new_num, can_record = round(remain, 6), True
                    if remain > cur:
                        st.warning("残りが増えています（訂正として記録されます）。")

            if can_record:
                finish_note = "（使い切り → 「利用完了」になります）" if new_num <= 0 else ""
                st.success(
                    f"残り: {fmt_amount(b['数量'], unit)} → **{fmt_amount(num_to_str(new_num), unit)}** {finish_note}"
                )

            col_u1, col_u2 = st.columns(2)
            with col_u1:
                if st.button("✅ 使用を記録する", key=f"use_btn_{kk}", type="primary", disabled=not can_record):
                    save(
                        record_usage(use_id, use_bi, new_num),
                        f"{use_row['銘柄名']}：残りを {fmt_amount(num_to_str(new_num), unit)} に更新しました。",
                    )
            with col_u2:
                all_used_button()
        else:
            st.info(
                "この優待は「数量/金額」が未入力のため、残りの計算ができません。"
                "「編集・削除」タブで数量を入力すると、一部使用も記録できます。"
            )
            all_used_button()

# ---------------------------------------------------------
# TAB 2: 新規登録
# ---------------------------------------------------------
with tab2:
    st.subheader("新しい株主優待情報を登録")
    # 登録成功時にバージョンを進めて、フォームを空にする（失敗時は入力内容を残す）
    ver = st.session_state.get("yutai_add_form_ver", 0)

    n_add = int(st.number_input(
        "この会社の優待内容の数（金券と割引券など複数あるとき増やす）",
        min_value=1, max_value=MAX_BENEFITS, value=1, step=1, key=f"add_n_{ver}",
    ))

    with st.form(f"yutai_add_form_{ver}"):
        st.markdown("##### 🏢 会社情報")
        col_a, col_b = st.columns(2)
        with col_a:
            code = st.text_input("銘柄コード (例: 7550)", key=f"add_code_{ver}")
            name = st.text_input("銘柄名 (例: ゼンショーHD) ※必須", key=f"add_name_{ver}")
            owner = st.selectbox("名義", OWNERS, key=f"add_owner_{ver}")
        with col_b:
            rights_month = st.text_input("権利確定月 (例: 3月/9月)", key=f"add_rm_{ver}")
            arrival_date = st.date_input("到着日 / 到着予定日", value=today, key=f"add_arr_{ver}")
            memo = st.text_area("メモ・使い道など", key=f"add_memo_{ver}")

        st.markdown("##### 🎁 優待内容")
        new_bens = []
        for i, t in enumerate(benefit_tabs(n_add)):
            with t:
                new_bens.append(benefit_inputs(f"add_{ver}_{i}", blank_benefit()))

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
                    "権利確定月": rights_month,
                    "到着日": str(arrival_date),
                    "メモ": memo,
                    "優待明細": json.dumps(new_bens, ensure_ascii=False),
                    **summarize(new_bens),
                }
                return pd.concat([d, pd.DataFrame([new_row])], ignore_index=True)

            st.session_state["yutai_add_form_ver"] = ver + 1
            save(add_row, "優待情報を追加し、Googleスプレッドシートに同期しました！")

# ---------------------------------------------------------
# TAB 3: 編集・削除
# ---------------------------------------------------------
with tab3:
    st.subheader("登録済み優待の更新・削除")
    if df.empty:
        st.info("編集対象のデータがありません。")
    else:
        edit_options = {
            f"{r['ID']} : [{r['銘柄コード']}] {r['銘柄名']} ({r['名義']})": r["ID"]
            for _, r in df.iterrows()
        }
        selected_label = st.selectbox("編集・削除する優待を選択", list(edit_options.keys()))
        target_id = edit_options[selected_label]
        target_row = df[df["ID"] == target_id].iloc[0]
        cur_bens = target_row["_b"]

        # 選択の切り替え・保存後の内容変更で入力欄が正しく更新されるよう、内容のハッシュをキーに含める
        sig = "|".join(str(target_row.get(c, "")) for c in COLUMNS)
        k = f"{target_id}_{zlib.crc32(sig.encode('utf-8'))}"

        n_edit = int(st.number_input(
            "優待内容の数（増やすと空の優待が追加されます）",
            min_value=1, max_value=MAX_BENEFITS, value=len(cur_bens), step=1, key=f"e_n_{k}",
        ))
        if n_edit < len(cur_bens):
            st.warning(f"保存すると、優待{CIRCLED[n_edit]}以降が削除されます。")

        with st.form("yutai_edit_form"):
            st.markdown("##### 🏢 会社情報")
            col_ea, col_eb = st.columns(2)
            owner_opts = with_current(OWNERS, target_row["名義"])
            with col_ea:
                e_code = st.text_input("銘柄コード", value=target_row["銘柄コード"], key=f"e_code_{k}")
                e_name = st.text_input("銘柄名", value=target_row["銘柄名"], key=f"e_name_{k}")
                e_owner = st.selectbox(
                    "名義", owner_opts,
                    index=owner_opts.index(target_row["名義"]) if target_row["名義"] in owner_opts else 0,
                    key=f"e_owner_{k}",
                )
            with col_eb:
                e_rights_month = st.text_input("権利確定月", value=target_row["権利確定月"], key=f"e_rm_{k}")
                e_arrival_date = st.date_input(
                    "到着日", value=parse_date(target_row["到着日"]) or today, key=f"e_arr_{k}"
                )
                e_memo = st.text_area("メモ", value=target_row["メモ"], key=f"e_memo_{k}")

            st.markdown("##### 🎁 優待内容")
            e_bens = []
            for i, t in enumerate(benefit_tabs(n_edit)):
                with t:
                    base = cur_bens[i] if i < len(cur_bens) else blank_benefit()
                    e_bens.append(benefit_inputs(f"e_{k}_{i}", base))

            confirm_delete = st.checkbox("削除する場合はここにチェック（元に戻せません）", key=f"e_del_{k}")

            col_btn1, col_btn2 = st.columns(2)
            with col_btn1:
                update_btn = st.form_submit_button("✏️ 変更内容を保存")
            with col_btn2:
                delete_btn = st.form_submit_button("🗑️ データを削除", type="primary")

        if update_btn:
            new_company = {
                "銘柄コード": e_code.strip(),
                "銘柄名": e_name.strip(),
                "名義": e_owner,
                "権利確定月": e_rights_month,
                "到着日": str(e_arrival_date),
                "メモ": e_memo,
            }

            def update_row(d):
                mask = d["ID"] == target_id
                if not mask.any():
                    raise ValueError("対象のデータが見つかりません（他の方が削除した可能性があります）。")
                for col_name, v in new_company.items():
                    d.loc[mask, col_name] = v
                set_benefits(d, mask, e_bens)
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
            st.write("▼ カテゴリ別 件数（優待ごと）")
            s = pd.Series([b["カテゴリ"] for _, _, b in all_items if b["カテゴリ"].strip()]).value_counts()
            if not s.empty:
                st.bar_chart(s)
        with col_g2:
            st.write("▼ 名義別 銘柄数")
            s = df.loc[df["名義"].str.strip() != "", "名義"].value_counts()
            if not s.empty:
                st.bar_chart(s)
        with col_g3:
            st.write("▼ ステータス別 件数（優待ごと）")
            s = pd.Series([b["ステータス"] for _, _, b in all_items if b["ステータス"].strip()]).value_counts()
            if not s.empty:
                st.bar_chart(s)
