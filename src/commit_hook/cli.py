"""CLI entrypoint for commit-hook."""

import click


@click.group()
@click.version_option(version="0.1.0", prog_name="commit-hook")
def main() -> None:
    """AI-powered commit message validator.

    Validate commit messages against configurable rules using LLMs.
    """
    pass


if __name__ == "__main__":
    main()
