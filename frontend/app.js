// ARRL 10 GHz log analyzer - browser side.
// Reads the chosen logs as text, POSTs them to /api/process, and shows the
// generated files. No frameworks, no inline scripts (the site's CSP forbids them).
(function () {
  'use strict';

  const MAX_FILES = 4;
  const MAX_FILE_BYTES = 1000000;
  const API_URL = 'api/process';
  const JOB_URL = 'api/jobs/';
  const MAX_WAIT_MS = 20 * 60 * 1000;

  const $ = (id) => document.getElementById(id);
  const form = $('processForm');
  const fileInput = $('logFiles');
  const dropZone = $('dropZone');
  const fileList = $('fileList');
  let chosenFiles = [];   // [{name, content}]

  // ------------------------------------------------------------ helpers
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else node.setAttribute(key, value);
    });
    children.flat().forEach((child) => {
      if (child != null) node.append(child);
    });
    return node;
  }

  function show(node, visible) { node.hidden = !visible; }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + ' KB';
    return (bytes / 1024 / 1024).toFixed(1) + ' MB';
  }

  function inputType() {
    return form.querySelector('input[name="inputType"]:checked').value;
  }

  // ------------------------------------------------------------ input source
  form.querySelectorAll('input[name="inputType"]').forEach((radio) => {
    radio.addEventListener('change', () => {
      show($('filePanel'), inputType() === 'files');
      show($('sheetsPanel'), inputType() === 'sheets');
      updateComparisonOption();
    });
  });

  async function addFiles(files) {
    hideError();
    for (const file of files) {
      if (chosenFiles.length >= MAX_FILES) {
        showError(`You can analyze up to ${MAX_FILES} logs at a time.`);
        break;
      }
      if (file.size > MAX_FILE_BYTES) {
        showError(`${file.name} is larger than 1 MB - is that really a contest log?`);
        continue;
      }
      chosenFiles.push({ name: file.name, content: await file.text() });
    }
    renderFileList();
  }

  function renderFileList() {
    fileList.replaceChildren(...chosenFiles.map((f, i) => {
      const remove = el('button', { type: 'button', class: 'link-btn', 'aria-label': `Remove ${f.name}`, text: 'remove' });
      remove.addEventListener('click', () => { chosenFiles.splice(i, 1); renderFileList(); });
      const lines = f.content.split(/\r?\n/).filter((l) => l.trim()).length;
      return el('li', {}, el('span', { text: f.name }), el('small', { text: ` ${lines} lines ` }), remove);
    }));
    show(fileList, chosenFiles.length > 0);
    updateComparisonOption();
  }

  function updateComparisonOption() {
    const box = form.querySelector('input[value="comparison"]');
    const possible = inputType() === 'files' && chosenFiles.length >= 2;
    box.disabled = !possible;
    if (!possible) box.checked = false;
    $('comparisonOption').classList.toggle('disabled', !possible);
  }

  fileInput.addEventListener('change', async () => {
    await addFiles(fileInput.files);
    fileInput.value = '';
  });

  ['dragenter', 'dragover'].forEach((type) => dropZone.addEventListener(type, (e) => {
    e.preventDefault();
    dropZone.classList.add('dragging');
  }));
  ['dragleave', 'drop'].forEach((type) => dropZone.addEventListener(type, () => dropZone.classList.remove('dragging')));
  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    addFiles(e.dataTransfer.files);
  });

  document.querySelectorAll('[data-sample]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const url = btn.getAttribute('data-sample');
      const resp = await fetch(url);
      if (!resp.ok) return showError('Could not load the sample log.');
      chosenFiles.push({ name: url.split('/').pop(), content: await resp.text() });
      chosenFiles = chosenFiles.slice(-MAX_FILES);
      if (url.endsWith('.csv') && !$('callsign').value) $('callsign').value = 'K2UA';
      renderFileList();
    });
  });

  $('callsign').addEventListener('input', (e) => {
    e.target.value = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '');
  });

  // ------------------------------------------------------------ status / errors
  function showError(message) {
    $('errorText').textContent = message;
    show($('errorBox'), true);
    $('errorBox').scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  function hideError() { show($('errorBox'), false); }

  function setBusy(busy) {
    $('submitBtn').disabled = busy;
    $('submitBtn').textContent = busy ? 'Analyzing…' : 'Analyze log';
    if (busy) setStatus('Sending your log…');
    show($('status'), busy);
  }

  function setStatus(text) { $('statusText').textContent = text; }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  // ------------------------------------------------------------ map options
  const mapBoxes = () => [...form.querySelectorAll('input[name="outputs"][value^="grid_"]')];
  function updateMapExtras() {
    const any = mapBoxes().some((b) => b.checked);
    ['mapHtml', 'mapOsm'].forEach((id) => { $(id).disabled = !any; });
    $('mapExtras').classList.toggle('disabled', !any);
  }
  mapBoxes().forEach((b) => b.addEventListener('change', updateMapExtras));

  // ------------------------------------------------------------ background jobs
  // /api/process validates and queues the analysis; the result is fetched by
  // polling /api/jobs/<id> (maps for a big rover log can take minutes).
  async function runJob(payload) {
    const resp = await fetch(API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    let data;
    try { data = await resp.json(); } catch (_) { data = null; }
    if (resp.status === 429 || resp.status === 503) {
      throw new Error('The analyzer is busy right now. Please wait a few seconds and try again.');
    }
    if (!data) throw new Error(`Server error (HTTP ${resp.status}). Please try again.`);
    if (resp.status !== 202 || !data.jobId) throw new Error(data.message || 'The analysis could not be started.');
    return pollJob(data.jobId, payload.outputs.some((o) => o.startsWith('grid_')));
  }

  async function pollJob(jobId, withMaps) {
    const started = Date.now();
    let failures = 0;
    for (;;) {
      const elapsed = Date.now() - started;
      if (elapsed > MAX_WAIT_MS) throw new Error('This is taking much longer than expected. Please try again later.');
      let job = null;
      try {
        const resp = await fetch(JOB_URL + jobId, { cache: 'no-store' });
        if (resp.status !== 429) job = await resp.json();
        failures = 0;
      } catch (_) {
        if (++failures > 5) throw new Error('Lost contact with the server. Check your connection and try again.');
      }
      if (job && job.state === 'done') return job.result;
      if (job && job.state === 'error') throw new Error(job.message || 'The analysis failed.');
      const secs = Math.round(elapsed / 1000);
      if (job && job.state === 'queued') {
        setStatus(secs < 5 ? 'Starting…' : `Waiting for a free worker… ${secs} s`);
      } else if (job) {
        setStatus(`Working… ${secs} s` + (withMaps ? ' · maps can take a minute or two' : ''));
      }
      await sleep(elapsed < 10000 ? 1500 : 3000);
    }
  }

  // ------------------------------------------------------------ submit
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    hideError();
    show($('results'), false);

    const outputs = [...form.querySelectorAll('input[name="outputs"]:checked')].map((c) => c.value);
    const payload = {
      callsign: $('callsign').value.trim(),
      bandCategory: $('bandCategory').value,
      outputs,
      mapHtml: $('mapHtml').checked,
      mapOsm: $('mapOsm').checked,
    };
    if (inputType() === 'files') {
      if (!chosenFiles.length) return showError('Choose at least one log file.');
      payload.files = chosenFiles;
    } else {
      const url = $('sheetsUrl').value.trim();
      if (!/^https:\/\/docs\.google\.com\/spreadsheets\/d\//.test(url)) {
        return showError('Paste a Google Sheets link that starts with https://docs.google.com/spreadsheets/d/');
      }
      payload.sheetsUrl = url;
    }
    if (!outputs.length) return showError('Pick at least one thing to generate.');

    setBusy(true);
    try {
      const data = await runJob(payload);
      if (!data.files || !data.files.length) {
        const details = (data.errors || []).map((e) => `${e.label}: ${e.message}`).join('\n');
        throw new Error([data.message || 'Nothing was generated.', details].filter(Boolean).join('\n'));
      }
      renderResults(data);
    } catch (err) {
      showError(err.message === 'Failed to fetch'
        ? 'Could not reach the server. Check your connection and try again.'
        : err.message);
    } finally {
      setBusy(false);
    }
  });

  form.addEventListener('reset', () => {
    chosenFiles = [];
    renderFileList();
    setTimeout(updateMapExtras);
    hideError();
    show($('results'), false);
    setTimeout(() => {
      show($('filePanel'), true);
      show($('sheetsPanel'), false);
    });
  });

  // ------------------------------------------------------------ results
  function renderResults(data) {
    if (data.upstream) $('upstreamVersion').textContent = data.upstream;
    if (data.gridMapper) $('gridMapperVersion').textContent = data.gridMapper;

    $('logSummary').replaceChildren(...data.logs.map((log) => el('div', { class: 'log-card' },
      el('div', { class: 'log-call', text: log.callsign }),
      el('div', { class: 'log-source', text: log.source }),
      el('dl', {},
        el('dt', { text: 'QSOs' }), el('dd', { text: String(log.qsos) }),
        el('dt', { text: 'Bands' }), el('dd', { text: log.bands.join(', ') }),
        el('dt', { text: 'Dates' }), el('dd', { text: log.firstDate === log.lastDate ? log.firstDate : `${log.firstDate} – ${log.lastDate}` }),
        el('dt', { text: 'Category' }), el('dd', { text: log.bandCategory })))));

    const warn = $('partialErrors');
    warn.replaceChildren(...data.errors.map((e) => el('p', { text: `${e.label} (${e.source}): ${e.message}` })));
    show(warn, data.errors.length > 0);

    // Group files by output type, preserving order.
    const groups = new Map();
    data.files.forEach((f) => {
      if (!groups.has(f.label)) groups.set(f.label, []);
      groups.get(f.label).push(f);
    });
    // A group can mix images (thumbnails) and downloads, e.g. maps + their HTML versions.
    const multi = data.logs.length > 1;
    $('fileGroups').replaceChildren(...[...groups].map(([label, files]) => {
      const images = files.filter((f) => f.kind === 'image');
      const others = files.filter((f) => f.kind !== 'image');
      return el('div', { class: 'file-group' },
        el('h3', { text: label }),
        images.length ? el('div', { class: 'thumbs' }, images.map((f) => renderFile(f, multi))) : null,
        others.length ? el('div', { class: 'downloads' }, others.map((f) => renderFile(f, multi))) : null);
    }));

    const zip = $('zipLink');
    show(zip, Boolean(data.archive));
    if (data.archive) {
      zip.href = data.archive.url;
      zip.setAttribute('download', data.archive.name);
    }

    $('notes').replaceChildren(...data.notes.map((n) => el('div', {},
      el('h4', { text: `${n.label} – ${n.source}` }), el('pre', { text: n.text }))));
    show($('notesBox'), data.notes.length > 0);

    show($('results'), true);
    $('results').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function renderFile(file, multiSource) {
    const caption = file.name + (multiSource ? ` (${file.source})` : '');
    if (file.kind === 'image') {
      return el('a', { class: 'thumb', href: file.url, target: '_blank', rel: 'noopener' },
        el('img', { src: file.url, alt: file.name, loading: 'lazy' }),
        el('span', { text: caption }));
    }
    const size = formatSize(file.size) + (file.kind === 'html' ? ' · interactive map' : '');
    return el('a', { class: 'download', href: file.url, download: file.name },
      el('span', { text: caption }), el('small', { text: size }));
  }

  updateComparisonOption();
  updateMapExtras();

  // Count this visit for the owner's stats dashboard: an anonymous ping, no
  // cookies, no identifiers. Failures are ignored.
  try {
    fetch('api/ping', { method: 'POST', body: '{}', keepalive: true }).catch(() => {});
  } catch (_) { /* ignore */ }
})();
