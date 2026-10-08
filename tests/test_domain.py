import itertools

import pytest

from app.domain import (
    INSTALLMENT_MONTHS,
    TARIFFS,
    UnknownPromoCodeError,
    build_schedule,
    calc_discount,
    can_transition,
)
from app.enums import PaymentStatus

PRICES = [price for _, _, price in TARIFFS]


def test_tariffs_prices_are_in_kopecks():
    assert {tariff_id: price for tariff_id, _, price in TARIFFS} == {
        "basic": 990_000,
        "standard": 1_990_000,
        "premium": 2_990_000,
    }


# --- промокод ---


@pytest.mark.parametrize("price", PRICES)
def test_no_promo_means_zero_discount(price):
    assert calc_discount(price, None) == 0


@pytest.mark.parametrize("code", ["KVITTO10", "kvitto10", "KvItTo10"])
def test_promo_is_case_insensitive(code):
    assert calc_discount(1_990_000, code) == 199_000


@pytest.mark.parametrize(
    ("price", "discount"),
    [(990_000, 99_000), (1_990_000, 199_000), (2_990_000, 299_000)],
)
def test_promo_gives_ten_percent_for_each_tariff(price, discount):
    assert calc_discount(price, "KVITTO10") == discount
    assert price - discount == price * 9 // 10  # amount + discount == price


def test_discount_is_int_and_rounds_down():
    discount = calc_discount(9_999, "KVITTO10")
    assert isinstance(discount, int)
    assert discount == 999  # 999.9 -> вниз


@pytest.mark.parametrize(
    "code", ["", " ", " KVITTO10", "KVITTO10 ", "KVITTO", "KVITTO100", "OTHER"]
)
def test_unknown_promo_raises(code):
    with pytest.raises(UnknownPromoCodeError):
        calc_discount(1_990_000, code)


# --- график рассрочки ---


def test_schedule_example_from_spec():
    assert build_schedule(1_990_000, 3) == [663_334, 663_333, 663_333]


@pytest.mark.parametrize(
    ("amount", "months", "expected"),
    [
        (2_990_000, 3, [996_667, 996_667, 996_666]),  # остаток 2
        (1_990_000, 6, [331_667] * 4 + [331_666] * 2),  # остаток 4
        (2_990_000, 12, [249_167] * 8 + [249_166] * 4),  # остаток 8
        (891_000, 12, [74_250] * 12),  # делится нацело
        (1_791_000, 3, [597_000] * 3),  # standard со скидкой, делится нацело
    ],
)
def test_schedule_exact_values(amount, months, expected):
    assert build_schedule(amount, months) == expected


@pytest.mark.parametrize("promo", [None, "KVITTO10"])
@pytest.mark.parametrize("months", INSTALLMENT_MONTHS)
@pytest.mark.parametrize("price", PRICES)
def test_schedule_invariants(price, months, promo):
    amount = price - calc_discount(price, promo)
    schedule = build_schedule(amount, months)

    assert sum(schedule) == amount
    assert len(schedule) == months
    assert all(isinstance(payment, int) for payment in schedule)
    assert max(schedule) - min(schedule) <= 1
    assert schedule == sorted(schedule, reverse=True)  # лишние копейки в первых платежах


@pytest.mark.parametrize("amount", [1, 2, 5, 11, 12, 13])
@pytest.mark.parametrize("months", INSTALLMENT_MONTHS)
def test_schedule_small_amounts_still_sum_up(amount, months):
    schedule = build_schedule(amount, months)

    assert sum(schedule) == amount
    assert len(schedule) == months
    assert min(schedule) >= 0


@pytest.mark.parametrize("months", [0, 1, 2, 4, 24, -3])
def test_schedule_rejects_unsupported_months(months):
    with pytest.raises(ValueError):
        build_schedule(1_990_000, months)


# --- переходы статусов ---

ALLOWED = {
    (PaymentStatus.PENDING, PaymentStatus.SUCCEEDED),
    (PaymentStatus.PENDING, PaymentStatus.FAILED),
    (PaymentStatus.SUCCEEDED, PaymentStatus.REFUNDED),
}
ALL_PAIRS = list(itertools.product(PaymentStatus, repeat=2))


@pytest.mark.parametrize(("current", "new"), ALL_PAIRS)
def test_transition_table_is_exactly_as_in_spec(current, new):
    assert can_transition(current, new) is ((current, new) in ALLOWED)
