"""pages/2_stocks.py — 株価確認(名義ごとの保有株 / 注目株 / 通知しきい値)

置き場所:  リポジトリの pages/ フォルダ(pages/2_stocks.py)
必要:      requirements.txt に yfinance を追加
認証・接続: common.py の setup_page / fetch_sheet / apply_and_save をそのまま使う

スプレッドシートのタブ(1行目がヘッダー。名前は半角英数で一致させる):
  holdings  名義 / 銘柄コード / 銘柄名 / 取得単価 / 株数 / 証券会社 / 取得日
  watch     名義 / 銘柄コード / 銘柄名 / 目標価格
  settings  名義 / しきい値(%) / 通知ON
  brokers   証券会社 / URL   (証券会社とリンク先の一覧。家族で共通。無くても他の機能は動く)
holdings の「証券会社」には、brokers に登録した証券会社名を入れる(表で名前をクリックするとリンク先が開く)。
※ログインID・パスワード・口座番号は、スプレッドシートに入れないこと。

【通貨のルール】
  日本株(7203.T など) … 取得単価・目標価格は「円」
  米国株(BDX など)    … 取得単価・目標価格は「ドル」(その銘柄の売買通貨で入力する)
  評価額・含み損益は、米国株を現在のドル円レート(USDJPY=X)で円換算して表示・合計する。
【取得単価】
  空欄 = 不明(含み損益は計算しない) / 0 = 無償取得(スピンオフ・株式分配など。評価額の全額が含み益)
【取得日と為替込み損益】
  米国株は、取得日(購入日)のドル円レート(終値。休日は直前の営業日)を「取得時レート(推定)」として使い、
  為替込み損益(円) = 現在値×株数×今のレート − 取得単価×株数×取得時レート を出す。
  取得日が空欄の米国株は為替込み損益を出さない(「含み損益」=為替差を除いた株価の損益 は出す)。
  取得単価が0(無償取得)なら取得額は0円なので、取得日がなくても為替込み損益を出す。
"""
import re
import time
import unicodedata
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

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
FX_USDJPY = "USDJPY=X"  # ドル円レートの取得に使う
JST = ZoneInfo("Asia/Tokyo")

COLUMNS = {
    "holdings": ["名義", "銘柄コード", "銘柄名", "取得単価", "株数", "証券会社", "取得日"],
    "watch": ["名義", "銘柄コード", "銘柄名", "目標価格"],
    "settings": ["名義", "しきい値(%)", "通知ON"],
    "brokers": ["証券会社", "URL"],
}
NO_BROKER = "（選択しない）"
NUMERIC_COLUMNS = ["取得単価", "株数", "目標価格", "しきい値(%)"]
TRUE_VALUES = ["TRUE", "1", "はい", "ON"]
TEXT_COLUMNS = ("銘柄コード", "銘柄名", "証券会社", "通知", "通貨")


# ---------------------------------------------------------
# スプレッドシートの読み書き(common.py の関数を利用)
# ---------------------------------------------------------
def num_str(v) -> str:
    """数値をシートに書く文字列にする(300.0 -> '300'、2500.5 -> '2500.5')。None は空欄('')"""
    if v is None:
        return ""
    v = float(v)
    return str(int(v)) if v.is_integer() else str(round(v, 4))


def default_settings_str() -> pd.DataFrame:
    n = len(DEFAULT_MEMBERS)
    return pd.DataFrame(
        {"名義": DEFAULT_MEMBERS, "しきい値(%)": [num_str(DEFAULT_THRESHOLD)] * n, "通知ON": ["TRUE"] * n}
    )


def parse_date_cell(v):
    """'2023-05-12' / '2023/5/12' / '2023年5月12日' -> date。空欄・読めない値は None"""
    s = unicodedata.normalize("NFKC", str(v)).strip()
    m = re.match(r"^(\d{4})\D+(\d{1,2})\D+(\d{1,2})", s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def as_date(v):
    """date ならそのまま、空欄(None/NaN)は None"""
    return v if isinstance(v, date) and not isinstance(v, datetime) else None


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
    for c in ["名義", "銘柄コード", "銘柄名", "証券会社", "URL"]:
        if c in df:
            df[c] = df[c].astype(str).str.strip()
    if "取得日" in df:
        df["取得日"] = df["取得日"].map(parse_date_cell)
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
            st.info(f"{len(COLUMNS)}つのタブはあります。1行目のヘッダーと、タブ名の前後の空白を確認してください。")
    except Exception as e:
        st.write(f"診断できませんでした: {type(e).__name__}: {str(e)[:200]}")


def load_all() -> dict:
    data = {}
    for kind, cols in COLUMNS.items():
        df, ok, err = fetch_sheet(kind, cols)
        if not ok and kind == "brokers":  # 証券会社の一覧は任意。読めなくても他の機能は使えるようにする
            st.warning(
                "「brokers」タブを読み込めませんでした。証券会社のリンクを使うには、スプレッドシートに "
                "brokers タブを作り、1行目に「証券会社」「URL」と入力してください。"
            )
            data[kind] = typed(kind, pd.DataFrame(columns=cols))
            continue
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
        return out.reindex(columns=COLUMNS[kind]).fillna("")  # 列が増えた古いデータでも、空欄で埋める
    return modifier


def make_broker_upsert(name: str, url: str):
    def modifier(latest: pd.DataFrame) -> pd.DataFrame:
        keep = latest[latest["証券会社"] != name]
        new = pd.DataFrame([{"証券会社": name, "URL": url}])
        out = new if keep.empty else pd.concat([keep, new], ignore_index=True)
        return out[COLUMNS["brokers"]]
    return modifier


def make_broker_delete(names: list):
    def modifier(latest: pd.DataFrame) -> pd.DataFrame:
        return latest[~latest["証券会社"].isin(names)]
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
    """日本株は '7203' / '７２０３' / '7203.t' / '285A' -> '7203.T'。
    米国株などアルファベットのコード('BDX', 'embc')は .T を付けずそのまま('BDX')。"""
    code = unicodedata.normalize("NFKC", raw).strip().upper()
    if "." in code:
        return code
    if re.fullmatch(r"\d{3}[0-9A-Z]", code):  # 日本株: 数字3桁+数字or英字1桁
        return f"{code}.T"
    return code


def currency_of(code: str) -> str:
    """銘柄コードから売買通貨を判定: 日本株=円 / サフィックスなし=米国株=USD / それ以外は未対応('?')"""
    if code.endswith(".T"):
        return "円"
    if "." not in code:
        return "USD"
    return "?"


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


def rate_to_jpy(cur: str):
    """その通貨1単位あたりの円。円=1.0、USD=ドル円レート。取得できない/未対応は None。"""
    if cur == "円":
        return 1.0
    if cur == "USD":
        return fetch_price(FX_USDJPY).get("last")
    return None


def fx_on(d: date):
    """取得日(d)のドル円レート(終値。休日は直前の営業日)。取得できなければ None。過去の値は変わらないので使い回す。"""
    cache = _store().setdefault("fx_hist", {})
    if d in cache:
        return cache[d]
    try:
        closes = yf.Ticker(FX_USDJPY).history(start=d - timedelta(days=7), end=d + timedelta(days=1))["Close"].dropna()
        closes = closes[[x.date() <= d for x in closes.index]]
        if closes.empty:
            return None
        rate = float(closes.iloc[-1])
    except Exception:
        return None
    cache[d] = rate
    return rate


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
def broker_link(name, urls: dict) -> str:
    """表のリンク用の値。'https://…#証券会社名' の形にして、表示は # 以降の名前だけにする。
    リンク先が未登録なら '#証券会社名'(クリックしても移動しない)"""
    name = "" if name is None or pd.isna(name) else str(name).strip()
    return f"{urls.get(name, '')}#{name}" if name else ""


def build_table(kind: str, df: pd.DataFrame, threshold: float, broker_urls: dict = None) -> pd.DataFrame:
    broker_urls = broker_urls or {}
    rows = []
    for _, r in df.iterrows():
        code = r["銘柄コード"]
        p = fetch_price(code)
        last, prev = p.get("last"), p.get("prev")
        chg = (last - prev) if last is not None and prev else None
        pct = (chg / prev * 100) if chg is not None else None
        cur = currency_of(code)
        rate = rate_to_jpy(cur)
        row = {"銘柄コード": code, "銘柄名": r["銘柄名"]}
        if kind == "holdings":
            row["証券会社"] = broker_link(r.get("証券会社"), broker_urls)
        row.update({
            "通知": "⚠" if pct is not None and abs(pct) >= threshold else "",
            "通貨": cur,
            "現在値": last,
            "前日比": chg,
            "前日比%": pct,
        })
        if kind == "holdings":
            cost, qty = r["取得単価"], r["株数"]
            has_cost = pd.notna(cost) and cost >= 0  # 空欄だけが「未入力」。0は「無償取得(0円/0ドル)」
            can_value = last is not None and rate is not None
            # 取得時レート(推定): 日本株は1、米国株は取得日のドル円(取得日が空欄なら None)
            buy_date = as_date(r.get("取得日"))
            buy_rate = 1.0 if cur == "円" else (fx_on(buy_date) if cur == "USD" and buy_date else None)
            row["取得単価"] = cost if has_cost else None
            row["株数"] = qty
            row["取得時レート(推定)"] = buy_rate if cur == "USD" else None
            # 評価額・含み損益は円換算(米国株は 現在値(ドル) × 株数 × ドル円)
            row["評価額"] = last * qty * rate if can_value else None
            row["含み損益"] = (last - cost) * qty * rate if can_value and has_cost else None
            # 為替込み損益: 取得額(円) = 取得単価 × 株数 × 取得時レート。無償取得(0)ならレート不要で取得額0円
            if can_value and has_cost and cost == 0:
                row["為替込み損益"] = last * qty * rate
            elif can_value and has_cost and buy_rate:
                row["為替込み損益"] = last * qty * rate - cost * qty * buy_rate
            else:
                row["為替込み損益"] = None
            # 損益率は株価ベース(為替変動は含まない)。取得単価0だと割れないので「-」
            row["損益率%"] = (last - cost) / cost * 100 if last is not None and has_cost and cost > 0 else None
        else:
            target = r["目標価格"]
            row["目標価格"] = target
            row["目標との差%"] = (last - target) / target * 100 if last is not None and target else None
        rows.append(row)
    out = pd.DataFrame(rows)
    for c in out.columns:
        if c not in TEXT_COLUMNS:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def _trim(s: str) -> str:
    return s.rstrip("0").rstrip(".") if "." in s else s


def fmt_price(v) -> str:
    """3941.0 -> '3,941' / 8.08 -> '8.08' / 325.6 -> '325.6'(円もドルも見やすく)"""
    return _trim(f"{v:,.2f}")


def fmt_signed(v) -> str:
    return _trim(f"{v:+,.2f}")


def style_table(table: pd.DataFrame, kind: str):
    """プラスは緑、マイナスは赤(日本の株アプリは逆の場合もあるので好みで入れ替え可)"""
    def color(v):
        if pd.isna(v):
            return ""
        return "color: #d62728" if v < 0 else "color: #2ca02c" if v > 0 else ""

    signed = ["前日比", "前日比%"] + (["含み損益", "為替込み損益", "損益率%"] if kind == "holdings" else [])
    fmt = {c: fmt_price for c in ["現在値", "取得単価", "目標価格"] if c in table}
    fmt["前日比"] = fmt_signed
    fmt.update({c: "{:+.2f}%" for c in ["前日比%", "損益率%", "目標との差%"] if c in table})
    fmt.update({c: "{:,.0f}" for c in ["株数", "評価額"] if c in table})
    for c in ["含み損益", "為替込み損益"]:
        if c in table:
            fmt[c] = "{:+,.0f}"
    if "取得時レート(推定)" in table:
        fmt["取得時レート(推定)"] = "{:,.2f}"
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
    # 取得額(円換算)= 評価額 − 含み損益。取得単価が空欄の銘柄は除く(0の銘柄は取得額0として含む)
    valid = table["含み損益"].notna()
    cost = (table.loc[valid, "評価額"] - table.loc[valid, "含み損益"]).sum()
    alerts = int((table["通知"] == "⚠").sum())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("評価額 合計(円)", f"{total:,.0f} 円")
    c2.metric("含み損益 合計(円)", f"{pnl:+,.0f} 円", f"{pnl / cost * 100:+.2f}%" if cost else None)
    c3.metric("銘柄数", f"{len(table)} 銘柄")
    c4.metric("しきい値超え", f"{alerts} 銘柄")
    if (table["通貨"] == "USD").any():
        fx = table["為替込み損益"]
        usd_no_date = int(((table["通貨"] == "USD") & table["取得単価"].notna() & (table["取得単価"] > 0) & fx.isna()).sum())
        note = f"(取得日が未入力の米国株 {usd_no_date} 銘柄は含まず)" if usd_no_date else ""
        st.caption(f"💴 為替込み損益 合計: {fx.sum():+,.0f} 円 {note}")
    missing = int(table["取得単価"].isna().sum())
    if missing:
        st.caption(f"※ 取得単価が未入力(空欄)の {missing} 銘柄は、含み損益の合計に含めていません。")
    no_value = int(table["評価額"].isna().sum())
    if no_value:
        st.caption(f"※ 株価または為替レートが取得できなかった {no_value} 銘柄は、評価額の合計に含めていません。")


def render_fx_note(table: pd.DataFrame) -> None:
    """米国株があるとき、円換算に使ったレートを表示する"""
    if (table["通貨"] == "USD").any():
        rate = rate_to_jpy("USD")
        if rate:
            st.caption(
                f"💱 米国株は 1ドル={rate:,.2f}円 で円換算(評価額・含み損益)。"
                "損益率%は株価ベースで、為替の変動は含みません。"
            )
        else:
            st.warning("ドル円レートを取得できなかったため、米国株の評価額・含み損益は表示していません。")
    if (table["通貨"] == "?").any():
        st.caption("※ 日本株・米国株以外(通貨が判定できない銘柄)は、円換算していません。")


def render_add_form(kind: str, owner: str, df: pd.DataFrame, broker_names: list = ()) -> None:
    with st.expander("➕ 銘柄を登録・更新", expanded=df.empty):
        with st.form(f"add_{kind}_{owner}", clear_on_submit=True):
            code_in = st.text_input("銘柄コード(日本株は 7203、米国株は BDX のようにティッカー)")
            name_in = st.text_input("銘柄名(空欄なら自動取得)")
            if kind == "holdings":
                cost = st.number_input(
                    "取得単価(日本株は円 / 米国株はドル)", min_value=0.0, step=1.0, value=None,
                    placeholder="不明なら空欄",
                    help="無償で取得した株(スピンオフなど)は 0 を入力してください。"
                         "分からない場合は空欄のままで構いません(その銘柄の含み損益は表示されません)。あとから編集できます。",
                )
                qty = st.number_input("株数", min_value=0, step=100, value=100)
                buy_date = st.date_input(
                    "取得日(購入日)", value=None, min_value=date(2000, 1, 1), max_value=datetime.now(JST).date(),
                    help="米国株は、この日のドル円レートで為替込み損益を計算します。分からなければ空欄で構いません。"
                         "複数回に分けて買った場合は、おおよその日(平均的な時期)を入れてください。",
                )
                broker = st.selectbox(
                    "証券会社", [NO_BROKER] + list(broker_names),
                    help="一覧にない場合は、下の「証券会社とリンクの登録」で先に登録してください。",
                )
            else:
                target = st.number_input("目標価格(日本株は円 / 米国株はドル)", min_value=0.0, step=1.0)
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
            new = {
                "銘柄コード": code, "銘柄名": name, "取得単価": num_str(cost), "株数": num_str(qty),
                "証券会社": "" if broker == NO_BROKER else broker,
                "取得日": str(buy_date) if buy_date else "",
            }
        else:
            new = {"銘柄コード": code, "銘柄名": name, "目標価格": num_str(target)}
        apply_and_save(kind, COLUMNS[kind], make_upsert(kind, owner, new), f"{name}({code})を登録しました。")


def _num(v) -> float:
    return float(v) if pd.notna(v) else 0.0


def _opt(v):
    """空欄(NaN)は None、0を含む数値はそのまま"""
    return float(v) if pd.notna(v) else None


def render_edit_form(kind: str, owner: str, df: pd.DataFrame, broker_names: list = ()) -> None:
    """登録済みの銘柄の取得単価・株数・証券会社(注目株は目標価格)を、選んで書き換える"""
    if df.empty:
        return
    label_text = "取得単価・株数・証券会社" if kind == "holdings" else "目標価格"
    with st.expander(f"✏ 登録済みの銘柄を編集({label_text})"):
        options = {f"{r['銘柄名']}({r['銘柄コード']})": i for i, r in df.iterrows()}
        label = st.selectbox("編集する銘柄", list(options), key=f"edit_sel_{kind}_{owner}")
        r = df.iloc[options[label]]
        code = r["銘柄コード"]
        unit = {"円": "円", "USD": "ドル"}.get(currency_of(code), "現地通貨")
        k = f"{kind}_{owner}_{code}"  # 銘柄を切り替えたとき、入力欄の値も切り替わるようにする
        if kind == "holdings":
            cost = st.number_input(
                f"取得単価({unit})", min_value=0.0, step=1.0, value=_opt(r["取得単価"]),
                placeholder="不明なら空欄", key=f"edit_cost_{k}",
                help="無償で取得した株(スピンオフなど)は 0。不明な場合は空欄。",
            )
            qty = st.number_input("株数", min_value=0, step=100, value=int(_num(r["株数"])), key=f"edit_qty_{k}")
            buy_date = st.date_input(
                "取得日(購入日)", value=as_date(r.get("取得日")), min_value=date(2000, 1, 1),
                max_value=datetime.now(JST).date(), key=f"edit_date_{k}",
                help="米国株は、この日のドル円レートで為替込み損益を計算します。空欄でもOKです。",
            )
            cur_broker = str(r["証券会社"]).strip() if pd.notna(r["証券会社"]) else ""
            names = list(broker_names) + ([cur_broker] if cur_broker and cur_broker not in broker_names else [])
            choices = [NO_BROKER] + names
            broker = st.selectbox(
                "証券会社", choices, index=choices.index(cur_broker) if cur_broker else 0, key=f"edit_broker_{k}"
            )
            new = {
                "銘柄コード": code, "銘柄名": r["銘柄名"], "取得単価": num_str(cost), "株数": num_str(qty),
                "証券会社": "" if broker == NO_BROKER else broker,
                "取得日": str(buy_date) if buy_date else "",
            }
        else:
            target = st.number_input(
                f"目標価格({unit})", min_value=0.0, step=1.0, value=_num(r["目標価格"]), key=f"edit_target_{k}"
            )
            new = {"銘柄コード": code, "銘柄名": r["銘柄名"], "目標価格": num_str(target)}
        if st.button("更新する", key=f"edit_btn_{k}"):
            apply_and_save(kind, COLUMNS[kind], make_upsert(kind, owner, new), f"{r['銘柄名']}({code})を更新しました。")


def render_delete_form(kind: str, owner: str, df: pd.DataFrame) -> None:
    if df.empty:
        return
    with st.expander("🗑 銘柄を削除"):
        options = {f"{r['銘柄名']}({r['銘柄コード']})": r["銘柄コード"] for _, r in df.iterrows()}
        picked = st.multiselect("削除する銘柄", list(options), key=f"del_{kind}_{owner}")
        if st.button("削除する", key=f"delbtn_{kind}_{owner}", disabled=not picked):
            codes = [options[p] for p in picked]
            apply_and_save(kind, COLUMNS[kind], make_delete(owner, codes), f"{len(codes)}銘柄を削除しました。")


def render_broker_manager(brokers: pd.DataFrame) -> None:
    """証券会社名とリンク先の登録(家族で共通)"""
    with st.expander("🏦 証券会社とリンクの登録"):
        st.caption(
            "ここで登録した証券会社を、保有株の登録・編集で選べます。保有株の表で証券会社名をクリックすると、"
            "登録したリンク先が開きます。 ※ログインID・パスワード・口座番号は入れないでください。"
        )
        if not brokers.empty:
            st.dataframe(brokers, hide_index=True)
        with st.form("add_broker", clear_on_submit=True):
            b_name = st.text_input("証券会社名(例: ○○証券)")
            b_url = st.text_input("リンク先(公式サイトのログインページなど。https:// から)")
            b_saved = st.form_submit_button("登録・更新")
        if b_saved:
            b_name, b_url = b_name.strip(), b_url.strip()
            if not b_name:
                st.error("証券会社名を入力してください。")
            elif "#" in b_name:
                st.error("証券会社名に # は使えません。")
            elif not b_url.lower().startswith("https://"):
                st.error("リンク先は https:// から始まるアドレスを入力してください。")
            else:
                apply_and_save(
                    "brokers", COLUMNS["brokers"], make_broker_upsert(b_name, b_url), f"{b_name}を登録しました。"
                )
        if not brokers.empty:
            picked = st.multiselect("削除する証券会社", list(brokers["証券会社"]), key="del_brokers")
            st.caption("削除しても、保有株に入っている証券会社名はそのまま残ります(リンクだけ使えなくなります)。")
            if st.button("削除する", key="delbtn_brokers", disabled=not picked):
                apply_and_save(
                    "brokers", COLUMNS["brokers"], make_broker_delete(picked), f"{len(picked)}件を削除しました。"
                )


def render_stock_tab(kind: str, owner: str, df: pd.DataFrame, threshold: float, brokers: pd.DataFrame = None) -> None:
    has_brokers = brokers is not None and not brokers.empty
    broker_names = list(brokers["証券会社"]) if has_brokers else []
    broker_urls = dict(zip(brokers["証券会社"], brokers["URL"])) if has_brokers else {}

    render_add_form(kind, owner, df, broker_names)

    if df.empty:
        st.info("まだ銘柄が登録されていません。上の「銘柄を登録」から追加してください。")
    else:
        with st.spinner("株価を取得中..."):
            table = build_table(kind, df, threshold, broker_urls)

        if kind == "holdings":
            render_summary(table)
        config = None
        if kind == "holdings":  # 証券会社名をクリックすると、登録したリンク先を開く
            config = {
                "証券会社": st.column_config.LinkColumn(
                    "証券会社", display_text=r"#([^#]*)$", help="クリックすると、登録したリンク先が開きます"
                )
            }
        st.dataframe(style_table(table, kind), hide_index=True, column_config=config)
        st.caption(
            f"⚠ = 前日比が ±{threshold:g}% 以上(通知対象) / "
            "現在値・取得単価は「通貨」列の通貨(円 or USD)、評価額・損益は円換算 / "
            "含み損益=株価の損益(為替差を除く)、為替込み損益=取得日のレートで計算(推定)"
            if kind == "holdings"
            else f"⚠ = 前日比が ±{threshold:g}% 以上(通知対象) / 現在値・目標価格は「通貨」列の通貨(円 or USD)"
        )
        render_fx_note(table)
        render_edit_form(kind, owner, df, broker_names)
        render_delete_form(kind, owner, df)

    if kind == "holdings" and brokers is not None:
        render_broker_manager(brokers)


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
        f"最終取得: {datetime.now(ZoneInfo('Asia/Tokyo')):%Y-%m-%d %H:%M:%S} / "
        f"{CACHE_SECONDS // 60}分以内の再表示は前回の値を使用 / 日本株は約20分遅延の場合あり"
    )
    if right.button("🔄 最新の株価に更新"):
        clear_prices()
        st.rerun()

    tab_h, tab_w, tab_s = st.tabs(["保有株", "注目株", "通知設定"])
    with tab_h:
        render_stock_tab("holdings", owner, rows_of(data["holdings"], owner), threshold, data["brokers"])
    with tab_w:
        render_stock_tab("watch", owner, rows_of(data["watch"], owner), threshold)
    with tab_s:
        render_settings_tab(owner, settings)


if __name__ == "__main__":
    main()
