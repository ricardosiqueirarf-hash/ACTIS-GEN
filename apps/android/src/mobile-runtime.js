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
import { Share } from '@capacitor/share';
import { StatusBar, Style } from '@capacitor/status-bar';

const native = Capacitor.isNativePlatform();
const ActisCore = native ? registerPlugin('ActisCore') : null;
const ActisTermux = native ? registerPlugin('ActisTermux') : null;
const LOCAL_CORE = 'http://127.0.0.1:8765';
const LOCAL_9ROUTER = 'http://127.0.0.1:20128/v1';
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

async function coreJson(path, options = {}) {
  const response = await fetch(`${LOCAL_CORE}${path}`, {
    cache: 'no-store',
    credentials: 'include',
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(String(value.error || `HTTP ${response.status}`));
  return value;
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
      <pre id="actisBootError" hidden></pre>
      <div class="actis-boot-track"><i></i></div>
      <button id="actisBootCopy" hidden>Copiar erro</button>
      <button id="actisBootRetry" hidden>Tentar novamente</button>
    </div>`;
  document.body.appendChild(overlay);
  return overlay;
}

function compactBootError(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  const lines = raw.split('\n').map(x => x.trim()).filter(Boolean);
  return lines.slice(-8).join('\n').slice(-2200);
}

async function copyBootError(text, button) {
  const value = String(text || '').trim();
  if (!value) return;
  try {
    if (native) await Clipboard.write({ string: value });
    else if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(value);
    else throw new Error('Clipboard indisponível');
    button.textContent = 'Erro copiado';
  } catch {
    button.textContent = 'Falha ao copiar';
  }
  setTimeout(() => { button.textContent = 'Copiar erro'; }, 1800);
}

async function coreNativeStatus() {
  if (!native || !ActisCore) return { state: 'web', error: '' };
  try { return await ActisCore.status(); }
  catch { return { state: 'unknown', error: '' }; }
}

async function waitForEmbeddedCore(timeoutMs = 45000) {
  const overlay = ensureBootOverlay();
  const text = overlay.querySelector('#actisBootText');
  const errorBox = overlay.querySelector('#actisBootError');
  const copy = overlay.querySelector('#actisBootCopy');
  const retry = overlay.querySelector('#actisBootRetry');
  overlay.classList.add('open');
  overlay.classList.remove('ready', 'failed');
  retry.hidden = true;
  copy.hidden = true;
  errorBox.hidden = true;
  errorBox.textContent = '';

  if (native && ActisCore) await ActisCore.ensureStarted().catch(() => {});

  const started = Date.now();
  let attempt = 0;
  let lastFetchError = '';
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
    } catch (error) {
      lastFetchError = String(error?.message || error || 'Falha ao conectar ao Core local');
    }

    if (attempt === 3) text.textContent = 'Carregando runtime Python…';
    if (attempt === 8) text.textContent = 'Inicializando banco e serviços locais…';
    if (attempt === 15) text.textContent = 'Primeira inicialização pode demorar um pouco mais…';
    if (attempt % 5 === 0) {
      const status = await coreNativeStatus();
      if (status?.state === 'failed') break;
      if (status?.state === 'python-ready') text.textContent = 'Python pronto. Carregando ACTIS Core…';
      if (status?.state === 'core-loading') text.textContent = 'Core carregado. Abrindo banco e servidor local…';
    }
    await sleep(900);
  }

  const status = await coreNativeStatus();
  const fullDetail = String(status?.error || lastFetchError || '').trim();
  const detail = compactBootError(fullDetail);
  overlay.classList.add('failed');
  text.textContent = `Falha ao iniciar o Core embutido (${status?.state || 'unknown'}).`;
  if (detail) {
    errorBox.hidden = false;
    errorBox.textContent = detail;
  }
  if (fullDetail) {
    copy.hidden = false;
    copy.onclick = () => copyBootError(fullDetail, copy);
  }
  retry.hidden = false;
  retry.onclick = async () => {
    retry.hidden = true;
    copy.hidden = true;
    if (native && ActisCore) await ActisCore.ensureStarted().catch(() => {});
    waitForEmbeddedCore(timeoutMs);
  };
  return false;
}

function createRouterStatusChip() {
  let chip = document.getElementById('actisRouterStatus');
  if (chip) return chip;
  chip = document.createElement('button');
  chip.id = 'actisRouterStatus';
  chip.type = 'button';
  chip.className = 'actis-router-status';
  chip.textContent = '9R off';
  chip.onclick = () => showRouterSetup(true);
  const top = document.querySelector('.top');
  if (top) top.appendChild(chip);
  else document.body.appendChild(chip);
  return chip;
}

function setRouterStatus(state, label = '') {
  const chip = createRouterStatusChip();
  chip.classList.toggle('ok', state === 'ok');
  chip.classList.toggle('busy', state === 'busy');
  chip.textContent = label || (state === 'ok' ? '9R on' : state === 'busy' ? '9R…' : '9R off');
}

function ensureRouterSetup() {
  let overlay = document.getElementById('actisRouterSetup');
  if (overlay) return overlay;
  overlay = document.createElement('div');
  overlay.id = 'actisRouterSetup';
  overlay.className = 'actis-mobile-setup';
  overlay.innerHTML = `
    <div class="actis-mobile-setup-card">
      <div class="actis-mobile-setup-kicker">9ROUTER · IA DO ACTIS</div>
      <h2>Ativar os agentes</h2>
      <p>O Core já está no celular. Falta ligar um gateway de IA. O modo recomendado usa o 9Router no próprio Termux, sem depender do desktop.</p>

      <div class="actis-core-local-row">
        <div><span>TERMUX / 9ROUTER LOCAL</span><strong id="actisRouterLocalLabel">Verificando…</strong></div>
        <b id="actisRouterLocalState" class="actis-core-state">OFF</b>
      </div>
      <div class="actis-mobile-setup-actions local-actions">
        <button id="actisRouterStartLocal">Ativar 9Router local</button>
        <button id="actisRouterDashboard">Abrir painel</button>
      </div>

      <label for="actisRouterUrl">Endpoint</label>
      <input id="actisRouterUrl" value="${LOCAL_9ROUTER}" autocomplete="off" spellcheck="false">
      <div class="actis-mobile-setup-hint">Local: <code>${LOCAL_9ROUTER}</code>. Para um 9Router remoto, informe a URL HTTPS terminando em <code>/v1</code>.</div>

      <label for="actisRouterKey">API key do 9Router</label>
      <input id="actisRouterKey" type="password" autocomplete="off" placeholder="Cole a chave do painel 9Router">

      <label for="actisRouterModel">Modelo</label>
      <input id="actisRouterModel" list="actisRouterModels" autocomplete="off" placeholder="Buscar modelos disponíveis">
      <datalist id="actisRouterModels"></datalist>

      <div class="actis-mobile-setup-actions">
        <button id="actisRouterModelsBtn">Buscar modelos</button>
        <button id="actisRouterSaveBtn">Testar e salvar</button>
      </div>

      <div id="actisRouterResult" class="actis-mobile-setup-result"></div>

      <div class="actis-termux-first" id="actisTermuxHelp" hidden>
        <strong>Primeira autorização do Termux</strong>
        <p>Se o Android bloquear a automação externa, rode uma vez no Termux e volte ao ACTIS:</p>
        <code id="actisTermuxCommand">mkdir -p ~/.termux; echo 'allow-external-apps=true' >> ~/.termux/termux.properties; termux-reload-settings</code>
        <button id="actisCopyTermux" class="actis-wide-btn">Copiar comando</button>
        <button id="actisOpenTermux" class="actis-wide-btn">Abrir Termux</button>
      </div>

      <details class="actis-remote-core">
        <summary>Usar 9Router remoto</summary>
        <div class="actis-mobile-setup-hint">Pode ser um 9Router em VPS/Hugging Face/rede privada. Tokens e API keys ficam somente no armazenamento privado deste app e não são enviados ao GitHub.</div>
      </details>
      <button id="actisRouterClose" class="actis-wide-btn">Fechar</button>
    </div>`;
  document.body.appendChild(overlay);

  overlay.querySelector('#actisRouterClose').onclick = () => overlay.classList.remove('open');
  overlay.addEventListener('click', event => { if (event.target === overlay) overlay.classList.remove('open'); });
  overlay.querySelector('#actisRouterDashboard').onclick = () => window.open('http://127.0.0.1:20128/dashboard', '_blank', 'noopener');
  overlay.querySelector('#actisRouterStartLocal').onclick = () => startLocal9Router(true);
  overlay.querySelector('#actisRouterModelsBtn').onclick = () => refreshRouterModels(true);
  overlay.querySelector('#actisRouterSaveBtn').onclick = () => saveAndProbeRouter();
  overlay.querySelector('#actisCopyTermux').onclick = async event => {
    const command = overlay.querySelector('#actisTermuxCommand').textContent;
    await copyBootError(command, event.currentTarget);
  };
  overlay.querySelector('#actisOpenTermux').onclick = () => ActisTermux?.openTermux().catch(() => {});
  return overlay;
}

function routerFields() {
  const overlay = ensureRouterSetup();
  return {
    overlay,
    url: overlay.querySelector('#actisRouterUrl'),
    key: overlay.querySelector('#actisRouterKey'),
    model: overlay.querySelector('#actisRouterModel'),
    models: overlay.querySelector('#actisRouterModels'),
    result: overlay.querySelector('#actisRouterResult'),
    localLabel: overlay.querySelector('#actisRouterLocalLabel'),
    localState: overlay.querySelector('#actisRouterLocalState'),
    help: overlay.querySelector('#actisTermuxHelp'),
  };
}

function routerMessage(text, ok = false) {
  const { result } = routerFields();
  result.textContent = String(text || '');
  result.classList.toggle('ok', ok);
}

async function loadRouterConfig() {
  const { provider = {} } = await coreJson('/api/android-provider');
  const fields = routerFields();
  fields.url.value = provider.base_url || LOCAL_9ROUTER;
  fields.model.value = provider.model || '';
  // Never echo the stored secret back into the DOM. Empty means "keep existing"
  // when the user is only changing endpoint/model.
  fields.key.value = '';
  return provider;
}

async function saveRouterProvider({ model = null } = {}) {
  const fields = routerFields();
  const current = await coreJson('/api/android-provider').then(x => x.provider || {}).catch(() => ({}));
  const body = {
    base_url: String(fields.url.value || LOCAL_9ROUTER).trim(),
    api_key: String(fields.key.value || '').trim() || (current.has_api_key ? '__KEEP__' : ''),
    model: model === null ? String(fields.model.value || '').trim() : String(model || '').trim(),
  };
  const response = await coreJson('/api/android-provider', { method: 'POST', body: JSON.stringify(body) });
  fields.key.value = '';
  return response.provider || {};
}

async function refreshRouterModels(interactive = false) {
  const fields = routerFields();
  if (interactive) routerMessage('Consultando /models do 9Router…');
  setRouterStatus('busy');
  try {
    await saveRouterProvider({ model: String(fields.model.value || '').trim() });
    const { models = [] } = await coreJson('/api/models');
    fields.models.innerHTML = '';
    for (const id of models) {
      const option = document.createElement('option');
      option.value = id;
      fields.models.appendChild(option);
    }
    if (!fields.model.value && models.length === 1) fields.model.value = models[0];
    if (!models.length) throw new Error('O 9Router respondeu sem modelos. Abra o painel e conecte um provider, ou confira a API key.');
    if (interactive) routerMessage(`${models.length} modelo(s) encontrado(s). Escolha um e toque em “Testar e salvar”.`, true);
    setRouterStatus('busy', '9R modelo');
    return models;
  } catch (error) {
    if (interactive) routerMessage(String(error?.message || error));
    setRouterStatus('off');
    throw error;
  }
}

async function saveAndProbeRouter() {
  const fields = routerFields();
  const model = String(fields.model.value || '').trim();
  if (!model) {
    routerMessage('Escolha ou informe um modelo antes de testar.');
    return false;
  }
  routerMessage('Salvando e executando probe real de chat…');
  setRouterStatus('busy');
  try {
    await saveRouterProvider({ model });
    const probe = await coreJson('/api/model-probe', {
      method: 'POST',
      body: JSON.stringify({ model }),
    });
    if (!probe.ok) throw new Error(`O modelo respondeu, mas falhou no probe: ${probe.text || 'resposta inesperada'}`);
    routerMessage(`IA pronta. ${probe.model || model} respondeu ao probe real.`, true);
    setRouterStatus('ok');
    await sleep(650);
    fields.overlay.classList.remove('open');
    return true;
  } catch (error) {
    routerMessage(String(error?.message || error));
    setRouterStatus('off');
    return false;
  }
}

async function termuxStatus() {
  if (!native || !ActisTermux) return { installed: false, permission: 'unavailable' };
  return ActisTermux.status().catch(() => ({ installed: false, permission: 'unknown' }));
}

async function startLocal9Router(interactive = false) {
  const fields = routerFields();
  const status = await termuxStatus();
  fields.help.hidden = true;
  if (!status.installed) {
    fields.localLabel.textContent = 'Termux não instalado';
    fields.localState.textContent = 'OFF';
    if (interactive) routerMessage('Instale o Termux para rodar o 9Router no próprio celular, ou use um endpoint remoto.');
    return false;
  }

  fields.localLabel.textContent = 'Preparando runtime Node/9Router';
  fields.localState.textContent = '…';
  fields.localState.classList.remove('ok');
  setRouterStatus('busy');
  if (interactive) routerMessage('Iniciando o 9Router no Termux. Na primeira vez ele pode instalar Node e o pacote 9router…');

  const script = [
    'set -e',
    'export DEBIAN_FRONTEND=noninteractive',
    'command -v node >/dev/null 2>&1 || (pkg update -y && pkg install -y nodejs)',
    'command -v 9router >/dev/null 2>&1 || npm install -g 9router',
    "if ! pgrep -f '[9]router' >/dev/null 2>&1; then nohup 9router > \"$HOME/actis-9router.log\" 2>&1 & fi",
  ].join('; ');

  try {
    await ActisTermux.requestRunPermission().catch(() => ({}));
    await ActisTermux.runCommand({ script, background: true });
  } catch (error) {
    fields.help.hidden = false;
    fields.localLabel.textContent = 'Termux precisa de autorização';
    fields.localState.textContent = 'AÇÃO';
    if (interactive) routerMessage(String(error?.message || error));
    setRouterStatus('off');
    return false;
  }

  fields.url.value = LOCAL_9ROUTER;
  await saveRouterProvider({ model: String(fields.model.value || '').trim() }).catch(() => {});
  for (let attempt = 0; attempt < 24; attempt += 1) {
    await sleep(attempt < 6 ? 1000 : 1800);
    try {
      const { models = [] } = await coreJson('/api/models');
      if (models.length) {
        fields.localLabel.textContent = `Online · ${models.length} modelo(s)`;
        fields.localState.textContent = 'ON';
        fields.localState.classList.add('ok');
        fields.models.innerHTML = models.map(id => `<option value="${String(id).replace(/"/g, '&quot;')}"></option>`).join('');
        if (!fields.model.value && models.length === 1) fields.model.value = models[0];
        routerMessage('9Router local online. Escolha um modelo e faça o teste final.', true);
        setRouterStatus('busy', '9R modelo');
        return true;
      }
    } catch {}
  }

  fields.localLabel.textContent = 'Servidor iniciado; provider ainda não conectado';
  fields.localState.textContent = 'SETUP';
  routerMessage('O processo foi enviado ao Termux, mas ainda não há modelos disponíveis. Abra o painel do 9Router, conecte um provider e depois toque em “Buscar modelos”.');
  setRouterStatus('off');
  return false;
}

async function probeConfiguredRouter() {
  try {
    const { provider = {} } = await coreJson('/api/android-provider');
    if (!provider.configured || !provider.model) return false;
    const { models = [] } = await coreJson('/api/models');
    return models.length > 0;
  } catch {
    return false;
  }
}

async function showRouterSetup(force = false) {
  const overlay = ensureRouterSetup();
  const fields = routerFields();
  await loadRouterConfig().catch(() => {});
  const status = await termuxStatus();
  fields.localLabel.textContent = status.installed ? 'Termux disponível' : 'Termux não encontrado';
  fields.localState.textContent = status.installed ? 'PRONTO' : 'OFF';
  fields.localState.classList.toggle('ok', Boolean(status.installed));
  if (force || !(await probeConfiguredRouter())) overlay.classList.add('open');
  return overlay;
}

async function ensureRouterReady() {
  createRouterStatusChip();
  const ready = await probeConfiguredRouter();
  if (ready) {
    setRouterStatus('ok');
    return true;
  }

  const provider = await coreJson('/api/android-provider').then(x => x.provider || {}).catch(() => ({}));
  if (String(provider.base_url || '').startsWith('http://127.0.0.1:20128')) {
    const status = await termuxStatus();
    if (status.installed) {
      await startLocal9Router(false).catch(() => false);
      if (await probeConfiguredRouter()) {
        setRouterStatus('ok');
        return true;
      }
    }
  }
  setRouterStatus('off');
  await showRouterSetup(true);
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
      if (view === 'connectors') createRouterStatusChip();
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
    const routerSetup = document.getElementById('actisRouterSetup');
    if (routerSetup?.classList.contains('open')) { routerSetup.classList.remove('open'); return; }
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
      'android.files', 'android.camera', 'android.notifications', 'ninerouter.local.termux',
    ],
  };
}

window.ACTIS_NATIVE = {
  native,
  coreUrl: LOCAL_CORE,
  workerDescriptor,
  testCore,
  coreStatus: coreNativeStatus,
  openRouterSetup: () => showRouterSetup(true),
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

  const coreReady = native ? await waitForEmbeddedCore() : true;
  if (coreReady) await ensureRouterReady().catch(() => showRouterSetup(true));

  document.querySelector('#connection')?.addEventListener('click', async () => {
    const ok = await testCore().catch(() => false);
    const conn = document.querySelector('#connection');
    if (conn) conn.textContent = ok ? 'core local' : 'core iniciando';
    if (!ok) waitForEmbeddedCore(20000);
    else showRouterSetup(true);
  });
  document.querySelector('#refresh')?.addEventListener('click', () => {
    if (native) Haptics.impact({ style: ImpactStyle.Light }).catch(() => {});
  });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
else init();
