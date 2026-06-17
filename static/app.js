/* ─────────────────────────────────────────────────────────────────────────────
   app.js — Pronectis YouTube Automation Frontend
   WebSocket client + full UI logic
   ───────────────────────────────────────────────────────────────────────────── */

'use strict';

// ─────────────────────────────────────────────────────────────────────────────
// State
// ─────────────────────────────────────────────────────────────────────────────

const state = {
  ws:              null,
  wsConnected:     false,
  pipelineStatus:  'idle',   // idle | running | waiting_confirmation | done | error
  currentStep:     0,
  lastOutputs:     null,
  reconnectTimer:  null,
  autoUpload:      false,
};

// WebSocket event → step number mapping
const STEP_NAMES = ['', 'Descarga', 'Gemini IA', 'Fusión FFmpeg', 'YouTube', 'Limpieza'];

// ─────────────────────────────────────────────────────────────────────────────
// Initialization
// ─────────────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  connectWebSocket();
  loadSettings();
  refreshOutputs();
  refreshLog();

  // Character counters for editable fields
  document.getElementById('editTitle').addEventListener('input', updateTitleCount);
  document.getElementById('editDescription').addEventListener('input', updateDescCount);

  // URL validation on input
  document.getElementById('inputUrl').addEventListener('input', validateUrl);
});

// ─────────────────────────────────────────────────────────────────────────────
// WebSocket
// ─────────────────────────────────────────────────────────────────────────────

function connectWebSocket() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${proto}//${location.host}/app/ws`;

  state.ws = new WebSocket(wsUrl);

  state.ws.onopen = () => {
    state.wsConnected = true;
    clearTimeout(state.reconnectTimer);
    setConnectionStatus(true);
    logLine('→ Conectado al servidor Pronectis.', 'info');
  };

  state.ws.onclose = () => {
    state.wsConnected = false;
    setConnectionStatus(false);
    // Reconnect automatically
    state.reconnectTimer = setTimeout(connectWebSocket, 3000);
  };

  state.ws.onerror = () => {
    state.wsConnected = false;
    setConnectionStatus(false);
  };

  state.ws.onmessage = (event) => {
    try {
      handleWsMessage(JSON.parse(event.data));
    } catch (e) {
      console.error('WS parse error:', e);
    }
  };
}

function sendWs(msg) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(msg));
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// WebSocket message handler (the heart of real-time UI)
// ─────────────────────────────────────────────────────────────────────────────

function handleWsMessage(msg) {
  switch (msg.type) {

    case 'status':
      applyServerStatus(msg.status, msg.step, msg.outputs);
      break;

    case 'log':
      logLine(msg.msg);
      break;

    case 'step':
      setStep(msg.step, msg.name);
      break;

    case 'download_progress':
      showDownloadProgress(msg.pct, msg.speed, msg.eta);
      break;

    case 'ffmpeg_progress':
      showFfmpegProgress(msg.pct);
      break;

    case 'upload_progress':
      showUploadProgress(msg.pct);
      // Also update manual upload bar if visible
      setManualUploadProgress(msg.pct);
      break;

    case 'error_prompt':
      showErrorModal(msg.step, msg.detail);
      break;

    case 'pipeline_done':
      onPipelineDone(msg.outputs, msg.errors || []);
      break;

    case 'pipeline_aborted':
      onPipelineAborted(msg.reason);
      break;

    case 'pipeline_error':
      onPipelineError(msg.error);
      break;

    case 'youtube_done':
      onYoutubeDone(msg.video_id, msg.url, msg.privacy);
      break;

    case 'youtube_error':
      onYoutubeError(msg.error);
      break;

    case 'token_expired':
      toast('⚠️ ' + msg.msg, 'warn', 10000);
      break;

    default:
      console.log('WS msg:', msg);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Pipeline control
// ─────────────────────────────────────────────────────────────────────────────

async function startPipeline() {
  const url        = document.getElementById('inputUrl').value.trim();
  const outName    = document.getElementById('inputOut').value.trim() || 'final_output.mp4';
  const trimStart  = parseFloat(document.getElementById('inputTrimStart').value) || 0;
  const trimEnd    = parseFloat(document.getElementById('inputTrimEnd').value) || 0;
  const autoUpload = document.getElementById('toggleAutoUpload').checked;

  // Validate
  if (!url) {
    showFieldError('inputUrl', 'Ingresá la URL del video de YouTube.');
    return;
  }
  if (!url.includes('youtube.com') && !url.includes('youtu.be')) {
    showFieldError('inputUrl', 'La URL debe ser de YouTube (youtube.com o youtu.be).');
    return;
  }

  const body = { url, out_filename: outName, trim_start: trimStart, trim_end: trimEnd, auto_upload: autoUpload };

  try {
    const res = await fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      const err = await res.json();
      toast('❌ ' + (err.detail || 'Error al iniciar el pipeline.'), 'error');
      return;
    }

    // Success: update UI to running state
    setPipelineRunning();
    clearConsole();
    logLine(`→ Pipeline iniciado para: ${url}`, 'info');
    logLine(`→ Archivo de salida: ${outName}`, 'dim');
    if (trimStart > 0 || trimEnd > 0) {
      logLine(`→ Recorte configurado: -${trimStart}s inicio / -${trimEnd}s fin`, 'dim');
    }
    logLine(`→ Modo: ${autoUpload ? 'Subida automática activada' : 'Revisión manual antes de subir'}`, 'dim');

  } catch (e) {
    toast('❌ No se pudo conectar con el servidor.', 'error');
  }
}

function setPipelineRunning() {
  state.pipelineStatus = 'running';
  const btn  = document.getElementById('btnRun');
  const icon = document.getElementById('btnRunIcon');
  const lbl  = document.getElementById('btnRunLabel');
  btn.disabled = true;
  btn.classList.add('running');
  icon.innerHTML = '<span class="spinner"></span>';
  lbl.textContent = 'Pipeline en ejecución...';

  document.getElementById('progressCard').style.display = 'block';
  resetSteps();
  hidePipelineBadge();
  hideAllProgressBars();
}

function setPipelineIdle() {
  state.pipelineStatus = 'idle';
  const btn  = document.getElementById('btnRun');
  const icon = document.getElementById('btnRunIcon');
  const lbl  = document.getElementById('btnRunLabel');
  btn.disabled = false;
  btn.classList.remove('running');
  icon.textContent = '▶';
  lbl.textContent = 'Iniciar Pipeline';
}

// ─────────────────────────────────────────────────────────────────────────────
// Pipeline event handlers
// ─────────────────────────────────────────────────────────────────────────────

function applyServerStatus(status, step, outputs) {
  state.pipelineStatus = status;
  state.currentStep    = step;

  // Restore UI from server state (e.g., page refresh while running)
  if (status === 'running' || status === 'waiting_confirmation') {
    setPipelineRunning();
    document.getElementById('progressCard').style.display = 'block';
    if (step > 0) setStep(step, STEP_NAMES[step] || '');
    // Mark previous steps as done
    for (let i = 1; i < step; i++) markStepDone(i);
  } else if (status === 'done' && outputs && outputs.video) {
    state.lastOutputs = outputs;
    setPipelineIdle();
    populateReviewPanel(outputs);
    showPipelineBadge('done');
    refreshOutputs();
  } else {
    setPipelineIdle();
  }
}

function onPipelineDone(outputs, errors) {
  state.pipelineStatus = 'done';
  state.lastOutputs    = outputs;

  setPipelineIdle();
  markStepDone(state.currentStep);

  hideAllProgressBars();

  logLine('', '');
  logLine('════════════════════════════════════', 'info');
  logLine('✅ Pipeline completado exitosamente.', 'success');

  if (errors && errors.length > 0) {
    logLine(`⚠️  Se registraron ${errors.length} advertencia(s):`, 'warn');
    errors.forEach(e => logLine(`   ${e}`, 'warn'));
  }
  logLine('════════════════════════════════════', 'info');

  if (outputs && outputs.video) {
    logLine(`→ Video final: outputs/${outputs.video}`, 'success');
  }
  if (outputs && outputs.youtube_url) {
    logLine(`→ YouTube: ${outputs.youtube_url}`, 'success');
  }

  showPipelineBadge(errors.length > 0 ? 'warn' : 'done');

  // Populate review panel and switch to it
  if (outputs) {
    populateReviewPanel(outputs);
    toast('✅ Pipeline completado. Revisá el panel de "Revisión".', 'success', 6000);
    // If no auto-upload, gently nudge user to review
    if (!state.autoUpload) {
      setTimeout(() => showPanel('review'), 1500);
    }
  }

  refreshOutputs();
  refreshLog();
}

function onPipelineAborted(reason) {
  state.pipelineStatus = 'idle';
  setPipelineIdle();
  closeErrorModal();
  hideAllProgressBars();
  logLine('', '');
  logLine('❌ Pipeline cancelado: ' + reason, 'error');
  showPipelineBadge('error');
  toast('Pipeline cancelado y archivos limpiados.', 'warn');
}

function onPipelineError(error) {
  state.pipelineStatus = 'error';
  setPipelineIdle();
  closeErrorModal();
  hideAllProgressBars();
  logLine('', '');
  logLine('💥 ERROR FATAL: ' + error, 'error');
  showPipelineBadge('error');
  toast('❌ Error fatal en el pipeline: ' + error, 'error', 8000);
}

// ─────────────────────────────────────────────────────────────────────────────
// Error modal (ask_to_continue replacement)
// ─────────────────────────────────────────────────────────────────────────────

function showErrorModal(step, detail) {
  document.getElementById('modalStep').textContent = step;
  document.getElementById('modalDetail').textContent = detail;
  document.getElementById('errorModal').classList.add('open');

  // Update status dot
  const dot   = document.getElementById('statusDot');
  const label = document.getElementById('statusLabel');
  dot.className = 'status-dot waiting';
  label.textContent = 'Esperando decisión';

  logLine('', '');
  logLine(`⚠️  ERROR en: ${step}`, 'warn');
  logLine(`   ${detail}`, 'warn');
  logLine('   [Esperando tu decisión en el modal...]', 'dim');
}

function closeErrorModal() {
  document.getElementById('errorModal').classList.remove('open');
}

async function respondPrompt(action) {
  closeErrorModal();
  logLine(`→ Respuesta: ${action === 'continue' ? 'Continuar' : 'Cancelar'}`, 'dim');

  // Send via WebSocket (preferred) and also via REST fallback
  sendWs({ type: 'prompt_response', action });

  try {
    await fetch('/api/prompt-response', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    });
  } catch (e) {
    // WebSocket already sent it; REST is just a fallback
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Progress updates
// ─────────────────────────────────────────────────────────────────────────────

function setStep(stepNum, name) {
  state.currentStep = stepNum;

  // Mark previous steps done, current active
  for (let i = 1; i <= 5; i++) {
    const circle = document.getElementById(`step${i}circle`);
    const label  = document.getElementById(`step${i}label`);
    const conn   = document.getElementById(`conn${i}`);

    if (i < stepNum) {
      circle.className = 'step-circle done';
      circle.textContent = '✓';
      label.className = 'step-label done';
      if (conn) conn.className = 'step-connector done';
    } else if (i === stepNum) {
      circle.className = 'step-circle active';
      circle.textContent = i;
      label.className = 'step-label active';
      if (conn) conn.className = 'step-connector active';
    } else {
      circle.className = 'step-circle';
      circle.textContent = i;
      label.className = 'step-label';
      if (conn) conn.className = 'step-connector';
    }
  }

  const badge = document.getElementById('progressStepName');
  badge.textContent = name || STEP_NAMES[stepNum] || '';

  // Show/hide relevant progress bars based on step
  document.getElementById('downloadProgressWrap').style.display =
    stepNum === 1 ? 'block' : 'none';
  document.getElementById('ffmpegProgressWrap').style.display =
    (stepNum === 1 || stepNum === 3) ? 'block' : 'none';
  document.getElementById('uploadProgressWrap').style.display =
    stepNum === 4 ? 'block' : 'none';
}

function markStepDone(stepNum) {
  for (let i = 1; i <= stepNum && i <= 5; i++) {
    const circle = document.getElementById(`step${i}circle`);
    const label  = document.getElementById(`step${i}label`);
    const conn   = document.getElementById(`conn${i}`);
    circle.className = 'step-circle done';
    circle.textContent = '✓';
    label.className = 'step-label done';
    if (conn) conn.className = 'step-connector done';
  }
}

function showDownloadProgress(pct, speed, eta) {
  const wrap  = document.getElementById('downloadProgressWrap');
  const fill  = document.getElementById('downloadProgressFill');
  const pctEl = document.getElementById('downloadProgressPct');
  const label = document.getElementById('downloadProgressLabel');
  wrap.style.display = 'block';
  fill.style.width   = pct + '%';
  pctEl.textContent  = pct + '%';
  label.textContent  = speed ? `Descargando... ${speed} · ETA: ${eta}` : 'Descargando...';
}

function showFfmpegProgress(pct) {
  const wrap  = document.getElementById('ffmpegProgressWrap');
  const fill  = document.getElementById('ffmpegProgressFill');
  const pctEl = document.getElementById('ffmpegProgressPct');
  wrap.style.display = 'block';
  fill.style.width   = pct + '%';
  pctEl.textContent  = pct + '%';
}

function showUploadProgress(pct) {
  const wrap  = document.getElementById('uploadProgressWrap');
  const fill  = document.getElementById('uploadProgressFill');
  const pctEl = document.getElementById('uploadProgressPct');
  wrap.style.display = 'block';
  fill.style.width   = pct + '%';
  pctEl.textContent  = pct + '%';
}

function hideAllProgressBars() {
  ['downloadProgressWrap', 'ffmpegProgressWrap', 'uploadProgressWrap'].forEach(id => {
    document.getElementById(id).style.display = 'none';
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Steps helpers
// ─────────────────────────────────────────────────────────────────────────────

function resetSteps() {
  for (let i = 1; i <= 5; i++) {
    const circle = document.getElementById(`step${i}circle`);
    const label  = document.getElementById(`step${i}label`);
    const conn   = document.getElementById(`conn${i}`);
    circle.className  = 'step-circle';
    circle.textContent = i;
    label.className   = 'step-label';
    if (conn) conn.className = 'step-connector';
  }
  document.getElementById('progressStepName').textContent = 'Iniciando...';
}

// ─────────────────────────────────────────────────────────────────────────────
// Console
// ─────────────────────────────────────────────────────────────────────────────

function logLine(msg, cls) {
  const console_el = document.getElementById('console');

  // Remove empty state placeholder
  const placeholder = document.getElementById('consoleEmpty');
  if (placeholder) placeholder.remove();

  const line = document.createElement('div');
  line.className = 'log-line' + (cls ? ' ' + cls : '');

  // Auto-classify log lines by content
  if (!cls) {
    const low = msg.toLowerCase();
    if (msg.startsWith('✅') || msg.startsWith('[✓]') || low.includes('éxit') ||
        low.includes('completad') || low.includes('finaliz')) {
      line.className = 'log-line success';
    } else if (msg.startsWith('❌') || msg.startsWith('💥') || low.includes('error') ||
               low.includes('fallo') || low.includes('crítico')) {
      line.className = 'log-line error';
    } else if (msg.startsWith('⚠️') || low.includes('aviso') || low.includes('advertencia')) {
      line.className = 'log-line warn';
    } else if (msg.startsWith('[') && (msg.includes('/5]') || msg.includes('YouTube]'))) {
      line.className = 'log-line info';
    }
  }

  line.textContent = msg;
  console_el.appendChild(line);
  console_el.scrollTop = console_el.scrollHeight;
}

function clearConsole() {
  const el = document.getElementById('console');
  el.innerHTML = '';
  const ph = document.createElement('div');
  ph.className = 'console-empty'; ph.id = 'consoleEmpty';
  ph.innerHTML = '<div class="console-empty-icon">💤</div>' +
                 '<div class="console-empty-text">Consola limpiada. Iniciá un pipeline para ver los logs.</div>';
  el.appendChild(ph);
}

// ─────────────────────────────────────────────────────────────────────────────
// Review panel
// ─────────────────────────────────────────────────────────────────────────────

function populateReviewPanel(outputs) {
  document.getElementById('reviewEmpty').style.display   = 'none';
  document.getElementById('reviewContent').style.display = 'block';

  // Video player
  const player      = document.getElementById('videoPlayer');
  const placeholder = document.getElementById('videoPlaceholder');
  const chip        = document.getElementById('videoChip');

  if (outputs.video) {
    const videoUrl = `/api/outputs/${encodeURIComponent(outputs.video)}`;
    player.src             = videoUrl;
    player.style.display   = 'block';
    placeholder.style.display = 'none';
    chip.style.display     = 'inline-flex';

    const dlVideo = document.getElementById('btnDownloadVideo');
    dlVideo.href         = videoUrl;
    dlVideo.download     = outputs.video;
    dlVideo.style.display = 'inline-flex';
  }

  // Download buttons for text files
  if (outputs.desc_file) {
    const dlDesc = document.getElementById('btnDownloadDesc');
    dlDesc.href         = `/api/outputs/${encodeURIComponent(outputs.desc_file)}`;
    dlDesc.download     = outputs.desc_file;
    dlDesc.style.display = 'inline-flex';
  }
  if (outputs.titulo_file) {
    const dlTitle = document.getElementById('btnDownloadTitle');
    dlTitle.href         = `/api/outputs/${encodeURIComponent(outputs.titulo_file)}`;
    dlTitle.download     = outputs.titulo_file;
    dlTitle.style.display = 'inline-flex';
  }

  // Editable fields
  const titleInput = document.getElementById('editTitle');
  const descInput  = document.getElementById('editDescription');
  titleInput.value  = outputs.titulo || '';
  descInput.value   = outputs.descripcion || '';
  updateTitleCount();
  updateDescCount();

  // If already uploaded to YouTube automatically
  if (outputs.youtube_url) {
    showYoutubeSuccess(outputs.youtube_id, outputs.youtube_url, 'private');
    document.getElementById('btnUploadYT').disabled = true;
    document.getElementById('btnUploadYT').textContent = '✓ Ya publicado';
  } else {
    document.getElementById('ytSuccessBanner').classList.add('hidden');
    document.getElementById('btnUploadYT').disabled = false;
    document.getElementById('btnUploadYT').innerHTML = '📤 Subir a YouTube';
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// YouTube upload (manual)
// ─────────────────────────────────────────────────────────────────────────────

async function uploadToYouTube() {
  if (!state.lastOutputs || !state.lastOutputs.video) {
    toast('❌ No hay video disponible para subir.', 'error');
    return;
  }

  const title       = document.getElementById('editTitle').value.trim();
  const description = document.getElementById('editDescription').value.trim();
  const privacy     = document.getElementById('privacySelect').value;

  if (!title) {
    toast('❌ El título no puede estar vacío.', 'error');
    document.getElementById('editTitle').focus();
    return;
  }
  if (!description) {
    toast('❌ La descripción no puede estar vacía.', 'error');
    document.getElementById('editDescription').focus();
    return;
  }

  const btn = document.getElementById('btnUploadYT');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Subiendo...';

  const progressWrap = document.getElementById('manualUploadProgress');
  progressWrap.classList.add('visible');
  setManualUploadProgress(0);

  try {
    const res = await fetch('/api/upload-youtube', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_filename: state.lastOutputs.video,
        title,
        description,
        privacy_status: privacy,
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      toast('❌ ' + (err.detail || 'Error al iniciar la subida.'), 'error');
      btn.disabled = false;
      btn.innerHTML = '📤 Subir a YouTube';
      progressWrap.classList.remove('visible');
    }
    // Result handled via WebSocket (youtube_done / youtube_error)

  } catch (e) {
    toast('❌ Error de conexión: ' + e.message, 'error');
    btn.disabled = false;
    btn.innerHTML = '📤 Subir a YouTube';
    progressWrap.classList.remove('visible');
  }
}

function setManualUploadProgress(pct) {
  document.getElementById('manualUploadFill').style.width = pct + '%';
  document.getElementById('manualUploadPct').textContent  = pct + '%';
}

function onYoutubeDone(videoId, url, privacy) {
  const progressWrap = document.getElementById('manualUploadProgress');
  progressWrap.classList.remove('visible');
  setManualUploadProgress(100);

  showYoutubeSuccess(videoId, url, privacy);

  const btn = document.getElementById('btnUploadYT');
  btn.disabled = true;
  btn.innerHTML = '✓ Publicado en YouTube';

  logLine(`✅ YouTube: ${url}`, 'success');
  toast(`✅ Video publicado en YouTube (${privacy})!`, 'success', 8000);

  if (state.lastOutputs) {
    state.lastOutputs.youtube_id  = videoId;
    state.lastOutputs.youtube_url = url;
  }
  refreshLog();
}

function showYoutubeSuccess(videoId, url, privacy) {
  const banner = document.getElementById('ytSuccessBanner');
  const link   = document.getElementById('ytSuccessLink');
  banner.classList.remove('hidden');
  link.href        = url;
  link.textContent = `Ver en YouTube (${url}) →`;
}

function onYoutubeError(error) {
  const progressWrap = document.getElementById('manualUploadProgress');
  progressWrap.classList.remove('visible');

  const btn = document.getElementById('btnUploadYT');
  btn.disabled = false;
  btn.innerHTML = '📤 Reintentar Subida';

  logLine('❌ Error en subida a YouTube: ' + error, 'error');
  toast('❌ Error en subida: ' + error, 'error', 8000);
}

// ─────────────────────────────────────────────────────────────────────────────
// Navigation
// ─────────────────────────────────────────────────────────────────────────────

function showPanel(name) {
  const panels  = ['pipeline', 'review', 'history', 'settings'];
  const navBtns = {
    pipeline: 'navPipeline', review: 'navReview',
    history: 'navHistory', settings: 'navSettings',
  };

  panels.forEach(p => {
    document.getElementById(`panel${capitalize(p)}`).classList.remove('active');
    const nb = document.getElementById(navBtns[p]);
    if (nb) { nb.classList.remove('active'); nb.removeAttribute('aria-current'); }
  });

  document.getElementById(`panel${capitalize(name)}`).classList.add('active');
  const activeBtn = document.getElementById(navBtns[name]);
  if (activeBtn) { activeBtn.classList.add('active'); activeBtn.setAttribute('aria-current', 'page'); }

  // Lazy-load panel data
  if (name === 'history') { refreshOutputs(); refreshLog(); }
  if (name === 'settings') loadSettings();
}

function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

// ─────────────────────────────────────────────────────────────────────────────
// Settings
// ─────────────────────────────────────────────────────────────────────────────

async function loadSettings() {
  try {
    const res  = await fetch('/api/settings');
    const data = await res.json();
    document.getElementById('settingLandingPages').value = data.landing_pages       || '';
    document.getElementById('settingDescripcion').value  = data.descripcion_ejemplo || '';
    document.getElementById('settingTitulos').value      = data.titulos_ejemplo     || '';
  } catch (e) {
    toast('No se pudieron cargar los ajustes.', 'error');
  }
}

async function saveSettings(key, inputId) {
  const content = document.getElementById(inputId).value;
  try {
    const res = await fetch('/api/settings', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ key, content }),
    });
    const data = await res.json();
    if (data.ok) toast(`✅ ${data.message}`, 'success');
    else         toast('Error al guardar.', 'error');
  } catch (e) {
    toast('Error de conexión al guardar.', 'error');
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// History & outputs
// ─────────────────────────────────────────────────────────────────────────────

async function refreshOutputs() {
  try {
    const res   = await fetch('/api/outputs');
    const files = await res.json();
    renderOutputsList(files);
  } catch (e) {
    // silently ignore
  }
}

function renderOutputsList(files) {
  const list = document.getElementById('outputsList');
  if (!files || files.length === 0) {
    list.innerHTML = '<div class="empty-state"><div class="empty-icon">📂</div>' +
                     '<div class="empty-text">Sin archivos generados todavía.</div></div>';
    return;
  }

  list.innerHTML = files.map(f => {
    const icon  = fileIcon(f.ext);
    const size  = formatSize(f.size);
    const date  = new Date(f.modified).toLocaleString('es-AR', {
      day:'2-digit', month:'2-digit', year:'numeric',
      hour:'2-digit', minute:'2-digit',
    });
    const dlUrl = `/api/outputs/${encodeURIComponent(f.name)}`;
    const isVid = f.ext === '.mp4';

    return `
      <div class="output-item">
        <div class="output-icon">${icon}</div>
        <div class="output-info">
          <div class="output-name">${escapeHtml(f.name)}</div>
          <div class="output-meta">${size} · ${date}</div>
        </div>
        <div class="output-actions">
          ${isVid ? `<button class="btn btn-ghost btn-sm btn-icon" title="Ver en revisión"
            onclick='previewVideo("${escapeHtml(f.name)}")'>👁</button>` : ''}
          <a class="btn btn-ghost btn-sm btn-icon" href="${dlUrl}"
             download="${escapeHtml(f.name)}" title="Descargar">⬇</a>
        </div>
      </div>`;
  }).join('');
}

function previewVideo(filename) {
  const player = document.getElementById('videoPlayer');
  const placeholder = document.getElementById('videoPlaceholder');
  player.src = `/api/outputs/${encodeURIComponent(filename)}`;
  player.style.display = 'block';
  placeholder.style.display = 'none';
  document.getElementById('reviewEmpty').style.display   = 'none';
  document.getElementById('reviewContent').style.display = 'block';
  const dlBtn = document.getElementById('btnDownloadVideo');
  dlBtn.href = `/api/outputs/${encodeURIComponent(filename)}`;
  dlBtn.download = filename;
  dlBtn.style.display = 'inline-flex';
  showPanel('review');
  toast('Video cargado en el panel de revisión.', 'success');
}

async function refreshLog() {
  try {
    const res  = await fetch('/api/log');
    const data = await res.json();
    document.getElementById('logContent').textContent = data.content || 'Sin registros.';
  } catch (e) {
    // silently ignore
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Connection status
// ─────────────────────────────────────────────────────────────────────────────

function setConnectionStatus(connected) {
  const dot   = document.getElementById('statusDot');
  const label = document.getElementById('statusLabel');

  if (connected) {
    dot.className   = 'status-dot online';
    label.textContent = 'Conectado';
    updateStatusFromPipeline();
  } else {
    dot.className   = 'status-dot';
    label.textContent = 'Reconectando...';
  }
}

function updateStatusFromPipeline() {
  const dot   = document.getElementById('statusDot');
  const label = document.getElementById('statusLabel');
  switch (state.pipelineStatus) {
    case 'running':
      dot.className   = 'status-dot running';
      label.textContent = 'Pipeline ejecutándose';
      break;
    case 'waiting_confirmation':
      dot.className   = 'status-dot waiting';
      label.textContent = 'Esperando decisión';
      break;
    case 'done':
      dot.className   = 'status-dot online';
      label.textContent = 'Pipeline completado';
      break;
    case 'error':
      dot.className   = 'status-dot error';
      label.textContent = 'Error en pipeline';
      break;
    default:
      dot.className   = 'status-dot online';
      label.textContent = 'Listo';
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Pipeline status badge
// ─────────────────────────────────────────────────────────────────────────────

function showPipelineBadge(type) {
  const badge = document.getElementById('pipelineStatusBadge');
  badge.style.display = 'inline-flex';
  const map = {
    'done':  { cls: 'chip chip-success', text: '✓ Completado' },
    'warn':  { cls: 'chip chip-yellow',  text: '⚠ Con advertencias' },
    'error': { cls: 'chip chip-error',   text: '✕ Error' },
  };
  const cfg = map[type] || map['done'];
  badge.className   = cfg.cls;
  badge.textContent = cfg.text;
  updateStatusFromPipeline();
}

function hidePipelineBadge() {
  document.getElementById('pipelineStatusBadge').style.display = 'none';
}

// ─────────────────────────────────────────────────────────────────────────────
// Toggle auto-upload
// ─────────────────────────────────────────────────────────────────────────────

function toggleAutoUpload() {
  const chk = document.getElementById('toggleAutoUpload');
  chk.checked = !chk.checked;
  onAutoUploadChange();
}

function onAutoUploadChange() {
  const checked = document.getElementById('toggleAutoUpload').checked;
  state.autoUpload = checked;
  const desc = document.getElementById('autoUploadDesc');
  desc.textContent = checked
    ? 'Activado — se subirá automáticamente a YouTube al finalizar (privado)'
    : 'Desactivado — podrás revisar y editar antes de subir';
}

// ─────────────────────────────────────────────────────────────────────────────
// Form helpers
// ─────────────────────────────────────────────────────────────────────────────

function validateUrl() {
  const url   = document.getElementById('inputUrl').value.trim();
  const hint  = document.getElementById('urlHint');
  const input = document.getElementById('inputUrl');

  if (!url) {
    hint.textContent = '';
    input.style.borderColor = '';
    return;
  }
  if (url.includes('youtube.com') || url.includes('youtu.be')) {
    hint.textContent = '✓ URL de YouTube detectada';
    hint.style.color = 'var(--green)';
    input.style.borderColor = 'var(--green)';
  } else {
    hint.textContent = '✕ Debe ser una URL de YouTube';
    hint.style.color = 'var(--red)';
    input.style.borderColor = 'var(--red)';
  }
}

function showFieldError(fieldId, msg) {
  const field = document.getElementById(fieldId);
  field.style.borderColor = 'var(--red)';
  field.style.boxShadow   = '0 0 0 3px var(--red-dim)';
  field.focus();
  toast('❌ ' + msg, 'error');
  setTimeout(() => {
    field.style.borderColor = '';
    field.style.boxShadow   = '';
  }, 3000);
}

function updateTitleCount() {
  const len  = document.getElementById('editTitle').value.length;
  const el   = document.getElementById('titleCharCount');
  el.textContent = `${len} / 100 caracteres`;
  el.style.color = len > 100 ? 'var(--red)' : len > 80 ? 'var(--yellow)' : '';
}

function updateDescCount() {
  const len = document.getElementById('editDescription').value.length;
  const el  = document.getElementById('descCharCount');
  el.textContent = `${len.toLocaleString('es-AR')} caracteres`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Toast notifications
// ─────────────────────────────────────────────────────────────────────────────

function toast(msg, type = 'info', duration = 4000) {
  const container = document.getElementById('toastContainer');
  const el        = document.createElement('div');
  const icons     = { success: '✅', error: '❌', warn: '⚠️', info: 'ℹ️' };
  el.className    = `toast ${type}`;
  el.innerHTML    = `<span class="toast-icon">${icons[type] || 'ℹ️'}</span>
                     <span class="toast-msg">${escapeHtml(msg)}</span>`;
  container.appendChild(el);

  setTimeout(() => {
    el.style.animation = 'toastOut 0.3s ease forwards';
    setTimeout(() => el.remove(), 300);
  }, duration);
}

// ─────────────────────────────────────────────────────────────────────────────
// Utilities
// ─────────────────────────────────────────────────────────────────────────────

function fileIcon(ext) {
  const map = { '.mp4': '🎬', '.txt': '📄', '.log': '📋', '.json': '🗂' };
  return map[ext] || '📁';
}

function formatSize(bytes) {
  if (bytes < 1024)        return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1024 ** 3)   return (bytes / 1024 / 1024).toFixed(1) + ' MB';
  return (bytes / 1024 ** 3).toFixed(2) + ' GB';
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
