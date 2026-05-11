#!/usr/bin/env python3
import argparse
import os
import signal
import threading
import time
from functools import wraps

from flask import Flask, Response, jsonify, redirect, render_template, request
from io import BytesIO
from urllib.parse import quote_plus
import json
from waitress import serve

from bootstrap_hue_credentials import bootstrap
from disco_core import DiscoEngine
from config_schema import export_settings_backup, import_settings_backup, load_config, parse_backup_payload, save_config


def start_background_bootstrap(engine, config_path: str):
    if engine.cfg.get('backend_mode') not in ('diyhue', 'official_bridge'):
        return

    def runner():
        while True:
            try:
                cfg = engine.cfg
                missing = (
                    not cfg.get('app_key')
                    or not cfg.get('client_key')
                    or not (cfg.get('psk_identity') or cfg.get('app_key'))
                    or not cfg.get('entertainment_group_id')
                    or any(light.get('enabled', True) and light.get('id') in (None, '', 0, '0') for light in cfg.get('lights', []))
                )
                if not missing:
                    return
                bootstrap(config_path, timeout=25)
                engine.reload()
            except Exception as exc:
                try:
                    engine._mark_error(exc)
                except Exception:
                    pass
            time.sleep(15)

    threading.Thread(target=runner, daemon=True).start()


def create_app(config_path: str):
    engine = DiscoEngine(config_path)
    app = Flask(__name__, template_folder='templates')
    app.secret_key = engine.cfg.get('web', {}).get('session_secret', 'change-me')

    def require_password(level='control'):
        def deco(fn):
            @wraps(fn)
            def inner(*args, **kwargs):
                auth = request.authorization
                web = engine.cfg.get('web', {})
                control_pw = web.get('control_password')
                admin_pw = web.get('admin_password')
                if not auth:
                    return Response('Auth required', 401, {'WWW-Authenticate': 'Basic realm="Hue Disco"'})
                supplied = auth.password or ''
                allowed = {admin_pw} if level == 'admin' else {control_pw, admin_pw}
                if supplied not in {pw for pw in allowed if pw}:
                    return Response('Auth required', 401, {'WWW-Authenticate': 'Basic realm="Hue Disco"'})
                return fn(*args, **kwargs)
            return inner
        return deco

    @app.get('/')
    @require_password('control')
    def index():
        return render_template('index.html', state=engine.get_state())

    @app.get('/admin')
    @require_password('admin')
    def admin_page():
        notice = request.args.get('notice', '')
        error = request.args.get('error', '')
        return render_template('admin.html', state=engine.get_state(), notice=notice, error=error)

    @app.post('/api/control/start')
    @require_password('control')
    def api_start():
        engine.start()
        return redirect('/')

    @app.post('/api/control/stop')
    @require_password('control')
    def api_stop():
        engine.stop()
        return redirect('/')

    @app.post('/api/control/on')
    @require_password('control')
    def api_on():
        engine.lights_on()
        return redirect('/')

    @app.post('/api/control/off')
    @require_password('control')
    def api_off():
        engine.lights_off()
        return redirect('/')

    @app.post('/api/control/strobe')
    @require_password('control')
    def api_strobe():
        threading.Thread(target=engine.strobe, daemon=True).start()
        return redirect('/')

    @app.post('/api/control/strobe_preset')
    @require_password('control')
    def api_strobe_preset():
        preset_name = request.form.get('preset_name', '')
        threading.Thread(target=lambda: engine.run_strobe_preset(preset_name), daemon=True).start()
        return redirect('/')

    @app.post('/api/control/profile')
    @require_password('control')
    def api_profile():
        name = request.form.get('profile_name', '')
        engine.set_active_profile(name)
        return redirect('/')


    @app.get('/api/admin/backup/export')
    @require_password('admin')
    def api_admin_backup_export():
        payload = export_settings_backup(load_config(config_path))
        filename = f"hue-disco-settings-backup-{payload.get('created_at','').replace(':','-')}"
        body = json.dumps(payload, indent=2, ensure_ascii=False).encode('utf-8')
        return Response(
            body,
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment; filename="{filename}.json"'}
        )

    @app.post('/api/admin/backup/import')
    @require_password('admin')
    def api_admin_backup_import():
        upload = request.files.get('backup_file')
        if not upload or not upload.filename:
            return redirect('/admin?error=' + quote_plus('Choose a backup file to import.'))
        try:
            payload = parse_backup_payload(upload.read().decode('utf-8'))
            merged = import_settings_backup(load_config(config_path), payload)
            save_config(config_path, merged)
            engine.reload()
            return redirect('/admin?notice=' + quote_plus('Backup imported successfully. Review the settings and save again if you make further edits.'))
        except Exception as exc:
            return redirect('/admin?error=' + quote_plus(f'Backup import failed: {exc}'))

    @app.post('/api/admin/save')
    @require_password('admin')
    def api_admin_save():
        engine.save_admin_settings(request.form.to_dict())
        start_background_bootstrap(engine, config_path)
        return redirect('/admin?notice=' + quote_plus('Settings saved. Bootstrap will try to register bridge credentials automatically when needed. If this is a real Hue bridge, press the physical link button when prompted by the status message.'))

    @app.get('/status')
    def status():
        return jsonify(engine.get_live_state())

    @app.get('/api/state')
    @require_password('control')
    def api_state():
        return jsonify(engine.get_state())

    app.engine = engine
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=os.environ.get('HUE_DISCO_CONFIG', '/opt/hue-disco/config.yaml'))
    args = parser.parse_args()

    app = create_app(args.config)
    start_background_bootstrap(app.engine, args.config)
    host = app.engine.cfg.get('web', {}).get('host', '0.0.0.0')
    port = int(app.engine.cfg.get('web', {}).get('port', 8090))

    def shutdown(*_):
        try:
            app.engine.stop()
        finally:
            raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    if app.engine.cfg.get('disco_autostart'):
        app.engine.start()
    serve(app, host=host, port=port, asyncore_use_poll=True)


if __name__ == '__main__':
    main()
