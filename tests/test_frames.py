from __future__ import annotations

import numpy as np
import pytest

from tanaw.frames import Frame, Rect, frame_difference, settle


def solid(value: int, shape: tuple[int, int] = (10, 10)) -> Frame:
    return np.full((*shape, 3), value, dtype=np.uint8)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_rect_rejects_empty_size() -> None:
    with pytest.raises(ValueError):
        Rect(0, 0, 0, 10)


def test_rect_intersection() -> None:
    a = Rect(0, 0, 100, 100)
    assert a.intersect(Rect(50, 50, 100, 100)) == Rect(50, 50, 50, 50)
    assert a.intersect(Rect(200, 200, 10, 10)) is None
    assert a.intersect(Rect(-10, -10, 20, 20)) == Rect(0, 0, 10, 10)


def test_frame_difference() -> None:
    a = solid(100)
    b = a.copy()
    assert frame_difference(a, b) == 0.0
    b[0, :5] = 200  # 5 of 100 pixels change a lot
    assert frame_difference(a, b) == pytest.approx(0.05)
    assert frame_difference(a, solid(105)) == 0.0  # within tolerance: noise
    assert frame_difference(a, solid(100, (5, 5))) == 1.0  # shape mismatch


def test_settle_returns_once_stable() -> None:
    frames = iter([solid(0), solid(80), solid(160), solid(160), solid(255)])
    clock = FakeClock()
    result = settle(lambda: next(frames), clock=clock, sleep=clock.sleep, interval_s=0.016)
    assert result.settled
    assert result.frames_grabbed == 4
    assert int(result.frame[0, 0, 0]) == 160


def test_settle_times_out_on_constant_animation() -> None:
    counter = iter(range(1000))
    clock = FakeClock()

    def animating() -> Frame:
        return solid((next(counter) * 40) % 256)

    result = settle(animating, clock=clock, sleep=clock.sleep, interval_s=0.016, timeout_s=0.1)
    assert not result.settled
    assert result.elapsed_s >= 0.1
    assert result.frames_grabbed <= 8


def test_settle_validates_arguments() -> None:
    with pytest.raises(ValueError):
        settle(lambda: solid(0), threshold=2.0)
