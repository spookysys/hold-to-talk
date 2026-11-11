# Key helper

The session process cannot read input devices or inject keys. The helper reports
`down`, `key`, and `up`. A `paste` request receives `pasted`, `held`, or `failed`.
`pasted` confirms keyd command completion, not delivery to the application.

A `select` request during a hold binds Space, Left, and Right in the launch
key's layer with `keyd bind`. The keys then activate a layer instead of sending
a key: Space and Right activate keyd's `meta` layer, which the launch key holds
already, and Left activates the launch key's layer once more. `keyd listen`
reports these as `+meta` and as a second `+overlay`, and the helper reports
`next` and `previous`. No application receives the keys. Before the request,
Super+Space and the arrows reach the desktop as usual.

The release runs `keyd bind reset`, and so do every start of the helper and
systemd's stop of it. Runtime bindings live only in keyd's memory, so a keyd
restart also removes them. The reset drops every runtime binding, also one
that the user made with `keyd bind`.

The helper checks the hold before starting a paste. That check alone cannot
prevent a new hold between the check and injection. The final check therefore
runs inside keyd's event loop, in the same operation as the paste macro.
A held layer returns `held`, and the daemon retries after release.

`keyd-guard.patch` adds `do-unless LAYER EXPRESSION` to the upstream keyd release
selected in `build-keyd.sh`. The command refuses unknown layers. It checks every
configuration before it executes the expression. Existing key commands and
physical key handling are unchanged. The helper uses only `S-insert` by default.

`build-keyd.sh` downloads the source, verifies its fixed checksum, applies the
patch, and builds it. It runs upstream keyboard simulations and the guard test.
The guard test captures generated keys in memory; it does not use input devices.

The system install places this binary at `/usr/local/libexec/voice-keyd`.
`keyd-voice.conf` selects it for the existing keyd service. The distribution's
binary remains installed. The source license is installed beside voice's data.
The main README contains the uninstall steps that restore the distribution's
keyd service command.

Upstream source: <https://github.com/rvaiya/keyd>. Physical keyboard verification
is still required. The simulations do not establish hardware behavior.
