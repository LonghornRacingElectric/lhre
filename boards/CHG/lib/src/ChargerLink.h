#ifndef CHARGER_LINK_H
#define CHARGER_LINK_H

#include <stddef.h>
#include <stdint.h>

namespace charger {

static const uint8_t kVersion = 1;
static const size_t kMaxPayload = 64;
static const size_t kMaxEncoded = 82;
static const uint32_t kTelemetryTimeoutMs = 1500;
static const uint32_t kAckTimeoutMs = 1200;
static const uint16_t kRequiredReadiness = 0x03ff;
static const uint16_t kUnknownPercent = 0xffff;
static const uint32_t kUnknownEta = 0xffffffffu;

enum MessageType : uint8_t {
  Hello = 1,
  HelloAck = 2,
  TelemetryMessage = 3,
  CommandMessage = 4,
  AckMessage = 5,
  Heartbeat = 6
};
enum State : uint8_t {
  Idle = 0,
  Checking = 1,
  Ready = 2,
  Charging = 3,
  Complete = 4,
  Fault = 5,
  Stopping = 6
};
enum Command : uint8_t {
  Start = 1,
  Stop = 2,
  AcknowledgeFault = 3,
  RequestSnapshot = 4
};
enum AckCode : uint8_t {
  Accepted = 0,
  Rejected = 1,
  Busy = 2,
  Unsupported = 3,
  TimedOut = 254,
  NoAck = 255
};
enum Readiness : uint16_t {
  PackPresent = 1 << 0,
  ChargerPresent = 1 << 1,
  BmsAllowed = 1 << 2,
  HvInterlockClosed = 1 << 3,
  IsolationOk = 1 << 4,
  TemperaturesOk = 1 << 5,
  VoltageOk = 1 << 6,
  ContactorFeedbackOk = 1 << 7,
  ControllerHealthy = 1 << 8,
  LimitsValid = 1 << 9
};
enum FaultBit : uint32_t {
  FaultBms = 1u << 0,
  FaultInterlock = 1u << 1,
  FaultIsolation = 1u << 2,
  FaultOverVoltage = 1u << 3,
  FaultOverCurrent = 1u << 4,
  FaultTemperature = 1u << 5,
  FaultContactor = 1u << 6,
  FaultCharger = 1u << 7,
  FaultController = 1u << 8,
  FaultLink = 1u << 9
};
enum LimitReason : uint8_t {
  LimitUnknown = 0,
  LimitNone = 1,
  LimitAcSupply = 2,
  LimitCharger = 3,
  LimitBms = 4,
  LimitTemperature = 5,
  LimitVoltage = 6
};

struct Telemetry {
  uint32_t packMv;
  int32_t currentMa;  // Positive means energy entering the pack.
  int32_t powerW;
  uint16_t socPermille;
  uint16_t soePermille;
  uint32_t etaSeconds;
  int16_t maxTempDeciC;
  uint32_t energyWh;  // Delivered this charging session.
  uint16_t targetSocPermille;
  uint16_t readyMask;
  uint16_t requiredMask;
  uint32_t faultBits;
  State state;
  uint32_t allowedPowerW;
  uint16_t cellMinMv;
  uint16_t cellMaxMv;
  LimitReason limitReason;
  uint16_t profileSeries;
  uint8_t profileParallel;
  uint32_t nominalWh;
  uint32_t acInputW;
  uint16_t profileId;
  Telemetry();
};

struct Frame {
  uint8_t type;
  uint16_t sequence;
  uint32_t session;
  uint8_t length;
  uint8_t payload[kMaxPayload];
};

uint16_t crc16(const uint8_t* data, size_t length);
// Encoded output includes the zero delimiter. Returns zero on invalid input.
size_t encodeFrame(const Frame& frame, uint8_t* output, size_t capacity);
// Input excludes the zero delimiter.
bool decodeFrame(const uint8_t* input, size_t length, Frame& frame);
size_t encodeTelemetry(const Telemetry& value, uint8_t* output,
                       size_t capacity);
bool decodeTelemetry(const uint8_t* input, size_t length, Telemetry& value);

struct Model {
  Telemetry telemetry;
  bool hasTelemetry;
  bool sessionReady;
  bool commandPending;
  uint32_t lastTelemetryMs;
  uint16_t pendingSequence;
  Command pendingCommand;
  AckCode lastAck;
  uint16_t ackDetail;
  uint32_t badFrames;
  uint32_t ignoredFrames;
  Model();
};

typedef void (*WriteFn)(const uint8_t* bytes, size_t length, void* context);

class Link {
 public:
  explicit Link(WriteFn write, void* context = 0);
  void beginSession(uint32_t session, uint32_t nowMs);
  void feed(uint8_t byte, uint32_t nowMs);
  void tick(uint32_t nowMs);
  const Model& model() const { return model_; }
  bool connected(uint32_t nowMs) const;
  bool readyToRequestStart(uint32_t nowMs) const;
  void setControlsEnabled(bool enabled) { controlsEnabled_ = enabled; }
  bool controlsEnabled() const { return controlsEnabled_; }
  // No command is retried automatically. STOP supersedes any pending request.
  // Accepted ACK is not proof of charging.
  bool sendCommand(Command command, uint32_t argument, uint32_t nowMs);

 private:
  void send(uint8_t type, const uint8_t* payload, uint8_t length);
  void receive(const Frame& frame, uint32_t nowMs);
  WriteFn write_;
  void* context_;
  Model model_;
  uint32_t session_;
  uint16_t txSequence_;
  uint16_t rxSequence_;
  bool haveRxSequence_;
  bool controlsEnabled_;
  uint32_t lastHelloMs_;
  uint32_t lastHeartbeatMs_;
  uint32_t commandSentMs_;
  uint8_t input_[kMaxEncoded];
  size_t inputLength_;
  bool discard_;
};

}  // namespace charger
#endif
