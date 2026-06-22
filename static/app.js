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

function updateTitleCount() {
  const el = document.getElementById('editTitle');
  const countEl = document.getElementById('editTitleCount');
  if (el && countEl) {
    countEl.textContent = `${el.value.length}/100`;
  }
}

function updateDescCount() {
  const el = document.getElementById('editDescription');
  const countEl = document.getElementById('editDescCount');
  if (el && countEl) {
    countEl.textContent = `${el.value.length}/5000`;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// CANCEL PIPELINE
// ─────────────────────────────────────────────────────────────────────────────
async function cancelPipeline() {
  if (state.pipelineStatus === 'idle') return;
  try {
    const res = await fetch('/api/cancel-pipeline', { method: 'POST' });
    if (res.ok) {
      toast('Pipeline cancelado.', 'success');
      setPipelineIdle();
      showPipelineBadge('idle');
    }
  } catch (e) {
    toast('Error cancelando pipeline.', 'error');
  }
}

// WebSocket event → step number mapping
const STEP_NAMES = ['', 'Descarga', 'Gemini IA', 'Fusión FFmpeg', 'YouTube', 'Limpieza'];

// ─────────────────────────────────────────────────────────────────────────────
// Initialization
// ─────────────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  connectWebSocket();
  loadSettings();
  loadHistory();
  refreshLog();

  // Character counters for editable fields
  document.getElementById('editTitle').addEventListener('input', updateTitleCount);
  document.getElementById('editDescription').addEventListener('input', updateDescCount);

  // URL validation on input
  document.getElementById('inputUrl').addEventListener('input', validateUrl);

  // Context counter
  document.getElementById('inputExtraContext').addEventListener('input', updateContextCounter);
});

function updateContextCounter() {
  const len = document.getElementById('inputExtraContext').value.length;
  const counter = document.getElementById('contextCounter');
  const mode = document.getElementById('videoSourceMode').value;
  const urlYtVal = document.getElementById('inputUrlYt')?.value || '';
  const urlDriveVal = document.getElementById('inputUrlDrive')?.value || '';
  const urlVal = mode === 'youtube' ? urlYtVal : (mode === 'drive' ? urlDriveVal : '');
  const needsMin = (mode === 'local') || (mode === 'drive');
  
  if (needsMin) {
    counter.textContent = `Mínimo: 50 caracteres (Van: ${len})`;
    counter.style.color = len >= 50 ? 'var(--green)' : 'var(--red)';
  } else {
    counter.textContent = `${len} chars`;
    counter.style.color = len > 0 ? 'var(--text-1)' : 'var(--text-2)';
  }
}

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

    case 'shutdown_warning':
      showShutdownModal(msg.timeout);
      break;

    default:
      console.log('WS msg:', msg);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Shutdown Modal
// ─────────────────────────────────────────────────────────────────────────────

let shutdownTimer = null;
let shutdownSeconds = 30;

function showShutdownModal(timeout) {
  shutdownSeconds = timeout || 30;
  document.getElementById('shutdownCountdown').textContent = shutdownSeconds;
  document.getElementById('shutdownModal').classList.add('open');
  
  clearInterval(shutdownTimer);
  shutdownTimer = setInterval(() => {
    shutdownSeconds--;
    if (shutdownSeconds <= 0) {
      clearInterval(shutdownTimer);
      document.getElementById('shutdownModal').classList.remove('open');
    } else {
      document.getElementById('shutdownCountdown').textContent = shutdownSeconds;
    }
  }, 1000);
}

function respondShutdown(action) {
  clearInterval(shutdownTimer);
  document.getElementById('shutdownModal').classList.remove('open');
  
  if (action === 'cancel') {
    sendWs({ type: 'cancel_shutdown' });
    toast('Apagado automático cancelado. Temporizador reiniciado.', 'info');
  } else if (action === 'confirm') {
    sendWs({ type: 'confirm_shutdown' });
    toast('Apagando la máquina virtual...', 'warn');
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Pipeline control
// ─────────────────────────────────────────────────────────────────────────────

async function startPipeline() {
  const mode       = document.getElementById('videoSourceMode').value; // 'youtube', 'drive', 'local'
  const fileInput  = document.getElementById('inputFile');
  const projectName= (document.getElementById('inputProjectName').value || '').trim();
  const trimStart  = parseFloat(document.getElementById('inputTrimStart').value) || 0;
  const trimEnd    = parseFloat(document.getElementById('inputTrimEnd').value) || 0;
  const videoFormat= document.getElementById('selectVideoFormat').value;
  const extraContext = document.getElementById('inputExtraContext').value.trim();
  const autoUpload = false;

  let localFile = "";
  let finalUrl = "";

  // Validate
  if (mode === 'youtube') {
    const url = document.getElementById('inputUrlYt').value.trim();
    if (!url) {
      showFieldError('inputUrlYt', 'Ingresá un enlace público de YouTube.');
      return;
    }
    if (!url.includes('youtube.com') && !url.includes('youtu.be')) {
      showFieldError('inputUrlYt', 'La URL debe ser de YouTube.');
      return;
    }
    finalUrl = url;
  } else if (mode === 'drive') {
    const url = document.getElementById('inputUrlDrive').value.trim();
    if (!url) {
      showFieldError('inputUrlDrive', 'Ingresá un enlace público de Google Drive.');
      return;
    }
    if (!url.includes('drive.google.com')) {
      showFieldError('inputUrlDrive', 'La URL debe ser de Google Drive.');
      return;
    }
    if (!url.includes('/file/d/') && !url.includes('id=')) {
      showFieldError('inputUrlDrive', 'Asegurate de que sea un enlace a un archivo (no a una carpeta).');
      return;
    }
    if (extraContext.length < 50) {
      showFieldError('inputExtraContext', 'Para videos de Google Drive el contexto es obligatorio (mínimo 50 caracteres).');
      return;
    }
    finalUrl = url;
  } else {
    if (fileInput.files.length === 0) {
      showFieldError('inputFile', 'Seleccioná un archivo de video local.');
      return;
    }
    if (extraContext.length < 50) {
      showFieldError('inputExtraContext', 'Para videos locales el contexto es obligatorio (mínimo 50 caracteres).');
      return;
    }

    // Upload file first
    const file = fileInput.files[0];
    const formData = new FormData();
    formData.append('file', file);
    
    document.getElementById('uploadProgressContainer').style.display = 'block';
    document.getElementById('uploadProgressBar').style.width = '0%';
    document.getElementById('uploadProgressText').textContent = '0%';

    try {
      // Usar XMLHttpRequest para tener progreso real de subida
      const uploadFilename = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/upload-video', true);
        
        xhr.upload.onprogress = function(e) {
          if (e.lengthComputable) {
            const percentComplete = Math.round((e.loaded / e.total) * 100);
            document.getElementById('uploadProgressBar').style.width = percentComplete + '%';
            document.getElementById('uploadProgressText').textContent = percentComplete + '%';
          }
        };

        xhr.onload = function() {
          if (xhr.status >= 200 && xhr.status < 300) {
            const res = JSON.parse(xhr.responseText);
            resolve(res.filename);
          } else {
            reject(new Error('Error al subir el archivo: ' + xhr.responseText));
          }
        };

        xhr.onerror = function() {
          reject(new Error('Error de red al intentar subir el archivo.'));
        };

        xhr.send(formData);
      });
      
      localFile = uploadFilename;
      document.getElementById('uploadProgressContainer').style.display = 'none';

    } catch (e) {
      toast('❌ Falló la subida del video: ' + e.message, 'error');
      document.getElementById('uploadProgressContainer').style.display = 'none';
      return;
    }
  }

  const body = { 
    url: finalUrl, 
    local_file: localFile,
    project_name: projectName,
    trim_start: trimStart, 
    trim_end: trimEnd, 
    video_format: videoFormat, 
    extra_context: extraContext, 
    auto_upload: autoUpload 
  };

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
    logLine(`→ Pipeline iniciado para: ${finalUrl || 'Archivo Local'}`, 'info');
    if (trimStart > 0 || trimEnd > 0) {
      logLine(`→ Recorte configurado: -${trimStart}s inicio / -${trimEnd}s fin`, 'dim');
    }
    logLine(`→ Modo: ${autoUpload ? 'Subida automática activada' : 'Revisión manual antes de subir'}`, 'dim');

  } catch (e) {
    console.error(e);
    toast('❌ Ocurrió un error al iniciar el pipeline.', 'error');
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

  // Show cancel button
  const cancelBtn = document.getElementById('btnCancelPipeline');
  if (cancelBtn) cancelBtn.style.display = 'inline-flex';

  // Open console card
  const consoleCard = document.getElementById('consoleCard');
  if (consoleCard) consoleCard.open = true;
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

  // Hide cancel button
  const cancelBtn = document.getElementById('btnCancelPipeline');
  if (cancelBtn) cancelBtn.style.display = 'none';
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
    // Always switch to review tab
    setTimeout(() => showPanel('review'), 800);
  }

  refreshLog();
  loadHistory();
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

  // Project name banner
  const banner = document.getElementById('projectNameBanner');
  const nameLabel = document.getElementById('projectNameLabel');
  const ytLinkBtn = document.getElementById('projectYoutubeLink');
  
  if (outputs.project_name && banner && nameLabel) {
    nameLabel.textContent = outputs.project_name;
    banner.style.display = 'flex';
    if (outputs.youtube_url && ytLinkBtn) {
      ytLinkBtn.href = outputs.youtube_url;
      ytLinkBtn.style.display = 'inline-block';
    } else if (ytLinkBtn) {
      ytLinkBtn.style.display = 'none';
    }
  } else if (banner) {
    banner.style.display = 'none';
  }

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
    dlVideo.download     = outputs.video.split('/').pop() || 'video.mp4';
    dlVideo.style.display = 'inline-flex';
  }

  // Download buttons for text files
  if (outputs.desc_file) {
    const dlDesc = document.getElementById('btnDownloadDesc');
    dlDesc.href         = `/api/outputs/${encodeURIComponent(outputs.desc_file)}`;
    dlDesc.download     = 'descripcion.txt';
    dlDesc.style.display = 'inline-flex';
  }
  if (outputs.titulo_file) {
    const dlTitle = document.getElementById('btnDownloadTitle');
    dlTitle.href         = `/api/outputs/${encodeURIComponent(outputs.titulo_file)}`;
    dlTitle.download     = 'titulo.txt';
    dlTitle.style.display = 'inline-flex';
  }

  // Editable fields
  const titleInput = document.getElementById('editTitle');
  const descInput  = document.getElementById('editDescription');
  titleInput.value  = outputs.titulo || '';
  descInput.value   = outputs.descripcion || '';
  updateTitleCount();
  updateDescCount();

  // Tags chips
  setTagsFromString(outputs.tags || '');
  
  const isShort = outputs.video_format && outputs.video_format.includes('short');
  
  if (outputs.thumbnail && !isShort) {
    document.getElementById('currentThumbnailFile').value = outputs.thumbnail;
    const thumbImg = document.getElementById('reviewThumbnail');
    thumbImg.src = `/api/outputs/${encodeURIComponent(outputs.thumbnail)}?t=` + new Date().getTime();
    thumbImg.style.display = 'inline-block';
    document.getElementById('thumbnailWrapper').style.display = 'block';
  } else {
    document.getElementById('currentThumbnailFile').value = '';
    document.getElementById('reviewThumbnail').style.display = 'none';
    document.getElementById('thumbnailWrapper').style.display = 'none';
  }
  
  // Hide thumbnail section entirely if format is short
  const thumbCard = document.getElementById('thumbnailCard');
  if (thumbCard) {
    if (isShort) {
      thumbCard.style.display = 'none';
    } else {
      thumbCard.style.display = 'block';
    }
  }

  loadPlaylists();

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
// Tags chip system
// ─────────────────────────────────────────────────────────────────────────────

let _tags = [];

function setTagsFromString(str) {
  _tags = str.split(',').map(t => t.trim()).filter(Boolean);
  renderTagChips();
}

function getTagsAsString() {
  return _tags.join(', ');
}

function renderTagChips() {
  const list = document.getElementById('tagsChipList');
  if (!list) return;
  list.innerHTML = '';
  _tags.forEach((tag, idx) => {
    const chip = document.createElement('span');
    chip.className = 'tag-chip';
    chip.innerHTML = `${escapeHtml(tag)}<button class="tag-chip-remove" onclick="removeTag(${idx})" aria-label="Eliminar ${escapeHtml(tag)}">×</button>`;
    list.appendChild(chip);
  });
  const hidden = document.getElementById('editTags');
  if (hidden) hidden.value = getTagsAsString();
  const hint = document.getElementById('tagsCountHint');
  if (hint) {
    const len = getTagsAsString().length;
    hint.textContent = `${len} / 500 caracteres`;
    hint.style.color = len > 500 ? 'var(--red)' : '';
  }
}

function removeTag(idx) {
  _tags.splice(idx, 1);
  renderTagChips();
}

function addTagFromInput() {
  const input = document.getElementById('tagsInput');
  if (!input) return;
  const raw = input.value.replace(/,/g, '').trim();
  if (raw && !_tags.includes(raw)) {
    _tags.push(raw);
    renderTagChips();
  }
  input.value = '';
}

document.addEventListener('DOMContentLoaded', () => {
  const tagsInput = document.getElementById('tagsInput');
  if (!tagsInput) return;
  tagsInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      addTagFromInput();
    } else if (e.key === 'Backspace' && tagsInput.value === '' && _tags.length > 0) {
      _tags.pop();
      renderTagChips();
    }
  });
  tagsInput.addEventListener('blur', () => {
    if (tagsInput.value.trim()) addTagFromInput();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// History panel
// ─────────────────────────────────────────────────────────────────────────────

async function loadHistory() {
  const container = document.getElementById('historyList');
  if (!container) return;
  try {
    const res = await fetch('/api/history');
    const data = await res.json();
    const projects = data.projects || [];
    if (projects.length === 0) {
      container.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">📂</div>
          <div class="empty-text">No hay proyectos anteriores todavía. Ejecutá un pipeline para crear el primero.</div>
        </div>`;
      return;
    }
    container.innerHTML = projects.map(p => {
      const title  = p.titulo || p.project_name || '(Sin título)';
      const format = p.video_format ? (p.video_format === 'normal' ? '🎬 Normal' : '📱 Short') : '';
      const thumb  = p.thumbnail
        ? `<img src="/api/outputs/${encodeURIComponent(p.thumbnail)}?t=${Date.now()}" alt="thumb">`
        : `<span style="font-size:2rem;">🎬</span>`;

      // Status badge
      const statusMap = {
        'done':        ['✅ Completado',  '#22c55e'],
        'in_progress': ['⏳ En progreso', '#f59e0b'],
        'error':       ['❌ Con errores', '#ef4444'],
        'aborted':     ['🚫 Cancelado',   '#6b7280'],
      };
      const [statusLabel, statusColor] = statusMap[p.status] || ['— Desconocido', '#6b7280'];
      const statusBadge = `<span style="background:${statusColor}22; color:${statusColor}; border:1px solid ${statusColor}44; border-radius:12px; padding:2px 8px; font-size:11px; font-weight:600;">${statusLabel}</span>`;

      // Source URL chip (truncated)
      const srcDisplay = (p.source_url || '').replace('https://www.youtube.com/watch?v=','youtu.be/').replace('https://youtu.be/','youtu.be/');
      const srcUrl = p.source_url
        ? `<div style="margin-top:4px;"><span title="${escapeHtml(p.source_url)}" style="color:var(--text-3); font-size:11px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:220px; display:inline-block; vertical-align:middle;">🔗 ${escapeHtml(srcDisplay)}</span></div>`
        : '';

      // YouTube published link
      const ytLink = p.youtube_url
        ? `<a href="${escapeHtml(p.youtube_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()" style="color:#ff4444; font-size:11px; font-weight:600; text-decoration:none; background:#ff444420; border:1px solid #ff444440; border-radius:10px; padding:2px 8px; white-space:nowrap;">▶ Ver en YouTube</a>`
        : '';

      return `
        <div class="project-card" onclick='loadProjectIntoReview(${JSON.stringify(p).replace(/'/g, "&#39;")})'>
          <div class="project-card-thumb">${thumb}</div>
          <div class="project-card-info" style="overflow:hidden;">
            <div class="project-card-title">${escapeHtml(title)}</div>
            <div class="project-card-meta" style="flex-wrap:wrap; gap:6px; align-items:center;">
              <span style="font-size:11px; color:var(--text-3);">📁 ${escapeHtml(p.project_name || '')}</span>
              ${statusBadge}
              ${format ? `<span style="font-size:11px;">${format}</span>` : ''}
              ${ytLink}
            </div>
            ${srcUrl}
          </div>
          <div class="project-card-arrow">›</div>
        </div>`;
    }).join('');
  } catch (e) {
    container.innerHTML = `<div class="empty-state"><div class="empty-text">Error cargando proyectos: ${escapeHtml(e.message)}</div></div>`;
  }
}

function loadProjectIntoReview(outputs) {
  state.lastOutputs = outputs;
  populateReviewPanel(outputs);
  showPanel('review');
  toast(`📁 Proyecto "${outputs.project_name}" cargado en Revisión.`, 'success', 4000);
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
  const tags        = document.getElementById('editTags').value.trim();
  const privacy     = document.getElementById('privacySelect').value;
  const category_id = document.getElementById('categorySelect').value;
  const playlist_id = document.getElementById('playlistSelect').value;
  const use_thumb   = document.getElementById('toggleUseThumbnail').checked;
  const thumb_file  = use_thumb ? document.getElementById('currentThumbnailFile').value : '';

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
        tags,
        category_id,
        playlist_id,
        thumbnail_file: thumb_file,
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
  const panels  = ['pipeline', 'review', 'history', 'settings', 'help'];
  const navBtns = {
    pipeline: 'navPipeline', review: 'navReview',
    history: 'navHistory', settings: 'navSettings', help: 'navHelp',
  };

  panels.forEach(p => {
    const panelEl = document.getElementById(`panel${capitalize(p)}`);
    if (panelEl) panelEl.classList.remove('active');
    const nb = document.getElementById(navBtns[p]);
    if (nb) { nb.classList.remove('active'); nb.removeAttribute('aria-current'); }
  });

  const targetPanel = document.getElementById(`panel${capitalize(name)}`);
  if (targetPanel) targetPanel.classList.add('active');
  const activeBtn = document.getElementById(navBtns[name]);
  if (activeBtn) { activeBtn.classList.add('active'); activeBtn.setAttribute('aria-current', 'page'); }

  // Lazy-load panel data
  if (name === 'history') { loadHistory(); refreshLog(); }
  if (name === 'settings') loadSettings();
}

// ─────────────────────────────────────────────────────────────────────────────
// IA Regeneration, Thumbnails & Playlists
// ─────────────────────────────────────────────────────────────────────────────

function promptRegenerate(field) {
  const wrap = document.getElementById(`regen_${field}`);
  if (!wrap) return;
  wrap.style.display = wrap.style.display === 'none' ? 'flex' : 'none';
  if (wrap.style.display === 'flex') {
    const inst = document.getElementById(`inst_${field}`);
    if (inst) inst.focus();
  }
}

async function doRegenerate(field) {
  const instructions = document.getElementById(`inst_${field}`).value.trim();
  if (!instructions) { toast('Escribe instrucciones primero.', 'warn'); return; }
  const btn = document.getElementById(`btnRegen_${field}`);
  btn.disabled = true;
  btn.textContent = '...';

  let currentVal = '';
  if (field === 'titulo') currentVal = document.getElementById('editTitle').value;
  else if (field === 'descripcion') currentVal = document.getElementById('editDescription').value;
  else if (field === 'tags') currentVal = document.getElementById('editTags').value;

  try {
    const res = await fetch('/api/regenerate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        field,
        current_value: currentVal,
        instructions,
        context: state.lastOutputs
          ? `${state.lastOutputs.titulo || ''}\n${state.lastOutputs.descripcion || ''}`
          : ''
      })
    });
    const data = await res.json();
    if (data.ok) {
      if (field === 'titulo') { document.getElementById('editTitle').value = data.new_value; updateTitleCount(); }
      else if (field === 'descripcion') { document.getElementById('editDescription').value = data.new_value; updateDescCount(); }
      else if (field === 'tags') { 
        document.getElementById('editTags').value = data.new_value;
        if (typeof setTagsFromString === 'function') setTagsFromString(data.new_value);
      }
      document.getElementById(`inst_${field}`).value = '';
      document.getElementById(`regen_${field}`).style.display = 'none';
      toast(`✅ ${field} regenerado con éxito.`, 'success');
    } else {
      toast('Error al regenerar: ' + (data.detail || ''), 'error');
    }
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
  }
  btn.disabled = false;
  btn.textContent = 'Aplicar';
}

async function doRegenerateThumbnail() {
  const instructions = document.getElementById('inst_thumbnail').value.trim();
  if (!instructions) { toast('Escribe instrucciones para la miniatura.', 'warn'); return; }
  const btn = document.getElementById('btnRegen_thumbnail');
  btn.disabled = true;
  btn.textContent = '...';
  toast('Generando miniatura con IA... puede demorar unos segundos.', 'info', 8000);

  const outFilename = state.lastOutputs && state.lastOutputs.video
    ? state.lastOutputs.video.replace('.mp4', '_thumb_regen.jpg')
    : `thumb_regen_${Date.now()}.jpg`;

  try {
    const res = await fetch('/api/regenerate-thumbnail', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt: instructions, output_filename: outFilename })
    });
    const data = await res.json();
    if (data.ok) {
      document.getElementById('currentThumbnailFile').value = data.thumbnail_file;
      const thumbImg = document.getElementById('reviewThumbnail');
      thumbImg.src = `/api/outputs/${encodeURIComponent(data.thumbnail_file)}?t=` + Date.now();
      thumbImg.style.display = 'inline-block';
      document.getElementById('thumbnailWrapper').style.display = 'block';
      document.getElementById('inst_thumbnail').value = '';
      document.getElementById('regen_thumbnail').style.display = 'none';
      toast('✅ Miniatura regenerada con éxito.', 'success');
    } else {
      toast('No se pudo generar la miniatura: ' + (data.detail || ''), 'error');
    }
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
  }
  btn.disabled = false;
  btn.textContent = 'Aplicar';
}

function toggleThumbnailVisibility() {
  const use_thumb = document.getElementById('toggleUseThumbnail').checked;
  const wrap = document.getElementById('thumbnailWrapper');
  wrap.style.opacity = use_thumb ? '1' : '0.3';
  ['btnApplyLogo'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = !use_thumb;
  });
}

async function handleUploadThumbnail(event) {
  const file = event.target.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  try {
    const res = await fetch('/api/upload-thumbnail', { method: 'POST', body: formData });
    const data = await res.json();
    if (data.ok) {
      document.getElementById('currentThumbnailFile').value = data.thumbnail_file;
      const thumbImg = document.getElementById('reviewThumbnail');
      thumbImg.src = `/api/outputs/${encodeURIComponent(data.thumbnail_file)}?t=` + Date.now();
      thumbImg.style.display = 'inline-block';
      document.getElementById('thumbnailWrapper').style.display = 'block';
      toast('✅ Miniatura cargada exitosamente.', 'success');
    }
  } catch (e) {
    toast('Error al subir miniatura: ' + e.message, 'error');
  }
}

async function applyLogoToThumbnail() {
  const current = document.getElementById('currentThumbnailFile').value;
  if (!current) { toast('No hay miniatura cargada. Generá o subí una primero.', 'warn'); return; }
  const btn = document.getElementById('btnApplyLogo');
  btn.disabled = true;
  btn.textContent = '...';
  try {
    const res = await fetch('/api/apply-logo', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ thumbnail_file: current })
    });
    const data = await res.json();
    if (data.ok) {
      document.getElementById('currentThumbnailFile').value = data.new_thumbnail;
      const thumbImg = document.getElementById('reviewThumbnail');
      thumbImg.src = `/api/outputs/${encodeURIComponent(data.new_thumbnail)}?t=` + Date.now();
      toast('✨ Logo de Pronectis aplicado!', 'success');
    } else {
      toast('Error al aplicar el logo.', 'error');
    }
  } catch (e) {
    toast('Error: ' + e.message, 'error');
  }
  btn.disabled = false;
  btn.textContent = '✨ Agregar Logo';
}

async function loadPlaylists() {
  try {
    const res = await fetch('/api/playlists');
    const data = await res.json();
    if (data.playlists) {
      const select = document.getElementById('playlistSelect');
      if (!select) return;
      select.innerHTML = '<option value="">(Ninguna)</option>';
      data.playlists.forEach(pl => {
        const opt = document.createElement('option');
        opt.value = pl.id;
        opt.textContent = pl.title;
        select.appendChild(opt);
      });
    }
  } catch (e) {
    console.warn('Error cargando playlists:', e);
  }
}

function openImageModal(src) {
  if (!src) return;
  document.getElementById('imageModalImg').src = src;
  document.getElementById('imageModal').classList.add('open');
}

function closeImageModal() {
  document.getElementById('imageModal').classList.remove('open');
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
// Format Picker
// ─────────────────────────────────────────────────────────────────────────────

function selectFormat(card) {
  // Remove selected from all cards
  document.querySelectorAll('#formatPicker .format-card').forEach(c => {
    c.classList.remove('selected');
  });
  // Select clicked card
  card.classList.add('selected');
  // Update hidden input so startPipeline() reads the right value
  document.getElementById('selectVideoFormat').value = card.dataset.value;
}

// ─────────────────────────────────────────────────────────────────────────────
// Video Source Tabs
// ─────────────────────────────────────────────────────────────────────────────

function switchVideoSource(mode) {
  document.getElementById('videoSourceMode').value = mode;
  
  // Update buttons
  document.querySelectorAll('.tabs-header .tab-btn').forEach((btn, idx) => {
    if ((mode === 'youtube' && idx === 0) || (mode === 'drive' && idx === 1) || (mode === 'local' && idx === 2)) {
      btn.classList.add('active');
    } else {
      btn.classList.remove('active');
    }
  });

  // Update panes
  document.getElementById('tab-youtube').classList.toggle('active', mode === 'youtube');
  document.getElementById('tab-drive').classList.toggle('active', mode === 'drive');
  document.getElementById('tab-local').classList.toggle('active', mode === 'local');

  // Update Context Label dynamically
  const contextLabel = document.querySelector('label[for="inputExtraContext"]');
  if (mode === 'local' || mode === 'drive') {
    contextLabel.innerHTML = 'Contexto del Video <span style="color:var(--red)">*</span> <span class="badge" style="background:var(--bg-2); color:var(--text-2); font-weight:normal; padding:2px 6px; font-size:10px;">Min. 50 caracteres</span>';
  } else {
    contextLabel.innerHTML = 'Contexto Adicional para IA (opcional)';
  }
  updateContextCounter();
}

function validateLocalFile() {
  const fileInput = document.getElementById('inputFile');
  const fileHint = document.getElementById('fileHint');
  if (fileInput.files.length > 0) {
    const file = fileInput.files[0];
    const sizeMB = (file.size / (1024 * 1024)).toFixed(1);
    fileHint.textContent = `Archivo seleccionado: ${file.name} (${sizeMB} MB).`;
    fileHint.style.color = 'var(--green)';
  } else {
    fileHint.textContent = 'Subí un archivo de video desde tu computadora (máx. recomendado: 2GB).';
    fileHint.style.color = '';
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Form helpers
// ─────────────────────────────────────────────────────────────────────────────

function validateUrl() {
  const mode = document.getElementById('videoSourceMode').value;
  
  if (mode === 'youtube') {
    const url = document.getElementById('inputUrlYt').value.trim();
    const hint = document.getElementById('urlYtHint');
    const input = document.getElementById('inputUrlYt');
    
    if (!url) {
      hint.textContent = 'Pegá un enlace público de YouTube.';
      input.style.borderColor = '';
    } else if (url.includes('youtube.com') || url.includes('youtu.be')) {
      hint.textContent = '✓ URL de YouTube válida';
      hint.style.color = 'var(--green)';
      input.style.borderColor = 'var(--green)';
    } else {
      hint.textContent = '✕ Debe ser una URL de YouTube';
      hint.style.color = 'var(--red)';
      input.style.borderColor = 'var(--red)';
    }
  } else if (mode === 'drive') {
    const url = document.getElementById('inputUrlDrive').value.trim();
    const hint = document.getElementById('urlDriveHint');
    const input = document.getElementById('inputUrlDrive');
    
    if (!url) {
      hint.textContent = 'Pegá un enlace público de Google Drive.';
      input.style.borderColor = '';
    } else if (url.includes('drive.google.com') && (url.includes('/file/d/') || url.includes('id='))) {
      hint.textContent = '✓ URL de Google Drive válida';
      hint.style.color = 'var(--green)';
      input.style.borderColor = 'var(--green)';
    } else {
      hint.textContent = '✕ Debe ser una URL de un archivo de Google Drive (no carpeta)';
      hint.style.color = 'var(--red)';
      input.style.borderColor = 'var(--red)';
    }
  }
  updateContextCounter();
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

async function shutdownVM() {
  if (confirm("¿Estás seguro de que querés APAGAR el servidor virtual? Esto detendrá la aplicación por completo.")) {
    try {
      const res = await fetch('/api/shutdown', { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        toast('Apagando servidor...', 'success');
      } else {
        toast('Error: ' + data.error, 'error');
      }
    } catch (e) {
      toast('Error de red al intentar apagar.', 'error');
    }
  }
}
