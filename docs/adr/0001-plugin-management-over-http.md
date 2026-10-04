# ADR 0001: Plugin management over HTTP

Status: accepted (frontend Phase 20, 2026-10-04)

## Context

Architecture §22 and §36 kept plugin management off HTTP in V1:
`/api/v1/plugins` only listed plugins, and install, enable, disable and
remove were operator CLI commands. Two reasons were given. Plugins are
trusted code (§65). Before authentication existed (backend Phase 20), an
install endpoint would have been remote code execution.

Frontend Phase 20 needs the plugin lifecycle in the UI. Authentication and
admin-only routers now exist (`require_admin`).

## Decision

1. **Enable, disable and remove move to HTTP, admin only.**
   - `POST /api/v1/plugins/{id}/enable`
   - `POST /api/v1/plugins/{id}/disable`
   - `DELETE /api/v1/plugins/{id}?force=`

   They call the same `PluginRegistry` and `application.plugins.remove_plugin`
   code as the CLI. Removal is blocked with `PLUGIN_IN_USE` while projects
   need the plugin; `force` is the explicit confirmation (§143). These calls
   only run code that an operator already installed, so they need no opt-in.
2. **Upload install is opt-in.** `POST /api/v1/plugins` takes an
   `application/zip` package. It is admin only and refused with 403
   `PLUGIN_INSTALL_DISABLED` unless `AKASHA_PLUGINS_HTTP_INSTALL=true`. The
   setting is checked before the body is read. The plugin list reports
   `install_enabled`, so clients know whether to offer the upload or point
   to the CLI.
3. **Unpacking is a trust boundary** (`akasha.plugins.archive`). The archive
   may contain only regular files and directories under relative paths: no
   `..` or absolute paths, no drive prefixes, backslashes, symlinks or
   devices. It is capped at 16 MiB compressed, 64 MiB unpacked and 2000
   entries, and reads are capped because declared sizes are not trusted.
   After unpacking, `PluginRegistry.install` does its usual validation
   (manifest, compatibility, all-or-nothing copy and load).
4. **The backend decides.** Compatibility and validity come only from the
   backend. The UI shows its error codes and details (`issues`, `fields`,
   `dependents`, `projects`) and treats plugin metadata as untrusted text.

## Alternatives

- **Keep everything on the CLI.** This is the safest option, but the UI
  could not satisfy the phase.
- **Upload install always on for admins.** Rejected. An admin password would
  then be equivalent to a shell on the host, and many installations do not
  want that trade.
- **Install from a server-side path.** Rejected. It needs the files on the
  host already (so it adds nothing over the CLI), and it exposes filesystem
  paths over the API (AGENTS §8).

## Consequences

- With the setting on, an admin account can run arbitrary code in the
  engine process. Operators must treat admin credentials as host
  credentials. The UI states this before every upload.
- Changes apply to the API process and its in-process worker. Dedicated
  `akasha worker` processes and other API replicas see them after a restart
  (§36). The UI says so.
- Lifecycle calls run on the event loop, which also serializes them on the
  unlocked registry (marked `ponytail:` in `api/plugins.py`).
- Upgrade is still remove + install.
