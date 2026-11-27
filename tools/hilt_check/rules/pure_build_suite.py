"""``hilt/pure-build-suite`` — ``build_suite`` is a table of contents, not a program.

A suite declares its composition the way a document declares its outline: one
``return`` of nested ``self.test`` / ``self.sequence`` / ``self.parallel`` calls. That
declarativeness is a defended convention, not something the language makes impossible
to violate, which is exactly why a rule exists for it. Logic in ``build_suite`` hides
which claims a run will make until the run happens; the Floor Technician reading the
summary tree, and the reviewer reading the source, both deserve to see the same list.

A value the outline uses more than once may be given a name first. A suite built
from a bill of materials reads it once and builds the board whose slots several parts
are fitted to, and one ``return`` cannot bind either:

    bom = self._read_bom()
    board = Board(name="board", part=bom.board)
    return self.parallel([board, Memory(name="memory", slots=board.slots)])

Naming a value is not deciding anything, so a plain assignment to a local name is
allowed before the ``return``, and the expression it names is held to the same limits
as the returned one.

Flagged:

- any statement other than the docstring, plain assignments to local names, and a
  single ``return`` after them. An assignment to anything else (``self.x``, a
  subscript), an augmented assignment, and an assignment after the ``return`` are
  flagged like any other statement;
- inside the returned expression or an assigned one: conditionals (``x if c else y``,
  ``and``/``or``, comparisons), comprehensions that carry an ``if`` filter, lambdas,
  ``await``, ``yield``, and walrus assignments.

Not flagged: calls (composition factories, child-suite constructors, name helpers),
f-strings, literals, attribute and subscript reads, unary/binary operators on them,
and an unfiltered comprehension. The enumeration idiom (issue #110) stamps one leaf per
core, rail, or channel from a single method:
``[self.test(f"cpu{i}", self._cpu, cpu=i) for i in range(n)]``.
That maps a known population onto leaves; it does not choose among them. A comprehension
with an ``if`` clause chooses, and is flagged. The rule polices control flow, not
vocabulary — a suite may compose children from a helper or enumerate them; it may not
decide at build time which children exist.

Tier-2 counterpart: ``__init_subclass__`` requires ``build_suite`` to exist with the
right shape; tier 1 refuses anything but a ``TestComponent`` from it.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable

from hilt_check.model import Rule, SuiteModule, Violation

_BUILD_SUITE = "build_suite"

_LOGIC_EXPRESSIONS: tuple[type[ast.AST], ...] = (
    ast.IfExp,
    ast.BoolOp,
    ast.Compare,
    ast.Lambda,
    ast.Await,
    ast.Yield,
    ast.YieldFrom,
    ast.NamedExpr,
)

_COMPREHENSIONS = (
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


class PureBuildSuiteRule(Rule):
    """Flag control flow inside ``build_suite``, as statements or as expressions."""

    code = "hilt/pure-build-suite"
    summary = "build_suite names values, returns one composition expression, and holds no logic"

    def check(self, module: SuiteModule) -> Iterable[Violation]:
        """Report statements and control flow inside ``build_suite``.

        See the ``hilt/pure-build-suite`` row in ``docs/hilt-check.md``.
        """
        for klass in module.hilt_classes():
            for member in klass.body:
                is_def = isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
                if is_def and member.name == _BUILD_SUITE:
                    yield from self._check_body(module, member)

    def _check_body(
        self, module: SuiteModule, function: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> Iterable[Violation]:
        statements = list(function.body)
        if statements and _is_docstring(statements[0]):
            statements = statements[1:]
        returns = [s for s in statements if isinstance(s, ast.Return)]
        first_return = statements.index(returns[0]) if returns else len(statements)
        for position, statement in enumerate(statements):
            if isinstance(statement, ast.Return):
                continue
            named = _local_assignment_value(statement) if position < first_return else None
            if named is not None:
                yield from self._check_expression(module, named)
                continue
            yield module.violation(
                statement,
                self.code,
                f"logic in build_suite: {type(statement).__name__} statement; "
                "build_suite is a table of contents — name a value with a plain local "
                "assignment if the outline needs one, and declare the composition in "
                "one return",
            )
        for statement in returns[1:]:
            yield module.violation(
                statement, self.code, "logic in build_suite: more than one return"
            )
        for statement in returns[:1]:
            if statement.value is not None:
                yield from self._check_expression(module, statement.value)

    def _check_expression(self, module: SuiteModule, expression: ast.expr) -> Iterable[Violation]:
        for node in ast.walk(expression):
            if isinstance(node, _COMPREHENSIONS) and any(g.ifs for g in node.generators):
                yield module.violation(
                    node,
                    self.code,
                    f"logic in build_suite: {type(node).__name__} filters the composition "
                    "at build time; enumerate a known population, do not choose among it",
                )
                continue
            if isinstance(node, _LOGIC_EXPRESSIONS):
                yield module.violation(
                    node,
                    self.code,
                    f"logic in build_suite: {type(node).__name__} decides the composition "
                    "at build time; the suite must declare its claims, not compute them",
                )


def _local_assignment_value(statement: ast.stmt) -> ast.expr | None:
    """Return the expression a plain local assignment names; ``None`` for anything else.

    ``x = ...``, ``x: T = ...``, and ``a, b = ...`` name locals. An assignment to an
    attribute or a subscript changes something that already exists, and a bare
    annotation names nothing.
    """
    if isinstance(statement, ast.Assign) and all(
        _is_local_target(target) for target in statement.targets
    ):
        return statement.value
    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
        return statement.value
    return None


def _is_local_target(target: ast.expr) -> bool:
    if isinstance(target, ast.Tuple | ast.List):
        return all(_is_local_target(element) for element in target.elts)
    return isinstance(target, ast.Name)


def _is_docstring(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )
