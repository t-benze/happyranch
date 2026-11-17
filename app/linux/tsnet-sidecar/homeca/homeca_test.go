package homeca_test

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/asn1"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/url"
	"strings"
	"testing"
	"time"

	"happyranch/linux-tsnet-sidecar/homeca"
)

var epoch = time.Date(2026, 11, 17, 12, 0, 0, 0, time.UTC)

func newCA(t *testing.T) ([]byte, homeca.PrivateKey) {
	t.Helper()
	der, key, err := homeca.NewCA(epoch, rand.Reader)
	if err != nil {
		t.Fatal("CA generation refused", err)
	}
	return der, key
}

func certificate(t *testing.T, der []byte) *x509.Certificate {
	t.Helper()
	c, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal("invalid certificate DER")
	}
	return c
}

func key(t *testing.T, k homeca.PrivateKey) *ecdsa.PrivateKey {
	t.Helper()
	parsed, err := x509.ParsePKCS8PrivateKey(k.DER())
	if err != nil {
		t.Fatal("invalid PKCS8 key")
	}
	result, ok := parsed.(*ecdsa.PrivateKey)
	if !ok || result.Curve != elliptic.P256() {
		t.Fatal("key must be P-256")
	}
	return result
}

func signed(t *testing.T, c, issuer *x509.Certificate, pub *ecdsa.PublicKey, signer *ecdsa.PrivateKey) []byte {
	t.Helper()
	der, err := x509.CreateCertificate(rand.Reader, c, issuer, pub, signer)
	if err != nil {
		t.Fatal("synthetic signing failed")
	}
	return der
}

func TestGeneration(t *testing.T) {
	caDER, caKey := newCA(t)
	ca := certificate(t, caDER)
	if len(caDER) > 4096 || !ca.IsCA || !ca.BasicConstraintsValid || ca.KeyUsage != x509.KeyUsageCertSign ||
		!ca.MaxPathLenZero || ca.Subject.CommonName != "HappyRanch home CA" || ca.CheckSignatureFrom(ca) != nil ||
		!ca.NotBefore.Equal(epoch.Add(-5*time.Minute)) || !ca.NotAfter.Equal(epoch.AddDate(10, 0, 0)) {
		t.Fatal("CA profile differs from the home contract")
	}
	leafDER, leafKey, err := homeca.IssueLeaf(caDER, caKey, epoch, rand.Reader)
	if err != nil {
		t.Fatal("leaf generation refused", err)
	}
	leaf := certificate(t, leafDER)
	if homeca.Check(caDER, leafDER, epoch) != nil || leaf.IsCA || !leaf.BasicConstraintsValid ||
		leaf.Subject.CommonName != "control.happyranch.invalid" || len(leaf.DNSNames) != 1 || leaf.DNSNames[0] != "control.happyranch.invalid" ||
		len(leaf.IPAddresses)+len(leaf.EmailAddresses)+len(leaf.URIs) != 0 ||
		len(leaf.ExtKeyUsage) != 1 || leaf.ExtKeyUsage[0] != x509.ExtKeyUsageServerAuth ||
		!leaf.NotBefore.Equal(ca.NotBefore) || !leaf.NotAfter.Equal(epoch.Add(365*24*time.Hour)) {
		t.Fatal("leaf profile differs from the fixed-name server contract")
	}
	for _, pair := range []struct {
		cert    *x509.Certificate
		private homeca.PrivateKey
	}{{ca, caKey}, {leaf, leafKey}} {
		if !key(t, pair.private).PublicKey.Equal(pair.cert.PublicKey) || pair.cert.SignatureAlgorithm != x509.ECDSAWithSHA256 ||
			pair.cert.SerialNumber.Sign() <= 0 || pair.cert.SerialNumber.BitLen() > 159 {
			t.Fatal("key, algorithm or serial profile invalid")
		}
	}
	if ca.SerialNumber.Cmp(leaf.SerialNumber) == 0 || bytes.Equal(caKey.DER(), leafKey.DER()) {
		t.Fatal("CA and leaf reused serial or key")
	}
	// Independently exercise normal root-only DNS/server-auth verification.
	roots := x509.NewCertPool()
	roots.AddCert(ca)
	if _, err := leaf.Verify(x509.VerifyOptions{Roots: roots, DNSName: "control.happyranch.invalid", CurrentTime: epoch}); err != nil {
		t.Fatal("standard verifier refused generated leaf")
	}
}

func TestSameCARenewal(t *testing.T) {
	caDER, caKey := newCA(t)
	original := bytes.Clone(caDER)
	leafDER, leafKey, err := homeca.IssueLeaf(caDER, caKey, epoch, rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	leaf := certificate(t, leafDER)
	boundary := leaf.NotAfter.Add(-90 * 24 * time.Hour)
	if homeca.LeafRenewalDue(leafDER, caDER, boundary.Add(-time.Nanosecond)) || !homeca.LeafRenewalDue(leafDER, caDER, boundary) {
		t.Fatal("renewal must begin at exactly 90 days remaining")
	}
	renewedDER, renewedKey, err := homeca.IssueLeaf(caDER, caKey, boundary, rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	renewed := certificate(t, renewedDER)
	if !bytes.Equal(original, caDER) || renewed.CheckSignatureFrom(certificate(t, original)) != nil ||
		homeca.Check(original, renewedDER, boundary) != nil || !renewed.NotAfter.After(leaf.NotAfter) ||
		bytes.Equal(leafDER, renewedDER) || bytes.Equal(leafKey.DER(), renewedKey.DER()) || renewed.SerialNumber.Cmp(leaf.SerialNumber) == 0 {
		t.Fatal("renewal failed to retain CA with a fresh valid leaf/key/serial")
	}
	// Address changes do not enter this API; an unchanged leaf remains usable.
	if homeca.LeafRenewalDue(leafDER, original, epoch.Add(time.Hour)) {
		t.Fatal("early leaf renewal")
	}
	if _, _, err := homeca.IssueLeaf(original, caKey, leaf.NotAfter, rand.Reader); err != nil {
		t.Fatal("same-CA expired-leaf recovery refused")
	}
	nearEnd := certificate(t, caDER).NotAfter.Add(-30 * 24 * time.Hour)
	capped, _, err := homeca.IssueLeaf(caDER, caKey, nearEnd, rand.Reader)
	if err != nil || !certificate(t, capped).NotAfter.Equal(certificate(t, caDER).NotAfter) {
		t.Fatal("leaf must cap at CA expiry and permit exact minimum")
	}
}

func TestCheckRefusals(t *testing.T) {
	caDER, caKey := newCA(t)
	ca := certificate(t, caDER)
	leafDER, leafKey, err := homeca.IssueLeaf(caDER, caKey, epoch, rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	otherCA, _ := newCA(t)
	for _, scenario := range []string{"wrong-name", "extra-dns", "no-san", "ip-only", "dns-and-ip", "email", "uri", "other-name", "client-auth", "any-eku", "extra-eku", "is-ca", "no-signature-usage", "over-ca-expiry", "forged-signature", "wrong-ca", "leaf-expired", "leaf-future", "ca-expired", "ca-future", "not-ca", "no-certsign", "malformed-ca", "trailing-ca", "trailing-leaf", "malformed-leaf"} {
		t.Run(scenario, func(t *testing.T) {
			c := certificate(t, leafDER)
			// Rebuild parsed extensions so changed typed fields become real signed SANs.
			c.Extensions, c.ExtraExtensions = nil, nil
			root := caDER
			now := epoch
			switch scenario {
			case "wrong-name":
				c.DNSNames = []string{"other.invalid"}
			case "extra-dns":
				c.DNSNames = append(c.DNSNames, "other.invalid")
			case "no-san":
				c.DNSNames = nil
			case "ip-only":
				c.DNSNames = nil
				c.IPAddresses = []net.IP{net.ParseIP("192.168.1.2")}
			case "dns-and-ip":
				c.IPAddresses = []net.IP{net.ParseIP("192.168.1.2")}
			case "email":
				c.EmailAddresses = []string{"control@example.invalid"}
			case "uri":
				c.URIs = []*url.URL{{Scheme: "https", Host: "example.invalid"}}
			case "other-name":
				value, err := asn1.Marshal([]asn1.RawValue{{Class: 2, Tag: 2, Bytes: []byte(homeca.ControlName)}, {Class: 2, Tag: 8, Bytes: []byte{42, 3}}})
				if err != nil {
					t.Fatal("SAN fixture failed")
				}
				c.ExtraExtensions = []pkix.Extension{{Id: asn1.ObjectIdentifier{2, 5, 29, 17}, Value: value}}
			case "client-auth":
				c.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}
			case "any-eku":
				c.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageAny}
			case "extra-eku":
				c.ExtKeyUsage = append(c.ExtKeyUsage, x509.ExtKeyUsageClientAuth)
			case "is-ca":
				c.IsCA = true
			case "no-signature-usage":
				c.KeyUsage = x509.KeyUsageKeyEncipherment
			case "over-ca-expiry":
				c.NotAfter = ca.NotAfter.Add(time.Second)
			case "wrong-ca":
				root = otherCA
			case "leaf-expired":
				now = c.NotAfter
			case "leaf-future":
				c.NotBefore = epoch.Add(time.Second)
			case "ca-expired":
				now = ca.NotAfter
			case "ca-future":
				now = ca.NotBefore.Add(-time.Nanosecond)
			case "not-ca", "no-certsign":
				broken := certificate(t, caDER)
				broken.Extensions = nil
				if scenario == "not-ca" {
					broken.IsCA = false
					broken.MaxPathLenZero = false
				} else {
					broken.KeyUsage = x509.KeyUsageDigitalSignature
				}
				root = signed(t, broken, broken, &key(t, caKey).PublicKey, key(t, caKey))
			case "malformed-ca":
				root = []byte("not DER")
			case "trailing-ca":
				root = append(bytes.Clone(root), root...)
			}
			candidate := signed(t, c, ca, &key(t, leafKey).PublicKey, key(t, caKey))
			if scenario == "forged-signature" {
				candidate[len(candidate)-1] ^= 1
			}
			if scenario == "trailing-leaf" {
				candidate = append(candidate, candidate...)
			}
			if scenario == "malformed-leaf" {
				candidate = []byte("not DER")
			}
			if homeca.Check(root, candidate, now) == nil {
				t.Fatal("invalid trust profile accepted")
			}
			if !homeca.LeafRenewalDue(candidate, root, now) {
				t.Fatal("invalid profile must require attention")
			}
		})
	}
}

func TestIssueLeafRefusals(t *testing.T) {
	caDER, caKey := newCA(t)
	_, foreignKey := newCA(t)
	for _, scenario := range []string{"minimum-minus-second", "expired", "future", "wrong-key", "empty-key", "malformed-ca"} {
		t.Run(scenario, func(t *testing.T) {
			now, root, signer := epoch, caDER, caKey
			var want error
			switch scenario {
			case "minimum-minus-second":
				now = certificate(t, caDER).NotAfter.Add(-30*24*time.Hour + time.Second)
				want = homeca.ErrInsufficientCALifetime
			case "expired":
				now = certificate(t, caDER).NotAfter
				want = homeca.ErrCAValidity
			case "future":
				now = certificate(t, caDER).NotBefore.Add(-time.Second)
				want = homeca.ErrCAValidity
			case "wrong-key":
				signer = foreignKey
				want = homeca.ErrPrivateKey
			case "empty-key":
				signer = homeca.PrivateKey{}
				want = homeca.ErrPrivateKey
			case "malformed-ca":
				root = []byte("not DER")
				want = homeca.ErrCA
			}
			der, private, err := homeca.IssueLeaf(root, signer, now, rand.Reader)
			if !errors.Is(err, want) || len(der) != 0 || len(private.DER()) != 0 {
				t.Fatal("must refuse without partial identity or fallback CA")
			}
		})
	}
}

func TestFingerprint(t *testing.T) {
	// Published SHA-256 test vector, independently fixed rather than computed by homeca.
	got := homeca.Fingerprint([]byte("abc"))
	if got != "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad" {
		t.Fatal("fingerprint must hash exact bytes")
	}
	if len(got) != 64 || strings.ToLower(got) != got {
		t.Fatal("fingerprint representation")
	}
	if _, err := hex.DecodeString(got); err != nil {
		t.Fatal("fingerprint is not hex")
	}
}

func TestPrivateKeyBoundary(t *testing.T) {
	_, original := newCA(t)
	transport := original.DER()
	loaded, err := homeca.ImportPrivateKey(transport)
	if err != nil || !bytes.Equal(loaded.DER(), transport) {
		t.Fatal("protected key import failed")
	}
	transport[0] ^= 1
	copy := loaded.DER()
	copy[0] ^= 1
	if !bytes.Equal(original.DER(), loaded.DER()) {
		t.Fatal("key storage aliases transport bytes")
	}
	for _, format := range []string{"%v", "%+v", "%#v", "%s", "%q", "%x", "%X", "%d"} {
		if fmt.Sprintf(format, loaded) != "homeca.PrivateKey(<redacted>)" || fmt.Sprintf(format, &loaded) != "homeca.PrivateKey(<redacted>)" {
			t.Fatal("private key formatter leaked material")
		}
	}
	if original.String() != "homeca.PrivateKey(<redacted>)" || original.GoString() != original.String() {
		t.Fatal("private key display leaked material")
	}
	if _, err := homeca.ImportPrivateKey([]byte("synthetic-private-key-canary")); !errors.Is(err, homeca.ErrPrivateKey) {
		t.Fatal("private parser error not closed")
	}
}

type hostileReader struct{}

func (hostileReader) Read([]byte) (int, error) { return 0, errors.New("synthetic-private-key-canary") }

func TestErrorsClosed(t *testing.T) {
	caDER, caKey := newCA(t)
	for _, operation := range []string{"ca", "leaf"} {
		for _, available := range []int{0, 20, 65} {
			t.Run(fmt.Sprintf("%s/entropy-after-%d", operation, available), func(t *testing.T) {
				want := homeca.ErrRandomness
				message := "homeca: randomness unavailable"
				if available == 65 {
					// The key has been generated; x509's signing step fails.
					want, message = homeca.ErrGeneration, "homeca: generation refused"
				}
				var err error
				var der []byte
				var private homeca.PrivateKey
				reader := io.MultiReader(bytes.NewReader(bytes.Repeat([]byte{0x61}, available)), hostileReader{})
				if operation == "ca" {
					der, private, err = homeca.NewCA(epoch, reader)
				} else {
					der, private, err = homeca.IssueLeaf(caDER, caKey, epoch, reader)
				}
				if !errors.Is(err, want) || err.Error() != message ||
					strings.Contains(fmt.Sprintf("%#v %+v", err, err), "synthetic-private-key-canary") || len(der) != 0 || len(private.DER()) != 0 {
					t.Fatal("reader error escaped closed refusal")
				}
			})
		}
	}
	if _, _, err := homeca.NewCA(epoch, nil); !errors.Is(err, homeca.ErrRandomness) {
		t.Fatal("nil entropy must refuse")
	}
	if _, _, err := homeca.NewCA(epoch, bytes.NewReader(make([]byte, 20))); !errors.Is(err, homeca.ErrRandomness) {
		t.Fatal("zero serial must refuse")
	}
	if _, _, err := homeca.NewCA(time.Time{}, rand.Reader); !errors.Is(err, homeca.ErrTime) {
		t.Fatal("unencodable time must refuse")
	}
	// Serial randomness is observable without relying on probabilistic bit length:
	// the supplied first 20 bytes become the positive 159-bit certificate serial.
	entropy := append(bytes.Repeat([]byte{0xff}, 20), bytes.Repeat([]byte{0x61}, 2048)...)
	der, _, err := homeca.NewCA(epoch, bytes.NewReader(entropy))
	if err != nil {
		t.Fatal("injected entropy not used")
	}
	want := new(big.Int).Sub(new(big.Int).Lsh(big.NewInt(1), 159), big.NewInt(1))
	if certificate(t, der).SerialNumber.Cmp(want) != 0 {
		t.Fatal("serial must use 159 random bits")
	}
}
