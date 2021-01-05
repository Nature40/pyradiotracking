
import logging
import csv
import rtlsdr
from radiotracking import Signal


class SignalMatcher:
    def __init__(self):
        self.signals = []

    def add(self, sdr: rtlsdr.RtlSdr, signal: Signal):
        # signals arrive in arbitrary order and signals could

        pass


class CsvConsumer:
    def __init__(self, out):
        self.out = out
        self.writer = csv.writer(out, dialect="excel", delimiter=";")
        self.writer.writerow(Signal.header)
        self.out.flush()

    def add(self, sdr: rtlsdr.RtlSdr, signal: Signal, **kwargs):
        self.writer.writerow(signal.as_list)
        self.out.flush()
