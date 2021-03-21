import datetime
import logging
import multiprocessing
from typing import List

from radiotracking import AbstractSignal, MatchingSignal, Signal
from radiotracking.consume import AbstractConsumer

logger = logging.getLogger(__name__)


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

        self._matched: List[MatchingSignal] = []

    def consume(self, msig: MatchingSignal):
        self.signal_queue.put(msig)
        self._matched.remove(msig)

    def add(self, sig: AbstractSignal):
        if not isinstance(sig, Signal):
            return
        now = sig.ts

        # iterate on a copy of the list to enable removing of elements
        for msig in list(self._matched):
            if msig.ts_mid < now - self.matching_timeout:
                logger.info(f"Timed out {msig}, consuming.")
                self.consume(msig)
                continue

            if msig.has_member(sig, bandwidth=self.matching_bandwidth_hz, time_diff=self.matching_time_diff, duration_diff=self.matching_duration_diff):
                msig.add_member(sig)
                logger.debug(f"Found member of {msig}")
                return

        msig = MatchingSignal(self.devices)
        msig.add_member(sig)
        logger.debug(f"Created new {msig}")
        self._matched.append(msig)
