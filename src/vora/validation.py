from __future__ import annotations

import re


VALIDATION_COMMAND_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in [
        r"(^|\s)(pytest|py\.test|tox|nox)(\s|$)",
        r"(^|\s)python(3)?\s+-m\s+(pytest|unittest|compileall|py_compile)(\s|$)",
        r"(^|\s)(ruff|mypy|pyright|pylint|flake8|black\s+--check|isort\s+--check)(\s|$)",
        r"(^|\s)node\s+--check(\s|$)",
        r"(^|\s)node\s+-c(\s|$)",
        r"(^|\s)(npm|pnpm|yarn|bun)\s+(run\s+)?(test|lint|typecheck|check|build|compile)(\s|$|:)",
        r"(^|\s)(npx|pnpm\s+exec|yarn\s+exec|bunx)\s+(tsc|eslint|biome|prettier|vitest|jest)(\s|$)",
        r"(^|\s)(tsc|eslint|biome|prettier|vitest|jest)(\s|$)",
        r"(^|\s)go\s+(test|vet|build)(\s|$)",
        r"(^|\s)cargo\s+(test|check|clippy|build)(\s|$)",
        r"(^|\s)(mvn|./mvnw)\s+.*\b(test|verify|package|compile)\b",
        r"(^|\s)(gradle|./gradlew)\s+.*\b(test|check|build|compileJava|compileKotlin)\b",
        r"(^|\s)javac(\s|$)",
        r"(^|\s)dotnet\s+(test|build)(\s|$)",
        r"(^|\s)swift\s+(test|build)(\s|$)",
        r"(^|\s)xcodebuild\s+.*\b(test|build)\b",
        r"(^|\s)php\s+-l(\s|$)",
        r"(^|\s)composer\s+(test|validate|check)(\s|$)",
        r"(^|\s)ruby\s+-c(\s|$)",
        r"(^|\s)(bundle\s+exec\s+)?(rspec|rubocop)(\s|$)",
        r"(^|\s)(shellcheck|sh\s+-n|bash\s+-n|zsh\s+-n)(\s|$)",
        r"(^|\s)(make|cmake|ctest)\s+.*\b(test|check|lint|build|compile)\b",
    ]
)


def looks_like_validation_command(command: str) -> bool:
    normalized = normalize_command_text(command)
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in VALIDATION_COMMAND_PATTERNS)


def looks_like_inline_test_script(content: str) -> bool:
    normalized = normalize_command_text(content)
    if not normalized:
        return False
    markers = [
        "assert ",
        "assert(",
        "expect(",
        "describe(",
        "it(",
        "test(",
        "unittest",
        "pytest",
        "should",
        "throw new error",
        "process.exit(1)",
    ]
    return any(marker in normalized for marker in markers)


def normalize_command_text(command: str) -> str:
    return re.sub(r"\s+", " ", command.replace("\\\n", " ")).strip().lower()
