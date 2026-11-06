CFLAGS ?= -O2 -Wall -Wextra

voice-keys: voice-keys.c
	$(CC) $(CFLAGS) -o $@ $< -lutil

clean:
	rm -f voice-keys

.PHONY: clean
