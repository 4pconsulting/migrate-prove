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
    strategy: Literal["full", "stratified"] = "full"
    strata: list[Stratum] = []


class Metric(BaseModel):
    name: str
    source_sql: str
    target_sql: str
    group_by: list[str] = []
    tolerance_abs: float = 0.0
    tolerance_pct: float | None = None


class Invariant(BaseModel):
    name: str
    kind: Literal["sql", "orphans", "temporal"] = "sql"
    sql: str | None = None
    expect: int | float = 0
    columns: list[str] = []
    child_table: str | None = None
    child_key: str | None = None
    parent_table: str | None = None
    parent_key: str | None = None
    concern: Concern = Concern.reconciliation


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
    volume_slices: list[list[str]] = []
    metrics: list[Metric] = []
    invariants: list[Invariant] = []
    sample: SamplePolicy = Field(default_factory=SamplePolicy)
    filter_source: str | None = None
    filter_target: str | None = None
    failure_probes: list[str] = []

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

        # sqlalchemy
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
    name: str
    contract: str
    source: ConnectionConfig | None = None
    target: ConnectionConfig | None = None
    mode: Literal["prove", "observe"] = "prove"
    observe_side: Literal["source", "target"] | None = None
    output_dir: str = "artifacts"
    continue_on_fail: bool = True
    levels: list[str] = Field(
        default_factory=lambda: ["1", "2", "3", "4", "5", "hash", "probes"]
    )

    @model_validator(mode="after")
    def mode_connections(self) -> SuiteConfig:
        if self.mode == "prove":
            if self.source is None or self.target is None:
                raise ValueError("prove mode requires both source and target connections")
            if self.observe_side is not None:
                raise ValueError("observe_side is only valid when mode is observe")
            return self

        if self.observe_side is None:
            raise ValueError("observe mode requires observe_side: source | target")
        live = self.source if self.observe_side == "source" else self.target
        if live is None:
            raise ValueError(
                f"observe mode with observe_side={self.observe_side!r} "
                f"requires a {self.observe_side} connection"
            )
        return self
