import { defineConfig } from "vite";

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
