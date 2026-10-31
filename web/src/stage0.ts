/**
 * 段階0: 計画建物（青）1棟と解体建物（オレンジ）1棟を重ねて出す。
 *
 * ここで確かめたい3点:
 *   1. 基準面   自前で置く押し出しの高さを、公式3D Tilesの建物とそろえる方法
 *   2. Zファイティング  計画建物が既存建物と重なったときの描画の乱れ
 *   3. featureUpdated  3D Tiles の地物に色を塗れるのはいつか
 *
 * 計画建物は**手で作った仮の1棟**で、届出のデータではない。
 * 解体建物も「オレンジに塗れるか」を確かめるために選んだ1棟で、
 * 実際に解体予定という意味ではない。
 */
import { Color } from "three";
import type ThreeView from "@navaramap/three";
import type { Layer } from "@navaramap/three";

/**
 * ### 基準面（これが段階0の一番の成果）
 *
 * NAVARA の高さはすべて**楕円体高**（WGS84楕円体からの高さ）で、
 * PLATEAU公式配信の3D Tiles も楕円体高で描かれている。
 * 一方、CityGML の原データの高さは**標高**（T.P.、東京湾平均海面から）。
 * 目黒区ではこの差（ジオイド高）が **約36.7m** ある。
 *
 * 実測: 中目黒周辺の895棟で、
 *   `bldg.geojson` の底面Z － b3dm のバッチテーブル `_zmin`
 *   = 36.708m ± 0.045m（min 36.597 / max 37.427）
 *
 * つまり:
 *   - **`bldg.geojson` のZをそのまま使ってよい**（nusamai が楕円体高に変換済み）
 *   - **b3dm のバッチテーブルの `_zmin` / `_zmax` は使ってはいけない**。
 *     これは原データの標高がそのまま入っているだけの属性で、
 *     描画されている位置より 36.7m 低い。これで地面を決めると建物が埋まる。
 *
 * 中目黒駅周辺の地面は楕円体高で約46m（標高では約9m）。
 */
const GEOID_MEGURO_M = 36.71;

/** 中目黒駅周辺の地面の楕円体高。近傍の既存建物の底面Zの実測値。 */
const GROUND_ELLIPSOIDAL_M = 46.0;

/**
 * 解体側として色を変える既存建物の `gml_id`。
 * 中目黒の25階・118.9mの業務施設。高いので画面で見つけやすいから選んだ。
 * `gml_id` は b3dm のバッチテーブルにも `bldg.geojson` の `properties.id`
 * にも同じ形で入っているので、これが両者をつなぐ鍵になる。
 */
export const DEMOLITION_GML_ID = "bldg_a7c9e45a-37be-4ec8-a772-6b580b6f9f21";

/** 手で作った計画建物。中目黒駅の南東、既存建物から46m離れた空き地に置いた。 */
const PLANNED_FOOTPRINT: [number, number][] = [
  [139.700438, 35.642823],
  [139.70049, 35.642961],
  [139.700236, 35.643025],
  [139.700184, 35.642887],
  [139.700438, 35.642823],
];

/**
 * Zファイティングの確認用。既存建物 `bldg_8959aa48-…`（11.8m、底面47.89m）の
 * 底面の輪郭そのもの。ここに計画建物を立てると、下から11.8m分の壁が
 * 公式3D Tilesの壁と完全に同じ位置に来る。
 * 低層で周りが開けているので、重なった部分が隠れずに見える。
 */
const OVERLAP_FOOTPRINT: [number, number][] = [
  [139.69951, 35.642676],
  [139.699556, 35.642607],
  [139.699511, 35.642587],
  [139.699465, 35.642656],
  [139.69951, 35.642676],
];

/** `OVERLAP_FOOTPRINT` の既存建物の底面の楕円体高。 */
const OVERLAP_BASE_Z = 47.89;

/** 計画建物の諸元（看板から読み取る想定の値を手で入れたもの）。 */
const PLANNED = {
  /** 建築基準法上の高さ[m]。10階建て相当。 */
  heightM: 31.0,
  /** 青。dev-policy 10章「これから建つもの＝青」。 */
  color: 0x2d7dff,
};

/** オレンジ。dev-policy 10章「消えるもの・注意＝オレンジ」。 */
const DEMOLITION_COLOR = new Color(0xff8c1a);

export type Stage0Options = {
  /**
   * 計画建物の底面の楕円体高[m]。
   * 既定は実測した地面の高さ。`0` を渡すと基準面を間違えた場合が見られる
   * （建物が46m沈んで地中に消える）。
   */
  baseZ?: number;
  /** 計画建物を解体建物と同じ輪郭に重ねる。Zファイティングの確認用。 */
  overlap?: boolean;
  /**
   * 計画建物（青）を出さない。解体側のオレンジはそのまま残るので、
   * これと撮り比べれば差分が計画建物のピクセルだけになる。
   */
  hidePlanned?: boolean;
};

/** スクリーンショット側から読む、段階0の診断情報。 */
export type Stage0Diag = {
  geoidMeguroM: number;
  baseZ: number;
  extrudedZ: number;
  overlap: boolean;
  /** featureUpdated が発火した回数。 */
  featureUpdatedCount: number;
  /** featureCreated が発火した回数。 */
  featureCreatedCount: number;
  /** 色を塗った地物の数（同じ地物が何度も来るので延べ回数）。 */
  demolitionPaintCount: number;
  /** 最初に塗れたのがレイヤ追加から何ミリ秒後か。 */
  firstPaintMs: number | null;
  /** 読み取れた地物プロパティのキー（最初の1件分）。 */
  samplePropertyKeys: string[];
  /** 見た gml_id の延べ数。発火の範囲を見るため。 */
  gmlIdsSeen: number;
};

export function addStage0(
  view: ThreeView<any>,
  tilesLayer: Layer,
  options: Stage0Options = {},
): Stage0Diag {
  const overlap = options.overlap ?? false;
  const baseZ =
    options.baseZ ?? (overlap ? OVERLAP_BASE_Z : GROUND_ELLIPSOIDAL_M);
  const footprint = overlap ? OVERLAP_FOOTPRINT : PLANNED_FOOTPRINT;

  const diag: Stage0Diag = {
    geoidMeguroM: GEOID_MEGURO_M,
    baseZ,
    extrudedZ: baseZ + PLANNED.heightM,
    overlap,
    featureUpdatedCount: 0,
    featureCreatedCount: 0,
    demolitionPaintCount: 0,
    firstPaintMs: null,
    samplePropertyKeys: [],
    gmlIdsSeen: 0,
  };

  // --- 計画建物（青） ---
  // 輪郭は2次元で渡し、高さは材質の height / extrudedHeight で与える。
  // perPositionHeight を false にしておくと、頂点のZではなくこの2つを使う。
  if (!options.hidePlanned) {
    const plannedSource = view.addSource({
      type: "geojson",
      data: {
        type: "FeatureCollection",
        features: [
          {
            type: "Feature",
            properties: { kind: "planned", heightM: PLANNED.heightM },
            geometry: { type: "Polygon", coordinates: [footprint] },
          },
        ],
      },
    });

    view.addLayer({
      type: "vector",
      source: plannedSource,
      polygon: {
        color: PLANNED.color,
        // 楕円体高。底面と天面の両方を明示する。
        height: baseZ,
        extrudedHeight: baseZ + PLANNED.heightM,
        perPositionHeight: false,
        // 地面に貼り付けない。貼り付けると押し出しの底が地形に潰される。
        clampToGround: false,
        castShadow: true,
        receiveShadow: true,
        outlineShow: true,
        outlineColor: 0x9ecbff,
      },
    });
  }

  // --- 解体建物（オレンジ） ---
  // 公式3D Tiles の中の1棟を gml_id で選んで色を変える。
  const start = performance.now();
  const paint = (evaluator: {
    evaluate: (
      f: (info: {
        properties?: Record<string, unknown>;
      }) => Record<string, unknown>,
      o?: { filters?: string[] },
    ) => void;
  }) => {
    evaluator.evaluate(
      ({ properties }) => {
        if (properties && diag.samplePropertyKeys.length === 0) {
          diag.samplePropertyKeys = Object.keys(properties).slice(0, 12);
        }
        const gmlId = properties?.["gml_id"];
        if (typeof gmlId !== "string") return {};
        diag.gmlIdsSeen += 1;
        if (gmlId !== DEMOLITION_GML_ID) return {};
        diag.demolitionPaintCount += 1;
        diag.firstPaintMs ??= Math.round(performance.now() - start);
        return {
          color: DEMOLITION_COLOR,
          emissive: DEMOLITION_COLOR,
          emissiveIntensity: 0.35,
        };
      },
      // gml_id だけ読む。全属性を展開すると1棟あたり数十項目になる。
      { filters: ["gml_id"] },
    );
  };

  tilesLayer.on("featureCreated", ({ evaluator }) => {
    diag.featureCreatedCount += 1;
    paint(evaluator as any);
  });
  tilesLayer.on("featureUpdated", ({ evaluator }) => {
    diag.featureUpdatedCount += 1;
    paint(evaluator as any);
  });

  return diag;
}
