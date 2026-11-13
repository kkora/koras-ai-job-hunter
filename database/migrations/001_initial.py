"""Initial model migration is applied by kajh_api.bootstrap with policy SQL.

The migration registry checksums this file, model metadata, and the policy SQL.
Subsequent structural changes require a new numbered migration.
"""
VERSION = "001_initial"
