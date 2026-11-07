# Three.js

Self-hosted Three.js r180 (0.180.0), MIT license, from the official tagged repository:
https://github.com/mrdoob/three.js/tree/r180

Files: build/three.module.min.js, build/three.core.min.js,
examples/jsm/controls/OrbitControls.js, LICENSE.

The only vendor modification is OrbitControls' bare `three` import rewritten to
`./three.module.min.js` for native browser modules without a bundler or CDN.
No remote script, telemetry, API key or model service is required.
