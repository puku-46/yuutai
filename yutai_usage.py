"""優待の「使用を記録」まわりの計算(Streamlit非依存。pages/1_yutai.py から使う)

「数量/金額」列を「残り」として扱い、使った分を引き算して書き戻す。
  例) "3,000円分" から 1,000 使う → "2,000円分"   / "5枚" から 2 使う → "3枚"
履歴は「メモ」列に1行ずつ追記する(例: 11/17 使用 2枚 → 残り 3枚)。
残りが0になったらステータスを「利用完了」、残りがあれば「一部利用」にする。

数量/金額が「数字で始まり、そのあとに数字を含まない単位」の形のときだけ自動計算できる。
  OK: "5" "5.0" "3,000円分" "10枚"      NG: "500円×6枚" "食事券6枚"(→ 手入力で更新)
"""
import re
from datetime import date

AMOUNT_RE = re.compile(r"^\s*(\d[\d,]*(?:\.\d+)?)\s*(\D*?)\s*$")


def clean_amount(value) -> str:
    """スプレッドシートから読むと '5' が '5.0' になるので、表示用に直す('5.0' -> '5')"""
    return re.sub(r"^(-?\d+)\.0+$", r"\1", str(value).strip())


def parse_amount(value):
    """'3,000円分' -> {'num': 3000.0, 'unit': '円分', 'comma': True}。自動計算できない形は None"""
    m = AMOUNT_RE.match(str(value))
    if not m:
        return None
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return {"num": num, "unit": m.group(2), "comma": "," in m.group(1)}


def fmt_num(x, comma: bool = False) -> str:
    x = round(float(x), 6)
    if x.is_integer():
        return f"{int(x):,}" if comma else str(int(x))
    return f"{x:.2f}".rstrip("0").rstrip(".")


def fmt_amount(p: dict) -> str:
    return fmt_num(p["num"], p["comma"]) + p["unit"]


# ---------------------------------------------------------
# apply_and_save に渡す「保存直前の最新データを書き換える関数」を作る
# ---------------------------------------------------------
def _find(d, use_id):
    idx = d.index[d["ID"] == use_id]
    if len(idx) == 0:
        raise ValueError("対象のデータが見つかりません(他の方が削除した可能性があります)。")
    return idx[0]


def _append_memo(d, i, line: str) -> None:
    current = str(d.at[i, "メモ"]).strip()
    d.at[i, "メモ"] = f"{current}\n{line}" if current else line


def record_use(use_id, used: float, today: date):
    """使った数を記録する(残りを減らす)"""
    def modifier(d):
        if used <= 0:
            raise ValueError("使った数は0より大きい数を入力してください。")
        i = _find(d, use_id)
        p = parse_amount(d.at[i, "数量/金額"])
        if p is None:
            raise ValueError("「数量/金額」が『数字+単位』の形ではないため、自動計算できません。")
        remaining = round(p["num"] - used, 6)
        if remaining < 0:
            raise ValueError(
                f"残り({fmt_amount(p)})より多くは使えません。"
                "他の方が先に更新した可能性があるので、再読み込みしてください。"
            )
        new_text = fmt_amount({**p, "num": remaining})
        d.at[i, "数量/金額"] = new_text
        d.at[i, "ステータス"] = "利用完了" if remaining == 0 else "一部利用"
        _append_memo(d, i, f"{today:%m/%d} 使用 {fmt_num(used, p['comma'])}{p['unit']} → 残り {new_text}")
        return d
    return modifier


def record_all_used(use_id, today: date):
    """全部使った(利用完了にする)。数量/金額が計算できる形なら 0 にする"""
    def modifier(d):
        i = _find(d, use_id)
        p = parse_amount(d.at[i, "数量/金額"])
        if p is not None:
            d.at[i, "数量/金額"] = fmt_amount({**p, "num": 0})
        d.at[i, "ステータス"] = "利用完了"
        _append_memo(d, i, f"{today:%m/%d} 全て使用 → 利用完了")
        return d
    return modifier


def record_manual(use_id, new_remaining: str, today: date, note: str = ""):
    """数量/金額が自動計算できない形のとき、残りを手入力で更新する(一部利用にする)"""
    def modifier(d):
        text = new_remaining.strip()
        if not text:
            raise ValueError("新しい残りを入力してください。")
        i = _find(d, use_id)
        d.at[i, "数量/金額"] = text
        d.at[i, "ステータス"] = "一部利用"
        suffix = f"({note.strip()})" if note.strip() else ""
        _append_memo(d, i, f"{today:%m/%d} 残りを「{text}」に更新{suffix}")
        return d
    return modifier
