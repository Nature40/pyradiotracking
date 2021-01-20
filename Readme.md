pyradiotracking
===

Detect signals of wildlife tracking systems with RTL SDR devices.

### Usage

```bash
$ python3 -m radiotracking -h
usage: radiotracking [-h] [-v] [-d [DEVICE [DEVICE ...]]] [-f CENTER_FREQ] [-s SAMPLE_RATE] [-b SDR_CALLBACK_LENGTH] [-g GAIN]
                     [--sdr-max-restart SDR_MAX_RESTART] [-n FFT_NPERSEG] [-w FFT_WINDOW] [-t SIGNAL_THRESHOLD_DB] [-r SNR_THRESHOLD_DB]
                     [-l SIGNAL_MIN_DURATION_MS] [-u SIGNAL_MAX_DURATION_MS] [--stdout] [--csv] [--csv-path CSV_PATH] [--mqtt]
                     [--mqtt-host MQTT_HOST] [--mqtt-port MQTT_PORT]

Detect signals of wildlife tracking systems with RTL SDR devices

optional arguments:
  -h, --help            show this help message and exit
  -v, --verbose         increase output verbosity

software-defined radio (SDR):
  -d [DEVICE [DEVICE ...]], --device [DEVICE [DEVICE ...]]
                        device indexes or names, default: 0
  -f CENTER_FREQ, --center-freq CENTER_FREQ
                        center frequency to tune to (Hz), default: 150100001
  -s SAMPLE_RATE, --sample-rate SAMPLE_RATE
                        sample rate (Hz), default: 300000
  -b SDR_CALLBACK_LENGTH, --sdr-callback-length SDR_CALLBACK_LENGTH
                        number of samples to read per batch
  -g GAIN, --gain GAIN  gain, supported levels 0.0 - 49.6, default: 49.6
  --sdr-max-restart SDR_MAX_RESTART
                        maximal restart count per SDR device, default: 3

signal analysis:
  -n FFT_NPERSEG, --fft-nperseg FFT_NPERSEG
                        fft number of samples, default: 256
  -w FFT_WINDOW, --fft-window FFT_WINDOW
                        fft window function, default: 'hamming', see
                        https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.spectrogram.html
  -t SIGNAL_THRESHOLD_DB, --signal-threshold-db SIGNAL_THRESHOLD_DB
                        lower limit for signal intensity (dBW), default: -50.0
  -r SNR_THRESHOLD_DB, --snr-threshold-db SNR_THRESHOLD_DB
                        lower limit for signal-to-noise ratio (dB), default: 10.0
  -l SIGNAL_MIN_DURATION_MS, --signal-min-duration-ms SIGNAL_MIN_DURATION_MS
                        lower limit for signal duration (ms), default: 8
  -u SIGNAL_MAX_DURATION_MS, --signal-max-duration-ms SIGNAL_MAX_DURATION_MS
                        upper limit for signal duration (ms), default: 40

data publishing:
  --stdout              enable stdout data publishing, default: False
  --csv                 enable csv data publishing, default: False
  --csv-path CSV_PATH   csv folder path, default: ./data/curta/radiotracking
  --mqtt                enable mqtt data publishing, default: False
  --mqtt-host MQTT_HOST
                        hostname of mqtt broker, default: localthost
  --mqtt-port MQTT_PORT
                        port of mqtt broker, default: 1883
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

`SDR 0 total clock drift (1.1482 s) is larger than two blocks, signal detection is degraded. Resyncing...`

Resyncing resets the internal clock to the current system clock, such that even though samples have been lost, the detected signals are using the correct timestamp.

> Whenever resyncing is happening, there is a high likelihood, that signals detected in the previous blocks are also undergoing delay. 

Resyncing can be prevented by using less ressource hungry settings, such as: higher signal / SNR thresholds, lower gain, lower sampling rate, larger callback length. 


### Caveats

- The sample rate of RTL-SDR is [limited to certain numbers](https://github.com/osmocom/rtl-sdr/blob/0847e93e0869feab50fd27c7afeb85d78ca04631/src/librtlsdr.c#L1103-L1108) in 225 kHz - 3.2 GHz, excluding 300 kHz - 900 kHz.
