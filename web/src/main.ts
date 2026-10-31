/**
 * PLATEAU 公式配信の 3D Tiles を NAVARA (@navaramap/three) で読む最小テスト。
 *
 * 配信は 3D Tiles 1.0 で、タイルは b3dm。中の glTF が
 * CESIUM_RTC / KHR_draco_mesh_compression を必須にしており、
 * LOD2 のテクスチャ付きタイルはさらに EXT_texture_webp を必須にする。
 *
 * 使い方:
 *   npm run dev
 *   http://localhost:5173/?set=lod2   (既定)
 *   http://localhost:5173/?set=lod1
 *   http://localhost:5173/?set=lod2NoTexture
 *   http://localhost:5173/?set=tokyo
 *
 * tileset の URL は src/tilesets.ts に集約してある。
 */
import ThreeView from "@navaramap/three";
import {
  DefaultPlugin,
  type DefaultDescriptions,
} from "@navaramap/three-default-plugin";

import {
  ATTRIBUTION,
  ATTRIBUTION_URL,
  DEFAULT_SET,
  isTilesetName,
  resolveTilesetUrl,
} from "./tilesets";

/** 中目黒駅 */
const NAKAMEGURO = { lng: 139.6989, lat: 35.6441 };

const hud = document.getElementById("hud")!;

/** スクリーンショット側から読むための診断情報。 */
const diag = {
  ready: false,
  tilesetUrl: "",
  errors: [] as string[],
  logs: [] as string[],
};
(window as any).__diag = diag;

function log(msg: string) {
  diag.logs.push(msg);
  hud.textContent = diag.logs.join("\n");
  if (diag.errors.length) {
    const e = document.createElement("div");
    e.className = "err";
    e.textContent = "\n" + diag.errors.join("\n");
    hud.appendChild(e);
  }
  console.log("[test]", msg);
}

function fail(where: string, err: unknown) {
  const msg = `${where}: ${err instanceof Error ? err.stack || err.message : String(err)}`;
  diag.errors.push(msg);
  console.error("[test]", msg);
  log(`失敗 (${where})`);
}

window.addEventListener("error", (e) =>
  diag.errors.push(`window.error: ${e.message}`),
);
window.addEventListener("unhandledrejection", (e) =>
  diag.errors.push(`unhandledrejection: ${String((e as PromiseRejectionEvent).reason)}`),
);

async function main() {
  const params = new URLSearchParams(location.search);
  const requested = params.get("set") ?? "";
  const name = isTilesetName(requested) ? requested : DEFAULT_SET;
  const url = resolveTilesetUrl(name);
  diag.tilesetUrl = url;

  log(`tileset: ${name}`);

  const view = new ThreeView<DefaultDescriptions>({
    shadow: true,
    container: document.getElementById("app") as HTMLElement,
  } as any);

  const plugin = new DefaultPlugin();
  view.addPlugin(plugin);

  log("init 中（WASM + workers）…");
  await view.init();
  log("init 完了");

  plugin.addDefaultPhotorealScene();

  view.setCamera({
    lng: Number(params.get("lng") ?? NAKAMEGURO.lng),
    lat: Number(params.get("lat") ?? NAKAMEGURO.lat),
    height: Number(params.get("height") ?? 1200),
    heading: Number(params.get("heading") ?? 20),
    pitch: Number(params.get("pitch") ?? -35),
    roll: 0,
  });

  // 地面がないと建物が宙に浮いて見えるので、背景に地図を敷く。
  try {
    const raster = view.addSource({
      type: "raster-tile",
      url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
      maxZoom: 18,
    });
    view.addLayer({ type: "raster", source: raster });
    log("ベースマップ追加");
  } catch (e) {
    fail("basemap", e);
  }

  // 本題: 公式配信の 3D Tiles
  const source = view.addSource({ type: "3d-tiles", url });
  view.addLayer({ type: "3d-tiles", source });
  log("3d-tiles レイヤ追加");

  // NAVARA 内蔵の出典UIにも登録しておく。
  // ただし画面表示は index.html の #credit に固定で出している
  // （内蔵UIの有無や折りたたみ状態に左右させないため）。
  view.attribution?.add([
    { attribution: ATTRIBUTION, attributionUrl: ATTRIBUTION_URL },
    {
      attribution: "© OpenStreetMap contributors",
      attributionUrl: "https://www.openstreetmap.org/copyright",
    },
  ]);

  diag.ready = true;
  log("準備完了（タイル読み込み中）");
}

main().catch((e) => fail("main", e));
