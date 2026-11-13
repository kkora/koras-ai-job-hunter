"""Initial frozen schema and policy SQL are applied by kajh_api.bootstrap.

The migration registry checksums 001_policies.sql followed by 001_schema.sql.
The exact frozen checksum is pinned even for the accepted historical registry row.
Subsequent structural changes require a new numbered migration.
"""
VERSION = "001_initial"
