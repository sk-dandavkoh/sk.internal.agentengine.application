# ADR 0004: Python libraries of plugins

Status: accepted (2026-10-05)

## Context

Plugin nodes could import only the standard library, `akasha.sdk` and,
by accident, whatever the engine happened to have installed. Real nodes need
third-party libraries (parsers, SDKs of external services). Plugins run in
the engine's interpreter (trusted code, architecture §34/§36), so a library
has one version per process. Another plugin, or the engine itself, may
already need that library.

The engine runs as a non-root container with a root-owned virtualenv. Only
the `plugins` volume is writable and survives an image upgrade. API and
worker are separate processes that share that volume.

## Decision

1. **Declared in the manifest.** `python_requirements` is a list of PEP 508
   requirements (at most 50). URLs and paths are rejected because they would
   bypass the index and the constraints and could read host files.
   `akasha-engine` is rejected because the engine provides the SDK. The
   manifest is validated before anything is installed or imported.
2. **Installed by the engine, with uv, per plugin.**
   `uv pip install --target <plugins_dir>/.deps/<id> --only-binary :all:`
   runs on install, and also on startup or enable when the directory is
   missing or stale. Stale means it was resolved for other requirements,
   another Python, or other engine libraries (for example after an engine
   upgrade). The installed set is recorded in `akasha-requirements.json`. On
   startup each process installs what is missing. The volume carries the
   result to the other processes. Removing the plugin deletes the directory.
3. **One version per library, enforced at resolution.** A constraints file
   pins every library of the engine environment, and every library of the
   other installed plugins, to its current version. A plugin can therefore
   never change an engine library, and two plugins can never need different
   versions of one library. Conflicts fail with `PLUGIN_DEPENDENCIES_FAILED`
   and carry uv's explanation, with index credentials masked. The directory
   is *appended* to `sys.path`, so the engine's own copy always wins.
4. **No silent stale code.** Imported modules cannot be unloaded. If another
   version of a library is already loaded in the process, activation is
   refused ("restart the engine") rather than running the old version.
5. **Wheels only.** No build scripts run on the engine host, and the slim
   image needs no compiler. The index comes from uv's configuration
   (`UV_DEFAULT_INDEX`, `UV_INDEX`, `UV_FIND_LINKS`, `UV_OFFLINE`, uv.toml).
   That covers private indexes and offline wheelhouses without new engine
   settings.
6. **The API stays responsive.** An HTTP install or enable runs the slow
   part (`PluginRegistry.prepare`, which only writes files) in a thread.
   Registry state still changes on the event loop. An `asyncio.Lock`
   serializes the lifecycle calls.

## Alternatives

- **Install into the engine's virtualenv.** Rejected. It is read-only by
  design and is replaced on every image upgrade. Removal would also be
  guesswork.
- **Vendored libraries inside the plugin package.** Rejected as the main
  path. Native wheels built on a developer's Mac do not run in the Linux
  container, and nothing would stop version clashes. A plugin may still ship
  pure-Python helper modules of its own.
- **One virtualenv or subprocess per plugin.** This would give real
  isolation, including conflicting versions. It needs the out-of-process
  loader that `registry._load` is the seam for, so it is deferred to that
  work.
- **Lock files with hashes per plugin.** More reproducible, but heavy for
  plugin authors. Exact pins (`==`) give reproducible top-level versions.
  The resolved set is logged (`plugin_dependencies_installed`) and stored in
  the marker file.

## Consequences

- Installing a plugin with requirements needs uv (now in the runtime image)
  and access to an index or wheelhouse. That applies at install time, and
  again at startup when the directory has to be rebuilt.
- Transitive versions may differ between installations unless the author
  pins them.
- Upgrading a plugin to a different version of a library it already loaded
  needs a restart: remove, restart, install.
- The architecture test lists `plugins/dependencies.py` as the only
  `subprocess` user, still without `shell=`.
