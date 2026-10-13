"""Recording checkpoints are fixed audio-time boundaries, independent of attempts."""

import math


def audio_checkpoint(elapsed, interval, *, is_final=False):
    position = max(0, elapsed) / interval
    return max(1, math.ceil(position)) if is_final else math.floor(position)
