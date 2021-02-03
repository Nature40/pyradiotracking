import csv
import datetime
import json
import logging
import multiprocessing
import queue
import socket
from abc import ABC, abstractmethod
from io import StringIO
from typing import List, Tuple

import cbor2 as cbor
import paho.mqtt.client

from radiotracking import AbstractSignal, MatchedSignal, Signal

logger = logging.getLogger(__name__)


def jsonify(o):
    if isinstance(o, datetime.datetime):
        o: datetime.datetime
        return o.isoformat()
    if isinstance(o, datetime.timedelta):
        o: datetime.timedelta
        return o.total_seconds()

    raise TypeError(f"Object of type {type(o)} is not JSON serializable")


def cborify(encoder, o):
    if isinstance(o, datetime.timedelta):
        o: datetime.timedelta
        encoder.encode(cbor.CBORTag(1337, o.total_seconds()))


def csvify(o):
    if isinstance(o, datetime.timedelta):
        return o.total_seconds()

    return o


class AbstractConsumer(ABC):
    @ abstractmethod
    def add(self, signal: AbstractSignal, device: str):
        pass


class ProcessConsumerConnector(AbstractConsumer):
    def __init__(self):
        self._q: multiprocessing.Queue[Tuple[AbstractSignal, str]] = multiprocessing.Queue()
        self._consumers: List[AbstractConsumer] = []

    def add_consumer(self, consumer: AbstractConsumer):
        self._consumers.append(consumer)

    def add(self, sig: AbstractSignal, device: str) -> None:
        self._q.put((sig, device))

    def step(self, timeout: datetime.timedelta):
        try:
            sig, device = self._q.get(timeout=timeout.total_seconds())
        except queue.Empty:
            return

        [c.add(sig, device) for c in self._consumers]


class MQTTConsumer(AbstractConsumer):
    def __init__(
        self,
        mqtt_host: str,
        mqtt_port: int,
    ):
        self.prefix = f"{socket.gethostname()}/radiotracking"
        self.client = paho.mqtt.client.Client()
        self.client.connect(mqtt_host, mqtt_port)

    def add(self, signal: AbstractSignal, device: str):

        if isinstance(signal, Signal):
            # desired path: /nature40-sensorbox-01234567/radiotracking/signal/0/FMT
            path = f"{self.prefix}/signal/{device}"
        elif isinstance(signal, MatchedSignal):
            # desired path: /nature40-sensorbox-01234567/radiotracking/matched/FMT
            path = f"{self.prefix}/matched"
        else:
            logger.critical(f"Unknown data type {type(signal)}, skipping.")
            return

        payload_json = json.dumps(
            signal.as_dict,
            default=jsonify,
        )
        self.client.publish(path + "/json", payload_json)

        # publish csv
        csv_io = StringIO()
        csv.writer(csv_io, dialect="excel", delimiter=";").writerow([csvify(v) for v in signal.as_list])
        payload_csv = csv_io.getvalue().splitlines()[0]
        self.client.publish(path + "/csv", payload_csv)

        # publish cbor
        payload_cbor = cbor.dumps(
            signal.as_list,
            timezone=datetime.timezone.utc,
            datetime_as_timestamp=True,
            default=cborify,
        )
        self.client.publish(path + "/cbor", payload_cbor)

        logger.debug(f"published via mqtt, json: {len(payload_json)}, csv: {len(payload_csv)}, cbor: {len(payload_cbor)}")


class CSVConsumer(AbstractConsumer):
    def __init__(self, out, header: list = None):
        self.out = out
        self.writer = csv.writer(out, dialect="excel", delimiter=";")
        if header:
            self.writer.writerow(header)
        self.out.flush()

    def add(self, signal: AbstractSignal, device: str):
        self.writer.writerow([csvify(v) for v in signal.as_list])
        self.out.flush()

        logger.debug(f"published {signal} via csv")
