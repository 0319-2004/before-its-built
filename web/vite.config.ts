import { copyFile, mkdir, readdir, stat } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";

/**
 * NAVARA が同梱している WASM を、ビルド結果に入れ直す。
 *
 * `@navaramap/three` は `dist/assets/` にハッシュ付きのWASMを同梱していて、
 * パッケージの中のワーカーが**隣のファイル名で**それを読む。
 * vite はこの参照をたどりきれず、`draco_decoder-*.wasm` と
 * `navara_wasm_font_worker_bg-*.wasm` を出力に入れない。
 *
 * 入っていないと、dev では動くのに**ビルドしたときだけ**、
 * 404 で返ってきた index.html を WebAssembly.instantiate しようとして
 * `expected magic word 00 61 73 6d, found 3c 21 64 6f`（`<!do`）で落ちる。
 * PLATEAU の b3dm は Draco 圧縮なので、**街並みが丸ごと出なくなる**。
 *
 * vite が出した同名のファイルは上書きしない（ハッシュが同じなら中身も同じ）。
 */
function copyNavaraAssets(): Plugin {
  let outDir = "dist";
  return {
    name: "copy-navara-assets",
    apply: "build",
    configResolved(config) {
      outDir = config.build.outDir;
    },
    async closeBundle() {
      const from = resolve(
        import.meta.dirname,
        "node_modules/@navaramap/three/dist/assets",
      );
      if (!existsSync(from)) return;
      const to = resolve(import.meta.dirname, outDir, "assets");
      await mkdir(to, { recursive: true });

      let copied = 0;
      for (const name of await readdir(from)) {
        // ソースマップは配らない。ディレクトリは今のところ参照されていない。
        if (name.endsWith(".map")) continue;
        if ((await stat(join(from, name))).isDirectory()) continue;
        if (existsSync(join(to, name))) continue;
        await copyFile(join(from, name), join(to, name));
        copied += 1;
      }
      if (copied) this.info(`NAVARA 同梱のアセットを ${copied}件 入れ直した`);
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
  plugins: [copyNavaraAssets()],
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
