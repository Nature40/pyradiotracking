import datetime
import logging
import multiprocessing
import os
import queue
import threading
from multiprocessing.queues import Queue
from typing import Callable, List, Tuple

from radiotracking import MatchedSignal, Signal
from radiotracking.consume import CSVConsumer, MQTTConsumer

logger = logging.getLogger(__name__)


class SignalMatcher(threading.Thread):
    def __init__(self,
                 csv: bool,
                 csv_path: str,
                 mqtt: bool,
                 mqtt_host: str,
                 mqtt_port: int,
                 matching_timeout_s: float = 2.0,
                 **kwargs,
                 ):
        self.matching_timeout = datetime.timedelta(seconds=matching_timeout_s)
        self._callbacks: List[Callable] = []

        ts = datetime.datetime.now()

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

        self._q: Queue[Tuple[Signal, str]] = multiprocessing.Queue()
        self._matched: List[MatchedSignal] = []
        self._running = False

    def add(self, sig: Signal, sdr_device: str) -> None:
        self._q.put((sig, sdr_device))

    def consume(self, msig: MatchedSignal):
        [callback(msig, "matched") for callback in self._callbacks]

    def step(self, timeout: datetime.timedelta):
        try:
            sig, device = self._q.get(timeout=timeout.total_seconds())
        except queue.Empty:
            return

        now = sig.ts

        for msig in self._matched:
            if msig.ts_mid < now - self.matching_timeout:
                logger.debug(f"Timed out {msig}, removing.")
                self._matched.remove(msig)
                continue

            if msig.has_member(sig):
                msig.add_member(device, sig)
                if len(msig._sigs) < 4:
                    logger.debug(f"Found member of {msig}")
                elif len(msig._sigs) == 4:
                    logger.info(f"Completed {msig}, consuming and removing.")
                    self.consume(msig)
                    self._matched.remove(msig)

                return

        msig = MatchedSignal(device, sig)
        logger.debug(f"Created new {msig}")
        self._matched.append(msig)

    def run(self):
        self._running = True

        while self._running:
            self.step()
