#pragma once

// Peripheral setup missing from the CubeMX configuration.

#include <atomic>
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
extern std::atomic<uint32_t> g_adc1_scans;
extern std::atomic<uint32_t> g_adc2_scans;

// Call after MX_*_Init and before starting the ADC/DAC adapters.
void ConfigureAnalog();

// Starts the ADC trigger timer once both DMA scans are running.
void StartAdcTrigger();

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
