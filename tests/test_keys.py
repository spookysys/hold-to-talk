"""Exercise the helper with local sockets and a fake keyd command."""

from pathlib import Path
import subprocess


SOURCE = Path(__file__).resolve().parents[1] / "keys/voice-keys.c"
PRELUDE = '''
#define main helper_main
#include "SOURCE"
#undef main
#include <assert.h>

__attribute__((unused)) static void expect(int fd, const char *text)
{
    char buf[64] = {0};
    assert(read(fd, buf, sizeof buf - 1) == (ssize_t)strlen(text));
    assert(strcmp(buf, text) == 0);
}

__attribute__((unused)) static void complete(void)
{
    for (int i = 0; paste_pid[0] > 0 && i < 1000; i++) {
        reap();
        usleep(1000);
    }
    assert(paste_pid[0] == 0);
}
'''


def run_harness(tmp_path, body, keyd_script):
    """Compile the helper with a test main() and run it with a fake keyd."""
    harness = tmp_path / "helper-test.c"
    harness.write_text((PRELUDE + body).replace("SOURCE", str(SOURCE)))
    keyd = tmp_path / "keyd"
    keyd.write_text(keyd_script)
    keyd.chmod(0o755)
    binary = tmp_path / "helper-test"
    subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-o", str(binary), str(harness), "-lutil"],
                   check=True, capture_output=True, timeout=20)
    # Keep unrelated credentials out of child processes and failure reports.
    subprocess.run([str(binary)], cwd=tmp_path,
                   env={"PATH": str(tmp_path) + ":/usr/bin:/bin", "VOICE_TEST_KEYD": str(keyd)},
                   check=True, capture_output=True, timeout=5)


def test_helper_rejects_holds_and_reports_command_status(tmp_path):
    run_harness(tmp_path, '''
int main(void)
{
    guarded_keyd = getenv("VOICE_TEST_KEYD");
    int peers[2], layers[2];
    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, peers) == 0);
    assert(pipe(layers) == 0);
    listen_fd = layers[0];
    fcntl(listen_fd, F_SETFL, O_NONBLOCK);
    clients[0] = peers[0];
    for (int i = 1; i < MAX_CLIENTS; i++) clients[i] = -1;

    held = 1;
    paste(0);
    expect(peers[1], "held\\n");
    assert(paste_pid[0] == 0);

    /* Drain a release queued after the main event loop's poll. */
    assert(write(layers[1], "-overlay\\n", 9) == 9);
    paste(0);
    expect(peers[1], "up\\n");
    assert(paste_pid[0] > 0);
    complete();
    expect(peers[1], "pasted\\n");

    setenv("VOICE_TEST_FAIL", "1", 1);
    paste(0);
    complete();
    expect(peers[1], "failed\\n");

    setenv("VOICE_TEST_FAIL", "2", 1);
    paste(0);
    complete();
    expect(peers[1], "held\\n");

    close(listen_fd);
    listen_fd = -1;
    paste(0);
    expect(peers[1], "failed\\n");
    return 0;
}
''', '#!/bin/sh\nexit "${VOICE_TEST_FAIL:-0}"\n')


def test_a_new_client_waits_for_the_old_paste_to_end(tmp_path):
    run_harness(tmp_path, '''
static int client(void)
{
    struct sockaddr_un addr = {.sun_family = AF_UNIX, .sun_path = "test.sock"};
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    assert(connect(fd, (struct sockaddr *)&addr, sizeof addr) == 0);
    accept_client();
    return fd;
}

int main(void)
{
    guarded_keyd = getenv("VOICE_TEST_KEYD");
    int layers[2];
    assert(pipe(layers) == 0);
    listen_fd = layers[0];
    fcntl(listen_fd, F_SETFL, O_NONBLOCK);
    for (int i = 0; i < MAX_CLIENTS; i++) clients[i] = -1;
    server = open_server("test.sock");
    assert(server >= 0);

    int first = client();
    assert(write(first, "paste\\n", 6) == 6);
    read_client(0);
    assert(paste_pid[0] > 0);
    close(first);              /* the daemon goes away during the paste */
    read_client(0);
    assert(clients[0] == -1);

    int second = client();
    assert(clients[0] == -1 && clients[1] >= 0);
    complete();
    char buf[16];
    assert(recv(second, buf, sizeof buf, MSG_DONTWAIT) < 0);   /* no stray result */

    client();
    assert(clients[0] >= 0);   /* the slot is free again */
    return 0;
}
''', "#!/bin/sh\nsleep 0.2\n")


def test_selection_keys_are_bound_for_one_hold(tmp_path):
    run_harness(tmp_path, '''
static void hear(const char *text)
{
    char buf[32];
    strcpy(buf, text);
    on_listen_line(buf);
}

static void calls(const char *text)
{
    while (wait(NULL) > 0)
        ;
    char buf[256] = {0};
    FILE *f = fopen("calls", "r");
    assert(f && fread(buf, 1, sizeof buf - 1, f) > 0);
    fclose(f);
    assert(strcmp(buf, text) == 0);
    remove("calls");
}

int main(void)
{
    guarded_keyd = getenv("VOICE_TEST_KEYD");
    int peers[2];
    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, peers) == 0);
    clients[0] = peers[0];
    for (int i = 1; i < MAX_CLIENTS; i++) clients[i] = -1;

    select_keys();                    /* no hold: nothing to bind */
    assert(!selecting);

    hear("+overlay");
    expect(peers[1], "down\\n");
    hear("+meta");                    /* not selecting: no signal */
    select_keys();
    calls("bind overlay.space = layer(meta) overlay.right = layer(meta) "
          "overlay.left = layer(overlay)\\n");
    hear("+meta");
    expect(peers[1], "next\\n");
    hear("-meta");
    hear("+overlay");                 /* Left */
    expect(peers[1], "previous\\n");
    hear("-overlay");                 /* Left up: still held */
    assert(held);
    hear("-overlay");                 /* the launch key up */
    expect(peers[1], "up\\n");
    calls("bind reset\\n");
    assert(!selecting && depth == 0);
    return 0;
}
''', '#!/bin/sh\necho "$@" >> calls\n')
