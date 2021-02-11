import argparse
import collections
import threading
from typing import DefaultDict, Deque, Iterable, List, Tuple

import dash
import dash_core_components as dcc
import dash_html_components as html
import plotly.graph_objs as go
from dash.dependencies import Input, Output
from werkzeug.serving import ThreadedWSGIServer

from radiotracking import AbstractSignal, MatchedSignal, Signal
from radiotracking.config import ArgConfParser
from radiotracking.consume import AbstractConsumer

SDR_COLORS: DefaultDict[str, str] = collections.defaultdict(lambda: "grey")
SDR_COLORS.update({
    "0": "blue",
    "1": "orange",
    "2": "red",
    "3": "green",
    "green": "green",
    "red": "red",
    "yellow": "yellow",
    "blue": "blue",
})


def group(sigs: Iterable[Signal], by: str) -> List[Tuple[str, List[Signal]]]:
    keys = sorted(set([sig.__dict__[by] for sig in sigs]))
    groups = []
    for key in keys:
        groups.append((key,
                       [sig for sig in sigs
                        if sig.__dict__[by] == key]
                       ))

    return groups


class Dashboard(AbstractConsumer, threading.Thread):
    def __init__(self,
                 dashboard_host: str,
                 dashboard_port: int,
                 dashboard_signals: int,
                 signal_min_duration_ms: int,
                 signal_max_duration_ms: int,
                 signal_threshold_dbw: float,
                 snr_threshold_db: float,
                 sample_rate: int,
                 center_freq: int,
                 signal_threshold_dbw_max: float = -20,
                 snr_threshold_db_max: float = 50,
                 **kwargs,
                 ):
        threading.Thread.__init__(self)
        self.signal_queue: Deque[Signal] = collections.deque(maxlen=dashboard_signals)
        self.matched_queue: Deque[MatchedSignal] = collections.deque(maxlen=dashboard_signals)

        # compute boundaries for sliders and initialize filters
        frequency_min = center_freq - sample_rate / 2
        frequency_max = center_freq + sample_rate / 2

        self.app = dash.Dash(__name__, meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}])

        graph_columns = html.Div(children=[], style={"columns": "2 359px"})
        graph_columns.children.append(
            html.Div(id="settings", style={"break-inside": "avoid-column"}, children=[
                html.H2("Vizualization Filters"),
                html.H3("Signal Power"),
                dcc.RangeSlider(
                    id="power-slider",
                    min=signal_threshold_dbw, max=signal_threshold_dbw_max, step=0.1,
                    value=[signal_threshold_dbw, signal_threshold_dbw_max],
                    marks={int(signal_threshold_dbw): f"{signal_threshold_dbw} dBW",
                           int(signal_threshold_dbw_max): f"{signal_threshold_dbw_max} dBW", },
                ),
                html.H3("SNR"),
                dcc.RangeSlider(
                    id="snr-slider",
                    min=snr_threshold_db, max=snr_threshold_db_max, step=0.1,
                    value=[snr_threshold_db, snr_threshold_db_max],
                    marks={int(snr_threshold_db): f"{snr_threshold_db} dBW",
                           int(snr_threshold_db_max): f"{snr_threshold_db_max} dBW", },
                ),
                html.H3("Frequency Range"),
                dcc.RangeSlider(
                    id="frequency-slider",
                    min=frequency_min, max=frequency_max, step=1,
                    marks={int(frequency_min): f"{frequency_min/1000/1000:.2f} MHz",
                           int(center_freq): f"{center_freq/1000/1000:.2f} MHz",
                           int(frequency_max): f"{frequency_max/1000/1000:.2f} MHz",
                           },
                    value=[frequency_min, frequency_max],
                    allowCross=False,
                ),
                html.H3("Signal Duration"),
                dcc.RangeSlider(
                    id="duration-slider",
                    min=signal_min_duration_ms, max=signal_max_duration_ms, step=0.1,
                    marks={int(signal_min_duration_ms): f"{signal_min_duration_ms} ms",
                           int(signal_max_duration_ms): f"{signal_max_duration_ms} ms",
                           },
                    value=[signal_min_duration_ms, signal_max_duration_ms],
                    allowCross=False,
                ),
                html.H2("Dashboard Update Interval"),
                dcc.Slider(
                    id="interval-slider",
                    min=0.1, max=10, step=0.1,
                    value=1.0,
                    marks={0.1: "0.1 s",
                           1: "1 s",
                           5: "5 s",
                           10: "10 s", },
                ),
            ]))
        graph_columns.children.append(dcc.Graph(id="signal-noise", style={"break-inside": "avoid-column"}))
        self.app.callback(Output("signal-noise", "figure"), [
            Input("update", "n_intervals"),
            Input("power-slider", "value"),
            Input("snr-slider", "value"),
            Input("frequency-slider", "value"),
            Input("duration-slider", "value"),
        ])(self.update_signal_noise)

        graph_columns.children.append(dcc.Graph(id="frequency-histogram", style={"break-inside": "avoid-column"}))
        self.app.callback(Output("frequency-histogram", "figure"), [
            Input("update", "n_intervals"),
            Input("power-slider", "value"),
            Input("snr-slider", "value"),
            Input("frequency-slider", "value"),
            Input("duration-slider", "value"),
        ])(self.update_frequency_histogram)

        graph_columns.children.append(dcc.Graph(id="signal-match", style={"break-inside": "avoid-column"}))
        self.app.callback(Output("signal-match", "figure"), [
            Input("update", "n_intervals"),
        ])(self.update_signal_match)

        graph_columns.children.append(dcc.Graph(id="signal-variance", style={"break-inside": "avoid-column"}))
        self.app.callback(Output("signal-variance", "figure"), [
            Input("update", "n_intervals"),
            Input("power-slider", "value"),
            Input("snr-slider", "value"),
            Input("frequency-slider", "value"),
            Input("duration-slider", "value"),
        ])(self.update_signal_variance)

        graph_tab = html.Div(children=[])
        graph_tab.children.append(dcc.Interval(id="update", interval=1000))
        self.app.callback(Output("update", "interval"), [Input("interval-slider", "value")])(self.update_interval)

        graph_tab.children.append(html.Div([dcc.Graph(id="signal-time"), ]))
        self.app.callback(Output("signal-time", "figure"), [
            Input("update", "n_intervals"),
            Input("power-slider", "value"),
            Input("snr-slider", "value"),
            Input("frequency-slider", "value"),
            Input("duration-slider", "value"),
        ])(self.update_signal_time)

        graph_tab.children.append(graph_columns)

        self.app.layout = graph_tab
        self.app.layout.style = {"font-family": "sans-serif"}

        self.server = ThreadedWSGIServer(dashboard_host, dashboard_port, self.app.server)

    def add(self, signal: AbstractSignal):
        if isinstance(signal, Signal):
            self.signal_queue.append(signal)
        elif isinstance(signal, MatchedSignal):
            self.matched_queue.append(signal)

    def update_interval(self, interval):
        return interval * 1000

    def select_sigs(self, power: List[float], snr: List[float], freq: List[float], duration: List[float]):
        return [sig for sig in self.signal_queue
                if sig.avg > power[0] and sig.avg < power[1]
                and sig.snr > snr[0] and sig.snr < snr[1]
                and sig.frequency > freq[0] and sig.frequency < freq[1]
                and sig.duration.total_seconds() * 1000 > duration[0] and sig.duration.total_seconds() * 1000 < duration[1]]

    def update_signal_time(self, n, power, snr, freq, duration):
        traces = []
        sigs = self.select_sigs(power, snr, freq, duration)

        for trace_sdr, sdr_sigs in group(sigs, "device"):
            trace = go.Scatter(
                x=[sig.ts for sig in sdr_sigs],
                y=[sig.avg for sig in sdr_sigs],
                name=trace_sdr,
                mode="markers",
                marker=dict(
                    size=[sig.duration.total_seconds() * 1000 for sig in sdr_sigs],
                    opacity=0.5,
                    color=SDR_COLORS[trace_sdr],
                ),
            )
            traces.append(trace)

        return {
            "data": traces,
            "layout": {
                "xaxis": {"title": "Time"},
                "yaxis": {"title": "Signal Power (dBW)",
                          "range": power},
                "legend": {"title": "SDR Receiver"},
            },
        }

    def update_signal_noise(self, n, power, snr, freq, duration):
        traces = []
        sigs = self.select_sigs(power, snr, freq, duration)

        for trace_sdr, sdr_sigs in group(sigs, "device"):
            trace = go.Scatter(
                x=[sig.snr for sig in sdr_sigs],
                y=[sig.avg for sig in sdr_sigs],
                name=trace_sdr,
                mode="markers",
                marker=dict(
                    size=[sig.duration.total_seconds() * 1000 for sig in sdr_sigs],
                    opacity=0.3,
                    color=SDR_COLORS[trace_sdr],
                ),
            )
            traces.append(trace)

        return {
            "data": traces,
            "layout": {
                "title": "Signal to Noise",
                "xaxis": {"title": "SNR (dB)"},
                "yaxis": {"title": "Signal Power (dBW)",
                          "range": power},
                "legend": {"title": "SDR Receiver"},
            },
        }

    def update_signal_variance(self, n, power, snr, freq, duration):
        traces = []
        sigs = self.select_sigs(power, snr, freq, duration)

        for trace_sdr, sdr_sigs in group(sigs, "device"):
            trace = go.Scatter(
                x=[sig.std for sig in sdr_sigs],
                y=[sig.avg for sig in sdr_sigs],
                name=trace_sdr,
                mode="markers",
                marker=dict(
                    size=[sig.duration.total_seconds() * 1000 for sig in sdr_sigs],
                    opacity=0.3,
                    color=SDR_COLORS[trace_sdr],
                ),
            )
            traces.append(trace)

        return {
            "data": traces,
            "layout": {
                "title": "Signal Variance",
                "xaxis": {"title": "Standard Deviation (dB)"},
                "yaxis": {"title": "Signal Power (dBW)",
                          "range": power},
                "legend": {"title": "SDR Receiver"},
            },
        }

    def update_frequency_histogram(self, n, power, snr, freq, duration):
        traces = []
        sigs = self.select_sigs(power, snr, freq, duration)

        for trace_sdr, sdr_sigs in group(sigs, "device"):
            trace = go.Scatter(
                x=[sig.frequency for sig in sdr_sigs],
                y=[sig.avg for sig in sdr_sigs],
                name=trace_sdr,
                mode="markers",
                marker=dict(
                    size=[sig.duration.total_seconds() * 1000 for sig in sdr_sigs],
                    opacity=0.3,
                    color=SDR_COLORS[trace_sdr],
                ),
            )
            traces.append(trace)

        return {
            "data": traces,
            "layout": {
                "title": "Frequency Usage",
                "xaxis": {"title": "Frequency (MHz)",
                          "range": freq},
                "yaxis": {"title": "Signal Power (dBW)",
                          "range": power},
                "legend_title_text": "SDR Receiver",
            },
        }

    def update_signal_match(self, n):
        traces = []

        completed_signals = [msig for msig in self.matched_queue if len(msig._sigs) == 4]

        trace = go.Scatter(
            x=[msig._sigs["0"].avg - msig._sigs["2"].avg for msig in completed_signals],
            y=[msig._sigs["1"].avg - msig._sigs["3"].avg for msig in completed_signals],
            mode="markers",
            marker=dict(
                color=[msig.ts.timestamp() for msig in completed_signals],
                colorscale='Cividis_r',
                opacity=0.5,
            )
        )
        traces.append(trace)

        return {
            "data": traces,
            "layout": {
                "title": "Matched Frequencies",
                "xaxis": {"title": "Horizontal Difference",
                          "range": [-50, 50],
                          },
                "yaxis": {"title": "Vertical Difference",
                          "range": [-50, 50],
                          },
            },
        }

    def run(self):
        self.server.serve_forever()

    def stop(self):
        self.server.shutdown()
