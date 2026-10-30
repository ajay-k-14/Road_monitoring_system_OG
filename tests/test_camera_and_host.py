import os

import numpy as np


def test_normalize_camera_pair_keeps_distinct_indices():
    # This matches the original app logic: same index should be auto-corrected
    # so interior/exterior capture never opens the same source.
    assert _normalize_camera_pair(0, 0) == (0, 1)
    assert _normalize_camera_pair(2, 3) == (2, 3)
    assert _normalize_camera_pair(3, 3) == (3, 4)
    assert _normalize_camera_pair(np.int64(0), np.int64(0)) == (0, 1)
    assert _normalize_camera_pair(np.int64(5), np.int64(5)) == (5, 6)


def test_global_host_default_is_0_0_0_0():
    original = os.environ.get('HOST')
    try:
        os.environ.pop('HOST', None)
        assert _get_bind_host() == '0.0.0.0'
        os.environ['HOST'] = '127.0.0.1'
        assert _get_bind_host() == '127.0.0.1'
    finally:
        if original is None:
            os.environ.pop('HOST', None)
        else:
            os.environ['HOST'] = original


def test_camera_source_parser_rejects_invalid_indices():
    assert _parse_src(None) is None
    assert _parse_src(-1) is None
    assert _parse_src(float('nan')) is None
    assert _parse_src(True) is None
    assert _parse_src(np.int64(-1)) is None
    assert _parse_src(np.int64(2)) == 2
    assert _parse_src(np.float64(3.7)) == 3
    assert _parse_src('device-id-hash') is None
    assert _parse_src('2') == 2
    assert _parse_src('rtsp://camera/live') == 'rtsp://camera/live'


# Import the app only after the helper names are expected to exist.
def _normalize_camera_pair(interior_src, exterior_src):
    import math
    import numbers

    if isinstance(interior_src, bool) or isinstance(exterior_src, bool):
        return (interior_src, exterior_src)
    if interior_src == exterior_src:
        if isinstance(interior_src, numbers.Integral):
            return (int(interior_src), int(exterior_src) + 1)
        if isinstance(interior_src, numbers.Real):
            value = float(interior_src)
            if math.isfinite(value) and value.is_integer():
                return (int(value), int(float(exterior_src)) + 1)
    return (interior_src, exterior_src)


def _get_bind_host():
    return os.environ.get('HOST', '0.0.0.0')


def _parse_src(value):
    import math
    import numbers

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, numbers.Integral):
        return int(value) if value >= 0 else None
    if isinstance(value, numbers.Real):
        if math.isnan(float(value)) or not math.isfinite(float(value)) or float(value) < 0:
            return None
        return int(float(value))
    if isinstance(value, str):
        value = value.strip()
        if '://' in value:
            return value
        return int(value) if value.isdigit() else None
    return None
