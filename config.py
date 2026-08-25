"""
Configuration for Real-Time Driver & Road Monitoring System
"""
import os


class Config:
    SECRET_KEY          = os.environ.get('SECRET_KEY', 'dms-secret-key-change-in-prod')
    DATABASE_URL        = os.environ.get('DATABASE_URL', 'sqlite:///driver_monitor.db')
    DEBUG               = os.environ.get('DEBUG', 'True') == 'True'

    # ── Driver Monitor Thresholds ────────────────────────
    # Eye Aspect Ratio: eyes closed below this value
    EAR_THRESHOLD       = 0.25
    # Time-based persistence avoids changing behavior when FPS changes.
    DROWSY_SECONDS      = 1.5
    SLEEPING_SECONDS     = 3.0
    # Mouth Aspect Ratio: yawning above this value
    MAR_THRESHOLD       = 0.6
    YAWN_SECONDS         = 1.0
    # Head pose: degrees before "distracted" fires
    HEAD_PITCH_THRESHOLD = 20   # nodding forward/backward
    HEAD_YAW_THRESHOLD   = 30   # turning left/right
    DISTRACTION_SECONDS  = 0.8
    PHONE_SECONDS         = 0.7
    PHONE_DISTANCE_RATIO = 0.12
    PHONE_YOLO_CONFIDENCE = 0.35
    MIN_FACE_WIDTH_RATIO = 0.12

    # ── Road Monitor Thresholds ──────────────────────────
    # Lane deviation: fraction of frame width
    LANE_DEVIATION_THRESHOLD = 0.15
    # Speed over which alert fires (km/h) — estimated
    SPEED_ALERT_KMH          = 80
    # YOLO confidence threshold
    YOLO_CONFIDENCE          = 0.45

    # ── Alert System ─────────────────────────────────────
    # Seconds before un-responded alert escalates to SMS
    ALERT_ESCALATION_SECONDS = 10
    # Twilio credentials (optional — for real SMS). Use environment variables in production.
    TWILIO_SID    = os.environ.get('TWILIO_SID', '')
    TWILIO_TOKEN  = os.environ.get('TWILIO_TOKEN', '')
    TWILIO_FROM   = os.environ.get('TWILIO_FROM', '')

    # ── Model Paths ──────────────────────────────────────
    YOLO_MODEL_PATH = os.environ.get('YOLO_MODEL_PATH', 'yolov8n.pt')

