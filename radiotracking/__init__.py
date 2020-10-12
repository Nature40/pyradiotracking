import numpy as np
import datetime
from si_prefix import si_format


class Signal:
    def __init__(
            self,
            ts: datetime.datetime,
            frequency: float,
            duration_s: float,
            data=None,
    ):
        if isinstance(ts, datetime.datetime):
            self.ts = ts
        else:
            self.ts = datetime.datetime.fromisoformat(ts)
        self.frequency = frequency
        self.duration_s = duration_s
        self.data = data

        # statistical data of the data is generated with a padding
        # circumventing the blur at the edges (esp. important for min and var values)
        self.data_padding = 0

    @property
    def data_padded(self):
        if self.data_padding:
            return self.data[self.data_padding:-self.data_padding]
        else:
            return self.data

    @property
    def max(self):
        return max(self.data_padded)

    @property
    def min(self):
        return min(self.data_padded)

    @property
    def avg(self):
        return np.average(self.data_padded)

    @property
    def var(self):
        return np.var(self.data_padded)

    @property
    def sum(self):
        return sum(self.data)

    header = [
        "Time",
        "Frequency (MHz)",
        "Duration (ms)",
        "min",
        "max",
        "sum",
        "avg",
        "var",
    ]

    @property
    def _raw_list(self):
        return [
            self.ts,
            self.frequency,
            self.duration_s,
            self.min,
            self.max,
            self.sum,
            self.avg,
            self.var,
        ]

    @property
    def as_list(self):
        ret = [
            f"{self.ts:%Y-%m-%dT%H%M%S}",
            self.frequency / 1000 / 1000,
            self.duration_s * 100,
            self.min,
            self.max,
            self.sum,
            self.avg,
            self.var,
        ]
        return ret

    @property
    def as_dict(self):
        return dict(zip(self.header, self.as_list))

    def __repr__(self):
        return f"Signal(\"{self.ts}\", {self.frequency}, {self.duration_s:13})"
