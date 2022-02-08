import argparse
import codecs
import csv
import logging

from flask import Flask, jsonify, render_template, request
from flask_dropzone import Dropzone
from influxdb import InfluxDBClient
from influxdb.exceptions import InfluxDBServerError
from radiotracking import MatchedSignal, Signal
from werkzeug.datastructures import FileStorage

parser = argparse.ArgumentParser(
    prog="influx-dropzone",
    description="Upload radiotracking CSV files to InfluxDB",
    formatter_class=argparse.ArgumentDefaultsHelpFormatter,
)
parser.add_argument("-v", "--verbose", help="increase output verbosity", action="count", default=0)
parser.add_argument("--duration-buckets", help="center of duration bucket (ms)", nargs="*", default=[10, 20, 40], type=float)
parser.add_argument("--duration-bucket-width", help="width of duration bucket (ms)", default=5, type=float)
parser.add_argument("--round-freq", help="value to round frequency to (MHz)", default=0.008, type=float)

parser.add_argument("--influx-host", help="hostname for InfluxDB connection", default="localhost", type=str)
parser.add_argument("--influx-port", help="port for InfluxDB connection", default=8086, type=int)
parser.add_argument("--influx-tls", help="use tls for InfluxDB connection", default=False, action="store_true")
parser.add_argument("--influx-username", default="root", type=str)
parser.add_argument("--influx-password", default="root", type=str)

app = Flask(__name__)
app.config.update(
    DROPZONE_MAX_FILE_SIZE=512,        # unit: MB
    DROPZONE_ALLOWED_FILE_CUSTOM=True,
    DROPZONE_ALLOWED_FILE_TYPE=".csv",
)

dropzone = Dropzone(app)


@app.route("/", methods=["POST", "GET"])
def upload():
    if request.method == "POST":
        file = request.files.get("file")

        # csv_path = os.path.join(app.config["UPLOADED_PATH"], file.filename)
        # if os.path.exists(csv_path):
        #     return jsonify(error="Already uploaded."), 409

        if not file.filename.endswith(".csv"):
            logging.info(f"Upload from {request.remote_addr} ({file.filename}): Not a csv file.")
            return jsonify(error="Not a csv file."), 400

        filename = file.filename[:-4]

        try:
            station, file_ts, = filename.split("_")
            if filename.endswith("-matched"):
                file_ts = file_ts[:-8]

            logging.info(f"Receiving file form {station}, created at {file_ts}.")

            # org, area, station = hostname.split("-")
        except ValueError as e:
            logging.info(f"Upload from {request.remote_addr} ({file.filename}): Filename does not follow required scheme.")
            return jsonify(error="Filename does not follow required scheme."), 406

        # call the parsing method
        if filename.endswith("-matched"):
            logging.info(f"Upload from {request.remote_addr} ({file.filename}): Files of matched signals are currently not supported.")
            return jsonify(error="Files of matched signals are currently not supported."), 400
        else:
            logging.info(f"Upload from {request.remote_addr} ({file.filename}): Parsing signal csv file...")
            try:
                parse_signals(file, station)
                logging.info(f"Upload from {request.remote_addr} ({file.filename}): Signals processed successfully!")
            except InfluxDBServerError as e:
                return jsonify(error=str(e)), 406

        return jsonify(success="Processed successfully!"), 200

    return render_template("index.html")


def prec_round(number: float, ndigits: int, base: float):
    return round(base * round(float(number) / base), ndigits)


def get_bucket(val, buckets, width):
    for buck in buckets:
        if val > buck - width and val < buck + width:
            return buck

    return None


def parse_signals(file: FileStorage, station: str):
    csvreader = csv.reader(codecs.iterdecode(
        file.stream, 'utf-8'), dialect="excel", delimiter=";")

    header = next(csvreader)

    points = []

    for row in csvreader:
        sig = Signal(*row)
        signal = sig.as_dict

        # round values according to config
        duration_bucket = get_bucket(signal["Duration"].total_seconds(
        ) * 1000, args.duration_buckets, args.duration_bucket_width)
        frequency_bucket = prec_round(
            signal["Frequency"] / 1000 / 1000, 3, args.round_freq)

        # log info message
        logging.debug(f"{station}: {sig}")

        # create influx body
        json_body = {
            "measurement": "signal",
            "tags": {
                "Station": station,
                "Device": signal.pop("Device"),
                "Frequency Bucket (MHz)": frequency_bucket,
                "Duration Bucket (ms)": duration_bucket,
            },
            "time": signal.pop("Time"),
            "fields": {
                "Duration (ms)": signal.pop("Duration").total_seconds() * 1000,
                "Frequency (MHz)": signal["Frequency"] / 1000 / 1000,
                **signal
            }
        }

        points.append(json_body)

    logging.info(f"Writing {len(points)} parsed signals to influx.")

    # write influx message
    if not influxc.write_points(points):
        raise InfluxDBServerError(f"Error writing signals of {station}.")


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
    logging.info(f"Connected to InfluxDB {args.influx_host}:{args.influx_port}")

    app.run(debug=True, host="0.0.0.0")
