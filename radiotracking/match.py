import datetime
import logging
import math
import os
import sys
from typing import Callable, List

from radiotracking import AbstractSignal, MatchedSignal, Signal
from radiotracking.consume import AbstractConsumer, CSVConsumer, MQTTConsumer

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

    def add(self, sig: AbstractSignal, device: str):
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

        i = self.devices.index(device)

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
                 csv: bool,
                 csv_path: str,
                 match_stdout: bool,
                 mqtt: bool,
                 mqtt_host: str,
                 mqtt_port: int,
                 matching_timeout_s: float,
                 matching_time_diff_ms: float,
                 matching_bandwidth_hz: float,
                 matching_duration_diff_ms: float = None,
                 **kwargs,
                 ):
        self.matching_timeout = datetime.timedelta(seconds=matching_timeout_s)
        self.matching_time_diff = datetime.timedelta(milliseconds=matching_time_diff_ms)
        self.matching_bandwidth_hz = float(matching_bandwidth_hz)
        self.matching_duration_diff = datetime.timedelta(milliseconds=matching_duration_diff_ms) if matching_duration_diff_ms else None
        self._callbacks: List[Callable] = []

        ts = datetime.datetime.now()

        # add stdout consumer
        if match_stdout:
            stdout_consumer = CSVConsumer(sys.stdout)
            self._callbacks.append(stdout_consumer.add)

        # add csv consumer
        if csv:
            os.makedirs(csv_path, exist_ok=True)
            csv_path = f"{csv_path}/{ts:%Y-%m-%dT%H%M%S}-matched.csv"
            out = open(csv_path, "w")
            csv_consumer = CSVConsumer(out, header=MatchedSignal.header)
            self._callbacks.append(csv_consumer.add)

        # add mqtt consumer
        if mqtt:
            mqtt_consumer = MQTTConsumer(mqtt_host, mqtt_port)
            self._callbacks.append(mqtt_consumer.add)

        self._matched: List[MatchedSignal] = []

    def consume(self, msig: MatchedSignal):
        [callback(msig, "matched") for callback in self._callbacks]
        self._matched.remove(msig)

    def add(self, sig: AbstractSignal, device: str):
        if not isinstance(sig, Signal):
            return
        now = sig.ts

        for msig in self._matched:
            if msig.ts_mid < now - self.matching_timeout:
                logger.debug(f"Timed out {msig}, removing.")
                self.consume(msig)
                continue

            if msig.has_member(sig, bandwidth=self.matching_bandwidth_hz, time_diff=self.matching_time_diff, duration_diff=self.matching_duration_diff):
                msig.add_member(device, sig)
                if len(msig._sigs) < 4:
                    logger.debug(f"Found member of {msig}")
                elif len(msig._sigs) == 4:
                    logger.info(f"Completed {msig}, consuming and removing.")
                    self.consume(msig)
                return

        msig = MatchedSignal(device, sig)
        logger.debug(f"Created new {msig}")
        self._matched.append(msig)
