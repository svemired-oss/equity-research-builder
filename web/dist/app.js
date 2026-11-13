const get = (id) => document.getElementById(id);
const ui = Object.fromEntries([
  'run-form', 'ticker', 'tickers', 'ticker-error', 'mode-note', 'guided-controls', 'driver', 'driver-value', 'run-case',
  'reason', 'run-button', 'run-status', 'coverage', 'coverage-list', 'results', 'company-sector', 'company', 'snapshot-date',
  'rating', 'result-content', 'close', 'spot', 'target', 'total-return', 'scenario-section', 'scenario-chart',
  'download-status', 'downloads',
].map((id) => [id, get(id)]));

const currency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
const axisCurrency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const percent = new Intl.NumberFormat('en-US', { style: 'percent', minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: 'exceptZero' });
const date = new Intl.DateTimeFormat('en-US', { dateStyle: 'medium', timeZone: 'UTC' });
const saved = new Map();
const mode = () => ui['run-form'].elements.mode.value;
const formatDate = (value) => date.format(new Date(`${value}T00:00:00Z`));
const titleCase = (value) => value.charAt(0).toUpperCase() + value.slice(1);

let api = false;
let active = null;
let busy = false;
let request = 0;

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

async function json(url, options) {
  const response = await fetch(url, options);
  const contentType = response.headers.get('content-type') ?? '';
  const body = contentType.includes('application/json') ? await response.json() : null;
  if (!response.ok || !body) throw new Error(body?.error?.message ?? body?.error ?? 'The research service is unavailable.');
  return body;
}

function setStatus(text, isError = false) {
  ui['run-status'].textContent = text;
  ui['run-status'].classList.toggle('error', isError);
}

function tickerValue() {
  return ui.ticker.value.trim().toUpperCase();
}

function setTickerError(text = '') {
  ui['ticker-error'].hidden = !text;
  ui['ticker-error'].textContent = text;
  if (text) ui.ticker.setAttribute('aria-invalid', 'true');
  else ui.ticker.removeAttribute('aria-invalid');
}

function setDriver() {
  const field = active?.guided?.fields?.find((entry) => entry.target === ui.driver.value);
  if (!field) return;
  for (const constraint of ['min', 'max', 'step']) {
    if (field[constraint] === undefined) ui['driver-value'].removeAttribute(constraint);
    else ui['driver-value'].setAttribute(constraint, field[constraint]);
  }
  if (field.step === undefined) ui['driver-value'].step = 'any';
  ui['driver-value'].value = field.default;
  ui['driver-value'].setAttribute('aria-label', `New value (${field.unit})`);
}

function syncMode() {
  const guided = mode() === 'guided';
  const available = api && active?.guided?.available && active.guided.fields?.length > 0;
  const show = guided && available;
  ui['guided-controls'].hidden = !show;
  for (const id of ['driver', 'driver-value', 'run-case', 'reason']) ui[id].disabled = !show || busy;
  ui['driver-value'].required = show;
  ui.reason.required = show;
  if (!guided) {
    ui['mode-note'].textContent = 'Open the verified model and report from the selected research date.';
  } else if (show) {
    ui['mode-note'].textContent = 'Change a forecast input and generate a matching Excel model and PDF.';
  } else if (api && active?.ticker === 'GD') {
    ui['mode-note'].textContent = 'General Dynamics supports the dated automated package. Guided forecasts are available for FedEx and Newmont with the modeling server configured.';
  } else {
    ui['mode-note'].textContent = 'Guided generation requires the Python modeling server. Use Automated to explore the saved research packages.';
  }
  ui['run-button'].firstChild.textContent = guided ? 'Generate research ' : 'Open research ';
  ui['run-button'].disabled = !saved.size || busy || (guided && !available);
}

function drawScenarios(cases, close) {
  const valid = Array.isArray(cases) && cases.length > 0;
  ui['scenario-section'].hidden = !valid;
  ui['scenario-chart'].replaceChildren();
  if (!valid) return;
  const values = cases.map((row) => row.target_price_usd).concat(close);
  const low = Math.min(...values);
  const high = Math.max(...values);
  const spread = Math.max(high - low, 10);
  const step = spread > 180 ? 50 : spread > 65 ? 25 : 10;
  const start = Math.floor((low - spread * 0.05) / step) * step;
  const end = Math.ceil((high + spread * 0.05) / step) * step;
  const at = (value) => `${100 * (value - start) / (end - start)}%`;
  for (const scenario of cases) {
    const name = titleCase(scenario.case);
    const row = node('div', 'scenario-row');
    row.dataset.case = scenario.case;
    const track = node('div', 'track');
    track.style.setProperty('--value', at(scenario.target_price_usd));
    track.style.setProperty('--close', at(close));
    track.setAttribute('aria-hidden', 'true');
    track.append(node('i'));
    row.append(node('span', '', name), track, node('span', '', currency.format(scenario.target_price_usd)));
    ui['scenario-chart'].append(row);
  }
  const axis = node('div', 'chart-axis');
  axis.setAttribute('aria-hidden', 'true');
  const labels = node('div');
  for (const value of [start, (start + end) / 2, end]) labels.append(node('span', '', axisCurrency.format(value)));
  axis.append(labels);
  ui['scenario-chart'].append(axis);
}

function safeDownload(path) {
  if (typeof path !== 'string') return null;
  const url = new URL(path, window.location.href);
  const endpoint = /^\/api\/(?:companies\/[A-Z]+|runs\/[a-f\d]+)\/downloads\/(workbook|report)$/;
  if (url.origin !== window.location.origin || (!/\.(xlsx|pdf)$/.test(url.pathname) && !endpoint.test(url.pathname))) return null;
  return url;
}

function renderDownloads(downloads) {
  ui.downloads.replaceChildren();
  for (const [key, type, title, description] of [
    ['workbook', 'XLSX', 'Excel financial model', 'Editable formulas and schedules'],
    ['report', 'PDF', 'Equity research report', 'Investment thesis and valuation'],
  ]) {
    const file = downloads?.[key];
    const url = file && safeDownload(file.path);
    if (!url) continue;
    const link = node('a', 'download-link');
    link.href = url.href;
    link.download = file.filename;
    const typeLabel = node('span', `file-type ${type.toLowerCase()}`, type);
    const detail = node('span', 'file-text');
    detail.append(node('strong', '', title), node('small', '', description));
    const arrow = node('span', 'download-arrow', '↓');
    arrow.setAttribute('aria-hidden', 'true');
    link.append(typeLabel, detail, arrow);
    ui.downloads.append(link);
  }
}

function render(snapshot, run = null) {
  const metrics = run?.summary ?? snapshot;
  const generated = run?.mode === 'guided';
  ui['company-sector'].textContent = `${snapshot.ticker} · ${snapshot.sector}`;
  ui.company.textContent = snapshot.company;
  ui['snapshot-date'].textContent = `${formatDate(snapshot.as_of)} cutoff · ${generated ? `${titleCase(run.case)} guided case` : 'Base research snapshot'}`;
  ui.rating.textContent = metrics.rating;
  ui.rating.dataset.rating = metrics.rating.toLowerCase();
  ui.rating.hidden = false;
  ui.close.textContent = currency.format(metrics.reference_close_usd);
  ui.close.title = `Reference close on ${formatDate(snapshot.price_date)}`;
  ui.spot.textContent = currency.format(metrics.spot_value_usd);
  ui.target.textContent = currency.format(metrics.target_price_usd);
  ui.target.title = `Target date ${formatDate(snapshot.target_date)}`;
  ui['total-return'].textContent = percent.format(metrics.total_return);
  ui['total-return'].dataset.direction = metrics.total_return < 0 ? 'down' : 'up';
  drawScenarios(run?.cases ?? (generated ? [] : snapshot.cases), metrics.reference_close_usd);
  renderDownloads(run?.downloads ?? snapshot.downloads);
  ui['download-status'].textContent = generated ? 'Verified guided run' : 'Verified snapshot';
  ui['result-content'].hidden = false;
  ui.results.setAttribute('aria-busy', 'false');
}

function select(ticker) {
  const snapshot = saved.get(ticker);
  if (!snapshot) return;
  request += 1;
  busy = false;
  active = snapshot;
  ui.ticker.value = ticker;
  setTickerError();
  setStatus('');
  for (const button of ui.tickers.children) button.setAttribute('aria-pressed', String(button.dataset.ticker === ticker));
  ui.driver.replaceChildren();
  for (const field of snapshot.guided?.fields ?? []) {
    const option = node('option', '', `${field.label} (${field.unit})`);
    option.value = field.target;
    ui.driver.append(option);
  }
  setDriver();
  syncMode();
  render(snapshot);
}

function addChoices(snapshots) {
  for (const snapshot of snapshots) {
    saved.set(snapshot.ticker, snapshot);
    const quick = node('button', '', snapshot.ticker);
    quick.type = 'button';
    quick.dataset.ticker = snapshot.ticker;
    quick.setAttribute('aria-pressed', 'false');
    quick.addEventListener('click', () => select(snapshot.ticker));
    ui.tickers.append(quick);
    const item = node('li');
    const detail = node('button');
    detail.type = 'button';
    detail.append(node('strong', '', snapshot.ticker), node('span', '', snapshot.company));
    detail.addEventListener('click', () => select(snapshot.ticker));
    item.append(detail);
    ui['coverage-list'].append(item);
  }
  ui.coverage.hidden = !snapshots.length;
}

const pause = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

async function runGuided(snapshot) {
  if (!api || !snapshot.guided?.available || !ui['run-form'].reportValidity()) return;
  const reason = ui.reason.value.trim();
  if (!reason) {
    setStatus('Enter a reason for changing the forecast input.', true);
    ui.reason.focus();
    return;
  }
  const payload = { ticker: snapshot.ticker, mode: 'guided', case: ui['run-case'].value,
    overrides: [{ target: ui.driver.value, value: ui['driver-value'].valueAsNumber, reason }] };
  const current = ++request;
  busy = true;
  syncMode();
  setStatus('Generating and verifying both files. This can take a few minutes.');
  try {
    let run = await json('/api/runs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    for (let polls = 0; ['queued', 'running'].includes(run.state) && current === request && polls < 180; polls += 1) {
      await pause(2000);
      if (current !== request) return;
      run = await json(`/api/runs/${encodeURIComponent(run.id)}`);
    }
    if (current !== request) return;
    if (run.state !== 'completed') throw new Error(run.error ?? 'The run is still processing. Try again after it finishes.');
    render(snapshot, run);
    setStatus('Guided research is ready. Download the model and report on the right.');
  } catch (error) {
    if (current === request) setStatus(error.message, true);
  } finally {
    if (current === request) {
      busy = false;
      syncMode();
    }
  }
}

ui.ticker.setAttribute('aria-describedby', 'ticker-error');
ui.ticker.addEventListener('input', () => {
  setTickerError();
  const symbol = tickerValue();
  if (symbol && saved.has(symbol) && active?.ticker !== symbol) select(symbol);
});
ui.driver.addEventListener('change', setDriver);
ui['run-form'].addEventListener('change', (event) => {
  if (event.target.name === 'mode') {
    setStatus('');
    syncMode();
  }
});
ui['run-form'].addEventListener('submit', (event) => {
  event.preventDefault();
  const snapshot = saved.get(tickerValue());
  if (!snapshot) {
    setTickerError(`Choose a supported ticker: ${[...saved.keys()].join(', ')}.`);
    ui.ticker.focus();
    return;
  }
  if (active?.ticker !== snapshot.ticker) select(snapshot.ticker);
  if (mode() === 'guided') void runGuided(snapshot);
  else {
    render(snapshot);
    setStatus(`${snapshot.ticker} research is ready.`);
    if (matchMedia('(max-width: 720px)').matches) ui.results.scrollIntoView({ block: 'start' });
  }
});

async function loadCatalog() {
  let catalog;
  try {
    try {
      catalog = await json('/api/companies', { signal: AbortSignal.timeout(2500) });
      api = Array.isArray(catalog.snapshots);
    } catch {
      api = false;
    }
    if (!api) catalog = await json('./catalog.json');
    if (catalog.schema_version !== 1 || !catalog.snapshots?.length) throw new Error('Research snapshots are missing.');
    addChoices(catalog.snapshots);
    select(saved.has('GD') ? 'GD' : catalog.snapshots[0].ticker);
  } catch (error) {
    ui.company.textContent = 'Research unavailable';
    ui['snapshot-date'].textContent = error.message;
    ui.results.setAttribute('aria-busy', 'false');
  }
}

void loadCatalog();
