"""Wire routing for the two supported cars; no database service is required."""
import unittest

from stack.ingest.sensor_decode import CAR_MESSAGES, car_from_headers, decode_sensor


class TestProtobufSerialization(unittest.TestCase):
    def test_supported_car_identities_are_explicit(self):
        self.assertEqual(set(CAR_MESSAGES), {"Orion", "Artemis"})
        for car in CAR_MESSAGES:
            self.assertEqual(car_from_headers([("car_type", car.encode())]), car)
        for retired in ("Angelique", "Nightwatch"):
            with self.assertRaises(ValueError):
                car_from_headers([("car_type", retired.encode())])

    def test_each_wire_type_round_trips_zero_false_and_arrays(self):
        for car, message_type in CAR_MESSAGES.items():
            with self.subTest(car=car):
                message = message_type(time=1000, packet_id=42)
                message.controls.apps1_v = 0
                message.controls.enable = False
                message.dynamics.gps_precise.extend([30.123456789, -97.123456789])
                result = decode_sensor(message.SerializeToString(), car)
                self.assertEqual(int(result["time"]), 1000)
                self.assertEqual(result["controls"]["apps1_v"], 0)
                self.assertFalse(result["controls"]["enable"])
                self.assertEqual(result["dynamics"]["gps_precise"], [30.123456789, -97.123456789])
                self.assertEqual(message.DESCRIPTOR.full_name, f"{car.lower()}.{car}SensorData")

    def test_only_orion_requires_packet_identity(self):
        for car, message_type in CAR_MESSAGES.items():
            message = message_type(time=1000)
            if car == "Orion":
                with self.assertRaises(ValueError):
                    decode_sensor(message.SerializeToString(), car)
            else:
                self.assertEqual(int(decode_sensor(message.SerializeToString(), car)["time"]), 1000)

    def test_invalid_time_is_rejected_for_both_cars(self):
        for car, message_type in CAR_MESSAGES.items():
            with self.assertRaises(ValueError):
                decode_sensor(message_type(packet_id=42).SerializeToString(), car)


if __name__ == "__main__":
    unittest.main()
