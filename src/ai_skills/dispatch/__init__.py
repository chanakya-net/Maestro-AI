"""Worker lifecycle, identity-checked inspection, bounded event watches and stop."""
from __future__ import annotations

from ai_skills._facade import install_facade
from ai_skills.dispatch import adoption, capture, control, lifecycle, models

install_facade(__name__, (models, capture, lifecycle, adoption, control))
