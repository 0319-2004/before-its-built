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
 * 日影（冬至・地上1.5m・直方体近似の目安。時刻スライダー8–16時）:
 *   http://localhost:5173/?stage=1&shadow=1
 *   http://localhost:5173/?stage=2&shadow=1&time=14
 *
 * サンプルモード（審査員向け。判断の記録を1手ずつ流して3Dで立ち上げる）:
 *   http://localhost:5173/?mode=sample
 *   http://localhost:5173/?mode=sample&id=IMG_3936
 *   http://localhost:5173/?mode=sample&shadow=1   立てた建物に日影を付ける
 *
 * 太陽の位置は既定で今の時刻。**夜に開くと建物が真っ黒になる**ので、
 * 見た目を確かめるときは現地の視太陽時を渡す:
 *   http://localhost:5173/?stage=1&time=10
 *
 * tileset の URL は src/tilesets.ts に集約してある。
 */
import ThreeView, { JAPAN_GSI_ELEVATION_DECODER } from "@navaramap/three";
import {
  DefaultPlugin,
  type DefaultDescriptions,
} from "@navaramap/three-default-plugin";

import { runSampleMode } from "./sample";
import { addShadowStudy, SHADOW_HOUR_MAX, SHADOW_HOUR_MIN } from "./shadow";
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

/**
 * 目黒区のジオイド高（楕円体高 − 標高）。約36.71m（段階0の実測）。
 * GSIの標高タイルは標高（T.P.）なので、これを足して楕円体高にそろえ、
 * 楕円体高で描かれる公式3D Tilesや計画建物と地面の高さを合わせる。
 */
const GEOID_MEGURO_M = 36.71;

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
  sample: null as unknown,
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

/**
 * アプリのトップ（着地ページ）。何も付けずに開いた審査員が最初に見る画面。
 * 一言の説明（英語を主、日本語を併記）と「サンプルで試す」の大きなボタンを出す。
 * 見た目は Design canvas「Before It's Built デモ画面」の夜の街の方向性に寄せる。
 * ここからモデルは呼ばない（ボタンでサンプルモードへ移る）。
 */
function showLanding() {
  const CSS = `
#landing {
  position: absolute; inset: 0; z-index: 30; overflow: hidden;
  display: flex; flex-direction: column; justify-content: center; align-items: center;
  text-align: center; padding: 24px;
  color: #eaf0f8; font: 15px/1.6 system-ui, -apple-system, sans-serif;
  background:
    radial-gradient(120% 80% at 50% 12%, rgba(45,125,255,0.16), transparent 60%),
    linear-gradient(180deg, #070b14 0%, #0a1020 55%, #0c1526 100%);
}
/* 夜の街のシルエット（下端のビル列） */
#landing .skyline { position: absolute; left: 0; right: 0; bottom: 0; height: 34vh;
  display: flex; align-items: flex-end; gap: 4px; padding: 0 2px; opacity: 0.5;
  -webkit-mask-image: linear-gradient(180deg, transparent, #000 55%);
          mask-image: linear-gradient(180deg, transparent, #000 55%); }
#landing .skyline i { flex: 1; background: linear-gradient(180deg, #121c30, #0a1120); border-top: 1px solid rgba(120,160,220,0.14); }
#landing .skyline i.blue { background: linear-gradient(180deg, rgba(45,125,255,0.5), rgba(45,125,255,0.08)); box-shadow: 0 0 24px rgba(45,125,255,0.35); }
#landing .card { position: relative; z-index: 1; max-width: 620px; }
#landing .kicker { font-size: 12px; letter-spacing: .14em; text-transform: uppercase; color: #6f8cc0; margin: 0 0 14px; }
#landing h1 { margin: 0 0 6px; font-size: clamp(34px, 7vw, 58px); font-weight: 700; letter-spacing: -.01em;
  background: linear-gradient(90deg, #cfe0ff, #7fb0ff); -webkit-background-clip: text; background-clip: text; color: transparent; }
#landing .lead { margin: 10px auto 4px; max-width: 540px; font-size: clamp(15px, 2.4vw, 18px); color: #d6deeb; }
#landing .lead-ja { margin: 0 auto 26px; max-width: 540px; font-size: 13.5px; color: #93a0b6; }
#landing .cta { appearance: none; cursor: pointer; border: 0; border-radius: 999px;
  padding: 16px 30px; font: inherit; font-size: 17px; font-weight: 650; color: #06101f;
  background: linear-gradient(180deg, #8fc0ff, #2d7dff); box-shadow: 0 12px 34px rgba(45,125,255,0.45);
  display: inline-flex; flex-direction: column; align-items: center; line-height: 1.15; }
#landing .cta span { font-size: 11.5px; font-weight: 500; color: rgba(6,16,31,0.72); margin-top: 2px; }
#landing .cta:hover { filter: brightness(1.06); }
#landing .more { margin: 20px 0 0; display: flex; gap: 8px 18px; flex-wrap: wrap; justify-content: center; font-size: 13px; }
#landing .more a { color: #8fb6ff; text-decoration: none; border-bottom: 1px solid rgba(143,182,255,0.3); padding-bottom: 1px; }
#landing .more a:hover { color: #bcd4ff; }
#landing .foot { position: absolute; bottom: 12px; left: 0; right: 0; z-index: 1; font-size: 11px; color: #5c6782; }
`;
  document.head.appendChild(document.createElement("style")).textContent = CSS;

  const root = document.createElement("div");
  root.id = "landing";

  // 夜の街のシルエット（青く光る1本が「これから建つ建物」）
  const sky = document.createElement("div");
  sky.className = "skyline";
  const heights = [40, 62, 48, 80, 55, 70, 46, 88, 52, 66, 44, 74, 58, 50, 82, 60, 47, 72, 54, 64];
  heights.forEach((h, i) => {
    const b = document.createElement("i");
    b.style.height = `${h}%`;
    if (i === 7) b.className = "blue"; // 1本だけ青く光らせる
    sky.appendChild(b);
  });
  root.appendChild(sky);

  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <p class="kicker">PLATEAU × Meta AI</p>
    <h1>Before It's Built</h1>
    <p class="lead">Snap the sign on a construction site, and an AI agent raises the
      building that's <b>about to be built</b> — and the shadow it will cast —
      in 3D on the real city.</p>
    <p class="lead-ja">工事の「建築計画のお知らせ」を撮ると、これから建つ建物と日影が、
      PLATEAUの3D都市の上に立ち上がります。</p>
  `;
  const cta = document.createElement("button");
  cta.className = "cta";
  cta.type = "button";
  cta.innerHTML = `Try the sample →<span>サンプルで試す</span>`;
  cta.addEventListener("click", () => {
    location.search = "?mode=sample";
  });
  card.appendChild(cta);

  const more = document.createElement("div");
  more.className = "more";
  more.innerHTML = `
    <a href="?stage=1&shadow=1">Planned buildings &amp; shadows →</a>
    <a href="?stage=2&shadow=1">+ buildings to be demolished →</a>`;
  card.appendChild(more);
  root.appendChild(card);

  const foot = document.createElement("div");
  foot.className = "foot";
  foot.textContent = "Nakameguro, Tokyo · winter-solstice shadows are a guide, not a legal assessment";
  root.appendChild(foot);

  document.body.appendChild(root);
}

async function main() {
  const params = new URLSearchParams(location.search);

  // 何も付けずに開いたら、アプリのトップ（着地ページ）を出す。
  // 開発用の 3D Tiles 確認ページと左上のHUDは ?debug=1 のときだけ。
  const debug = params.get("debug") === "1";
  const sampleMode = params.get("mode") === "sample";
  const appView = sampleMode || params.has("stage");
  if (!appView && !debug) {
    hud.style.display = "none";
    showLanding();
    return;
  }
  hud.style.display = debug ? "" : "none";

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
  // サンプルモードは審査員が何も付けずに開くので、既定で昼にしておく
  // （既定の「今の時刻」のままだと、夜に開いた人には建物が真っ黒に見える）。
  const solarHours = params.has("time")
    ? Number(params.get("time"))
    : sampleMode
      ? 10
      : null;
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

  // 地形（DEM）。これを入れないと地図は楕円体（高さ0）に貼られ、公式3D Tilesや
  // 計画建物（どちらも楕円体高）が地面から約46m浮く。GSIの標高タイルは標高なので、
  // デコーダの offset に目黒のジオイド(+36.71m)を足して楕円体高にそろえ、地表を
  // そこへ持ち上げる。地図（下の raster）とタイルはこの地形の上に乗る。
  // 出典: 標高タイル（国土地理院）。CORS は access-control-allow-origin: *。
  try {
    const decoder = JAPAN_GSI_ELEVATION_DECODER();
    // 標高[m] = decode(RGB) * epsilon + offset。**offset はメートル**なので、
    // ジオイド[m]をそのまま足して標高→楕円体高にそろえる（epsilon で割らない。
    // 一度 36.71/epsilon=3671 を足して地面を 3671m 持ち上げ、カメラが空を向いた）。
    decoder.offset = (decoder.offset ?? 0) + GEOID_MEGURO_M;
    const dem = view.addSource({
      type: "raster-dem",
      url: "https://cyberjapandata.gsi.go.jp/xyz/dem_png/{z}/{x}/{y}.png",
      elevationDecoder: decoder,
      // GSI の標高タイルは z5〜14（z15 以上はこの区域では 404）。
      // maxZoom=14 で取得はz14まで。overscaledMaxZoom はその z14 タイルを
      // **どのカメラズームまで使い回すか**で、14（＝maxZoom）にすると
      // 近づいたとき z15/z16 を取りに行って 404 を量産する。高く（22）して
      // z14 を拡大流用させ、深いズームでは取りに行かせない。
      // 遠景の粗いタイル（z5〜9）は海や測量範囲外で欠けて 404 になるが、
      // 近景は z11〜14 で足りるので minZoom=10 にして粗いタイルは要求しない。
      minZoom: 10,
      maxZoom: 14,
      overscaledMaxZoom: 22,
      tileSize: 256,
    });
    view.addLayer({ type: "terrain", source: dem });
    log("地形（GSI標高タイル＋ジオイド補正）追加");
  } catch (e) {
    fail("terrain", e);
  }

  // 地図を地形の上に敷く（地形に沿ってドレープされる）。
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

      // 日影（冬至・地上1.5m）。?shadow=1 で、描いた棟の実寸から影を計算する。
      if (params.get("shadow") === "1" && s1.volumes.length) {
        const initialHour = params.has("time")
          ? Math.max(SHADOW_HOUR_MIN, Math.min(SHADOW_HOUR_MAX, Number(params.get("time"))))
          : 10;
        addShadowStudy(view, s1.volumes, { lng: NAKAMEGURO.lng, initialHour });
        log(`日影: ${s1.volumes.length}棟（冬至・地上1.5m・直方体近似の目安）`);
      }
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

  // サンプルモード: 事前に計算したエージェントの判断の記録を流す（APIは呼ばない）。
  // 診断用のHUDは審査員には要らないので隠す。
  if (sampleMode) {
    try {
      const s = await runSampleMode(view, {
        id: params.get("id"),
        fast: params.get("fast") === "1",
        // 計測用: URL のカメラ（?lng/lat/height/pitch/heading）をそのまま使い、
        // open() の flyTo を止める。段階1と同じ視点で撮り比べるため。
        freecam: params.get("freecam") === "1",
        // 日影（冬至・地上1.5m・直方体近似の目安）。dev-policy §10。
        shadow: params.get("shadow") === "1",
      });
      diag.sample = s;
      log(`サンプルモード: ${s.count}件（3Dを立てた件 ${s.builtId ?? "なし"}）`);
    } catch (e) {
      // ここで落ちると審査員には真っ黒な画面しか残らないので、HUDを出し直す
      hud.style.display = "";
      fail("sample", e);
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
    {
      attribution: "標高タイル（国土地理院）",
      attributionUrl: "https://maps.gsi.go.jp/development/ichiran.html",
    },
  ]);

  diag.ready = true;
  log("準備完了（タイル読み込み中）");
}

main().catch((e) => fail("main", e));
