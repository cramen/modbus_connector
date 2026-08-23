import math

import pytest

from modbus_connector.models import (
    AlarmRule,
    ByteOrder,
    DisplayFormat,
    ReadMember,
    ReadPlan,
    ReadSpec,
    RegisterRow,
    RowDisplaySettings,
    Stats,
    alarm_rule_from_json,
    alarm_rule_to_json,
    alarm_rules_from_json,
    csv_header,
    decode_register_values,
    describe_exception,
    diff_snapshots,
    encode_ascii_values,
    encode_register_values,
    evaluate_alarm,
    format_named_value,
    format_register_values,
    format_scaled_values,
    format_values,
    guess_column_mapping,
    parse_expression,
    parse_formatted_values,
    parse_value_names,
    parse_values,
    plan_grouped_reads,
    rows_from_csv,
    rows_to_csv,
    rule_matches,
    value_names_from_json,
    value_names_to_json,
    value_names_to_text,
)


def _spec(token: int, address: int, count: int = 1, kind: str = "holding_registers",
          unit: int | None = 1) -> ReadSpec:
    return ReadSpec(token=token, unit=unit, kind=kind, address=address, count=count)


class TestPlanGroupedReads:
    def test_adjacent_rows_merge_into_one_plan(self) -> None:
        plans = plan_grouped_reads([_spec(1, 0), _spec(2, 1, 2), _spec(3, 3)])
        assert plans == [
            ReadPlan(
                unit=1,
                kind="holding_registers",
                address=0,
                count=4,
                members=[
                    ReadMember(token=1, offset=0, count=1),
                    ReadMember(token=2, offset=1, count=2),
                    ReadMember(token=3, offset=3, count=1),
                ],
            )
        ]

    def test_gap_within_max_merges(self) -> None:
        plans = plan_grouped_reads([_spec(1, 0), _spec(2, 9)])  # gap 8 ≤ 8
        assert len(plans) == 1
        assert plans[0].address == 0
        assert plans[0].count == 10
        assert plans[0].members[1] == ReadMember(token=2, offset=9, count=1)

    def test_gap_beyond_max_splits(self) -> None:
        plans = plan_grouped_reads([_spec(1, 0), _spec(2, 10)])  # gap 9 > 8
        assert [plan.address for plan in plans] == [0, 10]
        assert all(len(plan.members) == 1 for plan in plans)

    def test_custom_max_gap(self) -> None:
        rows = [_spec(1, 0), _spec(2, 2)]  # gap 1
        assert len(plan_grouped_reads(rows, max_gap=0)) == 2
        assert len(plan_grouped_reads(rows, max_gap=-3)) == 2  # clamped to 0
        assert len(plan_grouped_reads(rows, max_gap=1)) == 1

    def test_different_units_and_kinds_do_not_merge(self) -> None:
        plans = plan_grouped_reads([
            _spec(1, 0, unit=1),
            _spec(2, 1, unit=2),
            _spec(3, 2, kind="input_registers"),
        ])
        assert len(plans) == 3
        assert {(plan.unit, plan.kind) for plan in plans} == {
            (1, "holding_registers"),
            (2, "holding_registers"),
            (1, "input_registers"),
        }

    def test_overlapping_ranges_merge(self) -> None:
        plans = plan_grouped_reads([_spec(1, 0, 4), _spec(2, 2, 4)])
        assert plans == [
            ReadPlan(
                unit=1,
                kind="holding_registers",
                address=0,
                count=6,
                members=[
                    ReadMember(token=1, offset=0, count=4),
                    ReadMember(token=2, offset=2, count=4),
                ],
            )
        ]

    def test_plan_length_capped_at_125_registers(self) -> None:
        rows = [_spec(1, 0, 100), _spec(2, 100, 100)]  # merged would be 200
        plans = plan_grouped_reads(rows)
        assert [plan.count for plan in plans] == [100, 100]
        # a 100+20 merge fits the cap
        plans = plan_grouped_reads([_spec(1, 0, 100), _spec(2, 100, 20)])
        assert len(plans) == 1
        assert plans[0].count == 120

    def test_bit_areas_capped_at_2000(self) -> None:
        rows = [_spec(1, 0, 1500, kind="coils"), _spec(2, 1500, 1000, kind="coils")]
        plans = plan_grouped_reads(rows)
        assert [plan.count for plan in plans] == [1500, 1000]

    def test_unsorted_input_sorted_inside_group(self) -> None:
        plans = plan_grouped_reads([_spec(7, 5), _spec(3, 0), _spec(9, 1)])
        assert len(plans) == 1
        assert [member.token for member in plans[0].members] == [3, 9, 7]
        assert [member.offset for member in plans[0].members] == [0, 1, 5]

    def test_invalid_rows_skipped(self) -> None:
        plans = plan_grouped_reads([
            _spec(1, 0, 0),
            _spec(2, 0, -5),
            _spec(3, -1),
            _spec(4, 7),
        ])
        assert plans == [
            ReadPlan(
                unit=1, kind="holding_registers", address=7, count=1,
                members=[ReadMember(token=4, offset=0, count=1)],
            )
        ]

    def test_empty_input(self) -> None:
        assert plan_grouped_reads([]) == []


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


class TestDecodeComposeWithScaling:
    def test_f32_scaled_with_unit(self) -> None:
        decoded = decode_register_values([0x3F80, 0x0000], "f32")
        assert decoded == [1.0]
        assert format_scaled_values(decoded, 0.1, -40.0, "°C") == "-39.9 °C"

    def test_order_applies_before_scaling(self) -> None:
        decoded = decode_register_values([0x0000, 0x3F80], "f32", "CDAB")
        assert format_scaled_values(decoded, 0.1, -40.0, "°C") == "-39.9 °C"

    def test_s16_negative_scaled(self) -> None:
        decoded = decode_register_values([0xFFFF], "s16")
        assert decoded == [-1]
        assert format_scaled_values(decoded, 0.5, 0.0, "") == "-0.5"

    def test_u32_with_order_and_scale(self) -> None:
        decoded = decode_register_values([0x0000, 0x3F80], "u32", "CDAB")
        assert decoded == [1065353216]
        assert format_scaled_values(decoded, 1e-9, 0.0, "G") == "1.065 G"

    def test_dec_decode_is_identity(self) -> None:
        assert decode_register_values([1, 2, 300], "dec") == [1, 2, 300]

    def test_u64_decode(self) -> None:
        assert decode_register_values([0x0102, 0x0304, 0x0506, 0x0708], "u64") == [
            0x0102030405060708
        ]


class TestEncodeRegisterValues:
    def test_dec_round_and_clamp(self) -> None:
        assert encode_register_values(42.4, "dec") == [42]
        assert encode_register_values(70000, "dec") == [0xFFFF]
        assert encode_register_values(-5, "dec") == [0]

    def test_s16_clamp_and_sign(self) -> None:
        assert encode_register_values(-1, "s16") == [0xFFFF]
        assert encode_register_values(40000, "s16") == [0x7FFF]
        assert encode_register_values(-40000, "s16") == [0x8000]

    def test_string_formats_rejected(self) -> None:
        for fmt in ("hex", "ascii"):
            with pytest.raises(ValueError):
                encode_register_values(1.0, fmt)  # type: ignore[arg-type]

    @pytest.mark.parametrize("order", ["ABCD", "CDAB", "BADC", "DCBA"])
    @pytest.mark.parametrize(
        "fmt, value",
        [
            ("u32", 0x01020304),
            ("s32", -123456),
            ("f32", 1.5),
            ("f32", -2.25),
            ("u64", 0x0102030405060708),
            ("s64", -1234567890123),
            ("f64", 3.141592653589793),
        ],
    )
    def test_roundtrip_decode(self, fmt: DisplayFormat, value: float, order: ByteOrder) -> None:
        registers = encode_register_values(value, fmt, order)
        decoded = decode_register_values(registers, fmt, order)
        assert len(registers) == (4 if fmt in ("u64", "s64", "f64") else 2)
        assert decoded[0] == pytest.approx(value)

    def test_f32_overflow_raises(self) -> None:
        with pytest.raises(OverflowError):
            encode_register_values(1e300, "f32")


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


class TestCsv:
    def test_roundtrip_preserves_all_fields(self) -> None:
        rows = [
            RegisterRow(
                name="temp", kind="holding_registers", address=5, count=2,
                format="f32", unit_id=3, poll_ms=5000,
            ),
            RegisterRow(name="bits, with comma", kind="coils", address=0),
        ]
        displays = [
            RowDisplaySettings(scale=0.1, offset=-40.0, unit="°C", order="CDAB"),
            RowDisplaySettings(),
        ]
        parsed = rows_from_csv(rows_to_csv(rows, displays))
        assert parsed == list(zip(rows, displays, strict=True))

    def test_missing_optional_and_unknown_columns(self) -> None:
        text = "name,kind,address,comment\ntemp,holding_registers,5,hello\n"
        parsed = rows_from_csv(text)
        row, display = parsed[0]
        assert row.address == 5
        assert row.count == 1
        assert row.unit_id is None
        assert row.poll_ms is None
        assert display.scale == 1.0
        assert display.offset == 0.0
        assert display.unit == ""
        assert display.order is None

    def test_kind_fallback_and_hex_address(self) -> None:
        parsed = rows_from_csv("name,kind,address\nx,bogus,0x10\n")
        row, _ = parsed[0]
        assert row.kind == "holding_registers"
        assert row.address == 16

    def test_bad_rows_skipped(self) -> None:
        text = "name,kind,address\ngood,coils,3\nbad,coils,abc\n"
        parsed = rows_from_csv(text)
        assert len(parsed) == 1
        assert parsed[0][0].name == "good"

    def test_all_rows_bad_raises(self) -> None:
        with pytest.raises(ValueError):
            rows_from_csv("name,kind,address\nbad,coils,abc\n")

    def test_header_missing_address_raises(self) -> None:
        with pytest.raises(ValueError):
            rows_from_csv("name,kind,count\ntemp,coils,1\n")

    def test_export_column_subset_and_order(self) -> None:
        rows = [RegisterRow(name="t", kind="coils", address=5, count=3)]
        displays = [RowDisplaySettings(scale=2.0)]
        text = rows_to_csv(rows, displays, ["address", "name", "scale"])
        header, line = text.strip().split("\n")
        assert header == "address,name,scale"
        assert line == "5,t,2.0"

    def test_guess_column_mapping(self) -> None:
        assert guess_column_mapping(["Name", "type", "value", "wat", " scale "]) == {
            "Name": "name",
            "type": "kind",
            " scale ": "scale",
        }

    def test_explicit_mapping(self) -> None:
        text = "Register Name,type,addr,ignore me\nx,coils,5,junk\n"
        mapping = {"Register Name": "name", "type": "kind", "addr": "address"}
        parsed = rows_from_csv(text, mapping)
        row, _ = parsed[0]
        assert row.name == "x"
        assert row.kind == "coils"
        assert row.address == 5

    def test_mapping_missing_essential_raises(self) -> None:
        with pytest.raises(ValueError):
            rows_from_csv("name,kind,address\nx,coils,5\n", {"name": "name"})

    def test_csv_header(self) -> None:
        assert csv_header("a,b,c\n1,2,3\n") == ["a", "b", "c"]
        assert csv_header("") == []


class TestAlarmRuleValidation:
    def test_range_bounds_normalized(self) -> None:
        rule = AlarmRule("in_range", 10.0, 2.0)
        assert rule.value == 2.0
        assert rule.value2 == 10.0

    def test_range_requires_value2(self) -> None:
        with pytest.raises(ValueError):
            AlarmRule("in_range", 5.0)

    def test_scalar_rejects_value2(self) -> None:
        with pytest.raises(ValueError):
            AlarmRule("gt", 5.0, 6.0)

    def test_unknown_condition_and_color(self) -> None:
        with pytest.raises(ValueError):
            AlarmRule("bogus", 5.0)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            AlarmRule("gt", 5.0, color="blue")  # type: ignore[arg-type]


class TestRuleMatches:
    @pytest.mark.parametrize(
        ("rule", "matching", "not_matching"),
        [
            (AlarmRule("gt", 5.0), [5.1, 100.0], [5.0, 4.9]),
            (AlarmRule("ge", 5.0), [5.0, 5.1], [4.9]),
            (AlarmRule("lt", 5.0), [4.9, -100.0], [5.0, 5.1]),
            (AlarmRule("le", 5.0), [5.0, 4.9], [5.1]),
            (AlarmRule("eq", 5.0), [5.0], [5.0001, 4.9]),
            (AlarmRule("ne", 5.0), [5.1, 0.0], [5.0]),
        ],
    )
    def test_scalar_conditions(
        self, rule: AlarmRule, matching: list[float], not_matching: list[float]
    ) -> None:
        for x in matching:
            assert rule_matches(rule, x), x
        for x in not_matching:
            assert not rule_matches(rule, x), x

    def test_in_range_bounds_inclusive(self) -> None:
        rule = AlarmRule("in_range", 2.0, 10.0)
        assert rule_matches(rule, 2.0)
        assert rule_matches(rule, 10.0)
        assert rule_matches(rule, 5.0)
        assert not rule_matches(rule, 1.999)
        assert not rule_matches(rule, 10.001)

    def test_outside_range_bounds_inclusive(self) -> None:
        rule = AlarmRule("outside_range", 2.0, 10.0)
        assert not rule_matches(rule, 2.0)
        assert not rule_matches(rule, 10.0)
        assert not rule_matches(rule, 5.0)
        assert rule_matches(rule, 1.999)
        assert rule_matches(rule, 10.001)


class TestEvaluateAlarm:
    def test_first_match_wins(self) -> None:
        first = AlarmRule("ge", 5.0, color="yellow")
        second = AlarmRule("gt", 3.0, color="red")
        assert evaluate_alarm(10.0, [first, second]) is first

    def test_skips_non_matching(self) -> None:
        rule = AlarmRule("gt", 3.0, color="red")
        assert evaluate_alarm(10.0, [AlarmRule("lt", 5.0), rule]) is rule

    def test_no_match_returns_none(self) -> None:
        assert evaluate_alarm(5.0, [AlarmRule("gt", 10.0)]) is None
        assert evaluate_alarm(5.0, []) is None


class TestAlarmRuleJson:
    def test_roundtrip_scalar(self) -> None:
        rule = AlarmRule("gt", 5.0, color="red", log=True, sound=False)
        data = alarm_rule_to_json(rule)
        assert data == {
            "condition": "gt", "value": 5.0, "color": "red", "log": True, "sound": False,
        }
        assert "value2" not in data
        assert alarm_rule_from_json(data) == rule

    def test_roundtrip_range(self) -> None:
        rule = AlarmRule("outside_range", 2.0, 10.0, color="yellow", log=False, sound=True)
        data = alarm_rule_to_json(rule)
        assert data["value2"] == 10.0
        assert alarm_rule_from_json(data) == rule

    def test_int_value_coerced_to_float(self) -> None:
        rule = alarm_rule_from_json({"condition": "ge", "value": 5})
        assert rule is not None
        assert rule.value == 5.0

    def test_missing_color_defaults_to_red(self) -> None:
        rule = alarm_rule_from_json({"condition": "gt", "value": 5.0})
        assert rule is not None
        assert rule.color == "red"
        assert rule.log is True
        assert rule.sound is False

    @pytest.mark.parametrize(
        "data",
        [
            None,
            "gt",
            [1, 2],
            {},
            {"condition": "bogus", "value": 5.0},
            {"condition": "gt"},
            {"condition": "gt", "value": "high"},
            {"condition": "gt", "value": None},
            {"condition": "gt", "value": True},
            {"condition": "gt", "value": 5.0, "color": "blue"},
            {"condition": "in_range", "value": 5.0},
            {"condition": "in_range", "value": 5.0, "value2": "x"},
            {"condition": "gt", "value": 5.0, "value2": 6.0},
        ],
    )
    def test_garbage_returns_none(self, data: object) -> None:
        assert alarm_rule_from_json(data) is None

    def test_rules_from_json_skips_broken(self) -> None:
        good = alarm_rule_to_json(AlarmRule("gt", 5.0))
        rules = alarm_rules_from_json([good, {"condition": "bogus"}, "junk", None])
        assert rules == [AlarmRule("gt", 5.0)]

    def test_rules_from_json_non_list(self) -> None:
        assert alarm_rules_from_json(None) == []
        assert alarm_rules_from_json({"condition": "gt", "value": 5.0}) == []


class TestDescribeException:
    def test_known_codes(self) -> None:
        assert describe_exception(0x01) == "Illegal Function (0x01)"
        assert describe_exception(0x02) == "Illegal Data Address (0x02)"
        assert describe_exception(0x0B) == "Gateway Target Device Failed to Respond (0x0B)"

    def test_unknown_code(self) -> None:
        assert describe_exception(0x63) == "Exception 0x63"


class TestStatsErrorKinds:
    def test_kinds_counted(self) -> None:
        stats = Stats()
        stats.record(False, 1.0, "timeout")
        stats.record(False, 1.0, "timeout")
        stats.record(False, 1.0, "exception:Illegal Data Address (0x02)")
        stats.record(False, 1.0)  # no kind -> other
        snapshot = stats.snapshot()
        assert snapshot.error_kinds == {
            "timeout": 2,
            "exception:Illegal Data Address (0x02)": 1,
            "other": 1,
        }
        assert snapshot.top_error_kind == "timeout"

    def test_default_snapshot_has_empty_breakdown(self) -> None:
        snapshot = Stats().snapshot()
        assert snapshot.error_kinds == {}
        assert snapshot.top_error_kind is None

    def test_reset_clears_kinds(self) -> None:
        stats = Stats()
        stats.record(False, 1.0, "timeout")
        stats.reset()
        assert stats.snapshot().error_kinds == {}


class TestDiffSnapshots:
    def test_equal_raw_values(self) -> None:
        assert not diff_snapshots([1, 2], [1, 2])
        assert not diff_snapshots([True], [True])

    def test_different_raw_values(self) -> None:
        assert diff_snapshots([1, 2], [1, 3])
        assert diff_snapshots([1], [1, 2])
        assert diff_snapshots([True], [False])

    def test_none_means_no_data(self) -> None:
        assert not diff_snapshots(None, None)
        assert diff_snapshots(None, [1])
        assert diff_snapshots([1], None)


class TestExpressionArithmetic:
    def test_basic_ops_and_precedence(self) -> None:
        assert parse_expression("2 + 3 * 4").evaluate({}) == 14.0
        assert parse_expression("(2 + 3) * 4").evaluate({}) == 20.0
        assert parse_expression("10 / 4").evaluate({}) == 2.5
        assert parse_expression("10 // 4").evaluate({}) == 2.0
        assert parse_expression("10 % 4").evaluate({}) == 2.0
        assert parse_expression("2 ** 3 ** 2").evaluate({}) == 512.0

    def test_unary_ops(self) -> None:
        assert parse_expression("-5").evaluate({}) == -5.0
        assert parse_expression("+5").evaluate({}) == 5.0
        assert parse_expression("-2 ** 2").evaluate({}) == -4.0
        assert parse_expression("-[a]").evaluate({"a": 3}) == -3.0

    def test_number_literals(self) -> None:
        assert parse_expression("1e3").evaluate({}) == 1000.0
        assert parse_expression("2.5 + 1").evaluate({}) == 3.5
        assert parse_expression("0x10").evaluate({}) == 16.0

    def test_refs_and_int_values_coerced(self) -> None:
        expr = parse_expression("[a] * 2 + [b]")
        assert expr.evaluate({"a": 3, "b": 0.5}) == 6.5


class TestExpressionFunctions:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("abs(-3)", 3.0),
            ("sqrt(9)", 3.0),
            ("exp(0)", 1.0),
            ("log(e)", 1.0),
            ("log2(8)", 3.0),
            ("log10(1000)", 3.0),
            ("sin(0)", 0.0),
            ("cos(0)", 1.0),
            ("tan(0)", 0.0),
            ("asin(1)", math.pi / 2),
            ("acos(1)", 0.0),
            ("atan(1)", math.pi / 4),
            ("floor(2.7)", 2.0),
            ("ceil(2.1)", 3.0),
            ("round(2.5)", 2.0),
            ("min(3, 1, 2)", 1.0),
            ("max(3, 1, 2)", 3.0),
            ("pow(2, 10)", 1024.0),
            ("clamp(5, 0, 3)", 3.0),
            ("clamp(-5, 0, 3)", 0.0),
            ("clamp(2, 0, 3)", 2.0),
        ],
    )
    def test_functions(self, text: str, expected: float) -> None:
        assert parse_expression(text).evaluate({}) == pytest.approx(expected)

    def test_constants(self) -> None:
        assert parse_expression("pi").evaluate({}) == pytest.approx(math.pi)
        assert parse_expression("e").evaluate({}) == pytest.approx(math.e)
        assert parse_expression("2 * pi * [r]").evaluate({"r": 1}) == pytest.approx(
            2 * math.pi
        )

    def test_nested_calls(self) -> None:
        assert parse_expression("sqrt(abs(-16))").evaluate({}) == 4.0
        assert parse_expression("max([a], min([b], 10))").evaluate({"a": 5, "b": 20}) == 10.0


class TestExpressionDeps:
    def test_deps_extracted(self) -> None:
        expr = parse_expression("[temperature] + [pressure] * 2")
        assert expr.deps == frozenset({"temperature", "pressure"})
        assert expr.text == "[temperature] + [pressure] * 2"

    def test_names_with_spaces_and_unicode(self) -> None:
        expr = parse_expression("[flow rate] / [расход жидкости]")
        assert expr.deps == frozenset({"flow rate", "расход жидкости"})
        assert expr.evaluate({"flow rate": 10.0, "расход жидкости": 4.0}) == 2.5

    def test_repeated_ref_counts_once(self) -> None:
        expr = parse_expression("[a] + [a]")
        assert expr.deps == frozenset({"a"})
        assert expr.evaluate({"a": 2}) == 4.0

    def test_no_deps(self) -> None:
        assert parse_expression("1 + 2").deps == frozenset()

    def test_missing_dependency_raises_key_error(self) -> None:
        expr = parse_expression("[a] + [b]")
        with pytest.raises(KeyError):
            expr.evaluate({"a": 1})
        with pytest.raises(KeyError):
            expr.evaluate({})


class TestExpressionMathErrors:
    @pytest.mark.parametrize(
        "text",
        ["1 / 0", "1 // 0", "1 % 0", "sqrt(-1)", "log(0)", "log(-1)", "log2(-1)",
         "log10(0)", "exp(1000)", "asin(2)", "acos(-2)", "clamp(1, 2)", "0 ** -1"],
    )
    def test_math_errors_give_nan(self, text: str) -> None:
        result = parse_expression(text).evaluate({})
        assert math.isnan(result), text

    def test_nan_propagates_through_refs(self) -> None:
        expr = parse_expression("[a] / [b]")
        assert math.isnan(expr.evaluate({"a": 1, "b": 0}))


class TestExpressionParseErrors:
    @pytest.mark.parametrize(
        "text",
        [
            "",  # пустое
            "1 +",  # синтаксический мусор
            "1 2",
            "[abc",  # незакрытая [
            "[]",  # пустая ссылка
            "[   ]",
            "foo(1)",  # неизвестная функция
            "abc",  # неизвестное имя
            "[a](1)",  # вызов ссылки как функции
            "[a].__class__",  # атрибут
            "[a][0]",  # подзапрос
            "().__class__",
            "__import__('os')",  # инъекция: неизвестная функция + строковый литерал
            "lambda x: x",
            "a = 1",  # присваивание — не выражение
            "min(x=1)",  # именованные аргументы
            "'string'",  # строковый литерал
            "True",  # bool-литерал
            "[a] if [b] else [c]",  # условный оператор не нужен
            "[a] == [b]",  # сравнения не нужны
            "max(*[a])",  # starred args
        ],
    )
    def test_rejected(self, text: str) -> None:
        with pytest.raises(ValueError):
            parse_expression(text)

    def test_error_messages_are_readable(self) -> None:
        with pytest.raises(ValueError, match="Незакрытая скобка"):
            parse_expression("[abc")
        with pytest.raises(ValueError, match="Пустая ссылка"):
            parse_expression("[]")
        with pytest.raises(ValueError, match="Неизвестная функция 'foo'"):
            parse_expression("foo(1)")
        with pytest.raises(ValueError, match="Неизвестное имя 'abc'"):
            parse_expression("abc")
        with pytest.raises(ValueError, match="не является функцией"):
            parse_expression("[a](1)")

    def test_injection_rejected_without_side_effects(self) -> None:
        with pytest.raises(ValueError):
            parse_expression("__import__('os').system('echo pwned')")
        with pytest.raises(ValueError):
            parse_expression("(1).__class__.__bases__")

    def test_typed_placeholder_name_rejected(self) -> None:
        # плейсхолдер, подставленный за [a], нельзя «переиспользовать» руками
        with pytest.raises(ValueError):
            parse_expression("[a] + __ref_0")


class TestExpressionExtensions:
    @staticmethod
    def _rand() -> float:
        # детерминированный «rand» для тестов
        return 0.25

    @staticmethod
    def _randint(lo: float, hi: float) -> float:
        return float(int((lo + hi) // 2))

    def test_extra_function_accepted_and_evaluated(self) -> None:
        expr = parse_expression("rand() + [a]", extra_functions={"rand": self._rand})
        assert expr.evaluate({"a": 1.0}) == 1.25

    def test_extra_function_with_args(self) -> None:
        expr = parse_expression(
            "randint(0, 10) * 2",
            extra_functions={"randint": self._randint},
        )
        assert expr.evaluate({}) == 10.0

    def test_extra_function_math_error_gives_nan(self) -> None:
        def bad() -> float:
            raise ArithmeticError

        expr = parse_expression("bad()", extra_functions={"bad": bad})
        assert math.isnan(expr.evaluate({}))

    def test_extra_functions_do_not_leak_into_plain_parse(self) -> None:
        with pytest.raises(ValueError, match="Неизвестная функция 'rand'"):
            parse_expression("rand()")

    def test_extra_function_name_conflict_rejected(self) -> None:
        with pytest.raises(ValueError, match="конфликтует"):
            parse_expression("sqrt(4)", extra_functions={"sqrt": lambda x: x})
        with pytest.raises(ValueError, match="конфликтует"):
            parse_expression("pi", extra_functions={"pi": self._rand})

    def test_extra_function_invalid_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="Недопустимое имя"):
            parse_expression("1", extra_functions={"__ref_0": self._rand})

    def test_extra_names_accepted_and_evaluated(self) -> None:
        expr = parse_expression("t * 2 + prev", extra_names={"t", "prev"})
        assert expr.evaluate({}, names={"t": 3.0, "prev": 0.5}) == 6.5

    def test_extra_names_mixed_with_refs(self) -> None:
        expr = parse_expression("[base] + t", extra_names={"t"})
        assert expr.deps == frozenset({"base"})
        assert expr.evaluate({"base": 10.0}, names={"t": 1.5}) == 11.5

    def test_extra_names_not_in_deps(self) -> None:
        expr = parse_expression("[a] + t + prev", extra_names={"t", "prev"})
        assert expr.deps == frozenset({"a"})
        assert expr.names == frozenset({"t", "prev"})

    def test_unused_extra_names_not_stored(self) -> None:
        expr = parse_expression("t + 1", extra_names={"t", "prev"})
        assert expr.names == frozenset({"t"})

    def test_missing_extra_name_raises_key_error(self) -> None:
        expr = parse_expression("t + 1", extra_names={"t"})
        with pytest.raises(KeyError):
            expr.evaluate({}, names={})
        with pytest.raises(KeyError):
            expr.evaluate({})

    def test_extra_names_do_not_leak_into_plain_parse(self) -> None:
        with pytest.raises(ValueError, match="Неизвестное имя 't'"):
            parse_expression("t + 1")

    def test_extra_name_conflict_rejected(self) -> None:
        with pytest.raises(ValueError, match="конфликтует"):
            parse_expression("pi + 1", extra_names={"pi"})
        with pytest.raises(ValueError, match="конфликтует"):
            parse_expression("1", extra_names={"sqrt"})
        with pytest.raises(ValueError, match="конфликтует"):
            parse_expression(
                "1", extra_functions={"rand": self._rand}, extra_names={"rand"}
            )

    def test_extra_name_placeholder_like_rejected(self) -> None:
        with pytest.raises(ValueError, match="Недопустимое имя"):
            parse_expression("1", extra_names={"__ref_0"})

    def test_plain_expression_unchanged(self) -> None:
        expr = parse_expression("[a] + pi")
        assert expr.names == frozenset()
        assert expr.evaluate({"a": 1.0}) == pytest.approx(1.0 + math.pi)

    def test_injections_still_rejected_with_extensions(self) -> None:
        with pytest.raises(ValueError):
            parse_expression(
                "__import__('os').system('echo pwned')",
                extra_functions={"rand": self._rand},
                extra_names={"t"},
            )
        with pytest.raises(ValueError):
            parse_expression("t.__class__", extra_names={"t"})
        with pytest.raises(ValueError):
            parse_expression("rand.__globals__", extra_functions={"rand": self._rand})
        with pytest.raises(ValueError):
            parse_expression("t(1)", extra_names={"t"})


class TestParseFormattedValues:
    def test_single_float_f32(self) -> None:
        values = parse_formatted_values("0.1", "f32", 2)
        assert len(values) == 2
        assert abs(decode_register_values(values, "f32")[0] - 0.1) < 1e-6

    def test_multiple_numbers_fill_groups(self) -> None:
        values = parse_formatted_values("0.5, 2.5", "f32", 4)
        decoded = decode_register_values(values, "f32")
        assert abs(decoded[0] - 0.5) < 1e-6
        assert abs(decoded[1] - 2.5) < 1e-6

    def test_pad_and_truncate_to_count(self) -> None:
        assert parse_formatted_values("5", "s16", 3) == [5, 0, 0]
        assert parse_formatted_values("1, 2, 3", "s16", 2) == [1, 2]

    def test_separators_and_whitespace(self) -> None:
        assert parse_formatted_values("  1 ,  2   3 ", "s16", 3) == [1, 2, 3]

    def test_int_formats_round(self) -> None:
        assert parse_formatted_values("-5.4", "s16", 1) == [-5 & 0xFFFF]
        assert parse_formatted_values("70000", "u32", 2) == [1, 4464]

    def test_errors(self) -> None:
        with pytest.raises(ValueError):
            parse_formatted_values("", "f32", 2)
        with pytest.raises(ValueError):
            parse_formatted_values("abc", "f32", 2)


class TestEncodeAsciiValues:
    def test_roundtrip(self) -> None:
        values = encode_ascii_values("MC-42", 4)
        assert format_register_values(values, "ascii") == "MC-42"
        assert len(values) == 4  # pad до count

    def test_two_chars_per_register_high_first(self) -> None:
        assert encode_ascii_values("qwe", 2) == [0x7177, 0x6500]

    def test_truncate_to_count(self) -> None:
        assert encode_ascii_values("abcdef", 2) == [0x6162, 0x6364]

    def test_non_ascii_replaced_with_question_mark(self) -> None:
        values = encode_ascii_values("aж", 1)
        assert format_register_values(values, "ascii") == "a?"

    def test_empty(self) -> None:
        assert encode_ascii_values("", 2) == [0, 0]


class TestAscii1Format:
    def test_decode_one_char_per_register(self) -> None:
        values = [ord(c) for c in "WBMSW4"] + [0] * 14
        assert format_register_values(values, "ascii1") == "WBMSW4"

    def test_encode_one_char_per_register(self) -> None:
        assert encode_ascii_values("qwe", 5, 1) == [0x71, 0x77, 0x65, 0, 0]

    def test_roundtrip(self) -> None:
        values = encode_ascii_values("MC-42", 8, 1)
        assert format_register_values(values, "ascii1") == "MC-42"

    def test_non_ascii_replaced(self) -> None:
        assert format_register_values(encode_ascii_values("aж", 2, 1), "ascii1") == "a?"


class TestValueNames:
    def test_parse_lines(self) -> None:
        assert parse_value_names("0=Stopped\n1=Starting\n2 = Pump running") == {
            0: "Stopped",
            1: "Starting",
            2: "Pump running",
        }

    def test_parse_hex_and_negative_keys(self) -> None:
        assert parse_value_names("0x10=hex\n-1=below zero") == {16: "hex", -1: "below zero"}

    def test_parse_skips_junk(self) -> None:
        text = "no separator\n=empty key\n3=\nabc=name\n7=Ok"
        assert parse_value_names(text) == {7: "Ok"}

    def test_parse_empty_text_is_empty_dict(self) -> None:
        assert parse_value_names("") == {}
        assert parse_value_names("  \n\n") == {}

    def test_parse_duplicate_key_last_wins(self) -> None:
        assert parse_value_names("1=one\n1=uno") == {1: "uno"}

    def test_text_roundtrip(self) -> None:
        names = {0: "Stopped", 2: "Pump running", 10: "Full speed"}
        assert value_names_to_text(names) == "0=Stopped\n2=Pump running\n10=Full speed"
        assert parse_value_names(value_names_to_text(names)) == names

    def test_text_of_empty(self) -> None:
        assert value_names_to_text({}) == ""

    def test_json_roundtrip(self) -> None:
        names = {0: "Off", 1: "On"}
        assert value_names_to_json(names) == {"0": "Off", "1": "On"}
        assert value_names_from_json(value_names_to_json(names)) == names

    def test_from_json_tolerant(self) -> None:
        assert value_names_from_json({"0": "Off", "x": 1, "bad key": "no", "5": "On"}) == {
            0: "Off",
            5: "On",
        }
        assert value_names_from_json(None) == {}
        assert value_names_from_json([1, 2]) == {}

    def test_format_named_value(self) -> None:
        names = {0: "Off", 1: "On"}
        assert format_named_value(names, 0) == "Off (0)"
        assert format_named_value(names, True) == "On (1)"  # биты — как 0/1
        assert format_named_value(names, 5) is None
        assert format_named_value({}, 0) is None

    def test_row_display_settings_default(self) -> None:
        assert RowDisplaySettings().value_names == {}
