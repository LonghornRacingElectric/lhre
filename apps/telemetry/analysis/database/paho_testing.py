"""Artemis telemetry fixtures, logger CSV replay and MQTT publishing."""
import argparse
import datetime
import json
import logging
import math
import os
from pathlib import Path
import secrets
import time
from typing import Tuple, Union

import numpy as np
import pandas as pd
import requests
from numpy.random import default_rng
from google.protobuf.descriptor import FieldDescriptor
from paho.mqtt import client as mqtt_client
from tqdm import tqdm

from analysis.database.native_demo import contract, field_definitions, publish
from stack.ingest.protobuf import artemis_pb2


class DataTester:
    """
    Class for testing database with random values in correct data types or CSV data.
    """
    def __init__(self, mqtt, seed=None, csv_path=None, mapping_path=None, value_profile: str = "generic"):
        """
        Initializes DataTester class by initiating a numpy.random.Generator.

        :param seed:        seed to pass to numpy.random.Generator constructor
        :param csv_path:    path to CSV file to read data from (optional)
        :param mapping_path: path to JSON mapping file (optional)
        """
        self.rng = default_rng(seed)
        self.mqtt = mqtt
        self.csv_path = csv_path
        self.mapping_path = mapping_path
        self.csv_data = None
        self.mapping = None
        self.csv_index = 0
        self.value_profile = value_profile.lower().strip()
        if self.value_profile not in ("generic", "viewer"):
            raise ValueError("value_profile must be generic or viewer")
        self.storage = contract()
        self.definitions = field_definitions()
        self.pools = {pool["table"]: pool for pool in self.storage["pools"]}
        self.pool_by_field = {(pool["group"], field["name"]): pool["table"]
                              for pool in self.storage["pools"] for field in pool["fields"]}
        groups = self.get_proto_desc()
        self.pool_descriptions = {
            pool["table"]: {field["name"]: groups[pool["group"]][field["name"]]
                            for field in pool["fields"]}
            for pool in self.storage["pools"]
        }
        self.array_lengths = {field["names"][-1]: len(field["layout"])
                              for field in self.definitions.values() if field["repeated"]}
        self._viewer_story_packet = None
        self._viewer_story_data = None
        
        if csv_path:
            self.load_csv(csv_path)
        if mapping_path:
            self.load_mapping(mapping_path)

    @staticmethod
    def _is_int_dtype(dtype: type) -> bool:
        return np.issubdtype(dtype, np.integer) and dtype is not bool

    @staticmethod
    def _is_float_dtype(dtype: type) -> bool:
        return np.issubdtype(dtype, np.floating)

    @staticmethod
    def _cast_numeric(value: float, dtype: type):
        if np.issubdtype(dtype, np.integer):
            return int(round(value))
        if np.issubdtype(dtype, np.floating):
            return float(value)
        return value

    @staticmethod
    def _clamp(value: float, min_value: float, max_value: float) -> float:
        return max(min_value, min(max_value, value))

    @staticmethod
    def _viewer_noise(packet: int, channel: str, scale: float = 1.0) -> float:
        # Deterministic, packet-indexed noise so all table rows for a packet stay coherent.
        phase = (sum(ord(ch) for ch in channel) % 360) * math.pi / 180.0
        return scale * math.sin(packet * 0.173 + phase)

    def _viewer_story(self, packet: int):
        if self._viewer_story_packet == packet and self._viewer_story_data is not None:
            return self._viewer_story_data

        # Use a slightly accelerated sim-time so UI widgets visibly evolve during short demos.
        t = float(packet) * 0.06
        lap_period_s = 18.0
        phase = (t % lap_period_s) / lap_period_s
        theta = 2.0 * math.pi * phase

        corner_intensity = abs(math.sin(theta)) ** 1.35
        straight_intensity = 1.0 - corner_intensity

        throttle_pct = self._clamp(
            22.0 + 68.0 * straight_intensity + self._viewer_noise(packet, "throttle", 3.0),
            0.0,
            100.0,
        )
        brake_pct = self._clamp(
            58.0 * corner_intensity - 12.0 * straight_intensity + self._viewer_noise(packet, "brake", 2.5),
            0.0,
            100.0,
        )
        speed_mps = self._clamp(
            11.0 + 22.0 * straight_intensity + 2.0 * math.sin(2.0 * theta) + self._viewer_noise(packet, "speed", 0.7),
            2.0,
            42.0,
        )
        steer_deg = self._clamp(
            105.0 * math.sin(theta) * corner_intensity + self._viewer_noise(packet, "steer", 2.0),
            -130.0,
            130.0,
        )
        steer_v = self._clamp(
            1.25 + (steer_deg / 135.0) * 1.1 + self._viewer_noise(packet, "steer_v", 0.02),
            0.0,
            2.5,
        )
        heading_deg = (90.0 - math.degrees(theta)) % 360.0
        heading_rad = math.radians(heading_deg)

        lat = 30.289464 + 0.00055 * math.cos(theta)
        lon = -97.735303 + 0.00085 * math.sin(theta)

        longitudinal_accel = self._clamp(
            3.3 * (throttle_pct / 100.0) - 4.1 * (brake_pct / 100.0) - 0.15 + 0.25 * math.sin(4.0 * theta),
            -5.5,
            4.0,
        )
        lateral_accel = self._clamp(3.6 * math.sin(theta) * corner_intensity, -4.2, 4.2)
        vertical_accel = 0.15 * math.sin(7.0 * theta + 0.4)
        yaw_rate = 28.0 * math.sin(theta)

        hv_pack_v = self._clamp(
            410.0 + 22.0 * straight_intensity - 5.0 * corner_intensity + self._viewer_noise(packet, "hv_v", 2.0),
            330.0,
            620.0,
        )
        power_kw = 1.25 * throttle_pct - 1.55 * brake_pct - 6.0 + self._viewer_noise(packet, "power", 2.0)
        hv_current = self._clamp((power_kw * 1000.0) / max(hv_pack_v, 1.0), -220.0, 240.0)
        soc_pct = self._clamp(84.0 + 4.0 * math.sin(t / 40.0) - 0.0035 * t, 18.0, 98.0)
        lv_v = self._clamp(13.2 + 0.18 * math.sin(t / 45.0) + self._viewer_noise(packet, "lv_v", 0.05), 11.8, 14.6)
        lv_current = self._clamp(
            7.0 + 0.16 * throttle_pct + 0.05 * abs(hv_current) + self._viewer_noise(packet, "lv_c", 1.2),
            2.0,
            60.0,
        )

        ambient_temp_c = 27.5 + 3.0 * math.sin(t / 400.0)
        thermal_load = self._clamp((max(power_kw, 0.0) / 95.0) + (speed_mps / 55.0), 0.0, 1.8)
        inverter_temp_c = ambient_temp_c + 13.0 + 24.0 * thermal_load + 1.2 * math.sin(t / 30.0)
        motor_temp_c = ambient_temp_c + 11.0 + 22.0 * thermal_load + 1.0 * math.cos(t / 35.0)
        coolant_temp_c = ambient_temp_c + 9.0 + 16.0 * thermal_load + 0.8 * math.sin(t / 40.0)
        battery_temp_c = ambient_temp_c + 7.0 + 14.0 * thermal_load + 0.7 * math.sin(t / 50.0)
        fan_rpm = self._clamp(
            900.0
            + max(inverter_temp_c - 45.0, 0.0) * 95.0
            + max(battery_temp_c - 40.0, 0.0) * 80.0,
            800.0,
            6000.0,
        )
        odometer = 1240.0 + 0.018 * t

        steer_factor = steer_deg / 130.0
        flw_speed = self._clamp(speed_mps * (1.0 - 0.08 * steer_factor) + self._viewer_noise(packet, "flw", 0.4), 0.0, 45.0)
        frw_speed = self._clamp(speed_mps * (1.0 + 0.08 * steer_factor) + self._viewer_noise(packet, "frw", 0.4), 0.0, 45.0)
        blw_speed = self._clamp(speed_mps * (1.0 - 0.06 * steer_factor) + self._viewer_noise(packet, "blw", 0.35), 0.0, 45.0)
        brw_speed = self._clamp(speed_mps * (1.0 + 0.06 * steer_factor) + self._viewer_noise(packet, "brw", 0.35), 0.0, 45.0)

        cell_v_base = self._clamp(3.45 + (soc_pct / 100.0) * 0.72, 3.2, 4.2)
        cells_v = [
            self._clamp(
                cell_v_base + 0.03 * math.sin(theta + i * 0.7) + self._viewer_noise(packet, f"cell_v_{i}", 0.01),
                3.2,
                4.22,
            )
            for i in range(12)
        ]
        cells_temp = [
            self._clamp(
                battery_temp_c + 1.8 * math.sin(theta + i * 0.6) + self._viewer_noise(packet, f"cell_t_{i}", 0.35),
                20.0,
                75.0,
            )
            for i in range(12)
        ]

        fault_cycle = int(t) % 210
        fault_active = 160 <= fault_cycle <= 163
        fault_leg = int((t / 210.0) % 4) + 1
        shutdown_legs = {
            f"shutdown_leg{i}": (not fault_active or i != fault_leg)
            for i in range(1, 13)
        }

        ride_height_mm = {
            "fl": 52.0 + 4.0 * math.sin(theta + 0.2),
            "fr": 52.0 + 4.0 * math.sin(theta - 0.2),
            "bl": 51.0 + 3.5 * math.sin(theta + 0.4),
            "br": 51.0 + 3.5 * math.sin(theta - 0.4),
        }

        story = {
            "t": t,
            "lat": lat,
            "lon": lon,
            "speed_mps": speed_mps,
            "flw_speed": flw_speed,
            "frw_speed": frw_speed,
            "blw_speed": blw_speed,
            "brw_speed": brw_speed,
            "throttle_pct": throttle_pct,
            "brake_pct": brake_pct,
            "steer_deg": steer_deg,
            "steer_v": steer_v,
            "heading_deg": heading_deg,
            "heading_rad": heading_rad,
            "longitudinal_accel": longitudinal_accel,
            "lateral_accel": lateral_accel,
            "vertical_accel": vertical_accel,
            "yaw_rate": yaw_rate,
            "hv_pack_v": hv_pack_v,
            "hv_current": hv_current,
            "soc_pct": soc_pct,
            "lv_v": lv_v,
            "lv_current": lv_current,
            "ambient_temp_c": ambient_temp_c,
            "inverter_temp_c": inverter_temp_c,
            "motor_temp_c": motor_temp_c,
            "coolant_temp_c": coolant_temp_c,
            "battery_temp_c": battery_temp_c,
            "fan_rpm": fan_rpm,
            "odometer": odometer,
            "cells_v": cells_v,
            "cells_temp": cells_temp,
            "avg_cell_v": float(np.mean(cells_v)),
            "avg_cell_temp": float(np.mean(cells_temp)),
            "contactor_state": 2 if not fault_active else 0,
            "hvc_state_machine": 5 if not fault_active else 2,
            "shutdown_current": self._clamp(0.6 + abs(hv_current) * 0.04, 0.0, 20.0),
            "contactors_closed": not fault_active,
            "fault_active": fault_active,
            "shutdown_legs": shutdown_legs,
            "ride_height_mm": ride_height_mm,
        }

        self._viewer_story_packet = packet
        self._viewer_story_data = story
        return story

    def _viewer_scalar_value(self, col: str, dtype: type, packet: int):
        """
        Generate coherent, plausible scalar telemetry values for live-viewer demos.
        """
        story = self._viewer_story(packet)
        lower_col = col.lower()

        if dtype is bool:
            if lower_col.startswith("shutdown_leg"):
                leg_digits = ''.join(ch for ch in lower_col if ch.isdigit())
                leg_idx = int(leg_digits) if leg_digits else 1
                return bool(story["shutdown_legs"].get(f"shutdown_leg{leg_idx}", True))
            if lower_col in {
                "neg_hv_contactor",
                "pos_hv_contactor",
                "precharge_contactor",
                "r2d_authorized",
                "r2d_status",
            }:
                return bool(story["contactors_closed"])
            if lower_col in {"cells_v_balanced", "enable", "direction"}:
                return True
            if any(token in lower_col for token in ("error", "fault", "disconnect", "implause", "mismatch", "fail", "damaged")):
                return bool(story["fault_active"] and self._viewer_noise(packet, lower_col, 1.0) > 0.6)
            return bool(self._viewer_noise(packet, lower_col, 1.0) > 0.15)

        if not (self._is_int_dtype(dtype) or self._is_float_dtype(dtype)):
            return None

        if lower_col in {"odometer"}:
            return self._cast_numeric(story["odometer"], dtype)
        if lower_col in {"time_since_on"}:
            return self._cast_numeric(story["t"], dtype)
        if lower_col.endswith("_last_seen_s"):
            board_name = lower_col.removesuffix("_last_seen_s")
            # Viewer profile shows a healthy car; all boards active (<1s).
            # Stream dropout (no active publish) is detected by the dashboard via elapsed time.
            healthy_s = 0.03 + abs(self._viewer_noise(packet, board_name, 0.09))
            return self._cast_numeric(healthy_s, dtype)

        if lower_col in {"hv_soc"}:
            return self._cast_numeric(story["soc_pct"], dtype)
        if lower_col in {"hv_pack_v", "hv_tractive_v", "dc_bus_v", "bus_voltage", "batt_v"}:
            return self._cast_numeric(story["hv_pack_v"], dtype)
        if lower_col in {"hv_c", "dc_bus_current", "batt_c"}:
            return self._cast_numeric(story["hv_current"], dtype)

        if lower_col in {"avg_cell_v"}:
            return self._cast_numeric(story["avg_cell_v"], dtype)
        if lower_col in {"cell_min_v"}:
            return self._cast_numeric(min(story["cells_v"]), dtype)
        if lower_col in {"cell_max_v"}:
            return self._cast_numeric(max(story["cells_v"]), dtype)
        if lower_col in {"avg_cell_temp"}:
            return self._cast_numeric(story["avg_cell_temp"], dtype)

        if lower_col in {"lv_v", "lv_batt_v"}:
            return self._cast_numeric(story["lv_v"], dtype)
        if lower_col in {"lv_c", "lv_batt_c", "lv_boards_current", "lights_current"}:
            return self._cast_numeric(story["lv_current"], dtype)

        if lower_col in {"contactor_state"}:
            return self._cast_numeric(story["contactor_state"], dtype)
        if lower_col in {"hvc_state_machine"}:
            return self._cast_numeric(story["hvc_state_machine"], dtype)
        if lower_col in {"shutdown_current"}:
            return self._cast_numeric(story["shutdown_current"], dtype)

        if lower_col in {"steer_col_angle"}:
            return self._cast_numeric(story["steer_deg"], dtype)
        if lower_col == "fl_steer_angle":
            return self._cast_numeric(story["steer_deg"] * 0.94, dtype)
        if lower_col == "fr_steer_angle":
            return self._cast_numeric(story["steer_deg"] * 1.06, dtype)
        if lower_col in {"steer_v"}:
            return self._cast_numeric(story["steer_v"], dtype)

        if lower_col in {"flw_speed", "fl_wheel_speed"}:
            return self._cast_numeric(story["flw_speed"], dtype)
        if lower_col in {"frw_speed", "fr_wheel_speed"}:
            return self._cast_numeric(story["frw_speed"], dtype)
        if lower_col in {"blw_speed", "bl_wheel_speed"}:
            return self._cast_numeric(story["blw_speed"], dtype)
        if lower_col in {"brw_speed", "br_wheel_speed"}:
            return self._cast_numeric(story["brw_speed"], dtype)
        if lower_col == "gps_speed":
            return self._cast_numeric(story["speed_mps"] / 0.5144444444444445, dtype)
        if lower_col in {"wheel_speed", "dash_speed", "gps_velocity", "f_gps_velocity", "b_gps_velocity"}:
            return self._cast_numeric(story["speed_mps"], dtype)
        if lower_col in {"motor_speed"}:
            return self._cast_numeric(story["speed_mps"] * 165.0, dtype)
        if lower_col in {"inverter_rpm", "rpm_request"}:
            return self._cast_numeric(story["speed_mps"] * 170.0, dtype)

        if lower_col in {"accel_pedal_travel", "apps1_travel", "apps2_travel", "accel_pedal_t"}:
            throttle = story["throttle_pct"]
            if lower_col.endswith("_t"):
                throttle /= 100.0
            return self._cast_numeric(throttle, dtype)
        if lower_col in {"bpps1_travel", "bpps2_travel", "brake_pedal_t", "bpps1_t", "bpps2_t"}:
            brake = story["brake_pct"]
            if lower_col.endswith("_t"):
                brake /= 100.0
            return self._cast_numeric(brake, dtype)
        if lower_col in {"apps1_v", "apps2_v", "bse1_v", "bse2_v", "bse3_v", "bpps1_v", "bpps2_v"}:
            if lower_col.startswith("apps"):
                return self._cast_numeric(0.4 + 4.3 * (story["throttle_pct"] / 100.0), dtype)
            return self._cast_numeric(0.4 + 4.3 * (story["brake_pct"] / 100.0), dtype)
        if lower_col in {"apps1_t", "apps2_t"}:
            return self._cast_numeric(story["throttle_pct"] / 100.0, dtype)
        if "brake_pressure" in lower_col:
            return self._cast_numeric(6.0 * (story["brake_pct"] / 100.0), dtype)
        if lower_col in {"brake_bias"}:
            return self._cast_numeric(52.0 + self._viewer_noise(packet, "brake_bias", 2.0), dtype)
        if lower_col in {"brake_light_pct"}:
            return self._cast_numeric(story["brake_pct"], dtype)

        if lower_col in {"torque_request", "torque_command", "torque_feedback", "commanded_torque", "torque_limit"}:
            torque = self._clamp((story["throttle_pct"] - story["brake_pct"]) * 2.1, -220.0, 220.0)
            if lower_col == "torque_limit":
                torque = max(torque, 0.0) + 70.0
            return self._cast_numeric(torque, dtype)
        if lower_col in {"motor_angle"}:
            return self._cast_numeric((packet * 7) % 360, dtype)
        if lower_col in {"inverter_freq"}:
            return self._cast_numeric(40.0 + story["speed_mps"] * 4.5, dtype)
        if lower_col in {"flow_rate", "motor_loop_flow_rate"}:
            return self._cast_numeric(20.0 + story["speed_mps"] * 0.35, dtype)
        if lower_col in {"rad_fan_set", "batt_fan_set"}:
            return self._cast_numeric(min(100.0, story["fan_rpm"] / 60.0), dtype)
        if lower_col in {"rad_fan_rpm", "batt_fan_rpm", "motor_loop_rad_fan_speed", "batt_loop_rad_fan_speed"}:
            return self._cast_numeric(story["fan_rpm"], dtype)
        if lower_col in {"phase_a_current", "phase_b_current", "phase_c_current"}:
            phase_offset = {"phase_a_current": 0.0, "phase_b_current": 0.2, "phase_c_current": -0.2}[lower_col]
            return self._cast_numeric(story["hv_current"] * (1.0 + phase_offset), dtype)
        if lower_col in {"hv_charge_state", "lv_charge_state"}:
            return self._cast_numeric(1.0 if story["hv_current"] < -5.0 else 0.0, dtype)
        if lower_col in {"precharge_r_temp"}:
            return self._cast_numeric(story["coolant_temp_c"] + 2.5, dtype)
        if lower_col in {"discharge_r_temp"}:
            return self._cast_numeric(story["coolant_temp_c"] + 3.5, dtype)
        if lower_col in {"cell_top_temp", "cell_bottom_temp"}:
            return self._cast_numeric(story["battery_temp_c"] + self._viewer_noise(packet, lower_col, 1.2), dtype)
        if lower_col in {"vab_vq_v", "vbc_vd_v", "neutral_output_v"}:
            return self._cast_numeric(0.12 * story["hv_pack_v"], dtype)
        if lower_col in {"delta_resolver_angle"}:
            return self._cast_numeric((packet * 11) % 360, dtype)
        if lower_col in {"ride_height"}:
            return self._cast_numeric(np.mean(list(story["ride_height_mm"].values())), dtype)
        if lower_col in {"fl_ride_height", "fr_ride_height", "bl_ride_height", "br_ride_height"}:
            corner = lower_col[:2]
            return self._cast_numeric(story["ride_height_mm"][corner], dtype)
        if lower_col in {"fl_sus_pot_v", "fr_sus_pot_v", "bl_sus_pot_v", "br_sus_pot_v", "sus1_v", "sus2_v"}:
            if lower_col.startswith("fl"):
                ride_height = story["ride_height_mm"]["fl"]
            elif lower_col.startswith("fr"):
                ride_height = story["ride_height_mm"]["fr"]
            elif lower_col.startswith("bl"):
                ride_height = story["ride_height_mm"]["bl"]
            elif lower_col.startswith("br"):
                ride_height = story["ride_height_mm"]["br"]
            else:
                ride_height = np.mean(list(story["ride_height_mm"].values()))
            return self._cast_numeric(self._clamp(ride_height / 15.0, 0.5, 4.5), dtype)
        if lower_col in {"fl_strain_gauge_v", "fr_strain_gauge_v", "bl_strain_gauge_v", "br_strain_gauge_v"}:
            return self._cast_numeric(2.5 + 0.18 * story["lateral_accel"] + self._viewer_noise(packet, lower_col, 0.1), dtype)

        if lower_col.endswith("_heading"):
            return self._cast_numeric(story["heading_deg"], dtype)
        if lower_col.endswith("_velocity") or lower_col.endswith("_v"):
            return self._cast_numeric(story["speed_mps"], dtype)

        if "ambient" in lower_col and "temp" in lower_col:
            return self._cast_numeric(story["ambient_temp_c"], dtype)
        if "inverter" in lower_col and "temp" in lower_col:
            return self._cast_numeric(story["inverter_temp_c"], dtype)
        if "motor" in lower_col and "temp" in lower_col:
            return self._cast_numeric(story["motor_temp_c"], dtype)
        if ("coolant" in lower_col or "water" in lower_col or "rad_temp" in lower_col) and "temp" in lower_col:
            return self._cast_numeric(story["coolant_temp_c"], dtype)
        if ("cell" in lower_col and "temp" in lower_col) or ("batt" in lower_col and "temp" in lower_col):
            return self._cast_numeric(story["battery_temp_c"], dtype)
        if "temp" in lower_col:
            return self._cast_numeric(story["battery_temp_c"] + self._viewer_noise(packet, lower_col, 1.0), dtype)

        if lower_col.endswith("_speed"):
            return self._cast_numeric(story["speed_mps"], dtype)

        # Fallback to visible-changing values for UI demos.
        if self._is_float_dtype(dtype):
            return float(50.0 + 20.0 * self._viewer_noise(packet, lower_col, 1.0))
        return int(round(50.0 + 20.0 * self._viewer_noise(packet, lower_col, 1.0)))

    def _viewer_vector_value(self, col: str, dtype: type, ndims: int, packet: int):
        story = self._viewer_story(packet)
        lower_col = col.lower()

        def cast_vector(values):
            if np.issubdtype(dtype, np.integer):
                return [int(round(v)) for v in values]
            if np.issubdtype(dtype, np.floating):
                return [float(v) for v in values]
            return values

        if lower_col == "gps_precise" and ndims == 1:
            return cast_vector([story["lat"], story["lon"], 250., .1, 1., story["t"]])
        if lower_col in {"gps", "f_gps", "b_gps"} and ndims == 1:
            lat = float(story["lat"])
            lon = float(story["lon"])
            return cast_vector([lat, lon])

        if "cells_v" in lower_col and ndims == 1:
            return cast_vector(list(story["cells_v"]))

        if "cells_temp" in lower_col and ndims == 1:
            return cast_vector(list(story["cells_temp"]))

        if lower_col in {"vcu_position"} and ndims == 1:
            return cast_vector([story["lat"], story["lon"], story["odometer"]])

        if lower_col in {"vcu_velocity"} and ndims == 1:
            vx = story["speed_mps"] * math.cos(story["heading_rad"])
            vy = story["speed_mps"] * math.sin(story["heading_rad"])
            return cast_vector([vx, vy, 0.0])

        if lower_col in {"vcu_accel", "cent_mass_accel", "gps_imu"} and ndims == 1:
            return cast_vector([
                story["longitudinal_accel"],
                story["lateral_accel"],
                story["vertical_accel"],
            ])

        if lower_col in {"wheel_speed"} and ndims == 1:
            return cast_vector([
                story["flw_speed"],
                story["frw_speed"],
                story["blw_speed"],
                story["brw_speed"],
            ])

        if ("accel" in lower_col or "gyro" in lower_col) and ndims == 1:
            accel_offsets = {
                "fl": (-0.15, 0.25),
                "fr": (-0.15, -0.25),
                "bl": (0.15, 0.25),
                "br": (0.15, -0.25),
            }
            corner = lower_col[:2] if lower_col[:2] in accel_offsets else None
            long_offset, lat_offset = accel_offsets.get(corner, (0.0, 0.0))

            if "gyro" in lower_col or "ang_rate" in lower_col:
                return cast_vector([
                    0.02 * story["longitudinal_accel"],
                    0.02 * story["lateral_accel"],
                    story["yaw_rate"],
                ])

            return cast_vector([
                story["longitudinal_accel"] + long_offset,
                story["lateral_accel"] + lat_offset,
                story["vertical_accel"],
            ])

        return None

    def load_csv(self, csv_path):
        """Load one Artemis logger CSV or merge a directory's pool files by receipt time."""
        path = Path(csv_path)
        files = sorted(path.glob("artemis_*.csv")) if path.is_dir() else [path]
        if not files:
            raise ValueError(f"No Artemis logger CSVs found in {path}")
        # String columns keep nanosecond integers exact and distinguish blanks from zero.
        self.csv_data = pd.concat(
            [pd.read_csv(file, dtype=str, keep_default_na=False) for file in files],
            ignore_index=True,
        ).fillna("")
        if self.mapping is None and "sample_time_ns" in self.csv_data:
            order = sorted(range(len(self.csv_data)), key=lambda i: int(self.csv_data.iloc[i]["sample_time_ns"]))
            self.csv_data = self.csv_data.iloc[order].reset_index(drop=True)
        if self.csv_data.empty:
            raise ValueError("CSV contains no observations")
        self.csv_index = 0

    def load_mapping(self, mapping_path):
        """Map qualified Artemis field/metadata names to CSV columns or column lists."""
        with open(mapping_path) as source:
            self.mapping = json.load(source)
        if not isinstance(self.mapping, dict):
            raise ValueError("CSV mapping must be an object")
        for name in self.mapping:
            if name not in ("time", "sample_time_ns", "updated_fields") and name.split("[")[0] not in self.definitions:
                raise ValueError(f"Unknown Artemis mapping field: {name}")

    @staticmethod
    def _coerce(value, field):
        if field.type == FieldDescriptor.TYPE_BOOL:
            if isinstance(value, str):
                if value.lower() not in ("true", "false", "1", "0"):
                    raise ValueError(f"Invalid Boolean value: {value!r}")
                return value.lower() in ("true", "1")
            return bool(value)
        if field.type in (FieldDescriptor.TYPE_FLOAT, FieldDescriptor.TYPE_DOUBLE):
            return float(value)
        if field.type == FieldDescriptor.TYPE_STRING:
            return str(value)
        if field.type == FieldDescriptor.TYPE_BYTES:
            return value.encode() if isinstance(value, str) else bytes(value)
        return int(value)

    def create_row_from_csv(self, packet=0, table_desc=None):
        """Read one fresh logger observation; packet is only a fixture index."""
        if self.csv_data is None:
            raise ValueError("CSV data is not loaded")
        source = self.csv_data.iloc[self.csv_index].to_dict()
        self.csv_index = (self.csv_index + 1) % len(self.csv_data)
        values = dict(source)
        for name, columns in (self.mapping or {}).items():
            values[name] = [source[column] for column in columns] if isinstance(columns, list) else source[columns]
        try:
            stamp = int(values["sample_time_ns"])
            time_ms = int(values["time"])
            updated = json.loads(values["updated_fields"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Artemis CSV requires time, sample_time_ns and updated_fields") from error
        if stamp <= 0 or time_ms != stamp // 1_000_000:
            raise ValueError("CSV time does not match sample_time_ns")
        if not isinstance(updated, list) or not updated or any(not isinstance(name, str) for name in updated) or len(set(updated)) != len(updated):
            raise ValueError("CSV updated_fields must be a nonempty list of unique qualified names")
        row = {"time": time_ms, "sample_time_ns": stamp, "updated_fields": updated}
        for name in updated:
            if name not in self.definitions or "." not in name:
                raise ValueError(f"Unknown Artemis CSV field: {name}")
            group, column = name.split(".")
            field = artemis_pb2.ArtemisSensorData.DESCRIPTOR.fields_by_name[group].message_type.fields_by_name[column]
            if field.is_repeated:
                if name in values and isinstance(values[name], list):
                    items = values[name]
                else:
                    slots = {int(key[len(name) + 1:-1]): value for key, value in values.items()
                             if key.startswith(name + "[") and key.endswith("]")}
                    nonempty = [index for index, value in slots.items() if value != ""]
                    count = max(nonempty, default=-1) + 1
                    if any(slots.get(index, "") == "" for index in range(count)):
                        raise ValueError(f"Missing array slot in {name}")
                    items = [slots[index] for index in range(count)]
                row[name] = [self._coerce(item, field) for item in items]
            else:
                if name not in values or values[name] == "":
                    raise ValueError(f"Fresh scalar {name} is blank")
                row[name] = self._coerce(values[name], field)
        return row

    @staticmethod
    def _select_description(desc, tables=None, rm_cols=None):
        pools = {pool["table"]: pool for pool in contract()["pools"]}
        requested = [tables] if isinstance(tables, str) else tables
        selected = {}
        for table in requested or desc:
            if table in desc:
                selected[table] = dict(desc[table])
            elif table in pools:
                pool = pools[table]
                selected[table] = {field["name"]: desc[pool["group"]][field["name"]] for field in pool["fields"]}
            else:
                matching = [pool for pool in pools.values() if pool["group"] == table]
                if not matching:
                    raise ValueError(f"Unknown Artemis group or pool: {table}")
                selected[table] = {name: spec for pool in matching for name, spec in desc[pool["table"]].items()}
            removed = [rm_cols] if isinstance(rm_cols, str) else rm_cols
            if isinstance(removed, dict):
                removed = removed.get(table, [])
            for column in removed or []:
                selected[table].pop(column, None)
        return selected

    @classmethod
    def get_desc(cls, db=False, tables=None, rm_cols=None, **get_specs):
        """Return generated Artemis pool column types without connecting to a DB."""
        cls._root_proto_message(get_specs.get("target", "Artemis"))
        if bool(db) == bool(tables):
            raise ValueError("Select either the full schema (db=True) or tables")
        types = {"double precision": float, "numeric": float, "boolean": bool,
                 "integer": int, "bigint": int, "smallint": int, "text": str, "bytea": bytes}
        desc = {}
        for pool in contract()["pools"]:
            columns = {"time": (int, 0), "sample_time_ns": (int, 0)}
            for field in pool["fields"]:
                repeated = field["sql_type"].endswith("[]")
                sql_type = field["sql_type"].removesuffix("[]")
                columns[field["name"]] = (types[sql_type], int(repeated))
            desc[pool["table"]] = columns
        return cls._select_description(desc, tables, rm_cols)

    @staticmethod
    def _root_proto_message(target="Artemis"):
        if target != "Artemis":
            raise ValueError("Telemetry publishing supports Artemis only")
        return artemis_pb2.ArtemisSensorData

    @staticmethod
    def _proto_scalar_dtype(field_type: int):
        if field_type in (FieldDescriptor.TYPE_FLOAT, FieldDescriptor.TYPE_DOUBLE):
            return float
        if field_type in (
            FieldDescriptor.TYPE_INT32,
            FieldDescriptor.TYPE_INT64,
            FieldDescriptor.TYPE_UINT32,
            FieldDescriptor.TYPE_UINT64,
            FieldDescriptor.TYPE_SINT32,
            FieldDescriptor.TYPE_SINT64,
            FieldDescriptor.TYPE_FIXED32,
            FieldDescriptor.TYPE_FIXED64,
            FieldDescriptor.TYPE_SFIXED32,
            FieldDescriptor.TYPE_SFIXED64,
            FieldDescriptor.TYPE_ENUM,
        ):
            return int
        if field_type == FieldDescriptor.TYPE_BOOL:
            return bool
        if field_type == FieldDescriptor.TYPE_STRING:
            return str
        if field_type == FieldDescriptor.TYPE_BYTES:
            return bytes
        if field_type == FieldDescriptor.TYPE_MESSAGE:
            return dict
        return float

    @classmethod
    def get_proto_desc(cls, target="Artemis", tables=None, rm_cols=None):
        desc = {}
        for group in cls._root_proto_message(target).DESCRIPTOR.fields:
            if group.type == FieldDescriptor.TYPE_MESSAGE:
                desc[group.name] = {field.name: (cls._proto_scalar_dtype(field.type), int(field.is_repeated))
                                    for field in group.message_type.fields}
        return cls._select_description(desc, tables, rm_cols)

    def get_random_data(self, dtype: type, size: Union[int, Tuple[int, int]], as_scalar=False, **kwargs):
        """
        Creates and returns array of (pseudo-)randomly generated number based on given data type.

        :param dtype:       class indicating desired output data type
        :param size:        int indicating number of values desired
        :param as_scalar:   bool if true and size is 1, return value as scalar instead of list
        :param kwargs:      min/low: int indicating minimum value for rng
                            max/high: int indicating maximum value for rng
                            endpoint: bool indicating whether to include high/max value (note: only works for floats)
                            length: int indicating number of sentences (note: only works for strings)
        :return:            array of randomly generated numbers
        """

        # Checks if low/min was passed by name, but not high/max which would cause low/min to be treated as high/max
        if ('low' in kwargs or 'min' in kwargs) and 'high' not in kwargs and 'max' not in kwargs:
            raise ValueError('min/low number specified, but max/high number was not.')

        # Checks if as_scalar matches size. If size > 1, a scalar cannot contain the values
        if as_scalar and size != 1:
            raise ValueError('as_scalar was set to true, but more than 1 value was expected to be returned.')

        # If bool or int, use default_rng.integers method with some argument manipulation
        if dtype is bool or np.issubdtype(dtype, np.integer):
            res = self.rng.integers(0, 2 if dtype is bool else 32767, size=size, dtype=int).tolist()
        # If float, use default_rng.random method with [0, 1) --> [low/min, high/max) transformation algorithm
        elif np.issubdtype(dtype, np.floating):
            # Algorithm used is (big - small) * random(0-1) + small
            res = ((kwargs.get('high', kwargs.get('max', 1)) - kwargs.get('low', kwargs.get('min', 0))) * \
                   self.rng.random(size, dtype) + kwargs.get('low', kwargs.get('min', 0))).tolist()
        # If str, bacon
        elif dtype is str:
            res = [requests.get('https://baconipsum.com/api/',
                                params={'type': 'meat-and-filler', 'sentences': kwargs.get('length', 10),
                                        'start-with-lorem': 1}).text[2:-2] for _ in range(size)]
        elif dtype is bytearray:
            res = secrets.token_bytes(16)
            
        else:
            logging.warning(f'Data type {dtype} not implemented yet.')

        return res[0] if as_scalar else list(res)

    def create_row(self, table_desc, packet):
        """Generate a typed row using the existing generic or coherent viewer profile."""
        row = {}
        stamp = time.time_ns()
        for column, (dtype, ndims) in table_desc.items():
            if column == "time":
                value = stamp // 1_000_000
            elif column == "sample_time_ns":
                value = stamp
            elif dtype is datetime.datetime:
                value = datetime.datetime.now()
            elif dtype is dict:
                value = {"fake_jsonb_data": self.get_random_data(int, 3)}
            elif dtype is bytes or dtype is bytearray:
                value = secrets.token_bytes(16)
            elif isinstance(dtype, str) and dtype.lower() == "point":
                if self.value_profile == "viewer":
                    story = self._viewer_story(packet)
                    value = (story["lat"], story["lon"])
                else:
                    value = [(30.289464, -97.735303), (30.389670, -97.728152)][self.rng.integers(2)]
            else:
                value = None
                if self.value_profile == "viewer":
                    if ndims == 0:
                        value = self._viewer_scalar_value(column, dtype, packet)
                    else:
                        value = self._viewer_vector_value(column, dtype, ndims, packet)
                if ndims == 1:
                    length = self.array_lengths.get(column, 3)
                    if value is None:
                        value = self.get_random_data(dtype, length)
                    elif value:
                        value = (value * math.ceil(length / len(value)))[:length]
                elif value is None:
                    if ndims == 0:
                        value = self.get_random_data(dtype, 1, as_scalar=True)
                    elif ndims == 2:
                        value = self.get_random_data(dtype, (3, 3))
                    else:
                        raise ValueError(f"Invalid number of dimensions: {ndims}")
            row[column] = value
        return row

    def send_proto_rows(self, tables, num_rows, delay, rm_cols=None, use_csv=False,
                        schema_source="proto", **kwargs):
        """Publish the requested number of synthetic or CSV rows as native Artemis batches."""
        target = kwargs.get("target", "Artemis")
        self._root_proto_message(target)
        if num_rows < 0 or delay < 0:
            raise ValueError("Row count and delay must be nonnegative")
        if schema_source == "proto":
            desc = self.get_proto_desc(target, tables, rm_cols)
        elif schema_source == "orm":
            desc = self.get_desc(db=not tables, tables=tables, rm_cols=rm_cols, target=target)
        else:
            raise ValueError("schema_source must be proto or orm")
        start = kwargs.get("start_packet_id", 1)
        sent = 0
        for index in tqdm(range(num_rows)):
            batch = self.create_proto_message(start + index, desc, use_csv, target)
            if batch.updates:
                result = self.mqtt.publish("artemis/data", batch.SerializeToString(), qos=1)
                if result is not None:
                    result.wait_for_publish(timeout=5)
                    if not result.is_published():
                        raise TimeoutError("MQTT publish did not complete")
                sent += 1
            time.sleep(delay)
        return sent

    def create_proto_message(self, packet, db_desc, use_csv=False, target="Artemis", sample_time_ns=None):
        """Build fresh per-pool observations; packet controls fixture values, never wire identity."""
        self._root_proto_message(target)
        csv_row = self.create_row_from_csv(packet) if use_csv else None
        stamp = csv_row["sample_time_ns"] if csv_row is not None else sample_time_ns or time.time_ns()
        batch = artemis_pb2.TelemetryBatch(format_version=1)
        updates = {}
        seen = set()
        for table, desc in db_desc.items():
            group = self.pools[table]["group"] if table in self.pools else table
            group_descriptor = artemis_pb2.ArtemisSensorData.DESCRIPTOR.fields_by_name[group]
            values = csv_row if csv_row is not None else self.create_row(desc, packet)
            for name in desc:
                field = group_descriptor.message_type.fields_by_name.get(name)
                if field is None or (group, name) in seen:
                    continue
                qualified = f"{group}.{name}"
                if csv_row is not None and qualified not in csv_row["updated_fields"]:
                    continue
                value = values[qualified] if csv_row is not None else values[name]
                pool = self.pool_by_field[group, name]
                if pool not in updates:
                    update = batch.updates.add(sample_time_ns=stamp)
                    update.values.time = stamp // 1_000_000
                    updates[pool] = update
                update = updates[pool]
                destination = getattr(update.values, group)
                destination.SetInParent()
                if field.is_repeated:
                    getattr(destination, name).extend(self._coerce(item, field) for item in value)
                else:
                    setattr(destination, name, self._coerce(value, field))
                update.updated_fields.add(group=group_descriptor.number, field=field.number)
                seen.add((group, name))
        return batch

    def native_update(self, pool, stamp, elapsed, seed):
        desc = {pool["table"]: self.pool_descriptions[pool["table"]]}
        return self.create_proto_message(int(elapsed / .02) + 1, desc, sample_time_ns=stamp).updates[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--car", choices=["Artemis"], default="Artemis")
    parser.add_argument("--profile", choices=["viewer", "generic"], default="viewer")
    parser.add_argument("--schema-source", choices=["proto", "orm"], default="proto")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--rows", type=int, help="Number of fixture rows to publish (default: 500000).")
    mode.add_argument("--duration", type=float, help="Publish independent frequency pools for this many seconds.")
    parser.add_argument("--delay", type=float, help="Delay between fixture rows; defaults to 0.02 s (viewer) or 0.001 s (generic).")
    parser.add_argument("--csv", type=Path, help="Artemis logger CSV or directory of pool CSVs.")
    parser.add_argument("--mapping", type=Path, help="Optional qualified-field-to-CSV-column JSON mapping.")
    parser.add_argument("--tables", nargs="+", help="Subsystem groups or generated pool table names.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--rate-scale", type=float, default=1)
    parser.add_argument("--mqtt-host", default=os.getenv("MQTT_HOST", "localhost"))
    parser.add_argument("--mqtt-port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
    parser.add_argument("--contract", help="Override the generated storage contract path.")
    args = parser.parse_args(argv)
    if args.contract:
        os.environ["ARTEMIS_STORAGE_CONTRACT"] = args.contract
    if args.mapping and not args.csv:
        parser.error("--mapping requires --csv")
    if args.duration is None and args.rate_scale != 1:
        parser.error("--rate-scale requires --duration")
    if args.duration is not None and (args.csv or args.tables or args.delay is not None or args.schema_source != "proto"):
        parser.error("--duration uses generated pool rates; CSV, tables, delay and ORM selection use --rows")
    tester = DataTester(None, args.seed, args.csv, args.mapping, args.profile)
    if args.duration is not None:
        result = publish(args.duration, args.rate_scale, args.seed, args.mqtt_host,
                         args.mqtt_port, update_factory=tester.native_update)
    else:
        delay = args.delay if args.delay is not None else .02 if args.profile == "viewer" else .001
        rows = args.rows if args.rows is not None else 500000
        if rows < 0 or delay < 0:
            parser.error("--rows and --delay must be nonnegative")
        client = mqtt_client.Client(client_id="paho-test-" + str(os.getpid()))
        client.connect(args.mqtt_host, args.mqtt_port)
        client.loop_start()
        try:
            tester.mqtt = client
            sent = tester.send_proto_rows(args.tables, rows, delay, use_csv=args.csv is not None,
                                          schema_source=args.schema_source, target=args.car)
            result = {"rows": sent, "profile": args.profile, "schema_source": args.schema_source}
        finally:
            client.disconnect()
            client.loop_stop()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
