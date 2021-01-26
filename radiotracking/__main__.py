#!/usr/bin/env python3

import argparse
import datetime
import logging
import multiprocessing
import os
import signal
import subprocess
from typing import List

from radiotracking.analyze import SignalAnalyzer
from radiotracking.consume import ProcessConsumerConnector
from radiotracking.match import CalibrationConsumer, SignalMatcher

logger = logging.getLogger(__name__)


class FileArgumentParser(argparse.ArgumentParser):
    def convert_arg_line_to_args(self, line):
        return line.split(";")[0].split()


parser = FileArgumentParser(
    prog="radiotracking",
    description="Detect signals of wildlife tracking systems with RTL SDR devices",
    fromfile_prefix_chars="@",
)

# generic options
parser.add_argument("-v", "--verbose", help="increase output verbosity", action="count", default=0)
parser.add_argument("--calibration-freq", help="frequency to use for calibration (Hz), default: None", default=None, type=float)


# sdr / sampling options
sdr_options = parser.add_argument_group("software-defined radio (SDR)")
sdr_options.add_argument("-d", "--device", help="device indexes or names, default: 0", default=[0], nargs="*")
sdr_options.add_argument("-c", "--calibration", help="device calibration gain (dB), default: 0", default=[], nargs="*", type=float)
sdr_options.add_argument("-f", "--center-freq", help="center frequency to tune to (Hz), default: 150100001", default=150100001, type=int)
sdr_options.add_argument("-s", "--sample-rate", help="sample rate (Hz), default: 300000", default=300000, type=int)
sdr_options.add_argument("-b", "--sdr-callback-length", help="number of samples to read per batch", default=None, type=int)
sdr_options.add_argument("-g", "--gain", help="gain, supported levels 0.0 - 49.6, default: 49.6", default="49.6")
sdr_options.add_argument("--sdr-max-restart", help="maximal restart count per SDR device, default: 3", default=3, type=int)
sdr_options.add_argument("--sdr-timeout-s", help="Time after which an SDR device is considered unrepsonsive (s), default: 2", default=2, type=int)

# analysis options
analysis_options = parser.add_argument_group("signal analysis")
analysis_options.add_argument("-n", "--fft-nperseg", help="fft number of samples, default: 256", default=256, type=int)
analysis_options.add_argument(
    "-w", "--fft-window", help="fft window function, default: 'hamming', see https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.spectrogram.html", type=eval, default="'hamming'"
)
analysis_options.add_argument("-t", "--signal-threshold-dbw", help="lower limit for signal intensity (dBW), default: -50.0", type=float, default=-50.0)
analysis_options.add_argument("-r", "--snr-threshold-db", help="lower limit for signal-to-noise ratio (dB), default: 10.0", type=float, default=10.0)
analysis_options.add_argument("-l", "--signal-min-duration-ms", help="lower limit for signal duration (ms), default: 8", type=float, default=8)
analysis_options.add_argument("-u", "--signal-max-duration-ms", help="upper limit for signal duration (ms), default: 40", type=float, default=40)

# analysis options
matching_options = parser.add_argument_group("signal matching")
matching_options.add_argument("--matching-timeout-s", help="timeout for adding signals to a match group, default: 2.0", type=float, default=2.0)
matching_options.add_argument("-mt", "--matching-time-diff-ms", help="error margin for timestamp matching (ms), default: 0", type=float, default=0)
matching_options.add_argument("-mb", "--matching-bandwidth-hz", help="error margin for frequency (Hz), default: 0", type=float, default=0)
matching_options.add_argument("-md", "--matching-duration-diff-ms", help="error margin for duration (ms), default: None (do not match)", type=float)

# data publishing options
publish_options = parser.add_argument_group("data publishing")
publish_options.add_argument("--sig-stdout", help="enable stdout signal publishing, default: False", action="store_true")
publish_options.add_argument("--match-stdout", help="enable stdout matched signals publishing, default: False", action="store_true")
publish_options.add_argument("--csv", help="enable csv data publishing, default: False", action="store_true")
publish_options.add_argument("--csv-path", help=f"csv folder path, default: ./data/{os.uname()[1]}/radiotracking", default=f"./data/{os.uname()[1]}/radiotracking")
publish_options.add_argument("--mqtt", help="enable mqtt data publishing, default: False", action="store_true")
publish_options.add_argument("--mqtt-host", help="hostname of mqtt broker, default: localthost", default="localhost")
publish_options.add_argument("--mqtt-port", help="port of mqtt broker, default: 1883", default=1883, type=int)


def create_and_start(dargs: argparse.Namespace, connector: ProcessConsumerConnector) -> SignalAnalyzer:
    analyzer = SignalAnalyzer(**vars(dargs))
    analyzer._callbacks.append(connector.add)
    analyzer.start()

    try:
        cpu_core = analyzer.device_index % multiprocessing.cpu_count()
        out = subprocess.check_output(["taskset", "-p", "-c", str(cpu_core), str(analyzer.pid)])
        for line in out.decode().splitlines():
            logger.info(f"SDR {analyzer.device} CPU affinity: {line}")
    except FileNotFoundError:
        logger.warning(f"SDR {analyzer.device} CPU affinity: failed to configure")

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

    # init task & process handling
    running = True

    def terminate(sig, frame):
        global running
        running = False
        logging.warning(f"Caught {signal.Signals(sig).name}, terminating {len(analyzers)} analyzers.")

        # Stop the analyzers, and wait for completion
        [a.kill() for a in analyzers]
        [a.join() for a in analyzers]
        logging.warning("Termination complete.")

    signal.signal(signal.SIGINT, terminate)
    signal.signal(signal.SIGTERM, terminate)

    if len(args.calibration) == 0:
        args.calibration = [0.0] * len(args.device)
        logger.info(f"No calibration values supplied, using {args.calibration}")
    elif len(args.calibration) != len(args.device):
        logger.critical(f"Calibration values {args.calibration} do not match devices {args.device}.")
        exit(1)

    matcher = SignalMatcher(**args.__dict__)
    connector = ProcessConsumerConnector()
    connector.add_consumer(matcher)
    if args.calibration_freq:
        calibrator = CalibrationConsumer(**args.__dict__)
        connector.add_consumer(calibrator)

    devices = dict(zip(args.device, args.calibration))
    dargs = argparse.Namespace(**vars(args))

    # create & start analyzers
    analyzers: List[SignalAnalyzer] = []
    for device, calibration_db in devices.items():
        dargs.device = device
        dargs.calibration_db = calibration_db
        analyzers.append(create_and_start(dargs, connector))

    logger.info("All analyzers started.")

    while running:
        # check if any of the analyzers have died
        dead_analyzers = [a for a in analyzers if not a.is_alive()]
        for dead in dead_analyzers:
            # terminate execution if the restarts are depleted
            if dead.sdr_max_restart <= 0:
                logger.critical(f"SDR {dead.device} is dead and beyond restart count, terminating.")
                terminate(signal.SIGTERM, None)

            # remove old & start new analyzer
            analyzers.remove(dead)
            dargs.device = dead.device
            dargs.calibration_db = dead.calibration_db
            dargs.sdr_max_restart = dead.sdr_max_restart - 1
            analyzers.append(create_and_start(dargs, connector))

        start = datetime.datetime.now()
        while datetime.datetime.now() < start + datetime.timedelta(seconds=1):
            connector.step(datetime.datetime.now() - start)

    if args.calibration_freq:
        logger.warning(f"Calibration results: {calibrator.calibration_string}")
