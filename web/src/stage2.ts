/**
 * 段階2: 解体される建物を公式3D Tilesの中で塗り替える（オレンジ）。
 *
 * 塗る相手は `pipeline/make_demolition_geojson.py` が絞った `gml_id`。
 * `gml_id` は b3dm のバッチテーブルにも `bldg.geojson` にも入っていて、
 * これが計算と表示をつなぐ鍵になる（dev-policy §15、docs/stage0.md §3）。
 *
 * **1棟に決まっていないことがある。** 住居表示の「号」を座標にする手段がないので、
 * 街区の中の候補を階数・床面積・耐火性能で順位付けしているだけ。
 * `confident` が false のときは、1位だけを濃く塗って**残りも薄く見せる**
 * （dev-policy §2「決めきれなければ候補として複数を薄く示す」）。
 */
import { Color } from "three";
import type ThreeView from "@navaramap/three";
import type { Layer } from "@navaramap/three";

/** オレンジ。dev-policy §10「消えるもの・注意＝オレンジ」。 */
const PRIMARY = new Color(0xff8c1a);
/** 2位以下。同じ色味のまま暗く落として「候補」であることを出す。 */
const CANDIDATE = new Color(0x8a4a10);

export const DEMOLITION_URL = `${import.meta.env.BASE_URL}demolition.json`;

type Candidate = {
  gml_id: string;
  score: number;
  floors: number | null;
  height_m: number | null;
  footprint_m2: number;
  fireproof: string | null;
  reasons: string[];
};

type Demolition = {
  place: string;
  confident: boolean;
  chome: string;
  block: number;
  go: number | null;
  notice: {
    structure: string | null;
    floors_above: number | null;
    total_floor_area_m2: number | null;
    work_start: string;
    work_end: string;
  };
  candidates: Candidate[];
};

export type Stage2Diag = {
  url: string;
  place: string;
  confident: boolean;
  /** 塗る対象として渡した gml_id の数。 */
  targets: number;
  /** 実際に塗れた gml_id（延べではなく種類の数）。 */
  painted: string[];
  /** 1位の gml_id。 */
  primary: string | null;
  firstPaintMs: number | null;
  notice: Demolition["notice"] | null;
};

export async function addStage2(
  view: ThreeView<any>,
  tilesLayer: Layer,
  options: { url?: string } = {},
): Promise<Stage2Diag> {
  const url = options.url ?? DEMOLITION_URL;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(
      `${url} を読めない (${response.status})。` +
        `先に python pipeline/make_demolition_geojson.py を動かす。`,
    );
  }
  const data = (await response.json()) as Demolition;

  const primary = data.candidates[0]?.gml_id ?? null;
  const colors = new Map<string, Color>();
  data.candidates.forEach((c, i) => {
    // 1棟に決まっていれば1位だけ。決まっていなければ候補も薄く出す。
    if (i === 0) colors.set(c.gml_id, PRIMARY);
    else if (!data.confident) colors.set(c.gml_id, CANDIDATE);
  });

  const diag: Stage2Diag = {
    url,
    place: data.place,
    confident: data.confident,
    targets: colors.size,
    painted: [],
    primary,
    firstPaintMs: null,
    notice: data.notice,
  };

  const start = performance.now();
  const seen = new Set<string>();

  // タイルは後からも読み込まれるので、両方のイベントを購読して
  // 何度呼ばれてもいいように書く（docs/stage0.md §3）。
  const paint = (evaluator: {
    evaluate: (
      f: (info: { properties?: Record<string, unknown> }) => Record<string, unknown>,
      o?: { filters?: string[] },
    ) => void;
  }) => {
    evaluator.evaluate(
      ({ properties }) => {
        const gmlId = properties?.["gml_id"];
        if (typeof gmlId !== "string") return {};
        const color = colors.get(gmlId);
        if (!color) return {};
        if (!seen.has(gmlId)) {
          seen.add(gmlId);
          diag.painted = [...seen];
          diag.firstPaintMs ??= Math.round(performance.now() - start);
        }
        return {
          color,
          emissive: color,
          emissiveIntensity: gmlId === primary ? 0.35 : 0.15,
        };
      },
      // gml_id だけ読む。全属性を展開すると1棟あたり数十項目になる。
      { filters: ["gml_id"] },
    );
  };

  tilesLayer.on("featureCreated", ({ evaluator }) => paint(evaluator as any));
  tilesLayer.on("featureUpdated", ({ evaluator }) => paint(evaluator as any));

  return diag;
}
