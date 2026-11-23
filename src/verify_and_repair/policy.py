from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Candidate:
    name: str
    command: str
    timeout: float
    prior_yield: float
    applicable: bool = True


class LatencyPolicy:
    def __init__(
        self, path: Path, llm_estimate: float = 15.0, min_score: float = 0.015
    ):
        self.path = path
        self.llm_estimate = llm_estimate
        self.min_score = min_score
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.data = {}
        except (OSError, ValueError) as error:
            warnings.warn(f"Cannot load policy history {path}: {error}", stacklevel=2)
            self.data = {}

    def stats(self, name: str, default_seconds: float) -> tuple[float, float]:
        x = self.data.get(name, {})
        return float(x.get("mean_seconds", default_seconds)), float(
            x.get("useful_rate", 0.0)
        )

    def score(self, candidate: Candidate, default_seconds: float) -> float:
        elapsed, observed_yield = self.stats(candidate.name, default_seconds)
        p = (
            (candidate.prior_yield + observed_yield) / 2
            if candidate.name in self.data
            else candidate.prior_yield
        )
        return p / max(0.1, elapsed)

    def choose(
        self,
        candidates: list[Candidate],
        defaults: dict[str, float],
        remaining_seconds: float,
    ):
        ranked = []
        for c in candidates:
            if not c.applicable:
                continue
            elapsed, _ = self.stats(c.name, defaults[c.name])
            if elapsed <= remaining_seconds:
                ranked.append((self.score(c, defaults[c.name]), elapsed, c))
        ranked.sort(key=lambda x: (-x[0], x[1]))
        return ranked[0] if ranked and ranked[0][0] >= self.min_score else None

    def llm_score(
        self, useful_probability: float = 0.65, *, name: str = "agency-repair"
    ) -> float:
        return self.score(
            Candidate(name, "", self.llm_estimate, useful_probability),
            self.llm_estimate,
        )

    def update(self, name: str, duration_seconds: float, useful: bool) -> None:
        x = self.data.setdefault(
            name,
            {"count": 0, "mean_seconds": 0.0, "useful_count": 0, "useful_rate": 0.0},
        )
        n = int(x["count"]) + 1
        x["mean_seconds"] = (float(x["mean_seconds"]) * (n - 1) + duration_seconds) / n
        x["count"] = n
        x["useful_count"] = int(x["useful_count"]) + int(useful)
        x["useful_rate"] = x["useful_count"] / n
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")


def task_features(text: str, files: list[str]) -> dict[str, bool]:
    s = (text + " " + " ".join(files)).lower()
    return {
        "boundary": any(
            x in s
            for x in [
                "boundary",
                "index",
                "range",
                "off-by-one",
                "clamp",
                "search",
                "interval",
            ]
        ),
        "constraint": any(
            x in s
            for x in [
                "invariant",
                "constraint",
                "arithmetic",
                "state transition",
                "capacity",
                "maximize",
                "priority",
            ]
        ),
        "parser": any(x in s for x in ["parse", "parser", "json", "csv", "token"]),
    }


# compatibility alias with v0.4
features = task_features
