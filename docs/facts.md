# Canonical facts

<!-- trace:v1 id=doc.canonical-facts work=WORK-durable-knowledge-nodes-and-canonical-facts -->

Single-source values with tracked provenance (addendum Sections 94-106).
Verify with `trace facts --verify`.

## TraceLayer release version

<!-- trace:v1 id=VALUE-TL-VERSION type=value canonical_source=pyproject.toml::project.version value=0.2.40 work=WORK-durable-knowledge-nodes-and-canonical-facts -->

Authoritative definition: `pyproject.toml::project.version`.

Mirrors (kept in sync by the release process):
`src/tracelayer/__init__.py::__version__`, `adapters/oh-my-pi/package.json::version`.

## Minimum Python version

<!-- trace:v1 id=VALUE-TL-PYTHON-MIN type=value canonical_source=pyproject.toml::project.requires-python value=">=3.12" work=WORK-durable-knowledge-nodes-and-canonical-facts -->

Authoritative definition: `pyproject.toml::project.requires-python`.
