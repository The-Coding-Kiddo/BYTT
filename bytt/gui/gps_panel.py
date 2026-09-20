"""Dockable GPS/nav track panel: plots the accumulated GPSTrack history,
the current position, (live mode only) a heading arrow, and waypoints with
a live distance/bearing-from-current-position readout."""
import pyqtgraph as pg
from PySide6.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget, QInputDialog, QLabel,
)

_MPS_TO_KNOTS = 1.943844


class GpsPanel(QDockWidget):
    def __init__(self, gps_track, parent=None):
        super().__init__("GPS / Nav Track", parent)
        self.gps_track = gps_track

        self._plot_widget = pg.PlotWidget()
        self._plot = self._plot_widget.getPlotItem()
        self._plot.setAspectLocked(True)
        self._plot.showGrid(x=True, y=True, alpha=0.3)

        self._track_curve = self._plot.plot([], [], pen=pg.mkPen((80, 160, 255), width=2))
        self._current_marker = pg.ScatterPlotItem(
            size=12, brush=pg.mkBrush(255, 200, 0), pen=pg.mkPen(None))
        self._plot.addItem(self._current_marker)

        self._waypoint_scatter = pg.ScatterPlotItem(
            size=14, symbol='d', brush=pg.mkBrush(60, 220, 100), pen=pg.mkPen('k'))
        self._plot.addItem(self._waypoint_scatter)

        self._heading_arrow = pg.ArrowItem(angle=0, brush=pg.mkBrush(255, 60, 60))
        self._heading_visible = False

        self._x_axis = self._plot.getAxis('bottom')
        self._y_axis = self._plot.getAxis('left')
        self._x_axis.tickStrings = self._lon_tick_strings
        self._y_axis.tickStrings = self._lat_tick_strings

        self.speed_heading_label = QLabel("Speed: — kn   Heading: —°")

        self.add_waypoint_button = QPushButton("Add Waypoint Here")
        self.add_waypoint_button.clicked.connect(self._on_add_waypoint_clicked)
        self.remove_waypoint_button = QPushButton("Remove Selected")
        self.remove_waypoint_button.clicked.connect(self._on_remove_waypoint_clicked)
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_waypoint_button)
        buttons.addWidget(self.remove_waypoint_button)

        self.waypoint_list = QListWidget()
        # A handful of rows' worth -- the plot is the point of this panel,
        # the waypoint list is a small side reference, not a peer.
        self.waypoint_list.setMaximumHeight(90)

        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.addWidget(self._plot_widget, 1)  # stretch factor: plot claims all extra space
        layout.addWidget(self.speed_heading_label)
        layout.addLayout(buttons)
        layout.addWidget(self.waypoint_list)
        body.setLayout(layout)

        self.setWidget(body)

    def _lon_tick_strings(self, values, scale, spacing):
        if self.gps_track.ref_lat is None:
            return [f"{v:.0f}" for v in values]
        out = []
        for v in values:
            _lat, lon = self.gps_track.unproject(v, 0.0)
            out.append(f"{lon:.5f}°")
        return out

    def _lat_tick_strings(self, values, scale, spacing):
        if self.gps_track.ref_lat is None:
            return [f"{v:.0f}" for v in values]
        out = []
        for v in values:
            lat, _lon = self.gps_track.unproject(0.0, v)
            out.append(f"{lat:.5f}°")
        return out

    def _on_add_waypoint_clicked(self) -> None:
        if self.gps_track._last_latlon is None:
            return
        name, ok = QInputDialog.getText(self, "Add Waypoint", "Name:")
        if not ok:
            return
        lat, lon = self.gps_track._last_latlon
        self.gps_track.add_waypoint(lat, lon, name)
        self.refresh()

    def _on_remove_waypoint_clicked(self) -> None:
        row = self.waypoint_list.currentRow()
        if row < 0 or row >= len(self.gps_track.waypoints):
            return
        wp_id = self.gps_track.waypoints[row]['id']
        self.gps_track.remove_waypoint(wp_id)
        self.refresh()

    def refresh(self, heading: float | None = None) -> None:
        track = self.gps_track
        self._track_curve.setData(track.xs, track.ys)

        if track.has_data:
            x, y = track.xs[-1], track.ys[-1]
            self._current_marker.setData([x], [y])
        else:
            self._current_marker.setData([], [])

        if heading is not None and track.has_data:
            x, y = track.xs[-1], track.ys[-1]
            # Best-effort conversion from compass bearing (0=N, clockwise)
            # to pyqtgraph ArrowItem angle (0=pointing left, CCW positive).
            # Not yet verified against real hardware heading output.
            self._heading_arrow.setStyle(angle=180 - heading)
            self._heading_arrow.setPos(x, y)
            if not self._heading_visible:
                self._plot.addItem(self._heading_arrow)
                self._heading_visible = True
        else:
            if self._heading_visible:
                self._plot.removeItem(self._heading_arrow)
                self._heading_visible = False

        self._refresh_waypoints()

        speed_kn = track.speed_mps * _MPS_TO_KNOTS
        heading_text = f"{heading:.0f}°" if heading is not None else "—"
        self.speed_heading_label.setText(f"Speed: {speed_kn:.1f} kn   Heading: {heading_text}")

    def _refresh_waypoints(self) -> None:
        xs, ys = [], []
        self.waypoint_list.clear()
        for wp in self.gps_track.waypoints:
            xy = self.gps_track.waypoint_xy(wp['id'])
            if xy is not None:
                xs.append(xy[0])
                ys.append(xy[1])
            label = wp['name'] or f"WP{wp['id']}"
            bd = self.gps_track.bearing_distance_to_waypoint(wp['id'])
            if bd is not None:
                dist, brg = bd
                label += f" — {dist:.0f} m @ {brg:.0f}°"
            self.waypoint_list.addItem(label)
        self._waypoint_scatter.setData(xs, ys)
