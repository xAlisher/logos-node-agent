#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import mimetypes
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock


ROOT = Path(__file__).resolve().parent
RUNBOOK_ROOT = ROOT.parent
DEFAULT_NODE_API = "http://127.0.0.1:8080"
DEFAULT_NODE_BINARY = Path(os.environ.get("NODE_BINARY", str(RUNBOOK_ROOT / "artifacts/node/logos-blockchain-node")))
DEFAULT_LOG_DIR = RUNBOOK_ROOT / "state/live-v0.1.2/logs"
DEFAULT_ZONE_BOARD_DIR = RUNBOOK_ROOT / "state/zone-board-v0.2.2"
DEFAULT_LIVE_CHANNEL_CACHE_NAME = "dashboard-live-channels.json"
DEFAULT_ZONE_BOARD_TMUX_SESSION = "zone-board"
DEFAULT_LOCAL_ZONE_CHANNEL = "local"
ZONE_TOPIC_PREFIX = b"logos:yolo:"
MAX_PUBLISH_BYTES = 500
MAX_SUBSCRIBE_CHANNEL_BYTES = 64
MAX_LIVE_MESSAGES_PER_CHANNEL = 200
LOG_METADATA_REFRESH_SECS = 30
PROPOSAL_REFRESH_SECS = 60
MAX_LOG_METADATA_FILES = 3
MAX_CHANNEL_METADATA_MATCHES = 4
MAX_RECENT_PROPOSALS = 20
MAX_PROPOSAL_LOG_FILES = 4
# Journal window for nodes that log to stdout -> journald (no useful file logs).
# Bounded by time so the scan cost stays flat as the journal grows; --grep keeps
# the returned output tiny regardless of node verbosity.
PROPOSAL_JOURNAL_SINCE = "-24 hours"
PROPOSAL_JOURNAL_TIMEOUT_SECS = 30
DEFAULT_NODE_UNIT = "logos-node"
TUI_MESSAGE_RE = re.compile(r"^\s*(\d{2}:\d{2}:\d{2})\s+(.+?)\s*$")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
SLOT_RE = re.compile(r"slot: Slot\((\d+)\)")
CHANNEL_INSCRIBE_RE = re.compile(
    r"ChannelInscribe\(InscriptionOp \{ channel_id: ChannelId\(\[([^\]]+)\]\), "
    r"inscription: \[([^\]]*)\].*?signer: PublicKey\(([0-9a-f]+)\).*?"
    r"Received proposal with ID: HeaderId\(([0-9a-f]+)\)"
)
LOG_TIMESTAMP_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)")
PROPOSAL_RE = re.compile(
    r"propose_block\{parent=HeaderId\(([0-9a-f]+)\)\s+slot=Slot\((\d+)\)\}.*?"
    r"proposed block with id HeaderId\(([0-9a-f]+)\) containing (\d+) transactions \((\d+) removed\)"
)
PROPOSAL_APPLIED_RE = re.compile(
    r"Successfully applied our own proposed block\. Publishing it to the blend network: HeaderId\(([0-9a-f]+)\)"
)
# v0.2 chain-leader wording (no parent/slot, no "with id"/"containing"):
#   "proposed block HeaderId(<id>) with <n> transactions (<m> removed)"
PROPOSAL_RE_V02 = re.compile(
    r"proposed block HeaderId\(([0-9a-f]+)\) with (\d+) transactions \((\d+) removed\)"
)
NODE_VERSION_RE = re.compile(r"^logos-blockchain-node\s+([^\s]+)$", re.MULTILINE)


def latest_log_file(log_dir: Path) -> Path | None:
    if not log_dir.exists():
        return None
    files = [path for path in log_dir.iterdir() if path.is_file()]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def tail_lines(path: Path, line_count: int) -> list[str]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return list(deque(handle, maxlen=line_count))


def recent_log_files(log_dir: Path, limit: int) -> list[Path]:
    if not log_dir.exists():
        return []
    files = [path for path in log_dir.iterdir() if path.is_file()]
    return sorted(files, key=lambda path: path.name)[-limit:]


def decode_zone_channel(topic_hex: str) -> str:
    try:
        raw = bytes.fromhex(topic_hex).rstrip(b"\x00")
    except ValueError:
        return topic_hex

    if raw.startswith(ZONE_TOPIC_PREFIX):
        raw = raw[len(ZONE_TOPIC_PREFIX) :]
    elif any(value < 32 or value > 126 for value in raw):
        return short_hex(topic_hex)

    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return short_hex(topic_hex)
    if "\ufffd" in decoded or any(ord(char) < 32 for char in decoded):
        return short_hex(topic_hex)
    return decoded or short_hex(topic_hex)


def short_hex(value: str, length: int = 13) -> str:
    return f"{value[:length]}..." if len(value) > length else value


def short_channel_label(topic_hex: str) -> str:
    decoded = decode_zone_channel(topic_hex)
    if decoded != topic_hex and "\ufffd" not in decoded and all(ord(char) >= 32 for char in decoded):
        return decoded
    if len(topic_hex) == 64:
        return short_hex(topic_hex)
    return topic_hex


def channel_topic_bytes(channel: str) -> bytes:
    return (ZONE_TOPIC_PREFIX + channel.encode("utf-8"))[:32].ljust(32, b"\x00")


def parse_decimal_bytes(raw: str) -> bytes | None:
    try:
        values = [int(item.strip()) for item in raw.split(",") if item.strip()]
    except ValueError:
        return None
    if any(value < 0 or value > 255 for value in values):
        return None
    return bytes(values)


def parse_log_timestamp(line: str) -> str | None:
    match = LOG_TIMESTAMP_RE.search(ANSI_RE.sub("", line))
    return match.group(1) if match else None


def timestamp_time(timestamp: str | None) -> str:
    if not timestamp:
        return ""
    match = re.search(r"T(\d{2}:\d{2}:\d{2})", timestamp)
    return match.group(1) if match else ""


def _flatten_cryptarchia(payload):
    info = payload.get("cryptarchia_info")
    if not isinstance(info, dict):
        return payload
    out = dict(info)
    m = payload.get("mode")
    if isinstance(m, dict):
        v = next(iter(m.values()), None)
        m = v if isinstance(v, str) else next(iter(m.keys()), None)
    # 0.2.1: liveness moved from a top-level `mode` to `cryptarchia_info.state`
    # (e.g. "Online"/"Bootstrapping"). Fall back to it so consumers that read
    # `mode` (the /api/status payload, the top-bar indicator) keep working.
    if m is None:
        m = info.get("state")
    if m is not None:
        out["mode"] = m
    for k in ("ok","url","node_version","network","wallet"):
        if k in payload:
            out[k] = payload[k]
    return out


def current_lib_slot(node_api: str) -> int | None:
    try:
        with urllib.request.urlopen(f"{node_api}/cryptarchia/info", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None

    try:
        return int(_flatten_cryptarchia(payload).get("lib_slot"))
    except (TypeError, ValueError):
        return None


def parse_log_metadata(log_file: Path) -> tuple[dict[str, dict], dict[str, dict[str, list[dict]]]]:
    block_metadata: dict[str, dict] = {}
    channel_metadata: dict[str, dict[str, list[dict]]] = {}
    try:
        handle = log_file.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return block_metadata, channel_metadata

    with handle:
        for line in handle:
            if "ChannelInscribe" not in line or "Received proposal with ID" not in line:
                continue
            clean = ANSI_RE.sub("", line)
            match = CHANNEL_INSCRIBE_RE.search(clean)
            slot_match = SLOT_RE.search(clean)
            if not match or slot_match is None:
                continue

            channel_bytes = parse_decimal_bytes(match.group(1))
            inscription = parse_decimal_bytes(match.group(2))
            if channel_bytes is None or inscription is None:
                continue

            block_id = match.group(4)
            metadata = {
                "text": inscription.decode("utf-8", errors="replace"),
                "timestamp_iso": parse_log_timestamp(line),
                "slot": int(slot_match.group(1)),
                "signer": match.group(3),
                "block_id": block_id,
                "source": "node-log",
            }
            block_metadata[block_id] = metadata
            channel = decode_zone_channel(channel_bytes.hex())
            matches = channel_metadata.setdefault(channel, {}).setdefault(metadata["text"], [])
            matches.append(metadata)
            if len(matches) > MAX_CHANNEL_METADATA_MATCHES:
                del matches[:-MAX_CHANNEL_METADATA_MATCHES]

    return block_metadata, channel_metadata


def _ingest_proposal_line(proposals: dict[str, dict], raw_line: str) -> None:
    if "proposed block" not in raw_line and "Successfully applied our own proposed block" not in raw_line:
        return

    line = ANSI_RE.sub("", raw_line)
    if proposal_match := PROPOSAL_RE_V02.search(line):
        block_id, tx_count, removed_count = proposal_match.groups()
        proposal = proposals.setdefault(
            block_id,
            {
                "timestamp": parse_log_timestamp(line),
                "slot": None,
                "parent_block_id": "",
                "block_id": block_id,
                "tx_count": int(tx_count),
                "removed_count": int(removed_count),
                "applied_locally": False,
                "published_to_network": False,
                "status": "proposed",
            },
        )
        proposal.update(
            {
                "timestamp": proposal.get("timestamp") or parse_log_timestamp(line),
                "tx_count": int(tx_count),
                "removed_count": int(removed_count),
            }
        )
        return

    if proposal_match := PROPOSAL_RE.search(line):
        parent_block_id, slot, block_id, tx_count, removed_count = proposal_match.groups()
        proposal = proposals.setdefault(
            block_id,
            {
                "timestamp": parse_log_timestamp(line),
                "slot": int(slot),
                "parent_block_id": parent_block_id,
                "block_id": block_id,
                "tx_count": int(tx_count),
                "removed_count": int(removed_count),
                "applied_locally": False,
                "published_to_network": False,
                "status": "proposed",
            },
        )
        proposal.update(
            {
                "timestamp": proposal.get("timestamp") or parse_log_timestamp(line),
                "slot": int(slot),
                "parent_block_id": parent_block_id,
                "tx_count": int(tx_count),
                "removed_count": int(removed_count),
            }
        )
        return

    if applied_match := PROPOSAL_APPLIED_RE.search(line):
        block_id = applied_match.group(1)
        proposal = proposals.setdefault(
            block_id,
            {
                "timestamp": parse_log_timestamp(line),
                "slot": None,
                "parent_block_id": "",
                "block_id": block_id,
                "tx_count": 0,
                "removed_count": 0,
                "applied_locally": False,
                "published_to_network": False,
                "status": "proposed",
            },
        )
        proposal["applied_locally"] = True
        proposal["published_to_network"] = True


def _finalize_proposals(proposals: dict[str, dict]) -> list[dict]:
    sorted_proposals = sorted(
        proposals.values(),
        key=lambda proposal: parse_datetime(proposal.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    for proposal in sorted_proposals:
        if proposal["applied_locally"] and proposal["published_to_network"]:
            proposal["status"] = "applied+broadcast"
        elif proposal["applied_locally"]:
            proposal["status"] = "applied"
        elif proposal["published_to_network"]:
            proposal["status"] = "broadcast"
        else:
            proposal["status"] = "proposed"
    return sorted_proposals


def parse_recent_proposals(log_files: list[Path]) -> list[dict]:
    proposals: dict[str, dict] = {}
    for log_file in log_files:
        try:
            handle = log_file.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for raw_line in handle:
                _ingest_proposal_line(proposals, raw_line)
    return _finalize_proposals(proposals)


def journald_proposal_lines(unit: str, since: str = PROPOSAL_JOURNAL_SINCE) -> list[str]:
    """Proposal log lines for a user unit that logs to stdout -> journald.

    Filters server-side with `--grep` so only the (sparse) proposal lines are
    returned regardless of how chatty the node is, and bounds the scan with
    `--since` so the cost stays flat as the journal grows. Returns [] if
    journalctl is unavailable, the unit has no journal, or there are no matches
    (--grep exits non-zero) — the caller then keeps its file-based result, so
    file-logging nodes (Sneg) are unaffected.
    """
    try:
        output = subprocess.run(
            [
                "journalctl", "--user", "-u", unit,
                "-p", "info", "--since", since, "--no-pager",
                "--grep", "proposed block HeaderId|proposed block with id|Successfully applied our own proposed block",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=PROPOSAL_JOURNAL_TIMEOUT_SECS,
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    return output.splitlines()


def parse_recent_proposals_from_journal(unit: str) -> list[dict]:
    proposals: dict[str, dict] = {}
    for raw_line in journald_proposal_lines(unit):
        _ingest_proposal_line(proposals, raw_line)
    return _finalize_proposals(proposals)


def parse_datetime(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def detect_node_version(node_binary: Path) -> str:
    try:
        output = subprocess.run(
            [str(node_binary), "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""

    match = NODE_VERSION_RE.search(output)
    return match.group(1) if match else ""


def parse_zone_board_tui(capture: str) -> dict:
    channel = None
    messages = []

    for line in capture.splitlines():
        if "┌" in line and "─" in line:
            matches = re.findall(r"┌\s*(.*?)\s*─", line)
            names = [name.strip() for name in matches if name.strip() and name.strip() != "Channels"]
            if names:
                channel = names[-1]
                if channel.startswith("[you] "):
                    channel = channel[6:]

        if "││" not in line:
            continue

        content = line.split("││", 1)[1].rsplit("│", 1)[0]
        match = TUI_MESSAGE_RE.match(content)
        if not match:
            continue

        messages.append(
            {
                "text": match.group(2),
                "timestamp": match.group(1),
                "pending": False,
                "failed": False,
                "block_id": None,
                "source": "live-tui",
            }
        )

    return {
        "channel": channel,
        "messages": messages,
    }


def message_key(message: dict) -> tuple[str, str]:
    return (str(message.get("timestamp") or ""), str(message.get("text") or ""))


def observed_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_timestamp(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    except OSError:
        return None


def normalize_live_channels(payload: object) -> dict[str, list[dict]]:
    if not isinstance(payload, dict):
        return {}

    channels = payload.get("channels", payload)
    if not isinstance(channels, dict):
        return {}

    normalized: dict[str, list[dict]] = {}
    for channel, messages in channels.items():
        if not isinstance(channel, str) or not isinstance(messages, list):
            continue

        clean_messages = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            clean = dict(message)
            if not isinstance(clean.get("observed_at"), str) or not clean["observed_at"]:
                clean["observed_at"] = observed_now()
            clean_messages.append(clean)
        normalized[channel] = clean_messages[-MAX_LIVE_MESSAGES_PER_CHANNEL:]
    return normalized


def load_live_channels(path: Path) -> dict[str, list[dict]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return normalize_live_channels(payload)


def save_live_channels(path: Path, channels: dict[str, list[dict]]) -> None:
    payload = {
        "channels": channels,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


class DashboardHandler(BaseHTTPRequestHandler):
    node_api = DEFAULT_NODE_API
    node_binary = DEFAULT_NODE_BINARY
    node_version = ""
    log_dir = DEFAULT_LOG_DIR
    node_unit = DEFAULT_NODE_UNIT
    zone_board_dir = DEFAULT_ZONE_BOARD_DIR
    zone_board_tmux_session = DEFAULT_ZONE_BOARD_TMUX_SESSION
    local_zone_channel = DEFAULT_LOCAL_ZONE_CHANNEL
    wallet_public_key = ""
    live_channel_cache: Path | None = None
    live_channels: dict[str, list[dict]] = {}
    live_channels_lock = Lock()
    log_metadata_lock = Lock()
    log_metadata_last_refresh = 0.0
    log_file_cache: dict[str, dict] = {}
    block_metadata_cache: dict[str, dict] = {}
    channel_metadata_cache: dict[str, dict[str, list[dict]]] = {}
    proposal_cache_lock = Lock()
    proposal_cache_last_refresh = 0.0
    proposal_cache: dict = {"summary": {}, "recent": []}
    _wallet_cache: dict = {}
    _wallet_cache_lock = Lock()
    _wallet_cache_ts: float = 0.0
    _WALLET_CACHE_TTL: float = 15.0

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._serve_file(ROOT / "index.html")
            return

        if self.path == "/api/status":
            self._serve_status()
            return

        if self.path == "/api/logs":
            self._serve_logs()
            return

        if self.path == "/api/proposals":
            self._serve_proposals()
            return

        if self.path == "/api/zone-messages":
            self._serve_zone_messages()
            return

        if self.path == "/api/zone-live":
            self._serve_zone_live()
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        if self.path == "/api/zone-publish":
            self._serve_zone_publish()
            return

        if self.path == "/api/zone-subscribe":
            self._serve_zone_subscribe()
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, fmt: str, *args) -> None:
        return

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _serve_file(self, path: Path) -> None:
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        body = path.read_bytes()
        content_type, _ = mimetypes.guess_type(str(path))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    @classmethod
    def _refresh_log_metadata(cls) -> None:
        now = time.monotonic()
        if now - cls.log_metadata_last_refresh < LOG_METADATA_REFRESH_SECS:
            return

        with cls.log_metadata_lock:
            now = time.monotonic()
            if now - cls.log_metadata_last_refresh < LOG_METADATA_REFRESH_SECS:
                return

            if not cls.log_dir.exists():
                cls.log_file_cache = {}
                cls.block_metadata_cache = {}
                cls.channel_metadata_cache = {}
                cls.log_metadata_last_refresh = now
                return

            next_file_cache: dict[str, dict] = {}
            next_block_metadata: dict[str, dict] = {}
            next_channel_metadata: dict[str, dict[str, list[dict]]] = {}
            for log_file in recent_log_files(cls.log_dir, MAX_LOG_METADATA_FILES):
                try:
                    stat = log_file.stat()
                except OSError:
                    continue

                key = str(log_file)
                signature = (stat.st_size, stat.st_mtime_ns)
                cached = cls.log_file_cache.get(key)
                if cached and cached.get("signature") == signature:
                    file_block_metadata = cached["block_metadata"]
                    file_channel_metadata = cached["channel_metadata"]
                else:
                    file_block_metadata, file_channel_metadata = parse_log_metadata(log_file)

                next_file_cache[key] = {
                    "signature": signature,
                    "block_metadata": file_block_metadata,
                    "channel_metadata": file_channel_metadata,
                }
                next_block_metadata.update(file_block_metadata)
                for channel, messages in file_channel_metadata.items():
                    channel_messages = next_channel_metadata.setdefault(channel, {})
                    for text, text_messages in messages.items():
                        stored = channel_messages.setdefault(text, [])
                        stored.extend(text_messages)
                        if len(stored) > MAX_CHANNEL_METADATA_MATCHES:
                            del stored[:-MAX_CHANNEL_METADATA_MATCHES]

            cls.log_file_cache = next_file_cache
            cls.block_metadata_cache = next_block_metadata
            cls.channel_metadata_cache = next_channel_metadata
            cls.log_metadata_last_refresh = now

    @classmethod
    def _block_metadata(cls) -> dict[str, dict]:
        cls._refresh_log_metadata()
        return cls.block_metadata_cache

    @classmethod
    def _channel_inscriptions(cls, channel: str) -> dict[str, dict]:
        cls._refresh_log_metadata()
        return {
            text: messages[-1]
            for text, messages in cls.channel_metadata_cache.get(channel, {}).items()
            if messages
        }

    @classmethod
    def _match_channel_metadata(cls, channel: str, message: dict) -> dict | None:
        cls._refresh_log_metadata()
        text = str(message.get("text") or "")
        candidates = cls.channel_metadata_cache.get(channel, {}).get(text, [])
        if not candidates:
            return None

        message_time = str(message.get("timestamp") or "")
        if message_time:
            for candidate in candidates:
                if timestamp_time(candidate.get("timestamp_iso")) == message_time:
                    return candidate
        return candidates[-1]


    @staticmethod
    def _with_fallback_timestamp(message: dict, fallback_iso: str | None) -> dict:
        if message.get("timestamp_iso") or not fallback_iso:
            return message
        timestamp = str(message.get("timestamp") or "")
        if re.fullmatch(r"\d{2}:\d{2}:\d{2}", timestamp):
            return {**message, "timestamp_iso": f"{fallback_iso[:10]}T{timestamp}Z"}
        return {**message, "timestamp_iso": fallback_iso}

    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc

        if length <= 0:
            return {}
        if length > 4096:
            raise ValueError("Request body is too large")

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Request body must be JSON") from exc

        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    def _serve_status(self) -> None:
        url = f"{self.node_api}/cryptarchia/info"
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self._send_json(
                {
                    "ok": False,
                    "error": f"Node API unavailable: {exc}",
                    "url": url,
                },
                status=HTTPStatus.BAD_GATEWAY,
            )
            return

        payload = _flatten_cryptarchia(payload)
        payload["ok"] = True
        payload["url"] = url
        payload["node_version"] = self.node_version
        payload["network"] = self._network_info()
        payload["wallet"] = self._wallet_balance()
        payload["voucher_count"] = self._voucher_count()
        self._send_json(payload)

    def _voucher_count(self) -> int:
        # Claimable leadership vouchers = blocks this node has led (rewards pending).
        url = f"{self.node_api}/leader/claim/vouchers"
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
            return 0
        if isinstance(payload, dict) and isinstance(payload.get("vouchers"), list):
            return len(payload["vouchers"])
        if isinstance(payload, list):
            return len(payload)
        return 0

    def _wallet_balance(self) -> dict:
        public_key = self.wallet_public_key.strip()
        if not public_key:
            return {
                "ok": False,
                "error": "Wallet public key not configured",
            }

        cls = self.__class__
        now = time.time()

        # Return cached result if still fresh (double-checked locking)
        if cls._wallet_cache and now - cls._wallet_cache_ts < cls._WALLET_CACHE_TTL:
            return dict(cls._wallet_cache)

        with cls._wallet_cache_lock:
            if cls._wallet_cache and time.time() - cls._wallet_cache_ts < cls._WALLET_CACHE_TTL:
                return dict(cls._wallet_cache)

            url = f"{self.node_api}/wallet/{public_key}/balance"
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError) as exc:
                result: dict = {
                    "ok": False,
                    "url": url,
                    "address": public_key,
                    "error": str(exc),
                }
                cls._wallet_cache = result
                cls._wallet_cache_ts = time.time()
                return dict(result)

            if isinstance(payload, dict):
                payload["ok"] = True
                payload["url"] = url
                result = payload
            else:
                result = {
                    "ok": False,
                    "url": url,
                    "address": public_key,
                    "error": "Unexpected wallet balance response",
                }

            cls._wallet_cache = result
            cls._wallet_cache_ts = time.time()
            return dict(result)

    def _network_info(self) -> dict:
        url = f"{self.node_api}/network/info"
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError) as exc:
            return {
                "ok": False,
                "url": url,
                "error": str(exc),
            }

        if isinstance(payload, dict):
            payload["ok"] = True
            payload["url"] = url
            return payload

        return {
            "ok": False,
            "url": url,
            "error": "Unexpected network info response",
        }

    def _serve_logs(self) -> None:
        latest = latest_log_file(self.log_dir)
        if latest is None:
            self._send_json(
                {
                    "ok": False,
                    "error": "No log file found",
                    "log_dir": str(self.log_dir),
                    "lines": [],
                },
                status=HTTPStatus.NOT_FOUND,
            )
            return

        lines = [ANSI_RE.sub("", line.rstrip("\n")) for line in tail_lines(latest, 80)]
        self._send_json(
            {
                "ok": True,
                "log_dir": str(self.log_dir),
                "latest_file": latest.name,
                "lines": lines,
            }
        )

    def _serve_proposals(self) -> None:
        cls = type(self)
        now = time.monotonic()
        if now - cls.proposal_cache_last_refresh >= PROPOSAL_REFRESH_SECS:
            with cls.proposal_cache_lock:
                now = time.monotonic()
                if now - cls.proposal_cache_last_refresh >= PROPOSAL_REFRESH_SECS:
                    # Prefer file logs (Sneg), but fall back to journald whenever the
                    # files yield no proposals. This covers stdout->journald nodes
                    # (optiplex) AND misconfigured/stale log dirs. journald returns []
                    # for genuine file-logging nodes, so there is no regression.
                    log_files = recent_log_files(cls.log_dir, MAX_PROPOSAL_LOG_FILES)
                    proposals = parse_recent_proposals(log_files) if log_files else []
                    source = "files"
                    if not proposals:
                        journal_proposals = parse_recent_proposals_from_journal(cls.node_unit)
                        if journal_proposals:
                            proposals = journal_proposals
                            source = "journal"
                    non_empty = sum(1 for proposal in proposals if proposal.get("tx_count", 0) > 0)
                    latest = proposals[-1] if proposals else None
                    cls.proposal_cache = {
                        "summary": {
                            "proposals_recent": len(proposals),
                            "non_empty_recent": non_empty,
                            "zero_tx_recent": len(proposals) - non_empty,
                            "last_proposal_at": latest.get("timestamp") if latest else "",
                            "last_slot": latest.get("slot") if latest else None,
                            "last_block_id": latest.get("block_id") if latest else "",
                            "window_files": len(log_files),
                            "source": source,
                        },
                        "recent": proposals[-MAX_RECENT_PROPOSALS:],
                    }
                    cls.proposal_cache_last_refresh = now

        payload = cls.proposal_cache
        self._send_json(
            {
                "ok": True,
                "summary": payload.get("summary", {}),
                "recent": payload.get("recent", []),
            }
        )

    def _serve_zone_messages(self) -> None:
        cache_dir = self.zone_board_dir / "cache"
        if not cache_dir.exists():
            self._send_json(
                {
                    "ok": False,
                    "error": "Zone-board cache directory not found",
                    "zone_board_dir": str(self.zone_board_dir),
                    "channels": [],
                },
                status=HTTPStatus.NOT_FOUND,
            )
            return

        block_metadata = self._block_metadata()
        channels = []
        seen_topics = set()
        for cache_file in sorted(cache_dir.glob("*.json")):
            topic_hex = cache_file.stem
            channel_name = decode_zone_channel(topic_hex)
            fallback_iso = file_timestamp(cache_file)
            seen_topics.add(topic_hex)
            try:
                messages = json.loads(cache_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                channels.append(
                    {
                        "channel": decode_zone_channel(topic_hex),
                        "topic": topic_hex,
                        "ok": False,
                        "error": str(exc),
                        "messages": [],
                    }
                )
                continue

            enriched_messages = []
            if isinstance(messages, list):
                for message in messages:
                    if not isinstance(message, dict):
                        continue
                    enriched = dict(message)
                    block_id = enriched.get("block_id")
                    if isinstance(block_id, str) and block_id in block_metadata:
                        enriched = {**enriched, **block_metadata[block_id]}
                        enriched["timestamp"] = message.get("timestamp", enriched.get("timestamp", ""))
                    elif metadata := self._match_channel_metadata(channel_name, enriched):
                        enriched = {**enriched, **metadata}
                        enriched["timestamp"] = message.get("timestamp", enriched.get("timestamp", ""))
                    enriched = self._with_fallback_timestamp(enriched, fallback_iso)
                    enriched_messages.append(enriched)

            channels.append(
                {
                    "channel": channel_name,
                    "topic": topic_hex,
                    "ok": True,
                    "messages": enriched_messages,
                }
            )

        subscriptions_file = self.zone_board_dir / "subscriptions.json"
        if subscriptions_file.exists():
            try:
                subscriptions = json.loads(subscriptions_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                subscriptions = []

            for topic_hex in subscriptions:
                if not isinstance(topic_hex, str) or topic_hex in seen_topics:
                    continue
                seen_topics.add(topic_hex)
                channels.append(
                    {
                        "channel": short_channel_label(topic_hex),
                        "topic": topic_hex,
                        "ok": True,
                        "messages": [],
                    }
                )

        if not any(channel.get("channel") == self.local_zone_channel for channel in channels):
            channels.insert(
                0,
                {
                    "channel": self.local_zone_channel,
                    "topic": f"local:{self.local_zone_channel}",
                    "ok": True,
                    "messages": [],
                },
            )

        self._send_json(
            {
                "ok": True,
                "zone_board_dir": str(self.zone_board_dir),
                "channels": channels,
            }
        )

    def _serve_zone_live(self) -> None:
        session = self.zone_board_tmux_session
        try:
            capture = subprocess.run(
                ["tmux", "capture-pane", "-pt", session, "-S", "-80"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            self._send_json(
                {
                    "ok": False,
                    "error": f"Could not read zone-board tmux session '{session}': {exc}",
                    "session": session,
                },
                status=HTTPStatus.BAD_GATEWAY,
            )
            return

        parsed = parse_zone_board_tui(capture)
        inscriptions = self._channel_inscriptions(parsed["channel"] or self.local_zone_channel)
        lib_slot = current_lib_slot(self.node_api)
        for message in parsed["messages"]:
            inscription = inscriptions.get(message["text"])
            if not inscription:
                continue
            message.update(inscription)
            if lib_slot is not None and inscription["slot"] <= lib_slot:
                message["status"] = "finalized"
                message["finalized"] = True
            else:
                message["status"] = "confirmed"
                message["finalized"] = False

        channel = parsed["channel"] or self.local_zone_channel
        with self.live_channels_lock:
            stored = self.live_channels.setdefault(channel, [])
            seen = {message_key(message) for message in stored}
            changed = False
            for message in parsed["messages"]:
                message = dict(message)
                key = message_key(message)
                if key not in seen:
                    message["observed_at"] = observed_now()
                    stored.append(message)
                    seen.add(key)
                    changed = True
                else:
                    for index, existing in enumerate(stored):
                        if message_key(existing) == key:
                            updated = {**existing, **message}
                            updated["observed_at"] = existing.get("observed_at") or observed_now()
                            if updated != existing:
                                stored[index] = updated
                                changed = True
                            message = updated
                            break
                message["observed_at"] = message.get("observed_at") or observed_now()
            if len(stored) > MAX_LIVE_MESSAGES_PER_CHANNEL:
                del stored[:-MAX_LIVE_MESSAGES_PER_CHANNEL]
                changed = True
            if changed and self.live_channel_cache is not None:
                save_live_channels(self.live_channel_cache, self.live_channels)
            live_channels = [
                {
                    "channel": channel_name,
                    "topic": f"live:{channel_name}",
                    "ok": True,
                    "messages": messages,
                }
                for channel_name, messages in sorted(self.live_channels.items())
            ]

        self._send_json(
            {
                "ok": True,
                "session": session,
                "channel": channel,
                "messages": parsed["messages"],
                "channels": live_channels,
            }
        )

    def _serve_zone_publish(self) -> None:
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        message = str(payload.get("message", "")).strip()
        message = " ".join(message.splitlines())
        if not message:
            self._send_json({"ok": False, "error": "Message is empty"}, status=HTTPStatus.BAD_REQUEST)
            return
        if len(message.encode("utf-8")) > MAX_PUBLISH_BYTES:
            self._send_json(
                {
                    "ok": False,
                    "error": f"Message is too large; max {MAX_PUBLISH_BYTES} UTF-8 bytes",
                },
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        session = self.zone_board_tmux_session
        try:
            subprocess.run(["tmux", "send-keys", "-t", session, "Enter"], check=True, timeout=2)
            subprocess.run(["tmux", "send-keys", "-t", session, "-l", message], check=True, timeout=2)
            subprocess.run(["tmux", "send-keys", "-t", session, "Enter"], check=True, timeout=2)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            self._send_json(
                {
                    "ok": False,
                    "error": f"Could not publish through zone-board tmux session '{session}': {exc}",
                },
                status=HTTPStatus.BAD_GATEWAY,
            )
            return

        self._send_json(
            {
                "ok": True,
                "message": message,
                "session": session,
                "channel": "your channel",
            }
        )

    def _serve_zone_subscribe(self) -> None:
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        channel = str(payload.get("channel", "")).strip()
        if not channel:
            self._send_json({"ok": False, "error": "Channel is empty"}, status=HTTPStatus.BAD_REQUEST)
            return
        if any(char.isspace() for char in channel):
            self._send_json(
                {"ok": False, "error": "Channel must not contain whitespace"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        if len(channel.encode("utf-8")) > MAX_SUBSCRIBE_CHANNEL_BYTES:
            self._send_json(
                {
                    "ok": False,
                    "error": f"Channel is too large; max {MAX_SUBSCRIBE_CHANNEL_BYTES} UTF-8 bytes",
                },
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        session = self.zone_board_tmux_session
        command = f"/sub {channel}"
        try:
            subprocess.run(["tmux", "send-keys", "-t", session, "-l", command], check=True, timeout=2)
            subprocess.run(["tmux", "send-keys", "-t", session, "Enter"], check=True, timeout=2)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            self._send_json(
                {
                    "ok": False,
                    "error": f"Could not subscribe through zone-board tmux session '{session}': {exc}",
                },
                status=HTTPStatus.BAD_GATEWAY,
            )
            return

        self._send_json(
            {
                "ok": True,
                "channel": channel,
                "session": session,
            }
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Local dashboard for Logos node status")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--node-api", default=os.environ.get("NODE_API", DEFAULT_NODE_API))
    parser.add_argument(
        "--log-dir",
        default=os.environ.get("NODE_LOG_DIR", str(DEFAULT_LOG_DIR)),
    )
    parser.add_argument(
        "--node-unit",
        default=os.environ.get("NODE_UNIT", DEFAULT_NODE_UNIT),
        help="systemd --user unit for the node, used to read proposals from journald "
        "when no file logs exist (stdout-logging nodes).",
    )
    parser.add_argument(
        "--zone-board-dir",
        default=os.environ.get("ZONE_BOARD_DIR", str(DEFAULT_ZONE_BOARD_DIR)),
    )
    parser.add_argument(
        "--zone-board-tmux-session",
        default=os.environ.get("ZONE_BOARD_TMUX_SESSION", DEFAULT_ZONE_BOARD_TMUX_SESSION),
    )
    parser.add_argument(
        "--local-zone-channel",
        default=os.environ.get("ZONE_CHANNEL", DEFAULT_LOCAL_ZONE_CHANNEL),
        help="Fallback local channel name when zone-board has not rendered one yet.",
    )
    parser.add_argument(
        "--wallet-public-key",
        default=os.environ.get("WALLET_PUBLIC_KEY", ""),
        help="Wallet public key used for the balance card.",
    )
    parser.add_argument(
        "--live-channel-cache",
        default=os.environ.get("DASHBOARD_LIVE_CHANNEL_CACHE"),
        help="JSON file used to persist dashboard live channel messages across restarts.",
    )
    args = parser.parse_args()

    DashboardHandler.node_api = args.node_api.rstrip("/")
    DashboardHandler.node_binary = DEFAULT_NODE_BINARY
    DashboardHandler.node_version = detect_node_version(DashboardHandler.node_binary)
    DashboardHandler.log_dir = Path(args.log_dir)
    DashboardHandler.node_unit = args.node_unit
    DashboardHandler.zone_board_dir = Path(args.zone_board_dir)
    DashboardHandler.zone_board_tmux_session = args.zone_board_tmux_session
    DashboardHandler.local_zone_channel = args.local_zone_channel
    DashboardHandler.wallet_public_key = args.wallet_public_key
    DashboardHandler.live_channel_cache = (
        Path(args.live_channel_cache)
        if args.live_channel_cache
        else DashboardHandler.zone_board_dir / DEFAULT_LIVE_CHANNEL_CACHE_NAME
    )
    DashboardHandler.live_channels = load_live_channels(DashboardHandler.live_channel_cache)

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Dashboard listening on http://{args.host}:{args.port}")
    print(f"Node API: {DashboardHandler.node_api}")
    print(f"Log dir: {DashboardHandler.log_dir}")
    print(f"Zone-board dir: {DashboardHandler.zone_board_dir}")
    print(f"Zone-board tmux session: {DashboardHandler.zone_board_tmux_session}")
    print(f"Local zone channel: {DashboardHandler.local_zone_channel}")
    print(f"Wallet public key: {DashboardHandler.wallet_public_key or '(not configured)'}")
    print(f"Live channel cache: {DashboardHandler.live_channel_cache}")
    server.serve_forever()


if __name__ == "__main__":
    main()
