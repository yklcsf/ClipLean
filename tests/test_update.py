from cliplean.update import is_newer


def test_version_comparison():
    assert is_newer("v1.0.1", "1.0.0")
    assert is_newer("1.10.0", "1.9.3")
    assert not is_newer("v1.0.0", "1.0.0")
    assert not is_newer("0.9.9", "1.0.0")
    assert not is_newer("beta", "1.0.0")
