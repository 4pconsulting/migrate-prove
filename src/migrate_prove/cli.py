from __future__ import annotations

from pathlib import Path

import typer

from migrate_prove.connectors import SqlAlchemyConnector
from migrate_prove.contract import load_contract, load_suite
from migrate_prove.demo.banking import seed_banking
from migrate_prove.engine import ValidationEngine
from migrate_prove.reporting import print_console, write_html, write_json

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Prove migrated data against an STM contract.")


def _resolve_url(raw: str, base: Path) -> str:
    if raw.startswith("sqlite:///"):
        rest = raw.removeprefix("sqlite:///")
        if rest.startswith("/") or (len(rest) > 1 and rest[1] == ":"):
            return raw
        return "sqlite:///" + (base / rest).resolve().as_posix()
    return raw


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
) -> None:
    """Run the multi-tier validation pipeline and write an evidence scorecard."""
    config = load_suite(suite)
    base = suite.parent
    contract = load_contract((base / config.contract).resolve())
    source = SqlAlchemyConnector(_resolve_url(config.source["url"], base), name="source")
    target = SqlAlchemyConnector(_resolve_url(config.target["url"], base), name="target")
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
