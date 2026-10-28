"""Identifier helpers: ULID claim ids and NPIs with a valid Luhn check digit."""
import random

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid(ms: int, rng: random.Random | None = None) -> str:
    """26-character ULID: 48-bit millisecond timestamp + 80 random bits, Crockford base32.
    Unique without coordination, so concurrent intake never races for an id."""
    value = (ms & ((1 << 48) - 1)) << 80 | (rng or random).getrandbits(80)
    out = []
    for _ in range(26):
        out.append(CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


def npi_check_digit(base9: str) -> str:
    """NPI check digit: Luhn over the prefix 80840 + the first nine digits (CMS rule)."""
    total = 0
    for i, ch in enumerate(reversed("80840" + base9)):
        d = int(ch)
        if i % 2 == 0:  # doubled positions, counting from the digit next to the check digit
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def make_npi(rng: random.Random) -> str:
    base = str(rng.choice((1, 2))) + "".join(str(rng.randint(0, 9)) for _ in range(8))
    return base + npi_check_digit(base)


def is_valid_npi(npi: str) -> bool:
    return len(npi) == 10 and npi.isdigit() and npi_check_digit(npi[:9]) == npi[9]
