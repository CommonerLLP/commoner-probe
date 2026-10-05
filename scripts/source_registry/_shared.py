"""Shared fixtures for registry entries: a scratch directory and a placeholder topic."""
import tempfile
from pathlib import Path

from commoner_probe.topics import TopicProfile

SCRATCH = Path(tempfile.mkdtemp(prefix="commoner-freshness-"))
TOPIC = TopicProfile(name="freshness-check", description="", search_groups={},
                     lok_sabha_ministries=[], rajya_sabha_ministry_likes=[])
