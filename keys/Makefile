CFLAGS ?= -O2 -Wall -Wextra

talk-keys: talk-keys.c
	$(CC) $(CFLAGS) -o $@ $< -lutil

clean:
	rm -f talk-keys

.PHONY: clean
