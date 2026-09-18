import { build } from 'esbuild';
import { copyFile, mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const androidRoot = path.resolve(here, '..');
const repoRoot = path.resolve(androidRoot, '../..');
const sourceRoot = path.join(repoRoot, 'src/meuharness/web_assets');
const www = path.join(androidRoot, 'www');

await mkdir(path.join(www, 'assets'), { recursive: true });
let html = await readFile(path.join(sourceRoot, 'index.html'), 'utf8');
html = html.replace(
  'width=device-width,initial-scale=1',
  'width=device-width,initial-scale=1,viewport-fit=cover,maximum-scale=1',
);
const bootstrap = `<script>\n(() => {\n  document.documentElement.classList.add('actis-mobile');\n  const q = new URLSearchParams(location.search).get('api');\n  if (q) localStorage.setItem('actis.api.base', q.replace(/\\/$/, ''));\n  window.ACTIS_API_BASE = localStorage.getItem('actis.api.base') || '';\n})();\n</script>`;
html = html.replace('</head>', `<link rel="stylesheet" href="/mobile.css?v=1">${bootstrap}</head>`);
html = html.replace('</body>', '<script src="/mobile-runtime.js"></script></body>');
await writeFile(path.join(www, 'index.html'), html);
await copyFile(path.join(sourceRoot, 'visual-system.css'), path.join(www, 'assets/visual-system.css'));
await copyFile(path.join(androidRoot, 'src/mobile.css'), path.join(www, 'mobile.css'));

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
