"""Plan and install immutable native Python runtimes and owned registrations."""
from __future__ import annotations

from ai_skills._facade import install_facade
from ai_skills.installation import activation, plan, runtime, uninstall

install_facade(__name__, (plan, runtime, activation, uninstall))
