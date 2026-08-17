import pytest

from modbus_connector.models import (
    AlarmRule,
    RegisterRow,
    RowDisplaySettings,
    Stats,
    alarm_rule_from_json,
    alarm_rule_to_json,
    alarm_rules_from_json,
    csv_header,
    decode_register_values,
    describe_exception,
    evaluate_alarm,
    format_register_values,
    format_scaled_values,
    format_values,
    guess_column_mapping,
    parse_values,
    rows_from_csv,
    rows_to_csv,
    rule_matches,
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
