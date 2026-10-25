"""Complaint lexicon (lexicon.yaml) and phrase matching."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

LEXICON_PATH = Path(__file__).with_name("lexicon.yaml")
NEGATIONS = frozenset({"not", "never", "isn't", "isnt", "aren't", "arent", "doesn't", "doesnt", "don't",
                       "dont", "wasn't", "wasnt", "without", "hardly", "no"})
_NEGATION_WINDOW = 2


def normalize(text: str) -> str:
    text = text.lower().replace("\u2019", "'").replace("\u2018", "'")
    text = re.sub(r"[-_/]", " ", text)
    text = re.sub(r"[.,;:!?()]+", " . ", text)  # clause breaks stop the negation window
    text = re.sub(r"[^\w\s'.]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Match:
    descriptors: list[str] = field(default_factory=list)  # audio descriptors, in order of appearance
    midi: list[str] = field(default_factory=list)
    phrases: dict[str, str] = field(default_factory=dict)  # name -> the phrase that matched


@dataclass
class Lexicon:
    descriptors: dict[str, dict]
    midi: dict[str, list[str]]

    def __post_init__(self):
        entries = []
        for kind, table in (("audio", {k: v["phrases"] for k, v in self.descriptors.items()}), ("midi", self.midi)):
            for name, phrases in table.items():
                for phrase in phrases:
                    norm = normalize(phrase)
                    rx = re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, norm.split())) + r"(?!\w)")
                    entries.append((kind, name, norm, rx))
        self._entries = entries

    def match(self, complaint: str) -> Match:
        text = normalize(complaint)
        spans = []
        for kind, name, phrase, rx in self._entries:
            for m in rx.finditer(text):
                spans.append((m.start(), m.end(), kind, name, phrase))
        spans.sort(key=lambda s: (-(s[1] - s[0]), s[0]))
        taken: list[tuple[int, int, str, str, str]] = []
        for s in spans:
            if all(s[1] <= t[0] or s[0] >= t[1] for t in taken):
                taken.append(s)
        out = Match()
        for start, _, kind, name, phrase in sorted(taken):
            before = text[:start].split()
            if "." in before:
                before = before[len(before) - before[::-1].index("."):]
            if any(tok in NEGATIONS for tok in before[-_NEGATION_WINDOW:]):
                continue
            target = out.descriptors if kind == "audio" else out.midi
            if name not in target:
                target.append(name)
                out.phrases[name] = phrase
        return out


@lru_cache(maxsize=4)
def load_lexicon(path: str | None = None) -> Lexicon:
    data = yaml.safe_load(Path(path or LEXICON_PATH).read_text(encoding="utf-8"))
    return Lexicon(descriptors=data["descriptors"], midi=data.get("midi", {}))
