#!/usr/bin/env python3

import argparse
import logging
import multiprocessing
import os
import signal
import subprocess
import time

from radiotracking.analyze import SignalAnalyzer

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
sdr_options.add_argument("--sdr-max-restart", help="maximal restart count per SDR device, default: 3", default=3, type=int)

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
publish_options.add_argument("--stdout", help="enable stdout data publishing, default: False", action="store_true")
publish_options.add_argument("--csv", help="enable csv data publishing, default: False", action="store_true")
publish_options.add_argument("--csv-path", help=f"csv folder path, default: ./data/{os.uname()[1]}/radiotracking", default=f"./data/{os.uname()[1]}/radiotracking")
publish_options.add_argument("--mqtt", help="enable mqtt data publishing, default: False", action="store_true")
publish_options.add_argument("--mqtt-host", help="hostname of mqtt broker, default: localthost", default="localhost")
publish_options.add_argument("--mqtt-port", help="port of mqtt broker, default: 1883", default=1883, type=int)


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

    analyzers = []
    device_args = args.__dict__.copy()
    device_args.pop("device")

    # create & start analyzers
    for device in args.device:
        analyzer = SignalAnalyzer(device, **device_args)
        analyzers.append(analyzer)
        analyzer.start()

        try:
            cpu_core = analyzer.device_index % multiprocessing.cpu_count()
            out = subprocess.check_output(["taskset", "-p", "-c", str(cpu_core), str(analyzer.pid)])
            for line in out.decode().splitlines():
                logger.info(f"SDR {analyzer.device} CPU affinity: {line}")
        except FileNotFoundError:
            logger.warning(f"SDR {analyzer.device} CPU affinity: failed to configure")

    logger.info("All analyzers started.")

    while running:
        dead_analyzers = [a for a in analyzers if not a.is_alive()]
        for a in dead_analyzers:
            if a.sdr_max_restart <= 0:
                logger.critical(f"SDR {a.device} is dead and beyond restart count, terminating.")
                terminate(signal.SIGTERM, None)
                break

            logger.critical(f"SDR {a.device} is dead, restarting.")

            # create & start new analyzer
            device_args["sdr_max_restart"] = a.sdr_max_restart - 1
            new = SignalAnalyzer(a.device, **device_args)
            analyzers.remove(a)
            analyzers.append(new)
            new.start()

        time.sleep(1)
