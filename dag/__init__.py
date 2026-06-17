"""dag — a generic reactive directed acyclic graph library.

SourceNode emits `changed` when pushed. ComputedNode subscribes to its
deps' signals and re-computes on change. Every node has `.to_json()` for
serialisation and `_impossible()` for detailed error messages.
"""
