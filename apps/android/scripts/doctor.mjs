import { access } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const checks = [];
const command = name => {
  try { execFileSync('bash', ['-lc', `command -v ${name}`], { stdio: 'ignore' }); return true; }
  catch { return false; }
};
const exists = async file => { try { await access(file); return true; } catch { return false; } };

checks.push(['web bundle', await exists(path.join(root, 'www/index.html'))]);
checks.push(['Capacitor Android project', await exists(path.join(root, 'android'))]);
checks.push(['Java', command('java')]);
checks.push(['adb', command('adb')]);
checks.push(['Android SDK', !!process.env.ANDROID_HOME || !!process.env.ANDROID_SDK_ROOT]);
for (const [name, ok] of checks) console.log(`${ok ? 'OK ' : 'MISS'} ${name}`);
if (!checks[0][1]) process.exitCode = 1;
