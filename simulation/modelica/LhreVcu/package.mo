package LhreVcu
  class VcuSil
    extends ExternalObject;

    function constructor
      output VcuSil sil;
      external "C" sil = vcu_sil_create()
        annotation(Library = "vcu_sil", LibraryDirectory = "modelica://LhreVcu/Resources/Library");
    end constructor;

    function destructor
      input VcuSil sil;
      external "C" vcu_sil_destroy(sil)
        annotation(Library = "vcu_sil", LibraryDirectory = "modelica://LhreVcu/Resources/Library");
    end destructor;
  end VcuSil;

  impure function vcuSilStep
    input VcuSil sil;
    input Real time_s;
    input Real accelPedal;
    input Real brakePedal;
    input Real motorSpeed;
    input Real packVoltage;
    input Real packCurrent;
    output Real motorTorque;
    output Real regenLimit;
    output Integer rc;
    external "C" rc = vcu_sil_step(sil, time_s, accelPedal, brakePedal, motorSpeed, packVoltage, packCurrent, motorTorque, regenLimit)
      annotation(Library = "vcu_sil", LibraryDirectory = "modelica://LhreVcu/Resources/Library");
  end vcuSilStep;

  model FwVCU
    extends BobLib.Controllers.StandardVCU(
      motorTorqueRequest(y = tau_fw),
      regenLimitRequest(y = regen_fw));

    parameter Modelica.Units.SI.Time Ts = 0.1;
    parameter VcuSil sil = VcuSil();
    discrete output Modelica.Units.SI.Torque tau_fw(start = 0, fixed = true);
    discrete output Modelica.Units.SI.Torque regen_fw(start = 0, fixed = true);
  protected
    discrete Integer rc(start = 0, fixed = true);
  algorithm
    when sample(0, Ts) then
      (tau_fw, regen_fw, rc) := vcuSilStep(sil, time, acceleratorPedalCmd, brakePedalCmd,
        motorSpeedSign*sens_motor_speed, sens_hv_bus_voltage, sens_hv_bus_current);
      assert(rc == 0, "vcu_sil_step returned " + String(rc) + ". See boards/VCU/Sil/README.md for the codes.");
    end when;
  end FwVCU;

  model VehicleSimFw
    extends BobLib.Experiments.Standards.VehicleSim(
      redeclare LhreVcu.FwVCU vcu(
        tau_max = pVehicle.pVCU.tau_max,
        P_max_mot = pVehicle.pVCU.P_max_mot,
        w_eps = pVehicle.pVCU.w_eps,
        motorSpeedSign = pVehicle.pVCU.motorSpeedSign,
        finalDriveRatio = pVehicle.pDriveline.finalDriveRatio,
        targetVel = initialVel,
        regenTorqueLimit = pVehicle.pVCU.regenTorqueLimit,
        mechanicalBrakeTorqueLimit = pVehicle.pVCU.mechanicalBrakeTorqueLimit,
        regenBrakeBlend = pVehicle.pVCU.regenBrakeBlend));
  end VehicleSimFw;
end LhreVcu;
