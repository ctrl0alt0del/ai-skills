import { ReviewController } from './controller.js';
import { feedbackState, pendingFeedback, feedbackOutcome } from './feedback.js';

/** Mount the review UI over graph and review repository interfaces. */
export function mountReviewApp(repositories, {
  pollInterval = 3500
} = {}) {
  const controller = new ReviewController(repositories);
  let catalog = [],
    catalogLoading = false, catalogRefreshPending = false, destroyed = false,
    catalogRequest = 0,
    graphSwitchRequest = 0,
    roundRequest = 0;
  let editId = null,
    editVersion = null,
    deleteId = null,
    deleteVersion = null;
  const $ = id => document.getElementById(id),
    NS = 'http://www.w3.org/2000/svg';
  const names = {
    behavior: 'Behavior',
    state: 'Data & state',
    event: 'Event',
    rule: 'Rule',
    requirement: 'Requirement',
    interface: 'Interface',
    configuration: 'Configuration'
  };
  const colors = {
    behavior: '#318579',
    state: '#7596b2',
    event: '#c79b50',
    rule: '#7f8e7a',
    requirement: '#82939a',
    interface: '#55949d',
    configuration: '#a08c6b'
  };
  const backgrounds = {
    behavior: '#e6f3ec',
    state: '#edf3f8',
    event: '#fff4df',
    rule: '#eff2e9',
    requirement: '#eff3f2',
    interface: '#eaf5f5',
    configuration: '#f7f1e7'
  };
  const relationshipNames = {
    configured_by: 'configured by'
  };
  let state = null,
    history = null,
    selected = {
      type: 'graph',
      id: null
    },
    activeTab = 'details',
    focus = false,
    busy = false,
    polling = false,
    offline = false,
    approvalChecked = false,
    approvalSignature = '',
    lastLayoutKey = '',
    positions = Object.create(null),
    view = {
      x: 25,
      y: 25,
      k: 1
    },
    graphSize = {
      width: 800,
      height: 500
    },
    drag = null,
    moved = false,
    draftTarget = 'graph:',
    drafts = {},
    resolveId = null,
    resolveVersion = null,
    toastTimer = null,
    visibleNodes = [],
    visibleEdges = [];
  const newEl = (tag, className, text) => {
    const e = document.createElement(tag);
    if (className) e.className = className;
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const svgEl = (tag, attrs, text) => {
    const e = document.createElementNS(NS, tag);
    Object.entries(attrs || {}).forEach(([k, v]) => e.setAttribute(k, String(v)));
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const graph = () => history ? history.graph : state && state.graph;
  const current = () => !history;
  const agentActive = () => state?.agent_active === true && !offline;
  const feedbackGuidance = () => {
    const pending = pendingNotes(),
      withdrawn = pending.filter(a => a.status === 'deleted').length;
    const count = pending.length ? `${pending.length} ${pending.length === 1 ? 'note needs' : 'notes need'} AI review${withdrawn ? `, including ${withdrawn} withdrawn ${withdrawn === 1 ? 'note' : 'notes'}` : ''}. ` : '';
    if (!state?.review_requested) return count + 'Notes are saved. Send feedback when you are ready, or ask the agent in chat to review it. Processing and settling a note are separate steps.';
    return count + (agentActive() ? 'Feedback is submitted for the connected agent. Responses and revised proposals appear here when saved.' : 'Feedback is submitted. Return to chat and say “Review my graph feedback” to resume the agent.');
  };
  const approvalGuidance = () => agentActive() ? 'This exact version is approved and saved for the connected agent. The agent can now create the final graph. Approval itself does not create files.' : 'This exact version is approved. Return to chat and ask the agent to create the final graph. Approval itself does not create files.';
  function renderPresence() {
    const el = $('agent-presence');
    if (!el) return;
    el.classList.toggle('active', agentActive());
    el.textContent = offline ? 'Agent status unavailable' : agentActive() ? 'Agent connected' : 'Agent not waiting';
    el.title = agentActive() ? 'An active agent is listening for review feedback. The browser does not invoke an AI.' : 'Your saved feedback remains available. Return to chat to resume the agent when needed.';
  }
  const targetKey = t => t.type + ':' + (t.id || '');
  const kindLabel = k => names[k] || k;
  const nice = s => String(s || '').replaceAll('_', ' ');
  const openNotes = () => state ? (state.annotations || []).filter(a => a.status === 'open') : [];
  const pendingNotes = () => pendingFeedback(state?.annotations);
  const claimCount = g => (g?.nodes || []).reduce((n, x) => n + (x.claims || []).length, 0);
  const uncertainCount = g => (g?.nodes || []).reduce((n, x) => n + (x.claims || []).filter(c => c.basis !== 'supported').length, 0) + (g?.dependencies || []).filter(d => d.basis !== 'supported').length;
  const getNode = id => (graph()?.nodes || []).find(n => n.id === id);
  const getEdge = id => (graph()?.dependencies || []).find(d => d.id === id);
  const getClaim = id => {
    for (const n of graph()?.nodes || []) {
      const c = (n.claims || []).find(c => c.id === id);
      if (c) return {
        node: n,
        claim: c
      };
    }
    return null;
  };
  const findTarget = t => t.type === 'graph' ? graph() : t.type === 'node' ? getNode(t.id) : t.type === 'dependency' ? getEdge(t.id) : getClaim(t.id);
  function targetTitle(t) {
    if (t.type === 'graph') return 'Whole graph';
    if (t.type === 'node') return getNode(t.id)?.title || t.id;
    if (t.type === 'dependency') {
      const e = getEdge(t.id);
      return e ? `${getNode(e.from)?.title || e.from} · ${nice(e.relationship)} · ${getNode(e.to)?.title || e.to}` : t.id;
    }
    const c = getClaim(t.id);
    return c ? `${c.node.title} · ${nice(c.claim.aspect)}` : t.id;
  }
  function pill(text, style = '') {
    return newEl('span', 'pill ' + style, text);
  }
  function button(text, fn, className = 'small') {
    const b = newEl('button', className, text);
    b.type = 'button';
    b.addEventListener('click', fn);
    return b;
  }
  function toast(message, isError = false, timeout = 6500) {
    clearTimeout(toastTimer);
    const t = newEl('div', 'toast' + (isError ? ' error' : '')),
      p = newEl('div', '', message),
      close = button('×', () => $('toast-container').replaceChildren(), '');
    close.setAttribute('aria-label', 'Dismiss notification');
    t.append(p, close);
    $('toast-container').replaceChildren(t);
    toastTimer = setTimeout(() => $('toast-container').replaceChildren(), timeout);
  }
  function setConnection(ok) {
    offline = !ok;
    $('connection-dot').classList.toggle('offline', !ok);
    $('connection-text').textContent = ok ? 'Connected' : 'Reconnecting';
    renderPresence();
  }
  async function refresh(silent = false, force = false) {
    if (!controller.graphId) return false;
    if (polling) {
      await polling;
      if (!force) return false;
    }
    polling = (async () => {
      const id = controller.graphId;
      try {
        const next = await controller.refresh();
        if (!next || controller.graphId !== id) return false;
        setConnection(true);
        if (!state || state.state_version !== next.state_version || state.graph_id !== next.graph_id || !!state.agent_active !== !!next.agent_active) applyState(next);
        return true;
      } catch (error) {
        if (controller.graphId !== id) return false;
        setConnection(false);
        if (!silent) toast(error.message, true);
        return false;
      } finally {
        polling = false;
      }
    })();
    return polling;
  }
  async function mutate(method, args, options = {}) {
    if (busy || !state || !current()) return null;
    busy = true;
    updateComposer();
    updateApprovalButton();
    const graphId = state.graph_id;
    const expectedVersion = options.expectedVersion ?? state.state_version;
    try {
      const next = await controller.mutate(method, args, expectedVersion);
      if (!next || controller.graphId !== graphId) return null;
      applyState(next);
      setConnection(true);
      return next;
    } catch (error) {
      if (controller.graphId !== graphId) return null;
      if (error.status === 409) {
        const refreshed = await refresh(true, true);
        if (refreshed) {
          options.onConflict?.(state);
          toast('The review changed while you were editing. Your text is preserved. Check the current version, then deliberately retry.', true, 12000);
        } else toast('The review changed, but the latest version could not be loaded. Your text is preserved. Reconnect before retrying.', true, 12000);
      } else toast(error.message, true, 9000);
      return null;
    } finally {
      busy = false;
      updateComposer();
      updateApprovalButton();
      if (catalogRefreshPending) {
        catalogRefreshPending = false;
        // Let the initiating event handler record its successful save receipt
        // before a deferred catalog refresh can select a different graph.
        setTimeout(() => { if (!destroyed) loadCatalog(); }, 0);
      }
    }
  }
  async function loadCatalog() {
    if (destroyed) return;
    if (busy) { catalogRefreshPending = true; return; }
    catalogLoading = true;
    const request = ++catalogRequest;
    $('refresh-catalog').disabled = true;
    try {
      const next = await controller.listGraphs();
      if (request !== catalogRequest) return;
      if (busy) { catalogRefreshPending = true; return; }
      catalog = next;
      const selectedId = controller.graphId;
      const select = $('graph-select');
      select.replaceChildren();
      for (const item of catalog) select.append(new Option(item.title || item.id, item.id));
      select.disabled = busy || catalog.length === 0;
      $('catalog-count').textContent = `${catalog.length} ${catalog.length === 1 ? 'graph' : 'graphs'} available`;
      if (!catalog.length) {
        select.append(new Option('No graphs available', ''));
        await switchGraph(null);
      } else if (catalog.some(item => item.id === selectedId)) {
        select.value = selectedId;
      } else {
        await switchGraph(catalog[0].id);
      }
    } catch (error) {
      if (request !== catalogRequest) return;
      setConnection(false);
      $('catalog-count').textContent = 'Catalog unavailable';
      toast(error.message, true);
    } finally {
      if (request === catalogRequest) {
        catalogLoading = false;
        $('refresh-catalog').disabled = busy;
      }
    }
  }
  async function switchGraph(id) {
    if (busy) { $('graph-select').value = controller.graphId || ''; return; }
    saveDraft();
    const request = ++graphSwitchRequest;
    ++roundRequest;
    history = null;
    state = null;
    selected = {
      type: 'graph',
      id: null
    };
    focus = false;
    drafts = {};
    draftTarget = 'graph:';
    approvalChecked = false;
    approvalSignature = '';
    lastLayoutKey = '';
    positions = Object.create(null);
    $('annotation-text').value = '';
    $('annotation-kind').value = 'correction';
    $('search').value = '';
    $('domain-filter').value = '';
    $('kind-filter').value = '';
    for (const dialog of ['resolve-dialog', 'edit-dialog', 'delete-dialog']) $(dialog).close();
    $('graph-select').value = id || '';
    renderHeader();
    renderFilters();
    renderList();
    renderGraph(true);
    renderDetails();
    renderNotes();
    renderReview();
    updateComposer();
    $('empty-title').textContent = id ? 'Loading this graph' : 'No graphs available';
    $('empty-description').textContent = id ? 'Loading the graph and its review.' : 'Create or connect a graph, then refresh the catalog.';
    try {
      const next = await controller.selectGraph(id);
      if (request !== graphSwitchRequest || !next) return;
      applyState(next);
      setConnection(true);
    } catch (error) {
      if (request !== graphSwitchRequest) return;
      setConnection(false);
      toast(error.message, true);
      $('empty-title').textContent = 'Cannot load this graph';
      $('empty-description').textContent = 'Your saved review is retained. Refresh the catalog or reconnect to try again.';
    }
  }
  function persistDrafts() {
    if (!state) return;
    try {
      sessionStorage.setItem('graph-drafts:' + state.graph_id, JSON.stringify(drafts));
    } catch {}
  }
  function saveDraft() {
    if (!state) return;
    const previous = drafts[draftTarget] || {},
      text = $('annotation-text').value,
      hasText = !!text.trim();
    drafts[draftTarget] = {
      text,
      kind: $('annotation-kind').value,
      expected_version: hasText ? Number.isInteger(previous.expected_version) ? previous.expected_version : previous.text?.trim() ? 0 : state.state_version : null,
      round: hasText ? previous.round ?? state.round : null,
      retry_ready: hasText && !!previous.retry_ready
    };
    persistDrafts();
    $('draft-indicator').textContent = hasText ? 'Unsaved note · kept in this browser' : 'Saved notes are shared with the agent.';
  }
  function loadDraftForSelection() {
    saveDraft();
    draftTarget = targetKey(selected);
    const d = drafts[draftTarget] || {
      text: '',
      kind: 'correction'
    };
    $('annotation-text').value = d.text;
    $('annotation-kind').value = d.kind;
    $('draft-indicator').textContent = d.text.trim() ? 'Unsaved note · kept in this browser' : 'Saved notes are shared with the agent.';
    updateComposer();
  }
  function applyState(next) {
    if (state && state.graph_id === next.graph_id && next.state_version < state.state_version) return;
    const previous = state,
      changedGraph = !previous || previous.graph_digest !== next.graph_digest;
    state = next;
    if (!previous || previous.graph_id !== next.graph_id) {
      try {
        drafts = JSON.parse(sessionStorage.getItem('graph-drafts:' + state.graph_id) || '{}');
      } catch {
        drafts = {};
      }
      if (!drafts || typeof drafts !== 'object' || Array.isArray(drafts)) drafts = {};
      const d = drafts[draftTarget];
      $('annotation-text').value = d?.text || '';
      $('annotation-kind').value = d?.kind || 'correction';
    }
    if (current() && selected.type !== 'graph' && !findTarget(selected)) {
      const oldTitle = selected.id,
        oldText = $('annotation-text').value;
      saveDraft();
      const carriedDraft = {
        ...drafts[draftTarget]
      };
      selected = {
        type: 'graph',
        id: null
      };
      draftTarget = 'graph:';
      if (oldText.trim()) {
        $('annotation-text').value = `About the removed item ${oldTitle}:\n${oldText}`;
        drafts[draftTarget] = {
          ...carriedDraft,
          text: $('annotation-text').value
        };
        saveDraft();
      }
      toast('The selected item is absent from the new proposal. Its saved notes remain in the review.');
    }
    const signature = String(state.round) + ':' + state.graph_digest + ':' + state.state_version;
    if (approvalSignature !== signature) {
      approvalChecked = false;
      approvalSignature = signature;
    }
    renderHeader();
    renderFilters();
    renderList();
    renderGraph(changedGraph && !history);
    renderDetails();
    renderNotes();
    renderReview();
    updateComposer();
    if (previous && changedGraph && state.round > previous.round) toast(`Round ${state.round} is ready. Review the changes before approving this version.`, false, 8000);
  }
  function renderHeader() {
    const g = graph();
    $('project-title').textContent = state?.title || g?.project?.name || 'Review workspace';
    document.title = ($('project-title').textContent || 'Behavior graph') + ' · Review';
    let label = 'Preparing',
      style = 'amber';
    if (history) {
      label = 'Past proposal';
      style = 'amber';
    } else if (state?.finalized) {
      label = 'Final graph created';
      style = 'teal';
    } else if (state?.approval) {
      label = 'Approved';
      style = 'teal';
    } else if (state?.graph) {
      label = 'Proposal · round ' + state.round;
      style = 'blue';
    }
    $('status-pill').textContent = label;
    $('status-pill').className = 'pill ' + style;
    const select = $('round-select'),
      value = history ? String(history.round) : 'current';
    select.replaceChildren();
    select.append(new Option(state?.round ? `Round ${state.round} · current` : 'First proposal', 'current'));
    for (const r of [...(state?.rounds || [])].sort((a, b) => b.round - a.round)) {
      if (r.round !== state?.round) select.append(new Option(`Round ${r.round}`, String(r.round)));
    }
    select.value = value;
    select.disabled = !state?.round;
    $('stats').replaceChildren();
    for (const [n, t] of [[g?.nodes?.length || 0, 'items'], [g?.dependencies?.length || 0, 'relationships'], [openNotes().length, 'open notes'], [pendingNotes().length, 'need AI review']]) {
      const el = newEl('span');
      el.append(newEl('strong', '', String(n)), document.createTextNode(t));
      $('stats').append(el);
    }
    const presence = newEl('span', 'agent-presence');
    presence.id = 'agent-presence';
    presence.setAttribute('aria-live', 'polite');
    $('stats').append(presence);
    renderPresence();
    $('notes-count').textContent = String(openNotes().length);
    $('review-dot').textContent = state?.approval ? '✓' : String(openNotes().length || '•');
    const latest = (state?.rounds || []).find(r => r.round === (history?.round || state?.round));
    const bar = $('round-summary');
    bar.className = 'round-summary' + (history || latest?.summary ? ' visible' : '') + (history ? ' history' : '');
    bar.textContent = history ? `Viewing round ${history.round} · read only. ${history.summary || latest?.summary || ''}` : latest?.summary ? `Round ${state.round} · ${latest.summary}` : '';
    $('canvas-heading').textContent = history ? `Proposal from round ${history.round}` : 'A shared picture of your application';
  }
  function renderFilters() {
    const sel = $('domain-filter'),
      prev = sel.value;
    sel.replaceChildren(new Option('All domains', ''));
    for (const d of [...new Set((graph()?.nodes || []).map(n => n.domain).filter(Boolean))].sort()) sel.append(new Option(d, d));
    if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
  }
  function filteredNodes() {
    const g = graph();
    if (!g) return [];
    const q = $('search').value.trim().toLowerCase(),
      domain = $('domain-filter').value,
      kind = $('kind-filter').value;
    return g.nodes.filter(n => (!domain || n.domain === domain) && (!kind || n.kind === kind) && (!q || [n.title, n.summary, n.id, n.domain, ...(n.aliases || [])].join(' ').toLowerCase().includes(q)));
  }
  function focusedIDs() {
    if (!focus || selected.type === 'graph') return null;
    const ids = new Set();
    if (selected.type === 'node') ids.add(selected.id);else if (selected.type === 'claim') {
      const c = getClaim(selected.id);
      if (c) ids.add(c.node.id);
    } else {
      const e = getEdge(selected.id);
      if (e) {
        ids.add(e.from);
        ids.add(e.to);
      }
    }
    const seed = new Set(ids);
    for (const e of graph()?.dependencies || []) if (seed.has(e.from) || seed.has(e.to)) {
      ids.add(e.from);
      ids.add(e.to);
    }
    return ids;
  }
  function renderList() {
    const list = $('node-list');
    list.replaceChildren();
    const nodes = filteredNodes().sort((a, b) => (a.domain || '').localeCompare(b.domain || '') || a.title.localeCompare(b.title));
    if (!nodes.length) {
      list.append(newEl('p', 'small-text muted', graph() ? 'No matching items. Try another search or filter.' : 'The first proposal will appear here.'));
      return;
    }
    let group = null;
    for (const n of nodes) {
      if (n.domain !== group) {
        group = n.domain;
        list.append(newEl('div', 'list-group', group || 'Application'));
      }
      const b = button('', () => selectTarget('node', n.id), 'node-button');
      b.setAttribute('aria-pressed', String(selected.type === 'node' && selected.id === n.id));
      const symbol = newEl('span', 'node-symbol', kindLabel(n.kind).slice(0, 1));
      symbol.style.background = backgrounds[n.kind] || '#edf2ee';
      symbol.style.color = colors[n.kind] || '#647e71';
      const copy = newEl('div');
      copy.append(newEl('div', 'node-button-title', n.title), newEl('div', 'node-button-kind', kindLabel(n.kind) + ((n.claims || []).some(c => c.basis !== 'supported') ? ' · uncertainty recorded' : '')));
      b.append(symbol, copy);
      list.append(b);
    }
  }
  function layoutNodes(nodes) {
    const columns = [[], [], []];
    for (const n of nodes) {
      const col = ['behavior', 'interface'].includes(n.kind) ? 0 : ['state', 'event'].includes(n.kind) ? 1 : 2;
      columns[col].push(n);
    }
    columns.forEach(c => c.sort((a, b) => (a.domain || '').localeCompare(b.domain || '') || a.title.localeCompare(b.title)));
    const active = columns.filter(c => c.length),
      height = Math.max(1, ...active.map(c => c.length)) * 117;
    let xIndex = 0;
    for (const col of columns) {
      if (!col.length) continue;
      const yOffset = (height - col.length * 117) / 2;
      col.forEach((n, i) => {
        if (!Object.prototype.hasOwnProperty.call(positions, n.id)) positions[n.id] = {
          x: xIndex * 310 + 35,
          y: i * 117 + yOffset + 35
        };
      });
      xIndex++;
    }
    graphSize = {
      width: Math.max(400, xIndex * 310),
      height: Math.max(280, height + 60)
    };
  }
  function layoutStorageKey() {
    return 'graph-layout:' + state?.graph_id + ':' + (history?.digest || state?.graph_digest || 'empty');
  }
  function ensureLayout(force) {
    const key = layoutStorageKey();
    if (force || key !== lastLayoutKey) {
      lastLayoutKey = key;
      positions = Object.create(null);
      try {
        const stored = JSON.parse(localStorage.getItem(key) || '{}');
        if (stored && typeof stored === 'object' && !Array.isArray(stored)) positions = Object.assign(Object.create(null), stored);
      } catch {
        positions = Object.create(null);
      }
      for (const id in positions) if (!Number.isFinite(positions[id]?.x) || !Number.isFinite(positions[id]?.y)) delete positions[id];
      return true;
    }
    return false;
  }
  function persistPositions() {
    try {
      localStorage.setItem(layoutStorageKey(), JSON.stringify(positions));
    } catch {}
  }
  function wrapText(text, maxChars = 25, maxLines = 2) {
    const words = String(text || '').split(/\s+/),
      lines = [];
    let line = '';
    for (const word of words) {
      if ((line + ' ' + word).trim().length > maxChars && line) {
        lines.push(line);
        line = word;
      } else line = (line + ' ' + word).trim();
    }
    if (line) lines.push(line);
    if (lines.length > maxLines) {
      lines.length = maxLines;
      lines[maxLines - 1] = lines[maxLines - 1].slice(0, maxChars - 1) + '…';
    }
    return lines;
  }
  function renderGraph(forceFit = false) {
    const g = graph();
    const changed = ensureLayout(forceFit);
    const focusIDs = focusedIDs();
    visibleNodes = filteredNodes().filter(n => !focusIDs || focusIDs.has(n.id));
    const ids = new Set(visibleNodes.map(n => n.id));
    visibleEdges = (g?.dependencies || []).filter(e => ids.has(e.from) && ids.has(e.to));
    layoutNodes(g?.nodes || []);
    $('node-layer').replaceChildren();
    $('edge-layer').replaceChildren();
    const empty = $('graph-empty');
    empty.classList.toggle('hidden', visibleNodes.length > 0);
    if (!visibleNodes.length) {
      $('empty-title').textContent = g ? 'No items match this view' : 'Preparing your first proposal';
      $('empty-description').textContent = g ? 'Try a different filter, or show all items.' : 'The agent is gathering behaviors and their connections. This view updates when the proposal is ready.';
    }
    const selectedNode = selected.type === 'node' ? selected.id : selected.type === 'claim' ? getClaim(selected.id)?.node.id : null;
    const connected = new Set(selectedNode ? [selectedNode] : []);
    if (selectedNode) for (const e of g?.dependencies || []) if (e.from === selectedNode || e.to === selectedNode) {
      connected.add(e.from);
      connected.add(e.to);
    }
    visibleEdges.forEach((e, i) => {
      const a = positions[e.from],
        b = positions[e.to];
      if (!a || !b) return;
      const isSelected = selected.type === 'dependency' && selected.id === e.id;
      const highlighted = isSelected || !!selectedNode && (e.from === selectedNode || e.to === selectedNode);
      const group = svgEl('g', {
        'class': 'graph-edge' + (e.basis === 'inferred' ? ' inferred' : '') + (highlighted ? ' selected' : '') + (selectedNode && !highlighted ? ' dimmed' : ''),
        'data-edge': e.id,
        role: 'button',
        tabindex: '0',
        'aria-label': `${getNode(e.from)?.title} ${nice(e.relationship)} ${getNode(e.to)?.title}`
      });
      const same = Math.abs(a.x - b.x) < 20;
      let sx, sy, tx, ty, c1x, c1y, c2x, c2y;
      if (same) {
        sx = a.x + 210;
        sy = a.y + 43;
        tx = b.x + 210;
        ty = b.y + 43;
        const bend = 65 + i % 3 * 18;
        c1x = sx + bend;
        c1y = sy;
        c2x = tx + bend;
        c2y = ty;
      } else {
        const forward = b.x > a.x;
        sx = a.x + (forward ? 210 : 0);
        sy = a.y + 43;
        tx = b.x + (forward ? 0 : 210);
        ty = b.y + 43;
        const dist = Math.max(45, Math.abs(tx - sx) * .48);
        c1x = sx + (forward ? dist : -dist);
        c1y = sy;
        c2x = tx + (forward ? -dist : dist);
        c2y = ty;
      }
      const d = `M${sx} ${sy} C${c1x} ${c1y} ${c2x} ${c2y} ${tx} ${ty}`;
      group.append(svgEl('path', {
        d,
        stroke: 'transparent',
        'stroke-width': 19,
        fill: 'none'
      }), svgEl('path', {
        d,
        'class': 'line',
        'marker-end': highlighted ? 'url(#arrow-selected)' : 'url(#arrow)'
      }));
      const mx = (sx + 3 * c1x + 3 * c2x + tx) / 8,
        my = (sy + 3 * c1y + 3 * c2y + ty) / 8,
        label = nice(e.relationship),
        labelWidth = label.length * 6.5 + 12;
      group.append(svgEl('rect', {
        x: mx - labelWidth / 2,
        y: my - 9,
        width: labelWidth,
        height: 18,
        rx: 4,
        'class': 'edge-label-bg'
      }), svgEl('text', {
        x: mx,
        y: my + 3,
        'text-anchor': 'middle',
        'class': 'edge-label'
      }, label));
      group.append(svgEl('title', {}, e.rationale));
      group.addEventListener('click', () => {
        if (!moved) selectTarget('dependency', e.id);
      });
      group.addEventListener('keydown', ev => {
        if (ev.key === 'Enter' || ev.key === ' ') {
          ev.preventDefault();
          selectTarget('dependency', e.id);
        }
      });
      $('edge-layer').append(group);
    });
    for (const n of visibleNodes) {
      const p = positions[n.id],
        isSelected = selectedNode === n.id;
      const group = svgEl('g', {
        'class': 'graph-node' + (isSelected ? ' selected' : '') + (selectedNode && !connected.has(n.id) ? ' dimmed' : ''),
        transform: `translate(${p.x},${p.y})`,
        'data-node': n.id,
        role: 'button',
        tabindex: '0',
        'aria-label': `${n.title}, ${kindLabel(n.kind)}`
      });
      group.append(svgEl('rect', {
        width: 210,
        height: 86,
        rx: 11,
        'class': 'node-box'
      }), svgEl('rect', {
        x: 11,
        y: 13,
        width: 5,
        height: 16,
        rx: 2,
        fill: colors[n.kind] || '#82998a'
      }), svgEl('text', {
        x: 24,
        y: 24,
        'font-size': 9.2,
        fill: '#677c71',
        'font-weight': 650,
        'letter-spacing': .4
      }, kindLabel(n.kind).toUpperCase()));
      wrapText(n.title, 27, 2).forEach((line, i) => group.append(svgEl('text', {
        x: 14,
        y: 45 + i * 16,
        'font-size': 12.5,
        fill: '#203d35',
        'font-weight': 650
      }, line)));
      const count = (state?.annotations || []).filter(a => a.status === 'open' && a.target_type === 'node' && a.target_id === n.id).length;
      if (count) {
        group.append(svgEl('circle', {
          cx: 193,
          cy: 17,
          r: 9,
          fill: '#fff0d1'
        }), svgEl('text', {
          x: 193,
          y: 20,
          'text-anchor': 'middle',
          'font-size': 9,
          fill: '#8e601e',
          'font-weight': 700
        }, String(count)));
      }
      if (!count && (n.claims || []).some(c => c.basis !== 'supported')) {
        group.append(svgEl('circle', {
          cx: 193,
          cy: 17,
          r: 9,
          fill: '#fff0d1'
        }), svgEl('text', {
          x: 193,
          y: 20,
          'text-anchor': 'middle',
          'font-size': 10,
          fill: '#8e601e',
          'font-weight': 700
        }, '?'));
      }
      group.append(svgEl('title', {}, n.summary));
      group.addEventListener('click', () => {
        if (!moved) selectTarget('node', n.id);
      });
      group.addEventListener('keydown', ev => {
        if (ev.key === 'Enter' || ev.key === ' ') {
          ev.preventDefault();
          selectTarget('node', n.id);
        }
      });
      $('node-layer').append(group);
    }
    $('view-summary').textContent = g ? `${visibleNodes.length} of ${g.nodes.length} items shown` : '';
    $('focus-button').disabled = selected.type === 'graph';
    $('focus-button').classList.toggle('active', focus);
    $('focus-button').textContent = focus ? 'Showing connections' : 'Focus connections';
    if (changed || forceFit) requestAnimationFrame(fitView);else updateTransform();
  }
  function updateTransform() {
    $('viewport').setAttribute('transform', `translate(${view.x},${view.y}) scale(${view.k})`);
    $('zoom-value').textContent = Math.round(view.k * 100) + '%';
  }
  function fitView() {
    const r = $('graph-wrap').getBoundingClientRect();
    if (!visibleNodes.length) {
      view = {
        x: 25,
        y: 25,
        k: 1
      };
      updateTransform();
      return;
    }
    const pts = visibleNodes.map(n => positions[n.id]);
    const minX = Math.min(...pts.map(p => p.x)),
      minY = Math.min(...pts.map(p => p.y)),
      maxX = Math.max(...pts.map(p => p.x + 240)),
      maxY = Math.max(...pts.map(p => p.y + 86));
    const w = maxX - minX,
      h = maxY - minY,
      k = Math.min(1.25, Math.max(.18, Math.min((r.width - 50) / w, (r.height - 50) / h)));
    view = {
      k,
      x: (r.width - w * k) / 2 - minX * k,
      y: (r.height - h * k) / 2 - minY * k
    };
    updateTransform();
  }
  function zoomBy(factor, cx, cy) {
    const r = $('graph-wrap').getBoundingClientRect();
    cx = cx ?? r.width / 2;
    cy = cy ?? r.height / 2;
    const next = Math.max(.12, Math.min(3, view.k * factor)),
      ratio = next / view.k;
    view = {
      x: cx - (cx - view.x) * ratio,
      y: cy - (cy - view.y) * ratio,
      k: next
    };
    updateTransform();
  }
  function selectTarget(type, id) {
    selected = {
      type,
      id: id || null
    };
    loadDraftForSelection();
    if (focus && type === 'graph') focus = false;
    renderList();
    renderGraph();
    renderDetails();
    renderNotes();
    if (activeTab !== 'notes') setTab('details');
    $('sidebar').classList.remove('mobile-open');
  }
  function setTab(tab) {
    activeTab = tab;
    for (const key of ['details', 'notes', 'review']) {
      $('tab-' + key).classList.toggle('active', key === tab);
      $('tab-' + key).setAttribute('aria-selected', String(key === tab));
      $('panel-' + key).classList.toggle('hidden', key !== tab);
    }
    if (tab === 'review') renderReview();
  }
  function evidenceList(ids) {
    const container = newEl('div');
    for (const id of ids || []) {
      const ev = (graph()?.evidence || []).find(e => e.id === id);
      if (!ev) continue;
      const detail = newEl('details', 'evidence'),
        summary = newEl('summary', '', nice(ev.kind) + ' · ' + ev.id);
      detail.append(summary, newEl('p', '', ev.summary));
      const location = newEl('div', 'evidence-source');
      if (/^https?:\/\//i.test(ev.location)) {
        const a = newEl('a', '', ev.location);
        a.href = ev.location;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        location.append(a);
      } else location.textContent = ev.location;
      detail.append(location);
      const source = graph()?.project?.sources?.find(s => s.id === ev.source_id);
      if (source) {
        detail.append(newEl('div', 'evidence-source', source.location + (source.revision ? ' · ' + source.revision : ' · version not recorded')));
      }
      container.append(detail);
    }
    return container;
  }
  function section(title) {
    const s = newEl('section', 'section'),
      heading = newEl('div', 'section-heading');
    heading.append(newEl('h3', '', title));
    s.append(heading);
    return s;
  }
  function nodeClaims(n) {
    const sec = section('Rules, outcomes & assumptions');
    if (!n.claims?.length) {
      sec.append(newEl('p', 'small-text muted', 'No detailed claims have been recorded for this item yet.'));
      return sec;
    }
    for (const c of n.claims) {
      const card = newEl('article', 'claim'),
        top = newEl('div', 'claim-top');
      top.append(newEl('span', 'claim-aspect', nice(c.aspect)), button('Add note', () => {
        selectTarget('claim', c.id);
        setTab('notes');
        $('annotation-text').focus();
      }, 'small ghost'));
      const badges = newEl('div', 'row wrap');
      badges.append(pill(nice(c.perspective), 'blue'), pill(c.basis, c.basis === 'supported' ? 'teal' : c.basis === 'inferred' ? 'amber' : 'red'));
      card.append(top, newEl('p', '', c.statement), badges, evidenceList(c.evidence_ids));
      if (selected.type === 'claim' && selected.id === c.id) card.style.borderColor = '#459780';
      sec.append(card);
    }
    return sec;
  }
  function renderDetails() {
    const panel = $('panel-details');
    panel.replaceChildren();
    const g = graph();
    if (!g) {
      panel.append(newEl('h2', '', 'Start with shared understanding'), newEl('p', 'detail-summary', 'The proposal will show business behaviors and the rules, data, and events that connect them. You can review one area at a time.'));
      const box = newEl('div', 'overview-card');
      box.append(newEl('h3', '', 'Your knowledge shapes the graph'), newEl('p', '', 'Select an item to inspect its evidence. Leave a correction or question, then submit your feedback. When the agent is not waiting, return to chat to continue.'));
      panel.append(box);
      return;
    }
    if (selected.type === 'graph') {
      const heading = newEl('div', 'detail-heading');
      heading.append(pill('Review proposal', 'blue'), newEl('h2', '', g.project.name), newEl('p', 'detail-summary', 'A map of business behavior and its dependencies. Each connection should describe a real relationship and show what supports it.'));
      panel.append(heading, button('Add a note about the whole graph', () => {
        setTab('notes');
        $('annotation-text').focus();
      }, 'full'));
      const scope = section('In scope'),
        list = newEl('ul', 'scope-list');
      for (const item of g.project.scope || []) list.append(newEl('li', '', item));
      scope.append(list);
      panel.append(scope);
      const box = newEl('div', 'overview-card');
      box.append(newEl('h3', '', 'Read the arrows literally'), newEl('p', '', '“Consumes” points from the consumer to the event. “Reads” points from a reader to shared data. A dashed line marks an inferred relationship.'), newEl('p', '', 'Connections identify what to investigate. They do not prove that every change affects every connected behavior.'));
      panel.append(box);
      const evidence = section('Evidence coverage');
      evidence.append(newEl('p', 'small-text muted', `${g.evidence.length} source references support ${claimCount(g)} claims and ${g.dependencies.length} relationships.`), button('Review gaps and uncertainty', () => setTab('review'), 'small ghost'));
      panel.append(evidence);
      return;
    }
    if (selected.type === 'node' || selected.type === 'claim') {
      const n = selected.type === 'node' ? getNode(selected.id) : getClaim(selected.id)?.node;
      if (!n) return;
      const heading = newEl('div', 'detail-heading');
      heading.append(pill(kindLabel(n.kind), 'teal'), newEl('h2', '', n.title), newEl('div', 'detail-id', n.id), newEl('p', 'detail-summary', n.summary));
      if (n.owner_role) heading.append(newEl('div', 'small-text muted', 'Suggested owner · ' + n.owner_role));
      panel.append(heading, button('Annotate this ' + (selected.type === 'claim' ? 'claim' : 'item'), () => {
        setTab('notes');
        $('annotation-text').focus();
      }, 'full'));
      panel.append(nodeClaims(n));
      const relations = section('Connections');
      const edges = g.dependencies.filter(e => e.from === n.id || e.to === n.id);
      if (!edges.length) relations.append(newEl('p', 'small-text muted', 'No relationships have been recorded for this item.'));
      for (const e of edges) {
        const b = button('', () => selectTarget('dependency', e.id), 'relation-row');
        b.append(newEl('span', 'relation-label', nice(e.relationship) + (e.basis === 'inferred' ? ' · inferred' : '')), newEl('span', '', `${getNode(e.from)?.title || e.from} → ${getNode(e.to)?.title || e.to}`));
        relations.append(b);
      }
      panel.append(relations);
      return;
    }
    const e = getEdge(selected.id);
    if (!e) return;
    const heading = newEl('div', 'detail-heading');
    heading.append(pill('Relationship', 'blue'), newEl('h2', '', nice(e.relationship)), newEl('div', 'detail-id', e.id), newEl('p', 'detail-summary', e.rationale), pill(e.basis, e.basis === 'supported' ? 'teal' : 'amber'));
    panel.append(heading);
    const endpoints = newEl('div', 'overview-card');
    endpoints.append(newEl('div', 'eyebrow', 'From'), button(getNode(e.from)?.title || e.from, () => selectTarget('node', e.from), 'full ghost'), newEl('div', 'eyebrow', 'To'), button(getNode(e.to)?.title || e.to, () => selectTarget('node', e.to), 'full ghost'));
    panel.append(endpoints, button('Annotate this relationship', () => {
      setTab('notes');
      $('annotation-text').focus();
    }, 'full'));
    if (e.conditions?.length) {
      const sec = section('Applies when'),
        list = newEl('ul', 'coverage-list');
      e.conditions.forEach(x => list.append(newEl('li', '', x)));
      sec.append(list);
      panel.append(sec);
    }
    const evidence = section('Supporting evidence');
    evidence.append(evidenceList(e.evidence_ids));
    panel.append(evidence);
  }
  function updateComposer() {
    $('graph-select').disabled = busy || catalog.length === 0;
    $('refresh-catalog').disabled = busy || catalogLoading;
    const historical = !current(),
      draft = drafts[draftTarget];
    $('save-note').textContent = draft?.retry_ready ? 'Retry save note' : 'Save note';
    const warning = $('draft-warning');
    warning.classList.toggle('hidden', !draft?.retry_ready || !$('annotation-text').value.trim());
    warning.textContent = 'The latest review is loaded. Check the current proposal, then choose Retry save note to apply this text to that version.';
    $('composer-target').textContent = 'About ' + targetTitle(selected);
    $('save-note').disabled = busy || historical || !state || !$('annotation-text').value.trim();
    $('annotation-text').disabled = historical;
    $('annotation-kind').disabled = historical;
    $('request-review').disabled = busy || historical || !state || state.review_requested;
    const notice = $('feedback-notice');
    notice.className = 'notice' + (historical ? ' warning' : '') + (!historical && !state?.review_requested && !pendingNotes().length ? ' hidden' : '');
    notice.textContent = historical ? 'This is a past proposal. Note outcomes reflect the current review state. Return to the current round to add or settle notes.' : feedbackGuidance();
    $('feedback-help').textContent = '“AI processed” means the agent handled that revision of a note. It can still need your input or remain open. Editing a note queues its new revision for review. The page saves feedback; it does not run an AI.';
    $('request-review').textContent = state?.review_requested ? 'Feedback is ready for the agent' : 'Send feedback to the agent';
  }
  function annotationTargetExists(a) {
    if (a.target_type === 'graph') return true;
    return !!findTarget({
      type: a.target_type,
      id: a.target_id
    });
  }
  function renderNotes() {
    const list = $('notes-list'),
      filter = $('note-filter').value;
    list.replaceChildren();
    let notes = [...(state?.annotations || [])].filter(a => filter === 'pending' ? feedbackState(a).pending : filter === 'deleted' ? a.status === 'deleted' : a.status !== 'deleted');
    if (history) notes = notes.filter(a => a.round <= history.round);
    if (filter === 'open') notes = notes.filter(a => a.status === 'open');
    if (filter === 'selected') notes = notes.filter(a => a.target_type === selected.type && (a.target_id || null) === selected.id);
    notes.sort((a, b) => Number(a.status === 'resolved') - Number(b.status === 'resolved') || String(b.created_at).localeCompare(String(a.created_at)));
    if (!notes.length) {
      list.append(newEl('div', 'overview-card', filter === 'all' ? 'No notes yet. Add what the proposal gets right, what is missing, or what needs a decision.' : 'No notes match this view.'));
      return;
    }
    for (const a of notes) {
      const card = newEl('article', 'note ' + a.status),
        meta = newEl('div', 'note-meta'),
        left = newEl('div', 'row'),
        processing = feedbackState(a);
      left.append(pill(nice(a.kind), 'blue'), pill(nice(a.status), a.status === 'open' ? 'amber' : a.status === 'resolved' ? 'teal' : ''));
      meta.append(left, newEl('span', '', `Round ${a.round} · ${a.source === 'ui' ? 'Review page' : a.source === 'chat' ? 'Chat' : 'Agent'}`));
      card.append(meta);
      const target = {
        type: a.target_type,
        id: a.target_id || null
      };
      const targetBtn = button(targetTitle(target), () => {
        if (annotationTargetExists(a)) selectTarget(target.type, target.id);else toast('This item was removed from the current proposal. The annotation remains part of the review.');
      }, 'note-target');
      card.append(targetBtn, newEl('p', 'note-body', a.text));
      const processingRow = newEl('div', 'note-processing');
      processingRow.append(pill(processing.label, processing.tone));
      if (processing.revision) processingRow.append(newEl('span', 'tiny muted', `Feedback v${processing.revision}`));
      card.append(processingRow);
      if (processing.receipt) {
        const receipt = processing.receipt,
          response = newEl('div', 'processing-receipt' + (processing.receiptIsCurrent ? '' : ' previous'));
        response.append(newEl('strong', '', `${processing.receiptIsCurrent ? 'AI response' : 'Previous AI response'} · feedback v${receipt.feedback_revision}`), newEl('div', '', feedbackOutcome(receipt.outcome)), newEl('p', '', receipt.summary));
        if (!processing.receiptIsCurrent) response.append(newEl('div', 'tiny muted', 'This response belongs to an earlier revision. The latest feedback still needs review.'));
        card.append(response);
      }
      if (!annotationTargetExists(a)) card.append(newEl('div', 'orphan', 'Item not present in this proposal · note retained'));
      if (a.resolution) {
        const label = {
          addressed: 'Addressed',
          accepted_gap: 'Accepted limitation',
          rejected: 'No change needed'
        }[a.resolution.disposition] || nice(a.resolution.disposition);
        card.append(newEl('div', 'resolution', `${label} · ${a.resolution.source === 'agent' ? 'Agent' : 'Reviewer'}\n${a.resolution.text}`));
      }
      const actions = newEl('div', 'note-actions');
      if (a.status === 'deleted') card.append(newEl('div', 'small-text muted', 'Deleted from active review · audit retained'));
      if (current() && a.status !== 'deleted') {
        actions.append(button('Edit', () => openEdit(a), 'small ghost'), button('Delete', () => openDelete(a), 'small ghost danger'));
        if (a.status === 'open') actions.append(button('Settle note', () => openResolution(a), 'small'));else actions.append(button('Reopen', async () => {
          if (await mutate('reopenAnnotation', [a.id])) toast('Note reopened. The current proposal needs review again.');
        }, 'small ghost'));
      }
      card.append(actions);
      list.append(card);
    }
  }
  function renderCoverage(panel, title, items, warning = false) {
    const sec = section(title);
    if (items?.length) {
      const ul = newEl('ul', 'coverage-list');
      for (const x of items) ul.append(newEl('li', '', x));
      sec.append(ul);
    } else sec.append(newEl('p', 'small-text muted', warning ? 'None recorded. This does not prove complete coverage.' : 'None recorded.'));
    panel.append(sec);
  }
  function renderReview() {
    const panel = $('panel-review'),
      g = graph();
    panel.replaceChildren();
    panel.append(newEl('h2', '', 'Settle this version'), newEl('p', 'small-text muted', 'Review the scope, important assumptions, and every note before approving the graph.'));
    if (!g) {
      panel.append(newEl('div', 'overview-card', 'The first proposal must be ready before it can be approved.'));
      return;
    }
    if (history) panel.append(newEl('div', 'notice warning', 'You are viewing a past proposal. Approval and feedback are available on the current round.'));
    if (current() && state.finalized) {
      panel.append(newEl('div', 'notice', 'The final graph has been created from this approved version. Adding feedback or a revised proposal will reopen the review.'));
    } else if (current() && state.approval) {
      panel.append(newEl('div', 'notice', approvalGuidance()));
    }
    const counts = newEl('div', 'review-stats');
    for (const [n, label] of [[openNotes().length, 'open notes'], [pendingNotes().length, 'need AI review'], [uncertainCount(g), 'uncertain claims / links'], [(state.rounds || []).length, 'review rounds']]) {
      const b = newEl('div');
      b.append(newEl('strong', '', String(n)), newEl('span', '', label));
      counts.append(b);
    }
    panel.append(counts);
    if (pendingNotes().length) panel.append(newEl('p', 'small-text muted', 'Some feedback still needs AI review, including any pending withdrawals. Processing does not settle a note or approve the graph. Open the Notes tab and choose “Needs AI review” to inspect it.'));
    renderCoverage(panel, 'Reviewed scope', g.project.scope);
    renderCoverage(panel, 'Inspected evidence', g.coverage.checked);
    renderCoverage(panel, 'Known blind spots', g.coverage.blind_spots, true);
    renderCoverage(panel, 'Not yet inspected', g.coverage.unexpanded, true);
    renderCoverage(panel, 'Conflicting evidence', g.coverage.conflicts, true);
    const sourceSec = section('Source snapshots');
    for (const s of g.project.sources || []) {
      const d = newEl('div', 'source-item');
      d.append(newEl('strong', '', nice(s.kind)), newEl('div', 'evidence-source', s.location), newEl('div', 'tiny muted', s.revision ? 'Version · ' + s.revision : 'Version not recorded'));
      sourceSec.append(d);
    }
    if (!g.project.sources?.length) sourceSec.append(newEl('p', 'small-text muted', 'No source snapshots recorded.'));
    panel.append(sourceSec);
    const approval = newEl('div', 'approval-box');
    approval.append(newEl('h3', '', `Approve round ${history?.round || state?.round}`), newEl('p', 'approval-note', `Approval covers all ${g.nodes.length} items, ${g.dependencies.length} relationships, and ${claimCount(g)} claims, including anything hidden by diagram filters.`));
    const label = newEl('label', 'checkline'),
      check = newEl('input');
    check.type = 'checkbox';
    check.id = 'acknowledge-limits';
    check.checked = approvalChecked || !!state.approval?.acknowledge_limits;
    check.disabled = !current() || !!state.approval;
    check.addEventListener('change', () => {
      approvalChecked = check.checked;
      updateApprovalButton();
    });
    label.append(check, newEl('span', '', 'I have reviewed this version and accept its listed gaps, conflicts, and uncertainty as explicit limitations.'));
    approval.append(label, newEl('p', 'approval-reason'));
    const approve = button('Approve for final graph', approveCurrent, 'primary full');
    approve.id = 'approve-button';
    approval.append(approve, newEl('p', 'approval-note', 'Approval applies only to this exact version. A new proposal or any feedback change requires another approval.'));
    panel.append(approval);
    updateApprovalButton();
    const rounds = section('Review history');
    for (const r of [...(state.rounds || [])].sort((a, b) => b.round - a.round)) {
      const item = newEl('div', 'history-item');
      item.append(button(`Round ${r.round}${r.round === state.round ? ' · current' : ''}`, () => switchRound(r.round === state.round ? 'current' : String(r.round)), 'small ghost'), newEl('p', '', r.summary), newEl('div', 'tiny muted', `${r.node_count} items · ${r.dependency_count} relationships`));
      rounds.append(item);
    }
    panel.append(rounds);
  }
  function updateApprovalButton() {
    const b = $('approve-button');
    if (!b) return;
    const reasons = [];
    if (history) reasons.push('Return to the current round.');
    if (!state?.graph?.nodes?.length) reasons.push('Wait for a proposal.');
    if (openNotes().length) reasons.push(`Settle ${openNotes().length} open ${openNotes().length === 1 ? 'note' : 'notes'} first.`);
    if (state?.review_requested) reasons.push('The agent needs to respond to the requested review.');
    if (!approvalChecked && !state?.approval) reasons.push('Acknowledge the listed limitations.');
    b.disabled = busy || reasons.length > 0 || !!state?.approval;
    b.textContent = state?.approval ? 'This version is approved' : 'Approve for final graph';
    const reason = $('panel-review').querySelector('.approval-reason');
    if (reason) reason.textContent = reasons.join(' ') || 'All notes are settled. This version is ready for your approval.';
  }
  async function approveCurrent() {
    if (!state || !current() || !approvalChecked) return;
    const r = state.round;
    const next = await mutate('approveReview', [{
      acknowledge_limits: true,
      text: `I approve round ${r} for final graph creation and accept the listed limitations, conflicts, and uncertainty.`
    }]);
    if (next) toast(approvalGuidance(), false, 10000);
  }
  function openResolution(a) {
    resolveId = a.id;
    resolveVersion = state.state_version;
    $('resolution-warning').classList.add('hidden');
    $('save-resolution').textContent = 'Save outcome';
    $('resolve-question').textContent = a.text;
    $('resolution-text').value = '';
    $('disposition').value = 'addressed';
    $('resolve-dialog').showModal();
    $('resolution-text').focus();
  }
  async function switchRound(value) {
    const request = ++roundRequest;
    if (value === 'current') {
      controller.cancelHistory();
      history = null;
    } else {
      try {
        const snapshot = await controller.getRound(Number(value));
        if (!snapshot || request !== roundRequest) return;
        history = snapshot;
      } catch (error) {
        if (request === roundRequest) toast(error.message, true);
        return;
      }
    }
    selected = { type: 'graph', id: null };
    focus = false;
    loadDraftForSelection();
    renderHeader(); renderFilters(); renderList(); renderGraph(true);
    renderDetails(); renderNotes(); renderReview(); updateComposer();
  }

  function openEdit(note) {
    editId = note.id;
    editVersion = state.state_version;
    $('edit-kind').value = note.kind;
    $('edit-text').value = note.text;
    $('edit-warning').classList.add('hidden');
    $('save-edit').textContent = 'Save changes';
    $('edit-dialog').showModal();
    $('edit-text').focus();
  }
  function openDelete(note) {
    deleteId = note.id;
    deleteVersion = state.state_version;
    $('delete-preview').textContent = note.text;
    $('delete-warning').classList.add('hidden');
    $('confirm-delete').textContent = 'Delete note';
    $('delete-dialog').showModal();
  }
  $('graph-select').addEventListener('change', event => switchGraph(event.target.value));
  $('refresh-catalog').addEventListener('click', loadCatalog);
  $('cancel-edit').addEventListener('click', () => $('edit-dialog').close());
  $('edit-form').addEventListener('submit', async event => {
    event.preventDefault();
    const text = $('edit-text').value.trim();
    if (!text) return;
    $('save-edit').disabled = true;
    const next = await mutate('updateAnnotation', [editId, {
      text,
      kind: $('edit-kind').value
    }], {
      expectedVersion: editVersion,
      onConflict(latest) {
        editVersion = latest.state_version;
        $('edit-warning').textContent = 'The latest review is loaded. Your edits are preserved. Check the current note, then retry deliberately.';
        $('edit-warning').classList.remove('hidden');
        $('save-edit').textContent = 'Retry changes';
      }
    });
    $('save-edit').disabled = false;
    if (next) {
      $('edit-dialog').close();
      toast('Changes saved. The note is open for review again.');
    }
  });
  $('cancel-delete').addEventListener('click', () => $('delete-dialog').close());
  $('delete-form').addEventListener('submit', async event => {
    event.preventDefault();
    $('confirm-delete').disabled = true;
    const next = await mutate('deleteAnnotation', [deleteId], {
      expectedVersion: deleteVersion,
      onConflict(latest) {
        deleteVersion = latest.state_version;
        const currentNote = latest.annotations.find(note => note.id === deleteId);
        $('delete-preview').textContent = currentNote?.text || 'This note is no longer available.';
        $('delete-warning').textContent = 'The review changed. Check the current note above before retrying deletion.';
        $('delete-warning').classList.remove('hidden');
        $('confirm-delete').textContent = 'Retry delete';
      }
    });
    $('confirm-delete').disabled = false;
    if (next) {
      $('delete-dialog').close();
      toast('Note deleted from the active review. Its audit history is retained.');
    }
  });
  $('search').addEventListener('input', () => {
    renderList();
    renderGraph(true);
  });
  for (const id of ['domain-filter', 'kind-filter']) $(id).addEventListener('change', () => {
    renderList();
    renderGraph(true);
  });
  $('clear-filter').addEventListener('click', () => {
    $('search').value = '';
    $('domain-filter').value = '';
    $('kind-filter').value = '';
    focus = false;
    renderList();
    renderGraph(true);
  });
  $('focus-button').addEventListener('click', () => {
    focus = !focus;
    renderGraph(true);
  });
  $('fit-button').addEventListener('click', fitView);
  $('zoom-in').addEventListener('click', () => zoomBy(1.2));
  $('zoom-out').addEventListener('click', () => zoomBy(1 / 1.2));
  $('reset-layout').addEventListener('click', () => {
    positions = Object.create(null);
    try {
      localStorage.removeItem(layoutStorageKey());
    } catch {}
    layoutNodes(graph()?.nodes || []);
    renderGraph();
    fitView();
  });
  for (const tab of ['details', 'notes', 'review']) $('tab-' + tab).addEventListener('click', () => setTab(tab));
  $('open-review').addEventListener('click', () => {
    setTab('review');
    if (innerWidth < 701) $('panel-review').scrollIntoView({
      behavior: 'smooth',
      block: 'start'
    });
  });
  $('round-select').addEventListener('change', e => switchRound(e.target.value));
  $('note-whole-graph').addEventListener('click', () => {
    selectTarget('graph', null);
    setTab('notes');
  });
  $('note-filter').addEventListener('change', renderNotes);
  $('open-list').addEventListener('click', () => $('sidebar').classList.toggle('mobile-open'));
  $('close-list').addEventListener('click', () => $('sidebar').classList.remove('mobile-open'));
  $('annotation-text').addEventListener('input', () => {
    saveDraft();
    updateComposer();
  });
  $('annotation-kind').addEventListener('change', saveDraft);
  $('save-note').addEventListener('click', async () => {
    const text = $('annotation-text').value.trim();
    if (!text) return;
    saveDraft();
    const target = {
        ...selected
      },
      key = targetKey(target),
      kind = $('annotation-kind').value,
      expectedVersion = drafts[key].expected_version;
    const next = await mutate('createAnnotation', [{
      target_type: target.type,
      target_id: target.id,
      kind,
      text
    }], {
      expectedVersion,
      onConflict: latest => {
        const draft = drafts[key];
        if (draft) {
          draft.expected_version = latest.state_version;
          draft.round = latest.round;
          draft.retry_ready = true;
          persistDrafts();
        }
        updateComposer();
      }
    });
    if (next) {
      drafts[key] = {
        text: '',
        kind,
        expected_version: null,
        round: null,
        retry_ready: false
      };
      if (draftTarget === key && $('annotation-text').value.trim() === text) $('annotation-text').value = '';
      saveDraft();
      renderNotes();
      updateComposer();
      toast('Note saved with this review. Send your feedback when you are ready for the agent to respond.');
    }
  });
  $('request-review').addEventListener('click', async () => {
    if ($('annotation-text').value.trim()) {
      toast('Save your current note before sending feedback. Your text has not been submitted yet.', true);
      $('annotation-text').focus();
      return;
    }
    if (await mutate('requestReview', [])) toast(feedbackGuidance(), false, 12000);
  });
  $('cancel-resolution').addEventListener('click', () => $('resolve-dialog').close());
  $('resolve-form').addEventListener('submit', async e => {
    e.preventDefault();
    const text = $('resolution-text').value.trim();
    if (!text) {
      $('resolution-text').focus();
      return;
    }
    $('save-resolution').disabled = true;
    const next = await mutate('resolveAnnotation', [resolveId, {
      disposition: $('disposition').value,
      text
    }], {
      expectedVersion: resolveVersion,
      onConflict: latest => {
        resolveVersion = latest.state_version;
        $('resolution-warning').textContent = 'The latest review is loaded. Your explanation is preserved. Check the updated proposal and note before choosing Retry saving outcome.';
        $('resolution-warning').classList.remove('hidden');
        $('save-resolution').textContent = 'Retry saving outcome';
      }
    });
    $('save-resolution').disabled = false;
    if (next) {
      $('resolve-dialog').close();
      toast('Outcome saved. Review all remaining notes before approving the proposal.');
    }
  });
  const svg = $('graph-svg');
  svg.addEventListener('wheel', e => {
    e.preventDefault();
    const r = svg.getBoundingClientRect();
    zoomBy(Math.exp(-e.deltaY * .0014), e.clientX - r.left, e.clientY - r.top);
  }, {
    passive: false
  });
  svg.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    const node = e.target.closest('[data-node]');
    if (e.target.closest('[data-edge]') && !node) return;
    moved = false;
    const r = svg.getBoundingClientRect();
    drag = {
      type: node ? 'node' : 'pan',
      id: node?.dataset.node,
      startX: e.clientX,
      startY: e.clientY,
      x: view.x,
      y: view.y,
      px: node ? positions[node.dataset.node].x : 0,
      py: node ? positions[node.dataset.node].y : 0
    };
    svg.setPointerCapture(e.pointerId);
    svg.classList.add('dragging');
  });
  svg.addEventListener('pointermove', e => {
    if (!drag) return;
    const dx = e.clientX - drag.startX,
      dy = e.clientY - drag.startY;
    if (Math.abs(dx) + Math.abs(dy) > 4) moved = true;
    if (drag.type === 'pan') {
      view.x = drag.x + dx;
      view.y = drag.y + dy;
      updateTransform();
    } else if (moved) {
      positions[drag.id] = {
        x: drag.px + dx / view.k,
        y: drag.py + dy / view.k
      };
      renderGraph();
    }
  });
  svg.addEventListener('pointerup', e => {
    if (!drag) return;
    if (drag.type === 'node' && moved) persistPositions();
    const id = drag.id,
      wasMoved = moved;
    drag = null;
    svg.classList.remove('dragging');
    if (svg.hasPointerCapture(e.pointerId)) svg.releasePointerCapture(e.pointerId);
    if (id && !wasMoved) selectTarget('node', id);
    setTimeout(() => {
      moved = false;
    }, 30);
  });
  svg.addEventListener('pointercancel', () => {
    drag = null;
    svg.classList.remove('dragging');
  });
  const resizeObserver = new ResizeObserver(() => {
    if (!drag && state) fitView();
  });
  resizeObserver.observe($('graph-wrap'));
  const onKeyDown = e => {
    if (e.key === 'Escape') $('sidebar').classList.remove('mobile-open');
  };
  document.addEventListener('keydown', onKeyDown);
  renderDetails();
  updateComposer();
  const ready = loadCatalog();
  const timer = pollInterval > 0 ? setInterval(() => refresh(true), pollInterval) : null;
  return {
    controller,
    ready,
    refresh,
    selectGraph: switchGraph,
    destroy() {
      destroyed = true;
      controller.selectGraph(null);
      ++catalogRequest;
      ++graphSwitchRequest;
      ++roundRequest;
      clearInterval(timer);
      clearTimeout(toastTimer);
      resizeObserver.disconnect();
      document.removeEventListener('keydown', onKeyDown);
    }
  };
}
