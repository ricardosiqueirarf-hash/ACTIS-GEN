import { Capacitor, registerPlugin } from '@capacitor/core';
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
const native = Capacitor.isNativePlatform();
const LOCAL_CORE = 'http://127.0.0.1:8765';
const TERMUX_INSTALL = "curl -fsSL 'https://raw.githubusercontent.com/ricardosiqueirarf-hash/ACTIS-GEN/feat/android-client/apps/android/termux/install.sh' | bash";
const TERMUX_START = "bash ~/ACTIS-GEN/apps/android/termux/start-core.sh";
const TERMUX_STOP = "bash ~/ACTIS-GEN/apps/android/termux/stop-core.sh";
const TERMUX_BOOTSTRAP_PERMISSION = "mkdir -p ~/.termux; touch ~/.termux/termux.properties; sed -i '/^allow-external-apps=/d' ~/.termux/termux.properties; echo 'allow-external-apps=true' >> ~/.termux/termux.properties; termux-reload-settings";
const Termux = native ? registerPlugin('ActisTermux') : null;

async function getCoreUrl() {
  const stored = await Preferences.get({ key: 'actis.core.url' });
  return normalizeBase(stored.value || localStorage.getItem('actis.api.base') || (native ? LOCAL_CORE : ''));
}

async function setCoreUrl(value) {
  const base = normalizeBase(value || (native ? LOCAL_CORE : ''));
  await Preferences.set({ key: 'actis.core.url', value: base });
  localStorage.setItem('actis.api.base', base);
  window.actisSetApiBase?.(base);
  return base;
}

async function testCore(base = LOCAL_CORE) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 3500);
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

async function termuxStatus() {
  if (!native || !Termux) return { installed: false, permission: 'unavailable' };
  try { return await Termux.status(); }
  catch { return { installed: false, permission: 'unknown' }; }
}

async function runTermux(script, background = true) {
  if (!Termux) throw new Error('Bridge do Termux indisponível.');
  return Termux.runCommand({ script, background });
}

async function waitForLocalCore(onTick, timeoutMs = 45000) {
  const started = Date.now();
  let attempt = 0;
  while (Date.now() - started < timeoutMs) {
    attempt += 1;
    try {
      await testCore(LOCAL_CORE);
      return true;
    } catch {}
    onTick?.(attempt);
    await new Promise(resolve => setTimeout(resolve, 1200));
  }
  return false;
}

function setupMarkup(reason = '') {
  return `<div class="actis-mobile-setup-card">
    <div class="actis-mobile-setup-kicker">ACTIS ANDROID · CORE LOCAL</div>
    <h2>ACTIS Core no próprio celular</h2>
    <p>${reason || 'O APK usa o Termux como runtime local. Depois de instalado, o ACTIS funciona sem depender do computador.'}</p>

    <div class="actis-core-local-row">
      <div><span>CORE</span><strong>127.0.0.1:8765</strong></div>
      <b id="actisLocalCoreState" class="actis-core-state">verificando…</b>
    </div>

    <div class="actis-mobile-setup-actions local-actions">
      <button class="primary" id="actisCoreStart">Iniciar Core</button>
      <button id="actisCoreInstall">Instalar no Termux</button>
    </div>

    <div class="actis-termux-first">
      <strong>Primeira configuração</strong>
      <p>Uma única vez, o Termux precisa permitir comandos enviados pelo ACTIS. Copie o comando abaixo, abra o Termux e execute.</p>
      <code>${TERMUX_BOOTSTRAP_PERMISSION.replaceAll('<','&lt;').replaceAll('>','&gt;')}</code>
      <div class="actis-mobile-setup-actions">
        <button id="actisCopyTermuxSetup">Copiar comando</button>
        <button id="actisOpenTermux">Abrir Termux</button>
      </div>
      <button class="actis-wide-btn" id="actisTermuxPermission">Conceder permissão ao ACTIS</button>
    </div>

    <details class="actis-remote-core">
      <summary>Usar Core remoto</summary>
      <label>Endereço do Core</label>
      <input id="actisCoreUrl" inputmode="url" value="${LOCAL_CORE}" placeholder="http://192.168.0.10:8765">
      <div class="actis-mobile-setup-actions">
        <button id="actisCoreCancel">Fechar</button>
        <button id="actisCoreConnect">Salvar remoto</button>
      </div>
    </details>

    <div id="actisCoreResult" class="actis-mobile-setup-result"></div>
  </div>`;
}

async function openCoreSetup(reason = '') {
  let host = document.getElementById('actisMobileSetup');
  if (!host) {
    host = document.createElement('div');
    host.id = 'actisMobileSetup';
    host.className = 'actis-mobile-setup';
    document.body.appendChild(host);
  }
  host.innerHTML = setupMarkup(reason);
  host.classList.add('open');

  const result = host.querySelector('#actisCoreResult');
  const state = host.querySelector('#actisLocalCoreState');
  const input = host.querySelector('#actisCoreUrl');
  const saved = await getCoreUrl();
  input.value = saved || LOCAL_CORE;

  const show = (text, ok = false) => {
    result.textContent = text;
    result.classList.toggle('ok', ok);
  };

  try {
    await testCore(LOCAL_CORE);
    state.textContent = 'online';
    state.classList.add('ok');
    show('Core local já está ativo.', true);
  } catch {
    state.textContent = 'offline';
    state.classList.remove('ok');
  }

  host.querySelector('#actisCopyTermuxSetup').onclick = async () => {
    await Clipboard.write({ string: TERMUX_BOOTSTRAP_PERMISSION });
    show('Comando copiado. Cole no Termux e pressione Enter.', true);
  };

  host.querySelector('#actisOpenTermux').onclick = async () => {
    try { await Termux.openTermux(); }
    catch { show('Termux não encontrado. Instale o Termux e volte ao ACTIS.'); }
  };

  host.querySelector('#actisTermuxPermission').onclick = async () => {
    try {
      const status = await termuxStatus();
      if (!status.installed) throw new Error('Termux não está instalado.');
      const permission = await Termux.requestRunPermission();
      show(permission.granted ? 'Permissão concedida.' : 'Permissão não concedida.', !!permission.granted);
    } catch (error) {
      show(error.message || String(error));
    }
  };

  host.querySelector('#actisCoreInstall').onclick = async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const status = await termuxStatus();
      if (!status.installed) throw new Error('Termux não está instalado. Abra o Termux primeiro.');
      show('Instalação iniciada no Termux. Ela pode levar alguns minutos…', true);
      await runTermux(TERMUX_INSTALL, false);
      const ready = await waitForLocalCore(attempt => {
        state.textContent = `instalando ${attempt}`;
      }, 240000);
      if (!ready) throw new Error('A instalação ainda não terminou. Abra o Termux para ver o progresso.');
      state.textContent = 'online';
      state.classList.add('ok');
      await setCoreUrl(LOCAL_CORE);
      show('Core instalado e online. Abrindo ACTIS…', true);
      setTimeout(() => location.reload(), 350);
    } catch (error) {
      button.disabled = false;
      state.textContent = 'offline';
      show(error.message || String(error));
    }
  };

  host.querySelector('#actisCoreStart').onclick = async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await runTermux(TERMUX_START, true);
      show('Iniciando Core local…', true);
      const ready = await waitForLocalCore(attempt => { state.textContent = `iniciando ${attempt}`; });
      if (!ready) throw new Error('Core não iniciou. Se ainda não foi instalado, use “Instalar no Termux”.');
      state.textContent = 'online';
      state.classList.add('ok');
      await setCoreUrl(LOCAL_CORE);
      show('Core online. Abrindo ACTIS…', true);
      setTimeout(() => location.reload(), 300);
    } catch (error) {
      button.disabled = false;
      state.textContent = 'offline';
      show(error.message || String(error));
    }
  };

  host.querySelector('#actisCoreCancel').onclick = () => host.classList.remove('open');
  host.querySelector('#actisCoreConnect').onclick = async () => {
    const value = normalizeBase(input.value);
    if (!value) return;
    try {
      show('Testando Core remoto…');
      await testCore(value);
      await setCoreUrl(value);
      show('Core encontrado. Abrindo ACTIS…', true);
      setTimeout(() => location.reload(), 300);
    } catch (error) {
      show(`Não foi possível conectar: ${error.message}`);
    }
  };
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
    const setup = document.getElementById('actisMobileSetup');
    if (setup?.classList.contains('open')) { setup.classList.remove('open'); return; }
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
    capabilities: ['android.device','android.share','android.clipboard','android.files','android.camera','android.notifications','actis.core.local'],
  };
}

window.ACTIS_NATIVE = {
  native,
  getCoreUrl,
  setCoreUrl,
  openCoreSetup,
  workerDescriptor,
  termuxStatus,
  startLocalCore: () => runTermux(TERMUX_START, true),
  stopLocalCore: () => runTermux(TERMUX_STOP, true),
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

let coreSetupOpening = false;
window.ACTIS_MOBILE = {
  onApiError(error, base) {
    if (!native || coreSetupOpening) return;
    coreSetupOpening = true;
    const local = !base || normalizeBase(base) === LOCAL_CORE;
    const reason = local
      ? 'O Core local está parado ou ainda não foi instalado no Termux.'
      : `O Core em ${base} não respondeu. Você pode voltar ao Core local do celular.`;
    openCoreSetup(reason).catch(() => {}).finally(() => { coreSetupOpening = false; });
  },
};

async function init() {
  document.documentElement.classList.add('actis-mobile-runtime');
  createMobileNav();
  await bindNativeChrome();
  await bindNetworkState();

  const saved = await getCoreUrl();
  if (saved !== window.ACTIS_API_BASE) window.actisSetApiBase?.(saved);

  if (native) {
    try {
      await testCore(saved || LOCAL_CORE);
      if (!localStorage.getItem('actis.api.base')) await setCoreUrl(saved || LOCAL_CORE);
    } catch {
      await openCoreSetup('O Core local está parado ou ainda não foi instalado no Termux.');
    }
  }

  document.querySelector('#connection')?.addEventListener('click', () => openCoreSetup());
  document.querySelector('#refresh')?.addEventListener('click', () => {
    if (native) Haptics.impact({ style: ImpactStyle.Light }).catch(() => {});
  });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
else init();
