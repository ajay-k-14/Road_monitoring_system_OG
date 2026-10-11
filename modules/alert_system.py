"""
Alert System Module
────────────────────────────────────────────────────────────────────
Responsibilities:
  • Convert driver/road results into structured Alert objects
  • Deduplicate / throttle repeated alerts
  • Emit socket events for audio/visual dashboard alerts
  • Escalate un-responded CRITICAL alerts to emergency SMS (Twilio)
"""

import time
import threading
import uuid
from dataclasses import dataclass, field
from typing import List, Optional
from config import Config
from database.db import EmergencyContact, EmergencyMessage, get_db, log_alert, mark_alert_responded
from sms_with_loc import send_sos

# ─────────────────────────────────────────────
#  Alert definition
# ─────────────────────────────────────────────

@dataclass
class Alert:
    id:       str
    type:     str
    severity: str    # LOW | MEDIUM | HIGH | CRITICAL
    message:  str
    source:   str     # INTERIOR or EXTERIOR
    ts:       float  = field(default_factory=time.time)
    responded: bool  = False
    response_source: Optional[str] = None
    responded_at: Optional[float] = None
    database_id: Optional[int] = None
    user_id: Optional[int] = None


# Alert rules: (key_in_results, result_value, alert_type, severity, message)
DRIVER_RULES = [
    ('sleeping',    True, 'SLEEPING',    'CRITICAL', '⚠ Driver is sleeping! Immediate action required.'),
    ('drowsy',      True, 'DROWSINESS',  'HIGH',     '⚠ Drowsiness detected. Take a break.'),
    ('yawning',     True, 'YAWNING',     'MEDIUM',   '⚠ Yawning detected. Driver may be fatigued.'),
    ('phone_usage', True, 'PHONE_USAGE', 'HIGH',     '⚠ Mobile phone usage detected while driving!'),
    ('distracted',  True, 'DISTRACTION', 'HIGH',     '⚠ Driver distraction detected.'),
]

ROAD_RULES = [
    ('lane_deviation', True,  'LANE_DEVIATION',  'HIGH',   '⚠ Lane deviation detected!'),
    ('over_speed',     True,  'OVER_SPEED',      'HIGH',   '⚠ Vehicle exceeding safe speed limit!'),
]


class AlertSystem:
    """Manages alert evaluation, deduplication, and escalation."""

    # Minimum seconds between identical alerts
    COOLDOWN = {
        'CRITICAL': 5,
        'HIGH':     8,
        'MEDIUM':  15,
        'LOW':     30,
    }

    def __init__(self, socketio=None):
        self.cfg      = Config()
        self.socketio = socketio

        # last_fired[alert_type] = timestamp
        self._last_fired: dict = {}
        # active_escalations[alert_id] = Timer
        self._escalations: dict = {}
        self._alerts: dict = {}
        self._queued_alerts: list = []
        self._active_alert_id: Optional[str] = None
        self._latched_conditions: set = set()
        self._latched_escalation_sources: set = set()
        self._sms_lock = threading.Lock()
        self._alert_lock = threading.RLock()
        self._sms_attempted: set = set()
        self._last_sms_attempt_at: Optional[float] = None
        self._user_id: Optional[int] = None
        self._location: Optional[dict] = None

    def set_user_context(self, user_id: Optional[int]):
        self._user_id = int(user_id) if user_id is not None else None

    def update_location(self, latitude, longitude, accuracy=None):
        self._location = {
            'latitude': float(latitude),
            'longitude': float(longitude),
            'accuracy': float(accuracy) if accuracy is not None else None,
        }

    # ─────────────────────────────────────────────
    #  Public API
    # ─────────────────────────────────────────────

    def evaluate(self, driver_results: dict, road_results: dict) -> List[dict]:
        """
        Evaluate current monitoring results against alert rules.
        Returns list of new alerts emitted this frame.
        """
        now    = time.time()
        alerts = []
        active_conditions = set()
        active_escalation_sources = set()
        driver_observed = 'monitoring_valid' in driver_results
        road_observed = bool(road_results.get('camera_signal', False))

        if driver_observed:
            for key, val, atype, severity, _ in DRIVER_RULES:
                if driver_results.get(key) == val:
                    active_conditions.add(atype)
                    if severity in ('HIGH', 'CRITICAL'):
                        active_escalation_sources.add('INTERIOR')

        road_active = (
            road_observed and
            road_results.get('road_detected', False)
        )
        if road_active:
            for key, val, atype, severity, _ in ROAD_RULES:
                if road_results.get(key) == val:
                    active_conditions.add(atype)
                    if severity in ('HIGH', 'CRITICAL'):
                        active_escalation_sources.add('EXTERIOR')
            if road_results.get('pedestrians_detected'):
                active_conditions.add('PEDESTRIAN_DETECTED')
                active_escalation_sources.add('EXTERIOR')

        driver_conditions = {rule[2] for rule in DRIVER_RULES}
        road_conditions = {rule[2] for rule in ROAD_RULES} | {'PEDESTRIAN_DETECTED'}
        for atype in tuple(self._latched_conditions):
            if (driver_observed and atype in driver_conditions and
                atype not in active_conditions):
                self._latched_conditions.discard(atype)
            elif (road_observed and atype in road_conditions and
                  atype not in active_conditions):
                self._latched_conditions.discard(atype)
        if driver_observed and 'INTERIOR' not in active_escalation_sources:
            self._latched_escalation_sources.discard('INTERIOR')
        if road_observed and 'EXTERIOR' not in active_escalation_sources:
            self._latched_escalation_sources.discard('EXTERIOR')

        for (key, val, atype, severity, msg) in DRIVER_RULES:
            if driver_observed and driver_results.get(key) == val:
                if self._has_pending_type(atype):
                    self._latched_conditions.add(atype)
                    continue
                if (atype not in self._latched_conditions and
                    not (severity in ('HIGH', 'CRITICAL') and
                         'INTERIOR' in self._latched_escalation_sources)):
                    if self._should_fire(atype, severity, now):
                        alert = self._fire(atype, severity, msg, 'INTERIOR')
                        alerts.append(self._to_dict(alert))
                        self._latched_conditions.add(atype)
                        if severity in ('HIGH', 'CRITICAL'):
                            self._latched_escalation_sources.add('INTERIOR')

        if road_active:
            for (key, val, atype, severity, msg) in ROAD_RULES:
                if road_results.get(key) == val:
                    if self._has_pending_type(atype):
                        self._latched_conditions.add(atype)
                        continue
                    if (atype not in self._latched_conditions and
                        not (severity in ('HIGH', 'CRITICAL') and
                             'EXTERIOR' in self._latched_escalation_sources)):
                        if self._should_fire(atype, severity, now):
                            alert = self._fire(atype, severity, msg, 'EXTERIOR')
                            alerts.append(self._to_dict(alert))
                            self._latched_conditions.add(atype)
                            if severity in ('HIGH', 'CRITICAL'):
                                self._latched_escalation_sources.add('EXTERIOR')

            # Pedestrian proximity alert
            if road_results.get('pedestrians_detected'):
                n = len(road_results['pedestrians_detected'])
                atype = 'PEDESTRIAN_DETECTED'
                if self._has_pending_type(atype):
                    self._latched_conditions.add(atype)
                elif (atype not in self._latched_conditions and
                      'EXTERIOR' not in self._latched_escalation_sources and
                      self._should_fire(atype, 'HIGH', now)):
                    msg = f'⚠ {n} pedestrian(s) detected nearby!'
                    alert = self._fire(atype, 'HIGH', msg, 'EXTERIOR')
                    alerts.append(self._to_dict(alert))
                    self._latched_conditions.add(atype)
                    self._latched_escalation_sources.add('EXTERIOR')

        return alerts

    def _has_pending_type(self, atype: str) -> bool:
        with self._alert_lock:
            return any(
                alert.type == atype and not alert.responded
                for alert in self._alerts.values()
            )

    def mark_responded(
        self,
        alert_id: Optional[str],
        user_id: Optional[int] = None,
        source: str = 'button',
    ) -> bool:
        """Driver acknowledged the alert — cancel SMS escalation."""
        if not alert_id or source not in ('voice', 'button'):
            return False
        next_alert = None
        with self._alert_lock:
            alert = self._alerts.get(alert_id)
            if (
                alert is None
                or alert.responded
                or alert_id != self._active_alert_id
                or (user_id is not None and alert.user_id != int(user_id))
            ):
                return False
            alert.responded = True
            alert.response_source = source
            alert.responded_at = time.time()
            timer = self._escalations.pop(alert_id, None)
            if timer:
                timer.cancel()
            if alert.database_id:
                mark_alert_responded(alert.database_id)
            self._active_alert_id = None
            next_alert = self._activate_next_alert_locked()

        if next_alert and self.socketio:
            self.socketio.emit('alert', self._to_dict(next_alert))
        return True

    # ─────────────────────────────────────────────
    #  Internal
    # ─────────────────────────────────────────────

    def _should_fire(self, atype: str, severity: str, now: float) -> bool:
        cooldown = self.COOLDOWN.get(severity, 10)
        last     = self._last_fired.get(atype, 0)
        return (now - last) >= cooldown

    def _fire(self, atype: str, severity: str, msg: str, source: str) -> Alert:
        alert_id = f"{atype}_{uuid.uuid4().hex}"
        alert    = Alert(id=alert_id, type=atype, severity=severity,
                 message=msg, source=source, user_id=self._user_id)
        self._last_fired[atype] = time.time()

        stored_alert = log_alert(atype, severity, msg)
        alert.database_id = stored_alert.id

        with self._alert_lock:
            self._alerts[alert_id] = alert
            is_active = self._active_alert_id is None
            if is_active:
                self._activate_alert_locked(alert)
            else:
                self._queued_alerts.append(alert)

        if is_active and self.socketio:
            self.socketio.emit('alert', self._to_dict(alert))

        return alert

    def _activate_alert_locked(self, alert: Alert):
        self._active_alert_id = alert.id
        if alert.severity in ('HIGH', 'CRITICAL'):
            timer = threading.Timer(
                self.cfg.ALERT_ESCALATION_SECONDS,
                self._escalate,
                args=(alert,)
            )
            timer.daemon = True
            self._escalations[alert.id] = timer
            timer.start()

    def _activate_next_alert_locked(self) -> Optional[Alert]:
        while self._queued_alerts:
            alert = self._queued_alerts.pop(0)
            if alert.responded:
                continue
            self._activate_alert_locked(alert)
            return alert
        return None

    def _escalate(self, alert: Alert):
        """Notify the dashboard and SMS contacts after no acknowledgement."""
        with self._alert_lock:
            if (
                alert.responded
                or alert.id != self._active_alert_id
                or alert.id in self._sms_attempted
            ):
                return
            with self._sms_lock:
                now = time.monotonic()
                interval = self.cfg.ALERT_ESCALATION_SECONDS
                remaining = (interval - (now - self._last_sms_attempt_at)
                             if self._last_sms_attempt_at is not None else 0)
                if remaining > 0:
                    timer = threading.Timer(
                        remaining, self._escalate, args=(alert,)
                    )
                    timer.daemon = True
                    self._escalations[alert.id] = timer
                    timer.start()
                    return
                self._last_sms_attempt_at = now

            self._sms_attempted.add(alert.id)

        print(f"[AlertSystem] Escalating alert: {alert.type} — {alert.message}")

        db = get_db()
        try:
            contacts = (db.query(EmergencyContact)
                        .filter_by(user_id=alert.user_id)
                        .all()) if alert.user_id else []
            location = self._location or {}
            if location.get('latitude') is not None:
                location_text = (
                    f"Location: https://maps.google.com/?q="
                    f"{location['latitude']},{location['longitude']}"
                )
            else:
                location_text = "Location: unavailable"
            sms_message = (
                f"EMERGENCY ALERT: {alert.message}\n"
                f"Source: {alert.source}\n{location_text}"
            )
            gateway_accepted = send_sos(
                sms_message, [contact.phone for contact in contacts]
            )
            sms_status = (
                'accepted' if gateway_accepted else
                'no_contacts' if not contacts else
                'failed'
            )
            for contact in contacts:
                db.add(EmergencyMessage(
                    alert_id=alert.database_id,
                    contact_id=contact.id,
                    message=sms_message,
                    status='accepted' if gateway_accepted else 'failed',
                ))
            db.commit()
        finally:
            db.close()

        if self.socketio:
            self.socketio.emit('emergency_escalation', {
                'alert_id': alert.id,
                'message':  alert.message,
                'type':     alert.type,
                'source':   alert.source,
                'sms_sent': bool(contacts and gateway_accepted),
                'sms_status': sms_status,
                'location': self._location,
            })

    @staticmethod
    def _to_dict(alert: Alert) -> dict:
        return {
            'alert_id': alert.id,
            'type':     alert.type,
            'severity': alert.severity,
            'message':  alert.message,
            'source':   alert.source,
            'ts':       alert.ts,
            'escalation_seconds': (
                Config.ALERT_ESCALATION_SECONDS
                if alert.severity in ('HIGH', 'CRITICAL') else None
            ),
        }
