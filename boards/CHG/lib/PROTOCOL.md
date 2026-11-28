# Charger display UART protocol v1

The STM32 is the charging authority. The ESP32 is a display and request terminal.
This software has no contactor or charger output drivers. The STM32 application
must enforce its own BMS permissions, isolation monitoring, HV interlock,
temperature limits, cell voltage limits, contactor sequencing and feedback,
current/power budgets, fault latches, watchdogs, and safe shutdown. A UI stop is
not an emergency stop. CRC detects corruption; this protocol is not authenticated.

## Transport and framing

Use a dedicated 115200 baud UART, 8 data bits, no parity, 1 stop bit. Electrical
connections and any required galvanic isolation must be designed separately;
never connect this UART to HV. Both boards must use compatible logic levels.
Do not connect two push-pull TX outputs together. The ESP32 USB programming UART
must remain available; use a separately verified pin pair for the STM32 link.

Each frame is COBS encoded and ends with `00`. COBS input is the following raw
frame. All multibyte integers, including the CRC, are little endian.

| Raw offset | Size | Meaning |
| --- | --- | --- |
| 0 | 1 | Version: `01` |
| 1 | 1 | Message type |
| 2 | 1 | Payload length, 0–64 |
| 3 | 2 | Sender sequence, modulo 65536 |
| 5 | 4 | Nonzero ESP32 session ID |
| 9 | payload length | Payload |
| 9 + length | 2 | CRC-16/CCITT-FALSE over header and payload |

CRC parameters: polynomial `0x1021`, initial `0xFFFF`, no reflection, xor-out 0.
The ASCII test vector `123456789` has CRC `0x29B1`. Raw frames are at most 75
bytes; encoded frames including delimiter are at most 77 bytes. Buffers reserve
82 bytes. Oversize input is discarded until a delimiter; malformed lengths,
version, COBS, CRC, state and percentage fields are rejected. A truncated frame
cannot update the UI. The next delimiter restores frame alignment.

Each sender increments one shared sequence across all its message types. A
sequence is newer when unsigned `(incoming - previous)` is in `1..32767`.
Duplicate or older frames cannot refresh telemetry freshness or trigger control
actions. The link watchdog is based on valid incoming traffic, not byte activity.

## Session and timing

1. ESP32 boots in monitor mode and sends HELLO using a fresh nonzero random
   session ID. Call `Link::beginSession(esp_random(), millis())` after startup.
2. STM32 echoes that session in HELLO_ACK and sends a telemetry snapshot.
3. ESP32 accepts telemetry only after that ACK and only for its active session.
4. STM32 sends telemetry every 100 ms. ESP32 sends HEARTBEAT every 250 ms.
   HELLO repeats every 500 ms until acknowledged.
5. ESP32 marks data stale after 1500 ms without valid telemetry. It starts a new
   session and never sends an automatic START. Old values are for stale display
   only; they cannot enable controls.
6. STM32 revokes the UI session after 1000 ms without a newer valid HELLO,
   HEARTBEAT or COMMAND and invokes its stop callback. Replayed and malformed
   frames do not reset this deadline. It must latch a safe stop and require a
   new explicit START plus all live safety checks before any restart.
7. A new session during an existing STM32 session invokes the stop callback.
   Establishing communications or receiving a heartbeat never permits charging.

Call both `tick()` functions frequently (a 10 ms or faster communications task
is suitable) and drain received bytes without blocking the safety controller.
Elapsed-time arithmetic tolerates the 32-bit millisecond clock rollover.
Call parsing, ticking and transmission from one task or protect them against
concurrent access. UART DMA/interrupt handlers should enqueue bytes; process the
queue and application callbacks outside interrupt context. Do not block this
task on graphics rendering or long serial writes.

## Message types

| Type | Direction | Payload |
| --- | --- | --- |
| 1 HELLO | ESP → STM | Empty |
| 2 HELLO_ACK | STM → ESP | Empty |
| 3 TELEMETRY | STM → ESP | Exactly 59 bytes, below |
| 4 COMMAND | ESP → STM | command u8 + reserved argument u32 (must be zero) |
| 5 ACK | STM → ESP | request sequence u16 + command u8 + result u8 + detail u16 |
| 6 HEARTBEAT | ESP → STM | Empty |

HELLO_ACK confirms only protocol/session synchronization. It does not identify
the battery, validate hardware, authorize charging, or prove a safety function.
Unknown types are ignored. Payload schema changes require a new protocol version.

## Telemetry payload

| Payload offset | Type | Field / unit |
| --- | --- | --- |
| 0 | u32 | Pack voltage, mV |
| 4 | i32 | Pack charge current, mA; positive enters pack |
| 8 | i32 | Pack charge power, W; positive enters pack |
| 12 | u16 | SOC, permille (0–1000) |
| 14 | u16 | SOE, permille (0–1000) |
| 16 | u32 | ETA, seconds to configured target |
| 20 | i16 | Maximum battery temperature, 0.1 °C |
| 22 | u32 | Energy delivered this charge session, Wh |
| 26 | u16 | Target SOC, permille |
| 28 | u16 | Satisfied readiness bits |
| 30 | u16 | Required readiness bits |
| 32 | u32 | Active/latching fault bits |
| 36 | u8 | Controller charging state |
| 37 | u32 | Present permitted DC charging power, W (0 = no permission) |
| 41 | u16 | Minimum cell voltage, mV |
| 43 | u16 | Maximum cell voltage, mV |
| 45 | u8 | Active limiting reason |
| 46 | u16 | Active profile series cell count (0 = unknown) |
| 48 | u8 | Active profile parallel cell count (0 = unknown) |
| 49 | u32 | Profile nominal energy, Wh (0 = unknown) |
| 53 | u32 | Measured AC input power, W |
| 57 | u16 | Controller-owned profile ID (0 = unknown) |

SOC, SOE and target SOC unknown = `0xFFFF`; ETA and AC input power unknown =
`0xFFFFFFFF`; temperature unknown = `-32768`; cell voltage unknown = `0xFFFF`.
Zero SOC/SOE is valid and must display as zero. SOC, SOE and ETA must originate
from the BMS/controller estimator; the display must not invent accurate values
from pack voltage. ETA is an estimate including taper and the selected target.
`energyWh` is session energy, distinct from remaining pack energy/SOE.

Power budget and pack profile are informational controller telemetry. For a
137s4p pack and 6.6 kW charger, the controller must select a validated battery
profile and derive charge voltage/current/power limits from live BMS limits,
charger capability, and the actual AC source budget. The display cannot infer
outlet capacity. AC input power and permitted DC charging power are different
quantities; account for conversion losses and power factor in controller policy.
Do not treat 6.6 kW as always available or change a pack profile while charging.

States: 0 IDLE, 1 CHECKING, 2 READY, 3 CHARGING, 4 COMPLETE, 5 FAULT, 6 STOPPING.
The display shows COMPLETE only when the controller reports it; 100% SOC or a
zero ETA alone does not establish completion. Fault bits take visual priority
over the reported state. Stale communications take priority over live metrics.

Limiting reason: 0 unknown, 1 none, 2 AC supply, 3 charger, 4 BMS, 5 temperature,
6 voltage. A healthy taper near the voltage ceiling is not itself a fault.

## Readiness and faults

| Bit | Readiness meaning |
| --- | --- |
| 0 | Pack present |
| 1 | Charger present |
| 2 | BMS explicitly allows charging |
| 3 | HV interlock closed |
| 4 | Isolation OK |
| 5 | Temperatures within permitted limits |
| 6 | Pack/cell voltage within permitted limits |
| 7 | Contactor feedback matches controller sequence |
| 8 | Controller self checks healthy |
| 9 | Valid pack/charger/AC limits and positive charging budget |

Bits 0–9 (`0x03FF`) are the mandatory baseline for both implementations.
Additional controller requirements may use bits 10–15; all required bits must
be satisfied. The controller must not mark a physical check true because the
sensor is absent or because the display is in demo mode.

Fault bits 0–9 respectively: BMS, HV interlock, isolation, overvoltage,
overcurrent, temperature, contactor feedback, charger, controller, and UI link.
Bits 10–31 are available for agreed controller-specific diagnostics; any nonzero
fault mask blocks START. The hardware controller owns fault latching and reset.

## Commands, ACK and safety boundaries

Command values: 1 START, 2 STOP, 3 ACKNOWLEDGE_FAULT, 4 REQUEST_SNAPSHOT.
ACK result values: 0 accepted, 1 rejected, 2 busy, 3 unsupported. Detail 0 is
unspecified; the C example returns 1 for a nonzero reserved argument and 2 for
failed readiness. UI-local ACK states 254 (timeout) and 255 (none) are never sent
on the wire.

All control commands are disabled in the ESP library by default. The integrating
firmware must explicitly enable engineering controls after its bench validation.
REQUEST_SNAPSHOT is allowed in monitor mode. START additionally requires a live
session, fresh telemetry, READY state, no pending command, zero faults, a
positive allowed power budget, the full mandatory required mask, and every
required check satisfied. STM32 independently rechecks these conditions and its
application callback must atomically check live protection and budget values.

Only one command is tracked as pending. An explicit STOP supersedes any pending
START, REQUEST_SNAPSHOT or other request; the old ACK cannot complete the STOP.
Superseding does not cancel a START already received by the controller: the
controller must process the following STOP and report its actual state.
ACK must echo both the request sequence and
command. Commands are never retried automatically. After 1200 ms without an ACK,
the display reports an uncertain timeout and waits for controller telemetry;
it must not assume START or STOP succeeded. An accepted request is not a state
transition: only fresh controller telemetry can show CHARGING, STOPPING or
COMPLETE. Recovery after a timeout must not automatically restart charging.

STM32 caches the last command result and acknowledges a duplicate without
running its action again. The reserved argument must be zero. ACKNOWLEDGE_FAULT
means the user has read the message; it must never clear a protection latch,
close a contactor, or allow an automatic restart. STOP invokes the controller's
stop request; the independent emergency stop/protection path remains necessary.

The portable C example refuses START unless both start and stop hooks exist.
It implements transport and policy checks only. It is not a charger controller
or a validated safety system. Leave the start hook unset until the real STM32
application supplies protection, bounded shutdown and restart latching.

## Integration and tests

Add `boards/CHG/lib/src/charger_ui_link.c` and `.h` to the STM32 project. Populate hooks using
the MCU's UART transport, a coherent controller telemetry snapshot, and the real
controller's request queue. `request_stop(reason)` must latch stop and revoke
permission, including on link loss or session change. The example returns only
protocol acceptance; charger outputs belong to the existing STM32 state machine.

Call `chg_ui_init()`, feed queued RX bytes through `chg_ui_receive_byte()`, and
call `chg_ui_tick()` using the same monotonic millisecond clock. A missing start
hook is the safe bench integration default. Do not assign UART pins or HAL
handles from this generic example without checking the custom controller board.

Run native transport/interoperability tests with:

```sh
python3 boards/CHG/lib/test/run_native.py
```

Tests compile the STM32 peer as C99 and ESP library as C++11 with strict warnings,
exercise the real peers together, and cover the CRC reference vector, all
payload lengths, every single-bit corruption across 6500 generated frames,
default monitor policy, mask/fault checks, ACK matching, command deduplication,
stale and late traffic, oversized noise recovery, old sessions, sequence rollover,
millisecond rollover, and lost-command timeout. They do not validate physical
wiring, isolation, BMS limits, contactors, the charger or a real battery.
