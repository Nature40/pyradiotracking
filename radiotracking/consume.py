import csv
import datetime
import json
import logging
import multiprocessing
import os
import queue
import socket
import sys
from abc import ABC, abstractmethod
from io import StringIO
from typing import List, Type

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
    @abstractmethod
    def add(self, signal: AbstractSignal):
        pass


class MQTTConsumer(AbstractConsumer):
    def __init__(
        self,
        mqtt_host: str,
        mqtt_port: int,
        prefix: str = "/radiotracking",
    ):
        self.prefix = prefix
        self.client = paho.mqtt.client.Client()
        self.client.connect(mqtt_host, mqtt_port)

    def add(self, signal: AbstractSignal):

        if isinstance(signal, Signal):
            path = f"{self.prefix}/device/{signal.device}"
        elif isinstance(signal, MatchedSignal):
            path = f"{self.prefix}/matched"
        else:
            logger.critical(f"Unknown data type {type(signal)}, skipping.")
            return

        # publish json
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
    def __init__(self,
                 out,
                 cls: Type[AbstractSignal],
                 header: List[str] = None,
                 ):
        self.out = out
        self.cls = cls

        self.writer = csv.writer(out, dialect="excel", delimiter=";")
        if header:
            self.writer.writerow(header)
        self.out.flush()

    def add(self, signal: AbstractSignal):
        if isinstance(signal, self.cls):
            self.writer.writerow([csvify(v) for v in signal.as_list])
            self.out.flush()

            logger.debug(f"published {signal} via csv")
        else:
            pass


class ProcessConnector:
    def __init__(self,
                 station: str,
                 device: List[str],
                 sig_stdout: bool,
                 match_stdout: bool,
                 path: str,
                 csv: bool,
                 mqtt: bool,
                 mqtt_host: str,
                 mqtt_port: int,
                 **kwargs,
                 ):
        self.q: multiprocessing.Queue[AbstractSignal] = multiprocessing.Queue()
        self.consumers: List[AbstractConsumer] = []

        ts = datetime.datetime.now()

        # add stdout consumers
        if sig_stdout:
            sig_stdout_consumer = CSVConsumer(sys.stdout, Signal)
            self.consumers.append(sig_stdout_consumer)
        if match_stdout:
            match_stdout_consumer = CSVConsumer(sys.stdout, MatchedSignal)
            self.consumers.append(match_stdout_consumer)

        # add csv consumer
        if csv:
            path = f"{path}/{socket.gethostname()}/radiotracking"
            # create output directory
            os.makedirs(path, exist_ok=True)

            # create consumer for signals
            signal_csv_path = f"{path}/{station}_{ts:%Y-%m-%dT%H%M%S}.csv"
            signal_csv_consumer = CSVConsumer(open(signal_csv_path, "w"), cls=Signal, header=Signal.header)
            self.consumers.append(signal_csv_consumer)

            # create consumer for matched signals
            matched_csv_path = f"{path}/{station}_{ts:%Y-%m-%dT%H%M%S}-matched.csv"
            matched_csv_consumer = CSVConsumer(open(matched_csv_path, "w"), cls=MatchedSignal, header=MatchedSignal(device).header)
            self.consumers.append(matched_csv_consumer)

        # add mqtt consumer
        if mqtt:
            mqtt_consumer = MQTTConsumer(mqtt_host, mqtt_port, prefix=f"{station}/radiotracking")
            self.consumers.append(mqtt_consumer)

    def step(self, timeout: datetime.timedelta):
        try:
            sig = self.q.get(timeout=timeout.total_seconds())
        except queue.Empty:
            return

        [c.add(sig) for c in self.consumers]
