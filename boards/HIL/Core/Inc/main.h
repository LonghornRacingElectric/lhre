/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.h
  * @brief          : Header for main.c file.
  *                   This file contains the common defines of the application.
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */

/* Define to prevent recursive inclusion -------------------------------------*/
#ifndef __MAIN_H
#define __MAIN_H

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
#include "stm32g4xx_hal.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */

/* USER CODE END Includes */

/* Exported types ------------------------------------------------------------*/
/* USER CODE BEGIN ET */

/* USER CODE END ET */

/* Exported constants --------------------------------------------------------*/
/* USER CODE BEGIN EC */

/* USER CODE END EC */

/* Exported macro ------------------------------------------------------------*/
/* USER CODE BEGIN EM */

/* USER CODE END EM */

/* Exported functions prototypes ---------------------------------------------*/
void Error_Handler(void);

/* USER CODE BEGIN EFP */

/* USER CODE END EFP */

/* Private defines -----------------------------------------------------------*/
#define LED_G_Pin GPIO_PIN_0
#define LED_G_GPIO_Port GPIOA
#define LED_R_Pin GPIO_PIN_1
#define LED_R_GPIO_Port GPIOA
#define LED_B_Pin GPIO_PIN_2
#define LED_B_GPIO_Port GPIOA
#define GPIO1_Pin GPIO_PIN_3
#define GPIO1_GPIO_Port GPIOA
#define SHDN_IN1_Pin GPIO_PIN_4
#define SHDN_IN1_GPIO_Port GPIOA
#define SHDN_IN1_EXTI_IRQn EXTI4_IRQn
#define SHDN_IN2_Pin GPIO_PIN_5
#define SHDN_IN2_GPIO_Port GPIOA
#define SHDN_IN2_EXTI_IRQn EXTI9_5_IRQn
#define SHDN_IN3_Pin GPIO_PIN_6
#define SHDN_IN3_GPIO_Port GPIOA
#define SHDN_IN3_EXTI_IRQn EXTI9_5_IRQn
#define GPIO2_Pin GPIO_PIN_7
#define GPIO2_GPIO_Port GPIOA
#define SHDN_CTRL1_Pin GPIO_PIN_0
#define SHDN_CTRL1_GPIO_Port GPIOB
#define SHDN_CTRL2_Pin GPIO_PIN_1
#define SHDN_CTRL2_GPIO_Port GPIOB
#define GPIO3_Pin GPIO_PIN_2
#define GPIO3_GPIO_Port GPIOB
#define SHDN_IN4_Pin GPIO_PIN_10
#define SHDN_IN4_GPIO_Port GPIOB
#define SHDN_IN4_EXTI_IRQn EXTI15_10_IRQn
#define SHDN_READBACK1_Pin GPIO_PIN_11
#define SHDN_READBACK1_GPIO_Port GPIOB
#define SHDN_READBACK2_Pin GPIO_PIN_12
#define SHDN_READBACK2_GPIO_Port GPIOB
#define PWM_OUT_Pin GPIO_PIN_13
#define PWM_OUT_GPIO_Port GPIOB
#define LDAC1_Pin GPIO_PIN_14
#define LDAC1_GPIO_Port GPIOB
#define LDAC2_Pin GPIO_PIN_15
#define LDAC2_GPIO_Port GPIOB
#define BOOT0_TRIG_Pin GPIO_PIN_9
#define BOOT0_TRIG_GPIO_Port GPIOB

/* USER CODE BEGIN Private defines */

/* USER CODE END Private defines */

#ifdef __cplusplus
}
#endif

#endif /* __MAIN_H */
