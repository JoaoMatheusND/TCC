/*******************************************************************
 * @file module.c
 * @brief Module implementation — CPU/GPIO sampling
 *
 * @author João Matheus Nascimento Dias (joao.dias@edge.ufal.br)
 * @version 0.3
 * @date 22/10/2026
 *
 * @copyright Copyright (c) 2026
 *
 *******************************************************************/
#include "adc.h"
#include "handles/sigrok_handler.h"
#include "log.h"
#include "module.h"
#include "usb_util.h"

#include <stdint.h>

#include <hardware/structs/m33.h>
#include <hardware/structs/sio.h>
#include <hardware/sync.h>
#include <pico/platform.h>


#define ANA_MAX_MODULES      3
#define ABORT_CHECK_INTERVAL 64u

/*
 * Pacing.
 *
 * Every sample waits for its own absolute deadline on the free-running 32-bit
 * DWT cycle counter (CYCCNT, clk_sys); the next deadline is the previous one
 * plus the period, with the Q8 fraction carried separately so long captures do
 * not drift. Loop overhead and the periodic abort/ADC service only delay the
 * sample they land on — later samples catch up — instead of stretching every
 * period.
 *
 * With a long enough period (>= SERVICE_WAIT_THRESHOLD_CYCLES) the ADC FIFO is
 * also drained while waiting, but only while at least SERVICE_GUARD_CYCLES are
 * left before the deadline, so the service call never makes a sample late.
 */
#define SERVICE_WAIT_THRESHOLD_CYCLES 200u
#define SERVICE_GUARD_CYCLES          120

/* Calibration (ana_module_calibrate()): samples timed per module count, and
 * the safety margin applied to the measured loop cost. */
#define CALIBRATION_SAMPLES 512u
#define RATE_MARGIN_PCT     5u

static struct ana_module_system *modules[ANA_MAX_MODULES];
static int module_count;

/** Cycles of clk_sys per sample, Q8 fixed point (shared by all modules). */
static uint32_t cycles_per_sample_q8;

/** Measured minimum cycles per sample with 1..ANA_MAX_MODULES armed modules. */
static uint32_t min_cycles_per_sample[ANA_MAX_MODULES];

/** Set while ana_module_calibrate() runs: the sampler must not stop on a
 * disconnected USB (nothing is connected yet at boot). */
static volatile bool calibrating;

/* Pretrigger ring (ana_module_pretrigger_run()): raw SIO words, so every
 * module's bits can be extracted afterwards. */
static uint32_t pre_ring[BUFFER_SIZE];
static uint32_t pre_depth;   /**< Ring capacity used for this capture */
static uint32_t pre_count;   /**< Valid samples in the ring (<= pre_depth) */
static uint32_t pre_next;    /**< Next write index == oldest sample once full */
static uint32_t pre_trigger_word;


void ana_module_gpio_init(struct ana_module_system *config)
{
	log_debug(config->module.name, "Initializing GPIO...");

	for (int i = 0; i < config->module.pin_count; i++) {
		int pin = config->module.pin_base + i;
		gpio_init(pin);
		gpio_set_dir(pin, GPIO_IN);
		gpio_pull_down(pin);
	}

	config->capture.pending = false;
	config->capture.has_complete = false;
	config->trigger.enabled = false;

	if (module_count >= ANA_MAX_MODULES) {
		log_err(config->module.name, "Too many capture modules");
		return;
	}
	modules[module_count] = config;
	module_count++;
}

void ana_module_set_sample_rate(struct ana_module_system *config)
{
	(void)config;
	struct pulseview_sample_config *cfg = ana_sigrok_get_sample_config();

	cycles_per_sample_q8 =
		(uint32_t)(((uint64_t)clock_get_hz(clk_sys) << 8) / cfg->sample_rate_hz);
}

void ana_module_set_trigger(struct ana_module_system *config, uint8_t gpio,
			    enum ana_trigger_type type)
{
	config->trigger.gpio = gpio;
	config->trigger.type = type;
	config->trigger.enabled = true;
}

void ana_module_clear_trigger(struct ana_module_system *config)
{
	config->trigger.enabled = false;
}

void ana_module_capture_arm(struct ana_module_system *config)
{
	config->capture.pending = true;
	config->capture.has_complete = false;
}

bool ana_module_capture_is_busy(struct ana_module_system *config)
{
	return config->capture.pending && !config->capture.has_complete;
}

void ana_module_capture_abort(struct ana_module_system *config)
{
	config->capture.pending = false;
	config->capture.has_complete = true;
}

static inline bool sampler_aborted(void)
{
	/* Both calls are always made so calibration times the real cost. */
	bool aborted = !ana_usb_is_connected() || ana_usb_abort_requested();
	return aborted && !calibrating;
}

/** Start DWT->CYCCNT if it is not running (never resets a running counter,
 * which ana_occupancy_* may be using). */
static inline void dwt_cyccnt_enable(void)
{
	if ((m33_hw->dwt_ctrl & M33_DWT_CTRL_CYCCNTENA_BITS) == 0) {
		m33_hw->demcr |= M33_DEMCR_TRCENA_BITS;
		m33_hw->dwt_ctrl |= M33_DWT_CTRL_CYCCNTENA_BITS;
	}
}

/** Absolute sample deadline on DWT->CYCCNT, Q8 period split in integer
 * cycles plus a fractional carry so long captures do not drift. */
struct cyc_deadline {
	uint32_t next;
	uint32_t step;
	uint32_t frac;
	uint32_t step_frac;
};

/* Sample deadline left by ana_module_pretrigger_run() at the trigger, so the
 * first post-trigger sample keeps the pretrigger timebase (one period after
 * the trigger sample) instead of restarting it after the setup work. */
static struct cyc_deadline carry_dl;
static bool carry_dl_valid;

static inline void cyc_deadline_init(struct cyc_deadline *d, uint32_t step_q8)
{
	dwt_cyccnt_enable();
	d->step = step_q8 >> 8;
	d->step_frac = step_q8 & 0xFFu;
	d->frac = 0;
	d->next = m33_hw->dwt_cyccnt + d->step;
}

/** Busy-wait until the current deadline, then advance it by one period.
 * With `service`, drains the ADC FIFO while enough slack is left. */
static inline void __attribute__((always_inline)) cyc_deadline_wait(struct cyc_deadline *d,
								     bool service)
{
	int32_t left;

	/* Signed difference: correct across the 32-bit CYCCNT wrap. */
	while ((left = (int32_t)(d->next - m33_hw->dwt_cyccnt)) > 0) {
		if (service && left > SERVICE_GUARD_CYCLES) {
			ana_adc_capture_service();
		}
	}
	d->next += d->step;
	d->frac += d->step_frac;
	if (d->frac >= 0x100u) {
		d->frac -= 0x100u;
		d->next++;
	}
}

static inline bool trigger_hit(enum ana_trigger_type type, bool prev, bool cur)
{
	switch (type) {
	case ANA_TRIGGER_LEVEL_LOW:
		return !cur;
	case ANA_TRIGGER_LEVEL_HIGH:
		return cur;
	case ANA_TRIGGER_EDGE_RISE:
		return !prev && cur;
	case ANA_TRIGGER_EDGE_FALL:
		return prev && !cur;
	case ANA_TRIGGER_EDGE_BOTH:
		return prev != cur;
	default:
		return true;
	}
}

/** Poll the trigger GPIO until the condition matches. False on abort. */
static bool trigger_wait(const struct ana_module_trigger *trig)
{
	uint32_t check = 0;
	bool prev = gpio_get(trig->gpio);

	while (true) {
		check++;
		if (check >= ABORT_CHECK_INTERVAL) {
			check = 0;
			ana_adc_capture_service();
			if (sampler_aborted()) {
				return false;
			}
		}

		bool cur = gpio_get(trig->gpio);

		if (trigger_hit(trig->type, prev, cur)) {
			return true;
		}
		prev = cur;
	}
}

/**
 * @brief Run one capture for every armed module.
 *
 * Fills cfg->samples entries of each armed module's buffer with the GPIO
 * word shifted to pin_base and masked to the module's pin mask.
 *
 * @return false if aborted mid-capture (armed state left for the caller to
 * clear via ana_module_capture_abort()).
 *
 * Allocated in SRAM (not flash/XIP) so the sampling loop's timing does not
 * depend on flash cache state (Cap.3, Firmware B: "elimina o jitter de XIP").
 */
static bool __no_inline_not_in_flash_func(cpu_sampler_run)(void)
{
	struct pulseview_sample_config *cfg = ana_sigrok_get_sample_config();

	uint16_t *bufs[ANA_MAX_MODULES];
	uint8_t shifts[ANA_MAX_MODULES];
	uint16_t masks[ANA_MAX_MODULES];
	int n_active = 0;

	for (int i = 0; i < module_count; i++) {
		struct ana_module_system *m = modules[i];

		if (!m->capture.pending || m->capture.buffer == NULL) {
			continue;
		}
		bufs[n_active] = m->capture.buffer;
		shifts[n_active] = m->module.pin_base;
		masks[n_active] = m->module.mask;
		n_active++;
	}

	if (n_active == 0) {
		return true;
	}

	for (int i = 0; i < module_count; i++) {
		struct ana_module_system *m = modules[i];

		if (m->capture.pending && m->trigger.enabled) {
			if (!trigger_wait(&m->trigger)) {
				return false;
			}
		}
	}

	uint32_t samples = cfg->samples;
	bool service = (cycles_per_sample_q8 >> 8) >= SERVICE_WAIT_THRESHOLD_CYCLES;
	struct cyc_deadline dl;

	/* Open the analog window together with the digital one: the ADC was
	 * only armed at capture start (starting it earlier would overrun its
	 * 8-entry FIFO and slip the round-robin channel phase). */
	ana_adc_capture_kick();
	if (carry_dl_valid) {
		/* Right after a pretrigger: stay on its sample grid. The few
		 * samples whose deadline passed during setup are taken back to
		 * back, then the grid is exact again. */
		dl = carry_dl;
		carry_dl_valid = false;
	} else {
		cyc_deadline_init(&dl, cycles_per_sample_q8);
	}

	for (uint32_t s = 0; s < samples; s++) {
		cyc_deadline_wait(&dl, service);

		uint32_t word = sio_hw->gpio_in;

		for (int m = 0; m < n_active; m++) {
			bufs[m][s] = (uint16_t)((word >> shifts[m]) & masks[m]);
		}

		if ((s & (ABORT_CHECK_INTERVAL - 1u)) == 0u) {
			ana_adc_capture_service();
			if (sampler_aborted()) {
				return false;
			}
		}
	}

	for (int i = 0; i < module_count; i++) {
		struct ana_module_system *m = modules[i];

		if (m->capture.pending) {
			m->capture.pending = false;
			m->capture.has_complete = true;
		}
	}

	return true;
}

bool ana_module_capture_wait(struct ana_module_system *config)
{
	if (config->capture.pending) {
		if (!cpu_sampler_run()) {
			return false;
		}
	}

	return config->capture.has_complete;
}

bool __no_inline_not_in_flash_func(ana_module_pretrigger_run)(struct ana_module_system *config,
							       uint32_t depth)
{
	const struct ana_module_trigger *trig = &config->trigger;
	uint32_t trig_mask = 1u << trig->gpio;
	uint32_t check = 0;
	bool prev = false;
	bool first = true;
	struct cyc_deadline dl;

	if (depth == 0u) {
		return false; /* callers use trigger_wait() for no pretrigger */
	}
	if (depth > BUFFER_SIZE) {
		depth = BUFFER_SIZE;
	}
	carry_dl_valid = false;
	pre_depth = depth;
	pre_count = 0;
	pre_next = 0;

	cyc_deadline_init(&dl, cycles_per_sample_q8);

	while (true) {
		cyc_deadline_wait(&dl, false);

		uint32_t word = sio_hw->gpio_in;
		bool cur = (word & trig_mask) != 0u;

		/* Edges need a previous sample; levels may fire on the first. */
		if ((!first || trig->type == ANA_TRIGGER_LEVEL_LOW ||
		     trig->type == ANA_TRIGGER_LEVEL_HIGH) &&
		    trigger_hit(trig->type, prev, cur)) {
			pre_trigger_word = word;
			carry_dl = dl;
			carry_dl_valid = true;
			return true;
		}

		pre_ring[pre_next] = word;
		pre_next++;
		if (pre_next == depth) {
			pre_next = 0;
		}
		if (pre_count < depth) {
			pre_count++;
		}
		prev = cur;
		first = false;

		check++;
		if (check >= ABORT_CHECK_INTERVAL) {
			check = 0;
			if (sampler_aborted()) {
				return false;
			}
		}
	}
}

uint32_t ana_module_pretrigger_count(void)
{
	return pre_count + 1u;
}

uint32_t ana_module_pretrigger_copy(const struct ana_module_system *config, uint16_t *out)
{
	uint8_t shift = config->module.pin_base;
	uint16_t mask = config->module.mask;
	uint32_t idx = (pre_count < pre_depth) ? 0u : pre_next; /* oldest sample */

	for (uint32_t i = 0; i < pre_count; i++) {
		out[i] = (uint16_t)((pre_ring[idx] >> shift) & mask);
		idx++;
		if (idx == pre_depth) {
			idx = 0;
		}
	}
	out[pre_count] = (uint16_t)((pre_trigger_word >> shift) & mask);

	return pre_count + 1u;
}

void ana_module_calibrate(void)
{
	struct pulseview_sample_config *cfg = ana_sigrok_get_sample_config();
	uint32_t saved_samples = cfg->samples;
	uint32_t saved_q8 = cycles_per_sample_q8;
	bool saved_trig[ANA_MAX_MODULES];

	dwt_cyccnt_enable();
	calibrating = true;
	cfg->samples = CALIBRATION_SAMPLES;
	cycles_per_sample_q8 = 0; /* deadlines always in the past: run flat out */

	for (int i = 0; i < module_count; i++) {
		saved_trig[i] = modules[i]->trigger.enabled;
		modules[i]->trigger.enabled = false;
	}

	for (int n = 1; n <= module_count; n++) {
		for (int i = 0; i < n; i++) {
			ana_module_capture_arm(modules[i]);
		}

		uint32_t t0 = m33_hw->dwt_cyccnt;
		cpu_sampler_run();
		uint32_t elapsed = m33_hw->dwt_cyccnt - t0;

		min_cycles_per_sample[n - 1] =
			(elapsed + CALIBRATION_SAMPLES - 1u) / CALIBRATION_SAMPLES;
	}

	for (int i = 0; i < module_count; i++) {
		modules[i]->trigger.enabled = saved_trig[i];
		modules[i]->capture.pending = false;
		modules[i]->capture.has_complete = false;
	}

	cfg->samples = saved_samples;
	cycles_per_sample_q8 = saved_q8;
	calibrating = false;
}

uint32_t ana_module_max_sample_rate(int n_active)
{
	if (n_active < 1) {
		n_active = 1;
	} else if (n_active > ANA_MAX_MODULES) {
		n_active = ANA_MAX_MODULES;
	}

	uint32_t cycles = min_cycles_per_sample[n_active - 1];

	if (cycles == 0u) {
		return UINT32_MAX; /* not calibrated */
	}
	cycles += (cycles * RATE_MARGIN_PCT + 99u) / 100u;

	return clock_get_hz(clk_sys) / cycles;
}

void ana_module_ring_init(struct ana_module_ring *ring, uint16_t *buffer, uint32_t size)
{
	ring->buffer = buffer;
	ring->mask = size - 1u;
	ring->write_idx = 0;
	ring->read_idx = 0;
	ring->overflow = false;
}

uint32_t ana_module_ring_pop(struct ana_module_ring *ring, uint16_t *out, uint32_t max)
{
	__dmb();
	uint32_t w = ring->write_idx;
	uint32_t r = ring->read_idx;
	uint32_t avail = w - r; /* unsigned subtraction: correct across wraparound */
	uint32_t n = (avail < max) ? avail : max;

	for (uint32_t i = 0; i < n; i++) {
		out[i] = ring->buffer[(r + i) & ring->mask];
	}

	__dmb();
	ring->read_idx = r + n;
	return n;
}

/**
 * Allocated in SRAM for the same reason as cpu_sampler_run(): the pacing
 * must not depend on flash/XIP cache state.
 */
void __no_inline_not_in_flash_func(ana_module_dualcore_sample_run)(struct ana_module_system *config,
								     struct ana_module_ring *ring)
{
	uint8_t shift = config->module.pin_base;
	uint16_t mask = config->module.mask;
	uint32_t ring_capacity = ring->mask + 1u;
	uint32_t check = 0;
	struct cyc_deadline dl;

	if (config->trigger.enabled) {
		if (!trigger_wait(&config->trigger)) {
			return;
		}
	}

	cyc_deadline_init(&dl, cycles_per_sample_q8);

	while (true) {
		cyc_deadline_wait(&dl, false);

		uint32_t word = sio_hw->gpio_in;
		uint32_t w = ring->write_idx;

		if ((w - ring->read_idx) >= ring_capacity) {
			/* Core 0 fell behind: stop instead of silently dropping
			 * samples, so the host sees an aborted capture. */
			ring->overflow = true;
			return;
		}
		ring->buffer[w & ring->mask] = (uint16_t)((word >> shift) & mask);
		__dmb();
		ring->write_idx = w + 1u;

		check++;
		if (check >= ABORT_CHECK_INTERVAL) {
			check = 0;
			if (sampler_aborted()) {
				return;
			}
		}
	}
}
