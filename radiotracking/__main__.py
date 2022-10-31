#!/usr/bin/env python3

import argparse
import datetime
import logging
import multiprocessing
import os
import platform
import signal
import socket
import subprocess
import time
from ast import literal_eval
from typing import List

import schedule

from radiotracking import StateMessage
from radiotracking.analyze import SignalAnalyzer
from radiotracking.config import ArgConfParser
from radiotracking.consume import ProcessConnector
from radiotracking.match import SignalMatcher

logger = logging.getLogger("radiotracking")


class Runner:
    """
    A class to represent a running instance of pyradiotracking. 
    """
    parser = ArgConfParser(
        prog="radiotracking",
        description="Detect signals of wildlife tracking systems with RTL SDR devices",
        config_dest="config",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # generic options
    parser.add_argument("-v", "--verbose", help="increase output verbosity", action="count", default=0)
    parser.add_argument("--calibrate", help="enable calibration mode", action="store_true")
    parser.add_argument("--config", help="configuration file", default="etc/radiotracking.ini", type=str)
    parser.add_argument("--station", help="name of the station", default=platform.node(), type=str)
    parser.add_argument("--schedule", help="specify a schedule of operation, e.g. 18:00-18:59:59", type=str, default=[], nargs="*")

    # sdr / sampling options
    sdr_options = parser.add_argument_group("rtl-sdr")
    sdr_options.add_argument("-d", "--device", help="device indexes or names", default=["0"], nargs="*", type=str)
    sdr_options.add_argument("-c", "--calibration", help="device calibration gain (dB)", default=[], nargs="*", type=float)
    sdr_options.add_argument("-f", "--center-freq", help="center frequency to tune to (Hz)", default=150150000, type=int)
    sdr_options.add_argument("-s", "--sample-rate", help="sample rate (Hz)", default=300000, type=int)
    sdr_options.add_argument("-b", "--sdr-callback-length", help="number of samples to read per batch", default=None, type=int)
    sdr_options.add_argument("-g", "--gain", help="gain, supported levels 0.0 - 49.6", default="49.6", type=float)
    sdr_options.add_argument("--sdr-max-restart", help="maximal restart count per SDR device", default=3, type=int)
    sdr_options.add_argument("--sdr-timeout-s", help="Time after which an SDR device is considered unrepsonsive (s)", default=2, type=int)

    # analysis options
    analysis_options = parser.add_argument_group("analysis")
    analysis_options.add_argument("-n", "--fft-nperseg", help="fft number of samples", default=256, type=int)
    analysis_options.add_argument("-w", "--fft-window", help="fft window function", type=literal_eval, default="'hamming'")
    analysis_options.add_argument("-t", "--signal-threshold-dbw", help="lower limit for signal intensity (dBW)", type=float, default=-90.0)
    analysis_options.add_argument("-r", "--snr-threshold-db", help="lower limit for signal-to-noise ratio (dB)", type=float, default=5.0)
    analysis_options.add_argument("-l", "--signal-min-duration-ms", help="lower limit for signal duration (ms)", type=float, default=8)
    analysis_options.add_argument("-u", "--signal-max-duration-ms", help="upper limit for signal duration (ms)", type=float, default=40)

    # analysis options
    matching_options = parser.add_argument_group("matching")
    matching_options.add_argument("--matching-timeout-s", help="timeout for adding signals to a match group", type=float, default=2.0)
    matching_options.add_argument("-mt", "--matching-time-diff-s", help="error margin for timestamp matching (s)", type=float, default=0)
    matching_options.add_argument("-mb", "--matching-bandwidth-hz", help="error margin for frequency (Hz)", type=float, default=0)
    matching_options.add_argument("-md", "--matching-duration-diff-ms", help="error margin for duration (ms)", type=float)

    # data publishing options
    publish_options = parser.add_argument_group("publish")
    publish_options.add_argument("--sig-stdout", help="enable stdout signal publishing", action="store_true")
    publish_options.add_argument("--match-stdout", help="enable stdout matched signals publishing", action="store_true")
    publish_options.add_argument("--path", help="file output path", default="data", type=str)
    publish_options.add_argument("--csv", help="enable csv data publishing", action="store_true")
    publish_options.add_argument("--export-config", help="export configuration", action="store_true")
    publish_options.add_argument("--mqtt", help="enable mqtt data publishing", action="store_true")
    publish_options.add_argument("--mqtt-host", help="hostname of mqtt broker", default="localhost", type=str)
    publish_options.add_argument("--mqtt-port", help="port of mqtt broker", default=1883, type=int)
    publish_options.add_argument("--mqtt-qos", help="mqtt quality of service level (0, 1, 2)", default=1, type=int)
    publish_options.add_argument("--mqtt-keepalive", help="timeout for mqtt connection (s)", default=3600, type=int)
    publish_options.add_argument("-mv", "--mqtt-verbose", help="increase mqtt logging verbosity", action="count", default=0)

    # dashboard options
    dashboard_options = parser.add_argument_group("dashboard")
    dashboard_options.add_argument("--dashboard", help="enable web-dashboard", action="store_true")
    dashboard_options.add_argument("--dashboard-host", help="hostname to bind the dashboard to", default="localhost", type=str)
    dashboard_options.add_argument("--dashboard-port", help="port to bind the dashboard to", default=8050, type=int)
    dashboard_options.add_argument("--dashboard-signals", help="number of signals to present", default=100, type=int)

    def create_and_start(self, device: str, calibration_db: float, sdr_max_restart: int = None) -> SignalAnalyzer:
        """
        Creates, starts and returns a signal analyzer thread.

        Parameters
        ----------
        device: str
            device index or name
        calibration_db: float
            calibration gain (dB)
        sdr_max_restart: int
            max restart count per SDR device

        Returns
        -------
        SignalAnalyzer: radiotracking.SignalAnalyzer
            signal analyzer thread
        """
        dargs = argparse.Namespace(**vars(self.args))
        dargs.device = device
        dargs.calibration_db = calibration_db
        if sdr_max_restart is not None:
            dargs.sdr_max_restart = sdr_max_restart

        last_data_ts = multiprocessing.Value("d", 0.0)
        analyzer = SignalAnalyzer(signal_queue=self.connector.q, last_data_ts=last_data_ts, **vars(dargs))
        analyzer.start()

        try:
            cpu_core = analyzer.device_index % multiprocessing.cpu_count()
            out = subprocess.check_output(["taskset", "-p", "-c", str(cpu_core), str(analyzer.pid)])
            for line in out.decode().splitlines():
                logger.info(f"SDR {analyzer.device} CPU affinity: {line}")
        except FileNotFoundError:
            logger.warning(f"SDR {analyzer.device} CPU affinity: failed to configure")

        return analyzer

    def start_analyzers(self):
        """
        Start all requested analyzer threads.
        """
        if self.analyzers:
            logger.critical("")
        logger.info("Starting all analyzers")
        for device, calibration_db in zip(self.args.device, self.args.calibration):
            self.analyzers.append(self.create_and_start(device, calibration_db))

    def stop_analyzers(self):
        """
        Stop all analyzer threads.
        """
        logger.info("Stopping all analyzers")
        [a.kill() for a in self.analyzers]
        [a.join() for a in self.analyzers]
        self.analyzers = []

    def check_analyzers(self):
        """
        Check if all analyzer threads are still running.
        """
        now = datetime.datetime.now()

        # iterate the analyzer copy to allow for altering (restarting) analyzers
        for analyzer in self.analyzers.copy():
            # check if the process itself is running
            if analyzer.is_alive():
                # check if analyzer has started yet
                if analyzer.last_data_ts.value == 0.0:
                    continue

                # check if last data timestamp is within timeout
                if analyzer.last_data_ts.value > datetime.datetime.timestamp(now) - analyzer.sdr_timeout_s:
                    logger.info(f"SDR {analyzer.device} received last data {datetime.datetime.fromtimestamp(analyzer.last_data_ts.value)}")
                    continue

                # kill timed out analyzer
                logger.warning(f"SDR {analyzer.device} received last data {datetime.datetime.fromtimestamp(analyzer.last_data_ts.value)}; timed out.")
                analyzer.signal_queue.put(StateMessage(analyzer.device, datetime.datetime.fromtimestamp(analyzer.last_data_ts.value), StateMessage.State.STOPPED))
                analyzer.kill()
                analyzer.join()

            else:
                logger.info(f"SDR {analyzer.device} process is dead.")

            # check if SDR allows further restarts
            if analyzer.sdr_max_restart <= 0:
                logger.critical(f"SDR {analyzer.device} is dead and beyond restart count, terminating.")
                self.terminate(signal.SIGTERM)
                break

            # create new device
            logger.warning(f"Restarting SDR {analyzer.device}.")
            new_analyzer = self.create_and_start(analyzer.device, analyzer.calibration_db, analyzer.sdr_max_restart - 1)
            self.analyzers.remove(analyzer)
            self.analyzers.append(new_analyzer)

    def terminate(self, sig):
        """
        Terminate the application.
        """
        logger.warning(f"Caught {signal.Signals(sig).name}, terminating {len(self.analyzers)} analyzers.")
        self.running = False

        # Stop the analyzers, and wait for completion
        [a.kill() for a in self.analyzers]
        [a.join() for a in self.analyzers]
        self.analyzers = []

        if self.dashboard:
            self.dashboard.stop()

        logger.warning("Termination complete.")
        os.kill(os.getpid(), signal.SIGKILL)

    def __init__(self):
        self.running = True
        self.analyzers: List[SignalAnalyzer] = []
        self.args = Runner.parser.parse_args()

        # logging levels increase in steps of 10, start with warning
        logging_level = max(0, logging.WARN - (self.args.verbose * 10))
        logging_stderr = logging.StreamHandler()
        logging_stderr.setLevel(logging_level)
        logging.basicConfig(level=logging.DEBUG, handlers=[logging_stderr])

        signal.signal(signal.SIGINT, lambda sig, _: self.terminate(sig))
        signal.signal(signal.SIGTERM, lambda sig, _: self.terminate(sig))

        # initialize calibration parameter if unset
        if len(self.args.calibration) == 0:
            self.args.calibration = [0.0] * len(self.args.device)
            logger.info(f"No calibration values supplied, using {self.args.calibration}")
        elif len(self.args.calibration) != len(self.args.device):
            logger.critical(f"Calibration values {self.args.calibration} do not match devices {self.args.device}.")
            exit(1)

        # export configuration
        if self.args.export_config:
            path = f"{self.args.path}/{socket.gethostname()}/radiotracking"
            os.makedirs(path, exist_ok=True)

            ts = datetime.datetime.now()
            config_export_path = f"{path}/{self.args.station}_{ts:%Y-%m-%dT%H%M%S}.ini"
            with open(config_export_path, "w") as config_export_file:
                Runner.parser.write_config(self.args, config_export_file)

        # create process connector
        self.connector = ProcessConnector(**self.args.__dict__)

        # create signal matcher and add to connector queue
        self.matcher = SignalMatcher(signal_queue=self.connector.q, **self.args.__dict__)
        self.connector.consumers.append(self.matcher)

        # add vizualization consumer
        if self.args.dashboard:
            from radiotracking.present import Dashboard

            self.dashboard = Dashboard(**self.args.__dict__)
            self.connector.consumers.append(self.dashboard)
        else:
            self.dashboard = None

        self.schedule = []

        for entry in self.args.schedule:
            start, stop = entry.split("-")

            try:
                start_s = schedule.every().day.at(start)
                stop_s = schedule.every().day.at(stop)

                if start_s.at_time > stop_s.at_time:
                    raise schedule.ScheduleError("Schedule start is after stop")

                start_s.do(self.start_analyzers)
                stop_s.do(self.stop_analyzers)

                # check if there is an overlap with another schedule
                for other_start, other_stop in self.schedule:
                    # if they start before us and don't finish before us
                    if other_start < start_s.at_time and not other_stop < start_s.at_time:
                        raise schedule.ScheduleError(f"Scheduling overlaps with {other_start}-{other_stop}")

                    # if we start before them and do not finish before them
                    if start_s.at_time < other_start:
                        logger.debug("we start before them")
                        if not stop_s.at_time < other_start:
                            logger.debug("we don't finish before them")
                            raise schedule.ScheduleError(f"Scheduling overlaps with {other_start}-{other_stop}")

                self.schedule.append((start_s.at_time, stop_s.at_time))
                logger.debug(f"Added {start_s.at_time}-{stop_s.at_time} to schedule")

            except schedule.ScheduleError as error:
                logger.error(f"{error}, please check configuration '{entry}'.")
                exit(1)

    def main(self):
        """
        Run the main loop of the application.
        """
        logger.warning("Running radiotracking...")

        if self.dashboard:
            self.dashboard.start()

        # evaluate schedulings, and start if current time is in interval
        ts = datetime.datetime.now()
        for start_s, stop_s in self.schedule:
            if start_s < ts.time() and ts.time() < stop_s:
                logger.info(f"starting analyzers now (schedule {start_s}-{stop_s})")
                self.start_analyzers()

        # if no schedule is defined, run permanently
        if not self.schedule:
            self.start_analyzers()

        next_check = datetime.datetime.now()
        while self.running:
            # check if everything is working
            if next_check < datetime.datetime.now():
                self.check_analyzers()
                next_check += datetime.timedelta(seconds=1)

            # run scheduled functions (if present)
            schedule.run_pending()

            # do a connector step with remaining time (check for queued signals)
            self.connector.step(next_check - datetime.datetime.now())

        logger.info("Exit main loop")
        exit(0)


if __name__ == "__main__":
    runner = Runner()
    runner.main()
