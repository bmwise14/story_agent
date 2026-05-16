from typing import Literal, Optional
from pydantic import BaseModel, Field


class Character(BaseModel):
    name: str
    archetype: str
    short_description: str


class StoryConfig(BaseModel):
    premise: str
    chapter_count: int = Field(ge=1, le=10)
    approx_words_per_chapter: int = 800
    style: Literal["psychological_personal_journey"] = "psychological_personal_journey"
    themes: list[str]
    characters: list[Character]


class Outline(BaseModel):
    logline: str
    act_breakdown: list[str]       # one entry per chapter, ~2 sentences each
    character_arcs: dict[str, str] # character name -> arc description


class ChapterBeats(BaseModel):
    chapter_number: int
    beats: list[str]               # 3-5 key story beats for this chapter
    emotional_arc: str             # one sentence


class Chapter(BaseModel):
    chapter_number: int
    text: str
    summary: Optional[str] = None  # ~3 sentences, populated after summarize node


class Revelation(BaseModel):
    chapter_number: int            # which chapter this was uncovered in
    character_name: str            # must match a name in config.characters
    what_was_revealed: str         # one-sentence statement of what the reader now knows
    kind: Literal["fact", "feeling", "relationship", "memory", "secret"]
