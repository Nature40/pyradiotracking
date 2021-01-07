
from rtlsdr import RtlSdr
import logging
import scipy.signal
from radiotracking import Signal, from_dB
from threading import Thread
import datetime
import numpy as np
from typing import List, Union

logger = logging.getLogger(__name__)


class SignalAnalyzer(Thread):
    def __init__(
        self,
        sdr: RtlSdr,
        fft_nperseg: int,
        fft_window,
        signal_min_duration: float,
        signal_threshold_db: float,
        signal_padding: float,
        sdr_callback_length: int = None,
        **kwargs,
    ):
        super().__init__()
        if sdr_callback_length is None:
            sdr_callback_length = sdr.sample_rate

        self.sdr = sdr
        self.fft_nperseg = fft_nperseg
        self.fft_window = fft_window
        self.signal_min_duration = signal_min_duration
        self.signal_threshold = from_dB(signal_threshold_db)
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

        self.callbacks = [lambda sdr, signal: logger.debug(signal)]

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
        ts_recv = datetime.datetime.now()
        logger.debug(f"received {len(buffer)} samples at {ts_recv}")

        freqs, times, spectrogram = scipy.signal.spectrogram(
            buffer,
            window=self.fft_window,
            nperseg=self.fft_nperseg,
            fs=self.sdr.sample_rate,
            return_onesided=False,
        )

        ts_start = ts_recv - \
            datetime.timedelta(seconds=len(times) * self.sample_duration)

        signals = self.extract_signals(freqs, times, spectrogram, ts_start)
        filtered = self.filter_shadow_signals(signals)
        [self.consume_signal(s) for s in filtered]
        self._spectrogram_last = spectrogram

    def consume_signal(self, signal):
        [callback(self.sdr, signal) for callback in self.callbacks]

    def filter_shadow_signals(self, signals):
        def is_shadow_of(sig: Signal, signals: List[Signal]) -> Union[None, int]:
            """Compute shadow status of received signals. 
            A shadow signal occurs at the same datetime, but with lower power, often in neighbour frequencies.

            Args:
                sig (Signal): The signal to analyse.
                signals (List[Signal]): List of signals to compare to. 

            Returns:
                Union[None, int]: index in signals list, if a shadow of another signal; None if not a shadow.
            """
            # iterate through all other signals
            for i, fsig in enumerate(signals):
                sig_ts_mid = sig.ts + datetime.timedelta(seconds=sig.duration_s/2.0)
                
                # if fsig starts later than middle of sig, ignore
                if sig_ts_mid < fsig.ts:
                    continue
                # if fsig stops before middle of sig, ignore
                if sig_ts_mid > fsig.ts + datetime.timedelta(seconds=fsig.duration_s):
                    continue

                # if fsig is louder, we are a shadow, return index
                if fsig.max > sig.max:
                    return i

            return None

        signals_status = [is_shadow_of(sig, signals) for sig in signals]
        logger.debug(f"shadow list: {signals_status}")

        return [sig for sig, shadow in zip(signals, signals_status) if shadow is None] 


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
            freq = freqs[fi] + self.sdr.center_freq
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
                        power = self._spectrogram_last[fi, start]
                    else:
                        power = fft[start]

                    if power < self.signal_threshold:
                        break

                    start -= 1

                # loop up until threshold is undershot
                end = ti
                while end < len(fft):
                    if fft[end] < self.signal_threshold:
                        ti_skip = end
                        break

                    end += 1

                # skip signal, if it laps into next spectogram
                if end == len(fft):
                    logger.debug(
                        "signal overlaps to next spectogram, skipping")
                    continue

                # compute duration and skip, if too short
                duration_s = (end - start) * self.sample_duration
                if duration_s < self.signal_min_duration:
                    continue
                ts = ts_start + \
                    datetime.timedelta(seconds=start * self.sample_duration)

                # extract data
                if start < 0:
                    # data = self._spectrogram_last[fi][start:] + fft[:end]
                    data = np.concatenate((
                        self._spectrogram_last[fi][start:], fft[:end]))
                else:
                    data = fft[start:end]

                signal = Signal(ts, freq, duration_s, data)
                # self.consume_signal(signal)
                signals.append(signal)

        return signals
