"""Run avbtool with read-ahead for large images on a WSL Windows mount."""

import builtins
import sys

import avbtool


def open_with_read_ahead(filename, mode="r"):
    return builtins.open(filename, mode, buffering=4 * 1024 * 1024)


avbtool.open = open_with_read_ahead
avbtool.AvbTool().run(sys.argv)
