# Real-Time Driver & Road Monitoring with Accident Prevention System


---

## 🗂 Project Structure

```
driver_monitor_system/
├── app.py                    # Main Flask + SocketIO application
├── config.py                 # All configuration constants
├── requirements.txt          # Python dependencies
├── database/
│   └── db.py                 # SQLAlchemy models + DB helpers
├── modules/
│   ├── driver_monitor.py     # EAR / MAR / head-pose / phone detection
│   ├── road_monitor.py       # Lane / YOLO / speed detection
│   └── alert_system.py       # Alert rules, escalation, SMS
├── templates/
│   ├── base.html             # Nav + layout wrapper
│   ├── index.html            # Main dashboard
│   ├── login.html
│   ├── register.html
│   ├── history.html
│   └── settings.html
└── static/
    ├── css/style.css         # Dark industrial HUD theme
    ├── css/navigation.css    # Dashboard navigation drawer styles
    ├── js/app.js             # WebSocket + real-time UI
    └── js/navigation.js      # Leaflet map, location search + route planning
```

The dashboard MAP drawer uses Leaflet with OpenStreetMap tiles, Nominatim
location search, and OSRM driving routes. It is independent of the monitoring
and alert flows, and route duration is an estimate without live traffic.

---

## ⚙ Setup & Run

### 1. Create virtual environment
```bash
py -3.12 -m venv venv
venv\Scripts\activate
```

Use Python 3.11 or 3.12. The legacy MediaPipe Solutions API used by this project
is not available in the newest MediaPipe builds for Python 3.13.

### 2. Install dependencies
```bash
py -3.12 -m pip install -r requirements.txt
```

> **Note**: `ultralytics` (YOLOv8) requires ~800 MB on first run to download model weights.  
> Driver alerts are disabled when MediaPipe or the camera is unavailable; the system does not
> generate synthetic driver detections.

### 3. Run the application
```bash
py -3.12 app.py
```

Open browser at: **http://localhost:5000**

For camera access from a public/global host, the page must be served over HTTPS.
The browser blocks `getUserMedia()` on ordinary public HTTP URLs. If this app
terminates TLS itself, set `SSL_CERT_FILE` and `SSL_KEY_FILE` to the certificate
and private-key paths before starting it. If a hosting provider terminates TLS,
use its HTTPS URL and keep the app bound to `0.0.0.0`.

### Voice alert acknowledgement

On the dashboard, the browser's Web Speech API speaks each new safety alert and
listens for **“DRMS”**, **“Acknowledge”**, **“I accept”**, or **“DRMS, I accept”**
while that alert is active. Click **VOICE: ENABLE** in the dashboard to grant microphone
permission; browsers require this explicit user action before allowing voice
recognition. The permission check stops its temporary microphone stream
immediately, and recognition listens only while an alert is active. The
existing per-alert and emergency-modal acknowledgement buttons continue to
work and cancel the same server-side escalation timer. Voice acknowledgement
requires a browser that supports SpeechRecognition (commonly Chrome or Edge)
and a secure context such as HTTPS or localhost. Browser speech recognition may
use a browser-managed online service; voice input is not sent to this
application by the assistant. If recognition is unavailable or permission is
denied, spoken warnings, dashboard alerts, beep, acknowledgement buttons, and
SMS escalation remain available.

### GPS turn-by-turn navigation

Open **MAP**, select a destination from the location suggestions, then choose
**START NAVIGATION**. The browser asks for location permission on start; the
feature uses GPS only while navigation is active and **STOP NAVIGATION** releases
its location watcher. **USE CURRENT GPS** can also set the route origin for
**SHOW ROUTE**. GPS access requires HTTPS or `localhost`; allow location access
for this site and enable the device's location services.

The map uses OpenStreetMap tiles and Nominatim location search with OSRM's
public driving router. Route guidance includes turn instructions, GPS progress,
estimated remaining distance/time, arrival detection, and off-route rerouting.
Voice guidance can be muted independently from the safety-alert voice assistant.
Navigation estimates do not include live traffic, and public OSM/OSRM services
are best-effort services without an availability guarantee; use them
respectfully and retry later if unavailable. A routing failure during rerouting
leaves the last valid route visible. This feature adds no server-side route or
off-route reporting and does not trigger emergency SMS; existing monitoring
location sharing and emergency workflows remain unchanged.

To try it locally on Windows, run the app from the VS Code terminal:

```powershell
.\.venv\Scripts\python.exe app.py
```

Open `http://localhost:5000`, sign in, open **MAP**, choose a destination, and
press **START NAVIGATION**. For Windows device testing, allow location access
for the browser in Windows privacy settings and the browser site-permission
prompt. A desktop may provide coarse or no GPS; a phone or GPS-enabled device
usually provides better fixes. Use **STOP NAVIGATION** to release tracking.

### Emergency SMS configuration
 
Add these environment variables before starting the app:

```powershell
$env:SMS_GATE_USERNAME = "your-smsgate-username"
$env:SMS_GATE_PASSWORD = "your-smsgate-password"
```

When a `HIGH` or `CRITICAL` alert is not acknowledged within
`ALERT_ESCALATION_SECONDS` (4 seconds by default), the app sends one SMS to
every emergency contact saved in Dashboard > Settings. The message includes a
Google Maps link when browser location permission is available; otherwise it
states that the location is unavailable. Camera and location access require a
secure context such as HTTPS (localhost is also allowed).

<!-- ### 4. Demo login
```
Email:    demo@dms.com
Password: demo1234
``` -->

---

## 🎥 Camera Setup

| Camera | Source Index | Purpose |
|--------|-------------|---------|
| Interior | `0` (default) | Driver face monitoring |
| Exterior | `1` | Road / road hazard monitoring |

Edit `app.py` line `monitoring_loop(interior_src=0, exterior_src=1)` to change indices.  
If cameras are unavailable, the system runs in **demo mode** with simulated data.

---

## 🧠 Detection Features

### Interior Camera (Driver)
| Feature | Method | Threshold |
|---------|--------|-----------|
| Drowsiness | Eye Aspect Ratio (EAR) via MediaPipe | EAR < 0.25 for 20 frames |
| Sleeping | Prolonged eye closure | > 3 seconds |
| Yawning | Mouth Aspect Ratio (MAR) | MAR > 0.6 for 15 frames |
| Phone Usage | Hand-near-ear heuristic | Wrist within 20% frame width of ear |
| Distraction | Head pose (solvePnP) | Yaw > 30° or Pitch > 20° |

### Exterior Camera (Road)
| Feature | Method |
|---------|--------|
| Lane Deviation | Canny + Hough lines + ROI masking |
| Vehicle Detection | YOLOv8 (classes: car, motorcycle, bus, truck) |
| Pedestrian Detection | YOLOv8 (class: person) |
| Obstacle Detection | YOLOv8 (other classes) |
| Speed Estimation | Dense optical flow (Farneback) magnitude |

---

## 🚨 Alert System

```
Risk Detected → Audio beep + Visual overlay on dashboard
      ↓
   No response within 10 seconds
      ↓
Emergency SMS → All saved emergency contacts 
```

```

## 📦 Dependencies Summary

| Package | Purpose |
|---------|---------|
| Flask + Flask-SocketIO | Web server + real-time WebSocket |
| OpenCV | Frame capture, Canny/Hough, optical flow |
| MediaPipe | Face mesh (EAR, MAR, head pose) + Hands |
| Ultralytics (YOLOv8) | Object detection (vehicles, pedestrians) |
| SQLAlchemy | ORM for SQLite database |
| SMSGate | Emergency SMS |

---

## 🔮 Future Enhancements
- IoT integration for vehicle CAN bus data
- Cloud dashboard for fleet monitoring
- Firebase real-time database sync
- Autonomous braking integration
- Mobile companion app
