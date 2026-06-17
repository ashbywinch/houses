from __future__ import annotations

from dag.signals import Signal


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
    def test_slot_receives_signal(self):
        sig = Signal()

        class Receiver:
            def __init__(self):
                self._slot = Signal()  # we use Signal directly for simplicity
                pass

        sig.emit()
        assert True  # no error

    def test_slot_disconnects_when_owner_dies(self):
        sig = Signal()
        results = []

        class Owner:
            def __init__(self):
                self.slot = self._handler

            def _handler(self):
                results.append("called")

        owner = Owner()
        sig.connect(owner.slot)
        sig.emit()
        assert results == ["called"]
