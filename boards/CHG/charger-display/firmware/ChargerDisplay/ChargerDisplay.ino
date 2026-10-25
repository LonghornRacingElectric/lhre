// FSAE charger display. Charger authority always resides in the STM32.
#include <Arduino.h>
#include <ChargerLink.h>
#include <Preferences.h>
#include <TFT_eSPI.h>
#include <esp_system.h>
#include <math.h>
#include "PanelHAL.h"
#ifndef CHARGER_DEMO
#define CHARGER_DEMO 0
#endif
#ifndef CHARGER_ENABLE_CONTROLS
#define CHARGER_ENABLE_CONTROLS 0
#endif
static_assert(!(CHARGER_DEMO && CHARGER_ENABLE_CONTROLS),
              "Demo cannot enable control");
TFT_eSPI tft;
TFT_eSprite tile(&tft);
PanelHAL panel;
Preferences preferences;
HardwareSerial controller(2);
void uartWrite(const uint8_t* data, size_t size, void*) {
#if !CHARGER_DEMO
  controller.write(data, size);
#endif
}
charger::Link chargerLink(uartWrite);
charger::Telemetry demo;
uint8_t demoScenario = 0;
const uint16_t BG = 0x0862, PANEL = 0x10E5, EDGE = 0x21A8, WHITE = 0xF7BF,
               MUTED = 0x9D57, GREEN = 0x4F16, AMBER = 0xFE8E, RED = 0xFBB0,
               BLUE = 0x6DFF;
int tileY = 0, page = 0;
uint32_t lastPaint = 0, lastTouch = 0, holdSince = 0, lastLog = 0,
         lastFrameMs = 0;
bool wasTouch = false, calibrating = false;
int holdAction = 0;
bool haveCalibration = false;
struct TouchCalibration {
  float ax, bx, cx, ay, by, cy;
};
TouchCalibration calibration;
const char* notice = "";
uint32_t noticeUntil = 0;

void textAt(const String& s, int x, int y, int font = 2,
            uint16_t color = WHITE) {
  tile.setTextColor(color);
  tile.drawString(s, x, y - tileY, font);
}
void rightAt(const String& s, int x, int y, int font = 2,
             uint16_t color = WHITE) {
  tile.setTextColor(color);
  tile.drawRightString(s, x, y - tileY, font);
}
void rect(int x, int y, int w, int h, uint16_t c) {
  tile.fillRect(x, y - tileY, w, h, c);
}
void line(int x1, int y1, int x2, int y2, uint16_t c) {
  tile.drawLine(x1, y1 - tileY, x2, y2 - tileY, c);
}
void card(int x, int y, int w, int h) {
  tile.fillRoundRect(x, y - tileY, w, h, 9, PANEL);
  tile.drawRoundRect(x, y - tileY, w, h, 9, EDGE);
}
String decimal(float f, int n = 1) { return String(f, n); }
String percent(uint16_t v) {
  return v == charger::kUnknownPercent ? "--" : decimal(v / 10.0f, 0);
}
String eta(uint32_t seconds) {
  if (seconds == charger::kUnknownEta) return "--";
  if (seconds >= 3600)
    return String(seconds / 3600) + "h " + String((seconds % 3600) / 60) + "m";
  return String((seconds + 59) / 60) + " min";
}
bool fresh(uint32_t now) {
  return CHARGER_DEMO ? demoScenario != 5 : chargerLink.connected(now);
}
const charger::Telemetry& telemetry() {
  return CHARGER_DEMO ? demo : chargerLink.model().telemetry;
}
const char* stateName(const charger::Telemetry& t, bool valid) {
  if (!valid) return "LINK LOST / NO DATA";
  if (t.faultBits || t.state == charger::Fault) return "CHARGE FAULT";
  switch (t.state) {
    case charger::Ready:
      return "READY TO CHARGE";
    case charger::Charging:
      return "CHARGING";
    case charger::Complete:
      return "CHARGE COMPLETE";
    case charger::Checking:
      return "RUNNING CHECKS";
    case charger::Stopping:
      return "STOPPING";
    default:
      return "STANDBY";
  }
}
uint16_t stateColor(const charger::Telemetry& t, bool valid) {
  if (!valid || t.faultBits || t.state == charger::Fault) return RED;
  return t.state == charger::Charging || t.state == charger::Complete ||
                 t.state == charger::Ready
             ? GREEN
             : AMBER;
}
void metric(const char* label, const String& value, const char* unit, int x) {
  card(x, 48, 144, 68);
  textAt(label, x + 11, 55, 2, MUTED);
  textAt(value, x + 11, 75, 4);
  rightAt(unit, x + 133, 91, 2, MUTED);
}
void header(uint32_t now) {
  textAt("LHRE", 16, 9, 4);
  textAt("EV / CHARGER", 91, 14, 2, MUTED);
  rightAt(CHARGER_DEMO ? "DEMO  /  NO OUTPUT" : "STM32  /  UART", 464, 6, 2,
          CHARGER_DEMO ? AMBER : MUTED);
  rightAt(fresh(now) ? (CHARGER_DEMO ? "SIMULATED DATA" : "LIVE TELEMETRY")
                     : "AWAITING CONTROLLER",
          464, 23, 1, fresh(now) ? GREEN : RED);
  line(16, 39, 464, 39, EDGE);
}
void overview(uint32_t now) {
  const auto& t = telemetry();
  bool ok = fresh(now);
  uint16_t c = stateColor(t, ok);
  metric("PACK VOLTAGE", ok ? decimal(t.packMv / 1000.0f) : "--", "V", 16);
  metric("CHARGE CURRENT", ok ? decimal(t.currentMa / 1000.0f) : "--", "A",
         168);
  metric("CHARGE POWER", ok ? decimal(t.powerW / 1000.0f, 2) : "--", "kW", 320);
  card(16, 125, 250, 107);
  textAt("STATE OF CHARGE", 28, 132, 2, MUTED);
  textAt(ok ? percent(t.socPermille) + "%" : "--", 28, 152, 4,
         ok ? WHITE : MUTED);
  // Battery segments and moving energy markers only use fresh charging status.
  tile.drawRoundRect(120, 155 - tileY, 126, 45, 5, EDGE);
  rect(246, 168, 5, 19, EDGE);
  int segments =
      ok && t.socPermille != charger::kUnknownPercent ? t.socPermille / 100 : 0;
  for (int i = 0; i < 10; i++)
    rect(126 + i * 11, 161, 8, 33, i < segments ? GREEN : BG);
  textAt(ok && t.targetSocPermille != charger::kUnknownPercent
             ? "Target " + percent(t.targetSocPermille) + "%"
             : "Target --",
         28, 207, 2, MUTED);
  if (ok && t.state == charger::Charging && !t.faultBits) {
    for (int i = 0; i < 3; i++) {
      int x = 145 + (int(now / 55) + i * 29) % 90;
      line(x, 213, x + 4, 209, GREEN);
      line(x, 213, x - 4, 209, GREEN);
    }
  }
  card(274, 125, 190, 50);
  textAt("TIME TO TARGET", 286, 131, 2, MUTED);
  textAt(ok ? eta(t.etaSeconds) : "--", 286, 149, 4);
  card(274, 183, 190, 49);
  textAt("STATE OF ENERGY", 286, 188, 2, MUTED);
  textAt(ok ? percent(t.soePermille) + "%" : "--", 286, 205, 4);
  card(16, 240, 448, 32);
  textAt(stateName(t, ok), 28, 246, 2, c);
  rightAt(CHARGER_DEMO ? "SIMULATION"
                       : (ok && t.state == charger::Complete
                              ? "VERIFY SAFE RELEASE"
                              : (CHARGER_ENABLE_CONTROLS ? "CONTROL ENABLED"
                                                         : "MONITOR ONLY")),
          452, 249, 1, MUTED);
}
const char* checks[10] = {"Pack connected",     "Charger connected",
                          "BMS charge permit",  "HV interlock",
                          "Isolation monitor",  "Temperature limits",
                          "Voltage limits",     "Contactor feedback",
                          "Controller healthy", "Limits + profile"};
void readiness(uint32_t now) {
  bool ok = fresh(now);
  const auto& t = telemetry();
  textAt("PRE-CHARGE CHECKS", 16, 47, 2, MUTED);
  for (int i = 0; i < 10; i++) {
    int x = 16 + (i % 2) * 228, y = 71 + (i / 2) * 32;
    card(x, y, 220, 27);
    textAt(checks[i], x + 9, y + 5, 2);
    bool required = (t.requiredMask & (1u << i)) ||
                    (charger::kRequiredReadiness & (1u << i));
    rightAt(
        !ok ? "?"
            : (!required ? "N/A" : ((t.readyMask & (1u << i)) ? "OK" : "WAIT")),
        x + 211, y + 5, 2,
        !ok ? MUTED : ((t.readyMask & (1u << i)) ? GREEN : AMBER));
  }
  uint16_t extraPending =
      (t.requiredMask & ~charger::kRequiredReadiness) & ~t.readyMask;
  textAt(ok && extraPending ? "Additional controller checks: WAIT"
                            : "Controller must authorize every start request.",
         16, 239, 2, ok && extraPending ? AMBER : MUTED);
  textAt("Touchscreen STOP is not an emergency stop.", 16, 257, 2, AMBER);
}
const char* limitingReason(charger::LimitReason reason) {
  switch (reason) {
    case charger::LimitNone:
      return "NO DERATING";
    case charger::LimitAcSupply:
      return "OUTLET / AC SUPPLY";
    case charger::LimitCharger:
      return "CHARGER CEILING";
    case charger::LimitBms:
      return "BMS CURRENT LIMIT";
    case charger::LimitTemperature:
      return "THERMAL DERATING";
    case charger::LimitVoltage:
      return "CV / VOLTAGE LIMIT";
    default:
      return "NOT REPORTED";
  }
}
void packDetails(uint32_t now) {
  const auto& t = telemetry();
  bool ok = fresh(now);
  textAt("BATTERY PROFILE / SUPPLY", 16, 47, 2, MUTED);
  card(16, 71, 448, 201);
  const char* labels[] = {"Active pack",          "Nominal energy",
                          "Allowed charge power", "AC input power",
                          "Cell min / max",       "Cell imbalance",
                          "Power limited by"};
  String values[7];
  values[0] = ok && t.profileSeries && t.profileParallel
                  ? String(t.profileSeries) + "s" + String(t.profileParallel) +
                        "p / #" + String(t.profileId)
                  : "--";
  values[1] =
      ok && t.nominalWh ? decimal(t.nominalWh / 1000.0f, 3) + " kWh" : "--";
  values[2] = ok ? decimal(t.allowedPowerW / 1000.0f, 2) + " kW" : "--";
  values[3] = ok && t.acInputW != 0xffffffffu
                  ? decimal(t.acInputW / 1000.0f, 2) + " kW"
                  : "--";
  bool cells = ok && t.cellMinMv != 0xffff && t.cellMaxMv != 0xffff &&
               t.cellMaxMv >= t.cellMinMv;
  values[4] = cells ? decimal(t.cellMinMv / 1000.0f, 3) + " / " +
                          decimal(t.cellMaxMv / 1000.0f, 3) + " V"
                    : "--";
  values[5] = cells ? String(t.cellMaxMv - t.cellMinMv) + " mV" : "--";
  values[6] = ok ? limitingReason(t.limitReason) : "--";
  for (int i = 0; i < 7; i++) {
    textAt(labels[i], 28, 80 + i * 26, 2, MUTED);
    rightAt(values[i], 452, 80 + i * 26, 2, i == 6 ? AMBER : WHITE);
  }
}
const char* faultLabels[10] = {"BMS charge fault",   "HV interlock open",
                               "Isolation fault",    "Overvoltage",
                               "Overcurrent",        "Temperature fault",
                               "Contactor feedback", "Charger fault",
                               "Controller fault",   "UART lease expired"};
void details(uint32_t now) {
  const auto& t = telemetry();
  bool ok = fresh(now);
  textAt("SESSION / DIAGNOSTICS", 16, 47, 2, MUTED);
  card(16, 71, 448, 104);
  textAt("Delivered this session", 28, 80, 2, MUTED);
  rightAt(ok ? decimal(t.energyWh / 1000.0f, 3) + " kWh" : "--", 452, 80, 2);
  textAt("Hottest pack sensor", 28, 105, 2, MUTED);
  rightAt(ok && t.maxTempDeciC != INT16_MIN
              ? decimal(t.maxTempDeciC / 10.0f) + " C"
              : "--",
          452, 105, 2);
  textAt("Telemetry age / CRC errors", 28, 130, 2, MUTED);
  rightAt(CHARGER_DEMO
              ? "SIM / 0"
              : (chargerLink.model().hasTelemetry
                     ? String(now - chargerLink.model().lastTelemetryMs) +
                           " ms / " + String(chargerLink.model().badFrames)
                     : "--"),
          452, 130, 2);
  textAt("Command result", 28, 153, 2, MUTED);
  const char* result = chargerLink.model().commandPending ? "WAITING ACK"
                       : chargerLink.model().lastAck == charger::Accepted
                           ? "ACCEPTED (see state)"
                       : chargerLink.model().lastAck == charger::NoAck ? "NONE"
                       : chargerLink.model().lastAck == charger::TimedOut
                           ? "TIMED OUT"
                           : "REJECTED / CHECK STM32";
  rightAt(CHARGER_DEMO ? "DISABLED" : result, 452, 153, 1);
  card(16, 183, 448, 89);
  if (!ok) {
    textAt("LINK LOST: pack status unknown", 28, 191, 2, RED);
    textAt("Check STM32 power, wiring and protocol v1.", 28, 214, 2, MUTED);
  } else if (t.faultBits || t.state == charger::Fault) {
    textAt("FAULT  0x" + String(t.faultBits, HEX), 28, 190, 2, RED);
    int row = 0;
    for (int i = 0; i < 10 && row < 2; i++)
      if (t.faultBits & (1u << i))
        textAt(faultLabels[i], 28, 211 + row++ * 18, 2, RED);
    if (!t.faultBits)
      textAt("Controller fault: inspect STM32 diagnostics", 28, 214, 2, RED);
    textAt("STM32 owns fault latch; inspect cause.", 28, 251, 1, AMBER);
  } else if (CHARGER_DEMO) {
    textAt("137s4p P30B  /  12 Ah  /  5.918 kWh", 28, 191, 2);
    textAt("493.2 V nominal   /   575.4 V cell ceiling", 28, 213, 2, MUTED);
    textAt("6.6 kW charger; outlet + BMS can limit", 28, 237, 2, AMBER);
  } else {
    textAt("STM32 owns the active battery profile.", 28, 191, 2);
    textAt("SOC / SOE / ETA are controller estimates.", 28, 213, 2, MUTED);
    textAt("Complete does not mean HV is safe.", 28, 237, 2, AMBER);
  }
}
void controls(uint32_t now) {
  const auto& t = telemetry();
  bool ok = fresh(now);
  textAt("CHARGE CONTROL", 16, 47, 2, MUTED);
  card(16, 72, 448, 68);
  textAt(stateName(t, ok), 28, 82, 4, stateColor(t, ok));
  textAt(ok && t.state == charger::Complete
             ? "Wait for controller release before disconnect."
             : "Hardware emergency stop remains independent.",
         28, 115, 2, AMBER);
  card(16, 149, 216, 70);
  card(248, 149, 216, 70);
  textAt(CHARGER_DEMO ? "NEXT DEMO" : "HOLD TO START", 28, 170, 4,
         CHARGER_DEMO ? BLUE
                      : (chargerLink.readyToRequestStart(now) ? GREEN : MUTED));
  textAt(CHARGER_DEMO ? "SIMULATED" : "REQUEST STOP", 260, 170, 4,
         CHARGER_DEMO ? MUTED : RED);
  if (holdAction == 1 && holdSince)
    rect(28, 208, min(192, (int)((now - holdSince) * 192 / 1500)), 3, GREEN);
  textAt(CHARGER_DEMO ? (haveCalibration ? "Demo sends no UART messages."
                                         : "Hold BOOT 2s to calibrate touch.")
                      : (CHARGER_ENABLE_CONTROLS
                             ? "Start: hold 1.5s. Stop: one tap request."
                             : "Monitor firmware: charge requests disabled."),
         16, 233, 2, MUTED);
  textAt(
      now < noticeUntil ? notice : "An ACK confirms the request, not HV state.",
      16, 254, 2, AMBER);
}
void draw(uint32_t now) {
  uint32_t renderStart = millis();
  for (tileY = 0; tileY < 320; tileY += 80) {
    tile.fillSprite(BG);
    header(now);
    if (page == 0)
      overview(now);
    else if (page == 1)
      readiness(now);
    else if (page == 2)
      packDetails(now);
    else if (page == 3)
      details(now);
    else
      controls(now);
    line(0, 280, 479, 280, EDGE);
    const char* tabs[] = {"LIVE", "CHECKS", "PACK", "FAULTS", "CONTROL"};
    for (int i = 0; i < 5; i++) {
      if (i == page) rect(i * 96 + 8, 281, 80, 3, GREEN);
      textAt(tabs[i], i * 96 + 13, 294, 2, i == page ? GREEN : MUTED);
    }
    panel.push(tileY, static_cast<uint8_t*>(tile.getPointer()));
  }
  lastFrameMs = millis() - renderStart;
}
void initPanel() { panel.begin(); }
void calibrationScreen(int target, const char* message) {
  const int xs[] = {24, 455, 24, 455}, ys[] = {24, 24, 295, 295};
  for (tileY = 0; tileY < 320; tileY += 80) {
    tile.fillSprite(BG);
    textAt("TOUCH CALIBRATION", 100, 125, 4, WHITE);
    textAt(message, 48, 163, 2, MUTED);
    if (target >= 0) {
      int x = xs[target], y = ys[target] - tileY;
      tile.drawCircle(x, y, 12, WHITE);
      tile.drawFastHLine(x - 17, y, 35, GREEN);
      tile.drawFastVLine(x, y - 17, 35, GREEN);
    }
    panel.push(tileY, static_cast<uint8_t*>(tile.getPointer()));
  }
}
bool calibratedTouch(uint16_t* x, uint16_t* y) {
  uint16_t rx, ry;
  if (!haveCalibration || !panel.readRaw(rx, ry, 600)) return false;
  float px = calibration.ax * rx + calibration.bx * ry + calibration.cx;
  float py = calibration.ay * rx + calibration.by * ry + calibration.cy;
  if (!isfinite(px) || !isfinite(py) || px < 0 || px >= 480 || py < 0 ||
      py >= 320)
    return false;
  *x = uint16_t(px);
  *y = uint16_t(py);
  return true;
}
bool waitForTouchRelease(uint32_t timeoutMs) {
  uint32_t start = millis(), releasedSince = 0;
  while (millis() - start < timeoutMs) {
    if (digitalRead(36) == HIGH) {
      if (!releasedSince) releasedSince = millis();
      if (millis() - releasedSince >= 100) return true;
    } else
      releasedSince = 0;
    delay(10);
  }
  return false;
}
void calibrate() {
  if (!CHARGER_DEMO)
    return;  // Calibrate in demo; live communications must keep running.
  calibrating = true;
  uint16_t rx[4], ry[4];
  // Give the initiating BOOT hold time to release, then permit BOOT to cancel.
  calibrationScreen(-1, "Release BOOT; tap each corner target.");
  uint32_t start = millis();
  while (digitalRead(0) == LOW && millis() - start < 5000) delay(10);
  bool success = waitForTouchRelease(5000);
  for (int i = 0; i < 4 && success; i++) {
    calibrationScreen(i, "Press target. BOOT cancels calibration.");
    uint16_t x, y;
    start = millis();
    bool got = false;
    while (millis() - start < 60000 && digitalRead(0) == HIGH) {
      if (panel.readRaw(x, y, 600)) {
        delay(60);
        if (panel.readRaw(rx[i], ry[i], 600)) {
          got = true;
          break;
        }
      }
      delay(10);
    }
    if (!got) {
      success = false;
      break;
    }
    if (!waitForTouchRelease(5000)) {
      success = false;
      break;
    }
  }
  if (success) {
    float x0 = rx[0], x1 = rx[1], x2 = rx[2], y0 = ry[0], y1 = ry[1],
          y2 = ry[2];
    float determinant = x0 * (y1 - y2) + x1 * (y2 - y0) + x2 * (y0 - y1);
    if (fabsf(determinant) < 10000)
      success = false;
    else {
      TouchCalibration c;
      c.ax = 431 * (y2 - y0) / determinant;
      c.bx = 431 * (x0 - x2) / determinant;
      c.cx = 24 - c.ax * x0 - c.bx * y0;
      c.ay = 271 * (y0 - y1) / determinant;
      c.by = 271 * (x1 - x0) / determinant;
      c.cy = 24 - c.ay * x0 - c.by * y0;
      float ex = c.ax * rx[3] + c.bx * ry[3] + c.cx - 455;
      float ey = c.ay * rx[3] + c.by * ry[3] + c.cy - 295;
      if (!isfinite(ex) || !isfinite(ey) || fabsf(ex) > 25 || fabsf(ey) > 25)
        success = false;
      else if (preferences.putBytes("touch-hal", &c, sizeof(c)) != sizeof(c))
        success = false;
      else {
        calibration = c;
        haveCalibration = true;
      }
    }
  }
  calibrationScreen(-1, success ? "Calibration saved"
                                : "Calibration cancelled / invalid; try again");
  delay(1000);
  wasTouch = false;
  holdSince = 0;
  holdAction = 0;
  calibrating = false;
}
void updateDemo(uint32_t now) {
  // Demonstration values only. No inferred live pack SOC or charger actuation.
  float phase = (now % 60000) / 60000.0f;
  demo.packMv = 526400 + (uint32_t)(phase * 4000);
  demo.currentMa = 11800;
  demo.powerW = (int32_t)(demo.packMv / 1000.0f * demo.currentMa / 1000.0f);
  demo.socPermille = 680 + (uint16_t)(phase * 20);
  demo.soePermille = 650 + (uint16_t)(phase * 20);
  demo.etaSeconds = 1320 - (now / 1000) % 60;
  demo.maxTempDeciC = 324;
  demo.energyWh = 2170 + (now / 1000) % 100;
  demo.targetSocPermille = 1000;
  demo.allowedPowerW = 6600;
  demo.cellMinMv = 3838;
  demo.cellMaxMv = 3852;
  demo.limitReason = charger::LimitCharger;
  demo.profileSeries = 137;
  demo.profileParallel = 4;
  demo.nominalWh = 5918;
  demo.acInputW = 6810;
  demo.profileId = 1;
  demo.readyMask = charger::kRequiredReadiness;
  demo.requiredMask = charger::kRequiredReadiness;
  demo.faultBits = 0;
  demo.state = charger::Charging;
  if (demoScenario == 1) {
    demo.state = charger::Ready;
    demo.currentMa = demo.powerW = 0;
    demo.etaSeconds = charger::kUnknownEta;
  }
  if (demoScenario == 2) {
    demo.state = charger::Complete;
    demo.socPermille = 1000;
    demo.soePermille = 1000;
    demo.packMv = 575400;
    demo.cellMinMv = 4190;
    demo.cellMaxMv = 4200;
    demo.currentMa = demo.powerW = 0;
    demo.etaSeconds = 0;
  }
  if (demoScenario == 3) {
    demo.state = charger::Fault;
    demo.faultBits = charger::FaultTemperature;
    demo.readyMask &= ~charger::TemperaturesOk;
    demo.maxTempDeciC = 612;
    demo.currentMa = demo.powerW = 0;
    demo.etaSeconds = charger::kUnknownEta;
  }
  if (demoScenario == 6) {
    demo.packMv = 573200;
    demo.currentMa = 2300;
    demo.powerW = 1318;
    demo.socPermille = 975;
    demo.soePermille = 970;
    demo.etaSeconds = 480;
    demo.limitReason = charger::LimitVoltage;
    demo.cellMinMv = 4173;
    demo.cellMaxMv = 4190;
  }
  if (demoScenario == 4) {
    demo.state = charger::Checking;
    demo.readyMask = charger::PackPresent | charger::ChargerPresent;
    demo.currentMa = demo.powerW = 0;
    demo.etaSeconds = charger::kUnknownEta;
  }
}
void touch(uint32_t now) {
  uint16_t x = 0, y = 0;
  bool down = calibratedTouch(&x, &y);
  if (down && !wasTouch) {
    lastTouch = now;
    if (y >= 280)
      page = min(4, (int)x / 96);
    else if (page == 4 && y >= 149 && y < 219) {
      if (x < 232) {
        if (CHARGER_DEMO)
          demoScenario = (demoScenario + 1) % 7;
        else if (chargerLink.readyToRequestStart(now)) {
          holdSince = now;
          holdAction = 1;
        }
      } else if (!CHARGER_DEMO) {
        bool sent = chargerLink.sendCommand(charger::Stop, 0, now);
        notice = sent ? "Stop requested; waiting for controller"
                      : "Stop not sent: monitor / link / busy";
        noticeUntil = now + 3500;
      }
    }
  }
  if (down && holdAction == 1 &&
      (page != 4 || x >= 232 || y < 149 || y >= 219)) {
    holdSince = 0;
    holdAction = 0;
  }
  if (down && holdAction == 1 && now - holdSince >= 1500) {
    bool sent = chargerLink.sendCommand(charger::Start, 0, now);
    holdAction = 0;
    holdSince = 0;
    notice = sent ? "Start requested; waiting for controller"
                  : "Start blocked by controller/link checks";
    noticeUntil = now + 3500;
  }
  if (!down) {
    holdSince = 0;
    holdAction = 0;
  }
  wasTouch = down;
}
void setup() {
  Serial.begin(115200);
  pinMode(0, INPUT_PULLUP);
  // Disable audio amplifier and keep active-low RGB LEDs off.
  pinMode(4, OUTPUT);
  digitalWrite(4, HIGH);
  for (int pin : {22, 16, 17}) {
    pinMode(pin, OUTPUT);
    digitalWrite(pin, HIGH);
  }
  initPanel();
  tile.setColorDepth(16);
  if (!tile.createSprite(480, 80)) {
    Serial.println("ERROR: display buffer allocation failed");
    while (true) delay(1000);
  }
  preferences.begin("charger-ui", false);
  haveCalibration =
      preferences.isKey("touch-hal") &&
      preferences.getBytesLength("touch-hal") == sizeof(calibration);
  if (haveCalibration) {
    preferences.getBytes("touch-hal", &calibration, sizeof(calibration));
    haveCalibration = isfinite(calibration.ax) && isfinite(calibration.bx) &&
                      isfinite(calibration.cx) && isfinite(calibration.ay) &&
                      isfinite(calibration.by) && isfinite(calibration.cy);
  }
  // BOOT held for two seconds at startup forces recalibration.
  bool forceCal = false;
  if (digitalRead(0) == LOW) {
    delay(2000);
    forceCal = digitalRead(0) == LOW;
  }
  if (forceCal) calibrate();
#if !CHARGER_DEMO
  controller.setRxBufferSize(1024);
  controller.begin(115200, SERIAL_8N1, 32, 25);
  chargerLink.setControlsEnabled(CHARGER_ENABLE_CONTROLS);
  uint32_t session = esp_random();
  chargerLink.beginSession(session ? session : 1, millis());
#endif
  Serial.println("LCD_TRANSPORT Arduino_SPI HAL / ST7796 / 480x320");
  Serial.printf(
      "LHRE_CHARGER_UI boot mode=%s controls=%d LCD=480x320 UART_RX=32 "
      "UART_TX=25\n",
      CHARGER_DEMO ? "DEMO" : "LIVE", CHARGER_ENABLE_CONTROLS);
}
void loop() {
  uint32_t now = millis();
#if CHARGER_DEMO
  updateDemo(now);
#else
  // Bound per-pass receive work so a noisy UART cannot starve touch/link
  // timeouts.
  for (int budget = 256; budget > 0 && controller.available(); --budget)
    chargerLink.feed(controller.read(), now);
  chargerLink.tick(now);
#endif
  static uint32_t priorFaults = 0;
  static charger::State priorState = charger::Idle;
  static bool priorFresh = false;
  bool isFresh = fresh(now);
  if (isFresh &&
      (telemetry().faultBits != 0 || telemetry().state == charger::Fault) &&
      (priorFaults != telemetry().faultBits ||
       (telemetry().state == charger::Fault && priorState != charger::Fault)))
    page = 3;
  if (isFresh && telemetry().faultBits == 0 &&
      telemetry().state == charger::Complete && priorState != charger::Complete)
    page = 0;
  if (!isFresh && priorFresh) page = 0;
  priorFaults = isFresh ? telemetry().faultBits : 0;
  priorState = isFresh ? telemetry().state : charger::Idle;
  priorFresh = isFresh;
  touch(now);
  // Short BOOT press advances demo; hold 2s calibrates when offline.
  static bool previousBoot = true, longBoot = false;
  static uint32_t bootSince = 0;
  bool boot = digitalRead(0);
  if (!boot && previousBoot) {
    bootSince = now;
    longBoot = false;
  }
  if (!boot && !longBoot && now - bootSince >= 2000) {
    longBoot = true;
    if (CHARGER_DEMO) {
      calibrate();
      now = millis();
    }
  }
  if (boot && !previousBoot && !longBoot && now - bootSince > 40 &&
      CHARGER_DEMO)
    demoScenario = (demoScenario + 1) % 7;
  previousBoot = boot;
  if (Serial.available() && Serial.read() == 'c' && CHARGER_DEMO) calibrate();
  if (now - lastPaint >= 200) {
    lastPaint = now;
    draw(now);
  }
  if (now - lastLog >= 2000) {
    lastLog = now;
    Serial.printf(
        "UI_ALIVE mode=%s heap=%u touch=%d state=%u link=%d uptime_ms=%u "
        "frame_ms=%u\n",
        CHARGER_DEMO ? "DEMO" : "LIVE", ESP.getFreeHeap(), haveCalibration,
        (unsigned)telemetry().state, fresh(now), now, lastFrameMs);
  }
  delay(2);
}
