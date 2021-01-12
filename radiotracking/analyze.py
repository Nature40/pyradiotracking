from rtlsdr import RtlSdr
import logging
import scipy.signal
from radiotracking import Signal, from_dB, dB
from threading import Thread
import datetime
import time
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

        self._spectrogram_last = None
        self._ts = None

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
        buffer_len_dt = datetime.timedelta(seconds=len(buffer) / self.sdr.sample_rate)

        # initialize / advance clock
        if not self._ts:
            self._ts = ts_recv
        else:
            self._ts += buffer_len_dt

        clock_drift = (ts_recv - self._ts).total_seconds()
        logger.info(f"received {len(buffer)} samples, total clock drift: {clock_drift:.2} s")

        # warn on clock drift and resync
        if clock_drift > 2 * buffer_len_dt.total_seconds():
            logger.warn(f"total clock drift ({clock_drift:.5} s) is larger than two blocks, signal detection is degraded. Resyncing...")
            self._ts = ts_recv
            self._spectrogram_last = None

        ts_start = self._ts - buffer_len_dt

        bench_start = time.time()
        freqs, times, spectrogram = scipy.signal.spectrogram(
            buffer,
            fs=self.sdr.sample_rate,
            window=self.fft_window,
            nperseg=self.fft_nperseg,
            noverlap=0,
            return_onesided=False,
        )

        bench_spectrogram = time.time()

        signals = self.extract_signals(freqs, times, spectrogram, ts_start)
        bench_extract = time.time()

        filtered = self.filter_shadow_signals(signals)
        bench_filter = time.time()

        [self.consume_signal(s) for s in filtered]
        bench_consume = time.time()

        logger.info(
            f"filtered {len(filtered)} / {len(signals)} signals, timings: "
            + f"block len: {(buffer_len_dt.total_seconds())*100:.1f} ms, "
            + f"total: {(bench_consume-bench_start)*100:.1f} ms ("
            + f"spectogram: {(bench_spectrogram - bench_start)*100:.1f} ms, "
            + f"extract: {(bench_extract-bench_spectrogram)*100:.1f} ms, "
            + f"filter: {(bench_filter-bench_extract)*100:.1f} ms, "
            + f"consume: {(bench_consume-bench_filter)*100:.1f} ms)"
        )
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
                start_min = 0 if self._spectrogram_last is None else -len(self._spectrogram_last[0]) + 1
                while start > start_min:
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
