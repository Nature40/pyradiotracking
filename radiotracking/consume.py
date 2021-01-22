import csv
import datetime
import json
import logging
import socket
from abc import ABC, abstractmethod

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


class AbstractConsumer(ABC):
    @ abstractmethod
    def add(self, signal: AbstractSignal, sdr_device: str):
        pass


class MQTTConsumer(AbstractConsumer):
    def __init__(
        self,
        mqtt_host: str,
        mqtt_port: int,
    ):
        self.prefix = f"{socket.gethostname()}/radiotracking"
        self.client = paho.mqtt.client.Client()
        self.client.connect(mqtt_host, mqtt_port)

    def add(self, signal: AbstractSignal, sdr_device: str):

        if isinstance(signal, Signal):
            # desired path: /nature40-sensorbox-01234567/radiotracking/signal/0/FMT
            path = f"{self.prefix}/signal/{sdr_device}"
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
        payload_csv = ",".join([str(val) for val in signal.as_list])
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

    def add(self, signal: AbstractSignal, sdr_device: str):
        self.writer.writerow(signal.as_list)
        self.out.flush()

        logger.debug(f"published {signal} via csv")
