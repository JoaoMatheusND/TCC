#include "handles/handles_internal.h"
#include "module.h"

#include <hardware/clocks.h>
#include <hardware/vreg.h>
#include <pico/stdlib.h>

void handle_set_sample_rate(void)
{
	struct sigrok_handler *self = ana_sigrok_get_self();
	uint32_t rate = (uint32_t)strtol((char *)&self->cmd_str[1], &self->end_ptr, 10);
	if (self->end_ptr == (char *)&self->cmd_str[1] || self->end_ptr == NULL ||
	    *self->end_ptr != '\0') {
		log_warn("sigrok", "Invalid sample rate: %s", &self->cmd_str[1]);
		self->response[0] = '\0';
		return;
	}
	if (rate < SIGROK_SAMPLE_RATE_MIN) {
		rate = SIGROK_SAMPLE_RATE_MIN;
	} else if (rate > SIGROK_SAMPLE_RATE_MAX) {
#if ENABLE_OVERCLOCKING
		vreg_set_voltage(VREG_VOLTAGE_1_25);
		sleep_ms(1);
		set_sys_clock_khz(250000, true);
#endif
		rate = SIGROK_SAMPLE_RATE_MAX;
	}
	/* Firmware B samples with the CPU: refuse rates its loop cannot keep
	 * (no '*' ACK, so the host reports an error instead of mis-scaling the
	 * timebase). Uses the channels enabled so far; run_capture() checks
	 * again with the final mask. */
	int n_modules = 1 + ((self->digital_mask & DIGITAL_MASK_RS485) ? 1 : 0) +
			((self->digital_mask & DIGITAL_MASK_RS232) ? 1 : 0);
	uint32_t max_rate = ana_module_max_sample_rate(n_modules);

	if (rate > max_rate) {
		log_warn("sigrok", "Sample rate %lu Hz above sampler limit %lu Hz",
			 (unsigned long)rate, (unsigned long)max_rate);
		self->response[0] = '\0';
		return;
	}
	self->cfg.sample_rate_hz = rate;
	log_inf("sigrok", "Sample rate: %lu Hz", (unsigned long)rate);
}
