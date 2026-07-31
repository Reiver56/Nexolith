"""Shared data types."""

type Scalar = str | int | float | bool | None
type Row = dict[str, Scalar]
type Rows = list[Row]
