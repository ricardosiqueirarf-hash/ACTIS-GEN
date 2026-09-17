(() => {
  'use strict';

  const DASHBOARD = 'http://127.0.0.1:20128/dashboard';
  const FIX_COMMAND = [
    "mkdir -p ~/.termux",
    "touch ~/.termux/termux.properties",
    "grep -q '^allow-external-apps=true$' ~/.termux/termux.properties || echo 'allow-external-apps=true' >> ~/.termux/termux.properties",
    "termux-reload-settings",
  ].join(' && ');

  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  async function portAlive(timeoutMs = 1800) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      // no-cors is intentional: for this check we only care whether a TCP/HTTP
      // service answers on localhost. Auth/CORS are validated later by /models.
      await fetch(`${DASHBOARD}?actis_probe=${Date.now()}`, {
        mode: 'no-cors',
        cache: 'no-store',
        signal: controller.signal,
      });
      return true;
    } catch {
      return false;
    } finally {
      clearTimeout(timer);
    }
  }

  function els() {
    return {
      setup: document.getElementById('actisRouterSetup'),
      dashboard: document.getElementById('actisRouterDashboard'),
      start: document.getElementById('actisRouterStartLocal'),
      label: document.getElementById('actisRouterLocalLabel'),
      state: document.getElementById('actisRouterLocalState'),
      help: document.getElementById('actisTermuxHelp'),
      command: document.getElementById('actisTermuxCommand'),
      result: document.getElementById('actisRouterResult'),
    };
  }

  function setOffline(message = '9Router parado · porta 20128 fechada') {
    const { label, state, help, command } = els();
    if (label) label.textContent = message;
    if (state) {
      state.textContent = 'OFF';
      state.classList.remove('ok');
    }
    if (command) command.textContent = FIX_COMMAND;
    if (help) help.hidden = false;
  }

  function setOnline() {
    const { label, state, help } = els();
    if (label) label.textContent = '9Router respondendo em 127.0.0.1:20128';
    if (state) {
      state.textContent = 'ON';
      state.classList.add('ok');
    }
    if (help) help.hidden = true;
  }

  function result(text, ok = false) {
    const box = els().result;
    if (!box) return;
    box.textContent = text;
    box.classList.toggle('ok', ok);
  }

  async function refreshHealth({ quiet = true } = {}) {
    const alive = await portAlive();
    if (alive) setOnline();
    else {
      setOffline();
      if (!quiet) result('O Termux está instalado, mas o 9Router não iniciou. Faça a autorização abaixo no Termux e depois toque novamente em “Ativar 9Router local”.');
    }
    return alive;
  }

  async function bind() {
    const { setup, dashboard, start } = els();
    if (!setup || setup.dataset.routerGuardBound === '1') return false;
    setup.dataset.routerGuardBound = '1';

    // Never open Chrome to a dead localhost service. The previous build opened it
    // just because Termux was installed, which produced ERR_CONNECTION_REFUSED.
    if (dashboard) dashboard.addEventListener('click', async event => {
      event.preventDefault();
      event.stopImmediatePropagation();
      result('Verificando o 9Router local…');
      if (await refreshHealth({ quiet: false })) {
        result('9Router local online. Abrindo painel…', true);
        window.open(DASHBOARD, '_blank', 'noopener');
      }
    }, true);

    // Let the normal ACTIS start handler run, then verify the actual port instead
    // of trusting that the RUN_COMMAND intent was merely accepted by Android.
    if (start) start.addEventListener('click', async () => {
      for (let i = 0; i < 10; i += 1) {
        await sleep(i < 4 ? 1500 : 2500);
        if (await refreshHealth()) {
          result('9Router iniciou de verdade. Agora busque os modelos.', true);
          return;
        }
      }
      setOffline('Termux disponível · 9Router não iniciou');
      result('O comando não chegou a iniciar o servidor. No Termux, execute a autorização mostrada abaixo. No Android, confirme também: ACTIS GEN → Permissões → Permissões adicionais → “Executar comandos no ambiente Termux”.');
    }, false);

    await refreshHealth();
    return true;
  }

  const observer = new MutationObserver(() => { bind().catch(() => {}); });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  bind().catch(() => {});

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshHealth().catch(() => {});
  });

  setInterval(() => {
    if (document.getElementById('actisRouterSetup')?.classList.contains('open')) {
      refreshHealth().catch(() => {});
    }
  }, 5000);
})();
