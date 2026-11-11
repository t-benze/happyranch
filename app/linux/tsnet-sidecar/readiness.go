package sidecar

// Running reports whether external admission is still open. Unexpected accept
// failure tears down the listener; systemd's foreground process observes that
// loss and exits rather than restarting the engine in process.
func (s *Sidecar) Running() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.listener != nil && !s.stopping
}
