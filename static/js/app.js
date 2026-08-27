/**
 * DMS — Driver Monitoring System
 * Real-time dashboard JavaScript
 *
 * FIX: Camera dropdown stores numeric INDEX as option value.
 *      DeviceIds stored separately for preview only.
 *
 *  BEFORE (broken):
 *    option value = deviceId hash "abc123xyz..."
 *    parseInt("abc123xyz", 10) = NaN → JSON null → both cameras = index 0
 *
 *  AFTER (fixed):
 *    option value = "0", "1", "2"  (numeric index, always parseable)
 *    Distinct integers sent → server opens correct distinct cameras
 */
'use strict';

const socket = io({ transports: ['websocket', 'polling'] });

const interiorFeed    = document.getElementById('interiorFeed');
const exteriorFeed    = document.getElementById('exteriorFeed');
const statusDot       = document.getElementById('statusDot');
const statusLabel     = document.getElementById('statusLabel');
const fpsVal          = document.getElementById('fpsVal');
const btnStart        = document.getElementById('btnStart');
const btnStop         = document.getElementById('btnStop');
const interiorAlertList = document.getElementById('interiorAlertList');
const exteriorAlertList = document.getElementById('exteriorAlertList');
const emergencyModal  = document.getElementById('emergencyModal');
const interiorSrcInput = document.getElementById('interiorSrc');
const exteriorSrcInput = document.getElementById('exteriorSrc');
const cameraPreview   = document.getElementById('cameraPreview');
const cameraNote      = document.getElementById('cameraNote');
const earVal    = document.getElementById('earVal');
const earBar    = document.getElementById('earBar');
const yawVal    = document.getElementById('yawVal');
const yawNeedle = document.getElementById('yawNeedle');
const pitchVal  = document.getElementById('pitchVal');
const speedVal  = document.getElementById('speedVal');
const speedArc  = document.getElementById('speedArc');
const devVal    = document.getElementById('devVal');
const objVal    = document.getElementById('objVal');
const driverBadge = document.getElementById('driverBadge');
const roadBadge   = document.getElementById('roadBadge');

const indicators = {
  drowsy:     document.getElementById('ind-drowsy'),
  sleeping:   document.getElementById('ind-sleeping'),
  yawning:    document.getElementById('ind-yawning'),
  phone:      document.getElementById('ind-phone'),
  distracted: document.getElementById('ind-distracted'),
  lane:       document.getElementById('ind-lane'),
  overspeed:  document.getElementById('ind-overspeed'),
  pedestrian: document.getElementById('ind-pedestrian'),
  vehicle:    document.getElementById('ind-vehicle'),
  obstacle:   document.getElementById('ind-obstacle'),
};

let lastEmergencyAlertId = null;
let previewStream = null;
let _cameraDeviceIds = [];   // index → deviceId, for preview only

const SPEED_MAX = 120;
const ARC_TOTAL = 220;

// ── Camera Enumeration ────────────────────────────────────────────
async function loadCameraDevices() {
  setCameraNote('Requesting camera permission…', 'dim');
  let permStream = null;
  try {
    permStream = await navigator.mediaDevices.getUserMedia({ video: true });
  } catch (err) {
    setCameraNote('⚠ Camera permission denied — using index numbers only.', 'warn');
    populateCameraSelects([]);
    return;
  } finally {
    if (permStream) permStream.getTracks().forEach(t => t.stop());
  }
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const cameras = devices.filter(d => d.kind === 'videoinput');
    populateCameraSelects(cameras);
    setCameraNote(
      cameras.length === 0
        ? '⚠ No cameras detected. Check connections.'
        : `✓ ${cameras.length} camera(s) found. Select and Preview before starting.`,
      cameras.length === 0 ? 'warn' : 'ok'
    );
  } catch (err) {
    setCameraNote('Could not enumerate cameras.', 'warn');
    populateCameraSelects([]);
  }
}

function populateCameraSelects(cameras) {
  _cameraDeviceIds = [];

  const buildOptions = () => {
    if (cameras.length === 0) {
      return `
        <option value="0">📷 Camera 1 (Index 0 — default)</option>
        <option value="1">📷 Camera 2 (Index 1)</option>
        <option value="2">📷 Camera 3 (Index 2)</option>`;
    }
    return cameras.map((cam, index) => {
      _cameraDeviceIds[index] = cam.deviceId || '';   // save deviceId for preview

      let label = cam.label || '';
      if (label.length > 44) label = label.substring(0, 42) + '…';
      if (!label) label = `Camera ${index + 1}`;

      const l = label.toLowerCase();
      let icon = '📷';
      if      (l.includes('built-in') || l.includes('integrated') || l.includes('facetime')) icon = '💻';
      else if (l.includes('usb'))                           icon = '🔌';
      else if (l.includes('virtual') || l.includes('obs')) icon = '🖥️';
      else if (l.includes('back')    || l.includes('rear')) icon = '📸';
      else if (l.includes('front'))                         icon = '🤳';

      // ─── KEY FIX: value = numeric index string, NOT deviceId ───
      return `<option value="${index}">${icon} ${label}</option>`;
    }).join('');
  };

  const opts = buildOptions();
  interiorSrcInput.innerHTML = opts;
  exteriorSrcInput.innerHTML = opts;
  interiorSrcInput.value = '0';
  exteriorSrcInput.value = cameras.length > 1 ? '1' : '0';
}

function setCameraNote(msg, type) {
  if (!cameraNote) return;
  cameraNote.textContent = msg;
  cameraNote.style.color = type === 'ok'   ? 'var(--success)' :
                            type === 'warn' ? 'var(--warn)'    : 'var(--text-dim)';
}

// ── Preview ───────────────────────────────────────────────────────
function stopPreviewStream() {
  if (previewStream) { previewStream.getTracks().forEach(t => t.stop()); previewStream = null; }
  if (cameraPreview) cameraPreview.srcObject = null;
}

async function previewSelectedCamera(kind) {
  const selectEl     = kind === 'exterior' ? exteriorSrcInput : interiorSrcInput;
  const numericIndex = parseInt(selectEl.value, 10);   // always valid integer now
  const deviceId     = _cameraDeviceIds[numericIndex] || '';

  const constraints = deviceId
    ? { video: { deviceId: { exact: deviceId } } }
    : { video: true };

  try {
    stopPreviewStream();
    previewStream = await navigator.mediaDevices.getUserMedia(constraints);
    cameraPreview.srcObject = previewStream;
    await cameraPreview.play();
    setCameraNote(`Previewing ${kind} camera (index ${numericIndex}). Stop preview before starting.`, 'ok');
  } catch (err) {
    console.error('Preview failed:', err);
    setCameraNote('Preview failed — allow camera access or select a different camera.', 'warn');
  }
}

// ── Monitoring ────────────────────────────────────────────────────
async function startMonitoring() {
  stopPreviewStream();   // release browser camera lock before server opens it

  const interior = parseInt(interiorSrcInput ? interiorSrcInput.value : '0', 10);
  const exterior = parseInt(exteriorSrcInput ? exteriorSrcInput.value : '1', 10);

  const safeInterior = isNaN(interior) ? 0 : interior;
  const safeExterior = isNaN(exterior) ? 1 : exterior;

  if (safeInterior === safeExterior) {
    setCameraNote('⚠ Both cameras set to the same index! Select different cameras.', 'warn');
    return;
  }

  try {
    const res  = await fetch('/api/monitoring/start', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ interior_src: safeInterior, exterior_src: safeExterior }),
    });
    const data = await res.json();
    if (data.status === 'started' || data.status === 'already_running') {
      setMonitoringUI(true);
      setCameraNote(`▶ Monitoring — Interior: cam ${safeInterior} | Exterior: cam ${safeExterior}`, 'ok');
    }
  } catch (e) {
    console.error('Start error:', e);
    setCameraNote('Failed to start. Check server connection.', 'warn');
  }
}

async function stopMonitoring() {
  try {
    await fetch('/api/monitoring/stop', { method: 'POST' });
    setMonitoringUI(false);
  } catch (e) { console.error('Stop error:', e); }
}

async function applyCameraSelection() {
  stopPreviewStream();
  if (!btnStart.disabled) {
    await startMonitoring();
  } else {
    await stopMonitoring();
    await new Promise(r => setTimeout(r, 300));
    await startMonitoring();
  }
}

function setMonitoringUI(active) {
  resetExteriorState();
  btnStart.disabled = active;
  btnStop.disabled  = !active;
  statusDot.classList.toggle('active', active);
  statusLabel.textContent = active ? 'System Online — Monitoring' : 'System Offline';
  driverBadge.textContent = active ? 'LIVE' : 'OFFLINE';
  driverBadge.classList.toggle('live', active);
  roadBadge.textContent   = active ? 'LIVE' : 'OFFLINE';
  roadBadge.classList.toggle('live', active);
}

function resetExteriorState() {
  updateIndicator('lane', false, false);
  updateIndicator('overspeed', false, false);
  updateIndicator('pedestrian', false, false);
  updateIndicator('vehicle', false, false);
  updateIndicator('obstacle', false, false);
  if (devVal) { devVal.textContent = '0.0%'; devVal.style.color = 'var(--accent)'; }
  if (objVal) { objVal.textContent = '0'; objVal.style.color = 'var(--accent)'; }
  if (speedVal) speedVal.textContent = '0';
  if (speedArc) speedArc.style.strokeDashoffset = ARC_TOTAL;
  if (exteriorFeed) exteriorFeed.removeAttribute('src');
}

// ── Socket Events ─────────────────────────────────────────────────
socket.on('connect', () => console.log('[DMS] Socket connected'));
socket.on('monitoring_state', d => setMonitoringUI(d.active));

socket.on('frame_update', d => {
  if (d.interior) interiorFeed.src = 'data:image/jpeg;base64,' + d.interior;
  if (d.exterior) exteriorFeed.src = 'data:image/jpeg;base64,' + d.exterior;
});

socket.on('status_update', data => {
  const driver = data.driver || {};
  const rawRoad = data.road || {};
  const caps = data.caps || {};
  const exteriorValid = rawRoad.camera_signal === true && caps.exterior_ok === true;
  const road = exteriorValid ? rawRoad : {
    camera_signal: false,
    lane_deviation: false,
    over_speed: false,
    pedestrians_detected: [],
    vehicles_detected: [],
    obstacles_detected: [],
    speed_kmh: 0,
    deviation_pct: 0,
  };

  if (data.fps !== undefined) fpsVal.textContent = data.fps;

  updateIndicator('drowsy',     driver.sleeping ? false : driver.drowsy, driver.sleeping);
  updateIndicator('sleeping',   driver.sleeping,    false);
  updateIndicator('yawning',    driver.yawning,     false);
  updateIndicator('phone',      driver.phone_usage, false);
  updateIndicator('distracted', driver.distracted,  false);

  if (driver.ear != null) {
    earVal.textContent = driver.ear.toFixed(2);
    const pct = Math.min(100, (driver.ear / 0.40) * 100);
    earBar.style.width      = pct + '%';
    earBar.style.background = driver.ear < 0.25 ? 'var(--danger)' :
                              driver.ear < 0.28 ? 'var(--warn)'   : 'var(--success)';
  }
  if (driver.head_yaw != null) {
    yawVal.textContent = (driver.head_yaw > 0 ? '+' : '') + driver.head_yaw + '°';
    const angle = Math.max(-45, Math.min(45, driver.head_yaw));
    yawNeedle.style.transform  = `translateX(-50%) rotate(${angle}deg)`;
    yawNeedle.style.background = Math.abs(driver.head_yaw) > 30 ? 'var(--danger)' :
                                  Math.abs(driver.head_yaw) > 20 ? 'var(--warn)'   : 'var(--accent)';
  }
  if (driver.head_pitch != null)
    pitchVal.textContent = (driver.head_pitch > 0 ? '+' : '') + driver.head_pitch + '°';

  updateIndicator('lane',       road.lane_deviation, false);
  updateIndicator('overspeed',  road.over_speed,     false);
  updateIndicator('pedestrian', (road.pedestrians_detected || []).length > 0, false);
  updateIndicator('vehicle',    (road.vehicles_detected    || []).length > 0, false);
  updateIndicator('obstacle',   (road.obstacles_detected   || []).length > 0, false);

  if (road.speed_kmh !== undefined) {
    const spd = Math.min(SPEED_MAX, road.speed_kmh);
    speedVal.textContent = Math.round(spd);
    speedArc.style.strokeDashoffset = ARC_TOTAL - (spd / SPEED_MAX) * ARC_TOTAL;
    speedArc.style.stroke = road.over_speed ? 'var(--danger)' :
                            spd > 60        ? 'var(--warn)'   : 'var(--accent)';
  }
  if (road.deviation_pct !== undefined) {
    devVal.textContent = road.deviation_pct.toFixed(1) + '%';
    devVal.style.color = road.lane_deviation ? 'var(--danger)' : 'var(--accent)';
  }
  const totalObjs = (road.vehicles_detected   || []).length +
                    (road.pedestrians_detected || []).length +
                    (road.obstacles_detected   || []).length;
  objVal.textContent = totalObjs;
  objVal.style.color = totalObjs > 2 ? 'var(--warn)' : 'var(--accent)';

  if (btnStop && !btnStop.disabled) {
    driverBadge.textContent = caps.interior_ok === false ? 'NO SIGNAL' : 'LIVE';
    driverBadge.classList.toggle('live', caps.interior_ok !== false);
    roadBadge.textContent   = caps.exterior_ok === false ? 'NO SIGNAL' : 'LIVE';
    roadBadge.classList.toggle('live', caps.exterior_ok !== false);
  }
  if (!exteriorValid) resetExteriorState();
});

socket.on('alert', d => addAlert(d));
socket.on('emergency_escalation', d => {
  lastEmergencyAlertId = d.alert_id;
  const source = d.source === 'EXTERIOR' ? 'EXTERIOR' : 'INTERIOR';
  const interiorBox = document.getElementById('interiorEmergencyBox');
  const exteriorBox = document.getElementById('exteriorEmergencyBox');
  const interiorMsg = document.getElementById('interiorEmergencyMsg');
  const exteriorMsg = document.getElementById('exteriorEmergencyMsg');
  interiorBox.classList.toggle('active', source === 'INTERIOR');
  exteriorBox.classList.toggle('active', source === 'EXTERIOR');
  interiorMsg.textContent = source === 'INTERIOR' ? d.message : 'No interior emergency';
  exteriorMsg.textContent = source === 'EXTERIOR' ? d.message : 'No exterior emergency';
  emergencyModal.style.display = 'flex';
  playAlertBeep('critical');
});

// ── UI ────────────────────────────────────────────────────────────
function updateIndicator(key, active, critical) {
  const el = indicators[key];
  if (!el) return;
  el.classList.toggle('active',   active && !critical);
  el.classList.toggle('critical', !!critical);
}

function addAlert(data) {
  const source = data.source === 'EXTERIOR' ? 'EXTERIOR' : 'INTERIOR';
  const alertList = source === 'EXTERIOR' ? exteriorAlertList : interiorAlertList;
  if (!alertList) return;
  if (alertList.querySelector('.no-alerts')) alertList.innerHTML = '';
  const t        = new Date().toLocaleTimeString('en-IN', { hour12: false });
  const sevClass = 'sev-' + (data.severity || 'low').toLowerCase();
  const item     = document.createElement('div');
  item.className = 'alert-item';
  item.id        = 'alert-' + data.alert_id;
  item.innerHTML = `
    <span class="sev-badge ${sevClass}">${data.severity}</span>
    <span class="alert-msg">${data.message}</span>
    <span class="alert-time">${t}</span>
    <button class="ack-btn" onclick="acknowledgeAlert('${data.alert_id}',this)">✓</button>`;
  alertList.insertBefore(item, alertList.firstChild);
  playAlertBeep(data.severity);
  while (alertList.children.length > 50) alertList.removeChild(alertList.lastChild);
}

function acknowledgeAlert(alertId, btn) {
  socket.emit('alert_responded', { alert_id: alertId });
  btn.closest('.alert-item').style.opacity = '0.5';
  btn.textContent = '✓ OK'; btn.disabled = true;
}

function acknowledgeEmergency() {
  if (lastEmergencyAlertId) socket.emit('alert_responded', { alert_id: lastEmergencyAlertId });
  emergencyModal.style.display = 'none';
}

function clearAlerts(listId) {
  const list = document.getElementById(listId);
  if (!list) return;
  list.innerHTML = `<div class="no-alerts">${listId === 'interiorAlertList' ? 'No driver warnings' : 'No road warnings'}</div>`;
}

let audioCtx = null;
function playAlertBeep(severity) {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = audioCtx.createOscillator(), gain = audioCtx.createGain();
    osc.connect(gain); gain.connect(audioCtx.destination);
    const freqMap = { CRITICAL:880, HIGH:660, MEDIUM:440, LOW:330, critical:880 };
    osc.frequency.value = freqMap[severity] || 440;
    osc.type = 'square';
    gain.gain.setValueAtTime(0.08, audioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.3);
    osc.start(audioCtx.currentTime); osc.stop(audioCtx.currentTime + 0.3);
  } catch (e) {}
}

async function showServerLogs() {
  try {
    const data = await (await fetch('/api/monitoring/logs')).json();
    console.log('[DMS Logs]\n' + (data || []).slice(-20).join('\n'));
    alert('Server logs printed to browser console (F12 → Console).');
  } catch (e) { alert('Failed to fetch logs.'); }
}

(async function init() {
  try {
    const data = await (await fetch('/api/monitoring/status')).json();
    setMonitoringUI(data.active);
  } catch (e) {}
  await loadCameraDevices();
})();
