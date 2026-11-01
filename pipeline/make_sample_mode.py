"""エージェントの判断の記録を、サンプルモード用の公開データにする。

    python pipeline/make_sample_mode.py

読むのは `data/agent/*.json`（`pipeline/agent.py` が残した記録。リポジトリに入れない）。
出すのは **`web/public/samples.json`**。ここは公開されるので、
入れてよいものだけを入れる（dev-policy §9）。

審査員は日本の看板を撮れないので、**APIを呼ばずに**事前に計算した結果を見せる。
画面は判断の記録を1手ずつ流し、最後に3Dで建物が立ち上がる（`web/src/sample.ts`）。

## 看板の写真は出さない

dev-policy §4 で、建築主・設計者・施工者・担当者の氏名は出さないと決めている。
看板にはそれが大きく印刷されているので、**切り出し画像をそのまま公開できない**。
欄をぼかす手は、ぼかし漏れが公開リポジトリに入ると取り返せないため、
**1枚ずつ目で確かめるまで写真は出さない**（§9の「ぼかせない場合は値だけ」）。
このスクリプトは写真のパスも公開物に書かない。

## 公開してよい形かはコードで確かめる

`check_publishable()` が通らなければ**書き出さずに止まる**。確かめるのは:

  1. 「建築物の名称」がどこにも入っていないこと（「○○様邸」が入りうる）。
     キーの名前でも、文の中の文字列でも落とす
  2. 写真のパスが入っていないこと
  3. 終わり方が決めた4つ（dev-policy §15）のどれかであること
  4. 届出を使った件に「確認前／確認済み」の印が付いていること
  5. 読み取った値の項目が許可リストの中だけであること
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import evaluate
import normalize
from agent import OUTCOMES

RECORD_DIR = Path("data/agent")

# 公開される場所に直接書き出す（`data/` からコピーしない。dev-policy §9）
OUT = Path("web/public/samples.json")

SOURCE = "目黒区 中高層建築物の標識設置届"
LICENSE = "CC BY 4.0"
SOURCE_URL = "https://www.city.meguro.tokyo.jp/opendata/"

# 名前のキーは丸ごと落とす。「建築物の名称」は個人名が入りうる（「○○様邸」）。
DENY_KEYS = {"building_name", "owner", "designer", "builder", "contact",
             "phone", "tel", "person"}

# 公開してよい、読み取った値の項目。**ここに無い項目は出さない。**
# `make_planned_geojson.py` の許可リストと同じ考え方（dev-policy §4）。
ALLOWED_VALUE_FIELDS = {
    "notice_type", "use", "structure", "site_area_m2", "building_area_m2",
    "total_floor_area_m2", "floors_above", "floors_below",
    "height_m", "max_height_m", "eave_height_m", "units",
    "construction_start", "construction_end", "notice_posted",
}

# 写真のパスが公開物に混ざっていないかを確かめる目印。
PHOTO_PATH_MARKERS = ("data/photos", "samples/out", ".JPG", ".jpg", ".png")

# ツールを画面に出すときの日本語名。
TOOL_LABELS = {
    "read_board": "看板を読む",
    "check_consistency": "数値の矛盾を検算する",
    "reread_fields": "項目を読み直す",
    "lookup_filing": "区の届出と照合する",
    "geocode": "住居表示を座標にする",
    "extrude": "ボリュームを作る",
    "request_retake": "撮り直しを頼む",
    "finish": "結論を出す",
}


# ---------- 名前を落とす ----------

def known_names() -> set[str]:
    """落とすべき「建築物の名称」を集める。届出側と、看板の読み取り側の両方から。"""
    names = set()

    def add(value):
        for form in (value, normalize.text(value)):
            if form and len(str(form)) >= 3:
                names.add(str(form))

    for row in evaluate.load_filings():
        add(row.get("building_name"))
    for path in RECORD_DIR.glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        field = ((record.get("reading") or {}).get("fields") or {}).get("building_name") or {}
        add(field.get("value"))
        for step in record.get("steps") or []:
            output = step.get("output") or {}
            add((output.get("filing") or {}).get("building_name"))
            for candidate in output.get("candidates") or []:
                add(candidate.get("building_name"))
            add((step.get("input") or {}).get("building_name"))
    return names


def scrub_text(value: str, names: set[str]) -> str:
    """文の中に出てくる建築物の名称を伏せる。

    モデルが書いた理由や結論の文に名称が入っていることがある
    （「地番1735-8、○○新築工事は届出と照合済み」など）。
    """
    out = value
    # 長いものから消す（短い名前が長い名前の一部を食うのを防ぐ）
    for name in sorted(names, key=len, reverse=True):
        if name in out:
            out = out.replace(name, "（名称は非公開）")
    # 「(仮称)」付きの工事名が名称一覧に無い形で出てきたときの保険
    out = re.sub(r"[(（]仮称[)）][^、。\s]{0,30}?(新築|増築|改築|計画)工事",
                 "（名称は非公開）", out)
    return out


def scrub(value, names: set[str]):
    """名前のキーを落とし、文字列の中の名称を伏せる。再帰。"""
    if isinstance(value, dict):
        return {k: scrub(v, names) for k, v in value.items() if k not in DENY_KEYS}
    if isinstance(value, list):
        return [scrub(v, names) for v in value]
    if isinstance(value, str):
        return scrub_text(value, names)
    return value


# ---------- 1件を公開用にする ----------

def published_values(reading: dict | None) -> dict:
    """看板から読み取った値のうち、公開してよい項目だけ。"""
    if not reading:
        return {}
    out = {}
    for name, field in (reading.get("fields") or {}).items():
        if name not in ALLOWED_VALUE_FIELDS or not isinstance(field, dict):
            continue
        if field.get("value") is None:
            continue
        out[name] = {"value": field["value"], "status": field.get("status"),
                     "confidence": field.get("confidence")}
    # 地番と住居表示は位置の手がかりなので出す（区のオープンデータに載っている項目）
    lot = reading.get("lot") or {}
    if lot.get("parcels"):
        out["lot"] = {"value": "、".join(
            p["number"] + ("の一部" if p.get("partial") else "")
            for p in lot["parcels"] if p.get("number")), "status": lot.get("status")}
    address = reading.get("address") or {}
    if address.get("chome"):
        block = f"{address['block']}番" if address.get("block") else ""
        out["address"] = {"value": f"{address['chome']}{block}", "status": address.get("status")}
    return out


def step_detail(step: dict) -> str:
    """1手を画面に出すための短い説明。ツールごとにコードで作る。"""
    tool, out = step["tool"], step.get("output") or {}
    if out.get("error"):
        return f"エラー: {out['error']}"

    if tool == "read_board":
        fields = out.get("fields") or {}
        kind = (fields.get("notice_type") or {}).get("value")
        if kind == "other":
            return "建築計画のお知らせではない看板（労災保険関係成立票や許可票）"
        read = sum(1 for f in fields.values()
                   if isinstance(f, dict) and f.get("status") == "read")
        unreadable = [k for k, f in fields.items()
                      if isinstance(f, dict) and f.get("status") == "unreadable"]
        text = f"{read}項目を読んだ"
        if unreadable:
            text += f"／読めなかった項目: {'、'.join(unreadable)}"
        return text

    if tool == "check_consistency":
        issues = out.get("issues") or []
        low = out.get("low_confidence") or []
        parts = ["矛盾なし"] if not issues else [i["message"] for i in issues]
        if low:
            parts.append("確信度が低い項目: "
                         + "、".join(f"{x['field']}({x['confidence']})" for x in low))
        return "／".join(parts)

    if tool == "reread_fields":
        changed = out.get("changed") or {}
        if changed:
            return "／".join(f"{k}: {v['from']} → {v['to']}" for k, v in changed.items())
        return f"{'、'.join(out.get('reread') or [])} を読み直したが値は変わらず"

    if tool == "lookup_filing":
        if out.get("matches") == 1:
            diffs = [d for d in (out.get("differences") or [])
                     if d["outcome"] == evaluate.MISMATCH]
            agreed = len(out.get("agreed_fields") or [])
            text = f"{out['key']} で届出No.{(out.get('filing') or {}).get('filing_no')}に一致／{agreed}項目が一致"
            if diffs:
                text += "／不一致: " + "、".join(
                    f"{d['label']}（看板 {d['board']} ／ 届出 {d['filing']}）" for d in diffs)
            return text
        return f"{out.get('key')} では {out.get('matches')}件"

    if tool == "geocode":
        if out.get("matched"):
            return (f"{out['chome']}{out['block']}番の街区の代表点 "
                    f"{out['lat']:.6f}, {out['lon']:.6f}（街区の中のどこかは不明）")
        return out.get("error") or "座標にできなかった"

    if tool == "extrude":
        if out.get("built"):
            return (f"一辺 {out['side_m']}m・高さ {out['height_m']}m"
                    f"（底面 {out['base_z_m']}m → 天面 {out['top_z_m']}m、楕円体高）")
        return "作れなかった"

    if tool == "request_retake":
        return (step.get("input") or {}).get("reason") or "撮り直しを頼んだ"
    if tool == "finish":
        return OUTCOMES.get((step.get("input") or {}).get("outcome"), "結論を出した")
    return ""


def build_sample(record: dict, names: set[str]) -> dict:
    reading = record.get("reading")
    values = published_values(reading)

    steps = []
    for step in record.get("steps") or []:
        steps.append({
            "n": step["n"],
            "tool": step["tool"],
            "tool_label": TOOL_LABELS.get(step["tool"], step["tool"]),
            # モデルが次の手の前に書いた短い理由（commentary）
            "thought": step.get("thought"),
            "detail": step_detail(step),
            "input": step.get("input") or {},
            "ms": step.get("ms"),
            "failed": bool((step.get("output") or {}).get("error")),
        })

    address = values.get("address", {}).get("value")
    use = values.get("use", {}).get("value")
    outcome = record.get("outcome")

    sample = {
        "id": record["photo"],
        # 建築物の名称は使わない。場所と用途で見出しにする
        "label": "／".join(x for x in (address, use) if x) or "対象外の看板",
        "outcome": outcome,
        "outcome_label": OUTCOMES.get(outcome, ""),
        "summary": record.get("summary"),
        "unresolved": record.get("unresolved") or [],
        "filing_no": int(record["filing_no"]) if record.get("filing_no") else None,
        "match_key": record.get("match_key"),
        "filing_verified": record.get("filing_verified"),
        "status_label": None,
        "values": values,
        "steps": steps,
        "volume": record.get("volume"),
        "boards_found": record.get("boards_found"),
        # 看板の写真は出さない。なぜ出さないかも画面に書く
        "photo_published": False,
        "photo_note": "看板には建築主・設計者・施工者の氏名が印刷されているため、"
                      "写真は公開していません。読み取った値だけを出しています",
    }
    if sample["filing_no"] is not None:
        sample["status_label"] = "確認済み" if record.get("filing_verified") else "確認前"
    return scrub(sample, names)


# ---------- 公開前の確認 ----------

def check_publishable(data: dict, names: set[str]) -> list[str]:
    """公開してよい形かをコードで確かめる。問題の一覧を返す（空なら合格）。"""
    problems = []
    blob = json.dumps(data, ensure_ascii=False)

    # 1. 建築物の名称がキーとしても文字列としても残っていないこと
    def walk(value, path="$"):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in DENY_KEYS:
                    problems.append(f"{path}: 落とすべきキー {key!r} が残っている")
                walk(child, f"{path}.{key}")
        elif isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, f"{path}[{i}]")

    walk(data)
    for name in names:
        if name in blob:
            problems.append(f"建築物の名称 {name!r} が公開物に残っている")

    # 2. 写真のパスやファイル名
    for marker in PHOTO_PATH_MARKERS:
        if marker in blob:
            problems.append(f"写真のパスらしい文字 {marker!r} が公開物に入っている")

    # 3〜5. 1件ずつ
    for sample in data["samples"]:
        sid = sample.get("id")
        if sample.get("outcome") not in OUTCOMES:
            problems.append(f"{sid}: 終わり方 {sample.get('outcome')!r} が決めた4つにない")
        if not sample.get("summary"):
            problems.append(f"{sid}: 利用者に見せる説明がない")
        if sample.get("filing_no") is not None:
            expected = "確認済み" if sample.get("filing_verified") else "確認前"
            if sample.get("status_label") != expected:
                problems.append(
                    f"{sid}: status_label が {sample.get('status_label')!r}、"
                    f"filing_verified={sample.get('filing_verified')} と合わない")
        if sample.get("photo_published"):
            problems.append(f"{sid}: 看板の写真を公開しようとしている"
                            "（氏名のぼかしを目で確かめるまで出さない）")
        for field in sample.get("values") or {}:
            if field not in ALLOWED_VALUE_FIELDS | {"lot", "address"}:
                problems.append(f"{sid}: 許可していない項目 {field!r} が入っている")

    sources = data.get("sources") or []
    if not any(SOURCE in s for s in sources):
        problems.append(f"sources に出どころ（{SOURCE}）がない")
    if not any(LICENSE in s for s in sources):
        problems.append(f"sources にライセンス（{LICENSE}）がない")
    return problems


def main():
    records = sorted(RECORD_DIR.glob("*.json"))
    if not records:
        raise SystemExit(f"{RECORD_DIR} に判断の記録がありません。"
                         "先に python pipeline/agent.py --all data/photos/<日付> を動かしてください")

    names = known_names()
    print(f"判断の記録 {len(records)}件、伏せる名称 {len(names)}件")

    samples = [build_sample(json.loads(p.read_text(encoding="utf-8")), names)
               for p in records]

    data = {
        "generated": date.today().isoformat(),
        "model": "muse-spark-1.3",
        "note": "撮った看板の写真をエージェントに通した結果を、そのまま保存したもの。"
                "画面を開いたときにモデルは呼ばれない。"
                "届出は目視確認が済んでいないので「確認前」の印が付く。"
                "位置は街区の代表点、形は建築面積と同じ面積の北向きの正方形で、いずれも近似。",
        "outcomes": OUTCOMES,
        "sources": [
            f"{SOURCE}（目黒区オープンデータ {LICENSE}） {SOURCE_URL}",
            "国土交通省 街区レベル位置参照情報（目黒区、2025年版）",
            "3D都市モデル（Project PLATEAU）目黒区（2025年度）/ 国土交通省 を加工して作成",
        ],
        "samples": samples,
    }

    problems = check_publishable(data, names)
    if problems:
        print(f"\n公開してよい形になっていません（{len(problems)}件）。書き出しを中止します:")
        for problem in problems[:20]:
            print(f"  - {problem}")
        raise SystemExit(1)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n{len(samples)}件を書き出しました → {OUT}（{OUT.stat().st_size // 1024}KB）")
    for s in samples:
        print(f"  {s['id']}: {s['outcome']}（{len(s['steps'])}手）"
              f"{' 3D有り' if s['volume'] else ''} {s['label']}")
    print("  公開前の確認: 名称・写真のパス・終わり方・確認前の印・項目の許可リスト すべて通過")


if __name__ == "__main__":
    main()
