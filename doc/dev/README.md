# Developer documentation

End-user documentation lives one level up in [doc/](../); this directory is for
people working on Keyhac itself.

- [overview.md](overview.md) — why Keyhac 2 exists; the founding design decisions.
- [architecture.md](architecture.md) — layers, event loop, threading, the key-event
  lifecycle, module map.
- [platform-layer.md](platform-layer.md) — what is genuinely OS-specific: hooks,
  injection and ordering, focus, clipboard, permissions.
- [design-notes.md](design-notes.md) — per-feature design decisions and the subtle
  behaviors deliberately ported from the predecessors.
- [puikit.md](puikit.md) — what Keyhac uses from PuiKit and the extensions built
  for it.
- [packaging.md](packaging.md) — launchers, bundles, release pipeline, data paths.
- [testing.md](testing.md) — test layers, harness patterns, live verification
  record.
- [ai-integration.md](ai-integration.md) — design handover for AI integration:
  agent at authoring time producing plain-Python actions, observation /
  state-reading / safety layers, MCP topologies.

The project guide for coding agents is [CLAUDE.md](../../CLAUDE.md) at the repo root.
