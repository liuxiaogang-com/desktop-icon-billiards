"""Static checks for the Tk app, which cannot be instantiated without a live desktop.

``poll()`` runs every 50 ms and reads a set of ``StringVar`` hooks that ``__init__`` binds
from the panel. A missing binding there raises ``AttributeError`` only on a real desktop —
that is how ``remaining_title`` shipped — so the class body is inspected instead: every
``self.<name>`` that is read must also be assigned or defined somewhere in the class.
"""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
UI = ROOT / 'dib' / 'ui.py'
PANEL = ROOT / 'dib' / 'panel.py'


def module_tree(path):
    return ast.parse(path.read_text(encoding='utf-8'))


def find_class(tree, name, path):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f'{name} not found in {path.name}')


def attribute_usage(cls):
    """Return ``(bound, read)`` where ``read`` maps attribute -> first line it is read on."""
    bound, read = set(), {}
    # Class level constants (``DEFAULT_LOOK``) are legitimate ``self.`` reads too.
    for node in cls.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                bound.add(target.id)
    for node in ast.walk(cls):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(node.name)
        if not isinstance(node, ast.Attribute):
            continue
        owner = node.value
        if not (isinstance(owner, ast.Name) and owner.id == 'self'):
            continue
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.attr)
        elif isinstance(node.ctx, ast.Load):
            read.setdefault(node.attr, node.lineno)
    return bound, read


class AppBindingTests(unittest.TestCase):
    def test_every_attribute_the_app_reads_is_bound(self):
        cls = find_class(module_tree(UI), 'DesktopBilliardsApp', UI)
        bound, read = attribute_usage(cls)
        missing = [f'self.{name} (line {line})' for name, line in sorted(read.items())
                   if name not in bound]
        self.assertEqual(missing, [], 'DesktopBilliardsApp reads unbound attributes: '
                                      + '; '.join(missing))

    def test_every_attribute_the_panel_reads_is_bound(self):
        cls = find_class(module_tree(PANEL), 'MainPanel', PANEL)
        bound, read = attribute_usage(cls)
        missing = [f'self.{name} (line {line})' for name, line in sorted(read.items())
                   if name not in bound]
        self.assertEqual(missing, [], 'MainPanel reads unbound attributes: '
                                      + '; '.join(missing))

    def test_stat_hooks_are_wired_from_the_panel(self):
        """The six statistics hooks are copied from the panel in ``__init__``."""
        cls = find_class(module_tree(UI), 'DesktopBilliardsApp', UI)
        init = next(node for node in cls.body
                    if isinstance(node, ast.FunctionDef) and node.name == '__init__')
        bound = {node.attr for node in ast.walk(init)
                 if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                 and node.value.id == 'self' and isinstance(node.ctx, ast.Store)}
        for name in ('time_title', 'time_value', 'count_title', 'count_value',
                     'remaining_title', 'remaining_value'):
            self.assertIn(name, bound, f'__init__ never binds self.{name}')


if __name__ == '__main__':
    unittest.main()
