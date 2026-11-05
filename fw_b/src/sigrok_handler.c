/*******************************************************************
 * @file sigrok_handler.c
 *
 * @brief Handles the Sigrok protocol communication over USB CDC.
 *
 * Protocol reference: https://github.com/pico-coder/sigrok-pico
 *
 * @author Vinicius Rafael Marques de Carvalho (vinicius.carvalho@edge.ufal.br)
 * @author João Matheus Nascimento Dias (joao.dias@edge.ufal.br)
 * @version 0.6
 * @date 10/06/2026
 *
 * @copyright Copyright (c) 2026
 *
 *******************************************************************/

#include "adc.h"
#include "capture_data.h"
#include "channels.h"
#include "handles/handles.h"
#include "handles/handles_internal.h"
#include "led.h"
#include "log.h"
#include "module.h"
#include "occupancy.h"
#include "rs232.h"
#include "rs485.h"
#include "usb_util.h"

#include <stdio.h>

#ifdef ANA_DUALCORE_SAMPLING
#include <hardware/sync.h>
#include <pico/platform.h>
#endif

/* Chunk size when analog channels are enabled: bounded by the ADC scratch
 * buffer (ADC_BUF_SIZE in adc.c). Digital-only captures use BUFFER_SIZE. */
#define CAPTURE_CHUNK_ANALOG 1024U

/* 7-bit digital groups on the wire (channels 0-6, 7-13, 14). */
#define TX_DIG_MAX_GROUPS 3u

#ifdef ANA_DUALCORE_SAMPLING
/* Samples drained from the ring per ana_sigrok_dualcore_service() call. */
#define DUALCORE_DRAIN_CHUNK 256u

static uint16_t dualcore_ring_storage[ANA_DUALCORE_RING_SIZE];
static struct ana_module_ring dualcore_ring;
/* RS485/RS232 channels are inactive whenever the dual-core path is taken
 * (see run_capture()'s gating condition), so their contribution to the
 * merged digital word is always zero; a constant zero buffer stands in for
 * their per-sample data without touching those modules from this core. */
static const uint16_t dualcore_zero_aux[DUALCORE_DRAIN_CHUNK];
static volatile bool dualcore_capture_active;
static volatile bool dualcore_producer_done;
static volatile uint32_t dualcore_bytes_sent;
#endif


static struct sigrok_handler self = {
	.num_samples = 1024,
	.digital_mask = DIGITAL_MASK_DEFAULT,
	.analog_mask = ANALOG_MASK_DEFAULT,
	.analog_channel = 0,
	.digital_channel = 0,
	.digital_bits_per_transfer = 2,
	.cmd_str_index = 0,
	.cmd_str = {0},
	.response = {0},
	.last_was_cr = false,
	.tx = {0},
	.cfg =
		{
			.sample_rate_hz = 5000,
			.samples = 1000,
		},
	.trigger_config =
		{
			.trigger_mask = 0x0000,
			/* trigger_type entries default to 0 (ANA_TRIGGER_EDGE_RISE);
			 * they are only read for channels set in trigger_mask. */
		},
};

typedef struct {
	uint8_t command;
	void (*handler)(void);
} sigrok_command_t;

struct tx_stream {
	uint32_t idx;
	uint32_t bytes_sent;
};

static uint8_t active_analog_channels[ADC_NUM_CHANNELS];
static uint8_t active_analog_count;

/* Bit offsets of the 7-channel groups that carry at least one enabled
 * channel; only those are sent (see tx_init()). */
static uint8_t tx_dig_group_shift[TX_DIG_MAX_GROUPS];
static uint8_t tx_dig_group_count;

static inline bool module_active(uint16_t module_mask)
{
	return (self.digital_mask & module_mask) != 0u;
}

void ana_send_response(const char *str)
{
	if (!ana_usb_is_connected()) {
		log_debug("sigrok", "Cannot send (disconnected): %s", str);
		return;
	}
	if (!ana_usb_write((const uint8_t *)str, strlen(str))) {
		self.response[0] = '\0';
		self.last_was_cr = false;
		self.cmd_str_index = 0;
	}
}

static bool ana_send_bytes(const uint8_t *buf, uint32_t len)
{
	if (!ana_usb_is_connected()) {
		return false;
	}
	return ana_usb_write(buf, len);
}

/*
 * @note: Digital wire format, matching libsigrok's raspberrypi-pico driver
 * (api.c bytes_per_slice / protocol.c process_slice): channels travel in
 * 7-bit groups (0-6, 7-13, 14-...), one byte (0x80 | 7 bits) per group, and
 * a group is sent only if at least one of its channels is enabled. Sending
 * every group up to the highest enabled channel would desync the driver
 * whenever a lower group is entirely disabled.
 */
static void tx_init(void)
{
	tx_dig_group_count = 0;
	for (uint8_t g = 0; g < TX_DIG_MAX_GROUPS; g++) {
		uint8_t shift = (uint8_t)(7u * g);

		if ((self.digital_mask >> shift) & 0x7Fu) {
			tx_dig_group_shift[tx_dig_group_count] = shift;
			tx_dig_group_count++;
		}
	}

	self.tx.bytes_per_dig_sample = tx_dig_group_count;
	self.digital_bits_per_transfer = self.tx.bytes_per_dig_sample;
	self.tx.active_analog_ch = ana_capture_data_get_analog_channels_count(self.analog_mask);
	self.tx.bytes_per_sample = self.tx.bytes_per_dig_sample + self.tx.active_analog_ch;
}

static inline void tx_build_active_analog_list(void)
{
	active_analog_count = 0;

	for (uint8_t i = 0; i < ADC_NUM_CHANNELS; i++) {
		if (self.analog_mask & (1u << i)) {
			active_analog_channels[active_analog_count] = i;
			active_analog_count++;
		}
	}
}

static inline void tx_put_digital(struct tx_stream *tx, uint16_t sample)
{
	for (uint8_t g = 0; g < tx_dig_group_count; g++) {
		self.tx.buf[tx->idx] = (uint8_t)(0x80u | ((sample >> tx_dig_group_shift[g]) & 0x7Fu));
		tx->idx++;
	}
}

static inline bool tx_flush(struct tx_stream *tx)
{
	if (tx->idx == 0) {
		return true;
	}

	if (!ana_send_bytes(self.tx.buf, tx->idx)) {
		return false;
	}

	tx->bytes_sent += tx->idx;
	tx->idx = 0;

	return true;
}

static inline bool tx_reserve(struct tx_stream *tx, uint32_t needed)
{
	if ((tx->idx + needed) >= TX_BUF_SIZE) {
		return tx_flush(tx);
	}

	return true;
}

/*
 * @note: Merge channels, RS485, and RS232 into a single 16-bit digital word.
 * Each module's samples are right-aligned to its pin_base:
 *   channels: bits 11-0 → channels 0-11  (bit positions 11:0)
 *   RS485:    bit  0    → shift to bit 12 (channel 12)
 *   RS232:    bits  1-0 → shift to bits 14:13 (channels 13-14)
 *             GPIO24 (pin_base+0) = channel 13, GPIO25 (pin_base+1) = channel 14
 */
static inline uint16_t merge_digital_sample(uint16_t ch_word, uint16_t rs485_word,
					     uint16_t rs232_word)
{
	uint16_t ch_bits = (uint16_t)(ch_word & 0x0FFFu);
	uint16_t rs485_bit = (uint16_t)(rs485_word & 0x0001u);
	uint16_t rs232_bits = (uint16_t)(rs232_word & 0x0003u);
	return (uint16_t)(ch_bits | (uint16_t)(rs485_bit << 12u) | (uint16_t)(rs232_bits << 13u));
}

static bool ana_send_packet_channels(const uint16_t *dig_buf, const uint16_t *rs485_buf,
				     const uint16_t *rs232_buf, uint32_t chunk_samples,
				     uint32_t *bytes_out)
{
	uint16_t cur;
	uint32_t i = 0;
	uint32_t run, repeats, mult, rep;
	struct ana_adc_module *adc = ana_adc_get_module();

	struct tx_stream tx = {
		.idx = 0,
		.bytes_sent = 0,
	};

	tx_build_active_analog_list();

	if (chunk_samples == 0) {
		*bytes_out = 0;
		return true;
	}

	if (active_analog_count > 0) {

		for (uint32_t s = 0; s < chunk_samples; s++) {

			uint16_t sample =
				(uint16_t)(merge_digital_sample(dig_buf[s], rs485_buf[s],
								rs232_buf[s]) &
					   self.digital_mask);

			uint32_t needed = self.tx.bytes_per_dig_sample + active_analog_count;

			if (!tx_reserve(&tx, needed)) {
				return false;
			}

			tx_put_digital(&tx, sample);

			for (uint8_t ch = 0; ch < active_analog_count; ch++) {

				self.tx.buf[tx.idx] =
					ana_adc_sigrok_byte(active_analog_channels[ch], s, adc);
				tx.idx++;
			}
		}

		if (!tx_flush(&tx)) {
			return false;
		}

		*bytes_out = tx.bytes_sent;

		return true;
	}

	/*
	 * @note: Digital-only path with run-length encoding (RLE). Identical consecutive
	 * samples are sent once followed by RLE count bytes (< 0x80) that repeat the
	 * previous sample, matching the libsigrok srpico general-mode decoder
	 * (raspberrypi-pico/protocol.c process_slice):
	 *   - fine:   0x30-0x4F → repeats = byte - 47   (1..32)
	 *   - coarse: 0x50-0x7F → repeats = (byte-78)*32 (64,96,..,1568)
	 *   - data:   0x80-0xFF → sample bytes (never RLE)
	 */
	if (self.tx.bytes_per_dig_sample == 0) {
		*bytes_out = 0;
		return true;
	}

	while (i < chunk_samples) {

		cur = (uint16_t)(merge_digital_sample(dig_buf[i], rs485_buf[i], rs232_buf[i]) &
				 self.digital_mask);

		run = 1;
		while (i + run < chunk_samples &&
		       (uint16_t)(merge_digital_sample(dig_buf[i + run], rs485_buf[i + run],
						       rs232_buf[i + run]) &
				  self.digital_mask) == cur) {
			run++;
		}

		/* Worst-case RLE bytes for this run: one coarse byte per 1568
		 * repeats, plus at most one partial coarse and one fine byte. */
		repeats = run - 1u;
		if (!tx_reserve(&tx, self.tx.bytes_per_dig_sample + repeats / 1568u + 2u)) {
			return false;
		}

		tx_put_digital(&tx, cur);

		while (repeats >= 64u) {
			mult = repeats / 32u;
			if (mult > 49u) {
				mult = 49u;
			}
			self.tx.buf[tx.idx] = (uint8_t)(78u + mult);
			tx.idx++;
			repeats -= mult * 32u;
		}
		while (repeats > 0u) {
			rep = (repeats > 32u) ? 32u : repeats;
			self.tx.buf[tx.idx] = (uint8_t)(47u + rep);
			tx.idx++;
			repeats -= rep;
		}

		i += run;
	}

	if (!tx_flush(&tx)) {
		return false;
	}

	*bytes_out = tx.bytes_sent;

	return true;
}

static void abort_aux_captures(bool rs485_on, bool rs232_on)
{
	if (rs485_on) {
		ana_module_capture_abort(ana_rs485_get_module());
	}
	if (rs232_on) {
		ana_module_capture_abort(ana_rs232_get_module());
	}
	ana_adc_capture_abort();
}

/*
 * @note: The CPU sampler cannot sample while this core RLE-encodes and sends
 * a chunk, so chunks are sampled and sent strictly in turn: sample chunk k,
 * then send chunk k-1 (the two buffers only keep that order). Every chunk
 * boundary is therefore a sampling gap lasting one chunk's transmission —
 * the reason digital-only chunks are as large as BUFFER_SIZE allows.
 *
 * With `pretrig`, ana_module_pretrigger_run() has just fired: the first
 * chunk is sampled immediately (on the pretrigger's sample grid), and only
 * then are the pretrigger samples + trigger sample copied into the alternate
 * buffers and sent ahead of it. The trigger, if armed, only gates the first
 * chunk.
 */
static bool capture_chunks(struct ana_module_system *config, uint32_t n_samples,
			   bool pretrig, uint32_t *total_sent)
{
	struct ana_module_system *rs485_mod = ana_rs485_get_module();
	struct ana_module_system *rs232_mod = ana_rs232_get_module();

	bool rs485_on = module_active(DIGITAL_MASK_RS485);
	bool rs232_on = module_active(DIGITAL_MASK_RS232);
	bool analog_on = self.tx.active_analog_ch > 0;
	uint32_t chunk_max = analog_on ? CAPTURE_CHUNK_ANALOG : BUFFER_SIZE;

	uint32_t remaining = n_samples;

	uint16_t *buf[2] = {config->capture.buffer, ana_channels_get_alt_buffer()};
	uint16_t *buf_rs485[2] = {rs485_mod->capture.buffer, ana_rs485_get_alt_buffer()};
	uint16_t *buf_rs232[2] = {rs232_mod->capture.buffer, ana_rs232_get_alt_buffer()};
	int cap_idx = 0;
	uint16_t *tx_buf = NULL;
	uint16_t *tx_rs485_buf = NULL;
	uint16_t *tx_rs232_buf = NULL;
	uint32_t tx_samples = 0;
	bool ok = true;

	while (remaining > 0) {
		if (!ana_usb_is_connected() || ana_usb_abort_requested()) {
			ok = false;
			break;
		}

		uint32_t chunk = (remaining < chunk_max) ? remaining : chunk_max;

		self.cfg.samples = chunk;
		config->capture.buffer = buf[cap_idx];
		rs485_mod->capture.buffer = buf_rs485[cap_idx];
		rs232_mod->capture.buffer = buf_rs232[cap_idx];

		ana_capture_data_start(config);
		if (rs485_on) {
			ana_capture_data_start(rs485_mod);
		}
		if (rs232_on) {
			ana_capture_data_start(rs232_mod);
		}
		/* Armed only; the sampler kicks it when the digital window opens. */
		if (analog_on) {
			ana_adc_capture_start(chunk, self.analog_mask);
		}

		/* One shared sampling loop fills every armed module's buffer. */
		if (!ana_capture_data_wait(config) ||
		    (rs485_on && !ana_capture_data_wait(rs485_mod)) ||
		    (rs232_on && !ana_capture_data_wait(rs232_mod))) {
			abort_aux_captures(rs485_on, rs232_on);
			ok = false;
			break;
		}

		if (analog_on && !ana_adc_capture_finish()) {
			ok = false;
			break;
		}

		ana_channels_load_simple();

		if (pretrig) {
			pretrig = false;
			tx_samples = ana_module_pretrigger_copy(config, buf[1]);
			ana_module_pretrigger_copy(rs485_mod, buf_rs485[1]);
			ana_module_pretrigger_copy(rs232_mod, buf_rs232[1]);
			tx_buf = buf[1];
			tx_rs485_buf = buf_rs485[1];
			tx_rs232_buf = buf_rs232[1];
		}

		if (tx_buf != NULL) {
			uint32_t chunk_bytes = 0;
			if (!ana_send_packet_channels(tx_buf, tx_rs485_buf, tx_rs232_buf,
						      tx_samples, &chunk_bytes)) {
				ok = false;
				break;
			}
			*total_sent += chunk_bytes;
		}

		tx_buf = buf[cap_idx];
		tx_rs485_buf = buf_rs485[cap_idx];
		tx_rs232_buf = buf_rs232[cap_idx];
		tx_samples = chunk;
		cap_idx ^= 1;
		remaining -= chunk;
	}

	/* Nothing left to sample after the trigger (num_samples fit in the
	 * pretrigger): send the pretrigger samples alone. */
	if (ok && pretrig) {
		tx_samples = ana_module_pretrigger_copy(config, buf[1]);
		ana_module_pretrigger_copy(rs485_mod, buf_rs485[1]);
		ana_module_pretrigger_copy(rs232_mod, buf_rs232[1]);
		tx_buf = buf[1];
		tx_rs485_buf = buf_rs485[1];
		tx_rs232_buf = buf_rs232[1];
	}

	if (ok && tx_buf != NULL && ana_usb_is_connected() && !ana_usb_abort_requested()) {
		uint32_t chunk_bytes = 0;
		if (ana_send_packet_channels(tx_buf, tx_rs485_buf, tx_rs232_buf, tx_samples,
					     &chunk_bytes)) {
			*total_sent += chunk_bytes;
		} else {
			ok = false;
		}
	}

	config->capture.buffer = buf[0];
	rs485_mod->capture.buffer = buf_rs485[0];
	rs232_mod->capture.buffer = buf_rs232[0];
	return ok;
}

static void send_abort_marker(void)
{
	ana_usb_write((const uint8_t *)"!!!$0+", 6U);
}

/** Modules the CPU sampler reads for the current digital mask. */
static int sampler_module_count(void)
{
	return 1 + (module_active(DIGITAL_MASK_RS485) ? 1 : 0) +
	       (module_active(DIGITAL_MASK_RS232) ? 1 : 0);
}

#ifdef ANA_DUALCORE_SAMPLING
/**
 * @brief Dual-core sampling variant of a continuous capture.
 *
 * Runs entirely on this core (core 1): starts the ring, launches the
 * exclusive sampling loop (ana_module_dualcore_sample_run(), blocking until
 * abort/disconnect), then waits for core 0 (ana_sigrok_dualcore_service())
 * to drain and RLE-encode every sample still in the ring before reporting
 * completion. This core never calls ana_module_ring_pop()/
 * ana_send_packet_channels() itself once the ring is live — only core 0
 * does, for the whole capture including the tail end — so there is exactly
 * one consumer at all times.
 */
static void run_capture_dualcore(struct ana_module_system *config)
{
	ana_channels_apply_trigger();
	ana_module_set_sample_rate(config);

	/* Core 1 only samples here; the RLE/USB work runs on core 0. */
	ana_occupancy_capture_start();
	ana_occupancy_busy_enter();

	ana_module_ring_init(&dualcore_ring, dualcore_ring_storage, ANA_DUALCORE_RING_SIZE);
	dualcore_bytes_sent = 0;
	dualcore_producer_done = false;
	__dmb();
	dualcore_capture_active = true;
	__dmb();

	ana_module_dualcore_sample_run(config, &dualcore_ring);

	__dmb();
	dualcore_producer_done = true;
	__dmb();

	/* Core 0 clears dualcore_capture_active once it has drained the ring
	 * empty past this point; wait for that so the marker below accounts
	 * for every sample actually sent. */
	while (dualcore_capture_active) {
		tight_loop_contents();
	}

	ana_occupancy_busy_exit();
	ana_occupancy_capture_end();

	if (ana_usb_abort_requested()) {
		log_inf("sigrok", "Capture aborted by host");
		return;
	}

	if (!ana_usb_is_connected()) {
		send_abort_marker();
		log_warn("sigrok", "Capture aborted: host disconnected");
		return;
	}

	/* Samples were lost: end with the abort marker, not a done marker. */
	if (dualcore_ring.overflow) {
		send_abort_marker();
		log_warn("sigrok", "Dual-core sampling ring overflow: capture aborted");
		return;
	}

	char done_marker[32];

	__dmb();
	snprintf(done_marker, sizeof(done_marker), "$%lu+", (unsigned long)dualcore_bytes_sent);
	ana_usb_write((const uint8_t *)done_marker, strlen(done_marker));
}

bool ana_sigrok_dualcore_service(void)
{
	__dmb();
	if (!dualcore_capture_active) {
		return false;
	}

	uint16_t chunk[DUALCORE_DRAIN_CHUNK];

	/* Read "done" before popping: if the ring is then empty, nothing can
	 * have been written after the producer finished. */
	__dmb();
	bool producer_done = dualcore_producer_done;
	uint32_t n = ana_module_ring_pop(&dualcore_ring, chunk, DUALCORE_DRAIN_CHUNK);

	if (n > 0) {
		uint32_t bytes_out = 0;

		if (ana_send_packet_channels(chunk, dualcore_zero_aux, dualcore_zero_aux, n,
					     &bytes_out)) {
			dualcore_bytes_sent += bytes_out;
		}
		return true;
	}

	if (producer_done) {
		__dmb();
		dualcore_capture_active = false;
		__dmb();
	}

	return false;
}
#endif /* ANA_DUALCORE_SAMPLING */

void run_capture(bool continuous)
{
	struct ana_module_system *config = ana_channels_get_module();

	tx_init();

	if (self.tx.active_analog_ch > 0) {
		uint32_t adc_rate = self.cfg.sample_rate_hz * self.tx.active_analog_ch;

		/* Above this the ADC falls behind the digital samples and the
		 * analog/digital timebases no longer match: refuse, not mislead. */
		if (adc_rate > ANA_ADC_MAX_RATE_HZ) {
			log_warn("sigrok", "Analog rate %lu Hz exceeds ADC limit",
				 (unsigned long)adc_rate);
			send_abort_marker();
			return;
		}
		ana_adc_set_rate(adc_rate);
	}

	/* The CPU sampler cannot go faster than its own loop: refuse a rate it
	 * would silently miss (the host would mis-scale the timebase). */
	if (self.cfg.sample_rate_hz > ana_module_max_sample_rate(sampler_module_count())) {
		log_warn("sigrok", "Sample rate %lu Hz above sampler limit %lu Hz",
			 (unsigned long)self.cfg.sample_rate_hz,
			 (unsigned long)ana_module_max_sample_rate(sampler_module_count()));
		send_abort_marker();
		return;
	}

#ifdef ANA_DUALCORE_SAMPLING
	/* Dual-core variant: only for a channels-only continuous capture, no
	 * trigger, no analog, no RS232/RS485 (Cap.3, Firmware B). Mirrors the
	 * scope Firmware A restricts its own hardware ping-pong DMA to. */
	if (continuous && self.trigger_config.trigger_mask == 0 &&
	    self.tx.active_analog_ch == 0 && !module_active(DIGITAL_MASK_RS485) &&
	    !module_active(DIGITAL_MASK_RS232)) {
		ana_led_set_status(LED_STATUS_CAPTURING);
		run_capture_dualcore(config);
		ana_led_set_status(LED_STATUS_CONNECTED);
		return;
	}
#endif

	ana_led_set_status(LED_STATUS_CAPTURING);

	/* Firmware B (Cap.3, M4): the whole capture — sampling, RLE and USB
	 * TX — runs on this CPU with no hardware to offload to, so the whole
	 * body counts as "busy" (contrast with Firmware A, which brackets
	 * only its RLE+TX regions). */
	ana_occupancy_capture_start();
	ana_occupancy_busy_enter();

	bool first = true;

	do {
		uint32_t total_sent = 0;
		bool pretrig = false;
		uint32_t n = self.num_samples;
		bool ok = true;

		/* The trigger gates only the start of the capture. A continuous
		 * capture re-arms every num_samples and must not wait for the
		 * trigger again, which would cut gaps into the stream. */
		bool triggered = first && self.trigger_config.trigger_mask != 0;

		first = false;

		if (triggered) {
			ana_channels_apply_trigger();
		} else {
			ana_channels_load_simple();
		}
		ana_module_set_sample_rate(config);

		/* Real pretrigger: sample into a ring while waiting for the
		 * trigger, so the samples sent before it actually precede it
		 * (the sigrok driver places its soft trigger in this stream). */
		uint32_t depth = self.pretrigger_samples;

		if (depth > BUFFER_SIZE - 1u) {
			depth = BUFFER_SIZE - 1u;
		}
		if (depth > n - 1u) {
			depth = n - 1u;
		}
		if (triggered && depth > 0u) {
			if (self.tx.active_analog_ch > 0) {
				log_warn("sigrok", "Pretrigger ignored with analog channels");
			} else {
				ok = ana_module_pretrigger_run(config, depth);
				if (ok) {
					pretrig = true;
					n -= ana_module_pretrigger_count();
					ana_channels_load_simple();
				}
			}
		}

		if (ok) {
			ok = capture_chunks(config, n, pretrig, &total_sent);
		}

		self.cfg.samples = self.num_samples;

		if (ana_usb_abort_requested()) {
			log_inf("sigrok", "Capture aborted by host");
			break;
		}

		if (!ok) {
			send_abort_marker();
			log_warn("sigrok", "Capture aborted");
			break;
		}

		char done_marker[32];

		snprintf(done_marker, sizeof(done_marker), "$%lu+", (unsigned long)total_sent);
		ana_usb_write((const uint8_t *)done_marker, strlen(done_marker));

	} while (continuous && ana_usb_is_connected());

	ana_occupancy_busy_exit();
	ana_occupancy_capture_end();

	ana_led_set_status(LED_STATUS_CONNECTED);
}

struct sigrok_trigger *ana_sigrok_get_trigger(void)
{
	return &self.trigger_config;
}

static const sigrok_command_t sigrok_commands[] = {
	{SIGROK_CMD_IDENTIFY, handle_identify},
	{SIGROK_CMD_SET_SAMPLE_RATE, handle_set_sample_rate},
	{SIGROK_CMD_SET_SAMPLE_LIMIT, handle_set_sample_limit},
	{SIGROK_CMD_GET_ANALOG_SCALE, handle_get_analog_scale},
	{SIGROK_CMD_SET_ANALOG_CHANNEL, handle_set_analog_channel},
	{SIGROK_CMD_SET_DIGITAL_CHANNEL, handle_set_digital_channel},
	{SIGROK_CMD_FIXED_CAPTURE, handle_fixed_capture},
	{SIGROK_CMD_CONTINUOUS_CAPTURE, handle_continuous_capture},
	{SIGROK_CMD_SET_PRETRIGGER, handle_set_pretrigger},
	{SIGROK_CMD_SET_TRIGGER, handle_set_trigger},
	{SIGROK_CMD_GET_CPU_OCCUPANCY, handle_get_cpu_occupancy}};

#define SIGROK_COMMAND_COUNT (sizeof(sigrok_commands) / sizeof(sigrok_commands[0]))

void ana_sigrok_handle_init(void)
{
	self.cmd_str_index = 0;
	memset(self.cmd_str, 0, sizeof(self.cmd_str));
	self.trigger_config.trigger_mask = 0;
	self.analog_mask = ANALOG_MASK_DEFAULT;
	self.digital_mask = DIGITAL_MASK_DEFAULT;
	self.pretrigger_samples = 0;
	self.last_was_cr = false;
}

void ana_sigrok_handle_process_byte(uint8_t received_command)
{
	if (received_command == '\n' && self.last_was_cr) {
		self.last_was_cr = false;
		return;
	}
	self.last_was_cr = (received_command == '\r');

	memset(self.response, 0, sizeof(self.response));
	self.response[0] = '\0';

	/* '+'/'*' set the abort flag on Core 0 the moment they arrive (see
	 * main.c). Clear it here, when the byte that raised it is consumed, so
	 * a stop arriving right after a capture command is never discarded. */
	if (received_command == '*') {
		ana_usb_clear_abort();
		ana_sigrok_handle_init();
		ana_led_set_status(LED_STATUS_OFF);
		return;
	}

	if (received_command == '+') {
		ana_usb_clear_abort();
		self.cmd_str_index = 0;
		self.last_was_cr = false;
		return;
	}

	if (received_command == '\r' || received_command == '\n') {
		bool matched = false;

		self.cmd_str[self.cmd_str_index] = '\0';
		strcpy((char *)self.response, "*");

		for (size_t i = 0; i < SIGROK_COMMAND_COUNT; i++) {
			if (self.cmd_str[0] == sigrok_commands[i].command) {
				sigrok_commands[i].handler();
				matched = true;
				break;
			}
		}

		/* Unknown command: no success ACK. */
		if (!matched) {
			self.response[0] = '\0';
		}

		if (self.response[0] != '\0') {
			ana_send_response((char *)self.response);
		}

		self.cmd_str_index = 0;

	} else if (self.cmd_str_index < (int8_t)(sizeof(self.cmd_str) - 1)) {
		self.cmd_str[self.cmd_str_index] = (char)received_command;
		self.cmd_str_index++;
	} else {
		log_warn("sigrok", "Command buffer overflow, resetting");
		self.cmd_str_index = 0;
	}
}

struct pulseview_sample_config *ana_sigrok_get_sample_config(void)
{
	return &self.cfg;
}

struct sigrok_handler *ana_sigrok_get_self(void)
{
	return &self;
}
