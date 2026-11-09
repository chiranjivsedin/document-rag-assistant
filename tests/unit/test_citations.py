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


@pytest.mark.parametrize(
    ("reply", "tidied"),
    [
        # what gemma3:4b wrote in the live test on the Documentum 23.4 documents (5 passages)
        ("There are no new features in version 23.4 [2], 4].", "There are no new features in version 23.4 [2, 4]."),
        ("Feature X was added [2] , 4 ,5 ].", "Feature X was added [2, 4, 5]."),
        ("Feature X was added [2], 2].", "Feature X was added [2]."),  # repaired, then the repeat goes
        ("Feature X was added [2], 9].", "Feature X was added [2]."),  # repaired, then 9 (no such passage) goes
    ],
)
def test_a_broken_citation_group_is_repaired(reply, tidied):
    assert tidy_citations(reply, 5) == tidied


@pytest.mark.parametrize(
    ("reply", "tidied"),
    [
        ("Postgres 15.x is supported [7].", "Postgres 15.x is supported."),  # only 5 passages
        ("Postgres 15.x is supported [0].", "Postgres 15.x is supported."),
        ("Postgres 15.x is supported [2, 7].", "Postgres 15.x is supported [2]."),
        ("Postgres 15.x is supported [2][7].", "Postgres 15.x is supported [2]."),
        ("Postgres 15.x is supported [7] and RHEL 7.x is not [3].",
         "Postgres 15.x is supported and RHEL 7.x is not [3]."),
        ("Versions differ [4-8].", "Versions differ [4-5]."),  # a range is cut to the passages there are
        ("Versions differ [5-8].", "Versions differ [5]."),
        ("Versions differ [6-8].", "Versions differ."),
        ("Postgres 15.x is supported [2][3].", "Postgres 15.x is supported [2][3]."),  # valid: kept as written
        ("Versions [1-3] differ [5].", "Versions [1-3] differ [5]."),
    ],
)
def test_citations_of_passages_that_do_not_exist_are_dropped(reply, tidied):
    assert tidy_citations(reply, 5) == tidied


@pytest.mark.parametrize(
    "reply, tidied",
    [
        ("Add-on repositories can be provisioned [5.2.1.1].", "Add-on repositories can be provisioned."),
        ("Linux Slim is the base image [5.2.2]. Spring 6.x is used [3].",
         "Linux Slim is the base image. Spring 6.x is used [3]."),
        ("New features are listed [5][2.1].", "New features are listed [5]."),
        ("Documentum 23.4 supports Spring 6.x [1].", "Documentum 23.4 supports Spring 6.x [1]."),  # prose numbers stay
    ],
)
def test_section_numbers_cited_as_passages_are_dropped(reply, tidied):
    assert tidy_citations(reply, 5) == tidied


def test_without_the_number_of_passages_no_citation_is_dropped():
    assert tidy_citations("Postgres 15.x is supported [7].") == "Postgres 15.x is supported [7]."


def test_a_reply_left_with_only_invalid_citations_is_not_an_answer():
    assert has_no_words(tidy_citations("[7]", 5))
