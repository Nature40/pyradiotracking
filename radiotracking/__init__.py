import numpy as np
from si_prefix import si_format


class Signal:
    def __init__(self, frequency, duration_s, ts, data):
        self.frequency = frequency
        self.duration_s = duration_s
        self.ts = ts
        self.data = data

        self.padding = 1

    @property
    def max(self):
        return max(self.data[self.padding:-self.padding])

    @property
    def min(self):
        return min(self.data[self.padding:-self.padding])

    @property
    def avg(self):
        return np.average(self.data[self.padding:-self.padding])

    @property
    def var(self):
        return np.var(self.data[self.padding:-self.padding])

    @property
    def sum(self):
        return sum(self.data)

    def __str__(self):
        return f"signal: \
{si_format(self.frequency, precision=3)}Hz, \
{si_format(self.duration_s)}s, \
timestamp: {self.ts} \
power min:{si_format(self.min)}, avg:{si_format(self.avg)}, max:{si_format(self.max)}, sum:{si_format(self.sum)}, var: {si_format(self.var)}"
