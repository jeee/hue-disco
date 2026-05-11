# Adding lights for disco mode

## Recommended flow with Zigbee2MQTT + diyHue

1. Pair the bulbs in Zigbee2MQTT.
2. Wait until the bulbs appear in the Zigbee2MQTT frontend.
3. Trigger a diyHue scan from the browser or by visiting `http://<pi-ip>/scan`.
4. Re-run the playbook or restart `hue-disco` after diyHue can see the lights.

The diyHue MQTT integration requires Zigbee2MQTT discovery messages on the `homeassistant` topic space, so Zigbee2MQTT discovery must stay enabled. diyHue’s docs also note that you still need to search for lights after the MQTT integration is configured. 

## Config examples

You can define lights by name first and let the bootstrap helper resolve the numeric diyHue light IDs later:

```yaml
hue:
  lights:
    - id: null
      name: left_front
      enabled: true
      max_brightness: 180
      allowed_colors: ["#FF0040", "#00C8FF", "#FFE600"]
    - id: null
      name: right_front
      enabled: true
      max_brightness: 180
```

You can also pin known diyHue light IDs directly:

```yaml
hue:
  lights:
    - id: 3
      name: left_front
      enabled: true
      max_brightness: 180
    - id: 4
      name: right_front
      enabled: true
      max_brightness: 180
```

Group example:

```yaml
hue:
  groups:
    - name: front
      members: [left_front, right_front]
      max_brightness: 170
      allowed_colors: ["#FF0040", "#00C8FF"]
```

## Pairing tips

- Keep `permit_join` disabled by default and only enable it when pairing.
- Use `/dev/serial/by-id/...` for the coordinator path when possible.
- If Zigbee2MQTT sees the bulbs but diyHue does not, check that diyHue MQTT integration is enabled and then run `/scan` again.


## Zigbee2MQTT requirement

For diyHue-backed lights, Zigbee2MQTT must publish Home Assistant discovery messages. This package deploys Zigbee2MQTT with `homeassistant.enabled: true` and a local MQTT broker so diyHue can discover the paired lights automatically.
