/*
 * voice-keys: tell the voice daemon when the launch key is held, and nothing
 * more.
 *
 * keyd holds the keyboards, so no other program sees the raw keys. This helper
 * reads three things:
 *
 *   - `keyd listen`, which prints "+overlay" and "-overlay" when the layer of
 *     the launch key goes on and off;
 *   - the "keyd virtual keyboard" device, which shows each key that keyd
 *     sends. While the launch key is held alone, keyd sends only modifiers;
 *   - the mice and touchpads, only during a hold: with Super held, a click
 *     or a drag is a combination too (GNOME moves a window with Super+drag).
 *
 * Each client on the socket receives one line per change:
 *
 *   down      the launch key went down (the layer went on)
 *   key       another key was pressed during this hold (sent once per hold)
 *   next      Space or Right while the selection keys are bound
 *   previous  Left while the selection keys are bound
 *   up        the launch key went up (the layer went off)
 *
 * No other key names or codes leave this program. A client can send:
 *
 *   paste   make keyd send the paste chord, only while the layer is off.
 *           The reply is "held", "pasted" (keyd completed), or "failed".
 *   select  for the rest of this hold, keyd keeps Space, Left and Right with
 *           the launch key from the desktop. They activate a layer instead,
 *           which `keyd listen` reports and no application sees. The release
 *           drops these bindings again, and so does every start of the helper.
 *
 * systemd passes the listening socket (socket activation). Without it, give
 * --socket PATH, for a test.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <linux/input.h>
#include <poll.h>
#include <pty.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define KEYBOARD_NAME "keyd virtual keyboard"
#define MAX_CLIENTS 4
#define MAX_POINTERS 8
#define RETRY_MS 2000

static const char *layer = "overlay";
static const char *chord = "S-insert";
static const char *guarded_keyd = "/usr/local/libexec/voice-keyd";

static int server = -1;
static int clients[MAX_CLIENTS];
static char client_buf[MAX_CLIENTS][64];
static size_t client_len[MAX_CLIENTS];
static pid_t paste_pid[MAX_CLIENTS];
static sigset_t original_signals;

static int listen_fd = -1;   /* pty master of `keyd listen` */
static pid_t listen_pid = -1;
static char line[256];
static size_t line_len;

static int keyboard = -1;
static int pointers[MAX_POINTERS];   /* open during a hold only */
static int pointer_count;

static int held;             /* the layer is on */
static int depth;            /* activations of the layer: the launch key, and Left while selecting */
static int keyed;            /* a key came during this hold */
static int selecting;        /* the selection keys are bound in keyd */

/* The slot stays taken until its paste command ends: a new client must not
 * receive the result of the old one, nor start a second paste beside it. */
static void close_client(int i)
{
	close(clients[i]);
	clients[i] = -1;
	client_len[i] = 0;
}

static void reply(int i, const char *message)
{
	size_t n = strlen(message);
	if (clients[i] >= 0 &&
	    send(clients[i], message, n, MSG_NOSIGNAL | MSG_DONTWAIT) != (ssize_t)n)
		close_client(i);
}

static void say(const char *message)
{
	for (int i = 0; i < MAX_CLIENTS; i++)
		reply(i, message);
}

static int has_button(int fd)
{
	unsigned long bits[KEY_MAX / (8 * sizeof(long)) + 1] = {0};
	if (ioctl(fd, EVIOCGBIT(EV_KEY, sizeof bits), bits) < 0)
		return 0;
	return (bits[BTN_LEFT / (8 * sizeof(long))] >> (BTN_LEFT % (8 * sizeof(long)))) & 1;
}

static void open_pointers(void)
{
	DIR *dir = opendir("/dev/input");
	if (!dir)
		return;
	struct dirent *entry;
	while ((entry = readdir(dir)) && pointer_count < MAX_POINTERS) {
		if (strncmp(entry->d_name, "event", 5) != 0)
			continue;
		char path[300];
		snprintf(path, sizeof path, "/dev/input/%s", entry->d_name);
		int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
		if (fd < 0)
			continue;
		char name[256] = "";
		ioctl(fd, EVIOCGNAME(sizeof name), name);
		if (strcmp(name, KEYBOARD_NAME) != 0 && has_button(fd))
			pointers[pointer_count++] = fd;
		else
			close(fd);
	}
	closedir(dir);
}

static void close_pointers(void)
{
	while (pointer_count > 0)
		close(pointers[--pointer_count]);
}

/* Run the guarded keyd with these arguments; the main loop reaps the child. */
static pid_t run_keyd(char *const argv[])
{
	pid_t pid = fork();
	if (pid == 0) {
		sigprocmask(SIG_SETMASK, &original_signals, NULL);
		alarm(2);
		int null = open("/dev/null", O_WRONLY | O_CLOEXEC);   /* keyd prints "Success" */
		if (null >= 0)
			dup2(null, STDOUT_FILENO);
		execv(guarded_keyd, argv);
		_exit(127);
	}
	return pid;
}

/* Space and Right activate keyd's meta layer, which the launch key already
 * holds, so keyd sends no key. Left activates the launch key's own layer a
 * second time. `keyd listen` reports both. */
static void select_keys(void)
{
	if (!held || selecting)
		return;
	char space[64], right[64], left[96];
	snprintf(space, sizeof space, "%s.space = layer(meta)", layer);
	snprintf(right, sizeof right, "%s.right = layer(meta)", layer);
	snprintf(left, sizeof left, "%s.left = layer(%s)", layer, layer);
	char *argv[] = {"voice-keyd", "bind", space, right, left, NULL};
	if (run_keyd(argv) > 0)
		selecting = 1;
}

/* Drops every binding made at run time, also bindings of others. */
static void reset_keys(void)
{
	char *argv[] = {"voice-keyd", "bind", "reset", NULL};
	run_keyd(argv);
	selecting = 0;
}

static void release(void)
{
	depth = 0;
	if (held) {
		held = 0;
		close_pointers();
		if (selecting)
			reset_keys();
		say("up\n");
	}
}

/* keyd prints "+layer" and "-layer"; a line such as "/main" resets them. */
static void on_listen_line(const char *text)
{
	size_t n = strlen(layer);
	if ((text[0] == '+' || text[0] == '-') && strncmp(text + 1, layer, n) == 0 &&
	    text[1 + n] == '\0') {
		if (text[0] == '+') {
			depth++;
			if (!held) {
				held = 1;
				keyed = 0;
				say("down\n");
				open_pointers();
			} else if (selecting) {
				say("previous\n");
			}
		} else if (depth > 1) {
			depth--;         /* Left went up; the launch key is still down */
		} else {
			release();
		}
	} else if (selecting && strcmp(text, "+meta") == 0) {
		say("next\n");
	} else if (text[0] == '/') {
		release();
	}
}

static void start_listen(void)
{
	int master;
	pid_t pid = forkpty(&master, NULL, NULL, NULL);
	if (pid < 0) {
		perror("voice-keys: forkpty");
		return;
	}
	if (pid == 0) {
		sigprocmask(SIG_SETMASK, &original_signals, NULL);
		execlp("keyd", "keyd", "listen", (char *)NULL);
		_exit(127);
	}
	fcntl(master, F_SETFD, FD_CLOEXEC);
	fcntl(master, F_SETFL, O_NONBLOCK);
	listen_fd = master;
	listen_pid = pid;
	line_len = 0;
}

static void stop_listen(void)
{
	if (listen_pid > 0)
		kill(listen_pid, SIGTERM);
	if (listen_fd >= 0)
		close(listen_fd);
	listen_fd = -1;
	listen_pid = -1;
	release();
}

static void read_listen(void)
{
	char buf[512];
	ssize_t got = read(listen_fd, buf, sizeof buf);
	if (got < 0 && (errno == EAGAIN || errno == EINTR))
		return;
	if (got <= 0) {
		fprintf(stderr, "voice-keys: keyd listen ended\n");
		stop_listen();
		return;
	}
	for (ssize_t i = 0; i < got; i++) {
		char c = buf[i];
		if (c == '\r')
			continue;
		if (c != '\n') {
			if (line_len < sizeof line - 1)
				line[line_len++] = c;
			continue;
		}
		line[line_len] = '\0';
		line_len = 0;
		on_listen_line(line);
	}
}

static void open_keyboard(void)
{
	DIR *dir = opendir("/dev/input");
	if (!dir)
		return;
	struct dirent *entry;
	while ((entry = readdir(dir))) {
		if (strncmp(entry->d_name, "event", 5) != 0)
			continue;
		char path[300];
		snprintf(path, sizeof path, "/dev/input/%s", entry->d_name);
		int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
		if (fd < 0)
			continue;
		char name[256] = "";
		if (ioctl(fd, EVIOCGNAME(sizeof name), name) >= 0 &&
		    strcmp(name, KEYBOARD_NAME) == 0) {
			keyboard = fd;
			break;
		}
		close(fd);
	}
	closedir(dir);
}

static int modifier(int code)
{
	switch (code) {
	case KEY_LEFTCTRL: case KEY_RIGHTCTRL:
	case KEY_LEFTSHIFT: case KEY_RIGHTSHIFT:
	case KEY_LEFTALT: case KEY_RIGHTALT:
	case KEY_LEFTMETA: case KEY_RIGHTMETA:
		return 1;
	}
	return 0;
}

static void read_keyboard(void)
{
	struct input_event events[64];
	ssize_t got = read(keyboard, events, sizeof events);
	if (got < 0 && errno == EAGAIN)
		return;
	if (got <= 0) {
		fprintf(stderr, "voice-keys: keyboard gone\n");
		close(keyboard);
		keyboard = -1;
		return;
	}
	for (size_t i = 0; i < (size_t)got / sizeof events[0]; i++) {
		/* A modifier alone is not another key: a combination always has a
		 * key that is not a modifier. keyd itself sends modifiers during a
		 * hold. The layer is "overlay:M", so it sends Super, and at the
		 * release it taps Ctrl, so that GNOME does not open the overview. */
		int code = events[i].code;
		if (modifier(code))
			continue;
		if (events[i].type == EV_KEY && events[i].value == 1 && held && !keyed) {
			keyed = 1;
			say("key\n");
		}
	}
}

/* A mouse button during a hold. Touch and tool events of a touchpad are no
 * click; its buttons are. */
static void read_pointer(int i)
{
	struct input_event events[64];
	ssize_t got = read(pointers[i], events, sizeof events);
	if (got <= 0) {
		if (got < 0 && errno == EAGAIN)
			return;
		close(pointers[i]);
		pointers[i] = pointers[--pointer_count];
		return;
	}
	for (size_t j = 0; j < (size_t)got / sizeof events[0]; j++) {
		int code = events[j].code;
		if (events[j].type == EV_KEY && events[j].value == 1 &&
		    code >= BTN_LEFT && code <= BTN_TASK && held && !keyed) {
			keyed = 1;
			say("key\n");
		}
	}
}

static void paste(int i)
{
	/* A layer event can arrive after the main ppoll returned. */
	struct pollfd source = {.fd = listen_fd, .events = POLLIN};
	while (listen_fd >= 0 && poll(&source, 1, 0) > 0)
		read_listen();
	if (clients[i] < 0)
		return;
	if (held) {
		reply(i, "held\n");
		return;
	}
	if (listen_fd < 0 || paste_pid[i] > 0) {
		reply(i, "failed\n");
		return;
	}
	pid_t pid = fork();
	if (pid == 0) {
		sigprocmask(SIG_SETMASK, &original_signals, NULL);
		alarm(2);   /* A stalled command must not paste much later. */
		execl(guarded_keyd, "voice-keyd", "do-unless", layer, chord, (char *)NULL);
		_exit(127);
	}
	if (pid < 0)
		reply(i, "failed\n");
	else
		paste_pid[i] = pid;
}

static void child_exited(int signum)
{
	(void)signum;   /* ppoll returns; the main loop collects the status. */
}

static void reap(void)
{
	int status;
	pid_t pid;
	while ((pid = waitpid(-1, &status, WNOHANG)) > 0) {
		for (int i = 0; i < MAX_CLIENTS; i++) {
			if (paste_pid[i] == pid) {
				paste_pid[i] = 0;
				const char *result = "failed\n";
				if (WIFEXITED(status)) {
					if (WEXITSTATUS(status) == 0) result = "pasted\n";
					if (WEXITSTATUS(status) == 2) result = "held\n";
				}
				reply(i, result);
			}
		}
	}
}

static void read_client(int i)
{
	char *buf = client_buf[i];
	ssize_t got = recv(clients[i], buf + client_len[i], sizeof client_buf[i] - client_len[i], 0);
	if (got <= 0) {
		close_client(i);
		return;
	}
	client_len[i] += got;
	char *end;
	while ((end = memchr(buf, '\n', client_len[i]))) {
		*end = '\0';
		if (strcmp(buf, "paste") == 0)
			paste(i);
		else if (strcmp(buf, "select") == 0)
			select_keys();
		size_t used = end + 1 - buf;
		memmove(buf, end + 1, client_len[i] - used);
		client_len[i] -= used;
	}
	if (client_len[i] == sizeof client_buf[i])     /* a line that long is not ours */
		close_client(i);
}

static void accept_client(void)
{
	int fd = accept4(server, NULL, NULL, SOCK_CLOEXEC | SOCK_NONBLOCK);
	if (fd < 0)
		return;
	for (int i = 0; i < MAX_CLIENTS; i++) {
		if (clients[i] < 0 && paste_pid[i] == 0) {
			clients[i] = fd;
			/* Cancel a hold that began before this client connected. */
			if (held)
				reply(i, "down\nkey\n");
			return;
		}
	}
	close(fd);
}

static int open_server(const char *path)
{
	const char *fds = getenv("LISTEN_FDS");
	const char *pid = getenv("LISTEN_PID");
	if (fds && pid && atoi(fds) == 1 && atoi(pid) == getpid()) {
		fcntl(3, F_SETFD, FD_CLOEXEC);
		return 3;
	}
	if (!path) {
		fprintf(stderr, "voice-keys: no socket from systemd; use --socket PATH\n");
		return -1;
	}
	struct sockaddr_un addr = {.sun_family = AF_UNIX};
	if (strlen(path) >= sizeof addr.sun_path) {
		fprintf(stderr, "voice-keys: socket path longer than %zu bytes\n",
			sizeof addr.sun_path - 1);
		return -1;
	}
	strcpy(addr.sun_path, path);
	int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
	unlink(path);
	if (fd < 0 || bind(fd, (struct sockaddr *)&addr, sizeof addr) < 0 || listen(fd, 4) < 0) {
		perror("voice-keys: socket");
		return -1;
	}
	chmod(path, 0600);
	return fd;
}

static long now_ms(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return t.tv_sec * 1000L + t.tv_nsec / 1000000L;
}

int main(int argc, char **argv)
{
	const char *socket_path = NULL;
	static const struct option options[] = {
		{"socket", required_argument, 0, 's'},
		{"layer", required_argument, 0, 'l'},
		{"chord", required_argument, 0, 'c'},
		{0, 0, 0, 0},
	};
	int opt;
	while ((opt = getopt_long(argc, argv, "", options, NULL)) != -1) {
		switch (opt) {
		case 's': socket_path = optarg; break;
		case 'l': layer = optarg; break;
		case 'c': chord = optarg; break;
		default:
			fprintf(stderr, "usage: voice-keys [--socket PATH] [--layer NAME] [--chord KEYS]\n");
			return 2;
		}
	}
	for (int i = 0; i < MAX_CLIENTS; i++)
		clients[i] = -1;
	signal(SIGPIPE, SIG_IGN);
	struct sigaction action = {.sa_handler = child_exited};
	sigemptyset(&action.sa_mask);
	sigaction(SIGCHLD, &action, NULL);
	sigset_t blocked;
	sigemptyset(&blocked);
	sigaddset(&blocked, SIGCHLD);
	sigprocmask(SIG_BLOCK, &blocked, &original_signals);
	server = open_server(socket_path);
	if (server < 0)
		return 1;
	reset_keys();            /* a helper that ended during a hold left them */

	long retry_at = 0;
	for (;;) {
		reap();
		if (now_ms() >= retry_at) {
			if (listen_fd < 0)
				start_listen();
			if (keyboard < 0)
				open_keyboard();
			retry_at = now_ms() + RETRY_MS;
		}

		struct pollfd fds[3 + MAX_CLIENTS + MAX_POINTERS];
		int n = 0;
		fds[n++] = (struct pollfd){.fd = server, .events = POLLIN};
		fds[n++] = (struct pollfd){.fd = listen_fd, .events = POLLIN};
		fds[n++] = (struct pollfd){.fd = keyboard, .events = POLLIN};
		for (int i = 0; i < MAX_CLIENTS; i++)
			fds[n++] = (struct pollfd){.fd = clients[i], .events = POLLIN};
		int pointers_polled = pointer_count;
		for (int i = 0; i < pointers_polled; i++)
			fds[n++] = (struct pollfd){.fd = pointers[i], .events = POLLIN};

		int missing = listen_fd < 0 || keyboard < 0;
		struct timespec retry = {.tv_sec = RETRY_MS / 1000};
		/* Unblock SIGCHLD only inside the wait, so no completion is missed. */
		if (ppoll(fds, n, missing ? &retry : NULL, &original_signals) < 0) {
			if (errno == EINTR)
				continue;
			return 1;
		}

		/* The pointers first: reading the layer can close them. Backwards,
		 * because a closed pointer takes the place of the last one. */
		for (int i = pointers_polled - 1; i >= 0; i--)
			if (fds[3 + MAX_CLIENTS + i].revents && i < pointer_count)
				read_pointer(i);
		if (fds[2].revents)
			read_keyboard();
		if (fds[1].revents)
			read_listen();
		/* The clients before a new one: it may take the slot of one that
		 * closed, and the result of poll() is for the old one. */
		for (int i = 0; i < MAX_CLIENTS; i++)
			if (fds[3 + i].revents && clients[i] >= 0)
				read_client(i);
		if (fds[0].revents)
			accept_client();
	}
}
