
from rtlsdr import RtlSdr
import logging
import scipy.signal
from radiotracking import Signal
from threading import Thread
import time
import numpy as np

logger = logging.getLogger(__name__)


class SignalAnalyzer(Thread):
    def __init__(
        self,
        sdr: RtlSdr,
        fft_nperseg: int,
        fft_window,
        signal_min_duration: float,
        signal_threshold: float,
        signal_padding: float,
        sdr_callback_length: int = None,
        **kwargs,
    ):
        super().__init__()
        if sdr_callback_length is None:
            sdr_callback_length = sdr.sample_rate

        if len(kwargs) > 0:
            logger.debug(f"Unused arguments for SignalAnalyzer: {kwargs}")

        self.sdr = sdr
        self.fft_nperseg = fft_nperseg
        self.fft_window = fft_window
        self.signal_min_duration = signal_min_duration
        self.signal_threshold = signal_threshold
        self.sdr_callback_length = sdr_callback_length

        # test empty spectorgram
        buffer = sdr.read_samples()
        freqs, times, self._spectrogram_last = scipy.signal.spectrogram(
            buffer,
            window=self.fft_window,
            nperseg=self.fft_nperseg,
            fs=self.sdr.sample_rate,
            return_onesided=False,
        )

        self.sample_duration = times[0]

        # compute numeric values for time constrains
        self.signal_padding_num = int(signal_padding / self.sample_duration)
        self.signal_min_duration_num = int(
            signal_min_duration / self.sample_duration)

        self.signals = []

    def run(self):
        self.sdr.read_samples_async(
            self.process_samples,
            self.sdr_callback_length,
        )

    def stop(self):
        self.sdr.cancel_read_async()

    def process_samples(self, buffer, context):
        """Process samples read from sdr.

        buffer -- Buffer with read samples
        context -- Context as handed back from read_samples_async, unused
        """
        ts_recv = time.time()
        logger.debug(f"received {len(buffer)} samples at {ts_recv}")

        freqs, times, spectrogram = scipy.signal.spectrogram(
            buffer,
            window=self.fft_window,
            nperseg=self.fft_nperseg,
            fs=self.sdr.sample_rate,
            return_onesided=False,
        )

        ts_start = ts_recv - (len(buffer) * times[0])

        self.extract_signals(freqs, times, spectrogram, ts_start)
        self._spectrogram_last = spectrogram

    def consume_signal(self, s):
        if self.signals:
            logger.info(f"{s}, distance {s.ts - self.signals[-1].ts}")
        else:
            logger.info(f"{s}")

        self.signals.append(s)

    def extract_signals(self, freqs, times, spectrogram, ts_start):
        """Extract plateaus from spectogram data.

        Keyword arguments:
        freqs -- spectogram frequency offsets
        times -- spectogram discrete times
        spectrogram -- 2d spectrogram data
        ts_start -- spectogram start time 
        """
        signals = []

        # iterate over all frequencies
        for fi, fft in enumerate(spectrogram):
            freq = freqs[fi]
            ti_skip = 0

            # jump over all power values in signal_min_duration_num distance
            for ti in range(0, len(fft), int(self.signal_min_duration_num)):
                # skip values already inspected during a signal
                if ti < ti_skip:
                    continue

                # check if power of signal over threshold
                if fft[ti] < self.signal_threshold:
                    continue

                # loop down until threshold is undershot
                start = ti
                while start >= -len(self._spectrogram_last):
                    if start < 0:
                        power = self._spectrogram_last[fi, -start]
                    else:
                        power = fft[start]

                    if power < self.signal_threshold:
                        logger.debug(f"found start: {start}")
                        break

                    start -= 1

                # loop up until threshold is undershot
                end = ti
                while end < len(fft):
                    if fft[end] < self.signal_threshold:
                        logger.debug(f"found end: {end}")
                        ti_skip = end
                        break

                    end += 1

                # skip signal, if it laps into next spectogram
                if end == len(fft):
                    logger.debug(
                        "signal overlaps in next spectogram, skipping")
                    continue

                # compute duration and skip, if too short
                duration_s = (end - start) * self.sample_duration
                if duration_s < self.signal_min_duration:
                    continue
                ts = ts_start + (start * self.sample_duration)

                # extract data
                if start < 0:
                    data = np.concatenate(self._spectrogram_last[fi, -
                                                                 start:], fft[:end])
                else:
                    data = fft[start:end]

                signal = Signal(freq, duration_s, ts, data)
                self.consume_signal(signal)
                signals.append(signal)

        return signals
