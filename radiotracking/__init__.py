import datetime
import logging
import statistics
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


def dB(val):
    return 10 * np.log10(val)


def from_dB(dB):
    return 10 ** (dB / 10)


class AbstractSignal(ABC):
    ts: datetime.datetime
    frequency: float
    duration: datetime.timedelta
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
    def ts_mid(self) -> datetime.datetime:
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
    def as_list(self) -> list:
        return [
            self.ts,
            self.frequency,
            self.duration,
            len(self._sigs),
        ]

    def __str__(self):
        return f"MatchedSignal<{self.ts_mid}, {self.frequency/1000/1000} MHz, members {list(self._sigs.keys())}>"

    def has_member(self,
                   sig: Signal,
                   time_diff: datetime.timedelta = datetime.timedelta(seconds=0),
                   bandwidth: float = 0,
                   duration_diff: Optional[datetime.timedelta] = None,
                   ) -> bool:

        # if freq (including bw) out of range of freq
        if sig.frequency - bandwidth / 2 > self.frequency:
            logger.debug(f"{sig.frequency - bandwidth / 2} > {self.frequency}")
            return False
        if sig.frequency + bandwidth / 2 < self.frequency:
            logger.debug(f"{sig.frequency + bandwidth / 2} < {self.frequency}")
            return False

        # if start (minus diff) is after end
        if sig.ts - (time_diff / 2) > (self.ts + self.duration):
            logger.debug(f"{sig.ts - (time_diff / 2)} > {(self.ts + self.duration)}")
            return False
        # if end (plus diff) is before start
        if (sig.ts + sig.duration) + (time_diff / 2) < self.ts:
            logger.debug(f"{(sig.ts + sig.duration) + (time_diff / 2)} < {self.ts}")
            return False

        # if no duration_diff is present, don't match for it
        if duration_diff:
            if sig.duration - (duration_diff / 2) > self.duration:
                return False
            if sig.duration + (duration_diff / 2) < self.duration:
                return False

        return True

    def add_member(self, device: str, sig: Signal):
        self._sigs[device] = sig
