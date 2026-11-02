"""計画建物の「向き」を決める（dev-policy §5）。

看板の届出からは向きが分からないので、周りの街並みから推定する。
形は今までどおり**建築面積と同じ面積の正方形**のまま、向きだけを次の順で決める:

  1. 敷地に PLATEAU の古い建物（解体前の建物）があれば、
     その輪郭を囲む**最小の長方形（最小面積外接矩形）**の向きに合わせる
  2. なければ、`tran.geojson` の**最寄りの道路の縁**の向きに合わせる
  3. どちらも使えなければ、**近くの数棟の向きの多数派**に合わせる

どの方法で決めたかは各建物の属性 `orientation_source` に残す（§5）。
向きが取れなければ `north`（従来どおり北向き）。

置き換わるアニメーション（web）のために、方法1で見つけた既存建物の
輪郭・高さ・底面Zも返す（`existing`）。見つからなければ `None`。

座標はすべて **楕円体高**（`bldg.geojson` のZをそのまま使う。dev-policy §15）。
このモジュールはモデルを呼ばない。純粋な幾何計算だけ。
"""
import math
import os

# 敷地の既存建物とみなす距離[m]。街区の代表点からこの範囲に輪郭があれば使う。
# 代表点は街区の中心なので、輪郭が代表点を含む建物を優先し、なければ最寄りを使う。
SITE_RADIUS_M = 22.0

# 最寄りの道路を探す距離[m]。これより遠い道路は使わない。
ROAD_RADIUS_M = 60.0

# 多数派の向きを取る近傍の距離[m]と、最低何棟そろえば採用するか。
NEIGHBOR_RADIUS_M = 120.0
NEIGHBOR_MIN = 4

# 近傍探索用の格子の大きさ[度]（緯度方向で約110m）。
CELL_DEG = 0.001

_HALF_PI = math.pi / 2


def meters_per_degree(lat_deg: float) -> tuple[float, float]:
    """その緯度での1度あたりのメートル数（緯度方向, 経度方向）。"""
    phi = math.radians(lat_deg)
    m_lat = (111132.92 - 559.82 * math.cos(2 * phi)
             + 1.175 * math.cos(4 * phi) - 0.0023 * math.cos(6 * phi))
    m_lon = (111412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi)
             + 0.118 * math.cos(5 * phi))
    return m_lat, m_lon


# ---------- 幾何 ----------

def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """凸包（反時計回り）。Andrew's monotone chain。"""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def min_area_rect_angle(points: list[tuple[float, float]]) -> float | None:
    """点群の最小面積外接矩形の向き[rad]（東=x軸からの角、0〜π/2）。

    凸包の各辺に矩形の一辺をそろえると、最小面積矩形は必ずどれかの辺に
    沿う（回転キャリパー法の基礎）。全辺を試して面積が最小のものを選ぶ。
    """
    hull = _convex_hull(points)
    if len(hull) < 2:
        return None
    if len(hull) == 2:
        (ax, ay), (bx, by) = hull
        return math.atan2(by - ay, bx - ax) % _HALF_PI

    best = None
    n = len(hull)
    for i in range(n):
        ax, ay = hull[i]
        bx, by = hull[(i + 1) % n]
        ex, ey = bx - ax, by - ay
        length = math.hypot(ex, ey)
        if length == 0:
            continue
        ux, uy = ex / length, ey / length   # 辺方向の単位ベクトル
        px, py = -uy, ux                      # 直交方向
        us = [x * ux + y * uy for x, y in hull]
        vs = [x * px + y * py for x, y in hull]
        area = (max(us) - min(us)) * (max(vs) - min(vs))
        angle = math.atan2(uy, ux)
        if best is None or area < best[0]:
            best = (area, angle)
    return best[1] % _HALF_PI


def _ensure_ccw(ring: list) -> list:
    """外周を反時計回りにそろえる。NAVARA は時計回りの外周を穴/裏面として扱い、
    押し出した面が描かれない（計画建物は正方形で元から反時計回りだが、PLATEAU の
    建物の輪郭は時計回りのことがあり、そのままだとオレンジの箱が出ない）。"""
    area = 0.0
    for i in range(len(ring) - 1):
        area += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
    return ring if area > 0 else ring[::-1]


def _point_in_ring(lon: float, lat: float, ring: list) -> bool:
    """点が多角形の中にあるか（レイキャスティング）。ring は [lng,lat,...] の並び。"""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def _point_segment_dist_m(px, py, ax, ay, bx, by) -> float:
    """点(px,py)から線分(a,b)までの距離（すべてメートル座標）。"""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def rotated_square(lat: float, lon: float, area_m2: float, angle: float) -> list[list[float]]:
    """代表点を中心に、面積 `area_m2` の正方形を `angle`[rad] 回して置く。

    返すのは [lng, lat] の外周（反時計回り、最初の点で閉じる）。
    `angle=0` なら北向きの正方形（従来と同じ）。
    """
    half = math.sqrt(area_m2) / 2
    m_lat, m_lon = meters_per_degree(lat)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    corners_m = [(-half, -half), (half, -half), (half, half), (-half, half)]
    ring = []
    for x, y in corners_m:
        xr = x * cos_a - y * sin_a
        yr = x * sin_a + y * cos_a
        ring.append([round(lon + xr / m_lon, 8), round(lat + yr / m_lat, 8)])
    ring.append(ring[0])
    return ring


# ---------- 索引 ----------

class OrientIndex:
    """向きを決めるための索引。建物の輪郭・向き・高さと、道路の線分を格子に入れる。"""

    def __init__(self):
        self.building_cells: dict[tuple[int, int], list[dict]] = {}
        self.road_cells: dict[tuple[int, int], list[tuple]] = {}

    def _bcell(self, lat, lon):
        return (int(lat / CELL_DEG), int(lon / CELL_DEG))

    def buildings_near(self, lat, lon, radius_m):
        m_lat, _ = meters_per_degree(lat)
        reach = int(radius_m / (CELL_DEG * m_lat)) + 1
        base = self._bcell(lat, lon)
        for dy in range(-reach, reach + 1):
            for dx in range(-reach, reach + 1):
                yield from self.building_cells.get((base[0] + dy, base[1] + dx), ())

    def roads_near(self, lat, lon, radius_m):
        m_lat, _ = meters_per_degree(lat)
        reach = int(radius_m / (CELL_DEG * m_lat)) + 1
        base = self._bcell(lat, lon)
        for dy in range(-reach, reach + 1):
            for dx in range(-reach, reach + 1):
                yield from self.road_cells.get((base[0] + dy, base[1] + dx), ())


def build_indices(bldg_data: dict, tran_data: dict | None) -> OrientIndex:
    """bldg.geojson / tran.geojson（読み込み済み）から索引を作る。"""
    index = OrientIndex()

    for feature in bldg_data.get("features", []):
        geom = feature.get("geometry") or {}
        coords = geom.get("coordinates")
        if not coords:
            continue
        # MultiPolygon の最初のポリゴンの外周を輪郭として使う。
        ring = coords[0][0] if geom.get("type") == "MultiPolygon" else coords[0]
        if not ring or len(ring) < 3:
            continue
        pts = [(p[0], p[1]) for p in ring]
        clon = sum(x for x, _ in pts) / len(pts)
        clat = sum(y for _, y in pts) / len(pts)
        m_lat, m_lon = meters_per_degree(clat)
        # 中心からのメートル座標で最小外接矩形の向きを出す
        local = [((x - clon) * m_lon, (y - clat) * m_lat) for x, y in pts]
        angle = min_area_rect_angle(local)
        if angle is None:
            continue
        props = feature.get("properties") or {}
        height = props.get("measuredHeight")
        base_z = min((p[2] for p in ring if len(p) >= 3), default=None)
        index.building_cells.setdefault(index._bcell(clat, clon), []).append({
            "gml_id": props.get("id"),
            "lat": clat,
            "lon": clon,
            "angle": angle,
            "ring": ring,            # [lng, lat, z]（楕円体高）
            "height_m": float(height) if height is not None else None,
            "base_z_m": round(base_z, 2) if base_z is not None else None,
        })

    if tran_data:
        for feature in tran_data.get("features", []):
            geom = feature.get("geometry") or {}
            coords = geom.get("coordinates")
            if not coords:
                continue
            polys = coords if geom.get("type") == "MultiPolygon" else [coords]
            for poly in polys:
                for ring in poly:
                    for i in range(len(ring) - 1):
                        (alon, alat) = ring[i][0], ring[i][1]
                        (blon, blat) = ring[i + 1][0], ring[i + 1][1]
                        mlat = (alat + blat) / 2
                        mlon = (alon + blon) / 2
                        seg = (alat, alon, blat, blon)
                        index.road_cells.setdefault(
                            index._bcell(mlat, mlon), []).append(seg)
    return index


# ---------- 向きを決める ----------

def _existing_building(index: OrientIndex, lat: float, lon: float):
    """敷地（代表点）にある PLATEAU の建物。輪郭が点を含むものを優先、なければ最寄り。"""
    m_lat, m_lon = meters_per_degree(lat)
    containing = None
    nearest = None
    nearest_d = None
    for b in index.buildings_near(lat, lon, SITE_RADIUS_M):
        d = math.hypot((b["lat"] - lat) * m_lat, (b["lon"] - lon) * m_lon)
        if _point_in_ring(lon, lat, b["ring"]):
            if containing is None or d < containing[0]:
                containing = (d, b)
        if nearest_d is None or d < nearest_d:
            nearest_d, nearest = d, b
    if containing is not None:
        return containing[1], "contains_point", round(containing[0], 1)
    if nearest is not None and nearest_d <= SITE_RADIUS_M:
        return nearest, "nearest", round(nearest_d, 1)
    return None, None, None


def _nearest_road_angle(index: OrientIndex, lat: float, lon: float) -> float | None:
    m_lat, m_lon = meters_per_degree(lat)
    best = None
    for alat, alon, blat, blon in index.roads_near(lat, lon, ROAD_RADIUS_M):
        d = _point_segment_dist_m(
            0.0, 0.0,
            (alon - lon) * m_lon, (alat - lat) * m_lat,
            (blon - lon) * m_lon, (blat - lat) * m_lat)
        if d > ROAD_RADIUS_M:
            continue
        if best is None or d < best[0]:
            ex = (blon - alon) * m_lon
            ey = (blat - alat) * m_lat
            if ex == 0 and ey == 0:
                continue
            best = (d, math.atan2(ey, ex) % _HALF_PI)
    return best[1] if best else None


def _neighbor_majority_angle(index: OrientIndex, lat: float, lon: float) -> float | None:
    """近傍の建物の向きの多数派。向きは周期90°なので 4θ の円周平均で出す。"""
    m_lat, m_lon = meters_per_degree(lat)
    sx = sy = 0.0
    count = 0
    for b in index.buildings_near(lat, lon, NEIGHBOR_RADIUS_M):
        d = math.hypot((b["lat"] - lat) * m_lat, (b["lon"] - lon) * m_lon)
        if d > NEIGHBOR_RADIUS_M:
            continue
        sx += math.cos(4 * b["angle"])
        sy += math.sin(4 * b["angle"])
        count += 1
    if count < NEIGHBOR_MIN:
        return None
    return (math.atan2(sy, sx) / 4) % _HALF_PI


def orient_site(index: OrientIndex, lat: float, lon: float, area_m2: float) -> dict:
    """代表点の向きを決め、回した正方形と根拠を返す。

    返り値:
      footprint         回した正方形の外周 [[lng,lat], …]
      orientation_deg   向き[度]（0〜90。北向きは0）
      orientation_source  plateau_building / nearest_road / neighbor_majority / north
      existing          方法1で見つけた既存建物（置き換わるアニメ用）か None
    """
    # 比較用: 従来どおり北向きに固定する抜け道（前後のスクショを撮るとき用）。
    if os.environ.get("ORIENT_FORCE_NORTH"):
        return {
            "footprint": rotated_square(lat, lon, area_m2, 0.0),
            "orientation_deg": 0.0,
            "orientation_source": "north",
            "existing": None,
        }

    building, match, dist = _existing_building(index, lat, lon)
    source = "north"
    angle = 0.0
    existing = None

    if building is not None:
        angle = building["angle"]
        source = "plateau_building"
        existing = {
            "gml_id": building["gml_id"],
            "footprint": _ensure_ccw(
                [[round(p[0], 8), round(p[1], 8)] for p in building["ring"]]),
            "base_z_m": building["base_z_m"],
            "height_m": building["height_m"],
            "match": match,
            "distance_m": dist,
        }
    else:
        road = _nearest_road_angle(index, lat, lon)
        if road is not None:
            angle, source = road, "nearest_road"
        else:
            neighbor = _neighbor_majority_angle(index, lat, lon)
            if neighbor is not None:
                angle, source = neighbor, "neighbor_majority"

    return {
        "footprint": rotated_square(lat, lon, area_m2, angle),
        "orientation_deg": round(math.degrees(angle), 1),
        "orientation_source": source,
        "existing": existing,
    }
