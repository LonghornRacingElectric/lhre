#include "ChargerLink.h"
#include <string.h>

namespace charger {
namespace {
void put16(uint8_t* p, uint16_t x) {
  p[0] = uint8_t(x);
  p[1] = uint8_t(x >> 8);
}
void put32(uint8_t* p, uint32_t x) {
  for (uint8_t i = 0; i < 4; ++i) p[i] = uint8_t(x >> (8 * i));
}
uint16_t get16(const uint8_t* p) {
  return uint16_t(p[0]) | uint16_t(uint16_t(p[1]) << 8);
}
uint32_t get32(const uint8_t* p) {
  return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) |
         (uint32_t(p[3]) << 24);
}
bool newer(uint16_t a, uint16_t b) {
  uint16_t d = uint16_t(a - b);
  return d != 0 && d < 0x8000;
}
bool percent(uint16_t x) { return x <= 1000 || x == kUnknownPercent; }
}  // namespace

Telemetry::Telemetry()
    : packMv(0),
      currentMa(0),
      powerW(0),
      socPermille(kUnknownPercent),
      soePermille(kUnknownPercent),
      etaSeconds(kUnknownEta),
      maxTempDeciC(-32768),
      energyWh(0),
      targetSocPermille(kUnknownPercent),
      readyMask(0),
      requiredMask(kRequiredReadiness),
      faultBits(0),
      state(Idle),
      allowedPowerW(0),
      cellMinMv(0xffff),
      cellMaxMv(0xffff),
      limitReason(LimitUnknown),
      profileSeries(0),
      profileParallel(0),
      nominalWh(0),
      acInputW(0xffffffffu),
      profileId(0) {}

Model::Model()
    : hasTelemetry(false),
      sessionReady(false),
      commandPending(false),
      lastTelemetryMs(0),
      pendingSequence(0),
      pendingCommand(RequestSnapshot),
      lastAck(NoAck),
      ackDetail(0),
      badFrames(0),
      ignoredFrames(0) {}

uint16_t crc16(const uint8_t* p, size_t n) {
  uint16_t crc = 0xffff;
  while (n--) {
    crc ^= uint16_t(*p++) << 8;
    for (uint8_t i = 0; i < 8; ++i)
      crc = uint16_t((crc & 0x8000) ? ((crc << 1) ^ 0x1021) : (crc << 1));
  }
  return crc;
}

size_t encodeFrame(const Frame& frame, uint8_t* out, size_t capacity) {
  if (!out || frame.length > kMaxPayload ||
      capacity < size_t(frame.length) + 13)
    return 0;
  uint8_t raw[kMaxPayload + 11];
  raw[0] = kVersion;
  raw[1] = frame.type;
  raw[2] = frame.length;
  put16(raw + 3, frame.sequence);
  put32(raw + 5, frame.session);
  if (frame.length) memcpy(raw + 9, frame.payload, frame.length);
  size_t n = 9 + frame.length;
  put16(raw + n, crc16(raw, n));
  n += 2;
  size_t codeIndex = 0, writeIndex = 1;
  uint8_t code = 1;
  for (size_t i = 0; i < n; ++i) {
    if (raw[i] == 0) {
      out[codeIndex] = code;
      codeIndex = writeIndex++;
      code = 1;
    } else {
      out[writeIndex++] = raw[i];
      ++code;
    }
  }
  out[codeIndex] = code;
  out[writeIndex++] = 0;
  return writeIndex;
}

bool decodeFrame(const uint8_t* input, size_t length, Frame& frame) {
  if (!input || length == 0 || length >= kMaxEncoded) return false;
  uint8_t raw[kMaxPayload + 11];
  size_t read = 0, write = 0;
  while (read < length) {
    uint8_t code = input[read++];
    if (!code || size_t(code - 1) > length - read) return false;
    for (uint8_t i = 1; i < code; ++i) {
      if (write >= sizeof(raw) || input[read] == 0) return false;
      raw[write++] = input[read++];
    }
    if (code != 255 && read < length) {
      if (write >= sizeof(raw)) return false;
      raw[write++] = 0;
    }
  }
  if (write < 11 || raw[0] != kVersion || raw[2] > kMaxPayload ||
      write != size_t(raw[2]) + 11)
    return false;
  if (crc16(raw, write - 2) != get16(raw + write - 2)) return false;
  frame.type = raw[1];
  frame.length = raw[2];
  frame.sequence = get16(raw + 3);
  frame.session = get32(raw + 5);
  if (frame.length) memcpy(frame.payload, raw + 9, frame.length);
  return true;
}

size_t encodeTelemetry(const Telemetry& t, uint8_t* out, size_t capacity) {
  if (!out || capacity < 59) return 0;
  put32(out, t.packMv);
  put32(out + 4, uint32_t(t.currentMa));
  put32(out + 8, uint32_t(t.powerW));
  put16(out + 12, t.socPermille);
  put16(out + 14, t.soePermille);
  put32(out + 16, t.etaSeconds);
  put16(out + 20, uint16_t(t.maxTempDeciC));
  put32(out + 22, t.energyWh);
  put16(out + 26, t.targetSocPermille);
  put16(out + 28, t.readyMask);
  put16(out + 30, t.requiredMask);
  put32(out + 32, t.faultBits);
  out[36] = uint8_t(t.state);
  put32(out + 37, t.allowedPowerW);
  put16(out + 41, t.cellMinMv);
  put16(out + 43, t.cellMaxMv);
  out[45] = uint8_t(t.limitReason);
  put16(out + 46, t.profileSeries);
  out[48] = t.profileParallel;
  put32(out + 49, t.nominalWh);
  put32(out + 53, t.acInputW);
  put16(out + 57, t.profileId);
  return 59;
}

bool decodeTelemetry(const uint8_t* in, size_t n, Telemetry& t) {
  if (!in || n != 59 || in[36] > Stopping || in[45] > LimitVoltage)
    return false;
  Telemetry v;
  v.packMv = get32(in);
  v.currentMa = int32_t(get32(in + 4));
  v.powerW = int32_t(get32(in + 8));
  v.socPermille = get16(in + 12);
  v.soePermille = get16(in + 14);
  v.etaSeconds = get32(in + 16);
  v.maxTempDeciC = int16_t(get16(in + 20));
  v.energyWh = get32(in + 22);
  v.targetSocPermille = get16(in + 26);
  v.readyMask = get16(in + 28);
  v.requiredMask = get16(in + 30);
  v.faultBits = get32(in + 32);
  v.state = State(in[36]);
  v.allowedPowerW = get32(in + 37);
  v.cellMinMv = get16(in + 41);
  v.cellMaxMv = get16(in + 43);
  v.limitReason = LimitReason(in[45]);
  v.profileSeries = get16(in + 46);
  v.profileParallel = in[48];
  v.nominalWh = get32(in + 49);
  v.acInputW = get32(in + 53);
  v.profileId = get16(in + 57);
  if (!percent(v.socPermille) || !percent(v.soePermille) ||
      !percent(v.targetSocPermille))
    return false;
  t = v;
  return true;
}

Link::Link(WriteFn write, void* context)
    : write_(write),
      context_(context),
      session_(0),
      txSequence_(0),
      rxSequence_(0),
      haveRxSequence_(false),
      controlsEnabled_(false),
      lastHelloMs_(0),
      lastHeartbeatMs_(0),
      commandSentMs_(0),
      inputLength_(0),
      discard_(false) {}

void Link::beginSession(uint32_t session, uint32_t now) {
  session_ = session ? session : 1;
  txSequence_ = 0;
  haveRxSequence_ = false;
  inputLength_ = 0;
  discard_ = false;
  model_.hasTelemetry = false;
  model_.sessionReady = false;
  model_.commandPending = false;
  model_.lastAck = NoAck;
  lastHelloMs_ = lastHeartbeatMs_ = now;
  send(Hello, 0, 0);
}

void Link::send(uint8_t type, const uint8_t* payload, uint8_t length) {
  if (!write_ || !session_) return;
  Frame f = {};
  f.type = type;
  f.sequence = txSequence_++;
  f.session = session_;
  f.length = length;
  if (length) memcpy(f.payload, payload, length);
  uint8_t out[kMaxEncoded];
  size_t n = encodeFrame(f, out, sizeof(out));
  if (n) write_(out, n, context_);
}

void Link::feed(uint8_t byte, uint32_t now) {
  if (byte == 0) {
    if (discard_) {
      discard_ = false;
      inputLength_ = 0;
      return;
    }
    if (!inputLength_) return;
    Frame f = {};
    if (decodeFrame(input_, inputLength_, f))
      receive(f, now);
    else
      ++model_.badFrames;
    inputLength_ = 0;
    return;
  }
  if (discard_) return;
  if (inputLength_ >= sizeof(input_) - 1) {
    discard_ = true;
    inputLength_ = 0;
    ++model_.badFrames;
    return;
  }
  input_[inputLength_++] = byte;
}

void Link::receive(const Frame& f, uint32_t now) {
  // Late traffic must not revive an expired session before tick() gets CPU
  // time.
  if (model_.sessionReady &&
      uint32_t(now - model_.lastTelemetryMs) > kTelemetryTimeoutMs) {
    uint32_t next = session_ * 1664525u + 1013904223u;
    beginSession(next ? next : 1, now);
  }
  if (!session_ || f.session != session_) {
    ++model_.ignoredFrames;
    return;
  }
  if (f.type == HelloAck && f.length == 0 && !model_.sessionReady) {
    model_.sessionReady = true;
    model_.lastTelemetryMs = now;
    rxSequence_ = f.sequence;
    haveRxSequence_ = true;
    return;
  }
  if (!model_.sessionReady ||
      (haveRxSequence_ && !newer(f.sequence, rxSequence_))) {
    ++model_.ignoredFrames;
    return;
  }
  if (f.type == TelemetryMessage) {
    Telemetry t;
    if (!decodeTelemetry(f.payload, f.length, t)) {
      ++model_.badFrames;
      return;
    }
    model_.telemetry = t;
    model_.hasTelemetry = true;
    model_.lastTelemetryMs = now;
  } else if (f.type == AckMessage && f.length == 6) {
    if (!model_.commandPending || get16(f.payload) != model_.pendingSequence ||
        f.payload[2] != uint8_t(model_.pendingCommand) ||
        f.payload[3] > Unsupported) {
      ++model_.ignoredFrames;
      return;
    }
    model_.commandPending = false;
    model_.lastAck = AckCode(f.payload[3]);
    model_.ackDetail = get16(f.payload + 4);
  } else if (f.type != Heartbeat || f.length != 0) {
    ++model_.ignoredFrames;
    return;
  }
  rxSequence_ = f.sequence;
  haveRxSequence_ = true;
}

bool Link::connected(uint32_t now) const {
  return model_.sessionReady && model_.hasTelemetry &&
         uint32_t(now - model_.lastTelemetryMs) <= kTelemetryTimeoutMs;
}

bool Link::readyToRequestStart(uint32_t now) const {
  const Telemetry& t = model_.telemetry;
  return controlsEnabled_ && connected(now) && !model_.commandPending &&
         t.state == Ready && t.faultBits == 0 && t.allowedPowerW > 0 &&
         (t.requiredMask & kRequiredReadiness) == kRequiredReadiness &&
         (t.readyMask & t.requiredMask) == t.requiredMask;
}

bool Link::sendCommand(Command command, uint32_t argument, uint32_t now) {
  if (!write_ || !connected(now) ||
      (model_.commandPending && command != Stop) || argument != 0 ||
      command < Start || command > RequestSnapshot)
    return false;
  if (command != RequestSnapshot && !controlsEnabled_) return false;
  if (command == Start && !readyToRequestStart(now)) return false;
  uint8_t payload[5] = {uint8_t(command), 0, 0, 0, 0};
  model_.pendingSequence = txSequence_;
  model_.pendingCommand = command;
  model_.commandPending = true;
  model_.lastAck = NoAck;
  model_.ackDetail = 0;
  commandSentMs_ = now;
  send(CommandMessage, payload, sizeof(payload));
  return true;
}

void Link::tick(uint32_t now) {
  if (!session_) return;
  if (model_.commandPending && uint32_t(now - commandSentMs_) > kAckTimeoutMs) {
    model_.commandPending = false;
    model_.lastAck = TimedOut;
  }
  if (model_.sessionReady &&
      uint32_t(now - model_.lastTelemetryMs) > kTelemetryTimeoutMs) {
    uint32_t next = session_ * 1664525u + 1013904223u;
    beginSession(
        next ? next : 1,
        now);  // Old commands and telemetry cannot rejoin the new session.
  }
  if (!model_.sessionReady && uint32_t(now - lastHelloMs_) >= 500) {
    lastHelloMs_ = now;
    send(Hello, 0, 0);
  }
  if (model_.sessionReady && uint32_t(now - lastHeartbeatMs_) >= 250) {
    lastHeartbeatMs_ = now;
    send(Heartbeat, 0, 0);
  }
}

}  // namespace charger
