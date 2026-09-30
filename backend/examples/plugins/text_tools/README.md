# Sample plugin: `text_tools`

A plugin package is a directory that is also a Python package, with an
`akasha-plugin.json` manifest at its root. This one provides one node type,
`text_tools.slugify` (`"Hello, World!"` → `"hello-world"`).

Its node code imports only `akasha.sdk`. Installing it registers the node in
the engine's Node Registry like a built-in one; no engine code changes.

The full lifecycle (install → publish → execute → disable → remove) is
covered by `tests/test_plugins.py` and `tests/integration/test_plugin_runtime.py`.
See [`docs/sdk.md` §9](../../../../docs/sdk.md) for the manifest format.
