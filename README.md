# tcex-app-playbook

A [TcEx](https://github.com/ThreatConnect-Inc/tcex) submodule providing the typed read, write,
delete, and output-buffering layer that Playbook and Service Apps use to exchange variables through
the ThreatConnect key-value store.

## Overview

ThreatConnect Playbook Apps pass data between each other via a key-value store. Every value is
stored under a fully-qualified variable string in the format `#App:{job_id}:{key}!{type}` (e.g.,
`#App:1234:my.output!String`). This submodule owns the type-aware serialization, deserialization,
embedded-variable resolution, and validation logic for every TC variable type, and exposes it
through a clean `Playbook` facade that App developers interact with directly.

## Variable Types

All read and write methods handle the following TC Playbook variable types:

| Type | Description |
|---|---|
| `String` | Single string; embedded variable resolution supported |
| `StringArray` | List of strings; returns `list[StringVariable]` |
| `Binary` | Bytes; base64-encoded on write, decoded on read |
| `BinaryArray` | List of bytes; each element base64-encoded |
| `KeyValue` | `{"key": ..., "value": ...}` dict; embedded variable resolution in values |
| `KeyValueArray` | List of KeyValue dicts |
| `TCEntity` | `{"id": ..., "value": ..., "type": ...}` dict |
| `TCEntityArray` | List of TCEntity dicts |
| `TCBatch` | `{"indicator": [...], "group": [...]}` dict |

## Module Reference

### `Playbook`

The top-level entry point for App developers. Accepts a `KeyValueStore`, an execution `context`
(the Playbook job ID / service request ID), and the list of output variable strings requested by
downstream Apps. Exposes four lazily-instantiated sub-components as cached properties:

| Property | Type | Purpose |
|---|---|---|
| `create` | `PlaybookCreate` | Write typed variables to the KV store |
| `read` | `PlaybookRead` | Read typed variables from the KV store |
| `delete` | `PlaybookDelete` | Delete variables from the KV store (Redis only) |
| `output` | `PlaybookOutput` | Buffer outputs and flush them in a single `process()` call |

Helper methods:

- `check_key_requested(key)` — returns `True` if the bare key (e.g., `my.output`) was requested
  by any downstream App.
- `check_variable_requested(variable)` — returns `True` if the full variable string was requested.
- `get_variable_type(variable)` — extracts the type suffix from a variable string, or returns
  `"String"` for plain text values.
- `is_variable(key)` — returns `True` if the value matches the TC variable pattern.

A `RuntimeError` is raised if `create`, `read`, or `delete` are accessed while `context` is
`None` (which is valid for service Apps before a request arrives).

### `PlaybookCreate`

Typed write operations to the KV store. Each method validates the value, resolves the variable
string from the requested output list, and serializes the data before writing.

Key behaviors:

- **`when_requested` guard** — by default every write method silently skips variables that were
  not requested by a downstream App. Pass `when_requested=False` to write unconditionally.
- **Type coercion** — `string()` and `string_array()` automatically coerce `bool` and `float`/
  `int` values to strings before writing.
- **Binary encoding** — `binary()` and `binary_array()` base64-encode bytes before serializing.
- **Null tracking** — when the `TC_PLAYBOOK_WRITE_NULL` environment variable is set (test
  framework use only), writing `None` to a Redis-backed store records a sentinel key
  (`{variable}_NULL_VALIDATION`) so the test harness can assert on expected null outputs.
- **`any()` / `variable()`** — type-dispatching convenience methods: inspect the variable type
  from the key string and delegate to the appropriate typed write method automatically.

### `PlaybookRead`

Typed read operations from the KV store. Each method validates the variable type, fetches raw
bytes from the store, and deserializes/decodes the data before returning.

Key behaviors:

- **Embedded variable resolution** — `string()` scans user-provided string values for embedded
  `#App:...` variable patterns and resolves them one level deep. This supports inputs like
  `"prefix #App:1234:upstream.output!String suffix"`. Double nesting is not supported by the TC
  platform. The `\s` escape sequence in user input is expanded to a literal space.
- **`Sensitive` passthrough** — when a resolved embedded variable is a keychain (encrypted) type,
  `string()` returns a `Sensitive` wrapper so the App can control how the value is used without
  inadvertently logging it.
- **Binary decoding** — `binary()` and `binary_array()` reverse the base64 encoding written by
  `PlaybookCreate`. Returns a `BinaryVariable` (`bytes` subclass) by default; pass `decode=True`
  to get a `str` instead. Handles legacy data written by Java Apps using Latin-1 encoding.
- **`any()` / `variable()`** — type-dispatching read; determines the type from the variable
  string and returns the appropriately typed value.

### `PlaybookDelete`

Single-method class that removes a variable from the KV store via `variable(key)`. **Only
functional when the backing store is `KeyValueRedis`** — silently returns `None` for the API or
Mock backends, which do not support deletion.

### `PlaybookOutput`

A `dict` subclass for buffering output variables. App code populates it with
`output["my.key"] = value` assignments throughout execution, then calls `output.process()` at the
end to flush all entries to the KV store in one pass via `playbook.create.variable()`.

### `AdvancedRequest`

Implements the "Advanced Request" optional App feature declared in `app_spec.yml`. Takes an
`AdvancedRequestModel` (populated from standard input params), a `Playbook` instance, a
`requests.Session`, and an output prefix. Reads the HTTP configuration (path, method, headers,
query params, body, URL-encode flag) from inputs, executes the request, and writes the following
standard output variables under `{output_prefix}.request.*`:

| Output variable suffix | Content |
|---|---|
| `.content` | Response body as string |
| `.content.binary` | Response body as binary |
| `.headers` | Response headers as JSON string |
| `.ok` | `"true"` / `"false"` |
| `.reason` | HTTP reason phrase |
| `.status_code` | HTTP status code as string |
| `.url` | Final resolved request URL |

## Project Structure Note — No `pyproject.toml` or `.pre-commit-config.yaml`

This submodule intentionally ships **without** a `pyproject.toml` or `.pre-commit-config.yaml`.
All linting (`ruff`), type-checking (`ty`), and pre-commit hooks are configured in the **parent
projects** (`tcex`, `tcex-app-testing`), each of which scans this submodule as part of its own
workspace. Running `pre-commit run --all-files` or `ty check` from the parent repo root covers
this code automatically — there is no need for (and no benefit to) duplicating that configuration
here.

## Used By

- [tcex](https://github.com/ThreatConnect-Inc/tcex) — runtime variable I/O for Playbook and Service Apps
- [tcex-app-testing](https://github.com/ThreatConnect-Inc/tcex-app-testing) — read/write fixtures and output validation in test harnesses

## License

Apache 2.0 — see [LICENSE](LICENSE).
