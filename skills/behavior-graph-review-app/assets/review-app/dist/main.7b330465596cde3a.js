import { mountReviewApp } from './app.f4613fade31578b1.js';
import { loadConfiguredRepositories } from './repository-config.e8161b832d84c9a6.js';

// The static app reads only public client configuration. Server-side storage
// paths, database connections and cloud credentials never belong in this file.
export async function startReviewApp({ configUrl = new URL('./config.json', import.meta.url).href, fetchImpl = globalThis.fetch, cryptoImpl = globalThis.crypto, documentImpl = globalThis.document, mount = mountReviewApp } = {}) {
  try {
    const repositories = await loadConfiguredRepositories({ configUrl, fetchImpl, cryptoImpl });
    return mount(repositories);
  } catch (error) {
    const text = (id, value) => { const element = documentImpl.getElementById(id); if (element) element.textContent = value; };
    text('connection-text', 'Configuration error');
    documentImpl.getElementById('connection-dot')?.classList.add('offline');
    text('status-pill', 'Unavailable');
    text('empty-title', 'Cannot start graph review');
    text('empty-description', error.message || 'Check config.json and reload the app.');
    documentImpl.getElementById('graph-empty')?.classList.remove('hidden');
    // No controller is mounted, so controls must not imply that writes are available.
    for (const element of documentImpl.querySelectorAll('button, input, select, textarea')) element.disabled = true;
    const alert = documentImpl.getElementById('toast-container');
    if (alert) { alert.setAttribute('role', 'alert'); alert.textContent = error.message || 'Configuration failed.'; }
    return null;
  }
}

if (typeof document !== 'undefined') startReviewApp();
