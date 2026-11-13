"""pages/2_stocks.py — 株価確認(名義ごとの保有株 / 注目株 / 通知しきい値)
 
置き場所:  リポジトリの pages/ フォルダ(pages/2_stocks.py)
必要:      requirements.txt に yfinance を追加
認証・接続: common.py の setup_page / fetch_sheet / apply_and_save をそのまま使う
 
スプレッドシートのタブ(1行目がヘッダー。名前は半角英数で一致させる):
  holdings  名義 / 銘柄コード / 銘柄名 / 取得単価 / 株数
  watch     名義 / 銘柄コード / 銘柄名 / 目標価格
  settings  名義 / しきい値(%) / 通知ON
"""
import time
import unicodedata
from datetime import datetime
 
import pandas as pd
import streamlit as st
import yfinance as yf
 
from common import apply_and_save, fetch_sheet, setup_page
 
# ---------------------------------------------------------
# 定数
# ---------------------------------------------------------
CACHE_SECONDS = 300  # 株価を使い回す時間(秒)
DEFAULT_MEMBERS = ["夫", "妻", "長男", "長女", "その他"]  # ページ1の OWNERS と同じ(settingsタブが空のときだけ使う)
DEFAULT_THRESHOLD = 3.0  # 前日比 ±% の初期値
 
COLUMNS = {
    "holdings": ["名義", "銘柄コード", "銘柄名", "取得単価", "株数"],
    "watch": ["名義", "銘柄コード", "銘柄名", "目標価格"],
    "settings": ["名義", "しきい値(%)", "通知ON"],
}
NUMERIC_COLUMNS = ["取得単価", "株数", "目標価格", "しきい値(%)"]
TRUE_VALUES = ["TRUE", "1", "はい", "ON"]
 
 
# ---------------------------------------------------------
# スプレッドシートの読み書き(common.py の関数を利用)
# ---------------------------------------------------------
def num_str(v) -> str:
    """数値をシートに書く文字列にする(300.0 -> '300'、2500.5 -> '2500.5')"""
    v = float(v)
    return str(int(v)) if v.is_integer() else str(round(v, 4))
 
 
def default_settings_str() -> pd.DataFrame:
    n = len(DEFAULT_MEMBERS)
    return pd.DataFrame(
        {"名義": DEFAULT_MEMBERS, "しきい値(%)": [num_str(DEFAULT_THRESHOLD)] * n, "通知ON": ["TRUE"] * n}
    )
 
 
def typed(kind: str, df: pd.DataFrame) -> pd.DataFrame:
    """common.fetch_sheet は全て文字列で返すので、数値・真偽値に直す"""
    if kind == "settings" and df.empty:
        df = default_settings_str()
    df = df.copy()
    for c in NUMERIC_COLUMNS:
        if c in df:
            df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ""), errors="coerce")
    if "通知ON" in df:
        df["通知ON"] = df["通知ON"].astype(str).str.strip().str.upper().isin(TRUE_VALUES)
    for c in ["名義", "銘柄コード", "銘柄名"]:
        if c in df:
            df[c] = df[c].astype(str).str.strip()
    return df
 
 
def diagnose() -> None:
    """読み込み失敗時に、アプリが実際に見ているスプレッドシートとタブ名を表示する(原因の切り分け用)"""
    try:
        import gspread
 
        conf = dict(st.secrets["connections"]["gsheets"])
        url = conf.pop("spreadsheet")
        conf.pop("worksheet", None)
        sh = gspread.service_account_from_dict(conf).open_by_url(url)
        titles = [ws.title for ws in sh.worksheets()]
        st.write(f"アプリが見ているスプレッドシート: 「{sh.title}」")
        st.write("タブ名の一覧: " + " / ".join(f"「{t}」" for t in titles))
        missing = [k for k in COLUMNS if k not in titles]
        if missing:
            st.warning(
                "このスプレッドシートに次のタブがありません: "
                + ", ".join(missing)
                + "。タブ名(半角英数・小文字)で追加し、1行目にヘッダーを入れてください。"
            )
        else:
            st.info("3つのタブはあります。1行目のヘッダーと、タブ名の前後の空白を確認してください。")
    except Exception as e:
        st.write(f"診断できませんでした: {type(e).__name__}: {str(e)[:200]}")
 
 
def load_all() -> dict:
    data = {}
    for kind, cols in COLUMNS.items():
        df, ok, err = fetch_sheet(kind, cols)
        if not ok:
            st.error(
                f"スプレッドシートの「{kind}」シートの読み込みに失敗しました。"
                "シート名(半角英数)・Secretsの設定・共有設定を確認してください。"
                f"\n\n詳細: {err}"
            )
            with st.expander("🔍 原因を調べる", expanded=True):
                diagnose()
            if st.button("🔄 再読み込み"):
                st.rerun()
            st.stop()
        data[kind] = typed(kind, df)
    return data
 
 
def rows_of(df: pd.DataFrame, owner: str) -> pd.DataFrame:
    return df[df["名義"] == owner].reset_index(drop=True)
 
 
def make_upsert(kind: str, owner: str, new_row: dict):
    """保存直前の最新データに対して、この名義の同じ銘柄を置き換える(なければ追加)"""
    def modifier(latest: pd.DataFrame) -> pd.DataFrame:
        keep = latest[~((latest["名義"] == owner) & (latest["銘柄コード"] == new_row["銘柄コード"]))]
        new = pd.DataFrame([{"名義": owner, **new_row}])
        out = new if keep.empty else pd.concat([keep, new], ignore_index=True)
        return out[COLUMNS[kind]]
    return modifier
 
 
def make_delete(owner: str, codes: list):
    def modifier(latest: pd.DataFrame) -> pd.DataFrame:
        return latest[~((latest["名義"] == owner) & (latest["銘柄コード"].isin(codes)))]
    return modifier
 
 
# ---------------------------------------------------------
# 株価取得(common.py の apply_and_save が st.cache_data.clear() を呼ぶため、
# 株価の使い回しは cache_resource に置く。保存のたびに取り直しにならない)
# ---------------------------------------------------------
@st.cache_resource
def _store() -> dict:
    return {"prices": {}, "names": {}}
 
 
def clear_prices() -> None:
    _store()["prices"].clear()
 
 
def normalize_code(raw: str) -> str:
    """'7203' / '７２０３' / '7203.t' -> '7203.T'"""
    code = unicodedata.normalize("NFKC", raw).strip().upper()
    return code if code.endswith(".T") else f"{code}.T"
 
 
def fetch_price(code: str) -> dict:
    """現在値と前日終値。取得できなければ空のdict。"""
    cache = _store()["prices"]
    hit = cache.get(code)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        closes = yf.Ticker(code).history(period="5d")["Close"].dropna()
        if closes.empty:
            return {}
        data = {
            "last": float(closes.iloc[-1]),
            "prev": float(closes.iloc[-2]) if len(closes) >= 2 else None,
        }
    except Exception:
        return {}
    cache[code] = (time.time(), data)
    return data
 
 
def fetch_name(code: str) -> str:
    names = _store()["names"]
    if code not in names:
        try:
            info = yf.Ticker(code).info
            names[code] = info.get("shortName") or info.get("longName") or ""
        except Exception:
            return ""
    return names[code]
 
 
# ---------------------------------------------------------
# 表示用データの組み立て
# ---------------------------------------------------------
def build_table(kind: str, df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    rows = []
    for _, r in df.iterrows():
        p = fetch_price(r["銘柄コード"])
        last, prev = p.get("last"), p.get("prev")
        chg = (last - prev) if last is not None and prev else None
        pct = (chg / prev * 100) if chg is not None else None
        row = {
            "銘柄コード": r["銘柄コード"],
            "銘柄名": r["銘柄名"],
            "通知": "⚠" if pct is not None and abs(pct) >= threshold else "",
            "現在値": last,
            "前日比": chg,
            "前日比%": pct,
        }
        if kind == "holdings":
            cost, qty = r["取得単価"], r["株数"]
            row["取得単価"] = cost
            row["株数"] = qty
            row["評価額"] = last * qty if last is not None else None
            row["含み損益"] = (last - cost) * qty if last is not None else None
            row["損益率%"] = (last - cost) / cost * 100 if last is not None and cost else None
        else:
            target = r["目標価格"]
            row["目標価格"] = target
            row["目標との差%"] = (last - target) / target * 100 if last is not None and target else None
        rows.append(row)
    return pd.DataFrame(rows)
 
 
def style_table(table: pd.DataFrame, kind: str):
    """プラスは緑、マイナスは赤(日本の株アプリは逆の場合もあるので好みで入れ替え可)"""
    def color(v):
        if pd.isna(v):
            return ""
        return "color: #d62728" if v < 0 else "color: #2ca02c" if v > 0 else ""
 
    signed = ["前日比", "前日比%"] + (["含み損益", "損益率%"] if kind == "holdings" else [])
    fmt = {c: "{:,.1f}" for c in ["現在値", "取得単価", "目標価格"] if c in table}
    fmt["前日比"] = "{:+,.1f}"
    fmt.update({c: "{:+.2f}%" for c in ["前日比%", "損益率%", "目標との差%"] if c in table})
    fmt.update({c: "{:,.0f}" for c in ["株数", "評価額"] if c in table})
    if "含み損益" in table:
        fmt["含み損益"] = "{:+,.0f}"
    styler = table.style.format(fmt, na_rep="-")
    apply_color = getattr(styler, "map", None) or styler.applymap  # pandas新旧両対応
    return apply_color(color, subset=[c for c in signed if c in table])
 
 
def get_threshold(settings: pd.DataFrame, owner: str) -> float:
    v = settings.loc[settings["名義"] == owner, "しきい値(%)"]
    return float(v.iloc[0]) if len(v) and pd.notna(v.iloc[0]) else DEFAULT_THRESHOLD
 
 
# ---------------------------------------------------------
# 画面部品
# ---------------------------------------------------------
def select_owner(owners: list) -> str:
    """名義の選択。選んだ人を session_state に覚え、他のページでも使えるようにする"""
    saved = st.session_state.get("selected_owner")
    index = owners.index(saved) if saved in owners else 0
    owner = st.radio("誰の株を見ますか?", owners, index=index, horizontal=True)
    st.session_state["selected_owner"] = owner
    return owner
 
 
def render_summary(table: pd.DataFrame) -> None:
    total = table["評価額"].sum()
    pnl = table["含み損益"].sum()
    cost = (table["取得単価"] * table["株数"]).sum()
    alerts = int((table["通知"] == "⚠").sum())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("評価額 合計", f"{total:,.0f} 円")
    c2.metric("含み損益 合計", f"{pnl:+,.0f} 円", f"{pnl / cost * 100:+.2f}%" if cost else None)
    c3.metric("銘柄数", f"{len(table)} 銘柄")
    c4.metric("しきい値超え", f"{alerts} 銘柄")
 
 
def render_add_form(kind: str, owner: str, df: pd.DataFrame) -> None:
    with st.expander("➕ 銘柄を登録・更新", expanded=df.empty):
        with st.form(f"add_{kind}_{owner}", clear_on_submit=True):
            code_in = st.text_input("銘柄コード(例: 7203)")
            name_in = st.text_input("銘柄名(空欄なら自動取得)")
            if kind == "holdings":
                cost = st.number_input("取得単価(円)", min_value=0.0, step=1.0)
                qty = st.number_input("株数", min_value=0, step=100, value=100)
            else:
                target = st.number_input("目標価格(円)", min_value=0.0, step=1.0)
            submitted = st.form_submit_button("登録")
 
        if not submitted:
            return
        if not code_in.strip():
            st.error("銘柄コードを入力してください。")
            return
 
        code = normalize_code(code_in)
        if not fetch_price(code):
            st.error(f"{code} の株価が取得できませんでした。コードを確認してください。")
            return
 
        name = name_in.strip() or fetch_name(code) or code
        if kind == "holdings":
            new = {"銘柄コード": code, "銘柄名": name, "取得単価": num_str(cost), "株数": num_str(qty)}
        else:
            new = {"銘柄コード": code, "銘柄名": name, "目標価格": num_str(target)}
        apply_and_save(kind, COLUMNS[kind], make_upsert(kind, owner, new), f"{name}({code})を登録しました。")
 
 
def render_delete_form(kind: str, owner: str, df: pd.DataFrame) -> None:
    if df.empty:
        return
    with st.expander("🗑 銘柄を削除"):
        options = {f"{r['銘柄名']}({r['銘柄コード']})": r["銘柄コード"] for _, r in df.iterrows()}
        picked = st.multiselect("削除する銘柄", list(options), key=f"del_{kind}_{owner}")
        if st.button("削除する", key=f"delbtn_{kind}_{owner}", disabled=not picked):
            codes = [options[p] for p in picked]
            apply_and_save(kind, COLUMNS[kind], make_delete(owner, codes), f"{len(codes)}銘柄を削除しました。")
 
 
def render_stock_tab(kind: str, owner: str, df: pd.DataFrame, threshold: float) -> None:
    render_add_form(kind, owner, df)
 
    if df.empty:
        st.info("まだ銘柄が登録されていません。上の「銘柄を登録」から追加してください。")
        return
 
    with st.spinner("株価を取得中..."):
        table = build_table(kind, df, threshold)
 
    if kind == "holdings":
        render_summary(table)
    st.dataframe(style_table(table, kind), hide_index=True)
    st.caption(f"⚠ = 前日比が ±{threshold:g}% 以上(通知対象)")
    render_delete_form(kind, owner, df)
 
 
def render_settings_tab(owner: str, settings: pd.DataFrame) -> None:
    st.subheader(f"{owner}さんの通知設定")
    row = settings[settings["名義"] == owner].iloc[0]
    with st.form(f"settings_{owner}"):
        threshold = st.number_input(
            "しきい値(前日比 ±%)", min_value=0.1, max_value=50.0, step=0.5,
            value=get_threshold(settings, owner),
            help="前日比がこの値以上動いた銘柄を通知対象にします(上昇・下落どちらも)",
        )
        notify_on = st.checkbox("通知をONにする", value=bool(row["通知ON"]))
        saved = st.form_submit_button("保存")
 
    if saved:
        def modifier(latest: pd.DataFrame) -> pd.DataFrame:
            base = (latest if not latest.empty else default_settings_str()).copy()
            values = {"しきい値(%)": num_str(threshold), "通知ON": "TRUE" if notify_on else "FALSE"}
            mask = base["名義"] == owner
            if mask.any():
                for col, v in values.items():
                    base.loc[mask, col] = v
            else:
                base = pd.concat([base, pd.DataFrame([{"名義": owner, **values}])], ignore_index=True)
            return base[COLUMNS["settings"]]
        apply_and_save("settings", COLUMNS["settings"], modifier, f"{owner}さんの通知設定を保存しました。")
 
    with st.expander("👤 メンバーを追加"):
        with st.form("add_member", clear_on_submit=True):
            new_name = st.text_input("名前(例: 祖母)")
            added = st.form_submit_button("追加")
        if added:
            new_name = new_name.strip()
            if not new_name:
                st.error("名前を入力してください。")
            else:
                def add_modifier(latest: pd.DataFrame) -> pd.DataFrame:
                    base = (latest if not latest.empty else default_settings_str()).copy()
                    if new_name in set(base["名義"]):
                        raise ValueError("その名前は既に登録されています。")
                    add = pd.DataFrame(
                        [{"名義": new_name, "しきい値(%)": num_str(DEFAULT_THRESHOLD), "通知ON": "TRUE"}]
                    )
                    return pd.concat([base, add], ignore_index=True)[COLUMNS["settings"]]
                apply_and_save("settings", COLUMNS["settings"], add_modifier, f"{new_name}さんを追加しました。")
 
 
# ---------------------------------------------------------
# メイン
# ---------------------------------------------------------
def main() -> None:
    setup_page("株価確認", "📈")  # ページ設定 + ログイン確認(common.py)
    st.title("📈 株価確認")
 
    data = load_all()
    settings = data["settings"]
    owners = list(settings["名義"])
    owner = select_owner(owners)
    threshold = get_threshold(settings, owner)
 
    left, right = st.columns([4, 1])
    left.caption(
        f"最終取得: {datetime.now():%Y-%m-%d %H:%M:%S} / "
        f"{CACHE_SECONDS // 60}分以内の再表示は前回の値を使用 / 日本株は約20分遅延の場合あり"
    )
    if right.button("🔄 最新の株価に更新"):
        clear_prices()
        st.rerun()
 
    tab_h, tab_w, tab_s = st.tabs(["保有株", "注目株", "通知設定"])
    with tab_h:
        render_stock_tab("holdings", owner, rows_of(data["holdings"], owner), threshold)
    with tab_w:
        render_stock_tab("watch", owner, rows_of(data["watch"], owner), threshold)
    with tab_s:
        render_settings_tab(owner, settings)
 
 
if __name__ == "__main__":
    main()
 
