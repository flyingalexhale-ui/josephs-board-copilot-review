"""Safe evaluation of a strategy's rule strings, e.g. "rsi_14 < 30 and close > sma_50".

Deliberately does NOT use Python's eval()/exec() on the rule text. A strategy file is just
a YAML file you or I write, so the risk here isn't "someone attacks you" -- it's "a typo in
a rule string does something you didn't intend, invisibly." This restricts what a rule can
possibly do to a short, fixed grammar (comparisons, and/or/not, +-*/, parentheses, numbers,
and column-name lookups), by walking Python's own parser output (the `ast` module) and
rejecting anything outside that whitelist -- so a rule can never call a function, import
anything, or do anything but compare numbers.
"""
import ast
import operator

_ALLOWED_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
}
_ALLOWED_CMPOPS = {
    ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.Eq: operator.eq, ast.NotEq: operator.ne,
}


class RuleError(Exception):
    pass


def _eval_node(node, row):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, row)
    if isinstance(node, ast.BoolOp):
        vals = [_eval_node(v, row) for v in node.values]
        if isinstance(node.op, ast.And):
            result = True
            for v in vals:
                result = result and v
            return result
        if isinstance(node.op, ast.Or):
            result = False
            for v in vals:
                result = result or v
            return result
        raise RuleError(f'unsupported boolean operator: {node.op}')
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval_node(node.operand, row)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval_node(node.operand, row)
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, row)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, row)
            fn = _ALLOWED_CMPOPS.get(type(op))
            if fn is None:
                raise RuleError(f'unsupported comparison operator: {op}')
            if not fn(left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.BinOp):
        fn = _ALLOWED_BINOPS.get(type(node.op))
        if fn is None:
            raise RuleError(f'unsupported arithmetic operator: {node.op}')
        return fn(_eval_node(node.left, row), _eval_node(node.right, row))
    if isinstance(node, ast.Name):
        if node.id not in row:
            raise RuleError(
                f"rule references '{node.id}', which isn't an available column at this "
                f"point (available: {sorted(row.keys())}). Check the indicator name/period "
                f"in your strategy file matches what's configured under 'indicators:'.")
        return row[node.id]
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, bool)):
        return node.value
    raise RuleError(
        f'unsupported expression: {ast.dump(node)}. Rules may only use column names, '
        f'numbers, +-*/, comparisons (<,<=,>,>=,==,!=), and and/or/not.')


def compile_rule(expr: str):
    """Parses `expr` once and returns a function(row_dict) -> bool you can call repeatedly
    (row_dict maps column names, e.g. 'close', 'rsi_14', to their current numeric value).
    Raises RuleError immediately (at load time, not at evaluation time) if the expression
    uses anything outside the allowed grammar -- so a bad strategy file fails fast, with a
    clear message, before you ever run it against real or paper money."""
    try:
        tree = ast.parse(expr, mode='eval')
    except SyntaxError as e:
        raise RuleError(f"could not parse rule {expr!r}: {e}")

    # A lightweight static check pass: walk the tree once, ignoring
    # "missing column" errors (those are only knowable at real evaluation time) but still
    # catching genuinely disallowed syntax (a function call, a lambda, an import, etc.).
    class _StaticChecker(ast.NodeVisitor):
        ALLOWED = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not,
                   ast.USub, ast.Compare, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq,
                   ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Name, ast.Constant,
                   ast.Load)

        def generic_visit(self, node):
            if not isinstance(node, self.ALLOWED):
                raise RuleError(
                    f"rule {expr!r} uses something that isn't allowed "
                    f"({type(node).__name__}). Rules may only use column names, numbers, "
                    f"+-*/, comparisons, and and/or/not -- no function calls, no attribute "
                    f"access, nothing else.")
            super().generic_visit(node)

    _StaticChecker().visit(tree)

    def run(row):
        return bool(_eval_node(tree.body, row))

    run.source = expr
    run.names = sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name)})
    return run
