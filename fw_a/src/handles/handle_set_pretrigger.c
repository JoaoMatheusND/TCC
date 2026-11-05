#include "handles/handles_internal.h"

void handle_set_pretrigger(void)
{
	struct sigrok_handler *self = ana_sigrok_get_self();
	long value = strtol((char *)&self->cmd_str[1], &self->end_ptr, 10);

	/* Negative depths would wrap to a huge uint32 (clamped to the maximum
	 * pretrigger instead of none). */
	if (self->end_ptr == (char *)&self->cmd_str[1] || self->end_ptr == NULL ||
	    *self->end_ptr != '\0' || value < 0) {
		log_warn("sigrok", "Invalid pretrigger depth: %s", &self->cmd_str[1]);
		self->response[0] = '\0';
		return;
	}

	uint32_t depth = (uint32_t)value;

	self->pretrigger_samples = depth;
	log_inf("sigrok", "Pretrigger samples: %lu", (unsigned long)depth);
}
