"""Plugin-facing AI extension SDK (powered by Prompture).

This is how a plugin EXTENDS the core assistant instead of building its own:
installing the plugin can teach the assistant new tools, contribute page
context, and let plugin code invoke the assistant in-process.

A plugin declares an ``ai`` block in its ``plugin.json``::

    "ai": {
      "entry_point": "ai:register",                  # app.plugins.<slug>.ai:register
      "suggested_prompts": [
        {"route": "/git", "label": "Summarize commits", "prompt": "Summarize the latest commits"}
      ],
      "context_routes": ["/git", "/git/*"]
    }

and ships ``app/plugins/<slug>/ai.py``::

    from app.plugins_sdk import ai

    def register(reg):                                # reg is a PluginToolBinder
        @reg.tool(rbac_feature="git", rbac_level="read")
        def list_branches(repo: str) -> list:
            \"\"\"List branches in a repo.
            Args:
                repo: repository slug.
            \"\"\"
            from app.services.git_service import GitService
            return GitService.list_branches(repo)

        @reg.tool(rbac_feature="git", rbac_level="write", is_write=True)
        def delete_branch(repo: str, branch: str) -> dict:
            \"\"\"Delete a branch. Args: repo: ...; branch: ...\"\"\"
            ...                                       # runs only AFTER confirmation

        reg.register_context_provider("/git", lambda user, route, params: "...context...")

Tools are namespaced ``<slug>__<name>`` so they can never collide. RBAC is
declared per tool and enforced by the core (a tool the user can't use is never
even offered to the model). Write tools (``is_write=True``) always go through
the human confirmation handshake before they run.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Iterator, Optional

from app.services.ai_tool_registry import ai_tool_registry

logger = logging.getLogger(__name__)


class PluginToolBinder:
    """Passed to a plugin's ``register(reg)`` function; binds tools to its slug."""

    def __init__(self, slug: str):
        self.slug = slug

    def tool(self, _fn: Optional[Callable] = None, *, rbac_feature: Optional[str] = None,
             rbac_level: str = "read", is_write: bool = False,
             name: Optional[str] = None, description: Optional[str] = None):
        """Register a function as an AI tool. Usable bare (``@reg.tool``) or with args."""
        def decorator(fn: Callable) -> Callable:
            ai_tool_registry.register(
                name=name or fn.__name__, func=fn, description=description,
                plugin_slug=self.slug, rbac_feature=rbac_feature,
                rbac_level=rbac_level, is_write=is_write,
            )
            return fn
        if _fn is not None and callable(_fn):
            return decorator(_fn)
        return decorator

    def register_tool(self, fn: Callable, **kwargs) -> Callable:
        """Imperative equivalent of the :meth:`tool` decorator."""
        return self.tool(**kwargs)(fn)

    def register_context_provider(self, route_pattern: str, func: Callable[..., Any]) -> None:
        """Register a callable ``fn(user, route, params) -> str`` that augments the
        system prompt for pages matching *route_pattern* (fnmatch glob)."""
        ai_tool_registry.register_context_provider(route_pattern, func, plugin_slug=self.slug)

    def register_attachment_resolver(
            self, attachment_type: str, func: Callable[..., Any]) -> None:
        """Register ``fn(user, id) -> {label, source, observed_at, summary}``.

        Attachment types must be namespaced after the plugin slug, for example
        ``minecraft.world`` for the ``minecraft`` plugin.
        """
        from app.services.ai_attachment_registry import ai_attachment_registry
        ai_attachment_registry.register(
            attachment_type, func, plugin_slug=self.slug, replace=True,
        )


# Module-level convenience so a plugin can also register a context provider
# without holding the binder (defaults to no plugin slug → cleared only globally).
def register_context_provider(route_pattern: str, func: Callable[..., Any],
                              plugin_slug: Optional[str] = None) -> None:
    ai_tool_registry.register_context_provider(route_pattern, func, plugin_slug=plugin_slug)


def ask(prompt: str, *, mode: str = "simple", page_context: Optional[dict] = None,
        profile: Optional[str] = None, workflow: str = 'chat') -> str:
    """Invoke the assistant in-process as the current JWT user (RBAC applies)."""
    from app.plugins_sdk import current_user
    from app.services import ai_service
    return ai_service.oneshot_ask(current_user(), prompt, mode=mode, page_context=page_context, profile=profile, workflow=workflow)


def ask_stream(prompt: str, *, mode: str = "simple",
               page_context: Optional[dict] = None, profile: Optional[str] = None,
               workflow: str = 'chat') -> Iterator[str]:
    """Streaming in-process assistant call; yields text chunks."""
    from app.plugins_sdk import current_user
    from app.services import ai_service
    return ai_service.oneshot_stream(current_user(), prompt, mode=mode, page_context=page_context, profile=profile, workflow=workflow)
