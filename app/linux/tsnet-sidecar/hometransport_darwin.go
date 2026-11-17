package sidecar

// Native Mac home transport belongs to slice 5b. Never inherit Linux setup or
// accept a home profile through the legacy Darwin engine.
func prepareHomeTransport(c EngineConfig) (*homeForwarder, error) {
	if c.ControlTLS != nil {
		return nil, errHomeTransport
	}
	return nil, nil
}
