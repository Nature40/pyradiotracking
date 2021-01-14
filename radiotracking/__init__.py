import datetime
import numpy as np


def dB(val):
    return 10 * np.log10(val)


def from_dB(dB):
    return 10 ** (dB / 10)


class Signal:
    def __init__(
        self,
        ts: datetime.datetime,
        frequency: float,
        duration_s: float,
        min_dBW: float,
        max_dBW: float,
        avg_dBW: float,
        std_dB: float,
        snr_dB: float,
    ):
        if isinstance(ts, datetime.datetime):
            self.ts = ts
        else:
            self.ts = datetime.datetime.fromisoformat(ts)
        self.frequency = frequency
        self.duration_s = duration_s

        self.max = max_dBW
        self.min = min_dBW
        self.avg = avg_dBW
        self.std = std_dB
        self.snr = snr_dB

    header = [
        "Time",
        "Frequency (MHz)",
        "Duration (ms)",
        "min (dBW)",
        "max (dBW)",
        "avg (dBW)",
        "std (dB)",
        "snr (dB)",
    ]

    @property
    def as_list(self):
        ret = [
            f"{self.ts:%Y-%m-%dT%H%M%S.%f}",
            self.frequency / 1000 / 1000,
            self.duration_s * 1000,
            self.min,
            self.max,
            self.avg,
            self.std,
            self.snr,
        ]
        return ret

    @property
    def as_dict(self):
        return dict(zip(self.header, self.as_list))

    def __repr__(self):
        return f"Signal({self.ts}, {self.frequency}, {self.duration_s}, {self.min}, {self.max}, {self.avg}, {self.std}, {self.snr})"

    def __str__(self):
        return f"Signal<{self.frequency/1000/1000} MHz, {self.duration_s*1000:.2} ms, {self.max} dBW>"
