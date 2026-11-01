"""表記のゆれをそろえる。看板と届出を比べる前に、必ずここを通す。

看板も届出も人が書いたものなので、同じことが何通りにも書かれている。
比べる前にここで1つの形に寄せる。寄せ方を変えるときはこのファイルだけを直す。

- 丁目は漢数字にそろえる（「上目黒3丁目」→「上目黒三丁目」）
- 「ヶ」「ケ」のゆれは「ヶ」に寄せる
- 構造は決まった値にそろえる（木造 / S造 / RC造 / SRC造 / その他）
"""
import re
import unicodedata

KANJI_DIGITS = "〇一二三四五六七八九"

# 先に書いたものが優先。同じ位置で当たったときは上の行が勝つので、
# SRC造を RC造・S造より先に、RC造を S造より先に置いている。
STRUCTURE_PATTERNS = [
    ("SRC造", r"鉄骨鉄筋コンクリート|SRC"),
    ("RC造", r"鉄筋コンクリート|RC"),
    ("S造", r"鉄骨|S造"),
    ("木造", r"木造|木質|W造"),
]


def _kanji_number(n: int) -> str:
    """丁目に出る範囲（1〜99）の数字を漢数字にする。"""
    if n < 10:
        return KANJI_DIGITS[n]
    tens, ones = divmod(n, 10)
    return ("" if tens == 1 else KANJI_DIGITS[tens]) + "十" + ("" if ones == 0 else KANJI_DIGITS[ones])


def text(value) -> str | None:
    """文字を比較用の形にそろえる。

    全角と半角、空白、ダッシュの種類、「ヶ」「ケ」、丁目の数字の書き方を吸収する。
    """
    if value is None:
        return None
    s = unicodedata.normalize("NFKC", str(value))
    s = re.sub(r"\s+", "", s)
    s = s.replace("ケ", "ヶ")
    # 「自由が丘」「緑が丘」「東が丘」は、位置参照情報が「が丘」、
    # 看板や届出が「ヶ丘」と書くことがある。街区の代表点を引けるようにそろえる。
    s = s.replace("が丘", "ヶ丘")
    s = re.sub(r"[‐‑‒–—―−]", "-", s)  # 長音「ー」は名前の一部なので触らない
    s = re.sub(r"[,，]", "、", s)
    s = re.sub(r"(\d+)丁目", lambda m: _kanji_number(int(m.group(1))) + "丁目", s)
    return s or None


def chome(value) -> str | None:
    """町丁目を「上目黒三丁目」の形にそろえる。都・区の前置きは落とす。

    届出のCSVが町丁目までで持っているので、そちらに合わせる。
    """
    s = text(value)
    if s is None:
        return None
    s = re.sub(r"^東京都", "", s)
    s = re.sub(r"^目黒区", "", s)
    return s or None


def chome_in(value) -> str | None:
    """地名地番や住居表示の欄の文字から、町丁目の部分だけを取り出す。

    「目黒区 青葉台 1 丁目 1349 番 4」→「青葉台一丁目」
    """
    s = chome(value)
    if s is None:
        return None
    m = re.match(r"(.*?[〇一二三四五六七八九十]+丁目)", s)
    return m.group(1) if m else None


def structure(value) -> str | None:
    """構造を決まった値にそろえる。

    「木造(軸組)」は木造、「鉄筋コンクリート造一部鉄骨造」はRC造のように、
    最初に書かれている構造を主な構造として採る。当てはまらなければ「その他」。
    """
    if value is None:
        return None
    s = unicodedata.normalize("NFKC", str(value))
    best = None
    for rank, (label, pattern) in enumerate(STRUCTURE_PATTERNS):
        m = re.search(pattern, s, re.IGNORECASE)
        if m and (best is None or (m.start(), rank) < best[:2]):
            best = (m.start(), rank, label)
    return best[2] if best else "その他"


def parcel(value) -> str | None:
    """地番を「1349-4」の形にそろえる。「1349番4」「1349番地4」「1349の4」も同じ形にする。"""
    if value is None:
        return None
    s = unicodedata.normalize("NFKC", str(value))
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"番地?|の", "-", s)
    s = re.sub(r"[^0-9-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or None


def parcels_in(value) -> list[str]:
    """地名地番の欄の文字から、地番の一覧を取り出す（届出のCSVはこの形で持っている）。"""
    if value is None:
        return []
    s = unicodedata.normalize("NFKC", str(value))
    s = re.sub(r"\s+", "", s)
    s = re.split(r"住居表示", s)[0]  # 同じ欄に住居表示が続けて書かれていることがある
    found = []
    for m in re.finditer(r"(\d+)(?:番地?|の|-)(\d+)?", s):
        found.append(f"{m.group(1)}-{m.group(2)}" if m.group(2) else m.group(1))
    return found


def block_number(value) -> int | None:
    """街区番号を整数にする（「17番」「17」→ 17）。"""
    if value is None:
        return None
    s = unicodedata.normalize("NFKC", str(value))
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None
