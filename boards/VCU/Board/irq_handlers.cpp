// IRQ handlers for peripherals configured in Board/ rather than the .ioc
// (stm32g4xx_it.c only has the ones CubeMX enabled).

#include "board.hpp"
#include "fdcan.h"

extern "C" void FDCAN2_IT0_IRQHandler(void) { HAL_FDCAN_IRQHandler(&hfdcan2); }

extern "C" void FDCAN2_IT1_IRQHandler(void) { HAL_FDCAN_IRQHandler(&hfdcan2); }

extern "C" void DMA1_Channel2_IRQHandler(void) {
  HAL_DMA_IRQHandler(&vcu_board::hdma_adc2);
}

extern "C" void DMA1_Channel3_IRQHandler(void) {
  HAL_DMA_IRQHandler(&vcu_board::hdma_dac1);
}
