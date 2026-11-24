"""Validate and recover worker artifacts against role and Git contracts."""
from __future__ import annotations

from ai_skills._facade import install_facade
from ai_skills.artifacts import cli, schemas, synthesis, validators

install_facade(__name__, (schemas, validators, synthesis, cli))
