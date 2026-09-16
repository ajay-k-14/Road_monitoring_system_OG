"""
Database Models and Operations
Uses SQLAlchemy ORM with SQLite backend
"""

from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float,
    Boolean, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from werkzeug.security import generate_password_hash, check_password_hash

DATABASE_URL = 'sqlite:///driver_monitor.db'
engine       = create_engine(DATABASE_URL, connect_args={'check_same_thread': False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base         = declarative_base()

# ─────────────────────────────────────────────
#  Models
# ─────────────────────────────────────────────

class User(Base):
    __tablename__ = 'users'

    id           = Column(Integer, primary_key=True, index=True)
    name         = Column(String(100), nullable=False)
    email        = Column(String(150), unique=True, nullable=False)
    phone        = Column(String(20))
    password_hash = Column(String(256), nullable=False)
    created_at   = Column(DateTime, default=datetime.utcnow)
    is_active    = Column(Boolean, default=True)

    vehicles          = relationship('Vehicle',          back_populates='owner', cascade='all, delete')
    emergency_contacts = relationship('EmergencyContact', back_populates='user',  cascade='all, delete')

    # Flask-Login compatibility
    @property
    def is_authenticated(self): return True
    @property
    def is_anonymous(self): return False
    def get_id(self): return str(self.id)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.name}>'


class Vehicle(Base):
    __tablename__ = 'vehicles'

    id           = Column(Integer, primary_key=True)
    owner_id     = Column(Integer, ForeignKey('users.id'), nullable=False)
    model        = Column(String(100))
    number_plate = Column(String(20))
    vehicle_type = Column(String(50))
    color        = Column(String(30))

    owner   = relationship('User',   back_populates='vehicles')
    cameras = relationship('Camera', back_populates='vehicle', cascade='all, delete')


class Camera(Base):
    __tablename__ = 'cameras'

    id         = Column(Integer, primary_key=True)
    vehicle_id = Column(Integer, ForeignKey('vehicles.id'), nullable=False)
    cam_type   = Column(String(20))   # 'interior' | 'exterior'
    position   = Column(String(50))
    status     = Column(String(20), default='active')

    vehicle = relationship('Vehicle', back_populates='cameras')


class EmergencyContact(Base):
    __tablename__ = 'emergency_contacts'

    id                = Column(Integer, primary_key=True)
    user_id           = Column(Integer, ForeignKey('users.id'), nullable=False)
    name              = Column(String(100), nullable=False)
    phone             = Column(String(20),  nullable=False)
    relationship_type = Column(String(50))

    user = relationship('User', back_populates='emergency_contacts')


class Alert(Base):
    __tablename__ = 'alerts'

    id         = Column(Integer, primary_key=True)
    alert_type = Column(String(50), nullable=False)   # e.g. 'DROWSINESS', 'LANE_DEVIATION'
    severity   = Column(String(20), nullable=False)   # 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'
    message    = Column(Text)
    timestamp  = Column(DateTime, default=datetime.utcnow)
    responded  = Column(Boolean, default=False)

    emergency_messages = relationship('EmergencyMessage', back_populates='alert', cascade='all, delete')


class EmergencyMessage(Base):
    __tablename__ = 'emergency_messages'

    id         = Column(Integer, primary_key=True)
    alert_id   = Column(Integer, ForeignKey('alerts.id'))
    contact_id = Column(Integer, ForeignKey('emergency_contacts.id'))
    message    = Column(Text)
    timestamp  = Column(DateTime, default=datetime.utcnow)
    status     = Column(String(20), default='pending')   # 'sent' | 'failed' | 'pending'

    alert = relationship('Alert', back_populates='emergency_messages')


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def get_db():
    return SessionLocal()


def init_db():
    Base.metadata.create_all(bind=engine)
    # Seed a demo user if the table is empty
    db = get_db()
    try:
        if db.query(User).count() == 0:
            demo = User(name='Demo Driver', email='demo@dms.com', phone='+91-9000000000')
            demo.set_password('demo1234')
            db.add(demo)
            db.commit()
    finally:
        db.close()


def create_user(name: str, email: str, phone: str, password: str) -> User:
    db = get_db()
    try:
        user = User(name=name, email=email, phone=phone)
        user.set_password(password)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def get_user_by_email(email: str):
    db = get_db()
    try:
        return db.query(User).filter_by(email=email).first()
    finally:
        db.close()


def log_alert(alert_type: str, severity: str, message: str) -> Alert:
    db = get_db()
    try:
        alert = Alert(alert_type=alert_type, severity=severity, message=message)
        db.add(alert)
        db.commit()
        db.refresh(alert)
        return alert
    finally:
        db.close()


def get_alert_history(limit: int = 50):
    db = get_db()
    try:
        return (db.query(Alert)
                  .order_by(Alert.timestamp.desc())
                  .limit(limit)
                  .all())
    finally:
        db.close()


def mark_alert_responded(alert_id: int):
    db = get_db()
    try:
        alert = db.query(Alert).get(alert_id)
        if alert:
            alert.responded = True
            db.commit()
    finally:
        db.close()
