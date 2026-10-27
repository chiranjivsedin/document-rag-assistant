from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from rag.generation.llm import answer
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


def test_answer_sends_passages_and_returns_reply_with_sources():
    model = RecordingModel(responses=["  Classic Desktop is deprecated [1].  "], seen=[])
    result = answer("What is deprecated?", DOCS, model)
    assert result.text == "Classic Desktop is deprecated [1]."
    assert result.sources == DOCS
    sent = model.seen[0].to_messages()[1].content
    assert "Classic Desktop is deprecated." in sent and "Question: What is deprecated?" in sent


def test_no_passages_means_no_model_call():
    model = RecordingModel(responses=["should not be used"], seen=[])
    result = answer("Anything?", [], model)
    assert result.text == NO_ANSWER
    assert result.sources == []
    assert model.seen == []
