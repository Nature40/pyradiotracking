pyradiotracking
===

Detect signals of wildlife tracking systems with RTL SDR devices.

### Usage

```bash
$ python3 -m radiotracking -h
usage: radiotracking [-h] [-v] [-d [DEVICE [DEVICE ...]]] [-f CENTER_FREQ] [-s SAMPLE_RATE]
                     [-b SDR_CALLBACK_LENGTH] [-g GAIN] [-n FFT_NPERSEG] [-w FFT_WINDOW]
                     [-t SIGNAL_THRESHOLD_DB] [-l SIGNAL_MIN_DURATION] [-p SIGNAL_PADDING]

Detect signals of wildlife tracking systems with RTL SDR devices

optional arguments:
  -h, --help            show this help message and exit
  -v, --verbose         increase output verbosity

software-defined radio (SDR):
  -d [DEVICE [DEVICE ...]], --device [DEVICE [DEVICE ...]]
                        device indexes or names, default: 0
  -f CENTER_FREQ, --center_freq CENTER_FREQ
                        center frequency to tune to (Hz), default: 150100001
  -s SAMPLE_RATE, --sample_rate SAMPLE_RATE
                        sample rate (Hz), default: 2048000
  -b SDR_CALLBACK_LENGTH, --sdr_callback_length SDR_CALLBACK_LENGTH
                        number of samples to read per batch
  -g GAIN, --gain GAIN  gain, supported levels 0.0 - 49.6, default: 49.6

signal analysis:
  -n FFT_NPERSEG, --fft_nperseg FFT_NPERSEG
                        fft number of samples
  -w FFT_WINDOW, --fft_window FFT_WINDOW
                        fft window function, see https://docs.scipy.org/doc/scipy/reference/genera
                        ted/scipy.signal.spectrogram.html
  -t SIGNAL_THRESHOLD_DB, --signal_threshold_db SIGNAL_THRESHOLD_DB
                        lower limit for signal intensity (dBW)
  -l SIGNAL_MIN_DURATION, --signal_min_duration SIGNAL_MIN_DURATION
                        lower limit for signal duration (s), default: 0.002
  -p SIGNAL_PADDING, --signal_padding SIGNAL_PADDING
                        padding to apply when analysing signal (s), default: 0.001
```

### Caveats

- The sample rate of RTL-SDR is [limited to certain numbers](https://github.com/osmocom/rtl-sdr/blob/0847e93e0869feab50fd27c7afeb85d78ca04631/src/librtlsdr.c#L1103-L1108) in 225 kHz - 3.2 GHz, excluding 300 kHz - 900 kHz.
