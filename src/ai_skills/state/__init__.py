"""Transactional state operations shared by the native orchestration runtime."""
from __future__ import annotations

from ai_skills._facade import install_facade
from ai_skills.state import board, concurrency, io, pool_ops, recovery_ops

install_facade(__name__, (io, concurrency, pool_ops, recovery_ops, board))
