import datetime
import logging
import multiprocessing
import signal
import sys
import time
from typing import List, Union

import numpy as np
import pytz
import rtlsdr
import scipy.signal

import radiotracking
from radiotracking import Signal, StateMessage, dB, from_dB

logger = logging.getLogger(__name__)


class SignalAnalyzer(multiprocessing.Process):
    """
    Class representing a process for analysis of rtlsdr samples.

    Parameters
    ----------
    device: str
        The device index or serial number of the SDR to use.
    calibration_db: float
        The calibration offset in dB.
    sample_rate: int
        The sample rate of the SDR.
    center_freq: int
        The center frequency of the SDR.
    gain: float
        The gain of the SDR.
    fft_nperseg: int
        The number of samples per segment for the FFT.
    fft_window:
        The window function to use for the FFT.
    signal_min_duration_ms: float
        The minimum duration of a signal in ms.
    signal_max_duration_ms: float
        The maximum duration of a signal in ms.
    signal_threshold_dbw: float
        The signal threshold in dBW.
    snr_threshold_db: float
        The SNR threshold in dB.
    verbose: int
        The verbosity level.
    sdr_max_restart: int
        The maximum number of times to restart the SDR.
    sdr_timeout_s: int
        The timeout in seconds for the SDR.
    sdr_callback_length: int
        The length of the SDR callback in samples.
    signal_queue: multiprocessing.Queue
        The multiprocessing queue to put the signals in.
    last_data_ts: multiprocessing.Value
        The multiprocessing value to put the last data timestamp in.
    """

    def __init__(
        self,
        device: str,
        calibration_db: float,
        sample_rate: int,
        center_freq: int,
        gain: float,
        fft_nperseg: int,
        fft_window,
        signal_min_duration_ms: float,
        signal_max_duration_ms: float,
        signal_threshold_dbw: float,
        snr_threshold_db: float,
        verbose: int,
        sdr_max_restart: int,
        sdr_timeout_s: int,
        state_update_s: int,
        sdr_callback_length: int,
        signal_queue: multiprocessing.Queue,
        last_data_ts: multiprocessing.Value,
        **kwargs,
    ):
        super().__init__()

        self.device = device
        self.calibration_db = calibration_db
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
        self.signal_threshold = from_dB(signal_threshold_dbw + calibration_db)
        self.snr_threshold = from_dB(snr_threshold_db)
        self.sdr_callback_length = sdr_callback_length

        self.verbose = verbose

        self.sdr_max_restart = sdr_max_restart
        self.sdr_timeout_s = sdr_timeout_s
        self.state_update_s = state_update_s

        self.signal_queue = signal_queue
        self.last_data_ts = last_data_ts

        self._spectrogram_last = None
        self._ts = None

    def run(self):
        """
        Starts the analyzing process and hands control flow over to rtlsdr.
        """
        signal.signal(signal.SIGTERM, self.handle_signal)
        signal.signal(signal.SIGINT, self.handle_signal)

        # logging levels increase in steps of 10, start with warning
        logging_level = max(0, logging.WARN - (self.verbose * 10))
        logging.basicConfig(level=logging_level)

        # setup sdr
        sdr = rtlsdr.RtlSdr(self.device_index)
        sdr.sample_rate = self.sample_rate
        sdr.center_freq = self.center_freq
        sdr.gain = float(self.gain)
        sdr.set_agc_mode(False)
        self.sdr = sdr

        # initialize state
        self.last_state = None

        signal.signal(signal.SIGALRM, self.handle_signal)
        signal.alarm(self.sdr_timeout_s)

        # start sdr sampling
        self.sdr.read_samples_async(self.process_samples, self.sdr_callback_length)

    def handle_signal(self, sig, frame):
        """
        Handles the SIGINT and SIGTERM signals.

        Parameters
        ----------
        sig: int
            The signal number.
        frame:
            The stack frame.
        """
        if sig == signal.SIGALRM:
            logger.warning("SDR %s received SIGALRM, last data received %s ago.", self.device, datetime.datetime.now() - self._ts if self._ts else '(no signal yet)')
        elif sig == signal.SIGTERM:
            logger.warning("SDR %s received SIGTERM, terminating.", self.device)
        elif sig == signal.SIGINT:
            return

        self.update_state(datetime.datetime.now(), StateMessage.State.STOPPED)
        self.sdr.cancel_read_async()

    def update_state(self, ts: datetime.datetime, state: StateMessage.State):
        # skip update if there is a state
        if self.last_state:
            # the state is different
            if self.last_state.state == state:
                # the state's timeout isn't over
                if self.last_state.ts + datetime.timedelta(seconds=self.state_update_s) >= ts.astimezone(pytz.UTC):
                    return

        self.last_state = StateMessage(self.device, ts.astimezone(pytz.utc), state)
        self.signal_queue.put(self.last_state)

    def process_samples(self, buffer: np.ndarray, context):
        """
        Process samples read from sdr, callback method.

        Parameters
        ----------
        buffer: np.ndarray
            Buffer with read samples
        context:
            Context as handed back from read_samples_async, unused
        """
        # note recv datetime
        ts_recv = datetime.datetime.now()
        buffer_len_dt = datetime.timedelta(seconds=len(buffer) / self.sample_rate)

        # reset alarm timer
        signal.alarm(self.sdr_timeout_s)
        # logger.info()
        if not self.last_data_ts.value:
            self.update_state(datetime.datetime.now(), StateMessage.State.STARTED)
        else:
            self.update_state(ts_recv, StateMessage.State.RUNNING)
        self.last_data_ts.value = datetime.datetime.timestamp(ts_recv)
        logger.info(f"SDR {self.device} received data at {self.last_data_ts.value}")

        # initialize / advance clock
        if not self._ts:
            self._ts = ts_recv
        else:
            self._ts += buffer_len_dt

        clock_drift = (ts_recv - self._ts).total_seconds()

        # warn on clock drift and resync
        if clock_drift > 2 * buffer_len_dt.total_seconds():
            logger.warning(f"SDR {self.device} total clock drift ({clock_drift:.5f} s) is larger than two blocks, signal detection is degraded. Terminating...")
            self.update_state(datetime.datetime.now(), StateMessage.State.STOPPED)
            self.sdr.cancel_read_async()

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
            + f"clock drift: {clock_drift:.2f} s, "
            + f"filtered {len(filtered)} / {len(signals)} signals, "
            + f"block len: {(buffer_len_dt.total_seconds())*100:.1f} ms, "
            + f"compute: {(bench_consume-bench_start)*100:.1f} ms"
        )

        logger.debug(
            f"timings - spectogram: {(bench_spectrogram - bench_start)*100:.1f} ms, "
            + f"extract: {(bench_extract-bench_spectrogram)*100:.1f} ms, "
            + f"filter: {(bench_filter-bench_extract)*100:.1f} ms, "
            + f"consume: {(bench_consume-bench_filter)*100:.1f} ms"
        )
        self._spectrogram_last = spectrogram

    def consume_signal(self, signal: Signal):
        """
        Puts a detected signals into the signal queue.

        Parameters
        ----------
        signal: radiotracking.Signal
            The signal to put into the queue.
        """
        logger.debug(f"SDR {self.device} received {signal}")
        self.signal_queue.put(signal)

    @staticmethod
    def is_shadow_of(sig: Signal, signals: List[Signal]) -> Union[None, int]:
        """Compute shadow status of received signals.
        A shadow signal occurs at the same datetime, but with lower power, often in neighbour frequencies.

        Parameters
        ----------
        sig: radiotracking.Signal
            The signal to analyse.
        signals: typing.List[radiotracking.Signal]
            List of signals to compare to.

        Returns
        -------
        Union[None, int]:
            index in signals list, if a shadow of another signal; None if not a shadow.
        """
        # iterate through all other signals
        for i, fsig in enumerate(signals):
            # if sig starts after fsig ends, ignore
            if sig.ts > fsig.ts + fsig.duration:
                continue

            # if sig ends before fsig starts, ignore
            if sig.ts + sig.duration < fsig.ts:
                continue

            # if fsig is louder, we are a shadow, return index
            if fsig.max > sig.max:
                return i

        return None

    def filter_shadow_signals(self, signals: List[Signal]):
        """
        Filters out signals that are too close to each other.

        Parameters
        ----------
        signals: typing.List[radiotracking.Signal]
            The signals to filter.
        """

        signals_status = [SignalAnalyzer.is_shadow_of(sig, signals) for sig in signals]
        logger.debug(f"shadow list: {signals_status}")

        return [sig for sig, shadow in zip(signals, signals_status) if shadow is None]

    def extract_signals(self, freqs: np.ndarray, times: np.ndarray, spectrogram: np.ndarray, ts_start: datetime.datetime) -> List[Signal]:
        """Extract plateaus from spectogram data.

        Parameters
        ----------
        freqs: np.ndarray
            spectogram frequency offsets
        times: np.ndarray
            spectogram discrete times
        spectrogram: np.ndarray
            2d spectrogram data
        ts_start:
            spectogram start time

        Returns
        -------
        List[radiotracking.Signal]
            List of signals extracted from spectogram.
        """
        signals = []

        if len(times) == 0:
            return signals

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
                duration = datetime.timedelta(seconds=duration_s)
                if duration_s < self.signal_min_duration:
                    continue
                if duration_s > self.signal_max_duration:
                    logger.debug(f"signal duration too long ({duration_s * 1000} > {self.signal_max_duration*1000} ms), skipping")
                    continue
                ts = ts_start + datetime.timedelta(seconds=start_dt)

                # extract data
                if start < 0:
                    data = np.concatenate((self._spectrogram_last[fi][start:], fft[: end]))
                else:
                    data = fft[start: end]

                max_dBW = dB(np.max(data)) - self.calibration_db
                avg = np.mean(data)
                avg_dBW = dB(avg) - self.calibration_db
                std_dB = np.std(dB(data))
                noise_dBW = dB(freq_avg)
                snr_dB = dB(avg / freq_avg)

                signal = Signal(self.device, ts.astimezone(pytz.utc), freq, duration, max_dBW, avg_dBW, std_dB, noise_dBW, snr_dB)
                signals.append(signal)

        return signals
