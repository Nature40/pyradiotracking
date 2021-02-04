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
from radiotracking.consume import ProcessConnector
from radiotracking.match import CalibrationConsumer, SignalMatcher

logger = logging.getLogger(__name__)


class Runner:
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
    matching_options.add_argument("-mt", "--matching-time-diff-s", help="error margin for timestamp matching (s), default: 0", type=float, default=0)
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

    # dashboard options
    dashboard_options = parser.add_argument_group("dashboard")
    dashboard_options.add_argument("--dashboard", help="enable web-dashboard", action="store_true")
    dashboard_options.add_argument("--dashboard-host", help="hostname to bind the dashboard to, default: localhost", default="localhost")
    dashboard_options.add_argument("--dashboard-port", help="port to bind the dashboard to, default: 8050", default=8050, type=int)
    dashboard_options.add_argument("--dashboard-signals", help="number of signals to present, default: 100", default=100, type=int)

    @staticmethod
    def create_and_start(dargs: argparse.Namespace, queue: multiprocessing.Queue) -> SignalAnalyzer:
        analyzer = SignalAnalyzer(signal_queue=queue, **vars(dargs))
        analyzer.start()

        try:
            cpu_core = analyzer.device_index % multiprocessing.cpu_count()
            out = subprocess.check_output(["taskset", "-p", "-c", str(cpu_core), str(analyzer.pid)])
            for line in out.decode().splitlines():
                logger.info(f"SDR {analyzer.device} CPU affinity: {line}")
        except FileNotFoundError:
            logger.warning(f"SDR {analyzer.device} CPU affinity: failed to configure")

        return analyzer

    def terminate(self, sig):
        logging.warning(f"Caught {signal.Signals(sig).name}, terminating {len(self.analyzers)} analyzers.")
        self.running = False

        # Stop the analyzers, and wait for completion
        [a.kill() for a in self.analyzers]
        [a.join() for a in self.analyzers]
        self.analyzers = []

        if self.dashboard:
            self.dashboard.stop()

        logging.warning("Termination complete.")

    def __init__(self):
        self.running = True
        self.analyzers: List[SignalAnalyzer] = []
        self.args = Runner.parser.parse_args()

        # logging levels increase in steps of 10, start with warning
        logging_level = max(0, logging.WARN - (self.args.verbose * 10))
        logging.basicConfig(level=logging_level)

        signal.signal(signal.SIGINT, lambda sig, _: self.terminate(sig))
        signal.signal(signal.SIGTERM, lambda sig, _: self.terminate(sig))

        # initialize calibration parameter if unset
        if len(self.args.calibration) == 0:
            self.args.calibration = [0.0] * len(self.args.device)
            logger.info(f"No calibration values supplied, using {self.args.calibration}")
        elif len(self.args.calibration) != len(self.args.device):
            logger.critical(f"Calibration values {self.args.calibration} do not match devices {self.args.device}.")
            exit(1)

        # create process connector
        self.connector = ProcessConnector(**self.args.__dict__)

        # create signal matcher and add to connector queue
        self.matcher = SignalMatcher(signal_queue=self.connector.q, **self.args.__dict__)
        self.connector.consumers.append(self.matcher)

        # add calibration consumer
        if self.args.calibration_freq:
            self.calibrator = CalibrationConsumer(**self.args.__dict__)
            self.connector.consumers.append(self.calibrator)
        else:
            self.calibrator = None

        # add vizualization consumer
        if self.args.dashboard:
            from radiotracking.present import Dashboard
            self.dashboard = Dashboard(**self.args.__dict__)
            self.connector.consumers.append(self.dashboard)
        else:
            self.dashboard = None

    def main(self):
        # prepare device / calibration list
        devices = dict(zip(self.args.device, self.args.calibration))
        dargs = argparse.Namespace(**vars(self.args))

        # create & start analyzers
        for device, calibration_db in devices.items():
            dargs.device = device
            dargs.calibration_db = calibration_db
            self.analyzers.append(Runner.create_and_start(dargs, self.connector.q))

        logger.info("All analyzers started.")

        if self.dashboard:
            self.dashboard.start()

        while self.running:
            # check if any of the analyzers have died
            dead_analyzers = [a for a in self.analyzers if not a.is_alive()]
            for dead in dead_analyzers:
                # terminate execution if the restarts are depleted
                if dead.sdr_max_restart <= 0:
                    logger.critical(f"SDR {dead.device} is dead and beyond restart count, terminating.")
                    self.terminate(signal.SIGTERM)

                # remove old & start new analyzer
                self.analyzers.remove(dead)
                dargs.device = dead.device
                dargs.calibration_db = dead.calibration_db
                dargs.sdr_max_restart = dead.sdr_max_restart - 1
                self.analyzers.append(Runner.create_and_start(dargs, self.connector.q))

            start = datetime.datetime.now()
            while datetime.datetime.now() < start + datetime.timedelta(seconds=1):
                self.connector.step(datetime.datetime.now() - start)

        if self.calibrator:
            logger.warning(f"Calibration results: {self.calibrator.calibration_string}")

        exit(0)


if __name__ == "__main__":
    runner = Runner()
    runner.main()
