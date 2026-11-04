"""Content-free, bounded stage measurements for the source discovery benchmark."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Literal

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

Stage = Literal["planner_model", "extractor_model", "source_read", "search"]


def error_types(error: BaseException) -> list[str]:
    """Bounded structural diagnostics, never exception messages or response bodies."""
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    names: list[str] = []
    while pending and len(seen) < 12:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        names.append(type(current).__name__)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions[:4])
        if current.__context__ is not None:
            pending.append(current.__context__)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
    return names


@dataclass
class Measurement:
    stage: Stage
    started_seconds: float
    seconds: float = 0
    outcome: Literal["ok", "failed", "cancelled"] = "ok"
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class DiscoveryMetrics:
    clock: Callable[[], float] = time.monotonic
    measurements: list[Measurement] = field(default_factory=list)
    dropped: int = 0
    _started: float = field(init=False)

    def __post_init__(self) -> None:
        self._started = self.clock()

    @contextmanager
    def measure(self, stage: Stage) -> Iterator[Measurement]:
        started = self.clock()
        measurement = Measurement(stage, round(started - self._started, 3))
        try:
            yield measurement
        except asyncio.CancelledError:
            measurement.outcome = "cancelled"
            raise
        except Exception:
            measurement.outcome = "failed"
            raise
        finally:
            measurement.seconds = round(self.clock() - started, 3)
            if len(self.measurements) < 128:
                self.measurements.append(measurement)
            else:
                self.dropped += 1

    def report(self) -> dict[str, object]:
        # Keep a timeline, not a sum: asynchronous operations may overlap.
        return {
            "measurements": [asdict(item) for item in self.measurements],
            "dropped": self.dropped,
        }


class MeasuredModel(WrapperModel):
    def __init__(
        self,
        wrapped: Model,
        metrics: DiscoveryMetrics,
        stage: Literal["planner_model", "extractor_model"],
    ) -> None:
        super().__init__(wrapped)
        self.metrics = metrics
        self.stage = stage

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        with self.metrics.measure(self.stage) as measurement:
            response = await self.wrapped.request(
                messages, model_settings, model_request_parameters
            )
            measurement.input_tokens = response.usage.input_tokens
            measurement.output_tokens = response.usage.output_tokens
            return response
