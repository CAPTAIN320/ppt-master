"""
Loads the agent system prompt for web mode.

The system prompt is kept short and neutral to avoid proxy content filters.
The full SKILL.md workflow document is injected as the first user message
in agent_runner.py so the agent has the complete pipeline instructions.
"""

from pathlib import Path

_SKILL_MD_PATH = Path("/app/skills/ppt-master/SKILL.md")


def get_system_prompt() -> str:
    """Return a short, neutral system prompt for the agent."""
    return """\
You are PPT Master, an AI assistant that creates professional PowerPoint presentations.
You have access to tools for reading/writing files, running Python scripts, and managing projects.
You follow a structured pipeline to generate high-quality presentations from user topics or documents.
Always use the confirm_gate tool at the design confirmation step to get user approval before generating slides.
After writing each SVG slide file, call the slide_ready tool to notify the UI.
Export the final PPTX to /app/exports/ when the pipeline is complete.
"""


def get_skill_md() -> str:
    """Return the full SKILL.md content to inject as the first user message."""
    return _SKILL_MD_PATH.read_text(encoding="utf-8")
