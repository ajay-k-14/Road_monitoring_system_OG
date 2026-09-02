"""
Real-Time Driver & Road Monitoring with Accident Prevention System
Main Flask Application with WebSocket support
"""

import cv2
import base64
import threading
import time
import os
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_socketio import SocketIO, emit
from flask_login import LoginManager
import numpy as np
from collections import deque

from database.db import (
    init_db, get_db, User, Vehicle, EmergencyContact,
    create_user, get_user_by_email, log_alert, get_alert_history
)
from modules.driver_monitor import DriverMonitor
from modules.road_monitor import RoadMonitor
from modules.alert_system import AlertSystem
from config import Config

# ─────────────────────────────────────────────
#  App Initialization
# ─────────────────────────────────────────────
app = Flask(__name__)
app.config.from_object(Config)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ─────────────────────────────────────────────
#  Global State
# ─────────────────────────────────────────────
road_monitor      = RoadMonitor()
driver_monitor    = DriverMonitor(yolo_model=road_monitor._yolo)
alert_system      = AlertSystem(socketio)
monitoring_active = False
monitor_thread    = None
browser_capture_mode = False
interior_frame_bytes = None
exterior_frame_bytes = None
frame_lock  = threading.Lock()
browser_frame_lock = threading.Lock()
browser_frame_times = {'interior': 0.0, 'exterior': 0.0}
browser_pending_frames = {}
browser_worker_thread = None
debug_logs  = deque(maxlen=1000)

# Browser uploads are deliberately bounded so a hosted instance cannot be
# overwhelmed by camera bandwidth or concurrent inference requests.
BROWSER_FRAME_MAX_BYTES = int(os.environ.get('BROWSER_FRAME_MAX_BYTES', '300000'))
BROWSER_FRAME_MIN_INTERVAL = float(os.environ.get('BROWSER_FRAME_MIN_INTERVAL', '0.5'))
BROWSER_FRAME_MAX_WIDTH = int(os.environ.get('BROWSER_FRAME_MAX_WIDTH', '640'))


def log_debug(msg):
    try:
        app.logger.info(msg)
    except Exception:
        pass
    debug_logs.append(f"{time.strftime('%H:%M:%S')} {msg}")


def _process_browser_frame(frame_b64, side):
    """Decode a browser-captured JPEG and run server-side AI analysis."""
    global interior_frame_bytes, exterior_frame_bytes

    if not frame_b64:
        return

    try:
        encoded = frame_b64.split(',', 1)[1] if ',' in frame_b64 else frame_b64
        image_bytes = base64.b64decode(encoded)
        np_arr = np.frombuffer(image_bytes, dtype=np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if frame is None or frame.size == 0:
            return
        if frame.shape[1] > BROWSER_FRAME_MAX_WIDTH:
            scale = BROWSER_FRAME_MAX_WIDTH / frame.shape[1]
            frame = cv2.resize(frame, (BROWSER_FRAME_MAX_WIDTH,
                                       max(1, int(frame.shape[0] * scale))))
    except Exception as exc:
        log_debug(f"Browser frame decode failed for {side}: {exc}")
        return

    if side == 'interior':
        driver_results = driver_monitor.process(frame, input_valid=True)
        annotated = driver_monitor.annotate(frame.copy(), driver_results)
        _, buf = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 70])
        with frame_lock:
            interior_frame_bytes = base64.b64encode(buf.tobytes()).decode('utf-8')
        socketio.emit('frame_update', {
            'interior': interior_frame_bytes,
            'exterior': exterior_frame_bytes or '',
        })
        socketio.emit('status_update', {
            'driver': driver_results,
            'road': {'camera_signal': False},
            'alerts': [],
            'fps': float(driver_monitor.fps),
            'caps': {'interior_ok': True, 'exterior_ok': False},
        })
        return

    if side == 'exterior':
        road_results = road_monitor.process(frame, input_valid=True)
        annotated = road_monitor.annotate(frame.copy(), road_results)
        _, buf = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 70])
        with frame_lock:
            exterior_frame_bytes = base64.b64encode(buf.tobytes()).decode('utf-8')
        socketio.emit('frame_update', {
            'interior': interior_frame_bytes or '',
            'exterior': exterior_frame_bytes,
        })
        socketio.emit('status_update', {
            'driver': {'sleeping': False, 'drowsy': False},
            'road': road_results,
            'alerts': alert_system.evaluate({'sleeping': False, 'drowsy': False}, road_results),
            'fps': float(driver_monitor.fps),
            'caps': {'interior_ok': False, 'exterior_ok': True},
        })


def _browser_frame_worker():
    global browser_worker_thread
    worker = threading.current_thread()
    next_side = 'interior'
    while monitoring_active:
        frame = None
        side = None
        with browser_frame_lock:
            for candidate in (next_side, 'exterior' if next_side == 'interior' else 'interior'):
                if candidate in browser_pending_frames:
                    side = candidate
                    frame = browser_pending_frames.pop(candidate)
                    next_side = 'exterior' if candidate == 'interior' else 'interior'
                    break
        if frame is None:
            time.sleep(0.01)
            continue
        try:
            _process_browser_frame(frame, side)
        except Exception as exc:
            log_debug(f"Browser {side} frame processing failed: {exc}")
    with browser_frame_lock:
        browser_pending_frames.clear()
    with browser_frame_lock:
        if browser_worker_thread is worker:
            browser_worker_thread = None


@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    return db.query(User).get(int(user_id))


# ─────────────────────────────────────────────
#  Camera Source Parser  ← BUG FIX
# ─────────────────────────────────────────────
def normalize_camera_pair(interior_src, exterior_src):
    """Ensure the two configured sources are distinct while preserving the old logic."""
    if interior_src == exterior_src and isinstance(interior_src, int):
        return interior_src, exterior_src + 1
    return interior_src, exterior_src


def get_bind_host():
    """Allow the app to bind to the network interface requested by the environment."""
    return os.environ.get('HOST') or os.environ.get('FLASK_RUN_HOST') or Config.HOST or '0.0.0.0'


def _parse_src(v):
    """
    Safely convert camera source value from the API request to an int index
    or URL string.
    
    THE BUG WAS HERE:
      Browser enumerateDevices gives a deviceId like "abc123xyz..."
      JavaScript parseInt("abc123xyz", 10) returns NaN
      JSON.stringify({interior_src: NaN}) sends null to server
      cv2.VideoCapture(null) opened device 0 for BOTH cameras
      
    FIX: detect browser deviceId hash strings and return None so the
    caller can fall back to sensible defaults instead of both using 0.
    """
    import math

    # None arrives when JS sent null (parseInt of a hash string = NaN → JSON null)
    if v is None:
        return None

    # Guard float NaN
    if isinstance(v, float) and math.isnan(v):
        return None

    if isinstance(v, bool):
        return None

    if isinstance(v, (int, float)):
        if not math.isfinite(v) or v < 0:
            return None
        return int(v)

    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        # RTSP / HTTP stream URL — keep as string
        if '://' in s:
            return s
        # Pure numeric string e.g. "0", "1", "2"
        if s.isdigit():
            return int(s)
        # Anything else (browser deviceId hash) — ignore
        log_debug(f"[parse_src] non-numeric value received: '{s[:24]}…' — using default")
        return None

    return None


# ─────────────────────────────────────────────
#  Background Monitoring Thread
# ─────────────────────────────────────────────
def monitoring_loop(interior_src=0, exterior_src=1):
    global monitoring_active, interior_frame_bytes, exterior_frame_bytes

    def _open_cap(src, backends=None):
        """Open VideoCapture with Windows backend fallback."""
        try:
            if isinstance(src, int):
                if backends is None:
                    backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, None]
                for backend in backends:
                    try:
                        cap = cv2.VideoCapture(src, backend) if backend is not None else cv2.VideoCapture(src)
                        if cap and cap.isOpened():
                            log_debug(f"Opened cam index {src} backend={backend}")
                            return cap
                        if cap:
                            cap.release()
                    except Exception as e:
                        log_debug(f"Open cap failed index={src} backend={backend}: {e}")
                return None
            if isinstance(src, str):
                backends = backends or [None]
                for backend in backends:
                    try:
                        cap = cv2.VideoCapture(src) if backend is None else cv2.VideoCapture(src, backend)
                        if cap and cap.isOpened():
                            log_debug(f"Opened stream source {src} backend={backend}")
                            return cap
                        if cap:
                            cap.release()
                    except Exception as e:
                        log_debug(f"Open stream failed src={src} backend={backend}: {e}")
                return None
            return None
        except Exception as e:
            log_debug(f"Open cap exception src={src}: {e}")
            return None

    def _frames_are_same(a, b, threshold=2.5):
        if a is None or b is None:
            return False
        try:
            a_resized = cv2.resize(a, (160, 120))
            b_resized = cv2.resize(b, (160, 120))
            diff = np.mean(np.abs(a_resized.astype(np.float32) - b_resized.astype(np.float32)))
            log_debug(f"Frame compare diff={diff:.2f}")
            return diff < threshold
        except Exception as e:
            log_debug(f"Frame compare failed: {e}")
            return False

    def _scan_available_cameras(distinct_from=None, required=2):
        """Scan indices 0-8 and return list of (index, cap) for working cameras.

        If distinct_from is provided, ignore any camera whose first frame is too
        similar to that reference frame. Otherwise, return the first `required`
        distinct camera feeds found by comparing frames.
        """
        found = []
        found_frames = []
        for i in range(9):
            cap = _open_cap(i)
            if cap is None:
                continue
            frame = _capture_one(cap)
            if frame is None:
                try: cap.release()
                except Exception: pass
                continue
            if distinct_from is not None and _frames_are_same(frame, distinct_from):
                log_debug(f"Scan skip index {i}: matches reference camera")
                try: cap.release()
                except Exception: pass
                continue
            unique = True
            for existing in found_frames:
                if _frames_are_same(frame, existing):
                    unique = False
                    break
            if not unique:
                log_debug(f"Scan skip index {i}: duplicate of earlier scan result")
                try: cap.release()
                except Exception: pass
                continue
            found.append((i, cap))
            found_frames.append(frame)
            if len(found) == required:
                break
        return found

    # Open requested sources
    cap_in  = _open_cap(interior_src)
    cap_ext = _open_cap(exterior_src, backends=[cv2.CAP_MSMF, cv2.CAP_DSHOW, None])
    interior_ok = cap_in  is not None
    exterior_ok = cap_ext is not None

    log_debug(f"Requested: interior={interior_src} exterior={exterior_src} "
              f"| opened: in={interior_ok} ext={exterior_ok}")

    def _capture_one(cap):
        if cap is None:
            return None
        for _ in range(3):
            try:
                ret, f = cap.read()
            except Exception:
                ret, f = False, None
            if ret and f is not None:
                return f
            time.sleep(0.03)
        return None

    f_in = None
    f_ext = None
    try:
        f_in = _capture_one(cap_in)
        f_ext = _capture_one(cap_ext)
        interior_ok = cap_in is not None and f_in is not None
        exterior_ok = cap_ext is not None and f_ext is not None
        if f_in is not None and f_ext is not None:
            same_cam = _frames_are_same(f_in, f_ext)
            log_debug(f"Diag: cap ids in={id(cap_in)} ext={id(cap_ext)} same_cam={same_cam}")
            if same_cam:
                log_debug("Diag: interior/exterior frames appear nearly identical — retrying exterior on alternate backend")
                try:
                    if cap_ext is not None:
                        cap_ext.release()
                    cap_ext = _open_cap(exterior_src, backends=[cv2.CAP_DSHOW, None, cv2.CAP_MSMF])
                    exterior_ok = cap_ext is not None
                    f_ext = _capture_one(cap_ext)
                    if f_in is not None and f_ext is not None and _frames_are_same(f_in, f_ext):
                        log_debug("Diag: alternate backend still produced same camera — scanning for distinct camera")
                        if cap_ext is not None:
                            cap_ext.release()
                        cap_ext = None
                        exterior_ok = False
                    else:
                        log_debug("Diag: alternate backend produced distinct exterior camera")
                except Exception as e:
                    log_debug(f"Diag retry failed: {e}")

        else:
            log_debug(f"Diag: could not capture frames for compare in={f_in is not None} ext={f_ext is not None}")
    except Exception as e:
        log_debug(f"Diag exception: {e}")

    # If we still have duplicate or no exterior feed, try scanning distinct cameras.
    if not exterior_ok or (cap_in is not None and cap_ext is not None and f_in is not None and f_ext is not None and _frames_are_same(f_in, f_ext)):
        available = _scan_available_cameras(distinct_from=f_in)
        log_debug(f"Auto-scan found cameras at indices: {[i for i,_ in available]}")
        assign_idx = 0
        if not interior_ok and assign_idx < len(available):
            if cap_in:
                try: cap_in.release()
                except Exception: pass
            idx, cap_in = available[assign_idx]
            interior_ok = True
            assign_idx += 1
            log_debug(f"Auto-assigned cam {idx} → interior")

        if not exterior_ok and assign_idx < len(available):
            if cap_ext:
                try: cap_ext.release()
                except Exception: pass
            idx, cap_ext = available[assign_idx]
            exterior_ok = True
            assign_idx += 1
            log_debug(f"Auto-assigned cam {idx} → exterior")

        if cap_in is not None and f_in is None:
            f_in = _capture_one(cap_in)
        if cap_ext is not None:
            f_ext = _capture_one(cap_ext)
            if f_in is not None and f_ext is not None and _frames_are_same(f_in, f_ext):
                log_debug("Auto-scan still returned duplicate camera feeds.")
        interior_ok = cap_in is not None and f_in is not None
        exterior_ok = cap_ext is not None and f_ext is not None

        # Release any unused scanned caps
        for idx, cap in available:
            if cap is not cap_in and cap is not cap_ext:
                try: cap.release()
                except Exception: pass

        if not interior_ok or not exterior_ok:
            log_debug("One or more cameras still unavailable after auto-scan.")

    # ── Main loop ──────────────────────────────────────────────────────
    frame_count    = 0
    interior_fails = 0
    exterior_fails = 0

    def _sanitize_json(obj):
        if isinstance(obj, dict):
            return {str(k): _sanitize_json(v) for k, v in obj.items()
                    if not str(k).startswith('_')}
        if isinstance(obj, (list, tuple)):
            return [_sanitize_json(v) for v in obj]
        if isinstance(obj, np.ndarray):
            return _sanitize_json(obj.tolist())
        if isinstance(obj, (np.integer, np.floating)):
            return obj.item()
        if isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        return str(obj)

    while monitoring_active:
        frame_count += 1

        # ── Interior frame ─────────────────────────────
        if interior_ok and cap_in is not None:
            ret, in_frame = cap_in.read()
            if not ret or in_frame is None:
                interior_fails += 1
                in_frame = _demo_frame("Interior — No Signal", (480, 640, 3), (30, 30, 60))
            else:
                interior_fails = 0
            interior_signal = bool(ret and in_frame is not None)
        else:
            in_frame = _demo_frame("Demo — Interior Camera", (480, 640, 3), (20, 30, 50))
            interior_signal = False

        if interior_fails > 5:
            log_debug("Reconnecting interior…")
            try:
                if cap_in: cap_in.release()
                cap_in = _open_cap(interior_src if isinstance(interior_src, int) else 0)
                interior_ok = cap_in is not None
            except Exception:
                interior_ok = False
            interior_fails = 0

        # ── Exterior frame ─────────────────────────────
        if cap_ext is not None:
            ret, ext_frame = cap_ext.read()
            if not ret or ext_frame is None:
                exterior_fails += 1
                exterior_ok = False
                ext_frame = _demo_frame("Exterior — No Signal", (480, 640, 3), (30, 60, 30))
                exterior_signal = False
            else:
                exterior_fails = 0
                exterior_ok = True
                exterior_signal = True
        else:
            ext_frame = _demo_frame("Demo — Exterior Camera", (480, 640, 3), (20, 40, 20))
            exterior_signal = False

        if exterior_fails > 5:
            log_debug("Reconnecting exterior…")
            try:
                if cap_ext: cap_ext.release()
                cap_ext = _open_cap(exterior_src if isinstance(exterior_src, int) else 1)
                exterior_ok = cap_ext is not None
            except Exception:
                exterior_ok = False
            exterior_fails = 0

        # ── AI Processing ───────────────────────────────
        driver_results = driver_monitor.process(in_frame, input_valid=interior_signal)
        road_results   = road_monitor.process(ext_frame, input_valid=exterior_signal)
        annotated_in   = driver_monitor.annotate(in_frame.copy(),  driver_results)
        annotated_ext  = road_monitor.annotate(ext_frame.copy(), road_results)

        # ── Flip annotated frames for correct orientation ───────────
        #annotated_in = cv2.flip(annotated_in, 1)   # 180° rotation
        #annotated_ext = cv2.flip(annotated_ext, 1)  # 180° rotation


        # ── Alerts ──────────────────────────────────────
        alerts = alert_system.evaluate(driver_results, road_results)
        if alerts:
            for alert in alerts:
                log_alert(alert['type'], alert['severity'], alert['message'])

        # ── Encode & Emit ───────────────────────────────
        _, buf_in  = cv2.imencode('.jpg', annotated_in,  [cv2.IMWRITE_JPEG_QUALITY, 70])
        _, buf_ext = cv2.imencode('.jpg', annotated_ext, [cv2.IMWRITE_JPEG_QUALITY, 70])

        with frame_lock:
            interior_frame_bytes = base64.b64encode(buf_in.tobytes()).decode('utf-8')
            exterior_frame_bytes = base64.b64encode(buf_ext.tobytes()).decode('utf-8')

        socketio.emit('frame_update', {
            'interior': interior_frame_bytes,
            'exterior': exterior_frame_bytes,
        })
        socketio.emit('status_update', {
            'driver': _sanitize_json(driver_results),
            'road':   _sanitize_json(road_results),
            'alerts': _sanitize_json(alerts),
            'fps':    float(driver_monitor.fps),
            'caps': {
                'interior_ok': bool(interior_ok),
                'exterior_ok': bool(exterior_ok),
            }
        })

        time.sleep(0.04)

    if cap_in is not None:
        cap_in.release()
    if cap_ext is not None:
        cap_ext.release()


def _demo_frame(label, shape, bg_color):
    frame = np.full(shape, bg_color, dtype=np.uint8)
    cv2.putText(frame, label, (30, shape[0] // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (200, 200, 200), 2)
    cv2.putText(frame, time.strftime("%H:%M:%S"), (30, shape[0] // 2 + 35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1)
    return frame


# ─────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────
@app.route('/')
def index():
    # Show landing page for everyone (unauthenticated visitors + logged-in users)
    return render_template('landing.html')

@app.route('/landing')
def landing():
    # Alias for landing page
    return render_template('landing.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email    = request.form.get('email', '')
        password = request.form.get('password', '')
        user = get_user_by_email(email)
        if user and user.check_password(password):
            session['user_id']   = user.id
            session['user_name'] = user.name
            return redirect(url_for('dashboard'))
        return render_template('login.html', error='Invalid credentials')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name  = request.form.get('name', '')
        email = request.form.get('email', '')
        phone = request.form.get('phone', '')
        pwd   = request.form.get('password', '')
        if get_user_by_email(email):
            return render_template('register.html', error='Email already registered')
        create_user(name, email, phone, pwd)
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('index.html', user_name=session.get('user_name', 'Driver'))

@app.route('/history')
def history():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    alerts = get_alert_history(limit=100)
    return render_template('history.html', alerts=alerts,
                           user_name=session.get('user_name', 'Driver'))

@app.route('/settings')
def settings():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('settings.html', user_name=session.get('user_name', 'Driver'))

# ── API ────────────────────────────────────────────────────────────
@app.route('/api/monitoring/start', methods=['POST'])
def start_monitoring():
    global monitoring_active, monitor_thread, browser_capture_mode

    data    = request.get_json(silent=True) or {}
    raw_in  = data.get('interior_src', 0)
    raw_ext = data.get('exterior_src', 1)
    capture_mode = data.get('capture_mode', 'local')

    interior_src = _parse_src(raw_in)
    exterior_src = _parse_src(raw_ext)

    if interior_src is None:
        interior_src = 0
        log_debug(f"interior_src defaulted to 0 (raw was: {str(raw_in)[:30]})")
    if exterior_src is None:
        exterior_src = 1
        log_debug(f"exterior_src defaulted to 1 (raw was: {str(raw_ext)[:30]})")

    interior_src, exterior_src = normalize_camera_pair(interior_src, exterior_src)
    if interior_src != data.get('interior_src') or exterior_src != data.get('exterior_src'):
        log_debug(f"Same index conflict resolved — interior_src={interior_src} exterior_src={exterior_src}")

    if capture_mode == 'browser':
        if monitor_thread is not None and monitor_thread.is_alive():
            return jsonify({'status': 'already_running', 'mode': 'local'})
        browser_capture_mode = True
        monitoring_active = True
        with browser_frame_lock:
            browser_pending_frames.clear()
            if browser_worker_thread is None or not browser_worker_thread.is_alive():
                browser_worker_thread = threading.Thread(
                    target=_browser_frame_worker, daemon=True
                )
                browser_worker_thread.start()
        return jsonify({'status': 'started', 'mode': 'browser',
                        'interior_src': interior_src,
                        'exterior_src': exterior_src})

    if monitor_thread is not None and monitor_thread.is_alive():
        return jsonify({'status': 'already_running', 'mode': 'local'})

    if not monitoring_active:
        monitoring_active = True
        browser_capture_mode = False
        monitor_thread = threading.Thread(
            target=monitoring_loop,
            kwargs={'interior_src': interior_src, 'exterior_src': exterior_src},
            daemon=True
        )
        monitor_thread.start()
        return jsonify({'status': 'started', 'mode': 'local',
                        'interior_src': interior_src,
                        'exterior_src': exterior_src})
    return jsonify({'status': 'already_running'})

@app.route('/api/monitoring/stop', methods=['POST'])
def stop_monitoring():
    global monitoring_active, browser_capture_mode, monitor_thread, browser_worker_thread
    monitoring_active = False
    browser_capture_mode = False
    if monitor_thread is not None and monitor_thread.is_alive() and \
            monitor_thread is not threading.current_thread():
        monitor_thread.join(timeout=1.0)
    if monitor_thread is not None and not monitor_thread.is_alive():
        monitor_thread = None
    if browser_worker_thread is not None and browser_worker_thread.is_alive():
        browser_worker_thread.join(timeout=1.0)
    if browser_worker_thread is not None and not browser_worker_thread.is_alive():
        browser_worker_thread = None
    return jsonify({'status': 'stopped'})

@app.route('/api/monitoring/status')
def monitoring_status():
    return jsonify({'active': monitoring_active})

@app.route('/api/monitoring/logs')
def monitoring_logs():
    return jsonify(list(debug_logs))

@app.route('/api/alerts/history')
def api_alert_history():
    limit  = int(request.args.get('limit', 50))
    alerts = get_alert_history(limit=limit)
    return jsonify([{
        'id': a.id, 'type': a.alert_type, 'severity': a.severity,
        'message': a.message, 'timestamp': a.timestamp.isoformat(),
        'responded': a.responded,
    } for a in alerts])

@app.route('/api/emergency_contacts', methods=['GET', 'POST'])
def emergency_contacts():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    if request.method == 'POST':
        d = request.get_json()
        contact = EmergencyContact(
            user_id=session['user_id'],
            name=d.get('name', ''),
            phone=d.get('phone', ''),
            relationship_type=d.get('relationship', ''),
        )
        db.add(contact)
        db.commit()
        return jsonify({'status': 'added', 'id': contact.id})
    contacts = db.query(EmergencyContact).filter_by(user_id=session['user_id']).all()
    return jsonify([{'id': c.id, 'name': c.name, 'phone': c.phone,
                     'relationship': c.relationship_type} for c in contacts])

@app.route('/api/emergency_contacts/<int:cid>', methods=['DELETE'])
def delete_contact(cid):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    contact = db.query(EmergencyContact).filter_by(
        id=cid, user_id=session['user_id']).first()
    if contact:
        db.delete(contact)
        db.commit()
        return jsonify({'status': 'deleted'})
    return jsonify({'error': 'Not found'}), 404

# ── SocketIO ───────────────────────────────────────────────────────
@socketio.on('connect')
def on_connect():
    emit('monitoring_state', {'active': monitoring_active})


@socketio.on('browser_frame')
def on_browser_frame(data):
    global browser_frame_times, browser_worker_thread
    if not data:
        return
    if not monitoring_active:
        return
    side = (data.get('side') or 'interior').lower()
    image_data = data.get('image') or ''
    if side not in ('interior', 'exterior') or not isinstance(image_data, str):
        return
    if len(image_data) > BROWSER_FRAME_MAX_BYTES:
        log_debug(f"Dropped oversized browser {side} frame ({len(image_data)} bytes)")
        return

    now = time.monotonic()
    with frame_lock:
        if now - browser_frame_times[side] < BROWSER_FRAME_MIN_INTERVAL:
            return
        browser_frame_times[side] = now

    with browser_frame_lock:
        browser_pending_frames[side] = image_data


@socketio.on('alert_responded')
def on_alert_responded(data):
    alert_system.mark_responded(data.get('alert_id'))

if __name__ == '__main__':
    init_db()
    ssl_cert = os.environ.get('SSL_CERT_FILE')
    ssl_key = os.environ.get('SSL_KEY_FILE')
    ssl_context = (ssl_cert, ssl_key) if ssl_cert and ssl_key else None
    socketio.run(app, host=get_bind_host(), port=Config.PORT, debug=False,
                 allow_unsafe_werkzeug=True, ssl_context=ssl_context)
