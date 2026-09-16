from app.security.auth import ip_allowed, passphrase_matches


def test_passphrase_matches_correct():
    assert passphrase_matches("secret", "secret") is True


def test_passphrase_matches_incorrect():
    assert passphrase_matches("wrong", "secret") is False


def test_ip_allowed_empty_list_allows_all():
    assert ip_allowed("1.2.3.4", []) is True


def test_ip_allowed_exact_match():
    assert ip_allowed("1.2.3.4", ["1.2.3.4", "5.6.7.8"]) is True


def test_ip_allowed_no_match():
    assert ip_allowed("9.9.9.9", ["1.2.3.4"]) is False


def test_ip_allowed_cidr_match():
    assert ip_allowed("10.0.0.5", ["10.0.0.0/24"]) is True


def test_ip_allowed_invalid_client_ip():
    assert ip_allowed("not-an-ip", ["1.2.3.4"]) is False
