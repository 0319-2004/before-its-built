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
 * 段階0（計画建物＝青／解体建物＝オレンジ）を重ねる:
 *   http://localhost:5173/?stage=0
 *   http://localhost:5173/?stage=0&baseZ=0     基準面を間違えた場合
 *   http://localhost:5173/?stage=0&overlap=1   既存建物に重ねてZファイティングを見る
 *
 * 段階1（届出から出した計画建物＝青）を重ねる:
 *   http://localhost:5173/?stage=1             中目黒駅から800m以内
 *   http://localhost:5173/?stage=1&radius=0    絞らずに全部
 *
 * 段階2（計画建物＝青＋解体建物＝オレンジ）:
 *   http://localhost:5173/?stage=2&time=10
 *
 * 太陽の位置は既定で今の時刻。**夜に開くと建物が真っ黒になる**ので、
 * 見た目を確かめるときは現地の視太陽時を渡す:
 *   http://localhost:5173/?stage=1&time=10
 *
 * tileset の URL は src/tilesets.ts に集約してある。
 */
import ThreeView from "@navaramap/three";
import {
  DefaultPlugin,
  type DefaultDescriptions,
} from "@navaramap/three-default-plugin";

import { addStage0 } from "./stage0";
import { addStage1 } from "./stage1";
import { addStage2 } from "./stage2";
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
  stage0: null as unknown,
  stage1: null as unknown,
  stage2: null as unknown,
  /** 中目黒での太陽高度[度]。負なら夜で、建物が暗く写る。 */
  sunElevation: 0,
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

  // 太陽の位置。既定は今の時刻なので、**夜に開くと建物が真っ黒になる**
  // （地図のラスタは照明を受けないので、地図だけが見えて建物が消えたように見える）。
  // `?time=10` で現地の視太陽時を指定できる。日影（冬至）もここを使う。
  const solarHours = params.has("time") ? Number(params.get("time")) : null;
  if (solarHours !== null) {
    view.atmosphere.setSolarTime({ lng: NAKAMEGURO.lng }, solarHours);
  }
  const sunElevation = view.atmosphere.getSunElevation(NAKAMEGURO);
  log(`太陽高度 ${sunElevation.toFixed(1)}°`
      + (sunElevation < 0 ? "（夜。建物は暗く見える。?time=10 で昼にできる）" : ""));
  diag.sunElevation = sunElevation;

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
  const tilesLayer = view.addLayer({ type: "3d-tiles", source });
  log("3d-tiles レイヤ追加");

  // 段階0: 計画建物（青）と解体建物（オレンジ）を重ねる。
  if (params.get("stage") === "0") {
    try {
      const baseZ = params.has("baseZ") ? Number(params.get("baseZ")) : undefined;
      const s0 = addStage0(view, tilesLayer as any, {
        baseZ,
        overlap: params.get("overlap") === "1",
        hidePlanned: params.get("planned") === "0",
      });
      diag.stage0 = s0;
      log(`段階0: 計画建物 base=${s0.baseZ}m top=${s0.extrudedZ}m`);
    } catch (e) {
      fail("stage0", e);
    }
  }

  // 段階1: 届出から出した計画建物（青）。既定は中目黒駅の周り800m。
  // 段階2（青＋解体のオレンジ）でも計画建物は出す。
  if (params.get("stage") === "1" || params.get("stage") === "2") {
    try {
      const radiusM = Number(params.get("radius") ?? 800);
      const s1 = await addStage1(view, {
        center: radiusM > 0 ? NAKAMEGURO : undefined,
        radiusM: radiusM > 0 ? radiusM : undefined,
      });
      diag.stage1 = s1;
      log(
        `段階1: 計画建物 ${s1.added}/${s1.total}棟` +
          `（確認前 ${s1.unconfirmed}／確認済み ${s1.confirmed}）`,
      );
      if (s1.baseZRange) {
        log(
          `  底面Z ${s1.baseZRange[0].toFixed(1)}〜${s1.baseZRange[1].toFixed(1)}m` +
            ` / 高さ ${s1.heightRange![0].toFixed(1)}〜${s1.heightRange![1].toFixed(1)}m`,
        );
      }
      log("  ※ 届出は目視確認前。位置は街区の代表点、形は近似");
    } catch (e) {
      fail("stage1", e);
    }
  }

  // 段階2: 解体される建物（オレンジ）を公式3D Tilesの中で塗り替える。
  if (params.get("stage") === "2") {
    try {
      const s2 = await addStage2(view, tilesLayer as any, {});
      diag.stage2 = s2;
      const n = s2.notice;
      log(`段階2: ${s2.place} ${n?.structure}造 地上${n?.floors_above}階 `
          + `延べ${n?.total_floor_area_m2}㎡ を解体`);
      log(s2.confident
        ? `  1棟に特定（${s2.targets}棟を塗る）`
        : `  **1棟に決まっていない**。候補 ${s2.targets}棟を薄く出す`);
    } catch (e) {
      fail("stage2", e);
    }
  }

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
