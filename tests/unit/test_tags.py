import pytest

from rag.dms.tags import (
    BUILTIN_KEYS,
    SEED_VALUES,
    InvalidTag,
    TagConflict,
    check_category_key,
    check_code,
    check_label,
    version_sort_key,
)


def test_versions_sort_numerically():
    assert sorted(["16.10", "latest", "20.2", "16.9", "16.2"], key=version_sort_key) == [
        "16.2", "16.9", "16.10", "20.2", "latest"]


def test_non_numeric_versions_sort_alphabetically_after_numbers():
    assert sorted(["beta", "Alpha", "26.4", "16.7.1", "16.7"], key=version_sort_key) == [
        "16.7", "16.7.1", "26.4", "Alpha", "beta"]


def test_seed_holds_exactly_the_agreed_values():
    assert set(SEED_VALUES) == set(BUILTIN_KEYS)
    versions = [code for code, _ in SEED_VALUES["version"]]
    assert len(versions) == 31 and versions[0] == "16.0" and "16.1" not in versions and "26.3" not in versions
    assert versions == sorted(versions, key=version_sort_key)
    assert all(code == label for code, label in SEED_VALUES["version"])
    assert SEED_VALUES["product"] == [("content-management", "Content Management")]
    assert [code for code, _ in SEED_VALUES["doc_type"]] == [
        "release-notes", "installation-guide", "upgrade-guide", "admin-guide"]
    for values in SEED_VALUES.values():  # every seed code is a valid folder name
        assert all(check_code(code) == code for code, _ in values)


@pytest.mark.parametrize("key", ["module", "audience", "m", "os-family2"])
def test_valid_category_keys(key):
    assert check_category_key(f" {key} ") == key


@pytest.mark.parametrize("key", ["", "Module", "2module", "-x", "a_b", "a b", "a:b", "x" * 33])
def test_invalid_category_keys(key):
    with pytest.raises(InvalidTag):
        check_category_key(key)


@pytest.mark.parametrize("key", BUILTIN_KEYS)
def test_builtin_keys_cant_be_added_again(key):
    with pytest.raises(TagConflict):
        check_category_key(key)


def test_labels_are_trimmed_and_inner_spaces_collapsed():
    assert check_label("  Release   notes ") == "Release notes"
    for bad in ("", "   ", "x" * 65):
        with pytest.raises(InvalidTag):
            check_label(bad)


@pytest.mark.parametrize("code", ["../x", "a b", "-a", "con", ".", "x" * 65])
def test_invalid_codes(code):
    with pytest.raises(InvalidTag):
        check_code(code)
