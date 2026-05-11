import json
import os
import socket
import ssl
import struct
import subprocess
import requests
import threading
import time


def _kill_stale_dtls_clients(bridge_ip, port):
    try:
        subprocess.run(
            ['pkill', '-f', f'openssl s_client -dtls1_2 -quiet -connect {bridge_ip}:{port}'],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _resolve_real_bridge_entertainment_uuid(bridge_ip, api_app_key, entertainment_group_id):
    url = f'https://{bridge_ip}/clip/v2/resource/entertainment_configuration'
    response = requests.get(url, headers={'hue-application-key': api_app_key}, timeout=5, verify=False)
    response.raise_for_status()
    data = response.json()
    for item in (data.get('data') or []):
        if str(item.get('id_v1') or '') == f'/groups/{entertainment_group_id}':
            return item.get('id')
    raise RuntimeError(f'Could not map entertainment group {entertainment_group_id} to a V2 entertainment_configuration id')
from typing import List, Dict, Tuple


def rgb_to_hue16(v: int) -> int:
    v = max(0, min(255, int(v)))
    return v << 8


class OpenSSLDTLSHueStream:
    """
    Persistent DTLS 1.2 client using openssl s_client with PSK.
    Frames are written to stdin. This keeps latency low enough on a Pi while
    avoiding custom DTLS bindings.
    """

    def __init__(
        self,
        bridge_ip: str,
        psk_identity: str,
        psk_hex: str,
        entertainment_group_id: str,
        port: int = 2100,
        api_app_key: str | None = None,
        api_base_path: str = '/api',
    ):
        self.bridge_ip = bridge_ip
        self.port = port
        self.psk_identity = psk_identity
        self.psk_hex = psk_hex
        self.entertainment_group_id = entertainment_group_id
        self.api_app_key = api_app_key or psk_identity
        self.api_base_path = str(api_base_path or '/api').strip() or '/api'
        self.proc = None
        self.lock = threading.RLock()
        self.sequence = 0

    def _activate_v2_stream(self):
        ent_uuid = _resolve_real_bridge_entertainment_uuid(
            self.bridge_ip,
            self.api_app_key,
            self.entertainment_group_id,
        )
        url = f"https://{self.bridge_ip}/clip/v2/resource/entertainment_configuration/{ent_uuid}"
        response = requests.put(
            url,
            headers={"hue-application-key": self.api_app_key},
            json={"action": "start"},
            timeout=5,
            verify=False,
        )
        response.raise_for_status()
        try:
            data = response.json()
        except Exception:
            return
        errors = data.get("errors") if isinstance(data, dict) else None
        if errors:
            raise RuntimeError(f"Hue Bridge V2 stream activation failed: {errors}")

    def _activate_diyhue_stream(self):
        use_v1 = self.api_base_path == '/api' or str(self.bridge_ip).strip() in ('127.0.0.1', 'localhost') or str(self.entertainment_group_id).isdigit()
        if use_v1:
            scheme = 'http' if str(self.bridge_ip).strip() in ('127.0.0.1', 'localhost') else 'https'
            url = f"{scheme}://{self.bridge_ip}/api/{self.api_app_key}/groups/{self.entertainment_group_id}/action"
            response = requests.put(url, json={"stream": {"active": True}}, timeout=5, verify=False)
            response.raise_for_status()
            try:
                data = response.json()
            except Exception:
                return
            if isinstance(data, list):
                errors = [item.get("error") for item in data if isinstance(item, dict) and item.get("error")]
                if errors:
                    descriptions = ' | '.join(str((err or {}).get('description') or err) for err in errors)
                    if str(self.bridge_ip).strip() not in ('127.0.0.1', 'localhost'):
                        if 'stream' in descriptions.lower() or 'parameter' in descriptions.lower() or str(self.entertainment_group_id).isdigit():
                            self._activate_v2_stream()
                            return
                    raise RuntimeError(f"Hue stream activation failed: {errors}")
            return

        self._activate_v2_stream()

    def _cmd(self):
        return [
            "openssl", "s_client",
            "-dtls1_2",
            "-quiet",
            "-connect", f"{self.bridge_ip}:{self.port}",
            "-cipher", "PSK-AES128-GCM-SHA256",
            "-psk_identity", self.psk_identity,
            "-psk", self.psk_hex,
        ]

    def connect(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return
            _kill_stale_dtls_clients(self.bridge_ip, self.port)
            self.proc = None
            self._activate_diyhue_stream()
            self.proc = subprocess.Popen(
                self._cmd(),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            time.sleep(0.35)

    def close(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=2)
                except Exception:
                    try:
                        self.proc.kill()
                    except Exception:
                        pass
            self.proc = None

    def _build_frame(self, light_colors: List[Dict]):
        header = bytearray()
        header.extend(b"HueStream")
        header.extend(b"\x01\x00")  # version 1.0 for diyHue compatibility
        header.extend(struct.pack(">B", self.sequence & 0xFF))
        header.extend(b"\x00\x00")
        header.extend(b"\x00")  # color mode RGB
        header.extend(b"\x00")  # reserved

        payload = bytearray()
        for item in light_colors:
            lid = int(item["id"])
            r, g, b = item["rgb"]

            # diyHue expects v1-style entries:
            # [type=0x00][light_id_be_u16][r_u16][g_u16][b_u16]
            payload.extend(struct.pack(">B", 0x00))
            payload.extend(struct.pack(">H", lid))
            payload.extend(struct.pack(">H", rgb_to_hue16(r)))
            payload.extend(struct.pack(">H", rgb_to_hue16(g)))
            payload.extend(struct.pack(">H", rgb_to_hue16(b)))

        self.sequence = (self.sequence + 1) % 256
        return bytes(header + payload)

    def send(self, light_colors: List[Dict]):
        with self.lock:
            self.connect()
            if not self.proc or self.proc.poll() is not None or not self.proc.stdin:
                with open("/tmp/hue-disco-dtls.log", "a", encoding="utf-8") as f:
                    f.write(f"subprocess unavailable lights={len(light_colors)}\n")
                raise RuntimeError("DTLS subprocess unavailable")
            frame = self._build_frame(light_colors)
            try:
                with open("/tmp/hue-disco-dtls.log", "a", encoding="utf-8") as f:
                    f.write(f"send start lights={len(light_colors)} bytes={len(frame)} pid={self.proc.pid if self.proc else None}\n")
                self.proc.stdin.write(frame)
                self.proc.stdin.flush()
                with open("/tmp/hue-disco-dtls.log", "a", encoding="utf-8") as f:
                    f.write(f"send ok lights={len(light_colors)} bytes={len(frame)}\n")
            except BrokenPipeError:
                with open("/tmp/hue-disco-dtls.log", "a", encoding="utf-8") as f:
                    f.write(f"broken pipe lights={len(light_colors)} bytes={len(frame)}\n")
                self.close()
                self._activate_diyhue_stream()
                self.connect()
                self.proc.stdin.write(frame)
                self.proc.stdin.flush()
                with open("/tmp/hue-disco-dtls.log", "a", encoding="utf-8") as f:
                    f.write(f"send ok after reconnect lights={len(light_colors)} bytes={len(frame)}\n")
