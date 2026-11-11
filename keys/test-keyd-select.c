/*
 * Check the language keys with keyd's real key handling and the required
 * configuration: during a hold, the bindings that voice-keys makes send no
 * key, and `keyd listen` reports them. Before the bindings and after a reset,
 * Super+Space reaches the desktop.
 */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "config.h"
#include "keyboard.h"
#include "keys.h"

static char log_buf[4096];

static void send_key(uint8_t code, uint8_t state)
{
	snprintf(log_buf + strlen(log_buf), sizeof log_buf - strlen(log_buf),
		 "key %s %s\n", KEY_NAME(code), state ? "down" : "up");
}

static void on_layer_change(const struct keyboard *kbd, const struct layer *layer, uint8_t on)
{
	(void)kbd;
	snprintf(log_buf + strlen(log_buf), sizeof log_buf - strlen(log_buf),
		 "listen %c%s\n", on ? '+' : '-', layer->name);
}

static struct keyboard *kbd;

static void press(int code, int down, int at)
{
	struct key_event event = {.code = code, .pressed = down, .timestamp = at};
	kbd_process_events(kbd, &event, 1);
}

static void tap(int code, int at)
{
	press(code, 1, at);
	press(code, 0, at + 50);
}

static void expect(const char *text)
{
	if (strcmp(log_buf, text) != 0) {
		fprintf(stderr, "expected:\n%sgot:\n%s", text, log_buf);
		exit(1);
	}
	log_buf[0] = 0;
}

int main(int argc, char **argv)
{
	static struct config config;
	assert(argc == 2);
	assert(config_parse(&config, argv[1]) >= 0);
	struct output output = {.send_key = send_key, .on_layer_change = on_layer_change};
	kbd = new_keyboard(&config, &output);

	press(KEYD_LEFTMETA, 1, 0);
	expect("listen +overlay\nkey leftmeta down\n");
	assert(!kbd_eval(kbd, "overlay.space = layer(meta)"));
	assert(!kbd_eval(kbd, "overlay.right = layer(meta)"));
	assert(!kbd_eval(kbd, "overlay.left = layer(overlay)"));
	tap(KEYD_SPACE, 500);
	tap(KEYD_RIGHT, 600);
	expect("listen +meta\nlisten -meta\nlisten +meta\nlisten -meta\n");
	tap(KEYD_LEFT, 700);
	expect("listen +overlay\nlisten -overlay\n");
	press(KEYD_LEFTMETA, 0, 900);
	assert(!kbd_eval(kbd, "reset"));
	log_buf[0] = 0;

	press(KEYD_LEFTMETA, 1, 1000);
	tap(KEYD_SPACE, 1500);
	press(KEYD_LEFTMETA, 0, 1600);
	assert(strstr(log_buf, "key space down\nkey space up\n"));
	puts("Language keys: bound keys send nothing, a reset restores Super+Space.");
	return 0;
}
