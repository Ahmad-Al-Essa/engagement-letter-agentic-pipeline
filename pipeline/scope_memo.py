"""The scope memo: triage's decision, in a fixed shape every later step works from.

Because it has a fixed shape (a schema), code can read its fields: routing
looks at `verdict`, the checks test each `client_quote` against the intake,
and the partner's evidence view prints it line by line. Ollama is told to
answer in exactly this shape, so it always parses.

There are no database ids in the memo. On a test run the model copied a
prospect's id into the client_id field, pointing at a different, real client.
So ids never pass through the model: code reads them from the tool results
(see find_owner in triage.py).
"""

from typing import Literal

from pydantic import BaseModel, Field


class SelectedService(BaseModel):
    code: str = Field(description="The service code exactly as the catalog search returned it, e.g. AUD-001")
    reason: str = Field(description="One sentence: why this service answers the client's need")
    client_quote: str = Field(description="The client's own words that ask for this, copied exactly from the request")


class ExcludedService(BaseModel):
    code: str = Field(description="A service the search returned but that does not fit")
    reason: str = Field(description="One sentence: why it does not fit this client")


class NotOffered(BaseModel):
    need: str = Field(description="A need the catalog has no service for, in plain words")
    client_quote: str = Field(description="The client's own words for this need, copied exactly from the request")


class PausedService(BaseModel):
    code: str = Field(description="A matching service that the search marked PAUSED")
    client_quote: str = Field(description="The client's own words that ask for it, copied exactly from the request")


class ScopeMemo(BaseModel):
    company: str = Field(description="The company name from the intake")
    verdict: Literal["in_scope", "out_of_scope", "unclear"]
    verdict_reason: str = Field(description="One or two sentences explaining the verdict")
    services: list[SelectedService] = Field(description="Services selected, each with the client's words")
    excluded: list[ExcludedService] = Field(description="Near neighbours from the search that were ruled out")
    not_offered: list[NotOffered] = Field(description="Needs with no matching service in the catalog")
    paused: list[PausedService] = Field(description="Matching services that are paused right now")
    deadline: str = Field(description="The client's deadline in their words, or an empty string if none")
    open_questions: list[str] = Field(description="What a partner would need to ask the client (mainly for unclear)")
