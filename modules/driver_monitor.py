"""
Driver Monitoring Module
────────────────────────────────────────────────────────────────────
Detects:
  • Drowsiness   — Eye Aspect Ratio (EAR) via MediaPipe Face Mesh
  • Yawning      — Mouth Aspect Ratio (MAR)
  • Sleeping     — Prolonged eye closure
  • Head pose    — Yaw / Pitch / Roll via solvePnP
  • Phone usage  — Hand-near-ear heuristic via MediaPipe Hands
  • Distraction  — Head yaw deviation from centre

All detectors work on standard RGB frames (no GPU required).
"""

import cv2
import numpy as np
import time
import math
from collections import deque

mp = None
mp_solutions = None
mp_python = None
mp_vision = None
try:
    import mediapipe as mp
    try:
        mp_solutions = mp.solutions
    except AttributeError:
        try:
            import mediapipe.solutions as mp_solutions
        except ImportError:
            mp_solutions = None
    try:
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision
    except ImportError:
        pass
except ImportError:
    pass

MEDIAPIPE_OK = mp_solutions is not None
MEDIAPIPE_TASKS_OK = mp_python is not None and mp_vision is not None
if not MEDIAPIPE_OK and not MEDIAPIPE_TASKS_OK:
    print("[DriverMonitor] MediaPipe face APIs unavailable — using basic face detection")

from config import Config

try:
    from ultralytics import YOLO
    YOLO_OK = True
except ImportError:
    YOLO_OK = False

# ─── MediaPipe indices ────────────────────────────────────────────
# Face mesh landmarks used for EAR (both eyes)
LEFT_EYE_IDX  = [362, 385, 387, 263, 373, 380]
RIGHT_EYE_IDX = [33,  160, 158, 133, 153, 144]
# Mouth corners + top + bottom
MOUTH_IDX     = [61, 291, 13, 14]   # left, right, top, bottom
# For head pose (canonical 3-D model points)
_3D_MODEL_POINTS = np.array([
    [0.0,   0.0,    0.0],     # Nose tip
    [0.0,  -330.0, -65.0],    # Chin
    [-225.0, 170.0, -135.0],  # Left eye left corner
    [225.0,  170.0, -135.0],  # Right eye right corner
    [-150.0, -150.0, -125.0], # Left mouth corner
    [150.0,  -150.0, -125.0], # Right mouth corner
], dtype=np.float64)

FACE_2D_IDX = [1, 152, 263, 33, 287, 57]   # nose, chin, L-eye, R-eye, L-mouth, R-mouth


class DriverMonitor:
    """Real-time driver behaviour monitor."""

    def __init__(self, yolo_model=None):
        self.cfg = Config()
        self._fps_counter = deque(maxlen=30)
        self.fps = 0.0

        # Counters
        self._eyes_closed_start = None
        self._yawn_start        = None
        self._distraction_start = None
        self._phone_start       = None
        self._phone_detector    = yolo_model
        self._phone_frame_count = 0
        self._last_phone_objects = []
        try:
            cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
            self._face_cascade = cv2.CascadeClassifier(cascade_path)
            if self._face_cascade.empty():
                self._face_cascade = None
        except (AttributeError, cv2.error):
            self._face_cascade = None

        # State memory (for UI)
        self._last_results = {}

        self._face_mesh = None
        self._face_landmarker = None
        self._hands = None
        if MEDIAPIPE_OK:
            mp_face = mp_solutions.face_mesh
            mp_hands = mp_solutions.hands
            self._face_mesh = mp_face.FaceMesh(
                max_num_faces=1,
                refine_landmarks=self.cfg.REFINE_FACE_LANDMARKS,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            self._hands = mp_hands.Hands(
                max_num_hands=self.cfg.MAX_HANDS,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
        elif MEDIAPIPE_TASKS_OK:
            try:
                options = mp_vision.FaceLandmarkerOptions(
                    base_options=mp_python.BaseOptions(
                        model_asset_path=self.cfg.FACE_LANDMARKER_MODEL_PATH
                    ),
                    running_mode=mp_vision.RunningMode.IMAGE,
                    num_faces=1,
                    min_face_detection_confidence=0.4,
                    min_face_presence_confidence=0.4,
                    min_tracking_confidence=0.4,
                )
                self._face_landmarker = mp_vision.FaceLandmarker.create_from_options(options)
                print(f"[DriverMonitor] FaceLandmarker loaded: {self.cfg.FACE_LANDMARKER_MODEL_PATH}")
            except Exception as exc:
                print(f"[DriverMonitor] FaceLandmarker unavailable: {exc}")

        if self._phone_detector is None and YOLO_OK:
            try:
                self._phone_detector = YOLO(self.cfg.YOLO_MODEL_PATH)
                print(f"[DriverMonitor] Phone detector loaded: {self.cfg.YOLO_MODEL_PATH}")
            except Exception as exc:
                print(f"[DriverMonitor] Phone detector unavailable: {exc}")

    # ─────────────────────────────────────────────
    #  Public API
    # ─────────────────────────────────────────────

    def process(self, frame: np.ndarray, input_valid: bool = True) -> dict:
        """Run all detectors on a BGR frame; return result dict."""
        t0 = time.time()
        h, w = frame.shape[:2]
        rgb  = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        results = {
            'drowsy':       False,
            'sleeping':     False,
            'yawning':      False,
            'phone_usage':  False,
            'phone_detected': False,
            'phone_objects': [],
            'distracted':   False,
            'ear':          None,
            'mar':          None,
            'head_yaw':     None,
            'head_pitch':   None,
            'head_roll':    None,
            'face_detected': False,
            'face_analysis_ready': False,
            'face_box':     None,
            'driver_monitoring_available': bool(self._face_mesh or self._face_landmarker),
            'monitoring_valid': bool(input_valid),
        }

        if not input_valid:
            self._reset_temporal_state()
            self._last_phone_objects = []
            results['monitoring_valid'] = False
            return results

        if self._face_mesh is None and self._face_landmarker is None:
            self._reset_temporal_state()
            self._last_phone_objects = []
            face_box = self._detect_face_fallback(frame)
            results['face_box'] = face_box
            results['face_detected'] = face_box is not None
            results['monitoring_valid'] = False
            self._last_results = results
            return results

        self._phone_frame_count += 1
        if (self._phone_frame_count - 1) % max(1, self.cfg.PHONE_YOLO_INTERVAL) == 0:
            self._last_phone_objects = self._detect_phone_objects(frame)
        results['phone_objects'] = list(self._last_phone_objects)
        results['phone_detected'] = bool(results['phone_objects'])

        # ── Face Mesh ──────────────────────────────────────
        if self._face_mesh is not None:
            face_out = self._face_mesh.process(rgb)
            face_landmarks = (
                face_out.multi_face_landmarks[0].landmark
                if face_out.multi_face_landmarks else None
            )
        else:
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            face_out = self._face_landmarker.detect(image)
            face_landmarks = face_out.face_landmarks[0] if face_out.face_landmarks else None

        face_box = None
        if face_landmarks:
            lm = face_landmarks
            results['face_detected'] = True

            coords = np.array([[p.x * w, p.y * h] for p in lm])
            face_box = (
                int(coords[:, 0].min()), int(coords[:, 1].min()),
                int(coords[:, 0].max()), int(coords[:, 1].max())
            )
            results['face_box'] = face_box
            face_width = coords[:, 0].max() - coords[:, 0].min()
            if face_width < w * self.cfg.MIN_FACE_WIDTH_RATIO:
                self._reset_temporal_state()
                self._fps_counter.append(time.time() - t0)
                if len(self._fps_counter) > 1:
                    avg = sum(self._fps_counter) / len(self._fps_counter)
                    self.fps = round(1.0 / avg, 1) if avg > 0 else 0
                self._last_results = results
                return results
            results['face_analysis_ready'] = True

            # EAR
            ear = self._compute_ear(coords)
            results['ear'] = round(ear, 3)
            if ear < self.cfg.EAR_THRESHOLD:
                if self._eyes_closed_start is None:
                    self._eyes_closed_start = time.time()
            else:
                self._eyes_closed_start = None

            closed_for = (time.time() - self._eyes_closed_start
                          if self._eyes_closed_start is not None else 0.0)
            results['drowsy'] = closed_for >= self.cfg.DROWSY_SECONDS
            results['sleeping'] = closed_for >= self.cfg.SLEEPING_SECONDS

            # MAR / Yawn
            mar = self._compute_mar(coords)
            results['mar'] = round(mar, 3)
            if mar > self.cfg.MAR_THRESHOLD:
                if self._yawn_start is None:
                    self._yawn_start = time.time()
            else:
                self._yawn_start = None
            yawn_for = (time.time() - self._yawn_start
                        if self._yawn_start is not None else 0.0)
            results['yawning'] = yawn_for >= self.cfg.YAWN_SECONDS

            # Head pose
            yaw, pitch, roll = self._head_pose(coords, w, h)
            results['head_yaw']   = round(yaw,   1)
            results['head_pitch'] = round(pitch, 1)
            results['head_roll']  = round(roll,  1)
            pose_out_of_range = (
                abs(yaw)   > self.cfg.HEAD_YAW_THRESHOLD or
                abs(pitch) > self.cfg.HEAD_PITCH_THRESHOLD
            )
            if pose_out_of_range:
                if self._distraction_start is None:
                    self._distraction_start = time.time()
            else:
                self._distraction_start = None
            results['distracted'] = (
                self._distraction_start is not None and
                time.time() - self._distraction_start >= self.cfg.DISTRACTION_SECONDS
            )
        else:
            # Tracking loss is unknown, not proof of distraction.
            self._reset_temporal_state()

        # ── Hand detection — phone usage heuristic ─────────
        hand_out = self._hands.process(rgb) if self._hands is not None else None
        if hand_out and hand_out.multi_hand_landmarks and face_landmarks:
            hand_near_face = self._detect_phone_usage(
                hand_out.multi_hand_landmarks,
                face_landmarks,
                w, h
            )
            phone_near_face = hand_near_face or self._phone_overlaps_face(
                results['phone_objects'], face_box, w, h
            )
            if phone_near_face and results['phone_detected']:
                if self._phone_start is None:
                    self._phone_start = time.time()
            else:
                self._phone_start = None
            results['phone_usage'] = (
                self._phone_start is not None and
                time.time() - self._phone_start >= self.cfg.PHONE_SECONDS
            )
        else:
            phone_near_face = self._phone_overlaps_face(
                results['phone_objects'], face_box, w, h
            )
            if phone_near_face:
                if self._phone_start is None:
                    self._phone_start = time.time()
            else:
                self._phone_start = None
            results['phone_usage'] = (
                self._phone_start is not None and
                time.time() - self._phone_start >= self.cfg.PHONE_SECONDS
            )

        # ── FPS ─────────────────────────────────────────────
        self._fps_counter.append(time.time() - t0)
        if len(self._fps_counter) > 1:
            avg = sum(self._fps_counter) / len(self._fps_counter)
            self.fps = round(1.0 / avg, 1) if avg > 0 else 0

        self._last_results = results
        return results

    def _detect_phone_objects(self, frame):
        if self._phone_detector is None:
            return []
        try:
            detections = self._phone_detector.predict(
                frame,
                conf=self.cfg.PHONE_YOLO_CONFIDENCE,
                classes=[67],
                imgsz=self.cfg.YOLO_IMAGE_SIZE,
                verbose=False,
            )
            phones = []
            for detection in detections:
                for box in detection.boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    phones.append({
                        'label': 'Cell phone',
                        'conf': float(box.conf[0]),
                        'box': (x1, y1, x2, y2),
                        'class': 67,
                    })
            return phones
        except Exception:
            return []

    @staticmethod
    def _phone_overlaps_face(phone_objects, face_box, w, h):
        if not phone_objects or face_box is None:
            return False
        fx1, fy1, fx2, fy2 = face_box
        pad_x = int(w * 0.08)
        pad_y = int(h * 0.12)
        fx1 -= pad_x
        fy1 -= pad_y
        fx2 += pad_x
        fy2 += pad_y
        for phone in phone_objects:
            x1, y1, x2, y2 = phone['box']
            if x1 < fx2 and x2 > fx1 and y1 < fy2 and y2 > fy1:
                return True
        return False

    def _reset_temporal_state(self):
        self._eyes_closed_start = None
        self._yawn_start = None
        self._distraction_start = None
        self._phone_start = None

    # ─────────────────────────────────────────────
    #  Annotation
    # ─────────────────────────────────────────────

    def annotate(self, frame: np.ndarray, results: dict) -> np.ndarray:
        h, w = frame.shape[:2]

        # Status bar background
        cv2.rectangle(frame, (0, 0), (w, 32), (20, 20, 20), -1)

        # Keep diagnostics within the frame even when the camera width changes.
        margin = 8
        gap = 16
        font_scale = 0.5
        font = cv2.FONT_HERSHEY_SIMPLEX
        baseline_y = 22

        # EAR / head pose / FPS
        ear_txt = f"EAR:{results['ear']:.2f}" if results['ear'] is not None else "EAR:--"
        fps_txt = f"FPS:{self.fps}"
        yaw_txt = (
            f"Yaw:{results['head_yaw']:+.0f} deg"
            if results['head_yaw'] is not None else None
        )
        ear_width = cv2.getTextSize(ear_txt, font, font_scale, 1)[0][0]
        fps_width = cv2.getTextSize(fps_txt, font, font_scale, 1)[0][0]
        fps_x = max(margin, w - fps_width - margin)
        cv2.putText(frame, ear_txt, (margin, baseline_y), font, font_scale,
                    (255, 255, 255), 1, cv2.LINE_AA)
        if yaw_txt:
            yaw_width = cv2.getTextSize(yaw_txt, font, font_scale, 1)[0][0]
            yaw_x = max(margin + ear_width + gap, (w - yaw_width) // 2)
            yaw_x = min(yaw_x, fps_x - yaw_width - gap)
            if yaw_x >= margin + ear_width + gap:
                cv2.putText(frame, yaw_txt, (yaw_x, baseline_y), font, font_scale,
                            (200, 220, 255), 1, cv2.LINE_AA)
        cv2.putText(frame, fps_txt, (fps_x, baseline_y), font, font_scale,
                    (180, 255, 180), 1, cv2.LINE_AA)

        # Alert overlays
        for phone in results.get('phone_objects', []):
            x1, y1, x2, y2 = phone['box']
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
            cv2.putText(frame, f"Cell phone {phone['conf']:.0%}",
                        (x1, max(45, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (0, 0, 255), 1)

        alerts_active = []
        if results['sleeping']:
            alerts_active.append(('SLEEPING!', (0, 0, 200)))
        elif results['drowsy']:
            alerts_active.append(('DROWSY!', (0, 120, 255)))
        if results['yawning']:
            alerts_active.append(('YAWNING', (0, 200, 255)))
        if results['phone_usage']:
            alerts_active.append(('PHONE USAGE!', (0, 0, 255)))
        if results['distracted']:
            alerts_active.append(('DISTRACTED!', (0, 165, 255)))

        for i, (txt, color) in enumerate(alerts_active):
            y = 65 + i * 35
            cv2.rectangle(frame, (0, y - 24), (len(txt) * 14 + 10, y + 5), color, -1)
            cv2.putText(frame, txt, (6, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (255, 255, 255), 2)

        if results.get('face_box'):
            x1, y1, x2, y2 = results['face_box']
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 200, 80), 2)
            if not results.get('driver_monitoring_available', True):
                label = 'FACE DETECTED - BASIC'
            elif not results.get('face_analysis_ready', False):
                label = 'FACE DETECTED - MOVE CLOSER'
            else:
                label = 'FACE DETECTED'
            cv2.putText(frame, label, (x1, max(45, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 80), 1)
        elif not results['face_detected']:
            cv2.putText(frame, 'NO FACE DETECTED', (w//2 - 100, h//2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        elif not results.get('driver_monitoring_available', True):
            cv2.putText(frame, 'FACE LANDMARKS UNAVAILABLE', (w//2 - 160, h//2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 165, 255), 2)

        return frame

    # ─────────────────────────────────────────────
    #  Internal Helpers
    # ─────────────────────────────────────────────

    @staticmethod
    def _euclidean(p1, p2) -> float:
        return math.hypot(p1[0] - p2[0], p1[1] - p2[1])

    def _detect_face_fallback(self, frame):
        if self._face_cascade is None:
            return None
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        height, width = gray.shape
        min_size = (max(24, int(width * 0.06)), max(24, int(height * 0.06)))
        faces = self._face_cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=min_size
        )
        if len(faces) == 0:
            return None
        x, y, face_width, face_height = max(faces, key=lambda box: box[2] * box[3])
        return int(x), int(y), int(x + face_width), int(y + face_height)

    def _compute_ear(self, coords: np.ndarray) -> float:
        def _ear(idx):
            A = self._euclidean(coords[idx[1]], coords[idx[5]])
            B = self._euclidean(coords[idx[2]], coords[idx[4]])
            C = self._euclidean(coords[idx[0]], coords[idx[3]])
            return (A + B) / (2.0 * C + 1e-6)
        return (_ear(LEFT_EYE_IDX) + _ear(RIGHT_EYE_IDX)) / 2.0

    def _compute_mar(self, coords: np.ndarray) -> float:
        # vertical opening / horizontal width
        vert = self._euclidean(coords[MOUTH_IDX[2]], coords[MOUTH_IDX[3]])
        horiz = self._euclidean(coords[MOUTH_IDX[0]], coords[MOUTH_IDX[1]])
        return vert / (horiz + 1e-6)

    def _head_pose(self, coords: np.ndarray, w: int, h: int):
        """Returns (yaw, pitch, roll) in degrees via solvePnP."""
        try:
            img_pts = np.array(
                [coords[i] for i in FACE_2D_IDX], dtype=np.float64
            )
            focal_len  = w
            cam_matrix = np.array(
                [[focal_len, 0, w / 2],
                 [0, focal_len, h / 2],
                 [0, 0, 1]], dtype=np.float64
            )
            dist_coefs = np.zeros((4, 1))
            ok, rvec, _ = cv2.solvePnP(
                _3D_MODEL_POINTS, img_pts, cam_matrix, dist_coefs,
                flags=cv2.SOLVEPNP_ITERATIVE
            )
            if not ok:
                return 0.0, 0.0, 0.0
            rmat, _ = cv2.Rodrigues(rvec)
            # Euler angles
            sy = math.sqrt(rmat[0, 0] ** 2 + rmat[1, 0] ** 2)
            singular = sy < 1e-6
            if not singular:
                pitch = math.degrees(math.atan2( rmat[2, 1], rmat[2, 2]))
                yaw   = math.degrees(math.atan2(-rmat[2, 0], sy))
                roll  = math.degrees(math.atan2( rmat[1, 0], rmat[0, 0]))
            else:
                pitch = math.degrees(math.atan2(-rmat[1, 2], rmat[1, 1]))
                yaw   = math.degrees(math.atan2(-rmat[2, 0], sy))
                roll  = 0.0
            return yaw, pitch, roll
        except Exception:
            return 0.0, 0.0, 0.0

    def _detect_phone_usage(self, hand_landmarks_list, face_landmarks, w, h):
        """
        Heuristic: if a hand wrist is within ~20% frame width of the ear
        landmark, flag phone usage.
        """
        try:
            # Ear landmark indices in MediaPipe Face Mesh
            LEFT_EAR_IDX  = 234
            RIGHT_EAR_IDX = 454
            ear_l = face_landmarks[LEFT_EAR_IDX]
            ear_r = face_landmarks[RIGHT_EAR_IDX]
            ear_pts = [
                np.array([ear_l.x * w, ear_l.y * h]),
                np.array([ear_r.x * w, ear_r.y * h]),
            ]
            threshold = w * self.cfg.PHONE_DISTANCE_RATIO

            for hand_lm in hand_landmarks_list:
                wrist = hand_lm.landmark[0]   # index 0 is wrist
                wrist_pt = np.array([wrist.x * w, wrist.y * h])
                for ear_pt in ear_pts:
                    if np.linalg.norm(wrist_pt - ear_pt) < threshold:
                        return True
            return False
        except Exception:
            return False
