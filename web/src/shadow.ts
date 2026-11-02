/**
 * 日影（冬至・地上1.5m）。計画建物の直方体が落とす影を、自前で計算して
 * 地上1.5mの平面に平らなポリゴンで描く。時刻スライダー（冬至8–16時）で動かす。
 *
 * **なぜ自前計算か。** NAVARA のリアルタイム影（CSM）は3Dジオメトリの上にしか
 * 落ちず、ラスタ地図の地面・更地には映らない（dev-policy §9 の実測）。
 * 計画地はたいてい更地なので、影が出ないと意味がない。そこで §10 の
 * 「冬至・直方体近似・**地上1.5mで計算**」に従い、影の範囲をコードで出す。
 * スライダーはシーンの光（CSM）も同じ冬至時刻に合わせるので、既存建物には
 * 本物の影も付く（dev-policy §14「NAVARAで日影が出せるか」＝出せる）。
 *
 * **これは法令上の日影規制の判定ではない。** 目安であることを画面に必ず出す（§10）。
 * 色は青（これから建つ）・オレンジ（注意）とは別の暗い半透明にする（§10 と混ぜない）。
 */
import type ThreeView from "@navaramap/three";
import type { Layer } from "@navaramap/three";

/** 影の色。青/オレンジを避けた暗いスレート。半透明で地図を透かす。 */
const SHADOW_FILL = 0x0d1320;
const SHADOW_OUTLINE = 0x2a3550;
const SHADOW_OPACITY = 0.34;

/** 日影規制と同じく冬至で評価する。赤緯は固定でよい（冬至≈−23.44°）。 */
const WINTER_SOLSTICE_DECL_DEG = -23.44;
/** 評価する時間帯（真太陽時）。日影規制の冬至8〜16時にそろえる。 */
export const SHADOW_HOUR_MIN = 8;
export const SHADOW_HOUR_MAX = 16;
/** 影を測る高さ。地上1.5m（§10）。 */
const PLANE_ABOVE_GROUND_M = 1.5;
/** 低い太陽で影が伸びすぎるのを防ぐ上限[m]。朝夕の冬至は影が高さの7倍になる。 */
const MAX_SHADOW_M = 500;

export type ShadowVolume = {
  /** 閉じたリング [[lng,lat], …]（先頭=末尾でも可）。 */
  footprint: [number, number][];
  /** 底面の楕円体高[m]。 */
  base_z_m: number;
  /** 3D表示に使う建物の高さ[m]。 */
  display_height_m: number;
};

/**
 * 真太陽時 `hours` の太陽の高度と方位（冬至固定）。
 * 方位は北から時計回りの度（90=東、180=南、270=西）。
 */
export function sunAltAz(
  hours: number,
  latDeg: number,
): { altDeg: number; azDeg: number } {
  const d2r = Math.PI / 180;
  const lat = latDeg * d2r;
  const dec = WINTER_SOLSTICE_DECL_DEG * d2r;
  // 時角。真太陽時なので経度・均時差の補正は要らない（正午=0）。
  const H = (hours - 12) * 15 * d2r;
  const sinAlt =
    Math.sin(lat) * Math.sin(dec) + Math.cos(lat) * Math.cos(dec) * Math.cos(H);
  const altDeg = Math.asin(Math.max(-1, Math.min(1, sinAlt))) / d2r;
  // 方位（北0・東90）。atan2 の分母は南を基準にした標準式。
  const az =
    Math.atan2(
      Math.sin(H),
      Math.cos(H) * Math.sin(lat) - Math.tan(dec) * Math.cos(lat),
    ) /
      d2r +
    180;
  return { altDeg, azDeg: ((az % 360) + 360) % 360 };
}

/** メートル→経緯度の係数（stage1 と同じ）。 */
function metersPerDegree(latDeg: number): { lat: number; lng: number } {
  const r = (latDeg * Math.PI) / 180;
  return {
    lat: 111132.92 - 559.82 * Math.cos(2 * r),
    lng: 111412.84 * Math.cos(r),
  };
}

/** 2D凸包（単調鎖法）。lng/lat のままでよい（アフィンで凸性は保たれる）。 */
function convexHull(pts: [number, number][]): [number, number][] {
  const p = pts.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (p.length < 3) return p;
  const cross = (
    o: [number, number],
    a: [number, number],
    b: [number, number],
  ) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower: [number, number][] = [];
  for (const q of p) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], q) <= 0)
      lower.pop();
    lower.push(q);
  }
  const upper: [number, number][] = [];
  for (let i = p.length - 1; i >= 0; i--) {
    const q = p[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], q) <= 0)
      upper.pop();
    upper.push(q);
  }
  lower.pop();
  upper.pop();
  return lower.concat(upper);
}

/**
 * 直方体（footprint × 高さ）が、真太陽時 `hours` に地上1.5m平面へ落とす影のリング。
 * 影 = footprint ∪（天面を太陽と反対へ投影したもの）の凸包。
 * z は楕円体高（平面 = base_z + 1.5m）を各頂点に持たせる（perPositionHeight 用）。
 * 夜（高度≤0）は null。
 */
export function shadowRing(
  volume: ShadowVolume,
  hours: number,
  latDeg: number,
): [number, number, number][] | null {
  const { altDeg, azDeg } = sunAltAz(hours, latDeg);
  if (altDeg <= 0) return null;

  const planeZ = volume.base_z_m + PLANE_ABOVE_GROUND_M;
  // 天面（高さ h）から1.5m平面までの水平投影距離。h≤1.5 なら footprint のみ。
  const rise = volume.display_height_m - PLANE_ABOVE_GROUND_M;
  let offsetM = rise > 0 ? rise / Math.tan((altDeg * Math.PI) / 180) : 0;
  if (offsetM > MAX_SHADOW_M) offsetM = MAX_SHADOW_M;

  const a = (azDeg * Math.PI) / 180;
  // 反太陽方向（太陽は (sinA, cosA) の向き）。
  const offE = -offsetM * Math.sin(a);
  const offN = -offsetM * Math.cos(a);
  const m = metersPerDegree(latDeg);
  const dLng = offE / m.lng;
  const dLat = offN / m.lat;

  // footprint の角（閉じたリングの重複末尾は落とす）
  const ring = volume.footprint;
  const n = ring.length > 1 &&
    ring[0][0] === ring[ring.length - 1][0] &&
    ring[0][1] === ring[ring.length - 1][1]
    ? ring.length - 1
    : ring.length;
  const corners: [number, number][] = [];
  for (let i = 0; i < n; i++) {
    corners.push([ring[i][0], ring[i][1]]); // 底面側
    corners.push([ring[i][0] + dLng, ring[i][1] + dLat]); // 投影した天面側
  }
  const hull = convexHull(corners);
  const out: [number, number, number][] = hull.map((p) => [p[0], p[1], planeZ]);
  // リングを閉じる
  if (out.length) out.push([out[0][0], out[0][1], out[0][2]]);
  return out.length >= 4 ? out : null;
}

/** 代表点（緯度・経度の平均）。太陽位置の緯度に使う。 */
function meanLatLng(volumes: ShadowVolume[]): { lat: number; lng: number } {
  let lat = 0;
  let lng = 0;
  let k = 0;
  for (const v of volumes) {
    for (let i = 0; i < Math.min(4, v.footprint.length); i++) {
      lng += v.footprint[i][0];
      lat += v.footprint[i][1];
      k++;
    }
  }
  return k ? { lat: lat / k, lng: lng / k } : { lat: 35.6441, lng: 139.6989 };
}

const CSS = `
#shadow-study {
  position: absolute; left: 0; right: 0; bottom: 0; z-index: 21;
  display: flex; justify-content: center; pointer-events: none;
  font: 13px/1.55 system-ui, -apple-system, sans-serif; color: #e8ebf0;
}
#shadow-study .panel {
  pointer-events: auto;
  width: min(440px, calc(100% - 20px));
  margin: 0 10px 10px;
  padding: 11px 14px 12px;
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 13px;
  background: rgba(8,10,14,0.86);
  backdrop-filter: blur(14px);
  box-shadow: 0 18px 50px rgba(0,0,0,0.55);
}
#shadow-study .row { display: flex; align-items: baseline; gap: 8px; }
#shadow-study .row h2 { margin: 0; font-size: 13px; font-weight: 600; }
#shadow-study .row h2 b { color: #9fb0c6; font-weight: 600; }
#shadow-study .val { margin-left: auto; font-variant-numeric: tabular-nums; color: #cfd6e0; }
#shadow-study input[type=range] { width: 100%; margin: 9px 0 6px; accent-color: #6ea8ff; }
#shadow-study .ticks { display: flex; justify-content: space-between; font-size: 10px; color: #78808d; }
#shadow-study .note { margin: 8px 0 0; font-size: 10.5px; color: #8e97a4; }
#shadow-study .note b { color: #ffb366; font-weight: 600; }
`;

export type ShadowStudy = {
  setHour: (hours: number) => void;
  destroy: () => void;
};

export type ShadowOptions = {
  /** シーンの光（太陽時）に使う経度。既定は建物の代表点。 */
  lng?: number;
  /** スライダーの初期時刻（真太陽時）。既定10時。 */
  initialHour?: number;
};

/**
 * 影のレイヤとスライダーを作る。スライダーを動かすと
 *   1) 自前の影ポリゴンを描き直し、
 *   2) `atmosphere.setSolarTime` でシーンの光＝CSM影も同じ冬至時刻に合わせる。
 * `atmosphere.date` は冬至に固定する（太陽の赤緯＝季節）。
 */
export function addShadowStudy(
  view: ThreeView<any>,
  volumes: ShadowVolume[],
  options: ShadowOptions = {},
): ShadowStudy {
  const site = meanLatLng(volumes);
  const lng = options.lng ?? site.lng;
  let hours = options.initialHour ?? 10;

  // 季節を冬至に固定（時刻は setSolarTime で与える）。
  view.atmosphere.date = new Date(Date.UTC(2025, 11, 21, 3, 0, 0)); // 12/21

  let layer: Layer | null = null;

  const drawShadows = () => {
    const features = volumes
      .map((v) => shadowRing(v, hours, site.lat))
      .filter((r): r is [number, number, number][] => r !== null)
      .map((ring) => ({
        type: "Feature" as const,
        properties: {},
        geometry: { type: "Polygon" as const, coordinates: [ring] },
      }));

    const source = view.addSource({
      type: "geojson",
      data: { type: "FeatureCollection", features },
    });
    const next = view.addLayer({
      type: "vector",
      source,
      polygon: {
        color: SHADOW_FILL,
        // 各頂点の楕円体高（base_z+1.5m）を使う。押し出しはしない（平らな影）。
        perPositionHeight: true,
        clampToGround: false,
        castShadow: false,
        receiveShadow: false,
        outlineShow: true,
        outlineColor: SHADOW_OUTLINE,
        opacity: SHADOW_OPACITY,
      },
    } as any);
    // 前のレイヤは新しいのを足してから消す（ちらつき防止）。
    layer?.delete();
    layer = next;
  };

  // --- UI ---
  const style = document.createElement("style");
  style.textContent = CSS;
  document.head.appendChild(style);

  const root = document.createElement("div");
  root.id = "shadow-study";
  const panel = document.createElement("div");
  panel.className = "panel";
  root.appendChild(panel);

  const row = document.createElement("div");
  row.className = "row";
  const h2 = document.createElement("h2");
  h2.innerHTML = "日影 <b>（冬至）</b>";
  const val = document.createElement("span");
  val.className = "val";
  row.appendChild(h2);
  row.appendChild(val);
  panel.appendChild(row);

  const slider = document.createElement("input");
  slider.type = "range";
  slider.min = String(SHADOW_HOUR_MIN);
  slider.max = String(SHADOW_HOUR_MAX);
  slider.step = "0.25";
  slider.value = String(hours);
  panel.appendChild(slider);

  const ticks = document.createElement("div");
  ticks.className = "ticks";
  ticks.innerHTML = "<span>8時</span><span>10時</span><span>12時</span><span>14時</span><span>16時</span>";
  panel.appendChild(ticks);

  const note = document.createElement("p");
  note.className = "note";
  note.innerHTML =
    "<b>法令上の日影規制の判定ではない目安です。</b>" +
    "冬至・地上1.5m・建物は直方体で近似しています。";
  panel.appendChild(note);

  document.body.appendChild(root);

  const applyLabel = () => {
    const { altDeg } = sunAltAz(hours, site.lat);
    const hh = Math.floor(hours);
    const mm = Math.round((hours - hh) * 60);
    val.textContent = `${hh}:${String(mm).padStart(2, "0")} ・ 太陽高度 ${altDeg.toFixed(0)}°`;
  };

  // スライダーは rAF でスロットルする（連続入力で描き直しを詰まらせない）。
  let pending = false;
  const apply = () => {
    pending = false;
    view.atmosphere.setSolarTime({ lng }, hours);
    drawShadows();
    applyLabel();
  };
  const setHour = (h: number) => {
    hours = Math.max(SHADOW_HOUR_MIN, Math.min(SHADOW_HOUR_MAX, h));
    slider.value = String(hours);
    if (!pending) {
      pending = true;
      requestAnimationFrame(apply);
    }
  };
  slider.addEventListener("input", () => setHour(Number(slider.value)));

  // 初期描画
  apply();

  return {
    setHour,
    destroy: () => {
      layer?.delete();
      layer = null;
      root.remove();
      style.remove();
    },
  };
}
