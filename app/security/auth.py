import hmac
import ipaddress


def passphrase_matches(provided: str, expected: str) -> bool:
    """Constant-time comparison to avoid timing side-channels."""
    return hmac.compare_digest(provided.encode(), expected.encode())


def ip_allowed(client_ip: str, allowed_ips: list[str]) -> bool:
    if not allowed_ips:
        return True
    try:
        client = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for entry in allowed_ips:
        try:
            if "/" in entry:
                if client in ipaddress.ip_network(entry, strict=False):
                    return True
            elif client == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False
