#!/usr/bin/env python3

import argparse
import logging
import platform
import ssl

import cbor2 as cbor
import paho.mqtt.client as mqtt
from radiotracking import MatchedSignal
from radiotracking.bearing import Gottwald2019Bearings
from radiotracking.consume import uncborify

parser = argparse.ArgumentParser(
    prog="radiotracking.amalgate",
    description="Bring together matched signals from multiple radio tracking stations",
    formatter_class=argparse.ArgumentDefaultsHelpFormatter,
)
parser.add_argument("-v", "--verbose", help="increase output verbosity", action="count", default=0)

parser.add_argument("--mqtt-host", help="hostname for MQTT broker connection", default="localhost")
parser.add_argument("--mqtt-port", help="port for MQTT connection", default=1883, type=int)
parser.add_argument("--mqtt-keepalive", help="MQTT keepalive duration", default=60, type=int)
parser.add_argument("--mqtt-tls", help="use tls for broker connection", default=False, action="store_true")
parser.add_argument("--mqtt-username", help="MQTT username", type=str)
parser.add_argument("--mqtt-password", help="MQTT password", type=str)


def on_connect(mqttc: mqtt.Client, userdata, flags, rc):
    logging.info(f"MQTT connection established ({rc})")

    # subscribe to match signal cbor messages
    topic_matched_cbor = "+/radiotracking/matched/cbor"
    mqttc.subscribe(topic_matched_cbor)
    mqttc.message_callback_add(topic_matched_cbor, on_matched_cbor)
    logging.info(f"Subscribed to {topic_matched_cbor}")


def on_matched_cbor(client: mqtt.Client, userdata, message):
    # extract payload and meta data
    matched_list = cbor.loads(message.payload, tag_hook=uncborify)
    station, _, _, _ = message.topic.split('/')

    msig = MatchedSignal(["0", "1", "2", "3"], *matched_list)
    a = Gottwald2019Bearings().get_angle(msig)
    logging.info(f"Caclultated {a}")


def main():
    args = parser.parse_args()
    logging_level = max(0, logging.WARN - (args.verbose * 10))
    logging.basicConfig(level=logging_level)

    # create client object and set callback methods
    mqttc = mqtt.Client(client_id=f"{platform.node()}-{parser.prog}", clean_session=False)
    mqttc.on_connect = on_connect

    # configure tls connection (skip tls certificate validation for now)
    if args.mqtt_tls:
        mqttc.tls_set(cert_reqs=ssl.CERT_NONE)

    if args.mqtt_username:
        mqttc.username_pw_set(args.mqtt_username, args.mqtt_password)

    ret = mqttc.connect(args.mqtt_host, args.mqtt_port, args.mqtt_keepalive)
    if ret == mqtt.MQTT_ERR_SUCCESS:
        mqttc.loop_forever()
    else:
        logging.critical(f"MQTT connetion failed: {ret}")
        exit(ret)


if __name__ == "__main__":
    main()
