"""Dockable GPS/nav track panel: plots the accumulated GPSTrack history,
the current position, and (live mode only) a heading arrow."""
import pyqtgraph as pg
from PySide6.QtWidgets import QDockWidget


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

        self._heading_arrow = pg.ArrowItem(angle=0, brush=pg.mkBrush(255, 60, 60))
        self._heading_visible = False

        self._x_axis = self._plot.getAxis('bottom')
        self._y_axis = self._plot.getAxis('left')
        self._x_axis.tickStrings = self._lon_tick_strings
        self._y_axis.tickStrings = self._lat_tick_strings

        self.setWidget(self._plot_widget)

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
