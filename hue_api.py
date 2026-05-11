import requests


class HueAPI:
    def __init__(self, bridge_ip, app_key, api_base_path="/clip/v2", verify=False, timeout=5):
        self.bridge_ip = bridge_ip
        self.app_key = app_key
        self.base = f"https://{bridge_ip}{api_base_path.rstrip('/')}"
        self.session = requests.Session()
        self.session.verify = verify
        self.session.headers.update({"hue-application-key": app_key, "Content-Type": "application/json"})
        self.timeout = timeout

    def get_entertainment(self, group_id):
        r = self.session.get(f"{self.base}/resource/entertainment_configuration/{group_id}", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def get_lights(self):
        r = self.session.get(f"{self.base}/resource/light", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def set_light(self, rid, on=None, brightness=None, xy=None, rgb=None):
        body = {}
        if on is not None:
            body["on"] = {"on": bool(on)}
        if brightness is not None:
            body["dimming"] = {"brightness": float(brightness)}
        if xy is not None:
            body["color"] = {"xy": {"x": xy[0], "y": xy[1]}}
        r = self.session.put(f"{self.base}/resource/light/{rid}", json=body, timeout=self.timeout)
        r.raise_for_status()
        return r.json()
