const get = (id) => document.getElementById(id);
const ui = Object.fromEntries([
  'run-form', 'ticker', 'tickers', 'ticker-error', 'mode-note', 'guided-controls', 'driver', 'driver-value', 'run-case',
  'reason', 'run-button', 'run-label', 'run-status', 'resume-run', 'coverage', 'results', 'company-sector', 'company', 'snapshot-date',
  'rating', 'result-content', 'close', 'spot', 'target', 'total-return', 'scenario-section', 'scenario-chart',
  'scenario-title', 'download-status', 'downloads',
  'driver-basis', 'driver-range', 'result-state', 'state-title', 'state-detail', 'result-note', 'edit-assumptions',
  'workspace-title', 'workspace-intro', 'setup-title', 'site-context',
].map((id) => [id, get(id)]));

const currency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
const axisCurrency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const percent = new Intl.NumberFormat('en-US', { style: 'percent', minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: 'exceptZero' });
const assumptionPercent = new Intl.NumberFormat('en-US', { style: 'percent', maximumFractionDigits: 2 });
const date = new Intl.DateTimeFormat('en-US', { dateStyle: 'medium', timeZone: 'UTC' });
const saved = new Map();
const operatingRuns = new Map();
const publicPreview = document.documentElement.dataset.preview === 'public';
const codespaceHost = location.hostname.endsWith('-8053.app.github.dev');
const privateBuilder = !publicPreview && (codespaceHost || (location.port === '8053'
  && ['127.0.0.1', 'localhost'].includes(location.hostname)));
const mode = () => ui['run-form'].elements.mode.value;
const formatDate = (value) => {
  const day = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(day.getTime()) ? '' : date.format(day);
};
const titleCase = (value) => value.charAt(0).toUpperCase() + value.slice(1);
const validTicker = /^(?:[A-Z0-9]+(?:[.-][A-Z0-9]+)*|(?:XLON|XTKS):[A-Z0-9]+)$/;

let api = false;
let generation = null;
let appleGeneration = null;
let tickerScreening = false;
let operatingGeneration = false;
let exchangeGeneration = [];
let active = null;
let busy = false;
let pendingRun = null;
const runStorageKey = 'research-builder-last-run';

function rememberRun(entry) {
  try {
    if (entry) sessionStorage.setItem(runStorageKey, JSON.stringify(entry));
    else sessionStorage.removeItem(runStorageKey);
  } catch {
    // A blocked storage policy still permits research in the current page.
  }
}

function lastRun() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(runStorageKey));
    if (validTicker.test(saved?.payload?.ticker ?? '') &&
        (saved.id || /^[a-f0-9]{32}$/.test(saved.payload.request_id ?? ''))) return saved;
  } catch {
    // Missing or unusable state starts a fresh page.
  }
  return null;
}

const oracleSources = {
  live_collection: ['newly collected inputs', 'Collected for this run'],
  live_filings_dated_closes: ['live SEC filings and Treasury data with verified November 13 closes', 'Live filings and Treasury · dated closes'],
  dated_evidence: ['saved filing and market evidence', 'Generated from dated evidence'],
  apple_dated_close: ['SEC filings and Treasury data with a verified November 13 close', 'Live filings and Treasury · dated close'],
  operating_research: ['current evidence', 'Generated from current evidence'],
};

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

async function json(url, options) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (error) {
    throw new Error(error.name === 'TimeoutError'
      ? 'The research service took too long to respond.'
      : 'Cannot reach the research service. Check that the builder is still running.');
  }
  const contentType = response.headers.get('content-type') ?? '';
  const body = contentType.includes('application/json') ? await response.json() : null;
  if (!response.ok || !body) {
    const error = new Error(body?.error?.message ?? body?.error ?? 'The research service is unavailable.');
    error.status = response.status;
    throw error;
  }
  return body;
}

function setStatus(text, isError = false) {
  ui['run-status'].textContent = text;
  ui['run-status'].classList.toggle('error', isError);
}

function showState(state, heading, detail, { ticker = '', retainHeading = false } = {}) {
  if (!retainHeading) {
    ui['company-sector'].textContent = '';
    ui.company.textContent = ticker || 'Research results';
    ui['snapshot-date'].textContent = '';
  }
  ui.results.setAttribute('aria-busy', String(state === 'working'));
  ui['result-content'].hidden = true;
  ui.rating.hidden = true;
  ui['result-state'].hidden = false;
  ui['result-state'].dataset.state = state;
  ui['state-title'].textContent = heading;
  ui['state-detail'].textContent = detail;
}

function showResultContent(note = '') {
  ui.results.setAttribute('aria-busy', 'false');
  ui['result-state'].hidden = true;
  ui['result-content'].hidden = false;
  ui['result-note'].textContent = note;
  ui['result-note'].hidden = !note;
  ui['edit-assumptions'].hidden = !operatingRuns.has(tickerValue()) && !active?.guided?.available;
}

function showStart() {
  showState('ready', 'Start with a ticker', operatingGeneration
    ? 'The builder will check the available evidence, then show the workbook and report when they’re ready.'
    : exchangeGeneration.includes('XTKS:7974') && !saved.size
    ? 'Enter XTKS:7974 and choose Generate research to download the Nintendo model and report.'
    : 'Check a ticker’s filing coverage or open a dated portfolio example.');
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
  const entry = operatingRuns.get(tickerValue()) ?? saved.get(tickerValue());
  const field = entry?.guided?.fields?.find((item) => item.target === ui.driver.value);
  ui['driver-basis']?.replaceChildren();
  if (ui['driver-basis']) ui['driver-basis'].hidden = !field?.basis;
  ui['driver-range'].hidden = true;
  if (!field) return;
  if (field.basis && ui['driver-basis']) {
    ui['driver-basis'].textContent = field.basis;
    if (field.source?.startsWith('https://')) {
      const link = node('a', '', field.source.startsWith('https://www.sec.gov/') ? 'SEC source' : 'Source');
      link.href = field.source;
      link.target = '_blank';
      link.rel = 'noopener';
      ui['driver-basis'].append(' · ', link);
    }
  }
  for (const constraint of ['min', 'max', 'step']) {
    if (field[constraint] === undefined) ui['driver-value'].removeAttribute(constraint);
    else ui['driver-value'].setAttribute(constraint, field[constraint]);
  }
  if (field.step === undefined) ui['driver-value'].step = 'any';
  ui['driver-value'].value = field.default;
  ui['driver-value'].setAttribute('aria-label', `New value (${field.unit})`);
  if (field.unit?.startsWith('decimal') && Number.isFinite(field.default)) {
    const bounds = Number.isFinite(field.min) && Number.isFinite(field.max)
      ? ` · Range ${assumptionPercent.format(field.min)} to ${assumptionPercent.format(field.max)}` : '';
    ui['driver-range'].textContent = `Base ${assumptionPercent.format(field.default)}${bounds}. Enter as a decimal.`;
    ui['driver-range'].hidden = false;
  }
}

function setFields(snapshot) {
  ui.driver.replaceChildren();
  for (const field of snapshot?.guided?.fields ?? []) {
    const option = node('option', '', `${field.label} (${field.unit})`);
    option.value = field.target;
    ui.driver.append(option);
  }
  setDriver();
}

function syncMode() {
  const guided = mode() === 'guided';
  const fresh = [generation, appleGeneration].find(item => item?.ticker === tickerValue());
  const rebuild = saved.get(tickerValue())?.regenerate;
  const screen = Boolean(tickerValue()) && tickerScreening && !fresh && !saved.has(tickerValue());
  const operating = operatingRuns.get(tickerValue());
  const selected = operating ?? saved.get(tickerValue());
  const available = api && selected?.ticker === tickerValue() && selected?.guided?.available && selected.guided.fields?.length > 0;
  const show = guided && available;
  ui['guided-controls'].hidden = !show;
  for (const id of ['driver', 'driver-value', 'run-case', 'reason']) ui[id].disabled = !show || busy;
  const caseControl = ui['run-case'].closest('div');
  caseControl.hidden = Boolean(operating);
  caseControl.parentElement.classList.toggle('single-input', Boolean(operating));
  ui['driver-value'].required = show;
  ui.reason.required = show;
  ui.ticker.disabled = busy;
  for (const radio of ui['run-form'].querySelectorAll('[name=mode]')) radio.disabled = busy;
  for (const option of ui.tickers.children) option.disabled = busy;
  ui['resume-run'].hidden = busy || !pendingRun || pendingRun.ticker !== tickerValue();
  if (fresh) {
    const [source] = oracleSources[fresh.source_mode];
    ui['mode-note'].textContent = guided ? 'Choose Automated for this ticker. Both files use the Base case and Excel inputs are editable.'
      : 'Generate both files using ' + source + '.';
  } else if (!guided && rebuild) {
    ui['mode-note'].textContent = 'Generate both files from the dated reference inputs.';
  } else if (!guided && screen) {
    ui['mode-note'].textContent = exchangeGeneration.includes(tickerValue())
      ? 'Generate an editable yen valuation and a matching PDF using Nintendo disclosures and Japanese market data.'
      : operatingGeneration
      ? 'Research the company and generate both files when the evidence supports a valuation.'
      : 'Check filing coverage. General U.S. valuation generation is disabled on this server.';
  } else if (!guided) {
    ui['mode-note'].textContent = saved.has(tickerValue()) ? 'Open the verified model and report from the selected research date.'
      : 'Automated mode starts with the ticker alone.';
  } else if (show) {
    ui['mode-note'].textContent = 'Change a forecast input and generate a matching Excel model and PDF.';
  } else if (api && active?.ticker === 'GD') {
    ui['mode-note'].textContent = 'General Dynamics supports the dated automated package. Guided forecasts are available for FedEx and Newmont with the modeling server configured.';
  } else {
    ui['mode-note'].textContent = operatingGeneration || exchangeGeneration.includes(tickerValue()) ? 'Run Automated first to unlock the available assumptions for this ticker.'
      : !saved.size && tickerScreening ? 'For a Guided demo, enter XTKS:7974 and run Automated first.'
      : 'Select an example that offers Guided mode.';
  }
  ui['run-label'].textContent = busy ? 'Research in progress' : guided ? 'Regenerate research'
    : fresh || rebuild || operatingGeneration && (screen || !tickerValue()) ? 'Generate research'
    : screen && exchangeGeneration.includes(tickerValue()) ? 'Generate research'
    : screen ? 'Check ticker' : 'Open research';
  ui['run-button'].disabled = busy || !tickerValue() || (!saved.size && !tickerScreening)
    || (guided && !available) || pendingRun?.ticker === tickerValue();
}

function drawScenarios(cases, close, metric) {
  const valid = Array.isArray(cases) && cases.length > 0;
  ui['scenario-section'].hidden = !valid;
  ui['scenario-chart'].replaceChildren();
  if (!valid) return;
  const values = cases.map((row) => row[metric]).concat(close);
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
    track.style.setProperty('--value', at(scenario[metric]));
    track.style.setProperty('--close', at(close));
    track.setAttribute('aria-hidden', 'true');
    track.append(node('i'));
    row.append(node('span', '', name), track, node('span', '', currency.format(scenario[metric])));
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
  let url;
  try { url = new URL(path, window.location.href); } catch { return null; }
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
  ui.downloads.closest('.downloads').hidden = ui.downloads.childElementCount === 0;
}

function render(snapshot, run = null) {
  const metrics = run?.summary ?? snapshot;
  const spotOnly = typeof run?.summary?.spot_upside === 'number';
  const generated = run?.mode === 'guided';
  ui.close.closest('.metrics').hidden = false;
  ui['scenario-section'].hidden = false;
  ui['company-sector'].textContent = `${snapshot.ticker} · ${snapshot.sector}`;
  ui.company.textContent = snapshot.company;
  const basis = spotOnly ? (generated ? 'Guided run · original evidence' : oracleSources[run.source_mode]?.[1] ?? 'Valuation run')
    : generated ? `${titleCase(run.case)} guided case` : run?.source_mode === 'reference_regeneration'
      ? 'Generated from dated inputs' : 'Dated research snapshot';
  ui['snapshot-date'].textContent = `${formatDate(run?.as_of ?? snapshot.as_of)} cutoff · ${basis}`;
  ui.rating.textContent = metrics.rating;
  ui.rating.dataset.rating = metrics.rating.toLowerCase();
  ui.rating.hidden = false;
  ui.close.textContent = currency.format(metrics.reference_close_usd);
  ui.close.previousElementSibling.textContent = 'Reference close';
  ui.close.title = `Reference close on ${formatDate(run?.price_date ?? snapshot.price_date)}`;
  ui.spot.textContent = currency.format(metrics.spot_value_usd);
  ui.spot.previousElementSibling.textContent = 'Spot value';
  ui.target.parentElement.hidden = spotOnly;
  if (!spotOnly) {
    ui.target.textContent = currency.format(metrics.target_price_usd);
    ui.target.title = `Target date ${formatDate(snapshot.target_date)}`;
  }
  const upside = spotOnly ? metrics.spot_upside : metrics.total_return;
  ui['total-return'].previousElementSibling.textContent = spotOnly ? 'Upside to close' : 'Modeled total return';
  ui['total-return'].textContent = percent.format(upside);
  ui['total-return'].dataset.direction = upside < 0 ? 'down' : 'up';
  ui['scenario-title'].textContent = spotOnly ? 'DCF value by case' : '12-month target by case';
  drawScenarios(run?.cases ?? (generated ? [] : snapshot.cases), metrics.reference_close_usd,
    spotOnly ? 'spot_value_usd' : 'target_price_usd');
  renderDownloads(publicPreview ? null : run?.downloads ?? snapshot.downloads);
  ui['download-status'].textContent = run?.downloads ? 'Verified generated files' : 'Verified snapshot';
  showResultContent();
}

function select(ticker) {
  const snapshot = saved.get(ticker);
  if (!snapshot) return;
  active = snapshot;
  ui.ticker.value = ticker;
  setTickerError();
  setStatus('');
  for (const button of ui.tickers.children) button.setAttribute('aria-pressed', String(button.dataset.ticker === ticker));
  setFields(snapshot);
  syncMode();
  render(snapshot);
}

function addChoices(snapshots) {
  for (const snapshot of snapshots) {
    saved.set(snapshot.ticker, snapshot);
    const quick = node('button', '', undefined);
    quick.type = 'button';
    quick.dataset.ticker = snapshot.ticker;
    quick.setAttribute('aria-label', snapshot.ticker);
    quick.setAttribute('aria-pressed', 'false');
    quick.append(node('strong', '', snapshot.ticker), node('span', '', snapshot.company));
    quick.addEventListener('click', () => {
      select(snapshot.ticker);
      bringResultsIntoView();
    });
    ui.tickers.append(quick);
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
  const payload = { ticker: snapshot.ticker, mode: 'guided', case: snapshot.baseRunId ? 'base' : ui['run-case'].value,
    overrides: [{ target: ui.driver.value, value: ui['driver-value'].valueAsNumber, reason }] };
  if (snapshot.baseRunId) payload.base_run_id = snapshot.baseRunId;
  await runResearch(snapshot, payload);
}

async function runResearch(snapshot, payload, resumeId = null) {
  if (busy) return;
  if (!resumeId && !payload.request_id) payload = { ...payload, request_id: crypto.randomUUID().replaceAll('-', '') };
  pendingRun = { id: resumeId, ticker: payload.ticker, snapshot, payload };
  rememberRun({ id: resumeId, payload });
  busy = true;
  syncMode();
  showState('working', resumeId ? 'Checking your run' : 'Starting research', 'This can take a few minutes.',
    { ticker: payload.ticker });
  setStatus('Starting research.');
  try {
    let run = resumeId ? await checkRun(resumeId) : await json('/api/runs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      signal: AbortSignal.timeout(15000),
    });
    const recoveryPayload = { ticker: payload.ticker, mode: payload.mode };
    if (run.id) {
      pendingRun = { id: run.id, ticker: payload.ticker, snapshot, payload: recoveryPayload };
      rememberRun({ id: run.id, payload: recoveryPayload });
    }
    for (let polls = 0; ['queued', 'running'].includes(run.state); polls += 1) {
      if (polls === 180) throw new Error('Research is still running. Check this run again to pick up where you left off.');
      const heading = run.state === 'queued' ? 'Waiting for the research engine'
        : payload.mode === 'guided' ? 'Rebuilding with your assumptions' : 'Researching the company';
      if (ui['state-title'].textContent !== heading) {
        showState('working', heading, 'The results and download links will appear here.', { ticker: payload.ticker });
        setStatus(heading);
      }
      await pause(2000);
      run = await checkRun(run.id);
    }
    pendingRun = null;
    if (run.state !== 'completed') {
      rememberRun(null);
      throw new Error(run.error ?? 'The research run could not finish.');
    }
    if (run.mode === 'automated' && run.source_mode === 'operating_research' && run.guided?.available) {
      const operating = { ...run.company, ticker: run.ticker, guided: run.guided, baseRunId: run.id };
      operatingRuns.set(run.ticker, operating);
      setFields(operating);
    }
    if (run.evidence) {
      const evidence = run.evidence;
      ui['company-sector'].textContent = [run.ticker, evidence.industry].filter(Boolean).join(' · ');
      ui.company.textContent = evidence.company;
      const localValuation = typeof run.summary?.spot_value_local === 'number';
      const hasValuation = localValuation || typeof run.summary?.spot_value_usd === 'number';
      const bankValuation = evidence.valuation_method === 'Bank distributions';
      const dateLabel = hasValuation
        ? `${bankValuation ? evidence.valuation_method : evidence.reporting_currency + ' DCF'} · ${formatDate(run.balance_date)} balance sheet`
        : evidence.home_listing?.effective
          ? `${evidence.home_listing.market_segment} · Listing verified on ${formatDate(evidence.home_listing.effective)}`
          : `${evidence.annual_periods} normalized annual periods · ${evidence.annual_filings} annual filings found`;
      ui['snapshot-date'].textContent = run.mode === 'guided' ? `Guided · ${dateLabel}` : dateLabel;
      ui.rating.hidden = !run.summary?.rating;
      if (run.summary?.rating) {
        ui.rating.textContent = run.summary.rating;
        ui.rating.dataset.rating = run.summary.rating.toLowerCase();
      }
      renderDownloads(publicPreview ? null : run.downloads);
      ui.close.closest('.metrics').hidden = !hasValuation;
      if (hasValuation) {
        const denomination = localValuation
          ? new Intl.NumberFormat('en-US', {style: 'currency', currency: evidence.reporting_currency}) : currency;
        ui.close.textContent = denomination.format(run.summary.reference_close_local ?? run.summary.reference_close_usd);
        ui.close.previousElementSibling.textContent = localValuation || bankValuation ? 'Market close' : 'U.S. ADS close';
        ui.close.title = `Close on ${formatDate(run.price_date)}`;
        ui.spot.textContent = denomination.format(run.summary.spot_value_local ?? run.summary.spot_value_usd);
        ui.spot.previousElementSibling.textContent = bankValuation ? 'Distribution value per share'
          : localValuation ? 'DCF per ordinary share' : 'DCF per U.S. ADS';
        const hasTarget = Number.isFinite(run.summary.target_value_usd);
        ui.target.parentElement.hidden = !hasTarget;
        if (hasTarget) {
          ui.target.textContent = denomination.format(run.summary.target_value_usd);
          ui.target.previousElementSibling.textContent = '12-month target';
        }
        const displayedReturn = hasTarget ? run.summary.target_total_return : run.summary.spot_upside;
        ui['total-return'].previousElementSibling.textContent = hasTarget ? '12-month total return' : 'Value vs market close';
        ui['total-return'].textContent = percent.format(displayedReturn);
        ui['total-return'].dataset.direction = displayedReturn < 0 ? 'down' : 'up';
      }
      ui['scenario-section'].hidden = true;
      if (run.downloads) {
        ui['download-status'].textContent = hasValuation ? 'Verified research files' : 'Verified pilot files';
        showResultContent(evidence.missing);
        setStatus('Research is ready. Download the Excel model and PDF.');
      } else {
        // Earlier filing checks were saved without a source mode. Their scope survives configuration changes.
        const screeningOnly = !run.source_mode || run.source_mode === 'filing_screen';
        showState('review', screeningOnly ? 'Filing check complete' : 'Evidence found · valuation unavailable',
          screeningOnly ? 'This was a filing check only. General U.S. valuation generation was disabled for this run. For an Excel model and PDF, enter XTKS:7974 and choose Automated.'
            : evidence.missing ?? 'The available evidence does not support a valuation package for this ticker.', { retainHeading: true });
        setStatus('Evidence review complete.');
      }
    } else {
      render(run.company ?? snapshot, run);
      setStatus('Research is ready. Download the Excel model and PDF.');
    }
    if (run.downloads && run.expires_at) {
      ui['download-status'].textContent = 'Available until ' + new Date(run.expires_at).toLocaleString();
    }
    bringResultsIntoView();
  } catch (error) {
    if ([400, 404, 409, 413, 415, 429].includes(error.status)) {
      pendingRun = null;
      rememberRun(null);
    }
    const detail = error.status === 404
      ? 'This run is no longer available. Submit the ticker to start a new run.' : error.message;
    showState(pendingRun ? 'review' : 'error', pendingRun ? 'Check your run again' : 'Research unavailable',
      detail, { ticker: payload.ticker });
    setStatus(pendingRun ? 'Run status paused.' : 'The request could not be completed.', !pendingRun);
    bringResultsIntoView();
  } finally {
    busy = false;
    syncMode();
  }
}

function checkRun(id) {
  return json(`/api/runs/${encodeURIComponent(id)}`, { signal: AbortSignal.timeout(15000) });
}

function bringResultsIntoView() {
  if (matchMedia('(max-width: 720px)').matches) ui.results.scrollIntoView({ block: 'start' });
}

ui['resume-run'].addEventListener('click', () => {
  if (!pendingRun) return;
  const { id, snapshot, payload } = pendingRun;
  void runResearch(snapshot, payload, id);
});
ui['edit-assumptions'].addEventListener('click', () => {
  ui['run-form'].elements.mode.value = 'guided';
  setFields(operatingRuns.get(tickerValue()) ?? active);
  syncMode();
  ui.driver.focus();
});

ui.ticker.setAttribute('aria-describedby', 'ticker-error');
ui.ticker.addEventListener('input', () => {
  if (busy) return;
  setTickerError();
  setStatus('');
  const symbol = tickerValue();
  active = null;
  for (const button of ui.tickers.children) button.setAttribute('aria-pressed', 'false');
  setFields(operatingRuns.get(symbol) ?? saved.get(symbol));
  syncMode();
  if (symbol) showState('ready', 'Ready to research', saved.has(symbol)
    ? 'Open the dated research package for this company.' : exchangeGeneration.includes(symbol)
    ? 'Generate the workbook and report from Nintendo’s published financial results.' : 'Submit this ticker to check available filings and valuation coverage.', { ticker: symbol });
  else showStart();
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
  if (busy || pendingRun?.ticker === tickerValue()) return;
  const operating = operatingRuns.get(tickerValue());
  if (operating && mode() === 'guided') {
    void runGuided(operating);
    return;
  }
  const fresh = [generation, appleGeneration].find(item => item?.ticker === tickerValue());
  if (fresh && mode() === 'automated') {
    void runResearch(fresh, { ticker: fresh.ticker });
    return;
  }
  const snapshot = saved.get(tickerValue());
  if (!snapshot) {
    if (tickerScreening && mode() === 'automated' && validTicker.test(tickerValue())) {
      void runResearch(null, { ticker: tickerValue() });
      return;
    }
    setTickerError(tickerScreening ? 'Enter a ticker such as INTU, BRK.B, or XTKS:7974.'
      : `Choose a portfolio ticker: ${[...saved.keys()].join(', ')}.`);
    ui.ticker.focus();
    return;
  }
  if (active?.ticker !== snapshot.ticker) select(snapshot.ticker);
  if (mode() === 'guided') void runGuided(snapshot);
  else if (snapshot.regenerate) void runResearch(snapshot, { ticker: snapshot.ticker });
  else {
    render(snapshot);
    setStatus(`${snapshot.ticker} research is ready.`);
    bringResultsIntoView();
  }
});

async function loadCatalog() {
  let catalog;
  try {
    if (!publicPreview) {
      try {
        catalog = await json('./api/companies', { signal: AbortSignal.timeout(privateBuilder ? 10000 : 2500) });
        api = Array.isArray(catalog.snapshots);
        if (privateBuilder && !api) throw new Error('The builder returned an invalid response.');
      } catch (error) {
        api = false;
        if (privateBuilder) throw error;
      }
    }
    if (!api) catalog = await json('./catalog.json');
    if (catalog.schema_version !== 1 || !Array.isArray(catalog.snapshots)
        || (!catalog.snapshots.length && !(api && catalog.live_only === true))) {
      throw new Error('Research catalog is missing.');
    }
    generation = api ? catalog.generation ?? null : null;
    appleGeneration = api ? catalog.apple_generation ?? null : null;
    tickerScreening = api && catalog.ticker_screening === true;
    operatingGeneration = api && catalog.operating_generation === true;
    exchangeGeneration = api && Array.isArray(catalog.exchange_generation) ? catalog.exchange_generation : [];
    addChoices(catalog.snapshots);
    if (publicPreview) {
      ui['workspace-title'].textContent = 'Equity research portfolio';
      ui['workspace-intro'].textContent = 'Explore three dated company valuations and their bear, base, and bull scenarios.';
      ui['site-context'].textContent = 'Dated portfolio research';
    } else if (operatingGeneration && catalog.live_only) {
      ui['workspace-intro'].textContent = 'Enter a ticker to research a U.S. company, or try XTKS:7974 for the Nintendo demo. Downloads appear when the valuation checks pass.';
    } else if (!operatingGeneration) {
      ui['workspace-intro'].textContent = catalog.live_only
        ? 'Enter XTKS:7974 for the Nintendo demo. General U.S. company research is set to filing checks only.'
        : tickerScreening
        ? 'Check a ticker’s filing coverage and open dated portfolio research.'
        : 'Open a dated research package to view the valuation and download its files.';
    }
    if (tickerScreening) {
      syncMode();
      showStart();
    } else select(saved.has('GD') ? 'GD' : catalog.snapshots[0].ticker);
    const recent = api && lastRun();
    if (recent) {
      ui.ticker.value = recent.payload.ticker;
      void runResearch(saved.get(recent.payload.ticker), recent.payload, recent.id);
    }
  } catch (error) {
    showState('error', 'Research unavailable', error.message);
    setStatus(codespaceHost ? 'Open your Codespace at github.com/codespaces, resume it, then reload this page.'
      : 'Reload the page once the builder is listening.', true);
  }
}

void loadCatalog();
