/*
 * voice-keys: tell the voice daemon when the launch key is held, and nothing
 * more.
 *
 * keyd holds the keyboards, so no other program sees the raw keys. This helper
 * reads two things that keyd offers to its group:
 *
 *   - `keyd listen`, which prints "+overlay" and "-overlay" when the layer of
 *     the launch key goes on and off;
 *   - the "keyd virtual keyboard" device, which shows each key that keyd
 *     sends. While the launch key is held alone, keyd sends nothing.
 *
 * Each client on the socket receives one line per change:
 *
 *   down   the launch key went down (the layer went on)
 *   key    another key was pressed during this hold (sent once per hold)
 *   up     the launch key went up (the layer went off)
 *
 * No key names or codes leave this program. A client can send "paste", and
 * the helper then makes keyd send the paste chord.
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
#define RETRY_MS 2000

static const char *layer = "overlay";
static const char *chord = "S-insert";

static int server = -1;
static int clients[MAX_CLIENTS];
static char client_buf[MAX_CLIENTS][64];
static size_t client_len[MAX_CLIENTS];

static int listen_fd = -1;   /* pty master of `keyd listen` */
static pid_t listen_pid = -1;
static char line[256];
static size_t line_len;

static int keyboard = -1;

static int held;             /* the layer is on */
static int keyed;            /* a key came during this hold */

static void say(const char *message)
{
	size_t n = strlen(message);
	for (int i = 0; i < MAX_CLIENTS; i++) {
		if (clients[i] < 0)
			continue;
		if (send(clients[i], message, n, MSG_NOSIGNAL | MSG_DONTWAIT) != (ssize_t)n) {
			close(clients[i]);
			clients[i] = -1;
		}
	}
}

static void release(void)
{
	if (held) {
		held = 0;
		say("up\n");
	}
}

/* keyd prints "+layer" and "-layer"; a line such as "/main" resets them. */
static void on_listen_line(const char *text)
{
	size_t n = strlen(layer);
	if ((text[0] == '+' || text[0] == '-') && strncmp(text + 1, layer, n) == 0 &&
	    text[1 + n] == '\0') {
		if (text[0] == '+' && !held) {
			held = 1;
			keyed = 0;
			say("down\n");
		} else if (text[0] == '-') {
			release();
		}
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
		execlp("keyd", "keyd", "listen", (char *)NULL);
		_exit(127);
	}
	fcntl(master, F_SETFD, FD_CLOEXEC);
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
		if (events[i].type == EV_KEY && events[i].value == 1 && held && !keyed) {
			keyed = 1;
			say("key\n");
		}
	}
}

static void paste(void)
{
	pid_t pid = fork();
	if (pid == 0) {
		execlp("keyd", "keyd", "do", chord, (char *)NULL);
		_exit(127);
	}
}

static void read_client(int i)
{
	char *buf = client_buf[i];
	ssize_t got = recv(clients[i], buf + client_len[i], sizeof client_buf[i] - client_len[i], 0);
	if (got <= 0) {
		close(clients[i]);
		clients[i] = -1;
		client_len[i] = 0;
		return;
	}
	client_len[i] += got;
	char *end;
	while ((end = memchr(buf, '\n', client_len[i]))) {
		*end = '\0';
		if (strcmp(buf, "paste") == 0)
			paste();
		size_t used = end + 1 - buf;
		memmove(buf, end + 1, client_len[i] - used);
		client_len[i] -= used;
	}
	if (client_len[i] == sizeof client_buf[i]) {   /* a line that long is not ours */
		close(clients[i]);
		clients[i] = -1;
		client_len[i] = 0;
	}
}

static void accept_client(void)
{
	int fd = accept4(server, NULL, NULL, SOCK_CLOEXEC | SOCK_NONBLOCK);
	if (fd < 0)
		return;
	for (int i = 0; i < MAX_CLIENTS; i++) {
		if (clients[i] < 0) {
			clients[i] = fd;
			client_len[i] = 0;
			return;   /* a client that comes during a hold starts at the next one */
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
	server = open_server(socket_path);
	if (server < 0)
		return 1;

	long retry_at = 0;
	for (;;) {
		while (waitpid(-1, NULL, WNOHANG) > 0)
			;
		if (now_ms() >= retry_at) {
			if (listen_fd < 0)
				start_listen();
			if (keyboard < 0)
				open_keyboard();
			retry_at = now_ms() + RETRY_MS;
		}

		struct pollfd fds[3 + MAX_CLIENTS];
		int n = 0;
		fds[n++] = (struct pollfd){.fd = server, .events = POLLIN};
		fds[n++] = (struct pollfd){.fd = listen_fd, .events = POLLIN};
		fds[n++] = (struct pollfd){.fd = keyboard, .events = POLLIN};
		for (int i = 0; i < MAX_CLIENTS; i++)
			fds[n++] = (struct pollfd){.fd = clients[i], .events = POLLIN};

		int missing = listen_fd < 0 || keyboard < 0;
		if (poll(fds, n, missing ? RETRY_MS : -1) < 0 && errno != EINTR)
			return 1;

		if (fds[1].revents)
			read_listen();
		if (fds[2].revents)
			read_keyboard();
		if (fds[0].revents)
			accept_client();
		for (int i = 0; i < MAX_CLIENTS; i++)
			if (fds[3 + i].revents && clients[i] >= 0)
				read_client(i);
	}
}
