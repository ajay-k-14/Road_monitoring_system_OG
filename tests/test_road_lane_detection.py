from collections import deque

import cv2
import numpy as np

from config import Config
from modules.road_monitor import RoadMonitor


def _monitor():
    monitor = RoadMonitor.__new__(RoadMonitor)
    monitor.cfg = Config()
    monitor._lane_centre_history = deque(maxlen=15)
    return monitor


def _detect(frame):
    monitor = _monitor()
    height, width = frame.shape[:2]
    results = {
        'lane_deviation': False,
        'deviation_side': None,
        'deviation_pct': 0.0,
        'lane_centre_x': width // 2,
        'frame_centre_x': width // 2,
    }
    monitor._detect_lanes(frame, results, height, width)
    return results


def test_lane_detection_finds_centered_lane_boundaries():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.line(frame, (170, 470), (270, 280), (255, 255, 255), 8)
    cv2.line(frame, (470, 470), (370, 280), (255, 255, 255), 8)

    results = _detect(frame)

    assert results['lane_detected']
    assert results['_left_xs'] and results['_right_xs']
    assert abs(results['lane_centre_x'] - 320) <= 10
    assert not results['lane_deviation']


def test_single_lane_boundary_can_indicate_deviation():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.line(frame, (100, 470), (200, 280), (255, 255, 255), 8)

    results = _detect(frame)

    assert results['lane_detected']
    assert results['_left_xs']
    assert not results['_right_xs']
    assert results['lane_deviation']
    assert results['deviation_side'] == 'LEFT'


def test_no_lane_lines_does_not_report_lane_deviation():
    results = _detect(np.zeros((480, 640, 3), dtype=np.uint8))

    assert not results['lane_detected']
    assert not results['lane_deviation']
    assert results['deviation_pct'] == 0.0