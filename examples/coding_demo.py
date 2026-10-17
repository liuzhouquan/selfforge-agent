"""Reproducible end-to-end tool loop demo; no API key or Docker required."""

import json

from selfforge_agent.runtime.evaluation import OfflineFixtureModel, evaluate

if __name__ == "__main__":
    print(json.dumps(evaluate(OfflineFixtureModel), indent=2))
