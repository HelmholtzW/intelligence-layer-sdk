from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Literal, Optional

from aleph_alpha_client import (
    CompletionRequest,
    CompletionResponse,
    ExplanationRequest,
    ExplanationResponse,
)
from pydantic import BaseModel, ConfigDict
from tokenizers import Encoding, Tokenizer  # type: ignore

from intelligence_layer.connectors.limited_concurrency_client import (
    AlephAlphaClientProtocol,
    LimitedConcurrencyClient,
)
from intelligence_layer.core.prompt_template import PromptTemplate, RichPrompt
from intelligence_layer.core.task import Task
from intelligence_layer.core.tracer.tracer import TaskSpan, Tracer


class CompleteInput(BaseModel, CompletionRequest, frozen=True):
    """The input for a `Complete` task."""

    def to_completion_request(self) -> CompletionRequest:
        return CompletionRequest(**self.__dict__)


class CompleteOutput(BaseModel, CompletionResponse, frozen=True):
    """The output of a `Complete` task."""

    # BaseModel protects namespace "model_".
    # "model_version" is a field in CompletionResponse and clashes with the namespace.
    model_config = ConfigDict(protected_namespaces=())

    @staticmethod
    def from_completion_response(
        completion_response: CompletionResponse,
    ) -> "CompleteOutput":
        return CompleteOutput(**completion_response.__dict__)

    @property
    def completion(self) -> str:
        return self.completions[0].completion or ""

    @property
    def generated_tokens(self) -> int:
        return self.num_tokens_generated


class _Complete(Task[CompleteInput, CompleteOutput]):
    """Performs a completion request with access to all possible request parameters.

    Only use this task for testing. Is wrapped by the AlephAlphaModel for sending
    completion requests to the API.

    Args:
        client: Aleph Alpha client instance for running model related API calls.
        model: The name of a valid model that can access an API using an implementation
            of the AlephAlphaClientProtocol.
    """

    def __init__(self, client: AlephAlphaClientProtocol, model: str) -> None:
        super().__init__()
        self._client = client
        self._model = model

    def do_run(self, input: CompleteInput, task_span: TaskSpan) -> CompleteOutput:
        task_span.log("Model", self._model)
        return CompleteOutput.from_completion_response(
            self._client.complete(
                request=input.to_completion_request(),
                model=self._model,
            )
        )


class ExplainInput(BaseModel, ExplanationRequest, frozen=True):
    """The input for a `Explain` task."""

    def to_explanation_request(self) -> ExplanationRequest:
        return ExplanationRequest(**self.__dict__)


class ExplainOutput(BaseModel, ExplanationResponse, frozen=True):
    """The output of a `Explain` task."""

    # BaseModel protects namespace "model_".
    # "model_version" is a field in ExplanationResponse and clashes with the namespace.
    model_config = ConfigDict(protected_namespaces=())

    @staticmethod
    def from_explanation_response(
        explanation_response: ExplanationResponse,
    ) -> "ExplainOutput":
        return ExplainOutput(**explanation_response.__dict__)


class _Explain(Task[ExplainInput, ExplainOutput]):
    """Performs an explanation request with access to all possible request parameters.

    Only use this task for testing. Is wrapped by the AlephAlphaModel for sending
    explanation requests to the API.

    Args:
        client: Aleph Alpha client instance for running model related API calls.
        model: The name of a valid model that can access an API using an implementation
            of the AlephAlphaClientProtocol.
    """

    def __init__(self, client: AlephAlphaClientProtocol, model: str) -> None:
        super().__init__()
        self._client = client
        self._model = model

    def do_run(self, input: ExplainInput, task_span: TaskSpan) -> ExplainOutput:
        task_span.log("Model", self._model)
        return ExplainOutput.from_explanation_response(
            self._client.explain(
                request=input.to_explanation_request(), model=self._model
            )
        )


@lru_cache(maxsize=1)
def limited_concurrency_client_from_env() -> LimitedConcurrencyClient:
    return LimitedConcurrencyClient.from_env()


class AlephAlphaModel:
    """Abstract base class for the implementation of any model that uses the Aleph Alpha client.

    Any class of Aleph Alpha model is implemented on top of this base class. Exposes methods that
    are available to all models, such as `complete` and `tokenize`. It is the central place for
    all things that are physically interconnected with a model, such as its tokenizer or prompt
    format used during training.

    Args:
        name: The name of a valid model that can access an API using an implementation
            of the AlephAlphaClientProtocol.
        client: Aleph Alpha client instance for running model related API calls.
            Defaults to :class:`LimitedConcurrencyClient`
    """

    def __init__(
        self,
        name: str,
        client: Optional[AlephAlphaClientProtocol] = None,
    ) -> None:
        self.name = name
        self._client = (
            limited_concurrency_client_from_env() if client is None else client
        )
        self._complete: Task[CompleteInput, CompleteOutput] = _Complete(
            self._client, name
        )
        self._explain = _Explain(self._client, name)

    @property
    @lru_cache(maxsize=10)
    def context_size(self) -> int:
        models_info = self._client.models()
        context_size: Optional[int] = next(
            (
                model_info["max_context_size"]
                for model_info in models_info
                if model_info["name"] == self.name
            ),
            None,
        )
        if context_size is None:
            raise ValueError(f"No matching model found for name {self.name}")
        return context_size

    def complete_task(self) -> Task[CompleteInput, CompleteOutput]:
        return self._complete

    def complete(self, input: CompleteInput, tracer: Tracer) -> CompleteOutput:
        return self._complete.run(input, tracer)

    def explain(self, input: ExplainInput, tracer: Tracer) -> ExplainOutput:
        return self._explain.run(input, tracer)

    @lru_cache(maxsize=10)
    def get_tokenizer(self) -> Tokenizer:
        return self._client.tokenizer(self.name)

    def tokenize(self, text: str) -> Encoding:
        return self.get_tokenizer().encode(text)


class ControlModel(ABC, AlephAlphaModel):
    @abstractmethod
    def to_instruct_prompt(
        self,
        instruction: str,
        input: Optional[str] = None,
        response_prefix: Optional[str] = None,
    ) -> RichPrompt:
        pass


class LuminousControlModel(ControlModel):
    """An Aleph Alpha control model of the second generation.

    Args:
        name: The name of a valid model second generation control model.
            Defaults to `luminous-base-control`
        client: Aleph Alpha client instance for running model related API calls.
            Defaults to :class:`LimitedConcurrencyClient`
    """

    INSTRUCTION_PROMPT_TEMPLATE = PromptTemplate(
        """{% promptrange instruction %}{{instruction}}{% endpromptrange %}
{% if input %}
{% promptrange input %}{{input}}{% endpromptrange %}
{% endif %}
### Response:{{response_prefix}}"""
    )

    def __init__(
        self,
        name: Literal[
            "luminous-base-control-20230501",
            "luminous-extended-control-20230501",
            "luminous-supreme-control-20230501",
            "luminous-base-control",
            "luminous-extended-control",
            "luminous-supreme-control",
            "luminous-base-control-20240215",
            "luminous-extended-control-20240215",
            "luminous-supreme-control-20240215",
        ] = "luminous-base-control",
        client: Optional[AlephAlphaClientProtocol] = None,
    ) -> None:
        super().__init__(name, client)

    def to_instruct_prompt(
        self,
        instruction: str,
        input: Optional[str] = None,
        response_prefix: Optional[str] = None,
    ) -> RichPrompt:
        return self.INSTRUCTION_PROMPT_TEMPLATE.to_rich_prompt(
            instruction=instruction, input=input, response_prefix=response_prefix
        )


class Llama2InstructModel(ControlModel):
    """A llama-2-*-chat model, prompt-optimized for single-turn instructions.

    If possible, we recommend using `Llama3InstructModel` instead.

    Args:
        name: The name of a valid llama-2 model.
            Defaults to `llama-2-13b-chat`
        client: Aleph Alpha client instance for running model related API calls.
            Defaults to :class:`LimitedConcurrencyClient`
    """

    INSTRUCTION_PROMPT_TEMPLATE = PromptTemplate("""<s>[INST] <<SYS>>
{% promptrange instruction %}{{instruction}}{% endpromptrange %}
<</SYS>>{% if input %}

{% promptrange input %}{{input}}{% endpromptrange %}{% endif %} [/INST]{% if response_prefix %}

{{response_prefix}}{% endif %}""")

    def __init__(
        self,
        name: Literal[
            "llama-2-7b-chat",
            "llama-2-13b-chat",
            "llama-2-70b-chat",
        ] = "llama-2-13b-chat",
        client: Optional[AlephAlphaClientProtocol] = None,
    ) -> None:
        super().__init__(name, client)

    def to_instruct_prompt(
        self,
        instruction: str,
        input: Optional[str] = None,
        response_prefix: Optional[str] = None,
    ) -> RichPrompt:
        return self.INSTRUCTION_PROMPT_TEMPLATE.to_rich_prompt(
            instruction=instruction, input=input, response_prefix=response_prefix
        )


class Llama3InstructModel(ControlModel):
    """A llama-3-*-instruct model.

    Args:
        name: The name of a valid llama-3 model.
            Defaults to `llama-3-8b-instruct`
        client: Aleph Alpha client instance for running model related API calls.
            Defaults to :class:`LimitedConcurrencyClient`
    """

    INSTRUCTION_PROMPT_TEMPLATE = PromptTemplate(
        """<|begin_of_text|><|start_header_id|>user<|end_header_id|>

{% promptrange instruction %}{{instruction}}{% endpromptrange %}{% if input %}

{% promptrange input %}{{input}}{% endpromptrange %}{% endif %}<|eot_id|><|start_header_id|>assistant<|end_header_id|>{% if response_prefix %}

{{response_prefix}}{% endif %}"""
    )
    EOT_TOKEN = "<|eot_id|>"

    def __init__(
        self,
        name: Literal[
            "llama-3-8b-instruct",
            "llama-3-70b-instruct",
        ] = "llama-3-8b-instruct",
        client: Optional[AlephAlphaClientProtocol] = None,
    ) -> None:
        super().__init__(name, client)

    def _add_eot_token_to_stop_sequences(self, input: CompleteInput) -> CompleteInput:
        # remove this once the API supports the llama-3 EOT_TOKEN
        params = input.__dict__
        if isinstance(params["stop_sequences"], list):
            if self.EOT_TOKEN not in params["stop_sequences"]:
                params["stop_sequences"].append(self.EOT_TOKEN)
        else:
            params["stop_sequences"] = [self.EOT_TOKEN]
        return CompleteInput(**params)

    def complete(self, input: CompleteInput, tracer: Tracer) -> CompleteOutput:
        input_with_eot = self._add_eot_token_to_stop_sequences(input)
        return super().complete(input_with_eot, tracer)

    def to_instruct_prompt(
        self,
        instruction: str,
        input: Optional[str] = None,
        response_prefix: Optional[str] = None,
    ) -> RichPrompt:
        return self.INSTRUCTION_PROMPT_TEMPLATE.to_rich_prompt(
            instruction=instruction, input=input, response_prefix=response_prefix
        )
