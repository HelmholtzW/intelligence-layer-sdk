import pytest

from intelligence_layer.core import NoOpTracer
from intelligence_layer.core.chunk import TextChunk
from intelligence_layer.core.detect_language import Language, LanguageNotSupportedError
from intelligence_layer.examples.classify.keyword_extract import (
    KeywordExtract,
    KeywordExtractInput,
)


@pytest.fixture()
def keyword_extract() -> KeywordExtract:
    return KeywordExtract()


def test_keyword_extract_works(keyword_extract: KeywordExtract) -> None:
    input = KeywordExtractInput(
        chunk=TextChunk("I really like my computer"), language=Language("en")
    )

    result = keyword_extract.run(input, NoOpTracer())
    assert "computer" in [keyword.lower() for keyword in result.keywords]


def test_keyword_extract_raises_for_unsupported_language(
    keyword_extract: KeywordExtract,
) -> None:
    input = KeywordExtractInput(
        chunk=TextChunk("text about computers"), language=Language("pt")
    )
    with pytest.raises(LanguageNotSupportedError) as _:
        keyword_extract.run(input, NoOpTracer())
