class ConfigError(Exception):
    """Invalid or unresolved configuration. Message lists every problem found."""

    def __init__(self, problems):
        if isinstance(problems, str):
            problems = [problems]
        self.problems = list(problems)
        super().__init__("\n".join(f"  - {p}" for p in self.problems))
