#include "charger_ui_link.h"
#include <string.h>

static void p16(uint8_t* p, uint16_t v) {
  p[0] = (uint8_t)v;
  p[1] = (uint8_t)(v >> 8);
}
static void p32(uint8_t* p, uint32_t v) {
  unsigned i;
  for (i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (i * 8));
}
static uint16_t g16(const uint8_t* p) {
  return (uint16_t)(p[0] | ((uint16_t)p[1] << 8));
}
static uint32_t g32(const uint8_t* p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) |
         ((uint32_t)p[3] << 24);
}
static int newer(uint16_t a, uint16_t b) {
  uint16_t d = (uint16_t)(a - b);
  return d && d < 0x8000;
}
static uint16_t crc(const uint8_t* p, size_t n) {
  uint16_t c = 0xffff;
  unsigned i;
  while (n--) {
    c ^= (uint16_t)*p++ << 8;
    for (i = 0; i < 8; i++)
      c = (uint16_t)((c & 0x8000) ? (c << 1) ^ 0x1021 : c << 1);
  }
  return c;
}

static void send_frame(chg_ui_link* l, uint8_t type, const uint8_t* payload,
                       uint8_t len) {
  uint8_t raw[75], out[82], code = 1;
  size_t n = 9u + len, i, ci = 0, wi = 1;
  if (!l->hooks.write || len > 64) return;
  raw[0] = 1;
  raw[1] = type;
  raw[2] = len;
  p16(raw + 3, l->tx_sequence++);
  p32(raw + 5, l->session);
  if (len) memcpy(raw + 9, payload, len);
  p16(raw + n, crc(raw, n));
  n += 2;
  for (i = 0; i < n; i++) {
    if (!raw[i]) {
      out[ci] = code;
      ci = wi++;
      code = 1;
    } else {
      out[wi++] = raw[i];
      code++;
    }
  }
  out[ci] = code;
  out[wi++] = 0;
  l->hooks.write(out, wi, l->hooks.context);
}

static void snapshot(chg_ui_link* l, chg_telemetry* t) {
  memset(t, 0, sizeof(*t));
  t->soc_permille = t->soe_permille = t->target_soc_permille = 0xffff;
  t->eta_seconds = 0xffffffffu;
  t->max_temp_deci_c = -32768;
  t->required_mask = CHG_REQUIRED_READINESS;
  t->cell_min_mv = t->cell_max_mv = 0xffff;
  t->ac_input_w = 0xffffffffu;
  if (l->hooks.get_telemetry) l->hooks.get_telemetry(t, l->hooks.context);
}

static void telemetry(chg_ui_link* l) {
  chg_telemetry t;
  uint8_t p[59];
  snapshot(l, &t);
  p32(p, t.pack_mv);
  p32(p + 4, (uint32_t)t.current_ma);
  p32(p + 8, (uint32_t)t.power_w);
  p16(p + 12, t.soc_permille);
  p16(p + 14, t.soe_permille);
  p32(p + 16, t.eta_seconds);
  p16(p + 20, (uint16_t)t.max_temp_deci_c);
  p32(p + 22, t.energy_wh);
  p16(p + 26, t.target_soc_permille);
  p16(p + 28, t.ready_mask);
  p16(p + 30, t.required_mask);
  p32(p + 32, t.fault_bits);
  p[36] = t.state;
  p32(p + 37, t.allowed_power_w);
  p16(p + 41, t.cell_min_mv);
  p16(p + 43, t.cell_max_mv);
  p[45] = t.limit_reason;
  p16(p + 46, t.profile_series);
  p[48] = t.profile_parallel;
  p32(p + 49, t.nominal_wh);
  p32(p + 53, t.ac_input_w);
  p16(p + 57, t.profile_id);
  send_frame(l, 3, p, 59);
}

static void ack(chg_ui_link* l, uint16_t seq, uint8_t cmd, uint8_t result,
                uint16_t detail) {
  uint8_t p[6];
  p16(p, seq);
  p[2] = cmd;
  p[3] = result;
  p16(p + 4, detail);
  send_frame(l, 5, p, 6);
}

static void process(chg_ui_link* l, const uint8_t* r, uint32_t now) {
  uint8_t type = r[1], len = r[2];
  uint16_t seq = g16(r + 3);
  uint32_t session = g32(r + 5);
  /* A late frame cannot hide a missed watchdog deadline if polling was delayed.
   */
  if (l->active && (uint32_t)(now - l->last_rx_ms) > CHG_UI_TIMEOUT_MS) {
    l->active = 0;
    l->have_rx = 0;
    l->have_command = 0;
    if (l->hooks.request_stop)
      l->hooks.request_stop(CHG_STOP_LINK_LOSS, l->hooks.context);
  }
  if (type == 1 && len == 0 && session) {
    if (!l->active || session != l->session) {
      if (l->active && l->hooks.request_stop)
        l->hooks.request_stop(CHG_STOP_NEW_SESSION, l->hooks.context);
      l->session = session;
      l->tx_sequence = 0;
      l->have_command = 0;
      l->active = 1;
      l->have_rx = 1;
      l->rx_sequence = seq;
    } else if (!newer(seq, l->rx_sequence))
      return;
    else
      l->rx_sequence = seq;
    l->last_rx_ms = now;
    send_frame(l, 2, 0, 0);
    telemetry(l);
    l->last_tx_ms = now;
    return;
  }
  if (!l->active || session != l->session) return;
  /* A duplicate command gets its prior ACK without executing the callback
     twice. Never refresh the watchdog for a replay. START is never
     automatically retried. */
  if (type == 4 && len == 5 && l->have_command &&
      seq == l->last_command_sequence && r[9] == l->last_command) {
    ack(l, seq, l->last_command, l->last_ack, l->last_detail);
    return;
  }
  if (l->have_rx && !newer(seq, l->rx_sequence)) return;
  if (type == 6 && len == 0) {
    l->rx_sequence = seq;
    l->have_rx = 1;
    l->last_rx_ms = now;
    return;
  }
  if (type == 4 && len == 5) {
    uint8_t cmd = r[9], result = CHG_UNSUPPORTED;
    uint16_t detail = 0;
    chg_telemetry t;
    l->rx_sequence = seq;
    l->have_rx = 1;
    l->last_rx_ms = now;
    if (g32(r + 10) != 0) {
      result = CHG_REJECTED;
      detail = 1;
    } else if (cmd == 1) {
      snapshot(l, &t);
      if (t.state != CHG_READY || t.fault_bits || !t.allowed_power_w ||
          (t.required_mask & CHG_REQUIRED_READINESS) !=
              CHG_REQUIRED_READINESS ||
          (t.ready_mask & t.required_mask) != t.required_mask) {
        result = CHG_REJECTED;
        detail = 2;
      } else if (l->hooks.request_start && l->hooks.request_stop)
        result = l->hooks.request_start(l->hooks.context);
    } else if (cmd == 2 && l->hooks.request_stop) {
      l->hooks.request_stop(CHG_STOP_USER, l->hooks.context);
      result = CHG_ACCEPTED;
    } else if (cmd == 3 && l->hooks.acknowledge_fault) {
      l->hooks.acknowledge_fault(l->hooks.context);
      result = CHG_ACCEPTED;
    } else if (cmd == 4) {
      telemetry(l);
      result = CHG_ACCEPTED;
    }
    if (result > CHG_UNSUPPORTED) result = CHG_REJECTED;
    l->have_command = 1;
    l->last_command_sequence = seq;
    l->last_command = cmd;
    l->last_ack = result;
    l->last_detail = detail;
    ack(l, seq, cmd, result, detail);
  }
}

static void decode(chg_ui_link* l, uint32_t now) {
  uint8_t raw[75];
  size_t ri = 0, wi = 0, n = l->input_length;
  uint8_t code, i;
  while (ri < n) {
    code = l->input[ri++];
    if (!code || (size_t)(code - 1) > n - ri) return;
    for (i = 1; i < code; i++) {
      if (wi >= sizeof(raw) || l->input[ri] == 0) return;
      raw[wi++] = l->input[ri++];
    }
    if (code != 255 && ri < n) {
      if (wi >= sizeof(raw)) return;
      raw[wi++] = 0;
    }
  }
  if (wi < 11 || raw[0] != 1 || raw[2] > 64 || wi != (size_t)raw[2] + 11)
    return;
  if (crc(raw, wi - 2) != g16(raw + wi - 2)) return;
  process(l, raw, now);
}

void chg_ui_init(chg_ui_link* l, const chg_ui_hooks* hooks) {
  memset(l, 0, sizeof(*l));
  if (hooks) l->hooks = *hooks;
}

void chg_ui_receive_byte(chg_ui_link* l, uint8_t b, uint32_t now) {
  if (!b) {
    if (!l->overflow && l->input_length) decode(l, now);
    l->overflow = 0;
    l->input_length = 0;
    return;
  }
  if (l->overflow) return;
  if (l->input_length >= sizeof(l->input) - 1) {
    l->overflow = 1;
    l->input_length = 0;
    return;
  }
  l->input[l->input_length++] = b;
}

void chg_ui_tick(chg_ui_link* l, uint32_t now) {
  if (!l->active) return;
  if ((uint32_t)(now - l->last_rx_ms) > CHG_UI_TIMEOUT_MS) {
    l->active = 0;
    l->have_rx = 0;
    l->have_command = 0;
    if (l->hooks.request_stop)
      l->hooks.request_stop(CHG_STOP_LINK_LOSS, l->hooks.context);
    return;
  }
  if ((uint32_t)(now - l->last_tx_ms) >= 100u) {
    l->last_tx_ms = now;
    telemetry(l);
  }
}
