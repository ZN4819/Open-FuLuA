from pathlib import Path


def ensure_distinct_paths(source: Path, output: Path) -> None:
    if source.resolve() == output.resolve():
        raise ValueError("OUTPUT_MUST_NOT_OVERWRITE_SOURCE")
