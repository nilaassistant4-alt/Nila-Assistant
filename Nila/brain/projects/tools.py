"""Agent tools for projects. They only write encrypted records, and only when the
owner asks (the system prompt forbids storing anything on Nila's own initiative)."""
from __future__ import annotations

from typing import Any, Dict

from .files import FileStore
from .store import CANVAS, STATUSES, ProjectError, ProjectStore


def store_for(ctx: Any) -> ProjectStore:
    files = FileStore(ctx.config.home / "files", lambda: ctx.vault.session.mk)
    return ProjectStore(ctx.memory, files)


def _guard(fn):
    def wrapper(ctx, **kw):
        try:
            return {"ok": True, **fn(store_for(ctx), **kw)}
        except ProjectError as exc:
            return {"ok": False, "error": str(exc)}
    return wrapper


def register(reg: Any) -> None:
    blocks = [c["key"] for c in CANVAS]

    @reg.tool(name="project_create",
              description="Create a new project from an idea. Only when the user asks to save an idea or start a project.",
              parameters={"type": "object", "properties": {
                  "title": {"type": "string"}, "idea": {"type": "string"}}, "required": ["title"]},
              risk="low", tags=["projects", "memory"])
    def project_create(ctx, title: str, idea: str = "") -> Dict[str, Any]:
        return _guard(lambda st, title, idea: {"project": st.create(title, idea)})(ctx, title=title, idea=idea)

    @reg.tool(name="project_list", description="List the user's projects with their status.",
              parameters={"type": "object", "properties": {}}, risk="low", tags=["projects"])
    def project_list(ctx) -> Dict[str, Any]:
        return _guard(lambda st: st.listing())(ctx)

    @reg.tool(name="project_status",
              description="Summarize a project's progress and suggest next steps (name or id).",
              parameters={"type": "object", "properties": {"project": {"type": "string"}},
                          "required": ["project"]}, risk="low", tags=["projects"])
    def project_status(ctx, project: str) -> Dict[str, Any]:
        return _guard(lambda st, project: st.report(project))(ctx, project=project)

    @reg.tool(name="project_resume",
              description="Resume work: show the most recently touched unfinished project, its progress and next steps.",
              parameters={"type": "object", "properties": {}}, risk="low", tags=["projects"])
    def project_resume(ctx) -> Dict[str, Any]:
        def run(st):
            r = st.resume()
            return r if r else {"summary": "No unfinished projects.", "next_steps": []}
        return _guard(run)(ctx)

    @reg.tool(name="project_note", description="Add a progress note to a project (only when asked).",
              parameters={"type": "object", "properties": {
                  "project": {"type": "string"}, "text": {"type": "string"}},
                  "required": ["project", "text"]}, risk="low", tags=["projects", "memory"])
    def project_note(ctx, project: str, text: str) -> Dict[str, Any]:
        return _guard(lambda st, project, text: {"project": st.add_note(project, text)})(ctx, project=project, text=text)

    @reg.tool(name="project_canvas",
              description="Fill one block of the project's business model: " + ", ".join(blocks) + ".",
              parameters={"type": "object", "properties": {
                  "project": {"type": "string"}, "block": {"type": "string", "enum": blocks},
                  "text": {"type": "string"}}, "required": ["project", "block", "text"]},
              risk="low", tags=["projects", "memory"])
    def project_canvas(ctx, project: str, block: str, text: str) -> Dict[str, Any]:
        return _guard(lambda st, project, block, text: {"project": st.set_canvas(project, block, text)})(
            ctx, project=project, block=block, text=text)

    @reg.tool(name="project_milestone",
              description="Add a milestone to a project, optionally with the day it is due (YYYY-MM-DD).",
              parameters={"type": "object", "properties": {
                  "project": {"type": "string"}, "text": {"type": "string"},
                  "due": {"type": "string"}},
                  "required": ["project", "text"]}, risk="low", tags=["projects", "memory"])
    def project_milestone(ctx, project: str, text: str, due: str = "") -> Dict[str, Any]:
        return _guard(lambda st, project, text, due: {"project": st.add_milestone(project, text, due)})(
            ctx, project=project, text=text, due=due)

    @reg.tool(name="project_late",
              description="Milestones whose day has passed and are not done, across every project.",
              parameters={"type": "object", "properties": {}}, risk="low", tags=["projects"])
    def project_late(ctx) -> Dict[str, Any]:
        return _guard(lambda st: {"late": st.late_milestones()})(ctx)

    @reg.tool(name="project_next_question",
              description="The next single question of the business-model interview for a project.",
              parameters={"type": "object", "properties": {"project": {"type": "string"}},
                          "required": ["project"]}, risk="low", tags=["projects"])
    def project_next_question(ctx, project: str) -> Dict[str, Any]:
        return _guard(lambda st, project: {"question": st.interview(project)})(ctx, project=project)

    @reg.tool(name="project_set_status",
              description="Set a project's status: " + ", ".join(STATUSES) + ".",
              parameters={"type": "object", "properties": {
                  "project": {"type": "string"}, "status": {"type": "string", "enum": list(STATUSES)}},
                  "required": ["project", "status"]}, risk="low", tags=["projects", "memory"])
    def project_set_status(ctx, project: str, status: str) -> Dict[str, Any]:
        return _guard(lambda st, project, status: {"project": st.set_status(project, status)})(ctx, project=project, status=status)
