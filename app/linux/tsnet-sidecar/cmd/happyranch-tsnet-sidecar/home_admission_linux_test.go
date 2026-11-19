package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	sidecar "happyranch/linux-tsnet-sidecar"
)

func TestHomeForegroundPair(t *testing.T) {
	const sc = `{"Profile":"linux-managed-identity-v1","StateDir":"/var/lib/happyranch-tsnet-sidecar","RoleIdentity":"home-sidecar-home","DERPPolicy":"private-only","ListenAddr":":443","ExcludedDaemonPorts":[8765],"ControlURL":"https://control.happyranch.invalid:8443","control_tls":{"dial_ip":"192.168.1.5","ca_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"},"home_binding":{"home_id":"home","control_plane_id":"control","connector_id":"connector","activation_generation":7}}`
	const connector = `{"home_id":"home","connector_id":"connector","managed":{"profile":"linux-managed-identity-v1","control_plane_id":"control"}}`
	for _, scenario := range []string{"valid", "ids", "profile", "extra", "missing", "duplicate", "duplicate-nested", "state", "port"} {
		t.Run(scenario, func(t *testing.T) {
			s, c := sc, connector
			switch scenario {
			case "ids":
				c = strings.Replace(c, `"connector"`, `"foreign"`, 1)
			case "profile":
				c = strings.Replace(c, "linux-managed-identity-v1", "legacy", 1)
			case "extra":
				s = strings.Replace(s, "{", `{"ExpectedPeer":"foreign",`, 1)
			case "missing":
				s = strings.Replace(s, `"ListenAddr":":443",`, "", 1)
			case "duplicate":
				s = strings.Replace(s, "{", `{"Profile":"foreign",`, 1)
			case "duplicate-nested":
				c = strings.Replace(c, `"home_id"`, `"home_id":"foreign","home_id"`, 1)
			case "state":
				s = strings.Replace(s, "/var/lib/happyranch-tsnet-sidecar", "/tmp/foreign", 1)
			case "port":
				s = strings.Replace(s, "[8765]", "[]", 1)
			}
			var cfg sidecar.Config
			if err := json.Unmarshal([]byte(s), &cfg); err != nil {
				t.Fatal(err)
			}
			cfg.CredentialFile = "/run/credentials/happyranch-tsnet-sidecar.service/enrollment-key"
			if got := homePairMatches([]byte(s), []byte(c), cfg); got != (scenario == "valid") {
				t.Fatalf("pair admission=%v", got)
			}
		})
	}
}

func TestHomeForegroundFixedCustody(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sidecar.json")
	if err := os.WriteFile(path, []byte(`{}`), 0600); err != nil {
		t.Fatal(err)
	}
	// Real unprivileged temp directories cannot impersonate root-custodied config.
	if _, err := readHomeConfig(path); err == nil {
		t.Fatal("unprivileged config admitted")
	}
	if err := admitHomeForeground(path, sidecar.Config{Profile: sidecar.LinuxIdentityProfile}); err == nil {
		t.Fatal("unbound foreground admitted")
	}
	if err := admitHomeForeground(path, sidecar.Config{}); err != nil {
		t.Fatal("legacy admission changed")
	}
}
