#!/usr/bin/env python3

from rtlsdr.rtlsdr import RtlSdr
from si_prefix import si_format
from radiotracking.analyze import SignalAnalyzer
from radiotracking.consume import CsvConsumer, SignalMatcher
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
# generic options
parser.add_argument("-v", "--verbose", help="increase output verbosity", action="count", default=0)

# sdr / sampling options
sdr_options = parser.add_argument_group("software-defined radio (SDR)")
sdr_options.add_argument("-d", "--device", help="device indexes or names, default: 0", default=[0], nargs="*")
sdr_options.add_argument("-f", "--center_freq", help="center frequency to tune to (Hz), default: 150100001", default=150100001, type=int)
sdr_options.add_argument("-s", "--sample_rate", help="sample rate (Hz), default: 2048000", default=2048000, type=int)
sdr_options.add_argument("-b", "--sdr_callback_length", help="number of samples to read per batch", default=None, type=int)
sdr_options.add_argument("-g", "--gain", help="gain, supported levels 0.0 - 49.6, default: 49.6", default="49.6")

# analysis options
analysis_options = parser.add_argument_group("signal analysis")
analysis_options.add_argument("-n", "--fft_nperseg", help="fft number of samples", default=None, type=int)
analysis_options.add_argument("-w", "--fft_window", help="fft window function, see https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.spectrogram.html", type=eval, default="'hamming'")
analysis_options.add_argument("-t", "--signal_threshold_db", help="lower limit for signal intensity (dBW), default: -50.0", type=float, default=-50.0)
analysis_options.add_argument("-r", "--snr_threshold_db", help="lower limit for signal-to-noise ratio (dB), default: 10.0", type=float, default=10.0)
analysis_options.add_argument("-l", "--signal_min_duration_ms", help="lower limit for signal duration (ms), default: 8", type=float, default=8)
analysis_options.add_argument("-u", "--signal_max_duration_ms", help="upper limit for signal duration (ms), default: 40", type=float, default=40)


if __name__ == "__main__":
    args = parser.parse_args()

    # logging levels increase in steps of 10, start with warning
    logging_level = max(0, logging.WARN - (args.verbose * 10))
    logging.basicConfig(level=logging_level)

    # describe configuration
    logger.info(f"center frequency {si_format(args.center_freq, precision=3)}Hz")
    logger.info(f"sampling rate {si_format(args.sample_rate, precision=3)}Hz")

    frequency_min = args.center_freq - args.sample_rate / 2
    frequency_max = args.center_freq + args.sample_rate / 2
    logger.info(f"band {si_format(frequency_min, precision=3)}Hz - {si_format(frequency_max, precision=3)}Hz")

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

    analyze_start = datetime.datetime.now()
    analyzers = []

    signal_matcher = SignalMatcher()

    for device, device_index in zip(args.device, device_indexes):
        sdr = RtlSdr(device_index)
        sdr.device = device
        sdr.device_index = device_index
        sdr.sample_rate = args.sample_rate
        sdr.center_freq = args.center_freq
        try:
            sdr.gain = float(args.gain)
        except ValueError:
            sdr.gain = args.gain

        # create outfile
        os.makedirs("data", exist_ok=True)
        csv_path = f"data/{analyze_start:%Y-%m-%dT%H%M%S}-{device}.csv"
        csv_file = open(csv_path, "w")
        csv_consumer = CsvConsumer(csv_file)

        # create analyzers
        analyzer = SignalAnalyzer(sdr, **args.__dict__)
        analyzer.callbacks.append(csv_consumer.add)
        analyzer.callbacks.append(signal_matcher.add)
        analyzer.callbacks.append(lambda sdr, signal: logger.debug(f"SDR '{sdr.device}' received {signal}"))
        analyzers.append(analyzer)

    def handle(sig, frame):
        logging.warning(f"Caught {signal.Signals(sig).name}, terminating {len(analyzers)} analyzers.")

        # Stop the analyzers, and wait for completion
        [a.stop() for a in analyzers]
        [a.join() for a in analyzers]
        logging.warning("Termination complete.")

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)

    # Start all analyzers
    [a.start() for a in analyzers]

    logger.info("All analyzers started.")
    signal.pause()
