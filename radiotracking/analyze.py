from rtlsdr import RtlSdr
import logging
import scipy.signal
from radiotracking import Signal, from_dB, dB
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
        signal_min_duration_ms: float,
        signal_max_duration_ms: float,
        signal_threshold_db: float,
        sdr_callback_length: int = None,
        **kwargs,
    ):
        super().__init__()
        if sdr_callback_length is None:
            sdr_callback_length = sdr.sample_rate

        self.sdr = sdr
        self.fft_nperseg = fft_nperseg
        self.fft_window = fft_window
        self.signal_min_duration = signal_min_duration_ms / 1000
        self.signal_max_duration = signal_max_duration_ms / 1000
        self.signal_threshold = from_dB(signal_threshold_db)
        self.sdr_callback_length = sdr_callback_length

        # test empty spectorgram
        buffer = sdr.read_samples(self.sdr_callback_length)
        _, _, self._spectrogram_last = scipy.signal.spectrogram(
            buffer,
            window=self.fft_window,
            nperseg=self.fft_nperseg,
            fs=self.sdr.sample_rate,
            return_onesided=False,
        )
        self._ts_recv_last = None
        self._skipped_samples_sum = 0

        self.callbacks = [lambda sdr, signal: logger.debug(signal)]

    def run(self):
        self._time = datetime.datetime.now()
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
        ts_start = ts_recv - datetime.timedelta(seconds=len(buffer) / self.sdr.sample_rate)

        # compute difference of computed and actual time and derive skipped samples
        if self._ts_recv_last:
            ts_diff = ts_start - self._ts_recv_last
            samples_diff = int(ts_diff.total_seconds() * self.sdr.sample_rate)
            self._skipped_samples_sum += samples_diff
            logger.debug(f"received {len(buffer)} samples, difference to expeted: {samples_diff}, sum: {self._skipped_samples_sum}")
            if self._skipped_samples_sum > self.sdr.sample_rate:
                logger.warn(f"skipped more than one block of samples (sum: {self._skipped_samples_sum}), signal quality is degraded.")

        freqs, times, spectrogram = scipy.signal.spectrogram(
            buffer,
            window=self.fft_window,
            nperseg=self.fft_nperseg,
            fs=self.sdr.sample_rate,
            return_onesided=False,
        )

        signals = self.extract_signals(freqs, times, spectrogram, ts_start)
        filtered = self.filter_shadow_signals(signals)

        [self.consume_signal(s) for s in filtered]
        self._spectrogram_last = spectrogram
        self._ts_recv_last = ts_recv

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
                sig_ts_mid = sig.ts + datetime.timedelta(seconds=sig.duration_s / 2.0)

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

        signal_min_duration_num = self.signal_min_duration / (times[1] - times[0])

        # iterate over all frequencies
        for fi, fft in enumerate(spectrogram):
            freq_avg_dBW = None
            freq = freqs[fi] + self.sdr.center_freq
            ti_skip = 0

            # jump over all power values in signal_min_duration_num distance
            for ti in range(0, len(fft), max(1, int(signal_min_duration_num))):
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
                    logger.debug("signal overlaps to next spectogram, skipping")
                    continue

                # compute duration and skip, if too short
                end_dt = times[end]
                # if start has negative index
                if start < 0:
                    start_dt = -times[-start]
                else:
                    start_dt = times[start]

                duration_s = end_dt - start_dt
                if duration_s < self.signal_min_duration:
                    continue
                if duration_s > self.signal_max_duration:
                    logger.debug(f"signal duration too long ({duration_s * 1000} > {self.signal_max_duration*1000} ms), skipping")
                    continue
                ts = ts_start + datetime.timedelta(seconds=start_dt)

                # extract data
                if start < 0:
                    # data = self._spectrogram_last[fi][start:] + fft[:end]
                    data = np.concatenate((self._spectrogram_last[fi][start:], fft[:end]))
                else:
                    data = fft[start:end]

                if not freq_avg_dBW:
                    freq_avg_dBW = np.mean(dB(fft))

                max_dBW = dB(np.max(data))
                min_dBW = dB(np.min(data))
                avg_dBW = np.mean(dB(data))
                std_dB = np.std(dB(data))
                snr_dB = avg_dBW - freq_avg_dBW

                signal = Signal(ts, freq, duration_s, min_dBW, max_dBW, avg_dBW, std_dB, snr_dB)
                signals.append(signal)

        return signals
