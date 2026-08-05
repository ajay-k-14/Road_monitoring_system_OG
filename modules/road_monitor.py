"""
Road Monitoring Module
────────────────────────────────────────────────────────────────────
Detects:
  • Lane deviation   — Hough-line based lane centre tracking
  • Vehicles         — YOLOv8 / YOLOv5 object detection
  • Pedestrians      — YOLOv8 object detection
  • Obstacles        — Any detected object class not vehicle/pedestrian
  • Speed estimation — Optical flow magnitude converted to km/h estimate

Falls back gracefully when ultralytics is not installed.
"""

import cv2
import numpy as np
import time
import math
from collections import deque

try:
    from ultralytics import YOLO
    YOLO_OK = True
except ImportError:
    YOLO_OK = False
    print("[RoadMonitor] ultralytics not installed — YOLO disabled")

from config import Config

# COCO class names relevant to road monitoring
VEHICLE_CLASSES    = {2, 3, 5, 7}   # car, motorcycle, bus, truck
PEDESTRIAN_CLASSES = {0}             # person
ALL_ROAD_CLASSES   = VEHICLE_CLASSES | PEDESTRIAN_CLASSES | {1, 6, 8}  # +bicycle, train, boat

COCO_NAMES = {
    0: 'Person', 1: 'Bicycle', 2: 'Car', 3: 'Motorcycle',
    5: 'Bus', 7: 'Truck',
}


class RoadMonitor:
    """Real-time road hazard monitor using the exterior camera feed."""

    def __init__(self):
        self.cfg = Config()

        # YOLO model
        self._yolo = None
        if YOLO_OK:
            try:
                self._yolo = YOLO(self.cfg.YOLO_MODEL_PATH)
                print(f"[RoadMonitor] YOLO loaded: {self.cfg.YOLO_MODEL_PATH}")
            except Exception as e:
                print(f"[RoadMonitor] YOLO load failed: {e}")

        # Speed estimation state
        self._prev_gray      = None
        self._speed_history  = deque(maxlen=10)
        self._est_speed_kmh  = 0.0

        # Lane deviation state
        self._lane_centre_history = deque(maxlen=15)

    # ─────────────────────────────────────────────
    #  Public API
    # ─────────────────────────────────────────────

    def process(self, frame: np.ndarray) -> dict:
        h, w = frame.shape[:2]
        results = {
            'lane_deviation':       False,
            'deviation_side':       None,    # 'LEFT' | 'RIGHT'
            'deviation_pct':        0.0,
            'vehicles_detected':    [],
            'pedestrians_detected': [],
            'obstacles_detected':   [],
            'speed_kmh':            0.0,
            'over_speed':           False,
            'lane_centre_x':        w // 2,
            'frame_centre_x':       w // 2,
        }

        # ── Lane detection ────────────────────────────────
        self._detect_lanes(frame, results, h, w)

        # ── Object detection (YOLO) ───────────────────────
        if self._yolo is not None:
            self._detect_objects(frame, results)
        else:
            self._demo_objects(results, h, w)

        # ── Speed estimation via optical flow ─────────────
        self._estimate_speed(frame, results)

        return results

    def annotate(self, frame: np.ndarray, results: dict) -> np.ndarray:
        h, w = frame.shape[:2]

        # ── Draw lane lines and deviation indicator ───────
        self._draw_lane_overlay(frame, results, h, w)

        # ── Draw bounding boxes ───────────────────────────
        for obj in results['vehicles_detected']:
            x1, y1, x2, y2 = obj['box']
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 220, 0), 2)
            cv2.putText(frame, f"{obj['label']} {obj['conf']:.0%}",
                        (x1, y1 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (0, 220, 0), 1)

        for obj in results['pedestrians_detected']:
            x1, y1, x2, y2 = obj['box']
            cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 100, 0), 2)
            cv2.putText(frame, f"Person {obj['conf']:.0%}",
                        (x1, y1 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (255, 100, 0), 1)

        for obj in results['obstacles_detected']:
            x1, y1, x2, y2 = obj['box']
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 50, 255), 2)
            cv2.putText(frame, f"Obstacle {obj['conf']:.0%}",
                        (x1, y1 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (0, 50, 255), 1)

        # ── HUD bar ───────────────────────────────────────
        cv2.rectangle(frame, (0, 0), (w, 32), (20, 20, 20), -1)
        speed_color = (0, 60, 220) if results['over_speed'] else (180, 255, 180)
        cv2.putText(frame, f"Speed: ~{results['speed_kmh']:.0f} km/h",
                    (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, speed_color, 1)

        n_obj = (len(results['vehicles_detected']) +
                 len(results['pedestrians_detected']) +
                 len(results['obstacles_detected']))
        cv2.putText(frame, f"Objects: {n_obj}",
                    (240, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 220, 255), 1)

        # ── Alert banners ─────────────────────────────────
        banners = []
        if results['lane_deviation']:
            banners.append((f"LANE DEVIATION {results['deviation_side']}!", (0, 0, 200)))
        if results['over_speed']:
            banners.append((f"OVER SPEED {results['speed_kmh']:.0f}km/h!", (0, 0, 255)))
        if results['pedestrians_detected']:
            banners.append(('PEDESTRIAN AHEAD!', (0, 128, 255)))

        for i, (txt, color) in enumerate(banners):
            y = 65 + i * 35
            cv2.rectangle(frame, (0, y - 24), (len(txt) * 13 + 10, y + 5), color, -1)
            cv2.putText(frame, txt, (6, y), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                        (255, 255, 255), 2)

        return frame

    # ─────────────────────────────────────────────
    #  Lane Detection
    # ─────────────────────────────────────────────

    def _detect_lanes(self, frame, results, h, w):
        """Canny + Hough line lane detection with ROI masking."""
        gray    = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges   = cv2.Canny(blurred, 50, 150)

        # ROI: lower third of the frame
        roi_mask = np.zeros_like(edges)
        roi_pts  = np.array([[
            (0, h), (w, h), (int(w * 0.65), int(h * 0.55)), (int(w * 0.35), int(h * 0.55))
        ]], dtype=np.int32)
        cv2.fillPoly(roi_mask, roi_pts, 255)
        roi_edges = cv2.bitwise_and(edges, roi_mask)

        lines = cv2.HoughLinesP(
            roi_edges, rho=1, theta=np.pi / 180,
            threshold=40, minLineLength=60, maxLineGap=80
        )

        left_xs, right_xs = [], []
        if lines is not None:
            for line in lines:
                x1, y1, x2, y2 = line[0]
                if x2 == x1:
                    continue
                slope = (y2 - y1) / (x2 - x1)
                if abs(slope) < 0.3:
                    continue
                mid_x = (x1 + x2) / 2
                if slope < 0 and mid_x < w * 0.5:   # left lane
                    left_xs.append(int(mid_x))
                elif slope > 0 and mid_x > w * 0.5: # right lane
                    right_xs.append(int(mid_x))

        # Store roi pts for drawing
        results['_roi_pts']    = roi_pts
        results['_left_xs']    = left_xs
        results['_right_xs']   = right_xs

        if left_xs and right_xs:
            lane_cx = (np.mean(left_xs) + np.mean(right_xs)) / 2
        elif left_xs:
            lane_cx = np.mean(left_xs) + w * 0.20
        elif right_xs:
            lane_cx = np.mean(right_xs) - w * 0.20
        else:
            lane_cx = w / 2

        self._lane_centre_history.append(lane_cx)
        smooth_cx = np.mean(self._lane_centre_history)

        deviation = (smooth_cx - w / 2) / (w / 2)
        results['lane_centre_x']  = int(smooth_cx)
        results['frame_centre_x'] = w // 2
        results['deviation_pct']  = round(abs(deviation) * 100, 1)

        if abs(deviation) > self.cfg.LANE_DEVIATION_THRESHOLD:
            results['lane_deviation'] = True
            results['deviation_side'] = 'RIGHT' if deviation > 0 else 'LEFT'

    def _draw_lane_overlay(self, frame, results, h, w):
        """Draw lane lines and centre marker."""
        overlay = frame.copy()
        roi_pts = results.get('_roi_pts')
        if roi_pts is not None:
            cv2.polylines(overlay, roi_pts, True, (100, 100, 100), 1)

        cx = results['lane_centre_x']
        mid = w // 2
        color = (0, 60, 220) if results['lane_deviation'] else (0, 200, 80)
        cv2.arrowedLine(frame, (mid, h - 10), (cx, h - 30), color, 3, tipLength=0.4)
        cv2.line(frame, (mid, h - 40), (mid, h - 5), (200, 200, 200), 1)

    # ─────────────────────────────────────────────
    #  Object Detection
    # ─────────────────────────────────────────────

    def _detect_objects(self, frame, results):
        detections = self._yolo.predict(
            frame, conf=self.cfg.YOLO_CONFIDENCE, verbose=False
        )
        for det in detections:
            for box in det.boxes:
                cls_id = int(box.cls[0])
                conf   = float(box.conf[0])
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                label  = COCO_NAMES.get(cls_id, f'Class{cls_id}')
                entry  = {'label': label, 'conf': conf, 'box': (x1, y1, x2, y2), 'class': cls_id}

                if cls_id in VEHICLE_CLASSES:
                    results['vehicles_detected'].append(entry)
                elif cls_id in PEDESTRIAN_CLASSES:
                    results['pedestrians_detected'].append(entry)
                else:
                    results['obstacles_detected'].append(entry)

    def _demo_objects(self, results, h, w):
        """Simulated detections for demo / missing YOLO."""
        t = time.time() % 30
        if 5 < t < 10:
            results['vehicles_detected'].append({
                'label': 'Car', 'conf': 0.85,
                'box': (w//2 - 60, h//3, w//2 + 60, h//2),
                'class': 2,
            })
        if 18 < t < 22:
            results['pedestrians_detected'].append({
                'label': 'Person', 'conf': 0.78,
                'box': (w//4, h//3, w//4 + 50, h//2 + 20),
                'class': 0,
            })

    # ─────────────────────────────────────────────
    #  Speed Estimation (Optical Flow)
    # ─────────────────────────────────────────────

    def _estimate_speed(self, frame, results):
        """
        Estimate speed by computing dense optical flow magnitude.
        Pixel displacement is converted to km/h using a calibration factor.
        This is an approximation; for real deployment calibrate with known speeds.
        """
        CALIBRATION = 0.35    # pixels/frame → km/h scale factor

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (160, 120))   # small for speed

        if self._prev_gray is not None and self._prev_gray.shape == gray.shape:
            flow  = cv2.calcOpticalFlowFarneback(
                self._prev_gray, gray,
                None, 0.5, 3, 15, 3, 5, 1.2, 0
            )
            mag, _ = cv2.cartToPolar(flow[..., 0], flow[..., 1])
            mean_mag = float(np.mean(mag))
            speed    = mean_mag * CALIBRATION * 30    # × fps estimate
            self._speed_history.append(speed)

        self._prev_gray = gray

        if self._speed_history:
            smooth_speed = float(np.median(self._speed_history))
        else:
            smooth_speed = 0.0

        results['speed_kmh']  = round(smooth_speed, 1)
        results['over_speed'] = smooth_speed > self.cfg.SPEED_ALERT_KMH
