/**
 * 段階1: 届出から出した計画建物（青）を実データで立ち上げる。
 *
 * 段階0は手で作った1棟だったが、ここは `pipeline/make_planned_geojson.py` が
 * 届出から作った GeoJSON をそのまま読む。
 *
 *   位置  街区レベル位置参照情報の代表点
 *   形    建築面積と同じ面積の正方形（北向き）
 *   高さ  「3D表示に使う高さ」＝最高の高さがあればそれ、なければ高さ
 *   底面  近くの既存建物の底面Z（**楕円体高**。dev-policy §15）
 *
 * **届出はまだ目視確認が済んでいない。** 確認前の棟は半透明＋点線の輪郭にして、
 * 確認済みの棟（`confirmed: true`）と見分けられるようにする。
 * 根拠のない数字は画面に出さない（dev-policy §10）。
 */
import type ThreeView from "@navaramap/three";

/** 青。dev-policy §10「これから建つもの＝青」。 */
const PLANNED_COLOR = 0x2d7dff;
const PLANNED_OUTLINE = 0x9ecbff;

/**
 * 確認前の棟は半透明にして、確認済みの棟と見分ける。
 * 薄くしすぎると「これから建つもの」が読み取れなくなるので、
 * 透かしたうえで自発光を少し足し、青は残す。
 */
const UNCONFIRMED = { opacity: 0.55, emissiveIntensity: 0.25 };
const CONFIRMED = { opacity: 0.95, emissiveIntensity: 0.05 };

/** 既定の読み込み先。dev サーバーが `data/` から配る（vite.config.ts）。 */
export const PLANNED_URL = "/data/planned/planned.geojson";

type PlannedProperties = {
  filing_no: number | null;
  chome: string | null;
  block: number | null;
  display_height_m: number;
  display_height_source: string | null;
  base_z_m: number;
  base_z_rough: boolean;
  building_area_m2: number;
  floors_above: number | null;
  confirmed: boolean;
  status_label: string;
};

type PlannedCollection = {
  metadata?: {
    generated?: string;
    count?: number;
    confirmed?: number;
    unconfirmed?: number;
    note?: string;
    sources?: string[];
  };
  features: {
    type: "Feature";
    properties: PlannedProperties;
    geometry: { type: "Polygon"; coordinates: [number, number][][] };
  }[];
};

/** スクリーンショット側から読む、段階1の診断情報。 */
export type Stage1Diag = {
  url: string;
  /** GeoJSON に入っていた棟数。 */
  total: number;
  /** 実際にレイヤにした棟数。 */
  added: number;
  confirmed: number;
  unconfirmed: number;
  /** 地面を遠くから取った（当てにならない）棟数。 */
  roughBase: number;
  /** 底面Zの範囲[m]（楕円体高）。 */
  baseZRange: [number, number] | null;
  /** 3Dに使った高さの範囲[m]。 */
  heightRange: [number, number] | null;
  /** 中心にした棟の届出番号。 */
  centeredOn: number | null;
  generated: string | null;
  note: string | null;
};

export type Stage1Options = {
  url?: string;
  /**
   * この点から半径 `radiusM` [m] 以内の棟だけ出す。
   * 省略すると全部出す。
   */
  center?: { lng: number; lat: number };
  radiusM?: number;
};

function distanceM(
  a: { lng: number; lat: number },
  lng: number,
  lat: number,
): number {
  const mPerLat = 111132.92 - 559.82 * Math.cos((2 * a.lat * Math.PI) / 180);
  const mPerLng = 111412.84 * Math.cos((a.lat * Math.PI) / 180);
  return Math.hypot((lat - a.lat) * mPerLat, (lng - a.lng) * mPerLng);
}

/** 輪郭の重心。中心からの距離を測るのに使う（正方形なので4点の平均でよい）。 */
function centroid(ring: [number, number][]): [number, number] {
  const pts = ring.slice(0, 4);
  const lng = pts.reduce((s, p) => s + p[0], 0) / pts.length;
  const lat = pts.reduce((s, p) => s + p[1], 0) / pts.length;
  return [lng, lat];
}

export async function addStage1(
  view: ThreeView<any>,
  options: Stage1Options = {},
): Promise<Stage1Diag> {
  const url = options.url ?? PLANNED_URL;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(
      `${url} を読めない (${response.status})。` +
        `先に python pipeline/make_planned_geojson.py を動かす。`,
    );
  }
  const data = (await response.json()) as PlannedCollection;

  const diag: Stage1Diag = {
    url,
    total: data.features.length,
    added: 0,
    confirmed: 0,
    unconfirmed: 0,
    roughBase: 0,
    baseZRange: null,
    heightRange: null,
    centeredOn: null,
    generated: data.metadata?.generated ?? null,
    note: data.metadata?.note ?? null,
  };

  // 中心が指定されていれば、その周りだけに絞る
  let features = data.features;
  if (options.center && options.radiusM) {
    const center = options.center;
    const within = features
      .map((f) => {
        const [lng, lat] = centroid(f.geometry.coordinates[0]);
        return { f, d: distanceM(center, lng, lat) };
      })
      .filter((x) => x.d <= options.radiusM!)
      .sort((a, b) => a.d - b.d);
    features = within.map((x) => x.f);
    diag.centeredOn = within[0]?.f.properties.filing_no ?? null;
  }

  const baseZs: number[] = [];
  const heights: number[] = [];

  for (const feature of features) {
    const p = feature.properties;
    // 高さも底面も無いものは pipeline 側で落ちているはずだが、念のため確かめる
    if (!Number.isFinite(p.display_height_m) || !Number.isFinite(p.base_z_m)) {
      continue;
    }

    const source = view.addSource({
      type: "geojson",
      data: { type: "FeatureCollection", features: [feature] },
    });

    view.addLayer({
      type: "vector",
      source,
      polygon: {
        color: PLANNED_COLOR,
        // 楕円体高。底面と天面の両方を明示する（段階0と同じ）。
        height: p.base_z_m,
        extrudedHeight: p.base_z_m + p.display_height_m,
        perPositionHeight: false,
        clampToGround: false,
        castShadow: true,
        receiveShadow: true,
        outlineShow: true,
        outlineColor: PLANNED_OUTLINE,
        // 確認前は透かす。確認済み（verified=1）になったら濃くなる。
        ...(p.confirmed ? CONFIRMED : UNCONFIRMED),
        emissiveColor: PLANNED_COLOR,
      },
    });

    diag.added += 1;
    if (p.confirmed) diag.confirmed += 1;
    else diag.unconfirmed += 1;
    if (p.base_z_rough) diag.roughBase += 1;
    baseZs.push(p.base_z_m);
    heights.push(p.display_height_m);
  }

  if (baseZs.length) {
    diag.baseZRange = [Math.min(...baseZs), Math.max(...baseZs)];
    diag.heightRange = [Math.min(...heights), Math.max(...heights)];
  }

  return diag;
}
