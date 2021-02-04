import collections
import os
import threading
from typing import Deque

import dash
import dash_core_components as dcc
import dash_html_components as html
import plotly.graph_objs as go
from dash.dependencies import Input, Output
from flask import send_from_directory
from werkzeug.serving import BaseWSGIServer

from radiotracking import AbstractSignal, Signal
from radiotracking.consume import AbstractConsumer

SDR_COLORS = {"3": "blue", "2": "orange", "1": "red", "0": "green", }


class Dashboard(AbstractConsumer, threading.Thread):
    def __init__(self,
                 dashboard_host: str,
                 dashboard_port: int,
                 dashboard_signals: int = 50,
                 **kwargs,
                 ):
        threading.Thread.__init__(self)
        self.viz_queue: Deque[Signal] = collections.deque(maxlen=dashboard_signals)

        self.app = dash.Dash(__name__)
        self.app.layout = html.Div(children=[
            html.Div([
                html.H1(children='RadioTracking Dashboard'),
                dcc.Graph(id="signal-time"),
                dcc.Interval(id="signal-time-update", interval=1000),
            ]),
            html.Div([
                dcc.Graph(id="signal-noise", style={'display': 'inline-block', "width": "50%"}),
                dcc.Interval(id="signal-noise-update", interval=1000,),
                dcc.Graph(id="frequency-histogram", style={'display': 'inline-block', "width": "50%"}),
                dcc.Interval(id="frequency-histogram-update", interval=1000,),
            ],
            ),
        ])

        self.app.callback(Output("signal-time", "figure"), [Input("signal-time-update", "n_intervals")])(self.update_signal_time)
        self.app.callback(Output("signal-noise", "figure"), [Input("signal-noise-update", "n_intervals")])(self.update_signal_noise)
        self.app.callback(Output("frequency-histogram", "figure"), [Input("frequency-histogram-update", "n_intervals")])(self.update_frequency_histogram)

        self.app.server.route('/static/<path:path>')(self.static_file)

        self.server = BaseWSGIServer(dashboard_host, dashboard_port, self.app.server)

    def add(self, signal: AbstractSignal):
        if isinstance(signal, Signal):
            self.viz_queue.append(signal)

    def update_signal_time(self, n):
        sdrs = ["0", "1", "2", "3"]
        traces = []

        for trace_sdr in sdrs:
            trace = go.Scatter(
                x=[sig.ts for sig in self.viz_queue if sig.device == trace_sdr],
                y=[sig.avg for sig in self.viz_queue if sig.device == trace_sdr],
                name=trace_sdr,
                mode="markers",
                marker=dict(
                    size=[sig.duration.total_seconds() * 1000 for sig in self.viz_queue if sig.device == trace_sdr],
                    opacity=0.5,
                    color=SDR_COLORS[trace_sdr],
                ),
            )
            traces.append(trace)

        return {
            "data": traces,
            "layout": {
                "xaxis": {"title": "Time"},
                "yaxis": {"title": "Signal Power (dBW)"},
                "legend": {"title": "SDR Receiver"},
            },
        }

    def update_signal_noise(self, n):
        sdrs = ["0", "1", "2", "3"]
        traces = []

        for trace_sdr in sdrs:
            trace = go.Scatter(
                x=[sig.snr for sig in self.viz_queue if sig.device == trace_sdr],
                y=[sig.avg for sig in self.viz_queue if sig.device == trace_sdr],
                name=trace_sdr,
                mode="markers",
                marker=dict(
                    size=[sig.duration.total_seconds() * 1000 for sig in self.viz_queue if sig.device == trace_sdr],
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
                "yaxis": {"title": "Signal Power (dBW)"},
                "legend": {"title": "SDR Receiver"},
            },
        }

    def update_frequency_histogram(self, n):
        sdrs = ["0", "1", "2", "3"]
        traces = []

        for trace_sdr in sdrs:
            trace = go.Scatter(
                x=[sig.frequency / 1000 / 1000 for sig in self.viz_queue if sig.device == trace_sdr],
                y=[sig.avg for sig in self.viz_queue if sig.device == trace_sdr],
                name=trace_sdr,
                mode="markers",
                marker=dict(
                    size=[sig.duration.total_seconds() * 1000 for sig in self.viz_queue if sig.device == trace_sdr],
                    opacity=0.3,
                    color=SDR_COLORS[trace_sdr],
                ),
            )
            traces.append(trace)

        return {
            "data": traces,
            "layout": {
                "title": "Frequency Usage",
                "xaxis": {"title": "Frequency (MHz)"},
                "yaxis": {"title": "Signal Power (dBW)"},
                "legend_title_text": "SDR Receiver",
            },
        }

    def static_file(self, path):
        static_folder = os.path.join(os.getcwd(), 'static')
        return send_from_directory(static_folder, path)

    def run(self):
        self.server.serve_forever()

    def stop(self):
        self.server.shutdown()
