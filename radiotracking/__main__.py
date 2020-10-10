#!/usr/bin/env python3

from numpy.lib.financial import nper
from si_prefix import si_format
from radiotracking.analyze import SignalAnalyzer
import signal
import logging
import argparse
from rtlsdr import RtlSdr


logger = logging.getLogger(__name__)

arg_parser = argparse.ArgumentParser(fromfile_prefix_chars='@')
# generic options
arg_parser.add_argument("-v", "--verbose",
                        help="increase output verbosity",
                        action='count',
                        default=0)

# sdr / sampling options
arg_parser.add_argument("-d", "--device",
                        help="device index, default: 0",
                        default=0,
                        type=int)
arg_parser.add_argument("-f", "--center_freq",
                        help="center frequency to tune to (Hz), default: 150100001",
                        default=150100001,
                        type=int)
arg_parser.add_argument("-s", "--sample_rate",
                        help="sample rate (Hz), default: 2048000",
                        default=2048000,
                        type=int)
arg_parser.add_argument("--sdr_callback_length",
                        help="number of samples to read per batch",
                        default=None,
                        type=int)
arg_parser.add_argument("-g", "--gain",
                        help="gain (0 for auto), default: autp",
                        default="auto")

# analysis options
arg_parser.add_argument("-n", "--fft_nperseg",
                        help="fft number of samples",
                        default=None,
                        type=int)
arg_parser.add_argument("-w", "--fft_window",
                        help="fft window function, see https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.spectrogram.html",
                        type=eval,
                        default="'boxcar'")
arg_parser.add_argument("-l", "--signal_min_duration",
                        help="lower limit for signal duration (s), default: 0.002",
                        type=float,
                        default=0.002)
arg_parser.add_argument("-t", "--signal_threshold",
                        help="lower limit for signal intensity",
                        type=float,
                        default=0.00005)
arg_parser.add_argument("-p", "--signal_padding",
                        help="padding to apply when analysing signal (s), default: 0.001",
                        type=float,
                        default=0.001)


if __name__ == "__main__":
    args = arg_parser.parse_args()

    # logging levels increase in steps of 10, start with warning
    logging_level = max(0, logging.WARN - (args.verbose * 10))
    logging.basicConfig(level=logging_level)

    # describe configuration
    logger.info(
        f"center frequency {si_format(args.center_freq, precision=3)}Hz")
    logger.info(f"sampling rate {si_format(args.sample_rate, precision=3)}Hz")

    frequency_min = args.center_freq - args.sample_rate/2
    frequency_max = args.center_freq + args.sample_rate/2
    logger.info(
        f"band {si_format(frequency_min, precision=3)}Hz - {si_format(frequency_max, precision=3)}Hz")

    # initialize sdr
    sdr = RtlSdr()
    sdr.sample_rate = args.sample_rate
    sdr.center_freq = args.center_freq
    sdr.gain = args.gain

    # create analzyer
    analyzers = []
    analyzers.append(SignalAnalyzer(sdr, **args.__dict__))

    def handle(sig, frame):
        logging.warning(
            f"Caught {signal.Signals(sig).name}, terminating {len(analyzers)} analyzers.")

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
