package main

import (
	"encoding/json"
	sidecar "happyranch/linux-tsnet-sidecar"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestHomeBindingAdmission(t *testing.T) {
	binding := `{"home_id":"home","control_plane_id":"control","connector_id":"connector","activation_generation":7}`
	for _, scenario := range []string{"home", "absent", "legacy", "no-tls", "wrong-url", "null", "extra", "missing", "duplicate", "duplicate-member", "bool", "string", "negative", "id"} {
		t.Run(scenario, func(t *testing.T) {
			cfg := configForCommand(t)
			cfg.Profile = sidecar.LinuxIdentityProfile
			cfg.ExpectedPeer = ""
			cfg.ConnectorAddr = ""
			cfg.ControlURL = "https://control.happyranch.invalid:28443"
			cfg.ControlTLS = &sidecar.HomeControlTLS{DialIP: "192.168.1.5", CAFingerprint: strings.Repeat("c", 64)}
			member := binding
			switch scenario {
			case "legacy":
				cfg.Profile = ""
				cfg.ExpectedPeer = "peer"
				cfg.ConnectorAddr = "127.0.0.1:9443"
				cfg.ControlURL = "https://legacy.invalid"
				cfg.ControlTLS = nil
			case "no-tls":
				cfg.ControlTLS = nil
			case "wrong-url":
				cfg.ControlURL = "https://wrong.invalid"
			case "null":
				member = "null"
			case "extra":
				member = strings.Replace(binding, `"home_id"`, `"extra":1,"home_id"`, 1)
			case "missing":
				member = strings.Replace(binding, `"home_id":"home",`, "", 1)
			case "duplicate":
				member = strings.Replace(binding, `"home_id"`, `"home_id":"other","home_id"`, 1)
			case "bool":
				member = strings.Replace(binding, ":7", ":true", 1)
			case "string":
				member = strings.Replace(binding, ":7", `:"7"`, 1)
			case "negative":
				member = strings.Replace(binding, ":7", ":-1", 1)
			case "id":
				member = strings.Replace(binding, `"home"`, `"bad space"`, 1)
			}
			data, _ := json.Marshal(cfg)
			if scenario != "absent" {
				data = append([]byte(`{"home_binding":`+member+`,`), data[1:]...)
			}
			if scenario == "duplicate-member" {
				data = append([]byte(`{"home_binding":`+binding+`,`), data[1:]...)
			}
			path := filepath.Join(t.TempDir(), "config")
			if err := os.WriteFile(path, data, 0600); err != nil {
				t.Fatal(err)
			}
			loaded, err := loadConfig(path, "")
			if scenario == "home" || scenario == "absent" {
				if err != nil || (loaded.HomeBinding != nil) != (scenario == "home") {
					t.Fatal("valid optional binding refused", err)
				}
			} else if err == nil {
				t.Fatal("unsafe binding admitted")
			}
		})
	}
}
