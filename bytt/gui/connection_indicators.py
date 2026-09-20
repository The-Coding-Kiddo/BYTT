"""Three colored-dot connection indicators for the status bar: Data socket,
Command socket, and the sonar's own reported link health (type-166).
Separate because they can genuinely disagree -- e.g. our TCP connect()
succeeding tells us nothing about whether the sonar itself is reachable."""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel

_COLORS = {
    "unknown": "#888888",   # gray -- no signal yet
    "ok":      "#2ecc71",   # green -- fully connected / healthy
    "warning": "#e67e22",   # orange -- connected but degraded
    "error":   "#e74c3c",   # red -- down / failed
}


class _Indicator(QWidget):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        self._dot = QLabel("●")
        layout.addWidget(self._dot)
        layout.addWidget(QLabel(label))
        self.set_state("unknown")

    def set_state(self, state: str) -> None:
        color = _COLORS.get(state, _COLORS["unknown"])
        self._dot.setStyleSheet(f"color: {color}; font-size: 14px;")
        self._state = state


class ConnectionIndicatorBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.data = _Indicator("Data")
        self.command = _Indicator("Cmd")
        self.sonar = _Indicator("Sonar")
        layout.addWidget(self.data)
        layout.addWidget(self.command)
        layout.addWidget(self.sonar)

    def set_data_state(self, state: str) -> None:
        self.data.set_state(state)

    def set_command_state(self, state: str) -> None:
        self.command.set_state(state)

    def set_sonar_state(self, state: str) -> None:
        self.sonar.set_state(state)

    def reset(self) -> None:
        self.data.set_state("unknown")
        self.command.set_state("unknown")
        self.sonar.set_state("unknown")
