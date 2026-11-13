#include "board_safe_state.hpp"

namespace vcu_board {

void ForceSafeOutputs() {
  // BSRR: low half sets, high half resets; one write per port is atomic.
  GPIOE->BSRR = (GPIO_PIN_12 | GPIO_PIN_13) |
                (static_cast<uint32_t>(GPIO_PIN_14 | GPIO_PIN_15) << 16);
  GPIOD->BSRR = static_cast<uint32_t>(GPIO_PIN_8 | GPIO_PIN_9) << 16;
  GPIOB->BSRR = static_cast<uint32_t>(GPIO_PIN_7) << 16;
}

}  // namespace vcu_board

// C entry for the CubeMX fault handlers and Error_Handler (USER CODE).
extern "C" void vcu_board_force_safe_outputs(void) {
  vcu_board::ForceSafeOutputs();
}
