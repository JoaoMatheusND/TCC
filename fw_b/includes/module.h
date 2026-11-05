/*******************************************************************
 * @file module.h
 *
 * @brief Base module for the Multi-Signal Analyzer components
 * @author João Matheus Nascimento Dias (joao.dias@edge.ufal.br)
 * @version 0.2
 * @date 04/02/2026
 *
 * @copyright Copyright (c) 2026
 *
 *******************************************************************/

#ifndef MODULE_H
#define MODULE_H

#include "handles/sigrok_handler.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <hardware/clocks.h>
#include <hardware/gpio.h>
#include <pico/error.h>
#include <pico/time.h>
#include <pico/types.h>

/**
 * @brief Size of the capture buffers, in samples.
 *
 * The CPU sampler cannot sample while it RLE-encodes and sends a chunk, so
 * every chunk boundary is a sampling gap (Firmware B). Larger chunks mean
 * fewer gaps per capture. Captures with analog channels still use 1024-sample
 * chunks (the ADC scratch buffer size).
 */
#define BUFFER_SIZE 8192

/**
 * @brief Configuration structure for each module
 *
 */
struct ana_module_config {
	uint16_t mask;     /**< Pin mask to sump protocol*/
	uint8_t pin_base;  /**< Base GPIO pin number */
	uint8_t pin_count; /**< Number of GPIO pins */
	char *name;        /**< Name of the module */
};

/**
 * @brief CPU capture state for a module.
 *
 * The capture itself is performed by a shared CPU sampler (see module.c):
 * one SIO GPIO read per sample serves every armed module at once, so the
 * per-module state is just the destination buffer and the armed/complete
 * flags.
 */
struct ana_module_capture {
	uint16_t *buffer;           /**< Destination buffer for the next capture */
	volatile bool pending;      /**< Armed, waiting for the CPU sampler to run */
	volatile bool has_complete; /**< Last capture finished */
};

/**
 * @brief CPU trigger condition polled before the sampling loop starts.
 *
 */
struct ana_module_trigger {
	bool enabled;               /**< Trigger armed for the next capture */
	uint8_t gpio;               /**< GPIO polled for the trigger condition */
	enum ana_trigger_type type; /**< Level/edge condition */
};

/**
 * @brief Aggregated state for one capture module
 *
 */
struct ana_module_system {
	struct ana_module_config module;   /**< Module configuration */
	struct ana_module_capture capture; /**< CPU capture state */
	struct ana_module_trigger trigger; /**< CPU trigger condition */
};

/**
 * @brief Initialize the module GPIOs as pulled-down inputs and register the
 * module with the shared CPU sampler.
 *
 * @param config Structure referencing the module configuration
 */
void ana_module_gpio_init(struct ana_module_system *config);

/**
 * @brief Arm the module for the next CPU capture (non-blocking).
 *
 * The actual sampling happens inside ana_module_capture_wait(): the first
 * wait call runs the shared sampler, which fills the buffers of every armed
 * module simultaneously (one GPIO read per sample).
 *
 * @param config Structure referencing the module configuration
 */
void ana_module_capture_arm(struct ana_module_system *config);

/**
 * @brief Run/wait for the CPU capture armed by ana_module_capture_arm().
 *
 * The first waited module drives the shared sampling loop; modules waited
 * afterwards return immediately since their buffer was filled in the same
 * loop.
 *
 * @param config Structure referencing the module configuration
 * @return true  Capture completed
 * @return false Aborted (host '+'/'*' or USB disconnect)
 */
bool ana_module_capture_wait(struct ana_module_system *config);

/**
 * @brief Check whether the module is armed with an unfinished capture.
 *
 * @param config Structure referencing the module configuration
 * @return true  If a capture is pending
 * @return false Otherwise
 */
bool ana_module_capture_is_busy(struct ana_module_system *config);

/**
 * @brief Abort a pending capture (clears the armed state).
 *
 * @param config Structure referencing the module configuration
 */
void ana_module_capture_abort(struct ana_module_system *config);

/**
 * @brief Set the sample rate for the CPU sampler.
 *
 * Computes the clk_sys cycle budget per sample (Q8) from the live sysclk,
 * used as the DWT-deadline period. The pacing is shared by every module (a
 * single sampling loop reads all pins).
 *
 * @param config Structure referencing the module configuration
 */
void ana_module_set_sample_rate(struct ana_module_system *config);

/**
 * @brief Arm a CPU trigger condition for the next capture.
 *
 * The shared sampler polls the GPIO until the condition matches before
 * starting the paced sampling loop.
 *
 * @param config Structure referencing the module configuration
 * @param gpio   GPIO polled for the condition
 * @param type   Level/edge condition
 */
void ana_module_set_trigger(struct ana_module_system *config, uint8_t gpio,
			    enum ana_trigger_type type);

/**
 * @brief Disarm the CPU trigger (captures start immediately).
 *
 * @param config Structure referencing the module configuration
 */
void ana_module_clear_trigger(struct ana_module_system *config);

/**
 * @brief Sample into a ring buffer until the module's trigger fires.
 *
 * Paced at the configured rate. The trigger condition is evaluated on the
 * sampled words themselves (the same samples the host receives), and the
 * last `depth` samples before the trigger are kept, so they really precede
 * it. The next cpu_sampler capture continues on the same sample grid. Call
 * ana_module_pretrigger_copy() for each module only after that capture has
 * started its first chunk (the copy takes milliseconds).
 *
 * @param config Module carrying the trigger (its GPIO must be < 32)
 * @param depth  Pretrigger samples to keep (> 0), capped at BUFFER_SIZE
 * @return false on abort/disconnect (or depth == 0)
 */
bool ana_module_pretrigger_run(struct ana_module_system *config, uint32_t depth);

/**
 * @brief Samples ana_module_pretrigger_copy() will write (pretrigger + 1).
 */
uint32_t ana_module_pretrigger_count(void);

/**
 * @brief Copy the pretrigger samples (oldest first) followed by the trigger
 * sample into `out`, shifted/masked for `config`'s pins.
 *
 * @return Number of samples written (pretrigger samples + 1)
 */
uint32_t ana_module_pretrigger_copy(const struct ana_module_system *config, uint16_t *out);

/**
 * @brief Measure the sampling loop's own cost (cycles per sample) with
 * 1..N armed modules. Call once at boot, after every module is registered.
 */
void ana_module_calibrate(void);

/**
 * @brief Highest sample rate the CPU sampler sustains with `n_active` armed
 * modules (from ana_module_calibrate(), with a safety margin).
 */
uint32_t ana_module_max_sample_rate(int n_active);

/**
 * @brief Size, in samples, of the ring buffer used by the dual-core sampling
 * variant. Must be a power of two.
 */
#define ANA_DUALCORE_RING_SIZE 8192u

/**
 * @brief Single-producer/single-consumer ring buffer for the dual-core
 * sampling variant: core 1 (producer) writes samples via
 * ana_module_dualcore_sample_run(); core 0 (consumer) drains them via
 * ana_module_ring_pop(). No locks: synchronization is through the
 * monotonically increasing write_idx/read_idx alone.
 */
struct ana_module_ring {
	uint16_t *buffer;          /**< Backing storage, ANA_DUALCORE_RING_SIZE entries */
	uint32_t mask;             /**< buffer size - 1 (power-of-two sizing) */
	volatile uint32_t write_idx; /**< Written only by the producer (core 1) */
	volatile uint32_t read_idx;  /**< Written only by the consumer (core 0) */
	volatile bool overflow;      /**< Set if the producer ever caught up to the consumer */
};

/**
 * @brief Initialize a dual-core sampling ring buffer.
 *
 * @param ring   Ring to initialize
 * @param buffer Backing storage, must hold `size` entries
 * @param size   Capacity in samples; must be a power of two
 */
void ana_module_ring_init(struct ana_module_ring *ring, uint16_t *buffer, uint32_t size);

/**
 * @brief Pop up to `max` samples from the ring into `out` (consumer side).
 *
 * @return Number of samples actually popped (0 if the ring was empty).
 */
uint32_t ana_module_ring_pop(struct ana_module_ring *ring, uint16_t *out, uint32_t max);

/**
 * @brief Dual-core sampling variant (Cap.3, Firmware B, "variante adicional").
 *
 * Runs exclusively on the calling core (intended to be core 1): samples only
 * `config`'s GPIO channels into `ring`, with no RLE encoding, USB traffic or
 * other module's ADC/trigger servicing in this loop, until the host
 * disconnects or requests an abort, or the ring overflows (ring->overflow is
 * then set and sampling stops). The consumer (core 0, via
 * ana_module_ring_pop()) is expected to drain the ring concurrently.
 *
 * If `config->trigger.enabled`, the trigger condition is awaited first (also
 * on this core, before samples are produced).
 *
 * @param config Module to sample (only its own trigger/rate config is used)
 * @param ring   Destination ring, already initialized
 */
void ana_module_dualcore_sample_run(struct ana_module_system *config,
				     struct ana_module_ring *ring);

#endif /* MODULE_H */
