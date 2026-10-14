import pytest

from app.sources.http import UpstreamBusy
from app.sources.openmeteo import CallBudget, call_weight


def test_call_weight_matches_open_meteo_pricing_examples():
    assert call_weight(1, 15, 14) == pytest.approx(1.5)  # their FAQ example
    assert call_weight(1, 1, 14) == 1.0
    assert call_weight(1, 1, 365) == 1.0 * max(1, 0.1 * 365 / 14)
    # 65 years of one daily variable at one cell: what a region costs us
    assert call_weight(1, 1, 23741) == pytest.approx(169.6, abs=0.1)
    assert call_weight(9, 1, 23741) == pytest.approx(9 * 169.6, abs=1)


class FakeClock:
    def __init__(self, t=1_000_065.0):  # 45 s into a minute
        self.t = t
        self.slept = []

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.slept.append(s)
        self.t += s


async def test_budget_waits_for_next_minute():
    clock = FakeClock()
    b = CallBudget(600, 5000, 10000, clock=clock, sleep=clock.sleep)
    await b.acquire(700)  # allowed: counter was under the limit when it arrived
    await b.acquire(100)  # minute is spent -> wait for the next one
    assert clock.slept and clock.slept[0] <= 61
    assert b.used["hour"] == 800


async def test_budget_refuses_when_the_day_is_spent():
    clock = FakeClock()
    b = CallBudget(600, 5000, 1000, clock=clock, sleep=clock.sleep)
    await b.acquire(900)
    with pytest.raises(UpstreamBusy) as exc:
        await b.acquire(200)
    assert exc.value.retry_after > 0


async def test_budget_gives_up_instead_of_waiting_too_long():
    clock = FakeClock(t=1_000_000.0)  # start of a minute: a full minute to wait
    b = CallBudget(600, 5000, 10000, clock=clock, sleep=clock.sleep)
    b.exhaust("minute")
    with pytest.raises(UpstreamBusy):
        await b.acquire(10, max_wait=5)
