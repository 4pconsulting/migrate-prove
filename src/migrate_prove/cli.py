from __future__ import annotations

from pathlib import Path

import typer

from migrate_prove.connectors import SqlAlchemyConnector
from migrate_prove.contract import load_contract, load_suite
from migrate_prove.demo.banking import seed_banking
from migrate_prove.engine import ValidationEngine
from migrate_prove.reporting import print_console, write_html, write_json
from migrate_prove.secrets import MissingEnvError, load_env, redact_url, resolve_connection

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Prove migrated data against an STM contract.")


@app.command()
def compile(
    contract: Path = typer.Argument(..., exists=True, help="STM YAML contract"),
) -> None:
    """Validate that a Source-to-Target Mapping file is a well-formed contract."""
    loaded = load_contract(contract)
    typer.echo(f"OK  {loaded.name}: {len(loaded.entities)} entities")
    for entity in loaded.entities:
        typer.echo(f"  - {entity.name}  risk={entity.risk.value}  mappings={len(entity.mappings)}")


@app.command()
def run(
    suite: Path = typer.Argument(..., exists=True, help="Suite YAML (connections + contract path)"),
    entity: list[str] = typer.Option(None, "--entity", "-e", help="Limit to named entities"),
    env_file: Path | None = typer.Option(
        None,
        "--env-file",
        help="Optional .env path (default: discover .env from the working directory)",
        exists=True,
        dir_okay=False,
    ),
) -> None:
    """Run the multi-tier validation pipeline and write an evidence scorecard."""
    load_env(env_file)
    config = load_suite(suite)
    base = suite.parent
    contract = load_contract((base / config.contract).resolve())
    try:
        source_url = resolve_connection(config.source, base)
        target_url = resolve_connection(config.target, base)
    except MissingEnvError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(f"source: {redact_url(source_url)}")
    typer.echo(f"target: {redact_url(target_url)}")

    source = SqlAlchemyConnector(source_url, name="source")
    target = SqlAlchemyConnector(target_url, name="target")
    engine = ValidationEngine(source, target)
    report = engine.run(
        contract,
        levels=config.levels,
        continue_on_fail=config.continue_on_fail,
        entities=entity or None,
    )
    output_dir = (base / config.output_dir).resolve()
    write_json(report, output_dir / "report.json")
    write_html(report, contract, output_dir / "report.html")
    print_console(report)
    typer.echo(f"Evidence: {output_dir / 'report.html'}")
    if report.failed:
        raise typer.Exit(code=1)


@app.command("seed-demo")
def seed_demo(
    folder: Path = typer.Argument(
        Path("examples/banking_demo"),
        help="Directory that will receive source.db and target.db",
    ),
) -> None:
    """Load the banking demo with planted defects that COUNT(*) cannot see."""
    folder.mkdir(parents=True, exist_ok=True)
    source_path, target_path = seed_banking(folder)
    typer.echo(f"Seeded {source_path} and {target_path}")
