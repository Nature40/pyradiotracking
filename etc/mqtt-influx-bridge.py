#!/usr/bin/env python3

import argparse
import csv
import logging
import mariadb
import platform

import schedule
import signal
import ssl
import sys
import time

import cbor2 as cbor
import paho.mqtt.client as mqtt

from typing import List, Tuple, Dict
from io import StringIO
from influxdb import InfluxDBClient
from influxdb.exceptions import InfluxDBServerError, InfluxDBClientError

from radiotracking import MatchedSignal, Signal
from radiotracking.consume import uncborify

parser = argparse.ArgumentParser(
    prog="mqtt-influx-bridge",
    description="Relay radiotracking signals from mqtt to influx",
    formatter_class=argparse.ArgumentDefaultsHelpFormatter,
)
parser.add_argument("-v", "--verbose", help="increase output verbosity", action="count", default=0)
parser.add_argument("--duration-buckets", help="center of duration bucket (ms)", nargs="*", default=[10, 20, 40], type=float)
parser.add_argument("--duration-bucket-width", help="width of duration bucket (ms)", default=5, type=float)
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
parser.add_argument("--influx-log-db", default="logs", type=str)
parser.add_argument("--influx-telemetry-db", default="telemetry", type=str)
parser.add_argument("--influx-util-db", default="util", type=str)

parser.add_argument("--mariadb-host", help="hostname for mariadb connection", default="localhost", type=str)
parser.add_argument("--mariadb-port", help="port for mariadb connection", default=3306, type=int)
parser.add_argument("--mariadb-user", default="root", type=str)
parser.add_argument("--mariadb-password", default="root", type=str)

def signal_handler(signal, frame):
  sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)


def prec_round(number: float, ndigits: int, base: float):
    return round(base * round(float(number) / base), ndigits)


def get_bucket(val, buckets, width):
    for buck in buckets:
        if val > buck - width and val < buck + width:
            return buck
    return None


def get_all_project_planners():
    global planners
    planners = []
    try:
        with mariadb.connect(user=args.mariadb_user, password=args.mariadb_password, host=args.mariadb_host,
                             port=args.mariadb_port) as conn:
            cursor = conn.cursor()
            cursor.execute("Show databases")
            for result in cursor.fetchall():
                database_name = result[0]
                if database_name == "performance_schema" or database_name ==  "mysql" or database_name == "information_schema":
                    continue
                planners.append(database_name)
            logging.info(f"update the list of planner, now we have: {planners}")
    except mariadb.Error as e:
        logging.error(f"we had an error in connecting to the database: {e}")


def check_plausibility_of_planner(project_planner: str):
    if project_planner in planners:
        return True
    else:
        return False


def write_data_to_matching_influx_db(influx_json:  dict, database_name: str, project_planner: str):
    # write influx message
    database:str = f"{database_name}_{project_planner}"
    if not check_plausibility_of_planner(project_planner):
        logging.debug(f"mqtt packet for planner: {project_planner} and database: {database_name} was not stored in influxdb")
        return
    try:
        influxc.write_points([influx_json], database=database)
    except ValueError as e:
        logging.error(f"we have a ValueError message in writing points to influxdb: {e}")
    # if the response code of the http request is between 500 and 600
    except InfluxDBServerError as e:
        logging.error(f"we have a InfluxDBServerError message in writing points to influxdb: {e}")
    # all other response codes expect 200 and 204
    except InfluxDBClientError as e:
        # response code 404 in a write statement is thrown in case the database does not exists
        logging.info(f"we have a InfluxDBClientError message in writing points to influxdb: {e}")
        if str(e).split(":")[0] == "404":
            influxc.create_database(database)
            influxc.write_points([influx_json], database=database)
    # influxdb client raises an empty exception in some strange cases
    except Exception as e:
        logging.error(f"we have a Exception message in writing points to influxdb: {e}")


def get_ring_number(planner: str, sub_project_name: str, frequency_incoming_signal: int, duration_incoming_signal: int) -> str:
    if planner not in planners:
        return "unknown"
    try:
        individuals = active_individuals_per_project[planner][sub_project_name]
    except KeyError:
        individuals = []
    for ring_number, frequency, duration_min, duration_max in individuals:
        if frequency - args.duration_bucket_width < frequency_incoming_signal < frequency + args.duration_bucket_width \
                and duration_min < duration_incoming_signal < duration_max:
            return ring_number
    return "unknown"


def update_active_individuals():
    logging.debug(f"start updating active individuals")
    get_all_project_planners()
    for planner in planners:
        active_individuals_per_project[planner] = update_active_individuals_for_project(planner)
    logging.info(f"update the list of active individuals, now we have: {active_individuals_per_project}")


def update_active_individuals_for_project(planner: str):
    try:
        with mariadb.connect(user=args.mariadb_user, password=args.mariadb_password, host=args.mariadb_host,
                             port=args.mariadb_port, database=planner) as conn:
            cursor = conn.cursor()
            individuals_for_planner: Dict[str, List] = {}
            cursor.execute(f"SELECT project_id, name FROM project")
            for query_result in cursor.fetchall():
                project_id: int = query_result[0]
                subproject: str = query_result[1]
                list_of_active_individuals = []
                cursor.execute("SELECT ring_number, frequency, duration_min, duration_max FROM individual JOIN transmitter ON ( individual.id = transmitter.individual_id) WHERE individual.project_id=%s", (project_id, ))
                for ring_number, frequency, duration_min, duration_max in cursor.fetchall():
                    list_of_active_individuals.append((ring_number, frequency, duration_min, duration_max))
                individuals_for_planner[subproject] = list_of_active_individuals
                logging.debug(f"for planner: {planner} we have: {list_of_active_individuals}")
            return individuals_for_planner
    except mariadb.Error as e:
            logging.error(f"Can not query individuals for {planner} with error message: {e}")


def on_signal_cbor(client: mqtt.Client, influxc: InfluxDBClient, message):
    # extract payload and meta data
    signal_list = cbor.loads(message.payload, tag_hook=uncborify)
    station, _, _, device, _ = message.topic.split('/')
    if len(station.split("-")) < 3:
        return
    sig = Signal(*signal_list)
    signal = sig.as_dict

    # round values according to config
    duration_bucket = get_bucket(signal["Duration"].total_seconds() * 1000, args.duration_buckets, args.duration_bucket_width)
    frequency_bucket = prec_round(signal["Frequency"] / 1000 / 1000, 3, args.round_freq)

    # log info message
    logging.debug(f"{station}: {sig}")

    frequency = signal["Frequency"]
    duration = signal["Duration"]
    planner = station.split("-")[1]
    subproject = station.split("-")[1]

    ring_number = get_ring_number(planner, subproject, frequency, duration)

    # create influx body
    json_body = {
        "measurement": "signal",
        "tags": {
            "Station": station,
            "Subproject": subproject,
            "Device": signal.pop("Device"),
            "Frequency Bucket (MHz)": frequency_bucket,
            "Duration Bucket (ms)": duration_bucket,
            "Ring Number": ring_number,
        },
        "time": signal.pop("Time"),
        "fields": {
            "Duration (ms)": signal.pop("Duration").total_seconds() * 1000,
            "Frequency (MHz)": signal["Frequency"] / 1000 / 1000,
            **signal
        }
    }

    write_data_to_matching_influx_db(json_body, args.influx_telemetry_db, planner)


def on_matched_cbor(client: mqtt.Client, influxc: InfluxDBClient, message):
    # extract payload and meta data
    matched_list = cbor.loads(message.payload, tag_hook=uncborify)
    msig = MatchedSignal(["0", "1", "2", "3"], *matched_list)
    station, _, _, _ = message.topic.split('/')
    if len(station.split("-")) < 3:
        return
    matched = msig.as_dict

    # round values according to config
    duration_bucket = get_bucket(matched["Duration"].total_seconds() * 1000, args.duration_buckets, args.duration_bucket_width)
    frequency_bucket = prec_round(matched["Frequency"] / 1000 / 1000, 3, args.round_freq)

    # log info message
    logging.debug(f"{station}: {msig}")

    frequency = matched["Frequency"]
    duration = matched["Duration"]
    planner = station.split("-")[0]
    subproject = station.split("-")[1]

    ring_number = get_ring_number(planner, subproject, frequency, duration)

    # create influx body
    json_body = {
        "measurement": "matched",
        "tags": {
            "Station": station,
            "Subproject": subproject,
            "Frequency Bucket (MHz)": frequency_bucket,
            "Duration Bucket (ms)": duration_bucket,
            "Ring Number": ring_number,
        },
        "time": matched.pop("Time"),
        "fields": {
            "Duration (ms)": matched.pop("Duration").total_seconds() * 1000,
            "Frequency (MHz)": matched["Frequency"] / 1000 / 1000,
            **matched
        }
    }

    # write influx message
    write_data_to_matching_influx_db(json_body, args.influx_telemetry_db, planner)


def on_log_csv(client: mqtt.Client, influxc: InfluxDBClient, message):
    # extract payload and meta data
    csv_io = StringIO(message)
    csv_reader = csv.reader(csv_io, dialect="excel", delimiter=";")
    log_csv_header = ["Level", "Name", "Message"]
    log_arr = next(csv_reader)
    log_dict = dict(zip(log_csv_header, log_arr[:len(log_csv_header)]))
    station, _, _, _ = message.topic.split('/')
    if len(station.split("-")) < 3:
        return
    planner = station.split("-")[0]
    subproject = station.split("-")[1]

    # log info message
    logging.debug(f"{station}: {log_arr}")

    # create influx body
    json_body = {
        "measurement": "log",
        "tags": {
            "Station": station,
            "Subproject": subproject,
        },
        "fields": log_dict,
    }

    write_data_to_matching_influx_db(json_body, args.influx_log_db, planner)


def on_mqtt_util(client: mqtt.Client, influxc: InfluxDBClient, message):
    payload = message.payload
    # station is like xxx-yyy-12345
    # where xxx is project planer
    # and yyy is subproject
    station = message.topic.split('/')[1]
    if len(station.split("-")) < 3:
        return
    planner = station.split("-")[0]
    subproject = station.split("-")[1]
    sensor = message.topic.split('/')[3]

    # create influx body
    json_body = {
        "measurement": sensor,
        "tags": {
            "Station": station,
            "Subproject": subproject,
        },
        "time": time.time(),
        "fields": {
            payload
        }
    }

    write_data_to_matching_influx_db(json_body, args.influx_util_db, planner)


def on_connect(mqttc: mqtt.Client, inlfuxc, flags, rc):
    schedule.run_pending()
    logging.info(f"MQTT connection established ({rc})")

    # subscribe to signal cbor messages
    topic_signal_cbor = "+/radiotracking/device/+/cbor"
    mqttc.subscribe(topic_signal_cbor)
    mqttc.message_callback_add(topic_signal_cbor, on_signal_cbor)
    logging.info(f"Subscribed to {topic_signal_cbor}")

    # subscribe to match signal cbor messages
    topic_matched_cbor = "+/radiotracking/matched/cbor"
    mqttc.subscribe(topic_matched_cbor)
    mqttc.message_callback_add(topic_matched_cbor, on_matched_cbor)
    logging.info(f"Subscribed to {topic_matched_cbor}")

    # subscribe to log csv messages
    topic_log_csv = "+/radiotracking/log/csv"
    mqttc.subscribe(topic_log_csv)
    mqttc.message_callback_add(topic_log_csv, on_log_csv)
    logging.info(f"Subscribed to {topic_log_csv}")

    # subscribe to util messages
    topic_mqtt_util = "+/mqttutil/#"
    mqttc.subscribe(topic_mqtt_util)
    mqttc.message_callback_add(topic_mqtt_util, on_mqtt_util)
    logging.info(f"Subscribed to {topic_mqtt_util}")


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
                             verify_ssl=args.influx_tls
                             )
    logging.info(f"Connected to InfluxDB: {args.influx_host}:{args.influx_port}")

    planners = []
    active_individuals_per_project = {}

    update_active_individuals()
    schedule.every(10).minutes.do(update_active_individuals)


    # create client object and set callback methods
    mqttc = mqtt.Client(client_id=f"{platform.node()}-mqtt-influx-bridge", clean_session=False, userdata=influxc)
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
        logging.critical(f"MQTT connection failed: {ret}")
        exit(ret)

