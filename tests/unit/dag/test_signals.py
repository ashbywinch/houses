from __future__ import annotations

from dag.signals import Signal, Slot


class TestSignal:
    def test_connect_and_emit(self):
        received = []
        sig = Signal()

        def handler():
            received.append("called")

        sig.connect(handler)
        sig.emit()
        assert received == ["called"]

    def test_multiple_handlers(self):
        results = []
        sig = Signal()

        sig.connect(lambda: results.append("a"))
        sig.connect(lambda: results.append("b"))

        sig.emit()
        assert results == ["a", "b"]

    def test_disconnect(self):
        received = []
        sig = Signal()

        def handler():
            received.append("called")

        conn = sig.connect(handler)
        sig.emit()
        assert received == ["called"]

        conn.disconnect()
        sig.emit()
        assert received == ["called"]  # no second call

    def test_connect_twice_fires_once(self):
        received = []
        sig = Signal()

        def handler():
            received.append("called")

        sig.connect(handler)
        sig.connect(handler)
        sig.emit()
        assert received == ["called"]

    def test_no_handlers_does_not_error(self):
        sig = Signal()
        sig.emit()


class TestSlot:
    def test_slot_wrapping_bound_method_detects_dead_owner(self):
        """Slot wrapping a bound method: drop owner, is_dead() returns True."""
        sig = Signal()

        class Owner:
            def handler(self):
                pass

        owner = Owner()
        slot = Slot(owner.handler)
        sig.connect(slot)

        # Owner alive — slot not dead
        assert not slot.is_dead()

        del owner
        import gc
        gc.collect()
        # Owner gone — weakref dies
        assert slot.is_dead()

    def test_emit_removes_dead_slot(self):
        """emit() sweeps dead Slots from handlers list."""
        sig = Signal()

        class Owner:
            def handler(self):
                pass

        owner = Owner()
        slot = Slot(owner.handler)
        sig.connect(slot)
        assert len(sig._handlers) == 1

        del owner
        import gc
        gc.collect()
        sig.emit()  # should not crash

        # Dead Slot was removed
        assert len(sig._handlers) == 0

    def test_slot_non_method_callable_never_dead(self):
        """Non-method callable: Slot holds strong ref, is_dead() returns False."""
        sig = Signal()
        received = []

        def handler():
            received.append("called")

        slot = Slot(handler)
        sig.connect(slot)
        sig.emit()
        assert received == ["called"]
        assert not slot.is_dead()
        # Even after dropping ref, Slot keeps callable alive
        del handler
        assert not slot.is_dead()

    def test_slot_wrapping_lambda_via_fallback(self):
        """Lambda (not a method) hits TypeError in WeakMethod, uses _callback path."""
        sig = Signal()
        received = []

        slot = Slot(lambda: received.append("called"))
        sig.connect(slot)
        sig.emit()
        assert received == ["called"]
        assert not slot.is_dead()
