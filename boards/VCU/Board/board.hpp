#pragma once

// Rev A board bring-up pieces that the CubeMX output doesn't cover yet (see
// boards/VCU/README.md, "IOC gaps"). Each is ST HAL level; App/ sees only
// the LHAL adapters wrapped around the handles below.

#include <cstdint>

#include "main.h"
#include "vcu_io.hpp"

namespace vcu_board {

// Handles configured here (CubeMX owns hadc1/2, hdac1, hfdcan1/2, htim2).
extern TIM_HandleTypeDef htim6;  // 1 kHz ADC trigger (TRGO)
extern TIM_HandleTypeDef htim7;  // DAC sample clock (TRGO)
extern DMA_HandleTypeDef hdma_adc2;
extern DMA_HandleTypeDef hdma_dac1;

// DMA targets, one halfword per scan rank.
extern volatile uint16_t g_adc1_buffer[vcu::kAdc1Channels];
extern volatile uint16_t g_adc2_buffer[vcu::kAdc2Channels];

// Reconfigures the CubeMX ADCs into timer-triggered, 16x oversampled scans
// with circular DMA (adds the shutdown taps and VREFINT), and readies the
// DAC for timer-paced DMA. Call after the MX_*_Init functions, before
// starting the LHAL adapters.
void ConfigureAnalog();

// Starts the ADC trigger timer once both DMA scans are running.
void StartAdcTrigger();

// FDCAN details the .ioc doesn't express: the Critical bus (FDCAN2) gets
// automatic retransmission and its IRQ lines; both get a global filter
// accepting standard frames into RX FIFO0.
void ConfigureCan();

// The STDC14 virtual COM port (USART3 on PC10/PC11, AF7).
extern UART_HandleTypeDef huart3;
void ConfigureDebugUart();

// Brake comparator soft latch in the last flash page of bank 2.
uint32_t LoadFaultLatch();
void SaveFaultLatch(uint32_t value);

// BOOT0trig high long enough to charge the BOOT0 RC, then reset into the
// ST ROM bootloader (USB DFU). Never returns.
[[noreturn]] void EnterBootloader();

}  // namespace vcu_board
