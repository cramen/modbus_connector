import pytest

from modbus_connector.models import (
    Stats,
    format_register_values,
    format_scaled_values,
    format_values,
    parse_values,
)


class TestParseRegisters:
    def test_decimal_numbers(self) -> None:
        assert parse_values("holding_registers", "1 2 300") == [1, 2, 300]

    def test_comma_separated(self) -> None:
        assert parse_values("holding_registers", "10, 20,30") == [10, 20, 30]

    def test_hex_numbers(self) -> None:
        assert parse_values("holding_registers", "0x10 0xff") == [16, 255]

    def test_mixed_dec_hex(self) -> None:
        assert parse_values("input_registers", "5 0xA") == [5, 10]

    @pytest.mark.parametrize("text", ["", "abc", "1 x 3", "1.5", "0x"])
    def test_garbage_raises(self, text: str) -> None:
        with pytest.raises(ValueError):
            parse_values("holding_registers", text)


class TestParseCoils:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1 0", [True, False]),
            ("true false", [True, False]),
            ("TRUE False", [True, False]),
            ("on off", [True, False]),
            ("1, true, 0", [True, True, False]),
        ],
    )
    def test_coil_variants(self, text: str, expected: list[bool]) -> None:
        assert parse_values("coils", text) == expected

    @pytest.mark.parametrize("text", ["", "2", "yes", "abc", "1 x"])
    def test_garbage_raises(self, text: str) -> None:
        with pytest.raises(ValueError):
            parse_values("coils", text)


class TestFormatValues:
    def test_registers(self) -> None:
        text = format_values([1, 2, 300])
        assert parse_values("holding_registers", text) == [1, 2, 300]

    def test_coils(self) -> None:
        text = format_values([True, False, True])
        assert parse_values("coils", text) == [True, False, True]

    def test_empty(self) -> None:
        assert format_values([]) == ""

    def test_roundtrip_hex_friendly(self) -> None:
        values = [0, 255, 65535]
        assert parse_values("holding_registers", format_values(values)) == values


class TestFormatRegisterValues:
    def test_dec(self) -> None:
        assert format_register_values([1, 2, 65535], "dec") == "1, 2, 65535"

    def test_hex(self) -> None:
        assert format_register_values([0, 26, 65535], "hex") == "0x0000, 0x001A, 0xFFFF"

    def test_s16(self) -> None:
        assert format_register_values([1, 65535, 32768], "s16") == "1, -1, -32768"

    def test_u32_pairs_big_endian(self) -> None:
        assert format_register_values([0x0001, 0x0000], "u32") == "65536"
        assert format_register_values([0x0000, 0x0001], "u32") == "1"

    def test_u32_odd_trailing_register(self) -> None:
        assert format_register_values([0x0001, 0x0000, 7], "u32") == "65536, 7"

    def test_s32(self) -> None:
        assert format_register_values([0xFFFF, 0xFFFF], "s32") == "-1"
        assert format_register_values([0x0000, 0x0001], "s32") == "1"
        assert format_register_values([0x8000, 0x0000], "s32") == "-2147483648"

    def test_s32_odd_trailing_register(self) -> None:
        assert format_register_values([0xFFFF, 0xFFFF, 7], "s32") == "-1, 7"

    def test_f32(self) -> None:
        assert format_register_values([0x3F80, 0x0000], "f32") == "1"
        assert format_register_values([0x3F00, 0x0000], "f32") == "0.5"
        assert format_register_values([0xC020, 0x0000], "f32") == "-2.5"

    def test_f32_multiple_pairs(self) -> None:
        assert format_register_values([0x3F80, 0x0000, 0xC020, 0x0000], "f32") == "1, -2.5"

    def test_f32_odd_trailing_register(self) -> None:
        assert format_register_values([0x3F80, 0x0000, 7], "f32") == "1, 7"

    def test_empty(self) -> None:
        for fmt in (
            "dec", "hex", "s16", "u32", "s32", "f32", "u64", "s64", "f64", "ascii"
        ):
            assert format_register_values([], fmt) == ""


class TestAsciiFormat:
    def test_hello_with_nul_terminator(self) -> None:
        assert format_register_values([0x4865, 0x6C6C, 0x6F00], "ascii") == "Hello"

    def test_nul_terminates_mid_register(self) -> None:
        assert format_register_values([0x4100, 0x4243], "ascii") == "A"

    def test_non_printable_replaced_with_dot(self) -> None:
        assert format_register_values([0x4101, 0x7F42], "ascii") == "A..B"

    def test_no_nul_reads_all_registers(self) -> None:
        assert format_register_values([0x4142, 0x4344], "ascii") == "ABCD"

    def test_order_does_not_apply(self) -> None:
        values = [0x4865, 0x6C6C, 0x6F00]
        assert format_register_values(values, "ascii", "DCBA") == "Hello"


class Test64BitFormats:
    # u64 0x0102030405060708 = 72623859790382856
    def test_u64_orders(self) -> None:
        expected = "72623859790382856"
        assert format_register_values([0x0102, 0x0304, 0x0506, 0x0708], "u64") == expected
        assert (
            format_register_values([0x0708, 0x0506, 0x0304, 0x0102], "u64", "CDAB")
            == expected
        )
        assert (
            format_register_values([0x0201, 0x0403, 0x0605, 0x0807], "u64", "BADC")
            == expected
        )
        assert (
            format_register_values([0x0807, 0x0605, 0x0403, 0x0201], "u64", "DCBA")
            == expected
        )

    def test_s64(self) -> None:
        assert format_register_values([0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF], "s64") == "-1"
        assert format_register_values([0xFFFF, 0xFFFF, 0xFFFF, 0xFFFE], "s64") == "-2"
        assert format_register_values([0x0000, 0x0000, 0x0000, 0x0001], "s64") == "1"

    def test_f64(self) -> None:
        assert format_register_values([0x3FF0, 0, 0, 0], "f64") == "1"
        assert format_register_values([0x3FE0, 0, 0, 0], "f64") == "0.5"
        assert format_register_values([0xC004, 0, 0, 0], "f64") == "-2.5"

    def test_f64_with_order(self) -> None:
        assert format_register_values([0, 0, 0, 0x3FF0], "f64", "CDAB") == "1"
        assert format_register_values([0, 0, 0, 0xF03F], "f64", "DCBA") == "1"

    def test_trailing_registers_as_decimal(self) -> None:
        assert format_register_values([0x3FF0, 0, 0, 0, 7], "f64") == "1, 7"
        assert format_register_values([0x3FF0, 0, 0], "f64") == "16368, 0, 0"
        assert format_register_values([0x0102, 0x0304, 0x0506, 0x0708, 9], "u64").endswith(
            ", 9"
        )


class TestByteOrder:
    # f32 1.0 = 0x3F800000; u32 equivalent = 1065353216
    def test_abcd_is_canonical(self) -> None:
        assert format_register_values([0x3F80, 0x0000], "f32", "ABCD") == "1"
        assert format_register_values([0x3F80, 0x0000], "u32", "ABCD") == "1065353216"

    def test_cdab_word_swapped(self) -> None:
        assert format_register_values([0x0000, 0x3F80], "f32", "CDAB") == "1"
        assert format_register_values([0x0000, 0x3F80], "u32", "CDAB") == "1065353216"

    def test_badc_byte_swapped(self) -> None:
        assert format_register_values([0x803F, 0x0000], "f32", "BADC") == "1"
        assert format_register_values([0x803F, 0x0000], "u32", "BADC") == "1065353216"

    def test_dcba_full_reverse(self) -> None:
        assert format_register_values([0x0000, 0x803F], "f32", "DCBA") == "1"
        assert format_register_values([0x0000, 0x803F], "u32", "DCBA") == "1065353216"

    def test_s32_with_order(self) -> None:
        assert format_register_values([0xFFFF, 0xFFFF], "s32", "CDAB") == "-1"

    def test_order_ignored_by_16_bit_formats(self) -> None:
        values = [0x3F80, 0x0000]
        for fmt in ("dec", "hex", "s16"):
            assert format_register_values(values, fmt, "DCBA") == format_register_values(
                values, fmt, "ABCD"
            )

    def test_default_order_unchanged(self) -> None:
        assert format_register_values([0x3F80, 0x0000], "f32") == "1"


class TestFormatScaledValues:
    def test_default_passthrough(self) -> None:
        assert format_scaled_values([1, 2, 300], 1.0, 0.0, "") == "1, 2, 300"

    def test_scale_and_offset(self) -> None:
        assert format_scaled_values([1, 2], 0.1, 5.0, "") == "5.1, 5.2"

    def test_compact_float_formatting(self) -> None:
        assert format_scaled_values([100], 0.1, 0.0, "") == "10"
        assert format_scaled_values([1], 1.0 / 3.0, 0.0, "") == "0.3333"

    def test_unit_appended(self) -> None:
        assert format_scaled_values([20, 21], 1.0, 0.0, "°C") == "20, 21 °C"

    def test_unit_with_scaling(self) -> None:
        assert format_scaled_values([250], 0.1, -40.0, "V") == "-15 V"

    def test_empty(self) -> None:
        assert format_scaled_values([], 2.0, 1.0, "V") == ""


class TestStats:
    def test_empty_snapshot_has_no_division_by_zero(self) -> None:
        snapshot = Stats().snapshot()
        assert snapshot.total == 0
        assert snapshot.errors == 0
        assert snapshot.error_percent == 0.0
        assert snapshot.avg_ms == 0.0
        assert snapshot.last_ms == 0.0

    def test_counts_and_error_percent(self) -> None:
        stats = Stats()
        stats.record(True, 10.0)
        stats.record(False, 20.0)
        stats.record(True, 30.0)
        stats.record(True, 20.0)
        snapshot = stats.snapshot()
        assert snapshot.total == 4
        assert snapshot.errors == 1
        assert snapshot.error_percent == 25.0

    def test_average_covers_successful_ops_only(self) -> None:
        stats = Stats()
        stats.record(True, 10.0)
        stats.record(True, 30.0)
        stats.record(False, 999.0)
        snapshot = stats.snapshot()
        assert snapshot.avg_ms == 20.0
        assert snapshot.last_ms == 999.0

    def test_all_errors_gives_zero_average(self) -> None:
        stats = Stats()
        stats.record(False, 5.0)
        snapshot = stats.snapshot()
        assert snapshot.error_percent == 100.0
        assert snapshot.avg_ms == 0.0

    def test_reset(self) -> None:
        stats = Stats()
        stats.record(True, 10.0)
        stats.record(False, 20.0)
        stats.reset()
        assert stats.snapshot() == Stats().snapshot()
