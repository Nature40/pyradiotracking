import datetime
import logging
import multiprocessing
import os
import signal
import sys
import threading
import time
from typing import Callable, List, Union

import numpy as np
import rtlsdr
import scipy.signal

from radiotracking import Signal, dB, from_dB
from radiotracking.consume import CSVConsumer, MQTTConsumer

logger = logging.getLogger(__name__)


class SignalAnalyzer(multiprocessing.Process):
    def __init__(
        self,
        device: str,
        sample_rate: int,
        center_freq: int,
        gain: float,
        fft_nperseg: int,
        fft_window,
        signal_min_duration_ms: float,
        signal_max_duration_ms: float,
        signal_threshold_db: float,
        snr_threshold_db: float,
        verbose: int,
        stdout: bool,
        csv: bool,
        csv_path: str,
        mqtt: bool,
        mqtt_host: str,
        mqtt_port: int,
        sdr_max_restart: int,
        sdr_callback_length: int = None,
    ):
        super().__init__()

        self.device = device
        # try to use --device as index
        try:
            self.device_index = int(device)
            logger.info(f"Using '{device}' as device index.")
        except ValueError:
            # try to use --device as serial numbers
            try:
                self.device_index = rtlsdr.RtlSdr.get_device_index_by_serial(device)
                logger.info(f"Using '{device}' as serial number (index: {self.device_index}).")
            except rtlsdr.rtlsdr.LibUSBError:
                logger.warning(f"Device '{device}' could was not found, aborting.")
                sys.exit(1)

        self.sample_rate = sample_rate
        self.center_freq = center_freq
        try:
            self.gain = float(gain)
        except ValueError:
            self.gain = gain

        if sdr_callback_length is None:
            sdr_callback_length = sample_rate

        self.fft_nperseg = fft_nperseg
        self.fft_window = fft_window
        self.signal_min_duration = signal_min_duration_ms / 1000
        self.signal_max_duration = signal_max_duration_ms / 1000
        self.signal_threshold = from_dB(signal_threshold_db)
        self.snr_threshold = from_dB(snr_threshold_db)
        self.sdr_callback_length = sdr_callback_length

        self.verbose = verbose
        self.stdout = stdout
        self.csv = csv
        self.csv_path = csv_path
        self.mqtt = mqtt
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port

        self.sdr_max_restart = sdr_max_restart

        self._spectrogram_last = None
        self._ts = None

        self._callbacks: List[Callable] = []

    def run(self):
        signal.signal(signal.SIGTERM, lambda sig, frame: self.stop())
        ts = datetime.datetime.now()

        # logging levels increase in steps of 10, start with warning
        logging_level = max(0, logging.WARN - (self.verbose * 10))
        logging.basicConfig(level=logging_level)

        # add logging consumer
        self._callbacks.append(lambda analyzer, signal: logger.debug(f"SDR {analyzer.device} received {signal}"))

        # add stdout consumer
        if self.stdout:
            stdout_consumer = CSVConsumer(sys.stdout, write_header=False)
            self._callbacks.append(stdout_consumer.add)

        # add csv consumer
        os.makedirs(self.csv_path, exist_ok=True)
        csv_path = f"{self.csv_path}/{ts:%Y-%m-%dT%H%M%S}-{self.device}.csv"
        out = open(csv_path, "w")
        csv_consumer = CSVConsumer(out)
        self._callbacks.append(csv_consumer.add)

        # add mqtt consumer
        if self.mqtt:
            mqtt_consumer = MQTTConsumer(self.mqtt_host, self.mqtt_port)
            self._callbacks.append(mqtt_consumer.add)

        # setup sdr
        sdr = rtlsdr.RtlSdr(self.device_index)
        sdr.sample_rate = self.sample_rate
        sdr.center_freq = self.center_freq
        sdr.gain = float(self.gain)
        self.sdr = sdr

        # start sdr sampling
        t = threading.Thread(target=self.sdr.read_samples_async, args=(self.process_samples, self.sdr_callback_length))
        t.start()

        # monitor sdr
        while True:
            if self._ts:
                timeout_ts = datetime.datetime.now() - datetime.timedelta(seconds=2)
                if self._ts < timeout_ts:
                    logger.critical(f"SDR {self.device} timed out, killing.")
                    break

        self.stop()

    def stop(self):
        self.sdr.cancel_read_async()

    def process_samples(self, buffer, context):
        """Process samples read from sdr.

        buffer -- Buffer with read samples
        context -- Context as handed back from read_samples_async, unused
        """
        ts_recv = datetime.datetime.now()
        buffer_len_dt = datetime.timedelta(seconds=len(buffer) / self.sample_rate)

        # initialize / advance clock
        if not self._ts:
            self._ts = ts_recv
        else:
            self._ts += buffer_len_dt

        clock_drift = (ts_recv - self._ts).total_seconds()

        # warn on clock drift and resync
        if clock_drift > 2 * buffer_len_dt.total_seconds():
            logger.warning(f"SDR {self.device} total clock drift ({clock_drift:.5} s) is larger than two blocks, signal detection is degraded. Resyncing...")
            self._ts = ts_recv
            self._spectrogram_last = None

        ts_start = self._ts - buffer_len_dt

        bench_start = time.time()
        freqs, times, spectrogram = scipy.signal.spectrogram(
            buffer,
            fs=self.sample_rate,
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
            f"SDR {self.device} recv {len(buffer)}, "
            + f"clock drift: {clock_drift:.2} s, "
            + f"filtered {len(filtered)} / {len(signals)} signals, "
            + f"block len: {(buffer_len_dt.total_seconds())*100:.1f} ms, "
            + f"compute: {(bench_consume-bench_start)*100:.1f} ms")

        logger.debug(f"timings - spectogram: {(bench_spectrogram - bench_start)*100:.1f} ms, "
                     + f"extract: {(bench_extract-bench_spectrogram)*100:.1f} ms, "
                     + f"filter: {(bench_filter-bench_extract)*100:.1f} ms, "
                     + f"consume: {(bench_consume-bench_filter)*100:.1f} ms")
        self._spectrogram_last = spectrogram

    def consume_signal(self, signal):
        [callback(self, signal) for callback in self._callbacks]

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
            # set freq_avg to None to allow lazy evaluation
            freq_avg = None
            freq = freqs[fi] + self.center_freq
            ti_skip = 0

            # jump over all power values in signal_min_duration_num distance
            for ti in range(0, len(fft), max(1, int(signal_min_duration_num))):
                # skip values already inspected during a signal
                if ti < ti_skip:
                    continue

                # check if power of signal over threshold
                if fft[ti] < self.signal_threshold:
                    continue

                # lazy computation for freq_avg
                if freq_avg is None:
                    freq_avg = np.mean(fft)

                # check if snr of sample is below threshold
                if fft[ti] / freq_avg < self.snr_threshold:
                    continue

                # loop down until threshold is undershot
                start = ti
                start_min = 0 if self._spectrogram_last is None else -len(self._spectrogram_last[0]) + 1
                while start > start_min:
                    if start < 0:
                        power = self._spectrogram_last[fi, start]
                    else:
                        power = fft[start]

                    # check if power of signal over threshold
                    if power < self.signal_threshold:
                        break

                    # check if snr of sample is below threshold
                    if power / freq_avg < self.snr_threshold:
                        break

                    start -= 1

                # loop up until threshold is undershot
                end = ti
                while end < len(fft):
                    if fft[end] < self.signal_threshold:
                        ti_skip = end
                        break

                    # check if snr of sample is below threshold
                    if fft[end] / freq_avg < self.snr_threshold:
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
                    data = np.concatenate((self._spectrogram_last[fi][start:], fft[:end]))
                else:
                    data = fft[start:end]

                max_dBW = dB(np.max(data))
                min_dBW = dB(np.min(data))
                avg = np.mean(data)
                avg_dBW = dB(avg)
                std_dB = np.std(dB(data))
                snr_dB = dB(avg / freq_avg)

                signal = Signal(ts, freq, duration_s, min_dBW, max_dBW, avg_dBW, std_dB, snr_dB)
                signals.append(signal)

        return signals
