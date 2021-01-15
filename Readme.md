pyradiotracking
===

Detect signals of wildlife tracking systems with RTL SDR devices.

### Usage

```bash
$ python3 -m radiotracking -h
usage: radiotracking [-h] [-v] [-d [DEVICE [DEVICE ...]]] [-f CENTER_FREQ] [-s SAMPLE_RATE] [-b SDR_CALLBACK_LENGTH]
                     [-g GAIN] [-n FFT_NPERSEG] [-w FFT_WINDOW] [-t SIGNAL_THRESHOLD_DB] [-r SNR_THRESHOLD_DB]
                     [-l SIGNAL_MIN_DURATION_MS] [-u SIGNAL_MAX_DURATION_MS]

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
                        fft window function, see
                        https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.spectrogram.html
  -t SIGNAL_THRESHOLD_DB, --signal_threshold_db SIGNAL_THRESHOLD_DB
                        lower limit for signal intensity (dBW), default: -50.0
  -r SNR_THRESHOLD_DB, --snr_threshold_db SNR_THRESHOLD_DB
                        lower limit for signal-to-noise ratio (dB), default: 10.0
  -l SIGNAL_MIN_DURATION_MS, --signal_min_duration_ms SIGNAL_MIN_DURATION_MS
                        lower limit for signal duration (ms), default: 8
  -u SIGNAL_MAX_DURATION_MS, --signal_max_duration_ms SIGNAL_MAX_DURATION_MS
                        upper limit for signal duration (ms), default: 40
```

### Troubleshooting

#### Failed to allocate zero-copy buffer

The size of zero-copy kernel buffers is limited.

```
Failed to allocate zero-copy buffer for transfer 5
Falling back to buffers in userspace
Failed to submit transfer 6
Please increase your allowed usbfs buffer size with the following command:
echo 0 > /sys/module/usbcore/parameters/usbfs_memory_mb
```
For larger buffers, as preferable for high sampling rates in terms of effificiency, the usbfs memory limit can be removed:

`echo 0 | sudo tee /sys/module/usbcore/parameters/usbfs_memory_mb`

#### Clock Drift, Resyncing

Each analyzer holds a time stamp internally to derive the time of a detected signal from. The clock is initialized on first async callback from the RTL-SDR library and then incremented according to the data retrieved from the SDR in the following callbacks. Whenever the calculated timestamp differs from the system clock by more than one block length this is detected:

`SDR '0' total clock drift (1.1482 s) is larger than two blocks, signal detection is degraded. Resyncing...`

Resyncing resets the internal clock to the current system clock, such that even though samples have been lost, the detected signals are using the correct timestamp.

> Whenever resyncing is happening, there is a high likelihood, that signals detected in the previous blocks are also undergoing delay. 

Resyncing can be prevented by using less ressource hungry settings, such as: higher signal / SNR thresholds, lower gain, lower sampling rate, larger callback length. 


### Caveats

- The sample rate of RTL-SDR is [limited to certain numbers](https://github.com/osmocom/rtl-sdr/blob/0847e93e0869feab50fd27c7afeb85d78ca04631/src/librtlsdr.c#L1103-L1108) in 225 kHz - 3.2 GHz, excluding 300 kHz - 900 kHz.
