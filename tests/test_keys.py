"""Exercise the helper with local sockets and a fake keyd command."""

import os
from pathlib import Path
import subprocess


def test_helper_rejects_holds_and_reports_command_status(tmp_path):
    source = Path(__file__).resolve().parents[1] / "keys/voice-keys.c"
    harness = tmp_path / "helper-test.c"
    harness.write_text('''
#define main helper_main
#include "SOURCE"
#undef main
#include <assert.h>

static void expect(int fd, const char *text)
{
    char buf[64] = {0};
    assert(read(fd, buf, sizeof buf - 1) == (ssize_t)strlen(text));
    assert(strcmp(buf, text) == 0);
}

static void complete(void)
{
    for (int i = 0; paste_pid[0] > 0 && i < 1000; i++) {
        reap();
        usleep(1000);
    }
    assert(paste_pid[0] == 0);
}

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
'''.replace("SOURCE", str(source)))
    keyd = tmp_path / "keyd"
    keyd.write_text('#!/bin/sh\nexit "${VOICE_TEST_FAIL:-0}"\n')
    keyd.chmod(0o755)
    binary = tmp_path / "helper-test"
    subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-o", str(binary), str(harness), "-lutil"],
                   check=True, capture_output=True, timeout=20)
    subprocess.run([str(binary)], env={**os.environ, "PATH": str(tmp_path), "VOICE_TEST_KEYD": str(keyd)},
                   check=True, capture_output=True, timeout=5)
