"""Trace capture pipeline: records, WAL, redactor, transports, uploader.

Every component here is selected by config and accessed through a Protocol
defined in the relevant submodule, so each piece can be swapped independently
(e.g. RuleSetRedactor → ModelRedactor, HTTPSTransport → QueueTransport).
"""
