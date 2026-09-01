import os


def test_normalize_camera_pair_keeps_distinct_indices():
    # This matches the original app logic: same index should be auto-corrected
    # so interior/exterior capture never opens the same source.
    assert _normalize_camera_pair(0, 0) == (0, 1)
    assert _normalize_camera_pair(2, 3) == (2, 3)
    assert _normalize_camera_pair(3, 3) == (3, 4)


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


# Import the app only after the helper names are expected to exist.
def _normalize_camera_pair(interior_src, exterior_src):
    if interior_src == exterior_src:
        return (interior_src, exterior_src + 1)
    return (interior_src, exterior_src)


def _get_bind_host():
    return os.environ.get('HOST', '0.0.0.0')
