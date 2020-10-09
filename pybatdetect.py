#!/usr/bin/env python3

from rtlsdr import RtlSdr
import argparse
import logging
import signal
import scipy.signal
import numpy as np

from si_prefix import si_format

logger = logging.getLogger("pybatdetect")


class Plateau:
    data = None

    def __init__(self, freq, start, sample_duration_s, _padding_s=0.001):
        self.freq = freq
        self.start = start
        self.sample_duration_s = sample_duration_s
        self._padding_s = _padding_s
        self._padding = int(_padding_s / sample_duration_s)

    def finalize(self, end, fft):
        self.end = end
        self.data = fft[self.start:end]

    @property
    def duration(self):
        return self.end - self.start

    @property
    def duration_s(self):
        return self.duration * self.sample_duration_s

    @property
    def max(self):
        return max(self.data[self._padding:-self._padding])

    @property
    def min(self):
        return min(self.data[self._padding:-self._padding])

    @property
    def avg(self):
        return np.average(self.data[self._padding:-self._padding])

    @property
    def sum(self):
        return sum(self.data)

    def plot(self, fig):
        if not fig:
            fig = plt.figure()
        times = [i * self.sample_duration_s for i in range(self.duration)]
        ax, = plt.plot(times[self._padding:-self._padding],
                       self.data[self._padding:-self._padding])
        return fig

    def __str__(self):
        return f"plateau: \
{si_format(self.freq, precision=3)}Hz, \
{si_format(self.duration_s)}s, \
power min:{si_format(self.min)}, avg:{si_format(self.avg)}, max:{si_format(self.max)}, sum:{si_format(self.sum)}"


def extract_plateaus(
        center_frequency,
        freqs,
        times,
        spectrogram,
        power_thres=0.00005,
        min_duration_s=0.002):
    """Extract plateaus from spectogram data.

    Keyword arguments:
    center_frequency -- spectogram center frequency
    freqs -- spectogram frequency offsets
    times -- spectogram discrete times
    spectrogram -- 2d spectrogram data
    power_thres -- minimal power to detect signal (default 0.00005)
    min_duration_s - minimal duration of a signal in seconds (default 0.002)
    """
    min_duration = int(min_duration_s / times[0])
    plateaus = []

    # iterate over all frequencies
    for freq, fft in zip([center_frequency + f for f in freqs], spectrogram):
        ti_skip = 0

        # jump over all power values in min_duration distance
        for ti in range(0, len(fft), int(min_duration)):
            if ti < ti_skip:
                continue

            power = fft[ti]
            if power < power_thres:
                continue

            # loop down until threshold is reached
            start = ti
            while start > 0:
                if fft[start] < power_thres:
                    plateau = Plateau(freq, start, times[0])
                    break

                start -= 1

            # loop up until threshold is reached
            end = ti
            while end < len(fft):
                if fft[end] < power_thres:
                    plateau.finalize(end, fft)
                    break
                end += 1
            ti_skip = end

            if plateau.duration > min_duration:
                logger.debug(plateau)
                plateaus.append(plateau)

    return plateaus


def process_samples(buffer, context):
    """Process samples read from sdr.

    buffer -- Buffer of type unsigned char
    context -- User-defined value passed to rtlsdr_read_async.
    """
    logger.debug(f"received {len(buffer)} samples")

    freqs, times, spectrogram = scipy.signal.spectrogram(
        buffer,
        window=("boxcar",),
        nperseg=400,
        mode="psd",
        fs=context.sample_rate,
        return_onesided=False,
    )

    plateaus = extract_plateaus(context.center_freq, freqs, times, spectrogram)


parser = argparse.ArgumentParser(fromfile_prefix_chars='@')
parser.add_argument("-d", "--device",
                    help="device index",
                    default=0,
                    type=int)
parser.add_argument("-f", "--frequency",
                    help="center frequency to tune to (Hz)",
                    default=150100001,
                    type=int)
parser.add_argument("-g", "--gain",
                    help="gain (0 for auto)",
                    default="auto")
parser.add_argument("-p", "--ppm_error",
                    help="frequency correction (ppm)",
                    type=int)
parser.add_argument("-s", "--sample_rate",
                    help="sample rate (Hz)",
                    default="2048000",
                    type=int)
parser.add_argument("-v", "--verbose",
                    help="increase output verbosity",
                    action='count',
                    default=0)
parser.add_argument("-w", "--fft_window",
                    help="number of samples evaluated per fft (power of 2)",
                    type=int,
                    default=512)


if __name__ == "__main__":
    args = parser.parse_args()

    # logging levels increase in steps of 10, start with warning
    logging_level = max(0, logging.WARN - (args.verbose * 10))
    logging.basicConfig(level=logging_level)

    # describe configuration
    logger.info(
        f"center frequency: {si_format(args.frequency, precision=3)}Hz")
    logger.info(f"sampling rate: {si_format(args.sample_rate, precision=3)}Hz")

    frequency_min = args.frequency - args.sample_rate/2
    frequency_max = args.frequency + args.sample_rate/2
    logger.info(
        f"band: {si_format(frequency_min, precision=3)}Hz - {si_format(frequency_max, precision=3)}Hz")

    sdr = RtlSdr()
    sdr.sample_rate = args.sample_rate
    sdr.center_freq = args.frequency
    if args.ppm_error:
        sdr.freq_correction = args.ppm_error
    sdr.gain = args.gain

    def handle(sig, frame):
        logging.warning(
            f"Caught {signal.Signals(sig).name}, terminating pybatdetect.")
        sdr.cancel_read_async()

    signal.signal(signal.SIGINT, handle)

    sdr.read_samples_async(process_samples, args.sample_rate)
