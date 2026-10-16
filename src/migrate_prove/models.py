from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RiskLevel(str, Enum):
    critical = "critical"
    master = "master"
    high_volume = "high_volume"


class Concern(str, Enum):
    migration = "migration"
    transformation = "transformation"
    reconciliation = "reconciliation"


class MappingKind(str, Enum):
    direct = "direct"
    transform = "transform"
    coerce = "coerce"
    default = "default"
    surrogate = "surrogate"


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"
    ERROR = "ERROR"


class NormalizeSpec(BaseModel):
    trim: bool = True
    upper: bool = False
    date_format: str = "%Y-%m-%d"
    datetime_format: str = "%Y-%m-%dT%H:%M:%S"
    decimal_places: int | None = None
    null_token: str = ""
    empty_as_null: bool = True


class WhenThen(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    when: str | None = None
    then: Any = None
    else_value: Any = Field(default=None, alias="else")


class Mapping(BaseModel):
    source: str | None = None
    target: str
    kind: MappingKind = MappingKind.direct
    lookup: dict[str, Any] | None = None
    lookup_default: Any | None = None
    lookup_on_missing: Literal["fail", "passthrough", "null"] = "fail"
    rules: list[WhenThen] = []
    expression: str | None = None
    default: Any | None = None
    normalize: NormalizeSpec = Field(default_factory=NormalizeSpec)
    in_hash: bool = True
    type: str | None = None
    nullable: bool = True
    max_length: int | None = None


class Stratum(BaseModel):
    name: str
    where: str
    rate: float = 1.0

    @field_validator("rate")
    @classmethod
    def rate_bounds(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("stratum rate must be between 0 and 1")
        return value


class SamplePolicy(BaseModel):
    """Optional sampling policy for future hash/row_sample strategies."""

    strategy: Literal["full", "stratified"] = "full"
    strata: list[Stratum] = []


class RelationPart(BaseModel):
    """One physical table or inline SQL fragment that concatenates into a logical side."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    table: str | None = None
    sql: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")

    @model_validator(mode="after")
    def table_xor_sql(self) -> RelationPart:
        has_table = self.table is not None
        has_sql = self.sql is not None
        if has_table == has_sql:
            raise ValueError("RelationPart requires exactly one of table or sql")
        return self


class Entity(BaseModel):
    """STM entity: relations + mappings only (test intent lives in the suite)."""

    name: str
    source_table: str | None = None
    source_sql: str | None = None
    source_parts: list[RelationPart] | None = None
    target_table: str | None = None
    target_sql: str | None = None
    target_parts: list[RelationPart] | None = None
    source_schema: str | None = None
    target_schema: str | None = None
    business_key: list[str]
    source_key: list[str] | None = None
    risk: RiskLevel = RiskLevel.master
    mappings: list[Mapping]

    @model_validator(mode="after")
    def relation_bindings(self) -> Entity:
        source_modes = sum(
            [
                self.source_table is not None,
                self.source_sql is not None,
                bool(self.source_parts),
            ]
        )
        if source_modes != 1:
            raise ValueError(
                f"Entity {self.name}: provide exactly one of "
                "source_table, source_sql, or source_parts"
            )
        if self.source_parts is not None and len(self.source_parts) == 0:
            raise ValueError(f"Entity {self.name}: source_parts must be non-empty")

        target_modes = sum(
            [
                self.target_table is not None,
                self.target_sql is not None,
                bool(self.target_parts),
            ]
        )
        if target_modes != 1:
            raise ValueError(
                f"Entity {self.name}: provide exactly one of "
                "target_table, target_sql, or target_parts"
            )
        if self.target_parts is not None and len(self.target_parts) == 0:
            raise ValueError(f"Entity {self.name}: target_parts must be non-empty")
        return self

    def mapping_for_target(self, target: str) -> Mapping | None:
        for mapping in self.mappings:
            if mapping.target == target:
                return mapping
        return None

    def mapping_for_source(self, source: str) -> Mapping | None:
        for mapping in self.mappings:
            if mapping.source == source:
                return mapping
        return None

    def source_key_columns(self) -> list[str]:
        if self.source_key:
            return self.source_key
        keys: list[str] = []
        for target_col in self.business_key:
            mapping = self.mapping_for_target(target_col)
            if mapping is None or not mapping.source:
                raise ValueError(
                    f"Entity {self.name}: business key {target_col!r} needs "
                    "source_key or a mapping with a source column"
                )
            keys.append(mapping.source)
        return keys


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    name: str
    entities: list[Entity]

    def entity_by_name(self, name: str) -> Entity:
        for entity in self.entities:
            if entity.name == name:
                return entity
        raise KeyError(f"Unknown entity {name!r}")


class CheckResult(BaseModel):
    id: str
    level: str
    title: str
    entity: str
    concern: Concern
    status: CheckStatus
    expected: Any = None
    actual: Any = None
    variance: Any = None
    message: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0


class CheckFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str | None = None
    target: str | None = None


class DimensionSpec(BaseModel):
    """Group-by dimension: mapped shorthand or explicit side/names."""

    model_config = ConfigDict(extra="forbid")

    field: str | None = None
    side: Literal["source", "target", "both"] = "both"
    source: str | None = None
    target: str | None = None

    @model_validator(mode="after")
    def resolve_field(self) -> DimensionSpec:
        if self.field is None and self.source is None and self.target is None:
            raise ValueError("Dimension requires field and/or source/target names")
        if self.field is None:
            object.__setattr__(self, "field", self.target or self.source)
        return self


class InvariantSpec(BaseModel):
    """Target-side coherence rule (suite check kind: invariant)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    kind: Literal["sql", "orphans", "temporal"] = "sql"
    sql: str | None = None
    expect: int | float = 0
    columns: list[str] = []
    child_table: str | None = None
    child_key: str | None = None
    parent_table: str | None = None
    parent_key: str | None = None
    concern: Concern = Concern.reconciliation


class SuiteCheck(BaseModel):
    """One prove check declared in the suite (not in the STM)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal[
        "schema",
        "count",
        "group_count",
        "sum",
        "group_sum",
        "transforms",
        "row_sample",
        "hash",
        "probes",
        "invariant",
    ]
    entity: str
    field: str | None = None
    fields: list[str] | None = None
    dimensions: list[str | DimensionSpec] = []
    filter: CheckFilter | None = None
    tolerance_abs: float = 0.0
    tolerance_pct: float | None = None
    n: int = 5
    probes: list[str] = []
    invariant: InvariantSpec | None = None
    source_expr: str | None = None
    target_expr: str | None = None

    def resolved_dimensions(self) -> list[DimensionSpec]:
        out: list[DimensionSpec] = []
        for item in self.dimensions:
            if isinstance(item, str):
                out.append(DimensionSpec(field=item, side="both"))
            else:
                out.append(item)
        return out


class RowSmokeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n: int = 5


class ConnectionConfig(BaseModel):
    """Suite connection: SQLAlchemy, Salesforce mock/live, or declarative HTTP.

    Secrets belong in the environment (or a local `.env`), referenced as `${VAR}`.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["sqlalchemy", "salesforce_mock", "salesforce", "http"] = "sqlalchemy"
    url: str | None = None
    dialect: str | None = None
    host: str | None = None
    port: int | str | None = None
    database: str | None = None
    username: str | None = None
    password: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    fixtures: str | None = None
    api_version: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    base_url: str | None = None
    auth: dict[str, Any] | None = None
    resources: dict[str, Any] | None = None
    buffer_rows: int | None = None
    spill_dir: str | None = None
    max_rows: int | None = None

    def _has_sqlalchemy_structured(self) -> bool:
        return any(
            value is not None
            for value in (self.dialect, self.host, self.database, self.username, self.password)
        ) or bool(self.options) or self.port is not None

    def _forbid_sqlalchemy_fields(self, label: str) -> None:
        if self._has_sqlalchemy_structured():
            raise ValueError(f"{label} connections do not use structured SQLAlchemy fields")

    @model_validator(mode="after")
    def connection_shape(self) -> ConnectionConfig:
        if self.kind == "salesforce_mock":
            if not self.fixtures:
                raise ValueError("salesforce_mock connections require `fixtures`")
            if self.url is not None or self._has_sqlalchemy_structured():
                raise ValueError(
                    "salesforce_mock connections use `fixtures` only "
                    "(no url / structured SQLAlchemy fields)"
                )
            if any(
                value is not None
                for value in (
                    self.api_version,
                    self.client_id,
                    self.client_secret,
                    self.base_url,
                    self.auth,
                    self.resources,
                )
            ):
                raise ValueError("salesforce_mock connections use `fixtures` only")
            return self

        if self.kind == "salesforce":
            self._forbid_sqlalchemy_fields("salesforce")
            if self.fixtures is not None:
                raise ValueError("salesforce connections do not use `fixtures`")
            if self.base_url is not None or self.auth is not None or self.resources is not None:
                raise ValueError(
                    "salesforce connections use url/api_version/client_id/client_secret "
                    "(not base_url/auth/resources)"
                )
            missing = [
                name
                for name, value in (
                    ("url", self.url),
                    ("api_version", self.api_version),
                    ("client_id", self.client_id),
                    ("client_secret", self.client_secret),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    "salesforce connections require url, api_version, client_id, and client_secret "
                    f"(missing: {', '.join(missing)})"
                )
            return self

        if self.kind == "http":
            self._forbid_sqlalchemy_fields("http")
            if self.fixtures is not None:
                raise ValueError("http connections do not use `fixtures`")
            if self.url is not None:
                raise ValueError("http connections use `base_url` (not `url`)")
            if any(
                value is not None
                for value in (self.api_version, self.client_id, self.client_secret)
            ):
                raise ValueError(
                    "http connections use base_url/auth/resources "
                    "(not api_version/client_id/client_secret)"
                )
            if not self.base_url:
                raise ValueError("http connections require `base_url`")
            if not self.resources:
                raise ValueError("http connections require `resources`")
            return self

        has_url = self.url is not None
        has_structured = self._has_sqlalchemy_structured()
        if self.fixtures is not None:
            raise ValueError("sqlalchemy connections do not use `fixtures`")
        if any(
            value is not None
            for value in (
                self.api_version,
                self.client_id,
                self.client_secret,
                self.base_url,
                self.auth,
                self.resources,
                self.buffer_rows,
                self.spill_dir,
                self.max_rows,
            )
        ):
            raise ValueError("sqlalchemy connections do not use API connector fields")
        if has_url and has_structured:
            raise ValueError("Connection must use either `url` or structured fields, not both")
        if not has_url and not (self.dialect and self.host and self.database):
            raise ValueError(
                "Connection requires `url` or structured dialect/host/database "
                "(username/password usually come from ${ENV} refs)"
            )
        return self


class SuiteConfig(BaseModel):
    """Suite: connections + explicit prove checks against an STM contract."""

    model_config = ConfigDict(extra="forbid")

    name: str
    contract: str
    source: ConnectionConfig | None = None
    target: ConnectionConfig | None = None
    checks: list[SuiteCheck] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    output_dir: str = "artifacts"
    continue_on_fail: bool = True
    row_smoke: RowSmokeConfig | Literal[False] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_run_levels(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        legacy = [key for key in ("run", "levels", "mode", "observe_side") if key in data]
        if legacy:
            raise ValueError(
                "Suite no longer uses "
                + ", ".join(legacy)
                + "; declare prove checks under `checks` and omit unused connections for one-sided emit"
            )
        return data

    @model_validator(mode="after")
    def require_connection(self) -> SuiteConfig:
        if self.source is None and self.target is None:
            raise ValueError("Suite requires at least one of source or target connections")
        return self

    def row_smoke_n(self) -> int | None:
        if self.row_smoke is False:
            return None
        if self.row_smoke is None:
            return 5
        return self.row_smoke.n
