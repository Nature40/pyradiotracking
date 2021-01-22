import datetime
import statistics
from abc import ABC, abstractmethod
from typing import Dict, List

import numpy as np


def dB(val):
    return 10 * np.log10(val)


def from_dB(dB):
    return 10 ** (dB / 10)


class AbstractSignal(ABC):
    ts: datetime.datetime
    header: List[str]

    @property
    @abstractmethod
    def as_list(self):
        pass

    @property
    def as_dict(self):
        return dict(zip(self.header, self.as_list))


class Signal(AbstractSignal):
    def __init__(
        self,
        ts: datetime.datetime,
        frequency: float,
        duration: datetime.timedelta,
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
        if isinstance(duration, datetime.timedelta):
            self.duration = duration
        else:
            self.duration = datetime.timedelta(duration)

        self.max = max_dBW
        self.min = min_dBW
        self.avg = avg_dBW
        self.std = std_dB
        self.snr = snr_dB

    @property
    def ts_mid(self):
        return self.ts + (self.duration / 2.0)

    header = [
        "Time",
        "Frequency",
        "Duration",
        "min (dBW)",
        "max (dBW)",
        "avg (dBW)",
        "std (dB)",
        "snr (dB)",
    ]

    @property
    def as_list(self):
        return [
            self.ts,
            self.frequency,
            self.duration,
            self.min,
            self.max,
            self.avg,
            self.std,
            self.snr,
        ]

    def __repr__(self):
        return f"Signal({self.ts}, {self.frequency}, {self.duration}, {self.min}, {self.max}, {self.avg}, {self.std}, {self.snr})"

    def __str__(self):
        return f"Signal<{self.frequency/1000/1000} MHz, {self.duration.total_seconds()*1000:.2} ms, {self.max} dBW>"


class MatchedSignal(AbstractSignal):
    def __init__(self, device: str, sig: Signal):
        self._sigs: Dict[str, Signal] = {device: sig}

    @property
    def duration(self):
        return max([sig.duration for sig in self._sigs.values()])

    @property
    def ts(self):
        return min([sig.ts for sig in self._sigs.values()])

    @property
    def ts_mid(self):
        return self.ts + (self.duration / 2.0)

    @property
    def frequency(self):
        return statistics.median([sig.frequency for sig in self._sigs.values()])

    header = [
        "Time",
        "Frequency",
        "Duration",
        "Count",
    ]

    @property
    def as_list(self):
        return [
            self.ts,
            self.frequency,
            self.duration,
            len(self._sigs),
        ]

    def __str__(self):
        return f"MatchedSignal<{self.ts_mid}, {self.frequency/1000/1000} MHz, members {list(self._sigs.keys())}>"

    def has_member(self, sig: Signal) -> bool:
        # if freq differs
        if self.frequency != sig.frequency:
            return False

        # is sig starts after middle of our sig
        if self.ts_mid < sig.ts:
            return False
        # if sig ends before middle of our sig
        if self.ts_mid > sig.ts + sig.duration:
            return False

        return True

    def add_member(self, device: str, sig: Signal):
        self._sigs[device] = sig
