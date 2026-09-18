"""Main application window: menu/toolbar/status bar, wires whichever ping
source (LiveClient or PlaybackSource) is active to the waterfall display."""
from PySide6.QtWidgets import QMainWindow, QFileDialog, QInputDialog
from PySide6.QtGui import QAction
from bytt.config import AppConfig
from bytt.net.live_client import LiveClient
from bytt.net.command_client import CommandClient
from bytt.net.playback_source import PlaybackSource
from bytt.gui.waterfall_view import WaterfallView, build_display_row
from bytt.processing.enhancement import EnhanceParams, enhance_pixels

_DEFAULT_ENHANCE_PARAMS = EnhanceParams(
    noise_idx=0, contrast_idx=0, agc=False, target_idx=0, target_ksize=9,
    shadow_enh=False, overlay=0, sharpen=False, gain=1.0, gamma=1.0,
    lut_idx=0, fast=True, hdr=False,
)


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("BYTT Sonar Viewer")
        self.config = config
        self.source = None
        self.command_client = CommandClient(self)
        self._interp_cache = {}

        self.waterfall = WaterfallView()
        self.setCentralWidget(self.waterfall)

        connect_menu = self.menuBar().addMenu("&Connect")
        connect_action = QAction("Connect to &Towfish...", self)
        connect_action.triggered.connect(self._prompt_connect_towfish)
        connect_menu.addAction(connect_action)

        open_action = QAction("&Open Playback File...", self)
        open_action.triggered.connect(self._prompt_open_playback)
        connect_menu.addAction(open_action)

        disconnect_action = QAction("&Disconnect", self)
        disconnect_action.triggered.connect(self.disconnect_source)
        connect_menu.addAction(disconnect_action)

        self.statusBar().showMessage("disconnected")

    def _prompt_connect_towfish(self):
        host, ok = QInputDialog.getText(self, "Connect to Towfish", "Towfish IP:",
                                         text=self.config.towfish_ip)
        if ok and host:
            self.connect_towfish(host, self.config.data_port, self.config.cmd_port)

    def _prompt_open_playback(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open .bsf recording", "", "*.bsf")
        if path:
            self.open_playback_file(path)

    def connect_towfish(self, host: str, data_port: int, cmd_port: int) -> None:
        self.disconnect_source()
        self.source = LiveClient(host, data_port, parent=self)
        self._wire_source()
        self.source.start()
        try:
            self.command_client.connect_to(host, cmd_port)
        except OSError as e:
            self.statusBar().showMessage(f"command channel failed: {e}")

    def open_playback_file(self, path: str) -> None:
        self.disconnect_source()
        self.source = PlaybackSource(path)
        self._wire_source()
        self.source.start()

    def disconnect_source(self) -> None:
        if self.source is not None:
            self.source.stop()
            self.source = None

    def _wire_source(self) -> None:
        self.source.status_changed.connect(self.statusBar().showMessage)
        self.source.ping_received.connect(self._on_ping_received)

    def _on_ping_received(self, port_raw, stbd_raw, meta) -> None:
        # build_display_row interpolates EACH channel to channel_w samples,
        # then concatenates them with a gap in between, so the resulting row
        # length is 2*channel_w + gap. Solve for channel_w so that total
        # equals self.waterfall.width, which is what add_row() requires.
        gap = 8
        channel_w = (self.waterfall.width - gap) // 2
        row_f32 = build_display_row(
            port_raw, stbd_raw, channel_w=channel_w,
            port_on=True, stbd_on=True, gap=gap, interp_xs_cache=self._interp_cache,
        )
        row_2d = row_f32.reshape(1, -1)
        # enhance_pixels returns a 3-tuple (img8, target_mask, shadow_mask);
        # img8 has shape (1, width) here, so row 0 is the 1D uint8 row
        # add_row() expects.
        img8, target_mask, shadow_mask = enhance_pixels(row_2d, _DEFAULT_ENHANCE_PARAMS)
        row_u8 = img8[0]
        self.waterfall.add_row(row_u8)
