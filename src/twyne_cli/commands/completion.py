"""Shell completion script generator for Twyne CLI."""

import click


@click.command()
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def completion(shell: str):
    """Generate shell completion script.

    \b
    Setup instructions:
      bash:  eval "$(twyne completion bash)"
             Or add to ~/.bashrc for persistence.
      zsh:   eval "$(twyne completion zsh)"
             Or add to ~/.zshrc for persistence.
      fish:  twyne completion fish | source
             Or save to ~/.config/fish/completions/twyne.fish
    """
    from click.shell_completion import get_completion_class

    cls = get_completion_class(shell)
    if cls is None:
        click.echo(f"Unsupported shell: {shell}", err=True)
        raise SystemExit(1)

    comp = cls(cli=None, ctx_args={}, prog_name="twyne", complete_var="_TWYNE_COMPLETE")
    click.echo(comp.source())
