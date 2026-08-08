"""Classify CLI input without duplicating pipeline or DAG validation."""

from enum import StrEnum
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, ScalarNode


class DocumentKind(StrEnum):
    PIPELINE = "pipeline"
    DAG = "dag"


def detect_document_kind(path: Path) -> DocumentKind:
    """Identify the established DAG shape from safe top-level YAML nodes.

    Detection deliberately falls back to ``PIPELINE`` when the file cannot
    be inspected. The existing pipeline loader then preserves its historical
    missing-file, unreadable-file, and malformed-YAML diagnostics unchanged.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return DocumentKind.PIPELINE
    try:
        document = yaml.compose(content, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return DocumentKind.PIPELINE
    if not isinstance(document, MappingNode):
        return DocumentKind.PIPELINE

    keys = {
        key.value
        for key, _value in document.value
        if isinstance(key, ScalarNode) and isinstance(key.value, str)
    }
    if "tasks" in keys and "source" not in keys and "destination" not in keys:
        return DocumentKind.DAG
    return DocumentKind.PIPELINE
