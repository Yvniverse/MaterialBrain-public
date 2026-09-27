"""Typed request/response contracts for the TypeSafe System One API."""

from __future__ import annotations

from math import isclose
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

QuestionInstructions = str | dict[str, Any] | list[Any]


class _QuestionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: QuestionInstructions


class JevNoulQuestion(_QuestionBase):
    type: Literal["noul"]
    criteria: dict[str, QuestionInstructions] | None = None


class JevChoiceQuestion(_QuestionBase):
    type: Literal["choice"]
    criteria: dict[str, QuestionInstructions | None] = Field(min_length=2, max_length=255)


class JevScoreQuestion(_QuestionBase):
    type: Literal["score"]
    criteria: list[QuestionInstructions] = Field(min_length=2, max_length=10)


JevQuestion = Annotated[
    Union[JevNoulQuestion, JevChoiceQuestion, JevScoreQuestion],
    Field(discriminator="type"),
]


class JevRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str | dict[str, Any] | list[Any]
    model: str = Field(min_length=1, max_length=100)
    questions: dict[str, JevQuestion] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_question_ids(self):
        for question_id in self.questions:
            if not question_id or len(question_id) > 64:
                raise ValueError("Jev question id must be 1-64 characters")
        return self


class JevUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class _AnswerBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JevNoulAnswer(_AnswerBase):
    type: Literal["noul"]
    noul: float = Field(ge=0, le=1)


class JevChoiceAnswer(_AnswerBase):
    type: Literal["choice"]
    choice: str = Field(min_length=1)
    probabilities: dict[str, float] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class JevScoreAnswer(_AnswerBase):
    type: Literal["score"]
    score: float
    legend: dict[str, str]
    probabilities: dict[str, float] = Field(min_length=2)
    confidence: float = Field(ge=0, le=1)


JevAnswer = Annotated[
    Union[JevNoulAnswer, JevChoiceAnswer, JevScoreAnswer],
    Field(discriminator="type"),
]


class JevResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)
    answers: dict[str, JevAnswer]
    usage: JevUsage


def validate_jev_response(request: JevRequest, payload: Any) -> JevResponse:
    """Validate API shape and ensure answers match the typed questions exactly."""

    response = JevResponse.model_validate(payload)
    expected_ids = set(request.questions)
    actual_ids = set(response.answers)
    if actual_ids != expected_ids:
        raise ValueError(
            f"Jev answer ids mismatch: expected={sorted(expected_ids)} actual={sorted(actual_ids)}"
        )
    for question_id, question in request.questions.items():
        answer = response.answers[question_id]
        if answer.type != question.type:
            raise ValueError(f"Jev answer type mismatch for {question_id}")
        if isinstance(question, JevChoiceQuestion):
            assert isinstance(answer, JevChoiceAnswer)
            options = set(question.criteria)
            if answer.choice not in options or set(answer.probabilities) != options:
                raise ValueError(f"Jev choice options mismatch for {question_id}")
            if any(value < 0 or value > 1 for value in answer.probabilities.values()):
                raise ValueError(f"Jev choice probabilities out of range for {question_id}")
            if not isclose(sum(answer.probabilities.values()), 1.0, abs_tol=0.02):
                raise ValueError(f"Jev choice probabilities do not sum to one for {question_id}")
        elif isinstance(question, JevScoreQuestion):
            assert isinstance(answer, JevScoreAnswer)
            expected_legend = {
                str(index): str(value) for index, value in enumerate(question.criteria)
            }
            if answer.legend != expected_legend or set(answer.probabilities) != set(
                expected_legend
            ):
                raise ValueError(f"Jev score legend mismatch for {question_id}")
            if not isclose(sum(answer.probabilities.values()), 1.0, abs_tol=0.02):
                raise ValueError(f"Jev score probabilities do not sum to one for {question_id}")
            if answer.score < 0 or answer.score > len(question.criteria) - 1:
                raise ValueError(f"Jev score out of range for {question_id}")
        else:
            assert isinstance(answer, JevNoulAnswer)
    return response
