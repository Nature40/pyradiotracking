
from rtlsdr import RtlSdr
import logging
import scipy.signal
from radiotracking import Signal, from_dB
from threading import Thread
import datetime
import numpy as np

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
        # a shadow signal is one with 
        # a) significant time overlap with another signal of 
        # b) a higher max dBW.

        filtered = []

        for sig in signals:
            def match_filtered(sig, filtered):
                is_shadow = False
                has_shadow = None
            
                # check every signal against all previous non-shadow signals 
                for i, fsig in enumerate(filtered):
                    # check if middle of signal is in this filtered signal
                    sig_ts_mid = sig.ts + datetime.timedelta(seconds=sig.duration_s/2.0)
                    fsig_ts_start = fsig.ts
                    fsig_ts_end = fsig.ts + datetime.timedelta(seconds=fsig.duration_s)

                    # middle of sig is inbetween start and end of fsig
                    if fsig_ts_start < sig_ts_mid and fsig_ts_end > sig_ts_mid:
                        # if fsig is louder, sig is a shadow
                        if fsig.max > sig.max:
                            logger.debug(f"             {sig}")
                            logger.debug(f"is shadow of {fsig}")
                            is_shadow = True
                        # if sig is louder, fsig is a shadow
                        else:
                            logger.debug(f"           {sig}")
                            logger.debug(f"has shadow {fsig}")
                            has_shadow = i
                    else:
                        logger.debug(f"                    {sig}")
                        logger.debug(f"not at same time as {fsig}")
                        logger.debug(f"{fsig_ts_start} < {sig_ts_mid} < {fsig_ts_end}")

                return is_shadow, has_shadow

            is_shadow, has_shadow = match_filtered(sig, filtered)
            logger.debug(f"len: {len(filtered)}, has_shadow: {has_shadow}, is_shadow: {is_shadow}")
            if has_shadow != None:
                filtered.pop(has_shadow)
            if not is_shadow:
                filtered.append(sig)
            
            logger.debug(f"len: {len(filtered)}")

        return filtered

        


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
