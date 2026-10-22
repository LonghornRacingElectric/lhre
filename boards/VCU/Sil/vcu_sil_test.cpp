// Tests for the VCU SIL C ABI. Linked against libvcu_sil.so itself, so the
// exported symbol set the Modelica wrapper loads is under test too.

#include "vcu_sil.h"

#include <gtest/gtest.h>

#include <cmath>
#include <limits>

namespace {

struct PlantState {
  double accel_pedal = 0.0;
  double brake_pedal = 0.0;
  double motor_speed_rad_s = 0.0;
  double pack_voltage_v = 540.0;
  double pack_current_a = 0.0;
};

struct VcuCommand {
  double motor_torque_nm = std::numeric_limits<double>::quiet_NaN();
  double regen_limit_nm = std::numeric_limits<double>::quiet_NaN();
};

int StepAt(void* handle, double time_s, const PlantState& plant,
           VcuCommand* command) {
  return vcu_sil_step(handle, time_s, plant.accel_pedal, plant.brake_pedal,
                      plant.motor_speed_rad_s, plant.pack_voltage_v,
                      plant.pack_current_a, &command->motor_torque_nm,
                      &command->regen_limit_nm);
}

TEST(VcuSil, CreateStepDestroyRoundTrip) {
  void* handle = vcu_sil_create();
  ASSERT_NE(handle, nullptr);

  VcuCommand command;
  EXPECT_EQ(StepAt(handle, 0.0, PlantState{}, &command), VCU_SIL_OK);
  EXPECT_TRUE(std::isfinite(command.motor_torque_nm));
  EXPECT_TRUE(std::isfinite(command.regen_limit_nm));

  vcu_sil_destroy(handle);
}

// Pins today's firmware: the App has no pedal path and broadcasts zero
// torque. Update this when the control law lands in boards/VCU/App.
TEST(VcuSil, CommandsZeroTorqueWhileAppHasNoControlLaw) {
  void* handle = vcu_sil_create();
  ASSERT_NE(handle, nullptr);

  for (double accel_pedal : {0.0, 0.5, 1.0}) {
    PlantState plant;
    plant.accel_pedal = accel_pedal;
    plant.motor_speed_rad_s = 300.0;
    for (int tick = 0; tick < 100; ++tick) {
      const double time_s = accel_pedal * 10.0 + tick * 0.01;
      VcuCommand command;
      ASSERT_EQ(StepAt(handle, time_s, plant, &command), VCU_SIL_OK);
      EXPECT_EQ(command.motor_torque_nm, 0.0);
      EXPECT_EQ(command.regen_limit_nm, 0.0);
    }
  }

  vcu_sil_destroy(handle);
}

TEST(VcuSil, IdenticalInputsGiveIdenticalOutputs) {
  void* first = vcu_sil_create();
  void* second = vcu_sil_create();
  ASSERT_NE(first, nullptr);
  ASSERT_NE(second, nullptr);

  for (int tick = 0; tick < 500; ++tick) {
    PlantState plant;
    plant.accel_pedal = (tick % 50) / 50.0;
    plant.pack_current_a = tick * 0.3;
    VcuCommand from_first;
    VcuCommand from_second;
    ASSERT_EQ(StepAt(first, tick * 0.003, plant, &from_first), VCU_SIL_OK);
    ASSERT_EQ(StepAt(second, tick * 0.003, plant, &from_second), VCU_SIL_OK);
    EXPECT_EQ(from_first.motor_torque_nm, from_second.motor_torque_nm);
    EXPECT_EQ(from_first.regen_limit_nm, from_second.regen_limit_nm);
  }

  vcu_sil_destroy(first);
  vcu_sil_destroy(second);
}

TEST(VcuSil, RejectsTimeGoingBackwards) {
  void* handle = vcu_sil_create();
  ASSERT_NE(handle, nullptr);

  VcuCommand command;
  ASSERT_EQ(StepAt(handle, 1.0, PlantState{}, &command), VCU_SIL_OK);
  EXPECT_EQ(StepAt(handle, 0.5, PlantState{}, &command),
            VCU_SIL_ERR_TIME_WENT_BACKWARDS);
  EXPECT_EQ(StepAt(handle, -1.0, PlantState{}, &command),
            VCU_SIL_ERR_TIME_WENT_BACKWARDS);

  vcu_sil_destroy(handle);
}

TEST(VcuSil, RejectsNullArguments) {
  void* handle = vcu_sil_create();
  ASSERT_NE(handle, nullptr);

  double torque_nm = 0.0;
  double regen_nm = 0.0;
  EXPECT_EQ(vcu_sil_step(nullptr, 0.0, 0, 0, 0, 540, 0, &torque_nm, &regen_nm),
            VCU_SIL_ERR_NULL_ARGUMENT);
  EXPECT_EQ(vcu_sil_step(handle, 0.0, 0, 0, 0, 540, 0, nullptr, &regen_nm),
            VCU_SIL_ERR_NULL_ARGUMENT);
  EXPECT_EQ(vcu_sil_step(handle, 0.0, 0, 0, 0, 540, 0, &torque_nm, nullptr),
            VCU_SIL_ERR_NULL_ARGUMENT);

  vcu_sil_destroy(handle);
  vcu_sil_destroy(nullptr);  // no-op, like free()
}

TEST(VcuSil, RejectsNonFiniteInputs) {
  void* handle = vcu_sil_create();
  ASSERT_NE(handle, nullptr);

  PlantState plant;
  plant.pack_voltage_v = std::numeric_limits<double>::quiet_NaN();
  VcuCommand command;
  EXPECT_EQ(StepAt(handle, 0.0, plant, &command),
            VCU_SIL_ERR_NON_FINITE_INPUT);

  vcu_sil_destroy(handle);
}

}  // namespace
