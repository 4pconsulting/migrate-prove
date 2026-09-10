from migrate_prove.hashing import row_signature
from migrate_prove.models import NormalizeSpec
from migrate_prove.normalize import canonicalize


def test_lookup_equivalent_hashes():
    spec = NormalizeSpec(trim=True, upper=True, decimal_places=2)
    left = row_signature(["1002", "Jane Doe", "Active", "1984-03-12", "1240.50"], [spec] * 5)
    right = row_signature(["1002", "Jane Doe", "Active", "1984-03-12", "1240.5000"], [spec] * 5)
    assert left == right
    assert canonicalize("1240.5000", spec) == "1240.50"
    assert canonicalize("  jane doe ", spec) == "JANE DOE"


def test_null_and_empty_canonicalise_the_same():
    spec = NormalizeSpec(empty_as_null=True, null_token="")
    assert canonicalize(None, spec) == canonicalize("", spec)
