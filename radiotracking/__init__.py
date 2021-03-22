import datetime
import logging
import statistics
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Union

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
    max_dBW: float
    avg_dBW: float
    std_dB: float
    snr_dB: float
    device: str

    def __init__(
        self,
        device: str,
        ts: Union[datetime.datetime, str],
        frequency: Union[float, str],
        duration: Union[datetime.timedelta, float, str],
        max_dBW: Union[float, str],
        avg_dBW: Union[float, str],
        std_dB: Union[float, str],
        noise_dBW: Union[float, str],
        snr_dB: Union[float, str],
    ):
        self.device = device
        if isinstance(ts, datetime.datetime):
            self.ts = ts
        else:
            self.ts = datetime.datetime.fromisoformat(ts)
        self.frequency = float(frequency)
        if isinstance(duration, datetime.timedelta):
            self.duration = duration
        else:
            self.duration = datetime.timedelta(seconds=float(duration))

        self.max = float(max_dBW)
        self.avg = float(avg_dBW)
        self.std = float(std_dB)
        self.noise = float(noise_dBW)
        self.snr = float(snr_dB)

    header = [
        "Device",
        "Time",
        "Frequency",
        "Duration",
        "max (dBW)",
        "avg (dBW)",
        "std (dB)",
        "noise (dBW)",
        "snr (dB)",
    ]

    @property
    def as_list(self):
        return [
            self.device,
            self.ts,
            self.frequency,
            self.duration,
            self.max,
            self.avg,
            self.std,
            self.noise,
            self.snr,
        ]

    def __repr__(self):
        return f"Signal({self.device}, {self.ts}, {self.frequency}, {self.duration}, {self.max}, {self.avg}, {self.std}, {self.noise}, {self.snr})"

    def __str__(self):
        return f"Signal<{self.device}, {self.frequency/1000/1000} MHz, {self.duration.total_seconds()*1000:.2} ms, {self.max} dBW>"


class MatchedSignal(AbstractSignal):
    def __init__(
        self,
        devices: List[str],
        ts: Union[datetime.datetime, str],
        frequency: Union[float, str],
        duration: Union[datetime.timedelta, float, str],
        *avgs: float,
    ):
        self.devices = devices

        if isinstance(ts, datetime.datetime):
            self.ts = ts
        else:
            self.ts = datetime.datetime.fromisoformat(ts)
        self.frequency = float(frequency)
        if isinstance(duration, datetime.timedelta):
            self.duration = duration
        else:
            self.duration = datetime.timedelta(seconds=float(duration))

        self._avgs = avgs

    @property
    def header(self):
        return [
            "Time",
            "Frequency",
            "Duration",
            *self.devices,
        ]

    @property
    def as_list(self) -> list:
        return [
            self.ts,
            self.frequency,
            self.duration,
            *self._avgs
        ]

    def __repr__(self) -> str:
        avgs_str = ", ".join([repr(a) for a in self._avgs])
        return f"MatchedSignal({self.devices}, {self.ts}, {self.frequency}, {self.duration}, {avgs_str})"


class MatchingSignal(MatchedSignal):
    def __init__(self, devices: List[str]):
        self.devices = devices
        self._sigs: Dict[str, Signal] = {}

    @property
    def duration(self):
        return max([sig.duration for sig in self._sigs.values()])

    @property
    def ts(self):
        return min([sig.ts for sig in self._sigs.values()])

    @property
    def frequency(self):
        return statistics.median([sig.frequency for sig in self._sigs.values()])

    @property
    def _avgs(self):
        return [self._sigs[d].avg if d in self._sigs else None for d in self.devices]

    def has_member(self,
                   sig: Signal,
                   time_diff: datetime.timedelta = datetime.timedelta(seconds=0),
                   bandwidth: float = 0,
                   duration_diff: Optional[datetime.timedelta] = None,
                   ) -> bool:

        # if freq (including bw) out of range of freq
        if sig.frequency - bandwidth / 2 > self.frequency:
            logger.debug(f"1 {sig.frequency - bandwidth / 2} > {self.frequency}")
            return False
        if sig.frequency + bandwidth / 2 < self.frequency:
            logger.debug(f"2 {sig.frequency + bandwidth / 2} < {self.frequency}")
            return False

        # if start (minus diff) is after end
        if sig.ts - time_diff > (self.ts + self.duration):
            logger.debug(f"3 {sig.ts - time_diff} > {(self.ts + self.duration)}")
            return False
        # if end (plus diff) is before start
        if (sig.ts + sig.duration) + time_diff < self.ts:
            logger.debug(f"4 {(sig.ts + sig.duration) + time_diff} < {self.ts}")
            return False

        # if no duration_diff is present, don't match for it
        if duration_diff:
            if sig.duration - (duration_diff / 2) > self.duration:
                return False
            if sig.duration + (duration_diff / 2) < self.duration:
                return False

        return True

    def add_member(self, sig: Signal):
        if sig.device in self._sigs:
            logger.warning(f"Signal of {sig.device} already contained in {self}")
        self._sigs[sig.device] = sig
