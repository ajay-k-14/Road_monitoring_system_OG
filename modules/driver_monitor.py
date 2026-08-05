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
try:
    import mediapipe as mp
    try:
        mp_solutions = mp.solutions
    except AttributeError:
        import mediapipe.solutions as mp_solutions
    MEDIAPIPE_OK = True
except ImportError:
    MEDIAPIPE_OK = False
    print("[DriverMonitor] MediaPipe not installed — running in demo mode")
except Exception as exc:
    MEDIAPIPE_OK = False
    print(f"[DriverMonitor] MediaPipe import failed ({exc}) — running in demo mode")

from config import Config

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

    def __init__(self):
        self.cfg = Config()
        self._fps_counter = deque(maxlen=30)
        self.fps = 0.0

        # Counters
        self._ear_frames   = 0
        self._mar_frames   = 0
        self._sleep_start  = None

        # State memory (for UI)
        self._last_results = {}

        if MEDIAPIPE_OK:
            mp_face = mp_solutions.face_mesh
            mp_hands = mp_solutions.hands
            self._face_mesh = mp_face.FaceMesh(
                max_num_faces=1,
                refine_landmarks=True,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            self._hands = mp_hands.Hands(
                max_num_hands=2,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
        else:
            self._face_mesh = None
            self._hands     = None

    # ─────────────────────────────────────────────
    #  Public API
    # ─────────────────────────────────────────────

    def process(self, frame: np.ndarray) -> dict:
        """Run all detectors on a BGR frame; return result dict."""
        t0 = time.time()
        h, w = frame.shape[:2]
        rgb  = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        results = {
            'drowsy':       False,
            'sleeping':     False,
            'yawning':      False,
            'phone_usage':  False,
            'distracted':   False,
            'ear':          None,
            'mar':          None,
            'head_yaw':     None,
            'head_pitch':   None,
            'head_roll':    None,
            'face_detected': False,
        }

        if not MEDIAPIPE_OK:
            # Demo mode — simulate cycling states
            t = time.time() % 60
            results['drowsy']      = (10 < t < 15)
            results['phone_usage'] = (30 < t < 38)
            results['distracted']  = (50 < t < 55)
            results['ear']         = round(0.30 - 0.10 * (1 if results['drowsy'] else 0), 3)
            results['head_yaw']    = 25 if results['distracted'] else 5
            results['face_detected'] = True
            return results

        # ── Face Mesh ──────────────────────────────────────
        face_out = self._face_mesh.process(rgb)
        if face_out.multi_face_landmarks:
            lm = face_out.multi_face_landmarks[0].landmark
            results['face_detected'] = True

            coords = np.array([[p.x * w, p.y * h] for p in lm])

            # EAR
            ear = self._compute_ear(coords)
            results['ear'] = round(ear, 3)
            if ear < self.cfg.EAR_THRESHOLD:
                self._ear_frames += 1
                if self._sleep_start is None:
                    self._sleep_start = time.time()
            else:
                self._ear_frames = 0
                self._sleep_start = None

            results['drowsy']   = self._ear_frames >= self.cfg.EAR_CONSEC_FRAMES
            results['sleeping'] = (
                self._sleep_start is not None and
                (time.time() - self._sleep_start) > 3.0
            )

            # MAR / Yawn
            mar = self._compute_mar(coords)
            results['mar'] = round(mar, 3)
            if mar > self.cfg.MAR_THRESHOLD:
                self._mar_frames += 1
            else:
                self._mar_frames = max(0, self._mar_frames - 1)
            results['yawning'] = self._mar_frames >= self.cfg.MAR_CONSEC_FRAMES

            # Head pose
            yaw, pitch, roll = self._head_pose(coords, w, h)
            results['head_yaw']   = round(yaw,   1)
            results['head_pitch'] = round(pitch, 1)
            results['head_roll']  = round(roll,  1)
            results['distracted'] = (
                abs(yaw)   > self.cfg.HEAD_YAW_THRESHOLD or
                abs(pitch) > self.cfg.HEAD_PITCH_THRESHOLD
            )
        else:
            # No face — possible distraction / looking away
            self._ear_frames = 0
            results['distracted'] = True

        # ── Hand detection — phone usage heuristic ─────────
        hand_out = self._hands.process(rgb)
        if hand_out.multi_hand_landmarks and face_out.multi_face_landmarks:
            results['phone_usage'] = self._detect_phone_usage(
                hand_out.multi_hand_landmarks,
                face_out.multi_face_landmarks[0].landmark,
                w, h
            )

        # ── FPS ─────────────────────────────────────────────
        self._fps_counter.append(time.time() - t0)
        if len(self._fps_counter) > 1:
            avg = sum(self._fps_counter) / len(self._fps_counter)
            self.fps = round(1.0 / avg, 1) if avg > 0 else 0

        self._last_results = results
        return results

    # ─────────────────────────────────────────────
    #  Annotation
    # ─────────────────────────────────────────────

    def annotate(self, frame: np.ndarray, results: dict) -> np.ndarray:
        h, w = frame.shape[:2]

        # Status bar background
        cv2.rectangle(frame, (0, 0), (w, 32), (20, 20, 20), -1)

        # EAR / MAR
        ear_txt = f"EAR:{results['ear']:.2f}" if results['ear'] is not None else "EAR:--"
        cv2.putText(frame, ear_txt, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (255, 255, 255), 1)
        if results['head_yaw'] is not None:
            cv2.putText(frame, f"Yaw:{results['head_yaw']:+.0f}°", (130, 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 220, 255), 1)
        cv2.putText(frame, f"FPS:{self.fps}", (w - 75, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 255, 180), 1)

        # Alert overlays
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

        # Face not detected indicator
        if not results['face_detected']:
            cv2.putText(frame, 'NO FACE DETECTED', (w//2 - 100, h//2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

        return frame

    # ─────────────────────────────────────────────
    #  Internal Helpers
    # ─────────────────────────────────────────────

    @staticmethod
    def _euclidean(p1, p2) -> float:
        return math.hypot(p1[0] - p2[0], p1[1] - p2[1])

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
            threshold = w * 0.20

            for hand_lm in hand_landmarks_list:
                wrist = hand_lm.landmark[0]   # index 0 is wrist
                wrist_pt = np.array([wrist.x * w, wrist.y * h])
                for ear_pt in ear_pts:
                    if np.linalg.norm(wrist_pt - ear_pt) < threshold:
                        return True
            return False
        except Exception:
            return False
