/**
 * 使用する 3D Tiles の URL をここに集約する。他の場所にURLを書かないこと。
 *
 * 目黒区は「latest」ではなく**年度固定（2025年度）**を既定にする。
 * latest は年度更新で中身が入れ替わり、日影の計算結果や評価の再現性が
 * 崩れるため。東京都全域は背景として出すだけなので latest でよい。
 *
 * 環境変数で差し替えられる（公式配信が落ちたときの退避先に向ける）:
 *   VITE_MEGURO_TILESET_URL  目黒区の tileset.json
 *   VITE_TOKYO_TILESET_URL   東京都全域の tileset.json
 * 退避の手順は docs/data-prep.md の「公式配信が落ちたときの代替」を参照。
 */

const DATACATALOG = "https://api.plateauview.mlit.go.jp/datacatalog/3dtiles";

/** 目黒区（13110）。すべて2025年度に固定。 */
export const MEGURO_TILESETS = {
  /** LOD1。テクスチャ無しで軽い。 */
  lod1: `${DATACATALOG}/13110-bldg-lod1-2025/tileset.json`,
  /** LOD2（テクスチャ有り）。既定。 */
  lod2: `${DATACATALOG}/13110-bldg-lod2-2025/tileset.json`,
  /** LOD2のテクスチャ無し版。R2 に退避させるならこれ（33.5MB）。 */
  lod2NoTexture: `${DATACATALOG}/13110-bldg-lod2-notexture-2025/tileset.json`,
} as const;

/** 東京都全域の背景。62区市を束ねた複合tileset（LOD2が無い区はLOD1）。 */
export const TOKYO_TILESET = `${DATACATALOG}/13-bldg-maxlod2-latest/tileset.json`;

export type MeguroSet = keyof typeof MEGURO_TILESETS;
export type TilesetName = MeguroSet | "tokyo";

export const DEFAULT_SET: TilesetName = "lod2";

const env = import.meta.env as Record<string, string | undefined>;

/**
 * 表示する tileset の URL を返す。
 * 目黒区のものは VITE_MEGURO_TILESET_URL が設定されていればそちらを優先する。
 */
export function resolveTilesetUrl(name: TilesetName): string {
  if (name === "tokyo") {
    return env.VITE_TOKYO_TILESET_URL || TOKYO_TILESET;
  }
  return env.VITE_MEGURO_TILESET_URL || MEGURO_TILESETS[name];
}

export function isTilesetName(v: string): v is TilesetName {
  return v === "tokyo" || v in MEGURO_TILESETS;
}

/** 画面に出す出典表記。配信元を差し替えても出典は変わらない。 */
export const ATTRIBUTION =
  "3D都市モデル（Project PLATEAU）目黒区（2025年度）/ 国土交通省 を加工して作成";

export const ATTRIBUTION_URL =
  "https://www.geospatial.jp/ckan/dataset/plateau-13110-meguro-ku-2025";
