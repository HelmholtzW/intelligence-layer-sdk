from time import sleep
from typing import Callable, Iterable, Sequence, TypeVar
from uuid import uuid4

import pytest
from dotenv import load_dotenv
from pydantic import BaseModel
from pytest import fixture
from requests import HTTPError

from intelligence_layer.connectors.argilla.argilla_client import (
    ArgillaClient,
    ArgillaEvaluation,
    DefaultArgillaClient,
    Field,
    Question,
    RecordData,
)


class DummyInput(BaseModel):
    query: str


class DummyOutput(BaseModel):
    answer: str


ExpectedOutput = str


ReturnValue = TypeVar("ReturnValue")


def retry(
    f: Callable[[], ReturnValue], until: Callable[[ReturnValue], bool]
) -> ReturnValue:
    for i in range(10):
        r = f()
        if until(r):
            return r
        sleep(0.1)
    assert False, f"Condition not met after {i} retries"


@fixture
def argilla_client() -> DefaultArgillaClient:
    load_dotenv()
    return DefaultArgillaClient(total_retries=8)


@fixture
def workspace_id(argilla_client: DefaultArgillaClient) -> Iterable[str]:
    try:
        workspace_id = argilla_client.ensure_workspace_exists(str(uuid4()))
        yield workspace_id
    finally:
        argilla_client.delete_workspace(workspace_id)


@fixture
def qa_dataset_id(argilla_client: DefaultArgillaClient, workspace_id: str) -> str:
    dataset_name = "test-dataset"
    fields = [
        Field(name="question", title="Question"),
        Field(name="answer", title="Answer"),
    ]
    questions = [
        Question(
            name="rate-answer",
            title="Rate the answer",
            description="1 means bad, 3 means amazing.",
            options=list(range(1, 4)),
        )
    ]
    return argilla_client.ensure_dataset_exists(
        workspace_id, dataset_name, fields, questions
    )


@fixture
def qa_records(
    argilla_client: ArgillaClient, qa_dataset_id: str
) -> Sequence[RecordData]:
    records = [
        RecordData(
            content={"question": "What is 1+1?", "answer": str(i)},
            example_id=str(i),
            metadata={"model_1": "luminous-base"},
        )
        for i in range(60)
    ]
    for record in records:
        argilla_client.add_record(qa_dataset_id, record)
    return records


@fixture
def long_qa_records(
    argilla_client: ArgillaClient, qa_dataset_id: str
) -> Sequence[RecordData]:
    records = [
        RecordData(
            content={"question": "?", "answer": str(i)},
            example_id=str(i),
            metadata={"model_1": "luminous-base"},
        )
        for i in range(1024)
    ]
    for record in records:
        argilla_client.add_record(qa_dataset_id, record)
    return records


@pytest.mark.docker
def test_error_on_non_existent_dataset(
    argilla_client: DefaultArgillaClient,
) -> None:
    with pytest.raises(HTTPError):
        list(argilla_client.records("non_existent_dataset_id"))


@pytest.mark.docker
def test_records_returns_records_previously_added(
    argilla_client: DefaultArgillaClient,
    qa_dataset_id: str,
    qa_records: Sequence[RecordData],
) -> None:
    actual_records = argilla_client.records(qa_dataset_id)

    assert sorted(qa_records, key=lambda r: r.example_id) == sorted(
        [RecordData(**record.model_dump()) for record in actual_records],
        key=lambda r: r.example_id,
    )


@pytest.mark.docker
def test_evaluations_returns_evaluation_results(
    argilla_client: DefaultArgillaClient,
    qa_dataset_id: str,
    qa_records: Sequence[RecordData],
) -> None:
    evaluations = [
        ArgillaEvaluation(
            example_id=record.example_id,
            record_id=record.id,
            responses={"rate-answer": 1},
            metadata=record.metadata,
        )
        for record in argilla_client.records(qa_dataset_id)
    ]
    for evaluation in evaluations:
        argilla_client.create_evaluation(evaluation)

    actual_evaluations = retry(
        lambda: list(argilla_client.evaluations(qa_dataset_id)),
        lambda current: len(current) == len(evaluations),
    )

    assert sorted(actual_evaluations, key=lambda e: e.record_id) == sorted(
        evaluations, key=lambda e: e.record_id
    )


@pytest.mark.docker
def test_split_dataset_works(
    argilla_client: DefaultArgillaClient,
    qa_dataset_id: str,
    qa_records: Sequence[RecordData],
) -> None:
    n_splits = 5
    record_metadata = [
        record.metadata for record in argilla_client.records(qa_dataset_id)
    ]
    argilla_client.split_dataset(qa_dataset_id, n_splits)

    all_records = list(argilla_client.records(qa_dataset_id))
    for split in range(n_splits):
        assert (
            sum([record.metadata["split"] == str(split) for record in all_records])
            == 12
        )

    new_metadata_list = [record.metadata for record in all_records]
    for old_metadata, new_metadata in zip(record_metadata, new_metadata_list):
        del new_metadata["split"]  # type: ignore
        assert old_metadata == new_metadata


@pytest.mark.docker
def test_split_dataset_twice_works(
    argilla_client: DefaultArgillaClient,
    qa_dataset_id: str,
    qa_records: Sequence[RecordData],
) -> None:
    n_splits = 5
    argilla_client.split_dataset(qa_dataset_id, n_splits)

    n_splits = 1
    argilla_client.split_dataset(qa_dataset_id, n_splits)

    all_records = list(argilla_client.records(qa_dataset_id))

    assert sum([record.metadata["split"] == "0" for record in all_records]) == 60

    response = argilla_client.session.get(
        f"http://localhost:6900/api/v1/me/datasets/{qa_dataset_id}/metadata-properties"
    ).json()
    metadata_properties = response["items"][0]
    assert len(metadata_properties["settings"]["values"]) == 1


@pytest.mark.docker
def test_split_dataset_works_with_uneven_splits(
    argilla_client: DefaultArgillaClient,
    qa_dataset_id: str,
    qa_records: Sequence[RecordData],
) -> None:
    n_splits = 7
    argilla_client.split_dataset(qa_dataset_id, n_splits)

    all_records = list(argilla_client.records(qa_dataset_id))
    n_records_per_split = []
    for split in range(n_splits):
        n_records_per_split.append(
            sum([record.metadata["split"] == str(split) for record in all_records])
        )
    assert n_records_per_split == [9, 9, 9, 9, 8, 8, 8]


@pytest.mark.docker
def test_add_record_adds_multiple_records_with_same_content(
    argilla_client: DefaultArgillaClient,
    qa_dataset_id: str,
) -> None:
    first_data = RecordData(
        content={"question": "What is 1+1?", "answer": "1"},
        example_id="0",
        metadata={"first": "1", "second": "2"},
    )
    second_data = RecordData(
        content={"question": "What is 1+1?", "answer": "2"},
        example_id="0",
        metadata={"first": "2", "second": "1"},
    )

    argilla_client.add_record(qa_dataset_id, first_data)
    argilla_client.add_record(qa_dataset_id, second_data)
    assert len(list(argilla_client.records(qa_dataset_id))) == 2


@pytest.mark.docker
def test_add_record_does_not_put_example_id_into_metadata(
    argilla_client: DefaultArgillaClient,
    qa_dataset_id: str,
) -> None:
    first_data = RecordData(
        content={"question": "What is 1+1?", "answer": "1"},
        example_id="0",
        metadata={"first": "1", "second": "2"},
    )
    second_data = RecordData(
        content={"question": "What is 1+1?", "answer": "2"},
        example_id="0",
        metadata={"first": "2", "second": "1"},
    )

    argilla_client.add_record(qa_dataset_id, first_data)
    argilla_client.add_record(qa_dataset_id, second_data)
    records = list(argilla_client.records(qa_dataset_id))
    for record in records:
        assert "example_id" not in record.metadata.keys()
        assert record.example_id == "0"


@pytest.mark.docker
def test_split_dataset_can_split_long_dataset(
    argilla_client: DefaultArgillaClient,
    qa_dataset_id: str,
    long_qa_records: Sequence[RecordData],
) -> None:
    n_splits = 2
    record_metadata = [
        record.metadata for record in argilla_client.records(qa_dataset_id)
    ]
    argilla_client.split_dataset(qa_dataset_id, n_splits)

    all_records = list(argilla_client.records(qa_dataset_id))
    for split in range(n_splits):
        assert (
            sum([record.metadata["split"] == str(split) for record in all_records])
            == 512
        )

    new_metadata_list = [record.metadata for record in all_records]
    for old_metadata, new_metadata in zip(record_metadata, new_metadata_list):
        del new_metadata["split"]  # type: ignore
        assert old_metadata == new_metadata
