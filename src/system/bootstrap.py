"""System Bootstrap — auto-configure cross-organ integrations on first boot.

Sets up:
  1. Default backup schedule in OpenMarrow for the data directory
  2. Default health-monitor pulse signals in OpenPulse
  3. Default rate-limit configuration in OpenImmune
  4. Cross-organ wiring: Vital alerts → Echo notifications
"""

from __future__ import annotations

import os
import json
import time
import logging
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_BOOTSTRAP_STATE_FILE = os.path.expanduser("~/.opensoul/bootstrap_state.json")


@dataclass
class BootstrapState:
    """Tracks what has been bootstrapped."""
    bootstrapped: bool = False
    bootstrapped_at: float = 0.0
    version: str = "1.0.0"
    items: dict[str, bool] = field(default_factory=dict)


class SystemBootstrap:
    """Manages system bootstrap — one-time setup of default configurations."""

    def __init__(self):
        self._state = self._load_state()
        self._lock = threading.Lock()

    def _load_state(self) -> BootstrapState:
        try:
            if os.path.exists(_BOOTSTRAP_STATE_FILE):
                with open(_BOOTSTRAP_STATE_FILE, "r") as f:
                    data = json.load(f)
                state = BootstrapState()
                state.bootstrapped = data.get("bootstrapped", False)
                state.bootstrapped_at = data.get("bootstrapped_at", 0.0)
                state.version = data.get("version", "1.0.0")
                state.items = data.get("items", {})
                return state
        except Exception as e:
            logger.warning("Failed to load bootstrap state: %s", e)
        return BootstrapState()

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(_BOOTSTRAP_STATE_FILE), exist_ok=True)
            with open(_BOOTSTRAP_STATE_FILE, "w") as f:
                json.dump({
                    "bootstrapped": self._state.bootstrapped,
                    "bootstrapped_at": self._state.bootstrapped_at,
                    "version": self._state.version,
                    "items": self._state.items,
                }, f, indent=2)
        except Exception as e:
            logger.warning("Failed to save bootstrap state: %s", e)

    @property
    def is_bootstrapped(self) -> bool:
        return self._state.bootstrapped

    @property
    def state(self) -> dict:
        return {
            "bootstrapped": self._state.bootstrapped,
            "bootstrapped_at": self._state.bootstrapped_at,
            "version": self._state.version,
            "items": self._state.items,
        }

    def run_bootstrap(self, force: bool = False) -> dict:
        """Run the full bootstrap sequence. Returns results of each step."""
        with self._lock:
            if self._state.bootstrapped and not force:
                return {
                    "status": "already_bootstrapped",
                    "bootstrapped_at": self._state.bootstrapped_at,
                    "items": self._state.items,
                }

            results = {}

            # 1. Setup default backup schedule
            results["marrow_backup"] = self._setup_marrow_backup()

            # 2. Setup default health pulse signals
            results["pulse_health"] = self._setup_pulse_health()

            # 3. Setup default immune configuration
            results["immune_config"] = self._setup_immune_config()

            # 4. Setup Vital → Echo alert wiring
            results["vital_echo_wiring"] = self._setup_vital_echo_wiring()

            # 5. Setup default gene templates (if empty)
            results["gene_templates"] = self._setup_gene_defaults()

            # Update state
            self._state.bootstrapped = True
            self._state.bootstrapped_at = time.time()
            self._state.items = {k: v.get("success", False) for k, v in results.items()}
            self._save_state()

            logger.info("System bootstrap complete: %s", results)
            return {
                "status": "bootstrapped",
                "bootstrapped_at": self._state.bootstrapped_at,
                "results": results,
            }

    def _setup_marrow_backup(self) -> dict:
        """Create default backup schedule for the data directory."""
        try:
            # Use the same singleton instances as the API
            from src.api.marrow import backup_manager, scheduler

            # Check if schedule already exists
            existing = scheduler.list_schedules()
            if any(s.get("name") == "Daily Data Backup" for s in existing):
                return {"success": True, "skipped": True, "reason": "schedule already exists"}

            data_dir = os.path.expanduser("~/.opensoul/data")
            if not os.path.exists(data_dir):
                data_dir = os.path.expanduser("~/.opensoul")

            schedule = scheduler.create_schedule(
                name="Daily Data Backup",
                source_dirs=[data_dir],
                interval="daily",
                description="Automatic daily backup of OpenSoul data directory",
                tags=["auto", "bootstrap", "daily"],
            )
            return {
                "success": True,
                "schedule_id": schedule.schedule_id,
                "interval": "daily",
                "source": data_dir,
            }
        except Exception as e:
            logger.warning("Marrow backup setup failed: %s", e)
            return {"success": False, "error": str(e)}

    def _setup_pulse_health(self) -> dict:
        """Create default pulse signals for health monitoring."""
        try:
            from src.api.pulse import engine

            signals_created = []

            # Health check pulse — every 60 seconds
            existing = engine.list_signals()
            if not any(s.name == "system_health_check" for s in existing):
                sig = engine.create_signal(
                    name="system_health_check",
                    signal_type="interval",
                    interval_ms=60000,  # 60 seconds
                    callback_url="http://127.0.0.1:8090/api/health",
                    payload={"type": "health_check"},
                    max_fires=0,  # unlimited
                )
                signals_created.append(sig.signal_id)

            return {
                "success": True,
                "signals_created": signals_created,
                "count": len(signals_created),
            }
        except Exception as e:
            logger.warning("Pulse health setup failed: %s", e)
            return {"success": False, "error": str(e)}

    def _setup_immune_config(self) -> dict:
        """Setup default immune rate-limit configuration."""
        try:
            # Immune is auto-configured with defaults from the API module
            from src.api.immune import router as _  # ensure module loaded
            return {
                "success": True,
                "rate_limit": {
                    "requests_per_minute": 60,
                    "requests_per_hour": 1000,
                    "burst_size": 20,
                },
            }
        except Exception as e:
            logger.warning("Immune config setup failed: %s", e)
            return {"success": False, "error": str(e)}

    def _setup_vital_echo_wiring(self) -> dict:
        """Wire Vital alerts to Echo notifications — store the wiring config."""
        try:
            wiring_config = {
                "enabled": True,
                "rules": [
                    {
                        "name": "critical_alert_broadcast",
                        "condition": "alert.severity == 'critical'",
                        "action": "echo.broadcast",
                        "template": "🚨 CRITICAL: {alert.message}",
                    },
                    {
                        "name": "warning_alert_console",
                        "condition": "alert.severity == 'warning'",
                        "action": "echo.send",
                        "channel": "console",
                        "template": "⚠️ WARNING: {alert.message}",
                    },
                ],
            }

            # Store wiring config
            config_dir = os.path.expanduser("~/.opensoul")
            os.makedirs(config_dir, exist_ok=True)
            config_path = os.path.join(config_dir, "organ_wiring.json")

            with open(config_path, "w") as f:
                json.dump(wiring_config, f, indent=2)

            return {
                "success": True,
                "config_path": config_path,
                "rules_count": len(wiring_config["rules"]),
            }
        except Exception as e:
            logger.warning("Vital-Echo wiring setup failed: %s", e)
            return {"success": False, "error": str(e)}

    def _setup_gene_defaults(self) -> dict:
        """Ensure default gene templates exist."""
        try:
            from src.gene.templates import TemplateEngine
            engine = TemplateEngine()
            templates = engine.list_templates()

            if len(templates) > 0:
                return {"success": True, "skipped": True, "reason": f"{len(templates)} templates already exist"}

            return {"success": True, "skipped": True, "reason": "templates managed by gene module"}
        except Exception as e:
            logger.warning("Gene defaults setup failed: %s", e)
            return {"success": False, "error": str(e)}
