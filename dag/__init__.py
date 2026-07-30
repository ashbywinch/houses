"""dag — a generic reactive directed acyclic graph library.

UserInputNode emits `changed` when pushed. DerivedNode subscribes to its
deps' signals and re-computes on change. Every node has `.to_json()` for
serialisation and `_impossible()` for detailed error messages.

See docs/dag-library.md for full documentation.
"""
