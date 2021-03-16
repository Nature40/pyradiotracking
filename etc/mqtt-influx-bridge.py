#!/usr/bin/env python3

import argparse
import logging
import math
import platform
import ssl
from typing import SupportsRound

import cbor2 as cbor
import influxdb
import paho.mqtt.client as mqtt
from influxdb import InfluxDBClient
from radiotracking import MatchedSignal, Signal
from radiotracking.consume import uncborify

parser = argparse.ArgumentParser(
    prog="mqtt-influx-bridge",
    description="Relay radiotracking signals from mqtt to influx",
    formatter_class=argparse.ArgumentDefaultsHelpFormatter,
)
parser.add_argument("-v", "--verbose", help="increase verbosity", action="store_true")
parser.add_argument("--round-duration", help="value to round duration to (ms)", default=8, type=float)
parser.add_argument("--round-freq", help="value to round frequency to (MHz)", default=0.008, type=float)

parser.add_argument("--mqtt-host", help="hostname for MQTT broker connection", default="localhost")
parser.add_argument("--mqtt-port", help="port for MQTT connection", default=1883, type=int)
parser.add_argument("--mqtt-keepalive", help="MQTT keepalive duration", default=60, type=int)
parser.add_argument("--mqtt-tls", help="use tls for broker connection", default=False, action="store_true")
parser.add_argument("--mqtt-username", help="MQTT username", type=str)
parser.add_argument("--mqtt-password", help="MQTT password", type=str)

parser.add_argument("--influx-host", help="hostname for InfluxDB connection", default="localhost", type=str)
parser.add_argument("--influx-port", help="port for InfluxDB connection", default=8086, type=int)
parser.add_argument("--influx-tls", help="use tls for InfluxDB connection", default=False, action="store_true")
parser.add_argument("--influx-username", default="root", type=str)
parser.add_argument("--influx-password", default="root", type=str)


def prec_round(number: float, ndigits: int, base: float):
    return round(base * round(float(number) / base), ndigits)


def on_signal_cbor(client: mqtt.Client, inlfuxc: InfluxDBClient, message):
    # extract payload and meta data
    signal_list = cbor.loads(message.payload, tag_hook=uncborify)
    station, _, _, device, _ = message.topic.split('/')
    signal = dict(zip(Signal.header, signal_list))

    # round values according to config
    duration_rounded = prec_round(signal.pop("Duration").total_seconds() * 1000, 0, args.round_duration)
    frequency_rounded = prec_round(signal.pop("Frequency") / 1000 / 1000, 3, args.round_freq)

    # log info message
    logging.info(f"Received Signal from {station}, device {device}: {frequency_rounded} MHz, {duration_rounded} ms")

    # create influx body
    json_body = {
        "measurement": "signal",
        "tags": {
            "Station": station,
            "Device": signal.pop("Device"),
            "Frequency (MHz)": frequency_rounded,
            "Duration (ms)": duration_rounded,
        },
        "time": signal.pop("Time"),
        "fields": signal
    }

    # write influx message
    influxc.write_points([json_body])


def on_matched_cbor(client: mqtt.Client, inlfuxc: InfluxDBClient, message):
    # extract payload and meta data
    matched_list = cbor.loads(message.payload, tag_hook=uncborify)
    station, _, _, _ = message.topic.split('/')
    matched = dict(zip(MatchedSignal(["0", "1", "2", "3"]).header, matched_list))

    # round values according to config
    duration_rounded = prec_round(matched.pop("Duration").total_seconds() * 1000, 0, args.round_duration)
    frequency_rounded = prec_round(matched.pop("Frequency") / 1000 / 1000, 3, args.round_freq)

    # log info message
    logging.info(f"Received Matched Signal from {station}: {frequency_rounded} MHz, {duration_rounded} ms")

    # create influx body
    json_body = {
        "measurement": "matched",
        "tags": {
            "Station": station,
            "Frequency (MHz)": frequency_rounded,
            "Duration (ms)": duration_rounded,
        },
        "time": matched.pop("Time"),
        "fields": matched
    }

    # write influx message
    influxc.write_points([json_body])


def on_connect(mqttc: mqtt.Client, inlfuxc, flags, rc):
    logging.debug(f"MQTT connection established ({rc})")

    # subscribe to signal cbor messages
    topic_signal_cbor = "+/radiotracking/device/+/cbor"
    mqttc.subscribe(topic_signal_cbor)
    mqttc.message_callback_add(topic_signal_cbor, on_signal_cbor)

    # subscribe to match signal cbor messages
    topic_matched_cbor = "+/radiotracking/matched/cbor"
    mqttc.subscribe(topic_matched_cbor)
    mqttc.message_callback_add(topic_matched_cbor, on_matched_cbor)


if __name__ == "__main__":
    args = parser.parse_args()
    logging_level = max(0, logging.WARN - (args.verbose * 10))
    logging.basicConfig(level=logging_level)

    # create influx connection
    influxc = InfluxDBClient(host=args.influx_host,
                             port=args.influx_port,
                             username=args.influx_username,
                             password=args.influx_password,
                             ssl=args.influx_tls,
                             verify_ssl=args.influx_tls,
                             database="radiotracking"
                             )
    influxc.create_database("radiotracking")

    # create client object and set callback methods
    mqttc = mqtt.Client(client_id=f"{platform.node()}-mqtt-influx-bridge", clean_session=False, userdata=influxc)
    mqttc.on_connect = on_connect

    # configure tls connection (skip tls certificate validation for now)
    if args.mqtt_tls:
        mqttc.tls_set(cert_reqs=ssl.CERT_NONE)

    if args.mqtt_username:
        mqttc.username_pw_set(args.mqtt_username, args.mqtt_password)

    mqttc.connect(args.mqtt_host, args.mqtt_port, args.mqtt_keepalive)
    mqttc.loop_forever()
