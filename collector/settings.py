"""Configuration loading, first-run creation, and validation."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any


DEFAULT_SETTINGS: dict[str, Any] = {
    "bind_host": "0.0.0.0",
    "udp_port": 20777,
    "data_directory": "data",
    "log_directory": "logs",
    "receive_buffer_bytes": 4 * 1024 * 1024,
    "receiver_timeout_seconds": 0.25,
    "queue_capacity": 8192,
    "queue_put_timeout_seconds": 0.25,
    "database_batch_size": 256,
    "raw_flush_every": 256,
    "raw_compression": "zlib",
    "raw_compression_level": 1,
    "raw_block_bytes": 256 * 1024,
    "metadata_checkpoint_every": 1000,
    "status_interval_seconds": 10,
    "log_max_bytes": 5 * 1024 * 1024,
    "log_backup_count": 5,
    "foundation_enabled": True,
    "analysis_enabled": True,
}


class ConfigurationError(ValueError):
    """Raised when a settings file cannot be used safely."""


@dataclass(frozen=True, slots=True)
class Settings:
    bind_host: str
    udp_port: int
    data_directory: str
    log_directory: str
    receive_buffer_bytes: int
    receiver_timeout_seconds: float
    queue_capacity: int
    queue_put_timeout_seconds: float
    database_batch_size: int
    raw_flush_every: int
    metadata_checkpoint_every: int
    status_interval_seconds: float
    log_max_bytes: int
    log_backup_count: int
    raw_compression: str
    raw_compression_level: int
    raw_block_bytes: int
    foundation_enabled: bool = True
    analysis_enabled: bool = True


def _positive_int(config: dict[str, Any], key: str) -> int:
    try:
        value = int(config[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(f"{key} must be an integer") from exc
    if value <= 0:
        raise ConfigurationError(f"{key} must be greater than zero")
    return value


def _positive_float(config: dict[str, Any], key: str) -> float:
    try:
        value = float(config[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(f"{key} must be a number") from exc
    maximum = {"receiver_timeout_seconds": 5.0, "queue_put_timeout_seconds": 5.0,
               "status_interval_seconds": 3600.0}[key]
    if not math.isfinite(value) or not 0 < value <= maximum:
        raise ConfigurationError(f"{key} must be finite and between zero (exclusive) and {maximum}")
    return value


def _settings_from_mapping(values: dict[str, Any]) -> Settings:
    config = {**DEFAULT_SETTINGS, **values}
    bind_host = config.get("bind_host")
    data_directory = config.get("data_directory")
    log_directory = config.get("log_directory")
    if not isinstance(bind_host, str) or not bind_host.strip():
        raise ConfigurationError("bind_host must be a non-empty string")
    if not isinstance(data_directory, str) or not data_directory.strip():
        raise ConfigurationError("data_directory must be a non-empty string")
    if not isinstance(log_directory, str) or not log_directory.strip():
        raise ConfigurationError("log_directory must be a non-empty string")
    try:
        udp_port = int(config["udp_port"])
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("udp_port must be an integer") from exc
    if not 0 <= udp_port <= 65_535:
        raise ConfigurationError("udp_port must be between 0 and 65535")
    try:
        log_backup_count = int(config["log_backup_count"])
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("log_backup_count must be an integer") from exc
    if log_backup_count < 0:
        raise ConfigurationError("log_backup_count must be zero or greater")
    compression = config["raw_compression"]
    if type(config["foundation_enabled"]) is not bool:
        raise ConfigurationError("foundation_enabled must be true or false")
    if type(config["analysis_enabled"]) is not bool:
        raise ConfigurationError("analysis_enabled must be true or false")
    if compression not in {"none", "zlib"}:
        raise ConfigurationError("raw_compression must be 'none' or 'zlib'")
    level = config["raw_compression_level"]
    block_bytes = config["raw_block_bytes"]
    if type(level) is not int or not 0 <= level <= 9:
        raise ConfigurationError("raw_compression_level must be an integer from 0 to 9")
    if type(block_bytes) is not int or not 65_585 <= block_bytes <= 8 * 1024 * 1024:
        raise ConfigurationError("raw_block_bytes must be an integer from 65585 to 8388608")
    return Settings(
        bind_host=bind_host.strip(),
        udp_port=udp_port,
        data_directory=data_directory,
        log_directory=log_directory,
        receive_buffer_bytes=_positive_int(config, "receive_buffer_bytes"),
        receiver_timeout_seconds=_positive_float(config, "receiver_timeout_seconds"),
        queue_capacity=_positive_int(config, "queue_capacity"),
        queue_put_timeout_seconds=_positive_float(config, "queue_put_timeout_seconds"),
        database_batch_size=_positive_int(config, "database_batch_size"),
        raw_flush_every=_positive_int(config, "raw_flush_every"),
        metadata_checkpoint_every=_positive_int(config, "metadata_checkpoint_every"),
        status_interval_seconds=_positive_float(config, "status_interval_seconds"),
        log_max_bytes=_positive_int(config, "log_max_bytes"),
        log_backup_count=log_backup_count,
        raw_compression=compression,
        raw_compression_level=level,
        raw_block_bytes=block_bytes,
        foundation_enabled=config["foundation_enabled"],
        analysis_enabled=config["analysis_enabled"],
    )


def write_default_settings(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(DEFAULT_SETTINGS, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def load_settings(path: Path, create_if_missing: bool = True) -> Settings:
    if not path.exists():
        if not create_if_missing:
            raise ConfigurationError(f"settings file does not exist: {path}")
        try:
            write_default_settings(path)
        except OSError as exc:
            raise ConfigurationError(f"could not create settings file: {path}") from exc
    try:
        with path.open("r", encoding="utf-8") as stream:
            values = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"could not read settings file: {path}: {exc}") from exc
    if not isinstance(values, dict):
        raise ConfigurationError("settings root must be a JSON object")
    return _settings_from_mapping(values)


def parse_user_udp_port(value: str) -> int:
    text = value.strip()
    if not text or len(text) > 5 or not text.isascii() or not text.isdecimal():
        raise ConfigurationError("UDP 端口必须是 1–65535 之间的整数。")
    port = int(text)
    if not 1 <= port <= 65_535:
        raise ConfigurationError("UDP 端口必须是 1–65535 之间的整数。")
    return port


def save_udp_port(path: Path, port: int) -> Settings:
    """Atomically change only the port, preserving all other/unknown options."""
    if type(port) is not int or not 1 <= port <= 65_535:
        raise ConfigurationError("UDP 端口必须是 1–65535 之间的整数。")
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"无法读取配置文件，原配置未修改：{exc}") from exc
    if not isinstance(values, dict):
        raise ConfigurationError("配置文件必须是 JSON 对象，原配置未修改。")
    values["udp_port"] = port
    settings = _settings_from_mapping(values)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=path.name + ".", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(values, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return settings
