"""Static reachability gate for public production functions.

Implements the OpenSpec requirement "No test-only public production code"
(``consolidate-shared-primitives-and-test-fidelity``): a public function in
``src/sideloadedipa`` must be reachable from production code, be a registered
console-script entry point, or be removed. Tests must never be the sole
caller of production code.

References are resolved through the AST, never by bare-name text search:

- Reachability is transitive from registered console functions and executable
  ``__main__`` guards. A reference from a dead wrapper is not a root, and an
  entirely unreferenced public function is still a violation. Module bodies,
  functions, and consumed classes are separate graph contexts.
- Cross-module references require a *qualified* import of the function's
  module (``from pkg.mod import f`` or ``import pkg.mod`` plus attribute
  access), following ``__init__`` re-export closures for bucket imports.
- Every import binding is then checked with Python scope/binding rules: the
  imported alias must actually be loaded in a scope where it resolves to
  that import binding. Same-named locals shadow it (including ``import pkg.mod
  as mod`` shadowed by a local ``mod``); lazy imports inside a function bind
  the alias for that function's loads.
- Inside the defining module, module-level statements are processed in
  execution order: a genuine call *before* a module-level rebinding counts,
  a call after it does not; sibling-function locals, class attributes, and
  comprehension targets shadow the function; ``global``/``nonlocal``
  declarations are honored; recursion inside the function is not a caller.

The synthetic counterexample tests below pin each of these rules.
"""

from __future__ import annotations

import ast
import tomllib
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

SRC_ROOT = Path("src/sideloadedipa")
SRC_BASE = Path("src")
TESTS_ROOT = Path("tests")
TESTS_BASE = Path(".")

# Explicit allow-list for entry points that have no in-repo production
# caller by design. Console-script entry points from pyproject.toml are
# allow-listed automatically; entries here must carry a justification.
ALLOW_LIST: set[str] = set()

_SCOPE_CREATORS = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ClassDef,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


@dataclass
class ModuleInfo:
    """AST-resolved imports and top-level functions of one module."""

    functions: set[str] = field(default_factory=set)
    def_nodes: dict[str, ast.stmt] = field(default_factory=dict)
    owners: dict[ast.AST, str | None] = field(default_factory=dict)
    has_main_guard: bool = False
    # (resolved module, imported name, local alias, import statement)
    from_imports: list[tuple[str, str, str, ast.stmt]] = field(default_factory=list)
    # (resolved module, local binding, dotted access prefix, import statement)
    module_imports: list[tuple[str, str, str, ast.stmt]] = field(default_factory=list)
    # (resolved module, import statement)
    star_imports: list[tuple[str, ast.stmt]] = field(default_factory=list)
    tree: ast.Module | None = None


@dataclass
class _Scope:
    kind: str  # "module" | "function" | "class" | "comprehension"
    bound: set[str] = field(default_factory=set)
    anchors: set[str] = field(default_factory=set)
    globals: set[str] = field(default_factory=set)
    nonlocals: set[str] = field(default_factory=set)
    eventual_anchors: set[str] = field(default_factory=set)
    touched: set[str] = field(default_factory=set)
    eventual_reads: bool = True


def _module_name(path: Path, base: Path) -> str:
    parts = list(path.relative_to(base).with_suffix("").parts)
    # ``pkg/__init__.py" is imported as ``pkg``, not ``pkg.__init__``.
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_import(
    module: str | None,
    level: int,
    importing_module: str,
    *,
    importing_is_package: bool,
) -> str | None:
    if level == 0:
        return module
    parts = importing_module.split(".")
    package = parts if importing_is_package else parts[:-1]
    parents = level - 1
    if parents > len(package):
        return None
    base = package[: len(package) - parents] if parents else package
    if module:
        base = [*base, *module.split(".")]
    return ".".join(base) if base else None


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _class_context(name: str) -> str:
    return f"@class:{name}"


def _is_main_guard(node: ast.stmt) -> bool:
    """Whether ``node`` is an ``if __name__ == "__main__"`` entry guard."""

    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    comparison = node.test
    if len(comparison.ops) != 1 or not isinstance(comparison.ops[0], ast.Eq):
        return False
    if len(comparison.comparators) != 1:
        return False
    left, right = comparison.left, comparison.comparators[0]
    return (
        isinstance(left, ast.Name)
        and left.id == "__name__"
        and isinstance(right, ast.Constant)
        and right.value == "__main__"
    ) or (
        isinstance(right, ast.Name)
        and right.id == "__name__"
        and isinstance(left, ast.Constant)
        and left.value == "__main__"
    )


def _scan_tree(root: Path, base: Path) -> dict[str, ModuleInfo]:
    modules: dict[str, ModuleInfo] = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = _module_name(path, base)
        tree = ast.parse(path.read_text(), filename=str(path))
        info = ModuleInfo(tree=tree)
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

        def owner(node: ast.AST) -> str | None:
            current = node
            inside_function = False
            while current in parents:
                current = parents[current]
                if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    inside_function = True
                    if parents.get(current) is tree:
                        return current.name
                if (
                    inside_function
                    and isinstance(current, ast.ClassDef)
                    and parents.get(current) is tree
                ):
                    return _class_context(current.name)
            return None

        info.owners = {node: owner(node) for node in ast.walk(tree)}
        info.has_main_guard = any(_is_main_guard(node) for node in tree.body)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                info.def_nodes[node.name] = node
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not (
                    node.name.startswith("_")
                ):
                    info.functions.add(node.name)
                else:
                    info.functions.discard(node.name)
        for walked in ast.walk(tree):
            if isinstance(walked, ast.ImportFrom):
                resolved = _resolve_import(
                    walked.module,
                    walked.level,
                    module,
                    importing_is_package=path.name == "__init__.py",
                )
                if resolved is None:
                    continue
                for alias in walked.names:
                    if alias.name == "*":
                        info.star_imports.append((resolved, walked))
                    else:
                        info.from_imports.append(
                            (resolved, alias.name, alias.asname or alias.name, walked)
                        )
            elif isinstance(walked, ast.Import):
                for alias in walked.names:
                    binding = alias.asname or alias.name.split(".", maxsplit=1)[0]
                    access_prefix = alias.asname or alias.name
                    info.module_imports.append((alias.name, binding, access_prefix, walked))
        modules[module] = info
    return modules


def _collect_scope_bindings(
    statements: Iterable[ast.AST], skip_node: ast.AST | None
) -> tuple[set[str], set[str], set[str]]:
    """Names bound directly in one scope body, plus ``global``/``nonlocal``.

    Child scopes (nested functions, classes, lambdas, comprehensions) are
    never entered: their locals do not leak into the enclosing scope.
    Pattern-matching captures (``MatchAs``/``MatchStar``/``MatchMapping.rest``)
    bind just like assignment targets.
    """

    bound: set[str] = set()
    globals_: set[str] = set()
    nonlocals: set[str] = set()

    def visit(node: ast.AST) -> None:
        if node is skip_node:
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
            return
        if isinstance(node, ast.Lambda):
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            return
        if isinstance(node, ast.Global):
            globals_.update(node.names)
            return
        if isinstance(node, ast.Nonlocal):
            nonlocals.update(node.names)
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".", maxsplit=1)[0])
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
            return
        if isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name is not None:
            bound.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest is not None:
            bound.add(node.rest)
        for child in ast.iter_child_nodes(node):
            visit(child)

    for statement in statements:
        visit(statement)
    return bound, globals_, nonlocals


def _scope_has_anchor(statements: Iterable[ast.AST], anchor_nodes: frozenset[ast.AST]) -> bool:
    """Whether an anchor statement sits directly in this scope body."""

    found = False

    def visit(node: ast.AST) -> None:
        nonlocal found
        if found:
            return
        if node in anchor_nodes:
            found = True
            return
        if isinstance(node, _SCOPE_CREATORS):
            return
        for child in ast.iter_child_nodes(node):
            visit(child)

    for statement in statements:
        visit(statement)
    return found


def _store_names(target: ast.AST) -> set[str]:
    return {
        child.id
        for child in ast.walk(target)
        if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del))
    }


def _pattern_names(pattern: ast.AST) -> set[str]:
    """Capture names bound by a ``match`` case pattern."""

    names: set[str] = set()
    for node in ast.walk(pattern):
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name is not None:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest is not None:
            names.add(node.rest)
    return names


def _dotted_loads_bound_to_anchors(
    tree: ast.Module,
    name: str,
    anchor_nodes: frozenset[ast.AST],
    skip_node: ast.AST | None,
) -> set[tuple[str, str | None]]:
    """Dotted load chains whose base resolves to the anchor binding of ``name``.

    ``anchor_nodes`` are the statements that introduce the binding of
    interest (the ``def`` for a defining module, or one import statement per
    import binding). Module-level statements are processed in execution
    order so a load before a rebinding resolves to the anchor while a load
    after it resolves to the new binding. Function/class scopes precompute
    their bindings, matching Python's scope-wide local semantics, but the
    anchor state itself is updated statement-by-statement in every scope: a
    lazy import inside a function marks the alias from that statement on,
    and a later rebinding of the alias in the same scope clears it again.
    """

    loads: set[tuple[str, str | None]] = set()
    owner_stack: list[str | None] = [None]
    class_owner_stack: list[str | None] = [None]
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def directly_in_scope(node: ast.AST, scope: ast.AST) -> bool:
        current = node
        while current in parents:
            current = parents[current]
            if current is scope:
                return True
            if isinstance(
                current,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
            ):
                return False
        return False

    def enclosing_function(node: ast.AST) -> ast.AST | None:
        current = node
        while current in parents:
            current = parents[current]
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                return current
        return None

    def deferred_binding_names(node: ast.AST) -> set[str]:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return {node.name}
        parent = parents.get(node)
        if isinstance(parent, ast.Assign) and parent.value is node:
            return {stored for target in parent.targets for stored in _store_names(target)}
        if isinstance(parent, ast.AnnAssign) and parent.value is node:
            return _store_names(parent.target)
        if isinstance(parent, ast.NamedExpr) and parent.value is node:
            return _store_names(parent.target)
        return set()

    def has_eventual_read(node: ast.AST) -> bool:
        """Whether this deferred callable is consumed after its anchor binds."""

        scope = enclosing_function(node)
        bindings = deferred_binding_names(node)
        if scope is None or not bindings:
            return False
        anchor_lines = [
            anchor.lineno
            for anchor in anchor_nodes
            if hasattr(anchor, "lineno")
            and anchor.lineno > getattr(node, "lineno", -1)
            and directly_in_scope(anchor, scope)
        ]
        if not anchor_lines:
            return False
        first_anchor = min(anchor_lines)
        return any(
            isinstance(candidate, ast.Name)
            and isinstance(candidate.ctx, ast.Load)
            and candidate.id in bindings
            and candidate.lineno > first_anchor
            and directly_in_scope(candidate, scope)
            for candidate in ast.walk(scope)
        )

    # Module-level defs, classes, and imports are visible to function bodies
    # regardless of statement order (calls resolve at call time), so pre-bind
    # them; sequential processing below then handles store-style rebinding.
    module_scope = _Scope("module")
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if statement.name == name:
                module_scope.bound.add(name)
                if statement in anchor_nodes:
                    module_scope.anchors.add(name)
            else:
                module_scope.bound.add(statement.name)
        elif isinstance(statement, (ast.Import, ast.ImportFrom)):
            if (
                isinstance(statement, ast.ImportFrom)
                and statement in anchor_nodes
                and any(alias.name == "*" for alias in statement.names)
            ):
                module_scope.bound.add(name)
                module_scope.anchors.add(name)
            for alias in statement.names:
                bound_name = alias.asname or alias.name.split(".", maxsplit=1)[0]
                if bound_name == name:
                    module_scope.bound.add(name)
                    if statement in anchor_nodes:
                        module_scope.anchors.add(name)
                else:
                    module_scope.bound.add(bound_name)

    def resolves(scopes: list[_Scope]) -> bool:
        force_module = False
        innermost = scopes[-1]
        for index in range(len(scopes) - 1, -1, -1):
            scope = scopes[index]
            if scope.kind == "module":
                return name in scope.bound and name in scope.anchors
            if force_module:
                continue
            if scope.kind == "function":
                if name in scope.globals:
                    force_module = True
                    continue
                if name in scope.nonlocals:
                    continue
                if name in scope.bound:
                    deferred_scopes = [
                        child for child in scopes[index + 1 :] if child.kind == "function"
                    ]
                    deferred = bool(deferred_scopes) and all(
                        child.eventual_reads for child in deferred_scopes
                    )
                    return name in scope.anchors or (
                        deferred and name not in scope.touched and name in scope.eventual_anchors
                    )
                continue
            if scope.kind == "class":
                # Class scopes are transparent to nested functions. Direct
                # class-body loads and writes still honor global/nonlocal.
                if scope is not innermost:
                    continue
                if name in scope.globals:
                    force_module = True
                    continue
                if name in scope.nonlocals:
                    continue
                if name in scope.bound:
                    return name in scope.anchors
                continue
            # Comprehension targets are visible only inside the implicit
            # comprehension scope.
            if scope is innermost and name in scope.bound:
                return name in scope.anchors
        return False

    def binding_scope(
        scopes: list[_Scope],
        *,
        named_expression: bool = False,
    ) -> _Scope:
        """The scope a binding of ``name`` lands in, honoring global/nonlocal."""

        current_index = len(scopes) - 1
        if named_expression:
            while current_index > 0 and scopes[current_index].kind == "comprehension":
                current_index -= 1
        current = scopes[current_index]
        active_scopes = scopes[: current_index + 1]
        module = next(scope for scope in scopes if scope.kind == "module")
        if current.kind == "module":
            return current
        if name in current.globals:
            return module
        if name in current.nonlocals:
            for scope in reversed(active_scopes[:-1]):
                if scope.kind != "function":
                    continue
                if name in scope.globals or name in scope.nonlocals:
                    continue
                if name in scope.bound:
                    return scope
            return module
        # Class and comprehension scopes only receive bindings made directly
        # in their own body; nested functions never bind there.
        return current

    def bind(
        scopes: list[_Scope],
        is_anchor: bool,
        *,
        named_expression: bool = False,
    ) -> None:
        scope = binding_scope(scopes, named_expression=named_expression)
        scope.bound.add(name)
        scope.touched.add(name)
        if is_anchor:
            scope.anchors.add(name)
        else:
            scope.anchors.discard(name)

    def bind_targets(
        scopes: list[_Scope],
        target: ast.AST,
        *,
        named_expression: bool = False,
    ) -> None:
        for stored in _store_names(target):
            if stored == name:
                # Rebinding the tracked name clears the anchor in *any*
                # scope, so a lazy import followed by an assignment stops
                # counting from that statement on.
                bind(scopes, False, named_expression=named_expression)
            elif scopes[-1].kind == "module":
                scopes[-1].bound.add(stored)

    def function_scope(
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
        *,
        eventual_reads: bool = True,
    ) -> _Scope:
        body: list[ast.AST]
        if isinstance(node, ast.Lambda):
            body = [node.body]
        else:
            body = list(node.body)
        bound, globals_, nonlocals = _collect_scope_bindings(body, skip_node)
        arguments = node.args
        bound |= {
            argument.arg
            for argument in (
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
            )
        }
        if arguments.vararg is not None:
            bound.add(arguments.vararg.arg)
        if arguments.kwarg is not None:
            bound.add(arguments.kwarg.arg)
        bound -= globals_ | nonlocals
        eventual_anchors = {name} if _scope_has_anchor(body, anchor_nodes) else set()
        return _Scope(
            "function",
            bound=bound,
            globals=globals_,
            nonlocals=nonlocals,
            eventual_anchors=eventual_anchors,
            eventual_reads=eventual_reads,
        )

    def visit(node: ast.AST, scopes: list[_Scope]) -> None:
        at_module = scopes[-1].kind == "module"
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                visit(decorator, scopes)
            for default in node.args.defaults:
                visit(default, scopes)
            for keyword_default in node.args.kw_defaults:
                if keyword_default is not None:
                    visit(keyword_default, scopes)
            if node.name == name:
                bind(scopes, node in anchor_nodes)
            elif at_module:
                scopes[-1].bound.add(node.name)
            if node is skip_node:
                # Recursion is not an external caller.
                return
            deferred_owner: str | None
            if at_module:
                deferred_owner = node.name
            elif scopes[-1].kind == "class" and class_owner_stack[-1] is not None:
                deferred_owner = class_owner_stack[-1]
            else:
                deferred_owner = owner_stack[-1]
            owner_stack.append(deferred_owner)
            scopes.append(
                function_scope(
                    node,
                    eventual_reads=at_module or has_eventual_read(node),
                )
            )
            for statement in node.body:
                visit(statement, scopes)
            scopes.pop()
            owner_stack.pop()
            return
        if isinstance(node, ast.Lambda):
            deferred_owner = (
                class_owner_stack[-1]
                if scopes[-1].kind == "class" and class_owner_stack[-1] is not None
                else owner_stack[-1]
            )
            owner_stack.append(deferred_owner)
            scopes.append(function_scope(node, eventual_reads=has_eventual_read(node)))
            visit(node.body, scopes)
            scopes.pop()
            owner_stack.pop()
            return
        if isinstance(node, ast.ClassDef):
            for decorator in node.decorator_list:
                visit(decorator, scopes)
            for base in node.bases:
                visit(base, scopes)
            for keyword in node.keywords:
                visit(keyword.value, scopes)
            if node.name == name:
                bind(scopes, node in anchor_nodes)
            elif at_module:
                scopes[-1].bound.add(node.name)
            # Class bodies execute top-down like module bodies: bindings only
            # take effect from their statement on, so the scope starts empty
            # and a right-hand side still sees the enclosing binding.
            _, globals_, nonlocals = _collect_scope_bindings(node.body, skip_node)
            scopes.append(_Scope("class", globals=globals_, nonlocals=nonlocals))
            class_owner_stack.append(
                _class_context(node.name) if at_module else class_owner_stack[-1] or owner_stack[-1]
            )
            for statement in node.body:
                visit(statement, scopes)
            class_owner_stack.pop()
            scopes.pop()
            return
        if isinstance(node, ast.Assign):
            # The right-hand side is evaluated before the targets bind.
            visit(node.value, scopes)
            for target in node.targets:
                visit(target, scopes)
                bind_targets(scopes, target)
            return
        if isinstance(node, ast.AnnAssign):
            if node.value is not None:
                visit(node.value, scopes)
            visit(node.target, scopes)
            bind_targets(scopes, node.target)
            return
        if isinstance(node, ast.AugAssign):
            # The target is loaded before it is rebound.
            if isinstance(node.target, ast.Name) and node.target.id == name and resolves(scopes):
                loads.add((name, owner_stack[-1]))
            visit(node.value, scopes)
            bind_targets(scopes, node.target)
            return
        if isinstance(node, ast.NamedExpr):
            visit(node.value, scopes)
            bind_targets(scopes, node.target, named_expression=True)
            return
        if isinstance(node, (ast.For, ast.AsyncFor)):
            visit(node.iter, scopes)
            bind_targets(scopes, node.target)
            for statement in (*node.body, *node.orelse):
                visit(statement, scopes)
            return
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                visit(item.context_expr, scopes)
                if item.optional_vars is not None:
                    bind_targets(scopes, item.optional_vars)
            for statement in node.body:
                visit(statement, scopes)
            return
        if isinstance(node, ast.ExceptHandler):
            if node.type is not None:
                visit(node.type, scopes)
            if node.name:
                if node.name == name:
                    bind(scopes, False)
                elif at_module:
                    scopes[-1].bound.add(node.name)
            for statement in node.body:
                visit(statement, scopes)
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if (
                node in anchor_nodes
                and isinstance(node, ast.ImportFrom)
                and any(alias.name == "*" for alias in node.names)
            ):
                # A wildcard anchor import binds the tracked name from this
                # statement on, like an explicit import would.
                bind(scopes, True)
            for alias in node.names:
                bound_name = alias.asname or alias.name.split(".", maxsplit=1)[0]
                if bound_name == name:
                    # Anchor registration is scope-ordered: a lazy import
                    # inside a function marks the alias from that statement
                    # on, and a later rebinding clears it again.
                    bind(scopes, node in anchor_nodes)
                elif at_module:
                    scopes[-1].bound.add(bound_name)
            return
        if isinstance(node, ast.Match):
            visit(node.subject, scopes)
            for case in node.cases:
                # Value and class patterns load dotted names while matching.
                visit(case.pattern, scopes)
                # Capture patterns bind before the guard and body run.
                for captured in _pattern_names(case.pattern):
                    if captured == name:
                        bind(scopes, False)
                    elif at_module:
                        scopes[-1].bound.add(captured)
                if case.guard is not None:
                    visit(case.guard, scopes)
                for statement in case.body:
                    visit(statement, scopes)
            return
        if isinstance(node, ast.Global):
            scopes[-1].globals.update(node.names)
            return
        if isinstance(node, ast.Nonlocal):
            scopes[-1].nonlocals.update(node.names)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            # The outermost iterable is evaluated in the enclosing scope.
            if node.generators:
                visit(node.generators[0].iter, scopes)
            targets = {
                child.id
                for generator in node.generators
                for child in ast.walk(generator.target)
                if isinstance(child, ast.Name)
            }
            scopes.append(_Scope("comprehension", targets))
            for generator in node.generators[1:]:
                visit(generator.iter, scopes)
            for generator in node.generators:
                for condition in generator.ifs:
                    visit(condition, scopes)
            if isinstance(node, ast.DictComp):
                visit(node.key, scopes)
                visit(node.value, scopes)
            else:
                visit(node.elt, scopes)
            scopes.pop()
            return
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            chain = _dotted(node)
            if chain is not None and chain.split(".", maxsplit=1)[0] == name and resolves(scopes):
                loads.add((chain, owner_stack[-1]))
            for child in ast.iter_child_nodes(node):
                visit(child, scopes)
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id == name and resolves(scopes):
                loads.add((name, owner_stack[-1]))
            return
        for child in ast.iter_child_nodes(node):
            visit(child, scopes)

    visit(tree, [module_scope])
    return loads


def _reexport_closure(
    index: dict[str, ModuleInfo],
    module: str,
    name: str,
) -> dict[str, set[str]]:
    """Exported names for one function, following aliases and wildcard buckets."""

    exports = {module: {name}}
    grew = True
    while grew:
        grew = False
        for other, info in index.items():
            if info.tree is None:
                continue
            for resolved, imported, alias, node in info.from_imports:
                if node not in info.tree.body:
                    continue
                if imported in exports.get(resolved, set()):
                    names = exports.setdefault(other, set())
                    if alias not in names:
                        names.add(alias)
                        grew = True
            for resolved, node in info.star_imports:
                if node not in info.tree.body:
                    continue
                names = exports.setdefault(other, set())
                for exported in exports.get(resolved, set()):
                    if not exported.startswith("_") and exported not in names:
                        names.add(exported)
                        grew = True
    return exports


_Context = tuple[str, str | None]


def _reference_owners(
    index: dict[str, ModuleInfo],
    exports: dict[str, set[str]],
    defining_module: str,
    name: str,
) -> set[_Context]:
    """Contexts containing loads that resolve to one top-level function."""

    owners: set[_Context] = set()
    for module, info in index.items():
        if info.tree is None:
            continue
        if module == defining_module:
            # Scope/binding-aware resolution inside the defining module:
            # only loads that resolve to the module-level function binding
            # count; recursion inside the function is not a caller.
            chains = _dotted_loads_bound_to_anchors(
                info.tree,
                name,
                frozenset({info.def_nodes[name]}),
                info.def_nodes[name],
            )
            owners.update(
                (module, owner)
                for chain, owner in chains
                if chain == name or chain.startswith(f"{name}.")
            )
            continue
        for resolved, imported, alias, node in info.from_imports:
            if imported in exports.get(resolved, set()):
                # Every import must actually be consumed: the alias has to be
                # loaded in a scope where it resolves to this import binding.
                # An ``__init__`` re-export alone is not a caller either; it
                # only matters once production code consumes the re-export.
                owners.update(
                    (module, owner)
                    for chain, owner in _dotted_loads_bound_to_anchors(
                        info.tree, alias, frozenset({node}), None
                    )
                    if chain == alias or chain.startswith(f"{alias}.")
                )
        for resolved, binding, access_prefix, node in info.module_imports:
            expected = {f"{access_prefix}.{exported}" for exported in exports.get(resolved, set())}
            if not expected:
                continue
            owners.update(
                (module, owner)
                for chain, owner in _dotted_loads_bound_to_anchors(
                    info.tree, binding, frozenset({node}), None
                )
                if any(
                    chain == candidate or chain.startswith(f"{candidate}.")
                    for candidate in expected
                )
            )
        for resolved, node in info.star_imports:
            # A wildcard import binds exported names but is not itself a use.
            for exported in exports.get(resolved, set()):
                if exported.startswith("_"):
                    continue
                owners.update(
                    (module, owner)
                    for chain, owner in _dotted_loads_bound_to_anchors(
                        info.tree, exported, frozenset({node}), None
                    )
                    if chain == exported or chain.startswith(f"{exported}.")
                )
    return owners


def _console_script_entry_points() -> set[str]:
    with Path("pyproject.toml").open("rb") as handle:
        scripts = tomllib.load(handle)["project"]["scripts"]
    return {target.replace(":", ".") for target in scripts.values()}


def _module_initializers(index: dict[str, ModuleInfo], module: str) -> set[_Context]:
    """Package/module bodies executed while importing ``module``."""

    parts = module.split(".")
    return {
        (".".join(parts[:length]), None)
        for length in range(1, len(parts) + 1)
        if ".".join(parts[:length]) in index
    }


def _definition_context(module: str, name: str, node: ast.stmt) -> _Context:
    return module, _class_context(name) if isinstance(node, ast.ClassDef) else name


def _entry_context(index: dict[str, ModuleInfo], qualified_name: str) -> _Context | None:
    """Resolve a registered module or ``pkg.module.function`` context."""

    if qualified_name in index:
        return qualified_name, None
    for module in sorted(index, key=len, reverse=True):
        prefix = f"{module}."
        if not qualified_name.startswith(prefix):
            continue
        function = qualified_name[len(prefix) :]
        if function in index[module].def_nodes:
            return _definition_context(module, function, index[module].def_nodes[function])
    return None


def _reachability_graph(index: dict[str, ModuleInfo]) -> dict[_Context, set[_Context]]:
    """Build production module/function edges using resolved AST loads."""

    edges: defaultdict[_Context, set[_Context]] = defaultdict(set)
    for module, info in index.items():
        edges[(module, None)]
        for name, node in info.def_nodes.items():
            edges[_definition_context(module, name, node)]

        imports = [
            *((resolved, node) for resolved, _, _, node in info.from_imports),
            *((resolved, node) for resolved, _, _, node in info.module_imports),
            *info.star_imports,
        ]
        for resolved, node in imports:
            owner = info.owners.get(node)
            edges[(module, owner)].update(_module_initializers(index, resolved))

    for defining_module, info in index.items():
        for name, node in info.def_nodes.items():
            exports = _reexport_closure(index, defining_module, name)
            target = _definition_context(defining_module, name, node)
            for caller in _reference_owners(index, exports, defining_module, name):
                edges[caller].add(target)
    return dict(edges)


def _reachable_contexts(index: dict[str, ModuleInfo], entry_points: set[str]) -> set[_Context]:
    graph = _reachability_graph(index)
    roots: set[_Context] = {(module, None) for module, info in index.items() if info.has_main_guard}
    for qualified_name in entry_points:
        entry = _entry_context(index, qualified_name)
        if entry is None:
            continue
        roots.add(entry)
        roots.update(_module_initializers(index, entry[0]))

    reachable: set[_Context] = set()
    pending = deque(roots)
    while pending:
        context = pending.popleft()
        if context in reachable:
            continue
        reachable.add(context)
        pending.extend(graph.get(context, set()) - reachable)
    return reachable


def find_test_only_functions(
    src_root: Path,
    src_base: Path,
    tests_root: Path,
    tests_base: Path,
    allow_list: set[str],
) -> list[str]:
    """Public functions not reachable from a registered production entry."""

    src_index = _scan_tree(src_root, src_base)
    # Kept in the signature because callers supply the production and test
    # trees together. Reachability is deliberately independent of whether an
    # unreachable function happens to have a test caller: dead and test-only
    # public functions are both violations.
    _ = tests_root, tests_base
    reachable = _reachable_contexts(src_index, allow_list)
    violations: list[str] = []
    for module, info in sorted(src_index.items()):
        for name in sorted(info.functions):
            qualified_name = f"{module}.{name}"
            if (module, name) not in reachable:
                violations.append(qualified_name)
    return violations


def test_no_public_production_function_is_reachable_only_from_tests() -> None:
    violations = find_test_only_functions(
        SRC_ROOT,
        SRC_BASE,
        TESTS_ROOT,
        TESTS_BASE,
        ALLOW_LIST | _console_script_entry_points(),
    )
    assert not violations, "test-only public production code:\n" + "\n".join(violations)


# --- Synthetic counterexample tests for the resolver itself -----------------


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _synthetic_violations(
    tmp_path: Path,
    src: dict[str, str],
    tests: dict[str, str],
    *,
    entry_points: set[str] | None = None,
) -> list[str]:
    src_root = tmp_path / "src" / "pkg"
    tests_root = tmp_path / "tests"
    _write_tree(src_root, src)
    _write_tree(tests_root, tests)
    return find_test_only_functions(
        src_root,
        tmp_path / "src",
        tests_root,
        tmp_path,
        entry_points or set(),
    )


_TEST_IMPORT = "from pkg.mod import run\n\n\ndef test_run():\n    assert run() == 1\n"


def test_flags_function_referenced_only_from_tests(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {"mod.py": "def run() -> int:\n    return 1\n"},
        {"test_mod.py": _TEST_IMPORT},
    )

    assert violations == ["pkg.mod.run"]


def test_same_named_definition_elsewhere_is_not_a_reference(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "other.py": "def run() -> int:\n    return 2\n\n\nprint(run())\n",
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.other"},
    )

    assert violations == ["pkg.mod.run"]


def test_same_named_local_variable_is_not_a_reference(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "other.py": 'run = "just a variable"\nprint(run)\n',
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.other"},
    )

    assert violations == ["pkg.mod.run"]


def test_comment_mentioning_the_name_is_not_a_reference(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "other.py": "# run is handled elsewhere\nVALUE = 1\n",
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.other"},
    )

    assert violations == ["pkg.mod.run"]


def test_qualified_production_import_marks_reachable(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": "from pkg.mod import run\n\n\ndef go() -> int:\n    return run()\n",
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.go"},
    )

    # A registered production entry reaches ``run`` through ``go``.
    assert violations == []


def test_unused_production_import_is_not_a_reference(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": "from pkg.mod import run\n",
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer"},
    )

    assert violations == ["pkg.mod.run"]


def test_consumer_local_shadow_of_the_import_alias_is_not_a_reference(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "from pkg.mod import run\n"
                "\n\n"
                "def helper() -> str:\n    run = 'local shadow'\n    return run\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.helper"},
    )

    assert violations == ["pkg.mod.run"]


def test_module_import_alias_shadowed_by_a_local_is_not_a_reference(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "import pkg.mod as mod\n"
                "\n\n"
                "def helper() -> object:\n    mod = object()\n    return mod.run\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.helper"},
    )

    assert violations == ["pkg.mod.run"]


def test_unaliased_dotted_module_import_marks_reachable(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": "import pkg.mod\n\n\ndef go() -> int:\n    return pkg.mod.run()\n",
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.go"},
    )

    assert violations == []


def test_unaliased_dotted_module_import_in_tests_is_recognized(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {"mod.py": "def run() -> int:\n    return 1\n"},
        {
            "test_mod.py": (
                "import pkg.mod\n\n\ndef test_run() -> None:\n    assert pkg.mod.run() == 1\n"
            )
        },
    )

    assert violations == ["pkg.mod.run"]


def test_lazy_import_inside_a_function_marks_reachable(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": "def go() -> int:\n    from pkg.mod import run\n    return run()\n",
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.go"},
    )

    assert violations == []


def test_lazy_import_rebound_in_the_same_function_is_not_a_reference(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "def helper() -> int:\n"
                "    from pkg.mod import run\n"
                "    run = lambda: 2\n"
                "    return run()\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.helper"},
    )

    # The lazy import is rebound before the only call, so the call resolves
    # to the lambda, not to ``pkg.mod.run``.
    assert violations == ["pkg.mod.run"]


def test_lazy_module_alias_import_rebound_is_not_a_reference(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "def helper() -> object:\n"
                "    import pkg.mod as mod\n"
                "    mod = object()\n"
                "    return mod.run\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.helper"},
    )

    assert violations == ["pkg.mod.run"]


def test_init_reexport_and_bucket_import_mark_reachable(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "__init__.py": "from pkg.mod import run\n",
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": "from pkg import run\n\n\ndef go() -> int:\n    return run()\n",
        },
        {"test_mod.py": "from pkg import run\n\n\ndef test_run():\n    assert run() == 1\n"},
        entry_points={"pkg.consumer.go"},
    )

    assert violations == []


def test_init_reexport_alone_is_not_a_reference(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "__init__.py": "from pkg.mod import run\n",
            "mod.py": "def run() -> int:\n    return 1\n",
        },
        {"test_mod.py": "from pkg import run\n\n\ndef test_run():\n    assert run() == 1\n"},
        entry_points={"pkg"},
    )

    # The re-export itself is not a caller; nothing in production consumes
    # the bucket import, so the function remains test-only.
    assert violations == ["pkg.mod.run"]


def test_relative_init_reexport_and_test_bucket_import_are_resolved(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "__init__.py": "from .mod import run\n",
            "mod.py": "def run() -> int:\n    return 1\n",
        },
        {"test_mod.py": "from pkg import run\n\n\ndef test_run():\n    assert run() == 1\n"},
        entry_points={"pkg"},
    )

    assert violations == ["pkg.mod.run"]


def test_unused_star_import_is_not_a_reference(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": "from pkg.mod import *\n",
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer"},
    )

    assert violations == ["pkg.mod.run"]


def test_star_import_with_a_genuine_load_marks_reachable(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": "from pkg.mod import *\n\n\ndef go() -> int:\n    return run()\n",
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.go"},
    )

    assert violations == []


def test_star_import_after_function_definition_marks_reachable(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": "def go() -> int:\n    return run()\n\n\nfrom pkg.mod import *\n",
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.go"},
    )

    assert violations == []


def test_global_rebinding_clears_the_module_import_anchor(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "from pkg.mod import run\n"
                "\n\n"
                "def helper() -> int:\n"
                "    global run\n"
                "    run = lambda: 2\n"
                "    return run()\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.helper"},
    )

    # The ``global`` assignment rebinds the module-level import, so the call
    # resolves to the lambda, not to ``pkg.mod.run``.
    assert violations == ["pkg.mod.run"]


def test_global_lazy_import_binds_the_module_namespace(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "def helper() -> int:\n"
                "    global run\n"
                "    from pkg.mod import run\n"
                "    return run()\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.helper"},
    )

    # The import under ``global`` binds the module namespace to the anchor,
    # so the call is a genuine production use.
    assert violations == []


def test_nested_global_rebinding_clears_the_module_import_anchor(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "from pkg.mod import run\n"
                "\n\n"
                "def outer() -> int:\n"
                "    def inner() -> int:\n"
                "        global run\n"
                "        run = lambda: 2\n"
                "        return run()\n"
                "    return inner()\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.outer"},
    )

    assert violations == ["pkg.mod.run"]


def test_nested_global_lazy_import_binds_the_module_namespace(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "def outer() -> int:\n"
                "    def inner() -> int:\n"
                "        global run\n"
                "        from pkg.mod import run\n"
                "        return run()\n"
                "    return inner()\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.outer"},
    )

    assert violations == []


def test_nonlocal_load_before_rebinding_resolves_to_the_enclosing_import(
    tmp_path: Path,
) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "def outer() -> int:\n"
                "    from pkg.mod import run\n"
                "\n"
                "    def inner() -> int:\n"
                "        nonlocal run\n"
                "        result = run()\n"
                "        run = lambda: 2\n"
                "        return result\n"
                "\n"
                "    return inner()\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.outer"},
    )

    assert violations == []


def test_call_before_nonlocal_target_import_is_not_a_reference(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "def outer() -> int:\n"
                "    run()\n"
                "    from pkg.mod import run\n"
                "\n"
                "    def inner() -> int:\n"
                "        nonlocal run\n"
                "        run = lambda: 2\n"
                "        return run()\n"
                "\n"
                "    return inner()\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.outer"},
    )

    assert violations == ["pkg.mod.run"]


def test_class_attribute_assignment_rhs_reads_the_enclosing_import(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "from pkg.mod import run\n" "\n\n" "class API:\n" "    run = staticmethod(run)\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer"},
    )

    # Class bodies execute top-down: the right-hand side still resolves to
    # the module-level import, which is a genuine production use.
    assert violations == []


def test_match_capture_does_not_reference_the_import(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "from pkg.mod import run\n"
                "\n\n"
                "def helper(subject: object) -> object:\n"
                "    match subject:\n"
                "        case run:\n"
                "            return run\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.helper"},
    )

    # ``case run:`` captures into a local ``run``; the body load resolves to
    # the capture, not to ``pkg.mod.run``.
    assert violations == ["pkg.mod.run"]


def test_match_value_pattern_load_marks_reachable(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "import pkg.mod as mod\n"
                "\n\n"
                "def helper(subject: object) -> bool:\n"
                "    match subject:\n"
                "        case mod.run:\n"
                "            return True\n"
                "    return False\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.helper"},
    )

    assert violations == []


def test_match_class_pattern_load_marks_reachable(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "import pkg.mod as mod\n"
                "\n\n"
                "def helper(subject: object) -> bool:\n"
                "    match subject:\n"
                "        case mod.run():\n"
                "            return True\n"
                "    return False\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.helper"},
    )

    assert violations == []


def test_module_attribute_access_marks_reachable(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": "import pkg.mod as mod\n\n\ndef go() -> int:\n    return mod.run()\n",
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.go"},
    )

    assert violations == []


def test_same_module_local_variable_is_not_a_reference(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": (
                "def run() -> int:\n    return 1\n"
                "\n\n"
                "def helper() -> str:\n    run = 'local shadow'\n    return run\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.mod.helper"},
    )

    # The sibling function's local ``run`` shadows the module-level function,
    # so even a registered ``helper`` entry does not reach ``run``.
    assert violations == ["pkg.mod.run"]


def test_same_module_genuine_caller_marks_reachable(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {"mod.py": "def run() -> int:\n    return 1\n\n\ndef go() -> int:\n    return run()\n"},
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.mod.go"},
    )

    assert violations == []


def test_sibling_shadow_does_not_hide_a_genuine_same_module_caller(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": (
                "def run() -> int:\n    return 1\n"
                "\n\n"
                "def go() -> int:\n    return run()\n"
                "\n\n"
                "def _helper() -> str:\n    run = 'local shadow'\n    return run\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.mod.go"},
    )

    assert violations == []


def test_module_level_rebinding_shadows_the_function(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {"mod.py": "def run() -> int:\n    return 1\n\n\nrun = 'shadowed'\nprint(run)\n"},
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.mod"},
    )

    assert violations == ["pkg.mod.run"]


def test_call_before_module_level_rebinding_still_counts(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": (
                "def run() -> int:\n    return 1\n"
                "\n\n"
                "result = run()\n"
                "run = 'shadowed afterwards'\n"
                "print(run)\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.mod"},
    )

    assert violations == []


def test_global_declaration_load_marks_reachable(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": (
                "def run() -> int:\n    return 1\n"
                "\n\n"
                "def go() -> int:\n    global run\n    return run()\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.mod.go"},
    )

    assert violations == []


def test_comprehension_target_is_not_a_reference(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": (
                "def run() -> int:\n    return 1\n" "\n\n" "values = [run for run in (1, 2)]\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.mod"},
    )

    assert violations == ["pkg.mod.run"]


def test_dead_production_wrapper_does_not_make_target_reachable(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "from pkg.mod import run\n" "\n\n" "def dead() -> int:\n" "    return run()\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
    )

    assert violations == ["pkg.consumer.dead", "pkg.mod.run"]


def test_dead_class_method_does_not_make_target_reachable(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "from pkg.mod import run\n"
                "\n\n"
                "class Dead:\n"
                "    def invoke(self) -> int:\n"
                "        return run()\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer"},
    )

    assert violations == ["pkg.mod.run"]


def test_reachable_class_exposes_its_method_references(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "from pkg.mod import run\n"
                "\n\n"
                "class Runner:\n"
                "    def invoke(self) -> int:\n"
                "        return run()\n"
                "\n\n"
                "def main() -> Runner:\n"
                "    return Runner()\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.main"},
    )

    assert violations == []


def test_completely_unreferenced_public_function_is_a_violation(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {"mod.py": "def abandoned() -> int:\n    return 1\n"},
        {},
    )

    assert violations == ["pkg.mod.abandoned"]


def test_closure_defined_before_lazy_import_resolves_when_called_later(
    tmp_path: Path,
) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "def factory():\n"
                "    def inner() -> int:\n"
                "        return run()\n"
                "    from pkg.mod import run\n"
                "    return inner\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.factory"},
    )

    assert violations == []


def test_lambda_defined_before_lazy_import_resolves_when_called_later(
    tmp_path: Path,
) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "def factory():\n"
                "    inner = lambda: run()\n"
                "    from pkg.mod import run\n"
                "    return inner\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.factory"},
    )

    assert violations == []


def test_closure_called_before_lazy_import_does_not_resolve_eventual_binding(
    tmp_path: Path,
) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "def factory() -> int:\n"
                "    def inner() -> int:\n"
                "        return run()\n"
                "    result = inner()\n"
                "    from pkg.mod import run\n"
                "    return result\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.factory"},
    )

    assert violations == ["pkg.mod.run"]


def test_lambda_called_before_lazy_import_does_not_resolve_eventual_binding(
    tmp_path: Path,
) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "def factory() -> int:\n"
                "    inner = lambda: run()\n"
                "    result = inner()\n"
                "    from pkg.mod import run\n"
                "    return result\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.factory"},
    )

    assert violations == ["pkg.mod.run"]


def test_class_nonlocal_rebinding_clears_the_enclosing_import(
    tmp_path: Path,
) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "def factory() -> int:\n"
                "    from pkg.mod import run\n"
                "    class Namespace:\n"
                "        nonlocal run\n"
                "        run = lambda: 2\n"
                "    return run()\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.factory"},
    )

    assert violations == ["pkg.mod.run"]


def test_class_nonlocal_import_binds_the_enclosing_scope(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "def factory() -> int:\n"
                "    run = lambda: 2\n"
                "    class Namespace:\n"
                "        nonlocal run\n"
                "        from pkg.mod import run\n"
                "    return run()\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.factory"},
    )

    assert violations == []


def test_aliased_package_reexport_marks_bucket_use_reachable(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "__init__.py": "from .mod import run as execute\n",
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "from pkg import execute\n\n\ndef go() -> int:\n    return execute()\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.go"},
    )

    assert violations == []


def test_star_package_reexport_marks_bucket_use_reachable(tmp_path: Path) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "__init__.py": "from .mod import *\n",
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": "from pkg import run\n\n\ndef go() -> int:\n    return run()\n",
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.go"},
    )

    assert violations == []


def test_comprehension_named_expression_rebinds_the_enclosing_module(
    tmp_path: Path,
) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "from pkg.mod import run\n"
                "values = [(run := value) for value in (lambda: 2,)]\n"
                "result = run()\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer"},
    )

    assert violations == ["pkg.mod.run"]


def test_comprehension_named_expression_rebinds_the_enclosing_function(
    tmp_path: Path,
) -> None:
    violations = _synthetic_violations(
        tmp_path,
        {
            "mod.py": "def run() -> int:\n    return 1\n",
            "consumer.py": (
                "def go() -> int:\n"
                "    from pkg.mod import run\n"
                "    values = [(run := value) for value in (lambda: 2,)]\n"
                "    return run()\n"
            ),
        },
        {"test_mod.py": _TEST_IMPORT},
        entry_points={"pkg.consumer.go"},
    )

    assert violations == ["pkg.mod.run"]
