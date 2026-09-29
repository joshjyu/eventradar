"""Topic rules keep their accuracy on the labeled corpus."""

from pathlib import Path

import yaml

from eventradar.classify.topic import TopicMatcher
from eventradar.config import load_config

ROOT = Path(__file__).resolve().parents[2]
CORPUS = yaml.safe_load(
    (Path(__file__).parent / "classify_labels.yaml").read_text()
)


def test_accuracy_meets_baseline() -> None:
    """Accuracy never drops below the recorded baseline."""
    topic = load_config(ROOT / "config").topics[CORPUS["topic"]]
    matcher = TopicMatcher(topic)
    wrong = [
        item["title"]
        for item in CORPUS["items"]
        if matcher.matches(
            item["title"],
            item.get("description"),
            trusted=item.get("trusted", False),
        )
        != item["tech"]
    ]
    accuracy = 1 - len(wrong) / len(CORPUS["items"])
    assert accuracy >= CORPUS["baseline"], wrong
