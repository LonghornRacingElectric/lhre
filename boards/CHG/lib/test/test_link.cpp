#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <vector>
#include "ChargerLink.h"
#include "charger_ui_link.h"

using namespace charger;
struct Rig {
  std::vector<uint8_t> toStm, toEsp;
  chg_ui_link stm;
  Link esp;
  chg_telemetry telem;
  unsigned starts, stops, acknowledgements;
  uint8_t stopReason;
  Rig()
      : esp(espWrite, this),
        starts(0),
        stops(0),
        acknowledgements(0),
        stopReason(0) {
    memset(&telem, 0, sizeof(telem));
    telem.pack_mv = 548000;
    telem.soc_permille = 550;
    telem.soe_permille = 530;
    telem.eta_seconds = 3200;
    telem.max_temp_deci_c = 290;
    telem.target_soc_permille = 900;
    telem.required_mask = CHG_REQUIRED_READINESS;
    telem.ready_mask = CHG_REQUIRED_READINESS;
    telem.state = CHG_READY;
    telem.allowed_power_w = 6600;
    telem.cell_min_mv = 3950;
    telem.cell_max_mv = 4010;
    telem.limit_reason = LimitCharger;
    telem.profile_series = 137;
    telem.profile_parallel = 4;
    telem.nominal_wh = 5918;
    telem.ac_input_w = 7200;
    telem.profile_id = 1;
    chg_ui_hooks h = {stmWrite, snapshot, start, stop, ackFault, this};
    chg_ui_init(&stm, &h);
  }
  static void espWrite(const uint8_t* p, size_t n, void* c) {
    Rig* r = static_cast<Rig*>(c);
    r->toStm.insert(r->toStm.end(), p, p + n);
  }
  static void stmWrite(const uint8_t* p, size_t n, void* c) {
    Rig* r = static_cast<Rig*>(c);
    r->toEsp.insert(r->toEsp.end(), p, p + n);
  }
  static void snapshot(chg_telemetry* t, void* c) {
    *t = static_cast<Rig*>(c)->telem;
  }
  static uint8_t start(void* c) {
    ++static_cast<Rig*>(c)->starts;
    return CHG_ACCEPTED;
  }
  static void stop(uint8_t reason, void* c) {
    Rig* r = static_cast<Rig*>(c);
    ++r->stops;
    r->stopReason = reason;
  }
  static void ackFault(void* c) { ++static_cast<Rig*>(c)->acknowledgements; }
  void pump(uint32_t now) {
    std::vector<uint8_t> a;
    a.swap(toStm);
    for (size_t i = 0; i < a.size(); ++i) chg_ui_receive_byte(&stm, a[i], now);
    std::vector<uint8_t> b;
    b.swap(toEsp);
    for (size_t i = 0; i < b.size(); ++i) esp.feed(b[i], now);
  }
  void boot(uint32_t now = 10) {
    esp.beginSession(0x12345678, now);
    pump(now);
    assert(esp.connected(now));
  }
};

static void codec() {
  const uint8_t check[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
  assert(crc16(check, sizeof(check)) == 0x29b1);
  uint32_t random = 0x12345678;
  for (unsigned len = 0; len <= 64; ++len)
    for (unsigned rep = 0; rep < 100; ++rep) {
      Frame f = {}, g = {};
      f.length = uint8_t(len);
      f.type = TelemetryMessage;
      f.sequence = uint16_t(rep * 119);
      f.session = 0x90003000;
      for (unsigned i = 0; i < len; ++i) {
        random = random * 1664525u + 1013904223u;
        f.payload[i] = rep % 3 ? uint8_t(random >> 24) : 0;
      }
      uint8_t bytes[82];
      size_t n = encodeFrame(f, bytes, sizeof(bytes));
      assert(n > 0 && bytes[n - 1] == 0);
      assert(decodeFrame(bytes, n - 1, g));
      assert(g.sequence == f.sequence && g.session == f.session);
      assert(g.length == len && !memcmp(g.payload, f.payload, len));
      for (size_t bit = 0; bit < (n - 1) * 8; ++bit) {
        bytes[bit / 8] ^= uint8_t(1u << (bit % 8));
        assert(!decodeFrame(bytes, n - 1, g));
        bytes[bit / 8] ^= uint8_t(1u << (bit % 8));
      }
      assert(!decodeFrame(bytes, n, g));
    }
  Frame f = {};
  f.length = 65;
  uint8_t out[82];
  assert(!encodeFrame(f, out, sizeof(out)));
  Telemetry t, u;
  uint8_t p[64];
  assert(encodeTelemetry(t, p, sizeof(p)) == 59);
  assert(decodeTelemetry(p, 59, u));
  assert(u.socPermille == kUnknownPercent);
  p[12] = 0xe9;
  p[13] = 3;
  assert(!decodeTelemetry(p, 59, u));  // 1001 permille invalid.
  assert(!decodeTelemetry(p, 58, u));
}

static void integration() {
  Rig r;
  r.boot();
  assert(r.esp.model().telemetry.profileSeries == 137);
  assert(r.esp.model().telemetry.allowedPowerW == 6600);
  assert(!r.esp.sendCommand(Start, 0, 10));  // Monitor-only default.
  r.esp.setControlsEnabled(true);
  assert(r.esp.readyToRequestStart(10));
  assert(r.esp.sendCommand(Start, 0, 20));
  std::vector<uint8_t> duplicate = r.toStm;
  assert(!r.esp.sendCommand(Start, 0, 20));  // One outstanding request.
  r.pump(20);
  assert(r.starts == 1 && r.esp.model().lastAck == Accepted);
  assert(r.esp.model().telemetry.state ==
         Ready);  // ACK does not invent a state.
  r.toStm = duplicate;
  r.pump(21);
  assert(r.starts == 1);  // STM deduplicates.
  r.telem.fault_bits = FaultIsolation;
  chg_ui_tick(&r.stm, 120);
  r.pump(120);
  assert(!r.esp.readyToRequestStart(120));
  assert(!r.esp.sendCommand(Start, 0, 120));
  assert(r.esp.sendCommand(Stop, 0, 121));
  r.pump(121);
  assert(r.stops == 1 && r.stopReason == CHG_STOP_USER);
  assert(r.esp.sendCommand(AcknowledgeFault, 0, 122));
  r.pump(122);
  assert(r.acknowledgements == 1 && r.telem.fault_bits == FaultIsolation);
  r.telem.fault_bits = 0;
  r.telem.ready_mask &= uint16_t(~IsolationOk);
  chg_ui_tick(&r.stm, 220);
  r.pump(220);
  assert(!r.esp.readyToRequestStart(220));
  r.telem.ready_mask = CHG_REQUIRED_READINESS;
  r.telem.required_mask = 0;
  chg_ui_tick(&r.stm, 320);
  r.pump(320);
  assert(!r.esp.readyToRequestStart(320));
}

static void staleAndMalformed() {
  Rig r;
  r.boot();
  r.esp.setControlsEnabled(true);
  assert(!r.esp.connected(1511));
  assert(!r.esp.sendCommand(Start, 0, 1511));
  chg_ui_tick(&r.stm, 1011);
  assert(r.stops == 1 && !r.stm.active);
  r.esp.tick(1511);
  r.pump(1511);
  assert(r.esp.connected(1511));
  assert(r.starts == 0);
  // Overlength noise cannot bypass the delimiter resynchronization.
  for (unsigned i = 0; i < 200; ++i) r.esp.feed(0xaa, 1512);
  r.esp.feed(0, 1512);
  assert(r.esp.model().badFrames > 0);
  chg_ui_tick(&r.stm, 1611);
  r.pump(1611);
  assert(r.esp.connected(1611));
  // A late command cannot refresh the STM watchdog before the timeout is
  // noticed.
  assert(r.esp.sendCommand(Start, 0, 1612));
  r.pump(2612);
  assert(r.starts == 0 && !r.stm.active && r.stopReason == CHG_STOP_LINK_LOSS);
}

static void sessionAndSequence() {
  Rig r;
  r.boot();
  r.esp.setControlsEnabled(true);
  assert(r.esp.sendCommand(RequestSnapshot, 0, 11));
  std::vector<uint8_t> old = r.toStm;
  r.pump(11);
  r.esp.beginSession(0xaabbccdd, 20);
  r.pump(20);
  assert(r.stops == 1 && r.stopReason == CHG_STOP_NEW_SESSION);
  r.toStm = old;
  r.pump(21);
  assert(r.starts == 0);
  // Sequence rollover is valid and delayed telemetry must not roll the display
  // back.
  Frame f = {};
  f.type = TelemetryMessage;
  f.session = 0xaabbccdd;
  Telemetry t;
  t.state = Ready;
  t.packMv = 550000;
  f.length = uint8_t(encodeTelemetry(t, f.payload, sizeof(f.payload)));
  uint8_t bytes[82];
  f.sequence = 30000;
  size_t n = encodeFrame(f, bytes, sizeof(bytes));
  for (size_t i = 0; i < n; ++i) r.esp.feed(bytes[i], 30);
  f.sequence = 60000;
  n = encodeFrame(f, bytes, sizeof(bytes));
  for (size_t i = 0; i < n; ++i) r.esp.feed(bytes[i], 31);
  f.sequence = 10;
  n = encodeFrame(f, bytes, sizeof(bytes));
  for (size_t i = 0; i < n; ++i) r.esp.feed(bytes[i], 32);
  assert(r.esp.model().lastTelemetryMs == 32);
  f.sequence = 9;
  t.packMv = 1;
  f.length = uint8_t(encodeTelemetry(t, f.payload, sizeof(f.payload)));
  n = encodeFrame(f, bytes, sizeof(bytes));
  for (size_t i = 0; i < n; ++i) r.esp.feed(bytes[i], 33);
  assert(r.esp.model().telemetry.packMv == 550000);
}

static void timeoutAndWrap() {
  Rig r;
  r.boot(0xffffff00u);
  r.esp.setControlsEnabled(true);
  assert(r.esp.connected(0x20u));
  assert(r.esp.sendCommand(Start, 0, 0xfffffff0u));
  r.toStm
      .clear();  // Lost request/ACK: never retry and never assume it completed.
  r.esp.tick(0x4a1u);
  assert(!r.esp.model().commandPending);
  assert(r.esp.model().lastAck == TimedOut && r.starts == 0);
  chg_ui_tick(&r.stm, 0x400u);
  assert(!r.stm.active && r.stops == 1);
}

static void feedFrame(Link& link, const Frame& f, uint32_t now) {
  uint8_t bytes[82];
  size_t n = encodeFrame(f, bytes, sizeof(bytes));
  assert(n);
  for (size_t i = 0; i < n; ++i) link.feed(bytes[i], now);
}

static void invalidTelemetryAndAck() {
  Rig r;
  r.boot();
  r.esp.setControlsEnabled(true);
  Frame f = {};
  f.type = TelemetryMessage;
  f.sequence = 2;
  f.session = 0x12345678;
  Telemetry t;
  t.packMv = 1;
  f.length = uint8_t(encodeTelemetry(t, f.payload, sizeof(f.payload)));
  f.payload[36] = 99;
  feedFrame(r.esp, f, 20);
  assert(r.esp.model().lastTelemetryMs == 10 &&
         r.esp.model().telemetry.packMv == 548000);
  f.payload[36] = Ready;
  f.payload[45] = 99;
  feedFrame(r.esp, f, 21);
  assert(r.esp.model().lastTelemetryMs == 10);
  f.payload[45] = LimitNone;
  f.payload[14] = 0xff;
  f.payload[15] = 0x7f;
  feedFrame(r.esp, f, 22);
  assert(r.esp.model().lastTelemetryMs == 10);
  assert(r.esp.sendCommand(Start, 0, 23));
  r.toStm.clear();
  f.type = AckMessage;
  f.length = 6;
  memset(f.payload, 0, sizeof(f.payload));
  uint16_t request = r.esp.model().pendingSequence;
  f.payload[0] = uint8_t(request + 1);
  f.payload[1] = uint8_t((request + 1) >> 8);
  f.payload[2] = Start;
  feedFrame(r.esp, f, 24);
  assert(r.esp.model().commandPending);
  f.payload[0] = uint8_t(request);
  f.payload[1] = uint8_t(request >> 8);
  f.payload[2] = Stop;
  feedFrame(r.esp, f, 25);
  assert(r.esp.model().commandPending);
  f.payload[2] = Start;
  f.payload[3] = TimedOut;
  feedFrame(r.esp, f, 26);
  assert(r.esp.model().commandPending);
  f.payload[3] = Rejected;
  feedFrame(r.esp, f, 27);
  assert(!r.esp.model().commandPending && r.esp.model().lastAck == Rejected);
  assert(r.starts == 0);
  // The controller re-checks a fault that changed after the UI snapshot.
  Rig q;
  q.boot();
  q.esp.setControlsEnabled(true);
  assert(q.esp.sendCommand(Start, 0, 11));
  q.telem.fault_bits = FaultBms;
  q.pump(11);
  assert(q.starts == 0 && q.esp.model().lastAck == Rejected);
  // An invalid zero power budget also fails independently at the controller.
  q.telem.fault_bits = 0;
  q.telem.allowed_power_w = 0;
  assert(q.esp.sendCommand(Start, 0, 12));
  q.pump(12);
  assert(q.starts == 0 && q.esp.model().lastAck == Rejected);
}

static void controllerResetAndLateTelemetry() {
  Rig r;
  r.boot();
  r.esp.setControlsEnabled(true);
  chg_ui_hooks hooks = r.stm.hooks;
  chg_ui_init(&r.stm, &hooks);  // Controller reset.
  r.esp.tick(300);
  r.pump(300);
  assert(!r.stm.active);  // Heartbeat cannot authorize session.
  r.esp.tick(1511);
  r.pump(1511);
  assert(r.stm.active && r.esp.connected(1511));
  assert(r.starts == 0);
  // A valid old-session telemetry frame arriving late cannot hide ESP timeout.
  Rig q;
  q.boot();
  Frame f = {};
  f.type = TelemetryMessage;
  f.session = 0x12345678;
  f.sequence = 2;
  Telemetry t;
  t.packMv = 1;
  f.length = uint8_t(encodeTelemetry(t, f.payload, sizeof(f.payload)));
  feedFrame(q.esp, f, 1511);
  assert(!q.esp.connected(1511));
  assert(q.esp.model().telemetry.packMv == 548000 &&
         !q.esp.model().sessionReady);
  q.pump(1511);
  assert(q.esp.connected(1511) && q.starts == 0);
}

static void stopPreemptsPending() {
  Rig r;
  r.boot();
  r.esp.setControlsEnabled(true);
  assert(r.esp.sendCommand(Start, 0, 11));
  uint16_t startSequence = r.esp.model().pendingSequence;
  assert(r.esp.sendCommand(Stop, 0, 12));
  assert(r.esp.model().pendingCommand == Stop &&
         r.esp.model().pendingSequence != startSequence);
  assert(!r.esp.sendCommand(RequestSnapshot, 0, 12));
  // Deliver START alone, preserving STOP as queued traffic; old ACK must not
  // clear STOP.
  std::vector<uint8_t> all;
  all.swap(r.toStm);
  size_t split = 0;
  while (split < all.size() && all[split]) ++split;
  ++split;
  r.toStm.assign(all.begin(), all.begin() + split);
  r.pump(13);
  assert(r.starts == 1 && r.esp.model().commandPending &&
         r.esp.model().pendingCommand == Stop);
  r.toStm.assign(all.begin() + split, all.end());
  r.pump(14);
  assert(r.stops == 1 && !r.esp.model().commandPending &&
         r.esp.model().lastAck == Accepted);
  assert(r.esp.sendCommand(RequestSnapshot, 0, 15));
  assert(r.esp.sendCommand(Stop, 0, 16));
  r.pump(16);
  assert(r.stops == 2 && r.esp.model().pendingCommand == Stop &&
         !r.esp.model().commandPending);
  // Monitor mode still cannot send STOP; it has no control authority.
  r.esp.setControlsEnabled(false);
  assert(!r.esp.sendCommand(Stop, 0, 17));
}

int main() {
  codec();
  integration();
  staleAndMalformed();
  sessionAndSequence();
  timeoutAndWrap();
  invalidTelemetryAndAck();
  controllerResetAndLateTelemetry();
  stopPreemptsPending();
  puts(
      "PASS: codec/CRC corruption, STM32 interoperability, readiness, command "
      "deduplication, stale link, sessions, rollover, timeout");
}
