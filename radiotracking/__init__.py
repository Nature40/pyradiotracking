import datetime
import logging
import statistics
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Union

import numpy as np

logger = logging.getLogger(__name__)


def dB(val: float):
    """
    Convert a power value to dB.
    """
    return 10 * np.log10(val)


def from_dB(dB: float):
    """Convert a dB value to power."""
    return 10 ** (dB / 10)


class AbstractSignal(ABC):
    """
    Abstract class for a signal.
    """
    header: List[str]
    """Header for the as_list() method."""

    def __init__(self) -> None:
        super().__init__()

        self.ts: datetime.datetime
        """Timestamp of the signal."""
        self.frequency: float
        """Frequency of the signal in Hz."""
        self.duration: datetime.timedelta
        """Duration of the signal."""

    @property
    @abstractmethod
    def as_list(self) -> List:
        """
        Return the signal as a list of values.

        Returns
        -------
        typing.List[typing.Any]
        """

    @property
    def as_dict(self) -> Dict:
        """
        Return the signal as a dictionary.

        Returns
        -------
        typing.Dict[str, typing.Any]
        """
        return dict(zip(self.header, self.as_list))


class Signal(AbstractSignal):
    """
    Signal detected on a single device.

    Parameters
    ----------
    device: str 
        The device that detected the signal.
    ts: typing.Union[datetime.datetime, str]
        The timestamp of the signal.
    frequency: typing.Union[float, str] 
        The frequency of the signal.
    duration: typing.Union[datetime.timedelta, float, str]
        The duration of the signal.
    max_dBW: typing.Union[float, str]
        The maximum power of the signal.
    avg_dBW: typing.Union[float, str]
        The average power of the signal.
    std_dB: typing.Union[float, str]
        The standard deviation in power of the signal.
    noise_dBW: typing.Union[float, str]
        The noise level of the signal.
    snr_dB: typing.Union[float, str]
        The signal to noise ratio of the signal.
    """

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
        super().__init__()

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

        self.max: float = float(max_dBW)
        """The maximum power of the signal."""
        self.avg: float = float(avg_dBW)
        """The average power of the signal."""
        self.std: float = float(std_dB)
        """The standard deviation in power of the signal."""
        self.noise: float = float(noise_dBW)
        """The noise level of the signal."""
        self.snr: float = float(snr_dB)
        """The signal to noise ratio of the signal."""

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
        return f"Signal<SDR {self.device}, {self.frequency/1000/1000:.3f} MHz, {self.duration.total_seconds()*1000:.2f} ms, {self.max:.1f} dBW>"


class MatchedSignal(AbstractSignal):
    """
    Matched Signal detected on multiple devices.

    Parameters
    ----------
    devices: typing.List[str]
        The devices that could have detected the signal.
    ts: typing.Union[datetime.datetime, str]
        The timestamp of the signal.
    frequency: typing.Union[float, str]
        The frequency of the signal.
    duration: typing.Union[datetime.timedelta, float, str]
        The duration of the signal.
    avgs: typing.List[float]
        The average powers detected on the available devices.
    """

    def __init__(
        self,
        devices: List[str],
        ts: Union[datetime.datetime, str],
        frequency: Union[float, str],
        duration: Union[datetime.timedelta, float, str],
        *avgs: float,
    ):
        super().__init__()
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

        self._avgs: List[float] = avgs

    @property
    def header(self) -> List[str]:
        """Header for the as_list() method."""
        return [
            "Time",
            "Frequency",
            "Duration",
            *self.devices,
        ]

    @property
    def as_list(self) -> List:
        return [
            self.ts,
            self.frequency,
            self.duration,
            *self._avgs
        ]

    def __repr__(self) -> str:
        avgs_str = ", ".join([repr(avg) for avg in self._avgs])
        return f"MatchedSignal({self.devices}, {self.ts}, {self.frequency}, {self.duration}, {avgs_str})"

    def __str__(self):
        avgs_str = ", ".join([f"{avg:.2f}" if avg else f"{None}" for avg in self._avgs])
        return f"{self.__class__.__name__}<SDRs {self.devices}, {self.frequency/1000/1000:.3f} MHz, {self.duration.total_seconds()*1000:.2f} ms, dBWs: [{avgs_str}]>"


class MatchingSignal(MatchedSignal):
    """
    Class for matching signals detected on multiple devices.

    Parameters
    ----------
    devices: typing.List[str]
        The devices that are available to detect signals.
    """

    def __init__(self, devices: List[str]):
        self.devices = devices
        self._sigs: Dict[str, Signal] = {}

    @property
    def duration(self) -> datetime.timedelta:
        """
        Duration of the matching signal based on the detected maximum.

        Returns
        -------
        datetime.timedelta
        """
        return max([sig.duration for sig in self._sigs.values()])

    @property
    def ts(self) -> datetime.datetime:
        """
        Timestamp of the matching signal based on the earliest detection.

        Returns
        -------
        datetime.datetime
        """
        return min([sig.ts for sig in self._sigs.values()])

    @property
    def frequency(self) -> float:
        """
        Frequency of the matching signal based on median frequency.

        Returns
        -------
        float
        """
        return statistics.median([sig.frequency for sig in self._sigs.values()])

    @property
    def _avgs(self) -> List[float]:
        """
        Average powers of the matching signal.

        Returns
        -------
        typing.List[float]
        """
        return [self._sigs[d].avg if d in self._sigs else None for d in self.devices]

    def has_member(self,
                   sig: Signal,
                   time_diff: datetime.timedelta = datetime.timedelta(seconds=0),
                   bandwidth: float = 0,
                   duration_diff: Optional[datetime.timedelta] = None,
                   ) -> bool:
        """
        Checks if a Signal is part of this matching signal.

        Parameters
        ----------
        sig: radiotracking.Signal
            The signal to check.
        time_diff: datetime.timedelta
            Allowed difference of the timestamp.
        bandwidth: float
            Allowed difference of the frequency.
        duration_diff: datetime.timedelta
            Allowed difference of the duration.

        Returns
        -------
        bool
            True if the signal is part of this matching signal.
        """

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
        """
        Adds a signal to the matching signal.

        Parameters
        ----------
        sig: radiotracking.Signal
            The signal to add.
        """
        if sig.device in self._sigs:
            logger.warning(f"{sig} already contained in {self}")
            if self._sigs[sig.device].avg < sig.avg:
                logger.warning(f"Replacing initial {self._sigs[sig.device]}")
                self._sigs[sig.device] = sig
            else:
                logger.warning(f"Keeping initial {self._sigs[sig.device]}")
        else:
            self._sigs[sig.device] = sig
