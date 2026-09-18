import { App } from '@capacitor/app';
import { Camera, CameraResultType, CameraSource } from '@capacitor/camera';
import { Clipboard } from '@capacitor/clipboard';
import { Device } from '@capacitor/device';
import { Directory, Encoding, Filesystem } from '@capacitor/filesystem';
import { Haptics, ImpactStyle } from '@capacitor/haptics';
import { Keyboard } from '@capacitor/keyboard';
import { LocalNotifications } from '@capacitor/local-notifications';
import { Network } from '@capacitor/network';
import { Preferences } from '@capacitor/preferences';
import { Share } from '@capacitor/share';
import { StatusBar, Style } from '@capacitor/status-bar';

const normalizeBase = value => String(value || '').trim().replace(/\/$/, '');
const native = !!window.Capacitor?.isNativePlatform?.();

async function getCoreUrl() {
  const stored = await Preferences.get({ key: 'actis.core.url' });
  return normalizeBase(stored.value || localStorage.getItem('actis.api.base') || '');
}

async function setCoreUrl(value) {
  const base = normalizeBase(value);
  await Preferences.set({ key: 'actis.core.url', value: base });
  window.actisSetApiBase?.(base);
  return base;
}
function setupMarkup(base = '', reason = '') {
  return `<div class="actis-mobile-setup-card">
    <div class="actis-mobile-setup-kicker">ACTIS ANDROID</div>
    <h2>Conectar ao ACTIS Core</h2>
    <p>${reason || 'Informe o endereço do computador/servidor que executa o ACTIS GEN.'}</p>
    <label>Endereço do Core</label>
    <input id="actisCoreUrl" inputmode="url" value="${base}" placeholder="http://192.168.0.10:8765">
    <div class="actis-mobile-setup-hint">Em emulador Android, normalmente use <code>http://10.0.2.2:8765</code>.</div>
    <div class="actis-mobile-setup-actions">
      <button id="actisCoreCancel">Agora não</button>
      <button class="primary" id="actisCoreConnect">Salvar e conectar</button>
    </div>
    <div id="actisCoreResult" class="actis-mobile-setup-result"></div>
  </div>`;
}

async function testCore(base) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch(`${base}/api/agents`, { credentials: 'include', signal: controller.signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const body = await response.json();
    if (!Array.isArray(body.agents)) throw new Error('Resposta inválida do ACTIS Core');
    return true;
  } finally {
    clearTimeout(timeout);
  }
}
async function openCoreSetup(reason = '') {
  let host = document.getElementById('actisMobileSetup');
  if (!host) {
    host = document.createElement('div');
    host.id = 'actisMobileSetup';
    host.className = 'actis-mobile-setup';
    document.body.appendChild(host);
  }
  const base = await getCoreUrl();
  host.innerHTML = setupMarkup(base, reason);
  host.classList.add('open');
  const input = host.querySelector('#actisCoreUrl');
  const result = host.querySelector('#actisCoreResult');
  host.querySelector('#actisCoreCancel').onclick = () => host.classList.remove('open');
  host.querySelector('#actisCoreConnect').onclick = async event => {
    const button = event.currentTarget;
    const value = normalizeBase(input.value);
    if (!value) return;
    button.disabled = true;
    button.textContent = 'Testando…';
    result.textContent = '';
    try {
      await testCore(value);
      await setCoreUrl(value);
      result.textContent = 'Core encontrado. Reabrindo ACTIS…';
      result.classList.add('ok');
      setTimeout(() => location.reload(), 250);
    } catch (error) {
      result.textContent = `Não foi possível conectar: ${error.message}`;
      result.classList.remove('ok');
      button.disabled = false;
      button.textContent = 'Salvar e conectar';
    }
  };
  setTimeout(() => input?.focus(), 120);
}
function createMobileNav() {
  if (document.getElementById('actisMobileNav')) return;
  const nav = document.createElement('nav');
  nav.id = 'actisMobileNav';
  nav.className = 'actis-mobile-nav';
  nav.innerHTML = `
    <button data-mobile-view="world"><span>⌘</span><b>World</b></button>
    <button data-mobile-view="agents"><span>◇</span><b>Agentes</b></button>
    <button data-mobile-view="channels"><span>▱</span><b>Canais</b></button>
    <button data-mobile-view="connectors"><span>⌁</span><b>Config</b></button>
    <button id="actisMobileMore"><span>☰</span><b>Mais</b></button>`;
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
    if (document.querySelector('.agent-view.agent-chat-view')) { document.querySelector('.nav button[data-view="agents"]')?.click(); return; }
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
    capabilities: ['android.device','android.share','android.clipboard','android.files','android.camera','android.notifications'],
  };
}
window.ACTIS_NATIVE = {
  native,
  getCoreUrl,
  setCoreUrl,
  openCoreSetup,
  workerDescriptor,
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
window.ACTIS_MOBILE = {
  onApiError(error, base) {
    if (!native) return;
    const reason = base
      ? `O Core em ${base} não respondeu. Verifique a rede ou altere o endereço.`
      : 'Este aparelho ainda não está conectado a um ACTIS Core.';
    openCoreSetup(reason).catch(() => {});
  },
};

async function init() {
  document.documentElement.classList.add('actis-mobile-runtime');
  createMobileNav();
  await bindNativeChrome();
  await bindNetworkState();

  const saved = await getCoreUrl();
  if (saved && saved !== window.ACTIS_API_BASE) window.actisSetApiBase?.(saved);
  if (native && !saved) await openCoreSetup();

  document.querySelector('#connection')?.addEventListener('click', () => openCoreSetup());
  document.querySelector('#refresh')?.addEventListener('click', () => {
    if (native) Haptics.impact({ style: ImpactStyle.Light }).catch(() => {});
  });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
else init();
