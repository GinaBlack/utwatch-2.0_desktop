"""Decision-level fusion of the visual, temporal and audio flags."""
from __future__ import annotations

WEIGHTS = {"video": 0.45, "temporal": 0.35, "audio": 0.20}
WEIGHTED_THRESHOLD = 0.5


def decide(rule: str, video: bool, temporal: bool, audio: bool) -> bool:
    """or: any flag. and: video and audio. majority: two of three. weighted: score at least 0.5."""
    if rule == "or":
        return video or temporal or audio
    if rule == "and":
        return video and audio
    if rule == "majority":
        return (int(video) + int(temporal) + int(audio)) >= 2
    if rule == "weighted":
        score = WEIGHTS["video"] * video + WEIGHTS["temporal"] * temporal + WEIGHTS["audio"] * audio
        return score >= WEIGHTED_THRESHOLD
    raise ValueError("Unknown fusion rule: " + str(rule))
