#!/usr/bin/env python3

from rtlsdr.rtlsdr import RtlSdr
from radiotracking.analyze import SignalAnalyzer
from radiotracking.consume import CsvConsumer, MQTTConsumer
import signal
import datetime
import sys
import os
import logging
import argparse
import rtlsdr

logger = logging.getLogger(__name__)

parser = argparse.ArgumentParser(
    prog="radiotracking",
    description="Detect signals of wildlife tracking systems with RTL SDR devices",
    fromfile_prefix_chars="@",
)
# allow for better config file formatting; ignore everything after ';', split blanks
parser.convert_arg_line_to_args = lambda line: line.split(";")[0].split()

# generic options
parser.add_argument("-v", "--verbose", help="increase output verbosity", action="count", default=0)

# sdr / sampling options
sdr_options = parser.add_argument_group("software-defined radio (SDR)")
sdr_options.add_argument("-d", "--device", help="device indexes or names, default: 0", default=[0], nargs="*")
sdr_options.add_argument("-f", "--center-freq", help="center frequency to tune to (Hz), default: 150100001", default=150100001, type=int)
sdr_options.add_argument("-s", "--sample-rate", help="sample rate (Hz), default: 300000", default=300000, type=int)
sdr_options.add_argument("-b", "--sdr-callback-length", help="number of samples to read per batch", default=None, type=int)
sdr_options.add_argument("-g", "--gain", help="gain, supported levels 0.0 - 49.6, default: 49.6", default="49.6")

# analysis options
analysis_options = parser.add_argument_group("signal analysis")
analysis_options.add_argument("-n", "--fft-nperseg", help="fft number of samples, default: 256", default=256, type=int)
analysis_options.add_argument(
    "-w", "--fft-window", help="fft window function, default: 'hamming', see https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.spectrogram.html", type=eval, default="'hamming'"
)
analysis_options.add_argument("-t", "--signal-threshold-db", help="lower limit for signal intensity (dBW), default: -50.0", type=float, default=-50.0)
analysis_options.add_argument("-r", "--snr-threshold-db", help="lower limit for signal-to-noise ratio (dB), default: 10.0", type=float, default=10.0)
analysis_options.add_argument("-l", "--signal-min-duration-ms", help="lower limit for signal duration (ms), default: 8", type=float, default=8)
analysis_options.add_argument("-u", "--signal-max-duration-ms", help="upper limit for signal duration (ms), default: 40", type=float, default=40)

# data publishing options
publish_options = parser.add_argument_group("data publishing")
publish_options.add_argument("--csv-path", help=f"csv folder path, default: ./data/{os.uname()[1]}/radiotracking", default=f"./data/{os.uname()[1]}/radiotracking")
publish_options.add_argument("--mqtt", help="enable mqtt data publishing, default: False", action="store_true")
publish_options.add_argument("--mqtt-host", help="hostname of mqtt broker, default: localthost", default="localhost")
publish_options.add_argument("--mqtt-port", help="port of mqtt broker, default: 1883", default=1883, type=int)


def create_analyzer(device, device_index, arg_dict, ts):
    sdr = RtlSdr(device_index)
    sdr.device = device
    sdr.device_index = device_index
    sdr.sample_rate = args.sample_rate
    sdr.center_freq = args.center_freq
    try:
        sdr.gain = float(args.gain)
    except ValueError:
        sdr.gain = args.gain

    # create analyzers
    analyzer = SignalAnalyzer(sdr, **arg_dict)

    csv_consumer = CsvConsumer(f"{args.csv_path}/{ts:%Y-%m-%dT%H%M%S}-{device}.csv")
    analyzer.callbacks.append(csv_consumer.add)

    if args.mqtt:
        mqtt_consumer = MQTTConsumer(args.mqtt_host, args.mqtt_port)
        analyzer.callbacks.append(mqtt_consumer.add)

    analyzer.callbacks.append(lambda sdr, signal: logger.debug(f"SDR '{sdr.device}' received {signal}"))

    return analyzer


if __name__ == "__main__":
    args = parser.parse_args()

    # logging levels increase in steps of 10, start with warning
    logging_level = max(0, logging.WARN - (args.verbose * 10))
    logging.basicConfig(level=logging_level)

    # describe configuration
    logger.info(f"center frequency {args.center_freq/1000/1000:.2f}MHz")
    logger.info(f"sampling rate {args.sample_rate/1000:.0f}kHz")

    frequency_min = args.center_freq - args.sample_rate / 2
    frequency_max = args.center_freq + args.sample_rate / 2
    logger.info(f"band {frequency_min/1000/1000:.2f}MHz - {frequency_max/1000/1000:.2f}MHz")

    try:
        # try to use --device as index
        device_indexes = [int(d) for d in args.device]
        logger.info(f"Using devices {device_indexes} by indexes")
    except ValueError:
        # try to use --device as serial numbers
        device_indexes = []
        for d in args.device:
            try:
                i = rtlsdr.RtlSdr.get_device_index_by_serial(d)
                device_indexes.append(i)
            except rtlsdr.rtlsdr.LibUSBError:
                logger.warning(f"Device '{d}' could was not found, aborting.")
                sys.exit(1)

        logger.info(f"Using devices {device_indexes} by serials {args.device}")

    ts = datetime.datetime.now()
    os.makedirs(args.csv_path, exist_ok=True)
    analyzers = []

    for device, device_index in zip(args.device, device_indexes):
        analyzer = create_analyzer(device, device_index, args.__dict__, ts)
        analyzers.append(analyzer)

    # Start all analyzers
    [a.start() for a in analyzers]
    logger.info("All analyzers started.")

    def handle(sig, frame):
        logging.warning(f"Caught {signal.Signals(sig).name}, terminating {len(analyzers)} analyzers.")

        # Stop the analyzers, and wait for completion
        [a.terminate() for a in analyzers]
        [a.join() for a in analyzers]
        logging.warning("Termination complete.")

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)

    signal.pause()
