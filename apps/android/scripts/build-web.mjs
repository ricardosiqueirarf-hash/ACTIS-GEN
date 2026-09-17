import { build } from 'esbuild';
import { access, copyFile, mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const androidRoot = path.resolve(here, '..');
const repoRoot = path.resolve(androidRoot, '../..');
const sourceRoot = path.join(repoRoot, 'src/meuharness/web_assets');
const www = path.join(androidRoot, 'www');
const wwwAssets = path.join(www, 'assets');

await mkdir(wwwAssets, { recursive: true });
let html = await readFile(path.join(sourceRoot, 'index.html'), 'utf8');
html = html.replace(
  'width=device-width,initial-scale=1',
  'width=device-width,initial-scale=1,viewport-fit=cover,maximum-scale=1',
);
const bootstrap = `<script>\n(() => {\n  document.documentElement.classList.add('actis-mobile');\n  const q = new URLSearchParams(location.search).get('api');\n  const localCore = 'http://127.0.0.1:8765';\n  window.ACTIS_API_BASE = q ? q.replace(/\\/$/, '') : localCore;\n  localStorage.setItem('actis.api.base', window.ACTIS_API_BASE);\n})();\n</script>`;
html = html.replace('</head>', `<link rel="stylesheet" href="/mobile.css?v=5"><link rel="stylesheet" href="/embedded-core.css?v=2">${bootstrap}</head>`);
html = html.replace('</body>', '<script src="/mobile-runtime.js?v=5"></script></body>');
await writeFile(path.join(www, 'index.html'), html);
// Keep the APK web surface in lock-step with the current ACTIS GEN UI.
for (const entry of await readdir(sourceRoot, { withFileTypes: true })) {
  if (!entry.isFile()) continue;
  if (!/\.(css|js)$/.test(entry.name)) continue;
  await copyFile(path.join(sourceRoot, entry.name), path.join(wwwAssets, entry.name));
}
await copyFile(path.join(androidRoot, 'src/mobile.css'), path.join(www, 'mobile.css'));
await copyFile(path.join(androidRoot, 'src/embedded-core.css'), path.join(www, 'embedded-core.css'));

await build({
  entryPoints: [path.join(androidRoot, 'src/mobile-runtime.js')],
  bundle: true,
  minify: false,
  format: 'iife',
  platform: 'browser',
  outfile: path.join(www, 'mobile-runtime.js'),
  target: ['chrome120'],
});

console.log(`ACTIS Android web bundle atualizado em ${www}`);
