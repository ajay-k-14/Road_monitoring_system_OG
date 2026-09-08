"""
Configuration for Real-Time Driver & Road Monitoring System
"""
import os


class Config:
    SECRET_KEY          = os.environ.get('SECRET_KEY', 'dms-secret-key-change-in-prod')
    DATABASE_URL        = os.environ.get('DATABASE_URL', 'sqlite:///driver_monitor.db')
    DEBUG               = os.environ.get('DEBUG', 'False').lower() == 'true'
    HOST                = os.environ.get('HOST', os.environ.get('FLASK_RUN_HOST', '0.0.0.0'))
    PORT                = int(os.environ.get('PORT', '5000'))

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
    # Keep the CPU-bound vision pipeline bounded on laptops.
    PROCESSING_FPS          = int(os.environ.get('PROCESSING_FPS', '8'))
    MAX_FRAME_WIDTH         = int(os.environ.get('MAX_FRAME_WIDTH', '640'))
    CAMERA_CAPTURE_FPS      = int(os.environ.get('CAMERA_CAPTURE_FPS', '15'))
    CAMERA_CAPTURE_WIDTH    = int(os.environ.get('CAMERA_CAPTURE_WIDTH', '640'))
    CAMERA_CAPTURE_HEIGHT   = int(os.environ.get('CAMERA_CAPTURE_HEIGHT', '480'))
    ROAD_YOLO_INTERVAL      = int(os.environ.get('ROAD_YOLO_INTERVAL', '3'))
    PHONE_YOLO_INTERVAL     = int(os.environ.get('PHONE_YOLO_INTERVAL', '3'))
    YOLO_IMAGE_SIZE         = int(os.environ.get('YOLO_IMAGE_SIZE', '416'))
    TORCH_NUM_THREADS       = int(os.environ.get('TORCH_NUM_THREADS', '2'))
    REFINE_FACE_LANDMARKS   = os.environ.get('REFINE_FACE_LANDMARKS', 'false').lower() == 'true'
    MAX_HANDS               = int(os.environ.get('MAX_HANDS', '1'))

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
        # TWILIO_SID    = os.environ.get('TWILIO_SID', '')
        # TWILIO_TOKEN  = os.environ.get('TWILIO_TOKEN', '')
        # TWILIO_FROM   = os.environ.get('TWILIO_FROM', '')

    # ── Model Paths ──────────────────────────────────────
    YOLO_MODEL_PATH = os.environ.get('YOLO_MODEL_PATH', 'yolov8n.pt')

