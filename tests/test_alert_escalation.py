from types import SimpleNamespace

import modules.alert_system as alert_module
from modules.alert_system import AlertSystem


class _FakeSocket:
    def __init__(self):
        self.events = []

    def emit(self, event, payload):
        self.events.append((event, payload))


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
    monkeypatch.setattr(alert_module, 'mark_alert_responded', lambda *_: None)
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
    assert system.evaluate(driver_alert, no_road_frame) == []
    assert len(_FakeTimer.instances) == 1
    assert system.mark_responded(next(iter(system._alerts)), source='button') is True
    phone_alert = {
        **driver_alert,
        'drowsy': False,
        'phone_usage': True,
    }
    assert len(system.evaluate(phone_alert, no_road_frame)) == 1
    assert len(_FakeTimer.instances) == 2
    assert len([event for event in system.socketio.events if event[0] == 'alert']) == 2


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

    system._active_alert_id = first.id
    system._escalate(first)
    now[0] += 5
    system._active_alert_id = second.id
    system._escalate(second)

    assert len(_FakeTimer.instances) == 1
    assert _FakeTimer.instances[0].interval == 15
    assert _FakeTimer.instances[0].args == (second,)
    assert len(sent_messages) == 1

    now[0] += 15
    _FakeTimer.instances[0].callback(*_FakeTimer.instances[0].args)
    assert len(sent_messages) == 2


def test_acknowledgement_cancels_timer_once_and_blocks_duplicate_sms(monkeypatch):
    _FakeTimer.instances = []
    monkeypatch.setattr(alert_module.threading, 'Timer', _FakeTimer)
    monkeypatch.setattr(alert_module, 'log_alert', lambda *_: SimpleNamespace(id=7))
    responded_alerts = []
    monkeypatch.setattr(
        alert_module, 'mark_alert_responded',
        lambda alert_id: responded_alerts.append(alert_id),
    )
    sent_messages = []
    monkeypatch.setattr(
        alert_module, 'send_sos',
        lambda *args: sent_messages.append(args) or True,
    )
    monkeypatch.setattr(
        alert_module,
        'get_db',
        lambda: SimpleNamespace(commit=lambda: None, close=lambda: None),
    )

    system = AlertSystem(socketio=_FakeSocket())
    system.set_user_context(11)
    alert = system._fire('DROWSINESS', 'HIGH', 'drowsy', 'INTERIOR')

    assert system.mark_responded(alert.id, user_id=12) is False
    assert system.mark_responded(alert.id, user_id=11, source='voice') is True
    assert system.mark_responded(alert.id, user_id=11) is False
    assert _FakeTimer.instances[0].cancelled is True
    assert responded_alerts == [7]
    assert alert.response_source == 'voice'
    assert alert.responded_at is not None

    system._escalate(alert)
    system._escalate(alert)
    assert sent_messages == []


def test_repeated_escalation_attempt_sends_sms_once(monkeypatch):
    sent_messages = []
    monkeypatch.setattr(
        alert_module, 'send_sos',
        lambda *args: sent_messages.append(args) or True,
    )
    monkeypatch.setattr(
        alert_module,
        'get_db',
        lambda: SimpleNamespace(commit=lambda: None, close=lambda: None),
    )
    system = AlertSystem()
    alert = alert_module.Alert('single-sms', 'DROWSINESS', 'HIGH', 'drowsy', 'INTERIOR')
    system._active_alert_id = alert.id

    system._escalate(alert)
    system._escalate(alert)

    assert len(sent_messages) == 1


def test_alerts_are_queued_and_activated_one_at_a_time(monkeypatch):
    _FakeTimer.instances = []
    monkeypatch.setattr(alert_module.threading, 'Timer', _FakeTimer)
    monkeypatch.setattr(alert_module, 'log_alert',
                        lambda *_: SimpleNamespace(id=1))
    monkeypatch.setattr(alert_module, 'mark_alert_responded', lambda *_: None)
    socket = _FakeSocket()
    system = AlertSystem(socketio=socket)

    first = system._fire('DROWSINESS', 'HIGH', 'drowsy', 'INTERIOR')
    second = system._fire('LANE_DEVIATION', 'HIGH', 'lane', 'EXTERIOR')

    emitted_alerts = [payload for event, payload in socket.events if event == 'alert']
    assert [alert['alert_id'] for alert in emitted_alerts] == [first.id]
    assert len(_FakeTimer.instances) == 1
    assert system.mark_responded(second.id, source='voice') is False
    assert system.mark_responded(first.id, source='voice') is True

    emitted_alerts = [payload for event, payload in socket.events if event == 'alert']
    assert [alert['alert_id'] for alert in emitted_alerts] == [first.id, second.id]
    assert len(_FakeTimer.instances) == 2


def test_repeated_detection_does_not_queue_duplicate_unacknowledged_alert(monkeypatch):
    _FakeTimer.instances = []
    monkeypatch.setattr(alert_module.threading, 'Timer', _FakeTimer)
    monkeypatch.setattr(alert_module, 'log_alert',
                        lambda *_: SimpleNamespace(id=1))
    monkeypatch.setattr(alert_module, 'mark_alert_responded', lambda *_: None)
    socket = _FakeSocket()
    system = AlertSystem(socketio=socket)
    system.COOLDOWN = {'CRITICAL': 0, 'HIGH': 0, 'MEDIUM': 0, 'LOW': 0}
    road = {'camera_signal': False}
    drowsy = {
        'monitoring_valid': True,
        'sleeping': False,
        'drowsy': True,
        'yawning': False,
        'phone_usage': False,
        'distracted': False,
    }
    clear = {**drowsy, 'drowsy': False}

    assert len(system.evaluate(drowsy, road)) == 1
    first_id = system._active_alert_id
    assert system.evaluate(clear, road) == []
    assert system.evaluate(drowsy, road) == []
    assert len(system._queued_alerts) == 0
    assert len([event for event in socket.events if event[0] == 'alert']) == 1

    assert system.mark_responded(first_id, source='button') is True
    assert system.evaluate(drowsy, road) == []
    assert len([event for event in socket.events if event[0] == 'alert']) == 1