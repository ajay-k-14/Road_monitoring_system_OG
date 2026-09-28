from types import SimpleNamespace

import modules.alert_system as alert_module
from modules.alert_system import AlertSystem


class _FakeSocket:
    def emit(self, event, payload):
        pass


class _FakeTimer:
    instances = []

    def __init__(self, interval, callback, args=()):
        self.interval = interval
        self.callback = callback
        self.args = args
        self.cancelled = False
        self.instances.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


def test_sustained_driver_incident_schedules_one_escalation(monkeypatch):
    _FakeTimer.instances = []
    monkeypatch.setattr(alert_module.threading, 'Timer', _FakeTimer)
    monkeypatch.setattr(alert_module, 'log_alert', lambda *_: SimpleNamespace(id=1))
    system = AlertSystem(socketio=_FakeSocket())
    system.COOLDOWN = {'CRITICAL': 0, 'HIGH': 0, 'MEDIUM': 0, 'LOW': 0}

    driver_alert = {
        'monitoring_valid': True,
        'sleeping': False,
        'drowsy': True,
        'yawning': False,
        'phone_usage': False,
        'distracted': False,
    }
    no_road_frame = {'camera_signal': False}

    assert len(system.evaluate(driver_alert, no_road_frame)) == 1
    assert system.evaluate(driver_alert, no_road_frame) == []

    # Browser mode processes the exterior frame separately with driver placeholders.
    assert system.evaluate(
        {'sleeping': False, 'drowsy': False}, no_road_frame
    ) == []
    assert system.evaluate(driver_alert, no_road_frame) == []

    # A second high-severity condition during the same incident must not add an SMS timer.
    sleeping_alert = {**driver_alert, 'sleeping': True}
    assert system.evaluate(sleeping_alert, no_road_frame) == []
    assert len(_FakeTimer.instances) == 1
    assert _FakeTimer.instances[0].interval == 20

    cleared = {**driver_alert, 'drowsy': False}
    assert system.evaluate(cleared, no_road_frame) == []
    assert len(system.evaluate(driver_alert, no_road_frame)) == 1
    assert len(_FakeTimer.instances) == 2


def test_sms_escalations_are_spaced_by_configured_interval(monkeypatch):
    _FakeTimer.instances = []
    monkeypatch.setattr(alert_module.threading, 'Timer', _FakeTimer)
    sent_messages = []
    monkeypatch.setattr(
        alert_module, 'send_sos',
        lambda *args: sent_messages.append(args) or True,
    )
    monkeypatch.setattr(
        alert_module, 'get_db',
        lambda: SimpleNamespace(commit=lambda: None, close=lambda: None),
    )
    system = AlertSystem()
    now = [100.0]
    monkeypatch.setattr(alert_module.time, 'monotonic', lambda: now[0])
    first = alert_module.Alert('first', 'DROWSINESS', 'HIGH', 'first', 'INTERIOR')
    second = alert_module.Alert('second', 'OVER_SPEED', 'HIGH', 'second', 'EXTERIOR')

    system._escalate(first)
    now[0] += 5
    system._escalate(second)

    assert len(_FakeTimer.instances) == 1
    assert _FakeTimer.instances[0].interval == 15
    assert _FakeTimer.instances[0].args == (second,)
    assert len(sent_messages) == 1

    now[0] += 15
    _FakeTimer.instances[0].callback(*_FakeTimer.instances[0].args)
    assert len(sent_messages) == 2