from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    Boolean,
    BigInteger,
    Date,
    SmallInteger,
    Text,
    ForeignKey,
    Index,
)
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, JSONB
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy import MetaData, DateTime

Base = declarative_base()

# Separate bases to reflect two distinct DB schemas
BaseOrion = declarative_base(metadata=MetaData())
BaseArtemis = declarative_base(metadata=MetaData())

# -----------------------------
# Shared Base models (as before)
# -----------------------------

class DriveDay(Base):
    __tablename__ = 'drive_day'
    day_id = Column(SmallInteger, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    power_limit = Column(Integer)
    events = relationship("Event", back_populates="drive_day", overlaps="drive_day")

class LutDriver(Base):
    __tablename__ = 'lut_driver'
    driver_id = Column(SmallInteger, primary_key=True)
    driver_name = Column(Text, nullable=False)
    driver_weight = Column(SmallInteger)
    events = relationship("Event", back_populates="driver")

class LutLocation(Base):
    __tablename__ = 'lut_location'
    location_id = Column(SmallInteger, primary_key=True)
    area = Column(Text, nullable=False)
    track = Column(Text, nullable=False)
    events = relationship("Event", back_populates="location")

class LutCar(Base):
    __tablename__ = 'lut_car'
    car_id = Column(SmallInteger, primary_key=True)
    car_name = Column(Text, nullable=False)
    events = relationship("Event", back_populates="car")

class LutEventType(Base):
    __tablename__ = 'lut_event_type'
    type_id = Column(SmallInteger, primary_key=True)
    event_type = Column(Text, nullable=False)
    events = relationship("Event", back_populates="event_type_ref")

class Classifier(Base):
    __tablename__ = 'classifier'
    event_id = Column(BigInteger, ForeignKey('event.event_id'), primary_key=True)
    type = Column(Text, nullable=False, primary_key=True)
    start_time = Column(BigInteger, nullable=False, primary_key=True)
    end_time = Column(BigInteger)
    notes = Column(Text)
    event = relationship("Event", back_populates="classifiers", overlaps="classifiers,event")

class Event(Base):
    __tablename__ = 'event'
    event_id = Column(SmallInteger, primary_key=True, autoincrement=True)
    day_id = Column(SmallInteger, ForeignKey('drive_day.day_id'), nullable=False)
    status = Column(SmallInteger)
    creation_time = Column(BigInteger, nullable=False)
    start_time = Column(BigInteger)
    end_time = Column(BigInteger)
    packet_start = Column(BigInteger)
    packet_end = Column(BigInteger)
    car_id = Column(SmallInteger, ForeignKey('lut_car.car_id'), nullable=False)
    driver_id = Column(SmallInteger, ForeignKey('lut_driver.driver_id'), nullable=False)
    location_id = Column(SmallInteger, ForeignKey('lut_location.location_id'), nullable=False)
    event_type = Column(SmallInteger, ForeignKey('lut_event_type.type_id'), nullable=False)
    event_index = Column(SmallInteger)
    car_weight = Column(SmallInteger)
    tow_angle = Column(Float)
    # Alignment per axle
    camber_front = Column(Float)
    camber_rear = Column(Float)
    toe_front = Column(Float)
    toe_rear = Column(Float)
    # Ride height per axle (+ legacy single)
    ride_height_front = Column(Float)
    ride_height_rear = Column(Float)
    ride_height = Column(Float)
    ackerman_adjustment = Column(Float)
    shock_dampening = Column(SmallInteger)
    power_limit = Column(Integer)
    torque_limit = Column(SmallInteger)
    frw_pressure = Column(Float)
    flw_pressure = Column(Float)
    brw_pressure = Column(Float)
    blw_pressure = Column(Float)
    # Tire wear depth and durometer
    fr_wear_depth = Column(Float)
    fl_wear_depth = Column(Float)
    rr_wear_depth = Column(Float)
    rl_wear_depth = Column(Float)
    fr_durometer = Column(Float)
    fl_durometer = Column(Float)
    rr_durometer = Column(Float)
    rl_durometer = Column(Float)
    # Damping per corner (LSC/LSR/HSC/HSR)
    fr_lsc = Column(SmallInteger)
    fr_lsr = Column(SmallInteger)
    fr_hsc = Column(SmallInteger)
    fr_hsr = Column(SmallInteger)
    fl_lsc = Column(SmallInteger)
    fl_lsr = Column(SmallInteger)
    fl_hsc = Column(SmallInteger)
    fl_hsr = Column(SmallInteger)
    rr_lsc = Column(SmallInteger)
    rr_lsr = Column(SmallInteger)
    rr_hsc = Column(SmallInteger)
    rr_hsr = Column(SmallInteger)
    rl_lsc = Column(SmallInteger)
    rl_lsr = Column(SmallInteger)
    rl_hsc = Column(SmallInteger)
    rl_hsr = Column(SmallInteger)
    front_wing_on = Column(Boolean)
    rear_wing_on = Column(Boolean)
    front_wing_pitch = Column(Float)
    rear_wing_pitch = Column(Float)
    regen_on = Column(Boolean)
    undertray_on = Column(Boolean)
    # 2026 per-axle corner spring rates (Orion)
    front_corner_spring_rate = Column(Float)
    rear_corner_spring_rate = Column(Float)
    # ARB settings (free text)
    front_arb_setting = Column(Text)
    rear_arb_setting = Column(Text)
    drive_day = relationship("DriveDay", back_populates="events")
    car = relationship("LutCar", back_populates="events")
    driver = relationship("LutDriver", back_populates="events")
    location = relationship("LutLocation", back_populates="events")
    event_type_ref = relationship("LutEventType", back_populates="events")
    classifiers = relationship("Classifier", back_populates="event")


# -----------------------------
# -----------------------------


class Partitions(Base):
    __tablename__ = 'partitions'
    __table_args__ = {'schema': 'public', 'extend_existing': True}
    partition_name = Column(Text, primary_key=True)
    start_time = Column(BigInteger, primary_key=True)
    end_time = Column(BigInteger, nullable=False)

class CarStatusSegment(Base):
    # Written by the car_status processor: one row per closed state segment.
    # Standalone (car + time range), no drive_day FK; active_faults are advisory.
    __tablename__ = 'car_status_segment'
    __table_args__ = {'schema': 'public', 'extend_existing': True}
    segment_id = Column(BigInteger, primary_key=True, autoincrement=True)
    car = Column(Text, nullable=False)
    state = Column(Text, nullable=False)
    start_time = Column(BigInteger, nullable=False)
    end_time = Column(BigInteger)
    start_packet = Column(BigInteger)
    end_packet = Column(BigInteger)
    hv_soc_avg = Column(Float)
    lv_v_avg = Column(Float)
    active_faults = Column(Text)

from lib.codegen.telemetry.orion_models import declare_models as declare_orion_models
from lib.codegen.telemetry.artemis_models import declare_models as declare_artemis_models

ORION_MODELS = declare_orion_models(BaseOrion)
ARTEMIS_MODELS = declare_artemis_models(BaseArtemis)
globals().update(ORION_MODELS)
globals().update(ARTEMIS_MODELS)

# Unqualified analysis interfaces refer to Orion, the existing packet-based car.
BaseTelemetry = BaseOrion
Packet = OrionPacket
Dynamics = OrionDynamics
Controls = OrionControls
Pack = OrionPack
DiagnosticsHigh = OrionDiagnosticsHigh
DiagnosticsLow = OrionDiagnosticsLow
Thermal = OrionThermal

class OrionGpsPrecise(BaseOrion):
    """
    publishd's GPS-only feed (MQTT orion/gps): one row per receiver fix at up
    to PUBLISHD_GPS_HZ, beside the 20 Hz dynamics rows. Hand-written, not from
    the CAN spec: no packet row and no FK, so it never slows the full-frame
    insert and the normal tables keep their rate. reconcile_orm_schema()
    creates it (with the time index) on a deployed database at ingest startup;
    orion_db_init.sql carries the same DDL for fresh volumes.
    """
    __tablename__ = 'gps_precise'
    __table_args__ = (Index('idx_gps_precise_time', 'time'), {'schema': 'public', 'extend_existing': True})
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    time = Column(BigInteger, nullable=False)
    packet_id = Column(BigInteger)
    lat_precise = Column(Float)
    lon_precise = Column(Float)
    gps_h_m = Column(Float)
    gps_sigma_h_m = Column(Float)
    gps_src = Column(SmallInteger)
    gps_fix_time = Column(Float)
    gps_speed_kn = Column(Float)

class ArtemisGpsPrecise(BaseArtemis):
    """Independently timestamped receiver fixes, without a packet relationship."""
    __tablename__ = 'gps_precise'
    __table_args__ = {'schema': 'public', 'extend_existing': True}
    session_start_ms = Column(BigInteger, primary_key=True)
    sample_time_ns = Column(BigInteger, primary_key=True)
    time = Column(BigInteger, primary_key=True)
    lat_precise = Column(Float(53))
    lon_precise = Column(Float(53))
    gps_h_m = Column(Float(53))
    gps_sigma_h_m = Column(Float(53))
    gps_src = Column(SmallInteger)
    gps_fix_time = Column(Float(53))
    gps_speed_kn = Column(Float(53))


class ArtemisRecordingSession(BaseArtemis):
    __tablename__ = 'recording_session'
    __table_args__ = {'schema': 'public'}
    session_start_ms = Column(BigInteger, primary_key=True)
    start_time = Column(BigInteger, nullable=False)
    end_time = Column(BigInteger, nullable=False)
    data_revision = Column(BigInteger, nullable=False, default=0)
    external_revision = Column(BigInteger, nullable=False, default=0)
    last_write_ms = Column(BigInteger, nullable=False, default=0)


class ArtemisIngestProgress(BaseArtemis):
    __tablename__ = 'ingest_progress'
    __table_args__ = {'schema': 'public'}
    topic = Column(Text, primary_key=True)
    partition = Column(Integer, primary_key=True)
    next_offset = Column(BigInteger, nullable=False)



class ArtemisStorageMigration(BaseArtemis):
    __tablename__ = 'storage_migration'
    __table_args__ = {'schema': 'public'}
    storage_id = Column(Text, primary_key=True)
    applied_at = Column(DateTime(timezone=True), nullable=False)
    contract = Column(JSONB, nullable=False)
