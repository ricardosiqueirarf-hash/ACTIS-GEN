import { Capacitor } from '@capacitor/core';
import { App } from '@capacitor/app';
import { Camera, CameraResultType, CameraSource } from '@capacitor/camera';
import { Clipboard } from '@capacitor/clipboard';
import { Device } from '@capacitor/device';
import { Directory, Encoding, Filesystem } from '@capacitor/filesystem';
import { Haptics, ImpactStyle } from '@capacitor/haptics';
import { Keyboard } from '@capacitor/keyboard';
import { LocalNotifications } from '@capacitor/local-notifications';
import { Network } from '@capacitor/network';
import { Share } from '@capacitor/share';
import { StatusBar, Style } from '@capacitor/status-bar';

const native = Capacitor.isNativePlatform();
const LOCAL_CORE = 'http://127.0.0.1:8765';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function testCore(base = LOCAL_CORE) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1800);
  try {
    const response = await fetch(`${base}/api/agents`, {
      credentials: 'include',
      signal: controller.signal,
      cache: 'no-store',
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const body = await response.json();
    if (!Array.isArray(body.agents)) throw new Error('Resposta inválida do ACTIS Core');
    return true;
  } finally {
    clearTimeout(timeout);
  }
}

function ensureBootOverlay() {
  let overlay = document.getElementById('actisEmbeddedBoot');
  if (overlay) return overlay;
  overlay = document.createElement('div');
  overlay.id = 'actisEmbeddedBoot';
  overlay.className = 'actis-embedded-boot';
  overlay.innerHTML = `
    <div class="actis-embedded-boot-card">
      <div class="actis-embedded-bot">◉</div>
      <div class="actis-mobile-setup-kicker">ACTIS ANDROID</div>
      <h2>Iniciando ACTIS Core</h2>
      <p id="actisBootText">Preparando agentes, memória e automações no próprio celular…</p>
      <div class="actis-boot-track"><i></i></div>
      <button id="actisBootRetry" hidden>Tentar novamente</button>
    </div>`;
  document.body.appendChild(overlay);
  return overlay;
}

async function waitForEmbeddedCore(timeoutMs = 45000) {
  const overlay = ensureBootOverlay();
  const text = overlay.querySelector('#actisBootText');
  const retry = overlay.querySelector('#actisBootRetry');
  overlay.classList.add('open');
  retry.hidden = true;

  const started = Date.now();
  let attempt = 0;
  while (Date.now() - started < timeoutMs) {
    attempt += 1;
    try {
      await testCore();
      window.ACTIS_API_BASE = LOCAL_CORE;
      localStorage.setItem('actis.api.base', LOCAL_CORE);
      window.actisSetApiBase?.(LOCAL_CORE);
      text.textContent = 'Core online.';
      overlay.classList.add('ready');
      await sleep(260);
      overlay.classList.remove('open');
      return true;
    } catch {}

    if (attempt === 3) text.textContent = 'Carregando runtime Python…';
    if (attempt === 8) text.textContent = 'Inicializando banco e serviços locais…';
    if (attempt === 15) text.textContent = 'Primeira inicialização pode demorar um pouco mais…';
    await sleep(900);
  }

  text.textContent = 'O Core embutido não conseguiu iniciar. Toque para tentar novamente.';
  retry.hidden = false;
  retry.onclick = () => waitForEmbeddedCore(timeoutMs);
  return false;
}

function createMobileNav() {
  if (document.getElementById('actisMobileNav')) return;
  const options = [
    ['world','⌘','World'], ['agents','◇','Agentes'], ['channels','▱','Canais'],
    ['connectors','⌁','Config'], ['runs','▷','Runs'], ['automations','ϟ','Rotinas'],
  ].filter(([view]) => document.querySelector(`.nav button[data-view="${view}"]`)).slice(0, 4);
  const nav = document.createElement('nav');
  nav.id = 'actisMobileNav';
  nav.className = 'actis-mobile-nav';
  nav.innerHTML = options.map(([view, icon, label]) =>
    `<button data-mobile-view="${view}"><span>${icon}</span><b>${label}</b></button>`
  ).join('') + '<button id="actisMobileMore"><span>☰</span><b>Mais</b></button>';
  nav.style.setProperty('--actis-mobile-columns', String(options.length + 1));
  document.body.appendChild(nav);
  nav.querySelectorAll('[data-mobile-view]').forEach(button => {
    button.onclick = async () => {
      const view = button.dataset.mobileView;
      document.querySelector(`.nav button[data-view="${view}"]`)?.click();
      nav.querySelectorAll('button').forEach(x => x.classList.toggle('active', x === button));
      if (native) await Haptics.impact({ style: ImpactStyle.Light }).catch(() => {});
    };
  });
  nav.querySelector('#actisMobileMore').onclick = () => {
    document.querySelector('.app')?.classList.toggle('nav-open');
  };
}

async function bindNativeChrome() {
  if (!native) return;
  await StatusBar.setStyle({ style: Style.Light }).catch(() => {});
  await StatusBar.setBackgroundColor({ color: '#08090b' }).catch(() => {});
  await StatusBar.setOverlaysWebView({ overlay: false }).catch(() => {});

  Keyboard.addListener('keyboardWillShow', info => {
    document.documentElement.classList.add('actis-keyboard-open');
    document.documentElement.style.setProperty('--actis-keyboard-height', `${info.keyboardHeight || 0}px`);
  });
  Keyboard.addListener('keyboardWillHide', () => {
    document.documentElement.classList.remove('actis-keyboard-open');
    document.documentElement.style.setProperty('--actis-keyboard-height', '0px');
  });

  App.addListener('backButton', () => {
    const modal = document.querySelector('#modalRoot .modal-backdrop');
    if (modal) { window.closeModal?.(); return; }
    const app = document.querySelector('.app');
    if (app?.classList.contains('nav-open')) { app.classList.remove('nav-open'); return; }
    if (document.querySelector('.agent-view.agent-chat-view')) {
      document.querySelector('.nav button[data-view="agents"]')?.click();
      return;
    }
    App.minimizeApp();
  });
}

async function bindNetworkState() {
  if (!native) return;
  const apply = status => {
    const conn = document.querySelector('#connection');
    if (!conn) return;
    if (!status.connected) {
      conn.textContent = 'offline';
      conn.classList.add('bad');
    } else {
      conn.classList.remove('bad');
    }
  };
  apply(await Network.getStatus());
  Network.addListener('networkStatusChange', apply);
}

async function workerDescriptor() {
  const info = await Device.getInfo();
  const id = await Device.getId();
  return {
    id: `android-${id.identifier}`,
    platform: 'android',
    model: info.model,
    osVersion: info.osVersion,
    capabilities: [
      'actis.core.embedded', 'android.device', 'android.share', 'android.clipboard',
      'android.files', 'android.camera', 'android.notifications',
    ],
  };
}

window.ACTIS_NATIVE = {
  native,
  coreUrl: LOCAL_CORE,
  workerDescriptor,
  testCore,
  async shareText(text, title = 'ACTIS GEN') {
    return Share.share({ title, text: String(text || '') });
  },
  async copyText(text) {
    return Clipboard.write({ string: String(text || '') });
  },
  async saveText(name, text) {
    return Filesystem.writeFile({
      path: name || `actis-${Date.now()}.txt`,
      data: String(text || ''),
      directory: Directory.Data,
      encoding: Encoding.UTF8,
      recursive: true,
    });
  },
  async takePhoto() {
    return Camera.getPhoto({ quality: 82, resultType: CameraResultType.Uri, source: CameraSource.Prompt });
  },
  async notify(title, body) {
    await LocalNotifications.requestPermissions();
    return LocalNotifications.schedule({ notifications: [{ id: Date.now() % 2147483647, title, body }] });
  },
};

let recovering = false;
window.ACTIS_MOBILE = {
  onApiError() {
    if (!native || recovering) return;
    recovering = true;
    waitForEmbeddedCore(20000).finally(() => { recovering = false; });
  },
};

async function init() {
  document.documentElement.classList.add('actis-mobile-runtime');
  createMobileNav();
  await bindNativeChrome();
  await bindNetworkState();

  window.ACTIS_API_BASE = LOCAL_CORE;
  localStorage.setItem('actis.api.base', LOCAL_CORE);
  window.actisSetApiBase?.(LOCAL_CORE);

  if (native) await waitForEmbeddedCore();

  document.querySelector('#connection')?.addEventListener('click', async () => {
    const ok = await testCore().catch(() => false);
    const conn = document.querySelector('#connection');
    if (conn) conn.textContent = ok ? 'core local' : 'core iniciando';
    if (!ok) waitForEmbeddedCore(20000);
  });
  document.querySelector('#refresh')?.addEventListener('click', () => {
    if (native) Haptics.impact({ style: ImpactStyle.Light }).catch(() => {});
  });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
else init();
