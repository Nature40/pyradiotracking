import datetime
import logging
import multiprocessing
from typing import List

from radiotracking import AbstractMessage, MatchingSignal, Signal
from radiotracking.consume import AbstractConsumer

logger = logging.getLogger(__name__)


class SignalMatcher(AbstractConsumer):
    """
    Class to consume and match signals detected on multiple rtlsdr devices.

    Parameters
    ----------
    device : List[str]
        Name of the devices used by the station.
    matching_timeout_s : float
        Timeout for matching signals.
    matching_time_diff_s : float
        Time difference for matching signals.
    matching_bandwidth_hz : float
        Bandwidth for matching signals.
    matching_duration_diff_ms : float
        Duration difference for matching signals.
    signal_queue : multiprocessing.Queue
        Queue to publish matched signals to.
    """

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

    def add(self, signal: AbstractMessage):
        """
        Add a signal to the matcher.

        Parameters
        ----------
        signal : AbstractMessage
            Signal to add.
        """
        if not isinstance(signal, Signal):
            return
        now = signal.ts

        # iterate on a copy of the list to enable removing of elements
        for msig in list(self._matched):
            if msig.ts < now - self.matching_timeout:
                logger.info(f"Timed out {msig}, consuming.")
                self.consume(msig)
                continue

            if msig.has_member(signal, bandwidth=self.matching_bandwidth_hz, time_diff=self.matching_time_diff, duration_diff=self.matching_duration_diff):
                msig.add_member(signal)
                logger.debug(f"Found member of {msig}")
                return

        msig = MatchingSignal(self.devices)
        msig.add_member(signal)
        logger.debug(f"Created new {msig}")
        self._matched.append(msig)
