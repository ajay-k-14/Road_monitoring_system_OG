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

# ─────────────────────────────────────────────
#  Alert definition
# ─────────────────────────────────────────────

@dataclass
class Alert:
    id:       str
    type:     str
    severity: str    # LOW | MEDIUM | HIGH | CRITICAL
    message:  str
    ts:       float  = field(default_factory=time.time)
    responded: bool  = False


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

        for (key, val, atype, severity, msg) in DRIVER_RULES:
            if driver_results.get(key) == val:
                if self._should_fire(atype, severity, now):
                    alert = self._fire(atype, severity, msg)
                    alerts.append(self._to_dict(alert))

        for (key, val, atype, severity, msg) in ROAD_RULES:
            if road_results.get(key) == val:
                if self._should_fire(atype, severity, now):
                    alert = self._fire(atype, severity, msg)
                    alerts.append(self._to_dict(alert))

        # Pedestrian proximity alert
        if road_results.get('pedestrians_detected'):
            n = len(road_results['pedestrians_detected'])
            atype = 'PEDESTRIAN_DETECTED'
            if self._should_fire(atype, 'HIGH', now):
                msg = f'⚠ {n} pedestrian(s) detected nearby!'
                alert = self._fire(atype, 'HIGH', msg)
                alerts.append(self._to_dict(alert))

        return alerts

    def mark_responded(self, alert_id: Optional[str]):
        """Driver acknowledged the alert — cancel SMS escalation."""
        if alert_id and alert_id in self._escalations:
            self._escalations[alert_id].cancel()
            del self._escalations[alert_id]

    # ─────────────────────────────────────────────
    #  Internal
    # ─────────────────────────────────────────────

    def _should_fire(self, atype: str, severity: str, now: float) -> bool:
        cooldown = self.COOLDOWN.get(severity, 10)
        last     = self._last_fired.get(atype, 0)
        return (now - last) >= cooldown

    def _fire(self, atype: str, severity: str, msg: str) -> Alert:
        alert_id = f"{atype}_{int(time.time() * 1000)}"
        alert    = Alert(id=alert_id, type=atype, severity=severity, message=msg)
        self._last_fired[atype] = time.time()

        # Emit to dashboard
        if self.socketio:
            self.socketio.emit('alert', self._to_dict(alert))

        # Schedule SMS escalation for HIGH / CRITICAL
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
        """Send emergency SMS via Twilio if driver did not respond."""
        if alert.responded:
            return

        print(f"[AlertSystem] Escalating alert: {alert.type} — {alert.message}")

        # Emit dashboard notification
        if self.socketio:
            self.socketio.emit('emergency_escalation', {
                'alert_id': alert.id,
                'message':  alert.message,
                'type':     alert.type,
            })

        # Twilio SMS (only if credentials configured)
        if self.cfg.TWILIO_SID and self.cfg.TWILIO_TOKEN:
            self._send_sms(alert)
        else:
            print("[AlertSystem] Twilio not configured — SMS skipped")

    def _send_sms(self, alert: Alert):
        try:
            from twilio.rest import Client
            from database.db import get_db, EmergencyContact

            db       = get_db()
            contacts = db.query(EmergencyContact).limit(5).all()
            client   = Client(self.cfg.TWILIO_SID, self.cfg.TWILIO_TOKEN)

            for contact in contacts:
                body = (
                    f"🚨 DRIVER ALERT\n"
                    f"Type: {alert.type}\n"
                    f"{alert.message}\n"
                    f"Driver may need assistance. Please check."
                )
                client.messages.create(
                    body=body,
                    from_=self.cfg.TWILIO_FROM,
                    to=contact.phone,
                )
                print(f"[AlertSystem] SMS sent to {contact.name} ({contact.phone})")
            db.close()
        except Exception as e:
            print(f"[AlertSystem] SMS error: {e}")

    @staticmethod
    def _to_dict(alert: Alert) -> dict:
        return {
            'alert_id': alert.id,
            'type':     alert.type,
            'severity': alert.severity,
            'message':  alert.message,
            'ts':       alert.ts,
        }
