import pytest

from rag.generation.citations import has_no_words, only_says_not_stated, tidy_citations


@pytest.mark.parametrize("reply", ["[1]", " [1][2]. ", "[1], [2]", "[1, 2]", "[1-3]", "[ 2 ]\n", "", " . "])
def test_a_reply_of_citations_or_punctuation_only_is_not_an_answer(reply):
    # gemma3:4b answered "Which version added a mobile app?" with just "[1]"
    assert has_no_words(reply)


@pytest.mark.parametrize("reply", ["Version 2.0 [1].", "2.0 [1]", "Token authentication [1].", "Yes [2]."])
def test_a_reply_with_words_is_an_answer(reply):
    assert not has_no_words(reply)


@pytest.mark.parametrize(
    "reply",
    [
        # what gemma3:4b actually wrote for "What is the release date of 2.0?" instead of the exact reply
        "The release date of 2.0 is not stated in the passages [1].",
        "The release date of 2.0 is not mentioned in the document [1].",
        "The passages do not mention single sign-on.",
        "The documents don't specify a release date [1].",
        "There is no information about the release date in the provided documents [1].",
        "The release date isn't explicitly stated in the release notes.",
        "The release date is not specified in the release notes [1].\nIt is not provided in the passages [2].",
    ],
)
def test_a_reply_that_only_says_the_passages_lack_the_answer_is_not_an_answer(reply):
    assert only_says_not_stated(reply)


@pytest.mark.parametrize(
    "reply",
    [
        "Basic authentication is not supported in version 2.0 [1].",  # a fact, not a gap in the documents
        "The Classic Desktop client is not available in 2.0 [1].",
        "Token authentication is not provided in 1.0 [1].",  # "not provided", but about the product
        "Version 2.0 adds a mobile app [1]. Its release date is not stated in the passages.",  # partly answered
        "The documents say the XML export format was removed in 2.0 [2].",
        "",
    ],
)
def test_a_reply_with_facts_is_an_answer(reply):
    assert not only_says_not_stated(reply)


@pytest.mark.parametrize(
    ("reply", "tidied"),
    [
        ("Removed in 2.0 [1][1].", "Removed in 2.0 [1]."),
        ("Removed in 2.0 [1] [1].", "Removed in 2.0 [1]."),
        ("Removed in 2.0 [1, 1].", "Removed in 2.0 [1]."),
        ("Removed in 2.0 [1][2][1].", "Removed in 2.0 [1][2]."),
        ("Removed in 2.0 [2, 1][1].", "Removed in 2.0 [2, 1]."),
        ("Removed in 2.0 [1,2, 2].", "Removed in 2.0 [1, 2]."),
    ],
)
def test_repeated_citations_in_one_place_are_collapsed(reply, tidied):
    assert tidy_citations(reply) == tidied


@pytest.mark.parametrize(
    "reply",
    [
        "A is deprecated [1]. B is deprecated [1].",  # one citation per statement is how it should look
        "Removed in 2.0 [1][2].",
        "Versions [1-3] differ.",  # ranges are left as written
        "See [note] and [1].",
        "No citations here.",
    ],
)
def test_other_text_is_left_as_written(reply):
    assert tidy_citations(reply) == reply
