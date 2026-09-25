// Stats dashboard: renders stats/data.json (rebuilt daily by the Lambda).
// No libraries and no inline scripts/styles, to satisfy the site's CSP.
(function () {
  'use strict';

  const OUTPUT_LABELS = {
    cabrillo: 'Cabrillo log',
    summary: 'Contest summary',
    station_report: 'Station activity report',
    weekend_analysis: 'Weekend analysis',
    comprehensive_analysis: 'Comprehensive analysis',
    directional_viz: 'Directional plots (per day)',
    directional_location: 'Directional plots (per location)',
    comparison: 'Log comparison',
    grid_paths: 'Path maps',
    grid_density: 'Grid maps',
  };
  const ERROR_LABELS = {
    callsign: 'Call sign missing or invalid',
    no_qsos: 'No QSOs found in log',
    missing_columns: 'CSV missing columns',
    google_sheets: 'Google Sheets problem',
    too_large: 'File too large',
    too_many_files: 'Too many files',
    unrecognized_format: 'Unrecognized file format',
    unreadable_log: 'Unreadable log',
    bad_outputs: 'No or unknown outputs',
    no_input: 'No log provided',
    bad_band_category: 'Bad band category',
    bad_request: 'Malformed request',
    no_output: 'Nothing generated',
    server_error: 'Server error (bug)',
    other: 'Other',
  };
  const PLAIN_LABELS = {
    files: 'File upload', sheets: 'Google Sheets link',
    cabrillo: 'Cabrillo (.log)', csv: 'CSV',
    AUTO: 'Automatic', '10G': '10G (10 GHz only)', ALL: 'ALL (all bands)',
  };

  const $ = (id) => document.getElementById(id);
  const fmt = (n) => (n == null ? '–' : Number(n).toLocaleString());
  const pct = (part, whole) => (whole ? Math.round((100 * part) / whole) + '%' : '–');

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else node.setAttribute(k, v);
    });
    children.flat().forEach((c) => c != null && node.append(c));
    return node;
  }

  function svg(tag, attrs) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attrs || {}).forEach(([k, v]) => node.setAttribute(k, v));
    return node;
  }

  // ------------------------------------------------------------ tooltip
  const tip = $('tooltip');
  function showTip(text, x, y) {
    tip.textContent = text;
    tip.hidden = false;
    const w = tip.offsetWidth;
    tip.style.left = Math.min(window.innerWidth - w - 8, Math.max(8, x - w / 2)) + 'px';
    tip.style.top = Math.max(8, y - tip.offsetHeight - 12) + 'px';
  }
  function hideTip() { tip.hidden = true; }

  // ------------------------------------------------------------ tiles
  function tile(label, value, sub) {
    return el('div', { class: 'tile' },
      el('div', { class: 'label', text: label }),
      el('div', { class: 'value', text: value }),
      sub ? el('div', { class: 'sub', text: sub }) : null);
  }

  function renderTiles(d) {
    const t = d.totals;
    const served = t.ok + t.partial;
    $('tiles').replaceChildren(
      tile('Analyses', fmt(t.analyses), `${fmt(t.analyses30d)} in the last 30 days`),
      tile('Unique operators', fmt(t.operators), `${fmt(t.logs)} logs analyzed`),
      tile('Home page visits', fmt(t.visits), `${fmt(t.visitors)} daily unique visitors`),
      tile('Visit → analysis', pct(t.analyses, t.visits), 'analyses per visit'),
      tile('Success rate', pct(served, t.analyses),
        `${fmt(t.inputErrors)} rejected · ${fmt(t.serverErrors)} server errors`),
      tile('Median log size', t.medianQsos == null ? '–' : `${fmt(Math.round(t.medianQsos))} QSOs`,
        `${fmt(t.qsos)} QSOs total`),
      tile('Processing time', t.medianMs == null ? '–' : `${(t.medianMs / 1000).toFixed(1)} s`,
        t.p90Ms == null ? '' : `median · 90% under ${(t.p90Ms / 1000).toFixed(1)} s`),
    );
  }

  // ------------------------------------------------------------ daily bar chart
  function renderDaily(container, days, key, noun) {
    const width = Math.max(280, container.clientWidth);
    const height = 150;
    const pad = { top: 8, right: 4, bottom: 22, left: 34 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const max = Math.max(1, ...days.map((d) => d[key]));
    // Round the top of the scale so the midpoint gridline is a whole number.
    const niceMax = max <= 5 ? max : Math.ceil(max / (max > 20 ? 10 : 2)) * (max > 20 ? 10 : 2);
    const step = plotW / days.length;
    const barW = Math.max(1, step - 2); // 2px surface gap between bars
    const y = (v) => pad.top + plotH - (v / niceMax) * plotH;

    const root = svg('svg', { viewBox: `0 0 ${width} ${height}`, height, role: 'img',
      'aria-label': `${noun} per day, maximum ${max}` });

    [0, niceMax / 2, niceMax].forEach((v) => {
      if (v !== Math.round(v) && niceMax <= 5) return;
      root.append(svg('line', { class: 'gridline', x1: pad.left, x2: width - pad.right, y1: y(v), y2: y(v) }));
      const label = svg('text', { class: 'axis-label', x: pad.left - 6, y: y(v) + 4, 'text-anchor': 'end' });
      label.textContent = Math.round(v);
      root.append(label);
    });

    let lastLabelX = -Infinity;
    days.forEach((d, i) => {
      const x = pad.left + i * step + 1;
      if ((d.date.endsWith('-01') || i === 0) && x - lastLabelX > 42) {
        lastLabelX = x;
        const label = svg('text', { class: 'axis-label', x, y: height - 6 });
        label.textContent = new Date(d.date + 'T00:00:00Z')
          .toLocaleDateString(undefined, { month: 'short', day: i === 0 && !d.date.endsWith('-01') ? 'numeric' : undefined, timeZone: 'UTC' });
        root.append(label);
      }
      const value = d[key];
      const hit = svg('rect', { class: 'hit', x: x - 1, y: pad.top, width: step, height: plotH });
      const text = `${d.date}: ${value} ${value === 1 ? noun.replace(/s$/, '') : noun}`;
      hit.addEventListener('mousemove', (e) => showTip(text, e.clientX, e.clientY));
      hit.addEventListener('mouseleave', hideTip);
      root.append(hit);
      if (value > 0) {
        const h = Math.max(2, plotH - (y(value) - pad.top));
        const r = Math.min(4, barW / 2, h / 2);
        const top = pad.top + plotH - h;
        const bottom = pad.top + plotH;
        // rounded data end (top), square baseline
        root.append(svg('path', {
          class: 'bar',
          d: `M${x},${bottom} V${top + r} Q${x},${top} ${x + r},${top} H${x + barW - r} ` +
             `Q${x + barW},${top} ${x + barW},${top + r} V${bottom} Z`,
        }));
      }
    });
    container.replaceChildren(root);
  }

  // ------------------------------------------------------------ horizontal bars
  function renderBars(id, counts, options) {
    const opts = options || {};
    const entries = Array.isArray(counts) ? counts : Object.entries(counts || {});
    const total = opts.total != null ? opts.total : entries.reduce((s, [, n]) => s + n, 0);
    const max = Math.max(1, ...entries.map(([, n]) => n));
    const label = opts.label || ((k) => PLAIN_LABELS[k] || k);
    const rows = entries.map(([key, n]) => {
      const fill = el('div', { class: 'fill' });
      fill.style.width = (100 * n) / max + '%';
      const row = el('div', { class: 'bar-row' },
        el('span', { class: 'name', text: label(key), title: label(key) }),
        el('div', { class: 'track' }, fill),
        el('span', { class: 'val', text: opts.noPct ? fmt(n) : `${fmt(n)} · ${pct(n, total)}` }));
      return row;
    });
    $(id).replaceChildren(...(rows.length ? rows : [el('div', { class: 'none', text: 'None yet' })]));
  }

  // ------------------------------------------------------------ tables
  function renderMonthly(rows) {
    $('monthly').querySelector('tbody').replaceChildren(...rows.slice().reverse().map((m) =>
      el('tr', {}, el('td', { text: m.month }), el('td', { text: fmt(m.visits) }),
        el('td', { text: fmt(m.analyses) }), el('td', { text: fmt(m.logs) }),
        el('td', { text: fmt(m.qsos) }), el('td', { text: fmt(m.operators) }))));
  }

  function renderGrids(d) {
    $('gridsDistinct').textContent = fmt(d.gridsDistinct);
    const body = $('grids').querySelector('tbody');
    body.replaceChildren(...(d.grids.length
      ? d.grids.map(([g, n]) => el('tr', {}, el('td', { text: g }), el('td', { text: fmt(n) })))
      : [el('tr', {}, el('td', { text: 'None yet', colspan: '2' }))]));
  }

  // ------------------------------------------------------------ main
  function render(d) {
    const t = d.totals;
    const served = t.ok + t.partial;
    const updated = new Date(d.generated).toLocaleString();
    $('meta').textContent = `Updated ${updated}` +
      (d.firstDay ? ` · data since ${d.firstDay}` : '') +
      (d.upstream ? ` · analyzer ${d.upstream}` : '');
    if (!t.analyses && !t.visits) {
      $('empty').hidden = false;
      return;
    }
    $('content').hidden = false;
    renderTiles(d);

    $('dailySpan').textContent = d.daily.length;
    const drawDaily = () => {
      renderDaily($('visitsChart'), d.daily, 'visits', 'visits');
      renderDaily($('analysesChart'), d.daily, 'analyses', 'analyses');
    };
    drawDaily();
    let resizeTimer;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(drawDaily, 150);
    });
    renderMonthly(d.monthly);

    const allOutputs = Object.keys(OUTPUT_LABELS).map((k) => [k, (d.outputs || {})[k] || 0])
      .sort((a, b) => b[1] - a[1]);
    renderBars('outputs', allOutputs, { total: served, label: (k) => OUTPUT_LABELS[k] || k });
    renderBars('mapOptions', d.mapOptions || {}, { total: d.mapAnalyses || 0 });
    renderBars('source', d.source);
    renderBars('formats', d.formats);
    renderBars('logsPerRequest', Object.entries(d.logsPerRequest).sort(),
      { label: (k) => (k === '1' ? '1 log' : `${k} logs`) });
    renderBars('bandCategory', d.bandCategory);
    renderBars('bands', d.bands, { total: t.logs });
    renderBars('qsoBuckets', d.qsoBuckets);
    renderBars('years', d.years);
    renderGrids(d);
    renderBars('repeatOperators', d.repeatOperators);
    renderBars('errors', d.errors, { total: t.analyses, label: (k) => ERROR_LABELS[k] || k });
    renderBars('failedOutputs', d.failedOutputs, { noPct: true, label: (k) => OUTPUT_LABELS[k] || k });
  }

  fetch('data.json', { cache: 'no-cache' })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      if (d) render(d);
      else {
        $('meta').textContent = 'No statistics file yet.';
        $('empty').hidden = false;
      }
    })
    .catch(() => {
      $('meta').textContent = 'Could not load the statistics.';
      $('empty').hidden = false;
    });
})();
