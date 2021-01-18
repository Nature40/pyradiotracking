import csv
import rtlsdr
import socket
import logging
import json
import cbor2 as cbor
from datetime import timezone
import paho.mqtt.client
from radiotracking import Signal

logger = logging.getLogger(__name__)


class SignalMatcher:
    def __init__(self):
        self.signals = []

    def add(self, sdr: rtlsdr.RtlSdr, signal: Signal):
        # signals arrive in arbitrary order and signals could

        pass


class MQTTConsumer:
    def __init__(
        self,
        mqtt_host: str,
        mqtt_port: int,
    ):
        self.prefix = f"{socket.gethostname()}/radiotracking"
        self.client = paho.mqtt.client.Client()
        self.client.connect(mqtt_host, mqtt_port)

    def add(self, sdr: rtlsdr.RtlSdr, signal: Signal):
        # publish json
        payload_json = json.dumps(signal.as_dict)
        self.client.publish(
            f"{self.prefix}/json/{sdr.device_index}",
            payload_json,
        )

        # publish csv
        payload_csv = ",".join([str(val) for val in signal.as_list])
        self.client.publish(
            f"{self.prefix}/csv/{sdr.device_index}",
            payload_csv,
        )

        # publish cbor
        payload_cbor = cbor.dumps(signal.raw_list, timezone=timezone.utc, datetime_as_timestamp=True)
        self.client.publish(
            f"{self.prefix}/cbor/{sdr.device_index}",
            payload_cbor,
        )

        logger.debug(f"published via mqtt, json: {len(payload_json)}, csv: {len(payload_csv)}, cbor: {len(payload_cbor)}")


class CsvConsumer:
    def __init__(self, csv_path):
        self.csv_path = csv_path
        self.out = open(csv_path, "w")
        self.writer = csv.writer(self.out, dialect="excel", delimiter=";")
        self.writer.writerow(Signal.header)
        self.out.flush()

    def add(self, sdr: rtlsdr.RtlSdr, signal: Signal, **kwargs):
        self.writer.writerow(signal.as_list)
        self.out.flush()

        logger.debug(f"published via csv: {self.csv_path}")
