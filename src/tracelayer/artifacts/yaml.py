"""YAML section extraction and marker attachment."""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from tracelayer.protocol import MarkerHit


@dataclass
class YamlSection:
    key_path: str  # "top.key"
    start_line: int  # 1-based inclusive
    end_line: int  # 1-based inclusive


def extract_yaml_sections(text: str) -> list[YamlSection]:
    """Top-level (and one nested level) mapping sections with line spans.

    Uses the composed node tree (marks equivalent to ``yaml.parse()`` events):
    a top-level key spans from its key's start line to its value's end line,
    and first-level nested keys are reported as ``top.key`` with their own
    spans. Documents that are not mappings, and YAML errors, yield [] so the
    caller can fall back to file-level attachment.
    """
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return []
    if not isinstance(root, yaml.MappingNode):
        return []
    sections: list[YamlSection] = []
    for key_node, value_node in root.value:
        if not isinstance(key_node, yaml.ScalarNode):
            continue
        top = str(key_node.value)
        sections.append(
            YamlSection(
                key_path=top,
                start_line=key_node.start_mark.line + 1,
                end_line=value_node.end_mark.line + 1,
            )
        )
        if isinstance(value_node, yaml.MappingNode):
            for sub_key, sub_value in value_node.value:
                if not isinstance(sub_key, yaml.ScalarNode):
                    continue
                sections.append(
                    YamlSection(
                        key_path=f"{top}.{sub_key.value}",
                        start_line=sub_key.start_mark.line + 1,
                        end_line=sub_value.end_mark.line + 1,
                    )
                )
    return sections


def attach_sections(
    sections: list[YamlSection], hits: list[MarkerHit]
) -> list[tuple[YamlSection, MarkerHit]]:
    """Pair every section whose line span contains a marker line.

    Overlapping sections (``top`` and ``top.key``) both match; all
    containments are returned, ordered by section start line, then key path,
    then marker line (deterministic).
    """
    pairs = [
        (section, hit)
        for section in sections
        for hit in hits
        if section.start_line <= hit.line <= section.end_line
    ]
    pairs.sort(key=lambda p: (p[0].start_line, p[0].key_path, p[1].line))
    return pairs
