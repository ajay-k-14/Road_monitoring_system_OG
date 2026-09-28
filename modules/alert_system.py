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
        self._latched_conditions: set = set()
        self._latched_escalation_sources: set = set()
        self._sms_lock = threading.Lock()
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
            if (driver_observed and driver_results.get(key) == val and
                atype not in self._latched_conditions and
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
                if (road_results.get(key) == val and
                    atype not in self._latched_conditions and
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
                if (atype not in self._latched_conditions and
                    'EXTERIOR' not in self._latched_escalation_sources and
                    self._should_fire(atype, 'HIGH', now)):
                    msg = f'⚠ {n} pedestrian(s) detected nearby!'
                    alert = self._fire(atype, 'HIGH', msg, 'EXTERIOR')
                    alerts.append(self._to_dict(alert))
                    self._latched_conditions.add(atype)
                    self._latched_escalation_sources.add('EXTERIOR')

        return alerts

    def mark_responded(self, alert_id: Optional[str]):
        """Driver acknowledged the alert — cancel SMS escalation."""
        if alert_id and alert_id in self._escalations:
            self._escalations[alert_id].cancel()
            del self._escalations[alert_id]
        if alert_id:
            alert = self._alerts.get(alert_id)
            if alert:
                alert.responded = True
                if alert.database_id:
                    mark_alert_responded(alert.database_id)

    # ─────────────────────────────────────────────
    #  Internal
    # ─────────────────────────────────────────────

    def _should_fire(self, atype: str, severity: str, now: float) -> bool:
        cooldown = self.COOLDOWN.get(severity, 10)
        last     = self._last_fired.get(atype, 0)
        return (now - last) >= cooldown

    def _fire(self, atype: str, severity: str, msg: str, source: str) -> Alert:
        alert_id = f"{atype}_{int(time.time() * 1000)}"
        alert    = Alert(id=alert_id, type=atype, severity=severity,
                 message=msg, source=source, user_id=self._user_id)
        self._last_fired[atype] = time.time()
        self._alerts[alert_id] = alert

        stored_alert = log_alert(atype, severity, msg)
        alert.database_id = stored_alert.id

        # Emit to dashboard
        if self.socketio:
            self.socketio.emit('alert', self._to_dict(alert))

        # Schedule escalation timer for HIGH / CRITICAL alerts without external SMS integration.
        if severity in ('HIGH', 'CRITICAL'):
            t = threading.Timer(
                self.cfg.ALERT_ESCALATION_SECONDS,
                self._escalate,
                args=(alert,)
            )
            t.daemon = True
            t.start()
            self._escalations[alert_id] = t

        return alert

    def _escalate(self, alert: Alert):
        """Notify the dashboard and SMS contacts after no acknowledgement."""
        if alert.responded:
            return

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
