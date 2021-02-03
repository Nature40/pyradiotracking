import collections
import threading
from typing import Deque

import dash
import dash_core_components as dcc
import dash_html_components as html
import plotly.graph_objs as go
from dash.dependencies import Input, Output
from werkzeug.serving import BaseWSGIServer

from radiotracking import AbstractSignal, Signal
from radiotracking.consume import AbstractConsumer


class Dashboard(AbstractConsumer, threading.Thread):
    def __init__(self,
                 dashboard_host: str,
                 dashboard_port: int,
                 dashboard_signals: int,
                 **kwargs,
                 ):
        threading.Thread.__init__(self)
        self.viz_queue: Deque[Signal] = collections.deque(maxlen=dashboard_signals)

        self.app = dash.Dash(__name__)
        self.app.layout = html.Div(
            [
                dcc.Graph(
                    id="live-graph",
                    figure={},
                ),
                dcc.Interval(
                    id="graph-update",
                    interval=1000,
                ),
            ]
        )
        self.app.callback(Output("live-graph", "figure"),
                          [Input("graph-update", "n_intervals")])(self.update_graph_scatter)

        self.server = BaseWSGIServer(dashboard_host, dashboard_port, self.app.server)

    def add(self, signal: AbstractSignal):
        if isinstance(signal, Signal):
            self.viz_queue.append(signal)

    def update_graph_scatter(self, n):
        sdrs = ["0", "1", "2", "3"]
        traces = []

        for trace_sdr in sdrs:
            trace = go.Scatter(
                x=[sig.ts for sig in self.viz_queue if sig.device == trace_sdr],
                y=[sig.avg for sig in self.viz_queue if sig.device == trace_sdr],
                name=trace_sdr,
                # mode="markers",
            )
            traces.append(trace)

        return {
            "data": traces,
            "layout": {
                "title": "RadioTracking Signals",
                "xaxis": {"title": "Time"},
                "yaxis": {"title": "power (dBW)"},
                "legend": {"title": "SDR Receiver"},
            },
        }

    def run(self):
        self.server.serve_forever()

    def stop(self):
        self.server.shutdown()
