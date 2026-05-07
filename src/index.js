#!/usr/bin/env node

const required = ['HUE_BRIDGE_HOST', 'HUE_USERNAME'];
const missing = required.filter((name) => !process.env[name]);

if (missing.length > 0) {
  console.log(`hue-disco is installed. Configure ${missing.join(', ')} to connect to a Hue bridge.`);
  process.exit(0);
}

console.log(`hue-disco ready for bridge ${process.env.HUE_BRIDGE_HOST}.`);
