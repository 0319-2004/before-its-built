import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";

/**
 * `data/` の GeoJSON を dev サーバーから配る。
 *
 * `data/` はリポジトリに入れない決まりなので（届出のPDFや写真が入っている）、
 * ビルド成果物に取り込まず、**開発中だけ** `/data/…` で読めるようにする。
 * 配るのは `.geojson` だけ。PDFや写真はこの経路でも出ない。
 *
 * 公開用のデプロイでは、個人名を含まない planned.geojson を別途
 * 置き直す必要がある（まだ決めていない。dev-policy §9・§14）。
 */
function serveDataGeojson(): Plugin {
  const root = resolve(import.meta.dirname, "..", "data");
  return {
    name: "serve-data-geojson",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        const path = (req.url ?? "").split("?")[0];
        if (!path.startsWith("/data/") || !path.endsWith(".geojson")) return next();
        // `..` でディレクトリを抜けられないようにする
        const file = resolve(root, path.slice("/data/".length));
        if (!file.startsWith(root + "/")) return next();
        try {
          const body = await readFile(file);
          res.setHeader("Content-Type", "application/geo+json; charset=utf-8");
          res.end(body);
        } catch {
          next();
        }
      });
    },
  };
}

// NAVARA は WASM + Web Worker を使う。SharedArrayBuffer を要求されたら
// cross-origin isolation が必要になるので、その時だけ COOP/COEP を有効にする。
//   COOP_COEP=1 npm run dev
// 既定で切ってあるのは、COEP: require-corp が CORP ヘッダを返さない
// 外部タイル（PLATEAU の配信を含む）の読み込みを壊しうるため。
const headers =
  process.env.COOP_COEP === "1"
    ? {
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Embedder-Policy": "require-corp",
      }
    : undefined;

export default defineConfig({
  plugins: [serveDataGeojson()],
  server: { headers },
  preview: { headers },
  // プリバンドルすると WASM / worker の解決に失敗することがあるため除外する。
  optimizeDeps: {
    exclude: [
      "@navaramap/three",
      "@navaramap/three-default-plugin",
      "@navaramap/engine",
      "@navaramap/core",
      "@navaramap/worker",
    ],
  },
});
