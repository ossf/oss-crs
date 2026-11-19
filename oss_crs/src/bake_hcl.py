# SPDX-License-Identifier: MIT
"""Evaluate `docker-bake.hcl` files without `docker buildx bake`.

Podman has no `buildx bake`, so this resolves the plan itself. `python-hcl2`
only parses HCL; `_BakeEvaluator` evaluates the parsed expressions (variables,
`${...}` interpolation and user-defined functions) the way bake does.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import hcl2
from hcl2.rules.base import AttributeRule, BlockRule, StartRule
from hcl2.rules.containers import ObjectRule, TupleRule
from hcl2.rules.expressions import ExprTermRule
from hcl2.rules.functions import FunctionCallRule
from hcl2.rules.literal_rules import IdentifierRule
from hcl2.rules.strings import InterpolationRule, StringRule
from hcl2.utils import SerializationOptions, process_escape_sequences

_HCL_STRIP_OPTS = SerializationOptions(
    with_comments=False,
    explicit_blocks=False,
    strip_string_quotes=True,
)

Attrs = dict[str, ExprTermRule]


@dataclass
class _BakeBlocks:
    variables: dict[str, ExprTermRule | None] = field(default_factory=dict)
    functions: dict[str, tuple[list[str], ExprTermRule]] = field(default_factory=dict)
    groups: dict[str, Attrs] = field(default_factory=dict)
    targets: dict[str, Attrs] = field(default_factory=dict)


def _identifier(node: object) -> str | None:
    if isinstance(node, ExprTermRule):
        node = node.expression
    if isinstance(node, IdentifierRule):
        return str(node.token.serialize())
    return None


def _collect_blocks(doc: StartRule) -> _BakeBlocks:
    """Sort top-level `variable`, `function`, `group` and `target` blocks by type."""
    blocks = _BakeBlocks()
    for child in doc.body.children:
        if not isinstance(child, BlockRule):
            continue
        labels = [str(label.serialize(_HCL_STRIP_OPTS)) for label in child.labels]
        if len(labels) < 2:
            continue
        btype, bname = labels[0], labels[1]
        attrs = {
            str(attr.identifier.serialize(_HCL_STRIP_OPTS)): attr.expression
            for attr in child.body.children
            if isinstance(attr, AttributeRule)
        }
        if btype == "variable":
            blocks.variables[bname] = attrs.get("default")
        elif btype == "function" and "result" in attrs:
            params_term = attrs.get("params")
            elements = (
                params_term.expression.elements
                if isinstance(params_term, ExprTermRule)
                and isinstance(params_term.expression, TupleRule)
                else []
            )
            params = [name for el in elements if (name := _identifier(el))]
            blocks.functions[bname] = (params, attrs["result"])
        elif btype == "group":
            blocks.groups[bname] = attrs
        elif btype == "target":
            blocks.targets[bname] = attrs
    return blocks


class _BakeEvaluator:
    """Evaluate bake expressions; variables are resolved on first use."""

    def __init__(self, blocks: _BakeBlocks, env: Mapping[str, str], source: Path):
        self._defaults = blocks.variables
        self._functions = blocks.functions
        self._env = env
        self._source = source
        self._variables: dict[str, str] = {}
        self._resolving: set[str] = set()

    def variable(self, name: str) -> str:
        if name in self._variables:
            return self._variables[name]
        if name in self._env:
            value = self._env[name]
        else:
            if name in self._resolving:
                raise ValueError(f"{self._source}: variable {name!r} refers to itself")
            self._resolving.add(name)
            try:
                default = self.eval(self._defaults[name])
            finally:
                self._resolving.discard(name)
            value = "" if default is None else str(default)
        self._variables[name] = value
        return value

    def eval(self, node: object, scope: Mapping[str, object] | None = None) -> object:
        scope = scope or {}
        if node is None:
            return None
        if isinstance(node, ExprTermRule):
            return self.eval(node.expression, scope)
        if isinstance(node, IdentifierRule):
            name = str(node.token.serialize())
            if name in scope:
                return scope[name]
            if name in self._defaults:
                return self.variable(name)
            return self._env.get(name, name)
        if isinstance(node, StringRule):
            parts: list[str] = []
            for part in node.string_parts:
                if isinstance(part.content, InterpolationRule):
                    val = self.eval(part.content.expression, scope)
                    parts.append("" if val is None else str(val))
                else:
                    parts.append(
                        process_escape_sequences(str(part.serialize(_HCL_STRIP_OPTS)))
                    )
            return "".join(parts)
        if isinstance(node, TupleRule):
            return [self.eval(elem, scope) for elem in node.elements]
        if isinstance(node, ObjectRule):
            obj: dict[str, object] = {}
            for elem in node.elements:
                key_node = elem.key.value
                key = _identifier(key_node) or str(self.eval(key_node, scope))
                obj[key] = self.eval(elem.expression, scope)
            return obj
        if isinstance(node, FunctionCallRule):
            fn_name = "::".join(
                str(ident.token.serialize()) for ident in node.identifiers
            )
            if fn_name in self._functions:
                call_args = [
                    self.eval(arg, scope)
                    for arg in (node.arguments.arguments if node.arguments else [])
                ]
                params, result_expr = self._functions[fn_name]
                return self.eval(result_expr, {**scope, **dict(zip(params, call_args))})
        if hasattr(node, "serialize"):
            return node.serialize(_HCL_STRIP_OPTS)  # type: ignore[attr-defined]
        return node


def parse_bake_hcl(hcl_path: Path, env: Mapping[str, str]) -> dict:
    """Resolve `docker-bake.hcl` into the shape of `docker buildx bake --print`.

    Returns `{"target": ..., "group": ...}`, omitting `group` when there are none.
    Block order does not matter.
    """
    doc = hcl2.parses(hcl_path.read_text(), discard_comments=True)
    blocks = _collect_blocks(doc)
    evaluator = _BakeEvaluator(blocks, env, hcl_path)

    targets: dict[str, dict] = {}
    for name, attrs in blocks.targets.items():
        evaluated = {k: evaluator.eval(v) for k, v in attrs.items()}
        target: dict = {
            "context": evaluated.pop("context", "."),
            "dockerfile": evaluated.pop("dockerfile", "Dockerfile"),
            **evaluated,
        }
        if isinstance(target.get("args"), dict):
            target["args"] = {str(k): str(v) for k, v in target["args"].items()}
        targets[name] = target

    result: dict = {"target": targets}
    if blocks.groups:
        result["group"] = {
            name: {k: evaluator.eval(v) for k, v in attrs.items()}
            for name, attrs in blocks.groups.items()
        }
    return result
