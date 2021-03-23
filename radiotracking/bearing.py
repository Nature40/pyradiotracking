import logging
from abc import abstractmethod
from math import acos, pi

from radiotracking import MatchedSignal

logger = logging.getLogger(__name__)


class BearingCalculator:
    @abstractmethod
    def get_angle(self, msig: MatchedSignal):
        pass


class Gottwald2019Bearings(BearingCalculator):
    def __init__(self, signal_maximum: float = -31):
        super().__init__()
        self.signal_maximum = signal_maximum

    def get_angle(self, msig: MatchedSignal):
        # discard
        if None in msig._avgs:
            return

        avgs = list(zip(range(len(msig.devices)), msig._avgs))

        # get loudest antenna
        loudest = max(avgs, key=lambda tup: tup[1])

        # get matching 2nd antenna
        loudest_left = avgs[(loudest[0] - 1) % len(avgs)]
        loudest_right = avgs[(loudest[0] + 1) % len(avgs)]

        logger.info(f"Left: {loudest_left}, loudest: {loudest}, right: {loudest_right}")

        if loudest_left > loudest_right:
            left = loudest_left
            right = loudest
        else:
            left = loudest
            right = loudest_right

        logger.debug(f"Selected {left}, {right} for angle calculation")

        # calculate angle
        gain_delta = (left[1] - right[1]) / self.signal_maximum
        logger.debug(f"gain delta: {gain_delta}")

        angle = (pi / 90) * acos(gain_delta)
        logger.debug(f"relative angle: {angle}")

        return (left[0] * 90) + angle
