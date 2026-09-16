const LOCAL_CORE = 'http://127.0.0.1:8765';

async function coreOnline() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 1400);
  try {
    const response = await fetch(`${LOCAL_CORE}/api/agents`, {
      signal: controller.signal,
      cache: 'no-store',
      credentials: 'include',
    });
    return response.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

async function autoStart() {
  const bridge = window.ACTIS_NATIVE;
  if (!bridge?.native || !bridge.termuxStatus || !bridge.startLocalCore) return;
  if (await coreOnline()) return;

  try {
    const status = await bridge.termuxStatus();
    const permission = String(status?.permission || '').toLowerCase();
    if (!status?.installed || !permission.includes('granted')) return;

    await bridge.startLocalCore();
    for (let attempt = 0; attempt < 24; attempt += 1) {
      if (await coreOnline()) {
        const current = localStorage.getItem('actis.api.base');
        if (!current || current === LOCAL_CORE) {
          await bridge.setCoreUrl?.(LOCAL_CORE);
          location.reload();
        }
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 750));
    }
  } catch {
    // A tela de setup do runtime principal assume o fallback e mostra o erro ao usuário.
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => setTimeout(autoStart, 120), { once: true });
} else {
  setTimeout(autoStart, 120);
}
