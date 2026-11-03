/**
 * サンプルモード: エージェントの判断の記録を1手ずつ流し、最後に3Dで建物を立ち上げる。
 *
 * 審査員は日本の看板を撮れないので（dev-policy §9）、**事前に計算した結果**を見せる。
 * **ここからモデルは呼ばない。** 読むのは `web/public/samples.json` だけで、
 * 中身は `pipeline/make_sample_mode.py` が作る（公開してよい形かをコードで確かめている）。
 *
 *   http://localhost:5173/?mode=sample
 *   http://localhost:5173/?mode=sample&id=IMG_3936   最初から1件を開く
 *   http://localhost:5173/?mode=sample&fast=1        手の送りを待たずに全部出す
 *
 * 色は dev-policy §10 に合わせる（これから建つもの＝青、食い違い・注意＝オレンジ）。
 * 看板の写真は出さない（建築主・設計者・施工者の氏名が印刷されているため）。
 */
import type ThreeView from "@navaramap/three";
import type { Layer } from "@navaramap/three";
import { addShadowStudy, type ShadowStudy } from "./shadow";

/** 青＝これから建つもの。オレンジ＝消えるもの・注意。dev-policy §10。 */
const BLUE = 0x2d7dff;
const BLUE_LIGHT = 0x9ecbff;
const ORANGE = 0xff8c1a;
const ORANGE_LIGHT = 0xffc078;

export const SAMPLES_URL = `${import.meta.env.BASE_URL}samples.json`;

/** 1手を出してから次を出すまで[ms]。読みながら追える速さにする。 */
const STEP_DELAY_MS = 620;
/** 建物が立ち上がる時間[ms]。 */
const RISE_MS = 1400;
/** 置き換わるアニメ: 既存建物をオレンジで見せてから沈むまで[ms]。
 *  ホールドは NAVARA のベクタレイヤが表示されるまでの待ち（約2秒）を見込んで
 *  長めに取る（短いと描画される前に沈んで、オレンジが見えないまま消える）。 */
const EXISTING_HOLD_MS = 3500;
const SINK_MS = 1500;

type Step = {
  n: number;
  tool: string;
  tool_label: string;
  thought: string | null;
  detail: string;
  input: Record<string, unknown>;
  ms: number | null;
  failed: boolean;
};

/** 置き換わる前の PLATEAU（2021年調査）の建物。orient.py が付ける。 */
type Existing = {
  gml_id: string | null;
  footprint: [number, number][];
  base_z_m: number | null;
  height_m: number | null;
  match: string;
  distance_m: number;
};

type Volume = {
  footprint: [number, number][];
  building_area_m2: number;
  display_height_m: number;
  display_height_source: string;
  base_z_m: number;
  base_z_rough: boolean;
  orientation_deg?: number;
  orientation_source?: string;
  /** 置き換わる既存建物（なければ null）。 */
  existing?: Existing | null;
};

/** 3D Tiles の地物を gml_id ごとに評価してスタイル（color/show/opacity 等）を返す。
 *  stage0.ts と同じ仕組み。`show:false` で PLATEAU の建物を1棟だけ消せる。 */
type TileEvaluator = {
  evaluate: (
    f: (info: { properties?: Record<string, unknown> }) => Record<string, unknown>,
    o?: { filters?: string[] },
  ) => void;
};

type Sample = {
  id: string;
  label: string;
  outcome: "built" | "mismatch_notified" | "needs_review" | "needs_retake";
  outcome_label: string;
  summary: string;
  unresolved: string[];
  filing_no: number | null;
  match_key: string | null;
  filing_verified: boolean | null;
  status_label: string | null;
  values: Record<string, { value: unknown; status?: string; confidence?: number }>;
  steps: Step[];
  volume: Volume | null;
  photo_published: boolean;
  photo_note: string;
};

type SamplesFile = {
  generated: string;
  model: string;
  note: string;
  sources: string[];
  samples: Sample[];
};

/** 終わり方ごとの見せ方。青は立った、オレンジは注意。 */
const OUTCOME_STYLE: Record<Sample["outcome"], { tone: string; text: string }> = {
  built: { tone: "blue", text: "3Dに立てた" },
  mismatch_notified: { tone: "orange", text: "食い違いあり" },
  needs_review: { tone: "orange", text: "要確認" },
  needs_retake: { tone: "grey", text: "撮り直し" },
};

/** 項目の日本語名。読み取った値の表に使う。 */
const FIELD_LABELS: Record<string, string> = {
  use: "主要用途",
  structure: "構造",
  site_area_m2: "敷地面積",
  building_area_m2: "建築面積",
  total_floor_area_m2: "延べ面積",
  floors_above: "地上の階数",
  floors_below: "地下の階数",
  height_m: "高さ",
  max_height_m: "最高の高さ",
  eave_height_m: "軒高",
  units: "住戸数",
  construction_start: "着工予定",
  construction_end: "完了予定",
  notice_posted: "標識の設置日",
  lot: "地名地番",
  address: "住居表示",
};

/** 数値の項目に付ける単位。 */
const FIELD_UNITS: Record<string, string> = {
  site_area_m2: "㎡",
  building_area_m2: "㎡",
  total_floor_area_m2: "㎡",
  height_m: "m",
  max_height_m: "m",
  eave_height_m: "m",
  floors_above: "階",
  floors_below: "階",
  units: "戸",
};

const CSS = `
#sample {
  position: absolute; inset: 0; z-index: 20; pointer-events: none;
  display: flex; flex-direction: column; justify-content: flex-start;
  font: 13px/1.65 system-ui, -apple-system, sans-serif; color: #e8ebf0;
}
#sample .panel {
  pointer-events: auto;
  width: min(420px, 100%);
  max-height: 100%;
  margin: 10px;
  display: flex; flex-direction: column;
  border: 1px solid rgba(255,255,255,0.09);
  border-radius: 14px;
  background: rgba(8,10,14,0.86);
  backdrop-filter: blur(14px);
  box-shadow: 0 18px 50px rgba(0,0,0,0.55);
  overflow: hidden;
}
#sample .head { padding: 13px 15px 11px; border-bottom: 1px solid rgba(255,255,255,0.07); }
#sample .head h1 { margin: 0; font-size: 15px; letter-spacing: .02em; }
#sample .head h1 b { color: #6ea8ff; font-weight: 600; }
#sample .head p { margin: 4px 0 0; font-size: 11.5px; color: #8e97a4; }
#sample .chips { display: flex; flex-wrap: wrap; gap: 6px; padding: 11px 15px; }
#sample .chip {
  appearance: none; cursor: pointer;
  padding: 5px 10px 5px 8px; border-radius: 999px;
  border: 1px solid rgba(255,255,255,0.14);
  background: rgba(255,255,255,0.04); color: #d6dbe3;
  font: inherit; font-size: 11.5px;
  display: inline-flex; align-items: center; gap: 6px;
}
#sample .chip:hover { background: rgba(255,255,255,0.1); }
#sample .chip[aria-pressed="true"] { border-color: #2d7dff; background: rgba(45,125,255,0.2); color: #fff; }
#sample .dot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
#sample .dot.blue { background: #2d7dff; }
#sample .dot.orange { background: #ff8c1a; }
#sample .dot.grey { background: #68707d; }
#sample .body { overflow-y: auto; padding: 0 15px 14px; }
#sample .hint { padding: 2px 0 12px; font-size: 12px; color: #8e97a4; }

#sample .step {
  display: grid; grid-template-columns: 22px 1fr; gap: 9px;
  padding: 7px 0 8px; border-top: 1px solid rgba(255,255,255,0.055);
  animation: sample-in .32s ease both;
}
@keyframes sample-in { from { opacity: 0; transform: translateY(5px); } }
@media (prefers-reduced-motion: reduce) { #sample .step { animation: none; } }
#sample .num {
  width: 22px; height: 22px; border-radius: 50%;
  display: grid; place-items: center; font-size: 11px;
  background: rgba(45,125,255,0.16); color: #8ebaff;
  border: 1px solid rgba(45,125,255,0.35);
}
#sample .step.failed .num { background: rgba(255,140,26,0.16); color: #ffb366; border-color: rgba(255,140,26,0.4); }
#sample .tool { font-size: 12.5px; color: #fff; }
#sample .tool code { color: #7f8b9c; font-size: 11px; }
#sample .thought { color: #9fb6d8; font-size: 11.5px; font-style: italic; }
#sample .detail { color: #c3cad4; font-size: 11.5px; white-space: pre-wrap; }
#sample .detail .mm { color: #ffb366; }

#sample .card { margin-top: 12px; padding: 11px 12px; border-radius: 10px; border: 1px solid; }
#sample .card.blue { border-color: rgba(45,125,255,0.45); background: rgba(45,125,255,0.1); }
#sample .card.orange { border-color: rgba(255,140,26,0.45); background: rgba(255,140,26,0.1); }
#sample .card.grey { border-color: rgba(255,255,255,0.14); background: rgba(255,255,255,0.045); }
#sample .card h2 { margin: 0 0 5px; font-size: 12.5px; }
#sample .card p { margin: 0; font-size: 12px; color: #dde2e9; }
#sample .card ul { margin: 7px 0 0; padding-left: 17px; font-size: 11.5px; color: #ffcb99; }

#sample table { width: 100%; margin-top: 11px; border-collapse: collapse; font-size: 11.5px; }
#sample th, #sample td { text-align: left; padding: 3px 0; border-top: 1px solid rgba(255,255,255,0.055); }
#sample th { width: 42%; color: #8e97a4; font-weight: 400; }
#sample td b { color: #fff; font-weight: 600; }
#sample td .low { color: #ffb366; font-size: 10.5px; }
#sample .notes { margin-top: 11px; font-size: 10.5px; color: #78808d; }
#sample .notes p { margin: 0 0 4px; }
#sample .tag {
  display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 10.5px;
  border: 1px solid rgba(255,255,255,0.16); color: #a8b0bc; margin-left: 5px;
}
/* 3つの時点（PLATEAU 2021 / 現地・撮影日 / 計画・完了予定）の帯。
   「数年遅れるPLATEAU → 看板で分かる未来」をひと目で見せる。 */
#sample .timeline {
  position: absolute; top: 12px; right: 14px;
  display: flex; align-items: stretch; gap: 0; pointer-events: none;
  max-width: calc(100% - 470px); flex-wrap: nowrap;
}
#sample .timeline .seg {
  position: relative; min-width: 150px; max-width: 220px;
  padding: 8px 14px; margin: 0 5px;
  border: 1px solid rgba(255,255,255,0.1); border-radius: 11px;
  background: rgba(8,10,14,0.82); backdrop-filter: blur(12px);
  box-shadow: 0 10px 30px rgba(0,0,0,0.45);
  opacity: 0.5; transition: opacity .5s, border-color .5s, transform .5s;
}
#sample .timeline .seg.active { opacity: 1; transform: translateY(-2px); }
#sample .timeline .seg.blue.active { border-color: rgba(45,125,255,0.7); }
#sample .timeline .seg.orange.active { border-color: rgba(255,140,26,0.6); }
#sample .timeline .when {
  display: flex; align-items: center; gap: 6px;
  font-size: 10.5px; letter-spacing: .02em; color: #aeb6c4;
}
#sample .timeline .when .dot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
#sample .timeline .when .dot.grey { background: #8a93a3; }
#sample .timeline .when .dot.orange { background: #ff8c1a; }
#sample .timeline .when .dot.blue { background: #2d7dff; }
#sample .timeline .what { margin-top: 3px; font-size: 12px; color: #eef1f6; font-weight: 600; }
#sample .timeline .arrow { align-self: center; color: #5c6782; font-size: 13px; padding: 0 1px; }
/* 「PLATEAUのまま ⇄ 看板で更新した街」の切り替え。差分をその場で比べる。 */
#sample .toggle {
  position: absolute; bottom: 16px; left: calc(50% + 215px);
  transform: translateX(-50%); pointer-events: auto;
  display: inline-flex; padding: 4px; gap: 3px; border-radius: 999px;
  border: 1px solid rgba(255,255,255,0.12); background: rgba(8,10,14,0.86);
  backdrop-filter: blur(12px); box-shadow: 0 10px 30px rgba(0,0,0,0.5);
}
#sample .toggle button {
  appearance: none; cursor: pointer; border: 0; border-radius: 999px;
  padding: 7px 14px; font: inherit; font-size: 12px; font-weight: 600;
  background: transparent; color: #c2cad6;
}
#sample .toggle button[aria-pressed="true"] { color: #06101f; }
#sample .toggle button.plateau[aria-pressed="true"] { background: linear-gradient(180deg,#ffb366,#ff8c1a); }
#sample .toggle button.updated[aria-pressed="true"] { background: linear-gradient(180deg,#8fc0ff,#2d7dff); }
@media (max-width: 860px) {
  #sample .timeline { display: none; } /* 狭い画面ではパネルと干渉するので隠す */
  #sample .toggle { left: 50%; bottom: 10px; }
}
@media (max-width: 560px) {
  #sample { justify-content: flex-end; }
  #sample .panel { width: auto; max-height: 68%; margin: 0 7px 7px; }
}
`;

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function centroid(ring: [number, number][]): { lng: number; lat: number } {
  const pts = ring.slice(0, 4);
  return {
    lng: pts.reduce((s, p) => s + p[0], 0) / pts.length,
    lat: pts.reduce((s, p) => s + p[1], 0) / pts.length,
  };
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/** 画面がゆっくり回る速さ[度/秒]。1周 ≒ 60秒。スマホでも重くない程度に遅く。 */
const ORBIT_DEG_PER_SEC = 6;

type Orbit = { stop: () => void };

/**
 * 対象の敷地に寄ったまま、建物を中心にゆっくり旋回する（コンセプトの「差分」を
 * 回り込んで見せる）。**画面（地図）を触ったら止めて**操作をユーザーに返す。
 * パネルのスクロールや切り替えでは止めない（地図の canvas だけを見る）。
 */
function startOrbit(
  view: ThreeView<any>,
  center: { lng: number; lat: number; height: number },
  start: { distance: number; pitch: number; heading: number },
): Orbit {
  // 地図の canvas（パネル操作では止めたくないので window ではなく app を見る）。
  const canvas = document.getElementById("app");
  let stopped = false;
  let raf = 0;
  let heading = start.heading;
  let last = performance.now();

  const stop = () => {
    if (stopped) return;
    stopped = true;
    cancelAnimationFrame(raf);
    canvas?.removeEventListener("pointerdown", stop);
    canvas?.removeEventListener("wheel", stop);
    canvas?.removeEventListener("touchstart", stop);
  };

  const tick = (now: number) => {
    if (stopped) return;
    // dt は大きなコマ落ち（タブ復帰など）で飛ばないよう上限を付ける。
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    heading = (heading + ORBIT_DEG_PER_SEC * dt) % 360;
    view.setCamera({
      lng: center.lng,
      lat: center.lat,
      height: center.height,
      distance: start.distance,
      pitch: start.pitch,
      heading,
    });
    raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);

  // 画面を触ったら止めて操作をユーザーに返す。
  canvas?.addEventListener("pointerdown", stop);
  canvas?.addEventListener("wheel", stop, { passive: true });
  canvas?.addEventListener("touchstart", stop, { passive: true });

  return { stop };
}

/** 値を単位付きで出す。 */
function formatValue(field: string, value: unknown): string {
  return `${value}${FIELD_UNITS[field] ?? ""}`;
}

export type SampleDiag = {
  url: string;
  count: number;
  generated: string | null;
  /** 今開いている件。 */
  openedId: string | null;
  /** 3Dを立てた件。 */
  builtId: string | null;
  outcomes: Record<string, number>;
};

export async function runSampleMode(
  view: ThreeView<any>,
  options: {
    url?: string;
    id?: string | null;
    fast?: boolean;
    freecam?: boolean;
    /** 立ち上がった建物に日影（冬至・地上1.5m）を付ける。dev-policy §10。 */
    shadow?: boolean;
    /** 公式3D Tiles のレイヤ。置き換わるアニメで既存建物（gml_id）を隠すのに使う。 */
    tilesLayer?: {
      on: (event: string, cb: (e: { evaluator: TileEvaluator }) => void) => void;
    };
  } = {},
): Promise<SampleDiag> {
  const url = options.url ?? SAMPLES_URL;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(
      `${url} を読めない (${response.status})。` +
        `先に python pipeline/make_sample_mode.py を動かす。`,
    );
  }
  const data = (await response.json()) as SamplesFile;

  const diag: SampleDiag = {
    url,
    count: data.samples.length,
    generated: data.generated ?? null,
    openedId: null,
    builtId: null,
    outcomes: {},
  };
  for (const s of data.samples) {
    diag.outcomes[s.outcome] = (diag.outcomes[s.outcome] ?? 0) + 1;
  }

  // --- 画面を組む ---
  document.head.appendChild(el("style")).textContent = CSS;
  const root = el("div");
  root.id = "sample";
  const panel = el("div", "panel");
  root.appendChild(panel);
  // 3時点の帯（PLATEAU 2021 / 現地 / 計画）。件ごとに組み直す。
  const timeline = el("div", "timeline");
  root.appendChild(timeline);
  document.body.appendChild(root);

  /** 読み取った値（なければ undefined）。根拠のない値は出さないための入口。 */
  const val = (sample: Sample, field: string): unknown =>
    sample.values[field]?.value;

  /** 3時点の帯を組む。実データにある項目だけを出し、無い時点は控えめに書く。 */
  function buildTimeline(sample: Sample) {
    timeline.replaceChildren();
    const v = sample.volume;
    if (!v) {
      timeline.style.display = "none";
      return;
    }
    timeline.style.display = "";
    const use = val(sample, "use");
    const floors = val(sample, "floors_above");
    const end = val(sample, "construction_end");
    const posted = val(sample, "notice_posted");
    const existing = v.existing;
    const plannedWhat =
      [use, v.display_height_m ? `${v.display_height_m.toFixed(1)}m` : null,
       floors ? `地上${floors}階` : null].filter(Boolean).join("・") ||
      "新しい建物";

    const segs = [
      {
        tone: existing ? "orange" : "grey",
        when: "PLATEAU（2021年調査）",
        what:
          existing && existing.height_m != null
            ? `建物あり・約${Math.round(existing.height_m)}m`
            : "建物なし（更地）",
      },
      {
        tone: "orange",
        when: posted ? `現地（看板 ${posted}）` : "現地",
        what: "工事のお知らせ",
      },
      {
        tone: "blue",
        when: end ? `計画（${end} 完了予定）` : "計画",
        what: plannedWhat,
      },
    ];
    segs.forEach((s, i) => {
      if (i) timeline.appendChild(el("div", "arrow", "→"));
      const seg = el("div", `seg ${s.tone}`);
      seg.dataset.phase = String(i);
      const when = el("div", "when");
      when.appendChild(el("span", `dot ${s.tone}`));
      when.appendChild(el("span", undefined, s.when));
      seg.appendChild(when);
      seg.appendChild(el("div", "what", s.what));
      timeline.appendChild(seg);
    });
  }

  /** 帯の i 番目を強調する（3Dで今見えている時点に合わせる）。 */
  function setTimelinePhase(i: number) {
    for (const seg of timeline.querySelectorAll<HTMLElement>(".seg")) {
      seg.classList.toggle("active", Number(seg.dataset.phase) === i);
    }
  }

  const head = el("div", "head");
  const title = el("h1");
  title.innerHTML = "Before It's Built — <b>サンプルで試す</b>";
  head.appendChild(title);
  head.appendChild(
    el(
      "p",
      undefined,
      `撮った看板をAIエージェントに通した記録です。この画面を開いてもモデルは呼ばれません（${data.generated} に計算）。`,
    ),
  );
  panel.appendChild(head);

  const chips = el("div", "chips");
  panel.appendChild(chips);
  const body = el("div", "body");
  panel.appendChild(body);

  // --- 3D 側 ---
  let buildingLayer: Layer | null = null;
  let existingLayer: Layer | null = null;
  let shadowStudy: ShadowStudy | null = null;
  let orbit: Orbit | null = null;
  // トグル用: 今の件のボリュームを覚えておき、青を出し直せるようにする。
  let currentVolume: Volume | null = null;
  let toggleMode: "updated" | "plateau" = "updated";
  const toggleWrap = el("div", "toggle");
  toggleWrap.style.display = "none";
  const prefersReducedMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)",
  ).matches;

  // --- PLATEAU の既存建物を gml_id で消す/戻す（置き換わるアニメ用） ---
  // 公式3D Tiles は1棟だけ `show:false` にできる（stage0 と同じ評価の仕組み）。
  // これで「古い建物そのもの」を消し、同じ場所に沈むオレンジの箱を出せる。
  let latestEvaluator: TileEvaluator | null = null;
  let hiddenGmlId: string | null = null; // 今消している既存建物
  let restoreGmlId: string | null = null; // 件が替わったとき戻すべき直前の建物
  const applyTileVisibility = () => {
    latestEvaluator?.evaluate(
      ({ properties }) => {
        const id = properties?.["gml_id"];
        if (typeof id !== "string") return {};
        if (id === hiddenGmlId) return { show: false };
        if (id === restoreGmlId) return { show: true }; // 消していたのを戻す
        return {};
      },
      { filters: ["gml_id"] },
    );
  };
  options.tilesLayer?.on("featureCreated", ({ evaluator }) => {
    latestEvaluator = evaluator;
    applyTileVisibility(); // タイルが流れてきても消したままにする
  });
  options.tilesLayer?.on("featureUpdated", ({ evaluator }) => {
    latestEvaluator = evaluator;
    applyTileVisibility();
  });
  /** 既存建物を消す（null で戻す）。 */
  const setHiddenExisting = (gmlId: string | null) => {
    if (hiddenGmlId && hiddenGmlId !== gmlId) restoreGmlId = hiddenGmlId;
    hiddenGmlId = gmlId;
    applyTileVisibility();
  };

  // アニメの世代。件を切り替えたら増やし、進行中の rAF に「もう別の件」と知らせる
  // （古いアニメが新しいレイヤを動かしてしまうのを防ぐ）。
  let animGen = 0;
  const clearBuilding = () => {
    animGen++;
    buildingLayer?.delete();
    buildingLayer = null;
    // 置き換わるアニメの既存建物（オレンジ）も片付ける。
    existingLayer?.delete();
    existingLayer = null;
    // 消していた PLATEAU の本物の建物を戻し、トグルを隠す。
    setHiddenExisting(null);
    currentVolume = null;
    toggleMode = "updated";
    toggleWrap.style.display = "none";
    // 旋回も止める（件を切り替えたら前の旋回は残さない）。
    orbit?.stop();
    orbit = null;
    // 日影のスライダーとレイヤも片付ける（件を切り替えたら前の影は残さない）。
    shadowStudy?.destroy();
    shadowStudy = null;
  };

  /** 1フレームずつ t:0→1 を進めるアニメ。世代が変わったら途中でやめる。 */
  function animate(durationMs: number, onFrame: (t: number) => void): Promise<void> {
    const gen = animGen;
    const started = performance.now();
    return new Promise<void>((done) => {
      const tick = () => {
        if (gen !== animGen) return done(); // 別の件に切り替わった
        const t = Math.min(1, (performance.now() - started) / durationMs);
        onFrame(t);
        if (t < 1) requestAnimationFrame(tick);
        else done();
      };
      requestAnimationFrame(tick);
    });
  }

  function geojsonSource(footprint: [number, number][]) {
    return view.addSource({
      type: "geojson",
      data: {
        type: "FeatureCollection",
        features: [{
          type: "Feature",
          properties: {},
          geometry: { type: "Polygon", coordinates: [footprint] },
        }],
      },
    });
  }

  /** 計画建物（青）を本来の高さで立てる（トグルで出し直すとき用。アニメなし）。 */
  function makeBluePlanned(volume: Volume): Layer {
    const source = geojsonSource(volume.footprint);
    return view.addLayer({
      type: "vector",
      source,
      polygon: {
        color: BLUE, height: volume.base_z_m, extrudedHeight: volume.display_height_m,
        perPositionHeight: false, clampToGround: false,
        castShadow: true, receiveShadow: true,
        outlineShow: true, outlineColor: BLUE_LIGHT, opacity: 0.95,
        emissiveColor: BLUE, emissiveIntensity: 0.22,
      },
    } as any);
  }

  /** PLATEAUのまま（本物の既存建物を戻す・青なし）⇄ 看板で更新した街（本物を消して青）。 */
  function applyToggle(mode: "updated" | "plateau") {
    toggleMode = mode;
    for (const btn of toggleWrap.querySelectorAll<HTMLElement>("button")) {
      btn.setAttribute("aria-pressed", String(btn.dataset.mode === mode));
    }
    if (!currentVolume) return;
    if (mode === "plateau") {
      // 青を消し、PLATEAU の本物の建物を元どおり表示する。
      buildingLayer?.delete();
      buildingLayer = null;
      setHiddenExisting(null);
      setTimelinePhase(0);
    } else {
      // 本物の建物を消し、青い計画建物を出す。
      setHiddenExisting(currentVolume.existing?.gml_id ?? null);
      if (!buildingLayer) buildingLayer = makeBluePlanned(currentVolume);
      setTimelinePhase(2);
    }
  }

  // トグルのボタンを組む（押せるのは建物が立ってから）。
  root.appendChild(toggleWrap);
  for (const [mode, label] of [
    ["plateau", "PLATEAUのまま"],
    ["updated", "看板で更新した街"],
  ] as const) {
    const btn = el("button", mode);
    btn.type = "button";
    btn.dataset.mode = mode;
    btn.textContent = label;
    btn.setAttribute("aria-pressed", String(mode === "updated"));
    btn.addEventListener("click", () => applyToggle(mode));
    toggleWrap.appendChild(btn);
  }

  /**
   * 置き換わるアニメの前半: **PLATEAUの古い建物そのもの**をオレンジにして沈めて消す。
   *
   * PLATEAU の 3D Tiles はタイルの地物なので動かせない（沈められない）。そこで
   * 既存建物の実輪郭に合わせたオレンジの箱を重ねて用意し、箱が見えるようになったら
   * **本物の建物を `gml_id` で `show:false` にして消す**（灰色→オレンジに切り替わる）。
   * あとはオレンジの箱を地面へ沈めて消す。本物は消えているので下から灰色は出ない。
   */
  async function demolishExisting(existing: Existing): Promise<void> {
    if (existing.height_m == null || existing.base_z_m == null) return;
    const baseZ = existing.base_z_m;
    const extrudeM = Math.max(existing.height_m, 5);
    const source = geojsonSource(existing.footprint); // 本物の建物の実輪郭
    // height = 底面の楕円体高。下げると箱ごと地中に沈む。
    const describe = (sinkM: number, opacity: number) =>
      ({
        type: "vector",
        source,
        polygon: {
          color: ORANGE,
          height: baseZ - sinkM,
          extrudedHeight: extrudeM,
          perPositionHeight: false,
          clampToGround: false,
          castShadow: true,
          receiveShadow: true,
          outlineShow: true,
          outlineColor: ORANGE_LIGHT,
          opacity,
          emissiveColor: ORANGE,
          emissiveIntensity: 0.3,
        },
      }) as any;

    // 1) 本物の建物に重ねてオレンジの箱を用意（まだ本物の灰色が手前に見える）。
    existingLayer = view.addLayer(describe(0, 0.95));
    setTimelinePhase(0); // 帯: PLATEAU（2021年調査）を強調
    // NAVARA のベクタレイヤは追加から表示まで1〜2秒かかるので、切り替える前に待つ。
    await sleep(EXISTING_HOLD_MS);
    // 2) 本物の PLATEAU 建物を消す → 用意しておいたオレンジが現れる（灰→オレンジ）。
    setHiddenExisting(existing.gml_id);
    setTimelinePhase(1); // 帯: 現地（看板）を強調しながら沈める
    // 3) オレンジを地面へ沈めて消す。本物は消えているので下から灰色は出ない。
    await animate(SINK_MS, (t) => {
      const sink = (extrudeM + 2) * (t * t);
      existingLayer?.update(describe(sink, Math.max(0, 0.95 * (1 - t))));
    });
    existingLayer?.delete();
    existingLayer = null;
  }

  /** 建物を 0 から本来の高さまで立ち上げる（必要なら先に既存建物を解体する）。 */
  async function raise(volume: Volume) {
    currentVolume = volume; // トグルで出し直せるように覚えておく
    const source = geojsonSource(volume.footprint);
    // `extrudedHeight` は**底面からの相対の押し出し量**（NAVARA の仕様。
    // 「Extrusion height for polygons in meters」。絶対の天面高ではない）。
    // `height` が底面の楕円体高で、そこから `extrudedHeight` m 立ち上がる。
    // ここに `base_z + 高さ` を渡すと約66mの塔になる取り違えを一度やっている。
    const describe = (riseM: number) =>
      ({
        type: "vector",
        source,
        polygon: {
          color: BLUE,
          height: volume.base_z_m, // 底面の楕円体高（dev-policy §15）
          extrudedHeight: riseM, // 底面からの高さ[m]
          perPositionHeight: false,
          clampToGround: false,
          castShadow: true,
          receiveShadow: true,
          outlineShow: true,
          outlineColor: BLUE_LIGHT,
          opacity: 0.95,
          emissiveColor: BLUE,
          emissiveIntensity: 0.22,
        },
      }) as any;

    const hM = volume.display_height_m;
    // 建物が立ったら「PLATEAUのまま⇄看板で更新した街」のトグルを押せるようにする。
    const showToggle = () => {
      toggleWrap.style.display = volume.existing ? "" : "none";
    };
    if (options.fast) {
      // 計測・審査用の一発表示。本物の既存建物は消して、青を本来の高さで立てる。
      if (volume.existing) setHiddenExisting(volume.existing.gml_id);
      buildingLayer = view.addLayer(describe(hM));
      setTimelinePhase(2);
      showToggle();
      return;
    }
    // 置き換わるアニメ: 本物の既存建物をオレンジにして沈めてから青を立てる。
    if (volume.existing && !prefersReducedMotion) {
      await demolishExisting(volume.existing);
    } else if (volume.existing) {
      // 動きを嫌う設定のときはアニメなしで本物を消す。
      setHiddenExisting(volume.existing.gml_id);
    }
    // 計画（青）の時点へ。帯も計画を強調する。
    setTimelinePhase(2);
    // 高さ0から立ち上げる。押し出しが0だと面が潰れるので、わずかに持ち上げる。
    buildingLayer = view.addLayer(describe(0.05));
    await animate(RISE_MS, (t) => {
      // 立ち上がりは最後にゆるめる（easeOutCubic）
      const eased = 1 - Math.pow(1 - t, 3);
      buildingLayer?.update(describe(Math.max(0.05, hM * eased)));
    });
    showToggle();
  }

  // --- 1件を流す ---
  let running = 0;
  async function open(sample: Sample) {
    const token = ++running;
    diag.openedId = sample.id;
    clearBuilding();
    body.replaceChildren();
    // 3時点の帯を組み直す。まだ PLATEAU（今は差分前）の時点を強調しておく。
    buildTimeline(sample);
    setTimelinePhase(0);

    for (const chip of chips.querySelectorAll(".chip")) {
      chip.setAttribute("aria-pressed", String(chip.getAttribute("data-id") === sample.id));
    }

    body.appendChild(
      el("div", "hint", `${sample.label} — エージェントが${sample.steps.length}手で判断しました`),
    );

    // 建物があるなら、立ち上がるところが見えるように先に寄っておき、
    // エージェントが処理している間、敷地を中心にゆっくり旋回する（コンセプトの
    // 「差分」を回り込んで見せる。画面を触れば止まる）。
    // `freecam` のときは URL のカメラ（main.ts の setCamera）をそのまま使う。
    // 段階1と同じ視点で撮り比べて縦横比を測るための計測用の抜け道。
    if (sample.volume && !options.freecam) {
      const center = centroid(sample.volume.footprint);
      // 建物を探さずに済むよう、しっかり寄る。注視点は建物の中ほどの高さ
      // （地面＋半分くらい）にして、建物が画面の中央に来るようにする。
      const lookHeight =
        sample.volume.base_z_m + Math.max(sample.volume.display_height_m, 8) * 0.5;
      // 旋回の開始を、新しい建物の**面を正面に**見る向きにそろえる（角からではなく
      // 面から回り込む）。建物の輪郭の最初の辺の向きから、面に正対する方位を出す。
      const ring = sample.volume.footprint;
      const mLat = 111132.92;
      const mLon = 111412.84 * Math.cos((center.lat * Math.PI) / 180);
      const dE = (ring[1][0] - ring[0][0]) * mLon;
      const dN = (ring[1][1] - ring[0][1]) * mLat;
      const faceHeading =
        (((Math.atan2(dE, dN) * 180) / Math.PI + 90) % 360 + 360) % 360;
      const orbitStart = { distance: 130, pitch: -24, heading: faceHeading };
      const oblique = {
        lng: center.lng,
        lat: center.lat,
        height: lookHeight,
        ...orbitStart,
      };
      const orbitCenter = { lng: center.lng, lat: center.lat, height: lookHeight };
      // fast / reduced-motion 以外は「高い真上からズーム → 降りて傾けて旋回開始」。
      if (!options.fast && !prefersReducedMotion) {
        // 1) 高い真上（pitch -90 の見下ろし）からズームイン。flyTo は pitch -90 を
        //    取りきれない（斜めになる）ので、setCamera を1フレームずつ動かして
        //    自前で降ろす。カメラは敷地の真上、height はカメラの高度[m]。
        void (async () => {
          const fromAlt = 420; // 真上・高いところから
          const toAlt = 230; // 真上のまま降りる
          // 注: pitch -90 の見下ろしは `roll:0` を付けないと斜めになる（flyTo も
          // pitch -90 を取りきれない）。setCamera を1フレームずつ動かして降ろす。
          // 速すぎるとタイルの読み込みが追いつかないので、ゆっくり降ろす。
          await animate(3200, (t) => {
            const eased = 1 - Math.pow(1 - t, 2); // easeOutQuad
            view.setCamera({
              lng: center.lng,
              lat: center.lat,
              height: lookHeight + fromAlt + (toAlt - fromAlt) * eased,
              pitch: -90,
              heading: faceHeading,
              roll: 0,
            });
          });
          if (token !== running || !sample.volume) return;
          // 真上でしっかり止める。最初の建物はコールド読み込みで真上のタイルが
          // 来るのに時間がかかるので、見下ろしが見える時間を長めに取る。
          await sleep(2200);
          if (token !== running || !sample.volume) return;
          // 2) 降りながら斜めに傾け、着いたら旋回を始める。
          const arrived = await view.flyTo(oblique, { duration: 1.8 });
          if (arrived && token === running && sample.volume) {
            orbit = startOrbit(view, orbitCenter, orbitStart);
          }
        })();
      } else {
        // 計測/動きを嫌う設定: 斜めの定点へ一気に寄るだけ。
        void view.flyTo(oblique, { duration: options.fast ? 0.1 : 1.0 });
      }
    }

    for (const step of sample.steps) {
      if (token !== running) return; // 途中で別の件に切り替えられた
      if (!options.fast) await sleep(STEP_DELAY_MS);
      if (token !== running) return;

      const row = el("div", `step${step.failed ? " failed" : ""}`);
      row.appendChild(el("div", "num", String(step.n)));
      const right = el("div");
      const tool = el("div", "tool");
      tool.textContent = step.tool_label;
      tool.appendChild(el("code", undefined, ` ${step.tool}()`));
      right.appendChild(tool);
      if (step.thought) right.appendChild(el("div", "thought", `「${step.thought}」`));
      if (step.detail) {
        const detail = el("div", "detail");
        // 不一致だけオレンジで出す（dev-policy §10）
        if (step.detail.includes("不一致") || step.failed) {
          detail.classList.add("mm");
        }
        detail.textContent = step.detail;
        right.appendChild(detail);
      }
      row.appendChild(right);
      body.appendChild(row);
      body.scrollTop = body.scrollHeight;
    }

    if (token !== running) return;

    // --- 結論 ---
    const style = OUTCOME_STYLE[sample.outcome];
    const card = el("div", `card ${style.tone}`);
    const heading = el("h2", undefined, `${style.text} — ${sample.outcome_label}`);
    if (sample.status_label) {
      heading.appendChild(el("span", "tag", sample.status_label));
    }
    card.appendChild(heading);
    card.appendChild(el("p", undefined, sample.summary));
    if (sample.unresolved.length) {
      const list = el("ul");
      for (const item of sample.unresolved) {
        list.appendChild(el("li", undefined, item));
      }
      card.appendChild(list);
    }
    body.appendChild(card);

    // --- 読み取った値 ---
    const fields = Object.keys(sample.values).filter((f) => f !== "notice_type");
    if (fields.length) {
      const table = el("table");
      body.appendChild(table);
      for (const field of fields) {
        const item = sample.values[field];
        const tr = el("tr");
        tr.appendChild(el("th", undefined, FIELD_LABELS[field] ?? field));
        const td = el("td");
        td.appendChild(el("b", undefined, formatValue(field, item.value)));
        // 自信のない読み取りは隠さず出す（dev-policy §10）
        if (typeof item.confidence === "number" && item.confidence < 0.9) {
          td.appendChild(el("span", "low", ` 確信度 ${item.confidence}`));
        }
        tr.appendChild(td);
        table.appendChild(tr);
      }
    }

    // --- 断りと出どころ ---
    const notes = el("div", "notes");
    notes.appendChild(el("p", undefined, sample.photo_note + "。"));
    if (sample.filing_no !== null) {
      notes.appendChild(
        el(
          "p",
          undefined,
          `届出No.${sample.filing_no} と ${sample.match_key} で照合` +
            (sample.filing_verified ? "（目視確認済み）" : "（届出の目視確認はまだ）") +
            "。",
        ),
      );
    }
    if (sample.volume) {
      const orientWord =
        sample.volume.orientation_source === "plateau_building"
          ? "既存建物に合わせた"
          : sample.volume.orientation_source === "nearest_road"
            ? "最寄りの道路に合わせた"
            : sample.volume.orientation_source === "neighbor_majority"
              ? "周りの建物に合わせた"
              : "北向きの";
      notes.appendChild(
        el(
          "p",
          undefined,
          `形は建築面積 ${sample.volume.building_area_m2}㎡ と同じ面積の正方形で向きは${orientWord}もの、` +
            `位置は街区の代表点で、いずれも近似です。` +
            `高さは${sample.volume.display_height_source === "max_height_m" ? "最高の高さ" : "高さ"}を使っています。` +
            (sample.volume.existing
              ? `オレンジは PLATEAU（2021年調査）の建物で、沈めて青い計画建物に置き換えています。`
              : ""),
        ),
      );
    }
    notes.appendChild(el("p", undefined, data.sources.join(" / ")));
    body.appendChild(notes);
    body.scrollTop = body.scrollHeight;

    if (sample.volume) {
      await raise(sample.volume);
      if (token === running) {
        diag.builtId = sample.id;
        // 日影（冬至・地上1.5m・直方体近似の目安）。dev-policy §10。
        if (options.shadow) {
          shadowStudy = addShadowStudy(view, [sample.volume], { initialHour: 10 });
        }
      }
    }
  }

  // --- 選ぶところ ---
  for (const sample of data.samples) {
    const chip = el("button", "chip");
    chip.type = "button";
    chip.dataset.id = sample.id;
    chip.setAttribute("aria-pressed", "false");
    chip.appendChild(el("span", `dot ${OUTCOME_STYLE[sample.outcome].tone}`));
    chip.appendChild(
      el("span", undefined, sample.label === "対象外の看板" ? "対象外の看板" : sample.label),
    );
    chip.addEventListener("click", () => void open(sample));
    chips.appendChild(chip);
  }

  const first =
    data.samples.find((s) => s.id === options.id) ??
    // 既定は3Dが立つ件から始める（最初の画面で何が起きるかが伝わるように）
    data.samples.find((s) => s.volume) ??
    data.samples[0];
  if (first) await open(first);

  return diag;
}
