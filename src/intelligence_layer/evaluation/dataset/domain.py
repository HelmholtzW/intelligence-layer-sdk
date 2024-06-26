from typing import Generic, TypeVar
from uuid import uuid4

from pydantic import BaseModel, Field
from rich.tree import Tree

from intelligence_layer.core.task import Input
from intelligence_layer.core.tracer.tracer import PydanticSerializable

ExpectedOutput = TypeVar("ExpectedOutput", bound=PydanticSerializable)
"""Dataset-specific type that defines characteristics that an :class:`Output` can be checked against.

Traditional names for this are `label` or `y` in classification."""


class Example(BaseModel, Generic[Input, ExpectedOutput]):
    """Example case used for evaluations.

    Attributes:
        input: Input for the :class:`Task`. Has to be same type as the input for the task used.
        expected_output: The expected output from a given example run.
            This will be used by the evaluator to compare the received output with.
        id: Identifier for the example, defaults to uuid.

    Generics:
        Input: Interface to be passed to the :class:`Task` that shall be evaluated.
        ExpectedOutput: Output that is expected from the run with the supplied input.
    """

    input: Input
    expected_output: ExpectedOutput
    id: str = Field(default_factory=lambda: str(uuid4()))

    def __repr__(self) -> str:
        return self.__str__()

    def __str__(self) -> str:
        return (
            f"Example ID = {self.id}\n"
            f"Input = {self.input}\n"
            f'Expected output = "{self.expected_output}"\n'
        )

    def _rich_render(self) -> Tree:
        example_tree = Tree(f"Example: {self.id}")
        example_tree.add("Input").add(str(self.input))
        example_tree.add("Expected Output").add(str(self.expected_output))
        return example_tree


class Dataset(BaseModel):
    """Represents a dataset linked to multiple examples

    Attributes:
        id: Dataset ID.
        name: A short name of the dataset.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str

    def __repr__(self) -> str:
        return self.__str__()

    def __str__(self) -> str:
        return f"Dataset ID = {self.id}\nName = {self.name}\n"
