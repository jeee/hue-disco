from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def default_plugin_dir() -> Path:
    return Path(__file__).with_name('beat_plugins')


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f'beat_plugin_{path.stem}', path)
    if spec is None or spec.loader is None:
        raise ImportError(f'cannot load plugin spec for {path}')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def discover_plugins(plugin_dir: str | Path | None = None) -> dict[str, dict[str, Any]]:
    d = Path(plugin_dir) if plugin_dir else default_plugin_dir()
    out: dict[str, dict[str, Any]] = {}
    if not d.exists():
        return out
    for path in sorted(d.glob('*.py')):
        if path.name.startswith('_'):
            continue
        try:
            mod = _load_module(path)
            name = str(getattr(mod, 'PLUGIN_NAME', path.stem))
            if not hasattr(mod, 'create_tracker'):
                continue
            out[name] = {
                'name': name,
                'display_name': str(getattr(mod, 'DISPLAY_NAME', name)),
                'path': str(path),
                'module': mod,
            }
        except Exception as exc:
            out[path.stem] = {
                'name': path.stem,
                'display_name': path.stem,
                'path': str(path),
                'error': str(exc),
            }
    return out


def create_plugin_tracker(name: str, config: dict, plugin_dir: str | Path | None = None):
    plugins = discover_plugins(plugin_dir)
    item = plugins.get(name)
    if not item:
        raise KeyError(f'unknown beat plugin {name!r}; available: {", ".join(sorted(plugins))}')
    if item.get('error'):
        raise RuntimeError(f'beat plugin {name!r} failed to load: {item["error"]}')
    return item['module'].create_tracker(config)
