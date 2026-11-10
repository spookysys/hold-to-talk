#include "daemon.c"

static int client_peer, client_socket, reply_type;
static int test_connect(void)
{
    int pair[2];
    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, pair) == 0);
    client_socket = pair[0];
    client_peer = pair[1];
    struct ipc_message reply = {.type = reply_type};
    xwrite(client_peer, &reply, sizeof reply);
    return client_socket;
}

#define ipc_connect test_connect
#define input client_input
#define reload client_reload
#define main client_main
#include "keyd.c"
#undef ipc_connect
#undef input
#undef reload
#undef main

static void client_result(int status, int expected)
{
    reply_type = status;
    char *args[] = {"do-unless", "overlay", "S-insert"};
    assert(cmd_do_unless(3, args) == expected);
    struct ipc_message request;
    xread(client_peer, &request, sizeof request);
    assert(request.type == IPC_MACRO_UNLESS);
    assert(request.timeout == 0);
    assert(request.sz == strlen("overlay\nS-insert"));
    assert(memcmp(request.data, "overlay\nS-insert", request.sz) == 0);
    close(client_peer);
    close(client_socket);
}

static int emitted;
void __wrap_vkbd_send_key(const struct vkbd *device, uint8_t code, int state)
{
    (void)device;
    (void)code;
    (void)state;
    emitted++;
}

static int request(const char *text)
{
    int pair[2];
    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, pair) == 0);
    struct ipc_message msg = {.type = IPC_MACRO_UNLESS};
    msg.sz = strlen(text);
    memcpy(msg.data, text, msg.sz);
    xwrite(pair[0], &msg, sizeof msg);
    handle_client(pair[1]);
    xread(pair[0], &msg, sizeof msg);
    close(pair[0]);
    return msg.type;
}

int main(void)
{
    struct config_ent entry = {0};
    entry.kbd = calloc(1, sizeof *entry.kbd);
    configs = &entry;
    entry.kbd->config.nr_layers = 2;
    strcpy(entry.kbd->config.layers[0].name, "main");
    strcpy(entry.kbd->config.layers[1].name, "overlay");

    entry.kbd->layer_state[1].active = 1;
    assert(request("overlay\nS-insert") == IPC_BUSY);
    assert(emitted == 0);
    entry.kbd->layer_state[1].active = 0;
    assert(request("overlay\nS-insert") == IPC_SUCCESS);
    assert(emitted == 4);
    assert(request("missing\nS-insert") == IPC_FAIL);
    assert(request("overlay") == IPC_FAIL);
    assert(emitted == 4);
    client_result(IPC_SUCCESS, 0);
    client_result(IPC_FAIL, 1);
    client_result(IPC_BUSY, 2);
    free(entry.kbd);
    puts("Guarded injection: held rejected, release accepted, unknown layer rejected.");
    return 0;
}
