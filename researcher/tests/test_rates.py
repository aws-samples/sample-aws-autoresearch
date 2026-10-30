from aws_auto_research.rates import cost_usd, hourly_usd


def test_known_rates():
    assert hourly_usd("g4dn.xlarge") == 0.526
    assert hourly_usd("m8g.large") == 0.0898


def test_unknown_falls_back():
    assert hourly_usd("wat.xlarge", fallback=0.42) == 0.42
    assert hourly_usd(None, fallback=0.1) == 0.1


def test_cost_math():
    assert cost_usd(3600, "g4dn.xlarge") == 0.526          # 1h
    assert cost_usd(1800, "g4dn.xlarge") == 0.263          # 0.5h
    assert cost_usd(0, "g4dn.xlarge") == 0.0
    assert cost_usd(-5, "g4dn.xlarge") == 0.0              # never negative
