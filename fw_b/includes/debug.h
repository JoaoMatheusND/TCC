/*******************************************************************
 * @file debug.h
 *
 * @brief Debug mode configuration
 * @author João Matheus Nascimento Dias (joao.dias@edge.ufal.br)
 * @version 0.1
 * @date 28/01/2026
 *
 * @copyright Copyright (c) 2025
 *
 *******************************************************************/

 #ifndef DEBUG_H
 #define DEBUG_H

 /**
  * @brief Debug mode flag
  * 
  * @note Set to 1 to enable debug messages, 0 to disable at log.c
  *
  */
 #define DEBUG 0

 /**
 * @brief Maximum number of samples to capture in debug mode
 *
 */
#define MAX_SAMPLES_FOR_DEBUG 1024

/**
 * @brief Enable log_inf/log_warn/log_err output.
 *
 * stdio is disabled in CMakeLists.txt (pico_enable_stdio_uart/usb 0), so the
 * printf calls would only burn cycles formatting into nowhere, inside command
 * handlers and capture paths. Off unless DEBUG is set or overridden.
 */
#ifndef ANA_LOG_ENABLED
#define ANA_LOG_ENABLED DEBUG
#endif

 #endif /* DEBUG_H */