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
    └── js/app.js             # WebSocket + real-time UI
```

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

<!-- Configure Twilio in `.env`: -->
```

## 📦 Dependencies Summary

| Package | Purpose |
|---------|---------|
| Flask + Flask-SocketIO | Web server + real-time WebSocket |
| OpenCV | Frame capture, Canny/Hough, optical flow |
| MediaPipe | Face mesh (EAR, MAR, head pose) + Hands |
| Ultralytics (YOLOv8) | Object detection (vehicles, pedestrians) |
| SQLAlchemy | ORM for SQLite database |
<!-- | Twilio (optional) | Emergency SMS | -->

---

## 🔮 Future Enhancements
- IoT integration for vehicle CAN bus data
- Cloud dashboard for fleet monitoring
- Firebase real-time database sync
- Autonomous braking integration
- Mobile companion app
