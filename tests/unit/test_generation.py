import pytest
from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from rag.generation.llm import answer, stream_answer
from rag.generation.prompts import ANSWER_PROMPT, NO_ANSWER, format_context, source_label

DOCS = [
    Document(page_content="Classic Desktop is deprecated.",
             metadata={"source": "acme/1.0/deprecated.pdf", "version": "1.0", "page": 3}),
    Document(page_content="Use Acme Web instead.", metadata={"source": "acme/1.0/notes.md", "version": "1.0", "page": None}),
]


class RecordingModel(FakeListChatModel):
    """Fake chat model that also keeps the prompt it was sent."""

    seen: list = []

    def invoke(self, messages, *args, **kwargs):
        self.seen.append(messages)
        return super().invoke(messages, *args, **kwargs)


def test_source_label():
    assert source_label(DOCS[0]) == "acme/1.0/deprecated.pdf, version 1.0, page 3"
    assert source_label(DOCS[1]) == "acme/1.0/notes.md, version 1.0"
    assert source_label(Document(page_content="x", metadata={})) == "unknown source"


def test_context_numbers_passages_in_order():
    context = format_context(DOCS)
    assert context.startswith("[1] (acme/1.0/deprecated.pdf, version 1.0, page 3)\nClassic Desktop is deprecated.")
    assert "\n\n[2] (acme/1.0/notes.md, version 1.0)\nUse Acme Web instead." in context


def test_prompt_restricts_model_to_the_passages():
    system, human = ANSWER_PROMPT.invoke({"context": "[1] (x)\ntext", "question": "Q?"}).to_messages()
    assert "only the numbered context passages" in system.content
    assert NO_ANSWER in system.content
    assert human.content.endswith("Question: Q?")


def test_prompt_asks_for_worded_supported_answers_with_exact_citations():
    system = ANSWER_PROMPT.invoke({"context": "[1] (x)\ntext", "question": "Q?"}).to_messages()[0].content
    assert "A citation alone is never an answer" in system  # "[1]" came back as a whole answer
    assert "Only state what the passages say" in system  # claims beyond the passages
    assert "say which version each fact applies to" in system  # facts from different versions mixed up
    assert "Cite only the passages that state that fact" in system  # [1][2] where [2] says nothing about it
    assert "[2][3]" not in system  # the old example invited stacked citations
    # The "I don't know" reply must stay exact (no_answer and hidden sources depend on it), so it carries no
    # citation even though every other sentence must have one.
    assert f"reply exactly this, with no citation: {NO_ANSWER}" in system


def test_answer_sends_passages_and_returns_reply_with_sources():
    model = RecordingModel(responses=["  Classic Desktop is deprecated [1].  "], seen=[])
    result = answer("What is deprecated?", DOCS, model)
    assert result.text == "Classic Desktop is deprecated [1]."
    assert result.sources == DOCS
    sent = model.seen[0].to_messages()[1].content
    assert "Classic Desktop is deprecated." in sent and "Question: What is deprecated?" in sent


@pytest.mark.parametrize("reply", ["[1]", "[1], [2]", " [2]. ", ""])
def test_a_reply_without_words_becomes_the_no_answer_reply(reply):
    result = answer("Which version added a mobile app?", DOCS, FakeListChatModel(responses=[reply]))
    assert result.text == NO_ANSWER


def test_a_reply_that_only_says_the_passages_lack_the_answer_becomes_the_no_answer_reply():
    model = FakeListChatModel(responses=["The release date of 2.0 is not stated in the passages [1]."])
    assert answer("What is the release date of 2.0?", DOCS, model).text == NO_ANSWER


def test_answer_collapses_repeated_citations():
    result = answer("What is deprecated?", DOCS, FakeListChatModel(responses=["Classic Desktop is deprecated [1][1]."]))
    assert result.text == "Classic Desktop is deprecated [1]."


def test_no_passages_means_no_model_call():
    model = RecordingModel(responses=["should not be used"], seen=[])
    result = answer("Anything?", [], model)
    assert result.text == NO_ANSWER
    assert result.sources == []
    assert model.seen == []


def test_stream_answer_yields_the_reply_in_pieces():
    pieces = list(stream_answer("What is deprecated?", DOCS, FakeListChatModel(responses=["Classic Desktop [1]."])))
    assert len(pieces) > 1
    assert "".join(pieces) == "Classic Desktop [1]."


def test_stream_answer_with_no_passages_says_so():
    assert list(stream_answer("Anything?", [], FakeListChatModel(responses=["unused"]))) == [NO_ANSWER]
