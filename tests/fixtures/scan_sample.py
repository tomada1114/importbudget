"""Sample source for AST scanning tests. Parsed only, never imported."""

import os.path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import dead_only
else:
    import live_branch

if False:
    import also_dead

import importlib

from . import sibling
from .deeper import thing
from .. import parent_thing


def lazy_user():
    """Function-level imports run only if the function is called."""
    import json

    return json.dumps(importlib.import_module("csv").__name__), thing, sibling


def unknown_dynamic(name):
    """Dynamic import whose argument is not a literal."""
    return importlib.import_module(name), os.path, dead_only, also_dead, live_branch, parent_thing


import typing

if typing.TYPE_CHECKING:
    import dead_via_attribute

from ... import too_far_up

CALLABLE_TABLE = {"noop": lambda name: name}
RESULT = CALLABLE_TABLE["noop"]("not an import call")
