import pytest

from modbus_connector.models import format_values, parse_values


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
