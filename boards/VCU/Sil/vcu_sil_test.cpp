// The C ABI as a co-simulation host sees it: this test links only the
// shared library and its header, never the app.

#include "Sil/vcu_sil.h"

#include <dlfcn.h>
#include <gtest/gtest.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <map>
#include <string>
#include <vector>

namespace {

// lhre VcuStateMachine and HvcState values.
constexpr int32_t kRtdWait = 6;
constexpr int32_t kDrive = 2;
constexpr int32_t kEnergized = 2;

vcu_sil_inputs ParkedCar() {
  vcu_sil_inputs in{};
  in.struct_size = sizeof(in);
  in.hvc_state = kEnergized;
  in.contactors_closed = 1;
  in.shutdown_loop_v = 24.0;
  in.brake_front_psi = 300.0;
  in.brake_rear_psi = 300.0;
  in.dc_bus_voltage_v = 574.0;
  in.min_cell_v = 4.1;
  in.max_cell_v = 4.1;
  return in;
}

vcu_sil_outputs Outputs() {
  vcu_sil_outputs out{};
  out.struct_size = sizeof(out);
  return out;
}

class Sil : public ::testing::Test {
 protected:
  void SetUp() override {
    vcu_sil_config config{};
    config.struct_size = sizeof(config);
    config.event_mode = 4;  // endurance
    ASSERT_EQ(vcu_sil_create(&config, &handle_), VCU_SIL_OK);
    ASSERT_NE(handle_, nullptr);
  }
  void TearDown() override { vcu_sil_destroy(handle_); }

  vcu_sil_outputs Hold(const vcu_sil_inputs& in, uint32_t ms) {
    vcu_sil_outputs out = Outputs();
    for (uint32_t t = 0; t < ms; t += 3) {
      EXPECT_EQ(vcu_sil_advance(handle_, &in, 3, &out), VCU_SIL_OK);
    }
    return out;
  }

  std::string ReadConsole(uint32_t chunk) {
    std::string text;
    std::vector<char> buffer(chunk);
    uint32_t length = 0;
    do {
      EXPECT_EQ(vcu_sil_read_console(handle_, buffer.data(), chunk, &length),
                VCU_SIL_OK);
      EXPECT_LE(length, chunk);
      text.append(buffer.data(), length);
    } while (length > 0);
    return text;
  }

  vcu_sil_handle* handle_ = nullptr;
};

TEST(SilAbi, VersionMatchesTheHeader) {
  EXPECT_EQ(vcu_sil_abi_version(), VCU_SIL_ABI_VERSION);
}

// The table must describe every field, so a binding that mirrors it field
// by field cannot silently miss one hiding in what looks like padding.
TEST(SilAbi, LayoutTableCoversEveryStructExactly) {
  std::map<std::string, uint32_t> sizes;
  std::map<std::string, std::vector<vcu_sil_field>> fields;
  vcu_sil_field f{};
  uint32_t i = 0;
  for (; vcu_sil_layout(i, &f) == VCU_SIL_OK; ++i) {
    ASSERT_NE(f.structure, nullptr);
    ASSERT_NE(f.field, nullptr);
    if (f.field[0] == '\0') {
      sizes[f.structure] = f.size;
    } else {
      fields[f.structure].push_back(f);
    }
  }
  EXPECT_EQ(vcu_sil_layout(i, &f), VCU_SIL_ERR_RANGE);
  EXPECT_EQ(vcu_sil_layout(0, nullptr), VCU_SIL_ERR_NULL);
  EXPECT_EQ(sizes["config"], sizeof(vcu_sil_config));
  EXPECT_EQ(sizes["inputs"], sizeof(vcu_sil_inputs));
  EXPECT_EQ(sizes["outputs"], sizeof(vcu_sil_outputs));
  EXPECT_EQ(fields["config"].size(), 2u);
  EXPECT_EQ(fields["inputs"].size(), 16u);
  EXPECT_EQ(fields["outputs"].size(), 38u);
  for (auto& [name, list] : fields) {
    std::sort(list.begin(), list.end(),
              [](const vcu_sil_field& a, const vcu_sil_field& b) {
                return a.offset < b.offset;
              });
    uint32_t end = 0;
    for (const vcu_sil_field& field : list) {
      // Every member is a 4-byte integer or an 8-byte double (or array of
      // them), naturally aligned: any other gap is a missing field.
      const uint32_t align = field.size % 8 == 0 ? 8 : 4;
      const uint32_t expected = (end + align - 1) / align * align;
      EXPECT_EQ(field.offset, expected) << name << "." << field.field;
      end = field.offset + field.size;
    }
    EXPECT_EQ((end + 7) / 8 * 8, (sizes[name] + 7) / 8 * 8) << name;
    EXPECT_LE(end, sizes[name]) << name;
  }
}

TEST(SilAbi, CreateRejectsBadConfigs) {
  vcu_sil_handle* sentinel = reinterpret_cast<vcu_sil_handle*>(0x1);
  vcu_sil_handle* out = sentinel;
  vcu_sil_config config{};
  config.struct_size = sizeof(config);
  EXPECT_EQ(vcu_sil_create(nullptr, &out), VCU_SIL_ERR_NULL);
  EXPECT_EQ(vcu_sil_create(&config, nullptr), VCU_SIL_ERR_NULL);
  config.struct_size = sizeof(config) + 4;
  EXPECT_EQ(vcu_sil_create(&config, &out), VCU_SIL_ERR_SIZE);
  config.struct_size = sizeof(config);
  for (const int32_t mode : {-1, 5, 255}) {
    config.event_mode = mode;
    EXPECT_EQ(vcu_sil_create(&config, &out), VCU_SIL_ERR_RANGE) << mode;
  }
  EXPECT_EQ(out, sentinel);
  vcu_sil_destroy(nullptr);
}

// Mode 0 boots like the car (default event, ENDURANCE = 4); 1..4 pick that
// event's parameter set.
TEST(SilAbi, EventModeZeroBootsLikeTheCar) {
  for (const auto& [mode, reported] :
       std::vector<std::pair<int32_t, int32_t>>{{0, 4}, {1, 1}, {4, 4}}) {
    vcu_sil_config config{};
    config.struct_size = sizeof(config);
    config.event_mode = mode;
    vcu_sil_handle* handle = nullptr;
    ASSERT_EQ(vcu_sil_create(&config, &handle), VCU_SIL_OK);
    const vcu_sil_inputs in = ParkedCar();
    vcu_sil_outputs out = Outputs();
    ASSERT_EQ(vcu_sil_advance(handle, &in, 3, &out), VCU_SIL_OK);
    EXPECT_EQ(out.event_mode, reported) << mode;
    vcu_sil_destroy(handle);
  }
}

TEST(SilAbi, BuildInfoIsStamped) {
  ASSERT_NE(vcu_sil_build_git_sha(), nullptr);
  ASSERT_NE(vcu_sil_build_git_describe(), nullptr);
  EXPECT_GT(std::strlen(vcu_sil_build_git_sha()), 0u);
  const int32_t dirty = vcu_sil_build_dirty();
  EXPECT_TRUE(dirty == 0 || dirty == 1);
}

TEST_F(Sil, AdvanceRejectsBadCallsAndChangesNothing) {
  const vcu_sil_inputs good = ParkedCar();
  vcu_sil_outputs out = Outputs();
  out.now_ms = 12345;
  const vcu_sil_outputs before = out;

  auto rejects = [&](const vcu_sil_inputs& in, uint32_t ms, int32_t error) {
    EXPECT_EQ(vcu_sil_advance(handle_, &in, ms, &out), error);
    EXPECT_EQ(std::memcmp(&out, &before, sizeof(out)), 0);
  };
  EXPECT_EQ(vcu_sil_advance(nullptr, &good, 3, &out), VCU_SIL_ERR_NULL);
  EXPECT_EQ(vcu_sil_advance(handle_, nullptr, 3, &out), VCU_SIL_ERR_NULL);
  EXPECT_EQ(vcu_sil_advance(handle_, &good, 3, nullptr), VCU_SIL_ERR_NULL);
  rejects(good, 0, VCU_SIL_ERR_RANGE);
  rejects(good, 1001, VCU_SIL_ERR_RANGE);

  vcu_sil_inputs in = good;
  in.struct_size -= 8;
  rejects(in, 3, VCU_SIL_ERR_SIZE);
  vcu_sil_outputs wrong = Outputs();
  wrong.struct_size += 8;
  EXPECT_EQ(vcu_sil_advance(handle_, &good, 3, &wrong), VCU_SIL_ERR_SIZE);

  double* doubles[] = {
      &in.accelerator_travel,   &in.brake_front_psi,
      &in.brake_rear_psi,       &in.steering_angle_deg,
      &in.shutdown_loop_v,      &in.wheel_speed_rad_s[0],
      &in.wheel_speed_rad_s[1], &in.wheel_speed_rad_s[2],
      &in.wheel_speed_rad_s[3], &in.motor_speed_rad_s,
      &in.motor_torque_nm,      &in.dc_bus_voltage_v,
      &in.dc_bus_current_a,     &in.min_cell_v,
      &in.max_cell_v,
  };
  for (double* d : doubles) {
    for (const double bad : {NAN, INFINITY, -INFINITY}) {
      in = good;
      *d = bad;
      rejects(in, 3, VCU_SIL_ERR_NON_FINITE);
    }
  }

  const std::pair<double vcu_sil_inputs::*, double> ranges[] = {
      {&vcu_sil_inputs::accelerator_travel, -0.01},
      {&vcu_sil_inputs::accelerator_travel, 1.01},
      {&vcu_sil_inputs::brake_front_psi, -1.0},
      {&vcu_sil_inputs::brake_front_psi, 3000.5},
      {&vcu_sil_inputs::brake_rear_psi, -1.0},
      {&vcu_sil_inputs::brake_rear_psi, 3000.5},
      {&vcu_sil_inputs::shutdown_loop_v, -0.1},
      {&vcu_sil_inputs::shutdown_loop_v, 27.1},
  };
  for (const auto& [field, value] : ranges) {
    in = good;
    in.*field = value;
    rejects(in, 3, VCU_SIL_ERR_RANGE);
  }
  for (const int32_t hvc : {-1, 5}) {
    in = good;
    in.hvc_state = hvc;
    rejects(in, 3, VCU_SIL_ERR_RANGE);
  }

  // None of the rejected calls moved simulated time.
  ASSERT_EQ(vcu_sil_advance(handle_, &good, 3, &out), VCU_SIL_OK);
  EXPECT_EQ(out.now_ms, 3u);
}

TEST_F(Sil, ReachesDriveAndCommandsTorque) {
  vcu_sil_inputs in = ParkedCar();
  vcu_sil_outputs out = Hold(in, 300);
  EXPECT_EQ(out.state, kRtdWait);
  EXPECT_STREQ(vcu_sil_state_name(out.state), "RTD_WAIT");
  EXPECT_EQ(out.inverter_enable, 0);
  in.rtd_pressed = 1;
  Hold(in, 60);
  // Off the brake first: its filter takes tens of ms to fall below the
  // brake-over-throttle threshold.
  in.brake_front_psi = in.brake_rear_psi = 0.0;
  Hold(in, 60);
  in.accelerator_travel = 1.0;
  in.motor_speed_rad_s = 300.0;
  out = Hold(in, 240);
  EXPECT_EQ(out.state, kDrive);
  EXPECT_EQ(out.faults & ((1u << 20) | (1u << 10) | (1u << 9)), 0u);
  EXPECT_EQ(out.torque_frames, 1u);
  EXPECT_EQ(out.inverter_enable, 1);
  EXPECT_EQ(out.direction, 1);
  EXPECT_GT(out.torque_request_nm, 150.0);
  // The frame carries the command rounded half away from zero to 0.1 N.m,
  // exactly as the generated setter encodes it.
  const float scaled = static_cast<float>(out.torque_command_nm) / 0.1f;
  const int16_t raw =
      static_cast<int16_t>(scaled >= 0.0f ? scaled + 0.5f : scaled - 0.5f);
  EXPECT_EQ(out.torque_request_nm, static_cast<float>(raw) * 0.1f);
  EXPECT_DOUBLE_EQ(out.torque_limit_nm, 230.0);
  EXPECT_EQ(out.now_ms, 660u);
  EXPECT_EQ(out.control_ms, 657u);
  EXPECT_EQ(out.motor_rpm, 2865.0);
  EXPECT_EQ(out.event_mode, 4);
  EXPECT_EQ(out.saturated, 0u);

  in.wheel_speed_rad_s[1] = 400.0;
  out = Hold(in, 3);
  EXPECT_EQ(out.saturated, static_cast<uint32_t>(VCU_SIL_SAT_WHEEL_SPEED));
}

TEST_F(Sil, ConsoleReadsInChunksUntilEmpty) {
  vcu_sil_inputs in = ParkedCar();
  Hold(in, 300);
  in.rtd_pressed = 1;
  Hold(in, 60);
  const std::string text = ReadConsole(7);
  EXPECT_NE(text.find("-> RTD_WAIT"), std::string::npos) << text;
  EXPECT_NE(text.find("-> DRIVE"), std::string::npos) << text;
  EXPECT_TRUE(ReadConsole(64).empty());
  uint32_t length = 99;
  EXPECT_EQ(vcu_sil_read_console(handle_, nullptr, 0, &length), VCU_SIL_OK);
  EXPECT_EQ(length, 0u);
  char c = 0;
  EXPECT_EQ(vcu_sil_read_console(handle_, nullptr, 1, &length),
            VCU_SIL_ERR_NULL);
  EXPECT_EQ(vcu_sil_read_console(handle_, &c, 1, nullptr), VCU_SIL_ERR_NULL);
  EXPECT_EQ(vcu_sil_read_console(nullptr, &c, 1, &length), VCU_SIL_ERR_NULL);
}

// Each driver input reaches its own sensor, and each internal comes back in
// its own output field: front and rear differ on purpose.
TEST_F(Sil, DriverInputsComeBackFieldByField) {
  vcu_sil_inputs in = ParkedCar();
  in.brake_front_psi = 100.0;
  in.brake_rear_psi = 200.0;
  in.accelerator_travel = 0.5;
  in.steering_angle_deg = 45.0;
  const vcu_sil_outputs out = Hold(in, 60);
  EXPECT_NEAR(out.bse1_psi, 100.0, 1.0);
  EXPECT_NEAR(out.bse2_psi, 200.0, 1.0);
  EXPECT_NEAR(out.bse3_psi, 200.0, 0.05);
  EXPECT_NEAR(out.apps1_travel, 0.5, 1e-3);
  EXPECT_NEAR(out.apps2_travel, 0.5, 1e-3);
  EXPECT_NEAR(out.steering_angle_deg, 45.0, 0.15);
  EXPECT_EQ(out.hard_braking, 0);
  in.brake_front_psi = in.brake_rear_psi = 1500.0;
  EXPECT_EQ(Hold(in, 30).hard_braking, 1);
}

TEST_F(Sil, AcceptsEveryHvcState) {
  for (int32_t hvc = 0; hvc <= 4; ++hvc) {
    vcu_sil_inputs in = ParkedCar();
    in.hvc_state = hvc;
    vcu_sil_outputs out = Outputs();
    EXPECT_EQ(vcu_sil_advance(handle_, &in, 3, &out), VCU_SIL_OK) << hvc;
  }
}

TEST(SilAbi, NamesComeFromTheSpec) {
  EXPECT_STREQ(vcu_sil_state_name(kDrive), "DRIVE");
  EXPECT_STREQ(vcu_sil_state_name(7), "BSPD_TRIPPED");
  EXPECT_STREQ(vcu_sil_state_name(8), "?");
  EXPECT_STREQ(vcu_sil_state_name(-1), "?");
  EXPECT_STREQ(vcu_sil_fault_name(0), "NONE");
  EXPECT_STREQ(vcu_sil_fault_name(1), "APPS_IMPLAUSIBLE");
  EXPECT_STREQ(vcu_sil_fault_name(1000), "?");
  EXPECT_STREQ(vcu_sil_error_name(VCU_SIL_OK), "OK");
  EXPECT_STREQ(vcu_sil_error_name(VCU_SIL_ERR_RANGE), "ERR_RANGE");
  EXPECT_STREQ(vcu_sil_error_name(42), "?");
}

// The app, FreeRTOS and the C++ runtime must not leak into the process that
// also loads the car model.
TEST(SilAbi, OnlyTheAbiIsExported) {
  EXPECT_NE(dlsym(RTLD_DEFAULT, "vcu_sil_advance"), nullptr);
  EXPECT_NE(dlsym(RTLD_DEFAULT, "vcu_sil_layout"), nullptr);
  for (const char* hidden :
       {"_ZN3vcu3App4StepEv", "vTaskDelay", "xQueueGenericSend",
        "_ZN3vcu9ParamsForEN4lhre3can9EventModeE"}) {
    EXPECT_EQ(dlsym(RTLD_DEFAULT, hidden), nullptr) << hidden;
  }
}

}  // namespace
