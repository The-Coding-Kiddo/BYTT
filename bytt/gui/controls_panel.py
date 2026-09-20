"""Dockable panel exposing the enhancement pipeline's settings plus
Port/Starboard channel toggles -- ported from interactive_bsf_viewer.py's
TAB controls panel (interactive_bsf_viewer.py:2085-2116)."""
from PySide6.QtWidgets import (
    QDockWidget, QWidget, QFormLayout, QComboBox, QDoubleSpinBox, QSpinBox,
    QCheckBox, QPushButton,
)
from PySide6.QtCore import Signal, QTimer
from bytt.processing.enhancement import (
    EnhanceParams, DEFAULT_ENHANCE_PARAMS, NOISE_MODES, CONTRAST_MODES, TARGET_MODES,
    GAIN_DEFAULT, GAIN_STEP, GAIN_MIN, GAIN_MAX,
    GAMMA_DEFAULT, GAMMA_STEP, GAMMA_MIN, GAMMA_MAX,
    TARGET_KSIZE_MIN, TARGET_KSIZE_MAX, TARGET_KSIZE_STEP, TARGET_KSIZE_DEFAULT,
)
from bytt.processing.colormap import LUT_NAMES


class ControlsPanel(QDockWidget):
    params_changed = Signal(object)         # EnhanceParams
    channels_changed = Signal(bool, bool)   # port_on, stbd_on

    def __init__(self, parent=None):
        super().__init__("Enhancement Controls", parent)
        body = QWidget(self)
        form = QFormLayout(body)

        self.palette_combo = QComboBox()
        self.palette_combo.addItems(LUT_NAMES)
        form.addRow("Palette", self.palette_combo)

        self.gain_spin = QDoubleSpinBox()
        self.gain_spin.setRange(GAIN_MIN, GAIN_MAX)
        self.gain_spin.setSingleStep(GAIN_STEP)
        self.gain_spin.setValue(GAIN_DEFAULT)
        form.addRow("Gain", self.gain_spin)

        self.gamma_spin = QDoubleSpinBox()
        self.gamma_spin.setRange(GAMMA_MIN, GAMMA_MAX)
        self.gamma_spin.setSingleStep(GAMMA_STEP)
        self.gamma_spin.setValue(GAMMA_DEFAULT)
        form.addRow("Gamma", self.gamma_spin)

        self.noise_combo = QComboBox()
        self.noise_combo.addItems(NOISE_MODES)
        form.addRow("Denoise", self.noise_combo)

        self.contrast_combo = QComboBox()
        self.contrast_combo.addItems(CONTRAST_MODES)
        self.contrast_combo.setCurrentIndex(DEFAULT_ENHANCE_PARAMS.contrast_idx)
        form.addRow("Contrast", self.contrast_combo)

        self.agc_check = QCheckBox()
        form.addRow("AGC", self.agc_check)

        self.target_combo = QComboBox()
        self.target_combo.addItems(TARGET_MODES)
        form.addRow("Target enhance", self.target_combo)

        self.target_ksize_spin = QSpinBox()
        self.target_ksize_spin.setRange(TARGET_KSIZE_MIN, TARGET_KSIZE_MAX)
        self.target_ksize_spin.setSingleStep(TARGET_KSIZE_STEP)
        self.target_ksize_spin.setValue(TARGET_KSIZE_DEFAULT)
        form.addRow("Target kernel size", self.target_ksize_spin)

        self.shadow_check = QCheckBox()
        form.addRow("Shadow enhance", self.shadow_check)

        self.overlay_check = QCheckBox()
        form.addRow("Overlay", self.overlay_check)

        self.sharpen_check = QCheckBox()
        form.addRow("Sharpen", self.sharpen_check)

        self.hdr_check = QCheckBox()
        form.addRow("HDR", self.hdr_check)

        self.reset_button = QPushButton("Reset to Defaults")
        form.addRow(self.reset_button)

        self.port_check = QCheckBox()
        self.port_check.setChecked(True)
        form.addRow("Port channel", self.port_check)

        self.stbd_check = QCheckBox()
        self.stbd_check.setChecked(True)
        form.addRow("Starboard channel", self.stbd_check)

        body.setLayout(form)
        self.setWidget(body)

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(150)
        self._debounce_timer.timeout.connect(self._emit_params_changed)

        for widget, signal_name in (
            (self.palette_combo, 'currentIndexChanged'),
            (self.gain_spin, 'valueChanged'),
            (self.gamma_spin, 'valueChanged'),
            (self.noise_combo, 'currentIndexChanged'),
            (self.contrast_combo, 'currentIndexChanged'),
            (self.agc_check, 'stateChanged'),
            (self.target_combo, 'currentIndexChanged'),
            (self.target_ksize_spin, 'valueChanged'),
            (self.shadow_check, 'stateChanged'),
            (self.overlay_check, 'stateChanged'),
            (self.sharpen_check, 'stateChanged'),
            (self.hdr_check, 'stateChanged'),
        ):
            getattr(widget, signal_name).connect(self._schedule_params_changed)

        self.reset_button.clicked.connect(self.reset_to_defaults)
        self.port_check.stateChanged.connect(self._emit_channels_changed)
        self.stbd_check.stateChanged.connect(self._emit_channels_changed)

    def _schedule_params_changed(self, *_args) -> None:
        self._debounce_timer.start()

    def current_params(self) -> EnhanceParams:
        return EnhanceParams(
            noise_idx=self.noise_combo.currentIndex(),
            contrast_idx=self.contrast_combo.currentIndex(),
            agc=self.agc_check.isChecked(),
            target_idx=self.target_combo.currentIndex(),
            target_ksize=self.target_ksize_spin.value(),
            shadow_enh=self.shadow_check.isChecked(),
            overlay=self.overlay_check.isChecked(),
            sharpen=self.sharpen_check.isChecked(),
            gain=self.gain_spin.value(),
            gamma=self.gamma_spin.value(),
            lut_idx=self.palette_combo.currentIndex(),
            fast=False,  # overridden by WaterfallView.set_enhance_params based on live_mode
            hdr=self.hdr_check.isChecked(),
        )

    def _emit_params_changed(self) -> None:
        self.params_changed.emit(self.current_params())

    def _emit_channels_changed(self, *_args) -> None:
        self.channels_changed.emit(self.port_check.isChecked(), self.stbd_check.isChecked())

    def reset_to_defaults(self) -> None:
        d = DEFAULT_ENHANCE_PARAMS
        self.palette_combo.setCurrentIndex(d.lut_idx)
        self.gain_spin.setValue(d.gain)
        self.gamma_spin.setValue(d.gamma)
        self.noise_combo.setCurrentIndex(d.noise_idx)
        self.contrast_combo.setCurrentIndex(d.contrast_idx)
        self.agc_check.setChecked(d.agc)
        self.target_combo.setCurrentIndex(d.target_idx)
        self.target_ksize_spin.setValue(d.target_ksize)
        self.shadow_check.setChecked(d.shadow_enh)
        self.overlay_check.setChecked(d.overlay)
        self.sharpen_check.setChecked(d.sharpen)
        self.hdr_check.setChecked(d.hdr)
        self._emit_params_changed()
