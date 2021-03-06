import csv
import datetime
import json
import logging
import multiprocessing
import os
import platform
import queue
import socket
import sys
from abc import ABC, abstractmethod
from io import StringIO
from typing import List, Type

import cbor2 as cbor
import paho.mqtt.client

from radiotracking import AbstractSignal, MatchingSignal, Signal

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


def uncborify(decoder, tag, shareable_index=None):
    if tag.tag == 1337:
        return datetime.timedelta(seconds=tag.value)

    return tag


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
        mqtt_qos: int,
        mqtt_keepalive: int,
        prefix: str = "/radiotracking",
        **kwargs,
    ):
        self.prefix = prefix
        self.mqtt_qos = mqtt_qos
        self.client = paho.mqtt.client.Client(f"{platform.node()}-radiotracking", clean_session=False)
        self.client.connect(mqtt_host, mqtt_port, keepalive=mqtt_keepalive)
        self.client.loop_start()

    def __del__(self):
        logger.info("Stopping MQTT thread")
        self.client.loop_stop()

    def add(self, signal: AbstractSignal):

        if isinstance(signal, Signal):
            path = f"{self.prefix}/device/{signal.device}"
        elif isinstance(signal, MatchingSignal):
            path = f"{self.prefix}/matched"
        else:
            logger.critical(f"Unknown data type {type(signal)}, skipping.")
            return

        # publish json
        payload_json = json.dumps(
            signal.as_dict,
            default=jsonify,
        )
        self.client.publish(path + "/json", payload_json, qos=self.mqtt_qos)

        # publish csv
        csv_io = StringIO()
        csv.writer(csv_io, dialect="excel", delimiter=";").writerow([csvify(v) for v in signal.as_list])
        payload_csv = csv_io.getvalue().splitlines()[0]
        self.client.publish(path + "/csv", payload_csv, qos=self.mqtt_qos)

        # publish cbor
        payload_cbor = cbor.dumps(
            signal.as_list,
            timezone=datetime.timezone.utc,
            datetime_as_timestamp=True,
            default=cborify,
        )
        self.client.publish(path + "/cbor", payload_cbor, qos=self.mqtt_qos)

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
                 calibrate: bool,
                 sig_stdout: bool,
                 match_stdout: bool,
                 path: str,
                 csv: bool,
                 mqtt: bool,
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
            match_stdout_consumer = CSVConsumer(sys.stdout, MatchingSignal)
            self.consumers.append(match_stdout_consumer)

        # add csv consumer
        if csv:
            path = f"{path}/{socket.gethostname()}/radiotracking"
            # create output directory
            os.makedirs(path, exist_ok=True)

            # create consumer for signals
            signal_csv_path = f"{path}/{station}_{ts:%Y-%m-%dT%H%M%S}"
            signal_csv_path += "_calibration" if calibrate else ""
            signal_csv_consumer = CSVConsumer(open(f"{signal_csv_path}.csv", "w"), cls=Signal, header=Signal.header)
            self.consumers.append(signal_csv_consumer)

            # create consumer for matched signals
            matched_csv_path = f"{path}/{station}_{ts:%Y-%m-%dT%H%M%S}-matched"
            matched_csv_path += "_calibration" if calibrate else ""
            matched_csv_consumer = CSVConsumer(open(f"{matched_csv_path}.csv", "w"), cls=MatchingSignal, header=MatchingSignal(device).header)
            self.consumers.append(matched_csv_consumer)

        # add mqtt consumer (only if not in calibration)
        if mqtt and not calibrate:
            mqtt_consumer = MQTTConsumer(prefix=f"{station}/radiotracking", **kwargs)
            self.consumers.append(mqtt_consumer)

    def step(self, timeout: datetime.timedelta):
        try:
            sig = self.q.get(timeout=timeout.total_seconds())
        except queue.Empty:
            return

        [c.add(sig) for c in self.consumers]
