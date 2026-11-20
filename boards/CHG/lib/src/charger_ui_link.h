#ifndef CHARGER_UI_LINK_H
#define CHARGER_UI_LINK_H

/* Portable C99 integration example: no HAL, RTOS, contactor, or charger driver.
 */
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

enum {
  CHG_IDLE,
  CHG_CHECKING,
  CHG_READY,
  CHG_CHARGING,
  CHG_COMPLETE,
  CHG_FAULT,
  CHG_STOPPING
};
enum { CHG_ACCEPTED, CHG_REJECTED, CHG_BUSY, CHG_UNSUPPORTED };
enum { CHG_STOP_USER = 1, CHG_STOP_LINK_LOSS = 2, CHG_STOP_NEW_SESSION = 3 };
#define CHG_REQUIRED_READINESS 0x03ffu
#define CHG_UI_TIMEOUT_MS 1000u

typedef struct {
  uint32_t pack_mv;
  int32_t current_ma;
  int32_t power_w;
  uint16_t soc_permille, soe_permille;
  uint32_t eta_seconds;
  int16_t max_temp_deci_c;
  uint32_t energy_wh;
  uint16_t target_soc_permille, ready_mask, required_mask;
  uint32_t fault_bits;
  uint8_t state;
  uint32_t allowed_power_w;
  uint16_t cell_min_mv, cell_max_mv;
  uint8_t limit_reason;
  uint16_t profile_series;
  uint8_t profile_parallel;
  uint32_t nominal_wh, ac_input_w;
  uint16_t profile_id;
} chg_telemetry;

typedef struct {
  void (*write)(const uint8_t*, size_t, void*);
  void (*get_telemetry)(chg_telemetry*, void*);
  /* Must re-check live interlocks/limits atomically in the real controller.
     Default NULL returns UNSUPPORTED. ACCEPTED means request queued only. */
  uint8_t (*request_start)(void*);
  /* Required to enable START. Must latch a stop and revoke charge permission.
     Local protection must operate independently of this communications code. */
  void (*request_stop)(uint8_t reason, void*);
  /* UI acknowledgement only. Never clear a physical/protection fault here. */
  void (*acknowledge_fault)(void*);
  void* context;
} chg_ui_hooks;

typedef struct {
  chg_ui_hooks hooks;
  uint32_t session, last_rx_ms, last_tx_ms;
  uint16_t tx_sequence, rx_sequence, last_command_sequence;
  uint8_t active, have_rx, have_command, overflow;
  uint8_t last_command, last_ack;
  uint16_t last_detail;
  size_t input_length;
  uint8_t input[82];
} chg_ui_link;

void chg_ui_init(chg_ui_link* link, const chg_ui_hooks* hooks);
void chg_ui_receive_byte(chg_ui_link* link, uint8_t byte, uint32_t now_ms);
void chg_ui_tick(chg_ui_link* link, uint32_t now_ms);

#ifdef __cplusplus
}
#endif
#endif
