# SQL utilities

`models.py` owns shared metadata and historical-car ORM models. Current Orion
packet/group models are declared by the Bazel-generated `orion_models` module
and re-exported here. Do not paste sensor columns into this file.

The independent `OrionGpsPrecise` table remains alongside those generated
models. It serves the existing GPS-only feed, has no packet foreign key, and
its initialization DDL remains in the shared SQL rather than the CAN snapshot.

`QueryBuilder` discovers Orion classes through the generated `ORION_MODELS`
registry, so adding a textproto group or field does not need another class
list edit. Runtime query/insert behavior is unchanged. Generated initialization
DDL applies only to new databases; existing volumes still need explicit
operator-managed migrations.
