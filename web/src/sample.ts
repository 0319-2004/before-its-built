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

export const SAMPLES_URL = `${import.meta.env.BASE_URL}samples.json`;

/** 1手を出してから次を出すまで[ms]。読みながら追える速さにする。 */
const STEP_DELAY_MS = 620;
/** 建物が立ち上がる時間[ms]。 */
const RISE_MS = 1400;

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

type Volume = {
  footprint: [number, number][];
  building_area_m2: number;
  display_height_m: number;
  display_height_source: string;
  base_z_m: number;
  base_z_rough: boolean;
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
  document.body.appendChild(root);

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
  let shadowStudy: ShadowStudy | null = null;
  const clearBuilding = () => {
    buildingLayer?.delete();
    buildingLayer = null;
    // 日影のスライダーとレイヤも片付ける（件を切り替えたら前の影は残さない）。
    shadowStudy?.destroy();
    shadowStudy = null;
  };

  /** 建物を 0 から本来の高さまで立ち上げる。 */
  async function raise(volume: Volume) {
    const source = view.addSource({
      type: "geojson",
      data: {
        type: "FeatureCollection",
        features: [
          {
            type: "Feature",
            properties: {},
            geometry: { type: "Polygon", coordinates: [volume.footprint] },
          },
        ],
      },
    });
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
    if (options.fast) {
      // 計測・審査用の一発表示。最初から本来の高さで立てる。
      buildingLayer = view.addLayer(describe(hM));
      return;
    }
    // 高さ0から立ち上げる。押し出しが0だと面が潰れるので、わずかに持ち上げる。
    buildingLayer = view.addLayer(describe(0.05));
    const started = performance.now();
    await new Promise<void>((done) => {
      const tick = () => {
        const t = Math.min(1, (performance.now() - started) / RISE_MS);
        // 立ち上がりは最後にゆるめる（easeOutCubic）
        const eased = 1 - Math.pow(1 - t, 3);
        buildingLayer?.update(describe(Math.max(0.05, hM * eased)));
        if (t < 1) requestAnimationFrame(tick);
        else done();
      };
      requestAnimationFrame(tick);
    });
  }

  // --- 1件を流す ---
  let running = 0;
  async function open(sample: Sample) {
    const token = ++running;
    diag.openedId = sample.id;
    clearBuilding();
    body.replaceChildren();

    for (const chip of chips.querySelectorAll(".chip")) {
      chip.setAttribute("aria-pressed", String(chip.getAttribute("data-id") === sample.id));
    }

    body.appendChild(
      el("div", "hint", `${sample.label} — エージェントが${sample.steps.length}手で判断しました`),
    );

    // 建物があるなら、立ち上がるところが見えるように先に寄っておく。
    // `freecam` のときは URL のカメラ（main.ts の setCamera）をそのまま使う。
    // 段階1と同じ視点で撮り比べて縦横比を測るための計測用の抜け道。
    if (sample.volume && !options.freecam) {
      const center = centroid(sample.volume.footprint);
      void view.flyTo(
        {
          lng: center.lng,
          lat: center.lat,
          height: sample.volume.base_z_m,
          distance: 260,
          pitch: -22,
          heading: 25,
        },
        { duration: options.fast ? 0.1 : 1.6 },
      );
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
      notes.appendChild(
        el(
          "p",
          undefined,
          `形は建築面積 ${sample.volume.building_area_m2}㎡ と同じ面積の北向きの正方形、` +
            `位置は街区の代表点で、いずれも近似です。` +
            `高さは${sample.volume.display_height_source === "max_height_m" ? "最高の高さ" : "高さ"}を使っています。`,
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
