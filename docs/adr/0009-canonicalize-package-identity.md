---
status: accepted
---

# Canonicalize Package Identity from regular files

Compute Package Identity from a sorted canonical manifest of managed regular files containing normalized relative path, executable bit, byte size, and SHA-256, then hash that manifest with SHA-256. Empty directories, writable permission bits, timestamps, owners, groups, and platform separators do not participate because Git and cross-device checkout do not preserve them as stable package semantics; file bytes remain untouched, while portable UTF-8 NFC paths make identities comparable across supported Linux hosts.

## Consequences

Promotion omits empty directories, rejects symbolic links and nonportable or normalization-colliding paths, and changes the public Package Identity whenever a managed file's bytes, name, or executable status changes. Altering this algorithm requires a machine schema version change.
