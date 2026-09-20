"""Main application window: menu/toolbar/status bar, wires whichever ping
source (LiveClient or PlaybackSource) is active to the waterfall display."""
from PySide6.QtWidgets import (
    QMainWindow, QFileDialog, QInputDialog, QToolBar, QLabel, QSlider,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QShortcut, QKeySequence
from bytt.config import AppConfig
from bytt.net.live_client import LiveClient
from bytt.net.command_client import CommandClient
from bytt.net.playback_source import PlaybackSource
from bytt.gui.waterfall_view import WaterfallView, build_display_row
from bytt.gui.controls_panel import ControlsPanel
from bytt.gui.sonar_control_panel import SonarControlPanel
from bytt.nav.gps_track import GPSTrack
from bytt.gui.gps_panel import GpsPanel
from bytt.net.recorder import Recorder
from bytt.config import same_segment
from bytt.gui.connection_indicators import ConnectionIndicatorBar

NO_DATA_WARNING_MS = 5000  # how long a live connection can go silent before we warn

_SPEED_LABELS = ["1×", "2×", "4×", "8×", "16×", "32×", "MAX"]
_SPEED_MULTIPLIERS = [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, None]  # None = MAX (zero delay)
_BASE_PINGS_PER_SECOND = 16.0  # matches PlaybackSource's own constructor default


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

        self.recorder = Recorder()
        self.record_action = QAction("&Record", self)
        self.record_action.setCheckable(True)
        self.record_action.setEnabled(False)
        self.record_action.toggled.connect(self._on_record_toggled)
        connect_menu.addAction(self.record_action)

        self.controls_panel = ControlsPanel(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.controls_panel)
        self.controls_panel.params_changed.connect(self.waterfall.set_enhance_params)
        self.controls_panel.channels_changed.connect(self._on_channels_changed)
        self._port_on = True
        self._stbd_on = True

        self.sonar_control_panel = SonarControlPanel(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.sonar_control_panel)
        self.sonar_control_panel.apply_requested.connect(self._on_sonar_command_apply)

        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(self.controls_panel.toggleViewAction())
        view_menu.addAction(self.sonar_control_panel.toggleViewAction())

        self.gps_track = GPSTrack()
        self._latest_heading = None
        self.gps_panel = GpsPanel(self.gps_track, self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.gps_panel)
        view_menu.addAction(self.gps_panel.toggleViewAction())

        self.playback_toolbar = QToolBar("Playback", self)
        self.addToolBar(self.playback_toolbar)

        self.play_pause_action = QAction("Play", self)
        self.play_pause_action.triggered.connect(self._toggle_play_pause)
        self.playback_toolbar.addAction(self.play_pause_action)

        self.step_back_action = QAction("Step Back", self)
        self.step_back_action.triggered.connect(self._step_backward)
        self.playback_toolbar.addAction(self.step_back_action)

        self.step_fwd_action = QAction("Step Forward", self)
        self.step_fwd_action.triggered.connect(self._step_forward)
        self.playback_toolbar.addAction(self.step_fwd_action)

        self.position_label = QLabel("")
        self.playback_toolbar.addWidget(self.position_label)

        self.scrub_slider = QSlider(Qt.Orientation.Horizontal)
        self.scrub_slider.setRange(0, 0)
        self.scrub_slider.sliderPressed.connect(self._on_scrub_pressed)
        self.scrub_slider.sliderReleased.connect(self._on_scrub_released)
        self.playback_toolbar.addWidget(self.scrub_slider)
        self._scrub_dragging = False

        self.speed_dec_action = QAction("−", self)
        self.speed_dec_action.triggered.connect(self._speed_dec)
        self.playback_toolbar.addAction(self.speed_dec_action)

        self.speed_label = QLabel("")
        self.playback_toolbar.addWidget(self.speed_label)

        self.speed_inc_action = QAction("+", self)
        self.speed_inc_action.triggered.connect(self._speed_inc)
        self.playback_toolbar.addAction(self.speed_inc_action)

        self.home_action = QAction("Home", self)
        self.home_action.triggered.connect(self._home)
        self.playback_toolbar.addAction(self.home_action)

        self._speed_idx = 2  # default "4×", matching the original viewer's default
        self._apply_speed()

        self._is_playing = False
        self.playback_toolbar.setVisible(False)

        self._space_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self._space_shortcut.activated.connect(self._toggle_play_pause)

        self._left_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Left), self)
        self._left_shortcut.activated.connect(self._step_backward)

        self._right_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Right), self)
        self._right_shortcut.activated.connect(self._step_forward)

        self._quit_shortcut_q = QShortcut(QKeySequence(Qt.Key.Key_Q), self)
        self._quit_shortcut_q.activated.connect(self.close)

        self._quit_shortcut_esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._quit_shortcut_esc.activated.connect(self.close)

        self._speed_up_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Up), self)
        self._speed_up_shortcut.activated.connect(self._speed_inc)

        self._speed_down_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Down), self)
        self._speed_down_shortcut.activated.connect(self._speed_dec)

        self._home_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Home), self)
        self._home_shortcut.activated.connect(self._home)

        self._no_data_timer = QTimer(self)
        self._no_data_timer.setSingleShot(True)
        self._no_data_timer.timeout.connect(self._on_no_data_timeout)

        self.connection_indicators = ConnectionIndicatorBar()
        self.statusBar().addPermanentWidget(self.connection_indicators)
        self.command_client.status_changed.connect(self._on_command_status_changed)

        self.statusBar().showMessage("disconnected")

    def closeEvent(self, event):
        self.disconnect_source()
        self.command_client.close()
        self.recorder.stop()
        super().closeEvent(event)

    def _on_sonar_command_apply(self, cmd) -> None:
        try:
            self.command_client.send_command(cmd)
        except (RuntimeError, OSError) as e:
            self.statusBar().showMessage(f"sonar command failed: {e}")

    def _on_no_data_timeout(self) -> None:
        self.connection_indicators.set_data_state("warning")
        self.statusBar().showMessage(
            "connected but no data received — check sonar power/link")

    def _on_live_status_changed(self, status: str) -> None:
        if status == "connecting":
            self.connection_indicators.set_data_state("warning")
        elif status.startswith("connected"):
            self.connection_indicators.set_data_state("ok")
        elif status.startswith("disconnected") or status.startswith("not connected"):
            self.connection_indicators.set_data_state("error")
        # else (e.g. a checksum-failure message): leave the indicator as-is,
        # a single dropped packet doesn't mean the link is down.

    def _on_link_status_changed(self, data_up: bool, cmd_up: bool) -> None:
        if data_up and cmd_up:
            self.connection_indicators.set_sonar_state("ok")
        elif data_up or cmd_up:
            self.connection_indicators.set_sonar_state("warning")
        else:
            self.connection_indicators.set_sonar_state("error")

    def _on_command_status_changed(self, status: str) -> None:
        if status == "connecting":
            self.connection_indicators.set_command_state("warning")
        elif status == "connected":
            self.connection_indicators.set_command_state("ok")
        else:
            self.connection_indicators.set_command_state("error")

    def _on_record_toggled(self, checked: bool) -> None:
        if checked:
            path = self.recorder.start()
            self.statusBar().showMessage(f"recording to {path}")
        else:
            self.recorder.stop()
            self.statusBar().showMessage("recording stopped")

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
        self.gps_track = GPSTrack()
        self._latest_heading = None
        self.gps_panel.gps_track = self.gps_track
        self.gps_panel.refresh()
        self.source = LiveClient(host, data_port, parent=self)
        self.waterfall.live_mode = True
        self._wire_source()
        self.source.status_changed.connect(self._on_live_status_changed)
        self.source.link_status_changed.connect(self._on_link_status_changed)
        self.source.raw_packet_received.connect(self.recorder.write_packet)
        self.record_action.setEnabled(True)
        self.source.start()
        self._no_data_timer.start(NO_DATA_WARNING_MS)
        try:
            self.command_client.connect_to(host, cmd_port)
        except OSError as e:
            self.statusBar().showMessage(f"command channel failed: {e}")
        if self.config.pc_ip and not same_segment(host, self.config.pc_ip):
            self.statusBar().showMessage(
                f"warning: towfish {host} and this PC {self.config.pc_ip} "
                f"are not on the same network segment")

    def open_playback_file(self, path: str) -> None:
        self.disconnect_source()
        self.gps_track = GPSTrack()
        self._latest_heading = None
        self.gps_panel.gps_track = self.gps_track
        self.gps_panel.refresh()
        self.source = PlaybackSource(path)
        self.waterfall.live_mode = False
        self._wire_source()
        self.source.start()

    def disconnect_source(self) -> None:
        if self.source is not None:
            self.source.stop()
            self.source = None
        self.recorder.stop()
        self.record_action.setChecked(False)
        self.record_action.setEnabled(False)
        self._no_data_timer.stop()
        self.connection_indicators.reset()
        self.playback_toolbar.setVisible(False)
        self._is_playing = False

    def _wire_source(self) -> None:
        self.source.status_changed.connect(self.statusBar().showMessage)
        self.source.status_changed.connect(self._on_source_status_changed)
        self.source.ping_received.connect(self._on_ping_received)
        is_playback = isinstance(self.source, PlaybackSource)
        self.playback_toolbar.setVisible(is_playback)
        if is_playback:
            self.source.position_changed.connect(self._on_position_changed)
            self._is_playing = True
            self.play_pause_action.setText("Pause")
            self._apply_speed()

    def _toggle_play_pause(self) -> None:
        if not isinstance(self.source, PlaybackSource):
            return
        if self._is_playing:
            self.source.pause()
            self._is_playing = False
            self.play_pause_action.setText("Play")
        else:
            self.source.resume()
            self._is_playing = True
            self.play_pause_action.setText("Pause")

    def _step_forward(self) -> None:
        if isinstance(self.source, PlaybackSource):
            self.source.step_forward()
            self._is_playing = False
            self.play_pause_action.setText("Play")

    def _step_backward(self) -> None:
        if isinstance(self.source, PlaybackSource):
            self.source.step_backward()
            self._is_playing = False
            self.play_pause_action.setText("Play")

    def _apply_speed(self) -> None:
        multiplier = _SPEED_MULTIPLIERS[self._speed_idx]
        self.speed_label.setText(_SPEED_LABELS[self._speed_idx])
        if isinstance(self.source, PlaybackSource):
            interval_s = 0.0 if multiplier is None else 1.0 / (_BASE_PINGS_PER_SECOND * multiplier)
            self.source.set_speed(interval_s)

    def _speed_dec(self) -> None:
        self._speed_idx = max(0, self._speed_idx - 1)
        self._apply_speed()

    def _speed_inc(self) -> None:
        self._speed_idx = min(len(_SPEED_LABELS) - 1, self._speed_idx + 1)
        self._apply_speed()

    def _home(self) -> None:
        if isinstance(self.source, PlaybackSource):
            self.source.seek(0)
            self.source.resume()
            self._is_playing = True
            self.play_pause_action.setText("Pause")

    def _on_source_status_changed(self, message: str) -> None:
        if message == 'playback finished':
            # PlaybackSource pauses itself (rather than exiting) when it
            # naturally reaches end-of-file, so reflect that here the same
            # way _toggle_play_pause does for a user-initiated pause.
            self._is_playing = False
            self.play_pause_action.setText("Play")

    def _on_scrub_pressed(self) -> None:
        self._scrub_dragging = True

    def _on_scrub_released(self) -> None:
        self._scrub_dragging = False
        if isinstance(self.source, PlaybackSource):
            self.source.seek(self.scrub_slider.value())

    def _on_position_changed(self, current_index: int, total_pings: int) -> None:
        self.position_label.setText(f"Ping {current_index + 1} / {total_pings}")
        if not self._scrub_dragging:
            self.scrub_slider.blockSignals(True)
            self.scrub_slider.setRange(0, max(0, total_pings - 1))
            self.scrub_slider.setValue(current_index)
            self.scrub_slider.blockSignals(False)

    def _on_channels_changed(self, port_on: bool, stbd_on: bool) -> None:
        self._port_on = port_on
        self._stbd_on = stbd_on

    def _on_ping_received(self, port_raw, stbd_raw, meta) -> None:
        if self.waterfall.live_mode:
            self._no_data_timer.start(NO_DATA_WARNING_MS)
            self.connection_indicators.set_data_state("ok")
        # build_display_row interpolates EACH channel to channel_w samples,
        # then concatenates them with a gap in between, so the resulting row
        # length is 2*channel_w + gap. Solve for channel_w so that total
        # equals self.waterfall.row_width, which is what add_row() requires.
        try:
            gap = 8
            channel_w = (self.waterfall.row_width - gap) // 2
            row_f32 = build_display_row(
                port_raw, stbd_raw, channel_w=channel_w,
                port_on=self._port_on, stbd_on=self._stbd_on, gap=gap,
                interp_xs_cache=self._interp_cache,
            )
            self.waterfall.add_row(row_f32)

            nav_fix = meta.get('nav_fix')
            if nav_fix is not None:
                self._latest_heading = nav_fix.get('heading')
                latlon = (nav_fix['lat'], nav_fix['lon'])
                if latlon != self.gps_track._last_latlon:
                    self.gps_track.add_fix(self.waterfall.rows_written, nav_fix['lat'], nav_fix['lon'])
                    self.gps_panel.refresh(heading=self._latest_heading)
        except Exception as e:
            self.statusBar().showMessage(f"ping display error: {e}")
