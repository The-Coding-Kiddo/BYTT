"""Dockable sonar hardware control panel: HF/LF range and gain, channel
enable, and run/stop -- sent as a type-107 SonarWorkCommand over the
existing CommandClient. Unlike the enhancement ControlsPanel (which only
changes local display settings), these commands reach real hardware, so
changes are sent explicitly via Apply/Start/Stop rather than debounced."""
from PySide6.QtWidgets import (
    QDockWidget, QWidget, QFormLayout, QSpinBox, QCheckBox, QPushButton, QHBoxLayout,
)
from PySide6.QtCore import Signal
from bytt.protocol.commands import SonarWorkCommand


class SonarControlPanel(QDockWidget):
    apply_requested = Signal(object)  # SonarWorkCommand, with sonar_run reflecting current state

    def __init__(self, parent=None):
        super().__init__("Sonar Control", parent)
        body = QWidget(self)
        form = QFormLayout(body)

        self.hf_enable = QCheckBox()
        self.hf_enable.setChecked(True)
        form.addRow("HF channel", self.hf_enable)

        self.hf_range = QSpinBox()
        self.hf_range.setRange(1, 500)
        self.hf_range.setValue(50)
        self.hf_range.setSuffix(" m")
        form.addRow("HF range", self.hf_range)

        self.hf_gain = QSpinBox()
        self.hf_gain.setRange(0, 100)
        self.hf_gain.setValue(10)
        self.hf_gain.setSuffix(" dB")
        form.addRow("HF gain", self.hf_gain)

        self.lf_enable = QCheckBox()
        self.lf_enable.setChecked(True)
        form.addRow("LF channel", self.lf_enable)

        self.lf_range = QSpinBox()
        self.lf_range.setRange(1, 500)
        self.lf_range.setValue(50)
        self.lf_range.setSuffix(" m")
        form.addRow("LF range", self.lf_range)

        self.lf_gain = QSpinBox()
        self.lf_gain.setRange(0, 100)
        self.lf_gain.setValue(10)
        self.lf_gain.setSuffix(" dB")
        form.addRow("LF gain", self.lf_gain)

        self._running = False
        self.run_button = QPushButton("Start")
        self.run_button.clicked.connect(self._toggle_run)
        self.apply_button = QPushButton("Apply")
        self.apply_button.clicked.connect(self._emit_apply)
        buttons = QHBoxLayout()
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.apply_button)
        form.addRow(buttons)

        body.setLayout(form)
        self.setWidget(body)

    def current_command(self) -> SonarWorkCommand:
        return SonarWorkCommand(
            ch_en_hf=int(self.hf_enable.isChecked()),
            sonar_range_hf=self.hf_range.value(),
            gain_hf=self.hf_gain.value(),
            ch_en_lf=int(self.lf_enable.isChecked()),
            sonar_range_lf=self.lf_range.value(),
            gain_lf=self.lf_gain.value(),
            sonar_run=int(self._running),
        )

    def _toggle_run(self) -> None:
        self._running = not self._running
        self.run_button.setText("Stop" if self._running else "Start")
        self.apply_requested.emit(self.current_command())

    def _emit_apply(self) -> None:
        self.apply_requested.emit(self.current_command())
