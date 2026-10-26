import pytest

from whiletrue.cli.args import FLAG_OPTIONS, VALUE_OPTIONS, parse_args


def test_positionals_and_defaults() -> None:
    args = parse_args(["approve", "7"])
    assert args.positionals == ["approve", "7"] and args.file == "whiletrue.yaml"
    assert not (args.force or args.help or args.follow or args.yes)


def test_value_options_accept_both_spellings_and_equals() -> None:
    assert parse_args(["-f", "a.yaml"]).file == "a.yaml"
    assert parse_args(["--file=b.yaml"]).file == "b.yaml"
    assert parse_args(["reject", "1", "-m", "why"]).note == "why"
    assert parse_args(["--note=because x=y"]).note == "because x=y"
    assert parse_args(["--step", "review"]).step == "review"
    assert parse_args(["--from=work"]).from_step == "work"


def test_flags() -> None:
    assert parse_args(["--force", "--yes", "--follow", "-h"]).help
    assert parse_args(["--yes"]).yes and parse_args(["--follow"]).follow


def test_bad_input_is_a_clear_error() -> None:
    with pytest.raises(ValueError, match="argument missing"):
        parse_args(["--file"])
    with pytest.raises(ValueError, match="argument missing"):
        parse_args(["-m"])
    with pytest.raises(ValueError, match="Unknown option '--nope'"):
        parse_args(["--nope"])
    assert parse_args(["-"]).positionals == ["-"]


def test_every_option_maps_to_a_real_field() -> None:
    args = parse_args([])
    for field in {*VALUE_OPTIONS.values(), *FLAG_OPTIONS.values()}:
        assert hasattr(args, field)
