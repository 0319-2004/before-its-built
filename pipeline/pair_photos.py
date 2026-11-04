"""近い写真（看板）と遠い写真（敷地全体）を、撮影時刻とGPSで組み合わせる。

dev-policy §8「1か所につき2枚：看板全体が読める近い写真と、敷地を含めた遠い写真」。
撮影は1地点ずつ回るので、**GPSが近く、時刻も近い**写真は同じ地点とみなせる。

各写真の役割は `data/agent/<写真>.json`（エージェントの記録）から決める:
- near  … 看板が検出でき（board_detected）、建築計画／解体のお知らせを読めた写真
- far   … 看板を切り出せなかった（遠すぎてSAMが枠を出せない）＝敷地を含めた遠い写真
- other … 建築計画のお知らせではない看板（労災票など）

    python pipeline/pair_photos.py data/photos/2026-11-03

出力は `data/photos_pairs.json`（**公開しない**。GPSが入る）。
"""
import json
import math
import sys
from pathlib import Path

from PIL import ExifTags, Image

AGENT_DIR = Path("data/agent")
OUT = Path("data/photos_pairs.json")
GPSIFD = ExifTags.IFD.GPSInfo

# 同じ地点とみなす閾値。撮影は1地点ずつ歩いて回るので、
# この両方を満たす写真を1つのクラスタにまとめる。
SAME_PLACE_M = 45.0
SAME_TIME_S = 240


def dms_to_deg(dms, ref) -> float:
    deg = float(dms[0]) + float(dms[1]) / 60 + float(dms[2]) / 3600
    return -deg if ref in ("S", "W") else deg


def read_exif(path: Path) -> dict:
    exif = Image.open(path).getexif()
    dt = exif.get(ExifTags.Base.DateTimeOriginal) or exif.get(ExifTags.Base.DateTime)
    gps = exif.get_ifd(GPSIFD)
    lat = lon = None
    if gps and 2 in gps and 4 in gps:
        lat = dms_to_deg(gps[2], gps[1])
        lon = dms_to_deg(gps[4], gps[3])
    # EXIF の時刻は "YYYY:MM:DD HH:MM:SS"。秒に直して並べ替えに使う
    secs = None
    if dt:
        d, t = dt.split(" ")
        h, m, s = (int(x) for x in t.split(":"))
        secs = h * 3600 + m * 60 + s
    return {"datetime": dt, "lat": lat, "lon": lon, "secs": secs}


def meters(a: dict, b: dict) -> float:
    if None in (a["lat"], a["lon"], b["lat"], b["lon"]):
        return float("inf")
    mlat = 111_132.0
    mlon = 111_320.0 * math.cos(math.radians((a["lat"] + b["lat"]) / 2))
    return math.hypot((a["lat"] - b["lat"]) * mlat, (a["lon"] - b["lon"]) * mlon)


def legible(rd: dict) -> bool:
    """看板の文字を構造化して読めたか（＝近い写真）。

    SAM が枠を出せたか（board_detected）ではなく、**項目が読めたか**で判断する。
    遠くて SAM が枠を出せなくても全体読みで読めることがある（例: No.55）ので。
    """
    fields = rd.get("fields") or {}
    for name in ("building_area_m2", "height_m", "max_height_m", "site_area_m2",
                 "floors_above", "total_floor_area_m2"):
        if (fields.get(name) or {}).get("status") == "read":
            return True
    if [p for p in ((rd.get("lot") or {}).get("parcels") or []) if p.get("number")]:
        return True
    return bool((rd.get("address") or {}).get("chome"))


def role(rec: dict) -> str:
    rd = rec.get("reading") or {}
    nt = ((rd.get("fields") or {}).get("notice_type") or {}).get("value")
    if nt == "other":
        return "other"
    if nt in ("building", "demolition") and legible(rd):
        return "near"
    # 看板の文字を読めなかった＝遠い敷地写真（近づいて撮り直しの対象でもある）
    return "far"


def site_label(rec: dict) -> str | None:
    """地点の名前。住居表示があればそれ、なければ地番。公開しない内部メモ用。"""
    rd = rec.get("reading") or {}
    addr = rd.get("address") or {}
    if addr.get("chome"):
        b = f"{addr['block']}番" if addr.get("block") else ""
        return f"{addr['chome']}{b}"
    lot = rd.get("lot") or {}
    parcels = [p.get("number") for p in (lot.get("parcels") or []) if p.get("number")]
    if parcels:
        return "地番 " + "・".join(parcels)
    return None


def load_record(photo_stem: str) -> dict:
    path = AGENT_DIR / f"{photo_stem}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def cluster(photos: list[dict]) -> list[list[dict]]:
    """時刻順に見て、直前の写真とGPS・時刻が近ければ同じクラスタに足す。"""
    ordered = sorted(photos, key=lambda p: (p["secs"] if p["secs"] is not None else 1 << 30))
    clusters: list[list[dict]] = []
    for p in ordered:
        placed = False
        for c in clusters:
            last = c[-1]
            dt = abs((p["secs"] or 0) - (last["secs"] or 0))
            if meters(p, last) <= SAME_PLACE_M and dt <= SAME_TIME_S:
                c.append(p)
                placed = True
                break
        if not placed:
            clusters.append([p])
    return clusters


def main():
    photo_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/photos/2026-11-03")
    photos = []
    for path in sorted(photo_dir.glob("*.JPG")):
        rec = load_record(path.stem)
        ex = read_exif(path)
        photos.append({
            "photo": path.stem,
            **ex,
            "role": role(rec) if rec else "far",
            "outcome": rec.get("outcome"),
            "filing_no": rec.get("filing_no"),
            "site": site_label(rec) if rec else None,
        })

    clusters = cluster(photos)
    out = []
    for i, c in enumerate(clusters, 1):
        near = [p for p in c if p["role"] == "near"]
        far = [p for p in c if p["role"] == "far"]
        other = [p for p in c if p["role"] == "other"]
        site = next((p["site"] for p in near if p["site"]), None) \
            or next((p["site"] for p in c if p["site"]), None)
        out.append({
            "cluster": i,
            "site": site,
            "near": [p["photo"] for p in near],
            "far": [p["photo"] for p in far],
            "other": [p["photo"] for p in other],
            "members": [{"photo": p["photo"], "time": p["datetime"], "role": p["role"],
                         "lat": p["lat"], "lon": p["lon"],
                         "outcome": p["outcome"], "filing_no": p["filing_no"]}
                        for p in c],
        })

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(photos)}枚 → {len(clusters)}クラスタ")
    for c in out:
        print(f"\nクラスタ{c['cluster']}  {c['site'] or '（地点不明）'}")
        print(f"  近い（看板）: {c['near'] or '—'}")
        print(f"  遠い（敷地）: {c['far'] or '—'}")
        if c["other"]:
            print(f"  対象外の看板: {c['other']}")
    print(f"\n書き出し → {OUT}（公開しない）")


if __name__ == "__main__":
    main()
