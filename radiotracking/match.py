import datetime
import logging
import math
import multiprocessing
from typing import List

from radiotracking import AbstractSignal, MatchedSignal, Signal
from radiotracking.consume import AbstractConsumer

logger = logging.getLogger(__name__)


class CalibrationConsumer(AbstractConsumer):
    def __init__(self,
                 device: List[str],
                 calibration: List[float],
                 calibration_freq: float,
                 matching_bandwidth_hz: float,
                 **kwargs):

        self.devices = device
        self.maxima: List[float] = [-math.inf for d in device]
        self.calibration_in = calibration
        self.calibration_freq = calibration_freq
        self.bandwidth_hz = matching_bandwidth_hz

    def add(self, sig: AbstractSignal):
        # discard if non-signal is added
        if not isinstance(sig, Signal):
            return

        if not self.calibration_freq:
            return

        # discard, if freq mismatch
        if sig.frequency - self.bandwidth_hz / 2 > self.calibration_freq:
            logger.debug(f"{sig.frequency - self.bandwidth_hz / 2} > {self.calibration_freq}")
            return
        if sig.frequency + self.bandwidth_hz / 2 < self.calibration_freq:
            logger.debug(f"{sig.frequency + self.bandwidth_hz / 2} < {self.calibration_freq}")
            return

        i = self.devices.index(sig.device)

        if self.maxima[i] > sig.avg:
            return

        # setting new maximum
        self.maxima[i] = sig.avg

        logger.info(f"Found new calibration values \"{self.calibration_string}\"")

    @property
    def calibration(self) -> List[float]:
        maxima_uncalibrated = [m + c for m, c in zip(self.maxima, self.calibration_in)]
        return [m - max(maxima_uncalibrated) for m in maxima_uncalibrated]

    @ property
    def calibration_string(self) -> str:
        return " ".join([f"{c: .2f}" for c in self.calibration])


class SignalMatcher(AbstractConsumer):
    def __init__(self,
                 device: List[str],
                 matching_timeout_s: float,
                 matching_time_diff_s: float,
                 matching_bandwidth_hz: float,
                 signal_queue: multiprocessing.Queue,
                 matching_duration_diff_ms: float = None,
                 **kwargs,
                 ):
        self.devices = device
        self.matching_timeout = datetime.timedelta(seconds=matching_timeout_s)
        self.matching_time_diff = datetime.timedelta(seconds=matching_time_diff_s)
        self.matching_bandwidth_hz = float(matching_bandwidth_hz)
        self.matching_duration_diff = datetime.timedelta(milliseconds=matching_duration_diff_ms) if matching_duration_diff_ms else None
        self.signal_queue = signal_queue

        self._matched: List[MatchedSignal] = []

    def consume(self, msig: MatchedSignal):
        self.signal_queue.put(msig)
        self._matched.remove(msig)

    def add(self, sig: AbstractSignal):
        if not isinstance(sig, Signal):
            return
        now = sig.ts

        for msig in self._matched:
            if msig.ts_mid < now - self.matching_timeout:
                logger.info(f"Timed out {msig}, consuming.")
                self.consume(msig)
                continue

            if msig.has_member(sig, bandwidth=self.matching_bandwidth_hz, time_diff=self.matching_time_diff, duration_diff=self.matching_duration_diff):
                msig.add_member(sig)
                logger.debug(f"Found member of {msig}")
                return

        msig = MatchedSignal(self.devices)
        msig.add_member(sig)
        logger.debug(f"Created new {msig}")
        self._matched.append(msig)
