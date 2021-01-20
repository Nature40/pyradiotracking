import csv
import datetime
import json
import logging
import socket

import cbor2 as cbor
import paho.mqtt.client
import rtlsdr

from radiotracking import Signal

logger = logging.getLogger(__name__)


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
        payload_cbor = cbor.dumps(signal.raw_list, timezone=datetime.timezone.utc, datetime_as_timestamp=True)
        self.client.publish(
            f"{self.prefix}/cbor/{sdr.device_index}",
            payload_cbor,
        )

        logger.debug(f"published via mqtt, json: {len(payload_json)}, csv: {len(payload_csv)}, cbor: {len(payload_cbor)}")


class CSVConsumer:
    def __init__(self, out, write_header=True):
        self.out = out
        self.writer = csv.writer(out, dialect="excel", delimiter=";")
        if write_header:
            self.writer.writerow(Signal.header)
        self.out.flush()

    def add(self, sdr: rtlsdr.RtlSdr, signal: Signal, **kwargs):
        self.writer.writerow(signal.as_list)
        self.out.flush()

        logger.debug("published via csv")
